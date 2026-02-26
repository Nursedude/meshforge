#!/usr/bin/env python3
"""
MeshForge Launcher - raspi-config Style TUI

A whiptail/dialog based launcher that works:
- Over SSH (no display required)
- On any terminal (local or remote)

Uses whiptail (Debian/Ubuntu default) with dialog fallback.
Falls back to basic terminal menu if neither available.
"""

import os
import re
import sys
import shutil
import subprocess
import logging
import traceback
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

# Ensure src directory is in path for imports when run directly
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# Ensure launcher_tui directory is in path for direct backend import
# This avoids the RuntimeWarning when run with python -m
_launcher_dir = Path(__file__).parent
if str(_launcher_dir) not in sys.path:
    sys.path.insert(0, str(_launcher_dir))

# Import version
from __version__ import __version__

# Import optional modules at module level
from utils.cli import find_meshtastic_cli
from utils.active_health_probe import get_health_probe
from utils import config_api as config_api_mod
from utils.service_check import lock_port_external
# TopologyVisualizer imported in topology_mixin.py (export functions moved there)

# Import centralized path utility - SINGLE SOURCE OF TRUTH for all paths
# See: utils/paths.py (ReticulumPaths, get_real_user_home)
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)
from utils.paths import get_real_user_home, ReticulumPaths

# Import centralized service checker - SINGLE SOURCE OF TRUTH for service status
# See: utils/service_check.py and .claude/foundations/install_reliability_triage.md
from utils.service_check import check_service, check_port, apply_config_and_restart, ServiceState, _sudo_cmd

# Import dialog backend directly (not through package namespace)
from backend import DialogBackend, clear_screen

# Import startup checks and conflict resolution (v0.4.8)
from startup_checks import StartupChecker, EnvironmentState, ServiceRunState
from conflict_resolver import check_and_resolve_conflicts

# Import mixins to reduce file size
from rf_tools_mixin import RFToolsMixin
from channel_config_mixin import ChannelConfigMixin
from ai_tools_mixin import AIToolsMixin
from meshtasticd_config_mixin import MeshtasticdConfigMixin
from site_planner_mixin import SitePlannerMixin
from service_discovery_mixin import ServiceDiscoveryMixin
from first_run_mixin import FirstRunMixin
from system_tools_mixin import SystemToolsMixin
from quick_actions_mixin import QuickActionsMixin
from emergency_mode_mixin import EmergencyModeMixin
from rns_interfaces_mixin import RNSInterfacesMixin
from nomadnet_client_mixin import NomadNetClientMixin
from meshchat_client_mixin import MeshChatClientMixin
from topology_mixin import TopologyMixin
from rf_awareness_mixin import RFAwarenessMixin
from metrics_mixin import MetricsMixin
from link_quality_mixin import LinkQualityMixin
from rns_menu_mixin import RNSMenuMixin
from aredn_mixin import AREDNMixin
from radio_menu_mixin import RadioMenuMixin
from service_menu_mixin import ServiceMenuMixin
from hardware_menu_mixin import HardwareMenuMixin
from settings_menu_mixin import SettingsMenuMixin
from logs_menu_mixin import LogsMenuMixin
from device_backup_mixin import DeviceBackupMixin
from traffic_inspector_mixin import TrafficInspectorMixin
from updates_mixin import UpdatesMixin
from mqtt_mixin import MQTTMixin
from broker_mixin import BrokerMixin
from gateway_config_mixin import GatewayConfigMixin
from favorites_mixin import FavoritesMixin
from network_tools_mixin import NetworkToolsMixin
from web_client_mixin import WebClientMixin
from node_health_mixin import NodeHealthMixin
from amateur_radio_mixin import AmateurRadioMixin
from analytics_mixin import AnalyticsMixin
from webhooks_mixin import WebhooksMixin
from messaging_mixin import MessagingMixin
from classifier_mixin import ClassifierMixin
from rnode_mixin import RNodeMixin
from latency_mixin import LatencyMixin
from dashboard_mixin import DashboardMixin
from meshcore_mixin import MeshCoreMixin
from radio_mode_mixin import RadioModeMixin
from meshcore_config_mixin import MeshCoreConfigMixin
from tactical_ops_mixin import TacticalOpsMixin
from nanovna_mixin import NanoVNAMixin


