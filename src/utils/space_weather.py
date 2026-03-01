"""
NOAA Space Weather Prediction Center (SWPC) Integration

Provides solar/space weather data directly from NOAA for HF propagation assessment.
Works independently of HamClock - can be used as primary source or fallback.

API Reference: https://services.swpc.noaa.gov/json/

Available Data:
- Solar Flux (F10.7): Radio noise at 2800 MHz correlates with ionization
- K-index: Geomagnetic disturbance (0-9 scale)
- A-index: Daily geomagnetic activity average
- Sunspot Number (SN): Correlated with solar flux
- X-ray flux: Solar flare activity
- Aurora predictions

Usage:
    from utils.space_weather import SpaceWeatherAPI

    api = SpaceWeatherAPI()
    data = api.get_current_conditions()

    print(f"Solar Flux: {data.solar_flux}")
    print(f"K-index: {data.k_index}")
    print(f"Band Conditions: {data.band_conditions}")
"""

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class BandCondition(Enum):
    """HF band propagation condition assessment."""
    EXCELLENT = "Excellent"
    GOOD = "Good"
    FAIR = "Fair"
    POOR = "Poor"
    VERY_POOR = "Very Poor"


class GeomagneticStorm(Enum):
    """Geomagnetic storm scale (NOAA G-scale)."""
    QUIET = "Quiet"  # Kp 0-3
    UNSETTLED = "Unsettled"  # Kp 4
    MINOR = "G1 Minor"  # Kp 5
    MODERATE = "G2 Moderate"  # Kp 6
    STRONG = "G3 Strong"  # Kp 7
    SEVERE = "G4 Severe"  # Kp 8
    EXTREME = "G5 Extreme"  # Kp 9


@dataclass
class SpaceWeatherData:
    """Current space weather conditions."""
    # Solar indices
    solar_flux: Optional[float] = None  # 10.7 cm flux (SFU)
    sunspot_number: Optional[int] = None
    k_index: Optional[int] = None  # 0-9
    a_index: Optional[int] = None

    # X-ray flux
    xray_flux: Optional[str] = None  # e.g., "B4.2"
    xray_class: Optional[str] = None  # B, C, M, X

    # Geomagnetic
    geomag_storm: GeomagneticStorm = GeomagneticStorm.QUIET

    # Timestamps
    updated: Optional[datetime] = None
    k_index_time: Optional[datetime] = None

    # Band assessments (derived)
    band_conditions: Dict[str, BandCondition] = field(default_factory=dict)

    # Raw data for debugging
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'solar_flux': self.solar_flux,
            'sunspot_number': self.sunspot_number,
            'k_index': self.k_index,
            'a_index': self.a_index,
            'xray_flux': self.xray_flux,
            'geomag_storm': self.geomag_storm.value,
            'updated': self.updated.isoformat() if self.updated else None,
            'band_conditions': {k: v.value for k, v in self.band_conditions.items()},
        }


