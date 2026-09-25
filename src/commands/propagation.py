"""
MeshForge Propagation Commands — Standalone Space Weather & HF Propagation

MeshForge-owned module for space weather and HF propagation data.
Uses NOAA SWPC as the PRIMARY data source (no external dependencies).
Optionally enhances with HamClock or OpenHamClock when available.

Data Sources (priority order):
    1. NOAA SWPC (primary, always available)
    2. OpenHamClock (optional, REST API on port 3000)
    3. HamClock (optional/legacy, REST API on port 8080/8082)

Usage:
    from commands import propagation

    # Get space weather (always works - uses NOAA)
    result = propagation.get_space_weather()

    # Get band conditions (derived from NOAA data)
    result = propagation.get_band_conditions()

    # Get propagation summary
    result = propagation.get_propagation_summary()

    # Configure optional enhanced sources
    propagation.configure_source(DataSource.OPENHAMCLOCK, host="localhost", port=3000)
"""

import logging
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from .base import CommandResult
from utils.common import SettingsManager
from utils.space_weather import SpaceWeatherAPI, classify_overall_conditions
from monitoring.pskreporter_subscriber import get_pskreporter_subscriber

logger = logging.getLogger(__name__)

# Backward-compatible constants (modules are always available)
_HAS_SETTINGS = True
_HAS_SPACE_WEATHER = True
_HAS_PSKREPORTER = True

# Settings persistence for source configuration
_settings = SettingsManager("propagation", defaults={
    "sources": {
        "openhamclock": {"host": "localhost", "port": 3000, "enabled": False, "timeout": 10},
        "hamclock": {"host": "localhost", "port": 8080, "enabled": False, "timeout": 10},
        "pskreporter": {"enabled": False, "callsign": "", "bands": [], "modes": []},
    }
})


class DataSource(Enum):
    """Available propagation data sources."""
    NOAA = "noaa"                  # NOAA SWPC (primary, always available)
    OPENHAMCLOCK = "openhamclock"  # OpenHamClock (optional REST API)
    HAMCLOCK = "hamclock"          # Original HamClock (legacy, optional)
    PSKREPORTER = "pskreporter"   # PSKReporter MQTT feed (real-time spots)


@dataclass
class SourceConfig:
    """Configuration for a data source."""
    source: DataSource
    host: str = "localhost"
    port: int = 0
    enabled: bool = False
    timeout: int = 10
    # PSKReporter-specific fields
    callsign: str = ""
    bands: List[str] = field(default_factory=list)
    modes: List[str] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        if self.source == DataSource.OPENHAMCLOCK:
            return f"http://{self.host}:{self.port}"
        elif self.source == DataSource.HAMCLOCK:
            return f"http://{self.host}:{self.port}"
        return ""


# Module-level source configuration (defaults)
_sources: Dict[DataSource, SourceConfig] = {
    DataSource.NOAA: SourceConfig(
        source=DataSource.NOAA, enabled=True
    ),
    DataSource.OPENHAMCLOCK: SourceConfig(
        source=DataSource.OPENHAMCLOCK, port=3000, enabled=False
    ),
    DataSource.HAMCLOCK: SourceConfig(
        source=DataSource.HAMCLOCK, port=8080, enabled=False
    ),
    DataSource.PSKREPORTER: SourceConfig(
        source=DataSource.PSKREPORTER, enabled=False
    ),
}


def _load_sources() -> None:
    """Load source configuration from disk (if available)."""
    if not _HAS_SETTINGS or _settings is None:
        return
    saved = _settings.get("sources", {})
    for key, src_enum in [("openhamclock", DataSource.OPENHAMCLOCK),
                          ("hamclock", DataSource.HAMCLOCK)]:
        cfg = saved.get(key)
        if cfg and isinstance(cfg, dict):
            _sources[src_enum] = SourceConfig(
                source=src_enum,
                host=cfg.get("host", "localhost"),
                port=cfg.get("port", _sources[src_enum].port),
                enabled=cfg.get("enabled", False),
                timeout=cfg.get("timeout", 10),
            )
    # PSKReporter (MQTT-based, different config shape)
    pskr_cfg = saved.get("pskreporter")
    if pskr_cfg and isinstance(pskr_cfg, dict):
        _sources[DataSource.PSKREPORTER] = SourceConfig(
            source=DataSource.PSKREPORTER,
            enabled=pskr_cfg.get("enabled", False),
            callsign=pskr_cfg.get("callsign", ""),
            bands=pskr_cfg.get("bands", []),
            modes=pskr_cfg.get("modes", []),
        )


