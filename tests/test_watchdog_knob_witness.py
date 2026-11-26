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
    from utils import watchdog_probes_mini as _wpm
    # import-time constants: pin them, never trust the collecting env (review r1 nit)
    monkeypatch.setattr(_wpm, "CALIB_DRIFT_RECENT_WINDOW_S", 7 * 86400.0)
    monkeypatch.setattr(_wpm, "LOCAL_BRAIN_REGRESSION_MIN_PRIOR_PASSES", 2)
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
    assert "announce_watchdog_knobs(logger)" in run_loop   # resolves the knobs (r2: moved to the hub)


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


# ── review r1 (083b3deb..ac7b25cd) IMPORTANT: the knobs block had no reader ──
# watchdog.json carried it, but both transforms that hand watchdog state on
# (/api/status via status_blocks, the fleet view via fleet_snapshot) copied
# only ts/ok/probe_count/signals(/coverage) — the witness died at the file.
_KNOBS_BLOCK = {"effective": {"calib_drift_recent_days": 7.0},
                "rejected": [{"knob": _DRIFT, "value": "nan"}]}


def test_api_status_block_carries_the_knobs():
    from utils.status_blocks import watchdog_block_from_payload
    block = watchdog_block_from_payload(
        {"ts": 100.0, "ok": True, "signals": [], "knobs": _KNOBS_BLOCK}, now=101.0)
    assert block["knobs"] == _KNOBS_BLOCK


def test_fleet_snapshot_block_carries_the_knobs(monkeypatch, tmp_path):
    import json as _json
    import time as _time
    from utils import fleet_snapshot as fs
    p = tmp_path / "watchdog.json"
    p.write_text(_json.dumps({"ts": _time.time(), "ok": True, "signals": [],
                              "knobs": _KNOBS_BLOCK}))
    monkeypatch.setattr(fs, "_WATCHDOG_STATE_PATH", str(p))
    assert fs._watchdog_block()["knobs"] == _KNOBS_BLOCK


def test_a_knobs_block_that_is_not_an_object_is_not_passed_on():
    from utils.status_blocks import watchdog_block_from_payload
    block = watchdog_block_from_payload(
        {"ts": 100.0, "ok": True, "signals": [], "knobs": ["junk"]}, now=101.0)
    assert "knobs" not in block


def test_the_probe_uses_the_constant_the_report_names(monkeypatch):
    """review r1 nit: a def-time default froze the value; resolve read the
    module constant — the two could disagree. Now the probe reads it at call."""
    import inspect
    assert inspect.signature(wpm.probe_local_brain_regressed).parameters[
        "min_prior_passes"].default is None


# ── review r2 (ac7b25cd..f669b808) IMPORTANT: nothing an operator reads SHOWED it ──
# Same defect kind twice (r1: no reader of the file; r2: no renderer of the
# block) -> the seam is a SIGNAL: every surface already renders signals.
def test_the_witness_rides_an_existing_class_no_new_one():
    """operator 11-26: class budget held at 62 — no watchdog_knob_ignored class."""
    from utils.watchdog_probes import SIGNAL_CLASSES
    assert "watchdog_knob_ignored" not in SIGNAL_CLASSES
    assert "mini_watchdog_source_unwired" in SIGNAL_CLASSES


def test_an_ignored_knob_fires_one_degraded_signal_naming_it():
    sigs = wpm.probe_watchdog_knob_ignored([{"knob": _DRIFT, "value": "nan"}])
    assert [(s.cls, s.subject, s.severity) for s in sigs] == [
        ("mini_watchdog_source_unwired", _DRIFT, "degraded")]
    assert "'nan'" in sigs[0].detail and "NOT live" in sigs[0].detail


def test_the_probe_reads_what_the_parsers_refused(monkeypatch):
    monkeypatch.setenv("MESHFORGE_RNS_RPC_CONFIRM_TICKS", "3.0")
    resolve_watchdog_knobs()
    assert [s.subject for s in wpm.probe_watchdog_knob_ignored()] == [
        "MESHFORGE_RNS_RPC_CONFIRM_TICKS"]


def test_no_ignored_knob_is_clean():
    assert wpm.probe_watchdog_knob_ignored() == []


def test_the_runner_ticks_the_knob_probe():
    src = (Path(__file__).resolve().parent.parent / "src" / "utils"
           / "watchdog_runner.py").read_text(encoding="utf-8")
    assert "signals.extend(probe_watchdog_knob_ignored())" in src


