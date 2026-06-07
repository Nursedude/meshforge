"""
Meshtastic HTTP API Client

Accesses the Meshtastic JSON convenience endpoints over HTTP:

- HTTP /json/nodes  → Get all mesh nodes without TCP lock
- HTTP /json/report → Device health (airtime, memory, battery, radio)

⚠️ IMPORTANT (Issue #76): the /json/* endpoints are served ONLY by the ESP32
firmware webserver (ContentHandler.cpp, gated on HAS_WIFI). meshtasticd
(Linux-native / Portduino, PiWebServer.cpp) has NEVER served them — its route
set is /api/v1/fromradio + /api/v1/toradio plus a static-file fallback that
404s everything else. An open firmware feature request tracks adding them:
https://github.com/meshtastic/firmware/issues/9164

So against a local meshtasticd this client is honestly UNAVAILABLE: the probe
detects the alive-webserver-but-404 signature, marks `json_api_absent`, and
callers fall back to the TCP interface. The client remains fully useful when
pointed at an ESP32 node's WiFi webserver.

Reference: https://meshtastic.org/docs/development/device/http-api/
"""

import json
import logging
import ssl
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# meshtasticd default HTTPS port (configurable in config.yaml Webserver.Port)
DEFAULT_HTTP_PORT = 9443

# Ports to probe during auto-detection.
# NOTE: 4403 (the protobuf TCP/PhoneAPI port) must NEVER be probed with HTTP —
# garbage bytes on the PhoneAPI socket are the Issue #17 contention class.
PROBE_PORTS = [9443, 443, 80]

# When the webserver is alive but serves no /json/* API (the meshtasticd
# signature — Issue #76), re-check rarely: the route set only changes with a
# firmware upgrade, and every wasted probe costs a request per collect cycle.
JSON_ABSENT_RECHECK_INTERVAL = 3600.0  # seconds

# Connection timeouts — canonical source: utils.timeouts
from utils.timeouts import HTTP_CONNECT as CONNECT_TIMEOUT  # noqa: E402
from utils.timeouts import HTTP_READ as READ_TIMEOUT  # noqa: E402

# Per-host circuit breaker — stops hammering meshtasticd when it's down
from gateway.circuit_breaker import create_service_registry as _create_cb_registry

_http_circuit = _create_cb_registry("meshtastic_http", failure_threshold=3, recovery_timeout=30.0)


@dataclass
class MeshtasticNode:
    """Node data from meshtasticd /json/nodes endpoint."""
    node_id: str           # e.g., "!aabbccdd"
    long_name: str = ""
    short_name: str = ""
    hw_model: str = ""
    mac_address: str = ""
    snr: float = 0.0
    last_heard: int = 0    # Unix timestamp
    via_mqtt: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[int] = None

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            'id': self.node_id,
            'long_name': self.long_name,
            'short_name': self.short_name,
            'hw_model': self.hw_model,
            'snr': self.snr,
            'last_heard': self.last_heard,
            'via_mqtt': self.via_mqtt,
        }
        if self.has_position:
            d['position'] = {
                'latitude': self.latitude,
                'longitude': self.longitude,
                'altitude': self.altitude,
            }
        return d


@dataclass
class DeviceReport:
    """Device telemetry from meshtasticd /json/report endpoint."""
    # Airtime
    channel_utilization: float = 0.0
    tx_utilization: float = 0.0
    seconds_since_boot: int = 0

    # Memory
    heap_free: int = 0
    heap_total: int = 0
    fs_free: int = 0
    fs_total: int = 0
    fs_used: int = 0

    # Power
    battery_percent: int = 0
    battery_voltage_mv: int = 0
    has_battery: bool = False
    has_usb: bool = False
    is_charging: bool = False

    # Radio
    frequency: float = 0.0
    lora_channel: int = 0

    # WiFi
    wifi_rssi: int = 0

    # Device
    reboot_counter: int = 0

    # Raw data for extensibility
    raw: Dict[str, Any] = field(default_factory=dict)


# Singleton
_http_client: Optional['MeshtasticHTTPClient'] = None
_client_lock = threading.Lock()


