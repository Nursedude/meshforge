"""ONE read of the RNS config file for every scanner (WITNESS-TUI r11 seam 1).

r9/r10 made the INTERFACE reader read the file the way rnsd does (bytes,
``readlines()`` — split on b"\\n" only —, UTF-8 BOM stripped, utf-8). Every
OTHER scanner of the same file still read text its own way
(``read_text()``: universal newlines turn a lone ``\\r`` into a ghost line,
a BOM rides in front of ``[reticulum]``, non-UTF-8 raises an untyped
``UnicodeDecodeError`` through the caller). Fable r10 #2: a BOM config read
"share_instance not enabled" and a false shadow diff.

The cure is one pair of functions in ``utils.rns_config_reader`` —
``config_text`` / ``config_lines`` / ``read_config_text`` — and this file
pushes one corpus of byte shapes through EVERY consumer: a shape rnsd reads
the same as the plain file must give every consumer the plain file's
verdict; a non-UTF-8 file must never raise through a consumer.
"""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import rns_config_reader as rcr

KEY = "ab" * 32
BASE = (
    b"[reticulum]\n"
    b"  share_instance = Yes\n"
    b"  shared_instance_type = tcp\n"
    b"  instance_name = lab\n"
    b"  enable_transport = No\n"
    b"  rpc_key = " + KEY.encode() + b"\n"
    b"  loglevel = 5\n"
    b"\n"
    b"[interfaces]\n"
    b"  [[Mesh]]\n"
    b"    type = Meshtastic_Interface\n"
    b"    enabled = yes\n"
)
BOM = b"\xef\xbb\xbf"
# rnsd reads "  # moved\r[ghost]" as ONE comment line; read_text() / splitlines()
# made "[ghost]" a section header that ended [reticulum] early.
_GHOST = b"[reticulum]\n  # moved\r[ghost]\n"

SAME_AS_PLAIN = {
    "plain": BASE,
    "bom": BOM + BASE,
    "crlf": BASE.replace(b"\n", b"\r\n"),
    "bom_crlf": BOM + BASE.replace(b"\n", b"\r\n"),
    "lone_cr": BASE.replace(b"[reticulum]\n", _GHOST, 1),
    "bom_lone_cr": BOM + BASE.replace(b"[reticulum]\n", _GHOST, 1),
}
NOT_UTF8 = BASE.replace(b"[interfaces]\n", b"# caf\xe9\n[interfaces]\n", 1)


# ── the seam itself ─────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", sorted(SAME_AS_PLAIN))
def test_config_lines_are_rnsds_lines(shape):
    lines = rcr.config_lines(SAME_AS_PLAIN[shape])
    assert lines[0] == "[reticulum]"                     # BOM gone
    assert not any(line.endswith("\r") for line in lines)
    assert "[ghost]" not in lines                        # no ghost line
    assert rcr.config_lines(SAME_AS_PLAIN[shape].decode("utf-8")) == lines


def test_config_text_strips_bom_from_str_and_bytes():
    assert rcr.config_text(BOM + b"[a]\n") == "[a]\n"
    assert rcr.config_text("\ufeff[a]\n") == "[a]\n"
    assert rcr.config_text("[a]\r\n") == "[a]\r\n"      # no newline translation


def test_config_text_not_utf8_lenient_by_default_strict_refuses_typed():
    assert "caf" in rcr.config_text(NOT_UTF8)            # a scan never aborts
    with pytest.raises(rcr.RNSConfigError):
        rcr.config_text(NOT_UTF8, strict=True)           # a writer refuses, typed


def test_read_config_text_reads_bytes(tmp_path):
    p = tmp_path / "config"
    p.write_bytes(SAME_AS_PLAIN["bom_lone_cr"])
    text = rcr.read_config_text(p)
    assert not text.startswith("\ufeff") and "\r[ghost]" in text
    with pytest.raises(OSError):
        rcr.read_config_text(tmp_path / "missing")


def test_lone_surrogate_str_is_typed_not_unicode_encode_error():
    with pytest.raises(rcr.RNSConfigError):
        rcr.read_interfaces("[interfaces]\n[[A]]\nname = \udc80\n")
    with pytest.raises(rcr.RNSConfigError):
        rcr._read_strict("[interfaces]\n[[A]]\nname = \udc80\n")


