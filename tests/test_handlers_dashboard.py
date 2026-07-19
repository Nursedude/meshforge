"""
Unit tests for DashboardHandler.

Tests business logic of the 7 dashboard actions:
status, weather, nodes, score, datapath, reports, alerts.
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context


def _make_dashboard():
    from handlers.dashboard import DashboardHandler
    h = DashboardHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


class TestDashboardHandlerStructure:

    def test_handler_id(self):
        h = _make_dashboard()
        assert h.handler_id == "dashboard"

    def test_menu_section(self):
        h = _make_dashboard()
        assert h.menu_section == "dashboard"

    def test_menu_items_tags(self):
        h = _make_dashboard()
        tags = [t for t, _, _ in h.menu_items()]
        expected = ["status", "weather", "nodes", "score", "datapath", "reports", "alerts"]
        assert tags == expected

    def test_execute_unknown_action_does_not_raise(self):
        h = _make_dashboard()
        h.execute("nonexistent")  # Should not raise


class TestDashboardDispatch:

    def test_execute_status_dispatches(self):
        h = _make_dashboard()
        with patch.object(h, '_service_status_display') as mock:
            h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
            h.execute("status")
            mock.assert_called_once()

    def test_execute_weather_dispatches(self):
        h = _make_dashboard()
        with patch.object(h, '_dashboard_space_weather') as mock:
            h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
            h.execute("weather")
            mock.assert_called_once()

    def test_execute_nodes_dispatches(self):
        h = _make_dashboard()
        with patch.object(h, '_show_node_counts') as mock:
            h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
            h.execute("nodes")
            mock.assert_called_once()

    def test_execute_score_dispatches(self):
        h = _make_dashboard()
        with patch.object(h, '_health_score_display') as mock:
            h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
            h.execute("score")
            mock.assert_called_once()

    def test_execute_datapath_dispatches(self):
        h = _make_dashboard()
        with patch.object(h, '_data_path_diagnostic') as mock:
            h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
            h.execute("datapath")
            mock.assert_called_once()

    def test_execute_reports_dispatches(self):
        h = _make_dashboard()
        with patch.object(h, '_reports_menu') as mock:
            h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
            h.execute("reports")
            mock.assert_called_once()

    def test_execute_alerts_dispatches(self):
        h = _make_dashboard()
        with patch.object(h, '_show_alerts') as mock:
            h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
            h.execute("alerts")
            mock.assert_called_once()


class TestDashboardSpaceWeather:

    @patch('commands.propagation.get_space_weather')
    def test_weather_success(self, mock_get_weather):
        result = MagicMock()
        result.success = True
        result.data = {
            "solar_flux": 150,
            "k_index": 2,
            "a_index": 5,
            "xray_flux": "B3.0",
            "geomag_storm": False,
            "band_conditions": {"20m": "Good", "40m": "Fair"},
            "source": "NOAA SWPC",
        }
        mock_get_weather.return_value = result
        h = _make_dashboard()
        h._dashboard_space_weather()
        assert h.ctx.dialog.last_msgbox_title == "Space Weather"
        assert "150" in h.ctx.dialog.last_msgbox_text  # SFI value

    @patch('commands.propagation.get_space_weather')
    def test_weather_failure_shows_error(self, mock_get_weather):
        mock_get_weather.side_effect = Exception("API down")
        h = _make_dashboard()
        h.ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
        with pytest.raises(Exception, match="API down"):
            h._dashboard_space_weather()


class TestDashboardNodeCounts:

    @patch('subprocess.run')
    @patch('handlers.dashboard.get_http_client')
    def test_node_counts_with_http_client(self, mock_get_client, mock_run):
        mock_client = MagicMock()
        mock_client.is_available = True
        mock_client.get_nodes.return_value = [
            MagicMock(), MagicMock(), MagicMock()
        ]
        mock_get_client.return_value = mock_client

        # RNS rnstatus output
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="<abc123> RNS Dest\n<def456> RNS Dest\n"
        )

        h = _make_dashboard()
        h.ctx.wait_for_enter = MagicMock()
        h._show_node_counts()
        h.ctx.wait_for_enter.assert_called_once()

    @patch('subprocess.run')
    @patch('handlers.dashboard.get_http_client')
    def test_node_counts_http_unavailable(self, mock_get_client, mock_run):
        mock_client = MagicMock()
        mock_client.is_available = False
        mock_get_client.return_value = mock_client
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        h = _make_dashboard()
        h.ctx.wait_for_enter = MagicMock()
        h._show_node_counts()
        # Should not raise even when HTTP client is unavailable


class TestDashboardHealthScore:

    @patch('handlers.dashboard.get_health_scorer')
    @patch('subprocess.run')
    def test_health_score_display(self, mock_run, mock_scorer_fn):
        mock_run.return_value = MagicMock(returncode=0)
        scorer = MagicMock()
        snapshot = MagicMock()
        snapshot.overall_score = 85
        snapshot.status = "healthy"
        snapshot.category_scores = {
            "connectivity": 90,
            "services": 80,
            "hardware": 85,
        }
        snapshot.node_count = 5
        snapshot.service_count = 3
        scorer.get_snapshot.return_value = snapshot
        scorer.get_trend.return_value = "stable"
        mock_scorer_fn.return_value = scorer

        h = _make_dashboard()
        h.ctx.wait_for_enter = MagicMock()
        h._health_score_display()
        h.ctx.wait_for_enter.assert_called_once()


class TestDashboardReports:

    def test_reports_menu_back(self):
        h = _make_dashboard()
        h.ctx.dialog._menu_returns = [None]  # immediate back
        h._reports_menu()

    @patch('handlers.dashboard.generate_report')
    @patch('subprocess.run')
    def test_reports_generate_and_view(self, mock_run, mock_gen):
        mock_run.return_value = MagicMock(returncode=0)
        mock_gen.return_value = "=== MeshForge Report ===\nAll OK"

        h = _make_dashboard()
        h.ctx.dialog._menu_returns = ["generate", None]
        h.ctx.wait_for_enter = MagicMock()
        h._reports_menu()

    @patch('handlers.dashboard.generate_and_save')
    @patch('subprocess.run')
    def test_reports_generate_and_save(self, mock_run, mock_save):
        mock_run.return_value = MagicMock(returncode=0)
        mock_save.return_value = "/tmp/report.txt"

        h = _make_dashboard()
        h.ctx.dialog._menu_returns = ["save", None]
        h.ctx.wait_for_enter = MagicMock()
        h._reports_menu()


class TestDashboardAlerts:

    @patch('handlers.dashboard.EASAlertsPlugin')
    def test_alerts_with_system_and_weather(self, mock_eas_cls):
        mock_env = MagicMock()
        mock_env.get_alerts.return_value = ["meshtasticd: not running"]

        mock_plugin = MagicMock()
        mock_alert = MagicMock()
        mock_alert.severity = "Warning"
        mock_alert.headline = "High Wind Advisory for Oahu"
        mock_plugin.get_weather_alerts.return_value = [mock_alert]
        mock_eas_cls.return_value = mock_plugin

        h = _make_dashboard()
        h.ctx.env_state = mock_env
        h.ctx.wait_for_enter = MagicMock()
        h._show_alerts()
        h.ctx.wait_for_enter.assert_called_once()

    @patch('handlers.dashboard.EASAlertsPlugin')
    def test_alerts_no_alerts(self, mock_eas_cls):
        mock_env = MagicMock()
        mock_env.get_alerts.return_value = []
        mock_eas_cls.return_value.get_weather_alerts.return_value = []

        h = _make_dashboard()
        h.ctx.env_state = mock_env
        h.ctx.wait_for_enter = MagicMock()
        h._show_alerts()


class TestDashboardServiceStatus:

    @patch('subprocess.run')
    def test_service_status_display_all_healthy_waits(self, mock_run):
        # All services running + reachable => no degraded => plain wait-for-enter.
        from handlers.dashboard import ServiceRunState
        mock_run.return_value = MagicMock(returncode=0, stdout="active")
        mock_env = MagicMock()
        mock_env.services = {
            "meshtasticd": MagicMock(state=ServiceRunState.RUNNING),
            "rnsd": MagicMock(state=ServiceRunState.RUNNING),
            "mosquitto": MagicMock(state=ServiceRunState.RUNNING),
        }
        h = _make_dashboard()
        h.ctx.env_state = mock_env
        h._meshtasticd_api_reachable = MagicMock(return_value=True)
        h.ctx.wait_for_enter = MagicMock()
        h._service_status_display()
        h.ctx.wait_for_enter.assert_called_once()

    @patch('service_remediation.service_enabled_here', return_value=True)
    @patch('subprocess.run')
    def test_service_status_display_offers_fix_when_degraded(self, mock_run, _mock_enabled):
        # A FAILED known service => the in-app fix chooser is offered, not a bare
        # "press enter" (In-Domain: the fix comes to the operator, in this view).
        # service_enabled_here patched True so the test doesn't depend on the dev
        # box's actual rnsd enabled-state.
        from handlers.dashboard import ServiceRunState
        mock_run.return_value = MagicMock(returncode=0, stdout="active")
        mock_env = MagicMock()
        mock_env.services = {
            "meshtasticd": MagicMock(state=ServiceRunState.RUNNING),
            "rnsd": MagicMock(state=ServiceRunState.FAILED),
        }
        h = _make_dashboard()
        h.ctx.env_state = mock_env
        h._meshtasticd_api_reachable = MagicMock(return_value=True)
        h.ctx.dialog._menu_returns = ["__done__"]  # dismiss the fix chooser
        h.ctx.wait_for_enter = MagicMock()
        h._service_status_display()
        menu_titles = [c[1][0] for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert any("Fix a Degraded Service" in t for t in menu_titles)


class TestDataPathHttpTriState:
    """Issue #76 consumer fix: the data-path check must not map the
    structural json_api_absent state (live webserver, /json/* 404s —
    permanent on every meshtasticd box) to a FAIL. Only a genuinely dead
    webserver is a failure."""

    def test_json_api_absent_is_na_not_fail(self):
        h = _make_dashboard()
        client = MagicMock()
        client.json_api_absent = True
        client.port = 9443
        status, detail = h._classify_http_unavailable(client)
        assert status == "N/A"
        assert "Issue #76" in detail
        assert "webserver alive" in detail

    def test_webserver_down_is_fail(self):
        h = _make_dashboard()
        client = MagicMock()
        client.json_api_absent = False
        client.port = 9443
        status, detail = h._classify_http_unavailable(client)
        assert status == "FAIL"
        assert "No webserver answered" in detail

    def test_fail_detail_derives_ports_from_client(self):
        h = _make_dashboard()
        client = MagicMock()
        client.json_api_absent = False
        client.port = 8443  # non-default: must lead the derived list
        status, detail = h._classify_http_unavailable(client)
        assert status == "FAIL"
        assert "8443" in detail

    def test_client_without_flag_treated_as_down(self):
        """A client predating the tri-state (no json_api_absent attr) must
        not crash and must stay on the conservative FAIL path."""
        h = _make_dashboard()

        class Bare:
            port = 9443

        status, _ = h._classify_http_unavailable(Bare())
        assert status == "FAIL"
