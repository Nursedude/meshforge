"""box_truth/v1 export — collection, witness, rate limit, forced-command
behaviour (NOC read grant session 2, 2026-10-14).

Owed by: B1 (stdin / SSH_ORIGINAL_COMMAND are data), B2 (LC_ALL=C client),
B4 (fingerprint in the witness), B7b (tripwire withholds), B8 (rate limit),
S4 (view routes only), S7 (serialised), N4 (never a :4403 connect), and the
byte-compatibility of every section with the spool it replaces.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_app as app  # noqa: E402
from utils import box_truth_collect as bc  # noqa: E402

FP = "SHA256:" + "A" * 43


def _mini(home):
    """This app's mini state file — nested on MeshAnchor, so make its dir."""
    p = home / app.MINI_STATE_RELPATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _fake_runner(calls=None, crontab=b"*/2 * * * * job\n", show=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        assert kw.get("timeout"), "every subprocess is bounded (MF004)"
        if argv[:2] == ["systemctl", "list-unit-files"]:
            return SimpleNamespace(returncode=0,
                                   stdout=(app.APP + "-map.service enabled enabled\n").encode())
        if argv[:2] == ["systemctl", "show"]:
            return SimpleNamespace(returncode=0, stdout=show if show is not None else (
                b"ActiveState=active\nUnitFileState=enabled\n\n"
                b"ActiveState=inactive\nUnitFileState=\n\n"
                b"ActiveState=failed\nUnitFileState=disabled\n"))
        if argv[:2] == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0, stdout=crontab)
        raise AssertionError(f"unexpected argv {argv}")
    return run


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config" / app.CONFIG_DIRNAME).mkdir(parents=True)
    (h / ".config" / app.CONFIG_DIRNAME / "deployment.json").write_text(
        json.dumps({"role": "gateway-only"}))
    _mini(h).write_text(
        json.dumps({"last_tick_ts": 1, "reason": "café ☕"}), encoding="utf-8")
    (h / "cron_verdicts.log").write_text(
        "1 a OK\n2 b OK\n3 a FAIL(1)\n4 a OK\n5 c OK\n")
    return h


def _src(home, tmp_path, **kw):
    proc = tmp_path / "tcp"
    proc.write_text(
        "  sl  local_address rem_address   st\n"
        "   0: 00000000:1133 00000000:0000 0A 0\n")
    base = dict(home=home, env={}, watchdog_path=tmp_path / "wd.json",
                proc_tcp=(str(proc),), dev_globs=(str(tmp_path / "nodev*"),),
                runner=_fake_runner(), http_get=lambda url: None)
    base.update(kw)
    return bc.Sources(**base)


# ── collection ───────────────────────────────────────────────────────────

def test_sections_are_exactly_the_allowlist_in_order(home, tmp_path):
    sections, _errors = bc.collect_sections(_src(home, tmp_path))
    assert tuple(sections) == bt.SECTIONS


def test_section_values_match_the_spool_shapes(home, tmp_path):
    s, errors = bc.collect_sections(_src(home, tmp_path))
    assert s["deployment"] == {"role": "gateway-only"}
    assert s["raw_mini"]["reason"] == "café ☕"
    assert s["raw_watchdog"] is None and "raw_watchdog" not in errors  # absent ≠ error
    assert s["radio_probe"] == {"tcp_listening": True, "usb_present": False}
    assert s["services"] == {
        "meshtasticd": {"active": "active", "enabled": "enabled"},
        "rnsd": {"active": "inactive", "enabled": "unknown"},
        app.APP + "-map.service": {"active": "failed", "enabled": "disabled"}}
    assert base64.b64decode(s["schedules"]["crontab_b64"]) == b"*/2 * * * * job\n"
    assert isinstance(s["clock"]["now"], int)
    assert s["ingress"] is None


def test_usb_present_when_only_one_glob_matches(home, tmp_path):
    """The shell this replaces ran `ls /dev/ttyUSB* /dev/ttyACM*`, which exits
    2 when EITHER glob is empty — so a box with only /dev/ttyACM0 (measured
    on the manager box 2026-10-14) spooled usb_present=false. Any match is a radio."""
    (tmp_path / "ttyACM0").write_text("")
    src = _src(home, tmp_path, dev_globs=(str(tmp_path / "ttyUSB*"),
                                          str(tmp_path / "ttyACM*")))
    assert bc.collect_sections(src)[0]["radio_probe"]["usb_present"] is True


