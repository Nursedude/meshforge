"""
Network diagnostic checks.

Checks for network connectivity, DNS, and TCP ports.
"""

import socket
import time
import logging

from ..models import CheckResult, CheckStatus, CheckCategory
from utils.service_check import check_port

logger = logging.getLogger(__name__)


def check_tcp_port(port: int, name: str, optional: bool = False) -> CheckResult:
    """Check if a TCP port is listening."""
    start = time.time()

    try:
        is_open = check_port(port, '127.0.0.1', timeout=2.0)
        duration = (time.time() - start) * 1000

        if is_open:
            return CheckResult(
                name=f"{name} (:{port})",
                category=CheckCategory.NETWORK,
                status=CheckStatus.PASS,
                message="Listening",
                duration_ms=duration
            )
        else:
            return CheckResult(
                name=f"{name} (:{port})",
                category=CheckCategory.NETWORK,
                status=CheckStatus.SKIP if optional else CheckStatus.FAIL,
                message="Not reachable",
                fix_hint=f"Ensure {name} is running",
                duration_ms=duration
            )
    except Exception as e:
        return CheckResult(
            name=f"{name} (:{port})",
            category=CheckCategory.NETWORK,
            status=CheckStatus.FAIL,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )


def check_internet() -> CheckResult:
    """Check internet connectivity."""
    start = time.time()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex(('8.8.8.8', 53))
        sock.close()
        duration = (time.time() - start) * 1000

        if result == 0:
            return CheckResult(
                name="Internet connectivity",
                category=CheckCategory.NETWORK,
                status=CheckStatus.PASS,
                message="Connected",
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="Internet connectivity",
                category=CheckCategory.NETWORK,
                status=CheckStatus.WARN,
                message="No connection",
                fix_hint="Check network configuration",
                duration_ms=duration
            )
    except Exception as e:
        return CheckResult(
            name="Internet connectivity",
            category=CheckCategory.NETWORK,
            status=CheckStatus.WARN,
            message=str(e),
            duration_ms=(time.time() - start) * 1000
        )


def check_dns() -> CheckResult:
    """Check DNS resolution."""
    start = time.time()
    try:
        socket.gethostbyname('google.com')
        duration = (time.time() - start) * 1000
        return CheckResult(
            name="DNS resolution",
            category=CheckCategory.NETWORK,
            status=CheckStatus.PASS,
            message="Working",
            duration_ms=duration
        )
    except socket.gaierror:
        return CheckResult(
            name="DNS resolution",
            category=CheckCategory.NETWORK,
            status=CheckStatus.WARN,
            message="DNS failed",
            fix_hint="Check /etc/resolv.conf",
            duration_ms=(time.time() - start) * 1000
        )
