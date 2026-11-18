"""The lab tracer/echo leave NO temp dir behind after the process exits (2026-11-17).

Measured on the manager box 2026-11-17: 1,553 ``/tmp/meshforge_lab_tracer_*``
dirs (~50 MB of tmpfs = RAM, 144/day = one per 10-min tracer fire since 11-06)
and 39 ``meshforge_lab_echo_*``. Each held only ``lxmf/lxmf/{node_stats,
local_deliveries}``, every entry stamped the same millisecond — process exit.

Root cause, from a live no-transmit drill of a REAL RNS+LXMF client (atexit
calls traced): ``LXMRouter.exit_handler`` ran first and persisted into its
storagepath — re-creating the ``TemporaryDirectory`` already removed — then
``Reticulum.exit_handler`` reached ``os._exit(0)`` (seen in 1 of 2 drill runs),
so an atexit cleanup of ours is not guaranteed to run. (The first fix, an atexit rmtree registered early, passed
a fake without that os._exit and failed live at 19:10 — hence the fake below.)

These tests run the real code in a CHILD interpreter so real exit handling
happens, with fakes that reproduce BOTH halves of that order, and assert the
END state: the scratch TMPDIR holds no lab dir once the child is gone.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

_FAKES = textwrap.dedent("""
    import atexit, os, signal, sys, types
    from unittest.mock import MagicMock
    sys.path.insert(0, {src!r})

    class _Router:
        # What the real LXMRouter does: storagepath+"/lxmf", a GUARDED
        # exit_handler that persists there, registered with atexit.
        def __init__(self, storagepath=None, **kw):
            self.storagepath = storagepath + "/lxmf"
            self.exit_handler_running = False
            atexit.register(self.exit_handler)
            # ...and, like the real one (LXMRouter.py:290-291), takes over
            # SIGINT/SIGTERM: persist, then os._exit.
            signal.signal(signal.SIGTERM, self._sig)
            signal.signal(signal.SIGINT, self._sig)
            print("ROUTER_UP", flush=True)
        def _sig(self, signum, frame):
            self.exit_handler()
            os._exit(0)
        def exit_handler(self):
            if self.exit_handler_running:
                return
            self.exit_handler_running = True
            os.makedirs(self.storagepath, exist_ok=True)
            with open(self.storagepath + "/node_stats", "wb") as f:
                f.write(b"x" * 139)
        def __getattr__(self, name):
            return MagicMock()

    def _fake_init(configdir):
        # What the real Reticulum does at exit (observed live): os._exit, so
        # every atexit handler registered BEFORE it never runs.
        def _rns_exit():
            sys.stdout.flush(); sys.stderr.flush()
            os._exit(0)
        atexit.register(_rns_exit)
        return MagicMock()

    lxmf = types.ModuleType("LXMF")
    lxmf.LXMRouter = _Router
    lxmf.LXMessage = MagicMock()
    rns = MagicMock()
    rns.Transport.active_links = []
    rns.Transport.has_path.return_value = True
    sys.modules["LXMF"] = lxmf
    sys.modules["RNS"] = rns

    from lab import _lab_common as common
    common.init_reticulum_with_watchdog = _fake_init
    common.load_or_create_identity = lambda name: (MagicMock(), None)
""")

_TRACER = _FAKES + textwrap.dedent("""
    from lab import lxmf_tracer as t
    t.assert_rns_tx_allowed = lambda **kw: None
    t.run_trace([t.Peer(name="peer-a", hash_hex="0" * 32)],
                path_timeout_s=0.05, ack_timeout_s=0.05,
                path_retries=1, path_retry_backoff_s=0.0)
""")

_TRACER_TX_REFUSED = _FAKES + textwrap.dedent("""
    from lab import lxmf_tracer as t
    def _refuse(**kw):
        raise RuntimeError("tx guard: refused")
    t.assert_rns_tx_allowed = _refuse
    try:
        t.run_trace([t.Peer(name="peer-a", hash_hex="0" * 32)],
                    path_timeout_s=0.05, ack_timeout_s=0.05,
                    path_retries=1, path_retry_backoff_s=0.0)
    except RuntimeError:
        pass
""")

_ECHO_SIGTERM = _FAKES + textwrap.dedent("""
    from lab import lxmf_echo as e
    e.assert_rns_tx_allowed = lambda **kw: None
    rc = e.run_daemon(loglevel="WARNING", announce_interval_s=3600)
    print("ECHO_RC", rc, flush=True)
