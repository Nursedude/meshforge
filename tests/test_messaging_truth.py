"""Messaging screens say what their history IS (live-truth pass 2026-09-25):
Statistics read 'total_messages' (the command returns 'total') and printed
"Total: 0" beside "Received: 19185"; neither screen said the newest message
was 50 days old."""
import contextlib
import io
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
import handlers.messaging as hm  # noqa: E402


def _stats_screen(monkeypatch, data):
    fake = SimpleNamespace(get_stats=lambda: SimpleNamespace(success=True, data=data, message=""))
    monkeypatch.setattr(hm, "_messaging", lambda: fake)
    monkeypatch.setattr(hm, "clear_screen", lambda: None)
    monkeypatch.setattr(hm, "_listener_line", lambda: "PINNED")
    h = hm.MessagingHandler()
    h.set_context(make_handler_context(dialog=FakeDialog()))
    h.ctx.wait_for_enter = lambda *a, **k: None
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        h._messaging_stats()
    return out.getvalue()


def test_stats_total_is_the_commands_total(monkeypatch):
    out = _stats_screen(monkeypatch, {"total": 19185, "sent": 0, "received": 19185,
                                      "last_24h": 0, "newest": "2026-08-06 00:58:55",
                                      "by_network": {}})
    assert "Total messages:       19185" in out
    assert "d ago)" in out and "MQTT uplink" in out


def test_age_line():
    assert hm._age_line(None) == "none"
    assert "age unknown" in hm._age_line("not-a-date")


def test_command_reports_newest(tmp_path, monkeypatch):
    from commands import messaging as cm
    monkeypatch.setattr(cm, "_get_db_path", lambda: tmp_path / "messages.db")
    conn = cm._init_db()
    conn.execute("INSERT INTO messages (network, from_id, to_id, content) VALUES ('meshtastic','!a','bcast','x')")
    conn.commit()
    conn.close()
    d = cm.get_stats().data
    assert d["total"] == 1 and d["newest"]


def test_empty_history_explains_itself_not_start_the_listener(monkeypatch):
    fake = SimpleNamespace(get_messages=lambda limit=20: SimpleNamespace(
        success=True, data={"messages": []}, message=""))
    monkeypatch.setattr(hm, "_messaging", lambda: fake)
    monkeypatch.setattr(hm, "clear_screen", lambda: None)
    monkeypatch.setattr(hm, "_listener_line", lambda: "PINNED")
    h = hm.MessagingHandler()
    h.set_context(make_handler_context(dialog=FakeDialog()))
    h.ctx.wait_for_enter = lambda *a, **k: None
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        h._messaging_view()
    assert "Start the RX listener" not in out.getvalue()
    assert "MQTT uplink" in out.getvalue()



def test_listener_line_tri_state(monkeypatch):
    import io as _io
    import json as _json
    import urllib.request

    def fake(doc=None, exc=None):
        def _open(url, timeout=0):
            if exc:
                raise exc
            return _io.BytesIO(_json.dumps(doc).encode())
        return _open
    monkeypatch.setattr(urllib.request, "urlopen", fake(exc=OSError("refused")))
    assert hm._listener_line().startswith("UNKNOWN")
    monkeypatch.setattr(urllib.request, "urlopen", fake({"state": "disconnected", "error": "broker down"}))
    assert hm._listener_line().startswith("NOT RECORDING") and "broker down" in hm._listener_line()
    monkeypatch.setattr(urllib.request, "urlopen", fake({"state": "connected", "connected_since":
                        "2026-09-25T11:54:45", "messages_received": 0, "last_message_time": None}))
    assert hm._listener_line() == "connected since 2026-09-25 11:54, 0 received (none since connecting)"
