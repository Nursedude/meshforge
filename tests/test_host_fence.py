"""The tests/conftest.py HOST FENCE must actually refuse (2026-09-27).

That day a mutation drill's mutants ran REAL `sudo systemctl stop/start/
restart rnsd` on the manager box twice (mid CR A/B experiment), and — once a
sudo-only fence existed — a mutant's non-systemd fallback `pkill -x rnsd`
killed the live rnsd anyway (it runs as the operator's user). A guard that
has never been seen to refuse is not evidence; these tests make it refuse.
"""

import os
import queue
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

import pytest

import tests.host_fence as host_fence
from tests.host_fence import (_FENCE_PREFIX, _install_sudo_fence, _real_systemctl,
                              _shared_fence_path, _shims)


def _run(argv, env=None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=20, env=env)


@pytest.fixture
def stub_systemctl(tmp_path):
    """An env whose MF_REAL_SYSTEMCTL is an echo stub: what the shim passes
    through is printed, never executed."""
    stub = tmp_path / "real-systemctl-stub"
    stub.write_text('#!/bin/sh\necho "REAL-REACHED $*"\n')
    stub.chmod(0o755)
    return dict(os.environ, MF_REAL_SYSTEMCTL=str(stub))


def test_fence_is_installed_first_on_path():
    d = os.environ["PATH"].split(os.pathsep)[0]
    assert "mf-host-fence-" in d
    assert shutil.which("sudo").startswith(d)
    assert shutil.which("systemctl").startswith(d)


def test_sudo_is_refused():
    r = _run(["sudo", "-n", "systemctl", "restart", "rnsd"])
    assert r.returncode != 0
    assert "refused under pytest" in r.stderr


@pytest.mark.parametrize("verb", ["stop", "start", "restart", "daemon-reload"])
def test_systemctl_mutating_verbs_are_refused(verb):
    r = _run(["systemctl", verb, "rnsd"])
    assert r.returncode == 1
    assert "refused under pytest" in r.stderr


def test_systemctl_user_mutations_are_refused_too():
    r = _run(["systemctl", "--user", "stop", "nomadnet"])
    assert r.returncode == 1 and "refused under pytest" in r.stderr


def test_systemctl_read_only_passes_through():
    # a real answer from the real binary, not the fence's refusal
    r = _run(["systemctl", "is-active", "no-such-unit-mf-fence-test.service"])
    assert "refused" not in r.stderr
    assert r.stdout.strip() in ("inactive", "unknown")


@pytest.mark.parametrize("name", ["pkill", "killall", "rnsd", "reboot",
                                  "kill", "systemd-run", "busctl", "dbus-send",
                                  "halt", "init", "telinit", "loginctl"])
def test_killers_and_daemons_are_refused(name):
    """SAFE BY CONSTRUCTION (2026-11-20, live): this test used to EXEC `name` via
    PATH — so wherever `name` was not fenced (a red check against older code, a
    mutant) it ran the REAL binary. A red check ran the real `halt` on the lab
    Pi; only polkit ("Interactive authentication required") stopped it. Now: first
    prove PATH resolves `name` INTO the fence WITHOUT executing anything, then
    exec the shim by its absolute fence path — never a PATH lookup."""
    fence = os.environ["PATH"].split(os.pathsep)[0]
    resolved = shutil.which(name)
    assert resolved == os.path.join(fence, name), (
        f"{name} resolves to {resolved!r}, not the fence — NOT executing it")
    if name == "kill":
        args = ["-TERM", "1"]                  # the exec'd /bin/kill, not the builtin
    elif name == "systemd-run":
        args = ["--user", "/bin/true"]
    elif name in ("pkill", "killall"):
        args = ["-x", "rnsd"]
    else:
        args = []
    r = _run([resolved, *args])
    assert r.returncode == 1 and "refused under pytest" in r.stderr


def test_sudo_of_a_pytest_tmp_stub_runs_unprivileged(tmp_path):
    stub = tmp_path / "stub.sh"
    stub.write_text("#!/bin/sh\nid -u\n")
    stub.chmod(0o755)
    r = _run(["sudo", "-n", str(stub)])
    assert r.returncode == 0
    assert r.stdout.strip() == str(os.getuid())  # NOT root