""")

_SYNTH = _FAKES + textwrap.dedent("""
    from lab import lxmf_multi_user_synth as m
    m.assert_rns_tx_allowed = lambda **kw: None
    m._resolve_path = lambda RNS, dest, timeout_s: False   # no sends
    m.time.sleep = lambda s: None
    m.run_synth([m.Peer(name="peer-a", hash_hex="0" * 32)], n_users=1,
                interval_s=0.01, duration_s=0.01, ack_timeout_s=0.01)
""")

_ECHO = _FAKES + textwrap.dedent("""
    from lab import lxmf_echo as e
    e.assert_rns_tx_allowed = lambda **kw: None
    e._EchoState.wait = lambda self, timeout: True   # one loop pass, then exit
    rc = e.run_daemon(loglevel="WARNING")
    assert rc == 0, rc
""")


def _run_child(script: str, tmp_path: Path) -> list:
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    env = dict(os.environ, TMPDIR=str(scratch), PYTHONDONTWRITEBYTECODE="1")
    env.pop("MESHFORGE_LAB_RNS_CONFIGDIR", None)   # production path: own tmpdir
    proc = subprocess.run(
        [sys.executable, "-c", script.format(src=str(SRC))],
        env=env, capture_output=True, text=True, timeout=25,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return sorted(p.name for p in scratch.iterdir())


def _child_env(tmp_path: Path):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    env = dict(os.environ, TMPDIR=str(scratch), PYTHONDONTWRITEBYTECODE="1")
    env.pop("MESHFORGE_LAB_RNS_CONFIGDIR", None)
    return scratch, env


def test_tracer_leaves_no_tmpdir_when_rns_exit_skips_atexit(tmp_path):
    left = _run_child(_TRACER, tmp_path)
    assert left == [], f"tracer leaked {left} — LXMF persisted after our cleanup"


def test_echo_leaves_no_tmpdir_on_clean_shutdown(tmp_path):
    left = _run_child(_ECHO, tmp_path)
    assert left == [], f"echo leaked {left} — LXMF persisted after our cleanup"


# ── the dead-owner sweep (cures every exit that skips the with) ──────────────

def _helper(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SRC))
    import tempfile
    from lab import _lab_common as common
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return common


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=10)
    return p.pid      # reaped: the kernel now says ESRCH (reuse would only KEEP)


def test_sweep_removes_dead_owner_dirs_and_keeps_live_and_foreign(monkeypatch, tmp_path):
    common = _helper(monkeypatch, tmp_path)
    dead = tmp_path / f"meshforge_lab_tracer_pid{_dead_pid()}-abc"
    (dead / "lxmf" / "lxmf").mkdir(parents=True)
    live = tmp_path / f"meshforge_lab_echo_pid{os.getpid()}-xyz"   # us: alive
    live.mkdir()
    legacy = tmp_path / "meshforge_lab_tracer_q9z1x_ab"        # pre-fix, FRESH: keep
    legacy.mkdir()
    other = tmp_path / f"meshforge_lab_echo_pid{_dead_pid()}-d"  # other prefix
    other.mkdir()
    assert common.sweep_dead_owner_dirs("meshforge_lab_tracer_") == 1
    assert not dead.exists()
    assert live.is_dir() and legacy.is_dir() and other.is_dir()
    assert common.sweep_dead_owner_dirs("meshforge_lab_echo_") == 1
    assert live.is_dir() and not other.exists()


def test_sweep_never_follows_a_symlink(monkeypatch, tmp_path):
    common = _helper(monkeypatch, tmp_path)
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep").write_text("x")
    link = tmp_path / f"meshforge_lab_tracer_pid{_dead_pid()}-l"
    link.symlink_to(target)
    assert common.sweep_dead_owner_dirs("meshforge_lab_tracer_") == 0
    assert (target / "keep").exists() and link.is_symlink()


def test_own_dir_carries_pid_and_goes_when_with_closes(monkeypatch, tmp_path):
    common = _helper(monkeypatch, tmp_path)
    with common.exit_scoped_tmpdir("meshforge_lab_tracer_") as d:
        assert Path(d).name.startswith(f"meshforge_lab_tracer_pid{os.getpid()}-")
        assert common.sweep_dead_owner_dirs("meshforge_lab_tracer_") == 0
    assert not Path(d).exists()


def test_flush_lxmf_router_tolerates_none_and_failure(monkeypatch, tmp_path):
    common = _helper(monkeypatch, tmp_path)

    class Boom:
        def exit_handler(self):
            raise RuntimeError("socket gone")
    common.flush_lxmf_router(None)
    common.flush_lxmf_router(Boom())          # logged, not raised


def test_tracer_leaves_no_tmpdir_when_the_tx_guard_refuses(tmp_path):
    """Review r1 F5: an exception after the router exists must still flush."""
    left = _run_child(_TRACER_TX_REFUSED, tmp_path)
    assert left == [], f"tracer leaked {left} on the refusal path"


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_echo_leaves_no_tmpdir_on_systemd_stop(tmp_path, sig):
    """Review r1 F1: LXMRouter takes SIGTERM/SIGINT and os._exits; echo takes
    both back (r2 M11: SIGINT was unpinned)."""
    scratch, env = _child_env(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", _ECHO_SIGTERM.format(src=str(SRC))],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        line = proc.stdout.readline()
        assert line.strip() == "ROUTER_UP", line + proc.stderr.read()[-2000:]
        time.sleep(0.3)                        # past the handler re-install
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert proc.returncode == 0, err[-2000:]
    assert "ECHO_RC 0" in out, (out, err[-2000:])
    left = sorted(p.name for p in scratch.iterdir())
    assert left == [], f"echo leaked {left} on {sig!r}"


def test_sweep_removes_day_old_prefix_dirs_only(monkeypatch, tmp_path):
    """The fleet backlog (pre-fix names, no PID) clears on deploy (review r1 F2)."""
    common = _helper(monkeypatch, tmp_path)
    old = tmp_path / "meshforge_lab_tracer_5z5xaf98"
    (old / "lxmf" / "lxmf").mkdir(parents=True)
    fresh = tmp_path / "meshforge_lab_tracer_wlbg6h03"
    fresh.mkdir()
    odd = tmp_path / "meshforge_lab_tracer_notmkdtemp-name"   # neither form: keep
    odd.mkdir()
    day_ago = time.time() - common.LEGACY_DIR_MIN_AGE_S - 60
    for p in (old, odd):
        os.utime(p, (day_ago, day_ago))
    # without an owner module, pre-fix dirs are never touched
    assert common.sweep_dead_owner_dirs("meshforge_lab_tracer_") == 0
    assert common.sweep_dead_owner_dirs(
        "meshforge_lab_tracer_", owner_module="lab.no_such_module_here") == 1
    assert not old.exists() and fresh.is_dir() and odd.is_dir()


def test_a_prefix_name_that_looks_like_a_pid_is_not_one(monkeypatch, tmp_path):
    """Review r1 F3: pre-fix '4821_xq' must never parse as PID 4821."""
    common = _helper(monkeypatch, tmp_path)
    looks = tmp_path / "meshforge_lab_tracer_4821_xqz"        # 8 chars, fresh
    looks.mkdir()
    assert common.sweep_dead_owner_dirs(
        "meshforge_lab_tracer_", owner_module="lab.no_such_module_here") == 0
    assert looks.is_dir()


def test_sweep_survives_absurd_pid_names(monkeypatch, tmp_path):
    """Review r1 F4: overflow / non-ASCII digits never crash the start."""
    common = _helper(monkeypatch, tmp_path)
    (tmp_path / "meshforge_lab_tracer_pid99999999999999999999-x").mkdir()
    (tmp_path / "meshforge_lab_tracer_pid\u00b2-x").mkdir()
    (tmp_path / "meshforge_lab_tracer_pid9999999999-x").mkdir()   # > pid_max
    common.sweep_dead_owner_dirs("meshforge_lab_tracer_")        # must not raise
    with common.exit_scoped_tmpdir("meshforge_lab_tracer_"):
        pass


def test_own_dir_removal_failure_is_witnessed(monkeypatch, tmp_path, caplog):
    """Review r1 F7: a failed removal is logged, never swallowed."""
    common = _helper(monkeypatch, tmp_path)

    def _boom(path, *a, **k):
        raise PermissionError(13, "denied", str(path))
    with common.exit_scoped_tmpdir("meshforge_lab_tracer_") as d:
        monkeypatch.setattr(common.shutil, "rmtree", _boom)
    assert Path(d).exists()
    assert any("could not remove own dir" in r.getMessage() for r in caplog.records)


def test_synth_leaves_no_tmpdir(tmp_path):
    """Review r2 F6: reverting synth to TemporaryDirectory passed every test."""
    left = _run_child(_SYNTH, tmp_path)
    assert left == [], f"synth leaked {left}"


def test_entry_sweeps_before_making_its_own_dir(monkeypatch, tmp_path):
    """Review r2 M01: the sweep's ONLY call site is the entry — pin it there."""
    common = _helper(monkeypatch, tmp_path)
    dead = tmp_path / f"meshforge_lab_tracer_pid{_dead_pid()}-zz"
    dead.mkdir()
    with common.exit_scoped_tmpdir("meshforge_lab_tracer_"):
        assert not dead.exists()


