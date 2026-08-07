"""
Device Backup and Restore Commands

Provides functionality to backup and restore Meshtastic node configurations.
Useful for:
- Creating backups before firmware updates
- Cloning settings between devices
- Disaster recovery
"""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict

from utils.backup_paths import reserve_backup_path
from utils.paths import get_real_user_home
from utils.cli import find_meshtastic_cli
from utils.safe_import import safe_import

_yaml, _HAS_YAML = safe_import('yaml')

logger = logging.getLogger(__name__)


@dataclass
class BackupMetadata:
    """Metadata for a device backup."""
    backup_id: str
    created_at: str
    device_id: str
    device_name: str
    firmware_version: str
    hardware_model: str
    backup_type: str  # 'full', 'config', 'channels'
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'BackupMetadata':
        return cls(**data)


@dataclass
class DeviceBackup:
    """Complete device backup including config and channels."""
    metadata: BackupMetadata
    config: Dict  # Full device configuration
    channels: List[Dict]  # Channel configurations
    owner: Dict  # Owner/user info
    position: Optional[Dict] = None  # GPS position if available

    def to_dict(self) -> dict:
        return {
            'metadata': self.metadata.to_dict(),
            'config': self.config,
            'channels': self.channels,
            'owner': self.owner,
            'position': self.position,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DeviceBackup':
        return cls(
            metadata=BackupMetadata.from_dict(data['metadata']),
            config=data['config'],
            channels=data['channels'],
            owner=data['owner'],
            position=data.get('position'),
        )


def get_backup_dir() -> Path:
    """Get the backup directory path."""
    backup_dir = get_real_user_home() / ".config" / "meshforge" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def list_backups() -> List[Dict]:
    """
    List all available device backups.

    Returns:
        List of backup metadata dictionaries
    """
    backup_dir = get_backup_dir()
    backups = []

    for backup_file in sorted(backup_dir.glob("*.json"), reverse=True):
        try:
            with open(backup_file) as f:
                data = json.load(f)
                if 'metadata' in data:
                    meta = data['metadata']
                    meta['file_path'] = str(backup_file)
                    backups.append(meta)
        except Exception as e:
            logger.debug(f"Could not read backup {backup_file}: {e}")

    return backups


def create_backup(
    connection: str = "localhost",
    port: int = 4403,
    backup_type: str = "full",
    notes: str = ""
) -> Dict:
    """
    Create a backup of the connected Meshtastic device.

    Args:
        connection: Connection string (hostname or serial port)
        port: TCP port if using hostname
        backup_type: Type of backup ('full', 'config', 'channels')
        notes: Optional notes about this backup

    Returns:
        Dict with 'success', 'backup_id', 'file_path', and 'error' keys
    """
    result = {
        'success': False,
        'backup_id': None,
        'file_path': None,
        'error': None,
    }

    try:
        # Find meshtastic CLI
        cli_path = find_meshtastic_cli()

        if not cli_path:
            result['error'] = "meshtastic CLI not found - install with: pipx install meshtastic[cli]"
            return result

        # Determine connection args
        if connection.startswith('/dev/'):
            conn_args = ['--port', connection]
        else:
            conn_args = ['--host', connection]
            if port != 4403:
                conn_args.extend(['--port', str(port)])

        # Get device info first
        info_result = subprocess.run(
            [cli_path] + conn_args + ['--info'],
            capture_output=True, text=True, timeout=30
        )

        if info_result.returncode != 0:
            result['error'] = f"Could not get device info: {info_result.stderr}"
            return result

        # Parse device info
        device_id = "unknown"
        device_name = "Unknown Device"
        firmware_version = "unknown"
        hardware_model = "unknown"

        for line in info_result.stdout.split('\n'):
            if 'Owner:' in line:
                device_name = line.split('Owner:')[1].strip()
            elif 'Hardware:' in line or 'hwModel:' in line:
                hardware_model = line.split(':')[-1].strip()
            elif 'Firmware:' in line or 'firmwareVersion:' in line:
                firmware_version = line.split(':')[-1].strip()
            elif 'My info:' in line or 'myNodeNum:' in line:
                # Extract node ID
                parts = line.split()
                for part in parts:
                    if part.startswith('!') or part.isdigit():
                        device_id = part
                        break

        # Export full config
        export_result = subprocess.run(
            [cli_path] + conn_args + ['--export-config'],
            capture_output=True, text=True, timeout=30
        )

        if export_result.returncode != 0:
            result['error'] = f"Could not export config: {export_result.stderr}"
            return result

        # Parse the YAML config output
        config_data = {}
        channels_data = []
        owner_data = {}

        if _HAS_YAML:
            try:
                config_data = _yaml.safe_load(export_result.stdout) or {}
            except Exception as e:
                config_data = {'raw': export_result.stdout, 'parse_error': str(e)}
        else:
            # Simple parsing if yaml not available
            config_data = {'raw': export_result.stdout}

        # Extract channels if available
        if 'channel_url' in config_data:
            channels_data = [{'url': config_data['channel_url']}]
        elif 'channels' in config_data:
            channels_data = config_data.get('channels', [])

        # Extract owner info
        owner_data = {
            'longName': device_name,
            'shortName': device_name[:4] if device_name else 'UNKN',
        }

        # Create backup object
        timestamp = datetime.now()
        backup_id = f"{device_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        metadata = BackupMetadata(
            backup_id=backup_id,
            created_at=timestamp.isoformat(),
            device_id=device_id,
            device_name=device_name,
            firmware_version=firmware_version,
            hardware_model=hardware_model,
            backup_type=backup_type,
            notes=notes,
        )

        backup = DeviceBackup(
            metadata=metadata,
            config=config_data,
            channels=channels_data,
            owner=owner_data,
        )

        # Save backup. backup_id is <device>_<second-resolution timestamp>, so
        # two backups of one device inside the same second collide; stepping
        # aside is right here because the second backup is still wanted, it
        # just must not land on the first.
        backup_file = reserve_backup_path(
            get_backup_dir(), f"{backup_id}.json", on_exists="unique")

        # Identity follows the FILE: if the reservation stepped aside, the
        # on-disk metadata must carry the disambiguated stem too — otherwise
        # list/restore/delete key two files under one id and resolve the
        # WRONG one (a delete then destroys the unselected backup; 2026-08-06
        # ultra finding on this arc).
        metadata.backup_id = backup_file.stem

        with open(backup_file, 'w') as f:
            json.dump(backup.to_dict(), f, indent=2)

        result['success'] = True
        # Report the id that matches the file actually written — a disambiguated
        # name must not be reported under the name it stepped aside from, or
        # restore/delete would target the wrong backup.
        result['backup_id'] = backup_file.stem
        result['file_path'] = str(backup_file)
        logger.info(f"Backup created: {backup_file}")

    except subprocess.TimeoutExpired:
        result['error'] = "Command timed out - device may be unresponsive"
    except FileNotFoundError:
        result['error'] = "meshtastic CLI not found - install with: pip install meshtastic"
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Backup error: {e}")

    return result