def _save_sources() -> bool:
    """Persist source configuration to disk."""
    if not _HAS_SETTINGS or _settings is None:
        return False
    data = {}
    for key, src_enum in [("openhamclock", DataSource.OPENHAMCLOCK),
                          ("hamclock", DataSource.HAMCLOCK)]:
        cfg = _sources.get(src_enum)
        if cfg:
            data[key] = {
                "host": cfg.host,
                "port": cfg.port,
                "enabled": cfg.enabled,
                "timeout": cfg.timeout,
            }
    # PSKReporter (MQTT-based, different config shape)
    pskr_cfg = _sources.get(DataSource.PSKREPORTER)
    if pskr_cfg:
        data["pskreporter"] = {
            "enabled": pskr_cfg.enabled,
            "callsign": pskr_cfg.callsign,
            "bands": pskr_cfg.bands,
            "modes": pskr_cfg.modes,
        }
    _settings.set("sources", data)
    return _settings.save()


# Load persisted config on module import
_load_sources()


def configure_source(
    source: DataSource,
    host: str = "localhost",
    port: int = 0,
    enabled: bool = True,
    timeout: int = 10,
    callsign: str = "",
    bands: Optional[List[str]] = None,
    modes: Optional[List[str]] = None,
) -> CommandResult:
    """Configure an optional data source.

    NOAA is always enabled. This configures supplementary sources.

    Args:
        source: Which data source to configure
        host: Hostname or IP (REST sources)
        port: Port number (0 = use default, REST sources)
        enabled: Whether to use this source
        timeout: Request timeout in seconds
        callsign: Callsign filter (PSKReporter only)
        bands: Band filter list (PSKReporter only)
        modes: Mode filter list (PSKReporter only)
    """
    if source == DataSource.NOAA:
        return CommandResult.ok("NOAA is always enabled as primary source")

    # PSKReporter uses MQTT, not REST
    if source == DataSource.PSKREPORTER:
        _sources[source] = SourceConfig(
            source=source,
            enabled=enabled,
            callsign=callsign.strip().upper() if callsign else "",
            bands=bands or [],
            modes=modes or [],
        )
        saved = _save_sources()
        desc = f"PSKReporter MQTT (enabled={enabled})"
        if callsign:
            desc += f", callsign={callsign.strip().upper()}"
        return CommandResult.ok(
            desc,
            data={
                'source': source.value,
                'enabled': enabled,
                'callsign': callsign,
                'bands': bands or [],
                'modes': modes or [],
                'persisted': saved,
            }
        )

    if not host:
        return CommandResult.fail("Host cannot be empty")

    default_ports = {
        DataSource.OPENHAMCLOCK: 3000,
        DataSource.HAMCLOCK: 8080,
    }
    actual_port = port or default_ports.get(source, 8080)

    if not (1 <= actual_port <= 65535):
        return CommandResult.fail(f"Invalid port: {actual_port}")

    _sources[source] = SourceConfig(
        source=source,
        host=host.strip(),
        port=actual_port,
        enabled=enabled,
        timeout=timeout,
    )

    # Persist to disk
    saved = _save_sources()

    return CommandResult.ok(
        f"{source.value} configured: {host}:{actual_port} (enabled={enabled})",
        data={
            'source': source.value,
            'host': host,
            'port': actual_port,
            'enabled': enabled,
            'persisted': saved,
        }
    )


def get_sources() -> CommandResult:
    """Get status of all configured data sources."""
    sources_info = {}
    for src, cfg in _sources.items():
        sources_info[src.value] = {
            'enabled': cfg.enabled,
            'host': cfg.host,
            'port': cfg.port,
        }

    return CommandResult.ok(
        "Data source configuration",
        data={'sources': sources_info}
    )


# ==================== Space Weather (NOAA Primary) ====================

