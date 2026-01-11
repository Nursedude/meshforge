#!/usr/bin/env python3
"""
MeshForge VTE Launcher - GTK4 Terminal Wrapper

Embeds the TUI launcher in a GTK4 VTE terminal widget.
This provides:
- Proper taskbar icon (via GTK4 app_id)
- Window class support for desktop integration
- Native terminal experience with GTK4 decorations

Requirements:
- gir1.2-vte-2.91
- libvte-2.91-gtk4-0

Install: sudo apt install gir1.2-vte-2.91 libvte-2.91-gtk4-0
"""

import os
import sys
from pathlib import Path

# Setup gi before any imports
import gi

# VTE 2.91 is the GIR binding version - works with both GTK3 and GTK4
# The library (libvte-2.91-gtk4-0) provides GTK4 support
try:
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    gi.require_version('Vte', '2.91')  # VTE GIR version (same for GTK3/GTK4)
    GTK_VERSION = 4
except ValueError:
    try:
        gi.require_version('Gtk', '3.0')
        gi.require_version('Vte', '2.91')
        GTK_VERSION = 3
    except ValueError:
        print("Error: GTK and VTE libraries not found.")
        print("Install with: sudo apt install gir1.2-vte-2.91 libvte-2.91-gtk4-0")
        sys.exit(1)

if GTK_VERSION == 4:
    from gi.repository import Gtk, Adw, GLib, Gio, Gdk
    try:
        from gi.repository import Vte
        VTE_AVAILABLE = True
    except ImportError:
        VTE_AVAILABLE = False
else:
    from gi.repository import Gtk, GLib, Gio, Gdk
    try:
        from gi.repository import Vte
        VTE_AVAILABLE = True
    except ImportError:
        VTE_AVAILABLE = False


# Import version
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.5"


class MeshForgeVTEApp(Adw.Application if GTK_VERSION == 4 else Gtk.Application):
    """MeshForge VTE Terminal Application"""

    def __init__(self):
        app_id = 'org.meshforge.app'
        flags = Gio.ApplicationFlags.NON_UNIQUE

        if GTK_VERSION == 4:
            super().__init__(application_id=app_id, flags=flags)
        else:
            super().__init__(application_id=app_id, flags=flags)

        self.window = None
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        """Handle app activation"""
        if not self.window:
            self.window = MeshForgeVTEWindow(application=app)
        self.window.present()


