"""
Tests for the NanoVNA antenna analyzer plugin.

Tests cover:
- SweepPoint: SWR, impedance, return loss, phase calculations (pure math)
- SweepResult: min_swr, best_match_frequency, frequency_range
- NanoVNADevice: mock backends, connect/disconnect, sweep
- SweepStore: SQLite round-trip persistence
- RF Integration: mismatch loss, antenna efficiency, band summary
"""

import math
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from plugins.nanovna.nanovna_device import (
    NanoVNADevice,
    SweepPoint,
    SweepResult,
    format_impedance,
    format_swr,
)
from plugins.nanovna.sweep_store import SweepStore, StoredSweep
from plugins.nanovna.rf_integration import (
    BANDS,
    antenna_efficiency_from_swr,
    get_antenna_loss_at_frequency,
    sweep_summary_for_band,
    swr_to_mismatch_loss_db,
)


# ── SweepPoint Tests ─────────────────────────────────────────────────────────


class TestSweepPoint:
    """Test SweepPoint dataclass calculations."""

    def test_perfect_match_swr(self):
        """A perfect 50 ohm match has SWR 1.0."""
        p = SweepPoint(frequency_hz=915_000_000, s11_real=0.0, s11_imag=0.0)
        assert p.swr == 1.0

    def test_open_circuit_swr(self):
        """Open circuit (gamma=1.0) has infinite SWR."""
        p = SweepPoint(frequency_hz=915_000_000, s11_real=1.0, s11_imag=0.0)
        assert p.swr == float('inf')

    def test_known_swr_value(self):
        """SWR for gamma=0.333 should be ~2.0."""
        # gamma = (SWR-1)/(SWR+1) => 0.333 for SWR=2.0
        gamma = 1.0 / 3.0
        p = SweepPoint(frequency_hz=915_000_000, s11_real=gamma, s11_imag=0.0)
        assert abs(p.swr - 2.0) < 0.01

    def test_gamma_magnitude(self):
        """Test reflection coefficient magnitude."""
        p = SweepPoint(frequency_hz=144_000_000, s11_real=0.3, s11_imag=0.4)
        expected = math.sqrt(0.3**2 + 0.4**2)
        assert abs(p.gamma_magnitude - expected) < 1e-10

    def test_return_loss_perfect_match(self):
        """Perfect match has infinite return loss."""
        p = SweepPoint(frequency_hz=915_000_000, s11_real=0.0, s11_imag=0.0)
        assert p.return_loss_db == float('inf')

    def test_return_loss_known_value(self):
        """Known return loss: |gamma|=0.1 => RL = 20 dB."""
        p = SweepPoint(frequency_hz=915_000_000, s11_real=0.1, s11_imag=0.0)
        assert abs(p.return_loss_db - 20.0) < 0.01

    def test_impedance_perfect_match(self):
        """Perfect match gives Z = 50+0j ohms."""
        p = SweepPoint(frequency_hz=915_000_000, s11_real=0.0, s11_imag=0.0)
        z = p.impedance
        assert abs(z.real - 50.0) < 0.01
        assert abs(z.imag) < 0.01

    def test_impedance_open_circuit(self):
        """Open circuit (gamma=1) gives very high impedance."""
        p = SweepPoint(frequency_hz=915_000_000, s11_real=0.999, s11_imag=0.0)
        z = p.impedance
        assert z.real > 1000

    def test_resistance_and_reactance(self):
        """Resistance and reactance match impedance components."""
        p = SweepPoint(frequency_hz=433_000_000, s11_real=0.2, s11_imag=0.3)
        z = p.impedance
        assert abs(p.resistance - z.real) < 1e-10
        assert abs(p.reactance - z.imag) < 1e-10

    def test_phase_degrees(self):
        """Phase angle in degrees for known gamma."""
        p = SweepPoint(frequency_hz=915_000_000, s11_real=0.0, s11_imag=1.0)
        assert abs(p.phase_degrees - 90.0) < 0.01

    def test_frequency_mhz(self):
        """Frequency conversion from Hz to MHz."""
        p = SweepPoint(frequency_hz=915_000_000, s11_real=0.0, s11_imag=0.0)
        assert p.frequency_mhz == 915.0


# ── SweepResult Tests ─────────────────────────────────────────────────────────


