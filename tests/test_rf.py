"""
Tests for RF calculation utilities (src/utils/rf.py).

Covers: haversine distance, free space path loss, Fresnel zone,
earth bulge, link budget, SNR estimation, signal classification,
cable loss, detailed link budget, knife-edge diffraction,
environment-aware propagation, radio horizon, and batch operations.

Reference data:
- Haversine: well-known geographic distances
- FSPL: standard RF engineering formula 20*log10(d) + 20*log10(f) - 27.55
- Fresnel: 17.3 * sqrt(d / (4*f))
- Semtech datasheets for LoRa sensitivity/SNR thresholds
"""

import math
import sys
import pytest
from unittest.mock import patch

# Ensure src is importable
sys.path.insert(0, 'src')

from utils.rf import (
    haversine_distance,
    fresnel_radius,
    free_space_path_loss,
    earth_bulge,
    link_budget,
    snr_estimate,
    classify_signal,
    signal_quality_percent,
    analyze_signal,
    calculate_cable_loss,
    effective_radiated_power,
    required_antenna_height,
    detailed_link_budget,
    knife_edge_diffraction,
    multi_obstacle_loss,
    rx_sensitivity,
    log_distance_path_loss,
    realistic_max_range,
    radio_horizon_km,
    processing_gain_db,
    capture_effect,
    batch_haversine,
    batch_link_quality,
    is_fast_available,
    SignalQuality,
    SignalMetrics,
    LinkBudgetResult,
    DeployEnvironment,
    BuildingType,
    ENVIRONMENT_PARAMS,
    BUILDING_PENETRATION_DB,
    SNR_THRESHOLD_DB,
    LORA_SENSITIVITY_DBM,
    CABLE_LOSS_DB_PER_M,
    CONNECTOR_LOSS_DB,
)


# ============================================================================
# Haversine Distance Tests
# ============================================================================

class TestHaversineDistance:
    """Test haversine distance calculation with known geographic distances."""

    def test_sf_to_la(self):
        """SF to LA is approximately 559 km."""
        result = haversine_distance(37.7749, -122.4194, 34.0522, -118.2437)
        # Result is in meters
        assert 550_000 < result < 570_000

    def test_honolulu_to_maui(self):
        """Honolulu to Kahului, Maui is approximately 160 km."""
        result = haversine_distance(21.3069, -157.8583, 20.8893, -156.4729)
        assert 140_000 < result < 180_000

    def test_same_point(self):
        """Distance from a point to itself should be zero."""
        result = haversine_distance(37.7749, -122.4194, 37.7749, -122.4194)
        assert result == pytest.approx(0.0, abs=0.01)

    def test_equator_one_degree(self):
        """One degree of longitude at equator is ~111.32 km."""
        result = haversine_distance(0.0, 0.0, 0.0, 1.0)
        assert 111_000 < result < 112_000

    def test_poles(self):
        """North pole to south pole is half the Earth's circumference (~20,004 km)."""
        result = haversine_distance(90.0, 0.0, -90.0, 0.0)
        assert 20_000_000 < result < 20_050_000

    def test_antipodal_points(self):
        """Antipodal points should be half circumference apart."""
        result = haversine_distance(0.0, 0.0, 0.0, 180.0)
        assert 20_000_000 < result < 20_050_000

    def test_short_distance(self):
        """Short distance (~100m) should be calculable."""
        # ~0.001 degrees latitude = ~111m
        result = haversine_distance(37.7749, -122.4194, 37.7759, -122.4194)
        assert 90 < result < 130

    def test_negative_longitude(self):
        """Crossing prime meridian should work correctly."""
        result = haversine_distance(51.5074, -0.1278, 48.8566, 2.3522)
        # London to Paris ~344 km
        assert 330_000 < result < 360_000

    def test_symmetry(self):
        """Distance A->B should equal distance B->A."""
        d1 = haversine_distance(37.7749, -122.4194, 34.0522, -118.2437)
        d2 = haversine_distance(34.0522, -118.2437, 37.7749, -122.4194)
        assert d1 == pytest.approx(d2, rel=1e-10)


# ============================================================================
# Free Space Path Loss Tests
# ============================================================================

