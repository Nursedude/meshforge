"""Mock meshtasticd HTTP server for in-process bridge testing.

Captures PUTs to /api/v1/toradio so tests can assert what the bridge
attempted to send. Does NOT simulate radio behavior or fromradio replies —
this is intent-capture only. Listens plain HTTP (not HTTPS) on an
ephemeral port; bridge config must point at this port with tls=False.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
from urllib.parse import urlparse


class MockMeshtasticDaemon:
    def __init__(self):
        self.received_toradio: list[bytes] = []
        self._server: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        captured = self.received_toradio

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self_inner):
                if urlparse(self_inner.path).path == "/api/v1/toradio":
                    length = int(self_inner.headers.get("Content-Length", 0))
                    captured.append(self_inner.rfile.read(length))
                    self_inner.send_response(200)
                    self_inner.send_header("Content-Length", "0")
                    self_inner.end_headers()
                else:
                    self_inner.send_response(404)
                    self_inner.end_headers()

            def do_GET(self_inner):
                # Bridge code MUST NOT read /api/v1/fromradio (Issue #29).
                # Returning 410 makes any accidental read explicit.
                if urlparse(self_inner.path).path == "/api/v1/fromradio":
                    self_inner.send_response(410)
                    self_inner.end_headers()
                else:
                    self_inner.send_response(404)
                    self_inner.end_headers()

            def log_message(self_inner, *_args):
                pass  # silence default access log

        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def port(self) -> int:
        assert self._server is not None, "MockMeshtasticDaemon not started"
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def clear(self) -> None:
        self.received_toradio.clear()
