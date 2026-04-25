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


class TestExtractNodeInfoWithoutPosition:
    """Regression: _extract_node_info_without_position used to omit the
    'network' field, surfacing as 'unknown' in /api/status by_network summary."""

    def test_returns_meshtastic_network_tag(self, collector):
        info = collector._extract_node_info_without_position(
            node_id="!abc12345",
            data={
                "num": 0xabc12345,
                "user": {"longName": "Test", "shortName": "T", "hwModel": "HELTEC_V3"},
                "lastHeard": 1000000000,
                "snr": 5.5,
            },
            now=1000000300,
            online_threshold_seconds=900,
        )
        assert info is not None
        assert info["network"] == "meshtastic"
        assert info["id"] == "!abc12345"


class TestMeshCorePublicCollector:
    """The map.meshcore.dev public API is the only source of MeshCore nodes
    with GPS — local MeshCoreHandler advertisements carry no position."""

    def test_source_disabled_when_setting_off(self, collector):
        collector._settings.set("enable_meshcore_public", False)
        features = collector._collect_meshcore_public()
        assert features == []
        d = collector.get_source_diagnostics()
        assert d["meshcore_public"]["reason_if_zero"] == "source_disabled"

    @patch("urllib.request.urlopen")
    def test_parses_api_shape(self, mock_urlopen, collector):
        import json as _json
        collector._settings.set("enable_meshcore_public", True)
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = _json.dumps([
            {
                "public_key": "abc123def456",
                "adv_name": "HiloRepeater",
                "adv_lat": 19.42,
                "adv_lon": -155.28,
                "type": 2,  # repeater
                "last_advert": 1700000000,
                "params": {"freq": 915000000, "sf": 10, "cr": 5, "bw": 125000},
            },
            {
                # Missing pubkey — should be dropped
                "adv_name": "NoKey",
                "adv_lat": 10.0,
                "adv_lon": 10.0,
                "type": 1,
            },
            {
                # Null-island — should be dropped
                "public_key": "xyz",
                "adv_name": "NullIsland",
                "adv_lat": 0.0,
                "adv_lon": 0.0,
                "type": 1,
            },
        ]).encode()
        mock_urlopen.return_value = resp

        features = collector._collect_meshcore_public()
        assert len(features) == 1
        f = features[0]
        assert f["properties"]["network"] == "meshcore"
        assert f["properties"]["id"] == "meshcore:abc123def456"
        assert f["properties"]["node_type"] == "repeater"
        assert f["properties"]["is_gateway"] is True  # repeater → gateway
        assert f["geometry"]["coordinates"] == [-155.28, 19.42]
        assert f["properties"]["frequency"] == 915000000

        d = collector.get_source_diagnostics()
        assert d["meshcore_public"]["attempted"] == 3
        assert d["meshcore_public"]["yielded"] == 1
        assert d["meshcore_public"]["reason_if_zero"] == "ok"

    @patch("urllib.request.urlopen", side_effect=OSError("no network"))
    def test_network_error_records_unreachable(self, _mock_urlopen, collector):
        collector._settings.set("enable_meshcore_public", True)
        features = collector._collect_meshcore_public()
        assert features == []
        d = collector.get_source_diagnostics()
        assert d["meshcore_public"]["reason_if_zero"] == "unreachable"

    def test_invalid_coord_types_skipped(self, collector):
        result = collector._parse_meshcore_public_node({
            "public_key": "x", "adv_lat": "not-a-float", "adv_lon": -155.3, "type": 1,
        })
        assert result is None

    def test_out_of_range_coords_skipped(self, collector):
        result = collector._parse_meshcore_public_node({
            "public_key": "x", "adv_lat": 200.0, "adv_lon": -155.3, "type": 1,
        })
        assert result is None


class TestMeshCorePublicCache:
    """T1.1 — per-source cache wrapping the MeshCore public fetch.

    The 30 s outer collect() cache prevents within-burst refetches; this
    second tier absorbs the cache-miss case so a slow MeshCore fetch
    doesn't pay the 12 MB / 30 s timeout cost more than once per ttl.
    """

    @staticmethod
    def _stub_feature(pubkey: str = "abc"):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.1, 0.2]},
            "properties": {
                "id": f"meshcore:{pubkey}",
                "name": "stub",
                "network": "meshcore",
            },
        }

    def test_cache_within_ttl_returns_cached_without_refetch(self, collector):
        collector._settings.set("enable_meshcore_public", True)
        collector._settings.set("meshcore_public_cache_ttl_seconds", 600)
        feat = self._stub_feature()
        call_count = {"n": 0}

        def fake_fetch():
            call_count["n"] += 1
            return [feat], 1, True, "live"

        collector._fetch_meshcore_public_uncached = fake_fetch
        # First call populates cache.
        first = collector._collect_meshcore_public()
        assert first == [feat]
        assert call_count["n"] == 1
        # Second call (well within TTL) must NOT refetch.
        second = collector._collect_meshcore_public()
        assert second == [feat]
        assert call_count["n"] == 1, "cache-hit refetched"
        d = collector.get_source_diagnostics()
        assert d["meshcore_public"]["reason_if_zero"] == "ok"
        assert "cached" in (d["meshcore_public"]["notes"] or "")

    def test_cache_expired_refetches(self, collector):
        collector._settings.set("enable_meshcore_public", True)
        collector._settings.set("meshcore_public_cache_ttl_seconds", 600)
        feat = self._stub_feature()
        call_count = {"n": 0}

        def fake_fetch():
            call_count["n"] += 1
            return [feat], 1, True, "live"

        collector._fetch_meshcore_public_uncached = fake_fetch
        collector._collect_meshcore_public()
        # Force the cache stamp to be older than the TTL.
        collector._meshcore_public_cache_ts -= 700
        collector._collect_meshcore_public()
        assert call_count["n"] == 2, "expired cache failed to refetch"

    def test_fetch_error_serves_stale_cache_with_diagnostic(self, collector):
        collector._settings.set("enable_meshcore_public", True)
        collector._settings.set("meshcore_public_cache_ttl_seconds", 600)
        feat = self._stub_feature()
        # First, populate the cache via a successful fetch.
        collector._fetch_meshcore_public_uncached = lambda: ([feat], 1, True, "live")
        collector._collect_meshcore_public()
        # Now simulate the cache expiring AND the next fetch failing.
        collector._meshcore_public_cache_ts -= 700
        collector._fetch_meshcore_public_uncached = lambda: ([], 0, False, "no network")
        result = collector._collect_meshcore_public()
        assert result == [feat], "stale cache must be served on fetch error"
        d = collector.get_source_diagnostics()
        assert d["meshcore_public"]["reason_if_zero"] == "stale_cached"
        assert "no network" in (d["meshcore_public"]["notes"] or "")

    def test_fetch_error_with_no_cache_records_unreachable(self, collector):
        collector._settings.set("enable_meshcore_public", True)
        collector._fetch_meshcore_public_uncached = lambda: ([], 0, False, "no network")
        result = collector._collect_meshcore_public()
        assert result == []
        d = collector.get_source_diagnostics()
        assert d["meshcore_public"]["reason_if_zero"] == "unreachable"


