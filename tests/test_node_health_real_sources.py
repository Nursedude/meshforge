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


def test_the_default_probe_list_has_no_duplicate_port_and_no_tcp_rnsd():
    """2026-09-25 (review of the MA port, verified on a fleet box): meshtasticd_http
    probed 4403 twice-over, and rnsd — a unix-socket shared instance — was TCP
    probed on 37428, reading DOWN on every healthy box."""
    from utils import latency_monitor as lm
    from utils.ports import MESHTASTICD_WEB_PORT
    ports = [p for _n, _h, p in lm.DEFAULT_SERVICES]
    assert len(ports) == len(set(ports)), ports
    assert ("meshtasticd_http", "localhost", MESHTASTICD_WEB_PORT) in lm.DEFAULT_SERVICES
    assert not any(n == "rnsd" for n, _h, _p in lm.DEFAULT_SERVICES)
    assert any(n == "rnsd" for n, _why in lm.NOT_TCP_PROBED)