def get_space_weather() -> CommandResult:
    """Get current space weather conditions.

    Uses NOAA SWPC as primary source. This always works without
    any external service dependencies.

    Returns:
        CommandResult with:
        - solar_flux: Solar Flux Index (SFU)
        - k_index: Kp index (0-9)
        - a_index: A index (daily average)
        - xray_flux: X-ray flux class (A/B/C/M/X)
        - geomag_storm: Geomagnetic storm level
        - band_conditions: Per-band HF condition assessment
        - source: Data source used
    """
    if not _HAS_SPACE_WEATHER:
        return CommandResult.fail(
            "Space weather module not available",
            error="utils.space_weather not found"
        )

    api = SpaceWeatherAPI(timeout=10)
    data = api.get_current_conditions()

    # No source answered → there is no weather to report. Until 2026-09-22
    # this returned ok() with every field None, geomag QUIET and every band
    # "Fair" (the assessor's defaults), and the Dashboard rendered it as a
    # calm forecast with the network dead (truth sweep; hfm #1/#2).
    if getattr(data, 'sources_answered', 0) == 0:
        return CommandResult.fail(
            "No space weather source answered — conditions UNKNOWN "
            "(NOAA SWPC unreachable, or no internet).",
            error="No space weather source answered (sources_answered=0)",
        )

    result_data = {
        'solar_flux': data.solar_flux,
        'sunspot_number': data.sunspot_number,
        'k_index': data.k_index,
        'a_index': data.a_index,
        'xray_flux': data.xray_flux,
        'xray_class': data.xray_class,
        'geomag_storm': data.geomag_storm.value,
        'band_conditions': {k: v.value for k, v in data.band_conditions.items()},
        'source': 'NOAA SWPC',
        'updated': data.updated.isoformat() if data.updated else None,
    }

    # Build summary
    parts = []
    if data.solar_flux:
        parts.append(f"SFI={int(data.solar_flux)}")
    if data.k_index is not None:
        parts.append(f"Kp={data.k_index}")
    if data.a_index is not None:
        parts.append(f"A={data.a_index}")

    summary = ", ".join(parts) if parts else "No data"

    return CommandResult.ok(
        f"Space weather: {summary} ({data.geomag_storm.value})",
        data=result_data
    )


def get_band_conditions() -> CommandResult:
    """Get HF band propagation conditions.

    Derived from NOAA SWPC data (SFI, Kp, A-index). No external
    service required.

    Returns:
        CommandResult with per-band condition assessments
    """
    if not _HAS_SPACE_WEATHER:
        return CommandResult.fail(
            "Space weather module not available",
            error="utils.space_weather not found"
        )

    api = SpaceWeatherAPI(timeout=10)
    data = api.get_current_conditions()

    # Sibling of get_space_weather's 2026-09-22 fix (non-author review):
    # with nothing answered every band reads "Fair" from the assessor's
    # defaults. That is not an assessment.
    if getattr(data, 'sources_answered', 0) == 0:
        return CommandResult.fail(
            "No space weather source answered — band conditions UNKNOWN "
            "(NOAA SWPC unreachable, or no internet).",
            error="No space weather source answered (sources_answered=0)",
        )

    # The per-band estimate needs BOTH solar flux and Kp: a missing SFI scores
    # "very poor" and a missing Kp scores "excellent" inside
    # assess_band_conditions, so a partial answer would print a confident
    # table built on a default (2026-09-25).
    missing = [n for n, v in (("solar flux", data.solar_flux), ("K-index", data.k_index))
               if v is None]
    if missing:
        return CommandResult.fail(
            f"Band conditions UNKNOWN — NOAA did not return {' and '.join(missing)}; "
            "the estimate needs both.",
            error=f"missing inputs: {', '.join(missing)}",
        )

    bands = {k: v.value for k, v in data.band_conditions.items()}

    # Determine overall condition
    overall = classify_overall_conditions(data.solar_flux, data.k_index)

    return CommandResult.ok(
        f"Band conditions: {overall} ({len(bands)} bands assessed)",
        data={
            'bands': bands,
            'overall': overall,
            'solar_flux': data.solar_flux,
            'k_index': data.k_index,
            'a_index': data.a_index,
            # NOAA publishes the indices, not per-band conditions: this table is
            # MeshForge's rule of thumb over them (was labelled "NOAA SWPC").
            'source': 'MeshForge estimate from NOAA SWPC SFI/Kp/A (rule of thumb, not a NOAA forecast)',
        }
    )


