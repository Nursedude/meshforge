"""
HamClock Commands

Provides unified interface for HamClock API operations.
Used by both GTK and CLI interfaces.

HamClock provides:
- Space weather data (SFI, Kp, A index, X-ray flux)
- Band conditions (HF propagation status)
- VOACAP propagation predictions
- DX cluster spots
- Satellite tracking

Reference: https://www.clearskyinstitute.com/ham/HamClock/
"""

import urllib.request
import urllib.error
import json
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

from .base import CommandResult

logger = logging.getLogger(__name__)


# Module-level connection state
_host = "localhost"
_api_port = 8082  # HamClock REST API default
_live_port = 8081  # HamClock live display default
_timeout = 10  # Request timeout in seconds


@dataclass
class HamClockConfig:
    """HamClock connection configuration."""
    host: str
    api_port: int
    live_port: int


def configure(host: str, api_port: int = 8082, live_port: int = 8081) -> CommandResult:
    """
    Configure HamClock connection.

    Args:
        host: HamClock hostname or IP (e.g., 'localhost', '192.168.1.100')
        api_port: REST API port (default 8082)
        live_port: Live display port (default 8081)

    Returns:
        CommandResult indicating success
    """
    global _host, _api_port, _live_port

    if not host:
        return CommandResult.fail("Host cannot be empty")

    if not (1 <= api_port <= 65535):
        return CommandResult.fail(f"Invalid API port: {api_port}")

    if not (1 <= live_port <= 65535):
        return CommandResult.fail(f"Invalid live port: {live_port}")

    _host = host.strip()
    _api_port = api_port
    _live_port = live_port

    return CommandResult.ok(
        f"HamClock configured: {host}:{api_port}",
        data={'host': _host, 'api_port': _api_port, 'live_port': _live_port}
    )


def get_config() -> HamClockConfig:
    """Get current HamClock configuration."""
    return HamClockConfig(host=_host, api_port=_api_port, live_port=_live_port)


def _get_api_url() -> str:
    """Get the base API URL."""
    protocol = "http"
    return f"{protocol}://{_host}:{_api_port}"


def _fetch_endpoint(endpoint: str, timeout: int = None) -> CommandResult:
    """
    Fetch data from a HamClock API endpoint.

    Args:
        endpoint: API endpoint (e.g., 'get_spacewx.txt')
        timeout: Request timeout in seconds

    Returns:
        CommandResult with raw response data
    """
    url = f"{_get_api_url()}/{endpoint}"

    try:
        req = urllib.request.Request(url, method='GET')
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=timeout or _timeout) as response:
            data = response.read().decode('utf-8')
            return CommandResult.ok(
                f"Fetched {endpoint}",
                data={'raw': data, 'url': url},
                raw=data
            )

    except urllib.error.HTTPError as e:
        return CommandResult.fail(
            f"HTTP {e.code}: {e.reason}",
            error=f"HamClock returned HTTP {e.code}",
            data={'url': url, 'http_code': e.code}
        )
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if 'connection refused' in reason.lower():
            return CommandResult.fail(
                "Connection refused - is HamClock running?",
                error=reason,
                data={'url': url, 'hint': 'Start HamClock service'}
            )
        elif 'timed out' in reason.lower():
            return CommandResult.fail(
                "Connection timed out",
                error=reason,
                data={'url': url, 'hint': 'Check network connectivity'}
            )
        else:
            return CommandResult.fail(
                f"Connection error: {reason}",
                error=reason,
                data={'url': url}
            )
    except Exception as e:
        return CommandResult.fail(
            f"Request failed: {e}",
            error=str(e),
            data={'url': url}
        )


def _parse_key_value(data: str) -> Dict[str, str]:
    """Parse key=value format response."""
    result = {}
    for line in data.strip().split('\n'):
        if '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip()
    return result


# ==================== Connection Tests ====================

def test_connection() -> CommandResult:
    """
    Test connection to HamClock.

    Returns:
        CommandResult with system info if successful
    """
    result = _fetch_endpoint('get_sys.txt')
    if result.success:
        parsed = _parse_key_value(result.raw_output or "")
        return CommandResult.ok(
            f"Connected to HamClock",
            data={
                'connected': True,
                'host': _host,
                'port': _api_port,
                'system_info': parsed
            }
        )
    return result


def is_available() -> bool:
    """Check if HamClock API is reachable."""
    result = test_connection()
    return result.success


# ==================== Space Weather ====================

