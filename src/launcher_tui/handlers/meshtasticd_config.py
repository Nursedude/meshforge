"""
Meshtasticd Config Handler — Thin dispatcher for meshtasticd configuration.

Converted from meshtasticd_config_mixin.py and _config_menu in main.py as part
of the mixin-to-registry migration (Batch 9).

Routes to sub-handlers (meshtasticd_lora, meshtasticd_mqtt, meshtasticd_nodedb)
via the handler registry, and handles its own inline items (view, overlays,
presets, hardware, status, owner, web client, edit, restart).
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path

from handler_protocol import BaseHandler
from backend import clear_screen
from utils.service_check import (
    check_service, check_systemd_service,
    apply_config_and_restart,
)
logger = logging.getLogger(__name__)

# Direct imports for first-party modules (MF006: no safe_import for first-party)
from core.meshtastic_cli import get_cli as _get_cli
from utils.meshtastic_http import get_http_client as _get_http_client
from utils.broker_profiles import get_active_profile as _get_active_profile

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
    # Radio presets
    # ------------------------------------------------------------------

    def _radio_presets_menu(self):
        """Radio/LoRa preset selection via meshtastic CLI."""
        current_preset = None
        try:
            from utils.lora_presets import detect_meshtastic_settings
            detection = detect_meshtastic_settings()
            if detection and detection.get('preset'):
                current_preset = detection['preset']
        except Exception:
            pass

        presets = [
            ("SHORT_TURBO", "500kHz SF7  - Max speed, <1km"),
            ("SHORT_FAST", "250kHz SF7  - Urban, 1-5km"),
            ("SHORT_SLOW", "125kHz SF7  - Reliable short"),
            ("MEDIUM_FAST", "250kHz SF10 - MtnMesh std, 5-20km"),
            ("MEDIUM_SLOW", "125kHz SF10 - Alt medium"),
            ("LONG_FAST", "250kHz SF11 - Default, 10-30km"),
            ("LONG_MODERATE", "125kHz SF11 - Extended, 15-40km"),
            ("LONG_SLOW", "125kHz SF12 - Max range, 20-50km"),
            ("back", "Back"),
        ]

        if current_preset:
            presets = [
                (tag, f"{desc} [ACTIVE]" if tag == current_preset else desc)
                for tag, desc in presets
            ]

        current_info = f"\nCurrent: {current_preset}" if current_preset else "\nCurrent: Unknown"

        choice = self.ctx.dialog.menu(
            "Radio Presets",
            f"Select LoRa modem preset:{current_info}\n\n"
            "Higher speed = shorter range\n"
            "Lower speed = longer range",
            presets
        )

        if choice and choice != "back":
            self._apply_radio_preset(choice)

    def _apply_radio_preset(self, preset: str):
        """Apply a radio preset via meshtastic CLI."""
        preset_info = {
            "SHORT_TURBO": {"bw": 500, "sf": 7, "cr": 8},
            "SHORT_FAST": {"bw": 250, "sf": 7, "cr": 8},
            "SHORT_SLOW": {"bw": 125, "sf": 7, "cr": 8},
            "MEDIUM_FAST": {"bw": 250, "sf": 10, "cr": 8},
            "MEDIUM_SLOW": {"bw": 125, "sf": 10, "cr": 8},
            "LONG_FAST": {"bw": 250, "sf": 11, "cr": 8},
            "LONG_MODERATE": {"bw": 125, "sf": 11, "cr": 8},
            "LONG_SLOW": {"bw": 125, "sf": 12, "cr": 8},
        }

        info = preset_info.get(preset, {})
        if not info:
            return

        slot_input = self.ctx.dialog.inputbox(
            "Frequency Slot",
            f"Set frequency slot (channel_num) for {preset}:\n\n"
            "Slot determines the center frequency.\n"
            "US: 0=903.875 MHz (default), 12=903.625 (HawaiiNet)\n"
            "Must match your mesh network's slot.\n\n"
            "Leave empty or 0 for default:",
            "0"
        )

        if slot_input is None:
            return

        try:
            freq_slot = int(slot_input) if slot_input.strip() else 0
        except ValueError:
            freq_slot = 0

        confirm_text = (
            f"Apply {preset} preset?\n\n"
            f"Bandwidth: {info['bw']} kHz\n"
            f"Spreading Factor: SF{info['sf']}\n"
            f"Coding Rate: 4/{info['cr']}\n"
            f"Frequency Slot: {freq_slot}\n\n"
            "Applied via meshtastic CLI (--set lora.modem_preset).\n"
            "Region must already be set (use Web Client)."
        )

        confirm = self.ctx.dialog.yesno(
            "Apply Preset",
            confirm_text,
            default_no=True
        )

        if not confirm:
            return

        self.ctx.dialog.infobox("Applying", f"Applying {preset} preset...")

        try:
            cli = _get_cli()

            result = cli.set_lora_preset(preset)
            if not result.success:
                self.ctx.dialog.msgbox("Error",
                    f"Failed to set modem preset:\n{result.error}\n\n"
                    "Ensure meshtastic CLI is installed and\n"
                    "meshtasticd is running with region set.")
                return

            verified = '[verified]' in (result.output or '')

            slot_result = cli.set_channel_num(freq_slot)
            slot_msg = ""
            if not slot_result.success:
                slot_msg = f"\nFrequency slot: FAILED ({slot_result.error})"
            else:
                slot_msg = f"\nFrequency slot: {freq_slot}"

            from utils.device_config_store import save_device_settings
            save_device_settings({
                'lora': {
                    'modem_preset': preset,
                    'channel_num': freq_slot,
                }
            })

            verify_note = " (verified)" if verified else ""
            self.ctx.dialog.msgbox("Success",
                f"{preset} preset applied!{verify_note}\n\n"
                f"Modem preset: {preset}{slot_msg}\n\n"
                "Settings saved for restart persistence.\n"
                "Will be re-applied if meshtasticd restarts.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to apply preset:\n{e}")

    # ------------------------------------------------------------------
    # Hardware config
    # ------------------------------------------------------------------

    def _classify_hardware_config(self, config_path: Path) -> str:
        """Classify a hardware config as 'usb' or 'spi'."""
        try:
            from core.meshtasticd_config import RADIO_TEMPLATES, RadioType
            template = RADIO_TEMPLATES.get(config_path.stem, {})
            if template:
                rtype = template.get("radio_type")
                if rtype == RadioType.USB_SERIAL:
                    return "usb"
                return "spi"
        except ImportError:
            pass

        try:
            content = config_path.read_text(errors='replace')[:500]
            if 'Serial:' in content and 'spidev' not in content.lower():
                return "usb"
        except Exception:
            pass
        return "spi"

    def _hardware_config_menu(self):
        """Hardware configuration selection with USB/SPI categorization."""
        try:
            from core.meshtasticd_config import MeshtasticdConfig
            config_mgr = MeshtasticdConfig()
            config_mgr.ensure_structure()
        except PermissionError:
            logger.debug("Cannot auto-create templates (no root), using existing")
        except Exception as e:
            logger.debug("Template auto-creation failed: %s", e)

        available_dir = Path('/etc/meshtasticd/available.d')
        config_d = Path('/etc/meshtasticd/config.d')

        if not available_dir.exists():
            self.ctx.dialog.msgbox("Error",
                "Hardware templates not found.\n\n"
                f"Expected: {available_dir}\n\n"
                "Run with sudo to auto-create, or run the installer.")
            return

        available = list(available_dir.glob('*.yaml'))
        if not available:
            self.ctx.dialog.msgbox("Error",
                "No hardware templates found.\n\n"
                "Run with sudo to auto-create, or run the installer.")
            return

        active = set()
        if config_d.exists():
            active = {f.name for f in config_d.glob('*.yaml')}

        usb_configs = []
        spi_configs = []
        for cfg in sorted(available):
            if self._classify_hardware_config(cfg) == "usb":
                usb_configs.append(cfg)
            else:
                spi_configs.append(cfg)

        choices = []
        choices.append(("--usb--", f"--- USB Radios ({len(usb_configs)}) ---"))
        for cfg in usb_configs:
            status = " [ACTIVE]" if cfg.name in active else ""
            choices.append((cfg.name, f"  {cfg.stem}{status}"))

        choices.append(("--spi--", f"--- SPI HATs ({len(spi_configs)}) ---"))
        for cfg in spi_configs:
            status = " [ACTIVE]" if cfg.name in active else ""
            choices.append((cfg.name, f"  {cfg.stem}{status}"))

        choices.append(("view", "View Config Details"))
        choices.append(("back", "Back"))

        active_names = [n.replace('.yaml', '') for n in active
                        if n != 'meshforge-overrides.yaml']
        active_display = ', '.join(active_names) if active_names else 'none'

        choice = self.ctx.dialog.menu(
            "Hardware Config",
            f"Total: {len(available)} templates | "
            f"Active: {active_display}\n\n"
            "Select hardware configuration to activate:",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice in ("--usb--", "--spi--"):
            self._hardware_config_menu()
        elif choice == "view":
            self._view_hardware_config(available)
        else:
            self._activate_hardware_config(choice, available_dir, config_d)

    def _activate_hardware_config(self, config_name: str, available_dir: Path, config_d: Path):
        """Activate a hardware configuration."""
        src = available_dir / config_name

        if not src.exists():
            self.ctx.dialog.msgbox("Error", f"Config not found: {src}")
            return

        confirm = self.ctx.dialog.yesno(
            "Activate Config",
            f"Activate hardware config?\n\n"
            f"Template: {config_name}\n\n"
            "This will:\n"
            f"1. Copy to {config_d}/\n"
            "2. Restart meshtasticd service",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.ctx.dialog.infobox("Activating", f"Activating {config_name}...")
            config_d.mkdir(parents=True, exist_ok=True)
            dst = config_d / config_name
            shutil.copy(src, dst)
            apply_config_and_restart('meshtasticd')
            self.ctx.dialog.msgbox("Success",
                f"Hardware config activated!\n\n"
                f"Config: {dst}\n\n"
                "Service restarted.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Activation failed:\n{e}")

    def _view_hardware_config(self, configs: list):
        """View details of a hardware config."""
        choices = [(cfg.name, cfg.stem[:30]) for cfg in sorted(configs)]
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "View Config",
            "Select config to view:",
            choices
        )

        if choice and choice != "back":
            config_path = Path('/etc/meshtasticd/available.d') / choice
            if config_path.exists():
                try:
                    content = config_path.read_text()[:1500]
                    self.ctx.dialog.msgbox(f"Config: {choice}", content)
                except Exception as e:
                    self.ctx.dialog.msgbox("Error", str(e))

    # ------------------------------------------------------------------
    # Edit / restart
    # ------------------------------------------------------------------

    def _offer_restart(self, message: str):
        """Offer to restart meshtasticd after a config change."""
        if self.ctx.dialog.yesno(
            "Restart Service?",
            f"{message}\n\n"
            f"Saved to: {OVERLAY_PATH}\n"
            "(config.yaml unchanged)\n\n"
            "Restart meshtasticd to apply?",
            default_no=False
        ):
            self._restart_meshtasticd()

    def _edit_config_menu(self):
        """Edit config files directly."""
        choices = [
            ("main", "Main Config (/etc/meshtasticd/config.yaml)"),
            ("active", "Active Hardware Configs"),
            ("templates", "Hardware Templates"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Edit Config Files",
            "Edit meshtasticd configuration files:\n\n"
            "Opens in nano editor.\n"
            "Save: Ctrl+O, Exit: Ctrl+X",
            choices
        )

        if choice is None or choice == "back":
            return

        if choice == "main":
            self._edit_file('/etc/meshtasticd/config.yaml')
        elif choice == "active":
            self._edit_config_d()
        elif choice == "templates":
            self._edit_available_d()

    def _edit_file(self, path: str):
        """Edit a file with nano."""
        if not Path(path).exists():
            if '/etc/meshtasticd/' in path:
                try:
                    from core.meshtasticd_config import MeshtasticdConfig
                    config_mgr = MeshtasticdConfig()
                    config_mgr.ensure_structure()
                except Exception as e:
                    logger.debug("Auto-create config failed: %s", e)
            if not Path(path).exists():
                self.ctx.dialog.msgbox("Error", f"File not found:\n{path}")
                return

        clear_screen()
        subprocess.run(['nano', path])  # Interactive editor - no timeout

        if self.ctx.dialog.yesno(
            "Restart Service?",
            "Config file modified.\n\n"
            "Restart meshtasticd to apply changes?",
            default_no=False
        ):
            self._restart_meshtasticd()

    def _edit_config_d(self):
        """Edit files in config.d."""
        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            self.ctx.dialog.msgbox("Error", f"Directory not found:\n{config_d}")
            return

        configs = list(config_d.glob('*.yaml'))
        if not configs:
            self.ctx.dialog.msgbox("Info", "No active configs in config.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.ctx.dialog.menu(
            "Active Configs",
            "Select config to edit (Cancel to go back):",
            choices
        )

        if choice:
            self._edit_file(choice)

    def _edit_available_d(self):
        """Edit files in available.d."""
        available_d = Path('/etc/meshtasticd/available.d')
        if not available_d.exists():
            self.ctx.dialog.msgbox("Error", f"Directory not found:\n{available_d}")
            return

        configs = list(available_d.glob('*.yaml'))
        if not configs:
            self.ctx.dialog.msgbox("Info", "No templates in available.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.ctx.dialog.menu(
            "Hardware Templates",
            "Select template to view (Cancel to go back):",
            choices
        )

        if choice:
            self._edit_file(choice)

    def _restart_meshtasticd(self):
        """Restart meshtasticd service and re-apply saved device settings."""
        confirm = self.ctx.dialog.yesno(
            "Restart Service",
            "Restart meshtasticd?\n\n"
            "This will:\n"
            "1. Reload systemd daemon\n"
            "2. Restart meshtasticd service\n"
            "3. Wait for TCP readiness\n"
            "4. Re-apply saved device settings",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.ctx.dialog.infobox("Restarting", "Restarting meshtasticd...")

            success, msg = apply_config_and_restart('meshtasticd')
            if not success:
                self.ctx.dialog.msgbox("Error", f"Restart failed:\n{msg}")
                return

            from utils.device_config_store import load_device_config, apply_saved_config
            saved = load_device_config()

            if not saved:
                self.ctx.dialog.msgbox("Success", f"meshtasticd restarted.\n\n{msg}")
                return

            sections = []
            for section, values in saved.items():
                items = [f"  {k}: {v}" for k, v in values.items()]
                sections.append(f"{section}:\n" + "\n".join(items))
            summary = "\n".join(sections)

            reapply = self.ctx.dialog.yesno(
                "Re-apply Settings?",
                f"meshtasticd restarted.\n\n"
                f"Saved device settings found:\n{summary}\n\n"
                "Re-apply these settings now?\n"
                "(Device config may have reverted to defaults)",
                default_no=False
            )

            if not reapply:
                self.ctx.dialog.msgbox("Info",
                    "Settings NOT re-applied.\n\n"
                    "You can re-apply manually via the\n"
                    "Radio Presets or Owner Name menus.")
                return

            self.ctx.dialog.infobox("Applying", "Re-applying saved device settings...")

            cli = _get_cli()
            all_ok, results = apply_saved_config(cli)

            if all_ok:
                self.ctx.dialog.msgbox("Success",
                    "meshtasticd restarted and settings restored!\n\n"
                    f"{results}")
            else:
                self.ctx.dialog.msgbox("Partial Success",
                    "Some settings could not be restored:\n\n"
                    f"{results}\n\n"
                    "Check the web UI at :9443 to verify.")

        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Error", "Restart timed out")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Restart failed:\n{e}")
