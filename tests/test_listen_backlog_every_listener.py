"""H13 follow-up (MAP-H, 2026-11-07): EVERY first-party listener queues a burst.

H13 (b60166c5) moved the map's accept queue off Python's default of 5 and
queued two siblings still on it: ConfigAPIServer (:8081, TUI) and
MetricsServer (:9090, bridge/launcher). Both are single-threaded TCPServers,
so with backlog 5 a burst of scrapes/clients beyond 6 pays a full 1 s SYN
retry before the server even sees it.

The cure is ONE constant (``utils.http_listener.LISTEN_BACKLOG``) every
listener class reads (hfm #5), and a repo-wide guard so a NEW listener cannot
arrive on the default with every test green.

Behaviour is measured with the same kernel probe as test_map_http_backlog:
bind without serving and count the handshakes the kernel completes.
"""
from __future__ import annotations

import ast
import socketserver
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from listen_backlog_probe import (  # noqa: E402,F401
    BURST, STOCK_LISTENERS, STREAM_LISTENERS, _completed_connects, nm as _nm, pytestmark)


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):  # pragma: no cover - never served
        pass


def test_shared_constant_is_the_maps_value():
    from utils.http_listener import LISTEN_BACKLOG
    from utils.map_data_service import MapHTTPServer
    assert LISTEN_BACKLOG >= BURST
    assert MapHTTPServer.request_queue_size == LISTEN_BACKLOG


def test_config_api_listener_queues_a_whole_burst():
    from utils.config_api import ConfigAPITCPServer
    srv = ConfigAPITCPServer(("127.0.0.1", 0), _Quiet)
    try:
        # Unchanged by the fix: still single-threaded, still SO_REUSEADDR.
        assert not isinstance(srv, socketserver.ThreadingMixIn)
        assert srv.allow_reuse_address
        assert _completed_connects(srv) == BURST
    finally:
        srv.server_close()


def test_metrics_listener_queues_a_whole_burst():
    from utils.metrics_server import MetricsTCPServer
    srv = MetricsTCPServer(("127.0.0.1", 0), _Quiet)
    try:
        assert not isinstance(srv, socketserver.ThreadingMixIn)
        assert not srv.allow_reuse_address  # unchanged: stock TCPServer had False
        assert _completed_connects(srv) == BURST
    finally:
        srv.server_close()


def test_config_api_start_binds_through_the_backlog_class():
    from utils.config_api import ConfigAPIServer, ConfigAPITCPServer
    srv = ConfigAPIServer(api=None, host="127.0.0.1", port=0)
    try:
        assert srv.start()
        assert isinstance(srv._server, ConfigAPITCPServer)
    finally:
        srv.stop()


def test_metrics_start_binds_through_the_backlog_class():
    from utils.metrics_server import MetricsServer, MetricsTCPServer
    srv = MetricsServer(port=0, exporter=object())
    try:
        assert srv.start()
        assert isinstance(srv._server, MetricsTCPServer)
    finally:
        srv.stop()


def _aliases(tree) -> dict:
    """local name -> imported name, so `ThreadingHTTPServer as _TS` resolves."""
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.asname:
                    out[a.asname] = a.name
    return out


def _resolved(node, aliases):
    name = _nm(node)
    return aliases.get(name, name)


