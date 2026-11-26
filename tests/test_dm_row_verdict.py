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
    ROW_STATES, WAITING_S, JournalLine, RoutingReply, Send, Witness,
    routing_error_name, row_verdict,
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

    def test_nak_a_local_refusal_is_failed_said_by_our_radio(self):
        """Never-seen node: our radio refused (journal-only, exact idFrom);
        the DM never reached /e/."""
        s = _row(0xAB3C8ABF, dest=0x5EED1E56)
        j = [JournalLine(T0 + 0.1, "alloc_err", packet_id=0xAB3C8ABF, err=39)]
        v = row_verdict(s, [], LIVE, journal=j, now=T0 + 5)
        assert v.state == ROW_FAILED
        assert v.reason == "PKI_SEND_FAIL_PUBLIC_KEY"
        assert v.said_by == "our radio"

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

    def test_moc5_journal_routing_from_dest_is_possible_ack_never_delivered(self):
        """moc5 before the uplink flip: p5's ack in moc5's journal, absent
        from its broker. The journal has no request_id at INFO -> it can
        never decide 'delivered'."""
        s = _row(0x2A089819)
        j = [JournalLine(T0 + 2.5, "routing_from", sender=P5)]
        v = row_verdict(s, [], LIVE, journal=j, tx_seen=1,
                        now=T0 + WAITING_S + 1)
        assert v.state == ROW_NO_ACK
        assert v.possible_ack is True
        assert "possible ack, not matched" in v.why

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

    def test_refusal_for_another_packet_id_is_not_this_rows(self):
        s = _row(0xAB3C8AC0)
        j = [JournalLine(T0 + 0.1, "alloc_err", packet_id=0xAB3C8ABF, err=39)]
        v = row_verdict(s, [], LIVE, journal=j, now=T0 + 5)
        assert v.state == ROW_WAITING

    def test_journal_routing_from_someone_else_is_not_possible_ack(self):
        s = _row(0x2A089819)
        j = [JournalLine(T0 + 2.5, "routing_from", sender=FAR)]
        v = row_verdict(s, [], LIVE, journal=j, now=T0 + WAITING_S + 1)
        assert v.state == ROW_NO_ACK
        assert v.possible_ack is False

    def test_journal_routing_before_the_send_is_not_possible_ack(self):
        s = _row(0x2A089819)
        j = [JournalLine(T0 - 30, "routing_from", sender=P5)]
        v = row_verdict(s, [], LIVE, journal=j, now=T0 + WAITING_S + 1)
        assert v.possible_ack is False


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

    def test_journal_refusal_still_decides_when_mqtt_witness_is_blind(self):
        """The journal is its own witness for our radio's refusals."""
        s = _row(0xAB3C8ABF, dest=0x5EED1E56)
        blind = Witness(since=None, why="map MQTT listener not running")
        j = [JournalLine(T0 + 0.1, "alloc_err", packet_id=0xAB3C8ABF, err=39)]
        v = row_verdict(s, [], blind, journal=j, now=T0 + 5)
        assert v.state == ROW_FAILED

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

    @pytest.mark.parametrize("code,name", [
        (0, "NONE"), (5, "MAX_RETRANSMIT"), (35, "PKI_UNKNOWN_PUBKEY"),
        (39, "PKI_SEND_FAIL_PUBLIC_KEY")])
    def test_error_names_pinned_to_firmware_proto(self, code, name):
        """mesh.proto Routing.Error at firmware 54e0d8d."""
        assert routing_error_name(code) == name

    def test_unknown_error_code_is_named_not_dropped(self):
        assert routing_error_name(77) == "err=77"
