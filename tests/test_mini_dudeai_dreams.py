"""Tests for B3 — the dreams / synthesis + memory-delta loop.

Covers the four detectors (pure), the narrative renderer, dedup-on-append,
ratification, count_pending_deltas, the brief integration, and the write_dreams
I/O wrapper (round-trip + tolerance of missing files).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai import (
    build_brief,
    build_dreams,
    count_pending_deltas,
    resolve_delta,
    write_brief,
    write_dreams,
)
from mini_dudeai.dreams import (
    detect_chronic_flap,
    detect_escalation_rollup,
    detect_new_subject,
    detect_persistent_active,
)

NOW = 1_780_000_000.0
HOST = "TestBox"


def _rs(rule_id, subject, *, currently_active=False, last_fired_ts=0.0,
        fire_count=0, fire_count_24h=0, fires_window=None, last_detail=""):
    return {
        "rule_id": rule_id, "subject": subject,
        "currently_active": currently_active, "last_fired_ts": last_fired_ts,
        "fire_count": fire_count, "fire_count_24h": fire_count_24h,
        "fires_window": fires_window if fires_window is not None else [],
        "last_detail": last_detail,
    }


def _state(rules):
    return {"host": HOST, "rule_count": len(rules), "rules": rules}


def _flap_history(rule_id, subject, n, *, base=NOW - 80000, gap=600, active=30):
    """n quick up/down pairs: each clears in `active` seconds."""
    hist = []
    for i in range(n):
        up = base + i * gap
        hist.append({"ts": up, "iso": "x", "rule_id": rule_id, "subject": subject,
                     "transition": "edge_up", "detail": "d"})
        hist.append({"ts": up + active, "iso": "x", "rule_id": rule_id,
                     "subject": subject, "transition": "edge_down", "detail": "d"})
    return hist


# --- detect_chronic_flap ------------------------------------------------------

def test_chronic_flap_fires_on_frequent_quick_clears():
    rules = {"r::s": _rs("r", "s", fire_count=5, fire_count_24h=5,
                         fires_window=[NOW - i * 600 for i in range(5)])}
    hist = _flap_history("r", "s", 5, active=30)
    out = detect_chronic_flap(_state(rules), hist, NOW, HOST)
    assert len(out) == 1
    d = out[0]
    assert d["kind"] == "chronic_flap"
    assert d["key"] == "chronic_flap::r::s"
    assert d["evidence"]["fire_count_24h"] == 5
    assert d["evidence"]["mean_active_s"] == 30.0
    assert d["confidence"] == "medium"  # 5 fires < 2*min(4)=8 -> medium


def test_chronic_flap_confidence_tiers():
    rules = {"r::s": _rs("r", "s", fire_count=4, fire_count_24h=4)}
    out = detect_chronic_flap(_state(rules), _flap_history("r", "s", 4), NOW, HOST)
    assert out[0]["confidence"] == "medium"
    rules = {"r::s": _rs("r", "s", fire_count=8, fire_count_24h=8)}
    out = detect_chronic_flap(_state(rules), _flap_history("r", "s", 8), NOW, HOST)
    assert out[0]["confidence"] == "high"


def test_chronic_flap_ignores_low_fire_count():
    rules = {"r::s": _rs("r", "s", fire_count=2, fire_count_24h=2)}
    out = detect_chronic_flap(_state(rules), _flap_history("r", "s", 2), NOW, HOST)
    assert out == []


def test_chronic_flap_ignores_long_active_periods():
    # fires often but stays active ~1h each time -> real condition, not a flap
    rules = {"r::s": _rs("r", "s", fire_count=5, fire_count_24h=5)}
    hist = _flap_history("r", "s", 5, active=3600)
    out = detect_chronic_flap(_state(rules), hist, NOW, HOST)
    assert out == []


def test_chronic_flap_needs_paired_clears():
    # frequent fires but no edge_down in history -> can't confirm it clears
    rules = {"r::s": _rs("r", "s", fire_count=5, fire_count_24h=5)}
    hist = [{"ts": NOW - i * 600, "iso": "x", "rule_id": "r", "subject": "s",
             "transition": "edge_up", "detail": "d"} for i in range(5)]
    out = detect_chronic_flap(_state(rules), hist, NOW, HOST)
    assert out == []


# --- detect_new_subject -------------------------------------------------------

def test_new_subject_when_all_fires_in_window():
    rules = {"r::newbox": _rs("r", "newbox", fire_count=2, fire_count_24h=2,
                              fires_window=[NOW - 1000, NOW - 500])}
    out = detect_new_subject(_state(rules), [], NOW, HOST)
    assert len(out) == 1
    assert out[0]["kind"] == "new_subject"
    assert "newbox" in out[0]["summary"]


def test_new_subject_skips_when_prior_history_exists():
    # lifetime fires exceed 24h fires -> seen before the window -> not new
    rules = {"r::oldbox": _rs("r", "oldbox", fire_count=10, fire_count_24h=2,
                              fires_window=[NOW - 1000, NOW - 500])}
    out = detect_new_subject(_state(rules), [], NOW, HOST)
    assert out == []


def test_new_subject_skips_when_first_seen_before_window():
    rules = {"r::s": _rs("r", "s", fire_count=1, fire_count_24h=1,
                         fires_window=[NOW - 999999])}
    out = detect_new_subject(_state(rules), [], NOW, HOST)
    assert out == []


# --- detect_persistent_active -------------------------------------------------

def test_persistent_active_fires_after_hours():
    rules = {"r::moc3": _rs("r", "moc3", currently_active=True,
                            last_fired_ts=NOW - 7200, last_detail="backoff mult=60")}
    out = detect_persistent_active(_state(rules), [], NOW, HOST)
    assert len(out) == 1
    assert out[0]["kind"] == "persistent_active"
    assert out[0]["evidence"]["active_for_s"] == 7200
    assert "moc3" in out[0]["summary"]


def test_persistent_active_skips_recent_and_inactive():
    rules = {
        "r::a": _rs("r", "a", currently_active=True, last_fired_ts=NOW - 60),  # too recent
        "r::b": _rs("r", "b", currently_active=False, last_fired_ts=NOW - 999999),  # not active
    }
    out = detect_persistent_active(_state(rules), [], NOW, HOST)
    assert out == []


# --- detect_escalation_rollup -------------------------------------------------

def test_escalation_rollup_groups_by_rule_subject():
    esc = {"rule": "boom", "subject": "moc1", "detail": "peer down", "note": "chase"}
    hist = [{"ts": NOW - i * 100, "iso": f"i{i}", "rule_id": "boom", "subject": "moc1",
             "transition": "edge_up", "outcome": {"extras": {"escalation": esc}}}
            for i in range(3)]
    out = detect_escalation_rollup(_state({}), hist, NOW, HOST)
    assert len(out) == 1
    assert out[0]["evidence"]["count"] == 3
    assert out[0]["confidence"] == "high"


def test_escalation_rollup_ignores_outside_window():
    esc = {"rule": "boom", "subject": "moc1", "detail": "d", "note": ""}
    hist = [{"ts": NOW - 999999, "iso": "old", "rule_id": "boom", "subject": "moc1",
             "transition": "edge_up", "outcome": {"extras": {"escalation": esc}}}]
    out = detect_escalation_rollup(_state({}), hist, NOW, HOST)
    assert out == []


# --- build_dreams (composition + narrative) -----------------------------------

def test_build_dreams_quiet_when_nothing_crosses_threshold():
    narrative, deltas = build_dreams(_state({}), [], NOW)
    assert deltas == []
    assert "nothing to propose" in narrative.lower()
    assert "dream log — TestBox" in narrative


def test_build_dreams_first_person_scientific_voice():
    narrative, _ = build_dreams(_state({}), [], NOW)
    assert "I reviewed" in narrative
    assert "deterministic distillation" in narrative


def test_build_dreams_aggregates_multiple_detectors():
    rules = {
        "flap::s": _rs("flap", "s", fire_count=5, fire_count_24h=5),
        "r::moc3": _rs("r", "moc3", currently_active=True, last_fired_ts=NOW - 7200),
        "r::newbox": _rs("r", "newbox", fire_count=1, fire_count_24h=1,
                         fires_window=[NOW - 500]),
    }
    hist = _flap_history("flap", "s", 5, active=20)
    narrative, deltas = build_dreams(_state(rules), hist, NOW)
    kinds = {d["kind"] for d in deltas}
    assert {"chronic_flap", "persistent_active", "new_subject"} <= kinds
    assert "Proposed memory-deltas" in narrative


def test_build_dreams_detector_error_is_contained(monkeypatch):
    import mini_dudeai.dreams as dr

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(dr, "_DETECTORS", (boom,))
    narrative, deltas = build_dreams(_state({}), [], NOW)
    assert any(d["kind"] == "detector_error" for d in deltas)
    assert "kaboom" in deltas[0]["summary"]


# --- write_dreams + dedup + ratification --------------------------------------

def _persistent_state_file(tmp_path):
    rules = {"r::moc3": _rs("r", "moc3", currently_active=True,
                            last_fired_ts=NOW - 7200, last_detail="backoff")}
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(_state(rules)))
    hp = tmp_path / "history.jsonl"
    hp.write_text("")
    return sp, hp


def test_write_dreams_round_trip(tmp_path):
    sp, hp = _persistent_state_file(tmp_path)
    dp = tmp_path / "deltas.jsonl"
    np_ = tmp_path / "dreams.md"
    summary = write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW)
    assert summary["detected"] == 1
    assert summary["appended"] == 1
    assert np_.exists() and "dream log" in np_.read_text()
    lines = [json.loads(l) for l in dp.read_text().splitlines() if l.strip()]
    assert lines[0]["key"] == "persistent_active::r::moc3"
    assert lines[0]["status"] == "proposed"


def test_write_dreams_dedups_unresolved(tmp_path):
    sp, hp = _persistent_state_file(tmp_path)
    dp = tmp_path / "deltas.jsonl"
    np_ = tmp_path / "dreams.md"
    write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW)
    s2 = write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW + 86400)
    assert s2["detected"] == 1
    assert s2["appended"] == 0  # same key still proposed -> not re-appended
    assert s2["deduped"] == 1
    lines = [l for l in dp.read_text().splitlines() if l.strip()]
    assert len(lines) == 1


def test_resolve_delta_then_redetect_reproposes(tmp_path):
    sp, hp = _persistent_state_file(tmp_path)
    dp = tmp_path / "deltas.jsonl"
    np_ = tmp_path / "dreams.md"
    write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW)
    assert count_pending_deltas(str(dp)) == 1
    ok = resolve_delta(str(dp), "persistent_active::r::moc3", "ratified",
                       note="known-normal gateway box", now_ts=NOW + 10)
    assert ok is True
    assert count_pending_deltas(str(dp)) == 0
    # past the resolve-suppress window, a still-active condition re-proposes
    s2 = write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW + 8 * 86400)
    assert s2["appended"] == 1
    assert count_pending_deltas(str(dp)) == 1


def test_resolved_delta_not_reproposed_within_window(tmp_path):
    """After ratify/reject, the same key is NOT re-proposed by the next pass while
    the signal still detects — prevents the nightly timer resurrecting handled work."""
    sp, hp = _persistent_state_file(tmp_path)
    dp = tmp_path / "deltas.jsonl"
    np_ = tmp_path / "dreams.md"
    write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW)
    assert resolve_delta(str(dp), "persistent_active::r::moc3", "ratified", now_ts=NOW + 10)
    # next pass shortly after: signal still present (moc3 still active) but resolved recently
    s2 = write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW + 3600)
    assert s2["appended"] == 0
    assert s2["deduped"] == 1
    assert count_pending_deltas(str(dp)) == 0


def test_resolved_delta_resurfaces_after_window(tmp_path):
    """Past the suppress window, a still-detecting condition re-surfaces (it's
    genuinely persistent, so worth another look)."""
    sp, hp = _persistent_state_file(tmp_path)
    dp = tmp_path / "deltas.jsonl"
    np_ = tmp_path / "dreams.md"
    write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW)
    resolve_delta(str(dp), "persistent_active::r::moc3", "rejected", now_ts=NOW + 10)
    # 8 days later (> 7d default suppress window) — re-propose
    s2 = write_dreams(str(sp), str(hp), str(dp), str(np_), now_ts=NOW + 8 * 86400)
    assert s2["appended"] == 1
    assert count_pending_deltas(str(dp)) == 1


def test_resolve_delta_rejects_bad_status(tmp_path):
    dp = tmp_path / "deltas.jsonl"
    dp.write_text(json.dumps({"key": "k", "status": "proposed"}) + "\n")
    try:
        resolve_delta(str(dp), "k", "maybe")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_resolve_delta_returns_false_when_no_match(tmp_path):
    dp = tmp_path / "deltas.jsonl"
    dp.write_text(json.dumps({"key": "k", "status": "proposed"}) + "\n")
    assert resolve_delta(str(dp), "nonexistent", "ratified") is False


def test_count_pending_deltas_ignores_resolved(tmp_path):
    dp = tmp_path / "deltas.jsonl"
    dp.write_text(
        json.dumps({"key": "a", "status": "proposed"}) + "\n"
        + json.dumps({"key": "b", "status": "ratified"}) + "\n"
        + json.dumps({"key": "c", "status": "rejected"}) + "\n")
    assert count_pending_deltas(str(dp)) == 1


def test_write_dreams_tolerates_missing_files(tmp_path):
    dp = tmp_path / "deltas.jsonl"
    np_ = tmp_path / "dreams.md"
    summary = write_dreams(str(tmp_path / "nope.json"), str(tmp_path / "nope.jsonl"),
                           str(dp), str(np_), now_ts=NOW)
    assert summary["detected"] == 0
    assert np_.exists()


# --- brief integration --------------------------------------------------------

def test_brief_surfaces_pending_deltas():
    out = build_brief({"last_tick_ts": NOW, "host": HOST, "rules": {}}, [], NOW,
                      pending_deltas=3)
    assert "3 memory-delta(s) await ratification" in out
    assert "mini_dudeai_dreams.md" in out
    assert "Quiet" not in out  # pending deltas means not quiet


def test_write_brief_reads_pending_deltas(tmp_path):
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps({"last_tick_ts": NOW, "host": HOST, "rules": {}}))
    hp = tmp_path / "history.jsonl"
    hp.write_text("")
    dp = tmp_path / "mini_dudeai_memory_deltas.jsonl"
    dp.write_text(json.dumps({"key": "persistent_active::r::moc3",
                              "status": "proposed"}) + "\n")
    text = write_brief(str(sp), str(hp), str(tmp_path / "brief.md"), now_ts=NOW,
                       deltas_path=str(dp))
    assert "1 memory-delta(s) await ratification" in text


# ---------------------------------------------------------------------------
# CLI (mini_dudeai.dreams.main) — the cloud-session resolve path as a clean
# command, so a headless cadence session never needs `python3 -c`.
# ---------------------------------------------------------------------------

from mini_dudeai.dreams import main as dreams_main


def _deltas_file(tmp_path, *rows):
    dp = tmp_path / "deltas.jsonl"
    dp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return dp


def test_cli_list_proposed(tmp_path, capsys):
    dp = _deltas_file(
        tmp_path,
        {"key": "k.alpha", "status": "proposed"},
        {"key": "k.beta", "status": "proposed"},
        {"key": "k.done", "status": "ratified"},
    )
    rc = dreams_main(["--list-proposed", "--path", str(dp)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines() == ["k.alpha", "k.beta"]  # sorted, resolved excluded


def test_cli_list_proposed_empty(tmp_path, capsys):
    dp = _deltas_file(tmp_path, {"key": "k.done", "status": "rejected"})
    rc = dreams_main(["--list-proposed", "--path", str(dp)])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_cli_resolve_ratify(tmp_path, capsys):
    dp = _deltas_file(tmp_path, {"key": "k.alpha", "status": "proposed"})
    rc = dreams_main(["--resolve", "k.alpha", "--status", "ratified",
                      "--path", str(dp), "--note", "verified live"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "resolved: k.alpha -> ratified" in out
    # File reflects the resolution.
    rows = [json.loads(l) for l in dp.read_text().splitlines() if l.strip()]
    assert rows[0]["status"] == "ratified"
    assert rows[0]["resolved_note"] == "verified live"
    assert count_pending_deltas(str(dp)) == 0


def test_cli_resolve_reject(tmp_path, capsys):
    dp = _deltas_file(tmp_path, {"key": "k.alpha", "status": "proposed"})
    rc = dreams_main(["--resolve", "k.alpha", "--status", "rejected", "--path", str(dp)])
    assert rc == 0
    rows = [json.loads(l) for l in dp.read_text().splitlines() if l.strip()]
    assert rows[0]["status"] == "rejected"


def test_cli_resolve_unknown_key_is_not_found(tmp_path, capsys):
    dp = _deltas_file(tmp_path, {"key": "k.alpha", "status": "proposed"})
    rc = dreams_main(["--resolve", "k.nope", "--status", "ratified", "--path", str(dp)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "not found" in err
    # The real proposed delta is untouched.
    assert count_pending_deltas(str(dp)) == 1


def test_cli_resolve_requires_status(tmp_path, capsys):
    dp = _deltas_file(tmp_path, {"key": "k.alpha", "status": "proposed"})
    rc = dreams_main(["--resolve", "k.alpha", "--path", str(dp)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--status is required" in err


def test_cli_resolve_bad_status_rejected_by_argparse(tmp_path):
    dp = _deltas_file(tmp_path, {"key": "k.alpha", "status": "proposed"})
    # argparse choices= enforces valid status → SystemExit(2)
    try:
        dreams_main(["--resolve", "k.alpha", "--status", "maybe", "--path", str(dp)])
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 2


def test_cli_requires_an_action(tmp_path):
    # Neither --resolve nor --list-proposed → mutually-exclusive group required.
    try:
        dreams_main(["--path", str(tmp_path / "d.jsonl")])
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 2
