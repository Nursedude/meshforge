"""Cross-preset hand-off witness (2026-09-30).

mesh_bridge hands a LongFast original to the RNS path (cid-only drop) and a
peer gateway re-broadcasts it on SHORT_TURBO. These pin the pairing that turns
"left to the RNS path, delivery unconfirmed" into a per-message answer, with
blind (secondary radio down) kept apart from lost (honest_failure_modes #2),
and a cid-only drop that was never handed off (oracle-consumed query, echo of
our own inject) kept apart from both. Cases marked (A#n)/(B#n) come from the
two contextless reviews of the first cut.
"""
import logging

from gateway._handoff_witness import HandoffWitness


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _w(observable=True, handed_off=True, **kw):
    clock = _Clock()
    state = {"seeing": observable}
    w = HandoffWitness(observable=lambda: state["seeing"],
                       handed_off=lambda cid: handed_off,
                       clock=clock, window_s=30.0, **kw)
    return w, clock, state


def _expire(w, clock):
    clock.t += 31
    return w.sweep()


def test_tagged_copy_with_suffix_line_confirms():
    """moc3's real copy: '[RNS:Lehua] 🎙Testing [RF]\\nSNR:6.0 … @kiai'."""
    w, clock, _ = _w()
    w.note("🎙Testing [RF]", "c1")
    assert w.observe("[RNS:Lehua] 🎙Testing [RF]\nSNR:6.0 RSSI:-83 @kiai")
    assert w.snapshot()['cross_preset_handoff_confirmed'] == 1
    assert _expire(w, clock) == []


def test_bare_prefix_does_not_confirm_short_text():
    """(A#2/B#4) native ST 'hike?' must not confirm a hand-off of 'hi'."""
    w, clock, _ = _w()
    w.note("hi", "c1")
    assert w.observe("hike?") is False
    assert w.observe("hi there") is False      # same line, not a suffix line
    assert _expire(w, clock) == ["hi"]


def test_exact_match_wins_over_prefix_so_confirmations_do_not_swap():
    """(A#2) 'test' then 'test 2' pending; the 'test 2' copy must confirm
    'test 2', not the older 'test'."""
    w, clock, _ = _w()
    w.note("test", "c1")
    w.note("test 2", "c2")
    assert w.observe("[RNS:L] test 2") is True
    assert w.observe("[RNS:L] test") is True
    assert _expire(w, clock) == []
    assert w.snapshot()['cross_preset_handoff_confirmed'] == 2


def test_first_chunk_of_a_split_copy_confirms():
    """(A#1/B#5) the peer's chunker tags EVERY chunk and splits long text."""
    long = ("Today: Scattered rain showers. Partly sunny, with a high near "
            "71. S SE wind around 6 mph. Chance of precipitation is 40%.")
    w, clock, _ = _w()
    w.note(long, "c1")
    assert w.observe("[RNS:Lehua] Today: Scattered rain showers. Partly sunny,")
    assert _expire(w, clock) == []


def test_short_fragment_does_not_confirm_by_partial_match():
    w, clock, _ = _w()
    w.note("Today: Scattered rain showers. Partly sunny.", "c1")
    assert w.observe("[RNS:L] Today:") is False      # < 24 chars
    assert _expire(w, clock) != []


def test_one_unheard_handoff_is_info_not_a_warning(caplog):
    """moc 2026-09-30 22:18:56: moc3 DID transmit the copy; moc's radio
    missed it (half-duplex / RF loss). One miss is not an outage."""
    w, clock, _ = _w()
    w.note("test", "c1")
    clock.t += 29
    assert w.sweep() == []                       # still inside the window
    clock.t += 2
    with caplog.at_level(logging.DEBUG):
        assert w.sweep() == ["test"]
    snap = w.snapshot()
    assert snap['cross_preset_handoff_unconfirmed'] == 1
    assert snap['cross_preset_handoff_unheard_streak'] == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.levelno == logging.INFO and "not heard" in r.getMessage()
               for r in caplog.records)


def _miss(w, clock, text):
    w.note(text, "c-" + text)
    clock.t += 31
    w.sweep()


def test_a_run_of_unheard_handoffs_warns(caplog):
    """An RNS or peer-gateway outage loses EVERY hand-off: a streak."""
    w, clock, _ = _w()
    with caplog.at_level(logging.INFO):
        _miss(w, clock, "m1")
        _miss(w, clock, "m2")
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        _miss(w, clock, "m3")
    warns = [r.getMessage() for r in caplog.records
             if r.levelno >= logging.WARNING]
    assert len(warns) == 1 and "3 in a row" in warns[0]
    assert w.snapshot()['cross_preset_handoff_unheard_streak'] == 3
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        _miss(w, clock, "m4")                    # still out: keeps warning
    assert any("4 in a row" in r.getMessage() for r in caplog.records)


