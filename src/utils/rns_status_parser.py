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


@dataclass
class RNSInterface:
    """Parsed data for a single RNS interface."""
    type_name: str          # e.g. "TCPInterface", "Shared Instance"
    display_name: str       # e.g. "HawaiiNet RNS/192.168.86.38:4242"
    status: InterfaceStatus = InterfaceStatus.UNKNOWN
    mode: InterfaceMode = InterfaceMode.UNKNOWN
    rate: str = ""          # e.g. "10.00 Mbps"
    peers: Optional[int] = None
    serving: Optional[int] = None
    tx: TrafficCounters = field(default_factory=TrafficCounters)
    rx: TrafficCounters = field(default_factory=TrafficCounters)

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
_IFACE_RE = re.compile(r'^\s*([\w\s]+?)\[(.+?)\]')
_STATUS_RE = re.compile(r'^\s*Status\s*:\s*(\S+)')
_MODE_RE = re.compile(r'^\s*Mode\s*:\s*(.+)')
_RATE_RE = re.compile(r'^\s*Rate\s*:\s*(.+)')
_PEERS_RE = re.compile(r'^\s*Peers\s*:\s*(\d+)')
_SERVING_RE = re.compile(r'^\s*Serving\s*:\s*(\d+)')

# Traffic — bytes value, unit, then bps value, unit
# Handles both "↑242 B  0 bps" and "↑1,234 KiB  500 Kbps"
_TX_RE = re.compile(r'↑\s*([\d,.]+)\s*(\w+)\s+([\d,.]+)\s*(\w+)')
_RX_RE = re.compile(r'↓\s*([\d,.]+)\s*(\w+)\s+([\d,.]+)\s*(\w+)')

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
            break

    current: Optional[RNSInterface] = None

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
            continue

        # --- Properties (only if we have a current interface) ---
        if current is not None:
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

            # --- Traffic lines ---
            m = _TX_RE.search(line)
            if m:
                try:
                    current.tx = TrafficCounters(
                        bytes_total=float(m.group(1).replace(',', '')),
                        bytes_unit=m.group(2),
                        bps=float(m.group(3).replace(',', '')),
                        bps_unit=m.group(4),
                    )
                except ValueError:
                    pass
                continue

            m = _RX_RE.search(line)
            if m:
                try:
                    current.rx = TrafficCounters(
                        bytes_total=float(m.group(1).replace(',', '')),
                        bytes_unit=m.group(2),
                        bps=float(m.group(3).replace(',', '')),
                        bps_unit=m.group(4),
                    )
                except ValueError:
                    pass
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


def run_rnstatus() -> RNSStatus:
    """Run ``rnstatus`` and return parsed output.

    Returns:
        RNSStatus with ``parse_error`` set if rnsd is unreachable
        or the binary is missing.
    """
    rnstatus_path = _find_rnstatus_binary()
    if not rnstatus_path:
        return RNSStatus(
            parse_error="rnstatus binary not found. Install RNS: pip install rns"
        )

    try:
        proc = subprocess.run(
            [rnstatus_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        return parse_rnstatus(combined)
    except subprocess.TimeoutExpired:
        return RNSStatus(parse_error="rnstatus timed out (rnsd unresponsive)")
    except FileNotFoundError:
        return RNSStatus(parse_error=f"rnstatus not found at {rnstatus_path}")
    except OSError as e:
        return RNSStatus(parse_error=f"Failed to run rnstatus: {e}")
