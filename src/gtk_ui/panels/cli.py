"""
Meshtastic CLI Panel - Run meshtastic CLI commands
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import os
import subprocess
import threading
import shutil


def _find_meshtastic_cli():
    """Find the meshtastic CLI executable - uses centralized utils.cli"""
    try:
        from utils.cli import find_meshtastic_cli
        return find_meshtastic_cli()
    except ImportError:
        # Fallback if utils not available
        return shutil.which('meshtastic')


class CLIPanel(Gtk.Box):
    """Meshtastic CLI commands panel"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()
        self._check_cli_available()

    def _build_ui(self):
        """Build the CLI panel UI"""
        # Title
        title = Gtk.Label(label="Meshtastic CLI Commands")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        # CLI status
        self.cli_status = Gtk.Label(label="Checking CLI availability...")
        self.cli_status.set_xalign(0)
        self.append(self.cli_status)

        # Connection settings
        conn_frame = Gtk.Frame()
        conn_frame.set_label("Connection")

        conn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        conn_box.set_margin_start(10)
        conn_box.set_margin_end(10)
        conn_box.set_margin_top(10)
        conn_box.set_margin_bottom(10)

        conn_box.append(Gtk.Label(label="Connection:"))

        self.conn_type = Gtk.DropDown.new_from_strings([
            "Localhost (127.0.0.1)",
            "Serial (/dev/ttyUSB0)",
            "Serial (/dev/ttyACM0)",
            "BLE"
        ])
        self.conn_type.set_selected(0)
        conn_box.append(self.conn_type)

        conn_frame.set_child(conn_box)
        self.append(conn_frame)

        # Quick commands
        quick_frame = Gtk.Frame()
        quick_frame.set_label("Quick Commands")

        quick_grid = Gtk.Grid()
        quick_grid.set_row_spacing(5)
        quick_grid.set_column_spacing(10)
        quick_grid.set_margin_start(10)
        quick_grid.set_margin_end(10)
        quick_grid.set_margin_top(10)
        quick_grid.set_margin_bottom(10)

        quick_commands = [
            ("Node Info", "--info"),
            ("List Nodes", "--nodes"),
            ("Get All Settings", "--get all"),
            ("Device Metadata", "--metadata"),
            ("Channel Info", "--ch-index 0 --info"),
            ("Get Position", "--get position"),
        ]

        for i, (label, cmd) in enumerate(quick_commands):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda b, c=cmd: self._run_command(c))
            quick_grid.attach(btn, i % 3, i // 3, 1, 1)

        quick_frame.set_child(quick_grid)
        self.append(quick_frame)

        # Action commands
        action_frame = Gtk.Frame()
        action_frame.set_label("Actions")

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        action_box.set_margin_start(10)
        action_box.set_margin_end(10)
        action_box.set_margin_top(10)
        action_box.set_margin_bottom(10)
        action_box.set_halign(Gtk.Align.CENTER)

        actions = [
            ("Reboot Node", "--reboot", False),
            ("Shutdown Node", "--shutdown", True),
            ("Factory Reset", "--factory-reset", True),
            ("Reset NodeDB", "--reset-nodedb", True),
        ]

        for label, cmd, dangerous in actions:
            btn = Gtk.Button(label=label)
            if dangerous:
                btn.add_css_class("destructive-action")
            btn.connect("clicked", lambda b, c=cmd, d=dangerous: self._run_action(c, d))
            action_box.append(btn)

        action_frame.set_child(action_box)
        self.append(action_frame)

        # Custom command
        custom_frame = Gtk.Frame()
        custom_frame.set_label("Custom Command")

        custom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        custom_box.set_margin_start(10)
        custom_box.set_margin_end(10)
        custom_box.set_margin_top(10)
        custom_box.set_margin_bottom(10)

        custom_box.append(Gtk.Label(label="meshtastic"))

        self.custom_entry = Gtk.Entry()
        self.custom_entry.set_placeholder_text("--info")
        self.custom_entry.set_hexpand(True)
        self.custom_entry.connect("activate", lambda e: self._run_custom())
        custom_box.append(self.custom_entry)

        run_btn = Gtk.Button(label="Run")
        run_btn.add_css_class("suggested-action")
        run_btn.connect("clicked", lambda b: self._run_custom())
        custom_box.append(run_btn)

        help_btn = Gtk.Button(label="--help")
        help_btn.connect("clicked", lambda b: self._run_command("--help"))
        custom_box.append(help_btn)

        custom_frame.set_child(custom_box)
        self.append(custom_frame)

        # Output area
        output_frame = Gtk.Frame()
        output_frame.set_label("Output")
        output_frame.set_vexpand(True)

        output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Output toolbar
        output_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        output_toolbar.set_margin_start(10)
        output_toolbar.set_margin_end(10)
        output_toolbar.set_margin_top(5)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", lambda b: self.output_text.get_buffer().set_text(""))
        output_toolbar.append(clear_btn)

        self.spinner = Gtk.Spinner()
        output_toolbar.append(self.spinner)

        output_box.append(output_toolbar)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.output_text = Gtk.TextView()
        self.output_text.set_editable(False)
        self.output_text.set_monospace(True)
        self.output_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled.set_child(self.output_text)

        output_box.append(scrolled)
        output_frame.set_child(output_box)
        self.append(output_frame)

    def _check_cli_available(self):
        """Check if meshtastic CLI is available"""
        def check():
            cli_path = _find_meshtastic_cli()
            GLib.idle_add(self._update_cli_status, cli_path is not None, cli_path)

        thread = threading.Thread(target=check)
        thread.daemon = True
        thread.start()

    def _update_cli_status(self, available, cli_path=None):
        """Update CLI status label"""
        self._cli_path = cli_path
        if available:
            self.cli_status.set_label(f"CLI Status: Available ({cli_path})")
            self.cli_status.remove_css_class("error")
            self.cli_status.add_css_class("success")
        else:
            self.cli_status.set_label(
                "CLI Status: Not found. Install with: sudo apt install pipx && pipx install 'meshtastic[cli]'"
            )
            self.cli_status.remove_css_class("success")
            self.cli_status.add_css_class("error")
        return False

    def _get_connection_args(self):
        """Get connection arguments based on selection"""
        conn_map = {
            0: ["--host", "127.0.0.1"],
            1: ["--port", "/dev/ttyUSB0"],
            2: ["--port", "/dev/ttyACM0"],
            3: ["--ble"],
        }
        return conn_map.get(self.conn_type.get_selected(), ["--host", "127.0.0.1"])

    def _run_command(self, args_str):
        """Run a meshtastic command"""
        args = args_str.split()
        self._execute_command(args)

    def _run_custom(self):
        """Run custom command from entry"""
        args_str = self.custom_entry.get_text().strip()
        if args_str:
            args = args_str.split()
            self._execute_command(args)

    def _run_action(self, args_str, dangerous):
        """Run an action command with confirmation if dangerous"""
        if dangerous:
            self.main_window.show_confirm_dialog(
                "Confirm Action",
                f"This action ({args_str}) may be destructive. Continue?",
                lambda confirmed: self._run_command(args_str) if confirmed else None
            )
        else:
            self._run_command(args_str)

    def _execute_command(self, args):
        """Execute meshtastic command"""
        self.spinner.start()
        self.main_window.set_status_message("Running command...")

        def run():
            try:
                # Find CLI path
                cli_path = getattr(self, '_cli_path', None) or _find_meshtastic_cli()
                if not cli_path:
                    GLib.idle_add(
                        self._command_complete,
                        "meshtastic CLI not found. Install with:\nsudo apt install pipx && pipx install 'meshtastic[cli]'",
                        False
                    )
                    return

                # Build command
                cmd = [cli_path] + self._get_connection_args() + args

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                output = f"$ {' '.join(cmd)}\n\n"
                if result.stdout:
                    output += result.stdout
                if result.stderr:
                    output += f"\n\nSTDERR:\n{result.stderr}"

                GLib.idle_add(self._command_complete, output, result.returncode == 0)

            except subprocess.TimeoutExpired:
                GLib.idle_add(self._command_complete, "Command timed out after 60 seconds", False)
            except FileNotFoundError:
                GLib.idle_add(
                    self._command_complete,
                    "meshtastic CLI not found. Install with:\nsudo apt install pipx && pipx install 'meshtastic[cli]'",
                    False
                )
            except Exception as e:
                GLib.idle_add(self._command_complete, f"Error: {e}", False)

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()

    def _command_complete(self, output, success):
        """Handle command completion"""
        self.spinner.stop()

        buffer = self.output_text.get_buffer()
        existing = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

        # Append new output
        new_text = existing + "\n" + "=" * 60 + "\n" + output + "\n"
        buffer.set_text(new_text)

        # Scroll to end
        self.output_text.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 0)

        if success:
            self.main_window.set_status_message("Command completed successfully")
        else:
            self.main_window.set_status_message("Command failed")

        return False
