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
from utils.service_check import lock_port_external
# TopologyVisualizer is in handlers/topology.py

# Import centralized path utility - SINGLE SOURCE OF TRUTH for all paths
# See: utils/paths.py (ReticulumPaths, get_real_user_home)
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)
from utils.paths import get_real_user_home, ReticulumPaths

# Import centralized service checker - SINGLE SOURCE OF TRUTH for service status
# See: utils/service_check.py and .claude/foundations/install_reliability_triage.md
from utils.service_check import check_service, check_port, apply_config_and_restart, ServiceState

# Import dialog backend directly (not through package namespace)
from backend import DialogBackend, clear_screen

# Import startup checks and conflict resolution (v0.4.8)
from startup_checks import StartupChecker, EnvironmentState, ServiceRunState
from conflict_resolver import check_and_resolve_conflicts

# Mixins removed — all functionality now in handler registry (Batch 9)

# Handler registry infrastructure (Phase 1 of mixin-to-registry migration)
from handler_protocol import TUIContext
from handler_registry import HandlerRegistry
from handlers import get_all_handlers


class MeshForgeLauncher:
    """MeshForge launcher with raspi-config style interface."""

    def __init__(self, profile=None):
        self.dialog = DialogBackend()
        self.src_dir = Path(__file__).parent.parent  # src/ directory
        self.env = self._detect_environment()
        self._setup_status_bar()
        self._meshtastic_path = None  # Cached CLI path
        self._bridge_log_path = None  # Path to active bridge log file
        # Enhanced startup checker (v0.4.8)
        self._startup_checker = StartupChecker()
        self._env_state: Optional[EnvironmentState] = None
        # Deployment profile for menu filtering
        self._profile = profile
        self._feature_flags = getattr(profile, 'feature_flags', {}) if profile else {}

        # Handler registry (Phase 1 of mixin-to-registry migration).
        # Handlers are registered here and dispatched via _registry.dispatch()
        # in submenu methods. Legacy mixin dispatch is the fallback.
        self._tui_context = TUIContext(
            dialog=self.dialog,
            env_state=self._env_state,
            startup_checker=self._startup_checker,
            status_bar=getattr(self, '_status_bar', None),
            feature_flags=self._feature_flags,
            profile=self._profile,
            src_dir=self.src_dir,
            env=self.env,
        )
        self._registry = HandlerRegistry(self._tui_context)
        self._tui_context.registry = self._registry
        for handler_cls in get_all_handlers():
            self._registry.register(handler_cls())

    def _feature_enabled(self, feature: str) -> bool:
        """Check if a feature is enabled in the current deployment profile.

        When no profile is set, all features are enabled (backward compatible).
        """
        if not self._feature_flags:
            return True
        return self._feature_flags.get(feature, True)

    def _build_section_menu(self, section, legacy_items, ordering=None):
        """Build menu choices by merging registry + legacy items.

        Registry items auto-replace legacy items with the same tag.
        Ordering list controls display order when provided.

        Args:
            section: Menu section key (e.g., "dashboard", "rf_sdr").
            legacy_items: List of (tag, description) for unconverted items.
            ordering: Optional list of tags defining display order.

        Returns:
            List of (tag, description) tuples with "Back" appended.
        """
        registry_items = self._registry.get_menu_items(section)
        registry_tags = {tag for tag, _ in registry_items}

        # Filter legacy items already handled by registry
        filtered_legacy = [(t, d) for t, d in legacy_items if t not in registry_tags]

        all_map = {tag: desc for tag, desc in registry_items}
        all_map.update({tag: desc for tag, desc in filtered_legacy})

        if ordering:
            result = [(t, all_map[t]) for t in ordering if t in all_map]
            # Append items not in ordering
            ordered_set = set(ordering)
            for tag, desc in list(registry_items) + filtered_legacy:
                if tag not in ordered_set and (tag, desc) not in result:
                    result.append((tag, desc))
        else:
            result = list(registry_items) + filtered_legacy

        result.append(("back", "Back"))
        return result

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
        except Exception as e:
            logger.debug(f"Status bar initialization skipped: {e}")
            self._status_bar = None

    def _get_error_log_path(self) -> Path:
        """Get the path to the TUI error log file."""
        try:
            log_dir = get_real_user_home() / ".cache" / "meshforge" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir / "tui_errors.log"
        except Exception as e:
            logger.debug(f"Cannot create log directory, using /tmp fallback: {e}")
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

        # Check for first run and offer setup wizard (Batch 8: via handler)
        first_run_handler = self._registry.get_handler("first_run")
        if first_run_handler:
            first_run_handler.on_startup()

        # Check for service misconfiguration (SPI HAT with USB config)
        self._check_service_misconfig()

        # Detect if daemon is managing core services
        self._daemon_active = self._is_daemon_running()
        self._tui_context.daemon_active = self._daemon_active
        if self._daemon_active:
            logger.info("Daemon detected — TUI running in tool-only mode")
        else:
            # Only auto-start services when daemon ISN'T running.
            # If daemon owns these, starting them here would cause
            # port conflicts (Config API :8081) or singleton clashes.
            self._registry.startup_all()  # AITools, MQTT, ConfigAPI, etc.
            self._maybe_auto_lock_port()
            self._start_health_monitor()

        # Non-blocking update check — sets _updates_available for status hint
        self._check_startup_updates()

        try:
            self._run_main_menu()
        finally:
            self._registry.shutdown_all()  # MQTT, ConfigAPI, etc.
            if not self._daemon_active:
                self._stop_health_monitor()

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

    def _check_startup_updates(self) -> None:
        """Non-blocking startup update check.

        Queries the version checker for available updates and stores
        the count in self._updates_available. This is displayed in the
        main menu status hint. Completely best-effort — failures are
        silently ignored so the TUI always starts.
        """
        self._updates_available = 0
        try:
            from utils.safe_import import safe_import
            check_fn, _, has_checker = safe_import(
                'updates.version_checker', 'check_all_versions', 'VersionInfo'
            )
            if not has_checker:
                return
            versions = check_fn()
            count = sum(1 for v in versions.values() if v.update_available)
            if count > 0:
                self._updates_available = count
                logger.info("Startup update check: %d update(s) available", count)
        except Exception as e:
            logger.debug("Startup update check failed (non-blocking): %s", e)

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

        # Sync env_state to handler registry context
        if hasattr(self, '_tui_context'):
            self._tui_context.env_state = self._env_state

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

    _MAX_DIALOG_RETRIES = 3

    def _run_main_menu(self):
        """Display the main NOC menu.

        Redesigned in v0.4.8 to follow UI/UX best practices:
        - Max 10 items per menu (cognitive load)
        - Grouped by user task, not technical domain
        - 2-tap max for common operations

        Includes retry logic: consecutive dialog failures (None returns)
        are retried up to _MAX_DIALOG_RETRIES times before exiting.
        This prevents transient dialog subprocess failures from killing
        the TUI.
        """
        consecutive_failures = 0

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

            if choice == "x":
                break

            if choice is None:
                # Dialog returned None — could be user pressing Escape
                # or the dialog subprocess dying unexpectedly.
                consecutive_failures += 1
                if consecutive_failures >= self._MAX_DIALOG_RETRIES:
                    logger.error(
                        "Main menu dialog failed %d consecutive times, exiting",
                        consecutive_failures,
                    )
                    break
                logger.warning(
                    "Main menu returned None (attempt %d/%d), retrying",
                    consecutive_failures, self._MAX_DIALOG_RETRIES,
                )
                continue

            # Successful interaction resets the failure counter
            consecutive_failures = 0
            self._handle_main_choice(choice)

    def _get_menu_status_hint(self) -> str:
        """Generate status hint for main menu subtitle.

        Uses plain text indicators (UP/FAIL/--) since whiptail/dialog
        don't render ANSI color escape codes.
        Appends update count if updates were detected at startup.
        """
        hint = ""
        if self._env_state:
            hint = self._env_state.get_status_line(plain=True)
        else:
            hint = "Network Operations Center"

        # Append update notification if available
        update_count = getattr(self, '_updates_available', 0)
        if update_count > 0:
            hint += f"  |  {update_count} update(s) available"

        return hint

    def _handle_main_choice(self, choice: str):
        """Handle main menu selection (v0.4.8 restructured).

        All dispatches go through _safe_call to ensure unhandled
        exceptions in any mixin show a user-friendly error dialog
        instead of crashing the TUI.
        """
        # Try registry-based dispatch for main-menu handlers (Batch 4+)
        if self._registry.dispatch("main", choice):
            return

        dispatch = {
            "1": ("Dashboard", self._dashboard_menu),
            "2": ("Mesh Networks", self._mesh_networks_menu),
            "3": ("RF & SDR Tools", self._rf_sdr_menu),
            "4": ("Maps & Visualization", self._maps_viz_menu),
            "5": ("Configuration", self._configuration_menu),
            "6": ("System Tools", self._system_menu),
            "a": ("About", self._about_menu),
        }
        entry = dispatch.get(choice)
        if entry:
            name, method = entry
            self._safe_call(name, method)

    # --- Submenu: Dashboard (1) ---

    def _dashboard_menu(self):
        """Dashboard - Status, health, alerts, propagation."""
        _ORDERING = ["status", "weather", "network", "nodes", "health", "score",
                      "datapath", "metrics", "analytics", "latency", "reports", "alerts"]
        while True:
            # Legacy items — most now handled by DashboardHandler (Batch 4)
            legacy = [
                ("network", "Network Status      Ports, interfaces, conflicts"),
                ("health", "Node Health         Battery, signal, latency"),
                ("metrics", "Historical Trends   Metrics over time"),
            ]
            choices = self._build_section_menu("dashboard", legacy, _ORDERING)

            choice = self.dialog.menu(
                "Dashboard",
                "System status and monitoring:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Try registry-based dispatch first (converted handlers)
            if self._registry.dispatch("dashboard", choice):
                continue

            # Cross-section dispatch (network handler is in "system" section)
            if choice == "network":
                self._registry.dispatch("system", "network")

    # --- Submenu: Mesh Networks (2) ---

    def _mesh_networks_menu(self):
        """Mesh Networks - Meshtastic, RNS, AREDN."""
        _ORDERING = ["meshtastic", "meshcore", "rns", "gateway", "aredn",
                      "messaging", "traffic", "mqtt", "favorites", "ham", "services",
                      "nomadnet", "meshchat"]
        while True:
            # Legacy items — feature-gated items built conditionally
            legacy = []
            if self._feature_enabled("meshtastic"):
                legacy.append(("meshtastic", "Meshtastic          Radio, channels, CLI"))
            if self._feature_enabled("meshcore"):
                legacy.append(("meshcore", "MeshCore            Companion radio, config"))
            if self._feature_enabled("rns"):
                legacy.append(("rns", "RNS / Reticulum     Status, gateway, messaging"))
            if self._feature_enabled("gateway"):
                legacy.append(("gateway", "Gateway Bridge      RNS-Meshtastic-MeshCore"))
            legacy.append(("aredn", "AREDN Mesh          AREDN integration"))
            legacy.append(("messaging", "Messaging           Send/receive messages"))
            legacy.append(("favorites", "Favorites           Manage favorite nodes"))
            choices = self._build_section_menu("mesh_networks", legacy, _ORDERING)

            choice = self.dialog.menu(
                "Mesh Networks",
                "Manage mesh network connections:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Try registry-based dispatch first (converted handlers)
            if self._registry.dispatch("mesh_networks", choice):
                continue

            # All mesh_networks items handled by registry (Batch 3-9)

    # --- NEW Submenu: RF & SDR (3) ---

    def _rf_sdr_menu(self):
        """RF & SDR - Calculators, SDR monitoring."""
        _ORDERING = ["link", "site", "freq", "antenna", "weather", "sdr"]
        while True:
            # All RF & SDR tags handled by registry — empty legacy list
            legacy = []
            choices = self._build_section_menu("rf_sdr", legacy, _ORDERING)

            choice = self.dialog.menu(
                "RF & SDR Tools",
                "Radio frequency tools and monitoring:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Try registry-based dispatch first (converted handlers)
            if self._registry.dispatch("rf_sdr", choice):
                continue

            # RF & SDR section fully converted — no legacy dispatch remaining

    # --- NEW Submenu: Maps & Viz (4) ---

    def _maps_viz_menu(self):
        """Maps & Visualization - Coverage maps, topology."""
        _ORDERING = ["livemap", "coverage", "heatmap", "tiles", "topology",
                      "traffic", "quality", "export", "ai"]
        while True:
            # Legacy items — removed automatically as handlers take over their tags
            legacy = [
                ("quality", "Link Quality        Quality analysis"),
            ]
            choices = self._build_section_menu("maps_viz", legacy, _ORDERING)

            choice = self.dialog.menu(
                "Maps & Visualization",
                "Network visualization tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Try registry-based dispatch first (converted handlers)
            if self._registry.dispatch("maps_viz", choice):
                continue

            # All maps_viz items handled by registry

    # --- NEW Submenu: Configuration (5) ---

    def _configuration_menu(self):
        """Configuration - Radio, services, settings."""
        _ORDERING = ["radio", "channels", "rns-config", "rnode", "backup",
                      "updates", "webhooks", "meshforge", "config-api", "wizard"]
        while True:
            # Legacy items — removed automatically as handlers take over their tags
            legacy = [
                ("radio", "Radio Config        meshtasticd settings"),
                ("channels", "Channel Config      Meshtastic channels"),
                ("rns-config", "RNS Config          Reticulum settings"),
                ("backup", "Device Backup       Backup/restore configs"),
                ("updates", "Software Updates    One-click updates"),
                ("webhooks", "Webhooks            External notifications"),
                ("meshforge", "MeshForge Settings  App preferences"),
                ("config-api", "Config API Server   REST config endpoint"),
            ]
            choices = self._build_section_menu("configuration", legacy, _ORDERING)

            choice = self.dialog.menu(
                "Configuration",
                "System and service configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Registry-based dispatch (all configuration items converted)
            if self._registry.dispatch("configuration", choice):
                continue

            # Cross-section dispatch: RNS config is in the "rns" section
            if choice == "rns-config":
                self._registry.dispatch("rns", "edit")

    # --- NEW Submenu: System (6) ---

    def _system_menu(self):
        """System - Hardware, logs, Linux tools."""
        _ORDERING = ["hardware", "logs", "network", "discover", "diagnose", "daemon",
                      "review", "status", "shell", "reboot"]
        while True:
            # Legacy items — removed automatically as handlers take over their tags
            legacy = [
                ("hardware", "Hardware            Detect SPI/I2C/USB"),
                ("logs", "Logs                View/follow logs"),
                ("network", "Network Tools       Ping, ports, interfaces"),
                ("diagnose", "Diagnostics         System health check"),
                ("daemon", "Daemon Mode         Start/stop headless NOC"),
                ("status", "Quick Status        One-shot status display"),
                ("reboot", "Reboot/Shutdown     Safe system control"),
            ]
            choices = self._build_section_menu("system", legacy, _ORDERING)

            choice = self.dialog.menu(
                "System Tools",
                "System administration:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Registry-based dispatch (all system items converted)
            self._registry.dispatch("system", choice)

    # --- Submenu: About (a) ---

    def _about_menu(self):
        """About - Version, help, web client, system info, changelog."""
        _ORDERING = ["version", "changelog", "sysinfo", "deps", "web", "help"]
        while True:
            legacy = [
                ("version", "Version Info        MeshForge version"),
                ("changelog", "Changelog           Release history"),
                ("sysinfo", "System Info         OS, Python, disk, uptime"),
                ("deps", "Dependencies        Package status"),
                ("help", "Help                Documentation"),
            ]
            choices = self._build_section_menu("about", legacy, _ORDERING)

            choice = self.dialog.menu(
                "About MeshForge",
                "Information, help, and diagnostics:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Registry-based dispatch (all about items converted)
            self._registry.dispatch("about", choice)

def main():
    """Main entry point."""
    import argparse
    import logging
    import os
    import datetime

    # Parse command-line arguments (--help, --version, etc.)
    parser = argparse.ArgumentParser(
        prog='meshforge-tui',
        description='MeshForge TUI — Terminal interface for mesh network operations',
        epilog='Config: ~/.config/meshforge/ | Docs: https://github.com/Nursedude/meshforge',
    )
    try:
        from __version__ import __version__
        parser.add_argument('--version', action='version',
                            version=f'MeshForge TUI {__version__}')
    except ImportError:
        pass
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging to console')
    parser.add_argument('--no-startup-checks', action='store_true',
                        dest='no_startup_checks',
                        help='Skip startup service health checks')
    args, _ = parser.parse_known_args()

    # Initialize the MeshForge logging framework FIRST.
    # This creates the RotatingFileHandler that writes to
    # ~/.config/meshforge/logs/meshforge_YYYYMMDD.log
    # Console output is disabled (log_to_console=False) to prevent
    # whiptail/dialog TUI corruption.
    try:
        from utils.logging_config import setup_logging
        setup_logging(log_level=logging.DEBUG, log_to_file=True, log_to_console=False)
    except Exception:
        pass  # Logging is best-effort; don't block TUI startup

    # Belt-and-suspenders: suppress any stray console handlers that
    # third-party libraries may have registered before setup_logging().
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(logging.CRITICAL)

    # Redirect stderr to a crash-only log file to prevent TUI corruption
    log_dir = Path("/tmp")
    try:
        from utils.paths import get_real_user_home as _get_home
        log_dir = _get_home() / ".cache" / "meshforge" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # Fall back to /tmp

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

    # Show log paths before stderr redirect so user knows where to look
    try:
        from utils.logging_config import LOG_DIR as _app_log_dir
        print(f"  App log: {_app_log_dir}", file=_original_stderr)
    except Exception:
        pass
    print(f"  Crash log: {stderr_log}", file=_original_stderr)

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
            # Unsubscribe status bar before shutting down EventBus
            try:
                if hasattr(launcher, '_status_bar') and launcher._status_bar:
                    launcher._status_bar.cleanup()
            except Exception as e:
                logger.warning(f"Cleanup failed for status bar: {e}")

        # Shut down EventBus thread pool (prevents dangling worker threads)
        try:
            from utils.event_bus import event_bus
            event_bus.shutdown()
        except Exception as e:
            logger.warning(f"Cleanup failed for event bus: {e}")

        # Restore stderr and close the log file handle
        try:
            sys.stderr = _original_stderr
            if _stderr_file is not None:
                _stderr_file.flush()
                _stderr_file.close()
        except Exception:
            pass
        sys.excepthook = _original_excepthook

        # Restore terminal to clean state — prevents "prompt in middle of TUI"
        # when whiptail/dialog dies mid-render (alternate screen buffer left
        # active, cursor hidden, raw mode, etc.)
        try:
            # Exit alternate screen buffer + show cursor + reset attributes
            sys.stdout.write('\033[?1049l\033[?25h\033[0m')
            sys.stdout.flush()
        except Exception:
            pass
        try:
            subprocess.run(
                ['tput', 'reset'],
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

        sys.exit(exit_code)


if __name__ == '__main__':
    main()
