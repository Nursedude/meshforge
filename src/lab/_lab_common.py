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

import contextlib
import logging
import os
import re
import shutil
import socket
import tempfile
import time
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


# ---------------------------------------------------------------- scratch dir


def _pid_maybe_alive(pid: int) -> bool:
    """False ONLY when the kernel says no such process; anything else = keep.

    ⚠️ Deliberately NOT ``virtual_fleet._pid_alive``: that one reads EPERM as
    dead (it manages its own children); here a deletion rides on the answer,
    so EPERM (exists, another user's), overflow and any doubt all read alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, OverflowError, ValueError):
        return True
    return True


# New-style names: <prefix>pid<N>-<mkdtemp suffix>. mkdtemp's alphabet is
# [a-z0-9_], so a '-' can never appear in a pre-fix name — the two forms
# cannot be confused (review r1 F3: "4821_xq" read as PID 4821).
_PID_DIR_RE = re.compile(r"pid([0-9]{1,10})-[a-z0-9_]+")
# Pre-fix names: TemporaryDirectory(prefix=...) = prefix + 8 of [a-z0-9_],
# never starting "pid" (that is the new form's lead — review r2 F2).
_LEGACY_DIR_RE = re.compile(r"(?!pid)[a-z0-9_]{8}")
LEGACY_DIR_MIN_AGE_S = 86400


def _module_running_elsewhere(module: str) -> bool:
    """Is any OTHER process running ``python -m <module>``? Unsure = yes.

    The liveness test for pre-fix dirs, which carry no PID: a pre-fix daemon's
    dir keeps its START mtime for days, so age alone deleted a live echo's
    dir in review r2 (F1: virtual_fleet / a manual run beside the unit).
    Every launcher of these modules uses ``-m`` (systemd units, virtual_fleet).
    """
    me = os.getpid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return True
    for entry in entries:
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                argv = fh.read().split(b"\0")
        except OSError:
            continue                  # exited mid-scan, or not ours to read
        if module.encode() in argv:
            return True
    return False


def _rmtree_witnessed(path, what: str) -> bool:
    """rmtree that never swallows silently (honest_failure_modes #9)."""
    try:
        shutil.rmtree(path)
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning("lab tmpdir: could not remove %s %s: %s", what, path, exc)
        return False


def sweep_dead_owner_dirs(prefix: str, *, owner_module: Optional[str] = None,
                          now: Optional[float] = None) -> int:
    """Remove this user's ``prefix`` dirs that no live process can own.

    The cure for every exit that skips cleanup — ``os._exit`` in the RNS-init
    watchdog (#68), RNS/LXMF signal handlers, RNS's own exit path, a SIGKILL,
    a late LXMF writer (review r2 F3/F4/F5). Two forms:

    * ``<prefix>pid<N>-*`` — removed when PID N is gone. Liveness is the PID
      in the name, never an age: a daemon's own dir can be days old.
    * pre-fix ``<prefix><8 chars>`` (no PID) — only when ``owner_module`` is
      given, the dir is a day old, AND no other process runs that module
      (the PID-less dir's only liveness evidence). This clears the fleet's
      backlog on deploy with no hand cleanup.

    Anything else, symlinks, other users' dirs and a live/unknown owner are
    KEPT; PID reuse can only keep, never delete.
    """
    base = Path(tempfile.gettempdir())
    uid = os.getuid()
    now = time.time() if now is None else now
    legacy_ok: Optional[bool] = None     # evaluated once, only if needed
    swept = 0
    for old in base.glob(f"{prefix}*"):
        rest = old.name[len(prefix):]
        m = _PID_DIR_RE.fullmatch(rest)
        legacy = (m is None and owner_module is not None
                  and _LEGACY_DIR_RE.fullmatch(rest) is not None)
        if m is None and not legacy:
            continue
        try:
            st = old.lstat()
        except OSError:
            continue
        if old.is_symlink() or not old.is_dir() or st.st_uid != uid:
            continue
        if m is not None and _pid_maybe_alive(int(m.group(1))):
            continue
        if legacy:
            if now - st.st_mtime < LEGACY_DIR_MIN_AGE_S:
                continue
            if legacy_ok is None:
                legacy_ok = not _module_running_elsewhere(owner_module)
            if not legacy_ok:
                continue
        if _rmtree_witnessed(old, "stale dir"):
            swept += 1
    if swept:
        logger.info("lab tmpdir: swept %d stale %s* dir(s)", swept, prefix)
    return swept


@contextlib.contextmanager
def exit_scoped_tmpdir(prefix: str, *, owner_module: Optional[str] = None):
    """A temp dir for RNS/LXMF state that leaves nothing behind.

    ``owner_module`` (e.g. ``"lab.lxmf_echo"``) is the ``-m`` name the caller
    runs as; given, the start-up sweep may also clear pre-fix dirs.

    Root-caused 2026-11-17 (tests/test_lab_tmpdir_survives_exit.py) by a
    live drill of a real RNS+LXMF client: atexit ran ``LXMRouter.exit_handler``
    (persists node_stats / local_deliveries into its storagepath, re-creating
    a ``TemporaryDirectory`` already removed), then ``Reticulum.exit_handler``,
    which in that run reached ``os._exit(0)`` (seen once, not on every exit) —
    so a cleanup registered with atexit is not guaranteed to run at all.
    1,553 tracer dirs (~50 MB of tmpfs) in 11 days on the manager.

    So the contract is: the caller flushes its router INSIDE the ``with``
    (``flush_lxmf_router``), the dir is removed when the ``with`` closes, and
    any exit that skips that is cured by the next start's
    ``sweep_dead_owner_dirs`` (the PID is in the dir name). Review r2 named
    the 1-in-2 ``os._exit``: ``LocalClientInterface.teardown`` calls
    ``RNS.exit()`` from its reader thread when detach closes the socket,
    racing the main thread's atexit.
    """
    sweep_dead_owner_dirs(prefix, owner_module=owner_module)
    path = tempfile.mkdtemp(prefix=f"{prefix}pid{os.getpid()}-")
    try:
        yield path
    finally:
        _rmtree_witnessed(path, "own dir")


@contextlib.contextmanager
def lxmf_router_flushed(get_router):
    """Flush ``get_router()`` on EVERY way out of the block (review r1 F5).

    A getter, not the router: the block usually builds it, and an exception
    before that leaves ``None`` (a no-op). Nest it INSIDE ``bounded_block`` so
    a wedged teardown stays bounded, and inside ``exit_scoped_tmpdir`` so the
    persist lands before the dir is removed.
    """
    try:
        yield
    finally:
        flush_lxmf_router(get_router())


def flush_lxmf_router(router) -> None:
    """Run LXMRouter's exit persist NOW, while its storagepath still exists.

    LXMF guards the handler (``exit_handler_running``), so the atexit call
    later is a no-op. Not every LXMF writer is guarded: an announce carrying a
    stamp cost after this point runs ``save_outbound_stamp_costs`` (makedirs)
    and can re-create the dir (review r2 F3; 0 of 1,553 leaked dirs held that
    file) — the next start's sweep removes it. A failure is logged, not
    raised: losing ephemeral lab LXMF state is not worth a failed trace.
    """
    if router is None:
        return
    try:
        router.exit_handler()
    except Exception as exc:  # noqa: BLE001 — third-party teardown, any shape
        logger.warning("lab tmpdir: LXMRouter flush failed: %s", exc)


# ---------------------------------------------------------------- identity


def _identity_path_for(name: str) -> Path:
    """``~/.config/meshforge/<name>_identity`` (sudo-safe via paths util).

    ``MESHFORGE_LAB_IDENTITY_DIR`` overrides the directory — the virtual
    fleet (lab.virtual_fleet) gives each virtual node its OWN identity dir
    so a sandboxed echo/tracer never shares (or clobbers) the box's real
    lab identities. Unset = production behavior, unchanged.
    """
    import os

    from utils.paths import get_real_user_home

    override = os.environ.get("MESHFORGE_LAB_IDENTITY_DIR")
    if override:
        return Path(override) / f"{name}_identity"
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