class TestFreeSpacePathLoss:
    """Test FSPL calculation: 20*log10(d) + 20*log10(f) - 27.55"""

    def test_1km_915mhz(self):
        """FSPL at 1 km, 915 MHz should be ~91.7 dB."""
        result = free_space_path_loss(1000, 915)
        assert 91 < result < 93

    def test_10km_915mhz(self):
        """FSPL at 10 km should be 20 dB more than 1 km."""
        fspl_1km = free_space_path_loss(1000, 915)
        fspl_10km = free_space_path_loss(10000, 915)
        assert fspl_10km == pytest.approx(fspl_1km + 20.0, abs=0.1)

    def test_double_distance_adds_6db(self):
        """Doubling distance adds ~6 dB path loss."""
        fspl_1 = free_space_path_loss(1000, 915)
        fspl_2 = free_space_path_loss(2000, 915)
        assert fspl_2 - fspl_1 == pytest.approx(6.02, abs=0.1)

    def test_double_frequency_adds_6db(self):
        """Doubling frequency adds ~6 dB path loss."""
        fspl_915 = free_space_path_loss(1000, 915)
        fspl_1830 = free_space_path_loss(1000, 1830)
        assert fspl_1830 - fspl_915 == pytest.approx(6.02, abs=0.1)

    def test_zero_distance(self):
        """Zero distance returns 0.0."""
        assert free_space_path_loss(0, 915) == 0.0

    def test_negative_distance(self):
        """Negative distance returns 0.0."""
        assert free_space_path_loss(-100, 915) == 0.0

    def test_zero_frequency(self):
        """Zero frequency returns 0.0."""
        assert free_space_path_loss(1000, 0) == 0.0

    def test_1m_reference(self):
        """FSPL at 1m, 915 MHz is the reference value."""
        result = free_space_path_loss(1, 915)
        # 20*log10(1) + 20*log10(915) - 27.55 = 0 + 59.23 - 27.55 = 31.68
        assert result == pytest.approx(31.68, abs=0.1)


# ============================================================================
# Fresnel Zone Tests
# ============================================================================

class TestFresnelRadius:
    """Test first Fresnel zone radius: 17.3 * sqrt(d / (4*f))"""

    def test_1km_915mhz(self):
        """Fresnel radius at 1 km, 0.915 GHz."""
        result = fresnel_radius(1.0, 0.915)
        # 17.3 * sqrt(1.0 / (4 * 0.915)) = 17.3 * 0.523 = 9.05
        assert 8.5 < result < 9.5

    def test_5km_915mhz(self):
        """Fresnel radius at 5 km, 0.915 GHz."""
        result = fresnel_radius(5.0, 0.915)
        # 17.3 * sqrt(5.0 / (4 * 0.915)) = 17.3 * 1.170 = 20.24
        assert 19.5 < result < 21.0

    def test_longer_distance_larger_zone(self):
        """Fresnel zone increases with sqrt of distance."""
        r1 = fresnel_radius(1.0, 0.915)
        r4 = fresnel_radius(4.0, 0.915)
        # 4x distance -> 2x Fresnel radius
        assert r4 == pytest.approx(r1 * 2.0, rel=0.01)

    def test_higher_freq_smaller_zone(self):
        """Higher frequency means smaller Fresnel zone."""
        r_low = fresnel_radius(5.0, 0.433)
        r_high = fresnel_radius(5.0, 0.915)
        assert r_low > r_high

    def test_zero_distance(self):
        """Zero distance returns 0.0."""
        assert fresnel_radius(0.0, 0.915) == 0.0

    def test_negative_distance(self):
        """Negative distance returns 0.0."""
        assert fresnel_radius(-1.0, 0.915) == 0.0

    def test_zero_frequency(self):
        """Zero frequency returns 0.0."""
        assert fresnel_radius(1.0, 0.0) == 0.0


# ============================================================================
# Earth Bulge Tests
# ============================================================================

