"""
Map HTTP Handler - HTTP request handling for MeshForge Map Server.

Provides the HTTP endpoint logic for the live map and APIs.
This module is used by MapServer in map_data_service.py.

Endpoints:
- GET /              -> node_map.html (the live map)
- GET /api/nodes/geojson  -> live node GeoJSON from all sources
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
import math
import mimetypes
import os
import re
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# Default path where meshtasticd installs its web client files.
# MeshForge serves these directly from disk instead of proxying HTML.
MESHTASTICD_WEB_DIR = '/usr/share/meshtasticd/web'

# Ensure modern web asset MIME types are recognized (Python may lack these)
mimetypes.add_type('application/javascript', '.mjs')
mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('application/wasm', '.wasm')
mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('image/avif', '.avif')


class MapRequestHandler(SimpleHTTPRequestHandler):
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
        """
        origin = self.headers.get('Origin', '')
        origins = self.allowed_origins if self.allowed_origins is not None else self._DEFAULT_ORIGINS

        if origin and any(origin.startswith(allowed) for allowed in origins):
            self.send_header('Access-Control-Allow-Origin', origin)
        else:
            # Default fallback for localhost
            self.send_header('Access-Control-Allow-Origin', 'http://localhost:5000')

    def do_GET(self):
        if self.path == '/api/nodes/geojson' or self.path == '/api/nodes/geojson/':
            self._serve_geojson()
        elif self.path == '/' or self.path == '/index.html':
            self._serve_map()
        elif self.path == '/api/status':
            self._serve_status()
        elif self.path == '/api/nodes/history':
            self._serve_history_stats()
        elif self.path.startswith('/api/nodes/trajectory/'):
            node_id = self.path.split('/api/nodes/trajectory/', 1)[1].rstrip('/')
            self._serve_trajectory(node_id)
        elif self.path.startswith('/api/coverage/'):
            # Coverage prediction for a node: /api/coverage/<lat>/<lon>/<alt>
            from urllib.parse import urlparse
            path_only = urlparse(self.path).path
            parts = path_only.split('/api/coverage/', 1)[1].rstrip('/').split('/')
            self._serve_coverage(parts)
        elif self.path.startswith('/api/los/'):
            # Line of sight check: /api/los/<lat1>/<lon1>/<lat2>/<lon2>
            from urllib.parse import urlparse
            path_only = urlparse(self.path).path
            parts = path_only.split('/api/los/', 1)[1].rstrip('/').split('/')
            self._serve_los(parts)
        elif self.path.startswith('/api/nodes/snapshot'):
            # Historical snapshot: /api/nodes/snapshot?timestamp=<unix_ts>&window=300
            self._serve_snapshot()
        elif self.path == '/api/messages/queue' or self.path == '/api/messages/queue/':
            self._serve_message_queue()
        elif self.path.startswith('/api/messages/received'):
            self._serve_received_messages()
        elif self.path == '/api/messages/rx-status' or self.path == '/api/messages/rx-status/':
            self._serve_rx_status()
        elif self.path == '/api/websocket/status' or self.path == '/api/websocket/status/':
            self._serve_websocket_status()
        elif self.path == '/api/network/topology' or self.path == '/api/network/topology/':
            self._serve_network_topology()
        # ─────────────────────────────────────────────────────────────
        # Meshtastic API Proxy - MeshForge owns the web client API
        # ─────────────────────────────────────────────────────────────
        elif self.path.startswith('/api/v1/fromradio'):
            self._proxy_fromradio()
        elif self.path == '/json/nodes' or self.path == '/json/nodes/':
            self._proxy_json('/json/nodes')
        elif self.path == '/json/report' or self.path == '/json/report/':
            self._proxy_json('/json/report')
        elif self.path == '/json/blink' or self.path == '/json/blink/':
            self._proxy_json('/json/blink')
        elif self.path.startswith('/mesh/') or self.path == '/mesh':
            self._serve_mesh_web_client()
        elif self.path == '/api/proxy/status' or self.path == '/api/proxy/status/':
            self._serve_proxy_status()
        # ─────────────────────────────────────────────────────────────
        # Radio Control API - MeshForge-owned radio access
        # ─────────────────────────────────────────────────────────────
        elif self.path == '/api/radio/info' or self.path == '/api/radio/info/':
            self._serve_radio_info()
        elif self.path == '/api/radio/nodes' or self.path == '/api/radio/nodes/':
            self._serve_radio_nodes()
        elif self.path == '/api/radio/channels' or self.path == '/api/radio/channels/':
            self._serve_radio_channels()
        elif self.path == '/api/radio/status' or self.path == '/api/radio/status/':
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
        # ─────────────────────────────────────────────────────────────
        # Meshtastic API Proxy - POST endpoints
        # ─────────────────────────────────────────────────────────────
        if self.path.startswith('/api/v1/toradio'):
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
        elif self.path == '/api/radio/message' or self.path == '/api/radio/message/':
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
        """Handle POST /api/radio/message - send a message via radio."""
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

            conn = self._get_radio_connection()
            if not conn:
                self._serve_json({
                    "error": "Radio not available",
                    "detail": "meshtastic Python library not installed. "
                              "Install with: pip3 install meshtastic",
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
                    "detail": "Verify meshtasticd is running and TCP is enabled.",
                }, status=502)

        except json.JSONDecodeError:
            self._serve_json({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.warning(f"Radio message send error: {e}")
            self._serve_json({"error": "Send failed"}, status=500)

    def _serve_static_html(self):
        """Serve static HTML files with no-cache headers."""
        from urllib.parse import urlparse, unquote
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
        """Serve live node GeoJSON."""
        if self.collector:
            geojson = self.collector.collect()
        else:
            geojson = {"type": "FeatureCollection", "features": []}

        data = json.dumps(geojson).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self._send_cors_header()
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

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

        # Include radio connection status
        status["radio"] = self._get_radio_status_summary()

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
        from urllib.parse import unquote
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
            from urllib.parse import parse_qs, urlparse
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
        from urllib.parse import parse_qs, urlparse

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
            from urllib.parse import parse_qs, urlparse
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
        from urllib.parse import urlparse, parse_qs

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
    # Meshtastic API Proxy - MeshForge owns the web client
    # ─────────────────────────────────────────────────────────────────

    def _get_client_id(self) -> str:
        """Generate a client ID from the request for per-client packet buffering.

        Uses the client IP + a session cookie to distinguish browser tabs.
        """
        client_ip = self.client_address[0]

        # Check for session cookie
        cookie_header = self.headers.get('Cookie', '')
        session_id = ''
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('meshforge_session='):
                session_id = part.split('=', 1)[1]
                break

        if not session_id:
            # Generate from User-Agent + remote port as fallback
            ua = self.headers.get('User-Agent', '')
            session_id = f"{hash(ua) & 0xFFFF:04x}"

        return f"{client_ip}:{session_id}"

    def _proxy_fromradio(self):
        """Serve multiplexed FromRadio packets via the API proxy.

        Each client gets its own stream of packets. The proxy ensures
        ACK packets reach every connected browser.
        """
        if not self.api_proxy:
            self.send_error(503, "Meshtastic API proxy not running")
            return

        client_id = self._get_client_id()
        packet = self.api_proxy.get_next_packet(client_id)

        # Check if we need to set a session cookie (for per-tab multiplexing)
        cookie_header = self.headers.get('Cookie', '')
        needs_cookie = 'meshforge_session=' not in cookie_header

        if packet:
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-protobuf')
            self.send_header('Content-Length', str(len(packet)))
            self._send_cors_header()
            self.send_header('Cache-Control', 'no-cache, no-store')
            if needs_cookie:
                session = os.urandom(8).hex()
                self.send_header('Set-Cookie',
                                 f'meshforge_session={session}; Path=/; SameSite=Lax')
            self.end_headers()
            self.wfile.write(packet)
        else:
            # No data - return empty 200 (meshtasticd convention)
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-protobuf')
            self.send_header('Content-Length', '0')
            self._send_cors_header()
            self.send_header('Cache-Control', 'no-cache, no-store')
            if needs_cookie:
                session = os.urandom(8).hex()
                self.send_header('Set-Cookie',
                                 f'meshforge_session={session}; Path=/; SameSite=Lax')
            self.end_headers()

    def _proxy_toradio(self):
        """Forward ToRadio protobuf to meshtasticd via the proxy."""
        if not self.api_proxy:
            self.send_error(503, "Meshtastic API proxy not running")
            return

        # Meshtastic protobuf packets are small (< 512 bytes)
        max_size = 512

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                self.send_error(411, "Length Required")
                return
            if content_length > max_size:
                self.send_error(413, "Payload too large")
                return
            data = self.rfile.read(content_length)

            if not data:
                self.send_response(400)
                self._send_cors_header()
                self.send_header('Content-Length', '0')
                self.end_headers()
                return

            success = self.api_proxy.send_toradio(data)

            if success:
                self.send_response(200)
                self._send_cors_header()
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                self.send_error(502, "Failed to forward to meshtasticd")

        except Exception as e:
            logger.debug(f"toradio proxy error: {e}")
            self.send_error(500, str(e))

    def _proxy_json(self, path: str):
        """Proxy a /json/* endpoint from meshtasticd."""
        if not self.api_proxy:
            # Fallback: try direct HTTP client
            try:
                from utils.meshtastic_http import get_http_client
                client = get_http_client()
                if path == '/json/nodes':
                    data = client.get_nodes_as_dicts()
                    self._serve_json(data)
                    return
                elif path == '/json/report':
                    data = client.get_report_raw()
                    if data:
                        self._serve_json(data)
                        return
            except Exception as e:
                logger.debug(f"Protobuf client JSON request failed: {e}")
            self.send_error(503, "Meshtastic API proxy not running")
            return

        data = self.api_proxy.proxy_json(path)
        if data:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self._send_cors_header()
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(502, f"Could not proxy {path} from meshtasticd")

    def _proxy_toradio_json(self, path: str):
        """Proxy a POST JSON endpoint to meshtasticd (blink, restart, etc.)."""
        if not self.api_proxy:
            self.send_error(503, "Meshtastic API proxy not running")
            return

        result = self.api_proxy.proxy_endpoint(path)
        if result:
            content, content_type = result
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self._send_cors_header()
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(502, f"Could not proxy {path}")

    def _serve_mesh_web_client(self):
        """Serve the Meshtastic web client from disk.

        MeshForge owns the browser — serves meshtasticd's web client files
        directly from /usr/share/meshtasticd/web/ instead of proxying HTML
        through the network.  API calls are routed through MeshForge's
        multiplexed proxy so the web client gets proper phantom-node
        filtering and stream multiplexing.

        When API proxy is disabled, redirects to meshtasticd's native
        web client at :9443 so MeshForge doesn't consume fromradio packets.

        Static files:  /mesh/*           -> disk read from MESHTASTICD_WEB_DIR
        API proxied:   /mesh/api/v1/*    -> MeshForge multiplexed proxy
                       /mesh/json/*      -> MeshForge sanitized proxy
        """
        # When API proxy is disabled, redirect to native meshtasticd web client.
        # This avoids MeshForge consuming fromradio packets that the native
        # web client needs.
        if not self.api_proxy:
            host = self.headers.get('Host', 'localhost:5000').split(':')[0]
            redirect_url = f"https://{host}:9443/"
            self.send_response(302)
            self.send_header('Location', redirect_url)
            self.end_headers()
            return

        # Map /mesh/ to / within the web client dir
        path = self.path
        if path == '/mesh' or path == '/mesh/':
            path = '/index.html'
        elif path.startswith('/mesh/'):
            path = path[5:]  # Strip /mesh prefix -> /index.html, /static/...

        # Route API endpoints through MeshForge's sanitized, multiplexed
        # proxy.  The web client's fetch() calls resolve here because
        # they are relative to the /mesh/ origin.
        if path == '/json/nodes' or path == '/json/nodes/':
            self._proxy_json('/json/nodes')
            return
        if path == '/json/report' or path == '/json/report/':
            self._proxy_json('/json/report')
            return
        if path == '/json/blink' or path == '/json/blink/':
            self._proxy_json('/json/blink')
            return
        if path.startswith('/api/v1/fromradio'):
            self._proxy_fromradio()
            return
        if path.startswith('/api/v1/toradio'):
            self._proxy_toradio()
            return

        # Serve static files from meshtasticd's web directory on disk
        web_dir = Path(MESHTASTICD_WEB_DIR)
        if not web_dir.is_dir():
            self._serve_mesh_client_unavailable()
            return

        # Reject path traversal
        if '..' in path:
            self.send_error(400, "Invalid path")
            return

        file_path = web_dir / path.lstrip('/')

        # SPA fallback: non-file routes get index.html (React router)
        if not file_path.is_file():
            file_path = web_dir / 'index.html'
            if not file_path.is_file():
                self._serve_mesh_client_unavailable()
                return

        # Prevent path traversal via symlinks
        try:
            file_path.resolve().relative_to(web_dir.resolve())
        except (ValueError, OSError):
            self.send_error(403, "Forbidden")
            return

        # Read and serve the file
        content_type = mimetypes.guess_type(str(file_path))[0] or 'application/octet-stream'
        try:
            data = file_path.read_bytes()

            # Rewrite HTML so the React SPA works under the /mesh/ subpath.
            #
            # meshtasticd builds its web client for serving at root /.
            # Vite produces root-absolute paths: src="/assets/index-xxx.js"
            # <base href> does NOT affect root-absolute paths, only relative
            # ones.  So we must:
            #   1. Set <base href="/mesh/"> (replace existing or inject)
            #   2. Strip the leading / from src= and href= values, making
            #      them relative so the <base> tag takes effect:
            #      "/assets/x.js" -> "assets/x.js" -> resolves to /mesh/assets/x.js
            if content_type and content_type.startswith('text/html'):
                html = data.decode('utf-8', errors='replace')
                html = self._rewrite_mesh_html(html)
                data = html.encode('utf-8')

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self._send_cors_header()
            if content_type and content_type.startswith('text/html'):
                self.send_header('Cache-Control', 'no-cache')
            else:
                # Cache static assets (JS/CSS/fonts/images) for 1 hour
                self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(data)

        except (OSError, PermissionError) as e:
            logger.error("Failed to serve mesh web client file %s: %s", file_path, e)
            self.send_error(500, "Failed to read file")

    @staticmethod
    def _rewrite_mesh_html(html: str) -> str:
        """Rewrite HTML asset paths so the meshtastic SPA works at /mesh/.

        Vite/CRA build tools emit root-absolute paths (src="/assets/x.js").
        The HTML <base> tag only affects *relative* URLs, not root-absolute
        ones, so we must also strip the leading "/" to make them relative.

        Steps:
          1. Replace or inject <base href="/mesh/">
          2. Convert root-absolute src/href values to relative paths
             e.g. src="/assets/x.js" -> src="assets/x.js"
             The browser then resolves "assets/x.js" via <base> as
             /mesh/assets/x.js — which routes to _serve_mesh_web_client.
        """
        base_tag = '<base href="/mesh/">'

        # Step 1: Ensure correct <base> tag
        if re.search(r'<base\b', html, re.IGNORECASE):
            # Replace existing <base> (e.g. <base href="/">) with ours
            html = re.sub(
                r'<base\b[^>]*>',
                base_tag, html, count=1, flags=re.IGNORECASE,
            )
        else:
            # Inject after <head> (handles <head lang="en">, <head\n>, etc.)
            html = re.sub(
                r'(<head\b[^>]*>)',
                rf'\1{base_tag}',
                html, count=1, flags=re.IGNORECASE,
            )

        # Step 2: Strip leading "/" from root-absolute src= and href= values
        # so they become relative and resolve through <base href="/mesh/">.
        #
        #   src="/assets/index.js"  -> src="assets/index.js"
        #   href="/favicon.svg"     -> href="favicon.svg"
        #
        # Preserved (not rewritten):
        #   /mesh/...  (already correct)
        #   //cdn...   (protocol-relative)
        #   href="/"   (bare root — requires [^"']+ i.e. at least 1 char)
        html = re.sub(
            r'((?:src|href)\s*=\s*["\'])/((?!mesh/|/)[^"\']+)',
            r'\1\2',
            html,
            flags=re.IGNORECASE,
        )

        # Step 3: Inject CSS to prevent body-level scrollbar.
        # The meshtastic SPA is full-viewport; all scrolling happens inside
        # React components.  A body scrollbar steals width from the right
        # side, partially covering the sidebar/drawer menu.
        scrollbar_fix = (
            '<style data-meshforge>'
            'html,body{overflow:hidden;margin:0;padding:0;'
            'height:100%;width:100%}'
            '</style>'
        )
        html = re.sub(
            r'(</head>)',
            rf'{scrollbar_fix}\1',
            html, count=1, flags=re.IGNORECASE,
        )

        return html

    def _serve_mesh_client_unavailable(self):
        """Serve a page when meshtasticd web client files are not on disk."""
        html = """<!DOCTYPE html>
<html><head><title>MeshForge - Meshtastic Web Client</title>
<style>
body { font-family: sans-serif; background: #0a0e1a; color: #e0e0e0;
       display: flex; justify-content: center; align-items: center;
       min-height: 100vh; margin: 0; }
.card { background: #141e2e; border-radius: 12px; padding: 2em;
        max-width: 500px; text-align: center; }
h1 { color: #4fc3f7; }
a { color: #66bb6a; }
code { background: #1a2a3a; padding: 2px 8px; border-radius: 4px; }
</style></head><body>
<div class="card">
<h1>Meshtastic Web Client</h1>
<p>Web client files not found at <code>%s</code></p>
<p>Install meshtasticd to get the web client:</p>
<pre style="text-align:left;background:#1a2a3a;padding:1em;border-radius:8px;">
sudo apt install meshtasticd</pre>
<p>Or check your <code>/etc/meshtasticd/config.yaml</code>:</p>
<pre style="text-align:left;background:#1a2a3a;padding:1em;border-radius:8px;">
Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web</pre>
<p style="margin-top:2em;">
  <a href="/">MeshForge NOC Map</a> |
  <a href="/api/proxy/status">Proxy Status</a>
</p>
</div></body></html>""" % MESHTASTICD_WEB_DIR
        data = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_proxy_status(self):
        """Serve API proxy status and statistics."""
        if not self.api_proxy:
            self._serve_json({
                "enabled": False,
                "error": "API proxy not started",
            })
            return

        stats = self.api_proxy.stats
        self._serve_json({
            "enabled": True,
            "connected": self.api_proxy.is_connected,
            "target": f"{self.api_proxy.host}:{self.api_proxy.port}",
            "tls": self.api_proxy.tls,
            "packets_received": stats.packets_received,
            "packets_forwarded": stats.packets_forwarded,
            "toradio_forwarded": stats.toradio_forwarded,
            "json_proxied": stats.json_proxied,
            "active_clients": stats.active_clients,
            "errors": stats.errors,
            "started_at": stats.started_at.isoformat() if stats.started_at else None,
            "last_packet": stats.last_packet_time.isoformat() if stats.last_packet_time else None,
        })

    # ─────────────────────────────────────────────────────────────────
    # Radio Control API - MeshForge-owned Meshtastic access
    # ─────────────────────────────────────────────────────────────────

    def _get_radio_connection(self):
        """Get or create radio connection manager."""
        if not _HAS_MESHTASTIC_CONN:
            return None
        return _get_connection_manager(mode=_ConnectionMode.AUTO)

    def _serve_radio_info(self):
        """Serve radio device information."""
        conn = self._get_radio_connection()
        if not conn:
            self._serve_json({"error": "meshtastic library not available"}, status=500)
            return

        try:
            info = conn.get_radio_info()
            info["connection_mode"] = conn.get_mode()
            info["timestamp"] = datetime.now().isoformat()
            self._serve_json(info)
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)

    def _serve_radio_nodes(self):
        """Serve nodes from directly connected radio."""
        conn = self._get_radio_connection()
        if not conn:
            self._serve_json({"error": "meshtastic library not available"}, status=500)
            return

        try:
            nodes = conn.get_nodes()
            self._serve_json({
                "nodes": nodes,
                "count": len(nodes),
                "connection_mode": conn.get_mode(),
                "timestamp": datetime.now().isoformat()
            })
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)

    def _serve_radio_channels(self):
        """Serve channels from directly connected radio."""
        conn = self._get_radio_connection()
        if not conn:
            self._serve_json({"error": "meshtastic library not available"}, status=500)
            return

        try:
            channels = conn.get_channels()
            self._serve_json({
                "channels": channels,
                "count": len(channels),
                "connection_mode": conn.get_mode(),
                "timestamp": datetime.now().isoformat()
            })
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)

    def _serve_radio_status(self):
        """Serve radio connection status."""
        conn = self._get_radio_connection()
        if not conn:
            self._serve_json({
                "connected": False,
                "mode": "unavailable",
                "error": "meshtastic library not available"
            })
            return

        try:
            # Check if connection is available
            is_available = conn.is_available() if conn.mode.value == "tcp" else True
            has_persistent = conn.has_persistent()

            self._serve_json({
                "connected": is_available or has_persistent,
                "mode": conn.get_mode(),
                "persistent_owner": conn.get_persistent_owner(),
                "host": conn.host,
                "port": conn.port,
                "serial_port": conn.serial_port,
                "timestamp": datetime.now().isoformat()
            })
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in km."""
        R = 6371  # Earth radius in km
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat/2)**2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon/2)**2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

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
