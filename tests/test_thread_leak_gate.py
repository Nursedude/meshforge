"""THR-1 (2026-11-18): the non-daemon thread-leak gate in tests/conftest.py.

A full-suite tracker run found 30 non-daemon threads outliving their tests
(`statusbar-weather` from StatusBars never `cleanup()`-ed, `eventbus` workers),
27 still alive at session end — waited for at interpreter exit. The products
had the stop API; the tests did not call it. ``_no_leaked_nondaemon_threads``
fails such a test. These tests PLANT each shape: a gate that never failed is not
evidence.
"""
from __future__ import annotations

import re
import threading
import time

import pytest

from tests import conftest


def _parked_thread(daemon):
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=daemon, name="planted")
    t.start()
    return t, release


def test_a_new_nondaemon_thread_still_running_is_reported():
    before = set(threading.enumerate())
    t, release = _parked_thread(daemon=False)
    try:
        assert conftest._leaked_nondaemon_threads(before, deadline_s=0.05) == [t]
    finally:
        release.set()
        t.join(2)


def test_a_daemon_thread_and_a_finishing_thread_are_not_reported():
    before = set(threading.enumerate())
    d, d_release = _parked_thread(daemon=True)
    quick = threading.Thread(target=time.sleep, args=(0.01,), daemon=False)
    quick.start()
    try:
        assert conftest._leaked_nondaemon_threads(before, deadline_s=1.0) == []
    finally:
        d_release.set()
        d.join(2)


def test_matching_is_by_thread_object_not_ident():
    """Review #1: Linux reuses thread idents. A thread that existed before (same
    ident or not) is excluded by OBJECT; a new one is caught even if a dead
    thread once had its ident."""
    before = set(threading.enumerate())
    t, release = _parked_thread(daemon=False)
    try:
        assert t not in before
        assert t in conftest._leaked_nondaemon_threads(before, deadline_s=0.05)
        before_with_t = before | {t}
        assert conftest._leaked_nondaemon_threads(before_with_t, deadline_s=0.05) == []
    finally:
        release.set()
        t.join(2)


def test_the_global_event_bus_workers_are_exempt_by_identity():
    """The singleton's pool is process-lifetime by design (never shut — idle, its workers exit at interpreter shutdown)."""
    import importlib
    bus = importlib.import_module("utils.event_bus").event_bus
    ran = threading.Event()

    def _probe(_event):
        ran.set()
    bus.subscribe("thr1-gate-probe", _probe)     # emit() submits only to subscribers
    try:
        bus.emit("thr1-gate-probe", {})
        assert ran.wait(2), "the global bus never ran the probe"
    finally:
        bus.unsubscribe("thr1-gate-probe", _probe)
    workers = set(bus._executor._threads)
    assert workers, "expected the global bus to have started a worker"
    assert workers <= conftest._process_lifetime_threads()


def test_one_shared_deadline_not_one_per_thread():
    """Review #8: N stuck threads cost ONE deadline, not N of them."""
    before = set(threading.enumerate())
    parked = [_parked_thread(daemon=False) for _ in range(5)]
    try:
        t0 = time.monotonic()
        leaked = conftest._leaked_nondaemon_threads(before, deadline_s=0.2)
        elapsed = time.monotonic() - t0
        assert len(leaked) == 5
        assert elapsed < 0.6, elapsed
    finally:
        for t, release in parked:
            release.set()
            t.join(2)


def test_the_status_bar_wrapper_keeps_the_real_source_visible():
    """Fable r2 F1: the autouse wrapper replaced StatusBar.__init__, so
    test_tui_dead_code_purge's getsource() guard read the WRAPPER and could never
    fail. functools.wraps keeps the real source (and signature) reachable."""
    import inspect

    from status_bar import StatusBar
    assert hasattr(StatusBar.__init__, "__wrapped__"), "wrapper not active — test would pin nothing"
    src = inspect.getsource(StatusBar.__init__)
    assert "_weather_executor" in src and "_orig(self" not in src


