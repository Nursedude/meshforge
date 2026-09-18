"""
Device Backup Handler — Backup and restore device configurations.

Converted from device_backup_mixin.py as part of the mixin-to-registry migration.
"""

import datetime
import logging
import tarfile
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from commands.device_backup import create_backup, list_backups, get_backup_dir
from commands.device_backup import restore_backup, delete_backup
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class BackupHandler(BaseHandler):
    """TUI handler for device backup/restore."""

    handler_id = "backup"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("backup", "Device Backup       Backup/restore configs", None),
        ]

    def execute(self, action):
        if action == "backup":
            self._device_backup_menu()

    def _device_backup_menu(self):
        while True:
            choices = [
                ("create", "Create Backup       Backup current device"),
                ("full", "Full Config Backup  MeshForge + device configs"),
                ("list", "List Backups        View saved backups"),
                ("restore", "Restore Backup      Restore from backup"),
                ("delete", "Delete Backup       Remove old backups"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Device Backup",
                "Backup and restore Meshtastic configurations:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "create": ("Create Backup", self._create_device_backup),
                "full": ("Full Config Backup", self._full_config_backup),
                "list": ("List Backups", self._list_device_backups),
                "restore": ("Restore Backup", self._restore_device_backup),
                "delete": ("Delete Backup", self._delete_device_backup),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "BackupHandler._device_backup_menu")

    def _create_device_backup(self):
        conn_choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
        ]

        conn_type = self.ctx.dialog.menu(
            "Connection",
            "Select device connection:",
            conn_choices
        )

        if conn_type is None:
            return

        connection = "localhost"
        port = 4403

        if conn_type == "serial":
            port_input = self.ctx.dialog.inputbox(
                "Serial Port",
                "Enter serial port:",
                "/dev/ttyUSB0"
            )
            if not port_input:
                return
            connection = port_input
        elif conn_type == "remote":
            host_input = self.ctx.dialog.inputbox(
                "Remote Host",
                "Enter hostname:port:",
                "192.168.1.100:4403"
            )
            if not host_input:
                return
            if ':' in host_input:
                connection, port_str = host_input.rsplit(':', 1)
                try:
                    port = int(port_str)
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid port number.")
                    return
            else:
                connection = host_input

        notes = self.ctx.dialog.inputbox(
            "Backup Notes",
            "Optional notes for this backup:",
            ""
        )
        if notes is None:
            notes = ""

        self.ctx.dialog.infobox("Creating Backup", "Backing up device configuration...")

        result = create_backup(
            connection=connection,
            port=port,
            backup_type="full",
            notes=notes
        )

        if result['success']:
            self.ctx.dialog.msgbox(
                "Backup Created",
                f"Backup saved successfully!\n\n"
                f"ID: {result['backup_id']}\n"
                f"File: {result['file_path']}"
            )
        else:
            self.ctx.dialog.msgbox(
                "Backup Failed",
                f"Could not create backup:\n\n{result['error']}"
            )

    def _full_config_backup(self):
        """Archive all MeshForge configs (~/.config/meshforge/) to backup dir."""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        backup_dir = get_backup_dir()

        if not config_dir.exists():
            self.ctx.dialog.msgbox(
                "No Config",
                f"MeshForge config directory not found:\n{config_dir}\n\n"
                "Nothing to back up.")
            return

        # Count files to back up
        config_files = list(config_dir.rglob("*"))
        file_count = sum(1 for f in config_files if f.is_file())
        if file_count == 0:
            self.ctx.dialog.msgbox("Empty", "Config directory exists but has no files.")
            return

        confirm = self.ctx.dialog.yesno(
            "Full Config Backup",
            f"Back up all MeshForge configuration?\n\n"
            f"Source: {config_dir}\n"
            f"Files:  {file_count}\n\n"
            "Includes: gateway config, broker profiles,\n"
            "MQTT settings, deployment profile, app settings.\n\n"
            f"Saved to: {backup_dir}/",
        )

        if not confirm:
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"meshforge-config-{timestamp}.tar.gz"
        archive_path = backup_dir / archive_name

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(str(config_dir), arcname="meshforge")
            archive_path.chmod(0o600)  # May contain credentials
            self.ctx.dialog.msgbox(
                "Backup Created",
                f"Full config backup saved!\n\n"
                f"Archive: {archive_path}\n"
                f"Files:   {file_count}\n\n"
                "Restore: extract to ~/.config/meshforge/")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Backup failed:\n{e}")

    def _list_device_backups(self):
        backups = list_backups()

        if not backups:
            backup_dir = get_backup_dir()
            self.ctx.dialog.msgbox(
                "No Backups",
                f"No backups found.\n\nBackup directory:\n{backup_dir}"
            )
            return

        clear_screen()
        print("=== Device Backups ===\n")

        for backup in backups:
            created = backup.get('created_at', 'Unknown')[:19]
            device = backup.get('device_name', 'Unknown')
            hw = backup.get('hardware_model', 'Unknown')
            fw = backup.get('firmware_version', 'Unknown')
            notes = backup.get('notes', '')
            backup_id = backup.get('backup_id', 'Unknown')

            print(f"  ID: {backup_id}")
            print(f"     Device: {device} ({hw})")
            print(f"     Firmware: {fw}")
            print(f"     Created: {created}")
            if notes:
                print(f"     Notes: {notes}")
            print()

        print(f"Total: {len(backups)} backup(s)")
        print()
        self.ctx.wait_for_enter()

    def _restore_device_backup(self):
        backups = list_backups()

        if not backups:
            self.ctx.dialog.msgbox("No Backups", "No backups available to restore.")
            return

        choices = []
        for backup in backups:
            backup_id = backup.get('backup_id', 'unknown')
            device = backup.get('device_name', 'Unknown')
            created = backup.get('created_at', '')[:10]
            label = f"{device} ({created})"
            choices.append((backup_id, label))

        choices.append(("cancel", "Cancel"))

        selected = self.ctx.dialog.menu(
            "Select Backup",
            "Choose a backup to restore:",
            choices
        )

        if selected is None or selected == "cancel":
            return

        if not self.ctx.dialog.yesno(
            "Confirm Restore",
            f"Restore from backup: {selected}?\n\n"
            "This will overwrite current device settings."
        ):
            return

        conn_choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
        ]

        conn_type = self.ctx.dialog.menu(
            "Connection",
            "Select target device connection:",
            conn_choices
        )

        if conn_type is None:
            return

        connection = "localhost"
        port = 4403

        if conn_type == "serial":
            port_input = self.ctx.dialog.inputbox(
                "Serial Port",
                "Enter serial port:",
                "/dev/ttyUSB0"
            )
            if not port_input:
                return
            connection = port_input
        elif conn_type == "remote":
            host_input = self.ctx.dialog.inputbox(
                "Remote Host",
                "Enter hostname:port:",
                "192.168.1.100:4403"
            )
            if not host_input:
                return
            if ':' in host_input:
                connection, port_str = host_input.rsplit(':', 1)
                try:
                    port = int(port_str)
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid port number.")
                    return
            else:
                connection = host_input

        self.ctx.dialog.infobox("Restoring", "Restoring device configuration...")

        result = restore_backup(
            backup_id=selected,
            connection=connection,
            port=port
        )

        if result['success']:
            items = "\n".join(f"  - {item}" for item in result['restored_items'])
            self.ctx.dialog.msgbox(
                "Restore Complete",
                f"Successfully restored:\n\n{items}"
            )
        else:
            self.ctx.dialog.msgbox(
                "Restore Failed",
                f"Could not restore backup:\n\n{result['error']}"
            )

    def _delete_device_backup(self):
        backups = list_backups()

        if not backups:
            self.ctx.dialog.msgbox("No Backups", "No backups available to delete.")
            return

        choices = []
        for backup in backups:
            backup_id = backup.get('backup_id', 'unknown')
            device = backup.get('device_name', 'Unknown')
            created = backup.get('created_at', '')[:10]
            label = f"{device} ({created})"
            choices.append((backup_id, label))

        choices.append(("cancel", "Cancel"))

        selected = self.ctx.dialog.menu(
            "Select Backup",
            "Choose a backup to delete:",
            choices
        )

        if selected is None or selected == "cancel":
            return

        if not self.ctx.dialog.yesno(
            "Confirm Delete",
            f"Delete backup: {selected}?\n\n"
            "This cannot be undone."
        ):
            return

        result = delete_backup(selected)

        if result['success']:
            self.ctx.dialog.msgbox("Deleted", "Backup deleted successfully.")
        else:
            self.ctx.dialog.msgbox(
                "Delete Failed",
                f"Could not delete backup:\n\n{result['error']}"
            )
