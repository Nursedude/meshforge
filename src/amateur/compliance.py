"""
Part 97 Compliance for MeshForge Amateur Radio Edition

Provides FCC Part 97 reference and compliance checking features.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class LicenseClass(Enum):
    """Amateur radio license classes"""
    NOVICE = "Novice"  # Legacy, no longer issued
    TECHNICIAN = "Technician"
    GENERAL = "General"
    ADVANCED = "Advanced"  # Legacy, no longer issued
    EXTRA = "Amateur Extra"


@dataclass
class BandPrivilege:
    """Band privileges by license class"""
    band: str
    frequency_start: float  # MHz
    frequency_end: float  # MHz
    modes: List[str]
    max_power_watts: int
    license_classes: List[LicenseClass]
    notes: str = ""


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