@pytest.mark.parametrize("preset", ["fleet_gateway", "federator"])
def test_every_fleet_preset_escalates_it_without_paging(preset):
    import json as _json
    path = (Path(__file__).resolve().parent.parent / "configs"
            / f"mini_dudeai_rules.{preset}.json")
    doc = _json.loads(path.read_text(encoding="utf-8"))
    rules = doc["rules"] if isinstance(doc, dict) else doc
    cls = [r for r in rules if r.get("match", {}).get("class") == "mini_watchdog_source_unwired"]
    knob = [r for r in cls if r["match"]["subject_glob"] == "MESHFORGE_*"]
    assert len(knob) == 1 and knob[0]["action"]["kind"] == "propose_escalation"
    # the original rule must not ALSO fire (wrong fix text) on a knob subject
    other = [r for r in cls if r is not knob[0]]
    assert other and all("MESHFORGE_*" in r["match"].get("subject_exclude_globs", [])
                         for r in other)


# r2 nits: the startup announce is BEHAVIOUR, not a source grep; it names
# only IMPORT-time rejections (per-call knobs already warned in resolve);
# it never aborts startup.
def test_startup_announce_warns_import_time_rejections_once(caplog):
    from utils import watchdog_probes as wp
    core._rejected_knobs.add((_DRIFT, "nan"))
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        wp.announce_watchdog_knobs()
    hits = [r for r in caplog.records if _DRIFT in r.getMessage()]
    assert len(hits) == 1 and "IGNORED" in hits[0].getMessage()


def test_startup_announce_does_not_double_warn_a_per_call_knob(monkeypatch, caplog):
    from utils import watchdog_probes as wp
    monkeypatch.setenv("MESHFORGE_RNS_IFACE_DOWN_CONFIRM_TICKS", "0")
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        wp.announce_watchdog_knobs()
    assert len([r for r in caplog.records
                if "MESHFORGE_RNS_IFACE_DOWN_CONFIRM_TICKS" in r.getMessage()]) == 1


def test_startup_announce_never_raises(monkeypatch, caplog):
    from utils import watchdog_probes as wp

    def boom():
        raise TypeError("bad constant")
    monkeypatch.setattr(wp, "resolve_watchdog_knobs", boom)
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        wp.announce_watchdog_knobs()        # must not raise
    assert any("knob posture unavailable" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("floor, fires", [(1, True), (3, False)])
def test_the_probe_uses_the_module_constant_at_call_time(monkeypatch, floor, fires):
    """r2 nit: behaviour, not the signature — 2 prior passes then a fail
    fires at floor 1 and stays quiet at floor 3."""
    monkeypatch.setattr(wpm, "LOCAL_BRAIN_REGRESSION_MIN_PRIOR_PASSES", floor)
    recs = _brain_records(prior_passes=2)
    assert (wpm.probe_local_brain_regressed(records=recs) is not None) is fires


# r2 nit: knob posture from a dead / frozen writer is labelled, never live
def test_stale_knob_posture_is_labelled_stale():
    from utils.status_blocks import watchdog_block_from_payload
    block = watchdog_block_from_payload(
        {"ts": 100.0, "ok": True, "signals": [], "knobs": _KNOBS_BLOCK},
        now=100.0 + 10_000)
    assert block["knobs"] == {"state": "stale"}


# r2 pre_existing: fleet_snapshot hand-copied the transform and had drifted
# (no future-ts leg, ok via bool()) -> it now calls status_blocks
def test_fleet_snapshot_uses_the_shared_transform(monkeypatch, tmp_path):
    import json as _json
    import time as _time
    from utils import fleet_snapshot as fs
    p = tmp_path / "watchdog.json"
    p.write_text(_json.dumps({"ts": _time.time() + 3600, "ok": True, "signals": []}))
    monkeypatch.setattr(fs, "_WATCHDOG_STATE_PATH", str(p))
    assert fs._watchdog_block()["ok"] is False      # future stamp is a clock fault
    p.write_text(_json.dumps({"ts": _time.time(), "ok": "false", "signals": []}))
    assert fs._watchdog_block()["ok"] is False      # only a real True is ok


def _brain_records(prior_passes):
    """``prior_passes`` passing eval runs of one case, then a failing one."""
    recs = [{"ts": i + 1, "results": [{"id": "t1", "kind": "triage", "ok": True}]}
            for i in range(prior_passes)]
    recs.append({"ts": prior_passes + 1,
                 "results": [{"id": "t1", "kind": "triage", "ok": False}]})
    return recs
