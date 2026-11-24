"""BIC-2 step 3 — the heard-by line reaches /api/inbox and the page.

INBOX-T F2: steps 1-3 of INBOX-2 had NO caller — "a fleet-heard post still
reads 'unconfirmed' forever". Pinned here:
  * every outbound BROADCAST send with a packet id carries ``heard`` (the
    D4 line + summary) — the fleet's spools are read ONCE per inbox read;
  * a box with no fleet (no target list) says it cannot see hearings — one
    line per broadcast send, never a count, never an error;
  * fleet evidence that raises never 503s the inbox — the send says the
    fleet is unobservable, and the read says why (hfm #9 witness);
  * DMs and received rows never carry a broadcast heard-by line;
  * the page: a closed STATE map whose unknown default says "state unknown"
    (F11), a heard line rendered as text, and a send failure that the next
    poll does not wipe (F11).
"""
import pathlib

import pytest
from unittest.mock import patch

from commands.base import CommandResult
from utils import inbox_api, inbox_legs

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "inbox.html").read_text()


@pytest.fixture(autouse=True)
def _own_store(tmp_path, monkeypatch):
    """Each test its OWN messages.db: the inbox read now KEEPS heard-by
    verdicts (BIC-2 11-23), and the conftest home is shared by the whole
    session — a verdict one test kept leaked into the next (fixed copy, r1)."""
    from commands import messaging as messaging_mod
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: tmp_path / "messages.db")


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


def test_no_fleet_says_this_box_cannot_see_hearings():
    # BIC-2 one-box (measured 11-24): no target list used to mean NO heard
    # field — the page showed only "radio took it" and never said it could
    # not see hearings (an absence unexplained to a stranger, hfm #2/#11).
    # The blindness is SAID per send; it is not a negative ("heard by none")
    # and not a read error (no heard_error — nothing failed).
    out, msgs, _ = _read([_row()], None)
    line = msgs[1]["heard"]["line"]
    assert line == inbox_legs.NO_FLEET_LINE
    assert "cannot see hearings" in line
    assert "none" not in line and "0 of" not in line, "blind is never a count"
    assert "summary" not in msgs[1]["heard"] and "boxes" not in msgs[1]["heard"]
    assert "heard_error" not in out


def test_no_fleet_dms_and_received_rows_still_carry_no_heard_line():
    rows = [_row(id=1, to_id="!a1b2c3d4", is_dm=True),
            _row(id=2, from_id="!0000beef", packet_id=222)]
    out, msgs, _ = _read(rows, None)
    assert "heard" not in msgs[1] and "heard" not in msgs[2]


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
    assert "on another preset/slot (declared" in heard["line"]


def test_an_unreadable_declaration_is_said_and_never_cannot_hear():
    from utils import rf_segments
    decl = rf_segments.Declared({}, None, "declared segments x: unreadable JSON")
    with patch.object(rf_segments, "load", return_value=decl):
        out, msgs, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    assert msgs[1]["heard"]["summary"].get("cannot_hear", []) == []
    assert "declared segments" in out["heard_error"]


def test_h1_a_declaration_load_that_raises_never_breaks_the_inbox_read():
    """r1 H1: load() sat outside every guard; a raise killed /api/inbox and the
    page read 'offline'. Any failure is a said error, never a dead read."""
    from utils import rf_segments
    with patch.object(rf_segments, "load", side_effect=RecursionError("deep")):
        out, msgs, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    assert out["state"] == "ok"
    assert "heard" in msgs[1]
    assert "declared segments" in out["heard_error"]


def test_m1_a_declared_alias_the_fleet_does_not_list_is_said():
    """r1 M1: a typo ('MOC3') left moc3 undeclared and silently back to
    'listening, not heard'. A declared alias with no target is SAID."""
    from utils import rf_segments
    decl = rf_segments.Declared({"center-box": "meshtastic:LONG_FAST/ch20",
                                 "MOC3": "meshtastic:SHORT_TURBO/ch8"}, "center-box", None)
    with patch.object(rf_segments, "load", return_value=decl):
        out, msgs, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    assert "MOC3" in (out.get("heard_error") or ""), out.get("heard_error")


