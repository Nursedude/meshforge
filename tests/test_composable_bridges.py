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


class TestStatusDialects20260803:
    """A bridge's status must be read in ITS OWN dialect.

    Measured on moc 2026-08-03: the gateway printed

        [Meshtastic Preset Bridge (cross-preset)]
          Meshtastic: disconnected   RNS: n/a
          Messages bridged: 0 (M->R: 0, R->M: 0)

    while the journal showed both legs connected and real traffic crossing
    ("Bridged SHORT_TURBO -> primary ... Kilauea Volcano ADVISORY YELLOW").

    Cause: the printers read RNSMeshtasticBridge's vocabulary
    (meshtastic_connected / messages_mesh_to_rns) against MeshBridge's status
    dict (primary{connected} / messages_primary_to_secondary). Every missing
    key fell through to a default that LOOKS like a reading — absent became
    False became "disconnected", absent counters became 0 — so absence was
    rendered as a fault claim (honest_failure_modes #1).

    Note the asymmetry that made it survive: the RNS line already had an
    absence branch ("n/a"); the Meshtastic line did not.
    """

    MESH_STATUS = {
        "running": True,
        "enabled": True,
        "primary": {"connected": True, "preset": "LONG_FAST", "mode": "mqtt"},
        "secondary": {"connected": True, "preset": "SHORT_TURBO", "mode": "tcp"},
        "statistics": {
            "messages_primary_to_secondary": 7,
            "messages_secondary_to_primary": 3,
        },
    }
    RNS_STATUS = {
        "running": True,
        "meshtastic_connected": True,
        "rns_connected": True,
        "statistics": {"messages_mesh_to_rns": 5, "messages_rns_to_mesh": 2},
    }

    def _render(self, statuses, capsys):
        from gateway.bridge_cli import print_multi_status

        insts = []
        for i, st in enumerate(statuses):
            m = MagicMock()
            m.get_status.return_value = st
            m._bridge_label = f"bridge{i}"
            insts.append(m)
        print_multi_status(insts)
        return capsys.readouterr().out

    # --- the reported symptom -------------------------------------------

    def test_connected_mesh_bridge_is_never_called_disconnected(self, capsys):
        out = self._render([self.MESH_STATUS, self.RNS_STATUS], capsys)
        mesh_block = out.split("bridge1")[0]
        assert "disconnected" not in mesh_block, (
            "a fully connected cross-preset bridge was reported disconnected:\n" + mesh_block
        )

    def test_mesh_bridge_shows_both_preset_legs(self, capsys):
        out = self._render([self.MESH_STATUS, self.RNS_STATUS], capsys)
        assert "LONG_FAST" in out and "SHORT_TURBO" in out

    def test_mesh_bridge_counters_are_visible(self, capsys):
        """10 bridged messages must not render as 0."""
        out = self._render([self.MESH_STATUS, self.RNS_STATUS], capsys)
        mesh_block = out.split("bridge1")[0]
        assert "7" in mesh_block and "3" in mesh_block, (
            "mesh<->mesh counters invisible; display read the RNS key names:\n" + mesh_block
        )
        assert "M->R: 0" not in mesh_block

    def test_disconnected_leg_still_reads_disconnected(self, capsys):
        """The fix must not make everything look healthy — a real down leg shows."""
        down = dict(self.MESH_STATUS)
        down["secondary"] = {"connected": False, "preset": "SHORT_TURBO"}
        out = self._render([down, self.RNS_STATUS], capsys)
        assert "disconnected" in out.split("bridge1")[0]

    # --- absence is never a definitive claim -----------------------------

    def test_unrecognized_shape_is_not_claimed_disconnected(self, capsys):
        """A future bridge type must report unknown, not manufacture a fault."""
        out = self._render([{"running": True, "something_else": 1}, self.RNS_STATUS], capsys)
        block = out.split("bridge1")[0]
        assert "disconnected" not in block, "absence rendered as a fault claim:\n" + block
        assert "unknown" in block.lower()

    # --- the legacy single-bridge format must not regress ----------------

    def test_rns_dialect_rendering_unchanged(self, capsys):
        from gateway.bridge_cli import print_status

        print_status(self.RNS_STATUS)
        out = capsys.readouterr().out
        assert "Meshtastic: connected" in out
        assert "RNS: connected" in out
        assert "Messages bridged: 7 (M->R: 5, R->M: 2)" in out

    def test_single_mesh_bridge_also_reads_its_own_dialect(self, capsys):
        """print_multi_status delegates to print_status when only one bridge
        runs — a pure cross-preset box must not hit the same lie."""
        out = self._render([self.MESH_STATUS], capsys)
        assert "disconnected" not in out, out

    # --- readiness gate reads the same dialects --------------------------

    def test_bridge_is_ready_recognizes_the_mesh_dialect(self):
        from gateway.bridge_cli import _bridge_is_ready

        inst = MagicMock()
        inst.get_status.return_value = self.MESH_STATUS
        assert _bridge_is_ready(inst) is True, (
            "mesh_bridge could never be 'ready': meshtastic_connected is absent, "
            "so bool(None) was False forever"
        )

    def test_bridge_is_ready_mesh_partial_is_not_ready(self):
        from gateway.bridge_cli import _bridge_is_ready

        half = dict(self.MESH_STATUS)
        half["secondary"] = {"connected": False, "preset": "SHORT_TURBO"}
        inst = MagicMock()
        inst.get_status.return_value = half
        assert _bridge_is_ready(inst) is False

    def test_bridge_is_ready_rns_dialect_unchanged(self):
        from gateway.bridge_cli import _bridge_is_ready

        inst = MagicMock()
        inst.get_status.return_value = self.RNS_STATUS
        assert _bridge_is_ready(inst) is True
        inst.get_status.return_value = dict(self.RNS_STATUS, rns_connected=False)
        assert _bridge_is_ready(inst) is False
