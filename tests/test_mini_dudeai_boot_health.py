"""Tests for BootHealthSource — the on-boot crash-detect source (DRAFT).

Proves the dirty-bit clean/unclean classification works from stdlib signals
alone, with no subprocess. Time + uptime are injected via a fake /proc/uptime
file and explicit marker/state files, so the cases are deterministic.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai.sources.boot_health import BootHealthSource


def _write_uptime(tmp_path, seconds):
    p = tmp_path / "uptime"
    p.write_text(f"{seconds} {seconds}\n")
    return str(p)


def _write_state(tmp_path, last_tick_ts):
    p = tmp_path / "mini_dudeai_state.json"
    p.write_text(json.dumps({"rules": {}, "last_tick_ts": last_tick_ts}))
    return str(p)


def _make_source(tmp_path, uptime_s, last_tick_ts, clean_exit_ts=None,
                 power_line=None, **kw):
    uptime_path = _write_uptime(tmp_path, uptime_s)
    state_path = _write_state(tmp_path, last_tick_ts)
    clean_exit_path = str(tmp_path / "clean_exit")
    if clean_exit_ts is not None:
        (tmp_path / "clean_exit").write_text(str(clean_exit_ts))
    power_log_path = None
    if power_line is not None:
        plp = tmp_path / "power_history.log"
        plp.write_text(power_line + "\n")
        power_log_path = str(plp)
    return BootHealthSource(
        state_path=state_path,
        clean_exit_path=clean_exit_path,
        assessment_path=str(tmp_path / "boot_assessment.json"),
        power_log_path=power_log_path,
        uptime_path=uptime_path,
        **kw,
    )


def test_unclean_reboot_fires(tmp_path):
    now = time.time()
    # Fresh boot 120s ago; mini's last tick was an hour ago; no clean-exit marker.
    src = _make_source(tmp_path, uptime_s=120, last_tick_ts=now - 3600,
                       power_line="2026-05-29T02:34:00Z throttled=0x50000 temp=70'C ext5v=4.81")
    conds = list(src.collect())
    assert len(conds) == 1
    c = conds[0]
    assert c.kind == "unexpected_reboot"
    assert c.extras["verdict"] == "unclean"
    # the last power reading before the cut is surfaced for triage
    assert "0x50000" in c.detail
    assert c.extras["down_gap_s"] > 0


def test_clean_reboot_silent(tmp_path):
    now = time.time()
    last_tick = now - 3600
    # Graceful stop wrote the marker right after the final tick.
    src = _make_source(tmp_path, uptime_s=120, last_tick_ts=last_tick,
                       clean_exit_ts=last_tick + 5)
    assert list(src.collect()) == []


def test_stale_clean_marker_is_unclean(tmp_path):
    now = time.time()
    # Marker exists but from a much older graceful stop; mini kept ticking after
    # → crash leaves last_tick advanced past the stale marker.
    src = _make_source(tmp_path, uptime_s=120, last_tick_ts=now - 3600,
                       clean_exit_ts=now - 200000)
    conds = list(src.collect())
    assert len(conds) == 1
    assert conds[0].extras["verdict"] == "unclean"


def test_steady_state_silent(tmp_path):
    now = time.time()
    # Up for ~14h — well past the fresh-boot window — never assess.
    src = _make_source(tmp_path, uptime_s=50000, last_tick_ts=now - 60)
    assert list(src.collect()) == []


def test_first_ever_run_silent(tmp_path):
    now = time.time()
    # No prior tick recorded → cannot have crashed under our watch.
    src = _make_source(tmp_path, uptime_s=120, last_tick_ts=0)
    assert list(src.collect()) == []


def test_indeterminate_when_mini_ticked_post_boot(tmp_path):
    now = time.time()
    # last_tick is AFTER boot_time → mini already ran this boot; we missed the
    # fresh-boot edge and must not assert a crash.
    src = _make_source(tmp_path, uptime_s=600, last_tick_ts=now - 60)
    assert list(src.collect()) == []


def test_verdict_is_latched_across_ticks(tmp_path):
    now = time.time()
    src = _make_source(tmp_path, uptime_s=120, last_tick_ts=now - 3600)
    first = list(src.collect())
    assert len(first) == 1 and first[0].extras["verdict"] == "unclean"
    # assessment file written and reused on the next tick (stable condition)
    assert os.path.exists(src.assessment_path)
    second = list(src.collect())
    assert len(second) == 1
    assert second[0].extras["assessed_at_ts"] == first[0].extras["assessed_at_ts"]


def test_uptime_unreadable_is_source_error(tmp_path):
    src = BootHealthSource(
        state_path=str(tmp_path / "nope_state.json"),
        clean_exit_path=str(tmp_path / "nope_marker"),
        assessment_path=str(tmp_path / "assess.json"),
        uptime_path=str(tmp_path / "nonexistent_uptime"),
    )
    conds = list(src.collect())
    assert len(conds) == 1
    assert conds[0].kind == "source_error"
    assert conds[0].source == "boot_health"


def test_never_raises_on_garbage(tmp_path):
    # Corrupt state + garbage marker must not crash collect().
    (tmp_path / "state.json").write_text("{not json")
    (tmp_path / "marker").write_text("not-a-float")
    src = BootHealthSource(
        state_path=str(tmp_path / "state.json"),
        clean_exit_path=str(tmp_path / "marker"),
        assessment_path=str(tmp_path / "assess.json"),
        uptime_path=_write_uptime(tmp_path, 120),
    )
    # last_tick unreadable → 0 → first-run → silent, but importantly no exception
    assert list(src.collect()) == []


# === per-boot condition identity (paging defect, 2026-06-11) ============
#
# The engine keys edge state AND cooldowns by (rule_id, subject). With a
# bare-hostname subject, crash #5 (18.5 min after crash #4) produced no new
# edge — the alert rule's cooldown_s=3600 carried across boots, so the second
# crash never paged and never reached the digest. Each crash boot must be a
# distinct condition identity.


def _write_boot_id(tmp_path, value, name="boot_id"):
    p = tmp_path / name
    p.write_text(value + "\n")
    return str(p)


def test_subject_carries_boot_id_prefix(tmp_path):
    now = time.time()
    src = _make_source(
        tmp_path, uptime_s=120, last_tick_ts=now - 3600,
        boot_id_path=_write_boot_id(tmp_path, "deadbeef-aaaa-bbbb-cccc-121212121212"),
    )
    conds = list(src.collect())
    assert len(conds) == 1
    assert conds[0].subject == f"{os.uname().nodename}@deadbeef"


def test_subject_stable_across_ticks_within_one_boot(tmp_path):
    now = time.time()
    src = _make_source(
        tmp_path, uptime_s=120, last_tick_ts=now - 3600,
        boot_id_path=_write_boot_id(tmp_path, "deadbeef-aaaa-bbbb-cccc-121212121212"),
    )
    first = list(src.collect())
    second = list(src.collect())
    assert first[0].subject == second[0].subject  # latched — no flap


def test_consecutive_crash_boots_get_distinct_subjects(tmp_path):
    """Two unclean boots in a row (the 06-11 back-to-back shape) must emit
    two DIFFERENT subjects so the second crash opens a fresh edge."""
    now = time.time()
    boot_id_path = _write_boot_id(tmp_path, "aaaa1111-0000-0000-0000-000000000000")
    src = _make_source(tmp_path, uptime_s=120, last_tick_ts=now - 3600,
                       boot_id_path=boot_id_path)
    first = list(src.collect())
    assert first[0].extras["verdict"] == "unclean"

    # The box crashes AGAIN: new kernel boot_id, marker still absent, mini's
    # last tick still pre-boot. Same assessment path — the prior boot's latch
    # must not bleed into this boot.
    _write_boot_id(tmp_path, "bbbb2222-0000-0000-0000-000000000000")
    second = list(src.collect())
    assert second[0].extras["verdict"] == "unclean"
    assert first[0].subject != second[0].subject
    assert second[0].subject.endswith("@bbbb2222")


def test_subject_falls_back_to_boot_time_when_boot_id_unreadable(tmp_path):
    now = time.time()
    src = _make_source(
        tmp_path, uptime_s=120, last_tick_ts=now - 3600,
        boot_id_path=str(tmp_path / "nonexistent_boot_id"),
    )
    conds = list(src.collect())
    assert len(conds) == 1
    suffix = conds[0].subject.rsplit("@", 1)[1]
    assert suffix == str(conds[0].extras["boot_time_ts"])


# === engine integration: back-to-back crashes both page ================


def test_back_to_back_crash_boots_both_fire(tmp_path, monkeypatch):
    """The 2026-06-11 regression, end to end: crash boot A fires; a second
    crash 20 minutes later — inside the alert rule's cooldown_s=3600 — must
    fire AGAIN, because the per-boot subject opens a fresh state key with no
    cooldown carryover. Under the old bare-hostname subject this exact
    timeline produced zero pages and zero digest records for crash B."""
    import mini_dudeai.engine as eng
    from mini_dudeai import Action, Outcome, RuleEngine

    clock = {"t": 10_000.0}
    monkeypatch.setattr(eng.time, "time", lambda: clock["t"])

    class Recorder(Action):
        name = "recorder"
        def __init__(self):
            self.calls = []
        def execute(self, rule, cond, transition):
            self.calls.append((cond.subject, transition))
            return Outcome(action="recorder", ok=True)

    # Crash boot A: up 120s at t=10000; mini last ticked pre-crash; no marker.
    uptime_path = _write_uptime(tmp_path, 120)
    state_path = _write_state(tmp_path, last_tick_ts=8_000.0)
    boot_id_path = _write_boot_id(tmp_path, "aaaa1111-0000-0000-0000-000000000000")
    src = BootHealthSource(
        state_path=state_path,
        clean_exit_path=str(tmp_path / "clean_exit"),
        assessment_path=str(tmp_path / "assess.json"),
        uptime_path=uptime_path,
        boot_id_path=boot_id_path,
    )
    rec = Recorder()
    engine = RuleEngine(
        sources=[src],
        actions={"recorder": rec},
        rules_path=str(tmp_path / "rules.json"),
        state_path=state_path,          # shared with the source, as in production
        history_path=str(tmp_path / "history.jsonl"),
    )
    (tmp_path / "rules.json").write_text(json.dumps({"rules": [
        {"id": "reboot_alert", "match": {"kind": "unexpected_reboot"},
         "action": {"kind": "recorder"}, "cooldown_s": 3600}]}))

    engine.tick()
    host = os.uname().nodename
    assert rec.calls == [(f"{host}@aaaa1111", "edge_up")]

    # Crash boot B, 20 min later — well inside cooldown_s=3600.
    clock["t"] = 11_200.0
    _write_boot_id(tmp_path, "bbbb2222-0000-0000-0000-000000000000")
    engine.tick()
    ups = [c for c in rec.calls if c[1] == "edge_up"]
    assert ups == [(f"{host}@aaaa1111", "edge_up"),
                   (f"{host}@bbbb2222", "edge_up")]
    # boot A's condition is gone this tick — its edge closes honestly
    assert (f"{host}@aaaa1111", "edge_down") in rec.calls
