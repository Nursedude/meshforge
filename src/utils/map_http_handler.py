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
- GET /api/nodes/directory -> persistent node directory (Issue #49) — every
                              cached node across protocols, including those
                              older than the observations retention window
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

import gzip
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
from utils._map_visualization import VisualizationEndpointsMixin


# ── Query parameter helper ────────────────────────────────────────────
def _safe_query_param(query, key, default=None):
    """Safely extract a single query parameter value."""
    values = query.get(key)
    if not values:
        return default
    return values[0] if values[0] else default


# ── Region presets ────────────────────────────────────────────────────
# Single source of truth lives in utils.region_presets so the MOC
# Analysis Tool and the map HTTP handler share the same bbox definitions.
from utils.region_presets import REGION_PRESETS  # noqa: E402,F401


# ── View presets (server-side filter mirror of web/node_map.html dropdown) ────
# Same six options the operator picks in the View dropdown. Moving these
# server-side shrinks /api/nodes/geojson + /api/nodes/directory from the
# 50K+-feature federated union to just the slice the preset wants. The
# client-side switch in node_map.html still runs as defense-in-depth.
#
# Each spec: optional `origins` (allowed source_origin values),
# `exclude_federated` (drop properties.federated=True), `max_age_s` (drop
# features whose numeric last_heard/last_seen is older than this).
# `custom`, `fleet_union`, `all_gps` are intentionally absent — they're
# no-ops on the server (everything passes through).
VIEW_PRESETS = {
    "live_rf": {
        "origins": {"local_radio"},
        "exclude_federated": True,
        "max_age_s": 300,
    },
    "live_rf_mqtt": {
        "origins": {"local_radio", "mqtt_local"},
        "exclude_federated": True,
        "max_age_s": 900,
    },
    "external_only": {
        "origins": {
            "meshcore_public", "aredn_worldmap",
            "public_fallback", "mqtt_global",
        },
    },
    "local_only": {
        "exclude_federated": True,
    },
    # Pass-through presets: validated as known so we can return a
    # 'preset_filtered' marker, but no predicate applies on the server.
    "fleet_union": {},
    "all_gps": {},
    "custom": {},
}


def _feature_numeric_timestamp(props: Dict[str, Any]) -> Optional[float]:
    """Pick the numeric last-seen timestamp from a feature's properties.

    Live geojson features carry both `last_seen` (human string) and
    `last_heard` (numeric epoch). Directory snapshot features carry only
    `last_seen` (numeric epoch). Federated peer features carry whatever
    the peer pushed — could be either shape. Returns the first numeric
    candidate, or None if neither field is a number.
    """
    for key in ("last_heard", "last_seen"):
        v = props.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _apply_view_preset(features: List[Dict[str, Any]],
                       preset: Optional[str],
                       now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Filter a list of GeoJSON features by a View preset.

    Pure function — no I/O, no DB, no lock. Pass through if `preset` is
    None, unknown, or maps to a no-op spec (custom/fleet_union/all_gps).
    """
    if not preset:
        return features
    spec = VIEW_PRESETS.get(preset)
    if not spec:
        return features  # unknown or pass-through preset
    if not (spec.get("origins") or spec.get("exclude_federated") or spec.get("max_age_s")):
        return features  # explicit no-op (fleet_union/all_gps/custom)

    now = now if now is not None else time.time()
    origins = spec.get("origins")
    exclude_fed = spec.get("exclude_federated", False)
    max_age = spec.get("max_age_s")

    out: List[Dict[str, Any]] = []
    for f in features:
        props = f.get("properties") or {}
        if exclude_fed and props.get("federated"):
            continue
        if origins is not None and props.get("source_origin", "") not in origins:
            continue
        if max_age is not None:
            ts = _feature_numeric_timestamp(props)
            if ts is None or (now - ts) > max_age:
                continue
        out.append(f)
    return out


def _apply_view_preset_to_position_less(
    entries: List[Dict[str, Any]],
    preset: Optional[str],
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Apply a View preset to position-less directory entries.

    The position_less list shape is dicts (not Features) carrying the
    same id/network/source_origin/last_seen/federated keys. Wrap into
    a synthetic Feature shape just long enough to reuse `_apply_view_preset`.
    """
    if not preset or preset not in VIEW_PRESETS:
        return entries
    spec = VIEW_PRESETS[preset]
    if not (spec.get("origins") or spec.get("exclude_federated") or spec.get("max_age_s")):
        return entries
    wrapped = [{"properties": e} for e in entries]
    filtered = _apply_view_preset(wrapped, preset, now=now)
    return [w["properties"] for w in filtered]


class MapRequestHandler(
    RadioEndpointsMixin,
    MeshtasticProxyMixin,
    VisualizationEndpointsMixin,
    SimpleHTTPRequestHandler,
):
    """HTTP handler that serves the map HTML and node GeoJSON API."""

    collector = None  # MapDataCollector instance
    web_dir: Optional[str] = None
    # CORS: None = allow all, list = allow specific origins
    allowed_origins: Optional[List[str]] = None
    # Meshtastic API proxy (deprecated — always None, kept for graceful 503 responses)
    api_proxy = None

    # Cold-start warming state (Issue #44 / F3). True from server bind
    # until the background warmup thread completes its first collect.
    # Atomic-swap to False is the only state transition; never set back
    # to True at runtime. /healthz returns 200 in either state.
    is_warming = False
    # Unix timestamp when the server bound — included in 503 warming
    # responses so monitors can compute "warming for N seconds."
    warming_started_at: Optional[float] = None

    # Default allowed origins when none explicitly configured
    _DEFAULT_ORIGINS = ['http://localhost', 'https://localhost']

    # Content-Security-Policy applied to all HTML responses. Scope A
    # ("loose CSP") — allows `'unsafe-inline'` because node_map.html
    # carries ~30 inline event handlers (`onclick=`) and the
    # Python-emitted HTML has inline <script> blocks for map init.
    # Scope B (no `'unsafe-inline'`) is a post-May-17 UI refactor —
    # see project_npm_security_posture.md.
    #
    # Origins allowed for script-src match the SRI-pinned CDN refs
    # already in the repo (audited 94398db, 2026-05-14):
    #   unpkg.com           leaflet, leaflet.markercluster, leaflet.heat, d3
    #   cdn.jsdelivr.net    chart.js
    #   d3js.org            d3.v7.min (path_visualizer, topology_visualizer)
    #
    # img-src https: is permissive intentionally — tile servers
    # (OpenStreetMap, CartoDB, ESRI, etc.) are operator-configurable
    # at runtime, and pinning each one in the policy would require
    # rebuilding the CSP on every tile-source change.
    _CSP_POLICY = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' "
            "https://unpkg.com https://cdn.jsdelivr.net https://d3js.org; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )

    def _send_security_headers(self):
        """Send CSP + companion hardening headers for HTML responses.

        Called from HTML-emitting handlers only (not JSON / metrics —
        those don't render in a script context). CSP is the load-bearing
        line; the other three are zero-cost defense-in-depth.
        """
        self.send_header('Content-Security-Policy', self._CSP_POLICY)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer-when-downgrade')
        self.send_header('X-Frame-Options', 'DENY')

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

    def send_response(self, code, message=None):
        """Override to capture status code for /metrics instrumentation.

        BaseHTTPRequestHandler.send_response writes the status line
        but doesn't expose the code. Phase D-3 metrics need it to
        bucket requests as 2xx/3xx/4xx/5xx in Prometheus. Storing
        on `self` is per-request safe (each request is a new
        handler instance).
        """
        self._last_status = code
        super().send_response(code, message)

    def do_GET(self):
        # Phase D-3: top-level wrapper for HTTP metrics. The inner
        # dispatch is unchanged.
        import time as _time
        start = _time.perf_counter()
        # Parse path and query once for all routes
        parsed = urlparse(self.path)
        path_only = parsed.path.rstrip('/')
        self._query = parse_qs(parsed.query)
        self._last_status = 0  # reset; send_response will overwrite

        try:
            self._dispatch_get(path_only)
        finally:
            try:
                from utils import map_metrics
                duration = _time.perf_counter() - start
                # Normalize endpoint label to keep cardinality small.
                endpoint_label = self._endpoint_label(path_only)
                map_metrics.record_http(
                    method="GET",
                    endpoint=endpoint_label,
                    status_code=self._last_status or 0,
                    duration_s=duration,
                )
            except (ImportError, Exception):  # never let metrics break dispatch
                pass

    @staticmethod
    def _endpoint_label(path_only: str) -> str:
        """Normalize a request path to a stable Prometheus label.

        Bucket parametrized paths (e.g. /api/nodes/trajectory/<id>)
        into a single template so cardinality stays bounded. This is
        the bag-of-routes the Phase E dashboards will graph.
        """
        if path_only in (
            "", "/index.html", "/healthz", "/metrics",
            "/api/status", "/api/nodes/geojson", "/api/nodes/history",
            "/api/nodes/directory",
            "/api/messages/queue", "/api/messages/rx-status",
            "/api/network/topology", "/api/region-presets",
            "/api/settings", "/api/websocket/status", "/api/weather",
            "/fleet/slo",
        ):
            return path_only or "/"
        # Parametrized routes — bucket by prefix
        if path_only.startswith("/api/nodes/trajectory/"):
            return "/api/nodes/trajectory/{id}"
        if path_only.startswith("/api/nodes/snapshot"):
            return "/api/nodes/snapshot"
        if path_only.startswith("/api/coverage/"):
            return "/api/coverage/{lat}/{lon}/{alt}"
        if path_only.startswith("/api/los/"):
            return "/api/los/{lat1}/{lon1}/{lat2}/{lon2}"
        if path_only.startswith("/api/messages/received"):
            return "/api/messages/received"
        if path_only.startswith("/api/v1/"):
            return "/api/v1/*"
        if path_only.startswith("/api/radio/"):
            return "/api/radio/*"
        if path_only.startswith("/api/proxy/"):
            return "/api/proxy/*"
        if path_only.startswith("/api/"):
            return "/api/other"
        # Static files / unknown — bucket together
        return "/static_or_other"

    def _dispatch_get(self, path_only: str):
        # /healthz is ALWAYS available — Prometheus + monitors poll
        # this; it must return 200 even during warming. State is
        # in the body so monitors can distinguish "ready" from
        # "warming" without breaking is-host-up checks.
        if path_only == '/healthz':
            self._serve_healthz()
            return

        # /metrics is ALWAYS available — Prometheus scrape target.
        # During warming, gauges report state via SERVICE_UP=0 +
        # SERVICE_WARMING_SINCE=<ts>; counters are still meaningful
        # (no requests yet → all zeros), so a scrape during warming
        # produces a valid sample, not a 503.
        if path_only == '/metrics':
            self._serve_metrics()
            return

        # Cold-start warming gate (F3). Issue: ThreadingHTTPServer
        # binds AFTER _prewarm_collector() finishes, so :5000 was
        # connection-refused for 10-30s on cold start. We now bind
        # first and surface the warming state explicitly.
        if self.is_warming:
            self._serve_warming_503(path_only)
            return

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
        elif path_only == '/api/nodes/directory':
            self._serve_directory()
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
        elif path_only == '/fleet/slo':
            self._serve_fleet_slo()
        elif path_only == '/lab/rollup' or path_only == '/lab/rollup/':
            self._serve_lab_rollup(variant='leaderboard')
        elif path_only == '/lab/rollup/alphabetical':
            self._serve_lab_rollup(variant='alphabetical')
        elif path_only == '/lab/synth-rollup' or path_only == '/lab/synth-rollup/':
            self._serve_lab_rollup(variant='synth')
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
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404, f"File not found: {path_only}")

    def _serve_geojson(self):
        """Serve live node GeoJSON with optional bbox/region/preset filtering.

        Supports three orthogonal filters that compose: ?region= (named
        bbox preset), ?bbox= (explicit bbox), and ?preset= (View preset
        — origin/age/federation predicates). Preset is applied first so
        the bbox pass walks a smaller list.
        """
        if self.collector:
            geojson = self.collector.collect()
        else:
            geojson = {"type": "FeatureCollection", "features": []}

        # Resolve bbox from ?region= preset or explicit ?bbox= param
        query = getattr(self, '_query', {})

        # View preset filter — applied before bbox so federation's 50K
        # features collapse to the preset slice (often <5K) before any
        # geometry walk. Unknown/missing preset is a pass-through.
        preset_key = _safe_query_param(query, "preset")
        if preset_key in VIEW_PRESETS:
            spec = VIEW_PRESETS[preset_key]
            if spec.get("origins") or spec.get("exclude_federated") or spec.get("max_age_s"):
                filtered_features = _apply_view_preset(
                    geojson.get("features", []), preset_key
                )
                geojson = dict(geojson)
                geojson["features"] = filtered_features
                props = dict(geojson.get("properties", {}))
                props["preset_filtered"] = True
                props["preset"] = preset_key
                props["nodes_with_position"] = len(filtered_features)
                geojson["properties"] = props

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
            self._send_security_headers()
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

            # Directory stats (Issue #49) — persistent per-node cache
            # across protocols, with tiered retention. Surfaces total
            # count, by-network, by-source-origin, last-seen range so
            # operators can see at a glance how many MeshCore/AREDN/RNS
            # nodes are cached and which retention tier they fall into.
            try:
                status["directory"] = self.collector._history.get_directory_stats()
            except Exception as e:
                logger.debug(f"directory stats lookup failed: {e}")
                status["directory"] = None

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

        # Federation health (Issue #49 follow-up). Surfaces per-peer
        # status so monitoring can alert on stale federation: a peer
        # in `consecutive_failures > 3` for >5min means we're missing
        # whatever directory entries that box was contributing.
        if self.collector and getattr(self.collector, "_federation", None):
            try:
                snap = self.collector._federation.get_snapshot()
                status["federation"] = {
                    "enabled": True,
                    "peers": list(self.collector._federation.peers),
                    "last_sync": snap.last_sync,
                    "last_attempt": snap.last_attempt,
                    "federated_node_count": len(snap.by_node),
                    "peer_status": [
                        {
                            "hostname": s.hostname,
                            "ok": s.ok,
                            "last_sync": s.last_sync,
                            "last_attempt": s.last_attempt,
                            "last_error": s.last_error,
                            "last_count": s.last_count,
                            "last_latency_ms": s.last_latency_ms,
                            "consecutive_failures": s.consecutive_failures,
                        }
                        for s in snap.peer_status.values()
                    ],
                }
            except Exception as e:
                logger.debug(f"federation status lookup failed: {e}")
                status["federation"] = {"enabled": True, "error": str(e)[:200]}
        else:
            status["federation"] = {"enabled": False, "peers": [], "peer_status": []}

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
        presets (e.g. one on SHORT_TURBO vs another on LongFast) without SSHing to
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

    def _serve_directory(self):
        """Serve the persistent node directory as a GeoJSON FeatureCollection.

        Returns every node ever heard (within tier retention) — superset
        of `/api/nodes/geojson`, which only covers what the latest
        collect cycle saw. Position-less nodes (MeshCore adverts without
        GPS, RNS announces) surface in the sibling `nodes_without_position`
        array, mirroring the convention from Issue #43.

        Reuses the gzip + JSON helper used by /api/nodes/geojson — same
        threshold (10 KB) applies. With ~50k nodes at the count cap, the
        directory dump is ~5 MB raw / ~700 KB gzipped.
        """
        if not self.collector or not self.collector._history:
            self._serve_json({
                "type": "FeatureCollection",
                "features": [],
                "properties": {"error": "history not available"},
                "nodes_without_position": [],
            })
            return

        try:
            features, position_less = (
                self.collector._history.get_directory_snapshot(
                    include_position_less=True
                )
            )
        except Exception as e:
            logger.error(f"directory snapshot failed: {e}")
            self._serve_json({
                "type": "FeatureCollection",
                "features": [],
                "properties": {"error": str(e)[:200]},
                "nodes_without_position": [],
            }, status=500)
            return

        # Optional ?preset= filter (same shape the live geojson endpoint
        # accepts). Directory features carry numeric `last_seen` epoch
        # (Issue #49) so age-based presets work here without the
        # last_heard fallback the live path needs.
        query = getattr(self, '_query', {})
        preset_key = _safe_query_param(query, "preset")
        preset_applied = False
        if preset_key in VIEW_PRESETS:
            spec = VIEW_PRESETS[preset_key]
            if spec.get("origins") or spec.get("exclude_federated") or spec.get("max_age_s"):
                features = _apply_view_preset(features, preset_key)
                position_less = _apply_view_preset_to_position_less(
                    position_less, preset_key
                )
                preset_applied = True

        # Per-network breakdown alongside the full list — same shape
        # /api/status uses, so dashboards can consume either.
        by_network: Dict[str, int] = {}
        for entry in position_less:
            net = entry.get("network", "unknown")
            by_network[net] = by_network.get(net, 0) + 1

        properties = {
            "generated_at": datetime.now().isoformat(),
            "total_features": len(features),
            "total_position_less": len(position_less),
        }
        if preset_applied:
            properties["preset_filtered"] = True
            properties["preset"] = preset_key

        body = {
            "type": "FeatureCollection",
            "features": features,
            "properties": properties,
            "nodes_without_position": position_less,
            "nodes_without_position_by_network": by_network,
        }
        self._serve_json(body)

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

    # gzip threshold: payloads smaller than this aren't worth the CPU cost
    # (ratio is poor on small JSON, latency win is sub-millisecond). 10 KB
    # cuts in below /api/status (~50 KB) and well above /api/region-presets
    # (~2 KB) — sized for the actual endpoints in this file.
    _GZIP_MIN_BYTES = 10 * 1024

    def _client_accepts_gzip(self) -> bool:
        """True iff the request's Accept-Encoding header includes gzip."""
        accept = self.headers.get('Accept-Encoding', '') or ''
        # Tokens are comma-separated; quality values may be present (e.g.
        # "gzip;q=0.5"). Treat "identity;q=0, *;q=0" or "gzip;q=0" as opt-out.
        for token in accept.split(','):
            token = token.strip().lower()
            if token.startswith('gzip'):
                if 'q=0' in token and 'q=0.' not in token:
                    return False
                return True
        return False

    def _serve_healthz(self):
        """Cold-start-safe health endpoint.

        Returns 200 in both warming and ready states so generic
        is-host-up monitors don't false-alarm during cold start.
        State is in the body — Prometheus's `up` metric becomes 1
        as soon as we bind, but a panel can derive "warming time"
        from `state` + `since`.
        """
        import time
        if self.is_warming:
            body = {
                "state": "warming",
                "since": self.warming_started_at,
                "elapsed_s": (
                    time.time() - self.warming_started_at
                    if self.warming_started_at else None
                ),
            }
        else:
            body = {"state": "ready"}
        self._serve_json(body, status=200)

    def _serve_metrics(self):
        """Prometheus scrape endpoint (/metrics).

        Returns the standard text-format exposition for
        prometheus_client's CollectorRegistry. When the optional
        prometheus_client dep isn't installed, returns 503 + a
        clear message rather than 500 — operators can still curl
        /healthz to verify the service is up.
        """
        from utils import map_metrics

        if not map_metrics.is_available():
            self._serve_json(
                {
                    "error": "metrics_unavailable",
                    "reason": "prometheus_client python module not installed",
                    "fix": "pip install -r requirements/monitoring.txt",
                },
                status=503,
            )
            return
        body, content_type = map_metrics.render()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # scraper bailed; nothing to do

    def _serve_warming_503(self, path_only: str):
        """503 response for any non-/healthz path while warming.

        F3 fix: pre-Phase-D, the server didn't bind until prewarm
        completed → external monitors saw connection-refused for
        10-30s on cold start, indistinguishable from a hard failure.
        Now we bind first and return 503 explicitly with `state` and
        `since` so monitors can distinguish startup-warming from
        sustained-down. Retry-After advises clients to back off.
        """
        import time
        self._serve_json(
            {
                "error": "service_warming",
                "state": "warming",
                "path": path_only,
                "since": self.warming_started_at,
                "elapsed_s": (
                    time.time() - self.warming_started_at
                    if self.warming_started_at else None
                ),
                "retry_after_s": 10,
            },
            status=503,
        )

    def _serve_json(self, obj: Any, status: int = 200):
        """Helper to serve a JSON response, gzip-compressed when client supports it."""
        data = json.dumps(obj).encode()
        encoding: Optional[str] = None
        if len(data) >= self._GZIP_MIN_BYTES and self._client_accepts_gzip():
            # compresslevel=6 is the urllib default — ~30–80 ms for 20 MB on
            # Pi-class CPU, yielding ~5–10× shrink for GeoJSON-shaped data.
            data = gzip.compress(data, compresslevel=6)
            encoding = 'gzip'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        if encoding:
            self.send_header('Content-Encoding', encoding)
        # Vary advertises that response varies by Accept-Encoding so any
        # intermediate cache keys correctly. Send it whether or not we
        # gzipped this specific response.
        self.send_header('Vary', 'Accept-Encoding')
        self._send_cors_header()
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            # Client abandoned the request before we finished writing — common
            # when a slow collect made the browser time out. Logging at DEBUG
            # keeps the journal clean; the bare exception used to surface as
            # a noisy traceback per abandoned request.
            logger.debug(f"Client disconnected during _serve_json: {e}")

    def _serve_fleet_slo(self):
        """Serve the MeshAnchor `/fleet/slo` peer contract.

        MA's `/fleet/rollup` polls this on every peer in its fleet.json.
        Producing the same shape MA emits for itself lets a MF box show
        up in the rollup without running a full MA daemon — Path B per
        the `feedback_consolidate_dont_add` consolidation pattern.
        """
        from utils.fleet_snapshot import build_slo_snapshot
        self._serve_json(build_slo_snapshot(collector=self.collector))

    # Lab rollup state files written every 10min by
    # meshforge-lab-rollup.service. Variants:
    #   leaderboard   — traffic rollup sorted worst-fail-first (default)
    #   alphabetical  — traffic rollup sorted by pair (historical)
    #   synth         — multi-user synth-soak rollup (only on synth boxes)
    _LAB_ROLLUP_FILES = {
        'leaderboard':  'lab-traffic-rollup-leaderboard.md',
        'alphabetical': 'lab-traffic-rollup.md',
        'synth':        'lab-synth-soak-rollup.md',
    }

    def _serve_lab_rollup(self, variant: str):
        """Serve the lab rollup markdown produced by meshforge-lab-rollup.

        State file lives under $XDG_STATE_HOME/meshforge/ (default
        ~/.local/state/meshforge). 404s with a fix-hint when the file
        doesn't exist — typical cause is the rollup timer never having
        been installed on this host.
        """
        filename = self._LAB_ROLLUP_FILES.get(variant)
        if filename is None:
            self._serve_text(f"unknown rollup variant: {variant}\n",
                             status=404, content_type='text/plain')
            return

        # XDG_STATE_HOME with the standard ~/.local/state fallback.
        state_home = os.environ.get('XDG_STATE_HOME') or \
            os.path.join(os.path.expanduser('~'), '.local', 'state')
        path = os.path.join(state_home, 'meshforge', filename)

        try:
            with open(path, 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            hint = (
                f"# lab rollup not found\n\n"
                f"`{path}` does not exist on this host.\n\n"
                f"Fix: install and enable the rollup timer on this box:\n"
                f"```\n"
                f"cp /opt/meshforge/templates/systemd/meshforge-lab-rollup-user.service \\\n"
                f"   ~/.config/systemd/user/meshforge-lab-rollup.service\n"
                f"cp /opt/meshforge/templates/systemd/meshforge-lab-rollup-user.timer \\\n"
                f"   ~/.config/systemd/user/meshforge-lab-rollup.timer\n"
                f"systemctl --user daemon-reload\n"
                f"systemctl --user enable --now meshforge-lab-rollup.timer\n"
                f"```\n"
            )
            self._serve_text(hint, status=404, content_type='text/markdown')
            return
        except OSError as e:
            self._serve_text(f"# error reading rollup\n\n{e}\n",
                             status=500, content_type='text/markdown')
            return

        self._serve_text(data, status=200, content_type='text/markdown')

    def _serve_text(self, body, status: int = 200,
                    content_type: str = 'text/plain'):
        """Send a text/markdown response with the gzip + CORS pattern.

        Accepts bytes or str. Uses the same gzip threshold as _serve_json
        so small responses skip compression overhead.
        """
        if isinstance(body, str):
            data = body.encode('utf-8')
        else:
            data = body
        encoding: Optional[str] = None
        if len(data) >= self._GZIP_MIN_BYTES and self._client_accepts_gzip():
            data = gzip.compress(data, compresslevel=6)
            encoding = 'gzip'
        self.send_response(status)
        self.send_header('Content-Type', f'{content_type}; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        if encoding:
            self.send_header('Content-Encoding', encoding)
        self.send_header('Vary', 'Accept-Encoding')
        self._send_cors_header()
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            logger.debug(f"Client disconnected during _serve_text: {e}")

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

    # _serve_network_topology and _serve_weather inherited from
    # VisualizationEndpointsMixin in _map_visualization.py.

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