def test_matching_survives_ident_reuse():
    """Fable r2 #9: a thread alive in `before` exits; a NEW thread reusing its
    ident must still be reported (an ident-matching gate would skip it)."""
    old_release = threading.Event()
    old = threading.Thread(target=old_release.wait, daemon=False)
    old.start()
    before = set(threading.enumerate())
    old_ident = old.ident
    old_release.set()
    old.join(2)
    reused, extra = None, []
    for _ in range(64):                       # glibc reuses a freed stack quickly
        t, rel = _parked_thread(daemon=False)
        if t.ident == old_ident:
            reused = (t, rel)
            break
        extra.append((t, rel))
    try:
        if reused is None:
            import pytest
            pytest.skip("no ident reuse observed in 64 threads on this platform")
        assert reused[0] in conftest._leaked_nondaemon_threads(before, deadline_s=0.05)
    finally:
        for t, rel in extra + ([reused] if reused else []):
            rel.set()
            t.join(2)


def test_a_timer_is_named_by_its_function():
    """Fable r2 low: a threading.Timer has no _target; the message said '(?)'."""
    tm = threading.Timer(30, time.sleep, args=(0,))
    assert conftest._thread_fn_name(tm) == "sleep"


_PLANT = '''
import threading
_keep = []

def test_leaks_a_nondaemon_thread():
    ev = threading.Event()
    _keep.append(ev)
    threading.Thread(target=ev.wait, daemon=False, name="planted-leak").start()

def test_clean():
    t = threading.Thread(target=lambda: None, daemon=False)
    t.start()
    t.join()

def teardown_module(module):
    for ev in _keep:
        ev.set()
'''


def _child_env(tmp_path, **extra):
    """Env for a child pytest: this repo on PYTHONPATH and its TMPDIR INSIDE
    ``tmp_path``. Plants hang by design and the THR-1 exit watchdog ends them with
    os._exit, which skips pytest's atexit lock removal — with the real TMPDIR each
    stranded a top-level basetemp lock (11 per run of this file, measured
    2026-11-20). Inside tmp_path, whatever a child strands goes with the parent."""
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child_tmp = tmp_path / "childtmp"
    child_tmp.mkdir(exist_ok=True)
    return dict(os.environ, PYTHONPATH=os.pathsep.join([repo, os.path.join(repo, "src")]),
                TMPDIR=str(child_tmp), **extra)


def test_the_autouse_gate_fails_a_real_leaking_test_end_to_end(tmp_path):
    """Fable r2 F3: the helper was tested, the FIXTURE's wiring was not (mutant
    `leaked = []` survived). Run pytest on planted tests with this repo's
    conftest loaded as a plugin: the leak must ERROR, the clean test must pass."""
    import os
    import subprocess
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plant = tmp_path / "test_planted_thr1.py"
    plant.write_text(_PLANT)
    env = _child_env(tmp_path)
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider",
         "-q", "--rootdir", str(tmp_path), str(plant)],
        cwd=repo, env=env, capture_output=True, text=True, timeout=180)
    out = p.stdout + p.stderr
    assert "THR-1: test left non-daemon thread(s) running: planted-leak" in out, out[-2000:]
    # the leaking test's CALL passes; its TEARDOWN errors — pytest counts both
    assert re.search(r"2 passed.*1 error", out), out[-2000:]   # L1: warnings may sit between
    assert "ERROR at teardown of test_leaks_a_nondaemon_thread" in out, out[-2000:]
    assert "test_clean" not in out.split("short test summary info")[-1], out[-2000:]


_WEDGE_PLANT = '''
import threading
from utils.event_bus import event_bus

_never = threading.Event()

def never_returns_cb(_event):
    _never.wait()                       # a subscriber that never returns

def test_leaves_a_wedged_callback_on_the_global_bus():
    event_bus.subscribe("thr1-wedge", never_returns_cb)
    event_bus.emit("thr1-wedge", {})
    import time
    time.sleep(0.2)                     # let the worker pick it up

def test_clean_after():
    assert True
'''


