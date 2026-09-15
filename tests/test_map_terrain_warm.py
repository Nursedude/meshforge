"""The map's start-up SRTM warm: this box's OWN nodes, once, bounded.

Review finding 3 (2026-09-14): the first cut warmed tiles for EVERY
positioned feature — 49k public MeshCore nodes worldwide included — and
spent the 9-tile budget first-come. These pin the local-only filter, the
null-island drop, the density ranking and the once-guard.
"""

from unittest.mock import MagicMock, patch

from utils.map_data_service import MapServer


def _feat(lat, lon, origin="local_radio", is_local=None):
    props = {"source_origin": origin}
    if is_local is not None:
        props["is_local"] = is_local
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props}


def _server_with(features):
    srv = MapServer.__new__(MapServer)
    srv.collector = MagicMock()
    srv.collector._cached_geojson = {"features": features}
    return srv


class TestWarmPoints:
    def test_public_bulk_feeds_are_excluded(self):
        srv = _server_with([_feat(48.8, 2.3, "meshcore_public"), _feat(51.5, -0.1, "aredn_worldmap"),
                            _feat(19.7, -155.1, "local_radio")])
        with patch("utils.map_data_service._operator_position", return_value=None):
            ranked, n = srv._terrain_warm_points()
        assert n == 1 and ranked == [(19.7, -155.1)]

    def test_is_local_overrides_origin(self):
        srv = _server_with([_feat(19.7, -155.1, "meshcore_public", is_local=True)])
        with patch("utils.map_data_service._operator_position", return_value=None):
            ranked, n = srv._terrain_warm_points()
        assert n == 1

    def test_null_island_is_dropped(self):
        srv = _server_with([_feat(0.0, 0.0), _feat(19.7, -155.1)])
        with patch("utils.map_data_service._operator_position", return_value=None):
            ranked, n = srv._terrain_warm_points()
        assert ranked == [(19.7, -155.1)]

    def test_densest_tile_first_one_point_per_tile(self):
        srv = _server_with([_feat(20.7, -156.4)] + [_feat(19.7 + i * 0.01, -155.1) for i in range(3)])
        with patch("utils.map_data_service._operator_position", return_value=None):
            ranked, n = srv._terrain_warm_points()
        assert n == 4 and len(ranked) == 2
        assert int(ranked[0][0]) == 19

    def test_operator_position_leads(self):
        srv = _server_with([_feat(20.7, -156.4)])
        with patch("utils.map_data_service._operator_position", return_value=(19.5, -155.9)):
            ranked, n = srv._terrain_warm_points()
        assert ranked[0] == (19.5, -155.9)


class TestWarmRunsOnce:
    def test_second_call_is_a_no_op(self):
        srv = _server_with([_feat(19.7, -155.1)])
        prov = MagicMock()
        prov.warm_tiles.return_value = {k: [] for k in
                                        ("wanted", "cached", "downloaded", "failed",
                                         "skipped_budget", "skipped_disk")}
        with patch("utils._map_node_endpoints._terrain_provider", lambda: prov), \
             patch("utils._map_node_endpoints._HAS_TERRAIN", True), \
             patch("utils.map_data_service._operator_position", return_value=None):
            srv._warm_terrain_tiles()
            srv._warm_terrain_tiles()
        assert prov.warm_tiles.call_count == 1

    def test_zero_budget_disables(self):
        srv = _server_with([_feat(19.7, -155.1)])
        prov = MagicMock()
        with patch("utils._map_node_endpoints._terrain_provider", lambda: prov), \
             patch.dict("os.environ", {"MESHFORGE_SRTM_WARM_MAX": "0"}):
            srv._warm_terrain_tiles()
        prov.warm_tiles.assert_not_called()
