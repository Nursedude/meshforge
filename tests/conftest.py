"""
Pytest configuration for MeshForge test suite.

Handles CI-specific settings:
- Auto-skip hardware-dependent tests in CI
- Timeout defaults
- Fixtures for common mocks

TUI handler test infrastructure (FakeDialog, make_handler_context) lives
in ``tests/handler_test_utils.py`` — single source of truth. All handler
tests import from there. A duplicate copy used to live in this file; it
was unused (no test imported it) and was removed in the 2026-05-19
pattern-audit cleanup (Finding #9).
"""

import functools
import itertools
import os
import sys
import threading
from contextlib import ExitStack
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

# Ensure src and launcher_tui are importable for handler tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# utils.live_store_guard: this suite isolates its own stores (the session
# fixtures below), so the session-driver guard stands down — set at IMPORT so
# collection-time constructions are covered too.
os.environ["LIVE_STORE_GUARD_UNDER_PYTEST"] = "1"

# Detect CI environment
CI = os.environ.get('CI', 'false').lower() == 'true'
MESHFORGE_CI = os.environ.get('MESHFORGE_CI', 'false').lower() == 'true'

# The host fence (refusing sudo/pkill/systemctl-mutations) lives in
# tests/host_fence.py; installed in pytest_configure below.
from tests.host_fence import _FENCE_STATS, _install_sudo_fence  # noqa: E402


def pytest_unconfigure(config):
    """Remove this run's refusal LOG — never the fence dir.

    2026-11-20: the fence is one shared dir per (uid, shim content), never
    deleted: a dir removed at exit (or by a reaper) unfenced any child a test
    left running — it got the REAL sudo/pkill. A killed run now leaks nothing;
    a clean one removes only its log. (TestTempDirsAreReleased: the per-run
    fallback dir's release is deliberately NONE — see tests/host_fence.py.)"""
    log = getattr(config, "_mf_host_fence_log", None)
    if log:
        try:
            os.unlink(log)
        except OSError:
            pass                         # no refusal happened = no log was written
    _arm_exit_watchdog()                # THR-1: armed FIRST (r7 F2: a Ctrl-C in the census
    _end_if_wedged()                     # grace escaped before it was armed); then name + end fast


# ── THR-1 (2026-11-18): no test may leave a NON-DAEMON thread running ─────────
# A full-suite tracker run found 30 non-daemon threads outliving their tests
# (`statusbar-weather` ×25 from StatusBars never `cleanup()`-ed, `eventbus`
# workers from EventBus instances never `shutdown()`), 27 still alive at
# session end. A non-daemon thread is waited for at interpreter exit — GitHub
# terminated an orphan python after the CI job (BELIEVED this class). The
# product classes HAVE the stop API; the tests did not call it.

_EVENT_BUS_ALIASES = ("utils.event_bus", "src.utils.event_bus")

#: One bounded wait for the whole teardown (review 11-18 #2/#5/#8): never an
#: unbounded join — a wedged worker must not turn teardown into the hang THR-1
#: removes — and never a fresh grace per thread (that cost scales with N).
_THREAD_JOIN_DEADLINE_S = 2.0


def _join_all(threads, deadline_s):
    """Join ``threads`` against ONE shared monotonic deadline; return survivors."""
    import time
    end = time.monotonic() + deadline_s
    for t in threads:
        left = end - time.monotonic()
        if left <= 0:
            break
        t.join(left)
    return [t for t in threads if t.is_alive()]


def _settle_threads(select, deadline_s):
    """Join what ``select()`` returns, RE-READING it until it is empty or ONE shared
    deadline passes; survivors come from a FRESH ``select()`` (Fable r6: a single
    snapshot missed threads started during the grace — a re-arming Timer, a
    callback that spawns one — the third pointer-for-the-thing defect)."""
    import time
    end = time.monotonic() + deadline_s
    while True:
        cur = [t for t in select() if t.is_alive()]
        left = end - time.monotonic()
        if not cur or left <= 0:
            return cur
        for t in cur:
            left = end - time.monotonic()
            if left <= 0:
                break
            t.join(min(left, 0.25))


#: The exit watchdog's grace (seconds after pytest_unconfigure finishes). Long
#: enough for coverage / junit / atexit to finish on a slow Pi; short enough that
#: a CI hang ends in seconds, not at the job timeout. Tests set it low.
_EXIT_WATCHDOG_S = float(os.environ.get("MF_EXIT_WATCHDOG_S", "15"))


def _arm_exit_watchdog():
    """The GUARANTEE for the exit phases that matter here (Fable r6): a thread
    census can never be complete — a thread started during the grace, a blocking
    atexit handler, a non-daemon multiprocessing child, a ``-p`` plugin's
    unconfigure. So measure the OUTCOME: a DAEMON thread (daemon threads keep
    running while interpreter shutdown waits on non-daemon threads, atexit
    handlers and children — VERIFIED on 3.13 by Fable r7) that, if this process
    has not exited ``_EXIT_WATCHDOG_S`` after this hook, dumps every thread's
    stack and ends it with the recorded failing status. A clean run exits first.

    NOT covered (Fable r7, VERIFIED by plants; none occurs in this suite today):
    a blocking ``__del__`` in module teardown (after daemon threads stop); a
    plugin registered inside ``pytest_configure`` or a blocking
    ``pytest_sessionfinish`` (they run before this hook arms it); a C call
    holding the GIL; and ``os._exit`` orphans a non-daemon multiprocessing child,
    which can hold a ``| tee`` pipe open. The CI job timeout is the backstop."""
    def _watch():
        import faulthandler
        import time
        time.sleep(_EXIT_WATCHDOG_S)
        try:
            sys.stdout.flush()
            sys.stderr.write(f"\nTHR-1: the test process did not exit {_EXIT_WATCHDOG_S:g} s "
                             "after the session ended — something blocks interpreter exit "
                             "(a thread, an atexit handler, a child process). Stacks:\n")
            sys.stderr.flush()
            faulthandler.dump_traceback(all_threads=True)
        finally:
            os._exit(_SESSION_EXITSTATUS[0] or 1)
    threading.Thread(target=_watch, daemon=True, name="mf-exit-watchdog").start()


def _process_lifetime_threads():
    """Workers of the GLOBAL ``event_bus`` singleton (both import aliases):
    process-lifetime by design, bounded at 4. Exempt by IDENTITY — the
    executor's own Thread objects, never names or idents (review #1: Linux
    reuses thread idents). Idle, they exit at interpreter shutdown on their own.

    A subscriber callback WEDGED on this bus is caught by
    ``_no_wedged_global_bus_callbacks`` (fails the test that left it),
    ``_end_if_wedged`` (names it and ends the process at session end) and, as the
    guarantee, ``_arm_exit_watchdog`` (nothing may hang exit)."""
    out = set()
    for name in _EVENT_BUS_ALIASES:
        bus = getattr(sys.modules.get(name), "event_bus", None)
        out.update(_executor_threads(getattr(bus, "_executor", None)))
    return out


