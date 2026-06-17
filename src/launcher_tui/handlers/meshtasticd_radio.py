"""
Meshtasticd Radio Config Sub-handler — Radio presets, hardware, owner settings.

Sub-handler registered in section "meshtasticd", dispatched from
MeshtasticdConfigHandler's unified meshtasticd menu.
"""

import logging
import sys
from pathlib import Path

from handler_protocol import BaseHandler
from handlers.meshtasticd_config import (
    _glob_yaml, _is_overrides, activate_hardware_config,
    ensure_meshtasticd_config,
)

# Direct imports for first-party modules (MF006: no safe_import for first-party)
from core.meshtastic_cli import get_cli as _get_cli

logger = logging.getLogger(__name__)


class MeshtasticdRadioHandler(BaseHandler):
    """TUI sub-handler for meshtasticd radio configuration."""

    handler_id = "meshtasticd_radio"
    menu_section = "meshtasticd"

    def menu_items(self):
        return [
            ("owner", "Set Owner/Node Name", "meshtastic"),
            ("presets", "Radio Presets (LoRa)", "meshtastic"),
            ("hardware", "Select Radio Hardware", "meshtastic"),
        ]

    def execute(self, action):
        if action == "owner":
            self._set_owner_name()
        elif action == "presets":
            self._radio_presets_menu()
        elif action == "hardware":
            self._hardware_config_menu()

    # ------------------------------------------------------------------
    # Owner name
    # ------------------------------------------------------------------

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
                saved = save_device_settings({'owner': owner_data})

                if saved:
                    self.ctx.dialog.msgbox("Success",
                        f"Owner settings updated:\n\n"
                        + "\n".join(changes_made)
                        + "\n\nSaved for restart persistence.")
                else:
                    self.ctx.dialog.msgbox("Partial Success",
                        f"Owner settings updated on device:\n\n"
                        + "\n".join(changes_made)
                        + "\n\nWARNING: Failed to save for restart "
                        "persistence. Settings will be lost on next "
                        "meshtasticd restart.")
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
            "US: 0=903.875 MHz (default), 12=903.625 (regional/custom)\n"
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
            saved = save_device_settings({
                'lora': {
                    'modem_preset': preset,
                    'channel_num': freq_slot,
                }
            })

            verify_note = " (verified)" if verified else " [UNVERIFIED — readback not confirmed]"
            persistence_note = (
                "Settings saved for restart persistence.\n"
                "Will be re-applied if meshtasticd restarts."
                if saved else
                "WARNING: Failed to save for restart persistence.\n"
                "Settings will be lost on next meshtasticd restart."
            )
            # The preset call already succeeded (we'd have returned earlier
            # otherwise). "Success" also requires the readback to confirm
            # (verified) AND the channel slot + persistence save — an
            # unverified apply is Partial, not a clean Success (#74-#77).
            fully_ok = slot_result.success and saved and verified
            self.ctx.dialog.msgbox(
                "Success" if fully_ok else "Partial Success",
                f"{preset} preset applied!{verify_note}\n\n"
                f"Modem preset: {preset}{slot_msg}\n\n"
                f"{persistence_note}",
            )
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

    def _get_template_description(self, config_path: Path) -> str:
        """Get human-readable description for a hardware template.

        Resolution order:
        1. RADIO_TEMPLATES dict (covers MeshForge-bundled templates)
        2. First comment line from the YAML file (covers upstream templates)
        3. Filename stem as fallback
        """
        try:
            from core.meshtasticd_config import RADIO_TEMPLATES
            template = RADIO_TEMPLATES.get(config_path.stem, {})
            if template and template.get("description"):
                return template["description"]
        except ImportError:
            pass
        # Fallback: read first comment line from YAML
        try:
            with open(config_path, 'r') as f:
                first_line = f.readline().strip()
                if first_line.startswith("#"):
                    return first_line[1:].strip()[:60]
        except (OSError, UnicodeDecodeError):
            pass
        return config_path.stem

    def _offer_create_templates(self, available_dir) -> bool:
        """Offer to generate the missing meshtasticd hardware templates in-app.

        Returns True if templates now exist (caller should proceed/re-check),
        False otherwise. Creating files under /etc needs root, so the action is
        Admin-gated; the remediation surface handles the Viewer/Admin boundary
        (it tells the operator to relaunch with sudo rather than escaping to a
        shell). In-Domain Principle — no shell instruction for config bootstrap.
        """
        from remediation import RemediationAction, propose_remediation

        def _create():
            from core.meshtasticd_config import MeshtasticdConfig
            MeshtasticdConfig().ensure_structure()
            if available_dir.exists():
                return (True, f"Hardware templates created at {available_dir}.")
            return (False, "Template creation ran but the directory is still missing.")

        result = propose_remediation(
            self.ctx,
            "Hardware Templates Missing",
            f"meshtasticd hardware templates were not found at:\n  {available_dir}",
            [RemediationAction(
                label="Create hardware templates now",
                description="generate /etc/meshtasticd/available.d templates",
                apply=_create,
                requires_admin=True,
            )],
        )
        return bool(result and result[0])

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
            if not self._offer_create_templates(available_dir):
                return

        while True:
            available = _glob_yaml(available_dir)
            if not available:
                if self._offer_create_templates(available_dir):
                    continue
                return

            active = set()
            if config_d.exists():
                active = {f.name for f in _glob_yaml(config_d)}
            active_names_set = {f.stem for f in _glob_yaml(config_d)
                               if not _is_overrides(f)} if config_d.exists() else set()

            usb_configs = []
            spi_configs = []
            for cfg in sorted(available):
                if self._classify_hardware_config(cfg) == "usb":
                    usb_configs.append(cfg)
                else:
                    spi_configs.append(cfg)

            # Map stem tags back to full filenames for activation
            stem_to_name = {}

            choices = []
            choices.append(("_usb_", f"--- USB Radios ({len(usb_configs)}) ---"))
            for cfg in usb_configs:
                status = " [ACTIVE]" if cfg.name in active else ""
                stem = cfg.stem
                # Sanitize tag: prefix with 'f:' if stem starts with '-'
                # to prevent whiptail interpreting it as a flag
                tag = f"f:{stem}" if stem.startswith("-") else stem
                stem_to_name[tag] = cfg.name
                desc = self._get_template_description(cfg)
                choices.append((tag, f"{desc}{status}"))

            choices.append(("_spi_", f"--- SPI HATs ({len(spi_configs)}) ---"))
            for cfg in spi_configs:
                status = " [ACTIVE]" if cfg.name in active else ""
                stem = cfg.stem
                tag = f"f:{stem}" if stem.startswith("-") else stem
                stem_to_name[tag] = cfg.name
                desc = self._get_template_description(cfg)
                choices.append((tag, f"{desc}{status}"))

            choices.append(("view", "View Config Details"))
            choices.append(("remove", "Remove Active Config(s)"))
            choices.append(("back", "Back"))

            active_display = ', '.join(sorted(active_names_set)) if active_names_set else 'none'

            choice = self.ctx.dialog.menu(
                "Select Radio Hardware",
                f"Total: {len(available)} templates | "
                f"Active: {active_display}\n\n"
                "Select hardware configuration to activate:",
                choices
            )

            if choice is None or choice == "back":
                break
            elif choice.startswith("_") and choice.endswith("_"):
                continue  # Section headers — just re-display menu
            elif choice == "view":
                self._view_hardware_config(available)
            elif choice == "remove":
                self._remove_active_hardware_config(config_d, active_names_set)
            else:
                # Resolve stem tag back to full filename
                config_name = stem_to_name.get(choice, f"{choice}.yaml")
                self._activate_hardware_config(config_name, available_dir, config_d)

    def _activate_hardware_config(self, config_name: str, available_dir: Path, config_d: Path):
        """Activate a hardware configuration."""
        src = available_dir / config_name

        if not src.exists():
            self.ctx.dialog.msgbox("Error", f"Config not found: {src}")
            return

        # Show which configs will be replaced
        old_configs = []
        if config_d.exists():
            old_configs = [f.name for f in _glob_yaml(config_d)
                          if not _is_overrides(f)]

        replace_msg = ""
        if old_configs:
            replace_msg = (
                f"\nReplaces: {', '.join(old_configs)}\n"
            )

        confirm = self.ctx.dialog.yesno(
            "Activate Config",
            f"Activate hardware config?\n\n"
            f"Template: {config_name}\n"
            f"{replace_msg}\n"
            "This will:\n"
            "1. Remove old hardware configs from config.d/\n"
            f"2. Copy {config_name} to {config_d}/\n"
            "3. Restart meshtasticd service",
            default_no=False
        )

        if not confirm:
            return

        try:
            self.ctx.dialog.infobox("Activating", f"Activating {config_name}...")
            ok = activate_hardware_config(
                config_name, available_dir, config_d,
            )
            if ok:
                self.ctx.dialog.msgbox("Success",
                    f"Hardware config activated!\n\n"
                    f"Config: {config_d / config_name}\n\n"
                    "Old hardware configs removed.\n"
                    "Service restarted.")
            else:
                self.ctx.dialog.msgbox(
                    "Error",
                    f"Hardware config activation failed.\n\n"
                    f"Config: {config_name}\n\n"
                    "View meshtasticd logs in-app (Logs menu) for details. "
                    "The unit may have failed to restart or the copy "
                    "to config.d/ may have been rejected.",
                )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Activation failed:\n{e}")

    def _remove_active_hardware_config(self, config_d: Path, active_names: set):
        """Remove active hardware config(s) from config.d/."""
        hw_files = sorted(
            f for f in _glob_yaml(config_d)
            if not _is_overrides(f)
        )
        if not hw_files:
            self.ctx.dialog.msgbox("Info", "No active hardware configs to remove.")
            return

        if len(hw_files) == 1:
            target = hw_files[0]
            confirm = self.ctx.dialog.yesno(
                "Remove Config",
                f"Remove active hardware config?\n\n"
                f"  {target.name}\n\n"
                "meshtasticd will not start without a hardware config.\n"
                "You can re-activate one from the Device Templates menu.",
            )
            if confirm:
                try:
                    target.unlink()
                    logger.info("Removed hardware config: %s", target.name)
                    self.ctx.dialog.msgbox(
                        "Removed",
                        f"Removed: {target.name}\n\n"
                        "meshtasticd needs a hardware config to start.\n"
                        "Select a new one from this menu when ready."
                    )
                except Exception as e:
                    self.ctx.dialog.msgbox("Error", f"Failed to remove:\n{e}")
        else:
            # Multiple active configs — let user pick which to remove
            choices = [(f.name, f.stem) for f in hw_files]
            choices.append(("all", "Remove ALL hardware configs"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "Remove Config",
                f"{len(hw_files)} active hardware configs found.\n"
                "meshtasticd merges all YAML in config.d/ — multiple\n"
                "configs can conflict (especially with Module: auto).\n\n"
                "Select config to remove:",
                choices
            )

            if not choice or choice == "back":
                return

            try:
                if choice == "all":
                    for f in hw_files:
                        f.unlink()
                        logger.info("Removed hardware config: %s", f.name)
                    self.ctx.dialog.msgbox(
                        "Removed",
                        f"Removed {len(hw_files)} hardware configs.\n\n"
                        "Select a new one from this menu when ready."
                    )
                else:
                    target = config_d / choice
                    target.unlink()
                    logger.info("Removed hardware config: %s", choice)
                    self.ctx.dialog.msgbox(
                        "Removed", f"Removed: {choice}"
                    )
            except Exception as e:
                self.ctx.dialog.msgbox("Error", f"Failed to remove:\n{e}")

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
