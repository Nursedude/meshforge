"""
Callsign Management for MeshForge Amateur Radio Edition

Provides callsign lookup, validation, and management features.
"""

import re
import os
import logging
import threading
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Callable, Tuple
from datetime import datetime, timedelta
from pathlib import Path
import json
import math

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

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
        self.config_dir = config_dir or get_real_user_home() / '.config' / 'meshforge'
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

        Uses the FCC License View API:
        https://data.fcc.gov/api/license-view/basicSearch/getLicenses

        Args:
            callsign: The callsign to look up

        Returns:
            CallsignInfo if found
        """
        try:
            # FCC License View API endpoint
            base_url = "https://data.fcc.gov/api/license-view/basicSearch/getLicenses"
            params = urllib.parse.urlencode({
                'searchValue': callsign,
                'format': 'json'
            })
            url = f"{base_url}?{params}"

            logger.info(f"FCC ULS lookup for {callsign}")

            # Make request with timeout
            request = urllib.request.Request(
                url,
                headers={'User-Agent': 'MeshForge/1.0 (Amateur Radio NOC)'}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))

            # Parse response
            licenses = data.get('Licenses', {}).get('License', [])
            if not licenses:
                logger.info(f"No FCC license found for {callsign}")
                return None

            # Handle single result (not in list) vs multiple results
            if isinstance(licenses, dict):
                licenses = [licenses]

            # Find amateur license (serviceCode 'HA' = Amateur)
            amateur_license = None
            for lic in licenses:
                if lic.get('serviceCode') == 'HA':
                    amateur_license = lic
                    break

            if not amateur_license:
                # Take first license if no amateur found
                amateur_license = licenses[0]

            # Map FCC license class codes to readable names
            license_class_map = {
                'E': 'Amateur Extra',
                'A': 'Advanced',
                'G': 'General',
                'P': 'Technician Plus',
                'T': 'Technician',
                'N': 'Novice',
            }

            # Extract data from FCC response
            fcc_class = amateur_license.get('categoryDesc', '')
            # Try to map operator class if available
            op_class = amateur_license.get('operatorClass', '')
            readable_class = license_class_map.get(op_class, fcc_class)

            # Parse dates
            grant_date = amateur_license.get('grantDate', '')
            expiration_date = amateur_license.get('expiredDate', '')

            # Build CallsignInfo
            info = CallsignInfo(
                callsign=callsign.upper(),
                name=amateur_license.get('licName', ''),
                address=amateur_license.get('addressLine1', ''),
                city=amateur_license.get('addressCity', ''),
                state=amateur_license.get('addressState', ''),
                zip_code=amateur_license.get('addressZIP', ''),
                country='US',
                license_class=readable_class,
                grant_date=grant_date[:10] if grant_date else '',  # YYYY-MM-DD
                expiration_date=expiration_date[:10] if expiration_date else '',
                frn=amateur_license.get('frn', ''),
            )

            logger.info(f"FCC ULS found: {info.callsign} - {info.name} ({info.license_class})")
            return info

        except urllib.error.HTTPError as e:
            logger.warning(f"FCC ULS HTTP error for {callsign}: {e.code}")
            return None
        except urllib.error.URLError as e:
            logger.warning(f"FCC ULS network error for {callsign}: {e.reason}")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"FCC ULS JSON parse error for {callsign}: {e}")
            return None
        except Exception as e:
            logger.warning(f"FCC ULS lookup error for {callsign}: {e}")
            return None

    def lookup_callsign_hamqth(self, callsign: str, session_id: Optional[str] = None) -> Optional[CallsignInfo]:
        """
        Look up callsign using HamQTH.com (free alternative to QRZ).

        HamQTH provides a free XML API for callsign lookups.
        https://www.hamqth.com/developers.php

        Args:
            callsign: The callsign to look up
            session_id: HamQTH session ID (from prior login)

        Returns:
            CallsignInfo if found
        """
        try:
            # HamQTH XML API endpoint
            base_url = "https://www.hamqth.com/xml.php"
            params = urllib.parse.urlencode({
                'id': session_id or '',
                'callsign': callsign,
                'prg': 'MeshForge'
            })
            url = f"{base_url}?{params}"

            logger.info(f"HamQTH lookup for {callsign}")

            request = urllib.request.Request(
                url,
                headers={'User-Agent': 'MeshForge/1.0 (Amateur Radio NOC)'}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                xml_data = response.read().decode('utf-8')

            # Parse XML response
            root = ET.fromstring(xml_data)
            ns = {'h': 'https://www.hamqth.com'}

            # Check for errors
            error = root.find('.//h:error', ns)
            if error is not None:
                logger.info(f"HamQTH error for {callsign}: {error.text}")
                return None

            # Extract search result
            search = root.find('.//h:search', ns)
            if search is None:
                return None

            def get_text(elem_name: str) -> str:
                elem = search.find(f'h:{elem_name}', ns)
                return elem.text if elem is not None and elem.text else ''

            # Get coordinates for grid square calculation
            lat_str = get_text('latitude')
            lon_str = get_text('longitude')
            lat = float(lat_str) if lat_str else 0.0
            lon = float(lon_str) if lon_str else 0.0

            # Calculate grid square if coordinates available
            grid = get_text('grid') or (
                self.coords_to_grid(lat, lon) if lat and lon else ''
            )

            info = CallsignInfo(
                callsign=get_text('callsign').upper() or callsign.upper(),
                name=get_text('nick') or get_text('adr_name'),
                address=get_text('adr_street1'),
                city=get_text('adr_city'),
                state=get_text('us_state'),
                zip_code=get_text('adr_zip'),
                country=get_text('country') or 'US',
                grid_square=grid,
                license_class=get_text('itu'),
                latitude=lat,
                longitude=lon,
                qrz_bio=get_text('bio'),
                qrz_image_url=get_text('picture'),
            )

            logger.info(f"HamQTH found: {info.callsign} - {info.name}")
            return info

        except urllib.error.HTTPError as e:
            logger.warning(f"HamQTH HTTP error for {callsign}: {e.code}")
            return None
        except urllib.error.URLError as e:
            logger.warning(f"HamQTH network error for {callsign}: {e.reason}")
            return None
        except ET.ParseError as e:
            logger.warning(f"HamQTH XML parse error for {callsign}: {e}")
            return None
        except Exception as e:
            logger.warning(f"HamQTH lookup error for {callsign}: {e}")
            return None

    @staticmethod
    def coords_to_grid(lat: float, lon: float) -> str:
        """
        Convert latitude/longitude to Maidenhead grid square.

        The Maidenhead Locator System is used by amateur radio operators
        to specify locations at various levels of precision.

        Args:
            lat: Latitude in decimal degrees (-90 to +90)
            lon: Longitude in decimal degrees (-180 to +180)

        Returns:
            6-character grid square (e.g., 'FN31pr')
        """
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return ''

        # Normalize coordinates
        lon += 180
        lat += 90

        # Field (first pair - uppercase letters)
        field_lon = int(lon / 20)
        field_lat = int(lat / 10)

        # Square (second pair - digits)
        square_lon = int((lon % 20) / 2)
        square_lat = int(lat % 10)

        # Subsquare (third pair - lowercase letters)
        subsquare_lon = int((lon - (field_lon * 20) - (square_lon * 2)) * 12)
        subsquare_lat = int((lat - (field_lat * 10) - square_lat) * 24)

        grid = (
            chr(ord('A') + field_lon) +
            chr(ord('A') + field_lat) +
            str(square_lon) +
            str(square_lat) +
            chr(ord('a') + subsquare_lon) +
            chr(ord('a') + subsquare_lat)
        )

        return grid

    @staticmethod
    def grid_to_coords(grid: str) -> Tuple[float, float]:
        """
        Convert Maidenhead grid square to latitude/longitude.

        Args:
            grid: Grid square (4 or 6 characters, e.g., 'FN31' or 'FN31pr')

        Returns:
            Tuple of (latitude, longitude) at center of grid square
        """
        grid = grid.upper().strip()
        if len(grid) < 4:
            return (0.0, 0.0)

        # Field
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90

        # Square
        lon += int(grid[2]) * 2
        lat += int(grid[3])

        # Subsquare (if 6-char grid)
        if len(grid) >= 6:
            lon += (ord(grid[4].upper()) - ord('A')) / 12 + 1/24
            lat += (ord(grid[5].upper()) - ord('A')) / 24 + 1/48
        else:
            # Center of 4-char grid
            lon += 1
            lat += 0.5

        return (lat, lon)

    def lookup_callsign_multi(
        self,
        callsign: str,
        use_cache: bool = True,
        sources: Optional[List[str]] = None
    ) -> Optional[CallsignInfo]:
        """
        Look up callsign using multiple sources with fallback.

        Tries sources in order until a result is found:
        1. Local cache (if use_cache=True)
        2. FCC ULS (authoritative for US callsigns)
        3. HamQTH (free, includes international)

        Args:
            callsign: The callsign to look up
            use_cache: Whether to check cache first
            sources: List of sources to try ('fcc', 'hamqth')

        Returns:
            CallsignInfo if found from any source
        """
        callsign = callsign.upper().strip()
        sources = sources or ['fcc', 'hamqth']

        # Check cache first
        if use_cache and callsign in self._cache:
            cached = self._cache[callsign]
            logger.debug(f"Callsign {callsign} found in cache")
            return cached

        info = None

        for source in sources:
            if source == 'fcc':
                info = self._lookup_fcc_uls(callsign)
            elif source == 'hamqth':
                info = self.lookup_callsign_hamqth(callsign)

            if info:
                # Update cache
                self._cache[callsign] = info
                self._save_cache()
                return info

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

    def check_license_expiration(self, callsign: Optional[str] = None) -> Dict[str, Any]:
        """
        Check license expiration status.

        Returns detailed information about license expiration including
        days until expiration and warning status.

        Args:
            callsign: Callsign to check (defaults to my_callsign)

        Returns:
            Dictionary with expiration info:
            - callsign: The callsign checked
            - expiration_date: Date string or None
            - is_expired: Boolean
            - days_until_expiration: Integer (negative if expired)
            - status: 'valid', 'expiring_soon', 'expired', 'unknown'
            - message: Human-readable status message
        """
        call = callsign or self.my_callsign
        if not call:
            return {
                'callsign': None,
                'expiration_date': None,
                'is_expired': False,
                'days_until_expiration': None,
                'status': 'unknown',
                'message': 'No callsign specified'
            }

        # Look up callsign info
        info = self.lookup_callsign(call)
        if not info or not info.expiration_date:
            return {
                'callsign': call,
                'expiration_date': None,
                'is_expired': False,
                'days_until_expiration': None,
                'status': 'unknown',
                'message': f'No expiration data found for {call}'
            }

        try:
            exp_date = datetime.strptime(info.expiration_date, "%Y-%m-%d")
            now = datetime.now()
            delta = exp_date - now
            days = delta.days

            if days < 0:
                status = 'expired'
                message = f'{call} license EXPIRED {abs(days)} days ago!'
            elif days <= 90:
                status = 'expiring_soon'
                message = f'{call} license expires in {days} days - renew soon!'
            else:
                status = 'valid'
                message = f'{call} license valid until {info.expiration_date} ({days} days)'

            return {
                'callsign': call,
                'expiration_date': info.expiration_date,
                'is_expired': days < 0,
                'days_until_expiration': days,
                'status': status,
                'message': message
            }

        except ValueError:
            return {
                'callsign': call,
                'expiration_date': info.expiration_date,
                'is_expired': False,
                'days_until_expiration': None,
                'status': 'unknown',
                'message': f'Could not parse expiration date for {call}'
            }

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the callsign cache.

        Returns:
            Dictionary with cache statistics
        """
        return {
            'total_cached': len(self._cache),
            'callsigns': list(self._cache.keys()),
            'cache_file': str(self.cache_file),
            'cache_exists': self.cache_file.exists(),
        }