class MeshForgeVTEWindow(Adw.ApplicationWindow if GTK_VERSION == 4 else Gtk.ApplicationWindow):
    """MeshForge VTE Terminal Window"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.set_title(f"MeshForge v{__version__}")
        self.set_default_size(900, 700)

        # Set window icon
        self._set_window_icon()

        # Build UI
        self._build_ui()

    def _set_window_icon(self):
        """Set window icon for taskbar"""
        try:
            # Find icon
            src_dir = Path(__file__).parent.parent
            icon_paths = [
                src_dir / 'assets' / 'meshforge-icon.svg',
                Path('/usr/share/icons/hicolor/scalable/apps/org.meshforge.app.svg'),
                Path('/usr/share/pixmaps/org.meshforge.app.svg'),
            ]

            for path in icon_paths:
                if path.exists():
                    if GTK_VERSION == 4:
                        self.set_icon_name("org.meshforge.app")
                    else:
                        self.set_icon_from_file(str(path))
                    break
        except Exception as e:
            print(f"Icon setup: {e}")

    def _build_ui(self):
        """Build the terminal UI"""
        if GTK_VERSION == 4:
            self._build_gtk4_ui()
        else:
            self._build_gtk3_ui()

    def _build_gtk4_ui(self):
        """Build GTK4 + libadwaita UI"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label=f"MeshForge v{__version__}"))
        main_box.append(header)

        # Terminal area
        if VTE_AVAILABLE:
            self.terminal = Vte.Terminal()
            self.terminal.set_vexpand(True)
            self.terminal.set_hexpand(True)

            # Configure terminal
            self.terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            self.terminal.set_mouse_autohide(True)
            self.terminal.set_scroll_on_output(True)
            self.terminal.set_scroll_on_keystroke(True)

            # Set dark theme colors
            self._apply_terminal_colors()

            # Scrolled container
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_vexpand(True)
            scrolled.set_child(self.terminal)
            main_box.append(scrolled)

            # Connect signals
            self.terminal.connect("child-exited", self._on_child_exited)

            # Spawn the TUI
            self._spawn_tui()
        else:
            # VTE not available - show error
            error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
            error_box.set_valign(Gtk.Align.CENTER)
            error_box.set_halign(Gtk.Align.CENTER)

            icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
            icon.set_pixel_size(64)
            error_box.append(icon)

            label = Gtk.Label(label="VTE Terminal Widget Not Available")
            label.add_css_class("title-1")
            error_box.append(label)

            hint = Gtk.Label(label="Install with: sudo apt install gir1.2-vte-2.91 libvte-2.91-gtk4-0")
            hint.add_css_class("dim-label")
            error_box.append(hint)

            # Fallback button
            fallback_btn = Gtk.Button(label="Launch External Terminal")
            fallback_btn.add_css_class("suggested-action")
            fallback_btn.connect("clicked", self._launch_external_terminal)
            error_box.append(fallback_btn)

            main_box.append(error_box)

    def _build_gtk3_ui(self):
        """Build GTK3 UI"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(main_box)

        if VTE_AVAILABLE:
            self.terminal = Vte.Terminal()
            self.terminal.set_vexpand(True)
            self.terminal.set_hexpand(True)

            # Configure terminal
            self.terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            self.terminal.set_mouse_autohide(True)
            self.terminal.set_scroll_on_output(True)
            self.terminal.set_scroll_on_keystroke(True)

            # Set colors
            self._apply_terminal_colors()

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_vexpand(True)
            scrolled.add(self.terminal)
            main_box.pack_start(scrolled, True, True, 0)

            # Connect signals
            self.terminal.connect("child-exited", self._on_child_exited)

            # Spawn the TUI
            self._spawn_tui()
        else:
            label = Gtk.Label(label="VTE not available. Install gir1.2-vte-2.91")
            main_box.pack_start(label, True, True, 0)

        main_box.show_all()

    def _apply_terminal_colors(self):
        """Apply terminal color scheme"""
        try:
            if GTK_VERSION == 4:
                # GTK4 uses RGBA
                bg = Gdk.RGBA()
                bg.parse("#1e1e2e")  # Dark background
                fg = Gdk.RGBA()
                fg.parse("#cdd6f4")  # Light foreground

                self.terminal.set_color_background(bg)
                self.terminal.set_color_foreground(fg)
            else:
                # GTK3 uses different API
                from gi.repository import Gdk as Gdk3
                bg = Gdk3.RGBA()
                bg.parse("#1e1e2e")
                fg = Gdk3.RGBA()
                fg.parse("#cdd6f4")

                self.terminal.set_color_background(bg)
                self.terminal.set_color_foreground(fg)
        except Exception as e:
            print(f"Color setup: {e}")

    def _spawn_tui(self):
        """Spawn the TUI launcher in the terminal"""
        # Find the TUI launcher
        src_dir = Path(__file__).parent
        tui_path = src_dir / 'launcher_tui.py'

        if not tui_path.exists():
            # Try alternative locations
            for alt_path in [
                Path('/opt/meshforge/src/launcher_tui.py'),
                Path(__file__).parent.parent / 'src' / 'launcher_tui.py',
            ]:
                if alt_path.exists():
                    tui_path = alt_path
                    break

        # Build command - run TUI with sudo
        argv = ['/usr/bin/sudo', '/usr/bin/python3', str(tui_path)]

        # Environment
        env = os.environ.copy()
        env['TERM'] = 'xterm-256color'
        env['COLORTERM'] = 'truecolor'

        try:
            if GTK_VERSION == 4:
                # GTK4/VTE async spawn
                self.terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    str(src_dir),  # Working directory
                    argv,
                    list(f"{k}={v}" for k, v in env.items()),
                    GLib.SpawnFlags.DEFAULT,
                    None,  # child_setup
                    None,  # child_setup_data
                    -1,    # timeout
                    None,  # cancellable
                    self._spawn_callback,  # callback
                    None   # user_data
                )
            else:
                # GTK3 sync spawn
                self.terminal.spawn_sync(
                    Vte.PtyFlags.DEFAULT,
                    str(src_dir),
                    argv,
                    list(f"{k}={v}" for k, v in env.items()),
                    GLib.SpawnFlags.DEFAULT,
                    None,
                    None
                )
        except Exception as e:
            print(f"Spawn error: {e}")
            # Try fallback spawn
            self._spawn_fallback()

    def _spawn_callback(self, terminal, pid, error, user_data):
        """Callback for async spawn"""
        if error:
            print(f"Spawn error: {error}")
            self._spawn_fallback()
        else:
            print(f"TUI started with PID: {pid}")

    def _spawn_fallback(self):
        """Fallback spawn method"""
        try:
            src_dir = Path(__file__).parent
            tui_path = src_dir / 'launcher_tui.py'

            # Use simpler spawn
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                None,
                ['/bin/bash', '-c', f'sudo python3 {tui_path}'],
                None,
                GLib.SpawnFlags.DEFAULT,
                None, None, -1, None, None, None
            )
        except Exception as e:
            print(f"Fallback spawn error: {e}")

    def _on_child_exited(self, terminal, status):
        """Handle TUI exit"""
        print(f"TUI exited with status: {status}")
        # Close the window when TUI exits
        self.close()

    def _launch_external_terminal(self, button):
        """Launch TUI in external terminal as fallback"""
        import subprocess
        import shutil

        src_dir = Path(__file__).parent
        tui_path = src_dir / 'launcher_tui.py'

        terminals = [
            ['gnome-terminal', '--', 'sudo', 'python3', str(tui_path)],
            ['xfce4-terminal', '-e', f'sudo python3 {tui_path}'],
            ['konsole', '-e', 'sudo', 'python3', str(tui_path)],
            ['xterm', '-e', f'sudo python3 {tui_path}'],
        ]

        for term_cmd in terminals:
            if shutil.which(term_cmd[0]):
                try:
                    subprocess.Popen(term_cmd)
                    self.close()
                    return
                except Exception:
                    continue

        print("No terminal emulator found")


def main():
    """Main entry point"""
    if not VTE_AVAILABLE:
        print("VTE library not available.")
        print("Install with: sudo apt install gir1.2-vte-2.91 libvte-2.91-gtk4-0")
        print("\nFalling back to external terminal...")

        # Fallback to external terminal
        import subprocess
        import shutil

        src_dir = Path(__file__).parent
        tui_path = src_dir / 'launcher_tui.py'

        if not tui_path.exists():
            # Try alternative paths
            for alt_path in [
                Path('/opt/meshforge/src/launcher_tui.py'),
                Path(__file__).parent.parent / 'src' / 'launcher_tui.py',
            ]:
                if alt_path.exists():
                    tui_path = alt_path
                    break

        if not tui_path.exists():
            print(f"Error: TUI launcher not found at {tui_path}")
            sys.exit(1)

        # Terminal launch configurations (terminal, args_format)
        terminals = [
            ('gnome-terminal', ['--', 'sudo', 'python3', str(tui_path)]),
            ('xfce4-terminal', ['-e', f'sudo python3 {tui_path}']),
            ('konsole', ['-e', 'sudo', 'python3', str(tui_path)]),
            ('xterm', ['-fa', 'Monospace', '-fs', '11', '-geometry', '100x35',
                      '-bg', '#1e1e2e', '-fg', '#cdd6f4',
                      '-e', f'sudo python3 {tui_path}']),
            ('lxterminal', ['-e', f'sudo python3 {tui_path}']),
            ('mate-terminal', ['-e', f'sudo python3 {tui_path}']),
            ('terminator', ['-e', f'sudo python3 {tui_path}']),
            ('tilix', ['-e', f'sudo python3 {tui_path}']),
            ('kitty', ['sudo', 'python3', str(tui_path)]),
            ('alacritty', ['-e', 'sudo', 'python3', str(tui_path)]),
        ]

        launched = False
        for term, args in terminals:
            term_path = shutil.which(term)
            if term_path:
                print(f"Launching with {term}...")
                try:
                    result = subprocess.run([term_path] + args)
                    launched = True
                    sys.exit(result.returncode)
                except Exception as e:
                    print(f"Failed to launch {term}: {e}")
                    continue

        if not launched:
            # Last resort: try x-terminal-emulator
            xterm = shutil.which('x-terminal-emulator')
            if xterm:
                print("Launching with x-terminal-emulator...")
                subprocess.run([xterm, '-e', f'sudo python3 {tui_path}'])
            else:
                print("\nNo terminal emulator found!")
                print("Please run directly: sudo python3 " + str(tui_path))
                sys.exit(1)
        return

    app = MeshForgeVTEApp()
    app.run(sys.argv)


if __name__ == '__main__':
    main()
