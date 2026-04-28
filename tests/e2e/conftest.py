"""E2E harness fixtures: mock meshtasticd HTTP capture + real-pipeline bridge."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tests.e2e.harness.map_server import MapServerHarness
from tests.e2e.harness.mock_meshtasticd import MockMeshtasticDaemon


@pytest.fixture
def mock_meshtasticd():
    daemon = MockMeshtasticDaemon()
    daemon.start()
    yield daemon
    daemon.stop()


@pytest.fixture(scope="module")
def map_server():
    """Real MapServer on an ephemeral loopback port.

    Module-scoped so the slow `_prewarm_collector()` runs once per
    file, not per test — the prewarm hits real network (MQTT, RNS,
    AREDN, MeshCore) and is the F3 cold-start latency we already know
    about. Tests should treat the server as immutable shared fixture.

    WebSocket + message listener are disabled so the fixture doesn't
    need rnsd / mosquitto / meshtasticd. Use this for HTTP-shape
    contract tests; for collector behavior assertions, prefer the
    unit tests in tests/test_map_data_collector_diagnostics.py.
    """
    harness = MapServerHarness()
    harness.start()
    yield harness
    harness.stop()


@pytest.fixture
def gateway_identity_dir(tmp_path):
    d = tmp_path / "gateway_identity"
    d.mkdir()
    return d


@pytest.fixture
def queue_db_path(tmp_path):
    return tmp_path / "queue.db"


@pytest.fixture(autouse=True)
def _restore_send_text_direct():
    """Snapshot+restore the meshtastic_protobuf_client.send_text_direct
    module attribute so the harness's ``tls=False`` monkey-patch in
    ``build_bridge_with_real_pipeline`` does not leak across tests.

    Autouse so every e2e test gets isolation even if it never touches
    the pipeline helper. Cheap when no patch happened (just a rebind
    to the same object)."""
    try:
        from gateway import meshtastic_protobuf_client as _mpc
    except ImportError:
        yield
        return
    original = _mpc.send_text_direct
    yield
    _mpc.send_text_direct = original


@pytest.fixture(autouse=True)
def _stop_bridges_between_tests():
    """Track bridges built via the e2e pipeline and stop their queue
    workers at test teardown. Without this, each test leaks a daemon
    thread that keeps polling the SQLite db — harmless for correctness
    but noisy in pytest output."""
    bridges: list = []
    try:
        from gateway import rns_bridge as _bridge_mod
        _orig_ctor = _bridge_mod.RNSMeshtasticBridge.__init__

        def _tracking_ctor(self, *a, **kw):
            _orig_ctor(self, *a, **kw)
            bridges.append(self)

        _bridge_mod.RNSMeshtasticBridge.__init__ = _tracking_ctor
    except ImportError:
        yield
        return
    try:
        yield
    finally:
        _bridge_mod.RNSMeshtasticBridge.__init__ = _orig_ctor
        for b in bridges:
            q = getattr(b, "_persistent_queue", None)
            if q is not None:
                try:
                    q.stop_processing()
                except Exception:
                    pass
