#!/usr/bin/env python3
"""link_test_capture.py — turn an RNode into a calibrated path-loss instrument.

Born 2026-09-08 for the ~250 ft ʻōhiʻa link test. The point of that test is ONE
reusable number — the excess loss of that vegetation at 903 MHz — to be reused
for AREDN siting and the field kit. A number you cannot reuse means the whole
exercise has to be repeated, and there are only so many trips up the hill.

Two things make a reading reusable, and terminal scrollback provides neither:

  1. **Provenance.** RSSI and SNR are DEVICE-dependent. A Heltec V4 on one end
     and moc3's radio on the other measures the PATH correctly, but the absolute
     figures are not interchangeable with a RAK-to-RAK reading. So every row
     carries which hardware sat at each end, which end REPORTED the number, the
     TX power and both antenna gains. A row that cannot say where it came from
     is not evidence.
  2. **The arithmetic done once, from the file.** Measured path loss is
     ``tx_power + tx_gain + rx_gain - rssi``; the reusable figure is that minus
     free-space loss over the same distance. ``--summarize`` computes it from
     the CSV so it is re-derivable months later, rather than being a number
     somebody wrote down on the hill.

Why not just use LoRaMon
------------------------
LoRaMon (markqvist) does report per-packet RSSI/SNR, and the KISS sequence here
is transcribed from its proven implementation. But its ``-W`` mode writes ONLY
the raw packet bytes — the RF metrics exist on the console line and nowhere
else. Capturing them would mean scraping stdout, and the metadata that makes a
reading reusable would still be missing. It is also not installed on the fleet,
and a field tool should not acquire a dependency it can avoid.

⚠️ The measurement caveat this tool refuses to hide
---------------------------------------------------
RSSI and SNR arrive from the radio in their OWN KISS frames, separate from the
data frame. The tool records the most recently reported pair — which is correct
only if the firmware emits the stats BEFORE the packet they describe. That
ordering has not been verified against hardware here, so every row carries a
``stats_fresh`` flag: did a stats frame actually arrive between the previous
packet and this one? If a run comes back with ``stats_fresh`` mostly 0, the
numbers are stale-by-one and ``--summarize`` says so instead of averaging a lie
into a confident headline. An unverified assumption gets a witness, not silence.

The radio is confirmed ON before a single row is written
--------------------------------------------------------
Setting ``RADIO_STATE_ON`` is not evidence the radio started. This tool asks
(``RADIO_STATE_ASK``) and refuses to capture unless the device answers ``0x01``,
because a switched-off receiver is indistinguishable from a silent band and
would turn every reading into a false negative. Only the reply to an explicit
ASK counts -- a state frame can be an echo of the value just sent.

Deliberate non-goal: transmitting
---------------------------------
This captures only. Generating test traffic is the far end's job (an RNS link,
or a radio already beaconing). Adding a transmitter would mean keying a PA from
a script, and on a 28 dBm V4 an accidental transmit into a disconnected antenna
can damage the amplifier. Not worth the convenience.

Usage
-----
    # capture, on the box the receiving RNode is plugged into
    python3 scripts/link_test_capture.py --capture /dev/ttyUSB0 \
        --out ~/linktest/ohia_a_to_b.csv \
        --run "ohia-250ft" --direction "<far-box>-><near-box>" \
        --tx-hw "<far board + radio>" --rx-hw "<near board + radio>" \
        --tx-power 22 --tx-gain 2.15 --rx-gain 2.15 \
        --freq 903000000 --bw 125000 --sf 8 --cr 5 --distance 76.2

    # the reusable number, computed from the file
    python3 scripts/link_test_capture.py --summarize ~/linktest/ohia_a_to_b.csv

Take readings in BOTH directions and summarize each; asymmetry is real
information about the path, and averaging it away discards it.
"""

import argparse
import csv
import datetime
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.rf import free_space_path_loss  # noqa: E402
from utils.safe_import import safe_import  # noqa: E402

serial, _HAS_SERIAL = safe_import('serial')

# Minimum packets before a summary is worth quoting. Below this the median is
# noise dressed as a measurement.
MIN_PACKETS_FOR_SUMMARY = 20

# Below this fraction of rows carrying fresh stats, the RSSI/SNR pairing is
# unreliable and the headline number is withheld rather than qualified.
MIN_FRESH_FRACTION = 0.5