# ── every consumer, one corpus ──────────────────────────────────────────


def _cfg(tmp_path, monkeypatch, raw):
    from commands import rns
    from utils.paths import ReticulumPaths
    p = tmp_path / "config"
    p.write_bytes(raw)
    monkeypatch.setattr(rns, "get_config_path", lambda: p)
    monkeypatch.setattr(ReticulumPaths, "get_config_file", classmethod(lambda cls: p))
    return p


def _verdicts(tmp_path, monkeypatch, raw):
    """Every scanner's answer over one file."""
    from commands import rns
    from utils.config_drift import _parse_reticulum_section
    from utils.paths import ReticulumPaths
    from launcher_tui.handlers import _config_doctor_checks as cd
    from launcher_tui.handlers import _rns_diagnostics_engine as E
    p = _cfg(tmp_path, monkeypatch, raw)
    rc = rns.read_config()
    content = rc.data.get("content", "")
    return {
        "content_has_bom": content.startswith("\ufeff"),
        "share_instance(read_config)": rns._parse_share_instance(content),
        "share_instance(bytes)": rns._parse_share_instance(raw),
        "validate_config": rns.validate_config(content),
        "drift_section": {k: v for k, v in _parse_reticulum_section(raw).items()
                          if k != "interface_count"},
        "tcp_warning": E.shared_instance_type_is_tcp(rcr.read_config_text(p)),
        "rpc_key(doctor)": cd._read_rpc_key(p),
        "rpc_key(paths)": ReticulumPaths.get_shared_rpc_key(),
        "instance_name": ReticulumPaths.get_configured_instance_name(),
    }


@pytest.mark.parametrize("shape", sorted(SAME_AS_PLAIN))
def test_every_consumer_gives_the_plain_files_verdict(shape, tmp_path, monkeypatch):
    plain = _verdicts(tmp_path, monkeypatch, BASE)
    assert plain["share_instance(read_config)"] is True and plain["tcp_warning"] is True
    assert plain["rpc_key(paths)"] == KEY and plain["instance_name"] == "lab"
    assert _verdicts(tmp_path, monkeypatch, SAME_AS_PLAIN[shape]) == plain


def test_not_utf8_never_raises_through_a_consumer(tmp_path, monkeypatch):
    v = _verdicts(tmp_path, monkeypatch, NOT_UTF8)
    assert v["share_instance(bytes)"] is True
    assert v["rpc_key(paths)"] == KEY and v["instance_name"] == "lab"


def test_drift_counts_interfaces_from_bytes():
    from utils.config_drift import _parse_reticulum_section
    assert _parse_reticulum_section(BOM + BASE)["interface_count"] == 1
    assert _parse_reticulum_section(NOT_UTF8)["interface_count"] is None


def _shadow(tmp_path, monkeypatch, etc_raw, user_raw):
    from utils import config_drift
    etc = tmp_path / "etc_config"
    etc.write_bytes(etc_raw)
    home = tmp_path / "home"
    (home / ".reticulum").mkdir(parents=True)
    (home / ".reticulum" / "config").write_bytes(user_raw)
    monkeypatch.setattr(config_drift, "ETC_RNS_CONFIG", etc)
    monkeypatch.setattr(config_drift, "get_real_user_home", lambda: home)
    return config_drift.detect_config_shadowing()


@pytest.mark.parametrize("shape", sorted(SAME_AS_PLAIN))
def test_shadow_diff_same_file_shapes_no_false_difference(shape, tmp_path, monkeypatch):
    res = _shadow(tmp_path, monkeypatch, SAME_AS_PLAIN[shape], BASE)
    assert res.shadowed is True and res.differences == []


def test_shadow_diff_not_utf8_is_reported_not_swallowed(tmp_path, monkeypatch):
    res = _shadow(tmp_path, monkeypatch, NOT_UTF8, BASE)
    assert res.shadowed is True
    assert any("UNKNOWN (config not loadable)" in d for d in res.differences)


def test_nomadnet_subtitle_never_raises_on_not_utf8(tmp_path, monkeypatch):
    from launcher_tui.handlers._nomadnet_iface_checks import NomadNetIfaceChecksMixin
    for raw in (NOT_UTF8, BOM + BASE):
        _cfg(tmp_path, monkeypatch, raw)
        me = SimpleNamespace(_meshtastic_blockers=lambda: [("Mesh", "no meshtasticd")])
        out = NomadNetIfaceChecksMixin._mesh_iface_subtitle_state(me)
        assert out.startswith("Meshtastic iface: BLOCKED")


