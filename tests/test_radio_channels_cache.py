"""F5 of the BIC-3 adversarial pass (2026-10-14): `/api/radio/channels` did a
full PhoneAPI client-config handshake on the radio PER CALL (measured 0.26 s
+ 3 meshtasticd ERROR lines), and the inbox page calls it on every load. On a
gateway box the gateway's TCPInterface lives in another process, so the
in-process connection lock cannot serialise them (#17/#75). A short map-side
cache turns N page loads into one handshake per TTL; the answer says its age.
"""
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from utils import _map_radio_endpoints as re_mod  # noqa: E402
from utils.map_http_handler import MapRequestHandler  # noqa: E402

from test_web_auth import _req  # noqa: E402


class _Conn:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def get_channels(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("radio away")
        return [{"index": 0, "name": "LongFast", "role": "PRIMARY"}]

    def get_mode(self):
        return "tcp"


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", ["http://127.0.0.1"])
    monkeypatch.setattr(MapRequestHandler, "collector", None, raising=False)
    monkeypatch.setattr(MapRequestHandler, "is_warming", False, raising=False)
    monkeypatch.setattr(MapRequestHandler, "_radio_channels_cache", None, raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _conn(monkeypatch, conn):
    monkeypatch.setattr(MapRequestHandler, "_get_radio_connection", lambda self: conn)


def test_two_page_loads_are_one_handshake(server, monkeypatch):
    conn = _Conn()
    _conn(monkeypatch, conn)
    s1, b1, _ = _req(server, "GET", "/api/radio/channels")
    s2, b2, _ = _req(server, "GET", "/api/radio/channels")
    assert (s1, s2) == (200, 200)
    assert conn.calls == 1
    j1, j2 = json.loads(b1), json.loads(b2)
    assert j1["channels"] == j2["channels"] and j2["count"] == 1
    assert j1["cached_age_s"] == 0 and j2["cached_age_s"] >= 0
    assert j2["cache_ttl_s"] == re_mod.CHANNELS_CACHE_TTL_S


def test_the_cache_expires(server, monkeypatch):
    conn = _Conn()
    _conn(monkeypatch, conn)
    now = [5000.0]
    monkeypatch.setattr(re_mod.time, "monotonic", lambda: now[0])
    _req(server, "GET", "/api/radio/channels")
    now[0] += re_mod.CHANNELS_CACHE_TTL_S + 1
    _, body, _ = _req(server, "GET", "/api/radio/channels")
    assert conn.calls == 2 and json.loads(body)["cached_age_s"] == 0


def test_an_error_is_never_cached_as_an_answer(server, monkeypatch):
    conn = _Conn(fail=True)
    _conn(monkeypatch, conn)
    s1, _, _ = _req(server, "GET", "/api/radio/channels")
    s2, _, _ = _req(server, "GET", "/api/radio/channels")
    assert (s1, s2) == (500, 500) and conn.calls == 2
