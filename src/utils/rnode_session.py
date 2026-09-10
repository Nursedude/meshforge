"""rnode_session — the ONE place that opens an RNode and changes its radio mode.

Born 2026-09-08, self-inflicted. ``link_test_capture`` put two production RNodes
into **promiscuous mode and walked away**. Promiscuous is a MODE the device
stays in, and RNS's own ``RNodeInterface`` init never clears it — so both radios
stopped receiving normally, and stayed that way, until something reset them.

The failure was maximally misleading. Transmit worked, so one end's bytes
reached the far end and its downlink counter moved; nothing ever came back. That
reads as a one-way RF path — a real and plausible finding — and cost an
afternoon of noise floors, antenna asymmetry and a suspected PA receive fault
before anyone noticed the radios only went deaf AFTER the tool touched them.
⚠️ It was invisible that long because TCP was also up: a redundant path masking
a dead one. On a **pure-RF gateway** the same bug is a total outage.

So this module exists for the same reason ``open_reticulum()`` (MF019) and
``MeshtasticConnection`` (MF007) exist: a shared device whose STATE outlives the
process that changed it needs exactly one owner.

The contract
------------
``rnode_session()`` is a context manager that:

* **refuses a port another process holds** (review 2026-09-09, finding 4). A
  tty has no built-in exclusivity: pyserial's default sets no ``TIOCEXCL`` and
  RNS's ``RNodeInterface`` sets none either, so a second ``open()`` on the
  RNode that rnsd is running SUCCEEDS silently — and the session then splits
  the byte stream with rnsd's read loop (DETECT/ASK replies eaten → a
  misleading "no RNode responded") or, worse, rewrites freq/bw/sf/cr, TX power
  0 and RADIO_STATE into the fleet's production radio, which rnsd does not
  re-validate until its next restart. So the holders of the device are looked
  up first (``/proc/*/fd`` + ``lsof``, the ``device_scanner`` precedent), the
  session refuses with the owner named, and the port is opened
  ``exclusive=True`` so nothing can do the same to US.
* refuses to yield unless the device answers as an RNode (detect + firmware),
* refuses to yield unless the radio **confirms** it is on — asked explicitly
  with ``RADIO_STATE_ASK``, never inferred from having sent ``RADIO_STATE_ON``.
  A ``CMD_RADIO_STATE`` frame arriving right after we set the state can be an
  ECHO of the value we just sent; on 2026-09-08 a loose probe read that echo as
  ``0x01`` off a radio that was in fact off. Only the reply to an ASK counts,
  and the port buffer is drained first so a queued echo cannot be mistaken for
  the answer. Drained, not blindly flushed (finding 7): the firmware's
  ``CMD_ERROR`` reply to ON (``ERROR_INITRADIO`` is the real one) sits in that
  same buffer, and discarding it manufactured "reported no error explaining
  why" for a radio that HAD explained.
* **restores every mode and state it changed**, on every exit path — normal
  return, exception, and KeyboardInterrupt — and leaves a witness if it cannot.
  That includes ``RADIO_STATE`` (finding 14): the prior state is ASKed before
  the session turns the radio on, and a radio that was OFF is put back OFF.
  rnsd's ``detach()`` parks its RNode OFF on release, and a battery field-kit
  board left ON draws continuous SX126x RX current with no host reading it.

It restores only what it set. A caller that never asked for promiscuous mode
does not get it turned off underneath them; a radio that was already ON is
left ON.

Framing hygiene (finding 9): whatever the device was mid-sentence on when the
port opened — telemetry, a DATA frame on the OLD modem parameters, a partial
frame — is decoded and discarded before DETECT and again before the final ASK,
and the decoder's framing state is reset. Otherwise a half frame ahead of the
DETECT reply swallows it ("no RNode responded" on a healthy device), and
packets received on the old parameters surface at yield as provenance-tagged
rows for the new ones.

Not covered here: this module does not transmit. Keying a PA from a script is a
good way to damage an amplifier that has no antenna attached.
"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

from utils.safe_import import safe_import

serial, _HAS_SERIAL = safe_import('serial')

DEFAULT_BAUD = 115200
SETTLE_S = 2.0
DETECT_TIMEOUT_S = 5.0
RADIO_CONFIRM_TIMEOUT_S = 5.0
HOLDER_LOOKUP_TIMEOUT_S = 5.0

#: Upper bound on read() calls while draining what the device already sent.
#: With pyserial's 0.1 s read timeout that is ~6 s worst case against a device
#: that never stops talking — bounded, never a spin.
DRAIN_MAX_READS = 64


class RNodeUnavailable(RuntimeError):
    """The device is not a usable RNode right now, and the reason is in the message."""


class KISS:
    """RNode KISS constants — transcribed from markqvist/LoRaMon and rnodeconf."""
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


def kiss_cmd(command: int, payload: bytes) -> bytes:
    return bytes([KISS.FEND, command]) + KISS.escape(payload) + bytes([KISS.FEND])


def u32(value: int) -> bytes:
    return bytes([(value >> 24) & 0xFF, (value >> 16) & 0xFF,
                  (value >> 8) & 0xFF, value & 0xFF])


class KissDecoder:
    """Byte-stream state machine: RNode serial -> packets, stats, state, errors.

    Pure and hardware-free by design — feed it bytes, read the fields. That is
    what makes this layer testable without a radio on the bench, which is
    otherwise the only place it could be tested at all.

    Stats freshness (review 2026-09-09, finding 10): RSSI and SNR arrive in
    SEPARATE frames. ``stat_seq`` used to bump on EITHER, so a packet preceded
    by only one of the pair was flagged fresh while the other value was the
    previous packet's — the flag lied in exactly the case it was built to
    catch. Now each half is tracked on its own: a packet carries a value only
    if that value arrived since the previous packet (otherwise None), and
    ``stat_seq`` advances only when BOTH did. RNS's ``process_incoming`` clears
    its ``r_stat_*`` AFTER the packet, so on the firmware the stats precede the
    data they describe; the per-half tracking is what makes a missing half
    visible rather than silently stale.
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
        self.radio_state: Optional[int] = None
        self.errors: List[str] = []
        # Advances once per packet that had BOTH an RSSI and an SNR frame
        # arrive since the packet before it; lets a consumer tell whether the
        # pair it is about to record actually belongs to the packet in hand.
        self.stat_seq = 0
        self._rssi_fresh = False
        self._snr_fresh = False
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

    def reset_framing(self) -> None:
        """Forget a frame in progress. Used after the port buffer is discarded:
        a DATA frame cut in half by the flush would otherwise stay open and
        swallow the next reply's FEND as its own terminator (finding 9)."""
        self.in_frame = False
        self.escape = False
        self.command = KISS.CMD_UNKNOWN
        self.buf = bytearray()

    def feed(self, data: bytes) -> int:
        """Feed raw serial bytes. Returns the number of packets completed."""
        before = len(self.packets)
        for byte in data:
            self._feed_byte(byte)
        return len(self.packets) - before

    def _feed_byte(self, byte: int) -> None:
        if self.in_frame and byte == KISS.FEND and self.command == KISS.CMD_DATA:
            self.in_frame = False
            if self._rssi_fresh and self._snr_fresh:
                self.stat_seq += 1
            self.packets.append((
                self.stat_seq,
                self.rssi if self._rssi_fresh else None,
                self.snr if self._snr_fresh else None,
                len(self.buf),
            ))
            self._rssi_fresh = self._snr_fresh = False
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
            # Stat bytes may arrive KISS-escaped (an SNR of -16.0 dB is raw
            # 0xC0). An UNescaped 0xC0 is indistinguishable from FEND and is
            # lost here as it is in RNS; an escaped one is recovered.
            b = self._unescape(byte)
            if b is not None:
                self.rssi = b - KISS.RSSI_OFFSET
                self._rssi_fresh = True
        elif self.command == KISS.CMD_STAT_SNR:
            b = self._unescape(byte)
            if b is not None:
                self.snr = int.from_bytes(bytes([b]), byteorder="big", signed=True) * 0.25
                self._snr_fresh = True
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


