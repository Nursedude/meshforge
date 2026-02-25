"""
Antenna pattern modeling — directional gain effects on coverage.

Models common antenna types used in Meshtastic deployments and calculates
effective gain at arbitrary angles. Used for site planning and link budget
enhancement when directional antennas are deployed.

Supported antenna types:
- Isotropic: Theoretical reference (0 dBi uniform)
- Dipole: Standard 1/4-wave whip (2.15 dBi, omnidirectional)
- Ground Plane: Collinear/GP antenna (5-6 dBi, flatter vertical)
- Yagi: Directional beam (7-15 dBi, narrow beamwidth)
- Patch: Panel antenna (6-9 dBi, wide beam)

Pattern calculations use cos^n envelope approximation which gives
reasonable accuracy for link budget planning without full electromagnetic
simulation.

Usage:
    from utils.antenna_patterns import (
        AntennaPattern, DipolePattern, YagiPattern,
        effective_gain, coverage_with_antenna
    )

    # Standard dipole
    dipole = DipolePattern()
    gain = dipole.gain_at(azimuth=45.0, elevation=10.0)

    # Yagi pointed northeast
    yagi = YagiPattern(peak_gain_dbi=12.0, h_beamwidth=30.0, aim_azimuth=45.0)
    gain = yagi.gain_at(azimuth=90.0, elevation=5.0)
    print(f"Off-axis gain: {gain:.1f} dBi")

    # Coverage radius with antenna gain
    from utils.preset_impact import PresetAnalyzer
    analyzer = PresetAnalyzer()
    impact = analyzer.analyze_preset('LONG_FAST')
    range_km = coverage_with_antenna(impact.max_range_los_km, yagi, azimuth=45.0)
"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# Physical constants
SPEED_OF_LIGHT_MS = 299_792_458  # m/s


def normalize_angle(angle: float) -> float:
    """Normalize angle to [0, 360) range.

    Args:
        angle: Angle in degrees.

    Returns:
        Angle normalized to [0, 360).
    """
    return angle % 360.0


def angle_difference(a: float, b: float) -> float:
    """Calculate minimum angular difference between two bearings.

    Args:
        a: First angle in degrees.
        b: Second angle in degrees.

    Returns:
        Minimum difference in degrees [0, 180].
    """
    diff = abs(normalize_angle(a) - normalize_angle(b))
    if diff > 180.0:
        diff = 360.0 - diff
    return diff


@dataclass
class AntennaSpec:
    """Antenna specification for display and comparison."""
    name: str
    type_name: str
    peak_gain_dbi: float
    h_beamwidth_deg: float  # Horizontal -3dB beamwidth
    v_beamwidth_deg: float  # Vertical -3dB beamwidth
    front_to_back_db: float = 0.0  # F/B ratio (0 for omni)
    aim_azimuth_deg: float = 0.0  # Pointing direction
    aim_elevation_deg: float = 0.0  # Tilt angle
    efficiency: float = 0.85  # Typical antenna efficiency

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'type': self.type_name,
            'peak_gain_dbi': round(self.peak_gain_dbi, 1),
            'h_beamwidth_deg': round(self.h_beamwidth_deg, 0),
            'v_beamwidth_deg': round(self.v_beamwidth_deg, 0),
            'front_to_back_db': round(self.front_to_back_db, 0),
            'aim_azimuth_deg': round(self.aim_azimuth_deg, 0),
            'efficiency': round(self.efficiency, 2),
        }


class AntennaPattern(ABC):
    """Base class for antenna radiation patterns.

    All patterns implement gain_at(azimuth, elevation) returning
    the effective gain in dBi at the specified direction.
    """

    def __init__(self, peak_gain_dbi: float = 0.0, name: str = ""):
        """Initialize antenna pattern.

        Args:
            peak_gain_dbi: Maximum antenna gain in dBi.
            name: Human-readable antenna name.
        """
        self.peak_gain_dbi = peak_gain_dbi
        self.name = name or self.__class__.__name__

    @abstractmethod
    def gain_at(self, azimuth: float, elevation: float = 0.0) -> float:
        """Calculate antenna gain at specified direction.

        Args:
            azimuth: Horizontal angle in degrees (0=North, 90=East).
            elevation: Vertical angle in degrees (0=horizon, 90=zenith).

        Returns:
            Effective gain in dBi at the specified direction.
        """

    def gain_pattern(self, azimuth_step: float = 5.0,
                     elevation: float = 0.0) -> List[Tuple[float, float]]:
        """Generate horizontal gain pattern at fixed elevation.

        Args:
            azimuth_step: Step size in degrees.
            elevation: Fixed elevation angle.

        Returns:
            List of (azimuth_deg, gain_dbi) tuples.
        """
        pattern = []
        az = 0.0
        while az < 360.0:
            gain = self.gain_at(az, elevation)
            pattern.append((az, gain))
            az += azimuth_step
        return pattern

    @abstractmethod
    def spec(self) -> AntennaSpec:
        """Return antenna specification dataclass."""

    def effective_range_factor(self, azimuth: float,
                               elevation: float = 0.0,
                               reference_gain_dbi: float = 2.15) -> float:
        """Calculate range improvement factor vs reference antenna.

        Range scales as 10^((gain_diff) / 20) for FSPL.

        Args:
            azimuth: Direction to evaluate.
            elevation: Elevation to evaluate.
            reference_gain_dbi: Reference antenna gain (default: dipole).

        Returns:
            Multiplicative range factor (>1 = more range, <1 = less range).
        """
        gain_diff = self.gain_at(azimuth, elevation) - reference_gain_dbi
        return 10 ** (gain_diff / 20.0)


class IsotropicPattern(AntennaPattern):
    """Isotropic antenna — uniform radiation in all directions.

    Theoretical reference antenna. 0 dBi gain everywhere.
    Used as baseline for gain comparisons.
    """

    def __init__(self):
        super().__init__(peak_gain_dbi=0.0, name="Isotropic")

    def gain_at(self, azimuth: float, elevation: float = 0.0) -> float:
        """Uniform 0 dBi in all directions."""
        return 0.0

    def spec(self) -> AntennaSpec:
        return AntennaSpec(
            name=self.name,
            type_name="Isotropic",
            peak_gain_dbi=0.0,
            h_beamwidth_deg=360.0,
            v_beamwidth_deg=360.0,
        )


class DipolePattern(AntennaPattern):
    """Half-wave dipole / quarter-wave whip pattern.

    Omnidirectional in azimuth, donut-shaped in elevation.
    Peak gain at horizon, null at zenith/nadir.

    Elevation pattern: cos²(θ) where θ is elevation from horizon.
    This is the standard Meshtastic stock antenna pattern.
    """

    def __init__(self, peak_gain_dbi: float = 2.15, name: str = ""):
        """Initialize dipole pattern.

        Args:
            peak_gain_dbi: Peak gain (default 2.15 dBi for ideal dipole).
            name: Antenna name.
        """
        super().__init__(peak_gain_dbi=peak_gain_dbi,
                         name=name or "Dipole/Whip")

    def gain_at(self, azimuth: float, elevation: float = 0.0) -> float:
        """Dipole gain: omnidirectional, cos² elevation pattern.

        Args:
            azimuth: Ignored (omnidirectional).
            elevation: Angle from horizon in degrees.

        Returns:
            Gain in dBi.
        """
        # Elevation pattern: cos²(elevation)
        # At horizon (0°): full gain
        # At zenith (90°): null
        elev_rad = math.radians(min(abs(elevation), 90.0))
        pattern_factor = math.cos(elev_rad) ** 2

        if pattern_factor <= 0:
            return -40.0  # Practical null floor

        return self.peak_gain_dbi + 10 * math.log10(pattern_factor)

    def spec(self) -> AntennaSpec:
        return AntennaSpec(
            name=self.name,
            type_name="Dipole",
            peak_gain_dbi=self.peak_gain_dbi,
            h_beamwidth_deg=360.0,
            v_beamwidth_deg=78.0,  # Typical dipole -3dB vertical beamwidth
        )


class GroundPlanePattern(AntennaPattern):
    """Ground plane / collinear omnidirectional antenna.

    Higher gain than dipole by compressing vertical pattern.
    Common upgrade for Meshtastic fixed installations.

    Elevation pattern: cos^n(θ) where n determined by gain.
    """

    def __init__(self, peak_gain_dbi: float = 5.5, name: str = ""):
        """Initialize ground plane pattern.

        Args:
            peak_gain_dbi: Peak gain (typically 5-8 dBi for collinear).
            name: Antenna name.
        """
        super().__init__(peak_gain_dbi=peak_gain_dbi,
                         name=name or "Ground Plane")
        # Determine elevation exponent from gain
        # Higher gain = narrower vertical beam = higher exponent
        # Approximate: n ≈ 10^(gain_over_dipole / 5)
        gain_over_dipole = max(peak_gain_dbi - 2.15, 0)
        self._elev_exponent = max(2.0, 2.0 + gain_over_dipole * 1.5)

    def gain_at(self, azimuth: float, elevation: float = 0.0) -> float:
        """Ground plane gain: omnidirectional, compressed elevation.

        Args:
            azimuth: Ignored (omnidirectional).
            elevation: Angle from horizon in degrees.

        Returns:
            Gain in dBi.
        """
        elev_rad = math.radians(min(abs(elevation), 90.0))
        pattern_factor = math.cos(elev_rad) ** self._elev_exponent

        if pattern_factor <= 0:
            return -40.0

        return self.peak_gain_dbi + 10 * math.log10(pattern_factor)

    def spec(self) -> AntennaSpec:
        # Approximate -3dB beamwidth from exponent
        # cos^n(θ) = 0.5 → θ = acos(0.5^(1/n))
        half_power_angle = math.degrees(math.acos(0.5 ** (1 / self._elev_exponent)))
        return AntennaSpec(
            name=self.name,
            type_name="Ground Plane",
            peak_gain_dbi=self.peak_gain_dbi,
            h_beamwidth_deg=360.0,
            v_beamwidth_deg=round(2 * half_power_angle, 0),
        )


class YagiPattern(AntennaPattern):
    """Yagi-Uda directional antenna pattern.

    High gain in one direction with significant front-to-back ratio.
    Used for point-to-point links or coverage in a specific sector.

    Pattern: cos^n(φ-aim) envelope in azimuth, cos^m(θ) in elevation.
    The exponents are derived from the specified beamwidths.
    """

    def __init__(self,
                 peak_gain_dbi: float = 12.0,
                 h_beamwidth: float = 30.0,
                 v_beamwidth: float = 35.0,
                 front_to_back_db: float = 20.0,
                 aim_azimuth: float = 0.0,
                 aim_elevation: float = 0.0,
                 name: str = ""):
        """Initialize Yagi pattern.

        Args:
            peak_gain_dbi: Peak gain in dBi (typical 7-15 for Yagi).
            h_beamwidth: Horizontal -3dB beamwidth in degrees.
            v_beamwidth: Vertical -3dB beamwidth in degrees.
            front_to_back_db: Front-to-back ratio in dB.
            aim_azimuth: Pointing direction (0=North, 90=East).
            aim_elevation: Pointing elevation (0=horizon).
            name: Antenna name.
        """
        super().__init__(peak_gain_dbi=peak_gain_dbi,
                         name=name or "Yagi")
        self.h_beamwidth = max(h_beamwidth, 1.0)
        self.v_beamwidth = max(v_beamwidth, 1.0)
        self.front_to_back_db = front_to_back_db
        self.aim_azimuth = aim_azimuth
        self.aim_elevation = aim_elevation

        # Calculate exponents from beamwidths
        # cos^n(θ) = 0.5 at θ = beamwidth/2
        # n = log(0.5) / log(cos(beamwidth/2))
        h_half_rad = math.radians(self.h_beamwidth / 2.0)
        v_half_rad = math.radians(self.v_beamwidth / 2.0)

        cos_h = math.cos(h_half_rad)
        cos_v = math.cos(v_half_rad)

        self._h_exponent = (math.log(0.5) / math.log(cos_h)
                           if cos_h > 0 and cos_h < 1 else 2.0)
        self._v_exponent = (math.log(0.5) / math.log(cos_v)
                           if cos_v > 0 and cos_v < 1 else 2.0)

    def gain_at(self, azimuth: float, elevation: float = 0.0) -> float:
        """Yagi gain at specified direction.

        Args:
            azimuth: Horizontal angle in degrees.
            elevation: Vertical angle in degrees.

        Returns:
            Gain in dBi.
        """
        # Angular offset from aim direction
        h_offset = angle_difference(azimuth, self.aim_azimuth)
        v_offset = abs(elevation - self.aim_elevation)

        # Check if in rear hemisphere
        if h_offset > 90.0:
            # Behind the antenna: apply front-to-back ratio
            return self.peak_gain_dbi - self.front_to_back_db

        # Front hemisphere: cos^n envelope
        h_rad = math.radians(h_offset)
        v_rad = math.radians(min(v_offset, 90.0))

        cos_h = math.cos(h_rad)
        cos_v = math.cos(v_rad)

        h_factor = cos_h ** self._h_exponent if cos_h > 0 else 0
        v_factor = cos_v ** self._v_exponent if cos_v > 0 else 0

        combined = h_factor * v_factor
        if combined <= 0:
            return self.peak_gain_dbi - self.front_to_back_db

        return self.peak_gain_dbi + 10 * math.log10(combined)

    def spec(self) -> AntennaSpec:
        return AntennaSpec(
            name=self.name,
            type_name="Yagi",
            peak_gain_dbi=self.peak_gain_dbi,
            h_beamwidth_deg=self.h_beamwidth,
            v_beamwidth_deg=self.v_beamwidth,
            front_to_back_db=self.front_to_back_db,
            aim_azimuth_deg=self.aim_azimuth,
            aim_elevation_deg=self.aim_elevation,
        )


class PatchPattern(AntennaPattern):
    """Patch / panel antenna pattern.

    Moderate gain with wider beamwidth than Yagi. Common for
    sector coverage in fixed installations.

    Similar to Yagi model but with wider beamwidth defaults.
    """

    def __init__(self,
                 peak_gain_dbi: float = 8.0,
                 h_beamwidth: float = 70.0,
                 v_beamwidth: float = 65.0,
                 front_to_back_db: float = 15.0,
                 aim_azimuth: float = 0.0,
                 aim_elevation: float = 0.0,
                 name: str = ""):
        """Initialize patch antenna pattern.

        Args:
            peak_gain_dbi: Peak gain (typically 6-9 dBi).
            h_beamwidth: Horizontal -3dB beamwidth (typically 60-90°).
            v_beamwidth: Vertical -3dB beamwidth (typically 55-75°).
            front_to_back_db: Front-to-back ratio.
            aim_azimuth: Pointing direction.
            aim_elevation: Pointing elevation.
            name: Antenna name.
        """
        super().__init__(peak_gain_dbi=peak_gain_dbi,
                         name=name or "Patch/Panel")
        self.h_beamwidth = max(h_beamwidth, 1.0)
        self.v_beamwidth = max(v_beamwidth, 1.0)
        self.front_to_back_db = front_to_back_db
        self.aim_azimuth = aim_azimuth
        self.aim_elevation = aim_elevation

        h_half_rad = math.radians(self.h_beamwidth / 2.0)
        v_half_rad = math.radians(self.v_beamwidth / 2.0)

        cos_h = math.cos(h_half_rad)
        cos_v = math.cos(v_half_rad)

        self._h_exponent = (math.log(0.5) / math.log(cos_h)
                           if cos_h > 0 and cos_h < 1 else 2.0)
        self._v_exponent = (math.log(0.5) / math.log(cos_v)
                           if cos_v > 0 and cos_v < 1 else 2.0)

    def gain_at(self, azimuth: float, elevation: float = 0.0) -> float:
        """Patch antenna gain at specified direction.

        Args:
            azimuth: Horizontal angle in degrees.
            elevation: Vertical angle in degrees.

        Returns:
            Gain in dBi.
        """
        h_offset = angle_difference(azimuth, self.aim_azimuth)
        v_offset = abs(elevation - self.aim_elevation)

        if h_offset > 90.0:
            return self.peak_gain_dbi - self.front_to_back_db

        h_rad = math.radians(h_offset)
        v_rad = math.radians(min(v_offset, 90.0))

        cos_h = math.cos(h_rad)
        cos_v = math.cos(v_rad)

        h_factor = cos_h ** self._h_exponent if cos_h > 0 else 0
        v_factor = cos_v ** self._v_exponent if cos_v > 0 else 0

        combined = h_factor * v_factor
        if combined <= 0:
            return self.peak_gain_dbi - self.front_to_back_db

        return self.peak_gain_dbi + 10 * math.log10(combined)

    def spec(self) -> AntennaSpec:
        return AntennaSpec(
            name=self.name,
            type_name="Patch",
            peak_gain_dbi=self.peak_gain_dbi,
            h_beamwidth_deg=self.h_beamwidth,
            v_beamwidth_deg=self.v_beamwidth,
            front_to_back_db=self.front_to_back_db,
            aim_azimuth_deg=self.aim_azimuth,
            aim_elevation_deg=self.aim_elevation,
        )


# Preset antenna configurations for common Meshtastic setups
ANTENNA_PRESETS = {
    'stock_whip': lambda: DipolePattern(peak_gain_dbi=2.15, name="Stock Whip"),
    'dipole_tuned': lambda: DipolePattern(peak_gain_dbi=3.0, name="Tuned Dipole"),
    'collinear_5dbi': lambda: GroundPlanePattern(peak_gain_dbi=5.5, name="5.5 dBi Collinear"),
    'collinear_8dbi': lambda: GroundPlanePattern(peak_gain_dbi=8.0, name="8 dBi Collinear"),
    'yagi_3el': lambda: YagiPattern(peak_gain_dbi=7.5, h_beamwidth=60.0,
                                     v_beamwidth=55.0, front_to_back_db=15.0,
                                     name="3-Element Yagi"),
    'yagi_5el': lambda: YagiPattern(peak_gain_dbi=10.0, h_beamwidth=40.0,
                                     v_beamwidth=38.0, front_to_back_db=18.0,
                                     name="5-Element Yagi"),
    'yagi_9el': lambda: YagiPattern(peak_gain_dbi=13.0, h_beamwidth=28.0,
                                     v_beamwidth=26.0, front_to_back_db=22.0,
                                     name="9-Element Yagi"),
    'patch_900': lambda: PatchPattern(peak_gain_dbi=8.0, h_beamwidth=70.0,
                                       v_beamwidth=65.0, front_to_back_db=15.0,
                                       name="900 MHz Patch"),
    'sector_120': lambda: PatchPattern(peak_gain_dbi=10.0, h_beamwidth=120.0,
                                        v_beamwidth=25.0, front_to_back_db=20.0,
                                        name="120° Sector"),
}


def get_antenna_preset(name: str, aim_azimuth: float = 0.0) -> AntennaPattern:
    """Get a pre-configured antenna pattern by name.

    Args:
        name: Preset name from ANTENNA_PRESETS.
        aim_azimuth: Pointing direction for directional antennas.

    Returns:
        Configured AntennaPattern instance.

    Raises:
        ValueError: If preset name is unknown.
    """
    if name not in ANTENNA_PRESETS:
        raise ValueError(f"Unknown antenna preset: {name}. "
                        f"Available: {list(ANTENNA_PRESETS.keys())}")

    antenna = ANTENNA_PRESETS[name]()

    # Set aim direction for directional types
    if hasattr(antenna, 'aim_azimuth'):
        antenna.aim_azimuth = aim_azimuth
        # Recalculate if needed (Yagi/Patch store aim in __init__)

    return antenna


def effective_gain(antenna: AntennaPattern,
                   target_azimuth: float,
                   target_elevation: float = 0.0) -> float:
    """Calculate effective gain toward a target.

    Convenience function wrapping antenna.gain_at().

    Args:
        antenna: Antenna pattern to evaluate.
        target_azimuth: Direction to target in degrees.
        target_elevation: Elevation to target in degrees.

    Returns:
        Effective gain in dBi.
    """
    return antenna.gain_at(target_azimuth, target_elevation)


def coverage_with_antenna(base_range_km: float,
                          antenna: AntennaPattern,
                          azimuth: float,
                          elevation: float = 0.0,
                          reference_gain_dbi: float = 2.15) -> float:
    """Calculate effective range with antenna gain applied.

    Range scales as 10^(gain_diff/20) due to FSPL relationship.

    Args:
        base_range_km: Range with reference antenna.
        antenna: Antenna pattern to apply.
        azimuth: Direction to evaluate.
        elevation: Elevation to evaluate.
        reference_gain_dbi: Reference antenna gain (default: stock dipole).

    Returns:
        Effective range in km with antenna gain.
    """
    factor = antenna.effective_range_factor(azimuth, elevation, reference_gain_dbi)
    return base_range_km * factor


def azimuth_range_profile(antenna: AntennaPattern,
                          base_range_km: float,
                          step_deg: float = 5.0,
                          reference_gain_dbi: float = 2.15
                          ) -> List[Tuple[float, float]]:
    """Generate range profile around the compass.

    Calculates effective range at each azimuth step, producing
    data suitable for polar plot visualization.

    Args:
        antenna: Antenna pattern to evaluate.
        base_range_km: Base range with reference antenna.
        step_deg: Angular step in degrees.
        reference_gain_dbi: Reference gain for comparison.

    Returns:
        List of (azimuth_deg, range_km) tuples.
    """
    profile = []
    az = 0.0
    while az < 360.0:
        r = coverage_with_antenna(base_range_km, antenna, az,
                                  reference_gain_dbi=reference_gain_dbi)
        profile.append((az, r))
        az += step_deg
    return profile


def format_antenna_comparison(antennas: List[AntennaPattern],
                              base_range_km: float = 10.0,
                              target_azimuth: float = 0.0) -> str:
    """Format antenna comparison table for TUI display.

    Args:
        antennas: List of antennas to compare.
        base_range_km: Base range for comparison.
        target_azimuth: Direction to evaluate gain.

    Returns:
        Formatted multi-line string table.
    """
    lines = []
    lines.append("=" * 75)
    lines.append(f"  Antenna Comparison (target azimuth: {target_azimuth:.0f}°)")
    lines.append("=" * 75)
    lines.append(f"{'Name':<20} {'Type':<12} {'Peak':>6} {'At Target':>10} "
                 f"{'Range km':>9} {'Factor':>7}")
    lines.append("-" * 75)

    for ant in antennas:
        spec = ant.spec()
        gain_at_target = ant.gain_at(target_azimuth, 0.0)
        range_km = coverage_with_antenna(base_range_km, ant, target_azimuth)
        factor = ant.effective_range_factor(target_azimuth)

        lines.append(
            f"{spec.name:<20} {spec.type_name:<12} "
            f"{spec.peak_gain_dbi:>5.1f}i {gain_at_target:>9.1f}i "
            f"{range_km:>9.1f} {factor:>6.2f}x"
        )

    lines.append("=" * 75)
    return "\n".join(lines)
