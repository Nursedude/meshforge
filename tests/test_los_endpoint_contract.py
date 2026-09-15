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


def _make_handler(path: str, client="127.0.0.1") -> MapRequestHandler:
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = path
    h.headers = {}
    # The terrain endpoints are gated to loopback / configured LAN (2026-09-14).
    h.client_address = (client, 5000)
    h.allowed_origins = None
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
              query="?alt1=10&alt2=10", client="127.0.0.1"):
    path = f"/api/los/{lat1}/{lon1}/{lat2}/{lon2}{query}"
    h = _make_handler(path, client=client)
    with patch("utils._map_node_endpoints._HAS_TERRAIN", True), \
         patch("utils._map_node_endpoints._terrain_provider", lambda: provider), \
         patch("utils._map_node_endpoints._LOSAnalyzer",
               lambda p: LOSAnalyzer(p, profile_points=PROFILE_POINTS)):
        h._serve_los([str(lat1), str(lon1), str(lat2), str(lon2)])
    return h._served


def _call_coverage(provider, lat=19.7297, lon=-155.09, alt=10,
                   query="?radius_km=2&resolution=2", client="127.0.0.1"):
    path = f"/api/coverage/{lat}/{lon}/{alt}{query}"
    h = _make_handler(path, client=client)
    with patch("utils._map_node_endpoints._HAS_TERRAIN", True), \
         patch("utils._map_node_endpoints._terrain_provider", lambda: provider), \
         patch("utils._map_node_endpoints._LOSAnalyzer",
               lambda p: LOSAnalyzer(p, profile_points=PROFILE_POINTS)):
        h._serve_coverage([str(lat), str(lon), str(alt)])
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


# ── 2026-09-14 frontier security pass — the three bounds ──────────────────

class TestTerrainEndpointsAreGated:
    """Both amplifiers take _reject_if_untrusted(); loopback still works."""

    def test_los_rejects_untrusted_client(self):
        statuses = {}
        h = _make_handler("/api/los/19.7/-155.1/19.8/-155.2", client="203.0.113.9")
        h._serve_json = lambda payload, status=200: statuses.update(status=status, payload=payload)
        h._serve_los(["19.7", "-155.1", "19.8", "-155.2"])
        assert statuses["status"] == 403

    def test_coverage_rejects_untrusted_client(self):
        statuses = {}
        h = _make_handler("/api/coverage/19.7/-155.1/10", client="203.0.113.9")
        h._serve_json = lambda payload, status=200: statuses.update(status=status, payload=payload)
        h._serve_coverage(["19.7", "-155.1", "10"])
        assert statuses["status"] == 403

    def test_loopback_still_answers(self):
        resp = _call_los(SyntheticTerrainProvider())
        assert "error" not in resp and "is_clear" in resp


def _status_of(call, *a, **k):
    """Run a handler call and return (status, payload) via a status-aware capture."""
    captured = {}
    provider = k.pop("provider", SyntheticTerrainProvider())
    client = k.pop("client", "127.0.0.1")
    path = k.pop("path")
    h = _make_handler(path, client=client)
    h._serve_json = lambda payload, status=200: captured.update(status=status, payload=payload)
    with patch("utils._map_node_endpoints._HAS_TERRAIN", True), \
         patch("utils._map_node_endpoints._terrain_provider", lambda: provider), \
         patch("utils._map_node_endpoints._LOSAnalyzer",
               lambda p: LOSAnalyzer(p, profile_points=PROFILE_POINTS)):
        getattr(h, call)(*a)
    return captured.get("status"), captured.get("payload")


class TestFiniteRangeValidation:
    """float() accepts nan/inf; the analyzer must never see them."""

    @pytest.mark.parametrize("query", ["?alt1=nan", "?alt1=inf", "?alt1=-5", "?alt1=1e309",
                                       "?freq_mhz=0", "?freq_mhz=nan"])
    def test_los_bad_query_is_400(self, query):
        status, payload = _status_of("_serve_los", ["19.7", "-155.1", "19.8", "-155.2"],
                                     path=f"/api/los/19.7/-155.1/19.8/-155.2{query}")
        assert status == 400, (status, payload)
        assert "error" in payload

    @pytest.mark.parametrize("parts", [["95", "0", "95.001", "0"], ["nan", "0", "1", "1"],
                                       ["19.7", "-181", "19.8", "-155"], ["19.7", "x", "19.8", "-155"]])
    def test_los_bad_coordinates_are_400(self, parts):
        status, payload = _status_of("_serve_los", parts, path="/api/los/" + "/".join(parts))
        assert status == 400, (status, payload)

    @pytest.mark.parametrize("query", ["?radius_km=nan", "?radius_km=0", "?radius_km=51",
                                       "?resolution=0", "?resolution=49", "?resolution=inf"])
    def test_coverage_bad_query_is_400(self, query):
        status, payload = _status_of("_serve_coverage", ["19.7", "-155.1", "10"],
                                     path=f"/api/coverage/19.7/-155.1/10{query}")
        assert status == 400, (status, payload)

    def test_no_nan_token_can_reach_the_body(self):
        """The old path published `NaN` — not JSON, and a confident verdict beside it."""
        import json as _json
        resp = _call_los(SyntheticTerrainProvider())
        _json.loads(_json.dumps(resp, allow_nan=False))


