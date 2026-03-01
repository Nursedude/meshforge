"""
MeshCore diagnostic checks.

Checks for MeshCore library, companion radio device, gateway config,
and bridge status. Follows the same pattern as meshtastic.py checks.
"""

import os
import socket
import time
import logging
from typing import List

from ..models import CheckResult, CheckStatus, CheckCategory
from utils.safe_import import safe_import

# Third-party: meshcore library (optional install)
_meshcore_mod, _HAS_MESHCORE = safe_import('meshcore')

# First-party: direct imports with graceful fallback
try:
    from gateway.config import GatewayConfig as _GatewayConfig
    _HAS_GW_CONFIG = True
except ImportError:
    _GatewayConfig = None
    _HAS_GW_CONFIG = False

try:
    from gateway.gateway_cli import is_gateway_running as _is_gateway_running
    from gateway.gateway_cli import get_gateway_stats as _get_gateway_stats
    _HAS_GW_CLI = True
except ImportError:
    _is_gateway_running = None
    _get_gateway_stats = None
    _HAS_GW_CLI = False

logger = logging.getLogger(__name__)


def check_meshcore_installed() -> CheckResult:
    """Check if meshcore Python library is installed."""
    start = time.time()

    if _HAS_MESHCORE:
        version = getattr(_meshcore_mod, '__version__', 'unknown')
        return CheckResult(
            name="MeshCore library",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.PASS,
            message=f"Installed (v{version})",
            details={"version": version},
            duration_ms=(time.time() - start) * 1000
        )
    else:
        return CheckResult(
            name="MeshCore library",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.WARN,
            message="Not installed",
            fix_hint="pip install meshcore",
            duration_ms=(time.time() - start) * 1000
        )


def check_meshcore_config() -> CheckResult:
    """Check if MeshCore is configured in gateway config."""
    start = time.time()

    if not _HAS_GW_CONFIG:
        return CheckResult(
            name="MeshCore config",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.SKIP,
            message="Gateway config module not available",
            duration_ms=(time.time() - start) * 1000
        )

    try:
        config = _GatewayConfig.load()
    except Exception as e:
        return CheckResult(
            name="MeshCore config",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.WARN,
            message=f"Could not load gateway config: {e}",
            fix_hint="Check ~/.config/meshforge/gateway.yaml",
            duration_ms=(time.time() - start) * 1000
        )

    mc = getattr(config, 'meshcore', None)
    if mc is None:
        return CheckResult(
            name="MeshCore config",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.WARN,
            message="MeshCore section not configured",
            fix_hint="Use MeshCore > Configure in the TUI",
            duration_ms=(time.time() - start) * 1000
        )

    if not mc.enabled:
        return CheckResult(
            name="MeshCore config",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.WARN,
            message="MeshCore is configured but disabled",
            fix_hint="Use MeshCore > Enable/Disable in the TUI",
            details={"enabled": False, "connection_type": mc.connection_type},
            duration_ms=(time.time() - start) * 1000
        )

    # Validate connection settings
    conn_type = mc.connection_type
    if conn_type not in ("serial", "tcp", "ble"):
        return CheckResult(
            name="MeshCore config",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.FAIL,
            message=f"Invalid connection type: {conn_type}",
            fix_hint="Set connection_type to serial, tcp, or ble",
            duration_ms=(time.time() - start) * 1000
        )

    details = {
        "enabled": True,
        "connection_type": conn_type,
    }
    if conn_type == "serial":
        details["device_path"] = mc.device_path
    elif conn_type == "tcp":
        details["tcp_host"] = mc.tcp_host
        details["tcp_port"] = mc.tcp_port

    return CheckResult(
        name="MeshCore config",
        category=CheckCategory.MESHCORE,
        status=CheckStatus.PASS,
        message=f"Configured ({conn_type})",
        details=details,
        duration_ms=(time.time() - start) * 1000
    )


