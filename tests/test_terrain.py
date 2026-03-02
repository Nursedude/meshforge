"""
Tests for terrain elevation and LOS analysis (src/utils/terrain.py).

Covers:
- TerrainProvider ABC
- FlatTerrainProvider
- SyntheticTerrainProvider
- SRTMProvider (mocked I/O)
- LOSResult
- LOSAnalyzer.analyze()
- LOSAnalyzer.coverage_grid()
"""

import math
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.terrain import (
    FlatTerrainProvider,
    LOSAnalyzer,
    LOSResult,
    SRTMProvider,
    SyntheticTerrainProvider,
    TerrainProvider,
)


# ============================================================================
# FlatTerrainProvider
# ============================================================================


class TestFlatTerrainProvider:
    """Tests for constant-elevation terrain provider."""

    def test_default_elevation_zero(self):
        p = FlatTerrainProvider()
        assert p.get_elevation(0, 0) == 0.0

    def test_custom_elevation(self):
        p = FlatTerrainProvider(elevation=500.0)
        assert p.get_elevation(45.0, -120.0) == 500.0

    def test_negative_elevation(self):
        p = FlatTerrainProvider(elevation=-10.0)
        assert p.get_elevation(0, 0) == -10.0

    def test_get_profile_flat(self):
        p = FlatTerrainProvider(elevation=100.0)
        profile = p.get_profile(0, 0, 1, 1, num_points=10)
        assert len(profile) == 10
        assert all(e == 100.0 for e in profile)

    def test_profile_single_point(self):
        p = FlatTerrainProvider(elevation=50.0)
        profile = p.get_profile(0, 0, 1, 1, num_points=1)
        assert len(profile) == 1
        assert profile[0] == 50.0

    def test_is_terrain_provider(self):
        p = FlatTerrainProvider()
        assert isinstance(p, TerrainProvider)


# ============================================================================
# SyntheticTerrainProvider
# ============================================================================


class TestSyntheticTerrainProvider:
    """Tests for ridge-pattern terrain generator."""

    def test_base_elevation_minimum(self):
        p = SyntheticTerrainProvider(base_elevation=100.0, ridge_height=50.0)
        elev = p.get_elevation(0, 0)
        assert elev >= 100.0

    def test_ridge_height_maximum(self):
        p = SyntheticTerrainProvider(base_elevation=100.0, ridge_height=50.0)
        # Sample many points to find max
        elevations = [p.get_elevation(i * 0.001, 0) for i in range(100)]
        assert max(elevations) <= 150.01  # Small float tolerance

    def test_deterministic(self):
        p = SyntheticTerrainProvider()
        e1 = p.get_elevation(19.7, -155.08)
        e2 = p.get_elevation(19.7, -155.08)
        assert e1 == e2

    def test_profile_varies(self):
        p = SyntheticTerrainProvider(ridge_spacing_deg=0.001)
        profile = p.get_profile(0, 0, 0.1, 0.1, num_points=50)
        assert len(set(profile)) > 1  # Not all the same

    def test_is_terrain_provider(self):
        p = SyntheticTerrainProvider()
        assert isinstance(p, TerrainProvider)


# ============================================================================
# SRTMProvider (mocked I/O)
# ============================================================================


