"""
Config File Manager Panel - Select YAML from available.d, edit with nano
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango
import subprocess
import threading
import shutil
from pathlib import Path


class ConfigPanel(Gtk.Box):
    """Configuration file manager panel"""

    CONFIG_BASE = Path("/etc/meshtasticd")
    AVAILABLE_D = CONFIG_BASE / "available.d"
    CONFIG_D = CONFIG_BASE / "config.d"
    MAIN_CONFIG = CONFIG_BASE / "config.yaml"

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()
        self._refresh_configs()

    def _build_ui(self):
        """Build the config panel UI"""
        # Title
        title = Gtk.Label(label="Config File Manager")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(
            label="Select configuration files from /etc/meshtasticd/available.d and activate them"
        )
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Paned layout - available on left, active on right
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_position(400)
        self.append(paned)

        # Left side - Available configs
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        available_frame = Gtk.Frame()
        available_frame.set_label("Available Configurations")
        available_frame.set_hexpand(True)

        available_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Filter dropdown
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        filter_box.set_margin_start(10)
        filter_box.set_margin_end(10)
        filter_box.set_margin_top(5)

        filter_box.append(Gtk.Label(label="Filter:"))
        self.filter_dropdown = Gtk.DropDown.new_from_strings([
            "All", "LoRa Modules", "Displays", "Presets", "Other"
        ])
        self.filter_dropdown.set_selected(0)
        self.filter_dropdown.connect("notify::selected", lambda *_: self._refresh_configs())
        filter_box.append(self.filter_dropdown)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        filter_box.append(spacer)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda b: self._refresh_configs())
        filter_box.append(refresh_btn)

        available_box.append(filter_box)

        # Config list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.available_list = Gtk.ListBox()
        self.available_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.available_list.connect("row-selected", self._on_config_selected)
        scrolled.set_child(self.available_list)

        available_box.append(scrolled)
        available_frame.set_child(available_box)
        left_box.append(available_frame)

        paned.set_start_child(left_box)

        # Right side - Active configs and actions
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # Main config status frame
        main_config_frame = Gtk.Frame()
        main_config_frame.set_label("Main Configuration")

        main_config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        main_config_box.set_margin_start(10)
        main_config_box.set_margin_end(10)
        main_config_box.set_margin_top(8)
        main_config_box.set_margin_bottom(8)

        self.main_config_status = Gtk.Label()
        self.main_config_status.set_xalign(0)
        self.main_config_status.set_wrap(True)
        main_config_box.append(self.main_config_status)

        main_config_frame.set_child(main_config_box)
        right_box.append(main_config_frame)

        # Active configs frame
        active_frame = Gtk.Frame()
        active_frame.set_label("Active Configurations (config.d)")

        active_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        self.active_status = Gtk.Label()
        self.active_status.set_xalign(0)
        self.active_status.set_margin_start(10)
        self.active_status.set_margin_top(5)
        self.active_status.add_css_class("dim-label")
        active_box.append(self.active_status)

        active_scrolled = Gtk.ScrolledWindow()
        active_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        active_scrolled.set_vexpand(True)

        self.active_list = Gtk.ListBox()
        self.active_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.active_list.connect("row-selected", self._on_active_config_selected)
        active_scrolled.set_child(self.active_list)

        active_box.append(active_scrolled)
        active_frame.set_child(active_box)
        right_box.append(active_frame)

        # Preview frame
        preview_frame = Gtk.Frame()
        preview_frame.set_label("Preview")

        preview_scrolled = Gtk.ScrolledWindow()
        preview_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        preview_scrolled.set_size_request(-1, 150)

        self.preview_text = Gtk.TextView()
        self.preview_text.set_editable(False)
        self.preview_text.set_monospace(True)
        self.preview_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        preview_scrolled.set_child(self.preview_text)

        preview_frame.set_child(preview_scrolled)
        right_box.append(preview_frame)

        paned.set_end_child(right_box)

        # Action buttons at bottom
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(10)

        # Activate button
        self.activate_btn = Gtk.Button(label="Activate Selected")
        self.activate_btn.add_css_class("suggested-action")
        self.activate_btn.connect("clicked", self._on_activate)
        self.activate_btn.set_sensitive(False)
        button_box.append(self.activate_btn)

        # Edit with nano button
        self.edit_btn = Gtk.Button(label="Edit with nano")
        self.edit_btn.connect("clicked", self._on_edit)
        self.edit_btn.set_sensitive(False)
        button_box.append(self.edit_btn)

        # Deactivate button
        self.deactivate_btn = Gtk.Button(label="Deactivate Selected")
        self.deactivate_btn.add_css_class("destructive-action")
        self.deactivate_btn.connect("clicked", self._on_deactivate)
        self.deactivate_btn.set_sensitive(False)
        button_box.append(self.deactivate_btn)

        # Apply changes button
        apply_btn = Gtk.Button(label="Apply Changes (Restart)")
        apply_btn.connect("clicked", self._on_apply)
        button_box.append(apply_btn)

        # Edit main config button
        main_config_btn = Gtk.Button(label="Edit config.yaml")
        main_config_btn.connect("clicked", self._on_edit_main_config)
        button_box.append(main_config_btn)

        self.append(button_box)

        # Track selected config
        self.selected_config_path = None

    def _refresh_configs(self):
        """Refresh available and active config lists"""
        # Clear lists
        while True:
            row = self.available_list.get_row_at_index(0)
            if row:
                self.available_list.remove(row)
            else:
                break

        while True:
            row = self.active_list.get_row_at_index(0)
            if row:
                self.active_list.remove(row)
            else:
                break

        # Update main config status
        self._update_main_config_status()

        # Get filter
        filter_map = {
            0: None,  # All
            1: "lora",
            2: "display",
            3: "preset",
            4: "other"
        }
        selected_filter = filter_map.get(self.filter_dropdown.get_selected())

        # Load available configs
        available_count = 0
        if self.AVAILABLE_D.exists():
            # Check both .yaml and .yml files
            configs = sorted(list(self.AVAILABLE_D.glob("*.yaml")) + list(self.AVAILABLE_D.glob("*.yml")))

            for config_path in configs:
                name = config_path.name

                # Apply filter
                if selected_filter:
                    if selected_filter == "lora" and not name.startswith("lora"):
                        continue
                    if selected_filter == "display" and not name.startswith("display"):
                        continue
                    if selected_filter == "preset" and not any(
                        x in name for x in ["mtnmesh", "emergency", "urban", "repeater"]
                    ):
                        continue
                    if selected_filter == "other":
                        if name.startswith("lora") or name.startswith("display"):
                            continue
                        if any(x in name for x in ["mtnmesh", "emergency", "urban", "repeater"]):
                            continue

                self._add_config_row(self.available_list, config_path, False)
                available_count += 1

        # Load active configs
        active_count = 0
        if self.CONFIG_D.exists():
            # Check both .yaml and .yml files
            active_configs = sorted(list(self.CONFIG_D.glob("*.yaml")) + list(self.CONFIG_D.glob("*.yml")))
            for config_path in active_configs:
                self._add_config_row(self.active_list, config_path, True)
                active_count += 1

        # Also check for main config.yaml
        if self.MAIN_CONFIG.exists():
            # Show main config in active list too
            self._add_config_row(self.active_list, self.MAIN_CONFIG, True)
            active_count += 1

        # Update active status
        if active_count == 0:
            if not self.CONFIG_D.exists():
                self.active_status.set_label("config.d/ not found. Install meshtasticd first.")
            else:
                self.active_status.set_label("No configs active. Select from available.d to activate.")
        else:
            self.active_status.set_label(f"{active_count} config(s) active")

    def _update_main_config_status(self):
        """Update the main config.yaml status display"""
        if not self.CONFIG_BASE.exists():
            self.main_config_status.set_label("meshtasticd not installed\n/etc/meshtasticd/ does not exist")
            return

        status_lines = []

        # Check main config.yaml
        if self.MAIN_CONFIG.exists():
            try:
                size = self.MAIN_CONFIG.stat().st_size
                status_lines.append(f"config.yaml: {size} bytes")

                # Try to read first few lines for module info
                content = self.MAIN_CONFIG.read_text()
                for line in content.split('\n')[:20]:
                    if 'Module:' in line or 'module:' in line:
                        status_lines.append(f"  {line.strip()}")
                        break
            except Exception as e:
                status_lines.append(f"config.yaml: Error reading - {e}")
        else:
            status_lines.append("config.yaml: Not found (click 'Edit config.yaml' to create)")

        # Check directories
        if self.AVAILABLE_D.exists():
            available_count = len(list(self.AVAILABLE_D.glob("*.yaml")))
            status_lines.append(f"available.d/: {available_count} templates")
        else:
            status_lines.append("available.d/: Not found")

        if self.CONFIG_D.exists():
            active_count = len(list(self.CONFIG_D.glob("*.yaml")))
            status_lines.append(f"config.d/: {active_count} active")
        else:
            status_lines.append("config.d/: Not found")

        self.main_config_status.set_label('\n'.join(status_lines))

    def _on_active_config_selected(self, listbox, row):
        """Handle active config selection"""
        if row:
            config_path = Path(row.get_name())
            self.deactivate_btn.set_sensitive(True)
            self._show_preview(config_path)
        else:
            self.deactivate_btn.set_sensitive(False)

    def _add_config_row(self, listbox, config_path, is_active):
        """Add a config row to a listbox"""
        row = Gtk.ListBoxRow()
        row.set_name(str(config_path))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(5)
        box.set_margin_bottom(5)

        # Icon based on type
        name = config_path.name
        if "lora" in name.lower():
            icon = "network-wireless-symbolic"
        elif "display" in name.lower():
            icon = "video-display-symbolic"
        else:
            icon = "document-properties-symbolic"

        img = Gtk.Image.new_from_icon_name(icon)
        box.append(img)

        # Config name
        label = Gtk.Label(label=name)
        label.set_xalign(0)
        label.set_hexpand(True)
        box.append(label)

        # Active indicator
        if is_active:
            active_label = Gtk.Label(label="Active")
            active_label.add_css_class("success")
            box.append(active_label)

        row.set_child(box)
        listbox.append(row)

    def _on_config_selected(self, listbox, row):
        """Handle config selection"""
        if row:
            self.selected_config_path = Path(row.get_name())
            self.activate_btn.set_sensitive(True)
            self.edit_btn.set_sensitive(True)
            self._show_preview(self.selected_config_path)
        else:
            self.selected_config_path = None
            self.activate_btn.set_sensitive(False)
            self.edit_btn.set_sensitive(False)
            self.preview_text.get_buffer().set_text("")

    def _show_preview(self, config_path):
        """Show preview of config file"""
        try:
            content = config_path.read_text()
            # Show first 50 lines
            lines = content.split('\n')[:50]
            preview = '\n'.join(lines)
            if len(content.split('\n')) > 50:
                preview += "\n\n... (truncated)"
            self.preview_text.get_buffer().set_text(preview)
        except Exception as e:
            self.preview_text.get_buffer().set_text(f"Error reading file: {e}")

    def _on_activate(self, button):
        """Activate selected config"""
        if not self.selected_config_path:
            return

        def do_activate():
            try:
                # Ensure config.d exists
                self.CONFIG_D.mkdir(parents=True, exist_ok=True)

                # Copy file to config.d
                dest = self.CONFIG_D / self.selected_config_path.name
                shutil.copy2(self.selected_config_path, dest)

                GLib.idle_add(self._refresh_configs)
                GLib.idle_add(
                    self.main_window.set_status_message,
                    f"Activated: {self.selected_config_path.name}"
                )

                # Ask if user wants to edit
                GLib.idle_add(self._ask_edit_after_activate, dest)

            except Exception as e:
                GLib.idle_add(
                    self.main_window.show_info_dialog,
                    "Error",
                    f"Failed to activate config: {e}"
                )

        thread = threading.Thread(target=do_activate)
        thread.daemon = True
        thread.start()

    def _ask_edit_after_activate(self, config_path):
        """Ask if user wants to edit the activated config"""
        self.main_window.show_confirm_dialog(
            "Edit Configuration?",
            f"Would you like to edit {config_path.name} in nano?",
            lambda confirmed: self._do_edit(config_path) if confirmed else None
        )

    def _on_edit(self, button):
        """Edit selected config with nano"""
        if not self.selected_config_path:
            return
        self._do_edit(self.selected_config_path)

    def _do_edit(self, config_path):
        """Open config in nano editor"""
        self.main_window.set_status_message(f"Opening {config_path.name} in nano...")

        def on_editor_closed():
            self.main_window.set_status_message("Editor closed")
            self._refresh_configs()
            if self.selected_config_path:
                self._show_preview(self.selected_config_path)

        self.main_window.open_terminal_editor(config_path, on_editor_closed)

    def _on_edit_main_config(self, button):
        """Edit main config.yaml"""
        if not self.MAIN_CONFIG.exists():
            self.main_window.show_confirm_dialog(
                "Create config.yaml?",
                "config.yaml doesn't exist. Create a basic one?",
                self._create_main_config
            )
        else:
            self._do_edit(self.MAIN_CONFIG)

    def _create_main_config(self, confirmed):
        """Create basic config.yaml"""
        if not confirmed:
            return

        try:
            basic_config = """# Meshtasticd Configuration
