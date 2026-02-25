"""
MeshForge Device Backup and Restore Module

Provides backup and restore functionality for Meshtastic device configurations.
Backups are stored in JSON format for portability and human readability.
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

from utils.paths import get_real_user_home
from utils.cli import find_meshtastic_cli


class DeviceBackupManager:
    """Manages device configuration backups for Meshtastic nodes."""

    SCHEMA_VERSION = "1.0"
    BACKUP_DIR_NAME = "backups"

    def __init__(self, backup_dir: Optional[Path] = None):
        """Initialize backup manager.

        Args:
            backup_dir: Custom backup directory. Defaults to ~/.config/meshforge/backups/
        """
        if backup_dir:
            self.backup_dir = Path(backup_dir)
        else:
            self.backup_dir = get_real_user_home() / ".config" / "meshforge" / self.BACKUP_DIR_NAME

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[Backup] Initialized backup manager at {self.backup_dir}")

    def export_device_config(self, output_file: Optional[Path] = None,
                              host: str = "localhost", port: int = 4403) -> Dict[str, Any]:
        """Export device configuration to JSON.

        Args:
            output_file: Optional path to save backup. Auto-generated if not provided.
            host: Meshtasticd host address
            port: Meshtasticd TCP port

        Returns:
            Dictionary containing the backup data
        """
        logger.info(f"[Backup] Exporting device config from {host}:{port}")

        config = {
            "schema_version": self.SCHEMA_VERSION,
            "backup_date": datetime.now().isoformat(),
            "source": f"{host}:{port}",
            "node_info": {},
            "lora_config": {},
            "device_config": {},
            "position_config": {},
            "channels": [],
            "power_config": {},
            "display_config": {},
            "bluetooth_config": {},
            "network_config": {},
        }

        try:
            # Get device info using meshtastic CLI
            info = self._run_meshtastic_cmd(["--info"], host, port)
            config["node_info"] = self._parse_info(info)

            # Get all settings
            settings = self._run_meshtastic_cmd(["--get", "all"], host, port)
            parsed_settings = self._parse_settings(settings)

            # Map settings to config sections
            config["lora_config"] = parsed_settings.get("lora", {})
            config["device_config"] = parsed_settings.get("device", {})
            config["position_config"] = parsed_settings.get("position", {})
            config["power_config"] = parsed_settings.get("power", {})
            config["display_config"] = parsed_settings.get("display", {})
            config["bluetooth_config"] = parsed_settings.get("bluetooth", {})
            config["network_config"] = parsed_settings.get("network", {})

            # Get channel info
            config["channels"] = self._get_channels(host, port)

        except Exception as e:
            logger.error(f"[Backup] Failed to export config: {e}")
            config["error"] = str(e)

        # Save to file if path provided
        if output_file:
            output_path = Path(output_file)
        else:
            # Auto-generate filename
            node_id = config.get("node_info", {}).get("node_id", "unknown")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.backup_dir / f"backup_{node_id}_{timestamp}.json"

        self._save_backup(config, output_path)
        config["backup_file"] = str(output_path)

        return config

    def restore_device_config(self, backup_file: Path, host: str = "localhost",
                               port: int = 4403, dry_run: bool = False) -> Dict[str, Any]:
        """Restore device configuration from a backup file.

        Args:
            backup_file: Path to backup JSON file
            host: Meshtasticd host address
            port: Meshtasticd TCP port
            dry_run: If True, only validate without applying changes

        Returns:
            Dictionary with restore results
        """
        logger.info(f"[Backup] Restoring config from {backup_file} (dry_run={dry_run})")

        result = {
            "success": False,
            "dry_run": dry_run,
            "applied": [],
            "skipped": [],
            "errors": [],
        }

        try:
            with open(backup_file) as f:
                config = json.load(f)

            # Validate schema version
            schema = config.get("schema_version", "0")
            if schema != self.SCHEMA_VERSION:
                logger.warning(f"[Backup] Schema version mismatch: {schema} vs {self.SCHEMA_VERSION}")

            # Restore LoRa config
            lora = config.get("lora_config", {})
            for key, value in lora.items():
                self._apply_setting(f"lora.{key}", value, host, port, dry_run, result)

            # Restore device config
            device = config.get("device_config", {})
            for key, value in device.items():
                self._apply_setting(f"device.{key}", value, host, port, dry_run, result)

            # Restore position config
            position = config.get("position_config", {})
            for key, value in position.items():
                if key in ["latitude", "longitude", "altitude"] and value:
                    self._apply_setting(f"position.{key}", value, host, port, dry_run, result)

            # Restore channels
            channels = config.get("channels", [])
            for channel in channels:
                idx = channel.get("index", 0)
                for key, value in channel.items():
                    if key != "index" and value is not None:
                        self._apply_channel_setting(idx, key, value, host, port, dry_run, result)

            # Restore power config
            power = config.get("power_config", {})
            for key, value in power.items():
                self._apply_setting(f"power.{key}", value, host, port, dry_run, result)

            # Restore display config
            display = config.get("display_config", {})
            for key, value in display.items():
                self._apply_setting(f"display.{key}", value, host, port, dry_run, result)

            result["success"] = len(result["errors"]) == 0
            logger.info(f"[Backup] Restore completed: {len(result['applied'])} applied, "
                       f"{len(result['skipped'])} skipped, {len(result['errors'])} errors")

        except FileNotFoundError:
            result["errors"].append(f"Backup file not found: {backup_file}")
            logger.error(f"[Backup] File not found: {backup_file}")
        except json.JSONDecodeError as e:
            result["errors"].append(f"Invalid JSON: {e}")
            logger.error(f"[Backup] Invalid JSON in {backup_file}: {e}")
        except Exception as e:
            result["errors"].append(str(e))
            logger.error(f"[Backup] Restore failed: {e}")

        return result

    def list_backups(self) -> List[Dict[str, Any]]:
        """List all available backups.

        Returns:
            List of backup metadata dictionaries
        """
        backups = []

        for backup_file in sorted(self.backup_dir.glob("backup_*.json"), reverse=True):
            try:
                with open(backup_file) as f:
                    config = json.load(f)

                backups.append({
                    "file": str(backup_file),
                    "filename": backup_file.name,
                    "date": config.get("backup_date", "unknown"),
                    "node_id": config.get("node_info", {}).get("node_id", "unknown"),
                    "node_name": config.get("node_info", {}).get("long_name", "Unknown"),
                    "schema_version": config.get("schema_version", "0"),
                })
            except Exception as e:
                logger.debug(f"[Backup] Could not read {backup_file}: {e}")

        return backups

    def delete_backup(self, backup_file: Path) -> bool:
        """Delete a backup file.

        Args:
            backup_file: Path to backup file

        Returns:
            True if deleted, False otherwise
        """
        try:
            backup_path = Path(backup_file)
            if backup_path.exists() and backup_path.parent == self.backup_dir:
                backup_path.unlink()
                logger.info(f"[Backup] Deleted backup: {backup_file}")
                return True
        except Exception as e:
            logger.error(f"[Backup] Failed to delete {backup_file}: {e}")
        return False

    def _run_meshtastic_cmd(self, args: List[str], host: str, port: int) -> str:
        """Run meshtastic CLI command and return output."""
        cli_path = find_meshtastic_cli()

        if not cli_path:
            logger.error("[Backup] meshtastic CLI not found")
            return ""

        cmd = [cli_path, "--host", host, "--port", str(port)] + args
        logger.debug(f"[Backup] Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning(f"[Backup] Command failed: {result.stderr}")
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.error("[Backup] Command timed out")
            return ""
        except FileNotFoundError:
            logger.error("[Backup] meshtastic CLI not found")
            return ""

    def _parse_info(self, output: str) -> Dict[str, Any]:
        """Parse meshtastic --info output."""
        info = {}

        for line in output.split('\n'):
            line = line.strip()
            if ':' in line:
                key, _, value = line.partition(':')
                key = key.strip().lower().replace(' ', '_')
                value = value.strip()

                if key == 'owner':
                    info['long_name'] = value
                elif key == 'my_node_num':
                    info['node_num'] = int(value) if value.isdigit() else value
                elif key == 'firmware_version':
                    info['firmware_version'] = value
                elif key == 'hw_model':
                    info['hardware_model'] = value
                elif 'node_id' in key or key == 'id':
                    info['node_id'] = value

        return info

    def _parse_settings(self, output: str) -> Dict[str, Dict[str, Any]]:
        """Parse meshtastic --get all output into categorized settings."""
        settings = {
            "lora": {},
            "device": {},
            "position": {},
            "power": {},
            "display": {},
            "bluetooth": {},
            "network": {},
        }

        current_section = None

        for line in output.split('\n'):
            line = line.strip()

            # Detect section headers
            if 'lora' in line.lower() and ':' not in line:
                current_section = "lora"
            elif 'device' in line.lower() and ':' not in line:
                current_section = "device"
            elif 'position' in line.lower() and ':' not in line:
                current_section = "position"
            elif 'power' in line.lower() and ':' not in line:
                current_section = "power"
            elif 'display' in line.lower() and ':' not in line:
                current_section = "display"
            elif 'bluetooth' in line.lower() and ':' not in line:
                current_section = "bluetooth"
            elif 'network' in line.lower() or 'wifi' in line.lower():
                current_section = "network"

            # Parse key-value pairs
            if ':' in line and current_section:
                key, _, value = line.partition(':')
                key = key.strip().lower().replace(' ', '_')
                value = value.strip()

                # Convert types
                if value.lower() in ['true', 'false']:
                    value = value.lower() == 'true'
                elif value.isdigit():
                    value = int(value)
                elif self._is_float(value):
                    value = float(value)

                settings[current_section][key] = value

        return settings

    def _get_channels(self, host: str, port: int) -> List[Dict[str, Any]]:
        """Get channel configuration."""
        channels = []

        for idx in range(8):  # Meshtastic supports up to 8 channels
            output = self._run_meshtastic_cmd(["--ch-index", str(idx), "--info"], host, port)
            if not output or "error" in output.lower():
                break

            channel = {"index": idx}
            for line in output.split('\n'):
                if ':' in line:
                    key, _, value = line.partition(':')
                    key = key.strip().lower().replace(' ', '_')
                    value = value.strip()

                    if key in ['name', 'psk', 'role']:
                        channel[key] = value
                    elif key in ['uplink_enabled', 'downlink_enabled']:
                        channel[key] = value.lower() == 'true'

            if channel.get('name') or channel.get('role'):
                channels.append(channel)

        return channels

    def _apply_setting(self, key: str, value: Any, host: str, port: int,
                       dry_run: bool, result: Dict) -> None:
        """Apply a single setting to the device."""
        if value is None:
            result["skipped"].append(f"{key}=None")
            return

        if dry_run:
            result["applied"].append(f"{key}={value} (dry run)")
            return

        try:
            # Convert boolean to lowercase string
            if isinstance(value, bool):
                value = str(value).lower()

            output = self._run_meshtastic_cmd(["--set", key, str(value)], host, port)

            if "error" in output.lower():
                result["errors"].append(f"{key}: {output}")
            else:
                result["applied"].append(f"{key}={value}")
        except Exception as e:
            result["errors"].append(f"{key}: {e}")

    def _apply_channel_setting(self, index: int, key: str, value: Any,
                                host: str, port: int, dry_run: bool, result: Dict) -> None:
        """Apply a channel setting to the device."""
        if value is None or key == "psk":  # Skip PSK for security
            result["skipped"].append(f"ch{index}.{key}")
            return

        if dry_run:
            result["applied"].append(f"ch{index}.{key}={value} (dry run)")
            return

        try:
            if isinstance(value, bool):
                value = str(value).lower()

            output = self._run_meshtastic_cmd(
                ["--ch-index", str(index), "--ch-set", key, str(value)],
                host, port
            )

            if "error" in output.lower():
                result["errors"].append(f"ch{index}.{key}: {output}")
            else:
                result["applied"].append(f"ch{index}.{key}={value}")
        except Exception as e:
            result["errors"].append(f"ch{index}.{key}: {e}")

    def _save_backup(self, config: Dict[str, Any], output_path: Path) -> None:
        """Save backup to file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2, default=str)
        logger.info(f"[Backup] Saved backup to {output_path}")

    @staticmethod
    def _is_float(value: str) -> bool:
        """Check if string is a float."""
        try:
            float(value)
            return '.' in value
        except ValueError:
            return False


