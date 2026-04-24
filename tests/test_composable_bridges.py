"""Tests for the composable-bridges refactor in gateway/bridge_cli.py.

Covers:
  - resolve_bridges() picks bridges based on per-section .enabled flags
  - validate_bridge_conflicts() refuses inconsistent configs (no silent
    fallback — gateway must exit cleanly rather than starting broken)
  - migrate_legacy_bridge_mode() auto-enables sections for existing
    deployments that used bridge_mode as a gate
"""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fresh_config():
    from gateway.config import GatewayConfig
    return GatewayConfig()


class TestResolveBridges:
    def test_default_config_runs_rns_bridge_only(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges

        bridges = resolve_bridges(fresh_config)
        names = [b["name"] for b in bridges]
        assert names == ["rns_bridge"]

    def test_mesh_bridge_enabled_adds_mesh_bridge(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges

        fresh_config.mesh_bridge.enabled = True
        bridges = resolve_bridges(fresh_config)
        names = [b["name"] for b in bridges]
        assert "rns_bridge" in names
        assert "mesh_bridge" in names

    def test_rns_bridge_can_be_disabled_explicitly(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges

        fresh_config.rns_bridge_enabled = False
        fresh_config.mesh_bridge.enabled = True
        bridges = resolve_bridges(fresh_config)
        names = [b["name"] for b in bridges]
        assert names == ["mesh_bridge"]

    def test_all_disabled_returns_empty(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges

        fresh_config.rns_bridge_enabled = False
        bridges = resolve_bridges(fresh_config)
        assert bridges == []


class TestValidateBridgeConflicts:
    def test_default_config_has_no_conflicts(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        bridges = resolve_bridges(fresh_config)
        assert validate_bridge_conflicts(fresh_config, bridges) == []

    def test_all_bridges_disabled_is_a_conflict(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        fresh_config.rns_bridge_enabled = False
        bridges = resolve_bridges(fresh_config)
        errs = validate_bridge_conflicts(fresh_config, bridges)
        assert any("No bridges enabled" in e for e in errs)

    def test_mesh_bridge_primary_and_secondary_same_serial_device(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        fresh_config.mesh_bridge.enabled = True
        fresh_config.mesh_bridge.primary.connection_type = "serial"
        fresh_config.mesh_bridge.primary.serial_device = "/dev/ttyUSB0"
        fresh_config.mesh_bridge.secondary.connection_type = "serial"
        fresh_config.mesh_bridge.secondary.serial_device = "/dev/ttyUSB0"
        bridges = resolve_bridges(fresh_config)
        errs = validate_bridge_conflicts(fresh_config, bridges)
        assert any("serial_device=/dev/ttyUSB0" in e for e in errs)

    def test_mesh_bridge_and_rns_transport_both_enabled_conflict(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        fresh_config.mesh_bridge.enabled = True
        fresh_config.rns_transport.enabled = True
        bridges = resolve_bridges(fresh_config)
        errs = validate_bridge_conflicts(fresh_config, bridges)
        assert any("both claim the Meshtastic radio" in e for e in errs)

    def test_missing_secondary_serial_device_is_a_conflict(self, fresh_config):
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        fresh_config.mesh_bridge.enabled = True
        fresh_config.mesh_bridge.secondary.connection_type = "serial"
        fresh_config.mesh_bridge.secondary.serial_device = "/dev/does-not-exist-xyz"
        bridges = resolve_bridges(fresh_config)
        errs = validate_bridge_conflicts(fresh_config, bridges)
        assert any("does not exist" in e for e in errs)

    def test_present_secondary_serial_device_passes(self, fresh_config, tmp_path):
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        fake_dev = tmp_path / "ttyUSB0"
        fake_dev.write_text("")
        fresh_config.mesh_bridge.enabled = True
        fresh_config.mesh_bridge.secondary.connection_type = "serial"
        fresh_config.mesh_bridge.secondary.serial_device = str(fake_dev)
        bridges = resolve_bridges(fresh_config)
        errs = validate_bridge_conflicts(fresh_config, bridges)
        # No conflict messages about device existence
        assert not any("does not exist" in e for e in errs)

    def test_dual_enabled_rns_and_mesh_bridge_no_conflict(self, fresh_config, tmp_path):
        """The whole point of Option 3 — RNS bridge + mesh_bridge can coexist."""
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        fake_dev = tmp_path / "ttyUSB0"
        fake_dev.write_text("")
        fresh_config.rns_bridge_enabled = True  # default, explicit for clarity
        fresh_config.mesh_bridge.enabled = True
        fresh_config.mesh_bridge.secondary.connection_type = "serial"
        fresh_config.mesh_bridge.secondary.serial_device = str(fake_dev)
        bridges = resolve_bridges(fresh_config)
        names = [b["name"] for b in bridges]
        assert names == ["rns_bridge", "mesh_bridge"]
        assert validate_bridge_conflicts(fresh_config, bridges) == []


class TestLegacyBridgeModeMigration:
    def test_legacy_mesh_bridge_mode_auto_enables_section(self, fresh_config):
        from gateway.bridge_cli import migrate_legacy_bridge_mode

        fresh_config.bridge_mode = "mesh_bridge"
        fresh_config.mesh_bridge.enabled = False
        warnings_out = migrate_legacy_bridge_mode(fresh_config)
        assert fresh_config.mesh_bridge.enabled is True
        assert any("auto-enabled" in w for w in warnings_out)

    def test_legacy_rns_transport_mode_auto_enables_section(self, fresh_config):
        from gateway.bridge_cli import migrate_legacy_bridge_mode

        fresh_config.bridge_mode = "rns_transport"
        fresh_config.rns_transport.enabled = False
        warnings_out = migrate_legacy_bridge_mode(fresh_config)
        assert fresh_config.rns_transport.enabled is True
        assert any("auto-enabled" in w for w in warnings_out)

    def test_mqtt_bridge_mode_is_no_op_migration(self, fresh_config):
        """The common fleet config — bridge_mode=mqtt_bridge, rns_bridge_enabled default True.
        Should not mutate anything, should not warn."""
        from gateway.bridge_cli import migrate_legacy_bridge_mode

        fresh_config.bridge_mode = "mqtt_bridge"
        warnings_out = migrate_legacy_bridge_mode(fresh_config)
        assert warnings_out == []

    def test_explicit_mesh_bridge_enabled_no_migration_warning(self, fresh_config):
        """Already-migrated configs don't re-warn."""
        from gateway.bridge_cli import migrate_legacy_bridge_mode

        fresh_config.bridge_mode = "mesh_bridge"
        fresh_config.mesh_bridge.enabled = True
        warnings_out = migrate_legacy_bridge_mode(fresh_config)
        assert warnings_out == []


class TestConfigRoundTrip:
    def test_rns_bridge_enabled_serializes(self, fresh_config, tmp_path, monkeypatch):
        """rns_bridge_enabled survives save→load cycle."""
        from gateway.config import GatewayConfig

        fake_home = tmp_path
        monkeypatch.setattr(
            "gateway.config.get_real_user_home", lambda: fake_home
        )

        fresh_config.rns_bridge_enabled = False
        fresh_config.mesh_bridge.enabled = True
        fresh_config.save()

        loaded = GatewayConfig.load()
        assert loaded.rns_bridge_enabled is False
        assert loaded.mesh_bridge.enabled is True

    def test_legacy_config_without_rns_bridge_enabled_defaults_true(
        self, tmp_path, monkeypatch
    ):
        """A gateway.json from before this refactor (no rns_bridge_enabled key)
        must load with rns_bridge_enabled=True — preserves existing deployment
        behavior exactly."""
        import json

        from gateway.config import GatewayConfig

        fake_home = tmp_path
        monkeypatch.setattr(
            "gateway.config.get_real_user_home", lambda: fake_home
        )

        config_dir = fake_home / ".config" / "meshforge"
        config_dir.mkdir(parents=True)
        legacy_config = {
            "enabled": True,
            "bridge_mode": "mqtt_bridge",
            # intentionally no rns_bridge_enabled key
        }
        (config_dir / "gateway.json").write_text(json.dumps(legacy_config))

        loaded = GatewayConfig.load()
        assert loaded.rns_bridge_enabled is True
