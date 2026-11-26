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
          "MESHFORGE_RNS_IFACE_DOWN_CONFIRM_TICKS",
          "MESHFORGE_CALIB_DRIFT_RECENT_DAYS",
          "MESHFORGE_LOCAL_BRAIN_REGRESSION_MIN_PASSES")


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
                   "iface_down_confirm_ticks": 4, "rnstatus_slow_factor": 3.0,
                   "calib_drift_recent_days": 7.0,
                   "local_brain_regression_min_passes": 2}


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


# ── deferred watchdog-knob-witness-residue (a)+(b), 2026-11-26 ─────────────
# The mini probes' knobs parsed inline with float()/int(): nan/inf/"3.0"/-1
# slipped through or fell back SILENTLY. They now share the knob family's
# parsers, resolve with the rest, and a rejected knob rides watchdog.json
# (a probe-visible witness that survives journal rotation, hfm #9).
from utils import watchdog_probes_mini as wpm  # noqa: E402

_DRIFT = "MESHFORGE_CALIB_DRIFT_RECENT_DAYS"
_PASSES = "MESHFORGE_LOCAL_BRAIN_REGRESSION_MIN_PASSES"


@pytest.mark.parametrize("raw", ["nan", "inf", "-1", "0", "1e1", "nope", ""])
def test_calib_drift_days_rejects_and_witnesses(monkeypatch, caplog, raw):
    monkeypatch.setenv(_DRIFT, raw)
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        assert wpm._calib_drift_recent_days() == 7.0
    assert any(_DRIFT in r.getMessage() and "IGNORED" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("raw", ["3.0", "-1", "nan", "1_0", "+2", "two", ""])
def test_min_prior_passes_rejects_and_witnesses(monkeypatch, caplog, raw):
    monkeypatch.setenv(_PASSES, raw)
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        assert wpm._local_brain_min_prior_passes() == 2
    assert any(_PASSES in r.getMessage() and "IGNORED" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("env, raw, fn, expected", [
    (_DRIFT, "2.5", "_calib_drift_recent_days", 2.5),
    (_PASSES, "0", "_local_brain_min_prior_passes", 0),
    (_PASSES, " 5 ", "_local_brain_min_prior_passes", 5),
])
def test_mini_knobs_accept_a_meant_value(monkeypatch, caplog, env, raw, fn, expected):
    monkeypatch.setenv(env, raw)
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        assert getattr(wpm, fn)() == expected
    assert not caplog.records


def test_resolve_reports_the_mini_knobs_the_probes_use(monkeypatch):
    # import-time knobs: the EFFECTIVE value is the module constant, so a
    # later env change must not show up as if a probe were running with it
    monkeypatch.setattr(wpm, "LOCAL_BRAIN_REGRESSION_MIN_PRIOR_PASSES", 4)
    monkeypatch.setattr(wpm, "CALIB_DRIFT_RECENT_WINDOW_S", 3 * 86400.0)
    monkeypatch.setenv(_PASSES, "9")
    got = resolve_watchdog_knobs()
    assert got["local_brain_regression_min_passes"] == 4
    assert got["calib_drift_recent_days"] == 3.0


def test_a_rejected_knob_rides_watchdog_json(monkeypatch, tmp_path):
    import json as _json
    from utils import watchdog_runner as wr
    monkeypatch.setenv(_DRIFT, "nan")
    wpm._calib_drift_recent_days()          # what the import-time parse does
    out = tmp_path / "watchdog.json"
    wr.write_state(out, host="h", now=1.0, probe_count=0, active_signals=[])
    doc = _json.loads(out.read_text())
    assert doc["knobs"]["rejected"] == [{"knob": _DRIFT, "value": "nan"}]
    assert doc["knobs"]["effective"]["calib_drift_recent_days"] == 7.0


def test_clean_knobs_write_an_empty_rejected_list(tmp_path):
    import json as _json
    from utils import watchdog_runner as wr
    out = tmp_path / "watchdog.json"
    wr.write_state(out, host="h", now=1.0, probe_count=0, active_signals=[])
    assert _json.loads(out.read_text())["knobs"]["rejected"] == []
