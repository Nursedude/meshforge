"""1.0 gate 1: the REST doc is generated from code and test-pinned (2026-10-22).

Measured before this file: docs/REST_API.md said "reviewed against 0.4.6-beta"
and 29 of its 30 `#### METHOD /path` headings were routed by no server in
src/. These tests make that class fail: the generated endpoint index must equal
the generator's output, and every hand-written endpoint heading must name a
route the code actually serves.
"""
from __future__ import annotations

import importlib.util
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
    return {(m, p) for _t, rows in mod.collect() for m, p, _d in rows}


def _is_served(served, method, path):
    if (method, path) in served:
        return True
    return any(m == method and p.endswith("*") and path.startswith(p[:-1])
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
    title, src, methods, helpers = mod.PATH_SERVERS[0]
    monkeypatch.setattr(mod, "PATH_SERVERS",
                        [(title, src, {"GET": "_no_such_dispatcher"}, helpers)])
    monkeypatch.setattr(sys, "argv", ["rest_doc.py", "--check"])
    assert mod.main() == 2
