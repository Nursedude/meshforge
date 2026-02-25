"""
Meshtastic diagnostic checks.

Checks for Meshtastic library, CLI, and device connection.
"""

import os
import shutil
import socket
import time
import logging
from pathlib import Path
from typing import List

from ..models import CheckResult, CheckStatus, CheckCategory
from utils.paths import get_real_user_home as _resolve_user_home
from utils.safe_import import safe_import

_meshtastic_mod, _HAS_MESHTASTIC = safe_import('meshtastic')

logger = logging.getLogger(__name__)


def check_meshtastic_installed() -> CheckResult:
    """Check if meshtastic library is installed."""
    start = time.time()
    duration = (time.time() - start) * 1000

    if _HAS_MESHTASTIC:
        return CheckResult(
            name="Meshtastic library",
            category=CheckCategory.MESHTASTIC,
            status=CheckStatus.PASS,
            message="Installed",
            duration_ms=duration
        )
    else:
        return CheckResult(
            name="Meshtastic library",
            category=CheckCategory.MESHTASTIC,
            status=CheckStatus.FAIL,
            message="Not installed",
            fix_hint="pip3 install meshtastic",
            duration_ms=duration
        )


def check_meshtastic_cli() -> CheckResult:
    """Check if meshtastic CLI is available."""
    start = time.time()
    cli_path = shutil.which('meshtastic')

    if cli_path:
        return CheckResult(
            name="Meshtastic CLI",
            category=CheckCategory.MESHTASTIC,
            status=CheckStatus.PASS,
            message=f"Found at {cli_path}",
            details={"path": cli_path},
            duration_ms=(time.time() - start) * 1000
        )
    else:
        # Check user local bin
        local_bin = _resolve_user_home() / '.local' / 'bin' / 'meshtastic'
        if local_bin.exists():
            return CheckResult(
                name="Meshtastic CLI",
                category=CheckCategory.MESHTASTIC,
                status=CheckStatus.PASS,
                message=f"Found at {local_bin}",
                details={"path": str(local_bin)},
                duration_ms=(time.time() - start) * 1000
            )
        return CheckResult(
            name="Meshtastic CLI",
            category=CheckCategory.MESHTASTIC,
            status=CheckStatus.WARN,
            message="Not in PATH",
            fix_hint="pip3 install meshtastic (includes CLI)",
            duration_ms=(time.time() - start) * 1000
        )


def find_serial_devices() -> List[str]:
    """Find Meshtastic-compatible serial devices."""
    devices = []
    dev_path = Path('/dev')

    # Common patterns
    patterns = ['ttyACM*', 'ttyUSB*']

    for pattern in patterns:
        devices.extend([str(d) for d in dev_path.glob(pattern)])

    return devices


def check_meshtastic_connection() -> CheckResult:
    """Check if we can connect to a Meshtastic device."""
    start = time.time()

    # Try TCP first (meshtasticd)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 4403))
        sock.close()

        if result == 0:
            return CheckResult(
                name="Meshtastic connection",
                category=CheckCategory.MESHTASTIC,
                status=CheckStatus.PASS,
                message="TCP connection available (meshtasticd)",
                details={"method": "tcp", "port": 4403},
                duration_ms=(time.time() - start) * 1000
            )
    except Exception:
        pass

    # Check for serial devices
    serial_devices = find_serial_devices()
    if serial_devices:
        return CheckResult(
            name="Meshtastic connection",
            category=CheckCategory.MESHTASTIC,
            status=CheckStatus.PASS,
            message=f"Serial device found: {serial_devices[0]}",
            details={"method": "serial", "devices": serial_devices},
            duration_ms=(time.time() - start) * 1000
        )

    return CheckResult(
        name="Meshtastic connection",
        category=CheckCategory.MESHTASTIC,
        status=CheckStatus.FAIL,
        message="No connection available",
        fix_hint="Start meshtasticd or connect device via USB",
        duration_ms=(time.time() - start) * 1000
    )
