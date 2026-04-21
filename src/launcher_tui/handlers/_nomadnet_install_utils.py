"""NomadNet install utilities and helper methods.

Provides install, upgrade, binary discovery, and wrapper creation
for NomadNet.  Extracted from nomadnet.py for file size compliance
(CLAUDE.md #6).

Cross-mixin calls:
    _get_nomadnet_venv_python()  — defined in NomadNetRNSChecksMixin
    _upgrade_nomadnet()          — called FROM NomadNetRNSChecksMixin

Both mixins are composed on NomadNetHandler, so all self.* calls
resolve via MRO.
"""

import os
import shutil
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

from backend import clear_screen

from utils.paths import get_real_user_home

from utils.safe_import import safe_import

check_process_running, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running'
)

logger = logging.getLogger(__name__)


class NomadNetInstallUtilsMixin:
    """Mixin providing NomadNet install/upgrade utilities.

    Expects the host class to provide:
        self.ctx.dialog          — DialogBackend for TUI dialogs
        self.ctx.wait_for_enter  — block until user presses Enter
        self._get_nomadnet_venv_python(nn_path) -> Optional[str]
            (from NomadNetRNSChecksMixin)
    """

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def _install_nomadnet(self):
        """Install NomadNet via pipx (isolated environment)."""
        if self._is_nomadnet_installed():
            self.ctx.dialog.msgbox("Already Installed", "NomadNet is already installed.")
            return

        if not self.ctx.dialog.yesno(
            "Install NomadNet",
            "Install NomadNet RNS client?\n\n"
            "This will run:\n"
            "  pipx install nomadnet\n\n"
            "NomadNet pulls in RNS and LXMF automatically.\n\n"
            "It provides:\n"
            "  - Text UI with micron page browser\n"
            "  - LXMF encrypted messaging\n"
            "  - Node hosting and page serving\n"
            "  - Network announcement/discovery\n\n"
            "Source: github.com/markqvist/NomadNet\n\n"
            "Install now?",
        ):
            return

        clear_screen()
        print("=== Installing NomadNet ===\n")

        # Determine if we should install as a different user (when running via sudo)
        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        try:
            # Ensure pipx is available (this needs root for apt)
            if not shutil.which('pipx'):
                print("Installing pipx...\n")
                result = subprocess.run(
                    ['apt-get', 'install', '-y', 'pipx'],
                    timeout=60
                )
                if result.returncode != 0:
                    print("\nFailed to install pipx.")
                    print("Try manually: sudo apt install pipx")
                    self.ctx.wait_for_enter()
                    return

            # Build pipx commands - run as real user if we're under sudo
            def run_pipx_cmd(args, timeout_sec=300):
                """Run pipx command, as real user if running via sudo."""
                if run_as_user:
                    # Run as real user with login shell (-i) to set HOME correctly
                    cmd = ['sudo', '-i', '-u', run_as_user] + args
                else:
                    cmd = args
                return subprocess.run(cmd, timeout=timeout_sec)

            # Ensure pipx bin dir is in PATH for this session
            print("Ensuring pipx paths...\n")
            run_pipx_cmd(['pipx', 'ensurepath'], timeout_sec=15)

            # Add common pipx bin dirs to current process PATH
            for bindir in [
                get_real_user_home() / '.local' / 'bin',
                Path('/root/.local/bin'),
                Path('/usr/local/bin'),
            ]:
                if bindir.is_dir() and str(bindir) not in os.environ.get('PATH', ''):
                    os.environ['PATH'] = f"{bindir}:{os.environ.get('PATH', '')}"

            # Install nomadnet via pipx (live output)
            if run_as_user:
                print(f"\nInstalling NomadNet via pipx (as {run_as_user})...\n")
            else:
                print("\nInstalling NomadNet via pipx...\n")
            result = run_pipx_cmd(['pipx', 'install', 'nomadnet'])

            if result.returncode == 0:
                print("\nInstallation complete.")
                if self._is_nomadnet_installed():
                    nn_path = shutil.which('nomadnet')
                    if nn_path:
                        print(f"NomadNet installed at: {nn_path}")
                    else:
                        # Check user's local bin
                        user_bin = get_real_user_home() / '.local' / 'bin' / 'nomadnet'
                        if user_bin.exists():
                            print(f"NomadNet installed at: {user_bin}")

                    # Configure NomadNet for shared instance mode (use rnsd)
                    self._setup_nomadnet_shared_instance(run_as_user)
                else:
                    print("\nnomadnet not found in PATH.")
                    print("You may need to log out and back in,")
                    print("or run: eval \"$(pipx ensurepath)\"")
            else:
                print(f"\nInstallation failed (exit code {result.returncode}).")
                print("Try manually: pipx install nomadnet")
        except FileNotFoundError:
            print("pipx not found.")
            print("Try: sudo apt install pipx && pipx install nomadnet")
        except KeyboardInterrupt:
            print("\n\nInstallation cancelled.")
        except subprocess.TimeoutExpired:
            print("\nInstallation timed out. Check your internet connection.")
            print("Try manually: pipx install nomadnet")
        except Exception as e:
            print(f"\nInstallation error: {e}")
            print("Try manually: pipx install nomadnet")

        try:
            self.ctx.wait_for_enter()
        except (EOFError, KeyboardInterrupt):
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_pipx(self) -> str:
        """Find pipx binary, checking PATH and common locations.

        pipx is often installed to ~/.local/bin which may not be in
        the current shell's PATH (especially under sudo).
        Returns the path string, or None if not found.
        """
        pipx_path = shutil.which('pipx')
        if pipx_path:
            return pipx_path
        # Check common locations
        for candidate in [
            get_real_user_home() / '.local' / 'bin' / 'pipx',
            Path('/usr/bin/pipx'),
            Path('/usr/local/bin/pipx'),
        ]:
            if candidate.exists():
                return str(candidate)
        return None

    def _upgrade_nomadnet(self) -> bool:
        """Upgrade NomadNet and its RNS dependency to fix version mismatches.

        Strategy:
        1. pipx upgrade nomadnet (upgrades NomadNet + deps)
        2. If already at latest, also upgrade RNS inside the venv
           (pipx runpip nomadnet -- install --upgrade rns)
        3. Show version comparison between venv RNS and system RNS

        Returns True if upgrade succeeded, False otherwise.
        """
        pipx_path = self._find_pipx()
        if not pipx_path:
            self.ctx.dialog.msgbox(
                "pipx Not Found",
                "Cannot find pipx to upgrade NomadNet.\n\n"
                "Install pipx first:\n"
                "  sudo apt install pipx\n\n"
                "Then upgrade:\n"
                "  pipx upgrade nomadnet",
            )
            return False

        sudo_user = os.environ.get('SUDO_USER')

        def _run_pipx(args, timeout_sec=120):
            """Run pipx command as real user."""
            if sudo_user and sudo_user != 'root':
                cmd = ['sudo', '-H', '-u', sudo_user, pipx_path] + args
            else:
                cmd = [pipx_path] + args
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_sec,
            )

        # Step 1: Show RNS version comparison for diagnostics
        self.ctx.dialog.infobox(
            "Checking Versions",
            "Comparing RNS versions (system vs NomadNet venv)...",
        )
        versions = self._get_rns_version_info(pipx_path, sudo_user)

        # Step 2: Upgrade NomadNet package
        self.ctx.dialog.infobox(
            "Upgrading NomadNet",
            "Running pipx upgrade nomadnet...",
        )
        try:
            result = _run_pipx(['upgrade', 'nomadnet'])
            already_latest = 'already at latest' in (result.stdout + result.stderr).lower()
        except (subprocess.SubprocessError, OSError) as e:
            self.ctx.dialog.msgbox("Error", f"pipx upgrade failed: {e}")
            return False

        # Step 3: Also upgrade RNS inside the venv
        # pipx upgrade only upgrades the package itself; if NomadNet
        # pins an older RNS, the venv RNS stays stale.
        self.ctx.dialog.infobox(
            "Upgrading RNS",
            "Upgrading RNS library inside NomadNet venv...",
        )
        try:
            rns_result = _run_pipx(
                ['runpip', 'nomadnet', '--', 'install', '--upgrade', 'rns'],
            )
            rns_output = (rns_result.stdout + rns_result.stderr).strip()
            rns_upgraded = rns_result.returncode == 0
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("Failed to upgrade RNS in venv: %s", e)
            rns_output = str(e)
            rns_upgraded = False

        # Step 4: Show results
        new_versions = self._get_rns_version_info(pipx_path, sudo_user)
        summary_lines = []
        if versions:
            summary_lines.append(f"Before: {versions}")
        if new_versions:
            summary_lines.append(f"After:  {new_versions}")
        if rns_upgraded:
            summary_lines.append("\nRNS upgraded in NomadNet venv.")
        else:
            summary_lines.append(f"\nRNS upgrade issue:\n{rns_output[:150]}")

        self.ctx.dialog.msgbox(
            "Upgrade Complete",
            "\n".join(summary_lines),
        )
        return rns_upgraded

    def _get_rns_version_info(self, pipx_path: str, sudo_user: str) -> str:
        """Get RNS version comparison: system vs NomadNet venv.

        Returns a short summary string, or empty string on failure.
        """
        sys_ver = ''
        venv_ver = ''

        # System RNS version
        try:
            r = subprocess.run(
                ['pip3', 'show', 'rns'],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                if line.startswith('Version:'):
                    sys_ver = line.split(':', 1)[1].strip()
                    break
        except (subprocess.SubprocessError, OSError):
            pass

        # Venv RNS version
        try:
            if sudo_user and sudo_user != 'root':
                cmd = ['sudo', '-H', '-u', sudo_user, pipx_path,
                       'runpip', 'nomadnet', '--', 'show', 'rns']
            else:
                cmd = [pipx_path, 'runpip', 'nomadnet', '--', 'show', 'rns']
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                if line.startswith('Version:'):
                    venv_ver = line.split(':', 1)[1].strip()
                    break
        except (subprocess.SubprocessError, OSError):
            pass

        if sys_ver or venv_ver:
            match = "MATCH" if sys_ver == venv_ver else "MISMATCH"
            return f"system={sys_ver or '?'} venv={venv_ver or '?'} ({match})"
        return ''

    def _is_nomadnet_installed(self) -> bool:
        """Check if NomadNet is installed."""
        if shutil.which('nomadnet'):
            return True
        # Check user local bin
        user_home = get_real_user_home()
        candidate = user_home / '.local' / 'bin' / 'nomadnet'
        return candidate.exists()

    def _setup_nomadnet_shared_instance(self, run_as_user: str = None):
        """Post-install message for NomadNet.

        NomadNet creates its own complete default config on first run.
        We don't create configs - let NomadNet use its defaults.
        """
        user_home = get_real_user_home()
        config_file = user_home / '.nomadnetwork' / 'config'

        if config_file.exists():
            print(f"\nNomadNet config exists: {config_file}")
        else:
            print("\nNomadNet will create its default config on first run.")

        print("\nNomadNet uses the shared RNS instance from rnsd by default.")
        print("Config location: ~/.nomadnetwork/config")

    def _is_nomadnet_running(self) -> bool:
        """Check if NomadNet process is running.

        Uses centralized service_check module when available, with fallback
        to direct pgrep for custom filtering.
        """
        # Try unified check first (faster and standardized)
        if _HAS_SERVICE_CHECK:
            if check_process_running('nomadnet'):
                return True

        # Fallback to direct pgrep with custom filtering
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'bin/nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            # Filter out false positives (our own grep, etc.)
            if result.returncode == 0 and result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    if pid.strip() and pid.strip() != str(os.getpid()):
                        return True
            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("NomadNet running check failed: %s", e)
            return False

    def _find_nomadnet_binary(self) -> str:
        """Find NomadNet binary path, or show error and return None."""
        nn_path = shutil.which('nomadnet')
        if not nn_path:
            user_home = get_real_user_home()
            candidate = user_home / '.local' / 'bin' / 'nomadnet'
            if candidate.exists():
                nn_path = str(candidate)

        if not nn_path:
            self.ctx.dialog.msgbox(
                "Not Installed",
                "NomadNet is not installed.\n\n"
                "Install with: pipx install nomadnet\n"
                "Or use the Install option from this menu.",
            )
            return None
        return nn_path

    def _get_nomadnet_config_path(self):
        """Find the NomadNet config file.

        Mirrors NomadNet's own resolution order:
          /etc/nomadnetwork/config  ->
          ~/.config/nomadnetwork/config  ->
          ~/.nomadnetwork/config
        """
        user_home = get_real_user_home()

        candidates = [
            Path('/etc/nomadnetwork/config'),
            user_home / '.config' / 'nomadnetwork' / 'config',
            user_home / '.nomadnetwork' / 'config',
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        # Return the default path (even if it doesn't exist yet)
        return user_home / '.nomadnetwork' / 'config'

    # ------------------------------------------------------------------
    # NomadNet wrapper (monkey-patch broken RPC)
    # ------------------------------------------------------------------

    _WRAPPER_VERSION = "6"  # bump to force re-creation

    def _create_nomadnet_wrapper(self) -> Optional[Path]:
        """Create a wrapper script that patches get_interface_stats.

        NomadNet's TextUI.MainDisplay.__init__() calls
        RNS.Reticulum.get_interface_stats() which uses the RPC management
        socket (multiprocessing.connection). When rnsd's RPC listener is
        broken, this crashes NomadNet with ConnectionRefusedError.

        The wrapper monkey-patches get_interface_stats to catch the error
        and return an empty stats dict (graceful degradation — no stats shown).

        Returns the wrapper path, or None if creation failed.
        """
        user_home = get_real_user_home()
        wrapper_dir = user_home / '.config' / 'meshforge'
        wrapper_path = wrapper_dir / 'nomadnet_wrapper.py'

        wrapper_content = '''\
"""MeshForge NomadNet wrapper — patches RPC failures during startup.

Version: {version}

NomadNet's TextUI startup calls RNS.Reticulum.get_interface_stats(),
which opens a multiprocessing.connection.Client to rnsd's RPC socket.
When rnsd is unreachable OR when its rpc_key differs from the one our
RNS config derived, NomadNet crashes before any UI appears. This
wrapper monkey-patches get_interface_stats to swallow those failures
and return an empty stats dict so NomadNet at least starts.

Error classes caught:
  - ConnectionRefusedError / BrokenPipeError / OSError: rnsd RPC down.
  - AuthenticationError: rnsd running, but rpc_key mismatch — usually
    config-dir drift (rnsd loaded a different /etc/reticulum/config
    than NomadNet did, or ~/.reticulum vs /etc/reticulum split).
  - TypeError / KeyError: malformed stats structure from upstream.

NOT fixed by this wrapper: the underlying authkey mismatch. NomadNet
will start but interface stats stay empty. See Issue #37 in
.claude/foundations/persistent_issues.md for the diagnostic checklist.
"""
import sys
from multiprocessing.context import AuthenticationError
import RNS

_orig_get_interface_stats = RNS.Reticulum.get_interface_stats

_FALLBACK = dict(interfaces=[])

_SAFE_EXC = (
    ConnectionRefusedError,
    BrokenPipeError,
    TypeError,
    KeyError,
    OSError,
    AuthenticationError,
)


def _safe_get_interface_stats(self):
    try:
        result = _orig_get_interface_stats(self)
    except _SAFE_EXC:
        return _FALLBACK
    if not isinstance(result, dict) or 'interfaces' not in result:
        return _FALLBACK
    return result

RNS.Reticulum.get_interface_stats = _safe_get_interface_stats

from nomadnet.nomadnet import main
sys.exit(main())
'''.format(version=self._WRAPPER_VERSION)

        # Check if wrapper already exists with correct version
        version_marker = f"Version: {self._WRAPPER_VERSION}"
        if wrapper_path.exists():
            try:
                existing = wrapper_path.read_text()
                if version_marker in existing:
                    return wrapper_path
            except OSError:
                pass

        # Create/update the wrapper
        try:
            wrapper_dir.mkdir(parents=True, exist_ok=True)
            wrapper_path.write_text(wrapper_content)
            logger.debug("Created NomadNet wrapper at %s", wrapper_path)

            # Fix ownership if running under sudo
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                import pwd
                try:
                    pw = pwd.getpwnam(sudo_user)
                    os.chown(wrapper_dir, pw.pw_uid, pw.pw_gid)
                    os.chown(wrapper_path, pw.pw_uid, pw.pw_gid)
                except (KeyError, OSError) as e:
                    logger.debug("Could not chown wrapper: %s", e)

            return wrapper_path
        except OSError as e:
            logger.warning("Failed to create NomadNet wrapper: %s", e)
            return None

    def _get_wrapper_command(self, nn_path: str, nn_args: list) -> list:
        """Build launch command using wrapper if possible.

        Returns [venv_python, wrapper, ...args] if wrapper is available,
        otherwise [nn_path, ...args] as fallback.
        """
        venv_python = self._get_nomadnet_venv_python(nn_path)
        if not venv_python:
            return [nn_path] + nn_args

        wrapper_path = self._create_nomadnet_wrapper()
        if not wrapper_path:
            return [nn_path] + nn_args

        # sys.argv[0] will be the wrapper path, remaining args are
        # forwarded to NomadNet's main() via sys.argv
        return [venv_python, str(wrapper_path)] + nn_args

    def _uninstall_nomadnet(self):
        """Stop NomadNet and leave it disabled.

        Does not remove files -- just stops the process and shows how
        to reinstall later if desired.
        """
        if not self.ctx.dialog.yesno(
            "Disable NomadNet",
            "Stop NomadNet and disable it?\n\n"
            "This will:\n"
            "  - Stop NomadNet if running\n"
            "  - Leave files in place\n\n"
            "Reinstall later with: pipx install nomadnet\n\n"
            "Disable now?",
        ):
            return

        clear_screen()
        print("=== Disabling NomadNet ===\n")

        # Stop running processes
        if self._is_nomadnet_running():
            print("Stopping NomadNet...")
            try:
                subprocess.run(
                    ['pkill', '-f', 'bin/nomadnet'],
                    capture_output=True, timeout=10,
                )
                time.sleep(2)
            except (subprocess.SubprocessError, OSError):
                pass

            if self._is_nomadnet_running():
                try:
                    subprocess.run(
                        ['pkill', '-9', '-f', 'bin/nomadnet'],
                        capture_output=True, timeout=10,
                    )
                    time.sleep(1)
                except (subprocess.SubprocessError, OSError):
                    pass

        if self._is_nomadnet_running():
            print("NomadNet may still be running.")
            print("Try: sudo pkill -9 -f nomadnet")
        else:
            print("NomadNet stopped.")

        user_home = get_real_user_home()
        print(f"\nConfig remains at: {user_home}/.nomadnetwork/")
        print("Reinstall: pipx install nomadnet")

        self.ctx.wait_for_enter()

    def _diagnose_nomadnet_error(self, returncode: int, sudo_user: str = None) -> bool:
        """Analyze NomadNet failure and provide helpful diagnostics.

        Returns True if the failure was ConnectionRefusedError (caller
        can offer auto-restart), False otherwise.
        """
        print(f"NomadNet exited with error code {returncode}")
        connection_refused = False

        # Try to read the log file for clues
        user_home = get_real_user_home()
        logfile = user_home / '.nomadnetwork' / 'logfile'

        error_hints = []
        if logfile.exists():
            try:
                import collections
                with open(logfile, 'r') as f:
                    last_lines = list(
                        collections.deque(f, maxlen=50)
                    )

                # Look for known error patterns
                for line in last_lines:
                    if 'ConnectionRefusedError' in line or 'Errno 111' in line:
                        connection_refused = True
                        error_hints.append("RPC connection to rnsd refused (Errno 111)")
                        rnsd_user = self._get_rnsd_user()
                        if not rnsd_user:
                            error_hints.append("rnsd is NOT running — NomadNet cannot connect")
                            error_hints.append("Fix: sudo systemctl start rnsd")
                            error_hints.append("     Then wait a few seconds and retry")
                        else:
                            if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
                                error_hints.append(f"rnsd runs as root, NomadNet as '{sudo_user}'")
                                error_hints.append("Different users = different RNS identities")
                                error_hints.append("Fix: stop rnsd, reconfigure to run as your user")
                            else:
                                error_hints.append("rnsd is running but RPC socket refused connection")
                                error_hints.append("Possible causes:")
                                error_hints.append("  - RNS version mismatch (pipx venv vs system)")
                                error_hints.append("  - Stale auth tokens after rnsd restart")
                                error_hints.append("Verify: rnstatus")
                                error_hints.append("Fix: pipx upgrade nomadnet && sudo systemctl restart rnsd")
                        break
                    elif 'AuthenticationError' in line or 'digest sent was rejected' in line:
                        error_hints.append("RPC authentication failed between NomadNet and rnsd")
                        rnsd_user = self._get_rnsd_user()
                        if rnsd_user == 'root':
                            error_hints.append("rnsd is running as root - identities don't match")
                            error_hints.append("Fix: sudo systemctl stop rnsd")
                            error_hints.append("     Then run rnsd as your user, or reconfigure")
                        elif rnsd_user and rnsd_user != sudo_user:
                            error_hints.append(f"rnsd runs as '{rnsd_user}', you are '{sudo_user}'")
                        else:
                            error_hints.append("Check that rnsd uses the same ~/.reticulum/ identity")
                        break
                    elif 'KeyError' in line and 'textui' in line.lower():
                        error_hints.append("Config missing [textui] section")
                        error_hints.append("Delete ~/.nomadnetwork/config and restart")
                        break
                    elif 'PermissionError' in line or 'Permission denied' in line:
                        if '/etc/reticulum' in line:
                            error_hints.append("Cannot write to /etc/reticulum/ (system config)")
                            error_hints.append("This happens when rnsd was run as root first")
                            error_hints.append("Fix: sudo rm -rf /etc/reticulum")
                            error_hints.append("     (or sudo chown -R $USER /etc/reticulum)")
                        else:
                            error_hints.append("Permission denied accessing files")
                            error_hints.append(f"Check ownership: ls -la ~/.nomadnetwork/")
                        break
                    elif 'meshtastic' in line.lower() and (
                        'critical' in line.lower() or 'requires' in line.lower()
                        or 'no module' in line.lower() or 'modulenotfounderror' in line.lower()
                    ):
                        error_hints.append("rnsd cannot load the meshtastic module")
                        error_hints.append("The Meshtastic_Interface.py plugin requires meshtastic")
                        error_hints.append(
                            "Fix: sudo pip3 install --break-system-packages "
                            "--ignore-installed meshtastic"
                        )
                        error_hints.append("Then: sudo systemctl restart rnsd")
                        break
                    elif 'TypeError' in line and 'list indices' in line:
                        error_hints.append(
                            "NomadNet crash: interface stats returned wrong "
                            "type (list instead of dict)"
                        )
                        error_hints.append(
                            "The MeshForge NomadNet wrapper needs updating"
                        )
                        error_hints.append(
                            "Fix: Relaunch NomadNet from MeshForge "
                            "(wrapper auto-updates)"
                        )
                        break
                    elif 'ModuleNotFoundError' in line or 'ImportError' in line:
                        error_hints.append("Missing Python dependencies")
                        error_hints.append("Try: pipx reinstall nomadnet")
                        break
            except (OSError, PermissionError):
                pass

        # If no NomadNet-specific error found, check rnsd journal for clues.
        if not error_hints:
            try:
                journal_r = subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '20', '--no-pager', '-q'],
                    capture_output=True, text=True, timeout=5
                )
                journal_text = journal_r.stdout.lower()
                if 'meshtastic' in journal_text and (
                    'critical' in journal_text or 'module' in journal_text
                ):
                    error_hints.append("rnsd crashed because the meshtastic module is missing")
                    error_hints.append("NomadNet depends on rnsd for network access")
                    error_hints.append(
                        "Fix: sudo pip3 install --break-system-packages "
                        "--ignore-installed meshtastic"
                    )
                    error_hints.append("Then: sudo systemctl restart rnsd")
                elif 'status=255' in journal_text or 'exception' in journal_text:
                    error_hints.append("rnsd is crashing (exit code 255)")
                    error_hints.append("Check: sudo journalctl -u rnsd -n 30")
            except (subprocess.SubprocessError, OSError):
                pass

        if error_hints:
            print("\nDiagnosis:")
            for hint in error_hints:
                print(f"  - {hint}")
        else:
            print(f"\nNo known error pattern detected.")
            if logfile.exists():
                try:
                    import collections
                    with open(logfile, 'r') as f:
                        tail = list(collections.deque(f, maxlen=15))
                    if tail:
                        print(f"\n--- Last {len(tail)} lines of {logfile} ---")
                        for line in tail:
                            print(f"  {line.rstrip()}")
                        print("---")
                except OSError:
                    print(f"\nCheck logs: cat {logfile}")
            else:
                print(f"\nNo logfile found at: {logfile}")
            print(f"  journalctl --user -u nomadnet -n 50")

        return connection_refused
