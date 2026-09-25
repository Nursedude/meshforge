"""Space weather with every source dead must say UNKNOWN, not "Quiet / Fair".

Found by the TUI success-truth sweep (2026-09-22): `get_current_conditions()`
returned a SpaceWeatherData with every field None, `updated=now`, storm level
QUIET and every band "Fair" (the assessor's defaults), and the command layer
wrapped that in `CommandResult.ok`, so the Dashboard rendered a calm forecast
from a fetch that never happened — honest_failure_modes #1/#2 in weather form.
"""
import sys
from pathlib import Path
from unittest.mock import patch

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.space_weather import SpaceWeatherAPI  # noqa: E402
from commands import propagation  # noqa: E402

_DEAD = {
    "get_k_index": None,
    "get_a_index": None,
    "get_solar_flux": None,
    "get_xray_flux": None,
}


def _api_with(**answers):
    api = SpaceWeatherAPI(timeout=1)
    patches = [patch.object(SpaceWeatherAPI, name, return_value=val)
               for name, val in {**_DEAD, **answers}.items()]
    return api, patches


def test_nothing_answered_is_not_an_observation():
    api, patches = _api_with()
    for p in patches:
        p.start()
    try:
        data = api.get_current_conditions()
    finally:
        for p in patches:
            p.stop()
    assert data.sources_answered == 0
    assert data.updated is None, "a fetch that never happened must not read as fresh"


def test_one_source_answering_counts():
    api, patches = _api_with(get_solar_flux=150.0)
    for p in patches:
        p.start()
    try:
        data = api.get_current_conditions()
    finally:
        for p in patches:
            p.stop()
    assert data.sources_answered == 1
    assert data.updated is not None


def test_command_fails_loud_when_no_source_answered():
    api, patches = _api_with()
    for p in patches:
        p.start()
    try:
        with patch.object(propagation, "SpaceWeatherAPI", return_value=api), \
             patch.object(propagation, "_HAS_SPACE_WEATHER", True):
            result = propagation.get_space_weather()
    finally:
        for p in patches:
            p.stop()
    assert result.success is False
    assert "UNKNOWN" in result.message
    assert "Quiet" not in result.message


def test_command_still_reports_when_a_source_answered():
    api, patches = _api_with(get_k_index=(3, None), get_solar_flux=120.0)
    for p in patches:
        p.start()
    try:
        with patch.object(propagation, "SpaceWeatherAPI", return_value=api), \
             patch.object(propagation, "_HAS_SPACE_WEATHER", True):
            result = propagation.get_space_weather()
    finally:
        for p in patches:
            p.stop()
    assert result.success is True
    assert result.data["solar_flux"] == 120.0


# --- siblings (non-author review 2026-09-22, finding 4: hfm #5, grep the copies) ---

def _dead_api():
    api, patches = _api_with()
    for p in patches:
        p.start()
    return api, patches


def test_band_conditions_fail_loud_when_no_source_answered():
    api, patches = _dead_api()
    try:
        with patch.object(propagation, "SpaceWeatherAPI", return_value=api), \
             patch.object(propagation, "_HAS_SPACE_WEATHER", True):
            result = propagation.get_band_conditions()
    finally:
        for p in patches:
            p.stop()
    assert result.success is False
    assert "UNKNOWN" in result.message
    assert "Fair" not in result.message


def test_propagation_summary_fails_loud_when_no_source_answered():
    api, patches = _dead_api()
    try:
        with patch.object(propagation, "SpaceWeatherAPI", return_value=api), \
             patch.object(propagation, "_HAS_SPACE_WEATHER", True):
            result = propagation.get_propagation_summary()
    finally:
        for p in patches:
            p.stop()
    assert result.success is False
    assert "Quiet" not in result.message


def test_quick_summary_says_unavailable_not_quiet():
    api, patches = _dead_api()
    try:
        summary = api.get_quick_summary()
    finally:
        for p in patches:
            p.stop()
    assert "unavailable" in summary.lower()
    assert "Quiet" not in summary


def test_quick_summary_still_summarises_when_a_source_answered():
    api, patches = _api_with(get_k_index=(2, None), get_solar_flux=130.0)
    for p in patches:
        p.start()
    try:
        summary = api.get_quick_summary()
    finally:
        for p in patches:
            p.stop()
    assert summary.startswith("SFI:130 K:2")


# ---- live-truth pass 2026-09-25: Dashboard > Space Weather ----

REAL_ROWS = [  # NOAA daily-geomagnetic-indices.txt, 2026-09-25 (today's row partial)
    "2026 09 23     4  0 1 0 1 2 1 2 2     0  0 0 0 0 0 0 0 1     4   0.33  0.33  0.33  1.00  0.67  0.67  1.67  1.67",
    "2026 09 24    13  2 2 3 4 4 2 1 2    35  1 1 5 7 5 4 2 2    16   3.00  2.33  3.00  4.33  3.67  3.00  2.00  2.67",
    "2026 09 25    -1  3 3 4 4 3-1-1-1    -1  2 3 6 6 5-1-1-1    19   3.67  3.67  4.00  4.00  3.67 -1.00 -1.00 -1.00",
]


def test_planetary_a_is_read_not_fredericksburgs_minus_one():
    from utils.space_weather import parse_planetary_a
    assert parse_planetary_a(REAL_ROWS) == 19            # not -1 (Fredericksburg, not computed)
    assert parse_planetary_a(REAL_ROWS[:2]) == 16
    partial = REAL_ROWS[:2] + [REAL_ROWS[2].replace("    19   ", "    -1   ")]
    assert parse_planetary_a(partial) == 16              # a negative is skipped, never shown
    assert parse_planetary_a([]) is None


def test_kp_reads_noaas_current_object_format_and_the_old_pairs(monkeypatch):
    from utils.space_weather import SpaceWeatherAPI
    api = SpaceWeatherAPI()
    new = [{"time_tag": "2026-09-25T16:53:00", "kp_index": 2, "estimated_kp": 1.67, "kp": "2M"}]
    monkeypatch.setattr(api, "_fetch_json", lambda *_a, **_k: new)
    kp, ts = api.get_k_index()
    assert kp == 2 and ts.hour == 16
    old = [["2026-01-12 10:00:00.000", "4.33"]]
    monkeypatch.setattr(api, "_fetch_json", lambda *_a, **_k: old)
    assert api.get_k_index()[0] == 4


def test_no_kp_means_an_unknown_storm_level_never_quiet():
    from utils.space_weather import GeomagneticStorm, SpaceWeatherData
    assert SpaceWeatherData().geomag_storm is GeomagneticStorm.UNKNOWN
    assert "UNKNOWN" in GeomagneticStorm.UNKNOWN.value
