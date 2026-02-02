"""
Traffic Inspector Mixin - Wireshark-Grade Traffic Visibility TUI.

Provides menu integration for the traffic inspector in the MeshForge launcher.

Features:
- Real-time packet capture view
- Packet filtering (Wireshark-style expressions)
- Packet detail inspection
- Path tracing visualization
- Traffic statistics dashboard
- Export capabilities
"""

import os
import subprocess
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

# Import traffic inspector components
try:
    from monitoring.traffic_inspector import (
        TrafficInspector,
        MeshPacket,
        PacketProtocol,
        DisplayFilter,
    )
    from monitoring.path_visualizer import PathVisualizer, TracedPath
    HAS_INSPECTOR = True
except ImportError:
    HAS_INSPECTOR = False
    TrafficInspector = None
    MeshPacket = None


class TrafficInspectorMixin:
    """
    Mixin providing traffic inspector functionality for the TUI launcher.

    Adds menus for:
    - Live traffic capture
    - Packet list with filtering
    - Packet detail view
    - Path visualization
    - Statistics dashboard
    """

    def _get_inspector(self) -> Optional['TrafficInspector']:
        """Get or create the traffic inspector instance."""
        if not HAS_INSPECTOR:
            return None

        if not hasattr(self, '_traffic_inspector'):
            self._traffic_inspector = TrafficInspector()

        return self._traffic_inspector

    def menu_traffic_inspector(self) -> None:
        """Traffic Inspector - Wireshark-grade mesh traffic visibility."""
        if not HAS_INSPECTOR:
            self.dialog.msgbox(
                "Traffic Inspector Not Available",
                "The traffic inspector module is not installed.\n\n"
                "Required: monitoring/traffic_inspector.py",
                height=8, width=50
            )
            return

        while True:
            choice = self.dialog.menu(
                "Traffic Inspector",
                "Wireshark-grade mesh traffic visibility",
                choices=[
                    ("1", "View Live Traffic      - Real-time packet stream"),
                    ("2", "Packet List            - Browse captured packets"),
                    ("3", "Apply Filter           - Wireshark-style filtering"),
                    ("4", "Packet Details         - Deep packet inspection"),
                    ("5", "Path Visualization     - Multi-hop path view"),
                    ("6", "Traffic Statistics     - Analyze traffic patterns"),
                    ("7", "Filter Reference       - Available filter fields"),
                    ("8", "Export Data            - Export captures/paths"),
                    ("9", "Clear Capture          - Clear captured data"),
                ],
                height=18, width=65
            )

            if not choice:
                return

            if choice == "1":
                self._traffic_live_view()
            elif choice == "2":
                self._traffic_packet_list()
            elif choice == "3":
                self._traffic_apply_filter()
            elif choice == "4":
                self._traffic_packet_detail()
            elif choice == "5":
                self._traffic_path_visualization()
            elif choice == "6":
                self._traffic_statistics()
            elif choice == "7":
                self._traffic_filter_reference()
            elif choice == "8":
                self._traffic_export()
            elif choice == "9":
                self._traffic_clear()

    def _traffic_live_view(self) -> None:
        """View live traffic stream."""
        inspector = self._get_inspector()
        if not inspector:
            return

        stats = inspector.get_capture_stats()

        info = [
            "Live Traffic View",
            "=" * 60,
            "",
            f"Packets Captured: {stats.get('packets_captured', 0)}",
            f"Meshtastic: {stats.get('packets_meshtastic', 0)}",
            f"RNS: {stats.get('packets_rns', 0)}",
            f"Bytes: {stats.get('bytes_captured', 0):,}",
            "",
            "=" * 60,
            "",
            "Note: For real-time traffic monitoring, the inspector",
            "needs to be connected to meshtasticd or RNS services.",
            "",
            "Recent packets:",
            "-" * 60,
        ]

        # Show recent packets
        packets = inspector.get_packets(limit=20)
        if packets:
            for pkt in packets[:15]:
                summary = pkt.get_summary()
                if len(summary) > 58:
                    summary = summary[:55] + "..."
                info.append(summary)
        else:
            info.append("No packets captured yet.")
            info.append("")
            info.append("Traffic will appear here once the bridge is active")
            info.append("or packets are captured from the mesh network.")

        self.dialog.msgbox(
            "Live Traffic",
            "\n".join(info),
            height=30, width=70
        )

    def _traffic_packet_list(self) -> None:
        """Browse captured packets."""
        inspector = self._get_inspector()
        if not inspector:
            return

        # Get filter if any
        filter_expr = getattr(self, '_traffic_filter', None)

        packets = inspector.get_packets(
            limit=100,
            filter=filter_expr
        )

        if not packets:
            self.dialog.msgbox(
                "No Packets",
                "No packets match the current filter.\n\n"
                f"Filter: {filter_expr or '(none)'}",
                height=8, width=50
            )
            return

        # Build menu choices
        choices = []
        for i, pkt in enumerate(packets[:50]):
            time_str = pkt.timestamp.strftime("%H:%M:%S")
            src = pkt.source[:10] if pkt.source else "?"
            port = pkt.port_name[:12] if pkt.port_name else pkt.protocol.value[:12]
            hops = f"h{pkt.hops_taken}" if pkt.hops_taken else ""
            label = f"{time_str} {src:<10} {port:<12} {hops}"
            choices.append((str(i), label))

        title = f"Captured Packets ({len(packets)} total)"
        if filter_expr:
            title += f"\nFilter: {filter_expr}"

        choice = self.dialog.menu(
            title,
            "Select a packet to view details",
            choices=choices,
            height=25, width=70
        )

        if choice:
            idx = int(choice)
            if idx < len(packets):
                self._show_packet_detail(packets[idx])

    def _traffic_apply_filter(self) -> None:
        """Apply a display filter."""
        inspector = self._get_inspector()
        if not inspector:
            return

        current = getattr(self, '_traffic_filter', '')

        # Show filter input
        result = self.dialog.inputbox(
            "Display Filter",
            "Enter a Wireshark-style filter expression:\n\n"
            "Examples:\n"
            "  mesh.hops > 2\n"
            "  mesh.from == \"!abc123\"\n"
            "  mesh.portnum == 1\n"
            "  mesh.snr >= -5\n\n"
            "Leave empty to clear filter.",
            init=current,
            height=16, width=60
        )

        if result is not None:
            self._traffic_filter = result if result else None

            # Test filter
            if result:
                test_filter = DisplayFilter(result)
                if not test_filter.compile():
                    self.dialog.msgbox(
                        "Filter Warning",
                        "Filter may not parse correctly.\n"
                        "Check syntax and field names.",
                        height=7, width=45
                    )
                else:
                    # Count matches
                    packets = inspector.get_packets(limit=1000, filter=result)
                    self.dialog.msgbox(
                        "Filter Applied",
                        f"Filter: {result}\n\n"
                        f"Matching packets: {len(packets)}",
                        height=8, width=50
                    )

    def _traffic_packet_detail(self) -> None:
        """Select and view packet details."""
        inspector = self._get_inspector()
        if not inspector:
            return

        # Get packet ID
        packet_id = self.dialog.inputbox(
            "Packet Detail",
            "Enter packet ID to inspect:\n\n"
            "(Use Packet List to find IDs)",
            height=10, width=50
        )

        if packet_id:
            packet = inspector.get_packet(packet_id)
            if packet:
                self._show_packet_detail(packet)
            else:
                self.dialog.msgbox(
                    "Not Found",
                    f"Packet {packet_id} not found in capture.",
                    height=6, width=40
                )

    def _show_packet_detail(self, packet: 'MeshPacket') -> None:
        """Display detailed packet information."""
        inspector = self._get_inspector()
        detail = inspector.format_packet_detail(packet)

        self.dialog.msgbox(
            f"Packet: {packet.id[:20]}",
            detail,
            height=30, width=75
        )

    def _traffic_path_visualization(self) -> None:
        """Multi-hop path visualization."""
        if not HAS_INSPECTOR:
            return

        from monitoring.path_visualizer import PathVisualizer

        while True:
            choice = self.dialog.menu(
                "Path Visualization",
                "Multi-hop message path tracing",
                choices=[
                    ("1", "View Recent Paths    - ASCII path display"),
                    ("2", "Generate HTML View   - Interactive browser view"),
                    ("3", "Trace Message Path   - Trace specific message"),
                    ("4", "Path Statistics      - Aggregate path metrics"),
                ],
                height=12, width=60
            )

            if not choice:
                return

            if choice == "1":
                self._path_ascii_view()
            elif choice == "2":
                self._path_html_view()
            elif choice == "3":
                self._path_trace_message()
            elif choice == "4":
                self._path_statistics()

    def _path_ascii_view(self) -> None:
        """Show ASCII path visualization."""
        try:
            from monitoring.path_visualizer import PathVisualizer
        except ImportError:
            self.dialog.msgbox("Error", "Path visualizer not available.", height=6, width=40)
            return

        inspector = self._get_inspector()
        if not inspector:
            return

        visualizer = PathVisualizer()

        # Get recent packets with path traces
        packets = inspector.get_packets(limit=50)
        for pkt in packets:
            hops = inspector.trace_path(pkt.id)
            if hops:
                visualizer.add_path_trace(pkt.id, hops)

        if not visualizer._paths:
            # Add sample path for demo
            self.dialog.msgbox(
                "No Path Data",
                "No path traces available yet.\n\n"
                "Path data is collected when messages are relayed\n"
                "through the mesh network.",
                height=10, width=50
            )
            return

        ascii_view = visualizer.generate_ascii()
        self.dialog.msgbox(
            "Path Visualization",
            ascii_view,
            height=30, width=82
        )

    def _path_html_view(self) -> None:
        """Generate and open HTML path visualization."""
        try:
            from monitoring.path_visualizer import PathVisualizer
        except ImportError:
            self.dialog.msgbox("Error", "Path visualizer not available.", height=6, width=40)
            return

        inspector = self._get_inspector()
        if not inspector:
            return

        visualizer = PathVisualizer()

        # Get recent packets
        packets = inspector.get_packets(limit=100)
        path_count = 0

        for pkt in packets:
            hops = inspector.trace_path(pkt.id)
            if hops:
                visualizer.add_path_trace(pkt.id, hops)
                path_count += 1

        if path_count == 0:
            # Create demo data if no real paths
            self.dialog.msgbox(
                "Generating Demo",
                "No real path data available.\n"
                "Generating visualization with sample data...",
                height=7, width=50
            )

            # Add demo nodes and path
            visualizer.add_node("local", "Local Node", "local")
            visualizer.add_node("relay1", "Relay-1", "relay")
            visualizer.add_node("relay2", "Relay-2", "relay")
            visualizer.add_node("dest", "Destination", "destination")

            from monitoring.traffic_inspector import HopInfo, HopState

            demo_hops = [
                HopInfo(0, "local", "Local Node", HopState.RECEIVED, snr=12.5, rssi=-85),
                HopInfo(1, "relay1", "Relay-1", HopState.RELAYED, snr=8.2, rssi=-92, latency_ms=150),
                HopInfo(2, "relay2", "Relay-2", HopState.RELAYED, snr=5.1, rssi=-98, latency_ms=180),
                HopInfo(3, "dest", "Destination", HopState.DELIVERED, snr=3.5, rssi=-105, latency_ms=200),
            ]
            visualizer.add_path_trace("demo_path", demo_hops)

        # Generate HTML
        output_path = visualizer.generate()

        # Detect SSH/headless environment
        is_ssh = bool(os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'))
        has_display = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))

        if is_ssh or not has_display:
            # SSH/headless - show path only, don't try browser
            self.dialog.msgbox(
                "Path Visualization Generated",
                f"HTML visualization saved to:\n{output_path}\n\n"
                "No graphical display detected.\n"
                "Copy this file to view in a browser.",
                height=12, width=60
            )
        else:
            self.dialog.msgbox(
                "Path Visualization Generated",
                f"HTML visualization saved to:\n{output_path}\n\n"
                "Opening in browser...",
                height=9, width=60
            )

            # Try to open in browser (only when display available)
            try:
                webbrowser.open(f"file://{output_path}")
            except Exception as e:
                self.dialog.msgbox(
                    "Browser Error",
                    f"Could not open browser:\n{e}\n\n"
                    f"Manually open: {output_path}",
                    height=10, width=55
                )

    def _path_trace_message(self) -> None:
        """Trace a specific message's path."""
        inspector = self._get_inspector()
        if not inspector:
            return

        packet_id = self.dialog.inputbox(
            "Trace Message Path",
            "Enter packet ID to trace:\n\n"
            "(Use Packet List to find IDs)",
            height=10, width=50
        )

        if packet_id:
            hops = inspector.trace_path(packet_id)
            if hops:
                from monitoring.path_visualizer import TracedPath

                path = TracedPath.from_hop_list(f"trace_{packet_id[:8]}", hops, packet_id)
                visualizer = PathVisualizer()
                visualizer.add_path(path)

                report = visualizer.format_path_report(path)
                self.dialog.msgbox(
                    f"Path Trace: {packet_id[:16]}",
                    report,
                    height=30, width=75
                )
            else:
                self.dialog.msgbox(
                    "No Path Data",
                    f"No path trace available for packet:\n{packet_id}",
                    height=7, width=50
                )

    def _path_statistics(self) -> None:
        """Show path statistics."""
        inspector = self._get_inspector()
        if not inspector:
            return

        from monitoring.path_visualizer import PathVisualizer

        visualizer = PathVisualizer()

        # Collect path data
        packets = inspector.get_packets(limit=200)
        for pkt in packets:
            hops = inspector.trace_path(pkt.id)
            if hops:
                visualizer.add_path_trace(pkt.id, hops)

        stats = visualizer.get_path_stats()

        if not stats or stats.get('total_paths', 0) == 0:
            self.dialog.msgbox(
                "No Statistics",
                "No path data available for statistics.",
                height=6, width=45
            )
            return

        info = [
            "Path Statistics",
            "=" * 50,
            "",
            f"Total Paths Traced: {stats.get('total_paths', 0)}",
            f"Success Rate:       {stats.get('success_rate', 0)*100:.1f}%",
            f"Average Hops:       {stats.get('avg_hops', 0):.1f}",
            f"Maximum Hops:       {stats.get('max_hops', 0)}",
            "",
            "Signal Quality:",
            f"  Average SNR:      {stats.get('avg_snr', 'N/A')}",
            f"  Minimum SNR:      {stats.get('min_snr', 'N/A')}",
            "",
            "Latency:",
            f"  Average:          {stats.get('avg_latency_ms', 'N/A')} ms",
            "",
            f"Unique Nodes:       {stats.get('unique_nodes', 0)}",
            "",
            "=" * 50,
        ]

        self.dialog.msgbox(
            "Path Statistics",
            "\n".join(info),
            height=24, width=55
        )

    def _traffic_statistics(self) -> None:
        """Show traffic statistics."""
        inspector = self._get_inspector()
        if not inspector:
            return

        stats = inspector.get_stats()
        output = inspector.format_stats(stats)

        self.dialog.msgbox(
            "Traffic Statistics",
            output,
            height=30, width=75
        )

    def _traffic_filter_reference(self) -> None:
        """Show available filter fields."""
        fields = DisplayFilter.get_available_fields() if HAS_INSPECTOR else {}

        lines = [
            "Available Filter Fields",
            "=" * 60,
            "",
            "Usage: field operator value",
            "Operators: ==, !=, >, >=, <, <=, contains, matches",
            "",
            "Examples:",
            '  mesh.hops > 2',
            '  mesh.from == "!abc123"',
            '  mesh.snr >= -5 and mesh.portnum == 1',
            '',
            "=" * 60,
            "",
        ]

        for abbrev, desc in sorted(fields.items()):
            lines.append(f"  {abbrev:<20} {desc}")

        self.dialog.msgbox(
            "Filter Reference",
            "\n".join(lines),
            height=30, width=70
        )

    def _traffic_export(self) -> None:
        """Export traffic data."""
        inspector = self._get_inspector()
        if not inspector:
            return

        choice = self.dialog.menu(
            "Export Data",
            "Choose export format",
            choices=[
                ("1", "JSON         - Full packet data"),
                ("2", "CSV          - Packet summary"),
                ("3", "Path HTML    - Path visualization"),
            ],
            height=11, width=50
        )

        if not choice:
            return

        try:
            from utils.paths import get_real_user_home
        except ImportError:
            from pathlib import Path
            get_real_user_home = Path.home

        export_dir = get_real_user_home() / ".cache" / "meshforge" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if choice == "1":
            # JSON export
            import json
            packets = inspector.get_packets(limit=1000)
            data = [p.to_dict() for p in packets]
            output_path = export_dir / f"traffic_{timestamp}.json"
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)

        elif choice == "2":
            # CSV export
            packets = inspector.get_packets(limit=1000)
            output_path = export_dir / f"traffic_{timestamp}.csv"
            with open(output_path, 'w') as f:
                f.write("id,timestamp,direction,protocol,source,destination,port,hops,snr,rssi,size\n")
                for p in packets:
                    f.write(f"{p.id},{p.timestamp.isoformat()},{p.direction.value},"
                            f"{p.protocol.value},{p.source},{p.destination},"
                            f"{p.port_name},{p.hops_taken},{p.snr or ''},{p.rssi or ''},{p.size}\n")

        elif choice == "3":
            # Path HTML export
            from monitoring.path_visualizer import PathVisualizer
            visualizer = PathVisualizer()
            packets = inspector.get_packets(limit=100)
            for pkt in packets:
                hops = inspector.trace_path(pkt.id)
                if hops:
                    visualizer.add_path_trace(pkt.id, hops)
            output_path = visualizer.generate(str(export_dir / f"paths_{timestamp}.html"))

        self.dialog.msgbox(
            "Export Complete",
            f"Data exported to:\n{output_path}",
            height=7, width=55
        )

    def _traffic_clear(self) -> None:
        """Clear captured traffic data."""
        inspector = self._get_inspector()
        if not inspector:
            return

        confirm = self.dialog.yesno(
            "Clear Capture Data",
            "This will delete all captured packets.\n\n"
            "Are you sure?",
            height=8, width=45
        )

        if confirm:
            count = inspector.clear()
            self._traffic_filter = None
            self.dialog.msgbox(
                "Cleared",
                f"Deleted {count} packets from capture.",
                height=6, width=40
            )
