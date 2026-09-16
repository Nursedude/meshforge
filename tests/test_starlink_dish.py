"""Tests for utils.starlink_dish — the read-only dish telemetry reader.

THE GOLDEN FIXTURE IS REAL. ``LIVE_STATUS_B64`` is a byte-for-byte capture of
an actual ``get_status`` reply from the operator's dish (firmware
``2026.08.31.mr85832``, 2026-09-13). A fixture I invented would only prove the
decoder agrees with my own idea of the wire format — which is exactly the
mistake this module's field numbers were nearly shipped with. Sanitised: only
the dish's own hardware/software identifiers appear, which are device model
strings, not operator network topology (MF015).

The tests that matter most are the DEGRADED ones. An unreachable dish must
never render as a healthy-looking zero: ``fraction_obstructed=0.0`` means a
perfectly clear sky, so a reader that zeroed on failure would report the
healthiest possible value for a dish it could not reach.
"""
from __future__ import annotations

import base64
import struct

import pytest

from utils.starlink_dish import (
    DishStatus,
    decode_fields,
    format_status,
    get_dish_status,
    parse_status_response,
)

LIVE_STATUS_B64 = """
AAAAAiEYK6J9mwQKogEKHHV0MDE0ODk4MDAtODEzMTNjMGUtNWFiMTE0Y2YSEXJldjRfZ29waGVy
X3Byb2QxGhIyMDI2LjA4LjMxLm1yODU4MzIiAlVTKODm/f///////wFABWDOgd3UBnpEZTEwM2M3
ZGQtMGVmNy00ZjMyLThhNjctNGQzM2ViN2E4MmI2LnV0ZXJtX2NhdGFwdWx0X21hbmlmZXN0LnJl
bGVhc2USBAiGkSzVPkA51U7iPhcNQDwWOyXwhDBJPf////9NQbPFNlDWHuo+AP0+NrP+TIU/8wP3
SY0/j8Q2Qp0/X/zzQaU/r5aQQro/BggBEAsoAsA/6AfQPwHaPwoQARgBIAEoATAB4D8B6D8B+D8C
gEABiEABkkACCAGaQCIIAh2MvYxBJV/880Etr5aQQjACPYKhmT5FluMfQk1kFmlCokAUCB8QHRgf
ICYoMDAxOCFAKkgaUDG4QP///////////wGCQR9Sb3V0ZXItMDEwMDAwMDAwMDAwMDAwMDAyMzEw
M0Q1ikEGOgBCAEoAmkEAoEEBqEEBwkEAykEUDYeE9b0VkatsPx23j7K+JS2jxT3SQS8KH1JvdXRl
ci0wMTAwMDAwMDAwMDAwMDAwMDIzMTAzRDUSDAgBEKiai8S6m8jqGNhBAehBAfJBAI1CAACAP4J9
JEgDyD4BiH0ByLsBAYj6AQHIuAIBiPcCAci1AwGI9AMByLIEAQ==
"""

LIVE_STATUS = base64.b64decode(LIVE_STATUS_B64)


class TestWireFormatDecoder:
    """The decoder walks protobuf WITHOUT a schema, so its own correctness is
    the floor everything else stands on."""

    def test_varint_field(self):
        # field 1, wire 0, value 300 (two-byte varint)
        assert decode_fields(b"\x08\xac\x02") == {1: [300]}

    def test_length_delimited_field(self):
        assert decode_fields(b"\x12\x03abc") == {2: [b"abc"]}

    def test_fixed32_and_fixed64_keep_raw_bytes(self):
        out = decode_fields(b"\x15\x00\x00\x80\x3f" + b"\x19" + b"\x00" * 8)
        assert out[2] == [b"\x00\x00\x80\x3f"]
        assert out[3] == [b"\x00" * 8]

    def test_repeated_fields_are_all_kept(self):
        """Silently keeping one occurrence would hide a repeated field."""
        assert decode_fields(b"\x08\x01\x08\x02\x08\x03") == {1: [1, 2, 3]}

    def test_large_field_numbers_round_trip(self):
        """The dish uses field numbers above 1000; a decoder that assumed a
        single-byte key would misread every one of them."""
        key = struct.pack("B", 0xE2) + struct.pack("B", 0x3E)  # field 1004, wire 2
        assert decode_fields(key + b"\x00") == {1004: [b""]}

    @pytest.mark.parametrize("bad", [
        b"\x08",                      # truncated varint
        b"\x12\x05ab",                # length-delimited overruns the buffer
        b"\x15\x00\x00",              # truncated fixed32
        b"\x19\x00",                  # truncated fixed64
    ])
    def test_truncation_raises_rather_than_guesses(self, bad):
        with pytest.raises(ValueError):
            decode_fields(bad)

    def test_unsupported_wire_type_refuses(self):
        """Wire types 3/4 (groups) and 6/7 have no length we can skip. Guessing
        one silently shifts every later field and yields plausible nonsense."""
        with pytest.raises(ValueError, match="unsupported wire type"):
            decode_fields(b"\x0b")  # field 1, wire type 3


