"""Tests for probe_rf_leg_silent — does the RF leg actually carry traffic?

The probe exists because on 2026-09-08 two RNodes went deaf and every surface
still read healthy: interface Up, service active, box reachable, traffic
flowing — over TCP. So these tests are weighted heavily toward the cases that
must NOT fire, because a probe that cries wolf on a single-node RF network
would be wrong every day and get ignored by the time it mattered.

Two inputs, by design (review 2026-09-09): the parsed TEXT status is the gate
(unobservable / no RNode here), and the RAW integer counters from
``rnstatus -j`` are what is judged — the text counters are display-quantised
to 10 kB above 1 MB and cannot see one tick of RF traffic.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import watchdog_probes_rf_leg as rf  # noqa: E402
from utils.rns_status_parser import RNSStatus  # noqa: E402
from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions,
    reset_dispositions,
)


@dataclass
class FakeCounter:
    bytes_total: float = 0.0
    bytes_unit: str = "B"


@dataclass
class FakeStatusEnum:
    name: str = "UP"


@dataclass
class FakeIface:
    type_name: str = "RNodeInterface"
    display_name: str = "Regional RNode RF"
    status: FakeStatusEnum = field(default_factory=FakeStatusEnum)
    tx: FakeCounter = field(default_factory=FakeCounter)
    rx: FakeCounter = field(default_factory=FakeCounter)


@dataclass
class FakeStatus:
    interfaces: list = field(default_factory=list)
    parse_error: object = None
    timed_out: bool = False


NAME = "Regional RNode RF"


def text_iface(tx=FakeCounter(1.15, "MB"), rx=FakeCounter(0.0, "B"),
               name=NAME, type_name="RNodeInterface"):
    """The runner's parsed text view — the gate, never the counters."""
    return FakeIface(type_name=type_name, display_name=name, tx=tx, rx=rx)


def raw(tx_b, rx_b, up=True, name=NAME):
    """One ``rnstatus -j`` interface entry, integer counters."""
    return {"name": f"RNodeInterface[{name}]", "short_name": name,
            "type": "RNodeInterface", "status": bool(up),
            "rxb": int(rx_b), "txb": int(tx_b)}


def tick(tmp_path, tx_b, rx_b, *, up=True, silence=3, **kw):
    """One probe observation against a persistent state file.

    ``silence`` shortens BOTH thresholds — the calibrated floor
    (MIN_SILENCE_TICKS) and the uncalibrated one
    (UNCALIBRATED_SILENCE_TICKS) — so the classic sequences stay readable.
    These tests are about the FAULT, the counters and the state; the
    calibration policy itself is exercised explicitly in TestPeerCadence.
    """
    kw.setdefault("uncalibrated_ticks", silence)
    return rf.probe_rf_leg_silent(
        status=FakeStatus([text_iface()]),
        raw_stats=[raw(tx_b, rx_b, up=up)],
        state_path=str(tmp_path / "rf_leg_state.json"),
        min_silence_ticks=silence,
        **kw,
    )


@pytest.fixture(autouse=True)
def _fresh_dispositions():
    reset_dispositions()
    # getattr: lets the file run against the pre-fix module so the planted
    # tests fail as TESTS, not as a fixture error
    getattr(rf, "_state_mem_fallback", {}).clear()
    yield
    getattr(rf, "_state_mem_fallback", {}).clear()


def disposition():
    return collect_dispositions().get("rf_leg_silent", {}) or {}


