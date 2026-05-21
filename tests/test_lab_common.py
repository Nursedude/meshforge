"""Tests for src/lab/_lab_common.py — wire format + helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lab._lab_common import (
    AckMessage,
    PingMessage,
    bounded_block,
    check_rns_listener_owner,
    init_reticulum_with_watchdog,
    make_ack_body,
    make_ping_body,
    parse_ack,
    parse_ping,
    short_name,
    _parse_ss_listener_line,
    _read_instance_name_from_config,
)


# ---------------------------------------------------------------- PING/ACK


def test_make_ping_body_round_trip():
    body = make_ping_body(42, "box-a")
    parsed = parse_ping(body)
    assert parsed == PingMessage(seq=42, sender="box-a")


def test_make_ack_body_round_trip():
    body = make_ack_body(42, "box-a", "2026-05-12T03:00:00Z")
    parsed = parse_ack(body)
    assert parsed == AckMessage(
        seq=42, orig="box-a", recv_at_iso="2026-05-12T03:00:00Z",
    )


def test_make_ack_body_defaults_recv_at_to_now_iso():
    body = make_ack_body(1, "moc3")
    parsed = parse_ack(body)
    assert parsed is not None
    # Format must parse — exact value is now-ish, content sanity only.
    assert parsed.recv_at_iso.endswith("Z")
    assert "T" in parsed.recv_at_iso


@pytest.mark.parametrize("bad", [
    "",
    "hello world",
    "PING seq=abc from=moc",      # non-numeric seq
    "PING seq=1",                  # missing from
    "ACK seq=1 orig=moc",          # ACK shape passed to PING parser
])
def test_parse_ping_rejects_garbage(bad):
    assert parse_ping(bad) is None


@pytest.mark.parametrize("bad", [
    "",
    "hello world",
    "ACK seq=abc orig=moc recv_at=2026-05-12T03:00:00Z",
    "ACK seq=1 orig=moc",         # missing recv_at
    "PING seq=1 from=moc",         # PING shape passed to ACK parser
])
def test_parse_ack_rejects_garbage(bad):
    assert parse_ack(bad) is None


def test_parse_ping_handles_extra_whitespace():
    assert parse_ping("  PING   seq=7   from=moc1   ") == PingMessage(
        seq=7, sender="moc1",
    )


def test_parse_none_is_none():
    assert parse_ping(None) is None
    assert parse_ack(None) is None


# ------------------------------------------------------------------ short_name


def test_short_name_is_first_hostname_section(monkeypatch):
    monkeypatch.setattr(
        "lab._lab_common.socket.gethostname",
        lambda: "moc1.lan.example.org",
    )
    assert short_name() == "moc1"


def test_short_name_falls_back_on_oserror(monkeypatch):
    def _boom():
        raise OSError("hostname lookup failed")
    monkeypatch.setattr("lab._lab_common.socket.gethostname", _boom)
    assert short_name() == "unknown"


# ------------------------------------------- Issue #69 — RNS listener preflight
#
# Class: a non-rnsd process owns @rns/<instance> LISTEN, every RNS
# client connecting via share_instance=Yes EOFs on first RPC call.
# Real 2026-05-20 incident: MeshAnchor's daemon.py was running on a
# fleet box alongside MeshForge, claimed the listener, broke the
# fleet-wide lab-tracer measurement.


# Real `ss -xnpl` output shape captured during the 2026-05-20 incident.
# Field widths in `ss` vary by kernel; lock the actual bytes so the
# parser test stays honest if `ss` is ever reformatted.
_SS_OUTPUT_ROGUE = (
    'u_str LISTEN 0      0                                  '
    '         @rns/fleet-test 1206724            * 0    '
    'users:(("python3",pid=129485,fd=4))\n'
    'u_str LISTEN 0      0                                  '
    '         @rns/fleet-test 1206725            * 0    '
    'users:(("python3",pid=129485,fd=5))\n'
)

_SS_OUTPUT_HEALTHY = (
    'u_str LISTEN 0      0                                  '
    '         @rns/fleet-test 1858580            * 0    '
    'users:(("rnsd",pid=206118,fd=4))\n'
)

_SS_OUTPUT_NO_LISTENER = (
    'u_str LISTEN 0      0                                  '
    '         /run/something 1234567            * 0    '
    'users:(("other",pid=999,fd=4))\n'
)


def test_parse_ss_listener_line_extracts_pid_and_cmd():
    line = _SS_OUTPUT_ROGUE.splitlines()[0]
    assert _parse_ss_listener_line(line, "fleet-test") == (129485, "python3")


def test_parse_ss_listener_line_ignores_wrong_instance():
    line = _SS_OUTPUT_ROGUE.splitlines()[0]
    assert _parse_ss_listener_line(line, "pi-mesh") is None


def test_parse_ss_listener_line_returns_none_for_unrelated_socket():
    line = _SS_OUTPUT_NO_LISTENER
    assert _parse_ss_listener_line(line, "fleet-test") is None


def _patch_ss_and_proc(monkeypatch, ss_output, cmdlines):
    """Fake `ss -xnpl` output and /proc/<pid>/cmdline reads.

    cmdlines maps pid -> str (or to OSError to simulate vanished process).
    """
    import subprocess

    class _FakeResult:
        stdout = ss_output

    def _fake_run(cmd, **kwargs):
        assert cmd == ["ss", "-xnpl"]
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    real_open = open

    def _fake_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/") and path.endswith("/cmdline"):
            pid = int(path.split("/")[2])
            entry = cmdlines.get(pid)
            if isinstance(entry, OSError):
                raise entry
            import io
            return io.BytesIO((entry or "").encode("utf-8") + b"\x00")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _fake_open)


def test_preflight_passes_when_rnsd_owns_listener(monkeypatch):
    _patch_ss_and_proc(
        monkeypatch,
        _SS_OUTPUT_HEALTHY,
        {206118: "/usr/bin/python3 /usr/local/bin/rnsd --config /etc/reticulum --service"},
    )
    # No raise = pass.
    assert check_rns_listener_owner("fleet-test") is None


def test_preflight_raises_when_meshanchor_daemon_owns_listener(monkeypatch):
    """The 2026-05-20 incident shape: MeshAnchor's daemon.py owns the
    listener. The cmdline contains 'meshanchor' but NOT 'rnsd' or
    'reticulum' — the tightened allowlist must reject it."""
    _patch_ss_and_proc(
        monkeypatch,
        _SS_OUTPUT_ROGUE,
        {129485: "python3 /opt/meshanchor/src/daemon.py start --foreground"},
    )
    with pytest.raises(RuntimeError) as exc_info:
        check_rns_listener_owner("fleet-test")
    msg = str(exc_info.value)
    assert "@rns/fleet-test" in msg
    assert "129485" in msg
    assert "EOFError" in msg
    assert "sudo kill" in msg, "operator must see actionable recovery command"


def test_preflight_passes_when_no_listener_present(monkeypatch):
    """No existing listener — RNS will create one. Don't fail-loud."""
    _patch_ss_and_proc(monkeypatch, _SS_OUTPUT_NO_LISTENER, {})
    assert check_rns_listener_owner("fleet-test") is None