class TestLiveCapture:
    """Decode a real dish reply."""

    def test_state_is_ok(self):
        assert parse_status_response(LIVE_STATUS).state == "ok"

    def test_device_identity_matches_the_capture(self):
        st = parse_status_response(LIVE_STATUS)
        assert st.dish_id == "ut01489800-81313c0e-5ab114cf"
        assert st.hardware_version == "rev4_gopher_prod1"
        assert st.software_version == "2026.08.31.mr85832"

    def test_readings_are_physically_plausible(self):
        """Pins the FIELD NUMBERS. Reading pop_ping_latency_ms from the wrong
        field (an early draft took 1015, which is gps_stats) yields a value
        that fails these ranges rather than passing silently."""
        st = parse_status_response(LIVE_STATUS)
        assert st.pop_ping_latency_ms is not None
        assert 1.0 < st.pop_ping_latency_ms < 2000.0
        assert st.pop_ping_drop_rate is not None
        assert 0.0 <= st.pop_ping_drop_rate <= 1.0
        assert st.fraction_obstructed is not None
        assert 0.0 <= st.fraction_obstructed <= 1.0
        assert st.uptime_s is not None and st.uptime_s > 0

    def test_alerts_are_all_definite_booleans(self):
        """The alerts message was present, so every known alert has a value."""
        st = parse_status_response(LIVE_STATUS)
        assert st.alerts, "expected the alerts submessage to decode"
        assert all(isinstance(v, bool) for v in st.alerts.values())

    def test_format_status_renders_without_error(self):
        text = format_status(parse_status_response(LIVE_STATUS))
        assert "Latency" in text and "Obstructed" in text
        assert "unknown" not in text.split("Alerts")[0].replace("Obstructed", "")


class TestProto3DefaultSemantics:
    """A scalar equal to its default is not serialised. Inside a message we
    DID decode, absent means the default — reporting it 'unknown' is its own
    small lie (found live 2026-09-13 on a genuinely zero ping-drop rate)."""

    def _wrap(self, inner: bytes) -> bytes:
        # Response{ dish_get_status: inner }  -> field 2004, wire 2
        key = b"\xa2\x7d"  # (2004 << 3) | 2
        body = key + bytes([len(inner)]) + inner
        return b"\x00" + struct.pack(">I", len(body)) + body

    def test_absent_scalar_in_a_present_message_is_zero_not_unknown(self):
        st = parse_status_response(self._wrap(b""))
        assert st.state == "ok"
        assert st.pop_ping_drop_rate == 0.0
        assert st.pop_ping_latency_ms == 0.0

    def test_absent_submessage_leaves_fields_unknown(self):
        """We did not receive obstruction_stats at all, so we do NOT know the
        sky is clear. None, never 0.0."""
        st = parse_status_response(self._wrap(b""))
        assert st.fraction_obstructed is None
        assert st.currently_obstructed is None
        assert st.alerts == {}


