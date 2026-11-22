"""RNS Interface Management — blocking detection and interface disabling.

Extracted from rns_diagnostics.py for file size compliance (CLAUDE.md #6).
These are pure functions (no handler state needed).
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from utils.paths import ReticulumPaths
from utils.rns_config_reader import RNSConfigError, read_interfaces
from utils.rns_interface_flags import interface_enabled
from utils.service_check import check_service

logger = logging.getLogger(__name__)


def iter_enabled_interfaces(content: str):
    """Yield (name, type, body) for every ENABLED interface with a type.

    The one parse both consumers share — find_blocking_interfaces() judges
    these, and Config Doctor counts them so an empty result can say "no
    enabled interfaces parsed" instead of "all resolve" (vacuous truth,
    Fable review 2026-09-22).
    """
    # Read the way rnsd reads it — the ONE reader (WITNESS-TUI r9): names
    # match the TUI's config rows (blocking_map is keyed by name), an
    # RNodeMulti [[[sub]]] is not an interface, comments are not values.
    # A file rnsd cannot load yields nothing here; callers that must tell
    # that apart from "nothing enabled" call read_interfaces themselves
    # (Config Doctor does). `body` is rebuilt as `key = value` lines from
    # the parsed settings so the _check_* scanners read clean values.
    try:
        interfaces = read_interfaces(content)
    except RNSConfigError as e:
        logger.debug("RNS config not loadable: %s", e)
        return

    for iface in interfaces:
        settings = iface['settings']
        if not interface_enabled(settings):
            continue
        iface_type = settings.get('type', '').strip()
        if not iface_type:
            continue
        body = "\n".join(f"{k} = {v}" for k, v in settings.items()) + "\n"
        yield iface['name'], iface_type, body


def find_blocking_interfaces(content=None) -> list:
    """Check if enabled RNS interfaces have missing dependencies.

    Parses /etc/reticulum/config for enabled interfaces and checks
    whether their required services/hosts are available. Returns a
    list of (interface_name, problem, fix) tuples for blocking interfaces.

    ``content``: config text the caller already read, so a caller that
    judged the read (Config Doctor) is not answered from a SECOND read
    that may fail differently and collapse to ``[]``.

    This is the root cause of "rnsd active but not listening on 37428":
    rnsd initializes interfaces BEFORE binding the shared instance port.
    A blocking interface (e.g., TCP connect to dead host, missing serial
    device) prevents the shared instance from ever becoming available.
    """
    blocking = []
    if content is None:
        config_file = ReticulumPaths.get_config_file()
        if not config_file.exists():
            return blocking

        try:
            content = config_file.read_bytes()   # as rnsd reads it (r10)
        except (OSError, PermissionError):
            return blocking

    for name, iface_type, body in iter_enabled_interfaces(content):
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
            # MF008: service state via check_service(), not raw systemctl.
            if not check_service('meshtasticd').available:
                blocking.append((
                    name,
                    f"needs meshtasticd ({host_port}) but it is not running",
                    "Start meshtasticd from Service Control"
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
                from utils.service_check import check_port
                if not check_port(tcp_port_num, host=tcp_host, timeout=2):
                    blocking.append((
                        name,
                        f"meshtasticd running but TCP port "
                        f"{tcp_host}:{tcp_port_num} not accepting "
                        f"connections (still starting?)",
                        f"Wait for meshtasticd to finish starting, "
                        f"or restart it from Service Control"
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
        from utils.service_check import check_port
        if not check_port(int(port), host=host, timeout=1):
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

    from utils.rns_config_reader import RNSConfigError, read_config_text
    try:
        # A WRITER: strict — never write back over bytes it could not read.
        content = read_config_text(config_file, strict=True)
    except (OSError, PermissionError, RNSConfigError) as e:
        logger.error("Cannot read RNS config: %s", e)
        return []

    from utils.rns_interface_flags import set_section_enabled
    disabled = []
    for name in interface_names:
        # Section-scoped (review 2026-10-08): the old DOTALL regex ran into the
        # NEXT section when this one used `interface_enabled`, and disabled a
        # working interface while reporting this one.
        content, found, changed = set_section_enabled(content, name, False)
        if changed:
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
