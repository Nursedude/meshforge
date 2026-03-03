"""
LoRa Preset Impact Visualization — Coverage vs throughput tradeoff analysis.

Calculates and compares the real-world impact of Meshtastic LoRa preset
choices on coverage, throughput, airtime, and link budget. Helps operators
choose the right preset for their deployment scenario.

Key calculations:
- Receiver sensitivity per preset (SF + BW dependent)
- Maximum theoretical range (FSPL-based link budget)
- Airtime per packet (LoRa modulation timing)
- Coverage area (km² at max range)
- Throughput vs range tradeoff

Usage:
    from utils.preset_impact import PresetAnalyzer, compare_presets

    analyzer = PresetAnalyzer()
    impact = analyzer.analyze_preset('LONG_FAST')
    print(f"Max range: {impact.max_range_km:.1f} km")
    print(f"Airtime per packet: {impact.airtime_ms:.0f} ms")

    # Compare all presets
    comparison = compare_presets()
    for preset in comparison:
        print(f"{preset.preset_name:20s} {preset.max_range_km:6.1f} km  "
              f"{preset.throughput_bps:8.0f} bps  {preset.airtime_ms:6.0f} ms")
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.rf.calculator import (
    DeployEnvironment,
    BuildingType,
    realistic_max_range,
    radio_horizon_km,
)


# LoRa PHY constants
NOISE_FIGURE_DB = 6.0  # Typical for SX1262/SX1276

# Required SNR per spreading factor (LoRa specification)
# Lower values mean the receiver can decode weaker signals
REQUIRED_SNR_DB = {
    7: -7.5,
    8: -10.0,
    9: -12.5,
    10: -15.0,
    11: -17.5,
    12: -20.0,
}

# Default transmission parameters
DEFAULT_TX_POWER_DBM = 22  # Typical max for most modules (100 mW)
DEFAULT_TX_GAIN_DBI = 2.15  # Typical whip antenna
DEFAULT_RX_GAIN_DBI = 2.15  # Typical whip antenna

# LoRa preamble symbols
LORA_PREAMBLE_SYMBOLS = 16  # Meshtastic default

# Default payload size for airtime calculation
DEFAULT_PAYLOAD_BYTES = 50  # Typical Meshtastic message

# Meshtastic US frequency (906.875 MHz)
DEFAULT_FREQ_MHZ = 906.875


@dataclass
class PresetImpact:
    """Complete impact analysis for a single LoRa preset."""
    preset_name: str
    spreading_factor: int
    bandwidth_hz: int
    coding_rate: int
    frequency_mhz: float

    # Calculated values
    sensitivity_dbm: float = 0.0
    link_budget_db: float = 0.0
    max_range_km: float = 0.0
    max_range_los_km: float = 0.0  # With earth curvature limit
    coverage_area_km2: float = 0.0
    airtime_ms: float = 0.0
    throughput_bps: float = 0.0
    duty_cycle_pct: float = 0.0
    packets_per_hour: int = 0

    # Context
    tx_power_dbm: int = DEFAULT_TX_POWER_DBM
    description: str = ""
    estimated_range: str = ""
    warning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            'preset_name': self.preset_name,
            'spreading_factor': self.spreading_factor,
            'bandwidth_khz': self.bandwidth_hz / 1000,
            'coding_rate': f"4/{self.coding_rate}",
            'frequency_mhz': self.frequency_mhz,
            'sensitivity_dbm': round(self.sensitivity_dbm, 1),
            'link_budget_db': round(self.link_budget_db, 1),
            'max_range_km': round(self.max_range_km, 1),
            'coverage_area_km2': round(self.coverage_area_km2, 1),
            'airtime_ms': round(self.airtime_ms, 1),
            'throughput_bps': round(self.throughput_bps, 0),
            'duty_cycle_pct': round(self.duty_cycle_pct, 2),
            'packets_per_hour': self.packets_per_hour,
            'description': self.description,
            'warning': self.warning,
        }


@dataclass
class PresetComparison:
    """Comparison of all presets with rankings."""
    presets: List[PresetImpact]
    best_range: str = ""
    best_throughput: str = ""
    best_balance: str = ""  # Range × throughput product

    def to_dict(self) -> Dict[str, Any]:
        return {
            'presets': [p.to_dict() for p in self.presets],
            'rankings': {
                'best_range': self.best_range,
                'best_throughput': self.best_throughput,
                'best_balance': self.best_balance,
            }
        }


# Preset definitions (matches lora_presets.py)
PRESET_PARAMS = {
    'SHORT_TURBO': {'sf': 7, 'bw': 500000, 'cr': 5,
                    'desc': 'Very high speed, very short range',
                    'warning': 'May be illegal in some regions'},
    'SHORT_FAST': {'sf': 7, 'bw': 250000, 'cr': 5,
                   'desc': 'High speed, short range - Urban'},
    'SHORT_SLOW': {'sf': 7, 'bw': 125000, 'cr': 5,
                   'desc': 'Fast, reliable short range'},
    'MEDIUM_FAST': {'sf': 10, 'bw': 250000, 'cr': 5,
                    'desc': 'MtnMesh Standard - Best balance'},
    'MEDIUM_SLOW': {'sf': 10, 'bw': 125000, 'cr': 5,
                    'desc': 'Balanced speed and range'},
    'LONG_FAST': {'sf': 11, 'bw': 250000, 'cr': 5,
                  'desc': 'Default Meshtastic'},
    'LONG_MODERATE': {'sf': 11, 'bw': 125000, 'cr': 8,
                      'desc': 'Extended range, moderate speed'},
    'LONG_SLOW': {'sf': 12, 'bw': 125000, 'cr': 8,
                  'desc': 'Maximum range - SAR'},
    'VERY_LONG_SLOW': {'sf': 12, 'bw': 62500, 'cr': 8,
                       'desc': 'Experimental extreme range'},
}


class PresetAnalyzer:
    """Analyzes LoRa preset impact on coverage and performance.

    Combines LoRa PHY calculations (sensitivity, airtime) with
    environment-aware propagation models to produce actionable
    coverage comparisons.
    """

    def __init__(self,
                 tx_power_dbm: int = DEFAULT_TX_POWER_DBM,
                 tx_gain_dbi: float = DEFAULT_TX_GAIN_DBI,
                 rx_gain_dbi: float = DEFAULT_RX_GAIN_DBI,
                 freq_mhz: float = DEFAULT_FREQ_MHZ,
                 payload_bytes: int = DEFAULT_PAYLOAD_BYTES,
                 environment: DeployEnvironment = DeployEnvironment.FREE_SPACE,
                 building: BuildingType = BuildingType.NONE,
                 antenna_height_m: float = 2.0):
        """Initialize analyzer with radio parameters.

        Args:
            tx_power_dbm: Transmit power in dBm.
            tx_gain_dbi: Transmit antenna gain in dBi.
            rx_gain_dbi: Receive antenna gain in dBi.
            freq_mhz: Operating frequency in MHz.
            payload_bytes: Payload size for airtime calculation.
            environment: Deployment environment for path loss modeling.
            building: Building type at RX end (adds penetration loss).
            antenna_height_m: Antenna height above ground in meters.
        """
        self.tx_power_dbm = tx_power_dbm
        self.tx_gain_dbi = tx_gain_dbi
        self.rx_gain_dbi = rx_gain_dbi
        self.freq_mhz = freq_mhz
        self.payload_bytes = payload_bytes
        self.environment = environment
        self.building = building
        self.antenna_height_m = antenna_height_m

    def sensitivity(self, spreading_factor: int, bandwidth_hz: int) -> float:
        """Calculate receiver sensitivity for given LoRa parameters.

        Formula: sensitivity = -174 + 10*log10(BW) + NF + required_SNR

        Args:
            spreading_factor: LoRa spreading factor (7-12).
            bandwidth_hz: Channel bandwidth in Hz.

        Returns:
            Sensitivity in dBm.
        """
        thermal_noise = -174.0  # dBm/Hz at room temperature
        bw_contribution = 10 * math.log10(bandwidth_hz)
        required_snr = REQUIRED_SNR_DB.get(spreading_factor, -15.0)

        return thermal_noise + bw_contribution + NOISE_FIGURE_DB + required_snr

    def max_range_fspl(self, link_budget_db: float, freq_mhz: float) -> float:
        """Calculate maximum range from link budget using FSPL.

        Inverts FSPL formula: d = 10^((link_budget - 20*log10(f) + 27.55) / 20)

        Args:
            link_budget_db: Total link budget in dB.
            freq_mhz: Frequency in MHz.

        Returns:
            Maximum range in meters.
        """
        exponent = (link_budget_db - 20 * math.log10(freq_mhz) + 27.55) / 20
        return 10 ** exponent

    def airtime_ms(self, spreading_factor: int, bandwidth_hz: int,
                   coding_rate: int, payload_bytes: int) -> float:
        """Calculate LoRa packet airtime in milliseconds.

        Based on Semtech LoRa modem designer's guide (SX1276).

        Args:
            spreading_factor: SF 7-12.
            bandwidth_hz: BW in Hz.
            coding_rate: CR 5-8 (representing 4/5 to 4/8).
            payload_bytes: Payload size in bytes.

        Returns:
            Total airtime in milliseconds.
        """
        sf = spreading_factor
        bw = bandwidth_hz

        # Symbol duration
        t_sym = (2 ** sf) / bw * 1000  # ms

        # Preamble duration
        t_preamble = (LORA_PREAMBLE_SYMBOLS + 4.25) * t_sym

        # Payload symbols calculation
        # DE = 1 if SF >= 11 (low data rate optimization)
        de = 1 if sf >= 11 else 0
        # IH = 0 (explicit header mode, Meshtastic standard)
        ih = 0

        # Number of payload symbols
        numerator = 8 * payload_bytes - 4 * sf + 28 + 16 - 20 * ih
        denominator = 4 * (sf - 2 * de)

        if denominator <= 0:
            # Fallback for edge cases
            n_payload = 8
        else:
            n_payload = 8 + max(0, math.ceil(numerator / denominator)) * coding_rate

        # Payload duration
        t_payload = n_payload * t_sym

        return t_preamble + t_payload

    def throughput_bps(self, spreading_factor: int, bandwidth_hz: int,
                       coding_rate: int) -> float:
        """Calculate effective data throughput.

        This is the LoRa PHY data rate (not accounting for protocol overhead).

        Formula: DR = SF * (BW / 2^SF) * (4 / CR)

        Args:
            spreading_factor: SF 7-12.
            bandwidth_hz: BW in Hz.
            coding_rate: CR 5-8.

        Returns:
            Throughput in bits per second.
        """
        sf = spreading_factor
        bw = bandwidth_hz
        cr = coding_rate

        return sf * (bw / (2 ** sf)) * (4.0 / cr)

    def analyze_preset(self, preset_name: str) -> PresetImpact:
        """Analyze a single preset's coverage impact.

        Args:
            preset_name: Name from PRESET_PARAMS (e.g., 'LONG_FAST').

        Returns:
            PresetImpact with all calculated values.

        Raises:
            ValueError: If preset_name is not recognized.
        """
        params = PRESET_PARAMS.get(preset_name)
        if params is None:
            raise ValueError(f"Unknown preset: {preset_name}")

        sf = params['sf']
        bw = params['bw']
        cr = params['cr']

        # Calculate sensitivity
        sens = self.sensitivity(sf, bw)

        # Link budget = TX power + gains - sensitivity
        lb = self.tx_power_dbm + self.tx_gain_dbi + self.rx_gain_dbi - sens

        # Max range using environment-aware log-distance model
        if self.environment == DeployEnvironment.FREE_SPACE:
            # Pure FSPL for backwards compatibility and LOS reference
            max_range_m = self.max_range_fspl(lb, self.freq_mhz)
        else:
            max_range_m = realistic_max_range(
                lb, self.freq_mhz,
                environment=self.environment,
                building=self.building,
            )
        max_range_km = max_range_m / 1000.0

        # Cap at radio horizon based on antenna heights
        horizon_km = radio_horizon_km(self.antenna_height_m,
                                      self.antenna_height_m)
        practical_limit_km = min(max_range_km, horizon_km)

        # Coverage area (circular)
        coverage_km2 = math.pi * practical_limit_km ** 2

        # Airtime
        at_ms = self.airtime_ms(sf, bw, cr, self.payload_bytes)

        # Throughput
        tp_bps = self.throughput_bps(sf, bw, cr)

        # Duty cycle (assuming 1 packet per 30 seconds, typical Meshtastic)
        duty = (at_ms / 1000.0) / 30.0 * 100.0  # Percentage

        # Packets per hour (at 10% duty cycle limit, US ISM)
        if at_ms > 0:
            max_packets_per_sec = 0.10 / (at_ms / 1000.0)
            pph = int(max_packets_per_sec * 3600)
        else:
            pph = 0

        return PresetImpact(
            preset_name=preset_name,
            spreading_factor=sf,
            bandwidth_hz=bw,
            coding_rate=cr,
            frequency_mhz=self.freq_mhz,
            sensitivity_dbm=sens,
            link_budget_db=lb,
            max_range_km=practical_limit_km,
            max_range_los_km=max_range_km,
            coverage_area_km2=coverage_km2,
            airtime_ms=at_ms,
            throughput_bps=tp_bps,
            duty_cycle_pct=duty,
            packets_per_hour=pph,
            tx_power_dbm=self.tx_power_dbm,
            description=params.get('desc', ''),
            warning=params.get('warning', ''),
        )

    def analyze_all(self) -> List[PresetImpact]:
        """Analyze all presets.

        Returns:
            List of PresetImpact ordered by range (shortest to longest).
        """
        results = []
        for name in PRESET_PARAMS:
            results.append(self.analyze_preset(name))

        # Sort by uncapped LOS range (true physical ordering)
        results.sort(key=lambda p: p.max_range_los_km)
        return results

    def compare(self) -> PresetComparison:
        """Compare all presets and determine rankings.

        Returns:
            PresetComparison with ranked presets and recommendations.
        """
        presets = self.analyze_all()

        # Find best in each category using uncapped LOS range for true comparison
        best_range = max(presets, key=lambda p: p.max_range_los_km)
        best_throughput = max(presets, key=lambda p: p.throughput_bps)
        # Best balance: maximize range × throughput product
        best_balance = max(presets,
                           key=lambda p: p.max_range_los_km * p.throughput_bps)

        return PresetComparison(
            presets=presets,
            best_range=best_range.preset_name,
            best_throughput=best_throughput.preset_name,
            best_balance=best_balance.preset_name,
        )

    def range_at_snr(self, preset_name: str, target_snr_db: float) -> float:
        """Calculate range where received SNR equals target.

        Useful for finding where signal quality degrades from
        EXCELLENT to GOOD, GOOD to FAIR, etc.

        Args:
            preset_name: Preset to analyze.
            target_snr_db: Desired minimum SNR in dB.

        Returns:
            Range in km where SNR equals target.
        """
        params = PRESET_PARAMS.get(preset_name)
        if params is None:
            raise ValueError(f"Unknown preset: {preset_name}")

        # Target received power = noise floor + target SNR
        noise_floor = -174.0 + 10 * math.log10(params['bw']) + NOISE_FIGURE_DB
        target_rx_power = noise_floor + target_snr_db

        # Link budget to achieve this power
        effective_lb = self.tx_power_dbm + self.tx_gain_dbi + self.rx_gain_dbi - target_rx_power

        # Convert to range
        if effective_lb <= 0:
            return 0.0

        range_m = self.max_range_fspl(effective_lb, self.freq_mhz)
        return range_m / 1000.0

    def coverage_zones(self, preset_name: str) -> Dict[str, float]:
        """Calculate coverage zones by signal quality.

        Returns ranges for EXCELLENT, GOOD, FAIR, and MAX signal zones.
        Zone ranges are capped at the demodulation limit (max decodable range).

        Args:
            preset_name: Preset to analyze.

        Returns:
            Dict mapping quality level to range in km.
        """
        impact = self.analyze_preset(preset_name)
        max_decodable = impact.max_range_los_km

        return {
            'excellent_km': min(self.range_at_snr(preset_name, -3.0), max_decodable),
            'good_km': min(self.range_at_snr(preset_name, -7.0), max_decodable),
            'fair_km': min(self.range_at_snr(preset_name, -15.0), max_decodable),
            'max_km': max_decodable,
        }


def compare_presets(tx_power_dbm: int = DEFAULT_TX_POWER_DBM,
                    freq_mhz: float = DEFAULT_FREQ_MHZ,
                    payload_bytes: int = DEFAULT_PAYLOAD_BYTES) -> PresetComparison:
    """Convenience function to compare all presets with given parameters.

    Args:
        tx_power_dbm: Transmit power in dBm.
        freq_mhz: Operating frequency in MHz.
        payload_bytes: Payload size for airtime calculation.

    Returns:
        PresetComparison with all analyzed presets.
    """
    analyzer = PresetAnalyzer(
        tx_power_dbm=tx_power_dbm,
        freq_mhz=freq_mhz,
        payload_bytes=payload_bytes,
    )
    return analyzer.compare()


def format_comparison_table(comparison: PresetComparison) -> str:
    """Format preset comparison as a text table for TUI display.

    Args:
        comparison: PresetComparison from compare_presets().

    Returns:
        Formatted multi-line string table.
    """
    lines = []
    lines.append("=" * 85)
    lines.append(f"{'Preset':<18} {'SF':>2} {'BW kHz':>6} {'Sens dBm':>9} "
                 f"{'Range km':>9} {'kbps':>7} {'Airtime':>8} {'Pkts/hr':>7}")
    lines.append("-" * 85)

    for p in comparison.presets:
        bw_khz = p.bandwidth_hz / 1000
        throughput_kbps = p.throughput_bps / 1000
        airtime_str = f"{p.airtime_ms:.0f} ms"

        marker = ""
        if p.preset_name == comparison.best_range:
            marker = " [R]"
        elif p.preset_name == comparison.best_throughput:
            marker = " [T]"
        elif p.preset_name == comparison.best_balance:
            marker = " [B]"

        lines.append(
            f"{p.preset_name:<18} {p.spreading_factor:>2} {bw_khz:>6.0f} "
            f"{p.sensitivity_dbm:>9.1f} {p.max_range_km:>9.1f} "
            f"{throughput_kbps:>7.2f} {airtime_str:>8} {p.packets_per_hour:>7}{marker}"
        )

    lines.append("-" * 85)
    lines.append(f"  [R] = Best Range: {comparison.best_range}")
    lines.append(f"  [T] = Best Throughput: {comparison.best_throughput}")
    lines.append(f"  [B] = Best Balance (range × throughput): {comparison.best_balance}")
    lines.append("=" * 85)

    return "\n".join(lines)
