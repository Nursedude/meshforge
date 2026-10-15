"""Center side of the NOC read grant — targets file, key, push, unpush,
rotate, bulk revoke, spool cron (NOC read grant session 2, 2026-10-14).

Owed by: B6 (push = the target's CLI, identity first), B7a (key 0600 under
~/.ssh), B7c / acceptance 8 (rotation), Q11 (grant registers / revoke
unregisters), Q12 (cron installed by tooling), Q18 (one target, name typed).
"""
import base64
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_app as app  # noqa: E402
from utils import box_truth_center as bc  # noqa: E402
from utils import box_truth_reader as br  # noqa: E402

EXPECT = "SHA256:" + "H" * 43


def _pub(seed=b"\x11", comment="noc:center1"):
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + seed * 32
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}\n"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".ssh").mkdir(parents=True)
    (h / ".config" / app.CONFIG_DIRNAME).mkdir(parents=True)
    key = br.noc_key_path(h)
    key.write_text("PRIVATE")
    Path(str(key) + ".pub").write_text(_pub())
    return h


class Fleet:
    """Records every remote command; answers per alias."""
    def __init__(self, fail_exec=(), fail_read=(), mismatch=()):
        self.cmds, self.reads = [], []
        self.fail_exec, self.fail_read, self.mismatch = fail_exec, fail_read, mismatch

    def exec_(self, alias, expect, command):
        self.cmds.append((alias, expect, command))
        if alias in self.mismatch:
            return br.ExecResult(alias, "identity_mismatch", stderr_head="wrong box")
        if alias in self.fail_exec:
            return br.ExecResult(alias, "failed", rc=1, stdout="REFUSED: x")
        return br.ExecResult(alias, "ok", rc=0)

    def read(self, alias, expect, key_path=None):
        self.reads.append((alias, key_path))
        if alias in self.fail_read:
            return br.ReadResult(alias, "refused_grant", rc=255, pinned=True)
        return br.ReadResult(alias, "ok", rc=0, doc={}, pinned=True)


# ── targets file ─────────────────────────────────────────────────────────

def test_targets_file_reads_flags_and_skips_invalid(tmp_path, capsys):
    p = tmp_path / "t"
    p.write_text("# head\nkiai   # tunnel\nmoc3 noc  # gateway\n-oProxy x\nlehua bogus\n")
    assert bc.read_targets(p) == [bc.Target("kiai", False), bc.Target("moc3", True)]
    assert "SKIP" in capsys.readouterr().err


def test_set_target_preserves_comments_and_other_lines(tmp_path):
    p = tmp_path / "t"
    p.write_text("# head\nkiai   # tunnel\nmoc3  # gateway-only\n")
    bc.set_target(p, "moc3", pinned=True)
    bc.set_target(p, "lehua", pinned=True)
    lines = p.read_text().splitlines()
    assert lines[:2] == ["# head", "kiai   # tunnel"]
    assert lines[2].startswith("moc3 noc") and "gateway-only" in lines[2]
    assert lines[3] == "lehua noc"
    bc.set_target(p, "moc3", pinned=None)
    assert [t.alias for t in bc.read_targets(p)] == ["kiai", "lehua"]


# ── push (B6, Q11, Q18) ──────────────────────────────────────────────────

def test_push_grants_via_target_cli_verifies_registers_and_wires_cron(home):
    f = Fleet()
    rep = bc.push("moc3", "moc3", home=home, expect=EXPECT, exec_=f.exec_,
                  read=f.read, cron=lambda: "installed")
    assert rep.ok, rep.steps
    alias, expect, cmd = f.cmds[0]
    assert (alias, expect) == ("moc3", EXPECT)
    assert f'-I "$d/scripts/{bc.GRANT_CLI_BASENAME}" add --pubkey ' in cmd
    assert "--center center1" in cmd and "authorized_keys" not in cmd
    assert f.reads == [("moc3", br.noc_key_path(home))]          # pinned read
    assert bc.read_targets(bc.targets_path(home)) == [bc.Target("moc3", True)]
    assert any("spool cron installed" in s for s in rep.steps)


