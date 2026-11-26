"""BIC-2 DM slice 1, piece 3 wiring: the map's local subscriber feeds the ack
witness every channel's /e/, while the old encrypted handler's scope stays
exactly what it was (its configured channel only)."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from monitoring import mqtt_settings  # noqa: E402
from monitoring.mqtt_subscriber import (  # noqa: E402
    MQTTNodelessSubscriber, create_local_subscriber, local_subscriber_config,
)


def _sub(witness):
    cfg = local_subscriber_config()
    if witness:
        cfg["ack_witness"] = True
    return MQTTNodelessSubscriber(config=cfg)


def _msg(topic, payload=b"\x01\x02"):
    return SimpleNamespace(topic=topic, payload=payload)


class FakeWitness:
    def __init__(self):
        self.seen, self.posture = [], []

    def observe(self, topic, payload, **kw):
        self.seen.append(topic)

    def started(self, *a, **kw):
        self.posture.append("started")

    def stopped(self, why, *a, **kw):
        self.posture.append(("stopped", why))


class TestSubscription:

    def test_witness_widens_the_encrypted_leg_to_every_channel(self):
        subs = mqtt_settings.subscriptions(dict(local_subscriber_config(), ack_witness=True))
        assert subs[1] == "msh/2/e/+/#"

    def test_without_witness_the_subscription_is_unchanged(self):
        cfg = local_subscriber_config()
        assert mqtt_settings.subscriptions(cfg)[1] == f"msh/2/e/{cfg['channel']}/#"

    def test_factory_opt_in_only(self):
        assert create_local_subscriber(ack_witness=True)._config.get("ack_witness") is True
        assert not create_local_subscriber()._config.get("ack_witness")

    def test_the_maps_inbox_listener_opts_in(self):
        from utils import message_listener as ml
        with patch.object(ml, "create_local_subscriber") as fac, \
                patch.object(ml, "_HAS_MQTT_SUBSCRIBER", True):
            fac.return_value.start.return_value = False
            ml.MessageListener(store_messages=False)._run_mqtt_mode()
        assert fac.called
        assert fac.call_args.kwargs.get("ack_witness") is True


class TestDispatch:

    def _run(self, witness_on, topics):
        s = _sub(witness_on)
        fw = FakeWitness()
        handled = []
        with patch.object(MQTTNodelessSubscriber, "_ack_witness", staticmethod(lambda: fw)), \
                patch.object(s, "_handle_encrypted_message",
                             lambda t, p: handled.append(t)), \
                patch.object(s, "_persist_map_cache", lambda: None), \
                patch.object(s, "_cleanup_stale_nodes", lambda: None):
            for t in topics:
                s._on_message(None, None, _msg(t))
        return fw, handled, s

    def test_witness_sees_every_channel_old_handler_only_its_own(self):
        own = local_subscriber_config()["channel"]
        topics = [f"msh/2/e/{own}/!6201ce25", "msh/2/e/Regional/!6201ce25",
                  "msh/2/e/PKI/!6201ce25"]
        fw, handled, _ = self._run(True, topics)
        assert fw.seen == topics
        assert handled == [f"msh/2/e/{own}/!6201ce25"]

    def test_without_witness_nothing_changes(self):
        own = local_subscriber_config()["channel"]
        fw, handled, _ = self._run(False, [f"msh/2/e/{own}/!6201ce25"])
        assert fw.seen == [] and handled == [f"msh/2/e/{own}/!6201ce25"]

    def test_json_leg_never_reaches_the_witness(self):
        s = _sub(True)
        fw = FakeWitness()
        with patch.object(MQTTNodelessSubscriber, "_ack_witness", staticmethod(lambda: fw)), \
                patch.object(s, "_handle_json_message", lambda t, p: None), \
                patch.object(s, "_persist_map_cache", lambda: None), \
                patch.object(s, "_cleanup_stale_nodes", lambda: None):
            s._on_message(None, None, _msg("msh/2/json/LongFast/!6201ce25", b"{}"))
        assert fw.seen == []

    def test_connect_starts_and_disconnect_blinds_the_witness(self):
        s = _sub(True)
        fw = FakeWitness()
        with patch.object(MQTTNodelessSubscriber, "_ack_witness", staticmethod(lambda: fw)), \
                patch.object(s, "_subscribe_topics", lambda: None), \
                patch.object(s, "_start_reconnect", lambda: None):
            s._on_connect(None, None, {}, 0)
            s._on_disconnect(None, None, 7)
        assert fw.posture == ["started", ("stopped", "MQTT disconnected (rc=7)")]


def test_r2_9_witness_only_traffic_never_moves_the_old_stats():
    """Fable r2 #9: messages_received / last_message_time are read as the
    old subscription's liveness -- other channels' /e/ must not freshen it."""
    s = _sub(True)
    fw = FakeWitness()
    own = local_subscriber_config()["channel"]
    with patch.object(MQTTNodelessSubscriber, "_ack_witness", staticmethod(lambda: fw)), \
            patch.object(s, "_handle_encrypted_message", lambda t, p: None), \
            patch.object(s, "_persist_map_cache", lambda: None), \
            patch.object(s, "_cleanup_stale_nodes", lambda: None):
        before = dict(s._stats)
        s._on_message(None, None, _msg("msh/2/e/PKI/!6201ce25"))
        assert s._stats.get("messages_received") == before.get("messages_received")
        assert s._stats.get("last_message_time") == before.get("last_message_time")
        assert s._stats.get("witness_only_envelopes") == 1
        s._on_message(None, None, _msg(f"msh/2/e/{own}/!6201ce25"))
        assert s._stats["messages_received"] == (before.get("messages_received") or 0) + 1
    assert fw.seen == ["msh/2/e/PKI/!6201ce25", f"msh/2/e/{own}/!6201ce25"]
