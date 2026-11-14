"""Traffic Inspector's menu must not read as "this box captures nothing".

On moc (2026-09-26) the header said "Capture: STOPPED" beside Statistics
showing 5,027 packets the GATEWAY captured into the shared DB. The header now
says what THIS process does, and how fresh the shared DB is.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from launcher_tui.handlers import traffic_inspector as ti


def _line(packets=None, raises=None):
    class _Insp:
        def get_packets(self, limit=100):
            if raises:
                raise raises
            return packets or []

    h = ti.TrafficInspectorHandler()
    with patch.object(ti, "get_traffic_inspector", lambda: _Insp()):
        return h._shared_capture_line()


def _pkt(seconds_ago):
    return SimpleNamespace(timestamp=datetime.now() - timedelta(seconds=seconds_ago))


@pytest.mark.parametrize("ago,expect", [
    (12, "newest packet 12s ago"),
    (600, "newest packet 10m ago"),
    (54 * 86400, "newest packet 54d ago"),
])
def test_age_of_the_newest_shared_packet(ago, expect):
    assert expect in _line([_pkt(ago)])


def test_empty_db_says_empty():
    assert _line([]) == "Shared capture DB: empty"


def test_unreadable_db_is_unknown_not_empty():
    assert "UNKNOWN (OperationalError)" in _line(raises=type("OperationalError", (Exception,), {})())


def test_future_timestamp_is_named():
    assert "FUTURE" in _line([_pkt(-3600)])


# ── 2026-11-14 on the manager box: "Packet capture is now active" + Live Traffic
# "ACTIVE · 0 packets" while the TUI held NO meshtasticd connection (ss: zero
# :4403 sockets from its pid) and the shared DB's newest row was 98 d old.
# meshtastic.receive only fires for a radio connection IN THIS PROCESS, so
# the capture could never see a Meshtastic packet. A choice that falls silent
# must say so. ──

from unittest.mock import MagicMock


class _Svc:
    def __init__(self, up):
        self.up = up

    def __bool__(self):
        return self.up


def _capturing_handler(monkeypatch, connected, packets=()):
    h = ti.TrafficInspectorHandler()
    h.ctx = MagicMock()
    state = {"running": False}

    def _start():
        state["running"] = True
        return True
    monkeypatch.setattr(ti, "start_packet_capture", _start)
    monkeypatch.setattr(ti, "stop_packet_capture", lambda: True)
    monkeypatch.setattr(ti, "is_capture_running", lambda: state["running"])
    if isinstance(connected, Exception):
        def _conn():
            raise connected
    else:
        def _conn():
            return connected
    monkeypatch.setattr("utils.connection_manager.is_connected", _conn)

    class _Insp:
        def get_capture_stats(self):
            return {"packets_captured": 0, "packets_meshtastic": 0,
                    "packets_rns": 0, "bytes_captured": 0}

        def get_log_path(self):
            return None

        def get_packets(self, limit=100, **_kw):
            return list(packets)
    monkeypatch.setattr(h, "_get_inspector", lambda: _Insp())
    return h, state


def _shown(h):
    return "\n".join(str(a) for c in h.ctx.dialog.msgbox.call_args_list for a in c[0])


def test_start_capture_without_a_radio_connection_says_nothing_will_arrive(monkeypatch):
    h, _ = _capturing_handler(monkeypatch, connected=False)
    h._toggle_capture()
    text = _shown(h)
    assert "no radio connection" in text.lower()
    assert "no meshtastic packets" in text.lower()
    assert "Packets will appear" not in text


def test_start_capture_with_a_radio_connection_reads_as_active(monkeypatch):
    h, _ = _capturing_handler(monkeypatch, connected=True)
    h._toggle_capture()
    text = _shown(h)
    assert "Packet capture is now active" in text
    assert "no radio connection" not in text.lower()


def test_an_unreadable_connection_state_is_unknown_not_active(monkeypatch):
    h, _ = _capturing_handler(monkeypatch, connected=RuntimeError("boom"))
    h._toggle_capture()
    text = _shown(h)
    assert "UNKNOWN" in text
    assert "Packets will appear" not in text


def test_live_view_names_a_missing_meshtastic_feed(monkeypatch):
    h, state = _capturing_handler(monkeypatch, connected=False)
    state["running"] = True
    h._traffic_live_view()
    text = _shown(h)
    assert "Meshtastic feed: NONE" in text
    assert "Traffic will appear once mesh activity occurs" not in text


def test_live_view_recent_packets_carry_their_age(monkeypatch):
    old = SimpleNamespace(timestamp=datetime.now() - timedelta(days=98),
                          get_summary=lambda: "local <- f0a6899b6776 [RNS:announce] h=2")
    h, state = _capturing_handler(monkeypatch, connected=True, packets=[old])
    state["running"] = True
    h._traffic_live_view()
    text = _shown(h)
    assert "newest 98d ago" in text


def test_live_view_says_meshtasticd_down_when_it_is(monkeypatch):
    """`svc.get("active")` on a ServiceStatus always raised and was swallowed,
    so the stopped-capture hint never named a down meshtasticd."""
    h, _ = _capturing_handler(monkeypatch, connected=False)
    monkeypatch.setattr("utils.service_check.check_service", lambda name: _Svc(False))
    h._traffic_live_view()
    assert "meshtasticd not running" in _shown(h)
