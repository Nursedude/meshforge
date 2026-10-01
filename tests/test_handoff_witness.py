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


def test_unheard_handoff_is_unconfirmed_with_a_warning(caplog):
    w, clock, _ = _w()
    w.note("test", "c1")
    clock.t += 29
    assert w.sweep() == []                       # still inside the window
    clock.t += 2
    with caplog.at_level(logging.WARNING):
        assert w.sweep() == ["test"]
    snap = w.snapshot()
    assert snap['cross_preset_handoff_unconfirmed'] == 1
    assert any("UNCONFIRMED" in r.getMessage() for r in caplog.records)


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
        'cross_preset_handoff_not_handed_off', 'cross_preset_handoff_untracked'}
    assert all(v == 0 for v in w.snapshot().values())
