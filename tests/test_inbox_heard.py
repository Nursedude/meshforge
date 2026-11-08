"""INBOX-2 step 3 — the center's join of an inbox send to each box's
``rx_heard`` evidence, as a closed tri-state (plus the declared-segment
``cannot_hear``). Pure: no I/O, no clock read.

Design + pre-registered acceptance: .claude/plans/inbox2_heard_by_design_2026_11_07.md
(D2 join on the packet id, D3 the per-box states; step-2 review owed: the
clock window is a TAG, never a gate; ``covered_since`` is not "oldest entry").
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils import inbox_heard as ih  # noqa: E402

SENT = 1_800_000_000.0
PID = 0xBA78DAE5
FROM = 0x6201CE25
NOW = SENT + 3600


def section(*, entries=(), since=SENT - 3600, until=NOW - 60, last_line=NOW - 70,
            read="incremental"):
    return {"source": "meshtasticd journal", "entries": [list(e) for e in entries],
            "covered_since": since, "covered_until": until, "last_line_ts": last_line,
            "read": read, "lines_read": 10, "future_dropped": 0,
            "window_s": 86400, "cap": 2000, "note": None}


def judge(sec, **kw):
    kw.setdefault("now", NOW)
    return ih.judge_box("lehua", sec, packet_id=PID, sent_ts=SENT, **kw)


# --- the closed vocabulary (hfm #7) ---------------------------------------

def test_states_are_closed():
    assert ih.STATES == ("heard", "not_heard", "unobservable", "cannot_hear")


def test_summary_counts_every_state_and_nothing_else():
    results = [judge(section(entries=[(FROM, PID, SENT + 1)])),
               judge(section()),
               judge(None),
               judge(section(), box_segment="ST", send_segment="LF")]
    s = ih.summarize(results)
    assert set(s) == set(ih.STATES)
    assert [len(s[k]) for k in ih.STATES] == [1, 1, 1, 1]


# --- heard: the packet id is the key, never the text ------------------------

def test_heard_on_id_inside_window():
    r = judge(section(entries=[(FROM, PID, SENT + 1.1)]))
    assert (r.state, r.skewed) == ("heard", False)
    assert r.rx_ts == pytest.approx(SENT + 1.1)


def test_heard_outside_clock_window_is_tagged_not_dropped():
    # hfm #6: the box's wall clock may be wrong; the 32-bit id carries the join
    r = judge(section(entries=[(FROM, PID, SENT + 5000)]))
    assert (r.state, r.skewed) == ("heard", True)


def test_heard_takes_the_earliest_hearing():
    r = judge(section(entries=[(FROM, PID, SENT + 30), (FROM, PID, SENT + 2)]))
    assert r.rx_ts == pytest.approx(SENT + 2)


def test_heard_survives_a_failed_read_and_a_later_coverage_start():
    # a failed read serves the old ring; a reset keeps older hearings —
    # covered_since is NOT the oldest entry (step-2 review)
    r = judge(section(entries=[(FROM, PID, SENT + 1)], since=SENT + 900, read="failed"))
    assert r.state == "heard"


def test_heard_beats_declared_other_segment():
    # positive evidence outranks a declaration (it may have come via a relay)
    r = judge(section(entries=[(FROM, PID, SENT + 1)]), box_segment="ST", send_segment="LF")
    assert r.state == "heard"


def test_other_id_is_not_a_hearing():
    r = judge(section(entries=[(FROM, PID + 1, SENT + 1)]))
    assert r.state == "not_heard"


def test_malformed_entries_are_skipped_not_matched():
    sec = section(entries=[])
    sec["entries"] = [[FROM, PID], ["x", PID, SENT], [0, PID, SENT], [FROM, True, SENT],
                      [FROM, PID, "soon"]]
    assert judge(sec).state == "not_heard"


# --- not_heard needs coverage on BOTH sides of the send ---------------------

def test_not_heard_needs_coverage_from_before_the_send():
    r = judge(section(since=SENT + 1))
    assert r.state == "unobservable"
    assert "after the send" in r.reason


def test_not_heard_needs_coverage_past_the_listen_window():
    r = judge(section(until=SENT + ih.JOIN_AFTER_S - 1, last_line=SENT + 590),
              now=SENT + 700)
    assert r.state == "unobservable"
    assert "window" in r.reason


def test_not_heard_needs_the_journal_alive_after_the_send():
    # step-2 BLIND SPOT: meshtasticd stopped logging; coverage alone stays intact
    r = judge(section(last_line=SENT - 5))
    assert r.state == "unobservable"
    assert "silent" in r.reason


def test_not_heard_when_covered_and_absent():
    r = judge(section(entries=[(FROM, PID + 7, SENT + 3)]))
    assert (r.state, r.rx_ts) == ("not_heard", None)


# --- unobservable: every degraded input, never folded into not_heard --------

@pytest.mark.parametrize("sec", [
    None, "rx", [], {},
    {"entries": None, "covered_since": SENT - 10, "covered_until": NOW - 60,
     "last_line_ts": NOW - 60},                                  # null for a list (hfm #3)
])
def test_degraded_section_is_unobservable(sec):
    assert judge(sec).state == "unobservable"


@pytest.mark.parametrize("field", ["covered_since", "covered_until", "last_line_ts"])
@pytest.mark.parametrize("bad", [None, "1", True, float("nan")])
def test_missing_coverage_field_is_unobservable(field, bad):
    sec = section()
    sec[field] = bad
    assert judge(sec).state == "unobservable"


def test_box_clock_ahead_of_center_is_unobservable():
    r = judge(section(until=NOW + ih.FUTURE_SLOP_S + 1))
    assert r.state == "unobservable"
    assert "clock" in r.reason


def test_inverted_coverage_is_unobservable():
    r = judge(section(since=NOW - 10, until=NOW - 60))
    assert r.state == "unobservable"


def test_send_without_packet_id_is_unobservable_everywhere():
    r = ih.judge_box("lehua", section(entries=[(FROM, PID, SENT)]),
                     packet_id=None, sent_ts=SENT, now=NOW)
    assert r.state == "unobservable"
    assert "packet_id" in r.reason


# --- cannot_hear: declared, never guessed -----------------------------------

def test_cannot_hear_only_when_both_segments_declared_and_differ():
    assert judge(section(), box_segment="ST", send_segment="LF").state == "cannot_hear"
    assert judge(section(), box_segment="LF", send_segment="LF").state == "not_heard"
    assert judge(section(), box_segment=None, send_segment="LF").state == "not_heard"
    assert judge(section(), box_segment="ST", send_segment=None).state == "not_heard"


def test_cannot_hear_needs_no_section():
    # a box that cannot demodulate the preset needs no evidence to say so
    assert judge(None, box_segment="ST", send_segment="LF").state == "cannot_hear"


# --- judge_send: one row per box, sorted, no box dropped --------------------

def test_judge_send_returns_every_box_once_sorted():
    rows = ih.judge_send(PID, SENT, {"moc": section(entries=[(FROM, PID, SENT + 1)]),
                                     "kiai": None, "lehua": section()}, now=NOW)
    assert [(r.alias, r.state) for r in rows] == [
        ("kiai", "unobservable"), ("lehua", "not_heard"), ("moc", "heard")]