@pytest.mark.parametrize("kw,why", [
    (dict(confirm="moc"), "typed confirmation"),
    (dict(expect=None), "no pinned host key"),
])
def test_push_refuses_before_any_remote_command(home, kw, why):
    f = Fleet()
    args = dict(confirm="moc3", expect=EXPECT)
    args.update(kw)
    rep = bc.push("moc3", args["confirm"], home=home, expect=args["expect"],
                  exec_=f.exec_, read=f.read, cron=lambda: "present")
    assert not rep.ok and why in "\n".join(rep.steps) and f.cmds == []


def test_push_on_the_wrong_box_registers_nothing(home):
    f = Fleet(mismatch=("moc3",))
    rep = bc.push("moc3", "moc3", home=home, expect=EXPECT, exec_=f.exec_,
                  read=f.read, cron=lambda: "present")
    assert not rep.ok and f.reads == []
    assert bc.read_targets(bc.targets_path(home)) == []


def test_push_whose_pinned_read_fails_does_not_register(home):
    f = Fleet(fail_read=("moc3",))
    rep = bc.push("moc3", "moc3", home=home, expect=EXPECT, exec_=f.exec_,
                  read=f.read, cron=lambda: "present")
    assert not rep.ok and "grant refused on moc3" in "\n".join(rep.steps)
    assert bc.read_targets(bc.targets_path(home)) == []


def test_unpush_revokes_and_leaves_the_target_unflagged(home):
    bc.set_target(bc.targets_path(home), "moc3", pinned=True)
    f = Fleet()
    rep = bc.unpush("moc3", "moc3", home=home, expect=EXPECT, exec_=f.exec_)
    assert rep.ok
    fp = bt.parse_noc_pubkey(_pub()).fingerprint
    assert f"revoke --fp {fp} --confirm {fp[-8:]}; fi; done" in f.cmds[0][2]
    # Q11 amended 2026-10-14 (operator): a removed line made the spool writer
    # skip the box, so /fleet served its last healthy snapshot for up to
    # SPOOL_STALE_S after the revoke. Unflagged = read DARK with the re-grant
    # named on the very next tick.
    assert [(t.alias, t.pinned) for t in bc.read_targets(bc.targets_path(home))] \
        == [("moc3", False)]


# ── rotation (B7c, acceptance 8) ─────────────────────────────────────────

def _fake_keygen(home, path):
    path.write_text("NEWPRIVATE")
    Path(str(path) + ".pub").write_text(_pub(b"\x99"))
    return bt.parse_noc_pubkey(_pub(b"\x99"))


def test_rotate_grants_new_everywhere_then_revokes_old_then_swaps(home):
    for a in ("moc3", "kiai"):
        bc.set_target(bc.targets_path(home), a, pinned=True)
    bc.set_target(bc.targets_path(home), "lehua", pinned=False)   # legacy: not rotated
    old_fp = bt.parse_noc_pubkey(_pub()).fingerprint
    new_fp = bt.parse_noc_pubkey(_pub(b"\x99")).fingerprint
    f = Fleet()
    rep = bc.rotate("rotate", home=home, expect_of=lambda a: EXPECT,
                    exec_=f.exec_, read=f.read, keygen=_fake_keygen)
    assert rep.ok, rep.steps
    kinds = [(a, "add" if " add " in c else "revoke") for a, _e, c in f.cmds]
    assert kinds == [("moc3", "add"), ("kiai", "add"), ("moc3", "revoke"), ("kiai", "revoke")]
    assert all(old_fp in c for _a, _e, c in f.cmds if " revoke " in c)
    assert [r[1].name for r in f.reads] == [app.NOC_KEY_BASENAME + ".new"] * 2
    assert bc.load_center_key(home).fingerprint == new_fp
    assert br.noc_key_path(home).read_text() == "NEWPRIVATE"


