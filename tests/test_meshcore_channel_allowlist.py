"""Inbound MeshCore SOURCE-channel allowlist — the Public-channel leak.

Until 2026-09-18 nothing filtered inbound MeshCore channel traffic by
channel. ``_on_channel_message`` and ``_poll_channel_messages`` both went
straight to ``_should_bridge``, whose routing rules match ``source_network``
and carry NO channel field, so text from the **Public** channel (slot 0 —
the open channel every MeshCore node holds) was bridged onto the far mesh.
The reply could never come back: a channel sender is a display name, not a
DM-able contact, and the outbound rail refuses slot 0 by design (the
2026-05-19 leak fix). Leaky in exactly one direction, by ABSENCE of a
filter rather than a bad default.

⚠️ These tests drive the REAL async legs, not a re-implementation of them.
A test of ``_channel_bridge_allowed`` alone would pass even if a leg never
called it — and "a guard on one leg and not its symmetric twin" is the shape
this session found three separate times. The poll leg exists precisely to
carry the messages the event leg MISSES, so a filter on the event path alone
would leak exactly that traffic.
"""

import asyncio
import threading
from queue import Queue
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_config(**meshcore_kw):
    from gateway.config import GatewayConfig, MeshCoreConfig
    config = GatewayConfig()
    config.meshcore = MeshCoreConfig(
        enabled=True, simulation_mode=True, channel_poll_interval_sec=1,
    )
    for k, v in meshcore_kw.items():
        setattr(config.meshcore, k, v)
    return config


def _make_handler(config=None, queue=None):
    from gateway.meshcore_handler import MeshCoreHandler
    health = MagicMock()
    health.record_error = MagicMock(return_value="test")
    return MeshCoreHandler(
        config=config if config is not None else _make_config(),
        node_tracker=MagicMock(),
        health=health,
        stop_event=threading.Event(),
        stats={},
        stats_lock=threading.Lock(),
        message_queue=queue if queue is not None else Queue(maxsize=100),
    )


def _policy(config=None):
    """A ChannelPath on its own — no handler, no mocked health, no queue.

    Testing the policy without constructing a handler is the point of the
    2026-09-18 extraction: the inbound channel decision is one object's job.
    """
    from gateway.meshcore_channel_path import ChannelPath
    return ChannelPath(getattr(config, 'meshcore', None) if config is not None
                       else _make_config().meshcore)


def _channel_event(channel, text="hello"):
    """A CHANNEL_MSG_RECV-shaped event with a dict payload."""
    ev = MagicMock()
    ev.type = "CHANNEL_MSG_RECV"
    ev.payload = {
        'text': text, 'sender': 'deadbeef', 'is_channel': True,
        'channel': channel,
    }
    return ev


def _dm_event(text="dm hello"):
    ev = MagicMock()
    ev.type = "CONTACT_MSG_RECV"
    ev.payload = {
        'text': text, 'sender': 'deadbeef', 'is_channel': False,
        'destination': 'me',
    }
    return ev


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    """The allowlist reads an env var; a developer's shell must not decide."""
    monkeypatch.delenv("MESHFORGE_MESHCORE_BRIDGE_CHANNELS", raising=False)


class TestPredicateDefault:
    """Default posture: everything EXCEPT Public may bridge."""

    def test_public_is_refused_by_default(self):
        h = _policy()
        assert h.bridge_allowed(_msg(0)) is False

    @pytest.mark.parametrize("chan", [1, 2, 7])
    def test_non_public_is_allowed_by_default(self, chan):
        h = _policy()
        assert h.bridge_allowed(_msg(chan)) is True

    def test_unparseable_channel_is_refused(self):
        """Unobservable != healthy: an unknown source is not a safe source."""
        h = _policy()
        assert h.bridge_allowed(_msg("not-an-int")) is False


