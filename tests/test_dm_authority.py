"""BIC-2 DM slice 1 -- THE seam (Fable r2, 2026-11-25): which source may
settle a row, and as what. The same defect kind ("failed" from a source
that must not settle it) recurred in r1 (F2) and r2 (#2, #3), each time
patched at one of two sources. Now ONE rule, pinned here:

| source                                   | may say                          | final?              |
|------------------------------------------|----------------------------------|---------------------|
| /e/ positive ack, from == dest, not own  | delivered                        | yes                 |
| /e/ NAK from dest (not a retry give-up)  | failed (said by the destination) | yes                 |
| /e/ NAK from anyone else (not a give-up) | failed (said by !id)             | no -- dest outranks |
| /e/ TIMEOUT / MAX_RETRANSMIT, any sender | a hint ("gave up after retries") | never               |
| journal ``Alloc an err`` (any code)      | a hint only (operator 11-25)     | never               |
| journal routing from dest                | "possible ack, not matched"      | never               |

Operator 2026-11-25 (AskUserQuestion): "Journal = hint, never final" --
any node's text can shape the journal (a newline in a received message
becomes its own prefixed journal entry, Fable r2 #1).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.dm_delivery import (  # noqa: E402
    DELIVERED, FAILED, ROW_DELIVERED, ROW_FAILED, ROW_LEFT_UNCONFIRMED,
    ROW_NO_ACK, ROW_WAITING, TERMINAL_SAID_BY, WAITING_S, JournalLine,
    RoutingReply, Send, Witness, classify, row_verdict,
)

ME = 0x6201CE25
P5 = 0xB03DA894
RELAY = 0x514EAE28
PID = 0xAB3C8AC6
T0 = 1_795_000_000.0
LIVE = Witness(since=T0 - 3600)
LATE = T0 + WAITING_S + 5


def _s(dest=P5, t=T0):
    return Send(t, PID, dest)


def _r(dt, sender, err="NONE", pid=PID, via_own=False):
    return RoutingReply(T0 + dt, sender, pid, err, via_own=via_own)


def _j(dt, err, pid=PID):
    return JournalLine(T0 + dt, "alloc_err", packet_id=pid, err=err)


class TestFinality:

    def test_only_the_destination_is_terminal(self):
        assert TERMINAL_SAID_BY == ("the destination",)

    @pytest.mark.parametrize("err", [6, 39, 1])
    def test_journal_refusal_is_a_hint_never_failed(self, err):
        v = row_verdict(_s(), [], LIVE, journal=[_j(0.1, err)], now=LATE)
        assert v.state == ROW_NO_ACK
        assert any("our radio reported" in h for h in v.hints)
        assert "journal" in v.detail

    def test_journal_hint_rides_the_waiting_row_too(self):
        v = row_verdict(_s(), [], LIVE, journal=[_j(0.1, 39)], now=T0 + 5)
        assert v.state == ROW_WAITING and "PKI_SEND_FAIL_PUBLIC_KEY" in v.detail

    def test_journal_line_outside_the_send_window_is_not_this_sends(self):
        for dt in (-3000, -5, WAITING_S + 10):
            v = row_verdict(_s(), [], LIVE, journal=[_j(dt, 6)], now=LATE + WAITING_S)
            assert not v.hints, dt

    @pytest.mark.parametrize("err", ["MAX_RETRANSMIT", "TIMEOUT"])
    @pytest.mark.parametrize("sender", [ME, RELAY, P5])
    def test_e_give_up_from_anyone_is_never_failed(self, err, sender):
        v = row_verdict(_s(), [_r(17, sender, err)], LIVE, now=LATE)
        assert v.state != ROW_FAILED
        assert any("gave up after retries" in h for h in v.hints)

    def test_destination_nak_is_preferred_over_an_earlier_relay_nak(self):
        v = row_verdict(_s(), [_r(4, RELAY, "NO_ROUTE"), _r(24, P5, "PKI_UNKNOWN_PUBKEY")],
                        LIVE, now=T0 + 30)
        assert v.state == ROW_FAILED and v.said_by == "the destination"
        assert v.reason == "PKI_UNKNOWN_PUBKEY"

    def test_delivered_outranks_any_nak(self):
        v = row_verdict(_s(), [_r(4, P5, "NO_ROUTE"), _r(6, P5)], LIVE, now=T0 + 7)
        assert v.state == ROW_DELIVERED

    def test_delivered_outranks_a_journal_line(self):
        v = row_verdict(_s(), [_r(3, P5)], LIVE, journal=[_j(0.5, 6)], now=T0 + 4)
        assert v.state == ROW_DELIVERED


class TestWireFacts:

    def test_an_ack_uplinked_by_its_own_sender_is_never_delivery(self):
        """A DM to this box's own radio: the only 'destination' ack is our
        radio's own, uplinked by itself (Fable r2 #7). No own-id needed."""
        v = row_verdict(_s(dest=ME), [_r(0.02, ME, via_own=True)], LIVE, now=LATE)
        assert v.state != ROW_DELIVERED
        assert any("own radio" in h for h in v.hints)

    def test_destination_ack_uplinked_by_us_is_delivery(self):
        v = row_verdict(_s(), [_r(2, P5, via_own=False)], LIVE, now=T0 + 3)
        assert v.state == ROW_DELIVERED

    def test_ack_stamped_before_the_send_is_said_never_silently_dropped(self):
        """hfm #6, Fable r2 #6: a backward clock step < 60 s."""
        v = row_verdict(_s(), [_r(-28, P5)], LIVE, now=LATE)
        assert v.state == ROW_NO_ACK
        assert any("before the send" in h for h in v.hints)

    def test_a_reply_for_the_same_id_long_before_is_not_this_send(self):
        """A 32-bit id reused an hour earlier is not a clock step."""
        v = row_verdict(_s(), [_r(-3600, P5)], LIVE, now=LATE)
        assert not v.hints

    def test_own_ack_alone_after_window_is_left_unconfirmed(self):
        v = row_verdict(_s(), [_r(0.02, ME, via_own=True)], LIVE, now=LATE)
        assert v.state == ROW_LEFT_UNCONFIRMED


