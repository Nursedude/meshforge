"""
Tests for signal-strength weighted heatmap generation (A1).

Tests the _compute_heatmap_weight() helper and the enhanced
generate_heatmap(weight_by=...) functionality.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.coverage_map import MapNode, _compute_heatmap_weight


# ── _compute_heatmap_weight ──

class TestComputeHeatmapWeight:
    """Test the weight computation helper."""

    # -- density mode (original behavior) --

    def test_density_online(self):
        node = MapNode(id="1", name="A", latitude=0, longitude=0, is_online=True)
        assert _compute_heatmap_weight(node, "density") == 1.0

    def test_density_offline(self):
        node = MapNode(id="1", name="A", latitude=0, longitude=0, is_online=False)
        assert _compute_heatmap_weight(node, "density") == 0.3

    def test_density_ignores_snr(self):
        node = MapNode(id="1", name="A", latitude=0, longitude=0,
                       is_online=True, snr=10.0)
        assert _compute_heatmap_weight(node, "density") == 1.0

    # -- snr mode --

    def test_snr_excellent(self):
        """SNR +10 dB should map to 1.0 (max)."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, snr=10.0)
        assert _compute_heatmap_weight(node, "snr") == 1.0

    def test_snr_worst(self):
        """SNR -20 dB should map to 0.0 (min)."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, snr=-20.0)
        assert _compute_heatmap_weight(node, "snr") == 0.0

    def test_snr_midpoint(self):
        """SNR -5 dB should map to 0.5 (midpoint)."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, snr=-5.0)
        assert _compute_heatmap_weight(node, "snr") == 0.5

    def test_snr_clamps_high(self):
        """SNR above +10 dB should clamp to 1.0."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, snr=20.0)
        assert _compute_heatmap_weight(node, "snr") == 1.0

    def test_snr_clamps_low(self):
        """SNR below -20 dB should clamp to 0.0."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, snr=-30.0)
        assert _compute_heatmap_weight(node, "snr") == 0.0

    def test_snr_missing_online(self):
        """No SNR data, online node: fallback 0.5."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0,
                       is_online=True, snr=None)
        assert _compute_heatmap_weight(node, "snr") == 0.5

    def test_snr_missing_offline(self):
        """No SNR data, offline node: fallback 0.1."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0,
                       is_online=False, snr=None)
        assert _compute_heatmap_weight(node, "snr") == 0.1

    # -- rssi mode --

    def test_rssi_strong(self):
        """RSSI -70 dBm should map to 1.0 (max)."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, rssi=-70)
        assert _compute_heatmap_weight(node, "rssi") == 1.0

    def test_rssi_weakest(self):
        """RSSI -137 dBm should map to 0.0 (min)."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, rssi=-137)
        assert _compute_heatmap_weight(node, "rssi") == pytest.approx(0.0, abs=0.01)

    def test_rssi_midrange(self):
        """RSSI ~-103.5 dBm should be ~0.5."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, rssi=-104)
        weight = _compute_heatmap_weight(node, "rssi")
        assert 0.4 < weight < 0.6

    def test_rssi_clamps_high(self):
        """RSSI above -70 dBm should clamp to 1.0."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0, rssi=-50)
        assert _compute_heatmap_weight(node, "rssi") == 1.0

    def test_rssi_missing_online(self):
        """No RSSI data, online: fallback 0.5."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0,
                       is_online=True, rssi=None)
        assert _compute_heatmap_weight(node, "rssi") == 0.5

    def test_rssi_missing_offline(self):
        """No RSSI data, offline: fallback 0.1."""
        node = MapNode(id="1", name="A", latitude=0, longitude=0,
                       is_online=False, rssi=None)
        assert _compute_heatmap_weight(node, "rssi") == 0.1

    # -- unknown mode defaults to density --

    def test_unknown_mode_defaults_to_density(self):
        node = MapNode(id="1", name="A", latitude=0, longitude=0, is_online=True)
        assert _compute_heatmap_weight(node, "something_else") == 1.0


# ── generate_heatmap integration ──

class TestGenerateHeatmapSignal:
    """Test the generate_heatmap() method with different weight_by modes."""

    def _make_generator(self):
        from utils.coverage_map import CoverageMapGenerator
        gen = CoverageMapGenerator()
        gen.add_node(MapNode(
            id="1", name="NodeA", latitude=21.3, longitude=-157.8,
            is_online=True, snr=8.0, rssi=-85
        ))
        gen.add_node(MapNode(
            id="2", name="NodeB", latitude=21.31, longitude=-157.79,
            is_online=True, snr=-5.0, rssi=-120
        ))
        gen.add_node(MapNode(
            id="3", name="NodeC", latitude=21.32, longitude=-157.78,
            is_online=False, snr=None, rssi=None
        ))
        return gen

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._HAS_FOLIUM_PLUGINS', True)
    @patch('utils.coverage_map._folium')
    @patch('utils.coverage_map._folium_plugins')
    def test_density_mode_backward_compatible(self, mock_plugins, mock_folium):
        """density mode should produce the original gradient."""
        mock_map = MagicMock()
        mock_folium.Map.return_value = mock_map
        mock_folium.LayerControl.return_value = MagicMock()
        mock_heatmap = MagicMock()
        mock_plugins.HeatMap = mock_heatmap

        gen = self._make_generator()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            path = f.name
        try:
            result = gen.generate_heatmap(output_path=path, weight_by="density")
            assert result == path
            # Check HeatMap was called
            mock_heatmap.assert_called_once()
            call_args = mock_heatmap.call_args
            heat_data = call_args[0][0]
            # 3 nodes with lat/lon
            assert len(heat_data) == 3
            # density mode: online=1.0, offline=0.3
            assert heat_data[0][2] == 1.0  # NodeA online
            assert heat_data[2][2] == 0.3  # NodeC offline
        finally:
            os.unlink(path)

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._HAS_FOLIUM_PLUGINS', True)
    @patch('utils.coverage_map._folium')
    @patch('utils.coverage_map._folium_plugins')
    def test_snr_mode_uses_snr_weights(self, mock_plugins, mock_folium):
        """snr mode should use SNR-normalized weights."""
        mock_map = MagicMock()
        mock_folium.Map.return_value = mock_map
        mock_folium.LayerControl.return_value = MagicMock()
        mock_heatmap = MagicMock()
        mock_plugins.HeatMap = mock_heatmap

        gen = self._make_generator()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            path = f.name
        try:
            gen.generate_heatmap(output_path=path, weight_by="snr")
            call_args = mock_heatmap.call_args
            heat_data = call_args[0][0]
            # NodeA: SNR 8.0 -> (8+20)/30 = 0.933
            assert 0.9 < heat_data[0][2] < 1.0
            # NodeB: SNR -5.0 -> (-5+20)/30 = 0.5
            assert heat_data[1][2] == 0.5
            # NodeC: no SNR, offline -> 0.1
            assert heat_data[2][2] == 0.1
        finally:
            os.unlink(path)

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._HAS_FOLIUM_PLUGINS', True)
    @patch('utils.coverage_map._folium')
    @patch('utils.coverage_map._folium_plugins')
    def test_snr_mode_uses_signal_gradient(self, mock_plugins, mock_folium):
        """snr mode should use the 5-stop signal gradient."""
        mock_map = MagicMock()
        mock_folium.Map.return_value = mock_map
        mock_folium.LayerControl.return_value = MagicMock()
        mock_heatmap = MagicMock()
        mock_plugins.HeatMap = mock_heatmap

        gen = self._make_generator()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            path = f.name
        try:
            gen.generate_heatmap(output_path=path, weight_by="snr")
            call_kwargs = mock_heatmap.call_args[1]
            gradient = call_kwargs.get('gradient', {})
            # Should have 5 stops (signal gradient)
            assert len(gradient) == 5
            assert 0.2 in gradient
            assert 1.0 in gradient
        finally:
            os.unlink(path)

    @patch('utils.coverage_map._HAS_FOLIUM', False)
    def test_no_folium_returns_empty(self):
        """Without folium, generate_heatmap returns empty string."""
        gen = self._make_generator()
        result = gen.generate_heatmap(weight_by="snr")
        assert result == ""
