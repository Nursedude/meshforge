"""
Map HTTP Handler - HTTP request handling for MeshForge Map Server.

Provides the HTTP endpoint logic for the live map and APIs.
This module is used by MapServer in map_data_service.py.

Endpoints:
- GET /              -> node_map.html (the live map)
- GET /api/nodes/geojson  -> live node GeoJSON (?bbox=s,w,n,e or ?region=key)
- GET /api/region-presets -> available region presets with bbox definitions
- GET /api/settings      -> current map settings (selected_region)
- POST /api/settings     -> save map settings (selected_region)
- GET /api/nodes/history  -> node history stats + unique nodes (24h)
- GET /api/nodes/trajectory/<id> -> trajectory GeoJSON for a node
- GET /api/nodes/snapshot -> historical network snapshot for playback
- GET /api/messages/queue -> pending OUTBOUND messages from gateway queue
- GET /api/messages/received -> RECEIVED inbound messages from mesh
- GET /api/messages/rx-status -> MessageListener status (RX enabled?)
- GET /api/network/topology -> network topology for D3.js visualization
- GET /api/status    -> server health check + history stats
- GET /*             -> static files from web/

Meshtastic API Proxy (MeshForge-owned):
- GET  /api/v1/fromradio -> multiplexed protobuf packets from meshtasticd
- PUT  /api/v1/toradio   -> forwarded to meshtasticd
- GET  /json/nodes       -> proxied + sanitized from meshtasticd
- GET  /json/report      -> proxied from meshtasticd

Meshtastic Web Client (MeshForge-owned, served from disk):
- GET  /mesh/            -> meshtastic web client (from /usr/share/meshtasticd/web/)
- GET  /mesh/api/v1/*    -> routed through MeshForge multiplexed proxy
- GET  /mesh/json/*      -> routed through MeshForge sanitized proxy

Radio Control API (MeshForge-owned):
- GET /api/radio/info     -> radio device information
- GET /api/radio/nodes    -> nodes from connected radio
- GET /api/radio/channels -> channels from connected radio
- GET /api/radio/status   -> radio connection status
- POST /api/radio/message -> send message via radio
"""

import json
import ipaddress
import logging
import mimetypes
import os
import re
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, unquote

logger = logging.getLogger(__name__)

# ── Optional dependency imports via safe_import ──────────────────────
from utils.safe_import import safe_import

_get_connection_manager, _ConnectionMode, _HAS_MESHTASTIC_CONN = safe_import(
    'utils.meshtastic_connection', 'get_connection_manager', 'ConnectionMode'
)
_SRTMProvider, _LOSAnalyzer, _HAS_TERRAIN = safe_import(
    'utils.terrain', 'SRTMProvider', 'LOSAnalyzer'
)
_MessageQueue, _HAS_MSG_QUEUE = safe_import(
    'gateway.message_queue', 'MessageQueue'
)
from commands import messaging
_get_listener_status, _HAS_MSG_LISTENER = safe_import(
    'utils.message_listener', 'get_listener_status'
)
_get_websocket_server, _is_websocket_available, _HAS_WS_SERVER = safe_import(
    'utils.websocket_server', 'get_websocket_server', 'is_websocket_available'
)

# Ensure modern web asset MIME types are recognized (Python may lack these)
mimetypes.add_type('application/javascript', '.mjs')
mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('application/wasm', '.wasm')
mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('image/avif', '.avif')


from utils._map_meshtastic_proxy import MeshtasticProxyMixin
from utils._map_radio_endpoints import RadioEndpointsMixin


# ── Query parameter helper ────────────────────────────────────────────
def _safe_query_param(query, key, default=None):
    """Safely extract a single query parameter value."""
    values = query.get(key)
    if not values:
        return default
    return values[0] if values[0] else default


# ── Region presets (ported from meshforge-maps) ───────────────────────
REGION_PRESETS = {
    "hawaii": {
        "label": "Hawaii",
        "map_center_lat": 20.5, "map_center_lon": -157.0,
        "map_default_zoom": 7,
        "bbox": [18.5, -161.0, 22.5, -154.0],
    },
    "west_coast": {
        "label": "West Coast",
        "map_center_lat": 37.5, "map_center_lon": -122.0,
        "map_default_zoom": 6,
        "bbox": [32.0, -125.0, 49.0, -114.0],
    },
    "us": {
        "label": "United States",
        "map_center_lat": 39.0, "map_center_lon": -98.0,
        "map_default_zoom": 4,
        "bbox": [
            [24.5, -125.0, 49.5, -66.0],   # CONUS
            [51.0, -180.0, 72.0, -130.0],   # Alaska
            [18.5, -161.0, 22.5, -154.0],   # Hawaii
            [17.5, -68.0, 18.6, -64.0],     # PR + USVI
        ],
    },
    "americas": {
        "label": "Americas",
        "map_center_lat": 15.0, "map_center_lon": -80.0,
        "map_default_zoom": 3,
        "bbox": [-56.0, -180.0, 72.0, -34.0],
    },
    "world": {
        "label": "World",
        "map_center_lat": 20.0, "map_center_lon": 0.0,
        "map_default_zoom": 3,
        "bbox": None,
    },
}