class TestEarthBulge:
    """Test Earth bulge calculation using 4/3 Earth radius."""

    def test_10km_path(self):
        """Earth bulge at 10 km should be ~1.5m."""
        result = earth_bulge(10000)
        assert 1.0 < result < 2.0

    def test_50km_path(self):
        """Earth bulge at 50 km should be ~37m."""
        result = earth_bulge(50000)
        assert 30 < result < 45

    def test_zero_distance(self):
        """Zero distance has zero bulge."""
        assert earth_bulge(0) == 0.0

    def test_quadratic_scaling(self):
        """Earth bulge scales with distance squared."""
        b1 = earth_bulge(10000)
        b2 = earth_bulge(20000)
        assert b2 == pytest.approx(b1 * 4.0, rel=0.01)


# ============================================================================
# Link Budget Tests
# ============================================================================

class TestLinkBudget:
    """Test link budget: rx_power = tx_power + tx_gain + rx_gain - FSPL"""

    def test_basic_link(self):
        """Basic link budget at 1 km, 915 MHz."""
        # 20 + 2 + 2 - 91.7 = -67.7 dBm
        result = link_budget(20.0, 2.0, 2.0, 1000, 915)
        assert -70 < result < -65

    def test_zero_gains(self):
        """Link budget with unity gain antennas."""
        result = link_budget(20.0, 0.0, 0.0, 1000, 915)
        # 20 + 0 + 0 - FSPL
        fspl = free_space_path_loss(1000, 915)
        assert result == pytest.approx(20.0 - fspl, abs=0.01)

    def test_higher_power_stronger_signal(self):
        """More TX power means stronger received signal."""
        rx_20 = link_budget(20.0, 2.0, 2.0, 1000, 915)
        rx_30 = link_budget(30.0, 2.0, 2.0, 1000, 915)
        assert rx_30 - rx_20 == pytest.approx(10.0, abs=0.01)

    def test_farther_distance_weaker_signal(self):
        """Greater distance means weaker received signal."""
        rx_1km = link_budget(20.0, 2.0, 2.0, 1000, 915)
        rx_10km = link_budget(20.0, 2.0, 2.0, 10000, 915)
        assert rx_1km > rx_10km
        assert rx_1km - rx_10km == pytest.approx(20.0, abs=0.2)


# ============================================================================
# SNR Estimate Tests
# ============================================================================

class TestSnrEstimate:
    """Test SNR = rx_power - noise_floor"""

    def test_basic_snr(self):
        """SNR with -100 dBm signal and -120 noise floor is 20 dB."""
        assert snr_estimate(-100.0) == pytest.approx(20.0)

    def test_at_noise_floor(self):
        """Signal at noise floor has 0 dB SNR."""
        assert snr_estimate(-120.0) == pytest.approx(0.0)

    def test_below_noise_floor(self):
        """Signal below noise floor has negative SNR."""
        assert snr_estimate(-130.0) == pytest.approx(-10.0)

    def test_custom_noise_floor(self):
        """Custom noise floor changes SNR."""
        assert snr_estimate(-100.0, -110.0) == pytest.approx(10.0)


# ============================================================================
# Signal Classification Tests
# ============================================================================

class TestClassifySignal:
    """Test signal quality classification based on SNR and RSSI."""

    def test_excellent_signal(self):
        """Strong SNR and RSSI = EXCELLENT."""
        assert classify_signal(5.0, -80.0) == SignalQuality.EXCELLENT

    def test_good_signal(self):
        """Moderate SNR and RSSI = GOOD."""
        assert classify_signal(-5.0, -110.0) == SignalQuality.GOOD

    def test_fair_signal(self):
        """Weak but usable = FAIR."""
        assert classify_signal(-10.0, -120.0) == SignalQuality.FAIR

    def test_bad_signal(self):
        """Very weak = BAD."""
        assert classify_signal(-18.0, -130.0) == SignalQuality.BAD

    def test_no_signal(self):
        """Below sensitivity = NONE."""
        assert classify_signal(-20.0, -140.0) == SignalQuality.NONE

    def test_boundary_excellent_good(self):
        """At excellent threshold boundary."""
        result = classify_signal(-3.0, -100.0)
        assert result == SignalQuality.EXCELLENT

    def test_high_snr_low_rssi(self):
        """High SNR but low RSSI isn't excellent."""
        result = classify_signal(5.0, -125.0)
        # SNR is excellent but RSSI is only fair range
        assert result in (SignalQuality.FAIR, SignalQuality.GOOD)