class TestInertCases:
    """Silence that is correct. Getting these wrong makes the probe noise."""

    def test_no_rnode_interface_is_inert(self, tmp_path):
        """Most of the fleet has no RF leg at all — absent by design."""
        st = FakeStatus([text_iface(type_name="TCPInterface")])
        assert rf.probe_rf_leg_silent(
            status=st, raw_stats=[], state_path=str(tmp_path / "s.json")) is None
        assert disposition()["disp"] == "inert"

    def test_never_received_never_fires_however_much_we_transmit(self, tmp_path):
        """THE false-positive that would have made this probe useless.

        The fleet's only RNode sat at 1.15 MB up / 0 B down for months because
        nothing else was on the air. That is correct, not a fault.
        """
        for i in range(1, 30):
            assert tick(tmp_path, tx_b=1000 * i, rx_b=0) is None
        assert disposition()["disp"] == "inert"

    def test_down_interface_is_left_to_other_probes(self, tmp_path):
        for i in range(1, 8):
            assert tick(tmp_path, tx_b=1000 * i, rx_b=500, up=False) is None

    def test_healthy_two_way_traffic_is_clean(self, tmp_path):
        for i in range(1, 8):
            assert tick(tmp_path, tx_b=1000 * i, rx_b=800 * i) is None
        assert disposition()["disp"] == "clean"


class TestTheRealFault:
    def test_fires_when_a_leg_that_used_to_hear_goes_deaf(self, tmp_path):
        # establish that a peer exists
        assert tick(tmp_path, tx_b=1000, rx_b=500) is None
        # now RX pins while TX keeps climbing
        assert tick(tmp_path, tx_b=2000, rx_b=500) is None   # flat 1
        assert tick(tmp_path, tx_b=3000, rx_b=500) is None   # flat 2
        sig = tick(tmp_path, tx_b=4000, rx_b=500)            # flat 3 >= floor 3
        assert sig is not None
        assert sig.cls == "rf_leg_silent"
        assert sig.severity == "degraded"

    def test_debounce_means_one_tick_is_not_enough(self, tmp_path):
        assert tick(tmp_path, tx_b=1000, rx_b=500) is None
        assert tick(tmp_path, tx_b=9000, rx_b=500) is None

    def test_the_detail_names_the_known_cause_and_the_masking(self, tmp_path):
        tick(tmp_path, tx_b=1000, rx_b=500)
        for i in range(2, 6):
            sig = tick(tmp_path, tx_b=1000 * i, rx_b=500)
        assert "promiscuous" in sig.detail
        assert "IP backhaul" in sig.detail, "must warn that nothing else reports this"

    def test_recovery_clears_the_streak(self, tmp_path):
        tick(tmp_path, tx_b=1000, rx_b=500)
        tick(tmp_path, tx_b=2000, rx_b=500)
        tick(tmp_path, tx_b=3000, rx_b=500)
        # a single received byte resets it
        assert tick(tmp_path, tx_b=4000, rx_b=900) is None
        assert tick(tmp_path, tx_b=5000, rx_b=900) is None

    def test_tx_is_judged_across_the_window_not_per_tick(self, tmp_path):
        """A quiet leg's own TX is sparse (its ID every 600 s). The old
        per-tick "+200 B" test reset the streak on every TX-less tick, so a
        quiet deaf leg could never fire — blind in its own use case."""
        tick(tmp_path, tx_b=1000, rx_b=500)
        tick(tmp_path, tx_b=1300, rx_b=500)   # one burst
        tick(tmp_path, tx_b=1300, rx_b=500)   # then nothing
        sig = tick(tmp_path, tx_b=1300, rx_b=500)
        assert sig is not None, "300 B across the silent window is 'using the leg'"

    def test_no_tx_at_all_is_not_transmitting_into_silence(self, tmp_path):
        tick(tmp_path, tx_b=1000, rx_b=500)
        for _ in range(6):
            assert tick(tmp_path, tx_b=1000, rx_b=500) is None


