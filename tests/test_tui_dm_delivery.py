"""BIC-2 DM slice 1: the TUI shows each DM's delivery verdict, read from
the map daemon (the witness lives in the map's process). Unreachable is
UNKNOWN, never "no DMs" (hfm #2)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from handlers.messaging import (  # noqa: E402
    DM_DELIVERY_SHOWN, MessagingHandler, dm_delivery_lines,
)


def _doc(msgs, **top):
    return {"state": "ok", "dm_witness": {"line": "DM acks visible on: LongFast — seen here in the last 30 min",
                                          "late_acks": 0},
            "threads": [{"kind": "dm", "network": "meshtastic", "partner": "!b03da894", "messages": msgs},
                        {"kind": "channel", "partner": None,
                         "messages": [{"direction": "out", "ts": "2026-11-25T10:00:00+00:00",
                                       "content": "bcast", "state": "radio_echo"}]}],
            **top}


def _out(ts, detail=None, state="handed_off"):
    m = {"direction": "out", "ts": ts, "content": "Wx", "state": state}
    if detail:
        m["delivery"] = {"state": "delivered", "detail": detail}
    return m


def test_unreachable_map_is_unknown_never_empty():
    (line,) = dm_delivery_lines((None, "ConnectionRefusedError"))
    assert line.startswith("UNKNOWN") and "ConnectionRefusedError" in line


def test_unavailable_inbox_is_unknown():
    (line,) = dm_delivery_lines(({"state": "unavailable", "error": "store locked"}, None))
    assert line.startswith("UNKNOWN") and "store locked" in line


def test_rows_show_the_maps_verdict_newest_first_dms_only():
    lines = dm_delivery_lines((_doc([
        _out("2026-11-25T01:00:00+00:00", "delivered — acked by the destination in 1.9 s"),
        _out("2026-11-25T02:00:00+00:00", "no ack seen: window closed (sent 3×)")]), None))
    assert lines[0].startswith("DM acks visible on: LongFast")
    rows = [ln for ln in lines if "→" in ln]
    assert len(rows) == 2 and "window closed" in rows[0] and "delivered" in rows[1]
    assert not any("bcast" in ln for ln in lines)


def test_witness_line_missing_is_said():
    doc = _doc([])
    doc.pop("dm_witness")
    assert dm_delivery_lines((doc, None))[0].startswith("DM acks: UNKNOWN")


def test_delivery_error_and_late_acks_are_shown():
    doc = _doc([_out("2026-11-25T01:00:00+00:00", "d")], delivery_error="journal unreadable: x")
    doc["dm_witness"]["late_acks"] = 2
    text = "\n".join(dm_delivery_lines((doc, None)))
    assert "journal unreadable" in text and "2 ack(s) arrived after" in text


def test_a_send_without_a_radio_id_says_so():
    lines = dm_delivery_lines((_doc([_out("2026-11-25T01:00:00+00:00")]), None))
    assert any("no delivery verdict" in ln for ln in lines)


def test_long_history_is_capped_and_says_so():
    msgs = [_out(f"2026-11-25T{h:02d}:00:00+00:00", "d") for h in range(DM_DELIVERY_SHOWN + 3)]
    lines = dm_delivery_lines((_doc(msgs), None))
    assert sum("→" in ln for ln in lines) == DM_DELIVERY_SHOWN
    assert lines[-1].startswith("… 3 older")


def test_menu_item_is_wired():
    h = MessagingHandler.__new__(MessagingHandler)
    assert callable(getattr(h, "_messaging_dm_delivery"))
    import inspect
    src = inspect.getsource(MessagingHandler._messaging_menu)
    assert '"dmdeliv"' in src and "_messaging_dm_delivery" in src


def test_r1_f7_a_map_503_is_the_inbox_state_not_unreachable():
    import io
    import json
    import urllib.error
    from unittest.mock import patch
    from handlers import messaging as tui_msg
    body = io.BytesIO(json.dumps({"state": "unavailable", "error": "store locked"}).encode())
    err = urllib.error.HTTPError(tui_msg.INBOX_URL, 503, "Service Unavailable", {}, body)
    with patch("urllib.request.urlopen", side_effect=err):
        fetched = tui_msg._fetch_inbox()
    (line,) = dm_delivery_lines(fetched)
    assert "store locked" in line and "not reachable" not in line


def test_r1_f8_only_meshtastic_dms_are_listed():
    doc = _doc([_out("2026-11-25T01:00:00+00:00", "d")])
    doc["threads"].append({"kind": "dm", "network": "rns", "partner": "abcd",
                           "messages": [{"direction": "out", "ts": "2026-11-25T03:00:00+00:00",
                                         "content": "lxmf", "state": "handed_off"}]})
    doc["threads"][0]["network"] = "meshtastic"
    assert not any("lxmf" in ln for ln in dm_delivery_lines((doc, None)))
