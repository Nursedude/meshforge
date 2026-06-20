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

# ------------------------------------------------------------ big (resource) ping
#
# A second wire format whose REPLY is deliberately large enough to force LXMF
# to deliver it as an RNS Resource over a link instead of a single packet
# (LXMF's LINK_PACKET_MAX_CONTENT is ~319 bytes — above that the message is
# sequenced and transferred as a Resource, which the RECEIVER assembles to
# <configdir>/storage/resources/). That receive-side assembly is the exact
# step the 2026-06-20 wx-total-loss EROFS broke (#60 sandbox class): the
# gateway, a shared-instance RNS client, could not write the assembled
# Resource, so multi-chunk replies silently dropped while SINGLE-packet
# replies (the leaderboard) still worked and every liveness probe read green.
# The single-packet PING/ACK above is structurally blind to that class; this
# pair exercises it on a timer (gateway-reliability arc A1).
#
# PINGBIG : ``PINGBIG seq=<int> from=<short> want=<bytes>`` — a small,
#           single-packet request asking the echo to reply with a body of at
#           least <want> bytes.
# ACKBIG  : ``ACKBIG seq=<int> orig=<short> len=<bytes> recv_at=<iso>`` on the
#           FIRST line (the unique marker the canary matches, so a leading
#           ``[RNS:xxxx]`` bridge tag doesn't defeat it), then a newline and
#           deterministic padding out to <len> bytes total. RNS Resources are
#           all-or-nothing — a partial assembly delivers NOTHING — so observing
#           the first-line marker bridged back PROVES the whole Resource
#           assembled at the gateway.
_BIGPING_RE = re.compile(
    r"^PINGBIG\s+seq=(\d+)\s+from=([A-Za-z0-9_\-]+)\s+want=(\d+)\s*$"
)
# First-line only — the body continues with padding after a newline, so this
# is matched against ``body.split("\n", 1)[0]`` rather than the whole body.
_BIGACK_RE = re.compile(
    r"^ACKBIG\s+seq=(\d+)\s+orig=([A-Za-z0-9_\-]+)\s+len=(\d+)\s+recv_at=(\S+)"
)

# Upper bound on a reply the echo will synthesise, regardless of the requested
# ``want``. The echo destination is shared only via lab_peers (hash-obscurity
# v1), but an attacker who learns it could otherwise request a multi-megabyte
# reply as an amplification lever — clamp it. 4 KiB is ~13x the single-packet
# limit, plenty to force a multi-segment Resource, and a trivial RF cost when
# the gateway bridges it onward.
MAX_BIGACK_REPLY_BYTES = 4096

# Deterministic, human-readable pad byte (so an operator reading the message in
# any LXMF client sees an obvious filler, not garbage).
_BIGACK_PAD_CHAR = "."


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


@dataclass(frozen=True)
class BigPingMessage:
    """Parsed PINGBIG. Used by the echo daemon to size its Resource reply."""
    seq: int
    sender: str       # short name of the originator
    want_bytes: int   # requested minimum reply size (echo clamps to MAX)


@dataclass(frozen=True)
class BigAckMessage:
    """Parsed ACKBIG header line. Used by the canary / a tracer to match."""
    seq: int
    orig: str         # short name of the original PINGBIG sender (echoed back)
    length: int       # declared total body length in bytes
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


def make_bigping_body(seq: int, sender: str, want_bytes: int) -> str:
    """Build a PINGBIG body asking the echo for a >= want_bytes reply."""
    return f"PINGBIG seq={seq} from={sender} want={int(want_bytes)}"


def parse_bigping(body: str) -> Optional[BigPingMessage]:
    """Parse a PINGBIG body. Returns None if it doesn't match the format."""
    if body is None:
        return None
    m = _BIGPING_RE.match(body.strip())
    if not m:
        return None
    return BigPingMessage(
        seq=int(m.group(1)), sender=m.group(2), want_bytes=int(m.group(3)))


def make_bigack_body(
    seq: int, orig: str, want_bytes: int, recv_at_iso: Optional[str] = None,
) -> str:
    """Build an ACKBIG reply body of (close to) want_bytes total.

    The first line is the parseable header carrying the unique
    ``ACKBIG seq=<seq> orig=<orig>`` marker; the rest is deterministic padding
    out to ``want_bytes`` total. ``want_bytes`` is clamped to
    ``MAX_BIGACK_REPLY_BYTES`` (amplification guard). If the clamped target is
    smaller than the header itself (a misuse — the canary always asks for a
    resource-sized reply), the bare header is returned: it still carries the
    marker, it just won't force a Resource. ``len=`` always reports the ACTUAL
    final body length, never a value the body doesn't honour.
    """
    if recv_at_iso is None:
        recv_at_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    target = max(0, min(int(want_bytes), MAX_BIGACK_REPLY_BYTES))

    def _header(total: int) -> str:
        return f"ACKBIG seq={seq} orig={orig} len={total} recv_at={recv_at_iso}"

    bare = _header(target)
    if target <= len(bare) + 1:
        # Too small to pad — the body IS the bare header. Stamp len= to its own
        # actual length via a tiny fixpoint (the digit count converges in ≤2
        # steps), so len= never lies (honest_failure_modes).
        total = len(bare)
        for _ in range(4):
            h = _header(total)
            if len(h) == total:
                return h
            total = len(h)
        return _header(total)
    # bare already carries len=target; padding to exactly `target` bytes keeps
    # len= honest (final length == target).
    pad = target - len(bare) - 1  # -1 for the separating newline
    return bare + "\n" + (_BIGACK_PAD_CHAR * pad)


def parse_bigack(body: str) -> Optional[BigAckMessage]:
    """Parse an ACKBIG body (first line only). None if it doesn't match.

    Anchored to the start of the first line — a leading ``[RNS:xxxx]`` bridge
    tag is NOT absorbed here (the canary's SQL ``LIKE`` predicate handles the
    tag); this parses the echo's raw reply, mirroring ``parse_ack``.
    """
    if body is None:
        return None
    first = body.lstrip().split("\n", 1)[0]
    m = _BIGACK_RE.match(first)
    if not m:
        return None
    return BigAckMessage(
        seq=int(m.group(1)),
        orig=m.group(2),
        length=int(m.group(3)),
        recv_at_iso=m.group(4),
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