class TestPeerCadence:
    """Finding 2 (2026-09-09): the discriminator measured the gap BETWEEN a
    peer's announces, not deafness. id_interval=600 makes the fleet peer
    legitimately silent ~20 ticks at a time; any 3-tick TX burst paged."""

    def _peer_every(self, tmp_path, period, cycles, **kw):
        """A peer heard once per ``period`` ticks while our TX grows every tick."""
        tx, rx, t = 0, 0, 0
        for _ in range(cycles):
            for step in range(period):
                t += 1
                tx += 500
                if step == period - 1:
                    rx += 100
                assert tick(tmp_path, tx_b=tx, rx_b=rx, **kw) is None, f"paged at tick {t}"
        return tx, rx

    def test_silence_shorter_than_the_observed_cadence_never_pages(self, tmp_path):
        # floor 6 > period 5, so the calibration cycles cannot trip the floor.
        # cycles=6 -> 5 recorded gaps (the first RX has no measured-from
        # point), which is MIN_GAP_OBSERVATIONS: the estimate is only trusted once
        # it rests on enough samples, so the cadence path needs a calibrated leg.
        tx, rx = self._peer_every(tmp_path, period=5, cycles=6, silence=6)
        # observed gap 5 ticks -> requirement max(6, 2*5) = 10 flat ticks
        for i in range(1, 10):
            assert tick(tmp_path, tx_b=tx + 500 * i, rx_b=rx, silence=6) is None, \
                f"paged after {i} flat ticks; the peer has been quiet 5 before"

    def test_silence_beyond_twice_the_cadence_pages(self, tmp_path):
        tx, rx = self._peer_every(tmp_path, period=5, cycles=6, silence=6)
        for i in range(1, 10):
            tick(tmp_path, tx_b=tx + 500 * i, rx_b=rx, silence=6)
        sig = tick(tmp_path, tx_b=tx + 5000, rx_b=rx, silence=6)
        assert sig is not None
        assert "5 ticks" in sig.detail, "the page must quote the observed cadence"

    def test_floor_is_one_id_interval_while_no_cadence_is_known(self, tmp_path):
        """With no gap observed the floor applies: 20 ticks at 30 s = 600 s."""
        assert rf.MIN_SILENCE_TICKS == 20
        tick(tmp_path, tx_b=1000, rx_b=500, silence=rf.MIN_SILENCE_TICKS)
        for i in range(2, rf.MIN_SILENCE_TICKS + 1):
            assert tick(tmp_path, tx_b=1000 * i, rx_b=500,
                        silence=rf.MIN_SILENCE_TICKS) is None, f"paged at {i - 1} flat ticks"
        assert tick(tmp_path, tx_b=1000 * (rf.MIN_SILENCE_TICKS + 1), rx_b=500,
                    silence=rf.MIN_SILENCE_TICKS) is not None

    def test_required_silence_arithmetic(self):
        cal = dict(gap_count=rf.MIN_GAP_OBSERVATIONS)
        assert rf.required_silence_ticks(0, min_silence_ticks=20, **cal) == 20
        assert rf.required_silence_ticks(5, min_silence_ticks=20, **cal) == 20
        assert rf.required_silence_ticks(15, min_silence_ticks=20, **cal) == 30
        assert rf.required_silence_ticks(7, min_silence_ticks=3, gap_factor=1.5,
                                         **cal) == 11

    def test_an_estimate_from_too_few_samples_is_not_trusted(self):
        """THE 2026-09-10 flap, in arithmetic form.

        Two RNode boxes fired 15× and 5× on healthy legs because ONE observed
        gap set the threshold: a 16-tick sighting made 32 ticks of silence
        'deaf', and each false fire taught it more (16 → 52). Below
        MIN_GAP_OBSERVATIONS the conservative floor applies instead, whatever
        the sample happens to say.
        """
        for seen in range(rf.MIN_GAP_OBSERVATIONS):
            assert rf.required_silence_ticks(16, gap_count=seen) == \
                rf.UNCALIBRATED_SILENCE_TICKS, \
                f"trusted a cadence built from {seen} observation(s)"
        assert rf.required_silence_ticks(
            16, gap_count=rf.MIN_GAP_OBSERVATIONS) == 32
        assert rf.UNCALIBRATED_SILENCE_TICKS > 2 * 52, (
            "the uncalibrated floor must exceed twice the widest cadence the "
            "fleet actually learned (moc3: 52 ticks), or it cannot prevent "
            "the flap it exists for")