def get_alerts() -> CommandResult:
    """Get active space weather alerts from NOAA.

    Returns:
        CommandResult with list of active alerts
    """
    import urllib.request
    import json

    url = "https://services.swpc.noaa.gov/products/alerts.json"

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))

        alerts = []
        for alert in data[:10]:
            alerts.append({
                'message': alert.get('message', ''),
                'issue_datetime': alert.get('issue_datetime', ''),
            })

        return CommandResult.ok(
            f"{len(alerts)} space weather alerts",
            data={'alerts': alerts, 'count': len(alerts), 'source': 'NOAA SWPC'}
        )
    except Exception as e:
        return CommandResult.fail(f"Alerts fetch failed: {e}")


def get_propagation_summary() -> CommandResult:
    """Get a one-line propagation summary.

    Works entirely from NOAA data - no external dependencies.

    Returns:
        CommandResult with summary string and overall assessment
    """
    if not _HAS_SPACE_WEATHER:
        return CommandResult.fail("Space weather module not available")

    api = SpaceWeatherAPI(timeout=10)
    data = api.get_current_conditions()
    if getattr(data, 'sources_answered', 0) == 0:
        # Same class as get_space_weather / get_band_conditions (2026-09-22).
        return CommandResult.fail(
            "No space weather source answered — propagation UNKNOWN "
            "(NOAA SWPC unreachable, or no internet).",
            error="No space weather source answered (sources_answered=0)",
        )
    summary_str = api.get_quick_summary()

    # Overall assessment
    overall = classify_overall_conditions(data.solar_flux, data.k_index)

    return CommandResult.ok(
        summary_str,
        data={
            'summary': summary_str,
            'overall': overall,
            'solar_flux': data.solar_flux,
            'k_index': data.k_index,
            'a_index': data.a_index,
            'geomag_storm': data.geomag_storm.value,
            'source': 'NOAA SWPC',
        }
    )


# ==================== Enhanced Data (Optional Sources) ====================

def get_enhanced_data() -> CommandResult:
    """Get enhanced propagation data from optional sources.

    Tries OpenHamClock first, then HamClock, then returns NOAA-only.
    Optional sources provide:
    - VOACAP propagation predictions
    - DX cluster spots
    - Satellite tracking

    Returns:
        CommandResult with enhanced data (or NOAA-only if no sources configured)
    """
    # Start with NOAA base data
    weather_result = get_space_weather()
    base_data = weather_result.data if weather_result.success else {}

    enhanced = {
        'space_weather': base_data,
        'voacap': None,
        'dx_spots': None,
        'source': base_data.get('source', 'NOAA SWPC'),
        'enhanced_source': None,
    }

    # Try OpenHamClock
    ohc_cfg = _sources.get(DataSource.OPENHAMCLOCK)
    if ohc_cfg and ohc_cfg.enabled:
        result = _fetch_openhamclock_data(ohc_cfg)
        if result:
            enhanced.update(result)
            enhanced['enhanced_source'] = 'OpenHamClock'
            return CommandResult.ok(
                f"Enhanced data from OpenHamClock + NOAA",
                data=enhanced
            )

    # Try HamClock (legacy)
    hc_cfg = _sources.get(DataSource.HAMCLOCK)
    if hc_cfg and hc_cfg.enabled:
        result = _fetch_hamclock_enhanced(hc_cfg)
        if result:
            enhanced.update(result)
            enhanced['enhanced_source'] = 'HamClock'
            return CommandResult.ok(
                f"Enhanced data from HamClock + NOAA",
                data=enhanced
            )

    # Try PSKReporter (MQTT-based, real-time spots)
    pskr_cfg = _sources.get(DataSource.PSKREPORTER)
    if pskr_cfg and pskr_cfg.enabled:
        result = _fetch_pskreporter_data()
        if result:
            enhanced.update(result)
            if not enhanced.get('enhanced_source'):
                enhanced['enhanced_source'] = 'PSKReporter'
            else:
                enhanced['enhanced_source'] += ' + PSKReporter'
            return CommandResult.ok(
                f"Enhanced data from {enhanced['enhanced_source']} + NOAA",
                data=enhanced
            )

    return CommandResult.ok(
        "Space weather from NOAA (no enhanced sources configured)",
        data=enhanced
    )


def check_source(source: DataSource) -> CommandResult:
    """Test connectivity to an optional data source.

    Args:
        source: Which source to test

    Returns:
        CommandResult indicating connectivity status
    """
    if source == DataSource.NOAA:
        return _test_noaa()
    elif source == DataSource.OPENHAMCLOCK:
        return _test_openhamclock()
    elif source == DataSource.HAMCLOCK:
        return _test_hamclock()
    elif source == DataSource.PSKREPORTER:
        return _test_pskreporter()
    else:
        return CommandResult.fail(f"Unknown source: {source}")


