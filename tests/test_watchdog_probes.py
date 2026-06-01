"""Tests for src/utils/watchdog_probes.py and watchdog_runner.py.

Regression-pinned against the documented Issue shapes
(``.claude/foundations/persistent_issues.md``):

* Issue #61 — socketserver-deadlock — HTTP local probe must surface a
  bound-but-wedged port as a wedge signal.
* Issue #63 — delivery_counters write canary — probe must surface
  ``preflight_ok=False`` as wedge and ``consecutive_write_errors >= N``
  as degraded.
* Issue #68 — main-thread unix_stream_connect wedge — probe must match
  kernel-stack patterns and NOT match the healthy ``do_sys_poll`` shape.
* Issue #69 — foreign daemon owns ``@rns/<instance>`` — probe must match
  the actual ss output shape from the 2026-05-20 incident.

Plus exercise the edge-transition tracker and atomic-rename writer.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.watchdog_probes import (  # noqa: E402
    SEVERITIES,
    SIGNAL_CLASSES,
    Signal,
    probe_delivery_write_canary,
    probe_fd_exhaustion,
    probe_foundation_drift,
    probe_parity_drift,
    probe_rns_version_drift,
    probe_http_local,
    probe_lxmf_process_wedge,
    probe_main_thread_wedge,
    probe_rns_interface_down_peer_reachable,
    probe_rns_namespace_collision,
    probe_rns_rpc_responsive,
    probe_rns_shared_instance_responsive,
    probe_service_inactive,
    probe_tracer_peer_unreachable,
    signal_to_dict,
)
from utils.watchdog_runner import (  # noqa: E402
    SignalTracker,
    load_config_file,
    resolve_probe_targets,
    write_state,
    _DEFAULT_SERVICES_EXPECTED_ACTIVE,
    _DEFAULT_SERVICES_WEDGE_CHECK,
)


# ─────────────────────────────────────────────────────────────────────
# Closed enum + Signal shape
# ─────────────────────────────────────────────────────────────────────


def test_signal_classes_closed_enum_is_documented():
    """Every class in the enum must be one of the documented classes.
    Adding a class is a deliberate act — bumps this test AND requires
    a persistent_issues.md entry."""
    assert set(SIGNAL_CLASSES) == {
        "rns_namespace_collision",
        "main_thread_wedge",
        "http_local_unresponsive",
        "delivery_write_canary",
        "service_inactive",
        "tracer_peer_unreachable",
        "rns_shared_instance_unresponsive",
        "rns_interface_down_peer_reachable",
        "rns_rpc_unresponsive",
        "fd_exhaustion",
        "foundation_perms_drift",
        "parity_drift",
        "rns_version_drift",
    }
    assert set(SEVERITIES) == {"info", "degraded", "wedge"}


def test_signal_key_is_class_subject_tuple():
    sig = Signal(cls="rns_namespace_collision", subject="@rns/default",
                 severity="wedge", detail="x")
    assert sig.key() == ("rns_namespace_collision", "@rns/default")


def test_signal_to_dict_includes_first_seen():
    sig = Signal(cls="main_thread_wedge", subject="meshforge-map.service",
                 severity="wedge", detail="x", issue_ref=68,
                 extra={"pid": 1234})
    out = signal_to_dict(sig, first_seen_ts=1779480000.5)
    assert out["first_seen"] == 1779480000.5
    assert out["class"] == "main_thread_wedge"
    assert out["issue_ref"] == 68
    assert out["extra"]["pid"] == 1234


# ─────────────────────────────────────────────────────────────────────
# Issue #69 reconstruction — rns_namespace_collision
# ─────────────────────────────────────────────────────────────────────


# Real ss -xnpl output shape from the 2026-05-20 Issue #69 incident.
# rnsd-shaped line first, then the rogue meshanchor-daemon line.
_SS_RNSD_OWNED = """\
Netid State    Recv-Q Send-Q Local Address:Port Peer Address:Port Process
u_str LISTEN   0      0      @rns/volcano 12345 * 0 users:(("rnsd",pid=2286820,fd=4))
u_str LISTEN   0      0      @rns/volcano/rpc 67890 * 0 users:(("rnsd",pid=2286820,fd=11))
"""

_SS_FOREIGN_OWNED = """\
Netid State    Recv-Q Send-Q Local Address:Port Peer Address:Port Process
u_str LISTEN   0      0      @rns/volcano 99999 * 0 users:(("python3",pid=200825,fd=4))
"""


def _make_subprocess_mock(stdout, returncode=0):
    """Helper: produce a subprocess.run mock that returns the given stdout."""
    class _Result:
        def __init__(self):
            self.stdout = stdout
            self.returncode = returncode
    def _runner(*args, **kwargs):
        return _Result()
    return _runner


def test_rns_collision_returns_none_when_rnsd_owns_listener(tmp_path):
    fake_proc = tmp_path / "1"
    fake_proc.mkdir()
    (fake_proc / "cmdline").write_bytes(
        b"/usr/bin/python3\x00/opt/rnsd-bin/rnsd\x00"
    )
    # Override pid in our test: rewrite SS output to use pid=1
    ss_out = _SS_RNSD_OWNED.replace("2286820", "1")
    with patch("utils.watchdog_probes.subprocess.run",
               side_effect=_make_subprocess_mock(ss_out)):
        sig = probe_rns_namespace_collision(
            "volcano", proc_root=str(tmp_path),
        )
    assert sig is None


def test_rns_collision_fires_on_foreign_daemon(tmp_path):
    """Issue #69 reconstruction: meshanchor-daemon claimed @rns/volcano."""
    fake_proc = tmp_path / "1"
    fake_proc.mkdir()
    (fake_proc / "cmdline").write_bytes(
        b"/usr/bin/python3\x00/opt/meshanchor/src/daemon.py\x00start\x00"
        b"--foreground\x00"
    )
    ss_out = _SS_FOREIGN_OWNED.replace("200825", "1")
    with patch("utils.watchdog_probes.subprocess.run",
               side_effect=_make_subprocess_mock(ss_out)):
        sig = probe_rns_namespace_collision(
            "volcano", proc_root=str(tmp_path),
        )
    assert sig is not None
    assert sig.cls == "rns_namespace_collision"
    assert sig.severity == "wedge"
    assert sig.issue_ref == 69
    assert "foreign daemon" in sig.detail
    assert "meshanchor" in sig.detail
    assert "sudo kill 1" in sig.detail


def test_rns_collision_returns_none_when_no_listener():
    """No ss output line matches @rns/<instance> → nothing to check."""
    with patch("utils.watchdog_probes.subprocess.run",
               side_effect=_make_subprocess_mock(
                   "Netid State Local Address:Port\n")):
        sig = probe_rns_namespace_collision("volcano")
    assert sig is None


def test_rns_collision_returns_none_when_ss_unavailable():
    """Don't false-alarm when ss is missing or hangs."""
    import subprocess as sp
    def _raise(*args, **kwargs):
        raise FileNotFoundError("ss")
    with patch("utils.watchdog_probes.subprocess.run", side_effect=_raise):
        sig = probe_rns_namespace_collision("volcano")
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# Issue #68 reconstruction — main_thread_wedge
# ─────────────────────────────────────────────────────────────────────


# Real kernel stack from moc1's 2026-05-20 wedge (verbatim from
# persistent_issues.md Issue #68).
_WEDGE_STACK = """\
[<0>] do_sys_poll+0x3b8/0x540
[<0>] unix_wait_for_peer+0x80/0xd0
[<0>] unix_stream_connect+0xa0/0x4f0
[<0>] __sock_recvmsg+0x60/0x90
[<0>] el0_svc+0x30/0xf8
"""

