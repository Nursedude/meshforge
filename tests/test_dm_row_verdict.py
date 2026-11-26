"""BIC-2 DM slice 1: one /inbox DM row -> exactly ONE of five states.

Every fixture is a row of the 2026-11-25 §4 measurement session
(`.claude/plans/bic2_dm_ack_groundwork_2026_11_25.md`, "design §4 session").
On that day every one of these rows read ``handed_off`` -- refused, failed
and delivered alike. The rule is the design record's §1 + §4b
(`.claude/plans/bic2_dm_delivery_design_2026_11_25.md`).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.dm_delivery import (  # noqa: E402
    ROW_DELIVERED, ROW_FAILED, ROW_LEFT_UNCONFIRMED, ROW_NO_ACK, ROW_WAITING,
    ROW_STATES, WAITING_S, RoutingReply, Send, Witness, row_verdict,
)

ME = 0x6201CE25          # the sender box's radio -- never told to the judge
P5 = 0xB03DA894          # the 0-hop test node
FAR = 0x514EAE28         # the 1-hop node
RELAY = 0xD8
T0 = 1_795_000_000.0
LIVE = Witness(since=T0 - 3600, why="")


def _row(pid, dest=P5, t=T0):
    return Send(t=t, packet_id=pid, dest=dest)


def _ack(dt, sender, pid, err="NONE"):
    return RoutingReply(T0 + dt, sender, pid, err)


class TestMeasuredRows:
    """The §4 table, row by row -- each read handed_off that day."""

    def test_relay_1_destination_nak_is_failed_said_by_destination(self):
        s = _row(0xAB3C8AC1, dest=FAR)
        v = row_verdict(s, [_ack(24, FAR, 0xAB3C8AC1, "PKI_UNKNOWN_PUBKEY")],
                        LIVE, now=T0 + 30)
        assert v.state == ROW_FAILED
        assert v.reason == "PKI_UNKNOWN_PUBKEY"
        assert v.said_by == "the destination"

    def test_relay_2_destination_ack_is_delivered(self):
        s = _row(0xAB3C8AC2, dest=FAR)
        v = row_verdict(s, [_ack(0.02, ME, 0xAB3C8AC2),
                            _ack(4, FAR, 0xAB3C8AC2)], LIVE, now=T0 + 5)
        assert v.state == ROW_DELIVERED
        assert v.latency_s == pytest.approx(4)
        assert v.late is False

    def test_on_ack_of_the_ack_does_not_confuse_the_row(self):
        """p5's ack carried want_ack; our radio acked the ACK (request_id =
        the ack's own id). That reply is not about our DM."""
        s = _row(0xAB3C8AC3)
        v = row_verdict(s, [_ack(1.9, P5, 0xAB3C8AC3),
                            _ack(2.0, ME, 0x11112222)], LIVE, now=T0 + 3)
        assert v.state == ROW_DELIVERED

    def test_off_1_inside_window_is_waiting(self):
        s = _row(0xAB3C8AC4)
        v = row_verdict(s, [_ack(0.02, ME, 0xAB3C8AC4)], LIVE,
                        tx_seen=2, now=T0 + 10)
        assert v.state == ROW_WAITING

    def test_off_1_window_closed_says_why_and_sent_n_times(self):
        """Radio give-up is invisible (n=2): a dead destination ends
        no-ack-seen, with the retransmissions counted -- never 'failed'."""
        s = _row(0xAB3C8AC4)
        v = row_verdict(s, [], LIVE, tx_seen=3, now=T0 + WAITING_S + 1)
        assert v.state == ROW_NO_ACK
        assert "window closed" in v.why
        assert v.sent_n == 3
        assert "sent 3×" in v.detail

    def test_replug_after_boot_is_delivered(self):
        s = _row(0xAB3C8AC6)
        v = row_verdict(s, [_ack(3.0, P5, 0xAB3C8AC6)], LIVE, now=T0 + 4)
        assert v.state == ROW_DELIVERED


class TestRule:

    def test_own_radio_ack_alone_after_window_is_left_not_delivered(self):
        s = _row(0xAB3C8AC4)
        v = row_verdict(s, [_ack(0.02, ME, 0xAB3C8AC4)], LIVE,
                        now=T0 + WAITING_S + 1)
        assert v.state == ROW_LEFT_UNCONFIRMED

    def test_late_ack_still_flips_the_row_and_is_marked_late(self):
        """A closed window never blocks a later 'delivered'; the match is a
        witness that the window is too short (late-ack counter)."""
        s = _row(0xAB3C8AC6)
        v = row_verdict(s, [_ack(WAITING_S + 30, P5, 0xAB3C8AC6)], LIVE,
                        now=T0 + WAITING_S + 31)
        assert v.state == ROW_DELIVERED
        assert v.late is True

    def test_relay_nak_keeps_who_distinct_from_destination(self):
        s = _row(0xAB3C8AC1, dest=FAR)
        v = row_verdict(s, [_ack(5, RELAY, 0xAB3C8AC1, "NO_ROUTE")], LIVE,
                        now=T0 + 6)
        assert v.state == ROW_FAILED
        assert v.said_by == "!000000d8"