def test_r2_the_sender_missing_from_its_own_fleet_list_is_not_an_error():
    """r2 MED: parse() REQUIRES send_from in segments, and the stray check then
    flagged it on any center whose target list omits itself (the list is
    opt-in) - a false error the operator could not clear by editing."""
    from utils import rf_segments
    decl = rf_segments.Declared({"center-box": "meshtastic:LONG_FAST/ch20",
                                 "moc3": "meshtastic:SHORT_TURBO/ch8"}, "center-box", None)
    with patch.object(rf_segments, "load", return_value=decl):
        out, msgs, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    assert out.get("heard_error") is None, out.get("heard_error")
    assert msgs[1]["heard"]["summary"]["cannot_hear"] == ["moc3"]


def _decl(segments, send_from="center-box"):
    from utils import rf_segments
    return rf_segments.Declared(segments, send_from, None)


LF, ST = "meshtastic:LONG_FAST/ch20", "meshtastic:SHORT_TURBO/ch8"


def test_r3_a_hearing_that_contradicts_the_declaration_is_said_once_per_read():
    """r3 MED: a box declared on another segment DECODED the send (a bridged
    copy carries a new id, so this proves the declaration wrong). The read
    held the evidence and said nothing; other sends kept the false line."""
    from utils import rf_segments
    sent = inbox_api._epoch("2026-11-15 06:19:18")
    rows = [_row(id=1), _row(id=2, content="again", packet_id=111)]
    decl = _decl({"center-box": LF, "moc3": ST})
    with patch.object(rf_segments, "load", return_value=decl):
        out, msgs, _ = _read(rows, {"moc3": _sec((0x0DAEE001, 287851589, sent + 1),
                                                 (0x0DAEE001, 111, sent + 1))})
    err = out.get("heard_error") or ""
    assert "moc3" in err and "decoded" in err, err
    assert err.count("moc3") == 1, "one line per box per read, not per send"


def test_l1_a_contradiction_names_a_real_segment_change_as_the_other_cause():
    """r4 L-1, overruled by the operator 11-17: right after a REAL preset
    change, a now-correct declaration met a hearing made on the old preset
    and the page called the declaration wrong (for up to the 24 h ring). The
    line must name both causes; it cannot tell them apart."""
    from utils import rf_segments
    sent = inbox_api._epoch("2026-11-15 06:19:18")
    decl = _decl({"center-box": LF, "moc3": ST})
    with patch.object(rf_segments, "load", return_value=decl):
        out, _, _ = _read([_row(id=1)], {"moc3": _sec((0x0DAEE001, 287851589, sent + 1))})
    err = out.get("heard_error") or ""
    assert "(the declaration is wrong)" not in err, err
    assert "the declaration is wrong, or the box changed segment since that send" in err, err


def test_r3_a_skewed_hearing_is_not_called_a_contradiction():
    """A hearing outside the join window may be an unrelated packet id reuse."""
    from utils import rf_segments
    sent = inbox_api._epoch("2026-11-15 06:19:18")
    decl = _decl({"center-box": LF, "moc3": ST})
    with patch.object(rf_segments, "load", return_value=decl):
        out, _, _ = _read([_row()], {"moc3": _sec((0x0DAEE001, 287851589, sent + 5000))})
    assert "decoded" not in (out.get("heard_error") or "")


def test_r3_two_problems_are_both_said():
    """r3 L-3 (mutant: only the first error kept)."""
    from utils import rf_segments
    with patch.object(rf_segments, "load", return_value=_decl({"center-box": LF, "MOC3": ST})), \
         patch.object(inbox_legs, "heard_from", side_effect=RuntimeError("boom")):
        out, _, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    assert "MOC3" in out["heard_error"] and "boom" in out["heard_error"]


def test_r3_a_typo_on_the_senders_own_segment_is_said():
    """r3 L-4 (mutant: exempt by segment, not by name)."""
    from utils import rf_segments
    with patch.object(rf_segments, "load",
                      return_value=_decl({"center-box": LF, "kiaii": LF, "moc3": ST})):
        out, _, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    assert "kiaii" in (out.get("heard_error") or "")


def test_r3_a_stray_alias_never_discards_the_valid_declarations():
    """r3 L-5 (mutant: a stray drops every declaration)."""
    from utils import rf_segments
    with patch.object(rf_segments, "load",
                      return_value=_decl({"center-box": LF, "kiaii": LF, "moc3": ST})):
        out, msgs, _ = _read([_row()], {"kiai": _sec(), "moc3": _sec()})
    assert msgs[1]["heard"]["summary"]["cannot_hear"] == ["moc3"]
