"""A sender the radio's node DB lacks showed as ``????`` in the bridge tag.

Found live 2026-11-17 09:39 (inbox downlink-gap drill): the operator sent
"Cmd" from ``!a2e95ba4`` on SHORT_TURBO and moc's preset bridge carried it as
``[Mesh:SHORT_TURBO:????] Cmd``. moc runs meshtastic 2.7.11, whose
``_nodeNumToId`` returns ``None`` for any node not in ``nodesByNum``, so the
packet's ``fromId`` was ``None``. The bridge used that string as the origin, so
the tag fell back to ``????`` and the dedup/loop content_id saw "no origin".

The sender's number IS on the wire (``from``). Same rule as ``dm_destination``
(BR-T #18): the numeric field decides alone when present; ``fromId`` is a
node-DB string a neighbour can set (``user.id``), so it is a fallback only when
``from`` is absent, and only as ``'!'`` + 8 hex. ONE helper, used by both mesh
ingress paths (preset bridge and Mesh→RNS handler), because both feed
``mesh_origin_content_id`` and the dedup key must agree between them.
"""
import threading
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig

SENDER = 0xA2E95BA4


class TestPacketOrigin:

    @pytest.mark.parametrize("packet,expected", [
        ({"from": SENDER, "fromId": None}, "!a2e95ba4"),           # the live bug
        ({"from": SENDER}, "!a2e95ba4"),
        ({"from": SENDER, "fromId": "!a2e95ba4"}, "!a2e95ba4"),
        ({"from": SENDER, "fromId": "!0000beef"}, "!a2e95ba4"),    # wire wins
        ({"from": 0x00000ABC, "fromId": None}, "!00000abc"),       # zero-padded
        ({"from": 0, "fromId": "!0000beef"}, None),                # wire present, invalid
        ({"from": 0xFFFFFFFF, "fromId": "Unknown"}, None),         # broadcast is no sender
        ({"from": -1}, None),
        ({"from": 0x1FFFFFFFF}, None),                             # > 32 bit
        ({"from": True}, None),                                    # bool is not a node
        ({"from": "a2e95ba4"}, None),                              # non-int wire value
        ({"fromId": "!0000beef"}, "!0000beef"),                    # no wire: 8-hex fallback
        ({"fromId": "!0000BEEF"}, "!0000beef"),
        ({"fromId": "!abc"}, None),
        ({"fromId": "Unknown"}, None),
        ({"fromId": None}, None),
        ({}, None),
    ])
    def test_the_wire_number_decides(self, packet, expected):
        from gateway.wire_ids import packet_origin
        assert packet_origin(packet) == expected

    def test_dm_destination_and_origin_are_one_rule(self):
        """r1 review: packet_origin was a field-for-field copy of
        dm_destination; both now wrap one reader, so they cannot drift."""
        from gateway.wire_ids import dm_destination, packet_origin
        for num in (SENDER, 0, 0xFFFFFFFF, -1, True, 0x1FFFFFFFF, "x"):
            for sid in (None, "!0000beef", "!abc"):
                assert packet_origin({"from": num, "fromId": sid}) == \
                    dm_destination({"to": num, "toId": sid})
        for sid in (None, "!0000beef", "!0000BEEF", "!abc", "Unknown"):
            assert packet_origin({"fromId": sid}) == dm_destination({"toId": sid})

    def test_the_legacy_import_path_still_works(self):
        from gateway import _mesh_bridge_legs, wire_ids
        assert _mesh_bridge_legs.packet_origin is wire_ids.packet_origin
        assert _mesh_bridge_legs.dm_destination is wire_ids.dm_destination
        assert _mesh_bridge_legs.node_dest is wire_ids.node_dest


# ── preset bridge (the path that minted "[Mesh:SHORT_TURBO:????]") ───────────

@pytest.fixture
def bridge(tmp_path, monkeypatch):
    import gateway.mesh_bridge as mb
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.bridge_mode = "mesh_bridge"
    cfg.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(host="192.0.2.1", port=4403, preset="LONG_FAST"),   # TEST-NET-1
        secondary=MeshtasticConfig(host="192.0.2.2", port=4404, preset="SHORT_TURBO"),
        direction="bidirectional", add_prefix=True,
        prefix_format="[Mesh:{source_preset}:{source_id}] ",   # moc's live shape
    )
    return mb.MeshtasticPresetBridge(config=cfg)


