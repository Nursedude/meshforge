"""The deferred-work backlog line in the warm-start emitter.

2026-08-12. The ledger's contract is "a gated task can NEVER fall silent" — but
it pages ONCE when review_after passes and then never again. Ten blocked tasks
were overdue that day, the oldest by ~7 weeks, on a fleet worked daily. The
witness fired as designed; nothing re-surfaced it, and the reader who could act
never opened the file. These tests pin the re-surfacing, and specifically pin
that an UNREADABLE ledger never reads as an empty backlog.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai.warmstart import (  # noqa: E402
    deferred_backlog_line,
    render_warmstart,
)

TODAY = "2026-08-12"


def _ledger(tmp_path, tasks):
    p = tmp_path / "deferred_work.json"
    p.write_text(json.dumps({"tasks": tasks}))
    return str(p)


def test_overdue_blocked_task_is_named(tmp_path):
    p = _ledger(tmp_path, [{"id": "old-thing", "status": "blocked",
                            "review_after": "2026-06-24"}])
    out = deferred_backlog_line(p, TODAY)
    assert "1 deferred task(s) past review" in out and "old-thing" in out


def test_future_review_date_is_silent(tmp_path):
    p = _ledger(tmp_path, [{"id": "later", "status": "blocked",
                            "review_after": "2026-11-10"}])
    assert deferred_backlog_line(p, TODAY) == ""


def test_done_and_other_statuses_are_ignored(tmp_path):
    """Only 'blocked' pages in the watcher, so only 'blocked' is overdue here —
    the line must agree with the mechanism it re-surfaces, not invent its own."""
    p = _ledger(tmp_path, [
        {"id": "shipped", "status": "done", "review_after": "2026-01-01"},
        {"id": "parked", "status": "deferred", "review_after": "2026-01-01"},
        {"id": "ready", "status": "ready", "review_after": "2026-01-01"},
    ])
    assert deferred_backlog_line(p, TODAY) == ""


def test_blocked_with_no_review_date_is_surfaced(tmp_path):
    """A blocked task with no date can NEVER come due — it is more overdue than
    a dated one, never less, so it must not slip through the < comparison."""
    for ra in (None, "", "   ", 20260624):
        p = _ledger(tmp_path, [{"id": "dateless", "status": "blocked",
                                "review_after": ra}])
        assert "dateless" in deferred_backlog_line(p, TODAY), ra


def test_absent_ledger_is_silent_not_an_error(tmp_path):
    """The ledger lives on the operator's manager box; absent elsewhere is BY
    DESIGN (inert), and inert must never render as a failure."""
    assert deferred_backlog_line(str(tmp_path / "nope.json"), TODAY) == ""


def test_unreadable_ledger_says_UNKNOWN_never_empty(tmp_path):
    """THE honesty leg: a gate ledger that cannot be read is exactly when you
    must not conclude the backlog is empty (honest_failure_modes #1)."""
    p = tmp_path / "deferred_work.json"
    p.write_text("{not json")
    out = deferred_backlog_line(str(p), TODAY)
    assert "unreadable" in out and "UNKNOWN, not empty" in out


def test_ledger_without_a_task_list_says_UNKNOWN(tmp_path):
    p = tmp_path / "deferred_work.json"
    p.write_text(json.dumps({"tasks": "not-a-list"}))
    out = deferred_backlog_line(str(p), TODAY)
    assert "UNKNOWN, not empty" in out


def test_many_overdue_are_capped_but_the_COUNT_is_honest(tmp_path):
    """Truncating the names is fine; truncating the count silently is not —
    'no silent caps' (the line must say how many it did not show)."""
    tasks = [{"id": f"t{i}", "status": "blocked", "review_after": "2026-01-01"}
             for i in range(9)]
    out = deferred_backlog_line(_ledger(tmp_path, tasks), TODAY)
    assert "9 deferred task(s) past review" in out
    assert "+3 more" in out


def test_render_warmstart_carries_the_line_with_a_fresh_brief(tmp_path):
    brief = tmp_path / "b.md"
    brief.write_text("# brief body\n")
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"last_tick_ts": 1_780_000_000.0}))
    p = _ledger(tmp_path, [{"id": "old-thing", "status": "blocked",
                            "review_after": "2026-01-01"}])
    out = render_warmstart(str(brief), str(state), 1_780_000_010.0,
                           ledger_path=p)
    assert "FRESH" in out and "old-thing" in out and "# brief body" in out


def test_backlog_shows_even_on_a_box_with_no_mini(tmp_path):
    """render_warmstart is silent where mini never ran — but the backlog is the
    OPERATOR's, not mini's, and must not be hidden by a missing watcher."""
    p = _ledger(tmp_path, [{"id": "old-thing", "status": "blocked",
                            "review_after": "2026-01-01"}])
    out = render_warmstart(str(tmp_path / "none.md"), str(tmp_path / "none.json"),
                           1_780_000_010.0, ledger_path=p)
    assert "old-thing" in out


def test_no_mini_and_no_backlog_stays_completely_silent(tmp_path):
    """The hook must remain harmless on a plain box — no ledger, no mini, no
    output at all (not a blank banner)."""
    out = render_warmstart(str(tmp_path / "none.md"), str(tmp_path / "none.json"),
                           1_780_000_010.0, ledger_path=str(tmp_path / "no.json"))
    assert out == ""
