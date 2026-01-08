"""
RNS Configuration Utilities - Standalone Module

Drop this file into any RNS-related project to get:
- Safe config editing with validation
- Automatic backups before changes
- Atomic saves (prevents corruption)
- Consistent path handling (sudo-aware)

From MeshForge project: https://github.com/Nursedude/meshforge
Compatible with: rns-over-meshtastic-gateway, nomadnet, lxmf projects

Usage:
    from rns_config_utils import (
        get_rns_config_path,
        validate_rns_config,
        safe_save_config,
        add_interface_to_config,
        backup_config,
        restore_from_backup,
        list_backups
    )

    # Get correct config path (works with sudo)
    config_path = get_rns_config_path()

    # Validate before saving
    is_valid, errors = validate_rns_config(new_content)

    # Safe save with validation and backup
    result = safe_save_config(config_path, new_content)
    if result['success']:
        print(f"Saved! Backup at: {result['backup_path']}")
    else:
        print(f"Failed: {result['error']}")

    # Add/update interface section safely
    result = add_interface_to_config(config_path, interface_section, "MyInterface")

    # Restore from backup if needed
    backups = list_backups(config_path)
    if backups:
        restore_from_backup(config_path, backups[0])

Version: 1.0.0
License: MIT
Author: WH6GXZ (Nursedude) - MeshForge Project
"""

import os
import re
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Tuple, List, Dict, Any, Optional

__version__ = "1.0.0"
__author__ = "WH6GXZ"

logger = logging.getLogger(__name__)


# =============================================================================
# Path Utilities - Handles sudo/root scenarios correctly
# =============================================================================

def get_real_user_home() -> Path:
    """
    Get real user's home directory, even when running as sudo.

    When running `sudo python script.py`, Path.home() returns /root.
    This function returns the actual user's home directory.

    Returns:
        Path to real user's home directory
    """
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        return Path(f'/home/{sudo_user}')
    return Path.home()


def get_rns_config_path() -> Path:
    """
    Get the correct RNS config file path.

    When running as sudo, returns the real user's config path,
    not root's, to maintain consistency.

    Returns:
        Path to ~/.reticulum/config
    """
    return get_real_user_home() / ".reticulum" / "config"


def get_rns_config_dir() -> Path:
    """
    Get the RNS configuration directory.

    Returns:
        Path to ~/.reticulum/
    """
    return get_real_user_home() / ".reticulum"


# =============================================================================
# Config Validation - Prevents invalid configs from breaking RNS/NomadNet
# =============================================================================

def validate_rns_config(config: str) -> Tuple[bool, List[str]]:
    """
    Validate RNS config syntax and required sections.

    Checks for:
    - Empty config
    - Missing [reticulum] section
    - Malformed section headers
    - Bracket mismatches

    Args:
        config: Config file content as string

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check for basic syntax issues
    if not config.strip():
        errors.append("Config file is empty")
        return False, errors

    # Check for unclosed brackets
    open_brackets = config.count('[') - config.count('[[') * 2
    close_brackets = config.count(']') - config.count(']]') * 2
    if open_brackets != close_brackets:
        errors.append("Mismatched brackets in config")

    # Check for required [reticulum] section
    if '[reticulum]' not in config.lower():
        errors.append("Missing required [reticulum] section")

    # Check for malformed section headers
    lines = config.split('\n')
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('[') and not stripped.startswith('#'):
            if stripped.startswith('[['):
                if not re.match(r'^\[\[[^\]]+\]\]\s*$', stripped):
                    errors.append(f"Line {i}: Malformed subsection header: {stripped}")
            elif stripped.startswith('['):
                if not re.match(r'^\[[^\]]+\]\s*$', stripped):
                    errors.append(f"Line {i}: Malformed section header: {stripped}")

    # Warning for missing interfaces (not an error)
    if '[interfaces]' not in config.lower():
        logger.warning("Config missing [interfaces] section - no interfaces will be loaded")

    return len(errors) == 0, errors


def validate_interface_section(section: str) -> Tuple[bool, List[str]]:
    """
    Validate an interface section.

    Checks for:
    - Required 'type' field
    - Valid interface type

    Args:
        section: Interface section content

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check for type =
    if 'type' not in section.lower():
        errors.append("Interface section missing required 'type' field")
        return False, errors

    # Extract type and validate
    type_match = re.search(r'type\s*=\s*(\w+)', section, re.IGNORECASE)
    if type_match:
        iface_type = type_match.group(1)
        valid_types = [
            'AutoInterface', 'TCPServerInterface', 'TCPClientInterface',
            'UDPInterface', 'RNodeInterface', 'SerialInterface',
            'KISSInterface', 'AX25KISSInterface', 'I2PInterface'
        ]
        if iface_type not in valid_types:
            errors.append(f"Unknown interface type: {iface_type}")

    return len(errors) == 0, errors