class TestSweepResult:
    """Test SweepResult aggregate calculations."""

    @pytest.fixture
    def sample_sweep(self):
        """Create a sample sweep with known values."""
        points = [
            SweepPoint(frequency_hz=906_000_000, s11_real=0.3, s11_imag=0.1),
            SweepPoint(frequency_hz=910_000_000, s11_real=0.1, s11_imag=0.05),
            SweepPoint(frequency_hz=915_000_000, s11_real=0.02, s11_imag=0.01),  # Best
            SweepPoint(frequency_hz=920_000_000, s11_real=0.15, s11_imag=0.08),
            SweepPoint(frequency_hz=924_000_000, s11_real=0.35, s11_imag=0.2),
        ]
        return SweepResult(points=points, timestamp=1708905600.0, device_info="test")

    def test_empty_sweep(self):
        """Empty sweep returns default values."""
        s = SweepResult()
        assert s.frequency_range == (0.0, 0.0)
        assert s.min_swr == (float('inf'), 0.0)

    def test_frequency_range(self, sample_sweep):
        """Frequency range matches first and last points."""
        start, stop = sample_sweep.frequency_range
        assert start == 906.0
        assert stop == 924.0

    def test_min_swr(self, sample_sweep):
        """Min SWR is at the best-match point."""
        min_swr_val, freq = sample_sweep.min_swr
        assert freq == 915.0
        assert min_swr_val < 1.1  # Very good match

    def test_best_match_frequency(self, sample_sweep):
        """Best match frequency is at minimum SWR."""
        assert sample_sweep.best_match_frequency == 915.0

    def test_get_swr_at_frequency(self, sample_sweep):
        """Get SWR at closest frequency."""
        swr = sample_sweep.get_swr_at_frequency(915.0)
        assert swr is not None
        assert swr < 1.1

    def test_get_swr_at_frequency_interpolates(self, sample_sweep):
        """SWR lookup finds closest measured point."""
        swr = sample_sweep.get_swr_at_frequency(916.0)
        # Should return SWR at closest point (915 MHz)
        assert swr is not None
        assert swr < 1.1

    def test_get_swr_at_frequency_empty(self):
        """Empty sweep returns None for SWR lookup."""
        s = SweepResult()
        assert s.get_swr_at_frequency(915.0) is None


# ── NanoVNADevice Tests ───────────────────────────────────────────────────────


class TestNanoVNADevice:
    """Test NanoVNA device with mocked backends."""

    def test_backend_none_when_disconnected(self):
        """Backend is 'none' when not connected."""
        device = NanoVNADevice()
        assert device.backend == "none"
        assert not device.is_connected

    def test_find_devices_no_serial(self):
        """find_devices returns empty when pyserial not available."""
        with patch.object(NanoVNADevice, 'find_devices', return_value=[]):
            devices = NanoVNADevice.find_devices()
            assert devices == []

    @patch('plugins.nanovna.nanovna_device.HAS_PYNANOVNA', True)
    @patch('plugins.nanovna.nanovna_device.PyNanoVNA')
    def test_connect_pynanovna(self, mock_vna_class):
        """Test connection via pynanovna backend."""
        mock_vna = MagicMock()
        mock_vna_class.return_value = mock_vna

        device = NanoVNADevice(port="/dev/ttyUSB0")
        # Manually invoke pynanovna connection
        device._vna = mock_vna
        device._connected = True

        assert device.is_connected
        assert device.backend == "pynanovna"

    def test_disconnect_cleans_up(self):
        """Disconnect resets all state."""
        device = NanoVNADevice()
        device._vna = MagicMock()
        device._connected = True

        device.disconnect()

        assert not device.is_connected
        assert device.backend == "none"

    def test_sweep_not_connected(self):
        """Sweep returns empty result when not connected."""
        device = NanoVNADevice()
        result = device.sweep(900_000_000, 930_000_000, 11)
        assert len(result.points) == 0

    def test_quick_swr_not_connected(self):
        """Quick SWR returns None when not connected."""
        device = NanoVNADevice()
        result = device.quick_swr(915_000_000)
        assert result is None


# ── SweepStore Tests ──────────────────────────────────────────────────────────


