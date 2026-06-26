"""Tests for the Issue #71 regional-slice precompute (added 2026-06-26).

Surfaced when the cloud-map publish was decoupled to the map-owning
federator: the
``geojson?region=hawaii`` filter is ~6.5 s clean but the GIL-heavy
region-filter + json/gzip stacks distinct hot keys on one ``_build_lock``,
so a coincident request burst could push the 90 s cloud-push budget. The fix
keeps the configured region slice WARM so its build never lands on the
request path. Three layers pinned here:

1. ``ResponseByteCache.prime`` — build under the single-flight lock and store
   with a custom (long) TTL; surfaces ``warmed_count``.
2. ``build_geojson_response`` — the extracted single-source builder the handler
   and the warmer share, so warmed bytes are byte-identical to a live request.
3. ``MapDataCollector._warm_geojson_regions`` — opt-in (empty list = no-op),
   skips unknown regions, primes the request-matching cache key, and a
   subsequent request hits without rebuilding.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest

from utils._response_byte_cache import ResponseByteCache
from utils._map_node_endpoints import build_geojson_response
from utils.region_presets import REGION_PRESETS
from utils.map_data_collector import MapDataCollector


# hawaii bbox = [south, west, north, east] = [18.5, -161.0, 22.5, -154.0]
_IN_HI = {  # lat 19.5, lon -155.5 — inside
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-155.5, 19.5, 0]},
    "properties": {"id": "in1", "name": "in1", "network": "meshtastic"},
}
_OUT_HI = {  # lat 40.0, lon -100.0 — outside
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-100.0, 40.0, 0]},
    "properties": {"id": "out1", "name": "out1", "network": "meshtastic"},
}


def _fc(*features):
    return {"type": "FeatureCollection",
            "features": list(features),
            "properties": {"nodes_with_position": len(features)}}


# --------------------------------------------------------------------------
# 1. ResponseByteCache.prime
# --------------------------------------------------------------------------
class TestPrime:
    def test_prime_stores_and_counts(self):
        cache = ResponseByteCache(ttl_s=5.0)
        raw, gz = cache.prime("k", lambda: (b"RAW", b"GZ"), ttl_s=100.0)
        assert (raw, gz) == (b"RAW", b"GZ")
        assert cache.get("k") == (b"RAW", b"GZ")
        assert cache.stats()["warmed_count"] == 1

    def test_prime_rejects_nonpositive_ttl(self):
        cache = ResponseByteCache(ttl_s=5.0)
        with pytest.raises(ValueError):
            cache.prime("k", lambda: (b"x", None), ttl_s=0)

    def test_primed_entry_served_without_rebuild(self):
        """A request (get_or_build) for a primed key must NOT rebuild."""
        cache = ResponseByteCache(ttl_s=5.0)
        cache.prime("k", lambda: (b"WARM", None), ttl_s=100.0)
        built = {"n": 0}

        def _req_build():
            built["n"] += 1
            return b"COLD", None

        raw, gz, was_built = cache.get_or_build("k", _req_build)
        assert raw == b"WARM"
        assert was_built is False
        assert built["n"] == 0  # request never paid the build cost

    def test_prime_custom_ttl_outlives_default(self):
        """The warmer's long TTL must survive past the short request-path TTL."""
        cache = ResponseByteCache(ttl_s=5.0)
        t = [1000.0]
        with patch("utils._response_byte_cache.time.monotonic", lambda: t[0]):
            cache.prime("k", lambda: (b"W", None), ttl_s=100.0)
            t[0] = 1000.0 + 50.0  # past the 5 s default, within the 100 s prime
            assert cache.get("k") == (b"W", None)
            t[0] = 1000.0 + 101.0  # past the prime TTL
            assert cache.get("k") is None


# --------------------------------------------------------------------------
# 2. build_geojson_response (extracted single-source builder)
# --------------------------------------------------------------------------
class _StubCollector:
    def __init__(self, fc):
        self._fc = fc

    def collect(self):
        return self._fc