def check_meshcore_device() -> CheckResult:
    """Check if configured MeshCore device is reachable."""
    start = time.time()

    if not _HAS_GW_CONFIG:
        return CheckResult(
            name="MeshCore device",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.SKIP,
            message="Gateway config module not available",
            duration_ms=(time.time() - start) * 1000
        )

    try:
        config = _GatewayConfig.load()
    except Exception:
        return CheckResult(
            name="MeshCore device",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.SKIP,
            message="Could not load gateway config",
            duration_ms=(time.time() - start) * 1000
        )

    mc = getattr(config, 'meshcore', None)
    if mc is None or not mc.enabled:
        return CheckResult(
            name="MeshCore device",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.SKIP,
            message="MeshCore not enabled",
            duration_ms=(time.time() - start) * 1000
        )

    conn_type = mc.connection_type

    if conn_type == "serial":
        device_path = mc.device_path
        if os.path.exists(device_path):
            return CheckResult(
                name="MeshCore device",
                category=CheckCategory.MESHCORE,
                status=CheckStatus.PASS,
                message=f"Serial device present: {device_path}",
                details={"type": "serial", "path": device_path},
                duration_ms=(time.time() - start) * 1000
            )
        else:
            return CheckResult(
                name="MeshCore device",
                category=CheckCategory.MESHCORE,
                status=CheckStatus.FAIL,
                message=f"Serial device not found: {device_path}",
                fix_hint="Check USB connection or update device path",
                details={"type": "serial", "path": device_path},
                duration_ms=(time.time() - start) * 1000
            )

    elif conn_type == "tcp":
        host = mc.tcp_host or "localhost"
        port = mc.tcp_port
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                return CheckResult(
                    name="MeshCore device",
                    category=CheckCategory.MESHCORE,
                    status=CheckStatus.PASS,
                    message=f"TCP connection OK: {host}:{port}",
                    details={"type": "tcp", "host": host, "port": port},
                    duration_ms=(time.time() - start) * 1000
                )
            else:
                return CheckResult(
                    name="MeshCore device",
                    category=CheckCategory.MESHCORE,
                    status=CheckStatus.FAIL,
                    message=f"TCP connection refused: {host}:{port}",
                    fix_hint=f"Check that MeshCore radio is reachable at {host}:{port}",
                    details={"type": "tcp", "host": host, "port": port},
                    duration_ms=(time.time() - start) * 1000
                )
        except Exception as e:
            return CheckResult(
                name="MeshCore device",
                category=CheckCategory.MESHCORE,
                status=CheckStatus.FAIL,
                message=f"TCP connection error: {e}",
                fix_hint=f"Check network connection to {host}:{port}",
                details={"type": "tcp", "host": host, "port": port},
                duration_ms=(time.time() - start) * 1000
            )

    elif conn_type == "ble":
        return CheckResult(
            name="MeshCore device",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.WARN,
            message="BLE transport not yet supported by meshcore_py",
            fix_hint="Use serial or TCP connection instead",
            details={"type": "ble"},
            duration_ms=(time.time() - start) * 1000
        )

    return CheckResult(
        name="MeshCore device",
        category=CheckCategory.MESHCORE,
        status=CheckStatus.SKIP,
        message=f"Unknown connection type: {conn_type}",
        duration_ms=(time.time() - start) * 1000
    )


def check_meshcore_bridge_status() -> CheckResult:
    """Check if gateway bridge is running with MeshCore handler connected."""
    start = time.time()

    if not _HAS_GW_CLI:
        return CheckResult(
            name="MeshCore bridge",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.SKIP,
            message="Gateway CLI module not available",
            duration_ms=(time.time() - start) * 1000
        )

    if not _is_gateway_running():
        return CheckResult(
            name="MeshCore bridge",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.WARN,
            message="Gateway bridge not running",
            fix_hint="Start the gateway bridge from the TUI or CLI",
            duration_ms=(time.time() - start) * 1000
        )

    try:
        stats = _get_gateway_stats()
    except Exception as e:
        return CheckResult(
            name="MeshCore bridge",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.WARN,
            message=f"Could not get bridge stats: {e}",
            duration_ms=(time.time() - start) * 1000
        )

    mc_connected = stats.get('meshcore_connected', False)
    mc_rx = stats.get('meshcore_rx', stats.get('statistics', {}).get('meshcore_rx', 0))
    mc_tx = stats.get('meshcore_tx', stats.get('statistics', {}).get('meshcore_tx', 0))

    if mc_connected:
        return CheckResult(
            name="MeshCore bridge",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.PASS,
            message=f"Connected (RX:{mc_rx} TX:{mc_tx})",
            details={"connected": True, "rx": mc_rx, "tx": mc_tx},
            duration_ms=(time.time() - start) * 1000
        )
    else:
        return CheckResult(
            name="MeshCore bridge",
            category=CheckCategory.MESHCORE,
            status=CheckStatus.WARN,
            message="Bridge running but MeshCore not connected",
            fix_hint="Check MeshCore device connection and config",
            details={"connected": False},
            duration_ms=(time.time() - start) * 1000
        )


def get_all_checks() -> List:
    """Return all MeshCore check functions."""
    return [
        check_meshcore_installed,
        check_meshcore_config,
        check_meshcore_device,
        check_meshcore_bridge_status,
    ]
