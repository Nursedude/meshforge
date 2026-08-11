"""Tests for the fleet mini-dudeai posture rollup (rollup.py).

Pure functions (resolve/parse/build) tested directly; ssh collection tested with
an injected runner so no network is touched.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai.rollup import (  # noqa: E402
    _CLAW_SENTINEL,
    build_rollup,
    collect_fleet,
    collect_local,
    collect_remote,
    parse_claw_posture,
    parse_state_posture,
    resolve_fleet_hosts,
)

NOW = 1_780_000_000.0

_CLAW_DOC = {
    "captured_at": NOW - 10, "ok": True, "device": "dudeclaw-01",
    "device_info": {"uptime_s": 109368, "heap_free_bytes": 17764,
                    "wifi_rssi_dbm": -37},
    "ble": {"adv_age_s": 0, "advs": 767422, "last_rssi_dbm": -59},
}


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


def test_parse_carries_pending_deltas():
    st = {"last_tick_ts": NOW, "pending_deltas": 3}
    assert parse_state_posture("moc", st, NOW)["pending_deltas"] == 3


def test_parse_pending_deltas_absent_is_none_not_zero():
    """A pre-upgrade daemon's state has no pending_deltas key — that is
    UNKNOWN, never 0 (honest_failure_modes #1: absence must not overlap the
    healthy domain)."""
    p = parse_state_posture("moc", {"last_tick_ts": NOW}, NOW)
    assert p["pending_deltas"] is None


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


def test_build_rollup_renders_pending_deltas():
    postures = [{
        "host": "moc", "status": "fresh", "age": "1s", "rule_count": 8,
        "src_errors": 0, "self_box": False, "active": [],
        "pending_deltas": 35,
    }]
    out = build_rollup(postures, NOW)
    assert "35 delta(s) pending" in out


def test_build_rollup_omits_pending_deltas_when_zero_or_unknown():
    postures = [
        {"host": "moc", "status": "fresh", "age": "1s", "rule_count": 8,
         "src_errors": 0, "self_box": False, "active": [], "pending_deltas": 0},
        {"host": "moc2", "status": "fresh", "age": "1s", "rule_count": 8,
         "src_errors": 0, "self_box": False, "active": []},  # pre-upgrade daemon
    ]
    out = build_rollup(postures, NOW)
    assert "pending" not in out


# === claw card (batch-2-5 harness #2) ============================

def test_parse_claw_posture_fresh():
    c = parse_claw_posture(_CLAW_DOC, NOW)
    assert c["status"] == "fresh" and c["device"] == "dudeclaw-01"
    assert c["uptime_s"] == 109368 and c["heap_free_bytes"] == 17764
    assert c["wifi_rssi_dbm"] == -37 and c["ble_adv_age_s"] == 0


def test_parse_claw_posture_stale_when_capture_old():
    c = parse_claw_posture({**_CLAW_DOC, "captured_at": NOW - 9999}, NOW)
    assert c["status"] == "stale"


def test_parse_claw_posture_unreachable_when_tick_not_ok():
    # fresh capture but the claw didn't answer -> unreachable, not fresh
    c = parse_claw_posture({**_CLAW_DOC, "ok": False, "device_info": None}, NOW)
    assert c["status"] == "unreachable"


def test_parse_claw_posture_none_when_absent():
    assert parse_claw_posture(None, NOW) is None
    assert parse_claw_posture({}, NOW) is None


def test_parse_state_posture_carries_claw():
    p = parse_state_posture("moc2", {"last_tick_ts": NOW - 5}, NOW, claw=_CLAW_DOC)
    assert p["claw"]["status"] == "fresh"


def test_parse_state_posture_claw_none_by_default():
    p = parse_state_posture("moc2", {"last_tick_ts": NOW - 5}, NOW)
    assert p["claw"] is None


def test_build_rollup_renders_claw_card():
    postures = [parse_state_posture("moc2", {"last_tick_ts": NOW - 5,
                "rule_count": 9}, NOW, claw=_CLAW_DOC)]
    out = build_rollup(postures, NOW)
    assert "🦞" in out and "dudeclaw-01" in out
    assert "up " in out and "heap" in out  # numbers shown when fresh


def test_build_rollup_claw_stale_renders_stale():
    postures = [parse_state_posture("moc2", {"last_tick_ts": NOW - 5}, NOW,
                claw={**_CLAW_DOC, "captured_at": NOW - 9999})]
    out = build_rollup(postures, NOW)
    assert "🦞" in out and "STALE" in out and "capture cron" in out


def test_collect_remote_carries_claw_after_sentinel():
    payload = (json.dumps({"last_tick_ts": NOW - 5, "rule_count": 9, "host": "moc2"})
               + f"\n{_CLAW_SENTINEL}\n" + json.dumps(_CLAW_DOC))
    p = collect_remote("moc2", NOW, runner=_runner(0, payload))
    assert p["status"] == "fresh" and p["claw"]["status"] == "fresh"


def test_collect_remote_no_sentinel_means_no_claw():
    # backward compatible: a runner that returns state only -> no claw line
    st = json.dumps({"last_tick_ts": NOW - 5, "rule_count": 9, "host": "moc2"})
    p = collect_remote("moc2", NOW, runner=_runner(0, st))
    assert p["status"] == "fresh" and p["claw"] is None


# === multi-claw: EVERY dude-claw on a brain box (07-24 audit) ====
# The pane read only claw_last_tick.json, so a box hosting dudeclaw-02 showed
# one card and a DEAD second claw was invisible — a degraded device rendered as
# an absent one (honest_failure_modes #2).

_CLAW_DOC_2 = {
    "captured_at": NOW - 10, "ok": True, "device": "dudeclaw-02",
    "device_info": {"uptime_s": 3600, "heap_free_bytes": 20000},
}


def test_collect_remote_carries_every_claw_after_its_sentinel():
    payload = (json.dumps({"last_tick_ts": NOW - 5, "rule_count": 9, "host": "moc2"})
               + f"\n{_CLAW_SENTINEL}\n" + json.dumps(_CLAW_DOC)
               + f"\n{_CLAW_SENTINEL}\n"
               + json.dumps({**_CLAW_DOC_2, "ok": False, "device_info": None})
               + f"\n{_CLAW_SENTINEL}\n")
    p = collect_remote("moc2", NOW, runner=_runner(0, payload))
    assert [c["device"] for c in p["claws"]] == ["dudeclaw-01", "dudeclaw-02"]
    assert p["claws"][1]["status"] == "unreachable"   # the dead one is VISIBLE
    assert p["claw"]["device"] == "dudeclaw-01"       # primary stays back-compat


def test_torn_second_tick_does_not_hide_the_other_claws():
    payload = (json.dumps({"last_tick_ts": NOW - 5, "rule_count": 9, "host": "moc2"})
               + f"\n{_CLAW_SENTINEL}\n" + json.dumps(_CLAW_DOC)
               + f"\n{_CLAW_SENTINEL}\n" + '{"captured_at": 17800000'  # torn
               + f"\n{_CLAW_SENTINEL}\n")
    p = collect_remote("moc2", NOW, runner=_runner(0, payload))
    assert [c["device"] for c in p["claws"]] == ["dudeclaw-01"]


def test_no_mini_box_still_reports_its_claw():
    # build_rollup has always rendered a claw card on the no-mini branch; the
    # collector never filled one in (a reader with no writer).
    payload = f"\n{_CLAW_SENTINEL}\n" + json.dumps(_CLAW_DOC)
    p = collect_remote("moc2", NOW, runner=_runner(0, payload))
    assert p["status"] == "no_mini"
    assert p["claws"] and p["claws"][0]["device"] == "dudeclaw-01"
    assert "dudeclaw-01" in build_rollup([p], NOW)


def test_collect_local_reads_secondary_tick_siblings(tmp_path):
    sp = tmp_path / "mini_dudeai_state.json"
    sp.write_text(json.dumps({"last_tick_ts": NOW, "rule_count": 3, "host": "moc2"}))
    (tmp_path / "claw_last_tick.json").write_text(json.dumps(_CLAW_DOC))
    (tmp_path / "claw_last_tick.dudeclaw-02.json").write_text(
        json.dumps({**_CLAW_DOC_2, "captured_at": NOW - 9999}))
    p = collect_local(NOW, state_path=str(sp))
    assert [c["device"] for c in p["claws"]] == ["dudeclaw-01", "dudeclaw-02"]
    assert p["claws"][1]["status"] == "stale"


def test_build_rollup_renders_a_card_per_claw():
    p = parse_state_posture("moc2", {"last_tick_ts": NOW - 5, "rule_count": 9},
                            NOW, claws=[_CLAW_DOC, {**_CLAW_DOC_2, "ok": False,
                                                    "device_info": None}])
    out = build_rollup([p], NOW)
    assert "dudeclaw-01" in out and "dudeclaw-02" in out
    assert "UNREACHABLE" in out


def _run_breadth_cmd(cwd):
    """Execute the REAL remote one-liner in a real shell (the consumer of
    record for that string) and feed its stdout back through the parser."""
    import subprocess

    from mini_dudeai.rollup import _remote_breadth_cmd, _split_claw_payload
    p = subprocess.run(["sh", "-c", _remote_breadth_cmd()], cwd=str(cwd),
                       capture_output=True, text=True, timeout=30)
    return p.returncode, _split_claw_payload(p.stdout)


def test_remote_breadth_cmd_really_cats_every_claw_tick(tmp_path):
    # A renderer that can show two claws over a command that only cats one is
    # half-wired — so run the command, don't assert on its text.
    (tmp_path / "mini_dudeai_state.json").write_text(
        json.dumps({"last_tick_ts": NOW, "rule_count": 4}))
    (tmp_path / "claw_last_tick.json").write_text(json.dumps(_CLAW_DOC))
    (tmp_path / "claw_last_tick.dudeclaw-02.json").write_text(json.dumps(_CLAW_DOC_2))
    rc, (state_text, claws) = _run_breadth_cmd(tmp_path)
    assert rc == 0
    assert json.loads(state_text)["rule_count"] == 4
    assert [c["device"] for c in claws] == ["dudeclaw-01", "dudeclaw-02"]


def test_remote_breadth_cmd_on_a_claw_less_box_is_clean(tmp_path):
    # The common case: unmatched glob must stay literal + skipped, rc 0, and
    # the payload must parse to state + NO claws (never a literal filename).
    (tmp_path / "mini_dudeai_state.json").write_text(
        json.dumps({"last_tick_ts": NOW, "rule_count": 4}))
    rc, (state_text, claws) = _run_breadth_cmd(tmp_path)
    assert rc == 0 and claws == []
    assert json.loads(state_text)["rule_count"] == 4


def test_remote_breadth_cmd_on_a_mini_less_box_still_finds_the_claw(tmp_path):
    (tmp_path / "claw_last_tick.json").write_text(json.dumps(_CLAW_DOC))
    rc, (state_text, claws) = _run_breadth_cmd(tmp_path)
    assert rc == 0 and state_text == ""
    assert [c["device"] for c in claws] == ["dudeclaw-01"]


def test_claw_sentinel_is_shell_safe():
    assert not (set(_CLAW_SENTINEL) & set("<>|&;$`()\"' \t*?[]{}#~!"))


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


def test_build_box_deep_marks_resolved_escalation():
    """The deep feed had NO active/resolved split at all, so a cleared condition
    sat under the fleet-wide "look here first" forever — the 2026-06-03
    parity_drift defect the per-box brief was fixed for, in the sibling nobody
    grepped (found 2026-08-11). build_box_deep already receives the box's state;
    it just wasn't asking it."""
    history = [_hist_escalation(NOW - 10, "esc_rule", "moc3", "peer unhealthy")]
    state = {"last_tick_ts": NOW, "rule_count": 8,
             "rules": {"esc_rule::moc3": {"rule_id": "esc_rule",
                                          "subject": "moc3",
                                          "currently_active": False}}}
    rec = build_box_deep("moc3", state, history, NOW)
    assert rec["escalations"][0]["resolved"] is True