def test_a_wedged_global_bus_callback_fails_its_test_and_never_hangs_exit(tmp_path):
    """Operator 11-18 ("we need to fix this — we are better than this"), Fable r2
    F2: the global bus's workers are exempt from the leak gate, so a callback that
    never returns passed its test and then blocked interpreter exit forever. Now
    the test that left it FAILS naming the callback, and the process ENDS with a
    failing status instead of hanging."""
    import os
    import subprocess
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plant = tmp_path / "test_planted_wedge.py"
    plant.write_text(_WEDGE_PLANT)
    env = _child_env(tmp_path)
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider",
             "-q", "--rootdir", str(tmp_path), str(plant)],
            cwd=repo, env=env, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise AssertionError("the test process HUNG at exit on a wedged global-bus callback: "
                             + str((e.stdout or b"")[-1500:])) from None
    out = p.stdout + p.stderr
    assert "THR-1: callback(s) on the GLOBAL event_bus still running" in out, out[-2000:]
    assert "teardown: never_returns_cb" in out, out[-2000:]   # r4 F6: not a substring of the nodeid
    assert "never_returns_cb  (left by " in out, out[-2000:]   # r5 M8/M13: attribution recorded
    assert "ending the test process" in out, out[-2000:]
    assert p.returncode != 0, out[-2000:]
    assert re.search(r"2 passed.*1 error", out), out[-2000:]   # teardown error: its call passed


def _run_plant(tmp_path, code, name):
    """Run ``code`` as a test file in a child pytest with this repo's conftest."""
    import os
    import subprocess
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plant = tmp_path / f"test_{name}.py"
    plant.write_text(code)
    env = _child_env(tmp_path,
                     MF_EXIT_WATCHDOG_S="3")    # keeps a hang plant well inside CI's 30 s cap
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider",
             "-q", "--rootdir", str(tmp_path), str(plant)],
            cwd=repo, env=env, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired as e:
        raise AssertionError(f"{name}: the test process HUNG at exit: "
                             + str((e.stdout or b"")[-1200:])) from None
    return p.returncode, p.stdout + p.stderr


_WEDGE_OUTSIDE_A_TEST_WINDOW = {
    # Fable r4 F1: each of these HUNG exit with every test passing (rc=137 under a kill).
    "module_fixture": '''
import threading, pytest
from utils.event_bus import event_bus
_never = threading.Event()
def stuck_cb(_e):
    _never.wait()
@pytest.fixture(scope="module", autouse=True)
def _emit_once():
    event_bus.subscribe("thr1-mod", stuck_cb)
    event_bus.emit("thr1-mod", {})
    yield
def test_a():
    assert True
''',
    "lazy_src_alias": '''
import threading, time
_never = threading.Event()
def stuck_cb(_e):
    _never.wait()
def test_imports_the_src_alias_inside_the_test():
    from src.utils.event_bus import event_bus
    event_bus.subscribe("thr1-lazy", stuck_cb)
    event_bus.emit("thr1-lazy", {})
    time.sleep(0.2)
''',
    "import_time": '''
import threading
from utils.event_bus import event_bus
_never = threading.Event()
def stuck_cb(_e):
    _never.wait()
event_bus.subscribe("thr1-imp", stuck_cb)
event_bus.emit("thr1-imp", {})
def test_a():
    assert True
''',
    # Fable r5 F1: the session end trusted `event_bus._executor` — a wedge on an
    # executor that was then swapped (p8) or swapped inside a module fixture (p11,
    # SILENT: "1 passed", no message) still hung. Now every non-daemon thread counts.
    "wedge_then_swap_executor": '''
import threading, time
from concurrent.futures import ThreadPoolExecutor
from utils.event_bus import event_bus
_never = threading.Event()
def stuck_cb(_e):
    _never.wait()
def test_wedge_then_swap():
    event_bus.subscribe("thr1-swap", stuck_cb)
    event_bus.emit("thr1-swap", {})
    time.sleep(0.2)
    event_bus._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="eventbus")
''',
    "module_fixture_wedge_then_swap": '''
import threading, time, pytest
from concurrent.futures import ThreadPoolExecutor
from utils.event_bus import event_bus
_never = threading.Event()
def stuck_cb(_e):
    _never.wait()
@pytest.fixture(scope="module", autouse=True)
def _wedge_and_swap():
    event_bus.subscribe("thr1-mswap", stuck_cb)
    event_bus.emit("thr1-mswap", {})
    time.sleep(0.2)
    event_bus._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="eventbus")
    yield
def test_a():
    assert True
''',
    "module_fixture_plain_thread": '''
import threading, pytest
_never = threading.Event()
@pytest.fixture(scope="module", autouse=True)
def _start_forever():
    threading.Thread(target=_never.wait, daemon=False, name="forever").start()
    yield
def test_a():
    assert True
''',
}


