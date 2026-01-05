"""
RF utility function tests for MeshForge.

Run with: python3 -m pytest tests/test_rf_utils.py -v
Or: python3 tests/test_rf_utils.py
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.rf import haversine_distance, fresnel_radius, free_space_path_loss, earth_bulge


class TestHaversineDistance:
    """Test haversine distance calculations."""

    def test_hilo_to_honolulu(self):
        """Hilo to Honolulu should be ~337 km."""
        dist = haversine_distance(19.7297, -155.09, 21.3069, -157.8583)
        assert 335_000 < dist < 340_000  # meters

    def test_same_point(self):
        """Same point should return 0."""
        dist = haversine_distance(37.7749, -122.4194, 37.7749, -122.4194)
        assert dist == 0

    def test_short_distance(self):
        """Short distance ~1km accuracy."""
        # ~1km apart
        dist = haversine_distance(37.7749, -122.4194, 37.7839, -122.4094)
        assert 1000 < dist < 1500

    def test_antipodal_points(self):
        """Opposite sides of Earth ~20,000 km."""
        dist = haversine_distance(0, 0, 0, 180)
        assert 20_000_000 < dist < 20_100_000


class TestFresnelRadius:
    """Test Fresnel zone radius calculations."""

    def test_915mhz_10km(self):
        """915 MHz at 10km should be ~29m radius."""
        radius = fresnel_radius(10, 0.915)
        assert 27 < radius < 30

    def test_433mhz_10km(self):
        """Lower frequency = larger Fresnel zone."""
        radius_433 = fresnel_radius(10, 0.433)
        radius_915 = fresnel_radius(10, 0.915)
        assert radius_433 > radius_915

    def test_longer_distance(self):
        """Longer distance = larger Fresnel zone."""
        radius_10km = fresnel_radius(10, 0.915)
        radius_50km = fresnel_radius(50, 0.915)
        assert radius_50km > radius_10km


class TestFreeSpacePathLoss:
    """Test FSPL calculations."""

    def test_1km_915mhz(self):
        """1km at 915 MHz should be ~92 dB."""
        fspl = free_space_path_loss(1000, 915)
        assert 90 < fspl < 94

    def test_10km_915mhz(self):
        """10km at 915 MHz should be ~112 dB."""
        fspl = free_space_path_loss(10000, 915)
        assert 110 < fspl < 114

    def test_distance_doubles_adds_6db(self):
        """Doubling distance adds ~6 dB."""
        fspl_1km = free_space_path_loss(1000, 915)
        fspl_2km = free_space_path_loss(2000, 915)
        diff = fspl_2km - fspl_1km
        assert 5.5 < diff < 6.5


class TestEarthBulge:
    """Test Earth bulge calculations."""

    def test_10km(self):
        """10km path should have ~1.5m bulge."""
        bulge = earth_bulge(10000)
        assert 1.4 < bulge < 1.6

    def test_50km(self):
        """50km path should have ~37m bulge (scales with d^2)."""
        bulge = earth_bulge(50000)
        assert 35 < bulge < 40

    def test_short_distance(self):
        """1km should have negligible bulge."""
        bulge = earth_bulge(1000)
        assert bulge < 0.1


def run_tests():
    """Run all tests without pytest."""
    import traceback

    test_classes = [
        TestHaversineDistance,
        TestFresnelRadius,
        TestFreeSpacePathLoss,
        TestEarthBulge,
    ]

    total = 0
    passed = 0
    failed = 0

    for test_class in test_classes:
        print(f"\n{test_class.__name__}")
        print("-" * 40)

        instance = test_class()
        for name in dir(instance):
            if name.startswith("test_"):
                total += 1
                try:
                    getattr(instance, name)()
                    print(f"  PASS: {name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL: {name}")
                    print(f"        {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR: {name}")
                    traceback.print_exc()
                    failed += 1

    print("\n" + "=" * 40)
    print(f"Total: {total} | Passed: {passed} | Failed: {failed}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
