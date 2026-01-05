"""
MeshForge Logging Configuration

Provides centralized logging setup for consistent log formatting
and configuration across the application.

Usage:
    from utils.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Message")

Or for module-level configuration:
    from utils.logging_config import setup_logging
    setup_logging(level=logging.DEBUG, log_file="/var/log/meshforge.log")
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional
import threading

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


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    log_format: str = DEFAULT_FORMAT,
    use_colors: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    suppress_libs: bool = True,
) -> None:
    """
    Configure the root logger with consistent settings.

    Args:
        level: Logging level (default INFO)
        log_file: Optional file path for logging
        log_format: Log message format string
        use_colors: Enable colored output in terminal
        max_bytes: Max log file size before rotation
        backup_count: Number of backup files to keep
        suppress_libs: Suppress noisy third-party loggers
    """
    global _initialized

    with _lock:
        if _initialized:
            return

        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # Remove existing handlers
        root_logger.handlers.clear()

        # Console handler with colors
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)

        if use_colors:
            console_formatter = ColoredFormatter(log_format)
        else:
            console_formatter = logging.Formatter(log_format)

        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

        # File handler if specified
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(logging.Formatter(log_format))
            root_logger.addHandler(file_handler)

        # Suppress noisy third-party loggers
        if suppress_libs:
            for lib_name in [
                'urllib3',
                'requests',
                'werkzeug',
                'flask',
                'meshtastic',
                'serial',
                'asyncio',
                'PIL',
            ]:
                logging.getLogger(lib_name).setLevel(logging.WARNING)

        _initialized = True


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance with the given name.

    This ensures logging is configured before returning the logger.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured Logger instance
    """
    # Ensure basic setup
    if not _initialized:
        setup_logging()

    return logging.getLogger(name)


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
    """Enable debug logging"""
    set_level(logging.DEBUG, logger_name)


def suppress_logger(logger_name: str) -> None:
    """Suppress a specific logger to WARNING level"""
    logging.getLogger(logger_name).setLevel(logging.WARNING)


# Convenience aliases
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL
