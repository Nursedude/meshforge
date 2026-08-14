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
import shutil
import sys
import subprocess
import logging
import threading
import traceback
from pathlib import Path
from typing import Optional

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
from utils.active_health_probe import get_health_probe
# TopologyVisualizer is in handlers/topology.py

# Import centralized path utility - SINGLE SOURCE OF TRUTH for all paths
# See: utils/paths.py (ReticulumPaths, get_real_user_home)
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)
from utils.paths import get_real_user_home

# Service check utilities moved to handlers/startup_health.py

# Import dialog backend directly (not through package namespace)
from backend import DialogBackend, DialogError, clear_screen

# Import startup checks and conflict resolution (v0.4.8)
from startup_checks import StartupChecker, EnvironmentState, ServiceRunState
from conflict_resolver import check_and_resolve_conflicts

# Mixins removed — all functionality now in handler registry (Batch 9)

# Handler registry infrastructure (Phase 1 of mixin-to-registry migration)
from handler_protocol import TUIContext
from handler_registry import HandlerRegistry
from handlers import get_all_handlers


# Menu display order per section (Q5, audit W8). One place, drift-tested:
# tests/test_menu_orderings.py fails when a registry tag is missing here or
# an entry goes stale — before this dict, 23 tags across 5 sections had
# drifted out of the inline lists and rendered as an unordered tail.
# Cross-section legacy survivors (dashboard/network, configuration/
# rns-config, extensions/mfmaps) are ordered here too.
SECTION_ORDERINGS = {
    "dashboard": [
        "status", "weather", "network", "nodes", "health", "score",
        "datapath", "stack_health", "fleet_posture", "traffic_pulse",
        "metrics", "analytics", "latency", "reports", "alerts",
        "mini_dudeai", "mini_dudeai_chat", "mini_dudeai_rules",
        "offline_oracle", "moc_analysis", "demo",
    ],
    "mesh_networks": [
        "meshtastic", "meshcore", "rns", "gateway", "wizard", "check",
        "export", "test_gateway_rx", "aredn", "messaging", "meshchatx",
        "nomadnet", "traffic", "mqtt", "broker-menu", "mesh_alerts",
        "automation", "dual_failover", "load_balancer", "favorites",
        "ham", "services",
    ],
    "rf_sdr": ["link", "site", "freq", "antenna", "weather", "sdr"],
    "maps_viz": [
        "livemap", "mfmaps", "coverage", "heatmap", "tiles", "topology",
        "traffic", "quality", "export", "ai",
    ],
    "configuration": [
        "meshtasticd", "channels", "rns-config", "rnode", "backup",
        "fleet_backup", "updates", "webhooks", "meshforge", "config-api",
        "wizard",
    ],
    "system": [
        "hardware", "logs", "network", "discover", "diagnose", "db_health",
        "run", "details", "daemon", "fleet_membership", "fleet_provision",
        "review", "status", "shell", "reboot",
    ],
    "extensions": ["mfmaps", "meshing"],
    "about": ["version", "changelog", "sysinfo", "deps", "web", "help"],
}


