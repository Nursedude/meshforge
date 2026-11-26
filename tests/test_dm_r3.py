"""BIC-2 DM slice 1 -- Fable r3 (2026-11-25) folds, red on b5eb0612.

Operator 2026-11-25 (AskUserQuestion):
* "Cut from slice 1" -- the journal hints leave the inbox path: four review
  rounds found defects in that machinery, and since "journal = hint, never
  final" it could only add wording. The pure parser stays in box_truth_rx
  (MeshAnchor twin) for a later slice that earns it.
* "Delivered replaces it" -- a later destination ack replaces a kept
  destination NAK; delivered outranks any NAK whatever the poll timing.
"""
import inspect
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from commands import messaging as messaging_mod  # noqa: E402
from utils import dm_ack_witness, dm_delivery, inbox_api  # noqa: E402
from utils.dm_delivery import (  # noqa: E402
    REPLY_SLOP_S, ROW_DELIVERED, ROW_FAILED, ROW_LEFT_UNCONFIRMED, ROW_NO_ACK,
    RoutingReply, Send, Witness, classify, row_verdict,
)

ME = 0x6201CE25
P5 = 0xB03DA894
PID = 0xAB3C8AC6
T0 = 1_795_000_000.0
LIVE = Witness(since=T0 - 3600)


class TestCut:

    def test_row_verdict_takes_no_journal(self):
        assert "journal" not in inspect.signature(row_verdict).parameters

    def test_the_inbox_never_reads_a_journal(self):
        src = inspect.getsource(inbox_api._attach_delivery)
        assert "journal" not in src and "subprocess" not in src

    def test_the_cursor_cache_module_is_gone(self):
        with pytest.raises(ImportError):
            __import__("utils.dm_journal_cache")


class TestSelfDm:
    """r3 #1: self-DM is decided from the DM's OWN header (its uplinked
    ``from``), never from an ack's gateway -- a destination that is itself
    a gateway uplinks its own ack (gateway == from) on a bridged broker."""

    def test_a_gateway_destinations_own_uplinked_ack_is_delivery(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        LIVE, now=T0 + 3)
        assert v.state == ROW_DELIVERED

    def test_a_dm_to_our_own_radio_is_never_delivered(self):
        v = row_verdict(Send(T0, PID, ME, sender=ME), [RoutingReply(T0 + 0.02, ME, PID, "NONE")],
                        LIVE, now=T0 + 90)
        assert v.state == ROW_LEFT_UNCONFIRMED
        assert "own radio" in v.detail

    def test_unknown_sender_is_not_delivery_yet(self):
        """Inverted by r4 #1 (operator 11-26): with neither the header nor
        this box's own id, a self-DM is not ruled out -- never delivered."""
        v = row_verdict(Send(T0, PID, P5), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        LIVE, now=T0 + 3)
        assert v.state != ROW_DELIVERED

    def test_routing_reply_has_no_via_own(self):
        assert "via_own" not in RoutingReply.__dataclass_fields__


def test_witness_keeps_the_sender_of_each_uplinked_dm():
    w = dm_ack_witness.AckWitness()
    w.started(now=T0 - 60)
    from meshtastic.protobuf import mesh_pb2, mqtt_pb2
    pkt = mesh_pb2.MeshPacket()
    pkt.id, pkt.to = PID, P5
    setattr(pkt, "from", ME)
    pkt.encrypted = b"\x99" * 24
    pkt.pki_encrypted = True
    se = mqtt_pb2.ServiceEnvelope()
    se.packet.CopyFrom(pkt)
    se.channel_id, se.gateway_id = "PKI", "!6201ce25"
    w.observe("msh/2/e/PKI/!6201ce25", se.SerializeToString(), now=T0)
    assert w.snapshot(now=T0 + 1)["tx_from"][PID] == ME


