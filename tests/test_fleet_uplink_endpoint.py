"""Tests for /fleet/uplink — the Starlink pane's data source.

The pane exists to show two things a wire cannot be: mis-aimed, and clipped by
geometry invisible from the ground. Both claims are only worth making if the
degraded states stay distinguishable — "we could not ask the dish" must never
reach the page looking like "the sky is clear".

starlink_dish.py is scrupulous about this (`-1` means never-seen, unknown
readings stay None rather than 0, bearings are withheld outside the
earth-aligned frame). These tests pin that the ENDPOINT carries that discipline
through instead of flattening it on the way out.
"""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from utils.map_http_handler import MapRequestHandler
from utils.starlink_dish import DishStatus, ObstructionMap


FRAME_EARTH = 1
FRAME_UT = 2


def _make_handler() -> MapRequestHandler:
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = "/fleet/uplink"
    h.headers = {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    h.send_header = MagicMock()
    h._served = {}

    def _capture(payload, status=200):
        h._served.clear()
        h._served.update(payload)

    h._serve_json = _capture
    return h


def _grid(rows=9, cols=9, fill=1.0):
    """A square grid holding a disc of sky, like the real dish."""
    return [fill] * (rows * cols)


def _call(obstruction=None, status=None):
    obstruction = obstruction if obstruction is not None else ObstructionMap(
        "ok", "", num_rows=9, num_cols=9, min_elevation_deg=10.0,
        max_theta_deg=80.0, reference_frame=FRAME_EARTH, cells=_grid(),
    )
    status = status if status is not None else DishStatus("ok")
    h = _make_handler()
    with patch("utils.starlink_dish.get_obstruction_map", return_value=obstruction), \
         patch("utils.starlink_dish.get_dish_status", return_value=status):
        h._serve_fleet_uplink()
    return h._served


class TestDegradedStatesStayDistinguishable:
    """The whole point: 'could not ask' must not read as 'clear sky'."""

    @pytest.mark.parametrize("state", ["unreachable", "malformed", "unsupported"])
    def test_failure_carries_no_cells(self, state):
        resp = _call(obstruction=ObstructionMap(state, "dish did not answer"))
        assert resp["state"] == state
        assert resp["cells"] is None, (
            "a failed read must not ship a grid the page could paint as sky"
        )
        assert resp["detail"]

    def test_failure_census_is_all_zero_not_healthy_looking(self):
        resp = _call(obstruction=ObstructionMap("unreachable", "timeout"))
        assert resp["census"]["clear"] == 0
        assert resp["census"]["in_field"] == 0

    def test_ok_read_carries_a_grid(self):
        resp = _call()
        assert resp["state"] == "ok"
        assert len(resp["cells"]) == 81


class TestNeverSeenIsNotObstructed:
    """-1 means 'no satellite has ever passed here', not 'blocked'."""

    def test_minus_one_is_preserved_not_folded_to_zero(self):
        cells = _grid()
        cells[0] = -1.0
        cells[1] = 0.0
        resp = _call(obstruction=ObstructionMap(
            "ok", "", num_rows=9, num_cols=9, min_elevation_deg=10.0,
            max_theta_deg=80.0, reference_frame=FRAME_EARTH, cells=cells))
        assert resp["cells"][0] == -1, "unsurveyed collapsed into obstructed"
        assert resp["cells"][1] == 0
        assert resp["cells"][0] != resp["cells"][1]

    def test_quantisation_keeps_the_scale_explicit(self):
        cells = _grid(fill=0.5)
        resp = _call(obstruction=ObstructionMap(
            "ok", "", num_rows=9, num_cols=9, min_elevation_deg=10.0,
            max_theta_deg=80.0, reference_frame=FRAME_EARTH, cells=cells))
        assert resp["cells_scale"] == 100
        assert resp["cells"][40] == 50


class TestBearingsWithheldOutsideEarthFrame:
    """An azimuth in the terminal's own frame points at the wrong tree."""

    def test_earth_frame_offers_bearings(self):
        resp = _call()
        assert resp["bearings_available"] is True

    def test_terminal_frame_withholds_them(self):
        cells = _grid(fill=0.0)   # everything obstructed
        resp = _call(obstruction=ObstructionMap(
            "ok", "", num_rows=9, num_cols=9, min_elevation_deg=10.0,
            max_theta_deg=80.0, reference_frame=FRAME_UT, cells=cells))
        assert resp["bearings_available"] is False
        assert resp["bearings"] == [], (
            "an empty list plus bearings_available=True would read as "
            "'nothing is blocked' — the flag is what makes the silence honest"
        )

    def test_obstructed_sky_yields_bearings_in_earth_frame(self):
        cells = _grid(fill=0.0)
        resp = _call(obstruction=ObstructionMap(
            "ok", "", num_rows=9, num_cols=9, min_elevation_deg=10.0,
            max_theta_deg=80.0, reference_frame=FRAME_EARTH, cells=cells))
        assert resp["bearings"], "fixture is fully obstructed; expected bearings"
        for b in resp["bearings"]:
            assert 0.0 <= b["azimuth_deg"] <= 360.0
            assert -90.0 <= b["elevation_deg"] <= 90.0


class TestStatusRidesAlong:
    """The pane is NOC-local and must not have to read the truth document."""

    def test_status_present_on_success(self):
        resp = _call(status=DishStatus("ok", boresight_azimuth_deg=29.7,
                                       boresight_elevation_deg=72.3))
        assert resp["status"]["state"] == "ok"
        assert resp["status"]["boresight_azimuth_deg"] == pytest.approx(29.7)

    def test_unknown_readings_stay_null_never_zero(self):
        resp = _call(status=DishStatus("unreachable", "no answer"))
        st = resp["status"]
        assert st["state"] == "unreachable"
        assert st["pop_ping_latency_ms"] is None, "a null reading became a number"
        assert st["downlink_throughput_bps"] is None

    def test_dish_unreachable_does_not_blank_the_sky_map(self):
        """Two independent readings; one failing must not erase the other."""
        resp = _call(status=DishStatus("unreachable", "no answer"))
        assert resp["state"] == "ok"
        assert resp["cells"] is not None


class TestSurveyWindowAndWithheldFields:
    """The map's age is part of its meaning; one field is knowingly withheld."""

    def test_survey_window_reaches_the_pane(self):
        """"21 cells obstructed" means different things at 20 min and 9 days."""
        resp = _call(status=DishStatus("ok", obstruction_valid_s=772230.0))
        assert resp["status"]["obstruction_valid_s"] == pytest.approx(772230.0)

    def test_time_obstructed_is_deliberately_not_published(self):
        """DO NOT "fix" this by adding the field to as_dict().

        It is parsed, but the value does not support the name. Measured
        2026-09-14 on rev4_gopher_prod1: window 772,230s x fraction 0.00229
        implies ~1,767s obstructed; the field reads 5.9e-06. That slot does
        not carry seconds in current firmware. Publishing it would render
        "obstructed for 0.0 s" as a confident answer to an open question.
        Re-derive the wire mapping first, then delete this test.
        """
        st = DishStatus("ok", time_obstructed_s=5.9e-06)
        assert st.time_obstructed_s == pytest.approx(5.9e-06), "parser still reads it"
        assert "time_obstructed_s" not in st.as_dict(), (
            "time_obstructed_s was published; its units are unverified — see "
            "the field comment in starlink_dish.py before exposing it"
        )

    def test_unknown_window_stays_null_not_zero(self):
        resp = _call(status=DishStatus("ok"))
        assert resp["status"]["obstruction_valid_s"] is None


class TestReaderDefectsLeaveAWitness:
    def test_missing_module_reports_absent_not_a_fault(self):
        h = _make_handler()
        with patch.dict("sys.modules", {"utils.starlink_dish": None}):
            h._serve_fleet_uplink()
        assert h._served["state"] == "absent"
        assert "starlink" in h._served["detail"].lower()

    def test_reader_raising_is_an_error_not_a_dishless_site(self):
        """get_obstruction_map() is contracted never to raise."""
        h = _make_handler()
        with patch("utils.starlink_dish.get_obstruction_map",
                   side_effect=RuntimeError("boom")), \
             patch("utils.starlink_dish.get_dish_status", return_value=DishStatus("ok")):
            h._serve_fleet_uplink()
        assert h._served["state"] == "error"
        assert "RuntimeError" in h._served["detail"]
        assert h._served["state"] != "absent"
