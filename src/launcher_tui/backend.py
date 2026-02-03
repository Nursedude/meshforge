"""
Dialog Backend for MeshForge TUI Launcher

Provides a whiptail/dialog backend for terminal UI dialogs.
Works over SSH, without X display, on any terminal.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Tuple, Optional, List


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

    def _run(self, args: List[str]) -> Tuple[int, str]:
        """
        Run dialog/whiptail command and return (returncode, output).

        whiptail uses stderr for returning selection.
        newt library opens /dev/tty directly for ncurses display.
        stderr is redirected to a temp file to capture the selection.
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
                except Exception:
                    pass  # Status bar failure must never block UI

            # Build command as list args (safe, no shell needed)
            cmd_parts = [self.backend] + [str(a) for a in full_args]

            # Run with stderr redirected to file to capture selection
            # whiptail/dialog opens /dev/tty directly for ncurses display
            with open(tmp_path, 'w') as stderr_file:
                # Interactive: user controls timing, but timeout prevents orphaned
                # processes if terminal disconnects or dialog crashes
                result = subprocess.run(cmd_parts, stderr=stderr_file, timeout=3600)

            # Read the captured selection
            with open(tmp_path, 'r') as f:
                output = f.read().strip()

            return result.returncode, output

        except Exception as e:
            return 1, str(e)
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

        args = [
            '--title', title,
            '--menu', text,
            str(h), str(w), str(lh)
        ]
        for tag, desc in choices:
            args.extend([tag, desc])

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
            str(h), str(w), str(lh)
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