# ==================== Internal: PSKReporter ====================

def _test_pskreporter() -> CommandResult:
    """Test PSKReporter MQTT connectivity."""
    cfg = _sources.get(DataSource.PSKREPORTER)
    if not cfg or not cfg.enabled:
        return CommandResult.fail(
            "PSKReporter not configured",
            data={'hint': 'Configure with: propagation.configure_source(DataSource.PSKREPORTER, enabled=True)'}
        )

    if not _HAS_PSKREPORTER:
        return CommandResult.fail(
            "PSKReporter module not available",
            data={'hint': 'pip install paho-mqtt'}
        )

    try:
        sub = get_pskreporter_subscriber()
        if sub.is_connected():
            stats = sub.get_stats()
            return CommandResult.ok(
                f"PSKReporter MQTT connected ({stats.get('spots_received', 0)} spots)",
                data={
                    'status': 'connected',
                    'source': 'PSKReporter',
                    'spots_received': stats.get('spots_received', 0),
                    'bands_active': stats.get('bands_active', 0),
                }
            )
        else:
            return CommandResult.fail(
                "PSKReporter MQTT not connected",
                data={'hint': 'Check network connectivity to mqtt.pskreporter.info'}
            )
    except Exception as e:
        return CommandResult.fail(
            f"PSKReporter check failed: {e}",
            data={'hint': 'Check paho-mqtt installation'}
        )


# ==================== Internal: NOAA ====================

def _test_noaa() -> CommandResult:
    """Test NOAA SWPC connectivity."""
    import urllib.request

    url = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return CommandResult.ok(
                    "NOAA SWPC is reachable",
                    data={'status': 'connected', 'source': 'NOAA SWPC'}
                )
    except Exception as e:
        return CommandResult.fail(
            f"NOAA SWPC unreachable: {e}",
            error=str(e),
            data={'hint': 'Check internet connectivity'}
        )
    return CommandResult.fail("NOAA SWPC returned unexpected response")


# ==================== Internal: OpenHamClock ====================

def _test_openhamclock() -> CommandResult:
    """Test OpenHamClock connectivity."""
    import urllib.request

    cfg = _sources.get(DataSource.OPENHAMCLOCK)
    if not cfg or not cfg.enabled:
        return CommandResult.fail(
            "OpenHamClock not configured",
            data={'hint': 'Configure with: propagation.configure_source(DataSource.OPENHAMCLOCK, host="...", port=3000)'}
        )

    url = f"{cfg.base_url}/api/dxcluster/spots"
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')
        with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
            if response.status == 200:
                return CommandResult.ok(
                    f"OpenHamClock connected at {cfg.host}:{cfg.port}",
                    data={'status': 'connected', 'source': 'OpenHamClock',
                          'host': cfg.host, 'port': cfg.port}
                )
    except Exception as e:
        return CommandResult.fail(
            f"Cannot reach OpenHamClock at {cfg.host}:{cfg.port}: {e}",
            error=str(e),
            data={'hint': 'Ensure OpenHamClock is running (docker compose up)'}
        )
    return CommandResult.fail("OpenHamClock returned unexpected response")


def _fetch_openhamclock_data(cfg: SourceConfig) -> Optional[Dict[str, Any]]:
    """Fetch enhanced data from OpenHamClock.

    OpenHamClock provides JSON REST API on port 3000:
    - /api/dxcluster/spots - DX cluster spots
    - Space weather proxied from NOAA

    Args:
        cfg: OpenHamClock source configuration

    Returns:
        Dict with enhanced data or None on failure
    """
    import urllib.request
    import json

    enhanced = {}

    # Fetch DX spots
    try:
        url = f"{cfg.base_url}/api/dxcluster/spots"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')
        req.add_header('Accept', 'application/json')

        with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

        if isinstance(data, list):
            enhanced['dx_spots'] = {
                'spots': data[:20],
                'count': len(data),
                'source': 'OpenHamClock',
            }
    except Exception as e:
        logger.debug(f"OpenHamClock DX spots fetch failed: {e}")

    return enhanced if enhanced else None


# ==================== Internal: PSKReporter Data ====================

