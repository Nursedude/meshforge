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
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.5.0-beta"

# Import centralized path utility
try:
    from utils.paths import get_real_user_home, ReticulumPaths
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            candidate = Path(f'/home/{sudo_user}')
            return candidate
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            candidate = Path(f'/home/{logname}')
            return candidate
        return Path('/root')

    class ReticulumPaths:
        @classmethod
        def get_config_dir(cls) -> Path:
            if Path('/etc/reticulum/config').is_file():
                return Path('/etc/reticulum')
            home = get_real_user_home()
            xdg = home / '.config' / 'reticulum'
            if (xdg / 'config').is_file():
                return xdg
            return home / '.reticulum'

        @classmethod
        def get_config_file(cls) -> Path:
            return cls.get_config_dir() / 'config'

# Import centralized service checker - SINGLE SOURCE OF TRUTH for service status
# See: utils/service_check.py and .claude/foundations/install_reliability_triage.md
try:
    from utils.service_check import (
        check_service,
        check_port,
        apply_config_and_restart,
        ServiceState
    )
    _HAS_APPLY_RESTART = True
except ImportError:
    # Fallback if running standalone - will use direct systemctl
    check_service = None
    check_port = None
    apply_config_and_restart = None
    ServiceState = None
    _HAS_APPLY_RESTART = False

# Import dialog backend directly (not through package namespace)
from backend import DialogBackend

# Import startup checks and conflict resolution (v0.4.8)
try:
    from startup_checks import StartupChecker, EnvironmentState, ServiceRunState
    from conflict_resolver import check_and_resolve_conflicts
    HAS_STARTUP_CHECKS = True
