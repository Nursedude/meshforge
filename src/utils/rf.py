"""
RF calculation utilities for MeshForge.

Pure functions for radio frequency calculations - no UI dependencies.

If Cython-compiled rf_fast module is available, these functions are
replaced with optimized versions providing 5-10x speedup.

To compile fast version:
    cd src/utils && python setup_cython.py build_ext --inplace
"""

import math

# Try to import Cython-optimized versions
_USE_FAST = False
try:
    from utils.rf_fast import (
        haversine_distance as _haversine_fast,
        fresnel_radius as _fresnel_fast,
        free_space_path_loss as _fspl_fast,
        earth_bulge as _bulge_fast,
        link_budget as _link_budget_fast,
        snr_estimate as _snr_fast,
        batch_haversine as _batch_haversine_fast,
        batch_link_quality as _batch_link_quality_fast,
    )
    _USE_FAST = True
except ImportError:
    pass  # Fall back to pure Python


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points using Haversine formula.

    Args:
        lat1, lon1: First point coordinates (degrees)
        lat2, lon2: Second point coordinates (degrees)

    Returns:
        Distance in meters
    """
    R = 6371000  # Earth radius in meters

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def fresnel_radius(distance_km: float, freq_ghz: float) -> float:
    """Calculate first Fresnel zone radius at midpoint.

    Args:
        distance_km: Path distance in kilometers
        freq_ghz: Frequency in GHz

    Returns:
        Fresnel zone radius in meters
    """
    return 17.3 * math.sqrt(distance_km / (4 * freq_ghz))


def free_space_path_loss(distance_m: float, freq_mhz: float) -> float:
    """Calculate Free Space Path Loss (FSPL).

    Args:
        distance_m: Distance in meters
        freq_mhz: Frequency in MHz

    Returns:
        Path loss in dB
    """
    return 20 * math.log10(distance_m) + 20 * math.log10(freq_mhz) - 27.55


def earth_bulge(distance_m: float) -> float:
    """Calculate Earth bulge at midpoint of a path.

    Uses 4/3 Earth radius for RF refraction.

    Args:
        distance_m: Path distance in meters

    Returns:
        Earth bulge in meters
    """
    R = 6371000  # Earth radius
    k = 4 / 3    # RF refraction factor
    return (distance_m ** 2) / (8 * R * k)


def link_budget(tx_power_dbm: float, tx_gain_dbi: float,
                rx_gain_dbi: float, distance_m: float,
                freq_mhz: float) -> float:
    """Calculate received power using link budget equation.

    Args:
        tx_power_dbm: Transmit power in dBm
        tx_gain_dbi: Transmit antenna gain in dBi
        rx_gain_dbi: Receive antenna gain in dBi
        distance_m: Distance in meters
        freq_mhz: Frequency in MHz

    Returns:
        Received power in dBm
    """
    fspl = free_space_path_loss(distance_m, freq_mhz)
    return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fspl


def snr_estimate(rx_power_dbm: float, noise_floor_dbm: float = -120.0) -> float:
    """Estimate SNR given received power.

    Args:
        rx_power_dbm: Received power in dBm
        noise_floor_dbm: Noise floor in dBm (default -120 for LoRa)

    Returns:
        Estimated SNR in dB
    """
    return rx_power_dbm - noise_floor_dbm


# Use fast versions if available
if _USE_FAST:
    haversine_distance = _haversine_fast
    fresnel_radius = _fresnel_fast
    free_space_path_loss = _fspl_fast
    earth_bulge = _bulge_fast
    link_budget = _link_budget_fast
    snr_estimate = _snr_fast

    # Batch functions only available in fast version
    batch_haversine = _batch_haversine_fast
    batch_link_quality = _batch_link_quality_fast
else:
    # Provide pure Python batch implementations
    def batch_haversine(coords):
        """Calculate distances for multiple coordinate pairs."""
        return [haversine_distance(*c) for c in coords]

    def batch_link_quality(links, tx_power=20.0, freq_mhz=915.0):
        """Calculate link quality for multiple node pairs."""
        results = []
        for distance_m, tx_gain, rx_gain in links:
            rx_power = link_budget(tx_power, tx_gain, rx_gain, distance_m, freq_mhz)
            snr = snr_estimate(rx_power)
            # Quality as percentage
            if snr > 10.0:
                quality = 100.0
            elif snr < -10.0:
                quality = 0.0
            else:
                quality = (snr + 10.0) * 5.0
            results.append((rx_power, snr, quality))
        return results


def is_fast_available() -> bool:
    """Check if Cython-optimized RF functions are available."""
    return _USE_FAST
