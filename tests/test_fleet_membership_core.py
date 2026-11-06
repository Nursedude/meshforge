"""Tests for the fleet-membership wizard core (first-run declaration).

The installing user declares standalone-or-fleet in the TUI instead of
hand-authoring ~/.config/meshforge/fleet_hosts. Pure logic pinned here:
input parsing (reject garbage loudly), state reading (absent/empty file =
standalone, never an error), atomic write with provenance header,
standalone declaration (rename, not delete — reversible), and reachability
probing with an injected runner (unreachable is reported per-host, never
folded into a healthy-looking summary).
"""
import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "launcher_tui"))

from handlers._fleet_membership_core import (  # noqa: E402
    declare_standalone,
    membership_state,
    parse_host_input,
    probe_hosts,
    write_fleet_hosts,
)


# ------------------------------------------------------------ parse_host_input

def test_parse_accepts_commas_whitespace_and_dedupes():
    assert parse_host_input("boxa, boxb boxc\nboxa") == ["boxa", "boxb", "boxc"]


def test_parse_rejects_shell_metacharacters():
    with pytest.raises(ValueError):
        parse_host_input("boxa; rm -rf /")
    with pytest.raises(ValueError):
        parse_host_input("box$(whoami)")


def test_parse_empty_input_is_empty_list():
    assert parse_host_input("   \n ") == []


# ----------------------------------------------------------- membership_state

def test_state_fleet_when_file_has_hosts(tmp_path):
    f = tmp_path / "fleet_hosts"
    f.write_text("boxa\nboxb  # comment\n")
    st = membership_state(str(f))
    assert st["mode"] == "fleet"
    assert st["hosts"] == ["boxa", "boxb"]


def test_state_standalone_when_file_absent_or_empty(tmp_path):
    f = tmp_path / "fleet_hosts"
    assert membership_state(str(f))["mode"] == "standalone"
    f.write_text("# only comments\n")
    assert membership_state(str(f))["mode"] == "standalone"


# ---------------------------------------------------------- write / standalone

def test_write_then_read_roundtrip(tmp_path):
    f = tmp_path / "sub" / "fleet_hosts"
    write_fleet_hosts(str(f), ["boxa", "boxb"])
    st = membership_state(str(f))
    assert st["mode"] == "fleet" and st["hosts"] == ["boxa", "boxb"]
    # provenance header so a later reader knows where this file came from
    assert f.read_text().startswith("#")


def test_declare_standalone_renames_not_deletes(tmp_path):
    f = tmp_path / "fleet_hosts"
    f.write_text("boxa\n")
    res = declare_standalone(str(f))
    assert res["action"] == "move" and not f.exists()
    assert Path(res["saved"]).exists()
    assert "boxa" in Path(res["saved"]).read_text()
    assert membership_state(str(f))["mode"] == "standalone"


def test_declare_standalone_on_absent_file_is_noop():
    assert declare_standalone("/nonexistent/path/fleet_hosts")["action"] == "noop"


# ----------------------------------------------------------------- probe_hosts

def test_probe_reports_per_host_honestly():
    results = probe_hosts(
        ["up1", "down1", "up2"],
        runner=lambda host, timeout_s: host.startswith("up"))
    assert results == [("up1", "ok"), ("down1", "unreachable"), ("up2", "ok")]


def test_probe_runner_exception_is_unreachable_not_crash():
    def boom(host, timeout_s):
        raise OSError("ssh missing")
    assert probe_hosts(["boxa"], runner=boom) == [("boxa", "unreachable")]


# ------------------------------------------------------- handler flow (smoke)

def test_edit_flow_writes_file_via_dialogs(tmp_path, monkeypatch):
    """menu → edit → probe summary → confirm → file lands with the hosts."""
    from unittest.mock import MagicMock
    import handlers._fleet_membership_core as fm
    from handlers.fleet_provision import FleetProvisionHandler

    hosts_file = tmp_path / "fleet_hosts"
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(hosts_file))
    monkeypatch.setattr(fm, "probe_hosts",
                        lambda hosts, runner=None, timeout_s=8.0:
                        [(h, "ok") for h in hosts])

    h = FleetProvisionHandler()
    h.ctx = MagicMock()
    h.ctx.dialog.menu.side_effect = [("edit"), None]      # edit, then exit
    h.ctx.dialog.inputbox.return_value = "boxa, boxb"
    h.ctx.dialog.yesno.return_value = True

    h._membership_menu()

    st = fm.membership_state(str(hosts_file))
    assert st["mode"] == "fleet" and st["hosts"] == ["boxa", "boxb"]



