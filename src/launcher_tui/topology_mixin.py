"""
Network Topology Mixin for MeshForge Launcher TUI.

Provides network topology visualization and analysis tools:
- View topology statistics
- Trace paths to destinations
- View recent topology events
- Generate browser-based visualization
- ASCII topology display
"""

import logging
import os
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Optional, Dict, Any, List
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Module-level safe imports
_get_network_topology, _HAS_NETWORK_TOPOLOGY = safe_import(
    'gateway.network_topology', 'get_network_topology'
)
_get_node_tracker_func, _HAS_NODE_TRACKER = safe_import(
    'gateway.node_tracker', 'get_node_tracker'
)
_TopologyVisualizer, _HAS_TOPOLOGY_VISUALIZER = safe_import(
    'utils.topology_visualizer', 'TopologyVisualizer'
)


class TopologyMixin:
    """Mixin providing network topology tools for the TUI launcher."""

    def _topology_menu(self):
        """Network topology analysis menu."""
        choices = [
            ("stats", "Topology Statistics"),
            ("nodes", "View Nodes"),
            ("edges", "View Links/Edges"),
            ("events", "Recent Topology Events"),
            ("trace", "Trace Path to Destination"),
            ("ascii", "ASCII Topology View"),
            ("browser", "Open in Browser (D3.js Graph)"),
            ("export", "Export Topology Data"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Network Topology",
                "Analyze mesh network topology:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "stats": ("Topology Statistics", self._show_topology_stats),
                "nodes": ("View Nodes", self._show_topology_nodes),
                "edges": ("View Links/Edges", self._show_topology_edges),
                "events": ("Topology Events", self._show_topology_events),
                "trace": ("Trace Path", self._trace_path),
                "ascii": ("ASCII Topology", self._show_ascii_topology),
                "browser": ("Browser Topology", self._open_topology_browser),
                "export": ("Export Topology", self._export_topology),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _get_topology(self):
        """Get the network topology instance."""
        if not _HAS_NETWORK_TOPOLOGY:
            return None
        return _get_network_topology()

    def _get_node_tracker(self):
        """Get the node tracker instance if available."""
        if not _HAS_NODE_TRACKER:
            return None
        return _get_node_tracker_func()

    def _show_topology_stats(self):
        """Display topology statistics."""
        # Prefer getting stats from node tracker (has richer data)
        tracker = self._get_node_tracker()
        topology = self._get_topology()

        if topology is None and tracker is None:
            self.dialog.msgbox(
                "Topology Unavailable",
                "Network topology module not loaded.\n\n"
                "The gateway service may need to be running."
            )
            return

        try:
            # Get topology stats from tracker if available, else direct
            if tracker and hasattr(tracker, 'get_topology_stats'):
                stats = tracker.get_topology_stats() or {}
            elif topology:
                stats = topology.get_topology_stats()
            else:
                stats = {}

            # Get node counts from tracker for richer data
            tracker_node_count = 0
            online_count = 0
            rns_count = 0
            mesh_count = 0
            if tracker and hasattr(tracker, 'get_all_nodes'):
                try:
                    all_nodes = tracker.get_all_nodes()
                    tracker_node_count = len(all_nodes)
                    for node in all_nodes:
                        if getattr(node, 'is_online', False):
                            online_count += 1
                        network = getattr(node, 'network', '')
                        if network == 'rns':
                            rns_count += 1
                        elif network == 'meshtastic':
                            mesh_count += 1
                        elif network == 'both':
                            rns_count += 1
                            mesh_count += 1
                except (AttributeError, TypeError) as e:
                    logger.debug("Node network count failed: %s", e)

            # Use the higher of topology nodes or tracker nodes
            topo_node_count = stats.get('node_count', 0)
            node_count = max(topo_node_count, tracker_node_count)

            # Format stats display
            lines = [
                "NETWORK TOPOLOGY STATISTICS",
                "=" * 40,
                "",
                f"Total Nodes:    {node_count}",
            ]

            # Show network breakdown if we have tracker data
            if tracker_node_count > 0:
                if online_count > 0:
                    lines.append(f"  Online:       {online_count}")
                if rns_count > 0:
                    lines.append(f"  RNS:          {rns_count}")
                if mesh_count > 0:
                    lines.append(f"  Meshtastic:   {mesh_count}")

            lines.extend([
                "",
                f"Total Edges:    {stats.get('edge_count', 0)}",
                f"Active Edges:   {stats.get('active_edges', 0)}",
                "",
                f"Average Hops:   {stats.get('avg_hops', 0):.2f}",
                f"Maximum Hops:   {stats.get('max_hops', 0)}",
                "",
            ])

            # Add service stats if available from node tracker
            if tracker and hasattr(tracker, 'get_service_stats'):
                try:
                    svc_stats = tracker.get_service_stats()
                    if svc_stats:
                        lines.append("SERVICE DISCOVERY")
                        lines.append("-" * 40)
                        for svc_name, count in sorted(svc_stats.items()):
                            lines.append(f"  {svc_name}: {count}")
                        lines.append("")
                except (AttributeError, TypeError) as e:
                    logger.debug("Service stats lookup failed: %s", e)

            # Show help if no data
            if node_count == 0 and stats.get('edge_count', 0) == 0:
                lines.append("No topology data available.")
                lines.append("")
                lines.append("Topology is populated when:")
                lines.append("  - RNS discovers paths to destinations")
                lines.append("  - Meshtastic nodes are seen")
                lines.append("  - Gateway bridge is running")
                lines.append("")

            self.dialog.msgbox("Topology Statistics", "\n".join(lines))

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get topology stats:\n{e}")

    def _show_topology_nodes(self):
        """Show list of nodes in the topology."""
        topology = self._get_topology()
        tracker = self._get_node_tracker()

        if topology is None and tracker is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        try:
            # Collect nodes from both topology and node tracker
            all_nodes = {}  # node_id -> (display_name, node_type)

            # Get nodes from topology (RNS path table)
            if topology:
                topo_dict = topology.to_dict()
                for node_id in topo_dict.get("nodes", []):
                    if node_id == "local":
                        all_nodes[node_id] = ("Local Node", "local")
                    elif node_id.startswith("rns_"):
                        all_nodes[node_id] = (node_id[:12], "RNS")
                    else:
                        all_nodes[node_id] = (node_id[:12], "Network")

            # Get nodes from node tracker (has richer Meshtastic data)
            if tracker and hasattr(tracker, 'get_all_nodes'):
                for node in tracker.get_all_nodes():
                    name = node.name or node.short_name or node.id
                    if node.network == "meshtastic":
                        node_type = "Mesh"
                    elif node.network == "rns":
                        node_type = "RNS"
                    elif node.network == "both":
                        node_type = "Bridge"
                    else:
                        node_type = "Node"
                    # Add online indicator
                    status = "+" if getattr(node, 'is_online', False) else "-"
                    all_nodes[node.id] = (name, f"{node_type}{status}")

            if not all_nodes:
                self.dialog.msgbox("No Nodes", "No nodes discovered yet.\n\nNodes appear when:\n- RNS discovers paths\n- Meshtastic nodes are seen\n- Gateway bridge is running")
                return

            # Build node list for menu (sort by name, limit to 50)
            node_choices = []
            sorted_nodes = sorted(all_nodes.items(), key=lambda x: x[1][0].lower())[:50]
            for node_id, (name, node_type) in sorted_nodes:
                # Truncate long names
                display_name = name[:25] if len(name) > 25 else name
                node_choices.append((node_id, f"{display_name} [{node_type}]"))

            node_choices.append(("back", "Back"))

            selected = self.dialog.menu(
                "Network Nodes",
                f"Found {len(all_nodes)} nodes:",
                node_choices
            )

            if selected and selected != "back":
                self._show_node_details(selected)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to list nodes:\n{e}")

    def _show_node_details(self, node_id: str):
        """Show details for a specific node including PKI and health metrics."""
        topology = self._get_topology()
        tracker = self._get_node_tracker()

        lines = [
            f"NODE: {node_id}",
            "=" * 50,
            "",
        ]

        # Try to get UnifiedNode data from tracker for extended info
        node_data = None
        if tracker:
            try:
                node_data = tracker.get_node(node_id)
            except (KeyError, AttributeError) as e:
                logger.debug("Node data lookup for %s failed: %s", node_id, e)

        # Display basic node info
        if node_data:
            lines.append("IDENTITY:")
            lines.append("-" * 50)
            if node_data.name:
                fav_star = " [*]" if getattr(node_data, 'is_favorite', False) else ""
                lines.append(f"  Name:     {node_data.name}{fav_star}")
            if node_data.short_name:
                lines.append(f"  Short:    {node_data.short_name}")
            if node_data.meshtastic_id:
                lines.append(f"  Mesh ID:  {node_data.meshtastic_id}")
            if node_data.hardware_model:
                lines.append(f"  Hardware: {node_data.hardware_model}")
            if node_data.role:
                lines.append(f"  Role:     {node_data.role}")
            if node_data.firmware_version:
                lines.append(f"  Firmware: {node_data.firmware_version}")
            lines.append(f"  Status:   {node_data.state_name} {node_data.state_icon}")
            lines.append("")

            # PKI Status (Meshtastic 2.5+)
            if node_data.pki_status and hasattr(node_data.pki_status, 'state'):
                from gateway.node_tracker import PKIKeyState
                pki = node_data.pki_status
                lines.append("PKI ENCRYPTION:")
                lines.append("-" * 50)
                state_icons = {
                    PKIKeyState.UNKNOWN: "[?]",
                    PKIKeyState.TRUSTED: "[OK]",
                    PKIKeyState.CHANGED: "[!!]",
                    PKIKeyState.VERIFIED: "[V]",
                    PKIKeyState.LEGACY: "[-]",
                }
                icon = state_icons.get(pki.state, "[?]")
                lines.append(f"  Status:      {pki.state.value.title()} {icon}")
                if pki.public_key_hex:
                    lines.append(f"  Fingerprint: {pki.key_fingerprint()}")
                    # Show abbreviated key
                    key_short = pki.public_key_hex[:16] + "..." if len(pki.public_key_hex) > 16 else pki.public_key_hex
                    lines.append(f"  Public Key:  {key_short}")
                if pki.first_seen:
                    lines.append(f"  First Seen:  {pki.first_seen.strftime('%Y-%m-%d %H:%M')}")
                if pki.state == PKIKeyState.CHANGED and pki.last_changed:
                    lines.append(f"  KEY CHANGED: {pki.last_changed.strftime('%Y-%m-%d %H:%M')} !!!")
                if pki.is_admin_trusted:
                    lines.append(f"  Admin Key:   Yes")
                lines.append("")

            # Health Metrics (Meshtastic 2.7+)
            if node_data.telemetry and node_data.telemetry.health:
                health = node_data.telemetry.health
                if health.heart_rate or health.spo2 or health.body_temperature:
                    lines.append("HEALTH METRICS:")
                    lines.append("-" * 50)
                    if health.heart_rate:
                        lines.append(f"  Heart Rate:  {health.heart_rate} BPM")
                    if health.spo2:
                        lines.append(f"  SpO2:        {health.spo2}%")
                    if health.body_temperature:
                        lines.append(f"  Body Temp:   {health.body_temperature:.1f}C")
                    if health.timestamp:
                        lines.append(f"  Updated:     {health.timestamp.strftime('%H:%M:%S')}")
                    lines.append("")

            # Device Telemetry
            if node_data.telemetry:
                telem = node_data.telemetry
                has_device = telem.battery_level is not None or telem.voltage is not None
                has_env = telem.temperature is not None or telem.humidity is not None
                if has_device or has_env:
                    lines.append("TELEMETRY:")
                    lines.append("-" * 50)
                    if telem.battery_level is not None:
                        lines.append(f"  Battery:    {telem.battery_level}%")
                    if telem.voltage is not None:
                        lines.append(f"  Voltage:    {telem.voltage:.2f}V")
                    if telem.channel_utilization is not None:
                        lines.append(f"  ChUtil:     {telem.channel_utilization:.1f}%")
                    if telem.air_util_tx is not None:
                        lines.append(f"  AirUtilTX:  {telem.air_util_tx:.1f}%")
                    if telem.temperature is not None:
                        lines.append(f"  Temp:       {telem.temperature:.1f}C")
                    if telem.humidity is not None:
                        lines.append(f"  Humidity:   {telem.humidity:.0f}%")
                    if telem.pressure is not None:
                        lines.append(f"  Pressure:   {telem.pressure:.0f} hPa")
                    lines.append("")

            # Signal Quality
            if node_data.snr is not None or node_data.rssi is not None:
                lines.append("SIGNAL QUALITY:")
                lines.append("-" * 50)
                if node_data.snr is not None:
                    trend = node_data.snr_trend if hasattr(node_data, 'snr_trend') else ""
                    trend_icon = {"improving": "^", "degrading": "v", "stable": "-"}.get(trend, "")
                    lines.append(f"  SNR:        {node_data.snr:.1f} dB {trend_icon}")
                if node_data.rssi is not None:
                    lines.append(f"  RSSI:       {node_data.rssi} dBm")
                if node_data.hops is not None:
                    lines.append(f"  Hops:       {node_data.hops}")
                lines.append("")

        # Get edges involving this node (topology connections)
        if topology:
            try:
                topo_dict = topology.to_dict()
                edges = topo_dict.get("edges", [])

                node_edges = [e for e in edges
                              if e.get("source_id") == node_id or e.get("dest_id") == node_id]

                if node_edges:
                    lines.append("CONNECTIONS:")
                    lines.append("-" * 50)

                    for edge in node_edges:
                        src = edge.get("source_id", "")[:15]
                        dst = edge.get("dest_id", "")[:15]
                        hops = edge.get("hops", 0)
                        active = "Active" if edge.get("is_active", False) else "Inactive"
                        snr = edge.get("snr")
                        snr_str = f"{snr:.1f}dB" if snr else "N/A"

                        lines.append(f"  {src} -> {dst}")
                        lines.append(f"    Hops: {hops} | SNR: {snr_str} | {active}")
                        lines.append("")
                elif not node_data:
                    lines.append("No direct connections found.")

            except Exception as e:
                lines.append(f"Error loading topology: {e}")

        self.dialog.msgbox("Node Details", "\n".join(lines))

    def _show_topology_edges(self):
        """Show list of edges/links in the topology."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        try:
            topo_dict = topology.to_dict()
            edges = topo_dict.get("edges", [])

            if not edges:
                self.dialog.msgbox("No Edges", "No links discovered in the topology yet.")
                return

            # Sort by announce count (most active first)
            edges_sorted = sorted(edges, key=lambda e: e.get("announce_count", 0), reverse=True)

            lines = [
                "NETWORK LINKS",
                "=" * 70,
                f"Total: {len(edges)} links",
                "",
                "Most Active Links:",
                "-" * 70,
            ]

            for i, edge in enumerate(edges_sorted[:20]):
                src = edge.get("source_id", "")[:12]
                dst = edge.get("dest_id", "")[:12]
                hops = edge.get("hops", 0)
                count = edge.get("announce_count", 0)
                active = "●" if edge.get("is_active", False) else "○"

                snr = edge.get("snr")
                if snr is not None:
                    if snr > 10:
                        quality = "Excellent"
                    elif snr > 5:
                        quality = "Good"
                    elif snr > 0:
                        quality = "Marginal"
                    elif snr > -5:
                        quality = "Poor"
                    else:
                        quality = "Bad"
                    snr_str = f"{snr:.1f}dB ({quality})"
                else:
                    snr_str = "N/A"

                lines.append(f"{active} {src} → {dst}")
                lines.append(f"    Hops: {hops} | Announces: {count} | SNR: {snr_str}")

            if len(edges) > 20:
                lines.append("")
                lines.append(f"... and {len(edges) - 20} more links")

            self.dialog.msgbox("Network Links", "\n".join(lines))

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to list edges:\n{e}")

    def _show_topology_events(self):
        """Show recent topology events."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        try:
            events = topology.get_recent_events(30)

            if not events:
                self.dialog.msgbox("No Events", "No topology events recorded yet.")
                return

            lines = [
                "RECENT TOPOLOGY EVENTS",
                "=" * 60,
                "",
            ]

            for event in reversed(events[-20:]):
                event_type = event.get("event_type", "UNKNOWN")
                timestamp = event.get("timestamp", "")[:19]  # Trim microseconds
                node_id = event.get("node_id", "")
                dest_hash = event.get("dest_hash", "")[:16] if event.get("dest_hash") else ""

                # Format based on event type
                if event_type == "PATH_DISCOVERED":
                    lines.append(f"[{timestamp}] + PATH: {dest_hash}")
                    if event.get("new_value") is not None:
                        lines.append(f"    Hops: {event.get('new_value')}")
                elif event_type == "PATH_LOST":
                    lines.append(f"[{timestamp}] - PATH: {dest_hash}")
                elif event_type == "HOP_COUNT_CHANGED":
                    old = event.get("old_value", "?")
                    new = event.get("new_value", "?")
                    lines.append(f"[{timestamp}] ~ HOPS: {dest_hash}")
                    lines.append(f"    {old} → {new} hops")
                elif event_type == "NODE_ADDED":
                    lines.append(f"[{timestamp}] + NODE: {node_id}")
                elif event_type == "NODE_REMOVED":
                    lines.append(f"[{timestamp}] - NODE: {node_id}")
                elif event_type == "EDGE_ADDED":
                    lines.append(f"[{timestamp}] + EDGE: {node_id}")
                elif event_type == "EDGE_UPDATED":
                    lines.append(f"[{timestamp}] ~ EDGE: {node_id}")
                else:
                    lines.append(f"[{timestamp}] {event_type}")

                lines.append("")

            self.dialog.msgbox("Topology Events", "\n".join(lines))

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get events:\n{e}")

    def _trace_path(self):
        """Trace path to a destination."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        # Get destination hash from user
        dest_input = self.dialog.inputbox(
            "Trace Path",
            "Enter destination hash (hex) or node ID:",
            ""
        )

        if not dest_input:
            return

        try:
            # Try to interpret as hex hash
            dest_hash = None
            if len(dest_input) >= 16:
                try:
                    # Clean hex string
                    clean_hex = dest_input.replace(" ", "").replace(":", "")
                    dest_hash = bytes.fromhex(clean_hex[:32])
                except ValueError:
                    pass

            if dest_hash:
                result = topology.trace_path(dest_hash)

                lines = [
                    "PATH TRACE RESULT",
                    "=" * 50,
                    "",
                    f"Destination: {result.get('dest_hash', 'N/A')[:32]}",
                    f"Node ID: {result.get('dest_id', 'N/A')}",
                    "",
                ]

                if result.get("found"):
                    lines.append("Status: PATH FOUND")
                    lines.append(f"Total Hops: {result.get('total_hops', 0)}")
                    lines.append("")
                    lines.append("Path:")
                    for i, node in enumerate(result.get("path", [])):
                        lines.append(f"  {i+1}. {node}")

                    details = result.get("details", {})
                    if details:
                        lines.append("")
                        lines.append("Details:")
                        if details.get("interface"):
                            lines.append(f"  Interface: {details['interface']}")
                        if details.get("last_seen"):
                            lines.append(f"  Last Seen: {details['last_seen']}")
                        if details.get("announce_count"):
                            lines.append(f"  Announces: {details['announce_count']}")
                else:
                    lines.append("Status: NO PATH FOUND")
                    lines.append("")
                    lines.append("The destination may be unreachable or")
                    lines.append("not yet discovered in the topology.")

                self.dialog.msgbox("Path Trace", "\n".join(lines))

            else:
                # Try as node ID
                topo_dict = topology.to_dict()
                nodes = topo_dict.get("nodes", [])

                if dest_input in nodes:
                    # Find path from local to this node
                    path = topology.find_path("local", dest_input)

                    if path:
                        lines = [
                            "PATH TRACE RESULT",
                            "=" * 50,
                            "",
                            f"Destination: {dest_input}",
                            "",
                            "Status: PATH FOUND",
                            f"Hops: {len(path) - 1}",
                            "",
                            "Path:",
                        ]
                        for i, node in enumerate(path):
                            lines.append(f"  {i+1}. {node}")

                        self.dialog.msgbox("Path Trace", "\n".join(lines))
                    else:
                        self.dialog.msgbox(
                            "No Path",
                            f"No path found to {dest_input}"
                        )
                else:
                    self.dialog.msgbox(
                        "Not Found",
                        f"Node '{dest_input}' not found in topology.\n\n"
                        "Try entering a valid destination hash (hex) or node ID."
                    )

        except Exception as e:
            self.dialog.msgbox("Error", f"Path trace failed:\n{e}")

    def _show_ascii_topology(self):
        """Show ASCII representation of topology."""
        if not _HAS_TOPOLOGY_VISUALIZER:
            self.dialog.msgbox(
                "Module Not Found",
                "Topology visualizer module not available."
            )
            return

        try:
            topology = self._get_topology()
            if topology is None:
                self.dialog.msgbox("Unavailable", "Topology module not loaded.")
                return

            visualizer = _TopologyVisualizer.from_topology(topology)
            ascii_output = visualizer.generate_ascii(max_width=70)

            self.dialog.msgbox("Network Topology (ASCII)", ascii_output)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to generate ASCII view:\n{e}")

    def _open_topology_browser(self):
        """Generate and open topology visualization in browser."""
        if not _HAS_TOPOLOGY_VISUALIZER:
            self.dialog.msgbox(
                "Module Not Found",
                "Topology visualizer module not available."
            )
            return

        self.dialog.infobox("Generating...", "Creating topology visualization...")

        try:
            topology = self._get_topology()
            tracker = self._get_node_tracker()

            if topology is None and tracker is None:
                self.dialog.msgbox("Unavailable", "Topology module not loaded.")
                return

            # Generate visualization - start with topology data
            if topology:
                visualizer = _TopologyVisualizer.from_topology(topology)
            else:
                visualizer = _TopologyVisualizer()
                visualizer.add_node("local", name="Local Node", node_type="local", network="rns")

            # Enrich with node tracker data (has richer Meshtastic node info)
            nodes_added = 0
            if tracker and hasattr(tracker, 'get_all_nodes'):
                all_nodes = tracker.get_all_nodes()
                for node in all_nodes:
                    try:
                        # Determine node type
                        if node.network == "rns":
                            node_type = "rns"
                        elif node.network == "meshtastic":
                            node_type = "meshtastic"
                        elif node.network == "both":
                            node_type = "both"
                        else:
                            node_type = "node"

                        # Check if it's a router/gateway (safely handle non-string role)
                        role_str = str(node.role or "").lower()
                        if "router" in role_str:
                            node_type = "router"
                        elif "gateway" in role_str:
                            node_type = "gateway"

                        # Add/update node with rich data
                        # Position is stored in node.position, not directly on node
                        lat = node.position.latitude if node.position and node.position.is_valid() else None
                        lon = node.position.longitude if node.position and node.position.is_valid() else None
                        alt = node.position.altitude if node.position else None

                        visualizer.add_node(
                            node_id=node.id,
                            name=node.name or node.short_name or node.id,
                            node_type=node_type,
                            network=node.network or "unknown",
                            is_online=getattr(node, 'is_online', False),
                            hops=node.hops or 0,
                            latitude=lat,
                            longitude=lon,
                            altitude=alt,
                            metadata={
                                "hardware": node.hardware_model,
                                "firmware": node.firmware_version,
                                "role": node.role,
                                "snr": node.snr,
                                "rssi": node.rssi,
                            }
                        )

                        # Add edge from local to this node if not already present
                        if node.id != "local":
                            visualizer.add_edge(
                                source="local",
                                target=node.id,
                                hops=node.hops or 1,
                                snr=node.snr,
                                rssi=node.rssi,
                                is_active=getattr(node, 'is_online', False),
                            )
                        nodes_added += 1
                    except Exception as e:
                        # Log but continue processing other nodes
                        logger.debug(f"Error adding node {getattr(node, 'id', 'unknown')}: {e}")
                        continue

                logger.info(f"Added {nodes_added}/{len(all_nodes)} nodes to topology visualizer")
            output_path = visualizer.generate()

            # Detect SSH/headless environment
            is_ssh = bool(os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'))
            has_display = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))

            if is_ssh or not has_display:
                # SSH/headless - offer text browser or show path
                choices = [
                    ("lynx", "Open with lynx (text browser)"),
                    ("path", "Show file path only"),
                    ("back", "Back"),
                ]
                choice = self.dialog.menu(
                    "Topology Generated",
                    f"File: {output_path}\n\nNo graphical display detected.\nHow would you like to view it?",
                    choices
                )
                if choice == "lynx":
                    # Open with lynx in foreground
                    clear_screen()
                    subprocess.run(['lynx', output_path], timeout=300)
                elif choice == "path":
                    self.dialog.msgbox(
                        "Topology File",
                        f"Visualization saved to:\n\n{output_path}\n\n"
                        f"You can open this file in any browser,\n"
                        f"or copy to a machine with a GUI."
                    )
                return

            # Has display - try to open in browser (in background thread)
            # Drop privileges when running as root so browser runs as real user
            def open_browser():
                try:
                    real_user = os.environ.get('SUDO_USER')
                    if os.geteuid() == 0 and real_user:
                        # Running as root via sudo - run browser as real user
                        result = subprocess.run(
                            ['sudo', '-u', real_user, 'xdg-open', output_path],
                            capture_output=True,
                            timeout=10
                        )
                    else:
                        # Not root - try xdg-open directly
                        result = subprocess.run(
                            ["xdg-open", output_path],
                            capture_output=True,
                            timeout=10
                        )
                    if result.returncode != 0:
                        # Fallback to webbrowser module
                        webbrowser.open(Path(output_path).as_uri())
                except (subprocess.SubprocessError, OSError) as e:
                    logger.debug("xdg-open failed, trying webbrowser: %s", e)
                    try:
                        webbrowser.open(Path(output_path).as_uri())
                    except OSError as e2:
                        logger.debug("webbrowser fallback also failed: %s", e2)

            threading.Thread(target=open_browser, daemon=True).start()

            self.dialog.msgbox(
                "Topology Visualization",
                f"Visualization generated and opened in browser.\n\n"
                f"File: {output_path}\n\n"
                f"If the browser didn't open automatically,\n"
                f"you can open this file manually."
            )

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to generate visualization:\n{e}")

    def _export_topology(self):
        """Export topology data to file."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        # Ask for export format
        format_choices = [
            ("geojson", "GeoJSON (for mapping tools)"),
            ("d3", "D3.js JSON (for web visualization)"),
            ("graphml", "GraphML (for Gephi, etc.)"),
            ("csv", "CSV (nodes + edges)"),
            ("json", "JSON (full topology data)"),
            ("back", "Back"),
        ]

        export_format = self.dialog.menu(
            "Export Format",
            "Select export format:",
            format_choices
        )

        if not export_format or export_format == "back":
            return

        if not _HAS_TOPOLOGY_VISUALIZER:
            self.dialog.msgbox("Error", "Topology visualizer module not available.")
            return

        try:
            # Create visualizer from topology
            visualizer = _TopologyVisualizer.from_topology(topology)

            if export_format == "geojson":
                output_path, count = visualizer.export_geojson()
                self.dialog.msgbox(
                    "GeoJSON Export",
                    f"Exported {count} features.\n\n"
                    f"File: {output_path}\n\n"
                    "Note: Only nodes with GPS positions are included."
                )

            elif export_format == "d3":
                output_path, count = visualizer.export_d3_json()
                self.dialog.msgbox(
                    "D3.js Export",
                    f"Exported {count} nodes + links.\n\n"
                    f"File: {output_path}\n\n"
                    "Use with D3.js force-directed graph."
                )

            elif export_format == "graphml":
                output_path, count = visualizer.export_graphml()
                self.dialog.msgbox(
                    "GraphML Export",
                    f"Exported {count} edges.\n\n"
                    f"File: {output_path}\n\n"
                    "Open in Gephi, yEd, or similar tools."
                )

            elif export_format == "csv":
                nodes_path, edges_path = visualizer.export_csv()
                self.dialog.msgbox(
                    "CSV Export",
                    f"Exported CSV files:\n\n"
                    f"Nodes: {nodes_path}\n"
                    f"Edges: {edges_path}"
                )

            elif export_format == "json":
                import json
                from utils.paths import get_real_user_home
                export_dir = get_real_user_home() / ".cache" / "meshforge"
                export_dir.mkdir(parents=True, exist_ok=True)

                topo_dict = topology.to_dict()
                output_path = export_dir / "topology_export.json"
                with open(output_path, 'w') as f:
                    json.dump(topo_dict, f, indent=2, default=str)

                self.dialog.msgbox(
                    "JSON Export",
                    f"Exported full topology data.\n\n"
                    f"File: {output_path}"
                )

        except Exception as e:
            self.dialog.msgbox("Error", f"Export failed:\n{e}")

    def _export_data_menu(self):
        """Export data in various formats (GeoJSON, CSV, GraphML, D3.js)."""
        while True:
            choices = [
                ("geojson", "GeoJSON             For mapping tools"),
                ("csv", "CSV                 Spreadsheet format"),
                ("graphml", "GraphML             For graph analysis"),
                ("d3", "D3.js JSON          For web visualization"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Export Data",
                "Export network data:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "geojson": ("GeoJSON Export", lambda: self._export_topology_data("geojson")),
                "csv": ("CSV Export", lambda: self._export_topology_data("csv")),
                "graphml": ("GraphML Export", lambda: self._export_topology_data("graphml")),
                "d3": ("D3.js Export", lambda: self._export_topology_data("d3")),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _export_topology_data(self, format_type: str):
        """Export topology data in specified format."""
        try:
            topology = self._get_topology()
            if topology is None:
                self.dialog.msgbox(
                    "Export Unavailable",
                    "Network topology not loaded.\n\n"
                    "The gateway service may need to be running."
                )
                return

            viz = _TopologyVisualizer.from_topology(topology)

            if format_type == "geojson":
                path, count = viz.export_geojson()
                self.dialog.msgbox(
                    "GeoJSON Export",
                    f"Exported {count} features.\n\nFile: {path}"
                )
            elif format_type == "csv":
                nodes_path, edges_path = viz.export_csv()
                self.dialog.msgbox(
                    "CSV Export",
                    f"Exported CSV files:\n\nNodes: {nodes_path}\nEdges: {edges_path}"
                )
            elif format_type == "graphml":
                path, count = viz.export_graphml()
                self.dialog.msgbox(
                    "GraphML Export",
                    f"Exported {count} edges.\n\nFile: {path}"
                )
            elif format_type == "d3":
                path, count = viz.export_d3_json()
                self.dialog.msgbox(
                    "D3.js Export",
                    f"Exported {count} nodes + links.\n\nFile: {path}"
                )
        except Exception as e:
            self.dialog.msgbox("Export Failed", f"Error: {e}")
