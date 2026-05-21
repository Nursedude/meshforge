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
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import socket
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

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


RNS_INIT_TIMEOUT_S = float(os.environ.get("MESHFORGE_LAB_RNS_INIT_TIMEOUT", "60"))

# Cmdline substrings that are legitimate owners of an `@rns/<instance>`
# shared-instance LISTEN socket. Deliberately narrow: rnsd (canonical)
# and `reticulum` (some distros' wrapper). Other daemons that host an
# RNS instance via `share_instance = Yes` (MeshAnchor's daemon.py is the
# concrete instance — see Issue #69 / 2026-05-20) are NOT allowed,
# because their RPC subprocess speaks a different dialect than rnsd
# and breaks every RNS client that joins as a shared-instance peer.
# The right deployment for a box hosting both projects is to run only
# one RNS host (rnsd) and have the other daemon join as a client.
_RNS_LISTENER_ALLOWED_PATTERNS = ("rnsd", "reticulum")


def _parse_ss_listener_line(line: str, instance_name: str) -> Optional[tuple]:
    """Extract ``(pid:int, cmd:str)`` from one ``ss -xnpl`` line for the
    `@rns/<instance>` socket. Returns None if the line doesn't match.

    Tolerant of the kernel's varying field count (the address column
    sometimes wraps). Anchors on the `@rns/<instance>` token and on the
    ``users:(("<cmd>",pid=<n>,...))`` tail that ``ss -p`` appends.
    """
    needle = f"@rns/{instance_name}"
    if needle not in line:
        return None
    m = re.search(r'users:\(\("([^"]+)",pid=(\d+),', line)
    if not m:
        return None
    return int(m.group(2)), m.group(1)


def _read_instance_name_from_config(configdir: str) -> Optional[str]:
    """Parse ``instance_name = <name>`` out of ``<configdir>/config``.

    Returns None if the file is absent or the directive isn't present —
    a missing config is normal during first-ever RNS init.
    """
    try:
        path = Path(configdir) / "config"
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("instance_name"):
                _, _, rhs = line.partition("=")
                name = rhs.strip()
                if name:
                    return name
    except OSError:
        pass
    return None


