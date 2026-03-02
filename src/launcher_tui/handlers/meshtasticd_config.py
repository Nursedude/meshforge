"""
Meshtasticd Config Handler — Thin dispatcher for meshtasticd configuration.

Converted from meshtasticd_config_mixin.py and _config_menu in main.py as part
of the mixin-to-registry migration (Batch 9).

Routes to sub-handlers (meshtasticd_lora, meshtasticd_mqtt, meshtasticd_nodedb)
via the handler registry, and handles its own inline items (view, overlays,
presets, hardware, status, owner, web client, edit, restart).

Hardware config, radio presets, edit, and restart operations are delegated to
_meshtasticd_hw.py (extracted for file size compliance, CLAUDE.md #6).
"""

import logging
import os
import sys
import tempfile
import yaml
from pathlib import Path

from handler_protocol import BaseHandler
from backend import clear_screen
from utils.service_check import check_service, check_systemd_service

logger = logging.getLogger(__name__)

# --- Shared overlay utilities (imported by sub-handlers) ---

OVERLAY_PATH = Path('/etc/meshtasticd/config.d/meshforge-overrides.yaml')
OVERLAY_HEADER = (
    "# MeshForge configuration overrides\n"
    "# These settings override /etc/meshtasticd/config.yaml\n"
    "# To reset: sudo rm this file and restart meshtasticd\n"
)


def read_overlay() -> dict:
    """Load meshforge-overrides.yaml from config.d/ (or empty dict)."""
    if OVERLAY_PATH.exists():
        try:
            data = yaml.safe_load(OVERLAY_PATH.read_text())
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.debug("Failed to read overlay: %s", e)
    return {}


def write_overlay(data: dict, dialog=None) -> bool:
    """Write meshforge-overrides.yaml to config.d/. Never touches config.yaml.

    Uses atomic write (tempfile + rename) to prevent corruption on
    power loss or interruption.
    """
    try:
        OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
        content = OVERLAY_HEADER + "\n" + yaml.dump(
            data, default_flow_style=False, sort_keys=False
        )
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(OVERLAY_PATH.parent), suffix='.tmp'
        )
        try:
            with os.fdopen(tmp_fd, 'w') as f:
                f.write(content)
            os.rename(tmp_path, str(OVERLAY_PATH))
        except BaseException:
            os.unlink(tmp_path)
            raise
        return True
    except PermissionError:
        if dialog:
            dialog.msgbox("Error", "Permission denied. Run with sudo.")
        return False
    except Exception as e:
        if dialog:
            dialog.msgbox("Error", f"Failed to write overlay:\n{e}")
        return False


def ensure_meshtasticd_config():
    """Auto-create /etc/meshtasticd structure and templates if missing."""
    try:
        from core.meshtasticd_config import MeshtasticdConfig
        MeshtasticdConfig().ensure_structure()
    except PermissionError:
        logger.debug("Cannot auto-create meshtasticd config (no root)")
    except Exception as e:
        logger.debug("meshtasticd config auto-create failed: %s", e)


# Desired menu order for the meshtasticd submenu.
_MESHTASTICD_ORDERING = [
    "web", "status", "owner", "lora", "presets", "hardware",
    "channels", "mqtt", "gateway", "cleanup", "edit", "restart",
]


