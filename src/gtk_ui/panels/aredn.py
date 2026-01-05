"""
AREDN Panel - Amateur Radio Emergency Data Network Integration

Provides:
- Node discovery and inventory
- Network topology visualization
- Link quality monitoring
- Service browser
- MikroTik router setup wizard

Reference: https://docs.arednmesh.org/en/latest/
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Pango
import threading
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Import AREDN utilities
try:
    from utils.aredn import (
        AREDNClient, AREDNScanner, AREDNNode, AREDNLink,
        MikroTikAREDN, LinkType
    )
    HAS_AREDN = True
except ImportError:
    HAS_AREDN = False
    logger.warning("AREDN utilities not available")


class AREDNPanel(Gtk.Box):
    """Panel for AREDN mesh network integration"""

    SETTINGS_FILE = Path.home() / ".config" / "meshforge" / "aredn.json"

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._settings = self._load_settings()
        self._nodes: dict[str, AREDNNode] = {}
        self._scanner = None
        self._scanning = False

        self._build_ui()

    def _load_settings(self) -> dict:
        """Load AREDN settings"""
        defaults = {
            "scan_subnet": "10.0.0.0/24",
            "timeout": 3,
            "auto_refresh": False,
            "refresh_interval": 60,
            "known_nodes": [],
        }
        try:
            if self.SETTINGS_FILE.exists():
                with open(self.SETTINGS_FILE) as f:
                    saved = json.load(f)
                    defaults.update(saved)
        except Exception as e:
            logger.error(f"Error loading AREDN settings: {e}")
        return defaults

    def _save_settings(self):
        """Save AREDN settings"""
        try:
            self.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.SETTINGS_FILE, 'w') as f:
                json.dump(self._settings, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving AREDN settings: {e}")

    def _build_ui(self):
        """Build the AREDN panel UI"""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        title = Gtk.Label(label="AREDN Mesh Network")
        title.add_css_class("title-1")
        title.set_xalign(0)
        header_box.append(title)

        # Status indicator
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_hexpand(True)
        self.status_label.set_xalign(1)
        header_box.append(self.status_label)

        self.append(header_box)

        subtitle = Gtk.Label(label="Amateur Radio Emergency Data Network - Ham mesh networking")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Scrollable content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(400)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_box.set_margin_top(10)

        # Scanner section
        self._build_scanner_section(content_box)

        # Nodes list section
        self._build_nodes_section(content_box)

        # MikroTik setup section
        self._build_mikrotik_section(content_box)

        # Links section
        self._build_links_section(content_box)

        scrolled.set_child(content_box)
        self.append(scrolled)

    def _build_scanner_section(self, parent):
        """Build the node scanner section"""
        frame = Gtk.Frame()
        frame.set_label("Node Discovery")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Subnet entry row
        subnet_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        subnet_row.append(Gtk.Label(label="Subnet:"))

        self.subnet_entry = Gtk.Entry()
        self.subnet_entry.set_text(self._settings.get("scan_subnet", "10.0.0.0/24"))
        self.subnet_entry.set_placeholder_text("10.0.0.0/24")
        self.subnet_entry.set_tooltip_text("AREDN subnet to scan (CIDR notation)")
        self.subnet_entry.set_hexpand(True)
        subnet_row.append(self.subnet_entry)

        # Timeout spinner
        subnet_row.append(Gtk.Label(label="Timeout:"))
        self.timeout_spin = Gtk.SpinButton()
        self.timeout_spin.set_range(1, 30)
        self.timeout_spin.set_value(self._settings.get("timeout", 3))
        self.timeout_spin.set_increments(1, 5)
        self.timeout_spin.set_tooltip_text("Connection timeout in seconds")
        subnet_row.append(self.timeout_spin)

        box.append(subnet_row)

        # Scan buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        self.scan_btn = Gtk.Button(label="Scan Network")
        self.scan_btn.set_tooltip_text("Scan subnet for AREDN nodes")
        self.scan_btn.add_css_class("suggested-action")
        self.scan_btn.connect("clicked", self._on_scan)
        btn_row.append(self.scan_btn)

        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.set_tooltip_text("Stop ongoing scan")
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", self._on_stop_scan)
        btn_row.append(self.stop_btn)

        add_btn = Gtk.Button(label="Add Node")
        add_btn.set_tooltip_text("Manually add a node by hostname or IP")
        add_btn.connect("clicked", self._on_add_node)
        btn_row.append(add_btn)

        # Progress
        self.scan_progress = Gtk.ProgressBar()
        self.scan_progress.set_hexpand(True)
        self.scan_progress.set_show_text(True)
        self.scan_progress.set_visible(False)
        btn_row.append(self.scan_progress)

        box.append(btn_row)

        frame.set_child(box)
        parent.append(frame)

    def _build_nodes_section(self, parent):
        """Build the discovered nodes list section"""
        frame = Gtk.Frame()
        frame.set_label("Discovered Nodes")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Column headers
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header_row.add_css_class("dim-label")

        for label, width in [("Node", 150), ("Model", 120), ("Firmware", 80),
                              ("Links", 50), ("Status", 80)]:
            lbl = Gtk.Label(label=label)
            lbl.set_xalign(0)
            lbl.set_width_chars(width // 8)
            header_row.append(lbl)

        box.append(header_row)

        # Nodes list container
        self.nodes_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self.nodes_list)

        # Empty state
        self.empty_label = Gtk.Label(label="No nodes discovered. Click 'Scan Network' to find AREDN nodes.")
        self.empty_label.add_css_class("dim-label")
        self.nodes_list.append(self.empty_label)

        frame.set_child(box)
        parent.append(frame)

    def _build_mikrotik_section(self, parent):
        """Build MikroTik router setup section"""
        frame = Gtk.Frame()
        frame.set_label("MikroTik Router Setup")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Info row
        info_label = Gtk.Label()
        info_label.set_markup(
            "Install AREDN firmware on MikroTik routers (hAP ac lite, hAP ac2, hAP ac3, etc.)"
        )
        info_label.set_xalign(0)
        info_label.set_wrap(True)
        box.append(info_label)

        # Button row
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        wizard_btn = Gtk.Button(label="Installation Wizard")
        wizard_btn.set_tooltip_text("Step-by-step guide to install AREDN on MikroTik")
        wizard_btn.add_css_class("suggested-action")
        wizard_btn.connect("clicked", self._on_mikrotik_wizard)
        btn_row.append(wizard_btn)

        tftp_btn = Gtk.Button(label="Check TFTP Server")
        tftp_btn.set_tooltip_text("Verify TFTP server is ready for firmware installation")
        tftp_btn.connect("clicked", self._on_check_tftp)
        btn_row.append(tftp_btn)

        # Links
        docs_link = Gtk.LinkButton.new_with_label(
            "https://www.arednmesh.org/content/mikrotik-tutorial",
            "MikroTik Tutorial"
        )
        btn_row.append(docs_link)

        box.append(btn_row)

        # TFTP status
        self.tftp_status = Gtk.Label(label="")
        self.tftp_status.set_xalign(0)
        self.tftp_status.set_wrap(True)
        box.append(self.tftp_status)

        frame.set_child(box)
        parent.append(frame)

    def _build_links_section(self, parent):
        """Build the links/topology section"""
        frame = Gtk.Frame()
        frame.set_label("Network Links")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Links list
        self.links_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        empty_links = Gtk.Label(label="Select a node to view its links")
        empty_links.add_css_class("dim-label")
        self.links_list.append(empty_links)

        box.append(self.links_list)

        # Resources
        resources_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        resources_row.set_margin_top(10)

        aredn_link = Gtk.LinkButton.new_with_label(
            "https://www.arednmesh.org/",
            "AREDN Website"
        )
        resources_row.append(aredn_link)

        docs_link = Gtk.LinkButton.new_with_label(
            "https://docs.arednmesh.org/en/latest/",
            "Documentation"
        )
        resources_row.append(docs_link)

        github_link = Gtk.LinkButton.new_with_label(
            "https://github.com/aredn/aredn",
            "GitHub"
        )
        resources_row.append(github_link)

        box.append(resources_row)

        frame.set_child(box)
        parent.append(frame)

    def _on_scan(self, button):
        """Start network scan"""
        if not HAS_AREDN:
            self.status_label.set_label("AREDN utilities not available")
            return

        subnet = self.subnet_entry.get_text().strip()
        timeout = int(self.timeout_spin.get_value())

        # Save settings
        self._settings["scan_subnet"] = subnet
        self._settings["timeout"] = timeout
        self._save_settings()

        # Start scan
        self._scanning = True
        self.scan_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(True)
        self.scan_progress.set_visible(True)
        self.scan_progress.set_fraction(0)
        self.scan_progress.set_text("Starting scan...")
        self.status_label.set_label("Scanning...")

        # Clear existing nodes display
        self._clear_nodes_list()

        def scan_thread():
            try:
                self._scanner = AREDNScanner(timeout=timeout)

                def on_node_found(node):
                    GLib.idle_add(self._add_node_to_list, node)

                nodes = self._scanner.scan_subnet(subnet, callback=on_node_found)

                GLib.idle_add(self._scan_complete, len(nodes))

            except Exception as e:
                logger.error(f"Scan error: {e}")
                GLib.idle_add(self._scan_error, str(e))

        threading.Thread(target=scan_thread, daemon=True).start()

    def _on_stop_scan(self, button):
        """Stop ongoing scan"""
        if self._scanner:
            self._scanner.stop()
        self._scanning = False
        self.scan_btn.set_sensitive(True)
        self.stop_btn.set_sensitive(False)
        self.scan_progress.set_visible(False)
        self.status_label.set_label("Scan stopped")

    def _clear_nodes_list(self):
        """Clear the nodes list display"""
        while True:
            child = self.nodes_list.get_first_child()
            if child is None:
                break
            self.nodes_list.remove(child)

    def _add_node_to_list(self, node: AREDNNode):
        """Add a discovered node to the list"""
        # Store node
        self._nodes[node.hostname] = node

        # Remove empty label if present
        if self.empty_label.get_parent():
            self.nodes_list.remove(self.empty_label)

        # Create node row
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_top(2)
        row.set_margin_bottom(2)

        # Hostname
        hostname_btn = Gtk.Button(label=node.hostname)
        hostname_btn.set_tooltip_text(f"IP: {node.ip}\nClick to view details")
        hostname_btn.connect("clicked", lambda b, n=node: self._show_node_details(n))
        hostname_btn.set_has_frame(False)
        hostname_btn.set_width_chars(18)
        row.append(hostname_btn)

        # Model
        model_label = Gtk.Label(label=node.model[:15] if node.model else "Unknown")
        model_label.set_xalign(0)
        model_label.set_width_chars(15)
        row.append(model_label)

        # Firmware
        fw_label = Gtk.Label(label=node.firmware_version[:10] if node.firmware_version else "--")
        fw_label.set_xalign(0)
        fw_label.set_width_chars(10)
        row.append(fw_label)

        # Link count
        links_label = Gtk.Label(label=str(len(node.links)))
        links_label.set_xalign(0)
        links_label.set_width_chars(6)
        row.append(links_label)

        # Status indicator
        status_label = Gtk.Label(label="Online")
        status_label.add_css_class("success")
        status_label.set_xalign(0)
        row.append(status_label)

        self.nodes_list.append(row)

        # Update progress
        self.scan_progress.pulse()
        self.scan_progress.set_text(f"Found {len(self._nodes)} nodes")

    def _scan_complete(self, count: int):
        """Handle scan completion"""
        self._scanning = False
        self.scan_btn.set_sensitive(True)
        self.stop_btn.set_sensitive(False)
        self.scan_progress.set_visible(False)
        self.status_label.set_label(f"Found {count} AREDN nodes")

        if count == 0:
            self.nodes_list.append(self.empty_label)

    def _scan_error(self, error: str):
        """Handle scan error"""
        self._scanning = False
        self.scan_btn.set_sensitive(True)
        self.stop_btn.set_sensitive(False)
        self.scan_progress.set_visible(False)
        self.status_label.set_label(f"Scan error: {error}")

    def _on_add_node(self, button):
        """Manually add a node"""
        dialog = Gtk.MessageDialog(
            transient_for=self.main_window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Add AREDN Node"
        )
        dialog.format_secondary_text("Enter node hostname or IP address:")

        content = dialog.get_content_area()
        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g., KK6XXX-node or 10.0.0.1")
        entry.set_margin_start(20)
        entry.set_margin_end(20)
        content.append(entry)

        def on_response(d, response):
            if response == Gtk.ResponseType.OK:
                hostname = entry.get_text().strip()
                if hostname:
                    self._fetch_single_node(hostname)
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _fetch_single_node(self, hostname: str):
        """Fetch a single node's info"""
        if not HAS_AREDN:
            return

        self.status_label.set_label(f"Connecting to {hostname}...")

        def fetch():
            try:
                client = AREDNClient(hostname, timeout=5)
                node = client.get_node_info()
                if node:
                    GLib.idle_add(self._add_node_to_list, node)
                    GLib.idle_add(lambda: self.status_label.set_label(f"Added {node.hostname}"))
                else:
                    GLib.idle_add(lambda: self.status_label.set_label(f"No response from {hostname}"))
            except Exception as e:
                GLib.idle_add(lambda: self.status_label.set_label(f"Error: {e}"))

        threading.Thread(target=fetch, daemon=True).start()

    def _show_node_details(self, node: AREDNNode):
        """Show detailed node information"""
        # Update links section
        self._update_links_display(node)

        # Show info dialog
        dialog = Gtk.MessageDialog(
            transient_for=self.main_window,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text=f"Node: {node.hostname}"
        )

        details = [
            f"IP: {node.ip}",
            f"Model: {node.model}",
            f"Firmware: {node.firmware_version}",
            f"Uptime: {node.uptime}",
            f"SSID: {node.ssid}",
            f"Channel: {node.channel} ({node.frequency} MHz)",
            f"Width: {node.channel_width} MHz",
            f"Links: {len(node.links)}",
            f"Tunnels: {node.tunnel_count}",
            f"Description: {node.description or 'N/A'}",
        ]

        dialog.format_secondary_text("\n".join(details))
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()

    def _update_links_display(self, node: AREDNNode):
        """Update the links display for a node"""
        # Clear existing
        while True:
            child = self.links_list.get_first_child()
            if child is None:
                break
            self.links_list.remove(child)

        if not node.links:
            empty = Gtk.Label(label=f"No links for {node.hostname}")
            empty.add_css_class("dim-label")
            self.links_list.append(empty)
            return

        # Header
        header = Gtk.Label(label=f"Links from {node.hostname}:")
        header.set_xalign(0)
        header.add_css_class("heading")
        self.links_list.append(header)

        for link in node.links:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            # Link type icon
            type_icons = {
                LinkType.RF: "network-wireless-symbolic",
                LinkType.DTD: "network-wired-symbolic",
                LinkType.TUN: "network-vpn-symbolic",
            }
            icon = Gtk.Image.new_from_icon_name(
                type_icons.get(link.link_type, "network-offline-symbolic")
            )
            row.append(icon)

            # Hostname
            host_label = Gtk.Label(label=link.hostname or link.ip)
            host_label.set_xalign(0)
            host_label.set_hexpand(True)
            row.append(host_label)

            # Link quality
            lq = int(link.link_quality * 100)
            lq_label = Gtk.Label(label=f"LQ: {lq}%")
            if lq >= 80:
                lq_label.add_css_class("success")
            elif lq >= 50:
                lq_label.add_css_class("warning")
            else:
                lq_label.add_css_class("error")
            row.append(lq_label)

            # Signal (RF only)
            if link.link_type == LinkType.RF and link.signal:
                sig_label = Gtk.Label(label=f"{link.signal}dBm")
                row.append(sig_label)

                snr_label = Gtk.Label(label=f"SNR:{link.snr}dB")
                row.append(snr_label)

            self.links_list.append(row)

    def _on_mikrotik_wizard(self, button):
        """Show MikroTik installation wizard"""
        if not HAS_AREDN:
            self.status_label.set_label("AREDN utilities not available")
            return

        steps = MikroTikAREDN.get_installation_steps()

        dialog = Gtk.MessageDialog(
            transient_for=self.main_window,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text="MikroTik AREDN Installation"
        )

        # Create scrollable text view
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(400)
        scroll.set_min_content_width(500)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.get_buffer().set_text("\n".join(steps))
        scroll.set_child(text_view)

        dialog.get_content_area().append(scroll)
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()

    def _on_check_tftp(self, button):
        """Check TFTP server status"""
        if not HAS_AREDN:
            self.tftp_status.set_label("AREDN utilities not available")
            return

        result = MikroTikAREDN.check_tftp_server()

        if result['available']:
            self.tftp_status.set_markup(
                f"<span foreground='green'>✓ TFTP server available ({result['method']})</span>"
            )
        else:
            instructions = "\n".join(result.get('instructions', ['Install dnsmasq']))
            self.tftp_status.set_markup(
                f"<span foreground='orange'>✗ TFTP server not found</span>\n{instructions}"
            )