class TestDegradedStatesNeverLookHealthy:
    """The whole point of the tri-state."""

    def test_unreachable_carries_no_readings(self, monkeypatch):
        monkeypatch.setattr(
            "utils.starlink_dish._grpc_call",
            lambda *a, **k: (None, "curl exit 7 (dish unreachable?)"),
        )
        st = get_dish_status()
        assert st.state == "unreachable"
        assert not st.ok
        # The assertion that matters: NOT a clear sky, NOT zero packet loss.
        assert st.fraction_obstructed is None
        assert st.pop_ping_drop_rate is None
        assert st.currently_obstructed is None

    def test_missing_curl_is_unsupported_not_unreachable(self, monkeypatch):
        """'This box cannot ask' and 'the dish did not answer' are different
        claims and must not collapse into one."""
        monkeypatch.setattr(
            "utils.starlink_dish._grpc_call",
            lambda *a, **k: (None, "curl not found — this box cannot query the dish"),
        )
        assert get_dish_status().state == "unsupported"

    def test_nonzero_grpc_status_is_not_ok(self, monkeypatch):
        monkeypatch.setattr(
            "utils.starlink_dish._grpc_call",
            lambda *a, **k: (None, "grpc-status 12: Unimplemented"),
        )
        assert get_dish_status().state == "unreachable"

    @pytest.mark.parametrize("junk", [b"", b"\x00\x00", b"\x00\x00\x00\x00\x09short"])
    def test_short_or_empty_frames_are_malformed(self, junk):
        assert parse_status_response(junk).state == "malformed"

    def test_reply_without_dish_get_status_is_malformed(self):
        """A well-formed gRPC reply to a DIFFERENT request must not decode as
        a status full of zeros."""
        body = b"\x08\x01"  # field 1 varint, nothing we asked for
        framed = b"\x00" + struct.pack(">I", len(body)) + body
        st = parse_status_response(framed)
        assert st.state == "malformed"
        assert st.fraction_obstructed is None

    def test_undecodable_payload_is_malformed_not_ok(self):
        body = b"\xa2\x7d\x02\x0b\x00"  # dish_get_status containing wire type 3
        framed = b"\x00" + struct.pack(">I", len(body)) + body
        assert parse_status_response(framed).state == "malformed"

    def test_format_status_leads_with_the_failure(self):
        text = format_status(DishStatus("unreachable", "no route to host"))
        assert text.startswith("Starlink dish: UNREACHABLE")
        assert "no route to host" in text

    def test_active_alerts_empty_is_not_a_health_claim(self):
        """A caller must check state first; this pins that an unreachable dish
        reports no alerts purely because it reports nothing."""
        st = DishStatus("unreachable", "timed out")
        assert st.active_alerts == []
        assert not st.ok


class TestRollupUplinkLine:
    """The site uplink line in the fleet watchers pane.

    Absent-by-design must be SILENT. Most sites have no Starlink, and a
    permanent "no dish" line would train the reader to skip that row — the
    same reason the rollup does not print a standing "nothing here" for boxes
    that carry no session notes.
    """

    def _render(self, monkeypatch, status):
        from mini_dudeai import rollup
        monkeypatch.setattr("utils.starlink_dish.get_dish_status",
                            lambda *a, **k: status)
        return rollup._render_uplink()

    def test_unreachable_is_silent(self, monkeypatch):
        assert self._render(monkeypatch, DishStatus("unreachable", "curl exit 7")) == ""

    def test_missing_curl_is_silent(self, monkeypatch):
        assert self._render(monkeypatch, DishStatus("unsupported", "no curl")) == ""

    def test_malformed_is_reported_not_swallowed(self, monkeypatch):
        """The dish ANSWERED and we could not read it — that is a defect in
        our field numbers, and hiding it would make it permanent."""
        out = self._render(monkeypatch, DishStatus("malformed", "bad field"))
        assert "unreadable" in out and "bad field" in out

    def test_ok_renders_the_readings(self, monkeypatch):
        st = parse_status_response(LIVE_STATUS)
        out = self._render(monkeypatch, st)
        assert out.startswith("🛰️ **uplink** (site, one dish)")
        for token in ("latency", "drop", "down", "up", "obstructed"):
            assert token in out

    def test_current_obstruction_is_called_out(self, monkeypatch):
        st = DishStatus("ok", currently_obstructed=True)
        assert "OBSTRUCTED NOW" in self._render(monkeypatch, st)

    def test_active_alerts_are_named(self, monkeypatch):
        st = DishStatus("ok", alerts={"thermal_throttle": True, "roaming": False})
        out = self._render(monkeypatch, st)
        assert "thermal_throttle" in out and "roaming" not in out

    def test_unknown_readings_render_as_question_not_zero(self, monkeypatch):
        """A None latency must not print as '0ms' — that reads as a perfect
        link rather than an absent measurement."""
        out = self._render(monkeypatch, DishStatus("ok"))
        assert "latency ?" in out
        assert "latency 0ms" not in out