class _Radio:
    def __init__(self):
        self.sent = []

    def sendText(self, text, destinationId="^all", **kw):
        self.sent.append((text, destinationId))
        return {"id": len(self.sent)}


def _unknown_sender_broadcast(text="Cmd"):
    return {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": text.encode()},
            "from": SENDER, "fromId": None,                 # lib 2.7.11, node not in DB
            "to": 0xFFFFFFFF, "toId": "^all", "channel": 0}


class TestPresetBridge:

    def test_an_unknown_sender_is_named_from_the_wire_in_the_queue(self, bridge):
        q = bridge._secondary_to_primary
        bridge._process_receive(_unknown_sender_broadcast(), "secondary", "primary", q)
        rows = q.get_pending(destination="primary")
        assert len(rows) == 1
        assert rows[0].payload["source_id"] == "!a2e95ba4"

    def test_the_tag_on_air_names_the_sender_not_qmarks(self, bridge):
        """END of the path: the text the primary radio is handed."""
        radio = _Radio()
        bridge._primary_interface, bridge._primary_connected = radio, True
        bridge._process_receive(_unknown_sender_broadcast(), "secondary", "primary",
                                bridge._secondary_to_primary)
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
            bridge._secondary_to_primary.process_once(batch_size=5)
        assert [t for t, _ in radio.sent] == ["[Mesh:SHORT_TURBO:5ba4] Cmd"]


class TestPresetBridgeMqttLeg:
    """r1 review HIGH: the preset bridge's MQTT leg synthesizes a packet with
    ``fromId`` only. Fed the leg's REAL ``_on_message`` → the bridge, so the
    test exercises the dict that leg builds, not a hand-written one."""

    @staticmethod
    def _json(**kw):
        import json
        d = {"type": "text", "sender": "!aabb0042", "to": 0xFFFFFFFF,
             "channel": 0, "payload": {"text": "via mqtt"}}
        d.update(kw)
        m = MagicMock()
        m.topic = "msh/2/json/meshforge/!aabb0042"
        m.payload = json.dumps(d).encode("utf-8")
        return m

    def _source_id_through_leg(self, bridge, msg):
        from gateway.mesh_bridge import MQTTMeshInterface
        q = bridge._secondary_to_primary
        leg = MQTTMeshInterface(
            config=MeshtasticConfig(use_mqtt=True, mqtt_channel="meshforge"),
            name="secondary", stop_event=threading.Event(),
            message_callback=lambda p: bridge._process_receive(
                p, "secondary", "primary", q))
        leg._on_message(None, None, msg)
        rows = q.get_pending(destination="primary")
        assert len(rows) == 1
        return rows[0].payload["source_id"]

    @pytest.mark.parametrize("extra,expected", [
        ({"from": SENDER}, "!a2e95ba4"),           # the originator
        ({"from": 0}, "!aabb0042"),                # unusable from → sender kept
        ({}, "!aabb0042"),                         # no from → sender kept
        ({"from": 0xFFFFFFFF}, "!aabb0042"),       # broadcast is no sender
        ({"from": "a2e95ba4"}, "!aabb0042"),       # non-int from
    ])
    def test_the_mqtt_leg_keeps_an_origin(self, bridge, extra, expected):
        assert self._source_id_through_leg(bridge, self._json(**extra)) == expected