# Real healthy stack from a serving meshforge-map (this morning's
# verification on moc1 — same shape do_sys_poll, no Unix-connect site).
_HEALTHY_STACK = """\
[<0>] do_sys_poll+0x3b8/0x540
[<0>] __arm64_sys_ppoll+0xb4/0x148
[<0>] invoke_syscall+0x50/0x120
[<0>] el0_svc_common.constprop.0+0x48/0xf0
[<0>] do_el0_svc+0x24/0x38
[<0>] el0_svc+0x30/0xf8
"""


def test_main_thread_wedge_fires_on_unix_stream_connect(tmp_path):
    """Issue #68 reconstruction: main thread blocked in unix_wait_for_peer."""
    pid = 12345
    proc_dir = tmp_path / str(pid) / "task" / str(pid)
    proc_dir.mkdir(parents=True)
    (proc_dir / "stack").write_text(_WEDGE_STACK)
    sig = probe_main_thread_wedge(
        "meshforge-map.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is not None
    assert sig.cls == "main_thread_wedge"
    assert sig.severity == "wedge"
    assert sig.issue_ref == 68
    assert sig.extra["pid"] == pid
    # Should match the first wedge pattern in the stack, not a later one
    assert sig.extra["pattern"] in (
        "unix_wait_for_peer", "unix_stream_connect", "do_unix_stream_connect",
    )


def test_main_thread_wedge_does_not_fire_on_healthy_poll(tmp_path):
    """A healthy HTTP server spends its main-thread time in do_sys_poll.
    Must NOT false-alarm — the operator stops trusting the panel."""
    pid = 12345
    proc_dir = tmp_path / str(pid) / "task" / str(pid)
    proc_dir.mkdir(parents=True)
    (proc_dir / "stack").write_text(_HEALTHY_STACK)
    sig = probe_main_thread_wedge(
        "meshforge-map.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is None


def test_main_thread_wedge_returns_none_when_stack_unreadable(tmp_path):
    """No /proc/PID/stack (permission denied or process exited) →
    no signal, no false alarm."""
    sig = probe_main_thread_wedge(
        "meshforge-map.service", pid=99999, proc_root=str(tmp_path),
    )
    assert sig is None


def test_main_thread_wedge_skips_when_pid_unresolved(tmp_path):
    """systemctl show MainPID=0 means inactive — let service_inactive
    probe own that signal class."""
    def _runner(*args, **kwargs):
        class _R:
            stdout = "0\n"
            returncode = 0
        return _R()
    with patch("utils.watchdog_probes.subprocess.run", side_effect=_runner):
        sig = probe_main_thread_wedge(
            "meshforge-map.service", proc_root=str(tmp_path),
        )
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# 2026-05-21 moc1 investigation enhancement — worker-thread wedge scan
# ─────────────────────────────────────────────────────────────────────


def test_main_thread_wedge_finds_worker_thread_wedge(tmp_path):
    """Today's incident shape: meshforge-echo.service main thread was
    in futex_wait (healthy idle), but worker thread tid≠pid was in
    unix_wait_for_peer. Original main-thread-only probe missed it.
    The enhanced probe must scan ALL task/* stacks."""
    pid = 12345
    worker_tid = 12399
    main_dir = tmp_path / str(pid) / "task" / str(pid)
    worker_dir = tmp_path / str(pid) / "task" / str(worker_tid)
    main_dir.mkdir(parents=True)
    worker_dir.mkdir(parents=True)
    (main_dir / "stack").write_text(_HEALTHY_STACK)         # main idle
    (worker_dir / "stack").write_text(_WEDGE_STACK)         # worker wedged

    sig = probe_main_thread_wedge(
        "meshforge-echo.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is not None
    assert sig.cls == "main_thread_wedge"
    assert sig.severity == "wedge"
    assert sig.extra["tid"] == worker_tid
    assert sig.extra["thread_role"] == "worker"
    assert "worker thread" in sig.detail


def test_main_thread_wedge_prefers_main_thread_match(tmp_path):
    """When main AND worker both match a wedge pattern, the probe
    should report the main thread (cheaper, more authoritative)."""
    pid = 12345
    worker_tid = 12399
    main_dir = tmp_path / str(pid) / "task" / str(pid)
    worker_dir = tmp_path / str(pid) / "task" / str(worker_tid)
    main_dir.mkdir(parents=True)
    worker_dir.mkdir(parents=True)
    (main_dir / "stack").write_text(_WEDGE_STACK)
    (worker_dir / "stack").write_text(_WEDGE_STACK)

    sig = probe_main_thread_wedge(
        "meshforge-echo.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is not None
    assert sig.extra["tid"] == pid
    assert sig.extra["thread_role"] == "main"
    assert "main thread" in sig.detail


def test_main_thread_wedge_no_signal_when_all_threads_healthy(tmp_path):
    """Multiple threads, all idle → no signal."""
    pid = 12345
    for tid in (pid, 12399, 12400, 12401):
        d = tmp_path / str(pid) / "task" / str(tid)
        d.mkdir(parents=True)
        (d / "stack").write_text(_HEALTHY_STACK)
    sig = probe_main_thread_wedge(
        "meshforge-echo.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# probe_lxmf_process_wedge — cmdline-scan for user-scope services
# ─────────────────────────────────────────────────────────────────────


def _make_fake_proc(tmp_path, pid, cmdline_bytes, stack_text,
                    extra_threads=None):
    """Helper: lay out /proc/<pid>/{cmdline,task/<tid>/stack} files."""
    pid_dir = tmp_path / str(pid)
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "cmdline").write_bytes(cmdline_bytes)
    task_dir = pid_dir / "task" / str(pid)
    task_dir.mkdir(parents=True)
    (task_dir / "stack").write_text(stack_text)
    if extra_threads:
        for tid, stack in extra_threads:
            tdir = pid_dir / "task" / str(tid)
            tdir.mkdir(parents=True)
            (tdir / "stack").write_text(stack)


def test_lxmf_process_wedge_finds_echo_worker_thread_wedge(tmp_path):
    """Today's moc1 reconstruction: meshforge-echo.service's worker
    thread in unix_wait_for_peer. probe_lxmf_process_wedge walks
    /proc, matches `lab.lxmf_echo` cmdline, scans all threads, fires."""
    # Process whose cmdline matches the echo pattern, worker wedged.
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_echo\x00",
        stack_text=_HEALTHY_STACK,
        extra_threads=[(10099, _WEDGE_STACK)],
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert len(signals) == 1
    s = signals[0]
    assert s.cls == "main_thread_wedge"
    assert s.subject == "lab.lxmf_echo"
    assert s.severity == "wedge"
    assert s.issue_ref == 68
    assert s.extra["pid"] == 10001
    assert s.extra["tid"] == 10099
    assert s.extra["thread_role"] == "worker"
    assert "lab.lxmf_echo" in s.extra["cmdline"]


def test_lxmf_process_wedge_ignores_non_lxmf_processes(tmp_path):
    """A random process with a healthy stack must not produce a signal,
    even if its pid dir is in /proc."""
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00something.else\x00",
        stack_text=_HEALTHY_STACK,
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert signals == []


def test_lxmf_process_wedge_ignores_lxmf_process_when_healthy(tmp_path):
    """An lxmf_echo process with all threads healthy → no signal."""
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_echo\x00",
        stack_text=_HEALTHY_STACK,
        extra_threads=[(10099, _HEALTHY_STACK)],
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert signals == []


def test_lxmf_process_wedge_finds_multiple_wedged_processes(tmp_path):
    """Both lab.lxmf_echo and lab.lxmf_tracer wedged → one signal each."""
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_echo\x00",
        stack_text=_WEDGE_STACK,
    )
    _make_fake_proc(
        tmp_path, pid=20002,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_tracer\x00",
        stack_text=_WEDGE_STACK,
    )
    # Plus a healthy non-lxmf process — must not interfere.
    _make_fake_proc(
        tmp_path, pid=30003,
        cmdline_bytes=b"/usr/sbin/nginx\x00",
        stack_text=_HEALTHY_STACK,
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert {s.subject for s in signals} == {"lab.lxmf_echo", "lab.lxmf_tracer"}


def test_lxmf_process_wedge_skips_non_digit_proc_entries(tmp_path):
    """/proc contains non-numeric entries like /proc/cpuinfo, /proc/self.
    The walker must skip them without crashing."""
    (tmp_path / "cpuinfo").write_text("nope")
    (tmp_path / "self").mkdir()
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_echo\x00",
        stack_text=_HEALTHY_STACK,
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert signals == []


# ─────────────────────────────────────────────────────────────────────
# probe_rns_shared_instance_responsive — 2026-05-21 moc1 wedge class
# ─────────────────────────────────────────────────────────────────────


def test_rns_shared_instance_responsive_returns_none_when_no_listener():
    """No listener at @rns/<name> → FileNotFoundError → return None
    (service_inactive owns the 'rnsd not running' signal)."""
    # Pick an instance name nothing's listening on.
    import secrets
    name = f"watchdog-test-nope-{secrets.token_hex(8)}"
    sig = probe_rns_shared_instance_responsive(name, timeout_s=0.5)
    assert sig is None


def test_rns_shared_instance_responsive_returns_none_on_quick_connect():
    """Healthy path: listener accepts the connect quickly → no signal.
    Uses a real abstract Unix listener accepting immediately.

    Timeouts are generous because this test runs on shared CI runners
    where thread scheduling can lag by hundreds of ms under load. The
    timeouts only consume wall-time on real test failures; the happy
    path completes in <50 ms.
    """
    import secrets
    import threading
    name = f"watchdog-test-ok-{secrets.token_hex(8)}"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind("\x00rns/" + name)
    listener.listen(5)
    listener.settimeout(10.0)
    accepted = threading.Event()
    ready = threading.Event()

    def _accept_loop():
        # Signal "thread reached accept()" so the test can avoid racing
        # the probe's connect against thread startup on a loaded runner.
        ready.set()
        try:
            conn, _ = listener.accept()
            conn.close()
            accepted.set()
        except (socket.timeout, OSError):
            pass

    t = threading.Thread(target=_accept_loop, daemon=True)
    t.start()
    try:
        assert ready.wait(5.0), "accept thread failed to start"
        sig = probe_rns_shared_instance_responsive(name, timeout_s=2.0)
        # Wait for accept BEFORE closing the listener — close-mid-accept
        # raises OSError in the thread which never sets `accepted` and
        # used to flake CI (Issue #68 watchdog probe coverage).
        assert accepted.wait(5.0), "listener should have accepted the probe connect"
    finally:
        listener.close()
    assert sig is None


def test_rns_shared_instance_responsive_fires_wedge_on_full_backlog():
    """The 2026-05-21 moc1 reconstruction: listener exists but never
    accepts new connects. Real reproduction: bind + listen(0) and don't
    call accept(). Once the OS-imposed minimum backlog fills,
    subsequent connects block until our timeout fires.

    Note Linux clamps backlog at 0 to the kernel minimum (often 16-128),
    so we have to fill the queue first by spamming the listener with
    throwaway connects until additional ones block. This mirrors the
    real moc1 fault closer than pure mock-based testing.
    """
    import secrets
    import threading

    name = f"watchdog-test-hang-{secrets.token_hex(8)}"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind("\x00rns/" + name)
    listener.listen(1)  # tiny backlog — easy to fill

    # Fill the accept queue with dummy connects so the next connect
    # has to block. We never accept(), so the queue stays full.
    fillers = []
    for _ in range(200):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.05)
            s.connect("\x00rns/" + name)
            fillers.append(s)
        except (socket.timeout, ConnectionRefusedError, OSError):
            # Once the queue is full and unestablished half-opens
            # accumulate beyond the kernel min, additional connect
            # attempts start to refuse or time out. That's the
            # condition we want before running the probe.
            break

    try:
        sig = probe_rns_shared_instance_responsive(name, timeout_s=0.5)
    finally:
        for s in fillers:
            try:
                s.close()
            except OSError:
                pass
        listener.close()

    # Either ECONNREFUSED (kernel refused new attempts because backlog
    # is genuinely saturated) OR socket.timeout (connect blocked). The
    # probe must fire wedge ONLY on the latter; ECONNREFUSED is
    # explicitly handled as "not a wedge" so service_inactive owns it.
    # On Linux the accept-queue-full behavior under SOCK_STREAM Unix
    # sockets typically refuses with ECONNREFUSED. So this test pins
    # the *behavior contract* (probe doesn't crash, returns either
    # None or a valid Signal) more than a single outcome.
    if sig is not None:
        assert sig.cls == "rns_shared_instance_unresponsive"
        assert sig.severity == "wedge"
        assert sig.issue_ref == 68


def test_rns_shared_instance_responsive_returns_none_on_empty_name():
    sig = probe_rns_shared_instance_responsive("")
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# 2026-05-30 incident — rns_interface_down_peer_reachable
# ─────────────────────────────────────────────────────────────────────


# Exact rnstatus interface block from the 2026-05-30 incident: the sole
# RNS uplink TCPInterface stuck Status: Down while rnsd itself was fine.
_RNSTATUS_DOWN_BLOCK = (
    " TCPInterface[Regional RNS/192.168.86.38:4242]\n"
    "    Status    : Down\n"
    "    Mode      : Full\n"
    "    Rate      : 10.00 Mbps\n"
    "    Traffic   : ↑1.59 MB    0 bps\n"
    "                ↓1.58 MB    0 bps\n"
)

# Same interface, but Up — healthy steady state.
_RNSTATUS_UP_BLOCK = (
    " TCPInterface[Regional RNS/192.168.86.38:4242]\n"
    "    Status    : Up\n"
    "    Mode      : Full\n"
    "    Rate      : 10.00 Mbps\n"
)

# One Up TCP interface + one Down (reachable) TCP interface.
_RNSTATUS_MIXED = (
    " Shared Instance[37428]\n"
    "    Status    : Up\n"
    " TCPInterface[UpPeer RNS/10.0.0.5:4242]\n"
    "    Status    : Up\n"
    "    Mode      : Full\n"
    " TCPInterface[Regional RNS/192.168.86.38:4242]\n"
    "    Status    : Down\n"
    "    Mode      : Full\n"
    " RNodeInterface[LoRa]\n"
    "    Status    : Up\n"
)

# Only non-TCP interfaces — no routable host:port, must be ignored even
# when one is Down.
_RNSTATUS_NONTCP = (
    " Shared Instance[37428]\n"
    "    Status    : Up\n"
    " RNodeInterface[LoRa Radio]\n"
    "    Status    : Down\n"
    " AutoInterface[Default Interface]\n"
    "    Status    : Up\n"
)


class TestRnsInterfaceDownPeerReachable:
    """2026-05-30 production incident: rnsd healthy (Up, owns @rns,
    answers rnstatus) and the peer host:port + L3 reachable, but the
    box's sole RNS uplink TCPInterface stuck Status: Down — fleet
    islanded until rnsd restart. The watchdog must catch this DIRECTLY,
    not just indirectly via tracer_peer_unreachable."""

    def test_signal_class_registered(self):
        assert "rns_interface_down_peer_reachable" in SIGNAL_CLASSES

    def test_down_interface_peer_reachable_fires_wedge(self):
        with patch("utils.watchdog_probes._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_DOWN_BLOCK,
            )
        assert sig is not None
        assert sig.cls == "rns_interface_down_peer_reachable"
        # "wedge" is this codebase's highest severity (no "critical").
        assert sig.severity == "wedge"
        assert sig.extra["host"] == "192.168.86.38"
        assert sig.extra["port"] == 4242
        assert sig.extra["peer_reachable"] is True
        # Detail names the cure: restart rnsd.
        assert "rnsd.service" in sig.detail

    def test_down_interface_peer_unreachable_no_signal(self):
        """Genuine peer/network outage — owned by tracer_peer_unreachable,
        not this probe."""
        with patch("utils.watchdog_probes._tcp_reachable", return_value=False):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_DOWN_BLOCK,
            )
        assert sig is None

    def test_up_interface_no_signal_even_if_reachable(self):
        with patch("utils.watchdog_probes._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_UP_BLOCK,
            )
        assert sig is None

    def test_mixed_flags_only_the_down_reachable_interface(self):
        with patch("utils.watchdog_probes._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_MIXED,
            )
        assert sig is not None
        assert sig.extra["host"] == "192.168.86.38"
        assert sig.extra["port"] == 4242

    def test_parser_pins_exact_incident_block(self):
        """The 192.168.86.38:4242 Regional RNS block must parse to
        host 192.168.86.38, port 4242."""
        with patch("utils.watchdog_probes._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_DOWN_BLOCK,
            )
        assert sig is not None
        assert sig.extra["host"] == "192.168.86.38"
        assert sig.extra["port"] == 4242

    def test_non_tcp_interfaces_ignored(self):
        """RNodeInterface / Shared Instance / AutoInterface carry no
        host:port — never probed, never flagged."""
        with patch("utils.watchdog_probes._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_NONTCP,
            )
        assert sig is None

    def test_quiet_when_rnstatus_errored(self):
        """rnsd unreachable → rnstatus parse_error → no signal (a
        different probe owns 'rnsd is down')."""
        with patch("utils.watchdog_probes._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text="Could not connect to local shared instance.",
            )
        assert sig is None

    def test_tcp_reachable_false_on_closed_port(self):
        """The real reachability helper (not patched) must return False
        on a closed local port and never raise."""
        from utils.watchdog_probes import _tcp_reachable
        assert _tcp_reachable("127.0.0.1", 1, timeout=0.5) is False


# ─────────────────────────────────────────────────────────────────────
# 2026-05-30 — rns_rpc_unresponsive (wedged rnsd RPC; rnstatus hangs)
# ─────────────────────────────────────────────────────────────────────


class TestProbeRnsRpcResponsive:
    """Wedged rnsd RPC: the shared-instance socket accepts connects (so
    probe_rns_shared_instance_responsive reports healthy) but rnstatus's
    RPC round-trip hangs. probe_rns_rpc_responsive keys on
    RNSStatus.timed_out — set ONLY by a run_rnstatus subprocess TIMEOUT,
    never by a fast error — so clean-down rnsd and RNS-less boxes don't
    false-alarm. Companion to the connect-layer shared-instance probe
    (#68 SYN-SENT) and the #69 RPC-EOF family."""

    @staticmethod
    def _status(**kw):
        from utils.rns_status_parser import RNSStatus
        return RNSStatus(**kw)

    def test_signal_class_registered(self):
        assert "rns_rpc_unresponsive" in SIGNAL_CLASSES

    def test_fires_wedge_when_rnstatus_timed_out(self):
        status = self._status(
            timed_out=True,
            parse_error="rnstatus timed out (rnsd unresponsive)",
        )
        sig = probe_rns_rpc_responsive(rnstatus_status=status)
        assert sig is not None
        assert sig.cls == "rns_rpc_unresponsive"
        assert sig.severity == "wedge"
        assert sig.subject == "rnsd"
        assert sig.issue_ref == 68
        # Detail names the cure: restart rnsd.
        assert "rnsd.service" in sig.detail

    def test_quiet_when_healthy_interfaces_present(self):
        """Connect accepted + RPC answered (timed_out False) → no signal,
        even with interfaces present."""
        from utils.rns_status_parser import RNSInterface, InterfaceStatus
        status = self._status(
            interfaces=[RNSInterface(
                type_name="TCPInterface",
                display_name="Regional RNS/192.168.86.38:4242",
                status=InterfaceStatus.UP,
            )],
        )
        assert probe_rns_rpc_responsive(rnstatus_status=status) is None

    def test_quiet_when_binary_missing(self):
        """Binary missing → parse_error set but timed_out False → None
        (no false alarm on RNS-less boxes)."""
        status = self._status(
            parse_error="rnstatus binary not found. Install RNS: pip install rns",
        )
        assert probe_rns_rpc_responsive(rnstatus_status=status) is None

    def test_quiet_when_clean_down_error_not_timeout(self):
        """rnsd cleanly down → fast 'no shared instance' error, NOT a
        timeout → timed_out False → None (service_inactive owns down)."""
        status = self._status(
            parse_error="Could not connect to local shared instance.",
        )
        assert probe_rns_rpc_responsive(rnstatus_status=status) is None

    def test_runs_rnstatus_with_bounded_timeout_when_no_status_injected(self):
        """No injected status → probe calls run_rnstatus(timeout_s=...) and
        keys on the returned timed_out flag. Pins that the runner-shared
        call isn't required for the probe to function standalone."""
        from utils.rns_status_parser import RNSStatus
        timed = RNSStatus(
            timed_out=True,
            parse_error="rnstatus timed out (rnsd unresponsive)",
        )
        with patch("utils.rns_status_parser.run_rnstatus",
                   return_value=timed) as m:
            sig = probe_rns_rpc_responsive(timeout_s=4.0)
        m.assert_called_once_with(timeout_s=4.0)
        assert sig is not None
        assert sig.cls == "rns_rpc_unresponsive"

    # -- parser-level: run_rnstatus timed_out flag + timeout_s plumbing --

    def test_run_rnstatus_sets_timed_out_on_subprocess_timeout(self):
        import subprocess as sp
        from utils.rns_status_parser import run_rnstatus
        with patch("utils.rns_status_parser._find_rnstatus_binary",
                   return_value="/usr/bin/rnstatus"), \
             patch("utils.rns_status_parser.subprocess.run",
                   side_effect=sp.TimeoutExpired(cmd="rnstatus", timeout=8)):
            status = run_rnstatus(timeout_s=8.0)
        assert status.timed_out is True
        assert "timed out" in (status.parse_error or "")

    def test_run_rnstatus_forwards_timeout_to_subprocess(self):
        from unittest.mock import MagicMock
        from utils.rns_status_parser import run_rnstatus
        fake = MagicMock(stdout="", stderr="")
        with patch("utils.rns_status_parser._find_rnstatus_binary",
                   return_value="/usr/bin/rnstatus"), \
             patch("utils.rns_status_parser.subprocess.run",
                   return_value=fake) as m:
            run_rnstatus(timeout_s=3.5)
        assert m.call_args.kwargs["timeout"] == 3.5

    def test_run_rnstatus_binary_missing_is_not_timed_out(self):
        from utils.rns_status_parser import run_rnstatus
        with patch("utils.rns_status_parser._find_rnstatus_binary",
                   return_value=None):
            status = run_rnstatus()
        assert status.timed_out is False
        assert "binary not found" in (status.parse_error or "")


# ─────────────────────────────────────────────────────────────────────
# Issue #61 reconstruction — http_local_unresponsive
# ─────────────────────────────────────────────────────────────────────


def test_http_local_no_signal_when_response_arrives():
    """Any HTTP response (including 503 during warming) means the
    server loop is alive. Don't gate on status code."""
    class _Resp:
        def read(self, _n):
            return b"OK"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    with patch("utils.watchdog_probes.urlopen", return_value=_Resp()):
        sig = probe_http_local("meshforge-map.service")
    assert sig is None


def test_http_local_fires_on_timeout():
    """Issue #61 class: TCP accept succeeded, but the handler thread
    is wedged and no response comes back. Probe must surface this."""
    import socket as sk
    def _raise(*args, **kwargs):
        raise sk.timeout("timed out")
    with patch("utils.watchdog_probes.urlopen", side_effect=_raise):
        sig = probe_http_local("meshforge-map.service")
    assert sig is not None
    assert sig.cls == "http_local_unresponsive"
    assert sig.severity == "wedge"
    assert sig.issue_ref == 61


def test_http_local_skips_connection_refused():
    """ConnectionRefused = port not bound = service inactive. That's a
    different probe's domain — don't double-alarm."""
    from urllib.error import URLError
    def _raise(*args, **kwargs):
        raise URLError("[Errno 111] Connection refused")
    with patch("utils.watchdog_probes.urlopen", side_effect=_raise):
        sig = probe_http_local("meshforge-map.service")
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# Issue #73 — fd_exhaustion (proactive companion to http_local)
# ─────────────────────────────────────────────────────────────────────


def _fake_proc(tmp_path, pid, *, open_fds, soft="1024", hard="524288"):
    """Build a fake /proc/<pid> with `open_fds` fd entries and a limits file."""
    pdir = tmp_path / str(pid)
    fd_dir = pdir / "fd"
    fd_dir.mkdir(parents=True)
    for i in range(open_fds):
        # symlink target doesn't matter; os.scandir just counts entries
        (fd_dir / str(i)).write_text("")
    limits = (
        "Limit                     Soft Limit           Hard Limit           Units\n"
        "Max open files            {soft}                 {hard}               files\n"
    ).format(soft=soft, hard=hard)
    (pdir / "limits").write_text(limits)
    return str(tmp_path)


def test_fd_exhaustion_quiet_when_healthy(tmp_path):
    """Well under the soft limit → no signal."""
    root = _fake_proc(tmp_path, 4242, open_fds=50, soft="1024")
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=root, main_pid=4242
    )
    assert sig is None


def test_fd_exhaustion_degraded_past_80pct(tmp_path):
    """820/1024 = 80% → degraded, not yet wedge."""
    root = _fake_proc(tmp_path, 4242, open_fds=820, soft="1024")
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=root, main_pid=4242
    )
    assert sig is not None
    assert sig.cls == "fd_exhaustion"
    assert sig.severity == "degraded"
    assert sig.issue_ref == 73
    assert sig.extra["open_fds"] == 820
    assert sig.extra["soft_limit"] == 1024