def test_preflight_handles_ss_missing(monkeypatch):
    """ss binary absent (minimal container, container image) — preflight
    must not block RNS init. Skip silently and let RNS surface its own
    error if there's a real problem."""
    import subprocess

    def _raises(*args, **kwargs):
        raise FileNotFoundError("ss")

    monkeypatch.setattr(subprocess, "run", _raises)
    assert check_rns_listener_owner("fleet-test") is None


def test_preflight_handles_process_vanished_between_ss_and_proc(monkeypatch):
    """The owner process may exit in the microsecond between `ss` reading
    /proc/net/unix and us reading /proc/<pid>/cmdline. Treat the empty
    cmdline as suspicious (because we can't prove it's allowed), so the
    operator still gets a diagnostic — but only one pid is involved, so
    the message names it."""
    _patch_ss_and_proc(
        monkeypatch,
        _SS_OUTPUT_ROGUE,
        {129485: OSError("process gone")},
    )
    with pytest.raises(RuntimeError) as exc_info:
        check_rns_listener_owner("fleet-test")
    assert "process exited" in str(exc_info.value)


def test_read_instance_name_from_config_handles_missing_file(tmp_path):
    """No config file = no instance name to check. Return None silently
    (preflight skips, RNS will populate the config on first init)."""
    assert _read_instance_name_from_config(str(tmp_path)) is None


