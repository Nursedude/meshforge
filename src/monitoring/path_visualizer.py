"""
Path Visualizer - Multi-Hop Path Visualization for Mesh Networks.

Provides interactive visualization of message paths through the mesh network,
showing hop-by-hop details, signal quality, and animated packet flow.

Features:
- Animated packet flow along network paths
- Hop-by-hop signal quality visualization
- Path comparison (different routes to same destination)
- Integration with topology visualizer
- Real-time path updates

Usage:
    from monitoring.path_visualizer import PathVisualizer

    visualizer = PathVisualizer()
    visualizer.add_path_trace(packet_id, hops)
    visualizer.generate("path_view.html")

Reference: Inspired by Datadog Network Path and Wireshark flow graphs
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

_HopInfo, _HopState, _MeshPacket, _HAS_TRAFFIC_INSPECTOR = safe_import(
    'monitoring.traffic_inspector', 'HopInfo', 'HopState', 'MeshPacket'
)

# Resolve traffic inspector types with fallback
if _HAS_TRAFFIC_INSPECTOR:
    HopInfo = _HopInfo
    HopState = _HopState
    MeshPacket = _MeshPacket
else:
    # Fallback definitions for standalone use
    from enum import Enum, auto

    class HopState(Enum):
        RECEIVED = "received"
        DECODED = "decoded"
        RELAYED = "relayed"
        DELIVERED = "delivered"
        DROPPED = "dropped"
        FAILED = "failed"

    @dataclass
    class HopInfo:
        hop_number: int
        node_id: str
        node_name: str = ""
        state: HopState = HopState.RECEIVED
        timestamp: datetime = field(default_factory=datetime.now)
        latitude: Optional[float] = None
        longitude: Optional[float] = None
        snr: Optional[float] = None
        rssi: Optional[int] = None
        latency_ms: Optional[float] = None
        details: Dict[str, Any] = field(default_factory=dict)

        def to_dict(self) -> Dict[str, Any]:
            return {
                "hop": self.hop_number,
                "node_id": self.node_id,
                "node_name": self.node_name,
                "state": self.state.value,
                "snr": self.snr,
                "rssi": self.rssi,
                "latency_ms": self.latency_ms,
            }

logger = logging.getLogger(__name__)


@dataclass
class PathSegment:
    """A single segment (hop) in a path visualization."""
    from_node: str
    to_node: str
    from_name: str = ""
    to_name: str = ""

    # Segment metrics
    snr: Optional[float] = None
    rssi: Optional[int] = None
    latency_ms: Optional[float] = None
    success: bool = True

    # Position (for geo visualization)
    from_lat: Optional[float] = None
    from_lon: Optional[float] = None
    to_lat: Optional[float] = None
    to_lon: Optional[float] = None

    def get_quality_color(self) -> str:
        """Get color based on segment quality."""
        if not self.success:
            return "#ef4444"  # Red for failed
        if self.snr is None:
            return "#6b7280"  # Gray for unknown
        if self.snr > 10:
            return "#22c55e"  # Green - excellent
        if self.snr > 5:
            return "#84cc16"  # Light green - good
        if self.snr > 0:
            return "#eab308"  # Yellow - marginal
        if self.snr > -5:
            return "#f97316"  # Orange - poor
        return "#ef4444"  # Red - bad

    def get_quality_label(self) -> str:
        """Get quality description."""
        if not self.success:
            return "Failed"
        if self.snr is None:
            return "Unknown"
        if self.snr > 10:
            return "Excellent"
        if self.snr > 5:
            return "Good"
        if self.snr > 0:
            return "Marginal"
        if self.snr > -5:
            return "Poor"
        return "Bad"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_node,
            "to": self.to_node,
            "from_name": self.from_name or self.from_node,
            "to_name": self.to_name or self.to_node,
            "snr": self.snr,
            "rssi": self.rssi,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "color": self.get_quality_color(),
            "quality": self.get_quality_label(),
            "from_lat": self.from_lat,
            "from_lon": self.from_lon,
            "to_lat": self.to_lat,
            "to_lon": self.to_lon,
        }


@dataclass
class TracedPath:
    """A complete traced path through the network."""
    path_id: str
    packet_id: str = ""
    source: str = ""
    destination: str = ""
    segments: List[PathSegment] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    # Path summary
    total_hops: int = 0
    total_latency_ms: float = 0.0
    success: bool = True
    weakest_snr: Optional[float] = None

    # Animation state
    is_active: bool = False
    animation_progress: float = 0.0

    def add_hop(self, hop: HopInfo, prev_hop: Optional[HopInfo] = None) -> None:
        """Add a hop to the path."""
        if prev_hop is None:
            return

        segment = PathSegment(
            from_node=prev_hop.node_id,
            to_node=hop.node_id,
            from_name=prev_hop.node_name,
            to_name=hop.node_name,
            snr=hop.snr,
            rssi=hop.rssi,
            latency_ms=hop.latency_ms,
            success=hop.state not in (HopState.DROPPED, HopState.FAILED),
            from_lat=prev_hop.latitude,
            from_lon=prev_hop.longitude,
            to_lat=hop.latitude,
            to_lon=hop.longitude,
        )
        self.segments.append(segment)

        # Update summary
        self.total_hops = len(self.segments)
        if hop.latency_ms:
            self.total_latency_ms += hop.latency_ms
        if hop.snr is not None:
            if self.weakest_snr is None or hop.snr < self.weakest_snr:
                self.weakest_snr = hop.snr
        if hop.state in (HopState.DROPPED, HopState.FAILED):
            self.success = False

    @classmethod
    def from_hop_list(cls, path_id: str, hops: List[HopInfo],
                      packet_id: str = "") -> 'TracedPath':
        """Create TracedPath from list of HopInfo."""
        path = cls(path_id=path_id, packet_id=packet_id)

        if hops:
            path.source = hops[0].node_id
            path.destination = hops[-1].node_id
            path.timestamp = hops[0].timestamp

            for i, hop in enumerate(hops):
                if i > 0:
                    path.add_hop(hop, hops[i-1])

        return path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path_id": self.path_id,
            "packet_id": self.packet_id,
            "source": self.source,
            "destination": self.destination,
            "segments": [s.to_dict() for s in self.segments],
            "timestamp": self.timestamp.isoformat(),
            "total_hops": self.total_hops,
            "total_latency_ms": self.total_latency_ms,
            "success": self.success,
            "weakest_snr": self.weakest_snr,
            "is_active": self.is_active,
        }


class PathVisualizer:
    """
    Interactive multi-hop path visualization.

    Generates HTML visualization showing:
    - Network topology with nodes and edges
    - Animated packet flow along traced paths
    - Hop-by-hop metrics (SNR, RSSI, latency)
    - Path comparison view
    - Timeline of path traces
    """

    # Node colors (same as topology visualizer)
    NODE_COLORS = {
        "local": "#8b5cf6",      # Purple
        "gateway": "#ec4899",    # Pink
        "router": "#f97316",     # Orange
        "relay": "#3b82f6",      # Blue
        "destination": "#22c55e", # Green
        "source": "#06b6d4",     # Cyan
        "node": "#6b7280",       # Gray
    }

    def __init__(self):
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._paths: List[TracedPath] = []
        self._active_path_id: Optional[str] = None

    def add_node(self, node_id: str, name: str = "", node_type: str = "node",
                 latitude: Optional[float] = None,
                 longitude: Optional[float] = None,
                 metadata: Dict[str, Any] = None) -> None:
        """Add a node to the visualization."""
        self._nodes[node_id] = {
            "id": node_id,
            "name": name or node_id,
            "type": node_type,
            "latitude": latitude,
            "longitude": longitude,
            "metadata": metadata or {},
        }

    def add_path(self, path: TracedPath) -> None:
        """Add a traced path to the visualization."""
        self._paths.append(path)

        # Ensure all nodes in path exist
        for segment in path.segments:
            if segment.from_node not in self._nodes:
                self.add_node(segment.from_node, segment.from_name,
                              latitude=segment.from_lat, longitude=segment.from_lon)
            if segment.to_node not in self._nodes:
                self.add_node(segment.to_node, segment.to_name,
                              latitude=segment.to_lat, longitude=segment.to_lon)

    def add_path_trace(self, packet_id: str, hops: List[HopInfo]) -> TracedPath:
        """Add a path from a list of hop info."""
        path = TracedPath.from_hop_list(
            path_id=f"path_{len(self._paths)}",
            hops=hops,
            packet_id=packet_id,
        )
        self.add_path(path)
        return path

    def set_active_path(self, path_id: str) -> None:
        """Set the active (animated) path."""
        self._active_path_id = path_id
        for path in self._paths:
            path.is_active = (path.path_id == path_id)

    def has_paths(self) -> bool:
        """Check if any paths have been added."""
        return len(self._paths) > 0

    def get_path_count(self) -> int:
        """Get number of paths."""
        return len(self._paths)

    def get_path_stats(self) -> Dict[str, Any]:
        """Get statistics across all paths.

        Always returns a complete dict with all keys, even when empty.
        """
        if not self._paths:
            return {
                "total_paths": 0,
                "success_rate": 0,
                "avg_hops": 0,
                "max_hops": 0,
                "avg_snr": None,
                "min_snr": None,
                "avg_latency_ms": None,
                "unique_nodes": len(self._nodes),
            }

        all_snr = []
        all_latency = []
        success_count = 0

        for path in self._paths:
            if path.success:
                success_count += 1
            if path.weakest_snr is not None:
                all_snr.append(path.weakest_snr)
            if path.total_latency_ms:
                all_latency.append(path.total_latency_ms)

        return {
            "total_paths": len(self._paths),
            "success_rate": success_count / len(self._paths) if self._paths else 0,
            "avg_hops": sum(p.total_hops for p in self._paths) / len(self._paths) if self._paths else 0,
            "max_hops": max((p.total_hops for p in self._paths), default=0),
            "avg_snr": sum(all_snr) / len(all_snr) if all_snr else None,
            "min_snr": min(all_snr) if all_snr else None,
            "avg_latency_ms": sum(all_latency) / len(all_latency) if all_latency else None,
            "unique_nodes": len(self._nodes),
        }

    def generate(self, output_path: str = None,
                 title: str = "MeshForge Path Visualization") -> str:
        """Generate the path visualization HTML."""
        if output_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(cache_dir / "path_visualization.html")

        # Prepare data
        nodes_data = list(self._nodes.values())
        paths_data = [p.to_dict() for p in self._paths]
        stats = self.get_path_stats()

        html = self._generate_html(
            nodes_data=nodes_data,
            paths_data=paths_data,
            stats=stats,
            title=html_escape(title),
        )

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        logger.info(f"Path visualization saved to: {output_path}")
        return output_path

    def _generate_html(self, nodes_data: List[dict], paths_data: List[dict],
                       stats: dict, title: str) -> str:
        """Generate the complete HTML visualization."""
        nodes_json = json.dumps(nodes_data)
        paths_json = json.dumps(paths_data)
        stats_json = json.dumps(stats)
        colors_json = json.dumps(self.NODE_COLORS)

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            overflow: hidden;
        }}
        #container {{
            display: flex;
            height: 100vh;
        }}
        #main-view {{
            flex: 1;
            display: flex;
            flex-direction: column;
        }}
        #graph {{
            flex: 1;
            background: #1e293b;
            position: relative;
        }}
        #timeline {{
            height: 150px;
            background: #0f172a;
            border-top: 1px solid #334155;
            padding: 16px;
            overflow-x: auto;
        }}
        #sidebar {{
            width: 350px;
            background: #0f172a;
            border-left: 1px solid #334155;
            overflow-y: auto;
            padding: 16px;
        }}
        .panel {{
            background: #1e293b;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 16px;
        }}
        .panel h3 {{
            font-size: 14px;
            font-weight: 600;
            color: #94a3b8;
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .stat-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
        }}
        .stat-label {{
            color: #64748b;
        }}
        .stat-value {{
            color: #f8fafc;
            font-weight: 600;
        }}
        .path-list {{
            max-height: 300px;
            overflow-y: auto;
        }}
        .path-item {{
            padding: 12px;
            background: #0f172a;
            border-radius: 6px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .path-item:hover {{
            background: #1e3a5f;
        }}
        .path-item.active {{
            border: 2px solid #3b82f6;
        }}
        .path-header {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
        }}
        .path-route {{
            font-size: 13px;
            color: #94a3b8;
        }}
        .path-status {{
            font-size: 12px;
            padding: 2px 8px;
            border-radius: 4px;
        }}
        .path-status.success {{
            background: #166534;
            color: #86efac;
        }}
        .path-status.failed {{
            background: #7f1d1d;
            color: #fca5a5;
        }}
        .hop-detail {{
            display: flex;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid #334155;
        }}
        .hop-detail:last-child {{
            border-bottom: none;
        }}
        .hop-number {{
            width: 30px;
            height: 30px;
            background: #334155;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            margin-right: 12px;
        }}
        .hop-info {{
            flex: 1;
        }}
        .hop-node {{
            font-weight: 600;
            color: #f8fafc;
        }}
        .hop-metrics {{
            font-size: 12px;
            color: #64748b;
        }}
        .hop-quality {{
            width: 60px;
            text-align: right;
        }}
        .quality-bar {{
            height: 4px;
            background: #334155;
            border-radius: 2px;
            margin-top: 4px;
        }}
        .quality-fill {{
            height: 100%;
            border-radius: 2px;
        }}
        svg {{
            width: 100%;
            height: 100%;
        }}
        .node {{
            cursor: pointer;
        }}
        .node-circle {{
            stroke: #0f172a;
            stroke-width: 3;
        }}
        .node-label {{
            font-size: 11px;
            fill: #94a3b8;
            pointer-events: none;
            text-anchor: middle;
        }}
        .path-line {{
            fill: none;
            stroke-width: 3;
            opacity: 0.4;
        }}
        .path-line.active {{
            opacity: 1;
            stroke-width: 4;
        }}
        .packet-marker {{
            fill: #f8fafc;
            filter: drop-shadow(0 0 8px rgba(255,255,255,0.5));
        }}
        .hop-marker {{
            fill: none;
            stroke-width: 2;
            stroke-dasharray: 4,4;
        }}
        #title {{
            position: absolute;
            top: 16px;
            left: 50%;
            transform: translateX(-50%);
            font-size: 18px;
            font-weight: 600;
            color: #f8fafc;
            z-index: 100;
        }}
        .controls {{
            position: absolute;
            top: 16px;
            left: 16px;
            z-index: 100;
            display: flex;
            gap: 8px;
        }}
        .controls button {{
            background: #334155;
            border: none;
            color: #e2e8f0;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .controls button:hover {{
            background: #475569;
        }}
        .controls button.active {{
            background: #3b82f6;
        }}
        .timeline-title {{
            font-size: 12px;
            color: #64748b;
            margin-bottom: 12px;
        }}
        .timeline-track {{
            height: 80px;
            background: #1e293b;
            border-radius: 6px;
            position: relative;
            overflow: hidden;
        }}
        .timeline-event {{
            position: absolute;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            top: 50%;
            transform: translateY(-50%);
            cursor: pointer;
        }}
        .tooltip {{
            position: absolute;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 12px;
            font-size: 13px;
            pointer-events: none;
            z-index: 1000;
            min-width: 200px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            display: none;
        }}
        .legend {{
            display: flex;
            gap: 16px;
            margin-top: 12px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: #94a3b8;
        }}
        .legend-color {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }}
    </style>
</head>
<body>
    <div id="container">
        <div id="main-view">
            <div id="graph">
                <div id="title">{title}</div>
                <div class="controls">
                    <button onclick="toggleAnimation()" id="anim-btn">
                        <span>&#9654;</span> Animate
                    </button>
                    <button onclick="resetView()">Reset View</button>
                    <button onclick="showAllPaths()">Show All</button>
                </div>
                <svg></svg>
            </div>
            <div id="timeline">
                <div class="timeline-title">Path Timeline</div>
                <div class="timeline-track" id="timeline-track"></div>
                <div class="legend">
                    <div class="legend-item">
                        <div class="legend-color" style="background: #22c55e;"></div>
                        <span>Success</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-color" style="background: #ef4444;"></div>
                        <span>Failed</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-color" style="background: #3b82f6;"></div>
                        <span>Active</span>
                    </div>
                </div>
            </div>
        </div>
        <div id="sidebar">
            <div class="panel">
                <h3>Path Statistics</h3>
                <div class="stat-row">
                    <span class="stat-label">Total Paths</span>
                    <span class="stat-value" id="stat-paths">0</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Success Rate</span>
                    <span class="stat-value" id="stat-success">0%</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Avg Hops</span>
                    <span class="stat-value" id="stat-hops">0</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Avg SNR</span>
                    <span class="stat-value" id="stat-snr">-</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Avg Latency</span>
                    <span class="stat-value" id="stat-latency">-</span>
                </div>
            </div>

            <div class="panel">
                <h3>Traced Paths</h3>
                <div class="path-list" id="path-list"></div>
            </div>

            <div class="panel" id="hop-panel" style="display:none;">
                <h3>Hop Details</h3>
                <div id="hop-details"></div>
            </div>
        </div>
    </div>

    <div class="tooltip" id="tooltip"></div>

    <script>
        // Data from Python
        const nodesData = {nodes_json};
        const pathsData = {paths_json};
        const stats = {stats_json};
        const nodeColors = {colors_json};

        // State
        let selectedPath = null;
        let animating = false;
        let animationId = null;

        // Update stats display
        document.getElementById('stat-paths').textContent = stats.total_paths || 0;
        document.getElementById('stat-success').textContent =
            stats.success_rate ? (stats.success_rate * 100).toFixed(0) + '%' : '0%';
        document.getElementById('stat-hops').textContent =
            stats.avg_hops ? stats.avg_hops.toFixed(1) : '0';
        document.getElementById('stat-snr').textContent =
            stats.avg_snr !== null ? stats.avg_snr.toFixed(1) + ' dB' : '-';
        document.getElementById('stat-latency').textContent =
            stats.avg_latency_ms !== null ? stats.avg_latency_ms.toFixed(0) + ' ms' : '-';

        // Build path list
        const pathList = document.getElementById('path-list');
        pathsData.forEach((path, i) => {{
            const item = document.createElement('div');
            item.className = 'path-item';
            item.dataset.pathId = path.path_id;

            const route = path.segments.map(s => s.from_name || s.from).concat(
                path.segments.length ? [path.segments[path.segments.length-1].to_name ||
                                       path.segments[path.segments.length-1].to] : []
            ).join(' → ');

            item.innerHTML = `
                <div class="path-header">
                    <span>${{path.total_hops}} hops</span>
                    <span class="path-status ${{path.success ? 'success' : 'failed'}}">
                        ${{path.success ? 'Success' : 'Failed'}}
                    </span>
                </div>
                <div class="path-route">${{route || 'No hops'}}</div>
            `;

            item.onclick = () => selectPath(path.path_id);
            pathList.appendChild(item);
        }});

        // D3.js visualization
        const svg = d3.select('svg');
        const width = document.getElementById('graph').clientWidth;
        const height = document.getElementById('graph').clientHeight;

        const g = svg.append('g');

        // Zoom behavior
        const zoom = d3.zoom()
            .scaleExtent([0.1, 4])
            .on('zoom', (event) => g.attr('transform', event.transform));
        svg.call(zoom);

        // Create node positions using force simulation
        const nodeMap = new Map();
        nodesData.forEach(n => nodeMap.set(n.id, n));

        // Collect all unique nodes from paths
        const allNodes = new Set();
        pathsData.forEach(path => {{
            path.segments.forEach(seg => {{
                allNodes.add(seg.from);
                allNodes.add(seg.to);
            }});
        }});

        // Create nodes array for simulation
        const simNodes = Array.from(allNodes).map(id => {{
            const data = nodeMap.get(id) || {{}};
            return {{
                id: id,
                name: data.name || id,
                type: data.type || 'node',
                x: width / 2 + (Math.random() - 0.5) * 200,
                y: height / 2 + (Math.random() - 0.5) * 200,
            }};
        }});

        // Create links from all path segments
        const simLinks = [];
        pathsData.forEach(path => {{
            path.segments.forEach(seg => {{
                simLinks.push({{
                    source: seg.from,
                    target: seg.to,
                    snr: seg.snr,
                    color: seg.color,
                    path_id: path.path_id,
                }});
            }});
        }});

        // Force simulation
        const simulation = d3.forceSimulation(simNodes)
            .force('link', d3.forceLink(simLinks)
                .id(d => d.id)
                .distance(100))
            .force('charge', d3.forceManyBody().strength(-400))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collision', d3.forceCollide().radius(40));

        // Draw path lines (background)
        const pathLines = g.append('g').attr('class', 'path-lines');

        // Group links by path for drawing
        const pathGroups = {{}};
        simLinks.forEach(link => {{
            if (!pathGroups[link.path_id]) pathGroups[link.path_id] = [];
            pathGroups[link.path_id].push(link);
        }});

        Object.entries(pathGroups).forEach(([pathId, links]) => {{
            links.forEach(link => {{
                pathLines.append('line')
                    .attr('class', 'path-line')
                    .attr('data-path', pathId)
                    .attr('stroke', link.color || '#6b7280');
            }});
        }});

        // Draw nodes
        const nodeGroup = g.append('g').attr('class', 'nodes');

        const nodes = nodeGroup.selectAll('g')
            .data(simNodes)
            .enter().append('g')
            .attr('class', 'node')
            .call(d3.drag()
                .on('start', dragstarted)
                .on('drag', dragged)
                .on('end', dragended));

        nodes.append('circle')
            .attr('class', 'node-circle')
            .attr('r', 16)
            .attr('fill', d => nodeColors[d.type] || '#6b7280');

        nodes.append('text')
            .attr('class', 'node-label')
            .attr('dy', 30)
            .text(d => d.name.length > 10 ? d.name.substring(0, 10) + '...' : d.name);

        // Packet marker for animation
        const packetMarker = g.append('circle')
            .attr('class', 'packet-marker')
            .attr('r', 8)
            .style('display', 'none');

        // Simulation tick
        simulation.on('tick', () => {{
            // Update all path lines
            const lines = pathLines.selectAll('line').data(simLinks);
            lines
                .attr('x1', d => d.source.x)
                .attr('y1', d => d.source.y)
                .attr('x2', d => d.target.x)
                .attr('y2', d => d.target.y);

            nodes.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
        }});

        // Timeline visualization
        const timelineTrack = document.getElementById('timeline-track');
        const timeWidth = timelineTrack.clientWidth;

        if (pathsData.length > 0) {{
            const times = pathsData.map(p => new Date(p.timestamp).getTime());
            const minTime = Math.min(...times);
            const maxTime = Math.max(...times);
            const timeRange = maxTime - minTime || 1;

            pathsData.forEach((path, i) => {{
                const pathTime = new Date(path.timestamp).getTime();
                const x = ((pathTime - minTime) / timeRange) * (timeWidth - 20) + 10;

                const event = document.createElement('div');
                event.className = 'timeline-event';
                event.style.left = x + 'px';
                event.style.background = path.success ? '#22c55e' : '#ef4444';
                event.onclick = () => selectPath(path.path_id);
                timelineTrack.appendChild(event);
            }});
        }}

        // Select path function
        function selectPath(pathId) {{
            selectedPath = pathsData.find(p => p.path_id === pathId);

            // Update path list highlighting
            document.querySelectorAll('.path-item').forEach(item => {{
                item.classList.toggle('active', item.dataset.pathId === pathId);
            }});

            // Highlight path lines
            pathLines.selectAll('.path-line')
                .classed('active', d => d.path_id === pathId)
                .attr('opacity', d => d.path_id === pathId ? 1 : 0.2);

            // Show hop details
            if (selectedPath) {{
                showHopDetails(selectedPath);
            }}
        }}

        function showHopDetails(path) {{
            const panel = document.getElementById('hop-panel');
            const details = document.getElementById('hop-details');
            panel.style.display = 'block';

            details.innerHTML = '';
            path.segments.forEach((seg, i) => {{
                const hop = document.createElement('div');
                hop.className = 'hop-detail';

                const qualityPercent = seg.snr !== null ?
                    Math.min(100, Math.max(0, (seg.snr + 10) * 5)) : 50;

                hop.innerHTML = `
                    <div class="hop-number">${{i + 1}}</div>
                    <div class="hop-info">
                        <div class="hop-node">${{seg.from_name || seg.from}} → ${{seg.to_name || seg.to}}</div>
                        <div class="hop-metrics">
                            ${{seg.snr !== null ? `SNR: ${{seg.snr.toFixed(1)}} dB` : ''}}
                            ${{seg.rssi !== null ? ` | RSSI: ${{seg.rssi}} dBm` : ''}}
                            ${{seg.latency_ms !== null ? ` | ${{seg.latency_ms.toFixed(0)}}ms` : ''}}
                        </div>
                    </div>
                    <div class="hop-quality">
                        <div style="font-size:11px;color:${{seg.color}}">${{seg.quality}}</div>
                        <div class="quality-bar">
                            <div class="quality-fill" style="width:${{qualityPercent}}%;background:${{seg.color}}"></div>
                        </div>
                    </div>
                `;
                details.appendChild(hop);
            }});
        }}

        // Animation
        function toggleAnimation() {{
            animating = !animating;
            const btn = document.getElementById('anim-btn');

            if (animating && selectedPath) {{
                btn.innerHTML = '<span>&#9632;</span> Stop';
                btn.classList.add('active');
                animatePath(selectedPath);
            }} else {{
                btn.innerHTML = '<span>&#9654;</span> Animate';
                btn.classList.remove('active');
                stopAnimation();
            }}
        }}

        function animatePath(path) {{
            if (!path.segments.length) return;

            packetMarker.style('display', 'block');
            let segIndex = 0;
            let progress = 0;

            function animate() {{
                if (!animating || segIndex >= path.segments.length) {{
                    // Loop
                    segIndex = 0;
                    progress = 0;
                }}

                const seg = path.segments[segIndex];
                const sourceNode = simNodes.find(n => n.id === seg.from);
                const targetNode = simNodes.find(n => n.id === seg.to);

                if (sourceNode && targetNode) {{
                    const x = sourceNode.x + (targetNode.x - sourceNode.x) * progress;
                    const y = sourceNode.y + (targetNode.y - sourceNode.y) * progress;
                    packetMarker.attr('cx', x).attr('cy', y);
                }}

                progress += 0.02;
                if (progress >= 1) {{
                    progress = 0;
                    segIndex++;
                }}

                if (animating) {{
                    animationId = requestAnimationFrame(animate);
                }}
            }}

            animate();
        }}

        function stopAnimation() {{
            animating = false;
            if (animationId) cancelAnimationFrame(animationId);
            packetMarker.style('display', 'none');
        }}

        function resetView() {{
            svg.transition().duration(750).call(
                zoom.transform,
                d3.zoomIdentity
            );
        }}

        function showAllPaths() {{
            selectedPath = null;
            document.querySelectorAll('.path-item').forEach(item => {{
                item.classList.remove('active');
            }});
            pathLines.selectAll('.path-line')
                .classed('active', false)
                .attr('opacity', 0.4);
            document.getElementById('hop-panel').style.display = 'none';
            stopAnimation();
        }}

        // Drag functions
        function dragstarted(event, d) {{
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        }}

        function dragged(event, d) {{
            d.fx = event.x;
            d.fy = event.y;
        }}

        function dragended(event, d) {{
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        }}

        // Auto-select first path if available
        if (pathsData.length > 0) {{
            setTimeout(() => selectPath(pathsData[0].path_id), 1000);
        }}
    </script>
</body>
</html>'''

    def format_path_report(self, path: TracedPath) -> str:
        """Format a path as ASCII report for TUI display."""
        lines = []
        lines.append("=" * 70)
        lines.append(" PATH TRACE ".center(70, "="))
        lines.append("=" * 70)
        lines.append("")

        lines.append(f"Path ID: {path.path_id}")
        lines.append(f"Packet:  {path.packet_id}")
        lines.append(f"Time:    {path.timestamp.isoformat()}")
        lines.append(f"Status:  {'SUCCESS' if path.success else 'FAILED'}")
        lines.append("")

        lines.append(f"Source:      {path.source}")
        lines.append(f"Destination: {path.destination}")
        lines.append(f"Total Hops:  {path.total_hops}")
        lines.append(f"Latency:     {path.total_latency_ms:.0f} ms")
        if path.weakest_snr is not None:
            lines.append(f"Weakest SNR: {path.weakest_snr:.1f} dB")
        lines.append("")

        lines.append("-" * 70)
        lines.append(" HOP DETAILS")
        lines.append("-" * 70)

        for i, seg in enumerate(path.segments):
            status = "OK" if seg.success else "FAIL"
            quality = seg.get_quality_label()

            lines.append(f"\n  Hop {i+1}: {seg.from_name or seg.from_node} -> {seg.to_name or seg.to_node}")
            lines.append(f"         Status: {status} | Quality: {quality}")

            metrics = []
            if seg.snr is not None:
                metrics.append(f"SNR: {seg.snr:.1f} dB")
            if seg.rssi is not None:
                metrics.append(f"RSSI: {seg.rssi} dBm")
            if seg.latency_ms is not None:
                metrics.append(f"Latency: {seg.latency_ms:.0f} ms")

            if metrics:
                lines.append(f"         {' | '.join(metrics)}")

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    def generate_ascii(self, max_width: int = 78) -> str:
        """Generate ASCII visualization for TUI display."""
        lines = []
        lines.append("=" * max_width)
        lines.append(" MULTI-HOP PATH VISUALIZATION ".center(max_width, "="))
        lines.append("=" * max_width)
        lines.append("")

        stats = self.get_path_stats()
        if stats:
            lines.append(f"Paths: {stats.get('total_paths', 0)}")
            lines.append(f"Success Rate: {stats.get('success_rate', 0)*100:.0f}%")
            lines.append(f"Avg Hops: {stats.get('avg_hops', 0):.1f}")
            if stats.get('avg_snr') is not None:
                lines.append(f"Avg SNR: {stats['avg_snr']:.1f} dB")
            lines.append("")

        lines.append("-" * max_width)
        lines.append(" TRACED PATHS")
        lines.append("-" * max_width)

        for path in self._paths[:10]:  # Limit display
            status = "OK" if path.success else "FAIL"
            time_str = path.timestamp.strftime("%H:%M:%S")

            # Build route string
            nodes_in_path = []
            for seg in path.segments:
                if not nodes_in_path:
                    nodes_in_path.append(seg.from_name or seg.from_node[:8])
                nodes_in_path.append(seg.to_name or seg.to[:8])

            route = " -> ".join(nodes_in_path) if nodes_in_path else "empty"
            if len(route) > max_width - 30:
                route = route[:max_width-33] + "..."

            lines.append(f"\n  [{status}] {time_str} | {path.total_hops} hops")
            lines.append(f"       {route}")
            if path.weakest_snr is not None:
                lines.append(f"       Weakest: {path.weakest_snr:.1f} dB | Latency: {path.total_latency_ms:.0f}ms")

        lines.append("")
        lines.append("=" * max_width)
        return "\n".join(lines)
