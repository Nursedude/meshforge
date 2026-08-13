"""Tests for the mini-dudeai warm-start emitter (honest freshness banner).

The contract under test: warmstart re-derives freshness from state.json's
last_tick_ts at READ time, so a brief frozen by a dead daemon is flagged STALE
rather than presented as current. Uses tmp_path exclusively — never the live
store or the operator's ~/mini_dudeai_* files.

Every render_warmstart call pins ledger_path to a nonexistent tmp file: with
it unset, the function reads the operator's REAL ~/deferred_work.json (plus
any stray DEFERRED_WORK_LEDGER env), and the two `out == ""` assertions then
pass or fail by luck of the live ledger's contents on whatever box runs the
suite — the tests-must-pin-ambient-state class (2026-08-12 re-review).
"""

from __future__ import annotations

import json

import pytest

from mini_dudeai.warmstart import (
    DEFAULT_STALE_S,
    render_warmstart,
)

NOW = 1_700_000_000.0
BRIEF_TEXT = "# mini-dudeai warm brief — testbox\n🟢 alive — 3 rules.\n"


def _write_state(path, last_tick_ts):
    """Write a minimal state.json carrying just last_tick_ts."""
    data = {"host": "testbox", "rules": {}}
    if last_tick_ts is not None:
        data["last_tick_ts"] = last_tick_ts
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fresh — recent tick
# ---------------------------------------------------------------------------


def test_fresh_when_tick_is_recent(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    state = tmp_path / "state.json"
    _write_state(state, NOW - 40)  # 40s ago — well within the stale window

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))

    assert "FRESH" in out
    assert "🟢" in out
    assert "STALE" not in out
    # The brief body is included verbatim after the banner.
    assert BRIEF_TEXT in out


# ---------------------------------------------------------------------------
# Stale — old tick (the load-bearing honesty case)
# ---------------------------------------------------------------------------


def test_stale_when_tick_is_old(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    state = tmp_path / "state.json"
    _write_state(state, NOW - 6 * 3600)  # 6h ago — daemon likely dead

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))

    assert "STALE" in out
    assert "🔴" in out
    assert "FROZEN" in out  # explicitly warns the brief may misreport health
    # Even stale, the brief is still surfaced (historical context), just flagged.
    assert BRIEF_TEXT in out


def test_stale_boundary_just_past_threshold(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    state = tmp_path / "state.json"
    _write_state(state, NOW - (DEFAULT_STALE_S + 1))

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))
    assert "STALE" in out


def test_fresh_boundary_just_under_threshold(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    state = tmp_path / "state.json"
    _write_state(state, NOW - (DEFAULT_STALE_S - 1))

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))
    assert "FRESH" in out
    assert "STALE" not in out


# ---------------------------------------------------------------------------
# Freshness unknown — brief present, but no last_tick_ts
# ---------------------------------------------------------------------------


def test_unknown_freshness_when_no_last_tick(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    state = tmp_path / "state.json"
    _write_state(state, None)  # state exists but carries no tick timestamp

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))
    assert "UNKNOWN" in out
    # Never claims the green FRESH verdict when it cannot prove freshness.
    # (The brief body itself may contain 🟢 from its own generation-time
    # posture line — that's why we check the banner verdict specifically.)
    assert "— FRESH**" not in out
    assert BRIEF_TEXT in out


# ---------------------------------------------------------------------------
# No brief yet — mini ticked but no brief generated
# ---------------------------------------------------------------------------


def test_no_brief_but_state_present(tmp_path):
    state = tmp_path / "state.json"
    _write_state(state, NOW - 30)
    brief = tmp_path / "brief.md"  # never created

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))
    assert "no brief yet" in out
    assert "--brief" in out  # tells the reader how to generate one


# ---------------------------------------------------------------------------
# Silent — mini has never run here (no brief AND no state)
# ---------------------------------------------------------------------------


def test_silent_when_no_brief_and_no_state(tmp_path):
    brief = tmp_path / "brief.md"      # absent
    state = tmp_path / "state.json"    # absent

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))
    assert out == ""  # harmless on a mini-less box — inject nothing


