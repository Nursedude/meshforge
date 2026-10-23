"""BIC-2 slice 3 — the direct sends toward a mesh name their leg.

Before this slice a direct ``send_text`` recorded SENT only for a wantAck
DM (``_register_ack_for_send`` returned before recording for broadcasts and
whenever ACK consumption was off), so a direct Meshtastic broadcast was
never counted at all — slice 1's ``send_to_rns`` gap on the mesh side. And
no direct send named its origin, so even the DMs that were counted landed
on ``?>meshtastic``. Operator 10-23: "Source + SENT on all".

Seams: ``AckTracker.resolve_leg``; ``MeshtasticHandler.send_text(...,
source=)`` / ``queue_send`` / ``_handle_routing_ack`` →
``delivery_counters.snapshot()["legs"]``.
"""
from __future__ import annotations

import threading
from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from gateway import delivery_counters as dc
from gateway.ack_tracker import AckTracker
from tests.test_rns_bridge import bridge  # noqa: F401 — fixture


@pytest.fixture(autouse=True)
def _isolate_delivery_counters(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHFORGE_DELIVERY_COUNTERS_DB",
                       str(tmp_path / "delivery.db"))
    dc._reset_singleton_for_tests()
    yield
    dc._reset_singleton_for_tests()


def _legs():
    return dc.get_singleton().snapshot()["legs"]


# ── the ACK tracker carries the leg from send to confirmation ─────────

def test_resolve_leg_returns_the_registered_source():
    t = AckTracker()
    t.register(0xA1, "m1", dest_num=0xAABB0001, source="rns")

    assert t.resolve_leg(0xA1, from_num=0xAABB0001) == ("m1", "meshtastic",
                                                        "rns")


def test_resolve_keeps_its_two_tuple_shape():
    t = AckTracker()
    t.register(0xA2, "m2", dest_num=0xAABB0001, source="rns")

    assert t.resolve(0xA2, from_num=0xAABB0001) == ("m2", "meshtastic")


# ── MeshtasticHandler ─────────────────────────────────────────────────

@pytest.fixture
def mt(allow_local_radio_tx):
    from gateway.config import GatewayConfig, MeshtasticConfig
    from gateway.meshtastic_handler import MeshtasticHandler
    cfg = GatewayConfig()
    cfg.meshtastic = MeshtasticConfig(host="127.0.0.1", port=4403)
    h = MeshtasticHandler(
        config=cfg, node_tracker=MagicMock(), health=MagicMock(),
        stop_event=threading.Event(),
        stats={'errors': 0, 'messages_received': 0, 'messages_sent': 0},
        stats_lock=threading.Lock(), message_queue=Queue(maxsize=10))
    h._connected = True
    h._interface = MagicMock()
    h._interface.sendText.return_value = MagicMock(id=0x11112222, to=0xAABB0001)
    yield h
    h.disconnect()


def test_a_direct_broadcast_is_counted_sent_on_its_leg(mt):
    assert mt.send_text("all hands", source="rns") is True

    assert _legs()["rns>meshtastic"]["15m"]["sent"] == 1


def test_a_direct_dm_with_ack_consumption_off_is_counted_sent(mt):
    mt.config.rns.meshtastic_ack_consumption_enabled = False

    assert mt.send_text("hi", destination="!aabb0001", source="rns") is True

    assert _legs()["rns>meshtastic"]["15m"]["sent"] == 1


def test_a_direct_dm_confirms_on_the_same_leg_and_sends_once(mt):
    mt.config.rns.meshtastic_ack_consumption_enabled = True
    assert mt.send_text("hi", destination="!aabb0001", source="rns") is True

    mt._handle_routing_ack({"portnum": "ROUTING_APP",
                            "requestId": 0x11112222, "routing": {}},
                           0xAABB0001)

    win = _legs()["rns>meshtastic"]["15m"]
    assert win["sent"] == 1 and win["confirmed"] == 1


def test_a_nak_fails_on_the_same_leg(mt):
    mt.config.rns.meshtastic_ack_consumption_enabled = True
    mt.send_text("hi", destination="!aabb0001", source="rns")

    mt._handle_routing_ack({"portnum": "ROUTING_APP", "requestId": 0x11112222,
                            "routing": {"errorReason": "MAX_RETRANSMIT"}})

    assert _legs()["rns>meshtastic"]["15m"]["failed"] == 1