def test_the_exact_exit_status_survives_the_forced_exit(tmp_path):
    """r5 M6: the recorded pytest status is what the process exits with —
    a wedge plus a KeyboardInterrupt must exit 2 (INTERRUPTED), not a flat 1."""
    code = '''
import threading, time
from utils.event_bus import event_bus
_never = threading.Event()
def stuck_cb(_e):
    _never.wait()
def test_wedge_then_interrupt():
    event_bus.subscribe("thr1-kbi", stuck_cb)
    event_bus.emit("thr1-kbi", {})
    time.sleep(0.2)
    raise KeyboardInterrupt
'''
    rc, out = _run_plant(tmp_path, code, "wedge_kbi")
    assert "THR-1: ending the test process" in out, out[-1500:]
    assert rc == 2, (rc, out[-1500:])


@pytest.mark.parametrize("name", sorted(_WEDGE_OUTSIDE_A_TEST_WINDOW))
def test_a_wedge_submitted_outside_any_test_window_still_ends_the_process(tmp_path, name):
    """r4 F1 / r5 F1: the session-end check measures every alive NON-DAEMON thread,
    so a stuck thread no test window tracked still cannot hang exit. One plant per
    test: each child run takes ~3-4 s and CI caps a test at 30 s."""
    rc, out = _run_plant(tmp_path, _WEDGE_OUTSIDE_A_TEST_WINDOW[name], f"wedge_{name}")
    assert "THR-1: ending the test process" in out, (name, out[-1500:])
    assert rc != 0, (name, out[-1500:])


def test_a_finishing_callback_that_emits_a_child_is_not_a_false_failure(tmp_path):
    """r4 F2: wait() on a snapshot judged the freshly emitted child 'still running'."""
    code = '''
import time
from utils.event_bus import event_bus
def child_cb(_e):
    time.sleep(0.05)
def parent_cb(_e):
    time.sleep(0.3)
    event_bus.emit("thr1-child", {})
def test_chain():
    event_bus.subscribe("thr1-child", child_cb)
    event_bus.subscribe("thr1-parent", parent_cb)
    event_bus.emit("thr1-parent", {})
'''
    rc, out = _run_plant(tmp_path, code, "chained_ok")
    assert rc == 0, out[-1500:]
    assert "THR-1" not in out, out[-1500:]


# Fable r6: a census of threads can never be complete — a thread started DURING the
# grace (re-arming Timer, a callback that spawns one), a blocking atexit handler,
# a non-daemon multiprocessing child. The guarantee therefore measures the OUTCOME:
# an exit watchdog (a daemon thread armed at the very end of pytest_unconfigure)
# ends a process that has not exited within MF_EXIT_WATCHDOG_S. These plants hung
# (rc=137) before it.
_HANGS_A_CENSUS_CANNOT_SEE = {
    "rearming_timer": '''
import threading
def tick():
    t = threading.Timer(0.3, tick)
    t.start()
def test_a():
    tick()
''',
    "bus_callback_spawns_a_thread": '''
import threading, time
from utils.event_bus import event_bus
_never = threading.Event()
def spawner(_e):
    time.sleep(1)
    threading.Thread(target=_never.wait, daemon=False, name="late-child").start()
def test_a():
    event_bus.subscribe("thr1-spawn", spawner)
    event_bus.emit("thr1-spawn", {})
''',
    "blocking_atexit": '''
import atexit, threading
atexit.register(threading.Event().wait)
def test_a():
    assert True
''',
}


@pytest.mark.parametrize("name", sorted(_HANGS_A_CENSUS_CANNOT_SEE))
def test_the_exit_watchdog_ends_a_hang_no_census_can_see(tmp_path, name):
    rc, out = _run_plant(tmp_path, _HANGS_A_CENSUS_CANNOT_SEE[name], f"census_{name}")
    assert "THR-1" in out and "exit" in out, (name, out[-1500:])
    assert rc != 0, (name, out[-1500:])


