"""
Fleet Backup Handler — Backup and restore fleet node state.

Provides TUI access to fleet backup/restore operations:
  - Local node backup (identity, config, AI memory)
  - Push backups to peer Pis for redundancy
  - List and verify backup archives
  - Rotate old backups
  - Fleet config setup wizard

Feature-gated: requires "fleet_management" feature flag (PRO edition).
"""

import logging
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from commands.fleet_backup import (
    create_local_backup,
    list_backups,
    load_fleet_config,
    push_to_peers,
    rotate_backups,
    verify_backup,
    get_backup_base,
)
from utils.paths import get_real_user_home, MeshForgePaths

logger = logging.getLogger(__name__)


class FleetBackupHandler(BaseHandler):
    """TUI handler for fleet backup and recovery."""

    handler_id = "fleet_backup"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("fleet_backup", "Fleet Backup       Backup/restore fleet state", "fleet_management"),
        ]

    def execute(self, action):
        if action == "fleet_backup":
            self._fleet_backup_menu()

    def _fleet_backup_menu(self):
        while True:
            fleet_cfg = load_fleet_config()
            has_fleet = fleet_cfg is not None

            choices = [
                ("local", "Backup This Node    Create local backup archive"),
            ]

            if has_fleet:
                choices.append(("push", "Backup & Push       Backup + push to peer Pis"))

            choices.extend([
                ("list", "List Backups        View all backup archives"),
                ("verify", "Verify Backup       Check archive integrity"),
                ("status", "Fleet Status        Last backup per node"),
                ("rotate", "Rotate Backups      Remove old archives"),
            ])

            if not has_fleet:
                choices.append(("setup", "Setup Fleet Config  Configure peer Pis"))

            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "Fleet Backup & Recovery",
                "Backup node state (identity, config, AI memory) for disaster recovery:",
                choices,
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "local": ("Backup This Node", self._backup_local),
                "push": ("Backup & Push", self._backup_and_push),
                "list": ("List Backups", self._list_backups),
                "verify": ("Verify Backup", self._verify_backup),
                "status": ("Fleet Status", self._fleet_status),
                "rotate": ("Rotate Backups", self._rotate_backups),
                "setup": ("Setup Fleet Config", self._setup_fleet_config),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _backup_local(self):
        """Create a local backup of this node."""
        clear_screen()
        print("Creating fleet backup of this node...\n")

        result = create_local_backup()

        if result.success:
            archive = result.data.get("archive_path", "")
            items = result.data.get("items", 0)
            size = result.data.get("total_size", 0)
            size_str = _format_size(size)

            self.ctx.dialog.msgbox(
                "Backup Complete",
                f"Node backup created successfully.\n\n"
                f"Archive: {Path(archive).name}\n"
                f"Items:   {items} categories\n"
                f"Size:    {size_str}\n\n"
                f"Location: {archive}",
            )
        else:
            self.ctx.dialog.msgbox(
                "Backup Failed",
                f"{result.message}\n\n{result.error or ''}",
            )

    def _backup_and_push(self):
        """Create a local backup and push to peer Pis."""
        clear_screen()
        print("Creating fleet backup and pushing to peers...\n")

        result = create_local_backup()
        if not result.success:
            self.ctx.dialog.msgbox("Backup Failed", f"{result.message}\n\n{result.error or ''}")
            return

        archive_path = result.data.get("archive_path", "")
        print(f"\nBackup created: {Path(archive_path).name}\n")
        print("Pushing to peer Pis...\n")

        push_result = push_to_peers(archive_path)

        pushed = push_result.data.get("pushed", [])
        failed = push_result.data.get("failed", [])

        msg_parts = [f"Backup: {Path(archive_path).name}\n"]

        if pushed:
            msg_parts.append("Pushed to:")
            for p in pushed:
                msg_parts.append(f"  + {p}")

        if failed:
            msg_parts.append("\nFailed:")
            for f in failed:
                msg_parts.append(f"  ! {f}")

        self.ctx.dialog.msgbox(
            "Backup & Push Complete" if pushed else "Push Failed",
            "\n".join(msg_parts),
        )

    def _list_backups(self):
        """List all available backup archives."""
        result = list_backups()
        hosts = result.data.get("hosts", {})

        if not hosts:
            self.ctx.dialog.msgbox("No Backups", "No backup archives found.")
            return

        lines = []
        for host_name, archives in sorted(hosts.items()):
            lines.append(f"=== {host_name} ({len(archives)} backups) ===")
            for a in archives[:5]:  # Show most recent 5
                size_str = _format_size(a["size"])
                latest = " (latest)" if a.get("is_latest") else ""
                lines.append(f"  {a['name']}  {size_str}{latest}")
            if len(archives) > 5:
                lines.append(f"  ... and {len(archives) - 5} more")
            lines.append("")

        self.ctx.dialog.msgbox("Fleet Backups", "\n".join(lines))

    def _verify_backup(self):
        """Verify a backup archive integrity."""
        base = get_backup_base()
        if not base.is_dir():
            self.ctx.dialog.msgbox("No Backups", "No backup directory found.")
            return

        # Collect all archives
        archives = []
        for host_dir in sorted(base.iterdir()):
            if not host_dir.is_dir():
                continue
            for archive in sorted(host_dir.glob("*.tar.gz")):
                if not archive.is_symlink():
                    archives.append(archive)

        if not archives:
            self.ctx.dialog.msgbox("No Backups", "No backup archives found.")
            return

        choices = [
            (str(i), f"{a.parent.name}/{a.name}  ({_format_size(a.stat().st_size)})")
            for i, a in enumerate(archives)
        ]

        choice = self.ctx.dialog.menu("Select Backup", "Choose an archive to verify:", choices)
        if choice is None:
            return

        archive = archives[int(choice)]
        result = verify_backup(str(archive))

        if result.success:
            self.ctx.dialog.msgbox(
                "Verification OK",
                f"Archive: {archive.name}\n\n{result.message}",
            )
        else:
            self.ctx.dialog.msgbox(
                "Verification Failed",
                f"Archive: {archive.name}\n\n{result.message}\n{result.error or ''}",
            )

    def _fleet_status(self):
        """Show last backup date per node."""
        result = list_backups()
        hosts = result.data.get("hosts", {})

        if not hosts:
            self.ctx.dialog.msgbox("Fleet Status", "No backups found.\n\nRun a backup first.")
            return

        lines = ["Last backup per node:\n"]
        for host_name, archives in sorted(hosts.items()):
            if archives:
                latest = archives[0]  # Already sorted newest first
                lines.append(f"  {host_name:15s}  {latest['modified'][:19]}  {_format_size(latest['size'])}")
            else:
                lines.append(f"  {host_name:15s}  (no backups)")

        self.ctx.dialog.msgbox("Fleet Backup Status", "\n".join(lines))

    def _rotate_backups(self):
        """Remove old backup archives."""
        fleet_cfg = load_fleet_config()
        keep = 7
        if fleet_cfg:
            keep = fleet_cfg.get("keep_backups", 7)

        confirm = self.ctx.dialog.yesno(
            "Rotate Backups",
            f"Remove old backups, keeping the {keep} most recent per host?\n\n"
            f"This cannot be undone.",
        )

        if not confirm:
            return

        result = rotate_backups(keep=keep)
        self.ctx.dialog.msgbox("Rotation Complete", result.message)

    def _setup_fleet_config(self):
        """Interactive fleet.json creation wizard."""
        config_path = MeshForgePaths.get_config_dir() / "fleet.json"

        if config_path.is_file():
            overwrite = self.ctx.dialog.yesno(
                "Fleet Config Exists",
                f"Fleet config already exists at:\n{config_path}\n\nOverwrite?",
            )
            if not overwrite:
                return

        self.ctx.dialog.msgbox(
            "Fleet Config Setup",
            "This wizard creates ~/.config/meshforge/fleet.json\n"
            "which tells the backup system about your fleet Pis.\n\n"
            "You will need:\n"
            "  - Hostnames and IPs of your fleet Pis\n"
            "  - Path to SSH key for fleet access\n\n"
            "Fleet config is LOCAL ONLY (never committed to git).",
        )

        # Get this hostname
        import subprocess
        hostname_result = subprocess.run(
            ["hostname", "-s"],
            capture_output=True, text=True, timeout=5,
        )
        default_hostname = hostname_result.stdout.strip()

        this_host = self.ctx.dialog.inputbox(
            "This Hostname",
            "Short hostname for this Pi:",
            default_hostname,
        )
        if not this_host:
            return

        # Get SSH key path
        home = get_real_user_home()
        default_key = str(home / ".ssh" / "id_ed25519")
        claude_key = home / ".claude" / "ssh" / "id_ed25519"
        if claude_key.is_file():
            default_key = str(claude_key)

        ssh_key = self.ctx.dialog.inputbox(
            "SSH Key",
            "Path to SSH private key for fleet access:",
            default_key,
        )
        if not ssh_key:
            return

        # Build minimal config
        config = {
            "this_host": this_host,
            "backup_dir": f"~/{BACKUP_DIR_NAME}",
            "ssh_key": ssh_key,
            "keep_backups": 7,
            "peers": {
                this_host: {
                    "ip": "127.0.0.1",
                    "backup_targets": [],
                },
            },
        }

        self.ctx.dialog.msgbox(
            "Fleet Config Created",
            f"Basic fleet config saved to:\n{config_path}\n\n"
            f"Edit the file to add peer Pis:\n"
            f"  nano {config_path}\n\n"
            f"Add entries to 'peers' with IP and backup_targets.\n"
            f"See the fleet backup docs for the full format.",
        )

        import json
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        config_path.chmod(0o600)


# Avoid circular import — use local constant
BACKUP_DIR_NAME = ".meshforge-fleet-backups"


def _format_size(size_bytes: int) -> str:
    """Format byte count as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}K"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}M"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}G"
