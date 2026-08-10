"""Tests for the Meshtastic broadcast bridge plug-in.

Symmetric mirror of MeshAnchor's tests/test_lxmf_broadcast_bridge.py.
The bridge piggybacks on an already-running RNS/LXMF stack, so these
tests mock the router/RNS/LXMF modules directly.

We exercise:
- SubscriberStore CRUD round-trips on a real SQLite file
- format_broadcast_text default + custom + bad template
- MeshtasticBroadcastBridge channel + protocol + broadcast-only filtering
- BridgedMessage shape compatibility (source_id used when source_address
  is absent — the MeshForge RX path emits BridgedMessage)
- Fan-out to subscribers
- Subscription protocol (subscribe / unsubscribe / channels / help)
- create_from_gateway_config gating
- Active-bridge accessor identity-guard
- get_status payload shape
- Issue #66 first-caller wiring: channel:<idx> placeholder origin,
  register_pending_ack call shape, LXMF delivery callback wiring

Run: python3 -m pytest tests/test_meshtastic_broadcast_bridge.py -v
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.config import MeshtasticBroadcastConfig
from gateway.meshtastic_broadcast_bridge import (
    MeshtasticBroadcastBridge,
    STATE_DEAD,
    STATE_DEGRADED,
    STATE_HEALTHY,
    STATE_STALE,
    STATE_UNCONFIRMED,
    Subscriber,
    SubscriberStore,
    TIER_EXTERNAL,
    TIER_LOCAL,
    _DEAD_SINCE_SUCCESS_SEC,
    _STALE_SINCE_SUCCESS_SEC,
    create_from_gateway_config,
    format_broadcast_text,
    get_active_broadcast_bridge,
)


@pytest.fixture(autouse=True)
def _allow_rns_tx_in_this_file():
    """This whole file exercises RNS/LXMF paths against MOCKED routers, so it
    declares RNS egress once here rather than per test. The RNS egress guard
    (utils.tx_guard) is fail-closed and cannot tell a mocked router from a real
    one — declaring the intent is the point."""
    from utils import tx_guard
    with tx_guard.allow_rns_egress():
        yield


# ---------------------------------------------------------------------------
# Lightweight message stand-in: matches BridgedMessage's field shape so the
# bridge's getattr-based helpers route through the same code path as the
# real MeshtasticHandler emits at runtime.
# ---------------------------------------------------------------------------


@dataclass
class _FakeBridged:
    source_network: str = "meshtastic"
    source_id: str = ""
    destination_id: Optional[str] = None
    content: str = ""
    is_broadcast: bool = False
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


def _make_bridged(
    *,
    text: str = "hello",
    channel: int = 0,
    sender: str = "!abcd1234",
    is_broadcast: bool = True,
    source_network: str = "meshtastic",
    extra_meta: Optional[Dict[str, Any]] = None,
) -> _FakeBridged:
    md = {"channel": channel}
    if extra_meta:
        md.update(extra_meta)
    return _FakeBridged(
        source_network=source_network,
        source_id=sender,
        destination_id=None if is_broadcast else "!11112222",
        content=text,
        is_broadcast=is_broadcast,
        metadata=md,
    )


# ---------------------------------------------------------------------------
# SubscriberStore
# ---------------------------------------------------------------------------


class TestSubscriberStore:
    def test_add_and_list(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        assert store.add("aabbccdd11223344") is True
        subs = store.list_all()
        assert len(subs) == 1
        assert subs[0].lxmf_hash == "aabbccdd11223344"

    def test_add_idempotent(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        assert store.add("deadbeef") is True
        assert store.add("deadbeef") is False
        assert len(store.list_all()) == 1

    def test_add_normalises_case(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("AABBCCDD")
        assert store.add("aabbccdd") is False
        assert store.list_all()[0].lxmf_hash == "aabbccdd"

    def test_remove(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef")
        assert store.remove("deadbeef") is True
        assert store.remove("deadbeef") is False

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "subs.db"
        SubscriberStore(path).add("cafebabe")
        assert any(
            s.lxmf_hash == "cafebabe" for s in SubscriberStore(path).list_all()
        )

    def test_mark_fanout_enqueued_updates_timestamp(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef")
        store.mark_fanout_enqueued("deadbeef")
        sub = store.list_all()[0]
        assert sub.last_delivery is not None
        # An enqueue is NOT a confirmed delivery — it must not stamp the
        # confirmable anchor (finding-2).
        assert sub.last_confirmed_at is None

    def test_mark_confirmed_stamps_confirmable_anchor(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef")
        store.mark_confirmed("deadbeef")
        sub = store.list_all()[0]
        assert sub.last_confirmed_at is not None
        assert sub.state == STATE_HEALTHY

    def test_mark_failed_increments(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef")
        for _ in range(3):
            store.mark_failed("deadbeef", "test_reason")
        sub = store.list_all()[0]
        assert sub.consecutive_failures == 3

    def test_mark_failed_unknown_hash_is_noop(self, tmp_path):
        """Ephemeral _reply path must not crash on unknown row."""
        store = SubscriberStore(tmp_path / "subs.db")
        store.mark_failed("nonexistent000000000000", "reason")
        assert store.list_all() == []


# ---------------------------------------------------------------------------
# format_broadcast_text
# ---------------------------------------------------------------------------


class TestFormatBroadcastText:
    def test_default_format(self):
        msg = _make_bridged(text="ping", channel=1, sender="!alice")
        out = format_broadcast_text(
            msg, "[meshtastic ch{channel}:{sender}] {text}"
        )
        assert "ping" in out
        assert "ch1" in out
        assert "!alice" in out

    def test_custom_format(self):
        msg = _make_bridged(text="ping", channel=2, sender="zzz")
        out = format_broadcast_text(msg, "{sender}@{channel}> {text}")
        assert out == "zzz@2> ping"

    def test_truncates_long_sender(self):
        msg = _make_bridged(sender="a" * 64)
        out = format_broadcast_text(msg, "{sender}: {text}")
        assert out.startswith("a" * 16 + ":")

    def test_bad_template_falls_back(self):
        msg = _make_bridged(text="ping", channel=1, sender="abc")
        out = format_broadcast_text(msg, "[{missing}] {text}")
        assert "ping" in out


# ---------------------------------------------------------------------------
# MeshtasticBroadcastBridge — fixtures
# ---------------------------------------------------------------------------


def _make_fake_lxmf(*, register_returns_source: bool = True,
                    source_hash_hex: str = "aabbccddeeff0011"):
    lxmf = MagicMock(name="LXMF")
    lxmf.LXMessage = MagicMock(name="LXMessage")
    router = MagicMock(name="LXMRouter")
    if register_returns_source:
        src = MagicMock()
        src.hash = bytes.fromhex(source_hash_hex)
        router.register_delivery_identity.return_value = src
    else:
        router.register_delivery_identity.return_value = None
    lxmf.LXMRouter.return_value = router
    return lxmf, router


@pytest.fixture
def fake_rns_lxmf():
    rns = MagicMock(name="RNS")
    rns.Identity = MagicMock()
    rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
    rns.Identity.return_value = MagicMock(name="identity")

    rns.Transport = MagicMock()
    rns.Transport.has_path = MagicMock(return_value=True)
    rns.Transport.request_path = MagicMock()

    rns.Identity.recall = MagicMock(return_value=MagicMock(name="dest_identity"))

    rns.Destination = MagicMock()
    rns.Destination.OUT = "OUT"
    rns.Destination.SINGLE = "SINGLE"

    lxmf, router = _make_fake_lxmf()
    return rns, lxmf, router


def _make_bridge(tmp_path, fake_rns_lxmf, *, enabled=True, channels=None,
                 autosubscribe=False, prefix=None, ack_required=False,
                 persistent_queue=None, ack_emit_callback=None):
    rns, lxmf, _router = fake_rns_lxmf
    cfg = MeshtasticBroadcastConfig(
        enabled=enabled,
        channels=channels if channels is not None else [0, 2],
        display_name="Test Meshtastic Broadcast",
        announce_interval_sec=0,  # disable announce thread
        prefix_format=prefix or "[meshtastic ch{channel}:{sender}] {text}",
        autosubscribe=autosubscribe,
        ack_required=ack_required,
    )
    return MeshtasticBroadcastBridge(
        broadcast_config=cfg,
        rns_module=rns,
        lxmf_module=lxmf,
        identity_path=tmp_path / "identity",
        db_path=tmp_path / "subs.db",
        storage_path=tmp_path / "storage",
        persistent_queue=persistent_queue,
        ack_emit_callback=ack_emit_callback,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestBridgeLifecycle:
    def test_start_registers_identity(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        try:
            assert b.start() is True
            _rns, lxmf, router = fake_rns_lxmf
            lxmf.LXMRouter.assert_called_once()
            router.register_delivery_identity.assert_called_once()
            router.announce.assert_called_once()
            assert b.is_running is True
            assert b.destination_hash_hex == "aabbccddeeff0011"
        finally:
            b.stop()

    def test_start_fails_when_router_returns_none(self, tmp_path):
        """LXMF 0.9.4 returns None if a delivery identity is already
        registered. Bridge must fail start() instead of silently coming
        up with no usable hash."""
        rns = MagicMock()
        rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
        rns.Identity.return_value = MagicMock()
        lxmf, _router = _make_fake_lxmf(register_returns_source=False)
        cfg = MeshtasticBroadcastConfig(enabled=True, announce_interval_sec=0)
        b = MeshtasticBroadcastBridge(
            broadcast_config=cfg,
            rns_module=rns,
            lxmf_module=lxmf,
            identity_path=tmp_path / "id",
            db_path=tmp_path / "subs.db",
            storage_path=tmp_path / "storage",
        )
        assert b.start() is False
        assert b.is_running is False
        assert b.destination_hash_hex == ""

    def test_stop_is_safe_when_not_running(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.stop()  # must not raise


# ---------------------------------------------------------------------------
# Meshtastic RX filtering & fan-out
# ---------------------------------------------------------------------------


class TestMeshtasticFanout:
    def test_filters_non_meshtastic(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(source_network="rns", text="hi")
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
            assert b.stats["filtered_non_meshtastic"] == 1
        finally:
            b.stop()

    def test_filters_non_broadcast(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(is_broadcast=False)
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
        finally:
            b.stop()

    def test_filters_disallowed_channel(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(channel=7, text="off-channel")
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
            assert b.stats["filtered_channel"] == 1
        finally:
            b.stop()

    def test_skips_empty_content(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            b.on_meshtastic_message(_make_bridged(text=""))
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
        finally:
            b.stop()

    def test_fans_out_to_each_subscriber(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            b._subs.add("aaaa000000000002")
            msg = _make_bridged(channel=0, text="ping", sender="!alice")
            b.on_meshtastic_message(msg)
            _rns, lxmf, router = fake_rns_lxmf
            assert lxmf.LXMessage.call_count == 2
            assert router.handle_outbound.call_count == 2
            first_call_args = lxmf.LXMessage.call_args_list[0].args
            assert "ping" in first_call_args[2]
            assert "!alice" in first_call_args[2]
            assert b.stats["fanouts"] == 2
        finally:
            b.stop()

    def test_no_subscribers_no_fanout(self, tmp_path, fake_rns_lxmf):
        """Empty subscriber set is a clean no-op — no exception."""
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b.on_meshtastic_message(_make_bridged(text="ping"))
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
        finally:
            b.stop()


# ---------------------------------------------------------------------------
# Loop guard — Issue #66 synth-ACKs round-trip through LoRa and re-enter
# MeshtasticHandler with the in-process metadata flag stripped. The bridge
# must recognize them by textual content prefix.
# ---------------------------------------------------------------------------


class TestSynthAckLoopGuard:
    """Field smoke on moc3 (2026-05-18 22:21 HST) caught a 4-emission loop:
    bridge fan-out → synth ACK on Meshtastic → MQTT-bridge re-emit on peer
    → MeshtasticHandler RX → bridge sees `[delivered: mbcast-...]` as a new
    broadcast → fans out again. The wire only carries the textual ACK
    body; the meshforge_is_synth_ack metadata flag is in-process only.
    """

    def test_skips_delivered_synth_ack(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(text="[delivered: mbcast-foo] orig", channel=0)
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
            assert b.stats["filtered_synth_ack"] == 1
        finally:
            b.stop()

    def test_skips_timeout_synth_ack(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(text="[timeout: mbcast-bar]", channel=0)
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
            assert b.stats["filtered_synth_ack"] == 1
        finally:
            b.stop()

    def test_skips_failed_synth_ack(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(text="[failed: mbcast-baz]", channel=0)
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
            assert b.stats["filtered_synth_ack"] == 1
        finally:
            b.stop()

    def test_skips_leading_whitespace_synth_ack(self, tmp_path, fake_rns_lxmf):
        """Defensive: some emit paths may pad the body. lstrip first."""
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(text="   [delivered: mbcast-pad]", channel=0)
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_not_called()
            assert b.stats["filtered_synth_ack"] == 1
        finally:
            b.stop()

    def test_does_not_skip_user_message_mentioning_delivered(
        self, tmp_path, fake_rns_lxmf,
    ):
        """The guard checks for prefix shape, not the word — a user
        message that happens to talk about delivery (`"ack received,
        [delivered: ...] now relaying"`) is fine. Only the bridge's
        own synth output starts with `[delivered:`."""
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(
                text="ack received, [delivered: ...] now relaying", channel=0,
            )
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            lxmf.LXMessage.assert_called_once()
            assert b.stats["filtered_synth_ack"] == 0
            assert b.stats["fanouts"] == 1
        finally:
            b.stop()


# ---------------------------------------------------------------------------
# BridgedMessage compatibility — the production RX path uses source_id, not
# source_address; the format/origin-routing helpers must pick the right one.
# ---------------------------------------------------------------------------


class TestBridgedMessageCompatibility:
    def test_source_id_used_in_format_when_no_source_address(
        self, tmp_path, fake_rns_lxmf,
    ):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            msg = _make_bridged(text="hi", sender="!bob1234")
            b.on_meshtastic_message(msg)
            _rns, lxmf, _router = fake_rns_lxmf
            call_args = lxmf.LXMessage.call_args_list[0].args
            # Sender should appear in the body
            assert "!bob1234" in call_args[2]
        finally:
            b.stop()


# ---------------------------------------------------------------------------
# Subscription protocol
# ---------------------------------------------------------------------------


def _fake_lxmf_message(*, source_hash: bytes, body: str):
    msg = MagicMock()
    msg.source_hash = source_hash
    msg.content = body.encode("utf-8")
    msg.title = b"test"
    return msg


class TestSubscriptionProtocol:
    def test_subscribe_adds_subscriber(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            source = bytes.fromhex("aaaa000000000003")
            msg = _fake_lxmf_message(source_hash=source, body="subscribe please")
            b._on_lxmf_delivery(msg)
            hashes = [s.lxmf_hash for s in b._subs.list_all()]
            assert "aaaa000000000003" in hashes
            assert b.stats["subscribes"] == 1
        finally:
            b.stop()

    def test_unsubscribe_removes_subscriber(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("aaaa000000000003")
            msg = _fake_lxmf_message(
                source_hash=bytes.fromhex("aaaa000000000003"),
                body="unsubscribe",
            )
            b._on_lxmf_delivery(msg)
            assert b._subs.list_all() == []
            assert b.stats["unsubscribes"] == 1
        finally:
            b.stop()

    def test_unknown_verb_does_not_subscribe_by_default(
        self, tmp_path, fake_rns_lxmf,
    ):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            msg = _fake_lxmf_message(
                source_hash=bytes.fromhex("aaaa000000000003"),
                body="hello world",
            )
            b._on_lxmf_delivery(msg)
            assert b._subs.list_all() == []
        finally:
            b.stop()

    def test_autosubscribe_adds_on_unknown_verb(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, autosubscribe=True)
        b.start()
        try:
            msg = _fake_lxmf_message(
                source_hash=bytes.fromhex("aaaa000000000004"),
                body="hi there",
            )
            b._on_lxmf_delivery(msg)
            hashes = [s.lxmf_hash for s in b._subs.list_all()]
            assert "aaaa000000000004" in hashes
        finally:
            b.stop()


# ---------------------------------------------------------------------------
# create_from_gateway_config gating
# ---------------------------------------------------------------------------


class TestFactory:
    def test_returns_none_when_disabled(self):
        gateway_config = MagicMock()
        gateway_config.meshtastic_broadcast = MeshtasticBroadcastConfig(
            enabled=False
        )
        assert create_from_gateway_config(gateway_config) is None

    def test_returns_instance_when_enabled(self, tmp_path):
        gateway_config = MagicMock()
        gateway_config.meshtastic_broadcast = MeshtasticBroadcastConfig(
            enabled=True,
            identity_file=str(tmp_path / "id"),
            db_file=str(tmp_path / "subs.db"),
        )
        gateway_config.rns = MagicMock(propagation_node="")
        bridge = create_from_gateway_config(gateway_config)
        assert isinstance(bridge, MeshtasticBroadcastBridge)

    def test_returns_none_when_attribute_missing(self):
        """A GatewayConfig that pre-dates the meshtastic_broadcast field
        should not blow up — return None and let the parent bridge skip."""
        gateway_config = object()  # no attributes at all
        assert create_from_gateway_config(gateway_config) is None


# ---------------------------------------------------------------------------
# Active-bridge accessor (config_api / status seam)
# ---------------------------------------------------------------------------


class TestActiveBridgeAccessor:
    def test_start_registers_active_bridge(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        assert b.start() is True
        try:
            assert get_active_broadcast_bridge() is b
        finally:
            b.stop()
        assert get_active_broadcast_bridge() is None

    def test_stop_only_clears_self(self, tmp_path, fake_rns_lxmf):
        """A stale stop() from a previous bridge must NOT clobber a
        freshly-started replacement (identity guard)."""
        b1 = _make_bridge(tmp_path / "a", fake_rns_lxmf)
        b1.start()

        rns2 = MagicMock()
        rns2.Identity.from_file = MagicMock(side_effect=Exception("no file"))
        rns2.Identity.return_value = MagicMock()
        rns2.Transport = MagicMock()
        rns2.Transport.has_path = MagicMock(return_value=True)
        rns2.Identity.recall = MagicMock(return_value=MagicMock())
        rns2.Destination = MagicMock()
        lxmf2, _r2 = _make_fake_lxmf(source_hash_hex="bbccddeeff001122")
        cfg = MeshtasticBroadcastConfig(
            enabled=True, channels=[0], display_name="B2",
            announce_interval_sec=0,
        )
        b2 = MeshtasticBroadcastBridge(
            broadcast_config=cfg,
            rns_module=rns2,
            lxmf_module=lxmf2,
            identity_path=tmp_path / "b" / "id",
            db_path=tmp_path / "b" / "subs.db",
            storage_path=tmp_path / "b" / "storage",
        )
        b2.start()
        assert get_active_broadcast_bridge() is b2
        try:
            b1.stop()  # no-op for the active slot — b2 owns it
            assert get_active_broadcast_bridge() is b2
        finally:
            b2.stop()
        assert get_active_broadcast_bridge() is None


# ---------------------------------------------------------------------------
# get_status shape (TUI / HTTP consumers depend on this)
# ---------------------------------------------------------------------------


class TestGetStatusFields:
    def test_status_contains_required_fields(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            status = b.get_status()
            for key in (
                "running", "destination_hash", "display_name", "channels",
                "announce_interval_sec", "autosubscribe", "started_at_iso",
                "subscribers", "subscriber_count", "stats",
            ):
                assert key in status, f"missing key: {key}"
            assert status["started_at_iso"] is not None
            assert isinstance(status["subscribers"], list)
        finally:
            b.stop()

    def test_status_subscribers_are_row_dicts(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            status = b.get_status()
            assert status["subscriber_count"] == 1
            row = status["subscribers"][0]
            assert row["lxmf_hash"] == "deadbeef00112233"
            for k in ("added_at", "tier", "state", "consecutive_failures"):
                assert k in row
        finally:
            b.stop()

    def test_status_when_stopped(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf)
        status = b.get_status()
        assert status["running"] is False
        assert status["started_at_iso"] is None


# ---------------------------------------------------------------------------
# Issue #66 first-caller wiring
# ---------------------------------------------------------------------------


class TestIssue66Wiring:
    """Channel-broadcast ACK fallback pattern: when ack_required is True
    and the substrate is reachable, the bridge mints ONE per-broadcast
    msg_id and registers ONE pending-ack record per fan-out (not per
    subscriber) so first-wins semantics work.
    """

    def test_no_ack_when_disabled(self, tmp_path, fake_rns_lxmf):
        """ack_required=False → no register_pending_ack call ever."""
        queue = MagicMock()
        b = _make_bridge(
            tmp_path, fake_rns_lxmf, channels=[0],
            ack_required=False, persistent_queue=queue,
            ack_emit_callback=MagicMock(),
        )
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            b.on_meshtastic_message(_make_bridged(text="ping", channel=0))
            queue.register_pending_ack.assert_not_called()
        finally:
            b.stop()

    def test_ack_uses_channel_placeholder_when_no_sender(
        self, tmp_path, fake_rns_lxmf,
    ):
        """When the message has no source identity, origin_address falls
        back to the placeholder 'channel:<idx>' so the substrate emits
        the synth ACK back on the originating channel."""
        queue = MagicMock()
        queue.register_pending_ack = MagicMock(return_value=True)
        b = _make_bridge(
            tmp_path, fake_rns_lxmf, channels=[2],
            ack_required=True, persistent_queue=queue,
            ack_emit_callback=MagicMock(),
        )
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            # Sender deliberately empty — mirrors the channel-broadcast
            # case where MeshtasticHandler can't surface a source_id.
            msg = _make_bridged(text="ping", channel=2, sender="")
            b.on_meshtastic_message(msg)
            queue.register_pending_ack.assert_called_once()
            kwargs = queue.register_pending_ack.call_args.kwargs
            assert kwargs["origin_network"] == "meshtastic"
            assert kwargs["origin_address"] == "channel:2"
            assert kwargs["allow_orphan"] is True
            assert kwargs["timeout_seconds"] == 300
            # msg_id format is "mbcast-<ts>-ch<idx>"
            args = queue.register_pending_ack.call_args.args
            assert args[0].startswith("mbcast-")
            assert args[0].endswith("-ch2")
        finally:
            b.stop()

    def test_ack_uses_sender_when_available(self, tmp_path, fake_rns_lxmf):
        """When the message carries a sender identity, origin_address
        routes the synth ACK back to that specific node."""
        queue = MagicMock()
        queue.register_pending_ack = MagicMock(return_value=True)
        b = _make_bridge(
            tmp_path, fake_rns_lxmf, channels=[0],
            ack_required=True, persistent_queue=queue,
            ack_emit_callback=MagicMock(),
        )
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(text="ping", channel=0, sender="!abcd1234")
            b.on_meshtastic_message(msg)
            queue.register_pending_ack.assert_called_once()
            kwargs = queue.register_pending_ack.call_args.kwargs
            assert kwargs["origin_address"] == "!abcd1234"
        finally:
            b.stop()

    def test_ack_skipped_for_synth_ack_messages(
        self, tmp_path, fake_rns_lxmf,
    ):
        """Recursion guard: a CanonicalMessage already tagged as a synth
        ACK must NOT register a new pending-ack record (otherwise
        ACK→ACK loops are trivially trivial to construct)."""
        queue = MagicMock()
        b = _make_bridge(
            tmp_path, fake_rns_lxmf, channels=[0],
            ack_required=True, persistent_queue=queue,
            ack_emit_callback=MagicMock(),
        )
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            msg = _make_bridged(
                text="[delivered:mbcast-foo]", channel=0,
                extra_meta={"meshforge_is_synth_ack": True},
            )
            b.on_meshtastic_message(msg)
            queue.register_pending_ack.assert_not_called()
        finally:
            b.stop()

    def test_ack_callback_wired_on_lxmessage(self, tmp_path, fake_rns_lxmf):
        """When ack_msg_id is set, the per-subscriber LXMessage gets a
        delivery callback installed so first-wins can fire."""
        queue = MagicMock()
        queue.register_pending_ack = MagicMock(return_value=True)
        emit_cb = MagicMock()
        b = _make_bridge(
            tmp_path, fake_rns_lxmf, channels=[0],
            ack_required=True, persistent_queue=queue,
            ack_emit_callback=emit_cb,
        )
        b.start()
        try:
            b._subs.add("deadbeef00112233")
            b.on_meshtastic_message(_make_bridged(text="ping", channel=0))
            _rns, lxmf, _router = fake_rns_lxmf
            # The LXMessage instance returned by the constructor is what
            # gets register_delivery_callback called on it.
            lxm_instance = lxmf.LXMessage.return_value
            lxm_instance.register_delivery_callback.assert_called_once()
            lxm_instance.register_failed_callback.assert_called_once()
        finally:
            b.stop()


# ---------------------------------------------------------------------------
# finding-2 (2026-07-23): honest delivery confirmation.
#
# The pre-fix bridge called mark_delivered() right after handle_outbound()
# ENQUEUED the LXM — conflating "handed to the router" with "recipient got
# it." That reset state to HEALTHY + stamped last_delivery on every send, so
# the STALE/DEAD tiers were unreachable and a dead subscriber read HEALTHY
# forever. The fix separates enqueue (best-effort) from a real LXMF recipient
# proof (state DELIVERED), mirroring #74's confirmable-population framing.
# ---------------------------------------------------------------------------


def _make_sub(**kw) -> Subscriber:
    base = dict(lxmf_hash="deadbeef00112233", added_at=datetime(2026, 1, 1))
    base.update(kw)
    return Subscriber(**base)


class _FakeReceipt:
    """Stand-in for the LXMessage handed to a delivery/failed callback."""
    def __init__(self, state):
        self.state = state


def _grab_delivery_cb(fake_rns_lxmf):
    _rns, lxmf, _router = fake_rns_lxmf
    return lxmf.LXMessage.return_value.register_delivery_callback.call_args.args[0]


def _grab_failed_cb(fake_rns_lxmf):
    _rns, lxmf, _router = fake_rns_lxmf
    return lxmf.LXMessage.return_value.register_failed_callback.call_args.args[0]


class TestDeliveryConfirmationDesiredState:
    """desired_state() confirmable-population rule."""

    def test_never_confirmed_is_unconfirmed_not_healthy(self):
        # The core lie the fix kills: no proof yet must NOT read HEALTHY.
        sub = _make_sub(last_confirmed_at=None, consecutive_failures=0)
        assert SubscriberStore.desired_state(sub, datetime(2026, 1, 2)) == STATE_UNCONFIRMED

    def test_never_confirmed_but_failing_is_degraded_not_dead(self):
        # Absence of a proof must never by itself read as DEAD — active
        # failures surface as the honest DEGRADED, not the "went dark" DEAD.
        sub = _make_sub(
            last_confirmed_at=None, consecutive_failures=5,
            last_failure_at=datetime(2026, 6, 1),
        )
        # Even 30 days later, a never-confirmed subscriber never goes DEAD.
        assert SubscriberStore.desired_state(sub, datetime(2026, 7, 1)) == STATE_DEGRADED

    def test_confirmed_no_failures_is_healthy(self):
        sub = _make_sub(last_confirmed_at=datetime(2026, 6, 1), consecutive_failures=0)
        assert SubscriberStore.desired_state(sub, datetime(2026, 6, 1)) == STATE_HEALTHY

    def test_confirmed_then_failing_reaches_stale(self):
        # Now reachable: last_confirmed_at is an honest anchor.
        now = datetime(2026, 6, 2)
        sub = _make_sub(
            last_confirmed_at=now - timedelta(seconds=_STALE_SINCE_SUCCESS_SEC + 60),
            consecutive_failures=4,
        )
        assert SubscriberStore.desired_state(sub, now) == STATE_STALE

    def test_confirmed_then_failing_reaches_dead(self):
        now = datetime(2026, 6, 10)
        sub = _make_sub(
            last_confirmed_at=now - timedelta(seconds=_DEAD_SINCE_SUCCESS_SEC + 60),
            consecutive_failures=9,
        )
        assert SubscriberStore.desired_state(sub, now) == STATE_DEAD


class TestEnqueueIsNotDelivery:
    """mark_fanout_enqueued must not launder into a delivery confirmation."""

    def test_enqueue_does_not_reset_failures_or_reach_healthy(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        for _ in range(3):
            store.mark_failed("deadbeef00112233", "boom")
        sub = store.list_all()[0]
        assert sub.consecutive_failures == 3
        assert sub.state == STATE_DEGRADED
        # An enqueue after failures must NOT paper over them (the whole defect).
        store.mark_fanout_enqueued("deadbeef00112233")
        sub = store.list_all()[0]
        assert sub.consecutive_failures == 3
        assert sub.last_confirmed_at is None
        # Live-derived state still reflects the failures.
        assert SubscriberStore.desired_state(sub, datetime.utcnow()) == STATE_DEGRADED

    def test_confirmation_after_failures_recovers(self, tmp_path):
        store = SubscriberStore(tmp_path / "subs.db")
        store.add("deadbeef00112233")
        for _ in range(3):
            store.mark_failed("deadbeef00112233", "boom")
        # A REAL proof is the only thing that recovers.
        store.mark_confirmed("deadbeef00112233")
        sub = store.list_all()[0]
        assert sub.consecutive_failures == 0
        assert sub.state == STATE_HEALTHY
        assert sub.last_confirmed_at is not None


class TestDeliveryCallbackDrivesHealth:
    """The registered on_delivered/on_failed callbacks drive subscriber health,
    reading lxm.state to tell a recipient proof from a prop-node hand-off."""

    def test_fanout_does_not_confirm_synchronously(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            b.on_meshtastic_message(_make_bridged(channel=0, text="ping"))
            sub = b._subs.list_all()[0]
            # Enqueued, but NO recipient proof yet → unconfirmed, not healthy.
            assert sub.last_confirmed_at is None
            assert SubscriberStore.desired_state(sub, datetime.utcnow()) == STATE_UNCONFIRMED
        finally:
            b.stop()

    def test_delivered_state_confirms(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            b.on_meshtastic_message(_make_bridged(channel=0, text="ping"))
            on_delivered = _grab_delivery_cb(fake_rns_lxmf)
            on_delivered(_FakeReceipt(state=8))  # LXMessage.DELIVERED
            sub = b._subs.list_all()[0]
            assert sub.last_confirmed_at is not None
            assert sub.state == STATE_HEALTHY
        finally:
            b.stop()

    def test_propagated_state_does_not_confirm(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            b.on_meshtastic_message(_make_bridged(channel=0, text="ping"))
            on_delivered = _grab_delivery_cb(fake_rns_lxmf)
            on_delivered(_FakeReceipt(state=4))  # LXMessage.SENT (propagated)
            sub = b._subs.list_all()[0]
            # Reached the propagation node only — NOT a recipient proof.
            assert sub.last_confirmed_at is None
            assert SubscriberStore.desired_state(sub, datetime.utcnow()) == STATE_UNCONFIRMED
        finally:
            b.stop()

    def test_failed_callback_marks_failed(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            b.on_meshtastic_message(_make_bridged(channel=0, text="ping"))
            on_failed = _grab_failed_cb(fake_rns_lxmf)
            on_failed(_FakeReceipt(state=255))  # LXMessage.FAILED
            sub = b._subs.list_all()[0]
            assert sub.consecutive_failures == 1
        finally:
            b.stop()


class TestGetStatusDeliveryConfirmation:
    """get_status surfaces the unconfirmed population as a visible blind spot
    and reports LIVE state so a stale stored value can't lie (#74 / hfm #5)."""

    def test_surfaces_unconfirmed_blind_spot(self, tmp_path, fake_rns_lxmf):
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            b._subs.add("aaaa000000000002")
            b._subs.mark_confirmed("aaaa000000000001")  # one real proof
            status = b.get_status()
            dc = status["delivery_confirmation"]
            assert dc["confirmable_subscribers"] == 1
            assert dc["unconfirmed_subscribers"] == 1
            assert dc["by_state"].get(STATE_HEALTHY) == 1
            assert dc["by_state"].get(STATE_UNCONFIRMED) == 1
        finally:
            b.stop()

    def test_pre_fix_row_reads_unconfirmed_live(self, tmp_path, fake_rns_lxmf):
        # Simulate a migrated pre-fix row: stored state 'healthy', last_delivery
        # set (manufactured on enqueue), but no confirmable anchor. The status
        # surface must report the honest UNCONFIRMED, not the stale 'healthy'.
        b = _make_bridge(tmp_path, fake_rns_lxmf, channels=[0])
        b.start()
        try:
            store = b._subs
            store.add("aaaa000000000001")
            store.mark_fanout_enqueued("aaaa000000000001")  # stamps last_delivery
            from utils.db_helpers import connect_tuned
            with store._lock, connect_tuned(store._db_path) as conn:
                conn.execute(
                    "UPDATE subscribers SET state = ? WHERE lxmf_hash = ?",
                    (STATE_HEALTHY, "aaaa000000000001"),
                )
                conn.commit()
            status = b.get_status()
            sub_row = status["subscribers"][0]
            assert sub_row["last_delivery"] is not None
            assert sub_row["last_confirmed_at"] is None
            assert sub_row["state"] == STATE_UNCONFIRMED
        finally:
            b.stop()


class TestMsgContentBytesNormalizationPri12:
    """Pri-12 (gateway review 2026-07-23): _msg_content must normalize a bytes
    `content` (the advertised CanonicalMessage second shape) to str, or bytes
    flow into str ops → TypeError swallowed → the message silently fails to fan
    out."""

    def test_bytes_content_decoded(self):
        from gateway.meshtastic_broadcast_bridge import _msg_content

        class M:
            content = b"subscribe"
        assert _msg_content(M()) == "subscribe"

    def test_str_and_none_unchanged(self):
        from gateway.meshtastic_broadcast_bridge import _msg_content

        class S:
            content = "already str"

        class N:
            content = None
        assert _msg_content(S()) == "already str"
        assert _msg_content(N()) == ""
