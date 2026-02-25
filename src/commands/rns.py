"""
RNS Commands Module

Provides unified interface for Reticulum Network Stack operations.
Manages RNS configuration, service control, and connectivity testing.

Config file: ~/.reticulum/config
"""

import os
import re
import shutil
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field

from .base import CommandResult
from utils.safe_import import safe_import
from utils.service_check import check_service, start_service, stop_service

logger = logging.getLogger(__name__)

HAS_SERVICE_CHECK = True

# RNS module (optional — not installed on all systems)
RNS, _HAS_RNS = safe_import('RNS')


# ============================================================================
# PATH UTILITIES
# ============================================================================

from utils.paths import get_real_user_home, ReticulumPaths


def get_config_path() -> Path:
    """Get path to RNS config file.

    Uses ReticulumPaths which mirrors RNS's own config resolution:
    /etc/reticulum/ -> ~/.config/reticulum/ -> ~/.reticulum/
    """
    return ReticulumPaths.get_config_file()


def get_config_dir() -> Path:
    """Get path to RNS config directory."""
    return ReticulumPaths.get_config_dir()


def get_identity_path() -> Path:
    """Get path to MeshForge gateway identity.

    This is a MeshForge-specific file, so uses get_real_user_home()
    (not RNS paths) to store in the real user's config dir.
    """
    return get_real_user_home() / ".config" / "meshforge" / "gateway_identity"


def create_identities() -> CommandResult:
    """Create RNS and MeshForge gateway identities if they don't exist.

    RNS identity: Created by RNS.Identity() in the config directory.
    Gateway identity: MeshForge-specific, stored in ~/.config/meshforge/.

    Returns:
        CommandResult with created/existing identity info
    """
    results = {'rns_identity': None, 'gateway_identity': None, 'created': []}

    # 1. RNS identity (rnsd config dir)
    config_dir = ReticulumPaths.get_config_dir()
    rns_identity_path = config_dir / 'identity'
    results['rns_identity'] = str(rns_identity_path)

    if rns_identity_path.exists():
        results['rns_identity_status'] = 'exists'
    elif not _HAS_RNS:
        results['rns_identity_status'] = 'error'
        return CommandResult.fail(
            "RNS module not installed — cannot create identity",
            data=results
        )
    else:
        try:
            identity = RNS.Identity()
            config_dir.mkdir(parents=True, exist_ok=True)
            identity.to_file(str(rns_identity_path))
            results['rns_identity_status'] = 'created'
            results['created'].append('rns')
            logger.info(f"Created RNS identity at {rns_identity_path}")
        except Exception as e:
            results['rns_identity_status'] = 'error'
            return CommandResult.fail(
                f"Failed to create RNS identity: {e}",
                data=results
            )

    # 2. Gateway identity (meshforge config dir)
    gw_identity_path = get_identity_path()
    results['gateway_identity'] = str(gw_identity_path)

    if gw_identity_path.exists():
        results['gateway_identity_status'] = 'exists'
    else:
        try:
            identity = RNS.Identity()
            gw_identity_path.parent.mkdir(parents=True, exist_ok=True)
            identity.to_file(str(gw_identity_path))
            results['gateway_identity_status'] = 'created'
            results['created'].append('gateway')
            logger.info(f"Created gateway identity at {gw_identity_path}")
        except Exception as e:
            results['gateway_identity_status'] = 'error'
            return CommandResult.fail(
                f"Failed to create gateway identity: {e}",
                data=results
            )

    created = results['created']
    if created:
        return CommandResult.ok(
            f"Created identities: {', '.join(created)}",
            data=results
        )
    return CommandResult.ok("All identities already exist", data=results)


def get_lxmf_storage_path() -> Path:
    """Get path to LXMF message storage.

    This is a MeshForge-specific directory, so uses get_real_user_home().
    """
    return get_real_user_home() / ".config" / "meshforge" / "lxmf_storage"


# ============================================================================
# CONFIG FILE OPERATIONS
# ============================================================================

def read_config() -> CommandResult:
    """
    Read the RNS configuration file.

    Returns:
        CommandResult with config content and parsed interfaces
    """
    config_path = get_config_path()

    if not config_path.exists():
        return CommandResult.fail(
            "RNS config not found",
            error=f"No config at {config_path}",
            data={'path': str(config_path), 'exists': False}
        )

    try:
        content = config_path.read_text()
        interfaces = _parse_interfaces(content)

        return CommandResult.ok(
            f"Config loaded ({len(interfaces)} interfaces)",
            data={
                'path': str(config_path),
                'content': content,
                'interfaces': interfaces,
                'interface_count': len(interfaces)
            },
            raw=content
        )
    except Exception as e:
        return CommandResult.fail(
            f"Failed to read config: {e}",
            error=str(e),
            data={'path': str(config_path)}
        )


