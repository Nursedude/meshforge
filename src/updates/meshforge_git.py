"""git-layer SSOT for MeshForge self-update state.

Born from the 2026-07-10 follow-up ("I have not successfully updated
MeshForge"): the old self-update compared version STRINGS (src/__version__.py
vs the same file on GitHub main), which only move on releases — so a repo
that ships continuously by commit never showed an update. The pull ran as
root (risking root-owned objects in an operator-owned repo), used a merging
`git pull`, and the completion dialog said "Update Complete" without the
running services ever restarting — the box kept executing old code, which
reads exactly like a failed update.

This module reads the truth git itself acts on: HEAD vs origin/<branch>
after a real fetch, run AS THE REPO OWNER, with dirty/diverged states
surfaced honestly. The upgrade claim is gated on HEAD actually moving to
the remote SHA — never on exit 0.
"""

import logging
import os
import pwd
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_BRANCH = 'main'


@dataclass
class MeshForgeGitState:
    """Re-derived git truth for the MeshForge checkout."""
    is_git_repo: bool = True
    repo_dir: Optional[str] = None
    owner: Optional[str] = None
    head: Optional[str] = None
    remote_head: Optional[str] = None
    behind: Optional[int] = None   # commits origin/<branch> has that HEAD lacks
    ahead: Optional[int] = None    # local commits the remote lacks
    dirty: bool = False
    update_available: bool = False
    fetch_ok: Optional[bool] = None  # None = fetch not attempted
    error: Optional[str] = None


def repo_root() -> Path:
    """The checkout this code runs from: src/updates/ -> repo root."""
    return Path(__file__).resolve().parents[2]


def _repo_owner(repo_dir: Path) -> Tuple[Optional[int], Optional[str]]:
    try:
        uid = os.stat(repo_dir / '.git').st_uid
        return uid, pwd.getpwuid(uid).pw_name
    except (OSError, KeyError):
        return None, None


def _git(repo_dir: Path, args: List[str], timeout: int = 30,
         as_owner: bool = True) -> subprocess.CompletedProcess:
    """Run git in the repo — AS THE REPO OWNER when we are root and the repo
    is not root-owned. Root-created objects/locks in an operator-owned repo
    are the read/write split at the filesystem level: the operator's next
    plain `git pull` dies on permissions.
    """
    cmd = ['git', '-C', str(repo_dir)] + args
    if as_owner and os.geteuid() == 0:
        owner_uid, owner_name = _repo_owner(repo_dir)
        if owner_uid not in (None, 0):
            cmd = ['sudo', '-u', owner_name, '-H'] + cmd
    # Never prompt: git/ssh ask for credentials or host-key confirmation on
    # /dev/tty directly (capture_output does NOT cover that path). The TUI
    # now runs this check in a background thread, so a prompt would draw
    # over the live whiptail screen and steal keystrokes (2026-08-14 review
    # F5). A repo that needs interactive auth should fail fast and read as
    # "could not fetch" — the honest state.
    env = dict(os.environ,
               GIT_TERMINAL_PROMPT='0',
               GIT_SSH_COMMAND='ssh -oBatchMode=yes')
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, env=env)


def get_meshforge_git_state(fetch: bool = True,
                            branch: str = DEFAULT_BRANCH,
                            repo_dir: Optional[Path] = None) -> MeshForgeGitState:
    """Re-derive the checkout's update state (read-only apart from `git fetch`).

    Honest failure mode: a failed fetch does NOT read as "up to date" —
    ``fetch_ok=False`` + the ahead/behind counts of the LAST KNOWN remote
    ref, with the blindness recorded in ``error``.
    """
    state = MeshForgeGitState()
    repo = repo_dir or repo_root()
    state.repo_dir = str(repo)

    if not (repo / '.git').exists():
        state.is_git_repo = False
        return state

    _, state.owner = _repo_owner(repo)

    try:
        if fetch:
            fetched = _git(repo, ['fetch', 'origin', branch], timeout=60)
            state.fetch_ok = fetched.returncode == 0
            if not state.fetch_ok:
                state.error = ('git fetch failed (offline?): '
                               + (fetched.stderr or '').strip()[:200])

        head = _git(repo, ['rev-parse', 'HEAD'], timeout=10)
        if head.returncode != 0:
            state.error = f"rev-parse failed: {(head.stderr or '').strip()[:200]}"
            return state
        state.head = head.stdout.strip()

        remote = _git(repo, ['rev-parse', f'origin/{branch}'], timeout=10)
        if remote.returncode == 0:
            state.remote_head = remote.stdout.strip()

        counts = _git(repo, ['rev-list', '--left-right', '--count',
                             f'HEAD...origin/{branch}'], timeout=15)
        if counts.returncode == 0:
            parts = counts.stdout.split()
            if len(parts) == 2:
                state.ahead, state.behind = int(parts[0]), int(parts[1])

        status = _git(repo, ['status', '--porcelain'], timeout=15)
        if status.returncode == 0:
            state.dirty = bool(status.stdout.strip())

    except subprocess.TimeoutExpired as e:
        state.error = f'git timed out: {e}'
        return state
    except OSError as e:
        state.error = f'git failed to run: {e}'
        return state

    # An update exists when the remote is strictly ahead. A failed fetch
    # leaves this judged against the stale ref — surfaced via fetch_ok/error.
    state.update_available = bool(state.behind)
    return state