class TestCalibrationIsBounded:
    """The two defects behind the 2026-09-10 delta
    ``chronic_flap::rf_leg_silent_any::RNodeInterface[RNode LoRa]``.

    Both are the same shape: a cadence estimate believed more than the
    evidence under it supports.
    """

    def _peer_every(self, tmp_path, period, cycles, **kw):
        tx, rx = 0, 0
        for _ in range(cycles):
            for step in range(period):
                tx += 500
                if step == period - 1:
                    rx += 100
                tick(tmp_path, tx_b=tx, rx_b=rx, **kw)
        return tx, rx

    def _gaps(self, tmp_path):
        st = json.loads((tmp_path / "rf_leg_state.json").read_text())
        return st[f"RNodeInterface[{NAME}]"]

    def test_the_moc3_flap_does_not_reproduce(self, tmp_path):
        """moc3, 2026-09-09/10: 15 fires on a leg whose RX kept climbing.

        One observed 16-tick gap set the threshold to 32, so 32 flat ticks on a
        peer whose REAL cadence reached 52 ticks read as deafness. Replayed with
        the live floors, the leg must stay quiet.
        """
        tx, rx = 0, 0
        for step in range(16):          # one real gap, then the peer is heard
            tx += 500
            tick(tmp_path, tx_b=tx, rx_b=rx, silence=rf.MIN_SILENCE_TICKS,
                 uncalibrated_ticks=rf.UNCALIBRATED_SILENCE_TICKS)
        rx += 100
        tick(tmp_path, tx_b=tx, rx_b=rx, silence=rf.MIN_SILENCE_TICKS,
             uncalibrated_ticks=rf.UNCALIBRATED_SILENCE_TICKS)
        # ...now the 52-tick silence that moc3 actually showed, which the
        # 16-tick estimate called deafness 15 times.
        for i in range(1, 53):
            assert tick(tmp_path, tx_b=tx + 500 * i, rx_b=rx,
                        silence=rf.MIN_SILENCE_TICKS,
                        uncalibrated_ticks=rf.UNCALIBRATED_SILENCE_TICKS) is None, \
                f"fired after {i} flat ticks on one observed gap — the moc3 flap"

    def test_a_real_outage_does_not_become_this_peers_normal_cadence(self, tmp_path):
        """The desensitisation defect: ``rx_gap_max`` was a monotonic max.

        A genuine multi-hour deafness — the fault this probe exists for —
        ended, was recorded as a gap, and permanently taught the probe that
        multi-hour silence is normal here. A bounded window ages it out.
        """
        self._peer_every(tmp_path, period=5, cycles=6, silence=6)
        tx, rx = self._gaps(tmp_path)["last_tx"], self._gaps(tmp_path)["last_rx"]

        for i in range(1, 201):         # the outage: 200 ticks deaf, then heard
            tick(tmp_path, tx_b=tx + 500 * i, rx_b=rx, silence=6)
        tx += 500 * 201
        rx += 100
        tick(tmp_path, tx_b=tx, rx_b=rx, silence=6)
        assert self._gaps(tmp_path)["rx_gap_max"] >= 200, \
            "the outage should be recorded as an observation at all"

        # The peer returns to its 5-tick cadence. Once the outlier has aged out
        # of the window the estimate must follow the peer, not the outage.
        # The cycle count is FIXED, not GAP_WINDOW-derived, so that restoring
        # the old unbounded learner makes this test fail in bounded time — a
        # test whose cost scales with the thing it pins cannot be drilled.
        assert rf.GAP_WINDOW <= 12, "widen this loop if the window grows"
        self._peer_every(tmp_path, period=5, cycles=14, silence=6)
        assert self._gaps(tmp_path)["rx_gap_max"] < 200, \
            "a single outage still dominates the cadence: probe desensitised"
        assert len(self._gaps(tmp_path)["recent_gaps"]) <= rf.GAP_WINDOW

    def test_upgrading_a_box_does_not_trust_the_old_single_estimate(self, tmp_path):
        """Old state carries ``rx_gap_max`` and no window. Seeding it as ONE
        sample keeps the estimate but re-earns the right to use it."""
        sp = tmp_path / "rf_leg_state.json"
        sp.write_text(json.dumps({f"RNodeInterface[{NAME}]": {
            "ever_rx": True, "last_tx": 1000, "last_rx": 500,
            "flat_streak": 0, "tx_at_flat_start": 1000,
            "rx_gap_max": 16, "gap_valid": True}}))
        for i in range(1, 40):
            assert tick(tmp_path, tx_b=1000 + 500 * i, rx_b=500,
                        silence=rf.MIN_SILENCE_TICKS,
                        uncalibrated_ticks=rf.UNCALIBRATED_SILENCE_TICKS) is None, \
                f"fired at {i} flat ticks on an inherited one-sample estimate"
        assert self._gaps(tmp_path)["recent_gaps"] == [16]


