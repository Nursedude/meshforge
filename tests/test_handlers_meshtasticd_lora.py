"""
Unit tests for MeshtasticdLoRaHandler.

Regression tests for PR #1109 (LoRa hardware menu selection fix:
default_no, descriptions, stem tags) plus general handler coverage.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context


def _make_handler():
    from handlers.meshtasticd_lora import MeshtasticdLoRaHandler
    h = MeshtasticdLoRaHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


# ── Structure ───────────────────────────────────────────────────────


class TestLoRaHandlerStructure:

    def test_handler_id(self):
        h = _make_handler()
        assert h.handler_id == "meshtasticd_lora"

    def test_menu_section(self):
        h = _make_handler()
        assert h.menu_section == "meshtasticd"

    def test_menu_items(self):
        h = _make_handler()
        items = h.menu_items()
        assert len(items) == 1
        assert items[0][0] == "lora"
        assert "LoRa Module Config" in items[0][1]


# ── Dispatch ────────────────────────────────────────────────────────


class TestLoRaHandlerDispatch:

    def test_execute_lora_dispatches(self):
        h = _make_handler()
        with patch.object(h, '_lora_module_menu') as mock:
            h.execute("lora")
            mock.assert_called_once()

    def test_execute_unknown_action(self):
        h = _make_handler()
        h.execute("nonexistent")  # Should not raise


# ── Module Menu ─────────────────────────────────────────────────────


class TestLoRaModuleMenu:

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_menu_back_exits(self, mock_read):
        mock_read.return_value = {}
        h = _make_handler()
        h.ctx.dialog._menu_returns = [None]  # Exit immediately

        with patch('pathlib.Path.exists', return_value=False):
            h._lora_module_menu()

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_menu_dispatches_module(self, mock_write, mock_read):
        mock_read.return_value = {}
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["module", "back"]
        h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)

        with patch('pathlib.Path.exists', return_value=False), \
             patch.object(h, '_lora_set_module') as mock_set:
            h._lora_module_menu()
            mock_set.assert_called_once()


# ── Set Module ──────────────────────────────────────────────────────


class TestLoRaSetModule:

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_set_module_sx1262(self, mock_write, mock_read):
        mock_read.return_value = {}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["sx1262"]

        with patch.object(h, '_offer_restart'):
            h._lora_set_module()

        call_args = mock_write.call_args[0]
        assert call_args[0]['Lora']['Module'] == "sx1262"

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_set_module_cancelled(self, mock_read):
        mock_read.return_value = {}
        h = _make_handler()
        h.ctx.dialog._menu_returns = [None]

        h._lora_set_module()
        # No write_overlay call — no changes


# ── Set Pins ────────────────────────────────────────────────────────


class TestLoRaSetPins:

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_set_pins_valid(self, mock_write, mock_read):
        mock_read.return_value = {}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["21", "16", "20", "18"]

        with patch.object(h, '_offer_restart'):
            h._lora_set_pins()

        overlay = mock_write.call_args[0][0]
        assert overlay['Lora']['CS'] == 21
        assert overlay['Lora']['IRQ'] == 16
        assert overlay['Lora']['Busy'] == 20
        assert overlay['Lora']['Reset'] == 18

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_set_pins_invalid_shows_error(self, mock_read):
        mock_read.return_value = {}
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["abc", "16", "20", "18"]

        h._lora_set_pins()
        assert h.ctx.dialog.last_msgbox_title == "Error"
        assert "integers" in h.ctx.dialog.last_msgbox_text

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_set_pins_cancelled(self, mock_read):
        mock_read.return_value = {}
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = [None]  # Cancel first field

        h._lora_set_pins()  # Should return early


# ── DIO Settings (PR #1109 regression: default_no) ─────────────────


class TestLoRaDIOSettings:

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_dio2_default_no_when_disabled(self, mock_write, mock_read):
        """PR #1109 regression: default_no should be True when DIO2 is False."""
        mock_read.return_value = {'Lora': {'DIO2_AS_RF_SWITCH': False}}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]  # DIO2 stays off
        h.ctx.dialog._menu_returns = [None]    # Cancel TCXO

        with patch.object(h, '_offer_restart'):
            h._lora_set_dio()

        # Verify yesno was called with default_no=True when DIO2 was False
        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == 'yesno']
        assert len(yesno_calls) == 1
        # default_no should be True when DIO2 is False (not lora.get = not False = True)
        assert yesno_calls[0][2].get('default_no') is True

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_dio2_default_no_when_enabled(self, mock_write, mock_read):
        """PR #1109 regression: default_no should be False when DIO2 is True."""
        mock_read.return_value = {'Lora': {'DIO2_AS_RF_SWITCH': True}}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]   # DIO2 stays on
        h.ctx.dialog._menu_returns = ["false"]  # Disable TCXO

        with patch.object(h, '_offer_restart'):
            h._lora_set_dio()

        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == 'yesno']
        assert len(yesno_calls) == 1
        # default_no should be False when DIO2 is True (not True = False)
        assert yesno_calls[0][2].get('default_no') is False

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_dio3_tcxo_voltage_float(self, mock_write, mock_read):
        mock_read.return_value = {}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]  # DIO2 on
        h.ctx.dialog._menu_returns = ["1.8"]   # TCXO 1.8V

        with patch.object(h, '_offer_restart'):
            h._lora_set_dio()

        overlay = mock_write.call_args[0][0]
        assert overlay['Lora']['DIO3_TCXO_VOLTAGE'] == 1.8

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_dio3_tcxo_disabled(self, mock_write, mock_read):
        mock_read.return_value = {'Lora': {'DIO3_TCXO_VOLTAGE': 1.8}}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]   # DIO2 off
        h.ctx.dialog._menu_returns = ["false"]   # Disable TCXO

        with patch.object(h, '_offer_restart'):
            h._lora_set_dio()

        overlay = mock_write.call_args[0][0]
        assert 'DIO3_TCXO_VOLTAGE' not in overlay['Lora']