except ImportError:
    HAS_STARTUP_CHECKS = False
    StartupChecker = None
    EnvironmentState = None
    ServiceRunState = None
    check_and_resolve_conflicts = None

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
from gateway_config_mixin import GatewayConfigMixin
from favorites_mixin import FavoritesMixin
from network_tools_mixin import NetworkToolsMixin
from web_client_mixin import WebClientMixin


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
    GatewayConfigMixin,
    FavoritesMixin,
    NetworkToolsMixin,
    WebClientMixin
):
    """MeshForge launcher with raspi-config style interface."""

    def __init__(self):
        self.dialog = DialogBackend()
        self.src_dir = Path(__file__).parent.parent  # src/ directory
        self.env = self._detect_environment()
        self._setup_status_bar()
        self._meshtastic_path = None  # Cached CLI path
        self._bridge_log_path = None  # Path to active bridge log file
        # Enhanced startup checker (v0.4.8)
        self._startup_checker = StartupChecker() if HAS_STARTUP_CHECKS else None
        self._env_state: Optional[EnvironmentState] = None

    @staticmethod
    def _wait_for_enter(msg: str = "\nPress Enter to continue...") -> None:
        """Wait for user to press Enter, handling Ctrl+C gracefully."""
        try:
            input(msg)
        except (KeyboardInterrupt, EOFError):
            print()  # Clean newline after ^C

    def _get_meshtastic_cli(self) -> str:
        """Find the meshtastic CLI binary path, with caching."""
        if self._meshtastic_path is None:
            try:
                from utils.cli import find_meshtastic_cli
                self._meshtastic_path = find_meshtastic_cli() or 'meshtastic'
            except ImportError:
                self._meshtastic_path = shutil.which('meshtastic') or 'meshtastic'
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

    def run(self):
        """Run the launcher."""
        if not self.env['is_root']:
            print("\nError: MeshForge requires root/sudo privileges")
            print("Please run: sudo python3 src/launcher_tui/main.py")
            sys.exit(1)

        if not self.dialog.available:
            # Fallback to basic launcher
            print("whiptail/dialog not available, using basic launcher...")
            self._run_basic_launcher()
            return

        # Run startup environment checks (v0.4.8)
        if not self._run_startup_checks():
            return  # User aborted due to conflicts

        # Check for first run and offer setup wizard
        if self._check_first_run():
            self._run_first_run_wizard()

        # Check for service misconfiguration (SPI HAT with USB config)
        self._check_service_misconfig()

        # Auto-start map server if configured
        self._maybe_auto_start_map()

        # Auto-start MQTT subscriber and TelemetryPoller if configured
        self._maybe_auto_start_mqtt_and_telemetry()

        self._run_main_menu()

    def _run_startup_checks(self) -> bool:
        """
        Run startup environment checks and conflict resolution.

        Returns:
            True to continue, False if user aborted
        """
        if not HAS_STARTUP_CHECKS or not self._startup_checker:
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
                    if _HAS_APPLY_RESTART:
                        success, msg = apply_config_and_restart('meshtasticd')
                    else:
                        subprocess.run(['systemctl', 'daemon-reload'], timeout=30, check=False)
                        subprocess.run(['systemctl', 'restart', 'meshtasticd'], timeout=30, check=False)
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
                ("4", "Maps & Viz          Coverage maps, topology"),
                ("5", "Configuration       Radio, services, settings"),
                ("6", "System              Hardware, logs, Linux tools"),
                # Quick Access
                ("q", "Quick Actions       Common shortcuts"),
                ("e", "Emergency Mode      Field operations"),
                # Meta
                ("a", "About               Version, help, web client"),
                ("x", "Exit"),
            ]

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
        """Handle main menu selection (v0.4.8 restructured)."""
        if choice == "1":
            self._dashboard_menu()
        elif choice == "2":
            self._mesh_networks_menu()
        elif choice == "3":
            self._rf_sdr_menu()
        elif choice == "4":
            self._maps_viz_menu()
        elif choice == "5":
            self._configuration_menu()
        elif choice == "6":
            self._system_menu()
        elif choice == "q":
            self._quick_actions_menu()
        elif choice == "e":
            self._emergency_mode()
        elif choice == "a":
            self._about_menu()

    # =========================================================================
    # NEW Submenu: Dashboard (1)
    # =========================================================================

    def _dashboard_menu(self):
        """Dashboard - Status, health, alerts."""
        while True:
            choices = [
                ("status", "Service Status      All services with health"),
                ("network", "Network Status      Ports, interfaces, conflicts"),
                ("nodes", "Node Count          Meshtastic + RNS nodes"),
                ("datapath", "Data Path Check     Test all data sources"),
                ("metrics", "Historical Trends   Metrics over time"),
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

            if choice == "status":
                self._service_status_display()
            elif choice == "network":
                self._network_menu()
            elif choice == "nodes":
                self._show_node_counts()
            elif choice == "datapath":
                self._data_path_diagnostic()
            elif choice == "metrics":
                self._metrics_menu()
            elif choice == "alerts":
                self._show_alerts()

    def _service_status_display(self):
        """Show comprehensive service status."""
        # Delegate to existing service menu status
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Service Status ===\n")

        if self._env_state:
            for name, info in self._env_state.services.items():
                if info.state == ServiceRunState.RUNNING:
                    print(f"  \033[0;32m●\033[0m {name:<18} running")
                elif info.state == ServiceRunState.FAILED:
                    print(f"  \033[0;31m●\033[0m {name:<18} FAILED")
                else:
                    print(f"  \033[2m○\033[0m {name:<18} stopped")
        else:
            # Fallback to systemctl
            for svc in ['meshtasticd', 'rnsd', 'mosquitto']:
                try:
                    result = subprocess.run(
                        ['systemctl', 'is-active', svc],
                        capture_output=True, text=True, timeout=5
                    )
                    status = result.stdout.strip()
                    if status == 'active':
                        print(f"  \033[0;32m●\033[0m {svc:<18} running")
                    else:
                        print(f"  \033[2m○\033[0m {svc:<18} {status}")
                except Exception:
                    print(f"  ? {svc:<18} unknown")

        print()
        self._wait_for_enter()

    def _show_node_counts(self):
        """Show node counts from all sources."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Node Counts ===\n")

        # Meshtastic nodes
        try:
            cli = self._get_meshtastic_cli()
            result = subprocess.run(
                [cli, '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=30
            )
            # Count nodes in output
            node_count = result.stdout.count('Node ')
            print(f"  Meshtastic nodes: {node_count}")
        except Exception as e:
            print(f"  Meshtastic: unavailable ({e})")

        # RNS destinations
        try:
            result = subprocess.run(
                ['rnstatus', '-a'],
                capture_output=True, text=True, timeout=10
            )
            # Count lines that look like destinations
            dest_count = len([l for l in result.stdout.splitlines() if l.strip().startswith('<')])
            print(f"  RNS destinations: {dest_count}")
        except Exception:
            print("  RNS: unavailable")

        print()
        self._wait_for_enter()

    def _data_path_diagnostic(self):
        """Test all data collection paths to diagnose zero-data issues."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Data Path Diagnostic ===\n")
        print("Testing all data sources...\n")

        results = []

        # Test 1: meshtasticd TCP connection
        print("[1/6] Testing meshtasticd TCP (port 4403)...")
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()
            if result == 0:
                results.append(("meshtasticd TCP", "OK", "Port 4403 accepting connections"))
                print("      \033[0;32mOK\033[0m - Port 4403 reachable")
            else:
                results.append(("meshtasticd TCP", "FAIL", f"Connection refused (code {result})"))
                print(f"      \033[0;31mFAIL\033[0m - Connection refused")
        except Exception as e:
            results.append(("meshtasticd TCP", "FAIL", str(e)))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 2: meshtastic CLI node count
        print("[2/6] Testing meshtastic CLI...")
        try:
            result = subprocess.run(
                ['meshtastic', '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                # Count nodes from output
                node_lines = [l for l in result.stdout.split('\n') if 'Node' in l or '!' in l]
                results.append(("meshtastic CLI", "OK", f"Responded, ~{len(node_lines)} node refs"))
                print(f"      \033[0;32mOK\033[0m - CLI responded")
            else:
                results.append(("meshtastic CLI", "WARN", result.stderr[:50] if result.stderr else "No output"))
                print(f"      \033[0;33mWARN\033[0m - Non-zero exit")
        except FileNotFoundError:
            results.append(("meshtastic CLI", "SKIP", "CLI not installed"))
            print("      \033[0;33mSKIP\033[0m - CLI not found")
        except subprocess.TimeoutExpired:
            results.append(("meshtastic CLI", "FAIL", "Timeout after 15s"))
            print("      \033[0;31mFAIL\033[0m - Timeout")
        except Exception as e:
            results.append(("meshtastic CLI", "FAIL", str(e)[:50]))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 3: meshtastic Python API
        print("[3/6] Testing meshtastic Python API...")
        try:
            import meshtastic.tcp_interface
            iface = meshtastic.tcp_interface.TCPInterface(hostname='localhost', connectNow=True)
            node_count = len(iface.nodes) if iface.nodes else 0
            iface.close()
            results.append(("meshtastic API", "OK", f"{node_count} nodes in nodeDB"))
            print(f"      \033[0;32mOK\033[0m - {node_count} nodes found")
        except ImportError:
            results.append(("meshtastic API", "SKIP", "meshtastic module not installed"))
            print("      \033[0;33mSKIP\033[0m - Module not installed")
        except Exception as e:
            err_msg = str(e)[:50]
            results.append(("meshtastic API", "FAIL", err_msg))
            print(f"      \033[0;31mFAIL\033[0m - {err_msg}")

        # Test 4: pubsub availability
        print("[4/6] Testing pubsub (for live capture)...")
        try:
            from pubsub import pub
            # Check if any listeners on meshtastic.receive
            listeners = pub.getDefaultTopicMgr().getTopic('meshtastic.receive', okIfNone=True)
            if listeners:
                count = len(list(listeners.getListeners()))
                results.append(("pubsub", "OK", f"{count} listener(s) on meshtastic.receive"))
                print(f"      \033[0;32mOK\033[0m - {count} listener(s) registered")
            else:
                results.append(("pubsub", "WARN", "Topic exists but no listeners"))
                print("      \033[0;33mWARN\033[0m - No listeners registered")
        except ImportError:
            results.append(("pubsub", "SKIP", "pubsub module not installed"))
            print("      \033[0;33mSKIP\033[0m - Module not installed")
        except Exception as e:
            results.append(("pubsub", "WARN", str(e)[:50]))
            print(f"      \033[0;33mWARN\033[0m - {e}")

        # Test 5: MapDataCollector
        print("[5/6] Testing MapDataCollector...")
        try:
            from utils.map_data_collector import MapDataCollector
            collector = MapDataCollector(enable_history=False)
            geojson = collector.collect(max_age_seconds=30)
            props = geojson.get('properties', {})
            total = props.get('total_nodes', 0)
            with_gps = props.get('nodes_with_position', 0)
            sources = props.get('sources', {})
            active_sources = [k for k, v in sources.items() if v > 0]
            if total > 0:
                results.append(("MapDataCollector", "OK", f"{total} nodes ({with_gps} with GPS)"))
                print(f"      \033[0;32mOK\033[0m - {total} nodes, sources: {active_sources}")
            else:
                results.append(("MapDataCollector", "WARN", "0 nodes returned"))
                print("      \033[0;33mWARN\033[0m - 0 nodes (check meshtasticd connection)")
        except ImportError:
            results.append(("MapDataCollector", "SKIP", "Module not available"))
            print("      \033[0;33mSKIP\033[0m - Module not available")
        except Exception as e:
            results.append(("MapDataCollector", "FAIL", str(e)[:50]))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 6: RNS path table
        print("[6/6] Testing RNS path table...")
        try:
            result = subprocess.run(
                ['rnpath', '-t'],
                capture_output=True, text=True, timeout=10
            )
            lines = [l for l in result.stdout.splitlines() if l.strip() and not l.startswith('Path')]
            path_count = len(lines)
            if path_count > 0:
                results.append(("RNS paths", "OK", f"{path_count} known paths"))
                print(f"      \033[0;32mOK\033[0m - {path_count} paths in table")
            else:
                results.append(("RNS paths", "WARN", "Path table empty"))
                print("      \033[0;33mWARN\033[0m - No paths (normal if no RNS traffic yet)")
        except FileNotFoundError:
            results.append(("RNS paths", "SKIP", "rnpath not installed"))
            print("      \033[0;33mSKIP\033[0m - rnpath not found")
        except Exception as e:
            results.append(("RNS paths", "WARN", str(e)[:50]))
            print(f"      \033[0;33mWARN\033[0m - {e}")

        # Summary
        print("\n" + "=" * 50)
        print("SUMMARY")
        print("=" * 50)
        ok_count = len([r for r in results if r[1] == "OK"])
        fail_count = len([r for r in results if r[1] == "FAIL"])
        warn_count = len([r for r in results if r[1] == "WARN"])

        for test, status, detail in results:
            if status == "OK":
                print(f"  \033[0;32m✓\033[0m {test:<20} {detail}")
            elif status == "FAIL":
                print(f"  \033[0;31m✗\033[0m {test:<20} {detail}")
            elif status == "WARN":
                print(f"  \033[0;33m!\033[0m {test:<20} {detail}")
            else:
                print(f"  \033[2m-\033[0m {test:<20} {detail}")

        print()
        if fail_count > 0:
            print(f"Result: {fail_count} FAILED - check service connections")
        elif warn_count > 0 and ok_count == 0:
            print("Result: No data sources working - check meshtasticd")
        elif ok_count > 0:
            print(f"Result: {ok_count} sources OK - data should be flowing")
        print()
        self._wait_for_enter()

    def _show_alerts(self):
        """Show current alerts from environment state."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Current Alerts ===\n")

        if self._env_state:
            alerts = self._env_state.get_alerts()
            if alerts:
                for alert in alerts:
                    print(f"  \033[0;33m!\033[0m {alert}")
            else:
                print("  No alerts - system healthy")
        else:
            print("  Environment state not available")

        print()
        self._wait_for_enter()

    # =========================================================================
    # NEW Submenu: Mesh Networks (2)
    # =========================================================================

    def _mesh_networks_menu(self):
        """Mesh Networks - Meshtastic, RNS, AREDN."""
        while True:
            choices = [
                ("meshtastic", "Meshtastic          Radio, channels, CLI"),
                ("rns", "RNS / Reticulum     Status, gateway, NomadNet"),
                ("gateway", "Gateway Bridge      RNS-Meshtastic config"),
                ("aredn", "AREDN Mesh          AREDN integration"),
                ("mqtt", "MQTT Monitor        Nodeless mesh observation"),
                ("services", "Service Control     Start/stop/restart"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Mesh Networks",
                "Manage mesh network connections:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "meshtastic":
                self._radio_menu()
            elif choice == "rns":
                self._rns_menu()
            elif choice == "gateway":
                self._gateway_config_menu()
            elif choice == "aredn":
                self._aredn_menu()
            elif choice == "mqtt":
                self._mqtt_menu()
            elif choice == "services":
                self._service_menu()

    # =========================================================================
    # NEW Submenu: RF & SDR (3)
    # =========================================================================

    def _rf_sdr_menu(self):
        """RF & SDR - Calculators, SDR monitoring."""
        while True:
            choices = [
                ("link", "Link Budget         FSPL, Fresnel, range"),
                ("site", "Site Planner        Coverage estimation"),
                ("freq", "Frequency Slots     Channel calculator"),
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

            if choice == "link":
                self._rf_tools_menu()
            elif choice == "site":
                self._site_planner_menu()
            elif choice == "freq":
                self._frequency_calculator()
            elif choice == "sdr":
                self._rf_awareness_menu()

    # =========================================================================
    # NEW Submenu: Maps & Viz (4)
    # =========================================================================

    def _maps_viz_menu(self):
        """Maps & Visualization - Coverage maps, topology."""
        while True:
            choices = [
                ("coverage", "Coverage Map        Generate coverage map"),
                ("topology", "Network Topology    D3.js graph view"),
                ("traffic", "Traffic Inspector   Wireshark-grade visibility"),
                ("nodes", "Node Map            All nodes on map"),
                ("quality", "Link Quality        Quality analysis"),
                ("export", "Export Data         GeoJSON, CSV, GraphML"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Maps & Visualization",
                "Network visualization tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "coverage":
                self._ai_tools_menu()
            elif choice == "topology":
                self._topology_menu()
            elif choice == "traffic":
                self.menu_traffic_inspector()
            elif choice == "nodes":
                self._open_node_map()
            elif choice == "quality":
                self._link_quality_menu()
            elif choice == "export":
                self._export_data_menu()

    def _open_node_map(self):
        """Open the node map in browser."""
        # Trigger map generation and open
        self._ai_tools_menu()

    def _export_data_menu(self):
        """Export data in various formats."""
        while True:
            choices = [
                ("geojson", "GeoJSON             For mapping tools"),
                ("csv", "CSV                 Spreadsheet format"),
                ("graphml", "GraphML             For graph analysis"),
                ("d3", "D3.js JSON          For web visualization"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Export Data",
                "Export network data:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Delegate to topology export functions
            if choice in ["geojson", "csv", "graphml", "d3"]:
                self._export_topology_data(choice)

    def _export_topology_data(self, format_type: str):
        """Export topology data in specified format."""
        try:
            from utils.topology_visualizer import TopologyVisualizer

            # Get topology from TopologyMixin (properly populated)
            topology = self._get_topology()
            if topology is None:
                self.dialog.msgbox(
                    "Export Unavailable",
                    "Network topology not loaded.\n\n"
                    "The gateway service may need to be running."
                )
                return

            # Create visualizer from actual topology data
            viz = TopologyVisualizer.from_topology(topology)

            if format_type == "geojson":
                path, count = viz.export_geojson()
                self.dialog.msgbox(
                    "GeoJSON Export",
                    f"Exported {count} features.\n\nFile: {path}"
                )
            elif format_type == "csv":
                nodes_path, edges_path = viz.export_csv()
                self.dialog.msgbox(
                    "CSV Export",
                    f"Exported CSV files:\n\nNodes: {nodes_path}\nEdges: {edges_path}"
                )
            elif format_type == "graphml":
                path, count = viz.export_graphml()
                self.dialog.msgbox(
                    "GraphML Export",
                    f"Exported {count} edges.\n\nFile: {path}"
                )
            elif format_type == "d3":
                path, count = viz.export_d3_json()
                self.dialog.msgbox(
                    "D3.js Export",
                    f"Exported {count} nodes + links.\n\nFile: {path}"
                )

        except ImportError:
            self.dialog.msgbox("Export Failed", "Topology visualizer module not available.")
        except Exception as e:
            self.dialog.msgbox("Export Failed", f"Error: {e}")

    # =========================================================================
    # NEW Submenu: Configuration (5)
    # =========================================================================

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
                ("meshforge", "MeshForge Settings  App preferences"),
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

            if choice == "radio":
                self._config_menu()
            elif choice == "channels":
                self._channel_config_menu()
            elif choice == "rns-config":
                self._edit_rns_config()
            elif choice == "services":
                self._service_menu()
            elif choice == "backup":
                self._device_backup_menu()
            elif choice == "updates":
                self._updates_menu()
            elif choice == "meshforge":
                self._settings_menu()
            elif choice == "wizard":
                self._run_first_run_wizard()

    # =========================================================================
    # NEW Submenu: System (6)
    # =========================================================================

    def _system_menu(self):
        """System - Hardware, logs, Linux tools."""
        while True:
            choices = [
                ("hardware", "Hardware            Detect SPI/I2C/USB"),
                ("logs", "Logs                View/follow logs"),
                ("network", "Network Tools       Ping, ports, interfaces"),
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

            if choice == "hardware":
                self._hardware_menu()
            elif choice == "logs":
                self._logs_menu()
            elif choice == "network":
                self._network_tools_submenu()
            elif choice == "shell":
                self._drop_to_shell()
            elif choice == "reboot":
                self._reboot_menu()

    def _network_tools_submenu(self):
        """Network diagnostic tools."""
        # Delegate to existing network menu
        self._network_menu()

    def _drop_to_shell(self):
        """Drop to a bash shell."""
        subprocess.run(['clear'], check=False, timeout=5)
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
                    subprocess.run(['systemctl', 'reboot'], timeout=30)
            elif choice == "shutdown":
                if self.dialog.yesno("Confirm Shutdown", "Shutdown the system now?"):
                    subprocess.run(['systemctl', 'poweroff'], timeout=30)

    # =========================================================================
    # NEW Submenu: About (a)
    # =========================================================================

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

            if choice == "version":
                self._show_about()
            elif choice == "web":
                self._open_web_client()
            elif choice == "help":
                self._show_help()

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
        subprocess.run(['clear'], check=False, timeout=5)
        print(help_text)
        self._wait_for_enter()

    # =========================================================================
    # Legacy menu handler (for backward compatibility)
    # =========================================================================

    def _handle_choice(self, choice: str):
        """Handle menu selection (legacy - kept for compatibility)."""
        if choice == "status":
            self._run_terminal_status()
        elif choice == "quick":
            self._quick_actions_menu()
        elif choice == "radio":
            self._radio_menu()
        elif choice == "services":
            self._service_menu()
        elif choice == "emcomm":
            self._emergency_mode()
        elif choice == "logs":
            self._logs_menu()
        elif choice == "network":
            self._network_menu()
        elif choice == "rns":
            self._rns_menu()
        elif choice == "aredn":
            self._aredn_menu()
        elif choice == "metrics":
            self._metrics_menu()
        elif choice == "rf":
            self._rf_tools_menu()
        elif choice == "sdr":
            self._rf_awareness_menu()
        elif choice == "maps":
            self._ai_tools_menu()
        elif choice == "config":
            self._config_menu()
        elif choice == "hardware":
            self._hardware_menu()
        elif choice == "system":
            self._system_tools_menu()
        elif choice == "web":
            self._open_web_client()
        elif choice == "about":
            self._show_about()

    # Radio Menu methods moved to radio_menu_mixin.py (v0.4.8)

    # Logs Menu methods moved to logs_menu_mixin.py (v0.4.8)
    # Network Menu methods moved to network_tools_mixin.py (v0.5.0)
    # RNS Menu methods moved to rns_menu_mixin.py (v0.4.8)
    # AREDN Menu methods moved to aredn_mixin.py (v0.4.8)

    # =========================================================================
    # Config Menu - meshtasticd config.d/ management
    # =========================================================================

    def _config_menu(self):
        """Configuration management for meshtasticd."""
        while True:
            choices = [
                ("view", "View Active Config"),
                ("overlays", "View config.d/ Overlays"),
                ("available", "Available HAT Configs"),
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

            if choice == "view":
                self._view_active_config()
            elif choice == "overlays":
                self._view_config_overlays()
            elif choice == "available":
                self._view_available_hats()
            elif choice == "presets":
                self._radio_presets_menu()
            elif choice == "channels":
                self._channel_config_menu()
            elif choice == "meshtasticd":
                self._meshtasticd_menu()
            elif choice == "settings":
                self._settings_menu()
            elif choice == "wizard":
                self._run_first_run_wizard()

    def _view_active_config(self):
        """Show the active meshtasticd config.yaml."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== meshtasticd config.yaml ===\n")

        config_path = Path('/etc/meshtasticd/config.yaml')
        if config_path.exists():
            print(f"File: {config_path}\n")
            try:
                print(config_path.read_text())
            except PermissionError:
                print("Permission denied. Try: sudo cat /etc/meshtasticd/config.yaml")
        else:
            print("config.yaml not found!")
            print("\nInstall meshtasticd:")
            print("  sudo apt install meshtasticd")
            print("  # or run the MeshForge installer")

        self._wait_for_enter()

    def _view_config_overlays(self):
        """Show config.d/ overlay files."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== config.d/ Overlays ===\n")

        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            print("config.d/ directory not found.")
            print("Create it: sudo mkdir -p /etc/meshtasticd/config.d")
            self._wait_for_enter()
            return

        overlays = sorted(config_d.glob('*.yaml'))
        if not overlays:
            print("No overlay files in config.d/")
            print("\nOverlays override sections from config.yaml")
            print("MeshForge writes here instead of touching config.yaml")
        else:
            print(f"Found {len(overlays)} overlay(s):\n")
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

    def _view_available_hats(self):
        """Show available HAT configurations from meshtasticd package."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Available HAT Configs ===\n")

        available_d = Path('/etc/meshtasticd/available.d')
        if not available_d.exists():
            print("available.d/ not found.")
            print("meshtasticd package should provide this.")
            print("\nInstall: sudo apt install meshtasticd")
            self._wait_for_enter()
            return

        configs = sorted(available_d.glob('*.yaml'))
        if not configs:
            print("No HAT configs available.")
        else:
            print(f"Found {len(configs)} HAT config(s):\n")
            for i, f in enumerate(configs, 1):
                print(f"  {i:2d}. {f.name}")

            print("\nTo activate a HAT config:")
            print("  sudo cp /etc/meshtasticd/available.d/<file>.yaml \\")
            print("         /etc/meshtasticd/config.d/")
            print("  sudo systemctl restart meshtasticd")
            print("\nWARNING: Only ONE Lora config should be in config.d/")

        self._wait_for_enter()

    # Web client methods are in WebClientMixin

    # =========================================================================
    # Terminal-native utilities (used by menus above)
    # =========================================================================

    def _run_terminal_status(self):
        """Run meshforge-status (terminal-native one-shot status)."""
        subprocess.run(['clear'], check=False, timeout=5)
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

    # _run_terminal_network moved to network_tools_mixin.py (v0.5.0)

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
License: MIT

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

    # Set all loggers to CRITICAL to prevent output during TUI
    logging.getLogger().setLevel(logging.CRITICAL)
    for name in logging.root.manager.loggerDict:
        logging.getLogger(name).setLevel(logging.CRITICAL)

    # Redirect stderr to log file to prevent TUI corruption
    log_dir = Path("/tmp")
    try:
        from utils.paths import get_real_user_home
        log_dir = get_real_user_home() / ".cache" / "meshforge" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    stderr_log = log_dir / "tui_errors.log"
    _original_stderr = sys.stderr
    try:
        sys.stderr = open(stderr_log, 'a')
    except Exception:
        pass  # Keep original stderr if can't redirect

    try:
        launcher = MeshForgeLauncher()
        launcher.run()
    except KeyboardInterrupt:
        print("\n\nExiting MeshForge...")
        sys.exit(0)
    finally:
        # Restore stderr
        try:
            if sys.stderr != _original_stderr:
                sys.stderr.close()
                sys.stderr = _original_stderr
        except Exception:
            pass
        sys.exit(0)


if __name__ == '__main__':
    main()
