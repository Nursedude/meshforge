"""Deprecated: Use utils.logging_config instead.

All installer logging functionality has been merged into logging_config.py.
This stub re-exports for backward compatibility.

For main MeshForge code, use:
    from utils.logging_config import get_logger
    logger = get_logger(__name__)
"""

from utils.logging_config import (  # noqa: F401
    setup_installer_logger as setup_logger,
    _get_installer_logger as get_logger,
    log,
    log_command,
    log_installer_exception as log_exception,
)