class TestSRTMProvider:
    """Tests for SRTM elevation data provider (no network access)."""

    def test_tile_name_north_east(self):
        p = SRTMProvider(auto_download=False)
        assert p._get_tile_name(19.7, 155.08) == "N19E155.hgt"

    def test_tile_name_south_west(self):
        p = SRTMProvider(auto_download=False)
        assert p._get_tile_name(-33.9, -18.5) == "S34W019.hgt"

    def test_tile_name_north_west(self):
        p = SRTMProvider(auto_download=False)
        assert p._get_tile_name(21.3, -157.86) == "N21W158.hgt"

    def test_tile_name_equator_prime(self):
        p = SRTMProvider(auto_download=False)
        assert p._get_tile_name(0.5, 0.5) == "N00E000.hgt"

    def test_elevation_no_data_returns_zero(self):
        """Without tiles, elevation should return 0.0."""
        p = SRTMProvider(auto_download=False)
        assert p.get_elevation(89.99, 179.99) == 0.0

    def test_interpolation_valid_srtm3_tile(self):
        """Bilinear interpolation of a fake SRTM3 tile."""
        p = SRTMProvider(auto_download=False)
        samples = 1201  # SRTM3
        data = bytearray(samples * samples * 2)
        for i in range(samples * samples):
            struct.pack_into('>h', data, i * 2, 500)

        result = p._interpolate(bytes(data), 21.5, -157.5)
        assert abs(result - 500.0) < 1.0

    def test_interpolation_srtm1_tile(self):
        """Bilinear interpolation of a fake SRTM1 tile."""
        p = SRTMProvider(auto_download=False)
        samples = 3601  # SRTM1
        data = bytearray(samples * samples * 2)
        for i in range(samples * samples):
            struct.pack_into('>h', data, i * 2, 250)

        result = p._interpolate(bytes(data), 21.5, -157.5)
        assert abs(result - 250.0) < 1.0

    def test_interpolation_void_value(self):
        """SRTM void (-32768) should be treated as 0."""
        p = SRTMProvider(auto_download=False)
        samples = 1201
        data = bytearray(samples * samples * 2)
        for i in range(samples * samples):
            struct.pack_into('>h', data, i * 2, -32768)

        result = p._interpolate(bytes(data), 21.5, -157.5)
        assert result == 0.0

    def test_cached_tiles_empty(self, tmp_path):
        p = SRTMProvider(cache_dir=tmp_path, auto_download=False)
        assert p.get_cached_tiles() == []

    def test_cache_size_empty(self, tmp_path):
        p = SRTMProvider(cache_dir=tmp_path, auto_download=False)
        assert p.get_cache_size_mb() == 0.0

    def test_cache_dir_created(self, tmp_path):
        cache_dir = tmp_path / "srtm_test"
        SRTMProvider(cache_dir=cache_dir, auto_download=False)
        assert cache_dir.exists()


# ============================================================================
# LOSResult
# ============================================================================