# ── ONE shared fence, never deleted (2026-11-20) ─────────────────────────────
# Drilled 11-20 against a 2× green control: SIGTERM (`timeout`'s default),
# SIGKILL and pytest-timeout's `thread` method each leaked one PER-RUN fence dir
# (3,478 in tmpfs /tmp). Four review rounds of a reaper each found a live process
# it could not see — and every miss handed that process the REAL sudo/pkill. The
# fence is now one dir per (uid, shim content) that no code path removes. These
# tests drive REAL child pytests (the conftest loads as a plugin; the probe lives
# in tmp_path; the child's TMPDIR is a scratch dir, so counts are exact and the
# box's /tmp is never touched).

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _child(tmpdir, body, probe_dir):
    probe = probe_dir / "test_probe.py"
    probe.write_text(body)
    env = dict(os.environ, TMPDIR=str(tmpdir))
    argv = [sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider",
            "-p", "tests.conftest", "-c", os.path.join(ROOT, "pyproject.toml"),
            "--rootdir", ROOT, str(probe)]
    p = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l) for l in p.stdout], daemon=True).start()
    return p, lines


def _read_keys(lines, keys, deadline_s=15):
    """Wait for KEY=value lines; a REAL second exit (never blocks past it)."""
    seen, end = {}, time.monotonic() + deadline_s
    while len(seen) < len(keys) and time.monotonic() < end:
        try:
            line = lines.get(timeout=max(0.05, end - time.monotonic()))
        except queue.Empty:
            break
        for k in keys:
            if k + "=" in line:
                seen[k] = line.split(k + "=", 1)[1].strip()
    return seen


def _drain(lines):
    out = []
    while True:
        try:
            out.append(lines.get_nowait())
        except queue.Empty:
            return "".join(out)


def _end(p):
    if p.poll() is None:
        p.kill()
    p.wait(timeout=10)
    p.stdout.close()


def _fence_entries(tmpdir):
    return sorted(n for n in os.listdir(tmpdir) if n.startswith(_FENCE_PREFIX))


_REPORT = ("import os, subprocess, time\n"
           "def test_report():\n"
           "    print('FENCE=' + os.environ['PATH'].split(os.pathsep)[0], flush=True)\n"
           "    r = subprocess.run(['systemctl', 'is-active', 'no-such-unit-mf-fence.service'],\n"
           "                       capture_output=True, text=True, timeout=5)\n"
           "    print('SYSTEMCTL=' + r.stdout.strip(), flush=True)\n"
           "    time.sleep(%s)\n")


def test_this_run_uses_the_shared_fence():
    d = os.environ["PATH"].split(os.pathsep)[0]
    assert d == _shared_fence_path(os.path.dirname(d)), (
        f"expected the shared fence, got {d} (a ⚠️ fallback line in the header says why)")


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_a_killed_run_leaks_nothing(tmp_path, sig):
    """END-STATE: kill a real pytest the ways that leaked; nothing new remains."""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    p, lines = _child(tmpdir, _REPORT % 60, tmp_path)
    try:
        seen = _read_keys(lines, ("FENCE", "SYSTEMCTL"))
        assert seen.get("FENCE") == _shared_fence_path(str(tmpdir)), seen
        # nested run: its read-only systemctl must reach the REAL binary (it used
        # to exec the parent's fence script forever — review 11-20)
        assert seen.get("SYSTEMCTL") in ("inactive", "unknown"), seen
        os.kill(p.pid, sig)
        p.wait(timeout=10)
    finally:
        _end(p)
    assert _fence_entries(tmpdir) == [os.path.basename(_shared_fence_path(str(tmpdir)))]
    p2, lines2 = _child(tmpdir, _REPORT % 0, tmp_path)       # the NEXT run
    try:
        assert p2.wait(timeout=20) == 0, _drain(lines2)
    finally:
        _end(p2)
    assert _fence_entries(tmpdir) == [os.path.basename(_shared_fence_path(str(tmpdir)))], (
        "every run shares ONE fence dir")


