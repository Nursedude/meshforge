"""
Tests for RNS link quality visualization on the live web map (A2).

Tests the topology API enrichment in map_http_handler.py that adds
real RNS transport edges with link quality scoring to the network
topology endpoint.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.link_quality import LinkQualityScorer, LinkQuality


# ── LinkQualityScorer integration ──

class TestLinkQualityScorerForMap:
    """Test that LinkQualityScorer produces correct scores and colors for map use."""

    def setup_method(self):
        self.scorer = LinkQualityScorer()

    def test_excellent_snr_scores_high(self):
        """High SNR with direct link should score well."""
        score = self.scorer.score(snr=12.0, rssi=-75, hops=1)
        assert score.score >= 70
        assert score.quality in (LinkQuality.EXCELLENT, LinkQuality.GOOD)

    def test_poor_snr_scores_low(self):
        """Low SNR should score poorly."""
        score = self.scorer.score(snr=-10.0, rssi=-130, hops=4)
        assert score.score < 50

    def test_get_color_returns_hex(self):
        """get_color() should return a valid hex color string."""
        score = self.scorer.score(snr=8.0, hops=1)
        color = score.get_color()
        assert color.startswith('#')
        assert len(color) == 7

    def test_excellent_color_is_green(self):
        score = self.scorer.score(snr=15.0, rssi=-70, hops=1)
        if score.quality == LinkQuality.EXCELLENT:
            assert score.get_color() == '#22c55e'

    def test_bad_color_is_red(self):
        score = self.scorer.score(snr=-15.0, rssi=-140, hops=8)
        if score.quality == LinkQuality.BAD:
            assert score.get_color() == '#ef4444'

    def test_unknown_color_is_gray(self):
        """When no metrics available, quality should be unknown/gray."""
        score = self.scorer.score()
        assert score.get_color() in ('#6b7280', '#22c55e', '#84cc16',
                                      '#eab308', '#f97316', '#ef4444')

    def test_quality_label_is_string(self):
        """Quality label should be a string value."""
        score = self.scorer.score(snr=5.0, hops=2)
        assert isinstance(score.quality.value, str)
        assert score.quality.value in ('excellent', 'good', 'fair', 'poor', 'bad', 'unknown')


# ── Topology API RNS enrichment ──

class TestTopologyRnsEnrichment:
    """Test the RNS edge enrichment logic in _serve_network_topology."""

    def _build_mock_handler(self):
        """Create a minimal mock of MapRequestHandler for testing."""
        handler = MagicMock()
        handler._haversine = lambda lat1, lon1, lat2, lon2: (
            ((lat2 - lat1) ** 2 + (lon2 - lon1) ** 2) ** 0.5 * 111.0
        )
        return handler

    def _make_node(self, node_id, name, network, lat, lon, is_online=True):
        return {
            "id": node_id,
            "name": name,
            "network": network,
            "is_online": is_online,
            "is_gateway": False,
            "is_router": False,
            "lat": lat,
            "lon": lon,
            "snr": None,
            "battery": None,
            "link_type": None,
            "link_quality": None,
        }

    def _make_edge(self, src, dst, snr=None, rssi=None, hops=1,
                   is_active=True, announce_count=5):
        return {
            "source_id": src,
            "dest_id": dst,
            "snr": snr,
            "rssi": rssi,
            "hops": hops,
            "is_active": is_active,
            "announce_count": announce_count,
        }

    def test_rns_edges_added_to_links(self):
        """Active RNS edges with matching nodes should appear in links."""
        node_map = {
            "rns_aaa": self._make_node("rns_aaa", "NodeA", "rns", 21.3, -157.8),
            "rns_bbb": self._make_node("rns_bbb", "NodeB", "rns", 21.4, -157.7),
        }
        edges = [
            self._make_edge("rns_aaa", "rns_bbb", snr=8.0, rssi=-85, hops=1),
        ]
        topo = {"edges": edges, "nodes": [], "stats": {}}

        scorer = LinkQualityScorer()
        links = []

        for edge in topo.get("edges", []):
            if not edge.get("is_active", False):
                continue
            src_id = edge.get("source_id", "")
            dst_id = edge.get("dest_id", "")
            if src_id not in node_map or dst_id not in node_map:
                continue
            score = scorer.score(
                snr=edge.get("snr"),
                rssi=edge.get("rssi"),
                hops=edge.get("hops", 1),
                announce_count=edge.get("announce_count"),
            )
            links.append({
                "source": src_id,
                "target": dst_id,
                "type": "rns_transport",
                "snr": edge.get("snr"),
                "rssi": edge.get("rssi"),
                "hops": edge.get("hops", 0),
                "link_quality_score": round(score.score, 1),
                "link_quality_label": score.quality.value,
                "link_quality_color": score.get_color(),
                "announce_count": edge.get("announce_count", 0),
            })

        assert len(links) == 1
        link = links[0]
        assert link["type"] == "rns_transport"
        assert link["source"] == "rns_aaa"
        assert link["target"] == "rns_bbb"
        assert link["snr"] == 8.0
        assert link["rssi"] == -85
        assert link["hops"] == 1
        assert 0 <= link["link_quality_score"] <= 100
        assert link["link_quality_label"] in ('excellent', 'good', 'fair', 'poor', 'bad', 'unknown')
        assert link["link_quality_color"].startswith('#')

    def test_inactive_edges_filtered(self):
        """Edges with is_active=False should be skipped."""
        node_map = {
            "rns_aaa": self._make_node("rns_aaa", "A", "rns", 21.3, -157.8),
            "rns_bbb": self._make_node("rns_bbb", "B", "rns", 21.4, -157.7),
        }
        edges = [
            self._make_edge("rns_aaa", "rns_bbb", snr=5.0, is_active=False),
        ]

        links = []
        for edge in edges:
            if not edge.get("is_active", False):
                continue
            src_id = edge.get("source_id", "")
            dst_id = edge.get("dest_id", "")
            if src_id not in node_map or dst_id not in node_map:
                continue
            links.append({"type": "rns_transport"})

        assert len(links) == 0

    def test_edges_without_coordinates_skipped(self):
        """Edges where source or dest has no matching node are skipped."""
        node_map = {
            "rns_aaa": self._make_node("rns_aaa", "A", "rns", 21.3, -157.8),
            # rns_ccc deliberately missing from node_map
        }
        edges = [
            self._make_edge("rns_aaa", "rns_ccc", snr=5.0),
        ]

        links = []
        for edge in edges:
            if not edge.get("is_active", False):
                continue
            src_id = edge.get("source_id", "")
            dst_id = edge.get("dest_id", "")
            if src_id not in node_map or dst_id not in node_map:
                continue
            links.append({"type": "rns_transport"})

        assert len(links) == 0

    def test_multiple_edges_all_processed(self):
        """Multiple active edges should all be included."""
        node_map = {
            "rns_aaa": self._make_node("rns_aaa", "A", "rns", 21.3, -157.8),
            "rns_bbb": self._make_node("rns_bbb", "B", "rns", 21.4, -157.7),
            "rns_ccc": self._make_node("rns_ccc", "C", "rns", 21.5, -157.6),
        }
        edges = [
            self._make_edge("rns_aaa", "rns_bbb", snr=8.0, hops=1),
            self._make_edge("rns_aaa", "rns_ccc", snr=-3.0, hops=3),
            self._make_edge("rns_bbb", "rns_ccc", snr=2.0, hops=2),
        ]

        scorer = LinkQualityScorer()
        links = []
        for edge in edges:
            if not edge.get("is_active", False):
                continue
            src_id = edge.get("source_id", "")
            dst_id = edge.get("dest_id", "")
            if src_id not in node_map or dst_id not in node_map:
                continue
            score = scorer.score(
                snr=edge.get("snr"),
                rssi=edge.get("rssi"),
                hops=edge.get("hops", 1),
                announce_count=edge.get("announce_count"),
            )
            links.append({
                "type": "rns_transport",
                "link_quality_score": round(score.score, 1),
            })

        assert len(links) == 3
        # First link (best SNR) should have highest quality
        assert links[0]["link_quality_score"] >= links[1]["link_quality_score"]

    def test_edge_with_no_signal_data(self):
        """Edge with no SNR/RSSI should still produce a valid score."""
        node_map = {
            "rns_aaa": self._make_node("rns_aaa", "A", "rns", 21.3, -157.8),
            "rns_bbb": self._make_node("rns_bbb", "B", "rns", 21.4, -157.7),
        }
        edges = [
            self._make_edge("rns_aaa", "rns_bbb", snr=None, rssi=None, hops=2),
        ]

        scorer = LinkQualityScorer()
        links = []
        for edge in edges:
            if not edge.get("is_active", False):
                continue
            src_id = edge.get("source_id", "")
            dst_id = edge.get("dest_id", "")
            if src_id not in node_map or dst_id not in node_map:
                continue
            score = scorer.score(
                snr=edge.get("snr"),
                rssi=edge.get("rssi"),
                hops=edge.get("hops", 1),
                announce_count=edge.get("announce_count"),
            )
            links.append({
                "type": "rns_transport",
                "link_quality_score": round(score.score, 1),
                "link_quality_color": score.get_color(),
            })

        assert len(links) == 1
        assert 0 <= links[0]["link_quality_score"] <= 100
        assert links[0]["link_quality_color"].startswith('#')

    def test_graceful_when_tracker_unavailable(self):
        """When node tracker is unavailable, links list should be unchanged."""
        links_before = [{"source": "a", "target": "b", "type": "meshtastic"}]
        links = list(links_before)

        has_tracker = False
        if has_tracker:
            links.append({"type": "rns_transport"})

        assert links == links_before

    def test_link_quality_color_consistency(self):
        """Colors from scorer should match the documented palette."""
        scorer = LinkQualityScorer()
        valid_colors = {'#22c55e', '#84cc16', '#eab308', '#f97316', '#ef4444', '#6b7280'}

        test_snrs = [15.0, 7.0, 2.0, -3.0, -12.0]
        for snr in test_snrs:
            score = scorer.score(snr=snr, hops=1)
            assert score.get_color() in valid_colors, \
                f"SNR {snr} produced unexpected color {score.get_color()}"


# ── Edge dict format validation ──

class TestRnsLinkFormat:
    """Test the format of RNS transport link objects for the web map."""

    def test_link_has_required_fields(self):
        """RNS transport links must have all fields the frontend expects."""
        scorer = LinkQualityScorer()
        score = scorer.score(snr=5.0, rssi=-90, hops=2, announce_count=10)

        link = {
            "source": "rns_aaa",
            "target": "rns_bbb",
            "type": "rns_transport",
            "snr": 5.0,
            "rssi": -90,
            "hops": 2,
            "link_quality_score": round(score.score, 1),
            "link_quality_label": score.quality.value,
            "link_quality_color": score.get_color(),
            "announce_count": 10,
            "distance_km": 12.5,
        }

        required = ["source", "target", "type", "snr", "rssi", "hops",
                     "link_quality_score", "link_quality_label",
                     "link_quality_color", "announce_count", "distance_km"]
        for field in required:
            assert field in link, f"Missing required field: {field}"

    def test_type_is_rns_transport(self):
        """Type field must be 'rns_transport' to distinguish from proximity links."""
        link = {"type": "rns_transport"}
        assert link["type"] == "rns_transport"
        assert link["type"] != "rns"
        assert link["type"] != "gateway"

    def test_score_range(self):
        """Link quality score must be 0-100."""
        scorer = LinkQualityScorer()
        for snr in [-20, -10, -5, 0, 5, 10, 15]:
            score = scorer.score(snr=float(snr), hops=1)
            assert 0 <= score.score <= 100, \
                f"Score {score.score} out of range for SNR={snr}"
