"""
Callsign Management for MeshForge Amateur Radio Edition

Provides callsign lookup, validation, and management features.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path
import json

logger = logging.getLogger(__name__)


@dataclass
class CallsignInfo:
    """Amateur radio callsign information"""

    callsign: str
    name: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    country: str = "US"
    grid_square: str = ""
    license_class: str = ""  # Technician, General, Amateur Extra
    grant_date: str = ""
    expiration_date: str = ""
    frn: str = ""  # FCC Registration Number

    # Optional QRZ.com data
    qrz_bio: str = ""
    qrz_image_url: str = ""
    latitude: float = 0.0
    longitude: float = 0.0

    def is_valid(self) -> bool:
        """Check if callsign appears valid"""
        return bool(self.callsign and self.name)

    def is_expired(self) -> bool:
        """Check if license is expired"""
        if not self.expiration_date:
            return False
        try:
            exp_date = datetime.strptime(self.expiration_date, "%Y-%m-%d")
            return exp_date < datetime.now()
        except ValueError:
            return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'callsign': self.callsign,
            'name': self.name,
            'address': self.address,
            'city': self.city,
            'state': self.state,
            'zip_code': self.zip_code,
            'country': self.country,
            'grid_square': self.grid_square,
            'license_class': self.license_class,
            'grant_date': self.grant_date,
            'expiration_date': self.expiration_date,
            'frn': self.frn,
            'latitude': self.latitude,
            'longitude': self.longitude,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CallsignInfo':
        """Create from dictionary"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CallsignManager:
    """
    Manages amateur radio callsign operations.

    Features:
    - Callsign validation
    - FCC ULS database lookup
    - QRZ.com integration (optional)
    - Local callsign cache
    - Station identification reminders
    """

    # Callsign patterns by region
    CALLSIGN_PATTERNS = {
        'US': r'^[AKNW][A-Z]?[0-9][A-Z]{1,3}$',
        'Canada': r'^V[AEY][0-9][A-Z]{2,3}$',
        'UK': r'^[GM][0-9][A-Z]{2,3}$|^[2GM][A-Z][0-9][A-Z]{2,3}$',
        'Germany': r'^D[A-Z][0-9][A-Z]{2,3}$',
        'Japan': r'^J[A-S][0-9][A-Z]{2,3}$',
    }

    # US call districts
    US_DISTRICTS = {
        '0': 'Colorado, Iowa, Kansas, Minnesota, Missouri, Nebraska, North Dakota, South Dakota',
        '1': 'Connecticut, Maine, Massachusetts, New Hampshire, Rhode Island, Vermont',
        '2': 'New Jersey, New York',
        '3': 'Delaware, District of Columbia, Maryland, Pennsylvania',
        '4': 'Alabama, Florida, Georgia, Kentucky, North Carolina, South Carolina, Tennessee, Virginia',
        '5': 'Arkansas, Louisiana, Mississippi, New Mexico, Oklahoma, Texas',
        '6': 'California',
        '7': 'Arizona, Idaho, Montana, Nevada, Oregon, Utah, Washington, Wyoming',
        '8': 'Michigan, Ohio, West Virginia',
        '9': 'Illinois, Indiana, Wisconsin',
    }

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize callsign manager"""
        self.config_dir = config_dir or Path.home() / '.config' / 'meshforge'
        self.cache_file = self.config_dir / 'callsign_cache.json'
        self.my_callsign: Optional[str] = None
        self.my_info: Optional[CallsignInfo] = None
        self._cache: Dict[str, CallsignInfo] = {}
        self._load_cache()

        # Station ID settings
        self.id_interval_minutes = 10  # FCC requires every 10 minutes
        self.last_id_time: Optional[datetime] = None

    def _load_cache(self) -> None:
        """Load callsign cache from disk"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    self._cache = {
                        call: CallsignInfo.from_dict(info)
                        for call, info in data.get('cache', {}).items()
                    }
                    self.my_callsign = data.get('my_callsign')
                    if 'my_info' in data and data['my_info']:
                        self.my_info = CallsignInfo.from_dict(data['my_info'])
            except Exception as e:
                logger.warning(f"Failed to load callsign cache: {e}")

    def _save_cache(self) -> None:
        """Save callsign cache to disk"""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            data = {
                'cache': {call: info.to_dict() for call, info in self._cache.items()},
                'my_callsign': self.my_callsign,
                'my_info': self.my_info.to_dict() if self.my_info else None,
            }
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save callsign cache: {e}")

    def validate_callsign(self, callsign: str, country: str = 'US') -> bool:
        """
        Validate callsign format.

        Args:
            callsign: The callsign to validate
            country: Country code for pattern matching

        Returns:
            True if callsign format is valid
        """
        if not callsign:
            return False

        callsign = callsign.upper().strip()
        pattern = self.CALLSIGN_PATTERNS.get(country, self.CALLSIGN_PATTERNS['US'])

        return bool(re.match(pattern, callsign))

    def get_call_district(self, callsign: str) -> Optional[str]:
        """Get US call district description from callsign"""
        if not callsign:
            return None

        # Find the digit in the callsign
        for char in callsign:
            if char.isdigit():
                return self.US_DISTRICTS.get(char)

        return None

    def set_my_callsign(self, callsign: str, info: Optional[CallsignInfo] = None) -> bool:
        """
        Set the operator's callsign.

        Args:
            callsign: The operator's callsign
            info: Optional callsign information

        Returns:
            True if callsign was set successfully
        """
        if not self.validate_callsign(callsign):
            logger.warning(f"Invalid callsign format: {callsign}")
            return False

        self.my_callsign = callsign.upper()
        self.my_info = info or CallsignInfo(callsign=self.my_callsign)
        self._save_cache()

        logger.info(f"Set operator callsign: {self.my_callsign}")
        return True

    def lookup_callsign(self, callsign: str, use_cache: bool = True) -> Optional[CallsignInfo]:
        """
        Look up callsign information.

        Args:
            callsign: The callsign to look up
            use_cache: Whether to use cached data

        Returns:
            CallsignInfo if found, None otherwise
        """
        callsign = callsign.upper().strip()

        # Check cache first
        if use_cache and callsign in self._cache:
            logger.debug(f"Callsign {callsign} found in cache")
            return self._cache[callsign]

        # Try FCC ULS lookup (placeholder - would need actual API implementation)
        info = self._lookup_fcc_uls(callsign)

        if info:
            self._cache[callsign] = info
            self._save_cache()

        return info

    def _lookup_fcc_uls(self, callsign: str) -> Optional[CallsignInfo]:
        """
        Look up callsign in FCC ULS database.

        Note: This is a placeholder. Full implementation would use:
        - FCC ULS API: https://wireless2.fcc.gov/UlsApp/UlsSearch/searchLicense.jsp
        - Or parse FCC daily/weekly database dumps

        Args:
            callsign: The callsign to look up

        Returns:
            CallsignInfo if found
        """
        # Placeholder - return basic info structure
        # Real implementation would make HTTP request to FCC ULS
        logger.info(f"FCC ULS lookup for {callsign} (placeholder)")
        return None

    def should_identify(self) -> bool:
        """
        Check if station identification is due.

        Per FCC Part 97.119, identification must occur:
        - At the end of each communication
        - At least every 10 minutes during a communication

        Returns:
            True if identification is needed
        """
        if not self.last_id_time:
            return True

        elapsed = (datetime.now() - self.last_id_time).total_seconds() / 60
        return elapsed >= self.id_interval_minutes

    def record_identification(self) -> None:
        """Record that station identification was made"""
        self.last_id_time = datetime.now()
        logger.debug(f"Station ID recorded at {self.last_id_time}")

    def get_id_string(self, tactical: Optional[str] = None) -> str:
        """
        Get station identification string.

        Args:
            tactical: Optional tactical callsign

        Returns:
            Proper station ID string
        """
        if not self.my_callsign:
            return ""

        if tactical:
            return f"{tactical}, {self.my_callsign}"

        return self.my_callsign

    def get_cached_callsigns(self) -> List[str]:
        """Get list of cached callsigns"""
        return list(self._cache.keys())

    def clear_cache(self) -> None:
        """Clear the callsign cache"""
        self._cache.clear()
        self._save_cache()