def test_corrupt_json_is_an_error_not_a_value(home, tmp_path):
    _mini(home).write_text("{not json")
    s, errors = bc.collect_sections(_src(home, tmp_path))
    assert s["raw_mini"] is None and "raw_mini" in errors


def test_http_sections_use_the_view_routes(home, tmp_path):
    seen = []
    s, _ = bc.collect_sections(_src(home, tmp_path,
                                    http_get=lambda u: seen.append(u) or {"ok": 1}))
    assert seen == ["http://localhost:5000/fleet/slo", "http://localhost:5000/api/status"]
    assert s["slo"] == {"ok": 1}


def test_s4_export_routes_are_not_behind_the_operator_session():
    from utils._map_auth_endpoints import AuthEndpointsMixin
    for path in bc.HTTP_PATHS.values():
        assert path not in AuthEndpointsMixin.OPERATOR_GETS, (
            f"{path} moved behind login: read it from its file/socket instead — "
            "a loopback read would grant every center a login bypass (S4)")


def test_crontab_failure_is_unobservable_not_empty(home, tmp_path):
    def run(argv, **kw):
        if argv[:2] == ["crontab", "-l"]:
            return SimpleNamespace(returncode=1, stdout=b"")
        return _fake_runner()(argv, **kw)
    s, _ = bc.collect_sections(_src(home, tmp_path, runner=run))
    assert s["schedules"]["crontab_b64"] == ""


@pytest.mark.skipif(shutil.which("awk") is None, reason="needs awk")
def test_last_two_verdicts_matches_the_shell_it_replaces():
    text = "1 a OK\n2 b OK\n3 a FAIL(1)\n4 a OK\n5 c OK\n6 b FAIL(2)\n"
    awk = subprocess.run(
        ["awk", '{if ($2 in n) p[$2]=n[$2]; n[$2]=$0} '
                'END{for(k in n){if (k in p) print p[k]; print n[k]}}'],
        input=text, capture_output=True, text=True, timeout=10).stdout
    ours = bc.last_two_verdicts(text)
    # awk's `for (k in n)` order is unspecified: compare per name, in order.
    def per_name(t):
        d = {}
        for line in t.splitlines():
            d.setdefault(line.split()[1], []).append(line)
        return d
    assert per_name(ours) == per_name(awk)
    assert per_name(ours)["a"] == ["3 a FAIL(1)", "4 a OK"]   # oldest first


def test_n4_never_connects_to_the_radio():
    src = (_SRC / "utils" / "box_truth_collect.py").read_text()
    for banned in ("4403", "TCPInterface", "create_connection", "import meshtastic"):
        code = [ln for ln in src.split('"""', 2)[2].splitlines()   # skip docstring
                if banned in ln.split("#", 1)[0]]
        assert not code, f"{banned!r} in export code: {code}"


# ── B7b: tripwire withholds ──────────────────────────────────────────────

def test_planted_secret_never_leaves_the_box(home, tmp_path):
    _mini(home).write_text(
        json.dumps({"note": "-----BEGIN OPENSSH PRIVATE KEY-----"}))
    doc = bt.parse_framed(bc.run_export(FP, _src(home, tmp_path)))
    assert doc["sections"]["raw_mini"] is None
    assert doc["tripwire"] == ["raw_mini"]
    assert "BEGIN" not in json.dumps(doc)


# ── witness + B8 rate limit ──────────────────────────────────────────────

def test_witness_records_fp_src_and_orig_cmd_as_data(home, tmp_path):
    env = {"SSH_CONNECTION": "192.0.2.7 50000 192.0.2.1 22",
           "SSH_ORIGINAL_COMMAND": "bash -c 'rm -rf ~' " + "x" * 300}
    doc = bt.parse_framed(bc.run_export(FP, _src(home, tmp_path, env=env)))
    assert doc["grant"] == FP and doc["witness"] == "ok"
    rows = bc.read_witness(bc.witness_path(home, env))
    assert len(rows) == 1
    r = rows[0]
    assert r["fp"] == FP and r["src"] == "192.0.2.7" and r["outcome"] == "served"
    assert r["orig_cmd"].startswith("bash -c") and len(r["orig_cmd"]) == bc.ORIG_CMD_MAX
    assert r["bytes"] > 0
    assert oct(bc.witness_path(home, env).stat().st_mode & 0o777) == "0o600"


