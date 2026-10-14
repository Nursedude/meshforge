"""NOC read grants on the target — add / revoke / dedupe / list
(NOC read grant session 2, 2026-10-14).

Owed by: B3 (validator + read-after-write), B4 (fingerprint identity), B5
(revoke by blob across both files, refuse-loud, atomic 0600, symlink),
S5 (one grant per fingerprint), S6 (real shell), S10 (measured preflight),
S12 (dedupe touches only exact duplicates). Acceptance 7 (bad pastes refused).
"""
import base64
import os
import struct
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_grants as bg  # noqa: E402


def _key(seed=b"\x11", comment="noc:center1"):
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + seed * 32
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}"


CENTER = _key()
CENTER_FP = bt.parse_noc_pubkey(CENTER).fingerprint
INTERACTIVE = _key(b"\x44", comment="wh6gxz@moc3").replace("noc:", "")


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    ssh = home / ".ssh"
    ssh.mkdir(parents=True, mode=0o700)
    os.chmod(ssh, 0o700)
    ak = ssh / "authorized_keys"
    ak.write_text(INTERACTIVE + "\n")
    os.chmod(ak, 0o600)
    export = tmp_path / "opt" / "box_truth_export.py"
    export.parent.mkdir()
    export.write_text("#!/usr/bin/env python3\n")
    os.chmod(export, 0o755)
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    return home, str(export), env


def _plan(box, pubkey=CENTER, **kw):
    home, export, _env = box
    kw.setdefault("center", "center1")
    return bg.plan_add(home, pubkey, export_path=export, shell="/bin/bash", sshd={}, **kw)


def test_add_writes_one_restricted_line_and_keeps_the_rest(box):
    home, export, env = box
    plan = _plan(box)
    assert not plan.problems, plan.problems
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    lines = (home / ".ssh" / "authorized_keys").read_text().splitlines()
    assert lines[0] == INTERACTIVE
    assert lines[1].startswith('restrict,command="/usr/bin/python3 -I ')
    assert f"--grant {CENTER_FP} </dev/null" in lines[1]
    assert oct((home / ".ssh" / "authorized_keys").stat().st_mode & 0o777) == "0o600"
    rows = bg.list_grants(home, env)
    assert len(rows) == 1 and rows[0]["fingerprint"] == CENTER_FP
    assert rows[0]["blob_matches"] and rows[0]["comment"] == "noc:center1"


def test_confirm_must_be_the_fingerprint_tail_not_the_box_name(box):
    home, _export, env = box
    plan = _plan(box)
    with pytest.raises(bg.GrantRefused):
        bg.apply_add(home, plan, "moc3", env=env)
    assert "restrict" not in (home / ".ssh" / "authorized_keys").read_text()


@pytest.mark.parametrize("paste", [
    CENTER + "\n" + _key(b"\x55"),                       # two-line
    'command="bash" ' + CENTER,                          # option-prefixed
    "ssh-rsa AAAAB3NzaC1yc2E noc:center1",             # RSA
])
def test_acceptance_7_bad_pastes_refused_with_reason(box, paste):
    home, _export, env = box
    plan = _plan(box, pubkey=paste)
    assert plan.problems and plan.line is None
    with pytest.raises(bg.GrantRefused):
        bg.apply_add(home, plan, "whatever", env=env)
    assert (home / ".ssh" / "authorized_keys").read_text() == INTERACTIVE + "\n"


def test_comment_must_name_the_center(box):
    assert any("does not name center" in p for p in _plan(box, center="moc").problems)


def test_second_grant_for_same_fingerprint_refused(box):
    home, _export, env = box
    plan = _plan(box)
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    assert any("already granted" in p for p in _plan(box).problems)


def test_key_with_interactive_access_cannot_be_shadow_granted(box):
    home, _e, _env = box
    interactive_center = CENTER.replace("noc:center1", "laptop")
    (home / ".ssh" / "authorized_keys").write_text(interactive_center + "\n")
    assert any("INTERACTIVE access" in p for p in _plan(box).problems)