# ── Go standalone: the input table (Fable r20 #2/#3/#4/#7, own arc) ────────
# C = current list, D = saved .disabled. Every cell: what happens, and that
# NOTHING is lost. Names are *.invalid — no fixture can reach a box.

HOSTS = "a.invalid\nb.invalid\n"
SAVED = "old1.invalid\nold2.invalid\n"


def _mk(tmp_path, c, d):
    from launcher_tui.handlers._fleet_membership_core import DISABLED_SUFFIX
    p = tmp_path / "fleet_hosts"
    t = tmp_path / ("fleet_hosts" + DISABLED_SUFFIX)
    for node, spec in ((p, c), (t, d)):
        if spec == "hosts":
            node.write_text(HOSTS if node == p else SAVED)
        elif spec == "empty":
            node.write_text("# nobody\n")
        elif spec == "corrupt":
            node.write_bytes(b"\0\0\0\n")
        elif spec == "dangling":
            node.symlink_to(tmp_path / "gone")
        elif spec == "dir":
            node.mkdir()
    return p, t


@pytest.mark.parametrize("d", ["absent", "hosts", "empty", "corrupt", "dir"])
def test_standalone_absent_current_is_a_noop(tmp_path, d):
    p, t = _mk(tmp_path, "absent", d)
    before = sorted(x.name for x in tmp_path.iterdir())
    assert declare_standalone(str(p))["action"] == "noop"
    assert sorted(x.name for x in tmp_path.iterdir()) == before


@pytest.mark.parametrize("c", ["corrupt", "dangling", "dir"])
@pytest.mark.parametrize("d", ["absent", "hosts", "dir"])
def test_standalone_never_promotes_an_unusable_current(tmp_path, c, d):
    """#2: an unreadable current was renamed OVER the saved list; a
    dangling one read as 'already standalone'."""
    p, t = _mk(tmp_path, c, d)
    before = {x.name: (x.is_symlink(), x.is_dir(), x.read_bytes() if x.is_file() and not x.is_symlink() else None)
              for x in tmp_path.iterdir()}
    res = declare_standalone(str(p))
    assert res["action"] == "refuse" and res["why"]
    after = {x.name: (x.is_symlink(), x.is_dir(), x.read_bytes() if x.is_file() and not x.is_symlink() else None)
             for x in tmp_path.iterdir()}
    assert after == before


@pytest.mark.parametrize("d", ["absent", "hosts", "empty", "corrupt", "dir"])
def test_standalone_removes_an_empty_current_and_keeps_any_saved(tmp_path, d):
    """#4: an empty list set aside was advertised as restorable."""
    p, t = _mk(tmp_path, "empty", d)
    res = declare_standalone(str(p))
    assert res["action"] == "remove_empty" and not os.path.lexists(p)
    if d == "hosts":
        assert t.read_text() == SAVED
    if d == "absent":
        assert not os.path.lexists(t)


def test_standalone_with_hosts_and_no_saved_list_moves(tmp_path):
    p, t = _mk(tmp_path, "hosts", "absent")
    res = declare_standalone(str(p))
    assert res["action"] == "move" and not res["backup"]
    assert t.read_text() == HOSTS and not p.exists()


@pytest.mark.parametrize("d", ["hosts", "empty", "corrupt"])
def test_standalone_never_overwrites_a_saved_list(tmp_path, d):
    """#3 (pre-existing): the saved list was silently replaced."""
    p, t = _mk(tmp_path, "hosts", d)
    saved_before = t.read_bytes()
    res = declare_standalone(str(p))
    assert res["action"] == "move" and res["backup"]
    assert t.read_text() == HOSTS
    assert Path(res["backup"]).read_bytes() == saved_before


