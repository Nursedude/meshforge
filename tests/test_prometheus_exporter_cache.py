"""Tests for prometheus_exporter's node-geojson cache + per-node label cap.

2026-07-09 review of the 2026-07-06 deferred-perf commits (which shipped with
no tests): (1) the single-flight cache never recorded a FAILURE timestamp, so
while collect() failed every scrape re-ran the full collect serially under the
lock — worse than pre-lock behavior with a wedged rnsd; (2) the per-node label
cap silently dropped sensor series with no witness (absence reads as
sensor-offline, honest_failure_modes #9).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import prometheus_exporter as pe  # noqa: E402


def _reset_cache():
    pe._node_geojson_cache = {}
    pe._node_geojson_cache_time = 0.0


class TestNodeGeojsonCacheFailurePath(unittest.TestCase):
    def setUp(self):
        _reset_cache()
        self.addCleanup(_reset_cache)

    def test_failing_collect_is_rate_limited_to_ttl(self):
        """A raising collect() must not re-run on every scrape inside the TTL."""
        collector = MagicMock()
        collector.collect.side_effect = RuntimeError("rnsd wedged")
        with patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_get_shared_collector", return_value=collector):
            self.assertEqual(pe._collect_node_geojson(), {})
            self.assertEqual(pe._collect_node_geojson(), {})
            self.assertEqual(pe._collect_node_geojson(), {})
        # One attempt, not three — the failure timestamp holds for the TTL.
        self.assertEqual(collector.collect.call_count, 1)

    def test_empty_result_is_rate_limited_to_ttl(self):
        collector = MagicMock()
        collector.collect.return_value = {}
        with patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_get_shared_collector", return_value=collector):
            pe._collect_node_geojson()
            pe._collect_node_geojson()
        self.assertEqual(collector.collect.call_count, 1)

    def test_success_is_cached_and_returned(self):
        geo = {"type": "FeatureCollection", "features": [1]}
        collector = MagicMock()
        collector.collect.return_value = geo
        with patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_get_shared_collector", return_value=collector):
            self.assertEqual(pe._collect_node_geojson(), geo)
            self.assertEqual(pe._collect_node_geojson(), geo)
        self.assertEqual(collector.collect.call_count, 1)

    def test_expired_ttl_recollects(self):
        collector = MagicMock()
        collector.collect.return_value = {"f": 1}
        with patch.object(pe, "_HAS_MAP_COLLECTOR", True), \
             patch.object(pe, "_get_shared_collector", return_value=collector):
            pe._collect_node_geojson()
            pe._node_geojson_cache_time -= pe._NODE_CACHE_TTL + 1
            pe._collect_node_geojson()
        self.assertEqual(collector.collect.call_count, 2)


class _Node:
    def __init__(self, node_id, last_seen):
        self.node_id = node_id
        self.last_seen = last_seen


class TestCapRecent(unittest.TestCase):
    def setUp(self):
        pe._capped_families_logged.discard("testfam")

    def test_keeps_newest_n(self):
        nodes = [_Node(f"!{i:08x}", i) for i in range(pe._PER_NODE_LABEL_CAP + 50)]
        capped = pe._cap_recent(nodes, "testfam")
        self.assertEqual(len(capped), pe._PER_NODE_LABEL_CAP)
        # Newest-first: the highest last_seen survives, the oldest are dropped.
        self.assertEqual(capped[0].last_seen, pe._PER_NODE_LABEL_CAP + 49)

    def test_cap_engagement_leaves_a_witness(self):
        nodes = [_Node(f"!{i:08x}", i) for i in range(pe._PER_NODE_LABEL_CAP + 1)]
        with self.assertLogs("utils.prometheus_exporter", level="INFO") as logs:
            pe._cap_recent(nodes, "testfam")
        self.assertTrue(any("label cap" in m for m in logs.output))

    def test_under_cap_no_witness_and_no_drop(self):
        nodes = [_Node("!aa", 5), _Node("!bb", 9)]
        capped = pe._cap_recent(nodes, "testfam")
        self.assertEqual(len(capped), 2)
        self.assertNotIn("testfam", pe._capped_families_logged)

    def test_none_input_is_empty(self):
        self.assertEqual(pe._cap_recent(None, "testfam"), [])


if __name__ == "__main__":
    unittest.main()