def write_config(content: str, backup: bool = True) -> CommandResult:
    """
    Write RNS configuration file with validation and backup.

    Args:
        content: New config content
        backup: Create backup before writing

    Returns:
        CommandResult indicating success
    """
    config_path = get_config_path()

    # Validate first
    valid, errors = validate_config(content)
    if not valid:
        return CommandResult.fail(
            f"Invalid config: {'; '.join(errors)}",
            error="Validation failed",
            data={'errors': errors}
        )

    backup_path = None

    try:
        # Create backup if requested
        if backup and config_path.exists():
            backup_dir = config_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"config_{timestamp}.bak"
            shutil.copy2(config_path, backup_path)

        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Write atomically (temp file + rename)
        temp_path = config_path.with_suffix('.tmp')
        temp_path.write_text(content)
        temp_path.rename(config_path)

        return CommandResult.ok(
            "Config saved successfully",
            data={
                'path': str(config_path),
                'backup_path': str(backup_path) if backup_path else None,
                'bytes_written': len(content)
            }
        )
    except Exception as e:
        return CommandResult.fail(
            f"Failed to write config: {e}",
            error=str(e)
        )


def validate_config(content: str) -> Tuple[bool, List[str]]:
    """
    Validate RNS config syntax.

    Args:
        content: Config file content

    Returns:
        Tuple of (is_valid, error_list)
    """
    errors = []

    # Check for required section
    if '[reticulum]' not in content.lower():
        errors.append("Missing required [reticulum] section")

    # Check bracket matching
    open_brackets = content.count('[')
    close_brackets = content.count(']')
    if open_brackets != close_brackets:
        errors.append(f"Mismatched brackets: {open_brackets} '[' vs {close_brackets} ']'")

    # Check interface headers
    for line_num, line in enumerate(content.split('\n'), 1):
        stripped = line.strip()
        if stripped.startswith('[[') and not stripped.endswith(']]'):
            errors.append(f"Line {line_num}: Malformed interface header: {stripped}")
        if stripped.startswith('[') and not stripped.startswith('[['):
            if not stripped.endswith(']'):
                errors.append(f"Line {line_num}: Malformed section header: {stripped}")

    # Check for valid interface types
    # Use line-anchored regex to only match 'type' as a standalone key,
    # not as a suffix of other keys (e.g., 'connection_type = tcp').
    # Skip comment lines starting with '#'.
    valid_types = [
        'AutoInterface', 'TCPServerInterface', 'TCPClientInterface',
        'BackboneInterface', 'SerialInterface', 'RNodeInterface',
        'KISSInterface', 'AX25KISSInterface', 'I2PInterface',
        'Meshtastic_Interface', 'UDPInterface', 'PipeInterface'
    ]

    for match in re.finditer(r'^\s*(?!#)type\s*=\s*(\w+)', content, re.MULTILINE):
        iface_type = match.group(1)
        if iface_type not in valid_types:
            errors.append(f"Unknown interface type: {iface_type}")

    return len(errors) == 0, errors


def _parse_share_instance(content: str) -> bool:
    """Check if share_instance is enabled in the [reticulum] section.

    The shared instance allows multiple RNS programs (rnsd, gateway, nomadnet)
    to share a single Reticulum transport instance via UDP port 37428.

    Returns True if share_instance = Yes (or True), False otherwise.
    """
    in_reticulum = False
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if stripped == '[reticulum]':
            in_reticulum = True
            continue
        if stripped.startswith('[') and in_reticulum:
            break  # Left the [reticulum] section
        if in_reticulum and 'share_instance' in stripped and '=' in stripped:
            _, value = stripped.split('=', 1)
            return value.strip().lower() in ('yes', 'true', '1')
    return False


def _parse_interfaces(content: str) -> List[Dict[str, Any]]:
    """Parse interface definitions from config."""
    interfaces = []
    current_interface = None

    for line in content.split('\n'):
        stripped = line.strip()

        # Interface header
        if stripped.startswith('[[') and stripped.endswith(']]'):
            if current_interface:
                interfaces.append(current_interface)
            name = stripped[2:-2]
            current_interface = {'name': name, 'settings': {}}

        # Interface setting
        elif current_interface and '=' in stripped and not stripped.startswith('#'):
            key, value = stripped.split('=', 1)
            current_interface['settings'][key.strip()] = value.strip()

    # Add last interface
    if current_interface:
        interfaces.append(current_interface)

    return interfaces


def create_default_config() -> CommandResult:
    """
    Create a default RNS configuration file.

    Includes AutoInterface for local discovery and Meshtastic_Interface
    for bridging RNS over Meshtastic LoRa (requires Meshtastic_Interface.py
    plugin installed in ~/.reticulum/interfaces/).

    Returns:
        CommandResult with default config
    """
    default_config = """# Reticulum Configuration
# Generated by MeshForge
# https://github.com/Nursedude/meshforge

[reticulum]
  enable_transport = False
  share_instance = Yes
  instance_name = default

[logging]
  loglevel = 4

[interfaces]

  # Local network auto-discovery (UDP multicast, zero-config)
  [[Default Interface]]
    type = AutoInterface
    enabled = Yes

  # RNS over Meshtastic - bridges RNS to LoRa mesh network
  # Requires: Meshtastic_Interface.py plugin in ~/.reticulum/interfaces/
  # Source: https://github.com/landandair/RNS_Over_Meshtastic
  # Connection: TCP to local meshtasticd on port 4403
  [[Meshtastic Interface]]
    type = Meshtastic_Interface
    enabled = true
    mode = gateway
    tcp_port = 127.0.0.1:4403
    data_speed = 0
    hop_limit = 3
    bitrate = 500
"""

    return write_config(default_config, backup=True)


