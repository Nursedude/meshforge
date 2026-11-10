"""BR-T #1 — a radio write the interface REFUSED is never booked as sent.

Found 2026-11-09 by a review by running (research/landscape_2026_11_09.md §B #1):
``_forward_message`` discarded ``sendText()``'s return value. ``MQTTMeshInterface``
signals a failed or refused write by returning ``False`` (HTTP send failed, text
over the on-air budget, protobuf client unavailable), so that leg's failures were
logged ``tx``, counted ``messages_*_to_*`` and removed from the queue as delivered.

The contract pinned here: ``False`` from the interface is a FAILED forward — no
``tx`` line, no delivered counter, ``errors`` bumped, and the caller sees ``False``
so the queue retries and then dead-letters under a name. Anything else (the
meshtastic library returns a packet object, older fakes return None) stays a
success: only the interface's own declared failure value is read as failure.
"""
import logging
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig


@pytest.fixture
def bridge(tmp_path):
    from gateway.mesh_bridge import MeshtasticPresetBridge
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.bridge_mode = "mesh_bridge"
    cfg.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(host="primary.invalid", port=4403, preset="LONG_FAST"),
        secondary=MeshtasticConfig(host="secondary.invalid", port=4404, preset="SHORT_TURBO"),
        direction="bidirectional",
        add_prefix=True,
        prefix_format="[Mesh:{source_preset}] ",
    )
    with patch("gateway.mesh_bridge.get_real_user_home", return_value=tmp_path):
        yield MeshtasticPresetBridge(config=cfg)


def _msg(**kw):
    from gateway.mesh_bridge import BridgedMeshMessage
    base = dict(source_preset="SHORT_TURBO", source_id="!b03bb70c",
                destination_id="^all", content="hello from ST",
                channel=0, is_broadcast=True)
    base.update(kw)
    return BridgedMeshMessage(**base)


def _tx_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == "gateway.mesh_bridge"
            and r.getMessage().startswith("mesh_bridge tx ")]


def _refused_lines(caplog):
    """The ERROR line is the only operator-visible witness of a refusal
    (11-10 non-author review: a mutant that deleted it survived)."""
    return [r.getMessage() for r in caplog.records
            if r.name == "gateway.mesh_bridge" and r.levelno == logging.ERROR
            and "REFUSED" in r.getMessage()]


class TestRefusedWriteIsNotSent:

    def test_false_from_the_interface_is_a_failed_forward(self, bridge, caplog):
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        iface = MagicMock()
        iface.sendText.return_value = False        # the MQTT leg's failure signal
        errors_before = bridge.stats["errors"]
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            assert bridge._forward_message(_msg(), iface, True, "primary") is False
        assert iface.sendText.call_count == 1      # it was attempted, and refused
        assert _tx_lines(caplog) == []             # no "tx" line for a write that failed
        assert len(_refused_lines(caplog)) == 1    # and the operator is told
        assert bridge.stats["errors"] == errors_before + 1

    def test_refused_dm_is_a_failed_forward_too(self, bridge, caplog):
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        iface = MagicMock()
        iface.sendText.return_value = False
        errors_before = bridge.stats["errors"]
        msg = _msg(destination_id="!0b0b0b0b", is_broadcast=False, content="dm")
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            assert bridge._forward_message(msg, iface, True, "secondary") is False
        assert _tx_lines(caplog) == []
        assert len(_refused_lines(caplog)) == 1
        assert bridge.stats["errors"] == errors_before + 1

    def test_refused_write_is_not_counted_delivered_to_secondary(self, bridge):
        """The other direction's counter (11-10 review: unpinned)."""
        iface = MagicMock()
        iface.sendText.return_value = False
        bridge._secondary_interface = iface
        bridge._secondary_connected = True
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            assert bridge._send_to_secondary(
                _msg(source_preset="LONG_FAST").to_payload()) is False
        assert bridge.stats["messages_primary_to_secondary"] == 0

    def test_refused_write_is_not_counted_delivered(self, bridge):
        """End of the consumer path: the per-direction counter and the caller's verdict."""
        iface = MagicMock()
        iface.sendText.return_value = False
        bridge._primary_interface = iface
        bridge._primary_connected = True
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            assert bridge._send_to_primary(_msg().to_payload()) is False
        assert bridge.stats["messages_secondary_to_primary"] == 0

    def test_real_mqtt_leg_oversize_refusal_reaches_the_counter(self, bridge, caplog):
        """Consumer path with the REAL interface (matrix F17b): a legal 230-byte
        broadcast grows past the on-air limit once the bridge tag is added, the
        real ``MQTTMeshInterface.sendText`` refuses it before any I/O, and the
        bridge must say so — not count it delivered."""
        import threading
        from gateway._mesh_bridge_mqtt import MQTTMeshInterface
        from gateway.base_handler import mesh_onair_limit
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        iface = MQTTMeshInterface(bridge.bridge_config.primary, "primary",
                                  lambda *_a, **_k: None, threading.Event())
        text = "x" * 230
        assert len(text) <= mesh_onair_limit(None)   # legal before the tag is added
        bridge._primary_interface = iface
        bridge._primary_connected = True
        # tx_guard stays out of this test by a patch, NEVER by allow_targets():
        # the leg resolves to the local radio port, and allowing it would key a
        # real radio if the refusal ever stopped firing. Both send paths are
        # stubbed instead, and must not be reached.
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch("gateway.meshtastic_protobuf_client.send_text_direct") as direct, \
             patch("gateway._mesh_bridge_mqtt._get_protobuf_client") as session:
            assert bridge._send_to_primary(_msg(content=text).to_payload()) is False
        assert direct.call_count == 0 and session.call_count == 0   # refused before any I/O
        assert bridge.stats["messages_secondary_to_primary"] == 0
        assert _tx_lines(caplog) == []

    @pytest.mark.parametrize("ret", [True, None, object()])
    def test_any_other_return_is_still_a_success(self, bridge, caplog, ret):
        """Control: the fix must not turn a healthy leg red. The meshtastic library
        returns a packet object; only the declared failure value ``False`` fails."""
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        iface = MagicMock()
        iface.sendText.return_value = ret
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            assert bridge._forward_message(_msg(), iface, True, "primary") is True
        assert len(_tx_lines(caplog)) == 1
        assert _refused_lines(caplog) == []
