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
from utils.active_health_probe import get_health_probe
from utils.service_check import lock_port_external
# TopologyVisualizer is in handlers/topology.py

# Import centralized path utility - SINGLE SOURCE OF TRUTH for all paths
# See: utils/paths.py (ReticulumPaths, get_real_user_home)
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)
from utils.paths import get_real_user_home

# Import dialog backend directly (not through package namespace)
from backend import DialogBackend, clear_screen

# Import startup checks and conflict resolution (v0.4.8)
from startup_checks import (
    StartupChecker, EnvironmentState, ServiceRunState,
    check_root_without_sudo_user, check_service_misconfig,
    check_startup_updates,
)
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
        """Check if a feature is enabled — delegates to TUIContext."""
        return self._tui_context.feature_enabled(feature)


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
        """Get the path to the TUI error log file — delegates to TUIContext."""
        return self._tui_context.get_error_log_path()

    def _log_error(self, context: str, exc: Exception) -> None:
        """Log error with traceback — delegates to TUIContext."""
        self._tui_context.log_error(context, exc)

    def _safe_call(self, method_name: str, method, *args, **kwargs):
        """Safely call a method with exception handling — delegates to TUIContext."""
        return self._tui_context.safe_call(method_name, method, *args, **kwargs)

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
        check_root_without_sudo_user(self.dialog)

        # Run startup environment checks (v0.4.8)
        if not self._run_startup_checks():
            return  # User aborted due to conflicts

        # Check for first run and offer setup wizard (Batch 8: via handler)
        first_run_handler = self._registry.get_handler("first_run")
        if first_run_handler:
            first_run_handler.on_startup()

        # Check for service misconfiguration (SPI HAT with USB config)
        check_service_misconfig(self.dialog)

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
        self._tui_context._updates_available = check_startup_updates()

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
            status_hint = self._tui_context.get_menu_status_hint()

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

    def _handle_main_choice(self, choice: str):
        """Handle main menu selection — delegates to registry."""
        self._registry.handle_main_choice(choice)

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