class TestClassifyModes:
    """The 10-03 drill (scripts/dm_delivery_check.py) reads the radio's own
    API session, where a give-up IS the radio's answer: its default stays."""

    def test_drill_default_keeps_give_up_as_failed(self):
        (o,) = classify([_s()], [_r(40, ME, "MAX_RETRANSMIT")])
        assert o.status == FAILED

    def test_row_mode_never_fails_on_give_up(self):
        (o,) = classify([_s()], [_r(40, ME, "MAX_RETRANSMIT")], give_up_fails=False)
        assert o.status != FAILED and o.gave_up == "MAX_RETRANSMIT"

    def test_classify_delivered_outranks_nak_and_needs_the_send_bound(self):
        (o,) = classify([_s()], [_r(1, P5, "NO_ROUTE"), _r(2, P5)])
        assert o.status == DELIVERED
        (o,) = classify([_s()], [_r(-30, P5)])
        assert o.status != DELIVERED


class TestMutantKillers:

    def test_an_unparseable_destination_is_never_delivered(self):
        (o,) = classify([Send(T0, PID, "not-a-node")], [_r(2, P5)])
        assert o.status != DELIVERED

    def test_possible_ack_only_inside_the_send_window(self):
        late = [JournalLine(T0 + WAITING_S + 10, "routing_from", sender=P5)]
        v = row_verdict(_s(), [], LIVE, journal=late, now=T0 + 2 * WAITING_S + 20)
        assert v.possible_ack is False
        early = [JournalLine(T0 - 1, "routing_from", sender=P5)]
        assert row_verdict(_s(), [], LIVE, journal=early, now=LATE).possible_ack is False
