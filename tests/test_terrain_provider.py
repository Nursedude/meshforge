"""SRTMProvider contract tests — born 2026-09-14 from the frontier security pass.

Every test here is the drill that found (or refuted) a defect, pinned:

* a HALF-WRITTEN tile used to be read as terrain (elevations off by
  thousands of metres, ``has_elevation()`` True) — it must read MISSING and
  be quarantined loudly;
* downloads were written non-atomically — a reader must see whole or nothing;
* the request path must never download; the warm path is bounded by count
  and by free disk;
* the tiles carry bathymetry, and the sea SURFACE is the RF ground.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from utils.terrain import (
    FlatTerrainProvider,
    LOSAnalyzer,
    SRTMProvider,
    TileCorrupt,
)

SRTM3_LEN = SRTMProvider.SRTM3_SAMPLES ** 2 * 2


def _tile_bytes(value: int, length: int = SRTM3_LEN) -> bytes:
    """A whole SRTM3 tile where every sample is ``value`` (big-endian int16)."""
    return value.to_bytes(2, "big", signed=True) * (length // 2)


@pytest.fixture
def cache(tmp_path) -> Path:
    return tmp_path / "srtm"


class TestPartialTileIsNeverTerrain:
    def test_truncated_tile_reads_missing_and_is_quarantined(self, cache):
        cache.mkdir()
        (cache / "N19W156.hgt").write_bytes(_tile_bytes(1000)[:12_000_000 % SRTM3_LEN or 1234])
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        assert prov.has_elevation(19.5, -155.5) is False
        assert prov.get_elevation(19.5, -155.5) == 0.0
        assert not (cache / "N19W156.hgt").exists(), "must not stay readable as a tile"
        assert list(cache.glob("N19W156.hgt.corrupt-*")), "quarantine leaves a witness"

    def test_interpolate_refuses_unknown_length(self, cache):
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        with pytest.raises(TileCorrupt):
            prov._interpolate(b"\x00" * 100, 19.5, -155.5)

    def test_whole_tile_reads_its_value(self, cache):
        cache.mkdir()
        (cache / "N19W156.hgt").write_bytes(_tile_bytes(1234))
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        assert prov.has_elevation(19.5, -155.5) is True
        assert prov.get_elevation(19.5, -155.5) == pytest.approx(1234.0)


class TestAtomicWrite:
    def test_download_lands_whole_with_no_temp_left(self, cache):
        prov = SRTMProvider(cache_dir=cache, auto_download=True)
        with patch.object(prov, "_download_tile", return_value=_tile_bytes(7)):
            assert prov.get_elevation(19.5, -155.5) == pytest.approx(7.0)
        files = sorted(p.name for p in cache.iterdir())
        assert files == ["N19W156.hgt"], files
        assert (cache / "N19W156.hgt").stat().st_size == SRTM3_LEN

    def test_short_download_is_refused_not_written(self, cache):
        prov = SRTMProvider(cache_dir=cache, auto_download=True)
        with patch.object(prov, "_download_tile", return_value=b"\x00" * 500):
            assert prov.has_elevation(19.5, -155.5) is False
        assert not list(cache.iterdir())


class TestRequestPathNeverDownloads:
    def test_auto_download_false_does_not_touch_the_network(self, cache):
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        with patch.object(prov, "_download_tile") as dl:
            assert prov.has_elevation(19.5, -155.5) is False
            assert prov.get_elevation(19.5, -155.5) == 0.0
        dl.assert_not_called()

    def test_miss_is_remembered_then_rechecked(self, cache):
        """A miss must not stat the disk 172,800 times, and must not be sticky."""
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        assert prov.has_elevation(19.5, -155.5) is False
        cache.mkdir(exist_ok=True)
        (cache / "N19W156.hgt").write_bytes(_tile_bytes(5))
        assert prov.has_elevation(19.5, -155.5) is False, "inside the TTL the miss stands"
        prov._missing_until.clear()
        assert prov.has_elevation(19.5, -155.5) is True, "after the TTL the warm tile is seen"

    def test_impossible_coordinate_never_reaches_the_network(self, cache):
        prov = SRTMProvider(cache_dir=cache, auto_download=True)
        with patch("urllib.request.urlopen") as urlopen:
            assert prov._download_tile(95.0, 0.0) is None
        urlopen.assert_not_called()


class TestMemoryBound:
    def test_lru_evicts_beyond_max(self, cache):
        cache.mkdir()
        for name in ("N19W156.hgt", "N19W155.hgt", "N20W156.hgt"):
            (cache / name).write_bytes(_tile_bytes(1))
        prov = SRTMProvider(cache_dir=cache, auto_download=False, max_tiles_in_memory=2)
        prov.get_elevation(19.5, -155.5)
        prov.get_elevation(19.5, -154.5)
        prov.get_elevation(20.5, -155.5)
        assert len(prov._tile_cache) == 2
        assert "N19W156.hgt" not in prov._tile_cache


class TestWarmPath:
    def _prov(self, cache):
        return SRTMProvider(cache_dir=cache, auto_download=False)

    def test_wants_node_tiles_plus_neighbours_deduped(self, cache):
        prov = self._prov(cache)
        with patch.object(prov, "_download_tile", return_value=_tile_bytes(1)):
            s = prov.warm_tiles([(19.7, -155.1), (19.8, -155.2)], max_tiles=100)
        assert len(s["wanted"]) == 9, s["wanted"]
        assert "N19W156.hgt" in s["wanted"] and "N20W155.hgt" in s["wanted"]
        assert len(s["downloaded"]) == 9 and not s["failed"]

    def test_budget_bounds_downloads(self, cache):
        prov = self._prov(cache)
        with patch.object(prov, "_download_tile", return_value=_tile_bytes(1)) as dl:
            s = prov.warm_tiles([(19.7, -155.1)], max_tiles=2)
        assert dl.call_count == 2
        assert len(s["downloaded"]) == 2 and len(s["skipped_budget"]) == 7

    def test_zero_budget_disables(self, cache):
        prov = self._prov(cache)
        with patch.object(prov, "_download_tile") as dl:
            s = prov.warm_tiles([(19.7, -155.1)], max_tiles=0)
        dl.assert_not_called()
        assert len(s["skipped_budget"]) == 9

    def test_disk_floor_refuses(self, cache):
        prov = self._prov(cache)
        with patch.object(prov, "_download_tile") as dl, \
                patch("utils.terrain.shutil.disk_usage") as du:
            du.return_value = type("U", (), {"free": 10})()
            s = prov.warm_tiles([(19.7, -155.1)], max_tiles=9, min_free_bytes=1 << 30)
        dl.assert_not_called()
        assert len(s["skipped_disk"]) == 9

    def test_cached_tiles_are_not_refetched(self, cache):
        cache.mkdir()
        (cache / "N19W156.hgt").write_bytes(_tile_bytes(1))
        prov = self._prov(cache)
        with patch.object(prov, "_download_tile", return_value=_tile_bytes(1)) as dl:
            s = prov.warm_tiles([(19.7, -155.1)], max_tiles=100, neighbors=False)
        dl.assert_not_called()
        assert s["cached"] == ["N19W156.hgt"]

    def test_garbage_points_are_ignored(self, cache):
        prov = self._prov(cache)
        with patch.object(prov, "_download_tile") as dl:
            s = prov.warm_tiles([("x", "y"), (float("nan"), 1), (95, 0)], max_tiles=9)
        dl.assert_not_called()
        assert s["wanted"] == []

    def test_missing_tiles_for_names_what_is_absent(self, cache):
        cache.mkdir()
        (cache / "N19W156.hgt").write_bytes(_tile_bytes(1))
        prov = self._prov(cache)
        assert prov.missing_tiles_for([(19.5, -155.5), (20.5, -155.5)]) == ["N20W156.hgt"]


class TestSeaSurfaceIsTheGround:
    def test_bathymetry_is_floored_at_sea_level(self, cache):
        cache.mkdir()
        (cache / "N19W156.hgt").write_bytes(_tile_bytes(-2500))
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        assert prov.has_elevation(19.5, -155.5) is True
        assert prov.get_elevation(19.5, -155.5) == 0.0
        raw = SRTMProvider(cache_dir=cache, auto_download=False, sea_level_floor=False)
        assert raw.get_elevation(19.5, -155.5) == pytest.approx(-2500.0)

    def test_over_water_verdict_matches_a_flat_sea(self, cache):
        """76 km over the Alenuihaha channel, 100 m antennas.

        With the sea floor as ground the live endpoint said clear / 100 %;
        with the sea surface the module's own geometry says NOT clear.
        """
        cache.mkdir()
        for name in ("N19W156.hgt", "N20W157.hgt", "N20W156.hgt", "N19W157.hgt"):
            (cache / name).write_bytes(_tile_bytes(-2500))
        floored = LOSAnalyzer(SRTMProvider(cache_dir=cache, auto_download=False))
        flat = LOSAnalyzer(FlatTerrainProvider(0.0))
        a = floored.analyze(19.9, -155.95, 100, 20.5, -156.3, 100, 906)
        b = flat.analyze(19.9, -155.95, 100, 20.5, -156.3, 100, 906)
        assert a.terrain_complete
        assert a.is_clear == b.is_clear is False
        assert a.fresnel_clearance_pct == pytest.approx(b.fresnel_clearance_pct)


# ── added after the 2026-09-14 self-review of the fix commit ─────────────

def _two_value_tile(left: int, right: int) -> bytes:
    """SRTM3 tile whose even columns are ``left`` and odd columns ``right``."""
    row = (left.to_bytes(2, "big", signed=True) + right.to_bytes(2, "big", signed=True))
    row = row * (SRTMProvider.SRTM3_SAMPLES // 2) + left.to_bytes(2, "big", signed=True)
    return row * SRTMProvider.SRTM3_SAMPLES


class TestFloorIsPerSample:
    """A cliff beside the sea must stay a cliff, not blend toward 0."""

    def test_cliff_next_to_seafloor_keeps_its_height(self, cache):
        cache.mkdir()
        (cache / "N19W156.hgt").write_bytes(_two_value_tile(300, -3000))
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        step = 1.0 / (SRTMProvider.SRTM3_SAMPLES - 1)
        on_land = prov.get_elevation(19.5, -156.0 + 0 * step)
        near_shore = prov.get_elevation(19.5, -156.0 + 0.9 * step)
        assert on_land == pytest.approx(300.0)
        # honest per-sample floor: 0.1*300 + 0.9*0 = 30, never 0 (the post-blend floor)
        assert near_shore == pytest.approx(30.0, abs=1.0)
        assert near_shore > 0.0


class TestCornerInverse:
    @pytest.mark.parametrize("name,corner", [
        ("N19W156.hgt", (19, -156)), ("S01W001.hgt", (-1, -1)),
        ("S90E179.hgt", (-90, 179)), ("N00E000.hgt", (0, 0)),
    ])
    def test_name_to_corner(self, name, corner):
        assert SRTMProvider._tile_name_to_corner(name) == corner
        lat, lon = corner
        assert SRTMProvider(auto_download=False, cache_dir=None)._get_tile_name(lat + 0.5, lon + 0.5) == name

    def test_warm_downloads_the_tile_it_names(self, cache):
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        with patch.object(prov, "_download_tile", return_value=_tile_bytes(1)) as dl:
            prov.warm_tiles([(-0.5, -0.5)], max_tiles=1, neighbors=False)
        dl.assert_called_once_with(-0.5, -0.5)
        assert (cache / "S01W001.hgt").exists()


class TestMissTTLUsesTheClock:
    def test_ttl_expires_by_monotonic_time(self, cache):
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        with patch("utils.terrain.time.monotonic", return_value=1000.0):
            assert prov.has_elevation(19.5, -155.5) is False
        cache.mkdir(exist_ok=True)
        (cache / "N19W156.hgt").write_bytes(_tile_bytes(5))
        with patch("utils.terrain.time.monotonic", return_value=1000.0 + prov.MISSING_TTL_S - 1):
            assert prov.has_elevation(19.5, -155.5) is False
        with patch("utils.terrain.time.monotonic", return_value=1000.0 + prov.MISSING_TTL_S + 1):
            assert prov.has_elevation(19.5, -155.5) is True


class TestGzipAndDiskFailures:
    def test_short_gz_is_quarantined(self, cache):
        import gzip
        cache.mkdir()
        (cache / "N19W156.hgt.gz").write_bytes(gzip.compress(b"\x00" * 100))
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        assert prov.has_elevation(19.5, -155.5) is False
        assert list(cache.glob("N19W156.hgt.gz.corrupt-*"))

    def test_unreadable_tile_reads_missing_not_500(self, cache):
        cache.mkdir()
        (cache / "N19W156.hgt").write_bytes(_tile_bytes(1))
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        with patch("pathlib.Path.read_bytes", side_effect=OSError(5, "EIO")):
            assert prov.has_elevation(19.5, -155.5) is False
            assert prov.get_elevation(19.5, -155.5) == 0.0

    def test_oversized_download_is_refused(self, cache):
        import gzip, io
        prov = SRTMProvider(cache_dir=cache, auto_download=True)
        big = gzip.compress(b"\x00" * (SRTMProvider._VALID_LENGTHS[0] + 10))

        class _Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with patch("urllib.request.urlopen", return_value=_Resp(big)):
            assert prov._download_tile(19.5, -155.5) is None


class TestTrackedNamesAreBounded:
    def test_missing_dict_is_pruned(self, cache):
        prov = SRTMProvider(cache_dir=cache, auto_download=False)
        prov.MAX_TRACKED_NAMES = 5
        with patch("utils.terrain.time.monotonic", return_value=0.0):
            for i in range(8):
                prov._forget(f"N{i:02d}E000.hgt", 10.0)
        assert len(prov._missing_until) <= 6