def _leaked_nondaemon_threads(before, deadline_s=0.5):
    """Thread OBJECTS started since ``before`` (a set of Thread objects — never
    idents, review #1) that are non-daemon and still alive after ONE shared
    short deadline (a thread already finishing is not a leak; review #8)."""
    exempt = _process_lifetime_threads()
    return _settle_threads(lambda: [t for t in threading.enumerate()
                                    if t not in before and t not in exempt
                                    and not t.daemon and t is not threading.main_thread()],
                           deadline_s)


def _thread_fn_name(t):
    """What a thread runs, for the failure message (a Timer keeps it in .function)."""
    fn = getattr(t, "_target", None) or getattr(t, "function", None)
    return getattr(fn, "__qualname__", None) or type(t).__qualname__


@pytest.fixture(autouse=True)
def _no_leaked_nondaemon_threads():
    """Fail the test that leaves a non-daemon thread running (THR-1). The fix is
    the owner's stop API (``StatusBar.cleanup()``, ``EventBus.shutdown()``,
    ``executor.shutdown()``), not a wider exemption."""
    import threading
    before = set(threading.enumerate())
    yield
    leaked = _leaked_nondaemon_threads(before)
    if leaked:
        pytest.fail("THR-1: test left non-daemon thread(s) running: "
                    + ", ".join(f"{t.name} ({_thread_fn_name(t)})" for t in leaked)
                    + " — call the owner's stop API (cleanup()/shutdown()) in the test.",
                    pytrace=False)


#: THR-1 wedge guard (operator 11-18: "we need to fix this — we are better than
#: this"). The global bus's workers are exempt from the leak gate (process-
#: lifetime pool), so a subscriber callback that never returns was invisible —
#: the test passed and interpreter exit then blocked on the non-daemon worker
#: (Fable r2 F2, VERIFIED by a plant: results printed, python never exited).
#: Two parts: (1) every task submitted to the GLOBAL bus during a test is
#: tracked; at teardown one still running after the shared deadline FAILS THAT
#: TEST, naming the callback; (2) if any is still running at session end, the
#: process is ended with a failing status after saying exactly which — a stuck
#: callback can never again turn into a silent hang. A clean run is unchanged.
_WEDGED_BUS_CALLBACKS = []        # (callback name, nodeid, future) still running
_SESSION_EXITSTATUS = [0]


def _callback_name(fn, args):
    """EventBus submits ``_safe_call(callback, event)`` — name the CALLBACK."""
    target = args[0] if getattr(fn, "__name__", "") == "_safe_call" and args else fn
    return getattr(target, "__qualname__", None) or repr(target)


@pytest.fixture(autouse=True)
def _no_wedged_global_bus_callbacks(request, monkeypatch):
    import concurrent.futures
    inflight = []
    for name in _EVENT_BUS_ALIASES:
        bus = getattr(sys.modules.get(name), "event_bus", None)
        ex = getattr(bus, "_executor", None)
        if ex is None or getattr(ex, "_mf_tracked", False):
            continue
        orig_submit = ex.submit

        def _submit(fn, *a, _orig=orig_submit, **k):
            fut = _orig(fn, *a, **k)
            inflight.append((_callback_name(fn, a), fut))
            return fut
        monkeypatch.setattr(ex, "submit", _submit)
        monkeypatch.setattr(ex, "_mf_tracked", True, raising=False)
    yield
    if not inflight:
        return
    import time
    end = time.monotonic() + _THREAD_JOIN_DEADLINE_S
    while True:                          # r4 F2: a child emitted while finishing is waited for too
        pending = [f for _, f in list(inflight) if not f.done()]
        left = end - time.monotonic()
        if not pending or left <= 0:
            break
        concurrent.futures.wait(pending, timeout=left)
    stuck = [(n, f) for n, f in list(inflight) if not f.done()]
    if stuck:
        _WEDGED_BUS_CALLBACKS.extend((n, request.node.nodeid, f) for n, f in stuck)
        pytest.fail("THR-1: callback(s) on the GLOBAL event_bus still running after "
                    f"teardown: {', '.join(n for n, _ in stuck)} — a callback that never "
                    "returns blocks interpreter exit; make it return or time out.",
                    pytrace=False)


def pytest_sessionfinish(session, exitstatus):
    _SESSION_EXITSTATUS[0] = int(exitstatus)


def _executor_threads(ex):
    """The executor's worker Thread objects, robust to a mocked bus/executor and to
    the set changing size while we look (r5 lows)."""
    try:
        return [t for t in list(getattr(ex, "_threads", None) or ())
                if isinstance(t, threading.Thread)]
    except TypeError:                    # a test left a plain Mock() — nothing real to join
        return []


def _end_if_wedged():
    """Session end: the test process must NEVER hang at exit (operator 11-18).

    Measures the ONE thing interpreter exit waits for — every alive NON-DAEMON
    thread — not a pointer to it. Rounds 4 and 5 found the same defect twice:
    trusting the callbacks a test window tracked (r4 F1), then trusting whichever
    executor ``event_bus._executor`` named at the end (r5 F1: a wedge followed by
    a swapped or restored executor still hung, once silently). So: shut the
    global buses down without waiting, join EVERY alive non-daemon non-main
    thread against one bounded grace, and only if one survives say what it runs
    (and which tracked callbacks are stuck) and end the process with the
    recorded failing status. A clean run returns."""
    for name in _EVENT_BUS_ALIASES:
        bus = getattr(sys.modules.get(name), "event_bus", None)
        ex = getattr(bus, "_executor", None)
        if ex is None:
            continue
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:    # noqa: BLE001 — session teardown must not mask results
            pass
    survivors = _settle_threads(
        lambda: [t for t in threading.enumerate()
                 if t is not threading.main_thread() and not t.daemon],
        _THREAD_JOIN_DEADLINE_S)
    if not survivors:
        return
    named = [(n, node) for n, node, f in _WEDGED_BUS_CALLBACKS if not f.done()]
    lines = "".join(f"  {n}  (left by {node})\n" for n, node in named)
    lines += "".join(f"  thread {t.name} running {_thread_fn_name(t)}\n" for t in survivors)
    try:
        sys.stdout.flush()               # r4 F4: the summary tail first, then why we exit
        sys.stderr.write("\nTHR-1: ending the test process — non-daemon thread(s) still "
                         f"running after a {_THREAD_JOIN_DEADLINE_S:g} s grace would block "
                         "interpreter exit:\n" + lines)
        sys.stderr.flush()
    finally:
        os._exit(_SESSION_EXITSTATUS[0] or 1)

_STATUS_BAR_ALIASES = ("status_bar", "launcher_tui.status_bar", "src.launcher_tui.status_bar")