def _diag(tmp_path, raw, blocking=()):
    """run_rns_diagnostics end to end over ``raw`` (None = no config file);
    every external call mocked."""
    import contextlib
    import io
    import logging
    from unittest.mock import MagicMock, patch
    from utils.paths import ReticulumPaths
    from launcher_tui.handlers import _rns_diagnostics_engine as E
    cfg = tmp_path / "config"
    if raw is not None:
        cfg.write_bytes(raw)
    h = MagicMock()
    h._find_blocking_interfaces = lambda: list(blocking)
    h._wait_for_rns_shared_instance = lambda max_wait=10: False
    run = MagicMock(return_value=SimpleNamespace(returncode=1, stdout="", stderr=""))
    buf = io.StringIO()
    logging.disable(logging.CRITICAL)
    try:
        with patch.object(ReticulumPaths, "get_config_file", return_value=cfg), \
             patch.object(ReticulumPaths, "get_config_dir", return_value=tmp_path), \
             patch("commands.rns.get_config_path", return_value=cfg), \
             patch("utils.config_drift.ETC_RNS_CONFIG", tmp_path / "no_etc"), \
             patch("subprocess.run", run), \
             patch("subprocess.Popen", MagicMock(side_effect=OSError("no"))), \
             patch("socket.socket", MagicMock(side_effect=OSError("no sockets"))), \
             patch("builtins.input", return_value=""), \
             contextlib.redirect_stdout(buf):
            try:
                E.run_rns_diagnostics(h)
            except SystemExit:
                pass
    finally:
        logging.disable(logging.NOTSET)
    return buf.getvalue()


def _leg(out, n):
    """The text of diagnostics leg [n/6]."""
    head = f"[{n}/6]"
    rest = out[out.index(head):]
    nxt = rest.find(f"[{n + 1}/6]")
    return rest if nxt < 0 else rest[:nxt]


def test_diagnostics_run_completes_over_a_not_utf8_config(tmp_path):
    """Step 2c read_text() aborted the whole run on non-UTF-8 (Fable r10 NIT)."""
    out = _diag(tmp_path, NOT_UTF8)
    assert "[5/6]" in out and "[6/6]" in out
    assert "shared_instance_type = tcp" in out


def test_diagnostics_missing_config_never_says_dependencies_met(tmp_path):
    out = _diag(tmp_path, None)
    leg5 = _leg(out, 5)
    assert "dependencies met" not in leg5
    assert "UNKNOWN — not checked: no RNS config found" in leg5
    assert "Interfaces enabled: UNKNOWN" in _leg(out, 4)


def test_diagnostics_unloadable_config_counted_once_and_unknown_in_leg4(tmp_path):
    out = _diag(tmp_path, BASE + b"garbage {{{\n")
    assert "Interfaces enabled: UNKNOWN" in _leg(out, 4)
    issues = out[out.index("--- Issues Found"):]
    issues = issues[:issues.find("\n---", 5)] if "\n---" in issues[5:] else issues
    assert issues.count("not loadable") == 1, issues


def test_diagnostics_zero_enabled_interfaces_is_not_all_met(tmp_path):
    out = _diag(tmp_path, BASE.replace(b"enabled = yes", b"enabled = no"))
    leg5 = _leg(out, 5)
    assert "dependencies met" not in leg5
    assert "No enabled interfaces — nothing to check" in leg5


def test_diagnostics_loadable_config_with_enabled_interface_still_judged(tmp_path):
    out = _diag(tmp_path, BASE)
    assert "All enabled interfaces have their dependencies met" in _leg(out, 5)
    out = _diag(tmp_path, BASE, blocking=[("Mesh", "meshtasticd not running", "start it")])
    assert "! [Mesh] meshtasticd not running" in _leg(out, 5)


# ── r12: the readers r11 did not move (found by the closed inventory) ───
#
# Each takes the config as a PATH ARGUMENT (or lives in the standalone
# rns_config_utils.py), so the r11 AST gate — keyed on get_config_file —
# never saw them (Fable r11 #2: 1 of 12 planted shapes seen).

