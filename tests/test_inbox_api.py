"""MOC Inbox (BIC-1): one thread list over the messages store (2026-10-13).

Pins the honesty rules the browser inbox exists to keep:
  * an unreadable store reads ``unavailable`` — never an empty inbox;
  * a sent message reads ``handed_off`` / ``not_sent`` — never "delivered";
  * a reply goes out on the SAME Meshtastic channel the thread shows
    (``messaging.send_message`` maps channel 0 to index 1 — not reused);
  * RNS threads are read-only until the gateway send channel exists, and say so.
The HTTP leg runs through a REAL server and the real handler; only the
transmitter and the store are replaced by recorders.
"""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from commands.base import CommandResult
from utils import inbox_api
from utils.map_http_handler import MapRequestHandler


def _row(**kw):
    base = {"id": 1, "timestamp": "2026-10-13T10:00:00", "network": "meshtastic",
            "from_id": "!a1b2c3d4", "to_id": None, "content": "hi", "channel": 0,
            "is_dm": False, "snr": 5.5, "rssi": -90, "delivered": True}
    base.update(kw)
    return base


# ── grouping ──────────────────────────────────────────────────────────────

def test_meshtastic_broadcasts_group_by_channel_and_dms_by_partner():
    rows = [
        _row(id=1, content="ch0 a"),
        _row(id=2, from_id="!0000beef", content="ch0 b", timestamp="2026-10-13T10:01:00"),
        _row(id=3, channel=2, content="ch2"),
        _row(id=4, to_id="!deadbeef", is_dm=True, content="dm in"),
        _row(id=5, from_id="local", to_id="!a1b2c3d4", is_dm=True, content="dm out",
             timestamp="2026-10-13T10:05:00"),
    ]
    threads = {t["key"]: t for t in inbox_api.build_threads(rows)}
    assert set(threads) == {"meshtastic:channel:0", "meshtastic:channel:2",
                            "meshtastic:!a1b2c3d4"}
    # dm in (from !a1b2c3d4 to us) and dm out (to !a1b2c3d4) are ONE conversation
    dm = threads["meshtastic:!a1b2c3d4"]
    assert [m["content"] for m in dm["messages"]] == ["dm in", "dm out"]
    assert dm["kind"] == "dm" and dm["partner"] == "!a1b2c3d4"
    ch0 = threads["meshtastic:channel:0"]
    assert ch0["kind"] == "channel" and ch0["channel"] == 0
    assert [m["content"] for m in ch0["messages"]] == ["ch0 a", "ch0 b"]


def test_broadcast_markers_all_mean_channel_not_a_partner_named_all():
    for marker in (None, "", "^all", "!ffffffff"):
        (t,) = inbox_api.build_threads([_row(to_id=marker)])
        assert t["key"] == "meshtastic:channel:0", marker


def test_rns_rows_key_on_the_peer_hash_both_directions():
    peer = "ab" * 16
    rows = [_row(network="rns", from_id=peer, to_id=None, content="lxmf in"),
            _row(network="rns", from_id="local", to_id=peer, content="lxmf out",
                 timestamp="2026-10-13T11:00:00")]
    (t,) = inbox_api.build_threads(rows)
    assert t["key"] == f"rns:{peer}" and t["kind"] == "dm"
    assert [m["direction"] for m in t["messages"]] == ["in", "out"]


def test_threads_sort_newest_first_and_carry_a_preview():
    rows = [_row(id=1, content="old", timestamp="2026-10-13T09:00:00"),
            _row(id=2, channel=3, content="new", timestamp="2026-10-13T12:00:00")]
    threads = inbox_api.build_threads(rows)
    assert [t["preview"] for t in threads] == ["new", "old"]
    assert threads[0]["last_ts"] == "2026-10-13T12:00:00+00:00"


def test_outbound_state_is_handoff_never_delivered():
    rows = [_row(id=1, from_id="local", to_id=None, delivered=True),
            _row(id=2, from_id="local", to_id=None, delivered=False),
            _row(id=3)]
    msgs = inbox_api.build_threads(rows)[0]["messages"]
    states = {m["id"]: m["state"] for m in msgs}
    assert states == {1: "handed_off", 2: "not_sent", 3: "received"}
    assert "delivered" not in json.dumps(states)


def test_sendability_is_declared_per_thread():
    rows = [_row(), _row(network="rns", from_id="cd" * 16)]
    by_net = {t["network"]: t for t in inbox_api.build_threads(rows)}
    assert by_net["meshtastic"]["can_send"] is True
    assert by_net["rns"]["can_send"] is False
    assert "gateway" in by_net["rns"]["send_blocked_reason"].lower()


# ── tri-state read ────────────────────────────────────────────────────────

