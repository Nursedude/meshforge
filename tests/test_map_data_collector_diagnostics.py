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
    """Fresh collector with an isolated cache dir AND settings dir per test.

    Passing `config_dir=tmp_path` forces SettingsManager to a clean
    directory — without it, tests read the real
    ~/.config/meshforge/map_settings.json on the dev box and pick up
    operator-set keys (e.g. aredn_node_ips on a real fleet host),
    defeating `assert d["aredn"]["reason_if_zero"] == "not_configured"`.

    Hermetic against the public internet: with default settings the collector
    fetches the AREDN worldmap (``enable_aredn_worldmap_fallback`` defaults
    True), and tests that run the real ``collect()`` path otherwise reach
    ``worldmap.arednmesh.org`` live. When that host is slow/unreachable the
    15s per-source connect timeout × the CI ``--timeout=30`` thread killer
    reddened the suite on commits that touched no collector code (observed
    2026-06-16, host down). We construct the collector first (so __init__ may
    use the network), then block ``socket.socket`` for the test body: any
    fetch now raises OSError instantly, which the fetchers already catch and
    turn into ``[]`` + an "unreachable"/empty source diagnostic — identical to
    a real dead host, just without the hang. Tests that exercise a public
    source patch ``urllib.request.urlopen`` themselves, so they never reach a
    real socket and are unaffected.
    """
    c = MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path, enable_history=False)

    def _blocked_socket(*_a, **_k):
        raise OSError("network blocked in unit test (collector fixture)")

    with patch("socket.socket", _blocked_socket):
        yield c


class TestPositionLessThroughHistory:
    """Issue #49 follow-up — position-less nodes (MeshCore adverts, RNS
    announces) must reach the directory table. Caught live on moc3:
    1237 unified_tracker nodes had no GPS and weren't being persisted
    despite the directory schema supporting NULL lat/lon."""

    def test_synthesizes_features_for_position_less(self, tmp_path):
        from utils.map_data_collector import MapDataCollector
        c = MapDataCollector(cache_dir=tmp_path, enable_history=True)
        # Mock the collect path to skip every real source.
        for src in ("_collect_unified_tracker", "_collect_meshtasticd",
                    "_collect_direct_radio", "_collect_mqtt",
                    "_collect_node_tracker", "_collect_aredn",
                    "_collect_aredn_worldmap", "_collect_rns_direct",
                    "_collect_meshcore_public",
                    "_collect_public_fallbacks"):
            setattr(c, src, lambda *a, **kw: [])
        # Inject position-less entries directly so the synthetic-feature
        # path is exercised end-to-end.
        c._collect_locked.__func__  # ensure attribute exists
        c._nodes_without_position = []
        # Patch one collector to populate the list.
        def _fake_unified():
            c._nodes_without_position.extend([
                {"id": "meshcore:abcd", "name": "MC1",
                 "network": "meshcore", "is_online": True},
                {"id": "rns:efgh", "name": "RN1",
                 "network": "rns", "is_online": True},
            ])
            return []
        c._collect_unified_tracker = _fake_unified

        c._collect_locked()
        # Both should now exist in the directory.
        feats, pos_less = c._history.get_directory_snapshot()
        ids_pos_less = {p["id"] for p in pos_less}
        assert "meshcore:abcd" in ids_pos_less, (
            "MeshCore advert should reach directory as position-less"
        )
        assert "rns:efgh" in ids_pos_less
        # Tier tagging: MeshCore (via unified tracker) → local_radio (gateway-RX);
        # RNS → rns_path_table.
        by_id = {p["id"]: p for p in pos_less}
        assert by_id["meshcore:abcd"]["source_origin"] == "local_radio"
        assert by_id["rns:efgh"]["source_origin"] == "rns_path_table"