def test_preflight_refusals_are_measured(box, tmp_path):
    home, export, _env = box
    os.chmod(home / ".ssh", 0o755)
    assert any("chmod 700" in p for p in _plan(box).problems)
    os.chmod(home / ".ssh", 0o700)
    assert any("not a real shell" in p for p in bg.plan_add(
        home, CENTER, center="center1", export_path=export,
        shell="/usr/sbin/nologin", sshd={}).problems)
    assert any("missing or not executable" in p for p in bg.plan_add(
        home, CENTER, center="center1", export_path=str(tmp_path / "nope" / "box_truth_export.py"),
        shell="/bin/bash", sshd={}).problems)


def test_symlinked_authorized_keys_refused(box, tmp_path):
    home, _e, _env = box
    real = tmp_path / "elsewhere"
    real.write_text("")
    ak = home / ".ssh" / "authorized_keys"
    ak.unlink()
    ak.symlink_to(real)
    assert any("symlink" in p for p in _plan(box).problems)


def test_sshd_unknown_is_said_not_assumed(box):
    text = _plan(box).render()
    assert "UNKNOWN (sshd -T not readable" in text
    assert "confirm     : type" in text


def test_revoke_by_blob_sweeps_both_files(box):
    home, _e, env = box
    plan = _plan(box)
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    grant_line = plan.line
    ak2 = home / ".ssh" / "authorized_keys2"
    ak2.write_text(grant_line + "\n")              # the hole B5 names
    os.chmod(ak2, 0o600)
    rp = bg.plan_revoke(home, CENTER_FP)
    assert not rp.problems and len(rp.removes) == 2
    assert bg.apply_revoke(home, rp, rp.confirm_tail, env=env) == 2
    assert bg.list_grants(home, env) == []
    assert (home / ".ssh" / "authorized_keys").read_text() == INTERACTIVE + "\n"


def test_revoke_refuses_a_markerless_line_unless_forced(box):
    home, _e, env = box
    interactive_center = CENTER.replace("noc:center1", "laptop")
    (home / ".ssh" / "authorized_keys").write_text(interactive_center + "\n")
    rp = bg.plan_revoke(home, CENTER_FP)
    assert any("WITHOUT a grant marker" in p for p in rp.problems)
    rp = bg.plan_revoke(home, CENTER_FP, force=True)
    assert not rp.problems
    assert bg.apply_revoke(home, rp, rp.confirm_tail, env=env) == 1


def test_revoke_of_unknown_fingerprint_says_so(box):
    home, _e, _env = box
    assert any("no line carries" in p for p in bg.plan_revoke(home, "SHA256:" + "Q" * 43).problems)


def test_dedupe_removes_only_exact_duplicates(box):
    home, _e, env = box
    near = INTERACTIVE + "-other"                  # differs by a byte: kept
    (home / ".ssh" / "authorized_keys").write_text(
        f"{INTERACTIVE}\n{near}\n{INTERACTIVE}\n")
    plan = bg.plan_dedupe(home)
    assert len(plan.removes) == 1 and plan.removes[0].index == 2
    assert bg.apply_dedupe(home, plan, env=env) == 1
    assert (home / ".ssh" / "authorized_keys").read_text().splitlines() == [INTERACTIVE, near]


def test_dedupe_refuses_a_file_changed_since_plan(box):
    home, _e, env = box
    (home / ".ssh" / "authorized_keys").write_text(f"{INTERACTIVE}\n{INTERACTIVE}\n")
    plan = bg.plan_dedupe(home)
    (home / ".ssh" / "authorized_keys").write_text(f"{INTERACTIVE}\n")
    with pytest.raises(bg.GrantRefused):
        bg.apply_dedupe(home, plan, env=env)


def test_list_shows_last_read_time_and_source(box):
    home, export, env = box
    plan = _plan(box)
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    from utils import box_truth_collect as bc
    w = bc.witness_path(home, env)
    w.parent.mkdir(parents=True, exist_ok=True)
    w.write_text('{"ts": 123.0, "fp": "%s", "src": "192.0.2.5", "outcome": "served"}\n' % CENTER_FP)
    row = bg.list_grants(home, env)[0]
    assert row["last_read_ts"] == 123.0 and row["last_read_src"] == "192.0.2.5"


def test_actions_are_logged(box):
    home, _e, env = box
    plan = _plan(box)
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    log = Path(env["XDG_STATE_HOME"]) / "box_truth" / "grants.jsonl"
    assert '"action": "add"' in log.read_text()
