"""A TUI send records the radio's packet id per chunk, on the channel it left on.

Deferred ``tui-send-records-radio-packet-id-2026-11-14`` (BIC-2 review r2 MED):
``messaging.send_message`` (the TUI's Messaging > Send path) stored its rows
with NO packet id — its direct send went through ``send_text_direct``, the
bool wrapper that throws the id away. Since the fold decides on the wire id
(``inbox_api._fold_duplicates`` (b): an id'd echo is hidden ONLY by its own
send's id), the radio's uplink echo of a TUI send showed as a SECOND row in
/inbox. Two more facts the row got wrong, found while pinning this:

  * the row's ``channel`` was the CALLER's 0, while a broadcast leaves on index
    ``channel if channel > 0 else 1`` — the echo carries the wire index, the
    fold keys sends on (channel, text), so even an id could not pair them;
  * a multi-chunk send flagged only the LAST row delivered (INBOX-T F12).

What leaves on the air is unchanged here (same index, same chunks); only what
the store says about it is corrected. CLI-fallback and gateway sends cannot
return an id: their rows keep ``packet_id`` None (a duplicate, never a hide).
"""
from unittest.mock import MagicMock, patch

import pytest

from commands import messaging
from commands.base import CommandResult
from utils import inbox_api


@pytest.fixture(autouse=True)
def _own_store(tmp_path, monkeypatch):
    monkeypatch.setattr(messaging, "_get_db_path", lambda: tmp_path / "messages.db")


def _no_gateway():
    gw = MagicMock()
    gw.get_status.return_value = CommandResult.fail("gateway not running")
    return gw


def _send(text, ids, destination=None):
    """Run the TUI send with the HTTP direct path answering ``ids`` in turn.

    Both senders are patched — the pre-fix code calls the bool wrapper, and
    a real call would reach for a radio. The CLI runner is a refusing spy:
    these tests must never fall through to a subprocess."""
    ids = list(ids)
    calls = []

    def with_id(text, **kw):
        calls.append((text, kw))
        return ids.pop(0)

    cli = MagicMock(side_effect=AssertionError("CLI fallback reached"))
    with patch.object(messaging, "cmd_gateway", _no_gateway()), \
         patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
               side_effect=with_id), \
         patch("gateway.meshtastic_protobuf_client.send_text_direct",
               side_effect=lambda text, **kw: with_id(text, **kw) is not None), \
         patch("commands.meshtastic._run_command", cli):
        res = messaging.send_message(text, destination=destination, network="meshtastic")
    return res, calls


def _rows():
    conn = messaging._init_db()
    try:
        conn.row_factory = None
        return conn.execute("SELECT content, channel, delivered, packet_id FROM messages "
                            "WHERE from_id = 'local' ORDER BY id").fetchall()
    finally:
        conn.close()


def test_a_tui_broadcast_row_keeps_the_radios_packet_id():
    res, calls = _send("Aloha", [0x1A2B3C4D])
    assert res.success and res.data["delivered"] is True
    assert _rows() == [("Aloha", 1, 1, 0x1A2B3C4D)]


def test_the_row_says_the_channel_index_the_packet_left_on():
    # the wire index is what the echo carries; the send path chose it
    _, calls = _send("Aloha", [0x1A2B3C4D])
    (_, kw), = calls
    assert _rows()[0][1] == kw["channel_index"]


def test_every_chunk_row_carries_its_own_id_and_delivered():
    text = "word " * 120                      # > one broadcast packet
    res, calls = _send(text, [0x100 + i for i in range(10)])
    rows = _rows()
    assert len(rows) == len(calls) > 1
    assert [r[3] for r in rows] == [0x100 + i for i in range(len(rows))]
    assert all(r[2] == 1 for r in rows)                       # F12: not only the last
    assert [r[0] for r in rows] == [c[0] for c in calls]      # row i = chunk i on air


def test_a_tui_dm_row_keeps_its_id_on_channel_0():
    _send("hi", [0x77], destination="!a2e95ba4")
    assert _rows() == [("hi", 0, 1, 0x77)]


