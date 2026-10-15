"""Non-author read of the BIC-3 fixes (2026-10-15, Opus 5.5; author Fable 5.1).

Three holes the fix commit left, each pinned red-first against the REAL
consumer (no fake replaces the function under review):

N1 — the `/mesh/` alias dispatches a GET to `_proxy_toradio`, which never
     checks the method, so a LAN curl `GET /mesh/api/v1/toradio` WITH A BODY
     writes a ToRadio packet (transmit / admin config) with no session once
     `api_proxy` is on. F1's class: the consumer gate covered fromradio and
     blink, not the WRITE consumer reachable by GET.
N2 — `TryBudget` is checked before `redeem` and charged after it, so N
     concurrent wrong codes from ONE client all pass the check and spend the
     shared 5-try pairing burn (F4's claim, defeated by concurrency).
N3 — the real `get_channels()` returns `[]` on failure (it never raises), so
     the "an error is never cached" claim is false: a failed radio read is
     served as "no channels" for the whole TTL.
"""
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from utils import web_auth  # noqa: E402
from utils import meshtastic_connection as mc  # noqa: E402
from utils.map_http_handler import MapRequestHandler  # noqa: E402

from test_web_auth import JSON, _origin, _req, server, store  # noqa: E402,F401


class _ToRadioProxy:
    def __init__(self):
        self.sent = []

    def send_toradio(self, data):
        self.sent.append(data)
        return True


@pytest.mark.parametrize("path", ["/mesh/api/v1/toradio", "/mesh/api/v1/toradio/x"])
def test_a_get_with_a_body_never_writes_to_the_radio(server, monkeypatch, path):
    srv, _ = server
    proxy = _ToRadioProxy()
    monkeypatch.setattr(MapRequestHandler, "api_proxy", proxy, raising=False)
    status, _, _ = _req(srv, "GET", path, b"\x0a\x02\x08\x01",
                        {"Content-Type": "application/x-protobuf"})
    assert proxy.sent == [], f"GET {path} wrote {proxy.sent!r} with no session"
    assert status in (401, 404, 405), status


def test_concurrent_wrong_codes_from_one_client_cannot_burn_the_pairing(server, store):
    srv, _ = server
    store.start_pairing()
    n = 8
    barrier = threading.Barrier(n)
    statuses = []

    def one():
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.connect()
        barrier.wait()
        c.request("POST", "/login", body=json.dumps({"code": "AAAAAAAA"}),
                  headers=dict(JSON, Origin=_origin(srv)))
        statuses.append(c.getresponse().status)
        c.close()

    ts = [threading.Thread(target=one) for _ in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert statuses.count(401) <= web_auth.LOGIN_TRIES_PER_CLIENT, statuses
    assert store.status()["pairing_pending"] is True, statuses


def test_a_failed_channel_read_is_not_cached_as_no_channels(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", ["http://127.0.0.1"])
    monkeypatch.setattr(MapRequestHandler, "collector", None, raising=False)
    monkeypatch.setattr(MapRequestHandler, "is_warming", False, raising=False)
    monkeypatch.setattr(MapRequestHandler, "_radio_channels_cache", None, raising=False)
    # the REAL reader, with only its connection failing underneath it
    conn = mc.MeshtasticConnectionManager.__new__(mc.MeshtasticConnectionManager)

    def broken(*a, **k):
        raise ConnectionRefusedError("meshtasticd restarting")
    monkeypatch.setattr(mc.MeshtasticConnectionManager, "with_connection", broken)
    monkeypatch.setattr(mc.MeshtasticConnectionManager, "get_mode", lambda self: "tcp")
    monkeypatch.setattr(MapRequestHandler, "_get_radio_connection", lambda self: conn)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _req(srv, "GET", "/api/radio/channels")
        assert MapRequestHandler._radio_channels_cache is None, \
            "a failed read was cached: " + repr(MapRequestHandler._radio_channels_cache)
    finally:
        srv.shutdown()
        srv.server_close()