class TestSignalQualityPercent:
    """Test signal quality as percentage."""

    def test_strong_signal(self):
        """Strong signal should be near 100%."""
        pct = signal_quality_percent(10.0, -70.0)
        assert 90 <= pct <= 100

    def test_at_noise_floor(self):
        """Signal at noise floor should be low percent."""
        pct = signal_quality_percent(-20.0, -137.0)
        assert pct == 0

    def test_moderate_signal(self):
        """Mid-range signal should be around 50%."""
        pct = signal_quality_percent(-5.0, -103.0)
        assert 30 <= pct <= 70

    def test_returns_integer(self):
        """Should return an integer."""
        result = signal_quality_percent(0.0, -100.0)
        assert isinstance(result, int)


class TestAnalyzeSignal:
    """Test comprehensive signal analysis."""

    def test_returns_signal_metrics(self):
        """Should return a SignalMetrics namedtuple."""
        result = analyze_signal(-105.0, -3.0, 11)
        assert isinstance(result, SignalMetrics)

    def test_excellent_signal_description(self):
        """Excellent signal should mention link margin."""
        result = analyze_signal(-90.0, 5.0, 11)
        assert result.quality == SignalQuality.EXCELLENT
        assert "link margin" in result.description.lower()

    def test_link_margin_calculation(self):
        """Link margin = RSSI - sensitivity."""
        result = analyze_signal(-105.0, -3.0, 11)
        expected_margin = -105.0 - LORA_SENSITIVITY_DBM[11]
        assert result.link_margin_db == pytest.approx(expected_margin)

    def test_default_sf(self):
        """Default spreading factor is 11."""
        result = analyze_signal(-105.0, -3.0)
        expected_margin = -105.0 - LORA_SENSITIVITY_DBM[11]
        assert result.link_margin_db == pytest.approx(expected_margin)


# ============================================================================
# Cable Loss Tests
# ============================================================================

class TestCableLoss:
    """Test cable and connector loss calculations."""

    def test_rg58_3m(self):
        """RG58, 3m, 2 connectors."""
        result = calculate_cable_loss('rg58', 3.0, connectors=2)
        # 0.5 * 3 + 0.1 * 2 = 1.7 dB
        assert result == pytest.approx(1.7, abs=0.1)

    def test_lmr400_1m(self):
        """LMR-400, 1m, 2 connectors."""
        result = calculate_cable_loss('lmr400', 1.0, connectors=2)
        # 0.15 * 1 + 0.1 * 2 = 0.35 dB
        assert result == pytest.approx(0.35, abs=0.05)

    def test_zero_length(self):
        """Zero length cable still has connector loss."""
        result = calculate_cable_loss('rg58', 0.0, connectors=2)
        assert result == pytest.approx(0.2, abs=0.05)

    def test_no_connectors(self):
        """Cable with no connectors = cable loss only."""
        result = calculate_cable_loss('rg58', 2.0, connectors=0)
        assert result == pytest.approx(1.0, abs=0.05)

    def test_unknown_cable_defaults(self):
        """Unknown cable type defaults to 0.5 dB/m."""
        result = calculate_cable_loss('unknown_cable', 2.0, connectors=0)
        assert result == pytest.approx(1.0, abs=0.05)

    def test_longer_cable_more_loss(self):
        """Longer cable = more loss."""
        short = calculate_cable_loss('rg58', 1.0)
        long = calculate_cable_loss('rg58', 10.0)
        assert long > short


class TestEffectiveRadiatedPower:
    """Test ERP calculation."""

    def test_basic_erp(self):
        """ERP = tx_power + antenna_gain - cable_loss."""
        result = effective_radiated_power(20.0, 6.0, 1.5)
        assert result == pytest.approx(24.5)

    def test_no_loss(self):
        """Zero cable loss."""
        result = effective_radiated_power(20.0, 2.0)
        assert result == pytest.approx(22.0)


# ============================================================================
# Detailed Link Budget Tests
# ============================================================================

