"""Tests for map_data_collector diagnostics, MeshCore position enrichment, and AREDN fallback.

Covers Issue #42 work (exposing MeshCore + AREDN on :5000 maps):
- Per-source diagnostic shape in collect() output
- MeshCore position enrichment from operator-assigned coordinates
- AREDN reason-if-zero taxonomy (not_configured vs unreachable)
- nodes_without_position surfacing across sources (not overwritten)
"""

from unittest.mock import MagicMock, patch

import pytest

from utils.map_data_collector import MapDataCollector


@pytest.fixture
def collector(tmp_path):
    """Fresh collector with an isolated cache dir per test."""
    return MapDataCollector(cache_dir=tmp_path, enable_history=False)


class TestRecordDiagnostic:
    def test_ok_when_yielded(self, collector):
        collector._record_diagnostic("mqtt", attempted=5, yielded=3)
        d = collector.get_source_diagnostics()
        assert d["mqtt"]["reason_if_zero"] == "ok"
        assert d["mqtt"]["yielded"] == 3
        assert d["mqtt"]["attempted"] == 5

    def test_no_positions_when_attempted_but_zero_yielded(self, collector):
        collector._record_diagnostic("aredn", attempted=4, yielded=0)
        d = collector.get_source_diagnostics()
        assert d["aredn"]["reason_if_zero"] == "no_positions"

    def test_unreachable_when_nothing_attempted(self, collector):
        collector._record_diagnostic("meshtasticd", attempted=0, yielded=0)
        d = collector.get_source_diagnostics()
        assert d["meshtasticd"]["reason_if_zero"] == "unreachable"

    def test_explicit_reason_wins(self, collector):
        collector._record_diagnostic(
            "aredn", attempted=0, yielded=0, reason_if_zero="not_configured"
        )
        d = collector.get_source_diagnostics()
        assert d["aredn"]["reason_if_zero"] == "not_configured"

    def test_notes_preserved(self, collector):
        collector._record_diagnostic(
            "mqtt", attempted=0, yielded=0, notes="no broker"
        )
        d = collector.get_source_diagnostics()
        assert d["mqtt"]["notes"] == "no broker"


class TestInfoLogRateLimit:
    def test_first_call_logs(self, collector, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="utils.map_data_collector")
        collector._info_log_rate_limited("aredn", "hello")
        assert any("hello" in r.message for r in caplog.records)

    def test_second_call_within_cooldown_silent(self, collector, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="utils.map_data_collector")
        collector._info_log_rate_limited("aredn", "first", cooldown_s=300)
        caplog.clear()
        collector._info_log_rate_limited("aredn", "second", cooldown_s=300)
        assert not any("second" in r.message for r in caplog.records)

    def test_after_cooldown_logs_again(self, collector, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="utils.map_data_collector")
        collector._info_log_rate_limited("aredn", "first", cooldown_s=0.0)
        caplog.clear()
        collector._info_log_rate_limited("aredn", "second", cooldown_s=0.0)
        assert any("second" in r.message for r in caplog.records)


class TestOperatorPositions:
    def test_promotes_by_full_id(self, collector):
        collector._settings.set("meshcore_positions", {
            "meshcore:abc123": {"lat": 19.5, "lon": -155.3, "note": "test"},
        })
        collector._nodes_without_position = [
            {"id": "meshcore:abc123", "name": "HiloNode", "network": "meshcore",
             "is_online": True, "last_seen": "2m"},
        ]
        features = {}
        promoted = collector._apply_operator_positions(features)
        assert promoted == 1
        assert "meshcore:abc123" in features
        feat = features["meshcore:abc123"]
        assert feat["geometry"]["coordinates"] == [-155.3, 19.5]
        assert feat["properties"]["network"] == "meshcore"
        assert feat["properties"]["position_source"] == "operator"
        assert feat["properties"]["note"] == "test"
        # Removed from the no-position list once promoted
        assert collector._nodes_without_position == []

    def test_promotes_by_prefix(self, collector):
        collector._settings.set("meshcore_positions", {
            "abc": {"lat": 20.0, "lon": -156.0},
        })
        collector._nodes_without_position = [
            {"id": "meshcore:abc123def456", "name": "N", "network": "meshcore"},
        ]
        features = {}
        assert collector._apply_operator_positions(features) == 1
        assert "meshcore:abc123def456" in features

    def test_non_matching_node_stays_unpositioned(self, collector):
        collector._settings.set("meshcore_positions", {
            "deadbeef": {"lat": 19.5, "lon": -155.3},
        })
        collector._nodes_without_position = [
            {"id": "meshcore:abc123", "name": "N", "network": "meshcore"},
        ]
        features = {}
        assert collector._apply_operator_positions(features) == 0
        assert features == {}
        assert len(collector._nodes_without_position) == 1

    def test_missing_lat_lon_skipped(self, collector):
        collector._settings.set("meshcore_positions", {
            "abc123": {"note": "no coords"},
        })
        collector._nodes_without_position = [
            {"id": "meshcore:abc123", "name": "N", "network": "meshcore"},
        ]
        features = {}
        assert collector._apply_operator_positions(features) == 0

    def test_no_settings_is_noop(self, collector):
        collector._nodes_without_position = [
            {"id": "meshcore:abc123", "name": "N", "network": "meshcore"},
        ]
        features = {}
        assert collector._apply_operator_positions(features) == 0

    def test_duplicate_id_not_reappended(self, collector):
        """If the feature already exists (e.g. from unified_tracker), don't duplicate."""
        collector._settings.set("meshcore_positions", {
            "abc123": {"lat": 1.0, "lon": 2.0},
        })
        collector._nodes_without_position = [
            {"id": "meshcore:abc123", "name": "N", "network": "meshcore"},
        ]
        features = {"meshcore:abc123": {"type": "Feature", "properties": {"id": "meshcore:abc123"}}}
        promoted = collector._apply_operator_positions(features)
        assert promoted == 0
        # Still in the no-position list since we couldn't promote
        assert len(collector._nodes_without_position) == 1


