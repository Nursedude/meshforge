"""H13 (MAP-H, 2026-11-06): the map's HTTP listener must queue a burst of connects.

Measured on the manager: Python's ``ThreadingHTTPServer`` keeps
``request_queue_size = 5``. With 50 simultaneous requests, ~8 waited a full
1 s TCP SYN retry before the server ever saw them (``time_connect`` 1.01-1.04 s,
connect->first byte <= 30 ms) — a cost no handler change can remove.

The probe binds the server WITHOUT serving: the kernel completes a handshake
only while the accept queue has room, so the number of connects that finish
within 0.5 s is the backlog (+1), not anything the app does.
"""
from __future__ import annotations

import ast
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from listen_backlog_probe import BURST, STOCK_LISTENERS, _completed_connects, nm as _nm, needs_accept_queue_probe  # noqa: E402,F401


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):  # pragma: no cover - never served
        pass


class _Backlog5(ThreadingHTTPServer):
    # The control pins THE PROBE, not CPython: an explicit backlog of 5 must
    # read as a dropped burst, whatever default a future interpreter ships.
    request_queue_size = 5


@needs_accept_queue_probe
def test_control_a_backlog_of_five_drops_a_burst():
    srv = _Backlog5(("127.0.0.1", 0), _Quiet)
    try:
        assert _completed_connects(srv) < BURST // 2
    finally:
        srv.server_close()


@needs_accept_queue_probe
def test_map_server_queues_a_whole_burst():
    from utils import map_data_service as mds
    srv = mds._make_http_server("127.0.0.1", 0)
    try:
        # The backlog must not cost what the listener already had: one daemon
        # thread per request (a slow endpoint never blocks the rest; stop()
        # never waits on a hung handler) and HTTPServer's bind (server_name /
        # server_port the handler reads). Review 2026-11-06: an HTTPServer base,
        # daemon_threads=False or a ThreadingTCPServer base all passed without it.
        assert isinstance(srv, ThreadingHTTPServer)
        assert isinstance(srv, socketserver.ThreadingMixIn)
        assert srv.daemon_threads is True
        assert _completed_connects(srv) == BURST
    finally:
        srv.server_close()


_STOCK_LISTENERS = STOCK_LISTENERS


def _is_server_target(t) -> bool:
    return isinstance(t, ast.Attribute) and t.attr == "_server"


def _from_factory_or_none(v) -> bool:
    return v is None or (isinstance(v, ast.Constant) and v.value is None) or \
        (isinstance(v, ast.Call) and _nm(v.func) == "_make_http_server")


def test_every_map_listener_is_built_by_the_one_factory():
    # Two copies of the construction drifted apart once already (both kept the
    # default). The behavioural test measures _make_http_server, so this guard
    # makes that factory the ONLY road to self._server — otherwise a start path
    # that avoids it (a local subclass, an alias, a MapHTTPServer subclass that
    # shrinks the queue) regresses the live listener with every test green
    # (round-2 review, CONFIRMED by mutation 2026-11-06).
    src = (ROOT / "src" / "utils" / "map_data_service.py").read_text(encoding="utf-8")
    problems = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and _nm(n.func) in _STOCK_LISTENERS:
            problems.append(f"line {n.lineno}: stock listener constructed")
        elif isinstance(n, ast.ClassDef):
            bases = {_nm(b) for b in n.bases}
            if bases & _STOCK_LISTENERS and n.name != "MapHTTPServer":
                problems.append(f"line {n.lineno}: class {n.name} derives from a stock listener")
            if "MapHTTPServer" in bases:
                problems.append(f"line {n.lineno}: class {n.name} subclasses MapHTTPServer")
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if isinstance(n.value, (ast.Name, ast.Attribute)) and _nm(n.value) in _STOCK_LISTENERS:
                problems.append(f"line {n.lineno}: stock listener aliased")
            if any(_is_server_target(t) for t in targets) and not _from_factory_or_none(n.value):
                problems.append(f"line {n.lineno}: self._server not from _make_http_server()")
    assert problems == [], "build the map listener only via _make_http_server(): " + "; ".join(problems)