# SX126x sensitivity floor is around -137 dBm at the narrowest settings. A
# median within this margin of it means the reading is compressed against the
# floor and understates the true path loss.
FLOOR_DBM = -137.0
FLOOR_MARGIN_DB = 6.0

FIELDS = [
    "ts_iso", "run", "direction", "tx_hw", "rx_hw",
    "tx_power_dbm", "tx_gain_dbi", "rx_gain_dbi",
    "freq_hz", "bw_hz", "sf", "cr", "distance_m",
    "rssi_dbm", "snr_db", "bytes", "stats_fresh",
]


class KISS:
    """RNode KISS constants — transcribed from markqvist/LoRaMon."""
    FEND = 0xC0
    FESC = 0xDB
    TFEND = 0xDC
    TFESC = 0xDD

    CMD_UNKNOWN = 0xFE
    CMD_DATA = 0x00
    CMD_FREQUENCY = 0x01
    CMD_BANDWIDTH = 0x02
    CMD_TXPOWER = 0x03
    CMD_SF = 0x04
    CMD_CR = 0x05
    CMD_RADIO_STATE = 0x06
    CMD_DETECT = 0x08
    CMD_PROMISC = 0x0E
    CMD_STAT_RSSI = 0x23
    CMD_STAT_SNR = 0x24
    CMD_FW_VERSION = 0x50

    CMD_ERROR = 0x90

    DETECT_REQ = 0x73
    DETECT_RESP = 0x46
    RADIO_STATE_OFF = 0x00
    RADIO_STATE_ON = 0x01
    RADIO_STATE_ASK = 0xFF

    ERRORS = {
        0x01: "ERROR_INITRADIO — the radio hardware failed to initialise",
        0x02: "ERROR_TXFAILED",
        0x03: "ERROR_EEPROM_LOCKED",
    }

    RSSI_OFFSET = 157

    @staticmethod
    def escape(data: bytes) -> bytes:
        data = data.replace(bytes([0xDB]), bytes([0xDB, 0xDD]))
        data = data.replace(bytes([0xC0]), bytes([0xDB, 0xDC]))
        return data


class KissDecoder:
    """Byte-stream state machine: RNode serial -> packet and stat events.

    Pure and hardware-free by design — feed it bytes, read the events. That is
    what makes the load-bearing half of this tool testable without a radio on
    the bench, which is the only place it could otherwise be tested.
    """

    def __init__(self) -> None:
        self.in_frame = False
        self.escape = False
        self.command = KISS.CMD_UNKNOWN
        self.buf = bytearray()
        self.rssi: Optional[int] = None
        self.snr: Optional[float] = None
        self.detected = False
        self.fw_version: Optional[str] = None
        # Radio state as last REPORTED by the device. Deliberately starts None
        # and is cleared before an ASK: a CMD_RADIO_STATE frame arriving right
        # after we set the state can be an echo of the value we just sent, not
        # an observation. Only the reply to an explicit ASK is authority.
        self.radio_state: Optional[int] = None
        self.errors: List[str] = []
        # Bumped on every stats frame; lets a consumer tell whether the RSSI/SNR
        # it is about to record actually belongs to the packet in hand.
        self.stat_seq = 0
        self.packets: List[Tuple[int, Optional[int], Optional[float], int]] = []

    def _unescape(self, byte: int) -> Optional[int]:
        if byte == KISS.FESC:
            self.escape = True
            return None
        if self.escape:
            if byte == KISS.TFEND:
                byte = KISS.FEND
            elif byte == KISS.TFESC:
                byte = KISS.FESC
            self.escape = False
        return byte

    def feed(self, data: bytes) -> int:
        """Feed raw serial bytes. Returns the number of packets completed."""
        before = len(self.packets)
        for byte in data:
            self._feed_byte(byte)
        return len(self.packets) - before

    def _feed_byte(self, byte: int) -> None:
        if self.in_frame and byte == KISS.FEND and self.command == KISS.CMD_DATA:
            self.in_frame = False
            self.packets.append((self.stat_seq, self.rssi, self.snr, len(self.buf)))
            self.buf = bytearray()
            self.command = KISS.CMD_UNKNOWN
            return

        if byte == KISS.FEND:
            self.in_frame = True
            self.command = KISS.CMD_UNKNOWN
            self.buf = bytearray()
            self.escape = False
            return

        if not self.in_frame:
            return

        if not self.buf and self.command == KISS.CMD_UNKNOWN:
            self.command = byte
            return

        if self.command == KISS.CMD_DATA:
            b = self._unescape(byte)
            if b is not None and len(self.buf) < 512:
                self.buf.append(b)
        elif self.command == KISS.CMD_STAT_RSSI:
            self.rssi = byte - KISS.RSSI_OFFSET
            self.stat_seq += 1
        elif self.command == KISS.CMD_STAT_SNR:
            self.snr = int.from_bytes(bytes([byte]), byteorder="big", signed=True) * 0.25
            self.stat_seq += 1
        elif self.command == KISS.CMD_RADIO_STATE:
            self.radio_state = byte
        elif self.command == KISS.CMD_ERROR:
            self.errors.append(KISS.ERRORS.get(byte, f"unknown radio error 0x{byte:02x}"))
        elif self.command == KISS.CMD_DETECT:
            self.detected = (byte == KISS.DETECT_RESP)
        elif self.command == KISS.CMD_FW_VERSION:
            self.buf.append(byte)
            if len(self.buf) == 2:
                self.fw_version = f"{self.buf[0]}.{self.buf[1]:02d}"

    def drain_packets(self):
        out = self.packets
        self.packets = []
        return out


