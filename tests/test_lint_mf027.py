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
