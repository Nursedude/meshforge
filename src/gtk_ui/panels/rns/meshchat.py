"""
MeshChat Web Interface Section for RNS Panel

Provides web-based encrypted messaging interface for LXMF over Reticulum.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import shutil
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class MeshChatMixin:
    """
    Mixin class providing MeshChat functionality for RNSPanel.

    Expects the panel to have:
    - main_window: Reference to main application window
    - _get_real_user_home(): Method to get real user's home directory
    - _get_real_username(): Method to get real username
    - _show_info(msg): Method to show info toast
    - _show_error(msg): Method to show error toast
    """

    def _build_meshchat_section(self, parent):
        """Build MeshChat web interface section"""
        frame = Gtk.Frame()
        frame.set_label("MeshChat Web Interface")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Description
        desc = Gtk.Label(label="Web-based encrypted messaging interface for LXMF over Reticulum")
        desc.set_xalign(0)
        desc.set_wrap(True)
        desc.add_css_class("dim-label")
        box.append(desc)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.meshchat_status_icon = Gtk.Image.new_from_icon_name("emblem-question")
        self.meshchat_status_icon.set_pixel_size(20)
        status_row.append(self.meshchat_status_icon)

        self.meshchat_status_label = Gtk.Label(label="Checking...")
        self.meshchat_status_label.set_xalign(0)
        status_row.append(self.meshchat_status_label)

        # Port display
        self.meshchat_port_label = Gtk.Label(label="")
        self.meshchat_port_label.set_xalign(0)
        self.meshchat_port_label.add_css_class("dim-label")
        status_row.append(self.meshchat_port_label)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh status")
        refresh_btn.connect("clicked", lambda b: self._check_meshchat_status())
        status_row.append(refresh_btn)

        box.append(status_row)

        # Launch buttons row
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_halign(Gtk.Align.CENTER)

        # Start MeshChat server
        self.meshchat_start_btn = Gtk.Button(label="Start Server")
        self.meshchat_start_btn.add_css_class("suggested-action")
        self.meshchat_start_btn.set_tooltip_text("Start MeshChat web server (default port 5555)")
        self.meshchat_start_btn.connect("clicked", self._on_meshchat_start)
        btn_row.append(self.meshchat_start_btn)

        # Open in browser
        self.meshchat_browser_btn = Gtk.Button(label="Open in Browser")
        self.meshchat_browser_btn.set_tooltip_text("Open MeshChat in web browser")
        self.meshchat_browser_btn.connect("clicked", self._on_meshchat_browser)
        btn_row.append(self.meshchat_browser_btn)

        # Stop server
        self.meshchat_stop_btn = Gtk.Button(label="Stop Server")
        self.meshchat_stop_btn.add_css_class("destructive-action")
        self.meshchat_stop_btn.set_tooltip_text("Stop MeshChat server")
        self.meshchat_stop_btn.connect("clicked", self._on_meshchat_stop)
        btn_row.append(self.meshchat_stop_btn)

        box.append(btn_row)

        # Port configuration row
        port_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        port_row.set_halign(Gtk.Align.CENTER)

        port_label = Gtk.Label(label="Port:")
        port_row.append(port_label)

        self.meshchat_port_entry = Gtk.SpinButton()
        self.meshchat_port_entry.set_range(1024, 65535)
        self.meshchat_port_entry.set_value(5555)
        self.meshchat_port_entry.set_increments(1, 100)
        self.meshchat_port_entry.set_tooltip_text("MeshChat server port (default 5555)")
        port_row.append(self.meshchat_port_entry)

        # Host binding dropdown
        host_label = Gtk.Label(label="  Bind:")
        port_row.append(host_label)

        self.meshchat_host_dropdown = Gtk.DropDown.new_from_strings([
            "localhost only",
            "all interfaces"
        ])
        self.meshchat_host_dropdown.set_selected(0)
        self.meshchat_host_dropdown.set_tooltip_text("Network interface to bind to")
        port_row.append(self.meshchat_host_dropdown)

        box.append(port_row)

        # Links row
        links_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        links_row.set_halign(Gtk.Align.CENTER)

        docs_link = Gtk.LinkButton.new_with_label(
            "https://github.com/liamcottle/meshchat",
            "MeshChat Docs"
        )
        links_row.append(docs_link)

        lxmf_link = Gtk.LinkButton.new_with_label(
            "https://github.com/markqvist/lxmf",
            "LXMF Protocol"
        )
        links_row.append(lxmf_link)

        box.append(links_row)

        frame.set_child(box)
        parent.append(frame)

        # Check status on load
        GLib.timeout_add(600, self._check_meshchat_status)

    def _find_meshchat(self):
        """Find meshchat executable"""
        # First check system PATH
        meshchat_path = shutil.which('meshchat')
        if meshchat_path:
            return meshchat_path

        # Check real user's local bin (for --user pip installs)
        real_home = self._get_real_user_home()
        user_local_bin = real_home / ".local" / "bin" / "meshchat"
        if user_local_bin.exists():
            return str(user_local_bin)

        return None

    def _check_meshchat_status(self):
        """Check if MeshChat server is running"""
        def check():
            try:
                running = False
                port = None

                # Check for running meshchat process
                result = subprocess.run(
                    ['pgrep', '-f', 'meshchat'],
                    capture_output=True, text=True, timeout=5
                )

                if result.returncode == 0 and result.stdout.strip():
                    pids = [p for p in result.stdout.strip().split('\n') if p]
                    if pids:
                        running = True
                        # Try to find which port it's running on
                        for pid in pids:
                            try:
                                cmdline_result = subprocess.run(
                                    ['cat', f'/proc/{pid}/cmdline'],
                                    capture_output=True, text=True, timeout=2
                                )
                                if '--port' in cmdline_result.stdout or '-p' in cmdline_result.stdout:
                                    # Extract port from cmdline if specified
                                    pass
                            except Exception:
                                pass

                installed = self._find_meshchat() is not None
                GLib.idle_add(self._update_meshchat_status, running, installed, port)

            except Exception as e:
                GLib.idle_add(self._update_meshchat_status, False, False, None)

        threading.Thread(target=check, daemon=True).start()
        return False  # Don't repeat

    def _update_meshchat_status(self, running: bool, installed: bool, port: int = None):
        """Update MeshChat status in UI"""
        if running:
            self.meshchat_status_icon.set_from_icon_name("emblem-ok-symbolic")
            self.meshchat_status_label.set_text("Running")
            self.meshchat_status_label.remove_css_class("dim-label")
            self.meshchat_status_label.add_css_class("success")
            if port:
                self.meshchat_port_label.set_text(f"Port {port}")
            else:
                self.meshchat_port_label.set_text("Port 5555 (default)")
            self.meshchat_start_btn.set_sensitive(False)
            self.meshchat_stop_btn.set_sensitive(True)
            self.meshchat_browser_btn.set_sensitive(True)
        elif installed:
            self.meshchat_status_icon.set_from_icon_name("media-playback-stop-symbolic")
            self.meshchat_status_label.set_text("Installed (not running)")
            self.meshchat_status_label.remove_css_class("success")
            self.meshchat_status_label.add_css_class("dim-label")
            self.meshchat_port_label.set_text("")
            self.meshchat_start_btn.set_sensitive(True)
            self.meshchat_stop_btn.set_sensitive(False)
            self.meshchat_browser_btn.set_sensitive(False)
        else:
            self.meshchat_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.meshchat_status_label.set_text("Not installed")
            self.meshchat_status_label.remove_css_class("success")
            self.meshchat_status_label.add_css_class("dim-label")
            self.meshchat_port_label.set_text("pip install meshchat")
            self.meshchat_start_btn.set_sensitive(False)
            self.meshchat_stop_btn.set_sensitive(False)
            self.meshchat_browser_btn.set_sensitive(False)

    def _on_meshchat_start(self, button):
        """Start MeshChat server"""
        meshchat_path = self._find_meshchat()
        if not meshchat_path:
            self._show_error("MeshChat not found. Install with: pip install meshchat")
            return

        port = int(self.meshchat_port_entry.get_value())
        host_idx = self.meshchat_host_dropdown.get_selected()
        host = "127.0.0.1" if host_idx == 0 else "0.0.0.0"

        def start():
            try:
                real_home = self._get_real_user_home()
                real_user = self._get_real_username()

                # Build meshchat command
                cmd = [meshchat_path, '--port', str(port), '--host', host]

                # Run as real user if we're root
                if os.geteuid() == 0 and real_user != 'root':
                    cmd = ['sudo', '-u', real_user] + cmd

                # Start in background
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )

                # Wait a moment for server to start
                import time
                time.sleep(1.5)

                GLib.idle_add(self._check_meshchat_status)
                GLib.idle_add(
                    self._show_info,
                    f"MeshChat starting on http://{host}:{port}"
                )

            except Exception as e:
                GLib.idle_add(self._show_error, f"Failed to start MeshChat: {e}")

        threading.Thread(target=start, daemon=True).start()

    def _on_meshchat_stop(self, button):
        """Stop MeshChat server"""
        def stop():
            try:
                subprocess.run(
                    ['pkill', '-f', 'meshchat'],
                    capture_output=True, timeout=5
                )
                import time
                time.sleep(0.5)
                GLib.idle_add(self._check_meshchat_status)
                GLib.idle_add(self._show_info, "MeshChat stopped")
            except Exception as e:
                GLib.idle_add(self._show_error, f"Failed to stop MeshChat: {e}")

        threading.Thread(target=stop, daemon=True).start()

    def _on_meshchat_browser(self, button):
        """Open MeshChat in web browser"""
        port = int(self.meshchat_port_entry.get_value())
        host_idx = self.meshchat_host_dropdown.get_selected()

        # Always use localhost for browser, even if bound to 0.0.0.0
        url = f"http://127.0.0.1:{port}"

        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            self._show_error(f"Failed to open browser: {e}")