def radio_config_commands(freq: int, bw: int, sf: int, cr: int, txp: int) -> List[bytes]:
    """Configuration only — NOT radio state, NOT promiscuous.

    Split deliberately: config is idempotent and leaves nothing behind, whereas
    state and mode are what the session has to remember and restore.
    """
    return [
        kiss_cmd(KISS.CMD_FREQUENCY, u32(freq)),
        kiss_cmd(KISS.CMD_BANDWIDTH, u32(bw)),
        kiss_cmd(KISS.CMD_TXPOWER, bytes([txp])),
        kiss_cmd(KISS.CMD_SF, bytes([sf])),
        kiss_cmd(KISS.CMD_CR, bytes([cr])),
    ]


def _proc_comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip() or "?"
    except OSError:
        return "?"


def port_holders(port_path: str, *, timeout_s: float = HOLDER_LOOKUP_TIMEOUT_S) -> List[str]:
    """Other processes that currently hold ``port_path`` open, as "pid (comm)".

    Two views, unioned: a ``/proc/*/fd`` scan (no dependency; sees every
    process this user may inspect) and ``lsof`` (the ``device_scanner``
    precedent; the same permission limits, kept for the boxes where one of the
    two is unavailable). An empty list from a NON-root caller means "none this
    user can see", so the session still opens ``exclusive=True`` behind it.
    Never raises; a lookup that cannot run simply contributes nothing.
    """
    real = os.path.realpath(port_path)
    if not os.path.exists(real):
        return []
    me = os.getpid()
    holders: Dict[int, str] = {}

    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        if pid == me:
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue  # another user's process, or gone — not ours to read
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if target == real:
                holders[pid] = _proc_comm(pid)
                break

    try:
        result = subprocess.run(["lsof", "-t", real], capture_output=True,
                                text=True, timeout=timeout_s)
        for tok in (result.stdout or "").split():
            try:
                pid = int(tok)
            except ValueError:
                continue
            if pid != me and pid not in holders:
                holders[pid] = _proc_comm(pid)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    return [f"{pid} ({comm})" for pid, comm in sorted(holders.items())]


