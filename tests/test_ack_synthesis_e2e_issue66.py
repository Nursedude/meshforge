"""
Issue #66 step 5: end-to-end integration tests for application-layer
ack synthesis.

Unlike the unit tests, which mock either the queue (test_rns_bridge.py)
or the bridge (test_message_queue.py), this file wires a REAL
PersistentMessageQueue to a mocked-but-realistic RNSMeshtasticBridge
and exercises the full software path:

  enqueue_message(ack_required=True)
    → register_pending_ack on the real queue
    → mark_acked on the real queue (via _maybe_emit_ack_for_msgid)
    → _emit_ack_to_origin → handler.send_text

Plus the periodic-sweep alternate path:

  enqueue_message(ack_required=True, ack_timeout_seconds=1)
    → register_pending_ack
    → wait past deadline
    → _sweep_overdue_acks
    → find_overdue_acks finds the row
    → mark_timeout finalises it
    → _emit_ack_to_origin → handler.send_text("[timeout: ...]")

Plus the race-safety + idempotency contracts that the substrate is
supposed to provide:

- Double LXMF on_delivered fires → only one synthetic ACK
- LXMF delivery wins → sweep finds nothing
- Sweep wins → late LXMF delivery no-ops

Run with: python3 -m pytest tests/test_ack_synthesis_e2e_issue66.py -v
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _mock_gateway_config_min():
    """Minimal config matching the unit test pattern in test_rns_bridge.py."""
    config = MagicMock()
    config.enabled = True
    config.auto_start = False
    config.bridge_mode = "message_bridge"
    config.default_route = "bidirectional"
    config.routing_rules = []
    config.log_level = "DEBUG"
    config.log_messages = True
    config.rns = MagicMock()
    config.rns.config_dir = None
    config.meshtastic = MagicMock()
    config.meshtastic.channel = 0
    config.meshtastic.connection_type = "tcp"
    config.meshtastic.host = "localhost"
    config.meshtastic.port = 4403
    config.meshtastic.failover_enabled = False
    config.meshtastic.load_balancer_enabled = False
    config.meshtastic.gateway_heartbeat_enabled = False
    return config


@pytest.fixture
def integrated_bridge(tmp_path):
    """
    RNSMeshtasticBridge with a REAL PersistentMessageQueue wired in.

    The radio handlers stay mocked (no real meshtastic/meshcore/rns I/O),
    but the queue, ack substrate, and synthesis helpers run for real.
    """
    with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
         patch("gateway.rns_bridge.UnifiedNodeTracker"), \
         patch("gateway.rns_bridge.BridgeHealthMonitor"), \
         patch("gateway.rns_bridge.DeliveryTracker"), \
         patch("gateway.rns_bridge.MeshtasticHandler") as MockHandler, \
         patch("gateway.rns_bridge.ReconnectStrategy") as MockReconnect, \
         patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
         patch("gateway.rns_bridge.CircuitBreakerRegistry", None), \
         patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", False), \
         patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
         patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
         patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
         patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

        mock_config = _mock_gateway_config_min()
        MockConfig.load.return_value = mock_config
        MockHandler.return_value = MagicMock(is_connected=False)
        MockReconnect.for_rns.return_value = MagicMock()

        from gateway.rns_bridge import RNSMeshtasticBridge
        b = RNSMeshtasticBridge(config=mock_config)

        # Bridge was built with HAS_PERSISTENT_QUEUE=False so _persistent_queue
        # is None. Replace with a REAL queue against a temp DB so the full
        # substrate (register_pending_ack, mark_acked, find_overdue_acks,
        # mark_timeout) runs for real in the integration path.
        from gateway.message_queue import PersistentMessageQueue
        b._persistent_queue = PersistentMessageQueue(
            db_path=str(tmp_path / "ack_e2e.db")
        )
        try:
            yield b
        finally:
            b._persistent_queue.stop_processing()


class TestAckSynthesisEndToEndDelivered:
    """The happy path: enqueue → callback fires → synthetic ACK arrives."""

    def test_meshtastic_origin_delivered_ack(self, integrated_bridge):
        """
        Meshtastic-origin message with ack_required=True flows through
        enqueue → register_pending_ack on the REAL queue; a simulated
        on_delivered callback → mark_acked on the REAL queue → synthetic
        ACK dispatched to the mesh handler with the right destination.
        """
        bridge = integrated_bridge
        # Wire a mocked mesh handler that records send_text calls.
        bridge._mesh_handler = MagicMock()
        bridge._mesh_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "weather pls",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="!senderaddr",
        )
        assert msg_id is not None
        # Queue state: pending (the registration is real)
        assert bridge._persistent_queue.get_ack_status(msg_id) == 'pending'

        # Simulate the LXMF on_delivered hook (this is the actual path
        # _register_lxmf_delivery_callbacks wires it through).
        emitted = bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        assert emitted is True
        # Synthetic ACK landed on the mesh handler with the right addressing
        bridge._mesh_handler.send_text.assert_called_once_with(
            f"[delivered: {msg_id[:8]}]",
            destination="!senderaddr",
            channel=0,
        )
        # Queue final state: acked (verifies mark_acked ran on the real DB)
        assert bridge._persistent_queue.get_ack_status(msg_id) == 'acked'

    def test_meshcore_origin_delivered_ack(self, integrated_bridge):
        """Same flow, meshcore origin → meshcore handler gets the ACK."""
        bridge = integrated_bridge
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "test",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshcore",
            ack_origin_address="abc-pubkey",
        )
        bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        bridge._meshcore_handler.send_text.assert_called_once_with(
            f"[delivered: {msg_id[:8]}]",
            destination="abc-pubkey",
        )

    def test_failed_ack_path(self, integrated_bridge):
        """on_failed → "[failed: ...]" ACK; queue marked 'acked' (the
        finalisation transitions, semantically the message lifecycle is
        done either way)."""
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()
        bridge._mesh_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "test",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="!senderaddr",
        )
        bridge._maybe_emit_ack_for_msgid(msg_id, kind='failed')

        bridge._mesh_handler.send_text.assert_called_once_with(
            f"[failed: {msg_id[:8]}]",
            destination="!senderaddr",
            channel=0,
        )
        assert bridge._persistent_queue.get_ack_status(msg_id) == 'acked'


class TestAckSynthesisEndToEndTimeout:
    """The sweep path: deadline passes → TIMEOUT ACK fires."""

    def test_timeout_emits_synthetic_ack(self, integrated_bridge):
        """
        Real queue + real sweep: a message with ack_timeout_seconds=1
        becomes overdue, find_overdue_acks surfaces it, mark_timeout
        finalises, _emit_ack_to_origin dispatches "[timeout: ...]".
        """
        bridge = integrated_bridge
        bridge._meshcore_handler = MagicMock()
        bridge._meshcore_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "test",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshcore",
            ack_origin_address="abc-pubkey",
            ack_timeout_seconds=1,
        )
        assert bridge._persistent_queue.get_ack_status(msg_id) == 'pending'

        # Wait past the deadline.
        time.sleep(1.2)

        emitted = bridge._sweep_overdue_acks()
        assert emitted == 1

        bridge._meshcore_handler.send_text.assert_called_once_with(
            f"[timeout: {msg_id[:8]}]",
            destination="abc-pubkey",
        )
        assert bridge._persistent_queue.get_ack_status(msg_id) == 'timeout'

    def test_sweep_finds_nothing_before_deadline(self, integrated_bridge):
        """A still-fresh pending record is invisible to the sweep."""
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()

        msg_id = bridge.enqueue_message(
            "test",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="!senderaddr",
            ack_timeout_seconds=300,  # 5 min
        )
        # Don't wait.
        emitted = bridge._sweep_overdue_acks()
        assert emitted == 0
        bridge._mesh_handler.send_text.assert_not_called()
        # Still pending.
        assert bridge._persistent_queue.get_ack_status(msg_id) == 'pending'

    def test_sweep_skips_non_ack_messages(self, integrated_bridge):
        """
        Messages enqueued WITHOUT ack_required=True don't show up in the
        sweep even if their lifecycle exceeds typical timeouts.

        Why pinned: if a future refactor accidentally drops the
        ack_required=1 predicate from find_overdue_acks, every queued
        message would generate a TIMEOUT ACK — operator chat thread
        flooded.
        """
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()

        msg_id = bridge.enqueue_message(
            "ordinary",
            destination="!aabbccdd",
            dest_type="meshtastic",
            # ack_required absent — default False
        )
        assert bridge._persistent_queue.get_ack_status(msg_id) is None

        emitted = bridge._sweep_overdue_acks()
        assert emitted == 0
        bridge._mesh_handler.send_text.assert_not_called()


class TestAckSynthesisIdempotency:
    """The substrate's idempotency promise: never double-emit."""

    def test_double_on_delivered_emits_once(self, integrated_bridge):
        """
        A duplicate LXMF on_delivered callback (which can happen if the
        wire-level ack arrives twice, or if two layers both fire) emits
        EXACTLY ONE synthetic ACK. mark_acked() returns None the second
        time, _maybe_emit_ack_for_msgid no-ops.
        """
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()
        bridge._mesh_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "test",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="!senderaddr",
        )
        first = bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')
        second = bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        assert first is True
        assert second is False
        assert bridge._mesh_handler.send_text.call_count == 1