def get_backups() -> CommandResult:
    """
    List available config backups.

    Returns:
        CommandResult with backup list
    """
    backup_dir = get_config_dir() / "backups"

    if not backup_dir.exists():
        return CommandResult.ok(
            "No backups found",
            data={'backups': [], 'count': 0}
        )

    backups = []
    for f in sorted(backup_dir.glob("config_*.bak"), reverse=True):
        stat = f.stat()
        backups.append({
            'path': str(f),
            'name': f.name,
            'size': stat.st_size,
            'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
        })

    return CommandResult.ok(
        f"Found {len(backups)} backups",
        data={'backups': backups, 'count': len(backups)}
    )


def restore_backup(backup_path: str) -> CommandResult:
    """
    Restore config from backup.

    Args:
        backup_path: Path to backup file

    Returns:
        CommandResult indicating success
    """
    backup = Path(backup_path)

    if not backup.exists():
        return CommandResult.fail(f"Backup not found: {backup_path}")

    try:
        content = backup.read_text()
        return write_config(content, backup=True)
    except Exception as e:
        return CommandResult.fail(f"Restore failed: {e}")


# ============================================================================
# INTERFACE MANAGEMENT
# ============================================================================

@dataclass
class InterfaceConfig:
    """RNS interface configuration."""
    name: str
    type: str
    enabled: bool = True
    settings: Dict[str, str] = field(default_factory=dict)

    def to_config_block(self) -> str:
        """Convert to config file format."""
        lines = [f"  [[{self.name}]]"]
        lines.append(f"    type = {self.type}")
        lines.append(f"    enabled = {'yes' if self.enabled else 'no'}")
        for key, value in self.settings.items():
            if key not in ('type', 'enabled', 'name'):
                lines.append(f"    {key} = {value}")
        return '\n'.join(lines)


def list_interfaces() -> CommandResult:
    """
    List configured RNS interfaces.

    Returns:
        CommandResult with interface list
    """
    result = read_config()
    if not result.success:
        return result

    interfaces = result.data.get('interfaces', [])

    return CommandResult.ok(
        f"Found {len(interfaces)} interfaces",
        data={'interfaces': interfaces}
    )


def add_interface(name: str, iface_type: str, settings: Dict[str, Any]) -> CommandResult:
    """
    Add a new interface to RNS config.

    Args:
        name: Interface name
        iface_type: Interface type (TCPServerInterface, etc.)
        settings: Interface settings dict

    Returns:
        CommandResult indicating success
    """
    # Validate name
    if not name or not re.match(r'^[\w\s\-]+$', name):
        return CommandResult.fail(f"Invalid interface name: {name}")

    # Read current config
    result = read_config()
    if not result.success:
        # Create default if none exists
        result = create_default_config()
        if not result.success:
            return result
        result = read_config()

    content = result.data.get('content', '')
    interfaces = result.data.get('interfaces', [])

    # Check for duplicate
    for iface in interfaces:
        if iface['name'] == name:
            return CommandResult.fail(f"Interface '{name}' already exists")

    # Create interface config
    iface = InterfaceConfig(name=name, type=iface_type, settings=settings)
    new_block = iface.to_config_block()

    # Append to config
    new_content = content.rstrip() + '\n\n' + new_block + '\n'

    # Write updated config
    return write_config(new_content)


def remove_interface(name: str) -> CommandResult:
    """
    Remove an interface from RNS config.

    Args:
        name: Interface name to remove

    Returns:
        CommandResult indicating success
    """
    result = read_config()
    if not result.success:
        return result

    content = result.data.get('content', '')

    # Find and remove the interface block
    lines = content.split('\n')
    new_lines = []
    skip_until_next_section = False
    found = False

    for line in lines:
        stripped = line.strip()

        # Check if this is the interface to remove
        if stripped == f'[[{name}]]':
            skip_until_next_section = True
            found = True
            continue

        # Check if we've hit the next section
        if skip_until_next_section:
            if stripped.startswith('[[') or (stripped.startswith('[') and not stripped.startswith('[[')):
                skip_until_next_section = False
                new_lines.append(line)
            # Skip lines until next section
            continue

        new_lines.append(line)

    if not found:
        return CommandResult.fail(f"Interface '{name}' not found")

    new_content = '\n'.join(new_lines)
    return write_config(new_content)


def enable_interface(name: str) -> CommandResult:
    """Enable an interface."""
    return _set_interface_enabled(name, True)


def disable_interface(name: str) -> CommandResult:
    """Disable an interface."""
    return _set_interface_enabled(name, False)