def test_unreadable_store_is_unavailable_not_empty():
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.fail("database is locked")):
        out = inbox_api.read_inbox()
    assert out["state"] == "unavailable"
    assert "locked" in out["error"]
    assert "threads" not in out  # no valid-looking empty list beside the error


def test_store_that_raises_is_unavailable_too():
    with patch.object(inbox_api.messaging, "get_messages", side_effect=OSError("disk")):
        out = inbox_api.read_inbox()
    assert out["state"] == "unavailable"


def test_empty_store_is_ok_with_zero_threads():
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.ok("0", data={"messages": []})):
        out = inbox_api.read_inbox()
    out.pop("rx")  # receive coverage, pinned by its own tests below
    assert out == {"state": "ok", "threads": [], "message_count": 0,
                   "shown_count": 0, "folded_count": 0}


# ── send validation ───────────────────────────────────────────────────────

@pytest.mark.parametrize("data,why", [
    ({"network": "meshtastic", "destination": "^all", "text": ""}, "text"),
    ({"network": "meshtastic", "destination": "^all", "text": "x" * 201}, "text"),
    ({"network": "meshtastic", "destination": "!zzzz", "text": "hi"}, "destination"),
    ({"network": "meshtastic", "destination": "!123456789", "text": "hi"}, "destination"),
    ({"network": "meshtastic", "destination": "^all", "channel": 8, "text": "hi"}, "channel"),
    ({"network": "meshtastic", "destination": "^all", "channel": "1", "text": "hi"}, "channel"),
    ({"network": "rns", "destination": "ab" * 16, "text": "hi"}, "gateway"),
    ({"network": "meshcore", "destination": "x", "text": "hi"}, "network"),
    ("not a dict", "object"),
])
def test_validate_send_refuses(data, why):
    params, err = inbox_api.validate_send(data)
    assert params is None
    assert why in err.lower()


def test_validate_send_accepts_dm_and_channel():
    p, err = inbox_api.validate_send(
        {"network": "meshtastic", "destination": "!A1b2c3d4", "text": "hi"})
    assert err is None and p["dest_num"] == 0xA1B2C3D4 and p["to_id"] == "!a1b2c3d4"
    p, err = inbox_api.validate_send(
        {"network": "meshtastic", "destination": "^all", "channel": 2, "text": "net"})
    assert err is None and p["dest_num"] is None and p["channel"] == 2 and p["to_id"] is None


# ── HTTP leg, real server ─────────────────────────────────────────────────

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]


@pytest.fixture
def server(monkeypatch, tmp_path):
    from utils import web_auth
    store = web_auth.SessionStore(tmp_path / "web_sessions.json")
    monkeypatch.setattr(MapRequestHandler, "auth_store", store)
    monkeypatch.setattr(MapRequestHandler, "is_warming", False)
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", LAN)
    monkeypatch.setattr(MapRequestHandler, "collector", None, raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.store = store
    sent, stored = [], []

    def fake_tx(text, destination=None, channel_index=0, want_ack=True, **kw):
        sent.append({"text": text, "destination": destination,
                     "channel_index": channel_index, "want_ack": want_ack})
        return 4242

    def fake_store(**kw):
        stored.append(kw)
        return CommandResult.ok("stored", data={"message_id": 7})

    with patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id", fake_tx), \
         patch.object(inbox_api.messaging, "store_outgoing", fake_store):
        yield srv, sent, stored
    srv.shutdown()
    srv.server_close()


def _req(srv, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())
    finally:
        c.close()


JSON = {"Content-Type": "application/json"}


def _op(srv, ctype="application/json", origin=None):
    """Headers of a logged-in operator's browser on this page (BIC-3)."""
    from utils import web_auth
    token = srv.store.redeem(srv.store.start_pairing())
    return {"Content-Type": ctype,
            "Origin": origin or f"http://localhost:{srv.server_address[1]}",
            "Cookie": f"{web_auth.COOKIE_NAME}={token}"}


def test_send_without_an_operator_session_transmits_nothing(server):
    """BIC-3 in front of BIC-1: the white paper's admitted gap ("anyone who
    joins your LAN can open /inbox and key the radio") is closed."""
    srv, sent, stored = server
    body = json.dumps({"network": "meshtastic", "destination": "^all", "text": "x"})
    status, raw, _ = _req(srv, "POST", "/api/inbox/send", body,
                          {"Content-Type": "application/json",
                           "Origin": f"http://localhost:{srv.server_address[1]}"})
    assert status == 401 and json.loads(raw)["error"] == "operator login required"
    assert sent == [] and stored == []


def test_inbox_page_is_served(server):
    srv, _, _ = server
    status, body, headers = _req(srv, "GET", "/inbox")
    assert status == 200
    assert b"<title>" in body and b"/api/inbox" in body
    assert headers["Content-Type"].startswith("text/html")


def test_api_inbox_reports_unavailable_with_503(server):
    srv, _, _ = server
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.fail("no such table")):
        status, body, _ = _req(srv, "GET", "/api/inbox")
    assert status == 503
    assert json.loads(body)["state"] == "unavailable"


