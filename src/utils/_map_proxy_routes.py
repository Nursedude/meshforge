"""Meshtastic API proxy, radio control, and mesh web client methods.

Extracted from map_http_handler.py for file size compliance (CLAUDE.md #6).

This module defines _MapProxyMixin which is mixed into MapRequestHandler.
Methods reference self.send_response, self._serve_json, self._send_cors_header,
etc. which are resolved through MRO from SimpleHTTPRequestHandler and
the main MapRequestHandler class.

Endpoints provided:
  _proxy_fromradio          — GET /api/v1/fromradio (multiplexed protobuf)
  _proxy_toradio            — PUT /api/v1/toradio
  _proxy_json               — GET /json/nodes, /json/report
  _proxy_toradio_json       — POST /json/blink, etc.
  _serve_mesh_web_client    — GET /mesh/* (Meshtastic SPA from disk)
  _serve_proxy_status       — GET /api/proxy/status
  _serve_radio_info         — GET /api/radio/info
  _serve_radio_nodes        — GET /api/radio/nodes
  _serve_radio_channels     — GET /api/radio/channels
  _serve_radio_status       — GET /api/radio/status
"""

import logging
import math
import mimetypes
import os
import re
from datetime import datetime
from pathlib import Path

from utils.safe_import import safe_import

_get_connection_manager, _ConnectionMode, _HAS_MESHTASTIC_CONN = safe_import(
    'utils.meshtastic_connection', 'get_connection_manager', 'ConnectionMode'
)

logger = logging.getLogger(__name__)

# Default path where meshtasticd installs its web client files.
# MeshForge serves these directly from disk instead of proxying HTML.
MESHTASTICD_WEB_DIR = '/usr/share/meshtasticd/web'


class _MapProxyMixin:
    """Meshtastic API proxy and radio control methods for MapRequestHandler.

    Mixed into MapRequestHandler via multiple inheritance.  All methods
    reference attributes (self.api_proxy, self.send_response, etc.)
    provided by SimpleHTTPRequestHandler and the main handler class.
    """

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
