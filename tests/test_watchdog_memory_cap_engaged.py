"""Tests for ``probe_memory_cap_engaged`` (2026-07-24 hard-reset arc).

The probe exists because the same session that shipped hard ``MemoryMax`` caps to
eight fleet boxes shipped NO way to see them fire. A cap that OOM-kills an ssh
session or a user unit leaves nothing behind but a missing process — the
witness-less event of honest_failure_modes #9. These tests pin the properties
that make it a witness rather than another silent surface:

  1. a KILL is reported IMMEDIATELY (no debounce) — the process is already dead,
     so delay buys nothing, and the detail must carry BOTH possible readings
     because the counter genuinely cannot distinguish "runaway bounded" from
     "legitimate work killed by a too-tight cap";
  2. unobservable is never "no kills" — an unreadable ``memory.events`` on a
     capped cgroup is indeterminate, and a box with no cap at all is INERT;
  3. the counters are cumulative-since-boot, so a reboot must re-baseline
     WITHOUT losing a kill that already happened this boot, and a counter that
     DECREASED (cgroup destroyed and recreated) must never produce a negative
     delta or read as recovery.
"""

import json
import os

import pytest

from utils.watchdog_probe_core import (
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes import probe_memory_cap_engaged
from utils.watchdog_probes_host import (
    _load_cap_state,
    _read_memory_events,
)


@pytest.fixture(autouse=True)
def _clean_dispositions():
    reset_dispositions()
    yield
    reset_dispositions()


def _cgroup(root, rel, *, cap="max", current=0, events=None, no_events=False):
    """Build a fake cgroup dir. ``cap='max'`` = uncapped (the common case)."""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.max").write_text(f"{cap}\n")
    (d / "memory.current").write_text(f"{current}\n")
    if not no_events:
        ev = {"low": 0, "high": 0, "max": 0, "oom": 0,
              "oom_kill": 0, "oom_group_kill": 0}
        ev.update(events or {})
        (d / "memory.events").write_text(
            "".join(f"{k} {v}\n" for k, v in ev.items()))
    return d


def _bootid(tmp_path, value="boot-aaaa", name="bootid"):
    p = tmp_path / name
    p.write_text(value + "\n")
    return str(p)


def _run(tmp_path, root, *, boot="boot-aaaa", state="cap_state.json", **kw):
    return probe_memory_cap_engaged(
        cgroup_root=str(root),
        state_path=str(tmp_path / state),
        boot_id_path=_bootid(tmp_path, boot),
        **kw)


# ── self-guards: three distinct honest answers, never one shared silence ──

class TestSelfGuards:
    def test_no_capped_cgroup_is_inert_not_clean(self, tmp_path):
        """moc3's case: controller off / nothing capped => nothing to judge."""
        root = tmp_path / "cg"
        _cgroup(root, "user.slice", cap="max")
        _cgroup(root, "system.slice", cap="max")
        assert _run(tmp_path, root) == []
        assert collect_dispositions()["memory_cap_engaged"]["disp"] == "inert"

    def test_unwalkable_tree_is_indeterminate(self, tmp_path):
        sigs = _run(tmp_path, tmp_path / "does-not-exist")
        assert sigs == []
        assert collect_dispositions()["memory_cap_engaged"]["disp"] == "indeterminate"

    def test_unreadable_events_on_capped_cgroup_is_indeterminate(self, tmp_path):
        """The load-bearing one: absence of a readable counter must NOT read as
        'no kills happened'."""
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30, no_events=True)
        sigs = _run(tmp_path, root)
        assert sigs == []
        d = collect_dispositions()["memory_cap_engaged"]
        assert d["disp"] == "indeterminate"
        assert "unreadable" in d["reason"]

    def test_capped_and_quiet_emits_nothing(self, tmp_path):
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30, current=1 << 28)
        assert _run(tmp_path, root) == []

    def test_healthy_path_still_records_a_clean_disposition(self, tmp_path):
        """Found live, not by fixture: recording NOTHING on the healthy path
        leaves the class absent from the coverage view, which conflates
        'ran, observed, clean' with 'never ran' — the conflation the
        disposition recorder exists to remove."""
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        assert _run(tmp_path, root) == []
        d = collect_dispositions().get("memory_cap_engaged")
        assert d is not None, "healthy tick recorded no disposition at all"
        assert d["disp"] == "clean"
        assert "1 capped cgroup" in d["reason"]

    def test_a_fired_signal_still_records_a_disposition(self, tmp_path):
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30,
                events={"oom_kill": 1})
        assert len(_run(tmp_path, root)) == 1
        assert collect_dispositions()["memory_cap_engaged"]["disp"] == "clean"