class TestPerBoxTerrainBudget:
    """Beyond N concurrent computations the box answers 503 + Retry-After."""

    def test_exhausted_slots_answer_503_with_retry_after(self):
        from utils import _map_node_endpoints as ep
        sem = ep._TERRAIN_SLOTS
        held = 0
        while sem.acquire(blocking=False):
            held += 1
        try:
            h = _make_handler("/api/los/19.7/-155.1/19.8/-155.2")
            with patch("utils._map_node_endpoints._HAS_TERRAIN", True), \
                 patch("utils._map_node_endpoints._terrain_provider",
                       lambda: SyntheticTerrainProvider()):
                h._serve_los(["19.7", "-155.1", "19.8", "-155.2"])
            h.send_response.assert_called_once_with(503)
            headers = {c.args[0]: c.args[1] for c in h.send_header.call_args_list}
            assert headers.get("Retry-After") == str(ep._TERRAIN_RETRY_AFTER_S)
            body = h.wfile.getvalue()
            assert b"terrain busy" in body
        finally:
            for _ in range(held):
                sem.release()

    def test_slot_is_released_after_a_failing_computation(self):
        from utils import _map_node_endpoints as ep

        class _Boom(TerrainProvider):
            def get_elevation(self, lat, lon):
                raise RuntimeError("boom")

        before = ep._TERRAIN_SLOTS._value
        status, payload = _status_of("_serve_los", ["19.7", "-155.1", "19.8", "-155.2"],
                                     path="/api/los/19.7/-155.1/19.8/-155.2", provider=_Boom())
        assert status == 500
        assert ep._TERRAIN_SLOTS._value == before, "a failed computation must give its slot back"


class TestMissingTilesAreNamed:
    def test_incomplete_terrain_carries_the_note_and_the_tile_names(self):
        class _NoDataNamed(_NoDataProvider):
            def missing_tiles_for(self, pts):
                return ["N19W156.hgt"]
        resp = _call_los(_NoDataNamed())
        assert resp["terrain_complete"] is False
        assert "no terrain tile cached on this box" in resp["terrain_note"]
        assert resp["terrain_tiles_missing"] == ["N19W156.hgt"]

    def test_complete_terrain_carries_no_note(self):
        resp = _call_los(SyntheticTerrainProvider())
        assert "terrain_note" not in resp


# ── added after the 2026-09-14 self-review of the fix commit ─────────────

class TestTheSingletonNeverDownloads:
    """THE security claim of the commit, pinned at the consumer of record."""

    def test_provider_singleton_is_built_with_auto_download_false(self):
        from utils import _map_node_endpoints as ep
        seen = {}

        class _Stub:
            def __init__(self, *a, **k):
                seen.update(k)

        with patch.object(ep, "_SRTMProvider", _Stub), \
             patch.object(ep, "_provider_singleton", None):
            ep._terrain_provider()
            again = ep._terrain_provider()
        assert seen.get("auto_download") is False
        assert isinstance(again, _Stub)

    def test_real_singleton_never_touches_the_network(self, tmp_path):
        from utils import _map_node_endpoints as ep
        from utils.terrain import SRTMProvider
        real = SRTMProvider(cache_dir=tmp_path, auto_download=False)
        with patch.object(ep, "_terrain_provider", lambda: real), \
             patch("urllib.request.urlopen") as urlopen:
            resp = _call_los(real, lat1=45.0, lon1=10.0, lat2=45.01, lon2=10.01)
        urlopen.assert_not_called()
        assert resp["terrain_complete"] is False
        assert resp["terrain_tiles_missing"] == ["N45E010.hgt"]


class TestGateDefaults:
    def test_no_cors_origins_means_loopback_only(self):
        """The secure default: without --cors-origins a LAN browser gets 403 here."""
        statuses = {}
        h = _make_handler("/api/los/19.7/-155.1/19.8/-155.2", client="192.168.1.20")
        h.allowed_origins = None
        h._serve_json = lambda payload, status=200: statuses.update(status=status)
        h._serve_los(["19.7", "-155.1", "19.8", "-155.2"])
        assert statuses["status"] == 403

    def test_configured_lan_is_trusted(self):
        h = _make_handler("/api/los/19.7/-155.1/19.8/-155.2", client="192.168.1.20")
        h.allowed_origins = ["http://192.168.1."]
        with patch("utils._map_node_endpoints._HAS_TERRAIN", True), \
             patch("utils._map_node_endpoints._terrain_provider", lambda: SyntheticTerrainProvider()), \
             patch("utils._map_node_endpoints._LOSAnalyzer",
                   lambda p: LOSAnalyzer(p, profile_points=PROFILE_POINTS)):
            h._serve_los(["19.7", "-155.1", "19.8", "-155.2"])
        assert "is_clear" in h._served


class TestSlotsEnvIsGuarded:
    def test_bad_env_keeps_the_default(self):
        from utils import _map_node_endpoints as ep
        with patch.dict("os.environ", {"MESHFORGE_TERRAIN_SLOTS": "two"}):
            assert ep._slots_from_env(2) == 2
        with patch.dict("os.environ", {"MESHFORGE_TERRAIN_SLOTS": "0"}):
            assert ep._slots_from_env(2) == 1
        with patch.dict("os.environ", {"MESHFORGE_TERRAIN_SLOTS": "4"}):
            assert ep._slots_from_env(2) == 4
