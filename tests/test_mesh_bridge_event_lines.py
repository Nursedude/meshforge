"""mesh_bridge names what it did with every text it heard (C21, 2026-10-19).

Before this, a duplicate drop only bumped `duplicates_suppressed` (no line at
all) and a receipt left no trace, so a run could prove a drop happened only
from a counter delta, never by naming the message. These lines are the
per-message record the C21 run reconciles against.
"""
import logging

import pytest


@pytest.fixture(autouse=True)
def _bridge_queues_in_tmp(tmp_path, monkeypatch):
    # never the operator's live ~/.config/meshforge/mesh_bridge_queues
    import gateway.mesh_bridge as mb
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)


@pytest.fixture
def bridge():
    from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig
    from gateway.mesh_bridge import MeshtasticPresetBridge
    cfg = GatewayConfig()
    cfg.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(preset="LONG_FAST", name="lf",
                                 connection_type="serial", serial_device="/dev/null"),
        secondary=MeshtasticConfig(preset="SHORT_TURBO", name="st",
                                   connection_type="serial", serial_device="/dev/null"),
    )
    return MeshtasticPresetBridge(config=cfg)


def _pkt(text, sender="!aaaa0001"):
    return {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": text.encode()},
            "fromId": sender, "toId": "^all", "to": 0xFFFFFFFF, "channel": 0}


def _lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("mesh_bridge ")]


def test_receipt_and_duplicate_drop_are_both_named(bridge, caplog):
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
    for _ in range(2):
        bridge._process_receive(_pkt("c21 r1 1 lf2st"), "primary", "secondary",
                                bridge._primary_to_secondary)
    assert _lines(caplog) == [
        "mesh_bridge rx leg=primary: c21 r1 1 lf2st",
        "mesh_bridge rx leg=primary: c21 r1 1 lf2st",
        "mesh_bridge drop leg=primary reason=duplicate: c21 r1 1 lf2st",
    ]


def test_already_bridged_drop_is_named(bridge, caplog):
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
    bridge._process_receive(_pkt("[Mesh:LAB] c21 r1 2 st2lf"), "secondary",
                            "primary", bridge._secondary_to_primary)
    assert _lines(caplog)[-1] == (
        "mesh_bridge drop leg=secondary reason=already_bridged: "
        "[Mesh:LAB] c21 r1 2 st2lf")


def test_forward_is_named_with_the_text_as_sent(bridge, caplog):
    from unittest.mock import MagicMock, patch
    from gateway.mesh_bridge import BridgedMeshMessage
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
    msg = BridgedMeshMessage(source_preset="LONG_FAST", source_id="!aaaa0001",
                             destination_id="^all", content="c21 r1 3 lf2st",
                             channel=0, is_broadcast=True)
    with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
        assert bridge._forward_message(msg, MagicMock(), True, "secondary")
    assert _lines(caplog)[-1] == (
        "mesh_bridge tx dest=secondary: [Mesh:LONG_FAST] c21 r1 3 lf2st")


def test_dual_path_suppression_is_a_named_drop_on_the_ingress_leg(bridge, caplog):
    from unittest.mock import patch
    from gateway.mesh_bridge import BridgedMeshMessage
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
    msg = BridgedMeshMessage(source_preset="SHORT_TURBO", source_id="!aaaa0002",
                             destination_id="^all", content="c21 r1 4 st2lf",
                             channel=0, is_broadcast=True)
    with patch.object(bridge, "_dual_path_dedup_on", return_value=True), \
         patch("gateway.mesh_bridge.get_rf_tx_registry") as reg:
        reg.return_value.seen_namespace_within.return_value = "text"
        assert bridge._send_to_primary(msg.to_payload()) is True
    assert _lines(caplog)[-1] == (
        "mesh_bridge drop leg=secondary reason=dual_path: c21 r1 4 st2lf")


def test_a_message_the_queue_gives_up_on_is_a_named_drop(bridge, caplog):
    # non-author review 2026-10-19 #3: dead-letter / expiry / shed left no line,
    # so a lost message read "unaccounted" — indistinguishable from a logging gap
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
    bridge._process_receive(_pkt("c21 r1 5 lf2st"), "primary", "secondary",
                            bridge._primary_to_secondary)
    q = bridge._primary_to_secondary
    (msg,) = q.get_pending()
    for _ in range(10):
        q.mark_failed(msg.id, "radio write failed")   # by id: ignores retry_after
    assert q.get_queue_depth() == 0
    assert _lines(caplog)[-1] == (
        "mesh_bridge drop leg=primary reason=dead_letter: c21 r1 5 lf2st")
