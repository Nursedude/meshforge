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


def test_a_placeholder_used_elsewhere_is_left_there(reader_path):
    # r13.1: only the live [reticulum] rpc_key line is filled — the interface
    # value keeps its literal (r13 refused the whole pin over it).
    raw = _with(key_line=f"  rpc_key = {PLACEHOLDER}").replace(
        b"enabled = yes", f"enabled = yes\n    name = {PLACEHOLDER}".encode())
    new, key, gen = pin_rpc_key_bytes(raw)
    assert gen and _rnsd_key(new) == key
    view = rcr.rnsd_view(new)
    assert view["interfaces"]["Default Interface"]["name"] == PLACEHOLDER


def test_the_boundary_refuses_an_edit_that_changes_anything_else(reader_path):
    from utils.rns_config_setup import RPCKeyWriteRefused, _require_pinned
    before = rcr.rnsd_view(_with())
    wider = _with(key_line=f"  rpc_key = {HEX}").replace(b"enabled = yes", b"enabled = no")
    with pytest.raises(RPCKeyWriteRefused):
        _require_pinned(before, wider, HEX)
    _require_pinned(before, _with(key_line=f"  rpc_key = {HEX}"), HEX)   # exact: passes


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


# ── r13.1: ONE answer to "which rpc_key does rnsd use" (Fable r13 #1/#2/#4) ──
#
# r13 gated the WRITE; the READERS that feed every client
# (ReticulumPaths.get_shared_rpc_key + its two copies) still took the first
# `rpc_key =` line anywhere, and the pinner had its own "valid" (a trailing-
# comma LIST joined into a key). Every reader now asks rns_config_reader.
# rnsd_rpc_key; this table holds each of them to rnsd's own reading.

R131_SHAPES = SHAPES + [
    ("trailing-comma-list", _with(key_line=f"  rpc_key = {HEX},"), "keep:list"),
    ("subsection-rpc_key", _with(key_line="  [[rpc_key]]\n    a = 1").replace(
        b"\n\n[interfaces]", b"\n[interfaces]"), "keep:section"),
    ("top-level-key", (f"rpc_key = {'ab' * 32}\n").encode() + _with(), "pin"),
    ("key-in-logging", (f"[logging]\n  rpc_key = {'ab' * 32}\n").encode() + _with(), "pin"),
]


def _rnsd_uses(raw: bytes):
    """The 64-hex key rnsd pins from ``raw`` (lowercased), else None —
    rnsd: bytes.fromhex(config["reticulum"]["rpc_key"]); MeshForge's policy
    on top is 64 hex. Uses rnsd's own ConfigObj when importable."""
    try:
        from RNS.vendor.configobj import ConfigObj
        import io
        sect = ConfigObj(io.BytesIO(raw).readlines()).get("reticulum", {})
    except ImportError:
        sect = rcr.rnsd_view(raw).get("reticulum", {})
    v = sect.get("rpc_key") if hasattr(sect, "get") else None
    if not isinstance(v, str):
        return None
    v = v.lower()
    return v if is_valid_key(v) else None


def _readers(cfg):
    from utils import gateway_flow_audit as gfa
    from utils import rns_alignment as A
    from utils.paths import ReticulumPaths
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
    from handlers import _config_doctor_checks as cdc
    with patch.object(ReticulumPaths, "get_config_file", return_value=cfg):
        shared = ReticulumPaths.get_shared_rpc_key()   # no storage/ -> no derivation
    scan = A._scan_config_file(cfg)
    return {
        "paths.get_shared_rpc_key": shared,
        "gateway_flow_audit._read_rpc_key": gfa._read_rpc_key(cfg),
        "config_doctor._read_rpc_key": cdc._read_rpc_key(cfg),
        "alignment._read_rpc_key_value": A._read_rpc_key_value(cfg),
        "alignment.scan.has_rpc_key": scan.has_rpc_key,
    }


