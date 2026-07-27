"""Regression pins for the 2026-07-23 gateway honest-failure-modes review.

Four CONFIRMED defects, each an instance of the "degraded/omitted state maps to
a valid-looking value" class, with the fix pinned here so it cannot regress:

  [A] PersistentMessageQueue.get_stats()["failed"] was overridden with a
      point-in-time COUNT(status='failed') that is structurally always 0 (a
      failed message goes straight to DEAD_LETTER), masking the real cumulative
      counter that prometheus/influx export -> "0 failures forever".
  [C] UnifiedNode.from_meshtastic() hard-set last_seen=now()/is_online=True,
      ignoring the packet's real `lastHeard`, so sweeping the interface node DB
      marked every historically-known node "online / 0s ago".
  [D] BridgeHealthMonitor's per-service dicts drifted: _uptime_seconds omitted
      "meshcore", so a meshcore disconnect raised KeyError mid-lock and left the
      bridge's own view stuck "connected".
  [I] UnifiedNodeTracker._merge_node adopted a self-reported name but never
      updated name_is_self_reported, serving a name whose provenance flag lied.
"""
from datetime import datetime, timedelta

import pytest

from gateway.bridge_health import BridgeHealthMonitor
from gateway.message_queue import PersistentMessageQueue
from gateway.node_models import (
    MESHTASTIC_ONLINE_THRESHOLD_S,
    UnifiedNode,
)
from gateway.node_tracker import UnifiedNodeTracker


# ---------------------------------------------------------------------------
# [A] queue "failed" counter is the real cumulative count, not the always-0 query
# ---------------------------------------------------------------------------
class TestQueueFailedStatIsCumulative:
    def test_dead_letter_increments_get_stats_failed(self, tmp_path):
        q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
        # max_retries=0 → the first mark_failed dead-letters immediately.
        mid = q.enqueue({"text": "boom"}, "meshtastic", max_retries=0)
        assert mid is not None
        q.mark_failed(mid, "transport gone")

        stats = q.get_stats()
        # The bug: get_stats()["failed"] read COUNT(status='failed') == 0 forever
        # because mark_failed persists DEAD_LETTER, never 'failed'.
        assert stats["failed"] == 1, (
            "get_stats()['failed'] must reflect the cumulative failure counter, "
            "not a point-in-time COUNT(status='failed') that is always 0"
        )
        assert stats["dead_letter"] == 1

    def test_failed_stays_zero_when_nothing_failed(self, tmp_path):
        q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
        q.enqueue({"text": "ok"}, "meshtastic")
        assert q.get_stats()["failed"] == 0


# ---------------------------------------------------------------------------
# [C] from_meshtastic honours lastHeard
# ---------------------------------------------------------------------------
class TestFromMeshtasticHonoursLastHeard:
    def _node(self, **extra):
        base = {"num": 0x12345678, "user": {"longName": "X", "shortName": "X"}}
        base.update(extra)
        return UnifiedNode.from_meshtastic(base)

    def test_old_lastheard_is_offline(self):
        two_hours_ago = datetime.now().timestamp() - 2 * 3600
        node = self._node(lastHeard=two_hours_ago)
        assert node.is_online is False
        # last_seen reflects the real heard-time, not now()
        assert (datetime.now() - node.last_seen) > timedelta(hours=1)

    def test_recent_lastheard_is_online(self):
        node = self._node(lastHeard=datetime.now().timestamp() - 10)
        assert node.is_online is True

    def test_missing_lastheard_is_not_heard_now(self):
        # 2026-07-26 review C4 overturned the earlier "heard now" reading:
        # the interface node DB carries entries with absent/0 lastHeard that
        # were never heard this boot — no heard-time means cannot attribute,
        # not "online / 0s ago". (A live RX still goes online via the
        # tracker merge's update_seen.) Pinned in
        # tests/test_gateway_claw_fixes_2026_07_26.py as well.
        node = self._node()
        assert node.is_online is False
        assert node.last_seen is None

    def test_future_lastheard_does_not_forge_online_forever(self):
        node = self._node(lastHeard=datetime.now().timestamp() + 99999)
        # Skew guard: a future stamp is untrusted → "heard now", not a negative age.
        assert node.is_online is True
        assert (datetime.now() - node.last_seen) < timedelta(seconds=5)

    def test_threshold_pinned_to_tracker(self):
        # Two consumers of one threshold (honest_failure_modes #5) — node_models
        # cannot import node_tracker (circular), so the equality is pinned here.
        assert MESHTASTIC_ONLINE_THRESHOLD_S == UnifiedNodeTracker.OFFLINE_THRESHOLD


# ---------------------------------------------------------------------------
# [D] bridge health per-service dicts share one key set; meshcore can't KeyError
# ---------------------------------------------------------------------------
class TestBridgeHealthServiceDicts:
    def test_connection_trio_dicts_have_identical_keyset(self):
        # The three dicts record_connection_event indexes must agree — that is
        # the drift that caused the KeyError. (_subsystem_states is a separate,
        # guarded surface and intentionally not in this set.)
        h = BridgeHealthMonitor()
        base = set(h._connected)
        assert set(h._connection_count) == base
        assert set(h._uptime_seconds) == base
        assert "meshcore" in base

    def test_meshcore_connect_then_disconnect_does_not_raise(self):
        h = BridgeHealthMonitor()
        h.record_connection_event("meshcore", "connected")
        # This used to raise KeyError on _uptime_seconds["meshcore"] mid-lock.
        h.record_connection_event("meshcore", "disconnected")
        assert h._connected["meshcore"] is False
        assert h._uptime_seconds["meshcore"] >= 0.0

    def test_unknown_service_self_registers_without_crashing(self):
        h = BridgeHealthMonitor()
        h.record_connection_event("newradio", "connected")
        h.record_connection_event("newradio", "disconnected")
        assert "newradio" in h._connected
        assert h._connected["newradio"] is False


# ---------------------------------------------------------------------------
# [I] a self-reported name that wins carries its provenance flag
# ---------------------------------------------------------------------------
class TestMergeNameProvenance:
    def test_self_reported_name_sets_flag_true(self):
        t = UnifiedNodeTracker()
        existing = UnifiedNode(id="meshtastic:abc", network="meshtastic", name="!abc",
                               meshtastic_id="!abc", name_is_self_reported=False)
        t.add_node(existing)
        incoming = UnifiedNode(id="meshtastic:abc", network="meshtastic", name="Alice",
                               meshtastic_id="!abc", name_is_self_reported=True)
        t.add_node(incoming)  # merges into `existing` in place
        assert existing.name == "Alice"
        assert existing.name_is_self_reported is True

    def test_placeholder_fill_keeps_flag_false(self):
        t = UnifiedNodeTracker()
        existing = UnifiedNode(id="meshtastic:xyz", network="meshtastic", name="",
                               meshtastic_id="!xyz", name_is_self_reported=False)
        t.add_node(existing)
        incoming = UnifiedNode(id="meshtastic:xyz", network="meshtastic", name="!xyz",
                               meshtastic_id="!xyz", name_is_self_reported=False)
        t.add_node(incoming)
        assert existing.name == "!xyz"
        # A hash-derived placeholder is NOT the node self-reporting.
        assert existing.name_is_self_reported is False