# ── the KILL leg ──

class TestKillLeg:
    def test_first_look_this_boot_reports_existing_kills(self, tmp_path):
        """A kill that happened before we ever looked still happened THIS boot
        (the counters reset at boot), so it must not be swallowed as 'baseline'."""
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30,
                events={"oom_kill": 3, "max": 12})
        sigs = _run(tmp_path, root)
        assert len(sigs) == 1
        assert sigs[0].cls == "memory_cap_engaged"
        assert sigs[0].severity == "wedge"
        assert sigs[0].subject == "user.slice/user-1000.slice"
        assert sigs[0].extra["first_look_this_boot"] is True

    def test_new_kill_fires_immediately_without_debounce(self, tmp_path):
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        assert _run(tmp_path, root) == []                     # baseline, quiet
        (cg / "memory.events").write_text("max 5\noom_kill 1\noom_group_kill 0\n")
        sigs = _run(tmp_path, root)
        assert len(sigs) == 1 and sigs[0].severity == "wedge"
        assert sigs[0].extra["new_kills"] == 1

    def test_kill_detail_carries_BOTH_readings_and_the_victim_command(self, tmp_path):
        """The probe must not pretend to know which of the two causes it is."""
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30,
                events={"oom_kill": 1})
        detail = _run(tmp_path, root)[0].detail
        assert "runaway" in detail and "LEGITIMATE" in detail
        assert "killed process" in detail          # how to name the victim
        assert "memory.max" in detail              # verify at the KERNEL

    def test_group_kill_counts_too(self, tmp_path):
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30,
                events={"oom_group_kill": 2})
        assert len(_run(tmp_path, root)) == 1

    def test_steady_kill_count_stops_firing(self, tmp_path):
        """Cumulative counters must not pin the signal on forever — only NEW
        kills are news."""
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30,
                events={"oom_kill": 1})
        assert len(_run(tmp_path, root)) == 1     # first look reports it
        assert _run(tmp_path, root) == []         # unchanged => quiet


# ── reboot / cgroup-recreation handling (honest_failure_modes #6) ──

