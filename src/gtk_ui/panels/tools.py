"""
Tools Panel - Network, RF, and MUDP tools for GTK4 interface
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import socket
from pathlib import Path


class ToolsPanel(Gtk.Box):
    """Tools panel for network, RF, and MUDP utilities"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()
        GLib.idle_add(self._refresh_status)

    def _build_ui(self):
        """Build the tools panel UI"""
        # Title
        title = Gtk.Label(label="System Tools")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(label="Network, RF, and MUDP utilities")
        subtitle.add_css_class("dim-label")
        subtitle.set_xalign(0)
        self.append(subtitle)

        # Scrolled container for tools
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)

        # System Monitor Section
        sys_frame = Gtk.Frame()
        sys_frame.set_label("System Monitor")
        sys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sys_box.set_margin_start(15)
        sys_box.set_margin_end(15)
        sys_box.set_margin_top(10)
        sys_box.set_margin_bottom(10)

        # System stats grid
        sys_grid = Gtk.Grid()
        sys_grid.set_row_spacing(5)
        sys_grid.set_column_spacing(15)

        # CPU usage
        cpu_lbl = Gtk.Label(label="CPU:")
        cpu_lbl.set_xalign(1)
        sys_grid.attach(cpu_lbl, 0, 0, 1, 1)
        self.cpu_label = Gtk.Label(label="--")
        self.cpu_label.set_xalign(0)
        sys_grid.attach(self.cpu_label, 1, 0, 1, 1)
        self.cpu_bar = Gtk.ProgressBar()
        self.cpu_bar.set_hexpand(True)
        sys_grid.attach(self.cpu_bar, 2, 0, 1, 1)

        # Memory usage
        mem_lbl = Gtk.Label(label="Memory:")
        mem_lbl.set_xalign(1)
        sys_grid.attach(mem_lbl, 0, 1, 1, 1)
        self.mem_label = Gtk.Label(label="--")
        self.mem_label.set_xalign(0)
        sys_grid.attach(self.mem_label, 1, 1, 1, 1)
        self.mem_bar = Gtk.ProgressBar()
        self.mem_bar.set_hexpand(True)
        sys_grid.attach(self.mem_bar, 2, 1, 1, 1)

        # Disk usage
        disk_lbl = Gtk.Label(label="Disk:")
        disk_lbl.set_xalign(1)
        sys_grid.attach(disk_lbl, 0, 2, 1, 1)
        self.disk_label = Gtk.Label(label="--")
        self.disk_label.set_xalign(0)
        sys_grid.attach(self.disk_label, 1, 2, 1, 1)
        self.disk_bar = Gtk.ProgressBar()
        self.disk_bar.set_hexpand(True)
        sys_grid.attach(self.disk_bar, 2, 2, 1, 1)

        # Temperature
        temp_lbl = Gtk.Label(label="CPU Temp:")
        temp_lbl.set_xalign(1)
        sys_grid.attach(temp_lbl, 0, 3, 1, 1)
        self.temp_label = Gtk.Label(label="--")
        self.temp_label.set_xalign(0)
        sys_grid.attach(self.temp_label, 1, 3, 1, 1)
        self.temp_bar = Gtk.ProgressBar()
        self.temp_bar.set_hexpand(True)
        sys_grid.attach(self.temp_bar, 2, 3, 1, 1)

        # Uptime
        uptime_lbl = Gtk.Label(label="Uptime:")
        uptime_lbl.set_xalign(1)
        sys_grid.attach(uptime_lbl, 0, 4, 1, 1)
        self.uptime_label = Gtk.Label(label="--")
        self.uptime_label.set_xalign(0)
        sys_grid.attach(self.uptime_label, 1, 4, 2, 1)

        sys_box.append(sys_grid)

        # System monitor buttons
        sys_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sys_buttons.set_margin_top(10)

        htop_btn = Gtk.Button(label="Open htop")
        htop_btn.connect("clicked", self._on_open_htop)
        sys_buttons.append(htop_btn)

        top_btn = Gtk.Button(label="Show Processes")
        top_btn.connect("clicked", self._on_show_processes)
        sys_buttons.append(top_btn)

        sys_box.append(sys_buttons)
        sys_frame.set_child(sys_box)
        content.append(sys_frame)

        # Start system monitor update timer
        GLib.timeout_add_seconds(2, self._update_system_stats)

        # Network Tools Section
        net_frame = Gtk.Frame()
        net_frame.set_label("Network Tools")
        net_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        net_box.set_margin_start(15)
        net_box.set_margin_end(15)
        net_box.set_margin_top(10)
        net_box.set_margin_bottom(10)

        # Network status
        net_status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        net_status.append(Gtk.Label(label="Local IP:"))
        self.local_ip_label = Gtk.Label(label="--")
        net_status.append(self.local_ip_label)
        net_status.append(Gtk.Label(label="   Port 4403:"))
        self.port_status_label = Gtk.Label(label="--")
        net_status.append(self.port_status_label)
        net_box.append(net_status)

        # Network action buttons
        net_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        net_buttons.set_margin_top(10)

        ping_btn = Gtk.Button(label="Ping Test")
        ping_btn.connect("clicked", self._on_ping_test)
        net_buttons.append(ping_btn)

        port_btn = Gtk.Button(label="TCP Port Test")
        port_btn.connect("clicked", self._on_port_test)
        net_buttons.append(port_btn)

        scan_btn = Gtk.Button(label="Find Meshtastic Devices")
        scan_btn.connect("clicked", self._on_scan_devices)
        net_buttons.append(scan_btn)

        net_box.append(net_buttons)
        net_frame.set_child(net_box)
        content.append(net_frame)

        # RF Tools Section
        rf_frame = Gtk.Frame()
        rf_frame.set_label("RF Tools")
        rf_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        rf_box.set_margin_start(15)
        rf_box.set_margin_end(15)
        rf_box.set_margin_top(10)
        rf_box.set_margin_bottom(10)

        rf_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Site Planner button - prominent position
        planner_btn = Gtk.Button(label="Site Planner")
        planner_btn.add_css_class("suggested-action")
        planner_btn.connect("clicked", self._on_site_planner)
        rf_buttons.append(planner_btn)

        link_btn = Gtk.Button(label="Link Budget Calculator")
        link_btn.connect("clicked", self._on_link_budget)
        rf_buttons.append(link_btn)

        preset_btn = Gtk.Button(label="LoRa Preset Comparison")
        preset_btn.connect("clicked", self._on_preset_compare)
        rf_buttons.append(preset_btn)

        detect_btn = Gtk.Button(label="Detect Radio Hardware")
        detect_btn.connect("clicked", self._on_detect_radio)
        rf_buttons.append(detect_btn)

        rf_box.append(rf_buttons)
        rf_frame.set_child(rf_box)
        content.append(rf_frame)

        # MUDP Tools Section
        mudp_frame = Gtk.Frame()
        mudp_frame.set_label("MUDP Tools (Meshtastic UDP)")
        mudp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        mudp_box.set_margin_start(15)
        mudp_box.set_margin_end(15)
        mudp_box.set_margin_top(10)
        mudp_box.set_margin_bottom(10)

        # MUDP status
        mudp_status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mudp_status.append(Gtk.Label(label="MUDP Package:"))
        self.mudp_status_label = Gtk.Label(label="--")
        mudp_status.append(self.mudp_status_label)
        mudp_box.append(mudp_status)

        mudp_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mudp_buttons.set_margin_top(10)

        install_mudp_btn = Gtk.Button(label="Install/Update MUDP")
        install_mudp_btn.connect("clicked", self._on_install_mudp)
        mudp_buttons.append(install_mudp_btn)

        multicast_btn = Gtk.Button(label="Test Multicast")
        multicast_btn.connect("clicked", self._on_test_multicast)
        mudp_buttons.append(multicast_btn)

        mudp_box.append(mudp_buttons)
        mudp_frame.set_child(mudp_box)
        content.append(mudp_frame)

        # Tool Manager Section
        mgr_frame = Gtk.Frame()
        mgr_frame.set_label("Tool Manager")
        mgr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        mgr_box.set_margin_start(15)
        mgr_box.set_margin_end(15)
        mgr_box.set_margin_top(10)
        mgr_box.set_margin_bottom(10)

        mgr_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        check_btn = Gtk.Button(label="Check for Updates")
        check_btn.connect("clicked", self._on_check_updates)
        mgr_buttons.append(check_btn)

        install_all_btn = Gtk.Button(label="Install All Tools")
        install_all_btn.connect("clicked", self._on_install_all)
        mgr_buttons.append(install_all_btn)

        mgr_box.append(mgr_buttons)
        mgr_frame.set_child(mgr_box)
        content.append(mgr_frame)

        # Output log
        log_frame = Gtk.Frame()
        log_frame.set_label("Output")
        log_frame.set_vexpand(True)

        self.output_view = Gtk.TextView()
        self.output_view.set_editable(False)
        self.output_view.set_monospace(True)
        self.output_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.output_buffer = self.output_view.get_buffer()

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(200)
        log_scroll.set_child(self.output_view)

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        log_box.set_margin_start(10)
        log_box.set_margin_end(10)
        log_box.set_margin_top(10)
        log_box.set_margin_bottom(10)
        log_box.append(log_scroll)

        clear_btn = Gtk.Button(label="Clear Log")
        clear_btn.connect("clicked", lambda b: self.output_buffer.set_text(""))
        log_box.append(clear_btn)

        log_frame.set_child(log_box)
        content.append(log_frame)

        scrolled.set_child(content)
        self.append(scrolled)

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh Status")
        refresh_btn.connect("clicked", lambda b: self._refresh_status())
        self.append(refresh_btn)

    def _log(self, message):
        """Add message to log"""
        end_iter = self.output_buffer.get_end_iter()
        self.output_buffer.insert(end_iter, message + "\n")

    def _update_system_stats(self):
        """Update system statistics"""
        threading.Thread(target=self._fetch_system_stats, daemon=True).start()
        return True  # Continue timer

    def _fetch_system_stats(self):
        """Fetch system stats in background"""
        import os

        # CPU usage
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
            GLib.idle_add(self.cpu_label.set_label, f"{cpu_pct:.1f}%")
            GLib.idle_add(self.cpu_bar.set_fraction, cpu_pct / 100)
        except Exception:
            pass

        # Memory usage
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
            GLib.idle_add(self.mem_label.set_label, f"{used_mb:.0f}/{total_mb:.0f} MB")
            GLib.idle_add(self.mem_bar.set_fraction, mem_pct / 100)
        except Exception:
            pass

        # Disk usage
        try:
            stat = os.statvfs('/')
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bfree * stat.f_frsize
            used = total - free
            disk_pct = 100 * used / total if total > 0 else 0
            used_gb = used / (1024**3)
            total_gb = total / (1024**3)
            GLib.idle_add(self.disk_label.set_label, f"{used_gb:.1f}/{total_gb:.1f} GB")
            GLib.idle_add(self.disk_bar.set_fraction, disk_pct / 100)
        except Exception:
            pass

        # Temperature
        try:
            temp = None
            # Try thermal zone first
            temp_file = Path('/sys/class/thermal/thermal_zone0/temp')
            if temp_file.exists():
                temp = int(temp_file.read_text().strip()) / 1000
            # Try vcgencmd (Raspberry Pi)
            if temp is None:
                result = subprocess.run(['vcgencmd', 'measure_temp'],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    # Format: temp=45.0'C
                    match = result.stdout.strip()
                    if 'temp=' in match:
                        temp = float(match.split('=')[1].replace("'C", ""))
            if temp is not None:
                temp_pct = min(temp / 85, 1.0)  # 85°C as max
                GLib.idle_add(self.temp_label.set_label, f"{temp:.1f}°C")
                GLib.idle_add(self.temp_bar.set_fraction, temp_pct)
        except Exception:
            pass

        # Uptime
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
            GLib.idle_add(self.uptime_label.set_label, uptime_str)
        except Exception:
            pass

    def _on_open_htop(self, button):
        """Open htop in a terminal"""
        threading.Thread(target=self._run_htop, daemon=True).start()

    def _run_htop(self):
        """Run htop in terminal"""
        terminals = [
            ['x-terminal-emulator', '-e', 'htop'],
            ['gnome-terminal', '--', 'htop'],
            ['xfce4-terminal', '-e', 'htop'],
            ['lxterminal', '-e', 'htop'],
            ['xterm', '-e', 'htop'],
        ]
        for term in terminals:
            try:
                subprocess.Popen(term, start_new_session=True)
                GLib.idle_add(self._log, "htop opened in terminal")
                return
            except FileNotFoundError:
                continue
        GLib.idle_add(self._log, "No terminal emulator found. Install htop and run manually.")

    def _on_show_processes(self, button):
        """Show top processes"""
        GLib.idle_add(self._log, "\n=== Top Processes (by CPU) ===")
        threading.Thread(target=self._fetch_processes, daemon=True).start()

    def _fetch_processes(self):
        """Fetch process list"""
        try:
            result = subprocess.run(
                ['ps', 'aux', '--sort=-%cpu'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[:11]  # Header + 10 processes
                for line in lines:
                    GLib.idle_add(self._log, line[:100])
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _refresh_status(self):
        """Refresh tool status"""
        threading.Thread(target=self._refresh_status_thread, daemon=True).start()

    def _refresh_status_thread(self):
        """Background thread for status refresh"""
        # Get local IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            GLib.idle_add(self.local_ip_label.set_text, local_ip)
        except Exception:
            GLib.idle_add(self.local_ip_label.set_text, "Unknown")

        # Check port 4403
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 4403))
            sock.close()
            status = "OPEN" if result == 0 else "CLOSED"
            GLib.idle_add(self.port_status_label.set_text, status)
        except Exception:
            GLib.idle_add(self.port_status_label.set_text, "Error")

        # Check MUDP
        try:
            result = subprocess.run(['pip', 'show', 'mudp'], capture_output=True, timeout=10)
            status = "Installed" if result.returncode == 0 else "Not Installed"
            GLib.idle_add(self.mudp_status_label.set_text, status)
        except Exception:
            GLib.idle_add(self.mudp_status_label.set_text, "Unknown")

    def _on_ping_test(self, button):
        """Run ping test"""
        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading="Ping Test",
            body="Enter hostname or IP address:"
        )

        entry = Gtk.Entry()
        entry.set_text("8.8.8.8")
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ping", "Ping")
        dialog.set_response_appearance("ping", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "ping":
                host = entry.get_text()
                threading.Thread(target=self._run_ping, args=(host,), daemon=True).start()
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _run_ping(self, host):
        """Run ping in background"""
        GLib.idle_add(self._log, f"Pinging {host}...")
        try:
            result = subprocess.run(
                ['ping', '-c', '4', host],
                capture_output=True, text=True, timeout=30
            )
            GLib.idle_add(self._log, result.stdout)
            if result.returncode != 0:
                GLib.idle_add(self._log, result.stderr)
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_port_test(self, button):
        """Run TCP port test"""
        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading="TCP Port Test",
            body="Enter host:port (e.g., localhost:4403)"
        )

        entry = Gtk.Entry()
        entry.set_text("localhost:4403")
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("test", "Test")
        dialog.set_response_appearance("test", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "test":
                addr = entry.get_text()
                parts = addr.split(':')
                host = parts[0]
                port = int(parts[1]) if len(parts) > 1 else 4403
                threading.Thread(target=self._run_port_test, args=(host, port), daemon=True).start()
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _run_port_test(self, host, port):
        """Run port test in background"""
        GLib.idle_add(self._log, f"Testing TCP {host}:{port}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                GLib.idle_add(self._log, f"Port {port} is OPEN on {host}")
            else:
                GLib.idle_add(self._log, f"Port {port} is CLOSED on {host}")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_scan_devices(self, button):
        """Scan for Meshtastic devices"""
        GLib.idle_add(self._log, "Scanning for Meshtastic devices (port 4403)...")
        threading.Thread(target=self._run_scan, daemon=True).start()

    def _run_scan(self):
        """Run device scan in background"""
        try:
            # Get local network
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()

            base = '.'.join(local_ip.split('.')[:3])
            found = []

            for i in range(1, 255):
                ip = f"{base}.{i}"
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.3)
                    result = sock.connect_ex((ip, 4403))
                    sock.close()
                    if result == 0:
                        found.append(ip)
                        GLib.idle_add(self._log, f"Found: {ip}:4403")
                except Exception:
                    pass

            if found:
                GLib.idle_add(self._log, f"\nFound {len(found)} device(s)")
            else:
                GLib.idle_add(self._log, "No Meshtastic devices found on port 4403")
        except Exception as e:
            GLib.idle_add(self._log, f"Scan error: {e}")

    def _on_site_planner(self, button):
        """Open Meshtastic Site Planner in browser"""
        import os
        url = "https://site.meshtastic.org/"
        self._log("\n=== Meshtastic Site Planner ===")
        self._log(f"Opening {url}")
        self._log("\nFeatures:")
        self._log("  • RF coverage prediction using ITM/Longley-Rice model")
        self._log("  • Terrain analysis with NASA SRTM data")
        self._log("  • Multi-node network planning")
        self._log("  • Customizable antenna gain, cable loss, clutter")

        def try_open():
            # Get real user if running as sudo
            user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

            # Method 1: xdg-open as the real user
            try:
                result = subprocess.run(
                    ['sudo', '-u', user, 'xdg-open', url],
                    capture_output=True, timeout=10
                )
                if result.returncode == 0:
                    GLib.idle_add(self._log, "Browser opened successfully")
                    return
            except Exception:
                pass

            # Method 2: Try common browsers directly
            browsers = ['chromium-browser', 'firefox', 'epiphany-browser']
            for browser in browsers:
                try:
                    subprocess.Popen(
                        ['sudo', '-u', user, browser, url],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    GLib.idle_add(self._log, f"Browser opened ({browser})")
                    return
                except Exception:
                    continue

            # Method 3: webbrowser module fallback
            try:
                import webbrowser
                webbrowser.open(url)
                GLib.idle_add(self._log, "Browser opened successfully")
                return
            except Exception:
                pass

            GLib.idle_add(self._log, "Could not open browser automatically")
            GLib.idle_add(self._log, f"Visit manually: {url}")

        threading.Thread(target=try_open, daemon=True).start()

    def _on_link_budget(self, button):
        """Show link budget info"""
        GLib.idle_add(self._log, "\n=== Link Budget Calculator ===")
        GLib.idle_add(self._log, "For interactive calculator, use CLI: python3 src/main.py -> r")
        GLib.idle_add(self._log, "\nQuick Reference:")
        GLib.idle_add(self._log, "  LONG_FAST: ~30km LOS, -123dBm sensitivity")
        GLib.idle_add(self._log, "  LONG_SLOW: ~80km LOS, -129dBm sensitivity")
        GLib.idle_add(self._log, "  FSPL at 10km, 915MHz: ~112dB")

    def _on_preset_compare(self, button):
        """Show preset comparison"""
        GLib.idle_add(self._log, "\n=== LoRa Preset Comparison ===")
        presets = [
            ("SHORT_TURBO", "21875 bps", "-108 dBm", "~3 km"),
            ("SHORT_FAST", "10937 bps", "-111 dBm", "~5 km"),
            ("MEDIUM_FAST", "3516 bps", "-117 dBm", "~12 km"),
            ("LONG_FAST", "1066 bps", "-123 dBm", "~30 km"),
            ("LONG_SLOW", "293 bps", "-129 dBm", "~80 km"),
            ("VERY_LONG_SLOW", "146 bps", "-132 dBm", "~120 km"),
        ]
        for name, rate, sens, range_ in presets:
            GLib.idle_add(self._log, f"  {name}: {rate}, {sens}, {range_}")

    def _on_detect_radio(self, button):
        """Detect radio hardware"""
        GLib.idle_add(self._log, "\n=== Radio Hardware Detection ===")
        threading.Thread(target=self._run_detect_radio, daemon=True).start()

    def _run_detect_radio(self):
        """Run radio detection in background"""
        # Check SPI
        spi_devs = list(Path('/dev').glob('spidev*'))
        if spi_devs:
            GLib.idle_add(self._log, f"SPI: Enabled ({len(spi_devs)} devices)")
        else:
            GLib.idle_add(self._log, "SPI: Not enabled")

        # Check I2C
        i2c_devs = list(Path('/dev').glob('i2c-*'))
        if i2c_devs:
            GLib.idle_add(self._log, f"I2C: Enabled ({len(i2c_devs)} devices)")
        else:
            GLib.idle_add(self._log, "I2C: Not enabled")

        # Check GPIO
        if Path('/sys/class/gpio').exists():
            GLib.idle_add(self._log, "GPIO: Available")
        else:
            GLib.idle_add(self._log, "GPIO: Not available")

    def _on_install_mudp(self, button):
        """Install MUDP package"""
        GLib.idle_add(self._log, "\nInstalling/Updating MUDP...")
        threading.Thread(target=self._run_install_mudp, daemon=True).start()

    def _run_install_mudp(self):
        """Run MUDP install in background"""
        try:
            result = subprocess.run(
                ['pip', 'install', '--upgrade', '--break-system-packages', 'mudp'],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                GLib.idle_add(self._log, "MUDP installed/updated successfully")
            else:
                GLib.idle_add(self._log, f"Installation failed: {result.stderr}")
            GLib.idle_add(self._refresh_status)
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_test_multicast(self, button):
        """Test multicast join"""
        GLib.idle_add(self._log, "\n=== Multicast Test ===")
        threading.Thread(target=self._run_multicast_test, daemon=True).start()

    def _run_multicast_test(self):
        """Run multicast test in background"""
        import struct
        group = "224.0.0.69"
        port = 4403

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', port))

            mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            GLib.idle_add(self._log, f"Successfully joined multicast group {group}")

            sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            sock.close()
            GLib.idle_add(self._log, "Successfully left multicast group")

        except OSError as e:
            if "Address already in use" in str(e):
                GLib.idle_add(self._log, f"Port {port} in use (meshtasticd running?) - OK")
            else:
                GLib.idle_add(self._log, f"Error: {e}")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_check_updates(self, button):
        """Check for tool updates"""
        GLib.idle_add(self._log, "\n=== Checking for Updates ===")
        threading.Thread(target=self._run_check_updates, daemon=True).start()

    def _run_check_updates(self):
        """Run update check in background"""
        packages = ['mudp', 'meshtastic']
        for pkg in packages:
            try:
                result = subprocess.run(
                    ['pip', 'show', pkg],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if line.startswith('Version:'):
                            version = line.split(':', 1)[1].strip()
                            GLib.idle_add(self._log, f"{pkg}: {version}")
                            break
                else:
                    GLib.idle_add(self._log, f"{pkg}: Not installed")
            except Exception:
                GLib.idle_add(self._log, f"{pkg}: Check failed")

    def _on_install_all(self, button):
        """Install all tools"""
        GLib.idle_add(self._log, "\n=== Installing All Tools ===")
        threading.Thread(target=self._run_install_all, daemon=True).start()

    def _run_install_all(self):
        """Run install all in background"""
        # pip packages
        pip_pkgs = ['mudp']
        for pkg in pip_pkgs:
            GLib.idle_add(self._log, f"Installing {pkg}...")
            subprocess.run(
                ['pip', 'install', '--break-system-packages', pkg],
                capture_output=True, timeout=120
            )

        # apt packages
        apt_pkgs = ['nmap', 'net-tools', 'socat']
        GLib.idle_add(self._log, f"Installing apt packages: {', '.join(apt_pkgs)}")
        subprocess.run(['sudo', 'apt', 'install', '-y'] + apt_pkgs, capture_output=True)

        GLib.idle_add(self._log, "Installation complete")
        GLib.idle_add(self._refresh_status)