class TestSourceOriginTagging:
    """Issue #49 — directory tier retention is driven by source_origin
    set on each feature. _tag_source_origin stamps it post-collect."""

    def test_tags_unstamped_features(self, collector):
        feats = [
            {"properties": {"id": "!a"}},
            {"properties": {"id": "!b"}},
        ]
        out = collector._tag_source_origin(feats, "local_radio")
        assert out is feats  # in-place modification
        assert all(
            f["properties"]["source_origin"] == "local_radio" for f in feats
        )

    def test_does_not_overwrite_existing_origin(self, collector):
        # Higher-trust source already tagged this feature earlier in the
        # collect cycle; later tagger must NOT overwrite (sticky).
        feats = [
            {"properties": {"id": "!a", "source_origin": "local_radio"}},
            {"properties": {"id": "!b"}},
        ]
        collector._tag_source_origin(feats, "meshcore_public")
        assert feats[0]["properties"]["source_origin"] == "local_radio"
        assert feats[1]["properties"]["source_origin"] == "meshcore_public"

    def test_skips_malformed_features(self, collector):
        feats = [
            {"properties": "not_a_dict"},   # broken — must not crash
            {"no_properties_key": True},
            {"properties": {"id": "!ok"}},
        ]
        # Must not raise.
        collector._tag_source_origin(feats, "rns_path_table")
        assert feats[2]["properties"]["source_origin"] == "rns_path_table"


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

    def test_get_aredn_node_ip_skips_socket_when_no_custom_ips(self, collector):
        """Empty aredn_node_ips → no socket calls. Returns None immediately.

        Without this fast-skip, the previous default-host walk burned 4-5 s
        per cache-miss collect on non-AREDN boxes (the 95% case) probing
        localnode.local.mesh + 10.0.0.1 + 10.1.0.1 + localnode.
        """
        with patch("socket.socket") as mock_sock:
            result = collector._get_aredn_node_ip()
        assert result is None
        assert mock_sock.call_count == 0, (
            "socket.socket was called despite aredn_node_ips=[]; "
            "fast-skip regression — see _map_collector_aredn.py docstring."
        )

    def test_get_aredn_node_ip_probes_only_custom_ips(self, collector):
        """Configured aredn_node_ips → probe ONLY those. No default-host fallback."""
        collector._settings.set("aredn_node_ips", ["10.55.66.77"])
        with patch("socket.socket") as mock_sock:
            mock_sock.return_value.connect_ex.return_value = 1  # nonzero = unreachable
            collector._get_aredn_node_ip()
        # Exactly one socket per custom IP, no defaults appended
        assert mock_sock.call_count == 1, (
            f"expected 1 socket call (custom IP only), got {mock_sock.call_count}"
        )


class TestArednFeatureSourceField:
    """AREDN local-collector features must carry source='aredn'.

    Sibling worldmap path sets source='aredn_worldmap'. Frontend currently
    filters on `network`, but a future Data-Sources UI filter (and any
    cross-source dedup logic) needs the provenance tag set consistently.
    """

    def _make_aredn_node(self, hostname="bi-ecom"):
        """Minimal AREDNNode-like object with a location."""
        node = MagicMock()
        node.has_location.return_value = True
        node.hostname = hostname
        node.latitude = 19.42
        node.longitude = -155.28
        node.model = "MikroTik hAP"
        node.tunnel_count = 0
        node.mesh_status = "AREDN"
        node.links = []
        return node

    def test_aredn_feature_has_source_aredn(self, collector):
        node = self._make_aredn_node()
        feature = collector._aredn_node_to_feature(node)
        assert feature is not None, "valid AREDN node should produce a feature"
        assert feature["properties"].get("source") == "aredn", (
            "AREDN local-collector feature must carry source='aredn' "
            "(matches the provenance pattern used by worldmap, meshmap_net, "
            "rmap_world, meshtasticd_http, meshcore_public_map paths)."
        )

    def test_aredn_feature_network_unchanged(self, collector):
        """Adding `source` must not regress the existing `network` tag."""
        node = self._make_aredn_node()
        feature = collector._aredn_node_to_feature(node)
        assert feature["properties"].get("network") == "aredn"

    def test_aredn_feature_no_location_returns_none(self, collector):
        """Source-tag change must not change the no-location skip behavior."""
        node = self._make_aredn_node()
        node.has_location.return_value = False
        assert collector._aredn_node_to_feature(node) is None


