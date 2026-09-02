"""Tests for the MF028 state-saver swallow guard in scripts/lint.py.

The 2026-09-02 falsifiability audit found TEN ``_save_*`` helpers across the
watchdog probe modules ending in ``except OSError: pass``. Each paired loader
read the missing file as "no baseline / streak 0", so on an unwritable state
dir (the #60 sandbox-drift class) every tick was a first sighting forever and
the detector could never fire — with nothing anywhere recording that a write
had failed. Compiled into a gate so the class stops recurring at write time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib.util

_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint_mf028", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


DARK_PASS = '''\
def _save_thing_streak(state_path, streak):
    try:
        with open(state_path, "w") as fh:
            fh.write(str(streak))
    except OSError:
        pass
'''

DARK_BARE_RETURN = '''\
def _persist_thing(state_path, doc):
    try:
        with open(state_path, "w") as fh:
            fh.write(doc)
    except OSError:
        return
'''

DARK_RETURN_NONE = '''\
def _write_thing(state_path, doc):
    try:
        with open(state_path, "w") as fh:
            fh.write(doc)
    except (OSError, ValueError):
        return None
'''

WITNESSED_CALL = '''\
def _save_thing_streak(state_path, streak):
    try:
        with open(state_path, "w") as fh:
            fh.write(str(streak))
    except OSError as e:
        note_state_write_failure(state_path, e)
'''

WITNESSED_RETURN_FALSE = '''\
def _save_thing(state_path, doc):
    try:
        with open(state_path, "w") as fh:
            fh.write(doc)
        return True
    except OSError:
        return False
'''

WITNESSED_COUNTER = '''\
def _save_thing(state_path, doc):
    try:
        with open(state_path, "w") as fh:
            fh.write(doc)
    except OSError:
        _errors[state_path] = _errors.get(state_path, 0) + 1
'''

NOT_A_SAVER = '''\
def _load_thing(state_path):
    try:
        with open(state_path) as fh:
            return fh.read()
    except OSError:
        return None
'''


def _run(tmp_path, src, name="watchdog_probes_example.py"):
    f = tmp_path / name
    f.write_text(src)
    return lint.check_state_saver_swallow([str(f)])


def test_pass_swallow_is_flagged(tmp_path):
    got = _run(tmp_path, DARK_PASS)
    assert len(got) == 1 and got[0].code == "MF028"
    assert "_save_thing_streak" in got[0].message


def test_bare_return_swallow_is_flagged(tmp_path):
    assert [i.code for i in _run(tmp_path, DARK_BARE_RETURN)] == ["MF028"]


def test_return_none_swallow_is_flagged(tmp_path):
    assert [i.code for i in _run(tmp_path, DARK_RETURN_NONE)] == ["MF028"]


def test_witness_call_passes(tmp_path):
    assert _run(tmp_path, WITNESSED_CALL) == []


def test_non_none_return_is_a_witness(tmp_path):
    assert _run(tmp_path, WITNESSED_RETURN_FALSE) == []


def test_counter_assignment_is_a_witness(tmp_path):
    assert _run(tmp_path, WITNESSED_COUNTER) == []


def test_loaders_are_out_of_scope(tmp_path):
    assert _run(tmp_path, NOT_A_SAVER) == []


def test_non_watchdog_files_are_out_of_scope(tmp_path):
    assert _run(tmp_path, DARK_PASS, name="other_module.py") == []


def test_live_tree_is_clean():
    """The gate is wired and the tree it guards passes it (re-derived, not carried)."""
    root = Path(__file__).parent.parent / "src" / "utils"
    files = [str(p) for p in sorted(root.glob("watchdog_probe*.py"))]
    assert files
    assert lint.check_state_saver_swallow(files) == []
