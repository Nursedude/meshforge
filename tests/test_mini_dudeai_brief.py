"""Tests for the warm-start brief (build_brief pure renderer + write_brief I/O)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai import build_brief, recent_escalations, write_brief

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


def _esc_row(ts, rule, subject, detail, note="", legacy=False):
    esc = {"rule": rule, "subject": subject, "detail": detail, "note": note}
    outcome = {"action": "propose_escalation", "ok": True}
    if legacy:
        outcome["escalation"] = esc            # older top-level schema
    else:
        outcome["extras"] = {"escalation": esc}  # current schema
    # escalation rows are typically edge_down in practice; that keeps them out
    # of the "Recent fires" (edge_up only) section so we assert on "Look here
    # first" in isolation.
    return {"ts": ts, "transition": "edge_down", "rule_id": rule,
            "subject": subject, "detail": detail, "outcome": outcome}


def test_stale_escalation_filtered_by_window():
    # An escalation older than the 24h window must NOT surface (the bug: stale
    # "FORCED stuck-active" test entries replayed from the history tail).
    stale = _esc_row(NOW - 90_000, "federation_peer_unhealthy_unexpected",
                     "moc3", "FORCED stuck-active for daemon self-correction test")
    out = build_brief(_state(), [stale], NOW)
    assert "Look here first" not in out
    assert "FORCED stuck-active" not in out


def test_fresh_escalation_within_window_surfaces():
    fresh = _esc_row(NOW - 3600, "boom", "moc1", "unexpected peer down", note="chase")
    out = build_brief(_state(), [fresh], NOW)
    assert "Look here first" in out
    assert "unexpected peer down" in out


def test_escalations_deduped_keeping_latest():
    # Same (rule, subject, detail) fired three times — show once, with the
    # latest fire (so the brief isn't 3x the same moc3 line).
    rows = [_esc_row(NOW - 5000, "r", "moc3", "same detail"),
            _esc_row(NOW - 4000, "r", "moc3", "same detail"),
            _esc_row(NOW - 3000, "r", "moc3", "same detail")]
    out = build_brief(_state(), rows, NOW)
    assert out.count("r · moc3 · same detail") == 1


def test_legacy_top_level_escalation_schema_read():
    # outcome.escalation (no .extras) must not be silently dropped.
    legacy = _esc_row(NOW - 1000, "boom", "moc1", "legacy schema esc", legacy=True)
    out = build_brief(_state(), [legacy], NOW)
    assert "Look here first" in out
    assert "legacy schema esc" in out


def test_recent_escalations_shared_helper():
    # The helper both the brief and the digest call — windowed, deduped,
    # oldest→newest, schema-tolerant. Pins the single source of truth.
    rows = [
        _esc_row(NOW - 90_000, "r", "moc3", "stale"),          # outside window
        _esc_row(NOW - 5000, "r", "moc3", "dup"),              # \ dedup to one,
        _esc_row(NOW - 3000, "r", "moc3", "dup"),              # / keep latest
        _esc_row(NOW - 1000, "boom", "moc1", "legacy", legacy=True),
    ]
    out = recent_escalations(rows, NOW)
    details = [e.get("detail") for e in out]
    assert "stale" not in details            # window dropped it
    assert details.count("dup") == 1         # deduped
    assert "legacy" in details               # legacy schema read
    assert details[-1] == "legacy"           # newest last


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


# --- currently_active gating (2026-06-04) -----------------------------------
# Incident: the 2026-06-03 parity_drift port window self-healed (edge_down)
# in 10 minutes, but its escalation sat under "Look here first" for ~22h and
# read as a live 22-tick escalation. The headline section must be gated on
# the rule's currently_active state; cleared conditions move to "Recently
# resolved". Demotion requires POSITIVE evidence (pair present, active=False)
# — unknown pairs are never hidden.

def _brief_section(out: str, header: str) -> str:
    """Return the body of one '## header' section ('' if absent)."""
    parts = out.split("\n## ")
    for p in parts[1:]:
        if p.startswith(header):
            return p
    return ""


def test_resolved_escalation_moves_out_of_look_here_first():
    # The real incident shape: parity_drift_any escalated, then cleared.
    rules = {"parity_drift_any::mf<->ma": {
        "rule_id": "parity_drift_any", "subject": "mf<->ma",
        "currently_active": False, "last_detail": "drift: canonical_message.py"}}
    hist = [_esc_row(NOW - 600, "parity_drift_any", "mf<->ma",
                     "drift confirmed over 22 consecutive ticks")]
    out = build_brief(_state(rules=rules), hist, NOW)
    look = _brief_section(out, "🔎 Look here first")
    resolved = _brief_section(out, "✅ Recently resolved")
    assert "parity_drift_any" not in look
    assert "parity_drift_any" in resolved
    assert "22 consecutive ticks" in resolved


def test_active_escalation_stays_in_look_here_first():
    rules = {"boom::moc1": {"rule_id": "boom", "subject": "moc1",
                            "currently_active": True, "last_detail": "down"}}
    hist = [_esc_row(NOW - 600, "boom", "moc1", "peer down")]
    out = build_brief(_state(rules=rules), hist, NOW)
    look = _brief_section(out, "🔎 Look here first")
    assert "peer down" in look
    assert "Recently resolved" not in out


def test_unknown_rule_pair_is_never_demoted():
    # No matching (rule, subject) in state (renamed rule / pruned state /
    # schema drift) — conservative default keeps it in the headline section.
    hist = [_esc_row(NOW - 600, "renamed_rule", "subj", "still chase me")]
    out = build_brief(_state(rules={}), hist, NOW)
    assert "still chase me" in _brief_section(out, "🔎 Look here first")
    assert "Recently resolved" not in out


def test_mixed_active_and_resolved_partition():
    rules = {
        "live::a": {"rule_id": "live", "subject": "a", "currently_active": True},
        "done::b": {"rule_id": "done", "subject": "b", "currently_active": False},
    }
    hist = [_esc_row(NOW - 700, "live", "a", "live detail"),
            _esc_row(NOW - 600, "done", "b", "done detail")]
    out = build_brief(_state(rules=rules), hist, NOW)
    look = _brief_section(out, "🔎 Look here first")
    resolved = _brief_section(out, "✅ Recently resolved")
    assert "live detail" in look and "done detail" not in look
    assert "done detail" in resolved and "live detail" not in resolved


def test_resolved_only_brief_is_not_quiet():
    # A brief with only resolved escalations still shows them (not "Quiet").
    rules = {"done::b": {"rule_id": "done", "subject": "b",
                         "currently_active": False}}
    hist = [_esc_row(NOW - 600, "done", "b", "cleared earlier")]
    out = build_brief(_state(rules=rules), hist, NOW)
    assert "cleared earlier" in _brief_section(out, "✅ Recently resolved")
    assert "Quiet" not in out