def test_reply_goes_out_on_the_thread_channel_and_is_recorded(server):
    srv, sent, stored = server
    body = json.dumps({"network": "meshtastic", "destination": "^all",
                       "channel": 0, "text": "hello net"})
    status, raw, _ = _req(srv, "POST", "/api/inbox/send", body, _op(srv))
    out = json.loads(raw)
    assert status == 200, out
    assert sent == [{"text": "hello net", "destination": None,
                     "channel_index": 0, "want_ack": False}]
    assert out["state"] == "handed_off" and out["recorded"] is True
    assert "delivered" not in out
    assert stored[0]["delivered"] is True and stored[0]["channel"] == 0
    assert stored[0]["to_id"] is None and stored[0]["network"] == "meshtastic"


def test_dm_asks_for_ack(server):
    srv, sent, _ = server
    body = json.dumps({"network": "meshtastic", "destination": "!a1b2c3d4", "text": "dm"})
    status, _, _ = _req(srv, "POST", "/api/inbox/send", body, _op(srv))
    assert status == 200
    assert sent[0]["destination"] == 0xA1B2C3D4 and sent[0]["want_ack"] is True


def test_failed_transmit_is_recorded_as_not_sent(server):
    srv, _, stored = server
    with patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
               lambda *a, **k: None):
        status, raw, _ = _req(srv, "POST", "/api/inbox/send", json.dumps(
            {"network": "meshtastic", "destination": "^all", "text": "x"}), _op(srv))
    assert status == 502
    assert json.loads(raw)["state"] == "not_sent"
    assert stored[0]["delivered"] is False


def test_hostile_page_cannot_send(server):
    srv, sent, stored = server
    body = json.dumps({"network": "meshtastic", "destination": "^all", "text": "x"})
    s1, _, _ = _req(srv, "POST", "/api/inbox/send", body,
                    _op(srv, origin="http://evil.example"))      # even with a session
    s2, _, _ = _req(srv, "POST", "/api/inbox/send", body,
                    _op(srv, ctype="text/plain"))                # the cross-site layer
    assert s1 in (401, 403) and s2 == 415
    assert sent == [] and stored == []


def test_rns_send_is_refused_not_faked(server):
    srv, sent, stored = server
    status, raw, _ = _req(srv, "POST", "/api/inbox/send", json.dumps(
        {"network": "rns", "destination": "ab" * 16, "text": "x"}), _op(srv))
    assert status == 400
    assert "gateway" in json.loads(raw)["error"].lower()
    assert sent == [] and stored == []


def test_timestamps_leave_as_utc_not_browser_local():
    """The store writes SQLite CURRENT_TIMESTAMP (UTC, no offset); a browser
    parses an offset-less ISO time as LOCAL — 10 h wrong on a Hawaii box."""
    (t,) = inbox_api.build_threads([_row(timestamp="2026-10-13T20:32:24")])
    assert t["messages"][0]["ts"] == "2026-10-13T20:32:24+00:00"
    assert t["last_ts"] == "2026-10-13T20:32:24+00:00"
    # an already-zoned value is passed through untouched
    (t,) = inbox_api.build_threads([_row(timestamp="2026-10-13T10:32:24-10:00")])
    assert t["messages"][0]["ts"] == "2026-10-13T10:32:24-10:00"


# ── receive coverage: the inbox says what this box can hear (2026-10-13) ──

def test_inbox_carries_what_the_listener_can_hear():
    st = {"state": "connected", "messages_received": 2, "channels_seen": {"3": 2},
          "subscription": "mqtt localhost:1883 — every channel this radio uplinks as JSON"}
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.ok("ok", data={"messages": []})), \
         patch("utils.message_listener.get_listener_status", return_value=st):
        out = inbox_api.read_inbox()
    assert out["state"] == "ok" and out["rx"]["channels_seen"] == {"3": 2}
    assert out["rx"]["subscription"].startswith("mqtt")


def test_unknown_listener_is_said_not_assumed():
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.ok("ok", data={"messages": []})), \
         patch("utils.message_listener.get_listener_status", side_effect=RuntimeError("x")):
        out = inbox_api.read_inbox()
    assert out["rx"] == {"state": "unknown"}


# ── one send, one row: radio echo + encrypted-leg duplicate (2026-10-21) ──
# Measured on the operator box's store 10-21 evening (operator's iPhone): every send
# appeared TWICE (the radio's own uplinked JSON read back as "received"), and
# a LongFast send ALSO appeared under the regional primary (index 0) because the local
# subscriber's encrypted leg re-decoded the same packet with no channel →
# stored channel 0, to_id "". Row shapes below are the live rows' shapes.