def test_a_confirmation_resets_the_streak_and_marks_recovery(caplog):
    w, clock, _ = _w()
    for t in ("m1", "m2", "m3"):
        _miss(w, clock, t)
    w.note("back", "c9")
    with caplog.at_level(logging.WARNING):
        assert w.observe("[RNS:x] back") is True
    assert w.snapshot()['cross_preset_handoff_unheard_streak'] == 0
    assert any("recovered" in r.getMessage() for r in caplog.records)


def test_isolated_misses_between_confirmations_never_warn(caplog):
    w, clock, _ = _w()
    with caplog.at_level(logging.WARNING):
        for i in range(5):
            _miss(w, clock, f"miss{i}")
            w.note(f"ok{i}", f"k{i}")
            assert w.observe(f"[RNS:x] ok{i}")
    assert not caplog.records
    assert w.snapshot()['cross_preset_handoff_unconfirmed'] == 5


def test_blind_and_not_owed_do_not_touch_the_streak():
    w, clock, state = _w()
    _miss(w, clock, "m1")
    state["seeing"] = False
    _miss(w, clock, "blind")
    state["seeing"] = True
    w2 = w
    w2._handed_off = lambda cid: cid != "c-owed-not"
    _miss(w2, clock, "owed-not")
    assert w.snapshot()['cross_preset_handoff_unheard_streak'] == 1


def test_confirmation_leaves_an_info_line_with_the_delay(caplog):
    """INFO, not DEBUG (2026-10-10): the gateway runs at INFO once its level
    is honoured, and a confirmation is the witness's ONLY per-message success
    record — at DEBUG the journal would show the misses and never the hits."""
    w, clock, _ = _w()
    w.note("handoff test 1", "c1")
    clock.t += 12
    with caplog.at_level(logging.INFO):
        assert w.observe("[RNS:meshforge ] handoff test 1")
    assert any(r.levelno == logging.INFO and "confirmed" in r.getMessage()
               and "12.0s" in r.getMessage() for r in caplog.records)


def test_not_handed_off_is_neither_lost_nor_warned(caplog):
    """(B#1) an oracle-consumed '?status' is claimed by the M->R leg but
    never sent to RNS: nothing is owed on the other preset."""
    w, clock, _ = _w(handed_off=False)
    w.note("?status", "c1")
    with caplog.at_level(logging.WARNING):
        _expire(w, clock)
    snap = w.snapshot()
    assert snap['cross_preset_handoff_not_handed_off'] == 1
    assert snap['cross_preset_handoff_unconfirmed'] == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_blind_at_any_tick_in_the_window_is_unobservable():
    """(A#3/B#3) down at t+10, back up before expiry: blind, not lost."""
    w, clock, state = _w()
    w.note("test", "c1")
    clock.t += 10
    state["seeing"] = False
    w.sweep()
    state["seeing"] = True
    _expire(w, clock)
    snap = w.snapshot()
    assert snap['cross_preset_handoff_unobservable'] == 1
    assert snap['cross_preset_handoff_unconfirmed'] == 0


def test_blind_at_note_time_is_unobservable():
    w, clock, state = _w(observable=False)
    w.note("test", "c1")
    state["seeing"] = True
    _expire(w, clock)
    assert w.snapshot()['cross_preset_handoff_unobservable'] == 1


def test_observable_that_raises_reads_as_blind():
    clock = _Clock()

    def boom():
        raise RuntimeError("no state")
    w = HandoffWitness(observable=boom, handed_off=lambda c: True,
                       clock=clock, window_s=30.0)
    w.note("x1", "c1")
    _expire(w, clock)
    assert w.snapshot()['cross_preset_handoff_unobservable'] == 1


def test_handed_off_lookup_that_raises_never_hides_a_loss():
    clock = _Clock()

    def boom(cid):
        raise RuntimeError("registry gone")
    w = HandoffWitness(observable=lambda: True, handed_off=boom,
                       clock=clock, window_s=30.0)
    w.note("x1", "c1")
    _expire(w, clock)
    assert w.snapshot()['cross_preset_handoff_unconfirmed'] == 1