def check_rns_listener_owner(instance_name: str) -> Optional[str]:
    """Verify ``@rns/<instance_name>`` LISTEN owner looks like a real RNS
    process. Returns None on pass (or no listener found — let
    ``RNS.Reticulum()`` create one). Raises ``RuntimeError`` with a
    diagnostic message when the listener is owned by a process whose
    cmdline doesn't match any pattern in ``_RNS_LISTENER_ALLOWED_PATTERNS``.

    Why: a non-RNS process can claim ``@rns/<name>`` ahead of rnsd (e.g.
    a manually-launched MeshAnchor daemon that orphans to PID 1 and
    holds the abstract socket for hours). Every subsequent RNS client
    connects to that process, receives non-RNS-protocol bytes, and dies
    with EOFError from ``rpc_connection.recv()`` deep inside RNS — a
    trace that takes 30+ minutes to root-cause. This preflight surfaces
    the collision in one line at process start.

    Variant of the rnsd-RPC fragility class — siblings: Issue #58 (HAT
    overlay port hijack), #61 (single-thread socketserver deadlock),
    #63 (delivery_counters write canary), #68 (unix_stream_connect
    wedge).
    """
    try:
        proc = subprocess.run(
            ["ss", "-xnpl"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        # ss missing or hung — don't fail-loud here; let RNS proceed and
        # surface its own error if there's a real problem.
        logger.debug("lab: rns-listener preflight skipped (ss unavailable: %s)", exc)
        return None

    pids = set()
    for line in proc.stdout.splitlines():
        parsed = _parse_ss_listener_line(line, instance_name)
        if parsed:
            pids.add(parsed[0])

    if not pids:
        # No existing listener — RNS will create one. Nothing to check.
        return None

    # `ss -p` only reports the binary basename (e.g. "python3"), which
    # is identical for rnsd and any rogue python daemon. Read the full
    # cmdline from /proc to make the allowed-vs-suspicious determination.
    suspicious = []
    full_cmdlines: dict = {}
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode(
                    "utf-8", errors="replace").strip()
        except OSError:
            # Process died between ss and /proc read — treat as gone,
            # no diagnostic possible.
            cmdline = ""
        full_cmdlines[pid] = cmdline
        if not any(pat in cmdline.lower() for pat in _RNS_LISTENER_ALLOWED_PATTERNS):
            suspicious.append(pid)

    if suspicious:
        pid = suspicious[0]
        cmdline = full_cmdlines[pid] or "<unknown — process exited>"
        raise RuntimeError(
            f"RNS shared-instance listener @rns/{instance_name} is owned by "
            f"pid={pid} cmd={cmdline!r} — not an RNS process. Every RNS "
            f"client connecting to this socket will fail with EOFError on the "
            f"first RPC call. Fix: identify and stop this process "
            f"(`sudo kill {pid}`), then `sudo systemctl restart rnsd.service`."
        )

    owner_pid = next(iter(pids))
    logger.info(
        "lab: rns-listener preflight OK — @rns/%s owned by pid=%d (%s)",
        instance_name, owner_pid, full_cmdlines[owner_pid],
    )
    return None


def init_reticulum_with_watchdog(
    configdir: str,
    *,
    loglevel: int = 2,
    timeout_s: float = RNS_INIT_TIMEOUT_S,
):
    """Call ``RNS.Reticulum(configdir=..., loglevel=...)`` under a hard
    timeout watchdog.

    rnsd's ``@rns/default/rpc`` abstract Unix socket listener can wedge
    (observed 2026-05-15 on moc1, 18h silent freeze). When that happens
    a fresh ``RNS.Reticulum()`` blocks in kernel ``unix_wait_for_peer``
    on ``connect()`` with no userland timeout, and systemd reports the
    unit ``active (running)`` while it produces zero output. See
    ``project_rnsd_rpc_listener_wedge.md``.

    We run the constructor on the main thread (it installs signal
    handlers which Python only permits from the main thread) and arm
    a daemon watchdog thread that calls ``os._exit(2)`` if the
    constructor doesn't complete within ``timeout_s``. The kernel
    ``connect()`` is uninterruptible from userland, so a hard process
    abort is the only escape; systemd's timer will restart the unit
    on the next interval.

    Returns the ``Reticulum`` instance on success. Re-raises whatever
    the constructor raised on failure (preserves existing try/except
    semantics in callers).

    Before the constructor runs, verify the shared-instance LISTEN socket
    isn't owned by a foreign process (see ``check_rns_listener_owner``).
    The owner is parsed from the configdir's ``config`` file when present;
    if absent or unparseable, the check is skipped silently.
    """
    import RNS

    instance_name = _read_instance_name_from_config(configdir)
    if instance_name:
        check_rns_listener_owner(instance_name)

    done = threading.Event()

    def _watchdog() -> None:
        if not done.wait(timeout=timeout_s):
            logger.error(
                "lab: RNS.Reticulum() did not return after %.1fs — "
                "likely rnsd RPC listener wedge. Aborting process so "
                "systemd can restart us. See "
                "project_rnsd_rpc_listener_wedge.md.",
                timeout_s,
            )
            os._exit(2)

    watchdog = threading.Thread(
        target=_watchdog, daemon=True, name="rns-init-watchdog",
    )
    watchdog.start()
    try:
        return RNS.Reticulum(configdir=configdir, loglevel=loglevel)
    finally:
        done.set()


@contextlib.contextmanager
def bounded_block(timeout_s: float, *, label: str) -> Iterator[None]:
    """Run the wrapped block under a hard timeout watchdog.

    Sibling to ``init_reticulum_with_watchdog`` but generic: any region
    that might wedge on rnsd's RPC socket (LXMRouter init, announce,
    path-resolve, handle_outbound) can be wrapped here. A daemon watchdog
    thread calls ``os._exit(2)`` if the block doesn't exit within
    ``timeout_s``. Normal completion AND exceptions both disarm the
    watchdog.

    Why ``os._exit``: the kernel ``connect()`` in
    ``unix_wait_for_peer`` is uninterruptible from userland — SIGTERM
    queues behind the syscall. Only ``os._exit`` (or systemd-driven
    SIGKILL via ``TimeoutStartSec=``) gets the process out cleanly.

    See ``project_rnsd_rpc_listener_wedge.md`` for the wedge fingerprint
    and recovery recipe. The constructor watchdog
    (``init_reticulum_with_watchdog``) only covers ``RNS.Reticulum()``;
    this covers everything else.
    """
    done = threading.Event()

    def _watchdog() -> None:
        if not done.wait(timeout=timeout_s):
            logger.error(
                "lab: %s did not complete after %.1fs — likely rnsd "
                "RPC listener wedge. Aborting process so systemd can "
                "restart us. See project_rnsd_rpc_listener_wedge.md.",
                label, timeout_s,
            )
            os._exit(2)

    watchdog = threading.Thread(
        target=_watchdog, daemon=True, name=f"bounded-{label}",
    )
    watchdog.start()
    try:
        yield
    finally:
        done.set()


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