class TestLOSResult:
    """Tests for LOSResult data structure."""

    def test_default_values(self):
        r = LOSResult()
        assert r.is_clear is True
        assert r.terrain_loss_db == 0.0
        assert r.fspl_db == 0.0
        assert r.total_loss_db == 0.0
        assert r.distance_m == 0.0
        assert r.num_obstructions == 0
        assert r.worst_obstruction_m == 0.0
        assert r.fresnel_clearance_pct == 100.0

    def test_to_dict_keys(self):
        r = LOSResult()
        d = r.to_dict()
        expected_keys = {
            "is_clear", "terrain_loss_db", "fspl_db", "total_loss_db",
            "distance_m", "num_obstructions", "worst_obstruction_m",
            "fresnel_clearance_pct", "earth_bulge_m",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_rounding(self):
        r = LOSResult()
        r.distance_m = 1234.5678
        r.fspl_db = 90.123
        d = r.to_dict()
        assert d["distance_m"] == 1234.6
        assert d["fspl_db"] == 90.1


# ============================================================================
# LOSAnalyzer
# ============================================================================


class TestLOSAnalyzer:
    """Tests for line-of-sight analysis engine."""

    def test_zero_distance(self):
        """Same point should return clear LOS immediately."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        result = analyzer.analyze(21.3, -157.86, 10, 21.3, -157.86, 10, 915.0)
        assert result.is_clear is True
        assert result.distance_m < 1

    def test_flat_terrain_clear_los(self):
        """Flat terrain with antennas should give clear LOS."""
        analyzer = LOSAnalyzer(FlatTerrainProvider(elevation=0.0))
        result = analyzer.analyze(
            21.3, -157.86, 10,   # 10m antenna
            21.31, -157.86, 10,  # 10m antenna, ~1.1km away
            915.0
        )
        assert result.is_clear is True
        assert result.terrain_loss_db == 0.0
        assert result.fspl_db > 0
        assert result.num_obstructions == 0

    def test_distance_calculation(self):
        """Distance should be reasonable for 0.01 degree offset."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        result = analyzer.analyze(21.3, -157.86, 10, 21.31, -157.86, 10, 915.0)
        # ~1.11 km for 0.01 degree latitude
        assert 1000 < result.distance_m < 1200

    def test_fspl_increases_with_distance(self):
        """FSPL should increase with greater distance."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        r1 = analyzer.analyze(0, 0, 10, 0.01, 0, 10, 915.0)
        r2 = analyzer.analyze(0, 0, 10, 0.05, 0, 10, 915.0)
        assert r2.fspl_db > r1.fspl_db

    def test_total_loss_is_sum(self):
        """Total loss = FSPL + terrain loss."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        result = analyzer.analyze(0, 0, 10, 0.01, 0, 10, 915.0)
        assert abs(result.total_loss_db - (result.fspl_db + result.terrain_loss_db)) < 0.01

    def test_elevation_profile_populated(self):
        """Elevation profile should match profile_points."""
        analyzer = LOSAnalyzer(FlatTerrainProvider(elevation=100.0), profile_points=50)
        result = analyzer.analyze(0, 0, 10, 0.01, 0, 10, 915.0)
        assert len(result.elevation_profile) == 50
        assert all(e == 100.0 for e in result.elevation_profile)

    def test_los_heights_populated(self):
        """LOS height array should be same length as profile."""
        analyzer = LOSAnalyzer(FlatTerrainProvider(), profile_points=50)
        result = analyzer.analyze(0, 0, 10, 0.01, 0, 10, 915.0)
        assert len(result.los_heights) == 50

    def test_ridge_causes_obstruction(self):
        """High ridges between endpoints should obstruct LOS."""
        provider = SyntheticTerrainProvider(
            base_elevation=0.0,
            ridge_height=200.0,
            ridge_spacing_deg=0.005
        )
        analyzer = LOSAnalyzer(provider, profile_points=200)
        result = analyzer.analyze(
            0.0, 0.0, 2,      # 2m antenna
            0.02, 0.02, 2,    # 2m antenna, ~3km away
            915.0
        )
        assert result.num_obstructions > 0
        assert result.terrain_loss_db > 0

    def test_high_antennas_clear_ridges(self):
        """Very high antennas should clear moderate ridges."""
        provider = SyntheticTerrainProvider(
            base_elevation=0.0,
            ridge_height=10.0,
            ridge_spacing_deg=0.01
        )
        analyzer = LOSAnalyzer(provider, profile_points=100)
        result = analyzer.analyze(
            0.0, 0.0, 100,    # 100m antenna (tower)
            0.01, 0.0, 100,   # 100m antenna
            915.0
        )
        assert result.num_obstructions == 0

    def test_fresnel_clearance_flat(self):
        """Flat terrain with high antennas should have 100% Fresnel clearance."""
        analyzer = LOSAnalyzer(FlatTerrainProvider(), profile_points=100)
        result = analyzer.analyze(0, 0, 50, 0.01, 0, 50, 915.0)
        assert result.fresnel_clearance_pct == 100.0

    def test_earth_bulge_increases_with_distance(self):
        """Earth bulge should be larger for longer paths."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        r1 = analyzer.analyze(0, 0, 10, 0.01, 0, 10, 915.0)
        r2 = analyzer.analyze(0, 0, 10, 0.1, 0, 10, 915.0)
        assert r2.earth_bulge_m > r1.earth_bulge_m


# ============================================================================
# Coverage Grid
# ============================================================================


class TestCoverageGrid:
    """Tests for radial coverage grid computation."""

    def test_grid_returns_correct_count(self):
        """36 bearings * resolution radial steps."""
        analyzer = LOSAnalyzer(FlatTerrainProvider(), profile_points=20)
        points = analyzer.coverage_grid(
            21.3, -157.86, 10,
            radius_km=1.0, freq_mhz=915.0, resolution=5
        )
        assert len(points) == 36 * 5  # 180

    def test_grid_point_structure(self):
        """Each point should have required keys."""
        analyzer = LOSAnalyzer(FlatTerrainProvider(), profile_points=20)
        points = analyzer.coverage_grid(
            21.3, -157.86, 10,
            radius_km=1.0, freq_mhz=915.0, resolution=2
        )
        pt = points[0]
        required_keys = {"lat", "lon", "bearing", "distance_m", "is_clear",
                         "total_loss_db", "terrain_loss_db", "fresnel_clearance_pct"}
        assert required_keys.issubset(set(pt.keys()))

    def test_grid_flat_terrain_all_clear(self):
        """On flat terrain with high antennas, short-range points should be clear."""
        # 50m antennas provide adequate 60% Fresnel clearance at short range
        analyzer = LOSAnalyzer(FlatTerrainProvider(), profile_points=20)
        points = analyzer.coverage_grid(
            21.3, -157.86, 50,
            radius_km=0.5, freq_mhz=915.0, resolution=3
        )
        clear_count = sum(1 for p in points if p["is_clear"])
        assert clear_count == len(points)

    def test_grid_bearings_cover_360(self):
        """Should cover all 36 bearing directions (every 10 degrees)."""
        analyzer = LOSAnalyzer(FlatTerrainProvider(), profile_points=10)
        points = analyzer.coverage_grid(
            0, 0, 10,
            radius_km=1.0, freq_mhz=915.0, resolution=1
        )
        bearings = sorted(set(p["bearing"] for p in points))
        assert len(bearings) == 36
        assert bearings[0] == 0.0
        assert bearings[-1] == 350.0

    def test_grid_distance_increases(self):
        """Points at higher resolution indices should be farther from center."""
        analyzer = LOSAnalyzer(FlatTerrainProvider(), profile_points=10)
        points = analyzer.coverage_grid(
            0, 0, 10,
            radius_km=5.0, freq_mhz=915.0, resolution=5
        )
        # Filter to bearing 0 (north)
        north = [p for p in points if p["bearing"] == 0.0]
        north.sort(key=lambda p: p["distance_m"])
        assert len(north) == 5
        for i in range(1, len(north)):
            assert north[i]["distance_m"] > north[i - 1]["distance_m"]


# ============================================================================
# Destination Point Helper
# ============================================================================


class TestDestinationPoint:
    """Tests for geographic destination point calculation."""

    def test_north_1km(self):
        """1km north from equator should be ~0.009 degrees."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        lat2, lon2 = analyzer._destination_point(0.0, 0.0, 0.0, 1000.0)
        assert 0.008 < lat2 < 0.01
        assert abs(lon2) < 0.001

    def test_east_1km(self):
        """1km east from equator should shift longitude."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        lat2, lon2 = analyzer._destination_point(0.0, 0.0, 90.0, 1000.0)
        assert abs(lat2) < 0.001
        assert 0.008 < lon2 < 0.01

    def test_south_1km(self):
        """1km south should produce negative latitude change."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        lat2, lon2 = analyzer._destination_point(0.0, 0.0, 180.0, 1000.0)
        assert lat2 < 0
        assert abs(lon2) < 0.001

    def test_zero_distance(self):
        """Zero distance should return the same point."""
        analyzer = LOSAnalyzer(FlatTerrainProvider())
        lat2, lon2 = analyzer._destination_point(21.3, -157.86, 45.0, 0.0)
        assert abs(lat2 - 21.3) < 0.0001
        assert abs(lon2 - (-157.86)) < 0.0001