def test_an_orphan_keeps_its_fence_after_any_number_of_runs(tmp_path):
    """Fable r2/r4 class, end-state: a killed run's orphan (exec'd, or a fork
    without exec) still resolves the FENCE sudo after later runs come and go."""
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    p, lines = _child(tmpdir,
        "import os, subprocess, time, shutil\n"
        "def test_leave_children():\n"
        "    o = subprocess.Popen(['sleep', '30'], start_new_session=True,\n"
        "                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
        "                         stderr=subprocess.DEVNULL)\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        os.setsid()\n"
        "        time.sleep(30)\n"
        "        os._exit(0)\n"
        "    print('ORPHANS=%d,%d' % (o.pid, pid), flush=True)\n"
        "    print('FENCE=' + os.environ['PATH'].split(os.pathsep)[0], flush=True)\n"
        "    time.sleep(60)\n", tmp_path)
    pids = []
    try:
        seen = _read_keys(lines, ("ORPHANS", "FENCE"))
        pids = [int(x) for x in seen["ORPHANS"].split(",")]
        fence = seen["FENCE"]
        os.kill(p.pid, signal.SIGKILL)
        p.wait(timeout=10)
        for _ in range(2):                                    # later runs, clean exits
            q, ql = _child(tmpdir, _REPORT % 0, tmp_path)
            try:
                assert q.wait(timeout=20) == 0, _drain(ql)
            finally:
                _end(q)
        assert all(os.path.exists(f"/proc/{x}") for x in pids), "orphans must still live"
        sudo = os.path.join(fence, "sudo")
        r = subprocess.run([sudo, "-n", "true"], capture_output=True, text=True,
                           timeout=10, env=dict(os.environ, PATH=fence + ":/usr/bin:/bin"))
        assert r.returncode == 1 and "refused under pytest" in r.stderr, (
            "an orphan's fence must still REFUSE")
    finally:
        for x in pids:
            try:
                os.kill(x, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _end(p)


def test_concurrent_first_starts_share_one_dir(tmp_path):
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    for i in range(4):
        (tmp_path / f"p{i}").mkdir()
    kids = [_child(tmpdir, _REPORT % 0, tmp_path / f"p{i}") for i in range(4)]
    try:
        for p, lines in kids:
            assert p.wait(timeout=30) == 0, _drain(lines)
    finally:
        for p, _ in kids:
            _end(p)
    shared = _shared_fence_path(str(tmpdir))
    assert _fence_entries(tmpdir) == [os.path.basename(shared)]
    left = [n for n in os.listdir(shared) if n.startswith(".shim-")]
    assert left == [], f"atomic-write temp files left behind: {left}"
    for name, body in _shims().items():
        assert host_fence._shim_is_correct(os.path.join(shared, name), body), name


def _install_into(tmp_path, monkeypatch):
    """Run _install_sudo_fence() against a scratch TMPDIR, env restored after."""
    for k in ("PATH", "MF_REAL_SYSTEMCTL", "MF_SUDO_FENCE_LOG", "MF_SUDO_FENCE_TMP"):
        monkeypatch.setenv(k, os.environ.get(k, ""))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr(host_fence, "_FENCE_STATS", dict(host_fence._FENCE_STATS))
    return _install_sudo_fence()


def test_a_damaged_shim_is_repaired_in_place(tmp_path, monkeypatch):
    shared = _shared_fence_path(str(tmp_path))
    os.mkdir(shared, 0o755)
    with open(os.path.join(shared, "sudo"), "w") as fh:
        fh.write("#!/bin/sh\nexec /usr/bin/sudo \"$@\"\n")     # a WRONG shim
    os.chmod(os.path.join(shared, "sudo"), 0o755)
    ino = os.stat(shared).st_ino
    d, _log, is_shared = _install_into(tmp_path, monkeypatch)
    assert (d, is_shared) == (shared, True) and os.stat(shared).st_ino == ino, (
        "repair happens IN PLACE: the dir is never replaced")
    assert host_fence._FENCE_STATS["repaired"] == len(_shims())  # all were absent/wrong
    assert host_fence._shim_is_correct(os.path.join(shared, "sudo"), _shims()["sudo"])


@pytest.mark.parametrize("make_unsafe", ["symlink", "group_writable"])
def test_an_unsafe_shared_path_is_refused_and_said(tmp_path, monkeypatch, make_unsafe):
    shared = _shared_fence_path(str(tmp_path))
    if make_unsafe == "symlink":
        (tmp_path / "elsewhere").mkdir()
        os.symlink(tmp_path / "elsewhere", shared)
    else:
        os.mkdir(shared)
        os.chmod(shared, 0o777)
    d, _log, is_shared = _install_into(tmp_path, monkeypatch)
    assert not is_shared and d != shared and os.path.basename(d).startswith(_FENCE_PREFIX)
    assert "not ours or not safe" in host_fence._FENCE_STATS["fallback"]
    r = subprocess.run([os.path.join(d, "sudo"), "-n", "true"], capture_output=True,
                       text=True, timeout=10)
    assert r.returncode == 1 and "refused under pytest" in r.stderr
    if make_unsafe == "symlink":
        assert os.listdir(tmp_path / "elsewhere") == [], "never wrote through the link"


def test_legacy_dirs_are_counted_never_touched(tmp_path, monkeypatch):
    for n in ("abcd1234", "u1x2y3z4"):                        # 'u…' must not pass as shared
        (tmp_path / (_FENCE_PREFIX + n)).mkdir()
    _install_into(tmp_path, monkeypatch)
    assert host_fence._FENCE_STATS["legacy"] == 2
    assert (tmp_path / (_FENCE_PREFIX + "abcd1234")).is_dir()


def test_only_stale_logs_and_temp_files_are_pruned(tmp_path, monkeypatch):
    shared = _shared_fence_path(str(tmp_path))
    os.mkdir(shared, 0o755)
    old = time.time() - host_fence._LOG_MAX_AGE_S - 60
    for n in ("calls-1-aa.log", ".shim-zz"):
        p = os.path.join(shared, n)
        open(p, "w").close()
        os.utime(p, (old, old))
    open(os.path.join(shared, "calls-2-bb.log"), "w").close()  # fresh: kept
    _install_into(tmp_path, monkeypatch)
    names = set(os.listdir(shared))
    assert "calls-1-aa.log" not in names and ".shim-zz" not in names
    assert "calls-2-bb.log" in names and "sudo" in names


def test_real_systemctl_never_resolves_to_a_fence(tmp_path, monkeypatch):
    fake = tmp_path / (_FENCE_PREFIX + "parent")
    fake.mkdir()
    (fake / "systemctl").write_text("#!/bin/sh\n")
    (fake / "systemctl").chmod(0o755)
    monkeypatch.setenv("MF_REAL_SYSTEMCTL", str(fake / "systemctl"))
    monkeypatch.setenv("PATH", str(fake) + os.pathsep + os.environ["PATH"])
    assert _FENCE_PREFIX not in _real_systemctl()


def test_no_code_path_removes_a_fence_dir():
    """The invariant the cure rests on, pinned at the source: host_fence.py may
    rmtree only the fallback dir it JUST created on a failed write, and the
    conftest never removes a fence dir at all."""
    import ast
    import inspect
    import tests.conftest as conftest
    src = inspect.getsource(host_fence)
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") in ("rmtree", "rmdir", "rename", "removedirs")]
    assert len(calls) == 1 and calls[0].func.attr == "rmtree", ast.dump(calls[0]) if calls else calls
    csrc = inspect.getsource(conftest.pytest_unconfigure)
    assert "rmtree" not in csrc and "rmdir" not in csrc


# ── Fable r5 (2026-11-20) hardening ──────────────────────────────────────────

def test_sudo_with_no_tmp_prefix_refuses_an_absolute_path(tmp_path):
    """An env-scrubbed child (MF_SUDO_FENCE_TMP unset) used to exec ANY argv:
    `sudo -n /usr/bin/pkill …` ran the real pkill."""
    fence = os.environ["PATH"].split(os.pathsep)[0]
    decoy = tmp_path / "decoy"
    decoy.write_text("#!/bin/sh\necho REAL-DECOY\nexit 42\n")
    decoy.chmod(0o755)
    for env in ({"PATH": fence + ":/usr/bin:/bin"},
                {"PATH": fence + ":/usr/bin:/bin", "MF_SUDO_FENCE_TMP": ""}):
        r = subprocess.run([os.path.join(fence, "sudo"), "-n", str(decoy)],
                           capture_output=True, text=True, timeout=10, env=env)
        assert r.returncode == 1 and "REAL-DECOY" not in r.stdout, (env, r.stdout)


@pytest.mark.parametrize("argv", [
    # r5 + r6 misses of the old denylist, and a verb that does not exist yet
    ["condrestart"], ["force-reload"], ["reenable"], ["exit"], ["freeze"],
    ["soft-reboot"], ["set-default"], ["set-environment"], ["emergency"],
    ["sleep"], ["cancel"], ["log-level", "debug"], ["service-watchdogs", "off"],
    ["condreload"], ["reload-or-try-restart"], ["frobnicate-2027"],
    # option forms in front of the verb
    ["--", "restart"], ["-H", "host", "restart"], ["--now", "enable"],
    ["-p", "show", "restart"], ["--signal=KILL", "kill"],
    # Fable r7: clusters / abbreviations / unknown options hiding the verb
    ["-qp", "show", "restart"], ["-at", "status", "restart"],
    ["--prop", "status", "restart"], ["--stat", "status", "stop"],
    ["-C", "status", "restart"], ["--capsule", "show", "restart"],
    ["--kill-who", "status", "kill"], ["--legend", "status", "restart"],
    ["-pActiveState", "restart"], ["", "restart"],
])
def test_any_non_read_only_systemctl_call_is_refused(argv, stub_systemctl):
    """Driven against an ECHO STUB: a shim regression can never reach the real
    user manager with a mutating verb (Fable r7 nit), and 'reached' is exact."""
    r = _run(["systemctl", "--user", *argv, "no-such-unit-mf-fence.service"],
             env=stub_systemctl)
    assert "REAL-REACHED" not in r.stdout, (argv, r.stdout)
    assert r.returncode == 1 and "refused under pytest" in r.stderr, (argv, r.stderr)


_U = "no-such-unit-mf-fence.service"


@pytest.mark.parametrize("argv", [
    ["is-active", _U], ["--user", "is-enabled", _U], ["-p", "ActiveState", "show", _U],
    ["--no-pager", "status", _U], ["list-units", "--no-legend"], ["--version"],
    ["--user", "--failed"], ["--property=ActiveState", "--value", "show", _U],
    ["-H", "somehost", "is-active", _U], ["--state=failed,running", "list-units"],
    ["--", "status", _U],
])
def test_read_only_systemctl_reaches_the_real_binary_exactly(argv, stub_systemctl):
    r = _run(["systemctl", *argv], env=stub_systemctl)
    assert r.stdout.strip() == "REAL-REACHED " + " ".join(argv), (argv, r.stdout, r.stderr)


def test_read_only_passthrough_reaches_the_real_systemctl():
    """One call through the REAL binary (read-only) so the stub tests above are
    anchored to the thing itself."""
    r = _run(["systemctl", "is-active", "no-such-unit-mf-fence.service"])
    assert "refused" not in r.stderr and r.stdout.strip() in ("inactive", "unknown")


def test_sudo_tmp_allowance_cannot_be_escaped_with_dotdot():
    import tempfile
    prefix = os.environ["MF_SUDO_FENCE_TMP"]
    # the decoy must sit OUTSIDE pytest's tree (tmp_path is inside the allowance)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(prefix)) as outside:
        decoy = os.path.join(outside, "decoy")
        with open(decoy, "w") as fh:
            fh.write("#!/bin/sh\necho REAL-DECOY\nexit 42\n")
        os.chmod(decoy, 0o755)
        assert not os.path.realpath(decoy).startswith(prefix)
        escape = prefix + "x" + "/.." * 12 + decoy
        r = _run(["sudo", "-n", escape])
    assert r.returncode == 1 and "REAL-DECOY" not in r.stdout, r.stdout


@pytest.mark.parametrize("spelling", ["dot", "symlink"])
def test_real_systemctl_resolves_fence_spellings(tmp_path, monkeypatch, spelling):
    fence = os.environ["PATH"].split(os.pathsep)[0]
    if spelling == "dot":
        entry = fence + "/."
    else:
        entry = str(tmp_path / "link")
        os.symlink(fence, entry)
    monkeypatch.delenv("MF_REAL_SYSTEMCTL", raising=False)
    monkeypatch.setenv("PATH", entry + os.pathsep + os.environ["PATH"])
    got = _real_systemctl()
    assert os.path.realpath(os.path.dirname(got)) != os.path.realpath(fence), got


def test_an_older_version_shared_dir_is_counted(tmp_path, monkeypatch):
    (tmp_path / f"{_FENCE_PREFIX}u{os.getuid()}-000000000000").mkdir()
    _install_into(tmp_path, monkeypatch)
    assert host_fence._FENCE_STATS["legacy"] == 1
    assert host_fence._FENCE_STATS["tmpdir"] == str(tmp_path)


def test_first_creation_says_created_not_repaired(tmp_path, monkeypatch):
    d, _log, is_shared = _install_into(tmp_path, monkeypatch)
    assert is_shared
    assert host_fence._FENCE_STATS["created"] == len(_shims())
    assert host_fence._FENCE_STATS["repaired"] == 0


def test_every_start_refreshes_shim_timestamps(tmp_path, monkeypatch):
    """/tmp's 10-day tmpfiles age-out must never see an idle shim while pytest runs."""
    d, _log, _ = _install_into(tmp_path, monkeypatch)
    old = time.time() - 20 * 86400
    for name in _shims():
        os.utime(os.path.join(d, name), (old, old))
    _install_into(tmp_path, monkeypatch)
    for name in _shims():
        assert time.time() - os.stat(os.path.join(d, name)).st_mtime < 60, name


def test_a_hardlinked_shim_is_replaced(tmp_path, monkeypatch):
    d, _log, _ = _install_into(tmp_path, monkeypatch)
    os.link(os.path.join(d, "sudo"), tmp_path / "other-name")
    _install_into(tmp_path, monkeypatch)
    assert os.stat(os.path.join(d, "sudo")).st_nlink == 1
    assert host_fence._FENCE_STATS["repaired"] == 1


def test_an_unrepairable_shared_dir_falls_back_and_says_how_to_fix(tmp_path, monkeypatch):
    shared = _shared_fence_path(str(tmp_path))
    os.mkdir(shared, 0o755)
    os.chmod(shared, 0o755)                                   # umask-proof: SAFE but…
    os.mkdir(os.path.join(shared, "sudo"))                    # …a DIR at a shim name
    d, _log, is_shared = _install_into(tmp_path, monkeypatch)
    assert not is_shared and d != shared
    msg = host_fence._FENCE_STATS["fallback"]
    assert "cannot repair" in msg and "rm -rf" in msg, msg
    assert host_fence._FENCE_STATS["legacy"] == 0, "its own fallback dir is not 'legacy'"
    r = subprocess.run([os.path.join(d, "sudo"), "-n", "true"], capture_output=True,
                       text=True, timeout=10)
    assert r.returncode == 1 and "refused under pytest" in r.stderr


def test_an_unknown_entry_in_the_shared_dir_is_reported(tmp_path, monkeypatch):
    d, _log, _ = _install_into(tmp_path, monkeypatch)
    stub = os.path.join(d, "sh")
    with open(stub, "w") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(stub, 0o755)
    _install_into(tmp_path, monkeypatch)
    assert host_fence._FENCE_STATS["unknown"] == ["sh"]


def test_sudo_bare_name_refuses_even_with_cwd_in_pytest_tmp(tmp_path):
    """Fable r8: with cwd inside pytest's tmp tree, `sudo systemctl …` resolved
    `systemctl` against cwd (exec'd a file there, or 127) instead of refusing."""
    stub = tmp_path / "systemctl"
    stub.write_text("#!/bin/sh\necho CWD-RAN\n")
    stub.chmod(0o755)
    for argv in (["sudo", "-n", "systemctl", "restart", "rnsd"], ["sudo", "-n", "true"]):
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10, cwd=tmp_path)
        assert r.returncode == 1 and "refused under pytest" in r.stderr, (argv, r)
        assert "CWD-RAN" not in r.stdout
    # the allowance itself still works for an explicit path
    r = subprocess.run(["sudo", "-n", "./systemctl"], capture_output=True, text=True,
                       timeout=10, cwd=tmp_path)
    assert r.returncode == 0 and "CWD-RAN" in r.stdout


def test_clear_command_cannot_match_its_own_shell():
    """Live 2026-11-20: the printed command declined forever because its echo
    text carried the word pgrep was gating on — it matched its own `bash -c`."""
    cmd = host_fence._fence_clear_command("/tmp", "mf-host-fence-u1-0123456789ab")
    assert "pytest" not in cmd.replace("[p]ytest", ""), cmd
    assert "! -name 'mf-host-fence-u1-0123456789ab'" in cmd and "-mmin +1440" in cmd


@pytest.mark.parametrize("argv", [["sudo", "-u"], ["sudo", "-g"], ["sudo", "-n", "-u"]])
def test_sudo_with_a_dangling_value_option_refuses_cleanly(argv):
    """Fable r8: a bare `-u` died on `shift 2` (rc 2, dash's shift error) —
    fail-closed, but not the refusal a caller is written to recognise."""
    r = _run(argv)
    assert r.returncode == 1 and "refused under pytest" in r.stderr, (argv, r)
