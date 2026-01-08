"""
RNS Configuration Utilities

Provides safe, validated config editing with backup/restore functionality.
Prevents NomadNet/rnsd launch failures from invalid config files.
"""

import os
import re
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Tuple, List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def get_real_user_home() -> Path:
    """Get real user's home directory, even when running as sudo"""
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        return Path(f'/home/{sudo_user}')
    return Path.home()


def get_rns_config_path() -> Path:
    """
    Get the correct RNS config path.

    When running as sudo, returns the real user's config path,
    not root's, to maintain consistency.
    """
    return get_real_user_home() / ".reticulum" / "config"


def validate_rns_config(config: str) -> Tuple[bool, List[str]]:
    """
    Validate RNS config syntax and required sections.

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
    # Valid: [section] or [[subsection]]
    lines = config.split('\n')
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('[') and not stripped.startswith('#'):
            # Should be [name] or [[name]]
            if stripped.startswith('[['):
                if not re.match(r'^\[\[[^\]]+\]\]\s*$', stripped):
                    errors.append(f"Line {i}: Malformed subsection header: {stripped}")
            elif stripped.startswith('['):
                if not re.match(r'^\[[^\]]+\]\s*$', stripped):
                    errors.append(f"Line {i}: Malformed section header: {stripped}")

    # Check for [interfaces] section (recommended)
    if '[interfaces]' not in config.lower():
        # This is a warning, not an error
        logger.warning("Config missing [interfaces] section - no interfaces will be loaded")

    return len(errors) == 0, errors


def validate_interface_section(section: str) -> Tuple[bool, List[str]]:
    """
    Validate an interface section.

    Args:
        section: Interface section content

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check for type =
    if 'type' not in section.lower():
        errors.append("Interface section missing required 'type' field")

    # Extract type and validate it's a known type
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


def backup_config(config_path: Path) -> Path:
    """
    Create a backup of the config file.

    Args:
        config_path: Path to the config file

    Returns:
        Path to the backup file
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


def safe_save_config(config_path: Path, new_content: str) -> Dict[str, Any]:
    """
    Safely save config with validation and backup.

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

        # Clean up temp file if it exists
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


def add_interface_to_config(config_path: Path, interface_section: str,
                            interface_name: str = None) -> Dict[str, Any]:
    """
    Safely add or update an interface section in the config.

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
        # Pattern to match the interface section
        pattern = rf'\[\[{re.escape(interface_name)}[^\]]*\]\][^\[]*'
        if re.search(pattern, content, re.IGNORECASE):
            content = re.sub(pattern, interface_section.strip() + '\n\n', content, flags=re.IGNORECASE)
        else:
            # Add at end of [interfaces] section
            content = content.rstrip() + '\n\n' + interface_section
    else:
        # Just append
        content = content.rstrip() + '\n\n' + interface_section

    # Use safe_save_config
    return safe_save_config(config_path, content)


def list_backups(config_path: Path) -> List[Path]:
    """
    List available backup files.

    Args:
        config_path: Path to the config file

    Returns:
        List of backup file paths, newest first
    """
    backup_dir = config_path.parent / "backups"
    if not backup_dir.exists():
        return []

    backups = list(backup_dir.glob("config_*.bak"))
    backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return backups
