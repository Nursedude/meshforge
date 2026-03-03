"""
Service Menu Handler — Service and bridge management for the TUI.

Converted from service_menu_mixin.py as part of the mixin-to-registry migration.
Provides bridge start/stop, service management, port lockdown, OpenHamClock Docker,
MQTT setup wizard, and meshtasticd installation.
"""

import logging
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from backend import clear_screen
from handler_protocol import BaseHandler, PRIVILEGE_ADMIN

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

# Extracted helpers — OpenHamClock Docker + MQTT setup wizard
from handlers import _service_extras


class ServiceMenuHandler(BaseHandler):
    """TUI handler for service and bridge management."""

    handler_id = "service_menu"
    menu_section = "mesh_networks"
    privilege_level = PRIVILEGE_ADMIN

    def __init__(self):
        super().__init__()
        self._bridge_log_path = None

    def menu_items(self):
        return [
            ("services", "Service Control     Start/stop/restart", None),
        ]

    def execute(self, action):
        if action == "services":
            self._service_menu()

    def _run_bridge(self):
        """Gateway bridge start/stop/status menu."""
        def _bridge_choices():
            bridge_running = self._is_bridge_running()
            daemon_managed = self.ctx.daemon_active
            if daemon_managed and bridge_running:
                return [
                    ("status", "Bridge Status"),
                    ("logs", "View Bridge Logs"),
                ]
            elif bridge_running:
                return [
                    ("status", "Bridge Status"),
                    ("logs", "View Bridge Logs"),
                    ("stop", "Stop Bridge"),
                ]
            else:
                return [
                    ("start", "Start Bridge (background)"),
                    ("start-fg", "Start Bridge (foreground, live logs)"),
                ]

        dispatch = {
            "start": ("Start Bridge (bg)", self._start_bridge_background),
            "start-fg": ("Start Bridge (fg)", self._start_bridge_foreground),
            "status": ("Bridge Status", self._show_bridge_status),
            "stop": ("Stop Bridge", self._stop_bridge),
            "logs": ("Bridge Logs", self._show_bridge_logs),
        }
        self.run_menu_loop(
            "Gateway Bridge", "RNS <-> Meshtastic bridge:",
            _bridge_choices, dispatch,
        )

    def _is_bridge_running(self) -> bool:
        """Check if the gateway bridge process is running."""
        try:
            return check_process_running('bridge_cli.py')
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Bridge process check failed: %s", e)
            return False

    def _bridge_preflight(self) -> bool:
        """Pre-flight checks before starting the gateway bridge.

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

        if not self.ctx.dialog.yesno("Bridge Pre-Flight", msg + "\n\nFix now?"):
            return False

        clear_screen()
        print("=== Bridge Pre-Flight Fix ===\n")

        # Fix NomadNet conflict first
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
                    start_service('rnsd')
                time.sleep(2)
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
            self.ctx.dialog.msgbox("Already Running", "Gateway bridge is already running.")
            return

        if not self._bridge_preflight():
            return

        self.ctx.dialog.infobox("Starting", "Starting gateway bridge in background...")

        try:
            import tempfile
            prev_log = self._bridge_log_path
            if prev_log and prev_log.exists():
                try:
                    prev_log.unlink()
                except OSError:
                    pass
            log_fd, log_path_str = tempfile.mkstemp(
                suffix='.log', prefix='meshforge-gateway-'
            )
            log_path = Path(log_path_str)
            self._bridge_log_path = log_path
            log_file = os.fdopen(log_fd, 'w')
            subprocess.Popen(
                [sys.executable, str(self.ctx.src_dir / 'gateway' / 'bridge_cli.py')],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            log_file.close()

            import time
            time.sleep(3)

            if self._is_bridge_running():
                self.ctx.dialog.msgbox("Started",
                    "Gateway bridge started in background.\n\n"
                    f"Logs: {log_path}\n\n"
                    "Use 'Stop Bridge' to shut it down.")
            else:
                try:
                    error_text = log_path.read_text()[-300:]
                except OSError as e:
                    logger.debug("Bridge log read failed: %s", e)
                    error_text = "(no log output)"
                self.ctx.dialog.msgbox("Failed",
                    f"Bridge failed to start.\n\n{error_text}")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to start bridge:\n{e}")

    def _start_bridge_foreground(self):
        """Start gateway bridge in foreground with live output."""
        if self._is_bridge_running():
            self.ctx.dialog.msgbox("Already Running",
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
                [sys.executable, str(self.ctx.src_dir / 'gateway' / 'bridge_cli.py')],
                timeout=None
            )
        except KeyboardInterrupt:
            print("\nBridge stopped.")
        try:
            self.ctx.wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _stop_bridge(self):
        """Stop the background gateway bridge."""
        if not self._is_bridge_running():
            self.ctx.dialog.msgbox("Not Running", "Gateway bridge is not running.")
            return

        if not self.ctx.dialog.yesno("Stop Bridge", "Stop the gateway bridge?"):
            return

        try:
            subprocess.run(
                ['pkill', '-f', 'bridge_cli.py'],
                capture_output=True, timeout=10
            )
            import time
            time.sleep(1)

            if self._is_bridge_running():
                subprocess.run(
                    ['pkill', '-9', '-f', 'bridge_cli.py'],
                    capture_output=True, timeout=10
                )

            self.ctx.dialog.msgbox("Stopped", "Gateway bridge stopped.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to stop bridge:\n{e}")

    def _find_bridge_log(self) -> Optional[Path]:
        """Find the gateway bridge log file."""
        if self._bridge_log_path and self._bridge_log_path.exists():
            return self._bridge_log_path

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
            self.ctx.dialog.msgbox("No Logs", "No gateway log found.")
            return

        try:
            lines = log_path.read_text().strip().split('\n')
            tail = '\n'.join(lines[-30:])
            self.ctx.dialog.msgbox(f"Bridge Status (last 30 lines)\n{log_path}", tail)
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to read log:\n{e}")

    def _show_bridge_logs(self):
        """Show full gateway bridge logs in less."""
        log_path = self._find_bridge_log()
        if not log_path:
            self.ctx.dialog.msgbox("No Logs", "No gateway log found.")
            return

        clear_screen()
        try:
            subprocess.run(['less', '-R', '-X', '+G', str(log_path)], timeout=300)
        except KeyboardInterrupt:
            pass

    def _service_menu(self):
        """Service management menu."""
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
        ]
        dispatch = {
            "install": ("Install meshtasticd", self._install_native_meshtasticd),
            "mqtt-setup": ("MQTT Setup", self._mqtt_setup_wizard),
            "openhamclock": ("OpenHamClock Docker", self._manage_openhamclock_docker),
            "status": ("Service Status", self._show_all_service_status),
            "restart-mesh": ("Restart meshtasticd", self._restart_meshtasticd_service),
            "start-rns": ("Start rnsd", self._start_rnsd_service),
            "restart-rns": ("Restart rnsd", self._restart_rnsd_service),
            "meshtasticd": ("Manage meshtasticd", self._manage_service, "meshtasticd"),
            "rnsd": ("Manage rnsd", self._manage_service, "rnsd"),
            "lock-9443": ("Port 9443 Lockdown", self._manage_port_lockdown),
        }
        self.run_menu_loop("Service Management", "Start/stop/restart services:", choices, dispatch)

    def _show_all_service_status(self):
        """Show status of all mesh services."""
        _service_extras.show_all_service_status(self)

    def _manage_port_lockdown(self):
        """Lock/unlock external access to meshtasticd port 9443."""
        choices = [
            ("lock", "Lock Port 9443       Block external access"),
            ("unlock", "Unlock Port 9443     Allow external access"),
            ("persist", "Save Rules           Survive reboot"),
            ("status", "Check Status         Current lock state"),
        ]
        dispatch = {
            "lock": ("Lock Port 9443", self._port_lockdown_lock),
            "unlock": ("Unlock Port 9443", self._port_lockdown_unlock),
            "persist": ("Save Rules", self._port_lockdown_persist),
            "status": ("Check Status", self._port_lockdown_status),
        }
        self.run_menu_loop(
            "Port 9443 Lockdown",
            "MeshForge proxies meshtasticd at :5000/mesh/\n"
            "Locking port 9443 forces traffic through MeshForge.",
            choices, dispatch,
        )

    def _port_lockdown_lock(self):
        """Lock port 9443 to block external access."""
        clear_screen()
        success, msg = lock_port_external(9443)
        if success:
            print(f"\033[0;32m✓\033[0m {msg}")
            print("\nTo survive reboot, select 'Save Rules' from the menu.")
        else:
            print(f"\033[0;31m✗\033[0m {msg}")
        self.ctx.wait_for_enter()

    def _port_lockdown_unlock(self):
        """Unlock port 9443 to allow external access."""
        clear_screen()
        success, msg = unlock_port_external(9443)
        if success:
            print(f"\033[0;32m✓\033[0m {msg}")
        else:
            print(f"\033[0;31m✗\033[0m {msg}")
        self.ctx.wait_for_enter()

    def _port_lockdown_persist(self):
        """Save iptables rules for reboot persistence."""
        clear_screen()
        print("Saving iptables rules for reboot persistence...\n")
        success, msg = persist_iptables()
        if success:
            print(f"\033[0;32m✓\033[0m {msg}")
        else:
            print(f"\033[0;31m✗\033[0m {msg}")
        self.ctx.wait_for_enter()

    def _port_lockdown_status(self):
        """Check current port 9443 lock state."""
        clear_screen()
        print("=== Port 9443 Status ===\n")
        locked = check_port_locked(9443)
        if locked:
            print("  \033[0;32m●\033[0m Port 9443: LOCKED (localhost only)")
        else:
            print("  \033[0;31m●\033[0m Port 9443: OPEN (external access allowed)")
        print()
        print("  Lock blocks external access via iptables.")
        print("  MeshForge proxies at :5000/mesh/ with filtering.")
        self.ctx.wait_for_enter()

    def _restart_meshtasticd_service(self):
        """Restart the meshtasticd service."""
        clear_screen()
        print("Restarting meshtasticd...\n")
        success, msg = apply_config_and_restart('meshtasticd')
        print(msg)
        subprocess.run(['systemctl', 'status', 'meshtasticd', '--no-pager', '-l'], timeout=10)
        self.ctx.wait_for_enter()

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
        self.ctx.wait_for_enter()

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
        self.ctx.wait_for_enter()

    def _fix_spi_config(self, has_native: bool = False):
        """Quick fix for SPI HAT with wrong USB config."""
        self.ctx.dialog.infobox("Fixing", "Removing wrong USB configuration...")

        try:
            config_dir = Path('/etc/meshtasticd')

            usb_config = config_dir / 'config.d' / 'usb-serial.yaml'
            if usb_config.exists():
                usb_config.unlink()
                self.ctx.dialog.infobox("Fixing", "Removed usb-serial.yaml from config.d/")

            config_yaml = config_dir / 'config.yaml'
            needs_config = False
            if not config_yaml.exists():
                needs_config = True
            elif not config_yaml.read_text().strip():
                needs_config = True
            elif 'Webserver:' not in config_yaml.read_text():
                self.ctx.dialog.msgbox(
                    "Config Warning",
                    f"Your config.yaml may be corrupted:\n{config_yaml}\n\n"
                    "It's missing the Webserver section.\n"
                    "Check: cat /etc/meshtasticd/config.yaml"
                )

            if needs_config:
                from core.meshtasticd_config import MeshtasticdConfig
                MeshtasticdConfig().ensure_structure()
                self.ctx.dialog.infobox("Fixing", "Created minimal config.yaml")

            if not has_native:
                if self.ctx.dialog.yesno(
                    "Install Native Daemon?",
                    "SPI HATs require the native meshtasticd daemon.\n\n"
                    "Would you like to install it now?\n\n"
                    "(This requires internet connection)"
                ):
                    self._install_native_meshtasticd()
                else:
                    self.ctx.dialog.msgbox(
                        "Config Fixed",
                        "Wrong USB config removed.\n\n"
                        "To complete setup, install native meshtasticd:\n"
                        "  sudo apt install meshtasticd\n\n"
                        "Or run: sudo bash scripts/install_noc.sh --force-native"
                    )
            else:
                apply_config_and_restart('meshtasticd')
                self.ctx.dialog.msgbox(
                    "Config Fixed",
                    "Configuration corrected!\n\n"
                    "- Removed wrong USB config\n"
                    "- Restarted meshtasticd service\n\n"
                    "Check status: sudo systemctl status meshtasticd"
                )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Fix failed:\n{e}")

    def _install_native_meshtasticd(self):
        """Install native meshtasticd for SPI HAT."""
        _service_extras.install_native_meshtasticd(self)

    def _manage_service(self, service_name: str):
        """Manage a specific service."""
        choices = [
            ("status", "Check Status"),
            ("start", "Start Service"),
            ("stop", "Stop Service"),
            ("restart", "Restart Service"),
            ("logs", "View Logs"),
        ]
        self.run_menu_loop(
            f"Manage {service_name}", f"Select action for {service_name}:",
            choices, default_handler=lambda choice: self._service_action(service_name, choice),
        )

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

    def _start_rnsd_direct(self) -> bool:
        """Start rnsd directly as a background process."""
        if check_process_running('rnsd'):
            print("rnsd is already running.")
            return True

        rnsd_path = shutil.which('rnsd')
        if not rnsd_path:
            print("\033[0;31mError:\033[0m rnsd not found in PATH.")
            print("Install Reticulum: pip install rns")
            return False

        try:
            print("Starting rnsd daemon...")
            result = subprocess.run(
                ['rnsd'],
                capture_output=True,
                text=True,
                timeout=10
            )
            import time
            time.sleep(0.5)
            if check_process_running('rnsd'):
                print("\033[0;32m✓\033[0m rnsd started successfully.")
                return True
            else:
                print(f"\033[0;31mError:\033[0m rnsd failed to start.")
                if result.stderr:
                    print(result.stderr)
                return False
        except subprocess.TimeoutExpired:
            if check_process_running('rnsd'):
                print("\033[0;32m✓\033[0m rnsd started successfully.")
                return True
            print("\033[0;31mError:\033[0m rnsd start timed out.")
            return False
        except Exception as e:
            print(f"\033[0;31mError:\033[0m Failed to start rnsd: {e}")
            return False

    def _stop_rnsd_direct(self) -> bool:
        """Stop rnsd process directly."""
        if not check_process_running('rnsd'):
            print("rnsd is not running.")
            return True

        try:
            print("Stopping rnsd...")
            subprocess.run(
                ['pkill', '-TERM', '-x', 'rnsd'],
                capture_output=True,
                timeout=10
            )
            import time
            time.sleep(0.5)
            if not check_process_running('rnsd'):
                print("\033[0;32m✓\033[0m rnsd stopped.")
                return True
            subprocess.run(['pkill', '-KILL', '-x', 'rnsd'], timeout=5)
            time.sleep(0.3)
            if not check_process_running('rnsd'):
                print("\033[0;32m✓\033[0m rnsd stopped (forced).")
                return True
            print("\033[0;31mError:\033[0m Could not stop rnsd.")
            return False
        except Exception as e:
            print(f"\033[0;31mError:\033[0m Failed to stop rnsd: {e}")
            return False

    def _service_action(self, service_name: str, action: str):
        """Perform service action using systemctl or direct process control."""
        _service_extras.service_action(self, service_name, action)

    # =========================================================================
    # OpenHamClock Docker Management (delegated to _service_extras)
    # =========================================================================

    def _manage_openhamclock_docker(self):
        """Manage OpenHamClock as a Docker container."""
        _service_extras.manage_openhamclock_docker(self)

    def _configure_openhamclock_via_settings(self):
        """Delegate OpenHamClock configuration to SettingsHandler."""
        _service_extras.configure_openhamclock_via_settings(self)

    def _is_openhamclock_running(self) -> bool:
        """Check if OpenHamClock Docker container is running."""
        return _service_extras.is_openhamclock_running(self)

    # =========================================================================
    # MQTT Setup Wizard (delegated to _service_extras)
    # =========================================================================

    def _mqtt_setup_wizard(self):
        """MQTT setup wizard - install mosquitto and configure meshtasticd."""
        _service_extras.mqtt_setup_wizard(self)
