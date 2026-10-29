"""Every watchdog tuning knob rejects what an operator cannot have meant and
says so ONCE (hfm #3/#9) — and the daemon resolves them at STARTUP, so a bad
value is witnessed before the incident it would have mis-tuned, not during it.

2026-10-28: MESHFORGE_RNS_RPC_SLOW_FACTOR=inf passed the old `> 1.0` check
and silently made the latency arm's threshold infinite.
"""
import logging
from pathlib import Path

import pytest

from utils import watchdog_probe_core as core
from utils import watchdog_probes_rns as wpr
from utils.watchdog_probes import resolve_watchdog_knobs

_SLOW = "MESHFORGE_RNS_RPC_SLOW_FACTOR"
_KNOBS = (_SLOW, "MESHFORGE_RNS_RPC_CONFIRM_TICKS",
          "MESHFORGE_LXMF_WEDGE_CONFIRM_TICKS",
          "MESHFORGE_RNS_IFACE_DOWN_CONFIRM_TICKS")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for k in _KNOBS:
        monkeypatch.delenv(k, raising=False)
    core._rejected_knobs.clear()
    yield
    core._rejected_knobs.clear()


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "1.0", "0.5", "nope", "",
                                 "1_0", "+3", "٣", "1e1"])
def test_slow_factor_rejects_and_witnesses(monkeypatch, caplog, raw):
    monkeypatch.setenv(_SLOW, raw)
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        assert wpr._rnstatus_slow_factor() == wpr._DEFAULT_RNSTATUS_SLOW_FACTOR
    assert [r for r in caplog.records
            if _SLOW in r.getMessage() and "IGNORED" in r.getMessage()]


@pytest.mark.parametrize("raw, expected", [("2.5", 2.5), (" 4 ", 4.0), ("10", 10.0)])
def test_slow_factor_accepts_finite_above_one(monkeypatch, caplog, raw, expected):
    monkeypatch.setenv(_SLOW, raw)
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        assert wpr._rnstatus_slow_factor() == expected
    assert not caplog.records


def test_slow_factor_warns_once_per_value(monkeypatch, caplog):
    monkeypatch.setenv(_SLOW, "inf")
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        wpr._rnstatus_slow_factor()
        wpr._rnstatus_slow_factor()
    assert len([r for r in caplog.records if _SLOW in r.getMessage()]) == 1


def test_resolve_reports_every_knob_at_its_effective_value(monkeypatch):
    monkeypatch.setenv("MESHFORGE_RNS_IFACE_DOWN_CONFIRM_TICKS", "4")
    got = resolve_watchdog_knobs()
    assert got == {"rpc_confirm_ticks": 3, "lxmf_wedge_confirm_ticks": 5,
                   "iface_down_confirm_ticks": 4, "rnstatus_slow_factor": 3.0}


def test_resolve_witnesses_a_bad_knob(monkeypatch, caplog):
    monkeypatch.setenv("MESHFORGE_RNS_RPC_CONFIRM_TICKS", "3.0")
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        resolve_watchdog_knobs()
    assert any("MESHFORGE_RNS_RPC_CONFIRM_TICKS" in r.getMessage()
               for r in caplog.records)


def test_daemon_resolves_knobs_at_startup():
    """The runner's run loop calls it before its first tick (the live check
    of record is the journal's `watchdog: knobs` line after a restart)."""
    src = (Path(__file__).resolve().parent.parent / "src" / "utils"
           / "watchdog_runner.py").read_text(encoding="utf-8")
    run_loop = src[src.index("rehydrated = tracker.rehydrate"):]
    run_loop = run_loop[:run_loop.index("phase2_config = phase2_config or")]
    assert "resolve_watchdog_knobs()" in run_loop