class StationIDTimer:
    """
    Station Identification Timer per FCC Part 97.119.

    FCC requires station identification:
    - At the end of each communication
    - At least every 10 minutes during a communication

    This timer helps operators stay compliant by:
    - Tracking time since last ID
    - Alerting when ID is due
    - Showing countdown to next required ID
    """

    def __init__(
        self,
        callsign: str,
        interval_minutes: int = 10,
        warning_minutes: int = 1,
        on_id_due: Optional[Callable[[], None]] = None,
        on_warning: Optional[Callable[[int], None]] = None,
    ):
        """
        Initialize Station ID Timer.

        Args:
            callsign: Operator's callsign for ID string
            interval_minutes: ID interval (default 10 per FCC)
            warning_minutes: Minutes before due to warn
            on_id_due: Callback when ID is required
            on_warning: Callback with seconds remaining when warning
        """
        self.callsign = callsign.upper()
        self.interval_minutes = interval_minutes
        self.warning_minutes = warning_minutes
        self.on_id_due = on_id_due
        self.on_warning = on_warning

        self._last_id_time: Optional[datetime] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._warned = False

        # Persistence
        self._state_file = get_real_user_home() / '.config' / 'meshforge' / 'station_id_state.json'
        self._load_state()

    def _load_state(self) -> None:
        """Load timer state from disk"""
        try:
            if self._state_file.exists():
                with open(self._state_file, 'r') as f:
                    data = json.load(f)
                    if data.get('callsign') == self.callsign:
                        last_id = data.get('last_id_time')
                        if last_id:
                            self._last_id_time = datetime.fromisoformat(last_id)
                            logger.debug(f"Loaded last ID time: {self._last_id_time}")
        except Exception as e:
            logger.debug(f"Could not load ID timer state: {e}")

    def _save_state(self) -> None:
        """Save timer state to disk"""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'callsign': self.callsign,
                'last_id_time': self._last_id_time.isoformat() if self._last_id_time else None,
            }
            with open(self._state_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.debug(f"Could not save ID timer state: {e}")

    def start(self) -> None:
        """Start the ID timer background thread"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._thread.start()
        logger.info(f"Station ID timer started for {self.callsign}")

    def stop(self) -> None:
        """Stop the ID timer"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("Station ID timer stopped")

    def _timer_loop(self) -> None:
        """Background timer loop - checks every second"""
        import time
        while self._running:
            try:
                seconds_remaining = self.seconds_until_id_due()

                # Check if warning threshold reached
                warning_threshold = self.warning_minutes * 60
                if 0 < seconds_remaining <= warning_threshold:
                    if not self._warned and self.on_warning:
                        self._warned = True
                        self.on_warning(seconds_remaining)

                # Check if ID is due
                if seconds_remaining <= 0:
                    if self.on_id_due:
                        self.on_id_due()
                    # Reset warning flag after ID is due
                    self._warned = False

                time.sleep(1)
            except Exception as e:
                logger.error(f"Error in ID timer loop: {e}")
                time.sleep(1)

    def record_id(self) -> None:
        """Record that station ID was made"""
        self._last_id_time = datetime.now()
        self._warned = False
        self._save_state()
        logger.info(f"Station ID recorded: {self.callsign} at {self._last_id_time}")

    def seconds_until_id_due(self) -> int:
        """
        Get seconds until next ID is required.

        Returns:
            Seconds remaining (0 or negative means ID is due now)
        """
        if not self._last_id_time:
            return 0  # Never ID'd, due now

        elapsed = datetime.now() - self._last_id_time
        interval_seconds = self.interval_minutes * 60
        remaining = interval_seconds - elapsed.total_seconds()
        return max(0, int(remaining))

    def time_until_id_due(self) -> str:
        """
        Get human-readable time until next ID.

        Returns:
            String like "9:45" or "ID NOW"
        """
        seconds = self.seconds_until_id_due()
        if seconds <= 0:
            return "ID NOW"

        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"

    def is_id_due(self) -> bool:
        """Check if station ID is due now"""
        return self.seconds_until_id_due() <= 0

    def get_id_string(self, tactical: Optional[str] = None) -> str:
        """
        Get proper station identification string.

        Args:
            tactical: Optional tactical callsign

        Returns:
            Identification string per Part 97.119
        """
        if tactical:
            # Tactical ID followed by FCC callsign
            return f"{tactical}, {self.callsign}"
        return self.callsign

    @property
    def last_id_time(self) -> Optional[datetime]:
        """Get the time of last station identification"""
        return self._last_id_time

    @property
    def is_running(self) -> bool:
        """Check if timer is running"""
        return self._running
