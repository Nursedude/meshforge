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
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))

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
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))

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
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))
    assert "STALE" in out


def test_fresh_boundary_just_under_threshold(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    state = tmp_path / "state.json"
    _write_state(state, NOW - (DEFAULT_STALE_S - 1))

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))
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
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))
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
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))
    assert "no brief yet" in out
    assert "--brief" in out  # tells the reader how to generate one


# ---------------------------------------------------------------------------
# Silent — mini has never run here (no brief AND no state)
# ---------------------------------------------------------------------------


def test_silent_when_no_brief_and_no_state(tmp_path):
    brief = tmp_path / "brief.md"      # absent
    state = tmp_path / "state.json"    # absent

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))
    assert out == ""  # harmless on a mini-less box — inject nothing


def test_silent_when_state_unreadable_and_no_brief(tmp_path):
    brief = tmp_path / "brief.md"  # absent
    state = tmp_path / "state.json"
    state.write_text("{ this is not json", encoding="utf-8")  # corrupt

    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))
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
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))
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


# --- the session handoff block (2026-09-07) ----------------------------------
# SessionStart injected mini's brief — MACHINE state — and nothing else, so the
# previous session's handoff note (which CLAUDE.md calls the active sprint, and
# which opened "Do this first") sat five hours old and unread while the session
# worked on something else. The operator's framing: a fresh AI would not look
# for other notes. Every test here pins `path`, so none reads the real note.

import re  # noqa: E402
from mini_dudeai.warmstart import HANDOFF_MAX_CHARS, handoff_block  # noqa: E402

_NOTE = (
    "# Gateway session notes — boxa\n\n"
    "> season line\n\n"
    "## ⏭️ START HERE — close: do the thing\n\n"
    "**Do this first.** the plan.\n\n"
    "## Older section\n\nnot this one\n"
)


def test_handoff_lifts_the_start_here_section(tmp_path):
    n = tmp_path / "gateway-session-notes-boxa.md"
    n.write_text(_NOTE, encoding="utf-8")
    out = handoff_block(n.stat().st_mtime + 60, path=str(n))
    assert "START HERE" in out and "Do this first" in out
    assert "not this one" not in out, "must stop at the next ## heading"
    assert "session handoff" in out


def test_handoff_flags_a_stale_note_rather_than_presenting_it_as_current(tmp_path):
    """An old note read as current is worse than none — same contract the
    freshness banner already keeps for the brief."""
    n = tmp_path / "gateway-session-notes-boxa.md"
    n.write_text(_NOTE, encoding="utf-8")
    out = handoff_block(n.stat().st_mtime + 10 * 24 * 3600, path=str(n))
    assert "STALE" in out and "verify before acting" in out


def test_absent_with_no_siblings_is_SILENT(tmp_path):
    """Absent-by-design is inert. Most of the fleet carries no handoff note,
    and this text is injected into EVERY session — a permanent "nothing here"
    line is noise that trains the reader to skip."""
    assert handoff_block(1_800_000_000.0,
                         path=str(tmp_path / "gateway-session-notes-boxa.md")) == ""


def test_absent_WITH_siblings_is_loud(tmp_path):
    """THE defect this function shipped with: gethostname() may return
    "BoxA" while the note on disk is "...-boxa.md", so the first cut
    matched nothing and would have been silently inert forever on the one box
    that has a note. Notes present + none matched = a finding, not silence."""
    (tmp_path / "gateway-session-notes-otherbox.md").write_text(_NOTE, encoding="utf-8")
    out = handoff_block(1_800_000_000.0,
                        path=str(tmp_path / "gateway-session-notes-boxa.md"))
    assert out != ""
    assert "no handoff note matched this box" in out
    assert "otherbox" in out, "must name what it DID find"


def test_handoff_is_truncated_not_dumped(tmp_path):
    """RETARGETED 2026-09-09, not deleted.

    This asserted only that SOMETHING was elided and the block stayed bounded
    — which stayed green for the whole period in which every real section on
    the fleet was silently showing 24-39% of itself. "Is truncated" was never
    the property worth pinning; "says how much it truncated" is. The bound is
    kept (it is still the footprint guard); the vague marker is replaced by
    the quantified contract the sibling tests below pin in detail.
    """
    n = tmp_path / "gateway-session-notes-boxa.md"
    n.write_text("## START HERE\n\n" + ("x" * 9000), encoding="utf-8")
    out = handoff_block(n.stat().st_mtime + 60, path=str(n))
    assert "TRUNCATED" in out
    assert "unread" in out, "an elision must state its own size"
    assert len(out) < HANDOFF_MAX_CHARS + 400
    # A body with no usable line boundary must still yield its opening rather
    # than collapsing to nothing when the line-aligned cut is unavailable.
    assert "xxxx" in out


def test_truncation_states_how_much_is_unread(tmp_path):
    """The 2026-09-09 defect: a cut that hid its own magnitude.

    The reader has to decide whether the Read is worth a tool call, and
    "(truncated)" carries nothing to decide on. Numbers must be real and
    self-consistent, not decorative.
    """
    n = tmp_path / "gateway-session-notes-boxa.md"
    section = "## START HERE — x\n\n" + "\n".join(
        f"    key{i:<10} value {i}" for i in range(300))
    n.write_text(section, encoding="utf-8")
    out = handoff_block(n.stat().st_mtime + 60, path=str(n))

    m = re.search(r"showing ([\d,]+) of ([\d,]+) chars \((\d+)%\); "
                  r"([\d,]+) unread", out)
    assert m, f"truncation notice must carry real numbers, got: {out[-300:]!r}"
    shown, total, pct, unread = (int(g.replace(",", "")) for g in m.groups())
    assert shown + unread == total, "shown + unread must account for the whole"
    assert total == len(section.strip()), "total must be the REAL section size"
    assert 0 < shown <= HANDOFF_MAX_CHARS
    assert pct == round(100 * shown / total)
    assert unread > 0, "a truncation notice that reports 0 unread is a lie"


