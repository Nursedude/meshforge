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


# === deep merge: escalations + fires across the fleet ============

from mini_dudeai.rollup import (  # noqa: E402
    _DEEP_SENTINEL,
    build_box_deep,
    build_deep_feed,
    collect_fleet_deep,
    collect_remote_deep,
)
from mini_dudeai.brief import recent_escalations  # noqa: E402


def _hist_escalation(ts, rule, subject, detail, note=None):
    esc = {"rule": rule, "subject": subject, "detail": detail}
    if note:
        esc["note"] = note
    return {"ts": ts, "iso": "2026-05-31T00:00:00", "transition": "edge_up",
            "rule_id": rule, "subject": subject, "detail": detail,
            "outcome": {"extras": {"escalation": esc}}}


def _hist_fire(ts, rule, subject, detail):
    return {"ts": ts, "iso": "2026-05-31T00:00:00", "transition": "edge_up",
            "rule_id": rule, "subject": subject, "detail": detail,
            "outcome": {"extras": {}}}


# --- recent_escalations with_ts (brief.py addition) ---

def test_recent_escalations_with_ts_returns_tuples():
    h = [_hist_escalation(NOW - 100, "r1", "s1", "d1")]
    out = recent_escalations(h, NOW, with_ts=True)
    assert isinstance(out[0], tuple) and out[0][0] == NOW - 100
    # default unchanged
    assert recent_escalations(h, NOW)[0]["rule"] == "r1"


# --- build_box_deep ---

def test_build_box_deep_tags_escalations_and_fires():
    history = [
        _hist_escalation(NOW - 10, "esc_rule", "moc3", "peer unhealthy"),
        _hist_fire(NOW - 20, "fire_rule", "moc", "blip"),
    ]
    state = {"last_tick_ts": NOW, "rule_count": 8}
    rec = build_box_deep("moc", state, history, NOW)
    assert rec["status"] == "fresh"
    assert len(rec["escalations"]) == 1 and rec["escalations"][0]["box"] == "moc"
    # the escalation row is ALSO an edge_up, so it appears in fires too
    assert {f["rule_id"] for f in rec["fires"]} == {"esc_rule", "fire_rule"}
    assert all(f["box"] == "moc" for f in rec["fires"])


def test_build_box_deep_marks_stale_box():
    rec = build_box_deep("moc", {"last_tick_ts": NOW - 9999}, [
        _hist_escalation(NOW - 50, "r", "s", "d")], NOW)
    assert rec["status"] == "stale"
    assert rec["escalations"][0]["stale"] is True


# --- collect_remote_deep (injected runner) ---

def _deep_runner(rc, state, history_lines, err=""):
    payload = (json.dumps(state) if state else "") + f"\n{_DEEP_SENTINEL}\n" + \
        "\n".join(json.dumps(h) for h in history_lines)
    return lambda host, timeout_s: (rc, payload, err)


def test_collect_remote_deep_parses_state_and_history():
    st = {"last_tick_ts": NOW, "rule_count": 8}
    h = [_hist_escalation(NOW - 5, "r1", "x", "d")]
    rec = collect_remote_deep("host1", NOW, runner=_deep_runner(0, st, h))
    assert rec["status"] == "fresh" and len(rec["escalations"]) == 1


def test_collect_remote_deep_unreachable():
    rec = collect_remote_deep("host1", NOW, runner=lambda h, t: (255, "", "refused"))
    assert rec["status"] == "unreachable" and rec["escalations"] == []


def test_collect_remote_deep_no_mini_empty():
    rec = collect_remote_deep("host1", NOW, runner=lambda h, t: (0, f"\n{_DEEP_SENTINEL}\n", ""))
    assert rec["status"] == "no_mini"


def test_deep_sentinel_is_shell_safe():
    # the sentinel is echoed by the REMOTE shell — it must contain no shell
    # metacharacters, else the command mangles and the box reads false no_mini.
    assert not (set(_DEEP_SENTINEL) & set("<>|&;$`()\"' \t*?[]{}#~!"))


# --- build_deep_feed ---

def test_build_deep_feed_escalations_first_newest_first_tagged():
    results = [
        {"host": "managerbox", "status": "fresh", "self_box": True,
         "escalations": [{"ts": NOW - 100, "box": "managerbox", "stale": False,
                          "esc": {"rule": "fed_down", "subject": "peer", "detail": "503"}}],
         "fires": []},
        {"host": "moc3", "status": "fresh",
         "escalations": [{"ts": NOW - 5, "box": "moc3", "stale": False,
                          "esc": {"rule": "wd", "subject": "moc3", "detail": "rnsd"}}],
         "fires": [{"ts": NOW - 5, "box": "moc3", "stale": False, "iso": "2026-05-31T00:00:00",
                    "rule_id": "wd", "subject": "moc3", "detail": "rnsd"}]},
    ]
    out = build_deep_feed(results, NOW)
    assert "2 boxes reporting" in out and "2 escalations" in out
    # newest escalation (moc3, NOW-5) before older (managerbox, NOW-100)
    assert out.index("[moc3]") < out.index("[managerbox]")
    # section ordering: escalations header before fires header
    assert out.index("Fleet escalations") < out.index("Recent fleet fires")


def test_build_deep_feed_caps_fires_and_notes_overflow():
    fires = [{"ts": NOW - i, "box": "moc", "stale": False, "iso": "2026-05-31T00:00:00",
              "rule_id": f"r{i}", "subject": "s", "detail": "d"} for i in range(30)]
    results = [{"host": "moc", "status": "fresh", "escalations": [], "fires": fires}]
    out = build_deep_feed(results, NOW)
    assert "older fires not shown" in out  # 30 > cap of 20


def test_build_deep_feed_lists_skipped_boxes():
    results = [
        {"host": "moc", "status": "fresh", "escalations": [], "fires": []},
        {"host": "meshanchor-server", "status": "no_mini", "error": "no state",
         "escalations": [], "fires": []},
    ]
    out = build_deep_feed(results, NOW)
    assert "Skipped" in out and "meshanchor-server" in out


def test_build_deep_feed_empty_is_honest():
    results = [{"host": "moc", "status": "fresh", "escalations": [], "fires": []}]
    out = build_deep_feed(results, NOW)
    assert "no box is proposing an escalation" in out
    assert "No edge_up fires" in out


# --- collect_fleet_deep (local + remotes) ---

def test_collect_fleet_deep_local_plus_remotes(tmp_path):
    sp = tmp_path / "mini_dudeai_state.json"
    sp.write_text(json.dumps({"last_tick_ts": NOW, "rule_count": 12, "host": "managerbox"}))
    hp = tmp_path / "mini_dudeai_history.jsonl"
    hp.write_text(json.dumps(_hist_fire(NOW - 3, "r", "s", "d")) + "\n")
    hf = tmp_path / "fleet_hosts"
    hf.write_text("moc\n")
    st = {"last_tick_ts": NOW, "rule_count": 8}
    results = collect_fleet_deep(
        NOW, runner=_deep_runner(0, st, [_hist_escalation(NOW - 1, "r2", "x", "d")]),
        env={"MESHFORGE_FLEET_HOSTS": str(hf)},
        local_state_path=str(sp), local_history_path=str(hp),
    )
    assert [r["host"] for r in results] == ["managerbox", "moc"]
    assert results[0]["self_box"] is True and len(results[0]["fires"]) == 1
    assert len(results[1]["escalations"]) == 1