class TestCollectExposesDiagnostics:
    @staticmethod
    def _patched(coll):
        """Patch every source-collection method on an instance to return [].

        Must include the live-network sources (``_collect_aredn_worldmap``,
        ``_collect_meshcore_public``) or collect() reaches public hosts for
        real — when one is slow/down it hangs the test past CI's --timeout=30
        (observed 2026-06-16). Mirror TestGeoJSONExposesDirectoryStats's
        complete list; a missing source here is a silent network dependency.
        """
        sources = [
            "_collect_unified_tracker", "_collect_meshtasticd", "_collect_direct_radio",
            "_collect_mqtt", "_collect_node_tracker", "_collect_aredn",
            "_collect_aredn_worldmap", "_collect_meshcore_public",
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
                     "_collect_node_tracker", "_collect_aredn", "_collect_aredn_worldmap",
                     "_collect_meshcore_public", "_collect_rns_direct",
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


class TestGeoJSONExposesDirectoryStats:
    """Issue #49 follow-up — the persistent node directory must be reachable
    from /api/nodes/geojson so the sidebar can count per-protocol from the
    directory (which knows about position-less RNS / MeshCore nodes) rather
    than from positioned features only. Caught live on moc: 43 RNS nodes
    in directory.by_network.rns, but sidebar showed RNS:0 because no RNS
    node carries GPS by protocol."""

    @staticmethod
    def _stub_sources(coll):
        for name in ("_collect_unified_tracker", "_collect_meshtasticd",
                     "_collect_direct_radio", "_collect_mqtt",
                     "_collect_node_tracker", "_collect_aredn",
                     "_collect_aredn_worldmap", "_collect_rns_direct",
                     "_collect_meshcore_public",
                     "_collect_public_fallbacks"):
            setattr(coll, name, lambda *_a, **_kw: [])

    def test_directory_block_present_when_history_enabled(self, tmp_path):
        c = MapDataCollector(cache_dir=tmp_path, enable_history=True)
        self._stub_sources(c)
        result = c.collect(max_age_seconds=0)
        directory = result["properties"]["directory"]
        assert isinstance(directory, dict), (
            "directory block must be present in geojson properties when "
            "history is enabled (frontend reads it for per-protocol counts)"
        )
        assert "by_network" in directory
        assert "total" in directory

    def test_directory_block_is_none_without_history(self, tmp_path):
        c = MapDataCollector(cache_dir=tmp_path, enable_history=False)
        self._stub_sources(c)
        result = c.collect(max_age_seconds=0)
        assert result["properties"]["directory"] is None

    def test_directory_failure_does_not_break_geojson(self, tmp_path):
        c = MapDataCollector(cache_dir=tmp_path, enable_history=True)
        self._stub_sources(c)

        def _boom():
            raise RuntimeError("simulated directory failure")

        c._history.get_directory_stats = _boom
        result = c.collect(max_age_seconds=0)
        # Geojson must remain serializable; directory falls back to None.
        assert result["properties"]["directory"] is None
        import json
        json.dumps(result)

    def test_directory_by_network_reflects_observed_protocols(self, tmp_path):
        """Round-trip check: feeding RNS + MeshCore position-less nodes
        through one collect() cycle must surface them in
        directory.by_network on the next collect()."""
        c = MapDataCollector(cache_dir=tmp_path, enable_history=True)
        self._stub_sources(c)

        def _seed():
            c._nodes_without_position.extend([
                {"id": "rns:aaaa", "name": "R1", "network": "rns",
                 "is_online": True},
                {"id": "meshcore:bbbb", "name": "M1", "network": "meshcore",
                 "is_online": True},
            ])
            return []

        c._collect_unified_tracker = _seed
        c.collect(max_age_seconds=0)

        # Second pass: no new input, but directory stats reflect last seed.
        c._collect_unified_tracker = lambda: []
        c._nodes_without_position = []
        result = c.collect(max_age_seconds=0)
        by_net = result["properties"]["directory"]["by_network"]
        assert by_net.get("rns", 0) >= 1
        assert by_net.get("meshcore", 0) >= 1


class TestMeshtasticDoesNotClobberNoPositionList:
    """Regression: _collect_via_http used to = (overwrite) the no-position list,
    wiping MeshCore entries captured earlier by _collect_unified_tracker."""

    @patch("utils._map_collector_meshtastic.get_http_client")
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

    def test_iso8601_last_advert_normalized_to_epoch(self, collector):
        # Real upstream shape from map.meshcore.dev/api/v1/nodes —
        # last_advert is ISO-8601 with .000Z suffix, NOT Unix epoch.
        # Pre-fix this fell through float() ValueError and last_heard
        # was emitted as the raw string, which broke F7's upstream-stamp
        # path in node_history._build_directory_row.
        result = collector._parse_meshcore_public_node({
            "public_key": "abc",
            "adv_lat": 19.42, "adv_lon": -155.28,
            "type": 1,
            "last_advert": "2026-04-27T19:45:54.000Z",
        })
        assert result is not None
        last_heard = result["properties"]["last_heard"]
        assert isinstance(last_heard, float)
        # Sanity: 2026-04-27T19:45:54Z is 1777319154.0
        assert 1777319100 < last_heard < 1777319200

    def test_numeric_last_advert_passthrough(self, collector):
        # Forward-compat: if upstream ever switches to Unix epoch numeric,
        # the parser must still accept it.
        result = collector._parse_meshcore_public_node({
            "public_key": "abc",
            "adv_lat": 19.42, "adv_lon": -155.28,
            "type": 1,
            "last_advert": 1777707954.5,
        })
        assert result is not None
        assert result["properties"]["last_heard"] == 1777707954.5

    def test_invalid_iso_falls_back_to_zero(self, collector):
        # Garbage timestamp must not raise; last_heard=0 cleanly signals
        # "no credible upstream stamp" so _build_directory_row falls back
        # to now (the row is treated as freshly observed).
        result = collector._parse_meshcore_public_node({
            "public_key": "abc",
            "adv_lat": 19.42, "adv_lon": -155.28,
            "type": 1,
            "last_advert": "not-a-date",
        })
        assert result is not None
        assert result["properties"]["last_heard"] == 0.0
        assert result["properties"]["is_online"] is False

    def test_missing_last_advert_zero_no_online(self, collector):
        result = collector._parse_meshcore_public_node({
            "public_key": "abc",
            "adv_lat": 19.42, "adv_lon": -155.28,
            "type": 1,
        })
        assert result is not None
        assert result["properties"]["last_heard"] == 0.0
        assert result["properties"]["is_online"] is False

    def test_iso_last_advert_round_trips_to_directory_upstream_stamp(self, collector, tmp_path):
        # End-to-end: parser output flows through _build_directory_row
        # and lands as the row's last_seen. This is the F7 contract that
        # silently failed when the parser emitted ISO-8601 strings.
        from utils.node_history import NodeHistoryDB
        feature = collector._parse_meshcore_public_node({
            "public_key": "abc",
            "adv_lat": 19.42, "adv_lon": -155.28,
            "type": 1,
            "last_advert": "2026-04-27T19:45:54.000Z",
        })
        feature["properties"]["source_origin"] = "meshcore_public"
        hist = NodeHistoryDB(db_path=tmp_path / "directory.db")
        now = 1777800000.0  # well after the upstream stamp
        row = hist._build_directory_row(feature, now)
        assert row is not None
        # Tuple shape: (network, node_id, last_seen, ...)
        last_seen = row[2]
        # last_seen MUST be the upstream stamp (~1777707954), not now.
        assert last_seen < now
        assert 1777319100 < last_seen < 1777319200, (
            f"expected upstream-stamp last_seen, got {last_seen} (now={now})"
        )


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


class TestSourceTimeout:
    """T2.1 — per-source HTTP timeout reads from settings, defaults to 15s."""

    @patch("urllib.request.urlopen")
    def test_meshcore_fetch_uses_settings_timeout(self, mock_urlopen, collector):
        collector._settings.set("enable_meshcore_public", True)
        collector._settings.set("source_timeout_seconds", 7)
        # Force cache miss so the fetch path runs.
        collector._meshcore_public_cache = None
        collector._meshcore_public_cache_ts = None

        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = b"[]"
        mock_urlopen.return_value = resp

        collector._fetch_meshcore_public_uncached()
        # urlopen should have been called with timeout=7 (overriding the default).
        _, kwargs = mock_urlopen.call_args
        assert kwargs.get("timeout") == 7

    def test_public_mixin_get_source_timeout_default(self, collector):
        # No setting → default 15 (DEFAULT_SOURCE_TIMEOUT_SECONDS).
        collector._settings._settings.pop("source_timeout_seconds", None)
        assert collector._get_source_timeout() == 15

    def test_public_mixin_get_source_timeout_setting_wins(self, collector):
        collector._settings.set("source_timeout_seconds", 9)
        assert collector._get_source_timeout() == 9


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
