"""In-process map server harness for end-to-end smoke tests.

Starts a real `MapServer` on an ephemeral loopback port with
WebSocket and message listener disabled (so the test doesn't need a
running rnsd / mosquitto / meshtasticd). Tests can hit the HTTP
endpoints with `urllib.request` and assert response shape.

Foundation for Phase D: the same harness is the natural place to add
Prometheus exporter scraping tests when that work lands. Keep the
fixture minimal — collector behavior is unit-tested elsewhere
(`tests/test_map_data_collector_diagnostics.py`); this harness
exercises the HTTP wiring + main-thread RNS init invariant
(Issue #44) end-to-end.
"""

from __future__ import annotations

import socket
import threading
from typing import Optional


def _pick_ephemeral_port() -> int:
    """Bind to port 0 to claim an ephemeral port, then release.

    Race-free for our purposes: the OS will not hand the same port
    out a second time before the test thread re-binds it. If a real
    workload steals the port between release and `MapServer.start()`,
    the test will fail loudly (port already in use), not silently
    pass against the wrong server.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MapServerHarness:
    """Lifecycle wrapper around a real MapServer for e2e tests.

    Usage:
        with MapServerHarness() as harness:
            resp = urllib.request.urlopen(f"{harness.url}/api/status")
            ...

    Or as a pytest fixture (see tests/e2e/conftest.py).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: Optional[int] = None,
    ):
        self.host = host
        self.port = port or _pick_ephemeral_port()
        self._server = None  # type: ignore[assignment]
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        # Lazy import: keeps test collection cheap when the harness
        # isn't used (e.g. during a `pytest tests/test_rf.py`).
        from utils.map_data_service import MapServer

        with self._lock:
            if self._server is not None:
                return
            self._server = MapServer(
                port=self.port,
                host=self.host,
                enable_message_listener=False,
                enable_websocket=False,
            )
            self._server.start_background()

    def stop(self) -> None:
        with self._lock:
            if self._server is None:
                return
            self._server.stop()
            self._server = None

    def __enter__(self) -> "MapServerHarness":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
