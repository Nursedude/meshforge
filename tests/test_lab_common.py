"""Tests for src/lab/_lab_common.py — wire format + helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lab._lab_common import (
    AckMessage,
    PingMessage,
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
    """The wedge fingerprint: constructor never returns. Watchdog must
    call os._exit(2) so systemd restarts the unit."""
    import threading as _threading

    started = _threading.Event()
    release = _threading.Event()

    class _FakeRNSModule:
        @staticmethod
        def Reticulum(configdir, loglevel):
            started.set()
            # Simulate the kernel `unix_wait_for_peer` hang. release is
            # never set by the test so this thread blocks forever (as a
            # daemon thread, the interpreter will reap it on exit).
            release.wait()

    monkeypatch.setitem(sys.modules, "RNS", _FakeRNSModule)

    abort_calls = []

    def _fake_exit(code):
        # In production os._exit terminates; raise SystemExit so the test
        # also short-circuits and the fall-through code in the watchdog
        # (which would KeyError on the empty result dict) is bypassed.
        abort_calls.append(code)
        raise SystemExit(code)

    monkeypatch.setattr("lab._lab_common.os._exit", _fake_exit)

    with pytest.raises(SystemExit) as excinfo:
        init_reticulum_with_watchdog("/tmp/x", timeout_s=0.2)

    assert excinfo.value.code == 2
    assert started.is_set(), "constructor must have started"
    assert abort_calls == [2]


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