def _set_interface_enabled(name: str, enabled: bool) -> CommandResult:
    """Set interface enabled state."""
    result = read_config()
    if not result.success:
        return result

    content = result.data.get('content', '')
    lines = content.split('\n')
    new_lines = []
    in_target_interface = False
    found = False
    enabled_updated = False

    for line in lines:
        stripped = line.strip()

        # Check if entering target interface
        if stripped == f'[[{name}]]':
            in_target_interface = True
            found = True
            new_lines.append(line)
            continue

        # Check if leaving interface
        if in_target_interface and stripped.startswith('[['):
            in_target_interface = False

        # Update enabled setting
        if in_target_interface and stripped.startswith('enabled'):
            indent = len(line) - len(line.lstrip())
            new_lines.append(' ' * indent + f"enabled = {'yes' if enabled else 'no'}")
            enabled_updated = True
            continue

        new_lines.append(line)

    if not found:
        return CommandResult.fail(f"Interface '{name}' not found")

    new_content = '\n'.join(new_lines)
    return write_config(new_content)


# ============================================================================
# SERVICE MANAGEMENT
# ============================================================================

def get_status() -> CommandResult:
    """
    Get RNS daemon status.

    Uses centralized service_check.check_service() for consistency across
    all MeshForge UIs (GTK, TUI, CLI).

    Returns:
        CommandResult with daemon status
    """
    status = {
        'rnsd_running': False,
        'rnsd_pid': None,
        'config_exists': get_config_path().exists(),
        'identity_exists': get_identity_path().exists(),
    }

    # Use centralized service checker (SINGLE SOURCE OF TRUTH)
    # This checks: UDP port 37428 → pgrep → systemd for consistency
    if HAS_SERVICE_CHECK:
        service_status = check_service('rnsd')
        status['rnsd_running'] = service_status.available
        status['service_state'] = service_status.state.value if hasattr(service_status.state, 'value') else str(service_status.state)
        status['service_message'] = service_status.message
        logger.debug(f"[RNS] rnsd status via check_service: {service_status.state}")
    else:
        # Fallback if service_check unavailable (shouldn't happen in normal use)
        logger.warning("[RNS] service_check not available, using fallback detection")

        # Check for running rnsd via pgrep
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'rnsd'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                status['rnsd_running'] = True
                status['rnsd_pid'] = int(pids[0])
        except Exception as e:
            logger.debug(f"pgrep check failed: {e}")

        # Check systemd service
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'rnsd'],
                capture_output=True,
                text=True,
                timeout=5
            )
            status['systemd_status'] = result.stdout.strip()
            if result.stdout.strip() == 'active':
                status['rnsd_running'] = True
        except Exception as e:
            logger.debug(f"systemctl check failed: {e}")
            status['systemd_status'] = 'unknown'

    # Get interface count
    if status['config_exists']:
        config_result = read_config()
        if config_result.success:
            status['interface_count'] = config_result.data.get('interface_count', 0)

    msg = "rnsd running" if status['rnsd_running'] else "rnsd not running"
    return CommandResult.ok(msg, data=status)