def test_prefix_dir_of_a_running_module_is_kept(monkeypatch, tmp_path):
    """Review r2 F1: a pre-fix echo's dir keeps its START mtime for days; age
    alone deleted a live one. Another process running the module = keep."""
    common = _helper(monkeypatch, tmp_path)
    live = tmp_path / "meshforge_lab_echo_m07esvz1"
    live.mkdir()
    day_ago = time.time() - common.LEGACY_DIR_MIN_AGE_S - 60
    os.utime(live, (day_ago, day_ago))
    holder = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "lab.fake_echo_mod"])
    try:
        assert common.sweep_dead_owner_dirs(
            "meshforge_lab_echo_", owner_module="lab.fake_echo_mod") == 0
        assert live.is_dir()
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert common.sweep_dead_owner_dirs(
        "meshforge_lab_echo_", owner_module="lab.fake_echo_mod") == 1
    assert not live.exists()


def test_legacy_rule_takes_only_mkdtemp_shaped_names(monkeypatch, tmp_path):
    """Review r2 F2/M07/M06: exactly 8 chars, never a 'pid' lead, a day old."""
    common = _helper(monkeypatch, tmp_path)
    names = {
        "pid12345": False,      # 8 chars, but the new form's lead
        "abcd1234x": False,     # 9 chars
        "abc_123": False,       # 7 chars
        "wdbkg85y": True,       # mkdtemp-shaped, a day old
    }
    day_ago = time.time() - common.LEGACY_DIR_MIN_AGE_S - 60
    for n in names:
        d = tmp_path / f"meshforge_lab_tracer_{n}"
        d.mkdir()
        os.utime(d, (day_ago, day_ago))
    two_h = tmp_path / "meshforge_lab_tracer_lvhxn9vi"
    two_h.mkdir()
    os.utime(two_h, (time.time() - 7200,) * 2)
    common.sweep_dead_owner_dirs("meshforge_lab_tracer_",
                                 owner_module="lab.no_such_module_here")
    for n, gone in names.items():
        assert (tmp_path / f"meshforge_lab_tracer_{n}").exists() is not gone, n
    assert two_h.is_dir(), "a 2 h old pre-fix dir is inside the floor"