# ── SPI Settings ────────────────────────────────────────────────────


class TestLoRaSPISettings:

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_spi_valid_device(self, mock_write, mock_read):
        mock_read.return_value = {}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["spidev0.0", "2000000"]

        with patch.object(h, '_offer_restart'):
            h._lora_set_spi()

        overlay = mock_write.call_args[0][0]
        assert overlay['Lora']['spidev'] == "spidev0.0"
        assert overlay['Lora']['spiSpeed'] == 2000000

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_spi_invalid_device(self, mock_read):
        mock_read.return_value = {}
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["invalid_device"]

        h._lora_set_spi()
        assert h.ctx.dialog.last_msgbox_title == "Invalid"

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_spi_ch341_accepted(self, mock_write, mock_read):
        mock_read.return_value = {}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["ch341", ""]

        with patch.object(h, '_offer_restart'):
            h._lora_set_spi()

        overlay = mock_write.call_args[0][0]
        assert overlay['Lora']['spidev'] == "ch341"


# ── Hardware Presets (PR #1109 regression: default_no on confirmation) ──


class TestLoRaPresets:

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_preset_apply_confirmed(self, mock_write, mock_read):
        mock_read.return_value = {}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["waveshare-sx1262"]
        h.ctx.dialog._yesno_returns = [True]

        with patch.object(h, '_offer_restart'):
            h._lora_apply_preset()

        overlay = mock_write.call_args[0][0]
        assert overlay['Lora']['Module'] == "sx1262"
        assert overlay['Lora']['CS'] == 21

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_preset_default_no_on_confirm(self, mock_read):
        """PR #1109 regression: preset confirm dialog uses default_no=True."""
        mock_read.return_value = {}
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["waveshare-sx1262"]
        h.ctx.dialog._yesno_returns = [False]  # Cancel

        h._lora_apply_preset()

        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == 'yesno']
        assert len(yesno_calls) == 1
        assert yesno_calls[0][2].get('default_no') is True

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_preset_cancelled_selection(self, mock_read):
        mock_read.return_value = {}
        h = _make_handler()
        h.ctx.dialog._menu_returns = [None]

        h._lora_apply_preset()  # Should not raise


# ── Clear Overlay (PR #1109 regression: default_no on confirmation) ──


class TestLoRaClearOverlay:

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_clear_no_lora_section(self, mock_read):
        mock_read.return_value = {'Other': {}}
        h = _make_handler()

        h._lora_clear_overlay()
        assert h.ctx.dialog.last_msgbox_title == "Info"
        assert "No LoRa overrides" in h.ctx.dialog.last_msgbox_text

    @patch('handlers.meshtasticd_lora.read_overlay')
    def test_clear_default_no_on_confirm(self, mock_read):
        """PR #1109 regression: clear confirm dialog uses default_no=True."""
        mock_read.return_value = {'Lora': {'Module': 'sx1262'}}
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]  # Cancel

        h._lora_clear_overlay()

        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == 'yesno']
        assert len(yesno_calls) == 1
        assert yesno_calls[0][2].get('default_no') is True

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_clear_confirmed(self, mock_write, mock_read):
        mock_read.return_value = {'Lora': {'Module': 'sx1262'}, 'Other': {}}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]

        with patch.object(h, '_offer_restart'):
            h._lora_clear_overlay()

        overlay = mock_write.call_args[0][0]
        assert 'Lora' not in overlay


# ── LORA_MODULES constant ──────────────────────────────────────────


class TestLoRaModulesConstant:

    def test_modules_dict_has_expected_keys(self):
        from handlers.meshtasticd_lora import LORA_MODULES
        expected = {"sx1262", "sx1268", "sx1280", "RF95", "sim"}
        assert set(LORA_MODULES.keys()) == expected

    def test_modules_descriptions_are_strings(self):
        from handlers.meshtasticd_lora import LORA_MODULES
        for key, desc in LORA_MODULES.items():
            assert isinstance(desc, str)
            assert len(desc) > 0


# ── DOC-1 audit findings (2026-11-03): Esc never writes; blank Busy clears ──

class TestLoRaEscAndBlankBusy:

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_esc_at_the_tcxo_menu_writes_nothing(self, mock_write, mock_read):
        """Esc at DIO3 used to pop DIO3_TCXO_VOLTAGE and write — a Waveshare /
        E22 board (TCXO 1.8 V) then fails radio init."""
        mock_read.return_value = {'Lora': {'DIO2_AS_RF_SWITCH': True,
                                           'DIO3_TCXO_VOLTAGE': 1.8}}
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [True]
        h.ctx.dialog._menu_returns = [None]           # Esc at the TCXO menu
        with patch.object(h, '_offer_restart') as restart:
            h._lora_set_dio()
        assert not mock_write.called and not restart.called

    @patch('handlers.meshtasticd_lora.read_overlay')
    @patch('handlers.meshtasticd_lora.write_overlay')
    def test_blank_busy_removes_the_busy_pin(self, mock_write, mock_read):
        """The prompt says "leave empty for RF95"; blank kept the OLD pin."""
        mock_read.return_value = {'Lora': {'CS': 21, 'IRQ': 16, 'Busy': 20, 'Reset': 18}}
        mock_write.return_value = True
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = ["7", "25", "", "22"]
        with patch.object(h, '_offer_restart'):
            h._lora_set_pins()
        overlay = mock_write.call_args[0][0]
        assert 'Busy' not in overlay['Lora']
        assert (overlay['Lora']['CS'], overlay['Lora']['Reset']) == (7, 22)
