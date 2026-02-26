"""
Service Menu Mixin - Service and bridge management handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

import logging
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from backend import clear_screen
logger = logging.getLogger(__name__)

# Centralized service checking — first-party, always available
from utils.service_check import (
    check_systemd_service, check_process_running, check_service,
    apply_config_and_restart, enable_service, start_service, stop_service,
    restart_service, ServiceState, _sudo_cmd, check_udp_port,
    check_rns_shared_instance,
    lock_port_external, unlock_port_external,
    check_port_locked, persist_iptables,
)

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home

# Import RNS identity helpers
from commands.rns import get_identity_path
from commands.rns import create_identities

# Import propagation module
from commands import propagation


class ServiceMenuMixin:
    """Mixin providing service and bridge management functionality."""

    def _run_bridge(self):
        """Gateway bridge start/stop/status menu."""
        while True:
            # Check if bridge is already running
            bridge_running = self._is_bridge_running()
            daemon_managed = getattr(self, '_daemon_active', False)

            if daemon_managed and bridge_running:
                choices = [
                    ("status", "Bridge Status"),
                    ("logs", "View Bridge Logs"),
                    ("back", "Back"),
                ]
                subtitle = "Gateway bridge is RUNNING (managed by daemon)"
            elif bridge_running:
                choices = [
                    ("status", "Bridge Status"),
                    ("logs", "View Bridge Logs"),
                    ("stop", "Stop Bridge"),
                    ("back", "Back"),
                ]
                subtitle = "Gateway bridge is RUNNING (background)"
            else:
                choices = [
                    ("start", "Start Bridge (background)"),
                    ("start-fg", "Start Bridge (foreground, live logs)"),
                    ("back", "Back"),
                ]
                subtitle = "Gateway bridge is STOPPED"

            choice = self.dialog.menu(
                "Gateway Bridge",
                f"RNS <-> Meshtastic bridge:\n\n{subtitle}",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "start": ("Start Bridge (bg)", self._start_bridge_background),
                "start-fg": ("Start Bridge (fg)", self._start_bridge_foreground),
                "status": ("Bridge Status", self._show_bridge_status),
                "stop": ("Stop Bridge", self._stop_bridge),
                "logs": ("Bridge Logs", self._show_bridge_logs),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _is_bridge_running(self) -> bool:
        """Check if the gateway bridge process is running.

        Uses centralized service_check module when available.
        """
        try:
            return check_process_running('bridge_cli.py')
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Bridge process check failed: %s", e)
            return False

    def _bridge_preflight(self) -> bool:
        """Pre-flight checks before starting the gateway bridge.

        Checks prerequisites and offers to fix issues from the TUI
        so the user never needs to run manual commands.

        Returns True if all checks pass and bridge can start.
        """
        import time
        issues = []

        # 1. Check rnsd is running
        rnsd_running = False
        status = check_service('rnsd')
        rnsd_running = status.available

        if not rnsd_running:
            issues.append("rnsd is not running (required for RNS connectivity)")

        # 2. Check for NomadNet port conflict
        nomadnet_conflict = False
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and not rnsd_running:
                nomadnet_conflict = True
                issues.append("NomadNet is holding port 37428 (rnsd can't start)")
        except (subprocess.SubprocessError, OSError):
            pass

        # 3. Check gateway identity exists
        gw_id = get_identity_path()
        if not gw_id.exists():
            issues.append("Gateway identity not created yet")

        if not issues:
            return True

        # Build fix menu
        msg = "Pre-flight checks found issues:\n\n"
        for i, issue in enumerate(issues, 1):
            msg += f"  {i}. {issue}\n"
        msg += "\nMeshForge can fix these automatically."

        if not self.dialog.yesno("Bridge Pre-Flight", msg + "\n\nFix now?"):
            return False

        clear_screen()
        print("=== Bridge Pre-Flight Fix ===\n")

        # Fix NomadNet conflict first (must stop before rnsd can start)
        if nomadnet_conflict:
            print("[1] Stopping NomadNet (holds port 37428)...")
            try:
                subprocess.run(
                    ['pkill', '-f', 'nomadnet'],
                    capture_output=True, timeout=5
                )
                time.sleep(1)
                print("  NomadNet stopped.")
                print("  It will reconnect as a client after rnsd starts.\n")
            except (subprocess.SubprocessError, OSError) as e:
                print(f"  Warning: {e}")

        # Start rnsd if not running
        if not rnsd_running:
            print("[2] Starting rnsd (shared instance)...")
            try:
                success, msg_text = apply_config_and_restart('rnsd')
                if success:
                    print("  rnsd started via systemctl.")
                else:
                    # Try direct start as fallback
                    start_service('rnsd')
                time.sleep(2)
                # Verify
                status = check_service('rnsd')
                if status.available:
                    print("  rnsd is now running.\n")
                else:
                    print(f"  Warning: {status.message}\n")
            except (subprocess.SubprocessError, OSError) as e:
                print(f"  Error starting rnsd: {e}")
                print("  Bridge may fail to connect.\n")

        # Create gateway identity if missing
        gw_id = get_identity_path()
        if not gw_id.exists():
            print("[3] Creating gateway identity...")
            result = create_identities()
            if result.success:
                print(f"  {result.message}\n")
            else:
                print(f"  Warning: {result.message}\n")

        # Restart NomadNet as client (if we stopped it)
        if nomadnet_conflict:
            print("[4] Restarting NomadNet as rnsd client...")
            try:
                # Check if nomadnet is a systemd user service
                result = subprocess.run(
                    ['systemctl', '--user', 'start', 'nomadnet'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    print("  NomadNet restarted via systemctl --user.\n")
                else:
                    print("  NomadNet not managed by systemd.")
                    print("  Start manually: nomadnet --daemon &\n")
            except (subprocess.SubprocessError, OSError):
                print("  Start NomadNet manually: nomadnet --daemon &\n")

        print("Pre-flight complete. Starting bridge...\n")
        time.sleep(1)
        return True

    def _start_bridge_background(self):
        """Start gateway bridge as a background process."""
        if self._is_bridge_running():
            self.dialog.msgbox("Already Running", "Gateway bridge is already running.")
            return

        if not self._bridge_preflight():
            return

        self.dialog.infobox("Starting", "Starting gateway bridge in background...")

        try:
            import tempfile
            log_fd, log_path_str = tempfile.mkstemp(
                suffix='.log', prefix='meshforge-gateway-'
            )
            log_path = Path(log_path_str)
            self._bridge_log_path = log_path
            log_file = os.fdopen(log_fd, 'w')
            subprocess.Popen(
                [sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            log_file.close()

            # Wait briefly and verify it started
            import time
            time.sleep(3)

            if self._is_bridge_running():
                self.dialog.msgbox("Started",
                    "Gateway bridge started in background.\n\n"
                    f"Logs: {log_path}\n\n"
                    "Use 'Stop Bridge' to shut it down.")
            else:
                # Read log for error info
                try:
                    error_text = log_path.read_text()[-300:]
                except OSError as e:
                    logger.debug("Bridge log read failed: %s", e)
                    error_text = "(no log output)"
                self.dialog.msgbox("Failed",
                    f"Bridge failed to start.\n\n{error_text}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to start bridge:\n{e}")

    def _start_bridge_foreground(self):
        """Start gateway bridge in foreground with live output."""
        if self._is_bridge_running():
            self.dialog.msgbox("Already Running",
                "Gateway bridge is already running in background.\n\n"
                "Stop it first to run in foreground.")
            return

        if not self._bridge_preflight():
            return

        clear_screen()
        print("Starting Gateway Bridge (foreground)...")
        print("Press Ctrl+C to stop\n")
        try:
            subprocess.run(
                [sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')],
                timeout=None
            )
        except KeyboardInterrupt:
            print("\nBridge stopped.")
        try:
            self._wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _stop_bridge(self):
        """Stop the background gateway bridge."""
        if not self._is_bridge_running():
            self.dialog.msgbox("Not Running", "Gateway bridge is not running.")
            return

        if not self.dialog.yesno("Stop Bridge", "Stop the gateway bridge?"):
            return

        try:
            subprocess.run(
                ['pkill', '-f', 'bridge_cli.py'],
                capture_output=True, timeout=10
            )
            import time
            time.sleep(1)

            if self._is_bridge_running():
                # Force kill
                subprocess.run(
                    ['pkill', '-9', '-f', 'bridge_cli.py'],
                    capture_output=True, timeout=10
                )

            self.dialog.msgbox("Stopped", "Gateway bridge stopped.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to stop bridge:\n{e}")

    def _find_bridge_log(self) -> Optional[Path]:
        """Find the gateway bridge log file.

        Checks the stored path from the current session first, then searches
        /tmp for the most recent meshforge-gateway-*.log file.
        """
        if self._bridge_log_path and self._bridge_log_path.exists():
            return self._bridge_log_path

        # Search for most recent gateway log in /tmp
        try:
            logs = sorted(
                Path('/tmp').glob('meshforge-gateway-*.log'),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
        except OSError:
            logs = []
        if logs:
            self._bridge_log_path = logs[0]
            return logs[0]

        return None

    def _show_bridge_status(self):
        """Show gateway bridge log tail."""
        log_path = self._find_bridge_log()
        if not log_path:
            self.dialog.msgbox("No Logs", "No gateway log found.")
            return

        try:
            lines = log_path.read_text().strip().split('\n')
            # Show last 30 lines
            tail = '\n'.join(lines[-30:])
            self.dialog.msgbox(f"Bridge Status (last 30 lines)\n{log_path}", tail)
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read log:\n{e}")

    def _show_bridge_logs(self):
        """Show full gateway bridge logs in less."""
        log_path = self._find_bridge_log()
        if not log_path:
            self.dialog.msgbox("No Logs", "No gateway log found.")
            return

        clear_screen()
        try:
            subprocess.run(['less', '-R', '-X', '+G', str(log_path)], timeout=300)
        except KeyboardInterrupt:
            pass

    def _service_menu(self):
        """Service management menu - terminal-native."""
        while True:
            choices = [
                ("status", "Service Status (all)"),
                ("meshtasticd", "Manage meshtasticd"),
                ("rnsd", "Manage rnsd"),
                ("restart-mesh", "Restart meshtasticd"),
                ("start-rns", "Start rnsd"),
                ("restart-rns", "Restart rnsd"),
                ("install", "Install meshtasticd"),
                ("mqtt-setup", "MQTT Setup           Install & configure broker"),
                ("openhamclock", "OpenHamClock Docker  Start/stop/status"),
                ("lock-9443", "Lock Port 9443       Restrict to localhost"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Service Management",
                "Start/stop/restart services:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "install": ("Install meshtasticd", self._install_native_meshtasticd),
                "mqtt-setup": ("MQTT Setup", self._mqtt_setup_wizard),
                "openhamclock": ("OpenHamClock Docker", self._manage_openhamclock_docker),
                "status": ("Service Status", self._show_all_service_status),
                "restart-mesh": ("Restart meshtasticd", self._restart_meshtasticd_service),
                "start-rns": ("Start rnsd", self._start_rnsd_service),
                "restart-rns": ("Restart rnsd", self._restart_rnsd_service),
                "meshtasticd": ("Manage meshtasticd", lambda: self._manage_service("meshtasticd")),
                "rnsd": ("Manage rnsd", lambda: self._manage_service("rnsd")),
                "lock-9443": ("Port 9443 Lockdown", self._manage_port_lockdown),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _show_all_service_status(self):
        """Show status of all mesh services.

        Uses check_service() as the single source of truth to avoid
        contradictions between lightweight and detailed status checks.
        """
        clear_screen()
        print("=== Service Status ===\n")
        warnings = []
        failed_services = []
        use_direct_rnsd = not self._has_systemd_unit('rnsd')

        for svc in ['meshtasticd', 'rnsd', 'meshforge']:
            # MeshForge TUI IS MeshForge — if we're executing this code, it's running.
            # systemctl only knows about the systemd unit, not interactive sessions.
            if svc == 'meshforge':
                # Check systemd first, fall back to interactive detection
                is_systemd = False
                try:
                    svc_status = check_service(svc)
                    is_systemd = svc_status.available
                except Exception:
                    pass

                if is_systemd:
                    print(f"  \033[0;32m●\033[0m {svc:<18} running (service)")
                else:
                    print(f"  \033[0;32m●\033[0m {svc:<18} running (interactive)")
                continue

            # Special handling for rnsd without systemd unit
            if svc == 'rnsd' and use_direct_rnsd:
                if self._is_rnsd_running():
                    print(f"  \033[0;32m●\033[0m {svc:<18} running (process)")
                else:
                    print(f"  \033[2m○\033[0m {svc:<18} stopped")
                continue

            try:
                svc_status = check_service(svc)
                _, is_enabled = check_systemd_service(svc)

                boot_info = ""
                if svc_status.available and not is_enabled:
                    boot_info = "  (not enabled at boot)"
                    warnings.append(svc)

                if svc_status.available:
                    # rnsd zombie detection: systemd active but shared instance not available
                    if svc == 'rnsd' and not check_rns_shared_instance():
                        print(f"  \033[0;33m●\033[0m {svc:<18} running (shared instance not available)")
                    else:
                        print(f"  \033[0;32m●\033[0m {svc:<18} running{boot_info}")
                elif svc_status.state in (ServiceState.FAILED, ServiceState.DEGRADED):
                    print(f"  \033[0;31m●\033[0m {svc:<18} FAILED")
                    failed_services.append(svc)
                elif svc_status.state == ServiceState.NOT_RUNNING:
                    print(f"  \033[2m○\033[0m {svc:<18} stopped")
                else:
                    print(f"  \033[2m○\033[0m {svc:<18} {svc_status.state.value}")
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("Service status check for %s failed: %s", svc, e)
                print(f"  ? {svc:<18} unknown")
        print()

        if warnings:
            print(f"  \033[0;33mWarning:\033[0m {', '.join(warnings)} won't start on reboot.")
            print(f"  Fix: sudo systemctl enable {' '.join(warnings)}\n")

        # Show failure logs for services already identified as failed (no re-check)
        for svc in failed_services:
            try:
                print(f"\033[0;31m{svc} failure:\033[0m")
                subprocess.run(
                    ['journalctl', '-u', svc, '-n', '5', '--no-pager'],
                    timeout=10
                )
                print()
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("Failure log check for %s failed: %s", svc, e)
        self._wait_for_enter()

    def _manage_port_lockdown(self):
        """Lock/unlock external access to meshtasticd port 9443."""
        while True:
            locked = check_port_locked(9443)
            status_str = "\033[0;32mLOCKED\033[0m (localhost only)" if locked else "\033[0;31mOPEN\033[0m (external access allowed)"

            choices = [
                ("lock", "Lock Port 9443       Block external access"),
                ("unlock", "Unlock Port 9443     Allow external access"),
                ("persist", "Save Rules           Survive reboot"),
                ("status", "Check Status         Current lock state"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Port 9443 Lockdown",
                f"Current: {status_str}\n\n"
                "MeshForge proxies meshtasticd at :5000/mesh/\n"
                "Locking port 9443 forces traffic through MeshForge.",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "lock":
                clear_screen()
                success, msg = lock_port_external(9443)
                if success:
                    print(f"\033[0;32m✓\033[0m {msg}")
                    # Offer to persist
                    print("\nTo survive reboot, select 'Save Rules' from the menu.")
                else:
                    print(f"\033[0;31m✗\033[0m {msg}")
                self._wait_for_enter()

            elif choice == "unlock":
                clear_screen()
                success, msg = unlock_port_external(9443)
                if success:
                    print(f"\033[0;32m✓\033[0m {msg}")
                else:
                    print(f"\033[0;31m✗\033[0m {msg}")
                self._wait_for_enter()

            elif choice == "persist":
                clear_screen()
                print("Saving iptables rules for reboot persistence...\n")
                success, msg = persist_iptables()
                if success:
                    print(f"\033[0;32m✓\033[0m {msg}")
                else:
                    print(f"\033[0;31m✗\033[0m {msg}")
                self._wait_for_enter()

            elif choice == "status":
                clear_screen()
                print("=== Port 9443 Status ===\n")
                if locked:
                    print("  \033[0;32m●\033[0m Port 9443: LOCKED (localhost only)")
                else:
                    print("  \033[0;31m●\033[0m Port 9443: OPEN (external access allowed)")
                print()
                print("  Lock blocks external access via iptables.")
                print("  MeshForge proxies at :5000/mesh/ with filtering.")
                self._wait_for_enter()

    def _restart_meshtasticd_service(self):
        """Restart the meshtasticd service."""
        clear_screen()
        print("Restarting meshtasticd...\n")
        success, msg = apply_config_and_restart('meshtasticd')
        print(msg)
        subprocess.run(['systemctl', 'status', 'meshtasticd', '--no-pager', '-l'], timeout=10)
        self._wait_for_enter()

    def _start_rnsd_service(self):
        """Start the rnsd service."""
        clear_screen()
        print("Starting rnsd...\n")
        if not self._has_systemd_unit('rnsd'):
            self._start_rnsd_direct()
        else:
            success, msg = start_service('rnsd')
            print(msg)
            subprocess.run(['systemctl', 'status', 'rnsd', '--no-pager', '-l'], timeout=10)
        self._wait_for_enter()

    def _restart_rnsd_service(self):
        """Restart the rnsd service."""
        clear_screen()
        print("Restarting rnsd...\n")
        if not self._has_systemd_unit('rnsd'):
            self._stop_rnsd_direct()
            import time
            time.sleep(0.5)
            self._start_rnsd_direct()
        else:
            success, msg = restart_service('rnsd')
            print(msg)
            subprocess.run(['systemctl', 'status', 'rnsd', '--no-pager', '-l'], timeout=10)
        self._wait_for_enter()

    def _fix_spi_config(self, has_native: bool = False):
        """Quick fix for SPI HAT with wrong USB config."""
        self.dialog.infobox("Fixing", "Removing wrong USB configuration...")

        try:
            config_dir = Path('/etc/meshtasticd')

            # Remove wrong USB config from config.d
            usb_config = config_dir / 'config.d' / 'usb-serial.yaml'
            if usb_config.exists():
                usb_config.unlink()
                self.dialog.infobox("Fixing", "Removed usb-serial.yaml from config.d/")

            # Check if config.yaml exists and is valid (has Webserver section)
            config_yaml = config_dir / 'config.yaml'
            needs_config = False
            if not config_yaml.exists():
                needs_config = True
            elif not config_yaml.read_text().strip():
                needs_config = True
            elif 'Webserver:' not in config_yaml.read_text():
                # Config exists but missing Webserver - probably corrupted
                self.dialog.msgbox(
                    "Config Warning",
                    f"Your config.yaml may be corrupted:\n{config_yaml}\n\n"
                    "It's missing the Webserver section.\n"
                    "Check: cat /etc/meshtasticd/config.yaml"
                )

            # Only create config.yaml if it doesn't exist or is empty.
            # Uses centralized ensure_structure() so content stays in sync
            # with templates/config.yaml.  NEVER overwrites user's config.
            if needs_config:
                from core.meshtasticd_config import MeshtasticdConfig
                MeshtasticdConfig().ensure_structure()
                self.dialog.infobox("Fixing", "Created minimal config.yaml")

            # NOTE: We do NOT create HAT templates - meshtasticd provides them
            # User should select from /etc/meshtasticd/available.d/

            if not has_native:
                # Offer to install native meshtasticd
                if self.dialog.yesno(
                    "Install Native Daemon?",
                    "SPI HATs require the native meshtasticd daemon.\n\n"
                    "Would you like to install it now?\n\n"
                    "(This requires internet connection)"
                ):
                    self._install_native_meshtasticd()
                else:
                    self.dialog.msgbox(
                        "Config Fixed",
                        "Wrong USB config removed.\n\n"
                        "To complete setup, install native meshtasticd:\n"
                        "  sudo apt install meshtasticd\n\n"
                        "Or run: sudo bash scripts/install_noc.sh --force-native"
                    )
            else:
                # Native daemon exists - restart service
                apply_config_and_restart('meshtasticd')

                self.dialog.msgbox(
                    "Config Fixed",
                    "Configuration corrected!\n\n"
                    "- Removed wrong USB config\n"
                    "- Restarted meshtasticd service\n\n"
                    "Check status: sudo systemctl status meshtasticd"
                )

        except Exception as e:
            self.dialog.msgbox("Error", f"Fix failed:\n{e}")

    def _install_native_meshtasticd(self):
        """Install native meshtasticd for SPI HAT."""
        self.dialog.infobox("Installing", "Installing native meshtasticd...")

        try:
            # Check if already installed
            result = subprocess.run(['which', 'meshtasticd'], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                # Not installed - try to install
                self.dialog.infobox("Installing", "Adding Meshtastic repository...")

                # Detect OS for correct repo (matching install_noc.sh logic)
                os_repo = "Raspbian_12"  # Default for Pi
                if Path('/etc/os-release').exists():
                    os_info = {}
                    with open('/etc/os-release') as f:
                        for line in f:
                            if '=' in line:
                                key, val = line.strip().split('=', 1)
                                os_info[key] = val.strip('"')

                    os_id = os_info.get('ID', '')
                    version_id = os_info.get('VERSION_ID', '')

                    if os_id == 'raspbian':
                        os_repo = f"Raspbian_{version_id.split('.')[0]}" if version_id else "Raspbian_12"
                    elif os_id == 'debian':
                        os_repo = f"Debian_{version_id.split('.')[0]}" if version_id else "Debian_12"
                    elif os_id == 'ubuntu':
                        os_repo = f"xUbuntu_{version_id}" if version_id else "xUbuntu_24.04"

                repo_url = f"https://download.opensuse.org/repositories/network:/Meshtastic:/beta/{os_repo}/"

                # Add repo
                subprocess.run(
                    ['tee', '/etc/apt/sources.list.d/meshtastic.list'],
                    input=f"deb {repo_url} /\n",
                    text=True, timeout=30, check=False
                )

                # Download and install GPG key (no shell=True / bash -c)
                key_result = subprocess.run(
                    ['curl', '-fsSL', f'{repo_url}Release.key'],
                    capture_output=True, timeout=30, check=False
                )
                if key_result.returncode == 0:
                    subprocess.run(
                        ['gpg', '--dearmor', '-o', '/etc/apt/trusted.gpg.d/meshtastic.gpg'],
                        input=key_result.stdout, timeout=30, check=False
                    )

                self.dialog.infobox("Installing", "Updating package list...")
                subprocess.run(['apt-get', 'update'], timeout=120, check=False)

                self.dialog.infobox("Installing", "Installing meshtasticd...")
                result = subprocess.run(['apt-get', 'install', '-y', 'meshtasticd'], timeout=300, capture_output=True, text=True)

                if result.returncode != 0:
                    self.dialog.msgbox("Error", f"Failed to install meshtasticd:\n{result.stderr[:500]}")
                    return

            # Find actual meshtasticd binary path
            result = subprocess.run(['which', 'meshtasticd'], capture_output=True, text=True, timeout=5)
            meshtasticd_bin = result.stdout.strip() if result.returncode == 0 else '/usr/bin/meshtasticd'

            # Ensure config directories and config.yaml exist.
            # Uses centralized ensure_structure() — creates dirs, deploys
            # templates from templates/config.yaml.  NEVER overwrites user's
            # config.yaml (MaxNodes, MaxMessageQueue, etc.).
            config_dir = Path('/etc/meshtasticd')
            config_yaml = config_dir / 'config.yaml'
            from core.meshtasticd_config import MeshtasticdConfig
            MeshtasticdConfig().ensure_structure()
            if config_yaml.exists():
                self.dialog.infobox("Installing", "Config structure ready")

            # NOTE: We do NOT create HAT templates - meshtasticd package provides them
            # User selects their HAT from /etc/meshtasticd/available.d/ via Hardware Config menu

            # Remove wrong USB config if present
            usb_config = config_dir / 'config.d' / 'usb-serial.yaml'
            if usb_config.exists():
                usb_config.unlink()
                self.dialog.infobox("Installing", "Removed incorrect USB config")

            # Create service file
            service_content = f"""[Unit]