def test_silent_when_state_unreadable_and_no_brief(tmp_path):
    brief = tmp_path / "brief.md"  # absent
    state = tmp_path / "state.json"
    state.write_text("{ this is not json", encoding="utf-8")  # corrupt

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))
    # No usable tick and no brief → stay silent rather than emit a half-truth.
    assert out == ""


# ---------------------------------------------------------------------------
# Robustness — never raises on garbage tick values
# ---------------------------------------------------------------------------


def test_non_numeric_last_tick_is_treated_as_unknown(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"host": "testbox", "last_tick_ts": "not-a-number"}),
        encoding="utf-8",
    )

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"))
    assert "UNKNOWN" in out


# ---------------------------------------------------------------------------
# Calibration-ledger surfacing — the warm brief shows MY own track record
# ---------------------------------------------------------------------------


def test_calibration_block_surfaces_tracked_claims(tmp_path, monkeypatch):
    """A ledger with claims must surface in the warm brief; an absent verdict
    marker means nothing is re-checked yet (honest 'still unverified')."""
    from mini_dudeai import calibration_ledger as cl
    from mini_dudeai import warmstart

    ledger = tmp_path / "calibration_ledger.jsonl"
    monkeypatch.setenv("CALIBRATION_LEDGER_PATH", str(ledger))
    monkeypatch.setenv("HONEST_VERDICT_PATH", str(tmp_path / "no_marker.json"))
    cl.record_claim("all green", "fleet_green", "honest_status exit 0",
                    "d" * 40, ts=1.0, path=str(ledger))

    block = warmstart._calibration_block(NOW)
    assert "calibration ledger" in block
    assert "1 VERIFIED claim" in block and "still unverified" in block


def test_calibration_block_silent_when_no_ledger(tmp_path, monkeypatch):
    from mini_dudeai import warmstart
    monkeypatch.setenv("CALIBRATION_LEDGER_PATH", str(tmp_path / "absent.jsonl"))
    monkeypatch.setenv("HONEST_VERDICT_PATH", str(tmp_path / "absent.json"))
    assert warmstart._calibration_block(NOW) == ""


def test_calibration_block_never_raises(monkeypatch):
    """Defense in depth: even if the ledger import/IO blows up, the warm-start
    calibration block must degrade to '' and never break the hook."""
    from mini_dudeai import warmstart
    monkeypatch.setenv("CALIBRATION_LEDGER_PATH", "/nonexistent/dir/x.jsonl")
    monkeypatch.setenv("HONEST_VERDICT_PATH", "/nonexistent/dir/m.json")
    assert warmstart._calibration_block(NOW) == ""


# ---------------------------------------------------------------------------
# Reader/writer path wiring (2026-08-11)
# ---------------------------------------------------------------------------


def test_default_paths_come_from_the_app_adapter(tmp_path, monkeypatch):
    """_default_paths() must equal the adapter's app_artifact_paths() — the
    same function the fleet preset writes through. Hardcoded basenames here
    are exactly how the MA twin's warmstart ended up reading paths its daemon
    never writes and reported "mini has not run here" beside a ticking daemon
    (2026-08-11). MF-side limit, stated honestly: on MeshForge the adapter's
    values coincide with the old hardcodes, so a re-hardcode would still pass
    HERE — the MA twin's test (test_mini_artifact_paths.py in that repo) is
    the one that catches it. This pins the wiring and the MF values."""
    import os
    monkeypatch.setenv("MINI_DUDEAI_HOME", str(tmp_path))
    from mini_dudeai import _util, warmstart
    brief, state = warmstart._default_paths()
    a_brief, a_state, _a_hist = _util.app_artifact_paths()
    assert (brief, state) == (a_brief, a_state)
    assert brief == os.path.join(str(tmp_path), "mini_dudeai_brief.md")
    assert state == os.path.join(str(tmp_path), "mini_dudeai_state.json")
