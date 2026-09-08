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

Promiscuous mode is OFF by default -- measured, not assumed
-----------------------------------------------------------
This tool originally forced promiscuous mode, because LoRaMon does. On the
first real capture (2026-09-08, RNode firmware 1.86, ESP32-S3) that produced
**zero packets in 160 s** while the far end's own TX counter proved it had
radiated 8,840 B. A raw hexdump of the serial stream, promiscuous on then off,
over one beacon run, said why:

    PROMISC=ON   527 raw bytes, 0 data frames   (only CHTM/PHYPRM/BAT telemetry)
    PROMISC=OFF  3338 raw bytes, 13 data frames, RSSI -71 dBm, SNR -7.5 dB

On this firmware, promiscuous mode stops CMD_DATA frames reaching the host
entirely -- so the one setting the tool believed it needed was the reason it
measured nothing. Normal mode delivers the data frames AND their CMD_STAT_RSSI /
CMD_STAT_SNR companions, which is everything this measurement wants. Normal mode
is also the better filter for a path-loss reading: only well-formed frames on the
configured modem parameters arrive, so a stray transmitter is less likely to be
averaged into the result. ``--promiscuous`` remains available for a board whose
firmware behaves the other way -- but it is opt-in, and the silence witness below
will tell you which one you have within seconds rather than after a wasted run.

A capture that hears nothing says so WHILE it runs
--------------------------------------------------
The zero-packet run above looked perfectly healthy for its full 160 s: radio
confirmed ON, parameters printed, not one word about the silence. That is this
project's oldest defect class wearing a new hat -- a degraded state rendered as
a valid-looking one -- and on a hill, in the field, it costs a trip. The capture
now reports how long it has been silent, and names promiscuous mode as the first
thing to suspect. Silence is a finding, not a state to sit in quietly.

It always hands the radio back
------------------------------
Promiscuous mode, if enabled, is a mode the device STAYS in. RNS's own interface
init does not clear it, so an RNode left promiscuous stops receiving normally --
the capture would break the very link it exists to measure. This tool clears it
on every exit path, including Ctrl-C and errors.

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
from utils.rnode_session import (  # noqa: E402
    RNodeUnavailable,
    rnode_session,
)

# Minimum packets before a summary is worth quoting. Below this the median is
# noise dressed as a measurement.
MIN_PACKETS_FOR_SUMMARY = 20

# Below this fraction of rows carrying fresh stats, the RSSI/SNR pairing is
# unreliable and the headline number is withheld rather than qualified.
MIN_FRESH_FRACTION = 0.5

# How long a capture may hear nothing before it says so out loud. Short enough
# that a wrong mode is caught while you are still standing next to the radio.
SILENCE_WARN_S = 20.0

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


def report_silence(silent_s: float, count: int, promiscuous: bool) -> None:
    """Say that nothing is arriving, and name the likeliest reason.

    A capture that prints its parameters and then goes quiet is indistinguishable
    from a working one -- the 2026-09-08 zero-packet run looked healthy for its
    entire 160 s. Silence gets a witness (honest_failure_modes #9), and the
    witness carries the diagnosis rather than leaving it to be rediscovered.
    """
    if count == 0:
        print(f"  ... NOTHING RECEIVED for {silent_s:.0f}s. The radio is confirmed on, so "
              f"either nothing is transmitting on these parameters, or the host is not "
              f"being given the frames.")
        if promiscuous:
            print("      Suspect --promiscuous FIRST: on RNode firmware 1.86 it stops "
                  "CMD_DATA frames reaching the host entirely. Re-run without it.")
        else:
            print("      Check that the far end is actually radiating -- "
                  "scripts/link_test_beacon.py proves it from the TX counter.")
    else:
        print(f"  ... silent for {silent_s:.0f}s ({count} packet(s) so far).")


def do_capture(args) -> int:
    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "run": args.run, "direction": args.direction,
        "tx_hw": args.tx_hw, "rx_hw": args.rx_hw,
        "tx_power_dbm": args.tx_power, "tx_gain_dbi": args.tx_gain,
        "rx_gain_dbi": args.rx_gain, "freq_hz": args.freq, "bw_hz": args.bw,
        "sf": args.sf, "cr": args.cr, "distance_m": args.distance,
    }

    # utils.rnode_session owns opening the device, confirming the radio is
    # genuinely ON, and — the part this tool got wrong on 2026-09-08 — putting
    # every mode back on the way out, on every exit path.
    try:
        with rnode_session(args.port, freq=args.freq, bw=args.bw, sf=args.sf,
                           cr=args.cr, txpower=args.tx_power_setting,
                           promiscuous=args.promiscuous, baud=args.baud) as rn:
            print(f"RNode detected on {args.port}"
                  + (f", firmware {rn.fw_version}" if rn.fw_version else ""))
            print("radio    : ON (confirmed by RADIO_STATE_ASK)")
            print(f"promisc  : {'ON (opt-in)' if args.promiscuous else 'off'}")
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
                started = time.monotonic()
                last_packet_at = started
                next_silence_report = started + SILENCE_WARN_S
                end = started + args.seconds if args.seconds else None
                try:
                    while end is None or time.monotonic() < end:
                        rn.decoder.feed(rn.read(256))
                        now = time.monotonic()
                        if now >= next_silence_report:
                            report_silence(now - last_packet_at, count,
                                           args.promiscuous)
                            next_silence_report = now + SILENCE_WARN_S
                        for stat_seq, rssi, snr, nbytes in rn.decoder.drain_packets():
                            last_packet_at = time.monotonic()
                            next_silence_report = last_packet_at + SILENCE_WARN_S
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
                            print(f"[{count:5d}] {rssi} dBm  SNR {snr} dB  "
                                  f"{nbytes} bytes{flag}")
                except KeyboardInterrupt:
                    print("\nstopped by operator")
    except RNodeUnavailable as exc:
        print(f"FAIL: {exc}")
        print("      Refusing to capture. A receiver that is switched off looks")
        print("      exactly like a silent band, and every reading would be a")
        print("      false negative you could not tell from a real one.")
        return 1

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
    ap.add_argument("--promiscuous", action="store_true",
                    help="put the RNode in promiscuous mode. OFF by default: on "
                         "firmware 1.86 promiscuous mode delivers NO data frames at "
                         "all (measured 2026-09-08). Opt in only for a board you have "
                         "shown behaves otherwise.")

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
