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
    init_reticulum_with_watchdog,
    make_ack_body,
    make_ping_body,
    parse_ack,
    parse_ping,
    short_name,
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
