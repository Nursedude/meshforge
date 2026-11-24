"""BIC-2 — a send's heard-by verdict outlives the boxes' 24 h rx_heard ring.

Measured 2026-11-23: the operator's sends 7638/7640 (11-22 06:52 HST) were
decoded by kiai, moc, moc1 and moc4 (their meshtasticd journals still hold
the packet ids), yet /inbox read "heard by none · 0 of 0 listening · 7
unobservable" 31 h later — the verdict was re-judged on every read from a
ring trimmed to ``RING_WINDOW_S`` (24 h) and never kept (hfm #2: hold the
last-known state; gate 3: an ENDED state must not revert).

Seams (operator-agreed 11-23): read_inbox end to end on a real temp store;
the store's migration of an old file; the pure merge rule.
"""
from unittest.mock import patch

import pytest

from commands import messaging as messaging_mod
from utils import inbox_api, inbox_legs

PID = 4198519586
DAY = 24 * 3600


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "messages.db"
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)
    return db_path


def _send(packet_id=PID):
    r = messaging_mod.store_outgoing("Wx", "meshtastic", None, channel=0,
                                     delivered=True, packet_id=packet_id)
    assert r.success, r.message
    return r.data["message_id"]


def _ring(now, *hits):
    """A box's rx_heard section as box_truth_rx writes it: a ring that can
    vouch only for the last 24 h, read a minute ago."""
    return {"entries": [list(h) for h in hits], "covered_since": now - DAY,
            "covered_until": now - 60, "last_line_ts": now - 70}


def _read_at(now, sections):
    with patch.object(inbox_api.time, "time", return_value=now), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_legs, "load_sections", return_value=sections):
        out = inbox_api.read_inbox()
    return {m["id"]: m for t in out["threads"] for m in t["messages"]}


def _sent_ts(mid, now):
    msgs = _read_at(now, None)
    return inbox_api._epoch(msgs[mid]["ts"])


def test_a_heard_send_still_reads_heard_after_the_ring_trims_it(tmp_db):
    mid = _send()
    sent = _sent_ts(mid, 0)
    first = sent + 700                     # listen window (600 s) closed
    msgs = _read_at(first, {"kiai": _ring(first, (0x0DAEE001, PID, sent + 2))})
    assert msgs[mid]["heard"]["line"].startswith("heard by kiai · 1 of 1 listening")

    later = sent + 25 * 3600               # the ring no longer reaches the send
    msgs = _read_at(later, {"kiai": _ring(later)})
    heard = msgs[mid]["heard"]
    assert heard["line"].startswith("heard by kiai · 1 of 1 listening"), heard["line"]
    kiai = next(b for b in heard["boxes"] if b["alias"] == "kiai")
    assert kiai["state"] == "heard"


# ── seam 2: the store — an existing messages.db from before heard_kept ──

