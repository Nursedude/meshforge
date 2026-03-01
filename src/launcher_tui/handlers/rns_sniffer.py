"""
RNS Sniffer Handler — Wireshark-grade RNS traffic inspection.

Converted from rns_sniffer_mixin.py as part of the mixin-to-registry migration.
"""

import re

from handler_protocol import BaseHandler
from backend import clear_screen
from utils.safe_import import safe_import

# Hoist RNS sniffer imports to module level
_get_rns_sniffer, _start_rns_capture, _stop_rns_capture, _RNSPacketType, _integrate_with_traffic_inspector, _HAS_RNS_SNIFFER = safe_import(
    'monitoring.rns_sniffer',
    'get_rns_sniffer', 'start_rns_capture', 'stop_rns_capture',
    'RNSPacketType', 'integrate_with_traffic_inspector'
)


class RNSSnifferHandler(BaseHandler):
    """TUI handler for RNS traffic sniffer functionality."""

    handler_id = "rns_sniffer"
    menu_section = "rns"

    def menu_items(self):
        return [
            ("sniffer", "RNS Traffic Sniffer (Wireshark-grade)", None),
        ]

    def execute(self, action):
        if action == "sniffer":
            self._rns_traffic_sniffer()

    def _rns_traffic_sniffer(self):
        """RNS Traffic Sniffer - Wireshark-grade packet capture for RNS."""
        if not _HAS_RNS_SNIFFER:
            self.ctx.dialog.msgbox(
                "RNS Sniffer Not Available",
                "The RNS traffic sniffer module is not installed.\n\n"
                "Required: monitoring/rns_sniffer.py",
                height=8, width=50
            )
            return

        while True:
            sniffer = _get_rns_sniffer()
            capturing = sniffer._running if sniffer else False
            stats = sniffer.get_stats() if sniffer else {}

            capture_status = "CAPTURING" if capturing else "STOPPED"
            capture_action = "Stop Capture" if capturing else "Start Capture"

            packets = stats.get("packets_captured", 0)
            announces = stats.get("announces_seen", 0)
            paths = stats.get("paths_discovered", 0)

            choice = self.ctx.dialog.menu(
                "RNS Traffic Sniffer",
                f"Wireshark-grade RNS packet visibility\n"
                f"Status: {capture_status} | Packets: {packets} | "
                f"Announces: {announces} | Paths: {paths}",
                choices=[
                    ("capture", f"{capture_action}        - {'Stop' if capturing else 'Start'} RNS capture"),
                    ("1", "View Live Traffic      - Recent RNS packets"),
                    ("2", "View Path Table        - Discovered routes"),
                    ("3", "View Announces         - Node discoveries"),
                    ("4", "Filter by Destination  - Search by hash"),
                    ("5", "Probe Destination      - Request path + capture"),
                    ("6", "View Links             - Active RNS links"),
                    ("7", "Traffic Statistics     - Packet stats"),
                    ("8", "Test Known Node        - Test 17a4dcfd..."),
                    ("0", "Clear Capture          - Clear captured data"),
                ],
                height=20, width=70
            )

            if not choice:
                return

            dispatch = {
                "capture": ("Toggle Capture", self._rns_sniffer_toggle_capture, sniffer, capturing),
                "1": ("Live Traffic", self._rns_sniffer_live_traffic, sniffer),
                "2": ("Path Table", self._rns_sniffer_path_table, sniffer),
                "3": ("Announces", self._rns_sniffer_announces, sniffer),
                "4": ("Filter by Destination", self._rns_sniffer_filter_destination, sniffer),
                "5": ("Probe Destination", self._rns_sniffer_probe_destination, sniffer),
                "6": ("View Links", self._rns_sniffer_links, sniffer),
                "7": ("Traffic Statistics", self._rns_sniffer_statistics, sniffer),
                "8": ("Test Known Node", self._rns_sniffer_test_known_node, sniffer),
                "0": ("Clear Capture", self._rns_sniffer_clear, sniffer),
            }
            entry = dispatch.get(choice)
            if entry:
                name, method, *args = entry
                self.ctx.safe_call(name, method, *args)

    def _rns_sniffer_toggle_capture(self, sniffer, capturing):
        """Toggle RNS packet capture."""
        if capturing:
            _stop_rns_capture()
            self.ctx.dialog.msgbox(
                "Capture Stopped",
                "RNS packet capture has been stopped.\n\n"
                "Captured packets are preserved.",
                height=8, width=45
            )
        else:
            if _start_rns_capture():
                self.ctx.dialog.msgbox(
                    "Capture Started",
                    "RNS packet capture is now active.\n\n"
                    "Listening for RNS announces, links, and packets.\n"
                    "Packets will appear in Live Traffic view.",
                    height=10, width=50
                )
            else:
                self.ctx.dialog.msgbox(
                    "Capture Started (No RNS)",
                    "Capture mode enabled but RNS not detected.\n\n"
                    "Once rnsd or the gateway bridge starts,\n"
                    "packets will be captured automatically.",
                    height=10, width=50
                )

    def _rns_sniffer_live_traffic(self, sniffer):
        """View live RNS traffic."""
        if not sniffer:
            return

        stats = sniffer.get_stats()
        packets = sniffer.get_packets(limit=30)

        lines = [
            "RNS Live Traffic",
            "=" * 70,
            "",
            f"Capture: {'ACTIVE' if sniffer._running else 'STOPPED'}",
            f"Packets: {stats.get('packets_captured', 0)} | "
            f"Announces: {stats.get('announces_seen', 0)} | "
            f"Paths: {stats.get('paths_discovered', 0)}",
            "",
            "Recent Packets:",
            "-" * 70,
        ]

        if packets:
            for pkt in packets[:20]:
                summary = pkt.get_summary()
                if len(summary) > 68:
                    summary = summary[:65] + "..."
                lines.append(summary)
        else:
            lines.append("No packets captured yet.")
            if not sniffer._running:
                lines.append("")
                lines.append("Use 'Start Capture' to begin capturing RNS traffic.")

        self.ctx.dialog.msgbox(
            "RNS Live Traffic",
            "\n".join(lines),
            height=30, width=75
        )

    def _rns_sniffer_path_table(self, sniffer):
        """View discovered RNS paths."""
        if not sniffer:
            return

        paths = sniffer.get_path_table()

        lines = [
            "RNS Path Table",
            "=" * 70,
            "",
            f"Discovered Paths: {len(paths)}",
            "",
            f"{'Destination Hash':<34} {'Hops':<6} {'Announces':<10} {'Last Seen':<20}",
            "-" * 70,
        ]

        if paths:
            for path in sorted(paths, key=lambda p: p.last_seen, reverse=True)[:25]:
                dest = path.destination_hash.hex()[:32]
                hops = str(path.hops)
                ann = str(path.announce_count)
                last = path.last_seen.strftime("%H:%M:%S")
                lines.append(f"{dest:<34} {hops:<6} {ann:<10} {last:<20}")
        else:
            lines.append("No paths discovered yet.")
            lines.append("")
            lines.append("Paths are discovered when RNS announces are received.")

        self.ctx.dialog.msgbox(
            "RNS Path Table",
            "\n".join(lines),
            height=32, width=75
        )

    def _rns_sniffer_announces(self, sniffer):
        """View RNS announce packets."""
        if not sniffer:
            return

        packets = sniffer.get_packets(
            limit=50,
            packet_type=_RNSPacketType.ANNOUNCE
        )

        lines = [
            "RNS Announces",
            "=" * 70,
            "",
            f"Announce Packets: {len(packets)}",
            "",
            f"{'Time':<10} {'Destination':<34} {'Aspect':<20} {'Hops':<6}",
            "-" * 70,
        ]

        if packets:
            for pkt in packets[:25]:
                time_str = pkt.timestamp.strftime("%H:%M:%S")
                dest = pkt.destination_hash.hex()[:32] if pkt.destination_hash else "?"
                aspect = pkt.announce_aspect[:18] if pkt.announce_aspect else "?"
                hops = str(pkt.hops)
                lines.append(f"{time_str:<10} {dest:<34} {aspect:<20} {hops:<6}")
        else:
            lines.append("No announces captured yet.")
            lines.append("")
            lines.append("Enable capture and wait for nodes to announce.")

        self.ctx.dialog.msgbox(
            "RNS Announces",
            "\n".join(lines),
            height=32, width=75
        )

    def _rns_sniffer_filter_destination(self, sniffer):
        """Filter packets by destination hash."""
        if not sniffer:
            return

        dest = self.ctx.dialog.inputbox(
            "Filter by Destination",
            "Enter destination hash prefix (hex):\n\n"
            "Examples:\n"
            "  17a4dcfd  (first 8 chars)\n"
            "  17a4dcfd433f57c7  (16 chars)\n\n"
            "Leave empty to see all packets.",
            height=14, width=55
        )

        if dest is None:
            return

        packets = sniffer.get_packets(
            limit=50,
            destination=dest if dest else None
        )

        lines = [
            f"RNS Packets" + (f" (dest: {dest})" if dest else ""),
            "=" * 70,
            "",
            f"Matching Packets: {len(packets)}",
            "",
            "-" * 70,
        ]

        if packets:
            for pkt in packets[:20]:
                summary = pkt.get_summary()
                lines.append(summary[:68])
        else:
            lines.append("No packets match the filter.")

        self.ctx.dialog.msgbox(
            "Filtered Packets",
            "\n".join(lines),
            height=28, width=75
        )

    def _rns_sniffer_probe_destination(self, sniffer):
        """Probe a destination and capture the traffic."""
        if not sniffer:
            return

        dest = self.ctx.dialog.inputbox(
            "Probe Destination",
            "Enter destination hash to probe (hex):\n\n"
            "This will:\n"
            "1. Request path to destination\n"
            "2. Capture any response packets\n\n"
            "Example: 17a4dcfd433f57c7ec445d103a65e7a3",
            height=14, width=60
        )

        if not dest:
            return

        # Validate hex
        if not re.match(r'^[0-9a-fA-F]+$', dest):
            self.ctx.dialog.msgbox(
                "Invalid Hash",
                "Hash must contain only hex characters (0-9, a-f).",
                height=6, width=45
            )
            return

        # Start capture if not running
        if not sniffer._running:
            _start_rns_capture()

        # Probe
        success = sniffer.probe_destination(dest)

        if success:
            self.ctx.dialog.msgbox(
                "Probe Sent",
                f"Path request sent for:\n{dest}\n\n"
                "Check Live Traffic for responses.\n"
                "Use 'rnpath -t' to see if path was discovered.",
                height=11, width=60
            )
        else:
            self.ctx.dialog.msgbox(
                "Probe Failed",
                "Could not send path request.\n\n"
                "RNS may not be available.",
                height=8, width=45
            )

    def _rns_sniffer_links(self, sniffer):
        """View active RNS links."""
        if not sniffer:
            return

        links = sniffer.get_links()

        lines = [
            "RNS Links",
            "=" * 70,
            "",
            f"Tracked Links: {len(links)}",
            "",
            f"{'Link ID':<18} {'Destination':<18} {'State':<12} {'RTT':<10}",
            "-" * 70,
        ]

        if links:
            for link in links:
                link_id = link.link_id.hex()[:16] if link.link_id else "?"
                dest = link.destination_hash.hex()[:16] if link.destination_hash else "?"
                state = link.state.value[:10]
                rtt = f"{link.rtt_ms:.1f}ms" if link.rtt_ms else "-"
                lines.append(f"{link_id:<18} {dest:<18} {state:<12} {rtt:<10}")
        else:
            lines.append("No links tracked yet.")
            lines.append("")
            lines.append("Links appear when RNS connections are established.")

        self.ctx.dialog.msgbox(
            "RNS Links",
            "\n".join(lines),
            height=24, width=75
        )

    def _rns_sniffer_statistics(self, sniffer):
        """View RNS traffic statistics."""
        if not sniffer:
            return

        stats = sniffer.get_stats()

        lines = [
            "RNS Traffic Statistics",
            "=" * 50,
            "",
            f"Capture Status:    {'ACTIVE' if sniffer._running else 'STOPPED'}",
            f"Start Time:        {stats.get('start_time', 'N/A')}",
            "",
            "Packet Counts:",
            f"  Total Captured:  {stats.get('packets_captured', 0):,}",
            f"  Announces:       {stats.get('announces_seen', 0):,}",
            f"  Bytes Captured:  {stats.get('bytes_captured', 0):,}",
            "",
            "Network Discovery:",
            f"  Paths Discovered: {stats.get('paths_discovered', 0)}",
            f"  Current Paths:    {stats.get('path_count', 0)}",
            f"  Links Tracked:    {stats.get('link_count', 0)}",
            f"  Active Links:     {stats.get('active_links', 0)}",
            "",
            "Links Established: {stats.get('links_established', 0)}",
        ]

        self.ctx.dialog.msgbox(
            "RNS Statistics",
            "\n".join(lines),
            height=24, width=55
        )

    def _rns_sniffer_test_known_node(self, sniffer):
        """Test connectivity to the known working RNS node."""
        if not sniffer:
            return

        # Known working node from session notes
        identity_hash = "17a4dcfd433f57c7ec445d103a65e7a3"
        lxmf_address = "02ddf7b650daa8b73132badb18a8ce84"

        choice = self.ctx.dialog.menu(
            "Test Known RNS Node",
            f"Working RNS node for testing:\n"
            f"Identity: {identity_hash}\n"
            f"LXMF:     {lxmf_address}",
            choices=[
                ("1", "Probe Identity Hash"),
                ("2", "Probe LXMF Address"),
                ("3", "Filter Packets by Identity"),
                ("4", "Run rnprobe CLI"),
            ],
            height=15, width=60
        )

        if not choice:
            return

        # Start capture if not running
        if not sniffer._running:
            _start_rns_capture()

        if choice == "1":
            success = sniffer.probe_destination(identity_hash)
            msg = "Path request sent" if success else "Failed to send"
            self.ctx.dialog.msgbox("Probe Result", f"{msg} for:\n{identity_hash}", height=8, width=55)

        elif choice == "2":
            success = sniffer.probe_destination(lxmf_address)
            msg = "Path request sent" if success else "Failed to send"
            self.ctx.dialog.msgbox("Probe Result", f"{msg} for:\n{lxmf_address}", height=8, width=55)

        elif choice == "3":
            packets = sniffer.get_packets(limit=50, destination=identity_hash[:8])
            lines = [f"Packets for {identity_hash[:16]}...", "=" * 50, ""]
            if packets:
                for pkt in packets[:15]:
                    lines.append(pkt.get_summary()[:48])
            else:
                lines.append("No packets found for this destination.")
                lines.append("Try probing the node first.")
            self.ctx.dialog.msgbox("Filtered Packets", "\n".join(lines), height=22, width=55)

        elif choice == "4":
            # Cross-handler call to diagnostics handler for _run_rns_tool
            diag = self.ctx.registry.get_handler("rns_diagnostics") if self.ctx.registry else None
            if diag:
                clear_screen()
                print(f"=== Probing {identity_hash} ===\n")
                diag._run_rns_tool(['rnprobe', identity_hash], 'rnprobe')
                self.ctx.wait_for_enter()
            else:
                self.ctx.dialog.msgbox(
                    "Not Available",
                    "RNS diagnostics handler not loaded.\n"
                    "Try: rnprobe " + identity_hash,
                    height=6, width=50
                )

    def _rns_sniffer_clear(self, sniffer):
        """Clear captured RNS packets."""
        if not sniffer:
            return

        confirm = self.ctx.dialog.yesno(
            "Clear Capture",
            "Clear all captured RNS packets?\n\n"
            "This cannot be undone.",
            height=8, width=40
        )

        if confirm:
            count = sniffer.clear()
            self.ctx.dialog.msgbox(
                "Cleared",
                f"Cleared {count} captured packets.",
                height=6, width=35
            )
