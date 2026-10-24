"""BIC-2 slice 2 — the persistent queue's lifecycle events carry their leg.

Before this slice every queued, retried or dead-lettered message counted
under ``?>dst`` — exactly the outage path, where a Mesh→RNS backlog sits in
the queue while the named ``meshtastic>rns`` leg reads quiet (review F5,
10-23). The origin rides the payload as ``_leg_source`` (the queue already
injects ``_queue_msg_id`` and ``_deferrals`` the same way; every sender and
the dedup hash read named keys only).

Seam: ``PersistentMessageQueue.enqueue(..., source=)`` → its lifecycle
events → ``delivery_counters.snapshot()["legs"]``.
"""
from __future__ import annotations

import pytest

from gateway import delivery_counters as dc
from gateway.message_queue import PersistentMessageQueue


@pytest.fixture(autouse=True)
def _isolate_delivery_counters(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHFORGE_DELIVERY_COUNTERS_DB",
                       str(tmp_path / "delivery.db"))
    dc._reset_singleton_for_tests()
    yield
    dc._reset_singleton_for_tests()


@pytest.fixture
def queue(tmp_path):
    q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
    yield q
    q.stop_processing()


def _legs():
    return dc.get_singleton().snapshot()["legs"]


def test_a_queued_message_counts_on_its_leg(queue):
    queue.enqueue({"message": "hi", "destination_hash": "ab" * 16}, "rns",
                  source="meshtastic")

    assert _legs()["meshtastic>rns"]["15m"]["queued"] == 1


def test_a_delivered_queue_row_counts_sent_on_the_same_leg(queue):
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshtastic")

    queue.mark_delivered(msg_id)

    assert _legs()["meshtastic>rns"]["15m"]["sent"] == 1


def test_exhausted_retries_count_failed_on_the_same_leg(queue):
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshtastic",
                           max_retries=1)

    queue.mark_failed(msg_id, "no path")   # retry_count 1 >= 1: dead letter

    win = _legs()["meshtastic>rns"]["15m"]
    assert win["failed"] == 1
    assert "?>rns" not in _legs()


def test_a_shed_row_counts_on_its_leg(queue):
    from gateway.message_queue import MessagePriority
    queue.enqueue({"text": "sheddable"}, "rns", source="meshtastic",
                  priority=MessagePriority.LOW)

    assert queue._shed_overflow(count=1) == 1

    assert _legs()["meshtastic>rns"]["15m"]["dropped_other"] == 1


def test_an_expired_row_counts_failed_on_its_leg(queue):
    from datetime import datetime, timedelta
    queue.register_sender("rns", lambda p: True, max_age_s=100)
    msg_id = queue.enqueue({"message": "x", "destination": "1"}, "rns",
                           source="meshtastic")
    with queue._get_connection() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                     ((datetime.now() - timedelta(seconds=200)).isoformat(),
                      msg_id))

    assert queue.expire_overdue() == 1

    assert _legs()["meshtastic>rns"]["15m"]["failed"] == 1


def test_a_dedup_drop_counts_on_its_leg(queue):
    payload = {"message": "same", "destination": "1"}
    queue.enqueue(payload, "rns", source="meshtastic")
    queue.enqueue(payload, "rns", source="meshtastic")

    assert _legs()["meshtastic>rns"]["15m"]["dropped_other"] == 1


def test_the_leg_source_does_not_change_the_dedup_hash(queue):
    # A row queued before this slice (no _leg_source) and the same message
    # queued after must still dedup against each other across a deploy.
    payload = {"message": "same", "destination": "1"}
    queue.enqueue(payload, "rns")

    assert queue.enqueue(payload, "rns", source="meshtastic") is None


def test_the_callers_payload_is_not_mutated(queue):
    payload = {"message": "hi"}
    queue.enqueue(payload, "rns", source="meshtastic")

    assert payload == {"message": "hi"}


