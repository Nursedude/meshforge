"""Tests for the Thread-2 step-4 Meshtastic ACK consumption primitives:
the ROUTING_APP parse, the NAK→DropReason mapping, and the in-flight
AckTracker. These are the pure units behind honest Meshtastic delivery
confirmation (#74) — the handler wiring is exercised in
test_meshtastic_handler.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.ack_tracker import (
    AckTracker,
    RoutingAck,
    DEFAULT_TTL_SEC,
    DEFAULT_MAX_PENDING,
    parse_routing_ack,
    routing_error_to_drop_reason,
    _NAK_DROP_REASON,
)
from gateway.delivery_counters import DropReason


# The #74 probe only counts a `dropped` as a delivery failure when its
# drop_reason is in this set. Every NAK we map MUST land here or the
# confirmation-rate denominator silently under-counts failures. Mirrored
# from watchdog_probes so a drift in either trips this test.
_DELIVERY_FAILURE_REASONS = frozenset({
    "rns_delivery_failed", "retries_exhausted", "destination_unreachable",
    "delivery_timeout", "non_retriable_error", "circuit_open", "wedged",
})


# ---------------------------------------------------------------------------
# parse_routing_ack
# ---------------------------------------------------------------------------

class TestParseRoutingAck:

    def test_positive_ack_empty_routing(self):
        """A success ACK: MessageToDict omits the zero NONE enum, so
        routing is {} — that is an ACK, not a malformed packet."""
        ack = parse_routing_ack(
            {'portnum': 'ROUTING_APP', 'requestId': 12345, 'routing': {}})
        assert ack == RoutingAck(request_id=12345, ok=True, reason="")

    def test_positive_ack_explicit_none(self):
        ack = parse_routing_ack(
            {'portnum': 'ROUTING_APP', 'requestId': 7,
             'routing': {'errorReason': 'NONE'}})
        assert ack.ok is True and ack.request_id == 7 and ack.reason == ""

    def test_positive_ack_no_routing_key(self):
        """request_id present, routing key entirely absent — still a
        positive ACK (no error reason == success)."""
        ack = parse_routing_ack({'portnum': 'ROUTING_APP', 'requestId': 9})
        assert ack.ok is True and ack.request_id == 9

    def test_nak_preserves_reason(self):
        ack = parse_routing_ack(
            {'portnum': 'ROUTING_APP', 'requestId': 99,
             'routing': {'errorReason': 'MAX_RETRANSMIT'}})
        assert ack == RoutingAck(
            request_id=99, ok=False, reason='MAX_RETRANSMIT')

    def test_wrong_portnum_is_none(self):
        assert parse_routing_ack(
            {'portnum': 'TEXT_MESSAGE_APP', 'requestId': 1}) is None

    def test_missing_request_id_is_none(self):
        """A routing packet with no request_id is not correlated to any
        send (e.g. an unsolicited routing error) — not ours."""
        assert parse_routing_ack(
            {'portnum': 'ROUTING_APP', 'routing': {'errorReason': 'NONE'}}) is None

    def test_zero_request_id_is_none(self):
        assert parse_routing_ack(
            {'portnum': 'ROUTING_APP', 'requestId': 0}) is None

    def test_bool_request_id_is_none(self):
        """A bool must never be coerced to int(1) and matched."""
        assert parse_routing_ack(
            {'portnum': 'ROUTING_APP', 'requestId': True}) is None

    def test_snake_case_fallbacks(self):
        """Tolerate snake_case keys defensively (some shims emit them)."""
        ack = parse_routing_ack(
            {'portnum': 'ROUTING_APP', 'request_id': 5,
             'routing': {'error_reason': 'NO_CHANNEL'}})
        assert ack.request_id == 5 and ack.ok is False
        assert ack.reason == 'NO_CHANNEL'

    def test_numeric_string_request_id_coerced(self):
        ack = parse_routing_ack(
            {'portnum': 'ROUTING_APP', 'requestId': '4242', 'routing': {}})
        assert ack.request_id == 4242 and ack.ok is True

    @pytest.mark.parametrize("bad", [None, [], "x", 42, {'portnum': 5}])
    def test_malformed_input_is_none(self, bad):
        assert parse_routing_ack(bad) is None


# ---------------------------------------------------------------------------
# routing_error_to_drop_reason
# ---------------------------------------------------------------------------

class TestRoutingErrorMapping:

    def test_known_reasons_map(self):
        assert routing_error_to_drop_reason('MAX_RETRANSMIT') is \
            DropReason.RETRIES_EXHAUSTED
        assert routing_error_to_drop_reason('NO_ROUTE') is \
            DropReason.DESTINATION_UNREACHABLE
        assert routing_error_to_drop_reason('NO_CHANNEL') is \
            DropReason.NON_RETRIABLE_ERROR
        assert routing_error_to_drop_reason('DUTY_CYCLE_LIMIT') is \
            DropReason.DELIVERY_TIMEOUT

    def test_unknown_reason_is_non_retriable(self):
        """An unmapped / future enum is still a real failure."""
        assert routing_error_to_drop_reason('SOME_NEW_2027_REASON') is \
            DropReason.NON_RETRIABLE_ERROR

    def test_case_insensitive(self):
        assert routing_error_to_drop_reason('max_retransmit') is \
            DropReason.RETRIES_EXHAUSTED

    def test_non_string_is_non_retriable(self):
        assert routing_error_to_drop_reason(None) is \
            DropReason.NON_RETRIABLE_ERROR

    def test_every_mapped_reason_counts_as_delivery_failure(self):
        """The load-bearing invariant: every NAK mapping must be a member
        of the #74 probe's delivery-failure set, else a NAK wouldn't count
        against the confirmation rate."""
        for reason, dr in _NAK_DROP_REASON.items():
            assert dr.value in _DELIVERY_FAILURE_REASONS, \
                f"{reason} -> {dr.value} not in _DELIVERY_FAILURE_REASONS"
        # The unknown fallback too.
        assert DropReason.NON_RETRIABLE_ERROR.value in _DELIVERY_FAILURE_REASONS

    def test_full_enum_covered(self):
        """All meshtastic Routing.Error names (non-NONE) are mapped — a
        new firmware enum would surface here as a reminder to map it."""
        expected = {
            "NO_ROUTE", "GOT_NAK", "TIMEOUT", "NO_INTERFACE",
            "MAX_RETRANSMIT", "NO_CHANNEL", "TOO_LARGE", "NO_RESPONSE",
            "DUTY_CYCLE_LIMIT", "BAD_REQUEST", "NOT_AUTHORIZED",
            "PKI_FAILED", "PKI_UNKNOWN_PUBKEY", "RATE_LIMIT_EXCEEDED",
        }
        assert set(_NAK_DROP_REASON) == expected


# ---------------------------------------------------------------------------
# AckTracker
# ---------------------------------------------------------------------------

DEST = 0x1A2B3C4D        # the DM's destination node
OUR_NODE = 0x0BADF00D    # this gateway's own radio
RELAY = 0x55667788       # a relay that rebroadcast the DM


@pytest.fixture
def clock(monkeypatch):
    """Controllable monotonic clock for the tracker."""
    import gateway.ack_tracker as at
    state = [1000.0]
    monkeypatch.setattr(at.time, "monotonic", lambda: state[0])
    return state


class TestAckFromDestination:
    """A positive ACK proves delivery only when the DESTINATION sent it.

    Firmware v2.7.26 ReliableRouter.cpp:54: when a radio overhears a relay
    rebroadcast its own packet it generates an IMPLICIT ack to itself
    (DMs included) — and MQTT.cpp:97 does the same on an MQTT echo — so
    a ROUTING_APP with a matching request_id and error NONE can come from
    our own node. That proves the DM left, not that it ARRIVED
    (delivered != relayed). The relay case is pinned too, defensively. NAKs are different:
    MAX_RETRANSMIT is generated by OUR radio (NextHopRouter.cpp:282), so a
    NAK resolves whoever sent it.
    """

    def test_ack_from_destination_confirms(self):
        t = AckTracker()
        t.register(77, 'm77', dest_num=DEST)
        assert t.resolve(77, from_num=DEST) == ('m77', 'meshtastic')

    def test_implicit_ack_from_our_own_radio_does_not_confirm(self):
        t = AckTracker()
        t.register(77, 'm77', dest_num=DEST)
        assert t.resolve(77, from_num=OUR_NODE) is None

    def test_ack_from_a_relay_does_not_confirm(self):
        t = AckTracker()
        t.register(77, 'm77', dest_num=DEST)
        assert t.resolve(77, from_num=RELAY) is None

    def test_relay_ack_keeps_waiting_for_the_real_one(self):
        """The relay's ack must not consume the entry: the destination's
        own ACK can still arrive after it."""
        t = AckTracker()
        t.register(77, 'm77', dest_num=DEST)
        assert t.resolve(77, from_num=OUR_NODE) is None
        assert t.pending_count() == 1
        assert t.resolve(77, from_num=DEST) == ('m77', 'meshtastic')

    def test_unknown_sender_does_not_confirm(self):
        """Absent sender = unverifiable, never proof."""
        t = AckTracker()
        t.register(77, 'm77', dest_num=DEST)
        assert t.resolve(77) is None
        assert t.pending_count() == 1

    def test_unknown_destination_does_not_confirm(self):
        """A registration without a destination can never be confirmed:
        there is nothing to check the sender against."""
        t = AckTracker()
        t.register(77, 'm77')
        assert t.resolve(77, from_num=DEST) is None

    def test_nak_from_our_own_radio_resolves(self):
        """MAX_RETRANSMIT comes from our own node — a real failure."""
        t = AckTracker()
        t.register(77, 'm77', dest_num=DEST)
        assert t.resolve(77, from_num=OUR_NODE, positive=False) == ('m77', 'meshtastic')
        assert t.pending_count() == 0

    def test_nak_without_sender_resolves(self):
        t = AckTracker()
        t.register(77, 'm77', dest_num=DEST)
        assert t.resolve(77, positive=False) == ('m77', 'meshtastic')

    def test_rejections_leave_a_witness(self):
        """Every refused ACK is counted by why (hfm #9)."""
        t = AckTracker()
        t.register(1, 'a', dest_num=DEST)
        t.register(2, 'b')
        t.resolve(1, from_num=OUR_NODE)
        t.resolve(1)
        t.resolve(2, from_num=DEST)
        assert t.rejected_counts() == {
            'not_from_destination': 1,
            'sender_unknown': 1,
            'destination_unknown': 1,
        }

    @pytest.mark.parametrize("bad", [True, "x", -1, 1.5])
    def test_bad_sender_is_unknown_not_a_match(self, bad):
        t = AckTracker()
        t.register(77, 'm77', dest_num=DEST)
        assert t.resolve(77, from_num=bad) is None

    def test_broadcast_destination_never_confirms(self):
        t = AckTracker()
        assert t.register(77, 'm77', dest_num=0xFFFFFFFF) is True
        assert t.resolve(77, from_num=0xFFFFFFFF) is None


class TestAckTrackerCore:

    def test_register_resolve_roundtrip(self):
        t = AckTracker()
        assert t.register(12345, 'msg-abc', dest_num=DEST) is True
        assert t.resolve(12345, from_num=DEST) == ('msg-abc', 'meshtastic')

    def test_resolve_is_single_shot(self):
        """A retransmitted/duplicate ACK resolves once — no double
        CONFIRMED."""
        t = AckTracker()
        t.register(5, 'm5', dest_num=DEST)
        assert t.resolve(5, from_num=DEST) == ('m5', 'meshtastic')
        assert t.resolve(5, from_num=DEST) is None

    def test_resolve_unknown_is_none(self):
        assert AckTracker().resolve(404) is None

    def test_custom_protocol_passthrough(self):
        t = AckTracker()
        t.register(1, 'm1', protocol='meshcore', dest_num=DEST)
        assert t.resolve(1, from_num=DEST) == ('m1', 'meshcore')

    @pytest.mark.parametrize("pid", [0, -1, True, "x", None, 1.5])
    def test_bad_packet_id_register_false(self, pid):
        assert AckTracker().register(pid, 'm', dest_num=DEST) is False

    @pytest.mark.parametrize("mid", ["", None, 42])
    def test_bad_msg_id_register_false(self, mid):
        assert AckTracker().register(123, mid, dest_num=DEST) is False

    def test_bool_request_id_resolve_none(self):
        t = AckTracker()
        t.register(1, 'm1', dest_num=DEST)
        assert t.resolve(True) is None

    def test_clear(self):
        t = AckTracker()
        t.register(1, 'a', dest_num=DEST)
        t.register(2, 'b', dest_num=DEST)
        assert t.clear() == 2
        assert t.pending_count() == 0


class TestAckTrackerTTL:

    def test_expires_after_ttl(self, clock):
        t = AckTracker(ttl_sec=100)
        t.register(7, 'm7', dest_num=DEST)
        clock[0] += 101            # advance past TTL
        assert t.resolve(7, from_num=DEST) is None

    def test_within_ttl_resolves(self, clock):
        t = AckTracker(ttl_sec=100)
        t.register(7, 'm7', dest_num=DEST)
        clock[0] += 99
        assert t.resolve(7, from_num=DEST) == ('m7', 'meshtastic')

    def test_expire_idle_prunes(self, clock):
        t = AckTracker(ttl_sec=100)
        t.register(1, 'a', dest_num=DEST)
        t.register(2, 'b', dest_num=DEST)
        clock[0] += 101
        assert t.expire_idle() == 2
        assert t.pending_count() == 0

    def test_uses_monotonic_not_wallclock(self, clock):
        """Regression on the #74 monotonic lesson: the tracker must read
        time.monotonic (patched by `clock`) — a wall-clock backstep can't
        freeze the timer. The fixture only patches monotonic; if the code
        read time.time() these would not see the advance."""
        t = AckTracker(ttl_sec=10)
        t.register(9, 'm9', dest_num=DEST)
        clock[0] += 11
        assert t.resolve(9, from_num=DEST) is None


class TestAckTrackerCap:

    def test_oldest_evicted_over_cap(self, clock):
        t = AckTracker(max_pending=3)
        for i in range(1, 4):
            clock[0] += 1
            t.register(i, f'm{i}', dest_num=DEST)
        # Cap full with 1,2,3. Adding a 4th evicts the oldest (1).
        clock[0] += 1
        t.register(4, 'm4', dest_num=DEST)
        assert t.pending_count() == 3
        assert t.resolve(1, from_num=DEST) is None         # evicted
        assert t.resolve(4, from_num=DEST) == ('m4', 'meshtastic')

    def test_pending_count_reflects_state(self):
        t = AckTracker()
        assert t.pending_count() == 0
        t.register(1, 'a', dest_num=DEST)
        t.register(2, 'b', dest_num=DEST)
        assert t.pending_count() == 2
        t.resolve(1, from_num=DEST)
        assert t.pending_count() == 1


class TestAckTrackerDefaults:

    def test_defaults_sane(self):
        assert DEFAULT_TTL_SEC == 600
        assert DEFAULT_MAX_PENDING == 1024

    def test_magicmock_safe_numerics(self):
        """Config numerics may arrive as MagicMock in tests — must fall
        back to defaults, never crash (mirrors the store idiom)."""
        from unittest.mock import MagicMock
        t = AckTracker(ttl_sec=MagicMock(), max_pending=MagicMock())
        assert t._ttl == DEFAULT_TTL_SEC
        assert t._max == DEFAULT_MAX_PENDING
