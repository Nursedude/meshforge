"""Tests for the mini-dudeai warm-start emitter (honest freshness banner).

The contract under test: warmstart re-derives freshness from state.json's
last_tick_ts at READ time, so a brief frozen by a dead daemon is flagged STALE
rather than presented as current. Uses tmp_path exclusively — never the live
store or the operator's ~/mini_dudeai_* files.
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

    out = render_warmstart(str(brief), str(state), NOW)

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

    out = render_warmstart(str(brief), str(state), NOW)

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

    out = render_warmstart(str(brief), str(state), NOW)
    assert "STALE" in out


def test_fresh_boundary_just_under_threshold(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    state = tmp_path / "state.json"
    _write_state(state, NOW - (DEFAULT_STALE_S - 1))

    out = render_warmstart(str(brief), str(state), NOW)
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

    out = render_warmstart(str(brief), str(state), NOW)
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

    out = render_warmstart(str(brief), str(state), NOW)
    assert "no brief yet" in out
    assert "--brief" in out  # tells the reader how to generate one


# ---------------------------------------------------------------------------
# Silent — mini has never run here (no brief AND no state)
# ---------------------------------------------------------------------------


def test_silent_when_no_brief_and_no_state(tmp_path):
    brief = tmp_path / "brief.md"      # absent
    state = tmp_path / "state.json"    # absent

    out = render_warmstart(str(brief), str(state), NOW)
    assert out == ""  # harmless on a mini-less box — inject nothing


def test_silent_when_state_unreadable_and_no_brief(tmp_path):
    brief = tmp_path / "brief.md"  # absent
    state = tmp_path / "state.json"
    state.write_text("{ this is not json", encoding="utf-8")  # corrupt

    out = render_warmstart(str(brief), str(state), NOW)
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

    out = render_warmstart(str(brief), str(state), NOW)
    assert "UNKNOWN" in out
