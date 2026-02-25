"""
Meshtasticd Configuration Mixin for MeshForge Launcher TUI.

Handles all meshtasticd service configuration, radio presets,
hardware config, and channel management.
Extracted from main.py to reduce file size.
"""

import logging
import os
import re
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path
from backend import clear_screen

logger = logging.getLogger(__name__)

# Import centralized service checker - SINGLE SOURCE OF TRUTH (first-party)
from utils.service_check import (
    check_service, check_systemd_service, ServiceState,
    apply_config_and_restart, _sudo_cmd,
)

# Hoist function-level imports to module level
from core.meshtastic_cli import get_cli
from utils.meshtastic_http import get_http_client
from utils.broker_profiles import get_active_profile


OVERLAY_PATH = Path('/etc/meshtasticd/config.d/meshforge-overrides.yaml')
OVERLAY_HEADER = (
    "# MeshForge configuration overrides\n"
    "# These settings override /etc/meshtasticd/config.yaml\n"
    "# To reset: sudo rm this file and restart meshtasticd\n"
)


class MeshtasticdConfigMixin:
    """Mixin providing meshtasticd configuration methods for the launcher."""

    # LoRa module types supported by meshtasticd
    LORA_MODULES = {
        "sx1262": "SX1262 (Waveshare, Ebyte E22-900M, MeshAdv, etc.)",
        "sx1268": "SX1268 (Ebyte E22-400M, etc.)",
        "sx1280": "SX1280 (2.4 GHz)",
        "RF95": "RF95/RFM95 (Elecrow, Adafruit RFM9x)",
        "sim": "Simulation mode (no radio)",
    }

    def _read_overlay(self) -> dict:
        """Load meshforge-overrides.yaml from config.d/ (or empty dict)."""
        if OVERLAY_PATH.exists():
            try:
                data = yaml.safe_load(OVERLAY_PATH.read_text())
                return data if isinstance(data, dict) else {}
            except Exception as e:
                logger.debug("Failed to read overlay: %s", e)
        return {}

    def _write_overlay(self, data: dict) -> bool:
        """Write meshforge-overrides.yaml to config.d/. Never touches config.yaml.

        Uses atomic write (tempfile + rename) to prevent corruption on
        power loss or interruption.
        """
        try:
            OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
            content = OVERLAY_HEADER + "\n" + yaml.dump(
                data, default_flow_style=False, sort_keys=False
            )
            # Atomic write: temp file in same dir, then rename
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
            self.dialog.msgbox("Error", "Permission denied. Run with sudo.")
            return False
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to write overlay:\n{e}")
            return False

    def _ensure_meshtasticd_config(self):
        """Auto-create /etc/meshtasticd structure and templates if missing."""
        try:
            from core.meshtasticd_config import MeshtasticdConfig
            MeshtasticdConfig().ensure_structure()
        except PermissionError:
            logger.debug("Cannot auto-create meshtasticd config (no root)")
        except Exception as e:
            logger.debug("meshtasticd config auto-create failed: %s", e)

    def _meshtasticd_menu(self):
        """Meshtasticd configuration menu."""
        # Auto-create config structure if missing
        self._ensure_meshtasticd_config()

        while True:
            choices = [
                ("web", "Web Client (Full Config)"),
                ("status", "Service Status"),
                ("owner", "Set Owner/Node Name"),
                ("lora", "LoRa Module Config"),
                ("presets", "Radio Presets (LoRa)"),
                ("hardware", "Hardware Config"),
                ("channels", "Channel Config"),
                ("mqtt", "MQTT Uplink/Downlink"),
                ("gateway", "Gateway Template"),
                ("cleanup", "Node DB Cleanup"),
                ("edit", "Edit Config Files"),
                ("restart", "Restart Service"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Meshtasticd Config",
                "Configure meshtasticd radio daemon:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "web": ("Web Client", self._show_web_client_info),
                "status": ("Service Status", self._meshtasticd_status),
                "owner": ("Set Owner Name", self._set_owner_name),
                "lora": ("LoRa Module Config", self._lora_module_menu),
                "presets": ("Radio Presets", self._radio_presets_menu),
                "hardware": ("Hardware Config", self._hardware_config_menu),
                "channels": ("Channel Config", self._channel_config_menu),
                "mqtt": ("MQTT Config", self._mqtt_device_config),
                "gateway": ("Gateway Template", self._gateway_template_menu),
                "cleanup": ("Node DB Cleanup", self._node_db_cleanup_menu),
                "edit": ("Edit Config Files", self._edit_config_menu),
                "restart": ("Restart Service", self._restart_meshtasticd),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _show_web_client_info(self):
        """Show meshtasticd web client with browser launch option.

        Delegates to _open_web_client() in main.py which provides:
        - Browser launch functionality
        - URL display for copying
        - SSL certificate acceptance guidance
        """
        # Call the unified web client handler from main.py (inherited via mixin)
        if hasattr(self, '_open_web_client'):
            self._open_web_client()
        else:
            # Fallback if method not available (shouldn't happen)
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

            self.dialog.msgbox(
                "Meshtastic Web Client",
                f"Full radio configuration via browser:\n\n"
                f"  URL: https://{local_ip}:9443\n\n"
                f"Set these to join your mesh network:\n"
                f"  Config → LoRa → Region  (US, EU_868, etc.)\n"
                f"  Config → LoRa → Preset  (LONG_FAST, etc.)\n"
                f"  Config → Channels       (PSK, name)\n\n"
                f"The web client gives full access to all\n"
                f"meshtasticd settings, maps, and messaging."
            )

    def _meshtasticd_status(self):
        """Show meshtasticd service status.

        Issue #20 Phase 2: Separates service state from CLI/preset detection.
        Service state comes from systemctl (single source of truth).
        Preset detection is shown separately and never conflated with service state.
        """
        self.dialog.infobox("Status", "Checking meshtasticd status...")

        try:
            # ---- Service state (SINGLE SOURCE OF TRUTH: systemctl) ----
            status = check_service('meshtasticd')
            is_running = status.available
            _, is_enabled = check_systemd_service('meshtasticd')

            # ---- Preset detection (separate from service state) ----
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

            # ---- Config file info ----
            config_path = Path('/etc/meshtasticd/config.yaml')
            config_exists = config_path.exists()

            # Auto-create if missing
            if not config_exists:
                self._ensure_meshtasticd_config()
                config_exists = config_path.exists()

            config_d = Path('/etc/meshtasticd/config.d')
            active_configs = list(config_d.glob('*.yaml')) if config_d.exists() else []

            available_d = Path('/etc/meshtasticd/available.d')
            available_count = len(list(available_d.glob('*.yaml'))) if available_d.exists() else 0

            # ---- Build display (Issue #20 Phase 2: service state and
            #      detection shown separately with actionable hints) ----
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

            self.dialog.msgbox("Meshtasticd Status", text)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get status:\n{e}")

    def _set_owner_name(self):
        """Set node owner name (long name and short name)."""
        self.dialog.infobox("Owner", "Getting current owner info...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd

            # Try to get current owner info
            result = mesh_cmd.get_node_info()
            current_long = ""
            current_short = ""

            if result.success and result.raw:
                # Parse owner info from output
                for line in result.raw.split('\n'):
                    if 'longName' in line or 'long_name' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            current_long = parts[1].strip().strip('"')
                    elif 'shortName' in line or 'short_name' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            current_short = parts[1].strip().strip('"')

            # Show current info and get new values
            text = f"""Set your node's identity:

Current Long Name: {current_long or '(not set)'}
Current Short Name: {current_short or '(not set)'}

Long Name: Your node's full name (max 40 chars)
           Shown on other devices, maps, etc.

Short Name: 4-character abbreviation
           Shown in compact views

Press Cancel to keep current values."""

            # Get long name
            long_name = self.dialog.inputbox(
                "Set Long Name",
                f"Enter node name (current: {current_long or 'none'}):",
                current_long or ""
            )

            if long_name is None:  # Cancelled
                return

            # Get short name
            short_name = self.dialog.inputbox(
                "Set Short Name",
                f"Enter 4-char short name (current: {current_short or 'none'}):",
                current_short or ""
            )

            if short_name is None:  # Cancelled
                return

            # Validate
            if long_name:
                long_name = long_name[:40]
            if short_name:
                short_name = short_name[:4].upper()

            # Apply changes
            changes_made = []

            if long_name:
                self.dialog.infobox("Setting", f"Setting long name to: {long_name}")
                result = mesh_cmd.set_owner(long_name)
                if result.success:
                    changes_made.append(f"Long name: {long_name}")
                else:
                    self.dialog.msgbox("Error", f"Failed to set long name:\n{result.message}")
                    return

            if short_name:
                self.dialog.infobox("Setting", f"Setting short name to: {short_name}")
                result = mesh_cmd.set_owner_short(short_name)
                if result.success:
                    changes_made.append(f"Short name: {short_name}")
                else:
                    self.dialog.msgbox("Error", f"Failed to set short name:\n{result.message}")
                    return

            # Persist owner settings for restart survival
            if changes_made:
                from utils.device_config_store import save_device_settings
                owner_data = {}
                if long_name:
                    owner_data['long_name'] = long_name
                if short_name:
                    owner_data['short_name'] = short_name
                save_device_settings({'owner': owner_data})

                self.dialog.msgbox("Success",
                    f"Owner settings updated:\n\n"
                    + "\n".join(changes_made)
                    + "\n\nSaved for restart persistence.")
            else:
                self.dialog.msgbox("Info", "No changes made.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to set owner name:\n{e}")

    def _radio_presets_menu(self):
        """Radio/LoRa preset selection via meshtastic CLI.

        Issue #20 Phase 2: Shows current detected preset separately from
        service state. Detection failure does not imply service failure.
        """
        # Detect current preset (best-effort, won't block menu)
        current_preset = None
        try:
            from utils.lora_presets import detect_meshtastic_settings
            detection = detect_meshtastic_settings()
            if detection and detection.get('preset'):
                current_preset = detection['preset']
        except Exception:
            pass

        # Define modem presets with descriptions
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

        # Mark current preset in the list
        if current_preset:
            presets = [
                (tag, f"{desc} [ACTIVE]" if tag == current_preset else desc)
                for tag, desc in presets
            ]

        current_info = f"\nCurrent: {current_preset}" if current_preset else "\nCurrent: Unknown"

        choice = self.dialog.menu(
            "Radio Presets",
            f"Select LoRa modem preset:{current_info}\n\n"
            "Higher speed = shorter range\n"
            "Lower speed = longer range",
            presets
        )

        if choice and choice != "back":
            self._apply_radio_preset(choice)

    def _apply_radio_preset(self, preset: str):
        """Apply a radio preset via meshtastic CLI (not config.d YAML)."""
        # Preset display info (for confirmation dialog only)
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

        # Ask for frequency slot
        slot_input = self.dialog.inputbox(
            "Frequency Slot",
            f"Set frequency slot (channel_num) for {preset}:\n\n"
            "Slot determines the center frequency.\n"
            "US: 0=903.875 MHz (default), 12=903.625 (HawaiiNet)\n"
            "Must match your mesh network's slot.\n\n"
            "Leave empty or 0 for default:",
            "0"
        )

        if slot_input is None:  # Cancelled
            return

        try:
            freq_slot = int(slot_input) if slot_input.strip() else 0
        except ValueError:
            freq_slot = 0

        # Build confirmation text
        confirm_text = (
            f"Apply {preset} preset?\n\n"
            f"Bandwidth: {info['bw']} kHz\n"
            f"Spreading Factor: SF{info['sf']}\n"
            f"Coding Rate: 4/{info['cr']}\n"
            f"Frequency Slot: {freq_slot}\n\n"
            "Applied via meshtastic CLI (--set lora.modem_preset).\n"
            "Region must already be set (use Web Client)."
        )

        confirm = self.dialog.yesno(
            "Apply Preset",
            confirm_text,
            default_no=True
        )

        if not confirm:
            return

        self.dialog.infobox("Applying", f"Applying {preset} preset...")

        try:
            cli = get_cli()

            # Apply modem preset (with verification)
            result = cli.set_lora_preset(preset)
            if not result.success:
                self.dialog.msgbox("Error",
                    f"Failed to set modem preset:\n{result.error}\n\n"
                    "Ensure meshtastic CLI is installed and\n"
                    "meshtasticd is running with region set.")
                return

            verified = '[verified]' in (result.output or '')

            # Apply frequency slot (with verification)
            slot_result = cli.set_channel_num(freq_slot)
            slot_msg = ""
            if not slot_result.success:
                slot_msg = f"\nFrequency slot: FAILED ({slot_result.error})"
            else:
                slot_msg = f"\nFrequency slot: {freq_slot}"

            # Persist settings for restart survival
            from utils.device_config_store import save_device_settings
            save_device_settings({
                'lora': {
                    'modem_preset': preset,
                    'channel_num': freq_slot,
                }
            })

            verify_note = " (verified)" if verified else ""
            self.dialog.msgbox("Success",
                f"{preset} preset applied!{verify_note}\n\n"
                f"Modem preset: {preset}{slot_msg}\n\n"
                "Settings saved for restart persistence.\n"
                "Will be re-applied if meshtasticd restarts.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to apply preset:\n{e}")

    def _classify_hardware_config(self, config_path: Path) -> str:
        """Classify a hardware config as 'usb' or 'spi'.

        Uses RADIO_TEMPLATES radio_type first, then falls back to
        inspecting the YAML content for Serial: (USB) vs Lora:/Display: (SPI).
        """
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

        # Fallback: inspect file content
        try:
            content = config_path.read_text(errors='replace')[:500]
            if 'Serial:' in content and 'spidev' not in content.lower():
                return "usb"
        except Exception:
            pass
        return "spi"

    def _hardware_config_menu(self):
        """Hardware configuration selection with USB/SPI categorization."""
        # Auto-create directory structure and templates if missing
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
            self.dialog.msgbox("Error",
                "Hardware templates not found.\n\n"
                f"Expected: {available_dir}\n\n"
                "Run with sudo to auto-create, or run the installer.")
            return

        # List available hardware configs
        available = list(available_dir.glob('*.yaml'))
        if not available:
            self.dialog.msgbox("Error",
                "No hardware templates found.\n\n"
                "Run with sudo to auto-create, or run the installer.")
            return

        # Get currently active configs
        active = set()
        if config_d.exists():
            active = {f.name for f in config_d.glob('*.yaml')}

        # Classify configs into USB and SPI categories
        usb_configs = []
        spi_configs = []
        for cfg in sorted(available):
            if self._classify_hardware_config(cfg) == "usb":
                usb_configs.append(cfg)
            else:
                spi_configs.append(cfg)

        # Build categorized menu choices
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

        # Build subtitle with active config info
        active_names = [n.replace('.yaml', '') for n in active
                        if n != 'meshforge-overrides.yaml']
        active_display = ', '.join(active_names) if active_names else 'none'

        choice = self.dialog.menu(
            "Hardware Config",
            f"Total: {len(available)} templates | "
            f"Active: {active_display}\n\n"
            "Select hardware configuration to activate:",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice in ("--usb--", "--spi--"):
            # Category header selected — re-show menu
            self._hardware_config_menu()
        elif choice == "view":
            self._view_hardware_config(available)
        else:
            self._activate_hardware_config(choice, available_dir, config_d)

    def _activate_hardware_config(self, config_name: str, available_dir: Path, config_d: Path):
        """Activate a hardware configuration."""
        src = available_dir / config_name

        if not src.exists():
            self.dialog.msgbox("Error", f"Config not found: {src}")
            return

        confirm = self.dialog.yesno(
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
            self.dialog.infobox("Activating", f"Activating {config_name}...")

            # Create config.d if needed
            config_d.mkdir(parents=True, exist_ok=True)

            # Copy config
            import shutil
            dst = config_d / config_name
            shutil.copy(src, dst)

            # Restart service
            apply_config_and_restart('meshtasticd')

            self.dialog.msgbox("Success",
                f"Hardware config activated!\n\n"
                f"Config: {dst}\n\n"
                "Service restarted.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Activation failed:\n{e}")

    def _view_hardware_config(self, configs: list):
        """View details of a hardware config."""
        choices = [(cfg.name, cfg.stem[:30]) for cfg in sorted(configs)]
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "View Config",
            "Select config to view:",
            choices
        )

        if choice and choice != "back":
            config_path = Path('/etc/meshtasticd/available.d') / choice
            if config_path.exists():
                try:
                    content = config_path.read_text()[:1500]
                    self.dialog.msgbox(f"Config: {choice}", content)
                except Exception as e:
                    self.dialog.msgbox("Error", str(e))

    def _node_db_cleanup_menu(self):
        """Node database cleanup — identify and remove phantom/incomplete nodes.

        Phantom nodes appear via MQTT with incomplete data (no name, no user
        info). Clicking them in the meshtasticd web client crashes the React
        UI because it tries to render undefined properties.
        """
        while True:
            choices = [
                ("scan", "Scan for Phantom Nodes"),
                ("reset", "Reset Node Database (removes ALL nodes)"),
                ("maxnodes", "Check MaxNodes Setting"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Node DB Cleanup",
                "Clean up the meshtasticd node database.\n\n"
                "Phantom nodes (incomplete data from MQTT) can\n"
                "crash the web client when clicked.\n\n"
                "Scan identifies nodes with missing info.",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "scan": ("Scan Phantom Nodes", self._scan_phantom_nodes),
                "reset": ("Reset Node DB", self._reset_node_database),
                "maxnodes": ("Check MaxNodes", self._check_maxnodes),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _scan_phantom_nodes(self):
        """Scan for phantom/incomplete nodes via HTTP API."""
        self.dialog.infobox("Scanning", "Fetching node list from meshtasticd...")

        try:
            client = get_http_client()

            if not client.is_available:
                self.dialog.msgbox(
                    "Not Available",
                    "meshtasticd HTTP API not reachable.\n\n"
                    "Ensure meshtasticd is running:\n"
                    "  sudo systemctl start meshtasticd"
                )
                return

            nodes = client.get_nodes()

            if not nodes:
                self.dialog.msgbox("No Nodes", "No nodes found in the device database.")
                return

            # Identify phantom nodes: no long_name AND no short_name
            phantom = []
            healthy = []
            for node in nodes:
                has_name = bool(node.long_name.strip()) or bool(node.short_name.strip())
                if not has_name:
                    phantom.append(node)
                else:
                    healthy.append(node)

            if not phantom:
                self.dialog.msgbox(
                    "All Clear",
                    f"All {len(nodes)} nodes have valid names.\n\n"
                    "No phantom nodes detected.\n\n"
                    "If the web client still crashes on search,\n"
                    "this may be an upstream Meshtastic bug.\n"
                    "See: github.com/meshtastic/web/issues/862"
                )
                return

            # Build report
            lines = [
                f"Found {len(phantom)} phantom node(s) "
                f"(of {len(nodes)} total)\n",
                "Phantom nodes have no name data and can crash",
                "the web client when clicked in search results.\n",
            ]

            for node in phantom[:20]:
                node_id = node.node_id
                hw = node.hw_model or "unknown hw"
                heard = ""
                if node.last_heard > 0:
                    import time
                    age = time.time() - node.last_heard
                    if age < 3600:
                        heard = f"{age / 60:.0f}m ago"
                    elif age < 86400:
                        heard = f"{age / 3600:.0f}h ago"
                    else:
                        heard = f"{age / 86400:.0f}d ago"
                mqtt_tag = " [MQTT]" if node.via_mqtt else ""
                lines.append(f"  {node_id} ({hw}) {heard}{mqtt_tag}")

            if len(phantom) > 20:
                lines.append(f"  ... and {len(phantom) - 20} more")

            lines.append("")
            lines.append("Options:")
            lines.append("  - Remove individually (if CLI supports it)")
            lines.append("  - Reset entire node DB (re-discovers all)")
            lines.append("  - Reduce MaxNodes to limit MQTT phantoms")

            self.dialog.msgbox("Phantom Nodes Found", "\n".join(lines))

            # Offer to remove phantom nodes
            if self.dialog.yesno(
                "Remove Phantom Nodes?",
                f"Try to remove {len(phantom)} phantom node(s)?\n\n"
                "Uses 'meshtastic --remove-node' for each.\n"
                "If that command isn't available, will offer\n"
                "to reset the full node database instead.",
                default_no=True
            ):
                self._remove_phantom_nodes(phantom)

        except Exception as e:
            self.dialog.msgbox("Error", f"Scan failed:\n{e}")

    def _remove_phantom_nodes(self, phantom_nodes):
        """Try to remove phantom nodes individually via CLI."""
        cli = self._get_meshtastic_cli()
        removed = 0
        failed = 0
        cli_unsupported = False

        self.dialog.infobox(
            "Removing",
            f"Removing {len(phantom_nodes)} phantom node(s)..."
        )

        for node in phantom_nodes:
            # Extract numeric node ID (meshtastic CLI expects decimal nodeNum)
            node_id = node.node_id
            try:
                if node_id.startswith('!'):
                    node_num = str(int(node_id[1:], 16))
                else:
                    node_num = node_id
            except ValueError:
                node_num = node_id

            try:
                result = subprocess.run(
                    [cli, '--host', 'localhost:4403',
                     '--remove-node', node_num],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    removed += 1
                else:
                    stderr = result.stderr or ""
                    if "unrecognized" in stderr.lower() or "unknown" in stderr.lower():
                        cli_unsupported = True
                        break
                    failed += 1
            except FileNotFoundError:
                self.dialog.msgbox(
                    "CLI Not Found",
                    "meshtastic CLI not installed.\n\n"
                    "Install: pip install meshtastic"
                )
                return
            except subprocess.TimeoutExpired:
                failed += 1

        if cli_unsupported:
            # --remove-node not supported in this CLI version
            if self.dialog.yesno(
                "CLI Too Old",
                "'meshtastic --remove-node' not available.\n\n"
                "Your meshtastic CLI version doesn't support\n"
                "individual node removal.\n\n"
                "Options:\n"
                "  - Upgrade: pip install --upgrade meshtastic\n"
                "  - Reset entire node DB (re-discovers all)\n\n"
                "Reset entire node database now?",
                default_no=True
            ):
                self._reset_node_database()
        elif removed > 0 or failed > 0:
            self.dialog.msgbox(
                "Cleanup Complete",
                f"Removed: {removed} phantom node(s)\n"
                f"Failed:  {failed}\n\n"
                "The device will re-discover legitimate nodes\n"
                "through normal mesh traffic."
            )
        else:
            self.dialog.msgbox("No Changes", "No nodes were removed.")

    def _reset_node_database(self):
        """Reset the entire node database (nuclear option)."""
        confirm = self.dialog.yesno(
            "Reset Node Database",
            "This will CLEAR ALL known nodes from the device.\n\n"
            "The device will re-discover nodes through\n"
            "normal mesh traffic (may take minutes to hours).\n\n"
            "This fixes web client crashes caused by phantom\n"
            "nodes with incomplete data.\n\n"
            "Proceed?",
            default_no=True
        )

        if not confirm:
            return

        cli = self._get_meshtastic_cli()
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost:4403', '--reset-nodedb'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                self.dialog.msgbox(
                    "Database Reset",
                    "Node database cleared.\n\n"
                    "Nodes will re-appear as they are heard\n"
                    "over the mesh (a few minutes for nearby nodes)."
                )
            else:
                self.dialog.msgbox(
                    "Reset Failed",
                    f"Command failed:\n{result.stderr or result.stdout}"
                )
        except FileNotFoundError:
            self.dialog.msgbox("Error", "meshtastic CLI not found.")
        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Command timed out.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Reset failed:\n{e}")

    def _check_maxnodes(self):
        """Check and optionally reduce MaxNodes in config.yaml."""
        config_path = Path('/etc/meshtasticd/config.yaml')

        if not config_path.exists():
            self.dialog.msgbox(
                "Config Not Found",
                f"{config_path} not found.\n\n"
                "meshtasticd may not be installed."
            )
            return

        try:
            content = config_path.read_text()
        except OSError as e:
            self.dialog.msgbox("Error", f"Cannot read config:\n{e}")
            return

        # Find current MaxNodes value (check overlay first, then config.yaml)
        overlay = self._read_overlay()
        overlay_maxnodes = overlay.get('General', {}).get('MaxNodes')

        match = re.search(r'MaxNodes:\s*(\d+)', content)
        base_value = int(match.group(1)) if match else None
        current = overlay_maxnodes if overlay_maxnodes is not None else base_value

        if current is None:
            self.dialog.msgbox(
                "MaxNodes Not Set",
                "MaxNodes is not configured in config.yaml.\n\n"
                "Default is typically 200 (device dependent).\n\n"
                "Add to General section:\n"
                "  General:\n"
                "    MaxNodes: 100"
            )
            return

        source = "overlay" if overlay_maxnodes is not None else "config.yaml"
        text = (
            f"Current MaxNodes: {current} (from {source})\n\n"
            "MaxNodes limits how many nodes the device tracks.\n"
            "High values accumulate phantom MQTT nodes that\n"
            "can crash the web client.\n\n"
            "Recommended values:\n"
            "  50  — Small local mesh\n"
            "  100 — Medium mesh with MQTT\n"
            "  200 — Large mesh (default)\n\n"
        )

        if current > 100:
            text += (
                f"Your value ({current}) is high. Reducing to 100\n"
                "limits phantom node accumulation."
            )

        new_val = self.dialog.inputbox(
            "MaxNodes Setting",
            text + "\n\nEnter new MaxNodes value (or Cancel to keep):",
            str(current)
        )

        if new_val is None:
            return

        try:
            new_int = int(new_val)
            if new_int < 10 or new_int > 500:
                self.dialog.msgbox("Invalid", "MaxNodes must be between 10 and 500.")
                return
        except ValueError:
            self.dialog.msgbox("Invalid", "Enter a number between 10 and 500.")
            return

        if new_int == current:
            self.dialog.msgbox("No Change", f"MaxNodes remains at {current}.")
            return

        # Write override to config.d/ overlay (never modify config.yaml)
        overlay = self._read_overlay()
        if 'General' not in overlay:
            overlay['General'] = {}
        overlay['General']['MaxNodes'] = new_int

        if not self._write_overlay(overlay):
            return

        if self.dialog.yesno(
            "Restart Service?",
            f"MaxNodes override: {current} → {new_int}\n\n"
            f"Saved to: {OVERLAY_PATH}\n"
            "(config.yaml unchanged)\n\n"
            "Restart meshtasticd to apply?",
            default_no=False
        ):
            self._restart_meshtasticd()
        else:
            self.dialog.msgbox(
                "Config Updated",
                f"MaxNodes set to {new_int}.\n\n"
                f"Overlay: {OVERLAY_PATH}\n"
                "(config.yaml unchanged)\n\n"
                "Restart meshtasticd to apply:\n"
                "  sudo systemctl restart meshtasticd"
            )

    # ------------------------------------------------------------------
    # LoRa Module Configuration (writes to config.d/ overlay only)
    # ------------------------------------------------------------------

    def _lora_module_menu(self):
        """Configure LoRa module type and SPI/GPIO settings.

        All changes are saved to config.d/meshforge-overrides.yaml.
        The package-provided config.yaml is NEVER modified.
        """
        while True:
            # Read current effective settings
            overlay = self._read_overlay()
            lora_overlay = overlay.get('Lora', {})

            # Also read config.yaml for display (read-only)
            config_yaml = Path('/etc/meshtasticd/config.yaml')
            base_lora = {}
            if config_yaml.exists():
                try:
                    base = yaml.safe_load(config_yaml.read_text()) or {}
                    base_lora = base.get('Lora', {})
                except Exception as e:
                    logger.debug("Failed to read config.yaml for display: %s", e)

            # Effective = base merged with overlay
            effective = {**base_lora, **lora_overlay}
            current_module = effective.get('Module', 'auto')

            status_lines = (
                f"Current Module: {current_module}\n"
                f"CS: {effective.get('CS', '-')}  "
                f"IRQ: {effective.get('IRQ', '-')}  "
                f"Busy: {effective.get('Busy', '-')}  "
                f"Reset: {effective.get('Reset', '-')}\n"
                f"DIO2 RF Switch: {effective.get('DIO2_AS_RF_SWITCH', '-')}  "
                f"DIO3 TCXO: {effective.get('DIO3_TCXO_VOLTAGE', '-')}\n"
            )

            choices = [
                ("module", "Set Module Type"),
                ("pins", "Set GPIO Pins (CS, IRQ, Busy, Reset)"),
                ("dio", "DIO2/DIO3 Settings"),
                ("spi", "SPI Device & Speed"),
                ("txrx", "TX/RX Enable Pins (PA/LNA)"),
                ("gpiochip", "GPIO Chip (Pi 5)"),
                ("preset", "Apply Hardware Preset"),
                ("view", "View Current Overlay"),
                ("clear", "Clear LoRa Overlay"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "LoRa Module Config",
                f"{status_lines}\n"
                "Settings saved to config.d/ overlay.\n"
                "config.yaml is never modified.",
                choices
            )

            if choice is None or choice == "back":
                break
            elif choice == "module":
                self._lora_set_module()
            elif choice == "pins":
                self._lora_set_pins()
            elif choice == "dio":
                self._lora_set_dio()
            elif choice == "spi":
                self._lora_set_spi()
            elif choice == "txrx":
                self._lora_set_txrx()
            elif choice == "gpiochip":
                self._lora_set_gpiochip()
            elif choice == "preset":
                self._lora_apply_preset()
            elif choice == "view":
                self._lora_view_overlay()
            elif choice == "clear":
                self._lora_clear_overlay()

    def _lora_set_module(self):
        """Select LoRa module type."""
        choices = [
            (mod, desc) for mod, desc in self.LORA_MODULES.items()
        ]

        choice = self.dialog.menu(
            "LoRa Module Type",
            "Select the LoRa radio chip type.\n\n"
            "Match your hardware — SPI radios require\n"
            "the correct module type to bind.",
            choices
        )

        if choice is None:
            return

        overlay = self._read_overlay()
        if 'Lora' not in overlay:
            overlay['Lora'] = {}
        overlay['Lora']['Module'] = choice

        if self._write_overlay(overlay):
            self._offer_restart(f"Module set to: {choice}")

    def _lora_set_pins(self):
        """Configure GPIO pins for SPI LoRa radio."""
        overlay = self._read_overlay()
        lora = overlay.get('Lora', {})

        cs = self.dialog.inputbox(
            "Chip Select (CS)", "GPIO pin for Chip Select:",
            str(lora.get('CS', '21'))
        )
        if cs is None:
            return

        irq = self.dialog.inputbox(
            "IRQ (DIO1)", "GPIO pin for Interrupt Request:",
            str(lora.get('IRQ', '16'))
        )
        if irq is None:
            return

        busy = self.dialog.inputbox(
            "Busy", "GPIO pin for Busy signal\n(leave empty for RF95):",
            str(lora.get('Busy', '20'))
        )
        if busy is None:
            return

        reset = self.dialog.inputbox(
            "Reset", "GPIO pin for Reset:",
            str(lora.get('Reset', '18'))
        )
        if reset is None:
            return

        if 'Lora' not in overlay:
            overlay['Lora'] = {}

        try:
            overlay['Lora']['CS'] = int(cs)
            overlay['Lora']['IRQ'] = int(irq)
            if busy and busy.strip():
                overlay['Lora']['Busy'] = int(busy)
            overlay['Lora']['Reset'] = int(reset)
        except ValueError:
            self.dialog.msgbox("Error", "GPIO pins must be integers.")
            return

        if self._write_overlay(overlay):
            self._offer_restart("GPIO pins updated")

    def _lora_set_dio(self):
        """Configure DIO2 RF switch and DIO3 TCXO voltage."""
        overlay = self._read_overlay()
        lora = overlay.get('Lora', {})

        dio2 = self.dialog.yesno(
            "DIO2 as RF Switch",
            "Enable DIO2 as RF switch?\n\n"
            "Required for Ebyte E22 and some SX1262 modules.",
            default_no=not lora.get('DIO2_AS_RF_SWITCH', False)
        )

        choices = [
            ("false", "Disabled"),
            ("true", "Enabled (auto voltage)"),
            ("1.6", "1.6V"),
            ("1.7", "1.7V"),
            ("1.8", "1.8V (common)"),
            ("2.2", "2.2V"),
            ("2.4", "2.4V"),
            ("2.7", "2.7V"),
            ("3.0", "3.0V"),
            ("3.3", "3.3V"),
        ]

        tcxo = self.dialog.menu(
            "DIO3 TCXO Voltage",
            "Set DIO3 TCXO voltage.\n\n"
            "Required for Waveshare SX1262 and\n"
            "modules with a TCXO oscillator.",
            choices
        )

        if 'Lora' not in overlay:
            overlay['Lora'] = {}

        overlay['Lora']['DIO2_AS_RF_SWITCH'] = bool(dio2)

        if tcxo and tcxo != "false":
            if tcxo == "true":
                overlay['Lora']['DIO3_TCXO_VOLTAGE'] = True
            else:
                try:
                    overlay['Lora']['DIO3_TCXO_VOLTAGE'] = float(tcxo)
                except ValueError:
                    overlay['Lora']['DIO3_TCXO_VOLTAGE'] = True
        else:
            overlay['Lora'].pop('DIO3_TCXO_VOLTAGE', None)

        if self._write_overlay(overlay):
            self._offer_restart("DIO settings updated")

    def _lora_set_spi(self):
        """Configure SPI device and speed."""
        overlay = self._read_overlay()
        lora = overlay.get('Lora', {})

        spidev = self.dialog.inputbox(
            "SPI Device",
            "SPI device path:\n\n"
            "  spidev0.0 — Raspberry Pi native SPI\n"
            "  ch341     — CH341 USB-to-SPI bridge",
            str(lora.get('spidev', 'spidev0.0'))
        )
        if spidev is None:
            return

        # Validate spidev name
        if not re.match(r'^(spidev\d+\.\d+|ch341)$', spidev):
            self.dialog.msgbox(
                "Invalid",
                f"Invalid SPI device: {spidev}\n\n"
                "Expected: spidev0.0, spidev0.1, ch341"
            )
            return

        speed = self.dialog.inputbox(
            "SPI Speed",
            "SPI bus speed in Hz (default: 2000000):",
            str(lora.get('spiSpeed', '2000000'))
        )

        if 'Lora' not in overlay:
            overlay['Lora'] = {}
        overlay['Lora']['spidev'] = spidev

        if speed and speed.strip():
            try:
                overlay['Lora']['spiSpeed'] = int(speed)
            except ValueError:
                self.dialog.msgbox("Warning", f"Invalid SPI speed '{speed}' — using default.")

        if self._write_overlay(overlay):
            self._offer_restart("SPI settings updated")

    def _lora_set_txrx(self):
        """Configure TX/RX enable pins for external PA/LNA."""
        overlay = self._read_overlay()
        lora = overlay.get('Lora', {})

        txen = self.dialog.inputbox(
            "TX Enable Pin",
            "GPIO pin for TX enable (PA control).\n"
            "Leave empty if not used:",
            str(lora.get('TXen', ''))
        )

        rxen = self.dialog.inputbox(
            "RX Enable Pin",
            "GPIO pin for RX enable (LNA control).\n"
            "Leave empty if not used:",
            str(lora.get('RXen', ''))
        )

        if 'Lora' not in overlay:
            overlay['Lora'] = {}

        if txen and txen.strip():
            try:
                overlay['Lora']['TXen'] = int(txen)
            except ValueError:
                pass
        else:
            overlay['Lora'].pop('TXen', None)

        if rxen and rxen.strip():
            try:
                overlay['Lora']['RXen'] = int(rxen)
            except ValueError:
                pass
        else:
            overlay['Lora'].pop('RXen', None)

        if self._write_overlay(overlay):
            self._offer_restart("TX/RX pins updated")

    def _lora_set_gpiochip(self):
        """Set GPIO chip number (Raspberry Pi 5 uses gpiochip4)."""
        overlay = self._read_overlay()
        lora = overlay.get('Lora', {})

        chip = self.dialog.inputbox(
            "GPIO Chip",
            "GPIO chip number:\n\n"
            "  0 — Raspberry Pi 4 and earlier\n"
            "  4 — Raspberry Pi 5 (GPIO header)",
            str(lora.get('gpiochip', '0'))
        )

        if chip is None:
            return

        if 'Lora' not in overlay:
            overlay['Lora'] = {}

        try:
            overlay['Lora']['gpiochip'] = int(chip)
        except ValueError:
            self.dialog.msgbox("Error", "GPIO chip must be an integer.")
            return

        if self._write_overlay(overlay):
            self._offer_restart("GPIO chip updated")

    def _lora_apply_preset(self):
        """Apply a known hardware preset for common LoRa boards."""
        presets = {
            "meshadv-mini": {
                "desc": "MeshAdv-Mini (SX1262, 22dBm)",
                "config": {
                    "Module": "sx1262", "CS": 8, "IRQ": 16,
                    "Busy": 20, "Reset": 24,
                    "DIO2_AS_RF_SWITCH": True,
                    "DIO3_TCXO_VOLTAGE": True,
                },
            },
            "meshadv-pi-hat": {
                "desc": "MeshAdv-Pi HAT (SX1262, 1W, PA/LNA)",
                "config": {
                    "Module": "sx1262", "CS": 21, "IRQ": 16,
                    "Busy": 20, "Reset": 18, "RXen": 12, "TXen": 13,
                    "DIO2_AS_RF_SWITCH": True,
                    "DIO3_TCXO_VOLTAGE": True,
                },
            },
            "waveshare-sx1262": {
                "desc": "Waveshare SX1262 HAT",
                "config": {
                    "Module": "sx1262", "CS": 21, "IRQ": 16,
                    "Busy": 20, "Reset": 18,
                    "DIO3_TCXO_VOLTAGE": 1.8,
                },
            },
            "elecrow-rfm95": {
                "desc": "Elecrow RFM95 (SX1276, no Busy pin)",
                "config": {
                    "Module": "RF95", "CS": 7, "IRQ": 25, "Reset": 22,
                },
            },
            "ebyte-e22-900m30s": {
                "desc": "Ebyte E22-900M30S (SX1262, 1W, 915MHz)",
                "config": {
                    "Module": "sx1262", "CS": 21, "IRQ": 16,
                    "Busy": 20, "Reset": 18,
                    "DIO2_AS_RF_SWITCH": True,
                    "DIO3_TCXO_VOLTAGE": 1.8,
                },
            },
            "meshtoad-spi": {
                "desc": "MeshToad SPI (CH341 USB-SPI bridge)",
                "config": {
                    "Module": "sx1262", "spidev": "ch341",
                    "CS": 0, "IRQ": 6, "Busy": 4, "Reset": 2,
                    "DIO2_AS_RF_SWITCH": True,
                    "DIO3_TCXO_VOLTAGE": True,
                },
            },
        }

        choices = [(key, p["desc"]) for key, p in presets.items()]

        choice = self.dialog.menu(
            "Hardware Preset",
            "Select a hardware preset.\n\n"
            "This sets Module, GPIO pins, and DIO\n"
            "options for known hardware configurations.",
            choices
        )

        if choice is None or choice not in presets:
            return

        preset = presets[choice]
        detail = "\n".join(
            f"  {k}: {v}" for k, v in preset["config"].items()
        )

        if not self.dialog.yesno(
            "Apply Preset",
            f"Apply preset: {preset['desc']}\n\n{detail}\n\n"
            f"This writes to:\n  {OVERLAY_PATH}\n"
            "(config.yaml is not modified)",
            default_no=True
        ):
            return

        overlay = self._read_overlay()
        overlay['Lora'] = preset["config"]

        if self._write_overlay(overlay):
            self._offer_restart(f"Preset applied: {choice}")

    def _lora_view_overlay(self):
        """Show current meshforge-overrides.yaml content."""
        if OVERLAY_PATH.exists():
            try:
                content = OVERLAY_PATH.read_text()
            except Exception as e:
                content = f"Error reading overlay: {e}"
        else:
            content = "(No overlay file — using defaults from config.yaml)"

        self.dialog.msgbox(f"Overlay: {OVERLAY_PATH}", content)

    def _lora_clear_overlay(self):
        """Remove LoRa section from the overlay file."""
        overlay = self._read_overlay()
        if 'Lora' not in overlay:
            self.dialog.msgbox("Info", "No LoRa overrides to clear.")
            return

        if not self.dialog.yesno(
            "Clear LoRa Overlay",
            "Remove all LoRa overrides?\n\n"
            "meshtasticd will use config.yaml defaults\n"
            "and any active hardware template in config.d/.",
            default_no=True
        ):
            return

        del overlay['Lora']

        if overlay:
            if not self._write_overlay(overlay):
                return
        else:
            # No remaining overrides — remove the file
            try:
                OVERLAY_PATH.unlink()
            except Exception as e:
                self.dialog.msgbox("Error", f"Failed to remove overlay:\n{e}")
                return

        self._offer_restart("LoRa overlay cleared")

    def _offer_restart(self, message: str):
        """Offer to restart meshtasticd after a config change."""
        if self.dialog.yesno(
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

        choice = self.dialog.menu(
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
            # Try to auto-create meshtasticd config structure
            if '/etc/meshtasticd/' in path:
                try:
                    from core.meshtasticd_config import MeshtasticdConfig
                    config_mgr = MeshtasticdConfig()
                    config_mgr.ensure_structure()
                except Exception as e:
                    logger.debug("Auto-create config failed: %s", e)
            if not Path(path).exists():
                self.dialog.msgbox("Error", f"File not found:\n{path}")
                return

        # Clear screen and run nano
        clear_screen()
        subprocess.run(['nano', path])  # Interactive editor - no timeout

        # Ask to restart service
        if self.dialog.yesno(
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
            self.dialog.msgbox("Error", f"Directory not found:\n{config_d}")
            return

        configs = list(config_d.glob('*.yaml'))
        if not configs:
            self.dialog.msgbox("Info", "No active configs in config.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.dialog.menu(
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
            self.dialog.msgbox("Error", f"Directory not found:\n{available_d}")
            return

        configs = list(available_d.glob('*.yaml'))
        if not configs:
            self.dialog.msgbox("Info", "No templates in available.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.dialog.menu(
            "Hardware Templates",
            "Select template to view (Cancel to go back):",
            choices
        )

        if choice:
            self._edit_file(choice)

    def _restart_meshtasticd(self):
        """Restart meshtasticd service and re-apply saved device settings."""
        confirm = self.dialog.yesno(
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
            self.dialog.infobox("Restarting", "Restarting meshtasticd...")

            success, msg = apply_config_and_restart('meshtasticd')
            if not success:
                self.dialog.msgbox("Error", f"Restart failed:\n{msg}")
                return

            # Check for saved device settings to re-apply
            from utils.device_config_store import load_device_config, apply_saved_config
            saved = load_device_config()

            if not saved:
                self.dialog.msgbox("Success", f"meshtasticd restarted.\n\n{msg}")
                return

            # Summarize what will be re-applied
            sections = []
            for section, values in saved.items():
                items = [f"  {k}: {v}" for k, v in values.items()]
                sections.append(f"{section}:\n" + "\n".join(items))
            summary = "\n".join(sections)

            reapply = self.dialog.yesno(
                "Re-apply Settings?",
                f"meshtasticd restarted.\n\n"
                f"Saved device settings found:\n{summary}\n\n"
                "Re-apply these settings now?\n"
                "(Device config may have reverted to defaults)",
                default_no=False
            )

            if not reapply:
                self.dialog.msgbox("Info",
                    "Settings NOT re-applied.\n\n"
                    "You can re-apply manually via the\n"
                    "Radio Presets or Owner Name menus.")
                return

            self.dialog.infobox("Applying", "Re-applying saved device settings...")

            cli = get_cli()
            all_ok, results = apply_saved_config(cli)

            if all_ok:
                self.dialog.msgbox("Success",
                    "meshtasticd restarted and settings restored!\n\n"
                    f"{results}")
            else:
                self.dialog.msgbox("Partial Success",
                    "Some settings could not be restored:\n\n"
                    f"{results}\n\n"
                    "Check the web UI at :9443 to verify.")

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Restart timed out")
        except Exception as e:
            self.dialog.msgbox("Error", f"Restart failed:\n{e}")

    def _mqtt_device_config(self):
        """Configure MQTT uplink/downlink for the Meshtastic radio.

        This configures the radio to send/receive messages via an MQTT broker,
        enabling integration with the Meshtastic MQTT network.
        """
        while True:
            choices = [
                ("view", "View Current Settings"),
                ("enable", "Enable MQTT Uplink"),
                ("disable", "Disable MQTT"),
                ("broker", "Set Broker Address"),
                ("credentials", "Set Username/Password"),
                ("topic", "Set Root Topic"),
                ("encryption", "Encryption Key (PKC)"),
                ("uplink", "Configure Uplink Channels"),
                ("downlink", "Configure Downlink Channels"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "MQTT Device Config",
                "Configure radio MQTT uplink/downlink:\n\n"
                "This sends mesh traffic to an MQTT broker.",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "view": ("MQTT View Settings", self._mqtt_view_settings),
                "enable": ("Enable MQTT", lambda: self._mqtt_set_enabled(True)),
                "disable": ("Disable MQTT", lambda: self._mqtt_set_enabled(False)),
                "broker": ("Set MQTT Broker", self._mqtt_set_broker),
                "credentials": ("Set MQTT Credentials", self._mqtt_set_credentials),
                "topic": ("Set MQTT Topic", self._mqtt_set_topic),
                "encryption": ("Set MQTT Encryption", self._mqtt_set_encryption),
                "uplink": ("Configure Uplink", self._mqtt_configure_uplink),
                "downlink": ("Configure Downlink", self._mqtt_configure_downlink),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _mqtt_view_settings(self):
        """View current MQTT settings."""
        clear_screen()
        print("=== MQTT Settings ===\n")
        cli = self._get_meshtastic_cli()
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--get', 'mqtt'],
                timeout=15
            )
            if result.returncode != 0:
                print("\nFailed to get MQTT settings.")
                print("Is meshtasticd running?")
        except FileNotFoundError:
            print("meshtastic CLI not found. Install via Radio Tools menu.")
        except subprocess.TimeoutExpired:
            print("\nCommand timed out.")
        except KeyboardInterrupt:
            print("\nAborted.")
        self._wait_for_enter()

    def _mqtt_set_enabled(self, enabled: bool):
        """Enable or disable MQTT."""
        action = "enable" if enabled else "disable"
        if not self.dialog.yesno(
            f"{'Enable' if enabled else 'Disable'} MQTT",
            f"{'Enable' if enabled else 'Disable'} MQTT uplink/downlink?\n\n"
            f"{'This will start sending mesh traffic to the MQTT broker.' if enabled else 'This will stop MQTT traffic.'}"
        ):
            return

        cli = self._get_meshtastic_cli()
        clear_screen()
        print(f"=== {'Enabling' if enabled else 'Disabling'} MQTT ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.enabled', str(enabled).lower()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT {'enabled' if enabled else 'disabled'} successfully.")
                from utils.device_config_store import save_device_setting
                save_device_setting('mqtt', 'enabled', enabled)
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self._wait_for_enter()

    def _mqtt_set_broker(self):
        """Set MQTT broker address."""
        # Default to active broker profile's host if available
        default_broker = "mqtt.meshtastic.org"
        active = get_active_profile()
        if active:
            default_broker = active.host

        broker = self.dialog.inputbox(
            "MQTT Broker",
            "Enter MQTT broker address:\n\n"
            "Examples:\n"
            "  localhost (private broker on this machine)\n"
            "  192.168.1.100 (private broker on LAN)\n"
            "  mqtt.meshtastic.org (public)",
            init=default_broker
        )

        if not broker:
            return

        cli = self._get_meshtastic_cli()
        clear_screen()
        print("=== Setting MQTT Broker ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.address', broker.strip()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT broker set to: {broker}")
                from utils.device_config_store import save_device_setting
                save_device_setting('mqtt', 'address', broker.strip())
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self._wait_for_enter()

    def _mqtt_set_credentials(self):
        """Set MQTT username and password."""
        username = self.dialog.inputbox(
            "MQTT Username",
            "Enter MQTT username (blank for anonymous):",
            init=""
        )

        if username is None:
            return

        password = self.dialog.inputbox(
            "MQTT Password",
            "Enter MQTT password (blank for none):",
            init=""
        )

        if password is None:
            return

        cli = self._get_meshtastic_cli()
        clear_screen()
        print("=== Setting MQTT Credentials ===\n")
        try:
            cmd = [cli, '--host', 'localhost']
            if username:
                cmd.extend(['--set', 'mqtt.username', username])
            if password:
                cmd.extend(['--set', 'mqtt.password', password])

            if len(cmd) > 3:  # Has settings to apply
                result = subprocess.run(cmd, timeout=15)
                if result.returncode == 0:
                    print("\nMQTT credentials updated.")
                    from utils.device_config_store import save_device_settings
                    cred_data = {}
                    if username:
                        cred_data['username'] = username
                    if password:
                        cred_data['password'] = password
                    if cred_data:
                        save_device_settings({'mqtt': cred_data})
                else:
                    print("\nCommand failed.")
            else:
                print("No credentials to set.")
        except Exception as e:
            print(f"\nError: {e}")
        self._wait_for_enter()

    def _mqtt_set_topic(self):
        """Set MQTT root topic."""
        topic = self.dialog.inputbox(
            "MQTT Root Topic",
            "Enter MQTT root topic:\n\n"
            "Default: msh\n"
            "Full topic pattern: {root}/{region}/2/e/{channel}/...",
            init="msh"
        )

        if not topic:
            return

        cli = self._get_meshtastic_cli()
        clear_screen()
        print("=== Setting MQTT Topic ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.root', topic.strip()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT root topic set to: {topic}")
                from utils.device_config_store import save_device_setting
                save_device_setting('mqtt', 'root_topic', topic.strip())
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self._wait_for_enter()

    def _mqtt_set_encryption(self):
        """Configure MQTT encryption key (Public Key Cryptography)."""
        self.dialog.msgbox(
            "MQTT Encryption",
            "MQTT Encryption Options:\n\n"
            "1. JSON mode (default): Messages sent as plaintext JSON\n"
            "2. PKC mode: Messages encrypted with channel key\n\n"
            "PKC mode requires:\n"
            "  - encryption_enabled = true\n"
            "  - A valid channel PSK\n\n"
            "Configure encryption via:\n"
            "  --set mqtt.encryption_enabled true\n"
            "  --set mqtt.json_enabled false"
        )

        choices = [
            ("json", "JSON Mode (plaintext, human-readable)"),
            ("encrypted", "Encrypted Mode (PKC, secure)"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "MQTT Encryption Mode",
            "Select MQTT message format:",
            choices
        )

        if choice is None or choice == "back":
            return

        cli = self._get_meshtastic_cli()
        clear_screen()

        if choice == "json":
            print("=== Setting JSON Mode ===\n")
            try:
                subprocess.run(
                    [cli, '--host', 'localhost',
                     '--set', 'mqtt.json_enabled', 'true',
                     '--set', 'mqtt.encryption_enabled', 'false'],
                    timeout=15
                )
                print("\nMQTT set to JSON mode (plaintext).")
            except Exception as e:
                print(f"\nError: {e}")
        else:
            print("=== Setting Encrypted Mode ===\n")
            try:
                subprocess.run(
                    [cli, '--host', 'localhost',
                     '--set', 'mqtt.json_enabled', 'false',
                     '--set', 'mqtt.encryption_enabled', 'true'],
                    timeout=15
                )
                print("\nMQTT set to encrypted mode (PKC).")
                print("Messages will be encrypted with channel PSK.")
            except Exception as e:
                print(f"\nError: {e}")

        self._wait_for_enter()

    def _mqtt_configure_uplink(self):
        """Configure which channels uplink to MQTT."""
        self.dialog.msgbox(
            "MQTT Uplink",
            "MQTT Uplink sends local mesh messages to the broker.\n\n"
            "Per-channel uplink is configured via:\n"
            "  Channel Config → Edit Channel → Uplink Enabled\n\n"
            "Or via CLI:\n"
            "  meshtastic --ch-index 0 --ch-set uplink_enabled true"
        )

        # Offer to enable uplink on primary channel
        if self.dialog.yesno(
            "Enable Primary Uplink",
            "Enable MQTT uplink on primary channel (index 0)?",
            default_no=True
        ):
            cli = self._get_meshtastic_cli()
            clear_screen()
            print("=== Enabling Uplink ===\n")
            try:
                subprocess.run(
                    [cli, '--host', 'localhost',
                     '--ch-index', '0', '--ch-set', 'uplink_enabled', 'true'],
                    timeout=15
                )
                print("\nUplink enabled on primary channel.")
            except Exception as e:
                print(f"\nError: {e}")
            self._wait_for_enter()

    def _mqtt_configure_downlink(self):
        """Configure which channels downlink from MQTT."""
        self.dialog.msgbox(
            "MQTT Downlink",
            "MQTT Downlink receives broker messages to local mesh.\n\n"
            "Per-channel downlink is configured via:\n"
            "  Channel Config → Edit Channel → Downlink Enabled\n\n"
            "Or via CLI:\n"
            "  meshtastic --ch-index 0 --ch-set downlink_enabled true"
        )

        # Offer to enable downlink on primary channel
        if self.dialog.yesno(
            "Enable Primary Downlink",
            "Enable MQTT downlink on primary channel (index 0)?",
            default_no=True
        ):
            cli = self._get_meshtastic_cli()
            clear_screen()
            print("=== Enabling Downlink ===\n")
            try:
                subprocess.run(
                    [cli, '--host', 'localhost',
                     '--ch-index', '0', '--ch-set', 'downlink_enabled', 'true'],
                    timeout=15
                )
                print("\nDownlink enabled on primary channel.")
            except Exception as e:
                print(f"\nError: {e}")
            self._wait_for_enter()
