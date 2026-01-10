"""
RNS Ecosystem Components Section for RNS Panel

Install and manage RNS ecosystem packages (rns, lxmf, nomadnet, rnodeconf, meshchat).
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import shutil
import sys
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class ComponentsMixin:
    """
    Mixin class providing RNS component management for RNSPanel.

    Expects the panel to have:
    - main_window: Reference to main application window
    - _get_real_username(): Method to get real username
    - COMPONENTS: List of RNS components
    """

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

    def _check_systemd_service_exists(self):
        """Check if rnsd.service file exists"""
        return os.path.exists('/etc/systemd/system/rnsd.service')

    def _get_systemd_service_status(self):
        """Get rnsd systemd service status: 'active', 'inactive', 'not-found'"""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'rnsd'],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            if status == 'active':
                return 'active'
            elif status in ('inactive', 'failed'):
                return 'inactive'
            else:
                return 'not-found'
        except Exception:
            return 'not-found'

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
            # Check if running via systemd
            service_status = self._get_systemd_service_status()
            if service_status == 'active':
                self.rns_status_detail.set_label("rnsd running (systemd service)")
            else:
                self.rns_status_detail.set_label("rnsd running (process)")
            self.rns_install_note.set_label("")
            self.rns_start_btn.set_sensitive(False)
            self.rns_stop_btn.set_sensitive(True)
            self.rns_restart_btn.set_sensitive(True)
        else:
            self.rns_status_icon.set_from_icon_name("dialog-warning")
            self.rns_status_label.set_label("Stopped")
            service_status = self._get_systemd_service_status()
            if service_status == 'inactive':
                self.rns_status_detail.set_label("rnsd stopped (systemd service installed)")
            elif service_status == 'not-found':
                self.rns_status_detail.set_label("rnsd not running (no systemd service)")
            else:
                self.rns_status_detail.set_label("Reticulum daemon is not running")
            self.rns_install_note.set_label("Start with: rnsd")
            self.rns_start_btn.set_sensitive(True)
            self.rns_stop_btn.set_sensitive(False)
            self.rns_restart_btn.set_sensitive(True)

        # Update Install Service button based on whether service exists
        service_exists = self._check_systemd_service_exists()
        if service_exists:
            self.rns_install_service_btn.set_label("Remove Service")
            self.rns_install_service_btn.set_tooltip_text("Remove systemd service (rnsd will not start on boot)")
        else:
            self.rns_install_service_btn.set_label("Install Service")
            self.rns_install_service_btn.set_tooltip_text("Create systemd service for rnsd (persistent across reboots)")

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

    def _install_component(self, component):
        """Install or update a component"""
        package = component['package']
        logger.info(f"Install button clicked for: {component['display']} ({package})")
        logger.debug(f"[RNS] Installing: {component['display']}...")

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
                is_root = os.geteuid() == 0
                real_user = self._get_real_username()

                # Build pip install command
                pip_args = ['pip', 'install', '--upgrade', '--user',
                           '--no-cache-dir', '--break-system-packages', package]

                # When running as root, install as the real user
                if is_root and real_user != 'root':
                    # Use sudo -i -u to get user's environment and install to their home
                    cmd = ['sudo', '-i', '-u', real_user] + pip_args
                    logger.debug(f"[RNS] Installing as user {real_user}: {' '.join(cmd)}")
                else:
                    # Running as normal user, use python -m pip
                    cmd = [sys.executable, '-m'] + pip_args
                    logger.debug(f"[RNS] Running: {' '.join(cmd)}")

                result = subprocess.run(
                    cmd,
                    capture_output=True, text=True,
                    timeout=180  # 3 minute timeout for slow networks
                )
                output = result.stdout + result.stderr
                success = result.returncode == 0
                logger.debug(f"[RNS] pip install result: {'OK' if success else 'FAILED'}")
                if not success:
                    logger.debug(f"[RNS] Error: {output[:200]}")
                else:
                    logger.debug(f"[RNS] Install completed successfully")
                GLib.idle_add(self._install_complete, component['display'], success, output)
            except subprocess.TimeoutExpired:
                logger.debug(f"[RNS] Install timed out after 180s")
                GLib.idle_add(self._install_complete, component['display'], False, "Installation timed out")
            except Exception as e:
                logger.debug(f"[RNS] Exception during install: {e}")
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
            logger.debug(f"[RNS] {msg}")
            self.main_window.set_status_message(msg)
        else:
            short_error = str(error)[:80] if error else "Unknown error"
            msg = f"Failed to install {name}: {short_error}"
            logger.debug(f"[RNS] {msg}")
            self.main_window.set_status_message(msg)

        # Refresh status after a short delay to not overwrite the message
        GLib.timeout_add(2000, self._refresh_all)
        return False

    def _on_install_all(self, button):
        """Install all RNS components"""
        logger.info("Install All button clicked")
        logger.debug("[RNS] Installing all components...")

        # Disable button during install
        button.set_sensitive(False)
        button.set_label("Installing...")

        self.main_window.set_status_message("Installing all RNS components...")

        def do_install_all():
            packages = [c['package'] for c in self.COMPONENTS]
            try:
                is_root = os.geteuid() == 0
                real_user = self._get_real_username()

                # Build pip install command
                pip_args = ['pip', 'install', '--upgrade', '--user',
                           '--no-cache-dir', '--break-system-packages'] + packages

                # When running as root, install as the real user
                if is_root and real_user != 'root':
                    cmd = ['sudo', '-i', '-u', real_user] + pip_args
                    logger.debug(f"[RNS] Installing as user {real_user}: {' '.join(cmd)}")
                else:
                    cmd = [sys.executable, '-m'] + pip_args
                    logger.debug(f"[RNS] Running: {' '.join(cmd)}")

                result = subprocess.run(
                    cmd,
                    capture_output=True, text=True,
                    timeout=300  # 5 minute timeout for all packages
                )
                output = result.stdout + result.stderr
                success = result.returncode == 0
                logger.debug(f"[RNS] pip install all result: {'OK' if success else 'FAILED'}")
                if not success:
                    logger.debug(f"[RNS] Error: {output[:300]}")
                GLib.idle_add(self._install_all_complete, button, success, output)
            except subprocess.TimeoutExpired:
                GLib.idle_add(self._install_all_complete, button, False, "Installation timed out")
            except Exception as e:
                logger.debug(f"[RNS] Exception during install all: {e}")
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
            logger.debug(f"[RNS] {msg}")
            self.main_window.set_status_message(msg)
        else:
            short_error = str(error)[:100] if error else "Unknown error"
            msg = f"Install failed: {short_error}"
            logger.debug(f"[RNS] {msg}")
            self.main_window.set_status_message(msg)

        # Refresh status after a short delay to not overwrite the message
        GLib.timeout_add(2000, self._refresh_all)
        return False

    def _on_update_all(self, button):
        """Update all installed RNS components"""
        # Same as install all with --upgrade
        self._on_install_all(button)

    def _service_action(self, action):
        """Perform RNS service action"""
        logger.info(f"Service action button clicked: {action}")
        logger.debug(f"[RNS] Service action: {action}...")

        # Check if rnsd is available
        if not shutil.which('rnsd') and action in ('start', 'restart'):
            self.main_window.set_status_message("rnsd not found - install RNS first")
            logger.debug("[RNS] rnsd not found in PATH")
            return

        self.main_window.set_status_message(f"{action.capitalize()}ing rnsd...")

        def do_action():
            try:
                if action == "start":
                    # Start rnsd as a background process
                    logger.debug("[RNS] Starting rnsd in background...")
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
                    logger.debug(f"[RNS] Service start: {'OK' if success else 'FAILED'} - {output}")
                    GLib.idle_add(self._action_complete, action, success, output)
                    return
                elif action == "stop":
                    # Kill rnsd process
                    logger.debug("[RNS] Running: pkill -f rnsd")
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
                    logger.debug("[RNS] Starting rnsd in background...")
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
                    logger.debug(f"[RNS] Service restart: {'OK' if success else 'FAILED'} - {output}")
                    GLib.idle_add(self._action_complete, action, success, output)
                    return

                success = result.returncode == 0 if action != "stop" else True
                output = result.stderr if hasattr(result, 'stderr') else ''
                logger.debug(f"[RNS] Service {action}: {'OK' if success else 'FAILED'} - {output[:100]}")
                GLib.idle_add(self._action_complete, action, success, output)
            except subprocess.TimeoutExpired:
                logger.debug(f"[RNS] Service {action} timed out")
                GLib.idle_add(self._action_complete, action, False, "Command timed out")
            except FileNotFoundError as e:
                logger.debug(f"[RNS] Command not found: {e}")
                GLib.idle_add(self._action_complete, action, False, f"Command not found: {e}")
            except Exception as e:
                logger.debug(f"[RNS] Exception: {e}")
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

    def _install_rnsd_service(self, button):
        """Create/remove systemd service for rnsd"""
        service_exists = self._check_systemd_service_exists()

        if service_exists:
            # Remove service
            self.main_window.set_status_message("Removing rnsd systemd service...")
            button.set_sensitive(False)

            def do_remove():
                try:
                    subprocess.run(['systemctl', 'stop', 'rnsd'], timeout=30)
                    subprocess.run(['systemctl', 'disable', 'rnsd'], timeout=30)
                    os.remove('/etc/systemd/system/rnsd.service')
                    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
                    GLib.idle_add(self._install_service_complete, True, "Service removed")
                except PermissionError:
                    GLib.idle_add(self._install_service_complete, False, "Permission denied - run MeshForge as root")
                except Exception as e:
                    GLib.idle_add(self._install_service_complete, False, str(e))

            threading.Thread(target=do_remove, daemon=True).start()
        else:
            # Install service
            self.main_window.set_status_message("Installing rnsd systemd service...")
            button.set_sensitive(False)

            def do_install():
                try:
                    # Find rnsd path
                    rnsd_path = shutil.which('rnsd')
                    if not rnsd_path:
                        rnsd_path = '/usr/local/bin/rnsd'

                    service_content = f'''[Unit]
Description=Reticulum Network Stack Daemon
After=network.target

[Service]
Type=simple
ExecStart={rnsd_path}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
'''
                    service_path = '/etc/systemd/system/rnsd.service'

                    # Write service file
                    with open(service_path, 'w') as f:
                        f.write(service_content)

                    # Reload systemd and enable service
                    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
                    subprocess.run(['systemctl', 'enable', 'rnsd'], check=True, timeout=30)
                    subprocess.run(['systemctl', 'start', 'rnsd'], check=True, timeout=30)

                    GLib.idle_add(self._install_service_complete, True, "Service installed and started")
                except PermissionError:
                    GLib.idle_add(self._install_service_complete, False, "Permission denied - run MeshForge as root")
                except subprocess.CalledProcessError as e:
                    GLib.idle_add(self._install_service_complete, False, f"systemctl failed: {e}")
                except Exception as e:
                    GLib.idle_add(self._install_service_complete, False, str(e))

            threading.Thread(target=do_install, daemon=True).start()

    def _install_service_complete(self, success, message):
        """Handle service installation/removal completion"""
        self.rns_install_service_btn.set_sensitive(True)
        if success:
            self.main_window.set_status_message(f"rnsd service: {message}")
        else:
            self.main_window.set_status_message(f"Failed: {message}")
        # Refresh will update button label based on current state
        self._refresh_all()
        return False
