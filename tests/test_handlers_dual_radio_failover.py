"""
Unit tests for DualRadioFailoverHandler.

Tests structure, dispatch, menu flow, preflight checks,
configuration editing (validation, cross-validation), and toggle logic.
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context


def _make_handler():
    from handlers.dual_radio_failover import DualRadioFailoverHandler
    h = DualRadioFailoverHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


# ── Structure ───────────────────────────────────────────────────────


class TestDualRadioFailoverStructure:

    def test_handler_id(self):
        h = _make_handler()
        assert h.handler_id == "dual_radio_failover"

    def test_menu_section(self):
        h = _make_handler()
        assert h.menu_section == "mesh_networks"

    def test_menu_items_tags(self):
        h = _make_handler()
        items = h.menu_items()
        assert len(items) == 1
        assert items[0][0] == "dual_failover"

    def test_execute_unknown_action_does_not_raise(self):
        h = _make_handler()
        h.execute("nonexistent")


# ── Dispatch ────────────────────────────────────────────────────────


class TestDualRadioFailoverDispatch:

    def test_execute_dual_failover_dispatches(self):
        h = _make_handler()
        with patch.object(h, '_failover_menu') as mock:
            h.execute("dual_failover")
            mock.assert_called_once()


# ── Helpers ─────────────────────────────────────────────────────────


class TestDualRadioFailoverHelpers:

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', False)
    def test_get_failover_manager_without_module(self):
        h = _make_handler()
        assert h._get_failover_manager() is None

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_get_failover_manager_no_attr(self):
        h = _make_handler()
        # ctx doesn't have failover_manager attr
        assert h._get_failover_manager() is None

    @patch('handlers.dual_radio_failover._HAS_CONFIG', False)
    def test_load_config_without_module(self):
        h = _make_handler()
        assert h._load_config() is None

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_quick_status_disabled(self):
        h = _make_handler()
        with patch.object(h, '_load_config', return_value=None):
            assert h._get_quick_status() == "Disabled"

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_quick_status_enabled_no_bridge(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_enabled = True
        with patch.object(h, '_load_config', return_value=cfg):
            assert h._get_quick_status() == "Enabled in config — live state NOT OBSERVABLE here"

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_quick_status_with_manager(self):
        h = _make_handler()
        fm = MagicMock()
        fm.get_status.return_value = {
            'state': 'primary',
            'watchdog': {'enabled': True},
            'failover_count_1h': 2,
        }
        with patch.object(h, '_get_failover_manager', return_value=fm):
            status = h._get_quick_status()
            assert "PRIMARY" in status
            assert "ON" in status
            assert "2" in status


# ── Failover Menu ──────────────────────────────────────────────────


class TestDualRadioFailoverMenu:

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', False)
    def test_menu_not_available(self):
        h = _make_handler()
        h._failover_menu()
        assert h.ctx.dialog.last_msgbox_title == "Not Available"

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_menu_back_exits(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = [None]
        with patch.object(h, '_get_quick_status', return_value="Disabled"):
            h._failover_menu()

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_menu_dispatches_status(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["status", "back"]
        h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
        with patch.object(h, '_get_quick_status', return_value="Disabled"), \
             patch.object(h, '_show_status') as mock:
            h._failover_menu()
            mock.assert_called_once()

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_menu_dispatches_preflight(self):
        h = _make_handler()
        h.ctx.dialog._menu_returns = ["preflight", "back"]
        h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
        with patch.object(h, '_get_quick_status', return_value="Disabled"), \
             patch.object(h, '_preflight_check') as mock:
            h._failover_menu()
            mock.assert_called_once()


# ── Status Display ──────────────────────────────────────────────────


class TestDualRadioFailoverStatus:

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_status_no_config(self):
        h = _make_handler()
        with patch.object(h, '_load_config', return_value=None):
            h._show_status()
        assert h.ctx.dialog.last_msgbox_title == "Dual-Radio Failover"
        assert "No gateway config" in h.ctx.dialog.last_msgbox_text

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_status_config_only(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_enabled = True
        cfg.failover_utilization_threshold = 80.0
        cfg.failover_utilization_duration = 30
        cfg.failover_recovery_threshold = 50.0
        cfg.failover_recovery_duration = 60
        cfg.failover_health_poll_interval = 5
        cfg.failover_watchdog_enabled = True
        cfg.failover_primary_service = "meshtasticd"
        cfg.failover_secondary_service = "meshtasticd-alt"
        with patch.object(h, '_load_config', return_value=cfg):
            h._show_status()
        assert "NOT OBSERVABLE" in h.ctx.dialog.last_msgbox_text
        assert "80" in h.ctx.dialog.last_msgbox_text

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_status_with_live_manager(self):
        h = _make_handler()
        fm = MagicMock()
        fm.get_status.return_value = {
            'state': 'primary',
            'active_port': 4403,
            'primary': {
                'reachable': True, 'port': 4403,
                'channel_utilization': 25.5, 'tx_utilization': 3.2,
                'overloaded': False,
            },
            'secondary': {
                'reachable': True, 'port': 4404,
                'channel_utilization': 12.0, 'tx_utilization': 1.1,
                'overloaded': False,
            },
            'watchdog': {
                'enabled': True,
                'primary_restarts_1h': 0, 'secondary_restarts_1h': 0,
                'primary_down': False, 'secondary_down': False,
            },
            'thresholds': {
                'utilization': 80, 'recovery': 50, 'duration': 30,
            },
            'failover_count_1h': 1,
            'last_event': 'Primary overloaded -> secondary',
        }
        with patch.object(h, '_get_failover_manager', return_value=fm):
            h._show_status()
        assert "PRIMARY" in h.ctx.dialog.last_msgbox_text
        assert "25.5%" in h.ctx.dialog.last_msgbox_text
        assert "ONLINE" in h.ctx.dialog.last_msgbox_text


# ── Pre-flight Check ───────────────────────────────────────────────


class TestDualRadioFailoverPreflight:

    @patch('handlers.dual_radio_failover._HAS_SERVICE', False)
    @patch('handlers.dual_radio_failover._HAS_HTTP', False)
    @patch('handlers.dual_radio_failover._HAS_PORT_DETECTION', False)
    def test_preflight_no_modules(self):
        h = _make_handler()
        with patch.object(h, '_load_config', return_value=None):
            h._preflight_check()
        text = h.ctx.dialog.last_msgbox_text
        assert "NOT READY" in text
        assert "[FAIL]" in text

    @patch('handlers.dual_radio_failover._HAS_SERVICE', True)
    @patch('handlers.dual_radio_failover._HAS_HTTP', False)
    @patch('handlers.dual_radio_failover._HAS_PORT_DETECTION', False)
    @patch('handlers.dual_radio_failover.check_service')
    def test_preflight_services_pass(self, mock_check):
        svc = MagicMock()
        svc.available = True
        mock_check.return_value = svc

        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_enabled = True
        with patch.object(h, '_load_config', return_value=cfg):
            h._preflight_check()
        text = h.ctx.dialog.last_msgbox_text
        assert "[PASS]" in text


# ── Configure ──────────────────────────────────────────────────────


class TestDualRadioFailoverConfigure:

    @patch('handlers.dual_radio_failover._HAS_CONFIG', False)
    def test_configure_no_module(self):
        h = _make_handler()
        h._configure()
        assert h.ctx.dialog.last_msgbox_title == "Configure"

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_configure_back_exits(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_utilization_threshold = 80.0
        cfg.failover_utilization_duration = 30
        cfg.failover_recovery_threshold = 50.0
        cfg.failover_recovery_duration = 60
        cfg.failover_health_poll_interval = 5.0
        cfg.failover_watchdog_enabled = True
        cfg.failover_restart_after_failures = 3
        cfg.failover_max_restarts_per_hour = 5
        cfg.failover_restart_cooldown = 60
        cfg.failover_primary_service = "meshtasticd"
        cfg.failover_secondary_service = "meshtasticd-alt"
        with patch.object(h, '_load_config', return_value=cfg):
            h.ctx.dialog._menu_returns = ["back"]
            h._configure()

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_edit_numeric_field_valid(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_utilization_threshold = 80.0
        cfg.failover_recovery_threshold = 50.0
        h.ctx.dialog._inputbox_returns = ["85.0"]

        h._edit_config_field("thresh", cfg)
        assert cfg.failover_utilization_threshold == 85.0
        cfg.save.assert_called_once()

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_edit_numeric_field_out_of_range(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_utilization_threshold = 80.0
        cfg.failover_recovery_threshold = 50.0
        h.ctx.dialog._inputbox_returns = ["200"]

        h._edit_config_field("thresh", cfg)
        assert h.ctx.dialog.last_msgbox_title == "Out of Range"
        cfg.save.assert_not_called()

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_edit_numeric_field_invalid(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_utilization_threshold = 80.0
        cfg.failover_recovery_threshold = 50.0
        h.ctx.dialog._inputbox_returns = ["not_a_number"]

        h._edit_config_field("thresh", cfg)
        assert h.ctx.dialog.last_msgbox_title == "Invalid Input"
        cfg.save.assert_not_called()

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_edit_recovery_cross_validation(self):
        """Recovery threshold must be less than utilization threshold."""
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_utilization_threshold = 80.0
        cfg.failover_recovery_threshold = 50.0
        h.ctx.dialog._inputbox_returns = ["90.0"]  # > 80% threshold

        h._edit_config_field("recov", cfg)
        assert h.ctx.dialog.last_msgbox_title == "Invalid"
        cfg.save.assert_not_called()

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_edit_utilization_cross_validation(self):
        """Utilization threshold must be greater than recovery threshold."""
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_utilization_threshold = 80.0
        cfg.failover_recovery_threshold = 50.0
        h.ctx.dialog._inputbox_returns = ["40.0"]  # < 50% recovery

        h._edit_config_field("thresh", cfg)
        assert h.ctx.dialog.last_msgbox_title == "Invalid"
        cfg.save.assert_not_called()

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_edit_watchdog_toggle(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_watchdog_enabled = True
        h.ctx.dialog._yesno_returns = [True]  # Confirm disable

        h._edit_config_field("wd", cfg)
        assert cfg.failover_watchdog_enabled is False
        cfg.save.assert_called_once()

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_edit_service_name(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_primary_service = "meshtasticd"
        h.ctx.dialog._inputbox_returns = ["meshtasticd-custom"]

        h._edit_config_field("svcpri", cfg)
        assert cfg.failover_primary_service == "meshtasticd-custom"
        cfg.save.assert_called_once()

    @patch('handlers.dual_radio_failover._HAS_CONFIG', True)
    def test_edit_cancelled_inputbox(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_utilization_threshold = 80.0
        cfg.failover_recovery_threshold = 50.0
        h.ctx.dialog._inputbox_returns = [None]

        h._edit_config_field("thresh", cfg)
        cfg.save.assert_not_called()


# ── Toggle ─────────────────────────────────────────────────────────


class TestDualRadioFailoverToggle:

    def test_toggle_no_config(self):
        h = _make_handler()
        with patch.object(h, '_load_config', return_value=None):
            h._toggle_failover()
        assert h.ctx.dialog.last_msgbox_title == "Toggle"

    def test_toggle_enable(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_enabled = False
        h.ctx.dialog._yesno_returns = [True]

        with patch.object(h, '_load_config', return_value=cfg):
            h._toggle_failover()
        assert cfg.failover_enabled is True
        cfg.save.assert_called_once()
        assert "ENABLED" in h.ctx.dialog.last_msgbox_text

    def test_toggle_disable(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_enabled = True
        h.ctx.dialog._yesno_returns = [True]

        with patch.object(h, '_load_config', return_value=cfg):
            h._toggle_failover()
        assert cfg.failover_enabled is False
        cfg.save.assert_called_once()
        assert "DISABLED" in h.ctx.dialog.last_msgbox_text

    def test_toggle_cancelled(self):
        h = _make_handler()
        cfg = MagicMock()
        cfg.failover_enabled = True
        h.ctx.dialog._yesno_returns = [False]

        with patch.object(h, '_load_config', return_value=cfg):
            h._toggle_failover()
        cfg.save.assert_not_called()


# ── Event Log ──────────────────────────────────────────────────────


class TestDualRadioFailoverEventLog:

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_event_log_no_manager(self):
        h = _make_handler()
        h._show_event_log()
        assert h.ctx.dialog.last_msgbox_title == "Event Log"
        assert "NOT OBSERVABLE" in h.ctx.dialog.last_msgbox_text

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_event_log_empty(self):
        h = _make_handler()
        fm = MagicMock()
        fm._events = []
        with patch.object(h, '_get_failover_manager', return_value=fm):
            h._show_event_log()
        assert "No state transitions" in h.ctx.dialog.last_msgbox_text

    @patch('handlers.dual_radio_failover._HAS_FAILOVER', True)
    def test_event_log_with_events(self):
        from datetime import datetime

        h = _make_handler()
        event = MagicMock()
        event.timestamp = datetime(2026, 3, 10, 14, 30, 0)
        event.from_state.value = "primary"
        event.to_state.value = "secondary"
        event.primary_utilization = 85.2
        event.secondary_utilization = 12.3
        event.reason = "Primary overloaded"

        fm = MagicMock()
        fm._events = [event]
        with patch.object(h, '_get_failover_manager', return_value=fm):
            h._show_event_log()
        text = h.ctx.dialog.last_msgbox_text
        assert "14:30:00" in text
        assert "primary -> secondary" in text
        assert "85.2%" in text


# ── Deploy Secondary ───────────────────────────────────────────────


class TestDualRadioFailoverDeploy:

    @patch('handlers.dual_radio_failover._HAS_SERVICE', False)
    def test_deploy_no_service_module(self):
        h = _make_handler()
        h._deploy_secondary()
        assert h.ctx.dialog.last_msgbox_title == "Deploy"

    @patch('handlers.dual_radio_failover._HAS_SERVICE', True)
    def test_deploy_cancelled(self):
        h = _make_handler()
        h.ctx.dialog._yesno_returns = [False]
        h._deploy_secondary()
        # No msgbox shown after cancellation

    def test_generate_secondary_config_fallback(self):
        """Minimal config generated when no primary exists."""
        h = _make_handler()
        with patch('pathlib.Path.exists', return_value=False):
            config = h._generate_secondary_config()
        assert "MeshForge Secondary Radio" in config
        assert "9444" in config
        assert "sx1262" in config
