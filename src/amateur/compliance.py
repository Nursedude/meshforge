"""
Part 97 Compliance for MeshForge Amateur Radio Edition

Provides FCC Part 97 reference and compliance checking features.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class LicenseClass(Enum):
    """Amateur radio license classes"""
    NOVICE = "Novice"  # Legacy, no longer issued
    TECHNICIAN = "Technician"
    GENERAL = "General"
    ADVANCED = "Advanced"  # Legacy, no longer issued
    EXTRA = "Amateur Extra"


class RegulatoryFramework(Enum):
    """Regulatory frameworks for operation"""
    PART_97 = "Part 97"    # FCC Amateur Radio (licensed)
    PART_15 = "Part 15"    # FCC ISM/Unlicensed
    ITU_REGION_1 = "ITU Region 1"  # Europe, Africa, Middle East
    ITU_REGION_2 = "ITU Region 2"  # Americas
    ITU_REGION_3 = "ITU Region 3"  # Asia, Pacific


class EmissionMode(Enum):
    """Common emission modes"""
    CW = "CW"           # Continuous Wave (Morse)
    SSB = "SSB"         # Single Sideband (Voice)
    LSB = "LSB"         # Lower Sideband
    USB = "USB"         # Upper Sideband
    AM = "AM"           # Amplitude Modulation
    FM = "FM"           # Frequency Modulation
    DIGITAL = "Digital" # Digital modes (FT8, JS8Call, etc.)
    RTTY = "RTTY"       # Radio Teletype
    DATA = "Data"       # Data modes
    IMAGE = "Image"     # SSTV, FAX
    ATV = "ATV"         # Amateur Television
    LORA = "LoRa"       # LoRa spread spectrum (Meshtastic)


@dataclass
class BandSegment:
    """
    Detailed band segment with privileges by license class.

    This provides granular control over who can operate where
    and with what modes - essential for Part 97 compliance.
    """
    band: str                      # Band name (e.g., "80m")
    segment_name: str              # Segment description
    frequency_start: float         # MHz
    frequency_end: float           # MHz
    modes: List[EmissionMode]      # Allowed modes
    max_power_watts: int           # Maximum power
    license_classes: List[LicenseClass]  # Who can operate
    notes: str = ""                # Additional notes


@dataclass
class BandPrivilege:
    """
    Band privileges by license class.

    Backwards-compatible class for simple band lookups.
    For detailed segment info, use BandSegment.
    """
    band: str
    frequency_start: float  # MHz
    frequency_end: float  # MHz
    modes: List[str]
    max_power_watts: int
    license_classes: List[LicenseClass]
    notes: str = ""


@dataclass
class ComplianceResult:
    """Result of a compliance check"""
    authorized: bool
    frequency_mhz: float
    band: Optional[str] = None
    segment: Optional[str] = None
    modes_allowed: List[str] = field(default_factory=list)
    max_power_watts: int = 0
    license_required: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def is_compliant(self) -> bool:
        """Check if operation is fully compliant"""
        return self.authorized and len(self.errors) == 0

    def summary(self) -> str:
        """Get human-readable summary"""
        if not self.authorized:
            return f"NOT AUTHORIZED: {', '.join(self.errors)}"

        status = "AUTHORIZED"
        if self.warnings:
            status += f" (with {len(self.warnings)} warning(s))"

        return (
            f"{status}\n"
            f"Band: {self.band or 'Unknown'}\n"
            f"Segment: {self.segment or 'N/A'}\n"
            f"Modes: {', '.join(self.modes_allowed)}\n"
            f"Max Power: {self.max_power_watts}W"
        )


class Part97Reference:
    """
    FCC Part 97 Rules Reference.

    Provides quick access to amateur radio regulations.
    """

    # Key Part 97 rules
    RULES = {
        '97.1': {
            'title': 'Basis and Purpose',
            'summary': 'Amateur radio exists for: (a) Recognition of emergency communications, '
                      '(b) Advancement of radio art, (c) Improvement of communications, '
                      '(d) Expansion of trained operators, (e) International goodwill'
        },
        '97.3': {
            'title': 'Definitions',
            'summary': 'Defines terms used in Part 97 including amateur service, '
                      'amateur station, amateur operator, etc.'
        },
        '97.5': {
            'title': 'Station License Required',
            'summary': 'Amateur station must be licensed. License grants authority to '
                      'transmit on amateur frequencies.'
        },
        '97.7': {
            'title': 'Control Operator Required',
            'summary': 'Amateur station must have control operator present and responsible '
                      'for all transmissions.'
        },
        '97.101': {
            'title': 'General Standards',
            'summary': '(a) Good engineering practices, (b) Good amateur practice, '
                      '(c) No harmful interference, (d) Station ID required'
        },
        '97.103': {
            'title': 'Station Licensee Responsibilities',
            'summary': 'Licensee responsible for proper operation, maintaining station records, '
                      'and ensuring compliance.'
        },
        '97.105': {
            'title': 'Control Operator Duties',
            'summary': 'Control operator responsible for emissions, must ensure compliance '
                      'with rules and regulations.'
        },
        '97.111': {
            'title': 'Authorized Transmissions',
            'summary': 'Permitted: (a) One-way for beacons, telecommand, telemetry, '
                      '(b) Two-way communications, (c) Brief tests'
        },
        '97.113': {
            'title': 'Prohibited Transmissions',
            'summary': '(a) Music, (b) Business/commercial, (c) Broadcasting, '
                      '(d) Obscene language, (e) False signals, (f) Third-party without agreement'
        },
        '97.115': {
            'title': 'Third Party Communications',
            'summary': 'Control operator responsibility. Permitted with countries having '
                      'third-party agreements. Must ID with callsign.'
        },
        '97.117': {
            'title': 'International Communications',
            'summary': 'Transmissions must be limited to those permitted by ITU Radio Regulations.'
        },
        '97.119': {
            'title': 'Station Identification',
            'summary': '(a) At end of communication, (b) Every 10 minutes, '
                      '(c) English language, (d) May use phonetics'
        },
        '97.121': {
            'title': 'Restricted Operation',
            'summary': 'FCC may restrict operation during disasters or emergencies.'
        },
        '97.301': {
            'title': 'Authorized Frequency Bands',
            'summary': 'Lists all amateur frequency allocations by license class.'
        },
        '97.303': {
            'title': 'Frequency Sharing Requirements',
            'summary': 'Amateur service shares some bands with other services.'
        },
        '97.305': {
            'title': 'Authorized Emission Types',
            'summary': 'Lists emission types permitted on each band.'
        },
        '97.307': {
            'title': 'Emission Standards',
            'summary': 'Technical standards for emissions including spurious and out-of-band.'
        },
        '97.309': {
            'title': 'RTTY and Data Emission Codes',
            'summary': 'Specifies permitted digital mode encoding schemes.'
        },
        '97.311': {
            'title': 'SS Emission Types',
            'summary': 'Spread spectrum emission requirements and restrictions.'
        },
        '97.313': {
            'title': 'Transmitter Power Standards',
            'summary': 'Maximum 1500W PEP. Use minimum power necessary.'
        },
        '97.403': {
            'title': 'Safety of Life',
            'summary': 'Amateur station may be used for emergency communications '
                      'involving immediate safety of life.'
        },
        '97.405': {
            'title': 'Station in Distress',
            'summary': 'Station in distress may use any means to attract attention.'
        },
        '97.407': {
            'title': 'Radio Amateur Civil Emergency Service (RACES)',
            'summary': 'RACES operation authorized during civil defense emergencies.'
        },
    }

    # Amateur band allocations (US)
    BANDS = [
        BandPrivilege(
            band="160m",
            frequency_start=1.8,
            frequency_end=2.0,
            modes=["CW", "Phone", "Digital"],
            max_power_watts=1500,
            license_classes=[LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Night propagation, noise levels high"
        ),
        BandPrivilege(
            band="80m",
            frequency_start=3.5,
            frequency_end=4.0,
            modes=["CW", "Phone", "Digital"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Tech privileges: 3.525-3.600 CW only"
        ),
        BandPrivilege(
            band="40m",
            frequency_start=7.0,
            frequency_end=7.3,
            modes=["CW", "Phone", "Digital"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Popular worldwide band"
        ),
        BandPrivilege(
            band="20m",
            frequency_start=14.0,
            frequency_end=14.35,
            modes=["CW", "Phone", "Digital"],
            max_power_watts=1500,
            license_classes=[LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Primary DX band"
        ),
        BandPrivilege(
            band="15m",
            frequency_start=21.0,
            frequency_end=21.45,
            modes=["CW", "Phone", "Digital"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Tech: 21.025-21.200 CW only"
        ),
        BandPrivilege(
            band="10m",
            frequency_start=28.0,
            frequency_end=29.7,
            modes=["CW", "Phone", "Digital", "FM", "Repeater"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Tech: Full privileges"
        ),
        BandPrivilege(
            band="6m",
            frequency_start=50.0,
            frequency_end=54.0,
            modes=["CW", "Phone", "Digital", "FM", "SSB"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Magic band - sporadic E propagation"
        ),
        BandPrivilege(
            band="2m",
            frequency_start=144.0,
            frequency_end=148.0,
            modes=["CW", "Phone", "Digital", "FM", "SSB", "Packet"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Most popular VHF band"
        ),
        BandPrivilege(
            band="1.25m",
            frequency_start=222.0,
            frequency_end=225.0,
            modes=["CW", "Phone", "Digital", "FM"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="220 MHz band"
        ),
        BandPrivilege(
            band="70cm",
            frequency_start=420.0,
            frequency_end=450.0,
            modes=["CW", "Phone", "Digital", "FM", "ATV", "Packet"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Popular UHF band, ATV"
        ),
        BandPrivilege(
            band="33cm",
            frequency_start=902.0,
            frequency_end=928.0,
            modes=["CW", "Phone", "Digital", "FM"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="33cm band, ISM shared"
        ),
        BandPrivilege(
            band="23cm",
            frequency_start=1240.0,
            frequency_end=1300.0,
            modes=["CW", "Phone", "Digital", "ATV"],
            max_power_watts=1500,
            license_classes=[LicenseClass.TECHNICIAN, LicenseClass.GENERAL,
                           LicenseClass.ADVANCED, LicenseClass.EXTRA],
            notes="Microwave, satellite uplinks"
        ),
    ]

    # Detailed band segments with license-specific privileges
    # This is the authoritative data for compliance checking
    BAND_SEGMENTS: List[BandSegment] = [
        # 160m - General/Advanced/Extra only
        BandSegment("160m", "CW", 1.800, 1.810, [EmissionMode.CW], 1500,
                   [LicenseClass.EXTRA], "Extra CW exclusive"),
        BandSegment("160m", "CW/RTTY/Data", 1.810, 2.000, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),

        # 80m - Tech has limited CW privileges
        BandSegment("80m", "Extra CW", 3.500, 3.525, [EmissionMode.CW], 1500,
                   [LicenseClass.EXTRA], "Extra CW exclusive"),
        BandSegment("80m", "CW/RTTY/Data", 3.525, 3.600, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "Tech CW privileges"),
        BandSegment("80m", "Extra CW/Phone", 3.600, 3.700, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.EXTRA]),
        BandSegment("80m", "Adv/Extra Phone", 3.700, 3.800, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.ADVANCED, LicenseClass.EXTRA]),
        BandSegment("80m", "Gen/Adv/Extra Phone", 3.800, 4.000, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),

        # 40m
        BandSegment("40m", "Extra CW", 7.000, 7.025, [EmissionMode.CW], 1500,
                   [LicenseClass.EXTRA]),
        BandSegment("40m", "CW/RTTY/Data", 7.025, 7.125, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),
        BandSegment("40m", "Extra Phone", 7.125, 7.175, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.EXTRA]),
        BandSegment("40m", "Gen/Adv/Extra Phone", 7.175, 7.300, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),

        # 30m - CW/Digital only, 200W max
        BandSegment("30m", "CW/RTTY/Data", 10.100, 10.150, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 200,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "200W max, CW/Digital only"),

        # 20m
        BandSegment("20m", "Extra CW", 14.000, 14.025, [EmissionMode.CW], 1500,
                   [LicenseClass.EXTRA]),
        BandSegment("20m", "CW/RTTY/Data", 14.025, 14.150, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),
        BandSegment("20m", "Extra Phone", 14.150, 14.175, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.EXTRA]),
        BandSegment("20m", "Adv/Extra Phone", 14.175, 14.225, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.ADVANCED, LicenseClass.EXTRA]),
        BandSegment("20m", "Gen/Adv/Extra Phone", 14.225, 14.350, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),

        # 17m - General and up
        BandSegment("17m", "CW/RTTY/Data", 18.068, 18.110, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),
        BandSegment("17m", "Phone", 18.110, 18.168, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),

        # 15m - Tech has limited CW
        BandSegment("15m", "Extra CW", 21.000, 21.025, [EmissionMode.CW], 1500,
                   [LicenseClass.EXTRA]),
        BandSegment("15m", "CW/RTTY/Data", 21.025, 21.200, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "Tech CW privileges"),
        BandSegment("15m", "Extra Phone", 21.200, 21.225, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.EXTRA]),
        BandSegment("15m", "Adv/Extra Phone", 21.225, 21.275, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.ADVANCED, LicenseClass.EXTRA]),
        BandSegment("15m", "Gen/Adv/Extra Phone", 21.275, 21.450, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),

        # 12m - General and up
        BandSegment("12m", "CW/RTTY/Data", 24.890, 24.930, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),
        BandSegment("12m", "Phone", 24.930, 24.990, [EmissionMode.CW, EmissionMode.SSB], 1500,
                   [LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),

        # 10m - Tech has full privileges
        BandSegment("10m", "CW", 28.000, 28.300, [EmissionMode.CW, EmissionMode.RTTY, EmissionMode.DATA], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),
        BandSegment("10m", "Phone/CW", 28.300, 29.700, [EmissionMode.CW, EmissionMode.SSB, EmissionMode.FM], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "Tech full privileges"),

        # 6m - Tech full privileges
        BandSegment("6m", "All Modes", 50.000, 54.000,
                   [EmissionMode.CW, EmissionMode.SSB, EmissionMode.FM, EmissionMode.DIGITAL, EmissionMode.RTTY], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "Magic band - sporadic E"),

        # 2m - Tech full privileges
        BandSegment("2m", "All Modes", 144.000, 148.000,
                   [EmissionMode.CW, EmissionMode.SSB, EmissionMode.FM, EmissionMode.DIGITAL, EmissionMode.DATA], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "Most popular VHF band"),

        # 1.25m - Tech full privileges
        BandSegment("1.25m", "All Modes", 222.000, 225.000,
                   [EmissionMode.CW, EmissionMode.SSB, EmissionMode.FM, EmissionMode.DIGITAL], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA]),

        # 70cm - Tech full privileges
        BandSegment("70cm", "All Modes", 420.000, 450.000,
                   [EmissionMode.CW, EmissionMode.SSB, EmissionMode.FM, EmissionMode.DIGITAL, EmissionMode.ATV], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "UHF, ATV, Repeaters"),

        # 33cm
        BandSegment("33cm", "All Modes", 902.000, 928.000,
                   [EmissionMode.CW, EmissionMode.SSB, EmissionMode.FM, EmissionMode.DIGITAL], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "ISM shared"),

        # 23cm
        BandSegment("23cm", "All Modes", 1240.000, 1300.000,
                   [EmissionMode.CW, EmissionMode.SSB, EmissionMode.FM, EmissionMode.DIGITAL, EmissionMode.ATV], 1500,
                   [LicenseClass.TECHNICIAN, LicenseClass.GENERAL, LicenseClass.ADVANCED, LicenseClass.EXTRA],
                   "Microwave entry"),
    ]

    # ISM (Part 15) Bands - Unlicensed operation
    # These overlap with some amateur bands but have different rules
    ISM_BANDS = {
        'US': [
            {
                'name': '900 MHz ISM',
                'frequency_start': 902.0,
                'frequency_end': 928.0,
                'max_power_dbm': 30,  # 1W EIRP
                'max_power_watts': 1.0,
                'notes': 'Meshtastic US default, spread spectrum required',
                'meshtastic_region': 'US',
            },
            {
                'name': '2.4 GHz ISM',
                'frequency_start': 2400.0,
                'frequency_end': 2483.5,
                'max_power_dbm': 30,
                'max_power_watts': 1.0,
                'notes': 'WiFi, Bluetooth, LoRa 2.4G',
                'meshtastic_region': None,
            },
        ],
        'EU': [
            {
                'name': '868 MHz ISM',
                'frequency_start': 863.0,
                'frequency_end': 870.0,
                'max_power_dbm': 14,  # 25mW ERP
                'max_power_watts': 0.025,
                'duty_cycle': 0.01,  # 1% duty cycle
                'notes': 'EU LoRa band, strict duty cycle limits',
                'meshtastic_region': 'EU_868',
            },
        ],
        'AU_NZ': [
            {
                'name': '915 MHz ISM',
                'frequency_start': 915.0,
                'frequency_end': 928.0,
                'max_power_dbm': 30,
                'max_power_watts': 1.0,
                'notes': 'Australia/NZ LoRa band',
                'meshtastic_region': 'ANZ',
            },
        ],
    }

    # Meshtastic-specific frequency presets (for quick reference)
    MESHTASTIC_PRESETS = {
        'US': {'freq': 906.875, 'bw': 250, 'sf': 11, 'power_dbm': 30},
        'EU_868': {'freq': 869.525, 'bw': 125, 'sf': 12, 'power_dbm': 14},
        'ANZ': {'freq': 916.0, 'bw': 250, 'sf': 11, 'power_dbm': 30},
        'JP': {'freq': 920.9, 'bw': 125, 'sf': 10, 'power_dbm': 13},
        'KR': {'freq': 921.9, 'bw': 125, 'sf': 12, 'power_dbm': 14},
        'TW': {'freq': 923.0, 'bw': 125, 'sf': 12, 'power_dbm': 27},
    }

    @classmethod
    def get_ism_band(cls, freq_mhz: float, region: str = 'US') -> Optional[Dict]:
        """Get ISM band info for a frequency"""
        if region not in cls.ISM_BANDS:
            return None
        for band in cls.ISM_BANDS[region]:
            if band['frequency_start'] <= freq_mhz <= band['frequency_end']:
                return band
        return None

    @classmethod
    def check_ism_compliance(
        cls,
        freq_mhz: float,
        power_dbm: float,
        region: str = 'US'
    ) -> ComplianceResult:
        """
        Check ISM (Part 15) compliance for unlicensed operation.

        Args:
            freq_mhz: Frequency in MHz
            power_dbm: Transmit power in dBm
            region: Geographic region (US, EU, AU_NZ, etc.)

        Returns:
            ComplianceResult for ISM operation
        """
        band = cls.get_ism_band(freq_mhz, region)

        if not band:
            return ComplianceResult(
                authorized=False,
                frequency_mhz=freq_mhz,
                errors=[f"{freq_mhz} MHz is not in ISM band for {region}"]
            )

        warnings = []
        errors = []

        # Check power limit
        if power_dbm > band['max_power_dbm']:
            errors.append(
                f"Power {power_dbm} dBm exceeds ISM limit of {band['max_power_dbm']} dBm"
            )

        # Add duty cycle warning for EU
        if 'duty_cycle' in band:
            warnings.append(f"Duty cycle limit: {band['duty_cycle']*100}%")

        if band.get('notes'):
            warnings.append(band['notes'])

        return ComplianceResult(
            authorized=len(errors) == 0,
            frequency_mhz=freq_mhz,
            band=band['name'],
            segment="ISM/Unlicensed",
            modes_allowed=["LoRa", "Spread Spectrum"],
            max_power_watts=band['max_power_watts'],
            warnings=warnings,
            errors=errors
        )

    @classmethod
    def compare_part97_vs_ism(cls, freq_mhz: float, license_class: LicenseClass) -> Dict[str, Any]:
        """
        Compare Part 97 vs ISM operation for a frequency.

        Helps operators choose which regulatory framework to use.

        Args:
            freq_mhz: Frequency in MHz
            license_class: Operator's license class

        Returns:
            Comparison of Part 97 and ISM options
        """
        part97_result = cls.check_frequency_privilege(freq_mhz, license_class)
        ism_result = cls.check_ism_compliance(freq_mhz, 30)  # Check at 1W

        return {
            'frequency_mhz': freq_mhz,
            'part_97': {
                'authorized': part97_result.authorized,
                'max_power_watts': part97_result.max_power_watts,
                'requires_license': True,
                'station_id_required': True,
                'advantages': [
                    'Higher power allowed (up to 1500W)',
                    'Full amateur privileges',
                    'Can use any amateur mode',
                ],
                'notes': part97_result.warnings,
            },
            'ism_part_15': {
                'authorized': ism_result.authorized,
                'max_power_watts': ism_result.max_power_watts if ism_result.authorized else 0,
                'requires_license': False,
                'station_id_required': False,
                'advantages': [
                    'No license required',
                    'No station ID required',
                    'Good for public deployment',
                ],
                'limitations': [
                    f"Max power: {ism_result.max_power_watts}W" if ism_result.authorized else "Not available",
                    'Must accept interference',
                    'Duty cycle limits may apply',
                ],
                'notes': ism_result.warnings,
            },
            'recommendation': (
                'Part 97' if part97_result.authorized and part97_result.max_power_watts > 100
                else 'ISM' if ism_result.authorized
                else 'Neither available'
            ),
        }

    @classmethod
    def get_segment_by_frequency(cls, freq_mhz: float) -> Optional[BandSegment]:
        """Get detailed band segment by frequency"""
        for segment in cls.BAND_SEGMENTS:
            if segment.frequency_start <= freq_mhz <= segment.frequency_end:
                return segment
        return None

    @classmethod
    def get_segments_for_license(cls, license_class: LicenseClass) -> List[BandSegment]:
        """Get all band segments available for a license class"""
        return [seg for seg in cls.BAND_SEGMENTS if license_class in seg.license_classes]

    @classmethod
    def check_frequency_privilege(
        cls,
        freq_mhz: float,
        license_class: LicenseClass,
        mode: Optional[EmissionMode] = None
    ) -> ComplianceResult:
        """
        Comprehensive frequency privilege check.

        Args:
            freq_mhz: Frequency in MHz
            license_class: Operator's license class
            mode: Optional emission mode to validate

        Returns:
            ComplianceResult with detailed authorization info
        """
        segment = cls.get_segment_by_frequency(freq_mhz)

        if not segment:
            return ComplianceResult(
                authorized=False,
                frequency_mhz=freq_mhz,
                errors=[f"{freq_mhz} MHz is outside amateur allocations"]
            )

        # Check license class privilege
        authorized = license_class in segment.license_classes
        warnings = []
        errors = []

        if not authorized:
            required = min(segment.license_classes, key=lambda x: list(LicenseClass).index(x))
            errors.append(
                f"{segment.band} {segment.segment_name} requires {required.value} or higher"
            )

        # Check mode if specified
        modes_allowed = [m.value for m in segment.modes]
        if mode and mode not in segment.modes:
            errors.append(f"{mode.value} not permitted in this segment. Allowed: {', '.join(modes_allowed)}")
            authorized = False

        # Add notes as warnings
        if segment.notes:
            warnings.append(segment.notes)

        return ComplianceResult(
            authorized=authorized,
            frequency_mhz=freq_mhz,
            band=segment.band,
            segment=segment.segment_name,
            modes_allowed=modes_allowed,
            max_power_watts=segment.max_power_watts,
            license_required=min(segment.license_classes, key=lambda x: list(LicenseClass).index(x)).value if segment.license_classes else None,
            warnings=warnings,
            errors=errors
        )

    @classmethod
    def get_rule(cls, rule_number: str) -> Optional[Dict[str, str]]:
        """Get a specific rule by number"""
        return cls.RULES.get(rule_number)

    @classmethod
    def search_rules(cls, keyword: str) -> List[Dict[str, Any]]:
        """Search rules by keyword"""
        keyword = keyword.lower()
        results = []

        for number, rule in cls.RULES.items():
            if (keyword in rule['title'].lower() or
                keyword in rule['summary'].lower()):
                results.append({
                    'number': number,
                    'title': rule['title'],
                    'summary': rule['summary']
                })

        return results

    @classmethod
    def get_band_by_frequency(cls, freq_mhz: float) -> Optional[BandPrivilege]:
        """Get band information by frequency"""
        for band in cls.BANDS:
            if band.frequency_start <= freq_mhz <= band.frequency_end:
                return band
        return None

    @classmethod
    def get_bands_for_license(cls, license_class: LicenseClass) -> List[BandPrivilege]:
        """Get all bands available for a license class"""
        return [band for band in cls.BANDS if license_class in band.license_classes]


class ComplianceChecker:
    """
    Checks amateur radio operations for Part 97 compliance.
    """

    def __init__(self, license_class: LicenseClass = LicenseClass.TECHNICIAN):
        self.license_class = license_class
        self._warnings: List[str] = []

    def check_frequency(self, freq_mhz: float) -> Dict[str, Any]:
        """
        Check if frequency is authorized for license class.

        Args:
            freq_mhz: Frequency in MHz

        Returns:
            Dict with 'authorized', 'band', 'warnings'
        """
        self._warnings.clear()

        band = Part97Reference.get_band_by_frequency(freq_mhz)

        if not band:
            return {
                'authorized': False,
                'band': None,
                'warnings': ['Frequency is outside amateur allocations']
            }

        authorized = self.license_class in band.license_classes

        if not authorized:
            self._warnings.append(
                f"{band.band} requires {', '.join(lc.value for lc in band.license_classes)} "
                f"license or higher"
            )

        return {
            'authorized': authorized,
            'band': band,
            'warnings': self._warnings.copy()
        }

    def check_power(self, power_watts: float, freq_mhz: float) -> Dict[str, Any]:
        """
        Check if power level is authorized.

        Args:
            power_watts: Transmit power in watts
            freq_mhz: Operating frequency in MHz

        Returns:
            Dict with 'authorized', 'max_power', 'warnings'
        """
        self._warnings.clear()

        band = Part97Reference.get_band_by_frequency(freq_mhz)

        if not band:
            return {
                'authorized': False,
                'max_power': 0,
                'warnings': ['Frequency not in amateur allocation']
            }

        # Check maximum power
        authorized = power_watts <= band.max_power_watts

        if not authorized:
            self._warnings.append(
                f"Power exceeds maximum {band.max_power_watts}W for {band.band}"
            )

        # Remind about minimum power rule (97.313)
        if power_watts > 200:
            self._warnings.append(
                "Reminder: Use minimum power necessary (§97.313)"
            )

        return {
            'authorized': authorized,
            'max_power': band.max_power_watts,
            'warnings': self._warnings.copy()
        }

    def check_content(self, message: str) -> Dict[str, Any]:
        """
        Check message content for prohibited transmissions.

        Per §97.113, prohibited content includes:
        - Business/commercial content
        - Broadcasting
        - Obscene/indecent language
        - False/deceptive signals

        Args:
            message: Message text to check

        Returns:
            Dict with 'compliant', 'warnings'
        """
        self._warnings.clear()
        compliant = True

        # Check for obvious commercial indicators
        commercial_terms = ['buy', 'sell', 'price', '$', 'discount', 'sale', 'order']
        message_lower = message.lower()

        for term in commercial_terms:
            if term in message_lower:
                self._warnings.append(
                    f"Message may contain commercial content ('{term}'). "
                    "Review §97.113(a)(3) - business communications prohibited."
                )
                break

        # Check message length (reasonable limit for mesh networks)
        if len(message) > 500:
            self._warnings.append(
                "Long message - consider brevity for efficient spectrum use"
            )

        return {
            'compliant': compliant,
            'warnings': self._warnings.copy()
        }

    def get_id_reminder(self, last_id_minutes: float) -> Optional[str]:
        """
        Get station ID reminder if needed.

        Per §97.119, station must identify:
        - At end of communication
        - Every 10 minutes during communication

        Args:
            last_id_minutes: Minutes since last identification

        Returns:
            Reminder string if ID needed, None otherwise
        """
        if last_id_minutes >= 10:
            return "Station identification required (§97.119)"
        elif last_id_minutes >= 8:
            return f"Station ID due in {10 - last_id_minutes:.0f} minutes"

        return None
