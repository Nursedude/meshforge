"""scripts/tui_leaf_walk.py must not score a SELF-RESCUED leaf as ok (2026-10-30).

``safe_call`` is not the only rescuer. Handlers such as ``delivery.py`` catch
their own exception and render an "UNKNOWN … could not be read: KeyError: x"
screen, then return normally. The walk recorded dialog TITLES only, so that
screen — a crash wearing a polite face — scored "returned clean". These pins
make the walk read the TEXT and report such a leaf as its own outcome:
``said-unknown`` (the screen admitted it could not see — honest, but not a
pass) versus ``ok``. Pure classification — no handler is dispatched here.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts")]

import tui_leaf_walk  # noqa: E402


def _r(*dialogs, errors=(), owned=True):
    return {"owned": owned, "errors": list(errors),
            "dialogs": [list(d) for d in dialogs]}


def test_clean_leaf_is_ok():
    out = tui_leaf_walk.classify(_r(["msgbox", "Version", "MeshForge 0.7.0-beta"]))
    assert out["status"] == "ok"


def test_handler_rendered_exception_is_said_unknown_not_ok():
    text = ("Delivery legs — UNKNOWN\n\nThe gateway's delivery record could "
            "not be read:\n  KeyError: 'legs'\n\nUNKNOWN is not a pass.")
    out = tui_leaf_walk.classify(_r(["textbox", "Delivery legs", text]))
    assert out["status"] == "said-unknown"
    assert "KeyError" in out["note"]          # the witness, not just a label


def test_rendered_traceback_is_said_unknown():
    out = tui_leaf_walk.classify(
        _r(["msgbox", "Status", "Traceback (most recent call last):\n  File x"]))
    assert out["status"] == "said-unknown"


def test_bare_unknown_word_is_said_unknown():
    out = tui_leaf_walk.classify(_r(["msgbox", "RNS", "rnsd state: UNKNOWN"]))
    assert out["status"] == "said-unknown"


def test_word_containing_unknown_is_not_flagged():
    # "unknowns" / lowercase prose must not trip it — the screens' contract
    # word is the upper-case token UNKNOWN.
    out = tui_leaf_walk.classify(_r(["msgbox", "Help", "known unknowns are fine"]))
    assert out["status"] == "ok"


def test_safe_call_error_still_fails():
    out = tui_leaf_walk.classify(_r(["msgbox", "Error", "boom"], errors=["Error"]))
    assert out["status"] == "fail"


def test_unowned_tag_fails():
    out = tui_leaf_walk.classify(_r(owned=False))
    assert out["status"] == "fail"


def test_said_unknown_does_not_count_as_returned_clean_in_exit():
    # said-unknown is not a FAILURE (it is honest), and never counts
    # toward "returned clean" either.
    assert tui_leaf_walk.counts_as_clean("ok") is True
    assert tui_leaf_walk.counts_as_clean("said-unknown") is False
    assert tui_leaf_walk.is_failure("said-unknown") is False
    assert tui_leaf_walk.is_failure("fail") is True


def test_exit_code_said_unknown_is_2_never_a_pass():
    # repo rule (calibrated_claims #4): exit 2 / UNKNOWN is never a pass.
    assert tui_leaf_walk.exit_code(failures=0, said_unknown=0, allowed=3) == 0
    assert tui_leaf_walk.exit_code(failures=0, said_unknown=1, allowed=3) == 2
    assert tui_leaf_walk.exit_code(failures=1, said_unknown=1, allowed=3) == 1
    assert tui_leaf_walk.exit_code(failures=0, said_unknown=0, allowed=0) == 2


def test_unobservable_is_said_unknown():
    # TX Power Truth's honest blind state is the word UNOBSERVABLE, not UNKNOWN.
    out = tui_leaf_walk.classify(
        _r(["msgbox", "TX Power Truth", "Status: UNOBSERVABLE\nPart of it ..."]))
    assert out["status"] == "said-unknown"


def test_printed_unknown_on_stdout_is_said_unknown():
    # Many screens print() to the terminal instead of a dialog (Drift Check,
    # Service Status). A title/dialog-only walk was blind to what they said.
    r = _r()
    r["stdout"] = "=== RNS Config Drift Check ===\n  rnsd state: UNKNOWN\n"
    out = tui_leaf_walk.classify(r)
    assert out["status"] == "said-unknown"
    assert "[stdout]" in out["note"]


def test_printed_traceback_on_stdout_is_said_unknown():
    r = _r()
    r["stdout"] = "x\nValueError: bad literal\n"
    assert tui_leaf_walk.classify(r)["status"] == "said-unknown"
