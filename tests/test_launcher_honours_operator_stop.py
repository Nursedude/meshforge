"""A UI launch must not change WHICH services run.

Deferred row ``launcher-autostart-overrides-operator-stop`` (2026-10-31, moc
BIC-2 kill run): the operator stopped meshtasticd at 20:15:23, opened the TUI
at 20:34:27, and ``start_noc_services()`` -> ``ServiceOrchestrator.startup()``
STARTED it again at 20:34:43 — no prompt, no line — cutting the kill window
to 19m20s.

The boundary is systemd's own record, MEASURED 2026-11-19 on the manager box:
a unit that was up this boot and is now ``inactive`` keeps a non-zero
``ActiveEnterTimestampMonotonic`` (cloud-init-main / hostnamed /
pk-debconf-helper); a unit that never came up this boot reads ``0``
(alsa-state, apparmor). So:

* stopped THIS boot      -> HELD (an operator stop is honoured, said aloud)
* never started this boot -> started (the launcher's bring-up job, unchanged)
* failed                 -> started (keeps the hardware-recovery dialog path)
* unobservable           -> HELD (we cannot tell it was not a stop; refuse
                            by default, say so — honest_failure_modes #2)

Only the UI launch holds; ``meshforge-noc --start`` is an explicit start and
keeps ``startup()``'s default.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from utils import service_check
from utils.service_check import unit_boot_history


def _show(active_state, active_enter, load_state="loaded", result="success",
          inactive_wall="Thu 2026-11-19 20:15:23 HST"):
    out = (
        f"LoadState={load_state}\n"
        f"ActiveState={active_state}\n"
        f"Result={result}\n"
        f"ActiveEnterTimestampMonotonic={active_enter}\n"
        f"InactiveEnterTimestamp={inactive_wall}\n"
    )
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=out, stderr="")


# ── the one classifier: the full input space, not one spec ────────────────

@pytest.mark.parametrize("active_state,active_enter,load_state,expected", [
    ("active", "21568451", "loaded", "active"),
    ("activating", "0", "loaded", "active"),
    ("reloading", "21568451", "loaded", "active"),
    ("deactivating", "21568451", "loaded", "stopping"),   # review #5
    ("inactive", "340486206733", "loaded", "stopped_this_boot"),
    ("inactive", "0", "loaded", "not_started_this_boot"),
    ("failed", "744163299394", "loaded", "failed"),
    ("failed", "0", "loaded", "failed"),
    ("inactive", "0", "not-found", "absent"),
    ("maintenance", "5", "loaded", "unknown"),       # unknown state word
    ("inactive", "garbage", "loaded", "unknown"),    # unparseable stamp
    ("inactive", "", "loaded", "unknown"),           # missing stamp
])
def test_classifier_state_table(active_state, active_enter, load_state, expected):
    with patch.object(service_check.subprocess, "run",
                      return_value=_show(active_state, active_enter, load_state)):
        verdict, _since = unit_boot_history("meshtasticd")
    assert verdict == expected


def test_classifier_timeout_is_unknown_not_inactive():
    with patch.object(service_check.subprocess, "run",
                      side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=5)):
        assert unit_boot_history("meshtasticd")[0] == "unknown"


def test_classifier_nonzero_exit_is_unknown():
    bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="bus")
    with patch.object(service_check.subprocess, "run", return_value=bad):
        assert unit_boot_history("meshtasticd")[0] == "unknown"


def test_classifier_reports_since_for_a_stop():
    with patch.object(service_check.subprocess, "run",
                      return_value=_show("inactive", "340486206733")):
        verdict, since = unit_boot_history("meshtasticd")
    assert verdict == "stopped_this_boot"
    assert since == "Thu 2026-11-19 20:15:23 HST"


def test_classifier_is_read_only_and_never_sudo():
    with patch.object(service_check.subprocess, "run",
                      return_value=_show("active", "1")) as run:
        unit_boot_history("meshtasticd")
    argv = run.call_args.args[0]
    assert argv[0] == "systemctl" and argv[1] == "show"
    assert "sudo" not in argv
    assert run.call_args.kwargs.get("timeout")


# ── the orchestrator: hold only when asked ────────────────────────────────

@pytest.fixture
def orch():
    with patch('core.orchestrator.Path.exists', return_value=False), \
         patch('core.orchestrator._detect_radio_hardware', return_value={
             'has_spi': False, 'has_usb': True, 'spi_devices': [],
             'usb_devices': ['/dev/ttyUSB0'], 'usb_device': '/dev/ttyUSB0',
             'hardware_type': 'usb'}):
        from core.orchestrator import ServiceOrchestrator
        o = ServiceOrchestrator(config_path=Path('/nonexistent'))
    o._preflight_check = MagicMock(return_value=[])
    return o


def _history(table):
    return lambda unit, **kw: table[unit]


def test_ui_launch_holds_an_operator_stop(orch):
    """The 10-31 red: meshtasticd stopped by hand -> the launch must not start it."""
    table = {"meshtasticd": ("stopped_this_boot", "Sat 2026-10-31 20:15:23 HST"),
             "rnsd": ("active", None)}
    ready = []
    orch.on('all_ready', lambda: ready.append(1))
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', return_value=True) as start, \
         patch.object(orch, 'is_running', side_effect=lambda s: s == 'rnsd'):
        ok = orch.startup(hold_operator_stops=True)
    started = [c.args[0] for c in start.call_args_list]
    assert 'meshtasticd' not in started
    assert ok is True                       # a hold is not a failure ...
    assert ready == []                      # ... and not "all ready" (review #3)
    assert 'inactive since Sat 2026-10-31 20:15:23 HST' in orch.held_services['meshtasticd']
    # rnsd depends on meshtasticd but is RUNNING: left exactly as it is —
    # neither held nor told to start (review #2)
    assert 'rnsd' not in started
    assert 'rnsd' not in orch.held_services
    assert 'rnsd' not in orch.held_by_dependency


def test_dependency_of_a_held_unit_that_is_down_waits_and_keeps_its_state(orch):
    """Review #2/#6: a failed rnsd behind a held meshtasticd is reported as
    waiting on its parent — never given a bare 'start it' line."""
    table = {"meshtasticd": ("stopped_this_boot", None), "rnsd": ("failed", None)}
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', return_value=True) as start, \
         patch.object(orch, 'is_running', return_value=False):
        assert orch.startup(hold_operator_stops=True) is True
    assert start.call_args_list == []
    assert orch.held_by_dependency == {'rnsd': 'meshtasticd'}
    assert 'rnsd' not in orch.held_services


def test_a_stop_in_progress_is_held(orch):
    """Review #5: deactivating is not up."""
    table = {"meshtasticd": ("stopping", None), "rnsd": ("active", None)}
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', return_value=True) as start, \
         patch.object(orch, 'is_running', return_value=False):
        orch.startup(hold_operator_stops=True)
    assert 'meshtasticd' not in [c.args[0] for c in start.call_args_list]
    assert orch.held_services['meshtasticd'] == 'stop in progress'


