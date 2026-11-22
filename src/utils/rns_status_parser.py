"""
RNS Status Parser — Structured parsing of rnstatus CLI output.

Provides dataclasses and a parser function that converts the raw text
output of ``rnstatus`` into typed Python objects.

Used by:
- rns_monitor_mixin.py  (live status monitor)
- rns_diagnostics_mixin.py  (health checks — future consolidation)

No external dependencies — pure stdlib.
"""

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InterfaceStatus(Enum):
    UP = "Up"
    DOWN = "Down"
    UNKNOWN = "Unknown"


class InterfaceMode(Enum):
    FULL = "Full"
    GATEWAY = "Gateway"
    ACCESS_POINT = "Access Point"
    BOUNDARY = "Boundary"
    UNKNOWN = "Unknown"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TrafficCounters:
    """TX or RX traffic data from a single rnstatus line."""
    bytes_total: float = 0.0
    bytes_unit: str = "B"
    bps: float = 0.0
    bps_unit: str = "bps"
    # False = the default, never read from rnstatus. A 0 B that was READ and
    # a 0 B that was never there must not look alike (hfm #1, Fable r1 #2).
    observed: bool = False
    # The value exactly as rnstatus printed it ("1.20 KB", "1,234 B") —
    # what every reader (this one, the witness script's awk) shows.
    text: str = ""


@dataclass
class RNSInterface:
    """Parsed data for a single RNS interface."""
    type_name: str          # e.g. "TCPInterface", "Shared Instance"
    display_name: str       # e.g. "Regional RNS/<host>:<port>"
    status: InterfaceStatus = InterfaceStatus.UNKNOWN
    mode: InterfaceMode = InterfaceMode.UNKNOWN
    rate: str = ""          # e.g. "10.00 Mbps"
    peers: Optional[int] = None
    serving: Optional[int] = None
    tx: TrafficCounters = field(default_factory=TrafficCounters)
    rx: TrafficCounters = field(default_factory=TrafficCounters)
    # rns >= 1.6 `rnstatus -F` prints a "Filtered :" block: bytes the
    # transport DROPPED (e.g. unstamped announces under enforce_stamps — a
    # drop 1.6.1 does not log). None = no Filtered block in the output (the
    # fleet's 1.3.9 has no such counter, or -F was not passed) — never 0,
    # which would claim "nothing filtered" from a counter that isn't there.
    tx_filtered: Optional[TrafficCounters] = None
    rx_filtered: Optional[TrafficCounters] = None

    @property
    def full_name(self) -> str:
        return f"{self.type_name}[{self.display_name}]"

    @property
    def is_healthy(self) -> bool:
        """True unless RX > 0 with TX == 0 (link establishment failing)."""
        return not (self.rx.bytes_total > 0 and self.tx.bytes_total == 0)

    @property
    def is_rx_only(self) -> bool:
        return self.rx.bytes_total > 0 and self.tx.bytes_total == 0

    @property
    def is_zero_traffic(self) -> bool:
        return self.rx.bytes_total == 0 and self.tx.bytes_total == 0


@dataclass
class TransportStatus:
    """Transport instance information from rnstatus footer."""
    running: bool = False
    instance_hash: str = ""
    uptime_str: str = ""