def _cmd(command: int, payload: bytes) -> bytes:
    return bytes([KISS.FEND, command]) + KISS.escape(payload) + bytes([KISS.FEND])


def _u32(value: int) -> bytes:
    return bytes([(value >> 24) & 0xFF, (value >> 16) & 0xFF,
                  (value >> 8) & 0xFF, value & 0xFF])


def radio_init_commands(freq: int, bw: int, sf: int, cr: int, txp: int) -> List[bytes]:
    """The exact byte sequence that puts an RNode into promiscuous capture.

    Returned rather than written, so the encoding is testable without a port.
    """
    return [
        _cmd(KISS.CMD_FREQUENCY, _u32(freq)),
        _cmd(KISS.CMD_BANDWIDTH, _u32(bw)),
        _cmd(KISS.CMD_TXPOWER, bytes([txp])),
        _cmd(KISS.CMD_SF, bytes([sf])),
        _cmd(KISS.CMD_CR, bytes([cr])),
        _cmd(KISS.CMD_RADIO_STATE, bytes([KISS.RADIO_STATE_ON])),
        _cmd(KISS.CMD_PROMISC, bytes([0x01])),
    ]


def measured_path_loss(tx_power_dbm: float, tx_gain_dbi: float,
                       rx_gain_dbi: float, rssi_dbm: float) -> float:
    """Total path loss implied by a received signal level, in dB."""
    return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - rssi_dbm


