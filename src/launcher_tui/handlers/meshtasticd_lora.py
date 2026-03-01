"""
Meshtasticd LoRa Module Handler — SPI/GPIO configuration for LoRa radios.

Converted from meshtasticd_config_mixin.py (lines 1047-1491) as part of
the mixin-to-registry migration (Batch 9).

Sub-handler registered in section "meshtasticd", dispatched from
MeshtasticdConfigHandler's thin dispatcher menu.
"""

import logging
import re
import yaml
from pathlib import Path

from handler_protocol import BaseHandler
from handlers.meshtasticd_config import (
    OVERLAY_PATH, read_overlay, write_overlay,
)

logger = logging.getLogger(__name__)

# LoRa module types supported by meshtasticd
LORA_MODULES = {
    "sx1262": "SX1262 (Waveshare, Ebyte E22-900M, MeshAdv, etc.)",
    "sx1268": "SX1268 (Ebyte E22-400M, etc.)",
    "sx1280": "SX1280 (2.4 GHz)",
    "RF95": "RF95/RFM95 (Elecrow, Adafruit RFM9x)",
    "sim": "Simulation mode (no radio)",
}


class MeshtasticdLoRaHandler(BaseHandler):
    """TUI handler for LoRa module SPI/GPIO configuration."""

    handler_id = "meshtasticd_lora"
    menu_section = "meshtasticd"

    def menu_items(self):
        return [
            ("lora", "LoRa Module Config", None),
        ]

    def execute(self, action):
        if action == "lora":
            self._lora_module_menu()

    def _offer_restart(self, message: str):
        """Offer to restart meshtasticd after a config change."""
        handler = self.ctx.registry.get_handler("meshtasticd_config") if self.ctx.registry else None
        if handler and hasattr(handler, '_offer_restart'):
            handler._offer_restart(message)

    def _lora_module_menu(self):
        """Configure LoRa module type and SPI/GPIO settings."""
        while True:
            overlay = read_overlay()
            lora_overlay = overlay.get('Lora', {})

            config_yaml = Path('/etc/meshtasticd/config.yaml')
            base_lora = {}
            if config_yaml.exists():
                try:
                    base = yaml.safe_load(config_yaml.read_text()) or {}
                    base_lora = base.get('Lora', {})
                except Exception as e:
                    logger.debug("Failed to read config.yaml for display: %s", e)

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

            choice = self.ctx.dialog.menu(
                "LoRa Module Config",
                f"{status_lines}\n"
                "Settings saved to config.d/ overlay.\n"
                "config.yaml is never modified.",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "module": ("Set Module", self._lora_set_module),
                "pins": ("Set GPIO Pins", self._lora_set_pins),
                "dio": ("DIO Settings", self._lora_set_dio),
                "spi": ("SPI Settings", self._lora_set_spi),
                "txrx": ("TX/RX Pins", self._lora_set_txrx),
                "gpiochip": ("GPIO Chip", self._lora_set_gpiochip),
                "preset": ("Hardware Preset", self._lora_apply_preset),
                "view": ("View Overlay", self._lora_view_overlay),
                "clear": ("Clear Overlay", self._lora_clear_overlay),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _lora_set_module(self):
        """Select LoRa module type."""
        choices = [(mod, desc) for mod, desc in LORA_MODULES.items()]

        choice = self.ctx.dialog.menu(
            "LoRa Module Type",
            "Select the LoRa radio chip type.\n\n"
            "Match your hardware — SPI radios require\n"
            "the correct module type to bind.",
            choices
        )

        if choice is None:
            return

        overlay = read_overlay()
        if 'Lora' not in overlay:
            overlay['Lora'] = {}
        overlay['Lora']['Module'] = choice

        if write_overlay(overlay, self.ctx.dialog):
            self._offer_restart(f"Module set to: {choice}")

    def _lora_set_pins(self):
        """Configure GPIO pins for SPI LoRa radio."""
        overlay = read_overlay()
        lora = overlay.get('Lora', {})

        cs = self.ctx.dialog.inputbox(
            "Chip Select (CS)", "GPIO pin for Chip Select:",
            str(lora.get('CS', '21'))
        )
        if cs is None:
            return

        irq = self.ctx.dialog.inputbox(
            "IRQ (DIO1)", "GPIO pin for Interrupt Request:",
            str(lora.get('IRQ', '16'))
        )
        if irq is None:
            return

        busy = self.ctx.dialog.inputbox(
            "Busy", "GPIO pin for Busy signal\n(leave empty for RF95):",
            str(lora.get('Busy', '20'))
        )
        if busy is None:
            return

        reset = self.ctx.dialog.inputbox(
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
            self.ctx.dialog.msgbox("Error", "GPIO pins must be integers.")
            return

        if write_overlay(overlay, self.ctx.dialog):
            self._offer_restart("GPIO pins updated")

    def _lora_set_dio(self):
        """Configure DIO2 RF switch and DIO3 TCXO voltage."""
        overlay = read_overlay()
        lora = overlay.get('Lora', {})

        dio2 = self.ctx.dialog.yesno(
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

        tcxo = self.ctx.dialog.menu(
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

        if write_overlay(overlay, self.ctx.dialog):
            self._offer_restart("DIO settings updated")

    def _lora_set_spi(self):
        """Configure SPI device and speed."""
        overlay = read_overlay()
        lora = overlay.get('Lora', {})

        spidev = self.ctx.dialog.inputbox(
            "SPI Device",
            "SPI device path:\n\n"
            "  spidev0.0 — Raspberry Pi native SPI\n"
            "  ch341     — CH341 USB-to-SPI bridge",
            str(lora.get('spidev', 'spidev0.0'))
        )
        if spidev is None:
            return

        if not re.match(r'^(spidev\d+\.\d+|ch341)$', spidev):
            self.ctx.dialog.msgbox(
                "Invalid",
                f"Invalid SPI device: {spidev}\n\n"
                "Expected: spidev0.0, spidev0.1, ch341"
            )
            return

        speed = self.ctx.dialog.inputbox(
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
                self.ctx.dialog.msgbox("Warning", f"Invalid SPI speed '{speed}' — using default.")

        if write_overlay(overlay, self.ctx.dialog):
            self._offer_restart("SPI settings updated")

    def _lora_set_txrx(self):
        """Configure TX/RX enable pins for external PA/LNA."""
        overlay = read_overlay()
        lora = overlay.get('Lora', {})

        txen = self.ctx.dialog.inputbox(
            "TX Enable Pin",
            "GPIO pin for TX enable (PA control).\n"
            "Leave empty if not used:",
            str(lora.get('TXen', ''))
        )

        rxen = self.ctx.dialog.inputbox(
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

        if write_overlay(overlay, self.ctx.dialog):
            self._offer_restart("TX/RX pins updated")

    def _lora_set_gpiochip(self):
        """Set GPIO chip number (Raspberry Pi 5 uses gpiochip4)."""
        overlay = read_overlay()
        lora = overlay.get('Lora', {})

        chip = self.ctx.dialog.inputbox(
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
            self.ctx.dialog.msgbox("Error", "GPIO chip must be an integer.")
            return

        if write_overlay(overlay, self.ctx.dialog):
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

        choice = self.ctx.dialog.menu(
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

        if not self.ctx.dialog.yesno(
            "Apply Preset",
            f"Apply preset: {preset['desc']}\n\n{detail}\n\n"
            f"This writes to:\n  {OVERLAY_PATH}\n"
            "(config.yaml is not modified)",
            default_no=True
        ):
            return

        overlay = read_overlay()
        overlay['Lora'] = preset["config"]

        if write_overlay(overlay, self.ctx.dialog):
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

        self.ctx.dialog.msgbox(f"Overlay: {OVERLAY_PATH}", content)

    def _lora_clear_overlay(self):
        """Remove LoRa section from the overlay file."""
        overlay = read_overlay()
        if 'Lora' not in overlay:
            self.ctx.dialog.msgbox("Info", "No LoRa overrides to clear.")
            return

        if not self.ctx.dialog.yesno(
            "Clear LoRa Overlay",
            "Remove all LoRa overrides?\n\n"
            "meshtasticd will use config.yaml defaults\n"
            "and any active hardware template in config.d/.",
            default_no=True
        ):
            return

        del overlay['Lora']

        if overlay:
            if not write_overlay(overlay, self.ctx.dialog):
                return
        else:
            try:
                OVERLAY_PATH.unlink()
            except Exception as e:
                self.ctx.dialog.msgbox("Error", f"Failed to remove overlay:\n{e}")
                return

        self._offer_restart("LoRa overlay cleared")