class TestAckSynthesisRaceSafety:
    """The substrate's race-safety promise: ack-and-sweep don't double-fire."""

    def test_callback_wins_then_sweep_skips(self, integrated_bridge):
        """
        on_delivered fires BEFORE the timeout deadline expires. The
        sweep later finds nothing overdue (status='acked', not
        'pending') and emits zero TIMEOUT ACKs.
        """
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()
        bridge._mesh_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "test",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="!senderaddr",
            ack_timeout_seconds=1,
        )
        # Callback wins (still inside the deadline).
        bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        # Wait past the (now-irrelevant) deadline.
        time.sleep(1.2)
        emitted = bridge._sweep_overdue_acks()

        assert emitted == 0
        # Only ONE send_text — the delivered ACK from the callback.
        assert bridge._mesh_handler.send_text.call_count == 1
        call = bridge._mesh_handler.send_text.call_args
        assert call[0][0] == f"[delivered: {msg_id[:8]}]"

    def test_sweep_wins_then_late_callback_skips(self, integrated_bridge):
        """
        Sweep fires first (timeout deadline passed). Then a late LXMF
        on_delivered arrives. mark_acked() returns None because the row
        is already 'timeout'. No second ACK emitted.
        """
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()
        bridge._mesh_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "test",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="!senderaddr",
            ack_timeout_seconds=1,
        )
        # Sweep wins.
        time.sleep(1.2)
        bridge._sweep_overdue_acks()
        assert bridge._persistent_queue.get_ack_status(msg_id) == 'timeout'

        # Late callback arrives.
        emitted = bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        assert emitted is False
        # Only ONE send_text — the TIMEOUT from the sweep.
        assert bridge._mesh_handler.send_text.call_count == 1
        call = bridge._mesh_handler.send_text.call_args
        assert call[0][0] == f"[timeout: {msg_id[:8]}]"


