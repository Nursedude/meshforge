"""
RNS (Reticulum Network Stack) Management Panel
Integrates Reticulum mesh networking with MeshForge
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import shutil
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class RNSPanel(Gtk.Box):
    """RNS management panel for Reticulum Network Stack integration"""

    # RNS ecosystem components
    COMPONENTS = [
        {
            'name': 'rns',
            'display': 'Reticulum Network Stack',
            'package': 'rns',
            'description': 'Core cryptographic networking protocol',
            'service': 'rnsd',
        },
        {
            'name': 'lxmf',
            'display': 'LXMF',
            'package': 'lxmf',
            'description': 'Lightweight Extensible Message Format',
            'service': None,
        },
        {
            'name': 'nomadnet',
            'display': 'NomadNet',
            'package': 'nomadnet',
            'description': 'Terminal-based messaging client',
            'service': None,
        },
        {
            'name': 'rnodeconf',
            'display': 'RNode Configurator',
            'package': 'rnodeconf',
            'description': 'RNODE device configuration tool',
            'service': None,
        },
    ]

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._component_status = {}
        self._build_ui()
        self._refresh_all()

    def _build_ui(self):
        """Build the RNS panel UI"""
        # Title
        title = Gtk.Label(label="Reticulum Network Stack")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(label="Manage RNS ecosystem and gateway integration")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Scrollable content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)

        # RNS Service Status
        self._build_service_section(content)

        # Components Section
        self._build_components_section(content)

        # Gateway Section
        self._build_gateway_section(content)

        # Configuration Section
        self._build_config_section(content)

        # NomadNet Tools Section
        self._build_nomadnet_section(content)

        scrolled.set_child(content)
        self.append(scrolled)

    def _build_service_section(self, parent):
        """Build RNS service status section"""
        frame = Gtk.Frame()
        frame.set_label("RNS Service (rnsd)")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self.rns_status_icon = Gtk.Image.new_from_icon_name("emblem-question")
        self.rns_status_icon.set_pixel_size(32)
        status_row.append(self.rns_status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.rns_status_label = Gtk.Label(label="Checking...")
        self.rns_status_label.set_xalign(0)
        self.rns_status_label.add_css_class("heading")
        status_info.append(self.rns_status_label)

        self.rns_status_detail = Gtk.Label(label="")
        self.rns_status_detail.set_xalign(0)
        self.rns_status_detail.add_css_class("dim-label")
        status_info.append(self.rns_status_detail)

        status_row.append(status_info)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        # Control buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        self.rns_start_btn = Gtk.Button(label="Start")
        self.rns_start_btn.add_css_class("suggested-action")
        self.rns_start_btn.connect("clicked", lambda b: self._service_action("start"))
        btn_box.append(self.rns_start_btn)

        self.rns_stop_btn = Gtk.Button(label="Stop")
        self.rns_stop_btn.add_css_class("destructive-action")
        self.rns_stop_btn.connect("clicked", lambda b: self._service_action("stop"))
        btn_box.append(self.rns_stop_btn)

        self.rns_restart_btn = Gtk.Button(label="Restart")
        self.rns_restart_btn.connect("clicked", lambda b: self._service_action("restart"))
        btn_box.append(self.rns_restart_btn)

        status_row.append(btn_box)
        box.append(status_row)

        # Installation note
        self.rns_install_note = Gtk.Label(label="")
        self.rns_install_note.set_xalign(0)
        self.rns_install_note.add_css_class("dim-label")
        self.rns_install_note.set_wrap(True)
        box.append(self.rns_install_note)

        frame.set_child(box)
        parent.append(frame)

    def _build_components_section(self, parent):
        """Build components installation section"""
        frame = Gtk.Frame()
        frame.set_label("RNS Ecosystem Components")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        self.component_rows = {}

        for component in self.COMPONENTS:
            row = self._create_component_row(component)
            box.append(row)
            self.component_rows[component['name']] = row

        # Install all / Update all buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_margin_top(10)
        btn_row.set_halign(Gtk.Align.CENTER)

        install_all_btn = Gtk.Button(label="Install All")
        install_all_btn.add_css_class("suggested-action")
        install_all_btn.connect("clicked", self._on_install_all)
        btn_row.append(install_all_btn)

        update_all_btn = Gtk.Button(label="Update All")
        update_all_btn.connect("clicked", self._on_update_all)
        btn_row.append(update_all_btn)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda b: self._refresh_all())
        btn_row.append(refresh_btn)

        box.append(btn_row)

        frame.set_child(box)
        parent.append(frame)

    def _create_component_row(self, component):
        """Create a row for a component"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_top(5)
        row.set_margin_bottom(5)

        # Status indicator
        status_icon = Gtk.Image.new_from_icon_name("emblem-question")
        status_icon.set_pixel_size(16)
        row.append(status_icon)

        # Component info
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)

        name_label = Gtk.Label(label=component['display'])
        name_label.set_xalign(0)
        name_label.add_css_class("heading")
        info_box.append(name_label)

        desc_label = Gtk.Label(label=component['description'])
        desc_label.set_xalign(0)
        desc_label.add_css_class("dim-label")
        info_box.append(desc_label)

        row.append(info_box)

        # Version label
        version_label = Gtk.Label(label="--")
        version_label.add_css_class("dim-label")
        row.append(version_label)

        # Action button
        action_btn = Gtk.Button(label="Install")
        action_btn.connect("clicked", lambda b: self._install_component(component))
        row.append(action_btn)

        # Store references for updates
        row.status_icon = status_icon
        row.version_label = version_label
        row.action_btn = action_btn
        row.component = component

        return row

    def _build_gateway_section(self, parent):
        """Build RNS-Meshtastic gateway section"""
        frame = Gtk.Frame()
        frame.set_label("RNS-Meshtastic Gateway")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Description
        desc = Gtk.Label(label="Bridge Reticulum and Meshtastic networks for unified mesh communication")
        desc.set_xalign(0)
        desc.set_wrap(True)
        desc.add_css_class("dim-label")
        box.append(desc)

        # Gateway status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self.gateway_status_icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
        self.gateway_status_icon.set_pixel_size(24)
        status_row.append(self.gateway_status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.gateway_status_label = Gtk.Label(label="Gateway: Stopped")
        self.gateway_status_label.set_xalign(0)
        self.gateway_status_label.add_css_class("heading")
        status_info.append(self.gateway_status_label)

        self.gateway_detail_label = Gtk.Label(label="Not running")
        self.gateway_detail_label.set_xalign(0)
        self.gateway_detail_label.add_css_class("dim-label")
        status_info.append(self.gateway_detail_label)
        status_row.append(status_info)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        # Gateway control buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        self.gateway_start_btn = Gtk.Button(label="Start")
        self.gateway_start_btn.add_css_class("suggested-action")
        self.gateway_start_btn.connect("clicked", self._on_gateway_start)
        btn_box.append(self.gateway_start_btn)

        self.gateway_stop_btn = Gtk.Button(label="Stop")
        self.gateway_stop_btn.add_css_class("destructive-action")
        self.gateway_stop_btn.connect("clicked", self._on_gateway_stop)
        self.gateway_stop_btn.set_sensitive(False)
        btn_box.append(self.gateway_stop_btn)

        status_row.append(btn_box)
        box.append(status_row)

        # Gateway enable switch
        enable_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        enable_label = Gtk.Label(label="Enable Gateway:")
        enable_label.set_xalign(0)
        enable_row.append(enable_label)

        self.gateway_enable_switch = Gtk.Switch()
        self.gateway_enable_switch.connect("state-set", self._on_gateway_enable_changed)
        enable_row.append(self.gateway_enable_switch)

        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        enable_row.append(spacer2)

        box.append(enable_row)

        # Connection status
        conn_frame = Gtk.Frame()
        conn_frame.set_label("Connection Status")
        conn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        conn_box.set_margin_start(10)
        conn_box.set_margin_end(10)
        conn_box.set_margin_top(8)
        conn_box.set_margin_bottom(8)

        # Meshtastic connection
        mesh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.mesh_conn_icon = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        self.mesh_conn_icon.set_pixel_size(16)
        mesh_box.append(self.mesh_conn_icon)
        self.mesh_conn_label = Gtk.Label(label="Meshtastic: Unknown")
        mesh_box.append(self.mesh_conn_label)
        conn_box.append(mesh_box)

        # RNS connection
        rns_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.rns_conn_icon = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        self.rns_conn_icon.set_pixel_size(16)
        rns_box.append(self.rns_conn_icon)
        self.rns_conn_label = Gtk.Label(label="RNS: Unknown")
        rns_box.append(self.rns_conn_label)
        conn_box.append(rns_box)

        conn_frame.set_child(conn_box)
        box.append(conn_frame)

        # Test and configure buttons
        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        action_row.set_halign(Gtk.Align.CENTER)
        action_row.set_margin_top(5)

        test_btn = Gtk.Button(label="Test Connections")
        test_btn.connect("clicked", self._on_test_gateway)
        action_row.append(test_btn)

        config_btn = Gtk.Button(label="Configure")
        config_btn.set_tooltip_text("Open GUI config editor")
        config_btn.connect("clicked", self._on_configure_gateway)
        action_row.append(config_btn)

        # Terminal editor for gateway config
        config_terminal_btn = Gtk.Button(label="Edit (Terminal)")
        config_terminal_btn.set_tooltip_text("Edit gateway.json in terminal with nano")
        config_terminal_btn.connect("clicked", self._on_edit_gateway_terminal)
        action_row.append(config_terminal_btn)

        view_nodes_btn = Gtk.Button(label="View Nodes")
        view_nodes_btn.connect("clicked", self._on_view_nodes)
        action_row.append(view_nodes_btn)

        box.append(action_row)

        # Statistics
        stats_expander = Gtk.Expander(label="Statistics")
        stats_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        stats_box.set_margin_start(10)
        stats_box.set_margin_top(5)

        self.stats_labels = {}
        for stat_name in ["Messages Mesh→RNS", "Messages RNS→Mesh", "Total Nodes", "Errors"]:
            stat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            name_lbl = Gtk.Label(label=f"{stat_name}:")
            name_lbl.set_xalign(0)
            name_lbl.set_size_request(150, -1)
            stat_row.append(name_lbl)
            val_lbl = Gtk.Label(label="0")
            val_lbl.set_xalign(0)
            self.stats_labels[stat_name] = val_lbl
            stat_row.append(val_lbl)
            stats_box.append(stat_row)

        stats_expander.set_child(stats_box)
        box.append(stats_expander)

        # Meshtastic Interface Setup
        mesh_iface_expander = Gtk.Expander(label="Meshtastic Interface Setup")
        mesh_iface_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        mesh_iface_box.set_margin_start(10)
        mesh_iface_box.set_margin_top(5)
        mesh_iface_box.set_margin_bottom(5)

        mesh_desc = Gtk.Label(
            label="Install the RNS-Meshtastic interface to bridge networks.\n"
                  "Requires: pip install meshtastic"
        )
        mesh_desc.set_xalign(0)
        mesh_desc.add_css_class("dim-label")
        mesh_desc.set_wrap(True)
        mesh_iface_box.append(mesh_desc)

        # Install button row
        install_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        install_iface_btn = Gtk.Button(label="Install Interface")
        install_iface_btn.set_tooltip_text("Download Meshtastic_Interface.py to ~/.reticulum/interfaces/")
        install_iface_btn.connect("clicked", self._install_meshtastic_interface)
        install_row.append(install_iface_btn)

        add_config_btn = Gtk.Button(label="Add Config Template")
        add_config_btn.set_tooltip_text("Add Meshtastic Interface config to RNS config file")
        add_config_btn.connect("clicked", self._add_meshtastic_interface_config)
        install_row.append(add_config_btn)

        # Edit interface file button
        edit_iface_btn = Gtk.Button(label="Edit Interface")
        edit_iface_btn.set_tooltip_text("Edit Meshtastic_Interface.py in terminal (set speed, connection type)")
        edit_iface_btn.connect("clicked", self._edit_meshtastic_interface)
        install_row.append(edit_iface_btn)

        mesh_iface_box.append(install_row)

        # Status label
        self.mesh_iface_status = Gtk.Label(label="")
        self.mesh_iface_status.set_xalign(0)
        self.mesh_iface_status.add_css_class("dim-label")
        mesh_iface_box.append(self.mesh_iface_status)

        # Check if already installed
        iface_file = Path.home() / ".reticulum" / "interfaces" / "Meshtastic_Interface.py"
        if iface_file.exists():
            self.mesh_iface_status.set_label("✓ Meshtastic_Interface.py installed")

        mesh_iface_expander.set_child(mesh_iface_box)
        box.append(mesh_iface_expander)

        frame.set_child(box)
        parent.append(frame)

        # Initialize gateway state
        self._gateway_bridge = None
        self._update_gateway_status()

    def _build_config_section(self, parent):
        """Build RNS configuration section"""
        frame = Gtk.Frame()
        frame.set_label("Configuration")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Config file location - use real user's home when running as root
        config_path = self._get_real_user_home() / ".reticulum"

        config_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        config_label = Gtk.Label(label="Config Directory:")
        config_label.set_xalign(0)
        config_row.append(config_label)

        config_path_label = Gtk.Label(label=str(config_path))
        config_path_label.add_css_class("dim-label")
        config_path_label.set_selectable(True)
        config_row.append(config_path_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        config_row.append(spacer)

        if config_path.exists():
            open_btn = Gtk.Button(label="Open Folder")
            open_btn.connect("clicked", lambda b: self._open_config_folder(config_path))
            config_row.append(open_btn)

        box.append(config_row)

        # Main config file
        config_file = config_path / "config"
        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        file_label = Gtk.Label(label="Main Config:")
        file_label.set_xalign(0)
        file_row.append(file_label)

        file_path_label = Gtk.Label(label=str(config_file))
        file_path_label.add_css_class("dim-label")
        file_row.append(file_path_label)

        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        file_row.append(spacer2)

        # Edit with external editor
        ext_edit_btn = Gtk.Button(label="Edit (GUI)")
        ext_edit_btn.set_tooltip_text("Open in GUI text editor")
        ext_edit_btn.connect("clicked", lambda b: self._edit_config(config_file))
        file_row.append(ext_edit_btn)

        # Edit in terminal with nano
        terminal_edit_btn = Gtk.Button(label="Edit (Terminal)")
        terminal_edit_btn.set_tooltip_text("Open in terminal with nano")
        terminal_edit_btn.connect("clicked", lambda b: self._edit_config_terminal(config_file))
        file_row.append(terminal_edit_btn)

        # Edit with built-in config editor
        config_edit_btn = Gtk.Button(label="Config Editor")
        config_edit_btn.add_css_class("suggested-action")
        config_edit_btn.set_tooltip_text("Open in MeshForge config editor with templates")
        config_edit_btn.connect("clicked", lambda b: self._open_rns_config_dialog())
        file_row.append(config_edit_btn)

        box.append(file_row)

        if not config_file.exists():
            # Add Create Default button
            create_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            note_label = Gtk.Label(
                label="No config file exists yet."
            )
            note_label.add_css_class("dim-label")
            note_label.set_xalign(0)
            create_row.append(note_label)

            create_btn = Gtk.Button(label="Create Default Config")
            create_btn.add_css_class("suggested-action")
            create_btn.set_tooltip_text("Create a default RNS config with AutoInterface enabled")
            create_btn.connect("clicked", lambda b: self._create_default_rns_config(config_file))
            create_row.append(create_btn)

            box.append(create_row)

        frame.set_child(box)
        parent.append(frame)

    def _build_nomadnet_section(self, parent):
        """Build NomadNet tools section"""
        frame = Gtk.Frame()
        frame.set_label("NomadNet Tools")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Description
        desc = Gtk.Label(label="Terminal-based encrypted messaging and browsing over Reticulum")
        desc.set_xalign(0)
        desc.set_wrap(True)
        desc.add_css_class("dim-label")
        box.append(desc)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.nomadnet_status_icon = Gtk.Image.new_from_icon_name("emblem-question")
        self.nomadnet_status_icon.set_pixel_size(20)
        status_row.append(self.nomadnet_status_icon)

        self.nomadnet_status_label = Gtk.Label(label="Checking...")
        self.nomadnet_status_label.set_xalign(0)
        status_row.append(self.nomadnet_status_label)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh status")
        refresh_btn.connect("clicked", lambda b: self._check_nomadnet_status())
        status_row.append(refresh_btn)

        box.append(status_row)

        # Launch buttons row
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_halign(Gtk.Align.CENTER)

        # NomadNet Text UI
        self.nomadnet_textui_btn = Gtk.Button(label="Launch Text UI")
        self.nomadnet_textui_btn.add_css_class("suggested-action")
        self.nomadnet_textui_btn.set_tooltip_text("Launch NomadNet in a terminal window")
        self.nomadnet_textui_btn.connect("clicked", lambda b: self._launch_nomadnet("textui"))
        btn_row.append(self.nomadnet_textui_btn)

        # NomadNet Daemon
        self.nomadnet_daemon_btn = Gtk.Button(label="Start Daemon")
        self.nomadnet_daemon_btn.set_tooltip_text("Run NomadNet as background daemon")
        self.nomadnet_daemon_btn.connect("clicked", lambda b: self._launch_nomadnet("daemon"))
        btn_row.append(self.nomadnet_daemon_btn)

        # Stop Daemon
        self.nomadnet_stop_btn = Gtk.Button(label="Stop Daemon")
        self.nomadnet_stop_btn.add_css_class("destructive-action")
        self.nomadnet_stop_btn.set_tooltip_text("Stop NomadNet daemon")
        self.nomadnet_stop_btn.connect("clicked", lambda b: self._stop_nomadnet())
        btn_row.append(self.nomadnet_stop_btn)

        box.append(btn_row)

        # Config row - use lambdas to defer path resolution
        config_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        config_row.set_halign(Gtk.Align.CENTER)

        config_btn = Gtk.Button(label="Edit Config")
        config_btn.set_tooltip_text("Edit ~/.nomadnetwork/config")
        config_btn.connect("clicked", lambda b: self._edit_nomadnet_config())
        config_row.append(config_btn)

        # Open config folder
        folder_btn = Gtk.Button(label="Open Folder")
        folder_btn.set_tooltip_text("Open ~/.nomadnetwork folder")
        folder_btn.connect("clicked", lambda b: self._open_nomadnet_folder())
        config_row.append(folder_btn)

        box.append(config_row)

        # Links row
        links_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        links_row.set_halign(Gtk.Align.CENTER)

        docs_link = Gtk.LinkButton.new_with_label(
            "https://github.com/markqvist/NomadNet",
            "Documentation"
        )
        links_row.append(docs_link)

        reticulum_link = Gtk.LinkButton.new_with_label(
            "https://reticulum.network/",
            "Reticulum Network"
        )
        links_row.append(reticulum_link)

        box.append(links_row)

        # Testnet info (expandable)
        testnet_expander = Gtk.Expander(label="RNS Testnet Hubs")
        testnet_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        testnet_box.set_margin_start(10)
        testnet_box.set_margin_top(5)

        testnet_desc = Gtk.Label(label="Use Ctrl+U in NomadNet Network view to connect:")
        testnet_desc.set_xalign(0)
        testnet_desc.add_css_class("dim-label")
        testnet_box.append(testnet_desc)

        # Dublin hub
        dublin_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        dublin_label = Gtk.Label(label="Dublin: ")
        dublin_label.set_xalign(0)
        dublin_row.append(dublin_label)
        dublin_addr = Gtk.Label(label="abb3ebcd03cb2388a838e70c001291f9")
        dublin_addr.set_selectable(True)
        dublin_addr.add_css_class("monospace")
        dublin_row.append(dublin_addr)
        testnet_box.append(dublin_row)

        # Frankfurt hub
        frankfurt_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        frankfurt_label = Gtk.Label(label="Frankfurt: ")
        frankfurt_label.set_xalign(0)
        frankfurt_row.append(frankfurt_label)
        frankfurt_addr = Gtk.Label(label="ea6a715f814bdc37e56f80c34da6ad51")
        frankfurt_addr.set_selectable(True)
        frankfurt_addr.add_css_class("monospace")
        frankfurt_row.append(frankfurt_addr)
        testnet_box.append(frankfurt_row)

        testnet_expander.set_child(testnet_box)
        box.append(testnet_expander)

        frame.set_child(box)
        parent.append(frame)

        # Check status on load
        GLib.timeout_add(500, self._check_nomadnet_status)

    def _get_real_user_home(self):
        """Get the real user's home directory, even when running as root via sudo"""
        import os
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and os.geteuid() == 0:
            return Path('/home') / sudo_user
        return Path.home()

    def _get_real_username(self):
        """Get the real username, even when running as root via sudo"""
        import os
        return os.environ.get('SUDO_USER', os.environ.get('USER', 'root'))

    def _find_nomadnet(self):
        """Find nomadnet executable, checking user local bin if running as root"""
        import os

        # First check system PATH
        nomadnet_path = shutil.which('nomadnet')
        if nomadnet_path:
            return nomadnet_path

        # Check real user's local bin (for --user pip installs)
        real_home = self._get_real_user_home()
        user_local_bin = real_home / ".local" / "bin" / "nomadnet"
        if user_local_bin.exists():
            return str(user_local_bin)

        return None

    def _check_nomadnet_status(self):
        """Check if NomadNet daemon is running"""
        def check():
            try:
                running = False

                # Simple approach: use pgrep -f to find any process with "nomadnet" in cmdline
                result = subprocess.run(
                    ['pgrep', '-f', 'nomadnet'],
                    capture_output=True, text=True, timeout=5
                )

                if result.returncode == 0 and result.stdout.strip():
                    # Filter out any pgrep or grep processes from the PIDs
                    pids = result.stdout.strip().split('\n')
                    print(f"[RNS] pgrep found PIDs: {pids}", flush=True)

                    # Verify at least one PID is actually nomadnet (not grep/pgrep)
                    for pid in pids:
                        try:
                            # Read the cmdline for this PID
                            cmdline_path = f"/proc/{pid.strip()}/cmdline"
                            with open(cmdline_path, 'r') as f:
                                cmdline = f.read().replace('\x00', ' ')

                            # Check it's actually nomadnet, not grep/pgrep
                            if 'nomadnet' in cmdline and 'grep' not in cmdline and 'pgrep' not in cmdline:
                                running = True
                                print(f"[RNS] NomadNet daemon running (PID {pid.strip()}): {cmdline[:80]}", flush=True)
                                break
                        except (FileNotFoundError, PermissionError):
                            # Process may have exited
                            continue

                if not running:
                    print("[RNS] NomadNet daemon not detected", flush=True)

                GLib.idle_add(self._update_nomadnet_status, running)
            except Exception as e:
                print(f"[RNS] Error checking nomadnet status: {e}", flush=True)
                GLib.idle_add(self._update_nomadnet_status, False)

        threading.Thread(target=check, daemon=True).start()
        return False

    def _is_nomadnet_daemon_running(self):
        """Synchronously check if NomadNet daemon is running"""
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    try:
                        cmdline_path = f"/proc/{pid.strip()}/cmdline"
                        with open(cmdline_path, 'r') as f:
                            cmdline = f.read().replace('\x00', ' ')
                        if 'nomadnet' in cmdline and 'grep' not in cmdline and 'pgrep' not in cmdline:
                            return True
                    except (FileNotFoundError, PermissionError):
                        continue
            return False
        except Exception:
            return False

    def _update_nomadnet_status(self, running):
        """Update NomadNet status display"""
        nomadnet_path = self._find_nomadnet()

        if running:
            self.nomadnet_status_icon.set_from_icon_name("emblem-default-symbolic")
            self.nomadnet_status_label.set_label("NomadNet daemon running")
            self.nomadnet_daemon_btn.set_sensitive(False)
            self.nomadnet_stop_btn.set_sensitive(True)
        else:
            # Check if installed
            if nomadnet_path:
                self.nomadnet_status_icon.set_from_icon_name("media-playback-stop-symbolic")
                self.nomadnet_status_label.set_label("NomadNet installed (daemon stopped)")
                self.nomadnet_daemon_btn.set_sensitive(True)
                self.nomadnet_stop_btn.set_sensitive(False)
            else:
                self.nomadnet_status_icon.set_from_icon_name("dialog-warning-symbolic")
                self.nomadnet_status_label.set_label("NomadNet not installed")
                self.nomadnet_daemon_btn.set_sensitive(False)
                self.nomadnet_stop_btn.set_sensitive(False)
        return False

    def _launch_nomadnet(self, mode):
        """Launch NomadNet in specified mode"""
        import os

        print(f"[RNS] Launching NomadNet ({mode})...", flush=True)

        # Disable button immediately to prevent double-clicks
        if mode == "textui" and hasattr(self, 'nomadnet_textui_btn'):
            self.nomadnet_textui_btn.set_sensitive(False)
            # Re-enable after 2 seconds
            GLib.timeout_add(2000, lambda: self.nomadnet_textui_btn.set_sensitive(True) or False)

        nomadnet_path = self._find_nomadnet()
        if not nomadnet_path:
            self.main_window.set_status_message("NomadNet not installed - install it first")
            print("[RNS] NomadNet not found", flush=True)
            return

        print(f"[RNS] Found nomadnet at: {nomadnet_path}", flush=True)

        # Check if running as root via sudo
        is_root = os.geteuid() == 0
        real_user = self._get_real_username()

        # Check if daemon is running before launching text UI (they conflict)
        if mode == "textui":
            daemon_running = self._is_nomadnet_daemon_running()
            if daemon_running:
                self.main_window.set_status_message("Stop daemon first - Text UI and daemon can't run together")
                print("[RNS] Daemon is running, cannot start text UI", flush=True)
                return

        try:
            if mode == "textui":
                # Launch in a terminal
                # When running as root: run terminal as root (has X11), but command as user
                if is_root and real_user != 'root':
                    # Create a temp script to run the command - more reliable than complex quoting
                    import tempfile
                    script_content = f'''#!/bin/bash
sudo -i -u {real_user} nomadnet --config CONFIG
echo ""
echo "Press Enter to close..."
read
'''
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                        f.write(script_content)
                        script_path = f.name
                    os.chmod(script_path, 0o755)

                    terminals = [
                        f"lxterminal -e {script_path}",
                        f"xfce4-terminal -e {script_path}",
                        f"gnome-terminal -- {script_path}",
                        f"konsole -e {script_path}",
                        f"xterm -e {script_path}",
                    ]
                else:
                    import tempfile
                    script_content = '''#!/bin/bash
nomadnet --config CONFIG
echo ""
echo "Press Enter to close..."
read
'''
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                        f.write(script_content)
                        script_path = f.name
                    os.chmod(script_path, 0o755)

                    terminals = [
                        f"lxterminal -e {script_path}",
                        f"xfce4-terminal -e {script_path}",
                        f"gnome-terminal -- {script_path}",
                        f"konsole -e {script_path}",
                        f"xterm -e {script_path}",
                    ]

                for full_cmd in terminals:
                    term_name = full_cmd.split()[0]
                    if shutil.which(term_name):
                        print(f"[RNS] Using terminal: {term_name} (user: {real_user})", flush=True)
                        print(f"[RNS] Command: {full_cmd}", flush=True)
                        try:
                            # Use setsid to completely detach the terminal from MeshForge
                            os.system(f"setsid {full_cmd} >/dev/null 2>&1 &")
                            self.main_window.set_status_message("NomadNet launched in terminal")
                        except Exception as e:
                            print(f"[RNS] Failed to launch terminal: {e}", flush=True)
                            continue
                        return
                self.main_window.set_status_message("No terminal emulator found")
            elif mode == "daemon":
                # Run as daemon using full path
                # When running as root, run as real user
                if is_root and real_user != 'root':
                    cmd = ['sudo', '-u', real_user, nomadnet_path, '--daemon']
                else:
                    cmd = [nomadnet_path, '--daemon']

                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )
                self.main_window.set_status_message("NomadNet daemon started")
                print(f"[RNS] NomadNet daemon started (user: {real_user})", flush=True)
                # Refresh status after a moment
                GLib.timeout_add(1000, self._check_nomadnet_status)
        except Exception as e:
            print(f"[RNS] Failed to launch NomadNet: {e}", flush=True)
            self.main_window.set_status_message(f"Failed: {e}")

    def _check_terminal_launch(self, proc):
        """Check if terminal process launched successfully"""
        if proc.poll() is not None:
            # Process exited
            try:
                stderr = proc.stderr.read().decode() if proc.stderr else ""
                if stderr:
                    print(f"[RNS] Terminal error: {stderr}", flush=True)
                if proc.returncode != 0:
                    print(f"[RNS] Terminal exited with code: {proc.returncode}", flush=True)
            except Exception:
                pass
        return False  # Don't repeat

    def _stop_nomadnet(self):
        """Stop NomadNet daemon"""
        print("[RNS] Stopping NomadNet daemon...", flush=True)
        try:
            result = subprocess.run(['pkill', '-f', 'nomadnet'], capture_output=True, timeout=10)
            if result.returncode == 0:
                self.main_window.set_status_message("NomadNet daemon stopped")
                print("[RNS] NomadNet stopped", flush=True)
                # Refresh status
                GLib.timeout_add(500, self._check_nomadnet_status)
            else:
                self.main_window.set_status_message("NomadNet was not running")
        except Exception as e:
            print(f"[RNS] Failed to stop NomadNet: {e}", flush=True)
            self.main_window.set_status_message(f"Failed: {e}")

    def _edit_nomadnet_config(self):
        """Edit NomadNet config file"""
        real_home = self._get_real_user_home()
        config_file = real_home / ".nomadnetwork" / "config"

        # If config doesn't exist or is empty, create default config
        if not config_file.exists() or config_file.stat().st_size == 0:
            self._create_default_nomadnet_config(config_file)

        self._edit_config_terminal(config_file)

    def _create_default_rns_config(self, config_file):
        """Create a default RNS config file with sensible defaults"""
        import os

        default_config = '''# Reticulum Network Stack Configuration
# Reference: https://reticulum.network/manual/interfaces.html

[reticulum]
# Enable this node to act as a transport node
# and route traffic for other peers
enable_transport = False

# Share the Reticulum instance with locally
# running clients via a local socket
share_instance = Yes

# If running multiple instances, give them
# unique names to avoid conflicts
# instance_name = default

# Panic and forcibly close if a hardware
# interface experiences an unrecoverable error
panic_on_interface_error = No


[logging]
# Valid log levels are 0 through 7:
#   0: Log only critical information
#   1: Log errors and lower log levels
#   2: Log warnings and lower log levels
#   3: Log notices and lower log levels
#   4: Log info and lower (default)
#   5: Verbose logging
#   6: Debug logging
#   7: Extreme logging
loglevel = 4


[interfaces]
# Default AutoInterface for local network discovery
# Uses link-local UDP broadcasts for peer discovery
[[Default Interface]]
    type = AutoInterface
    enabled = Yes


# ===== RNS TESTNET CONNECTIONS =====
# Uncomment to connect to the public Reticulum Testnet

# [[RNS Testnet Dublin]]
#     type = TCPClientInterface
#     enabled = yes
#     target_host = dublin.connect.reticulum.network
#     target_port = 4965

# [[RNS Testnet BetweenTheBorders]]
#     type = TCPClientInterface
#     enabled = yes
#     target_host = reticulum.betweentheborders.com
#     target_port = 4242


# ===== TCP INTERFACES =====
# For hosting your own connectable node

# [[TCP Server Interface]]
#     type = TCPServerInterface
#     enabled = no
#     listen_ip = 0.0.0.0
#     listen_port = 4242

# [[TCP Client Interface]]
#     type = TCPClientInterface
#     enabled = no
#     target_host = example.com
#     target_port = 4242


# ===== RNODE LORA INTERFACE =====
# For LoRa communication using RNode devices

# [[RNode LoRa Interface]]
#     type = RNodeInterface
#     interface_enabled = False
#     port = /dev/ttyUSB0
#     frequency = 867200000
#     bandwidth = 125000
#     txpower = 7
#     spreadingfactor = 8
#     codingrate = 5

# BLE RNode connection (must be paired first):
# [[RNode BLE]]
#     type = RNodeInterface
#     interface_enabled = False
#     port = ble://RNode 3B87
'''

        try:
            config_path = Path(config_file)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(default_config)

            # Fix ownership if running as root
            real_user = self._get_real_username()
            is_root = os.geteuid() == 0
            if is_root and real_user != 'root':
                subprocess.run(['chown', '-R', f'{real_user}:{real_user}', str(config_path.parent)],
                               capture_output=True)

            print(f"[RNS] Created default RNS config: {config_path}", flush=True)
            self.main_window.set_status_message("Created default RNS config")

            # Refresh the panel to show the config exists now
            GLib.timeout_add(500, self._refresh_panel)

        except Exception as e:
            print(f"[RNS] Failed to create default config: {e}", flush=True)
            self.main_window.set_status_message(f"Failed to create config: {e}")

    def _refresh_panel(self):
        """Refresh the panel content"""
        # This is a simple refresh - in a full implementation we'd rebuild the config section
        return False

    def _create_default_nomadnet_config(self, config_file):
        """Create a default NomadNet config file with sensible defaults"""
        import os

        default_config = '''# NomadNet Configuration File
# Edit this file to customize your NomadNet settings
# Reference: https://github.com/markqvist/NomadNet

[logging]
# Valid log levels are 0 through 7:
#   0: Log only critical information
#   1: Log errors and lower log levels
#   2: Log warnings and lower log levels
#   3: Log notices and lower log levels
#   4: Log info and lower (this is the default)
#   5: Verbose logging
#   6: Debug logging
#   7: Extreme logging

loglevel = 4
destination = file

[client]

enable_client = yes
user_interface = text
downloads_path = ~/Downloads
notify_on_new_message = yes

# Announce this peer at startup to let others reach it
announce_at_start = yes

# Try LXMF propagation network if direct delivery fails
try_propagation_on_send_fail = yes

# Periodically sync messages from propagation nodes
periodic_lxmf_sync = yes

# Sync interval in minutes (360 = 6 hours)
lxmf_sync_interval = 360

# Max messages to download per sync (0 = unlimited)
lxmf_sync_limit = 8

# Required stamp cost for inbound messages (0 = disabled)
# stamp_cost = 8

[textui]
# Text UI theme: dark, light
theme = dark

# Editor to use for composing messages
# editor = nano

# Hide guide on startup after first run
hide_guide = no

[node]
# Enable hosting a NomadNet node
enable_node = no

# Node name displayed to visitors
# node_name = My Node

# Enable as LXMF propagation node
enable_propagation = no

# Max message storage in MB
message_storage_limit = 2000
'''
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, 'w') as f:
                f.write(default_config)

            # Fix ownership if running as root
            real_user = self._get_real_username()
            if os.geteuid() == 0 and real_user != 'root':
                subprocess.run(['chown', '-R', f'{real_user}:{real_user}',
                               str(config_file.parent)], capture_output=True)

            print(f"[RNS] Created default NomadNet config: {config_file}", flush=True)
            self.main_window.set_status_message("Created default NomadNet config")
        except Exception as e:
            print(f"[RNS] Failed to create config: {e}", flush=True)

    def _open_nomadnet_folder(self):
        """Open NomadNet config folder"""
        real_home = self._get_real_user_home()
        folder = real_home / ".nomadnetwork"
        self._open_config_folder(folder)

    def _edit_config_terminal(self, config_file):
        """Open config file in terminal with nano/vim"""
        import os

        config_path = Path(config_file)
        print(f"[RNS] Opening config in terminal: {config_path}", flush=True)

        # Get the real user for running commands
        real_user = self._get_real_username()
        is_root = os.geteuid() == 0

        # Create the config file if it doesn't exist (as the real user)
        if not config_path.exists():
            try:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.touch()
                # Fix ownership if running as root
                if is_root and real_user != 'root':
                    subprocess.run(['chown', f'{real_user}:{real_user}', str(config_path)],
                                   capture_output=True)
                    subprocess.run(['chown', f'{real_user}:{real_user}', str(config_path.parent)],
                                   capture_output=True)
                print(f"[RNS] Created config file: {config_path}", flush=True)
            except Exception as e:
                print(f"[RNS] Failed to create config: {e}", flush=True)

        try:
            # When running as root: run terminal as root (has X11), but nano as user
            if is_root and real_user != 'root':
                # Use sudo -i for login shell to get user's environment
                user_cmd = f"sudo -i -u {real_user} nano {config_path}"
                terminals = [
                    ('lxterminal', f'lxterminal -e {user_cmd}'),
                    ('xfce4-terminal', f'xfce4-terminal -e {user_cmd}'),
                    ('gnome-terminal', f'gnome-terminal -- sudo -i -u {real_user} nano {config_path}'),
                    ('konsole', f'konsole -e sudo -i -u {real_user} nano {config_path}'),
                    ('xterm', f'xterm -e sudo -i -u {real_user} nano {config_path}'),
                ]
            else:
                terminals = [
                    ('lxterminal', f'lxterminal -e nano {config_path}'),
                    ('xfce4-terminal', f'xfce4-terminal -e nano {config_path}'),
                    ('gnome-terminal', f'gnome-terminal -- nano {config_path}'),
                    ('konsole', f'konsole -e nano {config_path}'),
                    ('xterm', f'xterm -e nano {config_path}'),
                ]

            for term_name, full_cmd in terminals:
                if shutil.which(term_name):
                    print(f"[RNS] Using terminal: {term_name} (user: {real_user})", flush=True)
                    print(f"[RNS] Command: {full_cmd}", flush=True)
                    subprocess.Popen(full_cmd, shell=True, start_new_session=True)
                    self.main_window.set_status_message(f"Editing {config_path.name} in terminal")
                    return

            self.main_window.set_status_message("No terminal emulator found")
            print("[RNS] No terminal emulator found", flush=True)
        except Exception as e:
            print(f"[RNS] Failed to open terminal editor: {e}", flush=True)
            self.main_window.set_status_message(f"Failed to open editor: {e}")

    def _install_meshtastic_interface(self, button):
        """Download and install Meshtastic_Interface.py"""
        print("[RNS] Installing Meshtastic Interface...", flush=True)
        self.main_window.set_status_message("Downloading Meshtastic Interface...")

        def do_install():
            try:
                import urllib.request

                # Create interfaces directory
                interfaces_dir = Path.home() / ".reticulum" / "interfaces"
                interfaces_dir.mkdir(parents=True, exist_ok=True)

                # Download the interface file
                url = "https://raw.githubusercontent.com/landandair/RNS_Over_Meshtastic/main/Interface/Meshtastic_Interface.py"
                dest = interfaces_dir / "Meshtastic_Interface.py"

                print(f"[RNS] Downloading from {url}", flush=True)
                urllib.request.urlretrieve(url, str(dest))

                print(f"[RNS] Saved to {dest}", flush=True)
                GLib.idle_add(self._install_meshtastic_interface_complete, True, str(dest))

            except Exception as e:
                print(f"[RNS] Failed to install: {e}", flush=True)
                GLib.idle_add(self._install_meshtastic_interface_complete, False, str(e))

        threading.Thread(target=do_install, daemon=True).start()

    def _install_meshtastic_interface_complete(self, success, message):
        """Handle install completion"""
        if success:
            self.main_window.set_status_message("Meshtastic Interface installed")
            self.mesh_iface_status.set_label(f"✓ Installed: {message}")
            print("[RNS] Meshtastic Interface installed successfully", flush=True)
        else:
            self.main_window.set_status_message(f"Install failed: {message}")
            self.mesh_iface_status.set_label(f"✗ Failed: {message}")
        return False

    def _edit_meshtastic_interface(self, button):
        """Edit Meshtastic_Interface.py in terminal"""
        import os

        real_home = self._get_real_user_home()
        iface_file = real_home / ".reticulum" / "interfaces" / "Meshtastic_Interface.py"

        if not iface_file.exists():
            self.main_window.set_status_message("Interface not installed - click 'Install Interface' first")
            print(f"[RNS] Interface file not found: {iface_file}", flush=True)
            return

        print(f"[RNS] Opening interface file in terminal: {iface_file}", flush=True)
        self._edit_config_terminal(iface_file)

    def _add_meshtastic_interface_config(self, button):
        """Add Meshtastic Interface config template to RNS config"""
        print("[RNS] Adding Meshtastic Interface config...", flush=True)

        config_file = Path.home() / ".reticulum" / "config"

        # Config template
        config_template = '''
# Meshtastic Interface - RNS over Meshtastic
# Uncomment and configure ONE connection method (port, ble_port, or tcp_port)
[[Meshtastic Interface]]
  type = Meshtastic_Interface
  enabled = true
  mode = gateway

  # Serial connection (USB)
  port = /dev/ttyUSB0
  # port = /dev/ttyACM0

  # Bluetooth LE connection (alternative)
  # ble_port = short_1234

  # TCP/IP connection (alternative)
  # tcp_port = 127.0.0.1:4403

  # Radio speed: 8=Turbo, 6=ShortFast, 5=ShortSlow, 4=MedFast, 3=MedSlow, 7=LongMod, 1=LongSlow, 0=LongFast
  data_speed = 8
  hop_limit = 1
  bitrate = 500
'''

        try:
            # Check if config file exists
            if not config_file.exists():
                self.main_window.set_status_message("RNS config not found - run rnsd first")
                print("[RNS] Config file not found", flush=True)
                return

            # Read existing config
            with open(config_file, 'r') as f:
                existing_config = f.read()

            # Check if already configured
            if 'Meshtastic Interface' in existing_config:
                self.main_window.set_status_message("Meshtastic Interface already in config")
                print("[RNS] Config already contains Meshtastic Interface", flush=True)
                # Open editor anyway
                self._edit_config_terminal(config_file)
                return

            # Append the template
            with open(config_file, 'a') as f:
                f.write(config_template)

            self.main_window.set_status_message("Meshtastic Interface config added - edit to configure")
            self.mesh_iface_status.set_label("✓ Config template added - edit port settings")
            print("[RNS] Config template added", flush=True)

            # Open in terminal editor to configure
            self._edit_config_terminal(config_file)

        except Exception as e:
            self.main_window.set_status_message(f"Failed: {e}")
            print(f"[RNS] Failed to add config: {e}", flush=True)

    def _refresh_all(self):
        """Refresh all status information"""
        self.main_window.set_status_message("Checking RNS status...")

        def do_refresh():
            # Check RNS service
            rns_installed = self._check_rns_installed()
            rns_running = self._check_rns_service() if rns_installed else False

            # Check component versions
            component_status = {}
            for comp in self.COMPONENTS:
                version = self._get_package_version(comp['package'])
                component_status[comp['name']] = {
                    'installed': version is not None,
                    'version': version or 'Not installed'
                }

            GLib.idle_add(self._update_ui, rns_installed, rns_running, component_status)

        thread = threading.Thread(target=do_refresh)
        thread.daemon = True
        thread.start()

    def _check_rns_installed(self):
        """Check if RNS is installed"""
        try:
            result = subprocess.run(
                ['python3', '-c', 'import RNS'],
                capture_output=True, text=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def _check_rns_service(self):
        """Check if rnsd service is running"""
        try:
            # Check for rnsd process
            result = subprocess.run(
                ['pgrep', '-f', 'rnsd'],
                capture_output=True, text=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def _get_package_version(self, package):
        """Get installed version of a pip package"""
        try:
            result = subprocess.run(
                ['pip3', 'show', package],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('Version:'):
                        return line.split(':', 1)[1].strip()
            return None
        except Exception:
            return None

    def _update_ui(self, rns_installed, rns_running, component_status):
        """Update UI with status information"""
        # Update service status
        if not rns_installed:
            self.rns_status_icon.set_from_icon_name("software-update-available")
            self.rns_status_label.set_label("Not Installed")
            self.rns_status_detail.set_label("Install RNS to enable Reticulum networking")
            self.rns_install_note.set_label("Run: pip3 install rns")
            self.rns_start_btn.set_sensitive(False)
            self.rns_stop_btn.set_sensitive(False)
            self.rns_restart_btn.set_sensitive(False)
        elif rns_running:
            self.rns_status_icon.set_from_icon_name("emblem-default")
            self.rns_status_label.set_label("Running")
            self.rns_status_detail.set_label("Reticulum daemon is active")
            self.rns_install_note.set_label("")
            self.rns_start_btn.set_sensitive(False)
            self.rns_stop_btn.set_sensitive(True)
            self.rns_restart_btn.set_sensitive(True)
        else:
            self.rns_status_icon.set_from_icon_name("dialog-warning")
            self.rns_status_label.set_label("Stopped")
            self.rns_status_detail.set_label("Reticulum daemon is not running")
            self.rns_install_note.set_label("Start with: rnsd")
            self.rns_start_btn.set_sensitive(True)
            self.rns_stop_btn.set_sensitive(False)
            self.rns_restart_btn.set_sensitive(True)

        # Update component rows
        for comp_name, row in self.component_rows.items():
            status = component_status.get(comp_name, {'installed': False, 'version': 'Not installed'})

            if status['installed']:
                row.status_icon.set_from_icon_name("emblem-default")
                row.version_label.set_label(f"v{status['version']}")
                row.action_btn.set_label("Update")
            else:
                row.status_icon.set_from_icon_name("software-update-available")
                row.version_label.set_label("Not installed")
                row.action_btn.set_label("Install")

        self._component_status = component_status
        self.main_window.set_status_message("RNS status updated")
        return False

    def _service_action(self, action):
        """Perform RNS service action"""
        logger.info(f"Service action button clicked: {action}")
        print(f"[RNS] Service action: {action}...", flush=True)

        # Check if rnsd is available
        if not shutil.which('rnsd') and action in ('start', 'restart'):
            self.main_window.set_status_message("rnsd not found - install RNS first")
            print("[RNS] rnsd not found in PATH", flush=True)
            return

        self.main_window.set_status_message(f"{action.capitalize()}ing rnsd...")

        def do_action():
            try:
                if action == "start":
                    # Start rnsd as a background process
                    # rnsd doesn't have a --daemon flag, so we use Popen with detachment
                    print("[RNS] Starting rnsd in background...", flush=True)
                    process = subprocess.Popen(
                        ['rnsd'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    # Give it a moment to start
                    import time
                    time.sleep(1)
                    # Check if process is still running
                    if process.poll() is None:
                        success = True
                        output = f"rnsd started (PID: {process.pid})"
                    else:
                        success = False
                        output = "rnsd exited immediately - check config"
                    print(f"[RNS] Service start: {'OK' if success else 'FAILED'} - {output}", flush=True)
                    GLib.idle_add(self._action_complete, action, success, output)
                    return
                elif action == "stop":
                    # Kill rnsd process
                    print("[RNS] Running: pkill -f rnsd", flush=True)
                    result = subprocess.run(
                        ['pkill', '-f', 'rnsd'],
                        capture_output=True, text=True,
                        timeout=10
                    )
                elif action == "restart":
                    subprocess.run(['pkill', '-f', 'rnsd'], capture_output=True, timeout=10)
                    import time
                    time.sleep(1)
                    # Start rnsd as a background process
                    print("[RNS] Starting rnsd in background...", flush=True)
                    process = subprocess.Popen(
                        ['rnsd'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    time.sleep(1)
                    if process.poll() is None:
                        success = True
                        output = f"rnsd restarted (PID: {process.pid})"
                    else:
                        success = False
                        output = "rnsd exited immediately - check config"
                    print(f"[RNS] Service restart: {'OK' if success else 'FAILED'} - {output}", flush=True)
                    GLib.idle_add(self._action_complete, action, success, output)
                    return

                success = result.returncode == 0 if action != "stop" else True
                output = result.stderr if hasattr(result, 'stderr') else ''
                print(f"[RNS] Service {action}: {'OK' if success else 'FAILED'} - {output[:100]}", flush=True)
                GLib.idle_add(self._action_complete, action, success, output)
            except subprocess.TimeoutExpired:
                print(f"[RNS] Service {action} timed out", flush=True)
                GLib.idle_add(self._action_complete, action, False, "Command timed out")
            except FileNotFoundError as e:
                print(f"[RNS] Command not found: {e}", flush=True)
                GLib.idle_add(self._action_complete, action, False, f"Command not found: {e}")
            except Exception as e:
                print(f"[RNS] Exception: {e}", flush=True)
                GLib.idle_add(self._action_complete, action, False, str(e))

        thread = threading.Thread(target=do_action)
        thread.daemon = True
        thread.start()

    def _action_complete(self, action, success, error):
        """Handle action completion"""
        if success:
            self.main_window.set_status_message(f"rnsd {action}ed successfully")
        else:
            self.main_window.set_status_message(f"Failed to {action} rnsd: {error}")

        self._refresh_all()
        return False

    def _install_component(self, component):
        """Install or update a component"""
        package = component['package']
        logger.info(f"Install button clicked for: {component['display']} ({package})")
        print(f"[RNS] Installing: {component['display']}...", flush=True)  # Console feedback

        # Visual feedback - disable button and update text
        try:
            row = self.component_rows.get(component['name'])
            if row and hasattr(row, 'action_btn'):
                row.action_btn.set_sensitive(False)
                row.action_btn.set_label("Installing...")
        except Exception as e:
            logger.debug(f"Could not update button state: {e}")

        self.main_window.set_status_message(f"Installing {component['display']}...")

        def do_install():
            try:
                import os
                is_root = os.geteuid() == 0
                real_user = self._get_real_username()

                # Build pip install command
                pip_args = ['pip', 'install', '--upgrade', '--user',
                           '--no-cache-dir', '--break-system-packages', package]

                # When running as root, install as the real user
                if is_root and real_user != 'root':
                    # Use sudo -i -u to get user's environment and install to their home
                    cmd = ['sudo', '-i', '-u', real_user] + pip_args
                    print(f"[RNS] Installing as user {real_user}: {' '.join(cmd)}", flush=True)
                else:
                    # Running as normal user, use python -m pip
                    import sys
                    cmd = [sys.executable, '-m'] + pip_args
                    print(f"[RNS] Running: {' '.join(cmd)}", flush=True)

                result = subprocess.run(
                    cmd,
                    capture_output=True, text=True,
                    timeout=180  # 3 minute timeout for slow networks
                )
                output = result.stdout + result.stderr
                success = result.returncode == 0
                print(f"[RNS] pip install result: {'OK' if success else 'FAILED'}", flush=True)
                if not success:
                    print(f"[RNS] Error: {output[:200]}", flush=True)
                else:
                    print(f"[RNS] Install completed successfully", flush=True)
                GLib.idle_add(self._install_complete, component['display'], success, output)
            except subprocess.TimeoutExpired:
                print(f"[RNS] Install timed out after 180s", flush=True)
                GLib.idle_add(self._install_complete, component['display'], False, "Installation timed out")
            except Exception as e:
                print(f"[RNS] Exception during install: {e}", flush=True)
                GLib.idle_add(self._install_complete, component['display'], False, str(e))

        thread = threading.Thread(target=do_install)
        thread.daemon = True
        thread.start()

    def _install_complete(self, name, success, error):
        """Handle install completion"""
        # Re-enable the button for this component
        for comp in self.COMPONENTS:
            if comp['display'] == name:
                row = self.component_rows.get(comp['name'])
                if row and hasattr(row, 'action_btn'):
                    row.action_btn.set_sensitive(True)
                break

        if success:
            msg = f"{name} installed successfully"
            print(f"[RNS] {msg}", flush=True)
            self.main_window.set_status_message(msg)
        else:
            short_error = str(error)[:80] if error else "Unknown error"
            msg = f"Failed to install {name}: {short_error}"
            print(f"[RNS] {msg}", flush=True)
            self.main_window.set_status_message(msg)

        # Refresh status after a short delay to not overwrite the message
        GLib.timeout_add(2000, self._refresh_all)
        return False

    def _on_install_all(self, button):
        """Install all RNS components"""
        logger.info("Install All button clicked")
        print("[RNS] Installing all components...", flush=True)  # Console feedback

        # Disable button during install
        button.set_sensitive(False)
        button.set_label("Installing...")

        self.main_window.set_status_message("Installing all RNS components...")

        def do_install_all():
            packages = [c['package'] for c in self.COMPONENTS]
            try:
                import os
                is_root = os.geteuid() == 0
                real_user = self._get_real_username()

                # Build pip install command
                pip_args = ['pip', 'install', '--upgrade', '--user',
                           '--no-cache-dir', '--break-system-packages'] + packages

                # When running as root, install as the real user
                if is_root and real_user != 'root':
                    cmd = ['sudo', '-i', '-u', real_user] + pip_args
                    print(f"[RNS] Installing as user {real_user}: {' '.join(cmd)}", flush=True)
                else:
                    import sys
                    cmd = [sys.executable, '-m'] + pip_args
                    print(f"[RNS] Running: {' '.join(cmd)}", flush=True)

                result = subprocess.run(
                    cmd,
                    capture_output=True, text=True,
                    timeout=300  # 5 minute timeout for all packages
                )
                output = result.stdout + result.stderr
                success = result.returncode == 0
                print(f"[RNS] pip install all result: {'OK' if success else 'FAILED'}", flush=True)
                if not success:
                    print(f"[RNS] Error: {output[:300]}", flush=True)
                GLib.idle_add(self._install_all_complete, button, success, output)
            except subprocess.TimeoutExpired:
                GLib.idle_add(self._install_all_complete, button, False, "Installation timed out")
            except Exception as e:
                print(f"[RNS] Exception during install all: {e}", flush=True)
                GLib.idle_add(self._install_all_complete, button, False, str(e))

        thread = threading.Thread(target=do_install_all)
        thread.daemon = True
        thread.start()

    def _install_all_complete(self, button, success, error):
        """Handle install all completion"""
        # Re-enable button
        button.set_sensitive(True)
        button.set_label("Install All")

        if success:
            msg = "All RNS components installed successfully"
            print(f"[RNS] {msg}", flush=True)
            self.main_window.set_status_message(msg)
        else:
            short_error = str(error)[:100] if error else "Unknown error"
            msg = f"Install failed: {short_error}"
            print(f"[RNS] {msg}", flush=True)
            self.main_window.set_status_message(msg)

        # Refresh status after a short delay to not overwrite the message
        GLib.timeout_add(2000, self._refresh_all)
        return False

    def _on_update_all(self, button):
        """Update all installed RNS components"""
        # Same as install all with --upgrade
        self._on_install_all(button)

    # ========================================
    # Gateway Control Methods
    # ========================================

    def _update_gateway_status(self):
        """Update gateway status display"""
        if self._gateway_bridge and self._gateway_bridge.is_running:
            status = self._gateway_bridge.get_status()
            self.gateway_status_icon.set_from_icon_name("network-transmit-receive-symbolic")
            self.gateway_status_label.set_label("Gateway: Running")

            mesh_status = "Connected" if status['meshtastic_connected'] else "Disconnected"
            rns_status = "Connected" if status['rns_connected'] else "Disconnected"
            self.gateway_detail_label.set_label(f"Mesh: {mesh_status} | RNS: {rns_status}")

            self.gateway_start_btn.set_sensitive(False)
            self.gateway_stop_btn.set_sensitive(True)

            # Update connection indicators
            if status['meshtastic_connected']:
                self.mesh_conn_icon.set_from_icon_name("emblem-default-symbolic")
                self.mesh_conn_label.set_label("Meshtastic: Connected")
            else:
                self.mesh_conn_icon.set_from_icon_name("dialog-warning-symbolic")
                self.mesh_conn_label.set_label("Meshtastic: Disconnected")

            if status['rns_connected']:
                self.rns_conn_icon.set_from_icon_name("emblem-default-symbolic")
                self.rns_conn_label.set_label("RNS: Connected")
            else:
                self.rns_conn_icon.set_from_icon_name("dialog-warning-symbolic")
                self.rns_conn_label.set_label("RNS: Disconnected")

            # Update statistics
            stats = status.get('statistics', {})
            node_stats = status.get('node_stats', {})
            self.stats_labels["Messages Mesh→RNS"].set_label(str(stats.get('messages_mesh_to_rns', 0)))
            self.stats_labels["Messages RNS→Mesh"].set_label(str(stats.get('messages_rns_to_mesh', 0)))
            self.stats_labels["Total Nodes"].set_label(str(node_stats.get('total', 0)))
            self.stats_labels["Errors"].set_label(str(stats.get('errors', 0)))

        else:
            self.gateway_status_icon.set_from_icon_name("network-offline-symbolic")
            self.gateway_status_label.set_label("Gateway: Stopped")
            self.gateway_detail_label.set_label("Not running")
            self.gateway_start_btn.set_sensitive(True)
            self.gateway_stop_btn.set_sensitive(False)

            self.mesh_conn_icon.set_from_icon_name("dialog-question-symbolic")
            self.mesh_conn_label.set_label("Meshtastic: Unknown")
            self.rns_conn_icon.set_from_icon_name("dialog-question-symbolic")
            self.rns_conn_label.set_label("RNS: Unknown")

    def _on_gateway_start(self, button):
        """Start the gateway bridge"""
        print("[RNS] Starting gateway...", flush=True)
        self.main_window.set_status_message("Starting gateway...")

        def do_start():
            try:
                from gateway.rns_bridge import RNSMeshtasticBridge
                from gateway.config import GatewayConfig

                config = GatewayConfig.load()
                config.enabled = True
                config.save()

                self._gateway_bridge = RNSMeshtasticBridge(config)
                success = self._gateway_bridge.start()
                print(f"[RNS] Gateway start: {'OK' if success else 'FAILED'}", flush=True)

                GLib.idle_add(self._gateway_start_complete, success)
            except ImportError as e:
                print(f"[RNS] Gateway start failed - missing module: {e}", flush=True)
                GLib.idle_add(self._gateway_start_complete, False, f"Missing module: {e}")
            except Exception as e:
                print(f"[RNS] Gateway start exception: {e}", flush=True)
                GLib.idle_add(self._gateway_start_complete, False, str(e))

        thread = threading.Thread(target=do_start)
        thread.daemon = True
        thread.start()

    def _gateway_start_complete(self, success, error=None):
        """Handle gateway start completion"""
        if success:
            self.main_window.set_status_message("Gateway started successfully")
            self.gateway_enable_switch.set_active(True)
        else:
            self.main_window.set_status_message(f"Failed to start gateway: {error}")

        self._update_gateway_status()
        return False

    def _on_gateway_stop(self, button):
        """Stop the gateway bridge"""
        print("[RNS] Stopping gateway...", flush=True)
        self.main_window.set_status_message("Stopping gateway...")

        if self._gateway_bridge:
            self._gateway_bridge.stop()
            self._gateway_bridge = None
            print("[RNS] Gateway stopped", flush=True)
        else:
            print("[RNS] No gateway running", flush=True)

        self.main_window.set_status_message("Gateway stopped")
        self._update_gateway_status()

    def _on_gateway_enable_changed(self, switch, state):
        """Handle gateway enable switch toggle"""
        try:
            from gateway.config import GatewayConfig
            config = GatewayConfig.load()
            config.enabled = state
            config.save()
        except ImportError:
            pass
        return False

    def _on_test_gateway(self, button):
        """Test gateway connections"""
        print("[RNS] Testing gateway connections...", flush=True)
        self.main_window.set_status_message("Testing connections...")

        def do_test():
            results = {
                'meshtastic': {'connected': False, 'error': None},
                'rns': {'connected': False, 'error': None},
            }

            # Test Meshtastic
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex(('localhost', 4403))
                sock.close()
                results['meshtastic']['connected'] = (result == 0)
                if result != 0:
                    results['meshtastic']['error'] = "Cannot connect to port 4403"
            except Exception as e:
                results['meshtastic']['error'] = str(e)

            # Test RNS
            try:
                import subprocess
                result = subprocess.run(
                    ['python3', '-c', 'import RNS; print("OK")'],
                    capture_output=True, text=True, timeout=5
                )
                results['rns']['connected'] = (result.returncode == 0)
                if result.returncode != 0:
                    results['rns']['error'] = "RNS not installed"
            except Exception as e:
                results['rns']['error'] = str(e)

            GLib.idle_add(self._test_complete, results)

        thread = threading.Thread(target=do_test)
        thread.daemon = True
        thread.start()

    def _test_complete(self, results):
        """Handle test completion"""
        mesh_ok = results['meshtastic']['connected']
        rns_ok = results['rns']['connected']
        print(f"[RNS] Test results - Meshtastic: {'OK' if mesh_ok else 'FAIL'}, RNS: {'OK' if rns_ok else 'FAIL'}", flush=True)

        # Update icons
        if mesh_ok:
            self.mesh_conn_icon.set_from_icon_name("emblem-default-symbolic")
            self.mesh_conn_label.set_label("Meshtastic: OK")
        else:
            self.mesh_conn_icon.set_from_icon_name("dialog-error-symbolic")
            self.mesh_conn_label.set_label(f"Meshtastic: {results['meshtastic']['error'] or 'Failed'}")

        if rns_ok:
            self.rns_conn_icon.set_from_icon_name("emblem-default-symbolic")
            self.rns_conn_label.set_label("RNS: OK")
        else:
            self.rns_conn_icon.set_from_icon_name("dialog-error-symbolic")
            self.rns_conn_label.set_label(f"RNS: {results['rns']['error'] or 'Failed'}")

        status = "Meshtastic: " + ("OK" if mesh_ok else "FAIL")
        status += " | RNS: " + ("OK" if rns_ok else "FAIL")
        self.main_window.set_status_message(f"Test complete - {status}")

        return False

    def _on_configure_gateway(self, button):
        """Open gateway configuration dialog"""
        try:
            from ..dialogs.gateway_config import GatewayConfigDialog
            dialog = GatewayConfigDialog(self.main_window)
            dialog.present()
        except ImportError as e:
            # Fallback if dialog not available
            dialog = Adw.MessageDialog(
                transient_for=self.main_window,
                heading="Gateway Configuration",
                body=f"Config editor not available: {e}\n\n"
                     "Config file: ~/.config/meshforge/gateway.json"
            )
            dialog.add_response("ok", "OK")
            dialog.present()

    def _on_edit_gateway_terminal(self, button):
        """Edit gateway config in terminal with nano"""
        import os

        real_home = self._get_real_user_home()
        config_file = real_home / ".config" / "meshforge" / "gateway.json"

        print(f"[RNS] Opening gateway config in terminal: {config_file}", flush=True)

        # Create default config if it doesn't exist
        if not config_file.exists():
            try:
                config_file.parent.mkdir(parents=True, exist_ok=True)
                default_config = {
                    "enabled": False,
                    "meshtastic": {
                        "host": "localhost",
                        "port": 4403
                    },
                    "rns": {
                        "config_dir": "",
                        "announce_interval": 300
                    },
                    "telemetry": {
                        "enabled": True,
                        "interval": 60
                    },
                    "routing": {
                        "rules": []
                    }
                }
                import json
                config_file.write_text(json.dumps(default_config, indent=2))

                # Fix ownership if running as root
                real_user = self._get_real_username()
                is_root = os.geteuid() == 0
                if is_root and real_user != 'root':
                    subprocess.run(['chown', '-R', f'{real_user}:{real_user}', str(config_file.parent)],
                                   capture_output=True)

                print(f"[RNS] Created default gateway config: {config_file}", flush=True)
            except Exception as e:
                print(f"[RNS] Failed to create gateway config: {e}", flush=True)

        # Open in terminal editor
        self._edit_config_terminal(config_file)

    def _on_view_nodes(self, button):
        """Show tracked nodes from both networks"""
        if self._gateway_bridge:
            nodes = self._gateway_bridge.node_tracker.get_all_nodes()
            stats = self._gateway_bridge.node_tracker.get_stats()

            body = f"Total Nodes: {stats['total']}\n"
            body += f"Meshtastic: {stats['meshtastic']}\n"
            body += f"RNS: {stats['rns']}\n"
            body += f"Online: {stats['online']}\n"
            body += f"With Position: {stats['with_position']}\n\n"

            if nodes:
                body += "Recent Nodes:\n"
                for node in sorted(nodes, key=lambda n: n.last_seen or datetime.min, reverse=True)[:10]:
                    body += f"  - {node.name} ({node.network}) - {node.get_age_string()}\n"
        else:
            body = "Gateway not running. Start the gateway to track nodes."

        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading="Tracked Nodes",
            body=body
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def _on_setup_gateway(self, button):
        """Open gateway setup wizard"""
        self._on_configure_gateway(button)

    def _open_config_folder(self, path):
        """Open config folder in file manager"""
        try:
            subprocess.run(['xdg-open', str(path)])
        except Exception as e:
            self.main_window.set_status_message(f"Failed to open folder: {e}")

    def _open_rns_config_dialog(self):
        """Open the RNS configuration editor dialog"""
        print("[RNS] Opening RNS config dialog...", flush=True)
        try:
            from ..dialogs.rns_config import RNSConfigDialog
            dialog = RNSConfigDialog(self.main_window)
            dialog.present()
            print("[RNS] Config dialog opened", flush=True)
        except ImportError as e:
            print(f"[RNS] Config dialog import failed: {e}", flush=True)
            # Fallback if dialog not available
            dialog = Adw.MessageDialog(
                transient_for=self.main_window,
                heading="Configuration Editor",
                body=f"Config editor not available: {e}\n\n"
                     "Config file: ~/.reticulum/config"
            )
            dialog.add_response("ok", "OK")
            dialog.present()

    def _edit_config(self, config_file):
        """Open config file in editor"""
        print(f"[RNS] Opening config: {config_file}", flush=True)
        try:
            # Try GUI editors only (no terminal editors like nano/vim)
            gui_editors = ['gedit', 'kate', 'xed', 'mousepad', 'pluma', 'featherpad']
            for editor in gui_editors:
                if shutil.which(editor):
                    print(f"[RNS] Using editor: {editor}", flush=True)
                    subprocess.Popen([editor, str(config_file)])
                    return
            # Fallback to xdg-open
            print("[RNS] Using xdg-open", flush=True)
            subprocess.run(['xdg-open', str(config_file)])
        except Exception as e:
            print(f"[RNS] Failed to open editor: {e}", flush=True)
            self.main_window.set_status_message(f"Failed to open editor: {e}")
