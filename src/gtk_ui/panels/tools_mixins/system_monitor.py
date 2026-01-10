"""
System Monitor Mixin - CPU, Memory, Disk, Temperature monitoring

Extracted from tools.py for maintainability.
"""

import os
import subprocess
import threading
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib


class SystemMonitorMixin:
    """Mixin providing system monitoring functionality for ToolsPanel"""

    def _update_system_stats(self):
        """Update system statistics (called by timer)"""
        threading.Thread(target=self._fetch_system_stats, daemon=True).start()
        return True  # Continue timer

    def _fetch_system_stats(self):
        """Fetch system stats in background"""
        self._fetch_cpu_stats()
        self._fetch_memory_stats()
        self._fetch_disk_stats()
        self._fetch_temperature()
        self._fetch_uptime()

    def _fetch_cpu_stats(self):
        """Fetch CPU usage from /proc/stat"""
        try:
            with open('/proc/stat', 'r') as f:
                line = f.readline()
            cpu_vals = [int(x) for x in line.split()[1:8]]
            idle = cpu_vals[3]
            total = sum(cpu_vals)
            if hasattr(self, '_last_cpu'):
                diff_idle = idle - self._last_cpu[0]
                diff_total = total - self._last_cpu[1]
                cpu_pct = 100 * (1 - diff_idle / diff_total) if diff_total > 0 else 0
            else:
                cpu_pct = 0
            self._last_cpu = (idle, total)
            if hasattr(self, 'cpu_label'):
                GLib.idle_add(self.cpu_label.set_label, f"{cpu_pct:.1f}%")
            if hasattr(self, 'cpu_bar'):
                GLib.idle_add(self.cpu_bar.set_fraction, cpu_pct / 100)
        except Exception:
            pass

    def _fetch_memory_stats(self):
        """Fetch memory usage from /proc/meminfo"""
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_info = {}
            for line in lines:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = int(parts[1].split()[0])  # kB
                    mem_info[key] = val
            total = mem_info.get('MemTotal', 1)
            avail = mem_info.get('MemAvailable', mem_info.get('MemFree', 0))
            used = total - avail
            mem_pct = 100 * used / total if total > 0 else 0
            used_mb = used / 1024
            total_mb = total / 1024
            if hasattr(self, 'mem_label'):
                GLib.idle_add(self.mem_label.set_label, f"{used_mb:.0f}/{total_mb:.0f} MB")
            if hasattr(self, 'mem_bar'):
                GLib.idle_add(self.mem_bar.set_fraction, mem_pct / 100)
        except Exception:
            pass

    def _fetch_disk_stats(self):
        """Fetch disk usage via statvfs"""
        try:
            stat = os.statvfs('/')
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bfree * stat.f_frsize
            used = total - free
            disk_pct = 100 * used / total if total > 0 else 0
            used_gb = used / (1024**3)
            total_gb = total / (1024**3)
            if hasattr(self, 'disk_label'):
                GLib.idle_add(self.disk_label.set_label, f"{used_gb:.1f}/{total_gb:.1f} GB")
            if hasattr(self, 'disk_bar'):
                GLib.idle_add(self.disk_bar.set_fraction, disk_pct / 100)
        except Exception:
            pass

    def _fetch_temperature(self):
        """Fetch CPU temperature from sysfs or vcgencmd"""
        try:
            temp = None
            # Try thermal zone first
            temp_file = Path('/sys/class/thermal/thermal_zone0/temp')
            if temp_file.exists():
                temp = int(temp_file.read_text().strip()) / 1000
            # Try vcgencmd (Raspberry Pi)
            if temp is None:
                result = subprocess.run(
                    ['vcgencmd', 'measure_temp'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    # Format: temp=45.0'C
                    match = result.stdout.strip()
                    if 'temp=' in match:
                        temp = float(match.split('=')[1].replace("'C", ""))
            if temp is not None:
                temp_pct = min(temp / 85, 1.0)  # 85°C as max
                if hasattr(self, 'temp_label'):
                    GLib.idle_add(self.temp_label.set_label, f"{temp:.1f}°C")
                if hasattr(self, 'temp_bar'):
                    GLib.idle_add(self.temp_bar.set_fraction, temp_pct)
        except Exception:
            pass

    def _fetch_uptime(self):
        """Fetch system uptime from /proc/uptime"""
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_sec = float(f.read().split()[0])
            days = int(uptime_sec // 86400)
            hours = int((uptime_sec % 86400) // 3600)
            mins = int((uptime_sec % 3600) // 60)
            if days > 0:
                uptime_str = f"{days}d {hours}h {mins}m"
            elif hours > 0:
                uptime_str = f"{hours}h {mins}m"
            else:
                uptime_str = f"{mins}m"
            if hasattr(self, 'uptime_label'):
                GLib.idle_add(self.uptime_label.set_label, uptime_str)
        except Exception:
            pass