def start_rnsd() -> CommandResult:
    """
    Start the RNS daemon.

    Returns:
        CommandResult indicating success
    """
    # Check if already running
    status = get_status()
    if status.data.get('rnsd_running'):
        return CommandResult.ok(
            "rnsd already running",
            data={'pid': status.data.get('rnsd_pid')}
        )

    # Try systemctl first via centralized helper
    success, msg = start_service('rnsd')
    if success:
        return CommandResult.ok("rnsd started via systemd")

    # Fallback to direct start (non-systemd systems)
    try:
        result = subprocess.run(
            ['rnsd', '--service'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            return CommandResult.ok("rnsd started directly")

        return CommandResult.fail(
            "Failed to start rnsd",
            error=result.stderr
        )

    except FileNotFoundError:
        return CommandResult.not_available(
            "rnsd not installed",
            fix_hint="Install with: pipx install rns"
        )
    except Exception as e:
        return CommandResult.fail(f"Start failed: {e}")


def stop_rnsd() -> CommandResult:
    """
    Stop the RNS daemon.

    Returns:
        CommandResult indicating success
    """
    # Try systemctl first via centralized helper
    success, msg = stop_service('rnsd')
    if success:
        return CommandResult.ok("rnsd stopped via systemd")

    # Fallback to pkill (non-systemd systems)
    try:
        result = subprocess.run(
            ['pkill', '-f', 'rnsd'],
            capture_output=True,
            text=True,
            timeout=10
        )

        return CommandResult.ok("rnsd stopped")

    except Exception as e:
        return CommandResult.fail(f"Stop failed: {e}")


def restart_rnsd() -> CommandResult:
    """Restart the RNS daemon."""
    stop_result = stop_rnsd()
    # Brief pause
    import time
    time.sleep(1)
    return start_rnsd()


# ============================================================================
# CONNECTIVITY & DIAGNOSTICS
# ============================================================================

def check_connectivity() -> CommandResult:
    """
    Check RNS network connectivity.

    Returns:
        CommandResult with connectivity status
    """
    connectivity = {
        'rnsd_running': False,
        'can_import_rns': False,
        'config_valid': False,
        'interfaces_enabled': 0,
        'issues': [],
        'warnings': [],
    }

    # Check rnsd
    status = get_status()
    connectivity['rnsd_running'] = status.data.get('rnsd_running', False)
    if not connectivity['rnsd_running']:
        # Check if NomadNet is holding the port (common conflict)
        nomadnet_running = False
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            nomadnet_running = result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            pass

        if nomadnet_running:
            connectivity['issues'].append(
                "rnsd not running — NomadNet is holding port 37428 (shared instance conflict)"
            )
        else:
            connectivity['issues'].append("rnsd daemon not running")

    # Check RNS import
    if not _HAS_RNS:
        connectivity['issues'].append("RNS Python module not installed")
    else:
        connectivity['can_import_rns'] = True
        connectivity['rns_version'] = RNS.__version__ if hasattr(RNS, '__version__') else 'unknown'

    # Check config
    config_result = read_config()
    if config_result.success:
        content = config_result.data.get('content', '')
        valid, errors = validate_config(content)
        connectivity['config_valid'] = valid
        if not valid:
            connectivity['issues'].extend(errors)

        # Count enabled interfaces
        for iface in config_result.data.get('interfaces', []):
            if iface.get('settings', {}).get('enabled', 'yes') == 'yes':
                connectivity['interfaces_enabled'] += 1

        if connectivity['interfaces_enabled'] == 0:
            connectivity['issues'].append("No interfaces enabled")

        # Check share_instance setting (required for gateway to connect)
        share_instance = _parse_share_instance(content)
        connectivity['share_instance'] = share_instance
        if not share_instance:
            connectivity['warnings'].append(
                "share_instance not enabled in [reticulum] config — "
                "gateway and other RNS clients cannot connect to rnsd"
            )
    else:
        connectivity['issues'].append(f"Config error: {config_result.message}")

    # Check identities (warnings, not blocking issues)
    config_dir = ReticulumPaths.get_config_dir()
    rns_identity = config_dir / 'identity'
    gw_identity = get_identity_path()
    if not rns_identity.exists():
        connectivity['warnings'].append("RNS identity not created")
    if not gw_identity.exists():
        connectivity['warnings'].append("Gateway identity not created")

    # Overall status
    is_ok = (
        connectivity['rnsd_running'] and
        connectivity['can_import_rns'] and
        connectivity['config_valid'] and
        connectivity['interfaces_enabled'] > 0
    )

    if is_ok:
        return CommandResult.ok(
            f"RNS connectivity OK ({connectivity['interfaces_enabled']} interfaces)",
            data=connectivity
        )
    else:
        return CommandResult.fail(
            f"RNS issues: {len(connectivity['issues'])}",
            data=connectivity
        )


def test_path(destination_hash: str, timeout: int = 10) -> CommandResult:
    """
    Test path to an RNS destination.

    Args:
        destination_hash: Hex string of destination hash
        timeout: Timeout in seconds

    Returns:
        CommandResult with path status
    """
    # Note: Validate hash format before attempting RNS import to give
    # better error messages when RNS has cryptography issues
    if not re.match(r'^[0-9a-fA-F]{32}$', destination_hash):
        return CommandResult.fail(
            "Invalid hash format",
            error="Hash must be 32 hex characters"
        )

    if not _HAS_RNS:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pipx install rns"
        )

    try:
        dest_bytes = bytes.fromhex(destination_hash)

        # Check if path exists
        has_path = RNS.Transport.has_path(dest_bytes)

        if has_path:
            return CommandResult.ok(
                "Path exists",
                data={
                    'destination': destination_hash,
                    'has_path': True
                }
            )

        # Request path
        RNS.Transport.request_path(dest_bytes)

        # Wait for path
        import time
        start = time.time()
        while time.time() - start < timeout:
            if RNS.Transport.has_path(dest_bytes):
                return CommandResult.ok(
                    f"Path discovered in {time.time() - start:.1f}s",
                    data={
                        'destination': destination_hash,
                        'has_path': True,
                        'discovery_time': time.time() - start
                    }
                )
            time.sleep(0.1)

        return CommandResult.fail(
            f"No path found within {timeout}s",
            data={
                'destination': destination_hash,
                'has_path': False,
                'timeout': timeout
            }
        )

    except Exception as e:
        # Catch pyo3 PanicException and other RNS errors
        return CommandResult.fail(f"Path test failed: {e}")


def get_path_info(destination_hash: str) -> CommandResult:
    """
    Get detailed path information for an RNS destination.

    Queries the running RNS instance for path metrics including
    hop count, next hop, and interface used.

    Args:
        destination_hash: Hex string of destination hash (32 hex chars)

    Returns:
        CommandResult with path details (hops, next_hop, interface, etc.)
    """
    if not re.match(r'^[0-9a-fA-F]{32}$', destination_hash):
        return CommandResult.fail(
            "Invalid hash format",
            error="Hash must be 32 hex characters"
        )

    if not _HAS_RNS:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pipx install rns"
        )

    try:
        dest_bytes = bytes.fromhex(destination_hash)
        has_path = RNS.Transport.has_path(dest_bytes)

        if not has_path:
            return CommandResult.fail(
                "No path known",
                data={
                    'destination': destination_hash,
                    'has_path': False,
                    'note': 'Use rnprobe or test_path() to discover paths'
                }
            )

        info = {
            'destination': destination_hash,
            'has_path': True,
            'hops': None,
            'next_hop': None,
            'expires': None,
            'interface': None,
        }

        # Query path table for detailed info
        if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
            path_entry = RNS.Transport.path_table.get(dest_bytes)
            if path_entry and isinstance(path_entry, (list, tuple)):
                # Path table entry format: (timestamp, next_hop, interface, hops, expires, ...)
                # Format may vary by RNS version
                if len(path_entry) > 0:
                    info['timestamp'] = path_entry[0] if isinstance(path_entry[0], (int, float)) else None
                if len(path_entry) > 1:
                    next_hop = path_entry[1]
                    if isinstance(next_hop, bytes):
                        info['next_hop'] = next_hop.hex()
                if len(path_entry) > 2:
                    iface = path_entry[2]
                    if hasattr(iface, 'name'):
                        info['interface'] = iface.name
                    elif isinstance(iface, str):
                        info['interface'] = iface
                if len(path_entry) > 3:
                    if isinstance(path_entry[3], int):
                        info['hops'] = path_entry[3]
                if len(path_entry) > 4:
                    if isinstance(path_entry[4], (int, float)):
                        info['expires'] = path_entry[4]

        # Check if identity is known
        if hasattr(RNS.Identity, 'recall') and callable(RNS.Identity.recall):
            try:
                identity = RNS.Identity.recall(dest_bytes)
                info['identity_known'] = identity is not None
            except Exception:
                info['identity_known'] = False

        return CommandResult.ok(
            f"Path info for {destination_hash[:8]}...",
            data=info
        )

    except (SystemExit, KeyboardInterrupt, GeneratorExit):
        raise
    except BaseException as e:
        return CommandResult.fail(f"Path info failed: {e}")