def test_a_queued_dm_confirms_on_the_rows_leg_without_a_second_sent(mt):
    from gateway.delivery_legs import LEG_SOURCE_KEY
    mt.config.rns.meshtastic_ack_consumption_enabled = True
    assert mt.queue_send({"message": "reply", "destination": "!aabb0001",
                          "_queue_msg_id": "q-1", LEG_SOURCE_KEY: "rns"})

    mt._handle_routing_ack({"portnum": "ROUTING_APP",
                            "requestId": 0x11112222, "routing": {}},
                           0xAABB0001)

    win = _legs()["rns>meshtastic"]["15m"]
    assert win["confirmed"] == 1
    assert "sent" not in win           # the queue owns this row's SENT


def test_a_failed_direct_send_is_not_counted(mt):
    mt._interface.sendText.return_value = None

    assert mt.send_text("all hands", source="rns") is False

    assert "rns>meshtastic" not in _legs()


def test_a_counter_error_never_fails_a_direct_send(mt, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("counter broke")
    monkeypatch.setattr(dc, "record", boom)

    assert mt.send_text("all hands", source="rns") is True


# ── MQTTBridgeHandler (meshtasticd over HTTP protobuf; CLI fallback) ──

@pytest.fixture
def mq(allow_local_radio_tx):
    from gateway.ack_tracker import AckTracker as _AT
    from tests.test_mqtt_bridge_handler import _make_bridge_handler
    h = _make_bridge_handler()
    h.config.meshtastic.host = "127.0.0.1"
    h.config.meshtastic.http_port = 9443
    h.config.meshtastic.tls_for_port.return_value = False
    h._load_balancer = None
    h._ack_tracker = _AT()
    h._ack_consumption_enabled = lambda: True
    return h


class _Pkt:
    def __init__(self, req, frm, err=None):
        self.request_id, self.from_node, self._err = req, frm, err

    def routing_error_name(self):
        return self._err


def _http(pkt_id):
    from unittest.mock import patch
    return patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
                 lambda **kw: pkt_id)


def test_mqtt_direct_broadcast_is_counted_sent_on_its_leg(mq):
    with _http(0x2222):
        assert mq.send_text("all hands", source="rns") is True

    assert _legs()["rns>meshtastic"]["15m"]["sent"] == 1


def test_mqtt_direct_dm_confirms_on_the_same_leg(mq):
    with _http(0x3333):
        assert mq.send_text("hi", destination="!aabb0001", source="rns")

    mq._handle_routing_envelope(_Pkt(0x3333, 0xAABB0001))

    win = _legs()["rns>meshtastic"]["15m"]
    assert win["sent"] == 1 and win["confirmed"] == 1


def test_mqtt_nak_fails_on_the_same_leg(mq):
    with _http(0x4444):
        mq.send_text("hi", destination="!aabb0001", source="rns")

    mq._handle_routing_envelope(_Pkt(0x4444, 0xAABB0001, "MAX_RETRANSMIT"))

    assert _legs()["rns>meshtastic"]["15m"]["failed"] == 1


def test_mqtt_queued_dm_confirms_on_the_rows_leg_without_a_second_sent(mq):
    from gateway.delivery_legs import LEG_SOURCE_KEY
    with _http(0x5555):
        assert mq.queue_send({"message": "reply", "destination": "!aabb0001",
                              "_queue_msg_id": "q-9", LEG_SOURCE_KEY: "rns"})

    mq._handle_routing_envelope(_Pkt(0x5555, 0xAABB0001))

    win = _legs()["rns>meshtastic"]["15m"]
    assert win["confirmed"] == 1 and "sent" not in win


def test_mqtt_cli_fallback_send_is_counted_sent(mq, monkeypatch):
    monkeypatch.setattr(mq, "_send_via_http_protobuf", lambda *a, **k: False)
    monkeypatch.setattr(mq, "_send_via_cli", lambda *a, **k: True)

    assert mq.send_text("all hands", source="rns") is True

    assert _legs()["rns>meshtastic"]["15m"]["sent"] == 1


# ── MeshCoreHandler (async send worker; _record_tx) ───────────────────

def _mc_handler():
    from types import SimpleNamespace
    from gateway.meshcore_handler import MeshCoreHandler
    config = SimpleNamespace(
        meshcore=SimpleNamespace(
            enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
            connection_type="serial", tcp_host="localhost", tcp_port=4000,
            auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
            simulation_mode=True, channel_poll_interval_sec=5),
        meshtastic=SimpleNamespace(host="localhost", port=4403))
    health = MagicMock()
    health.record_error.return_value = "transient"
    h = MeshCoreHandler(config=config, node_tracker=MagicMock(), health=health,
                        stop_event=threading.Event(), stats={"errors": 0},
                        stats_lock=threading.Lock(),
                        message_queue=Queue(maxsize=10))
    return h


