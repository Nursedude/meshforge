"""Shared wire format + helpers for lab echo / tracer.

The PING/ACK protocol is intentionally text-only (LXMF content body, not
``fields``) so an operator can read the messages with any LXMF client
while debugging.

Wire format
-----------
PING : ``PING seq=<int> from=<short_name>``
ACK  : ``ACK seq=<int> orig=<short_name> recv_at=<iso8601_utc>``

The ``orig`` field on the ACK echoes the original sender's ``from`` so
the tracer can match an ACK to its PING under out-of-order delivery
across multiple peers without keeping per-peer seq spaces.

NOTE (RNS T2-isolate arc, sub-arc B — 2026-05-29): the guarded RNS-init
primitives (``init_reticulum_with_watchdog``, ``check_rns_listener_owner``,
``bounded_block``, ...) MOVED to ``utils.rns_init`` — the project-wide
chokepoint home — so ``utils/`` no longer imports up into ``lab/``. They are
re-exported from here unchanged for the lab echo/tracer daemons and existing
callers. New code should prefer ``utils.rns_init.open_reticulum``.
"""

from __future__ import annotations

import logging
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Re-export the guarded RNS-init primitives from their new home so existing
# imports (`from lab._lab_common import init_reticulum_with_watchdog`, etc.)
# and tests keep working after the move to utils.rns_init.
from utils.rns_init import (  # noqa: F401  (re-export for back-compat)
    RNS_INIT_TIMEOUT_S,
    _RNS_LISTENER_ALLOWED_PATTERNS,
    _parse_ss_listener_line,
    _read_instance_name_from_config,
    bounded_block,
    check_rns_listener_owner,
    init_reticulum_with_watchdog,
)

logger = logging.getLogger(__name__)

# Hash-obscurity v1 (per L1 plan question #1): peers learn the echo
# destination hash via lab_peers file. Outsiders don't have it. HMAC
# can be added in L2 if fleet-internal boxes spam each other.

_PING_RE = re.compile(
    r"^PING\s+seq=(\d+)\s+from=([A-Za-z0-9_\-]+)\s*$"
)
_ACK_RE = re.compile(
    r"^ACK\s+seq=(\d+)\s+orig=([A-Za-z0-9_\-]+)\s+recv_at=(\S+)\s*$"
)


@dataclass(frozen=True)
class PingMessage:
    """Parsed PING. Used by the echo daemon to validate inbound."""
    seq: int
    sender: str  # short name of the originator


@dataclass(frozen=True)
class AckMessage:
    """Parsed ACK. Used by the tracer to match outbound PING."""
    seq: int
    orig: str  # short name of the original PING sender (echoed back)
    recv_at_iso: str


def make_ping_body(seq: int, sender: str) -> str:
    """Build a PING body line for the given seq + sender short name."""
    return f"PING seq={seq} from={sender}"


def make_ack_body(seq: int, orig: str, recv_at_iso: Optional[str] = None) -> str:
    """Build an ACK body line. Defaults recv_at to 'now' in UTC ISO8601."""
    if recv_at_iso is None:
        recv_at_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"ACK seq={seq} orig={orig} recv_at={recv_at_iso}"


def parse_ping(body: str) -> Optional[PingMessage]:
    """Parse a PING body. Returns None if it doesn't match the format."""
    if body is None:
        return None
    m = _PING_RE.match(body.strip())
    if not m:
        return None
    return PingMessage(seq=int(m.group(1)), sender=m.group(2))


def parse_ack(body: str) -> Optional[AckMessage]:
    """Parse an ACK body. Returns None if it doesn't match the format."""
    if body is None:
        return None
    m = _ACK_RE.match(body.strip())
    if not m:
        return None
    return AckMessage(
        seq=int(m.group(1)),
        orig=m.group(2),
        recv_at_iso=m.group(3),
    )


def short_name() -> str:
    """Box short-name used in PING/ACK headers.

    First section of the hostname (matches the operator's ssh-config
    aliases). Falls back to "unknown" if hostname lookup blows up.
    """
    try:
        return socket.gethostname().split(".")[0] or "unknown"
    except OSError:
        return "unknown"


# ---------------------------------------------------------------- identity


def _identity_path_for(name: str) -> Path:
    """``~/.config/meshforge/<name>_identity`` (sudo-safe via paths util)."""
    from utils.paths import get_real_user_home

    return get_real_user_home() / ".config" / "meshforge" / f"{name}_identity"


def load_or_create_identity(name: str):
    """Load an RNS identity from disk; create + persist if absent.

    Mirrors the pattern in ``gateway/_rns_bridge_connection._setup_lxmf``
    and ``scripts/validate_rns_to_mesh.py``. Importing RNS lazily so the
    test suite can mock the path without dragging the whole stack in.
    """
    import RNS  # lazy — keeps module import cheap for tests

    path = _identity_path_for(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return RNS.Identity.from_file(str(path)), path
    identity = RNS.Identity()
    identity.to_file(str(path))
    return identity, path
