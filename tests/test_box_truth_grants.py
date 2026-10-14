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


# ── review A (2026-10-14): hostile file contents ─────────────────────────

def test_f1_rewrite_never_splits_on_unicode_line_separators(box):
    """splitlines() split U+2028 — a key smuggled in a comment became its own
    LIVE line after an unrelated add (reproduced against a real sshd)."""
    home, _e, env = box
    smuggled = INTERACTIVE + " " + _key(b"\x77", comment="smuggled").replace("noc:", "")
    ak = home / ".ssh" / "authorized_keys"
    ak.write_bytes((smuggled + "\n").encode("utf-8"))
    before = ak.read_bytes()
    plan = _plan(box)
    assert not plan.problems, plan.problems
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    after = ak.read_bytes().split(b"\n")
    assert after[0] == before.split(b"\n")[0]          # untouched line byte-identical
    assert len([x for x in after if x]) == 2


def test_f1_non_utf8_byte_does_not_disable_the_tool(box):
    home, _e, env = box
    ak = home / ".ssh" / "authorized_keys"
    ak.write_bytes(INTERACTIVE.encode() + b" caf\xe9\n")
    plan = _plan(box)
    assert not plan.problems, plan.problems
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    assert ak.read_bytes().startswith(INTERACTIVE.encode() + b" caf\xe9\n")
    rp = bg.plan_revoke(home, CENTER_FP)
    assert bg.apply_revoke(home, rp, rp.confirm_tail, env=env) == 1
    assert ak.read_bytes() == INTERACTIVE.encode() + b" caf\xe9\n"


def test_f2_backslash_decoy_cannot_hide_a_key_from_revoke(box):
    """OpenSSH escapes only \\" inside quotes; a parser that skipped any \\X
    read this line as the decoy key, so revoke said 'removed' while the real
    key kept authenticating (reproduced)."""
    home, _e, env = box
    plan = _plan(box)
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    decoy = _key(b"\x66", comment="decoy").replace("noc:", "")
    tricky = f'command="echo \\\\" {decoy}" {CENTER}'
    ak = home / ".ssh" / "authorized_keys"
    ak.write_text(ak.read_text() + tricky + "\n")
    e = bt.parse_authorized_keys_line(tricky)
    assert e is not None and e.fingerprint == CENTER_FP
    rp = bg.plan_revoke(home, CENTER_FP, force=True)
    assert len(rp.removes) == 2
    bg.apply_revoke(home, rp, rp.confirm_tail, env=env)
    assert CENTER.split()[1] not in ak.read_text()


def test_f2_unparseable_line_makes_add_and_revoke_refuse(box):
    home, _e, env = box
    ak = home / ".ssh" / "authorized_keys"
    ak.write_text(INTERACTIVE + '\ncommand="unterminated ssh-ed25519 AAAA x\n')
    assert any("could not parse" in p for p in _plan(box).problems)
    assert any("could not parse" in p for p in bg.plan_revoke(home, CENTER_FP).problems)


@pytest.mark.parametrize("opts", [
    'command="/usr/bin/python3 -I {exp} --grant {fp} </dev/null"',                 # no restrict
    'restrict,port-forwarding,pty,command="/usr/bin/python3 -I {exp} --grant {fp} </dev/null"',
    'restrict,command="/bin/bash; {exp} --grant {fp}"',
    'RESTRICT,COMMAND="/usr/bin/python3 -I {exp} --grant {fp} </dev/null"',
])
def test_f3_only_the_exact_built_line_is_a_grant(box, opts):
    home, export, env = box
    line = opts.format(exp=export, fp=CENTER_FP) + " " + CENTER
    e = bt.parse_authorized_keys_line(line)
    assert not e.is_grant and e.foreign
    (home / ".ssh" / "authorized_keys").write_text(line + "\n")
    rows = bg.list_grants(home, env)
    assert rows and rows[0]["foreign"]                    # listed WITH a warning
    rp = bg.plan_revoke(home, CENTER_FP)
    assert rp.problems                                    # not ours: operator decides


def test_f7_sudo_add_hands_every_created_path_to_the_home_owner(tmp_path):
    """Review F7 (Fable pass 2026-10-14, measured with geteuid patched to 0):
    `apply_add` under sudo created ~/.ssh, ~/.local, ~/.local/state,
    ~/.local/state/box_truth/ and grants.jsonl ROOT-owned and chowned only
    authorized_keys. The forced command runs as the USER and cannot open
    reads.lock in a root-owned 0700 dir — so the first grant read refused
    `witness`, a blindness our own writer caused. Every path the writer
    creates must belong to the home's owner (same reference as
    create_center_key, review B 2)."""
    home = tmp_path / "home"
    home.mkdir()                                   # no ~/.ssh, no ~/.local yet
    export = tmp_path / "box_truth_export.py"
    export.write_text("x")
    os.chmod(export, 0o755)
    env = {"XDG_STATE_HOME": str(home / ".local" / "state")}
    owner = (os.stat(home).st_uid, os.stat(home).st_gid)
    by_path, by_inode = {}, {}

    def chown(p, u, g, **_kw):
        by_path[str(p)] = (u, g)

    def fchown(fd, u, g):
        by_inode[os.fstat(fd).st_ino] = (u, g)

    from unittest import mock
    with mock.patch.object(os, "geteuid", lambda: 0), \
            mock.patch.object(os, "chown", chown), \
            mock.patch.object(os, "fchown", fchown):
        plan = bg.plan_add(home, CENTER, center="center1", export_path=str(export),
                           shell="/bin/bash", sshd={})
        assert not plan.problems, plan.problems
        bg.apply_add(home, plan, plan.confirm_tail, env=env)

    state = home / ".local" / "state" / "box_truth"
    for created in (home / ".ssh", home / ".local", home / ".local" / "state",
                    state, state / "grants.jsonl"):
        assert created.exists(), created
        assert by_path.get(str(created)) == owner, \
            f"{created} not handed to the home owner (chowned: {sorted(by_path)})"
    ak = home / ".ssh" / "authorized_keys"
    assert by_inode.get(os.stat(ak).st_ino) == owner, "authorized_keys not fchowned to the home owner"


def test_dedupe_plan_counts_existing_grants(box):
    """Live on moc3 2026-10-14: the dedupe plan rendered `grants now : 0`
    beside a live grant — plan_dedupe never filled existing_grants, and a
    reader could take the render as "no grant on this box"."""
    home, export, env = box
    plan = _plan(box)
    bg.apply_add(home, plan, plan.confirm_tail, env=env)
    ak = home / ".ssh" / "authorized_keys"
    ak.write_text(ak.read_text() + INTERACTIVE + "\n")      # an exact duplicate to plan
    dp = bg.plan_dedupe(home)
    assert len(dp.removes) == 1
    assert dp.existing_grants == 1
    assert "grants now  : 1" in dp.render()
