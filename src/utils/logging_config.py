"""
MeshForge Logging Configuration

Unified logging module — all logging setup in one place.

Provides:
- Centralized setup for console, file, and journald logging
- Structured JSON logging (StructuredFormatter, setup_structured_logging)
- Installer-compatible API (setup_installer_logger, log, log_command, log_exception)
- Sudo-safe log directory with ownership fix
- Component-level log control (per-module verbosity)
- Decorators and context managers for instrumented logging
- UI callback forwarding for real-time log display

Designed for Raspberry Pi deployments with journalctl integration:
    - Logs to journald when available (viewable via: journalctl -t meshforge)
    - Falls back to file/console logging
    - Structured logging for easy parsing

Usage:
    from utils.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Message")

Or for module-level configuration:
    from utils.logging_config import setup_logging
    setup_logging(level=logging.DEBUG, log_file="/var/log/meshforge.log")

TUI mode (suppress console to avoid whiptail corruption):
    setup_logging(log_level=logging.DEBUG, log_to_file=True, log_to_console=False)

View logs on RPi:
    journalctl -t meshforge -f          # Follow live logs
    journalctl -t meshforge --since today
    journalctl -t meshforge -p err      # Errors only
"""

import functools
import json
import logging
import logging.handlers
import os
import sys
import time
import traceback
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Callable, Any

# Sudo-safe home directory (MF001 — never use Path.home() directly)
from utils.paths import get_real_user_home

# Thread-safe initialization
_initialized = False
_lock = threading.Lock()

# Default format
DEFAULT_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
SIMPLE_FORMAT = "%(levelname)s: %(message)s"
DEBUG_FORMAT = "%(asctime)s | %(name)s:%(lineno)d | %(levelname)s | %(message)s"

# Color codes for terminal (if coloredlogs not available)
LEVEL_COLORS = {
    'DEBUG': '\033[36m',     # Cyan
    'INFO': '\033[32m',      # Green
    'WARNING': '\033[33m',   # Yellow
    'ERROR': '\033[31m',     # Red
    'CRITICAL': '\033[35m',  # Magenta
}
RESET = '\033[0m'

# Default log directory — uses real user's home even when running with sudo
LOG_DIR = get_real_user_home() / ".config" / "meshforge" / "logs"

# Global log level setting (can be changed at runtime)
_global_log_level = logging.DEBUG

# Component-specific log levels
_component_levels = {
    'hamclock': logging.DEBUG,
    'rns': logging.DEBUG,
    'meshtastic': logging.INFO,
    'gateway': logging.DEBUG,
}

# Shared handler references for runtime level changes
_file_handler: Optional[logging.Handler] = None
_console_handler: Optional[logging.Handler] = None


# ============================================================================
# Sudo-safe path handling
# ============================================================================

def _get_sudo_user_ids():
    """Get the UID and GID of the real user when running with sudo."""
    import pwd

    sudo_user = os.environ.get('SUDO_USER')
    if not sudo_user or sudo_user == 'root':
        return None, None

    try:
        pw = pwd.getpwnam(sudo_user)
        return pw.pw_uid, pw.pw_gid
    except KeyError:
        return None, None


def _fix_directory_ownership(path: Path) -> None:
    """
    Fix directory ownership when running with sudo.

    When running with sudo, directories are created as root. This function
    changes ownership back to the real user (SUDO_USER) so they can access
    the logs without root privileges.
    """
    uid, gid = _get_sudo_user_ids()
    if uid is None:
        return

    try:
        current = path
        while current != Path('/'):
            if current.exists() and current.owner() == 'root':
                os.chown(current, uid, gid)
            if current.name == '.config':
                break
            current = current.parent
    except (PermissionError, OSError):
        pass  # Silently fail — logging should still work


def _fix_file_ownership(path: Path) -> None:
    """Fix file ownership when running with sudo."""
    uid, gid = _get_sudo_user_ids()
    if uid is None:
        return

    try:
        if path.exists():
            os.chown(path, uid, gid)
    except (PermissionError, OSError):
        pass


# ============================================================================
# Colored formatter
# ============================================================================

