#!/usr/bin/env python3
"""rest_doc.py — the REST endpoint index in docs/REST_API.md, GENERATED from code.

1.0 gate 1 ("contract frozen") requires the REST doc to be regenerated from
code and test-pinned. Measured 2026-10-22: the doc still said "reviewed
against 0.4.6-beta" and 29 of its 30 endpoint headings (`/api/system/*`,
`/api/tools/*`, `/api/analytics/*`, `/api/service/*`, `/api/config/*`, ...)
were routed by NOTHING in src/ — a documented API that did not exist.

The routes are not a table; they are if/elif chains. So this reads them the
way the server does: every ``if`` whose TEST compares a ``/``-prefixed string
(``==`` / ``in`` / ``.startswith``) inside the function a server dispatches a
method through. The description is the first docstring line of the first
``self._<method>()`` the branch calls — the code that actually answers.

Usage:
    python3 scripts/rest_doc.py --check     # exit 0 current / 1 drift / 2 unknown
    python3 scripts/rest_doc.py --update    # rewrite the generated block in place
    python3 scripts/rest_doc.py --print     # print the block
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "REST_API.md"
BEGIN = "<!-- BEGIN GENERATED: scripts/rest_doc.py --update — do not edit by hand -->"
END = "<!-- END GENERATED -->"

# (title, module, {METHOD: dispatching function}, helper-module glob for docstrings)
PATH_SERVERS = [
    ("Map service — `meshforge-map`, port 5000",
     "src/utils/map_http_handler.py",
     {"GET": "_dispatch_get", "POST": "do_POST", "PUT": "do_PUT"},
     "src/utils/_map_*.py"),
    ("Prometheus exporter — default port 9090",
     "src/utils/prometheus_exporter.py",
     {"GET": "do_GET", "POST": "do_POST"},
     None),
]
CONFIG_API = ("Config API — `meshforge` daemon, 127.0.0.1:8081",
              "src/utils/config_api.py")


def _defs(paths):
    """{function name: first docstring line} over the given modules."""
    out = {}
    for p in paths:
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(n)
                if doc and n.name not in out:
                    para = " ".join(doc.strip().split("\n\n")[0].split())
                    out[n.name] = re.split(r"(?<=[.!?])\s", para, maxsplit=1)[0]
    return out


def _route_strings(test):
    """``/``-prefixed strings an if-test routes on; prefixes get a ``*``."""
    found = []
    for n in ast.walk(test):
        if isinstance(n, ast.Compare) and all(
                isinstance(op, (ast.Eq, ast.In)) for op in n.ops):
            for c in n.comparators:
                for v in ([c] if isinstance(c, ast.Constant)
                          else list(getattr(c, "elts", []))):
                    if isinstance(v, ast.Constant) and isinstance(v.value, str) \
                            and (v.value.startswith("/") or v.value == ""):
                        found.append(v.value or "/")
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "startswith" and n.args
              and isinstance(n.args[0], ast.Constant)
              and isinstance(n.args[0].value, str)
              and n.args[0].value.startswith("/")):
            found.append(n.args[0].value + "*")
    return found


def _first_self_call(body):
    for stmt in body:
        for n in ast.walk(stmt):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "self"
                    and n.func.attr.startswith("_")):
                return n.func.attr
    return None


def _routes_in(fn):
    """[(path, served_by)] for every routing branch in ``fn``."""
    rows = []
    for n in ast.walk(fn):
        if isinstance(n, ast.If):
            paths = _route_strings(n.test)
            if paths:
                by = _first_self_call(n.body)
                rows += [(p, by) for p in paths]
    return rows


def _function(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def collect():
    """[(server title, [(METHOD, path, description)])], or raise when a
    declared dispatcher is missing — a missing function is drift, never an
    empty server."""
    servers = []
    for title, mod, methods, helpers in PATH_SERVERS:
        src = ROOT / mod
        tree = ast.parse(src.read_text(encoding="utf-8"))
        docs = _defs([src] + (sorted(ROOT.glob(helpers)) if helpers else []))
        rows = {}
        for method, fname in methods.items():
            fn = _function(tree, fname)
            if fn is None:
                raise LookupError(f"{mod}: dispatcher {fname}() not found")
            for path, by in _routes_in(fn):
                # A trailing-slash twin is the same route.
                key = (method, path.rstrip("/") or "/")
                desc = docs.get(by, "") if by else ""
                if key not in rows or (desc and not rows[key]):
                    rows[key] = desc
        servers.append((title, sorted((m, p, d) for (m, p), d in rows.items())))
    title, mod = CONFIG_API
    tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))
    rows = set()
    for method in ("GET", "PUT", "DELETE", "POST"):
        fn = _function(tree, f"do_{method}")
        if fn is None:
            raise LookupError(f"{mod}: do_{method}() not found")
        for n in ast.walk(fn):
            if (isinstance(n, ast.Compare) and isinstance(n.left, ast.Name)
                    and n.left.id == "action"
                    and isinstance(n.ops[0], (ast.Eq, ast.NotEq))):
                for c in n.comparators:
                    if isinstance(c, ast.Constant) and c.value:
                        rows.add((method, "/config/{dotted.path}"
                                  if c.value == "config" else f"/config/{c.value}"))
    servers.append((title, sorted((m, p, "") for m, p in rows)))
    return servers


def render() -> str:
    lines = [BEGIN, ""]
    for title, rows in collect():
        lines += [f"### {title}", "", "| Method | Path | Served by |",
                  "|---|---|---|"]
        lines += [f"| {m} | `{p}` | {d.replace('|', '/')} |" for m, p, d in rows]
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


_BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)


def documented_headings(text: str):
    """``#### METHOD /path`` headings in the hand-written prose."""
    return re.findall(r"^#### (GET|POST|PUT|DELETE) (\S+)", text, re.M)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--update", action="store_true")
    g.add_argument("--print", action="store_true")
    a = ap.parse_args()
    try:
        block = render()
    except (LookupError, OSError, SyntaxError) as e:
        print(f"rest_doc: UNKNOWN — {e}", file=sys.stderr)
        return 2
    if a.print:
        print(block)
        return 0
    text = DOC.read_text(encoding="utf-8")
    if not _BLOCK_RE.search(text):
        print(f"rest_doc: no generated block in {DOC.name}", file=sys.stderr)
        return 2
    new = _BLOCK_RE.sub(lambda _m: block, text)
    if a.update:
        if new != text:
            DOC.write_text(new, encoding="utf-8")
            print("rest_doc: endpoint index rewritten")
        else:
            print("rest_doc: endpoint index already current")
        return 0
    if new != text:
        print("rest_doc: DRIFT — run `python3 scripts/rest_doc.py --update`")
        return 1
    print("rest_doc: endpoint index current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