def test_the_echo_of_a_tui_send_folds_into_one_radio_echo_row():
    """END of the deferred check: TUI send + its id'd echo -> one row."""
    _send("Aloha", [0x1A2B3C4D])
    messaging.store_incoming(from_id="!32962f10", content="Aloha", channel=1,
                             packet_id=0x1A2B3C4D)
    with patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_api, "_attach_heard", return_value=None):
        out = inbox_api.read_inbox()
    msgs = [m for t in out["threads"] for m in t["messages"]]
    assert [(m["direction"], m["state"]) for m in msgs] == [("out", "radio_echo")]


def test_control_a_strangers_same_text_with_another_id_stays_two_rows():
    _send("Aloha", [0x1A2B3C4D])
    messaging.store_incoming(from_id="!0000beef", content="Aloha", channel=1,
                             packet_id=0x0BADF00D)
    with patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_api, "_attach_heard", return_value=None):
        out = inbox_api.read_inbox()
    msgs = [m for t in out["threads"] for m in t["messages"]]
    assert sorted(m["direction"] for m in msgs) == ["in", "out"]


def test_a_refused_direct_send_records_no_id():
    """The radio refused (id None) and the CLI fallback also fails: the row
    stays id-less and undelivered — never an invented id."""
    cli = MagicMock(return_value=CommandResult.fail("no radio"))
    with patch.object(messaging, "cmd_gateway", _no_gateway()), \
         patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
               return_value=None), \
         patch("gateway.meshtastic_protobuf_client.send_text_direct",
               return_value=False), \
         patch("commands.meshtastic._run_command", cli):
        res = messaging.send_message("Aloha", network="meshtastic")
    assert res.data["delivered"] is False
    assert _rows() == [("Aloha", 1, 0, None)]   # aimed at index 1; nothing invented


def test_a_partial_send_keeps_the_chunks_that_left():
    """Review r1 (important): chunk 3 refused after 1 and 2 went out — rows 1
    and 2 keep their ids and delivered; only row 3 stays unsent."""
    cli = MagicMock(return_value=CommandResult.fail("no radio"))
    ids = iter([0x101, 0x102, None, None, None])
    with patch.object(messaging, "cmd_gateway", _no_gateway()), \
         patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
               side_effect=lambda text, **kw: next(ids)), \
         patch("gateway.meshtastic_protobuf_client.send_text_direct",
               side_effect=lambda text, **kw: next(ids) is not None), \
         patch("commands.meshtastic._run_command", cli):
        res = messaging.send_message("word " * 120, network="meshtastic")
    rows = _rows()
    assert len(rows) >= 3 and res.data["delivered"] is False
    assert [(r[2], r[3]) for r in rows[:3]] == [(1, 0x101), (1, 0x102), (0, None)]


def test_a_broadcast_to_ffffffff_is_not_stored_as_a_dm():
    _send("Aloha", [0x55], destination="!ffffffff")
    conn = messaging._init_db()
    try:
        (is_dm, ch), = conn.execute("SELECT is_dm, channel FROM messages").fetchall()
    finally:
        conn.close()
    assert (is_dm, ch) == (0, 1)


def test_a_gateway_send_marks_every_chunk_on_the_callers_channel():
    """Fable r2 (mutation M7/M9): the gateway path records each aired chunk
    delivered, on the channel it was HANDED (it returns no id)."""
    gw = MagicMock()
    gw.get_status.return_value = CommandResult.ok(
        "ok", data={"running": True, "meshtastic_connected": True})
    gw.send_to_meshtastic.return_value = CommandResult.ok("sent")
    with patch.object(messaging, "cmd_gateway", gw), \
         patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
               side_effect=AssertionError("direct path reached")), \
         patch("commands.meshtastic._run_command",
               MagicMock(side_effect=AssertionError("CLI fallback reached"))):
        res = messaging.send_message("word " * 120, network="meshtastic", channel=0)
    rows = _rows()
    assert res.data["delivered"] is True and len(rows) > 1
    assert all((r[1], r[2], r[3]) == (0, 1, None) for r in rows)
    assert [c.args[2] for c in gw.send_to_meshtastic.call_args_list] == [0] * len(rows)


@pytest.mark.parametrize("bogus", [0, 2 ** 40, -5])
def test_an_impossible_radio_id_is_stored_as_unknown(bogus):
    """Fable r2 (mutation M11): the id the sender returns goes through the
    store's validator — an impossible id is unknown, never a join key."""
    _send("Aloha", [bogus])
    assert _rows()[0][3] is None