class MapRequestHandler(RadioEndpointsMixin, MeshtasticProxyMixin, SimpleHTTPRequestHandler):
    """HTTP handler that serves the map HTML and node GeoJSON API."""

    collector = None  # MapDataCollector instance
    web_dir: Optional[str] = None
    # CORS: None = allow all, list = allow specific origins
    allowed_origins: Optional[List[str]] = None
    # Meshtastic API proxy (deprecated — always None, kept for graceful 503 responses)
    api_proxy = None

    # Default allowed origins when none explicitly configured
    _DEFAULT_ORIGINS = ['http://localhost', 'https://localhost']

    def _send_cors_header(self):
        """Send appropriate CORS header based on configuration.

        When allowed_origins is None: restrict to localhost (secure default)
        When allowed_origins is a list: only allow those origins

        If the request origin is not permitted, no Access-Control-Allow-Origin
        header is sent at all. Previously we fell back to
        ``http://localhost:5000`` for any unknown origin, which leaked an
        allow-list entry regardless of who was asking.
        """
        origin = self.headers.get('Origin', '')
        if not origin:
            return
        origins = self.allowed_origins if self.allowed_origins is not None else self._DEFAULT_ORIGINS
        if any(origin.startswith(allowed) for allowed in origins):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')

    def do_GET(self):
        # Parse path and query once for all routes
        parsed = urlparse(self.path)
        path_only = parsed.path.rstrip('/')
        self._query = parse_qs(parsed.query)

        if path_only == '/api/nodes/geojson':
            self._serve_geojson()
        elif path_only in ('', '/index.html'):
            self._serve_map()
        elif path_only == '/api/region-presets':
            self._serve_region_presets()
        elif path_only == '/api/settings':
            self._serve_settings()
        elif path_only == '/api/status':
            self._serve_status()
        elif path_only == '/api/nodes/history':
            self._serve_history_stats()
        elif self.path.startswith('/api/nodes/trajectory/'):
            node_id = path_only.split('/api/nodes/trajectory/', 1)[1].rstrip('/')
            self._serve_trajectory(node_id)
        elif self.path.startswith('/api/coverage/'):
            # Coverage prediction for a node: /api/coverage/<lat>/<lon>/<alt>
            parts = path_only.split('/api/coverage/', 1)[1].rstrip('/').split('/')
            self._serve_coverage(parts)
        elif self.path.startswith('/api/los/'):
            # Line of sight check: /api/los/<lat1>/<lon1>/<lat2>/<lon2>
            parts = path_only.split('/api/los/', 1)[1].rstrip('/').split('/')
            self._serve_los(parts)
        elif self.path.startswith('/api/nodes/snapshot'):
            # Historical snapshot: /api/nodes/snapshot?timestamp=<unix_ts>&window=300
            self._serve_snapshot()
        elif path_only == '/api/messages/queue':
            self._serve_message_queue()
        elif self.path.startswith('/api/messages/received'):
            self._serve_received_messages()
        elif path_only == '/api/messages/rx-status':
            self._serve_rx_status()
        elif path_only == '/api/websocket/status':
            self._serve_websocket_status()
        elif path_only == '/api/network/topology':
            self._serve_network_topology()
        elif path_only == '/api/weather':
            self._serve_weather()
        # ─────────────────────────────────────────────────────────────
        # Meshtastic API Proxy - MeshForge owns the web client API
        # ─────────────────────────────────────────────────────────────
        elif self.path.startswith('/api/v1/fromradio'):
            self._proxy_fromradio()
        elif path_only in ('/json/nodes', '/json/report', '/json/blink'):
            self._proxy_json(path_only)
        elif self.path.startswith('/mesh/') or self.path == '/mesh':
            self._serve_mesh_web_client()
        elif path_only == '/api/proxy/status':
            self._serve_proxy_status()
        # ─────────────────────────────────────────────────────────────
        # Radio Control API - MeshForge-owned radio access
        # ─────────────────────────────────────────────────────────────
        elif path_only == '/api/radio/info':
            self._serve_radio_info()
        elif path_only == '/api/radio/nodes':
            self._serve_radio_nodes()
        elif path_only == '/api/radio/channels':
            self._serve_radio_channels()
        elif path_only == '/api/radio/status':
            self._serve_radio_status()
        else:
            # Serve static files from web/ directory
            if self.web_dir:
                self.directory = self.web_dir
            # For HTML files, serve with no-cache headers
            if self.path.endswith('.html'):
                self._serve_static_html()
            else:
                super().do_GET()

    def do_POST(self):
        """Handle POST requests for radio control and meshtastic API proxy."""
        path_only = urlparse(self.path).path.rstrip('/')

        # ─────────────────────────────────────────────────────────────
        # Map settings API
        # ─────────────────────────────────────────────────────────────
        if path_only == '/api/settings':
            self._handle_settings_update()
        # ─────────────────────────────────────────────────────────────
        # Meshtastic API Proxy - POST endpoints
        # ─────────────────────────────────────────────────────────────
        elif self.path.startswith('/api/v1/toradio'):
            self._proxy_toradio()
        elif self.path.startswith('/mesh/api/v1/toradio'):
            self._proxy_toradio()
        elif self.path in ('/json/blink', '/json/blink/', '/mesh/json/blink', '/mesh/json/blink/'):
            self._proxy_toradio_json('/json/blink')
        elif self.path in ('/restart', '/restart/', '/mesh/restart', '/mesh/restart/'):
            # Restrict device restart to localhost only (handles IPv4, IPv6, mapped addresses)
            try:
                client_ip = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                client_ip = None
            if client_ip is None or not client_ip.is_loopback:
                self.send_error(403, "Restart only allowed from localhost")
            else:
                self._proxy_toradio_json('/restart')
        # ─────────────────────────────────────────────────────────────
        # Radio Control API - POST endpoints
        # ─────────────────────────────────────────────────────────────
        elif path_only == '/api/radio/message':
            self._handle_send_message()
        else:
            self.send_error(404, "Not Found")

    def do_PUT(self):
        """Handle PUT requests (meshtastic web client uses PUT for toradio)."""
        if self.path.startswith('/api/v1/toradio'):
            self._proxy_toradio()
        elif self.path.startswith('/mesh/api/v1/toradio'):
            self._proxy_toradio()
        else:
            self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self._send_cors_header()
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Accept')
        self.send_header('Content-Length', '0')
        self.end_headers()

    # Max body size for radio message POST (10 KB)
    _MAX_MESSAGE_BODY = 10240
    # Valid Meshtastic destination pattern: node IDs, channel prefixes, broadcast
    _VALID_DESTINATION = re.compile(r'^[!~^]?[a-zA-Z0-9]+$')

    def _handle_send_message(self):
        """Handle POST /api/radio/message - send a message via radio.

        Uses HTTP protobuf (send_text_direct) to avoid TCP contention
        with the meshtasticd web UI — fromradio is single-consumer.
        """
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0 or content_length > self._MAX_MESSAGE_BODY:
                self._serve_json({"error": "Invalid or oversized payload"}, status=400)
                return

            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)

            text = data.get('text', '')
            destination = data.get('destination', '^all')

            if not text or len(text) > 500:
                self._serve_json({"error": "text is required (max 500 chars)"}, status=400)
                return

            if not self._VALID_DESTINATION.match(destination):
                self._serve_json({"error": "Invalid destination format"}, status=400)
                return

            # Convert string destination to int for send_text_direct
            dest_num = None  # None = broadcast (0xFFFFFFFF)
            if destination and destination != '^all':
                try:
                    if destination.startswith('!'):
                        dest_num = int(destination[1:], 16)
                    else:
                        dest_num = int(destination)
                except (ValueError, IndexError):
                    self._serve_json({"error": "Invalid destination format"}, status=400)
                    return

            # Prefer HTTP protobuf — no TCP contention with web UI
            try:
                from gateway.meshtastic_protobuf_client import send_text_direct
                success = send_text_direct(text=text, destination=dest_num)
                if success:
                    self._serve_json({
                        "success": True,
                        "message": "Sent via radio (delivery best-effort)",
                        "destination": destination,
                        "connection_mode": "http"
                    })
                    return
                else:
                    logger.debug("send_text_direct failed, trying TCP fallback")
            except ImportError:
                logger.debug("Protobuf client not available, trying TCP fallback")

            # Fallback: TCP connection manager
            conn = self._get_radio_connection()
            if not conn:
                self._serve_json({
                    "error": "Radio not available",
                    "detail": "meshtasticd not reachable via HTTP or TCP.",
                }, status=503)
                return

            success = conn.send_message(text, destination)
            if success:
                self._serve_json({
                    "success": True,
                    "message": "Sent via radio (delivery best-effort)",
                    "destination": destination,
                    "connection_mode": conn.get_mode()
                })
            else:
                self._serve_json({
                    "error": "Send failed",
                    "detail": "Verify meshtasticd is running.",
                }, status=502)

        except json.JSONDecodeError:
            self._serve_json({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.warning(f"Radio message send error: {e}")
            self._serve_json({"error": "Send failed"}, status=500)

    def _serve_static_html(self):
        """Serve static HTML files with no-cache headers."""

        path_only = unquote(urlparse(self.path).path).lstrip('/')

        if self.web_dir:
            file_path = Path(self.web_dir) / path_only
        else:
            file_path = Path(__file__).parent.parent.parent / "web" / path_only

        # Security: prevent path traversal
        try:
            base_dir = Path(self.web_dir) if self.web_dir else Path(__file__).parent.parent.parent / "web"
            file_path = file_path.resolve()
            base_dir = base_dir.resolve()
            if not str(file_path).startswith(str(base_dir)):
                self.send_error(403, "Forbidden")
                return
        except Exception:
            self.send_error(400, "Invalid path")
            return

        if file_path.exists() and file_path.is_file():
            with open(file_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404, f"File not found: {path_only}")

    def _serve_geojson(self):
        """Serve live node GeoJSON with optional bbox/region filtering."""
        if self.collector:
            geojson = self.collector.collect()
        else:
            geojson = {"type": "FeatureCollection", "features": []}

        # Resolve bbox from ?region= preset or explicit ?bbox= param
        query = getattr(self, '_query', {})
        bboxes = []

        region_key = _safe_query_param(query, "region")
        if region_key and region_key in REGION_PRESETS:
            preset_bbox = REGION_PRESETS[region_key]["bbox"]
            if preset_bbox is not None:
                if isinstance(preset_bbox[0], list):
                    bboxes = preset_bbox
                else:
                    bboxes = [preset_bbox]

        # Explicit ?bbox= overrides ?region=. Reject malformed or
        # out-of-range coordinates so a crafted query can't stall the server
        # (NaN/inf arithmetic) or bypass the region allowlist.
        bbox_str = _safe_query_param(query, "bbox")
        if bbox_str:
            # Cap how many bboxes a single request can declare.
            MAX_BBOXES = 8
            parsed_bboxes: List[List[float]] = []
            for part in bbox_str.split(";")[:MAX_BBOXES]:
                try:
                    coords = [float(x) for x in part.split(",")]
                except (ValueError, TypeError):
                    continue
                if len(coords) != 4:
                    continue
                if not all(isinstance(c, float) and c == c and c not in (float("inf"), float("-inf")) for c in coords):
                    continue
                south, west, north, east = coords
                if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
                    continue
                if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
                    continue
                if south >= north or west >= east:
                    continue
                parsed_bboxes.append(coords)
            if parsed_bboxes:
                bboxes = parsed_bboxes

        # Apply bbox filter if any bboxes resolved
        if bboxes:
            filtered = []
            for f in geojson.get("features", []):
                gc = f.get("geometry", {}).get("coordinates", [])
                if len(gc) < 2:
                    continue
                lon, lat = gc[0], gc[1]
                for south, west, north, east in bboxes:
                    if south <= lat <= north and west <= lon <= east:
                        filtered.append(f)
                        break
            geojson = dict(geojson)
            geojson["features"] = filtered
            props = dict(geojson.get("properties", {}))
            props["nodes_with_position"] = len(filtered)
            props["bbox_filtered"] = True
            geojson["properties"] = props

        self._serve_json(geojson)

    def _serve_region_presets(self):
        """Serve available region preset definitions."""
        self._serve_json(REGION_PRESETS)

    def _serve_settings(self):
        """Serve current map settings (selected region)."""
        settings = {"selected_region": None}
        if self.collector:
            settings["selected_region"] = self.collector._settings.get(
                "selected_region"
            )
        self._serve_json(settings)

    def _handle_settings_update(self):
        """Handle POST /api/settings — save map settings."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0 or content_length > 4096:
                self._serve_json({"error": "Invalid payload"}, status=400)
                return
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            self._serve_json({"error": "Invalid JSON"}, status=400)
            return

        region = data.get("selected_region")
        if region is not None and region not in REGION_PRESETS:
            self._serve_json({"error": "Unknown region"}, status=400)
            return

        if self.collector:
            self.collector._settings.set("selected_region", region)
            self.collector._settings.save()

        self._serve_json({"status": "saved", "selected_region": region})

    def _serve_map(self):
        """Serve the node_map.html file."""
        if self.web_dir:
            map_path = Path(self.web_dir) / "node_map.html"
        else:
            map_path = Path(__file__).parent.parent.parent / "web" / "node_map.html"

        if map_path.exists():
            with open(map_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404, f"Map file not found: {map_path}")

    def _serve_status(self):
        """Serve server status including radio connection info."""
        status = {
            "status": "running",
            "time": datetime.now().isoformat(),
            "collector": self.collector is not None,
        }

        # Include history stats if available
        if self.collector and self.collector._history:
            try:
                status["history"] = self.collector._history.get_stats()
            except Exception:
                status["history"] = None

        # Per-source collection diagnostics from the most recent collect() call.
        # Operators use this to answer "why is source X empty" without a code reader.
        if self.collector:
            try:
                status["source_diagnostics"] = self.collector.get_source_diagnostics()
            except Exception as e:
                logger.debug(f"Failed to fetch source diagnostics: {e}")

            # Per-network breakdown of position-less nodes (MeshCore lives here).
            try:
                no_pos = self.collector.get_nodes_without_position()
                by_network: Dict[str, int] = {}
                for entry in no_pos:
                    net = entry.get("network", "unknown")
                    by_network[net] = by_network.get(net, 0) + 1
                status["nodes_without_position"] = {
                    "total": len(no_pos),
                    "by_network": by_network,
                }
            except Exception as e:
                logger.debug(f"Failed to summarize nodes_without_position: {e}")

        # Include radio connection status + LOCAL radio config
        # (helps operators diff heterogeneous fleet boxes — e.g. LongFast vs SHORT_TURBO)
        status["radio"] = self._get_radio_status_summary()
        status["radio_config"] = self._get_local_radio_config()

        data = json.dumps(status).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self._send_cors_header()
        self.end_headers()
        self.wfile.write(data)

    def _get_radio_status_summary(self) -> Dict[str, Any]:
        """Get a summary of radio connection status for the status endpoint."""
        if not _HAS_MESHTASTIC_CONN:
            return {"available": False, "error": "meshtastic library not installed"}

        # Check TCP port (meshtasticd)
        tcp_available = False
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                tcp_available = sock.connect_ex(('localhost', 4403)) == 0
        except Exception as e:
            logger.debug(f"TCP port check failed: {e}")

        # Check USB serial device
        import glob
        usb_devices = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
        usb_available = len(usb_devices) > 0

        # Determine connection mode
        if tcp_available:
            mode = "tcp"
            connected = True
        elif usb_available:
            mode = "serial"
            connected = True
        else:
            mode = "none"
            connected = False

        return {
            "connected": connected,
            "mode": mode,
            "tcp_available": tcp_available,
            "usb_available": usb_available,
            "usb_devices": usb_devices if usb_available else [],
        }

    def _get_local_radio_config(self) -> Dict[str, Any]:
        """Read the LOCAL Meshtastic HAT's LoRa config via meshtasticd HTTP /json/report.

        Surfaced in /api/status so operators can diff two fleet boxes on incompatible
        presets (e.g. fleet-host-3 on SHORT_TURBO vs fleet-host on LongFast) without SSHing to
        each to query meshtasticd — they legitimately can't hear each other over RF.

        Returns a dict with frequency_hz / lora_channel / region / modem_preset
        (whatever meshtasticd exposes) plus an 'available' bool.
        """
        try:
            from utils.meshtastic_http import get_http_client
            client = get_http_client()
            if not client.is_available:
                return {"available": False, "reason": "meshtasticd HTTP not reachable"}
            raw = client.get_report_raw()
            if not raw:
                return {"available": False, "reason": "no /json/report response"}
            radio = raw.get("radio", {}) or {}
            config = raw.get("config", {}) or {}
            lora = config.get("lora", {}) or {}
            return {
                "available": True,
                "frequency_hz": radio.get("frequency"),
                "lora_channel": radio.get("lora_channel"),
                "region": radio.get("region") or lora.get("region"),
                "modem_preset": radio.get("modem_preset") or lora.get("modem_preset"),
                "channel_num": lora.get("channel_num"),
                "hw_model": (raw.get("device", {}) or {}).get("hw_model"),
            }
        except Exception as e:
            logger.debug(f"Local radio config lookup failed: {e}")
            return {"available": False, "reason": str(e)[:120]}

    def _serve_history_stats(self):
        """Serve node history summary and unique nodes list."""
        if not self.collector or not self.collector._history:
            self._serve_json({"error": "history not available", "nodes": []})
            return

        history = self.collector._history
        result = {
            "stats": history.get_stats(),
            "nodes": history.get_unique_nodes(hours=24),
        }
        self._serve_json(result)

    def _serve_trajectory(self, node_id: str):
        """Serve trajectory GeoJSON for a specific node."""
        if not self.collector or not self.collector._history:
            self._serve_json({"error": "history not available"})
            return

        # URL decode the node_id (! becomes %21 in URLs)

        node_id = unquote(node_id)

        history = self.collector._history
        geojson = history.get_trajectory_geojson(node_id, hours=24)
        self._serve_json(geojson)

    def _serve_json(self, obj: Any, status: int = 200):
        """Helper to serve a JSON response."""
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self._send_cors_header()
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _serve_coverage(self, parts: List[str]):
        """Serve terrain-aware coverage prediction for a location.

        URL: /api/coverage/<lat>/<lon>/<antenna_height_m>
        Optional query params: radius_km (default 10), freq_mhz (default 906)
        """
        try:
            if len(parts) < 3:
                self._serve_json({"error": "Usage: /api/coverage/<lat>/<lon>/<height_m>"})
                return

            lat = float(parts[0])
            lon = float(parts[1])
            alt = float(parts[2])

            # Parse query params

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            radius_km = float(params.get('radius_km', ['10'])[0])
            freq_mhz = float(params.get('freq_mhz', ['906'])[0])
            resolution = int(params.get('resolution', ['24'])[0])

            # Limit resolution for performance
            resolution = min(resolution, 48)
            radius_km = min(radius_km, 50)

            # Get coverage prediction from terrain analyzer
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"})
                return
            try:
                provider = _SRTMProvider()
                analyzer = _LOSAnalyzer(provider)
                coverage = analyzer.coverage_grid(
                    lat, lon, alt,
                    radius_km=radius_km,
                    freq_mhz=freq_mhz,
                    resolution=resolution
                )
            except Exception as e:
                logger.error(f"Coverage calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"})
                return

            # Convert to GeoJSON for map display
            features = []
            for point in coverage:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [point["lon"], point["lat"]]
                    },
                    "properties": {
                        "is_clear": point["is_clear"],
                        "total_loss_db": point["total_loss_db"],
                        "terrain_loss_db": point["terrain_loss_db"],
                        "fresnel_pct": point["fresnel_clearance_pct"],
                        "distance_m": point["distance_m"],
                        "bearing": point["bearing"],
                    }
                })

            result = {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "center": [lon, lat],
                    "antenna_height_m": alt,
                    "radius_km": radius_km,
                    "freq_mhz": freq_mhz,
                }
            }
            self._serve_json(result)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"Coverage endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_snapshot(self):
        """Serve a historical network snapshot for playback.

        URL: /api/nodes/snapshot?timestamp=<unix_ts>&window=300
        """
        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            timestamp = float(params.get('timestamp', [str(time.time())])[0])
            window = int(params.get('window', ['300'])[0])

            if not self.collector or not self.collector._history:
                self._serve_json({"error": "history not available", "features": []})
                return

            history = self.collector._history
            observations = history.get_snapshot(timestamp=timestamp, window_seconds=window)

            # Convert observations to GeoJSON features
            features = []
            for obs in observations:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [obs.longitude, obs.latitude]
                    },
                    "properties": {
                        "id": obs.node_id,
                        "name": obs.name,
                        "network": obs.network,
                        "is_online": obs.is_online,
                        "snr": obs.snr,
                        "battery": obs.battery,
                        "hardware": obs.hardware,
                        "role": obs.role,
                        "via_mqtt": obs.via_mqtt,
                        "timestamp": obs.timestamp,
                    }
                })

            result = {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "snapshot_time": timestamp,
                    "window_seconds": window,
                    "node_count": len(features),
                }
            }
            self._serve_json(result)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"Snapshot endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_los(self, parts: List[str]):
        """Serve line-of-sight analysis between two points.

        URL: /api/los/<lat1>/<lon1>/<lat2>/<lon2>
        Optional query params: alt1, alt2 (antenna heights, default 10m), freq_mhz (default 906)
        """
        try:
            if len(parts) < 4:
                self._serve_json({"error": "Usage: /api/los/<lat1>/<lon1>/<lat2>/<lon2>"})
                return

            lat1 = float(parts[0])
            lon1 = float(parts[1])
            lat2 = float(parts[2])
            lon2 = float(parts[3])

            # Parse query params

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            alt1 = float(params.get('alt1', ['10'])[0])
            alt2 = float(params.get('alt2', ['10'])[0])
            freq_mhz = float(params.get('freq_mhz', ['906'])[0])

            # Calculate LOS
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"})
                return
            try:
                provider = _SRTMProvider()
                analyzer = _LOSAnalyzer(provider)
                result = analyzer.analyze(lat1, lon1, alt1, lat2, lon2, alt2, freq_mhz)
            except Exception as e:
                logger.error(f"LOS calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"})
                return

            # Build elevation profile for visualization
            profile = []
            if hasattr(result, 'profile') and result.profile:
                for p in result.profile:
                    profile.append({
                        "distance_m": p.distance_m,
                        "elevation_m": p.ground_elevation,
                        "los_height_m": p.los_height,
                        "fresnel_top": p.los_height + p.fresnel_radius,
                        "fresnel_bottom": p.los_height - p.fresnel_radius,
                    })

            response = {
                "is_clear": result.is_clear,
                "distance_m": result.distance_m,
                "total_loss_db": result.total_loss_db,
                "terrain_loss_db": result.terrain_loss_db,
                "fresnel_clearance_pct": result.fresnel_clearance_pct,
                "obstruction_count": len(result.obstructions) if hasattr(result, 'obstructions') else 0,
                "profile": profile,
                "endpoints": {
                    "from": {"lat": lat1, "lon": lon1, "alt": alt1},
                    "to": {"lat": lat2, "lon": lon2, "alt": alt2},
                }
            }
            self._serve_json(response)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"LOS endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_message_queue(self):
        """Serve pending messages from the gateway message queue."""
        messages = []

        # Try to load from SQLite message queue
        if not _HAS_MSG_QUEUE:
            logger.debug("MessageQueue not available")
        else:
            try:
                queue = _MessageQueue()
                pending = queue.get_pending_messages(limit=50)
                for msg in pending:
                    messages.append({
                        "id": msg.get("id"),
                        "source": msg.get("source_id"),
                        "source_name": msg.get("source_name", ""),
                        "target": msg.get("target_id"),
                        "target_name": msg.get("target_name", ""),
                        "network": msg.get("target_network", "meshtastic"),
                        "status": msg.get("status", "pending"),
                        "created_at": msg.get("created_at", ""),
                        "message_type": msg.get("message_type", "text")
                    })
            except Exception as e:
                logger.debug(f"Message queue error: {e}")

        # Also check for cached queue file
        if not messages:
            try:
                queue_cache = self.collector._cache_dir / "message_queue.json" if self.collector else None
                if queue_cache and queue_cache.exists():
                    with open(queue_cache) as f:
                        data = json.load(f)
                    messages = data.get("messages", [])
            except Exception as e:
                logger.debug(f"Queue cache read failed: {e}")

        self._serve_json({
            "messages": messages,
            "count": len(messages),
            "timestamp": datetime.now().isoformat()
        })

    def _serve_received_messages(self):
        """Serve received (inbound) messages from the messages database.

        Query params:
            limit: Max messages to return (default 50)
            network: Filter by network (all, meshtastic, rns)
            since: Only messages after this ISO timestamp

        This endpoint returns messages RECEIVED from the mesh, stored by
        the MessageListener. Use /api/messages/queue for pending OUTBOUND messages.
        """
        # Parse query parameters
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        limit = int(params.get('limit', ['50'])[0])
        network = params.get('network', ['all'])[0]
        since = params.get('since', [None])[0]

        messages = []

        try:
            result = messaging.get_messages(limit=limit, network=network)

            if result.success and result.data:
                all_messages = result.data.get('messages', [])

                # Filter by timestamp if 'since' is provided
                if since:
                    try:
                        since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
                        all_messages = [
                            m for m in all_messages
                            if m.get('timestamp') and
                            datetime.fromisoformat(m['timestamp']) > since_dt
                        ]
                    except (ValueError, TypeError):
                        pass  # Invalid timestamp, skip filtering

                # Filter to show only received messages (from_id != 'local')
                messages = [m for m in all_messages if m.get('from_id') != 'local']

        except Exception as e:
            logger.debug(f"Error getting received messages: {e}")

        self._serve_json({
            "messages": messages,
            "count": len(messages),
            "timestamp": datetime.now().isoformat(),
            "endpoint": "received"  # Distinguish from /queue
        })

    def _serve_rx_status(self):
        """Serve the RX (message listener) status.

        Returns whether the MessageListener is running and stats
        about received messages.
        """
        status = {
            "state": "disconnected",
            "messages_received": 0,
            "last_message_time": None,
            "error": None,
        }

        if not _HAS_MSG_LISTENER:
            status["error"] = "MessageListener not available"
        else:
            try:
                status = _get_listener_status()
            except Exception as e:
                status["error"] = str(e)

        self._serve_json(status)

    def _serve_websocket_status(self):
        """Serve WebSocket server status and connection info.

        Returns WebSocket URL and stats for clients to connect.
        """
        status = {
            "available": False,
            "url": None,
            "port": 5001,
            "connected_clients": 0,
            "messages_broadcast": 0,
        }

        if not _HAS_WS_SERVER:
            status["error"] = "WebSocket server not available"
        else:
            try:
                if not _is_websocket_available():
                    status["error"] = "websockets library not installed"
                    self._serve_json(status)
                    return

                ws_server = _get_websocket_server()
                if ws_server._running:
                    stats = ws_server.stats
                    status["available"] = True
                    status["port"] = ws_server.port
                    # Build WebSocket URL based on request host
                    host = self.headers.get('Host', 'localhost:5000')
                    hostname = host.split(':')[0]
                    status["url"] = f"ws://{hostname}:{ws_server.port}/"
                    status["connected_clients"] = stats.connected_clients
                    status["messages_broadcast"] = stats.messages_broadcast
                    status["total_connections"] = stats.total_connections
                    if stats.started_at:
                        status["started_at"] = stats.started_at.isoformat()

            except Exception as e:
                status["error"] = str(e)

        self._serve_json(status)

    def _serve_network_topology(self):
        """Serve network topology data for D3.js visualization."""
        if not self.collector:
            self._serve_json({"error": "collector not available", "nodes": [], "links": []})
            return

        geojson = self.collector.collect()
        nodes = []
        links = []
        node_map = {}
        aredn_links_added = set()  # Track AREDN links to avoid duplicates

        # Build nodes
        for feature in geojson.get("features", []):
            props = feature["properties"]
            coords = feature["geometry"]["coordinates"]
            node_id = props.get("id", f"{coords[0]}_{coords[1]}")

            network = "gateway" if props.get("is_gateway") else props.get("network", "meshtastic")

            node = {
                "id": node_id,
                "name": props.get("name", node_id),
                "network": network,
                "is_online": props.get("is_online", False),
                "is_gateway": props.get("is_gateway", False),
                "is_router": props.get("role") in ("ROUTER", "ROUTER_CLIENT", "REPEATER", "AREDN"),
                "lat": coords[1],
                "lon": coords[0],
                "snr": props.get("snr"),
                "battery": props.get("battery"),
                # AREDN-specific properties
                "link_type": props.get("link_type"),  # RF, DTD, TUN
                "link_quality": props.get("link_quality"),
            }
            nodes.append(node)
            node_map[node_id] = node

        # Build AREDN links from actual link data
        # AREDN neighbors have link_type property indicating real RF/DTD/TUN links
        aredn_nodes = [n for n in nodes if n["network"] == "aredn"]
        if aredn_nodes:
            # Find the local AREDN node (the one without link_type, it's the source)
            local_aredn = [n for n in aredn_nodes if not n.get("link_type")]
            neighbor_aredn = [n for n in aredn_nodes if n.get("link_type")]

            for local in local_aredn:
                for neighbor in neighbor_aredn:
                    # Create link from local to neighbor
                    link_key = tuple(sorted([local["id"], neighbor["id"]]))
                    if link_key not in aredn_links_added:
                        dist = self._haversine(local["lat"], local["lon"],
                                               neighbor["lat"], neighbor["lon"])
                        link_type_str = neighbor.get("link_type", "RF")
                        links.append({
                            "source": local["id"],
                            "target": neighbor["id"],
                            "type": f"aredn_{link_type_str.lower()}",  # aredn_rf, aredn_dtd, aredn_tun
                            "link_quality": neighbor.get("link_quality", 0),
                            "snr": neighbor.get("snr"),
                            "distance_km": round(dist, 2)
                        })
                        aredn_links_added.add(link_key)

        # Build links based on proximity and network relationships for non-AREDN nodes
        gateways = [n for n in nodes if (n["is_gateway"] or n["is_router"]) and n["network"] != "aredn"]
        regular_nodes = [n for n in nodes if not n["is_gateway"] and not n["is_router"] and n["network"] != "aredn"]

        # Connect regular nodes to nearest gateway/router
        for node in regular_nodes:
            if not node["is_online"]:
                continue

            nearest = None
            min_dist = float("inf")

            for gw in gateways:
                if not gw["is_online"]:
                    continue
                dist = self._haversine(node["lat"], node["lon"], gw["lat"], gw["lon"])
                if dist < min_dist and dist < 50:  # 50km max
                    min_dist = dist
                    nearest = gw

            if nearest:
                link_type = "gateway" if node["network"] != nearest["network"] else node["network"]
                links.append({
                    "source": node["id"],
                    "target": nearest["id"],
                    "type": link_type,
                    "distance_km": round(min_dist, 2)
                })

        # Connect gateways to each other
        for i, gw1 in enumerate(gateways):
            for gw2 in gateways[i+1:]:
                if not gw1["is_online"] or not gw2["is_online"]:
                    continue
                dist = self._haversine(gw1["lat"], gw1["lon"], gw2["lat"], gw2["lon"])
                if dist < 100:  # 100km for gateway-gateway
                    links.append({
                        "source": gw1["id"],
                        "target": gw2["id"],
                        "type": "gateway",
                        "distance_km": round(dist, 2)
                    })

        self._serve_json({
            "nodes": nodes,
            "links": links,
            "network_counts": {
                "meshtastic": len([n for n in nodes if n["network"] == "meshtastic"]),
                "rns": len([n for n in nodes if n["network"] == "rns"]),
                "aredn": len([n for n in nodes if n["network"] == "aredn"]),
                "gateway": len([n for n in nodes if n["is_gateway"]])
            },
            "timestamp": datetime.now().isoformat()
        })

    # ─────────────────────────────────────────────────────────────────
    # Space Weather API
    # ─────────────────────────────────────────────────────────────────

    # Cache space weather data (refreshes every 15 minutes)
    _weather_cache: Optional[Dict] = None
    _weather_cache_time: float = 0
    _WEATHER_CACHE_TTL = 900  # 15 minutes

    def _serve_weather(self):
        """Serve space weather and HF band conditions for map overlay.

        Returns NOAA SWPC data: SFI, Kp, A-index, X-ray class,
        geomagnetic storm level, and per-band HF conditions.

        Cached for 15 minutes (space weather changes slowly).
        """
        now = time.time()

        # Return cached data if still fresh
        if (MapRequestHandler._weather_cache
                and (now - MapRequestHandler._weather_cache_time) < self._WEATHER_CACHE_TTL):
            self._serve_json(MapRequestHandler._weather_cache)
            return

        try:
            from commands.propagation import get_space_weather, get_band_conditions

            weather_result = get_space_weather()
            band_result = get_band_conditions()

            if weather_result.success:
                data = weather_result.data or {}
                # Merge band conditions if available
                if band_result.success and band_result.data:
                    data["band_conditions"] = band_result.data.get(
                        "bands", data.get("band_conditions", {})
                    )
                    data["overall_condition"] = band_result.data.get("overall", "Unknown")

                # Add mesh-relevant assessment
                kp = data.get("k_index")
                sfi = data.get("solar_flux")
                if kp is not None and kp >= 5:
                    data["mesh_impact"] = "degraded"
                    data["mesh_impact_note"] = (
                        f"Kp={kp} — Geomagnetic storm may cause "
                        "increased noise on LoRa frequencies"
                    )
                elif sfi and sfi >= 200:
                    data["mesh_impact"] = "elevated"
                    data["mesh_impact_note"] = (
                        f"SFI={int(sfi)} — High solar activity, "
                        "monitor for interference"
                    )
                else:
                    data["mesh_impact"] = "nominal"
                    data["mesh_impact_note"] = "Conditions favorable for mesh operations"

                data["cached_at"] = now

                # Cache the result
                MapRequestHandler._weather_cache = data
                MapRequestHandler._weather_cache_time = now

                self._serve_json(data)
            else:
                self._serve_json({
                    "error": weather_result.error or "Space weather data unavailable",
                    "mesh_impact": "unknown",
                    "cached_at": now,
                })
        except Exception as e:
            logger.warning(f"Space weather fetch failed: {e}")
            self._serve_json({
                "error": str(e),
                "mesh_impact": "unknown",
                "cached_at": now,
            })

    # Meshtastic API proxy methods (_get_client_id, _proxy_fromradio,
    # _proxy_toradio, _proxy_json, _proxy_toradio_json) are inherited
    # from MeshtasticProxyMixin in _map_meshtastic_proxy.py

    # Mesh web client and radio API endpoints provided by RadioEndpointsMixin:
    # _serve_mesh_web_client, _rewrite_mesh_html, _serve_mesh_client_unavailable,
    # _serve_proxy_status, _get_radio_connection, _serve_radio_info,
    # _serve_radio_nodes, _serve_radio_channels, _serve_radio_status, _haversine

    def log_message(self, format, *args):
        """Route request logging through Python logger instead of stderr.

        The HTTP server runs in a background thread. Writing to
        stdout/stderr corrupts the whiptail/dialog TUI display,
        but errors still need to be visible in log files for debugging.
        """
        # Route through Python logger (goes to log file, not TUI)
        message = format % args if args else format
        if '40' in str(args) or '50' in str(args):
            # 4xx/5xx responses logged as warnings for debugging
            logger.warning("MapHTTP: %s", message)
        else:
            logger.debug("MapHTTP: %s", message)
