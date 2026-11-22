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
    # The header EXACTLY as rnstatus printed it (stock `str(interface)`):
    # not every stock name is "Type[name]" — WeaveInterface prints
    # "WDCL over <port>" (r6). "" = constructed by hand (tests).
    name: str = ""

    @property
    def full_name(self) -> str:
        if self.name:
            return self.name
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
    # Every output line the parser could NOT account for, verbatim, in order
    # (WITNESS-TUI r6). Non-empty = this reading is incomplete: something was
    # printed that no rule read, so nothing may be called healthy from it
    # (hfm #3 at the parser boundary). Judged by accepted_gap().
    unread_lines: List[str] = field(default_factory=list)

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

# ---- The line grammar (WITNESS-TUI r6: EVERY line is accounted for) ------
# Derived from the stock print code of rns 1.3.9 and 1.6.1
# (RNS/Utilities/rnstatus.py program_setup), pinned by the corpora in
# tests/fixtures/rnstatus_stock_*.json that code printed. A line matching no
# rule is UNREAD (RNSStatus.unread_lines) and makes the reading incomplete —
# never skipped. Five review rounds found five holes in an ignore-by-default
# parser; an allowlist that refuses is the cure, a longer allowlist is not.
#
# Stock prints an interface as " " + str(interface) (indent 1), then its
# fields at indent 4. str(interface) is free text: "Type[section name]",
# "<parent section>[<sub name>]" (RNodeSubInterface), "WDCL over <port>".
# So a header is not recognised by its characters (r5: a hyphen broke the
# old regex) but by its SHAPE: a top-level line whose indented block holds
# a "Status :" field.
_FIELD_RE = re.compile(r'^(\s+)([A-Za-z][^:↑↓]*?)\s*:(.*)$')
# Interface-block field names stock prints (lower-cased).
_IFACE_FIELDS = frozenset({
    "source", "network", "status", "serving", "clients", "peers", "blocked",
    "mode", "tx drops", "rate", "tunnel id", "rem.tr.id", "rem.vers.",
    "rem.csts.", "noise fl.", "intrfrnc.", "cpu load", "cpu temp",
    "mem usage", "battery", "airtime", "ch. load", "switch id", "endpoint",
    "via", "i2p", "access", "i2p b32", "queued", "held", "violatns.",
    "flt. hits", "path rqs.", "announces", "traffic", "filtered",
})
# Fields stock continues on following indented lines (no "Name :"):
# Traffic/Filtered = exactly ONE ↓ line; the rest are free text.
_ONE_ARROW_CONT = frozenset({"traffic", "filtered"})
# Fields the leg READS: each must parse, and appear once per block.
_READ_FIELDS = frozenset({"status", "traffic", "filtered"})
_FREE_CONT = frozenset({"path rqs.", "announces", "clients", "blocked"})
# Top-level footer fields (`-t`, `-t -F`, `-l`, `-P`, `-A`, `-q`) — each may
# continue on indented lines.
# name -> how many continuation lines stock prints under it.
_FOOTER_FIELDS = {
    "totals": 1, "filtered": 1, "links": 1, "path rqs.": 1, "announces": 1,
    "qu. pressure": 4,
}
_FOOTER_LINE_RE = re.compile(
    r'^ ?(?:Transport Instance \S+ running|Network Identity\s+\S+'
    r'|Probe responder at \S+ active|Uptime is .+'
    r'|,? ?\d+ entr(?:y|ies) in link table.*)$')