def test_fd_exhaustion_wedge_past_95pct(tmp_path):
    """1000/1024 ≈ 98% → wedge (exhaustion imminent — the #73 incident)."""
    root = _fake_proc(tmp_path, 4242, open_fds=1000, soft="1024")
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=root, main_pid=4242
    )
    assert sig is not None
    assert sig.severity == "wedge"
    assert "[Errno 24]" in sig.detail


def test_fd_exhaustion_none_when_pid_unresolved(tmp_path):
    """Inactive service (MainPID unresolved) → None; service_inactive owns it."""
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=str(tmp_path), main_pid=None,
        systemctl_path="/nonexistent/systemctl",
    )
    assert sig is None


def test_fd_exhaustion_none_when_proc_vanished(tmp_path):
    """PID resolved but /proc/<pid> gone (race) → None, never a false alarm."""
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=str(tmp_path), main_pid=99999,
    )
    assert sig is None


def test_fd_exhaustion_none_when_soft_limit_unlimited(tmp_path):
    """An 'unlimited' soft limit has no ceiling to measure against → None."""
    root = _fake_proc(tmp_path, 4242, open_fds=9000, soft="unlimited")
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=root, main_pid=4242
    )
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# 2026-06-01 — foundation_perms_drift (mf.4/#73 perms class)
# ─────────────────────────────────────────────────────────────────────