class TestCounterResets:
    def test_boot_change_rebaselines_and_reports_current_count(self, tmp_path):
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30,
                     events={"oom_kill": 4})
        assert len(_run(tmp_path, root, boot="boot-aaaa")) == 1
        assert _run(tmp_path, root, boot="boot-aaaa") == []
        # Reboot: counters restart at 0, then a kill happens.
        (cg / "memory.events").write_text("max 0\noom_kill 1\noom_group_kill 0\n")
        sigs = _run(tmp_path, root, boot="boot-bbbb", state="cap_state.json")
        assert len(sigs) == 1
        assert sigs[0].extra["first_look_this_boot"] is True

    def test_decreased_counter_never_yields_a_negative_delta_or_a_signal(self, tmp_path):
        """Cgroup destroyed + recreated within one boot (user slice on last
        logout without linger). Re-baseline silently; never 'recovered'."""
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30,
                     events={"oom_kill": 5, "max": 9})
        assert len(_run(tmp_path, root)) == 1          # first look
        (cg / "memory.events").write_text("max 0\noom_kill 0\noom_group_kill 0\n")
        assert _run(tmp_path, root) == []              # decrease => quiet
        # And the new baseline is the LOW value, so the next real kill fires.
        (cg / "memory.events").write_text("max 0\noom_kill 1\noom_group_kill 0\n")
        assert len(_run(tmp_path, root)) == 1

    def test_load_state_discards_baselines_from_another_boot(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"boot_id": "old", "cgroups": {"x": {"oom": 7}}}))
        st = _load_cap_state(str(p), "new")
        assert st["cgroups"] == {} and st["fresh"] is True

    def test_load_state_survives_garbage(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("{not json")
        st = _load_cap_state(str(p), "b")
        assert st["cgroups"] == {} and st["fresh"] is True


# ── the CEILING leg (debounced) ──

class TestCeilingLeg:
    def _tick(self, tmp_path, root, cg, hits):
        (cg / "memory.events").write_text(
            f"max {hits}\noom_kill 0\noom_group_kill 0\n")
        return _run(tmp_path, root)

    def test_one_touch_of_the_limit_is_not_a_signal(self, tmp_path):
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        assert self._tick(tmp_path, root, cg, 0) == []
        assert self._tick(tmp_path, root, cg, 4) == []      # streak 1

    def test_sustained_ceiling_fires_degraded(self, tmp_path):
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        self._tick(tmp_path, root, cg, 0)
        self._tick(tmp_path, root, cg, 4)                   # streak 1
        sigs = self._tick(tmp_path, root, cg, 9)            # streak 2 => fire
        assert len(sigs) == 1
        assert sigs[0].severity == "degraded"
        assert sigs[0].extra["streak"] >= 2
        # It must point at the evidence-based next step, not a calendar.
        assert "memory.peak" in sigs[0].detail

    def test_streak_resets_when_the_ceiling_stops_being_hit(self, tmp_path):
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        self._tick(tmp_path, root, cg, 0)
        self._tick(tmp_path, root, cg, 4)                   # streak 1
        assert self._tick(tmp_path, root, cg, 4) == []      # no rise => reset
        assert self._tick(tmp_path, root, cg, 8) == []      # streak 1 again

    def test_a_kill_outranks_the_ceiling_leg(self, tmp_path):
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        self._tick(tmp_path, root, cg, 0)
        self._tick(tmp_path, root, cg, 4)
        (cg / "memory.events").write_text(
            "max 9\noom_kill 1\noom_group_kill 0\n")
        sigs = _run(tmp_path, root)
        assert len(sigs) == 1 and sigs[0].severity == "wedge"


# ── the ceiling benignity gate (2026-08-28) ──

def _psi(cg, avg10):
    (cg / "memory.pressure").write_text(
        f"some avg10={avg10} avg60=0.00 avg300=0.00 total=100\n"
        f"full avg10=0.00 avg60=0.00 avg300=0.00 total=100\n")


def _stat(cg, *, anon_mb, shmem_mb=0, file_mb=0):
    (cg / "memory.stat").write_text(
        f"anon {anon_mb << 20}\nfile {file_mb << 20}\nshmem {shmem_mb << 20}\n")


class TestCeilingBenignityGate:
    """A slice riding its cap on clean page cache is the cap WORKING (live
    case 2026-08-28: 18k max_hits, PSI 0.00, 5.2 GB file cache, zero kills). The
    leg must page only on the costly/dangerous shapes — and an unreadable
    discriminator must page, never silence (the benign claim needs positive
    evidence, honest_failure_modes #2)."""

    def _ride_to_streak(self, tmp_path, root, cg):
        """Two quiet baseline-building ticks; returns the third tick's result.

        Dispositions are reset before the third tick: the recorder is
        worst-wins within one collection window, so an equal-severity later
        note does not override — without the reset, assertions on the reason
        would read tick 1's note, not the tick under test."""
        (cg / "memory.events").write_text("max 0\noom_kill 0\noom_group_kill 0\n")
        assert _run(tmp_path, root) == []
        (cg / "memory.events").write_text("max 40\noom_kill 0\noom_group_kill 0\n")
        assert _run(tmp_path, root) == []                    # streak 1
        (cg / "memory.events").write_text("max 90\noom_kill 0\noom_group_kill 0\n")
        reset_dispositions()
        return _run(tmp_path, root)                          # streak 2

    def test_cache_riding_with_low_psi_is_suppressed_with_a_witness(self, tmp_path):
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        _psi(cg, "0.00")
        _stat(cg, anon_mb=200, shmem_mb=50, file_mb=1700)    # cache-dominated
        assert self._ride_to_streak(tmp_path, root, cg) == []
        d = collect_dispositions()["memory_cap_engaged"]
        assert d["disp"] == "clean"
        # The suppression must NOT be a silent swallow (#9).
        assert "benign" in d["reason"]
        assert "user.slice/user-1000.slice" in d["reason"]

    def test_high_psi_fires_and_names_the_pressure(self, tmp_path):
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        _psi(cg, "23.70")
        _stat(cg, anon_mb=200, file_mb=1700)
        sigs = self._ride_to_streak(tmp_path, root, cg)
        assert len(sigs) == 1 and sigs[0].severity == "degraded"
        assert "COSTING" in sigs[0].detail
        assert sigs[0].extra["psi_some_avg10"] == pytest.approx(23.70)

    def test_anon_dominated_fires_even_with_zero_psi(self, tmp_path):
        """Unreclaimable memory over half the cap is one burst from kills —
        PSI can momentarily read 0.00 there and it is still not benign."""
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        _psi(cg, "0.00")
        _stat(cg, anon_mb=1100, shmem_mb=100, file_mb=300)   # >50% of 2048 MB
        sigs = self._ride_to_streak(tmp_path, root, cg)
        assert len(sigs) == 1
        assert "UNRECLAIMABLE" in sigs[0].detail

    def test_unreadable_discriminators_page_never_silence(self, tmp_path):
        """The pre-existing fixtures have no memory.pressure/memory.stat at all
        — that shape must keep paging, with the blindness named."""
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        sigs = self._ride_to_streak(tmp_path, root, cg)
        assert len(sigs) == 1
        assert "cannot rule" in sigs[0].detail

    def test_benign_turn_dangerous_pages_without_reserving_the_debounce(self, tmp_path):
        """The streak keeps counting through suppressed benign ticks, so the
        first dangerous tick pages immediately."""
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        _psi(cg, "0.00")
        _stat(cg, anon_mb=200, file_mb=1700)
        assert self._ride_to_streak(tmp_path, root, cg) == []   # benign, streak 2
        _psi(cg, "31.00")                                        # reclaim now stalls
        (cg / "memory.events").write_text("max 150\noom_kill 0\noom_group_kill 0\n")
        sigs = _run(tmp_path, root)
        assert len(sigs) == 1
        assert sigs[0].extra["streak"] >= 3

    def test_a_kill_bypasses_the_benignity_gate_entirely(self, tmp_path):
        """The gate refines the CEILING leg only — a kill is a kill."""
        root = tmp_path / "cg"
        cg = _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        _psi(cg, "0.00")
        _stat(cg, anon_mb=100, file_mb=1800)
        (cg / "memory.events").write_text("max 5\noom_kill 1\noom_group_kill 0\n")
        sigs = _run(tmp_path, root)
        assert len(sigs) == 1 and sigs[0].severity == "wedge"


# ── multi-cap boxes: one subject per cap, stable identity ──

class TestMultipleCaps:
    def test_each_capped_cgroup_is_its_own_subject(self, tmp_path):
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30,
                events={"oom_kill": 1})
        _cgroup(root, "system.slice/ollama.service", cap=8 << 30,
                events={"oom_kill": 2})
        _cgroup(root, "system.slice/rnsd.service", cap="max")   # uncapped, ignored
        sigs = _run(tmp_path, root)
        assert {s.subject for s in sigs} == {
            "user.slice/user-1000.slice", "system.slice/ollama.service"}

    def test_exceeding_the_subject_bound_is_indeterminate_not_clean(self, tmp_path):
        """A bound must not read as coverage: caps beyond it were never looked
        at, so claiming 'clean' would assert a verdict this tick did not make."""
        root = tmp_path / "cg"
        for i in range(4):
            _cgroup(root, f"system.slice/svc{i}.service", cap=1 << 30)
        assert _run(tmp_path, root, max_subjects=2) == []
        d = collect_dispositions()["memory_cap_engaged"]
        assert d["disp"] == "indeterminate"
        assert "NOT judged" in d["reason"]

    def test_state_write_failure_warns_and_does_not_raise(self, tmp_path, caplog):
        root = tmp_path / "cg"
        _cgroup(root, "user.slice/user-1000.slice", cap=2 << 30)
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file, not a directory")
        sigs = probe_memory_cap_engaged(
            cgroup_root=str(root),
            state_path=str(blocker / "nested" / "s.json"),
            boot_id_path=_bootid(tmp_path))
        assert sigs == []                       # no crash on bookkeeping
        # The swallow must leave a witness — a frozen debounce that says nothing
        # is how every other probe using this pattern went silent (2026-07-21 W4).
        assert "unwritable" in caplog.text


