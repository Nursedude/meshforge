"""The map's own CSP must allow its live-updates WebSocket (2026-09-25):
connect-src 'self' covers only :5000, so ws://<host>:5001 was refused from
2026-05-14 on and the map never went live."""
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(HERE, "..", "src") not in sys.path:
    sys.path.insert(0, os.path.join(HERE, "..", "src"))

import utils.map_http_handler as mh  # noqa: E402
from utils.map_http_handler import MapRequestHandler  # noqa: E402


def _handler(monkeypatch, host, running=True):
    monkeypatch.setattr(mh, "_HAS_WS_SERVER", True)
    monkeypatch.setattr(mh, "_is_websocket_available", lambda: True)
    monkeypatch.setattr(mh, "_get_websocket_server",
                        lambda: SimpleNamespace(_running=running, port=5001))
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {"Host": host}
    return h


def test_running_ws_is_allowed_for_the_host_the_page_came_from(monkeypatch):
    csp = _handler(monkeypatch, "192.0.2.10:5000")._csp_policy()
    assert "connect-src 'self' ws://192.0.2.10:5001;" in csp


def test_not_running_keeps_self_only(monkeypatch):
    csp = _handler(monkeypatch, "192.0.2.10:5000", running=False)._csp_policy()
    assert "connect-src 'self';" in csp and "ws://" not in csp


def test_hostile_host_header_never_reaches_the_policy(monkeypatch):
    csp = _handler(monkeypatch, "evil.example; script-src *")._csp_policy()
    assert "evil" not in csp and "connect-src 'self';" in csp
