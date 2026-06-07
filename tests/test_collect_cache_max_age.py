"""Demand-driven collect() cache window is settings-driven.

On boxes where the meshtasticd leg is expensive (moc5: CH341 USB-SPI
radio starves the daemon's TCP nodedb stream, 14-19 s per sync), a map
browser tab's ~60 s auto-refresh re-triggered a full collect every time
the hardcoded 30 s cache lapsed. `collect_cache_max_age_seconds` lets
such a box raise the demand-side window to just under
`periodic_refresh_seconds` so the periodic loop becomes the sole payer
and demand always serves cache. Default stays 30 — fleet behavior
unchanged unless a box opts in.
"""

import time
from unittest.mock import patch

import pytest

from utils.map_data_collector import MapDataCollector


@pytest.fixture
def collector(tmp_path):
    fake_home = tmp_path / "home"
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
        c = MapDataCollector(
            cache_dir=tmp_path / "cache",
            enable_history=False,
            config_dir=cfg_dir,
        )
    return c


def _prime_cache(collector, age_seconds):
    collector._cached_geojson = {"type": "FeatureCollection", "features": [],
                                 "properties": {"sentinel": "cached"}}
    collector._last_collect = time.time() - age_seconds


class TestCollectCacheMaxAgeSetting:
    def test_default_is_30(self, collector):
        assert collector._settings.get("collect_cache_max_age_seconds") == 30
        assert MapDataCollector.DEFAULT_COLLECT_CACHE_MAX_AGE_SECONDS == 30

    def test_no_arg_cache_hit_inside_default_window(self, collector):
        _prime_cache(collector, age_seconds=20)
        with patch.object(collector, "_collect_locked") as locked:
            result = collector.collect()
        locked.assert_not_called()
        assert result["properties"]["sentinel"] == "cached"

    def test_no_arg_rebuilds_past_default_window(self, collector):
        _prime_cache(collector, age_seconds=40)
        with patch.object(
            collector, "_collect_locked",
            return_value={"type": "FeatureCollection", "features": []},
        ) as locked:
            collector.collect()
        locked.assert_called_once()

    def test_setting_raises_window_for_no_arg_callers(self, collector):
        """The moc5 shape: setting=290 makes a 60 s-old cache a hit."""
        collector._settings.set("collect_cache_max_age_seconds", 290)
        _prime_cache(collector, age_seconds=60)
        with patch.object(collector, "_collect_locked") as locked:
            result = collector.collect()
        locked.assert_not_called()
        assert result["properties"]["sentinel"] == "cached"

    def test_explicit_arg_overrides_setting(self, collector):
        """Exporters/TUI passing an explicit window keep their contract."""
        collector._settings.set("collect_cache_max_age_seconds", 290)
        _prime_cache(collector, age_seconds=60)
        with patch.object(
            collector, "_collect_locked",
            return_value={"type": "FeatureCollection", "features": []},
        ) as locked:
            collector.collect(max_age_seconds=0)
        locked.assert_called_once()