class TestLinkPhysicsFields:
    """Boresight + SNR. The dish is a phased array, so the beam steers
    electronically and its pointing is a real measurement, not a mounting fact.
    """

    def test_boresight_is_read_from_the_live_capture(self):
        st = parse_status_response(LIVE_STATUS)
        assert st.boresight_azimuth_deg is not None
        assert st.boresight_elevation_deg is not None
        # Physical bounds pin the FIELD NUMBERS: a wrong field yields a value
        # outside these long before anyone notices a wrong-looking dashboard.
        assert -360.0 <= st.boresight_azimuth_deg <= 360.0
        assert -90.0 <= st.boresight_elevation_deg <= 90.0

    def test_snr_flag_is_a_definite_bool_when_status_decoded(self):
        st = parse_status_response(LIVE_STATUS)
        assert isinstance(st.snr_above_noise_floor, bool)

    def test_degraded_read_leaves_physics_unknown(self, monkeypatch):
        """An unreachable dish must not report a beam position — a plausible
        az/el for a dish we never reached is worse than no number."""
        monkeypatch.setattr(
            "utils.starlink_dish._grpc_call",
            lambda *a, **k: (None, "curl exit 7"),
        )
        st = get_dish_status()
        assert st.boresight_azimuth_deg is None
        assert st.boresight_elevation_deg is None
        assert st.snr_above_noise_floor is None

    def test_format_shows_alignment(self):
        text = format_status(parse_status_response(LIVE_STATUS))
        assert "Boresight" in text and "SNR" in text

    def test_as_dict_carries_the_physics(self):
        d = parse_status_response(LIVE_STATUS).as_dict()
        for key in ("boresight_azimuth_deg", "boresight_elevation_deg",
                    "snr_above_noise_floor"):
            assert key in d


class TestRollupSnrSignal:
    def _render(self, monkeypatch, status):
        from mini_dudeai import rollup
        monkeypatch.setattr("utils.starlink_dish.get_dish_status",
                            lambda *a, **k: status)
        return rollup._render_uplink()

    def test_below_noise_floor_is_called_out(self, monkeypatch):
        st = DishStatus("ok", snr_above_noise_floor=False)
        assert "SNR BELOW NOISE FLOOR" in self._render(monkeypatch, st)

    def test_healthy_snr_is_not_mentioned(self, monkeypatch):
        """The steady state must stay quiet or the line becomes wallpaper."""
        st = DishStatus("ok", snr_above_noise_floor=True)
        assert "SNR" not in self._render(monkeypatch, st)

    def test_unknown_snr_is_not_reported_as_bad(self, monkeypatch):
        """None is not False. An unknown reading must not raise an alarm."""
        st = DishStatus("ok", snr_above_noise_floor=None)
        assert "BELOW NOISE FLOOR" not in self._render(monkeypatch, st)


# ── obstruction map ─────────────────────────────────────────────────────────
LIVE_MAP_GZ_B64 = """
H4sICIyup2oCA29ic21hcC5iaW4A7dwxSsNgHIfhv3RxdNROLk4iCNnzUWdvVESvUSdvIjinew/g
6AEcNcZQ1DRUW2NNnsJDGzoE8vJLpjYinq4PTx+unhej/enBdPy4GEXc3AMAAAAAAENSvlwHXdGV
X5Ta1a9N2n/5ftX59Nh+2zUaL6//vLjMm7t9blgfL9+zLEvfOJfune64bd+rNty863lR5D8/n+5/
0/mj152ndZ7Zt7OjtK1zat595/atR1T3++r4/d6v9b/vXO+73O8sLz9v8GzWfcdbV23fNpw6pmmH
nXeMzsNprfewOietB0dnvbXWW2utddZca7211lprvXXWWmvNtdYarelXby21pn+9ddSafrXW0K6x
ba211ltrrbXWXGudtdZba5311ltru9bOtrFttNZaa6311lprvfXWWmud9dZba6311pph/D5bU/+b
o7He2mqtq9666q2r9joCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACNTiKOJ2cRdxfney+RhBnwhOwA
AA==
"""

#: A real 123x123 map captured from the dish 2026-09-13. Stored gzipped
#: because it is 60,548 bytes raw and 400 compressed — it is almost entirely
#: the -1.0 sentinel, which is itself the finding this fixture exists to pin.
LIVE_MAP = __import__("gzip").decompress(base64.b64decode(LIVE_MAP_GZ_B64))