def _mc_drain(h, send_evt=None):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from utils import tx_guard
    contact = {"public_key": b"\xab\xcd", "adv_name": "p3"}
    h._meshcore = SimpleNamespace(commands=SimpleNamespace(
        get_contacts=AsyncMock(return_value=[contact]),
        send_msg=AsyncMock(return_value=send_evt),
        send_chan_msg=AsyncMock(return_value=None)))
    h._connected = True
    with tx_guard.allow_meshcore_egress():
        asyncio.run(h._process_outbound())


def test_a_meshcore_dm_is_sent_on_its_leg():
    h = _mc_handler()
    h._connected = True
    assert h.send_text("hello", destination="abcd", source="rns")

    _mc_drain(h)

    assert _legs()["rns>meshcore"]["15m"]["sent"] == 1


def test_a_queued_meshcore_row_carries_its_leg_to_the_worker():
    from gateway.delivery_legs import LEG_SOURCE_KEY
    h = _mc_handler()
    h._connected = True
    assert h.queue_send({"message": "hello", "destination": "abcd",
                         LEG_SOURCE_KEY: "rns"})

    _mc_drain(h)

    assert "rns>meshcore" in _legs()


def test_a_companion_refusal_fails_on_its_leg():
    class _Err:
        type, payload = "command_error", {"code_string": "ERR_TIMEOUT"}

        def is_error(self):
            return True
    h = _mc_handler()
    h._connected = True
    h.send_text("hello", destination="abcd", source="rns")

    _mc_drain(h, send_evt=_Err())

    assert _legs()["rns>meshcore"]["15m"]["failed"] == 1


# ── every direct-send caller names its leg ────────────────────────────

# Receivers that are not delivery-counted handlers: the low-level protobuf
# session client (its handler records around it) and the CLI wrapper.
_NOT_HANDLERS = {"client", "cli", "_interface"}
# Callers that cannot know the origin; each counts under ``?>dst``.
_SOURCELESS_SEND = {
    # commands.gateway.send_to_meshtastic: the CLI command wrapper, which
    # stamps source="local" on the bridge call itself.
    ("messaging.py", "send_message"),
}
_SEND_FNS = {"send_text", "send_to_meshtastic", "send_to_meshcore"}


def test_every_direct_send_caller_names_a_source():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    missing = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _SEND_FNS):
                    continue
                recv = node.func.value
                name = (recv.attr if isinstance(recv, ast.Attribute)
                        else getattr(recv, "id", ""))
                if name in _NOT_HANDLERS:
                    continue
                if any(k.arg == "source" for k in node.keywords):
                    continue
                if (path.name, fn.name) in _SOURCELESS_SEND:
                    continue
                missing.append(f"{path.relative_to(src)}:{node.lineno} "
                               f"in {fn.name}()")
    assert not missing, ("direct send without source= counts under '?>dst': "
                         + ", ".join(sorted(set(missing))))


# ── review of slice 3 (non-author, 10-23) ─────────────────────────────

def test_a_queued_meshcore_row_is_not_counted_sent_twice():
    # R1: queue_send hands the row to the async worker and the queue's
    # mark_delivered records SENT; the worker must not record a second SENT
    # on a fresh id (it read msg.get('id'), never _queue_msg_id).
    from gateway.delivery_legs import LEG_SOURCE_KEY
    h = _mc_handler()
    h._connected = True
    h.queue_send({"message": "hello", "destination": "abcd",
                  "_queue_msg_id": "q-mc-1", LEG_SOURCE_KEY: "rns"})

    _mc_drain(h)

    assert "sent" not in _legs().get("rns>meshcore", {}).get("15m", {})


def test_a_queued_meshcore_refusal_is_a_failure_on_the_rows_id():
    from gateway.delivery_legs import LEG_SOURCE_KEY

    class _Err:
        type, payload = "command_error", {"code_string": "ERR_TIMEOUT"}

        def is_error(self):
            return True
    h = _mc_handler()
    h._connected = True
    h.queue_send({"message": "hello", "destination": "abcd",
                  "_queue_msg_id": "q-mc-2", LEG_SOURCE_KEY: "rns"})

    _mc_drain(h, send_evt=_Err())

    assert _legs()["rns>meshcore"]["15m"]["failed"] == 1
    assert [e.id for e in dc.get_singleton().history_for("q-mc-2")] == ["q-mc-2"]