@pytest.mark.parametrize("sid,raw,expected", R131_SHAPES, ids=[s[0] for s in R131_SHAPES])
def test_every_rpc_key_reader_answers_what_rnsd_uses(tmp_path, reader_path, sid, raw, expected):
    cfg = tmp_path / "config"
    cfg.write_bytes(raw)
    if expected == "refuse":
        return          # unloadable / not UTF-8: covered by the refusal tests
    want = _rnsd_uses(raw)
    got = _readers(cfg)
    assert got.pop("alignment.scan.has_rpc_key") is (want is not None), sid
    assert got == {k: want for k in got}, sid


@pytest.mark.parametrize("sid,raw,expected", R131_SHAPES, ids=[s[0] for s in R131_SHAPES])
def test_pinner_and_readers_agree_after_the_pin(tmp_path, reader_path, sid, raw, expected):
    """The pinner's 'already pinned' == the readers' key; after any pin the
    readers return exactly the key the pinner reported."""
    if expected == "refuse":
        return
    cfg = tmp_path / "config"
    cfg.write_bytes(raw)
    try:
        key, generated = ensure_rpc_key(cfg)
    except RNSConfigError:
        assert _rnsd_uses(raw) is None
        return
    want = _rnsd_uses(cfg.read_bytes())
    if generated:
        assert want == key
    else:
        assert (want is not None) == is_valid_key(key), (sid, key)
    got = _readers(cfg)
    got.pop("alignment.scan.has_rpc_key")
    assert set(got.values()) == {want}, (sid, got)


def test_a_list_or_section_rpc_key_is_never_reported_valid(tmp_path, reader_path):
    for line in (f"  rpc_key = {HEX},", f"  rpc_key = {HEX}, {HEX}"):
        new, key, gen = pin_rpc_key_bytes(_with(key_line=line))
        assert new is None and gen is False and not is_valid_key(key)
    raw = b"[reticulum]\n  [[rpc_key]]\n    a = 1\n"
    new, key, gen = pin_rpc_key_bytes(raw)          # typed, never AttributeError
    assert new is None and gen is False and not is_valid_key(key)


def test_alignment_pin_on_a_list_key_refuses_not_applied(tmp_path, reader_path):
    from utils.rns_alignment import _pin_rpc_key_in
    cfg = tmp_path / "config"
    raw = _with(key_line=f"  rpc_key = {HEX},")
    cfg.write_bytes(raw)
    with pytest.raises(RNSConfigError):
        _pin_rpc_key_in(cfg, sudo=False)
    assert cfg.read_bytes() == raw


def test_placeholder_in_a_comment_is_not_filled(reader_path):
    raw = _with(key_line=f"  rpc_key = {PLACEHOLDER}  # was {PLACEHOLDER}").replace(
        b"[interfaces]", f"# template: {PLACEHOLDER}\n[interfaces]".encode())
    new, key, gen = pin_rpc_key_bytes(raw)
    assert gen and new.count(key.encode()) == 1


# ── r13.1: the alignment write (Fable r13 #3, #5) ───────────────────────


def test_alignment_write_never_follows_a_planted_tmp_symlink(tmp_path, reader_path):
    from utils.rns_alignment import _pin_rpc_key_in
    victim = tmp_path / "victim"
    victim.write_bytes(b"untouched\n")
    cfg = tmp_path / "config"
    cfg.write_bytes(_with())
    (tmp_path / "config.mf-new").symlink_to(victim)
    _pin_rpc_key_in(cfg, sudo=False)
    assert victim.read_bytes() == b"untouched\n"
    assert not cfg.is_symlink() and is_valid_key(_rnsd_key(cfg.read_bytes()))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config", "config.mf-new", "victim"]


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
def test_alignment_write_keeps_mode_and_is_a_rename(tmp_path, reader_path, mode):
    from utils.rns_alignment import _pin_rpc_key_in
    cfg = tmp_path / "config"
    cfg.write_bytes(_with())
    cfg.chmod(mode)
    ino = cfg.stat().st_ino
    _pin_rpc_key_in(cfg, sudo=False)
    st = cfg.stat()
    assert st.st_mode & 0o777 == mode
    assert st.st_ino != ino                      # atomic rename, not an in-place write
    assert [p.name for p in tmp_path.iterdir()] == ["config"]


