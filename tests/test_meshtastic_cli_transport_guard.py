"""GATE: no `meshtastic` CLI call without an explicit transport (2026-10-30).

With no --host/--port the meshtastic CLI AUTO-DETECTS a serial port by USB
VID — whitelistVids = {0x239a, 0x303a} in the pinned library (util.py:39).
An RNode on an ESP32-S3 is VID 0x303a. rnsd opens that port WITHOUT an
exclusive lock (RNodeInterface.py:367), so the CLI's TCSAFLUSH +
exclusive open + wake bytes land on the RNode's KISS stream: flushed input,
DTR/RTS on open, stolen reads. The TUI audit found 11 such calls (Quick
Status, Emergency Mode send-text, channel scan, quick actions, a gateway
fallback, a dead health check). Whether one ever hit a live RNode is
UNKNOWN; that the code aims at it is not.

Every argv that starts with the meshtastic CLI must name its transport:
a literal --host/--port/--ble*, or be concatenated with
commands.meshtastic.cli_transport_args() (which resolves the configured
connection to --host or an EXPLICIT --port). Pure AST — runs in CI.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

#: Names a call site uses for the resolved CLI path (Name or attribute).
CLI_NAMES = {"cli_path", "cli", "mt_cli", "meshtastic_cli", "cli_bin"}
#: Calls that RETURN the CLI path.
CLI_GETTERS = {"get_meshtastic_cli", "find_meshtastic_cli", "_find_cli",
               "_get_emcomm_cli"}
#: Flags that need no transport (they never open a device).
NO_DEVICE = {"--version", "--help", "-h", "--support"}
TRANSPORT = {"--host", "--port", "--ble", "--ble-scan"}
#: Calls whose result IS a transport argv (or a tuple whose [0] is).
HELPERS = {"cli_transport_args", "_get_connection_args", "_emcomm_transport"}


def _callee(call):
    f = call.func
    return getattr(f, "attr", getattr(f, "id", None))


def _is_cli_head(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value == "meshtastic" or node.value.endswith("/meshtastic")
    if isinstance(node, ast.Name):
        return node.id in CLI_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in CLI_NAMES
    if isinstance(node, ast.Call):
        return _callee(node) in CLI_GETTERS
    return False


def _flags(elts):
    return {e.value for e in elts if isinstance(e, ast.Constant)
            and isinstance(e.value, str) and e.value.startswith("-")}


def _helper_vars(fn):
    """Names bound (directly or by tuple-unpack) from a transport helper."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                and _callee(n.value) in HELPERS:
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
                elif isinstance(t, ast.Tuple) and t.elts and isinstance(t.elts[0], ast.Name):
                    out.add(t.elts[0].id)
    return out


def _supplies_transport(node, helper_vars):
    """A list/tuple element, or a `+` operand, that IS a transport."""
    if isinstance(node, ast.Starred):
        node = node.value
    if isinstance(node, ast.Call) and _callee(node) in HELPERS:
        return True
    if isinstance(node, ast.Name) and node.id in helper_vars:
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return bool(_flags(node.elts) & TRANSPORT) or any(
            _supplies_transport(e, helper_vars) for e in node.elts)
    return False


