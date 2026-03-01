"""RNS Interface Management — blocking detection and interface disabling.

Extracted from rns_diagnostics.py for file size compliance (CLAUDE.md #6).
These are pure functions (no handler state needed).
"""

import logging
import re
import subprocess
from pathlib import Path

from utils.paths import ReticulumPaths

logger = logging.getLogger(__name__)


def find_blocking_interfaces() -> list:
    """Check if enabled RNS interfaces have missing dependencies.

    Parses /etc/reticulum/config for enabled interfaces and checks
    whether their required services/hosts are available. Returns a
    list of (interface_name, problem, fix) tuples for blocking interfaces.

    This is the root cause of "rnsd active but not listening on 37428":
    rnsd initializes interfaces BEFORE binding the shared instance port.
    A blocking interface (e.g., TCP connect to dead host, missing serial
    device) prevents the shared instance from ever becoming available.
    """
    blocking = []
    config_file = ReticulumPaths.get_config_file()
    if not config_file.exists():
        return blocking

    try:
        content = config_file.read_text()
    except (OSError, PermissionError):
        return blocking

    # Parse enabled interfaces from the config
    # RNS config uses [[InterfaceName]] sections with type= and enabled=
    iface_pattern = re.compile(
        r'^\s*\[\[(.+?)\]\]\s*$'
        r'(.*?)'
        r'(?=^\s*\[\[|\Z)',
        re.MULTILINE | re.DOTALL
    )

    for match in iface_pattern.finditer(content):
        name = match.group(1).strip()
        body = match.group(2)

        # Check if enabled (RNS uses both 'enabled' and 'interface_enabled')
        enabled_match = re.search(
            r'^\s*(?:interface_)?enabled\s*=\s*(yes|true|1)',
            body, re.IGNORECASE | re.MULTILINE
        )
        if not enabled_match:
            continue

        # Check interface type
        type_match = re.search(r'^\s*type\s*=\s*(\S+)', body,
                               re.IGNORECASE | re.MULTILINE)
        if not type_match:
            continue

        iface_type = type_match.group(1)

        # Check Meshtastic_Interface — tcp_port, serial port, or BLE
        if iface_type == 'Meshtastic_Interface':
            _check_meshtastic_interface(name, body, blocking)

        # Check TCPClientInterface → needs reachable host
        elif iface_type == 'TCPClientInterface':
            _check_tcp_client_interface(name, body, blocking)

        # Check RNodeInterface / SerialInterface → serial device must exist
        elif iface_type in ('RNodeInterface', 'SerialInterface', 'KISSInterface'):
            _check_serial_interface(name, body, blocking)

    return blocking


def _check_meshtastic_interface(name: str, body: str, blocking: list):
    """Check Meshtastic_Interface for missing dependencies."""
    tcp_match = re.search(r'^\s*tcp_port\s*=\s*(\S+)', body,
                          re.IGNORECASE | re.MULTILINE)
    port_match = re.search(r'^\s*port\s*=\s*(\S+)', body,
                           re.IGNORECASE | re.MULTILINE)
    ble_match = re.search(r'^\s*ble_port\s*=\s*(\S+)', body,
                          re.IGNORECASE | re.MULTILINE)

    if tcp_match:
        host_port = tcp_match.group(1)
        try:
            r = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            )
            if r.stdout.strip() != 'active':
                blocking.append((
                    name,
                    f"needs meshtasticd ({host_port}) but it is not running",
                    "sudo systemctl start meshtasticd"
                ))
            else:
                import socket
                tcp_host = host_port
                tcp_port_num = 4403
                if ':' in host_port:
                    parts = host_port.rsplit(':', 1)
                    tcp_host = parts[0]
                    try:
                        tcp_port_num = int(parts[1])
                    except ValueError:
                        pass
                try:
                    sock = socket.socket(
                        socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2)
                    sock.connect((tcp_host, tcp_port_num))
                    sock.close()
                except (socket.timeout, ConnectionRefusedError, OSError):
                    blocking.append((
                        name,
                        f"meshtasticd running but TCP port "
                        f"{tcp_host}:{tcp_port_num} not accepting "
                        f"connections (still starting?)",
                        f"Wait for meshtasticd to finish starting, "
                        f"or: sudo systemctl restart meshtasticd"
                    ))
        except (subprocess.SubprocessError, OSError):
            pass
    elif port_match:
        dev = port_match.group(1)
        if dev.startswith('/dev/') and not Path(dev).exists():
            blocking.append((
                name,
                f"serial device {dev} not found (disconnected?)",
                f"Connect the device or disable this interface"
            ))
    elif ble_match:
        ble_target = ble_match.group(1)
        blocking.append((
            name,
            f"BLE connection to {ble_target} may block if device is off",
            "Ensure BLE device is powered on, or disable this interface"
        ))


def _check_tcp_client_interface(name: str, body: str, blocking: list):
    """Check TCPClientInterface for unreachable hosts."""
    host_match = re.search(r'^\s*target_host\s*=\s*(\S+)', body,
                           re.IGNORECASE | re.MULTILINE)
    port_match = re.search(r'^\s*target_port\s*=\s*(\d+)', body,
                           re.IGNORECASE | re.MULTILINE)
    if host_match and port_match:
        host = host_match.group(1)
        port = port_match.group(1)
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect((host, int(port)))
            sock.close()
        except (socket.timeout, ConnectionRefusedError, OSError):
            blocking.append((
                name,
                f"target {host}:{port} is unreachable",
                f"Check if {host}:{port} is online, or disable this interface"
            ))


def _check_serial_interface(name: str, body: str, blocking: list):
    """Check serial-based interfaces for missing devices."""
    port_match = re.search(r'^\s*port\s*=\s*(\S+)', body,
                           re.IGNORECASE | re.MULTILINE)
    if port_match:
        dev = port_match.group(1)
        if dev.startswith('/dev/') and not Path(dev).exists():
            blocking.append((
                name,
                f"serial device {dev} not found (disconnected?)",
                f"Connect the device or disable this interface"
            ))


def disable_interfaces_in_config(interface_names: list) -> list:
    """Disable specific interfaces in the RNS config file.

    Changes 'enabled = yes' to 'enabled = no' for the named interfaces.
    Only modifies /etc/reticulum/config (the system config used by rnsd).

    Args:
        interface_names: List of interface names (matching [[Name]] sections)

    Returns:
        List of interface names that were successfully disabled.
    """
    config_file = ReticulumPaths.get_config_file()
    if not config_file.exists():
        return []

    try:
        content = config_file.read_text()
    except (OSError, PermissionError) as e:
        logger.error("Cannot read RNS config: %s", e)
        return []

    disabled = []
    for name in interface_names:
        pattern = re.compile(
            r'(^\s*\[\[' + re.escape(name) + r'\]\]\s*$'
            r'.*?)'
            r'(^\s*enabled\s*=\s*)(yes|true|1)',
            re.MULTILINE | re.DOTALL | re.IGNORECASE
        )
        new_content, count = pattern.subn(r'\1\g<2>no', content)
        if count > 0:
            content = new_content
            disabled.append(name)

    if disabled:
        try:
            config_file.write_text(content)
            logger.info("Disabled %d blocking interface(s): %s",
                        len(disabled), ", ".join(disabled))
        except (OSError, PermissionError) as e:
            logger.error("Cannot write RNS config: %s", e)
            return []

    return disabled