@pytest.fixture(autouse=True)
def _stop_status_bars(monkeypatch, _no_leaked_nondaemon_threads):
    """Every StatusBar a test builds is stopped with its OWN API at teardown —
    ``cleanup()``, then its executor shut down and its worker joined against one
    bounded deadline. AUTOUSE (review #6: an opt-in marker every new file had to
    remember; ``launcher_tui.main._setup_status_bar`` builds one indirectly).
    Requests the leak check so it tears down AFTER this. EVERY import alias is
    wrapped (separate module objects, separate classes — the
    ``_isolate_node_cache_files`` lesson), imported HERE so a lazy first import
    cannot slip past; only a genuinely unavailable alias is skipped (review #9)."""
    import importlib
    created = []
    for name in _STATUS_BAR_ALIASES:
        try:
            mod = importlib.import_module(name)
        except (ImportError, ModuleNotFoundError):   # this alias is not on sys.path here
            continue
        cls = getattr(mod, "StatusBar", None)
        if cls is None:
            continue
        orig = cls.__init__

        @functools.wraps(orig)          # F1 (Fable r2): getsource/signature see the REAL __init__ —
        def _init(self, *a, _orig=orig, **k):   # test_tui_dead_code_purge reads its source
            _orig(self, *a, **k)
            created.append(self)
        monkeypatch.setattr(cls, "__init__", _init)
    yield created
    errors, workers = [], []
    for bar in created:                    # review #3: one failure must not skip the rest
        try:
            bar.cleanup()
        except Exception as e:             # noqa: BLE001 — collected, re-raised below
            errors.append(e)
        ex = getattr(bar, "_weather_executor", None)
        if ex is None:
            continue
        try:
            workers.extend(t for t in (getattr(ex, "_threads", None) or ())
                           if isinstance(t, threading.Thread))
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:                  # noqa: BLE001 — a test-mocked executor (Mock()) is
            pass                           # scaffolding, not a leak: never errors the test (r3 L4)
    _join_all(workers, _THREAD_JOIN_DEADLINE_S)
    if errors:
        raise errors[0]


def pytest_report_header(config):
    """Host-fence witness: say what is not the healthy shape (silent when it is)."""
    out = []
    if _FENCE_STATS["fallback"]:
        out.append(f"host fence: ⚠️ {_FENCE_STATS['fallback']}")
    if _FENCE_STATS["repaired"]:
        out.append(f"host fence: repaired {_FENCE_STATS['repaired']} shim(s) in the "
                   "shared fence dir (missing or wrong — e.g. /tmp age-out)")
    if _FENCE_STATS["unknown"]:
        out.append("host fence: ⚠️ unexpected entr(ies) in the shared fence dir, "
                   "first on PATH for every run: " + ", ".join(_FENCE_STATS["unknown"]))
    if _FENCE_STATS["legacy"]:
        # '[p]ytest' cannot match this command's own shell (Fable r5); >1 day old
        # only. An orphan of a killed run is NOT detectable here — the operator's call.
        # Fable r6: name the LITERAL dir this run counted (not the operator
        # shell's $TMPDIR) and say so when the pytest gate declines.
        out.append(f"host fence: {_FENCE_STATS['legacy']} other fence dir(s) in "
                   f"{_FENCE_STATS['tmpdir']} (pre-2026-11-20 per-run dirs, fallbacks, "
                   "older-version shared dirs; never auto-removed — an orphan of a "
                   "killed run may still use one). To clear those >1 day old while "
                   "no pytest runs: pgrep -f '[p]ytest' >/dev/null && echo 'pytest "
                   "running - not clearing' || find '" + _FENCE_STATS["tmpdir"] +
                   "' -maxdepth 1 -name 'mf-host-fence-*' ! -name '" +
                   _FENCE_STATS["current"] + "' -mmin +1440 -exec rm -rf {} +")
    return out or None