class TestDetailedLinkBudget:
    """Test the full link budget analysis."""

    def test_returns_dataclass(self):
        """Should return a LinkBudgetResult."""
        result = detailed_link_budget()
        assert isinstance(result, LinkBudgetResult)

    def test_default_params(self):
        """Default parameters should produce a reasonable result."""
        result = detailed_link_budget()
        assert result.tx_power_dbm == 20.0
        assert result.distance_m == 1000.0
        assert result.freq_mhz == 906.875

    def test_summary_output(self):
        """Summary should return a list of strings."""
        result = detailed_link_budget()
        summary = result.summary()
        assert isinstance(summary, list)
        assert len(summary) > 0
        assert all(isinstance(s, str) for s in summary)

    def test_5km_high_power(self):
        """5 km link with 30 dBm should have positive margin."""
        result = detailed_link_budget(tx_power_dbm=30.0, distance_m=5000.0)
        assert result.link_margin_db > 0

    def test_eirp_calculation(self):
        """EIRP = tx_power - cable_loss + antenna_gain."""
        result = detailed_link_budget(
            tx_power_dbm=20.0,
            tx_cable_type='lmr400',
            tx_cable_length_m=1.0,
            tx_antenna_gain_dbi=6.0,
        )
        expected_cable_loss = calculate_cable_loss('lmr400', 1.0)
        expected_eirp = 20.0 - expected_cable_loss + 6.0
        assert result.eirp_dbm == pytest.approx(expected_eirp, abs=0.01)


# ============================================================================
# Knife-Edge Diffraction Tests
# ============================================================================

class TestKnifeEdgeDiffraction:
    """Test knife-edge diffraction loss."""

    def test_no_blockage(self):
        """Obstacle below LOS should give 0 dB loss."""
        assert knife_edge_diffraction(5000, -5.0) == 0.0

    def test_obstacle_above_los(self):
        """10m obstacle at 5 km midpoint should add significant loss."""
        result = knife_edge_diffraction(5000, 10.0)
        assert result > 5.0

    def test_zero_height_obstacle(self):
        """Zero-height obstacle = no loss."""
        assert knife_edge_diffraction(5000, 0.0) == 0.0

    def test_higher_obstacle_more_loss(self):
        """Taller obstacle produces more diffraction loss."""
        loss_5m = knife_edge_diffraction(5000, 5.0)
        loss_20m = knife_edge_diffraction(5000, 20.0)
        assert loss_20m > loss_5m

    def test_always_non_negative(self):
        """Loss should never be negative."""
        for h in [-10, -5, 0, 5, 10, 50]:
            assert knife_edge_diffraction(5000, h) >= 0.0


class TestMultiObstacleLoss:
    """Test cumulative diffraction loss from multiple obstacles."""

    def test_no_obstacles(self):
        """No obstacles = no loss."""
        assert multi_obstacle_loss(10000, []) == 0.0

    def test_single_obstacle(self):
        """Single obstacle should match knife_edge_diffraction."""
        single = knife_edge_diffraction(10000, 10.0, obstacle_position=0.5)
        multi = multi_obstacle_loss(10000, [(0.5, 10.0)])
        assert multi == pytest.approx(single)

    def test_two_obstacles_more_loss(self):
        """Two obstacles should cause more loss than one."""
        one = multi_obstacle_loss(10000, [(0.5, 10.0)])
        two = multi_obstacle_loss(10000, [(0.3, 10.0), (0.7, 10.0)])
        assert two > one


# ============================================================================
# Environment-Aware Propagation Tests
# ============================================================================

class TestRxSensitivity:
    """Test receiver sensitivity calculation."""

    def test_sf11_125khz(self):
        """SF11 at 125 kHz should be around -134.5 dBm."""
        result = rx_sensitivity(11, 125000)
        assert -137 < result < -132

    def test_higher_bw_less_sensitive(self):
        """Higher bandwidth = less sensitive."""
        narrow = rx_sensitivity(11, 125000)
        wide = rx_sensitivity(11, 500000)
        assert wide > narrow  # Higher (less negative) = less sensitive

    def test_higher_sf_more_sensitive(self):
        """Higher SF = more sensitive (lower threshold)."""
        sf7 = rx_sensitivity(7, 125000)
        sf12 = rx_sensitivity(12, 125000)
        assert sf12 < sf7  # More negative = more sensitive


