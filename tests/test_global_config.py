"""
Tests for src/utils/global_config.py — the MeshForge ecosystem-wide
``~/.config/meshforge/global.ini`` reader.

Verifies the read-side contract: missing file → empty overrides;
malformed file → empty overrides + DEBUG log; well-formed file →
flat dict keyed to DaemonConfig field names.

The cross-repo contract spec lives at meshing_around_meshforge/docs/global_config.md.
"""

import logging

import pytest

from utils.global_config import (
    _coerce_bool,
    _coerce_float,
    _coerce_int,
    global_config_path,
    load_global_overrides,
)


class TestCoerceHelpers:
    """The local _coerce_* helpers must never raise on garbage input."""

    def test_coerce_int_handles_garbage(self):
        assert _coerce_int("abc", 0) == 0
        assert _coerce_int(None, 1883) == 1883
        assert _coerce_int("", 9999) == 9999
        assert _coerce_int("1883", 0) == 1883
        assert _coerce_int(1883, 0) == 1883

    def test_coerce_float_handles_garbage(self):
        assert _coerce_float("abc") is None
        assert _coerce_float(None) is None
        assert _coerce_float("") is None
        assert _coerce_float("19.7") == pytest.approx(19.7)

    def test_coerce_bool_distinguishes_unset_from_false(self):
        # None / blank → None (unset; caller skips)
        assert _coerce_bool(None) is None
        assert _coerce_bool("") is None
        assert _coerce_bool("   ") is None
        # Truthy strings → True
        assert _coerce_bool("true") is True
        assert _coerce_bool("YES") is True
        assert _coerce_bool("1") is True
        assert _coerce_bool("on") is True
        # Anything else → False (explicit "false" is the common case)
        assert _coerce_bool("false") is False
        assert _coerce_bool("no") is False
        assert _coerce_bool("0") is False


class TestGlobalConfigPath:
    """global_config_path() honors SUDO_USER per MF001."""

    def test_path_ends_with_canonical_filename(self):
        p = global_config_path()
        assert str(p).endswith(".config/meshforge/global.ini")

    def test_path_under_sudo_user_home(self, monkeypatch):
        """Running under sudo must resolve to the invoking user's home."""
        monkeypatch.setenv("SUDO_USER", "wh6gxz")
        # Force a fresh import so the path uses current env
        from importlib import reload

        from utils import global_config as gc_module

        reload(gc_module)
        p = gc_module.global_config_path()
        assert "/home/wh6gxz/" in str(p)
        assert str(p).endswith(".config/meshforge/global.ini")


class TestLoadGlobalOverridesReader:
    """load_global_overrides() — the main entry point."""

    def test_missing_file_returns_empty(self, tmp_path):
        target = tmp_path / "does-not-exist.ini"
        assert load_global_overrides(target) == {}

    def test_malformed_ini_returns_empty_does_not_raise(self, tmp_path, caplog):
        target = tmp_path / "bad.ini"
        target.write_text("this is not [valid INI [\n=== broken ===")
        with caplog.at_level(logging.DEBUG, logger="utils.global_config"):
            result = load_global_overrides(target)
        assert result == {}
        # A DEBUG breadcrumb fires so an operator can debug — but no
        # warning/error escapes (would crash other apps' boots).
        assert any(
            "global.ini parse failed" in rec.message
            for rec in caplog.records
        ), "expected DEBUG log on malformed INI"

    def test_empty_ini_returns_empty(self, tmp_path):
        target = tmp_path / "empty.ini"
        target.write_text("")
        assert load_global_overrides(target) == {}

    def test_no_mqtt_section_returns_empty(self, tmp_path):
        target = tmp_path / "no-mqtt.ini"
        target.write_text("[node]\nshort_name = TREX\n")
        # node/region/paths are documented in the contract but NOC
        # doesn't currently have destinations for them, so the reader
        # emits nothing.
        assert load_global_overrides(target) == {}

    def test_mqtt_broker_only_infers_enabled(self, tmp_path):
        target = tmp_path / "broker-only.ini"
        target.write_text("[mqtt]\nbroker = mqtt.local\n")
        result = load_global_overrides(target)
        assert result == {
            "mqtt_broker": "mqtt.local",
            "mqtt_enabled": True,
        }

    def test_mqtt_full_block(self, tmp_path):
        target = tmp_path / "full.ini"
        target.write_text(
            "[mqtt]\n"
            "broker = mqtt.meshtastic.org\n"
            "port = 8883\n"
        )
        result = load_global_overrides(target)
        assert result["mqtt_broker"] == "mqtt.meshtastic.org"
        assert result["mqtt_port"] == 8883
        assert result["mqtt_enabled"] is True

    def test_explicit_enabled_false_wins_over_inference(self, tmp_path):
        """An operator who wrote ``enabled = false`` means it."""
        target = tmp_path / "opt-out.ini"
        target.write_text(
            "[mqtt]\n"
            "broker = mqtt.example.com\n"
            "enabled = false\n"
        )
        result = load_global_overrides(target)
        assert result["mqtt_broker"] == "mqtt.example.com"
        assert result["mqtt_enabled"] is False

    def test_blank_broker_does_not_emit_keys(self, tmp_path):
        target = tmp_path / "blank.ini"
        target.write_text("[mqtt]\nbroker = \n")
        # Blank broker → no inference, nothing emitted
        assert load_global_overrides(target) == {}

    def test_zero_port_skipped(self, tmp_path):
        """port=0 is the "unset" sentinel from the contract — don't emit."""
        target = tmp_path / "zero-port.ini"
        target.write_text("[mqtt]\nbroker = mqtt.local\nport = 0\n")
        result = load_global_overrides(target)
        assert "mqtt_port" not in result
        assert result["mqtt_broker"] == "mqtt.local"

    def test_garbage_port_skipped(self, tmp_path):
        target = tmp_path / "garbage-port.ini"
        target.write_text("[mqtt]\nbroker = mqtt.local\nport = abc\n")
        result = load_global_overrides(target)
        assert "mqtt_port" not in result