def test_truncation_cuts_on_a_line_boundary(tmp_path):
    """These sections lead with an indented key/value block — the payload.

    Half a table row reads as data, not as damage, so the cut must land
    between lines whenever that still spends most of the budget.
    """
    n = tmp_path / "gateway-session-notes-boxa.md"
    n.write_text("## START HERE\n\n" + "\n".join(
        f"    row{i:<4} value-{i}" for i in range(400)), encoding="utf-8")
    out = handoff_block(n.stat().st_mtime + 60, path=str(n))
    shown = out.split("…**TRUNCATED")[0]
    assert shown.rstrip().endswith(tuple(f"value-{i}" for i in range(400))), \
        "cut landed mid-row instead of on a line boundary"


def test_short_handoff_carries_no_truncation_notice(tmp_path):
    """An elision notice on a complete section would be a false claim of loss."""
    n = tmp_path / "gateway-session-notes-boxa.md"
    n.write_text("## START HERE\n\n    verdict   all good\n", encoding="utf-8")
    out = handoff_block(n.stat().st_mtime + 60, path=str(n))
    assert "TRUNCATED" not in out and "unread" not in out
    assert "all good" in out


def test_note_without_any_section_says_so(tmp_path):
    n = tmp_path / "gateway-session-notes-boxa.md"
    n.write_text("just prose, no headings\n", encoding="utf-8")
    out = handoff_block(n.stat().st_mtime + 60, path=str(n))
    assert "no `## ` section" in out and "read it directly" in out


def test_injected_path_reads_nothing_ambient(tmp_path, monkeypatch):
    """The seam must cover the WHOLE function: an injected path must not leave
    the sibling glob reading the operator's real home. A seam covering half a
    function still lets a verdict depend on the box running the suite."""
    monkeypatch.setattr("mini_dudeai.warmstart.operator_home",
                        lambda: "/nonexistent-operator-home")
    assert handoff_block(1_800_000_000.0,
                         path=str(tmp_path / "gateway-session-notes-boxa.md")) == ""


def test_hostname_resolution_tolerates_case(tmp_path, monkeypatch):
    """THE defect that actually shipped, and the one the other tests could not
    see: they all inject `path=`, so the RESOLUTION branch was never exercised
    and removing the case fallback left every test green.

    gethostname() may return "BoxA"; the note on disk is
    "gateway-session-notes-boxa.md". Matching only the exact case made the
    feature silently inert on the one box that has a note — found by RUNNING
    it, not by reading it. This pins the branch, not the parse.
    """
    plans = tmp_path / ".claude" / "plans"
    plans.mkdir(parents=True)
    (plans / "gateway-session-notes-boxa.md").write_text(_NOTE, encoding="utf-8")
    monkeypatch.setattr("mini_dudeai.warmstart.operator_home", lambda: str(tmp_path))
    monkeypatch.setattr("socket.gethostname", lambda: "BoxA")
    out = handoff_block(1_800_000_000.0)          # no path= — resolve it
    assert "START HERE" in out, "mixed-case hostname must still find the note"


def test_handoff_note_path_is_one_resolver_for_both_consumers(tmp_path, monkeypatch):
    """Finding 18 (2026-09-09): harness_audit.sh lowercased the hostname
    unconditionally while this module tried the exact case first — two
    consumers, two resolutions of one file. The audit now asks THIS function
    with its own `hostname`, so an exact-case note (the shape the audit read
    as 'absent') and a lowercase one resolve the same way for both."""
    from mini_dudeai.warmstart import handoff_note_path
    plans = tmp_path / ".claude" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setattr("mini_dudeai.warmstart.operator_home", lambda: str(tmp_path))
    exact = plans / "gateway-session-notes-BoxA.md"
    exact.write_text(_NOTE, encoding="utf-8")
    path, tried = handoff_note_path("BoxA.mf.internal")
    assert path == str(exact), "exact case must win when it exists"
    exact.unlink()
    (plans / "gateway-session-notes-boxa.md").write_text(_NOTE, encoding="utf-8")
    path, tried = handoff_note_path("BoxA")
    assert path.endswith("gateway-session-notes-boxa.md")
    assert len(tried) == 2
    path, tried = handoff_note_path("nobody")
    assert path == tried[0] and not __import__("os").path.exists(path)


def test_hostname_resolution_strips_the_domain(tmp_path, monkeypatch):
    plans = tmp_path / ".claude" / "plans"
    plans.mkdir(parents=True)
    (plans / "gateway-session-notes-boxa.md").write_text(_NOTE, encoding="utf-8")
    monkeypatch.setattr("mini_dudeai.warmstart.operator_home", lambda: str(tmp_path))
    monkeypatch.setattr("socket.gethostname", lambda: "BoxA.mf.internal")
    assert "START HERE" in handoff_block(1_800_000_000.0)
