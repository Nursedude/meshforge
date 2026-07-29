"""fleet_hosts membership SSOT — THE Python resolver.

Mirror of ``scripts/lib/fleet_hosts.sh``; the two are the only permitted
implementations of the resolution chain and are pinned to each other by
``tests/test_fleet_hosts_resolver.py`` (same fixture tree fed to both, same
answer required). Before convergence (2026-07-29) the tree carried ~13
independent copies — mini rollup, fleet_dup_collector, provision_role,
nomadnet_silence_watch, rns_alignment and 8 shell scripts each re-typed the
chain, and they disagreed about the env override, the /etc tier, comment
parsing, and sudo-home (honest_failure_modes #5 at fleet scale).

Resolution order (per-repo list wins over the generic one)::

    $MESHFORGE_FLEET_HOSTS    - AUTHORITATIVE when set: a missing/unreadable
    $FLEET_HOSTS                override yields NO list rather than falling
                                through to the box's real config (a degraded
                                state must not read as a valid value;
                                FLEET_HOSTS is the legacy alias
                                lab_traffic_rollup documented)
    ~/.config/meshforge/fleet_hosts.<repo-basename>
    /etc/meshforge/fleet_hosts.<repo-basename>
    ~/.config/meshforge/fleet_hosts
    /etc/meshforge/fleet_hosts

Home is the REAL user's home (sudo-safe, MF001) unless an ``env`` mapping is
injected — tests pass ``env={"HOME": ...}`` and ``etc_dir=`` so no assertion
ever depends on the machine running the suite
(feedback_tests_must_pin_ambient_state).

File format: hosts separated by whitespace/newlines; ``#`` starts a comment
anywhere on the line, so ``"moc1  # retired"`` parses as host ``moc1``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Mapping, Optional

DEFAULT_REPO_DIR = "/opt/meshforge"
DEFAULT_ETC_DIR = "/etc/meshforge"
#: First SET (non-empty) key wins and is authoritative.
ENV_OVERRIDE_KEYS = ("MESHFORGE_FLEET_HOSTS", "FLEET_HOSTS")


def parse_fleet_hosts_text(text: str) -> List[str]:
    """Hosts from a fleet_hosts document: ``#``-to-EOL stripped, then
    whitespace-split — identical to the shell lib's sed|tr|grep pipeline."""
    hosts: List[str] = []
    for raw in text.splitlines():
        hosts.extend(raw.split("#", 1)[0].split())
    return hosts


def _readable_file(p: Path) -> bool:
    try:
        return p.is_file() and os.access(p, os.R_OK)
    except OSError:
        return False


def resolve_fleet_hosts_file(
    repo_dir: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> Optional[Path]:
    """The file that wins the resolution order, or ``None`` if no list exists
    (including a SET-but-unresolvable env override — authoritative, no
    fall-through)."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    for key in ENV_OVERRIDE_KEYS:
        val = env_map.get(key)
        if val:
            p = Path(val)
            return p if _readable_file(p) else None
    base = os.path.basename(os.path.normpath(repo_dir or DEFAULT_REPO_DIR))
    if env is None:
        from utils.paths import get_real_user_home
        home: Optional[Path] = get_real_user_home()
    else:
        home = Path(env["HOME"]) if env.get("HOME") else None
    candidates: List[Path] = []
    if home:
        candidates.append(home / ".config" / "meshforge" / f"fleet_hosts.{base}")
    candidates.append(Path(etc_dir) / f"fleet_hosts.{base}")
    if home:
        candidates.append(home / ".config" / "meshforge" / "fleet_hosts")
    candidates.append(Path(etc_dir) / "fleet_hosts")
    for c in candidates:
        if _readable_file(c):
            return c
    return None


def resolve_fleet_hosts(
    repo_dir: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> List[str]:
    """The host list, or ``[]`` when no list resolves. ``[]`` conflates
    absent-list with empty-list on purpose — callers that must refuse a
    silent no-op (fleet_pull's posture) check ``resolve_fleet_hosts_file``."""
    f = resolve_fleet_hosts_file(repo_dir, env=env, etc_dir=etc_dir)
    if f is None:
        return []
    try:
        return parse_fleet_hosts_text(f.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
