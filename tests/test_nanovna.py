"""NanoVNA antenna analyzer — driver math and the TUI surface.

CONTEXT (2026-09-15): `src/utils/nanovna.py` was
`plugins/nanovna_analyzer/nanovna_device.py`, a complete 411-line driver
sitting in a GTK-era tree that nothing loads — `handlers/extensions.py` has
zero references to `plugins/`. It had NO tests and NO menu item. It now has
both. The operator has a SEESII NanoVNA-H4 to plug into the ecomm kit's Pi
(kiai) for field antenna testing, so the numbers below are the ones a HAM
will act on: getting SWR wrong is worse than having no analyzer.

Reference values are computed from first principles in the test names, not
copied from the implementation — a test that restates the code proves nothing.
"""

import json
import math
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from utils.nanovna import (  # noqa: E402
    NanoVNADevice, NanoVNAUnavailable, SweepPoint, SweepResult,
    format_impedance, format_swr,
)


def _pt(freq_mhz, re, im):
    return SweepPoint(frequency_hz=int(freq_mhz * 1e6), s11_real=re, s11_imag=im)


class TestReflectionMath:
    """Gamma -> SWR / return loss / impedance, against hand-computable cases."""

    def test_perfect_match_is_swr_1(self):
        """Gamma = 0 -> SWR = (1+0)/(1-0) = 1.0, and Z = Z0 = 50 ohms."""
        p = _pt(915, 0.0, 0.0)
        assert p.swr == pytest.approx(1.0)
        assert p.impedance.real == pytest.approx(50.0)
        assert p.impedance.imag == pytest.approx(0.0)

    def test_gamma_one_third_is_swr_2(self):
        """|G| = 1/3 -> SWR = (1+1/3)/(1-1/3) = 2.0 exactly."""
        p = _pt(915, 1.0 / 3.0, 0.0)
        assert p.swr == pytest.approx(2.0)

    def test_open_circuit_is_infinite_swr(self):
        """Gamma = 1 (open) -> mag >= 1 -> SWR is infinite, not a huge float."""
        p = _pt(915, 1.0, 0.0)
        assert p.swr == float('inf')

    def test_short_circuit_is_infinite_swr(self):
        """Gamma = -1 (short) is also |G| = 1."""
        p = _pt(915, -1.0, 0.0)
        assert p.swr == float('inf')

    def test_return_loss_of_half_gamma_is_about_6db(self):
        """RL = -20*log10(0.5) = 6.0206 dB."""
        p = _pt(915, 0.5, 0.0)
        assert p.return_loss_db == pytest.approx(-20 * math.log10(0.5))
        assert p.return_loss_db == pytest.approx(6.0206, abs=1e-3)

    def test_perfect_match_has_infinite_return_loss(self):
        assert _pt(915, 0.0, 0.0).return_loss_db == float('inf')

    def test_gamma_one_third_gives_100_ohms(self):
        """Z = Z0(1+G)/(1-G) = 50 * (4/3)/(2/3) = 100 ohms resistive."""
        p = _pt(915, 1.0 / 3.0, 0.0)
        assert p.resistance == pytest.approx(100.0)
        assert p.reactance == pytest.approx(0.0, abs=1e-9)

    def test_negative_gamma_gives_25_ohms(self):
        """G = -1/3 -> Z = 50 * (2/3)/(4/3) = 25 ohms."""
        p = _pt(915, -1.0 / 3.0, 0.0)
        assert p.resistance == pytest.approx(25.0)

    def test_reactive_load_reports_reactance(self):
        """A purely imaginary gamma must not read as resistive."""
        p = _pt(915, 0.0, 0.5)
        assert abs(p.reactance) > 1.0

    def test_phase_of_negative_real_gamma_is_180_degrees(self):
        assert abs(_pt(915, -0.5, 0.0).phase_degrees) == pytest.approx(180.0)

    def test_frequency_mhz_conversion(self):
        assert _pt(915.5, 0, 0).frequency_mhz == pytest.approx(915.5)