from utils.rns_tree_perms import RnsTreePerms  # noqa: E402


def _perms(configdir_owner="root:wh6gxz", configdir_mode="1775",
           logfile_owner="wh6gxz:wh6gxz", logfile_exists=True,
           rnsd_user="wh6gxz"):
    return RnsTreePerms(
        rnsd_user=rnsd_user, configdir_owner=configdir_owner,
        configdir_mode=configdir_mode, logfile_exists=logfile_exists,
        logfile_owner=logfile_owner,
    )


def test_foundation_drift_quiet_when_clean():
    """Canonical non-root layout → no signal."""
    assert probe_foundation_drift(perms=_perms()) is None


def test_foundation_drift_fires_on_root_owned_configdir():
    """The moc recurrence: re-provision left /etc/reticulum root:root 755 while
    rnsd runs non-root → degraded signal naming the perms fix."""
    sig = probe_foundation_drift(perms=_perms(
        configdir_owner="root:root", configdir_mode="755", logfile_owner="root:root"))
    assert sig is not None
    assert sig.cls == "foundation_perms_drift"
    assert sig.severity == "degraded"      # latent, not wedged now — proactive
    assert sig.subject == "rnsd"
    assert sig.issue_ref == 73
    assert "fleet_foundation.py apply" in sig.detail
    assert sig.extra["configdir_owner"] == "root:root"
    assert sig.extra["rnsd_user"] == "wh6gxz"


