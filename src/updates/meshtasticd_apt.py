"""apt-layer SSOT for meshtasticd install/update state.

Born from the 2026-07-10 TUI-updates audit: the old update path ran
``sudo apt update && sudo apt upgrade meshtasticd`` (interactive prompt →
EOF abort under capture_output; upgrades EVERYTHING; conffile prompts can
hang the 300s timeout), compared "latest" against GitHub firmware releases
(not what apt can actually install), was blind to apt holds (deliberate
canary pins on this fleet), and reported success on apt exit 0 without
re-deriving the installed version — a kept-back no-op read as "Update
Complete".

This module reads the truth apt itself acts on (``apt-cache policy``
Candidate), surfaces holds and unsatisfiable candidates honestly, can
diagnose the mismatched-OBS-repo class (a ``Debian_Testing`` line on a
``Debian_13`` box publishes the same version string built against a newer
libc — apt binds the candidate to the uninstallable stanza), and gates the
"updated" claim on a re-read of the installed version, never on exit 0.
"""

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from utils.service_check import _sudo_cmd, _sudo_write

logger = logging.getLogger(__name__)

APT_SOURCES_DIR = Path('/etc/apt/sources.list.d')
OS_RELEASE_PATH = Path('/etc/os-release')

# Non-interactive apt invocation: never prompt (capture_output has no TTY and
# an unanswered prompt reads as a hang until the timeout), and on conffile
# conflicts KEEP the local config — meshtasticd's /etc config is
# operator-managed (the #22/#58 "don't clobber config.yaml" surface).
_APT_ENV_PREFIX = ['env', 'DEBIAN_FRONTEND=noninteractive']
_APT_CONF_OPTS = [
    '-o', 'Dpkg::Options::=--force-confdef',
    '-o', 'Dpkg::Options::=--force-confold',
]


@dataclass
class RepoLine:
    """One deb line in an apt sources file."""
    path: str
    lineno: int  # 1-based
    line: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}: {self.line}"


@dataclass
class MeshtasticdAptState:
    """Re-derived apt truth for the meshtasticd package."""
    apt_available: bool = True
    installed: Optional[str] = None
    candidate: Optional[str] = None
    held: bool = False
    update_available: bool = False
    # None = simulation not run (unknown), never assumed installable.
    candidate_installable: Optional[bool] = None
    blocking_detail: Optional[str] = None
    mismatched_repos: List[RepoLine] = field(default_factory=list)
    error: Optional[str] = None