def get_http_client(
    host: str = 'localhost',
    port: int = DEFAULT_HTTP_PORT,
    tls: bool = True,
    auto_detect: bool = True,
) -> 'MeshtasticHTTPClient':
    """
    Get the singleton HTTP client instance.

    Args:
        host: meshtasticd host (default: localhost)
        port: meshtasticd web port (default: 9443)
        tls: Use HTTPS (default: True, meshtasticd uses self-signed cert)
        auto_detect: Try multiple ports if initial connection fails
    """
    global _http_client
    with _client_lock:
        if _http_client is None:
            _http_client = MeshtasticHTTPClient(
                host=host, port=port, tls=tls, auto_detect=auto_detect
            )
        return _http_client


def reset_http_client():
    """Reset the singleton (for testing)."""
    global _http_client
    with _client_lock:
        _http_client = None


class MeshtasticHTTPClient:
    """
    HTTP client for the Meshtastic /json/* convenience endpoints:
    - GET /json/nodes  → All known mesh nodes
    - GET /json/report → Device health telemetry

    Served only by the ESP32 firmware webserver — meshtasticd 404s them
    (Issue #76; see module docstring). Against a local meshtasticd this
    client reports `is_available=False` with `json_api_absent=True` and
    callers should use the TCP interface.
    """

    def __init__(
        self,
        host: str = 'localhost',
        port: int = DEFAULT_HTTP_PORT,
        tls: bool = True,
        auto_detect: bool = True,
    ):
        self.host = host
        self.port = port
        self.tls = tls
        self._base_url: Optional[str] = None
        self._available: Optional[bool] = None
        self._json_api_absent: bool = False  # webserver alive, /json/* 404 (Issue #76)
        self._last_check: float = 0.0
        self._check_interval: float = 60.0  # Re-check availability every 60s

        # SECURITY: meshtasticd uses self-signed certificates by default.
        # Certificate verification is disabled to support this. This is safe
        # for localhost/LAN connections but vulnerable to MITM if meshtasticd
        # is accessed over an untrusted network. For non-LAN deployments,
        # consider certificate pinning or a TLS-terminating reverse proxy.
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

        if auto_detect:
            self._auto_detect()
        else:
            scheme = "https" if tls else "http"
            self._base_url = f"{scheme}://{host}:{port}"

    def _auto_detect(self) -> None:
        """Probe known ports to find a webserver that serves the JSON API."""
        # Try configured port first, then common alternatives
        ports_to_try = [self.port] + [p for p in PROBE_PORTS if p != self.port]

        absent_hit = None  # first (url, port, scheme) that answered but 404'd
        for port in ports_to_try:
            for scheme in (["https", "http"] if self.tls else ["http", "https"]):
                url = f"{scheme}://{self.host}:{port}"
                state = self._probe_json_api(url)
                if state == 'ok':
                    self._base_url = url
                    self.port = port
                    self.tls = (scheme == "https")
                    self._available = True
                    self._json_api_absent = False
                    self._last_check = time.time()
                    logger.info(f"Meshtastic JSON API detected at {url}")
                    return
                if state == 'absent' and absent_hit is None:
                    absent_hit = (url, port, scheme)

        self._last_check = time.time()
        self._available = False

        if absent_hit:
            # A webserver answered but 404'd /json/* — the meshtasticd
            # signature (Issue #76: PiWebServer serves only /api/v1/*).
            url, port, scheme = absent_hit
            self._base_url = url
            self.port = port
            self.tls = (scheme == "https")
            self._json_api_absent = True
            logger.info(
                f"Webserver at {url} does not serve the /json/* API "
                "(meshtasticd exposes only /api/v1/*; JSON endpoints are "
                "ESP32-only — firmware FR #9164). Callers will use the TCP "
                f"interface; recheck in {int(JSON_ABSENT_RECHECK_INTERVAL)}s."
            )
            return

        # Nothing found at all
        self._json_api_absent = False
        scheme = "https" if self.tls else "http"
        self._base_url = f"{scheme}://{self.host}:{self.port}"
        logger.warning(
            f"Meshtastic HTTP API not detected on {self.host} "
            f"(tried ports {ports_to_try}). Will retry on next call."
        )

    def _probe_json_api(self, url: str) -> str:
        """Probe a URL for the Meshtastic /json/* API.

        Returns one of:
        - 'ok'     — /json/report answered 200 with a JSON object (ESP32
                     webserver, or a future meshtasticd that grew the API)
        - 'absent' — a webserver answered but 404'd the path: meshtasticd's
                     static-file fallback (Issue #76). Server alive, API gone.
        - 'down'   — no usable webserver at this URL

        ⚠️ Deliberately NO /api/v1/fromradio fallback probe: a GET on
        fromradio CONSUMES a packet from the PhoneAPI stream and starves the
        :9443 web client (Issue #17 contention class). Probing the protobuf
        endpoint to prove "the server is up" is exactly the read-in-a-probe
        pattern CLAUDE.md bans.
        """
        ctx = self._ssl_ctx if url.startswith("https") else None
        try:
            req = urllib.request.Request(
                f"{url}/json/report",
                method='GET',
                headers={'Accept': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT, context=ctx) as resp:
                if resp.status == 200:
                    data = resp.read(4096)
                    if data:
                        # Accept any JSON object (firmware returns {} when idle)
                        parsed = json.loads(data)
                        if isinstance(parsed, dict):
                            return 'ok'
            return 'down'
        except urllib.error.HTTPError as e:
            # A real HTTP error response means a webserver IS alive here.
            # 404/405 on /json/report = the API is not served (meshtasticd).
            if e.code in (404, 405):
                return 'absent'
            return 'down'
        except (json.JSONDecodeError, ValueError):
            # Responded 200 but not JSON — not the Meshtastic JSON API
            return 'down'
        except Exception:
            return 'down'

    @property
    def is_available(self) -> bool:
        """Check if the JSON API is reachable (cached, honest about absence).

        When the last probe found a webserver that doesn't serve /json/*
        (meshtasticd — Issue #76), the recheck interval stretches to
        JSON_ABSENT_RECHECK_INTERVAL so collect cycles stop paying for a
        round-trip that can only succeed after a firmware upgrade.
        """
        now = time.time()
        interval = (JSON_ABSENT_RECHECK_INTERVAL if self._json_api_absent
                    else self._check_interval)
        if self._available is None or (now - self._last_check) > interval:
            state = self._probe_json_api(self._base_url)
            self._available = (state == 'ok')
            self._json_api_absent = (state == 'absent')
            self._last_check = now
            if state == 'down':
                # Server vanished — re-scan ports
                self._auto_detect()
        return self._available or False

    @property
    def json_api_absent(self) -> bool:
        """True when a webserver answers but serves no /json/* API (Issue #76)."""
        return self._json_api_absent

    @property
    def availability_reason(self) -> str:
        """Human-readable availability state for status surfaces."""
        if self._available:
            return "ok"
        if self._json_api_absent:
            return (
                "webserver up but /json/* not served — meshtasticd exposes "
                "only /api/v1/* (JSON endpoints are ESP32-only; firmware "
                "FR #9164). Using TCP interface instead."
            )
        return "no Meshtastic webserver reachable"

    def _get_json(self, path: str, timeout: float = READ_TIMEOUT) -> Optional[Any]:
        """
        GET a JSON endpoint from meshtasticd.

        Args:
            path: URL path (e.g., '/json/nodes')
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON data, or None on error
        """
        if not self._base_url:
            return None

        # Circuit breaker — skip request if meshtasticd is known-down
        dest = self._base_url
        if not _http_circuit.can_send(dest):
            logger.debug(f"Circuit open for {dest}, skipping {path}")
            return None

        url = f"{self._base_url}{path}"
        try:
            req = urllib.request.Request(
                url,
                method='GET',
                headers={'Accept': 'application/json'},
            )
            ctx = self._ssl_ctx if self.tls else None
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                if resp.status == 200:
                    data = resp.read()
                    result = json.loads(data)
                    _http_circuit.record_success(dest)
                    return result
                else:
                    logger.warning(f"HTTP {resp.status} from {url}")
                    _http_circuit.record_failure(dest, f"HTTP {resp.status}")
                    return None
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON from {url}: {e}")
            # JSON decode errors are not connection failures — don't trip breaker
            return None
        except Exception as e:
            logger.debug(f"HTTP request failed: {url} → {e}")
            _http_circuit.record_failure(dest, str(e))
            self._available = False
            return None

    def get_nodes(self) -> List[MeshtasticNode]:
        """
        Get all mesh nodes from meshtasticd via HTTP.

        Uses GET /json/nodes — returns ALL nodes the device knows about,
        including position, SNR, hardware model, and MQTT status.

        This does NOT require the TCP connection lock.

        Returns:
            List of MeshtasticNode objects, empty list on error
        """
        data = self._get_json('/json/nodes')
        if not data:
            return []

        nodes = []
        # Response can be a dict (keyed by node ID) or a list
        items = data.values() if isinstance(data, dict) else data
        for item in items:
            try:
                node = self._parse_node(item)
                if node:
                    nodes.append(node)
            except (KeyError, TypeError, ValueError) as e:
                logger.debug(f"Skipping malformed node: {e}")
                continue

        logger.debug(f"HTTP /json/nodes returned {len(nodes)} nodes")
        return nodes

    def get_nodes_as_dicts(self) -> List[Dict[str, Any]]:
        """
        Get nodes as plain dictionaries (compatible with existing code).

        Returns:
            List of node dicts with keys: id, long_name, short_name, hw_model,
            snr, last_heard, via_mqtt, position
        """
        return [n.to_dict() for n in self.get_nodes()]

    def get_report(self) -> Optional[DeviceReport]:
        """
        Get device health report from meshtasticd.

        Uses GET /json/report — returns airtime, memory, power, radio info.

        Returns:
            DeviceReport object, or None on error
        """
        data = self._get_json('/json/report')
        if not data:
            return None

        return self._parse_report(data)

    def get_report_raw(self) -> Optional[Dict[str, Any]]:
        """
        Get raw device report as a dictionary.

        Returns:
            Raw JSON dict from /json/report, or None on error
        """
        return self._get_json('/json/report')

    def get_nodes_geojson(self) -> Dict[str, Any]:
        """
        Get nodes as GeoJSON FeatureCollection for map display.

        Returns:
            GeoJSON dict ready for Leaflet/Folium
        """
        features = []
        for node in self.get_nodes():
            if not node.has_position:
                continue

            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [node.longitude, node.latitude],
                },
                "properties": {
                    "id": node.node_id,
                    "name": node.long_name or node.short_name or node.node_id,
                    "short_name": node.short_name,
                    "hw_model": node.hw_model,
                    "snr": node.snr,
                    "last_heard": node.last_heard,
                    "via_mqtt": node.via_mqtt,
                    "altitude": node.altitude,
                    "source": "meshtasticd_http",
                },
            }
            features.append(feature)

        return {
            "type": "FeatureCollection",
            "features": features,
        }

    def restart_device(self, timeout: float = 5.0) -> bool:
        """
        Restart the meshtasticd device via HTTP.

        Uses POST /restart.

        Returns:
            True if restart command sent, False on error
        """
        if not self._base_url:
            return False

        url = f"{self._base_url}/restart"
        try:
            req = urllib.request.Request(url, method='POST', data=b'')
            ctx = self._ssl_ctx if self.tls else None
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.warning(f"Failed to restart device: {e}")
            return False

    def blink_led(self) -> bool:
        """
        Blink LED on the meshtastic device.

        Uses POST /json/blink — useful for identifying which device is which.

        Returns:
            True if blink command sent, False on error
        """
        if not self._base_url:
            return False

        url = f"{self._base_url}/json/blink"
        try:
            req = urllib.request.Request(url, method='POST', data=b'')
            ctx = self._ssl_ctx if self.tls else None
            with urllib.request.urlopen(req, timeout=5.0, context=ctx) as resp:
                return resp.status in (200, 204)
        except Exception:
            return False

    @staticmethod
    def _parse_node(data: Dict[str, Any]) -> Optional[MeshtasticNode]:
        """Parse a node entry from /json/nodes response."""
        node_id = data.get('id') or data.get('num')
        if not node_id:
            return None

        # Normalize node ID to string
        if isinstance(node_id, int):
            node_id = f"!{node_id:08x}"
        node_id = str(node_id)

        pos = data.get('position', {}) or {}
        latitude = pos.get('latitude') or pos.get('latitudeI')
        longitude = pos.get('longitude') or pos.get('longitudeI')

        # Handle integer-encoded coordinates (latitudeI is in 1e-7 degrees)
        if isinstance(latitude, int) and abs(latitude) > 1000:
            latitude = latitude / 1e7
        if isinstance(longitude, int) and abs(longitude) > 1000:
            longitude = longitude / 1e7

        # Validate coordinates
        if latitude is not None and longitude is not None:
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                latitude = None
                longitude = None
            elif latitude == 0.0 and longitude == 0.0:
                # (0,0) means no GPS fix
                latitude = None
                longitude = None

        return MeshtasticNode(
            node_id=node_id,
            long_name=data.get('long_name', '') or data.get('longName', '') or '',
            short_name=data.get('short_name', '') or data.get('shortName', '') or '',
            hw_model=str(data.get('hw_model', '') or data.get('hwModel', '') or ''),
            mac_address=data.get('mac_address', '') or data.get('macaddr', '') or '',
            snr=float(data.get('snr', 0.0) or 0.0),
            last_heard=int(data.get('last_heard', 0) or data.get('lastHeard', 0) or 0),
            via_mqtt=bool(data.get('via_mqtt', False) or data.get('viaMqtt', False)),
            latitude=latitude,
            longitude=longitude,
            altitude=pos.get('altitude') if pos else None,
        )

    @staticmethod
    def _parse_report(data: Dict[str, Any]) -> DeviceReport:
        """Parse device report from /json/report response."""
        airtime = data.get('airtime', {}) or {}
        memory = data.get('memory', {}) or {}
        power = data.get('power', {}) or {}
        wifi = data.get('wifi', {}) or {}
        device = data.get('device', {}) or {}
        radio = data.get('radio', {}) or {}

        return DeviceReport(
            channel_utilization=float(airtime.get('channel_utilization', 0.0) or 0.0),
            tx_utilization=float(airtime.get('utilization_tx', 0.0) or 0.0),
            seconds_since_boot=int(airtime.get('seconds_since_boot', 0) or 0),
            heap_free=int(memory.get('heap_free', 0) or 0),
            heap_total=int(memory.get('heap_total', 0) or 0),
            fs_free=int(memory.get('fs_free', 0) or 0),
            fs_total=int(memory.get('fs_total', 0) or 0),
            fs_used=int(memory.get('fs_used', 0) or 0),
            battery_percent=int(power.get('battery_percent', 0) or 0),
            battery_voltage_mv=int(power.get('battery_voltage_mv', 0) or 0),
            has_battery=bool(power.get('has_battery', False)),
            has_usb=bool(power.get('has_usb', False)),
            is_charging=bool(power.get('is_charging', False)),
            frequency=float(radio.get('frequency', 0.0) or 0.0),
            lora_channel=int(radio.get('lora_channel', 0) or 0),
            wifi_rssi=int(wifi.get('rssi', 0) or 0),
            reboot_counter=int(device.get('reboot_counter', 0) or 0),
            raw=data,
        )

    def __repr__(self) -> str:
        if self._available:
            status = "available"
        elif self._json_api_absent:
            status = "json_api_absent"
        else:
            status = "unavailable"
        return f"MeshtasticHTTPClient({self._base_url}, {status})"


def get_dual_radio_reports(
    primary_port: int = DEFAULT_HTTP_PORT,
    secondary_port: int = 9444,
    host: str = 'localhost',
) -> Dict[str, Optional['DeviceReport']]:
    """
    Poll both radios' health reports simultaneously for failover monitoring.

    Uses separate HTTP client instances (not the singleton) to avoid
    interfering with the primary client's state.

    Args:
        primary_port: HTTP port for primary meshtasticd (default 9443)
        secondary_port: HTTP port for secondary meshtasticd (default 9444)
        host: Hostname for both instances (default localhost)

    Returns:
        Dict with 'primary' and 'secondary' DeviceReport objects (or None if unreachable)
    """
    reports: Dict[str, Optional[DeviceReport]] = {
        'primary': None,
        'secondary': None,
    }

    for key, port in [('primary', primary_port), ('secondary', secondary_port)]:
        try:
            client = MeshtasticHTTPClient(host=host, port=port, auto_detect=False)
            reports[key] = client.get_report()
        except Exception as e:
            logger.debug("Failed to get %s radio report (port %d): %s", key, port, e)

    return reports
