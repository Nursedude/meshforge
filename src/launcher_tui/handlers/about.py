"""
About menu handler — version info, changelog, system info, deps, help.

Batch 10a: Extracted from MeshForgeLauncher legacy methods in main.py.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class AboutHandler(BaseHandler):
    """About section — informational displays."""

    handler_id = "about"
    menu_section = "about"

    def menu_items(self):
        return [
            ("version", "Version Info        MeshForge version", None),
            ("changelog", "Changelog           Release history", None),
            ("sysinfo", "System Info         OS, Python, disk, uptime", None),
            ("deps", "Dependencies        Package status", None),
            ("help", "Help                Documentation", None),
        ]

    def execute(self, action):
        dispatch = {
            "version": ("Version Info", self._show_version),
            "changelog": ("Changelog", self._show_changelog),
            "sysinfo": ("System Info", self._show_system_info),
            "deps": ("Dependencies", self._show_dependency_status),
            "help": ("Help", self._show_help),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    def _show_version(self):
        """Show about information."""
        from __version__ import __version__
        text = f"""MeshForge v{__version__}
Network Operations Center

Bridges Meshtastic and Reticulum (RNS) mesh networks.

Features:
- Service management
- Hardware detection
- Space weather & propagation
- Gateway bridge (Mesh \u2194 RNS)
- Node monitoring

GitHub: github.com/Nursedude/meshforge
License: GPL-3.0

Made with aloha for the mesh community
73 de WH6GXZ"""

        self.ctx.dialog.msgbox("About MeshForge", text)

    def _show_changelog(self):
        """Display release history from VERSION_HISTORY in __version__.py."""
        from __version__ import VERSION_HISTORY
        from backend import clear_screen

        lines = ["MESHFORGE RELEASE HISTORY", "=" * 40, ""]

        for release in VERSION_HISTORY[:8]:  # Show last 8 releases
            version = release.get("version", "?")
            date = release.get("date", "?")
            status = release.get("status", "?")
            branch = release.get("branch", "")
            branch_info = f" ({branch})" if branch else ""

            lines.append(f"v{version}  [{status}]  {date}{branch_info}")
            lines.append("-" * 40)
            for change in release.get("changes", []):
                # Wrap long lines for whiptail
                if len(change) > 55:
                    lines.append(f"  {change[:55]}")
                    lines.append(f"    {change[55:]}")
                else:
                    lines.append(f"  {change}")
            lines.append("")

        clear_screen()
        print('\n'.join(lines))
        self.ctx.wait_for_enter()

    def _show_system_info(self):
        """Display system information: OS, Python, hardware, uptime, disk."""
        import platform
        from __version__ import __version__
        from utils.paths import get_real_user_home

        lines = ["SYSTEM INFORMATION", "=" * 40, ""]

        # OS info
        lines.append(f"Hostname:  {platform.node()}")
        lines.append(f"OS:        {platform.system()} {platform.release()}")
        try:
            result = subprocess.run(
                ['lsb_release', '-ds'], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                lines.append(f"Distro:    {result.stdout.strip()}")
        except Exception:
            pass
        lines.append(f"Arch:      {platform.machine()}")
        lines.append(f"Python:    {platform.python_version()}")
        lines.append(f"MeshForge: v{__version__}")
        lines.append("")

        # Uptime
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_secs = float(f.read().split()[0])
            days = int(uptime_secs // 86400)
            hours = int((uptime_secs % 86400) // 3600)
            mins = int((uptime_secs % 3600) // 60)
            lines.append(f"Uptime:    {days}d {hours}h {mins}m")
        except Exception:
            pass

        # Memory
        try:
            with open('/proc/meminfo', 'r') as f:
                meminfo = f.read()
            for line in meminfo.split('\n'):
                if line.startswith('MemTotal:'):
                    total_kb = int(line.split()[1])
                    lines.append(f"Memory:    {total_kb // 1024} MB total")
                    break
        except Exception:
            pass

        # Disk usage
        lines.append("")
        lines.append("DISK USAGE")
        lines.append("-" * 40)
        try:
            statvfs = os.statvfs('/')
            total_gb = (statvfs.f_frsize * statvfs.f_blocks) / (1024**3)
            free_gb = (statvfs.f_frsize * statvfs.f_bavail) / (1024**3)
            used_pct = ((total_gb - free_gb) / total_gb) * 100 if total_gb else 0
            lines.append(f"Root (/):  {free_gb:.1f} GB free / {total_gb:.1f} GB ({used_pct:.0f}% used)")
        except Exception:
            pass

        # Log directory size
        try:
            log_dir = get_real_user_home() / ".config" / "meshforge" / "logs"
            if log_dir.exists():
                total_size = sum(f.stat().st_size for f in log_dir.rglob("*") if f.is_file())
                lines.append(f"Logs:      {total_size / 1024:.1f} KB in {log_dir}")
        except Exception:
            pass

        self.ctx.dialog.msgbox("System Information", "\n".join(lines), width=65, height=22)

    def _show_dependency_status(self):
        """Check and display status of key Python dependencies."""
        deps = [
            ("meshtastic", "Meshtastic Python API"),
            ("RNS", "Reticulum Network Stack"),
            ("paho.mqtt.client", "MQTT client (Paho)"),
            ("folium", "Map generation"),
            ("requests", "HTTP client"),
            ("yaml", "YAML parser (PyYAML)"),
            ("serial", "Serial port (pyserial)"),
            ("flask", "Web server (Flask)"),
            ("Cryptodome", "Cryptography"),
        ]

        lines = ["DEPENDENCY STATUS", "=" * 45, ""]
        installed = 0
        missing = 0

        for module_name, description in deps:
            try:
                mod = __import__(module_name.split('.')[0])
                ver = getattr(mod, '__version__', getattr(mod, 'VERSION', '?'))
                lines.append(f"  [OK] {description:<28s} {ver}")
                installed += 1
            except ImportError:
                lines.append(f"  [--] {description:<28s} not installed")
                missing += 1

        lines.append("")
        lines.append("=" * 45)
        lines.append(f"Installed: {installed}  |  Missing: {missing}")
        if missing > 0:
            lines.append("\nMissing packages can be installed via:")
            lines.append("  pip install -r requirements.txt")

        self.ctx.dialog.msgbox("Dependencies", "\n".join(lines), width=55, height=20)

    def _show_help(self):
        """Show help documentation."""
        from backend import clear_screen

        help_text = """
MeshForge - Network Operations Center

KEYBOARD SHORTCUTS:
  1-6     Quick access to main sections
  q       Quick Actions
  e       Emergency Mode
  a       About
  x       Exit

NAVIGATION:
  Enter   Select item
  Esc     Go back / Cancel
  Tab     Move between buttons

DOCUMENTATION:
  https://github.com/Nursedude/meshforge

SUPPORT:
  Issues: github.com/Nursedude/meshforge/issues
"""
        clear_screen()
        print(help_text)
        self.ctx.wait_for_enter()
