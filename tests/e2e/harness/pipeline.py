"""Build a real bridge pipeline (no RNS mocks) connected to a mock daemon.

The aim: exercise bridge → persistent queue → handler dispatch → HTTP
PUT with NO mocking below the LXMF receive boundary. Tests inject a
BridgedMessage at the bridge's processing entry and assert on what the
mock meshtasticd received over HTTP. This is the integration layer
Issue #40 lived in.

What's mocked (necessarily, because we're not running real RNS):
  - The RNS/LXMF import flags — we never call ``bridge.start()``, so
    the RNS thread never runs. We only invoke ``_process_rns_to_mesh``
    directly with pre-built ``BridgedMessage`` objects.

What's REAL:
  - PersistentMessageQueue (SQLite, tmpdir-backed)
  - MQTTBridgeHandler (constructed but not ``run_loop``'d — no MQTT
    broker required; we only exercise ``queue_send`` → ``send_text`` →
    HTTP PUT)
  - The bridge's own _process_rns_to_mesh, enqueue, dispatch thread
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

# Teardown callables registered by ``build_bridge_with_real_pipeline`` (RF
# egress allowlist + fallback neutralization). Drained by the
# ``_drain_e2e_pipeline_patches`` autouse fixture in tests/conftest.py (top
# level, keyed on this module being imported — so ANY caller anywhere gets
# drained, not only tests under tests/e2e/), plus a session-end sweep for
# higher-scoped leftovers. Registration rules (2026-08-09 review finding):
# each restore is a SEPARATE entry, appended IMMEDIATELY after its patch is
# applied — a bundled multi-restore lambda aborts the rest on the first
# failure, and any gap between patch and registration is a window where an
# exception strands the patch process-wide with no registered undo.
_E2E_TEARDOWN: list = []


def make_e2e_config(
    *,
    gateway_identity_dir: Path,
    queue_db_path: Path,
    meshtasticd_url: str,
    bridge_mode: str = "mqtt_bridge",
):
    """Build a GatewayConfig pointing at the mock daemon and a real tmpdir queue.

    The queue db path is stashed on the returned config so
    ``build_bridge_with_real_pipeline`` can wire it into
    ``PersistentMessageQueue`` at construction time (the bridge builds
    its own queue with default paths; we patch once briefly to
    redirect it).
    """
    from gateway.config import GatewayConfig, MeshtasticConfig, RNSConfig

    parsed = urlparse(meshtasticd_url)
    cfg = GatewayConfig(
        enabled=True,
        bridge_mode=bridge_mode,
        meshtastic=MeshtasticConfig(
            host=parsed.hostname or "127.0.0.1",
            http_port=parsed.port or 443,
            channel=0,
        ),
        rns=RNSConfig(),
    )
    cfg._test_queue_db_path = str(queue_db_path)
    cfg._test_identity_dir = str(gateway_identity_dir)
    return cfg


def build_bridge_with_real_pipeline(*, config, mock_daemon):
    """Construct an RNSMeshtasticBridge with a real persistent queue and
    an HTTP-talking MQTTBridgeHandler pointed at the mock daemon.

    Does NOT call ``bridge.start()`` — the RNS/LXMF thread never runs.
    Tests inject messages via ``bridge._process_rns_to_mesh`` directly
    and the queue worker dispatches them to the mock daemon.

    Side effect: monkey-patches
    ``gateway.meshtastic_protobuf_client.send_text_direct`` to force
    ``tls=False`` so the HTTP POST reaches the plain-HTTP mock. The
    ``_restore_send_text_direct`` autouse fixture in
    ``tests/e2e/conftest.py`` reverts this per-test.
    """
    from gateway import meshtastic_protobuf_client as _mpc
    from gateway import mqtt_bridge_handler as _mqtt_handler_mod
    from gateway import rns_bridge as _bridge_mod
    from gateway.message_queue import PersistentMessageQueue
    from utils import tx_guard

    _orig_send = _mpc.send_text_direct
    _orig_send_id = _mpc.send_text_direct_with_id

    def _http_send_text_direct(*args, **kwargs):
        kwargs["tls"] = False
        return _orig_send(*args, **kwargs)

    def _http_send_text_direct_with_id(*args, **kwargs):
        kwargs["tls"] = False
        return _orig_send_id(*args, **kwargs)

    # The bridge's HTTP TX path now calls send_text_direct_with_id (returns the
    # packet_id for ACK correlation); patch both so the plain-HTTP mock is hit.
    # Registered here as well as covered by the _restore_send_text_direct
    # fixture in tests/e2e/conftest.py — that fixture only exists for tests
    # under tests/e2e/, and this patch must not outlive any other caller.
    _mpc.send_text_direct = _http_send_text_direct
    _E2E_TEARDOWN.append(lambda: setattr(_mpc, "send_text_direct", _orig_send))
    _mpc.send_text_direct_with_id = _http_send_text_direct_with_id
    _E2E_TEARDOWN.append(
        lambda: setattr(_mpc, "send_text_direct_with_id", _orig_send_id))

    db_path = getattr(config, "_test_queue_db_path", None)
    if db_path is None:
        raise ValueError(
            "make_e2e_config must be called before build_bridge_with_real_pipeline — "
            "config is missing _test_queue_db_path"
        )

    # Allowlist the mock daemon for the RF egress guard. Its port is ephemeral,
    # so this has to happen in-process at runtime rather than via env.
    #
    # ⚠️ Only the MOCK is allowlisted. The handler's fallback chain
    # (session protobuf client -> localhost:9443, then
    # `meshtastic --host X --sendtext` -> TCP 4403) does NOT consult the
    # mock's address, and on 2026-08-09 it keyed the operator's real radio
    # onto a live statewide channel when a test stopped the mock to force
    # failures. Those hops stay BLOCKED here on purpose.
    #
    # The fallbacks are additionally neutralized below, because what these
    # tests simulate is "the daemon is down" — and a down daemon means every
    # path fails, not that TX silently escalates to a different radio. The
    # guard blocking them is proven separately and deliberately in
    # tests/test_tx_guard.py::TestRealFallbackChainIsBlocked, which is the
    # regression drill for this incident.
    _tx_allow = tx_guard.allow_targets(
        f"{config.meshtastic.host}:{config.meshtastic.http_port}"
    )
    _tx_allow.__enter__()
    _E2E_TEARDOWN.append(lambda: _tx_allow.__exit__(None, None, None))

    _orig_cli = _mqtt_handler_mod.MQTTBridgeHandler._send_via_cli
    _mqtt_handler_mod.MQTTBridgeHandler._send_via_cli = (
        lambda self, *a, **kw: False
    )
    _E2E_TEARDOWN.append(lambda: setattr(
        _mqtt_handler_mod.MQTTBridgeHandler, "_send_via_cli", _orig_cli))

    _orig_has_pb_client = _mqtt_handler_mod._HAS_PROTOBUF_CLIENT
    _mqtt_handler_mod._HAS_PROTOBUF_CLIENT = False
    _E2E_TEARDOWN.append(lambda: setattr(
        _mqtt_handler_mod, "_HAS_PROTOBUF_CLIENT", _orig_has_pb_client))

    _orig_pmq_init = PersistentMessageQueue.__init__

    def _patched_pmq_init(self, *args, **kwargs):
        kwargs["db_path"] = db_path
        _orig_pmq_init(self, *args, **kwargs)

    PersistentMessageQueue.__init__ = _patched_pmq_init
    try:
        bridge = _bridge_mod.RNSMeshtasticBridge(config=config)
    finally:
        PersistentMessageQueue.__init__ = _orig_pmq_init

    if bridge._persistent_queue:
        bridge._persistent_queue.start_processing(interval=0.05)

    return bridge


def wait_for_queue_idle(bridge, timeout: float = 2.0) -> None:
    """Block until the bridge's persistent queue has no in-flight messages.

    Polls ``get_stats()`` for ``pending + in_progress == 0``. Returns
    early on success; returns at ``timeout`` even if the queue is still
    busy (tests that want strict idleness should assert on stats after).
    """
    q = getattr(bridge, "_persistent_queue", None)
    if q is None:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = q.get_stats()
        if stats.get("pending", 0) == 0 and stats.get("in_progress", 0) == 0:
            return
        time.sleep(0.05)