class MeshtasticdConfigHandler(BaseHandler):
    """TUI handler for meshtasticd configuration (thin dispatcher + core)."""

    handler_id = "meshtasticd_config"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("radio", "Radio Config        meshtasticd settings", "meshtastic"),
        ]

    def execute(self, action):
        if action == "radio":
            self._config_menu()

    # ------------------------------------------------------------------
    # Top-level config menu (moved from main.py)
    # ------------------------------------------------------------------

    def _config_menu(self):
        """Configuration management for meshtasticd."""
        ensure_meshtasticd_config()

        while True:
            choices = [
                ("view", "View Active Config"),
                ("overlays", "View config.d/ Overlays"),
                ("available", "Available Hardware Configs"),
                ("presets", "LoRa Presets"),
                ("channels", "Channel Configuration"),
                ("meshtasticd", "Advanced meshtasticd Config"),
                ("settings", "MeshForge Settings"),
                ("wizard", "Run Setup Wizard"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Configuration",
                "meshtasticd & MeshForge configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "view": ("View Active Config", self._view_active_config),
                "overlays": ("Config Overlays", self._view_config_overlays),
                "available": ("Available Hardware Configs", self._view_available_configs),
                "presets": ("LoRa Presets", self._radio_presets_menu),
                "meshtasticd": ("Advanced Config", self._meshtasticd_menu),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
                continue

            # Cross-handler dispatch via registry
            if choice == "channels":
                if self.ctx.registry:
                    self.ctx.registry.dispatch("configuration", "channels")
                continue
            if choice == "settings":
                if self.ctx.registry:
                    self.ctx.registry.dispatch("configuration", "meshforge")
                continue
            if choice == "wizard":
                if self.ctx.registry:
                    self.ctx.registry.dispatch("configuration", "wizard")
                continue

    # ------------------------------------------------------------------
    # View methods (moved from main.py)
    # ------------------------------------------------------------------

    def _view_active_config(self):
        """Show the active meshtasticd config.yaml."""
        clear_screen()
        print("=== meshtasticd config.yaml ===\n")

        config_path = Path('/etc/meshtasticd/config.yaml')

        if not config_path.exists():
            ensure_meshtasticd_config()

        if config_path.exists():
            print(f"File: {config_path}\n")
            try:
                print(config_path.read_text())
            except PermissionError:
                print("Permission denied. Try: sudo cat /etc/meshtasticd/config.yaml")
        else:
            print("config.yaml not found!\n")
            print("Run MeshForge with sudo to auto-create:")
            print("  sudo python3 src/launcher_tui/main.py")
            print("\nOr create manually:")
            print("  sudo mkdir -p /etc/meshtasticd/{available.d,config.d}")
            print("  sudo cp templates/config.yaml /etc/meshtasticd/")
            print("  sudo cp templates/available.d/*.yaml /etc/meshtasticd/available.d/")

        self.ctx.wait_for_enter()

    def _view_config_overlays(self):
        """Show config.d/ overlay files (active hardware configs)."""
        clear_screen()
        print("=== config.d/ Active Hardware Configs ===\n")

        config_d = Path('/etc/meshtasticd/config.d')

        if not config_d.exists():
            ensure_meshtasticd_config()

        if not config_d.exists():
            print("config.d/ directory not found.")
            print("\nRun with sudo to auto-create, or:")
            print("  sudo mkdir -p /etc/meshtasticd/config.d")
            self.ctx.wait_for_enter()
            return

        overlays = sorted(config_d.glob('*.yaml'))
        if not overlays:
            print("No active hardware configs in config.d/\n")
            print("Select your hardware from:")
            print("  Configuration > Available Hardware Configs")
            print("  Configuration > Advanced meshtasticd Config > Hardware Config")
        else:
            print(f"Found {len(overlays)} active config(s):\n")
            for f in overlays:
                size = f.stat().st_size
                print(f"  {f.name} ({size} bytes)")

            print("\n" + "=" * 50)
            for f in overlays:
                print(f"\n--- {f.name} ---")
                try:
                    print(f.read_text())
                except PermissionError:
                    print("  (permission denied)")

        self.ctx.wait_for_enter()

    def _view_available_configs(self):
        """Show available hardware configs (USB + SPI HATs)."""
        clear_screen()
        print("=== Available Hardware Configs ===\n")

        available_d = Path('/etc/meshtasticd/available.d')

        if not available_d.exists():
            ensure_meshtasticd_config()

        if not available_d.exists():
            print("available.d/ not found.\n")
            print("Run with sudo to auto-create, or:")
            print("  sudo mkdir -p /etc/meshtasticd/available.d")
            print("  sudo cp templates/available.d/*.yaml /etc/meshtasticd/available.d/")
            self.ctx.wait_for_enter()
            return

        configs = sorted(available_d.glob('*.yaml'))
        if not configs:
            print("No hardware configs available.")
        else:
            usb_configs = [f for f in configs if '-usb' in f.stem or f.stem.startswith('usb-')]
            spi_configs = [f for f in configs if f not in usb_configs]

            if usb_configs:
                print(f"USB Radios ({len(usb_configs)}):")
                for i, f in enumerate(usb_configs, 1):
                    print(f"  {i:2d}. {f.stem}")

            if spi_configs:
                if usb_configs:
                    print()
                print(f"SPI HATs ({len(spi_configs)}):")
                for i, f in enumerate(spi_configs, 1):
                    print(f"  {i:2d}. {f.stem}")

            config_d = Path('/etc/meshtasticd/config.d')
            if config_d.exists():
                active = list(config_d.glob('*.yaml'))
                if active:
                    print(f"\nActive: {', '.join(f.stem for f in active)}")

            print(f"\nTotal: {len(configs)} templates")
            print("\nActivate via: Configuration > Advanced meshtasticd Config > Hardware Config")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Meshtasticd submenu (thin dispatcher)
    # ------------------------------------------------------------------

    def _meshtasticd_menu(self):
        """Meshtasticd configuration menu (thin dispatcher)."""
        ensure_meshtasticd_config()

        while True:
            # Own inline items
            own_items = [
                ("web", "Web Client (Full Config)"),
                ("status", "Service Status"),
                ("owner", "Set Owner/Node Name"),
                ("presets", "Radio Presets (LoRa)"),
                ("hardware", "Hardware Config"),
                ("channels", "Channel Config"),
                ("edit", "Edit Config Files"),
                ("restart", "Restart Service"),
            ]

            # Merge with registry sub-handler items (lora, mqtt, cleanup)
            registry_items = []
            if self.ctx.registry:
                registry_items = self.ctx.registry.get_menu_items("meshtasticd")

            registry_tags = {tag for tag, _ in registry_items}
            own_map = {tag: desc for tag, desc in own_items}
            reg_map = {tag: desc for tag, desc in registry_items}
            all_map = {**own_map, **reg_map}

            # Apply ordering
            result = []
            for tag in _MESHTASTICD_ORDERING:
                if tag in all_map:
                    result.append((tag, all_map[tag]))
            # Append any unordered items
            ordered_set = set(_MESHTASTICD_ORDERING)
            for tag, desc in list(own_items) + list(registry_items):
                if tag not in ordered_set and (tag, desc) not in result:
                    result.append((tag, desc))

            result.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "Meshtasticd Config",
                "Configure meshtasticd radio daemon:",
                result
            )

            if choice is None or choice == "back":
                break

            # Try registry sub-handlers first (lora, mqtt, cleanup)
            if choice in registry_tags:
                if self.ctx.registry:
                    self.ctx.registry.dispatch("meshtasticd", choice)
                continue

            # Own inline dispatch
            own_dispatch = {
                "web": ("Web Client", self._show_web_client_info),
                "status": ("Service Status", self._meshtasticd_status),
                "owner": ("Set Owner Name", self._set_owner_name),
                "presets": ("Radio Presets", self._radio_presets_menu),
                "hardware": ("Hardware Config", self._hardware_config_menu),
                "edit": ("Edit Config Files", self._edit_config_menu),
                "restart": ("Restart Service", self._restart_meshtasticd),
            }
            entry = own_dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
                continue

            # Cross-handler dispatch
            if choice == "channels":
                if self.ctx.registry:
                    self.ctx.registry.dispatch("configuration", "channels")
            elif choice == "gateway":
                # Gateway template is in ChannelConfigHandler
                handler = self.ctx.registry.get_handler("channel_config") if self.ctx.registry else None
                if handler and hasattr(handler, '_gateway_template_menu'):
                    self.ctx.safe_call("Gateway Template", handler._gateway_template_menu)

    # ------------------------------------------------------------------
    # General operations
    # ------------------------------------------------------------------

    def _show_web_client_info(self):
        """Show meshtasticd web client info with URL."""
        # Try WebClientHandler first
        if self.ctx.registry:
            handler = self.ctx.registry.get_handler("web_client")
            if handler:
                handler.execute("web")
                return

        # Fallback: show URL info
        import socket
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
            if local_ip.startswith('127.'):
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(2)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
        except OSError as e:
            logger.debug("Local IP detection failed: %s", e)
            local_ip = "YOUR_PI_IP"

        self.ctx.dialog.msgbox(
            "Meshtastic Web Client",
            f"Full radio configuration via browser:\n\n"
            f"  URL: https://{local_ip}:9443\n\n"
            f"Set these to join your mesh network:\n"
            f"  Config > LoRa > Region  (US, EU_868, etc.)\n"
            f"  Config > LoRa > Preset  (LONG_FAST, etc.)\n"
            f"  Config > Channels       (PSK, name)\n\n"
            f"The web client gives full access to all\n"
            f"meshtasticd settings, maps, and messaging."
        )

    def _meshtasticd_status(self):
        """Show meshtasticd service status."""
        self.ctx.dialog.infobox("Status", "Checking meshtasticd status...")

        try:
            status = check_service('meshtasticd')
            is_running = status.available
            _, is_enabled = check_systemd_service('meshtasticd')

            preset_display = "Unknown (select via Radio Presets)"
            region_display = ""
            detection_method = ""
            if is_running:
                try:
                    from utils.lora_presets import detect_meshtastic_settings
                    detection = detect_meshtastic_settings()
                    if detection and detection.get('preset'):
                        preset_display = detection['preset']
                        detection_method = detection.get('detection_method', '')
                        if detection.get('region'):
                            region_display = detection['region']
                except Exception as e:
                    logger.debug("Preset detection failed (service still running): %s", e)

            config_path = Path('/etc/meshtasticd/config.yaml')
            config_exists = config_path.exists()

            if not config_exists:
                ensure_meshtasticd_config()
                config_exists = config_path.exists()

            config_d = Path('/etc/meshtasticd/config.d')
            active_configs = list(config_d.glob('*.yaml')) if config_d.exists() else []

            available_d = Path('/etc/meshtasticd/available.d')
            available_count = len(list(available_d.glob('*.yaml'))) if available_d.exists() else 0

            text = "Meshtasticd Service Status:\n"
            if is_running:
                text += "\nService: RUNNING"
            else:
                text += "\nService: STOPPED"
                if status.fix_hint:
                    text += f"\n  Hint: {status.fix_hint}"
            text += f"\nBoot:    {'enabled' if is_enabled else 'not enabled (will not start on reboot)'}"
            text += f"\n\nPreset:  {preset_display}"
            if region_display:
                text += f"\nRegion:  {region_display}"
            if detection_method:
                text += f"\n  (detected via {detection_method})"
            elif is_running and preset_display.startswith("Unknown"):
                text += "\n  (CLI detection unavailable — select preset manually)"
            text += f"\n\nConfig File: {config_path}"
            text += f"\nConfig Exists: {'Yes' if config_exists else 'No — run with sudo to create'}"
            text += f"\nAvailable Templates: {available_count}"
            text += f"\n\nActive Hardware Configs: {len(active_configs)}"

            for cfg in active_configs[:5]:
                text += f"\n  - {cfg.name}"

            if len(active_configs) > 5:
                text += f"\n  ... and {len(active_configs) - 5} more"

            if not active_configs and available_count > 0:
                text += "\n  (none — select hardware from Hardware Config)"

            self.ctx.dialog.msgbox("Meshtasticd Status", text)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to get status:\n{e}")

    def _set_owner_name(self):
        """Set node owner name (long name and short name)."""
        self.ctx.dialog.infobox("Owner", "Getting current owner info...")

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            result = mesh_cmd.get_node_info()
            current_long = ""
            current_short = ""

            if result.success and result.raw:
                for line in result.raw.split('\n'):
                    if 'longName' in line or 'long_name' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            current_long = parts[1].strip().strip('"')
                    elif 'shortName' in line or 'short_name' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            current_short = parts[1].strip().strip('"')

            long_name = self.ctx.dialog.inputbox(
                "Set Long Name",
                f"Enter node name (current: {current_long or 'none'}):",
                current_long or ""
            )

            if long_name is None:
                return

            short_name = self.ctx.dialog.inputbox(
                "Set Short Name",
                f"Enter 4-char short name (current: {current_short or 'none'}):",
                current_short or ""
            )

            if short_name is None:
                return

            if long_name:
                long_name = long_name[:40]
            if short_name:
                short_name = short_name[:4].upper()

            changes_made = []

            if long_name:
                self.ctx.dialog.infobox("Setting", f"Setting long name to: {long_name}")
                result = mesh_cmd.set_owner(long_name)
                if result.success:
                    changes_made.append(f"Long name: {long_name}")
                else:
                    self.ctx.dialog.msgbox("Error", f"Failed to set long name:\n{result.message}")
                    return

            if short_name:
                self.ctx.dialog.infobox("Setting", f"Setting short name to: {short_name}")
                result = mesh_cmd.set_owner_short(short_name)
                if result.success:
                    changes_made.append(f"Short name: {short_name}")
                else:
                    self.ctx.dialog.msgbox("Error", f"Failed to set short name:\n{result.message}")
                    return

            if changes_made:
                from utils.device_config_store import save_device_settings
                owner_data = {}
                if long_name:
                    owner_data['long_name'] = long_name
                if short_name:
                    owner_data['short_name'] = short_name
                save_device_settings({'owner': owner_data})

                self.ctx.dialog.msgbox("Success",
                    f"Owner settings updated:\n\n"
                    + "\n".join(changes_made)
                    + "\n\nSaved for restart persistence.")
            else:
                self.ctx.dialog.msgbox("Info", "No changes made.")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to set owner name:\n{e}")

    # ------------------------------------------------------------------
    # Radio presets (delegated to _meshtasticd_hw)
    # ------------------------------------------------------------------

    def _radio_presets_menu(self):
        from handlers._meshtasticd_hw import radio_presets_menu
        radio_presets_menu(self)

    def _apply_radio_preset(self, preset: str):
        from handlers._meshtasticd_hw import apply_radio_preset
        apply_radio_preset(self, preset)

    # ------------------------------------------------------------------
    # Hardware config (delegated to _meshtasticd_hw)
    # ------------------------------------------------------------------

    def _classify_hardware_config(self, config_path: Path) -> str:
        from handlers._meshtasticd_hw import classify_hardware_config
        return classify_hardware_config(self, config_path)

    def _hardware_config_menu(self):
        from handlers._meshtasticd_hw import hardware_config_menu
        hardware_config_menu(self)

    def _activate_hardware_config(self, config_name: str, available_dir: Path, config_d: Path):
        from handlers._meshtasticd_hw import activate_hardware_config
        activate_hardware_config(self, config_name, available_dir, config_d)

    def _view_hardware_config(self, configs: list):
        from handlers._meshtasticd_hw import view_hardware_config
        view_hardware_config(self, configs)

    # ------------------------------------------------------------------
    # Edit / restart (delegated to _meshtasticd_hw)
    # ------------------------------------------------------------------

    def _offer_restart(self, message: str):
        from handlers._meshtasticd_hw import offer_restart
        offer_restart(self, message)

    def _edit_config_menu(self):
        from handlers._meshtasticd_hw import edit_config_menu
        edit_config_menu(self)

    def _edit_file(self, path: str):
        from handlers._meshtasticd_hw import edit_file
        edit_file(self, path)

    def _edit_config_d(self):
        from handlers._meshtasticd_hw import edit_config_d
        edit_config_d(self)

    def _edit_available_d(self):
        from handlers._meshtasticd_hw import edit_available_d
        edit_available_d(self)

    def _restart_meshtasticd(self):
        from handlers._meshtasticd_hw import restart_meshtasticd
        restart_meshtasticd(self)
