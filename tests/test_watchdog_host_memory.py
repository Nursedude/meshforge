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

import json
import os

import pytest

from utils.watchdog_probe_core import (
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes import probe_host_memory_pressure
from utils.watchdog_probes_host import (
    _format_consumers,
    _read_vmhwm,
    _scan_processes,
    _rate_drop,
    _read_meminfo,
    _read_psi_memory_avg60,
    _top_cgroups,
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


def _bootid(tmp_path, name="bootid"):
    p = tmp_path / name
    if not p.exists():
        p.write_text("11111111-2222-3333-4444-555555555555\n")
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
# RATE leg — the one that actually buys warning time
# ─────────────────────────────────────────────────────────────────────

class TestRateLeg:
    """Replays the real 07-24 samples through the probe tick by tick.

    MemTotal 16,603,376 kB. MemAvailable: 10,330,160 (23:40) -> 10,289,440
    (23:40) -> 9,849,072 (23:43) -> 5,237,792 (23:44) -> 2,556,176 (23:45),
    then the box died at 23:45:35. The level legs cannot fire until 23:45:01
    and, debounced, land ~4 s before the reset. The rate leg has to do better.
    """

    TOTAL = 16_603_376
    SAMPLES = [                      # (monotonic_s, MemAvailable_kb)
        (0.0,   10_330_160),         # 23:40
        (60.0,   9_849_072),         # 23:43-ish
        (120.0,  5_237_792),         # 23:44  <- the collapse becomes visible
        (180.0,  2_556_176),         # 23:45
    ]

    def _tick(self, tmp_path, mono, avail, **kw):
        return probe_host_memory_pressure(
            meminfo_path=_meminfo(tmp_path, total_kb=self.TOTAL, avail_kb=avail,
                                  name=f"meminfo{int(mono)}"),
            psi_path=_psi(tmp_path, avg60=0.0),
            debounce_path=str(tmp_path / "rate.json"),
            boot_id_path=_bootid(tmp_path),
            now_mono=mono,
            **kw)

    def test_fires_before_the_level_legs_would(self, tmp_path):
        """THE point of this leg: warning at the 23:44 sample, not the 23:45 one.

        At 23:44 availability is 31.5% — comfortably ABOVE the 20% level gate,
        so the level legs are still silent. The slope is what is screaming.
        """
        assert self._tick(tmp_path, *self.SAMPLES[0]) is None
        assert self._tick(tmp_path, *self.SAMPLES[1]) is None
        sig = self._tick(tmp_path, *self.SAMPLES[2])
        assert sig is not None, "rate leg did not fire on the 23:44 sample"
        assert sig.extra["rate_fired"] is True
        assert sig.severity == "degraded"
        assert "availability fell" in sig.detail
        # Level legs genuinely were not the trigger here.
        assert sig.extra["avail_ratio"] > 0.20

    def test_rate_leg_is_not_debounced_away(self, tmp_path):
        """It spans >= rate_min_span_s by construction, so a second tick of
        delay would re-spend exactly the warning time it exists to buy."""
        self._tick(tmp_path, *self.SAMPLES[0])
        self._tick(tmp_path, *self.SAMPLES[1])
        sig = self._tick(tmp_path, *self.SAMPLES[2])
        assert sig is not None      # fired on FIRST qualifying tick

    def test_a_big_but_fine_allocation_stays_quiet(self, tmp_path):
        """Measured ollama model load on this box: ~6 GB in ~43 s, settling near
        47% available. That clears the slope and must NEVER clear the floor —
        otherwise the probe pages on every model load and gets ignored."""
        assert self._tick(tmp_path, 0.0, 13_600_000) is None
        assert self._tick(tmp_path, 60.0, 7_600_000) is None   # 45.8% avail

    def test_single_sample_is_never_a_slope(self, tmp_path):
        """One reading cannot constitute a trend — the claw-battery lesson."""
        sig = self._tick(tmp_path, 0.0, 1_000_000)   # 6% avail: level fires...
        assert sig is not None
        assert sig.extra["rate_drop_ratio"] is None  # ...but NOT via rate

    def test_recovery_reports_zero_drop_not_a_negative_slope(self, tmp_path):
        self._tick(tmp_path, 0.0, 5_000_000)
        sig = self._tick(tmp_path, 60.0, 12_000_000)
        assert sig is None                            # recovered -> silent

    def test_history_is_discarded_across_a_reboot(self, tmp_path):
        """monotonic RESTARTS at boot; carrying samples over would make the
        newest look older than the oldest and manufacture an absurd slope. This
        fleet reboots unexpectedly, which is why the probe exists at all."""
        self._tick(tmp_path, 500.0, 10_000_000)
        state = json.loads((tmp_path / "rate.json").read_text())
        assert state["hist"], "history not persisted"
        # same file, DIFFERENT boot id -> history must be dropped
        other = tmp_path / "bootid2"
        other.write_text("ffffffff-ffff-ffff-ffff-ffffffffffff\n")
        # Wedge-level availability (6% < 8%) so it fires on this very tick and
        # the rate field is observable; a degraded-level value would debounce.
        sig = probe_host_memory_pressure(
            meminfo_path=_meminfo(tmp_path, total_kb=self.TOTAL,
                                  avail_kb=1_000_000, name="mi_reboot"),
            psi_path=_psi(tmp_path, avg60=0.0),
            debounce_path=str(tmp_path / "rate.json"),
            boot_id_path=str(other), now_mono=5.0)
        assert sig is not None                     # wedge leg fires (6%)
        assert sig.extra["rate_drop_ratio"] is None, (
            "slope computed across a reboot boundary")

    def test_negative_monotonic_span_is_ignored(self):
        """Defensive: a backwards span is impossible, so it must not be used."""
        hist = [[100.0, 9_000_000.0], [50.0, 2_000_000.0]]
        assert _rate_drop(hist, 60.0, 16_000_000,
                          window_s=180.0, min_span_s=45.0) is None

    def test_rate_drop_needs_the_minimum_span(self):
        hist = [[0.0, 9_000_000.0], [10.0, 2_000_000.0]]
        assert _rate_drop(hist, 10.0, 16_000_000,
                          window_s=180.0, min_span_s=45.0) is None

    def test_rate_drop_ignores_samples_older_than_the_window(self):
        hist = [[0.0, 16_000_000.0], [200.0, 15_000_000.0]]
        got = _rate_drop(hist, 250.0, 16_000_000,
                         window_s=180.0, min_span_s=45.0)
        assert got is not None
        assert got[0] < 0.10, "used a sample from outside the window"


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
        assert set(first) == {"comm", "pid", "rss_kb", "vmhwm_kb",
                             "cmdline", "cgroup"}

    def test_roster_carries_cmdline_and_cgroup_not_just_comm(self, tmp_path):
        """`comm` alone cannot attribute: this box runs two processes whose comm
        is plain `python`, one over 600 MB. cmdline+cgroup is what distinguishes
        a SERVICE from an interactive/agent session — the exact question the
        07-24 post-mortem could not answer."""
        rows = _top_rss_consumers()
        assert rows is not None and rows
        assert any(r["cmdline"] for r in rows), "no cmdline captured at all"
        assert any(r["cgroup"] for r in rows), "no cgroup captured at all"

    def test_roster_is_sorted_descending(self, tmp_path):
        rows = _top_rss_consumers()
        assert rows is not None
        sizes = [r["rss_kb"] for r in rows]
        assert sizes == sorted(sizes, reverse=True)

    def test_cgroup_attribution_present_and_sorted(self, tmp_path):
        """The aggregate view: a session reaching GBs as dozens of small
        processes is invisible per-process but obvious per-cgroup."""
        rows = _top_cgroups()
        if rows is None:
            pytest.skip("no cgroup memory accounting on this box")
        sizes = [r["current_kb"] for r in rows]
        assert sizes == sorted(sizes, reverse=True)
        assert all({"cgroup", "current_kb", "peak_kb"} == set(r) for r in rows)

    def test_detail_carries_non_rss_legs(self, tmp_path):
        """tmpfs/shm and kernel slab are charged to NO process, so a top-RSS
        roster names nothing when the memory went there."""
        _run(tmp_path, total_kb=1000, avail_kb=50)
        sig = _run(tmp_path, total_kb=1000, avail_kb=50)
        assert sig is not None
        assert "non-RSS:" in sig.detail
        assert isinstance(sig.extra["non_rss_kb"], dict)

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
# VmHWM — the burst allocator that current-RSS can never see
# ─────────────────────────────────────────────────────────────────────

def _fake_proc(tmp_path, procs):
    """Build a synthetic /proc. ``procs`` = [(pid, rss_kb, vmhwm_kb, cmd)].

    Synthetic on purpose: a real spike-then-release cannot be manufactured on
    the live box, and that is exactly the case 07-24 needed and lacked.
    """
    root = tmp_path / "proc"
    root.mkdir(exist_ok=True)
    for pid, rss_kb, vmhwm_kb, cmd in procs:
        d = root / str(pid)
        d.mkdir(exist_ok=True)
        # statm field[1] is resident PAGES; tests assume a 4 KiB page.
        (d / "statm").write_text(f"99999 {rss_kb // 4} 0 0 0 0 0\n")
        (d / "comm").write_text(cmd.split()[0] + "\n")
        (d / "cmdline").write_text(cmd.replace(" ", "\x00") + "\x00")
        (d / "cgroup").write_text(f"0::/user.slice/session-{pid}.scope\n")
        status = "Name:\tx\nVmRSS:\t%d kB\n" % rss_kb
        if vmhwm_kb is not None:
            status += "VmHWM:\t%d kB\n" % vmhwm_kb
        status += "VmSwap:\t0 kB\n"
        (d / "status").write_text(status)
    return str(root)


class TestReleasedPeaks:
    def test_burst_allocator_is_surfaced_though_rss_is_tiny(self, tmp_path):
        """THE gap this closes. A process at 40 MB now with a 5 GB high-water
        mark cannot appear in any current-RSS top-5 — yet it is the one that
        took the memory. On 07-24 that shape would have gone unfound."""
        proc = _fake_proc(tmp_path, [
            (101, 800_000, 800_000, "steady-service"),
            (102, 700_000, 700_000, "another-service"),
            (103, 600_000, 600_000, "third"),
            (104, 500_000, 500_000, "fourth"),
            (105, 400_000, 400_000, "fifth"),
            (999,  40_000, 5_000_000, "python burst_allocator.py"),
        ])
        sig = probe_host_memory_pressure(
            meminfo_path=_meminfo(tmp_path, total_kb=1000, avail_kb=50),
            psi_path=_psi(tmp_path, avg60=0.0), proc_root=proc,
            debounce_path=str(tmp_path / "s.json"),
            boot_id_path=_bootid(tmp_path))
        assert sig is not None
        rss_pids = {r["pid"] for r in sig.extra["top_rss"]}
        assert 999 not in rss_pids, "fixture wrong: burster should NOT be top-RSS"
        peak_pids = {r["pid"] for r in sig.extra["released_peaks"]}
        assert 999 in peak_pids, "burst allocator not surfaced by VmHWM"
        assert "RELEASED peaks" in sig.detail
        assert "burst_allocator" in sig.detail

    def test_no_duplication_between_the_two_views(self, tmp_path):
        proc = _fake_proc(tmp_path, [
            (201, 900_000, 4_000_000, "big-and-spiky"),
            (202, 100_000, 120_000, "small"),
        ])
        sig = probe_host_memory_pressure(
            meminfo_path=_meminfo(tmp_path, total_kb=1000, avail_kb=50),
            psi_path=_psi(tmp_path, avg60=0.0), proc_root=proc,
            debounce_path=str(tmp_path / "s.json"),
            boot_id_path=_bootid(tmp_path))
        assert 201 in {r["pid"] for r in sig.extra["top_rss"]}
        assert 201 not in {r["pid"] for r in sig.extra["released_peaks"]}, (
            "a pid already in the RSS view must not be repeated")

    def test_small_or_modest_peaks_are_not_noise(self, tmp_path):
        """A 60 MB process that doubled is noise on a 16 GB board, and a process
        merely 1.2x its current RSS never released anything."""
        proc = _fake_proc(tmp_path, [
            (301, 30_000, 60_000, "tiny-doubled"),
            (302, 500_000, 600_000, "grew-a-bit"),
        ])
        sig = probe_host_memory_pressure(
            meminfo_path=_meminfo(tmp_path, total_kb=1000, avail_kb=50),
            psi_path=_psi(tmp_path, avg60=0.0), proc_root=proc,
            debounce_path=str(tmp_path / "s.json"),
            boot_id_path=_bootid(tmp_path))
        assert sig.extra["released_peaks"] == []
        assert "no released peaks" in sig.detail

    def test_missing_vmhwm_is_none_not_zero(self, tmp_path):
        """Kernel threads have no mm and no VmHWM. None means unknown."""
        proc = _fake_proc(tmp_path, [(401, 100_000, None, "kthread")])
        rows = _scan_processes(proc_root=proc)
        assert rows and rows[0]["vmhwm_kb"] is None

    def test_vmhwm_parsed_from_real_proc(self):
        """Positive control against the live kernel, not just the fixture."""
        val = _read_vmhwm(f"/proc/{os.getpid()}/status")
        assert isinstance(val, int) and val > 0

    def test_unreadable_status_is_none(self, tmp_path):
        assert _read_vmhwm(str(tmp_path / "nope")) is None


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


# ─────────────────────────────────────────────────────────────────────
# State-write failure must not silently disable the probe
#
# Found 2026-07-26 by live drill during the post-07-23 audit. _save_hist_state
# swallowed OSError with logger.debug (the runner defaults to INFO, so it was
# invisible) and _load_hist_state mapped ANY read failure to streak=0. On a
# persistently unwritable path the streak could therefore never reach
# debounce_ticks, and hist stayed empty so the RATE leg had no samples --
# silently reducing a three-leg probe to its wedge "tombstone" leg, which the
# module's own docstring says arrives ~4s before the box dies instead of ~94s.
#
# Drill-measured BEFORE the fix: 0/10 degraded ticks fired on an unwritable
# path vs 9/10 on a writable one. This is Issue #60's class (sandbox path
# drift -> writes fail silently while the service stays active).
# ─────────────────────────────────────────────────────────────────────

class TestStateWriteFailureDoesNotSuppress:
    @staticmethod
    def _unwritable(tmp_path):
        """A path that can never be created: its parent is a FILE."""
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        return str(blocker / "streak.json")

    def test_degraded_leg_still_fires_when_state_is_unwritable(self, tmp_path):
        """The debounce must survive on in-process state when disk fails.

        Without this, a broken state path is indistinguishable from a box that
        is never under pressure -- the exact 'degraded state wearing a healthy
        value' shape (honest_failure_modes #1/#2).
        """
        path = self._unwritable(tmp_path)
        fired = []
        for tick in range(6):
            sig = probe_host_memory_pressure(
                meminfo_path=_meminfo(tmp_path, total_kb=16_000_000,
                                      avail_kb=2_400_000),   # 15% -> degraded
                psi_path=_psi(tmp_path, avg60=0.0),
                debounce_path=path,
                boot_id_path=_bootid(tmp_path),
                now_mono=1000.0 + tick * 30.0,
            )
            if sig:
                fired.append(sig)
        assert fired, (
            "degraded leg never fired with an unwritable state file — a broken "
            "write path silently disabled the probe"
        )

    def test_write_failure_is_surfaced_not_swallowed(self, tmp_path):
        """Every swallow leaves a witness (honest_failure_modes #9)."""
        path = self._unwritable(tmp_path)
        sig = None
        for tick in range(6):
            got = probe_host_memory_pressure(
                meminfo_path=_meminfo(tmp_path, total_kb=16_000_000,
                                      avail_kb=800_000),     # 5% -> wedge
                psi_path=_psi(tmp_path, avg60=0.0),
                debounce_path=path,
                boot_id_path=_bootid(tmp_path),
                now_mono=2000.0 + tick * 30.0,
            )
            sig = got or sig
        assert sig is not None
        assert sig.extra.get("state_write_errors"), (
            "a persistently failing state write left no probe-visible witness"
        )

    def test_writable_path_reports_no_write_errors(self, tmp_path):
        """The witness must not cry wolf on a healthy box."""
        sig = None
        for tick in range(6):
            got = probe_host_memory_pressure(
                meminfo_path=_meminfo(tmp_path, total_kb=16_000_000,
                                      avail_kb=800_000),
                psi_path=_psi(tmp_path, avg60=0.0),
                debounce_path=str(tmp_path / "ok" / "streak.json"),
                boot_id_path=_bootid(tmp_path),
                now_mono=3000.0 + tick * 30.0,
            )
            sig = got or sig
        assert sig is not None
        assert not sig.extra.get("state_write_errors")
