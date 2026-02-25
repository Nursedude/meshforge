"""
MeshForge Input Validation Utilities

Shared validators for hostnames, ports, paths, and other user inputs.
Extracted from launcher_tui/main.py for reuse across modules.

Usage:
    from utils.validation import validate_hostname, validate_port

    if validate_hostname(host):
        connect(host)
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def validate_hostname(host: str) -> bool:
    """Validate a hostname or IP address.

    Accepts:
        - IPv4 addresses (e.g., 192.168.1.1)
        - IPv6 addresses (e.g., ::1, [::1])
        - Hostnames (e.g., localhost, mesh.local)

    Rejects:
        - Empty strings
        - Strings over 253 characters
        - Strings starting with hyphen
        - Strings with invalid characters
    """
    if not host or len(host) > 253:
        return False
    host = host.strip()
    if host.startswith('-'):
        return False
    # Allow alphanumeric, dots, hyphens, colons (IPv6), brackets (IPv6)
    return bool(re.match(r'^[a-zA-Z0-9.\-:\[\]]+$', host))


def validate_port(port: int) -> bool:
    """Validate a TCP/UDP port number.

    Args:
        port: Port number to validate

    Returns:
        True if port is in valid range (1-65535)
    """
    try:
        port_int = int(port)
        return 1 <= port_int <= 65535
    except (TypeError, ValueError):
        return False


def validate_node_id(node_id: str) -> bool:
    """Validate a Meshtastic node ID.

    Valid format: !hex string (e.g., !abc123de)
    """
    if not node_id or not node_id.startswith('!'):
        return False
    hex_part = node_id[1:]
    if not hex_part:
        return False
    return bool(re.match(r'^[a-fA-F0-9]+$', hex_part))


def validate_message_length(message: str, max_bytes: int = 228) -> Optional[str]:
    """Validate message doesn't exceed byte limit.

    Meshtastic uses UTF-8 encoding, so multi-byte characters count more.

    Args:
        message: The message to validate
        max_bytes: Maximum allowed bytes (default: Meshtastic limit)

    Returns:
        None if valid, or error string if too long
    """
    if not message:
        return None
    byte_len = len(message.encode('utf-8'))
    if byte_len > max_bytes:
        return f"Message is {byte_len} bytes, max is {max_bytes}"
    return None