def test_every_queue_lifecycle_record_names_a_source():
    # Source-shape guard: a new _dc.record() in the queue without source=
    # silently puts its rows back on ?>dst.
    import ast
    from pathlib import Path

    # The byte-locked outage mixin (MeshAnchor's counters take no source=
    # yet) names the leg through the queue's ``_leg_record_kwargs`` hook.
    def _names_leg(k):
        if k.arg == "source":
            return True
        v = k.value
        return (k.arg is None and isinstance(v, ast.Call)
                and isinstance(v.func, ast.Attribute)
                and v.func.attr == "_leg_record_kwargs")

    gw = Path(__file__).resolve().parents[1] / "src" / "gateway"
    missing = []
    for path in sorted(gw.glob("message_queue*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "record"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "_dc"
                    and not any(_names_leg(k) for k in node.keywords)):
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, ("queue lifecycle record without source=: "
                         + ", ".join(missing))


# ── the queued M→R send confirms on the row's leg ─────────────────────

from unittest.mock import MagicMock, patch  # noqa: E402

from tests.test_rns_bridge import bridge  # noqa: E402,F401 — fixture
from tests.test_rns_bridge import TestSynAckCallbackSymmetry as _Sym  # noqa: E402


@pytest.mark.parametrize("which,outcome", [("delivered", "confirmed"),
                                           ("failed", "failed")])
def test_a_queued_rns_send_confirms_on_the_rows_leg(bridge, which, outcome):
    import sys
    from utils.tx_guard import allow_rns_egress
    from gateway.delivery_legs import LEG_SOURCE_KEY
    fake_rns, fake_lxmf, fake_lxm = _Sym._fake_rns_lxmf_modules()
    _Sym()._prime_bridge_for_send(bridge)
    payload = {"message": "hi", "destination_hash": b"\xab" * 16,
               "_queue_msg_id": "row-7", LEG_SOURCE_KEY: "meshtastic"}
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            allow_rns_egress():
        assert bridge._queue_send_rns(payload) is True
    reg = (fake_lxm.register_delivery_callback if which == "delivered"
           else fake_lxm.register_failed_callback)
    reg.call_args[0][0](MagicMock(name="receipt"))

    assert _legs()["meshtastic>rns"]["15m"][outcome] == 1
    assert "?>rns" not in _legs()


# Producers that cannot know the origin. Each counts under ``?>dst``.
_SOURCELESS_ENQUEUE = {
    # OfflineSyncEngine.enqueue: a different queue class (category-keyed
    # telemetry sync), not the persistent message queue.
    ("offline_sync.py", "enqueue"),
}


def test_every_gateway_enqueue_names_a_source():
    import ast
    from pathlib import Path

    # ALL of src/, not just gateway/: the live end-state check after the
    # slice-2 deploy found the round-trip canary (src/lab/) enqueueing into
    # the gateway's queue DB sourceless, on moc at 08:23:05 (10-23).
    src = Path(__file__).resolve().parents[1] / "src"
    missing = []
    for path in sorted(src.rglob("*.py")):
        if path.name.startswith("message_queue"):
            continue      # the queue itself (requeue paths pass row source)
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "enqueue"
                        and not any(k.arg == "source" for k in node.keywords)
                        and (path.name, fn.name) not in _SOURCELESS_ENQUEUE):
                    missing.append(f"{path.name}:{node.lineno} in {fn.name}()")
    assert not missing, ("enqueue() without source= counts under '?>dst': "
                         + ", ".join(sorted(set(missing))))


# ── rns_xform rows: RNS-bound traffic, but their "sent" is a dispatch ──

def test_an_rns_xform_row_is_queued_on_its_rns_leg(queue):
    queue.enqueue({"content": "hi", "source_id": "!a"}, "rns_xform",
                  source="mqtt")

    assert _legs()["mqtt>rns"]["15m"]["queued"] == 1


def test_an_rns_xform_dispatch_is_not_counted_as_a_send(queue):
    # The replay runs the real pipeline; its send_to_rns records the real
    # SENT. Counting the dispatch too would double every spilled message.
    msg_id = queue.enqueue({"content": "hi", "source_id": "!a"}, "rns_xform",
                           source="mqtt")

    queue.mark_delivered(msg_id)

    assert "sent" not in _legs()["mqtt>rns"]["15m"]
    assert "?>?" not in _legs()


def test_an_expired_rns_xform_row_is_a_failure_on_its_rns_leg(queue):
    # It never reached send_to_rns: a real Mesh→RNS loss.
    from datetime import datetime, timedelta
    queue.register_sender("rns_xform", lambda p: True, max_age_s=100)
    msg_id = queue.enqueue({"content": "x", "source_id": "!a"}, "rns_xform",
                           source="mqtt")
    with queue._get_connection() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                     ((datetime.now() - timedelta(seconds=200)).isoformat(),
                      msg_id))

    assert queue.expire_overdue() == 1

    assert _legs()["mqtt>rns"]["15m"]["failed"] == 1


