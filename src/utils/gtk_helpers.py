"""
GTK Helper Utilities for MeshForge

Provides common UI patterns to reduce code redundancy across panels.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

import logging
from typing import Optional, Tuple, Callable, List
import shutil
import subprocess
import shlex

logger = logging.getLogger(__name__)


# ============================================================================
# Box/Container Configuration
# ============================================================================

def configure_margins(widget: Gtk.Widget,
                      start: int = 15,
                      end: int = 15,
                      top: int = 10,
                      bottom: int = 10) -> Gtk.Widget:
    """
    Apply standard margins to a GTK widget.

    Args:
        widget: The GTK widget to configure
        start: Left margin (default 15)
        end: Right margin (default 15)
        top: Top margin (default 10)
        bottom: Bottom margin (default 10)

    Returns:
        The configured widget (for chaining)
    """
    widget.set_margin_start(start)
    widget.set_margin_end(end)
    widget.set_margin_top(top)
    widget.set_margin_bottom(bottom)
    return widget


def create_content_box(orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL,
                       spacing: int = 10,
                       margins: bool = True) -> Gtk.Box:
    """
    Create a standard content box with optional margins.

    Args:
        orientation: Box orientation (default VERTICAL)
        spacing: Space between children (default 10)
        margins: Whether to apply standard margins (default True)

    Returns:
        Configured Gtk.Box
    """
    box = Gtk.Box(orientation=orientation, spacing=spacing)
    if margins:
        configure_margins(box)
    return box


def create_section_frame(title: str,
                         description: str = "",
                         spacing: int = 8) -> Tuple[Adw.PreferencesGroup, None]:
    """
    Create a styled section frame using Adw.PreferencesGroup.

    Args:
        title: Section title
        description: Optional description text
        spacing: Space between children

    Returns:
        Tuple of (PreferencesGroup, None) - None for compatibility with old pattern
    """
    group = Adw.PreferencesGroup()
    group.set_title(title)
    if description:
        group.set_description(description)
    return group, None


# ============================================================================
# Terminal Launching (Secure)
# ============================================================================

def launch_terminal_command(command: str,
                            description: str = "",
                            log_func: Optional[Callable[[str], None]] = None) -> bool:
    """
    Launch a command in a terminal emulator securely.

    Security: Uses argument lists instead of shell=True to prevent command injection.

    Args:
        command: The command to run in the terminal
        description: Optional description for logging
        log_func: Optional function to call with log messages

    Returns:
        True if terminal launched successfully, False otherwise
    """
    # Terminal configs: (binary, args_before_command, split_command)
    terminals = [
        ('lxterminal', ['-e'], False),      # lxterminal -e "command"
        ('xfce4-terminal', ['-e'], False),  # xfce4-terminal -e "command"
        ('gnome-terminal', ['--'], True),   # gnome-terminal -- command args
        ('konsole', ['-e'], True),          # konsole -e command args
        ('xterm', ['-e'], True),            # xterm -e command args
    ]

    for term_name, term_args, split_command in terminals:
        term_path = shutil.which(term_name)
        if term_path:
            try:
                if split_command:
                    cmd_parts = shlex.split(command)
                    full_cmd = [term_path] + term_args + cmd_parts
                else:
                    full_cmd = [term_path] + term_args + [command]

                subprocess.Popen(full_cmd, start_new_session=True)

                if description and log_func:
                    log_func(f"{description} in {term_name}")

                return True
            except Exception as e:
                logger.debug(f"Failed to launch {term_name}: {e}")
                continue

    if log_func:
        log_func("No terminal emulator found")
    return False


def launch_editor(file_path: str,
                  editor: str = "nano",
                  as_user: Optional[str] = None,
                  log_func: Optional[Callable[[str], None]] = None) -> bool:
    """
    Launch a file editor in a terminal.

    Args:
        file_path: Path to the file to edit
        editor: Editor command (default "nano")
        as_user: If set, run editor as this user via sudo
        log_func: Optional function for logging

    Returns:
        True if launched successfully
    """
    if as_user:
        command = f"sudo -i -u {shlex.quote(as_user)} {editor} {shlex.quote(file_path)}"
    else:
        command = f"{editor} {shlex.quote(file_path)}"

    return launch_terminal_command(
        command,
        f"Editing {file_path}",
        log_func
    )


# ============================================================================
# Timer Management
# ============================================================================

class TimerManager:
    """
    Manages GLib timers with proper cleanup.

    Usage:
        timer_mgr = TimerManager()
        timer_mgr.add_timer("status", 5000, self._update_status)
        timer_mgr.add_timer("refresh", 30000, self._refresh)
        # On cleanup:
        timer_mgr.cleanup()
    """

    def __init__(self):
        self._timers: dict = {}

    def add_timer(self, name: str, interval_ms: int,
                  callback: Callable[[], bool],
                  priority: int = GLib.PRIORITY_DEFAULT) -> int:
        """
        Add a timer with a name for later reference.

        Args:
            name: Unique name for this timer
            interval_ms: Interval in milliseconds
            callback: Function to call (must return True to continue)
            priority: GLib priority level

        Returns:
            Timer source ID
        """
        # Remove existing timer with same name
        if name in self._timers:
            self.remove_timer(name)

        source_id = GLib.timeout_add(interval_ms, callback, priority=priority)
        self._timers[name] = source_id
        return source_id

    def add_seconds_timer(self, name: str, interval_sec: int,
                          callback: Callable[[], bool]) -> int:
        """Add a timer with seconds interval"""
        if name in self._timers:
            self.remove_timer(name)

        source_id = GLib.timeout_add_seconds(interval_sec, callback)
        self._timers[name] = source_id
        return source_id

    def remove_timer(self, name: str) -> bool:
        """
        Remove a timer by name.

        Args:
            name: Timer name to remove

        Returns:
            True if timer was found and removed
        """
        if name in self._timers:
            GLib.source_remove(self._timers[name])
            del self._timers[name]
            return True
        return False

    def cleanup(self):
        """Remove all managed timers"""
        for name in list(self._timers.keys()):
            try:
                GLib.source_remove(self._timers[name])
            except Exception as e:
                logger.debug(f"Error removing timer {name}: {e}")
        self._timers.clear()
        logger.debug(f"Timer manager cleaned up")

    @property
    def active_timers(self) -> List[str]:
        """Get list of active timer names"""
        return list(self._timers.keys())


# ============================================================================
# Panel Base Class
# ============================================================================

class MeshForgePanel(Gtk.Box):
    """
    Base class for MeshForge panels with common functionality.

    Provides:
    - Timer management with automatic cleanup
    - Standard UI building patterns
    - Logging integration
    """

    def __init__(self, parent_window=None, **kwargs):
        orientation = kwargs.pop('orientation', Gtk.Orientation.VERTICAL)
        spacing = kwargs.pop('spacing', 0)
        super().__init__(orientation=orientation, spacing=spacing, **kwargs)

        self.parent_window = parent_window
        self._timer_manager = TimerManager()

        # Connect cleanup to destruction
        self.connect("unrealize", self._on_unrealize)

    def _on_unrealize(self, widget):
        """Handle widget destruction - cleanup resources"""
        self.cleanup()

    def cleanup(self):
        """
        Clean up panel resources.

        Override this in subclasses, calling super().cleanup()
        """
        self._timer_manager.cleanup()

    def add_timer(self, name: str, interval_ms: int,
                  callback: Callable[[], bool]) -> int:
        """Add a managed timer"""
        return self._timer_manager.add_timer(name, interval_ms, callback)

    def add_seconds_timer(self, name: str, interval_sec: int,
                          callback: Callable[[], bool]) -> int:
        """Add a managed timer with seconds interval"""
        return self._timer_manager.add_seconds_timer(name, interval_sec, callback)

    def remove_timer(self, name: str) -> bool:
        """Remove a managed timer"""
        return self._timer_manager.remove_timer(name)

    def set_status(self, message: str):
        """Set status bar message if parent window supports it"""
        if self.parent_window and hasattr(self.parent_window, 'set_status_message'):
            self.parent_window.set_status_message(message)

    def create_scrolled_content(self) -> Tuple[Gtk.ScrolledWindow, Gtk.Box]:
        """
        Create a scrolled window with content box.

        Returns:
            Tuple of (ScrolledWindow, content Box)
        """
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = create_content_box()
        scroll.set_child(content)

        return scroll, content
