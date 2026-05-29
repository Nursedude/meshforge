"""
Unit tests for ServiceMenuHandler.

Tests the most complex handler: service control, gateway bridge
management, meshtasticd installation, MQTT setup wizard, port lockdown.
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context


def _make_service_menu():
    from handlers.service_menu import ServiceMenuHandler
    h = ServiceMenuHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


class TestServiceMenuStructure:

    def test_handler_id(self):
        h = _make_service_menu()
        assert h.handler_id == "service_menu"

    def test_menu_section(self):
        h = _make_service_menu()
        assert h.menu_section == "mesh_networks"

    def test_menu_items(self):
        h = _make_service_menu()
        items = h.menu_items()
        assert len(items) >= 1
        tag, desc, flag = items[0]
        assert tag == "services"

    def test_execute_dispatches(self):
        h = _make_service_menu()
        with patch.object(h, '_service_menu') as mock:
            h.execute("services")
            mock.assert_called_once()


class TestServiceMenuNavigation:

    def test_service_menu_back(self):
        h = _make_service_menu()
        h.ctx.dialog._menu_returns = [None]
        h._service_menu()  # Should exit cleanly

    def test_service_menu_dispatches_status(self):
        h = _make_service_menu()
        h.ctx.dialog._menu_returns = ["status", None]
        h.ctx.safe_call = MagicMock()
        h._service_menu()
        h.ctx.safe_call.assert_called()


class TestServiceStatus:

    @patch('subprocess.run')
    @patch('handlers.service_menu.check_service')
    @patch('handlers.service_menu.check_systemd_service')
    def test_show_all_service_status(self, mock_systemd, mock_check_svc, mock_run):
        svc_status = MagicMock()
        svc_status.available = True
        svc_status.running = True
        svc_status.state = "running"
        mock_check_svc.return_value = svc_status
        mock_systemd.return_value = (True, True)  # (is_running, is_enabled)
        mock_run.return_value = MagicMock(returncode=0, stdout="active")

        h = _make_service_menu()
        # Mock the instance method _has_systemd_unit
        h._has_systemd_unit = MagicMock(return_value=True)
        h._is_rnsd_running = MagicMock(return_value=False)
        h.ctx.wait_for_enter = MagicMock()
        h._show_all_service_status()
        h.ctx.wait_for_enter.assert_called_once()


class TestPortLockdown:

    @patch('handlers.service_menu.check_port_locked')
    def test_manage_port_lockdown_back(self, mock_check):
        mock_check.return_value = (False, "")
        h = _make_service_menu()
        h.ctx.dialog._menu_returns = [None]
        h._manage_port_lockdown()

    @patch('handlers.service_menu.persist_iptables')
    @patch('handlers.service_menu.lock_port_external')
    @patch('handlers.service_menu.check_port_locked')
    def test_lock_port(self, mock_check, mock_lock, mock_persist):
        mock_check.return_value = (False, "not locked")
        mock_lock.return_value = (True, "locked")
        h = _make_service_menu()
        h.ctx.dialog._menu_returns = ["lock", None]
        h.ctx.wait_for_enter = MagicMock()
        h._manage_port_lockdown()

    @patch('handlers.service_menu.persist_iptables')
    @patch('handlers.service_menu.unlock_port_external')
    @patch('handlers.service_menu.check_port_locked')
    def test_unlock_port(self, mock_check, mock_unlock, mock_persist):
        mock_check.return_value = (True, "locked via iptables")
        mock_unlock.return_value = (True, "unlocked")
        h = _make_service_menu()
        h.ctx.dialog._menu_returns = ["unlock", None]
        h.ctx.wait_for_enter = MagicMock()
        h._manage_port_lockdown()


class TestRestartServices:

    @patch('subprocess.run')
    @patch('handlers.service_menu.apply_config_and_restart')
    def test_restart_meshtasticd(self, mock_restart, mock_run):
        mock_restart.return_value = (True, "restarted")
        mock_run.return_value = MagicMock(returncode=0, stdout="active")
        h = _make_service_menu()
        h.ctx.wait_for_enter = MagicMock()
        h._restart_meshtasticd_service()
        mock_restart.assert_called_once_with('meshtasticd')

    @patch('subprocess.run')
    @patch('handlers.service_menu.start_service')
    def test_start_rnsd_with_systemd(self, mock_start, mock_run):
        mock_start.return_value = (True, "started")
        mock_run.return_value = MagicMock(returncode=0, stdout="active")
        h = _make_service_menu()
        h._has_systemd_unit = MagicMock(return_value=True)
        h.ctx.wait_for_enter = MagicMock()
        h._start_rnsd_service()
        mock_start.assert_called_once_with('rnsd')

    @patch('subprocess.run')
    def test_start_rnsd_direct(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        h = _make_service_menu()
        h._has_systemd_unit = MagicMock(return_value=False)
        h._start_rnsd_direct = MagicMock(return_value=True)
        h.ctx.wait_for_enter = MagicMock()
        h._start_rnsd_service()


class TestMQTTSetupWizard:

    def test_wizard_all_prereqs_met(self):
        h = _make_service_menu()
        h.ctx.dialog._yesno_returns = [True]  # Confirm wizard
        h._is_mosquitto_installed = MagicMock(return_value=True)
        h._install_mosquitto = MagicMock(return_value=True)
        h._ensure_mosquitto_running = MagicMock(return_value=True)
        h._auto_detect_primary_channel = MagicMock(return_value="LongFast")
        h._configure_meshtasticd_mqtt_local = MagicMock(return_value=True)
        h._mqtt_setup_wizard()
        assert h.ctx.dialog.last_msgbox_title is not None

    def test_wizard_cancel(self):
        h = _make_service_menu()
        h.ctx.dialog._yesno_returns = [False]  # Cancel wizard
        h._is_mosquitto_installed = MagicMock()
        h._mqtt_setup_wizard()
        h._is_mosquitto_installed.assert_not_called()


class TestMosquittoHelpers:

    @patch('subprocess.run')
    def test_is_mosquitto_installed_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        h = _make_service_menu()
        assert h._is_mosquitto_installed() is True

    @patch('subprocess.run')
    def test_is_mosquitto_installed_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        h = _make_service_menu()
        assert h._is_mosquitto_installed() is False

    @patch('subprocess.run')
    def test_is_mosquitto_installed_error(self, mock_run):
        mock_run.side_effect = OSError("not found")
        h = _make_service_menu()
        assert h._is_mosquitto_installed() is False


class TestHasSystemdUnit:

    @patch('subprocess.run')
    def test_has_systemd_unit_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        h = _make_service_menu()
        assert h._has_systemd_unit('rnsd') is True

    @patch('subprocess.run')
    def test_has_systemd_unit_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        h = _make_service_menu()
        assert h._has_systemd_unit('rnsd') is False


class TestManageService:

    def test_manage_service_back(self):
        h = _make_service_menu()
        h.ctx.dialog._menu_returns = [None]
        h._manage_service("meshtasticd")

    @patch('subprocess.run')
    def test_manage_service_status(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="active")
        h = _make_service_menu()
        h._has_systemd_unit = MagicMock(return_value=True)
        h.ctx.dialog._menu_returns = ["status", None]
        h.ctx.wait_for_enter = MagicMock()
        h._manage_service("meshtasticd")


class TestOpenHamClockDocker:

    @patch('shutil.which')
    def test_manage_openhamclock_no_docker(self, mock_which):
        mock_which.return_value = None
        h = _make_service_menu()
        h._manage_openhamclock_docker()
        assert h.ctx.dialog.last_msgbox_title is not None

    @patch('shutil.which')
    def test_manage_openhamclock_back(self, mock_which):
        mock_which.return_value = "/usr/bin/docker"
        h = _make_service_menu()
        h.ctx.dialog._menu_returns = [None]
        h._manage_openhamclock_docker()

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_openhamclock_status(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=0, stdout="openhamclock running")
        h = _make_service_menu()
        h.ctx.wait_for_enter = MagicMock()
        h._openhamclock_docker_status()
