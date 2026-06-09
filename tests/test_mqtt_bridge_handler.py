"""
Tests for gateway.mqtt_bridge_handler.MQTTBridgeHandler.

Focus: self-echo filter for the gateway's own RX rebroadcast (the bridge
must drop messages it just sent, otherwise R→M loops back to RNS as a
fresh M→R), the regression guard for Hardening E (deleted dead
publish_to_mqtt — see Issue #40), and Hardening B's persistent SQLite
spill on in-memory queue overflow.

Run: python3 -m pytest tests/test_mqtt_bridge_handler.py -v
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.mqtt_bridge_handler import MQTTBridgeHandler


class TestPublishToMqttDeleted:
    """Hardening E regression guard: the dead publish_to_mqtt path that
    used a wrong-shape downlink topic (Issue #40) was removed. R→M now
    routes exclusively through send_text_direct() via destination="meshtastic".
    """

    def test_publish_to_mqtt_symbol_absent(self):
        assert not hasattr(MQTTBridgeHandler, "publish_to_mqtt"), (
            "publish_to_mqtt was deleted as dead code (Hardening E). "
            "Reinstating it requires fixing the downlink topic shape "
            "(msh/{region}/2/json/mqtt/{node_id}) and the from-field at "
            "the same time, not just resurrecting the old version."
        )


class TestHardeningBSpillOnFull:
    """Hardening B: when the in-memory M→R queue overflows, the message
    must land in the persistent SQLite queue (destination='rns_xform')
    rather than being silently dropped. Verifies the spill helper
    contract — not the full dispatch path (which is exercised by
    test_rns_bridge.py through _dispatch_rns_xform_spill)."""

    def _make_minimal_handler(self, persistent_queue=None):
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h._persistent_queue = persistent_queue
        return h

    def test_spill_persists_under_rns_xform_destination(self):
        from gateway.rns_bridge import BridgedMessage
        pq = MagicMock()
        pq.enqueue.return_value = "msg-id-99"
        h = self._make_minimal_handler(persistent_queue=pq)

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!aabb0042",
            destination_id=None,
            content="overflow content",
            metadata={"channel": 2},
        )
        result = h._spill_to_persistent_queue(msg)

        assert result is True
        pq.enqueue.assert_called_once()
        kwargs = pq.enqueue.call_args.kwargs
        assert kwargs["destination"] == "rns_xform"
        assert kwargs["payload"]["source_id"] == "!aabb0042"
        assert kwargs["payload"]["content"] == "overflow content"
        assert kwargs["payload"]["is_broadcast"] is False
        assert kwargs["payload"]["metadata"] == {"channel": 2}

    def test_spill_returns_false_when_queue_unwired(self):
        from gateway.rns_bridge import BridgedMessage
        h = self._make_minimal_handler(persistent_queue=None)
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!a",
            destination_id=None, content="x",
        )
        assert h._spill_to_persistent_queue(msg) is False

    def test_spill_returns_false_when_enqueue_returns_none(self):
        """Persistent queue dedup or shed-on-overflow returns None;
        caller should treat that as drop, not silent success."""
        from gateway.rns_bridge import BridgedMessage
        pq = MagicMock()
        pq.enqueue.return_value = None
        h = self._make_minimal_handler(persistent_queue=pq)
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!a",
            destination_id=None, content="dup",
        )
        assert h._spill_to_persistent_queue(msg) is False

    def test_spill_swallows_persistent_queue_exception(self):
        """A persistent-queue failure mid-burst should not crash the MQTT
        thread; the caller falls through to the dropped counter."""
        from gateway.rns_bridge import BridgedMessage
        pq = MagicMock()
        pq.enqueue.side_effect = RuntimeError("disk full")
        h = self._make_minimal_handler(persistent_queue=pq)
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!a",
            destination_id=None, content="x",
        )
        assert h._spill_to_persistent_queue(msg) is False


# ---------------------------------------------------------------------------
# Self-echo filter — _bridge_text_message must drop the gateway's own TX
# rebroadcast that meshtasticd republishes via MQTT (otherwise it loops back
# to RNS as a fresh Mesh→RNS message).
# ---------------------------------------------------------------------------


def _make_bridge_handler(own_id: str = "") -> MQTTBridgeHandler:
    """Construct a handler with just enough wiring for _bridge_text_message."""
    config = MagicMock()
    config.meshtastic.gateway_node_id = own_id
    handler = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
    handler.config = config
    handler._message_queue = MagicMock()
    handler._stats_lock = MagicMock()
    handler._stats_lock.__enter__ = lambda _self: None
    handler._stats_lock.__exit__ = lambda _self, *a: False
    handler.stats = {"errors": 0}
    handler._should_bridge = None
    handler._message_callback = None
    handler._is_duplicate = MagicMock(return_value=False)
    return handler


class TestSelfEchoFilter:
    """gateway_node_id, when set, drops the RNS→Mesh loopback (own sender +
    [RNS:xxxx] prefix). Other own-sender traffic — web UI sends, CLI sends,
    fresh user messages — must still bridge so operators see their own
    activity in NomadNet."""

    def test_rns_loopback_dropped(self):
        """[RNS:xxxx]-prefixed text from own_id is the RNS→Mesh echo — drop."""
        handler = _make_bridge_handler(own_id="!ebfa1b11")
        handler._bridge_text_message(
            {
                "sender": "!ebfa1b11",
                "to": 0xFFFFFFFF,
                "payload": {"text": "[RNS:d1df] hello fleet"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!ebfa1b11",
        )
        handler._message_queue.put.assert_not_called()

    def test_own_sender_no_rns_prefix_passes(self):
        """Web UI / CLI sends from the gateway box have no [RNS:] prefix —
        these are legitimate operator traffic that must bridge to RNS so
        their own NomadNet inbox sees them."""
        handler = _make_bridge_handler(own_id="!ebfa1b11")
        handler._bridge_text_message(
            {
                "sender": "!ebfa1b11",
                "to": 0xFFFFFFFF,
                "payload": {"text": "typed from web UI"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!ebfa1b11",
        )
        handler._message_queue.put.assert_called_once()

    def test_other_sender_with_rns_prefix_dropped(self):
        """A SIBLING fleet gateway's [RNS:] injection, heard on the shared
        channel, must NOT be re-bridged — that content is already in RNS, so
        re-bridging loops (e.g. moc1 re-bridging moc's injection). This
        reverses the older behavior that bridged other-sender [RNS:] text."""
        handler = _make_bridge_handler(own_id="!ebfa1b11")
        handler._bridge_text_message(
            {
                "from": 0xaabb0042,
                "sender": "!aabb0042",
                "to": 0xFFFFFFFF,
                "payload": {"text": "[RNS:abcd] from sibling gateway"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!aabb0042",
        )
        handler._message_queue.put.assert_not_called()

    def test_other_sender_passes_through(self):
        handler = _make_bridge_handler(own_id="!ebfa1b11")
        handler._bridge_text_message(
            {
                "sender": "!aabb0042",
                "to": 0xFFFFFFFF,
                "payload": {"text": "real msg"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!aabb0042",
        )
        handler._message_queue.put.assert_called_once()

    def test_rns_tagged_dropped_regardless_of_gateway_node_id(self):
        """The loop guard no longer depends on gateway_node_id — [RNS:]-tagged
        content is already-bridged RNS content and is dropped even when
        gateway_node_id is unset."""
        handler = _make_bridge_handler(own_id="")
        handler._bridge_text_message(
            {
                "sender": "!ebfa1b11",
                "to": 0xFFFFFFFF,
                "payload": {"text": "[RNS:d1df] would-be-echo"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!ebfa1b11",
        )
        handler._message_queue.put.assert_not_called()


class TestIsAlreadyBridged:
    """The shared loop-guard predicate. Covers every bridge tag in
    BRIDGE_TAG_PREFIXES — mirrored with MeshAnchor's
    ECHO_LOOP_INVARIANT_PREFIXES (keep in sync)."""

    def test_rns_tagged_true(self):
        from gateway.base_handler import is_already_bridged
        assert is_already_bridged("[RNS:f68c] wx reply")
        assert is_already_bridged("   [RNS:abcd] leading space")

    def test_meshcore_and_broadcast_tags_true(self):
        """Live echo amplifier on moc (2026-06-03): bare [MC:p4] content
        injected through the gateway's own radio was re-bridged M→R to
        6 LXMF dests + Phase-1 relayed to 2 peer gateways. Any leading
        bridge tag means the content is already in the LXMF world."""
        from gateway.base_handler import is_already_bridged
        assert is_already_bridged("[MC:p4] hey you")
        assert is_already_bridged("[ch0:p4] hey you")
        assert is_already_bridged("[ch1:p4] something")
        assert is_already_bridged("[MeshCore] legacy tag")
        assert is_already_bridged("[Mesh:xxxx] aggregated")

    def test_plain_content_false(self):
        from gateway.base_handler import is_already_bridged
        assert not is_already_bridged("wx")
        assert not is_already_bridged("")
        assert not is_already_bridged("ola moc it's me")
        # Tag-like but not a bridge marker — operator brackets are fine
        assert not is_already_bridged("[urgent] meet at the pavilion")

    def test_prefix_list_mirrors_meshanchor(self):
        """Parity pin: the list must stay identical to MeshAnchor's
        ECHO_LOOP_INVARIANT_PREFIXES (gateway/config.py). If this fails,
        port the new tag to the other repo, not just here."""
        from gateway.base_handler import BRIDGE_TAG_PREFIXES
        assert set(BRIDGE_TAG_PREFIXES) == {
            "[MeshCore]", "[MC:", "[RNS:", "[ch0:", "[ch1:", "[Mesh:",
        }


class TestSourceAttribution:
    """Mesh→RNS must attribute to the ORIGINATING node (`from`), not the
    last-hop `sender`. Using `sender` collapsed every multi-hop source (the
    bot, reached over several hops) into whichever node last rebroadcast it,
    so they all surfaced as one identity (and then one [RNS:<gw>] tag)."""

    def test_source_id_is_originator_not_last_hop(self):
        handler = _make_bridge_handler(own_id="!32962f10")
        handler._bridge_text_message(
            {
                "from": 0xa2e95ba4,        # the bot (originator)
                "sender": "!ebfa1b11",     # last-hop rebroadcaster
                "to": 0xFFFFFFFF,
                "payload": {"text": "wx reply"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!ebfa1b11",
        )
        handler._message_queue.put.assert_called_once()
        msg = handler._message_queue.put.call_args.args[0]
        assert msg.source_id == "!a2e95ba4"   # originator, not !ebfa1b11

    def test_two_origins_via_same_last_hop_stay_distinct(self):
        """Two different originators relayed by the SAME last hop must keep
        distinct source_ids (the bug collapsed them to the last hop)."""
        handler = _make_bridge_handler(own_id="!32962f10")
        for frm in (0xa2e95ba4, 0x11112222):
            handler._bridge_text_message(
                {
                    "from": frm,
                    "sender": "!ebfa1b11",
                    "to": 0xFFFFFFFF,
                    "payload": {"text": "hi"},
                    "channel": 2,
                },
                topic="msh/US/2/json/meshforge/!ebfa1b11",
            )
        ids = [c.args[0].source_id for c in handler._message_queue.put.call_args_list]
        assert ids == ["!a2e95ba4", "!11112222"]

    def test_falls_back_to_sender_when_from_absent(self):
        """Older/edge payloads without `from` still attribute to sender
        rather than dropping the message."""
        handler = _make_bridge_handler(own_id="!32962f10")
        handler._bridge_text_message(
            {
                "sender": "!aabb0042",
                "to": 0xFFFFFFFF,
                "payload": {"text": "no from field"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!aabb0042",
        )
        handler._message_queue.put.assert_called_once()
        msg = handler._message_queue.put.call_args.args[0]
        assert msg.source_id == "!aabb0042"


class TestNodeTrackerAttribution:
    """Node-tracker updates must key on the ORIGINATOR (`from`), not the
    MQTT uplinker (`sender`). On a localhost broker `sender` is ALWAYS
    this box's own radio, so the old code wrote every heard node's
    nodeinfo/position onto the gateway's own node id — its name churned
    to whichever node was heard last (the live "hawaii_gaz" phantom on
    moc, 2026-06-03) and its position teleported between sites. Same
    sender-vs-from class as TestSourceAttribution / 9554f06.
    """

    def _make_tracker_handler(self):
        handler = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        handler.node_tracker = MagicMock()
        return handler

    def test_nodeinfo_keyed_on_originator_not_uplinker(self):
        """Pins the live corruption shape: KH7EH-15's nodeinfo arrived
        via moc's own radio (!32962f10) as sender — it must be recorded
        as !435b1180, never onto the gateway's own entry."""
        handler = self._make_tracker_handler()
        handler._update_nodeinfo({
            "from": 0x435b1180,
            "sender": "!32962f10",
            "payload": {"longname": "KH7EH-15", "shortname": "EH15"},
        })
        node = handler.node_tracker.add_node.call_args.args[0]
        assert node.id == "!435b1180"
        assert node.name == "KH7EH-15"

    def test_two_nodeinfos_via_same_uplinker_stay_distinct(self):
        handler = self._make_tracker_handler()
        for frm, name in ((0x435b1180, "KH7EH-15"), (0x47115e4f, "PHTO")):
            handler._update_nodeinfo({
                "from": frm,
                "sender": "!32962f10",
                "payload": {"longname": name, "shortname": name[:4]},
            })
        ids = [c.args[0].id
               for c in handler.node_tracker.add_node.call_args_list]
        assert ids == ["!435b1180", "!47115e4f"]

    def test_nodeinfo_falls_back_to_sender_when_from_absent(self):
        handler = self._make_tracker_handler()
        handler._update_nodeinfo({
            "sender": "!aabb0042",
            "payload": {"longname": "Edge", "shortname": "EDGE"},
        })
        node = handler.node_tracker.add_node.call_args.args[0]
        assert node.id == "!aabb0042"

    def test_node_from_mqtt_keyed_on_originator(self):
        handler = self._make_tracker_handler()
        handler._update_node_from_mqtt({
            "from": 0x435b1180,
            "sender": "!32962f10",
        })
        node = handler.node_tracker.add_node.call_args.args[0]
        assert node.id == "!435b1180"

    def test_originator_id_helper_contract(self):
        assert MQTTBridgeHandler._originator_id(
            {"from": 0xa2e95ba4, "sender": "!32962f10"}) == "!a2e95ba4"
        assert MQTTBridgeHandler._originator_id(
            {"sender": "!32962f10"}) == "!32962f10"
        assert MQTTBridgeHandler._originator_id({}) == ""


class TestDmToGatewayChannelWildcard:
    """Theme-A step 3 radio-smoke finding (2026-06-03): DMs ride the PRIMARY
    channel, whose name the config doesn't know — channel-scoped json
    subscriptions could never deliver a DM-to-gateway to the bridge. When
    the leg is armed, the json subscription widens to a wildcard and the
    channel scope is enforced per-message instead: configured channel
    passes as before; foreign channels pass ONLY when addressed to the
    gateway's own node."""

    OWN = "!ebfa1b11"
    OWN_NUM = 0xEBFA1B11

    def _armed_handler(self):
        h = _make_bridge_handler(own_id=self.OWN)
        h.config.rns.sessions_enabled = True
        h.config.mqtt_bridge.channel = "meshforge"
        return h

    def test_node_num_when_armed(self):
        h = self._armed_handler()
        assert h._dm_to_gateway_node_num() == self.OWN_NUM

    def test_node_num_dormant_when_flag_not_strict_true(self):
        h = _make_bridge_handler(own_id=self.OWN)  # MagicMock flag
        assert h._dm_to_gateway_node_num() is None

    def test_node_num_dormant_when_id_invalid(self):
        h = _make_bridge_handler(own_id="nothex")
        h.config.rns.sessions_enabled = True
        assert h._dm_to_gateway_node_num() is None

    def test_topic_channel_name_both_shapes(self):
        f = MQTTBridgeHandler._topic_channel_name
        assert f("msh/US/2/json/meshforge/!ebfa1b11") == "meshforge"
        assert f("msh/2/json/PrimaryChan/!ebfa1b11") == "PrimaryChan"
        assert f("msh/2/e/meshforge/!ebfa1b11") is None

    def test_on_connect_armed_subscribes_wildcard(self):
        h = self._armed_handler()
        h.config.mqtt_bridge.root_topic = "msh"
        h.config.mqtt_bridge.json_enabled = True
        h.config.mqtt_bridge.broker = "localhost"
        h.config.mqtt_bridge.port = 1883
        client = MagicMock()

        h._on_connect(client, None, None, 0)

        topics = [c.args[0] for c in client.subscribe.call_args_list]
        assert "msh/+/2/json/+/#" in topics
        assert "msh/2/json/+/#" in topics

    def test_on_connect_dormant_subscribes_scoped(self):
        h = _make_bridge_handler(own_id=self.OWN)  # flag MagicMock → dormant
        h.config.mqtt_bridge.root_topic = "msh"
        h.config.mqtt_bridge.channel = "meshforge"
        h.config.mqtt_bridge.json_enabled = True
        h.config.mqtt_bridge.broker = "localhost"
        h.config.mqtt_bridge.port = 1883
        client = MagicMock()

        h._on_connect(client, None, None, 0)

        topics = [c.args[0] for c in client.subscribe.call_args_list]
        assert "msh/+/2/json/meshforge/#" in topics
        assert "msh/+/2/json/+/#" not in topics

    def test_foreign_channel_broadcast_dropped(self):
        h = self._armed_handler()
        h._bridge_text_message(
            {"sender": "!ebfa1b11", "from": 0xAABB0042, "to": 0xFFFFFFFF,
             "payload": {"text": "primary-channel chatter"}, "channel": 0},
            topic="msh/2/json/PrimaryChan/!ebfa1b11",
        )
        h._message_queue.put.assert_not_called()

    def test_foreign_channel_dm_to_gateway_passes(self):
        h = self._armed_handler()
        h._bridge_text_message(
            {"sender": "!ebfa1b11", "from": 0xAABB0042, "to": self.OWN_NUM,
             "payload": {"text": "smoke B private"}, "channel": 0},
            topic="msh/2/json/PrimaryChan/!ebfa1b11",
        )
        h._message_queue.put.assert_called_once()
        msg = h._message_queue.put.call_args.args[0]
        assert msg.destination_id == self.OWN
        assert msg.is_broadcast is False
        assert msg.source_id == "!aabb0042"

    def test_configured_channel_still_passes_when_armed(self):
        h = self._armed_handler()
        h._bridge_text_message(
            {"sender": "!ebfa1b11", "from": 0xAABB0042, "to": 0xFFFFFFFF,
             "payload": {"text": "normal channel msg"}, "channel": 2},
            topic="msh/US/2/json/meshforge/!ebfa1b11",
        )
        h._message_queue.put.assert_called_once()


class TestDualPathDedupRegistration:
    """Broadcast TX through the MQTT bridge handler's toradio path registers
    in the process-wide RecentRfTxRegistry (dual-path dedup).

    Regression guard for the 2026-06-04 live finding: the "symmetric"
    registration (f02ad82) covered meshtastic_handler's TCP sendText —
    but the LIVE R→M dispatch route is THIS handler's send_text →
    send_text_direct() (HTTP toradio), which never registered. Relay TXs
    were invisible to the registry, so a second relay copy of the same
    content ([MC:p4] wx then [RNS:627f] [ch0:p4] wx) always hit RF twice.
    DMs and failed sends must not register.
    """

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _handler(self, http_ok=True, cli_ok=False):
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h._send_via_http_protobuf = MagicMock(return_value=http_ok)
        h._send_via_cli = MagicMock(return_value=cli_ok)
        return h

    def test_send_text_broadcast_registers(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._handler(http_ok=True)
        assert h.send_text("[RNS:58ce] [MC:p4] wx", destination=None, channel=2)
        # Tag-normalized: any later copy of the same logical content matches.
        assert reg.seen_within("wx", 60.0)
        assert reg.seen_within("[RNS:627f] [ch0:p4] wx", 60.0)

    def test_send_text_dm_does_not_register(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._handler(http_ok=True)
        assert h.send_text("dm content", destination="!b03bb70c", channel=2)
        assert not reg.seen_within("dm content", 60.0)

    def test_cli_fallback_broadcast_registers(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._handler(http_ok=False, cli_ok=True)
        assert h.send_text("cli fallback bc", destination=None, channel=2)
        assert reg.seen_within("cli fallback bc", 60.0)

    def test_failed_send_does_not_register(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._handler(http_ok=False, cli_ok=False)
        assert not h.send_text("never sent", destination=None, channel=2)
        assert not reg.seen_within("never sent", 60.0)


class TestDispatchTimeDedupRecheck:
    """queue_send re-checks the registry at DISPATCH time (gated).

    Live 2026-06-04 (moc, every wx event): the LAN-fast RNS relay copy of
    the bot's reply was enqueue-checked ~250ms BEFORE mesh_bridge
    registered its serial-leg downlink, missed, and TX'd 3s later as a
    visible duplicate on LF. By dispatch time (≥min_spacing_s pacing) the
    registry is settled — the re-check closes the race deterministically.
    Suppress-only-on-hit; flag-gated strict-True; DMs exempt.
    """

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _handler(self, dedup_on=True):
        import threading
        from types import SimpleNamespace
        h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        h.config = SimpleNamespace(rns=SimpleNamespace(
            dual_path_dedup_enabled=dedup_on,
            dual_path_dedup_window_sec=60,
        ))
        h.stats = {}
        h._stats_lock = threading.Lock()
        h.send_text = MagicMock(return_value=True)
        return h

    def test_suppresses_broadcast_on_registry_hit(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._handler(dedup_on=True)
        reg.register("Tonight: Occasional rain shwrs.")   # downlink already on RF
        assert h.queue_send({"message": "[RNS:Borg serve] Tonight: Occasional rain shwrs.",
                             "destination": None, "channel": 2}) is True
        h.send_text.assert_not_called()
        assert h.stats["dispatch_dedup_suppressed"] == 1

    def test_no_suppression_when_flag_off(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._handler(dedup_on=False)
        reg.register("Tonight: Occasional rain shwrs.")
        assert h.queue_send({"message": "[RNS:Borg serve] Tonight: Occasional rain shwrs.",
                             "destination": None, "channel": 2}) is True
        h.send_text.assert_called_once()

    def test_dm_never_suppressed(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._handler(dedup_on=True)
        reg.register("private reply")
        assert h.queue_send({"message": "private reply",
                             "destination": "!b03bb70c", "channel": 2}) is True
        h.send_text.assert_called_once()

    def test_no_hit_sends_normally(self, monkeypatch):
        self._fresh_registry(monkeypatch)
        h = self._handler(dedup_on=True)
        assert h.queue_send({"message": "fresh content",
                             "destination": None, "channel": 2}) is True
        h.send_text.assert_called_once()

    def test_second_relay_copy_of_same_command_suppressed(self, monkeypatch):
        """The full 09:24 live shape: copy #1 ([MC:p4] wx) dispatches and
        registers via send_text; copy #2 ([RNS:627f] [ch0:p4] wx) of the
        SAME normalized content must die at its own dispatch 3s later."""
        reg = self._fresh_registry(monkeypatch)
        h = self._handler(dedup_on=True)
        # copy #1 — no hit, sends; simulate the send_text-side registration
        assert h.queue_send({"message": "[RNS:58ce] [MC:p4] wx",
                             "destination": None, "channel": 2}) is True
        h.send_text.assert_called_once()
        reg.register("[RNS:58ce] [MC:p4] wx")
        # copy #2 — different tag chain, same content → suppressed
        assert h.queue_send({"message": "[RNS:627f] [ch0:p4] wx",
                             "destination": None, "channel": 2}) is True
        h.send_text.assert_called_once()  # still exactly one real TX


class TestSeenOnRfRegistration:
    """RX-time registration (seen-on-RF, cross-BOX dedup direction).

    A broadcast heard via MQTT is ON this radio's mesh whoever TX'd it —
    including another box's radio on the same RF segment (live 2026-06-04:
    moc's serial leg TX'd [Mesh:LONG_FAST:..] Cmd onto moc3's primary ST
    mesh; moc3's TX bookkeeping couldn't see it, its RNS relay copy of the
    same content went out too, and the bot answered both). Registration
    happens BEFORE the loop guard (tagged content is refused for bridging
    but is still on the mesh) and only for broadcasts.
    """

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _rx(self, handler, text, to=0xFFFFFFFF):
        handler._bridge_text_message(
            {"sender": "!ebfa1b11", "from": 0xAABB0042, "to": to,
             "payload": {"text": text}, "channel": 2},
            topic="msh/US/2/json/meshforge/!ebfa1b11",
        )

    def test_broadcast_rx_registers(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = _make_bridge_handler()
        self._rx(h, "plain user message")
        assert reg.seen_within("plain user message", 60.0)

    def test_tagged_rx_registers_despite_loop_guard_drop(self, monkeypatch):
        """The moc3 shape: a peer box's [Mesh:..]-tagged TX heard on RF must
        register (normalized) even though the loop guard refuses to bridge it."""
        reg = self._fresh_registry(monkeypatch)
        h = _make_bridge_handler()
        self._rx(h, "[Mesh:LONG_FAST:2f10] Cmd")
        h._message_queue.put.assert_not_called()   # loop guard still drops
        assert reg.seen_within("Cmd", 60.0)        # but the mesh HAS it
        assert reg.seen_within("[RNS:nurse dude] Cmd", 60.0)  # relay copy matches

    def test_dm_rx_does_not_register(self, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = _make_bridge_handler()
        self._rx(h, "private note", to=0x32962F10)
        assert not reg.seen_within("private note", 60.0)

    def test_cross_box_end_to_end_suppression(self, monkeypatch):
        """Full moc3 sequence: hear the peer's tagged TX on RF, then our own
        RNS relay copy of the same content dies at queue dispatch."""
        import threading
        from types import SimpleNamespace
        reg = self._fresh_registry(monkeypatch)
        rx = _make_bridge_handler()
        self._rx(rx, "[Mesh:LONG_FAST:2f10] Cmd")   # peer's radio put it on our mesh

        tx = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
        tx.config = SimpleNamespace(rns=SimpleNamespace(
            dual_path_dedup_enabled=True, dual_path_dedup_window_sec=60))
        tx.stats = {}
        tx._stats_lock = threading.Lock()
        tx.send_text = MagicMock(return_value=True)
        assert tx.queue_send({"message": "[RNS:nurse dude] Cmd",
                              "destination": None, "channel": 2}) is True
        tx.send_text.assert_not_called()
        assert tx.stats["dispatch_dedup_suppressed"] == 1


# ---------------------------------------------------------------------------
# Thread-2 step 4 — honest-signal: ACK consumption is inert in mqtt_bridge mode
# ---------------------------------------------------------------------------

class TestAckConsumptionViaServiceEnvelope:
    """Thread-2 step 4 in mqtt_bridge mode: ACK consumption via the /e/
    ServiceEnvelope topic (the soak-proven path). Active when crypto is
    available; an honest INERT warning only when it isn't."""

    def _build(self, ack_enabled):
        import threading
        from gateway.config import GatewayConfig
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        config = GatewayConfig()
        config.rns.meshtastic_ack_consumption_enabled = ack_enabled
        return MQTTBridgeHandler(
            config=config, node_tracker=MagicMock(), health=MagicMock(),
            stop_event=threading.Event(), stats={},
            stats_lock=threading.Lock(), message_queue=MagicMock())

    def test_active_log_when_crypto_available(self, caplog):
        import logging
        from unittest.mock import patch
        with patch('gateway.mqtt_bridge_handler.crypto_available',
                   return_value=True):
            with caplog.at_level(logging.INFO):
                h = self._build(ack_enabled=True)
        assert any("ACTIVE" in r.message and "/e/" in r.message
                   for r in caplog.records)
        assert not [r for r in caplog.records if "INERT" in r.message]
        assert h.ack_tracker is not None

    def test_inert_warning_only_when_crypto_unavailable(self, caplog):
        import logging
        from unittest.mock import patch
        with patch('gateway.mqtt_bridge_handler.crypto_available',
                   return_value=False):
            with caplog.at_level(logging.WARNING):
                self._build(ack_enabled=True)
        assert any("INERT" in r.message for r in caplog.records)

    def test_silent_when_flag_off(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            self._build(ack_enabled=False)
        assert not [r for r in caplog.records
                    if "ACK consumption" in r.message or "INERT" in r.message]


class TestEServiceEnvelopeAckIngestion:
    """The /e/ ACK pipeline: a decoded ROUTING_APP that matches an in-flight
    DM becomes CONFIRMED / DROPPED; TX registers the packet_id."""

    def _h(self, ack_enabled=True):
        import threading
        from gateway.config import GatewayConfig
        from gateway.mqtt_bridge_handler import MQTTBridgeHandler
        config = GatewayConfig()
        config.rns.meshtastic_ack_consumption_enabled = ack_enabled
        return MQTTBridgeHandler(
            config=config, node_tracker=MagicMock(), health=MagicMock(),
            stop_event=threading.Event(), stats={},
            stats_lock=threading.Lock(), message_queue=MagicMock())

    def _rec(self, monkeypatch):
        rec = MagicMock()
        monkeypatch.setattr('gateway.delivery_counters.record', rec)
        return rec

    def test_confirmed_on_positive_ack(self, monkeypatch):
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = self._h()
        h.ack_tracker.register(0xAA01, "msg-7")
        dp = MagicMock(request_id=0xAA01)
        dp.routing_error_name.return_value = "NONE"
        h._handle_routing_envelope(dp)
        rec.assert_called_once_with(DeliveryState.CONFIRMED,
                                    msg_id="msg-7", protocol="meshtastic")
        assert h.stats.get("mesh_ack_confirmed") == 1

    def test_dropped_on_nak(self, monkeypatch):
        from gateway.delivery_counters import DeliveryState, DropReason
        rec = self._rec(monkeypatch)
        h = self._h()
        h.ack_tracker.register(0xBB02, "msg-9")
        dp = MagicMock(request_id=0xBB02)
        dp.routing_error_name.return_value = "MAX_RETRANSMIT"
        h._handle_routing_envelope(dp)
        args, kwargs = rec.call_args
        assert args[0] == DeliveryState.DROPPED
        assert kwargs["msg_id"] == "msg-9"
        assert kwargs["drop_reason"] == DropReason.RETRIES_EXHAUSTED
        assert kwargs["note"] == "meshtastic_nak:MAX_RETRANSMIT"
        assert h.stats.get("mesh_ack_failed") == 1

    def test_unmatched_routing_is_noop(self, monkeypatch):
        rec = self._rec(monkeypatch)
        h = self._h()
        dp = MagicMock(request_id=0xDEAD)
        dp.routing_error_name.return_value = "NONE"
        h._handle_routing_envelope(dp)
        rec.assert_not_called()

    def test_protobuf_message_skips_when_disabled(self, monkeypatch):
        h = self._h(ack_enabled=False)
        called = {"n": 0}
        monkeypatch.setattr('gateway.mqtt_bridge_handler.decode_service_envelope',
                            lambda *a, **k: called.__setitem__("n", called["n"]+1))
        h._handle_protobuf_message("msh/2/e/LongFast/!x", b"\x00")
        assert called["n"] == 0   # never decoded — disabled

    def test_protobuf_message_skips_when_no_pending(self, monkeypatch):
        h = self._h(ack_enabled=True)
        called = {"n": 0}
        monkeypatch.setattr('gateway.mqtt_bridge_handler.decode_service_envelope',
                            lambda *a, **k: called.__setitem__("n", called["n"]+1))
        # no DM registered → pending_count 0 → no decode (cost guard)
        h._handle_protobuf_message("msh/2/e/LongFast/!x", b"\x00")
        assert called["n"] == 0

    def test_protobuf_message_decodes_routing_when_pending(self, monkeypatch):
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = self._h(ack_enabled=True)
        h.ack_tracker.register(0xCAFE, "msg-x")
        dp = MagicMock(request_id=0xCAFE, is_routing=True)
        dp.routing_error_name.return_value = "NONE"
        monkeypatch.setattr('gateway.mqtt_bridge_handler.decode_service_envelope',
                            lambda *a, **k: dp)
        h._handle_protobuf_message("msh/2/e/LongFast/!x", b"\x01\x02")
        rec.assert_called_once_with(DeliveryState.CONFIRMED,
                                    msg_id="msg-x", protocol="meshtastic")

    def test_maybe_register_skips_broadcast(self):
        h = self._h()
        h._maybe_register_ack(0x1234, 0xFFFFFFFF, None, record_sent=False)
        assert h.ack_tracker.pending_count() == 0

    def test_maybe_register_dm(self, monkeypatch):
        self._rec(monkeypatch)
        h = self._h()
        h._maybe_register_ack(0x1234, 0xAABB0001, "q-1", record_sent=False)
        assert h.ack_tracker.resolve(0x1234) == ("q-1", "meshtastic")

    def test_channel_keys_include_default_and_downlink(self):
        h = self._h()
        h.config.meshtastic.downlink_psk = "Zm9vYmFy"
        h.config.meshtastic.channel_keys = ["YmF6cXV4", "Zm9vYmFy"]  # dup ignored
        keys = h._channel_keys()
        from utils.meshtastic_se_crypto import DEFAULT_KEY_B64
        assert keys[0] == DEFAULT_KEY_B64
        assert "Zm9vYmFy" in keys
        assert "YmF6cXV4" in keys
        assert keys.count("Zm9vYmFy") == 1  # no duplicates
