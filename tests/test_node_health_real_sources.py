"""Node Health (2026-09-23): battery/signal screens read node_history via
Analytics, never meshtasticd's /json/* (ESP32-only, #76); the latency probe
says what a TCP connect proves (a listener), never HEALTHY."""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))

from handlers import node_health as nh  # noqa: E402


def _h():
    h = nh.NodeHealthHandler()
    h.ctx = MagicMock()
    return h


def test_battery_forecast_is_analytics_predictive():
    with patch("handlers.analytics.AnalyticsHandler._show_predictive_alerts") as m:
        _h()._battery_forecast_display()
    m.assert_called_once()


def test_signal_trends_is_analytics_link_trends():
    with patch("handlers.analytics.AnalyticsHandler._show_link_trends") as m:
        _h()._signal_trending_display()
    m.assert_called_once()


def test_no_json_endpoint_reader_remains():
    src = open(nh.__file__, encoding="utf-8").read()
    assert "get_http_client" not in src and "/json/" not in src.replace("/json/report and /json/nodes", "")


def test_latency_never_says_healthy(capsys):
    h = _h()
    with patch.object(nh, "_HAS_LATENCY", True), \
         patch.object(nh, "DEFAULT_SERVICES", [("svc", "127.0.0.1", 1)], create=True), \
         patch.object(nh, "probe_tcp", lambda *a, **k: (True, 1.0), create=True):
        h._service_latency_probe()
    out = capsys.readouterr().out
    assert "HEALTHY" not in out and "OPEN" in out
    assert "LISTENING" in out
