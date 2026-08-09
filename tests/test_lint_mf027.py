"""Tests for the MF027 probe fail-dark guard in scripts/lint.py.

THE defect class (#80: 18/18 findings one class; build:fix doctrine
2026-07-29): a degraded internal state mapped to a valid-looking value. In a
``probe_*`` function, an except-handler that returns None WITHOUT a
``note_disposition`` witness is byte-identical to "all is well" at every
consumer — the observation channel failed and nothing can ever see it.
Compiled from the write-time checklist (prose) into a gate, because gated
classes stop recurring and prose rules do not: the 3afda33a session
re-committed this class with the rule loaded in context.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import importlib.util
_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint_mf027", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


DARK = '''\
def probe_example_dark(x=None):
    try:
        observe(x)
    except OSError:
        return None
    return None
'''

BARE_RETURN_DARK = '''\
def probe_example_bare(x=None):
    try:
        observe(x)
    except Exception:
        return
    return None
'''

WITNESSED = '''\
def probe_example_ok(x=None):
    try:
        observe(x)
    except OSError:
        note_disposition("example", "indeterminate", reason="channel failed")
        return None
    return None
'''

SIGNAL_RETURN = '''\
def probe_example_fires(x=None):
    try:
        observe(x)
    except TimeoutError:
        return Signal(cls="example", subject="s", severity="wedge", detail="d")
    return None
'''

HELPER_EXEMPT = '''\
def _load_example_state(path):
    try:
        return open(path).read()
    except OSError:
        return None
'''


# ── the 2026-08-09 sanctioned-indirection widening ──────────────────────
# Two probes must reach the SAME verdict from the same evidence, so the
# classifier is shared. Demanding the literal call inside each handler would
# force a copy per probe — the two-consumers-two-copies drift that produced
# the moc3 blindness this widening was cut for.

WITNESS_HELPER = '''\
def _note_example_unreachable(cls):
    if wired():
        note_disposition(cls, "indeterminate", reason="manager is down")
        return
    note_disposition(cls, "inert", reason="absent by design")


def probe_example_via_helper(x=None):
    try:
        observe(x)
    except OSError:
        _note_example_unreachable("example")
        return None
    return None
'''

SILENT_HELPER = '''\
def _note_example_silent(cls):
    log("something went wrong")


def probe_example_via_silent_helper(x=None):
    try:
        observe(x)
    except OSError:
        _note_example_silent("example")
        return None
    return None
'''

FOREIGN_HELPER = '''\
def probe_example_via_foreign(x=None):
    try:
        observe(x)
    except OSError:
        _note_from_another_module("example")
        return None
    return None
'''

NON_NOTE_HELPER = '''\
def _classify_example(cls):
    note_disposition(cls, "inert", reason="absent by design")


def probe_example_via_misnamed(x=None):
    try:
        observe(x)
    except OSError:
        _classify_example("example")
        return None
    return None
'''


def _issues(tmp_path, source, name="watchdog_probes_fixture.py"):
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return lint.check_probe_fail_dark([str(p)])


def test_dark_except_return_none_is_an_error(tmp_path):
    got = _issues(tmp_path, DARK)
    assert len(got) == 1
    assert got[0].code == "MF027"
    assert "note_disposition" in got[0].message


def test_bare_return_in_except_is_an_error(tmp_path):
    assert len(_issues(tmp_path, BARE_RETURN_DARK)) == 1


def test_witnessed_except_passes(tmp_path):
    assert _issues(tmp_path, WITNESSED) == []


def test_returning_a_signal_is_loud_not_dark(tmp_path):
    """The probe FIRING from an except handler is the opposite of dark —
    the survey's first draft flagged exactly this and was wrong."""
    assert _issues(tmp_path, SIGNAL_RETURN) == []


def test_helpers_are_exempt(tmp_path):
    """Non-probe_* functions own their own contracts (documented state
    loaders deliberately return defaults)."""
    assert _issues(tmp_path, HELPER_EXEMPT) == []


def test_same_module_note_helper_satisfies_the_witness(tmp_path):
    """A shared classifier that provably notes a disposition IS a witness."""
    assert _issues(tmp_path, WITNESS_HELPER) == []


def test_silent_note_helper_still_fails(tmp_path):
    """The widening must not become a bypass: a helper NAMED _note_* that
    never notes anything leaves the handler exactly as dark as before."""
    got = _issues(tmp_path, SILENT_HELPER)
    assert len(got) == 1
    assert got[0].code == "MF027"


def test_helper_from_another_module_is_not_trusted(tmp_path):
    """Unresolvable here — the lint cannot prove it leaves a witness, so it
    does not get the benefit of the doubt."""
    assert len(_issues(tmp_path, FOREIGN_HELPER)) == 1


def test_only_note_prefixed_helpers_are_trusted(tmp_path):
    """Deliberately narrow: the _note_* name is the opt-in marker, so an
    arbitrary same-module call cannot silently satisfy the gate."""
    assert len(_issues(tmp_path, NON_NOTE_HELPER)) == 1


def test_only_watchdog_probe_files_are_scanned(tmp_path):
    assert _issues(tmp_path, DARK, name="some_other_module.py") == []


def test_syntax_error_is_skipped_not_crashed(tmp_path):
    assert _issues(tmp_path, "def broken(:\n") == []


def test_live_tree_is_clean():
    """The rule was born on a clean tree (0 dark returns, surveyed
    2026-07-29) — this pins that it STAYS clean, which is the whole point."""
    root = Path(__file__).parent.parent
    files = [str(p) for p in (root / "src" / "utils").glob("watchdog_probe*.py")]
    assert files, "watchdog probe modules missing?"
    assert lint.check_probe_fail_dark(files) == []
