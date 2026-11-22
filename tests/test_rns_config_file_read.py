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


# ── the gate: no hand scanner reads the RNS config as text again ────────

SRC = Path(__file__).resolve().parent.parent / "src"
# Functions that call the RNS config-path helper AND read_text() on a
# DIFFERENT file (each checked by hand, 2026-11-22).
_OTHER_FILE = {
    ("launcher_tui/handlers/_rns_repair.py", "validate_rnsd_service_file"),  # the unit file
    ("launcher_tui/handlers/nomadnet.py", "_nomadnet_status"),               # NomadNet's own config
}


def _rns_text_readers():
    hits = set()
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).as_posix()
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = set()
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    if isinstance(n.func, ast.Attribute):
                        names.add(n.func.attr)
                    elif isinstance(n.func, ast.Name):
                        names.add(n.func.id)
            rns_path = ("get_config_file" in names
                        or ("get_config_path" in names and not rel.startswith("gateway/")))
            if rns_path and "read_text" in names:
                hits.add((rel, fn.name))
    return hits


def test_no_rns_config_text_reader_left():
    assert _rns_text_readers() - _OTHER_FILE == set()


def test_gate_still_sees_a_planted_reader(tmp_path, monkeypatch):
    """The scan must FIRE on the shape it guards (a guard that never failed)."""
    planted = tmp_path / "src" / "planted.py"
    planted.parent.mkdir()
    planted.write_text("def f(P):\n    return P.get_config_file().read_text()\n")
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path / "src")
    assert _rns_text_readers() == {("planted.py", "f")}


@pytest.mark.parametrize("rel", [
    "utils/config_drift.py",
    "launcher_tui/handlers/_rns_diagnostics_engine.py",
    "launcher_tui/handlers/_config_doctor_checks.py",
])
def test_path_arg_scanners_have_no_read_text_on_the_config(rel):
    """These take the config as a path argument (the AST gate can't see them)."""
    text = (SRC / rel).read_text(encoding="utf-8")
    for bad in ("etc_config.read_text()", "user_config.read_text()",
                "config_path.read_text()"):
        assert bad not in text, f"{rel}: {bad}"
