"""
Diagnostics Commands

Provides unified interface for system and network diagnostics.
Used by both GTK and CLI interfaces.
"""

import subprocess
import logging
import socket
import os
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime

from .base import CommandResult

logger = logging.getLogger(__name__)


def check_network_connectivity(host: str = "8.8.8.8", timeout: int = 5) -> CommandResult:
    """
    Check basic network connectivity.

    Args:
        host: Host to ping (default Google DNS)
        timeout: Timeout in seconds
    """
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(timeout), host],
            capture_output=True,
            text=True,
            timeout=timeout + 2
        )
        if result.returncode == 0:
            return CommandResult.ok(
                f"Network connectivity OK ({host})",
                data={'host': host, 'connected': True}
            )
        else:
            return CommandResult.fail(
                f"Cannot reach {host}",
                data={'host': host, 'connected': False}
            )
    except subprocess.TimeoutExpired:
        return CommandResult.fail(f"Ping timeout to {host}")
    except FileNotFoundError:
        return CommandResult.fail("ping command not available")
    except Exception as e:
        return CommandResult.fail(f"Network check error: {e}")


def check_dns_resolution(hostname: str = "google.com") -> CommandResult:
    """
    Check DNS resolution.

    Args:
        hostname: Hostname to resolve
    """
    try:
        ip = socket.gethostbyname(hostname)
        return CommandResult.ok(
            f"DNS resolution OK ({hostname} -> {ip})",
            data={'hostname': hostname, 'ip': ip, 'resolved': True}
        )
    except socket.gaierror as e:
        return CommandResult.fail(
            f"DNS resolution failed for {hostname}",
            error=str(e),
            data={'hostname': hostname, 'resolved': False}
        )
    except Exception as e:
        return CommandResult.fail(f"DNS check error: {e}")