def _fetch_pskreporter_data() -> Optional[Dict[str, Any]]:
    """Fetch propagation data from PSKReporter MQTT subscriber.

    Returns:
        Dict with PSKReporter propagation data or None on failure
    """
    if not _HAS_PSKREPORTER:
        logger.debug("PSKReporter module not available")
        return None

    try:
        sub = get_pskreporter_subscriber()
        if sub.is_connected():
            return sub.get_propagation_data()
    except Exception as e:
        logger.debug(f"PSKReporter data fetch failed: {e}")
    return None


# ==================== Standalone: DX Cluster (Telnet) ====================

def get_dx_spots_telnet(
    server: str = "dxc.nc7j.com",
    port: int = 7373,
    callsign: str = "N0CALL",
    max_spots: int = 20,
    timeout: int = 15,
) -> CommandResult:
    """Get DX cluster spots via direct telnet connection.

    Bypasses HamClock — connects directly to a DX Spider cluster node.
    Common public DX clusters:
        dxc.nc7j.com:7373, telnet.reversebeacon.net:7000

    Args:
        server: DX cluster hostname
        port: DX cluster port
        callsign: Login callsign (some clusters require a valid call)
        max_spots: Maximum spots to collect
        timeout: Connection timeout in seconds

    Returns:
        CommandResult with DX spots list
    """
    import socket

    spots = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((server, port))

        # Read login prompt and send callsign
        data = sock.recv(4096).decode('utf-8', errors='replace')
        sock.sendall(f"{callsign}\n".encode('utf-8'))

        # Collect spot data
        buffer = ""
        import time
        deadline = time.monotonic() + timeout
        while len(spots) < max_spots and time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096).decode('utf-8', errors='replace')
                if not chunk:
                    break
                buffer += chunk
                lines = buffer.split('\n')
                buffer = lines[-1]  # Keep incomplete line
                for line in lines[:-1]:
                    line = line.strip()
                    if line.startswith('DX de ') or 'DX de' in line[:10]:
                        spots.append(_parse_dx_spot(line))
            except socket.timeout:
                break

        sock.sendall(b"bye\n")
        sock.close()

    except socket.timeout:
        if not spots:
            return CommandResult.fail(
                f"Connection to {server}:{port} timed out",
                data={'hint': f'Check connectivity to {server}'}
            )
    except socket.gaierror:
        return CommandResult.fail(
            f"Cannot resolve {server}",
            data={'hint': 'Check DNS or use IP address'}
        )
    except ConnectionRefusedError:
        return CommandResult.fail(
            f"Connection refused by {server}:{port}",
            data={'hint': 'Server may be down or port incorrect'}
        )
    except Exception as e:
        if not spots:
            return CommandResult.fail(f"DX cluster error: {e}")

    if not spots:
        return CommandResult.fail("No DX spots received")

    return CommandResult.ok(
        f"{len(spots)} DX spots from {server}",
        data={
            'spots': spots,
            'count': len(spots),
            'server': server,
            'source': 'DX Cluster (telnet)',
        }
    )


def _parse_dx_spot(line: str) -> Dict[str, Any]:
    """Parse a DX cluster spot line.

    Format: DX de <spotter>: <freq> <dx_call> <comment> <time>Z
    """
    spot: Dict[str, Any] = {'raw': line}
    try:
        parts = line.split()
        if len(parts) >= 5:
            spot['spotter'] = parts[2].rstrip(':')
            spot['frequency'] = parts[3]
            spot['dx_call'] = parts[4]
            # Comment is everything between dx_call and time
            if len(parts) > 5:
                # Last token might be time (e.g., "1234Z")
                if parts[-1].endswith('Z') and parts[-1][:-1].isdigit():
                    spot['time'] = parts[-1]
                    spot['comment'] = ' '.join(parts[5:-1])
                else:
                    spot['comment'] = ' '.join(parts[5:])
    except (IndexError, ValueError):
        pass
    return spot


# ==================== Standalone: VOACAP Online ====================