class TestBuildGeojsonResponse:
    def test_region_filter_drops_outside_features(self):
        col = _StubCollector(_fc(_IN_HI, _OUT_HI))
        raw, gz = build_geojson_response(col, "hawaii", None, None, 10 * 1024)
        body = json.loads(raw.decode())
        ids = {f["properties"]["id"] for f in body["features"]}
        assert ids == {"in1"}
        assert body["properties"].get("bbox_filtered") is True

    def test_no_region_is_passthrough(self):
        col = _StubCollector(_fc(_IN_HI, _OUT_HI))
        raw, _ = build_geojson_response(col, None, None, None, 10 * 1024)
        body = json.loads(raw.decode())
        assert {f["properties"]["id"] for f in body["features"]} == {"in1", "out1"}

    def test_handler_and_warmer_share_one_builder(self):
        """Byte-identical: two calls with the same inputs produce the same raw."""
        col = _StubCollector(_fc(_IN_HI, _OUT_HI))
        raw1, _ = build_geojson_response(col, "hawaii", None, None, 10 * 1024)
        raw2, _ = build_geojson_response(col, "hawaii", None, None, 10 * 1024)
        assert raw1 == raw2


# --------------------------------------------------------------------------
# 3. MapDataCollector._warm_geojson_regions
# --------------------------------------------------------------------------
def _make_collector(tmp_path, warm_regions):
    fake_home = tmp_path / "home"
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "map_settings.json").write_text(
        json.dumps({"geojson_warm_regions": warm_regions})
    )
    with patch("utils.map_data_collector.get_real_user_home",
               return_value=fake_home):
        c = MapDataCollector(
            cache_dir=tmp_path / "cache",
            enable_history=False,
            config_dir=cfg_dir,
        )
    return c


class TestWarmGeojsonRegions:
    def test_warms_configured_region(self, tmp_path):
        c = _make_collector(tmp_path, ["hawaii"])
        with patch.object(c, "collect", return_value=_fc(_IN_HI, _OUT_HI)):
            c._warm_geojson_regions()
        # Cache key must match _serve_geojson's (bbox, region, preset).
        hit = c._geojson_response_cache.get((None, "hawaii", None))
        assert hit is not None
        body = json.loads(hit[0].decode())
        assert {f["properties"]["id"] for f in body["features"]} == {"in1"}
        assert c._geojson_response_cache.stats()["warmed_count"] == 1

    def test_empty_list_is_noop(self, tmp_path):
        c = _make_collector(tmp_path, [])
        with patch.object(c, "collect", return_value=_fc(_IN_HI)) as m:
            c._warm_geojson_regions()
        assert m.call_count == 0  # never even built
        assert c._geojson_response_cache.stats()["warmed_count"] == 0
        assert c._geojson_response_cache.get((None, "hawaii", None)) is None

    def test_unknown_region_skipped_without_crash(self, tmp_path):
        c = _make_collector(tmp_path, ["atlantis"])
        with patch.object(c, "collect", return_value=_fc(_IN_HI)):
            c._warm_geojson_regions()  # must not raise
        assert c._geojson_response_cache.stats()["warmed_count"] == 0
        assert c._geojson_response_cache.get((None, "atlantis", None)) is None

    def test_warmed_slice_served_to_request_path(self, tmp_path):
        """After warming, the request build path hits the warm entry."""
        c = _make_collector(tmp_path, ["hawaii"])
        with patch.object(c, "collect", return_value=_fc(_IN_HI, _OUT_HI)):
            c._warm_geojson_regions()
        built = {"n": 0}

        def _req_build():
            built["n"] += 1
            return b"COLD", None

        _raw, _gz, was_built = c._geojson_response_cache.get_or_build(
            (None, "hawaii", None), _req_build
        )
        assert was_built is False
        assert built["n"] == 0

    def test_periodic_loop_invokes_warmer(self, tmp_path):
        """The refresh loop calls the warmer after each collect tick."""
        c = _make_collector(tmp_path, ["hawaii"])
        warmed = threading.Event()
        with patch.object(c, "_collect_locked"), \
             patch.object(c, "_warm_geojson_regions", side_effect=warmed.set):
            c.start_periodic_refresh(interval_seconds=0.05)
            try:
                assert warmed.wait(timeout=3.0), "warmer not invoked by loop"
            finally:
                c.stop_periodic_refresh()