def restore_backup(
    backup_id: str,
    connection: str = "localhost",
    port: int = 4403,
    restore_channels: bool = True,
    restore_config: bool = True,
    dry_run: bool = False
) -> Dict:
    """
    Restore a device from backup.

    Args:
        backup_id: ID of the backup to restore
        connection: Connection string
        port: TCP port if using hostname
        restore_channels: Whether to restore channel settings
        restore_config: Whether to restore device config
        dry_run: If True, only show what would be restored

    Returns:
        Dict with 'success', 'restored_items', and 'error' keys
    """
    result = {
        'success': False,
        'restored_items': [],
        'error': None,
        'dry_run': dry_run,
    }

    try:
        # Find backup file
        backup_dir = get_backup_dir()
        backup_file = backup_dir / f"{backup_id}.json"

        if not backup_file.exists():
            # Try to find by partial match
            matches = list(backup_dir.glob(f"*{backup_id}*.json"))
            if matches:
                backup_file = matches[0]
            else:
                result['error'] = f"Backup not found: {backup_id}"
                return result

        # Load backup
        with open(backup_file) as f:
            data = json.load(f)

        backup = DeviceBackup.from_dict(data)

        if dry_run:
            result['success'] = True
            result['restored_items'].append(f"Would restore from: {backup.metadata.device_name}")
            result['restored_items'].append(f"Backup date: {backup.metadata.created_at}")
            if restore_config:
                result['restored_items'].append("Would restore: Device configuration")
            if restore_channels:
                result['restored_items'].append(f"Would restore: {len(backup.channels)} channel(s)")
            return result

        # Find meshtastic CLI
        cli_path = find_meshtastic_cli()

        if not cli_path:
            result['error'] = "meshtastic CLI not found - install with: pipx install meshtastic[cli]"
            return result

        # Determine connection args
        if connection.startswith('/dev/'):
            conn_args = ['--port', connection]
        else:
            conn_args = ['--host', connection]
            if port != 4403:
                conn_args.extend(['--port', str(port)])

        # Restore channels via URL if available
        if restore_channels and backup.channels:
            for channel in backup.channels:
                if 'url' in channel:
                    url = channel['url']
                    cmd_result = subprocess.run(
                        [cli_path] + conn_args + ['--seturl', url],
                        capture_output=True, text=True, timeout=30
                    )
                    if cmd_result.returncode == 0:
                        result['restored_items'].append(f"Restored channel URL")
                    else:
                        logger.warning(f"Channel restore warning: {cmd_result.stderr}")

        # Restore owner info
        if restore_config and backup.owner:
            long_name = backup.owner.get('longName', '')
            short_name = backup.owner.get('shortName', '')

            if long_name:
                cmd_result = subprocess.run(
                    [cli_path] + conn_args + ['--set-owner', long_name],
                    capture_output=True, text=True, timeout=30
                )
                if cmd_result.returncode == 0:
                    result['restored_items'].append(f"Restored owner: {long_name}")

            if short_name:
                cmd_result = subprocess.run(
                    [cli_path] + conn_args + ['--set-owner-short', short_name],
                    capture_output=True, text=True, timeout=30
                )
                if cmd_result.returncode == 0:
                    result['restored_items'].append(f"Restored short name: {short_name}")

        result['success'] = len(result['restored_items']) > 0
        if not result['success']:
            result['error'] = "No items could be restored"

    except FileNotFoundError as e:
        if 'meshtastic' in str(e):
            result['error'] = "meshtastic CLI not found"
        else:
            result['error'] = f"Backup file not found: {e}"
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Restore error: {e}")

    return result