def summarize_rows(rows: List[Dict[str, str]]) -> Dict[str, object]:
    """Reduce a capture to the reusable number, or say why it cannot be reduced.

    Returns a dict with ``warnings`` and ``blockers``. A blocker means no
    headline figure is produced — an honest refusal beats a confident number
    computed from data that cannot support it.
    """
    out: Dict[str, object] = {"warnings": [], "blockers": []}
    if not rows:
        out["blockers"].append("capture is empty — no packets were received")
        return out

    rssis, snrs, fresh = [], [], 0
    for r in rows:
        try:
            rssis.append(float(r["rssi_dbm"]))
        except (KeyError, ValueError, TypeError):
            pass
        try:
            snrs.append(float(r["snr_db"]))
        except (KeyError, ValueError, TypeError):
            pass
        if str(r.get("stats_fresh", "0")).strip() in ("1", "true", "True"):
            fresh += 1

    out["packets"] = len(rows)
    out["rssi_n"] = len(rssis)
    if rssis:
        out["rssi_median"] = round(statistics.median(rssis), 1)
        out["rssi_min"] = round(min(rssis), 1)
        out["rssi_max"] = round(max(rssis), 1)
    if snrs:
        out["snr_median"] = round(statistics.median(snrs), 2)
        out["snr_min"] = round(min(snrs), 2)
        out["snr_max"] = round(max(snrs), 2)

    fresh_fraction = fresh / len(rows)
    out["fresh_fraction"] = round(fresh_fraction, 3)

    first = rows[0]
    for key in ("run", "direction", "tx_hw", "rx_hw"):
        out[key] = first.get(key, "")

    if not rssis:
        out["blockers"].append("no usable RSSI values in the capture")
        return out

    if len(rows) < MIN_PACKETS_FOR_SUMMARY:
        out["warnings"].append(
            f"only {len(rows)} packet(s); below {MIN_PACKETS_FOR_SUMMARY} the median "
            "is noise, not a measurement — capture longer before quoting this")

    if fresh_fraction < MIN_FRESH_FRACTION:
        out["blockers"].append(
            f"only {fresh_fraction:.0%} of rows carried fresh RSSI/SNR — the values "
            "are stale-by-one and cannot be paired with their packets. "
            "Re-capture; do not quote a number from this file.")

    if out["blockers"]:
        # Stop before deriving anything. A blocker that still leaves a number in
        # the result is only a blocker for the one consumer that reads the flag
        # -- exactly the half-wired detector shape (honest_failure_modes #4).
        return out

    if out.get("rssi_median", 0) <= FLOOR_DBM + FLOOR_MARGIN_DB:
        out["warnings"].append(
            f"median RSSI {out['rssi_median']} dBm is within {FLOOR_MARGIN_DB} dB of the "
            f"~{FLOOR_DBM} dBm receiver floor — readings are compressed against the "
            "floor and UNDERSTATE the true path loss. Raise TX power or shorten the path.")

    def _num(key: str) -> Optional[float]:
        raw = first.get(key, "")
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    txp, txg, rxg = _num("tx_power_dbm"), _num("tx_gain_dbi"), _num("rx_gain_dbi")
    dist, freq_hz = _num("distance_m"), _num("freq_hz")

    missing = [name for name, val in
               (("tx_power_dbm", txp), ("tx_gain_dbi", txg), ("rx_gain_dbi", rxg))
               if val is None]
    if missing:
        out["blockers"].append(
            f"cannot compute path loss — missing {', '.join(missing)}. "
            "Re-run the capture with those flags set; they are not recoverable later.")
        return out

    pl = measured_path_loss(txp, txg, rxg, float(out["rssi_median"]))
    out["path_loss_db"] = round(pl, 1)

    if dist is None or dist <= 0 or freq_hz is None or freq_hz <= 0:
        out["warnings"].append(
            "no distance/frequency recorded — total path loss is reported, but the "
            "excess-over-free-space figure (the reusable one) cannot be derived")
        return out

    fspl = free_space_path_loss(dist, freq_hz / 1_000_000.0)
    out["fspl_db"] = round(fspl, 1)
    out["excess_loss_db"] = round(pl - fspl, 1)
    out["distance_m"] = dist
    out["freq_mhz"] = round(freq_hz / 1_000_000.0, 3)
    return out


def print_summary(s: Dict[str, object]) -> int:
    print(f"run       : {s.get('run', '')}")
    print(f"direction : {s.get('direction', '')}")
    print(f"tx / rx   : {s.get('tx_hw', '')}  ->  {s.get('rx_hw', '')}")
    print(f"packets   : {s.get('packets', 0)}  "
          f"(fresh stats on {s.get('fresh_fraction', 0):.0%} of rows)")
    if "rssi_median" in s:
        print(f"RSSI      : median {s['rssi_median']} dBm   "
              f"[{s['rssi_min']} .. {s['rssi_max']}]  n={s['rssi_n']}")
    if "snr_median" in s:
        print(f"SNR       : median {s['snr_median']} dB    "
              f"[{s['snr_min']} .. {s['snr_max']}]")
    print()

    for w in s.get("warnings", []):
        print(f"WARNING: {w}")
    for b in s.get("blockers", []):
        print(f"BLOCKED: {b}")
    if s.get("blockers"):
        print("\nNo headline figure produced — see BLOCKED above.")
        return 1

    if "path_loss_db" in s:
        print(f"measured path loss     : {s['path_loss_db']} dB")
    if "excess_loss_db" in s:
        print(f"free-space loss @ {s['distance_m']} m, {s['freq_mhz']} MHz : {s['fspl_db']} dB")
        print()
        print(f"  >>> EXCESS LOSS OVER FREE SPACE: {s['excess_loss_db']} dB  <<<")
        print("      This is the reusable number. It is specific to this path and")
        print("      this direction — summarize the reverse capture separately.")
    return 0


def do_summarize(path: Path) -> int:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        print(f"FAIL: cannot read {path}: {e}")
        return 1
    except csv.Error as e:
        print(f"FAIL: {path} is not readable CSV: {e}")
        return 1
    return print_summary(summarize_rows(rows))


