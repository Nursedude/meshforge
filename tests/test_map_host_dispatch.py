"""Every route refuses a request addressed to a rebinding name (2026-09-28, #2).

Fable re-review: the F2 Host rule lived in ``_reject_if_untrusted`` only, so 15
ungated GETs (node roster/positions, /fleet/slo, /fleet/uplink, channel names)
still answered a DNS-rebinding page. The rule now runs at dispatch for every
verb; ``/healthz`` and ``/metrics`` are the only exemptions.

The route list is EXTRACTED from the dispatch source, so a route added later is
swept without anyone remembering to list it. Dispatch is replaced by a
recorder: a refused request must never reach a handler, and no test here reads
live state.
"""
import http.client
import inspect
import re
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from utils.map_http_handler import MapRequestHandler

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]
EVIL = "evil.example:5000"
EXEMPT = {"/healthz", "/metrics"}


def _routes(fn):
    src = inspect.getsource(fn)
    return sorted({m for m in re.findall(r"['\"](/[A-Za-z0-9_./-]*)['\"]", src)
                   if len(m) > 1 and not m.endswith(".")})


GET_ROUTES = _routes(MapRequestHandler._dispatch_get)
POST_ROUTES = _routes(MapRequestHandler.do_POST)


def test_the_sweep_actually_found_the_routes():
    # guard the guard: an extraction that finds nothing would pass vacuously
    assert len(GET_ROUTES) >= 30, GET_ROUTES
    for must in ("/api/nodes/geojson", "/fleet/slo", "/fleet/uplink",
                 "/api/radio/channels", "/api/messages/received", "/healthz"):
        assert any(r == must or r.startswith(must) for r in GET_ROUTES), must
    assert "/api/radio/message" in POST_ROUTES


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", LAN)
    calls = []

    def fake_dispatch(self, path_only):
        calls.append(path_only)
        self._serve_json({"ok": True})

    monkeypatch.setattr(MapRequestHandler, "_dispatch_get", fake_dispatch)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sent = []
    with patch("gateway.meshtastic_protobuf_client.send_text_direct",
               lambda text, destination=None: sent.append(text) or True):
        yield srv, calls, sent
    srv.shutdown()
    srv.server_close()


def _req(srv, method, path, host, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    try:
        c.putrequest(method, path, skip_host=True)
        c.putheader("Host", host)
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        if body is not None:
            c.putheader("Content-Length", str(len(body)))
        c.endheaders(body.encode() if body else None)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


@pytest.mark.parametrize("path", GET_ROUTES)
def test_every_get_route_refuses_a_rebinding_host(server, path):
    srv, calls, _ = server
    status, body = _req(srv, "GET", path, EVIL)
    if path in EXEMPT:
        assert status == 200 and calls == [path], (path, status)
    else:
        assert status == 403 and b"rebinding" in body, (path, status)
        assert calls == [], f"{path} reached dispatch"


@pytest.mark.parametrize("path", POST_ROUTES)
def test_every_post_route_refuses_a_rebinding_host(server, path):
    srv, _, sent = server
    status, _ = _req(srv, "POST", path, EVIL, body='{"text":"x"}',
                     headers={"Content-Type": "application/json"})
    assert status == 403, (path, status)
    assert sent == []


@pytest.mark.parametrize("method", ["PUT", "HEAD", "OPTIONS"])
def test_other_verbs_refuse_too(server, method):
    srv, _, _ = server
    status, _ = _req(srv, method, "/api/v1/toradio" if method == "PUT" else "/", EVIL)
    assert status == 403, method


@pytest.mark.parametrize("host", ["127.0.0.1:5000", "localhost:5000", "moc:5000",
                                  "moc.mf.internal:5000", "node.local", "[::1]:5000",
                                  "192.0.2.41:5000"])
def test_legitimate_hosts_reach_dispatch(server, host):
    srv, calls, _ = server
    status, _ = _req(srv, "GET", "/api/nodes/geojson", host)
    assert status == 200 and calls == ["/api/nodes/geojson"], host


def test_absolute_form_target_is_judged_by_its_path(server):
    # "GET http://evil.example/fleet/slo" with a benign Host must not dodge the
    # exemption list by path confusion; a benign Host is admitted, a public one not
    srv, calls, _ = server
    assert _req(srv, "GET", "http://evil.example/fleet/slo", "127.0.0.1:5000")[0] == 200
    assert _req(srv, "GET", "http://127.0.0.1/fleet/slo", EVIL)[0] == 403
    assert calls == ["/fleet/slo"]
