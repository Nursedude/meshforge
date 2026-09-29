"""State-changing map POSTs refuse a write a hostile PAGE could forge (2026-09-28).

Fable review F3: POST /api/radio/message checked only the client IP, then
parsed the body as JSON whatever its Content-Type. A ``text/plain`` POST is a
CORS "simple request" (no preflight), so a trusted LAN browser visiting an
attacker's page could key the radio. Pinned through a REAL HTTP server and the
real handler; only the transmitter is replaced, by a recorder that never
touches a radio.
"""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from utils import map_http_handler as mh
from utils.map_http_handler import MapRequestHandler

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", LAN)
    monkeypatch.setattr(MapRequestHandler, "collector", None, raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    sent = []

    def fake_send(text, destination=None):
        sent.append((text, destination))
        return True

    with patch("gateway.meshtastic_protobuf_client.send_text_direct", fake_send):
        yield srv, sent
    srv.shutdown()
    srv.server_close()


def _post(srv, path, body, headers):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request("POST", path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


MSG = json.dumps({"text": "csrf probe", "destination": "^all"})


def test_text_plain_from_a_hostile_page_never_keys_the_radio(server):
    srv, sent = server
    status, _ = _post(srv, "/api/radio/message", MSG,
                      {"Content-Type": "text/plain", "Origin": "http://evil.example"})
    assert status == 415
    assert sent == []


def test_json_from_a_hostile_origin_is_refused(server):
    srv, sent = server
    for origin in ("http://evil.example", "null", "http://192.0.2.evil.com"):
        status, _ = _post(srv, "/api/radio/message", MSG,
                          {"Content-Type": "application/json", "Origin": origin})
        assert status == 403, origin
    assert sent == []


def test_text_plain_without_origin_is_refused_too(server):
    srv, sent = server
    status, _ = _post(srv, "/api/radio/message", MSG, {"Content-Type": "text/plain"})
    assert status == 415
    assert sent == []


@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/json"},                        # curl / scripts
    {"Content-Type": "application/json; charset=utf-8",
     "Origin": "http://localhost:5000"},                          # the map page
    {"Content-Type": "application/json", "Origin": "http://127.0.0.1:5000"},
])
def test_legitimate_senders_still_transmit(server, headers):
    srv, sent = server
    status, body = _post(srv, "/api/radio/message", MSG, headers)
    assert status == 200, body
    assert sent == [("csrf probe", None)]


def test_same_page_reached_by_local_name_is_admitted(server):
    srv, sent = server
    port = srv.server_address[1]
    status, body = _post(srv, "/api/radio/message", MSG,
                         {"Content-Type": "application/json",
                          "Origin": f"http://moc:{port}", "Host": f"moc:{port}"})
    assert status == 200, body
    assert len(sent) == 1


@pytest.mark.parametrize("path", ["/api/settings", "/fleet/run-test"])
def test_sibling_writes_refuse_a_forged_post(server, path):
    srv, _ = server
    status, _ = _post(srv, path, json.dumps({"selected_region": None, "test": "x"}),
                      {"Content-Type": "text/plain", "Origin": "http://evil.example"})
    assert status == 415
    status, _ = _post(srv, path, json.dumps({"selected_region": None, "test": "x"}),
                      {"Content-Type": "application/json", "Origin": "http://evil.example"})
    assert status == 403


def test_the_map_page_can_still_save_settings(server):
    srv, _ = server
    status, body = _post(srv, "/api/settings", json.dumps({"selected_region": None}),
                         {"Content-Type": "application/json",
                          "Origin": "http://localhost:5000"})
    assert status == 200, body


def test_websocket_and_post_share_one_origin_rule():
    # hfm #5: two consumers, one constant — the WS gate delegates to the POST rule
    assert mh.ws_client_admitted("127.0.0.1", "http://evil.example", LAN) is False
    assert mh.browser_origin_allowed("http://evil.example", LAN) is False
    assert mh.browser_origin_allowed("http://192.0.2.7:5000", LAN) is True
