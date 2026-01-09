"""
MeshBot Panel - Integration with SpudGunMan's meshing-around

Mesh Bot provides BBS messaging, inventory/POS, weather alerts, games,
and more over Meshtastic networks.

Reference: https://github.com/SpudGunMan/meshing-around
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Pango
import json
import threading
import subprocess
import time
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Import path utilities
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

# Use centralized settings manager
try:
    from utils.common import SettingsManager
    HAS_SETTINGS_MANAGER = True
except ImportError:
    HAS_SETTINGS_MANAGER = False


class MeshBotPanel(Gtk.Box):
    """Panel for MeshBot (meshing-around) integration"""

    SETTINGS_DEFAULTS = {
        "install_path": "",
        "config_file": "",
        "auto_start": False,
        "web_port": 8420,
    }

    # Default install location
    DEFAULT_INSTALL_PATH = "/opt/meshing-around"

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self._process = None
        self._log_lines = []

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        # Load settings
        if HAS_SETTINGS_MANAGER:
            self._settings_mgr = SettingsManager("meshbot", defaults=self.SETTINGS_DEFAULTS)
            self._settings = self._settings_mgr.all()
        else:
            self._settings = self.SETTINGS_DEFAULTS.copy()

        self._build_ui()

        # Check status on startup
        GLib.timeout_add(500, self._check_status)

    def _save_settings(self):
        """Save settings"""
        if HAS_SETTINGS_MANAGER:
            self._settings_mgr.update(self._settings)
            self._settings_mgr.save()

    def _build_ui(self):
        """Build the MeshBot panel UI"""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        title = Gtk.Label(label="MeshBot")
        title.add_css_class("title-1")
        title.set_xalign(0)
        header_box.append(title)

        # Status indicator
        self.status_label = Gtk.Label(label="Checking...")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_hexpand(True)
        self.status_label.set_xalign(1)
        header_box.append(self.status_label)

        self.append(header_box)

        subtitle = Gtk.Label(label="BBS, Inventory, Weather & Games for Meshtastic")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Create notebook for tabs
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)

        # Tab 1: Status & Control
        status_page = self._build_status_tab()
        self.notebook.append_page(status_page, Gtk.Label(label="Status"))

        # Tab 2: Configuration
        config_page = self._build_config_tab()
        self.notebook.append_page(config_page, Gtk.Label(label="Config"))

        # Tab 3: E-Commerce / Inventory
        ecomm_page = self._build_ecomm_tab()
        self.notebook.append_page(ecomm_page, Gtk.Label(label="E-Comm"))

        # Tab 4: Logs
        logs_page = self._build_logs_tab()
        self.notebook.append_page(logs_page, Gtk.Label(label="Logs"))

        self.append(self.notebook)

    def _build_status_tab(self):
        """Build the status and control tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Installation status frame
        install_frame = Gtk.Frame()
        install_frame.set_label("Installation")
        install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        install_box.set_margin_start(15)
        install_box.set_margin_end(15)
        install_box.set_margin_top(10)
        install_box.set_margin_bottom(10)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self.install_status_icon = Gtk.Image.new_from_icon_name("emblem-question-symbolic")
        self.install_status_icon.set_pixel_size(32)
        status_row.append(self.install_status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.install_status_label = Gtk.Label(label="Checking installation...")
        self.install_status_label.set_xalign(0)
        self.install_status_label.add_css_class("heading")
        status_info.append(self.install_status_label)

        self.install_detail_label = Gtk.Label(label="")
        self.install_detail_label.set_xalign(0)
        self.install_detail_label.add_css_class("dim-label")
        status_info.append(self.install_detail_label)

        status_row.append(status_info)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        # Install button
        self.install_btn = Gtk.Button(label="Install MeshBot")
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.connect("clicked", self._on_install)
        self.install_btn.set_tooltip_text("Clone and set up meshing-around from GitHub")
        status_row.append(self.install_btn)

        install_box.append(status_row)

        # Install path entry
        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        path_row.append(Gtk.Label(label="Install Path:"))

        self.path_entry = Gtk.Entry()
        self.path_entry.set_text(self._settings.get("install_path", self.DEFAULT_INSTALL_PATH) or self.DEFAULT_INSTALL_PATH)
        self.path_entry.set_hexpand(True)
        self.path_entry.set_placeholder_text("/opt/meshing-around")
        path_row.append(self.path_entry)

        browse_btn = Gtk.Button(label="Browse")
        browse_btn.connect("clicked", self._on_browse_path)
        path_row.append(browse_btn)

        install_box.append(path_row)

        install_frame.set_child(install_box)
        box.append(install_frame)

        # Service control frame
        service_frame = Gtk.Frame()
        service_frame.set_label("Bot Control")
        service_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        service_box.set_margin_start(15)
        service_box.set_margin_end(15)
        service_box.set_margin_top(10)
        service_box.set_margin_bottom(10)

        # Bot status row
        bot_status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self.bot_status_icon = Gtk.Image.new_from_icon_name("emblem-question-symbolic")
        self.bot_status_icon.set_pixel_size(24)
        bot_status_row.append(self.bot_status_icon)

        self.bot_status_label = Gtk.Label(label="Bot Status: Unknown")
        self.bot_status_label.set_xalign(0)
        bot_status_row.append(self.bot_status_label)

        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        bot_status_row.append(spacer2)

        # Control buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        self.start_btn = Gtk.Button(label="Start")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.connect("clicked", self._on_start_bot)
        self.start_btn.set_sensitive(False)
        btn_box.append(self.start_btn)

        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.connect("clicked", self._on_stop_bot)
        self.stop_btn.set_sensitive(False)
        btn_box.append(self.stop_btn)

        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh Status")
        refresh_btn.connect("clicked", lambda b: self._check_status())
        btn_box.append(refresh_btn)

        bot_status_row.append(btn_box)
        service_box.append(bot_status_row)

        # Bot type selector
        type_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        type_row.append(Gtk.Label(label="Bot Type:"))

        self.bot_type_combo = Gtk.ComboBoxText()
        self.bot_type_combo.append("mesh_bot", "Full MeshBot (mesh_bot.py)")
        self.bot_type_combo.append("pong_bot", "Simple Pong Bot (pong_bot.py)")
        self.bot_type_combo.set_active_id("mesh_bot")
        type_row.append(self.bot_type_combo)

        type_info = Gtk.Label(label="Full bot has BBS, games, weather. Pong is minimal responder.")
        type_info.add_css_class("dim-label")
        type_info.set_xalign(0)
        type_info.set_hexpand(True)
        type_row.append(type_info)

        service_box.append(type_row)

        service_frame.set_child(service_box)
        box.append(service_frame)

        # Features overview
        features_frame = Gtk.Frame()
        features_frame.set_label("MeshBot Features")
        features_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        features_box.set_margin_start(15)
        features_box.set_margin_end(15)
        features_box.set_margin_top(10)
        features_box.set_margin_bottom(10)

        features = [
            ("mail-unread-symbolic", "BBS Messaging", "Store-and-forward bulletin board system"),
            ("accessories-calculator-symbolic", "Inventory/POS", "Point-of-sale system with cart and transactions"),
            ("weather-few-clouds-symbolic", "Weather Alerts", "NOAA, USGS earthquake, river, tide data"),
            ("input-gaming-symbolic", "Games", "DopeWars, Lemonade Stand, BlackJack, Poker"),
            ("dialog-warning-symbolic", "Emergency Alerts", "FEMA iPAWS, EAS, volcano alerts"),
            ("face-smile-symbolic", "LLM Integration", "Ollama AI with RAG support"),
        ]

        for icon, title, desc in features:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            img = Gtk.Image.new_from_icon_name(icon)
            row.append(img)

            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title_label = Gtk.Label(label=title)
            title_label.set_xalign(0)
            title_label.add_css_class("heading")
            text_box.append(title_label)

            desc_label = Gtk.Label(label=desc)
            desc_label.set_xalign(0)
            desc_label.add_css_class("dim-label")
            text_box.append(desc_label)

            row.append(text_box)
            features_box.append(row)

        features_frame.set_child(features_box)
        box.append(features_frame)

        # Links
        links_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        github_btn = Gtk.Button(label="GitHub Repository")
        github_btn.connect("clicked", lambda b: self._open_url("https://github.com/SpudGunMan/meshing-around"))
        links_box.append(github_btn)

        docs_btn = Gtk.Button(label="Documentation")
        docs_btn.connect("clicked", lambda b: self._open_url("https://github.com/SpudGunMan/meshing-around#readme"))
        links_box.append(docs_btn)

        box.append(links_box)

        scrolled.set_child(box)
        return scrolled

    def _build_config_tab(self):
        """Build the configuration tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Config file selector
        config_frame = Gtk.Frame()
        config_frame.set_label("Configuration File")
        config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        config_box.set_margin_start(15)
        config_box.set_margin_end(15)
        config_box.set_margin_top(10)
        config_box.set_margin_bottom(10)

        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        file_row.append(Gtk.Label(label="Config File:"))

        self.config_file_entry = Gtk.Entry()
        self.config_file_entry.set_hexpand(True)
        self.config_file_entry.set_placeholder_text("config.ini")
        file_row.append(self.config_file_entry)

        load_btn = Gtk.Button(label="Load")
        load_btn.connect("clicked", self._on_load_config)
        file_row.append(load_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save_config)
        file_row.append(save_btn)

        config_box.append(file_row)

        # Config editor
        self.config_text = Gtk.TextView()
        self.config_text.set_monospace(True)
        self.config_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        config_scroll = Gtk.ScrolledWindow()
        config_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        config_scroll.set_min_content_height(300)
        config_scroll.set_child(self.config_text)

        config_box.append(config_scroll)

        # Quick settings
        quick_label = Gtk.Label(label="Quick Settings (edit config for full options)")
        quick_label.set_xalign(0)
        quick_label.add_css_class("dim-label")
        config_box.append(quick_label)

        quick_grid = Gtk.Grid()
        quick_grid.set_column_spacing(15)
        quick_grid.set_row_spacing(8)

        # Device connection type
        quick_grid.attach(Gtk.Label(label="Device Type:"), 0, 0, 1, 1)
        self.device_type_combo = Gtk.ComboBoxText()
        self.device_type_combo.append("serial", "Serial (USB)")
        self.device_type_combo.append("tcp", "TCP/IP")
        self.device_type_combo.append("ble", "Bluetooth (BLE)")
        self.device_type_combo.set_active_id("serial")
        quick_grid.attach(self.device_type_combo, 1, 0, 1, 1)

        # Serial port / host
        quick_grid.attach(Gtk.Label(label="Port/Host:"), 0, 1, 1, 1)
        self.device_addr_entry = Gtk.Entry()
        self.device_addr_entry.set_placeholder_text("/dev/ttyUSB0 or 192.168.1.100")
        self.device_addr_entry.set_hexpand(True)
        quick_grid.attach(self.device_addr_entry, 1, 1, 1, 1)

        # Location
        quick_grid.attach(Gtk.Label(label="Location:"), 0, 2, 1, 1)
        loc_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.lat_entry = Gtk.Entry()
        self.lat_entry.set_placeholder_text("Latitude")
        self.lat_entry.set_width_chars(12)
        loc_box.append(self.lat_entry)
        self.lon_entry = Gtk.Entry()
        self.lon_entry.set_placeholder_text("Longitude")
        self.lon_entry.set_width_chars(12)
        loc_box.append(self.lon_entry)
        quick_grid.attach(loc_box, 1, 2, 1, 1)

        config_box.append(quick_grid)

        config_frame.set_child(config_box)
        box.append(config_frame)

        # Module toggles
        modules_frame = Gtk.Frame()
        modules_frame.set_label("Enabled Modules")
        modules_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        modules_box.set_margin_start(15)
        modules_box.set_margin_end(15)
        modules_box.set_margin_top(10)
        modules_box.set_margin_bottom(10)

        self.module_switches = {}
        modules = [
            ("bbs", "BBS Messaging", True),
            ("weather", "Weather Data", True),
            ("games", "Games", True),
            ("inventory", "Inventory/POS", False),
            ("llm", "LLM/Ollama", False),
            ("emergency", "Emergency Alerts", True),
        ]

        for key, label, default in modules:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            lbl = Gtk.Label(label=label)
            lbl.set_xalign(0)
            lbl.set_hexpand(True)
            row.append(lbl)

            switch = Gtk.Switch()
            switch.set_active(default)
            row.append(switch)
            self.module_switches[key] = switch

            modules_box.append(row)

        modules_frame.set_child(modules_box)
        box.append(modules_frame)

        scrolled.set_child(box)
        return scrolled

    def _build_ecomm_tab(self):
        """Build the E-Commerce / Inventory tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Info banner
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        info_box.add_css_class("dim-label")

        info_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        info_box.append(info_icon)

        info_label = Gtk.Label(
            label="MeshBot Inventory is a point-of-sale system accessible via mesh commands. "
                  "Users send 'itemlist', 'cartadd', 'cartbuy' etc. over Meshtastic."
        )
        info_label.set_wrap(True)
        info_label.set_xalign(0)
        info_box.append(info_label)

        box.append(info_box)

        # Inventory management
        inv_frame = Gtk.Frame()
        inv_frame.set_label("Inventory Items")
        inv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inv_box.set_margin_start(15)
        inv_box.set_margin_end(15)
        inv_box.set_margin_top(10)
        inv_box.set_margin_bottom(10)

        # Inventory list
        self.inv_store = Gtk.ListStore(str, str, str, str)  # name, price, qty, location

        self.inv_tree = Gtk.TreeView(model=self.inv_store)
        self.inv_tree.set_headers_visible(True)

        for i, title in enumerate(["Item Name", "Price", "Quantity", "Location"]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            column.set_resizable(True)
            column.set_min_width(80)
            self.inv_tree.append_column(column)

        inv_scroll = Gtk.ScrolledWindow()
        inv_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        inv_scroll.set_min_content_height(200)
        inv_scroll.set_child(self.inv_tree)
        inv_box.append(inv_scroll)

        # Inventory controls
        inv_ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        refresh_inv_btn = Gtk.Button(label="Refresh")
        refresh_inv_btn.connect("clicked", self._on_refresh_inventory)
        inv_ctrl.append(refresh_inv_btn)

        add_item_btn = Gtk.Button(label="Add Item")
        add_item_btn.add_css_class("suggested-action")
        add_item_btn.connect("clicked", self._on_add_item)
        inv_ctrl.append(add_item_btn)

        inv_box.append(inv_ctrl)

        inv_frame.set_child(inv_box)
        box.append(inv_frame)

        # Transactions
        trans_frame = Gtk.Frame()
        trans_frame.set_label("Recent Transactions")
        trans_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        trans_box.set_margin_start(15)
        trans_box.set_margin_end(15)
        trans_box.set_margin_top(10)
        trans_box.set_margin_bottom(10)

        self.trans_store = Gtk.ListStore(str, str, str, str)  # date, type, user, amount

        self.trans_tree = Gtk.TreeView(model=self.trans_store)
        self.trans_tree.set_headers_visible(True)

        for i, title in enumerate(["Date", "Type", "User", "Amount"]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            column.set_resizable(True)
            self.trans_tree.append_column(column)

        trans_scroll = Gtk.ScrolledWindow()
        trans_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        trans_scroll.set_min_content_height(150)
        trans_scroll.set_child(self.trans_tree)
        trans_box.append(trans_scroll)

        trans_frame.set_child(trans_box)
        box.append(trans_frame)

        # Commands reference
        cmd_frame = Gtk.Frame()
        cmd_frame.set_label("Inventory Commands (send via mesh)")
        cmd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        cmd_box.set_margin_start(15)
        cmd_box.set_margin_end(15)
        cmd_box.set_margin_top(10)
        cmd_box.set_margin_bottom(10)

        commands = [
            ("itemlist", "List all available items"),
            ("cartadd <item>", "Add item to your cart"),
            ("cartlist", "View your cart"),
            ("cartbuy", "Purchase items in cart"),
            ("cartsell", "Sell items from cart"),
            ("cartclear", "Empty your cart"),
            ("itemstats", "View sales statistics"),
        ]

        for cmd, desc in commands:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            cmd_label = Gtk.Label(label=cmd)
            cmd_label.set_xalign(0)
            cmd_label.add_css_class("monospace")
            cmd_label.set_width_chars(20)
            row.append(cmd_label)

            desc_label = Gtk.Label(label=desc)
            desc_label.set_xalign(0)
            desc_label.add_css_class("dim-label")
            row.append(desc_label)

            cmd_box.append(row)

        cmd_frame.set_child(cmd_box)
        box.append(cmd_frame)

        scrolled.set_child(box)
        return scrolled

    def _build_logs_tab(self):
        """Build the logs tab"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Controls
        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        refresh_btn = Gtk.Button(label="Refresh Logs")
        refresh_btn.connect("clicked", self._on_refresh_logs)
        ctrl_box.append(refresh_btn)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", self._on_clear_logs)
        ctrl_box.append(clear_btn)

        self.auto_scroll_check = Gtk.CheckButton(label="Auto-scroll")
        self.auto_scroll_check.set_active(True)
        ctrl_box.append(self.auto_scroll_check)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        ctrl_box.append(spacer)

        open_folder_btn = Gtk.Button(label="Open Logs Folder")
        open_folder_btn.connect("clicked", self._on_open_logs_folder)
        ctrl_box.append(open_folder_btn)

        box.append(ctrl_box)

        # Log viewer
        self.log_text = Gtk.TextView()
        self.log_text.set_editable(False)
        self.log_text.set_monospace(True)
        self.log_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_vexpand(True)
        log_scroll.set_child(self.log_text)
        self.log_scroll = log_scroll

        box.append(log_scroll)

        return box

    def _check_status(self):
        """Check MeshBot installation and running status"""
        def check():
            status = {
                'installed': False,
                'running': False,
                'path': None,
                'version': None,
            }

            # Check configured path first
            install_path = self._settings.get("install_path") or self.DEFAULT_INSTALL_PATH
            paths_to_check = [
                install_path,
                self.DEFAULT_INSTALL_PATH,
                str(get_real_user_home() / "meshing-around"),
                "/home/pi/meshing-around",
            ]

            for path in paths_to_check:
                mesh_bot = Path(path) / "mesh_bot.py"
                if mesh_bot.exists():
                    status['installed'] = True
                    status['path'] = path
                    break

            # Check if running
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'mesh_bot.py'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    status['running'] = True
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

            # Also check for pong_bot
            if not status['running']:
                try:
                    result = subprocess.run(
                        ['pgrep', '-f', 'pong_bot.py'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        status['running'] = True
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

            GLib.idle_add(self._update_status_ui, status)

        threading.Thread(target=check, daemon=True).start()
        return False

    def _update_status_ui(self, status):
        """Update the status UI"""
        if status['installed']:
            self.install_status_icon.set_from_icon_name("emblem-default-symbolic")
            self.install_status_label.set_label("MeshBot Installed")
            self.install_detail_label.set_label(f"Path: {status['path']}")
            self.install_btn.set_label("Reinstall")
            self.start_btn.set_sensitive(not status['running'])
            self.stop_btn.set_sensitive(status['running'])

            # Save found path
            if status['path']:
                self._settings["install_path"] = status['path']
                self.path_entry.set_text(status['path'])
                self._save_settings()

                # Update config file path
                config_path = Path(status['path']) / "config.ini"
                if config_path.exists():
                    self.config_file_entry.set_text(str(config_path))

        else:
            self.install_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.install_status_label.set_label("MeshBot Not Installed")
            self.install_detail_label.set_label("Click Install to set up meshing-around")
            self.install_btn.set_label("Install MeshBot")
            self.start_btn.set_sensitive(False)
            self.stop_btn.set_sensitive(False)

        if status['running']:
            self.bot_status_icon.set_from_icon_name("emblem-default-symbolic")
            self.bot_status_label.set_label("Bot Status: Running")
            self.status_label.set_label("Running")
        else:
            self.bot_status_icon.set_from_icon_name("media-playback-stop-symbolic")
            self.bot_status_label.set_label("Bot Status: Stopped")
            if status['installed']:
                self.status_label.set_label("Installed (stopped)")
            else:
                self.status_label.set_label("Not installed")

    def _on_install(self, button):
        """Install meshing-around from GitHub"""
        install_path = self.path_entry.get_text().strip() or self.DEFAULT_INSTALL_PATH

        button.set_sensitive(False)
        self.status_label.set_label("Installing...")
        self.main_window.set_status_message("Installing MeshBot...")

        def do_install():
            try:
                install_dir = Path(install_path)

                # Create parent directory if needed
                if not install_dir.parent.exists():
                    subprocess.run(
                        ['sudo', 'mkdir', '-p', str(install_dir.parent)],
                        timeout=10
                    )

                # Clone or update repository
                if install_dir.exists():
                    # Update existing
                    result = subprocess.run(
                        ['git', '-C', str(install_dir), 'pull'],
                        capture_output=True, text=True, timeout=120
                    )
                    action = "Updated"
                else:
                    # Clone new
                    result = subprocess.run(
                        ['sudo', 'git', 'clone',
                         'https://github.com/SpudGunMan/meshing-around.git',
                         str(install_dir)],
                        capture_output=True, text=True, timeout=300
                    )
                    action = "Installed"

                if result.returncode != 0:
                    GLib.idle_add(self._install_complete, False, result.stderr, button)
                    return

                # Run install script if it exists
                install_script = install_dir / "install.sh"
                if install_script.exists():
                    GLib.idle_add(lambda: self.status_label.set_label("Running setup..."))
                    subprocess.run(
                        ['sudo', 'bash', str(install_script)],
                        cwd=str(install_dir),
                        capture_output=True, timeout=600
                    )

                # Copy config template if needed
                config_template = install_dir / "config.template"
                config_file = install_dir / "config.ini"
                if config_template.exists() and not config_file.exists():
                    subprocess.run(
                        ['sudo', 'cp', str(config_template), str(config_file)],
                        timeout=10
                    )

                # Set ownership to real user
                real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
                subprocess.run(
                    ['sudo', 'chown', '-R', f'{real_user}:{real_user}', str(install_dir)],
                    timeout=30
                )

                GLib.idle_add(self._install_complete, True, f"{action} successfully!", button)

            except subprocess.TimeoutExpired:
                GLib.idle_add(self._install_complete, False, "Installation timed out", button)
            except Exception as e:
                GLib.idle_add(self._install_complete, False, str(e), button)

        threading.Thread(target=do_install, daemon=True).start()

    def _install_complete(self, success, message, button):
        """Handle installation completion"""
        button.set_sensitive(True)

        if success:
            self.status_label.set_label(message)
            self.main_window.set_status_message(f"MeshBot: {message}")
            self._settings["install_path"] = self.path_entry.get_text().strip()
            self._save_settings()
            GLib.timeout_add(1000, self._check_status)
        else:
            self.status_label.set_label(f"Install failed: {message[:50]}")
            self.main_window.set_status_message(f"Install failed: {message}")
            logger.error(f"MeshBot install failed: {message}")

    def _on_browse_path(self, button):
        """Browse for install path"""
        dialog = Gtk.FileDialog()
        dialog.set_title("Select MeshBot Install Directory")

        def on_response(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
                if folder:
                    self.path_entry.set_text(folder.get_path())
            except Exception:
                pass

        dialog.select_folder(self.main_window, None, on_response)

    def _on_start_bot(self, button):
        """Start the MeshBot"""
        install_path = self._settings.get("install_path", self.DEFAULT_INSTALL_PATH)
        bot_type = self.bot_type_combo.get_active_id() or "mesh_bot"

        script_path = Path(install_path) / f"{bot_type}.py"
        if not script_path.exists():
            self.status_label.set_label(f"Script not found: {script_path}")
            return

        self.start_btn.set_sensitive(False)
        self.status_label.set_label("Starting bot...")

        def do_start():
            try:
                # Check for launch.sh
                launch_script = Path(install_path) / "launch.sh"

                if launch_script.exists():
                    cmd = ['bash', str(launch_script)]
                else:
                    # Direct Python execution
                    cmd = ['python3', str(script_path)]

                # Start in background
                process = subprocess.Popen(
                    cmd,
                    cwd=install_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True
                )

                time.sleep(2)  # Give it time to start

                if process.poll() is None:
                    GLib.idle_add(self._start_complete, True, "Bot started")
                else:
                    _, stderr = process.communicate(timeout=5)
                    GLib.idle_add(self._start_complete, False, stderr.decode()[:100])

            except Exception as e:
                GLib.idle_add(self._start_complete, False, str(e))

        threading.Thread(target=do_start, daemon=True).start()

    def _start_complete(self, success, message):
        """Handle start completion"""
        self.start_btn.set_sensitive(True)

        if success:
            self.status_label.set_label(message)
            self.main_window.set_status_message("MeshBot started")
        else:
            self.status_label.set_label(f"Start failed: {message}")

        GLib.timeout_add(1000, self._check_status)

    def _on_stop_bot(self, button):
        """Stop the MeshBot"""
        self.stop_btn.set_sensitive(False)
        self.status_label.set_label("Stopping bot...")

        def do_stop():
            try:
                # Find and kill mesh_bot processes
                subprocess.run(
                    ['pkill', '-f', 'mesh_bot.py'],
                    timeout=10
                )
                subprocess.run(
                    ['pkill', '-f', 'pong_bot.py'],
                    timeout=10
                )

                time.sleep(1)
                GLib.idle_add(self._stop_complete, True, "Bot stopped")

            except Exception as e:
                GLib.idle_add(self._stop_complete, False, str(e))

        threading.Thread(target=do_stop, daemon=True).start()

    def _stop_complete(self, success, message):
        """Handle stop completion"""
        self.stop_btn.set_sensitive(True)
        self.status_label.set_label(message)

        if success:
            self.main_window.set_status_message("MeshBot stopped")

        GLib.timeout_add(1000, self._check_status)

    def _on_load_config(self, button):
        """Load config file into editor"""
        config_path = self.config_file_entry.get_text().strip()

        if not config_path:
            install_path = self._settings.get("install_path", self.DEFAULT_INSTALL_PATH)
            config_path = str(Path(install_path) / "config.ini")
            self.config_file_entry.set_text(config_path)

        def do_load():
            try:
                path = Path(config_path)
                if path.exists():
                    content = path.read_text()
                    GLib.idle_add(self._set_config_text, content)
                else:
                    GLib.idle_add(self._set_config_text, f"# Config file not found: {config_path}\n# Copy from config.template")
            except Exception as e:
                GLib.idle_add(self._set_config_text, f"# Error loading config: {e}")

        threading.Thread(target=do_load, daemon=True).start()

    def _set_config_text(self, text):
        """Set config text in editor"""
        buffer = self.config_text.get_buffer()
        buffer.set_text(text)

    def _on_save_config(self, button):
        """Save config file"""
        config_path = self.config_file_entry.get_text().strip()

        if not config_path:
            self.status_label.set_label("No config file specified")
            return

        buffer = self.config_text.get_buffer()
        start, end = buffer.get_bounds()
        content = buffer.get_text(start, end, False)

        def do_save():
            try:
                path = Path(config_path)
                path.write_text(content)
                GLib.idle_add(lambda: self.status_label.set_label("Config saved"))
                GLib.idle_add(lambda: self.main_window.set_status_message("MeshBot config saved"))
            except PermissionError:
                # Try with sudo
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.ini') as f:
                        f.write(content)
                        temp_path = f.name

                    subprocess.run(
                        ['sudo', 'cp', temp_path, config_path],
                        timeout=10
                    )
                    os.unlink(temp_path)
                    GLib.idle_add(lambda: self.status_label.set_label("Config saved (sudo)"))
                except Exception as e:
                    GLib.idle_add(lambda: self.status_label.set_label(f"Save failed: {e}"))
            except Exception as e:
                GLib.idle_add(lambda: self.status_label.set_label(f"Save failed: {e}"))

        threading.Thread(target=do_save, daemon=True).start()

    def _on_refresh_inventory(self, button):
        """Refresh inventory list from MeshBot database"""
        install_path = self._settings.get("install_path", self.DEFAULT_INSTALL_PATH)

        def do_refresh():
            try:
                # Look for inventory database
                db_path = Path(install_path) / "data" / "inventory.db"
                if not db_path.exists():
                    db_path = Path(install_path) / "inventory.db"

                if not db_path.exists():
                    GLib.idle_add(self._update_inventory, [], "No inventory database found")
                    return

                import sqlite3
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute("SELECT name, price, quantity, location FROM items")
                items = cursor.fetchall()
                conn.close()

                GLib.idle_add(self._update_inventory, items, None)

            except Exception as e:
                GLib.idle_add(self._update_inventory, [], str(e))

        threading.Thread(target=do_refresh, daemon=True).start()

    def _update_inventory(self, items, error):
        """Update inventory display"""
        self.inv_store.clear()

        if error:
            self.status_label.set_label(f"Inventory: {error}")
            return

        for item in items:
            self.inv_store.append([
                str(item[0]),  # name
                f"${item[1]:.2f}" if item[1] else "--",  # price
                str(item[2]) if item[2] else "0",  # qty
                str(item[3]) if item[3] else "--",  # location
            ])

        self.status_label.set_label(f"Inventory: {len(items)} items")

    def _on_add_item(self, button):
        """Show dialog to add inventory item"""
        # Simple dialog for adding items
        dialog = Gtk.Dialog(
            title="Add Inventory Item",
            transient_for=self.main_window,
            modal=True,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Add", Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_spacing(10)

        # Form fields
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(8)

        grid.attach(Gtk.Label(label="Item Name:"), 0, 0, 1, 1)
        name_entry = Gtk.Entry()
        grid.attach(name_entry, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Price:"), 0, 1, 1, 1)
        price_entry = Gtk.Entry()
        price_entry.set_placeholder_text("0.00")
        grid.attach(price_entry, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Quantity:"), 0, 2, 1, 1)
        qty_entry = Gtk.Entry()
        qty_entry.set_placeholder_text("1")
        grid.attach(qty_entry, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="Location:"), 0, 3, 1, 1)
        loc_entry = Gtk.Entry()
        grid.attach(loc_entry, 1, 3, 1, 1)

        content.append(grid)

        def on_response(dialog, response):
            if response == Gtk.ResponseType.OK:
                name = name_entry.get_text().strip()
                price = price_entry.get_text().strip()
                qty = qty_entry.get_text().strip()
                location = loc_entry.get_text().strip()

                if name:
                    self._add_inventory_item(name, price, qty, location)

            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _add_inventory_item(self, name, price, qty, location):
        """Add item to inventory database"""
        install_path = self._settings.get("install_path", self.DEFAULT_INSTALL_PATH)

        def do_add():
            try:
                db_path = Path(install_path) / "data" / "inventory.db"
                if not db_path.exists():
                    db_path = Path(install_path) / "inventory.db"

                if not db_path.exists():
                    # Create database
                    db_path.parent.mkdir(parents=True, exist_ok=True)

                import sqlite3
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                # Create table if not exists
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS items (
                        id INTEGER PRIMARY KEY,
                        name TEXT,
                        price REAL,
                        quantity INTEGER,
                        location TEXT
                    )
                """)

                cursor.execute(
                    "INSERT INTO items (name, price, quantity, location) VALUES (?, ?, ?, ?)",
                    (name, float(price or 0), int(qty or 1), location)
                )
                conn.commit()
                conn.close()

                GLib.idle_add(lambda: self.status_label.set_label(f"Added: {name}"))
                GLib.idle_add(lambda: self._on_refresh_inventory(None))

            except Exception as e:
                GLib.idle_add(lambda: self.status_label.set_label(f"Add failed: {e}"))

        threading.Thread(target=do_add, daemon=True).start()

    def _on_refresh_logs(self, button):
        """Refresh log display"""
        install_path = self._settings.get("install_path", self.DEFAULT_INSTALL_PATH)

        def do_refresh():
            try:
                log_dir = Path(install_path) / "logs"
                if not log_dir.exists():
                    log_dir = Path(install_path)

                # Find log files
                log_files = list(log_dir.glob("*.log")) + list(log_dir.glob("log*.txt"))

                if not log_files:
                    GLib.idle_add(self._set_log_text, "No log files found")
                    return

                # Get most recent log
                latest = max(log_files, key=lambda p: p.stat().st_mtime)
                content = latest.read_text()

                # Get last 200 lines
                lines = content.split('\n')[-200:]
                GLib.idle_add(self._set_log_text, '\n'.join(lines))

            except Exception as e:
                GLib.idle_add(self._set_log_text, f"Error loading logs: {e}")

        threading.Thread(target=do_refresh, daemon=True).start()

    def _set_log_text(self, text):
        """Set log text"""
        buffer = self.log_text.get_buffer()
        buffer.set_text(text)

        if self.auto_scroll_check.get_active():
            # Scroll to bottom
            end_iter = buffer.get_end_iter()
            self.log_text.scroll_to_iter(end_iter, 0, False, 0, 0)

    def _on_clear_logs(self, button):
        """Clear log display"""
        buffer = self.log_text.get_buffer()
        buffer.set_text("")

    def _on_open_logs_folder(self, button):
        """Open logs folder in file manager"""
        install_path = self._settings.get("install_path", self.DEFAULT_INSTALL_PATH)
        log_dir = Path(install_path) / "logs"

        if not log_dir.exists():
            log_dir = Path(install_path)

        self._open_folder(str(log_dir))

    def _open_url(self, url):
        """Open URL in browser"""
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        is_root = os.geteuid() == 0

        try:
            if is_root and real_user != 'root':
                subprocess.Popen(
                    ['sudo', '-u', real_user, 'xdg-open', url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
            else:
                subprocess.Popen(
                    ['xdg-open', url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
        except Exception as e:
            logger.error(f"Failed to open URL: {e}")

    def _open_folder(self, path):
        """Open folder in file manager"""
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        is_root = os.geteuid() == 0

        try:
            if is_root and real_user != 'root':
                subprocess.Popen(
                    ['sudo', '-u', real_user, 'xdg-open', path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
            else:
                subprocess.Popen(
                    ['xdg-open', path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
        except Exception as e:
            logger.error(f"Failed to open folder: {e}")

    def cleanup(self):
        """Clean up resources"""
        pass