def test_standalone_refuses_a_directory_where_the_saved_list_goes(tmp_path):
    """#7: path.replace raised IsADirectoryError out of the handler."""
    p, t = _mk(tmp_path, "hosts", "dir")
    res = declare_standalone(str(p))
    assert res["action"] == "refuse" and p.read_text() == HOSTS and t.is_dir()


def test_standalone_refuses_when_the_files_changed_after_confirming(tmp_path):
    from launcher_tui.handlers._fleet_membership_core import plan_standalone
    p, t = _mk(tmp_path, "hosts", "absent")
    seen = plan_standalone(str(p))           # operator saw: move, no backup
    t.write_text(SAVED)                       # someone saves a list meanwhile
    res = declare_standalone(str(p), expect=seen)
    assert res["action"] == "refuse" and t.read_text() == SAVED and p.read_text() == HOSTS


def test_the_dialog_states_the_backup_before_acting(tmp_path):
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    p, t = _mk(tmp_path, "hosts", "hosts")
    asked, told = [], []

    class _D:
        def yesno(self, title, text, default_no=False):
            asked.append(text)
            return True
        def msgbox(self, title, text):
            told.append(text)

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_standalone(fm, {"path": str(p)})
    assert "ALREADY saved there is kept as" in asked[0]
    assert "previously saved list is at" in told[0]


def test_tools_note_compares_the_refused_PATH_not_a_substring(tmp_path):
    """#1: fleet_hosts.meshforge CONTAINS fleet_hosts — a per-repo refusal
    was hidden when the wizard file was itself unreadable."""
    from launcher_tui.handlers._fleet_membership_core import tools_note
    wiz = str(tmp_path / "fleet_hosts")
    view = {"kind": "refuse", "file": wiz + ".meshforge", "hosts": [],
            "path": wiz + ".meshforge",
            "why": "fleet host list %s.meshforge is unreadable (lists no hosts)" % wiz}
    st = {"mode": "unreadable", "path": wiz, "view": view}
    assert "REFUSE" in tools_note(st)
    view.update(path=wiz, file=wiz)
    assert tools_note(st) == ""



# ── Go standalone, round 2 (Fable: lower tiers, symlinks, names, rollback) ──


def test_standalone_refuses_a_symlinked_current(tmp_path):
    """#2 (VERIFIED): C -> D made D a self-loop; 'List preserved at' a lie."""
    from launcher_tui.handlers._fleet_membership_core import DISABLED_SUFFIX
    d = tmp_path / ("fleet_hosts" + DISABLED_SUFFIX)
    d.write_text(SAVED)
    c = tmp_path / "fleet_hosts"
    c.symlink_to(d)
    res = declare_standalone(str(c))
    assert res["action"] == "refuse" and "symlink" in res["why"]
    assert c.is_symlink() and d.read_text() == SAVED


def test_a_backup_name_is_never_reused(tmp_path, monkeypatch):
    """#3 (VERIFIED with a frozen clock): rename clobbered an older backup."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    monkeypatch.setattr(fm, "_utc_stamp", lambda: "20270115T080000Z")
    p, t = _mk(tmp_path, "hosts", "hosts")
    stamp = "20270115T080000Z"
    older = Path("%s.%s" % (t, stamp))
    older.write_text("older.invalid\n")
    res = declare_standalone(str(p))
    assert res["action"] == "move" and res["backup"] != str(older)
    assert older.read_text() == "older.invalid\n"
    assert Path(res["backup"]).read_text() == SAVED


def test_the_backup_name_confirmed_is_the_one_used(tmp_path, monkeypatch):
    """#4 (VERIFIED): the prompt's stamp was regenerated a second later."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    p, t = _mk(tmp_path, "hosts", "hosts")
    monkeypatch.setattr(fm, "_utc_stamp", lambda: "20270115T080000Z")
    seen = fm.plan_standalone(str(p))
    monkeypatch.setattr(fm, "_utc_stamp", lambda: "20270115T080001Z")
    res = declare_standalone(str(p), expect=seen)
    assert res["backup"] == seen["backup"] and Path(seen["backup"]).read_text() == SAVED