class TestAckSynthesisOpts:
    """Opt-in contract: messages without ack_required=True do nothing."""

    def test_enqueue_without_ack_required_is_silent(self, integrated_bridge):
        """No ack params = no ack tracking. Callback path no-ops."""
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()

        msg_id = bridge.enqueue_message(
            "ordinary",
            destination="!aabbccdd",
            dest_type="meshtastic",
        )
        # Not tracked.
        assert bridge._persistent_queue.get_ack_status(msg_id) is None

        # Callback hook is a no-op for untracked messages.
        emitted = bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')
        assert emitted is False
        bridge._mesh_handler.send_text.assert_not_called()

    def test_enqueue_ack_required_without_origin_skips_tracking(
        self, integrated_bridge,
    ):
        """ack_required=True but missing origin_address → silent skip.
        Documents the contract: caller must pass BOTH origin fields."""
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()

        msg_id = bridge.enqueue_message(
            "test",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            # ack_origin_address missing
        )
        # No tracking established.
        assert bridge._persistent_queue.get_ack_status(msg_id) is None
        emitted = bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')
        assert emitted is False


class TestMeshtasticChannelPlaceholderDispatch:
    """Pattern audit (2026-05-19): the channel:<idx> placeholder that
    ``MeshtasticBroadcastBridge`` mints for source-less broadcasts must
    dispatch as a Meshtastic CHANNEL broadcast (destination=None,
    channel=idx), not as a DM with destination="channel:N". Mirrors the
    symmetric MeshCore branch — closes the asymmetric placeholder bug
    that would have produced an invalid destinationId on send_text.
    """

    def test_meshtastic_origin_channel_placeholder_dispatches_as_broadcast(
        self, integrated_bridge,
    ):
        """ack_origin_address="channel:2" → send_text called with
        destination=None, channel=2 (broadcast on slot 2). NEVER as
        destination="channel:2"."""
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()
        bridge._mesh_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "ping",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="channel:2",
        )
        emitted = bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        assert emitted is True
        bridge._mesh_handler.send_text.assert_called_once_with(
            f"[delivered: {msg_id[:8]}]",
            destination=None,
            channel=2,
        )

    def test_meshtastic_origin_dm_address_dispatches_as_dm(
        self, integrated_bridge,
    ):
        """Non-placeholder address (e.g., "!senderaddr") must still
        flow through the DM path with destination=address, channel=0.
        Regression guard against the placeholder-parsing change
        breaking the established DM contract."""
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()
        bridge._mesh_handler.send_text.return_value = True

        msg_id = bridge.enqueue_message(
            "ping",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="!senderaddr",
        )
        bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        bridge._mesh_handler.send_text.assert_called_once_with(
            f"[delivered: {msg_id[:8]}]",
            destination="!senderaddr",
            channel=0,
        )

    def test_meshtastic_origin_malformed_channel_placeholder_logs_and_drops(
        self, integrated_bridge, caplog,
    ):
        """A malformed placeholder ("channel:abc", "channel:") must
        log a warning and return False — NEVER fall through to a
        send with the literal string as destinationId."""
        import logging
        bridge = integrated_bridge
        bridge._mesh_handler = MagicMock()

        msg_id = bridge.enqueue_message(
            "ping",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshtastic",
            ack_origin_address="channel:not-an-int",
        )
        with caplog.at_level(logging.WARNING, logger='gateway.rns_bridge'):
            emitted = bridge._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        assert emitted is False
        bridge._mesh_handler.send_text.assert_not_called()
        assert any(
            "bad channel placeholder" in r.message for r in caplog.records
        )