_GHOST_KEY = (b"[reticulum]\n  # old\rrpc_key = " + (b"cd" * 32)
              + b"\n  # old\rinstance_name = ghost\n")
_NO_KEY = BASE.replace(b"  rpc_key = " + KEY.encode() + b"\n", b"") \
              .replace(b"  instance_name = lab\n", b"")


def _path_readers(p):
    from commands.meshtastic import _rns_declared_ports
    from utils import gateway_flow_audit, rns_alignment, rns_init
    return {
        "flow_audit._read_rpc_key": gateway_flow_audit._read_rpc_key(p),
        "alignment._read_rpc_key_value": rns_alignment._read_rpc_key_value(p),
        "alignment._scan_config_file": (lambda f: (f.has_rpc_key, f.instance_name))(
            rns_alignment._scan_config_file(p)),
        "rns_init._read_instance_name": rns_init._read_instance_name_from_config(p.parent),
        "meshtastic._rns_declared_ports": sorted(_rns_declared_ports([p]).values()),
    }


@pytest.mark.parametrize("shape", sorted(SAME_AS_PLAIN))
def test_path_arg_readers_give_the_plain_files_verdict(shape, tmp_path):
    def run(raw):
        p = tmp_path / "config"
        # (the port line lands inside every shape, CRLF included)
        p.write_bytes(raw.replace(b"    enabled = yes", b"    enabled = yes\n    port = /dev/ttyFAKE0"))
        return _path_readers(p)
    plain = run(BASE)
    assert plain["flow_audit._read_rpc_key"] == KEY
    assert plain["rns_init._read_instance_name"] == "lab"
    assert plain["meshtastic._rns_declared_ports"] == [str(tmp_path / "config")]
    assert run(SAME_AS_PLAIN[shape]) == plain


def test_path_arg_readers_never_read_a_lone_cr_comment_as_live(tmp_path):
    p = tmp_path / "config"
    p.write_bytes(_NO_KEY.replace(b"[reticulum]\n", _GHOST_KEY, 1))
    v = _path_readers(p)
    assert v["flow_audit._read_rpc_key"] is None
    assert v["alignment._read_rpc_key_value"] is None
    assert v["alignment._scan_config_file"] == (False, None)
    assert v["rns_init._read_instance_name"] is None


def test_path_arg_readers_never_raise_on_not_utf8(tmp_path):
    p = tmp_path / "config"
    p.write_bytes(NOT_UTF8)
    v = _path_readers(p)
    assert v["flow_audit._read_rpc_key"] == KEY
    assert v["rns_init._read_instance_name"] == "lab"


def _rnsd_view(p):
    """What rnsd loads from ``p`` — its own parser is the oracle."""
    configobj = pytest.importorskip("RNS.vendor.configobj")
    c = configobj.ConfigObj(str(p))
    return {"reticulum": dict(c["reticulum"]), "interfaces": sorted(c.get("interfaces", {}))}


def _add_iface(p):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import rns_config_utils
    return rns_config_utils.add_interface_to_config(
        p, "  [[Added]]\n    type = AutoInterface\n    enabled = yes\n", "Added")


def _restore(tmp_path, monkeypatch, backup_raw):
    from commands import rns
    target = tmp_path / "live" / "config"
    target.parent.mkdir()
    target.write_bytes(BASE)
    monkeypatch.setattr(rns, "get_config_path", lambda: target)
    b = tmp_path / "config.bak"
    b.write_bytes(backup_raw)
    return rns.restore_backup(str(b)), target


@pytest.mark.parametrize("shape", ["lone_cr", "bom_lone_cr", "bom_crlf"])
def test_writers_keep_rnsds_view_of_every_line_they_did_not_mean_to_change(shape, tmp_path, monkeypatch):
    raw = SAME_AS_PLAIN[shape]
    p = tmp_path / "config"
    p.write_bytes(raw)
    before = _rnsd_view(p)
    assert _add_iface(p)["success"] is True
    after = _rnsd_view(p)
    assert after["reticulum"] == before["reticulum"]        # no ghost section ended it
    assert after["interfaces"] == sorted(before["interfaces"] + ["Added"])
    res, target = _restore(tmp_path, monkeypatch, raw)
    assert res.success is True
    assert _rnsd_view(target) == before                     # restored = what was backed up