def test_a_failed_second_step_puts_the_saved_list_back(tmp_path, monkeypatch):
    """#6: the backup rename succeeded, the replace failed — no orphan."""
    p, t = _mk(tmp_path, "hosts", "hosts")
    real_replace = Path.replace

    def boom(self, target):
        if self == p:
            raise PermissionError(13, "Permission denied")
        return real_replace(self, target)
    monkeypatch.setattr(Path, "replace", boom)
    res = declare_standalone(str(p))
    assert res["action"] == "refuse" and "nothing was changed" in res["why"]
    assert t.read_text() == SAVED and p.read_text() == HOSTS
    assert [x.name for x in tmp_path.iterdir() if ".disabled." in x.name] == []


def _dialog(asked, told):
    class _D:
        def yesno(self, title, text, default_no=False):
            asked.append(text)
            return True
        def msgbox(self, title, text):
            told.append(text)
    return _D()


def test_a_lower_tier_is_named_before_and_after(tmp_path, monkeypatch):
    """#1 (VERIFIED): 'Standalone declared' while /etc kept the box in a fleet."""
    import utils.fleet_hosts as fhm
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    p, t = _mk(tmp_path, "hosts", "absent")
    etc = tmp_path / "etc_fleet_hosts"
    etc.write_text("etc-one.invalid\n")
    monkeypatch.delenv("MESHFORGE_FLEET_HOSTS", raising=False)
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    monkeypatch.setattr(fhm, "_tier_candidates", lambda *a, **k: [p, etc])
    monkeypatch.setattr(fhm, "resolve_fleet_hosts_file",
                        lambda *a, skip=None, **k: etc if (skip or not p.exists()) else p)
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(p))
    asked, told = [], []
    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _dialog(asked, told)})()
    h._membership_standalone(fm, {"path": str(p)})
    assert str(etc) in asked[0] and "STAYS in a fleet" in asked[0]
    assert str(etc) in told[0] and "now see no list" not in told[0]
    assert "switch to the standalone preset" not in told[0]


def test_remove_empty_does_not_call_a_directory_the_saved_list(tmp_path, monkeypatch):
    """#7: 'the saved list X is kept' for a directory / dangling link."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    p, t = _mk(tmp_path, "empty", "dir")
    monkeypatch.setattr(fm, "membership_state", lambda *a, **k: {"path": str(p), "mode": "standalone"})
    asked, told = [], []
    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _dialog(asked, told)})()
    h._membership_standalone(fm, {"path": str(p)})
    assert "saved list" not in asked[0]


# ── Go standalone, round 3 (Fable round 2: false statements) ───────────────


def test_remove_prompt_does_not_claim_every_tool_refuses_when_tools_read_another_file(tmp_path, monkeypatch):
    """r2 #1 (VERIFIED): the head said the tools read fleet_hosts.meshforge;
    the remove prompt then said 'every fleet tool refuses' THIS file."""
    import utils.fleet_hosts as fhm
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    p, t = _mk(tmp_path, "empty", "absent")
    other = tmp_path / "fleet_hosts.meshforge"
    other.write_text("x.invalid\n")
    monkeypatch.delenv("MESHFORGE_FLEET_HOSTS", raising=False)
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    monkeypatch.setattr(fhm, "_tier_candidates", lambda *a, **k: [other, p])
    monkeypatch.setattr(fhm, "resolve_fleet_hosts_file", lambda *a, **k: other)
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(p))
    asked, told = [], []
    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _dialog(asked, told)})()
    h._membership_standalone(fm, {"path": str(p)})
    assert "every fleet tool refuses" not in asked[0]
    assert str(other) in asked[0] and "STAYS in a fleet" in asked[0]


def test_an_env_override_is_named_before_confirming(tmp_path, monkeypatch):
    """r2 #4: the override the tools read FIRST was missing from the warning."""
    from launcher_tui.handlers._fleet_membership_core import plan_standalone
    p, t = _mk(tmp_path, "hosts", "absent")
    ov = tmp_path / "ov"
    ov.write_text("o.invalid\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(ov))
    plan = plan_standalone(str(p))
    v = plan["after_view"]          # THE resolver's answer, override first
    assert v["kind"] == "list" and v["file"] == str(ov)


def test_could_not_check_is_not_rendered_as_lists_exist(tmp_path, monkeypatch):
    """r2 #3 (VERIFIED): '(could not list …)' appeared under 'Other fleet
    lists exist'."""
    import utils.fleet_hosts as fhm
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    p, t = _mk(tmp_path, "hosts", "absent")
    monkeypatch.delenv("MESHFORGE_FLEET_HOSTS", raising=False)
    monkeypatch.delenv("FLEET_HOSTS", raising=False)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(fhm, "resolve_fleet_hosts_file", boom)
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(p))
    asked, told = [], []
    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _dialog(asked, told)})()
    h._membership_standalone(fm, {"path": str(p)})
    assert "Other fleet lists exist" not in asked[0] and "Could not check" in asked[0]


