"""Tests for gateway._channel_resolver (Issue #42)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway._channel_resolver import (
    ChannelResolutionError,
    apply_resolved_channel,
    resolve_tx_channel_index,
)


# ---------- resolve_tx_channel_index ----------


def test_empty_name_returns_no_name():
    idx, status = resolve_tx_channel_index("", 0)
    assert idx == 0
    assert status == "no_name"


@patch("gateway._channel_resolver._query_channels")
def test_query_fails_returns_unreachable(mock_q):
    mock_q.return_value = None
    idx, status = resolve_tx_channel_index("meshforge", 0)
    assert idx == 0
    assert status == "unreachable"


@patch("gateway._channel_resolver._query_channels")
def test_name_not_found_returns_not_found(mock_q):
    mock_q.return_value = [
        {"index": 0, "name": ""},
        {"index": 1, "name": "RNS Volcano"},
    ]
    idx, status = resolve_tx_channel_index("meshforge", 0)
    assert idx == 0
    assert status == "not_found"


@patch("gateway._channel_resolver._query_channels")
def test_name_resolves_to_matching_index(mock_q):
    mock_q.return_value = [
        {"index": 0, "name": ""},
        {"index": 1, "name": "RNS Volcano"},
        {"index": 2, "name": "meshforge"},
    ]
    idx, status = resolve_tx_channel_index("meshforge", 2)
    assert idx == 2
    assert status == "matches_config"


@patch("gateway._channel_resolver._query_channels")
def test_name_resolves_to_different_index(mock_q):
    """The fleet-host-3 scenario: config says 0, radio says meshforge is idx 2."""
    mock_q.return_value = [
        {"index": 0, "name": ""},
        {"index": 1, "name": "RNS Volcano"},
        {"index": 2, "name": "meshforge"},
    ]
    idx, status = resolve_tx_channel_index("meshforge", 0)
    assert idx == 2
    assert status == "resolved"


# ---------- apply_resolved_channel ----------


def _make_config(bridge_name: str = "meshforge", mt_channel: int = 0):
    return SimpleNamespace(
        mqtt_bridge=SimpleNamespace(channel=bridge_name),
        meshtastic=SimpleNamespace(channel=mt_channel),
    )


@patch("gateway._channel_resolver._query_channels")
def test_apply_mutates_config_on_mismatch(mock_q):
    mock_q.return_value = [
        {"index": 0, "name": ""},
        {"index": 2, "name": "meshforge"},
    ]
    cfg = _make_config("meshforge", 0)
    apply_resolved_channel(cfg)
    assert cfg.meshtastic.channel == 2


@patch("gateway._channel_resolver._query_channels")
def test_apply_leaves_config_when_matches(mock_q):
    mock_q.return_value = [
        {"index": 0, "name": ""},
        {"index": 2, "name": "meshforge"},
    ]
    cfg = _make_config("meshforge", 2)
    apply_resolved_channel(cfg)
    assert cfg.meshtastic.channel == 2


@patch("gateway._channel_resolver._query_channels")
def test_apply_raises_on_unreachable_when_name_set(mock_q):
    """Refuse-loud: an unverifiable bridge name must NOT silently fall
    through to a stale index — that's how RNS→Mesh leaks onto the wrong
    channel (e.g. Regional on fleet-host)."""
    mock_q.return_value = None
    cfg = _make_config("meshforge", 0)
    with pytest.raises(ChannelResolutionError) as excinfo:
        apply_resolved_channel(cfg)
    assert "meshforge" in str(excinfo.value)
    assert "configure_gateway.sh" in str(excinfo.value) or "meshtasticd" in str(excinfo.value)


@patch("gateway._channel_resolver._query_channels")
def test_apply_raises_on_not_found_when_name_set(mock_q):
    """Bridge name configured but missing from the radio's channel list."""
    mock_q.return_value = [
        {"index": 0, "name": "LongFast"},
        {"index": 1, "name": "Regional"},
    ]
    cfg = _make_config("meshforge", 0)
    with pytest.raises(ChannelResolutionError) as excinfo:
        apply_resolved_channel(cfg)
    assert "meshforge" in str(excinfo.value)
    assert "not present" in str(excinfo.value) or "not found" in str(excinfo.value).lower()


@patch("gateway._channel_resolver._query_channels")
def test_apply_skips_when_bridge_name_empty(mock_q):
    """No bridge name configured = legitimate 'no bridge channel' case;
    keep silent fallback for back-compat."""
    cfg = _make_config("", 0)
    apply_resolved_channel(cfg)
    assert cfg.meshtastic.channel == 0
    mock_q.assert_not_called()


