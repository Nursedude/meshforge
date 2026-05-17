"""Tests for the wedge_events ring buffer + pub/sub.

The module is small but load-bearing — it's the in-process channel
that bonds Fork A (gateway watchdog) to Fork B (cascade recovery
actor). These tests pin the contract both consumers depend on.
"""
from __future__ import annotations

import threading

import pytest

from utils import wedge_events
from utils.wedge_events import RING_BUFFER_CAP, WedgeEvent


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Module-level ring buffer + subscriber list survive across tests.
    Reset before AND after so neither side leaks."""
    wedge_events._reset_for_tests()
    yield
    wedge_events._reset_for_tests()


class TestRingBuffer:
    def test_publish_then_recent_round_trips(self):
        evt = WedgeEvent(
            ts=1700000000.0,
            source="gateway.rns",
            label="rnsd.has_path",
            target="abcd1234",
            timeout_s=3.0,
            note="probe",
        )
        wedge_events.publish(evt)
        got = wedge_events.recent()
        assert got == [evt]

    def test_recent_returns_newest_last(self):
        for i in range(5):
            wedge_events.publish(WedgeEvent(
                ts=float(i), source="t", label=f"l{i}",
                target=None, timeout_s=1.0,
            ))
        labels = [e.label for e in wedge_events.recent()]
        assert labels == ["l0", "l1", "l2", "l3", "l4"]

    def test_recent_limit_returns_last_n(self):
        for i in range(10):
            wedge_events.publish(WedgeEvent(
                ts=float(i), source="t", label=f"l{i}",
                target=None, timeout_s=1.0,
            ))
        last3 = wedge_events.recent(limit=3)
        assert [e.label for e in last3] == ["l7", "l8", "l9"]

    def test_recent_limit_zero_returns_empty(self):
        wedge_events.publish(WedgeEvent(
            ts=0.0, source="t", label="l", target=None, timeout_s=1.0,
        ))
        assert wedge_events.recent(limit=0) == []

    def test_recent_limit_larger_than_buffer_returns_all(self):
        for i in range(3):
            wedge_events.publish(WedgeEvent(
                ts=float(i), source="t", label=f"l{i}",
                target=None, timeout_s=1.0,
            ))
        assert len(wedge_events.recent(limit=999)) == 3

    def test_recent_returns_copy(self):
        """Caller mutations to the returned list must not corrupt internal state."""
        wedge_events.publish(WedgeEvent(
            ts=0.0, source="t", label="l", target=None, timeout_s=1.0,
        ))
        out = wedge_events.recent()
        out.append("not-a-WedgeEvent")  # type: ignore[arg-type]
        # Internal state should still have exactly the one event
        assert len(wedge_events.recent()) == 1

    def test_ring_buffer_caps_at_max(self):
        """Beyond RING_BUFFER_CAP, oldest entries are evicted."""
        for i in range(RING_BUFFER_CAP + 50):
            wedge_events.publish(WedgeEvent(
                ts=float(i), source="t", label=f"l{i}",
                target=None, timeout_s=1.0,
            ))
        all_evts = wedge_events.recent()
        assert len(all_evts) == RING_BUFFER_CAP
        # The first 50 should have been evicted; the buffer now holds
        # indices [50, 50+CAP)
        assert all_evts[0].label == "l50"
        assert all_evts[-1].label == f"l{RING_BUFFER_CAP + 49}"


class TestSubscribers:
    def test_subscribe_receives_subsequent_events(self):
        got = []
        wedge_events.subscribe(got.append)
        wedge_events.publish(WedgeEvent(
            ts=1.0, source="t", label="a", target=None, timeout_s=1.0,
        ))
        wedge_events.publish(WedgeEvent(
            ts=2.0, source="t", label="b", target=None, timeout_s=1.0,
        ))
        assert [e.label for e in got] == ["a", "b"]

    def test_subscriber_does_not_see_events_published_before_subscribe(self):
        """Subscription is forward-only; ring buffer is the back-fill API."""
        wedge_events.publish(WedgeEvent(
            ts=0.0, source="t", label="before", target=None, timeout_s=1.0,
        ))
        got = []
        wedge_events.subscribe(got.append)
        wedge_events.publish(WedgeEvent(
            ts=1.0, source="t", label="after", target=None, timeout_s=1.0,
        ))
        assert [e.label for e in got] == ["after"]

    def test_unsubscribe_stops_delivery(self):
        got = []
        wedge_events.subscribe(got.append)
        wedge_events.publish(WedgeEvent(
            ts=0.0, source="t", label="a", target=None, timeout_s=1.0,
        ))
        wedge_events.unsubscribe(got.append)
        wedge_events.publish(WedgeEvent(
            ts=1.0, source="t", label="b", target=None, timeout_s=1.0,
        ))
        assert [e.label for e in got] == ["a"]

    def test_unsubscribe_unknown_callback_is_noop(self):
        """Idempotent — defensive for shutdown paths that may
        double-unregister."""
        wedge_events.unsubscribe(lambda e: None)  # no-op, no raise

    def test_failing_subscriber_does_not_block_other_subscribers(self):
        """One subscriber raising must not stop dispatch to the others —
        Fork B's actor must keep getting events even if a debug
        subscriber misbehaves."""
        good_got = []

        def bad(event):
            raise RuntimeError("simulated subscriber bug")

        wedge_events.subscribe(bad)
        wedge_events.subscribe(good_got.append)
        wedge_events.publish(WedgeEvent(
            ts=0.0, source="t", label="a", target=None, timeout_s=1.0,
        ))
        assert len(good_got) == 1

    def test_concurrent_publishers_dont_lose_events(self):
        """Smoke test for the threading.Lock — many publishers across
        threads should all land in the buffer."""
        N = 200  # well under RING_BUFFER_CAP so no evictions
        threads = [
            threading.Thread(
                target=lambda i=i: wedge_events.publish(WedgeEvent(
                    ts=float(i), source="t", label=f"l{i}",
                    target=None, timeout_s=1.0,
                )),
            )
            for i in range(N)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(wedge_events.recent()) == N


class TestWedgeEventShape:
    """The dataclass is part of the cross-fork contract — pin its shape."""

    def test_to_dict_round_trips_all_fields(self):
        evt = WedgeEvent(
            ts=1.5, source="gateway.rns", label="rnsd.handle_outbound",
            target="abcd", timeout_s=15.0, note="link timeout",
        )
        d = evt.to_dict()
        assert d == {
            "ts": 1.5, "source": "gateway.rns",
            "label": "rnsd.handle_outbound", "target": "abcd",
            "timeout_s": 15.0, "note": "link timeout",
        }

    def test_note_defaults_to_empty_string(self):
        evt = WedgeEvent(
            ts=0.0, source="t", label="l", target=None, timeout_s=1.0,
        )
        assert evt.note == ""

    def test_target_can_be_none(self):
        """Some calls don't have a per-destination target (e.g. setup
        calls). None must round-trip cleanly."""
        evt = WedgeEvent(
            ts=0.0, source="t", label="l", target=None, timeout_s=1.0,
        )
        assert evt.target is None
        assert evt.to_dict()["target"] is None

    def test_dataclass_is_frozen(self):
        """Frozen so consumers can rely on the events not mutating
        after publication (the ring stores references)."""
        evt = WedgeEvent(
            ts=0.0, source="t", label="l", target=None, timeout_s=1.0,
        )
        with pytest.raises((AttributeError, Exception)):
            evt.ts = 99.0  # type: ignore[misc]