class TestTimedCollectLatency:
    """T1.3 — every source's diagnostic carries latency_ms after collect()."""

    def test_timed_collect_records_latency(self, collector):
        def slow():
            import time
            time.sleep(0.01)
            collector._record_diagnostic("test_src", attempted=2, yielded=2)
            return []

        collector._timed_collect("test_src", slow)
        d = collector.get_source_diagnostics()
        assert "latency_ms" in d["test_src"]
        assert d["test_src"]["latency_ms"] >= 5  # at least ~5 ms

    def test_timed_collect_records_latency_on_exception(self, collector):
        def boom():
            import time
            time.sleep(0.005)
            raise RuntimeError("source went sideways")

        with pytest.raises(RuntimeError):
            collector._timed_collect("test_src", boom)
        d = collector.get_source_diagnostics()
        assert "latency_ms" in d["test_src"]
        assert d["test_src"]["reason_if_zero"] == "unreachable"

    def test_record_diagnostic_preserves_latency_across_calls(self, collector):
        # Pre-stamp simulates _timed_collect's pre-write.
        collector._source_diagnostics["src"] = {"latency_ms": 42}
        collector._record_diagnostic("src", attempted=1, yielded=1)
        d = collector.get_source_diagnostics()
        assert d["src"]["latency_ms"] == 42  # not clobbered by _record_diagnostic


class TestTCPInterfaceDoesNotClobberNoPositionList:
    """Regression: _collect_via_tcp_interface used to `=` overwrite too."""

    def test_tcp_extraction_includes_network_tag(self, collector):
        # Calling the private helper directly is the right shape here since
        # the full TCP path requires a live meshtasticd.
        info = collector._extract_node_info_without_position(
            node_id="!beef", data={"num": 0xbeef, "user": {}, "lastHeard": 0},
            now=1000, online_threshold_seconds=900,
        )
        # This is the value the TCP path appends to no_position_nodes —
        # must carry a network tag so by_network doesn't say "unknown".
        assert info["network"] == "meshtastic"


class TestCollectIsThreadSafe:
    """With ThreadingHTTPServer in front, collect() must serialize under
    a lock so concurrent requests don't race on per-call state
    (_nodes_without_position / _source_diagnostics) or double-collect.
    The second caller should get the cache the first caller just wrote."""

    def test_concurrent_callers_get_single_collection(self, collector, tmp_path):
        import threading, time
        calls = []
        real_locked = collector._collect_locked

        def counting_locked():
            calls.append(time.time())
            # Small delay to make the race window reliably hit in the test.
            time.sleep(0.2)
            return real_locked()

        with patch.object(collector, '_collect_locked', side_effect=counting_locked):
            results = [None, None]
            def worker(i):
                results[i] = collector.collect(max_age_seconds=60)
            t1 = threading.Thread(target=worker, args=(0,))
            t2 = threading.Thread(target=worker, args=(1,))
            t1.start(); t2.start()
            t1.join(); t2.join()

        # Only one actual collection ran — the second waiter hit the
        # inside-lock cache re-check and returned the first caller's result.
        assert len(calls) == 1, f"expected 1 collection, got {len(calls)}"
        assert results[0] is results[1]

    def test_cache_hit_does_not_acquire_lock(self, collector):
        """Fast path (cache hit) must NOT block on the lock — concurrent
        readers should not serialize during normal steady-state traffic."""
        import threading
        # Prime the cache
        collector._cached_geojson = {"type": "FeatureCollection", "features": []}
        collector._last_collect = __import__('time').time()

        # Hold the lock from another thread
        release = threading.Event()
        def lock_holder():
            with collector._collect_lock:
                release.wait(timeout=2)
        holder = threading.Thread(target=lock_holder)
        holder.start()
        try:
            # This must return immediately from the fast path; if it waited
            # on the held lock, the test would time out on holder.join below.
            result = collector.collect(max_age_seconds=30)
            assert result is collector._cached_geojson
        finally:
            release.set()
            holder.join(timeout=3)
            assert not holder.is_alive()