def drain_stale_input(port, dec: KissDecoder, *, max_reads: int = DRAIN_MAX_READS) -> int:
    """Decode what the device already sent, then discard it — keeping errors.

    Everything buffered is FED to the decoder first, so a ``CMD_ERROR`` the
    firmware emitted (finding 7) is recorded rather than thrown away; then the
    packets it carried (received on whatever parameters were in force before
    the session — finding 9) and any radio-state frame (a possible echo of a
    value we set) are dropped, the framing state is reset so a half frame
    cannot swallow the next reply, and the port buffer is flushed.
    Returns the number of bytes discarded.
    """
    n = 0
    for _ in range(max_reads):
        chunk = port.read(256)
        if not chunk:
            break
        n += len(chunk)
        dec.feed(chunk)
    dec.drain_packets()
    dec.radio_state = None
    dec.reset_framing()
    try:
        port.reset_input_buffer()
    except (AttributeError, OSError):
        pass
    return n


def ask_radio_state(port, dec: KissDecoder,
                    timeout_s: float = RADIO_CONFIRM_TIMEOUT_S) -> Optional[int]:
    """Send ``RADIO_STATE_ASK`` until the device answers; None if it never does.

    Drains the buffer first so only the reply to THIS ask can be believed.
    """
    drain_stale_input(port, dec)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        port.write(kiss_cmd(KISS.CMD_RADIO_STATE, bytes([KISS.RADIO_STATE_ASK])))
        wait_until = min(time.monotonic() + 0.5, deadline)
        while time.monotonic() < wait_until:
            dec.feed(port.read(256))
            if dec.radio_state is not None:
                return dec.radio_state
    return dec.radio_state


def confirm_radio_on(port, dec: KissDecoder,
                     timeout_s: float = RADIO_CONFIRM_TIMEOUT_S):
    """Ask whether the radio is actually ON, and believe only the answer.

    Setting ``RADIO_STATE_ON`` is not evidence the radio started: one of two
    identical Heltec V4s silently declined, reporting state ``0x00`` forever and
    emitting no error at all. A consumer that assumes success reads a dead
    receiver as a silent band — the two are indistinguishable downstream.

    Returns ``(state, reason)``; ``state`` is None if the radio never answered.
    ``reason`` carries the device's own ``CMD_ERROR`` text when it sent one —
    the decoder's error list is NOT cleared here, and the buffer is drained
    through the decoder, so an error emitted in reply to ON survives.
    """
    state = ask_radio_state(port, dec, timeout_s=timeout_s)
    errs = "; ".join(dec.errors) if dec.errors else ""
    if state is None:
        why = "the radio never answered RADIO_STATE_ASK"
    elif state == KISS.RADIO_STATE_OFF:
        why = "the radio reports state OFF (0x00) after being told to turn on"
        why += (f", and reported: {errs}" if errs
                else ", and reported no error explaining why")
        return state, why
    else:
        why = f"state 0x{state:02x}"
    if errs:
        why += f" (device reported: {errs})"
    return state, why


