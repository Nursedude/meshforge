# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
"""
Cython-optimized RF calculations for MeshForge.

These functions are 5-10x faster than pure Python equivalents.
Used for intensive operations like network simulation and link budget calculations.

To compile:
    cd src/utils && python setup_cython.py build_ext --inplace

Falls back to rf.py if Cython module not available.
"""

from libc.math cimport sin, cos, sqrt, atan2, log10, M_PI

cdef double DEG_TO_RAD = M_PI / 180.0
cdef double EARTH_RADIUS = 6371000.0  # meters
cdef double RF_REFRACTION_K = 4.0 / 3.0


cpdef double haversine_distance(double lat1, double lon1, double lat2, double lon2) nogil:
    """Calculate distance between two points using Haversine formula.

    Args:
        lat1, lon1: First point coordinates (degrees)
        lat2, lon2: Second point coordinates (degrees)

    Returns:
        Distance in meters
    """
    cdef double lat1_rad, lat2_rad, delta_lat, delta_lon
    cdef double a, c

    lat1_rad = lat1 * DEG_TO_RAD
    lat2_rad = lat2 * DEG_TO_RAD
    delta_lat = (lat2 - lat1) * DEG_TO_RAD
    delta_lon = (lon2 - lon1) * DEG_TO_RAD

    a = (sin(delta_lat / 2.0) ** 2 +
         cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2.0) ** 2)
    c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a))

    return EARTH_RADIUS * c


cpdef double fresnel_radius(double distance_km, double freq_ghz) nogil:
    """Calculate first Fresnel zone radius at midpoint.

    Args:
        distance_km: Path distance in kilometers
        freq_ghz: Frequency in GHz

    Returns:
        Fresnel zone radius in meters
    """
    return 17.3 * sqrt(distance_km / (4.0 * freq_ghz))


cpdef double free_space_path_loss(double distance_m, double freq_mhz) nogil:
    """Calculate Free Space Path Loss (FSPL).

    Args:
        distance_m: Distance in meters
        freq_mhz: Frequency in MHz

    Returns:
        Path loss in dB
    """
    return 20.0 * log10(distance_m) + 20.0 * log10(freq_mhz) - 27.55


cpdef double earth_bulge(double distance_m) nogil:
    """Calculate Earth bulge at midpoint of a path.

    Uses 4/3 Earth radius for RF refraction.

    Args:
        distance_m: Path distance in meters

    Returns:
        Earth bulge in meters
    """
    return (distance_m ** 2) / (8.0 * EARTH_RADIUS * RF_REFRACTION_K)


cpdef double link_budget(double tx_power_dbm, double tx_gain_dbi,
                         double rx_gain_dbi, double distance_m,
                         double freq_mhz) nogil:
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
    cdef double fspl = free_space_path_loss(distance_m, freq_mhz)
    return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fspl


cpdef double snr_estimate(double rx_power_dbm, double noise_floor_dbm=-120.0) nogil:
    """Estimate SNR given received power.

    Args:
        rx_power_dbm: Received power in dBm
        noise_floor_dbm: Noise floor in dBm (default -120 for LoRa)

    Returns:
        Estimated SNR in dB
    """
    return rx_power_dbm - noise_floor_dbm


cpdef double path_loss_two_ray(double distance_m, double tx_height_m,
                                double rx_height_m, double freq_mhz) nogil:
    """Two-ray ground reflection path loss model.

    More accurate than FSPL for terrestrial links over distance.

    Args:
        distance_m: Distance in meters
        tx_height_m: Transmitter height in meters
        rx_height_m: Receiver height in meters
        freq_mhz: Frequency in MHz

    Returns:
        Path loss in dB
    """
    cdef double wavelength = 300.0 / freq_mhz  # wavelength in meters
    cdef double crossover = (4.0 * M_PI * tx_height_m * rx_height_m) / wavelength

    if distance_m < crossover:
        # Use free space model for short distances
        return free_space_path_loss(distance_m, freq_mhz)
    else:
        # Two-ray model for longer distances
        return 40.0 * log10(distance_m) - 20.0 * log10(tx_height_m) - 20.0 * log10(rx_height_m)


def batch_haversine(list coords):
    """Calculate distances for multiple coordinate pairs.

    Optimized batch processing for simulation.

    Args:
        coords: List of (lat1, lon1, lat2, lon2) tuples

    Returns:
        List of distances in meters
    """
    cdef list results = []
    cdef double lat1, lon1, lat2, lon2
    cdef tuple coord

    for coord in coords:
        lat1, lon1, lat2, lon2 = coord
        results.append(haversine_distance(lat1, lon1, lat2, lon2))

    return results


def batch_link_quality(list links, double tx_power=20.0, double freq_mhz=915.0):
    """Calculate link quality for multiple node pairs.

    Optimized batch processing for network simulation.

    Args:
        links: List of (distance_m, tx_gain, rx_gain) tuples
        tx_power: Transmit power in dBm (default 20)
        freq_mhz: Frequency in MHz (default 915)

    Returns:
        List of (rx_power_dbm, snr_db, quality_pct) tuples
    """
    cdef list results = []
    cdef double distance_m, tx_gain, rx_gain
    cdef double rx_power, snr, quality
    cdef tuple link

    for link in links:
        distance_m, tx_gain, rx_gain = link
        rx_power = link_budget(tx_power, tx_gain, rx_gain, distance_m, freq_mhz)
        snr = snr_estimate(rx_power)

        # Quality as percentage (0-100)
        # SNR > 10 dB = excellent (100%), SNR < -10 dB = poor (0%)
        if snr > 10.0:
            quality = 100.0
        elif snr < -10.0:
            quality = 0.0
        else:
            quality = (snr + 10.0) * 5.0  # Linear scale

        results.append((rx_power, snr, quality))

    return results