class TestPredicateExplicit:

    def test_env_list_is_honoured_exactly(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_MESHCORE_BRIDGE_CHANNELS", "0,2")
        h = _policy()
        assert h.bridge_allowed(_msg(0)) is True   # Public opt-IN
        assert h.bridge_allowed(_msg(2)) is True
        assert h.bridge_allowed(_msg(1)) is False

    def test_empty_env_means_bridge_nothing_not_bridge_all(self, monkeypatch):
        """An explicit empty list is a posture, not a missing value.

        Reinterpreting it as "allow all" would be the degraded-value-looks-
        valid class this codebase exists to refuse.
        """
        monkeypatch.setenv("MESHFORGE_MESHCORE_BRIDGE_CHANNELS", "")
        h = _policy()
        for chan in (0, 1, 5):
            assert h.bridge_allowed(_msg(chan)) is False

    def test_declared_config_used_when_env_absent(self):
        h = _policy(_make_config(bridge_source_channels=[3]))
        assert h.bridge_allowed(_msg(3)) is True
        assert h.bridge_allowed(_msg(0)) is False
        assert h.bridge_allowed(_msg(1)) is False

    def test_env_overrides_declared_config(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_MESHCORE_BRIDGE_CHANNELS", "9")
        h = _policy(_make_config(bridge_source_channels=[3]))
        assert h.bridge_allowed(_msg(9)) is True
        assert h.bridge_allowed(_msg(3)) is False

    def test_typo_is_loud_and_does_not_widen_the_gate(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_MESHCORE_BRIDGE_CHANNELS", "1,oops")
        h = _policy()
        assert h.bridge_allowed(_msg(1)) is True
        assert h.bridge_allowed(_msg(0)) is False


def _msg(channel):
    from gateway.canonical_message import CanonicalMessage
    return CanonicalMessage.from_meshcore(_channel_event(channel))


class TestEventLeg:
    """THE regression, on the event path."""

    def test_public_message_is_received_but_not_bridged(self):
        q = Queue(maxsize=100)
        h = _make_handler(queue=q)
        asyncio.run(h._on_channel_message(_channel_event(0)))
        m = h.get_channel_metrics()
        # Received (so this is a refusal, not a blindness) ...
        assert m['event_received'] == 1
        # ... and refused, with a witness.
        assert q.empty(), "Public channel message reached the bridge queue"
        assert m['channel_suppressed'] == 1

    def test_private_message_still_bridges(self):
        q = Queue(maxsize=100)
        h = _make_handler(queue=q)
        asyncio.run(h._on_channel_message(_channel_event(2)))
        assert not q.empty(), "non-Public channel message was dropped"
        assert h.get_channel_metrics()['channel_suppressed'] == 0

    def test_suppressed_message_does_not_reach_the_callback(self):
        """The callback is a second consumer — gating the queue alone would
        still hand Public traffic to whatever the callback feeds."""
        cb = MagicMock()
        h = _make_handler()
        h._message_callback = cb
        asyncio.run(h._on_channel_message(_channel_event(0)))
        cb.assert_not_called()
        asyncio.run(h._on_channel_message(_channel_event(1)))
        assert cb.call_count == 1


class TestPollLeg:
    """The symmetric twin. The poll path carries what the event path MISSES,
    so a filter on the event leg alone leaks exactly the fallback traffic."""

    def _armed_handler(self, queue):
        h = _make_handler(queue=queue)
        h._simulation_mode = False      # poll returns early in simulation
        h._connected = True
        h._last_channel_poll = 0.0      # force the interval open
        h._meshcore = MagicMock()
        return h

    def test_public_message_is_not_bridged_by_the_poll_path(self):
        q = Queue(maxsize=100)
        h = self._armed_handler(q)
        h._meshcore.commands.get_channel_messages = AsyncMock(
            return_value=[_channel_event(0)])
        asyncio.run(h._poll_channel_messages())
        m = h.get_channel_metrics()
        assert m['poll_discovered'] == 1, "poll leg never saw the message"
        assert q.empty(), "Public channel message reached the bridge via poll"
        assert m['channel_suppressed'] == 1

    def test_private_message_still_bridges_via_poll(self):
        q = Queue(maxsize=100)
        h = self._armed_handler(q)
        h._meshcore.commands.get_channel_messages = AsyncMock(
            return_value=[_channel_event(2)])
        asyncio.run(h._poll_channel_messages())
        assert not q.empty(), "non-Public poll message was dropped"
        assert h.get_channel_metrics()['channel_suppressed'] == 0


class TestDirectMessagesAreNotGated:
    """A DM has no channel, so a channel policy cannot speak to it.

    Over-gating here would be the cure costing more than the disease: the
    oracle already identity-gates DMs with channel=None.
    """

    def test_dm_still_bridges_with_public_refused(self):
        q = Queue(maxsize=100)
        h = _make_handler(queue=q)
        asyncio.run(h._on_contact_message(_dm_event()))
        assert not q.empty(), "DM was swallowed by the channel allowlist"
        assert h.get_channel_metrics()['channel_suppressed'] == 0


class TestWitness:
    """A swallow with no witness never happened (honest_failure_modes #9)."""

    def test_counter_exists_from_construction(self):
        assert 'channel_suppressed' in _make_handler().get_channel_metrics()

    def test_metrics_log_reports_suppressions(self, caplog):
        import logging
        h = _make_handler()
        asyncio.run(h._on_channel_message(_channel_event(0)))
        with caplog.at_level(logging.INFO):
            h._log_channel_metrics()
        assert "channel_suppressed=1" in caplog.text


class TestDeclaredConfigReallyLoads:
    """The knob must survive the REAL gateway.json path, not just a mock.

    ⚠️ Why this class exists: the first cut of this fix read the option with
    ``getattr(meshcore_config, 'bridge_source_channels', None)`` and the
    tests above set it with ``setattr`` on a dataclass instance. They passed
    while the option was UNDECLARED — and ``GatewayConfig.load`` builds the
    section as ``MeshCoreConfig(**meshcore_data)``, so an undeclared key in
    gateway.json raises TypeError and fails the WHOLE config load. The knob
    was not merely inert; declaring it would have broken the gateway. A test
    that pins my own mock instead of the consumer of record is the exact
    class this repo hunts.
    """

    def test_field_is_declared_on_the_dataclass(self):
        from gateway.config import MeshCoreConfig
        # Would raise TypeError: unexpected keyword argument before the fix.
        cfg = MeshCoreConfig(bridge_source_channels=[2])
        assert cfg.bridge_source_channels == [2]

    def test_default_is_none_meaning_all_but_public(self):
        from gateway.config import MeshCoreConfig
        assert MeshCoreConfig().bridge_source_channels is None

    def test_json_round_trip_reaches_the_predicate(self, tmp_path, monkeypatch):
        """gateway.json -> GatewayConfig.load() -> handler -> predicate."""
        import json
        from gateway.config import GatewayConfig

        cfg_file = tmp_path / "gateway.json"
        cfg_file.write_text(json.dumps({
            "meshcore": {
                "enabled": True,
                "simulation_mode": True,
                "bridge_source_channels": [0, 4],
            }
        }))
        monkeypatch.setattr(GatewayConfig, "get_config_path",
                            classmethod(lambda cls: cfg_file))
        loaded = GatewayConfig.load()
        assert loaded.meshcore.bridge_source_channels == [0, 4]

        # Through the HANDLER's own collaborator, not a bare ChannelPath —
        # this pins that the handler actually wires the loaded config in.
        h = _make_handler(loaded)._channel_path
        # Public was opted IN by the declared config — honoured exactly.
        assert h.bridge_allowed(_msg(0)) is True
        assert h.bridge_allowed(_msg(4)) is True
        assert h.bridge_allowed(_msg(1)) is False
