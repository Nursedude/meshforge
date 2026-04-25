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