def run_meshforge_git_update(branch: str = DEFAULT_BRANCH,
                             repo_dir: Optional[Path] = None,
                             timeout: int = 120) -> Tuple[bool, str]:
    """Fast-forward the checkout to origin/<branch>, success RE-DERIVED.

    ``--ff-only``: a diverged local branch must never be silently merged by
    an updater — that is an operator decision. Success requires HEAD to
    actually equal the remote SHA afterwards, never exit 0.
    """
    repo = repo_dir or repo_root()

    before = get_meshforge_git_state(fetch=True, branch=branch, repo_dir=repo)
    if not before.is_git_repo:
        return False, f'{repo} is not a git checkout'
    if before.fetch_ok is False:
        return False, before.error or 'git fetch failed'
    if before.dirty:
        return False, (
            'the working tree has local modifications — refusing to update '
            'over them. Commit/stash them (git status) and retry.'
        )
    if before.ahead:
        return False, (
            f'local branch is {before.ahead} commit(s) AHEAD of '
            f'origin/{branch} — a fast-forward is impossible. Push or reset '
            'deliberately; the updater will not merge for you.'
        )
    if not before.behind:
        return True, f'already up to date at {(before.head or "")[:12]}'

    try:
        result = _git(repo, ['merge', '--ff-only', f'origin/{branch}'],
                      timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f'git merge --ff-only timed out after {timeout}s'
    except OSError as e:
        return False, f'git failed to run: {e}'

    after = get_meshforge_git_state(fetch=False, branch=branch, repo_dir=repo)

    if result.returncode != 0:
        return False, ('git fast-forward failed:\n'
                       + (result.stderr or result.stdout or '')[:400])
    if not after.head or after.head != before.remote_head:
        return False, (
            f'git exited 0 but HEAD is {(after.head or "?")[:12]}, expected '
            f'{(before.remote_head or "?")[:12]} — the update did not land '
            '(silent no-op caught).'
        )

    return True, (f'updated {(before.head or "")[:12]} -> '
                  f'{(after.head or "")[:12]} '
                  f'({before.behind} commit(s), verified by re-reading HEAD)')


def requirements_changed(old_head: str, new_head: str,
                         repo_dir: Optional[Path] = None) -> Optional[bool]:
    """Did any requirements file change between two SHAs?

    None = could not determine (the caller must treat unknown as "run the
    deps step" — skipping deps on unknown is the silent-failure direction).
    """
    repo = repo_dir or repo_root()
    try:
        result = _git(repo, ['diff', '--name-only', f'{old_head}..{new_head}'],
                      timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    changed = result.stdout.splitlines()
    return any('requirements' in path for path in changed)


def meshforge_services_to_restart() -> List[str]:
    """Active meshforge-owned systemd units that host the updated code.

    Derived from the live systemd state, not a hardcoded list — a unit that
    is not active is not restarted (and not falsely reported)."""
    try:
        result = subprocess.run(
            ['systemctl', 'list-units', '--type=service', '--state=active',
             '--plain', '--no-legend', 'meshforge*'],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    units = []
    for line in result.stdout.splitlines():
        name = line.split()[0] if line.split() else ''
        if name.endswith('.service'):
            units.append(name[:-len('.service')])
    return units