class TestRestartsAreNotDeafness:
    def test_counter_reset_does_not_fire(self, tmp_path):
        """rnsd restart zeroes both counters — the classic false page."""
        tick(tmp_path, tx_b=50000, rx_b=9000)
        # restart: counters drop
        assert tick(tmp_path, tx_b=100, rx_b=0) is None
        assert tick(tmp_path, tx_b=1200, rx_b=0) is None
        assert tick(tmp_path, tx_b=2300, rx_b=0) is None

    def test_a_restart_does_not_earn_a_page_within_the_old_debounce(self, tmp_path):
        """Finding 2, the restart leg. The old probe fired 90 s after a routine
        rnsd restart: the restart zeroed last_rx, "flat" was trivially true,
        and three TX ticks later it paged on a peer that was merely between
        announces. The state file used to pin that page as CORRECT."""
        tick(tmp_path, tx_b=100_000, rx_b=5_000, silence=rf.MIN_SILENCE_TICKS)
        tick(tmp_path, tx_b=500, rx_b=0, silence=rf.MIN_SILENCE_TICKS)    # restart
        for i in range(2, 6):
            assert tick(tmp_path, tx_b=500 * i, rx_b=0,
                        silence=rf.MIN_SILENCE_TICKS) is None, \
                f"paged {i - 1} ticks after a restart"

    def test_ever_rx_survives_a_restart_and_pages_once_silence_is_real(self, tmp_path):
        """The 'has a peer' fact persists across a restart — otherwise every
        restart re-arms the inert state — but the SILENCE must still be earned
        against the floor, measured from the restart."""
        tick(tmp_path, tx_b=100_000, rx_b=5_000, silence=rf.MIN_SILENCE_TICKS)
        tick(tmp_path, tx_b=500, rx_b=0, silence=rf.MIN_SILENCE_TICKS)    # restart
        sig = None
        for i in range(2, rf.MIN_SILENCE_TICKS + 3):
            sig = tick(tmp_path, tx_b=500 * i, rx_b=0, silence=rf.MIN_SILENCE_TICKS)
            if sig is not None:
                break
        assert sig is not None, "ever_rx was lost across the restart"
        assert i - 1 >= rf.MIN_SILENCE_TICKS, f"paged after only {i - 1} flat ticks"

    def test_a_gap_measured_from_a_restart_does_not_calibrate_the_cadence(self, tmp_path):
        """After a restart the first RX gap is measured from the restart, not
        from the last real RX — it undercounts, which would LOWER the bar."""
        tick(tmp_path, tx_b=1000, rx_b=500)
        tick(tmp_path, tx_b=100, rx_b=0)       # restart
        tick(tmp_path, tx_b=200, rx_b=50)      # heard 1 tick after: gap NOT recorded
        saved = json.loads((tmp_path / "rf_leg_state.json").read_text())
        assert next(iter(saved.values()))["rx_gap_max"] == 0


