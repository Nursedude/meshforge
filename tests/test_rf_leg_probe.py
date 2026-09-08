"""Tests for probe_rf_leg_silent — does the RF leg actually carry traffic?

The probe exists because on 2026-09-08 two RNodes went deaf and every surface
still read healthy: interface Up, service active, box reachable, traffic
flowing — over TCP. So these tests are weighted heavily toward the cases that
must NOT fire, because a probe that cries wolf on a single-node RF network
would be wrong every day and get ignored by the time it mattered.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import watchdog_probes_rf_leg as rf  # noqa: E402


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


def iface(tx_b=0.0, rx_b=0.0, up=True, name="Regional RNode RF",
          type_name="RNodeInterface"):
    return FakeIface(
        type_name=type_name,
        display_name=name,
        status=FakeStatusEnum("UP" if up else "DOWN"),
        tx=FakeCounter(tx_b), rx=FakeCounter(rx_b),
    )


def tick(tmp_path, tx_b, rx_b, **kw):
    """One probe observation against a persistent state file."""
    return rf.probe_rf_leg_silent(
        status=FakeStatus([iface(tx_b, rx_b, **kw)]),
        state_path=str(tmp_path / "rf_leg_state.json"),
    )


class TestInertCases:
    """Silence that is correct. Getting these wrong makes the probe noise."""

    def test_no_rnode_interface_is_inert(self, tmp_path):
        """Most of the fleet has no RF leg at all — absent by design."""
        st = FakeStatus([iface(1e6, 1e6, type_name="TCPInterface")])
        assert rf.probe_rf_leg_silent(
            status=st, state_path=str(tmp_path / "s.json")) is None

    def test_never_received_never_fires_however_much_we_transmit(self, tmp_path):
        """THE false-positive that would have made this probe useless.

        The fleet's only RNode sat at 1.15 MB up / 0 B down for months because
        nothing else was on the air. That is correct, not a fault.
        """
        for i in range(1, 12):
            assert tick(tmp_path, tx_b=1000.0 * i, rx_b=0.0) is None

    def test_down_interface_is_left_to_other_probes(self, tmp_path):
        for i in range(1, 8):
            assert tick(tmp_path, tx_b=1000.0 * i, rx_b=500.0, up=False) is None

    def test_healthy_two_way_traffic_is_clean(self, tmp_path):
        for i in range(1, 8):
            assert tick(tmp_path, tx_b=1000.0 * i, rx_b=800.0 * i) is None


class TestTheRealFault:
    def test_fires_when_a_leg_that_used_to_hear_goes_deaf(self, tmp_path):
        # establish that a peer exists
        assert tick(tmp_path, tx_b=1000.0, rx_b=500.0) is None
        # now RX pins while TX keeps climbing
        assert tick(tmp_path, tx_b=2000.0, rx_b=500.0) is None   # streak 1
        assert tick(tmp_path, tx_b=3000.0, rx_b=500.0) is None   # streak 2
        sig = tick(tmp_path, tx_b=4000.0, rx_b=500.0)            # streak 3
        assert sig is not None
        assert sig.cls == "rf_leg_silent"
        assert sig.severity == "degraded"

    def test_debounce_means_one_tick_is_not_enough(self, tmp_path):
        assert tick(tmp_path, tx_b=1000.0, rx_b=500.0) is None
        assert tick(tmp_path, tx_b=9000.0, rx_b=500.0) is None

    def test_the_detail_names_the_known_cause_and_the_masking(self, tmp_path):
        tick(tmp_path, tx_b=1000.0, rx_b=500.0)
        for i in range(2, 6):
            sig = tick(tmp_path, tx_b=1000.0 * i, rx_b=500.0)
        assert "promiscuous" in sig.detail
        assert "IP backhaul" in sig.detail, "must warn that nothing else reports this"

    def test_recovery_clears_the_streak(self, tmp_path):
        tick(tmp_path, tx_b=1000.0, rx_b=500.0)
        tick(tmp_path, tx_b=2000.0, rx_b=500.0)
        tick(tmp_path, tx_b=3000.0, rx_b=500.0)
        # a single received byte resets it
        assert tick(tmp_path, tx_b=4000.0, rx_b=900.0) is None
        assert tick(tmp_path, tx_b=5000.0, rx_b=900.0) is None


class TestRestartsAreNotDeafness:
    def test_counter_reset_does_not_fire(self, tmp_path):
        """rnsd restart zeroes both counters — the classic false page."""
        tick(tmp_path, tx_b=50000.0, rx_b=9000.0)
        # restart: counters drop
        assert tick(tmp_path, tx_b=100.0, rx_b=0.0) is None
        assert tick(tmp_path, tx_b=1200.0, rx_b=0.0) is None
        assert tick(tmp_path, tx_b=2300.0, rx_b=0.0) is None

    def test_ever_rx_survives_a_restart(self, tmp_path):
        """The 'has a peer' fact must persist, or every restart re-arms silence."""
        tick(tmp_path, tx_b=50000.0, rx_b=9000.0)
        tick(tmp_path, tx_b=100.0, rx_b=0.0)          # restart, streak reset
        # climb again with RX still pinned -> should now fire after debounce
        tick(tmp_path, tx_b=1200.0, rx_b=0.0)
        tick(tmp_path, tx_b=2300.0, rx_b=0.0)
        sig = tick(tmp_path, tx_b=3400.0, rx_b=0.0)
        assert sig is not None, "ever_rx was lost across the restart"


class TestUnitsAndObservability:
    def test_mb_and_kb_units_are_normalised(self, tmp_path):
        st = FakeStatus([FakeIface(
            tx=FakeCounter(1.15, "MB"), rx=FakeCounter(2.71, "KB"))])
        rf.probe_rf_leg_silent(status=st, state_path=str(tmp_path / "s.json"))
        import json
        saved = json.loads((tmp_path / "s.json").read_text())
        entry = next(iter(saved.values()))
        assert entry["last_tx"] == pytest.approx(1.15e6)
        assert entry["last_rx"] == pytest.approx(2710.0)

    def test_unwritable_state_does_not_raise(self, tmp_path):
        """#60 sandbox class: a saver that cannot write must not crash the tick."""
        bad = tmp_path / "nope" / "deep"
        bad.mkdir(parents=True)
        bad.chmod(0o500)
        try:
            rf.probe_rf_leg_silent(status=FakeStatus([iface(1000.0, 500.0)]),
                                   state_path=str(bad / "s.json"))
        finally:
            bad.chmod(0o700)

    def test_two_legs_are_tracked_independently(self, tmp_path):
        sp = str(tmp_path / "s.json")
        both = FakeStatus([iface(1000.0, 500.0, name="A"),
                           iface(1000.0, 500.0, name="B")])
        rf.probe_rf_leg_silent(status=both, state_path=sp)
        import json
        assert len(json.loads(Path(sp).read_text())) == 2
