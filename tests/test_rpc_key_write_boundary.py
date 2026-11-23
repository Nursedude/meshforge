"""WITNESS-TUI r13 — ONE check at the rpc_key WRITE boundary.

Fable r12 (#1, #2) found every rpc_key pinner guessing the config's
grammar with its own regex/grep/awk: an inline comment on the key, a
``[reticulum]  # c`` header, ``[ reticulum ]``, ``["reticulum"]``, a quoted
key, an empty value, a commented placeholder — each made a pinner append a
second key/section (rnsd: DuplicateError) or "pin" a comment, while the
repair flow printed "Pinned a fresh rpc_key".

The cure is not a better regex. Before ANY pinner writes, the NEW bytes are
loaded the way rnsd loads them and must (a) load, (b) carry
``reticulum.rpc_key`` == the key the pinner reports, (c) differ from the
old view in nothing else. Otherwise it refuses and writes nothing. The
oracle here is rnsd's own ConfigObj when RNS is importable, and each test
runs over BOTH reader paths (RNS ConfigObj + the strict fallback CI uses).
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import rns_config_reader as rcr  # noqa: E402
from utils.rns_config_reader import RNSConfigError  # noqa: E402
from utils.rns_config_setup import (  # noqa: E402
    PLACEHOLDER, ensure_rpc_key, is_valid_key, pin_rpc_key_bytes,
    render_template,
)

HEX = "0123456789abcdef" * 4
BOM = b"\xef\xbb\xbf"

BASE = (
    "[reticulum]\n"
    "  share_instance = Yes\n"
    "  instance_name = lab\n"
    "\n"
    "[interfaces]\n"
    "  [[Default Interface]]\n"
    "    type = AutoInterface\n"
    "    enabled = yes\n"
)


def _with(header=None, key_line=None, base=BASE):
    text = base
    if header is not None:
        text = text.replace("[reticulum]", header, 1)
    if key_line is not None:
        text = text.replace("  instance_name = lab\n",
                            "  instance_name = lab\n" + key_line + "\n", 1)
    return text.encode()


# (id, bytes, expected): "pin" = a fresh key must land; "keep:<v>" = the
# existing value is reported and nothing is written; "refuse" = RNSConfigError,
# nothing written.
SHAPES = [
    ("nokey", _with(), "pin"),
    ("bom-nokey", BOM + _with(), "pin"),
    ("indented-header", _with(header="  [reticulum]"), "pin"),
    ("header-comment", _with(header="[reticulum]  # core"), "pin"),
    ("header-spaces", _with(header="[ reticulum ]"), "pin"),
    ("header-quoted", _with(header='["reticulum"]'), "pin"),
    ("commented-placeholder", _with(key_line=f"  # rpc_key = {PLACEHOLDER}"), "pin"),
    ("live-placeholder", _with(key_line=f"  rpc_key = {PLACEHOLDER}"), "pin"),
    ("key-inline-comment", _with(key_line=f"  rpc_key = {HEX}  # pinned"), f"keep:{HEX}"),
    ("key-quoted-name", _with(key_line=f'  "rpc_key" = {HEX}'), f"keep:{HEX}"),
    ("key-quoted-value", _with(key_line=f'  rpc_key = "{HEX}"'), f"keep:{HEX}"),
    ("key-upper", _with(key_line=f"  rpc_key = {HEX.upper()}"), f"keep:{HEX}"),
    ("key-empty", _with(key_line="  rpc_key ="), "keep:"),
    ("key-short", _with(key_line="  rpc_key = short"), "keep:short"),
    ("crlf-nokey", _with().replace(b"\n", b"\r\n"), "pin"),
    ("no-reticulum", b"[interfaces]\n  [[A]]\n    type = AutoInterface\n", "pin"),
    ("empty-file", b"", "pin"),
    ("unloadable-dup-key", _with(key_line="  instance_name = other"), "refuse"),
    ("non-utf8", _with().replace(b"[interfaces]", b"# caf\xe9\n[interfaces]"), "refuse"),
]


@pytest.fixture(params=["configobj", "strict"])
def reader_path(request, monkeypatch):
    if request.param == "configobj":
        if not rcr.HAS_CONFIGOBJ:
            pytest.skip("RNS (ConfigObj) not importable")
    else:
        monkeypatch.setattr(rcr, "HAS_CONFIGOBJ", False)
    return request.param


def _rnsd_key(raw: bytes):
    """What rnsd sees: its own ConfigObj when importable, else the reader."""
    try:
        from RNS.vendor.configobj import ConfigObj
    except ImportError:
        view = rcr.rnsd_view(raw)
    else:
        import io
        view = ConfigObj(io.BytesIO(raw).readlines())
    return view["reticulum"].get("rpc_key")


@pytest.mark.parametrize("sid,raw,expected", SHAPES, ids=[s[0] for s in SHAPES])
def test_ensure_rpc_key_never_reports_a_key_rnsd_will_not_read(
        tmp_path, reader_path, sid, raw, expected):
    cfg = tmp_path / "config"
    cfg.write_bytes(raw)
    if expected == "refuse":
        with pytest.raises(RNSConfigError):
            ensure_rpc_key(cfg)
        assert cfg.read_bytes() == raw          # nothing written
        return
    key, generated = ensure_rpc_key(cfg)
    if expected == "pin":
        assert generated is True and is_valid_key(key)
        after = cfg.read_bytes()
        assert _rnsd_key(after) == key           # rnsd LOADS it, sees THIS key
        before = rcr.rnsd_view(raw) if raw else {}
        now = rcr.rnsd_view(after)
        now["reticulum"].pop("rpc_key")
        if "reticulum" in before:
            before["reticulum"].pop("rpc_key", None)
        else:
            assert now.pop("reticulum") == {}
        assert now == before                     # nothing else rnsd reads changed
    else:
        assert generated is False
        assert key == expected.split(":", 1)[1]
        assert cfg.read_bytes() == raw           # untouched


@pytest.mark.parametrize("raw", [
    _with(key_line="  rpc_key ="),
    _with(key_line="  rpc_key = short"),
    _with(key_line='  "rpc_key" = short  # x'),
], ids=["empty", "short", "quoted-comment"])
def test_force_replaces_an_invalid_key_rnsd_reads_the_new_one(
        tmp_path, reader_path, raw):
    cfg = tmp_path / "config"
    cfg.write_bytes(raw)
    key, generated = ensure_rpc_key(cfg, force=True)
    assert generated is True
    assert _rnsd_key(cfg.read_bytes()) == key


def test_the_boundary_refuses_an_edit_that_changes_anything_else(reader_path):
    # A placeholder that is ALSO used outside rpc_key: substituting it would
    # change an interface value too — refuse, never write a wider edit.
    raw = _with(key_line=f"  rpc_key = {PLACEHOLDER}").replace(
        b"enabled = yes", f"enabled = yes\n    name = {PLACEHOLDER}".encode())
    with pytest.raises(RNSConfigError):
        pin_rpc_key_bytes(raw)


def test_refusal_says_why_in_words(reader_path):
    with pytest.raises(RNSConfigError) as ei:
        pin_rpc_key_bytes(_with(key_line="  instance_name = other"))
    assert "nothing" in str(ei.value).lower()


def test_render_template_refuses_when_the_key_does_not_land(reader_path):
    # The only placeholder is in a comment, and [reticulum] is unloadable
    # around it — render must not hand back text whose key rnsd cannot read.
    with pytest.raises(RNSConfigError):
        render_template(f"[reticulum]\n  a = 1\n  a = 2\n  # {PLACEHOLDER}\n", HEX)


# ── the alignment pin: no bash, the same function ───────────────────────


def _state_needing_pin(tmp_path):
    from utils.rns_alignment import ConfigFileFacts, RNSAlignmentState, CANONICAL_CONFIGDIR
    st = RNSAlignmentState(hostname="t", rnsd_active=True, rnsd_user="root",
                           rnsd_exec_start="", rnsd_configdir=CANONICAL_CONFIGDIR)
    st.etc_config = ConfigFileFacts(path=tmp_path / "config", exists=True,
                                    has_rpc_key=False)
    return st


def test_alignment_pin_runs_the_one_writer_not_bash(tmp_path):
    from utils.rns_alignment import plan_normalize
    plan = plan_normalize(_state_needing_pin(tmp_path))
    pins = [a for a in plan if "rpc_key" in a.description]
    assert len(pins) == 1
    assert pins[0].fn is not None
    assert "bash" not in pins[0].cmd and "awk" not in " ".join(pins[0].cmd)


@pytest.mark.parametrize("sid,raw,expected", SHAPES, ids=[s[0] for s in SHAPES])
def test_alignment_pin_on_every_shape(tmp_path, reader_path, sid, raw, expected):
    from utils.rns_alignment import _pin_rpc_key_in
    cfg = tmp_path / "config"
    cfg.write_bytes(raw)
    if expected == "pin":
        _pin_rpc_key_in(cfg, sudo=False)
        assert is_valid_key(_rnsd_key(cfg.read_bytes()))
        return
    if expected.startswith("keep:") and is_valid_key(expected[5:]):
        _pin_rpc_key_in(cfg, sudo=False)          # already pinned: no-op
    else:
        with pytest.raises(RNSConfigError):       # refuse, loud, nothing written
            _pin_rpc_key_in(cfg, sudo=False)
    assert cfg.read_bytes() == raw


# ── the read side: both scan branches answer from the same reader ───────


def _fake_sudo(argv, **kw):
    import subprocess
    from pathlib import Path
    path = Path(argv[-1])
    if argv[:4] == ["sudo", "-n", "test", "-f"]:
        return subprocess.CompletedProcess(argv, 0 if path.is_file() else 1, b"", b"")
    if argv[:3] == ["sudo", "-n", "cat"]:
        assert not kw.get("text"), "rns config must be read as BYTES"
        return subprocess.CompletedProcess(argv, 0, path.read_bytes(), b"")
    return subprocess.CompletedProcess(argv, 1, "", "")


@pytest.mark.parametrize("sid,raw,expected", SHAPES, ids=[s[0] for s in SHAPES])
def test_scan_sudo_and_direct_agree_and_match_the_reader(
        tmp_path, reader_path, sid, raw, expected):
    from utils import rns_alignment as A
    cfg = tmp_path / "config"
    cfg.write_bytes(raw)
    direct = A._scan_config_file(cfg)
    with patch.object(A.subprocess, "run", side_effect=_fake_sudo):
        via_sudo = A._scan_config_file(cfg, sudo=True)

    def facts(f):
        return (f.exists, f.has_rpc_key, f.has_instance_name, f.instance_name,
                f.config_error is not None)
    assert facts(direct) == facts(via_sudo)
    if expected == "refuse":
        assert direct.config_error and direct.has_rpc_key is False
        return
    assert direct.config_error is None
    assert direct.has_rpc_key is (expected.startswith("keep:")
                                  and is_valid_key(expected[5:].lower()))
    if b"instance_name = lab" in raw:
        assert direct.instance_name == "lab"


def test_unloadable_etc_config_is_a_drift_reason(tmp_path, reader_path):
    from utils.rns_alignment import analyze_drift
    st = _state_needing_pin(tmp_path)
    st.etc_config.config_error = "Duplicate keyword name at line 4"
    assert any("cannot load" in r for r in analyze_drift(st))


# ── the shell installers: the same pinner, no sed grammar ───────────────

_ROOT = os.path.join(os.path.dirname(__file__), '..')


def _pin_cli(path):
    import subprocess
    return subprocess.run(
        [sys.executable, os.path.join(_ROOT, "scripts", "rns_rpc_key_pin.py"), str(path)],
        capture_output=True, text=True, timeout=60)


def test_pin_cli_exit_codes_and_never_prints_the_key(tmp_path):
    import re
    cfg = tmp_path / "config"
    cfg.write_bytes(_with(header="[ reticulum ]"))
    first = _pin_cli(cfg)
    assert first.returncode == 10, first.stderr
    key = _rnsd_key(cfg.read_bytes())
    assert is_valid_key(key)
    assert key not in first.stdout + first.stderr
    assert re.search(r"[0-9a-f]{64}", first.stdout + first.stderr) is None
    again = _pin_cli(cfg)
    assert again.returncode == 0 and _rnsd_key(cfg.read_bytes()) == key

    bad = tmp_path / "bad"
    raw = _with(key_line="  instance_name = other")
    bad.write_bytes(raw)
    refused = _pin_cli(bad)
    assert refused.returncode == 2 and "NOT pinned" in refused.stderr
    assert bad.read_bytes() == raw

    short = tmp_path / "short"
    short.write_bytes(_with(key_line="  rpc_key = short"))
    assert _pin_cli(short).returncode == 3


@pytest.mark.parametrize("script", ["install_noc.sh", "fleet_restore.sh"])
def test_installers_pin_through_the_one_pinner(script):
    with open(os.path.join(_ROOT, "scripts", script), encoding="utf-8") as fh:
        text = fh.read()
    assert "rns_rpc_key_pin.py" in text
    assert "a\\\\  rpc_key = $RPC_KEY" not in text   # the old sed append
    assert 's/__RPC_KEY__/$RPC_KEY/' not in text


def test_declared_ports_read_like_rnsd(tmp_path, reader_path):
    # Fable r12 NIT (MC): quoted port, inline comment, RNodeMulti sub-port.
    from commands.meshtastic import _rns_declared_ports
    cfg = tmp_path / "config"
    cfg.write_text(
        "[interfaces]\n"
        "  [[RNode A]]\n    type = RNodeInterface\n    port = \"/dev/ttyQA\"  # rnode\n"
        "  [[Multi]]\n    type = RNodeMultiInterface\n    port = /dev/ttyQB\n"
        "    [[[High]]]\n      port = /dev/ttyQC\n"
        "  [[Off]]\n    # port = /dev/ttyQD\n    type = AutoInterface\n")
    got = {os.path.basename(k) for k in _rns_declared_ports([cfg])}
    assert got == {"ttyQA", "ttyQB", "ttyQC"}


def test_insert_keeps_crlf_on_the_new_line(reader_path):
    # Fable r12 NIT (MG): the inserted line carries the file's CR too.
    new_raw, _key, gen = pin_rpc_key_bytes(_with().replace(b"\n", b"\r\n"))
    assert gen and all(line.endswith(b"\r") for line in new_raw.split(b"\n")[:-1])


def test_no_pin_is_planned_into_a_config_rnsd_cannot_load(tmp_path):
    # The drift reason says "fix by hand"; planning a pin that must refuse
    # would stop normalize mid-plan instead (mutant M9, r13).
    from utils.rns_alignment import analyze_drift, plan_normalize
    st = _state_needing_pin(tmp_path)
    st.etc_config.config_error = "Duplicate keyword name at line 4"
    st.drift_reasons = analyze_drift(st)
    assert not [a for a in plan_normalize(st) if "rpc_key" in a.description]
