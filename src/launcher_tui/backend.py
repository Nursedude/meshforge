"""
Dialog Backend for MeshForge TUI Launcher

Provides a whiptail/dialog backend for terminal UI dialogs.
Works over SSH, without X display, on any terminal.
"""

import logging
import os
import shutil
import subprocess
import sys
import termios
from pathlib import Path
from typing import Tuple, Optional, List

logger = logging.getLogger(__name__)


def clear_screen() -> None:
    """Clear the terminal including scrollback buffer.

    Uses three ANSI sequences:
    - \\033[H     Move cursor to home position (top-left)
    - \\033[2J    Clear the visible viewport
    - \\033[3J    Clear the scrollback buffer

    The scrollback clear (\\033[3J) prevents "screen roll" where old
    print() output bleeds through when whiptail/dialog redraws.
    """
    sys.stdout.write('\033[H\033[2J\033[3J')
    sys.stdout.flush()


class DialogBackend:
    """Backend for whiptail/dialog TUI dialogs."""

    def __init__(self):
        self.backend = self._detect_backend()
        self.width = 78
        self.height = 22
        self.list_height = 14
        self._status_bar = None

    def set_status_bar(self, status_bar) -> None:
        """Set a StatusBar instance for persistent --backtitle display.

        Args:
            status_bar: StatusBar instance (from status_bar module).
        """
        self._status_bar = status_bar

    def _detect_backend(self) -> Optional[str]:
        """Detect available dialog backend."""
        # Prefer whiptail (Debian/Ubuntu default, like raspi-config)
        if shutil.which('whiptail'):
            return 'whiptail'
        elif shutil.which('dialog'):
            return 'dialog'
        return None

    @property
    def available(self) -> bool:
        return self.backend is not None

    def _run(self, args: List[str], timeout: Optional[int] = None) -> Tuple[int, str]:
        """
        Run dialog/whiptail command and return (returncode, output).

        whiptail uses stderr for returning selection.
        newt library opens /dev/tty directly for ncurses display.
        stderr is redirected to a temp file to capture the selection.

        Args:
            args: Command arguments for the dialog backend.
            timeout: Optional subprocess timeout in seconds. Defaults to
                None (no timeout). whiptail/dialog opens /dev/tty directly,
                so when the terminal disconnects the process receives SIGHUP
                and terminates naturally — no timeout needed for orphan
                prevention.
        """
        import tempfile

        # Create temp file to capture selection output
        fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='meshforge_')
        os.close(fd)

        try:
            # Inject --backtitle from status bar if available
            full_args = list(args)
            if self._status_bar is not None:
                try:
                    backtitle = self._status_bar.get_status_line()
                    if backtitle:
                        full_args = ['--backtitle', backtitle] + full_args
                except Exception as e:
                    logger.debug("Status bar update failed: %s", e)

            # Build command as list args (safe, no shell needed)
            cmd_parts = [self.backend] + [str(a) for a in full_args]

            # Flush stale input from terminal before launching dialog.
            # Without this, leftover keystrokes (Enter, ESC sequences) from
            # the previous menu interaction can be read by the new whiptail
            # instance, causing it to immediately exit or select an item.
            try:
                termios.tcflush(sys.stdin, termios.TCIFLUSH)
            except (termios.error, ValueError, OSError):
                pass  # Not a terminal or already closed

            # Clear screen before launching dialog so whiptail saves a clean
            # main buffer. Without this, whiptail saves whatever print() output
            # was on the main buffer and restores it on exit — causing the
            # "screen roll" where old text bleeds through between dialogs.
            clear_screen()

            # Run with stderr redirected to file to capture selection.
            # No default timeout — whiptail opens /dev/tty so SIGHUP
            # handles terminal disconnect. The old 3600s timeout caused
            # the TUI to silently exit after 1 hour of idle.
            with open(tmp_path, 'w') as stderr_file:
                result = subprocess.run(
                    cmd_parts, stderr=stderr_file, timeout=timeout,
                )

            # Read the captured selection
            with open(tmp_path, 'r') as f:
                output = f.read().strip()

            if result.returncode != 0:
                try:
                    term_size = os.get_terminal_size()
                    term_info = f"{term_size.lines}x{term_size.columns}"
                except (ValueError, OSError):
                    term_info = "unknown"
                logger.warning(
                    "Dialog exited %d (cmd=%s, term=%s, output=%r)",
                    result.returncode,
                    ' '.join(cmd_parts[:6]),
                    term_info,
                    output[:80] if output else '',
                )

            return result.returncode, output

        except subprocess.TimeoutExpired:
            logger.warning("Dialog subprocess timed out after %ss", timeout)
            return 1, ""
        except OSError as e:
            logger.error("Dialog subprocess failed: %s", e)
            return 1, ""
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def msgbox(self, title: str, text: str, height: int = None, width: int = None) -> None:
        """Display a message box."""
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        self._run([
            '--title', title,
            '--msgbox', text,
            str(h), str(w)
        ])

    def yesno(self, title: str, text: str, default_no: bool = False,
              height: int = None, width: int = None) -> bool:
        """Display yes/no dialog. Returns True for yes."""
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        args = ['--title', title]
        if default_no:
            args.append('--defaultno')
        args += ['--yesno', text, str(h), str(w)]
        code, _ = self._run(args)
        return code == 0

    def menu(self, title: str, text: str, choices: List[Tuple[str, str]],
             height: int = None, width: int = None, list_height: int = None) -> Optional[str]:
        """
        Display a menu and return selected tag.

        Args:
            title: Window title
            text: Description text
            choices: List of (tag, description) tuples
            height: Optional dialog height (uses default if not specified)
            width: Optional dialog width (uses default if not specified)
            list_height: Optional list height (uses default if not specified)

        Returns:
            Selected tag or None if cancelled
        """
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        lh = list_height if list_height is not None else self.list_height

        # Auto-fit: shrink list_height/height to fit within terminal.
        # Without this, menus with multi-line text overflow height=22
        # on 24-row terminals when backtitle is active (2 lines overhead).
        try:
            term_rows = os.get_terminal_size().lines
        except (ValueError, OSError):
            term_rows = 24
        backtitle_overhead = 2 if self._status_bar else 0
        max_h = term_rows - backtitle_overhead
        # Estimate text lines (account for \n and line wrapping)
        inner_w = max(w - 4, 20)
        text_lines = sum(
            max(1, (len(line) + inner_w - 1) // inner_w)
            for line in text.split('\n')
        )
        # Chrome: border(2) + title(1) + padding(2) + button(1) = 6
        chrome = 6
        if chrome + text_lines + lh > max_h or h > max_h:
            lh = max(4, max_h - chrome - text_lines)
            h = min(h, max_h)

        args = [
            '--title', title,
            '--menu', text,
            str(h), str(w), str(lh),
            '--',  # End of options — menu items are positional args
        ]
        for tag, desc in choices:
            args.extend([tag, desc])

        code, output = self._run(args)
        if code == 0:
            return output

        # Retry once on failure — the main menu already has retry logic for
        # transient dialog failures, but submenus silently return None.
        # A single retry with a fresh input flush (in _run) catches cases
        # where stale terminal input caused whiptail to exit immediately.
        logger.debug("Menu '%s' failed (code=%d), retrying once", title, code)
        code, output = self._run(args)
        if code == 0:
            return output
        return None

    def inputbox(self, title: str, text: str, init: str = "",
                 height: int = None, width: int = None) -> Optional[str]:
        """Display input box and return text."""
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        args = [
            '--title', title,
            '--inputbox', text,
            str(h), str(w),
            init
        ]
        code, output = self._run(args)
        if code == 0:
            return output
        return None

    def editbox(self, title: str, file_path: str, height: int = None,
                width: int = None) -> Optional[str]:
        """Edit a text file IN-APP (whiptail/dialog --editbox) and return the
        edited text, or None on cancel.

        The In-Domain alternative to spawning nano/vi (MF018): the operator
        never leaves the TUI to fix a config. Does NOT write the file — the
        caller (config_edit.edit_config_in_app) persists the returned text, so
        it controls permissions / atomic write / validation.
        """
        h = height if height is not None else max(self.height, 20)
        w = width if width is not None else max(self.width, 72)
        code, output = self._run([
            '--title', title,
            '--editbox', str(file_path),
            str(h), str(w),
        ])
        if code == 0:
            return output
        return None

    def textbox(self, title: str, text: str, height: int = None,
                width: int = None) -> None:
        """Show read-only, scrollable text IN-APP (whiptail/dialog --textbox).

        The In-Domain alternative to dumping logs / command output to the
        terminal (MF018 Class 3): captured output stays inside the TUI in a
        scrollable pane. --textbox reads a file, so the text is written to a
        temp file, shown, and removed. Blank input shows "(no output)" — an
        empty pane must never read as a clean result.
        """
        import tempfile
        h = height if height is not None else max(self.height, 22)
        w = width if width is not None else max(self.width, 78)
        fd, tmp = tempfile.mkstemp(suffix='.txt', prefix='meshforge_log_')
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(text if text else "(no output)")
            self._run(['--title', title, '--scrolltext',
                       '--textbox', tmp, str(h), str(w)])
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def infobox(self, title: str, text: str) -> None:
        """Display info box (no wait for input)."""
        self._run([
            '--title', title,
            '--infobox', text,
            str(8), str(self.width)
        ])

    def gauge(self, title: str, text: str, percent: int) -> None:
        """Display progress gauge."""
        args = [
            '--title', title,
            '--gauge', text,
            str(8), str(self.width), str(percent)
        ]
        # Gauge needs stdin for progress updates
        try:
            proc = subprocess.Popen(
                [self.backend] + args,
                stdin=subprocess.PIPE,
                text=True
            )
            proc.communicate(input=str(percent), timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            # Gauge timeout or display issue - non-critical
            pass

    def checklist(self, title: str, text: str,
                  choices: List[Tuple[str, str, bool]],
                  height: int = None, width: int = None, list_height: int = None) -> Optional[List[str]]:
        """
        Display checklist dialog.

        Args:
            choices: List of (tag, description, selected) tuples
            height: Optional dialog height (uses default if not specified)
            width: Optional dialog width (uses default if not specified)
            list_height: Optional list height (uses default if not specified)

        Returns:
            List of selected tags or None if cancelled
        """
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        lh = list_height if list_height is not None else self.list_height

        args = [
            '--title', title,
            '--checklist', text,
            str(h), str(w), str(lh),
            '--',  # End of options — checklist items are positional args
        ]
        for tag, desc, selected in choices:
            status = 'ON' if selected else 'OFF'
            args.extend([tag, desc, status])

        code, output = self._run(args)
        if code == 0:
            # Parse quoted output (whiptail uses quotes)
            selected = output.replace('"', '').split()
            return selected
        return None


# Alias for convenience
Dialog = DialogBackend