def get_space_weather() -> CommandResult:
    """
    Get space weather data from HamClock.

    Returns:
        CommandResult with space weather indices:
        - sfi: Solar Flux Index
        - kp: Kp Index (geomagnetic activity)
        - a: A Index
        - xray: X-Ray flux class
        - ssn: Sunspot Number
        - proton: Proton flux
        - aurora: Aurora activity
    """
    result = _fetch_endpoint('get_spacewx.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""
    parsed = _parse_key_value(raw)

    # Normalize keys to lowercase and standard names
    weather = {}

    # Map HamClock keys to standard names
    key_map = {
        'sfi': ['sfi', 'flux'],
        'kp': ['kp'],
        'a': ['a', 'a_index'],
        'xray': ['xray', 'x-ray'],
        'ssn': ['ssn', 'sunspot', 'sunspots'],
        'proton': ['proton', 'pf'],
        'aurora': ['aurora', 'aur'],
    }

    for standard_key, possible_keys in key_map.items():
        for raw_key, value in parsed.items():
            if raw_key.lower() in possible_keys or any(pk in raw_key.lower() for pk in possible_keys):
                weather[standard_key] = value
                break

    # Derive band conditions from Kp
    kp_value = weather.get('kp', '0')
    try:
        kp = float(kp_value)
        if kp < 3:
            weather['conditions'] = 'Good'
            weather['conditions_detail'] = 'Low geomagnetic activity'
        elif kp < 5:
            weather['conditions'] = 'Moderate'
            weather['conditions_detail'] = 'Moderate geomagnetic activity'
        elif kp < 7:
            weather['conditions'] = 'Disturbed'
            weather['conditions_detail'] = 'High geomagnetic activity'
        else:
            weather['conditions'] = 'Storm'
            weather['conditions_detail'] = 'Geomagnetic storm conditions'
    except ValueError:
        weather['conditions'] = 'Unknown'

    return CommandResult.ok(
        f"Space weather: SFI={weather.get('sfi', '?')}, Kp={weather.get('kp', '?')}",
        data=weather,
        raw=raw
    )


def get_band_conditions() -> CommandResult:
    """
    Get HF band conditions from HamClock.

    Returns:
        CommandResult with band conditions:
        - 80m-40m: Low frequency bands (Day/Night)
        - 30m-20m: Mid frequency bands
        - 17m-15m: High frequency bands
        - 12m-10m: VHF bands
    """
    result = _fetch_endpoint('get_bc.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""
    parsed = _parse_key_value(raw)

    # Parse band conditions into groups
    bands = {}

    for key, value in parsed.items():
        key_lower = key.lower()
        if '80' in key_lower or '40' in key_lower:
            bands['80m-40m'] = value
        elif '30' in key_lower or '20' in key_lower:
            bands['30m-20m'] = value
        elif '17' in key_lower or '15' in key_lower:
            bands['17m-15m'] = value
        elif '12' in key_lower or '10' in key_lower:
            bands['12m-10m'] = value

    return CommandResult.ok(
        f"Band conditions retrieved ({len(bands)} bands)",
        data={'bands': bands, 'raw_parsed': parsed},
        raw=raw
    )


# ==================== VOACAP Propagation ====================

def get_voacap() -> CommandResult:
    """
    Get VOACAP propagation predictions from HamClock.

    VOACAP (Voice of America Coverage Analysis Program) provides
    HF propagation predictions based on current solar conditions.

    Returns:
        CommandResult with VOACAP data:
        - path: DE to DX path description
        - utc: UTC hour for prediction
        - bands: Dict of band predictions with reliability% and SNR
    """
    result = _fetch_endpoint('get_voacap.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""

    voacap = {
        'path': '',
        'utc': '',
        'bands': {}
    }

    for line in raw.strip().split('\n'):
        if '=' not in line:
            continue

        key, value = line.split('=', 1)
        key = key.strip().lower()
        value = value.strip()

        if key == 'path':
            voacap['path'] = value
        elif key == 'utc':
            voacap['utc'] = value
        elif 'm' in key:
            # Band data (e.g., "80m=23,12" where 23=reliability%, 12=SNR dB)
            band_name = key
            try:
                if ',' in value:
                    rel, snr = value.split(',', 1)
                    voacap['bands'][band_name] = {
                        'reliability': int(rel.strip()),
                        'snr': int(snr.strip()),
                        'status': _reliability_to_status(int(rel.strip()))
                    }
                else:
                    rel = int(value)
                    voacap['bands'][band_name] = {
                        'reliability': rel,
                        'snr': 0,
                        'status': _reliability_to_status(rel)
                    }
            except ValueError:
                logger.debug(f"Could not parse VOACAP band {key}: {value}")

    # Calculate best band
    best_band = None
    best_rel = 0
    for band, data in voacap['bands'].items():
        if data['reliability'] > best_rel:
            best_rel = data['reliability']
            best_band = band

    voacap['best_band'] = best_band
    voacap['best_reliability'] = best_rel

    return CommandResult.ok(
        f"VOACAP: {len(voacap['bands'])} bands, best={best_band or 'none'} ({best_rel}%)",
        data=voacap,
        raw=raw
    )


def _reliability_to_status(reliability: int) -> str:
    """Convert reliability percentage to status string."""
    if reliability >= 80:
        return 'excellent'
    elif reliability >= 60:
        return 'good'
    elif reliability >= 40:
        return 'fair'
    elif reliability > 0:
        return 'poor'
    else:
        return 'closed'


# ==================== System Info ====================

def get_system_info() -> CommandResult:
    """
    Get HamClock system information.

    Returns:
        CommandResult with:
        - version: HamClock version
        - uptime: System uptime
        - de: Home location (DE)
        - dx: Target location (DX)
    """
    result = _fetch_endpoint('get_sys.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""
    parsed = _parse_key_value(raw)

    return CommandResult.ok(
        "HamClock system info retrieved",
        data={
            'version': parsed.get('Version', 'Unknown'),
            'uptime': parsed.get('Uptime', 'Unknown'),
            'de_call': parsed.get('DECall', ''),
            'de_grid': parsed.get('DEGrid', ''),
            'dx_call': parsed.get('DXCall', ''),
            'dx_grid': parsed.get('DXGrid', ''),
            'raw': parsed
        },
        raw=raw
    )


def get_de_location() -> CommandResult:
    """Get home (DE) location from HamClock."""
    result = _fetch_endpoint('get_de.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""
    parsed = _parse_key_value(raw)

    return CommandResult.ok(
        "DE location retrieved",
        data={
            'lat': parsed.get('lat', ''),
            'lon': parsed.get('lng', parsed.get('lon', '')),
            'grid': parsed.get('grid', ''),
            'call': parsed.get('call', ''),
            'raw': parsed
        },
        raw=raw
    )


def get_dx_location() -> CommandResult:
    """Get target (DX) location from HamClock."""
    result = _fetch_endpoint('get_dx.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""
    parsed = _parse_key_value(raw)

    return CommandResult.ok(
        "DX location retrieved",
        data={
            'lat': parsed.get('lat', ''),
            'lon': parsed.get('lng', parsed.get('lon', '')),
            'grid': parsed.get('grid', ''),
            'call': parsed.get('call', ''),
            'raw': parsed
        },
        raw=raw
    )


# ==================== DX Cluster ====================

def get_dx_spots() -> CommandResult:
    """
    Get DX cluster spots from HamClock.

    Returns:
        CommandResult with recent DX spots
    """
    result = _fetch_endpoint('get_dxspots.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""
    # DX spots format varies - return raw for now
    lines = [line.strip() for line in raw.strip().split('\n') if line.strip()]

    return CommandResult.ok(
        f"DX spots: {len(lines)} entries",
        data={'spots': lines, 'count': len(lines)},
        raw=raw
    )


# ==================== Satellites ====================

def get_satellite() -> CommandResult:
    """Get current satellite info from HamClock."""
    result = _fetch_endpoint('get_satellite.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""
    parsed = _parse_key_value(raw)

    return CommandResult.ok(
        f"Satellite: {parsed.get('Name', 'None selected')}",
        data={
            'name': parsed.get('Name', ''),
            'az': parsed.get('Az', ''),
            'el': parsed.get('El', ''),
            'range': parsed.get('Range', ''),
            'up': parsed.get('Up', ''),
            'down': parsed.get('Down', ''),
            'raw': parsed
        },
        raw=raw
    )


def get_satellite_list() -> CommandResult:
    """Get list of available satellites from HamClock."""
    result = _fetch_endpoint('get_satlist.txt')
    if not result.success:
        return result

    raw = result.raw_output or ""
    satellites = [line.strip() for line in raw.strip().split('\n') if line.strip()]

    return CommandResult.ok(
        f"Satellite list: {len(satellites)} satellites",
        data={'satellites': satellites, 'count': len(satellites)},
        raw=raw
    )


# ==================== NOAA Fallback ====================

def get_noaa_solar_data() -> CommandResult:
    """
    Get solar data from NOAA Space Weather Prediction Center.

    This is a fallback when HamClock is not available.
    Uses NOAA's public JSON API.

    Returns:
        CommandResult with solar indices
    """
    url = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json"

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=_timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

        if not data:
            return CommandResult.fail("No NOAA data available")

        # Get most recent entry
        latest = data[-1]

        # Extract solar indices
        solar = {
            'sfi': latest.get('f10.7', ''),
            'ssn': latest.get('ssn', ''),
            'date': latest.get('time-tag', ''),
            'source': 'NOAA SWPC'
        }

        # Estimate band conditions from SFI
        try:
            sfi = float(solar['sfi'])
            if sfi >= 150:
                solar['conditions'] = 'Excellent'
                solar['bands_estimate'] = {
                    '80m-40m': 'Good/Good',
                    '30m-20m': 'Excellent/Good',
                    '17m-15m': 'Excellent/Fair',
                    '12m-10m': 'Good/Poor'
                }
            elif sfi >= 120:
                solar['conditions'] = 'Good'
                solar['bands_estimate'] = {
                    '80m-40m': 'Good/Good',
                    '30m-20m': 'Good/Good',
                    '17m-15m': 'Good/Fair',
                    '12m-10m': 'Fair/Poor'
                }
            elif sfi >= 90:
                solar['conditions'] = 'Fair'
                solar['bands_estimate'] = {
                    '80m-40m': 'Good/Good',
                    '30m-20m': 'Fair/Fair',
                    '17m-15m': 'Fair/Poor',
                    '12m-10m': 'Poor/Poor'
                }
            else:
                solar['conditions'] = 'Poor'
                solar['bands_estimate'] = {
                    '80m-40m': 'Fair/Good',
                    '30m-20m': 'Poor/Fair',
                    '17m-15m': 'Poor/Poor',
                    '12m-10m': 'Poor/Poor'
                }
        except (ValueError, TypeError):
            solar['conditions'] = 'Unknown'

        return CommandResult.ok(
            f"NOAA solar data: SFI={solar['sfi']}, SSN={solar['ssn']}",
            data=solar
        )

    except urllib.error.URLError as e:
        return CommandResult.fail(
            "Could not reach NOAA API",
            error=str(e.reason)
        )
    except json.JSONDecodeError as e:
        return CommandResult.fail(
            "Invalid response from NOAA",
            error=str(e)
        )
    except Exception as e:
        return CommandResult.fail(
            f"NOAA request failed: {e}",
            error=str(e)
        )


# ==================== Comprehensive Data ====================

def get_all_data() -> CommandResult:
    """
    Get all available data from HamClock in one call.

    Returns:
        CommandResult with comprehensive data:
        - space_weather: Solar indices
        - band_conditions: HF band status
        - voacap: Propagation predictions
        - system: HamClock system info
    """
    all_data = {
        'space_weather': None,
        'band_conditions': None,
        'voacap': None,
        'system': None,
        'errors': []
    }

    # Space weather
    result = get_space_weather()
    if result.success:
        all_data['space_weather'] = result.data
    else:
        all_data['errors'].append(f"space_weather: {result.message}")

    # Band conditions
    result = get_band_conditions()
    if result.success:
        all_data['band_conditions'] = result.data
    else:
        all_data['errors'].append(f"band_conditions: {result.message}")

    # VOACAP
    result = get_voacap()
    if result.success:
        all_data['voacap'] = result.data
    else:
        all_data['errors'].append(f"voacap: {result.message}")

    # System info
    result = get_system_info()
    if result.success:
        all_data['system'] = result.data
    else:
        all_data['errors'].append(f"system: {result.message}")

    # Determine overall success
    success_count = sum(1 for k in ['space_weather', 'band_conditions', 'voacap', 'system']
                       if all_data[k] is not None)

    if success_count == 4:
        return CommandResult.ok(
            "All HamClock data retrieved",
            data=all_data
        )
    elif success_count > 0:
        return CommandResult.warn(
            f"Partial HamClock data: {success_count}/4 sections",
            data=all_data
        )
    else:
        return CommandResult.fail(
            "Could not retrieve HamClock data",
            data=all_data
        )
