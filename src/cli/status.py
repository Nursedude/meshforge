#!/usr/bin/env python3
"""
MeshForge Status - One-shot terminal status display.

Usage:
    meshforge-status          # Full status
    meshforge-status --brief  # One-line per service
    meshforge-status --json   # Machine-readable

Shows: services, radio info, web client URL, recent errors, system resources.
Designed for headless/SSH use - rich terminal output with ANSI colors.
"""

import os
import sys
import json
import socket
import subprocess
from pathlib import Path

# Ensure src directory is in path for imports
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from utils.safe_import import safe_import

# Module-level safe imports
_check_port, _check_udp_port, _check_systemd_service, _check_process_running, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_port', 'check_udp_port', 'check_systemd_service', 'check_process_running'
)

from utils.cli import find_meshtastic_cli


# ANSI colors
class C:
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    CYAN = '\033[0;36m'
    DIM = '\033[2m'
    BOLD = '\033[1m'
    NC = '\033[0m'

    @staticmethod
    def ok(text):
        return f"{C.GREEN}{text}{C.NC}"

    @staticmethod
    def err(text):
        return f"{C.RED}{text}{C.NC}"

    @staticmethod
    def warn(text):
        return f"{C.YELLOW}{text}{C.NC}"

    @staticmethod
    def info(text):
        return f"{C.CYAN}{text}{C.NC}"

    @staticmethod
    def dim(text):
        return f"{C.DIM}{text}{C.NC}"