class MeshForgeLauncher:
    """MeshForge launcher with raspi-config style interface."""

    def __init__(self):
        self.dialog = DialogBackend()
        self.src_dir = Path(__file__).parent.parent  # src/ directory
        self.env = self._detect_environment()
        self._setup_status_bar()
        self._bridge_log_path = None  # Path to active bridge log file
        # --no-startup-checks: set by main() before run(); skips the
        # startup environment sweep AND the background update check.
        self.skip_startup_checks = False
        # Enhanced startup checker (v0.4.8)
        self._startup_checker = StartupChecker()
        self._env_state: Optional[EnvironmentState] = None

        # Handler registry (Phase 1 of mixin-to-registry migration).
        # Handlers are registered here and dispatched via _registry.dispatch()
        # in submenu methods. Legacy mixin dispatch is the fallback.
        self._tui_context = TUIContext(
            dialog=self.dialog,
            env_state=self._env_state,
            startup_checker=self._startup_checker,
            status_bar=getattr(self, '_status_bar', None),
            src_dir=self.src_dir,
            env=self.env,
        )
        self._registry = HandlerRegistry(self._tui_context)
        self._tui_context.registry = self._registry
        for handler_cls in get_all_handlers():
            self._registry.register(handler_cls())

    # Q1 purge 2026-08-14 (audit W4): the launcher-side profile plumbing
    # (_profile/_feature_flags/_feature_enabled + the maps/tactical menu
    # gates) is gone — no construction site ever passed a profile, so the
    # flags were provably always {} and every gate always True. Deployment
    # profiles remain a launcher.py/setup_wizard/daemon feature; if TUI
    # menu filtering is ever wanted, wire it through TUIContext.feature_flags
    # (the handler-level flag field + registry filter still exist and are
    # tested — that is the seam to feed).

    def _notify_unwired(self, choice) -> None:
        """Honest feedback for a menu tag no handler owns (Q5, audit W17).

        Should never fire — menu entries come from the registry — so this
        is a tripwire: a silent re-render used to hide exactly the class
        of wiring bug where a menu names an action nothing implements.
        """
        logger.error("Menu tag %r reached dispatch with no owner", choice)
        self.dialog.msgbox(
            "Not wired",
            f"No handler owns the action '{choice}'.\n\n"
            "This is a MeshForge wiring bug — please report it\n"
            "(About > Version has the issue link).",
        )

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
        from utils.tui_logging import get_error_log_path
        return get_error_log_path()

    def _log_error(self, context: str, exc: Exception) -> None:
        """Write error details to the TUI error log file."""
        from utils.tui_logging import log_error
        log_error(context, exc)

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
                f"In-app: Configuration > Software Updates repairs\n"
                f"MeshForge dependencies.\n"
                f"Manual fallback: pip3 install {module}\n\n"
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

    def _run_basic_launcher(self) -> bool:
        """Degraded path when neither whiptail nor dialog is installed.

        Returns True only if the dialog backend was recovered (whiptail
        installed in-app) — the caller then continues normal startup.
        Returns False after printing an honest explanation; it never
        pretends a TUI ran (the old code called a method that did not
        exist and crashed — 2026-08-14 audit W1).

        The raw input() here is a documented exception to the no-raw-input
        rule: the dialog layer is precisely what is missing.
        """
        print("MeshForge TUI needs 'whiptail' (or 'dialog') to draw its "
              "menus, and neither is installed.")
        if sys.stdin.isatty() and shutil.which('apt-get'):
            if os.geteuid() != 0:
                # Dialog layer absent AND not root — the in-app installer
                # below cannot run, so naming the command is the only honest
                # help left.
                print("Re-run with sudo to install it from here, or run: "  # in-domain-ok: no dialog layer + no root, in-app install impossible
                      "apt-get install whiptail")
            else:
                try:
                    answer = input("Install whiptail now via apt? [y/N] ")
                except (EOFError, KeyboardInterrupt):
                    answer = ''
                if answer.strip().lower() == 'y':
                    try:
                        rc = subprocess.run(
                            ['apt-get', 'install', '-y', 'whiptail'],
                            timeout=300,
                        ).returncode
                    except (subprocess.SubprocessError, OSError) as e:
                        print(f"Install failed: {e}")
                        rc = 1
                    if rc == 0:
                        self.dialog = DialogBackend()
                        if self.dialog.available:
                            self._tui_context.dialog = self.dialog
                            bar = getattr(self, '_status_bar', None)
                            if bar:
                                self.dialog.set_status_bar(bar)
                            print("whiptail installed — starting the TUI...")
                            return True
                    print("Install did not produce a usable whiptail "
                          f"(apt exit code {rc}).")
        print("Zero-dependency RF tools still work without dialogs: "
              "python3 src/standalone.py")
        return False

    def run(self):
        """Run the launcher."""
        if not self.dialog.available:
            if not self._run_basic_launcher():
                # Honest failure: the message was printed, nothing was drawn.
                raise SystemExit(1)
            # Recovered — whiptail was installed in-app; continue normally.

        # Check for root without SUDO_USER (causes RNS auth issues)
        self._check_root_without_sudo_user()

        # Run startup environment checks (v0.4.8) unless --no-startup-checks
        if not self.skip_startup_checks:
            if not self._run_startup_checks():
                return  # User aborted due to conflicts

        # Check for first run and offer setup wizard (Batch 8: via handler)
        first_run_handler = self._registry.get_handler("first_run")
        if first_run_handler:
            first_run_handler.on_startup()

        # Check for service misconfiguration (SPI HAT with USB config)
        startup_health = self._registry.get_handler("startup_health")
        if startup_health:
            startup_health.on_startup()

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
            self._start_health_monitor()

        # Non-blocking update check — sets _updates_available for status hint
        if not self.skip_startup_checks:
            self._check_startup_updates()
        else:
            self._updates_available = 0

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
        """Start the update check in the background — never blocks first paint.

        The check runs git fetch / apt / GitHub queries that can take up to
        ~2 minutes on a flaky network; running it inline blanked the screen
        for that long before the first menu (2026-08-14 audit S1). The only
        shared state is the int ``self._updates_available``, which the main
        menu re-reads on every render — the badge simply appears once the
        thread finishes.
        """
        self._updates_available = 0
        t = threading.Thread(target=self._check_updates_now,
                             name='startup-update-check', daemon=True)
        t.start()
        self._update_check_thread = t  # joinable by tests

    def _check_updates_now(self) -> None:
        """The actual update query — best-effort, failures are silent."""
        try:
            from utils.safe_import import safe_import
            check_fn, _, has_checker = safe_import(
                'updates.version_checker', 'check_all_versions', 'VersionInfo'
            )
            if not has_checker:
                return
            versions = check_fn()
            # Count only ACTIONABLE updates — those Update All can apply and
            # verify. A real-but-out-of-band (firmware) or dedicated-flow
            # (MeshForge self-update) update is not counted here, so the badge
            # never says "N available" when running the update does nothing
            # (the 2026-07-16 nag; badge == the Update All set by construction).
            count = sum(1 for v in versions.values() if v.actionable)
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

    # _check_service_misconfig moved to handlers/startup_health.py (StartupHealthHandler)
    # auto_lock_port removed (Issue #31 — no silent persistent system changes)

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
                # NOC Home — operator landing: transports + health + one-touch fixes
                ("n", "NOC Home            Transports, health, one-touch fixes"),
                # Primary Operations (numbered for quick access)
                ("1", "Dashboard           Status, health, alerts"),
                ("2", "Mesh Networks       Meshtastic, RNS, AREDN"),
                ("3", "RF & SDR            Calculators, SDR monitoring"),
            ]
            choices.append(("4", "Maps & Viz          Coverage maps, topology"))
            choices.append(("5", "Configuration       Radio, services, settings"))
            choices.append(("6", "System              Hardware, logs, Linux tools"))
            choices.append(("7", "Extensions          Maps, bots, add-ons"))
            # Quick Access
            choices.append(("t", "Tactical Ops        SITREP, zones, QR, ATAK"))
            choices.extend([
                ("q", "Quick Actions       Common shortcuts"),
                ("e", "Emergency Mode      Field operations"),
                # Meta
                ("a", "About               Version, help, web client"),
                ("x", "Exit"),
            ])

            try:
                choice = self.dialog.menu(
                    f"MeshForge NOC v{__version__}",
                    status_hint,
                    choices
                )
            except DialogError as e:
                # Genuine dialog-subsystem failure — the only thing the
                # retry budget is for. Escape/Cancel never lands here
                # (menu() returns None for those; review F4).
                consecutive_failures += 1
                if consecutive_failures >= self._MAX_DIALOG_RETRIES:
                    logger.error(
                        "Main menu dialog failed %d consecutive times (%s), exiting",
                        consecutive_failures, e,
                    )
                    break
                logger.warning(
                    "Main menu dialog failed (attempt %d/%d): %s",
                    consecutive_failures, self._MAX_DIALOG_RETRIES, e,
                )
                continue

            if choice == "x":
                break

            if choice is None:
                # Escape/Cancel at the top level is a deliberate user answer
                # — offer the exit it is asking for instead of logging a
                # false dialog-failure ERROR (review F4).
                try:
                    if self.dialog.yesno("Exit MeshForge?",
                                         "Leave the NOC and return to the shell?"):
                        break
                except DialogError:
                    break  # dialog layer died mid-confirm — exit cleanly
                consecutive_failures = 0
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
            "7": ("Extensions", self._extensions_menu),
            "a": ("About", self._about_menu),
        }
        entry = dispatch.get(choice)
        if entry:
            name, method = entry
            self._safe_call(name, method)

    # --- Submenu: Dashboard (1) ---

    def _dashboard_menu(self):
        """Dashboard - Status, health, alerts, propagation."""
        _ORDERING = SECTION_ORDERINGS["dashboard"]
        while True:
            # 'network' is the one cross-section entry (handler lives in
            # "system"); every other legacy item is registry-owned now
            # (Q1 purge 2026-08-14, audit W7 — verified against the live
            # registry before deletion).
            legacy = [
                ("network", "Network Status      Ports, interfaces, conflicts"),
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
                continue
            self._notify_unwired(choice)

    # --- Submenu: Mesh Networks (2) ---

    def _mesh_networks_menu(self):
        """Mesh Networks - Meshtastic, RNS, AREDN."""
        _ORDERING = SECTION_ORDERINGS["mesh_networks"]
        while True:
            # All 7 legacy entries were shadowed by registry tags and
            # filtered out on every render (Q1 purge 2026-08-14, audit W7).
            choices = self._build_section_menu("mesh_networks", [], _ORDERING)

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
            self._notify_unwired(choice)

    # --- NEW Submenu: RF & SDR (3) ---

    def _rf_sdr_menu(self):
        """RF & SDR - Calculators, SDR monitoring."""
        _ORDERING = SECTION_ORDERINGS["rf_sdr"]
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
            self._notify_unwired(choice)

    # --- NEW Submenu: Maps & Viz (4) ---

    def _maps_viz_menu(self):
        """Maps & Visualization - Coverage maps, topology."""
        _ORDERING = SECTION_ORDERINGS["maps_viz"]
        while True:
            # 'quality' is registry-owned (Q1 purge 2026-08-14, audit W7)
            choices = self._build_section_menu("maps_viz", [], _ORDERING)

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
            self._notify_unwired(choice)

    # --- NEW Submenu: Configuration (5) ---

    def _configuration_menu(self):
        """Configuration - Radio, services, settings."""
        _ORDERING = SECTION_ORDERINGS["configuration"]
        while True:
            # 'rns-config' is the one cross-section entry (dispatches to
            # "rns"/"edit" below); the other 7 were registry-shadowed
            # (Q1 purge 2026-08-14, audit W7).
            legacy = [
                ("rns-config", "RNS Config          Reticulum settings"),
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
                continue
            self._notify_unwired(choice)

    # --- NEW Submenu: System (6) ---

    def _system_menu(self):
        """System - Hardware, logs, Linux tools."""
        _ORDERING = SECTION_ORDERINGS["system"]
        while True:
            # All 7 legacy entries were registry-shadowed (Q1 purge
            # 2026-08-14, audit W7).
            choices = self._build_section_menu("system", [], _ORDERING)

            choice = self.dialog.menu(
                "System Tools",
                "System administration:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Registry-based dispatch (all system items converted)
            if not self._registry.dispatch("system", choice):
                self._notify_unwired(choice)

    # --- Submenu: Extensions (7) ---

    def _extensions_menu(self):
        """Extensions - Maps, bots, add-ons."""
        _ORDERING = SECTION_ORDERINGS["extensions"]
        while True:
            # 'mfmaps' is the one cross-section entry (handler lives in
            # "maps_viz", dispatched below); 'meshing' was registry-shadowed
            # (Q1 purge 2026-08-14, audit W7).
            legacy = [
                ("mfmaps", "MeshForge Maps      Multi-source map extension"),
            ]
            choices = self._build_section_menu("extensions", legacy, _ORDERING)

            # Also include maps_viz mfmaps handler
            choice = self.dialog.menu(
                "Extensions",
                "MeshForge ecosystem extensions:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Try extensions section first, then maps_viz for mfmaps
            if not self._registry.dispatch("extensions", choice):
                if not self._registry.dispatch("maps_viz", choice):
                    self._notify_unwired(choice)

    # --- Submenu: About (a) ---

    def _about_menu(self):
        """About - Version, help, web client, system info, changelog."""
        _ORDERING = SECTION_ORDERINGS["about"]
        while True:
            # All 5 legacy entries were registry-shadowed (Q1 purge
            # 2026-08-14, audit W7).
            choices = self._build_section_menu("about", [], _ORDERING)

            choice = self.dialog.menu(
                "About MeshForge",
                "Information, help, and diagnostics:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Registry-based dispatch (all about items converted)
            if not self._registry.dispatch("about", choice):
                self._notify_unwired(choice)

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
    # --debug was deleted 2026-08-14: it was parsed and never read, and file
    # logging already runs at DEBUG level (audit W3).
    parser.add_argument('--no-startup-checks', action='store_true',
                        dest='no_startup_checks',
                        help='Skip startup service health checks and the '
                             'background update check')
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
        launcher.skip_startup_checks = args.no_startup_checks
        launcher.run()
    except SystemExit as e:
        # Deliberate exit (e.g. no dialog backend and no recovery). The
        # finally block below re-raises via sys.exit(exit_code), so the
        # requested code must be preserved here or it would read as 0.
        exit_code = e.code if isinstance(e.code, int) else 1
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
        # Q1 purge 2026-08-14 (audit W6): the old per-attribute cleanup loop
        # here getattr'd _mqtt_subscriber/_mqtt_ws_bridge/_telemetry_poller/
        # _map_server_process — attributes that moved onto their handlers
        # long ago and are never set on the launcher. Real service shutdown
        # is registry.shutdown_all() inside run()'s own finally.
        if launcher is not None:
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