def test_foundation_drift_none_when_rnsd_is_root():
    """A root rnsd writes anything — root:root tree is fine for it → None."""
    assert probe_foundation_drift(perms=_perms(
        configdir_owner="root:root", configdir_mode="755",
        logfile_owner="root:root", rnsd_user="root")) is None


def test_foundation_drift_none_when_perms_unprobed():
    """configdir_owner None (inaccessible/not probed) → never guess → None."""
    assert probe_foundation_drift(perms=_perms(
        configdir_owner=None, configdir_mode=None,
        logfile_owner=None, logfile_exists=False)) is None


def test_foundation_drift_logfile_owner_mismatch_fires():
    """Good dir but logfile owned by root while rnsd is non-root → degraded."""
    sig = probe_foundation_drift(perms=_perms(logfile_owner="root:root"))
    assert sig is not None and sig.severity == "degraded"
    assert "logfile is owned by" in sig.detail


# ─────────────────────────────────────────────────────────────────────
# 2026-06-01 — parity_drift (MeshForge<->MeshAnchor lead-repo port debt)
# ─────────────────────────────────────────────────────────────────────

from types import SimpleNamespace  # noqa: E402


def _pf(status, label):
    return SimpleNamespace(status=status, label=label)


def test_parity_drift_none_when_meshanchor_absent(tmp_path):
    """MeshForge-only box (no /opt/meshanchor) → not applicable, no alarm."""
    assert probe_parity_drift(meshanchor_root=str(tmp_path / "nope")) is None


