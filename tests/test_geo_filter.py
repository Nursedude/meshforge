"""Tests for utils.geo_filter — bbox math and settings precedence."""

import json
from unittest.mock import patch

import pytest

from utils.geo_filter import (
    derive_default_bbox,
    is_within_bbox,
    load_external_bulk_bbox,
)


# ---------- is_within_bbox ----------

class TestIsWithinBbox:
    def setup_method(self):
        self.bbox = {
            "lat_min": 19.0, "lat_max": 22.0,
            "lon_min": -158.0, "lon_max": -154.0,
        }

    def test_center_inside(self):
        assert is_within_bbox(20.5, -156.0, self.bbox) is True

    def test_corner_inclusive(self):
        for corner in [
            (19.0, -158.0), (22.0, -154.0),
            (19.0, -154.0), (22.0, -158.0),
        ]:
            assert is_within_bbox(*corner, self.bbox) is True

    def test_outside_north(self):
        assert is_within_bbox(22.001, -156.0, self.bbox) is False

    def test_outside_south(self):
        assert is_within_bbox(18.999, -156.0, self.bbox) is False

    def test_outside_east(self):
        assert is_within_bbox(20.0, -153.999, self.bbox) is False

    def test_outside_west(self):
        assert is_within_bbox(20.0, -158.001, self.bbox) is False

    def test_none_lat(self):
        assert is_within_bbox(None, -156.0, self.bbox) is False

    def test_none_lon(self):
        assert is_within_bbox(20.0, None, self.bbox) is False

    def test_none_bbox(self):
        assert is_within_bbox(20.0, -156.0, None) is False

    def test_nan_lat(self):
        assert is_within_bbox(float("nan"), -156.0, self.bbox) is False

    def test_inf_lon(self):
        assert is_within_bbox(20.0, float("inf"), self.bbox) is False

    def test_malformed_bbox_missing_key(self):
        bad = {"lat_min": 19.0, "lat_max": 22.0, "lon_min": -158.0}
        assert is_within_bbox(20.0, -156.0, bad) is False

    def test_antimeridian_wrap_inside(self):
        # bbox crosses 180°: lon_min=170, lon_max=-170 → matches lon in [170,180] ∪ [-180,-170]
        bbox = {"lat_min": -10, "lat_max": 10, "lon_min": 170, "lon_max": -170}
        assert is_within_bbox(0, 175, bbox) is True
        assert is_within_bbox(0, -175, bbox) is True

    def test_antimeridian_wrap_outside(self):
        bbox = {"lat_min": -10, "lat_max": 10, "lon_min": 170, "lon_max": -170}
        # Inside [-170, 170] complement → outside the bbox
        assert is_within_bbox(0, 0, bbox) is False
        assert is_within_bbox(0, 169, bbox) is False


# ---------- derive_default_bbox ----------

class TestDeriveDefaultBbox:
    def test_equator_500km(self):
        bbox = derive_default_bbox(0.0, 0.0, 500.0)
        # At equator: 500/111.32 ≈ 4.49 deg both ways
        assert bbox["lat_min"] == pytest.approx(-4.49, abs=0.05)
        assert bbox["lat_max"] == pytest.approx(4.49, abs=0.05)
        assert bbox["lon_min"] == pytest.approx(-4.49, abs=0.05)
        assert bbox["lon_max"] == pytest.approx(4.49, abs=0.05)

    def test_hawaii_500km_no_wrap(self):
        # Hawaii: ~19.5°N, -155.5°W. cos(19.5°) ≈ 0.942.
        # dlon = 500/(111.32*0.942) ≈ 4.77°.
        bbox = derive_default_bbox(19.5, -155.5, 500.0)
        assert bbox["lat_min"] == pytest.approx(15.0, abs=0.1)
        assert bbox["lat_max"] == pytest.approx(24.0, abs=0.1)
        assert bbox["lon_min"] == pytest.approx(-160.27, abs=0.1)
        assert bbox["lon_max"] == pytest.approx(-150.73, abs=0.1)

    def test_high_latitude_widens_longitude(self):
        # 70°N: cos ≈ 0.342. dlon = 500/(111.32*0.342) ≈ 13.1°.
        bbox = derive_default_bbox(70.0, 0.0, 500.0)
        assert (bbox["lon_max"] - bbox["lon_min"]) > 20  # much wider than equator

    def test_polar_collapses_safely(self):
        # At exactly 90° the cos goes to 0; helper widens lon to full circle.
        bbox = derive_default_bbox(90.0, 0.0, 500.0)
        # Either full 360 coverage (lon_min wrapped) or wide wrap — must not raise.
        assert "lon_min" in bbox and "lon_max" in bbox

    def test_lat_clamped_at_pole(self):
        bbox = derive_default_bbox(89.0, 0.0, 500.0)
        assert bbox["lat_max"] <= 90.0
        assert bbox["lat_min"] >= -90.0

    def test_antimeridian_crossing(self):
        # Box near 180°: 500 km east of 178° lands at ~182° → wraps to -178°.
        bbox = derive_default_bbox(0.0, 178.0, 500.0)
        # When wrap happens, lon_min > lon_max signals it.
        # (At equator dlon ≈ 4.49 so lon_max = 178+4.49 = 182.49 → -177.51)
        assert bbox["lon_min"] > bbox["lon_max"]