@dataclass
class RNSStatus:
    """Complete parsed rnstatus output."""
    interfaces: List[RNSInterface] = field(default_factory=list)
    transport: TransportStatus = field(default_factory=TransportStatus)
    raw_output: str = ""
    parse_error: Optional[str] = None
    # True ONLY when the rnstatus subprocess exceeded its timeout — the
    # "rnsd accepted the connect but the RPC round-trip hung" wedge shape.
    # Distinct from a fast error (binary missing, no shared instance,
    # connection refused), which leaves this False. The watchdog's
    # rns_rpc_unresponsive probe keys on this flag (see watchdog_probes).
    timed_out: bool = False
    # Wall time the rnstatus subprocess actually took, by MONOTONIC clock
    # (hfm #6: wall-clock durations are forgeable on RTC-less Pis). None
    # when no subprocess ran (binary missing) — never 0.0, which would be
    # indistinguishable from an impossibly fast run and would poison a
    # latency baseline built from these values. On a TIMEOUT this is the
    # bound we gave up at, not a measurement of rnsd.
    duration_s: Optional[float] = None

    @property
    def all_up(self) -> bool:
        return (
            len(self.interfaces) > 0
            and all(i.status == InterfaceStatus.UP for i in self.interfaces)
        )

    @property
    def any_down(self) -> bool:
        return any(i.status == InterfaceStatus.DOWN for i in self.interfaces)

    @property
    def rx_only_interfaces(self) -> List[RNSInterface]:
        return [i for i in self.interfaces if i.is_rx_only]

    @property
    def zero_traffic_interfaces(self) -> List[RNSInterface]:
        return [i for i in self.interfaces if i.is_zero_traffic]


# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# Interface header — uses [\w\s]+? to match "Shared Instance" (space)
# Greedy to the LAST ']' so an IPv6 name "[v6/[fe80::1]:4242]" stays whole.
_IFACE_RE = re.compile(r'^\s*([\w\s]+?)\[(.+)\]')
_STATUS_RE = re.compile(r'^\s*Status\s*:\s*(\S+)')
_MODE_RE = re.compile(r'^\s*Mode\s*:\s*(.+)')
_RATE_RE = re.compile(r'^\s*Rate\s*:\s*(.+)')
_PEERS_RE = re.compile(r'^\s*Peers\s*:\s*(\d+)')
_SERVING_RE = re.compile(r'^\s*Serving\s*:\s*(\d+)')
# Any "Name :" property header inside an interface block. EVERY header
# (re)sets the arrow section: only "traffic" and "filtered" route ↑/↓ lines;
# any other header (Status, Held, Announces, a footer...) closes the section,
# so a block this parser doesn't know can never land in rx (Fable r1 #3).
# ANY "Name :" line is a header — dots, digits, anything but an arrow
# ("Path Rqs.", "Ch. Load", "I2P B32"). An allowlist of name characters
# was cured by enumeration twice (Fable r3 dots, r4 digits).
_HDR_RE = re.compile(r'^\s*([A-Za-z][^:↑↓]*?)\s*:')
_ARROW_SECTIONS = {"traffic": "traffic", "filtered": "filtered"}
# Interface properties are indented >= 2; headers/footers sit at 0-1.
_TOP_LEVEL_RE = re.compile(r'^ ?\S')

# Traffic — bytes value, unit, then (optionally) bps value, unit
# Handles "↑242 B  0 bps", "↑1,234 KiB  500 Kbps" and a bare "↓254 B".
_TX_RE = re.compile(r'↑\s*([\d,.]+)\s*([A-Za-z]+)(?:\s+([\d,.]+)\s*(\w+))?')
_RX_RE = re.compile(r'↓\s*([\d,.]+)\s*([A-Za-z]+)(?:\s+([\d,.]+)\s*(\w+))?')

# Transport footer
_TRANSPORT_RE = re.compile(r'Transport Instance\s+<?(\S+?)>?\s+running')
_UPTIME_RE = re.compile(r'Uptime is\s+(.+)')

# Error patterns in rnstatus output (must not match valid interface headers).
# "Shared Instance[...]" is a valid interface — only match error messages.
_ERROR_PATTERNS = (
    "no shared instance",
    "could not connect",
    "could not get shared instance",
    "authenticationerror",
    "digest mismatch",
)

# Status mapping
_STATUS_MAP = {
    "up": InterfaceStatus.UP,
    "down": InterfaceStatus.DOWN,
}