def test_witness_is_app_independent_under_xdg_state(home, tmp_path):
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    assert bc.witness_path(home, env) == tmp_path / "state" / "box_truth" / "reads.jsonl"


def test_rate_limit_refuses_the_seventh_read_in_a_window(home, tmp_path):
    src = _src(home, tmp_path)
    for i in range(bt.RATE_MAX):
        assert bt.classify_transport(0, bc.run_export(FP, src, now=1000.0 + i), "") == "ok"
    refused = bc.run_export(FP, src, now=1000.0 + bt.RATE_MAX)
    assert bt.classify_transport(0, refused, "") == "rate_limited"
    # another grant is unaffected; the window slides
    assert bt.classify_transport(0, bc.run_export("SHA256:" + "B" * 43, src,
                                                  now=1007.0), "") == "ok"
    assert bt.classify_transport(0, bc.run_export(FP, src, now=1100.0), "") == "ok"
    outcomes = [r["outcome"] for r in bc.read_witness(src.witness_path)]
    assert outcomes.count("refused_rate") == 1


def test_last_reads_is_newest_served_per_fp(home, tmp_path):
    src = _src(home, tmp_path)
    bc.run_export(FP, src, now=10.0)
    bc.run_export(FP, src, now=20.0)
    assert bc.last_reads(src.witness_path)[FP]["ts"] == 20.0


def test_witness_trims_to_cap(home, tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "WITNESS_CAP", 5)
    monkeypatch.setattr(bc, "WITNESS_TRIM_AT", 8)
    src = _src(home, tmp_path)
    for i in range(9):
        bc.run_export(f"SHA256:{chr(65 + i) * 43}", src, now=float(i))
    rows = bc.read_witness(src.witness_path)
    assert len(rows) == 5 and rows[-1]["ts"] == 8.0


def test_f4a_unwritable_witness_refuses_never_serves_unlimited(home, tmp_path):
    """Review A F4a: an unwritable witness meant NO rate limit at all (10 reads
    in 10 s -> 10 ok). Fail closed, with the reason the center can show."""
    env = {"XDG_STATE_HOME": "/proc/nonexistent-dir"}
    out = bc.run_export(FP, _src(home, tmp_path, env=env))
    doc = bt.parse_framed(out)
    assert doc["refused"] == "witness" and "unwritable" in doc["reason"]
    assert "sections" not in doc
    assert bt.classify_transport(0, out, "") == "export_broken"


def test_malformed_grant_arg_is_recorded_not_trusted(home, tmp_path):
    src = _src(home, tmp_path)
    doc = bt.parse_framed(bc.run_export("SHA256:evil;rm", src))
    assert doc["grant"] is None
    assert bc.read_witness(src.witness_path)[0]["fp_malformed"] == "SHA256:evil;rm"


# ── the real script, under forced-command conditions (B1 + B2) ──────────

