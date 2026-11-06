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

A tier that is ABSENT passes to the next one. A tier that is PRESENT but
cannot be read — permission, a directory, a dangling symlink — raises
:class:`FleetHostsUnreadable` naming the path, and so does a read that
fails after resolution (r7e #2, operator 2026-11-05: "quietly failing is
not an answer"). Falling through would hand the caller the NEXT tier's
list — a different fleet, reported as the fleet.

File format: hosts separated by whitespace/newlines; ``#`` starts a comment
anywhere on the line, so ``"moc1  # retired"`` parses as host ``moc1``.
"""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

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


class FleetHostsUnreadable(OSError):
    """A fleet host list EXISTS at ``path`` but cannot be read.

    An ``OSError`` so a caller's existing ``except OSError`` around a read
    still refuses; every caller of the resolver must turn it into its own
    refusal / UNKNOWN, never into "no fleet"."""

    def __init__(self, path: Path, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(
            "fleet host list %s is unreadable (%s) - refusing to fall "
            "through to another list" % (self.path, reason))


#: A tier we cannot prove absent. Shared wording with the shell twin.
UNCHECKABLE = "cannot be checked: a parent directory is not searchable"


def _usable(p: Path) -> bool:
    """True = a readable regular file; False = provably absent (ENOENT, or a
    non-directory where a parent should be); raises when present but
    unusable OR uncheckable. Explicit ``lstat`` rather than ``Path.is_file``
    — the latter's error handling differs by Python version (3.14 swallows
    EACCES), and a tier under an unsearchable directory must never read as
    absent (r7f F2: the shell twin did exactly that)."""
    try:
        st = os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except PermissionError as exc:
        raise FleetHostsUnreadable(p, UNCHECKABLE) from exc
    except (OSError, ValueError) as exc:
        raise FleetHostsUnreadable(p, getattr(exc, "strerror", None) or str(exc)) from exc
    if stat.S_ISLNK(st.st_mode):
        try:
            st = os.stat(p)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise FleetHostsUnreadable(p, "dangling symlink") from exc
        except PermissionError as exc:
            raise FleetHostsUnreadable(p, "symlink target " + UNCHECKABLE) from exc
        except OSError as exc:
            # r7g F1: ELOOP is a loop, not a permission problem; same words
            # as the shell twin.
            why = ("symlink loop" if exc.errno == errno.ELOOP
                   else exc.strerror or str(exc))
            raise FleetHostsUnreadable(p, why) from exc
    if stat.S_ISDIR(st.st_mode):
        raise FleetHostsUnreadable(p, "a directory, not a file")
    if not stat.S_ISREG(st.st_mode):
        raise FleetHostsUnreadable(p, "not a regular file")
    if not os.access(p, os.R_OK):
        raise FleetHostsUnreadable(p, "permission denied")
    return True


def resolve_fleet_hosts_file(
    repo_dir: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> Optional[Path]:
    """The file that wins the resolution order, or ``None`` if no list exists
    (including a SET-but-ABSENT env override — authoritative, no
    fall-through). Raises :class:`FleetHostsUnreadable` when the winning
    candidate — or any tier above it — is present but unreadable."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    for key in ENV_OVERRIDE_KEYS:
        val = env_map.get(key)
        if val:
            p = Path(val)
            return p if _usable(p) else None
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
        if _usable(c):
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
    silent no-op (fleet_pull's posture) check ``resolve_fleet_hosts_file``.
    An unreadable list RAISES :class:`FleetHostsUnreadable` — never ``[]``."""
    f = resolve_fleet_hosts_file(repo_dir, env=env, etc_dir=etc_dir)
    if f is None:
        return []
    try:
        return parse_fleet_hosts_text(f.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        raise FleetHostsUnreadable(f, getattr(exc, "strerror", None) or str(exc)) from exc


# --- the generated /etc/hosts fleet block -------------------------------------
#
# THE definition of the block's delimiters and domain. Two consumers read
# them: ``scripts/gen_fleet_hosts.py`` WRITES the block, and membership
# readers (mini's ``_known_fleet_subjects``) PARSE it. Independent hardcodes
# WILL drift (honest_failure_modes #5), and a drift here would be SILENT — a
# reader would simply find no block and quietly know nothing.
HOSTS_BLOCK_BEGIN = "# BEGIN meshforge fleet (generated by scripts/gen_fleet_hosts.py)"
HOSTS_BLOCK_END = "# END meshforge fleet"
DEFAULT_DOMAIN = "mf.internal"


def parse_hosts_block(text: str) -> List[Tuple[str, List[str]]]:
    """``[(addr, [name, ...]), ...]`` from the DELIMITED meshforge block only.

    A second membership registry, and the only one present on EVERY box:
    ``fleet_hosts`` lives on the manager, while this block is generated and
    hourly self-healed on all of them (``fleet_hosts_selfheal.sh``).

    ⚠️ **Only ever the delimited block.** Parsing the whole file would make
    ``localhost``, the ``127.0.1.1`` line and any hand-added entry count as
    fleet members — widening what is "expected" is the one direction a
    membership registry must never fail in.

    ⚠️ **A torn block yields nothing.** BEGIN without END means the artifact
    is damaged; the safe read is to know less, not to guess more. The writer
    repairs torn blocks (``gen_fleet_hosts.split_hosts``); a reader must not.
    """
    if HOSTS_BLOCK_BEGIN not in text or HOSTS_BLOCK_END not in text:
        return []
    body = text.split(HOSTS_BLOCK_BEGIN, 1)[1].split(HOSTS_BLOCK_END, 1)[0]
    out: List[Tuple[str, List[str]]] = []
    for raw in body.splitlines():
        fields = raw.split("#", 1)[0].split()
        if len(fields) < 2:
            continue
        addr = fields[0].strip().lower()
        names = [h.strip().lower().rstrip(".") for h in fields[1:] if h.strip()]
        if addr and names:
            out.append((addr, names))
    return out