def test_writers_refuse_not_utf8_and_write_nothing(tmp_path, monkeypatch):
    p = tmp_path / "config"
    p.write_bytes(NOT_UTF8)
    r = _add_iface(p)
    assert r["success"] is False and "UTF-8" in r["error"]
    assert p.read_bytes() == NOT_UTF8
    res, target = _restore(tmp_path, monkeypatch, NOT_UTF8)
    assert res.success is False and "UTF-8" in res.message
    assert target.read_bytes() == BASE


# ── r12 (Fable r11 NIT #7): the three strict writers REFUSE ─────────────
# Each read with strict=True; with nothing pinning it, the lenient mutant
# (strict dropped) survived — and wrote U+FFFD back over the bytes it could
# not decode. Each test feeds a non-UTF-8 config the writer WOULD change.

_NOT_UTF8_WRITABLE = (b"[reticulum]\n  share_instance = No\n  loglevel = 4\n# caf\xe9\n"
                      b"[interfaces]\n  [[Mesh]]\n    type = AutoInterface\n    enabled = yes\n")


def _patched_cfg(tmp_path, monkeypatch):
    from utils.paths import ReticulumPaths
    p = tmp_path / "config"
    p.write_bytes(_NOT_UTF8_WRITABLE)
    monkeypatch.setattr(ReticulumPaths, "get_config_file", classmethod(lambda cls: p))
    return p


def test_strict_writer_disable_interfaces_refuses(tmp_path, monkeypatch):
    from launcher_tui.handlers._rns_interface_mgr import disable_interfaces_in_config
    p = _patched_cfg(tmp_path, monkeypatch)
    assert disable_interfaces_in_config(["Mesh"]) == []
    assert p.read_bytes() == _NOT_UTF8_WRITABLE


