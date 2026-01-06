"""
Settings Panel - Application preferences, theme settings, and simulation controls
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
from pathlib import Path
import os

# Import centralized path utility
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


class SettingsPanel(Gtk.Box):
    """Settings panel with theme, preferences, and simulation options"""

    # Settings file location
    SETTINGS_FILE = get_real_user_home() / ".config" / "meshforge" / "settings.json"

    # Settings defaults
    SETTINGS_DEFAULTS = {
        "theme": "dark",        # "system", "dark", "light"
        "dark_mode": True,      # Legacy, kept for compatibility
        "auto_refresh": True,
        "refresh_interval": 5,
        "show_node_ids": True,
        "compact_mode": False,
        "simulation_mode": "disabled",
        "simulation_preset": "hawaii",  # "hawaii" or "generic"
    }

    # Theme options
    THEME_OPTIONS = [
        ("system", "System Default"),
        ("dark", "Dark"),
        ("light", "Light"),
    ]

    # Simulation mode options
    SIMULATION_OPTIONS = [
        ("disabled", "Disabled (Real Hardware)"),
        ("rf_only", "RF Simulation Only"),
        ("mesh_network", "Mesh Network Simulation"),
        ("full", "Full Hardware Simulation"),
    ]

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        # Use centralized settings manager
        if HAS_SETTINGS_MANAGER:
            self._settings_mgr = SettingsManager("settings", defaults=self.SETTINGS_DEFAULTS)
            self._settings = self._settings_mgr.all()
        else:
            # Fallback to legacy method
            self._settings = self._load_settings_legacy()

        self._build_ui()
        self._apply_settings()

    def _load_settings_legacy(self):
        """Legacy settings load for fallback"""
        import json
        settings_file = get_real_user_home() / ".config" / "meshforge" / "settings.json"
        defaults = self.SETTINGS_DEFAULTS.copy()
        try:
            if settings_file.exists():
                with open(settings_file) as f:
                    saved = json.load(f)
                    defaults.update(saved)
        except Exception as e:
            print(f"[Settings] Error loading settings: {e}")
        return defaults

    def _save_settings(self):
        """Save settings to file"""
        if HAS_SETTINGS_MANAGER:
            self._settings_mgr.update(self._settings)
            self._settings_mgr.save()
        else:
            # Legacy fallback
            import json
            settings_file = get_real_user_home() / ".config" / "meshforge" / "settings.json"
            try:
                settings_file.parent.mkdir(parents=True, exist_ok=True)
                with open(settings_file, 'w') as f:
                    json.dump(self._settings, f, indent=2)
            except Exception as e:
                print(f"[Settings] Error saving settings: {e}")

    def _build_ui(self):
        """Build the settings UI"""
        # Title
        title = Gtk.Label(label="Settings")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(label="Configure MeshForge appearance, behavior, and simulation")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Scrollable content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)

        # ══════════════════════════════════════════════════════════════
        # Appearance Section
        # ══════════════════════════════════════════════════════════════
        appearance_frame = Gtk.Frame()
        appearance_frame.set_label("Appearance")
        appearance_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        appearance_box.set_margin_start(15)
        appearance_box.set_margin_end(15)
        appearance_box.set_margin_top(10)
        appearance_box.set_margin_bottom(10)

        # Theme Dropdown (replaces simple toggle)
        theme_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        theme_label = Gtk.Label(label="Theme")
        theme_label.set_xalign(0)
        theme_label.set_hexpand(True)
        theme_row.append(theme_label)

        self.theme_dropdown = Gtk.DropDown()
        theme_model = Gtk.StringList()
        for _, display_name in self.THEME_OPTIONS:
            theme_model.append(display_name)
        self.theme_dropdown.set_model(theme_model)

        # Set current theme selection
        current_theme = self._settings.get("theme", "dark")
        for i, (value, _) in enumerate(self.THEME_OPTIONS):
            if value == current_theme:
                self.theme_dropdown.set_selected(i)
                break

        self.theme_dropdown.connect("notify::selected", self._on_theme_changed)
        theme_row.append(self.theme_dropdown)
        appearance_box.append(theme_row)

        # Dark Mode Toggle (legacy, kept for quick access)
        dark_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dark_label = Gtk.Label(label="Force Dark Mode")
        dark_label.set_xalign(0)
        dark_label.set_hexpand(True)
        dark_row.append(dark_label)

        self.dark_switch = Gtk.Switch()
        self.dark_switch.set_active(self._settings.get("dark_mode", True))
        self.dark_switch.connect("notify::active", self._on_dark_mode_changed)
        dark_row.append(self.dark_switch)
        appearance_box.append(dark_row)

        # Compact Mode Toggle
        compact_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        compact_label = Gtk.Label(label="Compact Mode")
        compact_label.set_xalign(0)
        compact_label.set_hexpand(True)
        compact_row.append(compact_label)

        self.compact_switch = Gtk.Switch()
        self.compact_switch.set_active(self._settings.get("compact_mode", False))
        self.compact_switch.connect("notify::active", self._on_compact_mode_changed)
        compact_row.append(self.compact_switch)
        appearance_box.append(compact_row)

        compact_desc = Gtk.Label(label="Reduce spacing for smaller screens (DevTerm, uConsole)")
        compact_desc.add_css_class("dim-label")
        compact_desc.set_xalign(0)
        appearance_box.append(compact_desc)

        appearance_frame.set_child(appearance_box)
        content.append(appearance_frame)

        # ══════════════════════════════════════════════════════════════
        # Simulation Section (NEW)
        # ══════════════════════════════════════════════════════════════
        sim_frame = Gtk.Frame()
        sim_frame.set_label("Simulation Mode")
        sim_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sim_box.set_margin_start(15)
        sim_box.set_margin_end(15)
        sim_box.set_margin_top(10)
        sim_box.set_margin_bottom(10)

        # Simulation mode info
        sim_info = Gtk.Label(label="Test RF and mesh features without physical hardware")
        sim_info.add_css_class("dim-label")
        sim_info.set_xalign(0)
        sim_box.append(sim_info)

        # Simulation Mode Dropdown
        sim_mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sim_mode_label = Gtk.Label(label="Mode")
        sim_mode_label.set_xalign(0)
        sim_mode_label.set_hexpand(True)
        sim_mode_row.append(sim_mode_label)

        self.sim_mode_dropdown = Gtk.DropDown()
        sim_model = Gtk.StringList()
        for _, display_name in self.SIMULATION_OPTIONS:
            sim_model.append(display_name)
        self.sim_mode_dropdown.set_model(sim_model)

        # Set current simulation mode
        current_sim = self._settings.get("simulation_mode", "disabled")
        for i, (value, _) in enumerate(self.SIMULATION_OPTIONS):
            if value == current_sim:
                self.sim_mode_dropdown.set_selected(i)
                break

        self.sim_mode_dropdown.connect("notify::selected", self._on_simulation_mode_changed)
        sim_mode_row.append(self.sim_mode_dropdown)
        sim_box.append(sim_mode_row)

        # Simulation Preset (Hawaii vs Generic)
        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        preset_label = Gtk.Label(label="Node Preset")
        preset_label.set_xalign(0)
        preset_label.set_hexpand(True)
        preset_row.append(preset_label)

        self.preset_dropdown = Gtk.DropDown()
        preset_model = Gtk.StringList()
        preset_model.append("Hawaii Islands (8 nodes)")
        preset_model.append("Generic Test (5 nodes)")
        self.preset_dropdown.set_model(preset_model)

        current_preset = self._settings.get("simulation_preset", "hawaii")
        self.preset_dropdown.set_selected(0 if current_preset == "hawaii" else 1)
        self.preset_dropdown.connect("notify::selected", self._on_preset_changed)
        preset_row.append(self.preset_dropdown)
        sim_box.append(preset_row)

        # Simulation status indicator
        self.sim_status_label = Gtk.Label()
        self.sim_status_label.set_xalign(0)
        self._update_simulation_status()
        sim_box.append(self.sim_status_label)

        sim_frame.set_child(sim_box)
        content.append(sim_frame)

        # Dashboard Section
        dashboard_frame = Gtk.Frame()
        dashboard_frame.set_label("Dashboard")
        dashboard_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        dashboard_box.set_margin_start(15)
        dashboard_box.set_margin_end(15)
        dashboard_box.set_margin_top(10)
        dashboard_box.set_margin_bottom(10)

        # Auto Refresh Toggle
        refresh_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        refresh_label = Gtk.Label(label="Auto Refresh")
        refresh_label.set_xalign(0)
        refresh_label.set_hexpand(True)
        refresh_row.append(refresh_label)

        self.refresh_switch = Gtk.Switch()
        self.refresh_switch.set_active(self._settings.get("auto_refresh", True))
        self.refresh_switch.connect("notify::active", self._on_auto_refresh_changed)
        refresh_row.append(self.refresh_switch)
        dashboard_box.append(refresh_row)

        # Refresh Interval
        interval_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        interval_label = Gtk.Label(label="Refresh Interval (seconds)")
        interval_label.set_xalign(0)
        interval_label.set_hexpand(True)
        interval_row.append(interval_label)

        self.interval_spin = Gtk.SpinButton()
        self.interval_spin.set_range(1, 60)
        self.interval_spin.set_value(self._settings.get("refresh_interval", 5))
        self.interval_spin.set_increments(1, 5)
        self.interval_spin.connect("value-changed", self._on_interval_changed)
        interval_row.append(self.interval_spin)
        dashboard_box.append(interval_row)

        # Show Node IDs
        nodeid_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        nodeid_label = Gtk.Label(label="Show Full Node IDs")
        nodeid_label.set_xalign(0)
        nodeid_label.set_hexpand(True)
        nodeid_row.append(nodeid_label)

        self.nodeid_switch = Gtk.Switch()
        self.nodeid_switch.set_active(self._settings.get("show_node_ids", True))
        self.nodeid_switch.connect("notify::active", self._on_nodeid_changed)
        nodeid_row.append(self.nodeid_switch)
        dashboard_box.append(nodeid_row)

        dashboard_frame.set_child(dashboard_box)
        content.append(dashboard_frame)

        # Integration Section
        integration_frame = Gtk.Frame()
        integration_frame.set_label("Integrations")
        integration_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        integration_box.set_margin_start(15)
        integration_box.set_margin_end(15)
        integration_box.set_margin_top(10)
        integration_box.set_margin_bottom(10)

        # RNS Integration placeholder
        rns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        rns_label = Gtk.Label(label="Reticulum Network Stack (RNS)")
        rns_label.set_xalign(0)
        rns_label.set_hexpand(True)
        rns_row.append(rns_label)

        rns_status = Gtk.Label(label="Coming Soon")
        rns_status.add_css_class("dim-label")
        rns_row.append(rns_status)
        integration_box.append(rns_row)

        # MQTT Integration
        mqtt_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mqtt_label = Gtk.Label(label="MQTT Bridge")
        mqtt_label.set_xalign(0)
        mqtt_label.set_hexpand(True)
        mqtt_row.append(mqtt_label)

        mqtt_status = Gtk.Label(label="Configure in Radio Config")
        mqtt_status.add_css_class("dim-label")
        mqtt_row.append(mqtt_status)
        integration_box.append(mqtt_row)

        integration_frame.set_child(integration_box)
        content.append(integration_frame)

        # About Section
        about_frame = Gtk.Frame()
        about_frame.set_label("About")
        about_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        about_box.set_margin_start(15)
        about_box.set_margin_end(15)
        about_box.set_margin_top(10)
        about_box.set_margin_bottom(10)

        # Import version info
        try:
            from __version__ import __version__, get_full_version
            version_text = get_full_version()
        except ImportError:
            version_text = "Unknown"

        version_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        version_label = Gtk.Label(label="Version")
        version_label.set_xalign(0)
        version_label.set_hexpand(True)
        version_row.append(version_label)

        version_value = Gtk.Label(label=version_text)
        version_row.append(version_value)
        about_box.append(version_row)

        # Settings file location
        settings_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        settings_label = Gtk.Label(label="Settings File")
        settings_label.set_xalign(0)
        settings_label.set_hexpand(True)
        settings_row.append(settings_label)

        settings_path = Gtk.Label(label=str(self.SETTINGS_FILE))
        settings_path.set_selectable(True)
        settings_path.add_css_class("dim-label")
        settings_row.append(settings_path)
        about_box.append(settings_row)

        about_frame.set_child(about_box)
        content.append(about_frame)

        scrolled.set_child(content)
        self.append(scrolled)

        # Reset button at bottom
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(10)

        reset_btn = Gtk.Button(label="Reset to Defaults")
        reset_btn.connect("clicked", self._on_reset_defaults)
        button_box.append(reset_btn)

        self.append(button_box)

    def _apply_settings(self):
        """Apply current settings to the application"""
        # Apply dark mode
        style_manager = Adw.StyleManager.get_default()
        if self._settings.get("dark_mode", True):
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def _on_dark_mode_changed(self, switch, _):
        """Handle dark mode toggle"""
        is_dark = switch.get_active()
        self._settings["dark_mode"] = is_dark
        self._save_settings()

        # Apply immediately
        style_manager = Adw.StyleManager.get_default()
        if is_dark:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

        self.main_window.set_status_message(f"Theme: {'Dark' if is_dark else 'Light'}")

    def _on_compact_mode_changed(self, switch, _):
        """Handle compact mode toggle"""
        self._settings["compact_mode"] = switch.get_active()
        self._save_settings()
        self.main_window.set_status_message("Compact mode " + ("enabled" if switch.get_active() else "disabled"))

    def _on_auto_refresh_changed(self, switch, _):
        """Handle auto refresh toggle"""
        self._settings["auto_refresh"] = switch.get_active()
        self._save_settings()

    def _on_interval_changed(self, spin):
        """Handle refresh interval change"""
        self._settings["refresh_interval"] = int(spin.get_value())
        self._save_settings()

    def _on_nodeid_changed(self, switch, _):
        """Handle node ID display toggle"""
        self._settings["show_node_ids"] = switch.get_active()
        self._save_settings()

    def _on_theme_changed(self, dropdown, _):
        """Handle theme dropdown change"""
        selected = dropdown.get_selected()
        if selected < len(self.THEME_OPTIONS):
            theme_value, theme_name = self.THEME_OPTIONS[selected]
            self._settings["theme"] = theme_value
            self._save_settings()

            # Apply theme
            style_manager = Adw.StyleManager.get_default()
            if theme_value == "system":
                style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
            elif theme_value == "dark":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
                self.dark_switch.set_active(True)
            else:  # light
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
                self.dark_switch.set_active(False)

            self.main_window.set_status_message(f"Theme: {theme_name}")

    def _on_simulation_mode_changed(self, dropdown, _):
        """Handle simulation mode change"""
        selected = dropdown.get_selected()
        if selected < len(self.SIMULATION_OPTIONS):
            mode_value, mode_name = self.SIMULATION_OPTIONS[selected]
            self._settings["simulation_mode"] = mode_value
            self._save_settings()

            # Apply simulation mode
            self._apply_simulation_mode(mode_value)
            self._update_simulation_status()
            self.main_window.set_status_message(f"Simulation: {mode_name}")

    def _on_preset_changed(self, dropdown, _):
        """Handle simulation preset change"""
        selected = dropdown.get_selected()
        preset = "hawaii" if selected == 0 else "generic"
        self._settings["simulation_preset"] = preset
        self._save_settings()

        # Update simulator preset if running
        try:
            from utils.simulator import get_mesh_simulator
            sim = get_mesh_simulator()
            if sim.is_enabled:
                sim.set_preset(use_hawaii=(preset == "hawaii"))
                self._update_simulation_status()
        except ImportError:
            pass

        self.main_window.set_status_message(f"Preset: {'Hawaii Islands' if preset == 'hawaii' else 'Generic Test'}")

    def _apply_simulation_mode(self, mode: str):
        """Apply simulation mode to the simulator"""
        try:
            from utils.simulator import get_mesh_simulator, SimulationMode

            sim = get_mesh_simulator()
            preset = self._settings.get("simulation_preset", "hawaii")
            sim.set_preset(use_hawaii=(preset == "hawaii"))

            if mode == "disabled":
                sim.disable()
            elif mode == "rf_only":
                sim.enable(SimulationMode.RF_ONLY)
            elif mode == "mesh_network":
                sim.enable(SimulationMode.MESH_NETWORK)
            elif mode == "full":
                sim.enable(SimulationMode.FULL)
        except ImportError as e:
            print(f"[Settings] Could not load simulator: {e}")

    def _update_simulation_status(self):
        """Update simulation status label"""
        try:
            from utils.simulator import get_mesh_simulator, is_simulation_enabled

            if is_simulation_enabled():
                sim = get_mesh_simulator()
                node_count = len(sim.get_nodes())
                self.sim_status_label.set_label(f"Status: Active ({node_count} simulated nodes)")
                self.sim_status_label.remove_css_class("dim-label")
                self.sim_status_label.add_css_class("success")
            else:
                self.sim_status_label.set_label("Status: Disabled (using real hardware)")
                self.sim_status_label.remove_css_class("success")
                self.sim_status_label.add_css_class("dim-label")
        except ImportError:
            self.sim_status_label.set_label("Status: Simulator not available")
            self.sim_status_label.add_css_class("dim-label")

    def _on_reset_defaults(self, button):
        """Reset all settings to defaults"""
        def do_reset(confirmed):
            if confirmed:
                self._settings = {
                    "theme": "dark",
                    "dark_mode": True,
                    "auto_refresh": True,
                    "refresh_interval": 5,
                    "show_node_ids": True,
                    "compact_mode": False,
                    "simulation_mode": "disabled",
                    "simulation_preset": "hawaii",
                }
                self._save_settings()
                self._apply_settings()

                # Update UI
                self.theme_dropdown.set_selected(1)  # Dark
                self.dark_switch.set_active(True)
                self.compact_switch.set_active(False)
                self.refresh_switch.set_active(True)
                self.interval_spin.set_value(5)
                self.nodeid_switch.set_active(True)
                self.sim_mode_dropdown.set_selected(0)  # Disabled
                self.preset_dropdown.set_selected(0)  # Hawaii

                # Disable simulation
                self._apply_simulation_mode("disabled")
                self._update_simulation_status()

                self.main_window.set_status_message("Settings reset to defaults")

        self.main_window.show_confirm_dialog(
            "Reset Settings?",
            "This will reset all settings to their default values.",
            do_reset
        )

    def get_setting(self, key, default=None):
        """Get a setting value"""
        return self._settings.get(key, default)
