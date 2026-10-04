"""dm_delivery: a DM is DELIVERED only on a positive ack FROM its destination.

Fixtures follow the 2026-10-03 pilot's measured shape: our own node's
positive ack ~20 ms after the send, then the destination's ack 1-14 s later.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.dm_delivery import (  # noqa: E402
    DELIVERED, FAILED, LEFT_ONLY, NO_WITNESS, MIN_N_FOR_RATE,
    RoutingReply, Send, classify, summarize,
)

ME = 0x6201CE25
DEST = 0x5F01371F
RELAY = 0x55667788
T0 = 1_791_095_036.0


def _send(pid=0x8367CD80, t=T0, dest=DEST):
    return Send(t=t, packet_id=pid, dest=dest)


class TestClassify:

    def test_pilot_shape_self_ack_then_destination_is_delivered(self):
        s = _send()
        replies = [RoutingReply(T0 + 0.021, ME, s.packet_id, "NONE"),
                   RoutingReply(T0 + 2.3, DEST, s.packet_id, "NONE")]
        (o,) = classify([s], replies)
        assert o.status == DELIVERED
        assert abs(o.latency_s - 2.3) < 1e-6
        assert o.other_acks == 1

    def test_self_ack_alone_is_left_only_not_delivered(self):
        """The ~20 ms own-node ack proves the DM left, nothing more."""
        s = _send()
        (o,) = classify([s], [RoutingReply(T0 + 0.021, ME, s.packet_id, "NONE")])
        assert o.status == LEFT_ONLY

    def test_relay_ack_alone_is_left_only(self):
        s = _send()
        (o,) = classify([s], [RoutingReply(T0 + 1, RELAY, s.packet_id, "NONE")])
        assert o.status == LEFT_ONLY

    def test_nak_from_own_radio_is_failed_with_reason(self):
        s = _send()
        replies = [RoutingReply(T0 + 0.02, ME, s.packet_id, "NONE"),
                   RoutingReply(T0 + 40, ME, s.packet_id, "MAX_RETRANSMIT")]
        (o,) = classify([s], replies)
        assert o.status == FAILED and o.reason == "MAX_RETRANSMIT"

    def test_nothing_back_is_no_witness(self):
        (o,) = classify([_send()], [])
        assert o.status == NO_WITNESS and o.latency_s is None

    def test_reply_for_another_packet_is_ignored(self):
        s = _send()
        (o,) = classify([s], [RoutingReply(T0 + 2, DEST, 0xDEADBEEF, "NONE")])
        assert o.status == NO_WITNESS

    def test_destination_given_as_hex_id_still_matches(self):
        s = Send(t=T0, packet_id=7, dest="!5f01371f")
        (o,) = classify([s], [RoutingReply(T0 + 2, DEST, 7, "NONE")])
        assert o.status == DELIVERED

    def test_unknown_destination_never_delivers(self):
        s = Send(t=T0, packet_id=7, dest="moc5")
        (o,) = classify([s], [RoutingReply(T0 + 2, DEST, 7, "NONE")])
        assert o.status != DELIVERED

    def test_earliest_destination_ack_sets_latency(self):
        """lehua's acks arrived in pairs ~4 s apart (DM acks are want_ack)."""
        s = _send()
        replies = [RoutingReply(T0 + 5.1, DEST, s.packet_id, "NONE"),
                   RoutingReply(T0 + 1.4, DEST, s.packet_id, "NONE")]
        (o,) = classify([s], replies)
        assert abs(o.latency_s - 1.4) < 1e-6


class TestSummarize:

    def test_rate_refused_below_min_n(self):
        outs = classify([_send(pid=i) for i in range(1, MIN_N_FOR_RATE)], [])
        s = summarize(outs)
        assert s.sent == MIN_N_FOR_RATE - 1
        assert s.rate is None

    def test_rate_and_counts_at_min_n(self):
        sends = [_send(pid=i) for i in range(1, 11)]
        replies = [RoutingReply(T0 + 2, DEST, i, "NONE") for i in range(1, 8)]
        replies += [RoutingReply(T0 + 0.02, ME, 8, "NONE")]
        s = summarize(classify(sends, replies))
        assert s.counts == {DELIVERED: 7, FAILED: 0, LEFT_ONLY: 1, NO_WITNESS: 2}
        assert s.rate == 0.7
        assert s.median_latency_s == 2.0

    def test_empty_is_not_a_rate(self):
        s = summarize([])
        assert s.sent == 0 and s.rate is None


class TestCheckScriptArgs:
    """The sender refuses by default and refuses what cannot be measured."""

    def _mod(self):
        import importlib.util
        p = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'dm_delivery_check.py')
        spec = importlib.util.spec_from_file_location("dm_delivery_check", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_plan_only_without_send(self, capsys, monkeypatch):
        m = self._mod()
        monkeypatch.setattr(m, "run", lambda a: (_ for _ in ()).throw(AssertionError("transmitted")))
        assert m.main(["--dest", "!5f01371f", "--count", "3"]) == 0
        assert "plan only" in capsys.readouterr().out

    def test_broadcast_dest_refused(self):
        import pytest
        m = self._mod()
        with pytest.raises(SystemExit):
            m.parse_args(["--dest", "!ffffffff"])

    def test_name_dest_refused(self):
        import pytest
        m = self._mod()
        with pytest.raises(SystemExit):
            m.parse_args(["--dest", "moc5"])

    def test_flooding_gap_refused(self):
        import pytest
        m = self._mod()
        with pytest.raises(SystemExit):
            m.parse_args(["--dest", "!5f01371f", "--gap", "1"])