def _old_store(path):
    """A store as the 11-15..11-23 code left it: messages + packet_id +
    messages_meta, no heard_kept — the OLD probe called it current."""
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, network TEXT NOT NULL,
            from_id TEXT NOT NULL, to_id TEXT, content TEXT NOT NULL,
            channel INTEGER DEFAULT 0, is_dm BOOLEAN DEFAULT 1, snr REAL,
            rssi INTEGER, delivered BOOLEAN DEFAULT 0, packet_id INTEGER);
        CREATE TABLE messages_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
    """)
    conn.commit()
    conn.close()


def test_an_existing_store_without_the_table_keeps_verdicts_too(tmp_db):
    _old_store(tmp_db)
    mid = _send()
    sent = _sent_ts(mid, 0)
    first = sent + 700
    _read_at(first, {"kiai": _ring(first, (0x0DAEE001, PID, sent + 2))})
    later = sent + 25 * 3600
    msgs = _read_at(later, {"kiai": _ring(later)})
    assert msgs[mid]["heard"]["line"].startswith("heard by kiai"), msgs[mid]["heard"]


def _hearing(alias="kiai", skewed=False):
    return {"alias": alias, "state": "heard", "reason": "decoded this packet id",
            "rx_ts": 1.0, "skewed": skewed}


def test_a_kept_verdict_never_lends_itself_to_another_packet_id(tmp_db):
    mid = _send()
    messaging_mod.keep_heard([(mid, PID, [_hearing()])], now=2.0)
    assert messaging_mod.kept_heard([(mid, PID + 1)])[:2] == ({}, 0)
    assert messaging_mod.kept_heard([(mid, PID)])[0][mid][0]["state"] == "heard"


@pytest.mark.parametrize("state", ["unobservable", "not_heard", "cannot_hear"])
def test_the_store_keeps_hearings_only(tmp_db, state):
    """r1 L2: a kept ABSENCE outlived the evidence that corrects it."""
    mid = _send()
    with pytest.raises(ValueError):
        messaging_mod.keep_heard([(mid, PID, [dict(_hearing(), state=state)])])


def test_a_clean_hearing_replaces_a_skewed_one_never_the_reverse(tmp_db):
    """r1 L1/L5: overlapping reads never downgrade what the other kept."""
    mid = _send()
    messaging_mod.keep_heard([(mid, PID, [_hearing(skewed=True)])])
    messaging_mod.keep_heard([(mid, PID, [_hearing(skewed=False)])])
    assert messaging_mod.kept_heard([(mid, PID)])[0][mid][0]["skewed"] is False
    messaging_mod.keep_heard([(mid, PID, [_hearing(skewed=True)])])   # a stale reader
    assert messaging_mod.kept_heard([(mid, PID)])[0][mid][0]["skewed"] is False


def _plant(db, *row):
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO heard_kept VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
    conn.commit()
    conn.close()


@pytest.mark.parametrize("state,alias,reason", [
    ("unobservable", "kiai", "r"), ("cannot_hear", "kiai", "r"),
    ("not_heard", "kiai", "r"), ("bogus", "kiai", "r"), ("heard", "", "r"),
])
def test_the_reader_refuses_a_row_no_writer_can_have_stored(tmp_db, state, alias, reason):
    """r1 L4: the writer refused it, so the reader must too — and say so."""
    mid = _send()
    _plant(tmp_db, mid, PID, alias, state, reason, None, 0, 1.0)
    assert messaging_mod.kept_heard([(mid, PID)])[:2] == ({}, 1)


def test_a_refused_kept_row_is_said_and_never_beats_the_live_answer(tmp_db):
    mid = _send()
    _plant(tmp_db, mid, PID, "kiai", "unobservable", "r", None, 0, 1.0)
    sent = _sent_ts(mid, 0)
    now = sent + 700
    with patch.object(inbox_api.time, "time", return_value=now), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_legs, "load_sections", return_value={"kiai": _ring(now)}):
        out = inbox_api.read_inbox()
    m = next(m for t in out["threads"] for m in t["messages"] if m["id"] == mid)
    assert m["heard"]["summary"]["not_heard"] == ["kiai"]
    assert "1 kept heard-by row(s) refused" in out["heard_error"]


def test_an_unreadable_kept_table_is_said_and_the_live_verdict_still_shows(tmp_db):
    mid = _send()
    sent = _sent_ts(mid, 0)
    now = sent + 700
    with patch.object(messaging_mod, "kept_heard", side_effect=OSError("disk")), \
         patch.object(messaging_mod, "keep_heard") as keep, \
         patch.object(inbox_api.time, "time", return_value=now), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_legs, "load_sections",
                      return_value={"kiai": _ring(now, (0x0DAEE001, PID, sent + 2))}):
        out = inbox_api.read_inbox()
    m = next(m for t in out["threads"] for m in t["messages"] if m["id"] == mid)
    assert m["heard"]["line"].startswith("heard by kiai")
    assert "kept heard-by verdicts unreadable" in out["heard_error"]
    keep.assert_not_called()   # never write over what could not be read


def test_prune_and_clear_take_kept_rows_with_their_sends(tmp_db):
    import sqlite3
    mid = _send()
    messaging_mod.keep_heard([(mid, PID, [_hearing()])])
    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE messages SET timestamp = datetime('now', '-40 days')")
    conn.commit()
    conn.close()
    messaging_mod._maybe_prune_messages(now=10 ** 10)
    assert messaging_mod.kept_heard([(mid, PID)])[:2] == ({}, 0)
    mid2 = _send(PID + 1)
    messaging_mod.keep_heard([(mid2, PID + 1, [_hearing()])])
    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE messages SET timestamp = datetime('now', '-5 days')")
    conn.commit()
    conn.close()
    messaging_mod.clear_messages(older_than_days=1)
    conn = sqlite3.connect(tmp_db)
    assert conn.execute("SELECT COUNT(*) FROM heard_kept").fetchone()[0] == 0
    conn.close()


# ── seam 3: the merge rule (pure) ──

from utils.inbox_heard import BoxHeard, merge_frozen  # noqa: E402


def _b(alias, state, reason="r"):
    return BoxHeard(alias, state, reason)


def _sk(alias):
    return BoxHeard(alias, "heard", "r", skewed=True)


@pytest.mark.parametrize("live,kept,shown,to_keep", [
    # a kept hearing beats the blind live answer the trimmed ring gives
    ([_b("kiai", "unobservable")], {"kiai": _b("kiai", "heard")}, "heard", []),
    # r1 M1: a kept hearing beats a LATER declaration, as the live judge orders it
    ([_b("moc2", "cannot_hear")], {"moc2": _b("moc2", "heard")}, "heard", []),
    # and beats a live not_heard (positive evidence outranks absence)
    ([_b("kiai", "not_heard")], {"kiai": _b("kiai", "heard")}, "heard", []),
    # an equal kept hearing is not re-kept
    ([_b("kiai", "heard")], {"kiai": _b("kiai", "heard")}, "heard", []),
    # a fresh hearing is kept
    ([_b("kiai", "heard")], {}, "heard", ["kiai"]),
    # r1 L2: an absence is NEVER kept
    ([_b("kiai", "not_heard")], {}, "not_heard", []),
    ([_b("kiai", "unobservable")], {}, "unobservable", []),
    ([_b("moc3", "cannot_hear")], {}, "cannot_hear", []),
])
def test_merge_rule(live, kept, shown, to_keep):
    merged, new = merge_frozen(live, kept)
    assert [b.state for b in merged] == [shown]
    assert [b.alias for b in new] == to_keep


def test_a_clean_live_hearing_upgrades_a_kept_skewed_one_and_not_back():
    """r1 L5."""
    merged, new = merge_frozen([_b("kiai", "heard")], {"kiai": _sk("kiai")})
    assert merged[0].skewed is False and [b.alias for b in new] == ["kiai"]
    merged, new = merge_frozen([_sk("kiai")], {"kiai": _b("kiai", "heard")})
    assert merged[0].skewed is False and new == []


def test_a_kept_box_the_fleet_list_dropped_still_counts():
    merged, new = merge_frozen([_b("moc", "unobservable")], {"kiai": _b("kiai", "heard")})
    assert [(b.alias, b.state) for b in merged] == [("kiai", "heard"), ("moc", "unobservable")]
    assert new == []


# ── past the ring: only hearings are kept, nothing else is claimed ──
# Operator stop rule 11-23: four rounds found the "judged in time" witness
# wrong four ways (r1 not on the page, r2 counted prompt reads, r3 a
# center-clock mark, r4 a per-box mark blaming reads for boxes added /
# renamed / blind and keeping a clock-stepped not_heard). It is removed; a
# box that did not hear reads UNKNOWN past its ring, and says why.

def _read_out(now, sections, decl=None):
    from utils import rf_segments
    with patch.object(inbox_api.time, "time", return_value=now), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch.object(inbox_legs, "load_sections", return_value=sections), \
         patch.object(rf_segments, "load", return_value=decl or rf_segments.INERT):
        return inbox_api.read_inbox()


def _msg(out, mid):
    return next(m for t in out["threads"] for m in t["messages"] if m["id"] == mid)



NEUTRAL = "1 unobservable (1 past the box's rx ring; only hearings are kept)"


def test_a_send_first_read_after_the_ring_names_the_ring_not_the_journal(tmp_db):
    mid = _send()
    sent = _sent_ts(mid, 0)
    later = sent + 25 * 3600
    out = _read_out(later, {"kiai": dict(_ring(later), window_s=DAY)})
    kiai = _msg(out, mid)["heard"]["boxes"][0]
    assert kiai["state"] == "unobservable"
    assert kiai["reason"] == ("send is older than this box's 24 h rx ring; "
                              "only hearings are kept past it"), kiai
    assert NEUTRAL in _msg(out, mid)["heard"]["line"]
    assert "heard_not_kept" not in out


@pytest.mark.parametrize("shape", ["read_in_time", "never_read", "box_added_later",
                                   "box_blind_at_the_read", "clock_stepped_not_heard"])
def test_r4_every_path_to_an_aged_box_says_the_same_true_thing(tmp_db, shape):
    """r4 MED-1/MED-2: no wording may blame (or credit) earlier reads."""
    mid = _send()
    sent = _sent_ts(mid, 0)
    early = {"read_in_time": (sent + 700, {"moc": dict(_ring(sent + 700), window_s=DAY)}),
             "never_read": None,
             "box_added_later": (sent + 700, {}),
             "box_blind_at_the_read": (sent + 700, {"moc": None}),
             # r4 MED-2: send stamped 1 h early, read 10 s after the true send
             "clock_stepped_not_heard": (sent + 3610,
                                         {"moc": dict(_ring(sent + 3610), window_s=DAY)})}[shape]
    if early:
        _read_out(*early)
    later = sent + 30 * 3600
    out = _read_out(later, {"moc": dict(_ring(later), window_s=DAY)})
    line = _msg(out, mid)["heard"]["line"]
    assert line == "heard by none · 0 of 0 listening · " + NEUTRAL, line


def test_r4_the_page_carries_no_gap_count():
    import pathlib
    page = (pathlib.Path(__file__).parent.parent / "web" / "inbox.html").read_text()
    assert "heard_not_kept" not in page


def test_r4_no_coverage_table_is_created(tmp_db):
    import sqlite3
    messaging_mod._init_db().close()
    conn = sqlite3.connect(tmp_db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "heard_kept" in names and not names & {"heard_covered", "heard_judged"}, names


def test_r4_a_failed_keep_is_said(tmp_db):
    mid = _send()
    sent = _sent_ts(mid, 0)
    now = sent + 700
    with patch.object(messaging_mod, "keep_heard", side_effect=OSError("disk full")):
        out = _read_out(now, {"kiai": _ring(now, (0x0DAEE001, PID, sent + 2))})
    assert "heard-by not kept for 1 send(s): OSError: disk full" in out["heard_error"]


def test_r4_prune_drops_kept_rows_keyed_to_another_packet_id(tmp_db):
    import sqlite3
    mid = _send()
    messaging_mod.keep_heard([(mid, PID + 5, [_hearing()])])
    messaging_mod.keep_heard([(mid, PID, [_hearing()])])
    messaging_mod._last_message_prune_ts = 0.0
    messaging_mod._maybe_prune_messages(now=10 ** 10)
    conn = sqlite3.connect(tmp_db)
    left = conn.execute("SELECT packet_id FROM heard_kept").fetchall()
    conn.close()
    assert left == [(PID,)], left


def test_r4_heard_line_never_says_more_aged_than_blind():
    """r4 NIT-1."""
    summary = {"heard": [], "not_heard": [], "unobservable": ["a"], "cannot_hear": []}
    assert inbox_legs.heard_line(summary, packet_id=PID, aged=2).endswith(
        "1 unobservable (1 past the box's rx ring; only hearings are kept)")
    assert inbox_legs.heard_line(summary, packet_id=PID, aged=-1).endswith("1 unobservable")


def test_a_kept_hearing_says_so_on_the_line(tmp_db):
    """r1 M2: a kept verdict is distinguishable from a live one where it is read."""
    mid = _send()
    sent = _sent_ts(mid, 0)
    first = sent + 700
    _read_at(first, {"kiai": _ring(first, (0x0DAEE001, PID, sent + 2))})
    later = sent + 25 * 3600
    line = _read_at(later, {"kiai": dict(_ring(later), window_s=DAY)})[mid]["heard"]["line"]
    assert line == "heard by kiai · 1 of 1 listening · 1 kept from an earlier read", line


@pytest.mark.parametrize("window", [0, -3600, None, "x"])
def test_a_window_no_ring_can_have_is_not_a_ring_age(window):
    """r1 L7."""
    from utils.inbox_heard import judge_box
    now = 10 ** 9
    sec = dict(_ring(now), window_s=window, covered_since=now - 3600)
    b = judge_box("kiai", sec, packet_id=PID, sent_ts=now - 7200, now=now)
    assert b.reason == "box journal coverage starts after the send"


def test_a_kept_hearing_survives_a_later_declaration_end_to_end(tmp_db):
    """r1 M1, the reviewer's reproduction: moc2 heard it, then moved preset."""
    mid = _send()
    sent = _sent_ts(mid, 0)
    first = sent + 700
    _read_at(first, {"moc2": _ring(first, (0x0DAEE001, PID, sent + 2))})
    later = sent + 25 * 3600
    from utils import rf_segments
    decl = rf_segments.Declared({"moc2": "meshtastic:SHORT_TURBO/ch8",
                                 "self": "meshtastic:LONG_FAST/ch20"}, "self", None)
    with patch.object(rf_segments, "load", return_value=decl):
        msgs = _read_at(later, {"moc2": _ring(later)})
    boxes = {b["alias"]: b["state"] for b in msgs[mid]["heard"]["boxes"]}
    assert boxes["moc2"] == "heard", msgs[mid]["heard"]


