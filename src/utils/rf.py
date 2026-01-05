"""
RF calculation utilities for MeshForge.

Pure functions for radio frequency calculations - no UI dependencies.
"""

import math


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