def get_voacap_online(
    tx_lat: float = 0.0,
    tx_lon: float = 0.0,
    rx_lat: float = 0.0,
    rx_lon: float = 0.0,
    timeout: int = 15,
) -> CommandResult:
    """Get VOACAP propagation prediction from VOACAP online service.

    Uses the public VOACAP point-to-point prediction service.
    Independent of HamClock — works standalone.

    Args:
        tx_lat: Transmitter latitude
        tx_lon: Transmitter longitude
        rx_lat: Receiver latitude
        rx_lon: Receiver longitude
        timeout: Request timeout

    Returns:
        CommandResult with band reliability predictions
    """
    import urllib.request
    import json

    if tx_lat == 0 and tx_lon == 0 and rx_lat == 0 and rx_lon == 0:
        return CommandResult.fail(
            "Coordinates required for VOACAP prediction",
            data={'hint': 'Provide TX and RX lat/lon coordinates'}
        )

    # VOACAP online API (public, no auth required)
    url = (
        f"https://www.voacap.com/prediction.json"
        f"?tx_lat={tx_lat}&tx_lon={tx_lon}"
        f"&rx_lat={rx_lat}&rx_lon={rx_lon}"
        f"&mode=SSB&power=100"
    )

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

        bands = {}
        if isinstance(data, dict):
            for key, value in data.items():
                if 'm' in key.lower() or key.isdigit():
                    bands[key] = value

        return CommandResult.ok(
            f"VOACAP prediction: {len(bands)} bands",
            data={
                'bands': bands,
                'tx': {'lat': tx_lat, 'lon': tx_lon},
                'rx': {'lat': rx_lat, 'lon': rx_lon},
                'source': 'VOACAP Online',
                'raw': data,
            }
        )
    except Exception as e:
        return CommandResult.fail(
            f"VOACAP online error: {e}",
            data={'hint': 'Check internet connectivity'}
        )


# ==================== Standalone: Ionosonde Data (prop.kc2g.com) ====================

def get_ionosonde_data(timeout: int = 15) -> CommandResult:
    """Get real-time ionosonde data from prop.kc2g.com.

    Provides critical frequency (foF2) and Maximum Usable Frequency (MUF)
    data from ionosonde stations. This is real measured ionospheric data,
    not modeled predictions.

    Args:
        timeout: Request timeout

    Returns:
        CommandResult with ionosonde measurements
    """
    import urllib.request
    import json

    url = "https://prop.kc2g.com/api/stations.json"

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

        if not data:
            return CommandResult.fail("No ionosonde data available")

        stations = []
        for station in data[:30]:  # Limit to 30 stations
            entry: Dict[str, Any] = {
                'name': station.get('name', ''),
                'lat': station.get('lat'),
                'lon': station.get('lon'),
            }
            if 'fof2' in station:
                entry['fof2'] = station['fof2']
            if 'muf' in station:
                entry['muf'] = station['muf']
            if 'hmf2' in station:
                entry['hmf2'] = station['hmf2']
            stations.append(entry)

        # Calculate averages for summary
        fof2_values = [s['fof2'] for s in stations if s.get('fof2')]
        muf_values = [s['muf'] for s in stations if s.get('muf')]
        avg_fof2 = sum(fof2_values) / len(fof2_values) if fof2_values else None
        avg_muf = sum(muf_values) / len(muf_values) if muf_values else None

        summary_parts = []
        if avg_fof2:
            summary_parts.append(f"foF2={avg_fof2:.1f}MHz")
        if avg_muf:
            summary_parts.append(f"MUF={avg_muf:.1f}MHz")

        return CommandResult.ok(
            f"Ionosonde: {len(stations)} stations ({', '.join(summary_parts)})",
            data={
                'stations': stations,
                'count': len(stations),
                'avg_fof2': avg_fof2,
                'avg_muf': avg_muf,
                'source': 'prop.kc2g.com',
            }
        )
    except Exception as e:
        return CommandResult.fail(
            f"Ionosonde data error: {e}",
            data={'hint': 'Check internet connectivity'}
        )


# ==================== Standalone: CelesTrak TLE (Satellites) ====================

