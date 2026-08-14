"""DialogBackend semantics — the 2026-08-14 TUI hardening arc, Batch 1.

Pins three behaviors and one deletion:

1. ``menu()`` never retries a user Cancel (exit 1) or Escape (exit 255) —
   the old blanket retry made every Escape need two presses (audit W2).
2. ``menu()`` still retries once on genuine subprocess failure, which
   ``_run`` now reports as -1 (distinguishable from whiptail's exit 1).
3. ``checklist()`` parses whiptail's quoted output with shlex, so a tag
   containing a space survives instead of being split into fragments (T2).
4. ``gauge()`` is deleted: zero callers existed and the implementation
   wrote a single value and exited — it could never animate progress (D1).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from backend import DialogBackend


def _make_backend(run_results):
    """DialogBackend whose _run pops canned (code, output) results."""
    be = DialogBackend.__new__(DialogBackend)
    be.backend = 'whiptail'
    be.width = 78
    be.height = 22
    be.list_height = 14
    be._status_bar = None
    be._run_calls = []

    def fake_run(args, timeout=None):
        be._run_calls.append(args)
        return run_results.pop(0)

    be._run = fake_run
    return be


class TestMenuCancelSemantics:
    def test_escape_returns_none_without_retry(self):
        be = _make_backend([(255, "")])
        assert be.menu("T", "text", [("a", "A")]) is None
        assert len(be._run_calls) == 1

    def test_cancel_returns_none_without_retry(self):
        be = _make_backend([(1, "")])
        assert be.menu("T", "text", [("a", "A")]) is None
        assert len(be._run_calls) == 1

    def test_subprocess_failure_retries_once(self):
        be = _make_backend([(-1, ""), (-1, "")])
        assert be.menu("T", "text", [("a", "A")]) is None
        assert len(be._run_calls) == 2

    def test_subprocess_failure_then_success_returns_selection(self):
        be = _make_backend([(-1, ""), (0, "a")])
        assert be.menu("T", "text", [("a", "A")]) == "a"
        assert len(be._run_calls) == 2

    def test_success_returns_selection_one_call(self):
        be = _make_backend([(0, "a")])
        assert be.menu("T", "text", [("a", "A")]) == "a"
        assert len(be._run_calls) == 1


class TestChecklistParsing:
    def test_tags_with_spaces_survive(self):
        be = _make_backend([(0, '"tag one" "tag2"')])
        result = be.checklist("T", "text", [("tag one", "d", True),
                                            ("tag2", "d", False)])
        assert result == ["tag one", "tag2"]

    def test_unquoted_single_tags(self):
        # dialog(1) emits unquoted output for single-word tags
        be = _make_backend([(0, 'alpha beta')])
        assert be.checklist("T", "t", [("alpha", "d", True),
                                       ("beta", "d", True)]) == ["alpha", "beta"]

    def test_empty_selection(self):
        be = _make_backend([(0, '')])
        assert be.checklist("T", "t", [("a", "d", False)]) == []

    def test_cancel_returns_none(self):
        be = _make_backend([(1, "")])
        assert be.checklist("T", "t", [("a", "d", False)]) is None

    def test_unparseable_output_returns_none(self):
        be = _make_backend([(0, '"unterminated')])
        assert be.checklist("T", "t", [("a", "d", False)]) is None


class TestDeadCodeStaysDead:
    def test_gauge_removed(self):
        # Deleted 2026-08-14: zero callers, single-write non-functional
        # implementation. If a progress affordance returns, it must be a
        # working one with a caller and a test.
        assert not hasattr(DialogBackend, 'gauge')