class TestMqttBridgeHandlerReader:
    """The MQTT bridge handler reads `from` with the same rule (fallback:
    its sender, then the shared UNKNOWN_ORIGIN sentinel)."""

    @pytest.mark.parametrize("from_num,expected", [
        (SENDER, "!a2e95ba4"),
        (0xFFFFFFFF, "!aabb0042"),      # was '!ffffffff' (broadcast as a sender)
        (0x1A2E95BA4, "!aabb0042"),     # was masked to '!a2e95ba4' (a guess)
        (0, "!aabb0042"),
    ])
    def test_origin_rule(self, from_num, expected):
        from gateway.wire_ids import mqtt_json_origin
        assert mqtt_json_origin({"from": from_num, "sender": "!aabb0042"}) == expected

    @pytest.mark.parametrize("from_num,expected", [
        (SENDER, "!a2e95ba4"),
        (0xFFFFFFFF, "!aabb0042"),      # was '!ffffffff'
        (-1, "!aabb0042"),              # was '!-0000001'
    ])
    def test_node_tracker_keying_uses_the_same_rule(self, from_num, expected):
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        assert MQTTBridgeHandler._originator_id(
            {"from": from_num, "sender": "!aabb0042"}) == expected

    @staticmethod
    def _handler_source_id(data):
        """Drive the handler's REAL _bridge_text_message (r2 LOW-2: the call
        site was unpinned — only the helper was tested)."""
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h.config = MagicMock()
        h.config.meshtastic.gateway_node_id = ""
        h._message_queue = MagicMock()
        h._stats_lock = threading.Lock()
        h.stats = {"errors": 0}
        h._should_bridge = None
        h._message_callback = None
        h._is_duplicate = MagicMock(return_value=False)
        h._bridge_text_message(dict(data, to=0xFFFFFFFF, channel=0,
                                    payload={"text": "x"}),
                               topic="msh/US/2/json/meshforge/!aabb0042")
        return h._message_queue.put.call_args[0][0].source_id

    def test_the_handler_call_site_uses_the_rule(self):
        assert self._handler_source_id(
            {"from": 0xFFFFFFFF, "sender": "!aabb0042"}) == "!aabb0042"
        assert self._handler_source_id(
            {"from": SENDER, "sender": "!aabb0042"}) == "!a2e95ba4"

    @pytest.mark.parametrize("sender", [
        "!aabb0042", "!AABB0042", "aabb0042", "gw-1", "!ffffffff", "",
    ])
    def test_leg_and_handler_agree_on_the_fallback(self, bridge, sender):
        """r2 LOW-1: with `from` unusable, the bridge's MQTT leg and the MQTT
        handler must derive the SAME dedup key from the same JSON (old code
        agreed by both taking `sender` raw; r1 made the leg strict only)."""
        from gateway.base_handler import mesh_origin_content_id
        leg_sid = TestPresetBridgeMqttLeg()._source_id_through_leg(
            bridge, TestPresetBridgeMqttLeg._json(sender=sender))
        h_sid = self._handler_source_id({"sender": sender})
        assert mesh_origin_content_id(leg_sid, "x") == \
            mesh_origin_content_id(h_sid, "x")

    def test_the_handler_call_site_keeps_the_sentinel(self):
        """r3 LOW-3 (mutant M9 survived): the UNKNOWN_ORIGIN default at the
        handler's call site, not only on the helper."""
        from gateway.base_handler import UNKNOWN_ORIGIN
        assert self._handler_source_id({"sender": "gw-1"}) == UNKNOWN_ORIGIN

    @staticmethod
    def _nodeinfo_calls(from_value):
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h.node_tracker = MagicMock()
        data = {"type": "nodeinfo", "sender": "!aabb0042",
                "payload": {"longname": "Evil Twin", "shortname": "EVIL"}}
        if from_value is not None:
            data["from"] = from_value
        h._update_nodeinfo(data)
        return [(n.id, n.name_is_self_reported, n.short_name)
                for (n,), _ in h.node_tracker.add_node.call_args_list]

    @pytest.mark.parametrize("bad_from", [0xFFFFFFFF, 0x1A2E95BA4, -1, "junk", True])
    def test_a_malformed_from_is_never_a_self_report(self, bad_from):
        """r3 MED: the self-report gate tested `from` for truthiness while the
        node id fell back to the uplinker — a malformed `from` renamed the
        UPLINKER with a self-reported name and @address. The sender fallback
        is the uplinker's word, never a self-report."""
        # r4: not even a non-self-reported NAME lands on the uplinker — a
        # nodeinfo with no valid wire `from` names nobody.
        assert self._nodeinfo_calls(bad_from) == [], bad_from

    def test_a_valid_from_is_still_a_self_report(self):
        assert self._nodeinfo_calls(SENDER) == [("!a2e95ba4", True, "EVIL")]

    def test_an_absent_from_keeps_the_pinned_sender_fallback(self):
        """Unchanged contract (test_mqtt_bridge_handler pins it): absent
        `from` → the sender, never self-reported. Queued: should it exist?"""
        assert self._nodeinfo_calls(None) == [("!aabb0042", False, "")]

    def test_the_live_feed_names_the_originator(self):
        """r4 (mutant M11 survived): the TUI live-feed event named the raw
        last-hop `sender` while queue + store named the originator."""
        with patch("utils.event_bus.emit_message") as emit:
            self._handler_source_id({"from": SENDER, "sender": "!aabb0042"})
        assert emit.call_args.kwargs["node_id"] == "!a2e95ba4"

    @pytest.mark.parametrize("from_value,updates", [
        (SENDER, 1), (0xFFFFFFFF, 0), (-1, 0), ("junk", 0), (True, 0), (0, 0),
    ])
    def test_the_node_update_gate_is_the_wire_rule(self, from_value, updates):
        """r3 MED (same class, :679): only a valid wire originator triggers a
        node-tracker update from an MQTT json packet."""
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        import json as _json
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h._counts_as_bridge_uplink = lambda topic: False
        h._is_duplicate = MagicMock(return_value=False)
        h._update_node_from_mqtt = MagicMock()
        h._update_position = MagicMock()
        h._handle_json_message("msh/2/json/x/!aabb0042", _json.dumps(
            {"type": "position", "id": 1, "from": from_value,
             "sender": "!aabb0042", "payload": {}}).encode())
        assert h._update_node_from_mqtt.call_count == updates

    def test_no_from_no_sender_is_the_shared_sentinel(self):
        from gateway.base_handler import UNKNOWN_ORIGIN
        from gateway.wire_ids import mqtt_json_origin
        assert mqtt_json_origin({}, UNKNOWN_ORIGIN) == UNKNOWN_ORIGIN