def test_a_failed_rollback_says_where_the_saved_list_is(tmp_path, monkeypatch):
    """r2 #2 (VERIFIED by fault injection): 'nothing was changed' while the
    saved list sat at its backup name."""
    p, t = _mk(tmp_path, "hosts", "hosts")
    real_replace, real_rename = Path.replace, Path.rename

    def no_replace(self, target):
        if self == p:
            raise OSError(5, "Input/output error")
        return real_replace(self, target)

    def no_rollback(self, target):
        if str(target) == str(t) and ".disabled." in self.name:
            raise OSError(5, "Input/output error")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "replace", no_replace)
    monkeypatch.setattr(Path, "rename", no_rollback)
    res = declare_standalone(str(p))
    assert res["action"] == "partial" and "nothing was changed" not in res["why"]
    assert res["backup"] in res["why"] and Path(res["backup"]).read_text() == SAVED
    assert p.read_text() == HOSTS and not t.exists()



# ── Go standalone, round 4 (Fable r3): ONE tools view, from THE resolver ──


def _run_standalone(tmp_path, monkeypatch, p):
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(p))
    asked, told = [], []
    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _dialog(asked, told)})()
    h._membership_standalone(fm, {"path": str(p)})
    return asked, told


def test_an_override_pointing_nowhere_is_not_called_another_list(tmp_path, monkeypatch):
    """r3 #2 (VERIFIED): 'Other fleet lists exist … may STAY in a fleet:
    $MESHFORGE_FLEET_HOSTS=~/nowhere' — no list exists anywhere."""
    p, t = _mk(tmp_path, "hosts", "absent")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(tmp_path / "nowhere"))
    asked, told = _run_standalone(tmp_path, monkeypatch, p)
    assert "STAY" not in asked[0] and "see NO list" in asked[0]


def test_an_empty_other_tier_is_said_to_be_refused(tmp_path, monkeypatch):
    """r3 #2: 'use the first readable one' — the resolver REFUSES it."""
    p, t = _mk(tmp_path, "hosts", "absent")
    ov = tmp_path / "ov"
    ov.write_text("# nobody\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(ov))
    from launcher_tui.handlers._fleet_membership_core import plan_standalone
    assert plan_standalone(str(p))["after_view"]["kind"] == "refuse"


def test_a_partial_result_says_the_tools_still_read_this_file(tmp_path, monkeypatch):
    """r3 #1 (VERIFIED 2x): 'FAILED PART-WAY … The fleet tools now see no
    list' while the tools read the untouched current list."""
    p, t = _mk(tmp_path, "hosts", "hosts")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(p))
    real_replace, real_rename = Path.replace, Path.rename

    def no_replace(self, target):
        if self == p:
            raise OSError(13, "Permission denied")
        return real_replace(self, target)

    def no_rollback(self, target):
        if str(target) == str(t) and ".disabled." in self.name:
            raise OSError(5, "Input/output error")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "replace", no_replace)
    monkeypatch.setattr(Path, "rename", no_rollback)
    asked, told = _run_standalone(tmp_path, monkeypatch, p)
    assert "FAILED PART-WAY" in told[0]
    assert "see NO list" not in told[0] and "read THIS file" in told[0]