class TestArednReasonIfZero:
    @patch.object(MapDataCollector, "_get_aredn_node_ip", return_value=None)
    def test_not_configured_when_no_ips_and_unreachable(self, _mock_ip, collector):
        # Defaults: aredn_node_ips=[]
        features = collector._collect_aredn()
        assert features == []
        d = collector.get_source_diagnostics()
        assert d["aredn"]["reason_if_zero"] == "not_configured"

    @patch.object(MapDataCollector, "_get_aredn_node_ip", return_value=None)
    def test_unreachable_when_ips_configured_but_none_reachable(self, _mock_ip, collector):
        collector._settings.set("aredn_node_ips", ["10.99.99.99"])
        features = collector._collect_aredn()
        assert features == []
        d = collector.get_source_diagnostics()
        assert d["aredn"]["reason_if_zero"] == "unreachable"


class TestCollectExposesDiagnostics:
    @staticmethod
    def _patched(coll):
        """Patch every source-collection method on an instance to return []."""
        sources = [
            "_collect_unified_tracker", "_collect_meshtasticd", "_collect_direct_radio",
            "_collect_mqtt", "_collect_node_tracker", "_collect_aredn",
            "_collect_rns_direct", "_collect_public_fallbacks",
        ]
        for name in sources:
            setattr(coll, name, lambda *_a, **_kw: [])

    def test_geojson_properties_include_source_diagnostics(self, tmp_path):
        c = MapDataCollector(cache_dir=tmp_path, enable_history=False)
        # Monkey-patch sources AFTER construction. Side effect: collect() resets
        # _source_diagnostics at the top, so we override one source to record.
        orig_reset = c._record_diagnostic
        def _no_reset(*a, **kw):
            return orig_reset(*a, **kw)
        c._collect_unified_tracker = lambda: (c._record_diagnostic("test_source", attempted=1, yielded=1) or [])
        for name in ["_collect_meshtasticd", "_collect_direct_radio", "_collect_mqtt",
                     "_collect_node_tracker", "_collect_aredn", "_collect_rns_direct",
                     "_collect_public_fallbacks"]:
            setattr(c, name, lambda *_a, **_kw: [])

        result = c.collect(max_age_seconds=0)
        assert "source_diagnostics" in result["properties"]
        assert "test_source" in result["properties"]["source_diagnostics"]

    def test_collect_resets_diagnostics_and_no_position_list(self, tmp_path):
        c = MapDataCollector(cache_dir=tmp_path, enable_history=False)
        self._patched(c)
        # Inject stale state first
        c._record_diagnostic("stale", attempted=1, yielded=1)
        c._nodes_without_position = [{"id": "stale-node", "network": "x"}]
        c.collect(max_age_seconds=0)
        # Stale entries must NOT leak from a previous call
        d = c.get_source_diagnostics()
        assert "stale" not in d
        assert all(e.get("id") != "stale-node" for e in c.get_nodes_without_position())


class TestMeshtasticDoesNotClobberNoPositionList:
    """Regression: _collect_via_http used to = (overwrite) the no-position list,
    wiping MeshCore entries captured earlier by _collect_unified_tracker."""

    @patch("utils.map_data_collector.get_http_client")
    def test_http_extend_not_overwrite(self, mock_http, collector):
        # Pre-populate as _collect_unified_tracker would
        collector._nodes_without_position = [
            {"id": "meshcore:abc", "name": "MC", "network": "meshcore"},
        ]

        # Simulate meshtasticd HTTP returning one node without GPS
        node = MagicMock()
        node.has_position = False
        node.node_id = "!dead"
        node.long_name = "MT"
        node.short_name = "MT"
        node.hw_model = "T"
        node.snr = 0
        node.last_heard = 0
        client = MagicMock()
        client.is_available = True
        client.get_nodes.return_value = [node]
        mock_http.return_value = client

        collector._collect_via_http("localhost")

        ids = [e["id"] for e in collector._nodes_without_position]
        # MeshCore entry must survive
        assert "meshcore:abc" in ids
        # Meshtastic entry is appended
        assert "!dead" in ids