class TestSweepResult:

    def _result(self):
        return SweepResult(points=[
            _pt(902, 0.5, 0.0),          # SWR 3
            _pt(915, 1.0 / 3.0, 0.0),    # SWR 2
            _pt(920, 0.0, 0.0),          # SWR 1  <- best
            _pt(928, 0.5, 0.0),          # SWR 3
        ])

    def test_min_swr_finds_the_best_point_and_its_frequency(self):
        swr, freq = self._result().min_swr
        assert swr == pytest.approx(1.0)
        assert freq == pytest.approx(920.0)

    def test_best_match_frequency_matches_min_swr(self):
        r = self._result()
        assert r.best_match_frequency == r.min_swr[1]

    def test_frequency_range_is_first_and_last(self):
        lo, hi = self._result().frequency_range
        assert (lo, hi) == (pytest.approx(902.0), pytest.approx(928.0))

    def test_get_swr_at_frequency_snaps_to_nearest(self):
        assert self._result().get_swr_at_frequency(919.0) == pytest.approx(1.0)

    def test_empty_result_does_not_claim_a_perfect_match(self):
        """An empty sweep must not read as SWR 1.0 at 0 MHz — that is a
        measurement that never happened rendering as a great antenna."""
        empty = SweepResult()
        swr, freq = empty.min_swr
        assert swr == float('inf')
        assert empty.get_swr_at_frequency(915.0) is None


class TestFormatters:

    def test_swr_formatting(self):
        assert format_swr(1.5) == "1.50:1"
        assert format_swr(50.0) == ">10:1"

    def test_open_circuit_reads_differently_from_a_bad_match(self):
        """`swr > 10` is True for inf, so testing it first made the "Inf:1"
        branch unreachable and an OPEN antenna rendered exactly like a
        mismatched one. Different faults, different fixes: >10:1 means tune
        it, Inf:1 means the connector is not connected."""
        assert format_swr(float('inf')) == "Inf:1"
        assert format_swr(float('inf')) != format_swr(50.0)

    def test_impedance_sign_is_readable(self):
        assert format_impedance(complex(50, 12.3)) == "50.0 + j12.3"
        assert format_impedance(complex(50, -12.3)) == "50.0 - j12.3"
        assert format_impedance(complex(1e9, 0)) == "Open"


class TestSerialAbsenceIsHonest:
    """pyserial missing must not look like 'no analyzer connected'."""

    def test_find_devices_refuses_rather_than_returning_empty(self):
        with patch('utils.nanovna.HAS_SERIAL', False):
            with pytest.raises(NanoVNAUnavailable) as e:
                NanoVNADevice.find_devices()
        assert "pyserial" in str(e.value)

    def test_connect_refuses_rather_than_returning_false(self):
        with patch('utils.nanovna.HAS_SERIAL', False):
            with pytest.raises(NanoVNAUnavailable):
                NanoVNADevice("/dev/ttyACM0").connect()