def test_r2_lb_a_hearing_the_live_ring_still_confirms_is_not_labelled_kept(tmp_db):
    mid = _send()
    sent = _sent_ts(mid, 0)
    for now in (sent + 700, sent + 730):
        out = _read_out(now, {"kiai": _ring(now, (0x0DAEE001, PID, sent + 2))})
    m = _msg(out, mid)
    assert m["heard"]["line"] == "heard by kiai · 1 of 1 listening", m["heard"]["line"]
    assert [b["kept"] for b in m["heard"]["boxes"]] == [False]


def test_r2_la_a_kept_hearing_never_raises_the_declaration_warning(tmp_db):
    """A box moved and correctly re-declared: before the change the warning
    died with the ring; a kept hearing must not keep it alive for 30 days."""
    from utils import rf_segments
    mid = _send()
    sent = _sent_ts(mid, 0)
    first = sent + 700
    _read_out(first, {"moc2": _ring(first, (0x0DAEE001, PID, sent + 2))})
    later = sent + 2 * DAY
    decl = rf_segments.Declared({"moc2": "meshtastic:SHORT_TURBO/ch8",
                                 "self": "meshtastic:LONG_FAST/ch20"}, "self", None)
    out = _read_out(later, {"moc2": _ring(later)}, decl)
    assert out.get("heard_error") is None, out.get("heard_error")
    assert {b["alias"]: b["state"] for b in _msg(out, mid)["heard"]["boxes"]} == {"moc2": "heard"}