class MeshForgeLauncher(
    RFToolsMixin,
    ChannelConfigMixin,
    AIToolsMixin,
    MeshtasticdConfigMixin,
    SitePlannerMixin,
    ServiceDiscoveryMixin,
    FirstRunMixin,
    SystemToolsMixin,
    QuickActionsMixin,
    EmergencyModeMixin,
    RNSInterfacesMixin,
    NomadNetClientMixin,
    MeshChatClientMixin,
    TopologyMixin,
    RFAwarenessMixin,
    MetricsMixin,
    LinkQualityMixin,
    RNSMenuMixin,
    AREDNMixin,
    RadioMenuMixin,
    ServiceMenuMixin,
    HardwareMenuMixin,
    SettingsMenuMixin,
    LogsMenuMixin,
    DeviceBackupMixin,
    TrafficInspectorMixin,
    UpdatesMixin,
    MQTTMixin,
    BrokerMixin,
    GatewayConfigMixin,
    FavoritesMixin,
    NetworkToolsMixin,
    WebClientMixin,
    NodeHealthMixin,
    AmateurRadioMixin,
    AnalyticsMixin,
    WebhooksMixin,
    MessagingMixin,
    ClassifierMixin,
    RNodeMixin,
    LatencyMixin,
    DashboardMixin,
    MeshCoreMixin,
    RadioModeMixin,
    MeshCoreConfigMixin,
    TacticalOpsMixin,
    NanoVNAMixin,
):
    """MeshForge launcher with raspi-config style interface."""

    def __init__(self, profile=None):
        self.dialog = DialogBackend()
        self.src_dir = Path(__file__).parent.parent  # src/ directory
        self.env = self._detect_environment()
        self._setup_status_bar()
        self._meshtastic_path = None  # Cached CLI path
        self._bridge_log_path = None  # Path to active bridge log file
        self._config_api_server = None  # Config API HTTP server
        # Enhanced startup checker (v0.4.8)
        self._startup_checker = StartupChecker()
        self._env_state: Optional[EnvironmentState] = None
        # Deployment profile for menu filtering
        self._profile = profile
        self._feature_flags = getattr(profile, 'feature_flags', {}) if profile else {}

    def _feature_enabled(self, feature: str) -> bool:
        """Check if a feature is enabled in the current deployment profile.

        When no profile is set, all features are enabled (backward compatible).
        """
        if not self._feature_flags:
            return True
        return self._feature_flags.get(feature, True)

    @staticmethod
    def _wait_for_enter(msg: str = "\nPress Enter to continue...") -> None:
        """Wait for user to press Enter, handling Ctrl+C gracefully.

        Clears the screen (including scrollback) after input so that
        print() output doesn't bleed through when whiptail/dialog redraws.
        """
        try:
            input(msg)
        except (KeyboardInterrupt, EOFError):
            pass  # Clean exit on ^C
        # Clear screen + scrollback before returning to dialog menu.
        # Without this, old print output stays in scrollback and causes
        # "screen roll" — visible flash of terminal text behind the dialog.
        clear_screen()

    def _get_meshtastic_cli(self) -> str:
        """Find the meshtastic CLI binary path, with caching."""
        if self._meshtastic_path is None:
            self._meshtastic_path = find_meshtastic_cli() or 'meshtastic'
        return self._meshtastic_path

    @staticmethod
    def _validate_hostname(host: str) -> bool:
        """Validate hostname or IP address for use in network commands.

        Prevents flag injection (args starting with '-') and restricts
        to safe characters. Used before passing user input to ping,
        DNS lookup, or other network tools.
        """
        if not host or len(host) > 253:
            return False
        if host.startswith('-'):
            return False
        # Allow hostnames, IPv4, IPv6 — alphanumeric, dots, hyphens, colons
        return bool(re.match(r'^[a-zA-Z0-9.\-:]+$', host))

    @staticmethod
    def _validate_port(port_str: str) -> bool:
        """Validate a network port number string."""
        try:
            port = int(port_str)
            return 1 <= port <= 65535
        except (ValueError, TypeError):
            return False

    def _setup_status_bar(self) -> None:
        """Initialize and attach the status bar to the dialog backend."""
        try:
            from status_bar import StatusBar
            self._status_bar = StatusBar(version=__version__)
            self.dialog.set_status_bar(self._status_bar)
        except Exception:
            self._status_bar = None

    def _get_error_log_path(self) -> Path:
        """Get the path to the TUI error log file."""
        try:
            log_dir = get_real_user_home() / ".cache" / "meshforge" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir / "tui_errors.log"
        except Exception:
            return Path("/tmp/meshforge_tui_errors.log")

    def _log_error(self, context: str, exc: Exception) -> None:
        """Write error details to the TUI error log file.

        This preserves full tracebacks for debugging while keeping
        the TUI display clean for the user.

        Rotates the log when it exceeds 1 MB to prevent unbounded
        disk growth on resource-constrained systems (e.g. Pi).
        """
        try:
            import datetime
            log_path = self._get_error_log_path()

            # Rotate if log exceeds 1 MB
            _MAX_LOG_BYTES = 1_048_576
            try:
                if log_path.exists() and log_path.stat().st_size > _MAX_LOG_BYTES:
                    rotated = log_path.with_suffix('.log.1')
                    if rotated.exists():
                        rotated.unlink()
                    log_path.rename(rotated)
            except OSError:
                pass  # Rotation failure is non-critical

            with open(log_path, 'a') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{datetime.datetime.now().isoformat()}] {context}\n")
                f.write(f"Exception: {type(exc).__name__}: {exc}\n")
                f.write(traceback.format_exc())
                f.write(f"{'='*60}\n")
        except Exception:
            pass  # Logging failure must never compound the original error

    def _safe_call(self, method_name: str, method, *args, **kwargs):
        """Safely call a mixin method with exception handling.

        If the method raises an exception:
        1. Logs full traceback to the error log file
        2. Shows a user-friendly error dialog with the error summary
        3. Returns to the calling menu instead of crashing

        Args:
            method_name: Human-readable name for error messages
            method: The callable to invoke
            *args, **kwargs: Passed through to the method
        """
        try:
            return method(*args, **kwargs)
        except KeyboardInterrupt:
            # Let Ctrl+C propagate - user wants to exit
            raise
        except ImportError as e:
            module = str(e).replace("No module named ", "").strip("'\"")
            self._log_error(f"ImportError in {method_name}", e)
            self.dialog.msgbox(
                "Module Not Available",
                f"Required module not installed: {module}\n\n"
                f"This feature requires additional dependencies.\n"
                f"Try: pip3 install {module}\n\n"
                f"Details logged to:\n"
                f"  {self._get_error_log_path()}"
            )
        except subprocess.TimeoutExpired as e:
            self._log_error(f"Timeout in {method_name}", e)
            self.dialog.msgbox(
                "Operation Timed Out",
                f"{method_name} took too long to respond.\n\n"
                f"Possible causes:\n"
                f"  - Service not responding\n"
                f"  - Network connectivity issue\n"
                f"  - System under heavy load\n\n"
                f"Try checking service status from Dashboard."
            )
        except PermissionError as e:
            self._log_error(f"PermissionError in {method_name}", e)
            self.dialog.msgbox(
                "Permission Denied",
                f"Insufficient permissions for {method_name}.\n\n"
                f"{e}\n\n"
                f"Make sure MeshForge is running with sudo."
            )
        except FileNotFoundError as e:
            self._log_error(f"FileNotFoundError in {method_name}", e)
            self.dialog.msgbox(
                "File Not Found",
                f"A required file or command was not found:\n\n"
                f"{e}\n\n"
                f"The tool or file may not be installed."
            )
        except ConnectionError as e:
            self._log_error(f"ConnectionError in {method_name}", e)
            self.dialog.msgbox(
                "Connection Failed",
                f"Could not connect to service for {method_name}.\n\n"
                f"{e}\n\n"
                f"Check that the required service is running."
            )
        except Exception as e:
            self._log_error(f"Unexpected error in {method_name}", e)
            self.dialog.msgbox(
                "Error",
                f"An error occurred in {method_name}:\n\n"
                f"{type(e).__name__}: {e}\n\n"
                f"Full details logged to:\n"
                f"  {self._get_error_log_path()}\n\n"
                f"Please report this at:\n"
                f"  github.com/Nursedude/meshforge/issues"
            )

    def _detect_environment(self) -> dict:
        """Detect the current environment."""
        env = {
            'has_display': False,
            'display_type': None,
            'is_ssh': False,
            'is_root': os.geteuid() == 0,
        }

        # Check for display
        display = os.environ.get('DISPLAY')
        wayland = os.environ.get('WAYLAND_DISPLAY')
        if display or wayland:
            env['has_display'] = True
            env['display_type'] = 'Wayland' if wayland else 'X11'

        # Check for SSH
        if os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'):
            env['is_ssh'] = True

        return env

    def _is_daemon_running(self) -> bool:
        """Check if meshforged is running via PID file.

        Used on TUI startup to avoid auto-starting services the
        daemon already owns (Config API, health probe, etc.).
        """
        pid_file = Path("/run/meshforge/meshforged.pid")
        if not pid_file.exists():
            return False
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists (signal 0)
            return True
        except (ProcessLookupError, ValueError):
            return False
        except PermissionError:
            # Process exists but owned by different user — daemon is running
            return True

    def run(self):
        """Run the launcher."""
        if not self.dialog.available:
            # Fallback to basic launcher
            print("whiptail/dialog not available, using basic launcher...")
            self._run_basic_launcher()
            return

        # Check for root without SUDO_USER (causes RNS auth issues)
        self._check_root_without_sudo_user()

        # Run startup environment checks (v0.4.8)
        if not self._run_startup_checks():
            return  # User aborted due to conflicts

        # Check for first run and offer setup wizard
        if self._check_first_run():
            self._run_first_run_wizard()

        # Check for service misconfiguration (SPI HAT with USB config)
        self._check_service_misconfig()

        # Detect if daemon is managing core services
        self._daemon_active = self._is_daemon_running()
        if self._daemon_active:
            logger.info("Daemon detected — TUI running in tool-only mode")
        else:
            # Only auto-start services when daemon ISN'T running.
            # If daemon owns these, starting them here would cause
            # port conflicts (Config API :8081) or singleton clashes.
            self._maybe_auto_start_map()
            self._maybe_auto_start_mqtt_and_telemetry()
            self._maybe_auto_start_config_api()
            self._maybe_auto_lock_port()
            self._start_health_monitor()

        try:
            self._run_main_menu()
        finally:
            if not self._daemon_active:
                self._stop_health_monitor()
                self._stop_config_api_server()

    def _start_health_monitor(self) -> None:
        """Start the background health monitoring loop.

        Uses the singleton ActiveHealthProbe which checks meshtasticd,
        rnsd, and mosquitto every 30 seconds. State changes are pushed
        to the EventBus, which the StatusBar subscribes to.
        """
        try:
            self._health_probe = get_health_probe(interval=30, fails=3, passes=2)
            self._health_probe.start()
            logger.info("Health monitor started (30s interval)")
        except Exception as e:
            logger.warning(f"Failed to start health monitor: {e}")
            self._health_probe = None

    def _stop_health_monitor(self) -> None:
        """Stop the background health monitoring loop."""
        probe = getattr(self, '_health_probe', None)
        if probe:
            probe.stop(timeout=3)
            logger.info("Health monitor stopped")

    def _run_startup_checks(self) -> bool:
        """
        Run startup environment checks and conflict resolution.

        Returns:
            True to continue, False if user aborted
        """
        if not self._startup_checker:
            return True

        # Get environment state
        self._env_state = self._startup_checker.check_all()

        # Update status bar with environment info
        if self._status_bar and hasattr(self._status_bar, '_env_state'):
            self._status_bar._env_state = self._env_state

        # Check for port conflicts
        if self._env_state.conflicts:
            if not check_and_resolve_conflicts(self.dialog, self._startup_checker):
                return False  # User aborted

            # Re-check after resolution
            self._startup_checker.invalidate_cache()
            self._env_state = self._startup_checker.check_all()

        # Show alerts if any (non-blocking)
        alerts = self._env_state.get_alerts()
        if alerts and len(alerts) <= 3:
            # Show a quick info message for minor issues
            alert_text = "\n".join(f"  - {a}" for a in alerts)
            self.dialog.msgbox(
                "Startup Notes",
                f"Environment check found:\n\n{alert_text}\n\n"
                "These are informational - press Enter to continue."
            )

        return True

    def _check_root_without_sudo_user(self):
        """
        Warn if running as root without SUDO_USER set.

        This is a common issue on fresh installs where the user follows
        'sudo meshforge' guidance but the environment doesn't preserve
        SUDO_USER (e.g., after 'su -' or direct root login).

        Without SUDO_USER, RNS applications (NomadNet, rnstatus) will run
        as root while rnsd runs as the regular user, causing RPC auth failures.
        """
        # Only check if we're actually root
        if os.getuid() != 0:
            return

        sudo_user = os.environ.get('SUDO_USER', '')

        # SUDO_USER is set and not root - we're fine
        if sudo_user and sudo_user != 'root':
            return

        # We're root without SUDO_USER - this can cause issues
        # Check if rnsd is running as a non-root user (the problematic case)
        rnsd_user = None
        try:
            result = subprocess.run(
                ['ps', '-o', 'user=', '-C', 'rnsd'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                rnsd_user = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            # ps command failed - non-critical, skip user mismatch check
            logger.debug(f"Could not check rnsd user: {e}")

        # If rnsd is running as a regular user, warn about the mismatch
        if rnsd_user and rnsd_user != 'root':
            self.dialog.msgbox(
                "Root Context Warning",
                f"MeshForge is running as root, but rnsd runs as '{rnsd_user}'.\n\n"
                f"This mismatch will cause RNS apps (NomadNet) to fail\n"
                f"with RPC authentication errors.\n\n"
                f"Recommended: Exit and run as your regular user:\n"
                f"  exit\n"
                f"  meshforge   (without sudo)\n\n"
                f"Or preserve SUDO_USER:\n"
                f"  sudo -E meshforge\n\n"
                f"MeshForge will try to work around this, but some\n"
                f"features may not work correctly.",
            )
        elif not rnsd_user:
            # rnsd not running yet - just a general warning
            # Only show this once per session using a flag
            if not hasattr(self, '_root_warning_shown'):
                self._root_warning_shown = True
                # Less alarming message since rnsd isn't running yet
                # The NomadNet menu will handle specific issues when they arise

    def _check_service_misconfig(self):
        """Check for service misconfiguration and offer to fix."""
        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            return

        # Check what configs are active
        active_configs = list(config_d.glob('*.yaml'))
        usb_config = config_d / 'usb-serial.yaml'

        # Check for SPI configs
        spi_config_names = ['meshadv', 'waveshare', 'rak-hat', 'meshtoad', 'sx126', 'sx127', 'lora']
        has_spi_config = any(
            any(name in cfg.name.lower() for name in spi_config_names)
            for cfg in active_configs
        )

        # If SPI config exists AND usb-serial.yaml also exists, that's wrong
        if has_spi_config and usb_config.exists():
            spi_configs = [c.name for c in active_configs if any(n in c.name.lower() for n in spi_config_names)]

            msg = "CONFLICTING CONFIGURATIONS!\n\n"
            msg += "Both SPI HAT and USB configs are active:\n\n"
            msg += f"  SPI: {', '.join(spi_configs)}\n"
            msg += f"  USB: usb-serial.yaml (WRONG)\n\n"
            msg += "Remove the USB config?"

            if self.dialog.yesno("Config Conflict", msg):
                try:
                    usb_config.unlink()
                    apply_config_and_restart('meshtasticd')
                    self.dialog.msgbox(
                        "Fixed",
                        "Removed usb-serial.yaml\n"
                        "Restarted meshtasticd\n\n"
                        "Check: systemctl status meshtasticd"
                    )
                except Exception as e:
                    self.dialog.msgbox("Error", f"Failed:\n{e}")
            return

        # Check: SPI hardware present but USB config active (wrong)
        spi_devices = list(Path('/dev').glob('spidev*'))

        has_spi = len(spi_devices) > 0

        # Only skip if no SPI hardware at all
        if not has_spi:
            return

        if not usb_config.exists():
            return

        result = subprocess.run(['which', 'meshtasticd'], capture_output=True, timeout=5)
        has_native = result.returncode == 0

        msg = "CONFIGURATION MISMATCH!\n\n"
        msg += "SPI HAT detected but USB config active.\n\n"
        msg += f"SPI: {', '.join(d.name for d in spi_devices)}\n"
        msg += "Config: usb-serial.yaml (WRONG)\n"
        if not has_native:
            msg += "Native meshtasticd: NOT INSTALLED\n"
        msg += "\nFix this now?"

        if self.dialog.yesno("Service Misconfiguration", msg):
            self._fix_spi_config(has_native)

    def _maybe_auto_start_config_api(self):
        """Auto-start Config API Server on TUI launch.

        Provides RESTful configuration API on localhost:8081.
        Silent operation - no dialogs on failure.
        """
        try:
            create_gateway_config_api = config_api_mod.create_gateway_config_api
            ConfigAPIServer = config_api_mod.ConfigAPIServer
            api = create_gateway_config_api()
            self._config_api_server = ConfigAPIServer(api, host="127.0.0.1", port=8081)
            if self._config_api_server.start():
                logger.info("Config API server started on 127.0.0.1:8081")
            else:
                logger.debug("Config API server failed to start")
                self._config_api_server = None
        except Exception as e:
            logger.debug("Config API auto-start failed: %s", e)
            self._config_api_server = None

    def _stop_config_api_server(self):
        """Stop the Config API Server on TUI exit."""
        if self._config_api_server and self._config_api_server.is_running:
            try:
                self._config_api_server.stop()
                logger.info("Config API server stopped")
            except Exception as e:
                logger.debug("Config API stop failed: %s", e)
            self._config_api_server = None

    def _maybe_auto_lock_port(self):
        """Auto-lock port 9443 on startup so meshtasticd web is MeshForge-only.

        Silent operation - logs result but no dialogs on failure.
        """
        try:
            success, msg = lock_port_external(9443)
            if success:
                logger.info("Startup port lock: %s", msg)
            else:
                logger.warning("Startup port lock failed: %s", msg)
        except Exception as e:
            logger.debug("Auto port lock error: %s", e)

    def _config_api_menu(self):
        """Config API Server start/stop/status menu."""
        while True:
            running = self._config_api_server and self._config_api_server.is_running
            status = "RUNNING on 127.0.0.1:8081" if running else "STOPPED"

            choices = [
                ("status", f"Status              {status}"),
            ]
            if running:
                choices.append(("stop", "Stop Config API Server"))
            else:
                choices.append(("start", "Start Config API Server"))
            choices.append(("back", "Back"))

            choice = self.dialog.menu(
                "Config API Server",
                "RESTful configuration API for dynamic reconfiguration.\n\n"
                f"Status: {status}",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                if running:
                    self.dialog.msgbox(
                        "Config API Status",
                        "Config API Server is RUNNING\n\n"
                        "  Endpoint: http://127.0.0.1:8081/config\n"
                        "  GET /config/<path> - Read config value\n"
                        "  PUT /config/<path> - Set config value\n"
                        "  DELETE /config/<path> - Remove value\n"
                        "  GET /config/_paths - List all paths\n"
                        "  GET /config/_audit - Audit log"
                    )
                else:
                    self.dialog.msgbox(
                        "Config API Status",
                        "Config API Server is STOPPED\n\n"
                        "Start it to enable dynamic reconfiguration\n"
                        "via RESTful API."
                    )
            elif choice == "start":
                self._maybe_auto_start_config_api()
                if self._config_api_server and self._config_api_server.is_running:
                    self.dialog.msgbox("Started", "Config API Server started on 127.0.0.1:8081")
                else:
                    self.dialog.msgbox("Error", "Failed to start Config API Server.\nCheck logs for details.")
            elif choice == "stop":
                self._stop_config_api_server()
                self.dialog.msgbox("Stopped", "Config API Server stopped.")

    def _run_main_menu(self):
        """Display the main NOC menu.

        Redesigned in v0.4.8 to follow UI/UX best practices:
        - Max 10 items per menu (cognitive load)
        - Grouped by user task, not technical domain
        - 2-tap max for common operations
        """
        while True:
            # Build status hint for menu subtitle
            status_hint = self._get_menu_status_hint()

            choices = [
                # Primary Operations (numbered for quick access)
                ("1", "Dashboard           Status, health, alerts"),
                ("2", "Mesh Networks       Meshtastic, RNS, AREDN"),
                ("3", "RF & SDR            Calculators, SDR monitoring"),
            ]
            if self._feature_enabled("maps"):
                choices.append(("4", "Maps & Viz          Coverage maps, topology"))
            choices.append(("5", "Configuration       Radio, services, settings"))
            choices.append(("6", "System              Hardware, logs, Linux tools"))
            # Quick Access
            if self._feature_enabled("tactical"):
                choices.append(("t", "Tactical Ops        SITREP, zones, QR, ATAK"))
            choices.extend([
                ("q", "Quick Actions       Common shortcuts"),
                ("e", "Emergency Mode      Field operations"),
                # Meta
                ("a", "About               Version, help, web client"),
                ("x", "Exit"),
            ])

            choice = self.dialog.menu(
                f"MeshForge NOC v{__version__}",
                status_hint,
                choices
            )

            if choice is None or choice == "x":
                break

            self._handle_main_choice(choice)

    def _get_menu_status_hint(self) -> str:
        """Generate status hint for main menu subtitle.

        Uses plain text indicators (UP/FAIL/--) since whiptail/dialog
        don't render ANSI color escape codes.
        """
        if self._env_state:
            return self._env_state.get_status_line(plain=True)
        return "Network Operations Center"

    def _handle_main_choice(self, choice: str):
        """Handle main menu selection (v0.4.8 restructured).

        All dispatches go through _safe_call to ensure unhandled
        exceptions in any mixin show a user-friendly error dialog
        instead of crashing the TUI.
        """
        dispatch = {
            "1": ("Dashboard", self._dashboard_menu),
            "2": ("Mesh Networks", self._mesh_networks_menu),
            "3": ("RF & SDR Tools", self._rf_sdr_menu),
            "4": ("Maps & Visualization", self._maps_viz_menu),
            "5": ("Configuration", self._configuration_menu),
            "6": ("System Tools", self._system_menu),
            "t": ("Tactical Ops", self._tactical_ops_menu),
            "q": ("Quick Actions", self._quick_actions_menu),
            "e": ("Emergency Mode", self._emergency_mode),
            "a": ("About", self._about_menu),
        }
        entry = dispatch.get(choice)
        if entry:
            name, method = entry
            self._safe_call(name, method)

    # --- Submenu: Dashboard (1) ---

    def _dashboard_menu(self):
        """Dashboard - Status, health, alerts."""
        while True:
            choices = [
                ("status", "Service Status      All services with health"),
                ("network", "Network Status      Ports, interfaces, conflicts"),
                ("nodes", "Node Count          Meshtastic + RNS nodes"),
                ("health", "Node Health         Battery, signal, latency"),
                ("score", "Health Score        Network health snapshot"),
                ("datapath", "Data Path Check     Test all data sources"),
                ("metrics", "Historical Trends   Metrics over time"),
                ("analytics", "Analytics           Coverage & link trends"),
                ("latency", "Latency Monitor     Service response times"),
                ("reports", "Reports             Generate status report"),
                ("alerts", "View Alerts         Current warnings"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Dashboard",
                "System status and monitoring:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("Service Status", self._service_status_display),
                "network": ("Network Status", self._network_menu),
                "nodes": ("Node Count", self._show_node_counts),
                "health": ("Node Health", self._node_health_menu),
                "score": ("Health Score", self._health_score_display),
                "datapath": ("Data Path Check", self._data_path_diagnostic),
                "metrics": ("Historical Trends", self._metrics_menu),
                "analytics": ("Analytics", self._analytics_menu),
                "latency": ("Latency Monitor", self._latency_menu),
                "reports": ("Reports", self._reports_menu),
                "alerts": ("View Alerts", self._show_alerts),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    # --- Submenu: Mesh Networks (2) ---

    def _mesh_networks_menu(self):
        """Mesh Networks - Meshtastic, RNS, AREDN."""
        while True:
            choices = []
            choices.append(("radio_mode", "Radio Mode          Select primary radio (Meshtastic/MeshCore)"))
            if self._feature_enabled("meshtastic"):
                choices.append(("meshtastic", "Meshtastic          Radio, channels, CLI"))
            if self._feature_enabled("meshcore"):
                choices.append(("meshcore", "MeshCore            Companion radio, status"))
            choices.append(("mc_config", "MeshCore Config     /etc/meshcore/ management"))
            if self._feature_enabled("rns"):
                choices.append(("rns", "RNS / Reticulum     Status, gateway, messaging"))
            if self._feature_enabled("gateway"):
                choices.append(("gateway", "Gateway Bridge      RNS-Meshtastic-MeshCore"))
            choices.append(("aredn", "AREDN Mesh          AREDN integration"))
            choices.append(("messaging", "Messaging           Send/receive messages"))
            choices.append(("traffic", "Traffic Classifier  Routing & notification stats"))
            if self._feature_enabled("mqtt"):
                choices.append(("mqtt", "MQTT Monitor        Nodeless mesh observation"))
            choices.append(("favorites", "Favorites           Manage favorite nodes"))
            choices.append(("ham", "Ham Radio           Callsign, Part 97, ARES"))
            choices.append(("services", "Service Control     Start/stop/restart"))
            choices.append(("back", "Back"))

            choice = self.dialog.menu(
                "Mesh Networks",
                "Manage mesh network connections:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "radio_mode": ("Radio Mode", self._radio_mode_menu),
                "meshtastic": ("Meshtastic Radio", self._radio_menu),
                "meshcore": ("MeshCore Radio", self._meshcore_menu),
                "mc_config": ("MeshCore Config", self._meshcore_config_menu),
                "rns": ("RNS / Reticulum", self._rns_menu),
                "gateway": ("Gateway Bridge", self._gateway_config_menu),
                "aredn": ("AREDN Mesh", self._aredn_menu),
                "messaging": ("Messaging", self._messaging_menu),
                "traffic": ("Traffic Classifier", self._classifier_menu),
                "mqtt": ("MQTT Monitor", self._mqtt_menu),
                "favorites": ("Favorites", self._favorites_menu),
                "ham": ("Ham Radio Tools", self._amateur_radio_menu),
                "services": ("Service Control", self._service_menu),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    # --- NEW Submenu: RF & SDR (3) ---

    def _rf_sdr_menu(self):
        """RF & SDR - Calculators, SDR monitoring."""
        while True:
            choices = [
                ("link", "Link Budget         FSPL, Fresnel, range"),
                ("site", "Site Planner        Coverage estimation"),
                ("freq", "Frequency Slots     Channel calculator"),
                ("antenna", "Antenna Analysis    Compare antenna types"),
                ("nanovna", "NanoVNA Analyzer    SWR, impedance, sweeps"),
                ("sdr", "SDR Monitor         RF awareness (Airspy)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RF & SDR Tools",
                "Radio frequency tools and monitoring:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "link": ("Link Budget", self._rf_tools_menu),
                "site": ("Site Planner", self._site_planner_menu),
                "freq": ("Frequency Slots", self._calc_frequency_slot),
                "antenna": ("Antenna Analysis", self._antenna_comparison),
                "nanovna": ("NanoVNA Analyzer", self._nanovna_menu),
                "sdr": ("SDR Monitor", self._rf_awareness_menu),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    # --- NEW Submenu: Maps & Viz (4) ---

    def _maps_viz_menu(self):
        """Maps & Visualization - Coverage maps, topology."""
        while True:
            choices = [
                ("livemap", "Live NOC Map        Real-time browser view"),
                ("coverage", "Coverage Map        Generate coverage map"),
                ("heatmap", "Heatmap             Node density heatmap"),
                ("tiles", "Offline Tiles       Cache map tiles"),
                ("topology", "Network Topology    D3.js graph view"),
                ("traffic", "Traffic Inspector   Packet capture & analysis"),
                ("quality", "Link Quality        Quality analysis"),
                ("export", "Export Data         GeoJSON, CSV, GraphML"),
                ("ai", "AI Diagnostics      Knowledge base, assistant"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Maps & Visualization",
                "Network visualization tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "livemap": ("Live NOC Map", self._open_live_map),
                "coverage": ("Coverage Map", self._generate_coverage_map),
                "heatmap": ("Heatmap", self._generate_heatmap),
                "tiles": ("Offline Tile Cache", self._tile_cache_menu),
                "topology": ("Network Topology", self._topology_menu),
                "traffic": ("Traffic Inspector", self.menu_traffic_inspector),
                "quality": ("Link Quality", self._link_quality_menu),
                "export": ("Export Data", self._export_data_menu),
                "ai": ("AI Diagnostics", self._ai_tools_menu),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    # --- NEW Submenu: Configuration (5) ---

    def _configuration_menu(self):
        """Configuration - Radio, services, settings."""
        while True:
            choices = [
                ("radio", "Radio Config        meshtasticd settings"),
                ("channels", "Channel Config      Meshtastic channels"),
                ("rns-config", "RNS Config          Reticulum settings"),
                ("services", "Service Config      systemd services"),
                ("backup", "Device Backup       Backup/restore configs"),
                ("updates", "Software Updates    One-click updates"),
                ("webhooks", "Webhooks            External notifications"),
                ("meshforge", "MeshForge Settings  App preferences"),
                ("config-api", "Config API Server   REST config endpoint"),
                ("wizard", "Setup Wizard        First-run wizard"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Configuration",
                "System and service configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "radio": ("Radio Config", self._config_menu),
                "channels": ("Channel Config", self._channel_config_menu),
                "rns-config": ("RNS Config", self._edit_rns_config),
                "services": ("Service Config", self._service_menu),
                "backup": ("Device Backup", self._device_backup_menu),
                "updates": ("Software Updates", self._updates_menu),
                "webhooks": ("Webhooks", self._webhooks_menu),
                "meshforge": ("MeshForge Settings", self._settings_menu),
                "config-api": ("Config API Server", self._config_api_menu),
                "wizard": ("Setup Wizard", self._run_first_run_wizard),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    # --- NEW Submenu: System (6) ---

    def _system_menu(self):
        """System - Hardware, logs, Linux tools."""
        while True:
            choices = [
                ("hardware", "Hardware            Detect SPI/I2C/USB"),
                ("logs", "Logs                View/follow logs"),
                ("network", "Network Tools       Ping, ports, interfaces"),
                ("diagnose", "Diagnostics         System health check"),
                ("daemon", "Daemon Mode         Start/stop headless NOC"),
                ("review", "Code Review         Auto-review codebase"),
                ("status", "Quick Status        One-shot status display"),
                ("shell", "Linux Shell         Drop to bash"),
                ("reboot", "Reboot/Shutdown     Safe system control"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "System Tools",
                "System administration:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "hardware": ("Hardware Detection", self._hardware_menu),
                "logs": ("Log Viewer", self._logs_menu),
                "network": ("Network Tools", self._network_menu),
                "diagnose": ("Diagnostics", self._run_diagnostics),
                "daemon": ("Daemon Mode", self._daemon_menu),
                "review": ("Code Review", self._auto_review_menu),
                "status": ("Quick Status", self._run_terminal_status),
                "shell": ("Linux Shell", self._drop_to_shell),
                "reboot": ("Reboot/Shutdown", self._reboot_menu),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _daemon_menu(self):
        """Daemon Mode - Start/stop headless NOC services."""
        while True:
            # Check if daemon is running
            daemon_status = "unknown"
            try:
                status_file = get_real_user_home() / ".config" / "meshforge" / "daemon_status.json"
                pid_file = Path("/run/meshforge/meshforged.pid")
                if pid_file.exists():
                    import signal as _sig
                    pid = int(pid_file.read_text().strip())
                    try:
                        os.kill(pid, 0)
                        daemon_status = f"running (PID {pid})"
                    except ProcessLookupError:
                        daemon_status = "stopped (stale PID)"
                else:
                    daemon_status = "stopped"
            except Exception:
                daemon_status = "unknown"

            choices = [
                ("status", f"Status              Daemon: {daemon_status}"),
                ("start", "Start Daemon        Launch headless NOC"),
                ("stop", "Stop Daemon         Stop headless NOC"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Daemon Mode",
                "Headless NOC service manager:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._daemon_show_status()
            elif choice == "start":
                self._daemon_start()
            elif choice == "stop":
                self._daemon_stop()

    def _daemon_show_status(self):
        """Show daemon status in a dialog."""
        try:
            status_file = get_real_user_home() / ".config" / "meshforge" / "daemon_status.json"
            if not status_file.exists():
                self.dialog.msgbox("Daemon Status", "No status file found.\nDaemon may not be running.")
                return

            import json
            with open(status_file, 'r') as f:
                data = json.load(f)

            daemon = data.get("daemon", {})
            services = data.get("services", {})
            uptime = daemon.get("uptime_seconds", 0)
            hours = uptime // 3600
            minutes = (uptime % 3600) // 60

            lines = [
                f"Status:  {daemon.get('status', '?')}",
                f"PID:     {daemon.get('pid', '?')}",
                f"Profile: {daemon.get('profile', '?')}",
                f"Uptime:  {hours}h {minutes}m",
                "",
                "Services:",
            ]

            for name, svc in services.items():
                alive = svc.get("alive", False)
                marker = "*" if alive else "-"
                lines.append(f"  {marker} {name}")

            self.dialog.msgbox("Daemon Status", "\n".join(lines))

        except Exception as e:
            self.dialog.msgbox("Error", f"Could not read daemon status:\n{e}")

    def _daemon_start(self):
        """Start the daemon via subprocess."""
        if not self.dialog.yesno(
            "Start Daemon",
            "Start MeshForge daemon (headless mode)?\n\n"
            "This will run gateway bridge, health monitoring,\n"
            "and other configured services in the background."
        ):
            return

        try:
            daemon_script = self.src_dir / "daemon.py"
            subprocess.Popen(
                [sys.executable, str(daemon_script), "start", "--foreground"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.dialog.msgbox("Daemon Started", "Daemon launched in background.\nCheck status for details.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to start daemon:\n{e}")

    def _daemon_stop(self):
        """Stop the daemon via subprocess."""
        try:
            daemon_script = self.src_dir / "daemon.py"
            result = subprocess.run(
                [sys.executable, str(daemon_script), "stop"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout.strip() or result.stderr.strip() or "Stop signal sent."
            self.dialog.msgbox("Stop Daemon", output)
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to stop daemon:\n{e}")

    def _drop_to_shell(self):
        """Drop to a bash shell."""
        clear_screen()
        print("Dropping to shell. Type 'exit' to return to MeshForge.\n")
        subprocess.run(['bash'], check=False)  # Interactive shell - no timeout

    def _reboot_menu(self):
        """Safe reboot/shutdown options."""
        while True:
            choices = [
                ("reboot", "Reboot              Restart system"),
                ("shutdown", "Shutdown            Power off"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Reboot / Shutdown",
                "System power options:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "reboot":
                if self.dialog.yesno("Confirm Reboot", "Reboot the system now?"):
                    subprocess.run(_sudo_cmd(['systemctl', 'reboot']), timeout=30)
            elif choice == "shutdown":
                if self.dialog.yesno("Confirm Shutdown", "Shutdown the system now?"):
                    subprocess.run(_sudo_cmd(['systemctl', 'poweroff']), timeout=30)

    # --- NEW Submenu: About (a) ---

    def _about_menu(self):
        """About - Version, help, web client."""
        while True:
            choices = [
                ("version", "Version Info        MeshForge version"),
                ("web", "Web Client          Open web interface"),
                ("help", "Help                Documentation"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "About MeshForge",
                "Information and help:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "version": ("Version Info", self._show_about),
                "web": ("Web Client", self._open_web_client),
                "help": ("Help", self._show_help),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _show_help(self):
        """Show help documentation."""
        help_text = """
MeshForge - Network Operations Center

KEYBOARD SHORTCUTS:
  1-6     Quick access to main sections
  q       Quick Actions
  e       Emergency Mode
  a       About
  x       Exit

NAVIGATION:
  Enter   Select item
  Esc     Go back / Cancel
  Tab     Move between buttons

DOCUMENTATION:
  https://github.com/Nursedude/meshforge

SUPPORT:
  Issues: github.com/Nursedude/meshforge/issues
"""
        clear_screen()
        print(help_text)
        self._wait_for_enter()

    # --- Config Menu - meshtasticd config.d/ management ---

    def _ensure_meshtasticd_config(self):
        """Auto-create /etc/meshtasticd structure and templates if missing."""
        try:
            from core.meshtasticd_config import MeshtasticdConfig
            MeshtasticdConfig().ensure_structure()
        except PermissionError:
            logger.debug("Cannot auto-create meshtasticd config (no root)")
        except Exception as e:
            logger.debug("meshtasticd config auto-create failed: %s", e)

    def _config_menu(self):
        """Configuration management for meshtasticd."""
        # Auto-create /etc/meshtasticd structure if missing
        self._ensure_meshtasticd_config()

        while True:
            choices = [
                ("view", "View Active Config"),
                ("overlays", "View config.d/ Overlays"),
                ("available", "Available Hardware Configs"),
                ("presets", "LoRa Presets"),
                ("channels", "Channel Configuration"),
                ("meshtasticd", "Advanced meshtasticd Config"),
                ("settings", "MeshForge Settings"),
                ("wizard", "Run Setup Wizard"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Configuration",
                "meshtasticd & MeshForge configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "view": ("View Active Config", self._view_active_config),
                "overlays": ("Config Overlays", self._view_config_overlays),
                "available": ("Available Hardware Configs", self._view_available_configs),
                "presets": ("LoRa Presets", self._radio_presets_menu),
                "channels": ("Channel Config", self._channel_config_menu),
                "meshtasticd": ("Advanced Config", self._meshtasticd_menu),
                "settings": ("MeshForge Settings", self._settings_menu),
                "wizard": ("Setup Wizard", self._run_first_run_wizard),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _view_active_config(self):
        """Show the active meshtasticd config.yaml."""
        clear_screen()
        print("=== meshtasticd config.yaml ===\n")

        config_path = Path('/etc/meshtasticd/config.yaml')

        # Auto-create if missing
        if not config_path.exists():
            self._ensure_meshtasticd_config()

        if config_path.exists():
            print(f"File: {config_path}\n")
            try:
                print(config_path.read_text())
            except PermissionError:
                print("Permission denied. Try: sudo cat /etc/meshtasticd/config.yaml")
        else:
            print("config.yaml not found!\n")
            print("Run MeshForge with sudo to auto-create:")
            print("  sudo python3 src/launcher_tui/main.py")
            print("\nOr create manually:")
            print("  sudo mkdir -p /etc/meshtasticd/{available.d,config.d}")
            print("  sudo cp templates/config.yaml /etc/meshtasticd/")
            print("  sudo cp templates/available.d/*.yaml /etc/meshtasticd/available.d/")

        self._wait_for_enter()

    def _view_config_overlays(self):
        """Show config.d/ overlay files (active hardware configs)."""
        clear_screen()
        print("=== config.d/ Active Hardware Configs ===\n")

        config_d = Path('/etc/meshtasticd/config.d')

        # Auto-create if missing
        if not config_d.exists():
            self._ensure_meshtasticd_config()

        if not config_d.exists():
            print("config.d/ directory not found.")
            print("\nRun with sudo to auto-create, or:")
            print("  sudo mkdir -p /etc/meshtasticd/config.d")
            self._wait_for_enter()
            return

        overlays = sorted(config_d.glob('*.yaml'))
        if not overlays:
            print("No active hardware configs in config.d/\n")
            print("Select your hardware from:")
            print("  Configuration > Available Hardware Configs")
            print("  Configuration > Advanced meshtasticd Config > Hardware Config")
        else:
            print(f"Found {len(overlays)} active config(s):\n")
            for f in overlays:
                size = f.stat().st_size
                print(f"  {f.name} ({size} bytes)")

            # Show contents of each
            print("\n" + "=" * 50)
            for f in overlays:
                print(f"\n--- {f.name} ---")
                try:
                    print(f.read_text())
                except PermissionError:
                    print("  (permission denied)")

        self._wait_for_enter()

    def _view_available_configs(self):
        """Show available hardware configs (USB + SPI HATs)."""
        clear_screen()
        print("=== Available Hardware Configs ===\n")

        available_d = Path('/etc/meshtasticd/available.d')

        # Auto-create if missing
        if not available_d.exists():
            self._ensure_meshtasticd_config()

        if not available_d.exists():
            print("available.d/ not found.\n")
            print("Run with sudo to auto-create, or:")
            print("  sudo mkdir -p /etc/meshtasticd/available.d")
            print("  sudo cp templates/available.d/*.yaml /etc/meshtasticd/available.d/")
            self._wait_for_enter()
            return

        configs = sorted(available_d.glob('*.yaml'))
        if not configs:
            print("No hardware configs available.")
        else:
            # Categorize USB vs SPI
            usb_configs = [f for f in configs if '-usb' in f.stem or f.stem.startswith('usb-')]
            spi_configs = [f for f in configs if f not in usb_configs]

            if usb_configs:
                print(f"USB Radios ({len(usb_configs)}):")
                for i, f in enumerate(usb_configs, 1):
                    print(f"  {i:2d}. {f.stem}")

            if spi_configs:
                if usb_configs:
                    print()
                print(f"SPI HATs ({len(spi_configs)}):")
                for i, f in enumerate(spi_configs, 1):
                    print(f"  {i:2d}. {f.stem}")

            # Show active
            config_d = Path('/etc/meshtasticd/config.d')
            if config_d.exists():
                active = list(config_d.glob('*.yaml'))
                if active:
                    print(f"\nActive: {', '.join(f.stem for f in active)}")

            print(f"\nTotal: {len(configs)} templates")
            print("\nActivate via: Configuration > Advanced meshtasticd Config > Hardware Config")

        self._wait_for_enter()

    # --- Terminal-native utilities ---

    def _run_diagnostics(self):
        """Run the MeshForge diagnostic tool."""
        clear_screen()
        try:
            result = subprocess.run(
                [sys.executable, str(self.src_dir / 'cli' / 'diagnose.py')],
                timeout=30
            )
            if result.returncode != 0:
                print("\nDiagnostics encountered an error.")
        except subprocess.TimeoutExpired:
            print("\n\nDiagnostics timed out (30s).")
        except FileNotFoundError:
            print("\nDiagnostic tool not found at: src/cli/diagnose.py")
        except KeyboardInterrupt:
            print("\n\nAborted.")

        try:
            self._wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()

    def _run_terminal_status(self):
        """Run meshforge-status (terminal-native one-shot status)."""
        clear_screen()
        try:
            # Run status script directly, showing output in real-time
            result = subprocess.run(
                [sys.executable, str(self.src_dir / 'cli' / 'status.py')],
                timeout=20
            )
            if result.returncode != 0:
                print("\nStatus check encountered an error.")
        except subprocess.TimeoutExpired:
            print("\n\nStatus check timed out (20s).")
        except KeyboardInterrupt:
            print("\n\nAborted.")

        try:
            self._wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()

    def _show_about(self):
        """Show about information."""
        text = f"""MeshForge v{__version__}
Network Operations Center

Bridges Meshtastic and Reticulum (RNS) mesh networks.

Features:
- Service management
- Hardware detection
- Space weather & propagation
- Gateway bridge (Mesh ↔ RNS)
- Node monitoring

GitHub: github.com/Nursedude/meshforge
License: GPL-3.0

Made with aloha for the mesh community
73 de WH6GXZ"""

        self.dialog.msgbox("About MeshForge", text)

    def _run_basic_launcher(self):
        """Fallback basic terminal launcher."""
        # Import and run the original launcher
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "launcher",
            self.src_dir / "launcher.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()


def main():
    """Main entry point."""
    # Suppress logging output that would corrupt the TUI display
    # Redirect to file so errors can still be debugged
    import logging
    import os
    import datetime

    # Suppress CONSOLE logging to prevent TUI corruption, but keep file
    # handlers active so errors are still captured in log files.
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(logging.CRITICAL)
    for name in logging.root.manager.loggerDict:
        lgr = logging.getLogger(name)
        for handler in lgr.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(logging.CRITICAL)

    # Redirect stderr to log file to prevent TUI corruption
    log_dir = Path("/tmp")
    try:
        from utils.paths import get_real_user_home
        log_dir = get_real_user_home() / ".cache" / "meshforge" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.debug("Could not create log dir, falling back to /tmp")

    stderr_log = log_dir / "tui_errors.log"
    _original_stderr = sys.stderr

    # Last-resort exception hook — catches crashes that bypass try/except
    _original_excepthook = sys.excepthook

    def _crash_hook(exc_type, exc_value, exc_tb):
        try:
            with open(stderr_log, 'a') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{datetime.datetime.now().isoformat()}] "
                        f"UNHANDLED {exc_type.__name__}\n")
                traceback.print_exception(
                    exc_type, exc_value, exc_tb, file=f)
                f.write(f"{'='*60}\n")
                f.flush()
        except Exception:
            pass
        _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_hook

    # Show log path before stderr redirect so user knows where to look
    print(f"  Log: {stderr_log}", file=_original_stderr)

    _stderr_file = None
    try:
        _stderr_file = open(stderr_log, 'a')  # noqa: SIM115 — long-lived redirect
        sys.stderr = _stderr_file
    except Exception:
        logger.debug("Could not redirect stderr, keeping original")

    launcher = None
    exit_code = 0
    try:
        launcher = MeshForgeLauncher()
        launcher.run()
    except KeyboardInterrupt:
        print("\n\nExiting MeshForge...")
    except Exception as e:
        # Restore stderr FIRST so the user can see the error message
        try:
            sys.stderr = _original_stderr
            if _stderr_file is not None:
                _stderr_file.close()
                _stderr_file = None
        except Exception:
            pass

        # Log full traceback to file
        try:
            with open(stderr_log, 'a') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{datetime.datetime.now().isoformat()}] FATAL ERROR\n")
                f.write(traceback.format_exc())
                f.write(f"{'='*60}\n")
        except Exception:
            pass

        # Show user-friendly message on terminal
        print(f"\n\nMeshForge encountered a fatal error:\n")
        print(f"  {type(e).__name__}: {e}\n")
        print(f"Full error details saved to:")
        print(f"  {stderr_log}\n")
        print(f"To report this issue:")
        print(f"  https://github.com/Nursedude/meshforge/issues\n")
        exit_code = 1
    finally:
        # Stop background services (prevents hang on exit)
        if launcher is not None:
            _cleanup_items = [
                ('_mqtt_subscriber', 'stop', 'MQTT subscriber'),
                ('_mqtt_ws_bridge', 'stop', 'MQTT WebSocket bridge'),
                ('_telemetry_poller', 'stop', 'Telemetry poller'),
            ]
            for attr, method, name in _cleanup_items:
                try:
                    obj = getattr(launcher, attr, None)
                    if obj:
                        getattr(obj, method)()
                        setattr(launcher, attr, None)
                except Exception as e:
                    logger.warning(f"Cleanup failed for {name}: {e}")
            # Stop map server if running (uses terminate, not stop)
            try:
                if hasattr(launcher, '_map_server_process') and launcher._map_server_process:
                    launcher._map_server_process.terminate()
                    launcher._map_server_process = None
            except Exception as e:
                logger.warning(f"Cleanup failed for map server: {e}")

        # Restore stderr and close the log file handle
        try:
            sys.stderr = _original_stderr
            if _stderr_file is not None:
                _stderr_file.flush()
                _stderr_file.close()
        except Exception:
            pass
        sys.excepthook = _original_excepthook
        sys.exit(exit_code)


if __name__ == '__main__':
    main()
