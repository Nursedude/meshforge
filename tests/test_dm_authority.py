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
| a DM to our own radio (DM header)        | a hint ("own radio")             | never               |

Operator 2026-11-25, after Fable r3: the radio-log hints are CUT from
slice 1 -- the ``/e/`` ring is the only source. Only ``delivered`` is
final: the destination's later ack replaces even its own NAK.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.dm_delivery import (  # noqa: E402
    DELIVERED, FAILED, ROW_DELIVERED, ROW_FAILED, ROW_LEFT_UNCONFIRMED,
    ROW_NO_ACK, ROW_WAITING, TERMINAL_SAID_BY, WAITING_S,
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
    return Send(t, PID, dest, sender=ME)  # r4 #1: the DM's own uplinked header rules out a self-DM


def _r(dt, sender, err="NONE", pid=PID):
    return RoutingReply(T0 + dt, sender, pid, err)


class TestFinality:

    def test_only_the_destinations_failure_outranks_in_the_store(self):
        assert TERMINAL_SAID_BY == ("the destination",)

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

class TestWireFacts:

    def test_a_dm_to_our_own_radio_is_never_delivery(self):
        """Decided from the DM's OWN header (its uplinked from == dest), never
        from an ack's gateway (Fable r3 #1). No own-node id is inferred."""
        v = row_verdict(Send(T0, PID, ME, sender=ME), [_r(0.02, ME)], LIVE, now=LATE)
        assert v.state != ROW_DELIVERED
        assert any("own radio" in h for h in v.hints)

    def test_destination_ack_is_delivery_whoever_uplinked_it(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [_r(2, P5)], LIVE, now=T0 + 3)
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
        v = row_verdict(_s(), [_r(0.02, ME)], LIVE, now=LATE)
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