def test_read_instance_name_from_config_parses_instance_name(tmp_path):
    (tmp_path / "config").write_text(
        "[reticulum]\n"
        "share_instance = Yes\n"
        "instance_name = fleet-tester rns\n"
        "shared_instance_port = 37428\n"
    )
    assert _read_instance_name_from_config(str(tmp_path)) == "fleet-tester rns"


def test_init_reticulum_with_watchdog_invokes_preflight(monkeypatch, tmp_path):
    """init_reticulum_with_watchdog must call check_rns_listener_owner
    when configdir has an instance_name. This is the wire-up test that
    proves the preflight is actually engaged in the prod path."""
    (tmp_path / "config").write_text(
        "[reticulum]\ninstance_name = test-instance\n"
    )

    calls = []

    def _fake_check(instance_name):
        calls.append(instance_name)
        return None

    class _FakeRNSModule:
        @staticmethod
        def Reticulum(configdir, loglevel):
            return _FakeReticulum(configdir, loglevel)

    monkeypatch.setattr("lab._lab_common.check_rns_listener_owner", _fake_check)
    monkeypatch.setitem(sys.modules, "RNS", _FakeRNSModule)

    init_reticulum_with_watchdog(str(tmp_path), timeout_s=2.0)
    assert calls == ["test-instance"]


def test_init_reticulum_with_watchdog_propagates_preflight_failure(monkeypatch, tmp_path):
    """When the preflight raises, init_reticulum_with_watchdog must NOT
    proceed to RNS.Reticulum() — that's the whole point of catching the
    collision early. The exception propagates so the calling service
    fails loud with a diagnostic the operator can act on."""
    (tmp_path / "config").write_text(
        "[reticulum]\ninstance_name = test-instance\n"
    )

    reticulum_called = []

    def _failing_check(instance_name):
        raise RuntimeError("listener owned by pid=999 cmd='rogue'")

    class _FakeRNSModule:
        @staticmethod
        def Reticulum(configdir, loglevel):
            reticulum_called.append(True)
            return _FakeReticulum(configdir, loglevel)

    monkeypatch.setattr("lab._lab_common.check_rns_listener_owner", _failing_check)
    monkeypatch.setitem(sys.modules, "RNS", _FakeRNSModule)

    with pytest.raises(RuntimeError, match="rogue"):
        init_reticulum_with_watchdog(str(tmp_path), timeout_s=2.0)
    assert not reticulum_called, "preflight failure must prevent RNS.Reticulum() call"


# ----------------------------------------- init_reticulum_with_watchdog


class _FakeReticulum:
    """Stand-in instance returned by the patched RNS.Reticulum."""

    def __init__(self, configdir, loglevel):
        self.configdir = configdir
        self.loglevel = loglevel


def test_watchdog_returns_instance_when_constructor_returns(monkeypatch):
    sentinel = _FakeReticulum("/tmp/x", 2)

    class _FakeRNSModule:
        @staticmethod
        def Reticulum(configdir, loglevel):
            return _FakeReticulum(configdir, loglevel)

    monkeypatch.setitem(sys.modules, "RNS", _FakeRNSModule)
    result = init_reticulum_with_watchdog("/tmp/x", timeout_s=2.0)
    assert isinstance(result, _FakeReticulum)
    assert result.configdir == "/tmp/x"
    assert result.loglevel == 2
    _ = sentinel  # silence unused


def test_watchdog_reraises_constructor_exception(monkeypatch):
    class _Boom(RuntimeError):
        pass

    class _FakeRNSModule:
        @staticmethod
        def Reticulum(configdir, loglevel):
            raise _Boom("rns init blew up")

    monkeypatch.setitem(sys.modules, "RNS", _FakeRNSModule)
    with pytest.raises(_Boom, match="rns init blew up"):
        init_reticulum_with_watchdog("/tmp/x", timeout_s=2.0)