def _queue_size_from_constant(cls: ast.ClassDef) -> bool:
    for stmt in cls.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else \
            [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
        if any(_nm(t) == "request_queue_size" for t in targets):
            return _nm(stmt.value) == "LISTEN_BACKLOG"
    return False


def _imports_the_shared_constant(tree) -> bool:
    # The NAME alone is forgeable: a module-local `LISTEN_BACKLOG = 5` reads
    # right to the class check (Fable review 2026-11-07 #3).
    return any(isinstance(n, ast.ImportFrom) and n.module == "utils.http_listener"
               and any(a.name == "LISTEN_BACKLOG" and a.asname is None for a in n.names)
               for n in ast.walk(tree))


def _scan_sources(sources: dict) -> list:
    """sources: label -> python text. Listener classes are found ACROSS all of
    them (a subclass of another module's listener is still a listener)."""
    trees = {k: ast.parse(v) for k, v in sources.items()}
    aliases = {k: _aliases(t) for k, t in trees.items()}
    listeners, changed = set(STREAM_LISTENERS), True
    while changed:
        changed = False
        for k, t in trees.items():
            for n in ast.walk(t):
                if isinstance(n, ast.ClassDef) and n.name not in listeners and \
                        {_resolved(b, aliases[k]) for b in n.bases} & listeners:
                    listeners.add(n.name)
                    changed = True
    defined = listeners - STREAM_LISTENERS
    problems = []
    for k, t in trees.items():
        al = aliases[k]
        for n in ast.walk(t):
            if isinstance(n, ast.Call):
                fn = _resolved(n.func, al)
                if fn in STOCK_LISTENERS:
                    problems.append(f"{k}:{n.lineno}: stock {fn}() constructed")
                elif fn == "type" and len(n.args) >= 2 and isinstance(n.args[1], ast.Tuple) \
                        and {_resolved(e, al) for e in n.args[1].elts} & (listeners | STOCK_LISTENERS):
                    problems.append(f"{k}:{n.lineno}: listener class built with type()")
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                v = n.value
                if isinstance(v, (ast.Name, ast.Attribute)) and _resolved(v, al) in STOCK_LISTENERS:
                    problems.append(f"{k}:{n.lineno}: stock listener aliased")
            elif isinstance(n, ast.ClassDef) and n.name in defined:
                if not _queue_size_from_constant(n):
                    problems.append(f"{k}:{n.lineno}: class {n.name} must set "
                                    "request_queue_size = LISTEN_BACKLOG")
                elif not _imports_the_shared_constant(t):
                    problems.append(f"{k}:{n.lineno}: class {n.name} reads a LISTEN_BACKLOG "
                                    "not imported from utils.http_listener")
    return problems


def _scan(src: str) -> list:
    return _scan_sources({"<planted>": src})


def test_every_src_listener_reads_the_shared_backlog():
    # A listener anywhere in src/ is a stream-server subclass that sets
    # request_queue_size = LISTEN_BACKLOG itself; nothing constructs a stock one.
    # Scope (utils.http_listener): socketserver classes; raw socket.listen() is not.
    sources = {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8")
               for p in sorted((ROOT / "src").rglob("*.py"))}
    problems = _scan_sources(sources)
    assert problems == [], "; ".join(problems)


@pytest.mark.parametrize("planted", [
    "import socketserver\ns = socketserver.TCPServer(('', 0), H)\n",
    "from http.server import HTTPServer\nS = HTTPServer\n",
    "import socketserver\nclass X(socketserver.TCPServer):\n    pass\n",
    "import socketserver\nclass X(socketserver.TCPServer):\n    request_queue_size = 5\n",
    "class A(ThreadingHTTPServer):\n    request_queue_size = LISTEN_BACKLOG\n"
    "class B(A):\n    request_queue_size = 4\n",
    # review 2026-11-07 (#2): alias, dynamic type(), aliased construction
    "from http.server import ThreadingHTTPServer as _TS\nclass X(_TS):\n    pass\n",
    "import socketserver\nS = type('S', (socketserver.TCPServer,), {})\n",
    "from http.server import HTTPServer as H\ns = H(('', 0), Q)\n",
])
def test_guard_catches_planted_violations(planted):
    assert _scan(planted), planted


_IMPORT = "from utils.http_listener import LISTEN_BACKLOG\n"


def test_guard_passes_the_sanctioned_shape():
    assert _scan(_IMPORT + "class A(ThreadingHTTPServer):\n    request_queue_size = LISTEN_BACKLOG\n"
                 "class B(A):\n    request_queue_size: int = LISTEN_BACKLOG\n") == []


def test_guard_refuses_a_local_constant_of_the_same_name():
    assert _scan("LISTEN_BACKLOG = 5\nclass A(ThreadingHTTPServer):\n"
                 "    request_queue_size = LISTEN_BACKLOG\n") == \
        ["<planted>:2: class A reads a LISTEN_BACKLOG not imported from utils.http_listener"]


def test_metrics_stop_releases_the_bound_fd_and_joins():
    # Fable review 2026-11-07 #1: stop() used to leave the listening fd open
    # until GC (no server_close); nothing observed it.
    import socket
    from utils.metrics_server import MetricsServer
    srv = MetricsServer(port=0, exporter=object())
    assert srv.start()
    sock = srv._server.socket
    port = sock.getsockname()[1]
    thread = srv._thread
    srv.stop()
    assert sock.fileno() == -1, "stop() left the listening fd open"
    assert not thread.is_alive(), "stop() returned before serve_forever exited"
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))  # rebindable now, not after GC
    finally:
        s.close()


def test_guard_follows_a_listener_across_modules():
    # review 2026-11-07 (#2): a subclass of ANOTHER module's listener shrinking
    # the queue must be caught (MapHTTPServer imported elsewhere).
    srcs = {"a.py": "from utils.http_listener import LISTEN_BACKLOG\n"
                    "class MapHTTPServer(ThreadingHTTPServer):\n"
                    "    request_queue_size = LISTEN_BACKLOG\n",
            "b.py": "from a import MapHTTPServer\n"
                    "class Y(MapHTTPServer):\n    request_queue_size = 5\n"}
    assert _scan_sources(srcs) == ["b.py:2: class Y must set request_queue_size = LISTEN_BACKLOG"]