def test_rotate_stops_with_old_key_intact_on_first_failure(home):
    for a in ("moc3", "kiai"):
        bc.set_target(bc.targets_path(home), a, pinned=True)
    old_fp = bt.parse_noc_pubkey(_pub()).fingerprint
    f = Fleet(fail_read=("kiai",))
    rep = bc.rotate("rotate", home=home, expect_of=lambda a: EXPECT,
                    exec_=f.exec_, read=f.read, keygen=_fake_keygen)
    assert not rep.ok
    assert not any(" revoke " in c for _a, _e, c in f.cmds)
    assert bc.load_center_key(home).fingerprint == old_fp
    assert "STOP" in "\n".join(rep.steps)


def test_rotate_refuses_leftover_new_key(home):
    bc.set_target(bc.targets_path(home), "moc3", pinned=True)
    Path(str(br.noc_key_path(home)) + ".new").write_text("x")
    rep = bc.rotate("rotate", home=home, expect_of=lambda a: EXPECT,
                    exec_=Fleet().exec_, read=Fleet().read, keygen=_fake_keygen)
    assert not rep.ok and "interrupted rotate" in "\n".join(rep.steps)


def test_bulk_revoke_hits_every_pinned_target(home):
    for a in ("moc3", "kiai"):
        bc.set_target(bc.targets_path(home), a, pinned=True)
    fp = "SHA256:" + "L" * 43
    f = Fleet()
    rep = bc.bulk_revoke(fp, fp[-8:], home=home, expect_of=lambda a: EXPECT, exec_=f.exec_)
    assert rep.ok and [a for a, _e, _c in f.cmds] == ["moc3", "kiai"]


# ── key + cron ───────────────────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_create_center_key_is_0600_noc_comment_and_refuses_overwrite(tmp_path):
    h = tmp_path / "h"
    key = bc.create_center_key(h)
    p = br.noc_key_path(h)
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert key.comment == f"noc:{bc.center_name()}"
    with pytest.raises(bt.KeyRejected):
        bc.create_center_key(h)


def test_ensure_spool_cron_is_idempotent():
    state = {"tab": "0 * * * * other\n"}

    def run(argv, **kw):
        if argv == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0, stdout=state["tab"])
        if argv == ["crontab", "-"]:
            state["tab"] = kw["input"]
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(argv)
    assert bc.ensure_spool_cron(runner=run) == "installed"
    assert state["tab"].startswith("0 * * * * other\n")
    assert bc.ensure_spool_cron(runner=run) == "present"
    assert state["tab"].count("fleet_truth_spool.py") == 1


# ── review A N3/N4 + review B 6/7 ───────────────────────────────────────

def test_n3_bulk_revoke_refuses_a_malformed_fingerprint(home):
    bc.set_target(bc.targets_path(home), "moc3", pinned=True)
    f = Fleet()
    bad = "SHA256:abc;id>/tmp/p"
    rep = bc.bulk_revoke(bad, bad[-8:], home=home, expect_of=lambda a: EXPECT, exec_=f.exec_)
    assert not rep.ok and f.cmds == []


def test_n4_rotate_names_every_box_still_holding_the_old_key(home):
    for a in ("moc3", "kiai"):
        bc.set_target(bc.targets_path(home), a, pinned=True)
    old_fp = bt.parse_noc_pubkey(_pub()).fingerprint

    class Partial(Fleet):
        def exec_(self, alias, expect, command):
            if alias == "kiai" and " revoke " in command:
                self.cmds.append((alias, expect, command))
                return br.ExecResult(alias, "failed", rc=1, stdout="REFUSED")
            return super().exec_(alias, expect, command)
    rep = bc.rotate("rotate", home=home, expect_of=lambda a: EXPECT,
                    exec_=Partial().exec_, read=Partial().read, keygen=_fake_keygen)
    text = "\n".join(rep.steps)
    assert not rep.ok
    assert "OLD KEY STILL GRANTED on kiai" in text
    assert f"bulk-revoke --fp {old_fp}" in text
    assert "granted from their own side" in text


