"""
Service Management Panel - Start/Stop/Restart meshtasticd service
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading

# Import admin command helper for privilege escalation
try:
    from utils.system import run_admin_command_async, systemctl_admin
except ImportError:
    # Fallback if utils.system not available
    run_admin_command_async = None
    systemctl_admin = None


class ServicePanel(Gtk.Box):
    """Service management panel"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()
        self._refresh_status()

    def _build_ui(self):
        """Build the service panel UI"""
        # Title
        title = Gtk.Label(label="Service Management")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        # Status section
        status_frame = Gtk.Frame()
        status_frame.set_label("Service Status")

        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        status_box.set_margin_start(15)
        status_box.set_margin_end(15)
        status_box.set_margin_top(10)
        status_box.set_margin_bottom(10)

        self.status_icon = Gtk.Image.new_from_icon_name("emblem-question")
        self.status_icon.set_pixel_size(48)
        status_box.append(self.status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.status_label = Gtk.Label(label="Status: Checking...")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("heading")
        status_info.append(self.status_label)

        self.status_detail = Gtk.Label(label="")
        self.status_detail.set_xalign(0)
        status_info.append(self.status_detail)

        status_box.append(status_info)
        status_frame.set_child(status_box)
        self.append(status_frame)

        # Control buttons
        button_frame = Gtk.Frame()
        button_frame.set_label("Service Controls")

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_margin_start(15)
        button_box.set_margin_end(15)
        button_box.set_margin_top(10)
        button_box.set_margin_bottom(10)
        button_box.set_halign(Gtk.Align.CENTER)

        # Start button
        self.start_btn = Gtk.Button(label="Start")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", lambda b: self._service_action("start"))
        button_box.append(self.start_btn)

        # Stop button
        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.connect("clicked", lambda b: self._service_action("stop"))
        button_box.append(self.stop_btn)

        # Restart button
        self.restart_btn = Gtk.Button(label="Restart")
        self.restart_btn.connect("clicked", lambda b: self._service_action("restart"))
        button_box.append(self.restart_btn)

        # Reload button
        reload_btn = Gtk.Button(label="Reload Config")
        reload_btn.connect("clicked", lambda b: self._daemon_reload())
        button_box.append(reload_btn)

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda b: self._refresh_status())
        button_box.append(refresh_btn)

        button_frame.set_child(button_box)
        self.append(button_frame)

        # Boot options
        boot_frame = Gtk.Frame()
        boot_frame.set_label("Boot Options")

        boot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        boot_box.set_margin_start(15)
        boot_box.set_margin_end(15)
        boot_box.set_margin_top(10)
        boot_box.set_margin_bottom(10)

        self.boot_enabled_label = Gtk.Label(label="Start on boot: Checking...")
        boot_box.append(self.boot_enabled_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        boot_box.append(spacer)

        self.enable_btn = Gtk.Button(label="Enable")
        self.enable_btn.connect("clicked", lambda b: self._toggle_boot(True))
        boot_box.append(self.enable_btn)

        self.disable_btn = Gtk.Button(label="Disable")
        self.disable_btn.connect("clicked", lambda b: self._toggle_boot(False))
        boot_box.append(self.disable_btn)

        boot_frame.set_child(boot_box)
        self.append(boot_frame)

        # Logs section
        log_frame = Gtk.Frame()
        log_frame.set_label("Service Logs")
        log_frame.set_vexpand(True)

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Log controls
        log_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        log_controls.set_margin_start(10)
        log_controls.set_margin_end(10)
        log_controls.set_margin_top(5)

        log_controls.append(Gtk.Label(label="Lines:"))

        self.log_lines_spin = Gtk.SpinButton()
        self.log_lines_spin.set_range(10, 500)
        self.log_lines_spin.set_value(50)
        self.log_lines_spin.set_increments(10, 50)
        log_controls.append(self.log_lines_spin)

        # Since dropdown
        log_controls.append(Gtk.Label(label="Since:"))
        self.since_dropdown = Gtk.DropDown.new_from_strings(["All", "1 hour", "6 hours", "1 day", "1 week"])
        self.since_dropdown.set_selected(0)
        log_controls.append(self.since_dropdown)

        fetch_logs_btn = Gtk.Button(label="Fetch Logs")
        fetch_logs_btn.connect("clicked", lambda b: self._fetch_logs())
        log_controls.append(fetch_logs_btn)

        follow_btn = Gtk.ToggleButton(label="Follow")
        follow_btn.connect("toggled", self._on_follow_toggled)
        self.follow_btn = follow_btn
        log_controls.append(follow_btn)

        log_box.append(log_controls)

        # Log text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled.set_child(self.log_view)

        log_box.append(scrolled)
        log_frame.set_child(log_box)
        self.append(log_frame)

        self.follow_timer = None

    def _refresh_status(self):
        """Refresh service status"""
        thread = threading.Thread(target=self._fetch_status)
        thread.daemon = True
        thread.start()

    def _fetch_status(self):
        """Fetch service status in background"""
        try:
            # Get active state
            result = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True, timeout=10
            )
            is_active = result.stdout.strip() == 'active'

            # Get enabled state
            result = subprocess.run(
                ['systemctl', 'is-enabled', 'meshtasticd'],
                capture_output=True, text=True, timeout=10
            )
            is_enabled = result.stdout.strip() == 'enabled'

            # Get detailed status
            result = subprocess.run(
                ['systemctl', 'show', 'meshtasticd',
                 '--property=ActiveState,SubState,MainPID,ActiveEnterTimestamp'],
                capture_output=True, text=True, timeout=10
            )
            props = {}
            for line in result.stdout.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    props[key] = value

            GLib.idle_add(self._update_status_ui, is_active, is_enabled, props)

        except Exception as e:
            GLib.idle_add(self._show_error, str(e))

    def _update_status_ui(self, is_active, is_enabled, props):
        """Update status UI in main thread"""
        if is_active:
            self.status_icon.set_from_icon_name("emblem-default")
            self.status_label.set_label("Status: Running")
            self.status_label.remove_css_class("error")
            self.status_label.add_css_class("success")
            self.start_btn.set_sensitive(False)
            self.stop_btn.set_sensitive(True)
        else:
            self.status_icon.set_from_icon_name("dialog-error")
            self.status_label.set_label("Status: Stopped")
            self.status_label.remove_css_class("success")
            self.status_label.add_css_class("error")
            self.start_btn.set_sensitive(True)
            self.stop_btn.set_sensitive(False)

        # Detail info
        pid = props.get('MainPID', '0')
        state = props.get('SubState', 'unknown')
        detail = f"PID: {pid} | State: {state}"
        self.status_detail.set_label(detail)

        # Boot status
        if is_enabled:
            self.boot_enabled_label.set_label("Start on boot: Enabled")
            self.enable_btn.set_sensitive(False)
            self.disable_btn.set_sensitive(True)
        else:
            self.boot_enabled_label.set_label("Start on boot: Disabled")
            self.enable_btn.set_sensitive(True)
            self.disable_btn.set_sensitive(False)

        return False

    def _service_action(self, action):
        """Perform service action (start/stop/restart)"""
        self.main_window.set_status_message(f"{action.capitalize()}ing service...")

        def on_complete(success, stdout, stderr):
            self._action_complete(action, success, stderr)

        # Use elegant admin helper (pkexec GUI prompt) if available
        if systemctl_admin:
            systemctl_admin(action, 'meshtasticd', callback=on_complete)
        else:
            # Fallback to direct sudo
            def do_action():
                try:
                    result = subprocess.run(
                        ['sudo', 'systemctl', action, 'meshtasticd'],
                        capture_output=True, text=True, timeout=30
                    )
                    success = result.returncode == 0
                    GLib.idle_add(self._action_complete, action, success, result.stderr)
                except Exception as e:
                    GLib.idle_add(self._action_complete, action, False, str(e))

            thread = threading.Thread(target=do_action, daemon=True)
            thread.start()

    def _action_complete(self, action, success, error):
        """Handle action completion"""
        if success:
            self.main_window.set_status_message(f"Service {action}ed successfully")
        else:
            self.main_window.set_status_message(f"Failed to {action} service: {error}")

        self._refresh_status()
        return False

    def _daemon_reload(self):
        """Reload systemd daemon"""
        def on_complete(success, stdout, stderr):
            if success:
                self.main_window.set_status_message("Daemon reloaded successfully")
            else:
                self.main_window.set_status_message(f"Failed to reload daemon: {stderr}")

        if run_admin_command_async:
            run_admin_command_async(['systemctl', 'daemon-reload'], on_complete)
        else:
            def do_reload():
                try:
                    subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True, timeout=15)
                    GLib.idle_add(self.main_window.set_status_message, "Daemon reloaded successfully")
                except Exception as e:
                    GLib.idle_add(self.main_window.set_status_message, f"Failed to reload daemon: {e}")

            thread = threading.Thread(target=do_reload, daemon=True)
            thread.start()

    def _toggle_boot(self, enable):
        """Enable or disable service on boot"""
        action = "enable" if enable else "disable"

        def on_complete(success, stdout, stderr):
            self._refresh_status()
            if success:
                self.main_window.set_status_message(f"Service {action}d on boot")
            else:
                self.main_window.set_status_message(f"Failed to {action} service: {stderr}")

        if systemctl_admin:
            systemctl_admin(action, 'meshtasticd', callback=on_complete)
        else:
            def do_toggle():
                try:
                    subprocess.run(['sudo', 'systemctl', action, 'meshtasticd'], check=True, timeout=30)
                    GLib.idle_add(self._refresh_status)
                    GLib.idle_add(self.main_window.set_status_message, f"Service {action}d on boot")
                except Exception as e:
                    GLib.idle_add(self.main_window.set_status_message, f"Failed to {action} service: {e}")

            thread = threading.Thread(target=do_toggle, daemon=True)
            thread.start()

    def _fetch_logs(self):
        """Fetch service logs"""
        lines = int(self.log_lines_spin.get_value())
        since_options = {
            0: None,  # All
            1: "1 hour ago",
            2: "6 hours ago",
            3: "1 day ago",
            4: "1 week ago"
        }
        since = since_options.get(self.since_dropdown.get_selected())

        def do_fetch():
            try:
                cmd = ['journalctl', '-u', 'meshtasticd', '-n', str(lines), '--no-pager']
                if since:
                    cmd.extend(['--since', since])

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                logs = result.stdout if result.stdout else "No logs available"
                GLib.idle_add(self._update_logs, logs)
            except Exception as e:
                GLib.idle_add(self._update_logs, f"Failed to fetch logs: {e}")

        thread = threading.Thread(target=do_fetch)
        thread.daemon = True
        thread.start()

    def _update_logs(self, text):
        """Update log view"""
        buffer = self.log_view.get_buffer()
        buffer.set_text(text)
        # Auto-scroll to bottom
        end_iter = buffer.get_end_iter()
        self.log_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 1.0)
        return False

    def _on_follow_toggled(self, button):
        """Handle follow toggle"""
        if button.get_active():
            # Start following logs
            self.follow_timer = GLib.timeout_add_seconds(2, self._follow_tick)
            self._fetch_logs()
        else:
            # Stop following
            if self.follow_timer:
                GLib.source_remove(self.follow_timer)
                self.follow_timer = None

    def _follow_tick(self):
        """Timer tick for log following"""
        if self.follow_btn.get_active():
            self._fetch_logs()
            return True
        return False

    def _show_error(self, message):
        """Show error in UI"""
        self.status_label.set_label(f"Error: {message}")
        return False