# =============================================================================
# Backup Utilities - Auto-backup before any modification
# =============================================================================

def backup_config(config_path: Path) -> Path:
    """
    Create a timestamped backup of the config file.

    Backups are stored in ~/.reticulum/backups/

    Args:
        config_path: Path to the config file

    Returns:
        Path to the backup file

    Raises:
        FileNotFoundError: If config file doesn't exist
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    backup_dir = config_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"config_{timestamp}.bak"

    # Ensure unique name
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"config_{timestamp}_{counter}.bak"
        counter += 1

    shutil.copy2(config_path, backup_path)
    logger.info(f"Config backed up to: {backup_path}")

    return backup_path


def list_backups(config_path: Path) -> List[Path]:
    """
    List available backup files, newest first.

    Args:
        config_path: Path to the config file

    Returns:
        List of backup file paths, sorted by modification time (newest first)
    """
    backup_dir = config_path.parent / "backups"
    if not backup_dir.exists():
        return []

    backups = list(backup_dir.glob("config_*.bak"))
    backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return backups


def restore_from_backup(config_path: Path, backup_path: Path) -> Dict[str, Any]:
    """
    Restore config from a backup file.

    Args:
        config_path: Path to the config file
        backup_path: Path to the backup file

    Returns:
        Dict with 'success' and 'error' keys
    """
    result = {
        'success': False,
        'error': None
    }

    if not backup_path.exists():
        result['error'] = f"Backup file not found: {backup_path}"
        return result

    try:
        shutil.copy2(backup_path, config_path)
        result['success'] = True
        logger.info(f"Config restored from: {backup_path}")
    except Exception as e:
        result['error'] = f"Failed to restore config: {e}"
        logger.error(result['error'])

    return result


# =============================================================================
# Safe Save - Validates, backs up, and atomically saves config
# =============================================================================

def safe_save_config(config_path: Path, new_content: str) -> Dict[str, Any]:
    """
    Safely save config with validation and backup.

    Process:
    1. Validate new content
    2. Create backup of existing config
    3. Write to temp file
    4. Atomically rename temp to config
    5. Set correct permissions

    If anything fails, original config is preserved.

    Args:
        config_path: Path to the config file
        new_content: New config content

    Returns:
        Dict with 'success', 'error', 'backup_path' keys
    """
    result = {
        'success': False,
        'error': None,
        'backup_path': None
    }

    # Validate new content first
    is_valid, errors = validate_rns_config(new_content)
    if not is_valid:
        result['error'] = f"Invalid config: {'; '.join(errors)}"
        logger.error(result['error'])
        return result

    # Create backup of existing config
    try:
        if config_path.exists():
            result['backup_path'] = str(backup_config(config_path))
    except Exception as e:
        result['error'] = f"Failed to create backup: {e}"
        logger.error(result['error'])
        return result

    # Write to temp file first (atomic operation)
    temp_path = config_path.with_suffix('.tmp')
    try:
        temp_path.write_text(new_content)

        # Rename temp to actual (atomic on most filesystems)
        temp_path.rename(config_path)

        # Ensure correct permissions
        config_path.chmod(0o644)

        result['success'] = True
        logger.info(f"Config saved successfully: {config_path}")

    except Exception as e:
        result['error'] = f"Failed to save config: {e}"
        logger.error(result['error'])

        # Clean up temp file
        if temp_path.exists():
            temp_path.unlink()

        # Try to restore from backup
        if result['backup_path']:
            try:
                shutil.copy2(result['backup_path'], config_path)
                logger.info("Restored config from backup after save failure")
            except Exception as restore_e:
                logger.error(f"Failed to restore backup: {restore_e}")

    return result


def add_interface_to_config(config_path: Path, interface_section: str,
                            interface_name: str = None) -> Dict[str, Any]:
    """
    Safely add or update an interface section in the config.

    If interface_name is provided and exists in config, it will be replaced.
    Otherwise, the new section is appended.

    Args:
        config_path: Path to the config file
        interface_section: The interface section content
        interface_name: Name to look for when replacing (optional)

    Returns:
        Dict with 'success', 'error', 'backup_path' keys
    """
    result = {
        'success': False,
        'error': None,
        'backup_path': None
    }

    # Validate interface section
    is_valid, errors = validate_interface_section(interface_section)
    if not is_valid:
        result['error'] = f"Invalid interface section: {'; '.join(errors)}"
        return result

    # Read current config
    try:
        if config_path.exists():
            content = config_path.read_text()
        else:
            # Create minimal config if it doesn't exist
            content = """[reticulum]
  share_instance = Yes

