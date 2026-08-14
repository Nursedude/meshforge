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

from utils.service_check import check_process_running

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
        """Install NomadNet in-app via the canonical installer.

        Delegates to ``scripts/install_nomadnet.sh`` — the env-rigorous SSOT
        for the NomadNet install layout (pipx-first, PEP-668-safe, resolves the
        real user under sudo). We do NOT hand-roll pipx here: a second install
        path drifts from the canonical one (honest_failure_modes #5) and is the
        exact update/env failure class the operator flagged
        ([[feedback_version_env_rigor]]). On failure we offer an in-app retry —
        the In-Domain Principle: never punt the operator to a shell.
        """
        if self._is_nomadnet_installed():
            self.ctx.dialog.msgbox(
                "Already Installed",
                "NomadNet is already installed.\n\n"
                "To refresh or repair it, use:\n"
                "  Service Control > Reinstall NomadNet (idempotent)",
            )
            return

        if not self.ctx.dialog.yesno(
            "Install NomadNet",
            "Install NomadNet RNS client?\n\n"
            "Runs the canonical installer (pipx, isolated env):\n"
            "  scripts/install_nomadnet.sh\n\n"
            "NomadNet pulls in RNS and LXMF automatically and provides:\n"
            "  - Text UI with micron page browser\n"
            "  - LXMF encrypted messaging\n"
            "  - Node hosting and page serving\n\n"
            "Source: github.com/markqvist/NomadNet\n\n"
            "Install now?",
        ):
            return

        self.ctx.dialog.infobox(
            "Installing NomadNet",
            "Running the canonical installer — this may take a minute...",
        )
        ok, msg = self._run_canonical_installer()
        if ok:
            self.ctx.dialog.msgbox("NomadNet Installed", "✓ " + msg)
            return

        # In-Domain: a failed install offers a one-keystroke in-app retry,
        # never a shell instruction. The captured installer output (msg) is
        # the diagnostic the operator reads to decide.
        from remediation import RemediationAction, propose_remediation
        propose_remediation(
            self.ctx,
            "NomadNet install incomplete",
            msg,
            [RemediationAction(
                label="Retry installation",
                description="re-run scripts/install_nomadnet.sh in-app",
                apply=self._run_canonical_installer,
            )],
        )

    def _run_canonical_installer(self):
        """Run scripts/install_nomadnet.sh captured, in-app. Returns (ok, msg).

        The canonical installer is the single source of truth for the NomadNet
        install layout; delegating to it keeps the TUI and bash install paths
        identical instead of drifting (honest_failure_modes #5).

        FAILS LOUD: a non-zero exit returns ``(False, ...)`` carrying the
        captured output, and even a zero exit is downgraded to failure when the
        binary did not actually land on PATH — a failed install must never
        report success (Issue #24 / [[feedback_version_env_rigor]] point 5).
        """
        installer = (
            Path(__file__).resolve().parents[3]
            / 'scripts' / 'install_nomadnet.sh'
        )
        if not installer.is_file():
            return (
                False,
                f"Canonical installer not found at {installer}.\n"
                "Update MeshForge and try again.",
            )
        from ._service_ops_common import run_script_captured
        rc, tail = run_script_captured(['bash', str(installer)],
                                       timeout=600, tail=1800)
        if rc == -1:
            return (False, f"Installer failed: {tail}")
        if rc != 0:
            return (
                False,
                f"Installer exited {rc}.\n\n{tail or '(no output)'}",
            )
        # A zero exit is necessary but not sufficient — verify the consumer can
        # actually see the binary (the #24 success-but-broken guard).
        if not self._is_nomadnet_installed():
            return (
                False,
                "Installer reported success but nomadnet is not on PATH.\n\n"
                + (tail or '(no output)'),
            )
        return (True, "NomadNet installed via the canonical installer.\n\n" + tail)

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
            "Upgrade Complete" if rns_upgraded else "Upgrade Incomplete",
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

    def _is_nomadnet_running(self) -> bool:
        """Check if NomadNet process is running.

        Uses centralized service_check module when available, with fallback
        to direct pgrep for custom filtering.
        """
        # Try unified check first (faster and standardized)
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
        """Find NomadNet binary, restricted to the canonical pipx layout.

        Issue #46 follow-up: we used to fall back to ``shutil.which`` and
        any ``~/.local/bin/nomadnet``; pip-user / apt installs would
        satisfy this and silently produce a unit that referenced a binary
        whose adjacent python3 didn't exist. The canonical installer
        (``scripts/install_nomadnet.sh``) standardizes on pipx, so the
        canonical layout is:

            ~/.local/bin/nomadnet -> <pipx-venv>/bin/nomadnet
            <pipx-venv>/bin/python3   (used to run the wrapper)

        If that layout isn't present we refuse and tell the operator to
        run the canonical installer. No silent degradation.
        """
        user_home = get_real_user_home()
        candidate = user_home / '.local' / 'bin' / 'nomadnet'
        if candidate.exists():
            try:
                resolved = candidate.resolve()
                if (resolved.parent / 'python3').exists():
                    return str(candidate)
            except OSError:
                pass

        self.ctx.dialog.msgbox(
            "NomadNet not canonically installed",
            "NomadNet is not installed via pipx in the canonical layout.\n\n"
            "Install/repair with:\n"
            "  bash /opt/meshforge/scripts/install_nomadnet.sh\n\n"
            "Or via TUI:\n"
            "  NomadNet > Service Control > Reinstall NomadNet (idempotent)",
        )
        return None

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

    # Bump alongside the ``Version:`` marker in
    # ``templates/python/nomadnet_wrapper.py``. Bumping here forces every
    # box's wrapper to be regenerated on the next install/refresh.
    _WRAPPER_VERSION = "8"

    # Path to the shared wrapper template — same source of truth used by
    # ``scripts/install_nomadnet.sh`` so bash and Python install paths
    # converge on identical wrapper bytes.
    _WRAPPER_TEMPLATE = (
        Path(__file__).resolve().parents[3]
        / "templates" / "python" / "nomadnet_wrapper.py"
    )

    def _create_nomadnet_wrapper(self) -> Optional[Path]:
        """Copy the canonical wrapper template into the user config dir.

        The wrapper guards two failure modes:

        1. **Transient RPC failure**: rnsd briefly unreachable. Swallow
           ConnectionRefusedError / BrokenPipeError / OSError on
           ``get_interface_stats()``; return empty stats so the UI renders.
        2. **Authkey mismatch (Issue #41/#46)**: refuse-loud on the first
           AuthenticationError, exit 87. The unit's StartLimitBurst caps
           retries so the journal carries one clear diagnostic line.

        Returns the wrapper path, or None on failure.
        """
        if not self._WRAPPER_TEMPLATE.is_file():
            logger.warning(
                "Wrapper template missing: %s — has the repo been installed correctly?",
                self._WRAPPER_TEMPLATE,
            )
            return None

        user_home = get_real_user_home()
        wrapper_dir = user_home / '.config' / 'meshforge'
        wrapper_path = wrapper_dir / 'nomadnet_wrapper.py'

        try:
            template_bytes = self._WRAPPER_TEMPLATE.read_bytes()
        except OSError as e:
            logger.warning("Failed to read wrapper template: %s", e)
            return None

        # Idempotent: skip the write if bytes already match.
        if wrapper_path.exists():
            try:
                if wrapper_path.read_bytes() == template_bytes:
                    return wrapper_path
            except OSError:
                pass

        try:
            wrapper_dir.mkdir(parents=True, exist_ok=True)
            wrapper_path.write_bytes(template_bytes)
            logger.debug("Created NomadNet wrapper at %s", wrapper_path)

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

    def _get_wrapper_command(self, nn_path: str, nn_args: list) -> Optional[list]:
        """Build the canonical wrapped-launch argv.

        Issue #46 closure: refuses to fall back to the bare ``nn_path``
        when the pipx venv python is missing. Silent fallback was the
        wrapper-bypass hole — it produced a unit that booted nomadnet
        with no rpc_key handling, then quietly crashed under
        AuthenticationError. Return None so callers fail loud.
        """
        venv_python = self._get_nomadnet_venv_python(nn_path)
        if not venv_python:
            logger.warning(
                "Refusing to build launch command without pipx venv: %s",
                nn_path,
            )
            return None

        wrapper_path = self._create_nomadnet_wrapper()
        if not wrapper_path:
            return None

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
            "Reinstall later from Service Control > Reinstall NomadNet.\n\n"
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
            if os.geteuid() != 0:
                print("Relaunch MeshForge in Admin mode (sudo) to force-stop it.")
            else:
                print("It resisted SIGKILL — it may be a wedged or defunct process.")
        else:
            print("NomadNet stopped.")

        user_home = get_real_user_home()
        print(f"\nConfig remains at: {user_home}/.nomadnetwork/")
        print("Reinstall from Service Control > Reinstall NomadNet.")

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
                            error_hints.append("Fix: start rnsd from Service Control")
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
                                error_hints.append("Fix: Reinstall NomadNet, then restart rnsd (both in Service Control)")
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
                        error_hints.append("Then restart rnsd from Service Control")
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
                        error_hints.append("Reinstall via Service Control > Reinstall NomadNet")
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
                    error_hints.append("Then restart rnsd from Service Control")
                elif 'status=255' in journal_text or 'exception' in journal_text:
                    error_hints.append("rnsd is crashing (exit code 255)")
                    error_hints.append("View rnsd logs in-app (RNS > Diagnostics)")
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
            print(f"  View NomadNet logs from the NomadNet menu.")

        return connection_refused