def test_build_box_deep_keeps_active_and_unknown_escalations_live():
    """Conservative in the SAME direction as the brief: a still-active pair stays
    live, and an unknown pair (renamed rule / pruned state) is never demoted —
    hiding a live escalation on state drift would be the worse failure."""
    history = [_hist_escalation(NOW - 10, "live_rule", "moc3", "still bad"),
               _hist_escalation(NOW - 11, "ghost_rule", "moc3", "who knows")]
    state = {"last_tick_ts": NOW,
             "rules": {"live_rule::moc3": {"rule_id": "live_rule",
                                           "subject": "moc3",
                                           "currently_active": True}}}
    rec = build_box_deep("moc3", state, history, NOW)
    assert all(e["resolved"] is False for e in rec["escalations"])


def test_deep_feed_moves_resolved_out_of_look_here_first():
    results = [{"host": "moc3", "status": "fresh", "fires": [], "escalations": [
        {"ts": NOW - 5, "box": "moc3", "stale": False, "resolved": False,
         "esc": {"rule": "live", "subject": "moc3", "detail": "rnsd wedged"}},
        {"ts": NOW - 3600, "box": "moc3", "stale": False, "resolved": True,
         "esc": {"rule": "done", "subject": "moc3", "detail": "box is DOWN"}},
    ]}]
    out = build_deep_feed(results, NOW)
    head = out[out.index("Fleet escalations"):out.index("✅ Resolved in window")]
    assert "rnsd wedged" in head
    assert "box is DOWN" not in head, \
        "a cleared condition stayed under the fleet-wide 'look here first'"
    tail = out[out.index("✅ Resolved in window"):]
    assert "was (last seen 60m ago)" in tail and "box is DOWN" in tail


def test_deep_feed_without_resolved_key_treats_escalations_as_live():
    """Back-compat: records built before the split carry no `resolved` key and
    must not silently vanish from the headline section."""
    results = [{"host": "moc", "status": "fresh", "fires": [], "escalations": [
        {"ts": NOW - 5, "box": "moc", "stale": False,
         "esc": {"rule": "r", "subject": "s", "detail": "legacy record"}}]}]
    out = build_deep_feed(results, NOW)
    assert "legacy record" in out[:out.index("Recent fleet fires")]
    assert "✅ Resolved in window" not in out


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