class TestRawCountersNotDisplayText:
    """Finding 1 (2026-09-09): rnstatus text renders '%.2f MB' — a 10,000 B
    quantum above 1 MB. The fleet's only RNode is at 1.15 MB, so a tick of RF
    traffic is invisible in the text and the probe was structurally blind."""

    def _tick(self, tmp_path, *, text_tx, text_rx, raw_tx, raw_rx, silence=3):
        return rf.probe_rf_leg_silent(
            status=FakeStatus([text_iface(tx=FakeCounter(text_tx, "MB"),
                                          rx=FakeCounter(text_rx, "MB"))]),
            raw_stats=[raw(raw_tx, raw_rx)],
            state_path=str(tmp_path / "s.json"), min_silence_ticks=silence,
            uncalibrated_ticks=silence)

    def test_deaf_leg_invisible_in_the_text_still_fires_from_raw_counters(self, tmp_path):
        sig = None
        for i in range(0, 5):
            # text never moves: 1.15 MB up, 0.10 MB down, every tick
            sig = self._tick(tmp_path, text_tx=1.15, text_rx=0.10,
                             raw_tx=1_150_000 + 1_000 * i, raw_rx=100_000)
        assert sig is not None, "judged the display text, not the counters"

    def test_hearing_leg_does_not_page_when_only_the_text_steps(self, tmp_path):
        """Inverse: RX +150 B/tick (invisible at MB display) while the TX
        display happens to step 10 kB on three ticks — the old probe paged
        'RX pinned' on a receiving radio."""
        for i in range(0, 5):
            sig = self._tick(tmp_path, text_tx=1.15 + 0.01 * i, text_rx=0.10,
                             raw_tx=1_150_000 + 10_000 * i, raw_rx=100_000 + 150 * i)
            assert sig is None, "paged on a leg that is receiving"

    def test_unreadable_raw_counters_are_indeterminate_not_inert_or_clean(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rf, "_read_raw_interface_stats",
                            lambda timeout_s: (None, "rnstatus -j timed out"))
        assert rf.probe_rf_leg_silent(
            status=FakeStatus([text_iface()]), state_path=str(tmp_path / "s.json")) is None
        d = disposition()
        assert d["disp"] == "indeterminate"
        assert "quantised" in d["reason"]
        assert d["coverage"] == {"judged": 0, "enrolled": 1}

    def test_raw_stats_without_an_rnode_entry_is_indeterminate(self, tmp_path):
        rf.probe_rf_leg_silent(
            status=FakeStatus([text_iface()]),
            raw_stats=[{"name": "TCPInterface[x]", "type": "TCPInterface",
                        "status": True, "rxb": 1, "txb": 1}],
            state_path=str(tmp_path / "s.json"))
        assert disposition()["disp"] == "indeterminate"

    def test_raw_reader_uses_a_bounded_subprocess(self, monkeypatch):
        """A wedged rnsd must not hang the watchdog thread (#68/#72)."""
        import subprocess as sp
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            raise sp.TimeoutExpired(argv, kw.get("timeout"))

        monkeypatch.setattr(rf, "_find_rnstatus_binary", lambda: "/usr/bin/rnstatus")
        monkeypatch.setattr(rf.subprocess, "run", fake_run)
        got, why = rf._read_raw_interface_stats(timeout_s=4.0)
        assert got is None and "timed out" in why
        assert seen["argv"][-1] == "-j"
        assert seen["kw"]["timeout"] == 4.0

    def test_raw_reader_parses_the_real_json_shape(self, monkeypatch):
        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"interfaces": [raw(248405, 258454, name="Lab RNode RF")],
                                 "rxb": 1, "txb": 1})
        monkeypatch.setattr(rf, "_find_rnstatus_binary", lambda: "/usr/bin/rnstatus")
        monkeypatch.setattr(rf.subprocess, "run", lambda *a, **k: Proc())
        got, why = rf._read_raw_interface_stats()
        assert why == "ok" and got[0]["txb"] == 248405