def _run(cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _parse_policy(stdout: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract (installed, candidate) from ``apt-cache policy`` output."""
    installed = candidate = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith('Installed:'):
            installed = line.split(':', 1)[1].strip()
        elif line.startswith('Candidate:'):
            candidate = line.split(':', 1)[1].strip()
    if installed == '(none)':
        installed = None
    if candidate == '(none)':
        candidate = None
    return installed, candidate


def get_installed_version() -> Optional[str]:
    """Installed meshtasticd version per apt policy (None when absent)."""
    try:
        result = _run(['apt-cache', 'policy', 'meshtasticd'])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    installed, _ = _parse_policy(result.stdout)
    return installed


def _dpkg_version_lt(a: str, b: str) -> bool:
    """True iff a < b under Debian version semantics (dpkg is the SSOT —
    handles ~beta/~alpha epochs correctly where naive tuple parsing fails)."""
    try:
        result = _run(['dpkg', '--compare-versions', a, 'lt', b], timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False  # conservative: never a phantom update on a broken dpkg


def _os_version_id(os_release_path: Path = OS_RELEASE_PATH) -> Optional[str]:
    """VERSION_ID from /etc/os-release (e.g. '13'), None if unreadable."""
    try:
        content = os_release_path.read_text()
    except OSError:
        return None
    match = re.search(r'^VERSION_ID="?([A-Za-z0-9.]+)"?', content, re.MULTILINE)
    return match.group(1) if match else None


def find_mismatched_meshtastic_repos(
    sources_dir: Path = APT_SOURCES_DIR,
    os_release_path: Path = OS_RELEASE_PATH,
) -> List[RepoLine]:
    """Meshtastic OBS repo lines whose target distro is NOT this box's.

    The failure class: an early-install ``.../Meshtastic:/beta/Debian_Testing``
    line on a Debian 13 box. Once testing's libc moves past stable's, that
    repo's build of the SAME version string becomes uninstallable, and apt
    binds the candidate to it → "held broken packages" forever. Only lines
    with a distro token that disagrees with VERSION_ID are flagged; when
    VERSION_ID is unreadable we refuse to guess and flag nothing.
    """
    version_id = _os_version_id(os_release_path)
    if not version_id:
        return []

    mismatched: List[RepoLine] = []
    try:
        list_files = sorted(sources_dir.glob('*.list'))
    except OSError:
        return []
    for list_file in list_files:
        try:
            lines = list_file.read_text().splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped.startswith('deb') or 'Meshtastic' not in stripped:
                continue
            match = re.search(r'(Debian|Raspbian)_([A-Za-z0-9.]+)', stripped)
            if match and match.group(2) != version_id:
                mismatched.append(RepoLine(str(list_file), idx, stripped))
    return mismatched


def disable_repo_lines(repo_lines: List[RepoLine]) -> Tuple[bool, str]:
    """Comment out the given repo lines, keeping a one-time ``.meshforge-bak``.

    Uses the atomic sudo-aware writer so a partial write can't corrupt an apt
    sources file. Returns (ok, detail); any per-file failure fails the whole
    call loudly — a half-disabled repo set is worse than an untouched one.
    """
    if not repo_lines:
        return True, "nothing to disable"

    by_path = {}
    for entry in repo_lines:
        by_path.setdefault(entry.path, []).append(entry)

    changed = []
    for path, entries in by_path.items():
        try:
            original = Path(path).read_text()
        except OSError as e:
            return False, f"could not read {path}: {e}"

        backup_path = f"{path}.meshforge-bak"
        if not Path(backup_path).exists():
            ok, msg = _sudo_write(backup_path, original)
            if not ok:
                return False, f"backup of {path} failed: {msg}"

        lines = original.splitlines()
        target_linenos = {e.lineno for e in entries}
        for lineno in target_linenos:
            if lineno < 1 or lineno > len(lines):
                return False, f"{path}:{lineno} out of range — file changed underneath us"
            lines[lineno - 1] = (
                "# disabled by MeshForge (distro-mismatched Meshtastic repo): "
                + lines[lineno - 1]
            )
        ok, msg = _sudo_write(path, "\n".join(lines) + "\n")
        if not ok:
            return False, f"write of {path} failed: {msg}"
        changed.append(f"{path} ({len(target_linenos)} line(s))")

    return True, "disabled: " + ", ".join(changed)


def apt_update(timeout: int = 240) -> Tuple[bool, str]:
    """Refresh apt lists. Failure is surfaced but is often non-fatal (a single
    unreachable repo) — the caller decides whether to continue."""
    cmd = _sudo_cmd(_APT_ENV_PREFIX + ['apt-get', 'update'])
    try:
        result = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"apt-get update timed out after {timeout}s"
    except OSError as e:
        return False, f"apt-get update failed to start: {e}"
    if result.returncode == 0:
        return True, "apt lists refreshed"
    return False, (result.stderr or result.stdout or f"exit {result.returncode}")[-800:]


def get_meshtasticd_apt_state(simulate: bool = True) -> MeshtasticdAptState:
    """Re-derive meshtasticd's apt state (read-only; never escalates).

    ``simulate=True`` additionally dry-runs the upgrade so an unsatisfiable
    candidate (the mismatched-repo class) is caught BEFORE the operator is
    told an update is available.
    """
    state = MeshtasticdAptState()

    if not (shutil.which('apt-cache') and shutil.which('apt-get')):
        state.apt_available = False
        return state

    try:
        policy = _run(['apt-cache', 'policy', 'meshtasticd'])
    except subprocess.TimeoutExpired:
        state.error = 'apt-cache policy timed out'
        return state
    except OSError as e:
        state.error = f'apt-cache policy failed: {e}'
        return state
    if policy.returncode != 0:
        state.error = (policy.stderr or policy.stdout or
                       f'apt-cache policy exit {policy.returncode}').strip()[:400]
        return state
    state.installed, state.candidate = _parse_policy(policy.stdout)

    try:
        # showhold is a read-only query — never sudo (service_check discipline).
        hold = _run(['apt-mark', 'showhold', 'meshtasticd'], timeout=15)
        if hold.returncode == 0:
            state.held = 'meshtasticd' in hold.stdout.split()
        else:
            # Unknown hold state is NOT "not held": say so instead of guessing.
            note = 'hold state unreadable (apt-mark failed)'
            state.error = f"{state.error}; {note}" if state.error else note
    except (OSError, subprocess.TimeoutExpired):
        note = 'hold state unreadable (apt-mark failed)'
        state.error = f"{state.error}; {note}" if state.error else note

    if state.installed and state.candidate and state.installed != state.candidate:
        state.update_available = _dpkg_version_lt(state.installed, state.candidate)

    if simulate and state.update_available:
        try:
            sim = _run(['apt-get', '-s', 'install', '--only-upgrade', 'meshtasticd'],
                       timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            state.blocking_detail = f'upgrade simulation failed to run: {e}'
        else:
            combined = (sim.stdout or '') + (sim.stderr or '')
            # E: must be LINE-anchored: apt's healthy dry-run prints
            # "NOTE: This is only a simulation!" whose tail is 'E:' —
            # a substring test read every healthy sim as broken (caught
            # live 2026-07-10).
            has_error_lines = any(
                l.startswith('E:') for l in combined.splitlines())
            if sim.returncode == 0 and not has_error_lines:
                state.candidate_installable = True
            else:
                state.candidate_installable = False
                error_lines = [l for l in combined.splitlines()
                               if l.startswith('E:') or 'Depends:' in l]
                state.blocking_detail = '\n'.join(error_lines)[:800] or combined[-800:]
                state.mismatched_repos = find_mismatched_meshtastic_repos()

    return state


def run_meshtasticd_upgrade(
    unhold: bool = False,
    rehold: bool = False,
    timeout: int = 600,
) -> Tuple[bool, str]:
    """Upgrade meshtasticd via apt, with the success claim RE-DERIVED.

    Sequence: optional unhold → apt-get update (best-effort) → noninteractive
    ``install --only-upgrade -y`` → re-read the installed version → optional
    re-hold (the fleet pins deliberately; taking an update does not mean
    surrendering the no-surprise-upgrades posture).

    Success requires BOTH apt exit 0 AND an observed version change — apt
    happily exits 0 while keeping a package back, and that silent no-op used
    to read as "Update Complete".
    """
    before = get_installed_version()
    if before is None:
        return False, 'meshtasticd is not installed per apt — nothing to upgrade'

    unheld = False
    if unhold:
        ok, msg = _apt_mark('unhold')
        if not ok:
            return False, f'apt-mark unhold failed: {msg}'
        unheld = True

    ok, detail = _upgrade_only(before, timeout)

    if unheld and rehold:
        rehold_ok, rehold_msg = _apt_mark('hold')
        if not rehold_ok:
            # A lost pin is a posture change the operator MUST see — append it
            # to the returned detail, never just a log line.
            detail += (
                '\nWARNING: re-hold FAILED — meshtasticd is now UNPINNED and '
                f'future apt upgrades will move it: {rehold_msg}'
            )
    return ok, detail


def _apt_mark(action: str) -> Tuple[bool, str]:
    try:
        result = _run(_sudo_cmd(['apt-mark', action, 'meshtasticd']), timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or
                       f'exit {result.returncode}')[:200]
    return True, result.stdout.strip()


def _upgrade_only(before: str, timeout: int) -> Tuple[bool, str]:
    """The refresh + only-upgrade + re-derive core of the upgrade."""
    notes: List[str] = []
    ok, msg = apt_update()
    if not ok:
        # Often survivable (one dead repo); record it and try the upgrade
        # from the freshest lists we have.
        notes.append(f'apt-get update reported a problem: {msg[:200]}')

    cmd = _sudo_cmd(
        _APT_ENV_PREFIX
        + ['apt-get', 'install', '--only-upgrade', '-y']
        + _APT_CONF_OPTS
        + ['meshtasticd']
    )
    try:
        result = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f'apt-get install timed out after {timeout}s'
    except OSError as e:
        return False, f'apt-get install failed to start: {e}'

    after = get_installed_version()

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or
                  f'exit {result.returncode}')[-800:]
        return False, f'apt-get install failed:\n{detail}'
    if after == before:
        return False, (
            f'apt exited 0 but meshtasticd is still {before} — the package '
            'was kept back (silent no-op). Check `apt-cache policy '
            'meshtasticd` for a held or unsatisfiable candidate.'
        )
    if after is None:
        return False, (f'meshtasticd version unreadable after the upgrade '
                       f'(was {before}) — verify the package state manually')

    detail = f'meshtasticd {before} -> {after} (verified by re-reading apt)'
    if notes:
        detail += '\n' + '\n'.join(notes)
    return True, detail
