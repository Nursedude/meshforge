"""Contract tests for /api/los — the response must match the analyzer.

Born 2026-09-14. The response builder read `result.profile` and
`result.obstructions` behind `hasattr()` guards. ``LOSResult`` has never had
either attribute — it has ``elevation_profile`` / ``num_obstructions`` — so
``profile`` came back ``[]`` and ``obstruction_count`` came back ``0`` on
EVERY call, with real SRTM terrain loaded and a correct verdict beside them.

A ``hasattr()`` guard against a sibling module's shape cannot fail loudly: it
silently publishes nothing, forever, and the drift is invisible to every other
test. These tests pin the names instead, so renaming a field on ``LOSResult``
breaks here rather than emptying the API.
"""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from utils.map_http_handler import MapRequestHandler
from utils.terrain import (
    LOSAnalyzer,
    LOSResult,
    SyntheticTerrainProvider,
    TerrainProvider,
)


PROFILE_POINTS = 100


class _NoDataProvider(TerrainProvider):
    """A provider that answers, but with nothing real behind the answer.

    Mirrors SRTMProvider with a missing tile: get_elevation() returns a
    perfectly valid-looking 0.0 while has_elevation() says "that was a
    placeholder".
    """

    def get_elevation(self, lat: float, lon: float) -> float:
        return 0.0

    def has_elevation(self, lat: float, lon: float) -> bool:
        return False


def _make_handler(path: str) -> MapRequestHandler:
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = path
    h.headers = {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    h.send_header = MagicMock()
    h._served = {}

    def _capture(payload):
        h._served.clear()
        h._served.update(payload)

    h._serve_json = _capture
    return h


def _call_los(provider, lat1=19.7297, lon1=-155.09, lat2=19.8207, lon2=-155.4681,
              query="?alt1=10&alt2=10"):
    path = f"/api/los/{lat1}/{lon1}/{lat2}/{lon2}{query}"
    h = _make_handler(path)
    with patch("utils._map_node_endpoints._HAS_TERRAIN", True), \
         patch("utils._map_node_endpoints._SRTMProvider", lambda *a, **k: provider), \
         patch("utils._map_node_endpoints._LOSAnalyzer",
               lambda p: LOSAnalyzer(p, profile_points=PROFILE_POINTS)):
        h._serve_los([str(lat1), str(lon1), str(lat2), str(lon2)])
    return h._served


class TestLOSResponseMatchesAnalyzer:
    """Every attribute the endpoint reads must exist on LOSResult."""

    @pytest.mark.parametrize("attr", [
        "elevation_profile",
        "los_heights",
        "fresnel_radii",
        "num_obstructions",
        "fspl_db",
        "distance_m",
        "is_clear",
        "total_loss_db",
        "terrain_loss_db",
        "fresnel_clearance_pct",
        "terrain_samples_total",
        "terrain_samples_missing",
        "terrain_complete",
    ])
    def test_attribute_exists(self, attr):
        assert hasattr(LOSResult(), attr), (
            f"_serve_los reads result.{attr}; LOSResult no longer has it. "
            "Reconcile the two rather than letting the field publish a default."
        )

    def test_profile_is_populated_not_empty(self):
        """THE regression: profile was always [] regardless of terrain."""
        resp = _call_los(SyntheticTerrainProvider())
        assert "error" not in resp, resp
        assert len(resp["profile"]) == PROFILE_POINTS

    def test_profile_entries_carry_every_series_the_chart_plots(self):
        resp = _call_los(SyntheticTerrainProvider())
        for key in ("distance_m", "elevation_m", "los_height_m",
                    "fresnel_top", "fresnel_bottom"):
            assert key in resp["profile"][0], key

    def test_profile_distance_spans_the_path(self):
        resp = _call_los(SyntheticTerrainProvider())
        prof = resp["profile"]
        assert prof[0]["distance_m"] == pytest.approx(0.0)
        assert prof[-1]["distance_m"] == pytest.approx(resp["distance_m"])

    def test_obstruction_count_tracks_the_analyzer(self):
        """Was hardcoded to 0 by a hasattr() guard on a missing attribute."""
        provider = SyntheticTerrainProvider(base_elevation=2000.0,
                                            ridge_height=1500.0)
        resp = _call_los(provider)
        analyzer = LOSAnalyzer(provider, profile_points=PROFILE_POINTS)
        expected = analyzer.analyze(19.7297, -155.09, 10,
                                    19.8207, -155.4681, 10, 906).num_obstructions
        assert expected > 0, "fixture should obstruct, else this proves nothing"
        assert resp["obstruction_count"] == expected


class TestFresnelGeometry:
    def test_endpoints_have_zero_fresnel_width(self):
        """The first Fresnel zone has no width at either antenna.

        local_fresnel used to be assigned only inside the interior branch, so
        an endpoint sample silently reused the PREVIOUS point's radius.
        """
        resp = _call_los(SyntheticTerrainProvider())
        prof = resp["profile"]
        for edge in (prof[0], prof[-1]):
            assert edge["fresnel_top"] == pytest.approx(edge["los_height_m"])
            assert edge["fresnel_bottom"] == pytest.approx(edge["los_height_m"])

    def test_interior_zone_straddles_the_los_line(self):
        resp = _call_los(SyntheticTerrainProvider())
        mid = resp["profile"][PROFILE_POINTS // 2]
        assert mid["fresnel_top"] > mid["los_height_m"] > mid["fresnel_bottom"]


class TestTerrainCoverageHonesty:
    """A missing tile must not read as sea level."""

    def test_complete_coverage_reported_when_data_exists(self):
        resp = _call_los(SyntheticTerrainProvider())
        assert resp["terrain_complete"] is True
        assert resp["terrain_samples_missing"] == 0
        assert resp["terrain_samples_total"] == PROFILE_POINTS

    def test_missing_data_is_not_laundered_into_a_verdict(self):
        resp = _call_los(_NoDataProvider())
        assert resp["terrain_complete"] is False
        assert resp["terrain_samples_missing"] == PROFILE_POINTS

    def test_coverage_travels_with_the_verdict(self):
        """is_clear alone must never be consumable without the caveat."""
        resp = _call_los(_NoDataProvider())
        assert "is_clear" in resp and "terrain_complete" in resp
