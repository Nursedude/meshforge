"""Logging utilities for the installer"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


# Global logger instance
logger = None


def setup_logger(debug=False, log_file='/var/log/meshtasticd-installer.log'):
    """Setup application logger"""
    global logger

    # Create logger
    logger = logging.getLogger('meshtasticd_installer')
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Clear existing handlers
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)

    # Console formatter (simpler)
    console_formatter = logging.Formatter(
        '%(levelname)s: %(message)s'
    )
    console_handler.setFormatter(console_formatter)

    # File handler
    try:
        # Create log directory if it doesn't exist
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)

        # File formatter (more detailed)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)

        logger.addHandler(file_handler)
    except PermissionError:
        # If we can't write to /var/log, try user directory
        home_log = get_real_user_home() / '.meshtasticd-installer.log'
        file_handler = logging.FileHandler(home_log)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    logger.addHandler(console_handler)

    return logger


def get_logger():
    """Get the application logger"""
    global logger
    if logger is None:
        logger = setup_logger()
    return logger


def log(message, level='info'):
    """Quick log function"""
    logger = get_logger()

    level = level.lower()
    if level == 'debug':
        logger.debug(message)
    elif level == 'info':
        logger.info(message)
    elif level == 'warning':
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    elif level == 'critical':
        logger.critical(message)
    else:
        logger.info(message)


def log_command(command, result):
    """Log command execution results"""
    logger = get_logger()

    logger.debug(f"Command: {command}")
    logger.debug(f"Return code: {result.get('returncode', 'N/A')}")

    if result.get('stdout'):
        logger.debug(f"STDOUT: {result['stdout']}")

    if result.get('stderr'):
        logger.debug(f"STDERR: {result['stderr']}")


def log_exception(exception, context=''):
    """Log an exception with context"""
    logger = get_logger()

    if context:
        logger.error(f"{context}: {str(exception)}", exc_info=True)
    else:
        logger.error(f"Exception occurred: {str(exception)}", exc_info=True)