# CLI interface
def main():
    """Command-line interface for backup/restore."""
    import argparse

    parser = argparse.ArgumentParser(description="MeshForge Device Backup/Restore")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export device config to backup")
    export_parser.add_argument("--host", default="localhost", help="Meshtasticd host")
    export_parser.add_argument("--port", type=int, default=4403, help="Meshtasticd port")
    export_parser.add_argument("-o", "--output", help="Output file path")

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore device config from backup")
    restore_parser.add_argument("backup_file", help="Backup file to restore")
    restore_parser.add_argument("--host", default="localhost", help="Meshtasticd host")
    restore_parser.add_argument("--port", type=int, default=4403, help="Meshtasticd port")
    restore_parser.add_argument("--dry-run", action="store_true", help="Show what would be restored")

    # List command
    subparsers.add_parser("list", help="List available backups")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    manager = DeviceBackupManager()

    if args.command == "export":
        result = manager.export_device_config(
            output_file=args.output,
            host=args.host,
            port=args.port
        )
        print(f"Backup created: {result.get('backup_file')}")

    elif args.command == "restore":
        result = manager.restore_device_config(
            backup_file=Path(args.backup_file),
            host=args.host,
            port=args.port,
            dry_run=args.dry_run
        )
        print(f"Restore {'(dry run) ' if args.dry_run else ''}completed:")
        print(f"  Applied: {len(result['applied'])}")
        print(f"  Skipped: {len(result['skipped'])}")
        print(f"  Errors: {len(result['errors'])}")
        if result['errors']:
            for err in result['errors']:
                print(f"    - {err}")

    elif args.command == "list":
        backups = manager.list_backups()
        if backups:
            print("Available backups:")
            for b in backups:
                print(f"  {b['filename']} - {b['node_name']} ({b['date']})")
        else:
            print("No backups found")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
