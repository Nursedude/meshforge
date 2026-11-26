"""BIC-2 DM slice 1, piece 3: the per-box /e/ ack witness.

Envelopes are built exactly as meshtasticd uplinks them (AES-CTR under the
default key, nonce = packet id ‖ from). Shapes follow the 2026-11-25 §4
captures: the DM itself is PKI (header readable, body not); the
destination's ack is channel-keyed on whatever channel it answered on.
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.dm_ack_witness import (  # noqa: E402
    COUNTERS, RING_CAP, AckWitness, get_witness,
)
from utils.dm_delivery import (  # noqa: E402
    ROW_DELIVERED, ROW_FAILED, ROW_NO_ACK, Send, WAITING_S, row_verdict,
)
from utils.meshtastic_se_crypto import (  # noqa: E402
    DEFAULT_KEY_B64, _nonce, crypto_available, expand_psk,
)

pytestmark = pytest.mark.skipif(
    not crypto_available(),
    reason="meshtastic protobufs / cryptography not installed (minimal-deps)")

ME = 0x6201CE25
P5 = 0xB03DA894
DM_ID = 0xAB3C8AC3
T0 = 1_795_000_000.0
TOPIC = "msh/2/e/LongFast/!6201ce25"


def _env(*, frm, to, pid, portnum=5, error=0, request_id=0, channel="LongFast",
         pki=False, key_b64=DEFAULT_KEY_B64, raw_payload=None, gateway="!6201ce25"):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from meshtastic.protobuf import mesh_pb2, mqtt_pb2
    data = mesh_pb2.Data()
    data.portnum = portnum
    if raw_payload is not None:
        data.payload = raw_payload
    elif portnum == 5:
        r = mesh_pb2.Routing()
        r.error_reason = error
        data.payload = r.SerializeToString()
    else:
        data.payload = b"secret words"
    data.request_id = request_id
    pkt = mesh_pb2.MeshPacket()
    pkt.id, pkt.to = pid, to
    setattr(pkt, "from", frm)
    if pki:
        pkt.encrypted = b"\x99" * 24
        pkt.pki_encrypted = True
    else:
        key = expand_psk(key_b64)
        enc = Cipher(algorithms.AES(key), modes.CTR(_nonce(pid, frm))).encryptor()
        pkt.encrypted = enc.update(data.SerializeToString()) + enc.finalize()
    se = mqtt_pb2.ServiceEnvelope()
    se.packet.CopyFrom(pkt)
    se.channel_id = channel
    se.gateway_id = gateway
    return se.SerializeToString()


def _dm(n=1):
    return _env(frm=ME, to=P5, pid=DM_ID, portnum=1, pki=True, channel="PKI")


def _ack(frm=P5, error=0, request_id=DM_ID, pid=0x5151, channel="LongFast"):
    return _env(frm=frm, to=ME, pid=pid, error=error, request_id=request_id,
                channel=channel)


def _live():
    w = AckWitness()
    w.started(now=T0 - 60)
    return w


class TestObserve:

    def test_destination_ack_is_recorded_ids_only(self):
        w = _live()
        w.observe(TOPIC, _ack(), now=T0 + 1.9)
        (r,) = w.snapshot(now=T0 + 2)["replies"]
        assert (r.sender, r.request_id, r.error) == (P5, DM_ID, "NONE")
        assert r.t == T0 + 1.9

    def test_nak_keeps_its_name(self):
        w = _live()
        w.observe(TOPIC, _ack(error=35), now=T0 + 24)
        (r,) = w.snapshot(now=T0 + 25)["replies"]
        assert r.error == "PKI_UNKNOWN_PUBKEY"

    def test_pki_dm_counted_as_a_copy_and_skipped(self):
        w = _live()
        for dt in (0, 7.3, 16.5):
            w.observe("msh/2/e/PKI/!6201ce25", _dm(), now=T0 + dt)
        s = w.snapshot(now=T0 + 20)
        assert s["tx"][DM_ID] == 3
        assert s["counts"]["pki_skipped"] == 3
        assert s["replies"] == []

    def test_broadcast_counted_never_decrypted_never_a_copy(self):
        w = _live()
        w.observe(TOPIC, _env(frm=P5, to=0xFFFFFFFF, pid=0x77, portnum=1), now=T0)
        s = w.snapshot(now=T0)
        assert s["counts"]["broadcast"] == 1
        assert s["counts"]["decrypted"] == 0
        assert s["tx"] == {}

    def test_custom_keyed_channel_is_a_decrypt_failure_not_an_ack(self):
        import base64
        w = _live()
        w.observe(TOPIC,
                  _env(frm=P5, to=ME, pid=0x52, request_id=DM_ID,
                       channel="Private",
                       key_b64=base64.b64encode(bytes(range(16))).decode()),
                  now=T0 + 2)
        s = w.snapshot(now=T0 + 3)
        assert s["replies"] == []
        assert s["counts"]["decrypt_failed"] + s["counts"]["decrypted"] == 1

    def test_garbage_payload_counted_never_raises(self):
        w = _live()
        w.observe(TOPIC, b"\xff\x00garbage", now=T0)
        w.observe(TOPIC, b"", now=T0)
        s = w.snapshot(now=T0)
        assert s["counts"]["envelopes"] == 2
        assert s["replies"] == []

    def test_non_routing_decrypt_keeps_nothing(self):
        w = _live()
        w.observe(TOPIC, _env(frm=P5, to=ME, pid=0x53, portnum=1), now=T0)
        s = w.snapshot(now=T0)
        assert s["replies"] == [] and s["counts"]["decrypted"] == 1

    def test_channels_seen_are_named_from_the_broker(self):
        w = _live()
        w.observe(TOPIC, _ack(channel="LongFast"), now=T0 + 1)
        w.observe(TOPIC, _ack(channel="Regional", pid=0x5152), now=T0 + 2)
        assert set(w.snapshot(now=T0 + 3)["channels"]) == {"LongFast", "Regional"}

    def test_ring_is_bounded(self):
        w = _live()
        payload = _ack()
        for i in range(RING_CAP + 5):
            w.observe(TOPIC, payload, now=T0 + i * 0.001)
        assert len(w.snapshot(now=T0 + 10)["replies"]) == RING_CAP

    def test_counters_closed_vocabulary(self):
        assert set(_live().snapshot(now=T0)["counts"]) == set(COUNTERS)


class TestPosture:

    def test_new_witness_is_blind_and_says_why(self):
        wt = AckWitness().snapshot(now=T0)["witness"]
        assert wt.since is None and wt.why

    def test_stop_blinds_and_restart_restarts_coverage(self):
        w = _live()
        w.stopped("MQTT disconnected")
        wt = w.snapshot(now=T0)["witness"]
        assert wt.since is None and "MQTT disconnected" in wt.why
        w.started(now=T0 + 100)
        assert w.snapshot(now=T0 + 101)["witness"].since == T0 + 100

    def test_get_witness_is_one_per_process(self):
        assert get_witness() is get_witness()

    def test_concurrent_observe_and_snapshot(self):
        w = _live()
        payload = _ack()
        errs = []

        def writer():
            try:
                for i in range(300):
                    w.observe(TOPIC, payload, now=T0 + i)
            except Exception as e:      # pragma: no cover - the failure
                errs.append(e)
        ts = [threading.Thread(target=writer) for _ in range(3)]
        for t in ts:
            t.start()
        for _ in range(200):
            w.snapshot(now=T0 + 1)
        for t in ts:
            t.join()
        assert not errs
        assert w.snapshot(now=T0 + 1000)["counts"]["routing"] == 900


class TestEndToEnd:
    """Witness → row_verdict: the §4 shapes reach the right row state."""

    def test_on_row_delivered(self):
        w = _live()
        w.observe("msh/2/e/PKI/!6201ce25", _dm(), now=T0)
        w.observe(TOPIC, _ack(frm=ME, pid=0x10), now=T0 + 0.02)   # own radio
        w.observe(TOPIC, _ack(), now=T0 + 1.9)
        s = w.snapshot(now=T0 + 3)
        v = row_verdict(Send(T0, DM_ID, P5), s["replies"], s["witness"],
                        tx_seen=s["tx"].get(DM_ID, 0), now=T0 + 3)
        assert v.state == ROW_DELIVERED

    def test_relay_nak_row_failed_by_destination(self):
        w = _live()
        w.observe(TOPIC, _ack(error=35), now=T0 + 24)
        s = w.snapshot(now=T0 + 25)
        v = row_verdict(Send(T0, DM_ID, P5), s["replies"], s["witness"], now=T0 + 25)
        assert v.state == ROW_FAILED and v.said_by == "the destination"

    def test_off_row_no_ack_sent_three_times(self):
        w = _live()
        for dt in (0, 7.3, 16.5):
            w.observe("msh/2/e/PKI/!6201ce25", _dm(), now=T0 + dt)
        now = T0 + WAITING_S + 5
        s = w.snapshot(now=now)
        v = row_verdict(Send(T0, DM_ID, P5), s["replies"], s["witness"],
                        tx_seen=s["tx"].get(DM_ID, 0), now=now)
        assert v.state == ROW_NO_ACK and v.sent_n == 3


class TestReviewR1:
    """code-review r1 findings 4 + 6 -- red on f9a23a48."""

    def test_f4_unparseable_routing_is_counted_never_a_nak(self):
        w = _live()
        w.observe(TOPIC, _env(frm=P5, to=ME, pid=0x61, request_id=DM_ID,
                              raw_payload=b"\xff\xff\xff"), now=T0 + 2)
        s = w.snapshot(now=T0 + 3)
        assert s["replies"] == []
        assert s["counts"]["routing_unparseable"] == 1

    def test_f6_another_gateways_uplink_is_not_a_retransmission(self):
        """Our radio uplinks its own DM with gateway == from; a bridged
        copy from another gateway is the same transmission, not a resend."""
        w = _live()
        w.observe("msh/2/e/PKI/!6201ce25", _dm(), now=T0)
        w.observe("msh/2/e/PKI/!5f01371f",
                  _env(frm=ME, to=P5, pid=DM_ID, portnum=1, pki=True, channel="PKI",
                       gateway="!5f01371f"), now=T0 + 0.5)
        assert w.snapshot(now=T0 + 1)["tx"][DM_ID] == 1


class TestReviewR2:
    """Fable r2 #4 + #7 at the witness -- red on d01fafa2."""

    def test_r2_7_own_radios_ack_is_marked_via_own(self):
        w = _live()
        w.observe(TOPIC, _ack(frm=ME, pid=0x10), now=T0 + 0.02)
        w.observe(TOPIC, _ack(), now=T0 + 2)
        own, dest = sorted(w.snapshot(now=T0 + 3)["replies"], key=lambda r: r.t)
        assert own.via_own is True and dest.via_own is False

    def test_r2_4_a_channel_is_visible_only_once_something_decrypted_on_it(self):
        import base64
        w = _live()
        w.observe("msh/2/e/PKI/!6201ce25", _dm(), now=T0)
        w.observe(TOPIC, _env(frm=P5, to=ME, pid=0x52, request_id=DM_ID, channel="Private",
                              key_b64=base64.b64encode(bytes(range(16))).decode()), now=T0 + 1)
        w.observe(TOPIC, _ack(channel="LongFast"), now=T0 + 2)
        s = w.snapshot(now=T0 + 3)
        assert set(s["decrypted"]) == {"LongFast"}
        assert {"PKI", "Private", "LongFast"} <= set(s["channels"])


class TestMutantsWitness:

    def test_mut_a_second_start_never_moves_coverage(self):
        w = AckWitness()
        w.started(now=T0)
        w.started(now=T0 + 50)
        assert w.snapshot(now=T0 + 60)["witness"].since == T0

    def test_mut_replies_older_than_the_ring_window_are_dropped(self):
        from utils.dm_ack_witness import RING_WINDOW_S
        w = _live()
        w.observe(TOPIC, _ack(), now=T0)
        assert w.snapshot(now=T0 + RING_WINDOW_S + 1)["replies"] == []
