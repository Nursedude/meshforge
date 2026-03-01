"""
Handler Protocol — TUIContext and CommandHandler for registry-based dispatch.

Replaces the 49-mixin inheritance chain on MeshForgeLauncher with a
Protocol + Registry pattern. Each handler is a self-contained class that
receives a shared TUIContext instead of accessing state via ``self``.

Phase 0 of the migration: defines the interfaces only, no behavior change.

See also:
    handler_registry.py — HandlerRegistry (register/lookup/dispatch)
    handlers/            — Converted handler implementations
"""

import logging
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple, runtime_checkable

from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass
class TUIContext:
    """Shared state and utilities for TUI command handlers.

    Created once in MeshForgeLauncher.__init__() and passed to every handler
    via HandlerRegistry.register(). Replaces the implicit ``self.*`` access
    pattern from the mixin architecture.

    Attributes:
        dialog: DialogBackend instance for whiptail/dialog menus.
        env_state: EnvironmentState from startup checks (may be None).
        startup_checker: StartupChecker instance (may be None).
        status_bar: StatusBar instance (may be None).
        feature_flags: Deployment-profile feature flags.
        profile: Active deployment profile object (may be None).
        src_dir: Path to the ``src/`` directory.
        env: Environment dict from ``_detect_environment()``.
        registry: Back-reference to the HandlerRegistry (set after construction).
    """

    dialog: Any  # DialogBackend — avoid circular import
    env_state: Optional[Any] = None
    startup_checker: Optional[Any] = None
    status_bar: Optional[Any] = None
    feature_flags: dict = field(default_factory=dict)
    profile: Optional[Any] = None
    src_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    env: dict = field(default_factory=dict)
    registry: Optional[Any] = None  # HandlerRegistry — set after construction
    daemon_active: bool = False  # True when meshforged owns services

    # Internal cached values
    _meshtastic_path: Optional[str] = field(default=None, repr=False)

    def feature_enabled(self, feature: str) -> bool:
        """Check if a feature is enabled in the current deployment profile.

        When no profile is set, all features are enabled (backward compatible).
        """
        if not self.feature_flags:
            return True
        return self.feature_flags.get(feature, True)

    @staticmethod
    def wait_for_enter(msg: str = "\nPress Enter to continue...") -> None:
        """Wait for user to press Enter, handling Ctrl+C gracefully."""
        from backend import clear_screen
        try:
            input(msg)
        except (KeyboardInterrupt, EOFError):
            pass
        clear_screen()

    def get_meshtastic_cli(self) -> str:
        """Find the meshtastic CLI binary path, with caching."""
        if self._meshtastic_path is None:
            from utils.cli import find_meshtastic_cli
            self._meshtastic_path = find_meshtastic_cli() or 'meshtastic'
        return self._meshtastic_path

    @staticmethod
    def validate_hostname(host: str) -> bool:
        """Validate hostname or IP address for use in network commands."""
        if not host or len(host) > 253:
            return False
        if host.startswith('-'):
            return False
        return bool(re.match(r'^[a-zA-Z0-9.\-:]+$', host))

    @staticmethod
    def validate_port(port_str: str) -> bool:
        """Validate a network port number string."""
        try:
            port = int(port_str)
            return 1 <= port <= 65535
        except (ValueError, TypeError):
            return False

    def get_error_log_path(self) -> Path:
        """Get the path to the TUI error log file."""
        try:
            from utils.paths import get_real_user_home
            log_dir = get_real_user_home() / ".cache" / "meshforge" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir / "tui_errors.log"
        except Exception as e:
            logger.debug("Cannot create log directory, using /tmp fallback: %s", e)
            return Path("/tmp/meshforge_tui_errors.log")

    def log_error(self, context: str, exc: Exception) -> None:
        """Write error details to the TUI error log file."""
        try:
            import datetime
            log_path = self.get_error_log_path()

            _MAX_LOG_BYTES = 1_048_576
            try:
                if log_path.exists() and log_path.stat().st_size > _MAX_LOG_BYTES:
                    rotated = log_path.with_suffix('.log.1')
                    if rotated.exists():
                        rotated.unlink()
                    log_path.rename(rotated)
            except OSError:
                pass

            with open(log_path, 'a') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{datetime.datetime.now().isoformat()}] {context}\n")
                f.write(f"Exception: {type(exc).__name__}: {exc}\n")
                f.write(traceback.format_exc())
                f.write(f"{'='*60}\n")
        except Exception:
            pass

    def safe_call(self, name: str, method, *args, **kwargs):
        """Safely call a handler method with exception handling.

        Mirrors MeshForgeLauncher._safe_call() so handlers get the same
        error-dialog behavior as mixin methods.
        """
        import subprocess as _sp
        try:
            return method(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except ImportError as e:
            module = str(e).replace("No module named ", "").strip("'\"")
            self.log_error(f"ImportError in {name}", e)
            self.dialog.msgbox(
                "Module Not Available",
                f"Required module not installed: {module}\n\n"
                f"This feature requires additional dependencies.\n"
                f"Try: pip3 install {module}\n\n"
                f"Details logged to:\n"
                f"  {self.get_error_log_path()}"
            )
        except _sp.TimeoutExpired as e:
            self.log_error(f"Timeout in {name}", e)
            self.dialog.msgbox(
                "Operation Timed Out",
                f"{name} took too long to respond.\n\n"
                f"Possible causes:\n"
                f"  - Service not responding\n"
                f"  - Network connectivity issue\n"
                f"  - System under heavy load\n\n"
                f"Try checking service status from Dashboard."
            )
        except PermissionError as e:
            self.log_error(f"PermissionError in {name}", e)
            self.dialog.msgbox(
                "Permission Denied",
                f"Insufficient permissions for {name}.\n\n"
                f"{e}\n\n"
                f"Make sure MeshForge is running with sudo."
            )
        except FileNotFoundError as e:
            self.log_error(f"FileNotFoundError in {name}", e)
            self.dialog.msgbox(
                "File Not Found",
                f"A required file or command was not found:\n\n"
                f"{e}\n\n"
                f"The tool or file may not be installed."
            )
        except ConnectionError as e:
            self.log_error(f"ConnectionError in {name}", e)
            self.dialog.msgbox(
                "Connection Failed",
                f"Could not connect to service for {name}.\n\n"
                f"{e}\n\n"
                f"Check that the required service is running."
            )
        except Exception as e:
            self.log_error(f"Unexpected error in {name}", e)
            self.dialog.msgbox(
                "Error",
                f"An error occurred in {name}:\n\n"
                f"{type(e).__name__}: {e}\n\n"
                f"Full details logged to:\n"
                f"  {self.get_error_log_path()}\n\n"
                f"Please report this at:\n"
                f"  github.com/Nursedude/meshforge/issues"
            )


@runtime_checkable
class CommandHandler(Protocol):
    """Protocol for TUI menu command handlers.

    Handlers implement this protocol to participate in registry-based dispatch.
    Each handler owns a set of menu items within a ``menu_section`` and
    executes actions when dispatched by tag.

    Use ``BaseHandler`` for a convenience base class, or implement the
    protocol directly (structural typing — no inheritance required).
    """

    handler_id: str
    menu_section: str

    def menu_items(self) -> List[Tuple[str, str, Optional[str]]]:
        """Return menu entries owned by this handler.

        Returns:
            List of (tag, description, feature_flag_or_None) tuples.
            ``tag`` is the dispatch key, ``description`` is the menu label,
            and ``feature_flag`` (if not None) gates visibility.
        """
        ...

    def execute(self, action: str) -> None:
        """Execute the action identified by ``action`` tag."""
        ...

    def set_context(self, ctx: TUIContext) -> None:
        """Receive the shared TUI context."""
        ...


@runtime_checkable
class LifecycleHandler(Protocol):
    """Optional protocol for handlers that need startup/shutdown hooks.

    Implement this alongside CommandHandler for handlers that manage
    background services (MQTT subscriber, map server, etc.).
    """

    def on_startup(self) -> None:
        """Called once during MeshForgeLauncher.run() before the main menu."""
        ...

    def on_shutdown(self) -> None:
        """Called once during MeshForgeLauncher cleanup."""
        ...


class BaseHandler:
    """Convenience base class for handlers.

    Not required — the CommandHandler Protocol uses structural typing.
    This class provides common boilerplate: context storage, a no-op
    ``menu_items()``, and a ``NotImplementedError`` for ``execute()``.
    """

    handler_id: str = ""
    menu_section: str = ""

    def __init__(self):
        self.ctx: Optional[TUIContext] = None

    def set_context(self, ctx: TUIContext) -> None:
        self.ctx = ctx

    def menu_items(self) -> List[Tuple[str, str, Optional[str]]]:
        return []

    def execute(self, action: str) -> None:
        raise NotImplementedError(
            f"{type(self).__name__}.execute() not implemented for action={action!r}"
        )