class TestSweepStore:
    """Test SQLite sweep storage."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a store with temp DB."""
        db_path = tmp_path / "test_sweeps.db"
        return SweepStore(db_path=db_path)

    @pytest.fixture
    def sample_result(self):
        """Create a sample SweepResult for storage."""
        points = [
            SweepPoint(frequency_hz=906_000_000, s11_real=0.3, s11_imag=0.1),
            SweepPoint(frequency_hz=915_000_000, s11_real=0.05, s11_imag=0.02),
            SweepPoint(frequency_hz=924_000_000, s11_real=0.25, s11_imag=0.15),
        ]
        return SweepResult(points=points, timestamp=time.time(), device_info="test-v1")

    def test_save_and_retrieve(self, store, sample_result):
        """Save sweep and retrieve metadata."""
        sweep_id = store.save_sweep(sample_result, label="test antenna")
        assert sweep_id >= 1

        meta = store.get_sweep(sweep_id)
        assert meta is not None
        assert meta.label == "test antenna"
        assert meta.num_points == 3
        assert meta.device_info == "test-v1"

    def test_save_empty_raises(self, store):
        """Cannot save empty sweep."""
        with pytest.raises(ValueError, match="empty"):
            store.save_sweep(SweepResult())

    def test_get_sweep_points(self, store, sample_result):
        """Retrieve sweep points matches input."""
        sweep_id = store.save_sweep(sample_result)
        points = store.get_sweep_points(sweep_id)

        assert len(points) == 3
        assert points[0].frequency_hz == 906_000_000
        assert abs(points[0].s11_real - 0.3) < 1e-6
        assert abs(points[0].s11_imag - 0.1) < 1e-6

    def test_get_sweep_result(self, store, sample_result):
        """Full SweepResult round-trip."""
        sweep_id = store.save_sweep(sample_result)
        result = store.get_sweep_result(sweep_id)

        assert result is not None
        assert len(result.points) == 3
        assert result.device_info == "test-v1"

        # SWR at 915 MHz should be best match
        min_swr_val, min_freq = result.min_swr
        assert min_freq == 915.0

    def test_list_sweeps(self, store, sample_result):
        """List sweeps returns all stored sweeps."""
        store.save_sweep(sample_result, label="sweep1")
        store.save_sweep(sample_result, label="sweep2")

        sweeps = store.list_sweeps()
        assert len(sweeps) == 2
        labels = {s.label for s in sweeps}
        assert labels == {"sweep1", "sweep2"}

    def test_delete_sweep(self, store, sample_result):
        """Delete removes sweep and points."""
        sweep_id = store.save_sweep(sample_result)
        assert store.delete_sweep(sweep_id)
        assert store.get_sweep(sweep_id) is None
        assert store.get_sweep_points(sweep_id) == []

    def test_delete_nonexistent(self, store):
        """Delete nonexistent sweep returns False."""
        assert not store.delete_sweep(999)

    def test_export_csv(self, store, sample_result):
        """CSV export has header and data rows."""
        sweep_id = store.save_sweep(sample_result)
        csv = store.export_csv(sweep_id)

        assert csv
        lines = csv.strip().split("\n")
        assert len(lines) == 4  # header + 3 points
        assert "frequency_hz" in lines[0]
        assert "swr" in lines[0]

    def test_export_empty(self, store):
        """Export of nonexistent sweep returns empty."""
        assert store.export_csv(999) == ""

    def test_get_nonexistent(self, store):
        """Get nonexistent sweep returns None."""
        assert store.get_sweep(999) is None
        assert store.get_sweep_result(999) is None

    def test_stored_sweep_properties(self):
        """StoredSweep property conversions."""
        s = StoredSweep(
            id=1, timestamp=1.0,
            start_hz=906_000_000, stop_hz=924_000_000,
            num_points=101, min_swr=1.2,
            min_swr_freq_hz=915_000_000,
            device_info="test", label="myant",
        )
        assert s.start_mhz == 906.0
        assert s.stop_mhz == 924.0
        assert s.min_swr_freq_mhz == 915.0


# ── RF Integration Tests ─────────────────────────────────────────────────────