class SpaceWeatherAPI:
    """
    NOAA SWPC API client for space weather data.

    Provides current conditions for HF propagation assessment.
    """

    BASE_URL = "https://services.swpc.noaa.gov"

    # Known endpoints
    ENDPOINTS = {
        'k_index_1m': '/json/planetary_k_index_1m.json',
        'k_index_3d': '/json/boulder_k_index_1m.json',
        'a_index_text': '/text/daily-geomagnetic-indices.txt',  # Text format (JSON not available)
        'solar_flux': '/json/f107_cm_flux.json',
        'sunspot': '/json/sunspot_report.json',
        'goes_xray': '/json/goes/primary/xrays-6-hour.json',
        'aurora': '/json/ovation_aurora_latest.json',
        'solar_regions': '/json/solar_regions.json',
        'alerts': '/products/alerts.json',
    }

    # HamQSL.com as backup (provides similar data)
    HAMQSL_URL = "https://www.hamqsl.com/solarxml.php"

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self._cache: Dict[str, Tuple[datetime, Any]] = {}
        self._cache_ttl = 300  # 5 minutes

    def _fetch_json(self, endpoint: str) -> Optional[Dict]:
        """Fetch JSON from NOAA SWPC."""
        url = f"{self.BASE_URL}{endpoint}"

        # Check cache
        cache_key = endpoint
        if cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if datetime.now() - cached_time < timedelta(seconds=self._cache_ttl):
                return cached_data

        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'MeshForge/1.0 (Space Weather Monitor)')
            req.add_header('Accept', 'application/json')

            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read().decode('utf-8'))

            # Cache result
            self._cache[cache_key] = (datetime.now(), data)
            return data

        except urllib.error.HTTPError as e:
            logger.warning(f"[SWPC] HTTP error fetching {endpoint}: {e.code}")
        except urllib.error.URLError as e:
            logger.warning(f"[SWPC] URL error fetching {endpoint}: {e.reason}")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[SWPC] Parse error fetching {endpoint}: {e}")

        return None

    def get_k_index(self) -> Optional[Tuple[int, datetime]]:
        """Get current planetary K-index.

        Returns:
            Tuple of (k_index, timestamp) or None
        """
        data = self._fetch_json(self.ENDPOINTS['k_index_1m'])

        if data and isinstance(data, list) and len(data) > 0:
            try:
                # Data is list of [timestamp, kp] pairs
                # Get most recent entry
                latest = data[-1]

                # Parse timestamp: "2026-01-12 10:00:00.000" (variable milliseconds)
                if isinstance(latest, list) and len(latest) >= 2:
                    ts_str = latest[0]
                    kp = float(latest[1])

                    # Strip any milliseconds (handles .000, .123, or none)
                    ts_str_clean = ts_str.split('.')[0] if '.' in ts_str else ts_str
                    ts = datetime.fromisoformat(ts_str_clean)
                    k_index = int(round(kp))

                    return (k_index, ts)
            except (ValueError, KeyError, IndexError, TypeError) as e:
                logger.debug(f"[SWPC] Error parsing K-index: {e}")

        return None

    def get_solar_flux(self) -> Optional[float]:
        """Get 10.7 cm solar flux (SFU).

        Returns:
            Solar flux in SFU or None
        """
        data = self._fetch_json(self.ENDPOINTS['solar_flux'])

        if data and isinstance(data, list) and len(data) > 0:
            try:
                # Get most recent observed flux
                for entry in reversed(data):
                    if entry.get('flux'):
                        return float(entry['flux'])
            except (ValueError, KeyError, TypeError) as e:
                logger.debug(f"[SWPC] Error parsing solar flux: {e}")

        return None

    def get_xray_flux(self) -> Optional[str]:
        """Get current X-ray flux class.

        Returns:
            X-ray class string (e.g., "B4.2", "C1.5", "M2.0")
        """
        data = self._fetch_json(self.ENDPOINTS['goes_xray'])

        if data and isinstance(data, list) and len(data) > 0:
            try:
                latest = data[-1]
                flux = latest.get('flux', latest.get('observed_flux'))
                if flux:
                    # Convert W/m² to class
                    return self._flux_to_class(float(flux))
            except (ValueError, KeyError, TypeError, IndexError) as e:
                logger.debug(f"[SWPC] Error parsing X-ray flux: {e}")

        return None

    def get_a_index(self) -> Optional[int]:
        """Get current daily A-index (planetary).

        The A-index is a daily average of geomagnetic activity,
        providing a more stable measure than the 3-hourly K-index.

        Note: NOAA provides this as a text file, not JSON.
        Format: daily-geomagnetic-indices.txt

        Returns:
            A-index value (0-400 scale) or None
        """
        return self._fetch_a_index_from_text()

    def _fetch_a_index_from_text(self) -> Optional[int]:
        """Fetch A-index from NOAA text endpoint.

        Parses daily-geomagnetic-indices.txt which has format like:
        #  Date        Ap
        2026 01 30     8
        2026 01 31     5
        """
        url = f"{self.BASE_URL}{self.ENDPOINTS['a_index_text']}"

        # Check cache
        cache_key = 'a_index_text'
        if cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if datetime.now() - cached_time < timedelta(seconds=self._cache_ttl):
                return cached_data

        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'MeshForge/1.0 (Space Weather Monitor)')

            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                text = response.read().decode('utf-8')

            # Parse text file - look for lines with date and Ap value
            # Skip comment lines starting with #
            lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith('#') and not l.startswith(':')]

            if lines:
                # Get the last data line (most recent)
                for line in reversed(lines):
                    parts = line.split()
                    # Expected: YYYY MM DD Ap [other values...]
                    # or similar format with Ap in position 3 or 4
                    if len(parts) >= 4:
                        try:
                            # Try to find Ap value (typically column 4 or later)
                            # Format varies, but Ap is usually after date fields
                            ap_value = int(parts[3])  # After YYYY MM DD
                            self._cache[cache_key] = (datetime.now(), ap_value)
                            return ap_value
                        except (ValueError, IndexError):
                            continue

        except urllib.error.HTTPError as e:
            logger.warning(f"[SWPC] HTTP error fetching A-index text: {e.code}")
        except urllib.error.URLError as e:
            logger.warning(f"[SWPC] URL error fetching A-index text: {e.reason}")
        except Exception as e:
            logger.debug(f"[SWPC] Error parsing A-index text: {e}")

        return None

    def _flux_to_class(self, flux_wm2: float) -> str:
        """Convert X-ray flux (W/m²) to class notation."""
        if flux_wm2 < 1e-7:
            letter = 'A'
            value = flux_wm2 / 1e-8
        elif flux_wm2 < 1e-6:
            letter = 'B'
            value = flux_wm2 / 1e-7
        elif flux_wm2 < 1e-5:
            letter = 'C'
            value = flux_wm2 / 1e-6
        elif flux_wm2 < 1e-4:
            letter = 'M'
            value = flux_wm2 / 1e-5
        else:
            letter = 'X'
            value = flux_wm2 / 1e-4

        return f"{letter}{value:.1f}"

    def assess_band_conditions(
        self,
        solar_flux: Optional[float],
        k_index: Optional[int],
        a_index: Optional[int] = None
    ) -> Dict[str, BandCondition]:
        """Assess HF band conditions based on indices.

        Higher solar flux = better ionization = better HF
        Higher K-index = more disturbance = worse HF
        Higher A-index = sustained disturbance = worse for low bands

        The A-index is a daily average, so it indicates sustained conditions
        that particularly affect lower HF bands (160m, 80m, 40m).

        Returns:
            Dict mapping band names to condition assessments
        """
        conditions = {}

        # Default to unknown
        default = BandCondition.FAIR

        if solar_flux is None and k_index is None:
            return {band: default for band in ['160m', '80m', '40m', '30m', '20m', '17m', '15m', '12m', '10m', '6m']}

        # Score based on solar flux (higher = better for higher bands)
        flux_score = 0
        if solar_flux:
            if solar_flux >= 150:
                flux_score = 4  # Excellent
            elif solar_flux >= 120:
                flux_score = 3  # Good
            elif solar_flux >= 90:
                flux_score = 2  # Fair
            elif solar_flux >= 70:
                flux_score = 1  # Poor
            else:
                flux_score = 0  # Very Poor

        # Score based on K-index (lower = better)
        k_score = 4  # Default excellent if unknown
        if k_index is not None:
            if k_index <= 1:
                k_score = 4
            elif k_index <= 2:
                k_score = 3
            elif k_index <= 3:
                k_score = 2
            elif k_index <= 4:
                k_score = 1
            else:
                k_score = 0  # Geomagnetic storm

        # Score based on A-index (lower = better, affects low bands)
        # A-index scale: <7 Quiet, 7-15 Unsettled, 15-30 Active, 30-50 Minor storm
        a_score = 4  # Default excellent if unknown
        if a_index is None and k_index is not None:
            # Infer A-index from K-index when not available
            # During a geomagnetic storm (high K), A-index is typically also elevated
            if k_index <= 1:
                a_score = 4  # Quiet
            elif k_index <= 3:
                a_score = 3  # Unsettled
            elif k_index <= 4:
                a_score = 2  # Active
            elif k_index <= 5:
                a_score = 1  # Minor storm
            else:
                a_score = 0  # Major storm
        if a_index is not None:
            if a_index < 7:
                a_score = 4  # Quiet
            elif a_index < 15:
                a_score = 3  # Unsettled
            elif a_index < 30:
                a_score = 2  # Active
            elif a_index < 50:
                a_score = 1  # Minor storm
            else:
                a_score = 0  # Major storm

        # Combine scores
        def score_to_condition(score: float) -> BandCondition:
            if score >= 7:
                return BandCondition.EXCELLENT
            elif score >= 5:
                return BandCondition.GOOD
            elif score >= 3:
                return BandCondition.FAIR
            elif score >= 1:
                return BandCondition.POOR
            else:
                return BandCondition.VERY_POOR

        # Higher bands benefit more from high flux
        # Lower bands are more affected by K-index and A-index
        # A-index (daily average) particularly impacts sustained low-band propagation
        conditions['160m'] = score_to_condition(k_score + a_score)  # Very sensitive to geomag
        conditions['80m'] = score_to_condition(k_score + a_score)
        conditions['40m'] = score_to_condition(flux_score * 0.5 + k_score * 0.5 + a_score)
        conditions['30m'] = score_to_condition(flux_score + k_score)
        conditions['20m'] = score_to_condition(flux_score + k_score)
        conditions['17m'] = score_to_condition(flux_score * 1.5 + k_score * 0.5)
        conditions['15m'] = score_to_condition(flux_score * 1.5 + k_score * 0.5)
        conditions['12m'] = score_to_condition(flux_score * 2)  # Needs high flux
        conditions['10m'] = score_to_condition(flux_score * 2)
        conditions['6m'] = score_to_condition(flux_score * 2)  # Sporadic E depends on other factors

        return conditions

    def k_to_storm_level(self, k_index: int) -> GeomagneticStorm:
        """Convert K-index to NOAA G-scale storm level."""
        if k_index <= 3:
            return GeomagneticStorm.QUIET
        elif k_index == 4:
            return GeomagneticStorm.UNSETTLED
        elif k_index == 5:
            return GeomagneticStorm.MINOR
        elif k_index == 6:
            return GeomagneticStorm.MODERATE
        elif k_index == 7:
            return GeomagneticStorm.STRONG
        elif k_index == 8:
            return GeomagneticStorm.SEVERE
        else:
            return GeomagneticStorm.EXTREME

    def get_current_conditions(self) -> SpaceWeatherData:
        """Get comprehensive current space weather conditions.

        Fetches all available data and returns unified structure.
        """
        data = SpaceWeatherData(updated=datetime.now())

        # Fetch K-index
        k_result = self.get_k_index()
        if k_result:
            data.k_index, data.k_index_time = k_result
            data.geomag_storm = self.k_to_storm_level(data.k_index)

        # Fetch A-index (daily geomagnetic average)
        data.a_index = self.get_a_index()

        # Fetch Solar Flux
        data.solar_flux = self.get_solar_flux()

        # Fetch X-ray flux
        data.xray_flux = self.get_xray_flux()
        if data.xray_flux:
            data.xray_class = data.xray_flux[0]  # First letter

        # Assess band conditions (now includes A-index for low-band accuracy)
        data.band_conditions = self.assess_band_conditions(
            data.solar_flux, data.k_index, data.a_index
        )

        return data

    def get_quick_summary(self) -> str:
        """Get one-line summary of current conditions.

        Returns:
            String like "SFI:125 K:2 Quiet - Good HF conditions"
        """
        data = self.get_current_conditions()

        parts = []

        if data.solar_flux:
            parts.append(f"SFI:{int(data.solar_flux)}")

        if data.k_index is not None:
            parts.append(f"K:{data.k_index}")

        if data.a_index is not None:
            parts.append(f"A:{data.a_index}")

        parts.append(data.geomag_storm.value)

        # Overall assessment
        if data.solar_flux and data.solar_flux >= 120 and data.k_index and data.k_index <= 2:
            parts.append("- Good HF")
        elif data.k_index and data.k_index >= 5:
            parts.append("- HF Disturbed")

        return " ".join(parts) if parts else "Data unavailable"


# Convenience functions
def get_space_weather() -> SpaceWeatherData:
    """Quick function to get current space weather."""
    api = SpaceWeatherAPI(timeout=10)
    return api.get_current_conditions()


def get_propagation_summary() -> str:
    """Get one-line propagation summary."""
    api = SpaceWeatherAPI(timeout=10)
    return api.get_quick_summary()


if __name__ == "__main__":
    # Test the API
    from utils.logging_config import setup_logging
    setup_logging(level=logging.DEBUG)

    print("Fetching space weather data from NOAA SWPC...")
    api = SpaceWeatherAPI()
    data = api.get_current_conditions()

    print(f"\nSpace Weather Conditions ({data.updated}):")
    print(f"  Solar Flux: {data.solar_flux} SFU")
    print(f"  K-index: {data.k_index}")
    print(f"  A-index: {data.a_index}")
    print(f"  X-ray: {data.xray_flux}")
    print(f"  Geomagnetic: {data.geomag_storm.value}")

    print("\nBand Conditions:")
    for band, condition in data.band_conditions.items():
        print(f"  {band}: {condition.value}")

    print(f"\nSummary: {api.get_quick_summary()}")
