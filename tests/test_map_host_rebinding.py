"""The trusted-read gate refuses a DNS-rebinding page (2026-09-28, Fable F2).

A LAN browser on ``http://evil.example:5000`` whose name the attacker re-points
at this box makes SAME-ORIGIN reads from the victim's trusted address; the IP
gate admits them. The Host header is the one thing the attacker cannot choose,
so the gate also requires it to be an IP literal or a local-only name. Pinned
through a real HTTP server and the real dispatch; the messages DB is stubbed so
a test never reads the box's real messages (2026-09-23 class).
"""
import http.client
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils import map_http_handler as mh
from utils.map_http_handler import MapRequestHandler

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]
_EMPTY = SimpleNamespace(success=True, data={"messages": []})


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", LAN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with patch("utils.map_http_handler._HAS_MSG_QUEUE", False), \
         patch("utils.map_http_handler.messaging.get_messages", return_value=_EMPTY) as gm:
        yield srv, gm
    srv.shutdown()
    srv.server_close()


def _get(srv, path, host):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    try:
        if host is None:
            c.putrequest("GET", path, skip_host=True)
        else:
            c.putrequest("GET", path, skip_host=True)
            c.putheader("Host", host)
        c.endheaders()
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


@pytest.mark.parametrize("host", ["evil.example:5000", "evil.example",
                                  "192.0.2.7.evil.example:5000", "moc.example.com",
                                  "", "a:b:c"])
def test_rebinding_host_is_refused_before_any_read(server, host):
    srv, gm = server
    status, body = _get(srv, "/api/messages/received", host)
    assert status == 403, (host, body)
    assert b"rebinding" in body or b"forbidden" in body
    gm.assert_not_called()


@pytest.mark.parametrize("host", [None, "127.0.0.1:5000", "localhost:5000", "localhost",
                                  "moc:5000", "moc.mf.internal:5000", "node.local",
                                  "[::1]:5000", "192.0.2.41:5000", "LOCALHOST."])
def test_legitimate_hosts_still_read(server, host):
    srv, gm = server
    status, body = _get(srv, "/api/messages/received", host)
    assert status == 200, (host, body)


def test_the_gate_covers_every_trusted_endpoint_not_just_messages(server):
    # the check lives in _reject_if_untrusted itself, so logs/queue/run-test inherit it
    srv, _ = server
    assert _get(srv, "/api/messages/queue", "evil.example:5000")[0] == 403


def test_untrusted_ip_still_refused_first():
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {"Host": "127.0.0.1:5000"}
    h.client_address = ("203.0.113.9", 5000)
    h.allowed_origins = LAN
    served = []
    h._serve_json = lambda payload, status=200: served.append((status, payload))
    assert h._reject_if_untrusted() is True
    assert served[0][0] == 403 and served[0][1]["client"] == "203.0.113.9"


def test_host_rule_matches_the_websocket_local_name_rule():
    # hfm #5: one vocabulary of local-only names for both gates
    for name in ("moc", "x.local", "x.home.arpa", "x.internal", "x.local.mesh"):
        assert mh._host_header_trusted(name + ":5000") is mh._local_only_name(name) is True
    assert mh._host_header_trusted("evil.example:5000") is mh._local_only_name("evil.example") is False


def test_standalone_map_with_no_lan_origins_still_checks_host(monkeypatch):
    # a loopback-only map (no --cors-origins) is the "browser ON the box"
    # rebinding case — the Host rule must not depend on a LAN being set
    # (Fable re-review 2026-09-28 #4, mutant M2 survived)
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with patch("utils.map_http_handler._HAS_MSG_QUEUE", False), \
             patch("utils.map_http_handler.messaging.get_messages", return_value=_EMPTY) as gm:
            assert _get(srv, "/api/messages/received", "evil.example:5000")[0] == 403
            gm.assert_not_called()
            assert _get(srv, "/api/messages/received", "127.0.0.1:5000")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("name", ["x.internal.evil.example", "x.local.evil.example",
                                  "evil.local.mesh.example.com", "x.home.arpa.example"])
def test_a_local_suffix_in_the_middle_is_not_local(name):
    # suffixes match at the END only (Fable re-review #4, mutant M4 survived)
    assert mh._local_only_name(name) is False
    assert mh._host_header_trusted(name + ":5000") is False
