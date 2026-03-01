"""
Tests for RNS link quality map overlay (A2).

Tests the _snr_to_link_color() helper, set_rns_edges(), and
add_rns_link_quality_layer() functionality.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.coverage_map import (
    MapNode, CoverageMapGenerator, _snr_to_link_color,
)


# ── _snr_to_link_color ──

class TestSnrToLinkColor:

    def test_excellent_snr(self):
        color, label = _snr_to_link_color(8.0)
        assert color == '#22c55e'
        assert label == 'Excellent'

    def test_good_snr(self):
        color, label = _snr_to_link_color(3.0)
        assert color == '#eab308'
        assert label == 'Good'

    def test_fair_snr(self):
        color, label = _snr_to_link_color(-5.0)
        assert color == '#f97316'
        assert label == 'Fair'

    def test_bad_snr(self):
        color, label = _snr_to_link_color(-15.0)
        assert color == '#ef4444'
        assert label == 'Bad'

    def test_none_snr(self):
        color, label = _snr_to_link_color(None)
        assert color == '#6b7280'
        assert label == 'Unknown'

    def test_boundary_excellent(self):
        """SNR exactly 5 should be 'Good' not 'Excellent'."""
        color, label = _snr_to_link_color(5.0)
        assert label == 'Good'

    def test_boundary_good(self):
        """SNR exactly 0 should be 'Fair' not 'Good'."""
        color, label = _snr_to_link_color(0.0)
        assert label == 'Fair'

    def test_boundary_fair(self):
        """SNR exactly -10 should be 'Bad' not 'Fair'."""
        color, label = _snr_to_link_color(-10.0)
        assert label == 'Bad'


# ── set_rns_edges ──

class TestSetRnsEdges:

    def test_set_edges(self):
        gen = CoverageMapGenerator()
        edges = [{"source_id": "A", "dest_id": "B", "snr": 5.0}]
        gen.set_rns_edges(edges)
        assert gen._rns_edges == edges

    def test_set_empty_edges(self):
        gen = CoverageMapGenerator()
        gen.set_rns_edges([])
        assert gen._rns_edges == []

    def test_set_none_edges(self):
        gen = CoverageMapGenerator()
        gen.set_rns_edges(None)
        assert gen._rns_edges == []


# ── add_rns_link_quality_layer ──

class TestAddRnsLinkQualityLayer:

    def _make_generator_with_nodes(self):
        gen = CoverageMapGenerator()
        gen.add_node(MapNode(id="A", name="NodeA", latitude=21.3, longitude=-157.8))
        gen.add_node(MapNode(id="B", name="NodeB", latitude=21.31, longitude=-157.79))
        gen.add_node(MapNode(id="C", name="NodeC", latitude=21.32, longitude=-157.78))
        return gen

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._folium')
    def test_creates_feature_group(self, mock_folium):
        gen = self._make_generator_with_nodes()
        mock_fg = MagicMock()
        mock_folium.FeatureGroup.return_value = mock_fg
        mock_folium.PolyLine.return_value = MagicMock()

        mock_map = MagicMock()
        gen._map = mock_map

        edges = [{"source_id": "A", "dest_id": "B", "snr": 8.0}]
        gen.add_rns_link_quality_layer(edges)

        mock_folium.FeatureGroup.assert_called_once_with(
            name='RNS Link Quality', show=True
        )
        mock_fg.add_to.assert_called_once_with(mock_map)

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._folium')
    def test_draws_polyline_for_matching_nodes(self, mock_folium):
        gen = self._make_generator_with_nodes()
        mock_fg = MagicMock()
        mock_folium.FeatureGroup.return_value = mock_fg
        mock_poly = MagicMock()
        mock_folium.PolyLine.return_value = mock_poly

        mock_map = MagicMock()
        gen._map = mock_map

        edges = [{"source_id": "A", "dest_id": "B", "snr": 8.0, "rssi": -90}]
        gen.add_rns_link_quality_layer(edges)

        mock_folium.PolyLine.assert_called_once()
        kwargs = mock_folium.PolyLine.call_args[1]
        assert kwargs['color'] == '#22c55e'  # Excellent
        assert 'SNR: 8.0' in kwargs['tooltip']
        assert 'RSSI: -90' in kwargs['tooltip']
        mock_poly.add_to.assert_called_once_with(mock_fg)

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._folium')
    def test_skips_edges_with_missing_nodes(self, mock_folium):
        gen = self._make_generator_with_nodes()
        mock_fg = MagicMock()
        mock_folium.FeatureGroup.return_value = mock_fg

        mock_map = MagicMock()
        gen._map = mock_map

        # "X" is not a known node
        edges = [{"source_id": "A", "dest_id": "X", "snr": 5.0}]
        gen.add_rns_link_quality_layer(edges)

        mock_folium.PolyLine.assert_not_called()

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._folium')
    def test_inactive_edges_are_dashed(self, mock_folium):
        gen = self._make_generator_with_nodes()
        mock_fg = MagicMock()
        mock_folium.FeatureGroup.return_value = mock_fg
        mock_folium.PolyLine.return_value = MagicMock()

        mock_map = MagicMock()
        gen._map = mock_map

        edges = [{"source_id": "A", "dest_id": "B", "is_active": False}]
        gen.add_rns_link_quality_layer(edges)

        kwargs = mock_folium.PolyLine.call_args[1]
        assert kwargs['dash_array'] == '5, 10'
        assert kwargs['weight'] == 1

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._folium')
    def test_multiple_edges(self, mock_folium):
        gen = self._make_generator_with_nodes()
        mock_fg = MagicMock()
        mock_folium.FeatureGroup.return_value = mock_fg
        mock_folium.PolyLine.return_value = MagicMock()

        mock_map = MagicMock()
        gen._map = mock_map

        edges = [
            {"source_id": "A", "dest_id": "B", "snr": 8.0},
            {"source_id": "B", "dest_id": "C", "snr": -2.0},
            {"source_id": "A", "dest_id": "C", "snr": None},
        ]
        gen.add_rns_link_quality_layer(edges)

        assert mock_folium.PolyLine.call_count == 3

    @patch('utils.coverage_map._HAS_FOLIUM', False)
    def test_no_folium_is_noop(self):
        gen = self._make_generator_with_nodes()
        gen._map = MagicMock()
        # Should not raise
        gen.add_rns_link_quality_layer([{"source_id": "A", "dest_id": "B"}])

    def test_no_map_is_noop(self):
        gen = self._make_generator_with_nodes()
        gen._map = None
        # Should not raise
        gen.add_rns_link_quality_layer([{"source_id": "A", "dest_id": "B"}])

    def test_empty_edges_is_noop(self):
        gen = self._make_generator_with_nodes()
        gen._map = MagicMock()
        gen.add_rns_link_quality_layer([])
        # No crash expected

    @patch('utils.coverage_map._HAS_FOLIUM', True)
    @patch('utils.coverage_map._folium')
    def test_tooltip_includes_hops(self, mock_folium):
        gen = self._make_generator_with_nodes()
        mock_fg = MagicMock()
        mock_folium.FeatureGroup.return_value = mock_fg
        mock_folium.PolyLine.return_value = MagicMock()

        mock_map = MagicMock()
        gen._map = mock_map

        edges = [{"source_id": "A", "dest_id": "B", "hops": 3}]
        gen.add_rns_link_quality_layer(edges)

        kwargs = mock_folium.PolyLine.call_args[1]
        assert 'Hops: 3' in kwargs['tooltip']