def test_parity_drift_fires_on_drift(tmp_path):
    findings = [_pf("ok", "src/utils/rns_init.py"),
                _pf("drift", "src/utils/rns_tree_perms.py")]
    sig = probe_parity_drift(
        meshanchor_root=str(tmp_path),
        check_fn=lambda mf, ma: (findings, "drift"))
    assert sig is not None
    assert sig.cls == "parity_drift"
    assert sig.severity == "degraded"
    assert sig.subject == "meshforge<->meshanchor"
    assert "rns_tree_perms.py" in sig.detail
    assert sig.extra["drift_items"] == ["src/utils/rns_tree_perms.py"]


def test_parity_drift_none_when_in_sync(tmp_path):
    sig = probe_parity_drift(
        meshanchor_root=str(tmp_path),
        check_fn=lambda mf, ma: ([_pf("ok", "x")], "in_sync"))
    assert sig is None


def test_parity_drift_none_when_missing_is_indeterminate(tmp_path):
    """A tracked file merely absent (overall 'missing') → indeterminate (possible
    mid-deploy window), not a drift alarm."""
    sig = probe_parity_drift(
        meshanchor_root=str(tmp_path),
        check_fn=lambda mf, ma: ([_pf("missing", "x")], "missing"))
    assert sig is None


def test_parity_drift_none_when_check_raises(tmp_path):
    def boom(mf, ma):
        raise RuntimeError("parity tool blew up")
    assert probe_parity_drift(meshanchor_root=str(tmp_path), check_fn=boom) is None


# ─────────────────────────────────────────────────────────────────────
# 2026-06-01 — rns_version_drift (off the +mf.N fork pin, in the rnsd env)
# ─────────────────────────────────────────────────────────────────────

_PINS = {"rns": "1.2.5+mf.4", "lxmf": "0.9.4+mf.0"}


def test_rns_version_drift_fires_on_mismatch():
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS,
        installed={"rns": "1.1.1", "lxmf": "0.9.4+mf.0"})
    assert sig is not None
    assert sig.cls == "rns_version_drift"
    assert sig.severity == "degraded"
    assert sig.subject == "rns/lxmf"
    assert "1.2.5+mf.4" in sig.detail and "1.1.1" in sig.detail
    assert sig.extra["drift"] == ["rns installed=1.1.1 pinned=1.2.5+mf.4"]


def test_rns_version_drift_none_when_compliant():
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS,
        installed={"rns": "1.2.5+mf.4", "lxmf": "0.9.4+mf.0"})
    assert sig is None


def test_rns_version_drift_none_when_no_pin():
    # No parseable pin (sub-arc A not applied) → indeterminate, no alarm.
    assert probe_rns_version_drift(rnsd_user="wh6gxz", pins={}, installed={"rns": "1.1.1"}) is None


def test_rns_version_drift_none_when_env_unreadable():
    # Couldn't read the service user's site-packages → indeterminate, no false alarm
    # (this is the NoNewPrivileges/RestrictSUIDSGID reality — no user switch possible).
    assert probe_rns_version_drift(rnsd_user="wh6gxz", pins=_PINS, installed={}) is None


def test_rns_version_drift_none_when_pkg_not_visible():
    # rns not found in the user site (venv elsewhere?) — don't guess drift on absence.
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS, installed={"lxmf": "0.9.4+mf.0"})
    assert sig is None


def test_rns_version_drift_fires_only_for_visible_mismatch():
    # lxmf drifted + visible; rns absent → only lxmf is flagged, rns not guessed.
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS, installed={"lxmf": "0.9.0"})
    assert sig is not None
    assert sig.extra["drift"] == ["lxmf installed=0.9.0 pinned=0.9.4+mf.0"]


# ─────────────────────────────────────────────────────────────────────
# Issue #63 reconstruction — delivery_write_canary
# ─────────────────────────────────────────────────────────────────────


def _http_json_mock(payload):
    class _Resp:
        def __init__(self):
            self._body = json.dumps(payload).encode()
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    return _Resp()


