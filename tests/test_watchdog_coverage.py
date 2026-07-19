"""Fleet-truth Phase 0 — producer coverage dispositions.

The watchdog producer now emits a per-``SIGNAL_CLASSES`` disposition map
(``coverage`` key of watchdog.json), assembled from the per-tick disposition
recorder in ``watchdog_probe_core``. The contract under test is FAIL-DARK:

- a class nothing noted is ``unknown`` (renders dark) — silence never green;
- an active Signal outranks any note;
- worst-wins merge (indeterminate > inert > clean) per class per tick;
- every member of the closed enum appears in the map (a silently-missing
  class would be indistinguishable from "not watched");
- the ``coverage`` key rides watchdog.json → ``/api/status.watchdog`` →
  ``fleet_truth.merge_coverage`` unchanged, and its absence (legacy writer)
  keeps the pre-Phase-0 all-dark behavior.
"""

import json
import time
from pathlib import Path

import pytest

from utils.watchdog_probe_core import (
    SIGNAL_CLASSES,
    Signal,
    collect_dispositions,
    note_disposition,
    reset_dispositions,
)
from utils.watchdog_runner import build_coverage, write_state


@pytest.fixture(autouse=True)
def _clean_recorder():
    reset_dispositions()
    yield
    reset_dispositions()


class TestDispositionRecorder:
    def test_note_and_collect(self):
        note_disposition("service_inactive", "clean")
        got = collect_dispositions()
        assert got["service_inactive"] == {"disp": "clean"}

    def test_reason_carried(self):
        note_disposition("parity_drift", "inert", reason="no sister repo on this box")
        assert collect_dispositions()["parity_drift"] == {
            "disp": "inert", "reason": "no sister repo on this box"}

    def test_worst_wins_upgrade(self):
        note_disposition("service_inactive", "clean")
        note_disposition("service_inactive", "indeterminate", reason="one unit unreadable")
        assert collect_dispositions()["service_inactive"]["disp"] == "indeterminate"

    def test_worst_wins_never_downgrades(self):
        note_disposition("service_inactive", "indeterminate", reason="x")
        note_disposition("service_inactive", "clean")
        assert collect_dispositions()["service_inactive"]["disp"] == "indeterminate"

    def test_inert_outranks_clean(self):
        note_disposition("host_frozen", "clean")
        note_disposition("host_frozen", "inert", reason="not the brain box")
        assert collect_dispositions()["host_frozen"]["disp"] == "inert"

    def test_invalid_disposition_becomes_indeterminate_not_clean(self):
        """A programming error must never read as a healthy-looking value."""
        note_disposition("service_inactive", "helthy")  # typo'd disp
        got = collect_dispositions()["service_inactive"]
        assert got["disp"] == "indeterminate"
        assert "invalid disposition" in got["reason"]

    def test_reset_clears(self):
        note_disposition("service_inactive", "clean")
        reset_dispositions()
        assert collect_dispositions() == {}


class TestBuildCoverage:
    def test_every_signal_class_present(self):
        """The coverage completeness gate: every member of the closed enum
        gets an entry — pre-noted or not."""
        cov = build_coverage([])
        assert set(cov.keys()) == set(SIGNAL_CLASSES)

    def test_unnoted_class_is_unknown_never_clean(self):
        cov = build_coverage([])
        for cls, entry in cov.items():
            assert entry["disp"] == "unknown", f"{cls} defaulted to {entry['disp']}"

    def test_active_signal_wins_over_note(self):
        note_disposition("service_inactive", "clean")
        sig = Signal(cls="service_inactive", subject="u.service",
                     severity="degraded", detail="d")
        cov = build_coverage([sig])
        assert cov["service_inactive"]["disp"] == "active"

    def test_noted_dispositions_pass_through(self):
        note_disposition("parity_drift", "inert", reason="no sister repo")
        note_disposition("cron_verdict_stale", "clean")
        note_disposition("channel_feed_dark", "indeterminate", reason="no json uplink")
        cov = build_coverage([])
        assert cov["parity_drift"] == {"disp": "inert", "reason": "no sister repo"}
        assert cov["cron_verdict_stale"] == {"disp": "clean"}
        assert cov["channel_feed_dark"]["disp"] == "indeterminate"