def test_strict_writer_loglevel_refuses(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch
    from launcher_tui.handlers.rns_config import RNSConfigHandler
    p = _patched_cfg(tmp_path, monkeypatch)
    h = RNSConfigHandler.__new__(RNSConfigHandler)
    h.ctx = MagicMock()
    h.ctx.dialog.menu.return_value = "6"
    with patch("utils.paths.atomic_write_text_preserving") as w:
        h._configure_rns_logging()
    w.assert_not_called()
    assert p.read_bytes() == _NOT_UTF8_WRITABLE
    title, body = h.ctx.dialog.msgbox.call_args[0][:2]
    assert title == "Error" and "UnicodeDecodeError" in body


def test_strict_writer_share_instance_preflight_refuses(tmp_path, monkeypatch, capsys):
    from unittest.mock import MagicMock
    from launcher_tui.handlers import _rns_repair as R
    p = _patched_cfg(tmp_path, monkeypatch)
    ctx = MagicMock()
    ctx.dialog.yesno.return_value = True
    w = MagicMock(return_value=(True, "ok"))
    monkeypatch.setattr(R, "_sudo_write", w)
    R._preflight_share_instance(ctx)
    w.assert_not_called()
    ctx.dialog.yesno.assert_not_called()
    assert p.read_bytes() == _NOT_UTF8_WRITABLE
    assert "share_instance: UNKNOWN" in capsys.readouterr().out


# ── the gate: a CLOSED inventory of every raw file read (r12) ─────────
#
# r11 keyed on functions calling get_config_file/get_config_path — the wrong
# quantity (Fable r11 #2: of 12 planted shapes it saw 1). r12 inventories
# EVERY raw read call in src/ (+ rns_config_utils.py) with a class; any new
# site fails until a human says which file it reads. tests/_file_read_inventory.py

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _file_read_inventory as fri  # noqa: E402


def test_every_raw_file_read_is_inventoried_and_classified():
    probs = fri.problems(fri.sites(), fri.load())
    assert probs == [], "\n".join(probs)


def test_rns_config_class_is_reserved_for_byte_reads():
    for key, entry in fri.load().items():
        if entry["class"] == "rns-config":
            assert key.rsplit("::", 1)[1] in fri.RNS_CONFIG_KINDS, key
            assert entry["audited"], key


# Every shape the r11 gate could not see, plus the plain one it could.
_PLANTED = {
    "plain": "def f(P):\n    return P.get_config_file().read_text()\n",
    "path_arg": "def f(path):\n    return path.read_text()\n",
    "open_builtin": "def f(p):\n    with open(p) as fh:\n        return fh.read()\n",
    "path_open": "def f(p):\n    with p.open() as fh:\n        return fh.read()\n",
    "io_open": "import io\ndef f(p):\n    return io.open(p).read()\n",
    "codecs_open": "import codecs\ndef f(p):\n    return codecs.open(p).read()\n",
    "bytes_decode": "def f(p):\n    return p.read_bytes().decode()\n",
    "sudo_cat": "import subprocess\ndef f(p):\n    return subprocess.run(['sudo', '-n', 'cat', str(p)], timeout=5).stdout\n",
    "configobj": "from RNS.vendor.configobj import ConfigObj\ndef f(p):\n    return ConfigObj(str(p))\n",
    "fdopen": "import os\ndef f(fd):\n    return os.fdopen(fd).read()\n",
    "module_level": "from pathlib import Path\nTEXT = Path('/etc/reticulum/config').read_text()\n",
    "method": "class C:\n    def f(self, p):\n        return p.read_text()\n",
    "nested": "def f(p):\n    def g():\n        return p.read_text()\n    return g()\n",
    "lambda": "def f(p):\n    return (lambda q: q.read_text())(p)\n",
    # r13 (Fable r12 #1/#3): argv readers beyond cat.
    "sudo_grep": "import subprocess\ndef f(p):\n    return subprocess.run(['sudo', '-n', 'grep', '-E', '^rpc_key', str(p)], timeout=5)\n",
    "sudo_head": "import subprocess\ndef f(p):\n    return subprocess.run(('head', '-n', '5', str(p)), timeout=5)\n",
    "cat_text": "import subprocess\ndef f(p):\n    return subprocess.run(['sudo', 'cat', str(p)], text=True, timeout=5)\n",
}


@pytest.mark.parametrize("shape", sorted(_PLANTED))
def test_gate_fires_on_every_planted_reader_shape(shape, tmp_path):
    """A guard that never failed is not evidence: plant each shape in a COPY
    of the tree layout and the inventory must refuse it, naming the remedy."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "planted.py").write_text(_PLANTED[shape], encoding="utf-8")
    found = fri.sites(tmp_path)
    assert found, f"{shape}: the scan did not see the read"
    probs = fri.problems(found, {})
    assert probs and all("NEW read site src/planted.py::" in p for p in probs), probs
    assert "read_config_text" in probs[0]


def test_gate_refuses_a_raised_count_and_a_stale_entry():
    inv = {"src/a.py::f::read_text": {"class": "other", "audited": "x", "count": 1},
           "src/b.py::g::open": {"class": "other", "audited": "x", "count": 1}}
    probs = fri.problems({"src/a.py::f::read_text": 2}, inv)
    assert any(p.startswith("MORE read sites in src/a.py::f::read_text") for p in probs)
    assert any(p.startswith("STALE inventory entry src/b.py::g::open") for p in probs)


def test_gate_refuses_rns_config_class_on_a_text_read():
    inv = {"src/a.py::f::read_text": {"class": "rns-config", "audited": "x", "count": 1}}
    probs = fri.problems({"src/a.py::f::read_text": 1}, inv)
    assert any("read BYTES and decode via the seam" in p for p in probs)


def test_gate_refuses_unclassified():
    inv = {"src/a.py::f::open": {"class": "UNCLASSIFIED", "audited": "", "count": 1}}
    assert any("must classify" in p for p in fri.problems({"src/a.py::f::open": 1}, inv))


@pytest.mark.parametrize("kind", ["grep", "head", "tail", "cat+text"])
def test_gate_refuses_rns_config_class_on_a_line_or_text_argv_read(kind):
    # r13: a sudo grep/head sees lines its own way and a text=True cat decodes
    # before the seam — neither may be classed as reading the RNS config.
    key = f"src/a.py::f::{kind}"
    inv = {key: {"class": "rns-config", "audited": "x", "count": 1}}
    assert any("read BYTES and decode via the seam" in p
               for p in fri.problems({key: 1}, inv))


def test_cat_kind_reads_text_kwarg():
    import ast
    call = ast.parse("run(['cat', p], text=True)").body[0].value
    assert fri._kind(call) == "cat+text"
    call = ast.parse("run(['cat', p], text=False)").body[0].value
    assert fri._kind(call) == "cat"
