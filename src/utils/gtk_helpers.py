"""
GTK Helper Utilities for MeshForge

Provides common UI patterns to reduce code redundancy across panels.
Establishes human interface standards for consistent UX across the application.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango

import logging
import threading
import os
from typing import Optional, Tuple, Callable, List
import shutil
import subprocess
import shlex

logger = logging.getLogger(__name__)


# ============================================================================
# UI Standards - Human Interface Constants
# ============================================================================

class UIStandards:
    """
    Human interface standards for consistent UX across MeshForge.

    Usage:
        from utils.gtk_helpers import UI
        box.set_margin_start(UI.MARGIN_PANEL)
    """

    # Spacing constants (in pixels)
    MARGIN_PANEL = 20       # Outer panel margin
    MARGIN_SECTION = 15     # Section/frame margin
    MARGIN_INNER = 10       # Inner content margin
    MARGIN_COMPACT = 5      # Compact spacing

    SPACING_PANEL = 15      # Between major panel sections
    SPACING_SECTION = 10    # Between items in a section
    SPACING_COMPACT = 5     # Compact spacing

    # Log viewer defaults
    LOG_MIN_HEIGHT = 150    # Minimum log viewer height
    LOG_DEFAULT_HEIGHT = 200  # Default log viewer height
    LOG_MAX_LINES = 500     # Maximum lines to keep in buffer

    # CSS class names (standardized)
    CSS_TITLE_MAIN = "title-1"
    CSS_TITLE_SECTION = "title-2"
    CSS_TITLE_SUB = "title-3"
    CSS_HEADING = "heading"
    CSS_DIM = "dim-label"
    CSS_MONOSPACE = "monospace"
    CSS_SUCCESS = "success"
    CSS_WARNING = "warning"
    CSS_ERROR = "error"
    CSS_CARD = "card"
    CSS_SUGGESTED = "suggested-action"
    CSS_DESTRUCTIVE = "destructive-action"

    # Icon names (standardized)
    ICON_SUCCESS = "emblem-default-symbolic"
    ICON_WARNING = "dialog-warning-symbolic"
    ICON_ERROR = "dialog-error-symbolic"
    ICON_INFO = "dialog-information-symbolic"
    ICON_QUESTION = "emblem-question-symbolic"
    ICON_REFRESH = "view-refresh-symbolic"
    ICON_SETTINGS = "preferences-system-symbolic"
    ICON_TERMINAL = "utilities-terminal-symbolic"
    ICON_FOLDER = "folder-symbolic"
    ICON_RUNNING = "media-playback-start-symbolic"
    ICON_STOPPED = "media-playback-stop-symbolic"


# Convenience alias
UI = UIStandards


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


# ============================================================================
# Standard Panel Header
# ============================================================================

def create_panel_header(title: str,
                        subtitle: str = "",
                        icon_name: Optional[str] = None) -> Gtk.Box:
    """
    Create a standard panel header with title, subtitle, and optional icon.

    Args:
        title: Main panel title
        subtitle: Optional description
        icon_name: Optional icon name

    Returns:
        Configured header box
    """
    header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=UI.SPACING_COMPACT)

    # Title row
    title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=UI.SPACING_SECTION)

    if icon_name:
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(32)
        title_row.append(icon)

    title_label = Gtk.Label(label=title)
    title_label.add_css_class(UI.CSS_TITLE_MAIN)
    title_label.set_xalign(0)
    title_row.append(title_label)

    header.append(title_row)

    if subtitle:
        sub_label = Gtk.Label(label=subtitle)
        sub_label.add_css_class(UI.CSS_DIM)
        sub_label.set_xalign(0)
        sub_label.set_wrap(True)
        header.append(sub_label)

    return header


def create_section_header(title: str, description: str = "") -> Gtk.Box:
    """
    Create a section header within a panel.

    Args:
        title: Section title
        description: Optional description

    Returns:
        Configured section header box
    """
    header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

    title_label = Gtk.Label(label=title)
    title_label.add_css_class(UI.CSS_TITLE_SECTION)
    title_label.set_xalign(0)
    header.append(title_label)

    if description:
        desc_label = Gtk.Label(label=description)
        desc_label.add_css_class(UI.CSS_DIM)
        desc_label.set_xalign(0)
        desc_label.set_wrap(True)
        header.append(desc_label)

    return header


# ============================================================================
# Resizable Log Viewer Component
# ============================================================================

class ResizableLogViewer(Gtk.Box):
    """
    A resizable log viewer component with controls.

    Features:
    - Resizable via drag handle
    - Auto-scroll toggle
    - Refresh and clear buttons
    - Line count limiting
    - Monospace text display

    Usage:
        log_viewer = ResizableLogViewer(title="Output Log")
        log_viewer.append_text("Log message here")
        log_viewer.set_text("Replace all content")
    """

    def __init__(self,
                 title: str = "Log Output",
                 min_height: int = UI.LOG_MIN_HEIGHT,
                 default_height: int = UI.LOG_DEFAULT_HEIGHT,
                 show_controls: bool = True,
                 auto_scroll: bool = True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._auto_scroll = auto_scroll
        self._max_lines = UI.LOG_MAX_LINES
        self._refresh_callback = None

        # Build UI
        self._build_ui(title, min_height, default_height, show_controls)

    def _build_ui(self, title: str, min_height: int, default_height: int, show_controls: bool):
        """Build the log viewer UI"""
        # Header with title and controls
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=UI.SPACING_SECTION)
        header.set_margin_start(UI.MARGIN_COMPACT)
        header.set_margin_end(UI.MARGIN_COMPACT)
        header.set_margin_top(UI.MARGIN_COMPACT)
        header.set_margin_bottom(UI.MARGIN_COMPACT)

        # Title
        title_label = Gtk.Label(label=title)
        title_label.add_css_class(UI.CSS_HEADING)
        title_label.set_xalign(0)
        header.append(title_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        if show_controls:
            # Auto-scroll toggle
            self._auto_scroll_check = Gtk.CheckButton(label="Auto-scroll")
            self._auto_scroll_check.set_active(self._auto_scroll)
            self._auto_scroll_check.connect("toggled", self._on_auto_scroll_toggled)
            header.append(self._auto_scroll_check)

            # Refresh button
            refresh_btn = Gtk.Button()
            refresh_btn.set_icon_name(UI.ICON_REFRESH)
            refresh_btn.set_tooltip_text("Refresh")
            refresh_btn.connect("clicked", self._on_refresh)
            header.append(refresh_btn)

            # Clear button
            clear_btn = Gtk.Button(label="Clear")
            clear_btn.connect("clicked", self._on_clear)
            header.append(clear_btn)

        self.append(header)

        # Separator
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Text view in scrolled window
        self._text_view = Gtk.TextView()
        self._text_view.set_editable(False)
        self._text_view.set_monospace(True)
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text_view.set_cursor_visible(False)

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_min_content_height(min_height)
        self._scroll.set_vexpand(True)
        self._scroll.set_child(self._text_view)

        # Resize handle frame
        resize_frame = Gtk.Frame()
        resize_frame.set_child(self._scroll)
        resize_frame.set_vexpand(True)

        self.append(resize_frame)

    def set_text(self, text: str):
        """Set the log text, replacing existing content"""
        buffer = self._text_view.get_buffer()
        buffer.set_text(text)
        self._trim_buffer()
        if self._auto_scroll:
            self._scroll_to_bottom()

    def append_text(self, text: str):
        """Append text to the log"""
        buffer = self._text_view.get_buffer()
        end_iter = buffer.get_end_iter()
        buffer.insert(end_iter, text + "\n")
        self._trim_buffer()
        if self._auto_scroll:
            self._scroll_to_bottom()

    def clear(self):
        """Clear all log content"""
        buffer = self._text_view.get_buffer()
        buffer.set_text("")

    def get_text(self) -> str:
        """Get all log text"""
        buffer = self._text_view.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False)

    def set_refresh_callback(self, callback: Callable):
        """Set callback for refresh button"""
        self._refresh_callback = callback

    def _trim_buffer(self):
        """Trim buffer to max lines"""
        buffer = self._text_view.get_buffer()
        line_count = buffer.get_line_count()

        if line_count > self._max_lines:
            # Remove oldest lines
            lines_to_remove = line_count - self._max_lines
            start = buffer.get_start_iter()
            end = buffer.get_iter_at_line(lines_to_remove)
            buffer.delete(start, end)

    def _scroll_to_bottom(self):
        """Scroll to bottom of log"""
        def scroll():
            buffer = self._text_view.get_buffer()
            end_iter = buffer.get_end_iter()
            self._text_view.scroll_to_iter(end_iter, 0, False, 0, 0)
            return False
        GLib.idle_add(scroll)

    def _on_auto_scroll_toggled(self, button):
        """Handle auto-scroll toggle"""
        self._auto_scroll = button.get_active()

    def _on_refresh(self, button):
        """Handle refresh button"""
        if self._refresh_callback:
            self._refresh_callback()

    def _on_clear(self, button):
        """Handle clear button"""
        self.clear()


# ============================================================================
# Resizable Paned Layout
# ============================================================================

class ResizablePanedLayout(Gtk.Paned):
    """
    A resizable two-pane layout for main content + log/output.

    Usage:
        layout = ResizablePanedLayout()
        layout.set_main_content(main_widget)
        layout.set_bottom_content(log_viewer)
    """

    def __init__(self,
                 orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL,
                 main_min_size: int = 200,
                 bottom_min_size: int = 100):
        super().__init__(orientation=orientation)

        self._main_min_size = main_min_size
        self._bottom_min_size = bottom_min_size

        self.set_wide_handle(True)
        self.set_shrink_start_child(False)
        self.set_shrink_end_child(False)
        self.set_resize_start_child(True)
        self.set_resize_end_child(True)

    def set_main_content(self, widget: Gtk.Widget):
        """Set the main (top/left) content"""
        widget.set_size_request(-1, self._main_min_size)
        self.set_start_child(widget)

    def set_bottom_content(self, widget: Gtk.Widget):
        """Set the bottom (or right) content"""
        widget.set_size_request(-1, self._bottom_min_size)
        self.set_end_child(widget)


# ============================================================================
# Status Indicator Widget
# ============================================================================

class StatusIndicator(Gtk.Box):
    """
    A standard status indicator with icon and label.

    Usage:
        status = StatusIndicator()
        status.set_running()
        status.set_stopped("Service not running")
        status.set_warning("Check configuration")
    """

    def __init__(self, initial_text: str = "Unknown"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=UI.SPACING_COMPACT)

        self._icon = Gtk.Image.new_from_icon_name(UI.ICON_QUESTION)
        self._icon.set_pixel_size(16)
        self.append(self._icon)

        self._label = Gtk.Label(label=initial_text)
        self._label.set_xalign(0)
        self.append(self._label)

    def set_running(self, text: str = "Running"):
        """Set to running/success state"""
        self._icon.set_from_icon_name(UI.ICON_SUCCESS)
        self._label.set_label(text)
        self._clear_css()
        self._label.add_css_class(UI.CSS_SUCCESS)

    def set_stopped(self, text: str = "Stopped"):
        """Set to stopped state"""
        self._icon.set_from_icon_name(UI.ICON_STOPPED)
        self._label.set_label(text)
        self._clear_css()

    def set_warning(self, text: str = "Warning"):
        """Set to warning state"""
        self._icon.set_from_icon_name(UI.ICON_WARNING)
        self._label.set_label(text)
        self._clear_css()
        self._label.add_css_class(UI.CSS_WARNING)

    def set_error(self, text: str = "Error"):
        """Set to error state"""
        self._icon.set_from_icon_name(UI.ICON_ERROR)
        self._label.set_label(text)
        self._clear_css()
        self._label.add_css_class(UI.CSS_ERROR)

    def set_info(self, text: str):
        """Set to info state"""
        self._icon.set_from_icon_name(UI.ICON_INFO)
        self._label.set_label(text)
        self._clear_css()

    def set_unknown(self, text: str = "Unknown"):
        """Set to unknown state"""
        self._icon.set_from_icon_name(UI.ICON_QUESTION)
        self._label.set_label(text)
        self._clear_css()
        self._label.add_css_class(UI.CSS_DIM)

    def _clear_css(self):
        """Remove status CSS classes"""
        for css in [UI.CSS_SUCCESS, UI.CSS_WARNING, UI.CSS_ERROR, UI.CSS_DIM]:
            self._label.remove_css_class(css)


# ============================================================================
# Standard Frame Builder
# ============================================================================

def create_standard_frame(title: str,
                          content: Optional[Gtk.Widget] = None,
                          spacing: int = UI.SPACING_SECTION) -> Tuple[Gtk.Frame, Gtk.Box]:
    """
    Create a standard frame with consistent margins.

    Args:
        title: Frame title
        content: Optional widget to add to frame
        spacing: Internal spacing

    Returns:
        Tuple of (Frame, content_box)
    """
    frame = Gtk.Frame()
    frame.set_label(title)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    box.set_margin_start(UI.MARGIN_SECTION)
    box.set_margin_end(UI.MARGIN_SECTION)
    box.set_margin_top(UI.MARGIN_INNER)
    box.set_margin_bottom(UI.MARGIN_INNER)

    if content:
        box.append(content)

    frame.set_child(box)
    return frame, box
