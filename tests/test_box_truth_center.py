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
    (h / ".config" / "meshforge").mkdir(parents=True)
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


def test_spool_writer_reads_the_same_targets_file(home):
    """One path, two consumers (hfm #5): the spool imports it from here."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fts", Path(__file__).resolve().parent.parent / "scripts" / "fleet_truth_spool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.targets_path is bc.targets_path and mod.read_targets is bc.read_targets


# ── push (B6, Q11, Q18) ──────────────────────────────────────────────────

def test_push_grants_via_target_cli_verifies_registers_and_wires_cron(home):
    f = Fleet()
    rep = bc.push("moc3", "moc3", home=home, expect=EXPECT, exec_=f.exec_,
                  read=f.read, cron=lambda: "installed")
    assert rep.ok, rep.steps
    alias, expect, cmd = f.cmds[0]
    assert (alias, expect) == ("moc3", EXPECT)
    assert cmd.startswith(f"/usr/bin/python3 -I {bc.REMOTE_GRANT_CLI} add --pubkey ")
    assert "--center center1" in cmd and "authorized_keys" not in cmd
    assert f.reads == [("moc3", br.noc_key_path(home))]          # pinned read
    assert bc.read_targets(bc.targets_path(home)) == [bc.Target("moc3", True)]
    assert any("spool cron installed" in s for s in rep.steps)


@pytest.mark.parametrize("kw,why", [
    (dict(confirm="moc"), "typed confirmation"),
    (dict(expect=None), "no expect_hostkey"),
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


def test_unpush_revokes_and_unregisters(home):
    bc.set_target(bc.targets_path(home), "moc3", pinned=True)
    f = Fleet()
    rep = bc.unpush("moc3", "moc3", home=home, expect=EXPECT, exec_=f.exec_)
    assert rep.ok
    fp = bt.parse_noc_pubkey(_pub()).fingerprint
    assert f.cmds[0][2].endswith(f"revoke --fp {fp} --confirm {fp[-8:]}")
    assert bc.read_targets(bc.targets_path(home)) == []


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
    assert [r[1].name for r in f.reads] == ["meshforge_noc_ed25519.new"] * 2
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


def test_spool_cron_line_is_the_live_one():
    """The line the tooling installs must be the one the manager runs today
    (wired by hand 2026-07-19) — so installing it on a fresh center changes
    nothing on this one."""
    try:
        tab = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("no crontab here")
    if "fleet_truth_spool.py" not in tab:
        pytest.skip("not a center")
    assert bc.SPOOL_CRON_LINE in tab