def test_eperm_and_non_dirs_are_kept(monkeypatch, tmp_path):
    """Review r2 M02/M20: EPERM = alive; a FILE with a dead-pid name is kept."""
    common = _helper(monkeypatch, tmp_path)
    if os.getuid() != 0:
        assert common._pid_maybe_alive(1) is True        # EPERM (or alive)
    f = tmp_path / f"meshforge_lab_tracer_pid{_dead_pid()}-file"
    f.write_text("x")
    assert common.sweep_dead_owner_dirs("meshforge_lab_tracer_") == 0
    assert f.is_file()


# ── owner_module is DERIVED, never typed (review r3 F1) ─────────────────────

REPO = SRC.parent
_OWNERS = {
    "lab.lxmf_echo": "templates/systemd/meshforge-echo-user.service",
    "lab.lxmf_tracer": "templates/systemd/meshforge-tracer-user.service",
    "lab.lxmf_multi_user_synth": None,
}


def test_dash_m_sets_spec_name_to_the_dotted_module(tmp_path):
    """The mechanism: under `python -m pkg.mod`, __spec__.name == 'pkg.mod'."""
    pkg = tmp_path / "pkgx"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "modx.py").write_text('print(getattr(__spec__, "name", None))\n')
    out = subprocess.run([sys.executable, "-m", "pkgx.modx"], cwd=str(tmp_path),
                         capture_output=True, text=True, timeout=20)
    assert out.stdout.strip() == "pkgx.modx", (out.stdout, out.stderr)


@pytest.mark.parametrize("module", sorted(_OWNERS))
def test_owner_module_is_the_modules_own_name(monkeypatch, module):
    monkeypatch.syspath_prepend(str(SRC))
    import importlib
    mod = importlib.import_module(module)
    assert mod._OWNER_MODULE == module
    src = Path(mod.__file__).read_text()
    assert src.count("owner_module=_OWNER_MODULE") == 1, "call site must pass it"
    assert 'owner_module="' not in src, "a typed literal can drift — derive it"
    unit = _OWNERS[module]
    if unit:
        text = (REPO / unit).read_text()
        assert f"-m {module}\n" in text or f"-m {module} " in text, unit