class TestNakThenAck:
    """r3 #8: the verdict never depends on poll timing."""

    def test_classify_destination_ack_after_its_nak_is_delivered(self):
        (o,) = classify([Send(T0, PID, P5)], [RoutingReply(T0 + 2, P5, PID, "NO_CHANNEL"),
                                              RoutingReply(T0 + 6, P5, PID, "NONE")])
        assert o.status == "DELIVERED"

    def test_a_kept_destination_nak_is_replaced_by_its_later_ack(self, tmp_path, monkeypatch):
        db = tmp_path / "messages.db"
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
        monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)
        r = messaging_mod.store_outgoing("Wx", "meshtastic", "!b03da894", channel=0,
                                         delivered=True, packet_id=PID)
        mid = r.data["message_id"]

        class W:
            def __init__(self, replies):
                self.s = {"witness": Witness(T0 - 3600), "replies": replies, "tx": {},
                          "tx_from": {PID: ME}, "channels": {}, "decrypted": {}, "counts": {}}

            def snapshot(self, now=None):
                return self.s

        def read(now, replies):
            with patch.object(inbox_api.time, "time", return_value=now), \
                 patch("utils.message_listener.get_listener_status", return_value={}), \
                 patch("utils.inbox_legs.load_sections", return_value=None), \
                 patch.object(dm_ack_witness, "get_witness", return_value=W(replies)):
                out = inbox_api.read_inbox()
            return {m["id"]: m for t in out["threads"] for m in t["messages"]}[mid]
        sent = inbox_api._epoch(read(0, [])["ts"])
        nak = RoutingReply(sent + 2, P5, PID, "NO_CHANNEL")
        assert read(sent + 3, [nak])["delivery"]["state"] == ROW_FAILED          # a poll in between
        ack = RoutingReply(sent + 6, P5, PID, "NONE")
        assert read(sent + 9, [nak, ack])["delivery"]["state"] == ROW_DELIVERED
        assert messaging_mod.kept_dm([(mid, PID)])[0][mid]["state"] == ROW_DELIVERED


class TestR3Nits:

    def test_one_slop_constant_and_a_reply_just_before_the_stamp_counts(self):
        """r3 #10: the row is stamped AFTER the HTTP send returns."""
        assert REPLY_SLOP_S == 2
        v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 - 1.5, P5, PID, "NONE")],
                        LIVE, now=T0 + 3)
        assert v.state == ROW_DELIVERED

    def test_a_kept_our_radio_row_is_refused_on_read(self, tmp_path, monkeypatch):
        """r3 #10: no writer produces "said by our radio" any more (the
        journal decides nothing); an early-shape row is refused, counted."""
        db = tmp_path / "messages.db"
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
        r = messaging_mod.store_outgoing("Wx", "meshtastic", "!b03da894", channel=0,
                                         delivered=True, packet_id=PID)
        mid = r.data["message_id"]
        conn = messaging_mod._init_db()
        conn.execute("INSERT INTO dm_kept (message_id, packet_id, state, detail, reason, said_by, "
                     "latency_s, sent_n, kept_at) VALUES (?, ?, 'failed', 'x', 'MAX_RETRANSMIT', "
                     "'our radio', NULL, 0, 1.0)", (mid, PID))
        conn.commit()
        conn.close()
        assert messaging_mod.kept_dm([(mid, PID)]) == ({}, 1)

    def test_the_stored_base_never_carries_the_live_tail(self):
        """r3 #5, re-pinned by Fable r4 #5 (the old assertion -- the word
        "hint" absent -- pinned nothing): ``base`` is what the store keeps,
        ``detail`` = base + the live tail."""
        v = row_verdict(Send(T0, PID, P5, sender=ME),
                        [RoutingReply(T0 + 2, 0x514EAE28, PID, "NO_ROUTE"),
                         RoutingReply(T0 + 17, ME, PID, "MAX_RETRANSMIT")],
                        LIVE, tx_seen=2, now=T0 + 30)
        assert v.state == ROW_FAILED
        assert v.base == "failed: NO_ROUTE (said by !514eae28)"
        assert v.detail == v.base + v.tail and "gave up" in v.tail and "sent 2×" in v.tail


