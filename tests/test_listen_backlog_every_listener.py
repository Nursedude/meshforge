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
import importlib
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


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT / "src").with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join(parts)


def _scan_sources(sources: dict, modnames: dict = None) -> list:
    """sources: label -> python text. AST finds the SHAPES (listener classes
    ACROSS all sources, stock construction, aliases, type()); the VALUE is
    measured at runtime when ``modnames`` (label -> importable module) is
    given: the class's effective ``request_queue_size`` must equal
    LISTEN_BACKLOG. Two review rounds found new AST spellings around a
    value-by-pattern check (shadow, reassignment, attribute, class-local,
    function-local import) — the seam is to ask the class (2026-11-07).
    Out of scope (stated in utils.http_listener): an __init__ override and
    raw socket.listen(); the kernel probe covers the known classes."""
    from utils.http_listener import LISTEN_BACKLOG
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
        toplevel = {n.name for n in t.body if isinstance(n, ast.ClassDef)}
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
                if n.name not in toplevel:
                    problems.append(f"{k}:{n.lineno}: listener class {n.name} is not at "
                                    "module level — cannot be imported and measured")
                elif modnames is not None:
                    mod = importlib.import_module(modnames[k])
                    size = getattr(getattr(mod, n.name), "request_queue_size", None)
                    if size != LISTEN_BACKLOG:
                        problems.append(f"{k}:{n.lineno}: class {n.name} listens with "
                                        f"request_queue_size={size}, not LISTEN_BACKLOG "
                                        f"({LISTEN_BACKLOG})")
    return problems


def _scan(src: str) -> list:
    """Shapes only (no import) — for planted construction/alias cases."""
    return _scan_sources({"<planted>": src})


def test_every_src_listener_reads_the_shared_backlog():
    # Every listener class in src/ is module-level and carries LISTEN_BACKLOG
    # (measured on the imported class); nothing constructs a stock one.
    paths = sorted((ROOT / "src").rglob("*.py"))
    sources = {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in paths}
    modnames = {str(p.relative_to(ROOT)): _module_name(p) for p in paths}
    problems = _scan_sources(sources, modnames)
    assert problems == [], "; ".join(problems)


@pytest.mark.parametrize("planted", [
    "import socketserver\ns = socketserver.TCPServer(('', 0), H)\n",
    "from http.server import HTTPServer\nS = HTTPServer\n",
    "from http.server import ThreadingHTTPServer as _TS\nS = _TS\n",
    "import socketserver\nS = type('S', (socketserver.TCPServer,), {})\n",
    "from http.server import HTTPServer as H\ns = H(('', 0), Q)\n",
    "import socketserver\ndef f():\n    class X(socketserver.TCPServer):\n"
    "        request_queue_size = 128\n",
])
def test_guard_catches_planted_shapes(planted):
    assert _scan(planted), planted


_HEAD = ("import socketserver\nfrom http.server import ThreadingHTTPServer\n"
         "from utils.http_listener import LISTEN_BACKLOG\n")


def _plant(tmp_path, monkeypatch, files: dict) -> list:
    """Write planted modules into a fresh package, import them for real."""
    pkg = f"planted_{tmp_path.name.replace('-', '_')}"
    (tmp_path / pkg).mkdir()
    (tmp_path / pkg / "__init__.py").write_text("")
    for name, text in files.items():
        (tmp_path / pkg / f"{name}.py").write_text(text)
    monkeypatch.syspath_prepend(str(tmp_path))
    return _scan_sources({f"{n}.py": t for n, t in files.items()},
                         {f"{n}.py": f"{pkg}.{n}" for n in files})


@pytest.mark.parametrize("body", [
    "class X(socketserver.TCPServer):\n    pass\n",                                  # stock 5
    "class X(socketserver.TCPServer):\n    request_queue_size = 5\n",
    "LISTEN_BACKLOG = 5\nclass X(ThreadingHTTPServer):\n"
    "    request_queue_size = LISTEN_BACKLOG\n",                                      # rebind after import
    "class X(ThreadingHTTPServer):\n    request_queue_size = LISTEN_BACKLOG\n"
    "    request_queue_size = 5\n",                                                   # later reassignment
    "class X(ThreadingHTTPServer):\n    LISTEN_BACKLOG = 5\n"
    "    request_queue_size = LISTEN_BACKLOG\n",                                      # class-body shadow
    "import types\nfoo = types.SimpleNamespace(LISTEN_BACKLOG=5)\n"
    "class X(ThreadingHTTPServer):\n    request_queue_size = foo.LISTEN_BACKLOG\n",  # attribute
])
def test_guard_measures_the_value_not_the_spelling(tmp_path, monkeypatch, body):
    problems = _plant(tmp_path, monkeypatch, {"m": _HEAD + body})
    assert len(problems) == 1 and "class X listens with request_queue_size=5" in problems[0], problems


@pytest.mark.parametrize("body", [
    "class X(ThreadingHTTPServer):\n    request_queue_size = LISTEN_BACKLOG\n",
    "class X(ThreadingHTTPServer):\n    request_queue_size: int = LISTEN_BACKLOG\n",
    "import utils.http_listener as hl\nclass X(ThreadingHTTPServer):\n"
    "    request_queue_size = hl.LISTEN_BACKLOG\n",
    "class A(ThreadingHTTPServer):\n    request_queue_size = LISTEN_BACKLOG\n"
    "class X(A):\n    pass\n",                                                       # inherits it
])
def test_guard_passes_correct_listeners_however_spelled(tmp_path, monkeypatch, body):
    assert _plant(tmp_path, monkeypatch, {"m": _HEAD + body}) == []


def test_guard_follows_a_listener_across_modules(tmp_path, monkeypatch):
    # A subclass of ANOTHER module's listener shrinking the queue is caught —
    # and only it (exact match, so the base can't mask the subclass).
    problems = _plant(tmp_path, monkeypatch, {
        "a": _HEAD + "class MapHTTPServer(ThreadingHTTPServer):\n"
                     "    request_queue_size = LISTEN_BACKLOG\n",
        "b": "from .a import MapHTTPServer\n"
             "class Y(MapHTTPServer):\n    request_queue_size = 5\n"})
    assert problems == ["b.py:2: class Y listens with request_queue_size=5, "
                        "not LISTEN_BACKLOG (128)"]


def test_metrics_stop_releases_the_bound_fd_and_joins():
    # Fable review 2026-11-07 #1: stop() used to leave the listening fd open
    # until GC (no server_close); nothing observed it.
    from utils.metrics_server import MetricsServer
    srv = MetricsServer(port=0, exporter=object())
    try:
        assert srv.start()
        sock, thread = srv._server.socket, srv._thread
    finally:
        srv.stop()
    assert sock.fileno() == -1, "stop() left the listening fd open"
    assert not thread.is_alive(), "stop() returned before serve_forever exited"
