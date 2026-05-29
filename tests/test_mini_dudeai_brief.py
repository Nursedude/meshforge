"""Tests for the warm-start brief (build_brief pure renderer + write_brief I/O)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai import build_brief, write_brief

NOW = 1_780_000_000.0


def _state(last_tick=NOW, rules=None, **kw):
    s = {"last_tick_ts": last_tick, "host": "TestBox", "rule_count": len(rules or {}),
         "error_count": 0, "rules": rules or {}}
    s.update(kw)
    return s


def test_alive_quiet_brief():
    out = build_brief(_state(), [], NOW)
    assert "mini-dudeai warm brief — TestBox" in out
    assert "🟢 alive" in out
    assert "Quiet" in out


def test_stale_brief_flags_down():
    out = build_brief(_state(last_tick=NOW - 99999), [], NOW)
    assert "🔴 **STALE**" in out
    assert "meshforge-mini-dudeai" in out


def test_no_state_yet():
    out = build_brief({}, [], NOW)
    assert "no state yet" in out


def test_active_rules_listed():
    rules = {"r::moc3": {"rule_id": "r", "subject": "moc3",
                          "currently_active": True, "last_detail": "backoff mult=60"}}
    out = build_brief(_state(rules=rules), [], NOW)
    assert "Still active now" in out
    assert "moc3" in out and "backoff mult=60" in out


def test_escalations_surface_look_here_first():
    hist = [{
        "transition": "edge_up", "iso": "2026-05-28T19:00:00", "rule_id": "boom",
        "subject": "moc1", "detail": "d",
        "outcome": {"action": "propose_escalation", "ok": True,
                    "extras": {"escalation": {"rule": "boom", "subject": "moc1",
                                              "detail": "unexpected peer down", "note": "chase this"}}},
    }]
    out = build_brief(_state(), hist, NOW)
    assert "Look here first" in out
    assert "unexpected peer down" in out
    assert "chase this" in out


def test_recent_fires_counted():
    # build two edge_up entries dated "today" relative to NOW
    import datetime
    today = datetime.datetime.fromtimestamp(NOW).date().isoformat()
    hist = [{"transition": "edge_up", "iso": f"{today}T01:00:00", "rule_id": "a", "subject": "s1", "detail": ""},
            {"transition": "edge_up", "iso": f"{today}T02:00:00", "rule_id": "b", "subject": "s2", "detail": ""}]
    out = build_brief(_state(), hist, NOW)
    assert "Recent fires" in out
    assert "2 edge_up today" in out


def test_write_brief_round_trip(tmp_path):
    state_p = tmp_path / "state.json"
    state_p.write_text(json.dumps(_state()))
    hist_p = tmp_path / "history.jsonl"
    hist_p.write_text('{"transition":"edge_up","iso":"2026-01-01T00:00:00","rule_id":"x","subject":"y","detail":"d"}\n')
    out_p = tmp_path / "brief.md"
    text = write_brief(str(state_p), str(hist_p), str(out_p), now_ts=NOW)
    assert out_p.exists()
    assert out_p.read_text() == text
    assert "warm brief" in text


def test_write_brief_tolerates_missing_files(tmp_path):
    out_p = tmp_path / "brief.md"
    # neither state nor history exist — must still write a coherent brief
    text = write_brief(str(tmp_path / "nope.json"), str(tmp_path / "nope.jsonl"),
                       str(out_p), now_ts=NOW)
    assert out_p.exists()
    assert "no state yet" in text