# ── Mesh→RNS handler (same ingress fact, same dedup key) ─────────────────────

@pytest.fixture
def handler():
    from gateway.meshtastic_handler import MeshtasticHandler
    cfg = GatewayConfig()
    cfg.meshtastic = MeshtasticConfig(host="127.0.0.1", port=4403)
    tracker = MagicMock()
    tracker.get_meshtastic_nodes.return_value = []
    h = MeshtasticHandler(
        config=cfg, node_tracker=tracker, health=MagicMock(),
        stop_event=threading.Event(),
        stats={'errors': 0, 'messages_received': 0, 'messages_sent': 0},
        stats_lock=threading.Lock(), message_queue=Queue(maxsize=100),
    )
    h._connected = True
    yield h
    h.disconnect()


class TestMeshToRnsHandler:

    def test_an_unknown_sender_reaches_rns_with_its_wire_id(self, handler):
        with patch.dict('sys.modules', {'commands': MagicMock(),
                                        'commands.messaging': MagicMock()}):
            handler._on_receive(_unknown_sender_broadcast("hi rns"))
        msg = handler._message_queue.get_nowait()
        assert msg.content == "hi rns"
        assert msg.source_id == "!a2e95ba4"

    def test_an_unknown_sender_is_tracked_like_a_known_one(self, handler):
        """r1 review: unknown senders now reach node_tracker (as a known node
        always did) — pinned, with the number taken from the wire."""
        with patch.dict('sys.modules', {'commands': MagicMock(),
                                        'commands.messaging': MagicMock()}):
            handler._on_receive(_unknown_sender_broadcast("track me"))
        node = handler.node_tracker.add_node.call_args[0][0]
        assert node.meshtastic_id == "!a2e95ba4"

    def test_a_spoofed_fromId_does_not_rename_the_wire_sender(self, handler):
        pkt = _unknown_sender_broadcast("spoof")
        pkt["fromId"] = "!0000beef"            # neighbour-set user.id
        with patch.dict('sys.modules', {'commands': MagicMock(),
                                        'commands.messaging': MagicMock()}):
            handler._on_receive(pkt)
        assert handler._message_queue.get_nowait().source_id == "!a2e95ba4"
