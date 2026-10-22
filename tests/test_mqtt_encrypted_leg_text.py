"""The local subscriber's encrypted leg must not re-emit text (2026-10-21).

On the local (own-radio) subscriber the JSON leg is subscribed on EVERY
channel ("json_channel": "+") and carries each uplinked text with its real
channel index. The encrypted leg decodes the SAME packet but its decoded
payload has no channel, so it was emitted as channel 0 — on the operator's box that
is the regional primary — with to_id "" (stored as a DM). Measured: 5/5 such rows on
10-21 had a JSON twin on channel 1. Node discovery from that leg stays.
"""
from unittest.mock import MagicMock, patch

from monitoring._mqtt_message_decoder import JSON_LEG_LIVE_S
from monitoring.mqtt_subscriber import MQTTNodelessSubscriber, create_local_subscriber

TOPIC = "msh/US/2/e/LongFast/!6201ce25"


def _decryptor():
    d = MagicMock(is_available=True)
    d.decrypt_packet.return_value = {"text": "Hey there ", "sender": 0x6201ce25,
                                     "packet_id": 2101968483}
    return d


def _run(sub):
    got = []
    sub.register_message_callback(got.append)
    with patch("utils.mqtt_decryptor.get_decryptor", return_value=_decryptor()):
        sub._handle_encrypted_message(TOPIC, b"\x00envelope")
    return got


def test_local_subscriber_does_not_emit_encrypted_leg_text_while_json_leg_is_live():
    sub = create_local_subscriber()
    sub._handle_json_message("msh/2/json/LongFast/!6201ce25",
                             b'{"type": "telemetry", "from": 1644285477, "payload": {}}')
    assert _run(sub) == []
    assert sub.get_messages() == []
    assert "!6201ce25" in sub._nodes                       # discovery kept
    assert sub.get_stats().get("encrypted_text_json_covered") == 1  # witness


def test_remote_subscriber_still_emits_decrypted_text():
    sub = MQTTNodelessSubscriber(config={"root_topic": "msh/US/2/e", "channel": "LongFast"})
    got = _run(sub)
    assert [m.text for m in got] == ["Hey there "]


def test_local_subscriber_keeps_encrypted_text_when_json_leg_is_silent():
    # json_channel "+" is CONFIG; with JSON uplink off on the radio, this leg
    # is the only text path — dropping it would blind the inbox silently
    sub = create_local_subscriber()
    assert [m.text for m in _run(sub)] == ["Hey there "]
    assert "encrypted_text_json_covered" not in sub.get_stats()


def test_a_json_leg_gone_quiet_stops_counting_as_coverage():
    sub = create_local_subscriber()
    sub._handle_json_message("msh/2/json/LongFast/!6201ce25", b'{"type": "telemetry"}')
    with patch("monitoring._mqtt_message_decoder.time.monotonic",
               return_value=sub._json_last_rx_mono + JSON_LEG_LIVE_S + 1):
        assert [m.text for m in _run(sub)] == ["Hey there "]
