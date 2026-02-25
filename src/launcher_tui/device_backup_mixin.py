"""
Device Backup Menu Mixin - Backup and restore device configurations.

Extracted to mixin per CLAUDE.md guidelines.
Provides UI for commands/device_backup.py functionality.
"""

import subprocess
from backend import clear_screen
from commands.device_backup import create_backup
from commands.device_backup import list_backups, get_backup_dir
from commands.device_backup import restore_backup
from commands.device_backup import delete_backup


class DeviceBackupMixin:
    """Mixin providing device backup/restore functionality."""

    def _device_backup_menu(self):
        """Device backup and restore menu."""
        while True:
            choices = [
                ("create", "Create Backup       Backup current device"),
                ("list", "List Backups        View saved backups"),
                ("restore", "Restore Backup      Restore from backup"),
                ("delete", "Delete Backup       Remove old backups"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Device Backup",
                "Backup and restore Meshtastic configurations:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "create": ("Create Backup", self._create_device_backup),
                "list": ("List Backups", self._list_device_backups),
                "restore": ("Restore Backup", self._restore_device_backup),
                "delete": ("Delete Backup", self._delete_device_backup),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _create_device_backup(self):
        """Create a new device backup."""
        # Ask for connection type
        conn_choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
        ]

        conn_type = self.dialog.menu(
            "Connection",
            "Select device connection:",
            conn_choices
        )

        if conn_type is None:
            return

        connection = "localhost"
        port = 4403

        if conn_type == "serial":
            port_input = self.dialog.inputbox(
                "Serial Port",
                "Enter serial port:",
                "/dev/ttyUSB0"
            )
            if not port_input:
                return
            connection = port_input
        elif conn_type == "remote":
            host_input = self.dialog.inputbox(
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
                    self.dialog.msgbox("Error", "Invalid port number.")
                    return
            else:
                connection = host_input

        # Ask for backup notes
        notes = self.dialog.inputbox(
            "Backup Notes",
            "Optional notes for this backup:",
            ""
        )
        if notes is None:
            notes = ""

        # Show progress
        self.dialog.infobox("Creating Backup", "Backing up device configuration...")

        # Create the backup
        result = create_backup(
            connection=connection,
            port=port,
            backup_type="full",
            notes=notes
        )

        if result['success']:
            self.dialog.msgbox(
                "Backup Created",
                f"Backup saved successfully!\n\n"
                f"ID: {result['backup_id']}\n"
                f"File: {result['file_path']}"
            )
        else:
            self.dialog.msgbox(
                "Backup Failed",
                f"Could not create backup:\n\n{result['error']}"
            )

    def _list_device_backups(self):
        """List available device backups."""
        backups = list_backups()

        if not backups:
            backup_dir = get_backup_dir()
            self.dialog.msgbox(
                "No Backups",
                f"No backups found.\n\nBackup directory:\n{backup_dir}"
            )
            return

        # Display backups in terminal for better formatting
        clear_screen()
        print("=== Device Backups ===\n")

        for backup in backups:
            created = backup.get('created_at', 'Unknown')[:19]  # Trim to datetime
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
        self._wait_for_enter()

    def _restore_device_backup(self):
        """Restore device from a backup."""
        backups = list_backups()

        if not backups:
            self.dialog.msgbox("No Backups", "No backups available to restore.")
            return

        # Build selection menu
        choices = []
        for backup in backups:
            backup_id = backup.get('backup_id', 'unknown')
            device = backup.get('device_name', 'Unknown')
            created = backup.get('created_at', '')[:10]  # Just date
            label = f"{device} ({created})"
            choices.append((backup_id, label))

        choices.append(("back", "Cancel"))

        selected = self.dialog.menu(
            "Select Backup",
            "Choose a backup to restore:",
            choices
        )

        if selected is None or selected == "back":
            return

        # Confirm restore
        if not self.dialog.yesno(
            "Confirm Restore",
            f"Restore from backup: {selected}?\n\n"
            "This will overwrite current device settings."
        ):
            return

        # Ask for connection type
        conn_choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
        ]

        conn_type = self.dialog.menu(
            "Connection",
            "Select target device connection:",
            conn_choices
        )

        if conn_type is None:
            return

        connection = "localhost"
        port = 4403

        if conn_type == "serial":
            port_input = self.dialog.inputbox(
                "Serial Port",
                "Enter serial port:",
                "/dev/ttyUSB0"
            )
            if not port_input:
                return
            connection = port_input
        elif conn_type == "remote":
            host_input = self.dialog.inputbox(
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
                    self.dialog.msgbox("Error", "Invalid port number.")
                    return
            else:
                connection = host_input

        # Show progress
        self.dialog.infobox("Restoring", "Restoring device configuration...")

        # Restore the backup
        result = restore_backup(
            backup_id=selected,
            connection=connection,
            port=port
        )

        if result['success']:
            items = "\n".join(f"  - {item}" for item in result['restored_items'])
            self.dialog.msgbox(
                "Restore Complete",
                f"Successfully restored:\n\n{items}"
            )
        else:
            self.dialog.msgbox(
                "Restore Failed",
                f"Could not restore backup:\n\n{result['error']}"
            )

    def _delete_device_backup(self):
        """Delete a device backup."""
        backups = list_backups()

        if not backups:
            self.dialog.msgbox("No Backups", "No backups available to delete.")
            return

        # Build selection menu
        choices = []
        for backup in backups:
            backup_id = backup.get('backup_id', 'unknown')
            device = backup.get('device_name', 'Unknown')
            created = backup.get('created_at', '')[:10]  # Just date
            label = f"{device} ({created})"
            choices.append((backup_id, label))

        choices.append(("back", "Cancel"))

        selected = self.dialog.menu(
            "Select Backup",
            "Choose a backup to delete:",
            choices
        )

        if selected is None or selected == "back":
            return

        # Confirm deletion
        if not self.dialog.yesno(
            "Confirm Delete",
            f"Delete backup: {selected}?\n\n"
            "This cannot be undone."
        ):
            return

        # Delete the backup
        result = delete_backup(selected)

        if result['success']:
            self.dialog.msgbox("Deleted", "Backup deleted successfully.")
        else:
            self.dialog.msgbox(
                "Delete Failed",
                f"Could not delete backup:\n\n{result['error']}"
            )
