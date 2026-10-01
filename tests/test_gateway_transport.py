"""
Tests for the RNS-over-Meshtastic config section.

The transport itself (gateway/rns_transport.py) was removed 2026-10-01; the
config dataclass stays so every existing gateway.json (which carries an
``rns_transport`` block) keeps loading. bridge_cli refuses enabled=true.

Run: python3 -m pytest tests/test_gateway_transport.py -v
"""

import pytest

from src.gateway.config import RNSOverMeshtasticConfig


class TestRNSOverMeshtasticConfig:
    """Tests for RNSOverMeshtasticConfig dataclass."""

    def test_defaults(self):
        """Test default configuration values."""
        config = RNSOverMeshtasticConfig()

        assert config.enabled is False
        assert config.connection_type == "tcp"
        assert config.device_path == "localhost:4403"
        assert config.data_speed == 8
        assert config.hop_limit == 3
        assert config.fragment_timeout_sec == 30
        assert config.max_pending_fragments == 100
        assert config.enable_stats is True
        assert config.stats_interval_sec == 60
        assert config.packet_loss_threshold == 0.1
        assert config.latency_threshold_ms == 5000

    def test_custom_values(self):
        """Test custom configuration values."""
        config = RNSOverMeshtasticConfig(
            enabled=True,
            connection_type="serial",
            device_path="/dev/ttyUSB0",
            data_speed=4,
            hop_limit=5,
            fragment_timeout_sec=60,
        )

        assert config.enabled is True
        assert config.connection_type == "serial"
        assert config.device_path == "/dev/ttyUSB0"
        assert config.data_speed == 4
        assert config.hop_limit == 5
        assert config.fragment_timeout_sec == 60

    def test_get_throughput_estimate_short_turbo(self):
        """Test throughput estimate for SHORT_TURBO preset."""
        config = RNSOverMeshtasticConfig(data_speed=8)
        throughput = config.get_throughput_estimate()

        assert throughput['name'] == 'SHORT_TURBO'
        assert throughput['bps'] == 500
        assert throughput['range'] == 'short'
        assert throughput['delay'] == 0.4

    def test_get_throughput_estimate_long_fast(self):
        """Test throughput estimate for LONG_FAST preset."""
        config = RNSOverMeshtasticConfig(data_speed=0)
        throughput = config.get_throughput_estimate()

        assert throughput['name'] == 'LONG_FAST'
        assert throughput['bps'] == 50
        assert throughput['range'] == 'maximum'

    def test_get_throughput_estimate_all_presets(self):
        """Test all speed presets return valid data."""
        for speed in range(9):
            config = RNSOverMeshtasticConfig(data_speed=speed)
            throughput = config.get_throughput_estimate()

            assert 'name' in throughput
            assert 'bps' in throughput
            assert 'range' in throughput
            assert 'delay' in throughput
            assert throughput['bps'] > 0

    def test_get_throughput_estimate_invalid_speed(self):
        """Test throughput estimate falls back for invalid speed."""
        config = RNSOverMeshtasticConfig(data_speed=99)
        throughput = config.get_throughput_estimate()

        # Should return SHORT_TURBO as default
        assert throughput['name'] == 'SHORT_TURBO'