def test_the_same_file_spelled_differently_is_this_file(tmp_path, monkeypatch):
    """r3 #3 (VERIFIED): an override == C via '..' read 'NOT this file'."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    d = tmp_path / "cfg"
    d.mkdir()
    p = d / "fleet_hosts"
    p.write_text(HOSTS)
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(d / ".." / "cfg" / "fleet_hosts"))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(p))
    st = fm.membership_state()
    assert st["mode"] == "fleet" and fm.tools_note(st) == ""


# ── Go standalone, round 5 (Fable r4) ───────────────────────────────────────


def test_a_symlink_to_the_list_previews_as_refused_not_no_list(tmp_path, monkeypatch):
    """r4 #1 (VERIFIED x4): a tier/override SYMLINK to C dangles once C is
    moved and the tools REFUSE it — the preview said 'see NO list'."""
    from launcher_tui.handlers._fleet_membership_core import plan_standalone
    p, t = _mk(tmp_path, "hosts", "absent")
    link = tmp_path / "ovlink"
    link.symlink_to(p)
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(link))
    v = plan_standalone(str(p))["after_view"]
    assert v["kind"] == "refuse" and "dangling symlink" in v["why"]


def test_skip_never_changes_an_unskipped_resolve(tmp_path):
    """skip defaults to None; with it None the resolver is unchanged."""
    from utils.fleet_hosts import resolve_fleet_hosts_file
    f = tmp_path / "fl"
    f.write_text("a.invalid\n")
    env = {"MESHFORGE_FLEET_HOSTS": str(f)}
    assert resolve_fleet_hosts_file(env=env) == f
    assert resolve_fleet_hosts_file(env=env, skip=str(tmp_path / "other")) == f
    assert resolve_fleet_hosts_file(env=env, skip=str(f)) is None


def test_the_edit_result_names_what_the_tools_read(tmp_path, monkeypatch):
    """r4 #2 (VERIFIED x3): 'Fleet panes pick this up on next run' while the
    tools read another tier."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    wiz = tmp_path / "wizard"
    other = tmp_path / "tools-list"
    other.write_text("repo.invalid\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(other))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(wiz))
    monkeypatch.setattr(fm, "probe_hosts", lambda hosts, **k: [(h, "ok") for h in hosts])
    told = []

    class _D:
        def inputbox(self, title, text, init=""):
            return "new.invalid"
        def yesno(self, title, text, default_no=False):
            return True
        def msgbox(self, title, text):
            told.append(text)

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_edit(fm, fm.membership_state())
    assert "pick this up" not in told[-1]
    assert str(other) in told[-1] and "NOT this file" in told[-1]


def test_the_head_note_is_actionable_not_a_box_verdict(tmp_path, monkeypatch):
    """r4 #3: 'this box STAYS in a fleet' read as a verdict under a FLEET head."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    p, t = _mk(tmp_path, "hosts", "absent")
    other = tmp_path / "tools-list"
    other.write_text("x.invalid\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(other))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(p))
    note = fm.tools_note(fm.membership_state())
    assert "Editing here does not change them" in note and "STAYS" not in note


# ── Go standalone, round 6 (Fable r5) ───────────────────────────────────────


def test_a_list_naming_the_moves_destination_is_not_predicted(tmp_path, monkeypatch):
    """r5 F1 (VERIFIED): override = .disabled -> preview said 'see NO list
    … standalone'; after the move the tools read .disabled (the moved list)."""
    from launcher_tui.handlers._fleet_membership_core import plan_standalone
    p, t = _mk(tmp_path, "hosts", "absent")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(t))
    v = plan_standalone(str(p))["after_view"]
    assert v["kind"] == "unknown" and "where this move puts a list" in v["why"]


def test_a_tier_symlinked_to_the_destination_is_not_predicted(tmp_path, monkeypatch):
    import utils.fleet_hosts as fhm
    from launcher_tui.handlers._fleet_membership_core import plan_standalone
    p, t = _mk(tmp_path, "hosts", "hosts")
    tier = tmp_path / "fleet_hosts.meshforge"
    tier.symlink_to(t)
    monkeypatch.delenv("MESHFORGE_FLEET_HOSTS", raising=False)
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    monkeypatch.setattr(fhm, "_tier_candidates", lambda *a, **k: [tier, p])
    assert plan_standalone(str(p))["after_view"]["kind"] == "unknown"