def _extended_with_transport(fn, name, helper_vars):
    """`name.extend([... '--port' ...])` / `name += cli_transport_args()`."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in ("extend", "append", "insert") \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == name \
                and any(_supplies_transport(a, helper_vars) for a in n.args):
            return True
        if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) \
                and n.target.id == name and _supplies_transport(n.value, helper_vars):
            return True
    return False


def offenders(root=SRC):
    out = []
    for p in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

        def scope(n):
            while n in parents and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                n = parents[n]
            return n if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) else tree

        for n in ast.walk(tree):
            if not (isinstance(n, (ast.List, ast.Tuple)) and n.elts
                    and _is_cli_head(n.elts[0])):
                continue
            elts = list(n.elts)
            par = parents.get(n)
            # `[cli] + ['--nodes']` — judge the concatenation as one argv.
            if isinstance(par, ast.BinOp) and isinstance(par.op, ast.Add) and par.left is n:
                if _supplies_transport(par.right, _helper_vars(scope(n))):
                    continue
                if isinstance(par.right, (ast.List, ast.Tuple)):
                    elts += list(par.right.elts)
            flags = _flags(elts)
            if not flags or flags & (NO_DEVICE | TRANSPORT):
                continue
            hv = _helper_vars(scope(n))
            if any(_supplies_transport(e, hv) for e in elts[1:]):
                continue
            tgt = par.targets[0] if isinstance(par, ast.Assign) and len(par.targets) == 1 else None
            if isinstance(tgt, ast.Name) and _extended_with_transport(scope(n), tgt.id, hv):
                continue
            out.append(f"{p.relative_to(root.parent)}:{n.lineno}")
    return out


def test_no_meshtastic_cli_call_without_a_transport():
    bad = offenders()
    assert not bad, (
        "meshtastic CLI argv with NO --host/--port: the CLI will auto-detect a "
        "serial port by USB VID and can open an RNode (VID 0x303a) that rnsd "
        "holds. Build the argv with commands.meshtastic.cli_transport_args(): "
        + ", ".join(bad))


def test_guard_catches_a_planted_call(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        "import subprocess\n"
        "def f(cli_path):\n"
        "    subprocess.run([cli_path, '--nodes'])\n")
    assert offenders(tmp_path / "pkg") == ["pkg/m.py:3"]


def test_guard_accepts_the_helper_and_explicit_host(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        "from commands.meshtastic import cli_transport_args\n"
        "def f(cli_path):\n"
        "    a = [cli_path, '--nodes'] + cli_transport_args()\n"
        "    b = ['meshtastic', '--host', 'localhost', '--info']\n"
        "    c = [cli_path, '--version']\n")
    assert offenders(tmp_path / "pkg") == []


# ---- non-author review 10-30 #6/#7: the guard judges each argv, not its
# neighbourhood, and sees the other ways a CLI argv is spelled.


def _plant(tmp_path, body):
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "m.py").write_text(body)
    return offenders(tmp_path / "pkg")


def test_a_bare_call_beside_a_good_one_is_still_caught(tmp_path):
    assert _plant(tmp_path,
        "def f(cli_path, h):\n"
        "    a = [cli_path, '--host', h, '--info']\n"
        "    b = [cli_path, '--nodes']\n") == ["pkg/m.py:3"]


def test_attribute_and_call_heads_are_seen(tmp_path):
    assert _plant(tmp_path,
        "def f(self):\n"
        "    a = [self.cli_path, '--info']\n"
        "    b = [self.ctx.get_meshtastic_cli(), '--nodes']\n") == \
        ["pkg/m.py:2", "pkg/m.py:3"]


def test_concatenated_bare_call_is_caught(tmp_path):
    assert _plant(tmp_path,
        "def f(cli):\n"
        "    a = [cli] + ['--nodes']\n") == ["pkg/m.py:2"]


def test_tuple_argv_is_caught(tmp_path):
    assert _plant(tmp_path,
        "def f(cli_path):\n"
        "    a = (cli_path, '--info')\n") == ["pkg/m.py:2"]


def test_args_from_a_transport_helper_are_accepted(tmp_path):
    assert _plant(tmp_path,
        "def f(self, cli_path):\n"
        "    targs, host = self._emcomm_transport()\n"
        "    a = [cli_path, *targs, '--sendtext', 'x']\n"
        "    t2 = cli_transport_args(resolve=True)\n"
        "    b = [cli_path, *t2, '--nodes']\n") == []


def test_extend_with_explicit_port_is_accepted(tmp_path):
    assert _plant(tmp_path,
        "def f(cli_path, port, host):\n"
        "    cmd = [cli_path, '--configure', 'p']\n"
        "    if port:\n"
        "        cmd.extend(['--port', port])\n"
        "    elif host:\n"
        "        cmd.extend(['--host', host])\n") == []


def test_an_unrelated_starred_var_is_not_a_transport(tmp_path):
    assert _plant(tmp_path,
        "def f(cli_path, extra):\n"
        "    a = [cli_path, *extra, '--nodes']\n") == ["pkg/m.py:2"]


# ---- gateway CLI target: host:port, and the TX guard judges the SAME target


def _handler(host, port):
    import sys as _s
    _s.path.insert(0, str(SRC))
    from types import SimpleNamespace
    from gateway.meshtastic_handler import MeshtasticHandler
    h = MeshtasticHandler.__new__(MeshtasticHandler)
    h.config = SimpleNamespace(meshtastic=SimpleNamespace(host=host, port=port))
    return h


def test_cli_host_carries_a_non_default_port():
    assert _handler("localhost", 4403)._cli_host() == "localhost"
    assert _handler("localhost", 4404)._cli_host() == "localhost:4404"
    assert _handler("localhost", "4403")._cli_host() == "localhost"   # string port


def test_cli_host_never_builds_an_unparseable_ipv6_host_port():
    # the CLI splits --host on ':'; "::1:4404" cannot round-trip.
    assert _handler("::1", 4404)._cli_host() == "::1"


def test_tx_guard_is_asked_about_the_port_the_cli_will_use(monkeypatch):
    import gateway.meshtastic_handler as mh
    h = _handler("localhost", 4404)
    asked = {}
    monkeypatch.setattr(mh, "assert_tx_allowed",
                        lambda host, port=None, **k: asked.update(host=host, port=port))
    monkeypatch.setattr(mh.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})())
    try:
        h._send_via_cli("hi")
    except Exception:
        pass
    assert asked == {"host": "localhost", "port": 4404}


def test_emcomm_target_splits_host_port_at_the_last_colon(monkeypatch):
    import sys as _s
    _s.path[:0] = [str(SRC / "launcher_tui")]
    import commands.meshtastic as cm
    from handlers.emergency_mode import EmergencyModeHandler
    h = EmergencyModeHandler.__new__(EmergencyModeHandler)
    for val, want in [("10.0.0.5:4404", ("10.0.0.5", 4404)),
                      ("[fe80::1]:4403", ("fe80::1", 4403)),
                      ("localhost", ("localhost", 4403))]:
        monkeypatch.setattr(cm, "cli_transport_args", lambda v=val: ["--host", v])
        _args, host, port = h._emcomm_transport()
        assert (host, port) == want, val
