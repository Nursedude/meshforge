"""
MeshForge Logging Utilities - Comprehensive logging framework

Provides:
- Configurable log levels per component
- File and console logging with rotation
- UI event logging helpers
- Performance timing utilities
- Error context capturing
- Button/action logging decorators

Usage:
    from utils.logging_utils import get_logger, log_button_click, LogContext

    logger = get_logger(__name__)

    @log_button_click
    def on_my_button(self, button):
        # Your handler code
        pass

    with LogContext(logger, "processing data"):
        # Code that might fail
        pass
"""

import logging
import logging.handlers
import functools
import time
import traceback
import threading
from pathlib import Path
from typing import Optional, Callable, Any
from datetime import datetime

# Default log directory
LOG_DIR = Path.home() / ".config" / "meshforge" / "logs"

# Global log level setting (can be changed at runtime)
_global_log_level = logging.DEBUG

# Component-specific log levels
_component_levels = {
    'hamclock': logging.DEBUG,
    'university': logging.DEBUG,
    'rns': logging.DEBUG,
    'meshtastic': logging.INFO,
    'gateway': logging.DEBUG,
}

# Shared file handler for all loggers
_file_handler: Optional[logging.Handler] = None
_console_handler: Optional[logging.Handler] = None


def setup_logging(
    log_level: int = logging.DEBUG,
    log_to_file: bool = True,
    log_to_console: bool = True,
    max_file_size: int = 5 * 1024 * 1024,  # 5MB
    backup_count: int = 3
) -> None:
    """
    Configure the MeshForge logging system.

    Call this once at application startup (in launcher.py or main_gtk.py).

    Args:
        log_level: Default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_file: Whether to log to file
        log_to_console: Whether to log to console
        max_file_size: Max size of log file before rotation
        backup_count: Number of backup log files to keep
    """
    global _file_handler, _console_handler, _global_log_level

    _global_log_level = log_level

    # Create log directory
    if log_to_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all, filter at handler level

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )

    # File handler with rotation
    if log_to_file:
        log_file = LOG_DIR / f"meshforge_{datetime.now().strftime('%Y%m%d')}.log"
        _file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_file_size,
            backupCount=backup_count
        )
        _file_handler.setLevel(logging.DEBUG)  # Log everything to file
        _file_handler.setFormatter(detailed_formatter)
        root_logger.addHandler(_file_handler)

    # Console handler
    if log_to_console:
        _console_handler = logging.StreamHandler()
        _console_handler.setLevel(log_level)
        _console_handler.setFormatter(console_formatter)
        root_logger.addHandler(_console_handler)

    # Log startup message
    root_logger.info("=" * 60)
    root_logger.info(f"MeshForge logging initialized - Level: {logging.getLevelName(log_level)}")
    if log_to_file:
        root_logger.info(f"Log file: {log_file}")
    root_logger.info("=" * 60)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific component.

    Args:
        name: Component name (usually __name__)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Check for component-specific log level
    for component, level in _component_levels.items():
        if component in name.lower():
            logger.setLevel(level)
            break

    return logger


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
        # Update existing loggers
        for name, logger in logging.Logger.manager.loggerDict.items():
            if isinstance(logger, logging.Logger) and component.lower() in name.lower():
                logger.setLevel(level)
    else:
        _global_log_level = level
        if _console_handler:
            _console_handler.setLevel(level)


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
        logger = getattr(self, 'logger', None) or logging.getLogger(self.__class__.__name__)
        func_name = func.__name__
        button_label = button.get_label() if hasattr(button, 'get_label') else 'unknown'

        logger.debug(f"Button clicked: {func_name} (label: {button_label})")
        start_time = time.time()

        try:
            result = func(self, button, *args, **kwargs)
            elapsed = time.time() - start_time
            logger.debug(f"Button handler {func_name} completed in {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Button handler {func_name} failed after {elapsed:.3f}s: {e}")
            logger.debug(f"Traceback:\n{traceback.format_exc()}")
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
            # Try to get logger from self if available
            self = args[0] if args else None
            logger = getattr(self, 'logger', None) or logging.getLogger(func.__module__)

            logger.info(f"Starting: {action_name}")
            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                logger.info(f"Completed: {action_name} ({elapsed:.3f}s)")
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"Failed: {action_name} ({elapsed:.3f}s) - {e}")
                logger.debug(f"Traceback:\n{traceback.format_exc()}")
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
            # ... fetch code
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


def create_panel_logger(panel_name: str) -> logging.Logger:
    """
    Create a logger specifically for GTK panels with standard naming.

    Args:
        panel_name: Name of the panel (e.g., "hamclock", "university")

    Returns:
        Configured logger
    """
    logger = get_logger(f"gtk_ui.panels.{panel_name}")
    logger.setLevel(_component_levels.get(panel_name.lower(), _global_log_level))
    return logger


# Utility function for debugging button connections
def log_signal_connection(widget: Any, signal_name: str, handler_name: str) -> None:
    """
    Log when a signal is connected to help debug button/event issues.

    Usage:
        button.connect("clicked", self._on_click)
        log_signal_connection(button, "clicked", "_on_click")
    """
    logger = logging.getLogger("gtk_ui.signals")
    widget_type = type(widget).__name__
    widget_label = ""
    if hasattr(widget, 'get_label'):
        widget_label = f" ('{widget.get_label()}')"
    logger.debug(f"Connected: {widget_type}{widget_label}.{signal_name} -> {handler_name}")