def test_declare_does_not_compute_a_preview_it_discards(tmp_path):
    """r5 E1."""
    p, t = _mk(tmp_path, "hosts", "absent")
    assert declare_standalone(str(p))["after_view"] is None


def test_edit_warns_before_replacing_a_symlinked_list(tmp_path, monkeypatch):
    """r5 O1: Edit silently replaced a symlink with a regular file."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    real = tmp_path / "real"
    real.write_text("r.invalid\n")
    wiz = tmp_path / "wizard"
    wiz.symlink_to(real)
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(wiz))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(wiz))
    prompts = []

    class _D:
        def inputbox(self, title, text, init=""):
            prompts.append(text)
            return None
        def msgbox(self, *a): pass

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_edit(fm, fm.membership_state())
    assert "is a symlink" in prompts[0] and "REPLACES the link" in prompts[0]


# ── write_fleet_hosts + Edit (Fable r6 F1/O1/O2) ────────────────────────────


@pytest.mark.parametrize("plant", ["symlink", "hardlink"])
def test_a_link_planted_at_the_old_tmp_name_cannot_redirect_the_write(tmp_path, plant):
    """r6 F1 (VERIFIED): the fixed 'fleet_hosts.tmp' followed a planted link
    and the Edit write destroyed the SAVED list."""
    from launcher_tui.handlers._fleet_membership_core import write_fleet_hosts
    p, t = _mk(tmp_path, "hosts", "hosts")
    planted = tmp_path / "fleet_hosts.tmp"
    if plant == "symlink":
        planted.symlink_to(t)
    else:
        os.link(t, planted)
    write_fleet_hosts(str(p), ["n.invalid"])
    assert t.read_text() == SAVED
    assert not p.is_symlink() and "n.invalid" in p.read_text()


def test_a_failed_write_leaves_no_tmp_and_the_list_untouched(tmp_path, monkeypatch):
    from launcher_tui.handlers import _fleet_membership_core as fm
    p, t = _mk(tmp_path, "hosts", "absent")

    def boom(a, b):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(fm.os, "replace", boom)
    with pytest.raises(OSError):
        fm.write_fleet_hosts(str(p), ["n.invalid"])
    assert p.read_text() == HOSTS
    assert [x.name for x in tmp_path.iterdir() if x.name.endswith(".tmp")] == []


def test_the_write_keeps_the_files_mode(tmp_path):
    from launcher_tui.handlers._fleet_membership_core import write_fleet_hosts
    p, t = _mk(tmp_path, "hosts", "absent")
    p.chmod(0o640)
    write_fleet_hosts(str(p), ["n.invalid"])
    assert (p.stat().st_mode & 0o777) == 0o640


def test_edit_reports_a_write_failure_and_stays(tmp_path, monkeypatch):
    """r6 O1: the OSError escaped to a generic 'run with sudo' dialog."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    p, t = _mk(tmp_path, "hosts", "absent")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(p))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(p))
    monkeypatch.setattr(fm, "probe_hosts", lambda hosts, **k: [(h, "ok") for h in hosts])

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(fm, "write_fleet_hosts", boom)
    told = []

    class _D:
        def inputbox(self, title, text, init=""):
            return "n.invalid"
        def yesno(self, *a, **k):
            return True
        def msgbox(self, title, text):
            told.append(text)

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_edit(fm, fm.membership_state())
    assert "Could not write" in told[-1] and "Nothing was changed" in told[-1]
    assert p.read_text() == HOSTS


@pytest.mark.parametrize("d,hint", [("hosts", True), ("empty", False), ("corrupt", False), ("dir", False)])
def test_the_restore_hint_needs_a_real_saved_list(tmp_path, monkeypatch, d, hint):
    """r6 O2: 'Edit restores it' for a socket/empty/corrupt .disabled."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    p, t = _mk(tmp_path, "absent", d)
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(tmp_path / "none"))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(p))
    heads = []

    class _D:
        def menu(self, title, text, choices):
            heads.append(text)
            return "back"

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_menu()
    assert ("Edit restores it" in heads[0]) == hint
