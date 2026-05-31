"""Tests for the fleet mini-dudeai posture rollup (rollup.py).

Pure functions (resolve/parse/build) tested directly; ssh collection tested with
an injected runner so no network is touched.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai.rollup import (  # noqa: E402
    build_rollup,
    collect_fleet,
    collect_local,
    collect_remote,
    parse_state_posture,
    resolve_fleet_hosts,
)

NOW = 1_780_000_000.0


# === resolve_fleet_hosts =========================================

def test_resolve_hosts_from_env_path(tmp_path):
    f = tmp_path / "fleet_hosts"
    f.write_text("# comment\nmoc\nmoc1  \n\n  # blank+comment\nmoc2\n")
    hosts = resolve_fleet_hosts({"MESHFORGE_FLEET_HOSTS": str(f)})
    assert hosts == ["moc", "moc1", "moc2"]


def test_resolve_hosts_from_home_default(tmp_path):
    cfg = tmp_path / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    (cfg / "fleet_hosts").write_text("boxA\nboxB\n")
    hosts = resolve_fleet_hosts({"HOME": str(tmp_path)})
    assert hosts == ["boxA", "boxB"]


def test_resolve_hosts_empty_when_none(tmp_path):
    # HOME points at an empty dir, no /etc file expected in CI
    assert resolve_fleet_hosts({"HOME": str(tmp_path)}) == []


def test_resolve_hosts_inline_comment_stripped(tmp_path):
    f = tmp_path / "fleet_hosts"
    f.write_text("moc   # the gateway\n")
    assert resolve_fleet_hosts({"MESHFORGE_FLEET_HOSTS": str(f)}) == ["moc"]


# === parse_state_posture =========================================

def test_parse_fresh():
    st = {"last_tick_ts": NOW - 10, "rule_count": 8, "error_count": 0, "host": "moc"}
    p = parse_state_posture("moc", st, NOW)
    assert p["status"] == "fresh"
    assert p["rule_count"] == 8 and p["src_errors"] == 0


def test_parse_stale():
    st = {"last_tick_ts": NOW - 9999, "rule_count": 8, "host": "moc"}
    p = parse_state_posture("moc", st, NOW)
    assert p["status"] == "stale"


def test_parse_no_state():
    assert parse_state_posture("moc", {}, NOW)["status"] == "no_state"
    assert parse_state_posture("moc", None, NOW)["status"] == "no_state"


def test_parse_extracts_active_rules():
    st = {
        "last_tick_ts": NOW,
        "rules": {
            "k1": {"rule_id": "r1", "subject": "moc3", "currently_active": True,
                   "last_detail": "in_backoff"},
            "k2": {"rule_id": "r2", "subject": "x", "currently_active": False},
        },
    }
    p = parse_state_posture("moc", st, NOW)
    assert len(p["active"]) == 1
    assert p["active"][0]["rule_id"] == "r1"


def test_parse_self_box_flag():
    p = parse_state_posture("managerbox", {"last_tick_ts": NOW}, NOW, self_box=True)
    assert p["self_box"] is True


# === collect_remote (injected runner) ============================

def _runner(rc, out, err=""):
    return lambda host, timeout_s: (rc, out, err)


def test_collect_remote_unreachable_is_ssh_255_only():
    p = collect_remote("host1", NOW, runner=_runner(255, "", "Connection refused"))
    assert p["status"] == "unreachable"
    assert "Connection refused" in p["error"]


def test_collect_remote_cat_failure_is_no_mini_not_unreachable():
    # ssh OK, remote `cat` rc=1 (no state file) → no_mini, NOT unreachable
    p = collect_remote("host2", NOW,
                       runner=_runner(1, "", "cat: mini_dudeai_state.json: No such file"))
    assert p["status"] == "no_mini"


def test_collect_remote_no_mini_empty():
    p = collect_remote("host3", NOW, runner=_runner(0, "   "))
    assert p["status"] == "no_mini"


def test_collect_remote_no_mini_bad_json():
    p = collect_remote("moc", NOW, runner=_runner(0, "not json"))
    assert p["status"] == "no_mini"


def test_collect_remote_fresh():
    st = json.dumps({"last_tick_ts": NOW - 5, "rule_count": 12, "host": "moc1"})
    p = collect_remote("moc1", NOW, runner=_runner(0, st))
    assert p["status"] == "fresh" and p["rule_count"] == 12


# === collect_local ===============================================

def test_collect_local_reads_file(tmp_path):
    sp = tmp_path / "mini_dudeai_state.json"
    sp.write_text(json.dumps({"last_tick_ts": NOW, "rule_count": 12, "host": "managerbox"}))
    p = collect_local(NOW, str(sp))
    assert p is not None and p["self_box"] is True and p["host"] == "managerbox"


def test_collect_local_missing_returns_none(tmp_path):
    assert collect_local(NOW, str(tmp_path / "nope.json")) is None


# === build_rollup ================================================

def test_build_rollup_summary_and_ordering():
    postures = [
        {"host": "moc1", "status": "fresh", "age": "5s", "rule_count": 12,
         "src_errors": 0, "active": [], "self_box": True},
        {"host": "moc", "status": "unreachable", "error": "timeout"},
        {"host": "moc3", "status": "stale", "age": "2h", "rule_count": 8,
         "src_errors": 0, "active": []},
        {"host": "meshanchor-server", "status": "no_mini", "error": "no state"},
    ]
    out = build_rollup(postures, NOW)
    assert "4 boxes" in out
    assert "1 fresh" in out and "1 stale" in out
    # problems first: unreachable (moc) before stale before fresh
    assert out.index("**moc**") < out.index("**moc3**") < out.index("**moc1**")
    assert "(self)" in out
    assert "daemon may be down" in out  # stale annotation


def test_build_rollup_renders_active_rules():
    postures = [{
        "host": "moc3", "status": "fresh", "age": "1s", "rule_count": 8,
        "src_errors": 0, "self_box": False,
        "active": [{"rule_id": "backoff", "subject": "moc3", "detail": "in_backoff=True"}],
    }]
    out = build_rollup(postures, NOW)
    assert "active: backoff" in out and "in_backoff=True" in out


# === collect_fleet (local + injected remotes) ====================

def test_collect_fleet_local_plus_remotes(tmp_path):
    sp = tmp_path / "mini_dudeai_state.json"
    sp.write_text(json.dumps({"last_tick_ts": NOW, "rule_count": 12, "host": "managerbox"}))
    hf = tmp_path / "fleet_hosts"
    hf.write_text("moc\nmoc1\n")
    st = json.dumps({"last_tick_ts": NOW, "rule_count": 8, "host": "remote"})
    postures = collect_fleet(
        NOW,
        runner=_runner(0, st),
        env={"MESHFORGE_FLEET_HOSTS": str(hf)},
        local_state_path=str(sp),
    )
    assert [p["host"] for p in postures] == ["managerbox", "moc", "moc1"]
    assert postures[0]["self_box"] is True
    assert all(p["status"] == "fresh" for p in postures)