# RNS's own log line (stderr is folded into the output): recognised as not
# part of the listing. Error words in it are still caught by _ERROR_PATTERNS.
_LOG_LINE_RE = re.compile(r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[')
_STATUS_VALUE_RE = re.compile(r'^\s*(Up|Down)(?:,.*)?\s*$')
_INT_RE = re.compile(r'^\s*(\d+)')


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _is_top_level(line: str) -> bool:
    return bool(line.strip()) and not line.startswith('\t') and _indent(line) <= 1


def _opens_interface(lines: List[str], idx: int) -> bool:
    """True when the top-level line at ``idx`` heads an interface block:
    its following lines (until a blank or another top-level line) include an
    indented ``Status :`` field — every stock interface prints one."""
    for nxt in lines[idx + 1:]:
        if not nxt.strip() or _is_top_level(nxt):
            return False
        f = _FIELD_RE.match(nxt)
        if f and f.group(2).strip().lower() == "status":
            return True
    return False


def _split_name(text: str):
    """("Type", "inner") for "Type[inner]" (the LAST ']' — IPv6 stays whole);
    (text, "") for a name without brackets ("WDCL over /dev/ttyUSB0")."""
    if text.endswith("]") and "[" in text:
        i = text.index("[")
        return text[:i].strip(), text[i + 1:-1].strip()
    return text, ""

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
        ``parse_error`` will be set and ``interfaces`` may be empty. Every
        line no grammar rule accounts for is in ``unread_lines`` — the
        reading is then incomplete (:func:`accepted_gap`), never "healthy
        from what I recognised".
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
    # The multi-line field the next continuation line belongs to (None = a
    # continuation here is unread), and for Traffic/Filtered how many ↓
    # lines it may still take (exactly one).
    cont: Optional[str] = None
    cont_left = 0
    lines = output.splitlines()

    def unread(ln: str) -> None:
        result.unread_lines.append(ln)

    def close(iface: Optional[RNSInterface]) -> None:
        # A block missing a counter line is not an unread LINE: its unread
        # counter (observed False) is named by the leg's ONE half-read
        # message (accepted_blind_reason) — one producer per fact.
        if iface is not None:
            result.interfaces.append(iface)

    # Read fields already seen in the open block: stock prints each ONCE, so
    # a second Status/Traffic/Filtered is not rnstatus as we know it.
    seen: set = set()

    for idx, line in enumerate(lines):
        if not line.strip():
            continue                                   # blank: accounted
        if '\t' in line[:len(line) - len(line.lstrip())]:
            unread(line)                               # stock never indents with tabs
            continue

        if _is_top_level(line):
            # Any top-level line closes the open interface block (Fable r2
            # #4: a footer's arrows must never land on the last interface).
            close(current)
            current = None
            cont = None
            text = line.strip()
            if _opens_interface(lines, idx):
                type_name, display = _split_name(text)
                current = RNSInterface(type_name=type_name, display_name=display, name=text)
                seen = set()
                continue
            f = _FIELD_RE.match(" " + text)
            if f and f.group(2).strip().lower() in _FOOTER_FIELDS:
                cont = "footer"                        # its continuation lines
                cont_left = _FOOTER_FIELDS[f.group(2).strip().lower()]
                continue
            m = _TRANSPORT_RE.search(line)
            if m and _FOOTER_LINE_RE.match(line):
                result.transport.running = True
                result.transport.instance_hash = m.group(1)
                continue
            m = _UPTIME_RE.search(line)
            if m and _FOOTER_LINE_RE.match(line):
                result.transport.uptime_str = m.group(1).strip()
                continue
            if _FOOTER_LINE_RE.match(line) or _LOG_LINE_RE.match(text):
                continue
            unread(line)
            continue

        # --- Indented line ---
        f = _FIELD_RE.match(line)
        fname = f.group(2).strip().lower() if f else None
        if current is not None and fname in _IFACE_FIELDS:
            value = f.group(3)
            cont, cont_left = None, 0
            if fname in _READ_FIELDS:
                if fname in seen:
                    unread(line)        # and its continuation: cont is None
                    continue
                seen.add(fname)
            if fname in _ONE_ARROW_CONT:
                cont, cont_left = fname, 1
            elif fname in _FREE_CONT:
                cont, cont_left = fname, -1
            if not _read_field(current, fname, value):
                unread(line)
            continue

        if cont is not None and not f:
            # A continuation of the open multi-line field.
            if cont_left == -1:
                continue                               # free text: accounted
            if cont == "footer":
                if cont_left > 0:
                    cont_left -= 1
                    continue
            elif cont_left == 1:
                cont_left = 0
                m = _RX_RE.search(line)
                counters = _counters(m) if m and line.lstrip().startswith("↓") else None
                if counters is not None:
                    if cont == "filtered":
                        current.rx_filtered = counters
                    else:
                        current.rx = counters
                    continue
            unread(line)
            continue

        unread(line)

    # Don't forget the last interface
    close(current)

    return result


def _read_field(iface: RNSInterface, fname: str, value: str) -> bool:
    """Apply one interface field; False = a field the leg READS whose value
    did not parse (an unread line). Informational fields are accounted for
    by name alone."""
    if fname == "status":
        m = _STATUS_VALUE_RE.match(value)
        if not m:
            iface.status = InterfaceStatus.UNKNOWN
            return False
        iface.status = _STATUS_MAP[m.group(1).lower()]
        return True
    if fname in ("traffic", "filtered"):
        m = _TX_RE.search(value)
        counters = _counters(m) if m and value.lstrip().startswith("↑") else None
        if counters is None:
            return False
        if fname == "filtered":
            iface.tx_filtered = counters
        else:
            iface.tx = counters
        return True
    if fname == "mode":
        iface.mode = _MODE_MAP.get(value.strip().lower(), InterfaceMode.UNKNOWN)
    elif fname == "rate":
        iface.rate = value.strip()
    elif fname in ("peers", "serving"):
        m = _INT_RE.match(value)
        if m:
            setattr(iface, fname, int(m.group(1)))
    return True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


# The ONE missing-binary text (hfm #5: tests and the TUI import it; three
# fixtures still carried the retired "pip install rns" text after r4).
# One remedy, the in-app one: never pip over an apt/fork install (Fable r4
# nit 3 — the leg printed two contradictory fixes).
RNSTATUS_MISSING = ("rnstatus binary not found — reinstall MeshForge to "
                    "restore RNS (Settings > Updates)")


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
        return RNSStatus(parse_error=RNSTATUS_MISSING)

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

    ``interfaces`` alone was not proof under the old header regex, which
    read argparse usage continuation lines as interfaces (Fable r1 round);
    an interface now needs a Status block (r6), but a header with no
    readable counter has still observed nothing.
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
    if status.unread_lines:
        # r6: "All checks passed" requires every line accounted for.
        return (f"{len(status.unread_lines)} line(s) this parser could not read: "
                f"'{status.unread_lines[0][:80]}'")
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
