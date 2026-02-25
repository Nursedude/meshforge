"""
Network Topology Visualizer for MeshForge.

Generates interactive browser-based network topology visualizations using D3.js
force-directed graphs. Shows nodes, edges, link quality, and real-time topology.

Output: Self-contained HTML files viewable in any browser.

Usage:
    from utils.topology_visualizer import TopologyVisualizer

    visualizer = TopologyVisualizer()
    visualizer.add_node("node1", name="Gateway", node_type="gateway")
    visualizer.add_edge("node1", "node2", hops=2, snr=8.5)
    visualizer.generate("topology.html")

    # Or from NetworkTopology instance
    from gateway.network_topology import get_network_topology
    topology = get_network_topology()
    visualizer = TopologyVisualizer.from_topology(topology)
    visualizer.generate("topology.html")
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


@dataclass
class TopoNode:
    """Node for topology visualization."""
    id: str
    name: str = ""
    node_type: str = "node"  # node, gateway, router, local, rns, meshtastic
    network: str = "unknown"  # rns, meshtastic, both
    is_online: bool = True
    services: List[str] = field(default_factory=list)
    hops: int = 0
    last_seen: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Geographic position for mapping
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "type": self.node_type,
            "network": self.network,
            "online": self.is_online,
            "services": self.services,
            "hops": self.hops,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "metadata": self.metadata,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
        }

    def has_position(self) -> bool:
        """Check if node has a geographic position."""
        return self.latitude is not None and self.longitude is not None


@dataclass
class TopoEdge:
    """Edge for topology visualization."""
    source: str
    target: str
    hops: int = 1
    snr: Optional[float] = None
    rssi: Optional[int] = None
    is_active: bool = True
    bidirectional: bool = True
    announce_count: int = 0
    interface: str = ""
    weight: float = 1.0

    def get_quality_color(self) -> str:
        """Get color based on link quality (SNR)."""
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

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "hops": self.hops,
            "snr": self.snr,
            "rssi": self.rssi,
            "active": self.is_active,
            "bidirectional": self.bidirectional,
            "announce_count": self.announce_count,
            "interface": self.interface,
            "weight": self.weight,
            "color": self.get_quality_color(),
            "quality": self.get_quality_label(),
        }


class TopologyVisualizer:
    """
    Interactive network topology visualizer using D3.js force-directed graph.

    Features:
    - Force-directed layout for automatic node positioning
    - Node coloring by type (local, gateway, RNS, Meshtastic)
    - Edge coloring by link quality (SNR)
    - Interactive zoom and pan
    - Node click for details
    - Real-time statistics panel
    - Export to standalone HTML
    """

    # Node colors by type
    NODE_COLORS = {
        "local": "#8b5cf6",      # Purple - our node
        "gateway": "#ec4899",    # Pink - bridges
        "router": "#f97316",     # Orange - routers
        "rns": "#22c55e",        # Green - RNS nodes
        "meshtastic": "#3b82f6", # Blue - Meshtastic
        "both": "#06b6d4",       # Cyan - dual-network
        "node": "#6b7280",       # Gray - generic
    }

    # Node sizes by type
    NODE_SIZES = {
        "local": 20,
        "gateway": 16,
        "router": 14,
        "rns": 12,
        "meshtastic": 12,
        "both": 14,
        "node": 10,
    }

    def __init__(self):
        self._nodes: Dict[str, TopoNode] = {}
        self._edges: List[TopoEdge] = []
        self._events: List[Dict[str, Any]] = []

    def add_node(self, node_id: str, name: str = None, node_type: str = "node",
                 network: str = "unknown", is_online: bool = True,
                 services: List[str] = None, hops: int = 0,
                 metadata: Dict[str, Any] = None,
                 latitude: float = None, longitude: float = None,
                 altitude: float = None) -> TopoNode:
        """Add a node to the topology."""
        node = TopoNode(
            id=node_id,
            name=name or node_id,
            node_type=node_type,
            network=network,
            is_online=is_online,
            services=services or [],
            hops=hops,
            last_seen=datetime.now(),
            metadata=metadata or {},
            latitude=latitude,
            longitude=longitude,
            altitude=altitude,
        )
        self._nodes[node_id] = node
        return node

    def add_edge(self, source: str, target: str, hops: int = 1,
                 snr: float = None, rssi: int = None,
                 is_active: bool = True, bidirectional: bool = True,
                 announce_count: int = 0, interface: str = "",
                 weight: float = None) -> TopoEdge:
        """Add an edge between nodes."""
        # Auto-create nodes if they don't exist
        if source not in self._nodes:
            self.add_node(source)
        if target not in self._nodes:
            self.add_node(target)

        edge = TopoEdge(
            source=source,
            target=target,
            hops=hops,
            snr=snr,
            rssi=rssi,
            is_active=is_active,
            bidirectional=bidirectional,
            announce_count=announce_count,
            interface=interface,
            weight=weight or float(hops + 1),
        )
        self._edges.append(edge)
        return edge

    def add_event(self, event_type: str, node_id: str = None,
                  details: Dict[str, Any] = None):
        """Add a topology event for display."""
        self._events.append({
            "type": event_type,
            "node_id": node_id,
            "timestamp": datetime.now().isoformat(),
            "details": details or {},
        })
        # Keep last 100 events
        if len(self._events) > 100:
            self._events = self._events[-100:]

    @classmethod
    def from_topology(cls, topology) -> 'TopologyVisualizer':
        """
        Create visualizer from a NetworkTopology instance.

        Args:
            topology: NetworkTopology instance from gateway.network_topology

        Returns:
            TopologyVisualizer populated with topology data
        """
        viz = cls()

        # Add local node
        viz.add_node("local", name="Local Node", node_type="local", network="rns")

        # Get topology data
        try:
            topo_dict = topology.to_dict()

            # Add nodes
            for node_id in topo_dict.get("nodes", []):
                if node_id == "local":
                    continue

                # Determine node type from ID
                node_type = "node"
                network = "unknown"
                if node_id.startswith("rns_"):
                    node_type = "rns"
                    network = "rns"
                elif node_id.startswith("mesh_") or node_id.startswith("!"):
                    node_type = "meshtastic"
                    network = "meshtastic"

                viz.add_node(node_id, node_type=node_type, network=network)

            # Add edges
            for edge_data in topo_dict.get("edges", []):
                viz.add_edge(
                    source=edge_data.get("source_id", ""),
                    target=edge_data.get("dest_id", ""),
                    hops=edge_data.get("hops", 1),
                    snr=edge_data.get("snr"),
                    rssi=edge_data.get("rssi"),
                    is_active=edge_data.get("is_active", True),
                    announce_count=edge_data.get("announce_count", 0),
                    interface=edge_data.get("interface", ""),
                    weight=edge_data.get("weight", 1.0),
                )

            # Add recent events
            for event in topology.get_recent_events(50):
                viz.add_event(
                    event_type=event.get("event_type", "UNKNOWN"),
                    node_id=event.get("node_id"),
                    details=event,
                )

        except Exception as e:
            logger.warning(f"Error loading topology data: {e}")

        return viz

    def get_stats(self) -> Dict[str, Any]:
        """Get topology statistics."""
        active_edges = [e for e in self._edges if e.is_active]
        online_nodes = [n for n in self._nodes.values() if n.is_online]

        # Calculate average hops
        total_hops = sum(e.hops for e in active_edges)
        avg_hops = total_hops / len(active_edges) if active_edges else 0

        # Count by network type
        rns_count = sum(1 for n in self._nodes.values() if n.network == "rns")
        mesh_count = sum(1 for n in self._nodes.values() if n.network == "meshtastic")

        # Count by node type
        type_counts = {}
        for node in self._nodes.values():
            type_counts[node.node_type] = type_counts.get(node.node_type, 0) + 1

        return {
            "total_nodes": len(self._nodes),
            "online_nodes": len(online_nodes),
            "total_edges": len(self._edges),
            "active_edges": len(active_edges),
            "avg_hops": round(avg_hops, 2),
            "max_hops": max((e.hops for e in self._edges), default=0),
            "rns_nodes": rns_count,
            "meshtastic_nodes": mesh_count,
            "type_counts": type_counts,
            "event_count": len(self._events),
        }

    def generate(self, output_path: str = None, title: str = "MeshForge Network Topology") -> str:
        """
        Generate the topology visualization HTML.

        Args:
            output_path: Output file path (default: ~/.cache/meshforge/topology.html)
            title: Page title

        Returns:
            Path to generated HTML file
        """
        if output_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(cache_dir / "topology.html")

        # Prepare data for D3.js
        nodes_data = [n.to_dict() for n in self._nodes.values()]
        edges_data = [e.to_dict() for e in self._edges]
        stats = self.get_stats()
        events_data = self._events[-20:]  # Last 20 events for display

        # Escape title for HTML
        safe_title = html_escape(title)

        html = self._generate_html(
            nodes_data=nodes_data,
            edges_data=edges_data,
            stats=stats,
            events_data=events_data,
            title=safe_title,
        )

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        logger.info(f"Topology visualization saved to: {output_path}")
        return output_path

    def _generate_html(self, nodes_data: List[dict], edges_data: List[dict],
                       stats: dict, events_data: List[dict], title: str) -> str:
        """Generate the complete HTML visualization."""
        # JSON-encode data for JavaScript
        nodes_json = json.dumps(nodes_data)
        edges_json = json.dumps(edges_data)
        stats_json = json.dumps(stats)
        events_json = json.dumps(events_data)
        node_colors_json = json.dumps(self.NODE_COLORS)
        node_sizes_json = json.dumps(self.NODE_SIZES)

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
        #graph {{
            flex: 1;
            background: #1e293b;
        }}
        #sidebar {{
            width: 320px;
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
        .stat-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }}
        .stat-item {{
            text-align: center;
        }}
        .stat-value {{
            font-size: 24px;
            font-weight: 700;
            color: #f8fafc;
        }}
        .stat-label {{
            font-size: 11px;
            color: #64748b;
            margin-top: 4px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            margin-bottom: 8px;
            font-size: 13px;
        }}
        .legend-color {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }}
        .legend-line {{
            width: 24px;
            height: 3px;
            margin-right: 8px;
            border-radius: 2px;
        }}
        .event-list {{
            max-height: 200px;
            overflow-y: auto;
        }}
        .event-item {{
            padding: 8px;
            background: #0f172a;
            border-radius: 4px;
            margin-bottom: 6px;
            font-size: 12px;
        }}
        .event-type {{
            font-weight: 600;
            color: #8b5cf6;
        }}
        .event-time {{
            color: #64748b;
            font-size: 10px;
        }}
        .node-tooltip {{
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
        }}
        .tooltip-title {{
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 8px;
            color: #f8fafc;
        }}
        .tooltip-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 4px;
        }}
        .tooltip-label {{
            color: #64748b;
        }}
        .tooltip-value {{
            color: #e2e8f0;
            font-weight: 500;
        }}
        .controls {{
            position: absolute;
            top: 16px;
            left: 16px;
            z-index: 100;
        }}
        .controls button {{
            background: #334155;
            border: none;
            color: #e2e8f0;
            padding: 8px 12px;
            margin-right: 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
        }}
        .controls button:hover {{
            background: #475569;
        }}
        svg {{
            width: 100%;
            height: 100%;
        }}
        .link {{
            stroke-opacity: 0.6;
        }}
        .link.inactive {{
            stroke-dasharray: 5, 5;
            stroke-opacity: 0.3;
        }}
        .node {{
            cursor: pointer;
        }}
        .node:hover {{
            filter: brightness(1.2);
        }}
        .node-label {{
            font-size: 10px;
            fill: #94a3b8;
            pointer-events: none;
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
    </style>
</head>
<body>
    <div id="container">
        <div id="graph">
            <div id="title">{title}</div>
            <div class="controls">
                <button onclick="resetZoom()">Reset View</button>
                <button onclick="toggleLabels()">Toggle Labels</button>
                <button onclick="centerGraph()">Center</button>
            </div>
            <svg></svg>
        </div>
        <div id="sidebar">
            <div class="panel">
                <h3>Network Statistics</h3>
                <div class="stat-grid">
                    <div class="stat-item">
                        <div class="stat-value" id="stat-nodes">0</div>
                        <div class="stat-label">Nodes</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="stat-edges">0</div>
                        <div class="stat-label">Links</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="stat-hops">0</div>
                        <div class="stat-label">Avg Hops</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="stat-max">0</div>
                        <div class="stat-label">Max Hops</div>
                    </div>
                </div>
            </div>

            <div class="panel">
                <h3>Node Types</h3>
                <div id="node-legend"></div>
            </div>

            <div class="panel">
                <h3>Link Quality (SNR)</h3>
                <div class="legend-item">
                    <div class="legend-line" style="background: #22c55e;"></div>
                    <span>Excellent (&gt;10 dB)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-line" style="background: #84cc16;"></div>
                    <span>Good (5-10 dB)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-line" style="background: #eab308;"></div>
                    <span>Marginal (0-5 dB)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-line" style="background: #f97316;"></div>
                    <span>Poor (-5-0 dB)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-line" style="background: #ef4444;"></div>
                    <span>Bad (&lt;-5 dB)</span>
                </div>
                <div class="legend-item">
                    <div class="legend-line" style="background: #6b7280;"></div>
                    <span>Unknown</span>
                </div>
            </div>

            <div class="panel">
                <h3>Recent Events</h3>
                <div class="event-list" id="events"></div>
            </div>
        </div>
    </div>

    <div class="node-tooltip" id="tooltip" style="display: none;"></div>

    <script>
        // Data from Python
        const nodesData = {nodes_json};
        const edgesData = {edges_json};
        const stats = {stats_json};
        const events = {events_json};
        const nodeColors = {node_colors_json};
        const nodeSizes = {node_sizes_json};

        // Update stats display
        document.getElementById('stat-nodes').textContent = stats.total_nodes;
        document.getElementById('stat-edges').textContent = stats.active_edges;
        document.getElementById('stat-hops').textContent = stats.avg_hops;
        document.getElementById('stat-max').textContent = stats.max_hops;

        // Build node legend
        const legendDiv = document.getElementById('node-legend');
        Object.entries(nodeColors).forEach(([type, color]) => {{
            const item = document.createElement('div');
            item.className = 'legend-item';
            item.innerHTML = `<div class="legend-color" style="background: ${{color}};"></div><span>${{type}}</span>`;
            legendDiv.appendChild(item);
        }});

        // Build events list
        const eventsDiv = document.getElementById('events');
        events.slice().reverse().forEach(event => {{
            const item = document.createElement('div');
            item.className = 'event-item';
            const time = new Date(event.timestamp).toLocaleTimeString();
            item.innerHTML = `
                <span class="event-type">${{event.type}}</span>
                ${{event.node_id ? `<span> - ${{event.node_id.substring(0, 16)}}</span>` : ''}}
                <div class="event-time">${{time}}</div>
            `;
            eventsDiv.appendChild(item);
        }});

        // D3.js Force-Directed Graph
        const svg = d3.select('svg');
        const width = document.getElementById('graph').clientWidth;
        const height = document.getElementById('graph').clientHeight;

        // Create arrow markers for directed edges
        svg.append('defs').selectAll('marker')
            .data(['end'])
            .enter().append('marker')
            .attr('id', d => d)
            .attr('viewBox', '0 -5 10 10')
            .attr('refX', 20)
            .attr('refY', 0)
            .attr('markerWidth', 6)
            .attr('markerHeight', 6)
            .attr('orient', 'auto')
            .append('path')
            .attr('fill', '#64748b')
            .attr('d', 'M0,-5L10,0L0,5');

        const g = svg.append('g');

        // Zoom behavior
        const zoom = d3.zoom()
            .scaleExtent([0.1, 4])
            .on('zoom', (event) => {{
                g.attr('transform', event.transform);
            }});

        svg.call(zoom);

        // Create simulation
        const simulation = d3.forceSimulation(nodesData)
            .force('link', d3.forceLink(edgesData)
                .id(d => d.id)
                .distance(d => 80 + d.hops * 30))
            .force('charge', d3.forceManyBody().strength(-300))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collision', d3.forceCollide().radius(30));

        // Draw links
        const link = g.append('g')
            .selectAll('line')
            .data(edgesData)
            .enter().append('line')
            .attr('class', d => 'link' + (d.active ? '' : ' inactive'))
            .attr('stroke', d => d.color)
            .attr('stroke-width', d => Math.max(1, 3 - d.hops * 0.5))
            .attr('marker-end', d => d.bidirectional ? null : 'url(#end)');

        // Draw nodes
        const node = g.append('g')
            .selectAll('circle')
            .data(nodesData)
            .enter().append('circle')
            .attr('class', 'node')
            .attr('r', d => nodeSizes[d.type] || 10)
            .attr('fill', d => nodeColors[d.type] || '#6b7280')
            .attr('stroke', '#0f172a')
            .attr('stroke-width', 2)
            .call(d3.drag()
                .on('start', dragstarted)
                .on('drag', dragged)
                .on('end', dragended));

        // Node labels
        let showLabels = true;
        const labels = g.append('g')
            .selectAll('text')
            .data(nodesData)
            .enter().append('text')
            .attr('class', 'node-label')
            .attr('dy', d => (nodeSizes[d.type] || 10) + 12)
            .attr('text-anchor', 'middle')
            .text(d => d.name.length > 12 ? d.name.substring(0, 12) + '...' : d.name);

        // Tooltip
        const tooltip = document.getElementById('tooltip');

        node.on('mouseover', (event, d) => {{
            tooltip.style.display = 'block';
            tooltip.style.left = (event.pageX + 10) + 'px';
            tooltip.style.top = (event.pageY + 10) + 'px';

            let html = `<div class="tooltip-title">${{d.name}}</div>`;
            html += `<div class="tooltip-row"><span class="tooltip-label">ID:</span><span class="tooltip-value">${{d.id.substring(0, 20)}}</span></div>`;
            html += `<div class="tooltip-row"><span class="tooltip-label">Type:</span><span class="tooltip-value">${{d.type}}</span></div>`;
            html += `<div class="tooltip-row"><span class="tooltip-label">Network:</span><span class="tooltip-value">${{d.network}}</span></div>`;
            html += `<div class="tooltip-row"><span class="tooltip-label">Status:</span><span class="tooltip-value">${{d.online ? 'Online' : 'Offline'}}</span></div>`;
            if (d.hops > 0) {{
                html += `<div class="tooltip-row"><span class="tooltip-label">Hops:</span><span class="tooltip-value">${{d.hops}}</span></div>`;
            }}
            if (d.services && d.services.length > 0) {{
                html += `<div class="tooltip-row"><span class="tooltip-label">Services:</span><span class="tooltip-value">${{d.services.join(', ')}}</span></div>`;
            }}
            tooltip.innerHTML = html;
        }})
        .on('mouseout', () => {{
            tooltip.style.display = 'none';
        }});

        // Link hover
        link.on('mouseover', (event, d) => {{
            tooltip.style.display = 'block';
            tooltip.style.left = (event.pageX + 10) + 'px';
            tooltip.style.top = (event.pageY + 10) + 'px';

            let html = `<div class="tooltip-title">Link</div>`;
            html += `<div class="tooltip-row"><span class="tooltip-label">Quality:</span><span class="tooltip-value">${{d.quality}}</span></div>`;
            html += `<div class="tooltip-row"><span class="tooltip-label">Hops:</span><span class="tooltip-value">${{d.hops}}</span></div>`;
            if (d.snr !== null) {{
                html += `<div class="tooltip-row"><span class="tooltip-label">SNR:</span><span class="tooltip-value">${{d.snr.toFixed(1)}} dB</span></div>`;
            }}
            if (d.rssi !== null) {{
                html += `<div class="tooltip-row"><span class="tooltip-label">RSSI:</span><span class="tooltip-value">${{d.rssi}} dBm</span></div>`;
            }}
            if (d.interface) {{
                html += `<div class="tooltip-row"><span class="tooltip-label">Interface:</span><span class="tooltip-value">${{d.interface}}</span></div>`;
            }}
            html += `<div class="tooltip-row"><span class="tooltip-label">Announces:</span><span class="tooltip-value">${{d.announce_count}}</span></div>`;
            tooltip.innerHTML = html;
        }})
        .on('mouseout', () => {{
            tooltip.style.display = 'none';
        }});

        // Simulation tick
        simulation.on('tick', () => {{
            link
                .attr('x1', d => d.source.x)
                .attr('y1', d => d.source.y)
                .attr('x2', d => d.target.x)
                .attr('y2', d => d.target.y);

            node
                .attr('cx', d => d.x)
                .attr('cy', d => d.y);

            labels
                .attr('x', d => d.x)
                .attr('y', d => d.y);
        }});

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

        // Control functions
        function resetZoom() {{
            svg.transition().duration(750).call(
                zoom.transform,
                d3.zoomIdentity
            );
        }}

        function toggleLabels() {{
            showLabels = !showLabels;
            labels.style('display', showLabels ? 'block' : 'none');
        }}

        function centerGraph() {{
            const bounds = g.node().getBBox();
            const fullWidth = width;
            const fullHeight = height;
            const widthScale = fullWidth / bounds.width;
            const heightScale = fullHeight / bounds.height;
            const scale = 0.8 * Math.min(widthScale, heightScale);
            const translate = [
                fullWidth / 2 - scale * (bounds.x + bounds.width / 2),
                fullHeight / 2 - scale * (bounds.y + bounds.height / 2)
            ];

            svg.transition().duration(750).call(
                zoom.transform,
                d3.zoomIdentity.translate(translate[0], translate[1]).scale(scale)
            );
        }}

        // Initial centering after simulation settles
        setTimeout(centerGraph, 2000);
    </script>
</body>
</html>'''

    def generate_ascii(self, max_width: int = 78) -> str:
        """
        Generate ASCII representation of topology for TUI display.

        Returns a simple text representation suitable for terminal display.
        """
        lines = []
        stats = self.get_stats()

        lines.append("=" * max_width)
        lines.append("NETWORK TOPOLOGY".center(max_width))
        lines.append("=" * max_width)
        lines.append("")

        # Stats summary
        lines.append(f"Nodes: {stats['total_nodes']} ({stats['online_nodes']} online)")
        lines.append(f"Links: {stats['active_edges']}/{stats['total_edges']} active")
        lines.append(f"Avg Hops: {stats['avg_hops']} | Max Hops: {stats['max_hops']}")
        lines.append("")

        # Node list
        lines.append("-" * max_width)
        lines.append("NODES:")
        lines.append("-" * max_width)

        for node_id, node in sorted(self._nodes.items()):
            status = "●" if node.is_online else "○"
            name = node.name[:20] if node.name else node_id[:20]
            ntype = node.node_type[:10]
            services = ", ".join(node.services[:2]) if node.services else "-"
            lines.append(f"  {status} {name:<20} [{ntype:<10}] Services: {services}")

        lines.append("")

        # Edge list (top 10 by recent activity)
        lines.append("-" * max_width)
        lines.append("LINKS (most active):")
        lines.append("-" * max_width)

        sorted_edges = sorted(self._edges, key=lambda e: e.announce_count, reverse=True)[:10]
        for edge in sorted_edges:
            src = edge.source[:12]
            dst = edge.target[:12]
            quality = edge.get_quality_label()[:8]
            snr = f"{edge.snr:.1f}dB" if edge.snr else "N/A"
            status = "↔" if edge.bidirectional else "→"
            lines.append(f"  {src} {status} {dst} | {quality:<8} | SNR: {snr:<7} | {edge.hops}hop")

        lines.append("")
        lines.append("=" * max_width)

        return "\n".join(lines)

    def export_geojson(self, output_path: str = None,
                       include_edges: bool = True) -> Tuple[str, int]:
        """
        Export topology as GeoJSON for mapping.

        Creates a GeoJSON FeatureCollection with:
        - Point features for nodes with positions
        - LineString features for edges between positioned nodes

        Args:
            output_path: Output file path (default: topology.geojson in cache)
            include_edges: Include edges as LineString features

        Returns:
            Tuple of (output_path, feature_count)
        """
        if output_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(cache_dir / "topology.geojson")

        features = []

        # Add node features
        for node_id, node in self._nodes.items():
            if not node.has_position():
                continue

            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [node.longitude, node.latitude]
                },
                "properties": {
                    "id": node.id,
                    "name": node.name or node.id,
                    "type": node.node_type,
                    "network": node.network,
                    "online": node.is_online,
                    "hops": node.hops,
                    "services": node.services,
                    "marker-color": self.NODE_COLORS.get(node.node_type, "#6b7280"),
                    "marker-size": "medium" if node.node_type in ("local", "gateway") else "small",
                }
            }

            if node.altitude is not None:
                feature["properties"]["altitude"] = node.altitude

            if node.last_seen:
                feature["properties"]["last_seen"] = node.last_seen.isoformat()

            features.append(feature)

        # Add edge features (only between positioned nodes)
        if include_edges:
            for edge in self._edges:
                source = self._nodes.get(edge.source)
                target = self._nodes.get(edge.target)

                if not (source and target and source.has_position() and target.has_position()):
                    continue

                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [source.longitude, source.latitude],
                            [target.longitude, target.latitude]
                        ]
                    },
                    "properties": {
                        "source": edge.source,
                        "target": edge.target,
                        "hops": edge.hops,
                        "active": edge.is_active,
                        "bidirectional": edge.bidirectional,
                        "quality": edge.get_quality_label(),
                        "stroke": edge.get_quality_color(),
                        "stroke-width": 2 if edge.is_active else 1,
                        "stroke-opacity": 0.8 if edge.is_active else 0.4,
                    }
                }

                if edge.snr is not None:
                    feature["properties"]["snr"] = edge.snr
                if edge.rssi is not None:
                    feature["properties"]["rssi"] = edge.rssi

                features.append(feature)

        # Build GeoJSON
        geojson = {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "generator": "MeshForge TopologyVisualizer",
                "generated_at": datetime.now().isoformat(),
                "stats": self.get_stats(),
            }
        }

        with open(output_path, 'w') as f:
            json.dump(geojson, f, indent=2)

        logger.info(f"Exported {len(features)} features to {output_path}")
        return output_path, len(features)

    def export_d3_json(self, output_path: str = None) -> Tuple[str, int]:
        """
        Export topology as D3.js compatible JSON format.

        Creates a JSON file with nodes and links arrays suitable for
        D3.js force-directed graph visualization.

        Args:
            output_path: Output file path (default: topology_d3.json in cache)

        Returns:
            Tuple of (output_path, total_count)
        """
        if output_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(cache_dir / "topology_d3.json")

        # Build nodes array
        nodes = []
        for node_id, node in self._nodes.items():
            node_data = {
                "id": node.id,
                "name": node.name or node.id,
                "group": node.node_type,
                "network": node.network,
                "online": node.is_online,
                "hops": node.hops,
                "services": node.services,
                "color": self.NODE_COLORS.get(node.node_type, "#6b7280"),
                "size": self.NODE_SIZES.get(node.node_type, 10),
            }

            if node.has_position():
                node_data["lat"] = node.latitude
                node_data["lon"] = node.longitude
                if node.altitude:
                    node_data["alt"] = node.altitude

            nodes.append(node_data)

        # Build links array
        links = []
        for edge in self._edges:
            link_data = {
                "source": edge.source,
                "target": edge.target,
                "value": edge.weight,
                "hops": edge.hops,
                "active": edge.is_active,
                "bidirectional": edge.bidirectional,
                "color": edge.get_quality_color(),
                "quality": edge.get_quality_label(),
            }

            if edge.snr is not None:
                link_data["snr"] = edge.snr
            if edge.rssi is not None:
                link_data["rssi"] = edge.rssi

            links.append(link_data)

        # Build D3 format
        d3_data = {
            "nodes": nodes,
            "links": links,
            "meta": {
                "generator": "MeshForge TopologyVisualizer",
                "generated_at": datetime.now().isoformat(),
                "stats": self.get_stats(),
            }
        }

        with open(output_path, 'w') as f:
            json.dump(d3_data, f, indent=2)

        total = len(nodes) + len(links)
        logger.info(f"Exported D3 data ({len(nodes)} nodes, {len(links)} links) to {output_path}")
        return output_path, total

    def export_graphml(self, output_path: str = None) -> Tuple[str, int]:
        """
        Export topology as GraphML format for tools like Gephi.

        Args:
            output_path: Output file path (default: topology.graphml in cache)

        Returns:
            Tuple of (output_path, edge_count)
        """
        if output_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(cache_dir / "topology.graphml")

        def xml_escape(s: str) -> str:
            """Escape XML special characters."""
            return (s.replace("&", "&amp;")
                     .replace("<", "&lt;")
                     .replace(">", "&gt;")
                     .replace('"', "&quot;")
                     .replace("'", "&apos;"))

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
            '  <!-- Node attributes -->',
            '  <key id="name" for="node" attr.name="name" attr.type="string"/>',
            '  <key id="type" for="node" attr.name="type" attr.type="string"/>',
            '  <key id="network" for="node" attr.name="network" attr.type="string"/>',
            '  <key id="online" for="node" attr.name="online" attr.type="boolean"/>',
            '  <key id="lat" for="node" attr.name="latitude" attr.type="double"/>',
            '  <key id="lon" for="node" attr.name="longitude" attr.type="double"/>',
            '  <!-- Edge attributes -->',
            '  <key id="hops" for="edge" attr.name="hops" attr.type="int"/>',
            '  <key id="snr" for="edge" attr.name="snr" attr.type="double"/>',
            '  <key id="rssi" for="edge" attr.name="rssi" attr.type="int"/>',
            '  <key id="active" for="edge" attr.name="active" attr.type="boolean"/>',
            '  <key id="weight" for="edge" attr.name="weight" attr.type="double"/>',
            '  <graph id="topology" edgedefault="directed">',
        ]

        # Add nodes
        for node_id, node in self._nodes.items():
            safe_id = xml_escape(node_id)
            lines.append(f'    <node id="{safe_id}">')
            lines.append(f'      <data key="name">{xml_escape(node.name or node_id)}</data>')
            lines.append(f'      <data key="type">{xml_escape(node.node_type)}</data>')
            lines.append(f'      <data key="network">{xml_escape(node.network)}</data>')
            lines.append(f'      <data key="online">{str(node.is_online).lower()}</data>')
            if node.latitude is not None:
                lines.append(f'      <data key="lat">{node.latitude}</data>')
            if node.longitude is not None:
                lines.append(f'      <data key="lon">{node.longitude}</data>')
            lines.append('    </node>')

        # Add edges
        for i, edge in enumerate(self._edges):
            src = xml_escape(edge.source)
            dst = xml_escape(edge.target)
            lines.append(f'    <edge id="e{i}" source="{src}" target="{dst}">')
            lines.append(f'      <data key="hops">{edge.hops}</data>')
            if edge.snr is not None:
                lines.append(f'      <data key="snr">{edge.snr}</data>')
            if edge.rssi is not None:
                lines.append(f'      <data key="rssi">{edge.rssi}</data>')
            lines.append(f'      <data key="active">{str(edge.is_active).lower()}</data>')
            lines.append(f'      <data key="weight">{edge.weight}</data>')
            lines.append('    </edge>')

        lines.append('  </graph>')
        lines.append('</graphml>')

        with open(output_path, 'w') as f:
            f.write('\n'.join(lines))

        logger.info(f"Exported GraphML ({len(self._nodes)} nodes, {len(self._edges)} edges) to {output_path}")
        return output_path, len(self._edges)

    def export_csv(self, output_dir: str = None) -> Tuple[str, str]:
        """
        Export topology as CSV files (nodes.csv, edges.csv).

        Args:
            output_dir: Output directory (default: cache dir)

        Returns:
            Tuple of (nodes_path, edges_path)
        """
        if output_dir is None:
            output_dir = str(get_real_user_home() / ".cache" / "meshforge")

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Export nodes
        nodes_path = str(Path(output_dir) / "topology_nodes.csv")
        with open(nodes_path, 'w') as f:
            f.write("id,name,type,network,online,hops,latitude,longitude,altitude,services\n")
            for node_id, node in self._nodes.items():
                services_str = ";".join(node.services)
                f.write(f'{node.id},"{node.name}",{node.node_type},{node.network},'
                        f'{node.is_online},{node.hops},{node.latitude or ""},'
                        f'{node.longitude or ""},{node.altitude or ""},"{services_str}"\n')

        # Export edges
        edges_path = str(Path(output_dir) / "topology_edges.csv")
        with open(edges_path, 'w') as f:
            f.write("source,target,hops,snr,rssi,active,bidirectional,weight,quality\n")
            for edge in self._edges:
                f.write(f'{edge.source},{edge.target},{edge.hops},'
                        f'{edge.snr or ""},{edge.rssi or ""},{edge.is_active},'
                        f'{edge.bidirectional},{edge.weight},{edge.get_quality_label()}\n')

        logger.info(f"Exported CSV files to {output_dir}")
        return nodes_path, edges_path