# See available.d for hardware-specific configurations

Lora:
  Module: sx1262  # Change to match your hardware
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18

Logging:
  LogLevel: info

Webserver:
  Port: 443
"""
            self.MAIN_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            self.MAIN_CONFIG.write_text(basic_config)
            self._do_edit(self.MAIN_CONFIG)
        except Exception as e:
            self.main_window.show_info_dialog("Error", f"Failed to create config: {e}")

    def _on_deactivate(self, button):
        """Deactivate selected active config"""
        row = self.active_list.get_selected_row()
        if not row:
            return

        config_path = Path(row.get_name())

        def do_deactivate(confirmed):
            if not confirmed:
                return

            try:
                config_path.unlink()
                GLib.idle_add(self._refresh_configs)
                GLib.idle_add(
                    self.main_window.set_status_message,
                    f"Deactivated: {config_path.name}"
                )
            except Exception as e:
                GLib.idle_add(
                    self.main_window.show_info_dialog,
                    "Error",
                    f"Failed to deactivate: {e}"
                )

        self.main_window.show_confirm_dialog(
            "Deactivate Configuration?",
            f"Remove {config_path.name} from active configurations?",
            do_deactivate
        )

    def _on_apply(self, button):
        """Apply changes by reloading and restarting service"""
        def do_apply():
            try:
                # Daemon reload
                subprocess.run(['systemctl', 'daemon-reload'], check=True)

                # Restart service
                subprocess.run(['systemctl', 'restart', 'meshtasticd'], check=True)

                GLib.idle_add(
                    self.main_window.set_status_message,
                    "Configuration applied - service restarted"
                )
                GLib.idle_add(
                    self.main_window.show_info_dialog,
                    "Success",
                    "Configuration changes applied.\nService has been restarted."
                )
            except Exception as e:
                GLib.idle_add(
                    self.main_window.show_info_dialog,
                    "Error",
                    f"Failed to apply changes: {e}"
                )

        self.main_window.show_confirm_dialog(
            "Apply Changes?",
            "This will reload the configuration and restart the meshtasticd service.",
            lambda confirmed: threading.Thread(target=do_apply, daemon=True).start() if confirmed else None
        )