Description=Meshtastic Daemon (Native SPI)
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart={meshtasticd_bin} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
            Path('/etc/systemd/system/meshtasticd.service').write_text(service_content)

            # Reload, enable, and start
            success, msg = enable_service('meshtasticd', start=True)
            if not success:
                self.dialog.msgbox("Warning", f"Service setup issue: {msg}")

            self.dialog.msgbox(
                "Success",
                "Native meshtasticd installed!\n\n"
                "NEXT STEP: Select your HAT config:\n"
                "  meshtasticd → Hardware Config\n\n"
                "Or manually:\n"
                "  ls /etc/meshtasticd/available.d/\n"
                "  sudo cp /etc/meshtasticd/available.d/<your-hat>.yaml \\\n"
                "         /etc/meshtasticd/config.d/\n"
                "  sudo systemctl restart meshtasticd"
            )

        except Exception as e:
            self.dialog.msgbox("Error", f"Installation failed:\n{e}")

    def _manage_service(self, service_name: str):
        """Manage a specific service."""
        choices = [
            ("status", "Check Status"),
            ("start", "Start Service"),
            ("stop", "Stop Service"),
            ("restart", "Restart Service"),
            ("logs", "View Logs"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                f"Manage {service_name}",
                f"Select action for {service_name}:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._service_action(service_name, choice)

    def _has_systemd_unit(self, service_name: str) -> bool:
        """Check if a service has a systemd unit file."""
        try:
            result = subprocess.run(
                ['systemctl', 'cat', service_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("systemd unit check for %s failed: %s", service_name, e)
            return False

    def _is_rnsd_running(self) -> bool:
        """Check if rnsd is running as a process."""
        try:
            return check_process_running('rnsd')
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("rnsd process check failed: %s", e)
            return False

    def _start_rnsd_direct(self) -> bool:
        """Start rnsd directly as a background process.

        Returns True if started successfully.
        """
        # Check if already running
        if self._is_rnsd_running():
            print("rnsd is already running.")
            return True

        # Check if rnsd binary exists
        rnsd_path = shutil.which('rnsd')
        if not rnsd_path:
            print("\033[0;31mError:\033[0m rnsd not found in PATH.")
            print("Install Reticulum: pip install rns")
            return False

        try:
            # Start rnsd as a background daemon
            # rnsd itself daemonizes when run without -f flag
            print("Starting rnsd daemon...")
            result = subprocess.run(
                ['rnsd'],
                capture_output=True,
                text=True,
                timeout=10
            )
            # rnsd daemonizes and returns quickly
            # Check if it actually started
            import time
            time.sleep(0.5)
            if self._is_rnsd_running():
                print("\033[0;32m✓\033[0m rnsd started successfully.")
                return True
            else:
                print(f"\033[0;31mError:\033[0m rnsd failed to start.")
                if result.stderr:
                    print(result.stderr)
                return False
        except subprocess.TimeoutExpired:
            # If it times out, check if running anyway (daemon fork)
            if self._is_rnsd_running():
                print("\033[0;32m✓\033[0m rnsd started successfully.")
                return True
            print("\033[0;31mError:\033[0m rnsd start timed out.")
            return False
        except Exception as e:
            print(f"\033[0;31mError:\033[0m Failed to start rnsd: {e}")
            return False

    def _stop_rnsd_direct(self) -> bool:
        """Stop rnsd process directly.

        Returns True if stopped successfully.
        """
        if not self._is_rnsd_running():
            print("rnsd is not running.")
            return True

        try:
            print("Stopping rnsd...")
            # Use pkill to stop rnsd gracefully
            result = subprocess.run(
                ['pkill', '-TERM', '-x', 'rnsd'],
                capture_output=True,
                timeout=10
            )
            import time
            time.sleep(0.5)
            if not self._is_rnsd_running():
                print("\033[0;32m✓\033[0m rnsd stopped.")
                return True
            # If still running, try SIGKILL
            subprocess.run(['pkill', '-KILL', '-x', 'rnsd'], timeout=5)
            time.sleep(0.3)
            if not self._is_rnsd_running():
                print("\033[0;32m✓\033[0m rnsd stopped (forced).")
                return True
            print("\033[0;31mError:\033[0m Could not stop rnsd.")
            return False
        except Exception as e:
            print(f"\033[0;31mError:\033[0m Failed to stop rnsd: {e}")
            return False

    def _service_action(self, service_name: str, action: str):
        """Perform service action using systemctl or direct process control.

        For rnsd: Uses direct process control if no systemd unit exists.
        For other services: Uses systemctl.
        """
        clear_screen()

        # Check if rnsd needs direct process handling
        use_direct_rnsd = (service_name == 'rnsd' and
                          not self._has_systemd_unit('rnsd'))

        if action == "status":
            print(f"=== {service_name} status ===\n")
            if use_direct_rnsd:
                # Show process status for rnsd
                if self._is_rnsd_running():
                    print(f"\033[0;32m●\033[0m rnsd is \033[0;32mrunning\033[0m")
                    # Show process info
                    try:
                        subprocess.run(
                            ['pgrep', '-a', '-x', 'rnsd'],
                            timeout=5
                        )
                    except (subprocess.SubprocessError, OSError) as e:
                        logger.debug("rnsd process info display failed: %s", e)
                else:
                    print(f"\033[0;31m○\033[0m rnsd is \033[0;31mnot running\033[0m")
                    print("\nTo start: Select 'Start Service' from the menu")
            else:
                subprocess.run(
                    ['systemctl', 'status', service_name, '--no-pager', '-l'],
                    timeout=10
                )
            self._wait_for_enter()

        elif action == "start":
            print(f"Starting {service_name}...\n")
            if use_direct_rnsd:
                self._start_rnsd_direct()
            else:
                success, msg = start_service(service_name)
                print(msg)
                subprocess.run(
                    ['systemctl', 'status', service_name, '--no-pager', '-l'],
                    timeout=10
                )
            self._wait_for_enter()

        elif action == "stop":
            if self.dialog.yesno("Confirm", f"Stop {service_name}?", default_no=True):
                clear_screen()
                print(f"Stopping {service_name}...\n")
                if use_direct_rnsd:
                    self._stop_rnsd_direct()
                else:
                    success, msg = stop_service(service_name)
                    print(msg)
                self._wait_for_enter()

        elif action == "restart":
            print(f"Restarting {service_name}...\n")
            if use_direct_rnsd:
                self._stop_rnsd_direct()
                import time
                time.sleep(0.5)
                self._start_rnsd_direct()
            else:
                success, msg = restart_service(service_name)
                print(msg)
                subprocess.run(
                    ['systemctl', 'status', service_name, '--no-pager', '-l'],
                    timeout=10
                )
            self._wait_for_enter()

        elif action == "logs":
            print(f"=== {service_name} logs (last 30) ===\n")
            if use_direct_rnsd:
                # rnsd logs go to ~/.reticulum/logfile by default
                try:
                    log_path = get_real_user_home() / '.reticulum' / 'logfile'
                    if log_path.exists():
                        print(f"Log file: {log_path}\n")
                        subprocess.run(
                            ['tail', '-n', '30', str(log_path)],
                            timeout=10
                        )
                    else:
                        print("No log file found at ~/.reticulum/logfile")
                        print("rnsd may log to stdout or syslog depending on config.")
                except Exception as e:
                    print(f"Could not read logs: {e}")
            else:
                subprocess.run(
                    ['journalctl', '-u', service_name, '-n', '30', '--no-pager'],
                    timeout=15
                )
            self._wait_for_enter()

    # =========================================================================
    # OpenHamClock Docker Management
    # =========================================================================

    def _manage_openhamclock_docker(self):
        """Manage OpenHamClock as a Docker container."""
        docker_bin = shutil.which('docker')
        if not docker_bin:
            self.dialog.msgbox(
                "Docker Not Found",
                "Docker is required for OpenHamClock.\n\n"
                "Install Docker:\n"
                "  curl -fsSL https://get.docker.com | sh\n"
                "  sudo usermod -aG docker $USER"
            )
            return

        while True:
            # Check current container status
            running = self._is_openhamclock_running()
            status_str = "Running" if running else "Stopped"

            choices = [
                ("status", f"Status: {status_str}"),
                ("start", "Start OpenHamClock"),
                ("stop", "Stop OpenHamClock"),
                ("logs", "View Logs"),
                ("configure", "Configure in MeshForge"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "OpenHamClock (Docker)",
                "Manage OpenHamClock container.\n"
                "Community replacement for HamClock.\n"
                "https://github.com/accius/openhamclock",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("OpenHamClock Status", self._openhamclock_docker_status),
                "start": ("Start OpenHamClock", self._start_openhamclock_docker),
                "stop": ("Stop OpenHamClock", self._stop_openhamclock_docker),
                "logs": ("OpenHamClock Logs", self._openhamclock_docker_logs),
                "configure": ("Configure OpenHamClock", self._configure_openhamclock),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _is_openhamclock_running(self) -> bool:
        """Check if OpenHamClock Docker container is running."""
        try:
            result = subprocess.run(
                ['docker', 'ps', '--filter', 'name=openhamclock',
                 '--filter', 'status=running', '--format', '{{.Names}}'],
                capture_output=True, text=True, timeout=10
            )
            return 'openhamclock' in result.stdout.lower()
        except (subprocess.SubprocessError, OSError):
            return False

    def _openhamclock_docker_status(self):
        """Show OpenHamClock Docker container status."""
        clear_screen()
        print("=== OpenHamClock Docker Status ===\n")

        try:
            result = subprocess.run(
                ['docker', 'ps', '-a', '--filter', 'name=openhamclock',
                 '--format', 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip():
                print(result.stdout)
            else:
                print("No OpenHamClock container found.\n")
                print("Start with: docker run -d --name openhamclock -p 3000:3000 openhamclock")
        except (subprocess.SubprocessError, OSError) as e:
            print(f"Error checking status: {e}")

        self._wait_for_enter()

    def _start_openhamclock_docker(self):
        """Start OpenHamClock Docker container."""
        clear_screen()
        print("=== Starting OpenHamClock ===\n")

        # Check if container already exists
        try:
            result = subprocess.run(
                ['docker', 'ps', '-a', '--filter', 'name=openhamclock',
                 '--format', '{{.Names}}'],
                capture_output=True, text=True, timeout=10
            )

            if 'openhamclock' in result.stdout.lower():
                # Container exists, just start it
                print("Starting existing container...")
                result = subprocess.run(
                    ['docker', 'start', 'openhamclock'],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    print("\033[0;32m✓\033[0m OpenHamClock started on port 3000")
                else:
                    print(f"\033[0;31mError:\033[0m {result.stderr}")
            else:
                # Need to create the container
                print("Pulling and starting OpenHamClock...")
                print("(This may take a moment on first run)\n")
                result = subprocess.run(
                    ['docker', 'run', '-d',
                     '--name', 'openhamclock',
                     '-p', '3000:3000',
                     '--restart', 'unless-stopped',
                     'ghcr.io/accius/openhamclock:latest'],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    print("\033[0;32m✓\033[0m OpenHamClock started on port 3000")
                    print("\nAuto-configuring MeshForge...")
                    propagation.configure_source(
                        propagation.DataSource.OPENHAMCLOCK,
                        host="localhost", port=3000
                    )
                    print("\033[0;32m✓\033[0m MeshForge configured for OpenHamClock")
                else:
                    print(f"\033[0;31mError:\033[0m {result.stderr}")

        except subprocess.TimeoutExpired:
            print("\033[0;31mError:\033[0m Docker operation timed out.")
        except (subprocess.SubprocessError, OSError) as e:
            print(f"\033[0;31mError:\033[0m {e}")

        self._wait_for_enter()

    def _stop_openhamclock_docker(self):
        """Stop OpenHamClock Docker container."""
        clear_screen()
        print("=== Stopping OpenHamClock ===\n")

        try:
            result = subprocess.run(
                ['docker', 'stop', 'openhamclock'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                print("\033[0;32m✓\033[0m OpenHamClock stopped.")
            else:
                print(f"\033[0;31mError:\033[0m {result.stderr}")
        except subprocess.TimeoutExpired:
            print("\033[0;31mError:\033[0m Stop operation timed out.")
        except (subprocess.SubprocessError, OSError) as e:
            print(f"\033[0;31mError:\033[0m {e}")

        self._wait_for_enter()

    def _openhamclock_docker_logs(self):
        """Show OpenHamClock Docker container logs."""
        clear_screen()
        print("=== OpenHamClock Logs (last 30) ===\n")

        try:
            subprocess.run(
                ['docker', 'logs', '--tail', '30', 'openhamclock'],
                timeout=15
            )
        except subprocess.TimeoutExpired:
            print("Log retrieval timed out.")
        except (subprocess.SubprocessError, OSError) as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    # =========================================================================
    # MQTT Setup Wizard - Local broker for multi-consumer architecture
    # =========================================================================

    def _mqtt_setup_wizard(self):
        """MQTT setup wizard - install mosquitto and configure meshtasticd.

        This enables the local MQTT architecture where meshtasticd publishes
        to a local mosquitto broker, allowing multiple consumers (MeshForge,
        meshing-around, Grafana, etc.) to receive mesh messages.
        """
        # Introduction
        if not self.dialog.yesno(
            "MQTT Setup Wizard",
            "This wizard will set up local MQTT architecture:\n\n"
            "1. Install mosquitto MQTT broker\n"
            "2. Configure meshtasticd to publish to local broker\n"
            "3. Enable uplink on primary channel\n\n"
            "Benefits:\n"
            "• Multiple apps can receive mesh messages\n"
            "• No more TCP one-client limitation\n"
            "• Works with meshing-around, Grafana, etc.\n\n"
            "Continue with setup?"
        ):
            return

        # Step 1: Check/Install mosquitto
        self.dialog.infobox("MQTT Setup", "Step 1/3: Checking mosquitto...")

        if not self._is_mosquitto_installed():
            if self.dialog.yesno(
                "Install Mosquitto",
                "Mosquitto MQTT broker is not installed.\n\n"
                "Install it now?\n\n"
                "This will run: apt install mosquitto mosquitto-clients"
            ):
                if not self._install_mosquitto():
                    return
            else:
                self.dialog.msgbox(
                    "Setup Cancelled",
                    "MQTT setup requires mosquitto.\n\n"
                    "Install manually with:\n"
                    "  sudo apt install mosquitto mosquitto-clients"
                )
                return
        else:
            self.dialog.infobox("MQTT Setup", "Mosquitto is already installed.")

        # Step 2: Ensure mosquitto is running
        self.dialog.infobox("MQTT Setup", "Step 2/3: Starting mosquitto service...")
        if not self._ensure_mosquitto_running():
            self.dialog.msgbox(
                "Warning",
                "Could not start mosquitto service.\n\n"
                "Check: sudo systemctl status mosquitto"
            )
            # Continue anyway - user might fix manually

        # Step 3: Configure meshtasticd
        self.dialog.infobox("MQTT Setup", "Step 3/3: Configuring meshtasticd...")

        # Auto-detect channel name
        channel_name = self._auto_detect_primary_channel()

        if not self._configure_meshtasticd_mqtt_local(channel_name):
            self.dialog.msgbox(
                "Warning",
                "Could not fully configure meshtasticd MQTT.\n\n"
                "You may need to configure manually:\n"
                "  meshtastic --set mqtt.enabled true\n"
                "  meshtastic --set mqtt.address localhost\n"
                "  meshtastic --set mqtt.json_enabled true\n"
                "  meshtastic --ch-index 0 --ch-set uplink_enabled true"
            )
            return

        # Success!
        topic_pattern = f"msh/2/json/{channel_name}/#" if channel_name else "msh/2/json/+/#"
        self.dialog.msgbox(
            "MQTT Setup Complete",
            "Local MQTT architecture is ready!\n\n"
            "Services:\n"
            f"  • Mosquitto: localhost:1883\n"
            f"  • Topic: {topic_pattern}\n\n"
            "Test with:\n"
            f"  mosquitto_sub -h localhost -t 'msh/#' -v\n\n"
            "MeshForge will now receive messages via MQTT\n"
            "alongside other consumers like meshing-around."
        )

    def _is_mosquitto_installed(self) -> bool:
        """Check if mosquitto is installed."""
        try:
            result = subprocess.run(
                ['which', 'mosquitto'],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("mosquitto install check failed: %s", e)
            return False

    def _install_mosquitto(self) -> bool:
        """Install mosquitto MQTT broker.

        Returns True if installation succeeded.
        """
        clear_screen()
        print("=== Installing Mosquitto MQTT Broker ===\n")

        try:
            # Update package list
            print("Updating package list...")
            result = subprocess.run(
                ['apt-get', 'update'],
                timeout=120
            )

            # Install mosquitto and clients
            print("\nInstalling mosquitto and mosquitto-clients...")
            result = subprocess.run(
                ['apt-get', 'install', '-y', 'mosquitto', 'mosquitto-clients'],
                timeout=300
            )

            if result.returncode != 0:
                print("\n\033[0;31mError:\033[0m Installation failed.")
                self._wait_for_enter()
                return False

            print("\n\033[0;32m✓\033[0m Mosquitto installed successfully.")
            self._wait_for_enter()
            return True

        except subprocess.TimeoutExpired:
            print("\n\033[0;31mError:\033[0m Installation timed out.")
            self._wait_for_enter()
            return False
        except Exception as e:
            print(f"\n\033[0;31mError:\033[0m {e}")
            self._wait_for_enter()
            return False

    def _ensure_mosquitto_running(self) -> bool:
        """Ensure mosquitto service is running and enabled.

        Returns True if mosquitto is running.
        """
        try:
            # Enable and start mosquitto
            success, msg = enable_service('mosquitto', start=True)
            return success

        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("mosquitto start/verify failed: %s", e)
            return False

    def _auto_detect_primary_channel(self) -> Optional[str]:
        """Auto-detect primary channel name from meshtasticd.

        Returns channel name or None if detection fails.
        """
        try:
            # Try meshtastic CLI
            cli = shutil.which('meshtastic') or 'meshtastic'
            result = subprocess.run(
                [cli, '--host', 'localhost', '--ch-index', '0', '--info'],
                capture_output=True, text=True, timeout=15
            )

            if result.returncode == 0:
                # Parse channel name from output
                for line in result.stdout.split('\n'):
                    if 'name' in line.lower():
                        parts = line.split(':')
                        if len(parts) >= 2:
                            name = parts[1].strip().strip('"\'')
                            if name and name.lower() != 'none':
                                return name

        except (subprocess.SubprocessError, OSError, ValueError) as e:
            logger.debug("Channel auto-detect failed: %s", e)

        return None

    def _configure_meshtasticd_mqtt_local(self, channel_name: Optional[str] = None) -> bool:
        """Configure meshtasticd to use local mosquitto broker.

        Args:
            channel_name: Optional channel name for display (auto-detected)

        Returns True if configuration succeeded.
        """
        clear_screen()
        print("=== Configuring meshtasticd for Local MQTT ===\n")

        cli = shutil.which('meshtastic') or 'meshtastic'
        success = True

        try:
            # Enable MQTT
            print("Enabling MQTT...")
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.enabled', 'true'],
                timeout=15
            )
            if result.returncode != 0:
                success = False

            # Set broker to localhost
            print("Setting broker to localhost...")
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.address', 'localhost'],
                timeout=15
            )
            if result.returncode != 0:
                success = False

            # Enable JSON mode for human-readable messages
            print("Enabling JSON mode...")
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.json_enabled', 'true'],
                timeout=15
            )
            if result.returncode != 0:
                success = False

            # Enable uplink on primary channel
            print("Enabling uplink on primary channel...")
            result = subprocess.run(
                [cli, '--host', 'localhost',
                 '--ch-index', '0', '--ch-set', 'uplink_enabled', 'true'],
                timeout=15
            )
            if result.returncode != 0:
                success = False

            if success:
                print(f"\n\033[0;32m✓\033[0m Configuration complete!")
                if channel_name:
                    print(f"  Channel: {channel_name}")
                print(f"  Broker: localhost:1883")
                print(f"  JSON mode: enabled")
                print(f"  Uplink: enabled (channel 0)")
            else:
                print("\n\033[0;33mWarning:\033[0m Some settings may have failed.")
                print("Check meshtasticd is running: sudo systemctl status meshtasticd")

            self._wait_for_enter()
            return success

        except Exception as e:
            print(f"\n\033[0;31mError:\033[0m {e}")
            self._wait_for_enter()
            return False