# ---------- load_external_bulk_bbox ----------

class _StubSettings:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, key, default=None):
        return self._m.get(key, default)


class TestLoadExternalBulkBbox:
    def test_explicit_bbox_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "utils.geo_filter.get_real_user_home",
            lambda: tmp_path,
        )
        settings = _StubSettings({
            "external_bulk_bbox": {
                "lat_min": 1, "lat_max": 2, "lon_min": 3, "lon_max": 4,
            },
            "external_bulk_radius_km": 500,
        })
        bbox = load_external_bulk_bbox(settings)
        assert bbox == {"lat_min": 1.0, "lat_max": 2.0, "lon_min": 3.0, "lon_max": 4.0}

    def test_explicit_bbox_missing_keys_falls_through(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "utils.geo_filter.get_real_user_home",
            lambda: tmp_path,
        )
        # operator_position.json does not exist → final result is None
        settings = _StubSettings({
            "external_bulk_bbox": {"lat_min": 1},  # incomplete
            "external_bulk_radius_km": 500,
        })
        assert load_external_bulk_bbox(settings) is None

    def test_derived_from_operator_position(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "utils.geo_filter.get_real_user_home",
            lambda: tmp_path,
        )
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "operator_position.json").write_text(
            json.dumps({"lat": 19.5, "lon": -155.5}),
        )
        settings = _StubSettings({"external_bulk_radius_km": 500})
        bbox = load_external_bulk_bbox(settings)
        assert bbox is not None
        # Hawaii bbox around 19.5/-155.5 — verify it contains operator
        assert is_within_bbox(19.5, -155.5, bbox) is True
        # And excludes a node 1000 km away (e.g., far-off Pacific)
        assert is_within_bbox(0.0, 0.0, bbox) is False

    def test_no_operator_position_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "utils.geo_filter.get_real_user_home",
            lambda: tmp_path,
        )
        settings = _StubSettings({"external_bulk_radius_km": 500})
        assert load_external_bulk_bbox(settings) is None

    def test_zero_radius_disables(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "utils.geo_filter.get_real_user_home",
            lambda: tmp_path,
        )
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "operator_position.json").write_text(
            json.dumps({"lat": 19.5, "lon": -155.5}),
        )
        settings = _StubSettings({"external_bulk_radius_km": 0})
        assert load_external_bulk_bbox(settings) is None

    def test_malformed_operator_position_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "utils.geo_filter.get_real_user_home",
            lambda: tmp_path,
        )
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "operator_position.json").write_text("not json")
        settings = _StubSettings({"external_bulk_radius_km": 500})
        assert load_external_bulk_bbox(settings) is None

    def test_invalid_lat_in_position_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "utils.geo_filter.get_real_user_home",
            lambda: tmp_path,
        )
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "operator_position.json").write_text(
            json.dumps({"lat": 999, "lon": -155.5}),
        )
        settings = _StubSettings({"external_bulk_radius_km": 500})
        assert load_external_bulk_bbox(settings) is None

    def test_none_settings_returns_none(self):
        assert load_external_bulk_bbox(None) is None
