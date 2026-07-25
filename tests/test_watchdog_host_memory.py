"""Tests for ``probe_host_memory_pressure`` (2026-07-24 hard-reset arc).

The probe exists because the 07-24 manager-box reset was detectable and
undetected. These tests pin the two things that made it undetected, so a
future edit cannot quietly undo them:

  1. a degraded reading must not be swallowed forever by the debounce, and a
     WEDGE reading must bypass the debounce entirely (the real reset arrived
     34 s after the first sub-20% sample — one tick of budget);
  2. an unobservable reading must never render as healthy, and a missing PSI
     file must not silently become a "clean" vote on that leg.

The incident's own numbers are used as a regression fixture where possible so
the thresholds stay anchored to a real event rather than to taste.
"""

import pytest

from utils.watchdog_probe_core import (
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes import probe_host_memory_pressure
from utils.watchdog_probes_host import (
    _format_consumers,
    _read_meminfo,
    _read_psi_memory_avg60,
    _top_rss_consumers,
)


@pytest.fixture(autouse=True)
def _clean_recorder():
    reset_dispositions()
    yield
    reset_dispositions()


def _meminfo(tmp_path, *, total_kb, avail_kb, name="meminfo"):
    p = tmp_path / name
    p.write_text(
        f"MemTotal:       {total_kb} kB\n"
        f"MemFree:        {avail_kb} kB\n"
        f"MemAvailable:   {avail_kb} kB\n"
        f"Buffers:            1000 kB\n"
    )
    return str(p)


def _psi(tmp_path, *, avg60, name="pressure_memory"):
    p = tmp_path / name
    p.write_text(
        f"some avg10=1.00 avg60={avg60} avg300=0.50 total=123456\n"
        f"full avg10=0.50 avg60=0.20 avg300=0.10 total=65432\n"
    )
    return str(p)


def _run(tmp_path, *, total_kb, avail_kb, psi_avg60=0.0, streak_name="streak.json",
         **kw):
    return probe_host_memory_pressure(
        meminfo_path=_meminfo(tmp_path, total_kb=total_kb, avail_kb=avail_kb),
        psi_path=_psi(tmp_path, avg60=psi_avg60),
        debounce_path=str(tmp_path / streak_name),
        **kw,
    )


# ─────────────────────────────────────────────────────────────────────
# Healthy / quiet
# ─────────────────────────────────────────────────────────────────────

class TestHealthy:
    def test_silent_with_plenty_of_memory(self, tmp_path):
        sig = _run(tmp_path, total_kb=16_603_376, avail_kb=10_192_384)
        assert sig is None
        assert collect_dispositions()["host_memory_pressure"]["disp"] == "clean"

    def test_healthy_resets_the_streak(self, tmp_path):
        """A recovery must clear the debounce, or the next isolated dip pages."""
        # One degraded tick banks a streak of 1...
        _run(tmp_path, total_kb=1000, avail_kb=150)
        # ...then a healthy tick must clear it...
        _run(tmp_path, total_kb=1000, avail_kb=900)
        # ...so a fresh single degraded tick is still debounced, not fired.
        assert _run(tmp_path, total_kb=1000, avail_kb=150) is None


# ─────────────────────────────────────────────────────────────────────
# Availability leg
# ─────────────────────────────────────────────────────────────────────

class TestAvailabilityLeg:
    def test_degraded_debounced_then_fires(self, tmp_path):
        """First sub-20% tick is silent (transient spike); the second fires."""
        assert _run(tmp_path, total_kb=1000, avail_kb=150) is None
        disp = collect_dispositions()["host_memory_pressure"]
        assert disp["disp"] == "indeterminate"
        assert "1/2" in disp["reason"]

        sig = _run(tmp_path, total_kb=1000, avail_kb=150)
        assert sig is not None
        assert sig.severity == "degraded"
        assert sig.cls == "host_memory_pressure"

    def test_wedge_fires_immediately_no_debounce(self, tmp_path):
        """The 07-24 reset left 34 s of budget — a wedge cannot wait a tick."""
        sig = _run(tmp_path, total_kb=1000, avail_kb=50)  # 5%
        assert sig is not None
        assert sig.severity == "wedge"

    def test_incident_sample_is_degraded(self, tmp_path):
        """The real 23:45:01 sample: 2,556,176 kB of 16,603,376 kB = 15.4%."""
        _run(tmp_path, total_kb=16_603_376, avail_kb=2_556_176)  # debounce tick
        sig = _run(tmp_path, total_kb=16_603_376, avail_kb=2_556_176)
        assert sig is not None
        assert sig.severity == "degraded"
        assert sig.extra["avail_ratio"] == pytest.approx(0.1539, abs=1e-3)

    def test_incident_earlier_sample_is_quiet(self, tmp_path):
        """23:43:01 (9.85 GB avail, PSI 0.0) was genuinely healthy — no page."""
        assert _run(tmp_path, total_kb=16_603_376, avail_kb=10_330_160) is None


# ─────────────────────────────────────────────────────────────────────
# PSI stall leg
# ─────────────────────────────────────────────────────────────────────

class TestStallLeg:
    def test_psi_alone_can_fire_with_memory_looking_fine(self, tmp_path):
        """The 07-24 shape: the box died from a STALL, not from true
        exhaustion. Plenty 'available' must not veto the stall leg."""
        _run(tmp_path, total_kb=1000, avail_kb=900, psi_avg60=25.0)
        sig = _run(tmp_path, total_kb=1000, avail_kb=900, psi_avg60=25.0)
        assert sig is not None
        assert sig.severity == "degraded"
        assert "stall pressure" in sig.detail

    def test_psi_wedge_bypasses_debounce(self, tmp_path):
        sig = _run(tmp_path, total_kb=1000, avail_kb=900, psi_avg60=55.0)
        assert sig is not None
        assert sig.severity == "wedge"

    def test_worst_leg_wins(self, tmp_path):
        """Degraded availability + wedge stall => wedge."""
        sig = _run(tmp_path, total_kb=1000, avail_kb=150, psi_avg60=55.0)
        assert sig is not None
        assert sig.severity == "wedge"

    def test_missing_psi_does_not_vote_clean(self, tmp_path):
        """PSI absent must not become 0.0 (a degraded state wearing a
        healthy-looking value). The availability leg judges alone and SAYS so."""
        sig = probe_host_memory_pressure(
            meminfo_path=_meminfo(tmp_path, total_kb=1000, avail_kb=50),
            psi_path=str(tmp_path / "does_not_exist"),
            debounce_path=str(tmp_path / "streak.json"),
        )
        assert sig is not None
        assert sig.severity == "wedge"
        assert sig.extra["psi_some_avg60"] is None
        assert "UNOBSERVABLE" in sig.detail


# ─────────────────────────────────────────────────────────────────────
# Honest failure modes — unobservable never reads as healthy
# ─────────────────────────────────────────────────────────────────────

class TestUnobservable:
    def test_missing_meminfo_is_indeterminate_not_clean(self, tmp_path):
        sig = probe_host_memory_pressure(
            meminfo_path=str(tmp_path / "nope"),
            psi_path=_psi(tmp_path, avg60=0.0),
            debounce_path=str(tmp_path / "streak.json"),
        )
        assert sig is None
        disp = collect_dispositions()["host_memory_pressure"]
        assert disp["disp"] == "indeterminate"
        assert "unreadable" in disp["reason"]

    def test_meminfo_without_memavailable_is_indeterminate(self, tmp_path):
        p = tmp_path / "meminfo"
        p.write_text("MemTotal:  1000 kB\nMemFree:  500 kB\n")
        sig = probe_host_memory_pressure(
            meminfo_path=str(p),
            psi_path=_psi(tmp_path, avg60=0.0),
            debounce_path=str(tmp_path / "streak.json"),
        )
        assert sig is None
        assert collect_dispositions()["host_memory_pressure"]["disp"] == "indeterminate"

    def test_zero_memtotal_is_indeterminate_not_division_error(self, tmp_path):
        p = tmp_path / "meminfo"
        p.write_text("MemTotal:  0 kB\nMemAvailable:  0 kB\n")
        sig = probe_host_memory_pressure(
            meminfo_path=str(p),
            psi_path=_psi(tmp_path, avg60=0.0),
            debounce_path=str(tmp_path / "streak.json"),
        )
        assert sig is None
        assert collect_dispositions()["host_memory_pressure"]["disp"] == "indeterminate"

    def test_unobservable_holds_the_streak(self, tmp_path):
        """Going blind mid-spiral must neither page nor forget the streak."""
        _run(tmp_path, total_kb=1000, avail_kb=150)          # streak -> 1
        probe_host_memory_pressure(                           # blind tick
            meminfo_path=str(tmp_path / "nope"),
            psi_path=_psi(tmp_path, avg60=0.0),
            debounce_path=str(tmp_path / "streak.json"),
        )
        sig = _run(tmp_path, total_kb=1000, avail_kb=150)     # streak -> 2, fires
        assert sig is not None


# ─────────────────────────────────────────────────────────────────────
# The forensic witness — the whole reason the probe carries a roster
# ─────────────────────────────────────────────────────────────────────

class TestConsumerRoster:
    def test_detail_names_top_consumers(self, tmp_path):
        _run(tmp_path, total_kb=1000, avail_kb=50)
        sig = _run(tmp_path, total_kb=1000, avail_kb=50)
        assert sig is not None
        # Real /proc — the roster must be present and structured.
        assert sig.extra["top_rss"] is not None
        assert len(sig.extra["top_rss"]) >= 1
        assert "top RSS" in sig.detail
        first = sig.extra["top_rss"][0]
        assert set(first) == {"comm", "pid", "rss_kb"}

    def test_roster_is_sorted_descending(self, tmp_path):
        rows = _top_rss_consumers()
        assert rows is not None
        assert [r[2] for r in rows] == sorted((r[2] for r in rows), reverse=True)

    def test_unreadable_roster_says_so_rather_than_empty(self):
        """An empty list would read as 'nothing was using memory' — the exact
        degraded-value-overlap defect this fleet keeps re-learning."""
        assert "UNREADABLE" in _format_consumers(None)
        assert "unreadable" in _format_consumers([])

    def test_probe_still_fires_when_roster_unreadable(self, tmp_path):
        sig = probe_host_memory_pressure(
            meminfo_path=_meminfo(tmp_path, total_kb=1000, avail_kb=50),
            psi_path=_psi(tmp_path, avg60=0.0),
            proc_root=str(tmp_path / "no_such_proc"),
            debounce_path=str(tmp_path / "streak.json"),
        )
        assert sig is not None
        assert sig.severity == "wedge"
        assert sig.extra["top_rss"] is None
        assert "UNREADABLE" in sig.detail


# ─────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────

class TestParsers:
    def test_meminfo_parses_kb(self, tmp_path):
        got = _read_meminfo(_meminfo(tmp_path, total_kb=1234, avail_kb=567))
        assert got["MemTotal"] == 1234
        assert got["MemAvailable"] == 567

    def test_meminfo_missing_file_is_none(self, tmp_path):
        assert _read_meminfo(str(tmp_path / "nope")) is None

    def test_psi_parses_some_avg60(self, tmp_path):
        assert _read_psi_memory_avg60(_psi(tmp_path, avg60=12.34)) == 12.34

    def test_psi_missing_file_is_none_not_zero(self, tmp_path):
        assert _read_psi_memory_avg60(str(tmp_path / "nope")) is None

    def test_psi_garbage_is_none_not_zero(self, tmp_path):
        p = tmp_path / "psi"
        p.write_text("some avg10=x avg60=notanumber avg300=1\n")
        assert _read_psi_memory_avg60(str(p)) is None