class TestHandlerSurface:

    def _handler(self):
        from handlers.nanovna import NanoVNAHandler
        h = NanoVNAHandler()
        h.ctx = make_handler_context(dialog=FakeDialog())
        return h

    def test_registered_in_rf_sdr_with_the_vna_tag(self):
        from handlers import get_all_handlers
        found = [c for c in get_all_handlers() if c.__name__ == 'NanoVNAHandler']
        assert found, "NanoVNAHandler is not registered — the driver is dark again"
        h = found[0]()
        assert h.menu_section == 'rf_sdr'
        assert [t for t, _, _ in h.menu_items()] == ['vna']

    def test_menu_tag_is_in_the_section_ordering(self):
        """An unordered tag renders in an unpredictable tail."""
        from main import SECTION_ORDERINGS
        assert 'vna' in SECTION_ORDERINGS['rf_sdr']

    def test_unknown_action_is_reported_not_swallowed(self):
        h = self._handler()
        h.execute("nonsense")
        assert any(c[0] == 'msgbox' and 'Not wired' in c[1][0]
                   for c in h.ctx.dialog.calls)

    def test_verdict_never_calls_an_open_antenna_good(self):
        from handlers.nanovna import NanoVNAHandler
        assert "good" not in NanoVNAHandler._verdict(float('inf')).lower()
        assert "OPEN" in NanoVNAHandler._verdict(float('inf'))
        assert "good" in NanoVNAHandler._verdict(1.1)
        assert "POOR" in NanoVNAHandler._verdict(5.0)

    def test_display_sampling_keeps_both_ends_of_the_band(self):
        """Truncating to the first N would hide the top of the sweep — the
        half of the band most likely to be where the antenna falls apart."""
        from handlers.nanovna import NanoVNAHandler
        pts = [_pt(900 + i * 0.5, 0, 0) for i in range(101)]
        shown = NanoVNAHandler._sample(pts, 12)
        assert len(shown) == 12
        assert shown[0] is pts[0]
        assert shown[-1] is pts[-1]

    def test_sampling_passes_short_lists_through(self):
        from handlers.nanovna import NanoVNAHandler
        pts = [_pt(900, 0, 0), _pt(901, 0, 0)]
        assert NanoVNAHandler._sample(pts, 12) == pts


class TestBaselineRoundTrip:
    """The field-testing feature: save a known-good sweep, compare later."""

    def _handler(self, tmp_path):
        from handlers.nanovna import NanoVNAHandler
        h = NanoVNAHandler()
        h.ctx = make_handler_context(dialog=FakeDialog())
        h._baseline_dir = lambda: tmp_path            # type: ignore
        return h

    def test_save_then_load_preserves_the_measurement(self, tmp_path):
        h = self._handler(tmp_path)
        original = SweepResult(points=[_pt(915, 1.0 / 3.0, 0.0)],
                               timestamp=1234.0, device_info="NanoVNA-H4")
        ok, detail = h._save_baseline("kiai-whip", original)
        assert ok, detail

        loaded, err = h._load_baseline(tmp_path / "kiai-whip.json")
        assert loaded is not None, err
        assert loaded.min_swr[0] == pytest.approx(original.min_swr[0])
        assert loaded.min_swr[1] == pytest.approx(original.min_swr[1])
        assert loaded.device_info == "NanoVNA-H4"

    def test_name_is_sanitised(self, tmp_path):
        h = self._handler(tmp_path)
        ok, detail = h._save_baseline("../../etc/passwd", SweepResult(
            points=[_pt(915, 0, 0)]))
        assert ok
        assert "etcpasswd" in detail
        assert ".." not in os.path.basename(detail)

    def test_empty_name_is_refused(self, tmp_path):
        h = self._handler(tmp_path)
        ok, msg = h._save_baseline("///", SweepResult(points=[_pt(915, 0, 0)]))
        assert not ok and "at least one" in msg

    def test_unwritable_destination_reports_rather_than_claiming_success(self, tmp_path):
        h = self._handler(tmp_path)
        h._baseline_dir = lambda: tmp_path / "nope" / "deeper"  # type: ignore
        with patch('pathlib.Path.mkdir', side_effect=OSError("read-only")):
            ok, msg = h._save_baseline("x", SweepResult(points=[_pt(915, 0, 0)]))
        assert not ok and "OSError" in msg

    def test_corrupt_baseline_is_named_not_crashed(self, tmp_path):
        h = self._handler(tmp_path)
        (tmp_path / "bad.json").write_text("{not json")
        loaded, err = h._load_baseline(tmp_path / "bad.json")
        assert loaded is None and err

    def test_baseline_with_no_points_is_refused(self, tmp_path):
        h = self._handler(tmp_path)
        (tmp_path / "empty.json").write_text(json.dumps(
            {"name": "empty", "saved_at": 1.0, "device": "", "points": []}))
        loaded, err = h._load_baseline(tmp_path / "empty.json")
        assert loaded is None
        assert "no sweep points" in err
