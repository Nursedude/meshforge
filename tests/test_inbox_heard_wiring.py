"""BIC-2 step 3 — the heard-by line reaches /api/inbox and the page.

INBOX-T F2: steps 1-3 of INBOX-2 had NO caller — "a fleet-heard post still
reads 'unconfirmed' forever". Pinned here:
  * every outbound BROADCAST send with a packet id carries ``heard`` (the
    D4 line + summary) — the fleet's spools are read ONCE per inbox read;
  * a box with no fleet (no target list) carries no ``heard`` at all;
  * fleet evidence that raises never 503s the inbox — the send says the
    fleet is unobservable, and the read says why (hfm #9 witness);
  * DMs and received rows never carry a broadcast heard-by line;
  * the page: a closed STATE map whose unknown default says "state unknown"
    (F11), a heard line rendered as text, and a send failure that the next
    poll does not wipe (F11).
"""
import pathlib
from unittest.mock import patch

from commands.base import CommandResult
from utils import inbox_api, inbox_legs

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "inbox.html").read_text()


def _row(**kw):
    base = {"id": 1, "timestamp": "2026-11-15 06:19:18", "network": "meshtastic",
            "from_id": "local", "to_id": None, "content": "Test", "channel": 3,
            "is_dm": False, "snr": None, "rssi": None, "delivered": True,
            "packet_id": 287851589}
    base.update(kw)
    return base


def _read(rows, sections):
    calls = []

    def fake_load(now, **kw):
        calls.append(now)
        return sections

    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.ok("ok", data={"messages": rows})), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_api.messaging, "first_received_id_row", return_value=None), \
         patch.object(inbox_legs, "load_sections", side_effect=fake_load):
        out = inbox_api.read_inbox()
    msgs = {m["id"]: m for t in out["threads"] for m in t["messages"]}
    return out, msgs, calls


def _sec(*hits):
    import time
    now = time.time()   # coverage must end before "now" (a future stamp is a clock fault)
    return {"entries": [list(h) for h in hits], "covered_since": 0,
            "covered_until": now - 60, "last_line_ts": now - 70}


def test_a_broadcast_send_carries_the_heard_line_and_spools_are_read_once():
    sent = inbox_api._epoch("2026-11-15 06:19:18")
    rows = [_row(id=1), _row(id=2, content="again", packet_id=111)]
    out, msgs, calls = _read(rows, {"kiai": _sec((0x0DAEE001, 287851589, sent + 1)),
                                    "moc3": _sec()})
    assert len(calls) == 1, "the fleet's spools are read once per inbox read"
    assert msgs[1]["heard"]["line"].startswith("heard by kiai · 1 of 2 listening")
    assert msgs[2]["heard"]["line"].startswith("heard by none")


def test_no_fleet_means_no_heard_field():
    out, msgs, _ = _read([_row()], None)
    assert "heard" not in msgs[1]


def test_dms_and_received_rows_carry_no_broadcast_heard_line():
    rows = [_row(id=1, to_id="!a1b2c3d4", is_dm=True),
            _row(id=2, from_id="!0000beef", packet_id=222)]
    out, msgs, _ = _read(rows, {"kiai": _sec()})
    assert "heard" not in msgs[1] and "heard" not in msgs[2]


def test_fleet_evidence_that_raises_never_503s_the_inbox():
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.ok("ok", data={"messages": [_row()]})), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_api.messaging, "first_received_id_row", return_value=None), \
         patch.object(inbox_legs, "load_sections", side_effect=OSError("spool dir gone")):
        out = inbox_api.read_inbox()
    assert out["state"] == "ok"
    (t,) = out["threads"]
    assert t["messages"][0]["heard"]["line"].startswith("heard by: unobservable")
    assert "spool dir gone" in out["heard_error"]


def test_page_state_map_is_closed_with_an_unknown_default():
    assert 'STATE[m.state] || "state unknown"' in PAGE or "stateLabel(" in PAGE
    i = PAGE.index("function stateLabel")
    fn = PAGE[i:PAGE.index("\n  }\n", i)]
    assert '"state unknown"' in fn


def test_page_renders_the_heard_line_as_text_only():
    i = PAGE.index("m.heard")
    chunk = PAGE[i - 200:i + 300]
    assert "innerHTML" not in chunk and "deliver" not in chunk.lower()


