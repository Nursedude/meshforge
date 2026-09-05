"""Tests for scripts/battery_discharge_track.py.

The predecessor reported "100.0% after 1910.2h" for 79 days and never once
said anything was wrong. Every test here pins one of the four reasons that
was possible: a flat curve must eventually read BROKEN, a rising pack is
CHARGING (not a discharge), samples older than the run are not evidence, and
an unwritable log is reported rather than swallowed.
"""
import importlib.util
import json
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

_spec = importlib.util.spec_from_file_location(
    "bdt", os.path.join(_ROOT, "scripts", "battery_discharge_track.py"))
bdt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bdt)

H = 3600.0


def _s(ts, v, batt=100):
    return {"ts": ts, "voltage": v, "battery": batt}


class TestFlatCurveEventuallyReadsBroken:
    """THE defect: `drop < STEP` printed AWAITING-RESOLUTION forever, so a
    test measuring nothing was indistinguishable from one still settling."""

    def test_flat_past_the_horizon_is_broken(self):
        now = 1_000_000.0
        t0 = now - 20 * H
        samples = [_s(t0 + i * H, 4.198) for i in range(20)]
        verdict, detail = bdt.analyse(samples, t0, 4000, now)
        assert verdict == "broken"
        assert "not patience" in detail

    def test_flat_but_still_early_is_awaiting_not_broken(self):
        now = 1_000_000.0
        t0 = now - 2 * H
        samples = [_s(t0 + i * H, 4.198) for i in range(3)]
        verdict, _ = bdt.analyse(samples, t0, 4000, now)
        assert verdict == "awaiting"

    def test_no_samples_at_all_past_the_horizon_is_broken(self):
        """The exact 1910h shape: a run with nothing behind it."""
        now = 1_000_000.0
        verdict, detail = bdt.analyse([], now - 1910 * H, 4000, now)
        assert verdict == "broken"
        assert "nothing is being measured" in detail

    def test_no_samples_but_early_is_awaiting(self):
        now = 1_000_000.0
        verdict, _ = bdt.analyse([], now - 1 * H, 4000, now)
        assert verdict == "awaiting"


class TestChargingIsNotDischarging:
    """The old check was `battery_level >= 101`, a sentinel the node had never
    emitted — dead code. Voltage says it directly."""

    def test_rising_voltage_is_charging(self):
        now = 1_000_000.0
        t0 = now - 4 * H
        samples = [_s(t0, 3.900), _s(t0 + 2 * H, 4.050), _s(t0 + 4 * H, 4.190)]
        verdict, detail = bdt.analyse(samples, t0, 4000, now)
        assert verdict == "charging"
        assert "CHARGING" in detail

    def test_noise_sized_rise_is_not_called_charging(self):
        now = 1_000_000.0
        t0 = now - 4 * H
        samples = [_s(t0, 4.000), _s(t0 + 4 * H, 4.005)]   # +5 mV
        verdict, _ = bdt.analyse(samples, t0, 4000, now)
        assert verdict != "charging"


class TestRealDischarge:
    def test_declining_voltage_reports_a_rate_and_projection(self):
        now = 1_000_000.0
        t0 = now - 10 * H
        samples = [_s(t0 + i * H, 4.200 - 0.010 * i) for i in range(11)]
        verdict, detail = bdt.analyse(samples, t0, 4000, now)
        assert verdict == "running"
        assert "mV/h" in detail
        assert "to 3.3V" in detail or "3.3V" in detail

    def test_cutoff_completes_the_run(self):
        now = 1_000_000.0
        t0 = now - 48 * H
        samples = [_s(t0, 4.200), _s(now, 3.290)]
        verdict, detail = bdt.analyse(samples, t0, 4000, now)
        assert verdict == "complete"
        assert "48.0h" in detail

    def test_it_refuses_to_quote_mA_from_voltage_alone(self):
        """A number the curve cannot support is exactly what got us here."""
        now = 1_000_000.0
        t0 = now - 10 * H
        samples = [_s(t0 + i * H, 4.200 - 0.010 * i) for i in range(11)]
        _, detail = bdt.analyse(samples, t0, 4000, now)
        assert "mA cannot be derived" in detail


class TestSampleHygiene:
    def test_samples_without_voltage_are_ignored_not_zeroed(self):
        now = 1_000_000.0
        t0 = now - 20 * H
        samples = [{"ts": t0 + i * H, "voltage": None, "battery": 100}
                   for i in range(20)]
        verdict, detail = bdt.analyse(samples, t0, 4000, now)
        assert verdict == "broken"
        assert "not reporting telemetry" in detail

    def test_a_corrupt_line_does_not_void_the_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHFORGE_DISCHARGE_DIR", str(tmp_path))
        p = tmp_path / "run-x.jsonl"
        p.write_text('{"ts": 1.0, "voltage": 4.2}\nNOT JSON\n'
                     '{"ts": 2.0, "voltage": 4.1}\n')
        status, rows = bdt.read_run_log("run-x")
        assert status == "ok"
        assert len(rows) == 2

    def test_missing_log_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHFORGE_DISCHARGE_DIR", str(tmp_path))
        status, rows = bdt.read_run_log("nope")
        assert rows == []
        assert "no samples" in status

    def test_unwritable_log_is_reported_not_swallowed(self, tmp_path,
                                                      monkeypatch):
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a directory")
        monkeypatch.setenv("MESHFORGE_DISCHARGE_DIR", str(blocked / "sub"))
        err = bdt.append_sample("run-x", {"ts": 1.0, "voltage": 4.2})
        assert err, "a failed write must be reported"