class TestObstructionMapDecode:
    def test_dimensions_agree_with_the_payload(self):
        from utils.starlink_dish import parse_obstruction_map
        m = parse_obstruction_map(LIVE_MAP)
        assert m.state == "ok"
        assert m.num_rows == 123 and m.num_cols == 123
        assert len(m.cells) == 123 * 123

    def test_geometry_constants_are_complementary(self):
        """max_theta + min_elevation == 90 is what proves the radius->zenith
        mapping is the intended one. If a firmware change broke that, every
        bearing we report would be quietly wrong."""
        from utils.starlink_dish import parse_obstruction_map
        m = parse_obstruction_map(LIVE_MAP)
        assert m.min_elevation_deg is not None and m.max_theta_deg is not None
        assert abs((m.max_theta_deg + m.min_elevation_deg) - 90.0) < 0.01

    def test_frame_is_earth_aligned(self):
        from utils.starlink_dish import FRAME_EARTH, parse_obstruction_map
        m = parse_obstruction_map(LIVE_MAP)
        assert m.reference_frame == FRAME_EARTH
        assert "north" in m.frame_name

    def test_sentinel_is_exactly_minus_one(self):
        from utils.starlink_dish import parse_obstruction_map
        m = parse_obstruction_map(LIVE_MAP)
        negatives = {round(v, 4) for v in m.cells if v < 0}
        assert negatives == {-1.0}

    def test_corners_are_outside_the_field(self):
        """The grid is a square holding a disc; corners can never be observed.
        Counting them as 'unsurveyed' understates coverage by ~21%."""
        from utils.starlink_dish import parse_obstruction_map
        m = parse_obstruction_map(LIVE_MAP)
        c = m.census()
        assert c["outside_field"] > 3000
        assert c["in_field"] + c["outside_field"] == 123 * 123

    def test_census_partitions_every_cell(self):
        from utils.starlink_dish import parse_obstruction_map
        c = parse_obstruction_map(LIVE_MAP).census()
        assert c["surveyed"] + c["no_data"] == c["in_field"]
        assert c["clear"] + c["obstructed"] + c["partial"] == c["surveyed"]

    def test_dimension_mismatch_is_refused(self):
        """Reading the grid with the wrong stride yields a picture that LOOKS
        like a sky map and is wrong everywhere — refuse rather than render."""
        from utils.starlink_dish import parse_obstruction_map
        import struct as _s
        inner = (b"\x08\x05" b"\x10\x05"          # num_rows=5, num_cols=5
                 + b"\x1a\x08" + _s.pack("<2f", 1.0, 1.0))  # only 2 cells
        body = b"\xc2\x7d" + bytes([len(inner)]) + inner
        framed = b"\x00" + _s.pack(">I", len(body)) + body
        m = parse_obstruction_map(framed)
        assert m.state == "malformed"
        assert "25 declared" in m.detail or "5x5 declared" in m.detail

    @pytest.mark.parametrize("junk", [b"", b"\x00\x00\x00"])
    def test_short_frames_are_malformed(self, junk):
        from utils.starlink_dish import parse_obstruction_map
        assert parse_obstruction_map(junk).state == "malformed"


class TestObstructionBearings:
    def _map(self):
        from utils.starlink_dish import parse_obstruction_map
        return parse_obstruction_map(LIVE_MAP)

    def test_bearings_are_physically_bounded(self):
        for az, el, snr in self._map().obstruction_bearings():
            assert 0.0 <= az < 360.0
            assert 10.0 - 1e-6 <= el <= 90.0
            assert 0.0 <= snr <= 0.5

    def test_centre_cell_is_the_zenith(self):
        m = self._map()
        az, el = m.cell_bearing(61, 61)
        assert abs(el - 90.0) < 1.0

    def test_top_centre_is_true_north(self):
        """FRAME_EARTH puts true north at top centre. A sign error here would
        send an operator to the opposite side of the house."""
        m = self._map()
        az, el = m.cell_bearing(0, 61)
        assert az < 1.0 or az > 359.0
        assert el < 20.0          # near the horizon at the disc edge

    def test_right_of_centre_is_east(self):
        m = self._map()
        az, _ = m.cell_bearing(61, 110)
        assert 80.0 < az < 100.0

    def test_bearings_refused_when_frame_is_not_earth_aligned(self):
        """A dish-aligned map has no compass bearing. Guessing one is worse
        than withholding it."""
        from utils.starlink_dish import FRAME_UT
        m = self._map()
        m.reference_frame = FRAME_UT
        assert m.obstruction_bearings() == []
        assert m.cell_bearing(61, 61) is None

    def test_outside_the_disc_has_no_bearing(self):
        assert self._map().cell_bearing(0, 0) is None


