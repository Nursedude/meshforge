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

import json
import socket
import threading
import time
import urllib.error
import urllib.request
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

    def start(self, wait_ready: bool = True, timeout_s: float = 600.0) -> None:
        """Start the in-process MapServer.

        Args:
            wait_ready: When True (default), block until /healthz
                reports state="ready". Tests that want to assert
                warming-state behavior should pass wait_ready=False
                and check the state explicitly.
            timeout_s: Max seconds to wait for warming. The first
                collect can be slow (F3 cold-start latency); 10
                minutes is a generous ceiling for a Pi-class box.
        """
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

        if wait_ready:
            self.wait_ready(timeout_s=timeout_s)

    def wait_ready(self, timeout_s: float = 600.0, poll_s: float = 0.5) -> None:
        """Block until /healthz reports state="ready" or timeout.

        Raises TimeoutError if warming doesn't complete in time.
        Useful when tests need ready state but want to sleep/work
        between start() and the wait.
        """
        deadline = time.time() + timeout_s
        last_state = "<no response>"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"{self.url}/healthz", timeout=2
                ) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    last_state = data.get("state", "<missing>")
                    if last_state == "ready":
                        return
            except (urllib.error.URLError, OSError) as e:
                last_state = f"error: {e}"
            time.sleep(poll_s)
        raise TimeoutError(
            f"MapServer at {self.url} did not become ready in "
            f"{timeout_s}s; last /healthz state: {last_state}"
        )

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
