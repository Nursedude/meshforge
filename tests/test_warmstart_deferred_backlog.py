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


def test_due_TODAY_is_surfaced_like_the_watcher_pages_it(tmp_path):
    """Boundary pin against scripts/deferred_work_watch.py: the watcher pages
    when today >= review_after (`today < ra_date -> continue`). The first cut
    here used `ra < today`, so the task the operator was paged about this
    morning was invisible in the same morning's warm-start brief."""
    p = _ledger(tmp_path, [{"id": "due-today", "status": "blocked",
                            "review_after": TODAY}])
    assert "due-today" in deferred_backlog_line(p, TODAY)


def test_missing_status_field_defaults_to_blocked_like_the_watcher(tmp_path):
    """The watcher's predicate is `t.get(\"status\", \"blocked\")` — a task
    written without a status key IS blocked to the mechanism this line
    mirrors. Skipping it here recreated the page-once-then-silence gap for
    exactly those tasks."""
    p = _ledger(tmp_path, [{"id": "no-status", "review_after": "2026-01-01"}])
    assert "no-status" in deferred_backlog_line(p, TODAY)


def test_unpadded_past_date_is_parsed_not_lexicographically_lost(tmp_path):
    """Lexicographic `ra < today` read \"2026-1-5\" (unpadded, PAST) as later
    than \"2026-08-12\" forever ('1' > '0' at position 5) — overdue since
    January, silent for good. strptime parses it the way the watcher does."""
    p = _ledger(tmp_path, [{"id": "old-unpadded", "status": "blocked",
                            "review_after": "2026-1-5"}])
    assert "old-unpadded" in deferred_backlog_line(p, TODAY)


def test_malformed_date_is_surfaced_not_silently_never_due(tmp_path):
    """A truly unparseable review date can never come due. The watcher pages
    these as ledger errors; the honest analogue here is surfacing the task."""
    for ra in ("08/12/2026", "yesterday"):
        p = _ledger(tmp_path, [{"id": "bad-date", "status": "blocked",
                                "review_after": ra}])
        assert "bad-date" in deferred_backlog_line(p, TODAY), ra


def test_mixed_type_ids_do_not_crash_the_hook(tmp_path):
    """sorted() over [123, \"abc\"] raises TypeError; a hand-edited numeric id
    must not take down the whole warm-start injection (the hook swallows the
    exception, so the failure mode was silence, not a traceback)."""
    p = _ledger(tmp_path, [
        {"id": 123, "status": "blocked", "review_after": "2026-01-01"},
        {"id": "abc", "status": "blocked", "review_after": "2026-01-01"},
    ])
    out = deferred_backlog_line(p, TODAY)
    assert "123" in out and "abc" in out


def test_unreadable_present_ledger_is_UNKNOWN_not_absent(tmp_path):
    """A ledger that EXISTS but cannot be read must never be filed under
    'absent by design'. An os.path.exists() pre-check answered False on
    EACCES/untraversable-parent too; absence is now judged only by
    read_json's FileNotFoundError leg. A directory at the path is the
    portable stand-in for 'present but unreadable' (chmod tricks are
    invisible to root, which is how the suite often runs)."""
    d = tmp_path / "deferred_work.json"
    d.mkdir()
    out = deferred_backlog_line(str(d), TODAY)
    assert "UNKNOWN, not empty" in out


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


def test_whitespace_padded_date_is_surfaced_like_the_watcher(tmp_path):
    """The watcher parses review_after RAW (`strptime(ra, ...)`), so a padded
    "2026-12-01 " is a paged ledger error there. The first cut here stripped
    before parsing — the padded date read as a clean future date, and the two
    consumers disagreed in the quiet direction (2026-08-12 re-review)."""
    p = _ledger(tmp_path, [{"id": "padded", "status": "blocked",
                            "review_after": "2026-12-01 "}])
    assert "padded" in deferred_backlog_line(p, TODAY)


def test_empty_ledger_path_is_loud_UNKNOWN_not_absent(tmp_path):
    """An empty path is a MISCONFIGURATION (e.g. `Environment=
    DEFERRED_WORK_LEDGER=` in a unit file), not an absent ledger. The watcher
    opens '' and pages LEDGER UNREADABLE; silence here would be the
    quiet-direction divergence again."""
    out = deferred_backlog_line("", TODAY)
    assert "UNKNOWN" in out and "empty" in out


def test_empty_env_override_reaches_the_loud_branch(tmp_path, monkeypatch):
    """render_warmstart must bind the env var the way the watcher does
    (`environ.get(key, default)`): a set-but-empty override must NOT fall
    through to the real production ledger."""
    monkeypatch.setenv("DEFERRED_WORK_LEDGER", "")
    out = render_warmstart(str(tmp_path / "none.md"), str(tmp_path / "none.json"),
                           1_780_000_010.0)
    assert "ledger path is empty" in out


_WATCHER = os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "deferred_work_watch.py")


def test_watcher_predicate_still_says_what_these_tests_pin():
    """MECHANIZED cross-file pin (2026-08-12 re-review). warmstart duplicates
    the watcher's overdue predicate DELIBERATELY (scripts/ is not a package,
    and warmstart.py is parity-byte-locked into MeshAnchor, which carries no
    watcher) — so nothing but this test notices when the watcher's predicate
    evolves. If any assertion here reddens: re-sync deferred_backlog_line's
    predicate to the watcher's new semantics, update the sibling tests, THEN
    update the pinned strings. Skipped where the watcher does not exist
    (MeshAnchor): absent by design, not blind.

    Same shape as the hs_codehead pathspec pin (check #10 in
    test_honest_status_skew_repos.sh): two hardcodes of one semantic must
    fail a test when they can drift, not stay green."""
    import pytest
    if not os.path.exists(_WATCHER):
        pytest.skip("no scripts/deferred_work_watch.py here — the ledger's "
                    "owning consumer lives in MeshForge only (skipped is "
                    "not passed: this pin runs only in the lead repo)")
    with open(_WATCHER, encoding="utf-8") as fh:
        src = fh.read()
    # Axis 1: missing status defaults to blocked.
    assert 't.get("status", "blocked")' in src
    # Axis 2 + 3: raw strptime parse (no strip), due when today >= ra.
    assert 'datetime.strptime(ra, "%Y-%m-%d")' in src
    assert "if today < ra_date" in src
    # Env binding: two-arg get, so a set-but-empty override propagates.
    assert 'os.environ.get("DEFERRED_WORK_LEDGER", os.path.join(' in src