# ============================================================================
# INTERFACE TEMPLATES
# ============================================================================

def get_interface_templates() -> CommandResult:
    """
    Get pre-built interface configuration templates.

    Returns:
        CommandResult with template list
    """
    templates = {
        'auto': {
            'name': 'AutoInterface',
            'description': 'Zero-config local network discovery (UDP multicast)',
            'type': 'AutoInterface',
            'settings': {}
        },
        'tcp_server': {
            'name': 'TCP Server',
            'description': 'Accept incoming RNS connections',
            'type': 'TCPServerInterface',
            'settings': {
                'listen_ip': '0.0.0.0',
                'listen_port': '4242'
            }
        },
        'tcp_client': {
            'name': 'TCP Client',
            'description': 'Connect to remote RNS server',
            'type': 'TCPClientInterface',
            'settings': {
                'target_host': '192.168.1.100',
                'target_port': '4242'
            }
        },
        'serial': {
            'name': 'Serial Link',
            'description': 'Direct serial/USB connection',
            'type': 'SerialInterface',
            'settings': {
                'port': '/dev/ttyUSB0',
                'speed': '115200'
            }
        },
        'meshtastic': {
            'name': 'Meshtastic Gateway',
            'description': 'RNS over Meshtastic LoRa network',
            'type': 'Meshtastic_Interface',
            'settings': {
                'tcp_port': '127.0.0.1:4403',
                'data_speed': '8',
                'hop_limit': '3'
            }
        },
        'meshtastic_dual': {
            'name': 'Meshtastic Dual-Radio Gateway',
            'description': 'Two radios: Short Turbo + Long Fast',
            'multi_interface': True,
            'interfaces': [
                {
                    'default_name': 'Meshtastic Short Turbo',
                    'type': 'Meshtastic_Interface',
                    'settings': {
                        'mode': 'gateway',
                        'tcp_port': '127.0.0.1:4403',
                        'data_speed': '8',
                        'hop_limit': '3',
                    }
                },
                {
                    'default_name': 'Meshtastic Long Fast',
                    'type': 'Meshtastic_Interface',
                    'settings': {
                        'mode': 'gateway',
                        'tcp_port': '127.0.0.1:4404',
                        'data_speed': '0',
                        'hop_limit': '3',
                    }
                }
            ]
        },
        'rnode': {
            'name': 'RNode LoRa',
            'description': 'Direct LoRa via RNode hardware',
            'type': 'RNodeInterface',
            'settings': {
                'port': '/dev/ttyUSB0',
                'frequency': '903625000',
                'txpower': '22',
                'bandwidth': '250000',
                'spreadingfactor': '7',
                'codingrate': '5'
            }
        }
    }

    return CommandResult.ok(
        f"Available templates: {len(templates)}",
        data={'templates': templates}
    )


