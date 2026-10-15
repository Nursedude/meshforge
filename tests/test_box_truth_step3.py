"""NOC read grant session 3, step 3 (2026-10-15): the parts a MeshAnchor
center needs that MeshForge already had — shared, byte-identical with
MeshAnchor (parity BYTE_IDENTICAL), so nothing here names an app.

* the spool constants live in the SHARED center module (one constant for
  writer and reader — hfm #5), not in MeshForge's collector;
* a target's pinned host key comes from the per-app seam
  (``app.expect_hostkey_of``) so the spool writer and the grant CLI are the
  same file in both apps;
* Q6: a box's grant CLI refuses ``add`` unless THIS app has a running unit
  on the box — a dormant checkout must not answer for a box it does not run.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from utils import box_truth_app as app  # noqa: E402
from utils import box_truth_center as bc  # noqa: E402
from utils import box_truth_collect as bcol  # noqa: E402


def test_spool_constants_live_in_the_shared_center():
    assert bc.SPOOL_SCHEMA == "truth_spool/v1"
    assert bc.SPOOL_STALE_S == 360.0


def test_spool_dir_is_this_apps_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert bc.truth_spool_dir() == tmp_path / app.CONFIG_DIRNAME / "truth_spool"


def test_expect_hostkey_of_answers_a_pair_never_raises():
    got = app.expect_hostkey_of("no-such-box-zz9")
    assert isinstance(got, tuple) and len(got) == 2
    expect, why = got
    assert expect is None
    assert why is None or isinstance(why, str)


class _Run:
    """A planted systemctl: ``list-units <pattern> --state=active`` per scope.
    ``system`` / ``user`` are the active unit names each scope reports; a
    scope set to None fails."""
    def __init__(self, system=(), user=(), fail=()):
        self.scopes = {"system": system, "user": user}
        self.fail = fail
        self.calls = []

    def __call__(self, argv, **k):
        import subprocess
        scope = "user" if "--user" in argv else "system"
        self.calls.append(argv)
        if scope in self.fail:
            raise OSError("no systemd answer")
        out = "".join(f"{u} loaded active running x\n" for u in self.scopes[scope])
        return subprocess.CompletedProcess(argv, 0, out.encode(), b"")


def _src(tmp_path, runner):
    return bcol.Sources(home=tmp_path, env={}, runner=runner)


UNIT = app.UNIT_GLOB.replace("*", "-x")


def test_own_app_running_true_for_an_active_system_unit(tmp_path):
    ok, why = bcol.own_app_running(_src(tmp_path, _Run(system=[UNIT])))
    assert ok is True and why is None


def test_own_app_running_true_for_a_user_unit_alone(tmp_path):
    # a granted box runs the app only as a user unit (+ a timer)
    run = _Run(user=[UNIT.replace(".service", "-mini.service")])
    ok, _ = bcol.own_app_running(_src(tmp_path, run))
    assert ok is True
    assert all(app.UNIT_GLOB.rsplit(".", 1)[0] in c for c in run.calls)


def test_own_app_running_false_when_nothing_of_the_app_is_active(tmp_path):
    ok, why = bcol.own_app_running(_src(tmp_path, _Run()))
    assert ok is False and app.APP in why


def test_own_app_running_is_unknown_when_a_scope_fails_and_none_is_active(tmp_path):
    ok, why = bcol.own_app_running(_src(tmp_path, _Run(fail=("user",))))
    assert ok is None and "user" in why


def test_own_app_running_true_even_if_the_other_scope_fails(tmp_path):
    ok, _ = bcol.own_app_running(_src(tmp_path, _Run(system=[UNIT], fail=("user",))))
    assert ok is True


@pytest.mark.parametrize("state", [False, None])
def test_grant_add_refuses_on_a_box_this_app_does_not_run(monkeypatch, capsys, state):
    import box_truth_grant as cli
    monkeypatch.setattr(cli.bcol, "own_app_running",
                        lambda src: (state, "planted: nothing of this app runs"))
    called = []
    monkeypatch.setattr(cli.bg, "plan_add", lambda *a, **k: called.append(1))
    rc = cli.main(["add", "--pubkey", "ssh-ed25519 AAAA x", "--center", "c"])
    out = capsys.readouterr().out
    assert rc == 1 and not called
    assert "REFUSED" in out and app.APP in out


def _spool(tmp_path, alias, doc):
    import json
    (tmp_path / f"{alias}.json").write_text(json.dumps(doc))


def test_read_spool_freshness_is_one_rule(tmp_path):
    _spool(tmp_path, "a", {"schema": bc.SPOOL_SCHEMA, "fetched_at": 1000.0})
    assert bc.read_spool("a", now=1000.0 + bc.SPOOL_STALE_S, spool_dir=tmp_path)
    assert bc.read_spool("a", now=1000.0 + bc.SPOOL_STALE_S + 1, spool_dir=tmp_path) is None
    assert bc.read_spool("a", now=999.0, spool_dir=tmp_path) is None   # future stamp


def test_read_spool_refuses_a_foreign_schema_or_missing_file(tmp_path):
    _spool(tmp_path, "b", {"schema": "other/v9", "fetched_at": 1000.0})
    assert bc.read_spool("b", now=1000.0, spool_dir=tmp_path) is None
    assert bc.read_spool("nope", now=1000.0, spool_dir=tmp_path) is None


def test_a_target_alias_with_a_trailing_newline_is_skipped(tmp_path):
    p = tmp_path / "targets"
    p.write_text("moc3\n")
    assert [t.alias for t in bc.read_targets(p)] == ["moc3"]
    assert bc._ALIAS_RE.match("moc3\n") is None   # the call sites use .match


def test_the_spool_cron_install_creates_its_log_dir(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(bc, "get_real_user_home", lambda: tmp_path)
    state = {"tab": ""}

    def runner(argv, **k):
        if argv[:2] == ["crontab", "-"]:
            state["tab"] = k["input"]
        return subprocess.CompletedProcess(argv, 0, state["tab"], "")
    assert bc.ensure_spool_cron(runner=runner) == "installed"
    assert (tmp_path / ".local" / "state" / app.CONFIG_DIRNAME / "cron_out").is_dir()