class TestObstructionMapRender:
    def test_render_includes_the_coverage_caveat(self):
        """The surveyed fraction must never read as a progress bar."""
        from utils.starlink_dish import parse_obstruction_map, render_obstruction_map
        out = render_obstruction_map(parse_obstruction_map(LIVE_MAP))
        assert "COVERAGE figure, not a progress bar" in out
        assert "true north" in out

    def test_render_degraded_leads_with_the_state(self):
        from utils.starlink_dish import ObstructionMap, render_obstruction_map
        out = render_obstruction_map(ObstructionMap("unreachable", "curl exit 7"))
        assert out.startswith("obstruction map: UNREACHABLE")
        assert "curl exit 7" in out

    def test_render_is_bounded_in_width(self):
        from utils.starlink_dish import parse_obstruction_map, render_obstruction_map
        out = render_obstruction_map(parse_obstruction_map(LIVE_MAP), width=41)
        assert all(len(line) <= 80 for line in out.splitlines())


class TestFailsFastWhenThereIsNoDish:
    """CI regression, 2026-09-13.

    ``test_all_tags_dispatch`` exercises EVERY registered menu action, and the
    sky-map pane budgets 20s. On a machine with no dish that one pane spent
    the whole budget and took the suite's timeout with it. It passed locally
    only because a dish happens to sit on this LAN — a verdict that depended
    on un-pinned machine state, which is no verdict at all.

    The cure is a cheap TCP pre-check plus a curl ``--connect-timeout``:
    ``--max-time`` bounds the whole transfer, so without a connect timeout an
    unroutable address spends all of it in SYN retries.
    """

    def test_unreachable_host_never_reaches_the_subprocess(self, monkeypatch):
        """Deterministic, not timing-based: prove the expensive path is not
        entered at all. A wall-clock assertion would itself depend on ambient
        machine state, which is the defect this test exists for."""
        import utils.starlink_dish as sd

        monkeypatch.setattr(sd, "_dish_reachable", lambda *a, **k: False)

        def forbidden(*a, **k):
            raise AssertionError("subprocess must not run when nothing listens")

        monkeypatch.setattr(sd.subprocess, "run", forbidden)
        st = sd.get_dish_status()
        assert st.state == "unreachable"
        assert "nothing listening" in st.detail

    def test_obstruction_map_short_circuits_too(self, monkeypatch):
        """The map is the expensive call (20s budget, ~60KB) — it is the one
        that actually broke CI, so it gets its own assertion."""
        import utils.starlink_dish as sd

        monkeypatch.setattr(sd, "_dish_reachable", lambda *a, **k: False)
        monkeypatch.setattr(
            sd.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("subprocess must not run")))
        m = sd.get_obstruction_map(timeout=20)
        assert m.state == "unreachable"

    def test_curl_invocation_carries_a_connect_timeout(self, monkeypatch):
        """Pins the flag itself. Losing it would restore the original bug
        while every other test still passed."""
        import utils.starlink_dish as sd
        seen = {}

        monkeypatch.setattr(sd, "_dish_reachable", lambda *a, **k: True)

        class _Proc:
            returncode = 1
            stdout = stderr = b""

        def capture(cmd, **kw):
            seen["cmd"] = cmd
            return _Proc()

        monkeypatch.setattr(sd.subprocess, "run", capture)
        sd.get_dish_status()
        assert "--connect-timeout" in seen["cmd"]
        assert "--max-time" in seen["cmd"]

    def test_reachability_probe_uses_connect_ex_not_exceptions(self):
        """An absent host must be a return value, not a raise — otherwise the
        probe itself becomes the thing that needs a try/except everywhere."""
        from utils.starlink_dish import _dish_reachable
        # TEST-NET-1 (RFC 5737) is guaranteed unroutable, so this is a real
        # negative without depending on the local network.
        assert _dish_reachable("192.0.2.1", 9200, timeout=0.5) is False