class TestWriteStateCoverage:
    def _write(self, tmp_path: Path, **kw) -> dict:
        out = tmp_path / "watchdog.json"
        write_state(out, host="testbox", now=time.time(), probe_count=1,
                    active_signals=[], **kw)
        return json.loads(out.read_text())

    def test_coverage_emitted_when_passed(self, tmp_path):
        payload = self._write(
            tmp_path, coverage={"service_inactive": {"disp": "clean"}})
        assert payload["coverage"] == {"service_inactive": {"disp": "clean"}}

    def test_legacy_shape_unchanged_when_coverage_none(self, tmp_path):
        payload = self._write(tmp_path)
        assert "coverage" not in payload


class TestWatchdogBlockPassthrough:
    """/api/status.watchdog carries the coverage map verbatim; absence on a
    legacy writer keeps the pre-Phase-0 (all-dark) consumer behavior."""

    def _block(self, tmp_path, payload: dict) -> dict:
        from utils.map_http_handler import MapRequestHandler
        state = tmp_path / "watchdog.json"
        state.write_text(json.dumps(payload))
        h = MapRequestHandler.__new__(MapRequestHandler)
        h._WATCHDOG_STATE_PATH = state
        return h._read_watchdog_block()

    def test_coverage_passed_through(self, tmp_path):
        cov = {"service_inactive": {"disp": "clean"},
               "parity_drift": {"disp": "inert", "reason": "no sister repo"}}
        block = self._block(tmp_path, {"ts": time.time(), "ok": True,
                                       "signals": [], "coverage": cov})
        assert block["coverage"] == cov

    def test_absent_coverage_absent_in_block(self, tmp_path):
        block = self._block(tmp_path, {"ts": time.time(), "ok": True, "signals": []})
        assert "coverage" not in block

    def test_malformed_coverage_dropped_not_crashed(self, tmp_path):
        block = self._block(tmp_path, {"ts": time.time(), "ok": True,
                                       "signals": [], "coverage": "garbage"})
        assert "coverage" not in block
        assert block["ok"] is True


class TestMergeCoverageProducerReasons:
    """fleet_truth.merge_coverage consumes the producer map: clean counts
    green; producer reasons ride through to the rendered cell."""

    def test_producer_clean_counts_green(self):
        from utils.fleet_truth import merge_coverage
        wb = {"installed": True, "ok": True, "signals": [],
              "coverage": {"a": {"disp": "clean"}, "b": {"disp": "inert",
                           "reason": "organ absent by role"}}}
        cov = merge_coverage(wb, ["a", "b", "c"])
        assert cov["green"] == 1 and cov["dark"] == 2
        assert cov["classes"]["a"]["disp"] == "clean"
        assert cov["classes"]["b"]["reason"] == "organ absent by role"
        assert cov["classes"]["c"]["disp"] == "unknown"

    def test_indeterminate_reason_passthrough(self):
        from utils.fleet_truth import merge_coverage
        wb = {"installed": True, "ok": True, "signals": [],
              "coverage": {"a": {"disp": "indeterminate",
                                 "reason": "journal unavailable"}}}
        cov = merge_coverage(wb, ["a"])
        assert cov["classes"]["a"] == {"disp": "indeterminate",
                                      "reason": "journal unavailable"}
        assert cov["dark"] == 1 and cov["green"] == 0


class TestRunnerGateInertNotes:
    """run_all_probes notes inert for the probes its own gates skip. Probes
    are stubbed to isolate the runner's gate behavior."""

    def test_no_rns_instance_notes_inert(self, monkeypatch):
        import utils.watchdog_runner as wr
        # Stub every probe call to a no-op so only runner-level notes land.
        for name in dir(wr):
            if name.startswith("probe_"):
                fn = getattr(wr, name)
                if callable(fn):
                    ret = [] if name in ("probe_lxmf_process_wedge",
                                         "probe_tracer_peer_unreachable") else None
                    monkeypatch.setattr(wr, name, lambda *a, _r=ret, **k: _r)
        monkeypatch.setattr(wr, "run_rnstatus", lambda **k: None)
        signals = wr.run_all_probes(
            rns_instance_name=None,
            services_expected_active=(),   # no map, no rnsd expected
            services_wedge_check=(),
        )
        cov = wr.build_coverage(signals)
        assert cov["rns_namespace_collision"]["disp"] == "inert"
        assert cov["rns_shared_instance_unresponsive"]["disp"] == "inert"
        assert cov["http_local_unresponsive"]["disp"] == "inert"
        assert cov["fd_exhaustion"]["disp"] == "inert"
        assert cov["phoneapi_tcp_leak"]["disp"] == "inert"
        assert cov["aredn_source_dark"]["disp"] == "inert"
        assert cov["foundation_perms_drift"]["disp"] == "inert"
        assert cov["rns_version_drift"]["disp"] == "inert"