class RNodeSession:
    """A live RNode with its mode changes tracked so they can be undone."""

    def __init__(self, port, decoder: KissDecoder, port_path: str):
        self.port = port
        self.decoder = decoder
        self.port_path = port_path
        self.restore_warnings: List[str] = []
        self._promiscuous_set = False
        #: What RADIO_STATE_ASK answered BEFORE this session turned the radio
        #: on: 0x00 / 0x01, or None if it never answered. Restored on exit.
        self.prior_radio_state: Optional[int] = None
        self._radio_turned_on = False

    @property
    def fw_version(self) -> Optional[str]:
        return self.decoder.fw_version

    def read(self, n: int = 256) -> bytes:
        return self.port.read(n)

    def set_promiscuous(self, enabled: bool) -> None:
        """Enter/leave promiscuous mode. The session remembers and will undo it."""
        self.port.write(kiss_cmd(KISS.CMD_PROMISC, bytes([0x01 if enabled else 0x00])))
        self._promiscuous_set = enabled

    def _restore(self) -> None:
        """Undo only the modes and state this session set. Never silent on failure."""
        if self._promiscuous_set:
            try:
                self.port.write(kiss_cmd(KISS.CMD_PROMISC, bytes([0x00])))
                self.port.flush()
                time.sleep(0.2)
                self._promiscuous_set = False
            except Exception as exc:  # noqa: BLE001 - must not mask the caller's error
                self.restore_warnings.append(
                    f"could not clear promiscuous mode on {self.port_path}: {exc}. "
                    "That RNode may not receive normally until it is reset.")

        if not self._radio_turned_on:
            return
        if self.prior_radio_state == KISS.RADIO_STATE_OFF:
            try:
                self.port.write(kiss_cmd(KISS.CMD_RADIO_STATE, bytes([KISS.RADIO_STATE_OFF])))
                self.port.flush()
                time.sleep(0.2)
                self._radio_turned_on = False
            except Exception as exc:  # noqa: BLE001 - must not mask the caller's error
                self.restore_warnings.append(
                    f"could not turn the radio back OFF on {self.port_path}: {exc}. "
                    "It was OFF before this session and has been left ON.")
        elif self.prior_radio_state is None:
            self.restore_warnings.append(
                f"the radio on {self.port_path} never answered RADIO_STATE_ASK before "
                "this session turned it on, so its prior state is unknown and it has "
                "been left ON. If it was parked OFF (rnsd does that on release), "
                "it is now drawing RX current with no host reading it.")


@contextmanager
def rnode_session(port_path: str, *, freq: int, bw: int, sf: int, cr: int,
                  txpower: int = 0, promiscuous: bool = False,
                  baud: int = DEFAULT_BAUD, settle_s: float = SETTLE_S,
                  serial_mod=None):
    """Open an RNode, configure it, and guarantee its mode and state are handed back.

    Raises RNodeUnavailable if another process holds the port, the device does
    not answer as an RNode, or the radio does not confirm it is on. Yields an
    RNodeSession.
    """
    mod = serial_mod if serial_mod is not None else serial
    if mod is None:
        raise RNodeUnavailable("pyserial is not available; cannot open an RNode")

    holders = port_holders(port_path)
    if holders:
        raise RNodeUnavailable(
            f"{port_path} is held open by {', '.join(holders)} — refusing to open "
            "it a second time. Two readers split the byte stream, and every "
            "parameter this session writes would land in THAT process's radio "
            "(rnsd does not re-validate its RNode until restart). Stop the "
            "holder, or use a different port.")

    try:
        port = mod.Serial(port=port_path, baudrate=baud, bytesize=8,
                          parity="N", stopbits=1, timeout=0.1, exclusive=True)
    except Exception as exc:  # noqa: BLE001 - surfaced with the path that failed
        raise RNodeUnavailable(f"cannot open {port_path}: {exc}") from exc

    dec = KissDecoder()
    session = RNodeSession(port, dec, port_path)
    try:
        time.sleep(settle_s)
        # Whatever the device was mid-sentence on: decode it, drop it, and
        # start DETECT on a clean frame boundary (finding 9). Errors emitted
        # before we sent anything describe the OLD state — not ours to report.
        drain_stale_input(port, dec)
        dec.errors.clear()

        port.write(kiss_cmd(KISS.CMD_DETECT, bytes([KISS.DETECT_REQ])))
        port.write(kiss_cmd(KISS.CMD_FW_VERSION, bytes([0x00])))
        deadline = time.monotonic() + DETECT_TIMEOUT_S
        while time.monotonic() < deadline and not dec.detected:
            dec.feed(port.read(256))
        if not dec.detected:
            raise RNodeUnavailable(
                f"no RNode responded on {port_path} — wrong port, or the board is "
                "not running RNode firmware (check with: rnodeconf -i <port>)")

        # Learn the state we are about to change, so it can be put back
        # (finding 14). Asked BEFORE any write that could alter it.
        session.prior_radio_state = ask_radio_state(port, dec)

        for command in radio_config_commands(freq, bw, sf, cr, txpower):
            port.write(command)
            time.sleep(0.05)
        port.write(kiss_cmd(KISS.CMD_RADIO_STATE, bytes([KISS.RADIO_STATE_ON])))
        session._radio_turned_on = True
        time.sleep(0.05)

        state, why = confirm_radio_on(port, dec)
        if state != KISS.RADIO_STATE_ON:
            raise RNodeUnavailable(
                f"the radio on {port_path} did not come up — {why}")

        if promiscuous:
            session.set_promiscuous(True)

        yield session
    finally:
        session._restore()
        for warning in session.restore_warnings:
            print(f"WARNING: {warning}")
        try:
            port.close()
        except Exception:  # noqa: BLE001
            pass
