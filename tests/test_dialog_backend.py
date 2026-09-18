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

import pytest

from backend import DialogBackend, DialogError, _ANSI_RE


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

    def test_subprocess_failure_retries_once_then_raises(self):
        # Review F4/F7: a dead dialog must not impersonate a user cancel —
        # after the one retry, menu() raises instead of returning None.
        be = _make_backend([(-1, ""), (-1, "")])
        with pytest.raises(DialogError):
            be.menu("T", "text", [("a", "A")])
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

    def test_unparseable_output_raises(self):
        # An OK press whose selections can't be read is an ERROR, not a
        # cancel — None would silently drop the user's choices (review F7).
        be = _make_backend([(0, '"unterminated')])
        with pytest.raises(DialogError):
            be.checklist("T", "t", [("a", "d", False)])


class TestDeadDialogNeverAnswers:
    """Review F7: input primitives raise on subprocess death instead of
    fabricating an answer (yesno=False / inputbox=None used to read as a
    choice the operator never made)."""

    def test_yesno_raises_on_dead_subprocess(self):
        be = _make_backend([(-1, "")])
        with pytest.raises(DialogError):
            be.yesno("Confirm?", "keep going?")

    def test_yesno_escape_is_no(self):
        be = _make_backend([(255, "")])
        assert be.yesno("Confirm?", "keep going?") is False

    def test_inputbox_raises_on_dead_subprocess(self):
        be = _make_backend([(-1, "")])
        with pytest.raises(DialogError):
            be.inputbox("T", "value?")

    def test_checklist_raises_on_dead_subprocess(self):
        be = _make_backend([(-1, "")])
        with pytest.raises(DialogError):
            be.checklist("T", "t", [("a", "d", False)])


class TestMenuBoxGrowsForContent:
    def test_tall_panel_grows_box_height(self):
        # Review F3: a ~10-line panel inside the fixed 22-row box was
        # clipped even on a 40-row terminal — the fit only ever shrank.
        be = _make_backend([(0, "a")])
        tall_text = "\n".join(f"line {i}" for i in range(10))
        import unittest.mock as um
        with um.patch('backend.os.get_terminal_size',
                      return_value=um.Mock(lines=40, columns=100)):
            be.menu("T", tall_text, [("a", "A")])
        args = be._run_calls[0]
        h = int(args[args.index('--menu') + 2])
        # chrome(6) + text(10) + list(14) = 30 needed; must have grown past 22
        assert h >= 30, f"box height {h} still clips a 10-line panel"


class TestAnsiRegex:
    def test_strips_color_and_cursor_sequences(self):
        s = "\033[0;32mLOCKED\033[0m and \033[2Kplain"
        assert _ANSI_RE.sub('', s) == "LOCKED and plain"


class TestCancelButtonLabel:
    """The Cancel button is the escape hatch that cannot scroll off.

    whiptail and dialog spell the flag differently, and an unsupported
    flag does not degrade — it kills the dialog. So the spelling is
    chosen from the DETECTED backend, and an unrecognised one emits
    nothing: a mislabelled button is survivable, a dead menu is not.
    """

    def test_whiptail_spelling(self):
        be = _make_backend([(0, "a")])
        be.menu("T", "text", [("a", "A")], cancel_label="Back")
        args = be._run_calls[0]
        assert '--cancel-button' in args
        assert args[args.index('--cancel-button') + 1] == 'Back'

    def test_dialog_spelling(self):
        be = _make_backend([(0, "a")])
        be.backend = 'dialog'
        be.menu("T", "text", [("a", "A")], cancel_label="Back")
        args = be._run_calls[0]
        assert '--cancel-label' in args, "dialog(1) spells it --cancel-label"
        assert '--cancel-button' not in args

    def test_unknown_backend_emits_no_flag(self):
        be = _make_backend([(0, "a")])
        be.backend = None
        be.menu("T", "text", [("a", "A")], cancel_label="Back")
        args = be._run_calls[0]
        assert not [a for a in args if a.startswith('--cancel')], args

    def test_flag_precedes_the_menu_box_option(self):
        """whiptail parses [options] --menu text h w lh [tag item]...

        A --cancel-button AFTER --menu would be read as a menu ITEM, so
        the button label would silently become a selectable row.
        """
        be = _make_backend([(0, "a")])
        be.menu("T", "text", [("a", "A")], cancel_label="Back")
        args = be._run_calls[0]
        assert args.index('--cancel-button') < args.index('--menu')

    def test_omitted_label_leaves_args_untouched(self):
        """No caller opts in -> byte-identical to the pre-change command."""
        be = _make_backend([(0, "a")])
        be.menu("T", "text", [("a", "A")])
        assert be._run_calls[0][0] == '--title'
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]