# Mode mapping
_MODE_MAP = {
    "full": InterfaceMode.FULL,
    "gateway": InterfaceMode.GATEWAY,
    "access point": InterfaceMode.ACCESS_POINT,
    "boundary": InterfaceMode.BOUNDARY,
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_BYTE_UNIT_RE = re.compile(r'^[KMGTPE]?i?B$')


def _counters(m: "re.Match[str]") -> Optional[TrafficCounters]:
    """TrafficCounters from a _TX_RE/_RX_RE match; None if a number won't
    parse or the unit is not a byte unit ("2.0 Hz" is not traffic)."""
    if not _BYTE_UNIT_RE.match(m.group(2)):
        return None
    try:
        return TrafficCounters(
            bytes_total=float(m.group(1).replace(',', '')),
            bytes_unit=m.group(2),
            bps=float(m.group(3).replace(',', '')) if m.group(3) else 0.0,
            bps_unit=m.group(4) or "bps",
            observed=True,
            text=f"{m.group(1)} {m.group(2)}",
        )
    except ValueError:
        return None


def parse_rnstatus(output: str) -> RNSStatus:
    """Parse raw ``rnstatus`` text into an :class:`RNSStatus` object.

    Args:
        output: Raw stdout+stderr from running ``rnstatus``.

    Returns:
        Populated RNSStatus. If the output contains error patterns,
        ``parse_error`` will be set and ``interfaces`` may be empty.
    """
    result = RNSStatus(raw_output=output)

    if not output or not output.strip():
        return result

    # Detect error output
    lower = output.lower()
    for pattern in _ERROR_PATTERNS:
        if pattern in lower:
            result.parse_error = output.strip()
            # the matching LINE, not the whole dump (Fable r4 nit 5)
            hit = next((ln.strip() for ln in output.splitlines() if pattern in ln.lower()),
                       pattern)
            logger.warning("rnstatus error detected: %s", hit[:160])
            break

    current: Optional[RNSInterface] = None
    # Which block the ↑/↓ lines belong to. The ↓ of a block sits on the line
    # AFTER its header, so the section persists across lines — until the
    # next header. None = no arrow-carrying block open: arrows are ignored.
    section: Optional[str] = None

    for line in output.splitlines():
        # --- Interface header ---
        m = _IFACE_RE.match(line)
        if m:
            # Save previous interface
            if current is not None:
                result.interfaces.append(current)
            current = RNSInterface(
                type_name=m.group(1).strip(),
                display_name=m.group(2).strip(),
            )
            section = None
            continue

        # A TOP-LEVEL line that is not an interface header (indent <= 1: the
        # " Totals" / " Transport Instance" footers, a "Traceback") ends the
        # interface block — its arrows must never land on the last interface
        # (Fable r2 #4: `-t -F` prints a Filtered total after " Totals").
        if current is not None and _TOP_LEVEL_RE.match(line):
            result.interfaces.append(current)
            current = None
            section = None

        # --- Properties (only if we have a current interface) ---
        if current is not None:
            h = _HDR_RE.match(line)
            if h:
                section = _ARROW_SECTIONS.get(h.group(1).strip().lower())

            m = _STATUS_RE.match(line)
            if m:
                current.status = _STATUS_MAP.get(
                    m.group(1).lower(), InterfaceStatus.UNKNOWN
                )
                continue

            m = _MODE_RE.match(line)
            if m:
                current.mode = _MODE_MAP.get(
                    m.group(1).strip().lower(), InterfaceMode.UNKNOWN
                )
                continue

            m = _RATE_RE.match(line)
            if m:
                current.rate = m.group(1).strip()
                continue

            m = _PEERS_RE.match(line)
            if m:
                try:
                    current.peers = int(m.group(1))
                except ValueError:
                    pass
                continue

            m = _SERVING_RE.match(line)
            if m:
                try:
                    current.serving = int(m.group(1))
                except ValueError:
                    pass
                continue

            # --- Traffic / Filtered blocks (section set by the header) ---
            m = _TX_RE.search(line) if section else None
            if m:
                counters = _counters(m)
                if counters is not None:
                    if section == "filtered":
                        current.tx_filtered = counters
                    else:
                        current.tx = counters
                continue

            m = _RX_RE.search(line) if section else None
            if m:
                counters = _counters(m)
                if counters is not None:
                    if section == "filtered":
                        current.rx_filtered = counters
                    else:
                        current.rx = counters
                continue

        # --- Transport footer (outside any interface) ---
        m = _TRANSPORT_RE.search(line)
        if m:
            result.transport.running = True
            result.transport.instance_hash = m.group(1)
            continue

        m = _UPTIME_RE.search(line)
        if m:
            result.transport.uptime_str = m.group(1).strip()
            continue

    # Don't forget the last interface
    if current is not None:
        result.interfaces.append(current)

    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _find_rnstatus_binary() -> Optional[str]:
    """Locate the rnstatus binary on the system."""
    path = shutil.which('rnstatus')
    if path:
        return path
    # Pip --user installs land here
    candidate = get_real_user_home() / '.local' / 'bin' / 'rnstatus'
    if candidate.exists():
        return str(candidate)
    return None


def run_rnstatus(timeout_s: float = 15.0, extra_args: tuple = ()) -> RNSStatus:
    """Run ``rnstatus`` and return parsed output.

    Args:
        timeout_s: subprocess timeout. Default 15s preserves existing
            callers (e.g. rns_monitor_mixin). The watchdog passes a
            shorter value so a wedged rnsd can't stall its 30s tick.
        extra_args: flags appended to the command (e.g. ``("-F",)``).

    Returns:
        RNSStatus with ``parse_error`` set if rnsd is unreachable or the
        binary is missing. ``timed_out=True`` ONLY when the subprocess
        exceeded ``timeout_s`` — the wedged-rnsd-RPC shape — distinct from
        a fast error (binary missing, no shared instance), which leaves
        ``timed_out`` False.
    """
    rnstatus_path = _find_rnstatus_binary()
    if not rnstatus_path:
        logger.warning("rnstatus binary not found")
        return RNSStatus(
            # One remedy, the in-app one: never pip over an apt/fork install
            # (Fable r4 nit 3 — the leg printed two contradictory fixes).
            parse_error=("rnstatus binary not found — reinstall MeshForge to "
                         "restore RNS (Settings > Updates)")
        )

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [rnstatus_path, *extra_args],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        elapsed = time.monotonic() - started
        combined = (proc.stdout or "") + (proc.stderr or "")
        status = parse_rnstatus(combined)
        # A bare RNSStatus (no parse_error) used to come back for EMPTY output
        # and for a non-zero exit whose text matched no _ERROR_PATTERNS —
        # and the watchdog's rns_rpc_unresponsive probe noted `clean` on it.
        # A fast, silent, or failing rnstatus has observed nothing
        # (falsifiability audit phase 2, 2026-09-02).
        rc = proc.returncode if isinstance(proc.returncode, int) else 0
        if not combined.strip():
            status.parse_error = f"rnstatus produced no output (exit {rc})"
        elif rc != 0 and not status.parse_error:
            # The cause, not stdout line 1: a traceback's last stderr line is
            # its exception ("KeyError: 'frxb'"), argparse's is its "error:".
            err = [ln.strip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
            first = (err[-1] if err else combined.strip().splitlines()[0])[:120]
            status.parse_error = f"rnstatus exited {rc}: {first}"
        status.duration_s = elapsed
        return status
    except subprocess.TimeoutExpired:
        logger.warning("rnstatus timed out — rnsd may be unresponsive")
        return RNSStatus(
            parse_error="rnstatus timed out (rnsd unresponsive)",
            timed_out=True,
            duration_s=time.monotonic() - started,
        )
    except FileNotFoundError:
        logger.warning("rnstatus not found at %s", rnstatus_path)
        return RNSStatus(parse_error=f"rnstatus not found at {rnstatus_path}")
    except OSError as e:
        logger.warning("Failed to run rnstatus: %s", e)
        return RNSStatus(parse_error=f"Failed to run rnstatus: {e}",
                         duration_s=time.monotonic() - started)


# ---------------------------------------------------------------------------
# Filtered rx — the only witness of a silent drop (WITNESS-TUI, 2026-11-21)
# ---------------------------------------------------------------------------


class FilterState(Enum):
    """What `rnstatus -F` could tell us about filtered (dropped) traffic."""
    COUNTED = "counted"        # this RNS counts filtered bytes; the number is real
    NO_FILTER = "no_filter"    # this rnstatus CLI rejects -F (a fact about the
    #                            CLI; the daemon is not proven filter-free)
    UNKNOWN = "unknown"        # nothing observed (timeout, error, no output)


@dataclass
class InterfaceTraffic:
    """One reading of per-interface accepted (and, where counted, filtered) traffic."""
    status: RNSStatus
    filter_state: FilterState
    filter_reason: str = ""

    @property
    def accepted_gap(self) -> Optional[str]:
        """Why the ACCEPTED view is not a complete reading — None = complete.

        The ONE judgement, for whichever view the reader returned (-F or the
        plain re-read): Fable r4 found the NO_FILTER path's plain re-read
        crash after listing and the leg say "All checks passed", because
        completeness was judged in the reader for -F only.
        """
        return accepted_gap(self.status)


# The CLI's answer when this rnstatus has no -F (rns < 1.6, the fleet's
# 1.3.9: argparse "unrecognized arguments: -F"; optparse "no such option").
# Only trusted when the run listed NO interfaces — the same words inside a
# real listing (an interface name) are not a rejection (Fable r1 #7).
_NO_F_RE = re.compile(
    r"(?:unrecognized arguments:|no such option:)[^\n]*(?<![\w-])['\"]?-F(?![\w-])")


def _listed(status: RNSStatus) -> bool:
    """True when rnstatus really listed interfaces: some counter was READ.

    ``interfaces`` alone is not proof — _IFACE_RE also matches argparse's
    usage continuation lines ("                [-t] [-s SORT]") as an
    interface with an all-whitespace type, so the 1.3.9 -F rejection
    "lists" bogus interfaces (found 2026-11-21, Fable r1 round).
    """
    return any(i.rx.observed or i.tx.observed for i in status.interfaces)


def _complete(status: RNSStatus) -> bool:
    """True when the run ended cleanly: exit 0 and no error text.

    A listing followed by a crash is PARTIAL (Fable r2 #2: stock 1.6.1
    rnstatus indexes ifstat["frxb"] unguarded — against a pre-1.6 rnsd it
    prints the first block then dies with KeyError, rc 1). An uncaught
    exception always exits non-zero, so run_rnstatus's rc != 0 parse_error
    is the whole test (a separate traceback grep was a never-failing guard).
    """
    return status.parse_error is None


def error_line(status: RNSStatus) -> Optional[str]:
    """The rnstatus output line carrying a known error pattern, or None.

    parse_rnstatus sets parse_error to the WHOLE output on a pattern hit, so
    its first line is usually listing text, not the cause (Fable r3 nit).
    """
    for line in (status.raw_output or "").splitlines():
        low = line.lower()
        if any(p in low for p in _ERROR_PATTERNS):
            return line.strip()[:160]
    return None


def error_summary(status: RNSStatus) -> str:
    """One line naming why a reading failed ("" when it did not)."""
    if not status.parse_error:
        return ""
    return error_line(status) or status.parse_error.strip().splitlines()[0][:160]


def accepted_gap(status: RNSStatus) -> Optional[str]:
    """Why one rnstatus run is not a complete accepted reading; None = complete.

    timed out > an error-pattern line (the listing is not trusted) > a
    failed run: "partial listing — <cause>" when it listed first, else the
    cause. Nothing listed with no error is NOT a gap here (the TUI says why).
    """
    if status.timed_out:
        return "rnstatus timed out"
    hit = error_line(status)
    if hit:
        return f"rnstatus error: {hit}"
    if status.parse_error:
        why = error_summary(status)
        return f"partial listing — {why}" if _listed(status) else why
    return None


NO_F_SHORT = "this rnstatus has no -F"
NO_F_REASON = f"{NO_F_SHORT} (no filtered counter to read)"


def read_interface_traffic(timeout_s: float = 15.0) -> InterfaceTraffic:
    """Read accepted + filtered traffic per interface: `rnstatus -F` first.

    States (one row each, no other outcome):
      * -F complete + listed  -> COUNTED (a leg without a Filtered block is
                                 still blind — see blind_spots in the TUI)
      * -F rejected, nothing  -> NO_FILTER; accepted traffic re-read plain (a
        listed                   failed re-read says so in ``filter_reason``)
      * -F timed out          -> UNKNOWN, NOT retried (a wedged rnsd is not
                                 hit twice)
      * anything else: missing binary, an error, rc != 0, a traceback, a
        PARTIAL listing, no interfaces -> UNKNOWN with the reason; accepted
        traffic re-read plain so the accepted view is never lost to -F

    NO_FILTER is a fact about the CLI (argparse refused -F), not proof the
    daemon has no filter — the wording says exactly that (Fable r2 #7).
    """
    status = run_rnstatus(timeout_s=timeout_s, extra_args=("-F",))
    if status.timed_out:
        return InterfaceTraffic(status, FilterState.UNKNOWN, "rnstatus -F timed out")
    if not _listed(status) and _NO_F_RE.search(status.raw_output or ""):
        plain = run_rnstatus(timeout_s=timeout_s)
        reason = NO_F_REASON
        if plain.parse_error or not _listed(plain):
            reason += ("; accepted traffic UNKNOWN: "
                       + (error_summary(plain) or "rnstatus reported no interfaces"))
        return InterfaceTraffic(plain, FilterState.NO_FILTER, reason)
    if _listed(status) and _complete(status):
        return InterfaceTraffic(status, FilterState.COUNTED)
    if _listed(status):
        reason = "partial listing — " + (error_summary(status) or "incomplete run")
    else:
        reason = error_summary(status) or "reported no interfaces"
    plain = run_rnstatus(timeout_s=timeout_s)
    return InterfaceTraffic(plain, FilterState.UNKNOWN,
                            f"rnstatus -F: {reason.splitlines()[0][:160]}")


def format_bytes(c: TrafficCounters) -> str:
    """'254 B' / '1.20 KB' / '1,234 B' — exactly as rnstatus printed it."""
    if c.text:
        return c.text
    return f"{c.bytes_total:g} {c.bytes_unit}"


def filtered_rx_verdict(iface: RNSInterface, filter_state: FilterState):
    """(state, one-line text) for one interface's accepted vs filtered rx.

    Never renders a filtered 0 it did not read: a leg with no Filtered block
    under a COUNTED reading is UNKNOWN for that leg.
    """
    accepted = ("rx accepted " + format_bytes(iface.rx)
                if iface.rx.observed else "rx accepted UNKNOWN")
    if filter_state is FilterState.NO_FILTER:
        return (FilterState.NO_FILTER,
                f"{accepted} · filtered n/a ({NO_F_SHORT})")
    if filter_state is FilterState.UNKNOWN:
        return (FilterState.UNKNOWN, f"{accepted} · filtered UNKNOWN")
    if iface.rx_filtered is None:
        return (FilterState.UNKNOWN,
                f"{accepted} · filtered UNKNOWN (no Filtered line for this interface)")
    text = f"{accepted} · filtered {format_bytes(iface.rx_filtered)}"
    if iface.rx_filtered.bytes_total > 0:
        text += (" ⚠ dropped by this RNS, not logged "
                 "(e.g. unstamped announces under enforce_stamps)")
    return (FilterState.COUNTED, text)