class TestConfigRefusal:
    def test_absent_config_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHFORGE_DISCHARGE_CONFIG",
                           str(tmp_path / "nope.json"))
        cfg, err = bdt.load_config()
        assert cfg is None and "refusing to run" in err

    def test_config_without_node_id_refuses(self, tmp_path, monkeypatch):
        p = tmp_path / "c.json"
        p.write_text('{"capacity_mah": 4000}')
        monkeypatch.setenv("MESHFORGE_DISCHARGE_CONFIG", str(p))
        cfg, err = bdt.load_config()
        assert cfg is None and "node_id" in err


class TestStaleRunMarkerCannotInflateTheSpan:
    """The 1910h number was `now - t0` against a marker nobody disarmed. The
    span now comes from the SAMPLES, never from the marker."""

    def test_span_is_measured_between_samples_not_from_t0(self):
        now = 1_000_000.0
        t0 = now - 1910 * H              # ancient, stale marker
        samples = [_s(now - 2 * H, 4.200), _s(now, 4.100)]
        verdict, detail = bdt.analyse(samples, t0, 4000, now)
        assert verdict == "running"
        assert "over 2.0h" in detail, "span must come from the samples"
        assert "1910" not in detail


class TestEndToEndSampling:
    def _db(self, tmp_path, rows):
        import sqlite3
        db = tmp_path / "n.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE node_observations (timestamp REAL, "
                    "node_id TEXT, battery INTEGER, voltage REAL)")
        con.executemany("INSERT INTO node_observations VALUES (?,?,?,?)", rows)
        con.commit(); con.close()
        return str(db)

    def test_latest_observation_skips_null_voltage_rows(self, tmp_path):
        db = self._db(tmp_path, [
            (100.0, "!a", 100, 4.2),
            (200.0, "!a", 100, None),      # newer, but no voltage
        ])
        status, obs = bdt.latest_observation("!a", db)
        assert status == "ok"
        assert obs["voltage"] == pytest.approx(4.2)

    def test_node_with_no_voltage_at_all_says_so(self, tmp_path):
        db = self._db(tmp_path, [(100.0, "!a", 100, None)])
        status, obs = bdt.latest_observation("!a", db)
        assert obs is None
        assert "not be reporting telemetry" in status

    def test_unreadable_db_is_unobservable(self, tmp_path):
        status, obs = bdt.latest_observation("!a", str(tmp_path / "nope.db"))
        assert obs is None
        assert status.startswith("unobservable")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestSamplerDoesNotManufactureDuplicates:
    """Caught before wiring, 2026-09-05: the sampler runs on a fixed cron but
    a node is only OBSERVED when it reports — the target lands a row about
    hourly. Appending "the newest row" every tick writes the SAME observation
    six times an hour, and N identical points is exactly how the predecessor's
    flat line was manufactured. One observation, one sample."""

    def _setup(self, tmp_path, monkeypatch, obs_ts, existing_ts=None):
        import sqlite3
        monkeypatch.setenv("MESHFORGE_DISCHARGE_DIR", str(tmp_path))
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({"node_id": "!a", "capacity_mah": 4000}))
        monkeypatch.setenv("MESHFORGE_DISCHARGE_CONFIG", str(cfg))
        db = tmp_path / "n.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE node_observations (timestamp REAL, "
                    "node_id TEXT, battery INTEGER, voltage REAL)")
        con.execute("INSERT INTO node_observations VALUES (?,?,?,?)",
                    (obs_ts, "!a", 100, 4.198))
        con.commit(); con.close()
        monkeypatch.setenv("MESHFORGE_NODE_HISTORY_DB", str(db))
        (tmp_path / "current_run.json").write_text(json.dumps(
            {"run_id": "run-t", "t0": obs_ts - 600, "node_id": "!a",
             "capacity_mah": 4000, "label": "t"}))
        if existing_ts is not None:
            (tmp_path / "run-t.jsonl").write_text(json.dumps(
                {"ts": existing_ts, "voltage": 4.198, "battery": 100}) + "\n")
        return tmp_path / "run-t.jsonl"

    def test_first_sample_is_recorded(self, tmp_path, monkeypatch):
        log = self._setup(tmp_path, monkeypatch, obs_ts=1000.0)
        assert bdt.main(["--sample"]) == 0
        assert len(log.read_text().strip().splitlines()) == 1

    def test_same_observation_is_not_appended_twice(self, tmp_path,
                                                    monkeypatch, capsys):
        log = self._setup(tmp_path, monkeypatch, obs_ts=1000.0,
                          existing_ts=1000.0)
        assert bdt.main(["--sample"]) == 0
        assert len(log.read_text().strip().splitlines()) == 1, \
            "the same observation was recorded twice"
        assert "no new observation" in capsys.readouterr().out

    def test_a_genuinely_new_observation_is_appended(self, tmp_path,
                                                     monkeypatch):
        log = self._setup(tmp_path, monkeypatch, obs_ts=2000.0,
                          existing_ts=1000.0)
        assert bdt.main(["--sample"]) == 0
        assert len(log.read_text().strip().splitlines()) == 2

    def test_repeated_ticks_on_a_static_node_add_nothing(self, tmp_path,
                                                        monkeypatch):
        """Six cron ticks, one hourly report: one sample, not six."""
        log = self._setup(tmp_path, monkeypatch, obs_ts=1000.0)
        for _ in range(6):
            bdt.main(["--sample"])
        assert len(log.read_text().strip().splitlines()) == 1