class TestLogDistancePathLoss:
    """Test log-distance propagation model."""

    def test_free_space_matches_fspl(self):
        """Free space environment should approximate FSPL."""
        result = log_distance_path_loss(1000, 915, DeployEnvironment.FREE_SPACE)
        fspl = free_space_path_loss(1000, 915)
        # Should be close to FSPL (PLE = 2.0 for free space)
        assert abs(result - fspl) < 3.0

    def test_suburban_more_loss(self):
        """Suburban has higher path loss than free space."""
        free = log_distance_path_loss(5000, 915, DeployEnvironment.FREE_SPACE)
        suburb = log_distance_path_loss(5000, 915, DeployEnvironment.SUBURBAN)
        assert suburb > free

    def test_forest_high_loss(self):
        """Forest environment has very high path loss."""
        forest = log_distance_path_loss(5000, 915, DeployEnvironment.FOREST)
        rural = log_distance_path_loss(5000, 915, DeployEnvironment.RURAL_OPEN)
        assert forest > rural

    def test_zero_distance(self):
        """Zero distance returns 0."""
        assert log_distance_path_loss(0, 915) == 0.0

    def test_over_water_low_loss(self):
        """Over water has low path loss (PLE ~1.9)."""
        water = log_distance_path_loss(5000, 915, DeployEnvironment.OVER_WATER)
        urban = log_distance_path_loss(5000, 915, DeployEnvironment.URBAN_GROUND)
        assert water < urban


class TestRealisticMaxRange:
    """Test environment-aware range estimation."""

    def test_positive_range(self):
        """Should return positive range for reasonable link budget."""
        result = realistic_max_range(156.5, 915, DeployEnvironment.SUBURBAN)
        assert result > 0

    def test_free_space_longest(self):
        """Free space should give longest range."""
        free = realistic_max_range(156.5, 915, DeployEnvironment.FREE_SPACE)
        suburb = realistic_max_range(156.5, 915, DeployEnvironment.SUBURBAN)
        assert free > suburb

    def test_building_reduces_range(self):
        """Building penetration reduces range."""
        outdoor = realistic_max_range(156.5, 915, building=BuildingType.NONE)
        indoor = realistic_max_range(156.5, 915, building=BuildingType.CONCRETE)
        assert outdoor > indoor

    def test_zero_budget(self):
        """Zero link budget should return 0 range."""
        result = realistic_max_range(0.0, 915, DeployEnvironment.DENSE_URBAN)
        assert result == 0.0


class TestRadioHorizon:
    """Test radio horizon calculation."""

    def test_two_10m_masts(self):
        """Two 10m masts should see ~26 km."""
        result = radio_horizon_km(10, 10)
        assert 24 < result < 28

    def test_higher_antenna_farther(self):
        """Higher antenna extends radio horizon."""
        low = radio_horizon_km(2, 2)
        high = radio_horizon_km(30, 30)
        assert high > low

    def test_zero_height(self):
        """Zero height should give small horizon from other antenna."""
        result = radio_horizon_km(0, 10)
        assert result > 0

    def test_mountain_to_mast(self):
        """Mountain (1000m) to 10m mast should see ~140+ km."""
        result = radio_horizon_km(10, 1000)
        assert 130 < result < 160


class TestProcessingGain:
    """Test LoRa processing gain."""

    def test_sf12(self):
        """SF12 processing gain is ~36.1 dB."""
        result = processing_gain_db(12)
        assert result == pytest.approx(36.12, abs=0.1)

    def test_sf7(self):
        """SF7 processing gain is ~21.1 dB."""
        result = processing_gain_db(7)
        assert result == pytest.approx(21.07, abs=0.1)

    def test_higher_sf_more_gain(self):
        """Higher SF = more processing gain."""
        assert processing_gain_db(12) > processing_gain_db(7)