def test_script_under_lc_all_c_with_hostile_stdin_and_orig_cmd(home, tmp_path):
    canary = tmp_path / "pwned"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LC_ALL": "C", "LANG": "C",
           "LC_EVIL": "pwned", "XDG_STATE_HOME": str(tmp_path / "state"),
           "SSH_CONNECTION": "192.0.2.9 1 192.0.2.1 22",
           "SSH_ORIGINAL_COMMAND": f"touch {canary}"}
    proc = subprocess.run(
        [sys.executable, "-I", str(_ROOT / "scripts" / "box_truth_export.py"),
         "--grant", FP],
        input=b"client-stdin-payload; touch " + str(canary).encode() + b"\n",
        capture_output=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode()
    proc.stdout.decode("ascii")                           # ASCII on the wire
    doc = bt.parse_framed(proc.stdout.decode("ascii"))
    assert doc["schema"] == bt.SCHEMA and doc["grant"] == FP
    assert doc["sections"]["raw_mini"]["reason"] == "café ☕"
    assert doc["sections"]["deployment"] == {"role": "gateway-only"}
    assert not canary.exists(), "SSH_ORIGINAL_COMMAND or stdin was executed"
    row = bc.read_witness(tmp_path / "state" / "box_truth" / "reads.jsonl")[0]
    assert row["orig_cmd"] == f"touch {canary}"


def test_script_is_executable_in_git():
    out = subprocess.run(["git", "ls-files", "-s", "scripts/box_truth_export.py"],
                         cwd=_ROOT, capture_output=True, text=True, timeout=10).stdout
    if not out:
        pytest.skip("not yet tracked")
    assert out.startswith("100755"), out


# ── review A F4b/F4c/F5 + review B 1/5 ──────────────────────────────────

def test_f4b_lock_not_acquired_refuses_busy(home, tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setattr(bc, "LOCK_WAIT_S", 0.2)
    src = _src(home, tmp_path)
    lock = bc.lock_path(src.witness_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        out = bc.run_export(FP, src)
    doc = bt.parse_framed(out)
    assert doc["refused"] == "busy"
    assert bt.classify_transport(0, out, "") == "rate_limited"


def test_f4c_refused_attempts_are_trimmed_too(home, tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "WITNESS_CAP", 5)
    monkeypatch.setattr(bc, "WITNESS_TRIM_AT", 8)
    src = _src(home, tmp_path)
    for i in range(40):
        bc.run_export(FP, src, now=1000.0 + i * 0.01)
    assert len(bc.read_witness(src.witness_path)) <= 8


def test_f5_a_waiter_appends_to_the_file_that_exists_after_a_trim(home, tmp_path, monkeypatch):
    """Review A F5: the lock lived on reads.jsonl, which _trim REPLACES; a
    waiter holding the old inode wrote into an unlinked file (200 reads ->
    ~55 rows). The lock is a separate, never-replaced file now."""
    import fcntl
    import threading
    monkeypatch.setattr(bc, "WITNESS_CAP", 2)
    monkeypatch.setattr(bc, "WITNESS_TRIM_AT", 3)
    src = _src(home, tmp_path)
    w = src.witness_path
    w.parent.mkdir(parents=True, exist_ok=True)
    w.write_text("".join('{"ts": %d, "fp": "x", "outcome": "served"}\n' % i for i in range(5)))
    lock = bc.lock_path(w)
    assert lock != w
    with open(lock, "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        t = threading.Thread(target=bc.run_export, args=(FP, src), kwargs={"now": 999.0})
        t.start()
        t.join(0.3)
        bc.trim_witness(w)                     # the holder trims: file replaced
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    t.join(10)
    assert any(r.get("fp") == FP for r in bc.read_witness(w)), "waiter's row lost"


def test_b1_bad_http_answer_costs_one_section_not_the_box(home, tmp_path):
    import http.client

    def bad(url):
        raise http.client.BadStatusLine("GARBAGE LINE")
    out = bc.run_export(FP, _src(home, tmp_path, http_get=bad))
    doc = bt.parse_framed(out)
    assert doc["sections"]["slo"] is None and "BadStatusLine" in doc["section_errors"]["slo"]
    assert doc["sections"]["deployment"] == {"role": "gateway-only"}


def test_b1_any_section_reader_crash_is_that_sections_error(home, tmp_path):
    def boom(argv, **kw):
        raise RuntimeError("unexpected")
    doc = bt.parse_framed(bc.run_export(FP, _src(home, tmp_path, runner=boom)))
    assert doc["sections"]["services"] is None and "RuntimeError" in doc["section_errors"]["services"]
    assert doc["sections"]["raw_mini"]["reason"] == "café ☕"


def test_b5_partial_systemctl_show_falls_back_per_unit(home, tmp_path):
    def run(argv, **kw):
        if argv[:2] == ["systemctl", "list-unit-files"]:
            return SimpleNamespace(returncode=0, stdout=(app.APP + "-map.service enabled enabled\n").encode())
        if argv[:2] == ["systemctl", "show"] and len(argv) > 4:
            return SimpleNamespace(returncode=1, stdout=b"ActiveState=active\nUnitFileState=enabled\n")
        if argv[:2] == ["systemctl", "show"]:
            unit = argv[-1]
            st = {"meshtasticd": b"active", "rnsd": b"active", app.APP + "-map.service": b"failed"}[unit]
            return SimpleNamespace(returncode=0, stdout=b"ActiveState=" + st + b"\nUnitFileState=enabled\n")
        return _fake_runner()(argv, **kw)
    s, errors = bc.collect_sections(_src(home, tmp_path, runner=run))
    assert s["services"][app.APP + "-map.service"]["active"] == "failed"
    assert s["services"]["rnsd"]["active"] == "active"
