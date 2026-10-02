"""
The removed RNS-over-Meshtastic transport, as config: read once, never re-written.

The transport (gateway/rns_transport.py) was removed 2026-10-01. Until
2026-10-02 its config dataclass stayed, and ``GatewayConfig.save`` re-serialised
all 11 of its keys into every gateway.json on every save, so the removed
feature kept regenerating in every user's file and in two shipped templates
(an outside review, rev 4.9, measured it). These tests pin the END state on a
real file round trip, not the dataclass: an old file loads, an old ``enabled:
true`` is still refused at startup with the reason, and the next save drops the
section.

Run: python3 -m pytest tests/test_gateway_transport.py -v
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.gateway.config import GatewayConfig, RNS_TRANSPORT_REMOVED

OLD_SECTION = {
    "enabled": False, "connection_type": "tcp", "device_path": "localhost:4403",
    "data_speed": 8, "hop_limit": 3, "fragment_timeout_sec": 30,
    "max_pending_fragments": 100, "enable_stats": True, "stats_interval_sec": 60,
    "packet_loss_threshold": 0.1, "latency_threshold_ms": 5000,
}


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "gateway.json"
    with patch.object(GatewayConfig, "get_config_path", classmethod(lambda cls: path)):
        yield path


def _write(path, **extra):
    data = {"enabled": True, "bridge_mode": "mqtt_bridge", **extra}
    path.write_text(json.dumps(data))


def test_old_file_with_the_section_still_loads(cfg_file):
    _write(cfg_file, rns_transport=OLD_SECTION)
    cfg = GatewayConfig.load()
    assert cfg.load_error is None
    assert cfg.rns_transport_legacy_enabled is False


def test_save_drops_the_dead_section(cfg_file):
    """The defect: save() wrote the block back forever."""
    _write(cfg_file, rns_transport=OLD_SECTION)
    cfg = GatewayConfig.load()
    assert cfg.save() is True
    assert "rns_transport" not in json.loads(cfg_file.read_text())


def test_fresh_save_never_writes_the_section(cfg_file):
    assert GatewayConfig().save() is True
    assert "rns_transport" not in json.loads(cfg_file.read_text())


def test_enabled_true_is_still_refused_at_startup(cfg_file):
    from src.gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts
    _write(cfg_file, rns_transport=dict(OLD_SECTION, enabled=True))
    cfg = GatewayConfig.load()
    assert cfg.rns_transport_legacy_enabled is True
    assert RNS_TRANSPORT_REMOVED in validate_bridge_conflicts(cfg, resolve_bridges(cfg))


def test_null_section_is_off(cfg_file):
    _write(cfg_file, rns_transport=None)
    assert GatewayConfig.load().rns_transport_legacy_enabled is False


@pytest.mark.parametrize("bad", [[], "yes", 1, "false", {"enabled": "false"}])
def test_anything_but_explicit_false_fails_closed(cfg_file, bad):
    """bool("false") is True; a non-dict section used to be a load error.
    Neither may quietly read as OFF and start a gateway the file asked
    otherwise of (review B3)."""
    _write(cfg_file, rns_transport=bad)
    cfg = GatewayConfig.load()
    assert cfg.load_error is None
    assert cfg.rns_transport_legacy_enabled is True


def test_an_enabled_section_survives_an_unrelated_save(cfg_file):
    """An unrelated save (TUI meshcore toggle) must not lift the refusal
    the operator has not seen yet (review B2)."""
    from src.gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts
    _write(cfg_file, rns_transport=dict(OLD_SECTION, enabled=True))
    assert GatewayConfig.load().save() is True
    assert json.loads(cfg_file.read_text())["rns_transport"] == {"enabled": True}
    cfg = GatewayConfig.load()
    assert RNS_TRANSPORT_REMOVED in validate_bridge_conflicts(cfg, resolve_bridges(cfg))


def test_no_shipped_template_carries_the_section():
    root = Path(__file__).resolve().parent.parent
    shipped = list((root / "src" / "gateway" / "templates").glob("*.json"))
    shipped += [root / "templates" / "gateway" / "gateway.json.template"]
    assert shipped
    for p in shipped:
        assert '"rns_transport"' not in p.read_text(), p
