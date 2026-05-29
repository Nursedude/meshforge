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
