"""Review B (2026-09-23): the latency panes under an UNOBSERVABLE probe.

8c41765a made ServiceHealth.status read UNKNOWN while the probe cannot be
made (EMFILE). The two panes that consume it still folded that UNKNOWN into
"No probe data yet" / "All services healthy", and the status pane printed
the stale sample's RTT under the UNKNOWN label. Pinned here: the degraded
pane names the unobservable service and never says "healthy" or "no data"
for it; the status pane prints no RTT for a service it cannot probe.
"""
import contextlib
import io
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))

from utils import latency_monitor as lm  # noqa: E402
from handlers import latency as L  # noqa: E402


def _monitor_gone_unobservable():
    m = lm.LatencyMonitor(services=[("svc", "127.0.0.1", 1)])
    with patch.object(lm, "probe_tcp", return_value=(True, 1.0)):
        m.probe_once()                                  # one real HEALTHY sample
    with patch.object(lm.socket, "socket", side_effect=OSError("EMFILE")):
        m.probe_once()                                  # now the probe cannot be made
    return m


def _run(method):
    h = L.LatencyHandler.__new__(L.LatencyHandler)
    h.ctx = MagicMock()
    buf = io.StringIO()
    with patch.object(L, "get_latency_monitor", return_value=_monitor_gone_unobservable()), \
         patch.object(L, "clear_screen", lambda: None), contextlib.redirect_stdout(buf):
        getattr(h, method)()
    return buf.getvalue()


def test_degraded_pane_names_the_unobservable_service():
    out = _run("_show_degraded_services")
    assert "No probe data yet" not in out
    assert "All services healthy" not in out
    assert "UNKNOWN" in out and "Cannot probe" in out and "svc" in out


def test_status_table_says_cannot_probe_not_no_data_yet():
    out = _run("_show_latency_status")
    assert "No probe data yet" not in out
    assert "cannot probe" in out


def test_probe_now_pane_prints_no_rtt_for_a_service_it_cannot_probe():
    # _latency_probe_now probes again itself; keep the socket unopenable so
    # that probe is also unobservable (one stale HEALTHY sample remains).
    with patch.object(lm.socket, "socket", side_effect=OSError("EMFILE")):
        out = _run("_latency_probe_now")
    assert "cannot probe" in out
    assert "RTT:" not in out
