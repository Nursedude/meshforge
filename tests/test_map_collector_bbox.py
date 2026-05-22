"""Integration tests for the external-bulk bbox filter inside MapDataCollector.

The pure helpers live in `tests/test_geo_filter.py`. This file pins the
wiring: external-bulk source lists actually pass through
`_apply_external_bulk_bbox`, the bbox is resolved from
operator_position.json, drop counts surface in
`get_bbox_filter_stats()`, and out-of-bbox features never reach
`record_observations` (so they don't poison node_history.db).
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from utils.map_data_collector import MapDataCollector


def _feature(fid: str, lat: float, lon: float) -> dict:
    """Helper — synthesize a GeoJSON Point feature."""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat, 0]},
        "properties": {"id": fid, "name": fid, "network": "meshcore"},
    }


def _make_collector(tmp_path, *, lat=19.5, lon=-155.5,
                    radius_km=500, enable_history=False):
    """Build a collector with operator_position.json + radius set.

    Returns the constructed collector. Caller can patch the source
    collector methods after.
    """
    fake_home = tmp_path / "home"
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    if lat is not None and lon is not None:
        (cfg_dir / "operator_position.json").write_text(
            json.dumps({"lat": lat, "lon": lon}),
        )
    settings_payload = {"external_bulk_radius_km": radius_km}
    (cfg_dir / "map_settings.json").write_text(json.dumps(settings_payload))
    # `utils.geo_filter` reads operator_position.json via get_real_user_home.
    # Patch BOTH the collector's import and geo_filter's import so the
    # synthetic home is honored end-to-end.
    with patch("utils.map_data_collector.get_real_user_home",
               return_value=fake_home), \
         patch("utils.geo_filter.get_real_user_home",
               return_value=fake_home):
        c = MapDataCollector(
            cache_dir=tmp_path / "cache",
            enable_history=enable_history,
            config_dir=cfg_dir,
        )
    # Stash the patch target so per-test collect calls can re-patch.
    c._test_fake_home = fake_home
    return c


def _silence_all_except(collector, override_name=None, override_features=None):
    """Stub all source collectors to return []. Optionally override one."""
    collector._collect_unified_tracker = lambda: []
    collector._collect_meshtasticd = lambda: []
    collector._collect_direct_radio = lambda: []
    collector._collect_mqtt = lambda: []
    collector._collect_node_tracker = lambda: []
    collector._collect_aredn = lambda: []
    collector._collect_aredn_worldmap = lambda: []
    collector._collect_rns_direct = lambda: []
    collector._collect_meshcore_public = lambda: []
    collector._collect_public_fallbacks = lambda current_feature_count=0: []
    if override_name is not None:
        if override_name == "public_fallback":
            setattr(
                collector,
                "_collect_public_fallbacks",
                lambda current_feature_count=0: list(override_features),
            )
        else:
            setattr(
                collector,
                f"_collect_{override_name}",
                lambda: list(override_features),
            )


class TestBboxAppliedAtMeshCorePublic:
    def test_in_bbox_kept_out_of_bbox_dropped(self, tmp_path):
        c = _make_collector(tmp_path)
        # Re-patch operator_position for collect() (load happens at
        # _collect_locked time, not __init__).
        with patch("utils.geo_filter.get_real_user_home",
                   return_value=c._test_fake_home):
            _silence_all_except(c, "meshcore_public", [
                _feature("inside",  19.5, -155.5),    # operator center
                _feature("outside", 0.0,    0.0),     # ~half a planet away
            ])
            result = c._collect_locked()
        ids = [f["properties"]["id"] for f in result["features"]]
        assert "inside" in ids
        assert "outside" not in ids
        stats = c.get_bbox_filter_stats()
        assert stats["bbox_dropped"].get("meshcore_public", 0) == 1

    def test_no_coords_dropped(self, tmp_path):
        c = _make_collector(tmp_path)
        # Feature without a Point geometry — bbox helper drops it.
        no_geom = {
            "type": "Feature",
            "geometry": {},
            "properties": {"id": "noloc", "name": "N", "network": "meshcore"},
        }
        with patch("utils.geo_filter.get_real_user_home",
                   return_value=c._test_fake_home):
            _silence_all_except(c, "meshcore_public", [no_geom])
            result = c._collect_locked()
        ids = [f["properties"]["id"] for f in result["features"]]
        assert "noloc" not in ids
        stats = c.get_bbox_filter_stats()
        assert stats["bbox_dropped"].get("meshcore_public", 0) == 1


class TestBboxAppliedAtArednWorldmap:
    def test_drops_out_of_bbox(self, tmp_path):
        c = _make_collector(tmp_path)
        with patch("utils.geo_filter.get_real_user_home",
                   return_value=c._test_fake_home):
            _silence_all_except(c, "aredn_worldmap", [
                _feature("hawaii", 19.5, -155.5),
                _feature("europe", 50.0, 10.0),
            ])
            result = c._collect_locked()
        ids = [f["properties"]["id"] for f in result["features"]]
        assert "hawaii" in ids
        assert "europe" not in ids
        stats = c.get_bbox_filter_stats()
        assert stats["bbox_dropped"].get("aredn_worldmap", 0) == 1


class TestBboxAppliedAtPublicFallback:
    def test_drops_out_of_bbox(self, tmp_path):
        c = _make_collector(tmp_path)
        with patch("utils.geo_filter.get_real_user_home",
                   return_value=c._test_fake_home):
            _silence_all_except(c, "public_fallback", [
                _feature("near", 19.6, -155.4),
                _feature("far",  -34.0, 18.0),  # Cape Town
            ])
            result = c._collect_locked()
        ids = [f["properties"]["id"] for f in result["features"]]
        assert "near" in ids
        assert "far" not in ids
        stats = c.get_bbox_filter_stats()
        assert stats["bbox_dropped"].get("public_fallback", 0) == 1


class TestBboxDisabledWhenNoOperatorPosition:
    def test_no_position_means_no_filter(self, tmp_path):
        # Build collector WITHOUT operator_position.json
        c = _make_collector(tmp_path, lat=None, lon=None)
        with patch("utils.geo_filter.get_real_user_home",
                   return_value=c._test_fake_home):
            _silence_all_except(c, "meshcore_public", [
                _feature("anywhere1", 19.5, -155.5),
                _feature("anywhere2", 50.0,   10.0),
                _feature("anywhere3", -34.0,  18.0),
            ])
            result = c._collect_locked()
        ids = {f["properties"]["id"] for f in result["features"]}
        # All three pass through — filter inactive without position.
        assert ids == {"anywhere1", "anywhere2", "anywhere3"}
        stats = c.get_bbox_filter_stats()
        assert stats["bbox"] is None
        assert stats["bbox_dropped"] == {}


class TestBboxDoesNotTouchLocalSources:
    def test_local_radio_features_never_filtered(self, tmp_path):
        c = _make_collector(tmp_path)
        with patch("utils.geo_filter.get_real_user_home",
                   return_value=c._test_fake_home):
            # Inject a local-radio feature OUTSIDE the bbox. The local
            # path shouldn't see the bbox helper at all — even nodes
            # outside the radius (e.g., a roaming operator-known peer)
            # stay visible.
            _silence_all_except(c, "meshtasticd", [
                _feature("europe-roam", 50.0, 10.0),
            ])
            result = c._collect_locked()
        ids = [f["properties"]["id"] for f in result["features"]]
        assert "europe-roam" in ids
        stats = c.get_bbox_filter_stats()
        assert "meshtasticd" not in stats["bbox_dropped"]


class TestBboxDropsDoNotReachHistory:
    def test_history_record_observations_excludes_filtered_meshcore(self, tmp_path):
        c = _make_collector(tmp_path, enable_history=True)
        c._history.record_observations = MagicMock()
        with patch("utils.geo_filter.get_real_user_home",
                   return_value=c._test_fake_home):
            _silence_all_except(c, "meshcore_public", [
                _feature("inside",  19.5, -155.5),
                _feature("outside", 0.0,    0.0),
            ])
            c._collect_locked()
        # Whatever record_observations saw, it must NOT contain "outside".
        all_persisted = []
        for call in c._history.record_observations.call_args_list:
            args = call.args[0] if call.args else call.kwargs.get("features", [])
            all_persisted.extend(args)
        ids = [f.get("properties", {}).get("id") for f in all_persisted]
        assert "inside" in ids
        assert "outside" not in ids


class TestBboxStatsShape:
    def test_get_bbox_filter_stats_when_active(self, tmp_path):
        c = _make_collector(tmp_path)
        with patch("utils.geo_filter.get_real_user_home",
                   return_value=c._test_fake_home):
            _silence_all_except(c, "meshcore_public", [
                _feature("inside", 19.5, -155.5),
                _feature("outside", 0.0, 0.0),
            ])
            c._collect_locked()
        stats = c.get_bbox_filter_stats()
        assert set(stats.keys()) == {
            "bbox", "bbox_dropped", "federated_skipped_persistence",
        }
        assert stats["bbox"] is not None
        assert set(stats["bbox"].keys()) == {
            "lat_min", "lat_max", "lon_min", "lon_max",
        }
        assert stats["bbox_dropped"]["meshcore_public"] == 1
        assert stats["federated_skipped_persistence"] == 0
