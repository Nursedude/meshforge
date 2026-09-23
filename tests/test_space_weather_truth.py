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