def test_alignment_write_failure_raises(tmp_path, reader_path):
    import subprocess
    from utils import rns_alignment as A
    cfg = tmp_path / "config"
    cfg.write_bytes(_with())
    with patch.object(A.subprocess, "run",
                      return_value=subprocess.CompletedProcess([], 1, b"", b"boom")):
        with pytest.raises(OSError):
            A._pin_rpc_key_in(cfg, sudo=False)


def test_normalize_runs_fn_not_cmd():
    from utils.rns_alignment import NormalizeAction, normalize_local
    ran = []
    act = NormalizeAction(description="x", cmd=["false"], fn=lambda: ran.append(1))
    with patch("utils.rns_alignment.plan_normalize", return_value=[act]), \
            patch("utils.rns_alignment.subprocess.run") as run:
        assert normalize_local(None) == ["x"]
    assert ran == [1] and not run.called


def test_normalize_cli_says_failed_on_a_refusal(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rns_alignment_cli", os.path.join(_ROOT, "scripts", "rns_alignment.py"))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    st = _state_needing_pin(__import__("pathlib").Path("/nonexistent"))
    st.drift_reasons = ["x"]
    with patch.object(cli, "probe_local", return_value=st), \
            patch.object(cli, "analyze_drift", return_value=["x"]), \
            patch.object(cli, "plan_normalize", return_value=[]), \
            patch.object(cli, "normalize_local",
                         side_effect=RNSConfigError("refused — nothing written")):
        rc = cli.cmd_normalize(type("A", (), {"dry_run": False, "yes": True})())
    assert rc == 1 and "FAILED" in capsys.readouterr().out


def test_unreadable_config_is_named_as_ours_not_rnsds(tmp_path, reader_path):
    if os.geteuid() == 0:
        pytest.skip("root reads a 000 file")
    from utils.rns_alignment import _scan_config_file, analyze_drift
    cfg = tmp_path / "config"
    cfg.write_bytes(_with())
    cfg.chmod(0)
    try:
        f = _scan_config_file(cfg)
    finally:
        cfg.chmod(0o644)
    assert f.config_error and f.has_rpc_key is False
    st = _state_needing_pin(tmp_path)
    st.etc_config = f
    reasons = " ".join(analyze_drift(st))
    assert "could not read" in reasons and "rnsd cannot load" not in reasons


def test_declared_ports_fallback_on_an_unloadable_config(tmp_path, reader_path):
    from commands.meshtastic import _rns_declared_ports
    cfg = tmp_path / "config"
    cfg.write_text("[interfaces]\n  [[A]]\n    port = /dev/ttyQZ\n    port = /dev/ttyQY\n")
    got = {os.path.basename(k) for k in _rns_declared_ports([cfg])}
    assert got == {"ttyQZ", "ttyQY"}           # still REFUSED though rnsd can't load it


@pytest.mark.parametrize("raw,derives", [
    (_with(), True),                                         # absent -> derive
    (_with(key_line="  rpc_key = short"), False),            # explicit, broken
    (_with(key_line=f"  rpc_key = {HEX},"), False),          # a list: broken
    (_with(key_line="  instance_name = other"), False),      # rnsd cannot load
], ids=["absent", "invalid", "list", "unloadable"])
def test_shared_rpc_key_derives_only_when_no_key_is_set(tmp_path, raw, derives):
    """Mutants S3/S4 (r13.1): derivation from the transport identity is
    rnsd's behaviour for an ABSENT key only — never over a broken explicit
    key or a config rnsd cannot load."""
    RNS = pytest.importorskip("RNS")
    from utils.paths import ReticulumPaths
    cfg = tmp_path / "config"
    cfg.write_bytes(raw)
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "transport_identity").write_bytes(b"x")

    class _Id:
        def get_private_key(self):
            return b"k"
    with patch.object(ReticulumPaths, "get_config_file", return_value=cfg), \
            patch.object(RNS.Identity, "from_file", return_value=_Id()), \
            patch.object(RNS.Identity, "full_hash", return_value=bytes.fromhex("cd" * 32)):
        got = ReticulumPaths.get_shared_rpc_key()
    assert got == ("cd" * 32 if derives else None)