def test_delivery_canary_fires_wedge_on_preflight_false():
    """Issue #63 reconstruction: preflight failed → every record() is
    failing. Probe must surface this as wedge."""
    payload = {"health": {
        "preflight_ok": False,
        "preflight_error": "unable to open database file",
        "consecutive_write_errors": 47,
        "last_write_error": "unable to open database file",
        "db_path": "/home/op/.local/share/meshforge/delivery_counters.db",
    }}
    with patch("utils.watchdog_probes.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_write_canary()
    assert sig is not None
    assert sig.cls == "delivery_write_canary"
    assert sig.severity == "wedge"
    assert sig.issue_ref == 63
    assert "preflight" in sig.detail.lower()


def test_delivery_canary_fires_degraded_above_error_threshold():
    """Preflight passed but runtime writes are failing — degraded
    (a different recovery: writes are happening at all, just rate-limited)."""
    payload = {"health": {
        "preflight_ok": True,
        "consecutive_write_errors": 5,
        "last_write_error": "database is locked",
        "db_path": "/x/y/z.db",
    }}
    with patch("utils.watchdog_probes.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_write_canary(error_threshold=3)
    assert sig is not None
    assert sig.severity == "degraded"
    assert "5 consecutive" in sig.detail


def test_delivery_canary_quiet_when_healthy():
    payload = {"health": {
        "preflight_ok": True,
        "consecutive_write_errors": 0,
        "last_write_error": None,
        "db_path": "/x/y/z.db",
    }}
    with patch("utils.watchdog_probes.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_write_canary()
    assert sig is None


def test_delivery_canary_quiet_when_endpoint_unreachable():
    """Gateway not running or HTTP error → return None. A different
    probe surfaces gateway-down."""
    from urllib.error import URLError
    def _raise(*args, **kwargs):
        raise URLError("connection refused")
    with patch("utils.watchdog_probes.urlopen", side_effect=_raise):
        sig = probe_delivery_write_canary()
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# service_inactive
# ─────────────────────────────────────────────────────────────────────


def test_service_inactive_fires_when_active_expected_but_failed():
    def _runner(*args, **kwargs):
        class _R:
            stdout = "failed\n"
            returncode = 3
        return _R()
    with patch("utils.watchdog_probes.subprocess.run", side_effect=_runner):
        sig = probe_service_inactive("meshforge-map.service")
    assert sig is not None
    assert sig.severity == "wedge"
    assert "failed" in sig.detail


def test_service_inactive_quiet_when_active():
    def _runner(*args, **kwargs):
        class _R:
            stdout = "active\n"
            returncode = 0
        return _R()
    with patch("utils.watchdog_probes.subprocess.run", side_effect=_runner):
        sig = probe_service_inactive("meshforge-map.service")
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# tracer_peer_unreachable — today's symptom class
# ─────────────────────────────────────────────────────────────────────


def _write_fire(tracer_dir: Path, fire_at_unix: float,
                results: list) -> None:
    """Helper: write one tracer-<unix>.json file."""
    payload = {
        "schema_version": 1,
        "fire_at_unix": fire_at_unix,
        "fire_at_iso": "2026-05-21T00:00:00Z",
        "self_short": "moc1",
        "results": results,
    }
    target = tracer_dir / f"tracer-{int(fire_at_unix)}.json"
    target.write_text(json.dumps(payload))


def test_tracer_persistent_unreachable_fires_after_three_consecutive_fails(tmp_path):
    """Today's symptom reconstruction: 5 peers all failing to moc1.
    A peer with N≥3 consecutive no-route fires must surface as wedge."""
    tracer_dir = tmp_path
    now = time.time()
    # Three recent fires, all no-route to moc1, no good fires in window.
    _write_fire(tracer_dir, now - 600,
                [{"peer": "moc1", "seq": 1, "result": "no-route", "rtt_ms": 0}])
    _write_fire(tracer_dir, now - 300,
                [{"peer": "moc1", "seq": 2, "result": "timeout", "rtt_ms": 0}])
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc1", "seq": 3, "result": "no-route", "rtt_ms": 0}])
    signals = probe_tracer_peer_unreachable(
        tracer_dir=tracer_dir, persistent_cycles=3, now=now,
    )
    assert len(signals) == 1
    s = signals[0]
    assert s.cls == "tracer_peer_unreachable"
    assert s.subject == "moc1"
    assert s.severity == "wedge"
    assert s.extra["tier"] == "persistent"
    assert s.extra["leading_fail"] >= 3


def test_tracer_transient_unreachable_classified_as_info(tmp_path):
    """A single no-route fire after recent successes is transient — should
    NOT surface as wedge. (This is the cold-start path the user hit today.)"""
    tracer_dir = tmp_path
    now = time.time()
    _write_fire(tracer_dir, now - 600,
                [{"peer": "moc1", "seq": 1, "result": "ok", "rtt_ms": 1500}])
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc1", "seq": 2, "result": "timeout", "rtt_ms": 0}])
    signals = probe_tracer_peer_unreachable(
        tracer_dir=tracer_dir, persistent_cycles=3, now=now,
    )
    assert len(signals) == 1
    s = signals[0]
    assert s.severity == "info"
    assert s.extra["tier"] == "transient"


def test_tracer_quiet_when_latest_fire_is_ok(tmp_path):
    """Latest fire = ok → peer reachable now → nothing to report."""
    tracer_dir = tmp_path
    now = time.time()
    _write_fire(tracer_dir, now - 600,
                [{"peer": "moc1", "seq": 1, "result": "timeout", "rtt_ms": 0}])
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc1", "seq": 2, "result": "ok", "rtt_ms": 2200}])
    signals = probe_tracer_peer_unreachable(
        tracer_dir=tracer_dir, now=now,
    )
    assert signals == []


def test_tracer_quiet_when_tracer_dir_missing(tmp_path):
    """No tracer state → no signals (tracer not running on this box)."""
    missing = tmp_path / "nope"
    signals = probe_tracer_peer_unreachable(tracer_dir=missing)
    assert signals == []


def test_tracer_skips_malformed_files(tmp_path):
    """A bad file in the dir doesn't poison the result."""
    tracer_dir = tmp_path
    (tracer_dir / "tracer-not-a-number.json").write_text("garbage")
    (tracer_dir / "tracer-1779000000.json").write_text("not json at all")
    now = time.time()
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc1", "seq": 1, "result": "ok", "rtt_ms": 100}])
    signals = probe_tracer_peer_unreachable(tracer_dir=tracer_dir, now=now)
    assert signals == []


# ─────────────────────────────────────────────────────────────────────
# Runner — edge-transition tracker
# ─────────────────────────────────────────────────────────────────────


def test_tracker_first_seen_persists_across_ticks():
    tracker = SignalTracker()
    sig = Signal(cls="main_thread_wedge", subject="x",
                 severity="wedge", detail="d")
    # Tick 1
    active, cleared = tracker.update([sig], now=100.0)
    assert len(active) == 1
    assert active[0][1] == 100.0
    assert cleared == []
    # Tick 2 — same signal, first_seen must still be 100.0
    active, cleared = tracker.update([sig], now=130.0)
    assert active[0][1] == 100.0
    assert cleared == []


def test_tracker_reports_cleared_on_disappear():
    tracker = SignalTracker()
    sig = Signal(cls="main_thread_wedge", subject="x",
                 severity="wedge", detail="d")
    tracker.update([sig], now=100.0)
    active, cleared = tracker.update([], now=130.0)
    assert active == []
    assert cleared == [("main_thread_wedge", "x")]


def test_tracker_distinct_subjects_dont_collide():
    tracker = SignalTracker()
    s1 = Signal(cls="service_inactive", subject="meshforge-map.service",
                severity="degraded", detail="x")
    s2 = Signal(cls="service_inactive", subject="rnsd.service",
                severity="degraded", detail="y")
    active, _ = tracker.update([s1, s2], now=100.0)
    assert {(a[0].subject, a[1]) for a in active} == {
        ("meshforge-map.service", 100.0),
        ("rnsd.service", 100.0),
    }


# ─────────────────────────────────────────────────────────────────────
# Runner — atomic-rename writer
# ─────────────────────────────────────────────────────────────────────


def test_write_state_emits_valid_json(tmp_path):
    out = tmp_path / "watchdog.json"
    sig = Signal(cls="main_thread_wedge", subject="meshforge-map.service",
                 severity="wedge", detail="stuck", issue_ref=68)
    write_state(out, host="moc1", now=1779480000.0, probe_count=1,
                active_signals=[(sig, 1779480000.0)])
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["host"] == "moc1"
    assert payload["ok"] is False  # wedge present
    assert len(payload["signals"]) == 1
    assert payload["signals"][0]["class"] == "main_thread_wedge"
    assert payload["signals"][0]["first_seen"] == 1779480000.0


def test_write_state_ok_true_when_no_wedge(tmp_path):
    out = tmp_path / "watchdog.json"
    info_sig = Signal(cls="tracer_peer_unreachable", subject="moc2",
                      severity="info", detail="cold-start")
    write_state(out, host="x", now=1.0, probe_count=1,
                active_signals=[(info_sig, 1.0)])
    payload = json.loads(out.read_text())
    assert payload["ok"] is True


def test_write_state_atomic_rename_leaves_no_tmpfile(tmp_path):
    out = tmp_path / "watchdog.json"
    write_state(out, host="x", now=1.0, probe_count=1, active_signals=[])
    assert out.exists()
    # No leftover tmp file
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == []


def test_write_state_handles_missing_parent_dir(tmp_path):
    """Parent dir auto-create — atomic-rename writer must not require
    the StateDirectory= line to have run first in dev/test."""
    out = tmp_path / "newdir" / "watchdog.json"
    write_state(out, host="x", now=1.0, probe_count=1, active_signals=[])
    assert out.exists()


# ─────────────────────────────────────────────────────────────────────
# Federation passthrough — /fleet/slo + /api/status include watchdog
# ─────────────────────────────────────────────────────────────────────


def test_fleet_slo_includes_watchdog_block(tmp_path, monkeypatch):
    """build_slo_snapshot must include a `watchdog` block. Federation
    relies on this — without it, signals don't propagate to /fleet
    rollup."""
    from utils import fleet_snapshot
    # Point the reader at a real file so we exercise the read path,
    # not just the "no file" fallback.
    state = tmp_path / "watchdog.json"
    state.write_text(json.dumps({
        "host": "moc1", "ts": time.time(), "probe_count": 7, "ok": True,
        "signals": [],
    }))
    monkeypatch.setattr(fleet_snapshot, "_WATCHDOG_STATE_PATH", str(state))

    snap = fleet_snapshot.build_slo_snapshot()
    assert "watchdog" in snap
    w = snap["watchdog"]
    assert w["installed"] is True
    assert w["ok"] is True
    assert w["probe_count"] == 7


def test_fleet_slo_degrades_overall_status_on_wedge_signal(tmp_path, monkeypatch):
    """A wedge-severity watchdog signal must push overall_status to
    `degraded`, mirroring the cascade-pre-fail behavior already in
    build_slo_snapshot."""
    from utils import fleet_snapshot
    state = tmp_path / "watchdog.json"
    state.write_text(json.dumps({
        "host": "moc1", "ts": time.time(), "probe_count": 1, "ok": False,
        "signals": [{
            "class": "main_thread_wedge",
            "subject": "meshforge-map.service",
            "severity": "wedge",
            "detail": "stuck in unix_wait_for_peer",
            "issue_ref": 68,
        }],
    }))
    monkeypatch.setattr(fleet_snapshot, "_WATCHDOG_STATE_PATH", str(state))

    snap = fleet_snapshot.build_slo_snapshot()
    assert snap["overall_status"] == "degraded"
    assert any(
        "main_thread_wedge" in err for err in snap["errors"]
    ), f"errors should mention wedge signal class; got {snap['errors']!r}"


def test_fleet_slo_degrades_when_watchdog_state_stale(tmp_path, monkeypatch):
    """A stale watchdog (>5min since last write) means the watchdog
    itself is broken — surface this rather than silently trusting
    a frozen `ok=True` snapshot."""
    from utils import fleet_snapshot
    state = tmp_path / "watchdog.json"
    state.write_text(json.dumps({
        "host": "moc1",
        "ts": time.time() - 600,  # 10 min old → stale
        "probe_count": 1, "ok": True, "signals": [],
    }))
    monkeypatch.setattr(fleet_snapshot, "_WATCHDOG_STATE_PATH", str(state))

    snap = fleet_snapshot.build_slo_snapshot()
    assert snap["watchdog"]["ok"] is False
    assert "stale" in snap["watchdog"]["reason"]


def test_fleet_slo_watchdog_silent_when_not_installed(monkeypatch):
    """Boxes during rollout: watchdog file missing → block reports
    installed=False, doesn't trip overall_status. Backwards-compatible."""
    from utils import fleet_snapshot
    monkeypatch.setattr(
        fleet_snapshot, "_WATCHDOG_STATE_PATH", "/nonexistent/path",
    )
    snap = fleet_snapshot.build_slo_snapshot()
    assert snap["watchdog"]["installed"] is False
    # overall_status decision isn't influenced by absent watchdog
    # (only services + cascade matter when watchdog is offline).


# ─────────────────────────────────────────────────────────────────────
# Per-box config override (moc3 expected-state)
# ─────────────────────────────────────────────────────────────────────


def test_load_config_file_returns_empty_on_missing(tmp_path):
    """No config file → empty dict → defaults preserved by resolve_probe_targets."""
    cfg = load_config_file(tmp_path / "nope.json")
    assert cfg == {}


def test_load_config_file_returns_empty_on_malformed(tmp_path):
    """Malformed JSON → empty dict + warning logged. Daemon keeps starting."""
    p = tmp_path / "watchdog.json"
    p.write_text("not json at all {{{")
    cfg = load_config_file(p)
    assert cfg == {}


def test_load_config_file_returns_empty_on_non_object_root(tmp_path):
    """JSON array at root → empty dict (defensive)."""
    p = tmp_path / "watchdog.json"
    p.write_text('["service_a", "service_b"]')
    cfg = load_config_file(p)
    assert cfg == {}


def test_load_config_file_parses_valid_object(tmp_path):
    p = tmp_path / "watchdog.json"
    p.write_text(json.dumps({"http_port": 8080}))
    cfg = load_config_file(p)
    assert cfg == {"http_port": 8080}


def test_resolve_probe_targets_uses_defaults_for_empty_config():
    sea, swc, port = resolve_probe_targets({})
    assert sea == _DEFAULT_SERVICES_EXPECTED_ACTIVE
    assert swc == _DEFAULT_SERVICES_WEDGE_CHECK
    assert port == 5000


def test_resolve_probe_targets_moc3_override_drops_meshforge_map():
    """The moc3 use case: drop meshforge-map.service from expected-active
    because the box is gateway-only by design (project_moc3_hardware_constraint
    memory). Override fully replaces the default list."""
    sea, swc, port = resolve_probe_targets({
        "services_expected_active": ["rnsd.service"],
        "services_wedge_check": [],
    })
    assert sea == ("rnsd.service",)
    assert "meshforge-map.service" not in sea
    assert swc == ()
    # http_port still defaults when not overridden
    assert port == 5000


def test_resolve_probe_targets_partial_override_keeps_other_defaults():
    """Only overriding one key shouldn't touch the others."""
    sea, swc, port = resolve_probe_targets({"http_port": 8808})
    assert sea == _DEFAULT_SERVICES_EXPECTED_ACTIVE
    assert swc == _DEFAULT_SERVICES_WEDGE_CHECK
    assert port == 8808


def test_resolve_probe_targets_rejects_garbage_overrides():
    """Bad types fall back to defaults silently (warning logged), don't crash."""
    sea, swc, port = resolve_probe_targets({
        "services_expected_active": "not a list",
        "services_wedge_check": [1, 2, 3],
        "http_port": "8080",   # string, not int
    })
    assert sea == _DEFAULT_SERVICES_EXPECTED_ACTIVE
    assert swc == _DEFAULT_SERVICES_WEDGE_CHECK
    assert port == 5000


def test_resolve_probe_targets_rejects_out_of_range_port():
    sea, swc, port = resolve_probe_targets({"http_port": 99999})
    assert port == 5000  # falls back
    sea, swc, port = resolve_probe_targets({"http_port": -1})
    assert port == 5000  # falls back


def test_load_config_file_candidate_resolution_walks_to_etc(tmp_path, monkeypatch):
    """When the watchdog runs as root with no SUDO_USER (the systemd
    case), the operator-home lookup must NOT cause us to silently use
    /root/.config — we walk the candidate list and pick the first
    existing-and-parseable file. /etc/meshforge/watchdog.json is the
    documented fallback."""
    from utils import watchdog_runner

    # Stub the operator-home resolver to point at our tmp_path so we
    # don't actually touch a real user's home.
    op_home = tmp_path / "op-home"
    op_home.mkdir()
    monkeypatch.setattr(
        watchdog_runner, "_operator_home_for_root", lambda: op_home,
    )

    # Stub /etc/meshforge/watchdog.json to a real file under tmp_path.
    etc_path = tmp_path / "etc-watchdog.json"
    etc_path.write_text(json.dumps({"http_port": 8808}))

    original_resolver = watchdog_runner._resolve_config_candidates
    def _patched_resolver(explicit):
        if explicit is not None:
            return [Path(str(explicit)).expanduser()]
        # The operator-home candidate doesn't exist; the next one does.
        return [
            op_home / ".config" / "meshforge" / "watchdog.json",  # missing
            etc_path,                                              # present
        ]
    monkeypatch.setattr(
        watchdog_runner, "_resolve_config_candidates", _patched_resolver,
    )

    cfg = load_config_file()
    assert cfg == {"http_port": 8808}


def test_load_config_file_operator_home_wins_over_etc(tmp_path, monkeypatch):
    """When both candidates exist, the operator-home file takes
    precedence — it's the per-box config the operator actually writes."""
    from utils import watchdog_runner

    op_cfg_dir = tmp_path / "op-home" / ".config" / "meshforge"
    op_cfg_dir.mkdir(parents=True)
    op_cfg = op_cfg_dir / "watchdog.json"
    op_cfg.write_text(json.dumps({"http_port": 1111}))

    etc_cfg = tmp_path / "etc-watchdog.json"
    etc_cfg.write_text(json.dumps({"http_port": 9999}))

    def _patched_resolver(explicit):
        if explicit is not None:
            return [Path(str(explicit)).expanduser()]
        return [op_cfg, etc_cfg]
    monkeypatch.setattr(
        watchdog_runner, "_resolve_config_candidates", _patched_resolver,
    )

    cfg = load_config_file()
    assert cfg == {"http_port": 1111}
