"""
NanoVNA RF Tools Integration

Bridges NanoVNA sweep data into MeshForge's RF calculation utilities.
Provides antenna mismatch loss calculations for link budget analysis.
"""

import math
from typing import Dict, Optional, Any, List, Tuple

from .nanovna_device import SweepResult, SweepPoint


# Common amateur/LoRa frequency bands (start_mhz, stop_mhz)
BANDS: Dict[str, Tuple[float, float]] = {
    "2m": (144.0, 148.0),
    "1.25m": (219.0, 225.0),
    "70cm": (420.0, 450.0),
    "33cm": (902.0, 928.0),
    "23cm": (1240.0, 1300.0),
    "915mhz_lora": (906.0, 924.0),       # Meshtastic US band
    "868mhz_lora": (863.0, 870.0),       # Meshtastic EU band
    "433mhz_lora": (433.05, 434.79),     # Meshtastic 433 band
}


def swr_to_mismatch_loss_db(swr: float) -> float:
    """Convert SWR to mismatch loss in dB.

    This value represents power lost due to impedance mismatch and can be
    subtracted from link_budget() calculations.

    Args:
        swr: Standing Wave Ratio (must be >= 1.0).

    Returns:
        Mismatch loss in dB (0.0 for perfect match, higher = worse).
    """
    if swr <= 1.0:
        return 0.0
    gamma = (swr - 1) / (swr + 1)
    return -10 * math.log10(1.0 - gamma ** 2)


def antenna_efficiency_from_swr(swr: float) -> float:
    """Estimate antenna radiation efficiency from SWR.

    Returns the fraction (0.0-1.0) of power that is actually radiated
    vs reflected back to the transmitter.

    Args:
        swr: Standing Wave Ratio.

    Returns:
        Efficiency factor (1.0 = perfect, 0.0 = total reflection).
    """
    if swr <= 1.0:
        return 1.0
    gamma = (swr - 1) / (swr + 1)
    return 1.0 - gamma ** 2


def get_antenna_loss_at_frequency(sweep: SweepResult, freq_mhz: float) -> Optional[float]:
    """Get mismatch loss at a specific frequency from a sweep.

    Args:
        sweep: SweepResult from a NanoVNA measurement.
        freq_mhz: Target frequency in MHz.

    Returns:
        Mismatch loss in dB, or None if no data at that frequency.
    """
    swr = sweep.get_swr_at_frequency(freq_mhz)
    if swr is None:
        return None
    return swr_to_mismatch_loss_db(swr)


def sweep_summary_for_band(sweep: SweepResult, band_name: str) -> Optional[Dict[str, Any]]:
    """Summarize sweep results for a specific band.

    Args:
        sweep: SweepResult with measurement data.
        band_name: Band identifier (e.g., "915mhz_lora", "70cm").

    Returns:
        Dict with band summary or None if band not defined.
    """
    if band_name not in BANDS:
        return None

    band_start, band_stop = BANDS[band_name]

    # Filter points within band
    band_points = [
        p for p in sweep.points
        if band_start <= p.frequency_mhz <= band_stop
    ]

    if not band_points:
        return {
            "band": band_name,
            "range_mhz": (band_start, band_stop),
            "points_in_band": 0,
            "error": "No measurement data in this band",
        }

    min_swr_point = min(band_points, key=lambda p: p.swr)
    max_swr_point = max(band_points, key=lambda p: p.swr)
    avg_swr = sum(p.swr for p in band_points) / len(band_points)

    return {
        "band": band_name,
        "range_mhz": (band_start, band_stop),
        "points_in_band": len(band_points),
        "min_swr": min_swr_point.swr,
        "min_swr_freq_mhz": min_swr_point.frequency_mhz,
        "max_swr": max_swr_point.swr,
        "avg_swr": avg_swr,
        "mismatch_loss_db_at_best": swr_to_mismatch_loss_db(min_swr_point.swr),
        "impedance_at_best": {
            "r": min_swr_point.resistance,
            "x": min_swr_point.reactance,
        },
        "usable": min_swr_point.swr < 3.0,  # SWR < 3:1 is generally usable
    }