def test_copy_heard_before_the_drop_is_noted_still_confirms():
    """(A#5) MQTT backlog: the ST copy is processed before the LF drop."""
    w, clock, _ = _w()
    assert w.observe("[RNS:meshforge ] wx") is False
    clock.t += 3
    w.note("wx", "c1")
    assert w.snapshot()['cross_preset_handoff_confirmed'] == 1
    assert _expire(w, clock) == []


def test_each_handoff_needs_its_own_copy():
    w, clock, _ = _w()
    w.note("test", "c1")
    w.note("test", "c2")
    assert w.observe("[RNS:meshforge ] test") is True
    assert _expire(w, clock) == ["test"]
    snap = w.snapshot()
    assert snap['cross_preset_handoff_confirmed'] == 1
    assert snap['cross_preset_handoff_unconfirmed'] == 1


def test_overflow_is_counted_never_silent():
    w, _, _ = _w(max_pending=2)
    for i in range(3):
        w.note(f"m{i}", f"c{i}")
    assert w.snapshot()['cross_preset_handoff_untracked'] == 1


def test_empty_and_tag_only_text_is_ignored():
    w, clock, _ = _w()
    w.note("", "c1")
    w.note("[RNS:abcd]", "c2")
    assert _expire(w, clock) == []
    assert w.observe("") is False


def test_counters_are_pre_seeded():
    w, _, _ = _w()
    assert set(w.snapshot()) == {
        'cross_preset_handoff_confirmed', 'cross_preset_handoff_unconfirmed',
        'cross_preset_handoff_unobservable',
        'cross_preset_handoff_not_handed_off', 'cross_preset_handoff_untracked',
        'cross_preset_handoff_unheard_streak'}
    assert all(v == 0 for v in w.snapshot().values())


# -- review C/D of the streak change (2026-09-30) --------------------------

def _run_of_three(w, clock):
    for t in ("m1", "m2", "m3"):
        _miss(w, clock, t)


def test_outage_leftovers_expiring_after_recovery_do_not_re_warn(caplog):
    """(C#1) three hand-offs pending when RNS comes back, a LATER one is
    confirmed, then the three expire: no '3 in a row' after 'recovered'."""
    w, clock, _ = _w()
    for t in ("o1", "o2", "o3"):
        w.note(t, "c-" + t)
        clock.t += 2
    w.note("back", "c-back")
    assert w.observe("[RNS:x] back") is True
    clock.t += 31
    with caplog.at_level(logging.INFO):
        assert sorted(w.sweep()) == ["o1", "o2", "o3"]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("not counted toward the run" in r.getMessage()
               for r in caplog.records)
    snap = w.snapshot()
    assert snap['cross_preset_handoff_unconfirmed'] == 3
    assert snap['cross_preset_handoff_unheard_streak'] == 0


def test_short_untagged_native_text_confirms_nothing():
    """(D#3) a native SHORT_TURBO 'test' is not the peer's copy."""
    w, clock, _ = _w()
    w.note("test", "c1")
    assert w.observe("test") is False
    assert _expire(w, clock) == ["test"]


def test_untagged_long_copy_confirms_but_never_clears_the_alarm(caplog):
    """(C#3) a long text posted natively on both presets may confirm its
    hand-off, but must not end an outage run or log 'recovered'."""
    w, clock, _ = _w()
    _run_of_three(w, clock)
    beacon = "Net check-in tonight 19:00 on the meshforge channel, all welcome"
    w.note(beacon, "cb")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert w.observe(beacon) is True
    assert not caplog.records
    snap = w.snapshot()
    assert snap['cross_preset_handoff_confirmed'] == 1
    assert snap['cross_preset_handoff_unheard_streak'] == 3


def test_warning_says_receiving_when_other_traffic_was_heard(caplog):
    """(D) some other secondary traffic during the run ⇒ the radio hears."""
    w, clock, _ = _w()
    _miss(w, clock, "m1")
    w.observe("[Mesh:LONG_FAST:e001] unrelated chatter")
    _miss(w, clock, "m2")
    with caplog.at_level(logging.WARNING):
        _miss(w, clock, "m3")
    msg = caplog.records[-1].getMessage()
    assert "not heard by this box's secondary radio" in msg
    assert "heard 1 other broadcast(s)" in msg and "RNS path or peer" in msg


def test_warning_says_radio_may_be_deaf_when_nothing_was_heard(caplog):
    w, clock, _ = _w()
    with caplog.at_level(logging.WARNING):
        _run_of_three(w, clock)
    msg = caplog.records[-1].getMessage()
    assert "may not be receiving" in msg
    assert "RNS path or peer gateway down" not in msg


