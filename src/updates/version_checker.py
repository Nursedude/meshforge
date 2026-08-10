"""
MeshForge Version Checker

Checks installed versions of:
- meshtasticd (Linux native daemon)
- meshtastic CLI (Python package)
- Node firmware (via connected device)

And compares them against latest available versions.
"""

import re
import subprocess
import json
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

# Module-level safe imports
_version_mod, _HAS_VERSION = safe_import('__version__', '__version__')
from utils.cli import find_meshtastic_cli
# Shared SSOT for the fleet version floor (requirements/core.txt) — the SAME
# parser + below-floor test the watchdog dep_version_drift probe uses, so the
# TUI badge and the watchdog page can never disagree about the baseline
# (honest_failure_modes item 5; feedback_version_env_rigor).
from utils.requirements_floor import read_floor, version_below

# Cache for version checks to avoid hitting APIs too frequently
_version_cache: Dict[str, Any] = {}
_cache_ttl = timedelta(hours=1)


@dataclass
class VersionInfo:
    """Version information for a component"""
    name: str
    installed: Optional[str] = None
    latest: Optional[str] = None
    update_available: bool = False
    install_command: Optional[str] = None
    update_command: Optional[str] = None
    error: Optional[str] = None
    # When this component is gated against the REVIEWED fleet baseline rather
    # than raw PyPI-latest, ``fleet_floor`` is that baseline (from
    # requirements/core.txt) and ``update_available`` means "installed BELOW the
    # floor" — a real laggard — not "PyPI moved past the floor" (which is a
    # reviewed bump, surfaced informationally in ``pypi_latest``). See
    # feedback_version_env_rigor (the 2026-06-17 phantom-update class).
    fleet_floor: Optional[str] = None
    pypi_latest: Optional[str] = None
    # apt-managed components (meshtasticd): a hold is a DELIBERATE pin on this
    # fleet (canary rolls) — surfaced as its own state, never as a nagging
    # "update available" nor silently hidden. ``notes`` carries operator-facing
    # context (pin state, candidate waiting behind the pin, ...).
    held: bool = False
    notes: Optional[str] = None
    # The update MECHANISM, as a property of the row — so every consumer reads
    # ONE truth instead of re-deriving its own predicate (the 2026-07-16
    # "1 available -> nothing to update" nag: the badge counted
    # ``update_available`` while Update All required an in-app command, so a
    # firmware tag inflated the badge but Update All did nothing).
    #  - out_of_band: no in-app apply path at all (firmware = Web Flasher).
    #    Real, but surfaced informationally — NEVER counted, NEVER batched.
    #  - dedicated_flow: applied from its OWN menu entry, not the generic
    #    "Update All" batch (MeshForge self-update via 'Update MeshForge').
    out_of_band: bool = False
    dedicated_flow: bool = False

    @property
    def actionable(self) -> bool:
        """True iff this update can be applied AND verified by the generic
        'Update All' batch — i.e. exactly what the status-badge count promises.

        Held (deliberate pin), out-of-band (firmware), and dedicated-flow
        (MeshForge self-update) updates are all real but are NOT applied by
        Update All, so counting them is the nag. Badge == actionable-count ==
        the Update All set, by construction: badge 0 iff Update All is a no-op.
        """
        return bool(
            self.update_available
            and not self.held
            and not self.out_of_band
            and not self.dedicated_flow
            and self.update_command
        )


def parse_version(version_str: str) -> tuple:
    """Parse version string into comparable tuple"""
    if not version_str:
        return (0, 0, 0)

    # Remove common prefixes
    version_str = version_str.lstrip('v').strip()

    # Handle versions like "2.5.6.abcd123"
    match = re.match(r'(\d+)\.(\d+)\.(\d+)', version_str)
    if match:
        return tuple(int(x) for x in match.groups())

    return (0, 0, 0)


def compare_versions(installed: str, latest: str) -> bool:
    """Check if latest version is newer than installed"""
    if not installed or not latest:
        return False

    inst_tuple = parse_version(installed)
    latest_tuple = parse_version(latest)

    return latest_tuple > inst_tuple


def get_meshforge_version() -> Optional[str]:
    """Get installed MeshForge version from __version__.py"""
    try:
        # Import from the package
        if _HAS_VERSION:
            return _version_mod

        # Fallback: read the file directly
        version_file = Path(__file__).parent.parent / '__version__.py'
        if version_file.exists():
            content = version_file.read_text()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)

    except Exception as e:
        logger.debug(f"Error getting MeshForge version: {e}")

    return None


