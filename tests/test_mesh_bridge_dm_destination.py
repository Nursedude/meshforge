"""BR-T #18 — a DM whose ``toId`` the library could not resolve killed the
bridge-loop thread silently.

Found by the 11-11 end-state review by running (landscape §B #18), pre-existing:
the meshtastic library reports ``toId`` as ``None`` when the destination number
is not in that radio's node DB. ``_process_receive`` stored ``destination_id =
None``; the forward then called ``sendText(destinationId=None)``, the library's
``our_exit`` raised ``SystemExit`` — which ``except Exception`` does not catch —
and the bridge-loop thread died. Status still read "running"; nothing crossed in
either direction and the drained queues stopped expiring.

The cure, three layers, each deciding on a WIRE fact (operator 09-18):
1. ingress: the DM's numeric ``to`` IS on the wire — the destination is
   ``!{to:08x}``; with neither ``toId`` nor a numeric ``to``, a named drop;
2. send boundary: a DM with no destination is refused, never handed over;
3. backstop: a library ``SystemExit`` inside a loop is witnessed, not fatal.
"""
import logging
from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Queues under tmp, never the live ~/.config/meshforge/mesh_bridge_queues."""
    import gateway.mesh_bridge as mb
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)


@pytest.fixture
def bridge():
    from gateway.mesh_bridge import MeshtasticPresetBridge
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.bridge_mode = "mesh_bridge"
    cfg.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(host="192.0.2.1", port=4403, preset="LONG_FAST"),   # TEST-NET-1
        secondary=MeshtasticConfig(host="192.0.2.2", port=4404, preset="SHORT_TURBO"),
        direction="bidirectional", add_prefix=True,
        prefix_format="[Mesh:{source_preset}] ",
    )
    return MeshtasticPresetBridge(config=cfg)


def _dm(text, to=0x1234ABCD, to_id=None):
    pkt = {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": text.encode()},
           "fromId": "!aaaa0001", "toId": to_id, "channel": 0}
    if to is not None:
        pkt["to"] = to
    return pkt


class _LibraryLikeRadio:
    """sendText behaves like meshtastic's: destinationId=None -> our_exit."""

    def __init__(self):
        self.sent = []

    def sendText(self, text, destinationId="^all", **kw):
        if destinationId is None:
            raise SystemExit("Warning: No destinationId")     # library our_exit
        self.sent.append((text, destinationId))
        return {"id": len(self.sent)}


def _lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("mesh_bridge ")]


class TestIngress:

    def test_unresolved_toId_takes_the_destination_from_the_wire(self, bridge):
        q = bridge._primary_to_secondary
        bridge._process_receive(_dm("dm one"), "primary", "secondary", q)
        rows = q.get_pending(destination="secondary")
        assert len(rows) == 1
        assert rows[0].payload["destination_id"] == "!1234abcd"
        assert rows[0].payload["is_broadcast"] is False

    def test_a_resolved_toId_is_kept(self, bridge):
        q = bridge._primary_to_secondary
        bridge._process_receive(_dm("dm two", to_id="!0000beef"), "primary", "secondary", q)
        assert q.get_pending(destination="secondary")[0].payload["destination_id"] == "!0000beef"

    def test_a_dm_with_no_destination_at_all_is_a_named_drop(self, bridge, caplog):
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        q = bridge._primary_to_secondary
        bridge._process_receive(_dm("dm nowhere", to=None), "primary", "secondary", q)
        assert q.get_queue_depth() == 0
        assert _lines(caplog)[-1] == (
            "mesh_bridge drop leg=primary reason=dm_no_destination: dm nowhere")
        assert bridge.stats.get("dm_no_destination") == 1


class TestSendBoundary:

    def test_a_persisted_dm_without_destination_is_refused_not_handed_over(self, bridge):
        """Rows queued before the ingress fix: never sendText(destinationId=None)."""
        from gateway.mesh_bridge import BridgedMeshMessage
        radio = _LibraryLikeRadio()
        bridge._secondary_interface, bridge._secondary_connected = radio, True
        msg = BridgedMeshMessage(source_preset="LONG_FAST", source_id="!aaaa0001",
                                 destination_id=None, content="old dm",
                                 channel=0, is_broadcast=False)
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            assert bridge._send_to_secondary(msg.to_payload()) is False
        assert radio.sent == []
        assert bridge.stats.get("dm_no_destination") == 1

    def test_the_wire_destination_reaches_the_radio(self, bridge):
        from gateway.mesh_bridge import BridgedMeshMessage
        radio = _LibraryLikeRadio()
        bridge._secondary_interface, bridge._secondary_connected = radio, True
        msg = BridgedMeshMessage(source_preset="LONG_FAST", source_id="!aaaa0001",
                                 destination_id="!1234abcd", content="dm ok",
                                 channel=0, is_broadcast=False)
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            assert bridge._send_to_secondary(msg.to_payload()) is True
        assert radio.sent[0][1] == "!1234abcd"


class TestBackstop:

    def test_a_library_systemexit_does_not_kill_the_bridge_loop(self, bridge, caplog):
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        bridge._running = True
        calls = []

        def once_exit(batch_size=5):
            calls.append(1)
            if len(calls) == 1:
                raise SystemExit("library our_exit")
            return 0

        waits = iter([False, True, True])
        with patch.object(bridge._stop_event, "wait", lambda *_: next(waits)), \
             patch.object(bridge._primary_to_secondary, "process_once", once_exit), \
             patch.object(bridge._secondary_to_primary, "process_once", return_value=0):
            bridge._bridge_loop()                         # must RETURN, not raise
        assert len(calls) == 2                            # the loop went round again
        assert bridge.stats.get("loop_fatal_caught") == 1
        assert any(r.levelno >= logging.ERROR and "SystemExit" in r.getMessage()
                   for r in caplog.records)

    def test_end_to_end_the_queue_keeps_moving_after_a_bad_dm(self, bridge):
        """The reviewer's shape: an unresolvable DM, then a broadcast and a
        reverse-direction message — all through the real process_once."""
        radio_s, radio_p = _LibraryLikeRadio(), _LibraryLikeRadio()
        bridge._secondary_interface, bridge._secondary_connected = radio_s, True
        bridge._primary_interface, bridge._primary_connected = radio_p, True
        bridge._process_receive(_dm("bad dm", to=None), "primary", "secondary",
                                bridge._primary_to_secondary)
        bridge._process_receive(_dm("wire dm"), "primary", "secondary",
                                bridge._primary_to_secondary)
        bcast = {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hello st"},
                 "fromId": "!bbbb0002", "toId": "^all", "to": 0xFFFFFFFF, "channel": 0}
        bridge._process_receive(bcast, "secondary", "primary",
                                bridge._secondary_to_primary)
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            bridge._primary_to_secondary.process_once(batch_size=5)
            bridge._secondary_to_primary.process_once(batch_size=5)
        assert [d for _, d in radio_s.sent] == ["!1234abcd"]
        assert len(radio_p.sent) == 1