# ---------- Hardening A: bridge channel deployment diagnostic ----------
#
# The decision logic for "should we emit a one-shot stall warning?" is
# extracted as a static helper on RNSMeshtasticBridge so it's testable
# without spinning up the full bridge fixture or wrestling with the
# diagnostic thread's stop_event/sleep cadence.


def test_stall_warning_silent_when_uplink_seen():
    """Any timestamp on _last_uplink_at means at least one fleet client
    is on the bridge channel — silence the diagnostic."""
    from gateway.rns_bridge import RNSMeshtasticBridge
    assert RNSMeshtasticBridge._should_emit_channel_stall_warning(
        last_uplink_at=1234567890.0,
        already_emitted=False,
        elapsed_sec=3600,
        threshold_sec=1800,
    ) is False


def test_stall_warning_silent_below_threshold():
    """Mid-rollout — operators are still provisioning fleet devices.
    Don't spam the journal until past the configured grace window."""
    from gateway.rns_bridge import RNSMeshtasticBridge
    assert RNSMeshtasticBridge._should_emit_channel_stall_warning(
        last_uplink_at=None,
        already_emitted=False,
        elapsed_sec=600,    # 10 min
        threshold_sec=1800, # 30 min
    ) is False


def test_stall_warning_fires_past_threshold_with_no_uplink():
    """The moc3 stall shape: bridge running for hours, zero uplinks
    on the configured channel, no surface error. This is the moment
    we want a journal WARNING."""
    from gateway.rns_bridge import RNSMeshtasticBridge
    assert RNSMeshtasticBridge._should_emit_channel_stall_warning(
        last_uplink_at=None,
        already_emitted=False,
        elapsed_sec=1800,
        threshold_sec=1800,
    ) is True


def test_stall_warning_only_fires_once():
    """One-shot per bridge run. Repeated WARNs every 30s would just
    paper over the journal and de-train operators from caring."""
    from gateway.rns_bridge import RNSMeshtasticBridge
    assert RNSMeshtasticBridge._should_emit_channel_stall_warning(
        last_uplink_at=None,
        already_emitted=True,
        elapsed_sec=7200,  # well past threshold
        threshold_sec=1800,
    ) is False


def test_handler_records_uplink_timestamp_on_json_message():
    """Hardening A: any well-formed JSON arrival proves a fleet client
    is publishing on the bridge channel. The timestamp must update on
    EVERY JSON arrival (not just text), since nodeinfo/telemetry/
    position broadcasts all prove channel deployment."""
    from gateway.mqtt_bridge_handler import MQTTBridgeHandler
    h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
    h._last_uplink_at = None
    h._stale_warning_emitted = False
    h._recent_ids = {}
    h._dedup_window = 60
    h._mqtt_lock = __import__("threading").Lock()
    # _update_node_from_mqtt and the dispatch helpers are out-of-scope
    # for the timestamp test; stub them so _handle_json_message reaches
    # the timestamp update without exploding.
    h._update_node_from_mqtt = lambda data: None
    h._update_telemetry = lambda data: None
    h._update_position = lambda data: None
    h._update_nodeinfo = lambda data: None
    h._bridge_text_message = lambda data, topic: None

    payload = b'{"type":"nodeinfo","from":1234,"id":"x1","sender":"!aabb"}'
    h._handle_json_message("msh/US/2/json/meshforge/!aabb", payload)

    assert h._last_uplink_at is not None
    assert h._last_uplink_at > 0


def test_handler_timestamp_persists_across_message_types():
    """Successive uplinks bump the timestamp; the diagnostic uses the
    most recent observation, not just the first."""
    import time
    from gateway.mqtt_bridge_handler import MQTTBridgeHandler
    h = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
    h._last_uplink_at = None
    h._stale_warning_emitted = False
    h._recent_ids = {}
    h._dedup_window = 60
    h._mqtt_lock = __import__("threading").Lock()
    h._update_node_from_mqtt = lambda data: None
    h._update_telemetry = lambda data: None
    h._update_position = lambda data: None
    h._update_nodeinfo = lambda data: None
    h._bridge_text_message = lambda data, topic: None

    h._handle_json_message(
        "msh/US/2/json/meshforge/!a",
        b'{"type":"telemetry","from":1,"id":"a"}',
    )
    first = h._last_uplink_at
    time.sleep(0.01)
    h._handle_json_message(
        "msh/US/2/json/meshforge/!b",
        b'{"type":"position","from":2,"id":"b"}',
    )
    second = h._last_uplink_at

    assert second > first