def test_unknown_gets_one_slower_retry_before_holding(orch):
    """Review #4: one slow systemctl answer must not hold the boot bring-up."""
    from core.startup_hold import BOOT_HISTORY_RETRY_TIMEOUT
    calls = []

    def hist(unit, **kw):
        calls.append((unit, kw.get('timeout')))
        if unit == 'meshtasticd' and kw.get('timeout') is None:
            return ('unknown', None)
        return ('not_started_this_boot', None)
    running = set()
    with patch('core.orchestrator.unit_boot_history', side_effect=hist), \
         patch.object(orch, 'start_service',
                      side_effect=lambda n, *a, **k: running.add(n) or True) as start, \
         patch.object(orch, 'is_running', side_effect=lambda s: s in running):
        orch.startup(hold_operator_stops=True)
    assert ('meshtasticd', BOOT_HISTORY_RETRY_TIMEOUT) in calls
    assert [c.args[0] for c in start.call_args_list] == ['meshtasticd', 'rnsd']
    assert orch.held_services == {}


def test_every_boot_history_verdict_has_exactly_one_policy():
    """Review #7 / honest_failure_modes #7: a new verdict fails HERE first."""
    from utils.service_check import BOOT_HISTORY_VERDICTS
    from core.startup_hold import UI_HOLD_VERDICTS, UI_START_VERDICTS
    assert not (UI_HOLD_VERDICTS & UI_START_VERDICTS)
    assert UI_HOLD_VERDICTS | UI_START_VERDICTS == set(BOOT_HISTORY_VERDICTS)


@pytest.mark.parametrize("verdict", [
    "active", "stopping", "stopped_this_boot", "not_started_this_boot",
    "failed", "absent", "unknown"])
def test_each_verdict_walks_to_its_policy(orch, verdict):
    from core.startup_hold import UI_HOLD_VERDICTS
    table = {"meshtasticd": (verdict, None), "rnsd": ("active", None)}
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', return_value=True) as start, \
         patch.object(orch, 'is_running', return_value=True):
        orch.startup(hold_operator_stops=True)
    started = 'meshtasticd' in [c.args[0] for c in start.call_args_list]
    assert started is (verdict not in UI_HOLD_VERDICTS)
    assert ('meshtasticd' in orch.held_services) is (verdict in UI_HOLD_VERDICTS)