@pytest.mark.parametrize("planted", ["not_heard", "unobservable", "bogus"])
def test_r2_lc_a_refused_row_never_blocks_keeping_the_real_hearing(tmp_db, planted):
    mid = _send()
    _plant(tmp_db, mid, PID, "kiai", planted, "r", None, 0, 1.0)
    messaging_mod.keep_heard([(mid, PID, [_hearing()])])
    kept, refused = messaging_mod.kept_heard([(mid, PID)])
    assert refused == 0 and kept[mid][0]["state"] == "heard"


@pytest.mark.parametrize("rx_ts,skewed,alias", [
    (float("inf"), 0, "kiai"), (None, 7, "kiai"),
    (None, "x", "kiai"), (None, 0, "ki\nai"), (None, 0, "kiai\x00"),
])
def test_r2_ld_the_reader_refuses_values_the_judge_cannot_produce(tmp_db, rx_ts, skewed, alias):
    mid = _send()
    _plant(tmp_db, mid, PID, alias, "heard", "r", rx_ts, skewed, 1.0)
    assert messaging_mod.kept_heard([(mid, PID)]) == ({}, 1)


def test_r2_a_kept_skewed_hearing_still_says_clock_off_after_the_ring(tmp_db):
    """r2 survivor #22: the writer must store the skew it was given."""
    mid = _send()
    sent = _sent_ts(mid, 0)
    first = sent + 700
    _read_out(first, {"kiai": _ring(first, (0x0DAEE001, PID, sent + 3600))})   # outside window
    later = sent + 25 * 3600
    line = _msg(_read_out(later, {"kiai": _ring(later)}), mid)["heard"]["line"]
    assert line.startswith("heard by kiai (clock off)"), line


