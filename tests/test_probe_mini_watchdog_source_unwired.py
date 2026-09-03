"""probe_mini_watchdog_source_unwired — the silent half of a reader/writer pair.

WHY THIS EXISTS (2026-09-03): ``MINI_DUDEAI_ENABLE_WATCHDOG=0`` switches off
mini's only signal_class feed. On a box with no watchdog that flag is CORRECT
and required — without it the source emits ``source_error_watchdog`` every tick
and pins ``src_errors=1`` forever (declared-absent != unobservable != error).
So the flag cannot simply be removed, and the defect is the COMBINATION:
a watchdog gets installed on such a box later and nobody flips the flag back.

lehua's env file documents exactly that transition in prose — "FLIP TO 1 the
day a watchdog is installed here, or the dead-watchdog rule stays blind" — and
nothing enforced it. Meanwhile mini keeps ticking at ``src_errors=0`` and reads
GREEN, indistinguishable from a genuinely healthy box.

The LOUD polarity (flag on, no watchdog) was already covered by src_errors.
This closes the polarity that never complains — both directions, per the
frozen-green lesson.
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes_mini import (  # noqa: E402
    probe_mini_watchdog_source_unwired,
)

CLS = "mini_watchdog_source_unwired"
FLEET_CMD = "/usr/bin/python3\0-m\0mini_dudeai\0--preset\0meshforge_fleet\0--interval\30"
CLAW_CMD = "/usr/bin/python3\0-m\0mini_dudeai\0--preset\0standalone\0--interval\0 30"


@pytest.fixture
def dispositions():
    reset_dispositions()
    return collect_dispositions


def _proc(tmp_path, pid="1234", cmdline=FLEET_CMD, env=None, environ=True):
    root = tmp_path / "proc"
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes(cmdline.encode())
    if environ:
        blob = "\0".join(f"{k}={v}" for k, v in (env or {}).items())
        (d / "environ").write_bytes(blob.encode())
    (root / "not-a-pid").mkdir(exist_ok=True)   # non-numeric entries ignored
    return str(root)


# ── the contradiction ─────────────────────────────────────────────────
def test_watchdog_running_while_mini_feed_is_off_is_degraded(tmp_path, dispositions):
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    sig = probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok")
    assert sig is not None
    assert sig.cls == CLS and sig.severity == "degraded"
    assert "MINI_DUDEAI_ENABLE_WATCHDOG" in sig.detail
    assert "RESTART" in sig.detail       # editing the file alone is not enough


def test_flag_on_is_clean(tmp_path, dispositions):
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "1"})
    assert probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_flag_unset_is_clean_because_the_preset_default_is_on(tmp_path, dispositions):
    proc = _proc(tmp_path, env={"PYTHONPATH": "/opt/meshforge/src"})
    assert probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_reads_the_live_environ_not_the_config_file(tmp_path, dispositions):
    """THE consumer-of-record property (calibrated_claims #7). An env file
    edited to 1 without restarting the unit leaves the RUNNING process on 0 —
    the state this probe must still catch. Config files are the wiring; the
    live environ is what actually decides."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".config").mkdir()
    # A config file that says the feed is ON ...
    cfg = home / "mini_dudeai.env"
    cfg.write_text("MINI_DUDEAI_ENABLE_WATCHDOG=1\n")
    # ... while the process actually running was started with it OFF.
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    sig = probe_mini_watchdog_source_unwired(proc_root=proc, unit_status="ok")
    assert sig is not None, "read the config file instead of the live process"


def test_claw_instance_is_not_a_consumer(tmp_path, dispositions):
    """A --preset standalone claw mini reads no watchdog document, so it is not
    party to this contract and must not be matched."""
    proc = _proc(tmp_path, cmdline=CLAW_CMD,
                 env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    sig = probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="ok", mini_home=str(tmp_path / "nohome"))
    assert sig is None
    assert dispositions()[CLS]["disp"] == "inert"


# ── no producer -> nothing to contradict ──────────────────────────────
def test_no_watchdog_unit_here_is_inert(tmp_path, dispositions):
    """lehua's real shape: the flag is correct there, and this must stay quiet."""
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    assert probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="absent") is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_watchdog_installed_but_stopped_is_inert(tmp_path, dispositions):
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    assert probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="down") is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_unresolvable_unit_state_is_indeterminate_not_inert(tmp_path, dispositions):
    """systemctl unreadable is unobservable, never 'absent by design'."""
    proc = _proc(tmp_path, env={"MINI_DUDEAI_ENABLE_WATCHDOG": "0"})
    assert probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="unknown") is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


# ── reader missing -> honest tri-state ────────────────────────────────
def test_mini_seeded_but_not_running_is_indeterminate(tmp_path, dispositions):
    home = tmp_path / "home"
    home.mkdir()
    (home / "mini_dudeai_rules.json").write_text(json.dumps({"rules": []}))
    empty = tmp_path / "emptyproc"
    empty.mkdir()
    assert probe_mini_watchdog_source_unwired(
        proc_root=str(empty), unit_status="ok", mini_home=str(home)) is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


def test_no_mini_at_all_is_inert(tmp_path, dispositions):
    empty = tmp_path / "emptyproc"
    empty.mkdir()
    assert probe_mini_watchdog_source_unwired(
        proc_root=str(empty), unit_status="ok",
        mini_home=str(tmp_path / "nothing")) is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_unreadable_environ_is_indeterminate_not_clean(tmp_path, dispositions):
    """A process we cannot read is unobservable — it must never fold into the
    affirmative 'flag is on' branch (honest_failure_modes #1)."""
    proc = _proc(tmp_path, environ=False)
    assert probe_mini_watchdog_source_unwired(
        proc_root=proc, unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


def test_unlistable_proc_is_indeterminate(tmp_path, dispositions):
    assert probe_mini_watchdog_source_unwired(
        proc_root=str(tmp_path / "does-not-exist"), unit_status="ok") is None
    assert dispositions()[CLS]["disp"] == "indeterminate"
