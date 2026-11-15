"""BIC-2 step 1 — the inbox joins one send to its copies on the radio's PACKET ID.

INBOX-T findings (`.claude/research/landscape_2026_11_09.md` §C):
  F1  ``_fold_duplicates`` matched an echo on channel + text + <= 10 s, with no
      sender and no wire fact: a STRANGER posting the same text on the same
      channel inside the window (an MQTT-heard row has no SNR) vanished, and
      my send was upgraded to ``radio_echo`` on someone else's packet.
  F2  ``/api/inbox`` rows carried no ``packet_id`` — the heard-by join had
      nothing to join on.
  F3  ``send()`` treated a packet id of ``0`` / ``False`` as handed off.

The cure is a wire fact (memory: composable — decide on a slot/identity, never
text): received rows keep the radio's packet id, and when BOTH rows carry one
the fold matches on the id alone. A row with no id (stored before this change)
keeps the legacy text/time match — those age out of the store.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils import inbox_api
from utils import message_listener as ml


def _db(**kw):
    base = {"id": 1, "timestamp": "2026-10-22 03:21:08", "network": "meshtastic",
            "from_id": "!6201ce25", "to_id": None, "content": "Hey there ",
            "channel": 1, "is_dm": False, "snr": None, "rssi": None,
            "delivered": True, "packet_id": None}
    base.update(kw)
    return base


# ── F1: the fold decides on the packet id ────────────────────────────────

def test_a_strangers_same_text_with_its_own_packet_id_is_never_folded():
    rows = [_db(id=1, from_id="local", packet_id=0x1001),
            _db(id=2, from_id="!0000beef", packet_id=0x2002)]   # MQTT-heard, no SNR
    (t,) = inbox_api.build_threads(rows)
    assert [(m["direction"], m["state"]) for m in t["messages"]] == \
        [("out", "handed_off"), ("in", "received")]


def test_the_echo_with_my_packet_id_folds_even_past_the_text_window():
    # the id is the proof; a slow uplink (30 s) does not un-prove it
    rows = [_db(id=1, from_id="local", packet_id=0x1001),
            _db(id=2, packet_id=0x1001, timestamp="2026-10-22 03:21:38")]
    (t,) = inbox_api.build_threads(rows)
    assert [(m["direction"], m["state"]) for m in t["messages"]] == [("out", "radio_echo")]


def test_one_echo_upgrades_only_the_send_that_owns_its_id():
    # two identical sends in one second: the echo of the SECOND must not
    # confirm the first
    rows = [_db(id=1, from_id="local", packet_id=0x1001),
            _db(id=2, from_id="local", packet_id=0x1002),
            _db(id=3, packet_id=0x1002)]
    (t,) = inbox_api.build_threads(rows)
    assert [(m["id"], m["state"]) for m in t["messages"]] == \
        [(1, "handed_off"), (2, "radio_echo")]


def test_legacy_rows_without_ids_keep_the_old_text_match():
    rows = [_db(id=1, from_id="local"), _db(id=2)]
    (t,) = inbox_api.build_threads(rows)
    assert [m["state"] for m in t["messages"]] == ["radio_echo"]


def test_an_id_on_only_one_side_never_falls_back_to_text():
    # the received row carries a wire id that is not my send's → it is a
    # different packet, even though my (pre-step-1) send has no id to compare
    rows = [_db(id=1, from_id="local"), _db(id=2, packet_id=0x2002)]
    (t,) = inbox_api.build_threads(rows)
    assert [m["state"] for m in t["messages"]] == ["handed_off", "received"]


def test_encrypted_leg_twins_with_different_ids_are_two_messages():
    rows = [_db(id=1, from_id="!851a9fe7", content="ola", packet_id=0x3003),
            _db(id=2, from_id="!851a9fe7", content="ola", to_id="", channel=0,
                is_dm=True, packet_id=0x3004)]
    assert sum(t["count"] for t in inbox_api.build_threads(rows)) == 2


def test_encrypted_leg_twin_with_the_same_id_is_dropped():
    rows = [_db(id=1, from_id="!851a9fe7", content="ola", packet_id=0x3003),
            _db(id=2, from_id="!851a9fe7", content="ola", to_id="", channel=0,
                is_dm=True, packet_id=0x3003)]
    (t,) = inbox_api.build_threads(rows)
    assert t["key"] == "meshtastic:channel:1" and t["count"] == 1


# ── F2: the row says its packet id ────────────────────────────────────────

def test_every_message_row_carries_its_packet_id_or_none():
    rows = [_db(id=1, from_id="local", packet_id=0x79C4A807),
            _db(id=2, from_id="!0000beef", content="other")]
    (t,) = inbox_api.build_threads(rows)
    assert [m["packet_id"] for m in t["messages"]] == [0x79C4A807, None]


# ── F3: only a real packet id is a hand-off ───────────────────────────────

@pytest.mark.parametrize("bogus", [0, False, -5, 2 ** 32, "4242"])
def test_a_bogus_packet_id_is_not_a_hand_off(bogus):
    stored = []
    with patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
               lambda **k: bogus), \
         patch.object(inbox_api.messaging, "store_outgoing",
                      lambda **k: stored.append(k) or SimpleNamespace(success=True)):
        status, body = inbox_api.send({"text": "x", "dest_num": None,
                                       "to_id": None, "channel": 0})
    assert status == 502 and body["state"] == "not_sent"
    assert stored[0]["delivered"] is False and stored[0]["packet_id"] is None


# ── the listener legs store the wire id ───────────────────────────────────

def _capture_store():
    seen = []
    return seen, (lambda **k: seen.append(k) or SimpleNamespace(success=True))


def test_tcp_leg_stores_the_packets_id():
    seen, fake = _capture_store()
    lst = ml.MessageListener(store_messages=True)
    pkt = {"id": 0x79C4A807, "fromId": "!a1b2c3d4", "toId": "^all", "channel": 1,
           "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hi"}}
    with patch("commands.messaging.store_incoming", fake):
        lst._handle_text_message(pkt, pkt["decoded"], "!a1b2c3d4")
    assert seen and seen[0]["packet_id"] == 0x79C4A807


def _mqtt(message_id):
    import datetime
    return SimpleNamespace(message_id=message_id, from_id="!a1b2c3d4", to_id="^all",
                           text="hi", channel=1, snr=None, rssi=None, hop_start=3,
                           timestamp=datetime.datetime.now())


@pytest.mark.parametrize("message_id, expect", [
    ("2042931207", 2042931207),          # the JSON leg's str(id)
    ("1731600000.123", None),            # the decoder's time.time() fallback
    ("", None),                          # encrypted leg with no id
    ("abc", None),
])
def test_mqtt_leg_stores_only_a_real_wire_id(message_id, expect):
    seen, fake = _capture_store()
    lst = ml.MessageListener(store_messages=True)
    with patch("commands.messaging.store_incoming", fake):
        lst._on_mqtt_message(_mqtt(message_id))
    assert seen and seen[0]["packet_id"] == expect


# ── one packet stored twice → one row (operator 11-14 20:12: "Oops doubled") ──
# Rows 6087/6088, 6089/6090, 6095/6096: this radio's own texts, sent from
# another client, stored twice 2-8 s apart. With ids kept, the wire fact
# (sender, packet id) merges them; without an id nothing does.

def test_one_packet_stored_twice_shows_once():
    rows = [_db(id=6089, content="Oops doubled", packet_id=0x4004),
            _db(id=6090, content="Oops doubled", packet_id=0x4004,
                timestamp="2026-10-22 03:21:16")]
    (t,) = inbox_api.build_threads(rows)
    assert [m["id"] for m in t["messages"]] == [6089]


def test_same_id_from_two_senders_is_two_messages():
    # a packet id is unique per SENDER, not per mesh
    rows = [_db(id=1, from_id="!0000beef", packet_id=0x4004),
            _db(id=2, from_id="!0000cafe", packet_id=0x4004)]
    assert sum(t["count"] for t in inbox_api.build_threads(rows)) == 2


def test_identical_text_without_ids_is_never_merged():
    # no wire fact → a real resend and a storage double look the same; keep both
    rows = [_db(id=1, content="Oops doubled"),
            _db(id=2, content="Oops doubled", timestamp="2026-10-22 03:21:16")]
    assert sum(t["count"] for t in inbox_api.build_threads(rows)) == 2


def test_two_copies_of_my_echo_fold_into_one_send():
    rows = [_db(id=1, from_id="local", packet_id=0x1001),
            _db(id=2, packet_id=0x1001),
            _db(id=3, packet_id=0x1001, timestamp="2026-10-22 03:21:11")]
    (t,) = inbox_api.build_threads(rows)
    assert [(m["id"], m["state"]) for m in t["messages"]] == [(1, "radio_echo")]


def test_dedupe_is_counted_as_folded():
    rows = [_db(id=1, packet_id=0x4004), _db(id=2, packet_id=0x4004)]
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=__import__("commands.base", fromlist=["x"])
                      .CommandResult.ok("ok", data={"messages": rows})), \
         patch("utils.message_listener.get_listener_status", return_value={}):
        out = inbox_api.read_inbox()
    assert (out["message_count"], out["shown_count"], out["folded_count"]) == (2, 1, 1)
