"""
Dashboard Panel - Quick status overview

Uses the unified commands layer for status checks, shared with CLI.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading

# Import unified commands layer (shared with CLI)
try:
    from commands import service, hardware
    COMMANDS_AVAILABLE = True
except ImportError:
    COMMANDS_AVAILABLE = False

# Fallback to old service checker
try:
    from utils.service_check import check_service, ServiceState
except ImportError:
    check_service = None
    ServiceState = None


class DashboardPanel(Gtk.Box):
    """Dashboard panel showing system status"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()
        self._refresh_data()

    def _build_ui(self):
        """Build the dashboard UI"""
        # Title
        title = Gtk.Label(label="System Dashboard")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda b: self._refresh_data())
        refresh_btn.set_halign(Gtk.Align.END)
        self.append(refresh_btn)

        # Status cards in a grid
        grid = Gtk.Grid()
        grid.set_row_spacing(15)
        grid.set_column_spacing(15)
        grid.set_row_homogeneous(True)
        grid.set_column_homogeneous(True)
        self.append(grid)

        # Service Status Card
        self.service_card = self._create_status_card(
            "Service Status",
            "Checking...",
            "system-run-symbolic"
        )
        grid.attach(self.service_card, 0, 0, 1, 1)

        # Version Card
        self.version_card = self._create_status_card(
            "Meshtasticd Version",
            "Checking...",
            "software-update-available-symbolic"
        )
        grid.attach(self.version_card, 1, 0, 1, 1)

        # Config Status Card
        self.config_card = self._create_status_card(
            "Configuration",
            "Checking...",
            "document-properties-symbolic"
        )
        grid.attach(self.config_card, 0, 1, 1, 1)

        # Hardware Card
        self.hardware_card = self._create_status_card(
            "Hardware",
            "Checking...",
            "drive-harddisk-symbolic"
        )
        grid.attach(self.hardware_card, 1, 1, 1, 1)

        # Log output area
        log_frame = Gtk.Frame()
        log_frame.set_label("Recent Service Logs")
        log_frame.set_vexpand(True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled.set_child(self.log_view)

        log_frame.set_child(scrolled)
        self.append(log_frame)

    def _create_status_card(self, title, value, icon_name):
        """Create a status card widget"""
        frame = Gtk.Frame()
        frame.add_css_class("card")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Header with icon
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(24)
        header.append(icon)

        title_label = Gtk.Label(label=title)
        title_label.add_css_class("heading")
        title_label.set_xalign(0)
        header.append(title_label)
        box.append(header)

        # Value
        value_label = Gtk.Label(label=value)
        value_label.set_xalign(0)
        value_label.set_name("value_label")
        box.append(value_label)

        frame.set_child(box)
        return frame

    def _update_card_value(self, card, value, css_class=None):
        """Update a card's value label"""
        box = card.get_child()
        for child in box:
            if isinstance(child, Gtk.Label) and child.get_name() == "value_label":
                child.set_label(value)
                if css_class:
                    child.remove_css_class("success")
                    child.remove_css_class("error")
                    child.remove_css_class("warning")
                    child.add_css_class(css_class)
                break

    def _refresh_data(self):
        """Refresh all dashboard data"""
        thread = threading.Thread(target=self._fetch_data)
        thread.daemon = True
        thread.start()

    def _fetch_data(self):
        """Fetch all status data in background thread using commands layer"""
        # Service status - use unified commands layer
        try:
            if COMMANDS_AVAILABLE:
                # Use unified commands layer (shared with CLI)
                result = service.check_status('meshtasticd')
                if result.data.get('running', False):
                    status_detail = result.data.get('status', 'Running')
                    css_class = "success"
                else:
                    status_detail = result.data.get('status', 'Stopped')
                    css_class = "error"
            elif check_service:
                # Fallback to old service checker
                meshtastic_status = check_service('meshtasticd')
                if meshtastic_status.available:
                    status_detail = meshtastic_status.message
                    css_class = "success"
                else:
                    status_detail = meshtastic_status.message
                    css_class = "error"
            else:
                # Last resort fallback to direct check
                is_running = False
                status_detail = "Stopped"

                result = subprocess.run(
                    ['systemctl', 'is-active', 'meshtasticd'],
                    capture_output=True, text=True, timeout=10
                )
                if result.stdout.strip() == 'active':
                    is_running = True
                    status_detail = "Running"

                css_class = "success" if is_running else "error"

            GLib.idle_add(
                self._update_card_value,
                self.service_card,
                status_detail,
                css_class
            )
        except Exception as e:
            GLib.idle_add(self._update_card_value, self.service_card, f"Error: {e}", "error")

        # Version - use unified commands layer
        try:
            if COMMANDS_AVAILABLE:
                result = service.get_version('meshtasticd')
                if result.success:
                    version = result.data.get('version', 'Unknown')
                else:
                    version = "Not installed"
            else:
                # Fallback to direct subprocess
                result = subprocess.run(
                    ['meshtasticd', '--version'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    version = result.stdout.strip()
                else:
                    version = "Not installed"
            GLib.idle_add(self._update_card_value, self.version_card, version, None)
        except FileNotFoundError:
            GLib.idle_add(self._update_card_value, self.version_card, "Not installed", "warning")
        except Exception as e:
            GLib.idle_add(self._update_card_value, self.version_card, f"Error: {e}", "error")

        # Config status - more robust detection
        try:
            from pathlib import Path
            config_path = Path('/etc/meshtasticd/config.yaml')
            config_d = Path('/etc/meshtasticd/config.d')
            available_d = Path('/etc/meshtasticd/available.d')
            meshtasticd_dir = Path('/etc/meshtasticd')

            if meshtasticd_dir.exists():
                # Count active configs (both .yaml and .yml)
                active_configs = []
                if config_d.exists():
                    active_configs = list(config_d.glob('*.yaml')) + list(config_d.glob('*.yml'))

                # Count available configs
                available_configs = []
                if available_d.exists():
                    available_configs = list(available_d.glob('*.yaml')) + list(available_d.glob('*.yml'))

                # Check for LoRa config specifically
                has_lora = any('lora' in str(c).lower() for c in active_configs)

                # Build status message
                if active_configs:
                    config_names = [c.stem for c in active_configs[:3]]
                    names_str = ', '.join(config_names)
                    if len(active_configs) > 3:
                        names_str += f" +{len(active_configs)-3} more"
                    status = f"{len(active_configs)} active: {names_str}"
                    css = "success" if has_lora else "warning"
                elif config_path.exists():
                    # Check if main config has content
                    try:
                        content = config_path.read_text()
                        if content.strip() and len(content) > 10:
                            status = "Main config active"
                            css = "success"
                        else:
                            status = "Main config empty"
                            css = "warning"
                    except (OSError, PermissionError):
                        status = "Main config exists"
                        css = "success"
                elif available_configs:
                    status = f"0 active ({len(available_configs)} available)"
                    css = "warning"
                else:
                    status = "No configs found"
                    css = "warning"

                GLib.idle_add(self._update_card_value, self.config_card, status, css)
            else:
                GLib.idle_add(self._update_card_value, self.config_card, "Not installed", "warning")
        except Exception as e:
            GLib.idle_add(self._update_card_value, self.config_card, f"Error: {e}", "error")

        # Hardware - use unified commands layer
        try:
            if COMMANDS_AVAILABLE:
                # Use unified commands layer (shared with CLI)
                spi_result = hardware.check_spi()
                i2c_result = hardware.check_i2c()

                hw_status = []
                if spi_result.data.get('enabled', False):
                    hw_status.append("SPI")
                if i2c_result.data.get('enabled', False):
                    hw_status.append("I2C")
            else:
                # Fallback to direct check
                from pathlib import Path
                spi_enabled = Path('/dev/spidev0.0').exists()
                i2c_enabled = Path('/dev/i2c-1').exists()

                hw_status = []
                if spi_enabled:
                    hw_status.append("SPI")
                if i2c_enabled:
                    hw_status.append("I2C")

            if hw_status:
                status = ", ".join(hw_status) + " enabled"
                css = "success"
            else:
                status = "SPI/I2C not enabled"
                css = "warning"

            GLib.idle_add(self._update_card_value, self.hardware_card, status, css)
        except Exception as e:
            GLib.idle_add(self._update_card_value, self.hardware_card, f"Error: {e}", "error")

        # Logs - use unified commands layer
        try:
            if COMMANDS_AVAILABLE:
                result = service.get_logs('meshtasticd', lines=20)
                logs = result.raw_output if result.raw_output else "No logs available"
            else:
                result = subprocess.run(
                    ['journalctl', '-u', 'meshtasticd', '-n', '20', '--no-pager'],
                    capture_output=True, text=True, timeout=10
                )
                logs = result.stdout if result.stdout else "No logs available"
            GLib.idle_add(self._update_logs, logs)
        except Exception as e:
            GLib.idle_add(self._update_logs, f"Failed to fetch logs: {e}")

    def _update_logs(self, text):
        """Update log view text"""
        buffer = self.log_view.get_buffer()
        buffer.set_text(text)
        return False
