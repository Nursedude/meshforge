#!/usr/bin/env python3
"""rest_doc.py — the REST endpoint index in docs/REST_API.md, GENERATED from code.

1.0 gate 1 ("contract frozen") requires the REST doc to be regenerated from
code and test-pinned. Measured 2026-10-22: the doc still said "reviewed
against 0.4.6-beta" and 29 of its 30 endpoint headings (`/api/system/*`,
`/api/tools/*`, `/api/analytics/*`, `/api/service/*`, `/api/config/*`, ...)
were routed by NOTHING in src/ — a documented API that did not exist.

The routes are not a table; they are if/elif chains. So this reads them the
way the server does: every ``do_<VERB>`` method the handler defines (never a
hardcoded verb list — the config API's PATCH was missed that way), and in it
every ``if`` whose TEST compares a ``/``-prefixed string (``==`` / ``in`` /
``.startswith``). The description is the first docstring sentence of the code
the branch calls. The Login column re-derives the map's BIC-3 gate from the
gate's own constants (``OPERATOR_GETS`` / ``SESSIONLESS_WRITES``).

What this CANNOT see — a routing shape other than those (a tuple
``startswith``, ``'/x' in path``, a regex, a dispatch dict) — is caught by
``unaccounted_literals()``: every ``/``-prefixed literal in a dispatcher must
be an extracted route or listed in ``NOT_ROUTES`` with a reason.

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
SKIP_VERBS = ("HEAD", "OPTIONS")  # answer on every path; rendered as notes

# (key, title, module, {VERB: dispatcher override}, helper-module glob)
PATH_SERVERS = [
    ("map", "Map service — `meshforge-map`, port 5000",
     "src/utils/map_http_handler.py", {"GET": "_dispatch_get"},
     "src/utils/_map_*.py"),
    ("prometheus", "Prometheus exporter — default port 9090",
     "src/utils/prometheus_exporter.py", {}, None),
]
CONFIG_API = ("config", "Config API — `meshforge` daemon, 127.0.0.1:8081",
              "src/utils/config_api.py")
AUTH_MODULE = "src/utils/_map_auth_endpoints.py"

# Routed by us, never answered upstream: meshtasticd serves no /json/nodes or
# /json/report (ESP32-only, persistent_issues #76). A test keeps each entry a
# live route, so a removed proxy cannot leave a stale warning behind.
UPSTREAM_NEVER_SERVES = {
    "/json/nodes": "meshtasticd never serves it (ESP32-only, #76) — expect 404",
    "/json/report": "meshtasticd never serves it (ESP32-only, #76) — expect 404",
}

# `/`-prefixed literals inside a dispatcher that are NOT routes. Anything else
# unaccounted for is a routing shape this generator cannot read.
NOT_ROUTES = {
    "/": "rstrip('/') argument / trailing-slash normalisation",
    "/index.html": "same branch as `/` (the map page)",
}


def _first_sentence(doc: str) -> str:
    para = " ".join(doc.strip().split("\n\n")[0].split())
    return re.split(r"(?<=[.!?])\s", para, maxsplit=1)[0]


def _defs(paths):
    """{function name: first docstring sentence} over the given modules."""
    out = {}
    for p in paths:
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(n)
                if doc and n.name not in out:
                    out[n.name] = _first_sentence(doc)
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


def _served_by(body):
    """(kind, name) of the code a branch hands the request to: the first
    ``self._x()`` call, else a function imported inside the branch."""
    imported = {}
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.ImportFrom) and n.module:
                for a in n.names:
                    imported[a.asname or a.name] = (n.module, a.name)
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call):
                f = n.func
                if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id == "self" and f.attr.startswith("_")):
                    return ("self", f.attr)
                if isinstance(f, ast.Name) and f.id in imported:
                    return ("import", imported[f.id])
    return (None, None)


def _describe(by, docs):
    kind, name = by
    if kind == "self":
        return docs.get(name, "")
    if kind == "import":
        mod, fn = name
        p = ROOT / "src" / (mod.replace(".", "/") + ".py")
        return _defs([p]).get(fn, "") if p.is_file() else ""
    return ""


def _api_call(body):
    """("self", name) for the first ``self.api.<name>()`` in ``body`` — the
    ConfigurationAPI method a config-API branch hands the request to."""
    for stmt in body:
        for n in ast.walk(stmt):
            f = getattr(n, "func", None)
            if (isinstance(n, ast.Call) and isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Attribute)
                    and f.value.attr == "api"):
                return ("self", f.attr)
    return (None, None)


def _functions(tree):
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _dispatchers(tree, overrides):
    """{VERB: dispatcher function} — every do_<VERB> the handler defines."""
    fns = _functions(tree)
    out = {}
    for name in sorted(fns):
        if name.startswith("do_") and name[3:].isupper():
            verb = name[3:]
            if verb in SKIP_VERBS:
                continue
            target = overrides.get(verb, name)
            if target not in fns:
                raise LookupError(f"dispatcher {target}() not found")
            out[verb] = fns[target]
    for verb, target in overrides.items():
        if verb not in out:
            raise LookupError(f"override for {verb} names no do_{verb}()")
    return out


def _frozenset_const(tree, name):
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in n.targets)
                and isinstance(n.value, ast.Call) and n.value.args
                and isinstance(n.value.args[0], ast.Set)):
            return {e.value for e in n.value.args[0].elts
                    if isinstance(e, ast.Constant)}
    raise LookupError(f"{AUTH_MODULE}: {name} not found")


def _map_login(method, path, operator_gets, sessionless):
    """The BIC-3 gate, re-derived: a GET needs a session only when it is a
    side-effect GET; every other write needs one unless sessionless."""
    base = path.rstrip("*")
    if method == "GET":
        return "operator" if base in operator_gets else "—"
    return "—" if base in sessionless else "operator"


def collect():
    """[(key, title, rows, notes)] where rows are (METHOD, path, login, desc).
    Raises LookupError when a declared dispatcher or gate constant is
    missing — a missing function is drift, never an empty server."""
    auth_tree = ast.parse((ROOT / AUTH_MODULE).read_text(encoding="utf-8"))
    operator_gets = _frozenset_const(auth_tree, "OPERATOR_GETS")
    sessionless = _frozenset_const(auth_tree, "SESSIONLESS_WRITES")
    servers = []
    for key, title, mod, overrides, helpers in PATH_SERVERS:
        src = ROOT / mod
        tree = ast.parse(src.read_text(encoding="utf-8"))
        docs = _defs([src] + (sorted(ROOT.glob(helpers)) if helpers else []))
        rows = {}
        for method, fn in _dispatchers(tree, overrides).items():
            for n in ast.walk(fn):
                if not isinstance(n, ast.If):
                    continue
                for path in _route_strings(n.test):
                    k = (method, path.rstrip("/") or "/")
                    desc = _describe(_served_by(n.body), docs)
                    if k[1] in UPSTREAM_NEVER_SERVES:
                        desc = f"{desc} ⚠️ {UPSTREAM_NEVER_SERVES[k[1]]}".strip()
                    if k not in rows or (desc and not rows[k]):
                        rows[k] = desc
        login = (lambda m, p: _map_login(m, p, operator_gets, sessionless)) \
            if key == "map" else (lambda m, p: "—")
        out_rows = sorted((m, p, login(m, p), d) for (m, p), d in rows.items())
        fns = _functions(tree)
        notes = []
        for verb in SKIP_VERBS:
            fn = fns.get(f"do_{verb}")
            if fn is not None and ast.get_docstring(fn):
                notes.append(f"`{verb}` on any path: "
                             f"{_first_sentence(ast.get_docstring(fn))}")
        disp = _dispatchers(tree, overrides).get("GET")
        if disp is not None and "super().do_GET()" in ast.unparse(disp):
            notes.append("Any other `GET` path falls through to the stdlib "
                         "static-file server over `web/`.")
        servers.append((key, title, out_rows, notes))
    key, title, mod = CONFIG_API
    tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))
    docs = _defs([ROOT / mod])
    rows = set()
    for method, fn in _dispatchers(tree, {}).items():
        for n in ast.walk(fn):
            if not isinstance(n, ast.If):
                continue
            cmp = next((c for c in ast.walk(n.test)
                        if isinstance(c, ast.Compare)
                        and isinstance(c.left, ast.Name)
                        and c.left.id == "action"
                        and isinstance(c.ops[0], (ast.Eq, ast.NotEq))), None)
            if cmp is None:
                continue
            # `action == X` → X's own branch; `action != X` is a guard
            # (`action != "config" or not config_path`), so the code that
            # answers is the rest of the function.
            scope = n.body if isinstance(cmp.ops[0], ast.Eq) else fn.body
            for c in cmp.comparators:
                if isinstance(c, ast.Constant) and c.value:
                    path = ("/config/{dotted.path}" if c.value == "config"
                            else f"/config/{c.value}")
                    rows.add((method, path, "—", _describe(
                        _api_call(scope), docs)))
    servers.append((key, title, sorted(rows), []))
    return servers


def unaccounted_literals():
    """[(module, function, literal)] — `/`-prefixed string constants inside a
    dispatcher that are neither an extracted route nor in NOT_ROUTES. Each one
    is a routing shape the generator cannot read (non-empty = blind spot)."""
    found = []
    for key, _title, mod, overrides, _h in PATH_SERVERS:
        tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))
        for _verb, fn in _dispatchers(tree, overrides).items():
            routed = set()
            for n in ast.walk(fn):
                if isinstance(n, ast.If):
                    for p in _route_strings(n.test):
                        routed.add(p.rstrip("*"))
                        routed.add(p.rstrip("*").rstrip("/") or "/")
            for n in ast.walk(fn):
                if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and n.value.startswith("/")
                        and n.value not in routed
                        and n.value.rstrip("/") not in routed
                        and n.value not in NOT_ROUTES):
                    found.append((mod, fn.name, n.value))
    return sorted(set(found))


def render() -> str:
    lines = [BEGIN, ""]
    for key, title, rows, notes in collect():
        lines += [f"### {title}", ""]
        if key == "map":
            lines += ["| Method | Path | Login | Served by |", "|---|---|---|---|"]
            lines += [f"| {m} | `{p}` | {lg} | {d.replace('|', '/')} |"
                      for m, p, lg, d in rows]
        else:
            lines += ["| Method | Path | Served by |", "|---|---|---|"]
            lines += [f"| {m} | `{p}` | {d.replace('|', '/')} |"
                      for m, p, _lg, d in rows]
        if notes:
            lines.append("")
            lines += [f"- {n}" for n in notes]
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


_BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)


def documented_headings(text: str):
    """``#### METHOD /path`` headings in the hand-written prose."""
    return re.findall(r"^#### (GET|POST|PUT|PATCH|DELETE) (\S+)", text, re.M)


def prose_paths(text: str):
    """Every route-shaped path the HAND-WRITTEN prose names (backticked or in a
    URL), the generated block excluded — what a reader would try to call."""
    prose = _BLOCK_RE.sub("", text)
    paths = set()
    for m in re.finditer(r"`(/[A-Za-z0-9_./{}*-]+)`", prose):
        paths.add(m.group(1))
    for m in re.finditer(r"https?://[^\s/`]+(/[A-Za-z0-9_./{}-]*)", prose):
        if m.group(1) not in ("", "/"):
            paths.add(m.group(1))
    return sorted(p for p in paths if not p.startswith(("/etc/", "/var/", "/opt/")))


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
    try:
        text = DOC.read_text(encoding="utf-8")
    except OSError as e:
        print(f"rest_doc: UNKNOWN — cannot read {DOC}: {e}", file=sys.stderr)
        return 2
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