def apply_template(template_name: str, interface_name: str, overrides: Dict[str, str] = None) -> CommandResult:
    """
    Apply an interface template to create a new interface.

    Args:
        template_name: Name of template (auto, tcp_server, etc.)
        interface_name: Name for the new interface
        overrides: Settings to override from template

    Returns:
        CommandResult indicating success
    """
    templates_result = get_interface_templates()
    templates = templates_result.data.get('templates', {})

    if template_name not in templates:
        return CommandResult.fail(
            f"Unknown template: {template_name}",
            data={'available': list(templates.keys())}
        )

    template = templates[template_name]

    if template.get('multi_interface'):
        return CommandResult.fail(
            f"Template '{template_name}' is a multi-interface template. "
            f"Use apply_multi_template() instead."
        )

    settings = template['settings'].copy()

    # Apply overrides
    if overrides:
        settings.update(overrides)

    return add_interface(interface_name, template['type'], settings)


def apply_multi_template(
    template_name: str,
    interface_configs: List[Dict[str, Any]],
) -> CommandResult:
    """
    Apply a multi-interface template to create several interfaces at once.

    Args:
        template_name: Name of template (e.g. meshtastic_dual)
        interface_configs: List of dicts with 'name' and optional 'overrides'
            for each interface defined in the template.

    Returns:
        CommandResult indicating success (all added) or failure
    """
    templates_result = get_interface_templates()
    templates = templates_result.data.get('templates', {})

    if template_name not in templates:
        return CommandResult.fail(
            f"Unknown template: {template_name}",
            data={'available': list(templates.keys())}
        )

    template = templates[template_name]
    if not template.get('multi_interface'):
        return CommandResult.fail(
            f"Template '{template_name}' is not a multi-interface template. "
            f"Use apply_template() instead."
        )

    iface_defs = template.get('interfaces', [])
    if len(interface_configs) != len(iface_defs):
        return CommandResult.fail(
            f"Expected {len(iface_defs)} interface configs, got {len(interface_configs)}"
        )

    added = []
    for iface_def, user_cfg in zip(iface_defs, interface_configs):
        name = user_cfg.get('name', iface_def['default_name'])
        settings = iface_def['settings'].copy()
        overrides = user_cfg.get('overrides')
        if overrides:
            settings.update(overrides)

        result = add_interface(name, iface_def['type'], settings)
        if not result.success:
            cleanup_hint = ""
            if added:
                cleanup_hint = (
                    f"\n\nAlready added: {', '.join(added)}"
                    f"\nTo clean up, remove them via Manage Interfaces > Remove."
                )
            return CommandResult.fail(
                f"Failed adding [[{name}]]: {result.message}{cleanup_hint}"
            )
        added.append(name)

    return CommandResult.ok(
        f"Added {len(added)} interfaces: {', '.join(added)}",
        data={'added': added}
    )


# ============================================================================
# RNS CLIENT INITIALIZATION
# ============================================================================

def _init_rns_client():
    """Initialize RNS as a client connecting to the running rnsd instance.

    Creates a client-only config with no interfaces to avoid
    "Address already in use" errors when rnsd already owns the ports.
    See: .claude/foundations/persistent_issues.md Issue #12
    """
    import tempfile

    client_config_dir = Path(tempfile.gettempdir()) / "meshforge_rns_client"
    client_config_dir.mkdir(exist_ok=True)
    client_config_file = client_config_dir / "config"

    client_config_file.write_text(
        "# MeshForge RNS Client Config (auto-generated)\n"
        "# Connects to existing rnsd without creating interfaces\n\n"
        "[reticulum]\n"
        "share_instance = Yes\n"
        "shared_instance_port = 37428\n"
        "instance_control_port = 37429\n"
    )

    return RNS.Reticulum(configdir=str(client_config_dir))


# ============================================================================
# RNS NODE DISCOVERY
# ============================================================================

