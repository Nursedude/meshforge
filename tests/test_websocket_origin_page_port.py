"""The live-updates WebSocket must accept the map page that opens it.

websockets matches Origin EXACTLY and a browser's Origin carries the page's
port, so the portless 'http://localhost' allow-list refused the :5000 map
with 403 in every browser (found 2026-09-25, journal: "connection rejected
(403 Forbidden)"). These tests do a real handshake on loopback.
"""
import asyncio
import socket

import pytest

websockets = pytest.importorskip("websockets")

from utils.websocket_server import MessageWebSocketServer  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _handshake(ws_port: int, origin: str) -> bool:
    async def go():
        try:
            async with websockets.connect(f"ws://127.0.0.1:{ws_port}/",
                                          origin=origin, open_timeout=5):
                return True
        except Exception:
            return False
    return asyncio.run(go())


@pytest.fixture
def server_factory():
    started = []

    def make(page_port=None, gate=None):
        srv = MessageWebSocketServer(port=_free_port(), page_port=page_port, gate=gate)
        assert srv.start()
        started.append(srv)
        for _ in range(50):
            if srv._server is not None:
                break
            asyncio.run(asyncio.sleep(0.05))
        return srv

    yield make
    for srv in started:
        srv.stop()


def test_map_page_origin_is_accepted(server_factory):
    srv = server_factory(page_port=5000)
    assert _handshake(srv.port, "http://localhost:5000")
    assert _handshake(srv.port, "http://127.0.0.1:5000")


def test_foreign_origin_still_refused(server_factory):
    srv = server_factory(page_port=5000)
    assert not _handshake(srv.port, "http://evil.example:5000")
    assert not _handshake(srv.port, "http://localhost:6000")


def test_without_page_port_the_map_origin_is_refused(server_factory):
    """Pins the defect: the old portless list alone cannot admit :5000."""
    srv = server_factory()
    assert not _handshake(srv.port, "http://localhost:5000")


def test_bind_stays_loopback():
    assert MessageWebSocketServer(page_port=5000).host == "127.0.0.1"


# --- gate mode: the WebSocket admits exactly who the HTTP read gate admits ---

from utils.map_http_handler import ws_client_admitted  # noqa: E402

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]


@pytest.mark.parametrize("ip,origin,allowed,expected", [
    ("192.0.2.40", "http://192.0.2.10:5000", LAN, True),     # LAN browser, map page
    ("127.0.0.1", "http://localhost:5000", LAN, True),
    ("198.51.100.7", "http://192.0.2.10:5000", LAN, False),  # outside the gate
    ("192.0.2.40", "http://evil.example", LAN, False),       # hostile page, trusted browser
    ("192.0.2.40", "http://192.0.2.evil.com:5000", LAN, False),
    ("127.0.0.1", "http://localhost:5000", None, True),      # standalone default
    ("127.0.0.1", "http://127.0.0.1:5000", None, True),
    ("192.0.2.40", "http://192.0.2.10:5000", None, False),   # no LAN opted in
    ("127.0.0.1", "", LAN, False),                            # no Origin at all
])
def test_ws_gate_matches_read_gate(ip, origin, allowed, expected):
    assert ws_client_admitted(ip, origin, allowed) is expected


def test_gate_mode_handshake(server_factory):
    seen = []

    def gate(ip, origin, host):
        seen.append((ip, origin, host))
        return origin == "http://localhost:5000"

    srv = server_factory(gate=gate)
    assert _handshake(srv.port, "http://localhost:5000")
    assert not _handshake(srv.port, "http://localhost:6000")
    assert seen and seen[0][0] == "127.0.0.1"
    assert seen[0][2] == f"127.0.0.1:{srv.port}"  # the Host header reaches the gate


def test_gate_that_raises_refuses(server_factory):
    def gate(ip, origin, host):
        raise RuntimeError("cannot decide")

    srv = server_factory(gate=gate)
    assert not _handshake(srv.port, "http://localhost:5000")


def test_stop_releases_the_port(server_factory):
    """stop() must close the listener, not abandon it with the loop."""
    srv = server_factory(page_port=5000)
    port = srv.port
    srv.stop()
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))  # raises EADDRINUSE if still listening


# --- the map opened by HOSTNAME (2026-09-25: moc IP origin 101, hostname 403) ---

@pytest.mark.parametrize("ip,origin,host,expected", [
    ("192.0.2.40", "http://moc:5000", "moc:5001", True),              # fleet alias
    ("192.0.2.40", "http://MOC:5000", "moc:5001", True),              # case
    ("192.0.2.40", "http://kiai.local:5000", "kiai.local:5001", True),
    ("192.0.2.40", "http://n.home.arpa:5000", "n.home.arpa:5001", True),
    ("192.0.2.40", "http://wh6gxz-6-n.local.mesh:5000", "wh6gxz-6-n.local.mesh:5001", True),
    # DNS rebinding: an attacker controls a PUBLIC name's resolution
    ("192.0.2.40", "http://evil.example:5000", "evil.example:5001", False),
    ("192.0.2.40", "http://moc.evil.example:5000", "moc.evil.example:5001", False),
    # hostile page reaching the box by its real name
    ("192.0.2.40", "http://evil:5000", "moc:5001", False),
    ("192.0.2.40", "http://moc:6000", "moc:5001", False),             # not the map port
    ("192.0.2.40", "https://moc:5000", "moc:5001", False),            # map is http
    ("192.0.2.40", "http://moc:5000/x", "moc:5001", False),
    ("192.0.2.40", "http://moc:5000", "", False),                     # no Host header
    ("192.0.2.40", "http://1234:5000", "1234:5001", False),           # all-digit label
    # the IP gate still comes first
    ("198.51.100.7", "http://moc:5000", "moc:5001", False),
])
def test_ws_gate_admits_the_map_by_local_hostname(ip, origin, host, expected):
    assert ws_client_admitted(ip, origin, LAN, request_host=host,
                              page_port=5000) is expected


def test_hostname_rule_needs_the_page_port():
    """Without page_port the same-host rule cannot fire (fail closed)."""
    assert not ws_client_admitted("192.0.2.40", "http://moc:5000", LAN,
                                  request_host="moc:5001")