def test_page_poll_does_not_wipe_a_send_failure():
    # F11: load() called setBanner("") on every good poll, erasing the send error
    i = PAGE.index("function load()")
    fn = PAGE[i:PAGE.index("\n  }\n", i)]
    assert 'setBanner("")' not in fn


# ── review r5 ─────────────────────────────────────────────────────────────

def test_judge_box_never_raises_on_an_absurd_number():
    # r5 MED: float(10**400) raised OverflowError — "never raises" was false
    from utils import inbox_heard
    sec = {"entries": [[0x0DAEE001, 777, 10 ** 400]], "covered_since": 10 ** 400,
           "covered_until": 1.0, "last_line_ts": 1.0}
    r = inbox_heard.judge_box("moc", sec, packet_id=287851589, sent_ts=1.8e9, now=1.8e9)
    assert r.state == "unobservable"


def test_one_poisoned_spool_never_takes_the_inbox_down():
    import time
    now = time.time()
    poisoned = {"entries": [], "covered_since": 10 ** 400,
                "covered_until": now - 60, "last_line_ts": now - 70}
    out, msgs, _ = _read([_row()], {"kiai": poisoned, "moc": _sec()})
    assert out["state"] == "ok" and "heard" in msgs[1]


def test_a_clock_skewed_hearing_is_qualified_in_the_line():
    # r5 LOW: a hearing outside the join window read as a plain "heard by moc"
    sent = inbox_api._epoch("2026-11-15 06:19:18")
    out, msgs, _ = _read([_row()], {"moc": _sec((0x99, 287851589, sent - 72000))})
    assert "moc (clock off)" in msgs[1]["heard"]["line"]


def test_a_target_list_with_no_valid_line_is_an_error_not_standalone(tmp_path):
    # r5 LOW: a damaged target file silently read as "no fleet"
    import pytest
    t = tmp_path / "targets"
    t.write_text("bad alias!! extra\n#comment\n")
    with pytest.raises(ValueError):
        inbox_legs.load_sections(1.0, targets_file=t, spool_dir=tmp_path)
    t.write_text("# only a comment\n\n")
    assert inbox_legs.load_sections(1.0, targets_file=t, spool_dir=tmp_path) is None


def test_page_a_failed_poll_does_not_overwrite_an_unread_send_failure():
    i = PAGE.index("function setPollBanner")
    fn = PAGE[i:PAGE.index("\n", PAGE.index("{", i) + 1) + 400]
    assert 'bannerFrom === "send"' in fn


def test_page_says_when_fleet_evidence_or_the_era_is_unreadable():
    assert "heard_error" in PAGE and "era_error" in PAGE


def test_a_send_whose_heard_by_raises_is_unobservable_and_the_read_says_why():
    # the per-send guard (r5): any failure inside heard_from stays on that send
    with patch.object(inbox_api.messaging, "get_messages",
                      return_value=CommandResult.ok("ok", data={"messages": [_row()]})), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_api.messaging, "first_received_id_row", return_value=None), \
         patch.object(inbox_legs, "load_sections", return_value={"kiai": _sec()}), \
         patch.object(inbox_legs, "heard_from", side_effect=RuntimeError("boom")):
        out = inbox_api.read_inbox()
    assert out["state"] == "ok"
    assert out["threads"][0]["messages"][0]["heard"]["line"] == inbox_api.HEARD_UNREADABLE
    assert "boom" in out["heard_error"]


# ── cannot_hear wired (BIC-2, 11-16): inbox_api.py passed NO segments, so
# cannot_hear was unreachable live. The DECLARED segments now ride every read.

def test_a_box_on_the_other_declared_preset_reads_cannot_hear_on_the_inbox():
    from utils import rf_segments
    decl = rf_segments.Declared({"center-box": "meshtastic:LONG_FAST/ch20",
                                 "moc3": "meshtastic:SHORT_TURBO/ch8"}, "center-box", None)
    with patch.object(rf_segments, "load", return_value=decl):
        out, msgs, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    heard = msgs[1]["heard"]
    assert heard["summary"]["cannot_hear"] == ["moc3"], heard
    assert "on another preset (declared" in heard["line"]


def test_an_unreadable_declaration_is_said_and_never_cannot_hear():
    from utils import rf_segments
    decl = rf_segments.Declared({}, None, "declared segments x: unreadable JSON")
    with patch.object(rf_segments, "load", return_value=decl):
        out, msgs, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    assert msgs[1]["heard"]["summary"].get("cannot_hear", []) == []
    assert "declared segments" in out["heard_error"]