def delete_backup(backup_id: str) -> Dict:
    """
    Delete a backup file.

    Args:
        backup_id: ID of the backup to delete

    Returns:
        Dict with 'success' and 'error' keys
    """
    result = {'success': False, 'error': None}

    try:
        backup_dir = get_backup_dir()
        backup_file = backup_dir / f"{backup_id}.json"

        if not backup_file.exists():
            matches = list(backup_dir.glob(f"*{backup_id}*.json"))
            if matches:
                backup_file = matches[0]
            else:
                result['error'] = f"Backup not found: {backup_id}"
                return result

        backup_file.unlink()
        result['success'] = True
        logger.info(f"Deleted backup: {backup_file}")

    except Exception as e:
        result['error'] = str(e)

    return result


def get_backup_details(backup_id: str) -> Optional[Dict]:
    """
    Get full details of a backup.

    Args:
        backup_id: ID of the backup

    Returns:
        Full backup data or None if not found
    """
    backup_dir = get_backup_dir()
    backup_file = backup_dir / f"{backup_id}.json"

    if not backup_file.exists():
        matches = list(backup_dir.glob(f"*{backup_id}*.json"))
        if matches:
            backup_file = matches[0]
        else:
            return None

    try:
        with open(backup_file) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading backup: {e}")
        return None
