"""1.0 gate 1: the REST doc is generated from code and test-pinned (2026-10-22).

Measured before this file: docs/REST_API.md said "reviewed against 0.4.6-beta"
and 29 of its 30 `#### METHOD /path` headings were routed by no server in
src/. These tests make that class fail: the generated endpoint index must equal
the generator's output, and every hand-written endpoint heading must name a
route the code actually serves.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "rest_doc.py"


def _load():
    spec = importlib.util.spec_from_file_location("rest_doc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _served(mod):
    return {(m, p) for _k, _t, rows, _n in mod.collect() for m, p, _l, _d in rows}


def _matches(route, path):
    """A route row covers `path`: exact, a `*` prefix, or `{param}` segments."""
    if route == path:
        return True
    if route.endswith("*"):
        return path.startswith(route[:-1])
    if "{" in route:
        pat = "^" + re.sub(r"\\\{[^}]*\\\}", r"[^?#]+", re.escape(route)) + "$"
        return re.match(pat, path) is not None
    return False


def _is_served(served, method, path):
    return any((method is None or m == method) and _matches(p, path)
               for m, p in served)


def test_generated_index_is_current(monkeypatch):
    mod = _load()
    monkeypatch.setattr(sys, "argv", ["rest_doc.py", "--check"])
    assert mod.main() == 0, "run: python3 scripts/rest_doc.py --update"


def test_every_documented_endpoint_is_served():
    mod = _load()
    served = _served(mod)
    text = mod.DOC.read_text(encoding="utf-8")
    ghosts = [f"{m} {p}" for m, p in mod.documented_headings(text)
              if not _is_served(served, m, p)]
    assert not ghosts, f"documented but routed by nothing: {ghosts}"


def test_every_path_the_prose_names_is_served():
    # Non-author pass 2026-10-22: the heading check alone went vacuous on the
    # rewritten doc (no headings left) while the auth prose and the curl
    # examples still name routes. Every path a reader would try must exist.
    mod = _load()
    served = _served(mod)
    paths = mod.prose_paths(mod.DOC.read_text(encoding="utf-8"))
    assert len(paths) >= 5, f"prose check went vacuous: {paths}"
    ghosts = [p for p in paths if not _is_served(served, None, p)]
    assert not ghosts, f"prose names routes nothing serves: {ghosts}"


def test_prose_check_catches_a_planted_ghost():
    mod = _load()
    assert "/api/system/status" in mod.prose_paths(
        "Call `/api/system/status` for health.")
    assert not _is_served(_served(mod), None, "/api/system/status")


def test_no_routing_shape_the_generator_cannot_read():
    mod = _load()
    assert mod.unaccounted_literals() == []


def test_unread_routing_shape_is_caught(tmp_path, monkeypatch):
    mod = _load()
    (tmp_path / "srv.py").write_text(
        "class H:\n"
        "    def do_GET(self):\n"
        "        if self.path == '/seen':\n"
        "            self._a()\n"
        "        elif self.path.startswith(('/tuple-a', '/tuple-b')):\n"
        "            self._b()\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "PATH_SERVERS",
                        [("t", "t", "srv.py", {}, None)])
    got = {lit for _m, _f, lit in mod.unaccounted_literals()}
    assert got == {"/tuple-a", "/tuple-b"}


def test_every_verb_the_handler_defines_is_indexed():
    # do_PATCH was missed by a hardcoded verb list (non-author pass).
    assert ("PATCH", "/config/{dotted.path}") in _served(_load())


def test_login_column_rederives_the_bic3_gate():
    mod = _load()
    rows = {(m, p): lg for k, _t, rs, _n in mod.collect() if k == "map"
            for m, p, lg, _d in rs}
    assert rows[("GET", "/fleet/logs")] == "operator"
    assert rows[("GET", "/json/blink")] == "operator"
    assert rows[("GET", "/api/status")] == "—"
    assert rows[("POST", "/api/inbox/send")] == "operator"
    assert rows[("POST", "/login")] == "—"


def test_upstream_never_serves_entries_are_live_routes():
    mod = _load()
    served = _served(mod)
    for path in mod.UPSTREAM_NEVER_SERVES:
        assert ("GET", path) in served, path


def test_missing_doc_is_unknown(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "DOC", tmp_path / "nope.md")
    monkeypatch.setattr(sys, "argv", ["rest_doc.py", "--check"])
    assert mod.main() == 2


def test_index_covers_the_real_servers():
    mod = _load()
    served = _served(mod)
    for route in [("GET", "/api/status"), ("GET", "/healthz"),
                  ("POST", "/api/inbox/send"), ("GET", "/metrics"),
                  ("GET", "/config/{dotted.path}")]:
        assert route in served, route


def test_planted_drift_is_caught(tmp_path, monkeypatch):
    mod = _load()
    doc = tmp_path / "REST_API.md"
    doc.write_text("x\n" + mod.BEGIN + "\n| GET | `/stale` | |\n" + mod.END + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(mod, "DOC", doc)
    monkeypatch.setattr(sys, "argv", ["rest_doc.py", "--check"])
    assert mod.main() == 1


def test_missing_dispatcher_is_unknown_not_empty(monkeypatch):
    mod = _load()
    key, title, src, _over, helpers = mod.PATH_SERVERS[0]
    monkeypatch.setattr(mod, "PATH_SERVERS",
                        [(key, title, src, {"GET": "_no_such_dispatcher"}, helpers)])
    monkeypatch.setattr(sys, "argv", ["rest_doc.py", "--check"])
    assert mod.main() == 2