# ── r13.2: the SHELL readers ask the seam too (Fable r14 #1) ────────────
#
# configure_gateway.sh, the MeshChatX wrapper and install_meshchatx.sh each
# grepped "is rpc_key pinned" (30-34 of 88 verdicts wrong vs rnsd; a quoted
# key rnsd reads parked MeshChatX at exit 87). They now call
# scripts/rns_rpc_key_pin.py --check / --copy-from.

CHECK_RC = {"valid": 0, "invalid": 3, "absent": 4}


@pytest.mark.parametrize("sid,raw,expected", R131_SHAPES, ids=[s[0] for s in R131_SHAPES])
def test_check_cli_answers_what_rnsd_uses(tmp_path, sid, raw, expected):
    import subprocess
    cfg = tmp_path / "config"
    cfg.write_bytes(raw)
    r = subprocess.run([sys.executable, os.path.join(_ROOT, "scripts", "rns_rpc_key_pin.py"),
                        "--check", str(cfg)], capture_output=True, text=True, timeout=60)
    assert cfg.read_bytes() == raw                            # read-only
    if expected == "refuse":
        assert r.returncode == 2, (sid, r.stdout, r.stderr)   # rnsd cannot load it
        return
    want = _rnsd_uses(raw)
    assert (r.returncode == 0) is (want is not None), (sid, r.returncode)
    assert r.returncode in (0, 3, 4)
    assert want is None or want not in r.stdout + r.stderr    # never prints the key