class ColoredFormatter(logging.Formatter):
    """Formatter that adds colors to log levels for terminal output."""

    def __init__(self, fmt=None, datefmt=None, use_colors=True):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors and sys.stdout.isatty()

    def format(self, record):
        if self.use_colors:
            levelname = record.levelname
            if levelname in LEVEL_COLORS:
                record.levelname = f"{LEVEL_COLORS[levelname]}{levelname}{RESET}"
        return super().format(record)


# ============================================================================
# Core setup
# ============================================================================

def setup_logging(
    level: int = logging.INFO,
    log_level: Optional[int] = None,
    log_file: Optional[str] = None,
    log_format: str = DEFAULT_FORMAT,
    use_colors: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    suppress_libs: bool = True,
    log_to_file: bool = False,
    log_to_console: bool = True,
    max_file_size: Optional[int] = None,
) -> None:
    """
    Configure the root logger with consistent settings.

    Supports two calling conventions:
    - Gateway style: setup_logging(level=logging.DEBUG)
    - TUI style: setup_logging(log_level=logging.DEBUG, log_to_file=True,
                                log_to_console=False)

    Args:
        level: Logging level (default INFO)
        log_level: Alias for level (TUI convention, takes precedence)
        log_file: Explicit file path for logging
        log_format: Log message format string
        use_colors: Enable colored output in terminal
        max_bytes: Max log file size before rotation
        backup_count: Number of backup files to keep
        suppress_libs: Suppress noisy third-party loggers
        log_to_file: Auto-generate log file in LOG_DIR
        log_to_console: Enable console output (False for TUI mode)
        max_file_size: Alias for max_bytes (TUI convention)
    """
    global _initialized, _file_handler, _console_handler, _global_log_level

    # Parameter aliasing — TUI uses log_level=, gateway uses level=
    if log_level is not None:
        level = log_level
    if max_file_size is not None:
        max_bytes = max_file_size

    with _lock:
        if _initialized:
            return

        _global_log_level = level
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)  # Capture all, filter at handler level

        # Remove existing handlers
        root_logger.handlers.clear()

        # Console handler (unless suppressed for TUI mode)
        if log_to_console:
            _console_handler = logging.StreamHandler(sys.stdout)
            _console_handler.setLevel(level)

            if use_colors:
                console_formatter = ColoredFormatter(log_format)
            else:
                console_formatter = logging.Formatter(log_format)

            _console_handler.setFormatter(console_formatter)
            root_logger.addHandler(_console_handler)

        # File handler — explicit path or auto-generated
        file_path = None
        if log_file:
            file_path = Path(log_file)
        elif log_to_file:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            _fix_directory_ownership(LOG_DIR)
            file_path = LOG_DIR / f"meshforge_{datetime.now().strftime('%Y%m%d')}.log"

        if file_path:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            _file_handler = logging.handlers.RotatingFileHandler(
                str(file_path),
                maxBytes=max_bytes,
                backupCount=backup_count,
            )
            _file_handler.setLevel(logging.DEBUG)  # Log everything to file
            detailed_format = logging.Formatter(
                '%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            _file_handler.setFormatter(detailed_format)
            root_logger.addHandler(_file_handler)

            # Fix ownership for sudo user
            _fix_file_ownership(file_path)

        # Suppress noisy third-party loggers
        if suppress_libs:
            for lib_name in [
                'urllib3',
                'requests',
                'meshtastic',
                'serial',
                'asyncio',
                'PIL',
            ]:
                logging.getLogger(lib_name).setLevel(logging.WARNING)

        _initialized = True


# ============================================================================
# Logger retrieval with component-level support
# ============================================================================

def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance with the given name.

    Ensures logging is configured before returning the logger.
    Applies component-specific log levels if configured.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured Logger instance
    """
    # Ensure basic setup
    if not _initialized:
        setup_logging()

    logger = logging.getLogger(name)

    # Apply component-specific log level
    if name:
        for component, comp_level in _component_levels.items():
            if component in name.lower():
                logger.setLevel(comp_level)
                break

    return logger


# ============================================================================
# Level management
# ============================================================================

def set_log_level(level: int, component: Optional[str] = None) -> None:
    """
    Set log level globally or for a specific component.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        component: Optional component name to set level for
    """
    global _global_log_level

    if component:
        _component_levels[component.lower()] = level
        # Update existing loggers matching this component
        for name, lgr in logging.Logger.manager.loggerDict.items():
            if isinstance(lgr, logging.Logger) and component.lower() in name.lower():
                lgr.setLevel(level)
    else:
        _global_log_level = level
        if _console_handler:
            _console_handler.setLevel(level)


def set_level(level: int, logger_name: str = None) -> None:
    """
    Set logging level for a specific logger or root logger.

    Args:
        level: Logging level
        logger_name: Optional specific logger name
    """
    if logger_name:
        logging.getLogger(logger_name).setLevel(level)
    else:
        logging.getLogger().setLevel(level)


def enable_debug(logger_name: str = None) -> None:
    """Enable debug logging."""
    set_level(logging.DEBUG, logger_name)


def suppress_logger(logger_name: str) -> None:
    """Suppress a specific logger to WARNING level."""
    logging.getLogger(logger_name).setLevel(logging.WARNING)


# Convenience aliases
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL


# ============================================================================
# Journald Integration (for RPi/systemd deployments)
# ============================================================================

from utils.safe_import import safe_import

_JournalHandler, _journald_available = safe_import('systemd.journal', 'JournalHandler')
_JournaldLogHandler = _JournalHandler


def is_journald_available() -> bool:
    """Check if systemd journald logging is available."""
    return _journald_available


def setup_journald_logging(
    level: int = logging.INFO,
    identifier: str = "meshforge",
    suppress_libs: bool = True,
) -> bool:
    """
    Configure logging to use systemd journald.

    This is the preferred logging method on Raspberry Pi and other
    systemd-based systems. Logs can be viewed with:
        journalctl -t meshforge -f

    Args:
        level: Logging level
        identifier: Syslog identifier (shows in journalctl -t <identifier>)
        suppress_libs: Suppress noisy third-party loggers

    Returns:
        True if journald was configured, False if unavailable
    """
    global _initialized

    if not _journald_available:
        return False

    with _lock:
        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # Remove existing handlers
        root_logger.handlers.clear()

        # Add journald handler
        journal_handler = _JournaldLogHandler(SYSLOG_IDENTIFIER=identifier)
        journal_handler.setLevel(level)

        # Use structured format for journald
        journal_formatter = logging.Formatter(
            "%(name)s | %(levelname)s | %(message)s"
        )
        journal_handler.setFormatter(journal_formatter)
        root_logger.addHandler(journal_handler)

        # Suppress noisy third-party loggers
        if suppress_libs:
            for lib_name in [
                'urllib3', 'requests',
                'meshtastic', 'serial', 'asyncio', 'PIL',
            ]:
                logging.getLogger(lib_name).setLevel(logging.WARNING)

        _initialized = True
        return True


# ============================================================================
# Log Callbacks for UI Integration
# ============================================================================

_log_callbacks: List[Callable[[logging.LogRecord], None]] = []
_callback_lock = threading.Lock()


class CallbackHandler(logging.Handler):
    """Handler that forwards log records to registered callbacks."""

    def emit(self, record: logging.LogRecord) -> None:
        with _callback_lock:
            callbacks = list(_log_callbacks)

        for callback in callbacks:
            try:
                callback(record)
            except Exception:
                pass  # Don't let callback errors break logging


def register_log_callback(callback: Callable[[logging.LogRecord], None]) -> None:
    """
    Register a callback to receive log records.

    This allows UI components to display logs in real-time.

    Args:
        callback: Function that receives logging.LogRecord objects
    """
    with _callback_lock:
        if callback not in _log_callbacks:
            _log_callbacks.append(callback)


def unregister_log_callback(callback: Callable[[logging.LogRecord], None]) -> None:
    """Unregister a previously registered callback."""
    with _callback_lock:
        if callback in _log_callbacks:
            _log_callbacks.remove(callback)


def enable_ui_logging() -> None:
    """Enable log forwarding to UI callbacks."""
    root_logger = logging.getLogger()

    # Check if callback handler already exists
    for handler in root_logger.handlers:
        if isinstance(handler, CallbackHandler):
            return

    callback_handler = CallbackHandler()
    callback_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(callback_handler)


# ============================================================================
# Smart Setup (auto-detect best logging method)
# ============================================================================

def setup_smart_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    identifier: str = "meshforge",
    use_colors: bool = True,
    enable_callbacks: bool = True,
) -> str:
    """
    Automatically configure the best logging method for the environment.

    Priority:
    1. Journald (if available on systemd systems)
    2. File logging (if log_file specified)
    3. Console logging (fallback)

    Args:
        level: Logging level
        log_file: Optional log file path
        identifier: Syslog identifier for journald
        use_colors: Enable colored console output
        enable_callbacks: Enable UI callback handler

    Returns:
        String describing which method was configured
    """
    method = "console"

    # Try journald first (preferred on RPi)
    if _journald_available and os.path.exists('/run/systemd/system'):
        if setup_journald_logging(level, identifier):
            method = "journald"

    # If journald not used, fall back to standard setup
    if method != "journald":
        setup_logging(
            level=level,
            log_file=log_file,
            use_colors=use_colors,
        )
        method = "file" if log_file else "console"

    # Enable UI callbacks if requested
    if enable_callbacks:
        enable_ui_logging()

    return method


# ============================================================================
# Log Record Utilities
# ============================================================================

def format_log_record(record: logging.LogRecord) -> dict:
    """
    Format a log record as a dictionary for UI display.

    Args:
        record: The log record to format

    Returns:
        Dictionary with timestamp, level, name, message
    """
    return {
        'timestamp': datetime.fromtimestamp(record.created).isoformat(),
        'level': record.levelname,
        'name': record.name,
        'message': record.getMessage(),
        'lineno': record.lineno,
        'pathname': record.pathname,
    }


def get_level_color(level: str) -> str:
    """Get ANSI color code for a log level."""
    return LEVEL_COLORS.get(level, '')


# ============================================================================
# Decorators and Context Managers (from logging_utils)
# ============================================================================

def log_button_click(func: Callable) -> Callable:
    """
    Decorator to log button click events with timing and error handling.

    Usage:
        @log_button_click
        def _on_connect(self, button):
            # handler code
    """
    @functools.wraps(func)
    def wrapper(self, button, *args, **kwargs):
        lgr = getattr(self, 'logger', None) or logging.getLogger(self.__class__.__name__)
        func_name = func.__name__
        button_label = button.get_label() if hasattr(button, 'get_label') else 'unknown'

        lgr.debug(f"Button clicked: {func_name} (label: {button_label})")
        start_time = time.time()

        try:
            result = func(self, button, *args, **kwargs)
            elapsed = time.time() - start_time
            lgr.debug(f"Button handler {func_name} completed in {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            lgr.error(f"Button handler {func_name} failed after {elapsed:.3f}s: {e}")
            lgr.debug(f"Traceback:\n{traceback.format_exc()}")
            raise

    return wrapper


def log_action(action_name: str) -> Callable:
    """
    Decorator to log any action with timing and error handling.

    Usage:
        @log_action("connecting to HamClock")
        def _connect(self):
            # action code
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            self = args[0] if args else None
            lgr = getattr(self, 'logger', None) or logging.getLogger(func.__module__)

            lgr.info(f"Starting: {action_name}")
            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                lgr.info(f"Completed: {action_name} ({elapsed:.3f}s)")
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                lgr.error(f"Failed: {action_name} ({elapsed:.3f}s) - {e}")
                lgr.debug(f"Traceback:\n{traceback.format_exc()}")
                raise

        return wrapper
    return decorator


class LogContext:
    """
    Context manager for logging code blocks with timing and error handling.

    Usage:
        with LogContext(logger, "processing data"):
            # Code that might fail
            process_data()
    """

    def __init__(self, logger: logging.Logger, operation: str, level: int = logging.DEBUG):
        self.logger = logger
        self.operation = operation
        self.level = level
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        self.logger.log(self.level, f"Starting: {self.operation}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.time() - self.start_time

        if exc_type:
            self.logger.error(f"Failed: {self.operation} ({elapsed:.3f}s) - {exc_val}")
            self.logger.debug(f"Traceback:\n{''.join(traceback.format_tb(exc_tb))}")
            return False  # Don't suppress the exception
        else:
            self.logger.log(self.level, f"Completed: {self.operation} ({elapsed:.3f}s)")
            return True


class ThreadLogger:
    """
    Logger wrapper for background threads with thread ID tracking.

    Usage:
        thread_logger = ThreadLogger(logger, "network-fetch")
        def fetch_data():
            thread_logger.info("Fetching...")
            thread_logger.debug("Received 100 bytes")
    """

    def __init__(self, logger: logging.Logger, thread_name: str):
        self.logger = logger
        self.thread_name = thread_name
        self.thread_id = None

    def _prefix(self) -> str:
        tid = threading.get_ident()
        return f"[{self.thread_name}:{tid}] "

    def debug(self, msg: str, *args, **kwargs):
        self.logger.debug(self._prefix() + msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        self.logger.info(self._prefix() + msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self.logger.warning(self._prefix() + msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self.logger.error(self._prefix() + msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        self.logger.exception(self._prefix() + msg, *args, **kwargs)


def log_exception(logger: logging.Logger, msg: str = "Unexpected error") -> None:
    """
    Log an exception with full traceback.

    Usage:
        try:
            risky_operation()
        except Exception:
            log_exception(logger, "Failed to perform risky operation")
    """
    logger.error(f"{msg}:")
    logger.error(traceback.format_exc())


# ═══════════════════════════════════════════════════════════════════════
# Structured JSON Logging (merged from logging_structured.py)
# ═══════════════════════════════════════════════════════════════════════


class StructuredFormatter(logging.Formatter):
    """JSON formatter producing one JSON object per log line (.jsonl format)."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            'ts': datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec='microseconds'),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
            'module': record.module,
            'line': record.lineno,
            'thread': record.threadName,
            'exc': None,
        }

        if record.exc_info and record.exc_info[0]:
            log_obj['exc'] = traceback.format_exception(
                record.exc_info[0],
                record.exc_info[1],
                record.exc_info[2],
            )

        return json.dumps(log_obj, ensure_ascii=False)


def setup_structured_logging(
    log_dir: Optional[Path] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    min_level: int = logging.INFO,
) -> logging.Handler:
    """
    Add structured JSON logging handler to root logger.

    Args:
        log_dir: Directory for log files (default: ~/.config/meshforge/logs/)
        max_bytes: Max file size before rotation
        backup_count: Number of rotated files to keep
        min_level: Minimum log level to capture

    Returns:
        The configured handler (for testing/removal)
    """
    if log_dir is None:
        log_dir = get_real_user_home() / ".config" / "meshforge" / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "meshforge_structured.jsonl"

    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8',
    )
    handler.setLevel(min_level)
    handler.setFormatter(StructuredFormatter())

    logging.getLogger().addHandler(handler)
    return handler


# ═══════════════════════════════════════════════════════════════════════
# Installer Logger API (merged from logger.py)
# ═══════════════════════════════════════════════════════════════════════

_installer_logger = None


def setup_installer_logger(debug=False, log_file='/var/log/meshtasticd-installer.log'):
    """Setup installer-specific logger.

    For main app logging, use get_logger(__name__) instead.
    """
    global _installer_logger

    _installer_logger = logging.getLogger('meshtasticd_installer')
    _installer_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    _installer_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    try:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        _installer_logger.addHandler(file_handler)
    except PermissionError:
        home_log = get_real_user_home() / '.meshtasticd-installer.log'
        file_handler = logging.FileHandler(home_log)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        _installer_logger.addHandler(file_handler)

    _installer_logger.addHandler(console_handler)
    return _installer_logger


def _get_installer_logger():
    """Get the installer logger (lazy init)."""
    global _installer_logger
    if _installer_logger is None:
        _installer_logger = setup_installer_logger()
    return _installer_logger


def log(message, level='info'):
    """Quick log function for installer code."""
    lgr = _get_installer_logger()
    getattr(lgr, level.lower(), lgr.info)(message)


def log_command(command, result):
    """Log command execution results."""
    lgr = _get_installer_logger()
    lgr.debug(f"Command: {command}")
    lgr.debug(f"Return code: {result.get('returncode', 'N/A')}")
    if result.get('stdout'):
        lgr.debug(f"STDOUT: {result['stdout']}")
    if result.get('stderr'):
        lgr.debug(f"STDERR: {result['stderr']}")


def log_installer_exception(exception, context=''):
    """Log an exception with context (installer API)."""
    lgr = _get_installer_logger()
    if context:
        lgr.error(f"{context}: {str(exception)}", exc_info=True)
    else:
        lgr.error(f"Exception occurred: {str(exception)}", exc_info=True)
