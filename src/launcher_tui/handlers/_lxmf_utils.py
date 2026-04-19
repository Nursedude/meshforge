"""LXMF Exclusivity Utility — detect competing LXMF clients per config dir.

Two LXMF clients (NomadNet, Sideband) collide ONLY when they share the
same identity — i.e. the same config directory. They can coexist freely
when each has its own ``--config <dir>`` because:

  - rnsd is the shared instance (port 37428 IPC), they all attach to it
  - each config dir has its own identity and its own LXMF delivery hash
  - storage/lockfile is per-config-dir so there's no file contention

The earlier version of this check looked at port 37428 LISTEN, which is
always rnsd itself when rnsd is running — so it false-warned on every
launch. This version walks /proc for actual nomadnet/sideband processes,
extracts their ``--config <path>`` argument (defaulting to ``~/.nomadnetwork``),
and warns only when the new launch would collide on the same dir.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Optional

# Sudo-safe home resolution (MF001) — when MeshForge runs via `sudo`,
# Path.home() returns /root, which is the wrong identity dir to check.
# Competing LXMF clients are nearly always the same user's processes,
# so use the real user's home as the default.
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Apps we recognise as LXMF clients. The argv[0] basename is checked against
# this set after stripping common prefixes (e.g. python interpreter wrappers).
LXMF_CLIENT_NAMES = {"nomadnet", "sideband"}

# Default config dirs each client uses when --config is not passed.
DEFAULT_CONFIG_DIRS = {
    "nomadnet": ".nomadnetwork",
    "sideband": ".config/sideband",
}


def _normalize_path(path: str | os.PathLike | None) -> Optional[str]:
    """Resolve a path string to an absolute, link-resolved form.

    Returns None for None/empty input. Never raises — invalid paths
    fall back to a best-effort absolute string.
    """
    if not path:
        return None
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except (OSError, ValueError) as e:
        logger.debug("path normalize failed for %r: %s", path, e)
        return str(path)


def _read_cmdline(pid: str) -> list[str]:
    """Return argv list for a /proc PID, or [] on any failure.

    /proc/{pid}/cmdline is null-separated argv. Empty argv means a kernel
    thread or a process that has exited mid-read; both safely treated as
    "not interesting".
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, PermissionError):
        return []
    if not raw:
        return []
    return [seg.decode("utf-8", errors="replace") for seg in raw.split(b"\x00") if seg]


def _argv_client_name(argv: list[str]) -> Optional[str]:
    """Identify which LXMF client argv represents, or None if not one.

    Matches against the basename of argv[0] for simple invocations
    (``/usr/local/bin/nomadnet``) and against argv[1] for python-wrapped
    invocations (``python3 /path/to/nomadnet ...``).
    """
    if not argv:
        return None
    candidates = [argv[0]]
    if len(argv) > 1 and Path(argv[0]).name.startswith("python"):
        candidates.append(argv[1])
    for cand in candidates:
        base = Path(cand).name.lower()
        if base in LXMF_CLIENT_NAMES:
            return base
    return None


def _argv_config_dir(argv: list[str], client: str) -> Optional[str]:
    """Extract the --config dir from argv, falling back to client default.

    Handles both ``--config DIR`` and ``--config=DIR`` forms. Returns the
    normalized absolute path. The default lives in the running user's
    home, NOT in the home of the user calling this check — caller must
    pass through the right home if they care about cross-user matching.
    """
    for i, tok in enumerate(argv):
        if tok == "--config" and i + 1 < len(argv):
            return _normalize_path(argv[i + 1])
        if tok.startswith("--config="):
            return _normalize_path(tok.split("=", 1)[1])
    # No --config — client uses its default in $HOME. MeshForge launches
    # nomadnet as the real user, so resolve relative to that home.
    default_rel = DEFAULT_CONFIG_DIRS.get(client)
    if default_rel is None:
        return None
    return _normalize_path(get_real_user_home() / default_rel)


def _iter_proc_pids() -> Iterable[str]:
    """Yield numeric PID directory names from /proc."""
    try:
        for entry in os.scandir("/proc"):
            if entry.name.isdigit():
                yield entry.name
    except (OSError, PermissionError) as e:
        logger.debug("/proc scan failed: %s", e)


def find_competing_clients(target_config_dir: Optional[str]) -> list[tuple[str, str, str]]:
    """Return list of (pid, client_name, config_dir) tuples conflicting with target.

    A "conflict" means: another LXMF client process is already using
    target_config_dir. Both clients sharing one identity → broken state.

    Self-detection is suppressed via PID comparison so the launcher
    process itself never appears as a competitor.
    """
    target = _normalize_path(target_config_dir) if target_config_dir else \
        _normalize_path(get_real_user_home() / ".nomadnetwork")
    own_pid = str(os.getpid())
    conflicts: list[tuple[str, str, str]] = []

    for pid in _iter_proc_pids():
        if pid == own_pid:
            continue
        argv = _read_cmdline(pid)
        client = _argv_client_name(argv)
        if not client:
            continue
        cfg = _argv_config_dir(argv, client)
        if cfg and target and cfg == target:
            conflicts.append((pid, client, cfg))

    return conflicts


def ensure_lxmf_exclusive(
    dialog,
    starting_app: str,
    config_dir: Optional[str] = None,
    **_kwargs,
) -> bool:
    """Ensure no other LXMF client is using the same config dir as this launch.

    Args:
        dialog: DialogBackend for user prompts.
        starting_app: Name of the app being started (currently only
            "nomadnet" triggers the check; other values pass through OK).
        config_dir: The ``--config`` path the new launch will use. Pass
            None for the client's default config dir. Two launches with
            different config_dir values never conflict.

    Returns:
        True if OK to proceed, False if the user cancelled.
    """
    if starting_app != "nomadnet":
        return True

    conflicts = find_competing_clients(config_dir)
    if not conflicts:
        return True

    # Build a human-readable description of every conflicting process.
    lines = []
    for pid, client, cfg in conflicts:
        lines.append(f"  • {client} (PID {pid}) using {cfg}")
    detail = "\n".join(lines)
    target = config_dir or "default (~/.nomadnetwork)"

    return dialog.yesno(
        "LXMF Identity Conflict",
        f"Another LXMF client is already using this config dir:\n\n"
        f"{detail}\n\n"
        f"You're trying to launch a new client with config:\n"
        f"  {target}\n\n"
        f"Two clients sharing one identity will fight over the\n"
        f"identity file and lose messages. Continue anyway?",
    )
