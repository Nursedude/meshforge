"""The local MQTT RX listener hears every uplinked channel, and says which (2026-10-13).

Measured on one fleet box: the radio received 21 texts in 3 h and uplinked JSON
only on the ``meshforge`` channel (25 publishes to ``msh/2/json/meshforge``),
while the listener was subscribed to the hard-coded ``msh/2/json/LongFast/#``.
It read "connected" with 0 messages; the browser inbox said "No conversations
yet". Another box worked only because its radio happens to uplink a channel NAMED
LongFast. Two fixes pinned here: subscribe JSON on every channel (``+``), and
record per-channel counts so the inbox and TUI can say what the box can see.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from monitoring import mqtt_subscriber as ms
from utils import message_listener as ml


def _subscribed(sub):
    client = MagicMock()
    sub._client = client
    sub._subscribe_topics()
    return [c.args[0] for c in client.subscribe.call_args_list]


def test_local_subscriber_hears_json_on_every_channel():
    topics = _subscribed(ms.create_local_subscriber())
    assert "msh/2/json/+/#" in topics, topics
    assert not any("/json/LongFast/" in t for t in topics), topics


def test_encrypted_leg_stays_on_the_default_key_channel():
    # decryption only ever uses the default public key: a wildcard here would
    # feed it every private channel's packets for nothing
    topics = _subscribed(ms.create_local_subscriber())
    assert "msh/2/e/LongFast/#" in topics, topics


def test_public_subscriber_is_unchanged():
    topics = _subscribed(ms.create_public_subscriber())
    assert any("/json/LongFast/" in t for t in topics), topics


def _mqtt_msg(channel, text="hi"):
    return SimpleNamespace(from_id="!a1b2c3d4", to_id="^all", text=text, channel=channel,
                           snr=5.0, rssi=-90, hop_start=3,
                           timestamp=__import__("datetime").datetime.now())


def test_listener_counts_what_it_hears_per_channel():
    lst = ml.MessageListener(store_messages=False)
    for ch in (0, 2, 2):
        lst._on_mqtt_message(_mqtt_msg(ch))
    st = lst.get_status().to_dict()
    assert st["channels_seen"] == {"0": 1, "2": 2}
    assert st["messages_received"] == 3


def test_a_fresh_listener_admits_it_has_seen_nothing():
    st = ml.MessageListener(store_messages=False).get_status().to_dict()
    assert st["channels_seen"] == {}
    assert "subscription" in st