# ── the raw reader ──

class TestReadMemoryEvents:
    def test_parses_counters(self, tmp_path):
        p = tmp_path / "memory.events"
        p.write_text("low 0\nhigh 2\nmax 5\noom 1\noom_kill 3\n")
        assert _read_memory_events(str(p))["oom_kill"] == 3

    def test_missing_file_is_none_not_empty(self, tmp_path):
        assert _read_memory_events(str(tmp_path / "nope")) is None

    def test_garbage_lines_are_skipped_not_fatal(self, tmp_path):
        p = tmp_path / "memory.events"
        p.write_text("garbage\nmax notanumber\noom_kill 2\n")
        assert _read_memory_events(str(p)) == {"oom_kill": 2}

    def test_all_garbage_is_none(self, tmp_path):
        p = tmp_path / "memory.events"
        p.write_text("nothing parseable here\n")
        assert _read_memory_events(str(p)) is None


# ── registry contract ──

class TestRegistered:
    def test_class_is_in_the_closed_enum(self):
        from utils.watchdog_probe_core import SIGNAL_CLASSES, SEVERITIES
        assert "memory_cap_engaged" in SIGNAL_CLASSES
        assert {"wedge", "degraded"} <= set(SEVERITIES)

    def test_both_role_seeds_route_the_class(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for seed in ("configs/mini_dudeai_rules.federator.json",
                     "configs/mini_dudeai_rules.fleet_gateway.json"):
            doc = json.load(open(os.path.join(root, seed)))
            classes = {r.get("match", {}).get("class") for r in doc["rules"]}
            assert "memory_cap_engaged" in classes, seed
