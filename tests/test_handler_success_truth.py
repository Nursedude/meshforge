"""Pattern-audit Finding #7 (2026-05-19): TUI handlers must reflect the
truth about action return values.

Before this commit, several handlers in ``channel_config.py`` and
``meshtasticd_radio.py`` showed ``msgbox("Success", ...)`` unconditionally
after calling action functions that return ``CommandResult`` /
``bool``-typed success indicators. When the underlying action failed
without raising — meshtastic CLI rejected a config arg, device_config_store
file write failed, activate_hardware_config returned False — the operator
saw "Success" while the action had silently failed (UI-lies class).

These tests pin the corrected contract: a handler shows "Error" title
(or "Partial Success" where only a secondary step failed) when the
underlying action returned a non-success value.

Reference pattern from the codebase: ``broker.py:489``:

    success, msg = restart_mosquitto()
    self.ctx.dialog.msgbox("Restart" if success else "Error", msg)
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402

# Pre-import so inline `from commands import meshtastic as mesh_cmd` calls
# resolve to a module we can patch attributes on.
from commands import meshtastic as _mesh_cmd_module  # noqa: E402
from commands.base import CommandResult  # noqa: E402


def _make_channel_handler():
    from handlers.channel_config import ChannelConfigHandler
    h = ChannelConfigHandler()
    ctx = make_handler_context()
    ctx.src_dir = Path(__file__).parent.parent / "src"
    h.set_context(ctx)
    return h


def _make_radio_handler():
    from handlers.meshtasticd_radio import MeshtasticdRadioHandler
    h = MeshtasticdRadioHandler()
    ctx = make_handler_context()
    ctx.src_dir = Path(__file__).parent.parent / "src"
    h.set_context(ctx)
    return h


# ─────────────────────────────────────────────────────────────────────
# ChannelConfigHandler — 4 sites
# ─────────────────────────────────────────────────────────────────────


class TestAddChannelTruthful:
    """``_add_channel`` calls set_channel_name + set_channel_psk and must
    show 'Error' when either returns success=False (pre-fix: unconditional
    'Success')."""

    def test_failed_set_channel_name_shows_error(self):
        h = _make_channel_handler()
        h.ctx.dialog._menu_returns = ["3"]
        h.ctx.dialog._inputbox_returns = ["MyChan"]
        h.ctx.dialog._yesno_returns = [True]  # use_psk

        with patch.object(_mesh_cmd_module, 'set_channel_name',
                          return_value=CommandResult.fail("CLI rejected name")), \
             patch.object(_mesh_cmd_module, 'set_channel_psk',
                          return_value=CommandResult.ok("OK")):
            h._add_channel()

        assert h.ctx.dialog.last_msgbox_title == "Error", (
            f"got msgbox title={h.ctx.dialog.last_msgbox_title!r} — "
            "set_channel_name returned failure but handler reported Success"
        )
        assert "CLI rejected name" in (h.ctx.dialog.last_msgbox_text or "")

    def test_failed_set_channel_psk_shows_error(self):
        h = _make_channel_handler()
        h.ctx.dialog._menu_returns = ["3"]
        h.ctx.dialog._inputbox_returns = ["MyChan"]
        h.ctx.dialog._yesno_returns = [True]  # use_psk

        with patch.object(_mesh_cmd_module, 'set_channel_name',
                          return_value=CommandResult.ok("OK")), \
             patch.object(_mesh_cmd_module, 'set_channel_psk',
                          return_value=CommandResult.fail("bad psk format")):
            h._add_channel()

        assert h.ctx.dialog.last_msgbox_title == "Error"
        assert "bad psk format" in (h.ctx.dialog.last_msgbox_text or "")

    def test_both_succeed_shows_success(self):
        h = _make_channel_handler()
        h.ctx.dialog._menu_returns = ["3"]
        h.ctx.dialog._inputbox_returns = ["MyChan"]
        h.ctx.dialog._yesno_returns = [False]  # no psk

        with patch.object(_mesh_cmd_module, 'set_channel_name',
                          return_value=CommandResult.ok("OK")), \
             patch.object(_mesh_cmd_module, 'set_channel_psk',
                          return_value=CommandResult.ok("OK")):
            h._add_channel()

        assert h.ctx.dialog.last_msgbox_title == "Success"


class TestDisableChannelTruthful:
    """``_disable_channel`` calls _run_command; must show 'Error' on
    non-success result."""

    def test_failed_run_command_shows_error(self):
        h = _make_channel_handler()
        h.ctx.dialog._menu_returns = ["3"]
        h.ctx.dialog._yesno_returns = [True]  # confirm

        with patch.object(_mesh_cmd_module, '_run_command',
                          return_value=CommandResult.fail("ch-set rejected")):
            h._disable_channel()

        assert h.ctx.dialog.last_msgbox_title == "Error"
        assert "ch-set rejected" in (h.ctx.dialog.last_msgbox_text or "")

    def test_successful_disable_shows_success(self):
        h = _make_channel_handler()
        h.ctx.dialog._menu_returns = ["3"]
        h.ctx.dialog._yesno_returns = [True]

        with patch.object(_mesh_cmd_module, '_run_command',
                          return_value=CommandResult.ok("OK")):
            h._disable_channel()

        assert h.ctx.dialog.last_msgbox_title == "Success"


class TestGatewayChannelTruthful:
    """``_set_gateway_channel`` calls set_channel_name + set_channel_psk
    for slot 7; same shape as _add_channel."""

    def test_failed_gateway_setup_shows_error(self):
        h = _make_channel_handler()
        # yesno confirms the "set up gateway channel?" prompt; menu picks
        # the "default" PSK option (hardcoded AQ==).
        h.ctx.dialog._yesno_returns = [True]
        h.ctx.dialog._menu_returns = ["default"]

        with patch.object(_mesh_cmd_module, 'set_channel_name',
                          return_value=CommandResult.fail("device offline")), \
             patch.object(_mesh_cmd_module, 'set_channel_psk',
                          return_value=CommandResult.ok("OK")):
            h._set_gateway_channel()

        assert h.ctx.dialog.last_msgbox_title == "Error"
        assert "device offline" in (h.ctx.dialog.last_msgbox_text or "")


class TestApplyGatewayTemplateTruthful:
    """``_apply_gateway_template`` runs preset via subprocess (uncovered by
    this test — separate concern) plus set_channel_name; the channel-name
    failure must surface."""

    def test_failed_channel_name_shows_error(self):
        h = _make_channel_handler()
        h.ctx.dialog._yesno_returns = [True]  # confirm template
        # Stub get_meshtastic_cli to avoid CLI lookup; subprocess.run
        # patched so the preset step is a no-op success in this test.
        h.ctx.get_meshtastic_cli = lambda: "/usr/bin/true"

        with patch('subprocess.run') as mock_subproc, \
             patch.object(_mesh_cmd_module, 'set_channel_name',
                          return_value=CommandResult.fail("rejected by device")):
            mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")
            h._apply_gateway_template("standard")

        assert h.ctx.dialog.last_msgbox_title == "Error"
        assert "rejected by device" in (h.ctx.dialog.last_msgbox_text or "")


# ─────────────────────────────────────────────────────────────────────
# MeshtasticdRadioHandler — 3 sites
# ─────────────────────────────────────────────────────────────────────


class TestSetOwnerNameTruthful:
    """``_set_owner_name`` calls set_owner + set_owner_short on the device
    AND save_device_settings for local persistence. The device call is
    already success-checked (pre-existing); the local persistence call
    was previously ignored.

    Operator semantic: device-side success + persistence failure is a
    real partial state — the device IS updated, but settings will be
    lost on next meshtasticd restart. 'Partial Success' is the truthful
    label."""

    def test_failed_local_persistence_shows_partial_success(self):
        h = _make_radio_handler()
        h.ctx.dialog._inputbox_returns = ["LongName", "SHRT"]

        with patch.object(_mesh_cmd_module, 'set_owner',
                          return_value=CommandResult.ok("OK")), \
             patch.object(_mesh_cmd_module, 'set_owner_short',
                          return_value=CommandResult.ok("OK")), \
             patch('utils.device_config_store.save_device_settings',
                   return_value=False):
            h._set_owner_name()

        assert h.ctx.dialog.last_msgbox_title == "Partial Success", (
            f"got msgbox title={h.ctx.dialog.last_msgbox_title!r} — "
            "save_device_settings returned False but title doesn't "
            "reflect the persistence failure"
        )
        assert "restart persistence" in (h.ctx.dialog.last_msgbox_text or "").lower()

    def test_full_success_shows_success(self):
        h = _make_radio_handler()
        h.ctx.dialog._inputbox_returns = ["LongName", "SHRT"]

        with patch.object(_mesh_cmd_module, 'set_owner',
                          return_value=CommandResult.ok("OK")), \
             patch.object(_mesh_cmd_module, 'set_owner_short',
                          return_value=CommandResult.ok("OK")), \
             patch('utils.device_config_store.save_device_settings',
                   return_value=True):
            h._set_owner_name()

        assert h.ctx.dialog.last_msgbox_title == "Success"


class TestActivateHardwareConfigTruthful:
    """``_activate_hardware_config`` calls activate_hardware_config (returns
    bool). Pre-fix: unconditional 'Success'. Post-fix: 'Error' when the
    function returns False (file copy failed, service restart rejected,
    etc.)."""

    def test_failed_activation_shows_error(self, tmp_path):
        h = _make_radio_handler()
        h.ctx.dialog._yesno_returns = [True]  # confirm
        cfg = tmp_path / "test-hat.yaml"
        cfg.write_text("# test\n")
        available = tmp_path
        config_d = tmp_path / "config.d"
        config_d.mkdir()

        with patch('handlers.meshtasticd_radio.activate_hardware_config',
                   return_value=False):
            h._activate_hardware_config("test-hat.yaml", available, config_d)

        assert h.ctx.dialog.last_msgbox_title == "Error", (
            f"got msgbox title={h.ctx.dialog.last_msgbox_title!r} — "
            "activate_hardware_config returned False but handler "
            "reported Success"
        )
        assert "activation failed" in (h.ctx.dialog.last_msgbox_text or "").lower()

    def test_successful_activation_shows_success(self, tmp_path):
        h = _make_radio_handler()
        h.ctx.dialog._yesno_returns = [True]
        cfg = tmp_path / "test-hat.yaml"
        cfg.write_text("# test\n")
        available = tmp_path
        config_d = tmp_path / "config.d"
        config_d.mkdir()

        with patch('handlers.meshtasticd_radio.activate_hardware_config',
                   return_value=True):
            h._activate_hardware_config("test-hat.yaml", available, config_d)

        assert h.ctx.dialog.last_msgbox_title == "Success"
