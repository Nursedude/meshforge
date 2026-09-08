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

* refuses to yield unless the device answers as an RNode (detect + firmware),
* refuses to yield unless the radio **confirms** it is on — asked explicitly
  with ``RADIO_STATE_ASK``, never inferred from having sent ``RADIO_STATE_ON``.
  A ``CMD_RADIO_STATE`` frame arriving right after we set the state can be an
  ECHO of the value we just sent; on 2026-09-08 a loose probe read that echo as
  ``0x01`` off a radio that was in fact off. Only the reply to an ASK counts,
  and the port buffer is flushed first so a queued echo cannot be mistaken for
  the answer.
* **restores every mode it changed**, on every exit path — normal return,
  exception, and KeyboardInterrupt — and leaves a witness if it cannot.

It restores only what it set. A caller that never asked for promiscuous mode
does not get it turned off underneath them.

Not covered here: this module does not transmit. Keying a PA from a script is a
good way to damage an amplifier that has no antenna attached.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import List, Optional, Tuple

from utils.safe_import import safe_import

serial, _HAS_SERIAL = safe_import('serial')

DEFAULT_BAUD = 115200
SETTLE_S = 2.0
DETECT_TIMEOUT_S = 5.0
RADIO_CONFIRM_TIMEOUT_S = 5.0


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


def confirm_radio_on(port, dec: KissDecoder,
                     timeout_s: float = RADIO_CONFIRM_TIMEOUT_S):
    """Ask whether the radio is actually ON, and believe only the answer.

    Setting ``RADIO_STATE_ON`` is not evidence the radio started: one of two
    identical Heltec V4s silently declined, reporting state ``0x00`` forever and
    emitting no error at all. A consumer that assumes success reads a dead
    receiver as a silent band — the two are indistinguishable downstream.

    Returns ``(state, reason)``; ``state`` is None if the radio never answered.
    """
    dec.radio_state = None
    dec.errors.clear()
    # Discard anything already buffered: clearing only the decoder would still
    # let a queued ECHO of the value we just set be read back and believed.
    try:
        port.reset_input_buffer()
    except (AttributeError, OSError):
        pass

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        port.write(kiss_cmd(KISS.CMD_RADIO_STATE, bytes([KISS.RADIO_STATE_ASK])))
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
        return dec.radio_state, ("the radio reports state OFF (0x00) after being told "
                                 "to turn on, and reported no error explaining why")
    return dec.radio_state, f"state 0x{dec.radio_state:02x}"


class RNodeSession:
    """A live RNode with its mode changes tracked so they can be undone."""

    def __init__(self, port, decoder: KissDecoder, port_path: str):
        self.port = port
        self.decoder = decoder
        self.port_path = port_path
        self.restore_warnings: List[str] = []
        self._promiscuous_set = False

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
        """Undo only the modes this session set. Never silent on failure."""
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


@contextmanager
def rnode_session(port_path: str, *, freq: int, bw: int, sf: int, cr: int,
                  txpower: int = 0, promiscuous: bool = False,
                  baud: int = DEFAULT_BAUD, settle_s: float = SETTLE_S,
                  serial_mod=None):
    """Open an RNode, configure it, and guarantee its mode is handed back.

    Raises RNodeUnavailable if the device does not answer as an RNode or the
    radio does not confirm it is on. Yields an RNodeSession.
    """
    mod = serial_mod if serial_mod is not None else serial
    if mod is None:
        raise RNodeUnavailable("pyserial is not available; cannot open an RNode")

    try:
        port = mod.Serial(port=port_path, baudrate=baud, bytesize=8,
                          parity="N", stopbits=1, timeout=0.1)
    except Exception as exc:  # noqa: BLE001 - surfaced with the path that failed
        raise RNodeUnavailable(f"cannot open {port_path}: {exc}") from exc

    dec = KissDecoder()
    session = RNodeSession(port, dec, port_path)
    try:
        time.sleep(settle_s)
        port.write(kiss_cmd(KISS.CMD_DETECT, bytes([KISS.DETECT_REQ])))
        port.write(kiss_cmd(KISS.CMD_FW_VERSION, bytes([0x00])))
        deadline = time.monotonic() + DETECT_TIMEOUT_S
        while time.monotonic() < deadline and not dec.detected:
            dec.feed(port.read(256))
        if not dec.detected:
            raise RNodeUnavailable(
                f"no RNode responded on {port_path} — wrong port, or the board is "
                "not running RNode firmware (check with: rnodeconf -i <port>)")

        for command in radio_config_commands(freq, bw, sf, cr, txpower):
            port.write(command)
            time.sleep(0.05)
        port.write(kiss_cmd(KISS.CMD_RADIO_STATE, bytes([KISS.RADIO_STATE_ON])))
        time.sleep(0.05)

        state, why = confirm_radio_on(port, dec)
        if state != KISS.RADIO_STATE_ON:
            errs = ("; ".join(dec.errors)) if dec.errors else "no error reported"
            raise RNodeUnavailable(
                f"the radio on {port_path} did not come up — {why} ({errs})")

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