def confirm_radio_on(port, dec: "KissDecoder", timeout_s: float = 5.0):
    """Ask the radio whether it is actually ON, and believe only the answer.

    Born 2026-09-08: this tool used to send RADIO_STATE_ON and then print
    "listening" without ever checking. On a board whose radio silently declines
    to start (one of two identical Heltec V4s did exactly that, reporting no
    error at all) it would capture nothing forever and look perfectly healthy —
    honest_failure_modes #9, in this file's own code.

    ⚠️ The subtlety that makes this non-trivial: a ``CMD_RADIO_STATE`` frame
    arriving just after we SET the state can be an echo of the value we sent
    rather than an observation. On that day a loose probe read the echo as
    ``01`` while the radio was off, and only an explicit ASK told the truth. So
    the reported state is CLEARED before asking, and only a reply that lands
    afterwards is accepted.

    Returns (state, reason) — state is None if the radio never answered.
    """
    dec.radio_state = None
    dec.errors.clear()
    # Discard anything already buffered -- clearing only the decoder would still
    # let a queued echo be read back and believed a moment later.
    try:
        port.reset_input_buffer()
    except (AttributeError, OSError):
        pass
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        port.write(_cmd(KISS.CMD_RADIO_STATE, bytes([KISS.RADIO_STATE_ASK])))
        wait_until = min(time.monotonic() + 0.5, deadline)
        while time.monotonic() < wait_until:
            dec.feed(port.read(256))
            if dec.radio_state is not None:
                break
        if dec.radio_state is not None:
            break

    if dec.radio_state is None:
        return None, "the radio never answered RADIO_STATE_ASK"
    if dec.radio_state == KISS.RADIO_STATE_OFF:
        return dec.radio_state, ("the radio reports state OFF (0x00) after being told to "
                                 "turn on, and reported no error explaining why")
    return dec.radio_state, f"state 0x{dec.radio_state:02x}"