class TestBlindWitness:
    """UNKNOWN is first-class: a box that cannot see acks says why."""

    def test_no_witness_says_its_why_never_waiting(self):
        s = _row(0xAB3C8AC4)
        blind = Witness(since=None, why="map MQTT listener not running")
        v = row_verdict(s, [], blind, now=T0 + 5)
        assert v.state == ROW_NO_ACK
        assert "map MQTT listener not running" in v.why

    def test_witness_started_after_send_says_so(self):
        """Q7: the ring is in memory; a send older than the witness cannot
        be judged from it."""
        s = _row(0xAB3C8AC4)
        late = Witness(since=T0 + 100, why="")
        v = row_verdict(s, [], late, now=T0 + 200)
        assert v.state == ROW_NO_ACK
        assert "witness started after this send" in v.why

    def test_wrong_subscription_control_never_delivered(self):
        """§4 #5: the witness on the wrong topic sees nothing -> no ack
        seen, never delivered."""
        s = _row(0xAB3C8AC3)
        v = row_verdict(s, [], LIVE, now=T0 + WAITING_S + 1)
        assert v.state == ROW_NO_ACK
        assert v.state != ROW_DELIVERED


class TestVocabulary:

    def test_exactly_five_states(self):
        assert set(ROW_STATES) == {ROW_DELIVERED, ROW_FAILED,
                                   ROW_LEFT_UNCONFIRMED, ROW_WAITING,
                                   ROW_NO_ACK}

    def test_every_verdict_is_one_of_the_five_and_has_detail(self):
        s = _row(0xAB3C8AC4)
        for replies, now in (([], T0 + 1), ([], T0 + 999),
                             ([_ack(1, P5, 0xAB3C8AC4)], T0 + 2),
                             ([_ack(0.02, ME, 0xAB3C8AC4)], T0 + 999)):
            v = row_verdict(s, replies, LIVE, now=now)
            assert v.state in ROW_STATES
            assert v.detail

    def test_waiting_window_is_the_measured_belief(self):
        """§4b Q2': 60 s, BELIEVED (n=6 acks, max 24 s)."""
        assert WAITING_S == 60


class TestClock:
    """hfm #6: RTC-less Pis, NTP steps -- a backward step must not hold a
    row in 'waiting' forever."""

    def test_send_stamped_in_the_future_is_said_not_waiting(self):
        s = _row(0xAB3C8AC4, t=T0 + 3600)
        v = row_verdict(s, [], LIVE, now=T0)
        assert v.state == ROW_NO_ACK and "clock" in v.why

    def test_small_skew_is_still_waiting(self):
        s = _row(0xAB3C8AC4, t=T0 + 2)
        assert row_verdict(s, [], LIVE, now=T0).state == ROW_WAITING


class TestReviewR1:
    """code-review r1 (2026-11-25) findings 2, 3, 9 -- red on f9a23a48."""

    def test_f3_ack_in_the_ring_counts_after_a_reconnect(self):
        """A broker blip moves witness.since past the send; the ring still
        holds the destination's ack -- it must still decide."""
        s = _row(0xAB3C8AC6)
        after = Witness(since=T0 + 120, why="")
        v = row_verdict(s, [_ack(5, P5, 0xAB3C8AC6)], after, now=T0 + 200)
        assert v.state == ROW_DELIVERED

    def test_f3_ack_in_the_ring_counts_while_the_witness_is_down(self):
        s = _row(0xAB3C8AC1, dest=FAR)
        down = Witness(since=None, why="MQTT disconnected (rc=7)")
        v = row_verdict(s, [_ack(24, FAR, 0xAB3C8AC1, "PKI_UNKNOWN_PUBKEY")], down,
                        now=T0 + 30)
        assert v.state == ROW_FAILED and v.said_by == "the destination"

    def test_f9_small_future_skew_never_prints_a_negative_wait(self):
        s = _row(0xAB3C8AC4, t=T0 + 20)
        v = row_verdict(s, [], LIVE, now=T0)
        assert v.state == ROW_WAITING and "-" not in v.detail.split("—")[1].split("s of")[0]
