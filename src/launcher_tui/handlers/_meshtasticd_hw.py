"""Meshtasticd hardware, presets, edit, and restart operations.

Extracted from meshtasticd_config.py for file size compliance (CLAUDE.md #6).
Functions take the MeshtasticdConfigHandler instance for TUI interaction
via handler.ctx.

Operations in this module:
  radio_presets_menu        - LoRa preset selection UI
  apply_radio_preset        - Apply preset via meshtastic CLI
  classify_hardware_config  - Classify config as USB or SPI
  hardware_config_menu      - Hardware config selection with USB/SPI categories
  activate_hardware_config  - Copy template to config.d/ and restart
  view_hardware_config      - View details of a hardware template
  offer_restart             - Offer to restart meshtasticd after config change
  edit_config_menu          - Edit config files menu
  edit_file                 - Open file in nano editor
  edit_config_d             - Edit active hardware configs
  edit_available_d          - Edit hardware templates
  restart_meshtasticd       - Restart meshtasticd and re-apply saved settings
"""

import logging
import shutil
import subprocess
from pathlib import Path

from backend import clear_screen
from utils.service_check import apply_config_and_restart

# Direct imports for first-party modules (MF006: no safe_import for first-party)
from core.meshtastic_cli import get_cli as _get_cli

from handlers.meshtasticd_config import (
    OVERLAY_PATH,
    ensure_meshtasticd_config,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Radio presets
# ------------------------------------------------------------------

def radio_presets_menu(handler):
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

    choice = handler.ctx.dialog.menu(
        "Radio Presets",
        f"Select LoRa modem preset:{current_info}\n\n"
        "Higher speed = shorter range\n"
        "Lower speed = longer range",
        presets
    )

    if choice and choice != "back":
        apply_radio_preset(handler, choice)


def apply_radio_preset(handler, preset: str):
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

    slot_input = handler.ctx.dialog.inputbox(
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

    confirm = handler.ctx.dialog.yesno(
        "Apply Preset",
        confirm_text,
        default_no=True
    )

    if not confirm:
        return

    handler.ctx.dialog.infobox("Applying", f"Applying {preset} preset...")

    try:
        cli = _get_cli()

        result = cli.set_lora_preset(preset)
        if not result.success:
            handler.ctx.dialog.msgbox("Error",
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
        handler.ctx.dialog.msgbox("Success",
            f"{preset} preset applied!{verify_note}\n\n"
            f"Modem preset: {preset}{slot_msg}\n\n"
            "Settings saved for restart persistence.\n"
            "Will be re-applied if meshtasticd restarts.")
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to apply preset:\n{e}")


# ------------------------------------------------------------------
# Hardware config
# ------------------------------------------------------------------

def classify_hardware_config(handler, config_path: Path) -> str:
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


def hardware_config_menu(handler):
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
        handler.ctx.dialog.msgbox("Error",
            "Hardware templates not found.\n\n"
            f"Expected: {available_dir}\n\n"
            "Run with sudo to auto-create, or run the installer.")
        return

    available = list(available_dir.glob('*.yaml'))
    if not available:
        handler.ctx.dialog.msgbox("Error",
            "No hardware templates found.\n\n"
            "Run with sudo to auto-create, or run the installer.")
        return

    active = set()
    if config_d.exists():
        active = {f.name for f in config_d.glob('*.yaml')}

    usb_configs = []
    spi_configs = []
    for cfg in sorted(available):
        if classify_hardware_config(handler, cfg) == "usb":
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

    choice = handler.ctx.dialog.menu(
        "Hardware Config",
        f"Total: {len(available)} templates | "
        f"Active: {active_display}\n\n"
        "Select hardware configuration to activate:",
        choices
    )

    if choice is None or choice == "back":
        return
    elif choice in ("--usb--", "--spi--"):
        hardware_config_menu(handler)
    elif choice == "view":
        view_hardware_config(handler, available)
    else:
        activate_hardware_config(handler, choice, available_dir, config_d)


def activate_hardware_config(handler, config_name: str,
                             available_dir: Path, config_d: Path):
    """Activate a hardware configuration."""
    src = available_dir / config_name

    if not src.exists():
        handler.ctx.dialog.msgbox("Error", f"Config not found: {src}")
        return

    confirm = handler.ctx.dialog.yesno(
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
        handler.ctx.dialog.infobox("Activating", f"Activating {config_name}...")
        config_d.mkdir(parents=True, exist_ok=True)
        dst = config_d / config_name
        shutil.copy(src, dst)
        apply_config_and_restart('meshtasticd')
        handler.ctx.dialog.msgbox("Success",
            f"Hardware config activated!\n\n"
            f"Config: {dst}\n\n"
            "Service restarted.")
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Activation failed:\n{e}")


def view_hardware_config(handler, configs: list):
    """View details of a hardware config."""
    choices = [(cfg.name, cfg.stem[:30]) for cfg in sorted(configs)]
    choices.append(("back", "Back"))

    choice = handler.ctx.dialog.menu(
        "View Config",
        "Select config to view:",
        choices
    )

    if choice and choice != "back":
        config_path = Path('/etc/meshtasticd/available.d') / choice
        if config_path.exists():
            try:
                content = config_path.read_text()[:1500]
                handler.ctx.dialog.msgbox(f"Config: {choice}", content)
            except Exception as e:
                handler.ctx.dialog.msgbox("Error", str(e))


# ------------------------------------------------------------------
# Edit / restart
# ------------------------------------------------------------------

def offer_restart(handler, message: str):
    """Offer to restart meshtasticd after a config change."""
    if handler.ctx.dialog.yesno(
        "Restart Service?",
        f"{message}\n\n"
        f"Saved to: {OVERLAY_PATH}\n"
        "(config.yaml unchanged)\n\n"
        "Restart meshtasticd to apply?",
        default_no=False
    ):
        restart_meshtasticd(handler)


def edit_config_menu(handler):
    """Edit config files directly."""
    choices = [
        ("main", "Main Config (/etc/meshtasticd/config.yaml)"),
        ("active", "Active Hardware Configs"),
        ("templates", "Hardware Templates"),
        ("back", "Back"),
    ]

    choice = handler.ctx.dialog.menu(
        "Edit Config Files",
        "Edit meshtasticd configuration files:\n\n"
        "Opens in nano editor.\n"
        "Save: Ctrl+O, Exit: Ctrl+X",
        choices
    )

    if choice is None or choice == "back":
        return

    if choice == "main":
        edit_file(handler, '/etc/meshtasticd/config.yaml')
    elif choice == "active":
        edit_config_d(handler)
    elif choice == "templates":
        edit_available_d(handler)


def edit_file(handler, path: str):
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
            handler.ctx.dialog.msgbox("Error", f"File not found:\n{path}")
            return

    clear_screen()
    subprocess.run(['nano', path])  # Interactive editor - no timeout

    if handler.ctx.dialog.yesno(
        "Restart Service?",
        "Config file modified.\n\n"
        "Restart meshtasticd to apply changes?",
        default_no=False
    ):
        restart_meshtasticd(handler)


def edit_config_d(handler):
    """Edit files in config.d."""
    config_d = Path('/etc/meshtasticd/config.d')
    if not config_d.exists():
        handler.ctx.dialog.msgbox("Error", f"Directory not found:\n{config_d}")
        return

    configs = list(config_d.glob('*.yaml'))
    if not configs:
        handler.ctx.dialog.msgbox("Info", "No active configs in config.d/")
        return

    choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

    choice = handler.ctx.dialog.menu(
        "Active Configs",
        "Select config to edit (Cancel to go back):",
        choices
    )

    if choice:
        edit_file(handler, choice)


def edit_available_d(handler):
    """Edit files in available.d."""
    available_d = Path('/etc/meshtasticd/available.d')
    if not available_d.exists():
        handler.ctx.dialog.msgbox("Error", f"Directory not found:\n{available_d}")
        return

    configs = list(available_d.glob('*.yaml'))
    if not configs:
        handler.ctx.dialog.msgbox("Info", "No templates in available.d/")
        return

    choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

    choice = handler.ctx.dialog.menu(
        "Hardware Templates",
        "Select template to view (Cancel to go back):",
        choices
    )

    if choice:
        edit_file(handler, choice)


def restart_meshtasticd(handler):
    """Restart meshtasticd service and re-apply saved device settings."""
    confirm = handler.ctx.dialog.yesno(
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
        handler.ctx.dialog.infobox("Restarting", "Restarting meshtasticd...")

        success, msg = apply_config_and_restart('meshtasticd')
        if not success:
            handler.ctx.dialog.msgbox("Error", f"Restart failed:\n{msg}")
            return

        from utils.device_config_store import load_device_config, apply_saved_config
        saved = load_device_config()

        if not saved:
            handler.ctx.dialog.msgbox("Success", f"meshtasticd restarted.\n\n{msg}")
            return

        sections = []
        for section, values in saved.items():
            items = [f"  {k}: {v}" for k, v in values.items()]
            sections.append(f"{section}:\n" + "\n".join(items))
        summary = "\n".join(sections)

        reapply = handler.ctx.dialog.yesno(
            "Re-apply Settings?",
            f"meshtasticd restarted.\n\n"
            f"Saved device settings found:\n{summary}\n\n"
            "Re-apply these settings now?\n"
            "(Device config may have reverted to defaults)",
            default_no=False
        )

        if not reapply:
            handler.ctx.dialog.msgbox("Info",
                "Settings NOT re-applied.\n\n"
                "You can re-apply manually via the\n"
                "Radio Presets or Owner Name menus.")
            return

        handler.ctx.dialog.infobox("Applying", "Re-applying saved device settings...")

        cli = _get_cli()
        all_ok, results = apply_saved_config(cli)

        if all_ok:
            handler.ctx.dialog.msgbox("Success",
                "meshtasticd restarted and settings restored!\n\n"
                f"{results}")
        else:
            handler.ctx.dialog.msgbox("Partial Success",
                "Some settings could not be restored:\n\n"
                f"{results}\n\n"
                "Check the web UI at :9443 to verify.")

    except subprocess.TimeoutExpired:
        handler.ctx.dialog.msgbox("Error", "Restart timed out")
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Restart failed:\n{e}")