def get_satellite_tle(
    satellite: str = "ISS",
    timeout: int = 15,
) -> CommandResult:
    """Get satellite TLE data from CelesTrak.

    Provides Two-Line Element sets for satellite tracking.
    Independent of HamClock — uses CelesTrak public API directly.

    Args:
        satellite: Satellite name or NORAD catalog number
        timeout: Request timeout

    Returns:
        CommandResult with TLE data
    """
    import urllib.request
    import json

    # Common satellite name mappings
    name_map = {
        'ISS': '25544',
        'NOAA-19': '33591',
        'NOAA-18': '28654',
        'ISS (ZARYA)': '25544',
        'SO-50': '27607',
        'AO-91': '43017',
    }

    # Determine query: by name or NORAD ID
    norad_id = name_map.get(satellite.upper(), '')
    if norad_id:
        url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=JSON"
    elif satellite.isdigit():
        url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={satellite}&FORMAT=JSON"
    else:
        url = f"https://celestrak.org/NORAD/elements/gp.php?NAME={satellite}&FORMAT=JSON"

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

        if not data:
            return CommandResult.fail(
                f"No TLE data for '{satellite}'",
                data={'hint': 'Check satellite name or NORAD ID'}
            )

        # CelesTrak returns JSON GP format
        sat_data = data[0] if isinstance(data, list) else data
        result = {
            'name': sat_data.get('OBJECT_NAME', satellite),
            'norad_id': sat_data.get('NORAD_CAT_ID', ''),
            'epoch': sat_data.get('EPOCH', ''),
            'inclination': sat_data.get('INCLINATION', ''),
            'eccentricity': sat_data.get('ECCENTRICITY', ''),
            'period_min': sat_data.get('PERIOD', ''),
            'tle_line1': sat_data.get('TLE_LINE1', ''),
            'tle_line2': sat_data.get('TLE_LINE2', ''),
            'source': 'CelesTrak',
        }

        return CommandResult.ok(
            f"TLE: {result['name']} (NORAD {result['norad_id']})",
            data=result
        )
    except Exception as e:
        return CommandResult.fail(
            f"CelesTrak error: {e}",
            data={'hint': 'Check internet connectivity'}
        )

# ==================== Internal: HamClock (Legacy) ====================

def _test_hamclock() -> CommandResult:
    """Test HamClock connectivity."""
    import urllib.request

    cfg = _sources.get(DataSource.HAMCLOCK)
    if not cfg or not cfg.enabled:
        return CommandResult.fail(
            "HamClock not configured",
            data={'hint': 'Configure with: propagation.configure_source(DataSource.HAMCLOCK, host="...", port=8080)'}
        )

    url = f"{cfg.base_url}/get_sys.txt"
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')
        with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
            data = response.read().decode('utf-8')
            if data:
                return CommandResult.ok(
                    f"HamClock connected at {cfg.host}:{cfg.port}",
                    data={'status': 'connected', 'source': 'HamClock',
                          'host': cfg.host, 'port': cfg.port, 'info': data[:200]}
                )
    except Exception as e:
        return CommandResult.fail(
            f"Cannot reach HamClock at {cfg.host}:{cfg.port}: {e}",
            error=str(e),
            data={'hint': 'Ensure HamClock is running'}
        )
    return CommandResult.fail("HamClock returned unexpected response")


def _fetch_hamclock_enhanced(cfg: SourceConfig) -> Optional[Dict[str, Any]]:
    """Fetch enhanced data from HamClock (legacy).

    HamClock REST API returns key=value text format.

    Args:
        cfg: HamClock source configuration

    Returns:
        Dict with enhanced data or None on failure
    """
    import urllib.request

    enhanced = {}

    def fetch_endpoint(endpoint: str) -> Optional[str]:
        try:
            url = f"{cfg.base_url}/{endpoint}"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'MeshForge/1.0')
            with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
                return response.read().decode('utf-8')
        except Exception as e:
            logger.debug(f"HamClock {endpoint} fetch failed: {e}")
            return None

    def parse_kv(data: str) -> Dict[str, str]:
        result = {}
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                result[key.strip()] = value.strip()
        return result

    # VOACAP propagation predictions
    raw = fetch_endpoint('get_voacap.txt')
    if raw:
        voacap = {'path': '', 'utc': '', 'bands': {}}
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
                try:
                    if ',' in value:
                        rel, snr = value.split(',', 1)
                        reliability = int(rel.strip())
                    else:
                        reliability = int(value)
                        snr = "0"
                    voacap['bands'][key] = {
                        'reliability': reliability,
                        'snr': int(snr.strip()) if ',' in value else 0,
                    }
                except ValueError:
                    pass

        if voacap['bands']:
            enhanced['voacap'] = voacap

    # DX spots
    raw = fetch_endpoint('get_dxspots.txt')
    if raw:
        lines = [l.strip() for l in raw.strip().split('\n') if l.strip()]
        if lines:
            enhanced['dx_spots'] = {
                'spots': lines,
                'count': len(lines),
                'source': 'HamClock',
            }

    return enhanced if enhanced else None