class TestCaptureEffect:
    """Test LoRa capture effect determination."""

    def test_strong_captures_weak_same_sf(self):
        """10 dB stronger signal captures with same SF."""
        captured, margin, desc = capture_effect(-90, -100, same_sf=True)
        assert captured is True
        assert margin > 0

    def test_equal_signals_not_captured(self):
        """Equal signals don't capture with same SF (need 6 dB)."""
        captured, margin, desc = capture_effect(-90, -90, same_sf=True)
        assert captured is False

    def test_cross_sf_easier_capture(self):
        """Cross-SF signals have lower capture threshold."""
        captured, margin, desc = capture_effect(-90, -80, same_sf=False)
        # -90 - (-80) = -10, threshold is -16, so -10 > -16 -> captured
        assert captured is True

    def test_description_contains_sir(self):
        """Description should mention SIR."""
        _, _, desc = capture_effect(-90, -100)
        assert "SIR" in desc


# ============================================================================
# Batch Operations Tests
# ============================================================================

class TestBatchOperations:
    """Test batch calculation functions."""

    def test_batch_haversine(self):
        """Batch haversine should return same results as individual calls."""
        coords = [
            (37.7749, -122.4194, 34.0522, -118.2437),  # SF to LA
            (0.0, 0.0, 0.0, 1.0),                       # Equator 1 degree
        ]
        results = batch_haversine(coords)
        assert len(results) == 2
        assert results[0] == pytest.approx(
            haversine_distance(37.7749, -122.4194, 34.0522, -118.2437)
        )
        assert results[1] == pytest.approx(
            haversine_distance(0.0, 0.0, 0.0, 1.0)
        )

    def test_batch_link_quality(self):
        """Batch link quality should return tuples of (rx_power, snr, quality)."""
        links = [
            (1000.0, 2.0, 2.0),   # 1 km
            (10000.0, 2.0, 2.0),  # 10 km
        ]
        results = batch_link_quality(links, tx_power=20.0, freq_mhz=915.0)
        assert len(results) == 2
        # Closer link should have better quality
        assert results[0][2] >= results[1][2]  # quality comparison

    def test_batch_empty(self):
        """Empty batch should return empty list."""
        assert batch_haversine([]) == []
        assert batch_link_quality([]) == []


# ============================================================================
# Constants and Enums Validation
# ============================================================================

class TestConstants:
    """Validate RF constants are reasonable."""

    def test_environment_params_complete(self):
        """All DeployEnvironment values should have parameters."""
        for env in DeployEnvironment:
            assert env in ENVIRONMENT_PARAMS

    def test_building_penetration_complete(self):
        """All BuildingType values should have penetration loss."""
        for bt in BuildingType:
            assert bt in BUILDING_PENETRATION_DB

    def test_snr_thresholds_range(self):
        """SNR thresholds should be negative (below noise floor)."""
        for sf, threshold in SNR_THRESHOLD_DB.items():
            assert 7 <= sf <= 12
            assert threshold < 0

    def test_sensitivity_decreases_with_sf(self):
        """Higher SF = lower sensitivity (more negative dBm)."""
        prev = LORA_SENSITIVITY_DBM[7]
        for sf in range(8, 13):
            current = LORA_SENSITIVITY_DBM[sf]
            assert current < prev  # More sensitive = more negative
            prev = current

    def test_cable_loss_all_positive(self):
        """All cable losses should be positive values."""
        for cable, loss in CABLE_LOSS_DB_PER_M.items():
            assert loss > 0, f"{cable} has non-positive loss"

    def test_building_none_zero_loss(self):
        """No building should have zero penetration loss."""
        assert BUILDING_PENETRATION_DB[BuildingType.NONE] == 0.0


class TestIsFastAvailable:
    """Test Cython fast path detection."""

    def test_returns_bool(self):
        """Should return a boolean."""
        assert isinstance(is_fast_available(), bool)


class TestRequiredAntennaHeight:
    """Test minimum antenna height calculation."""

    def test_5km_link(self):
        """5 km link needs reasonable clearance."""
        result = required_antenna_height(5.0)
        assert 5 < result < 20

    def test_longer_link_higher_antenna(self):
        """Longer link needs taller antenna."""
        short = required_antenna_height(1.0)
        long = required_antenna_height(10.0)
        assert long > short