def _db(**kw):
    base = {"id": 1, "timestamp": "2026-10-22 03:21:08", "network": "meshtastic",
            "from_id": "!6201ce25", "to_id": None, "content": "Hey there ",
            "channel": 1, "is_dm": False, "snr": None, "rssi": None, "delivered": True}
    base.update(kw)
    return base


def test_own_radio_echo_folds_into_the_send_and_upgrades_its_state():
    rows = [_db(id=1460, from_id="local"),                    # the send
            _db(id=1459)]                                     # radio's JSON echo
    (t,) = inbox_api.build_threads(rows)
    assert t["key"] == "meshtastic:channel:1"
    assert [(m["direction"], m["state"]) for m in t["messages"]] == [("out", "radio_echo")]
    assert t["count"] == 1


def test_encrypted_leg_duplicate_is_not_filed_under_channel_zero():
    rows = [_db(id=1460, from_id="local"),
            _db(id=1459),                                     # JSON leg, ch 1
            _db(id=1458, to_id="", channel=0, is_dm=True),    # encrypted leg
            # a FOREIGN sender, same two-leg duplication (row 1451 shape)
            _db(id=1452, from_id="!851a9fe7", content="ola", snr=6.0, rssi=-80,
                timestamp="2026-10-22 03:11:11"),
            _db(id=1451, from_id="!851a9fe7", content="ola", to_id="", channel=0,
                is_dm=True, timestamp="2026-10-22 03:11:11")]
    threads = {t["key"]: t for t in inbox_api.build_threads(rows)}
    assert set(threads) == {"meshtastic:channel:1"}           # nothing on the primary
    assert [m["content"] for m in threads["meshtastic:channel:1"]["messages"]] == \
        ["ola", "Hey there "]


def test_echo_pairing_needs_same_channel_text_and_time():
    # same text from another node on another channel, or a minute later, is a
    # real message — never swallowed as an echo
    rows = [_db(id=1, from_id="local", content="Test", channel=3),
            _db(id=2, from_id="!0000beef", content="Test", channel=0),
            _db(id=3, content="Test", channel=3, timestamp="2026-10-22 03:22:30"),
            # a neighbour HEARD over the air (rx stats) saying the same thing
            # on the same channel in the same second is a real message
            _db(id=4, from_id="!0000beef", content="Test", channel=3, snr=4.0, rssi=-95)]
    out = {t["key"]: [(m["direction"], m["state"]) for m in t["messages"]]
           for t in inbox_api.build_threads(rows)}
    assert out["meshtastic:channel:0"] == [("in", "received")]
    assert out["meshtastic:channel:3"] == [("out", "handed_off"), ("in", "received"),
                                           ("in", "received")]


def test_an_unechoed_send_still_reads_unconfirmed():
    (t,) = inbox_api.build_threads([_db(from_id="local")])
    assert t["messages"][0]["state"] == "handed_off"


def test_a_send_that_never_reached_the_radio_is_never_echo_confirmed():
    (t,) = inbox_api.build_threads([_db(id=1, from_id="local", delivered=False), _db(id=2)])
    assert [m["state"] for m in t["messages"]] == ["not_sent", "received"]


def test_a_lone_encrypted_leg_row_is_kept():
    # no JSON twin → it is the only witness of that message; keep it
    (t,) = inbox_api.build_threads([_db(to_id="", channel=0, from_id="!0000beef")])
    assert t["count"] == 1


def test_encrypted_leg_copy_of_a_dm_is_dropped_too():
    rows = [_db(id=1, from_id="!851a9fe7", to_id="!6201ce25", is_dm=True, content="dm",
                snr=6.0, rssi=-80),
            _db(id=2, from_id="!851a9fe7", to_id="", channel=0, is_dm=True, content="dm")]
    (t,) = inbox_api.build_threads(rows)
    assert t["key"] == "meshtastic:!851a9fe7" and t["count"] == 1


def test_z_suffixed_timestamps_still_pair():
    rows = [_db(id=1, from_id="local", timestamp="2026-10-22T03:21:08Z"),
            _db(id=2, timestamp="2026-10-22T03:21:09Z")]
    (t,) = inbox_api.build_threads(rows)
    assert [m["state"] for m in t["messages"]] == ["radio_echo"]


def test_inbox_counts_say_what_was_folded():
    rows = [_db(id=1460, from_id="local"), _db(id=1459),
            _db(id=1458, to_id="", channel=0, is_dm=True)]
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.ok("ok", data={"messages": rows})), \
         patch("utils.message_listener.get_listener_status", return_value={}):
        out = inbox_api.read_inbox()
    assert (out["message_count"], out["shown_count"], out["folded_count"]) == (3, 1, 2)
