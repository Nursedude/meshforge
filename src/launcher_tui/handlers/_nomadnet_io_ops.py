"""NomadNet log viewer, config view/edit, and launch-error diagnosis.

Extracted from nomadnet.py to keep the main handler under the
1,500-line cap (CLAUDE.md Issue #6). All methods live as a mixin on
``NomadNetHandler`` alongside the other NomadNet mixins.

Expected host class methods (via MRO):
    self.ctx
    self._get_nomadnet_config_path()
    self._find_nomadnet_binary()
    self._get_rns_config_for_user()
    self._interactive_config_dir()
"""

import collections
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from backend import clear_screen
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class NomadNetIOOpsMixin:
    """Log viewing, config viewing/editing, and launch-error reporting."""

    # ------------------------------------------------------------------
    # Launch-error diagnosis
    # ------------------------------------------------------------------

    def _show_launch_error(self, returncode: int, stderr: str = ""):
        """Show NomadNet launch error using stderr output."""
        print(f"NomadNet exited with error code {returncode}")

        if stderr and stderr.strip():
            lines = stderr.strip().splitlines()
            for line in lines:
                if 'ConnectionRefusedError' in line or 'Errno 111' in line:
                    print("\nDiagnosis: RPC connection to rnsd refused")
                    print("  Fix: Use RNS Diagnostics to check rnsd status")
                    print("       sudo systemctl restart rnsd")
                    return
                if 'AuthenticationError' in line or 'digest sent was rejected' in line:
                    print("\nDiagnosis: RPC authentication failed")
                    print("  Fix: Use RNS Diagnostics to fix user mismatch")
                    return
                if 'KeyError' in line and 'textui' in line.lower():
                    print("\nDiagnosis: Config missing [textui] section")
                    print("  Fix: Delete ~/.nomadnetwork/config and restart")
                    return
                if 'PermissionError' in line or 'Permission denied' in line:
                    print("\nDiagnosis: Permission denied")
                    print("  Fix: Check file ownership with ls -la ~/.nomadnetwork/")
                    return
                if 'ModuleNotFoundError' in line or 'ImportError' in line:
                    print("\nDiagnosis: Missing Python dependencies")
                    print("  Fix: pipx reinstall nomadnet")
                    return

            print("\n--- stderr output ---")
            for line in lines[-10:]:
                print(f"  {line}")
            print("---")
        else:
            user_home = get_real_user_home()
            logfile = user_home / '.nomadnetwork' / 'logfile'
            print(f"\nCheck logs: cat {logfile}")
            print(f"  journalctl --user -u nomadnet -n 50")

    # ------------------------------------------------------------------
    # Log viewer
    # ------------------------------------------------------------------

    def _view_nomadnet_logs_for(self, config_dir: Path):
        """Scoped log viewer — shows only the logfile under ``config_dir``."""
        logfile = config_dir / "logfile"
        if not logfile.exists():
            self.ctx.dialog.msgbox(
                "No Logs",
                f"NomadNet logfile not found yet:\n\n  {logfile}\n\n"
                f"Logs are created on first start for this identity.",
            )
            return
        self._show_log_options(logfile)

    def _view_nomadnet_logs(self):
        """View NomadNet logfile. Offers a picker when multiple logs exist."""
        user_home = get_real_user_home()
        default_log = user_home / '.nomadnetwork' / 'logfile'
        interactive_log = self._interactive_config_dir() / 'logfile'

        available = []
        if default_log.exists():
            available.append((str(default_log), default_log))
        if interactive_log.exists():
            available.append((str(interactive_log), interactive_log))

        if not available:
            self.ctx.dialog.msgbox(
                "No Logs",
                "NomadNet logfile not found yet.\n\n"
                f"Default:     {default_log}\n"
                f"Interactive: {interactive_log}\n\n"
                "Logs are created when NomadNet runs.",
            )
            return

        if len(available) == 1:
            logfile = available[0][1]
        else:
            picker = [
                ("default", f"Daemon/default ({default_log})"),
                ("interactive", f"Interactive client ({interactive_log})"),
                ("back", "Back"),
            ]
            which = self.ctx.dialog.menu(
                "Which Logfile?",
                "Two NomadNet logfiles exist on this Pi.\n"
                "Pick the one you want to view:",
                picker,
            )
            if which is None or which == "back":
                return
            logfile = (interactive_log if which == "interactive"
                       else default_log)

        self._show_log_options(logfile)

    def _show_log_options(self, logfile: Path):
        """Render the last-N/errors/follow/rnsd-journal picker for a given logfile."""
        clear_screen()

        choices = [
            ("last50", "Last 50 lines"),
            ("last200", "Last 200 lines"),
            ("errors", "Errors only (last 200 lines)"),
            ("rnsd", "rnsd journal logs (last 50 lines)"),
            ("follow", "Follow live (Ctrl+C to stop)"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "NomadNet Logs",
            f"Logfile: {logfile}",
            choices,
        )

        if choice is None or choice == "back":
            return

        if choice == "follow":
            clear_screen()
            print(f"=== NomadNet log — {logfile} "
                  f"(Ctrl+C to stop) ===\n")
            try:
                subprocess.run(
                    ['tail', '-f', '-n', '30', str(logfile)],
                    timeout=None
                )
            except KeyboardInterrupt:
                pass
            return

        if choice == "rnsd":
            clear_screen()
            print("=== rnsd journal (last 50 lines) ===\n")
            try:
                result = subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '50',
                     '--no-pager'],
                    capture_output=True, text=True, timeout=15,
                )
                output = result.stdout.strip()
                if output:
                    print(output)
                else:
                    print("  (no rnsd journal entries found)")
                    print("  Check if rnsd runs as a systemd service:")
                    print("    sudo systemctl status rnsd")
            except FileNotFoundError:
                print("  journalctl not found (not a systemd system?)")
            except subprocess.TimeoutExpired:
                print("  journalctl timed out")
            except OSError as e:
                print(f"  Error reading journal: {e}")
            self.ctx.wait_for_enter()
            return

        maxlines = 200 if choice == "last200" else 50

        clear_screen()

        try:
            with open(logfile, 'r') as f:
                lines = list(collections.deque(
                    f, maxlen=max(maxlines, 200)
                ))

            if choice == "errors":
                error_patterns = [
                    'Error', 'Exception', 'CRITICAL',
                    'WARNING', 'AuthenticationError',
                    'PermissionError', 'Traceback',
                ]
                lines = [
                    line for line in lines
                    if any(p in line for p in error_patterns)
                ]
                print(f"=== NomadNet errors "
                      f"({len(lines)} found) ===\n")
            else:
                lines = lines[-maxlines:]
                print(f"=== NomadNet log (last "
                      f"{len(lines)} lines) ===\n")

            if lines:
                for line in lines:
                    print(line.rstrip())
            else:
                print("  (no matching lines)")

        except PermissionError:
            print(f"Cannot read {logfile} — permission denied")
        except OSError as e:
            print(f"Error reading logfile: {e}")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Config view / edit
    # ------------------------------------------------------------------

    def _view_nomadnet_config(self):
        """View NomadNet configuration (default identity)."""
        clear_screen()
        print("=== NomadNet Configuration ===\n")

        config_path = self._get_nomadnet_config_path()
        if config_path and config_path.exists():
            print(f"Config: {config_path}\n")
            try:
                content = config_path.read_text()
                print(content)

                print("\n--- Connectivity Notes ---")
                content_lower = content.lower()
                if 'enable_client = yes' in content_lower:
                    print("  Client:    ENABLED (can send/receive messages)")
                elif 'enable_client = no' in content_lower:
                    print("  Client:    DISABLED")
                if 'enable_node = yes' in content_lower:
                    print("  Node:      ENABLED (serving pages, propagation)")
                elif 'enable_node = no' in content_lower:
                    print("  Node:      DISABLED (not serving)")
                if 'announce_at_start = yes' in content_lower:
                    print("  Announce:  YES (visible to other nodes)")
                if 'user_interface = text' in content_lower:
                    print("  UI mode:   text (interactive TUI with browser)")
            except PermissionError:
                print(f"Permission denied reading {config_path}")
        else:
            print("No NomadNet config found.\n")
            print("Config is created on first run of NomadNet.")
            print("Expected locations (checked in order):")
            print("  1. /etc/nomadnetwork/config")
            user_home = get_real_user_home()
            print(f"  2. {user_home}/.config/nomadnetwork/config")
            print(f"  3. {user_home}/.nomadnetwork/config")
            print("\nRun 'Launch Text UI' to create the default config.")

        self.ctx.wait_for_enter()

    def _edit_nomadnet_config_for(self, config_dir: Path):
        """Edit a specific NomadNet config directory's config file."""
        config_path = config_dir / "config"
        if not config_path.exists():
            self.ctx.dialog.msgbox(
                "No Config Yet",
                f"No config file at:\n  {config_path}\n\n"
                f"Start this identity once to generate it, then Edit Config.",
            )
            return
        self._open_editor(config_path)

    def _open_editor(self, config_path: Path):
        """Launch an available editor against a config path and fix ownership."""
        editor = None
        for cmd in ['nano', 'vim', 'vi']:
            if shutil.which(cmd):
                editor = cmd
                break
        if not editor:
            self.ctx.dialog.msgbox("Error", "No text editor found (nano, vim, vi)")
            return
        subprocess.run([editor, str(config_path)], timeout=None)
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            try:
                subprocess.run(
                    ['chown', f'{sudo_user}:{sudo_user}', str(config_path)],
                    capture_output=True, timeout=10,
                )
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("chown after edit failed: %s", e)

    def _edit_nomadnet_config(self):
        """Edit default NomadNet config, generating it first if missing."""
        config_path = self._get_nomadnet_config_path()

        if not config_path or not config_path.exists():
            if self.ctx.dialog.yesno(
                "No Config Found",
                "NomadNet config doesn't exist yet.\n\n"
                "It is created automatically on first run.\n"
                "Launch NomadNet once to generate it?\n\n"
                "(It will create the config and exit.)",
            ):
                nn_path = self._find_nomadnet_binary()
                if nn_path:
                    self.ctx.dialog.infobox(
                        "Generating Config",
                        "Running NomadNet briefly to generate config...",
                    )
                    try:
                        rns_config_path = self._get_rns_config_for_user()
                        sudo_user = os.environ.get('SUDO_USER')

                        nn_args = ['--daemon']
                        if rns_config_path:
                            nn_args = ['--rnsconfig', rns_config_path, '--daemon']

                        if sudo_user and sudo_user != 'root':
                            cmd = ['sudo', '-H', '-u', sudo_user, nn_path] + nn_args
                        else:
                            cmd = [nn_path] + nn_args

                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        time.sleep(5)
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()

                        config_path = self._get_nomadnet_config_path()
                        if config_path and config_path.exists():
                            self.ctx.dialog.msgbox(
                                "Config Generated",
                                f"Config created at:\n  {config_path}\n\n"
                                f"Opening editor...",
                            )
                        else:
                            self.ctx.dialog.msgbox(
                                "Config Not Found",
                                "NomadNet ran but config was not generated.\n"
                                "Check: ~/.nomadnetwork/config",
                            )
                            return
                    except FileNotFoundError:
                        self.ctx.dialog.msgbox(
                            "Error",
                            f"NomadNet not found at: {nn_path}",
                        )
                        return
                    except Exception as e:
                        self.ctx.dialog.msgbox(
                            "Error",
                            f"Failed to generate config:\n{e}",
                        )
                        return
            else:
                return

        if not config_path or not config_path.exists():
            return

        self._open_editor(config_path)
