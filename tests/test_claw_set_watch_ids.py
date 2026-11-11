"""claw_set_watch_ids --reboot must survive the claw being DARK mid-reboot.

2026-11-10, dudeclaw-02: the write verified and the reboot armed, then the
first poll hit the bus while the claw was down. nats_client raises
``NatsError("timed out after 8.0s")``; ``_device_state`` caught only
RuntimeError, so the very state the poll exists to wait through escaped to the
top-level handler -> rc 1 ("refused") while the claw came back fine 18 s later.
Dark-right-now is not an answer and not a failure (the function's own words).

Every test stubs ``_tool``; the server is a ``.invalid`` name so no run can
reach a real bus (red-run fixtures must not reach the fleet).
"""
import importlib.util
import os

import pytest

from mini_dudeai.nats_client import NatsError

_SPEC = importlib.util.spec_from_file_location(
    "claw_set_watch_ids",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "claw_set_watch_ids.py"))
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)

SRV = "nats.invalid:4222"


def _info(up, reason="sw-restart"):
    return {"ok": True, "result": "Reset reason: %s, Uptime: %d seconds" % (reason, up)}


def test_device_state_reads_a_bus_timeout_as_dark(monkeypatch):
    def boom(*_a, **_k):
        raise NatsError("timed out after 8.0s")
    monkeypatch.setattr(mod, "_tool", boom)
    assert mod._device_state(SRV, "dudeclaw-02", 1.0) == (None, None)


def test_reboot_waits_through_a_dark_claw_and_verifies(monkeypatch):
    seq = iter([_info(59129, "TASK-WDT"),                 # baseline
                {"ok": True, "result": "reboot armed in 2000ms"},
                NatsError("timed out after 8.0s"),        # claw down
                NatsError("timed out after 8.0s"),
                _info(18)])                               # back

    def tool(*_a, **_k):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(mod, "_tool", tool)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    assert mod._reboot_and_verify(SRV, "dudeclaw-02", 1.0, wait_s=60) == 0


def test_a_claw_that_never_returns_is_unknown_not_refused(monkeypatch):
    calls = {"n": 0}

    def tool(_s, _d, payload, _t):
        calls["n"] += 1
        if calls["n"] == 1:
            return _info(500)
        if payload.get("tool") == "reboot":
            return {"ok": True, "result": "reboot armed in 2000ms"}
        raise NatsError("timed out after 8.0s")
    clock = {"t": 0.0}
    monkeypatch.setattr(mod, "_tool", tool)
    monkeypatch.setattr(mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(mod.time, "time", lambda: clock["t"])
    assert mod._reboot_and_verify(SRV, "dudeclaw-02", 1.0, wait_s=30) == 2