def test_r2_a_kept_hearing_carries_the_kept_note(tmp_db):
    """r2 survivor #19."""
    from utils.inbox_heard import KEPT_NOTE
    mid = _send()
    sent = _sent_ts(mid, 0)
    _read_out(sent + 700, {"kiai": _ring(sent + 700, (0x0DAEE001, PID, sent + 2))})
    later = sent + 25 * 3600
    b = _msg(_read_out(later, {"kiai": _ring(later)}), mid)["heard"]["boxes"][0]
    assert b["reason"].endswith(KEPT_NOTE) and b["kept"] is True


def test_r2_the_schema_probe_never_trusts_a_heard_kept_of_the_wrong_shape(tmp_db):
    import sqlite3
    messaging_mod._init_db().close()
    conn = sqlite3.connect(tmp_db)
    conn.execute("DROP TABLE heard_kept")
    conn.execute("CREATE TABLE heard_kept (message_id INTEGER, state TEXT)")
    conn.commit()
    conn.close()
    conn = messaging_mod._init_db()
    try:
        assert messaging_mod._schema_is_current(conn) is False   # said, not trusted
    finally:
        conn.close()


def test_r3_low3_a_non_text_reason_never_blocks_the_real_hearing(tmp_db):
    import sqlite3
    mid = _send()
    messaging_mod._init_db().close()
    conn = sqlite3.connect(tmp_db)
    conn.execute("INSERT INTO heard_kept VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (mid, PID, "kiai", "heard", b"\x00blob", None, 0, 1.0))
    conn.commit()
    conn.close()
    messaging_mod.keep_heard([(mid, PID, [_hearing()])])
    kept, refused = messaging_mod.kept_heard([(mid, PID)])
    assert refused == 0 and kept[mid][0]["reason"] == "decoded this packet id"
