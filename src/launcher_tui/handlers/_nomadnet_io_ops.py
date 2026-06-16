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
            chown_ok = False
            try:
                chown_ok = subprocess.run(
                    ['chown', f'{sudo_user}:{sudo_user}', str(config_path)],
                    capture_output=True, timeout=10,
                ).returncode == 0
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("chown after edit failed: %s", e)
            if not chown_ok:
                # A root-owned config is unreadable to NomadNet next launch —
                # the user must know (mirrors _nomadnet_rns_checks chown warning).
                self.ctx.dialog.msgbox(
                    "Ownership Warning",
                    f"Config saved, but ownership could not be restored to "
                    f"{sudo_user} — NomadNet may not be able to read it.\n\n"
                    f"Fix: sudo chown {sudo_user}:{sudo_user} {config_path}"
                )

    # ------------------------------------------------------------------
    # Unified logs menu (Issue #45) — journal + tmux + logfile + rnsd
    # ------------------------------------------------------------------

    def _unified_logs_menu(self):
        """Dispatcher for the new Logs submenu.

        Combines the systemd user-unit journal, tmux capture-pane
        snapshot, NomadNet logfile and rnsd system journal under one
        picker. The snapshot view renders all four at once.

        Expects these mixin methods:
            self._default_identity_config_dir()
            self._nomadnet_service_state()
            self._service_state_line(state)
            self._view_nomadnet_logs_for(cfg_dir)
        """
        while True:
            cfg_dir = self._default_identity_config_dir()
            svc = self._nomadnet_service_state()
            unit_label = (
                "installed" if svc["unit_installed"] else "not installed"
            )
            tmux_label = "up" if svc["tmux_session"] else "down"

            choices = [
                ("snapshot", "Health snapshot (all sources)"),
                ("journal", f"systemd journal (user unit, {unit_label})"),
                ("tmux", f"tmux capture-pane (session: {tmux_label})"),
                ("logfile", "NomadNet logfile (default identity)"),
                ("rnsd", "rnsd journal (system scope)"),
                ("errors", "Errors only (logfile last 200 lines)"),
                ("follow", "Follow logfile live (Ctrl+C to stop)"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "NomadNet Logs",
                f"Config dir: {cfg_dir}\n{self._service_state_line(svc)}",
                choices,
            )
            if choice is None or choice == "back":
                return

            if choice == "snapshot":
                self.ctx.safe_call(
                    "Health snapshot",
                    lambda: self._show_unified_log_snapshot(cfg_dir, svc),
                )
            elif choice == "journal":
                self.ctx.safe_call(
                    "User journal", self._show_user_journal)
            elif choice == "tmux":
                self.ctx.safe_call(
                    "tmux capture", self._show_tmux_capture)
            elif choice == "logfile":
                self.ctx.safe_call(
                    "Logfile",
                    lambda: self._view_nomadnet_logs_for(cfg_dir))
            elif choice == "rnsd":
                self.ctx.safe_call(
                    "rnsd journal", self._show_rnsd_journal_quick)
            elif choice == "errors":
                self.ctx.safe_call(
                    "Errors only",
                    lambda: self._show_logfile_errors(cfg_dir))
            elif choice == "follow":
                self.ctx.safe_call(
                    "Follow",
                    lambda: self._follow_logfile(cfg_dir))

    # ------------------------------------------------------------------
    # Individual view helpers for the unified logs menu
    # ------------------------------------------------------------------

    def _show_unified_log_snapshot(self, cfg_dir: Path, svc: dict) -> None:
        """Render a single screen with journal + tmux pane + logfile + errors."""
        clear_screen()
        print("=== NomadNet Health Snapshot ===\n")

        # Service state line
        try:
            print(self._service_state_line(svc))
        except Exception:
            pass
        print()

        # Service journal (last 20)
        print("--- systemd --user -u nomadnet (last 20) ---")
        journal = self._capture_user_journal(tail=20)
        if journal:
            for line in journal.splitlines():
                print(f"  {line}")
        else:
            print("  (journal unavailable — unit may not be installed)")
        print()

        # tmux capture-pane (last 40)
        print("--- tmux capture-pane -t nomadnet (last 40) ---")
        pane = self._capture_tmux_pane(tail=40) if svc.get("tmux_session") else ""
        if pane:
            for line in pane.splitlines():
                print(f"  {line.rstrip()}")
        elif svc.get("tmux_session"):
            print("  (capture failed)")
        else:
            print("  (no tmux session)")
        print()

        # NomadNet logfile (last 20)
        print(f"--- {cfg_dir}/logfile (last 20) ---")
        logfile = cfg_dir / "logfile"
        if logfile.exists():
            try:
                with open(logfile, 'r') as f:
                    lines = list(collections.deque(f, maxlen=20))
                for line in lines:
                    print(f"  {line.rstrip()}")
            except OSError as e:
                print(f"  (read failed: {e})")
        else:
            print("  (no logfile yet)")
        print()

        self.ctx.wait_for_enter()

    def _show_user_journal(self) -> None:
        """Render ``journalctl --user -u nomadnet -n 50``."""
        clear_screen()
        print("=== journalctl --user -u nomadnet (last 50) ===\n")
        out = self._capture_user_journal(tail=50)
        if out:
            print(out)
        else:
            print("  (no entries — unit may not be installed / linger off)")
        self.ctx.wait_for_enter()

    def _show_tmux_capture(self) -> None:
        """Render ``tmux capture-pane -p -t nomadnet`` last 100 lines."""
        clear_screen()
        print("=== tmux capture-pane -t nomadnet (last 100) ===\n")
        out = self._capture_tmux_pane(tail=100)
        if out:
            print(out)
        else:
            print("  (no session or capture failed)")
        self.ctx.wait_for_enter()

    def _show_rnsd_journal_quick(self) -> None:
        """Render ``journalctl -u rnsd -n 50``."""
        clear_screen()
        print("=== journalctl -u rnsd (last 50) ===\n")
        try:
            res = subprocess.run(
                ['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'],
                capture_output=True, text=True, timeout=15,
            )
            out = (res.stdout or "").strip()
            print(out if out else "  (no entries)")
        except (subprocess.SubprocessError, OSError,
                FileNotFoundError) as e:
            print(f"  (journalctl failed: {e})")
        self.ctx.wait_for_enter()

    def _show_logfile_errors(self, cfg_dir: Path) -> None:
        """Errors-only tail from the default-identity logfile."""
        logfile = cfg_dir / "logfile"
        clear_screen()
        if not logfile.exists():
            print(f"No logfile yet: {logfile}")
            self.ctx.wait_for_enter()
            return
        patterns = ('Error', 'Exception', 'CRITICAL', 'WARNING',
                    'AuthenticationError', 'PermissionError', 'Traceback')
        try:
            with open(logfile, 'r') as f:
                lines = list(collections.deque(f, maxlen=200))
            matches = [
                line for line in lines if any(p in line for p in patterns)
            ]
            print(f"=== {logfile} — errors ({len(matches)}) ===\n")
            if matches:
                for line in matches:
                    print(line.rstrip())
            else:
                print("  (no errors in last 200 lines)")
        except OSError as e:
            print(f"Read failed: {e}")
        self.ctx.wait_for_enter()

    def _follow_logfile(self, cfg_dir: Path) -> None:
        """``tail -f`` wrapper for the default-identity logfile."""
        logfile = cfg_dir / "logfile"
        if not logfile.exists():
            self.ctx.dialog.msgbox(
                "No logfile",
                f"NomadNet logfile not found yet:\n\n  {logfile}",
            )
            return
        clear_screen()
        print(f"=== follow {logfile} (Ctrl+C to stop) ===\n")
        try:
            subprocess.run(
                ['tail', '-f', '-n', '30', str(logfile)], timeout=None,
            )
        except KeyboardInterrupt:
            pass

    def _capture_user_journal(self, tail: int = 50) -> str:
        """Return ``journalctl --user -u nomadnet -n <tail>`` stdout.

        Honors sudo context via the service-ops mixin so the journal
        comes from the real user's session bus.
        """
        # Delegate to service_ops if available (preferred — handles sudo).
        capture_fn = getattr(self, '_capture_journal_via_service_ops', None)
        if capture_fn is not None:
            return capture_fn(tail=tail)
        try:
            res = subprocess.run(
                ['journalctl', '--user', '-u', 'nomadnet',
                 '-n', str(tail), '--no-pager'],
                capture_output=True, text=True, timeout=15,
            )
            return (res.stdout or "").strip()
        except (subprocess.SubprocessError, OSError,
                FileNotFoundError):
            return ""

    def _capture_tmux_pane(self, tail: int = 40) -> str:
        """Return ``tmux capture-pane -p -t nomadnet`` last N lines.

        Honors sudo context: tmux sessions belong to the real user on
        fleet boxes.
        """
        tmux_bin = shutil.which("tmux")
        if not tmux_bin:
            return ""
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            try:
                import pwd as _pwd
                uid = _pwd.getpwnam(sudo_user).pw_uid
            except KeyError:
                return ""
            argv = [
                'sudo', '-u', sudo_user, '-H',
                'env', f'XDG_RUNTIME_DIR=/run/user/{uid}',
                tmux_bin, 'capture-pane', '-p', '-t', 'nomadnet',
            ]
        else:
            argv = [tmux_bin, 'capture-pane', '-p', '-t', 'nomadnet']
        try:
            res = subprocess.run(
                argv, capture_output=True, text=True, timeout=10,
            )
            out = res.stdout or ""
            lines = out.splitlines()
            return "\n".join(lines[-tail:])
        except (subprocess.SubprocessError, OSError,
                FileNotFoundError):
            return ""

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
