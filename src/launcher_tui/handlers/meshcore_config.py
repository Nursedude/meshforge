"""
MeshCore Config Handler — /etc/meshcore/ configuration management in the TUI.

Converted from meshcore_config_mixin.py as part of the mixin-to-registry migration.
Manages the /etc/meshcore/ directory structure, connection templates, and
device detection.
"""

import logging
import os
import subprocess

from backend import clear_screen
from handler_protocol import BaseHandler, PRIVILEGE_ADMIN

logger = logging.getLogger(__name__)

# First-party imports — meshcore_config is part of core/ but depends on
# optional packages for full functionality
try:
    from core.meshcore_config import (
        MeshcoreConfig, get_meshcore_config, setup_meshcore,
        MeshCoreRadioType
    )
    _HAS_MC_CONFIG = True
except ImportError:
    _HAS_MC_CONFIG = False

try:
    from utils.service_check import check_meshcore_device
    _HAS_DEVICE_CHECK = True
except ImportError:
    _HAS_DEVICE_CHECK = False


def _check_device_access(device_path: str) -> bool:
    """Check if a device path is readable."""
    return os.access(device_path, os.R_OK)


class MeshCoreConfigHandler(BaseHandler):
    """TUI handler for MeshCore /etc/meshcore/ configuration management."""

    handler_id = "meshcore_config"
    menu_section = "configuration"
    privilege_level = PRIVILEGE_ADMIN

    def menu_items(self):
        return [
            ("meshcore_config", "MeshCore Config     /etc/meshcore/ management", "meshcore"),
        ]

    def execute(self, action):
        if action == "meshcore_config":
            self._meshcore_config_menu()

    def _meshcore_config_menu(self):
        """MeshCore Config — manage /etc/meshcore/ directory structure."""
        if not _HAS_MC_CONFIG:
            self.ctx.dialog.msgbox(
                "MeshCore Config",
                "MeshCore configuration module not available.\n\n"
                "This feature requires core.meshcore_config."
            )
            return

        def _build_choices():
            config = get_meshcore_config()
            status = config.get_status()
            status_line = self._mc_config_status_line(status)
            return [
                ("status", f"Status              {status_line}"),
                ("setup", "Setup               Create config directory structure"),
                ("detect", "Detect Devices      Scan for companion radios"),
                ("templates", "Templates           View available configs"),
                ("enable", "Enable Config       Activate a connection config"),
                ("disable", "Disable Config      Deactivate a connection config"),
                ("edit", "Edit Config         Edit config files (nano)"),
            ]

        dispatch = {
            "status": ("MC Config Status", self._mc_config_status),
            "setup": ("MC Config Setup", self._mc_config_setup),
            "detect": ("MC Detect Devices", self._mc_config_detect),
            "templates": ("MC Templates", self._mc_config_templates),
            "enable": ("MC Enable Config", self._mc_config_enable),
            "disable": ("MC Disable Config", self._mc_config_disable),
            "edit": ("MC Edit Config", self._mc_config_edit),
        }
        self.run_menu_loop(
            "MeshCore Config",
            "MeshCore /etc/meshcore/ configuration:",
            _build_choices, dispatch,
        )

    def _mc_config_status_line(self, status: dict) -> str:
        """Build status line for MeshCore config menu subtitle."""
        parts = []
        if status.get("structure_exists"):
            parts.append(f"available: {status.get('available_configs', 0)}")
            parts.append(f"enabled: {status.get('enabled_configs', 0)}")
        else:
            parts.append("/etc/meshcore not initialized")

        devices = status.get("detected_devices", [])
        if devices:
            parts.append(f"devices: {len(devices)}")

        py_installed = status.get("meshcore_py_installed", False)
        parts.append(f"meshcore_py: {'yes' if py_installed else 'no'}")

        return " | ".join(parts)

    def _mc_config_status(self):
        """Show comprehensive MeshCore configuration status."""
        config = get_meshcore_config()
        status = config.get_status()

        lines = [
            "MESHCORE CONFIGURATION STATUS",
            "=" * 35,
            "",
            f"Config Directory:   {status['config_dir']}",
            f"Structure Exists:   {status['structure_exists']}",
            f"Radio Type:         {status['radio_type']}",
            f"meshcore_py:        {'installed' if status['meshcore_py_installed'] else 'NOT INSTALLED'}",
            f"Available Configs:  {status['available_configs']}",
            f"Enabled Configs:    {status['enabled_configs']}",
            "",
            "DETECTED DEVICES:",
        ]

        devices = status.get("detected_devices", [])
        if devices:
            for dev in devices:
                lines.append(f"  {dev}")
        else:
            lines.append("  (none)")

        # Show enabled configs
        lines.append("")
        lines.append("ENABLED CONFIGS:")
        enabled = config.list_enabled()
        if enabled:
            for cfg in enabled:
                lines.append(f"  {cfg.name} ({cfg.radio_type.value})")
        else:
            lines.append("  (none)")

        if not status['meshcore_py_installed']:
            lines.extend([
                "",
                "INSTALL MESHCORE LIBRARY:",
                "  pip install meshcore",
                "  (requires Python 3.10+)",
            ])

        self.ctx.dialog.msgbox(
            "MeshCore Config Status", "\n".join(lines), height=25, width=60
        )

    def _mc_config_setup(self):
        """Create /etc/meshcore/ directory structure."""
        confirm = self.ctx.dialog.yesno(
            "Setup MeshCore Config",
            "This will create the MeshCore configuration directory:\n\n"
            "  /etc/meshcore/\n"
            "  /etc/meshcore/available.d/  (connection templates)\n"
            "  /etc/meshcore/config.d/     (enabled configs)\n"
            "  /etc/meshcore/config.yaml   (main config)\n\n"
            "Continue?"
        )

        if not confirm:
            return

        success = setup_meshcore()
        if success:
            config = get_meshcore_config()
            available = config.list_available()
            self.ctx.dialog.msgbox(
                "Setup Complete",
                f"MeshCore config structure created.\n\n"
                f"Templates deployed: {len(available)}\n\n"
                "Next: Enable a connection config and connect your radio."
            )
        else:
            self.ctx.dialog.msgbox(
                "Setup Failed",
                "Failed to create config structure.\n\n"
                "Make sure you are running with sudo."
            )

    def _mc_config_detect(self):
        """Scan for MeshCore companion radio devices."""
        config = get_meshcore_config()
        devices = config.detect_devices()

        if devices:
            lines = ["DETECTED SERIAL DEVICES:", ""]
            for dev in devices:
                accessible = (
                    "accessible" if _check_device_access(dev) else "no permission"
                )
                lines.append(f"  {dev}  ({accessible})")
            lines.extend([
                "",
                "Note: These are serial devices that COULD be MeshCore radios.",
                "Verify by checking device firmware.",
            ])
            self.ctx.dialog.msgbox(
                "Device Detection", "\n".join(lines), height=15, width=60
            )
        else:
            self.ctx.dialog.msgbox(
                "Device Detection",
                "No USB serial devices found.\n\n"
                "Connect a MeshCore companion radio via USB and try again.\n\n"
                "Supported devices:\n"
                "  - RAK4631 (nRF52840)\n"
                "  - Heltec V3 (ESP32-S3)\n"
                "  - T-Deck (ESP32-S3)\n"
                "  - Nano G2 Ultra (ESP32-S3)"
            )

    def _mc_config_templates(self):
        """List available MeshCore connection templates."""
        config = get_meshcore_config()
        available = config.list_available()

        if not available:
            self.ctx.dialog.msgbox(
                "Templates",
                "No templates found.\n\n"
                "Run 'Setup' to create the config directory structure."
            )
            return

        lines = ["AVAILABLE MESHCORE CONFIGS:", ""]
        for cfg in available:
            status = "[ENABLED]" if cfg.enabled else ""
            lines.append(f"  {cfg.name:<25} {cfg.description} {status}")

        self.ctx.dialog.msgbox(
            "MeshCore Templates", "\n".join(lines), height=15, width=70
        )

    def _mc_config_enable(self):
        """Enable a MeshCore connection configuration."""
        config = get_meshcore_config()
        available = config.list_available()
        disabled = [c for c in available if not c.enabled]

        if not disabled:
            self.ctx.dialog.msgbox(
                "Enable Config",
                "All configurations are already enabled, or none available.\n\n"
                "Run 'Setup' if no configs exist."
            )
            return

        choices = [
            (c.name, f"{c.description} ({c.radio_type.value})")
            for c in disabled
        ]

        choice = self.ctx.dialog.menu(
            "Enable Config",
            "Select a configuration to enable:",
            choices
        )

        if choice:
            success = config.enable(choice)
            if success:
                self.ctx.dialog.msgbox("Config Enabled", f"Enabled: {choice}")
            else:
                self.ctx.dialog.msgbox("Error", f"Failed to enable: {choice}")

    def _mc_config_disable(self):
        """Disable a MeshCore connection configuration."""
        config = get_meshcore_config()
        enabled = config.list_enabled()

        if not enabled:
            self.ctx.dialog.msgbox(
                "Disable Config", "No configs are currently enabled."
            )
            return

        choices = [
            (c.name, f"{c.description} ({c.radio_type.value})")
            for c in enabled
        ]

        choice = self.ctx.dialog.menu(
            "Disable Config",
            "Select a configuration to disable:",
            choices
        )

        if choice:
            success = config.disable(choice)
            if success:
                self.ctx.dialog.msgbox("Config Disabled", f"Disabled: {choice}")
            else:
                self.ctx.dialog.msgbox("Error", f"Failed to disable: {choice}")

    def _mc_config_edit(self):
        """Edit MeshCore config files with nano."""
        config = get_meshcore_config()
        if not config.main_config.exists():
            self.ctx.dialog.msgbox(
                "Edit Config",
                "Config directory not set up.\n\n"
                "Run 'Setup' first to create the config structure."
            )
            return

        # Build file list
        files = [("main", f"config.yaml   {config.main_config}")]

        for cfg_file in sorted(config.available_dir.glob("*.yaml")):
            files.append((str(cfg_file), f"available.d/{cfg_file.name}"))

        for cfg_file in sorted(config.config_d_dir.glob("*.yaml")):
            files.append((str(cfg_file), f"config.d/{cfg_file.name}"))

        choice = self.ctx.dialog.menu(
            "Edit Config File",
            "Select a file to edit:",
            files
        )

        if choice:
            file_path = config.main_config if choice == "main" else choice
            clear_screen()
            try:
                subprocess.run(
                    ["nano", str(file_path)],
                    timeout=300,
                )
            except FileNotFoundError:
                self.ctx.dialog.msgbox("Error", "nano editor not found.")
            except subprocess.TimeoutExpired:
                pass
