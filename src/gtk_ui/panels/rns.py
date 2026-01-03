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

        # Gateway status
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        self.gateway_status_label = Gtk.Label(label="Gateway: Not Configured")
        self.gateway_status_label.set_xalign(0)
        status_row.append(self.gateway_status_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        setup_btn = Gtk.Button(label="Setup Gateway")
        setup_btn.connect("clicked", self._on_setup_gateway)
        setup_btn.set_sensitive(False)  # Disable until implemented
        status_row.append(setup_btn)

        box.append(status_row)

        # Coming soon note
        note = Gtk.Label(label="Gateway integration coming in future release")
        note.add_css_class("dim-label")
        note.set_xalign(0)
        box.append(note)

        frame.set_child(box)
        parent.append(frame)

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
        if config_file.exists():
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

            edit_btn = Gtk.Button(label="Edit")
            edit_btn.connect("clicked", lambda b: self._edit_config(config_file))
            file_row.append(edit_btn)

            box.append(file_row)
        else:
            no_config = Gtk.Label(label="No RNS configuration found. Install RNS first.")
            no_config.add_css_class("dim-label")
            no_config.set_xalign(0)
            box.append(no_config)

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
        self.main_window.set_status_message(f"{action.capitalize()}ing rnsd...")

        def do_action():
            try:
                if action == "start":
                    # Start rnsd in daemon mode
                    result = subprocess.run(
                        ['rnsd', '--daemon'],
                        capture_output=True, text=True
                    )
                elif action == "stop":
                    # Kill rnsd process
                    result = subprocess.run(
                        ['pkill', '-f', 'rnsd'],
                        capture_output=True, text=True
                    )
                elif action == "restart":
                    subprocess.run(['pkill', '-f', 'rnsd'], capture_output=True)
                    import time
                    time.sleep(1)
                    result = subprocess.run(
                        ['rnsd', '--daemon'],
                        capture_output=True, text=True
                    )

                success = result.returncode == 0 if action != "stop" else True
                GLib.idle_add(self._action_complete, action, success, result.stderr if hasattr(result, 'stderr') else '')
            except Exception as e:
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
        self.main_window.set_status_message(f"Installing {component['display']}...")

        def do_install():
            try:
                result = subprocess.run(
                    ['pip3', 'install', '--upgrade', package],
                    capture_output=True, text=True
                )
                success = result.returncode == 0
                GLib.idle_add(self._install_complete, component['display'], success, result.stderr)
            except Exception as e:
                GLib.idle_add(self._install_complete, component['display'], False, str(e))

        thread = threading.Thread(target=do_install)
        thread.daemon = True
        thread.start()

    def _install_complete(self, name, success, error):
        """Handle install completion"""
        if success:
            self.main_window.set_status_message(f"{name} installed successfully")
        else:
            self.main_window.set_status_message(f"Failed to install {name}: {error}")

        self._refresh_all()
        return False

    def _on_install_all(self, button):
        """Install all RNS components"""
        self.main_window.set_status_message("Installing all RNS components...")

        def do_install_all():
            packages = [c['package'] for c in self.COMPONENTS]
            try:
                result = subprocess.run(
                    ['pip3', 'install', '--upgrade'] + packages,
                    capture_output=True, text=True
                )
                success = result.returncode == 0
                GLib.idle_add(self._install_complete, "All components", success, result.stderr)
            except Exception as e:
                GLib.idle_add(self._install_complete, "All components", False, str(e))

        thread = threading.Thread(target=do_install_all)
        thread.daemon = True
        thread.start()

    def _on_update_all(self, button):
        """Update all installed RNS components"""
        # Same as install all with --upgrade
        self._on_install_all(button)

    def _on_setup_gateway(self, button):
        """Open gateway setup wizard"""
        # TODO: Implement gateway setup wizard
        self.main_window.set_status_message("Gateway setup wizard coming soon")

    def _open_config_folder(self, path):
        """Open config folder in file manager"""
        try:
            subprocess.run(['xdg-open', str(path)])
        except Exception as e:
            self.main_window.set_status_message(f"Failed to open folder: {e}")

    def _edit_config(self, config_file):
        """Open config file in editor"""
        try:
            # Try common editors
            editors = ['gedit', 'kate', 'xed', 'mousepad', 'nano', 'vim']
            for editor in editors:
                if shutil.which(editor):
                    subprocess.Popen([editor, str(config_file)])
                    return
            # Fallback to xdg-open
            subprocess.run(['xdg-open', str(config_file)])
        except Exception as e:
            self.main_window.set_status_message(f"Failed to open editor: {e}")