def test_recovered_is_logged_once(caplog):
    w, clock, _ = _w()
    _run_of_three(w, clock)
    for t in ("back1", "back2"):
        w.note(t, "c-" + t)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert w.observe("[RNS:x] back1")
        assert w.observe("[RNS:x] back2")
    assert len(caplog.records) == 1 and "recovered" in caplog.records[0].getMessage()


def test_early_tagged_copy_resets_the_run_and_says_it_came_first(caplog):
    w, clock, _ = _w()
    _run_of_three(w, clock)
    assert w.observe("[RNS:meshforge ] early bird") is False
    clock.t += 3
    with caplog.at_level(logging.DEBUG):
        w.note("early bird", "ce")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("3.0s before the drop was processed" in m for m in msgs)
    assert any("recovered" in m for m in msgs)
    assert w.snapshot()['cross_preset_handoff_unheard_streak'] == 0


# ── the journal can count hand-offs on its own (2026-10-10) ────────────────
# At INFO (5d57ce56 honours the gateway's level) the bridge's cid-only DROP
# line — the only record that a hand-off STARTED — is DEBUG, and so was the
# witness's `not_handed_off` outcome: the moc journal for 07:06–11:10 read
# 0 confirmed / 0 unheard, which cannot tell "no hand-offs" from "none owed".
# Counters are in-process only. So: one INFO "noted" line per hand-off, and
# every noted hand-off ends in exactly one INFO+ outcome line.

_NOTED = "hand-off noted"
_OUTCOMES = ("hand-off confirmed", "not heard by this box's secondary radio",
             "hand-off UNOBSERVABLE", "was not a hand-off", "hand-off untracked")


def _journal(caplog):
    """What an INFO journal would hold: (noted, outcomes) line counts."""
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    return (sum(_NOTED in s for s in msgs),
            sum(any(o in s for o in _OUTCOMES) for s in msgs))


def test_every_noted_handoff_leaves_an_info_line(caplog):
    w, clock, _ = _w()
    with caplog.at_level(logging.INFO):
        w.note("weather update one", "c1")
    assert _journal(caplog)[0] == 1, [r.getMessage() for r in caplog.records]


def test_not_handed_off_outcome_is_visible_at_info(caplog):
    w, clock, _ = _w(handed_off=False)
    with caplog.at_level(logging.INFO):
        w.note("?status", "c1")
        _expire(w, clock)
    assert _journal(caplog) == (1, 1), [r.getMessage() for r in caplog.records]


def test_overflow_is_visible_at_info(caplog):
    w, clock, _ = _w(max_pending=1)
    with caplog.at_level(logging.INFO):
        w.note("first pending message", "c1")
        w.note("second one overflows", "c2")
    noted, outcomes = _journal(caplog)
    assert noted == 2 and outcomes == 1, [r.getMessage() for r in caplog.records]


def test_journal_alone_accounts_for_every_handoff(caplog):
    """The 10-16 read's invariant: noted == outcomes, every path exercised —
    confirmed (late copy), confirmed (copy heard first), unheard, blind,
    not owed, overflow."""
    owed = {"c-late": True, "c-early": True, "c-miss": True, "c-blind": True,
            "c-notowed": False, "c-over": True}
    clock = _Clock()
    state = {"seeing": True}
    w = HandoffWitness(observable=lambda: state["seeing"],
                       handed_off=lambda cid: owed.get(cid, True),
                       clock=clock, window_s=30.0, max_pending=4)
    with caplog.at_level(logging.INFO):
        w.observe("[RNS:moc3] copy heard before its drop")          # early copy
        w.note("copy heard before its drop", "c-early")
        w.note("late copy will arrive", "c-late")
        w.note("this one is never heard", "c-miss")
        w.note("not owed, an oracle query", "c-notowed")
        w.note("blind window message here", "c-blind")
        w.note("overflowing the pending list", "c-over")             # 5th pending > 4
        clock.t += 5
        w.observe("[RNS:moc3] late copy will arrive")
        state["seeing"] = False
        w.sweep()                                                    # marks the blind tick
        state["seeing"] = True
        clock.t += 31
        w.sweep()
    noted, outcomes = _journal(caplog)
    total = sum(w.snapshot()[k] for k in (
        "cross_preset_handoff_confirmed", "cross_preset_handoff_unconfirmed",
        "cross_preset_handoff_unobservable", "cross_preset_handoff_not_handed_off",
        "cross_preset_handoff_untracked"))
    assert noted == 6 == outcomes == total, (noted, outcomes, total,
                                             [r.getMessage() for r in caplog.records])
