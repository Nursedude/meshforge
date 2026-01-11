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

logger = logging.getLogger(__name__)


# ============================================================================
# PATH UTILITIES
# ============================================================================

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


def get_config_path() -> Path:
    """Get path to RNS config file."""
    return get_real_user_home() / ".reticulum" / "config"


def get_config_dir() -> Path:
    """Get path to RNS config directory."""
    return get_real_user_home() / ".reticulum"


def get_identity_path() -> Path:
    """Get path to MeshForge gateway identity."""
    return get_real_user_home() / ".config" / "meshforge" / "gateway_identity"


def get_lxmf_storage_path() -> Path:
    """Get path to LXMF message storage."""
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
    valid_types = [
        'AutoInterface', 'TCPServerInterface', 'TCPClientInterface',
        'SerialInterface', 'RNodeInterface', 'I2PInterface',
        'Meshtastic_Interface', 'UDPInterface', 'PipeInterface'
    ]

    for match in re.finditer(r'type\s*=\s*(\w+)', content):
        iface_type = match.group(1)
        if iface_type not in valid_types:
            errors.append(f"Unknown interface type: {iface_type}")

    return len(errors) == 0, errors


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

    Returns:
        CommandResult with default config
    """
    default_config = """# Reticulum Configuration
# Generated by MeshForge

[reticulum]
enable_transport = yes
share_instance = yes
shared_instance_port = 37428
instance_control_port = 37429

[logging]
loglevel = 4

[interfaces]
  # Default auto-discovery interface
  [[Default Interface]]
    type = AutoInterface
    enabled = yes

  # Add additional interfaces below
  # See: https://reticulum.network/manual/interfaces.html
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

    Returns:
        CommandResult with daemon status
    """
    status = {
        'rnsd_running': False,
        'rnsd_pid': None,
        'config_exists': get_config_path().exists(),
        'identity_exists': get_identity_path().exists(),
    }

    # Check for running rnsd
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
    except Exception:
        # pgrep may not be available - fall back to systemd check
        pass

    # Check systemd service
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'rnsd'],
            capture_output=True,
            text=True,
            timeout=5
        )
        status['systemd_status'] = result.stdout.strip()
    except Exception:
        # systemctl may not be available on non-systemd systems
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

    try:
        # Try systemctl first
        result = subprocess.run(
            ['sudo', 'systemctl', 'start', 'rnsd'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return CommandResult.ok("rnsd started via systemd")

        # Fallback to direct start
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
            fix_hint="Install with: pip install rns"
        )
    except Exception as e:
        return CommandResult.fail(f"Start failed: {e}")


def stop_rnsd() -> CommandResult:
    """
    Stop the RNS daemon.

    Returns:
        CommandResult indicating success
    """
    try:
        # Try systemctl first
        result = subprocess.run(
            ['sudo', 'systemctl', 'stop', 'rnsd'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return CommandResult.ok("rnsd stopped via systemd")

        # Fallback to pkill
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
        'issues': []
    }

    # Check rnsd
    status = get_status()
    connectivity['rnsd_running'] = status.data.get('rnsd_running', False)
    if not connectivity['rnsd_running']:
        connectivity['issues'].append("rnsd daemon not running")

    # Check RNS import
    try:
        import RNS
        connectivity['can_import_rns'] = True
        connectivity['rns_version'] = RNS.__version__ if hasattr(RNS, '__version__') else 'unknown'
    except ImportError:
        connectivity['issues'].append("RNS Python module not installed")

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
    else:
        connectivity['issues'].append(f"Config error: {config_result.message}")

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
    try:
        import RNS

        # Validate hash format
        if not re.match(r'^[0-9a-fA-F]{32}$', destination_hash):
            return CommandResult.fail(
                "Invalid hash format",
                error="Hash must be 32 hex characters"
            )

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

    except ImportError:
        return CommandResult.not_available(
            "RNS not installed",
            fix_hint="pip install rns"
        )
    except Exception as e:
        return CommandResult.fail(f"Path test failed: {e}")


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
    settings = template['settings'].copy()

    # Apply overrides
    if overrides:
        settings.update(overrides)

    return add_interface(interface_name, template['type'], settings)