def test_check_cli_unreadable_is_rc1(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root reads a 000 file")
    cfg = tmp_path / "config"
    cfg.write_bytes(_with(key_line=f"  rpc_key = {HEX}"))
    cfg.chmod(0)
    try:
        assert _pin_cli_args("--check", cfg).returncode == 1
    finally:
        cfg.chmod(0o644)


def _pin_cli_args(*args):
    import subprocess
    return subprocess.run(
        [sys.executable, os.path.join(_ROOT, "scripts", "rns_rpc_key_pin.py"),
         *[str(a) for a in args]], capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("client_raw,rc", [
    (_with(), 10),                                             # absent -> copy
    (_with(header="[ reticulum ]"), 10),                       # grammar the old insert missed
    (_with(key_line=f'  rpc_key = "{HEX}"'), 0),               # same key, quoted
    (_with(key_line=f"  rpc_key = {'ef' * 32}"), 4),            # different: never overwritten
    (_with(key_line="  instance_name = other"), 2),            # client unloadable: refuse
], ids=["absent", "spaced-header", "same-quoted", "different", "client-unloadable"])
def test_copy_from_cli(tmp_path, client_raw, rc):
    server = tmp_path / "server"
    server.write_bytes(_with(key_line=f"  rpc_key = {HEX}  # pinned"))
    client = tmp_path / "client"
    client.write_bytes(client_raw)
    r = _pin_cli_args("--copy-from", server, client)
    assert r.returncode == rc, (r.stdout, r.stderr)
    assert HEX not in r.stdout + r.stderr
    if rc == 10:
        assert _rnsd_uses(client.read_bytes()) == HEX
        assert list(tmp_path.glob("client.bak-*-rpckey"))     # backup kept, as before
    else:
        assert client.read_bytes() == client_raw


def test_copy_from_cli_server_without_a_usable_key_is_rc3(tmp_path):
    server = tmp_path / "server"
    server.write_bytes(_with(key_line=f"  rpc_key = {HEX},"))   # a list: rnsd falls back
    client = tmp_path / "client"
    client.write_bytes(_with())
    assert _pin_cli_args("--copy-from", server, client).returncode == 3
    assert client.read_bytes() == _with()


_SHELL_ROOTS = ["scripts", "templates"]
_SHELL_ALLOW = {
    # Scans MEMORY files for a leaked key literal — not an RNS config reader.
    "scripts/memory_secrets_check.sh",
}
# No owed sites (r13.3, Fable r15 #1): the install_meshchatx.sh COPY block
# now calls `rns_rpc_key_pin.py --copy-from`. Its MeshAnchor twin keeps its
# own python until the MA seam port; only the shared harness test is
# byte-identical (scripts/parity_check.py), not the block.
_SHELL_OWED: set = set()


def test_no_shell_or_template_parses_rpc_key_itself():
    """Every reader of an RNS config's rpc_key outside src/ goes through
    scripts/rns_rpc_key_pin.py (the seam). A grep/sed/awk/regex over
    `rpc_key` in a shell script or template is the r14 #1 defect."""
    import re
    bad = []
    pat = re.compile(r"rpc_key")
    tool = re.compile(r"\b(grep|sed|awk|perl)\b|=~|re\.(compile|search|match)")
    for root in _SHELL_ROOTS:
        for dirpath, _dirs, files in os.walk(os.path.join(_ROOT, root)):
            for name in files:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, _ROOT).replace(os.sep, "/")
                if rel in _SHELL_ALLOW or rel.endswith((".md", ".json", ".py")) and not rel.startswith("templates/"):
                    continue
                try:
                    text = open(full, encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if (rel, line.strip()) in _SHELL_OWED:
                        continue
                    if pat.search(line) and tool.search(line):
                        bad.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert bad == [], "\n".join(bad)


@pytest.mark.parametrize("script", [
    "scripts/configure_gateway.sh", "templates/python/meshchatx_wrapper.sh",
    "scripts/install_meshchatx.sh"])
def test_shell_readers_call_the_seam(script):
    with open(os.path.join(_ROOT, script), encoding="utf-8") as fh:
        assert "rns_rpc_key_pin.py" in fh.read()


def test_alignment_pin_refuses_a_symlinked_config(tmp_path, reader_path):
    from utils.rns_alignment import _pin_rpc_key_in
    real = tmp_path / "real_config"
    real.write_bytes(_with())
    cfg = tmp_path / "config"
    cfg.symlink_to(real)
    with pytest.raises(RNSConfigError):
        _pin_rpc_key_in(cfg, sudo=False)
    assert cfg.is_symlink() and real.read_bytes() == _with()


def test_shared_rpc_key_unreadable_config_never_derives(tmp_path):
    """Fable r14 #2: an UNREADABLE config is not an absent key."""
    if os.geteuid() == 0:
        pytest.skip("root reads a 000 file")
    RNS = pytest.importorskip("RNS")
    from utils.paths import ReticulumPaths
    cfg = tmp_path / "config"
    cfg.write_bytes(_with(key_line=f"  rpc_key = {HEX}"))
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "transport_identity").write_bytes(b"x")

    class _Id:
        def get_private_key(self):
            return b"k"
    cfg.chmod(0)
    try:
        with patch.object(ReticulumPaths, "get_config_file", return_value=cfg), \
                patch.object(RNS.Identity, "from_file", return_value=_Id()), \
                patch.object(RNS.Identity, "full_hash", return_value=bytes.fromhex("cd" * 32)):
            assert ReticulumPaths.get_shared_rpc_key() is None
    finally:
        cfg.chmod(0o644)


def test_repair_says_why_an_invalid_key_is_left(tmp_path, capsys):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
    from handlers import _rns_repair as R
    from utils.paths import ReticulumPaths
    cfg = tmp_path / "config"
    cfg.write_bytes(_with(key_line="  rpc_key = " + "ab" * 16))   # 32 hex: rnsd uses it
    with patch.object(ReticulumPaths, "get_config_file", return_value=cfg), \
            patch.object(ReticulumPaths, "get_shared_rpc_key", return_value=None):
        assert R._ensure_rnsd_rpc_key() is False
    out = capsys.readouterr().out
    assert "64" in out and "Pinned" not in out