def test_b6_commented_spool_line_is_not_present():
    state = {"tab": "# " + bc.SPOOL_CRON_LINE + "\n"}

    def run(argv, **kw):
        if argv == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0, stdout=state["tab"])
        state["tab"] = kw["input"]
        return SimpleNamespace(returncode=0, stdout="")
    assert bc.ensure_spool_cron(runner=run) == "installed"


def test_b7_unregister_keeps_the_operator_comment_visible(tmp_path):
    p = tmp_path / "t"
    p.write_text("lehua noc  # AREDN field node; ssh alias carries :2200\n")
    bc.set_target(p, "lehua", pinned=None)
    text = p.read_text()
    assert bc.read_targets(p) == []
    assert "lehua" in text and ":2200" in text and text.lstrip().startswith("#")


# ── the target's grant CLI is FOUND, not assumed (session 3 seam) ────────
# A center pushes to a box running either app, or both. These run the exact
# string the center sends through a real POSIX shell, against fake installs,
# so the loop's quoting and its fallthrough are measured, not read.

_FAKE_CLI = ("import json, sys\n"
             "print(json.dumps({'cli': __file__, 'argv': sys.argv[1:]}))\n")


def _install(root, name):
    d = root / name / "scripts"
    d.mkdir(parents=True)
    (d / bc.GRANT_CLI_BASENAME).write_text(_FAKE_CLI)
    return str(root / name)


def _run_remote(cmd):
    return subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=30)


@pytest.fixture
def two_dirs(tmp_path, monkeypatch):
    own, sib = str(tmp_path / "own"), str(tmp_path / "sib")
    monkeypatch.setattr(app, "GRANT_CLI_SEARCH", (own, sib))
    return tmp_path, own, sib


def test_remote_add_runs_own_app_cli_first_with_argv_intact(two_dirs):
    import json
    root, own, sib = two_dirs
    _install(root, "own"), _install(root, "sib")
    key = bt.parse_noc_pubkey(_pub())
    center = "c1 'q' $HOME;x"            # shell metachars must arrive verbatim
    p = _run_remote(bc._add_cmd(key, center))
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)
    assert got["cli"] == f"{own}/scripts/{bc.GRANT_CLI_BASENAME}"
    assert got["argv"] == ["add", "--pubkey", key.line(), "--center", center,
                           "--confirm", key.fingerprint[-8:]]


def test_remote_falls_through_to_the_sibling_app(two_dirs):
    import json
    root, _own, sib = two_dirs
    _install(root, "sib")
    fp = bt.parse_noc_pubkey(_pub()).fingerprint
    p = _run_remote(bc._revoke_cmd(fp))
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)
    assert got["cli"] == f"{sib}/scripts/{bc.GRANT_CLI_BASENAME}"
    assert got["argv"] == ["revoke", "--fp", fp, "--confirm", fp[-8:]]


def test_remote_with_no_grant_cli_fails_loud_naming_the_dirs(two_dirs):
    _root, own, sib = two_dirs
    p = _run_remote(bc._revoke_cmd(bt.parse_noc_pubkey(_pub()).fingerprint))
    assert p.returncode == 127 and p.stdout == ""
    assert "not installed under" in p.stderr and own in p.stderr and sib in p.stderr


def test_remote_cli_is_one_simple_command_for_any_login_shell(two_dirs):
    """The target's LOGIN shell parses what the center sends. A bare POSIX
    `for…do…done` parses in bash/dash but not in fish or csh (non-author
    pass on 3f4c9996, item 1); wrapped as `sh -c '<loop>'` every login shell
    sees ONE simple command with one quoted argument. Measured here through
    sh (the fleet's shells); fish/csh reasoned, not installed."""
    import shlex
    root, own, _sib = two_dirs
    _install(root, "own")
    cmd = bc._revoke_cmd("SHA256:" + "A" * 43)
    words = shlex.split(cmd)
    assert words[:2] == ["sh", "-c"] and len(words) == 3, words
    assert words[2].startswith("for d in ")
    p = _run_remote(cmd)
    assert p.returncode == 0, p.stderr