class TestUnobservableRnstatus:
    """Finding 3 (2026-09-09): the runner ALWAYS passes an RNSStatus object, so
    the ``status is None`` guard never fired; a timed-out rnstatus arrived with
    interfaces=[] and read as 'no RNode configured here' — inert. On the RNode
    box a #72 wedge rendered as absent-by-design for its whole duration."""

    @pytest.mark.parametrize("status", [
        RNSStatus(parse_error="rnstatus timed out (rnsd unresponsive)", timed_out=True),
        RNSStatus(parse_error="rnstatus binary not found. Install RNS: pip install rns"),
        RNSStatus(parse_error="rnstatus produced no output (exit 1)"),
    ], ids=["timed_out", "binary_missing", "empty_output"])
    def test_errored_status_is_indeterminate_not_inert(self, tmp_path, status):
        assert rf.probe_rf_leg_silent(status=status, raw_stats=[],
                                      state_path=str(tmp_path / "s.json")) is None
        d = disposition()
        assert d["disp"] == "indeterminate", d
        assert "unobservable" in d["reason"]


class TestStateAndObservability:
    def test_unwritable_state_does_not_raise(self, tmp_path):
        """#60 sandbox class: a saver that cannot write must not crash the tick."""
        bad = tmp_path / "nope" / "deep"
        bad.mkdir(parents=True)
        bad.chmod(0o500)
        try:
            rf.probe_rf_leg_silent(status=FakeStatus([text_iface()]),
                                   raw_stats=[raw(1000, 500)],
                                   state_path=str(bad / "s.json"))
        finally:
            bad.chmod(0o700)

    def test_unwritable_state_still_lets_the_probe_fire(self, tmp_path):
        """Finding 8 (2026-09-09): with the state dir unwritable, every tick
        reloaded the stale (absent) file, the streak restarted from 0 and the
        probe could NEVER fire — the frozen-green shape of the 2026-09-02
        audit — while the write-failure witness promised the value was 'held
        in-process'. It was not. Now it is."""
        bad = tmp_path / "ro"
        bad.mkdir()
        bad.chmod(0o500)
        sp = str(bad / "s.json")
        try:
            sig = None
            for i in range(0, 5):
                sig = rf.probe_rf_leg_silent(
                    status=FakeStatus([text_iface()]),
                    raw_stats=[raw(1000 + 500 * i, 500)],
                    state_path=sp, min_silence_ticks=3, uncalibrated_ticks=3)
            assert sig is not None, "streak reset to 0 every tick: state not held in-process"
            assert not Path(sp).exists(), "the disk really was unwritable"
        finally:
            bad.chmod(0o700)

    def test_two_legs_are_tracked_independently(self, tmp_path):
        sp = str(tmp_path / "s.json")
        rf.probe_rf_leg_silent(
            status=FakeStatus([text_iface(name="A"), text_iface(name="B")]),
            raw_stats=[raw(1000, 500, name="A"), raw(1000, 500, name="B")],
            state_path=sp)
        assert len(json.loads(Path(sp).read_text())) == 2

    def test_partial_coverage_is_recorded_in_structure(self, tmp_path):
        """Two legs enrolled, one has no integer counters: judged < enrolled."""
        rf.probe_rf_leg_silent(
            status=FakeStatus([text_iface(name="A"), text_iface(name="B")]),
            raw_stats=[raw(1000, 500, name="A"),
                       {"name": "RNodeInterface[B]", "type": "RNodeInterface",
                        "status": True, "rxb": None, "txb": "1.15 MB"}],
            state_path=str(tmp_path / "s.json"))
        d = disposition()
        assert d["disp"] == "clean"
        assert d["coverage"] == {"judged": 1, "enrolled": 2}