def test_an_unhandled_verdict_is_held_loudly(orch):
    table = {"meshtasticd": ("brand_new", None), "rnsd": ("active", None)}
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', return_value=True) as start, \
         patch.object(orch, 'is_running', return_value=True):
        orch.startup(hold_operator_stops=True)
    assert 'meshtasticd' not in [c.args[0] for c in start.call_args_list]
    assert 'brand_new' in orch.held_services['meshtasticd']


def test_ui_launch_still_brings_up_a_never_started_unit(orch):
    table = {"meshtasticd": ("not_started_this_boot", None),
             "rnsd": ("not_started_this_boot", None)}
    running = set()

    def _start(name, *a, **k):
        running.add(name)
        return True
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', side_effect=_start) as start, \
         patch.object(orch, 'is_running', side_effect=lambda s: s in running):
        assert orch.startup(hold_operator_stops=True) is True
    assert [c.args[0] for c in start.call_args_list] == ['meshtasticd', 'rnsd']
    assert orch.held_services == {} and orch.held_by_dependency == {}


def test_ui_launch_still_starts_a_failed_unit(orch):
    """Failed keeps the hardware-config recovery dialog reachable."""
    table = {"meshtasticd": ("failed", None), "rnsd": ("active", None)}
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', return_value=True) as start, \
         patch.object(orch, 'is_running', return_value=True):
        orch.startup(hold_operator_stops=True)
    assert 'meshtasticd' in [c.args[0] for c in start.call_args_list]


def test_ui_launch_holds_when_history_is_unobservable(orch):
    table = {"meshtasticd": ("unknown", None), "rnsd": ("active", None)}
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', return_value=True) as start, \
         patch.object(orch, 'is_running', side_effect=lambda s: s == 'rnsd'):
        assert orch.startup(hold_operator_stops=True) is True
    assert 'meshtasticd' not in [c.args[0] for c in start.call_args_list]
    assert 'unobservable' in orch.held_services['meshtasticd']


def test_running_unit_is_never_held(orch):
    table = {"meshtasticd": ("active", None), "rnsd": ("active", None)}
    with patch('core.orchestrator.unit_boot_history', side_effect=_history(table)), \
         patch.object(orch, 'start_service', return_value=True), \
         patch.object(orch, 'is_running', return_value=True):
        orch.startup(hold_operator_stops=True)
    assert orch.held_services == {}


def test_explicit_start_keeps_default_behaviour(orch):
    """`meshforge-noc --start` is the operator ASKING: no history read, start all."""
    with patch('core.orchestrator.unit_boot_history') as hist, \
         patch.object(orch, 'start_service', return_value=True) as start, \
         patch.object(orch, 'is_running', return_value=True):
        orch.startup()
    hist.assert_not_called()
    assert [c.args[0] for c in start.call_args_list] == ['meshtasticd', 'rnsd']


# ── the launcher: asks for the hold, and SAYS it ──────────────────────────

def _launch(fake, capsys):
    import launcher
    fake.get_all_status.return_value = {}
    fake.startup.return_value = True
    with patch.object(launcher, '_HAS_ORCHESTRATOR', True), \
         patch.object(launcher, 'ServiceOrchestrator', return_value=fake), \
         patch.object(launcher.Path, 'exists', return_value=True), \
         patch('builtins.open', mock_open(read_data="noc:\n  mode: local\n")):
        launcher.start_noc_services()
    return capsys.readouterr().out


def test_launcher_asks_for_hold_and_prints_it(capsys):
    fake = MagicMock()
    fake.held_services = {'meshtasticd': 'inactive since Sat 2026-10-31 20:15:23 HST this boot'}
    fake.held_by_dependency = {'rnsd': 'meshtasticd'}
    out = _launch(fake, capsys)
    fake.startup.assert_called_once_with(hold_operator_stops=True)
    assert 'meshtasticd held' in out
    assert 'sudo systemctl start meshtasticd' in out     # the knob, as a literal
    assert 'rnsd not started - waits on held meshtasticd' in out
    assert 'systemctl start rnsd' not in out             # review #2
    assert 'NOC services ready' not in out               # review #3: not green
    assert '2 held, not started' in out


def test_launcher_says_ready_only_when_nothing_is_held(capsys):
    fake = MagicMock()
    fake.held_services = {}
    fake.held_by_dependency = {}
    out = _launch(fake, capsys)
    assert 'NOC services ready' in out and 'held' not in out