def get_latest_meshforge_version() -> Optional[str]:
    """Get latest MeshForge version from GitHub"""
    cache_key = 'meshforge_latest'

    # Check cache
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['version']

    try:
        import urllib.request
        import ssl

        ctx = ssl.create_default_context()

        # Check the __version__.py file in the main branch
        url = 'https://raw.githubusercontent.com/Nursedude/meshforge/main/src/__version__.py'
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshForge'})

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            content = response.read().decode()
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                version = match.group(1)

                # Cache result
                _version_cache[cache_key] = {
                    'version': version,
                    'timestamp': datetime.now()
                }

                return version

    except Exception as e:
        logger.debug(f"Error getting latest MeshForge version: {e}")

    return None


def get_meshtasticd_version() -> Optional[str]:
    """Get installed meshtasticd version"""
    try:
        # Try dpkg first (Debian/Ubuntu)
        result = subprocess.run(
            ['dpkg', '-s', 'meshtasticd'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()

        # Try meshtasticd --version
        result = subprocess.run(
            ['meshtasticd', '--version'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Parse version from output
            match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)

    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"Error getting meshtasticd version: {e}")

    return None


def get_meshforge_git_snapshot():
    """git-layer truth for the MeshForge checkout (patchable seam).

    Cached for the module TTL — check_all_versions runs on TUI startup and
    in quick actions, and a `git fetch` per call would hammer the network.
    Returns None when the git layer errors out, so the caller falls back to
    the version-string compare rather than trusting a half-read state.
    """
    cache_key = 'meshforge_git_state'
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['state']
    try:
        from updates.meshforge_git import get_meshforge_git_state
        state = get_meshforge_git_state(fetch=True)
    except Exception as e:
        logger.debug(f"git state read failed: {e}")
        return None
    _version_cache[cache_key] = {'state': state, 'timestamp': datetime.now()}
    return state


def get_meshtasticd_apt_snapshot():
    """apt-layer truth for meshtasticd (installed/candidate/held), no simulation.

    Thin, patchable seam over ``updates.meshtasticd_apt`` — the check-all path
    wants the cheap state read; the update FLOW runs its own dry-run
    simulation. Returns None when the apt layer itself errors out, so the
    caller can fall back rather than trust a half-read state.
    """
    try:
        from updates.meshtasticd_apt import get_meshtasticd_apt_state
        return get_meshtasticd_apt_state(simulate=False)
    except Exception as e:
        logger.debug(f"apt state read failed: {e}")
        return None


def get_meshtastic_cli_version() -> Optional[str]:
    """Get installed meshtastic CLI version"""
    try:
        # Find meshtastic CLI using centralized function
        cli_path = find_meshtastic_cli()

        if not cli_path:
            return None

        # Get version
        result = subprocess.run(
            [cli_path, '--version'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Parse version - format is usually "meshtastic 2.3.4"
            match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)

    except Exception as e:
        logger.debug(f"Error getting meshtastic CLI version: {e}")

    return None


def get_meshforge_venv_dir() -> Optional[Path]:
    """Return MeshForge's venv dir IFF the updater installs into it.

    SINGLE SOURCE OF TRUTH for "which python does MeshForge install into" —
    consumed by BOTH the version reader (`get_meshtastic_lib_version`) and the
    writer (`_pip_install_meshtastic`). The two MUST resolve the same target:
    if the reader looks at the TUI's own interpreter (system python, root under
    sudo) while the writer installs into the venv the services actually run, an
    upgrade can succeed yet the "update available" flag never clears — the
    read/write split (feedback_version_env_rigor).

    The venv is used when `venv/bin/python` exists and there is no `.no-venv`
    opt-out marker — identical to the writer's gate.
    """
    repo_root = Path(__file__).resolve().parents[2]
    venv_dir = repo_root / 'venv'
    if (venv_dir / 'bin' / 'python').exists() and not (repo_root / '.no-venv').exists():
        return venv_dir
    return None


def get_meshtastic_lib_version() -> Optional[str]:
    """Get the meshtastic library version FROM THE PYTHON THE UPDATER WRITES TO.

    When MeshForge uses a venv, read meshtastic from THAT interpreter (the one
    the gateway/map services run and that `_pip_install_meshtastic` upgrades),
    not from whatever python is running this checker. Otherwise the reported
    version reflects the wrong site-packages and an upgrade no-ops against a
    flag that never clears (the read/write split, feedback_version_env_rigor).
    With no venv the updater uses system `pip3`, which targets this same
    interpreter, so reading in-process is correct.
    """
    venv_dir = get_meshforge_venv_dir()
    if venv_dir is not None:
        venv_python = venv_dir / 'bin' / 'python'
        try:
            result = subprocess.run(
                [str(venv_python), '-c',
                 "import importlib.metadata as m; print(m.version('meshtastic'))"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            # venv is the install target but meshtastic isn't importable there:
            # report not-installed (the install path writes into this same venv).
            return None
        except subprocess.TimeoutExpired:
            logger.debug("venv meshtastic version check timed out")
            return None
        except Exception as e:
            logger.debug(f"Error reading venv meshtastic version: {e}")
            return None

    # No venv → the updater uses system pip3 (--break-system-packages), which
    # targets this interpreter; read it directly.
    try:
        import importlib.metadata
        return importlib.metadata.version('meshtastic')
    except Exception:
        pass

    # Fallback: check via pip show
    try:
        import sys
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'show', 'meshtastic'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"Error getting meshtastic library version: {e}")

    return None


def get_node_firmware_version() -> Optional[str]:
    """Get firmware version from connected node via meshtastic CLI"""
    try:
        import socket

        # Quick check if port is available
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            if sock.connect_ex(('localhost', 4403)) != 0:
                return None
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        # Find CLI using centralized function
        cli_path = find_meshtastic_cli()

        if not cli_path:
            return None

        # RF egress chokepoint — --info is read-only today, but every CLI
        # invocation goes through the guard so a transmitting flag added
        # here later is covered the day it is written (leg-b audit
        # 2026-08-10: this was the one CLI site in the tree without it).
        from utils.tx_guard import assert_cli_args_allowed
        assert_cli_args_allowed(['--info'], 'localhost',
                                detail="version_checker firmware query")

        result = subprocess.run(
            [cli_path, '--host', 'localhost', '--info'],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            # Parse firmware version from JSON
            match = re.search(r'"firmwareVersion":\s*"([^"]+)"', result.stdout)
            if match:
                return match.group(1)

    except Exception as e:
        logger.debug(f"Error getting firmware version: {e}")

    return None


def get_latest_meshtasticd_version() -> Optional[str]:
    """Get latest meshtasticd version from GitHub releases"""
    cache_key = 'meshtasticd_latest'

    # Check cache
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['version']

    try:
        import urllib.request
        import ssl

        # Create SSL context
        ctx = ssl.create_default_context()

        url = 'https://api.github.com/repos/meshtastic/firmware/releases/latest'
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshForge'})

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            data = json.loads(response.read().decode())
            version = data.get('tag_name', '').lstrip('v')

            # Cache result
            _version_cache[cache_key] = {
                'version': version,
                'timestamp': datetime.now()
            }

            return version

    except Exception as e:
        logger.debug(f"Error getting latest meshtasticd version: {e}")

    return None


def get_latest_meshtastic_cli_version() -> Optional[str]:
    """Get latest meshtastic CLI version from PyPI"""
    cache_key = 'meshtastic_cli_latest'

    # Check cache
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['version']

    try:
        import urllib.request
        import ssl

        ctx = ssl.create_default_context()

        url = 'https://pypi.org/pypi/meshtastic/json'
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshForge'})

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            data = json.loads(response.read().decode())
            version = data.get('info', {}).get('version')

            # Cache result
            _version_cache[cache_key] = {
                'version': version,
                'timestamp': datetime.now()
            }

            return version

    except Exception as e:
        logger.debug(f"Error getting latest CLI version: {e}")

    return None


def get_latest_firmware_version() -> Optional[str]:
    """Get latest Meshtastic firmware version from GitHub"""
    # Same as meshtasticd for now - they share the firmware repo
    return get_latest_meshtasticd_version()


def _apply_fleet_floor(info: VersionInfo, pypi_latest: Optional[str]) -> None:
    """Gate ``info.update_available`` on the REVIEWED fleet floor, not PyPI-latest.

    For a fleet pinned to a reviewed baseline (``requirements/core.txt:
    meshtastic>=X``), a box is a real laggard only when it is installed BELOW
    that floor. Comparing against raw PyPI-latest produced phantom "update
    available" the moment PyPI moved past the reviewed baseline — the recurring
    class the operator called out 2026-06-17 (feedback_version_env_rigor). The
    floor is a LOCAL file read, so the gating decision also works offline; the
    PyPI version is kept only as informational context (a reviewed bump is
    possible, but it is not an automatic update).

    Honest failure mode: if the floor can't be read we refuse to claim either
    "up to date" OR a phantom update — we leave ``update_available`` False and
    record the blindness in ``error`` so it is surfaced, not silently healthy.
    """
    info.pypi_latest = pypi_latest
    floor = read_floor('meshtastic')
    if not floor:
        info.update_available = False
        note = 'fleet baseline (requirements/core.txt) unavailable'
        info.error = f"{info.error}; {note}" if info.error else note
        return
    info.fleet_floor = floor
    # The comparison target shown to the operator IS the reviewed floor.
    info.latest = floor
    info.update_available = bool(info.installed and version_below(info.installed, floor))


def check_all_versions() -> Dict[str, VersionInfo]:
    """Check all component versions and return status"""
    results = {}

    # MeshForge itself — judged by GIT truth (HEAD vs origin/main), not
    # release version strings: this repo ships continuously by commit, and a
    # version string that only moves on releases NEVER showed an update — the
    # operator could not successfully update from the TUI (2026-07-10
    # follow-up). Version-string compare remains the non-git fallback.
    meshforge = VersionInfo(name='MeshForge')
    meshforge.installed = get_meshforge_version()
    git_state = get_meshforge_git_snapshot()
    if git_state is not None and git_state.is_git_repo and git_state.head:
        head = (git_state.head or '')[:8]
        remote = (git_state.remote_head or '')[:8]
        meshforge.installed = f"{meshforge.installed or '?'} @ {head}"
        meshforge.latest = f"{meshforge.installed.split(' @ ')[0]} @ {remote or '?'}"
        meshforge.update_available = git_state.update_available
        if git_state.fetch_ok is False:
            # Offline: judged against the last-known remote ref — say so
            # instead of reading as verified-current (unobservable ≠ healthy).
            meshforge.notes = 'remote unverified (git fetch failed — offline?)'
        elif git_state.behind:
            meshforge.notes = f'{git_state.behind} commit(s) behind origin/main'
        elif git_state.ahead:
            meshforge.notes = f'{git_state.ahead} commit(s) AHEAD of origin/main (dev box)'
    else:
        meshforge.latest = get_latest_meshforge_version()
        if meshforge.installed and meshforge.latest:
            meshforge.update_available = compare_versions(meshforge.installed, meshforge.latest)
    meshforge.update_command = 'meshforge-update'  # Special command handled by TUI
    meshforge.dedicated_flow = True  # applied via the 'Update MeshForge' menu, not Update All
    results['meshforge'] = meshforge

    # meshtasticd — judged against the apt CANDIDATE (what apt can actually
    # install on this box), not GitHub firmware releases: the GitHub tag says
    # nothing about what the configured OBS repos serve, and comparing against
    # it produced both phantom updates and blindness to broken candidates
    # (2026-07-10 audit). GitHub remains the fallback for non-apt systems only.
    meshtasticd = VersionInfo(name='meshtasticd')
    apt_state = get_meshtasticd_apt_snapshot()
    if apt_state is not None and apt_state.apt_available and (
            apt_state.installed or apt_state.candidate):
        meshtasticd.installed = apt_state.installed
        meshtasticd.latest = apt_state.candidate
        meshtasticd.held = apt_state.held
        if apt_state.held:
            # A hold is a deliberate pin — not a laggard. Don't nag, but do
            # surface a candidate waiting behind the pin.
            meshtasticd.update_available = False
            if (apt_state.installed and apt_state.candidate
                    and apt_state.installed != apt_state.candidate):
                meshtasticd.notes = (
                    f'pinned by apt hold; candidate {apt_state.candidate} '
                    'waiting — use "Update meshtasticd" to unhold deliberately'
                )
            else:
                meshtasticd.notes = 'pinned by apt hold (deliberate)'
        else:
            meshtasticd.update_available = apt_state.update_available
        if apt_state.error:
            meshtasticd.error = apt_state.error
    else:
        meshtasticd.installed = get_meshtasticd_version()
        meshtasticd.latest = get_latest_meshtasticd_version()
        if meshtasticd.installed and meshtasticd.latest:
            meshtasticd.update_available = compare_versions(meshtasticd.installed, meshtasticd.latest)
    meshtasticd.update_command = 'sudo apt-get install --only-upgrade -y meshtasticd'
    results['meshtasticd'] = meshtasticd

    # Meshtastic CLI — gated against the fleet floor, not PyPI-latest (same
    # PyPI package as the library; both phantom-flagged 2026-06-17).
    cli = VersionInfo(name='Meshtastic CLI')
    cli.installed = get_meshtastic_cli_version()
    _apply_fleet_floor(cli, get_latest_meshtastic_cli_version())
    # Display/manual-copy form: the writer targets the reviewed floor, never
    # PyPI-latest (`pipx upgrade` overshoots the pin the moment PyPI moves).
    if cli.fleet_floor:
        cli.update_command = f'pipx install --force meshtastic=={cli.fleet_floor}'
        cli.install_command = f'pipx install meshtastic=={cli.fleet_floor}'
    else:
        cli.update_command = 'pipx install --force meshtastic'
        cli.install_command = 'pipx install meshtastic'
    results['cli'] = cli

    # Meshtastic Python Library (protobuf definitions) — fleet-floor gated.
    lib = VersionInfo(name='Meshtastic Library')
    lib.installed = get_meshtastic_lib_version()
    _apply_fleet_floor(lib, get_latest_meshtastic_cli_version())  # same PyPI pkg (cached)
    # --break-system-packages: Debian/RPi mark system Python externally-managed
    # (PEP 668), so a bare `pip3 install` fails. The flag is a no-op where no
    # EXTERNALLY-MANAGED marker exists, so it's safe to show generically. The
    # TUI's Update flow routes execution through _pip_install_meshtastic (which
    # also handles venv + the rnsd dual-install, #24); this string is the
    # display/manual-copy form.
    _lib_spec = (f'meshtastic=={lib.fleet_floor}' if lib.fleet_floor
                 else 'meshtastic')
    lib.update_command = f'pip3 install --break-system-packages --upgrade {_lib_spec}'
    lib.install_command = f'pip3 install --break-system-packages {_lib_spec}'
    results['meshtastic_lib'] = lib

    # Node Firmware
    firmware = VersionInfo(name='Node Firmware')
    firmware.installed = get_node_firmware_version()
    firmware.latest = get_latest_firmware_version()
    if firmware.installed and firmware.latest:
        firmware.update_available = compare_versions(firmware.installed, firmware.latest)
    firmware.update_command = 'Use Meshtastic Web Flasher or meshtastic-flasher'
    firmware.out_of_band = True  # flashed out-of-band; informational, never batched/counted
    results['firmware'] = firmware

    return results


def get_version_summary() -> Dict[str, Any]:
    """Get version summary for API/UI consumption"""
    versions = check_all_versions()

    summary = {
        'components': [],
        'updates_available': 0,
        'checked_at': datetime.now().isoformat(),
    }

    for key, info in versions.items():
        component = {
            'id': key,
            'name': info.name,
            'installed': info.installed or 'Not installed',
            'latest': info.latest or 'Unknown',
            'update_available': info.update_available,
            'update_command': info.update_command,
            # Floor-gated components expose the reviewed baseline they're judged
            # against (and the PyPI version for a possible reviewed bump), so a
            # consumer can distinguish "below the fleet floor" from "PyPI moved".
            'fleet_floor': info.fleet_floor,
            'pypi_latest': info.pypi_latest,
            'held': info.held,
            'out_of_band': info.out_of_band,
            'dedicated_flow': info.dedicated_flow,
            'actionable': info.actionable,
            'notes': info.notes,
        }
        summary['components'].append(component)

        # Count only what Update All can actually apply — a real-but-out-of-band
        # (firmware) or dedicated-flow (meshforge) update is surfaced in its own
        # component row, never inflated into this scalar (the 2026-07-16 nag).
        if info.actionable:
            summary['updates_available'] += 1

    return summary


if __name__ == '__main__':
    """Test version checker"""
    import pprint

    print("Checking versions...")
    summary = get_version_summary()
    pprint.pprint(summary)