def check_port_open(port: int, host: str = "localhost", timeout: float = 2.0) -> CommandResult:
    """
    Check if a TCP port is open.

    Args:
        port: Port number to check
        host: Host to check (default localhost)
        timeout: Connection timeout
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            return CommandResult.ok(
                f"Port {port} is open on {host}",
                data={'port': port, 'host': host, 'open': True}
            )
        else:
            return CommandResult.fail(
                f"Port {port} is closed on {host}",
                data={'port': port, 'host': host, 'open': False}
            )
    except Exception as e:
        return CommandResult.fail(f"Port check error: {e}")


def get_system_health() -> CommandResult:
    """
    Get comprehensive system health information.

    Returns:
        CommandResult with CPU temp, memory, disk usage
    """
    health = {}

    # CPU temperature
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = int(f.read().strip()) / 1000
            health['cpu_temp_c'] = round(temp, 1)
            health['cpu_temp_status'] = 'ok' if temp < 70 else 'warning' if temp < 80 else 'critical'
    except (FileNotFoundError, ValueError, PermissionError):
        health['cpu_temp_c'] = None
        health['cpu_temp_status'] = 'unknown'

    # Memory usage
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = {}
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip().split()[0]
                    meminfo[key] = int(value)
            total = meminfo.get('MemTotal', 0)
            available = meminfo.get('MemAvailable', 0)
            used = total - available
            if total > 0:
                usage_pct = (used / total) * 100
                health['memory_percent'] = round(usage_pct, 1)
                health['memory_total_mb'] = total // 1024
                health['memory_used_mb'] = used // 1024
                health['memory_status'] = 'ok' if usage_pct < 80 else 'warning' if usage_pct < 95 else 'critical'
    except (FileNotFoundError, ValueError, KeyError, PermissionError):
        health['memory_percent'] = None
        health['memory_status'] = 'unknown'

    # Disk usage
    try:
        statvfs = os.statvfs('/')
        total = statvfs.f_blocks * statvfs.f_frsize
        free = statvfs.f_bavail * statvfs.f_frsize
        used = total - free
        if total > 0:
            usage_pct = (used / total) * 100
            health['disk_percent'] = round(usage_pct, 1)
            health['disk_total_gb'] = round(total / (1024**3), 1)
            health['disk_used_gb'] = round(used / (1024**3), 1)
            health['disk_status'] = 'ok' if usage_pct < 80 else 'warning' if usage_pct < 95 else 'critical'
    except (OSError, ZeroDivisionError):
        health['disk_percent'] = None
        health['disk_status'] = 'unknown'

    # Load average
    try:
        with open('/proc/loadavg', 'r') as f:
            parts = f.read().strip().split()
            health['load_1min'] = float(parts[0])
            health['load_5min'] = float(parts[1])
            health['load_15min'] = float(parts[2])
    except (FileNotFoundError, ValueError, IndexError):
        health['load_1min'] = None

    # Uptime
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.read().split()[0])
            health['uptime_seconds'] = int(uptime_seconds)
            health['uptime_days'] = round(uptime_seconds / 86400, 1)
    except (FileNotFoundError, ValueError):
        health['uptime_seconds'] = None

    # Overall status
    statuses = [health.get('cpu_temp_status'), health.get('memory_status'), health.get('disk_status')]
    if 'critical' in statuses:
        overall = 'critical'
    elif 'warning' in statuses:
        overall = 'warning'
    elif 'unknown' in statuses:
        overall = 'degraded'
    else:
        overall = 'healthy'

    health['overall_status'] = overall

    return CommandResult.ok(
        f"System health: {overall}",
        data=health
    )


def run_gateway_diagnostics() -> CommandResult:
    """
    Run diagnostics specific to gateway operation.

    Checks:
    - meshtasticd service
    - rnsd service
    - Network connectivity
    - Required ports
    """
    from . import service

    results = {
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }
    issues = []

    # Check meshtasticd
    mesh_status = service.check_status('meshtasticd')
    results['checks']['meshtasticd'] = {
        'running': mesh_status.data.get('running', False),
        'status': mesh_status.message
    }
    if not mesh_status.data.get('running'):
        issues.append("meshtasticd not running")

    # Check rnsd
    rns_status = service.check_status('rnsd')
    results['checks']['rnsd'] = {
        'running': rns_status.data.get('running', False),
        'status': rns_status.message
    }
    if not rns_status.data.get('running'):
        issues.append("rnsd not running")

    # Check meshtasticd port (4403)
    port_4403 = check_port_open(4403)
    results['checks']['port_4403'] = {
        'open': port_4403.data.get('open', False),
        'status': port_4403.message
    }
    if not port_4403.data.get('open'):
        issues.append("Port 4403 (meshtasticd) not open")

    # Network connectivity
    network = check_network_connectivity()
    results['checks']['network'] = {
        'connected': network.data.get('connected', False),
        'status': network.message
    }

    # DNS
    dns = check_dns_resolution()
    results['checks']['dns'] = {
        'resolved': dns.data.get('resolved', False),
        'status': dns.message
    }

    results['issues'] = issues
    results['issue_count'] = len(issues)

    if len(issues) == 0:
        return CommandResult.ok(
            "Gateway diagnostics passed",
            data=results
        )
    else:
        return CommandResult.fail(
            f"Gateway diagnostics: {len(issues)} issue(s)",
            data=results
        )


def check_dependencies() -> CommandResult:
    """
    Check if required dependencies are installed.

    Returns:
        CommandResult with dependency status
    """
    deps = {}

    # Check Python packages
    python_packages = ['meshtastic', 'RNS', 'LXMF']
    for pkg in python_packages:
        try:
            __import__(pkg)
            deps[pkg] = {'installed': True, 'type': 'python'}
        except ImportError:
            deps[pkg] = {'installed': False, 'type': 'python'}

    # Check system binaries
    binaries = ['meshtasticd', 'meshtastic', 'rnsd']
    for binary in binaries:
        try:
            result = subprocess.run(
                ['which', binary],
                capture_output=True,
                text=True,
                timeout=5
            )
            deps[binary] = {
                'installed': result.returncode == 0,
                'path': result.stdout.strip() if result.returncode == 0 else None,
                'type': 'binary'
            }
        except Exception:
            # which command failed - assume binary not installed
            deps[binary] = {'installed': False, 'type': 'binary'}

    installed = sum(1 for d in deps.values() if d.get('installed'))
    total = len(deps)

    return CommandResult.ok(
        f"Dependencies: {installed}/{total} installed",
        data={'dependencies': deps, 'installed_count': installed, 'total_count': total}
    )


def get_network_interfaces() -> CommandResult:
    """
    Get network interface information.
    """
    try:
        result = subprocess.run(
            ['ip', '-j', 'addr'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            import json
            interfaces = json.loads(result.stdout)
            return CommandResult.ok(
                f"Found {len(interfaces)} interfaces",
                data={'interfaces': interfaces}
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, ImportError):
        pass

    # Fallback to parsing ip addr
    try:
        result = subprocess.run(
            ['ip', 'addr'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return CommandResult.ok(
            "Network interfaces retrieved",
            raw=result.stdout
        )
    except Exception as e:
        return CommandResult.fail(f"Failed to get network interfaces: {e}")


def run_full_diagnostics() -> CommandResult:
    """
    Run comprehensive system diagnostics.

    Returns:
        CommandResult with full diagnostic report
    """
    report = {
        'timestamp': datetime.now().isoformat(),
        'sections': {}
    }

    # System health
    health = get_system_health()
    report['sections']['system_health'] = health.data

    # Gateway checks
    gateway = run_gateway_diagnostics()
    report['sections']['gateway'] = gateway.data

    # Dependencies
    deps = check_dependencies()
    report['sections']['dependencies'] = deps.data

    # Network
    network = check_network_connectivity()
    report['sections']['network'] = {
        'connectivity': network.data,
        'dns': check_dns_resolution().data
    }

    # Count issues
    total_issues = gateway.data.get('issue_count', 0)
    if health.data.get('overall_status') in ('critical', 'warning'):
        total_issues += 1

    report['total_issues'] = total_issues

    if total_issues == 0:
        return CommandResult.ok(
            "Full diagnostics passed",
            data=report
        )
    else:
        return CommandResult.warn(
            f"Diagnostics completed with {total_issues} issue(s)",
            data=report
        )
