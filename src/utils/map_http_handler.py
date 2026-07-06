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
import socket
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
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
# Issue #74: the class is PersistentMessageQueue — the old
# 'MessageQueue' name never existed, so _HAS_MSG_QUEUE was always
# False and the /api/messages/queue SQLite branch was dead code
# (silently served the cache-file fallback).
_MessageQueue, _HAS_MSG_QUEUE = safe_import(
    'gateway.message_queue', 'PersistentMessageQueue'
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


from utils._map_fleet_endpoints import FleetEndpointsMixin
from utils._map_meshtastic_proxy import MeshtasticProxyMixin
from utils._map_node_endpoints import NodeDataEndpointsMixin
from utils._map_radio_endpoints import RadioEndpointsMixin
from utils._map_status_endpoints import StatusEndpointsMixin
from utils._map_visualization import VisualizationEndpointsMixin

# Re-exports (moved to _map_node_endpoints.py in the size-cap split).
# Tests and external callers import these from this module — keep the
# names visible here so `from utils.map_http_handler import ...` works.
from utils._map_node_endpoints import (  # noqa: F401
    VIEW_PRESETS,
    _apply_view_preset,
    _apply_view_preset_to_position_less,
    _feature_numeric_timestamp,
    _safe_query_param,
)
from utils.region_presets import REGION_PRESETS  # noqa: F401


# App-identifying HTTP Server: header (cross-domain fleet presence, Layer 0).
# MeshForge and MeshAnchor serve identically-shaped HTTP APIs on :5000; this
# makes even a `HEAD /` disclose which NOC answered, as a cheap companion to
# the /api/status `app` block. Derived from this repo's own __version__, so a
# version bump tracks automatically.
try:
    from __version__ import __app_name__ as _APP_NAME, __version__ as _APP_VER
    _SERVER_VERSION = f"{_APP_NAME}/{_APP_VER}"
except Exception:
    _SERVER_VERSION = "MeshForge"


def _origin_allowed(origin: str, allowed: Optional[List[str]]) -> bool:
    """Exact-or-/24 CORS origin match (tail-anchored).

    ``allowed`` holds the prefixes passed via ``--cors-origins``. Two shapes:
      * exact host  (``http://localhost``) — the request origin must equal it,
        optionally with a ``:port`` suffix and nothing else after.
      * IP /24 prefix (``http://192.168.86.`` — trailing dot) — the origin must
        complete the final octet with 1-3 digits (+ optional ``:port``).

    A bare ``origin.startswith(prefix)`` let ``http://192.168.86.evil.com`` and
    ``http://localhost.attacker.example`` pass the check (subdomain-suffix CORS
    bypass — an attacker page reads the whole NOC API cross-origin). Anchoring
    the tail with ``$`` closes that while preserving the /24 intent.
    """
    if not origin or not allowed:
        return False
    for prefix in allowed:
        if not prefix:
            continue
        esc = re.escape(prefix)
        # trailing-dot prefix completes an IP octet; otherwise exact host+port
        pat = esc + (r'\d{1,3}(?::\d+)?$' if prefix.endswith('.') else r'(?::\d+)?$')
        if re.match(pat, origin):
            return True
    return False


def _trusted_networks_from_origins(allowed: Optional[List[str]]):
    """Parse the CORS allow-list host parts into ``ip_network`` objects, used to
    gate state-changing / log-exposing endpoints by client IP on a ``0.0.0.0``
    bind. A ``.``-terminated prefix (``http://192.168.86.``) → the /24; a bare IP
    host → /32. Non-IP hosts (``localhost``) are skipped."""
    nets = []
    for prefix in allowed or []:
        if not prefix:
            continue
        host = prefix.split('://', 1)[-1].split(':', 1)[0].rstrip('.')
        cidr = host + '.0/24' if prefix.endswith('.') else host + '/32'
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue  # non-IP host (localhost) or malformed prefix
    return nets


def _client_ip_trusted(client_host: str, allowed: Optional[List[str]]) -> bool:
    """True if ``client_host`` is loopback or inside a configured LAN origin.

    With no ``--cors-origins`` configured (``allowed`` None/empty) only loopback
    is trusted — the secure default for a box that never opted a LAN in."""
    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    return any(ip in net for net in _trusted_networks_from_origins(allowed))


class MapRequestHandler(
    RadioEndpointsMixin,
    MeshtasticProxyMixin,
    VisualizationEndpointsMixin,
    NodeDataEndpointsMixin,
    StatusEndpointsMixin,
    FleetEndpointsMixin,
    SimpleHTTPRequestHandler,
):
    """HTTP handler that serves the map HTML and node GeoJSON API."""

    # Overrides BaseHTTPRequestHandler's default ("BaseHTTP/x.x") so the HTTP
    # Server: header names this app (cross-domain fleet presence, Layer 0).
    server_version = _SERVER_VERSION

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
        if _origin_allowed(origin, origins):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')

    def _client_is_trusted(self) -> bool:
        """True when the request comes from loopback or a configured LAN origin.

        Gate for state-changing (RF transmit, fleet test-runner) and
        data-leaking (journal logs) endpoints. The map binds ``0.0.0.0`` so a
        multi-homed box (AREDN 10.x, wg VPN) otherwise exposes those to
        untrusted networks; this narrows them to the operator's own LAN/loopback
        without breaking the LAN dashboard (the operator's browser is on the
        LAN, so its client IP is inside the CORS /24)."""
        try:
            host = self.client_address[0]
        except (IndexError, AttributeError):
            return False
        return _client_ip_trusted(host, self.allowed_origins)

    def _reject_if_untrusted(self) -> bool:
        """Send 403 + return True when the caller isn't loopback/LAN-trusted."""
        if self._client_is_trusted():
            return False
        self._serve_json(
            {"error": "forbidden",
             "detail": "endpoint restricted to loopback or a configured LAN origin"},
            status=403)
        return True

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
            "/api/network/topology", "/api/network/rns/paths",
            "/api/network/interfaces",
            "/api/region-presets",
            "/api/settings", "/api/websocket/status", "/api/weather",
            "/fleet/slo", "/fleet/cascade", "/fleet/dups",
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
        elif path_only == '/api/network/rns/paths':
            self._serve_rns_paths()
        elif path_only == '/api/network/interfaces':
            self._serve_rns_interfaces()
        elif path_only == '/api/weather':
            self._serve_weather()
        elif path_only == '/fleet/slo':
            self._serve_fleet_slo()
        elif path_only == '/fleet/dups':
            self._serve_fleet_dups()
        elif path_only == '/fleet/logs':
            self._serve_fleet_logs()
        elif path_only == '/fleet/tracer-fires':
            self._serve_fleet_tracer_fires()
        elif path_only == '/fleet/tests':
            self._serve_fleet_tests_list()
        elif path_only == '/fleet/cascade':
            self._serve_fleet_cascade()
        elif path_only == '/api/gateway/delivery':
            self._serve_gateway_delivery()
        elif path_only == '/api/gateway/queue':
            self._serve_gateway_queue()
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
        # ─────────────────────────────────────────────────────────────
        # Fleet test runner — operator clicks dashboard button → fire
        # ─────────────────────────────────────────────────────────────
        elif path_only == '/fleet/run-test':
            self._serve_fleet_run_test()
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

        Keying a licensed transmitter is a state-changing action, so it is
        gated to loopback / the configured LAN (never an arbitrary host that
        can merely reach ``0.0.0.0:5000``). Unattended third-party RF control
        from an untrusted network is an operator/FCC problem.
        """
        if self._reject_if_untrusted():
            return
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
                # Meshtastic node numbers are unsigned 32-bit; reject anything
                # outside that range before it reaches send_text_direct (the
                # _VALID_DESTINATION regex allows arbitrarily long digit runs).
                if not (0 <= dest_num <= 0xFFFFFFFF):
                    self._serve_json({"error": "destination out of range"}, status=400)
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

        # Security: prevent path traversal. Use relative_to on the resolved
        # paths, not a string prefix — `startswith(base)` also matches a
        # sibling dir sharing the prefix (base `web` → `web-secret`), a
        # one-rename-away escape. Mirrors _serve_mesh_web_client's guard.
        try:
            base_dir = Path(self.web_dir) if self.web_dir else Path(__file__).parent.parent.parent / "web"
            file_path = file_path.resolve()
            base_dir = base_dir.resolve()
            file_path.relative_to(base_dir)
        except ValueError:
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

    def _serve_json(
        self,
        obj: Any,
        status: int = 200,
        size_observer: Optional[Callable[[int, Optional[int]], None]] = None,
    ):
        """Helper to serve a JSON response, gzip-compressed when client supports it.

        Args:
            obj: The Python value to JSON-serialize.
            status: HTTP status code (default 200).
            size_observer: Optional callback invoked with
                ``(raw_bytes, compressed_bytes_or_None)`` after the
                serializer decides whether to gzip. Used by
                ``/api/nodes/directory`` to feed the size-budget alarm
                (Issue #64). Observer exceptions are swallowed —
                observability must never break the request.
        """
        data = json.dumps(obj).encode()
        raw_bytes = len(data)
        encoding: Optional[str] = None
        if len(data) >= self._GZIP_MIN_BYTES and self._client_accepts_gzip():
            # compresslevel=6 is the urllib default — ~30–80 ms for 20 MB on
            # Pi-class CPU, yielding ~5–10× shrink for GeoJSON-shaped data.
            data = gzip.compress(data, compresslevel=6)
            encoding = 'gzip'
        if size_observer is not None:
            try:
                size_observer(
                    raw_bytes,
                    len(data) if encoding else None,
                )
            except Exception as e:
                logger.debug("size_observer raised, ignoring: %s", e)
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

    def _send_prebuilt_json(
        self,
        raw_bytes: bytes,
        gzip_bytes: Optional[bytes],
        status: int = 200,
    ) -> None:
        """Emit a JSON response from already-serialized bytes (Issue #70).

        Used by short-TTL response caches that pre-build both the raw
        and gzipped variants. The per-request decision — gzip or not —
        depends on this specific client's ``Accept-Encoding``, but the
        expensive serialization work happens once per TTL window.

        ``gzip_bytes=None`` means the cached body is below the gzip
        threshold and gzip was never built; the response is always raw.
        """
        if gzip_bytes is not None and self._client_accepts_gzip():
            data = gzip_bytes
            encoding: Optional[str] = 'gzip'
        else:
            data = raw_bytes
            encoding = None
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
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
            logger.debug(f"Client disconnected during _send_prebuilt_json: {e}")

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

    def _serve_message_queue(self):
        """Serve pending messages from the gateway message queue."""
        messages = []

        # Try to load from SQLite message queue
        if not _HAS_MSG_QUEUE:
            logger.debug("MessageQueue not available")
        else:
            try:
                queue = _MessageQueue()
                pending = queue.get_pending(limit=50)
                for msg in pending:
                    payload = msg.payload or {}
                    messages.append({
                        "id": msg.id,
                        "source": payload.get("source_id", ""),
                        "source_name": payload.get("source_name", ""),
                        "target": payload.get("destination_id", ""),
                        "target_name": payload.get("target_name", ""),
                        "network": msg.destination,
                        "status": msg.status.value,
                        "created_at": msg.created_at.isoformat(),
                        "message_type": payload.get("message_type", "text"),
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
        # Parse query parameters. Clamp limit defensively — a bare int() here
        # (outside the try below) turned ?limit=abc into an uncaught 500 and
        # ?limit=-1 into SQLite `LIMIT -1` (unbounded table dump on the request
        # thread).
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            limit = int(params.get('limit', ['50'])[0])
        except (ValueError, TypeError):
            limit = 50
        limit = max(1, min(limit, 500))
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

    # Node-data endpoints (geojson/directory/history/trajectory/snapshot,
    # region presets, settings, coverage, LOS) + the VIEW_PRESETS filter
    # helpers are inherited from NodeDataEndpointsMixin in
    # _map_node_endpoints.py (re-exported above).

    # /api/status (_serve_status, _get_radio_status_summary,
    # _read_watchdog_block, _read_mini_state_block, _get_local_radio_config)
    # inherited from StatusEndpointsMixin in _map_status_endpoints.py.

    # /fleet/* (slo, logs, tracer-fires, tests, run-test, cascade),
    # /api/gateway/* (delivery, queue), /api/network/interfaces,
    # /api/network/rns/paths and /lab/rollup* inherited from
    # FleetEndpointsMixin in _map_fleet_endpoints.py.

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
