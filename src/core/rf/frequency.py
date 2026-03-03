"""
Frequency Slot Calculator and Validator

Utility for working with LoRa frequency slots across regions.
Supports Meshtastic channel_num and RNode frequency configuration.

Usage:
    from utils.frequency import FrequencyCalculator

    calc = FrequencyCalculator("US")
    freq = calc.slot_to_frequency(12)  # 903625000 Hz
    slot = calc.frequency_to_slot(903625000)  # 12
    calc.validate_frequency(903625000)  # True
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RegionConfig:
    """Regional frequency configuration."""
    name: str
    start_freq: int  # Hz
    end_freq: int    # Hz
    num_channels: int
    channel_spacing: int  # Hz
    default_freq: int  # Hz


# Regional frequency definitions
REGIONS: Dict[str, RegionConfig] = {
    "US": RegionConfig(
        name="United States",
        start_freq=902_000_000,
        end_freq=928_000_000,
        num_channels=104,  # Meshtastic uses 104 channels
        channel_spacing=250_000,
        default_freq=903_875_000,
    ),
    "EU_868": RegionConfig(
        name="Europe 868 MHz",
        start_freq=863_000_000,
        end_freq=870_000_000,
        num_channels=8,
        channel_spacing=500_000,
        default_freq=869_525_000,
    ),
    "AU_915": RegionConfig(
        name="Australia 915 MHz",
        start_freq=915_000_000,
        end_freq=928_000_000,
        num_channels=52,
        channel_spacing=250_000,
        default_freq=916_875_000,
    ),
    "NZ_915": RegionConfig(
        name="New Zealand 915 MHz",
        start_freq=915_000_000,
        end_freq=928_000_000,
        num_channels=52,
        channel_spacing=250_000,
        default_freq=916_875_000,
    ),
    "KR_920": RegionConfig(
        name="Korea 920 MHz",
        start_freq=920_000_000,
        end_freq=923_000_000,
        num_channels=12,
        channel_spacing=250_000,
        default_freq=921_875_000,
    ),
    "JP_920": RegionConfig(
        name="Japan 920 MHz",
        start_freq=920_000_000,
        end_freq=923_000_000,
        num_channels=12,
        channel_spacing=200_000,
        default_freq=920_800_000,
    ),
    "IN_865": RegionConfig(
        name="India 865 MHz",
        start_freq=865_000_000,
        end_freq=867_000_000,
        num_channels=4,
        channel_spacing=500_000,
        default_freq=865_625_000,
    ),
}


# US frequency slot mapping (Meshtastic channel_num to frequency)
# Based on Meshtastic firmware frequency calculations
US_SLOT_MAP: Dict[int, int] = {
    0: 903_875_000,
    1: 903_875_000,
    2: 906_125_000,
    3: 908_375_000,
    4: 910_625_000,
    5: 912_875_000,
    6: 915_125_000,
    7: 917_375_000,
    8: 919_625_000,
    9: 921_875_000,
    10: 924_125_000,
    11: 926_375_000,
    # Extended/custom slots
    12: 903_625_000,  # HawaiiNet
    13: 905_875_000,
    14: 907_125_000,
    15: 909_375_000,
    16: 911_625_000,
    17: 913_875_000,
    18: 916_125_000,
    19: 918_375_000,
    20: 920_625_000,
}


class FrequencyCalculator:
    """Calculate and validate LoRa frequencies."""

    def __init__(self, region: str = "US"):
        """
        Initialize frequency calculator.

        Args:
            region: Region code (US, EU_868, AU_915, etc.)
        """
        if region not in REGIONS:
            raise ValueError(f"Unknown region: {region}. Valid: {list(REGIONS.keys())}")
        self.region = region
        self.config = REGIONS[region]

    def slot_to_frequency(self, slot: int) -> int:
        """
        Convert channel slot number to frequency in Hz.

        Args:
            slot: Channel slot number (0-255 for Meshtastic)

        Returns:
            Frequency in Hz
        """
        if self.region == "US" and slot in US_SLOT_MAP:
            return US_SLOT_MAP[slot]

        # Generic calculation for other regions/slots
        freq = self.config.start_freq + (slot * self.config.channel_spacing)
        if freq > self.config.end_freq:
            freq = self.config.default_freq
        return freq

    def frequency_to_slot(self, frequency: int) -> Optional[int]:
        """
        Convert frequency in Hz to nearest channel slot.

        Args:
            frequency: Frequency in Hz

        Returns:
            Channel slot number or None if not in valid range
        """
        if not self.validate_frequency(frequency):
            return None

        # Check US slot map first
        if self.region == "US":
            for slot, freq in US_SLOT_MAP.items():
                if abs(freq - frequency) < 50_000:  # Within 50 kHz tolerance
                    return slot

        # Generic calculation
        offset = frequency - self.config.start_freq
        slot = offset // self.config.channel_spacing
        return max(0, min(slot, self.config.num_channels - 1))

    def validate_frequency(self, frequency: int) -> bool:
        """
        Check if frequency is valid for this region.

        Args:
            frequency: Frequency in Hz

        Returns:
            True if frequency is within valid range
        """
        return self.config.start_freq <= frequency <= self.config.end_freq

    def get_frequency_range(self) -> Tuple[int, int]:
        """Get valid frequency range for region."""
        return (self.config.start_freq, self.config.end_freq)

    def get_available_slots(self) -> List[Tuple[int, int]]:
        """
        Get list of available (slot, frequency) pairs.

        Returns:
            List of (slot_number, frequency_hz) tuples
        """
        if self.region == "US":
            return sorted(US_SLOT_MAP.items())

        slots = []
        for i in range(self.config.num_channels):
            freq = self.slot_to_frequency(i)
            slots.append((i, freq))
        return slots

    def format_frequency(self, frequency: int) -> str:
        """Format frequency for display (Hz to MHz)."""
        mhz = frequency / 1_000_000
        return f"{mhz:.3f} MHz"

    @classmethod
    def get_regions(cls) -> List[str]:
        """Get list of available region codes."""
        return list(REGIONS.keys())

    @classmethod
    def get_region_info(cls, region: str) -> Optional[RegionConfig]:
        """Get configuration for a region."""
        return REGIONS.get(region)


# Convenience functions
def hz_to_mhz(frequency: int) -> float:
    """Convert Hz to MHz."""
    return frequency / 1_000_000


def mhz_to_hz(frequency: float) -> int:
    """Convert MHz to Hz."""
    return int(frequency * 1_000_000)


def slot_to_freq(slot: int, region: str = "US") -> int:
    """Quick slot to frequency conversion."""
    return FrequencyCalculator(region).slot_to_frequency(slot)


def freq_to_slot(frequency: int, region: str = "US") -> Optional[int]:
    """Quick frequency to slot conversion."""
    return FrequencyCalculator(region).frequency_to_slot(frequency)


def validate_frequency(frequency: int, region: str = "US") -> bool:
    """Quick frequency validation."""
    return FrequencyCalculator(region).validate_frequency(frequency)


def get_hawaiinet_frequency() -> int:
    """Get HawaiiNet frequency (903.625 MHz)."""
    return 903_625_000


def get_default_frequency(region: str = "US") -> int:
    """Get default frequency for a region."""
    config = REGIONS.get(region)
    return config.default_freq if config else 903_875_000


# CLI interface for quick lookups
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        calc = FrequencyCalculator("US")

        if sys.argv[1] == "slots":
            print("US Frequency Slots:")
            print("-" * 40)
            for slot, freq in calc.get_available_slots():
                print(f"  Slot {slot:2d}: {calc.format_frequency(freq)}")

        elif sys.argv[1] == "regions":
            print("Available Regions:")
            print("-" * 40)
            for code, config in REGIONS.items():
                print(f"  {code}: {config.name}")
                print(f"         {hz_to_mhz(config.start_freq):.1f} - {hz_to_mhz(config.end_freq):.1f} MHz")

        elif sys.argv[1].isdigit():
            slot = int(sys.argv[1])
            freq = calc.slot_to_frequency(slot)
            print(f"Slot {slot} = {calc.format_frequency(freq)}")

        else:
            try:
                freq = int(float(sys.argv[1]) * 1_000_000)
                slot = calc.frequency_to_slot(freq)
                valid = calc.validate_frequency(freq)
                print(f"{calc.format_frequency(freq)}")
                print(f"  Slot: {slot}")
                print(f"  Valid for US: {valid}")
            except ValueError:
                print(f"Unknown argument: {sys.argv[1]}")
                print("Usage: python frequency.py [slot|frequency|slots|regions]")
    else:
        print("Frequency Slot Calculator")
        print("Usage:")
        print("  python frequency.py 12        # Slot to frequency")
        print("  python frequency.py 903.625   # Frequency to slot (MHz)")
        print("  python frequency.py slots     # List all US slots")
        print("  python frequency.py regions   # List all regions")