def list_known_destinations() -> CommandResult:
    """
    List known RNS destinations from the running rnsd instance.

    This queries the rnsd daemon for all destinations it has heard about
    via announces or path requests.

    Returns:
        CommandResult with list of known destinations
    """
    # First check if rnsd is running (using improved detection)
    status = get_status()
    if not status.data.get('rnsd_running'):
        # Also check shared instance as fallback (domain socket, TCP, or UDP)
        try:
            from utils.service_check import check_rns_shared_instance
            if not check_rns_shared_instance():
                return CommandResult.fail(
                    "rnsd not running",
                    fix_hint="Start with: rnsd or sudo systemctl start rnsd"
                )
            # Shared instance is reachable, continue
        except ImportError:
            pass  # No service_check available, proceed anyway
        except Exception as e:
            logger.debug(f"RNS availability check error: {e}")

    if not _HAS_RNS:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pipx install rns"
        )

    try:
        # Connect as client to avoid "Address already in use" when rnsd owns ports
        reticulum = _init_rns_client()

        nodes = []

        # Method 1: Check Transport path table
        if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
            for dest_hash, path_data in RNS.Transport.path_table.items():
                try:
                    hash_hex = dest_hash.hex() if isinstance(dest_hash, bytes) else str(dest_hash)
                    # Path data format varies by RNS version
                    hops = 0
                    if isinstance(path_data, tuple) and len(path_data) > 1:
                        hops = path_data[1] if isinstance(path_data[1], int) else 0

                    nodes.append({
                        'hash': hash_hex,
                        'short_hash': hash_hex[:8],
                        'hops': hops,
                        'source': 'path_table'
                    })
                except Exception as e:
                    logger.debug(f"Error parsing path entry: {e}")

        # Method 2: Check known destinations
        if hasattr(RNS.Identity, 'known_destinations') and RNS.Identity.known_destinations:
            known_dests = RNS.Identity.known_destinations
            if isinstance(known_dests, dict):
                for dest_hash, identity in known_dests.items():
                    try:
                        hash_hex = dest_hash.hex() if isinstance(dest_hash, bytes) else str(dest_hash)
                        # Check if already added from path_table
                        if not any(n['hash'] == hash_hex for n in nodes):
                            nodes.append({
                                'hash': hash_hex,
                                'short_hash': hash_hex[:8],
                                'hops': -1,  # Unknown
                                'source': 'known_destinations'
                            })
                    except Exception as e:
                        logger.debug(f"Error parsing known destination: {e}")

        # Method 3: Check destination table
        if hasattr(RNS.Transport, 'destinations') and RNS.Transport.destinations:
            for dest in RNS.Transport.destinations:
                try:
                    if hasattr(dest, 'hash'):
                        hash_hex = dest.hash.hex() if isinstance(dest.hash, bytes) else str(dest.hash)
                        if not any(n['hash'] == hash_hex for n in nodes):
                            name = dest.name if hasattr(dest, 'name') else ''
                            nodes.append({
                                'hash': hash_hex,
                                'short_hash': hash_hex[:8],
                                'name': name,
                                'hops': -1,
                                'source': 'destinations'
                            })
                except Exception as e:
                    logger.debug(f"Error parsing destination: {e}")

        if nodes:
            return CommandResult.ok(
                f"Found {len(nodes)} RNS destinations",
                data={
                    'nodes': nodes,
                    'count': len(nodes)
                }
            )
        else:
            return CommandResult.ok(
                "No known RNS destinations",
                data={
                    'nodes': [],
                    'count': 0,
                    'note': "Nodes appear when they announce or when you request paths"
                }
            )

    except (SystemExit, KeyboardInterrupt, GeneratorExit):
        raise
    except BaseException as e:
        return CommandResult.fail(
            f"Failed to query RNS: {e}",
            error=str(e)
        )


def discover_nodes(timeout: int = 30) -> CommandResult:
    """
    Actively discover RNS nodes on the network.

    This sends out path requests and waits for announces to discover
    new nodes on the network.

    Args:
        timeout: How long to wait for discoveries (seconds)

    Returns:
        CommandResult with discovered nodes
    """
    if not _HAS_RNS:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pipx install rns"
        )

    try:
        import time

        # Connect as client to avoid "Address already in use" when rnsd owns ports
        reticulum = _init_rns_client()

        initial_count = 0
        if hasattr(RNS.Identity, 'known_destinations'):
            initial_count = len(RNS.Identity.known_destinations or {})

        discovered = []

        # Set up a simple announce handler to catch new nodes
        class DiscoveryHandler:
            def __init__(self):
                self.aspect_filter = None  # All aspects
                self.nodes = []

            def received_announce(self, dest_hash, announced_identity, app_data):
                try:
                    hash_hex = dest_hash.hex()
                    name = ""
                    if app_data:
                        try:
                            name = app_data.decode('utf-8', errors='ignore').strip()
                            name = ''.join(c for c in name if c.isprintable())
                        except Exception as e:
                            logger.debug(f"Failed to decode announce app_data: {e}")

                    self.nodes.append({
                        'hash': hash_hex,
                        'short_hash': hash_hex[:8],
                        'name': name,
                        'source': 'announce'
                    })
                except Exception as e:
                    logger.debug(f"Error processing announce: {e}")

        handler = DiscoveryHandler()
        RNS.Transport.register_announce_handler(handler)

        logger.info(f"Listening for RNS announces for {timeout} seconds...")

        # Wait for timeout
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(0.5)

            # Check for new discoveries
            if handler.nodes:
                for node in handler.nodes:
                    if not any(d['hash'] == node['hash'] for d in discovered):
                        discovered.append(node)
                        logger.info(f"Discovered: {node['short_hash']} ({node.get('name', 'unnamed')})")
                handler.nodes = []

        if discovered:
            return CommandResult.ok(
                f"Discovered {len(discovered)} nodes",
                data={
                    'nodes': discovered,
                    'count': len(discovered),
                    'duration': timeout
                }
            )
        else:
            return CommandResult.ok(
                "No new nodes discovered",
                data={
                    'nodes': [],
                    'count': 0,
                    'duration': timeout,
                    'note': "Try longer timeout or check if other RNS nodes are announcing"
                }
            )

    except Exception as e:
        return CommandResult.fail(f"Discovery failed: {e}")
