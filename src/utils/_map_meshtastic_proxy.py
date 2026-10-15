"""Meshtastic HTTP API proxy methods for MapRequestHandler.

Proxies meshtasticd's HTTP/protobuf API through MeshForge's map server,
providing per-client packet multiplexing and phantom node filtering.

Extracted from map_http_handler.py for file size compliance (CLAUDE.md #6).

Expects the following attributes on the host class:
- self.api_proxy: MeshtasticAPIProxy instance (or None)
- self.headers: HTTP request headers
- self.client_address: (ip, port) tuple
- self.send_response(), self.send_header(), self.send_error(), self.end_headers()
- self.wfile: writable response body
- self.rfile: readable request body
- self._send_cors_header(): method
- self._serve_json(): method
"""

import logging
import os

logger = logging.getLogger(__name__)


class MeshtasticProxyMixin:
    """Mixin providing Meshtastic API proxy methods."""

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

        Operator-gated HERE, whatever path dispatched to it (``/mesh/``
        alias, encoded or suffixed spellings): reading fromradio DRAINS the
        radio's queue (Issue #29), so it is a side effect.

        Each client gets its own stream of packets. The proxy ensures
        ACK packets reach every connected browser.
        """
        if self._refuse_side_effect_get("/api/v1/fromradio"):
            return
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
        """Forward ToRadio protobuf to meshtasticd via the proxy.

        A WRITE to the radio, so it answers only the write methods: the
        ``/mesh/`` alias dispatches GETs here too, and GET is outside the
        default-deny write gate — a LAN ``GET`` with a body transmitted with
        no session (non-author read 2026-10-15, N1). POST/PUT already met
        ``_refuse_without_operator`` before dispatch."""
        if getattr(self, "command", None) not in ("POST", "PUT"):
            self.send_error(405, "toradio is a write: POST or PUT only")
            return
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
        """Proxy a /json/* endpoint from meshtasticd. ``/json/blink`` acts on
        the radio, so it is operator-gated HERE for every spelling."""
        if path == "/json/blink" and self._refuse_side_effect_get("/json/blink"):
            return
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
