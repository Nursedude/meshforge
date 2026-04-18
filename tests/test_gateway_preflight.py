"""Tests for the Gateway Pre-Flight handler.

Verifies handler registration and individual check functions. Mocks external
services so tests run offline.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Put src/launcher_tui on path so `from handlers import ...` works
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))


def test_handler_registered():
    """GatewayPreflightHandler must be in get_all_handlers()."""
    from handlers import get_all_handlers
    classes = get_all_handlers()
    names = [c.__name__ for c in classes]
    assert "GatewayPreflightHandler" in names


def test_handler_protocol_contract():
    """Handler must have handler_id, menu_section, menu_items, execute."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    assert h.handler_id == "gateway_preflight"
    assert h.menu_section == "mesh_networks"
    items = h.menu_items()
    assert len(items) == 1
    assert items[0][0] == "check"


def test_check_lxmf_present(monkeypatch):
    """LXMF check returns OK when both RNS and LXMF are importable."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    fake_rns = MagicMock(__version__="1.1.4")
    fake_lxmf = MagicMock(__version__="0.9.4")

    def fake_safe_import(name):
        return {"RNS": (fake_rns, True), "LXMF": (fake_lxmf, True)}[name]

    monkeypatch.setattr("handlers.gateway_preflight.safe_import", fake_safe_import)
    status, msg, fix = h._check_lxmf()
    assert "1.1.4" in msg and "0.9.4" in msg
    assert fix is None


def test_check_lxmf_missing(monkeypatch):
    """LXMF check returns FAIL with pip install hint when LXMF missing."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()

    def fake_safe_import(name):
        if name == "RNS":
            return (MagicMock(), True)
        return (None, False)

    monkeypatch.setattr("handlers.gateway_preflight.safe_import", fake_safe_import)
    status, msg, fix = h._check_lxmf()
    assert "lxmf" in msg.lower()
    assert fix is not None and "pip3 install" in fix


def test_channel_uplink_parsing():
    """Channel parser pulls out the right channel name when uplinkEnabled=true."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    fake_info = '''Primary channel URL: ...
Channels:
  Index 0: PRIMARY psk=default { "psk": "AQ==", "name": "", "uplinkEnabled": false, "downlinkEnabled": false }
  Index 2: SECONDARY psk=secret { "psk": "xxx", "name": "meshforge", "uplinkEnabled": true, "downlinkEnabled": true }
'''
    with patch.object(h, "_run_meshtastic_info", return_value=fake_info):
        (status, msg, _fix), uplinked = h._check_channel_uplink()
    assert uplinked == ["meshforge"]
    assert "meshforge" in msg


def test_channel_uplink_none_enabled():
    """FAIL when no channel has both uplink + downlink true."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    fake_info = '''Channels:
  Index 0: PRIMARY psk=default { "name": "", "uplinkEnabled": false, "downlinkEnabled": false }
'''
    with patch.object(h, "_run_meshtastic_info", return_value=fake_info):
        (status, msg, fix), uplinked = h._check_channel_uplink()
    assert uplinked == []
    assert fix is not None and "uplink_enabled" in fix


def test_gateway_config_channel_mismatch(tmp_path, monkeypatch):
    """FAIL when gateway.json mqtt_channel isn't in uplinked list."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    fake_home = tmp_path
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "gateway.json").write_text(json.dumps({
        "mqtt_bridge": {"channel": "LongFast"},
        "meshtastic": {"mqtt_channel": "LongFast"},
    }))
    monkeypatch.setattr(
        "handlers.gateway_preflight.get_real_user_home", lambda: fake_home
    )
    status, msg, fix = h._check_gateway_config_channel(["meshforge"])
    assert "LongFast" in msg and "meshforge" in msg
    assert fix is not None


def test_gateway_config_channel_match(tmp_path, monkeypatch):
    """OK when gateway.json mqtt_channel is among uplinked channels."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    fake_home = tmp_path
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "gateway.json").write_text(json.dumps({
        "mqtt_bridge": {"channel": "meshforge"},
    }))
    monkeypatch.setattr(
        "handlers.gateway_preflight.get_real_user_home", lambda: fake_home
    )
    status, msg, fix = h._check_gateway_config_channel(["meshforge"])
    assert fix is None
    assert "matches" in msg.lower() or "meshforge" in msg


def test_nomadnet_identity_match(tmp_path, monkeypatch):
    """OK when default_lxmf_destination matches NomadNet's most recent hash."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    fake_home = tmp_path
    (fake_home / ".config" / "meshforge").mkdir(parents=True)
    (fake_home / ".config" / "meshforge" / "gateway.json").write_text(json.dumps({
        "rns": {"default_lxmf_destination": "d69f7e802960b39561768588fc6e6082"},
    }))
    (fake_home / ".nomadnetwork").mkdir()
    (fake_home / ".nomadnetwork" / "logfile").write_text(
        "[2026-04-18 08:00] [Notice] LXMF Router ready to receive on: <d69f7e802960b39561768588fc6e6082>\n"
    )
    monkeypatch.setattr(
        "handlers.gateway_preflight.get_real_user_home", lambda: fake_home
    )
    status, msg, fix = h._check_nomadnet_identity_match()
    assert fix is None
    assert "matches" in msg.lower()


def test_nomadnet_identity_mismatch(tmp_path, monkeypatch):
    """FAIL when stored default_lxmf_destination doesn't match NomadNet."""
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    fake_home = tmp_path
    (fake_home / ".config" / "meshforge").mkdir(parents=True)
    (fake_home / ".config" / "meshforge" / "gateway.json").write_text(json.dumps({
        "rns": {"default_lxmf_destination": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    }))
    (fake_home / ".nomadnetwork").mkdir()
    (fake_home / ".nomadnetwork" / "logfile").write_text(
        "LXMF Router ready to receive on: <bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb>\n"
    )
    monkeypatch.setattr(
        "handlers.gateway_preflight.get_real_user_home", lambda: fake_home
    )
    status, msg, fix = h._check_nomadnet_identity_match()
    assert fix is not None and "update" in fix.lower()
    assert "aaaaaaaa" in msg or "bbbbbbbb" in msg