def pytest_configure(config):
    """Register custom markers."""
    if os.environ.get("MF_TESTS_ALLOW_REAL_SUDO") != "1":
        (config._mf_host_fence_dir, config._mf_host_fence_log,
         config._mf_host_fence_shared) = _install_sudo_fence()
    config.addinivalue_line(
        "markers", "hardware: mark test as requiring hardware (skipped in CI)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow (may be skipped with --fast)"
    )
    config.addinivalue_line(
        "markers", "network: mark test as requiring network access"
    )

    # Disable cascade-detector probes during pytest. The
    # rns_rpc_wedge fingerprint calls `subprocess.run(["ss", ...],
    # timeout=2)`; when a daemon detector thread (started by any test
    # that exercises the full service init path) is still running
    # during another test's `with patch('subprocess.run')` window, the
    # probe's call leaks into the mock's call_args_list and trips
    # assertions like `assert call[1]['timeout'] == 60` —
    # observed on Python 3.9/3.11 in CI starting at commit 79f5d7b.
    # See project_ci_red_track0_followup.md for full forensics.
    # Tests that explicitly exercise probe behavior unset this via
    # `monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)`.
    os.environ["MESHFORGE_CASCADE_PROBE_DISABLED"] = "1"

    # Redirect the gateway's content_id_view state file (dedup arc STEP 4c)
    # to a throwaway tmp path for the whole suite. The producer hook fires
    # inside the real RNSMeshtasticBridge._rns_loop, which several bridge
    # tests drive (bridge._running = True), and without this it would write
    # the operator's actual ~/.local/state/meshforge/content_id_view.json.
    # setdefault so a test can still point it elsewhere.
    import tempfile as _tempfile
    os.environ.setdefault(
        "MESHFORGE_CONTENT_ID_VIEW_STATE",
        os.path.join(_tempfile.gettempdir(),
                     "meshforge-pytest-content_id_view.json"),
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report every RF/RNS transmission the egress guard refused this run.

    ``utils.tx_guard`` records what it blocks so a suite run can ENUMERATE the
    hops that would have keyed the radio without keying it. That claim was only
    true if somebody called ``blocked_attempts()`` — the drills did, nothing
    else did. A witness nobody reads is the writer-with-no-reader defect this
    project keeps naming, so here is the reader.

    It matters most in the case that started all this (2026-08-09): a send
    attempted from a BACKGROUND thread. ``TransmitBlocked`` derives from
    BaseException so it is never swallowed by the site's ``except Exception``,
    but in a daemon thread it kills that thread and no test asserts on it —
    the block would be correct, invisible, and the leak's twin would go
    unnoticed. This prints it either way.

    Informational only: it never changes ``exitstatus``. A blocked attempt is
    the guard WORKING. What it buys is that you find out.
    """
    try:
        from utils import tx_guard
        blocked = tx_guard.blocked_attempts()
    except Exception:
        return
    if not blocked:
        return
    by_target = {}
    for rec in blocked:
        key = (rec.get("kind", "?"), rec.get("target", "?"))
        by_target.setdefault(key, []).append(rec.get("test") or "(no test)")
    terminalreporter.write_sep(
        "-", f"tx_guard: refused {len(blocked)} transmission(s)")
    for (kind, target), tests in sorted(by_target.items()):
        terminalreporter.write_line(f"  {kind:<24} -> {target}  x{len(tests)}")
        seen = []
        for t in tests:
            short = t.split(" ")[0]
            if short not in seen:
                seen.append(short)
        for t in seen[:3]:
            terminalreporter.write_line(f"      {t}")
        if len(seen) > 3:
            terminalreporter.write_line(f"      ... and {len(seen) - 3} more")


def pytest_collection_modifyitems(config, items):
    """Auto-skip certain tests in CI environment."""
    if not (CI or MESHFORGE_CI):
        return

    skip_hardware = pytest.mark.skip(reason="Hardware not available in CI")
    skip_network = pytest.mark.skip(reason="Network tests skipped in CI")

    for item in items:
        # Skip hardware-marked tests
        if "hardware" in item.keywords:
            item.add_marker(skip_hardware)

        # Skip network-marked tests in CI
        if "network" in item.keywords:
            item.add_marker(skip_network)

        # Auto-detect likely hardware tests by name
        test_name = item.name.lower()
        if any(kw in test_name for kw in ['real_device', 'physical', 'actual_hardware']):
            item.add_marker(skip_hardware)


@pytest.fixture(autouse=True)
def _reset_journal_scan_memo():
    """Clear the watchdog's incremental journal-scan memo between tests.

    ``_journal_newest_match_status`` remembers, per (unit, pattern), how far it
    has successfully scanned and what the newest match was, so a 30s tick costs
    30s of journal instead of the whole window. That memo is MODULE-GLOBAL, so
    without this fixture a case that primes it changes the answer a later case
    gets from its own patched ``subprocess.run`` — a verdict depending on
    un-pinned ambient state, which is a defect this suite has paid for before.

    Autouse rather than opt-in: the leak crosses FILES, so remembering to
    request it is exactly the kind of discipline that fails silently.
    """
    try:
        from utils.watchdog_probe_core import reset_journal_memo
    except ImportError:      # minimal-deps CI profile — nothing to reset
        yield
        return
    reset_journal_memo()
    yield
    reset_journal_memo()


@pytest.fixture(autouse=True)
def _reset_lxmf_wedge_streaks():
    """Clear ``probe_lxmf_process_wedge``'s per-(pattern, pid) blocked
    streaks between tests (DET-WEDGE, 2026-10-22). Module-global like the
    journal memo above, and keyed on fake pids that many tests reuse, so a
    case that primes it would make a later case fire a tick early."""
    try:
        from utils.watchdog_probes_rns import reset_lxmf_wedge_streaks
    except ImportError:      # minimal-deps CI profile — nothing to reset
        yield
        return
    reset_lxmf_wedge_streaks()
    yield
    reset_lxmf_wedge_streaks()


@pytest.fixture(autouse=True)
def _pin_inbox_unseen_journal(monkeypatch):
    """No test reads THIS box's meshtasticd journal through /api/inbox
    (downlink gap, 2026-11-17). ``read_inbox`` now scans the radio's journal;
    unpinned, every inbox test's verdict would depend on the machine (and the
    module cache would leak one test's scan into the next). The default is an
    absent journalctl -> ``unobservable``; tests/test_inbox_unseen.py replaces
    ``_run`` itself to drive each state."""
    try:
        from utils import inbox_unseen
    except ImportError:
        yield
        return

    def _no_journal(argv, **kw):
        raise FileNotFoundError("journalctl (pinned off in tests)")
    monkeypatch.setattr(inbox_unseen, "_run", _no_journal)
    inbox_unseen._reset_cache()
    yield
    inbox_unseen._reset_cache()


@pytest.fixture(autouse=True)
def _isolate_meshtastic_cli_connection():
    """Every test starts from the DEFAULT meshtastic CLI connection and never
    touches the real saved one (2026-10-30). commands.meshtastic now persists
    the operator's choice (Settings > Meshtastic Connection) in
    ~/.config/meshforge/meshtastic_cli.json and loads it lazily into module
    globals — so without this, an operator's saved serial port would decide
    test verdicts, a test could WRITE the operator's file, and one test's
    set_connection would leak into the next. Both import aliases (see
    _reset_node_tracker_singleton). Tests that exercise persistence patch
    _read_persisted/_write_persisted themselves.
    """
    # Import it HERE (r5 #8): a test that imports it lazily, first in the
    # run, would otherwise reach the operator's real file.
    try:
        import commands.meshtastic  # noqa: F401
    except Exception:            # minimal-deps profile — nothing to isolate
        pass
    attrs = ("_connection_type", "_connection_value", "_persisted_loaded",
             "_explicit_in_process", "_persisted_seen_stamp",
             "_read_persisted", "_write_persisted", "_persisted_stamp")
    saved = []
    for name in ("commands.meshtastic", "src.commands.meshtastic"):
        mod = sys.modules.get(name)
        if mod is None or not hasattr(mod, "_persisted_loaded"):
            continue
        saved.append((mod, {a: getattr(mod, a) for a in attrs if hasattr(mod, a)}))
        mod._connection_type, mod._connection_value = "localhost", "localhost"
        mod._persisted_loaded = True               # nothing is read from disk
        mod._explicit_in_process = False
        mod._persisted_seen_stamp = None
        mod._persisted_stamp = lambda: None
        mod._read_persisted = lambda: {}
        mod._write_persisted = lambda t, v: True   # nothing is written to disk
    yield
    for mod, vals in saved:
        for a, v in vals.items():
            setattr(mod, a, v)


#: One counter PER ROOT (review 11-18 #6/#9): a fresh root numbers from 0, so
#: naming is deterministic and testable; uniqueness never rests on one
#: process-wide counter — a name already on disk is SKIPPED, never reused.
_ISOLATION_SEQ = {}


def _fresh_isolation_dir(root):
    """A NEW, empty directory under ``root`` — O(1), never a scan (CI-1, 11-18).

    ``tmp_path_factory.mktemp`` (and every ``tmp_path`` request) picks its number
    by scanning EVERY entry of the basetemp root, so paying it per test in an
    autouse fixture made the suite quadratic in its own size (cProfile diff:
    +98.6 ms/test late in the run; 27,780 entries; see
    ``plans/ci_suite_cost_2026_11_18.md`` and ``tests/test_conftest_tmp_cost.py``,
    the gate). Children are sharded 1,000 per parent so no directory grows
    without bound; ``exist_ok=False`` + skip means a test is never handed a
    directory that already existed (a second conftest module object, a rerun)."""
    root = Path(root)
    seq = _ISOLATION_SEQ.setdefault(str(root), itertools.count())
    while True:
        n = next(seq)
        d = root / f"s{n // 1000:04d}" / f"t{n % 1000:03d}"
        d.parent.mkdir(exist_ok=True)
        try:
            d.mkdir(exist_ok=False)
            return d
        except FileExistsError:
            continue


@pytest.fixture(scope="session")
def _isolation_root(tmp_path_factory):
    """ONE ``mktemp`` for the session; per-test children via ``_fresh_isolation_dir``."""
    return tmp_path_factory.mktemp("per_test_isolation")


@pytest.fixture
def _per_test_isolation_dir(_isolation_root):
    """ONE fresh empty directory per test, shared by the autouse isolation
    fixtures (review 11-18 #5: one mkdir per test, not one per fixture). Their
    file names do not collide: broker_profiles.json, rns_nodes.json,
    node_cache.json. Function-scoped, but it requests only the SESSION root —
    the CI-1 gate allows exactly this shape."""
    return _fresh_isolation_dir(_isolation_root)


@pytest.fixture(autouse=True)
def _isolate_broker_profile_store(_per_test_isolation_dir):
    """Every test's broker profile store is a tmp file, never the operator's
    ``~/.config/meshforge/broker_profiles.json`` (F8 v2 r8, 2026-11-03).

    Three suite files called the real ``load_profiles()``: the r7 full suite
    tightened the manager box's live store 664 -> 600, and before r7 the same leak
    could WRITE defaults into an empty real store. Autouse, both import
    aliases, imported HERE so a lazy first import cannot reach the real file
    (the ``_isolate_meshtastic_cli_connection`` lesson). A test that patches
    ``_get_profiles_path`` itself still wins — its patch nests inside this.
    """
    try:
        import utils.broker_profiles  # noqa: F401
    except Exception:            # minimal-deps profile — nothing to isolate
        pass
    path = _per_test_isolation_dir / "broker_profiles.json"
    saved = []
    for name in ("utils.broker_profiles", "src.utils.broker_profiles"):
        mod = sys.modules.get(name)
        if mod is None or not hasattr(mod, "_get_profiles_path"):
            continue
        saved.append((mod, mod._get_profiles_path))
        mod._get_profiles_path = lambda _p=path: _p
    yield
    for mod, fn in saved:
        mod._get_profiles_path = fn


@pytest.fixture(autouse=True)
def _reset_node_tracker_singleton():
    """Give every test a fresh node-tracker singleton.

    The bridge SHARES the ``get_node_tracker()`` singleton instead of building
    a private ``UnifiedNodeTracker`` (2026-09-22, the shutdown-clobber fix:
    two trackers over one node_cache.json, the stale one flushing last).
    Without this reset the second test to construct a bridge would inherit the
    first test's node set — cross-test leakage that surfaces as a flake in
    whichever test reads the population next, nowhere near the one that
    dirtied it.

    BOTH import aliases are reset, for exactly the reason
    ``_isolate_node_cache_files`` gives below: ``sys.path`` carries the repo
    root AND ``src/``, so ``gateway.node_tracker`` and
    ``src.gateway.node_tracker`` are two module objects holding two
    independent ``_node_tracker`` globals. Resetting the one in front of you
    leaves the other handing the next test a used tracker.

    Runs on setup as well as teardown: a test that leaks despite this must not
    poison its successor.
    """
    def _reset():
        for name in ("gateway.node_tracker", "src.gateway.node_tracker"):
            mod = sys.modules.get(name)
            reset = getattr(mod, "reset_node_tracker", None)
            if reset is not None:
                reset()

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _isolate_node_cache_files(_per_test_isolation_dir):
    """Keep the node-cache writers out of the operator's live data directory.

    ``UnifiedNodeTracker._save_cache`` writes TWO files: the one
    ``get_cache_file()`` names (``~/.config/meshforge/node_cache.json``) and an
    operator-owned copy at ``MeshForgePaths.rns_nodes_cache_path()``
    (``~/.cache/meshforge/rns_nodes.json``). Any test that constructs a bare
    ``UnifiedNodeTracker()`` therefore reads and overwrites real fleet data —
    measured 2026-08-03: a full-suite run on this box replaced a live
    ``rns_nodes.json`` with 917 KB it had loaded from the box's own config,
    and on a GATEWAY box that file is the 10 MB RNS node cache the map
    collectors read.

    Autouse and suite-wide on purpose. A per-file guard only holds until the
    next test file constructs a tracker (``test_gateway_integration`` already
    did), so this is a gate, not a convention: a test's verdict must not depend
    on which box ran it, and neither must its side effects
    (feedback_tests_must_pin_ambient_state).

    Tests that assert on cache CONTENT patch these to their own tmp paths;
    an inner patch wins, so this only catches what would otherwise escape.

    BOTH import aliases are patched (2026-08-03, found porting this fixture to
    MeshAnchor). ``sys.path`` carries the repo root AND ``src/``, so
    ``gateway.node_tracker`` and ``src.gateway.node_tracker`` are two distinct
    module objects holding two distinct ``UnifiedNodeTracker`` classes —
    measured, not assumed: under the single-alias version of this fixture the
    ``src.*`` class still returned ``~/.config/meshforge/node_cache.json``.
    ``test_gateway_integration`` and ``test_node_tracker`` import via ``src.``,
    so the gate was covering the alias those files do NOT use. The md5 check
    that blessed it held only because those files also patch locally — a
    property of today's tests, not of the gate. Patching the alias in front of
    you is exactly the half-covered guard this fixture exists to prevent.
    """
    root = _per_test_isolation_dir
    targets = [
        ('utils.paths.MeshForgePaths.rns_nodes_cache_path', root / "rns_nodes.json"),
        ('src.utils.paths.MeshForgePaths.rns_nodes_cache_path', root / "rns_nodes.json"),
        ('gateway.node_tracker.UnifiedNodeTracker.get_cache_file', root / "node_cache.json"),
        ('src.gateway.node_tracker.UnifiedNodeTracker.get_cache_file', root / "node_cache.json"),
    ]
    with ExitStack() as stack:
        patched = 0
        for target, value in targets:
            try:
                stack.enter_context(patch(target, return_value=value))
                patched += 1
            except (ImportError, AttributeError, ModuleNotFoundError):
                # An alias that is not importable in this environment cannot
                # be the one writing to the live path either.
                continue
        for target in ('utils.paths.chown_to_operator',
                       'src.utils.paths.chown_to_operator'):
            try:
                stack.enter_context(patch(target))
            except (ImportError, AttributeError, ModuleNotFoundError):
                continue
        if not patched:
            raise RuntimeError(
                "node cache isolation patched NOTHING — the suite would write "
                "to the operator's live node_cache.json / rns_nodes.json"
            )
        yield root


#: Modules that resolve an operator data store as
#: ``get_real_user_home() / ".config" / "meshforge" / <name>`` and WRITE it.
#: Each is patched at its own module attribute (both import aliases), which
#: redirects only that module — patching ``utils.paths.get_real_user_home``
#: itself would break the tests that assert what it returns under sudo.
#: Found by directory sweep, not by memory: see _isolate_operator_data_stores.
_OPERATOR_STORE_MODULES = (
    "commands.messaging",              # messages.db
    "gateway.correlation_store",       # gateway_correlation.db
    "gateway.message_queue",           # message_queue.db
    # mesh_bridge_queues/p2s.db + s2p.db — MeshtasticPresetBridge() resolves
    # the home through ITS OWN import; only per-file fixtures covered it
    # (2026-11-11, found porting BR-T #4–#18 to MeshAnchor before any red run).
    "gateway.mesh_bridge",
    "commands.rns",                    # lxmf_storage/ (LXMF ratchets)
    "gateway._rns_bridge_connection",  # lxmf_storage/ (LXMF ratchets)
    # operator_line_status.json (BIC-15 pane, 2026-10-15) + the line's default
    # identity/storage — a test that omits status_path must not write the live one.
    "gateway.operator_line",
    # templates/exported_<ts>.json — LITTER, a NEW file every run by design
    # ("timestamp suffix makes each export unique", so nothing ever
    # overwrites and nothing ever cleans up). 856 had accumulated since
    # 2026-04-21 before this line existed. Reached under three aliases
    # because sys.path carries src/ AND src/launcher_tui.
    "launcher_tui.handlers._gateway_preflight_template",
    "handlers._gateway_preflight_template",
    # mqtt_nodeless.json — the operator's saved MQTT settings. 2026-10-31: a
    # red-first test run against the OLD save_mqtt_config (hardcoded path)
    # overwrote the manager box's real file with a test's {"broker": "x"}; the
    # original was unrecoverable. Every module that resolves that path:
    "monitoring.mqtt_subscriber",      # saved_mqtt_config_path / save_config
    "launcher_tui.handlers.mqtt",      # load/save_mqtt_config (+ alias below)
    "handlers.mqtt",
    # rf_segments_declared.json — READ only, but a live declaration flips
    # /inbox verdicts (moc3 -> cannot_hear) in tests that never asked for one
    # (2026-11-16; feedback_tests_must_pin_ambient_state).
    "utils.rf_segments",
    "daemon",                          # _operator_home_unknown reads the home
)


@pytest.fixture(scope="session", autouse=True)
def _no_space_weather_network():
    """No test may reach NOAA (tests/test_no_network_space_weather.py).

    A real fetch started by a status-bar worker thread outlives its test; on
    CI it segfaulted mid-SSL-handshake while a later test ran (exit 139, no
    pytest summary): 7d6b46c7 and f9b5e0eb. A per-file patch of
    ``status_bar.SpaceWeatherAPI`` missed ``src.launcher_tui.status_bar`` (a
    second module object), so the stub sits below every alias. URLError is
    what urlopen raises with no network — every call site handles it.
    """
    import urllib.error
    from utils import space_weather

    def _refuse(*_a, **_k):
        raise urllib.error.URLError("network disabled in tests")

    with patch.object(space_weather, "_urlopen", _refuse):
        yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_operator_data_stores(tmp_path_factory):
    """Keep the suite out of the operator's message store, queue, correlation
    DB and LXMF ratchets.

    Third sibling of ``_isolate_node_cache_files`` and
    ``_isolate_delivery_counters_db``. Measured 2026-08-05 by sweeping the
    whole operator tree (``~/.config/meshforge`` + ``~/.local/share/meshforge``
    + ``~/.cache/meshforge``) across one suite run: 22 files touched, and —
    after subtracting an idle CONTROL run to remove the live map service's own
    writes — FOUR genuinely mutated:

        lxmf_storage/lxmf/ratchets/*.ratchets   LXMF crypto ratchet state
        messages.db                             message store
        message_queue.db                        persistent queue
        gateway_correlation.db                  correlation store

    The ratchets are the reason this is a gate and not a note: they are
    per-destination cryptographic state on the box the fleet treats as its
    source of truth, and corrupting them fails opaquely and late.
    ``device_config.yaml`` (the RADIO config) is also rewritten by the suite
    and survives only because the fixture happens to match what is there —
    it is covered here too, via ``commands.messaging``'s sibling paths, by
    virtue of the whole config dir moving.

    Session-scoped: redirection is idempotent and O(1), and a per-test
    version of the delivery-counters guard cost 45% of suite wall-clock
    before it was fixed. Files needing per-test isolation still bring their
    own fixtures, which patch inside this one and win.
    """
    root = tmp_path_factory.mktemp("operator_home_isolation")
    (root / ".config" / "meshforge").mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        patched = []
        # device_config.yaml — the RADIO config, and the one file here that
        # could do real-world harm. Its header says "Re-applied automatically
        # after meshtasticd restart", so a wrong value is pushed to the radio.
        # On this box the suite rewrote it BYTE-IDENTICALLY, which read as
        # harmless — but that is a coincidence of the fixture matching this
        # box's LONG_FAST/ch20. moc3 runs SHORT_TURBO/ch8: the same write
        # there would take a gateway off its preset and break the
        # moc2<->moc3 RNS leg. Gate the single chokepoint both writers use
        # (`save_device_setting` / `save_device_settings` both go through
        # `_get_config_path`), rather than hunting whichever test does it.
        for alias in ("utils.device_config_store", "src.utils.device_config_store"):
            try:
                stack.enter_context(patch(
                    f"{alias}._get_config_path",
                    return_value=root / ".config" / "meshforge" / "device_config.yaml"))
                patched.append(alias)
            except (ImportError, AttributeError, ModuleNotFoundError):
                continue
        for mod in _OPERATOR_STORE_MODULES:
            for alias in (mod, f"src.{mod}"):
                try:
                    stack.enter_context(
                        patch(f"{alias}.get_real_user_home", return_value=root))
                    patched.append(alias)
                except (ImportError, AttributeError, ModuleNotFoundError):
                    continue
        if not patched:
            raise RuntimeError(
                "operator data-store isolation patched NOTHING — the suite "
                "would write the operator's messages.db / message_queue.db / "
                "gateway_correlation.db / LXMF ratchets"
            )
        yield root


@pytest.fixture(scope="session", autouse=True)
def _block_radio_egress():
    """FOURTH sibling of the isolation gates — and the first that fences the
    ANTENNA rather than the disk.

    2026-08-09: a full suite run on a fleet radio box transmitted the e2e fixture
    ``[RNS:abc] retry with bytes`` onto a live statewide public channel. The
    suite reported 10,535 passed while keying the air; the operator found out
    by looking at his phone. Three session-scoped gates isolated databases and
    NOTHING isolated egress — we fenced the disk and left the antenna open.

    This is the backstop layer. The primary layer is ``utils.tx_guard``, wired
    into each send site, which names the hop and is the ONLY thing that can
    stop the CLI fallback (``meshtastic --host X --sendtext``) because that
    egress happens in a SUBPROCESS this patch cannot see. What this fixture
    adds is coverage of sites nobody guarded — including the meshtastic
    library's own ``TCPInterface``, which opens its socket well below any code
    we wrote.

    Deliberately narrow: only the meshtasticd radio ports. A blanket
    "block non-loopback" fixture would NOT have caught the 08-09 leak, because
    the radio is ON loopback here — and a blanket block would break the
    map-server and MQTT harnesses that legitimately bind sockets.
    """
    import socket as _socket

    from utils import tx_guard

    radio_ports = {4403, 9443}
    orig_connect = _socket.socket.connect
    orig_connect_ex = _socket.socket.connect_ex

    def _check(address, probe_idiom):
        if not (isinstance(address, tuple) and len(address) >= 2):
            return
        host, port = address[0], address[1]
        if port not in radio_ports:
            return
        # `connect_ex` returns an errno instead of raising — that IS the
        # reachability-probe idiom, and ~15 sites in this tree use it to ask
        # "is the radio port open?". Blocking those converts a benign probe
        # into a failure (CI found exactly that in fleet_snapshot._probe_radio)
        # without catching anything: meshtastic's TCPInterface reaches the
        # radio through create_connection -> sock.connect, which stays blocked.
        # A prober that must use plain connect declares tx_guard.probe_connect().
        if probe_idiom or tx_guard.in_probe():
            # Permitted-but-RECORDED: the witness makes this branch auditable
            # (a permission that leaves no artifact cannot be checked — the
            # leg-b audit had to be done by static reading until this line).
            tx_guard.note_probe_permitted(
                host, port,
                kind="connect_ex" if probe_idiom else "probe_connect",
                detail="permitted probe-idiom touch of a radio port",
            )
            return
        tx_guard.assert_tx_allowed(
            host, port, kind="socket_tripwire",
            detail="raw socket connect to a meshtasticd radio port",
        )

    def _connect(self, address):
        _check(address, probe_idiom=False)
        return orig_connect(self, address)

    def _connect_ex(self, address):
        _check(address, probe_idiom=True)
        return orig_connect_ex(self, address)

    _socket.socket.connect = _connect
    _socket.socket.connect_ex = _connect_ex
    try:
        yield
    finally:
        _socket.socket.connect = orig_connect
        _socket.socket.connect_ex = orig_connect_ex


@pytest.fixture
def allow_local_radio_tx():
    """Allowlist the local radio for a test whose TRANSPORT IS MOCKED.

    ⚠️ Opt-in, never autouse. Requesting this fixture is a test AUTHOR
    asserting "my socket / subprocess / interface is a double, so permitting
    this target cannot key anything." If that assertion is wrong, the test
    transmits — which is precisely the 2026-08-09 failure.

    Use it for tests that exercise send LOGIC (truncation, dedup registration,
    ACK bookkeeping, return-value handling) where the I/O below is patched.
    Do NOT use it to make a red test green: if a test fails with
    ``TransmitBlocked`` and its transport is NOT mocked, the guard just found a
    real leak.
    """
    from utils import tx_guard

    # 4403/9443 = the radio; 1883 = the local broker meshtasticd relays
    # to RF (a downlink inject keys the radio just as toradio does).
    with tx_guard.allow_targets("127.0.0.1:4403", "127.0.0.1:9443",
                                "127.0.0.1:1883"):
        yield


@pytest.fixture
def allow_rns_tx():
    """Allowlist RNS/LXMF egress for a test whose ROUTER/RNS IS MOCKED.

    Opt-in, never autouse. Coarse on/off, because an announce has no
    per-destination target — it floods the whole Reticulum network the process
    is attached to, which on this fleet reaches public transport nodes AND a
    physical RNode on LoRa RF.

    Requesting this is a test author asserting "my LXMRouter / RNS.Reticulum is
    a double." Do NOT use it to turn a red test green: if a test fails with
    TransmitBlocked and its router is real, the guard just found a live leak.
    """
    from utils import tx_guard

    with tx_guard.allow_rns_egress():
        yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_delivery_counters_db(tmp_path_factory):
    """Keep delivery-counter writes out of the operator's live DB.

    Sibling of ``_isolate_node_cache_files``, same class, found the same
    way. ``delivery_counters`` is SQLite-backed at
    ``~/.local/share/meshforge/delivery_counters.db``, and the coupling is
    INDIRECT: a test never names it — it constructs a
    ``PersistentMessageQueue`` or a bridge, and lifecycle events flow
    through. So per-file fixtures kept missing it.
    ``test_delivery_counters.py`` and ``test_rns_bridge.py`` had one;
    ``test_message_queue.py`` did not, and its module docstring said
    "isolated tests" because its OWN queue DB was.

    ⚠️ FOUR seams, not three. The first version pinned the three
    delivery_counters paths and I verified it by watching exactly those
    two files' mtimes — so ``queue_stats.json``, written by
    ``message_queue`` through its OWN seam, kept being overwritten and my
    "operator DB untouched: YES" check could not see it. Verify a guard
    against EVERY artifact the guarded code writes, not the ones you
    happened to think of; the check that only looks where you already
    looked cannot find what you missed.

    Measured 2026-08-05: the manager box — which runs no gateway — held
    252k queued delivery events accumulated since May, purely from suite
    runs. A full run added ~240; test_message_queue alone ~111. That
    garbage is indistinguishable from real telemetry (``queued`` /
    ``dropped`` with ``queue_shed``, protocols ``meshtastic`` / ``rns`` /
    ``primary``) and was briefly reasoned about AS fleet evidence before
    the write schedule gave it away — three bursts in a 500-event ring,
    two of them during that session's own pytest runs.

    Suite-wide and autouse on purpose: a gate, not a convention. A file
    that sets the env var itself still wins (both point at tmp dirs), and
    a test that needs the real resolution deletes the var explicitly
    (``test_state_path_is_in_share_dir_next_to_db`` already does).

    ⚠️ SESSION-scoped deliberately. The first version was function-scoped
    and cost a ``mktemp`` + two imports + two singleton resets PER TEST —
    across 10,333 tests that added ~45% to suite wall-clock (6:08 → 8:54
    locally) and CANCELLED the CI 3.9 job on its timeout. Redirection is
    the whole job here and it is idempotent, so it belongs at session
    scope; per-test ISOLATION between cases that assert on counter
    CONTENT is the business of those files' own fixtures, which already
    exist and still win. A guard that makes the suite 45% more expensive
    is the wrong trade on a Pi fleet — cheap gates get kept.
    """
    root = tmp_path_factory.mktemp("delivery_counters_isolation")
    prior = {}
    for var, name in (("MESHFORGE_DELIVERY_COUNTERS_DB", "delivery_counters.db"),
                      ("MESHFORGE_DELIVERY_SNAPSHOT_STATE", "delivery_snapshot.json"),
                      ("MESHFORGE_CONTENT_ID_VIEW_STATE", "content_id_view.json"),
                      ("MESHFORGE_QUEUE_STATS_STATE", "queue_stats.json")):
        prior[var] = os.environ.get(var)
        os.environ[var] = str(root / name)
    for alias in ("gateway.delivery_counters", "src.gateway.delivery_counters"):
        try:
            __import__(alias)
            sys.modules[alias]._reset_singleton_for_tests()
        except (ImportError, AttributeError, ModuleNotFoundError, KeyError):
            continue
    yield root
    for var, was in prior.items():
        if was is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = was


@pytest.fixture
def mock_meshtastic():
    """Mock meshtastic module for tests that don't need real hardware."""
    mock_module = MagicMock()
    mock_interface = MagicMock()
    mock_interface.nodes = {}
    mock_interface.myInfo = MagicMock()
    mock_interface.myInfo.my_node_num = 12345678

    mock_module.serial_interface.SerialInterface.return_value = mock_interface
    mock_module.tcp_interface.TCPInterface.return_value = mock_interface

    with patch.dict('sys.modules', {
        'meshtastic': mock_module,
        'meshtastic.serial_interface': mock_module.serial_interface,
        'meshtastic.tcp_interface': mock_module.tcp_interface,
    }):
        yield mock_module


@pytest.fixture
def mock_rns():
    """Mock RNS module for tests that don't need real Reticulum."""
    mock_module = MagicMock()

    with patch.dict('sys.modules', {
        'RNS': mock_module,
    }):
        yield mock_module


@pytest.fixture
def no_network():
    """Block network access for isolated tests."""
    import socket
    original_socket = socket.socket

    def guarded_socket(*args, **kwargs):
        raise OSError("Network access blocked in test")

    with patch.object(socket, 'socket', guarded_socket):
        yield


# TUI Handler Test Infrastructure (FakeDialog, make_handler_context) lives
# in tests/handler_test_utils.py — the single source of truth. Don't
# re-add definitions here; they were duplicated previously and the copy
# in this file was unused (no test imported it from conftest). Re-adding
# definitions risks silent divergence the next time someone edits one
# and not the other.


# ---------------------------------------------------------------------------
# e2e pipeline patch drain (2026-08-09 review finding 7)
# ---------------------------------------------------------------------------

def _drain_pipeline_teardowns(registry: list, down_to: int) -> list:
    """Pop and run teardown callables until ``len(registry) == down_to``.

    Returns the failures instead of swallowing them: a restore that fails
    leaves a PROCESS-WIDE patch (widened RF allowlist, neutered CLI fallback)
    live for the rest of the session, and the old ``except Exception: pass``
    made that invisible while also discarding the popped callable
    (honest_failure_modes #9 — every swallow leaves a witness).
    """
    failures = []
    while len(registry) > down_to:
        fn = registry.pop()
        try:
            fn()
        except Exception as e:
            failures.append(f"{fn!r}: {type(e).__name__}: {e}")
    return failures


@pytest.fixture(autouse=True)
def _drain_e2e_pipeline_patches():
    """Per-test drain of tests.e2e.harness.pipeline._E2E_TEARDOWN.

    Lives HERE (top-level conftest), not in tests/e2e/conftest.py: the
    pipeline helper's patches are process-wide, so any caller anywhere must
    get drained. Keyed on the module being in sys.modules — zero cost for
    the whole suite when no test imported it.

    Depth-snapshot semantics, NOT clear(): the old fixture cleared the
    registry at setup, which DISCARDED restores registered by module- or
    session-scoped fixtures (their setup runs before this fixture's) instead
    of running them — unrevertable, since allow_targets restores from the
    discarded object. Higher-scoped registrations survive the per-test drain
    and are swept at session end by _drain_e2e_pipeline_patches_session.
    """
    mod = sys.modules.get("tests.e2e.harness.pipeline")
    depth = len(mod._E2E_TEARDOWN) if mod is not None else 0
    yield
    mod = sys.modules.get("tests.e2e.harness.pipeline")
    if mod is None:
        return
    failures = _drain_pipeline_teardowns(mod._E2E_TEARDOWN, depth)
    if failures:
        pytest.fail(
            "e2e pipeline teardown restore(s) FAILED — process-wide patches "
            "(RF egress allowlist / fallback neutralization) may still be "
            "live for the rest of this run:\n  " + "\n  ".join(failures)
        )


@pytest.fixture(scope="session", autouse=True)
def _drain_e2e_pipeline_patches_session():
    """Session-end sweep for registrations the per-test drain must not touch
    (made by module/session-scoped fixtures). A leftover here is legitimate
    during the run and MUST still be restored before the process is reused
    (pytest-xdist workers, IDE test runners keep the interpreter alive)."""
    yield
    mod = sys.modules.get("tests.e2e.harness.pipeline")
    if mod is None:
        return
    failures = _drain_pipeline_teardowns(mod._E2E_TEARDOWN, 0)
    if failures:
        raise RuntimeError(
            "e2e pipeline teardown restore(s) FAILED at session end:\n  "
            + "\n  ".join(failures)
        )


@pytest.fixture(scope="session")
def _absent_posture_path(tmp_path_factory):
    """ONE directory for the whole session, not one per test.

    The first cut called ``tmp_path_factory.mktemp()`` inside the autouse
    fixture below, so it ran ~12,300 times and left ~12,300 directories in a
    single parent. MEASURED on CI (the authority I did not write): the 3.9
    job went 13m00s -> 15m15s and was killed by the 15-minute cap, which
    GitHub reports as `cancelled` rather than a failure.

    ⚠️ Worth carrying: a local sample of 977 tests put the cost at ~1.95 ms
    each, which extrapolates to ~24 s and would have exonerated it. Directory
    creation in one parent is NOT linear in the entry count, so the small
    sample understated the real cost by ~5x. Only the full-scale number
    settled it (calibrated_claims: a measurement at the wrong scale is a
    measurement of the wrong thing).

    Nothing ever reads or creates this path -- it exists only to be ABSENT,
    so one per session is one more than strictly needed.
    """
    return tmp_path_factory.mktemp("no-posture") / "fleet_posture.json"


@pytest.fixture(autouse=True)
def _pin_declared_posture(monkeypatch, _absent_posture_path):
    """No test may read the OPERATOR'S LIVE power posture, or this machine's
    real clock, to reach its verdict.

    Both became test-visible on 2026-09-10, when the posture document stopped
    being manager-only. ``probe_tracer_peer_unreachable`` and mini's
    federation source now consult the declaration on THIS box, and
    ``read_posture`` asks the machine whether its clock can be trusted. With
    neither pinned, a probe test's answer depends on whether the operator
    happens to have a box declared dormant while the suite runs, and an
    expiry test's answer depends on the NTP state of whoever runs it: green
    on a synced dev box, opposite on an RTC-less Pi mid-boot, a third answer
    in CI. A test whose verdict depends on un-pinned machine state pins
    nothing (2026-07-28).

    Pinned to a path that does not exist = UNDECLARED = every box active =
    the behaviour every pre-existing probe test was written against, so this
    fixture changes no existing expectation. A test that is ABOUT posture
    overrides both (monkeypatch in the test wins over the fixture).
    """
    absent = _absent_posture_path
    monkeypatch.setenv("MESHFORGE_FLEET_POSTURE", str(absent))
    monkeypatch.setenv("MESHFORGE_CLOCK_CONFIDENT", "1")
    # And the mirror's fan-out is made STRUCTURALLY impossible, not merely
    # unlikely. `fleet_power.watch_and_clear` calls the sync organ on the
    # return leg; the suite runs ON the manager, where that organ can ssh nine
    # real boxes and — with the posture path pinned to a file that does not
    # exist — would enter WITHDRAW mode and start removing their copies.
    # Relying on each test to remember to patch it is exactly the guard whose
    # blast radius holds the thing arming it. Pointed at a host list that does
    # not exist, the organ refuses on its own first check (rc 2) and no test
    # can reach the fleet however it is written.
    monkeypatch.setenv("FLEET_POSTURE_SYNC_HOSTS",
                       str(absent.parent / "no-such-fleet_hosts"))


@pytest.fixture(scope="session", autouse=True)
def _pin_rns_client_measurement():
    """hold_rns_clients() MEASURES which live units load RNS (2026-10-02).
    Unpinned, every test that calls it probes THIS host's running processes
    — ~3 s each and a verdict that depends on where the suite runs. Pin the
    measurement to Unobservable so callers exercise the curated-floor path
    deterministically; the measurement's own tests use
    ``_measure_rns_clients_impl`` with mocked inputs."""
    from utils.observation import Unobservable as _Unobs
    stub = lambda *a, **k: _Unobs("measurement pinned off in tests (conftest)")  # noqa: E731
    patched = []
    with ExitStack() as stack:
        for alias in ("utils.rnsd_restart_order", "src.utils.rnsd_restart_order"):
            try:
                stack.enter_context(patch(f"{alias}.measure_rns_clients", stub))
                patched.append(alias)
            except (ImportError, AttributeError, ModuleNotFoundError):
                continue
        if not patched:
            raise RuntimeError("rns client measurement pin patched NOTHING — "
                               "tests would probe this host's live processes")
        yield