class TestDeadCodeStaysDead:
    def test_gauge_removed(self):
        # Deleted 2026-08-14: zero callers, single-write non-functional
        # implementation. If a progress affordance returns, it must be a
        # working one with a caller and a test.
        assert not hasattr(DialogBackend, 'gauge')


class TestCancelLabelInference:
    """The button's label is DERIVED from the menu's own rows.

    A navigational menu carries a `back` row and maps menu()'s None to
    that same row -- one control, two faces, and only the row can scroll
    off a 24x80 terminal. Deriving the label from the choices means the
    two faces cannot disagree.

    Measured 2026-09-18: MeshForge has 238 menu call sites, ~169 of them
    carrying a `back` row, and the "None means back" contract is spelled
    at least five different ways across them. No regex over call sites
    classifies that reliably; the presence of the ROW does.
    """

    def test_a_back_row_infers_the_back_label(self):
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("status", "Status"), ("back", "Back")])
        args = be._run_calls[0]
        assert '--cancel-button' in args
        assert args[args.index('--cancel-button') + 1] == 'Back'

    def test_no_back_row_keeps_whiptails_cancel(self):
        """An operational picker must NOT say Back."""
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("eth0", "eth0"), ("wlan0", "wlan0")])
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]

    def test_explicit_label_beats_inference(self):
        """The top-level menu says Exit even though rows may look navigational."""
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("a", "A"), ("back", "Back")],
                cancel_label="Exit")
        args = be._run_calls[0]
        assert args[args.index('--cancel-button') + 1] == 'Exit'

    def test_explicit_cancel_is_the_opt_out(self):
        """A back row whose Cancel genuinely is not a back can say so."""
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("a", "A"), ("back", "Back")],
                cancel_label="Cancel")
        args = be._run_calls[0]
        assert args[args.index('--cancel-button') + 1] == 'Cancel'

    def test_explicit_none_emits_no_flag(self):
        """None stays distinguishable from 'infer one for me'."""
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("a", "A"), ("back", "Back")],
                cancel_label=None)
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]

    def test_inference_itself_tolerates_ragged_rows(self):
        """The HELPER is defensive, even though menu() is not.

        menu() has never accepted a ragged choices list — `for tag, desc
        in choices` raises on one, and that contract predates this change.
        So this pins the helper directly rather than claiming menu()
        tolerates input it never did: label inference must not be the
        thing that introduces a new crash path into the dialog layer.
        """
        be = _make_backend([])
        assert be._infer_cancel_label(
            [(), None, ("back", "Back")], be._AUTO_CANCEL) == 'Back'
        assert be._infer_cancel_label(None, be._AUTO_CANCEL) is None
        assert be._infer_cancel_label([], be._AUTO_CANCEL) is None

    def test_back_must_be_the_TAG_not_the_label(self):
        """A row LABELLED 'Back' with another tag is not the back row."""
        be = _make_backend([(0, "x")])
        be.menu("T", "text", [("return_home", "Back")])
        assert not [a for a in be._run_calls[0] if a.startswith('--cancel')]