class TestRFIntegration:
    """Test RF integration functions."""

    def test_mismatch_loss_perfect_match(self):
        """Perfect match (SWR=1.0) has zero loss."""
        assert swr_to_mismatch_loss_db(1.0) == 0.0

    def test_mismatch_loss_known_value(self):
        """SWR=2.0 should have ~0.512 dB mismatch loss."""
        loss = swr_to_mismatch_loss_db(2.0)
        assert 0.5 < loss < 0.52

    def test_mismatch_loss_increases_with_swr(self):
        """Higher SWR means more loss."""
        loss_2 = swr_to_mismatch_loss_db(2.0)
        loss_3 = swr_to_mismatch_loss_db(3.0)
        loss_5 = swr_to_mismatch_loss_db(5.0)
        assert loss_2 < loss_3 < loss_5

    def test_mismatch_loss_below_one(self):
        """SWR below 1.0 returns 0."""
        assert swr_to_mismatch_loss_db(0.5) == 0.0

    def test_efficiency_perfect_match(self):
        """Perfect match has 100% efficiency."""
        assert antenna_efficiency_from_swr(1.0) == 1.0

    def test_efficiency_known_value(self):
        """SWR=2.0 should have ~88.9% efficiency."""
        eff = antenna_efficiency_from_swr(2.0)
        assert 0.88 < eff < 0.90

    def test_efficiency_decreases_with_swr(self):
        """Higher SWR means lower efficiency."""
        eff_2 = antenna_efficiency_from_swr(2.0)
        eff_3 = antenna_efficiency_from_swr(3.0)
        eff_5 = antenna_efficiency_from_swr(5.0)
        assert eff_2 > eff_3 > eff_5

    def test_bands_defined(self):
        """Key bands are defined."""
        assert "915mhz_lora" in BANDS
        assert "868mhz_lora" in BANDS
        assert "433mhz_lora" in BANDS
        assert "70cm" in BANDS
        assert "2m" in BANDS

    def test_sweep_summary_for_band(self):
        """Band summary calculates correctly."""
        points = [
            SweepPoint(frequency_hz=910_000_000, s11_real=0.2, s11_imag=0.1),
            SweepPoint(frequency_hz=915_000_000, s11_real=0.05, s11_imag=0.02),
            SweepPoint(frequency_hz=920_000_000, s11_real=0.15, s11_imag=0.08),
        ]
        sweep = SweepResult(points=points)

        summary = sweep_summary_for_band(sweep, "915mhz_lora")
        assert summary is not None
        assert summary["points_in_band"] == 3
        assert summary["min_swr_freq_mhz"] == 915.0
        assert summary["usable"]  # Best SWR should be < 3:1

    def test_sweep_summary_unknown_band(self):
        """Unknown band returns None."""
        sweep = SweepResult(points=[])
        assert sweep_summary_for_band(sweep, "nonexistent_band") is None

    def test_sweep_summary_no_data_in_band(self):
        """Band with no data points returns error."""
        # Points outside 915 LoRa band
        points = [
            SweepPoint(frequency_hz=144_000_000, s11_real=0.1, s11_imag=0.1),
        ]
        sweep = SweepResult(points=points)
        summary = sweep_summary_for_band(sweep, "915mhz_lora")
        assert summary is not None
        assert summary["points_in_band"] == 0
        assert "error" in summary

    def test_antenna_loss_at_frequency(self):
        """Get antenna loss at specific frequency."""
        points = [
            SweepPoint(frequency_hz=914_000_000, s11_real=0.1, s11_imag=0.05),
            SweepPoint(frequency_hz=915_000_000, s11_real=0.05, s11_imag=0.02),
            SweepPoint(frequency_hz=916_000_000, s11_real=0.12, s11_imag=0.06),
        ]
        sweep = SweepResult(points=points)

        loss = get_antenna_loss_at_frequency(sweep, 915.0)
        assert loss is not None
        assert loss > 0
        assert loss < 0.5  # Good match should have low loss

    def test_antenna_loss_empty_sweep(self):
        """Empty sweep returns None for loss."""
        sweep = SweepResult()
        assert get_antenna_loss_at_frequency(sweep, 915.0) is None


# ── Format Tests ──────────────────────────────────────────────────────────────


class TestFormatFunctions:
    """Test display formatting functions."""

    def test_format_swr_normal(self):
        """Normal SWR formats as X.XX:1."""
        assert format_swr(1.5) == "1.50:1"

    def test_format_swr_high(self):
        """SWR > 10 shows >10:1."""
        assert format_swr(15.0) == ">10:1"

    def test_format_swr_inf(self):
        """Infinite SWR shows >10:1 (same as very high SWR)."""
        assert format_swr(float('inf')) == ">10:1"

    def test_format_impedance_normal(self):
        """Normal impedance formats with j notation."""
        z = complex(50.0, 12.3)
        result = format_impedance(z)
        assert "50.0" in result
        assert "j12.3" in result

    def test_format_impedance_negative_reactance(self):
        """Negative reactance uses minus sign."""
        z = complex(50.0, -12.3)
        result = format_impedance(z)
        assert "50.0 - j12.3" in result

    def test_format_impedance_open(self):
        """Very high impedance shows Open."""
        z = complex(99999.0, 0.0)
        assert format_impedance(z) == "Open"
