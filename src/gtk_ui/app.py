"""
MeshForge - GTK4 Application
LoRa Mesh Network Development & Operations Suite
Main application entry point and window management
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Adw, GLib, Gio, Gdk
import sys
import os
import subprocess
import threading
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from __version__ import __version__, get_full_version, __app_name__

# Edition detection
try:
    from core.edition import Edition, detect_edition, has_feature, get_edition_info
    EDITION_AVAILABLE = True
except ImportError:
    EDITION_AVAILABLE = False
    class Edition:
        PRO = "pro"
        AMATEUR = "amateur"
        IO = "io"
    def detect_edition():
        return Edition.PRO
    def has_feature(f):
        return True
    def get_edition_info():
        return {"edition": "pro", "display_name": "MeshForge PRO"}


class MeshForgeApp(Adw.Application):
    """MeshForge GTK4 Application"""

    def __init__(self):
        super().__init__(
            application_id='org.meshforge.app',
            # NON_UNIQUE allows running without D-Bus registration
            # This fixes "Failed to register: Timeout was reached" when running as root
            flags=Gio.ApplicationFlags.NON_UNIQUE
        )
        self.window = None
        self._icons_registered = False
        self.connect('activate', self.on_activate)

    def _register_custom_icons(self):
        """Register custom icons from assets directory"""
        if self._icons_registered:
            return

        # Find assets directory relative to source
        src_dir = Path(__file__).parent.parent.parent
        assets_dir = src_dir / 'assets'

        if assets_dir.exists():
            display = Gdk.Display.get_default()
            if display:
                icon_theme = Gtk.IconTheme.get_for_display(display)
                icon_theme.add_search_path(str(assets_dir))
                self._icons_registered = True

    def on_activate(self, app):
        """Called when application is activated"""
        # Register custom icons (display is now available)
        self._register_custom_icons()

        if not self.window:
            self.window = MeshForgeWindow(application=app)
        self.window.present()


# Backwards compatibility alias
MeshtasticdApp = MeshForgeApp


class MeshForgeWindow(Adw.ApplicationWindow):
    """MeshForge main application window"""

    # Window constraints
    MIN_WIDTH = 800
    MIN_HEIGHT = 600
    DEFAULT_WIDTH = 1024
    DEFAULT_HEIGHT = 768
    SIDEBAR_COLLAPSE_WIDTH = 900  # Collapse sidebar below this width

    # Edition-specific colors
    EDITION_COLORS = {
        "pro": "#1a73e8",      # Blue
        "amateur": "#fbbc04",   # Gold
        "io": "#673ab7",        # Purple
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Detect edition
        self.edition = detect_edition()
        self.edition_info = get_edition_info()

        # Set edition-aware title
        edition_suffix = ""
        if EDITION_AVAILABLE:
            if self.edition == Edition.AMATEUR:
                edition_suffix = " Amateur"
            elif self.edition == Edition.IO:
                edition_suffix = ".io"
            else:
                edition_suffix = " PRO"

        self.set_title(f"MeshForge{edition_suffix} v{__version__}")

        # Set minimum size constraints
        self.set_size_request(self.MIN_WIDTH, self.MIN_HEIGHT)

        # Get monitor dimensions and set appropriate default size
        default_width, default_height = self._get_smart_default_size()
        self.set_default_size(default_width, default_height)

        # Track sidebar visibility for responsive layout
        self._sidebar_visible = True
        self._sidebar_widget = None

        # Track subprocess for nano/terminal operations
        self.external_process = None

        # Cache for node count (avoid repeated connections)
        self._node_count_cache = "--"
        self._node_count_timestamp = 0
        self._node_count_cache_ttl = 30  # Cache for 30 seconds

        # Create the main layout
        self._build_ui()

        # Set up keyboard shortcuts
        self._setup_keyboard_shortcuts()

        # Check if we're resuming after reboot
        self._check_resume_state()

    def _get_smart_default_size(self):
        """Calculate smart default window size based on monitor dimensions"""
        try:
            # Get the display
            display = Gdk.Display.get_default()
            if display:
                # Get all monitors
                monitors = display.get_monitors()
                if monitors and monitors.get_n_items() > 0:
                    # Use primary or first monitor
                    monitor = monitors.get_item(0)
                    if monitor:
                        geometry = monitor.get_geometry()
                        mon_width = geometry.width
                        mon_height = geometry.height

                        # Use 75% of monitor size, but respect min/max bounds
                        target_width = int(mon_width * 0.75)
                        target_height = int(mon_height * 0.75)

                        # Clamp to reasonable bounds
                        width = max(self.MIN_WIDTH, min(target_width, 1600))
                        height = max(self.MIN_HEIGHT, min(target_height, 1000))

                        return width, height
        except Exception:
            pass

        # Fallback to defaults
        return self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT

    def _setup_responsive_layout(self):
        """Set up responsive layout handling for window resize"""
        # Connect to window state changes for resize handling
        self.connect("notify::default-width", self._on_window_resize)

    def _on_window_resize(self, widget, param):
        """Handle window resize for responsive layout"""
        if not self._sidebar_widget:
            return

        width = self.get_width()
        if width > 0:
            if width < self.SIDEBAR_COLLAPSE_WIDTH and self._sidebar_visible:
                # Hide sidebar on small screens
                self._sidebar_widget.set_visible(False)
                if hasattr(self, '_sidebar_separator'):
                    self._sidebar_separator.set_visible(False)
                self._sidebar_visible = False
            elif width >= self.SIDEBAR_COLLAPSE_WIDTH and not self._sidebar_visible:
                # Show sidebar on larger screens
                self._sidebar_widget.set_visible(True)
                if hasattr(self, '_sidebar_separator'):
                    self._sidebar_separator.set_visible(True)
                self._sidebar_visible = True

    def toggle_sidebar(self):
        """Manually toggle sidebar visibility (for menu button)"""
        if self._sidebar_widget:
            self._sidebar_visible = not self._sidebar_visible
            self._sidebar_widget.set_visible(self._sidebar_visible)
            if hasattr(self, '_sidebar_separator'):
                self._sidebar_separator.set_visible(self._sidebar_visible)

    def _setup_keyboard_shortcuts(self):
        """Set up keyboard shortcuts for the window"""
        # Create key controller
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press events"""
        # Escape key - unfullscreen if fullscreened
        if keyval == Gdk.KEY_Escape:
            if self.is_fullscreen():
                self.unfullscreen()
                return True
        # F9 - toggle sidebar
        elif keyval == Gdk.KEY_F9:
            self.toggle_sidebar()
            return True
        # F11 - toggle fullscreen
        elif keyval == Gdk.KEY_F11:
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()
            return True
        # Ctrl+Q - quit
        elif keyval == Gdk.KEY_q and (state & Gdk.ModifierType.CONTROL_MASK):
            self.get_application().quit()
            return True
        # Ctrl+1 through Ctrl+9 - quick page navigation
        elif state & Gdk.ModifierType.CONTROL_MASK:
            nav_keys = {
                Gdk.KEY_1: "dashboard",
                Gdk.KEY_2: "service",
                Gdk.KEY_3: "install",
                Gdk.KEY_4: "config",
                Gdk.KEY_5: "radio_config",
                Gdk.KEY_6: "rns",
                Gdk.KEY_7: "map",
                Gdk.KEY_8: "hamclock",
                Gdk.KEY_9: "tools",
            }
            if keyval in nav_keys:
                self.content_stack.set_visible_child_name(nav_keys[keyval])
                return True
        return False

    def _build_ui(self):
        """Build the main UI layout"""
        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar with title and menu
        header = Adw.HeaderBar()
        header_title = self.edition_info.get("display_name", "MeshForge")
        header.set_title_widget(Gtk.Label(label=f"{header_title} v{__version__}"))

        # Sidebar toggle button (for responsive layout)
        self._sidebar_toggle_btn = Gtk.Button()
        self._sidebar_toggle_btn.set_icon_name("sidebar-show-symbolic")
        self._sidebar_toggle_btn.set_tooltip_text("Toggle sidebar (F9)")
        self._sidebar_toggle_btn.connect("clicked", lambda btn: self.toggle_sidebar())
        header.pack_start(self._sidebar_toggle_btn)

        # Menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Application menu")
        menu = Gio.Menu()
        menu.append("Toggle Sidebar", "app.toggle-sidebar")
        menu.append("About", "app.about")
        menu.append("Quit", "app.quit")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        main_box.append(header)

        # Create actions
        self._create_actions()

        # Status bar at top
        self.status_bar = self._create_status_bar()
        main_box.append(self.status_bar)

        # Main content area with navigation
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content_box.set_vexpand(True)
        main_box.append(content_box)

        # Main content stack (create BEFORE sidebar to avoid callback issues)
        self.content_stack = Gtk.Stack()
        self.content_stack.set_hexpand(True)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        # Add content pages BEFORE sidebar (so stack has pages when nav callback fires)
        self._add_dashboard_page()
        self._add_service_page()
        self._add_install_page()
        self._add_config_page()
        self._add_radio_config_page()
        self._add_rns_page()
        self._add_map_page()
        self._add_hamclock_page()
        self._add_cli_page()
        self._add_hardware_page()
        self._add_tools_page()
        self._add_aredn_page()
        self._add_amateur_page()
        self._add_university_page()
        self._add_settings_page()

        # Left sidebar navigation (after content_stack exists)
        sidebar = self._create_sidebar()
        self._sidebar_widget = sidebar  # Store reference for responsive layout
        content_box.append(sidebar)

        # Separator (also needs to hide with sidebar)
        self._sidebar_separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(self._sidebar_separator)

        # Add content stack to layout
        content_box.append(self.content_stack)

        # Bottom status bar
        self.bottom_status = self._create_bottom_status()
        main_box.append(self.bottom_status)

        # Start status update timer
        GLib.timeout_add_seconds(5, self._update_status)
        self._update_status()

        # Set up responsive layout handling
        self._setup_responsive_layout()

    def _create_actions(self):
        """Create application actions"""
        app = self.get_application()

        # Toggle sidebar action
        sidebar_action = Gio.SimpleAction.new("toggle-sidebar", None)
        sidebar_action.connect("activate", lambda *_: self.toggle_sidebar())
        app.add_action(sidebar_action)

        # About action
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        app.add_action(about_action)

        # Quit action
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: app.quit())
        app.add_action(quit_action)

    def _on_about(self, action, param):
        """Show about dialog"""
        # Edition-aware app name
        app_name = self.edition_info.get("display_name", "MeshForge")
        tagline = ""
        if EDITION_AVAILABLE:
            if self.edition == Edition.AMATEUR:
                tagline = "When All Else Fails"
            elif self.edition == Edition.IO:
                tagline = "Mesh Made Simple"
            else:
                tagline = "Professional Mesh Management"

        dialog = Adw.AboutWindow(
            transient_for=self,
            application_name=app_name,
            application_icon="meshforge-icon",
            version=get_full_version(),
            developer_name="MeshForge Community",
            license_type=Gtk.License.GPL_3_0,
            website="https://meshforge.org",
            issue_url="https://github.com/Nursedude/meshforge/issues",
            developers=["Nursedude", "Contributors"],
            comments=tagline,
        )
        dialog.present()

    def _create_status_bar(self):
        """Create the top status bar"""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(5)
        box.set_margin_bottom(5)
        box.add_css_class("status-bar")

        # Service status indicator
        self.service_status_icon = Gtk.Image.new_from_icon_name("emblem-default")
        box.append(self.service_status_icon)

        self.service_status_label = Gtk.Label(label="Service: Checking...")
        box.append(self.service_status_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        box.append(spacer)

        # Node count
        self.node_count_label = Gtk.Label(label="Nodes: --")
        box.append(self.node_count_label)

        # Separator
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Uptime
        self.uptime_label = Gtk.Label(label="Uptime: --")
        box.append(self.uptime_label)

        # Separator
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # View Logs button
        logs_btn = Gtk.Button()
        logs_btn.set_icon_name("utilities-terminal-symbolic")
        logs_btn.set_tooltip_text("View Application Logs")
        logs_btn.connect("clicked", self._on_view_logs)
        box.append(logs_btn)

        return box

    def _create_sidebar(self):
        """Create the navigation sidebar"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(200, -1)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.add_css_class("navigation-sidebar")
        listbox.connect("row-selected", self._on_nav_selected)
        scrolled.set_child(listbox)

        # Navigation items
        # Navigation items with feature requirements
        # Format: (page_name, label, icon, required_feature or None)
        all_nav_items = [
            ("dashboard", "Dashboard", "utilities-system-monitor-symbolic", None),
            ("service", "Service Management", "system-run-symbolic", None),
            ("install", "Install / Update", "system-software-install-symbolic", None),
            ("config", "Config File Manager", "document-edit-symbolic", None),
            ("radio_config", "Radio Configuration", "network-wireless-symbolic", None),
            ("rns", "Reticulum (RNS)", "network-transmit-receive-symbolic", "rns_integration"),
            ("map", "Node Map", "mark-location-symbolic", None),
            ("hamclock", "HamClock", "weather-clear-symbolic", "hamclock"),
            ("cli", "Meshtastic CLI", "utilities-terminal-symbolic", None),
            ("hardware", "Hardware Detection", "drive-harddisk-symbolic", None),
            ("tools", "System Tools", "applications-utilities-symbolic", None),
            ("aredn", "AREDN Mesh", "network-server-symbolic", "aredn_integration"),
            ("amateur", "Amateur Radio", "audio-speakers-symbolic", "amateur_radio"),
            ("university", "MeshForge University", "school-symbolic", None),
            ("settings", "Settings", "preferences-system-symbolic", None),
        ]

        # Filter by edition features
        nav_items = []
        for item in all_nav_items:
            name, label, icon, feature = item
            if feature is None or has_feature(feature):
                nav_items.append((name, label, icon))

        for name, label, icon in nav_items:
            row = Gtk.ListBoxRow()
            row.set_name(name)

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.set_margin_top(8)
            box.set_margin_bottom(8)

            img = Gtk.Image.new_from_icon_name(icon)
            box.append(img)

            lbl = Gtk.Label(label=label)
            lbl.set_xalign(0)
            box.append(lbl)

            row.set_child(box)
            listbox.append(row)

        # Select first item
        listbox.select_row(listbox.get_row_at_index(0))

        return scrolled

    def _on_nav_selected(self, listbox, row):
        """Handle navigation selection"""
        if row:
            page_name = row.get_name()
            self.content_stack.set_visible_child_name(page_name)

    def _add_dashboard_page(self):
        """Add the dashboard page"""
        from .panels.dashboard import DashboardPanel
        panel = DashboardPanel(self)
        self.content_stack.add_named(panel, "dashboard")
        self.dashboard_panel = panel

    def _add_service_page(self):
        """Add the service management page"""
        from .panels.service import ServicePanel
        panel = ServicePanel(self)
        self.content_stack.add_named(panel, "service")
        self.service_panel = panel

    def _add_install_page(self):
        """Add the install/update page"""
        from .panels.install import InstallPanel
        panel = InstallPanel(self)
        self.content_stack.add_named(panel, "install")
        self.install_panel = panel

    def _add_config_page(self):
        """Add the config file manager page"""
        from .panels.config import ConfigPanel
        panel = ConfigPanel(self)
        self.content_stack.add_named(panel, "config")
        self.config_panel = panel

    def _add_radio_config_page(self):
        """Add the radio configuration page"""
        from .panels.radio_config import RadioConfigPanel
        panel = RadioConfigPanel(self)
        self.content_stack.add_named(panel, "radio_config")
        self.radio_config_panel = panel

    def _add_rns_page(self):
        """Add the RNS/Reticulum management page"""
        from .panels.rns import RNSPanel
        panel = RNSPanel(self)
        self.content_stack.add_named(panel, "rns")
        self.rns_panel = panel

    def _add_map_page(self):
        """Add the unified node map page"""
        from .panels.map import MapPanel
        panel = MapPanel(self)
        self.content_stack.add_named(panel, "map")
        self.map_panel = panel

    def _add_hamclock_page(self):
        """Add the HamClock integration page"""
        from .panels.hamclock import HamClockPanel
        panel = HamClockPanel(self)
        self.content_stack.add_named(panel, "hamclock")
        self.hamclock_panel = panel

    def _add_cli_page(self):
        """Add the meshtastic CLI page"""
        from .panels.cli import CLIPanel
        panel = CLIPanel(self)
        self.content_stack.add_named(panel, "cli")
        self.cli_panel = panel

    def _add_hardware_page(self):
        """Add the hardware detection page"""
        from .panels.hardware import HardwarePanel
        panel = HardwarePanel(self)
        self.content_stack.add_named(panel, "hardware")
        self.hardware_panel = panel

    def _add_tools_page(self):
        """Add the system tools page"""
        from .panels.tools import ToolsPanel
        panel = ToolsPanel(self)
        self.content_stack.add_named(panel, "tools")
        self.tools_panel = panel

    def _add_aredn_page(self):
        """Add the AREDN mesh network page"""
        from .panels.aredn import AREDNPanel
        panel = AREDNPanel(self)
        self.content_stack.add_named(panel, "aredn")
        self.aredn_panel = panel

    def _add_amateur_page(self):
        """Add the Amateur Radio page (Amateur Edition)"""
        try:
            from .amateur_panel import AmateurPanel
            panel = AmateurPanel(self)
            self.content_stack.add_named(panel, "amateur")
            self.amateur_panel = panel
        except ImportError:
            # Create placeholder if amateur module not available
            placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
            placeholder.set_valign(Gtk.Align.CENTER)
            placeholder.set_halign(Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_icon_name("audio-speakers-symbolic")
            icon.set_pixel_size(64)
            placeholder.append(icon)
            label = Gtk.Label(label="Amateur Radio Features")
            label.add_css_class("title-1")
            placeholder.append(label)
            desc = Gtk.Label(label="Ham radio specific features for licensed operators")
            desc.add_css_class("dim-label")
            placeholder.append(desc)
            self.content_stack.add_named(placeholder, "amateur")
            self.amateur_panel = None

    def _add_university_page(self):
        """Add the MeshForge University learning page"""
        from .panels.university import UniversityPanel
        panel = UniversityPanel(self)
        self.content_stack.add_named(panel, "university")
        self.university_panel = panel

    def _add_settings_page(self):
        """Add the settings page"""
        from .panels.settings import SettingsPanel
        panel = SettingsPanel(self)
        self.content_stack.add_named(panel, "settings")
        self.settings_panel = panel

    def _create_bottom_status(self):
        """Create bottom status bar"""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(5)
        box.set_margin_bottom(5)

        self.bottom_message = Gtk.Label(label="Ready")
        self.bottom_message.set_xalign(0)
        self.bottom_message.set_hexpand(True)
        box.append(self.bottom_message)

        return box

    def set_status_message(self, message):
        """Set the bottom status message"""
        # Defensive check: bottom_message may not exist during panel initialization
        if hasattr(self, 'bottom_message') and self.bottom_message:
            self.bottom_message.set_label(message)

    def _on_view_logs(self, button):
        """Show log viewer dialog"""
        dialog = Adw.Window(transient_for=self, modal=True)
        dialog.set_title("Application Logs")
        dialog.set_default_size(800, 500)

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh Logs")
        header.pack_start(refresh_btn)

        # Open in file manager button
        open_btn = Gtk.Button()
        open_btn.set_icon_name("folder-open-symbolic")
        open_btn.set_tooltip_text("Open Log Directory")
        header.pack_start(open_btn)

        main_box.append(header)

        # Log content area
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_left_margin(10)
        text_view.set_right_margin(10)
        text_view.set_top_margin(10)
        text_view.set_bottom_margin(10)
        buffer = text_view.get_buffer()
        scrolled.set_child(text_view)
        main_box.append(scrolled)

        # Status bar at bottom
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status_box.set_margin_start(10)
        status_box.set_margin_end(10)
        status_box.set_margin_top(5)
        status_box.set_margin_bottom(5)
        log_path_label = Gtk.Label()
        log_path_label.set_xalign(0)
        log_path_label.add_css_class("dim-label")
        status_box.append(log_path_label)
        main_box.append(status_box)

        dialog.set_content(main_box)

        # Load log content
        def load_logs():
            try:
                from utils.logging_utils import LOG_DIR
                log_dir = LOG_DIR
            except ImportError:
                # Fallback path
                import os
                sudo_user = os.environ.get('SUDO_USER')
                if sudo_user and sudo_user != 'root':
                    log_dir = Path(f'/home/{sudo_user}/.config/meshforge/logs')
                else:
                    log_dir = Path.home() / '.config' / 'meshforge' / 'logs'

            log_path_label.set_label(f"Log directory: {log_dir}")

            if not log_dir.exists():
                buffer.set_text(f"Log directory not found: {log_dir}\n\nLogs will appear here after the application is restarted.")
                return

            # Find most recent log file
            log_files = sorted(log_dir.glob("meshforge_*.log"), reverse=True)
            if not log_files:
                buffer.set_text("No log files found yet.\n\nLogs will appear after application activity.")
                return

            log_file = log_files[0]
            try:
                # Read last 500 lines of log
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    recent_lines = lines[-500:] if len(lines) > 500 else lines
                    content = ''.join(recent_lines)
                    if len(lines) > 500:
                        content = f"[... showing last 500 of {len(lines)} lines ...]\n\n" + content
                    buffer.set_text(content)

                    # Scroll to end
                    end_iter = buffer.get_end_iter()
                    text_view.scroll_to_iter(end_iter, 0.0, True, 0.0, 1.0)
            except Exception as e:
                buffer.set_text(f"Error reading log file: {e}")

        def on_refresh(btn):
            load_logs()

        def on_open_dir(btn):
            try:
                from utils.logging_utils import LOG_DIR
                log_dir = LOG_DIR
            except ImportError:
                import os
                sudo_user = os.environ.get('SUDO_USER')
                if sudo_user and sudo_user != 'root':
                    log_dir = Path(f'/home/{sudo_user}/.config/meshforge/logs')
                else:
                    log_dir = Path.home() / '.config' / 'meshforge' / 'logs'

            if log_dir.exists():
                subprocess.Popen(['xdg-open', str(log_dir)])

        refresh_btn.connect("clicked", on_refresh)
        open_btn.connect("clicked", on_open_dir)

        load_logs()
        dialog.present()

    def _update_status(self):
        """Update status bar information"""
        # Run in thread to avoid blocking UI
        thread = threading.Thread(target=self._update_status_thread)
        thread.daemon = True
        thread.start()
        return True  # Continue timer

    def _update_status_thread(self):
        """Thread for updating status"""
        try:
            import socket

            # Check service status - multiple methods
            is_active = False

            # Method 1: systemctl is-active
            result = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True
            )
            if result.stdout.strip() == 'active':
                is_active = True

            # Method 2: Check if process is running
            if not is_active:
                result = subprocess.run(
                    ['pgrep', '-f', 'meshtasticd'],
                    capture_output=True, text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    is_active = True

            # Method 3: Check TCP port 4403
            if not is_active:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1.0)
                    if sock.connect_ex(('localhost', 4403)) == 0:
                        is_active = True
                    sock.close()
                except Exception:
                    pass

            # Get uptime if active
            uptime = "--"
            node_count = "--"
            if is_active:
                result = subprocess.run(
                    ['systemctl', 'show', 'meshtasticd', '--property=ActiveEnterTimestamp'],
                    capture_output=True, text=True
                )
                if 'ActiveEnterTimestamp=' in result.stdout:
                    timestamp = result.stdout.split('=')[1].strip()
                    if timestamp:
                        uptime = self._calculate_uptime(timestamp)

                # Try to get node count from meshtastic CLI
                node_count = self._get_node_count()

            # Update UI in main thread
            GLib.idle_add(self._update_status_ui, is_active, uptime, node_count)

        except Exception as e:
            GLib.idle_add(self._update_status_ui, False, "--", "--")

    def _get_node_count(self):
        """Get the number of nodes from meshtastic TCP interface or CLI"""
        import time as time_module
        import socket

        # Check cache first
        now = time_module.time()
        if now - self._node_count_timestamp < self._node_count_cache_ttl:
            return self._node_count_cache

        # Quick pre-check: is meshtasticd TCP port reachable?
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(("localhost", 4403))
            sock.close()
        except (socket.timeout, socket.error, OSError):
            # Port not reachable, skip node count
            return self._node_count_cache

        # Use CLI method (more reliable, avoids meshtastic library noise)
        try:
            import re
            # Find meshtastic CLI using centralized function
            try:
                from utils.cli import find_meshtastic_cli
                cli_path = find_meshtastic_cli()
            except ImportError:
                cli_path = shutil.which('meshtastic')

            if not cli_path:
                return self._node_count_cache

            # Run meshtastic --nodes to get node info (suppress stderr)
            result = subprocess.run(
                [cli_path, '--host', 'localhost', '--nodes'],
                capture_output=True, text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout:
                # Look for node IDs in the output (format: !xxxxxxxx)
                node_ids = re.findall(r'!([0-9a-fA-F]{8})', result.stdout)
                if node_ids:
                    self._node_count_cache = str(len(set(node_ids)))
                    self._node_count_timestamp = now
                    return self._node_count_cache
            return self._node_count_cache
        except subprocess.TimeoutExpired:
            return self._node_count_cache
        except Exception:
            return self._node_count_cache

    def _update_status_ui(self, is_active, uptime, node_count="--"):
        """Update status UI elements (must run in main thread)"""
        if is_active:
            self.service_status_icon.set_from_icon_name("emblem-default")
            self.service_status_label.set_label("Service: Running")
            self.service_status_label.remove_css_class("error")
            self.service_status_label.add_css_class("success")
        else:
            self.service_status_icon.set_from_icon_name("dialog-error")
            self.service_status_label.set_label("Service: Stopped")
            self.service_status_label.remove_css_class("success")
            self.service_status_label.add_css_class("error")

        self.uptime_label.set_label(f"Uptime: {uptime}")
        self.node_count_label.set_label(f"Nodes: {node_count}")
        return False

    def _calculate_uptime(self, timestamp):
        """Calculate uptime from timestamp"""
        try:
            from datetime import datetime
            import re

            if not timestamp or timestamp == 'n/a':
                return "--"

            # Try multiple timestamp formats
            # Format 1: "2026-01-02 10:30:45 UTC"
            # Format 2: "Thu 2026-01-02 10:30:45 UTC"
            # Format 3: "Thu Jan  2 10:30:45 2026"

            start = None

            # Try ISO format first (most common)
            try:
                # Remove timezone suffix
                clean_ts = re.sub(r'\s+(UTC|[A-Z]{3,4})$', '', timestamp)
                # Remove day name prefix if present
                clean_ts = re.sub(r'^[A-Za-z]+\s+', '', clean_ts)
                start = datetime.strptime(clean_ts[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass

            # Try alternative format (ctime-like)
            if not start:
                try:
                    # Format: "Thu Jan  2 10:30:45 2026"
                    start = datetime.strptime(timestamp, '%a %b %d %H:%M:%S %Y')
                except ValueError:
                    pass

            # Try with timezone
            if not start:
                try:
                    # Format: "Thu 2026-01-02 10:30:45 PST"
                    clean_ts = re.sub(r'\s+[A-Z]{3,4}$', '', timestamp)
                    start = datetime.strptime(clean_ts, '%a %Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass

            if not start:
                return "--"

            now = datetime.now()
            delta = now - start

            total_seconds = delta.total_seconds()
            if total_seconds < 0:
                return "--"

            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)

            if hours > 24:
                days = hours // 24
                hours = hours % 24
                return f"{days}d {hours}h"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        except (ValueError, TypeError, OSError):
            return "--"

    def open_terminal_editor(self, file_path, callback=None):
        """
        Open a file in nano editor in a terminal window.
        This ensures the user can always return to the installer.

        Args:
            file_path: Path to file to edit
            callback: Optional callback when editor closes
        """
        def run_editor():
            try:
                # Try different terminal emulators
                terminals = [
                    ['x-terminal-emulator', '-e'],
                    ['gnome-terminal', '--'],
                    ['xfce4-terminal', '-e'],
                    ['lxterminal', '-e'],
                    ['xterm', '-e'],
                ]

                for term_cmd in terminals:
                    try:
                        # Build command - nano with the file
                        cmd = term_cmd + ['nano', str(file_path)]

                        # Run and wait for completion
                        self.external_process = subprocess.Popen(cmd)
                        self.external_process.wait()
                        self.external_process = None

                        # Call callback in main thread
                        if callback:
                            GLib.idle_add(callback)
                        return

                    except FileNotFoundError:
                        continue

                # No terminal found - show error in main thread
                GLib.idle_add(
                    self._show_error_dialog,
                    "No Terminal Found",
                    "Could not find a terminal emulator. Please install one of:\n"
                    "gnome-terminal, xfce4-terminal, lxterminal, or xterm"
                )

            except Exception as e:
                GLib.idle_add(
                    self._show_error_dialog,
                    "Editor Error",
                    f"Failed to open editor: {e}"
                )

        # Run in thread to not block UI
        thread = threading.Thread(target=run_editor)
        thread.daemon = True
        thread.start()

    def run_command_with_output(self, command, callback=None):
        """
        Run a command and capture output for display.

        Args:
            command: Command list to run
            callback: Callback with (success, stdout, stderr)
        """
        def run_cmd():
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if callback:
                    GLib.idle_add(callback, result.returncode == 0, result.stdout, result.stderr)
            except subprocess.TimeoutExpired:
                if callback:
                    GLib.idle_add(callback, False, "", "Command timed out")
            except Exception as e:
                if callback:
                    GLib.idle_add(callback, False, "", str(e))

        thread = threading.Thread(target=run_cmd)
        thread.daemon = True
        thread.start()

    def _show_error_dialog(self, title, message):
        """Show an error dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def show_info_dialog(self, title, message):
        """Show an info dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def show_confirm_dialog(self, title, message, callback):
        """Show a confirmation dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("confirm", "Confirm")
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: callback(r == "confirm"))
        dialog.present()

    def request_reboot(self, reason):
        """
        Request a system reboot with persistence.
        Sets up autostart so installer returns after reboot.
        """
        def do_reboot(confirmed):
            if confirmed:
                # Save resume state
                self._save_resume_state(reason)

                # Enable autostart
                self._enable_autostart()

                # Schedule reboot
                self.set_status_message("Rebooting in 5 seconds...")
                GLib.timeout_add_seconds(5, self._perform_reboot)

        self.show_confirm_dialog(
            "Reboot Required",
            f"{reason}\n\nThe system needs to reboot. The installer will automatically "
            "restart after reboot.\n\nReboot now?",
            do_reboot
        )

    def _perform_reboot(self):
        """Perform the actual reboot"""
        try:
            subprocess.run(['systemctl', 'reboot'], check=True)
        except Exception as e:
            self._show_error_dialog("Reboot Failed", str(e))
        return False

    def _save_resume_state(self, reason):
        """Save state for resume after reboot"""
        state_file = Path.home() / '.meshtasticd-installer-resume'
        try:
            state_file.write_text(f"reason={reason}\npage=config\n")
        except Exception as e:
            print(f"Failed to save resume state: {e}")

    def _check_resume_state(self):
        """Check if we're resuming after reboot"""
        state_file = Path.home() / '.meshtasticd-installer-resume'
        if state_file.exists():
            try:
                content = state_file.read_text()
                state_file.unlink()  # Remove the state file

                # Parse state
                state = {}
                for line in content.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        state[key] = value

                # Show resume message
                reason = state.get('reason', 'Unknown')
                self.show_info_dialog(
                    "Welcome Back",
                    f"System rebooted successfully.\n\nReason: {reason}\n\n"
                    "You can now continue with configuration."
                )

                # Navigate to the appropriate page
                page = state.get('page', 'dashboard')
                self.content_stack.set_visible_child_name(page)

            except Exception as e:
                print(f"Failed to read resume state: {e}")

    def _enable_autostart(self):
        """Enable autostart for the installer after reboot"""
        autostart_dir = Path.home() / '.config' / 'autostart'
        autostart_dir.mkdir(parents=True, exist_ok=True)

        desktop_entry = f"""[Desktop Entry]
Type=Application
Name=Meshtasticd Manager
Exec=sudo python3 {Path(__file__).parent.parent}/main_gtk.py
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
Comment=Resume Meshtasticd Manager after reboot
"""

        desktop_file = autostart_dir / 'meshtasticd-manager.desktop'
        try:
            desktop_file.write_text(desktop_entry)
        except Exception as e:
            print(f"Failed to create autostart entry: {e}")

    def _disable_autostart(self):
        """Disable autostart"""
        desktop_file = Path.home() / '.config' / 'autostart' / 'meshtasticd-manager.desktop'
        try:
            if desktop_file.exists():
                desktop_file.unlink()
        except Exception as e:
            print(f"Failed to remove autostart entry: {e}")


def main():
    """Main entry point for GTK4 application"""
    app = MeshtasticdApp()
    return app.run(sys.argv)


if __name__ == '__main__':
    main()