def do_capture(args) -> int:
    if not _HAS_SERIAL:
        print("FAIL: pyserial is not available — cannot open the RNode.")
        print("      Install it with: pip3 install pyserial")
        return 1

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "run": args.run, "direction": args.direction,
        "tx_hw": args.tx_hw, "rx_hw": args.rx_hw,
        "tx_power_dbm": args.tx_power, "tx_gain_dbi": args.tx_gain,
        "rx_gain_dbi": args.rx_gain, "freq_hz": args.freq, "bw_hz": args.bw,
        "sf": args.sf, "cr": args.cr, "distance_m": args.distance,
    }

    try:
        port = serial.Serial(
            port=args.port, baudrate=args.baud, bytesize=8,
            parity=serial.PARITY_NONE, stopbits=1, timeout=0.1,
        )
    except (serial.SerialException, OSError) as e:
        print(f"FAIL: cannot open {args.port}: {e}")
        return 1

    dec = KissDecoder()
    try:
        time.sleep(2.0)  # let the board settle after the port opens
        port.write(_cmd(KISS.CMD_DETECT, bytes([KISS.DETECT_REQ])))
        port.write(_cmd(KISS.CMD_FW_VERSION, bytes([0x00])))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not dec.detected:
            dec.feed(port.read(256))
        if not dec.detected:
            print(f"FAIL: no RNode responded on {args.port}. Wrong port, or the board")
            print("      is not running RNode firmware. Check with: rnodeconf -i <port>")
            return 1
        print(f"RNode detected on {args.port}"
              + (f", firmware {dec.fw_version}" if dec.fw_version else ""))

        for command in radio_init_commands(args.freq, args.bw, args.sf, args.cr,
                                           args.tx_power_setting):
            port.write(command)
            time.sleep(0.05)

        state, why = confirm_radio_on(port, dec)
        if state != KISS.RADIO_STATE_ON:
            print(f"FAIL: the radio did not come up — {why}")
            print("      Refusing to capture. A receiver that is switched off looks")
            print("      exactly like a silent band, and every reading would be a")
            print("      false negative you could not tell from a real one.")
            for err in dec.errors:
                print(f"      radio reported: {err}")
            print("      Check the board with: rnodeconf -i <port>")
            return 1
        print(f"radio    : ON (confirmed by RADIO_STATE_ASK)")

        print(f"listening: {args.freq/1e6:.3f} MHz  bw {args.bw/1e3:.1f} kHz  "
              f"sf {args.sf}  cr 4/{args.cr}")
        print(f"writing  : {out_path}")
        print("Ctrl-C to stop.\n")

        new_file = not out_path.exists() or out_path.stat().st_size == 0
        with open(out_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()

            count = 0
            last_stat_seq = -1
            end = time.monotonic() + args.seconds if args.seconds else None
            while end is None or time.monotonic() < end:
                dec.feed(port.read(256))
                for stat_seq, rssi, snr, nbytes in dec.drain_packets():
                    fresh = 1 if stat_seq != last_stat_seq else 0
                    last_stat_seq = stat_seq
                    row = dict(meta)
                    row.update({
                        "ts_iso": datetime.datetime.now().astimezone().isoformat(),
                        "rssi_dbm": rssi if rssi is not None else "",
                        "snr_db": snr if snr is not None else "",
                        "bytes": nbytes, "stats_fresh": fresh,
                    })
                    writer.writerow(row)
                    f.flush()
                    count += 1
                    flag = "" if fresh else "   [STALE STATS]"
                    print(f"[{count:5d}] {rssi} dBm  SNR {snr} dB  {nbytes} bytes{flag}")
    except KeyboardInterrupt:
        print("\nstopped by operator")
    except (serial.SerialException, OSError) as e:
        print(f"\nFAIL: serial error during capture: {e}")
        return 1
    finally:
        try:
            port.close()
        except (serial.SerialException, OSError):
            pass

    print(f"\nWrote {out_path}")
    print(f"Now run: python3 {sys.argv[0]} --summarize {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Capture per-packet RSSI/SNR from an RNode and reduce it to a "
                    "reusable path-loss figure.")
    ap.add_argument("--capture", dest="port", metavar="PORT",
                    help="serial port of the receiving RNode, e.g. /dev/ttyUSB0")
    ap.add_argument("--summarize", metavar="CSV",
                    help="reduce an existing capture to the reusable number")
    ap.add_argument("--out", metavar="CSV", help="capture output file (appended)")
    ap.add_argument("--seconds", type=int, default=0,
                    help="stop after N seconds (default: run until Ctrl-C)")
    ap.add_argument("--baud", type=int, default=115200)

    prov = ap.add_argument_group(
        "provenance", "recorded on EVERY row — without these a reading is not reusable")
    prov.add_argument("--run", default="", help="label for this run, e.g. 'ohia-250ft'")
    prov.add_argument("--direction", default="",
                      help="which way this reading runs, e.g. '<far-box>-><near-box>'")
    prov.add_argument("--tx-hw", default="", help="hardware at the TRANSMITTING end")
    prov.add_argument("--rx-hw", default="", help="hardware at the RECEIVING end (this one)")
    prov.add_argument("--tx-power", type=float, default=None,
                      help="dBm actually radiated by the FAR end")
    prov.add_argument("--tx-gain", type=float, default=0.0, help="far-end antenna gain, dBi")
    prov.add_argument("--rx-gain", type=float, default=0.0, help="this end's antenna gain, dBi")
    prov.add_argument("--distance", type=float, default=None, help="path length in metres")

    radio = ap.add_argument_group("radio", "must match the far end exactly")
    radio.add_argument("--freq", type=int, default=903000000, help="Hz")
    radio.add_argument("--bw", type=int, default=125000, help="Hz")
    radio.add_argument("--sf", type=int, default=8)
    radio.add_argument("--cr", type=int, default=5, help="coding rate denominator (4/N)")
    radio.add_argument("--tx-power-setting", type=int, default=0,
                       help="dBm to configure on THIS radio; capture never transmits, "
                            "so 0 is correct unless you know otherwise")

    args = ap.parse_args()

    if args.summarize:
        return do_summarize(Path(args.summarize).expanduser())

    if not args.port:
        ap.print_help()
        return 2
    if not args.out:
        print("FAIL: --out is required for a capture. A reading kept only in the")
        print("      terminal is the thing this tool exists to prevent.")
        return 1

    missing = [n for n, v in (("--tx-power", args.tx_power), ("--distance", args.distance))
               if v is None]
    if missing:
        print(f"WARNING: {', '.join(missing)} not given. The capture will record RSSI/SNR,")
        print("         but the path-loss figure cannot be derived from it later, and")
        print("         these values are NOT recoverable after you leave the site.")
        print()

    return do_capture(args)


if __name__ == "__main__":
    sys.exit(main())