def test_synthesized_ids_do_not_collide_within_one_millisecond(
        mt, monkeypatch):
    # R5: two chunks sent back to back with no packet id must not share an id
    # (frozen clock: the collision is deterministic, not timing-dependent).
    import gateway.meshtastic_handler as mh
    monkeypatch.setattr(mh.time, "time", lambda: 1792780000.123)
    mt._interface.sendText.return_value = MagicMock(id=None, to=None)
    mt.send_text("chunk 1", source="rns")
    mt.send_text("chunk 2", source="rns")

    ids = [e.id for e in dc.get_singleton().recent() if e.state.value == "sent"]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_mqtt_refuses_a_bool_as_a_packet_id(mq):
    # R7: True is an int. AckTracker.register already refuses it, so no
    # false ACK can arm; the residue is the handler minting "mesh-00000001"
    # from a success flag (its meshtastic_handler twin excludes bool).
    with _http(True):
        mq.send_text("hi", destination="!aabb0001", source="rns")

    ids = [e.id for e in dc.get_singleton().recent()]
    assert "mesh-00000001" not in ids
    assert mq._ack_tracker.pending_count() == 0


# ── re-review of the slice-3 fixes (non-author, 10-23) ────────────────

def test_a_meshcore_queue_row_ends_with_exactly_one_sent_on_its_own_id(
        tmp_path):
    # #9: through a REAL queue dispatch, not queue_send alone.
    from gateway.message_queue import PersistentMessageQueue
    h = _mc_handler()
    h._connected = True
    q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
    q.register_sender("meshcore", h.queue_send)
    row = q.enqueue({"message": "hello", "destination": "abcd"}, "meshcore",
                    source="rns")
    q.process_once()
    _mc_drain(h)
    q.stop_processing()

    states = [e.state.value for e in dc.get_singleton().history_for(row)]
    assert states.count("sent") == 1
    assert _legs()["rns>meshcore"]["15m"]["sent"] == 1


def test_a_payload_id_never_outranks_the_queue_rows_id():
    # #2: the queue recorded SENT on _queue_msg_id; a failure must land there.
    from gateway.delivery_legs import LEG_SOURCE_KEY

    class _Err:
        type, payload = "command_error", {"code_string": "ERR_TIMEOUT"}

        def is_error(self):
            return True
    h = _mc_handler()
    h._connected = True
    h.queue_send({"message": "x", "destination": "abcd", "id": "abc",
                  "_queue_msg_id": "q-row", LEG_SOURCE_KEY: "rns"})

    _mc_drain(h, send_evt=_Err())

    assert dc.get_singleton().history_for("q-row")
    assert not dc.get_singleton().history_for("abc")


def test_meshcore_synthesized_ids_are_collision_free(monkeypatch):
    # #3: the R5 fix had to reach every copy of the pattern.
    import gateway.delivery_legs as dl
    import gateway.meshcore_handler as mch
    monkeypatch.setattr(dl.time, "time", lambda: 1792780000.123)
    monkeypatch.setattr(mch.time, "time", lambda: 1792780000.123)
    mch._record_tx("dropped", None, "non_retriable_error", "not connected")
    mch._record_tx("dropped", None, "non_retriable_error", "not connected")

    ids = [e.id for e in dc.get_singleton().recent()]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_lxmf_synthesized_ids_are_collision_free(bridge, monkeypatch):
    # #4: two direct send_to_rns calls in one millisecond (a fan-out).
    import sys
    import gateway.delivery_legs as dl
    from tests.test_rns_bridge import TestSynAckCallbackSymmetry as _Sym
    from utils.tx_guard import allow_rns_egress
    monkeypatch.setattr(dl.time, "time", lambda: 1792780000.123)
    fake_rns, fake_lxmf, _ = _Sym._fake_rns_lxmf_modules()
    _Sym()._prime_bridge_for_send(bridge)
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            allow_rns_egress():
        bridge.send_to_rns("a", b"\xab" * 16, source="meshtastic")
        bridge.send_to_rns("b", b"\xcd" * 16, source="meshtastic")

    ids = [e.id for e in dc.get_singleton().recent() if e.state.value == "sent"]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_enqueue_message_does_not_store_its_source_twice(tmp_path):
    # #6: kwargs["source"] must become the leg stamp only, not also a raw
    # "source" key handed to every sender.
    from gateway.delivery_legs import LEG_SOURCE_KEY
    from gateway.message_queue import PersistentMessageQueue
    q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
    seen = []
    q.register_sender("meshtastic", lambda p: seen.append(p) or True)

    class _Host:
        _persistent_queue = q
    from gateway.bridge_send_mixin import BridgeSendMixin
    BridgeSendMixin.enqueue_message(_Host(), "hi", "!aabb0001",
                                    dest_type="meshtastic", source="rns")
    q.process_once()
    q.stop_processing()

    assert seen and seen[0].get(LEG_SOURCE_KEY) == "rns"
    assert "source" not in seen[0]
