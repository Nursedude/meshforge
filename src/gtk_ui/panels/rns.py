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

        config_btn = Gtk.Button(label="Configure Gateway")
        config_btn.connect("clicked", self._on_configure_gateway)
        action_row.append(config_btn)

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

        # Config file location
        config_path = Path.home() / ".reticulum"

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
        ext_edit_btn = Gtk.Button(label="Edit (External)")
        ext_edit_btn.set_tooltip_text("Open in external text editor")
        ext_edit_btn.connect("clicked", lambda b: self._edit_config(config_file))
        file_row.append(ext_edit_btn)

        # Edit with built-in config editor
        config_edit_btn = Gtk.Button(label="Config Editor")
        config_edit_btn.add_css_class("suggested-action")
        config_edit_btn.set_tooltip_text("Open in MeshForge config editor with templates")
        config_edit_btn.connect("clicked", lambda b: self._open_rns_config_dialog())
        file_row.append(config_edit_btn)

        box.append(file_row)

        if not config_file.exists():
            note_label = Gtk.Label(
                label="No config file exists yet. Click 'Config Editor' to create one with templates."
            )
            note_label.add_css_class("dim-label")
            note_label.set_xalign(0)
            note_label.set_wrap(True)
            box.append(note_label)

        frame.set_child(box)
        parent.append(frame)

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
                    # Start rnsd in daemon mode
                    print("[RNS] Running: rnsd --daemon", flush=True)
                    result = subprocess.run(
                        ['rnsd', '--daemon'],
                        capture_output=True, text=True,
                        timeout=30
                    )
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
                    print("[RNS] Running: rnsd --daemon", flush=True)
                    result = subprocess.run(
                        ['rnsd', '--daemon'],
                        capture_output=True, text=True,
                        timeout=30
                    )

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
                # Use python -m pip for better reliability
                import sys
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '--upgrade', '--user', package],
                    capture_output=True, text=True,
                    timeout=120  # 2 minute timeout
                )
                output = result.stdout + result.stderr
                success = result.returncode == 0
                print(f"[RNS] pip install result: {'OK' if success else 'FAILED'}", flush=True)
                if not success:
                    print(f"[RNS] Error: {output[:200]}", flush=True)
                GLib.idle_add(self._install_complete, component['display'], success, output)
            except subprocess.TimeoutExpired:
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
                import sys
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '--upgrade', '--user'] + packages,
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