def check_service(name):
    """Check if a systemd service is running.

    Uses centralized service_check module when available.
    """
    try:
        if _HAS_SERVICE_CHECK:
            is_running, is_enabled = _check_systemd_service(name)
            status = 'active' if is_running else 'inactive'
        else:
            # Fallback to direct systemctl call
            result = subprocess.run(
                ['systemctl', 'is-active', name],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()

        # If meshforge systemd service isn't active, check for interactive process
        if name == 'meshforge' and status != 'active':
            if _is_meshforge_process_running():
                return 'interactive'

        return status
    except Exception:
        return 'unknown'


def _is_meshforge_process_running():
    """Check if MeshForge is running as an interactive process (not systemd).

    Note: This is a specialized check that filters out the status script itself,
    so it doesn't use the generic check_process_running() from service_check.
    """
    try:
        result = subprocess.run(
            ['pgrep', '-af', 'python.*meshforge|python.*launcher'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Filter out this status script itself
            for line in result.stdout.strip().split('\n'):
                if line and 'status.py' not in line:
                    return True
    except Exception:
        pass
    return False


# UDP ports that require bind-test instead of TCP connect
_UDP_PORTS = {37428}  # RNS shared instance


def check_port(port):
    """Check if a port is in use.

    Uses UDP bind-test for known UDP ports (e.g., RNS 37428),
    TCP connect for everything else.
    """
    if port in _UDP_PORTS:
        if _HAS_SERVICE_CHECK:
            return _check_udp_port(port)
        # Fallback: inline UDP bind test
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1)
            sock.bind(('127.0.0.1', port))
            sock.close()
            return False  # Bind succeeded = port NOT in use
        except OSError as e:
            return e.errno in (98, 48, 10048)  # EADDRINUSE = in use
        finally:
            try:
                sock.close()
            except Exception:
                pass
        return False

    if _HAS_SERVICE_CHECK:
        return _check_port(port, host='127.0.0.1', timeout=1.0)

    # Fallback to direct TCP socket check
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def get_local_ip():
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)  # 2 second timeout for network issues
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def _find_cli():
    """Find meshtastic CLI path using centralized resolver."""
    return find_meshtastic_cli()


def get_radio_info():
    """Get meshtastic radio info via CLI."""
    info = {}
    try:
        cli_path = _find_cli()
        if not cli_path:
            return info
        result = subprocess.run(
            [cli_path, '--info'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'Owner:' in line:
                    info['owner'] = line.split('Owner:')[1].strip()
                elif 'My info:' in line or 'myNodeNum' in line:
                    pass  # Skip raw
                elif 'Short Name:' in line:
                    info['short_name'] = line.split('Short Name:')[1].strip()
                elif 'Hardware:' in line:
                    info['hardware'] = line.split('Hardware:')[1].strip()
                elif 'Firmware:' in line:
                    info['firmware'] = line.split('Firmware:')[1].strip()
                elif 'Num Online Nodes:' in line:
                    info['nodes_online'] = line.split(':')[1].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return info


def get_node_count():
    """Get number of known nodes."""
    try:
        cli_path = _find_cli()
        if not cli_path:
            return None
        result = subprocess.run(
            [cli_path, '--nodes'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Count lines that look like node entries (contain !)
            lines = [l for l in result.stdout.split('\n') if '!' in l and '│' in l]
            return len(lines)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_recent_errors(count=5):
    """Get recent error log entries."""
    errors = []
    try:
        result = subprocess.run(
            ['journalctl', '--since', '1 hour ago', '-p', 'err',
             '--no-pager', '-n', str(count), '-o', 'short-monotonic'],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                if line.strip() and '-- No entries --' not in line:
                    errors.append(line.strip())
    except Exception:
        pass
    return errors


def get_system_resources():
    """Get system resource usage."""
    resources = {}

    # CPU temperature
    try:
        temp_path = Path('/sys/class/thermal/thermal_zone0/temp')
        if temp_path.exists():
            temp_c = int(temp_path.read_text().strip()) / 1000
            resources['temp'] = f"{temp_c:.0f}°C"
    except Exception:
        pass

    # Memory
    try:
        with open('/proc/meminfo') as f:
            meminfo = f.read()
        total = int([l for l in meminfo.split('\n') if 'MemTotal' in l][0].split()[1])
        avail = int([l for l in meminfo.split('\n') if 'MemAvailable' in l][0].split()[1])
        used_pct = ((total - avail) / total) * 100
        resources['memory'] = f"{used_pct:.0f}% ({(total - avail) // 1024}MB / {total // 1024}MB)"
    except Exception:
        pass

    # Disk
    try:
        st = os.statvfs('/')
        total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
        free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        used_pct = ((total_gb - free_gb) / total_gb) * 100
        resources['disk'] = f"{used_pct:.0f}% ({free_gb:.1f}GB free / {total_gb:.1f}GB)"
    except Exception:
        pass

    # Uptime
    try:
        with open('/proc/uptime') as f:
            uptime_sec = float(f.read().split()[0])
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        minutes = int((uptime_sec % 3600) // 60)
        if days > 0:
            resources['uptime'] = f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            resources['uptime'] = f"{hours}h {minutes}m"
        else:
            resources['uptime'] = f"{minutes}m"
    except Exception:
        pass

    # Load average
    try:
        with open('/proc/loadavg') as f:
            load = f.read().split()[:3]
        resources['load'] = ' '.join(load)
    except Exception:
        pass

    return resources


def print_status(brief=False, as_json=False):
    """Print the full status display."""

    # Gather data
    local_ip = get_local_ip()

    services = {
        'meshtasticd': {'status': check_service('meshtasticd'), 'ports': [4403, 9443]},
        'rnsd': {'status': check_service('rnsd'), 'ports': [37428]},
        'meshforge': {'status': check_service('meshforge'), 'ports': []},
    }

    # Check ports
    port_4403 = check_port(4403)
    port_9443 = check_port(9443)
    port_37428 = check_port(37428)

    # JSON output
    if as_json:
        data = {
            'ip': local_ip,
            'services': {k: v['status'] for k, v in services.items()},
            'ports': {'4403': port_4403, '9443': port_9443, '37428': port_37428},
            'resources': get_system_resources(),
        }
        print(json.dumps(data, indent=2))
        return

    # Brief output
    if brief:
        for name, info in services.items():
            status = info['status']
            is_up = status in ('active', 'interactive')
            icon = '●' if is_up else '○'
            color = C.ok if is_up else C.err
            label = 'running' if is_up else status
            print(f"  {color(icon)} {name:<15} {label}")
        return

    # Full output
    print()
    print(f"{C.BOLD}MeshForge NOC Status{C.NC}")
    print(f"{C.DIM}{'─' * 50}{C.NC}")

    # Services
    print(f"\n{C.BOLD}Services:{C.NC}")
    for name, info in services.items():
        status = info['status']
        if status == 'active':
            icon = C.ok('●')
            state = C.ok('running')
        elif status == 'interactive':
            icon = C.ok('●')
            state = C.ok('running') + C.dim(' (interactive)')
        elif status == 'inactive':
            icon = C.dim('○')
            state = C.dim('stopped')
        elif status == 'failed':
            icon = C.err('●')
            state = C.err('failed')
        else:
            icon = C.warn('?')
            state = C.warn(status)

        # Port info
        port_info = ''
        if name == 'meshtasticd':
            ports = []
            if port_4403:
                ports.append('4403/TCP')
            if port_9443:
                ports.append('9443/Web')
            port_info = f"  ({', '.join(ports)})" if ports else ''
        elif name == 'rnsd' and port_37428:
            port_info = '  (37428)'

        print(f"  {icon} {name:<15} {state}{C.dim(port_info)}")

    # Web Client
    print(f"\n{C.BOLD}Web Client:{C.NC}")
    if port_9443:
        print(f"  {C.ok('●')} https://{local_ip}:9443")
    else:
        print(f"  {C.dim('○')} Not responding (meshtasticd web port 9443)")

    # Radio info (only if meshtasticd responding)
    if port_4403:
        radio = get_radio_info()
        if radio:
            print(f"\n{C.BOLD}Radio:{C.NC}")
            if 'owner' in radio:
                print(f"  Node:     {radio['owner']}")
            if 'hardware' in radio:
                print(f"  Hardware: {radio['hardware']}")
            if 'firmware' in radio:
                print(f"  Firmware: {radio['firmware']}")
            if 'nodes_online' in radio:
                print(f"  Nodes:    {radio['nodes_online']} online")
            else:
                node_count = get_node_count()
                if node_count is not None:
                    print(f"  Nodes:    {node_count} known")

    # System resources
    resources = get_system_resources()
    if resources:
        print(f"\n{C.BOLD}System:{C.NC}")
        if 'uptime' in resources:
            print(f"  Uptime:  {resources['uptime']}")
        if 'load' in resources:
            print(f"  Load:    {resources['load']}")
        if 'memory' in resources:
            print(f"  Memory:  {resources['memory']}")
        if 'disk' in resources:
            print(f"  Disk:    {resources['disk']}")
        if 'temp' in resources:
            temp_val = float(resources['temp'].replace('°C', ''))
            if temp_val > 70:
                print(f"  Temp:    {C.err(resources['temp'])}")
            elif temp_val > 60:
                print(f"  Temp:    {C.warn(resources['temp'])}")
            else:
                print(f"  Temp:    {resources['temp']}")

    # Recent errors
    errors = get_recent_errors(3)
    if errors:
        print(f"\n{C.BOLD}Recent Errors{C.NC} {C.dim('(last hour)')}{C.NC}:")
        for err in errors[-3:]:
            # Truncate long lines
            if len(err) > 70:
                err = err[:67] + '...'
            print(f"  {C.err('!')} {err}")
    else:
        print(f"\n{C.BOLD}Errors:{C.NC} {C.ok('None in last hour')}")

    # Quick commands
    print(f"\n{C.DIM}{'─' * 50}{C.NC}")
    print(f"{C.DIM}Commands: meshforge (TUI)  meshforge-web (browser)  meshforge-status --brief{C.NC}")
    print()


def main():
    brief = '--brief' in sys.argv or '-b' in sys.argv
    as_json = '--json' in sys.argv or '-j' in sys.argv

    print_status(brief=brief, as_json=as_json)


if __name__ == '__main__':
    main()