def test_a_replayed_spill_sends_on_its_origin_leg_without_changing_routing(
        bridge):
    import sys
    from utils.tx_guard import allow_rns_egress
    from gateway.delivery_legs import LEG_SOURCE_KEY
    bridge.config.rns.get_lxmf_destinations.return_value = [
        "522c4ac1d2f9964e03e3782ef5b0224c"]
    fake_rns, fake_lxmf, _ = _Sym._fake_rns_lxmf_modules()
    _Sym()._prime_bridge_for_send(bridge)
    seen = []
    real = bridge._process_mesh_to_rns

    def spy(msg):
        seen.append(msg.source_network)
        return real(msg)
    bridge._process_mesh_to_rns = spy
    payload = {"content": "hello", "source_id": "!aabb0042",
               "is_broadcast": True, LEG_SOURCE_KEY: "mqtt"}
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            allow_rns_egress():
        bridge._dispatch_rns_xform_spill(payload)

    assert seen == ["meshtastic"]                 # routing input unchanged
    assert _legs()["mqtt>rns"]["15m"]["sent"] == 1


# ── review of slice 2 (non-author, 10-23) ─────────────────────────────

def test_a_late_mark_failed_on_a_dead_lettered_row_is_not_a_second_loss(queue):
    # F5: the stale reset can dead-letter an in_progress row while a slow
    # worker is still sending; its late mark_failed must not count again.
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshtastic",
                           max_retries=1)
    queue.mark_failed(msg_id, "no path")         # dead letter: one loss
    queue.mark_failed(msg_id, "late worker")     # row already terminal

    assert _legs()["meshtastic>rns"]["15m"]["failed"] == 1
    assert dc.get_singleton().snapshot()["drop_reasons"][
        "retries_exhausted"] == 1


def test_live_mqtt_ingress_and_its_spill_count_on_the_same_leg(bridge):
    # F1: live MQTT M->R messages carry source_network="meshtastic" (a
    # routing input) and origin=MQTT; the spill was tagged "mqtt". One
    # ingress must not split across two legs.
    import sys
    from gateway.bridge_health import MessageOrigin
    from gateway.rns_bridge import BridgedMessage
    from utils.tx_guard import allow_rns_egress
    bridge.config.rns.get_lxmf_destinations.return_value = [
        "522c4ac1d2f9964e03e3782ef5b0224c"]
    fake_rns, fake_lxmf, _ = _Sym._fake_rns_lxmf_modules()
    _Sym()._prime_bridge_for_send(bridge)
    msg = BridgedMessage(source_network="meshtastic", source_id="!aabb0042",
                         destination_id=None, content="hi via mqtt",
                         is_broadcast=True, origin=MessageOrigin.MQTT)
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            allow_rns_egress():
        bridge._process_mesh_to_rns(msg)

    assert _legs()["mqtt>rns"]["15m"]["sent"] == 1
    assert "meshtastic>rns" not in _legs()


# ── re-review of the slice 2 fixes (non-author, 10-23) ────────────────

def test_a_late_failure_after_the_stale_reset_dead_lettered_the_row(queue):
    # The interleaving F5 names (re-review #8): a worker wedges past
    # STALE_TIMEOUT, cleanup_stale dead-letters the in_progress row (its
    # retries spent), then the slow worker returns a failure.
    from datetime import datetime, timedelta
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshtastic",
                           max_retries=1)
    old = (datetime.now() - timedelta(seconds=queue.STALE_TIMEOUT + 60))
    with queue._get_connection() as conn:
        conn.execute("UPDATE messages SET status = 'in_progress', "
                     "updated_at = ? WHERE id = ?", (old.isoformat(), msg_id))
    queue.cleanup_stale()                       # dead letter: one loss
    failed_before = queue._stats["failed"]

    queue.mark_failed(msg_id, "late worker")    # row already terminal

    assert _legs()["meshtastic>rns"]["15m"]["failed"] == 1
    assert queue._stats["failed"] == failed_before   # re-review #1


def test_the_queue_failed_stat_agrees_with_the_drop_count(queue):
    # Re-review #1: the queue's own loss counter doubled while the delivery
    # counter did not — two loss counters disagreeing.
    msg_id = queue.enqueue({"message": "hi"}, "rns", source="meshtastic",
                           max_retries=1)
    queue.mark_failed(msg_id, "no path")
    queue.mark_failed(msg_id, "late worker")

    assert queue._stats["failed"] == 1


@pytest.mark.parametrize("origin", ["mqtt", "MQTT_ENUM"])
def test_mqtt_origin_is_read_whether_enum_or_string(bridge, origin):
    # Re-review #5: an origin carried as a plain string must not silently
    # put MQTT ingress back on meshtastic>rns.
    from gateway.bridge_health import MessageOrigin
    from gateway.rns_bridge import BridgedMessage
    msg = BridgedMessage(source_network="meshtastic", source_id="!a",
                         destination_id=None, content="x", is_broadcast=True)
    msg.origin = MessageOrigin.MQTT if origin == "MQTT_ENUM" else origin

    assert bridge._leg_source(msg) == "mqtt"