def test_watchdog_aborts_process_on_timeout(monkeypatch):
    """The wedge fingerprint: constructor never returns. Watchdog thread
    must call os._exit(2) so systemd restarts the unit. In production
    os._exit terminates the whole process regardless of which thread
    called it; the test substitutes a recorder that also unblocks the
    main-thread mock so the test itself can complete."""
    import threading as _threading

    started = _threading.Event()
    release = _threading.Event()
    abort_calls = []

    class _FakeRNSModule:
        @staticmethod
        def Reticulum(configdir, loglevel):
            started.set()
            # Simulate the kernel `unix_wait_for_peer` hang. Safety bound
            # (5s) so a test failure can't wedge pytest forever.
            release.wait(timeout=5.0)

    def _fake_exit(code):
        abort_calls.append(code)
        # Real os._exit terminates the process; here we instead release
        # the blocked main-thread mock so the function under test can
        # return and the test can assert.
        release.set()

    monkeypatch.setitem(sys.modules, "RNS", _FakeRNSModule)
    monkeypatch.setattr("lab._lab_common.os._exit", _fake_exit)

    init_reticulum_with_watchdog("/tmp/x", timeout_s=0.2)

    assert started.is_set(), "constructor must have started"
    assert abort_calls == [2], (
        "watchdog must call os._exit(2); got %r" % (abort_calls,)
    )
    assert release.wait(timeout=0.1), "release must have been set by watchdog"


def test_watchdog_default_timeout_from_env(monkeypatch):
    """RNS_INIT_TIMEOUT_S resolves from MESHFORGE_LAB_RNS_INIT_TIMEOUT."""
    monkeypatch.setenv("MESHFORGE_LAB_RNS_INIT_TIMEOUT", "12.5")
    # Re-import to pick up the new env value.
    import importlib

    import lab._lab_common as lc
    importlib.reload(lc)
    assert lc.RNS_INIT_TIMEOUT_S == 12.5
    # Restore default for other tests.
    monkeypatch.delenv("MESHFORGE_LAB_RNS_INIT_TIMEOUT", raising=False)
    importlib.reload(lc)


# ----------------------------------------------------- bounded_block


def test_bounded_block_normal_exit_disarms_watchdog(monkeypatch):
    """Wrapped block returns; watchdog must NOT fire os._exit."""
    abort_calls = []
    monkeypatch.setattr(
        "lab._lab_common.os._exit", lambda code: abort_calls.append(code),
    )

    with bounded_block(timeout_s=1.0, label="test"):
        pass  # immediate return

    # Give the watchdog thread a moment to settle (it should have seen
    # done.set() and exited cleanly).
    import time
    time.sleep(0.1)
    assert abort_calls == [], (
        "bounded_block must not fire os._exit on normal return; got %r"
        % (abort_calls,)
    )


def test_bounded_block_exception_propagates_and_disarms(monkeypatch):
    """Exception inside the block propagates AND disarms the watchdog."""
    abort_calls = []
    monkeypatch.setattr(
        "lab._lab_common.os._exit", lambda code: abort_calls.append(code),
    )

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom, match="kaboom"):
        with bounded_block(timeout_s=1.0, label="test"):
            raise _Boom("kaboom")

    import time
    time.sleep(0.1)
    assert abort_calls == [], (
        "exception inside the block must also disarm; got %r" % (abort_calls,)
    )


def test_bounded_block_fires_os_exit_on_timeout(monkeypatch):
    """Block doesn't return within timeout — watchdog must call os._exit(2).

    Production semantics: os._exit terminates the whole process. The test
    captures the call instead, then signals an event so the wedged block
    can complete and the test itself doesn't hang."""
    import threading as _threading

    release = _threading.Event()
    abort_calls = []

    def _fake_exit(code):
        abort_calls.append(code)
        # Unblock the test's "wedged" block so it can complete.
        release.set()

    monkeypatch.setattr("lab._lab_common.os._exit", _fake_exit)

    with bounded_block(timeout_s=0.2, label="wedge-test"):
        # Simulate a kernel hang; bounded by 5s so a test bug can't
        # wedge pytest forever.
        release.wait(timeout=5.0)

    assert abort_calls == [2], (
        "watchdog must fire os._exit(2) on timeout; got %r" % (abort_calls,)
    )


def test_bounded_block_label_appears_in_log(monkeypatch, caplog):
    """Operators read the watchdog log to diagnose which region wedged;
    the label is the only signal pointing them at the right call site."""
    import threading as _threading

    release = _threading.Event()

    def _fake_exit(code):
        release.set()

    monkeypatch.setattr("lab._lab_common.os._exit", _fake_exit)

    with caplog.at_level("ERROR", logger="lab._lab_common"):
        with bounded_block(timeout_s=0.1, label="my-special-region"):
            release.wait(timeout=5.0)

    assert any("my-special-region" in rec.message for rec in caplog.records), (
        "watchdog log must include the label; got %r"
        % ([r.message for r in caplog.records],)
    )