@pytest.mark.parametrize("raise_line, want_rc", [
    ("assert True", 1),                 # a passing run that hangs is a failure
    ("assert False", 1),                # a real failure keeps its status
    ("raise KeyboardInterrupt", 2),     # r7 M6: the status is the recorded one, not a flat 1
])
def test_the_watchdog_itself_fires_with_the_recorded_status_and_a_stack_dump(
        tmp_path, raise_line, want_rc):
    """r7: the census messages satisfied the old assert, so the WATCHDOG's own
    message (M3b/M6/M7) was unpinned. Only a blocking atexit reaches it."""
    code = f'''
import atexit, threading
atexit.register(threading.Event().wait)
def test_a():
    {raise_line}
'''
    rc, out = _run_plant(tmp_path, code, f"watchdog_{want_rc}_{len(raise_line)}")
    assert "did not exit 3 s after the session ended" in out, out[-1500:]   # env honoured (M3b)
    assert "Thread 0x" in out or "Current thread 0x" in out, out[-1500:]    # stacks dumped (M7)
    assert rc == want_rc, (rc, out[-1500:])


def test_settle_re_reads_until_nothing_new_appears():
    """r7 M4/M5: a single pass (or survivors from the first list) misses a thread
    started during the grace — a re-arming Timer keeps itself alive across reads."""
    stop = threading.Event()
    timers = []

    def tick():
        if not stop.is_set():
            t = threading.Timer(0.05, tick)
            timers.append(t)
            t.start()
    tick()
    try:
        alive = conftest._settle_threads(
            lambda: [t for t in timers if t.is_alive()], deadline_s=0.6)
        assert alive, "the re-arming chain must still be seen after the grace"
        assert alive[0] is not timers[0], "survivors must come from a FRESH read"
    finally:
        stop.set()
        for t in list(timers):
            t.join(1)


def test_the_thread_census_names_what_it_found(tmp_path):
    """r6 M10: the 'thread X running fn' line is the only diagnostic in a silent-pass shape."""
    code = '''
import threading, pytest
_never = threading.Event()
def parked_forever():
    _never.wait()
@pytest.fixture(scope="module", autouse=True)
def _start():
    threading.Thread(target=parked_forever, daemon=False, name="named-forever").start()
    yield
def test_a():
    assert True
'''
    rc, out = _run_plant(tmp_path, code, "census_named")
    assert "thread named-forever running parked_forever" in out, out[-1500:]
    assert rc != 0


@pytest.mark.parametrize("name, code", [
    # r6 M05: a thread finishing INSIDE the grace is not a forced exit
    ("finishes_inside_grace", '''
import threading, time, pytest
@pytest.fixture(scope="module", autouse=True)
def _start():
    threading.Thread(target=time.sleep, args=(1,), daemon=False).start()
    yield
def test_a():
    assert True
'''),
    # r6 M22: a clean run through the src.* alias bus exits 0 (its idle workers are shut)
    ("clean_src_alias_bus", '''
def test_a():
    from src.utils.event_bus import event_bus
    seen = []
    event_bus.subscribe("thr1-src-ok", seen.append)
    event_bus.emit("thr1-src-ok", {})
'''),
])
def test_clean_runs_exit_zero_with_no_thr1_message(tmp_path, name, code):
    rc, out = _run_plant(tmp_path, code, f"clean_{name}")
    assert rc == 0, (name, out[-1500:])
    assert "THR-1" not in out, (name, out[-1500:])


def test_child_pytests_keep_their_basetemp_inside_this_tests_tmp(tmp_path):
    """2026-11-20, measured: one run of THIS file left 11 dead-owner locks at the
    TOP level of the real basetemp root (2 full suites → the 22 found that
    morning). Its plants hang by design and the THR-1 exit watchdog ends them
    with os._exit — which skips pytest's atexit lock removal — and the children
    inherited the real TMPDIR. A child's basetemp must live under the parent
    test's tmp_path, so whatever it strands is removed with the parent's."""
    import glob
    _run_plant(tmp_path, "def test_ok():\n    pass\n", "basetemp_probe")
    inside = glob.glob(str(tmp_path / "childtmp" / "pytest-of-*" / "pytest-*"))
    assert inside, "the child pytest's basetemp is not under this test's tmp_path"