class TestMutantKillersR3:
    """The 5 survivors of the author's r3 mutation pass (baseline-gated)."""

    def _store(self, tmp_path, monkeypatch):
        db = tmp_path / "messages.db"
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
        monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)
        r = messaging_mod.store_outgoing("Wx", "meshtastic", "!b03da894", channel=0,
                                         delivered=True, packet_id=PID)
        return r.data["message_id"]

    def test_the_first_destination_failure_kept_is_not_replaced_by_another(self, tmp_path, monkeypatch):
        mid = self._store(tmp_path, monkeypatch)
        for reason in ("PKI_UNKNOWN_PUBKEY", "NO_CHANNEL"):
            messaging_mod.keep_dm([(mid, PID, {"state": ROW_FAILED, "detail": reason,
                                               "reason": reason, "said_by": "the destination"})])
        assert messaging_mod.kept_dm([(mid, PID)])[0][mid]["reason"] == "PKI_UNKNOWN_PUBKEY"

    def test_an_unparseable_sender_never_matches_an_unparseable_destination(self):
        (o,) = classify([Send(T0, PID, "junk")], [RoutingReply(T0 + 2, "junk", PID, "NONE")])
        assert o.status != "DELIVERED"

    def test_inbox_shows_the_kept_destination_failure_not_a_later_one(self, tmp_path, monkeypatch):
        mid = self._store(tmp_path, monkeypatch)
        rows = _reader(mid, tx_from={PID: ME})   # r5: an origin source is needed
        sent = inbox_api._epoch(rows(0, [])["ts"])
        n1 = RoutingReply(sent + 2, P5, PID, "PKI_UNKNOWN_PUBKEY")
        rows(sent + 3, [n1])
        d = rows(sent + 9, [n1, RoutingReply(sent + 6, P5, PID, "NO_CHANNEL")])["delivery"]
        assert d["reason"] == "PKI_UNKNOWN_PUBKEY" and d["kept"] is True

    def test_inbox_passes_the_dms_own_sender_to_the_judge(self, tmp_path, monkeypatch):
        """A DM to our own radio: tx_from (the DM's own header) says so."""
        db = tmp_path / "messages.db"
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
        r = messaging_mod.store_outgoing("Wx", "meshtastic", "!6201ce25", channel=0,
                                         delivered=True, packet_id=PID)
        mid = r.data["message_id"]
        rows = _reader(mid, tx_from={PID: ME})
        sent = inbox_api._epoch(rows(0, [])["ts"])
        d = rows(sent + 90, [RoutingReply(sent + 0.02, ME, PID, "NONE")])["delivery"]
        assert d["state"] != ROW_DELIVERED and "own radio" in d["detail"]

    def test_witness_only_envelopes_still_run_periodic_maintenance(self):
        from types import SimpleNamespace
        from monitoring.mqtt_subscriber import MQTTNodelessSubscriber, local_subscriber_config
        cfg = dict(local_subscriber_config(), ack_witness=True)
        s = MQTTNodelessSubscriber(config=cfg)
        calls = []

        class FW:
            def observe(self, *a, **k):
                pass
        with patch.object(MQTTNodelessSubscriber, "_ack_witness", staticmethod(lambda: FW())), \
                patch.object(s, "_periodic_maintenance", lambda: calls.append(1)):
            s._on_message(None, None, SimpleNamespace(topic="msh/2/e/PKI/!6201ce25", payload=b"x"))
        assert calls == [1]


def _reader(mid, tx_from=None):
    def read(now, replies):
        class W:
            def snapshot(self, now=None):
                return {"witness": Witness(T0 - 10 ** 9), "replies": replies, "tx": {},
                        "tx_from": dict(tx_from or {}), "channels": {}, "decrypted": {},
                        "counts": {}}
        with patch.object(inbox_api.time, "time", return_value=now), \
             patch("utils.message_listener.get_listener_status", return_value={}), \
             patch("utils.inbox_legs.load_sections", return_value=None), \
             patch.object(dm_ack_witness, "get_witness", return_value=W()):
            out = inbox_api.read_inbox()
        return {m["id"]: m for t in out["threads"] for m in t["messages"]}[mid]
    return read
