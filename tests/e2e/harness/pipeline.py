"""Build a real bridge pipeline (no RNS mocks) connected to a mock daemon.

The aim: exercise bridge → persistent queue → handler dispatch → HTTP
PUT with NO mocking below the LXMF receive boundary. Tests inject a
BridgedMessage at the bridge's processing entry and assert on what the
mock meshtasticd received over HTTP. This is the integration layer
Issue #40 lived in.

What's mocked (necessarily, because we're not running real RNS):
  - GatewayConfig (built by `make_e2e_config`)
  - RNS subsystem flags (HAS_RNS, _HAS_LXMF) — patched to True/False as
    needed so the bridge code paths run without an actual RNS instance

What's REAL:
  - PersistentMessageQueue (SQLite, tmpdir-backed)
  - MQTTBridgeHandler (with send_text path overridden to the mock URL)
  - The bridge's own _process_rns_to_mesh, enqueue, dispatch
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def make_e2e_config(
    *,
    gateway_identity_dir: Path,
    queue_db_path: Path,
    meshtasticd_url: str,
    bridge_mode: str = "mqtt_bridge",
):
    """Build a GatewayConfig pointing at the mock daemon and a real tmpdir queue.

    TODO: real implementation. Should mirror gateway.config.GatewayConfig
    field-by-field — easiest is to import it and construct, overriding
    only what the test needs.
    """
    raise NotImplementedError("pipeline.make_e2e_config: see module TODO")


def build_bridge_with_real_pipeline(*, config, mock_daemon):
    """Construct an RNSMeshtasticBridge with real queue + real handler.

    TODO: real implementation. Pattern:
      - patch HAS_PERSISTENT_QUEUE=True so the real queue is wired
      - patch the meshtasticd HTTP client base URL to mock_daemon.url
      - patch RNS/LXMF init to a no-op (we inject BridgedMessage directly)
      - return the started bridge
    """
    raise NotImplementedError("pipeline.build_bridge_with_real_pipeline: see module TODO")


def wait_for_queue_idle(bridge, timeout: float = 2.0) -> None:
    """Block until the bridge's persistent queue has no in-flight messages.

    TODO: poll bridge._persistent_queue stats until pending==0 or timeout.
    """
    raise NotImplementedError("pipeline.wait_for_queue_idle: see module TODO")