[interfaces]
"""
    except Exception as e:
        result['error'] = f"Failed to read config: {e}"
        return result

    # If interface_name provided, look for existing section to replace
    if interface_name:
        pattern = rf'\[\[{re.escape(interface_name)}[^\]]*\]\][^\[]*'
        if re.search(pattern, content, re.IGNORECASE):
            content = re.sub(pattern, interface_section.strip() + '\n\n', content, flags=re.IGNORECASE)
        else:
            content = content.rstrip() + '\n\n' + interface_section
    else:
        content = content.rstrip() + '\n\n' + interface_section

    return safe_save_config(config_path, content)


# =============================================================================
# Convenience Functions
# =============================================================================

def ensure_config_exists() -> Path:
    """
    Ensure RNS config file exists with minimal valid content.

    Returns:
        Path to the config file
    """
    config_path = get_rns_config_path()
    config_dir = config_path.parent

    # Create directory if needed
    config_dir.mkdir(parents=True, exist_ok=True)

    # Create minimal config if it doesn't exist
    if not config_path.exists():
        minimal_config = """[reticulum]
  enable_transport = False
  share_instance = Yes

[logging]
  loglevel = 4

[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = Yes
"""
        config_path.write_text(minimal_config)
        config_path.chmod(0o644)
        logger.info(f"Created default RNS config at: {config_path}")

    return config_path


def get_config_backup_count() -> int:
    """
    Get the number of available backups.

    Returns:
        Number of backup files
    """
    return len(list_backups(get_rns_config_path()))


# =============================================================================
# Main - CLI utility when run directly
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="RNS Configuration Utilities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python rns_config_utils.py --validate          # Validate current config
  python rns_config_utils.py --backup            # Create backup
  python rns_config_utils.py --list-backups      # List available backups
  python rns_config_utils.py --restore 0         # Restore most recent backup
  python rns_config_utils.py --path              # Show config path
        """
    )

    parser.add_argument('--validate', action='store_true',
                        help='Validate current config')
    parser.add_argument('--backup', action='store_true',
                        help='Create backup of current config')
    parser.add_argument('--list-backups', action='store_true',
                        help='List available backups')
    parser.add_argument('--restore', type=int, metavar='N',
                        help='Restore backup N (0=most recent)')
    parser.add_argument('--path', action='store_true',
                        help='Show config file path')
    parser.add_argument('--version', action='version',
                        version=f'%(prog)s {__version__}')

    args = parser.parse_args()

    config_path = get_rns_config_path()

    if args.path:
        print(f"Config path: {config_path}")
        print(f"Exists: {config_path.exists()}")

    elif args.validate:
        if not config_path.exists():
            print(f"Config not found: {config_path}")
            exit(1)

        content = config_path.read_text()
        is_valid, errors = validate_rns_config(content)

        if is_valid:
            print("✓ Config is valid")
        else:
            print("✗ Config has errors:")
            for error in errors:
                print(f"  - {error}")
            exit(1)

    elif args.backup:
        if not config_path.exists():
            print(f"Config not found: {config_path}")
            exit(1)

        backup_path = backup_config(config_path)
        print(f"✓ Backup created: {backup_path}")

    elif args.list_backups:
        backups = list_backups(config_path)
        if backups:
            print(f"Available backups ({len(backups)}):")
            for i, backup in enumerate(backups):
                mtime = datetime.fromtimestamp(backup.stat().st_mtime)
                print(f"  [{i}] {backup.name} - {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print("No backups found")

    elif args.restore is not None:
        backups = list_backups(config_path)
        if not backups:
            print("No backups available")
            exit(1)

        if args.restore >= len(backups):
            print(f"Invalid backup index. Available: 0-{len(backups)-1}")
            exit(1)

        result = restore_from_backup(config_path, backups[args.restore])
        if result['success']:
            print(f"✓ Config restored from: {backups[args.restore].name}")
        else:
            print(f"✗ Restore failed: {result['error']}")
            exit(1)
    else:
        parser.print_help()
