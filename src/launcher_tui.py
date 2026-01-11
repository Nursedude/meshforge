#!/usr/bin/env python3
"""
MeshForge Launcher - raspi-config Style TUI

A whiptail/dialog based launcher that works:
- Over SSH (no display required)
- With GTK when display available
- On any terminal

Uses whiptail (Debian/Ubuntu default) with dialog fallback.
Falls back to basic terminal menu if neither available.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Tuple, Optional, List

# Import version
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.5"

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


class DialogBackend:
    """Backend for whiptail/dialog TUI dialogs."""

    def __init__(self):
        self.backend = self._detect_backend()
        self.width = 78
        self.height = 22
        self.list_height = 14

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
        We use os.system for proper terminal inheritance and redirect
        stderr to a temp file to capture the selection.
        """
        import tempfile
        import shlex

        # Create temp file to capture selection output
        fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='meshforge_')
        os.close(fd)

        try:
            # Build command with proper shell quoting
            cmd_parts = [self.backend] + [str(a) for a in args]
            escaped_cmd = ' '.join(shlex.quote(p) for p in cmd_parts)

            # Use os.system for proper terminal inheritance
            # stderr redirected to file captures selection
            # newt library opens /dev/tty directly for display
            exit_code = os.system(f'{escaped_cmd} 2>{shlex.quote(tmp_path)}')

            # Read the captured selection
            with open(tmp_path, 'r') as f:
                output = f.read().strip()

            # os.system returns wait status, extract exit code
            return os.waitstatus_to_exitcode(exit_code) if hasattr(os, 'waitstatus_to_exitcode') else (exit_code >> 8), output

        except Exception as e:
            return 1, str(e)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def msgbox(self, title: str, text: str) -> None:
        """Display a message box."""
        self._run([
            '--title', title,
            '--msgbox', text,
            str(self.height), str(self.width)
        ])

    def yesno(self, title: str, text: str, default_no: bool = False) -> bool:
        """Display yes/no dialog. Returns True for yes."""
        args = ['--title', title]
        if default_no:
            args.append('--defaultno')
        args += ['--yesno', text, str(self.height), str(self.width)]
        code, _ = self._run(args)
        return code == 0

    def menu(self, title: str, text: str, choices: List[Tuple[str, str]]) -> Optional[str]:
        """
        Display a menu and return selected tag.

        Args:
            title: Window title
            text: Description text
            choices: List of (tag, description) tuples

        Returns:
            Selected tag or None if cancelled
        """
        args = [
            '--title', title,
            '--menu', text,
            str(self.height), str(self.width), str(self.list_height)
        ]
        for tag, desc in choices:
            args.extend([tag, desc])

        code, output = self._run(args)
        if code == 0:
            return output
        return None

    def inputbox(self, title: str, text: str, init: str = "") -> Optional[str]:
        """Display input box and return text."""
        args = [
            '--title', title,
            '--inputbox', text,
            str(self.height), str(self.width),
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
                  choices: List[Tuple[str, str, bool]]) -> Optional[List[str]]:
        """
        Display checklist dialog.

        Args:
            choices: List of (tag, description, selected) tuples

        Returns:
            List of selected tags or None if cancelled
        """
        args = [
            '--title', title,
            '--checklist', text,
            str(self.height), str(self.width), str(self.list_height)
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


class MeshForgeLauncher:
    """MeshForge launcher with raspi-config style interface."""

    def __init__(self):
        self.dialog = DialogBackend()
        self.src_dir = Path(__file__).parent
        self.env = self._detect_environment()

    def _detect_environment(self) -> dict:
        """Detect the current environment."""
        env = {
            'has_display': False,
            'display_type': None,
            'is_ssh': False,
            'has_gtk': False,
            'is_root': os.geteuid() == 0,
        }

        # Check for display
        display = os.environ.get('DISPLAY')
        wayland = os.environ.get('WAYLAND_DISPLAY')
        if display or wayland:
            env['has_display'] = True
            env['display_type'] = 'Wayland' if wayland else 'X11'

        # Check for SSH
        if os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'):
            env['is_ssh'] = True

        # Check for GTK4
        try:
            import gi
            gi.require_version('Gtk', '4.0')
            gi.require_version('Adw', '1')
            from gi.repository import Gtk, Adw
            env['has_gtk'] = True
        except (ImportError, ValueError):
            pass

        return env

    def run(self):
        """Run the launcher."""
        if not self.env['is_root']:
            print("\nError: MeshForge requires root/sudo privileges")
            print("Please run: sudo python3 src/launcher_tui.py")
            sys.exit(1)

        if not self.dialog.available:
            # Fallback to basic launcher
            print("whiptail/dialog not available, using basic launcher...")
            self._run_basic_launcher()
            return

        self._run_main_menu()

    def _run_main_menu(self):
        """Display the main menu."""
        while True:
            # Build dynamic choices based on environment
            choices = []

            # Interfaces section
            if self.env['has_display'] and self.env['has_gtk']:
                choices.append(("gtk", "GTK4 Desktop Interface"))
            choices.append(("cli", "Rich CLI (Terminal Menu)"))
            choices.append(("web", "Web Monitor Dashboard"))

            # Tools section
            choices.append(("---", "──────────── Tools ────────────"))
            choices.append(("diag", "System Diagnostics"))
            choices.append(("network", "Network Tools"))
            choices.append(("rf", "RF Tools"))
            choices.append(("site", "Site Planner"))
            choices.append(("bridge", "Start Gateway Bridge"))
            choices.append(("monitor", "Node Monitor"))
            choices.append(("nodes", "View Nodes"))
            choices.append(("messaging", "Messaging"))
            choices.append(("space", "Space Weather"))

            # Config section
            choices.append(("---", "──────────── Config ───────────"))
            choices.append(("meshtasticd", "Meshtasticd Config"))
            choices.append(("services", "Service Management"))
            choices.append(("hardware", "Hardware Detection"))
            choices.append(("settings", "Settings"))

            # System
            choices.append(("---", "──────────────────────────────"))
            choices.append(("about", "About MeshForge"))
            choices.append(("quit", "Exit"))

            # Filter out separators for whiptail
            filtered_choices = [(t, d) for t, d in choices if t != "---"]

            choice = self.dialog.menu(
                f"MeshForge v{__version__}",
                "Select an option:",
                filtered_choices
            )

            if choice is None or choice == "quit":
                break

            self._handle_choice(choice)

    def _handle_choice(self, choice: str):
        """Handle menu selection."""
        if choice == "gtk":
            self._launch_gtk()
        elif choice == "cli":
            self._launch_cli()
        elif choice == "web":
            self._launch_web()
        elif choice == "diag":
            self._diagnostics_menu()
        elif choice == "network":
            self._network_tools_menu()
        elif choice == "site":
            self._site_planner_menu()
        elif choice == "bridge":
            self._run_bridge()
        elif choice == "monitor":
            self._run_monitor()
        elif choice == "space":
            self._show_space_weather()
        elif choice == "nodes":
            self._show_nodes()
        elif choice == "messaging":
            self._messaging_menu()
        elif choice == "rf":
            self._rf_tools_menu()
        elif choice == "meshtasticd":
            self._meshtasticd_menu()
        elif choice == "services":
            self._service_menu()
        elif choice == "hardware":
            self._hardware_menu()
        elif choice == "settings":
            self._settings_menu()
        elif choice == "about":
            self._show_about()

    def _launch_gtk(self):
        """Launch GTK interface."""
        self.dialog.infobox("Launching", "Starting GTK4 Desktop Interface...")
        os.execv(sys.executable, [sys.executable, str(self.src_dir / 'main_gtk.py')])

    def _launch_cli(self):
        """Launch CLI interface."""
        self.dialog.infobox("Launching", "Starting Rich CLI...")
        os.execv(sys.executable, [sys.executable, str(self.src_dir / 'main.py')])

    def _launch_web(self):
        """Launch Web monitor."""
        self.dialog.msgbox(
            "Web Monitor",
            "Starting Web Monitor...\n\n"
            "Access at: http://localhost:5000\n\n"
            "Press Ctrl+C to stop."
        )
        os.execv(sys.executable, [sys.executable, str(self.src_dir / 'web_monitor.py')])

    # =========================================================================
    # System Diagnostics
    # =========================================================================

    def _diagnostics_menu(self):
        """System diagnostics menu."""
        while True:
            choices = [
                ("full", "Full System Diagnostic"),
                ("services", "Service Status Check"),
                ("network", "Network Connectivity"),
                ("hardware", "Hardware Interfaces"),
                ("logs", "Log Analysis"),
                ("system", "System Resources"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "System Diagnostics",
                "Comprehensive system health checks:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "full":
                self._run_full_diagnostics()
            elif choice == "services":
                self._check_services()
            elif choice == "network":
                self._check_network()
            elif choice == "hardware":
                self._check_hardware_interfaces()
            elif choice == "logs":
                self._analyze_logs()
            elif choice == "system":
                self._check_system_resources()

    def _run_full_diagnostics(self):
        """Run full diagnostics script."""
        subprocess.run(['clear'], check=False)
        subprocess.run([sys.executable, str(self.src_dir / 'cli' / 'diagnose.py')])
        input("\nPress Enter to continue...")

    def _check_services(self):
        """Check service status."""
        self.dialog.infobox("Services", "Checking services...")

        services = ['meshtasticd', 'rnsd', 'lxmf.delivery']
        results = []

        for svc in services:
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', svc],
                    capture_output=True, text=True, timeout=5
                )
                status = result.stdout.strip()
                results.append(f"{svc}: {status.upper()}")
            except Exception:
                results.append(f"{svc}: UNKNOWN")

        self.dialog.msgbox("Service Status", "\n".join(results))

    def _check_network(self):
        """Check network connectivity."""
        self.dialog.infobox("Network", "Testing connectivity...")

        tests = []

        # Test meshtasticd TCP
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()
            tests.append(f"meshtasticd (4403): {'OK' if result == 0 else 'FAIL'}")
        except Exception:
            tests.append("meshtasticd (4403): ERROR")

        # Test RNS
        try:
            result = subprocess.run(
                ['rnstatus', '-j'],
                capture_output=True, text=True, timeout=5
            )
            tests.append(f"RNS Status: {'OK' if result.returncode == 0 else 'FAIL'}")
        except Exception:
            tests.append("RNS Status: NOT AVAILABLE")

        # Test internet
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('8.8.8.8', 53))
            sock.close()
            tests.append(f"Internet (DNS): {'OK' if result == 0 else 'FAIL'}")
        except Exception:
            tests.append("Internet: ERROR")

        self.dialog.msgbox("Network Connectivity", "\n".join(tests))

    def _check_hardware_interfaces(self):
        """Check hardware interfaces."""
        self.dialog.infobox("Hardware", "Checking interfaces...")

        checks = []

        # SPI
        spi_enabled = Path('/dev/spidev0.0').exists()
        checks.append(f"SPI: {'ENABLED' if spi_enabled else 'DISABLED'}")

        # I2C
        i2c_enabled = Path('/dev/i2c-1').exists()
        checks.append(f"I2C: {'ENABLED' if i2c_enabled else 'DISABLED'}")

        # Serial
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        checks.append(f"Serial Ports: {len(serial_ports)} found")
        for port in serial_ports[:3]:
            checks.append(f"  - {port.name}")

        # GPIO
        gpio_available = Path('/sys/class/gpio').exists()
        checks.append(f"GPIO: {'AVAILABLE' if gpio_available else 'NOT AVAILABLE'}")

        self.dialog.msgbox("Hardware Interfaces", "\n".join(checks))

    def _analyze_logs(self):
        """Analyze system logs for errors."""
        self.dialog.infobox("Logs", "Analyzing logs...")

        logs = []

        # Check meshtasticd logs
        try:
            result = subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '-n', '20', '--no-pager'],
                capture_output=True, text=True, timeout=10
            )
            errors = [l for l in result.stdout.split('\n') if 'error' in l.lower()]
            logs.append(f"meshtasticd: {len(errors)} errors in last 20 lines")
        except Exception:
            logs.append("meshtasticd: Unable to read logs")

        # Check rnsd logs
        try:
            result = subprocess.run(
                ['journalctl', '-u', 'rnsd', '-n', '20', '--no-pager'],
                capture_output=True, text=True, timeout=10
            )
            errors = [l for l in result.stdout.split('\n') if 'error' in l.lower()]
            logs.append(f"rnsd: {len(errors)} errors in last 20 lines")
        except Exception:
            logs.append("rnsd: Unable to read logs")

        logs.append("")
        logs.append("For detailed logs, use:")
        logs.append("  journalctl -u meshtasticd -f")

        self.dialog.msgbox("Log Analysis", "\n".join(logs))

    def _check_system_resources(self):
        """Check system resources."""
        self.dialog.infobox("System", "Checking resources...")

        resources = []

        # CPU temperature
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read()) / 1000
                resources.append(f"CPU Temperature: {temp:.1f}°C")
        except Exception:
            resources.append("CPU Temperature: N/A")

        # Memory
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
                total = int([l for l in lines if 'MemTotal' in l][0].split()[1]) / 1024
                avail = int([l for l in lines if 'MemAvailable' in l][0].split()[1]) / 1024
                used_pct = (1 - avail/total) * 100
                resources.append(f"Memory: {used_pct:.0f}% used ({avail:.0f}/{total:.0f} MB)")
        except Exception:
            resources.append("Memory: N/A")

        # Disk
        try:
            result = subprocess.run(
                ['df', '-h', '/'],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                resources.append(f"Disk: {parts[4]} used ({parts[2]}/{parts[1]})")
        except Exception:
            resources.append("Disk: N/A")

        # Uptime
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_secs = float(f.read().split()[0])
                days = int(uptime_secs // 86400)
                hours = int((uptime_secs % 86400) // 3600)
                resources.append(f"Uptime: {days}d {hours}h")
        except Exception:
            resources.append("Uptime: N/A")

        self.dialog.msgbox("System Resources", "\n".join(resources))

    # =========================================================================
    # Network Tools
    # =========================================================================

    def _network_tools_menu(self):
        """Network tools menu."""
        while True:
            choices = [
                ("ping", "Ping Test"),
                ("ports", "Port Scanner"),
                ("mesh", "Meshtastic Discovery"),
                ("ifaces", "Network Interfaces"),
                ("routes", "Routing Table"),
                ("conns", "Active Connections"),
                ("dns", "DNS Lookup"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Network Tools",
                "Network diagnostics and testing:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "ping":
                self._ping_test()
            elif choice == "ports":
                self._port_scan()
            elif choice == "mesh":
                self._meshtastic_discovery()
            elif choice == "ifaces":
                self._show_interfaces()
            elif choice == "routes":
                self._show_routes()
            elif choice == "conns":
                self._show_connections()
            elif choice == "dns":
                self._dns_lookup()

    def _ping_test(self):
        """Run ping test."""
        host = self.dialog.inputbox(
            "Ping Test",
            "Enter host to ping:",
            "8.8.8.8"
        )

        if not host:
            return

        self.dialog.infobox("Pinging", f"Pinging {host}...")

        try:
            result = subprocess.run(
                ['ping', '-c', '4', host],
                capture_output=True, text=True, timeout=15
            )

            # Parse results
            output = result.stdout
            if 'transmitted' in output:
                stats_line = [l for l in output.split('\n') if 'transmitted' in l]
                time_line = [l for l in output.split('\n') if 'rtt' in l or 'round-trip' in l]

                text = f"Ping {host}:\n\n"
                if stats_line:
                    text += stats_line[0] + "\n"
                if time_line:
                    text += time_line[0]

                self.dialog.msgbox("Ping Results", text)
            else:
                self.dialog.msgbox("Ping Failed", output[:500])

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Ping timed out")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _port_scan(self):
        """Scan common ports."""
        host = self.dialog.inputbox(
            "Port Scanner",
            "Enter host to scan:",
            "localhost"
        )

        if not host:
            return

        self.dialog.infobox("Scanning", f"Scanning ports on {host}...")

        import socket
        common_ports = [
            (22, "SSH"),
            (80, "HTTP"),
            (443, "HTTPS"),
            (4403, "Meshtasticd"),
            (5000, "Flask/Web"),
            (8080, "HamClock"),
            (8082, "HamClock API"),
            (9443, "Meshtastic Web"),
        ]

        results = []
        for port, name in common_ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((host, port))
                sock.close()
                status = "OPEN" if result == 0 else "closed"
                results.append(f"{port:5d} {name:15s} {status}")
            except Exception:
                results.append(f"{port:5d} {name:15s} error")

        self.dialog.msgbox(f"Port Scan: {host}", "\n".join(results))

    def _meshtastic_discovery(self):
        """Discover Meshtastic devices."""
        self.dialog.infobox("Discovery", "Scanning for Meshtastic devices...")

        devices = []

        # Check TCP localhost
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            if sock.connect_ex(('localhost', 4403)) == 0:
                devices.append("TCP: localhost:4403 (meshtasticd)")
            sock.close()
        except Exception:
            pass

        # Check serial ports
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        for port in serial_ports:
            devices.append(f"Serial: {port}")

        # BLE hint
        devices.append("")
        devices.append("BLE devices require scanning:")
        devices.append("  meshtastic --ble-scan")

        if not devices:
            text = "No Meshtastic devices found.\n\nMake sure meshtasticd is running."
        else:
            text = "Found devices:\n\n" + "\n".join(devices)

        self.dialog.msgbox("Meshtastic Discovery", text)

    def _show_interfaces(self):
        """Show network interfaces."""
        try:
            result = subprocess.run(
                ['ip', '-br', 'addr'],
                capture_output=True, text=True, timeout=5
            )
            self.dialog.msgbox("Network Interfaces", result.stdout or "No interfaces found")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _show_routes(self):
        """Show routing table."""
        try:
            result = subprocess.run(
                ['ip', 'route'],
                capture_output=True, text=True, timeout=5
            )
            self.dialog.msgbox("Routing Table", result.stdout or "No routes found")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _show_connections(self):
        """Show active connections."""
        try:
            result = subprocess.run(
                ['ss', '-tuln'],
                capture_output=True, text=True, timeout=5
            )
            # Truncate for display
            output = result.stdout[:1500] if result.stdout else "No connections"
            self.dialog.msgbox("Active Connections", output)
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _dns_lookup(self):
        """Perform DNS lookup."""
        host = self.dialog.inputbox(
            "DNS Lookup",
            "Enter hostname to lookup:",
            "meshtastic.org"
        )

        if not host:
            return

        try:
            import socket
            results = []
            for info in socket.getaddrinfo(host, None):
                addr = info[4][0]
                if addr not in [r.split(': ')[1] for r in results if ': ' in r]:
                    family = "IPv4" if info[0] == socket.AF_INET else "IPv6"
                    results.append(f"{family}: {addr}")

            self.dialog.msgbox(f"DNS: {host}", "\n".join(results) or "No results")
        except socket.gaierror as e:
            self.dialog.msgbox("Error", f"DNS lookup failed:\n{e}")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    # =========================================================================
    # Site Planner
    # =========================================================================

    def _site_planner_menu(self):
        """Site planner menu for RF coverage planning."""
        while True:
            choices = [
                ("link", "Link Budget Calculator"),
                ("range", "Range Estimator"),
                ("presets", "LoRa Preset Comparison"),
                ("fresnel", "Fresnel Zone Calculator"),
                ("antenna", "Antenna Guidelines"),
                ("freq", "Frequency Reference"),
                ("tools", "External Planning Tools"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Site Planner",
                "RF coverage and link planning:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "link":
                self._calc_link_budget()  # Reuse existing
            elif choice == "range":
                self._estimate_range()
            elif choice == "presets":
                self._compare_presets()
            elif choice == "fresnel":
                self._calc_fresnel()  # Reuse existing
            elif choice == "antenna":
                self._antenna_guidelines()
            elif choice == "freq":
                self._frequency_reference()
            elif choice == "tools":
                self._external_tools()

    def _estimate_range(self):
        """Estimate communication range based on parameters."""
        # Get TX power
        tx_pwr = self.dialog.inputbox("Range Estimator", "TX Power (dBm):", "20")
        if not tx_pwr:
            return

        # Get antenna gains
        ant_gain = self.dialog.inputbox("Range Estimator", "Total Antenna Gain (dBi):", "4")
        if not ant_gain:
            return

        # Get preset
        presets = [
            ("SHORT_TURBO", "-105 dBm sensitivity"),
            ("SHORT_FAST", "-110 dBm sensitivity"),
            ("MEDIUM_FAST", "-120 dBm sensitivity"),
            ("LONG_FAST", "-125 dBm sensitivity"),
            ("LONG_SLOW", "-132 dBm sensitivity"),
        ]

        preset = self.dialog.menu(
            "Select Preset",
            "Choose LoRa modem preset:",
            presets
        )

        if not preset:
            return

        # Sensitivity values
        sens_map = {
            "SHORT_TURBO": -105,
            "SHORT_FAST": -110,
            "MEDIUM_FAST": -120,
            "LONG_FAST": -125,
            "LONG_SLOW": -132,
        }

        try:
            import math
            tx_p = float(tx_pwr)
            ant_g = float(ant_gain)
            sens = sens_map.get(preset, -125)

            # Link budget
            link_budget = tx_p + ant_g - sens

            # Estimate range using FSPL formula (915 MHz)
            # FSPL = 20*log10(d) + 20*log10(f) + 32.45
            # d = 10^((FSPL - 20*log10(f) - 32.45) / 20)
            freq_mhz = 915
            max_fspl = link_budget
            range_km = 10 ** ((max_fspl - 20 * math.log10(freq_mhz) - 32.45) / 20)

            # Apply terrain factor (0.3-0.7 of theoretical)
            los_range = range_km
            urban_range = range_km * 0.3
            rural_range = range_km * 0.5

            text = f"""Range Estimation:

Preset: {preset}
TX Power: {tx_p} dBm
Antenna Gain: {ant_g} dBi
RX Sensitivity: {sens} dBm

Link Budget: {link_budget:.1f} dB

Estimated Range (915 MHz):
  Line of Sight: {los_range:.1f} km
  Rural/Suburban: {rural_range:.1f} km
  Urban/Dense: {urban_range:.1f} km

Note: Actual range depends on terrain,
vegetation, and antenna height."""

            self.dialog.msgbox("Range Estimation", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")

    def _compare_presets(self):
        """Compare LoRa modem presets."""
        text = """LoRa Modem Preset Comparison:

Preset          BW    SF  Range     Speed
──────────────────────────────────────────
SHORT_TURBO    500   7   <1 km     Fastest
SHORT_FAST     250   7   1-5 km    Fast
SHORT_SLOW     125   7   1-5 km    Medium
MEDIUM_FAST    250   10  5-20 km   Medium
MEDIUM_SLOW    125   10  5-20 km   Slower
LONG_FAST      250   11  10-30 km  Default
LONG_MODERATE  125   11  15-40 km  Slower
LONG_SLOW      125   12  20-50 km  Slowest

Higher SF = Longer range, slower speed
Lower BW = Better sensitivity, slower speed

Recommended:
  Gateway: SHORT_TURBO or MEDIUM_FAST
  Rural: LONG_FAST or LONG_MODERATE
  Urban: SHORT_FAST or MEDIUM_FAST"""

        self.dialog.msgbox("Preset Comparison", text)

    def _antenna_guidelines(self):
        """Show antenna guidelines."""
        text = """Antenna Guidelines for 915 MHz:

Height:
  - Higher is better for range
  - 10m height doubles range vs 2m
  - Avoid below tree canopy

Antenna Types:
  - Dipole: 2.15 dBi, omnidirectional
  - 1/4 wave GP: 2-3 dBi, omnidirectional
  - Yagi: 6-12 dBi, directional
  - Colinear: 5-9 dBi, omnidirectional

Cable Loss (per 10m @ 915MHz):
  - RG58: ~2.5 dB (avoid)
  - RG8X: ~1.8 dB
  - LMR-240: ~1.3 dB
  - LMR-400: ~0.7 dB (recommended)

Best Practices:
  - Mount antenna clear of obstructions
  - Use quality coax, keep runs short
  - Ground antenna mast for lightning
  - Weatherproof all connections"""

        self.dialog.msgbox("Antenna Guidelines", text)

    def _frequency_reference(self):
        """Show frequency reference."""
        text = """LoRa Frequency Reference:

Region      Frequencies       TX Power
───────────────────────────────────────
US/FCC      902-928 MHz       30 dBm
EU 868      863-870 MHz       14-27 dBm
EU 433      433.05-434.79     10 dBm
UK          868 MHz           25 dBm
AU/NZ       915-928 MHz       30 dBm
AS          920-923 MHz       varies
CN          470-510 MHz       17 dBm
JP          920-923 MHz       13 dBm

Default Meshtastic Frequencies:
  US: 906.875 MHz (Ch 0)
  EU 868: 869.525 MHz
  EU 433: 433.175 MHz

ISM Band Limits (US):
  EIRP: 36 dBm (4W) max
  Duty Cycle: No limit (FHSS)"""

        self.dialog.msgbox("Frequency Reference", text)

    def _external_tools(self):
        """Show external planning tools."""
        text = """External RF Planning Tools:

Web-Based:
  meshtastic.org/docs/software/coverage/
    - Meshtastic Site Planner
    - Coverage prediction

  heywhatsthat.com
    - Line of sight analysis
    - Terrain profiles

  splat.ecso.org
    - Detailed RF coverage
    - Terrain analysis

Software:
  Radio Mobile (Windows/Wine)
    - Professional RF planning
    - Free for amateur use

  SPLAT! (Linux)
    - RF Signal Propagation
    - Terrain analysis

  CloudRF
    - Cloud-based planning
    - API available

Tip: Use these tools to plan
repeater locations and verify
line-of-sight paths."""

        self.dialog.msgbox("External Planning Tools", text)

    def _run_bridge(self):
        """Start gateway bridge."""
        if self.dialog.yesno(
            "Gateway Bridge",
            "Start the RNS ↔ Meshtastic gateway bridge?\n\n"
            "This will bridge messages between Reticulum and Meshtastic networks.",
            default_no=True
        ):
            subprocess.run(['clear'], check=False)
            print("Starting Gateway Bridge...")
            print("Press Ctrl+C to stop\n")
            try:
                subprocess.run([sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')])
            except KeyboardInterrupt:
                print("\nBridge stopped.")
            input("\nPress Enter to continue...")

    def _run_monitor(self):
        """Run node monitor."""
        subprocess.run(['clear'], check=False)
        try:
            subprocess.run([sys.executable, str(self.src_dir / 'monitor.py')])
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
        input("\nPress Enter to continue...")

    def _show_space_weather(self):
        """Show space weather (uses HamClock if available, else NOAA)."""
        self.dialog.infobox("Space Weather", "Fetching space weather data...")

        try:
            # Use the commands layer with auto-fallback
            sys.path.insert(0, str(self.src_dir))
            from commands import hamclock

            # Auto-fallback: tries HamClock first, then NOAA
            result = hamclock.get_propagation_summary()

            if result.success:
                data = result.data
                source = data.get('source', 'Unknown')

                # Build display text
                lines = [
                    f"Solar Flux Index (SFI): {data.get('sfi', 'N/A')}",
                    f"Kp Index: {data.get('kp', 'N/A')}",
                    f"X-Ray Flux: {data.get('xray', 'N/A')}",
                    f"Sunspot Number: {data.get('ssn', 'N/A')}",
                    f"Geomagnetic: {data.get('geomagnetic', 'N/A')}",
                    "",
                    f"Overall Conditions: {data.get('overall', 'Unknown')}",
                ]

                # Add band conditions if available
                bands = data.get('hf_conditions', {})
                if bands:
                    lines.append("")
                    lines.append("HF Band Conditions:")
                    for band, cond in bands.items():
                        lines.append(f"  {band}: {cond}")

                # Add alerts if any
                alerts = data.get('alerts', [])
                if alerts:
                    lines.append("")
                    lines.append("Active Alerts:")
                    for alert in alerts[:2]:
                        msg = alert.get('message', '')[:60]
                        lines.append(f"  - {msg}...")

                lines.append("")
                lines.append(f"Source: {source}")

                text = "\n".join(lines)
            else:
                text = f"Could not retrieve space weather data.\n\nError: {result.message}"

            self.dialog.msgbox("Space Weather", text)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get space weather:\n{e}")

    def _service_menu(self):
        """Service management menu."""
        while True:
            choices = [
                ("status", "View Service Status"),
                ("meshtasticd", "Manage meshtasticd"),
                ("rnsd", "Manage rnsd"),
                ("hamclock", "Manage HamClock"),
                ("back", "Back to Main Menu"),
            ]

            choice = self.dialog.menu(
                "Service Management",
                "Manage system services:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._show_service_status()
            else:
                self._manage_service(choice)

    def _show_service_status(self):
        """Show status of all services."""
        self.dialog.infobox("Services", "Checking service status...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import service

            result = service.list_all()
            if result.success:
                services = result.data.get('services', {})
                lines = []
                for name, info in services.items():
                    status = "RUNNING" if info.get('running') else "STOPPED"
                    enabled = "enabled" if info.get('enabled') else "disabled"
                    lines.append(f"{name}: {status} ({enabled})")

                text = "\n".join(lines) if lines else "No services configured"
            else:
                text = f"Error: {result.message}"

            self.dialog.msgbox("Service Status", text)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get service status:\n{e}")

    def _manage_service(self, service_name: str):
        """Manage a specific service."""
        choices = [
            ("status", "Check Status"),
            ("start", "Start Service"),
            ("stop", "Stop Service"),
            ("restart", "Restart Service"),
            ("logs", "View Logs"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                f"Manage {service_name}",
                f"Select action for {service_name}:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._service_action(service_name, choice)

    def _service_action(self, service_name: str, action: str):
        """Perform service action."""
        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import service

            if action == "status":
                result = service.check_status(service_name)
                text = f"Service: {service_name}\n"
                text += f"Running: {'Yes' if result.data.get('running') else 'No'}\n"
                text += f"Enabled: {'Yes' if result.data.get('enabled') else 'No'}\n"
                text += f"Status: {result.data.get('status', 'Unknown')}"
                self.dialog.msgbox(f"{service_name} Status", text)

            elif action == "start":
                self.dialog.infobox(service_name, f"Starting {service_name}...")
                result = service.start(service_name)
                self.dialog.msgbox("Result", result.message)

            elif action == "stop":
                if self.dialog.yesno("Confirm", f"Stop {service_name}?", default_no=True):
                    self.dialog.infobox(service_name, f"Stopping {service_name}...")
                    result = service.stop(service_name)
                    self.dialog.msgbox("Result", result.message)

            elif action == "restart":
                self.dialog.infobox(service_name, f"Restarting {service_name}...")
                result = service.restart(service_name)
                self.dialog.msgbox("Result", result.message)

            elif action == "logs":
                result = service.get_logs(service_name, lines=20)
                logs = result.data.get('logs', 'No logs available')
                # Truncate for display
                if len(logs) > 2000:
                    logs = logs[-2000:] + "\n...(truncated)"
                self.dialog.msgbox(f"{service_name} Logs", logs)

        except Exception as e:
            self.dialog.msgbox("Error", f"Action failed:\n{e}")

    def _hardware_menu(self):
        """Hardware detection menu."""
        self.dialog.infobox("Hardware", "Detecting hardware...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import hardware

            result = hardware.detect_devices()
            if result.success:
                data = result.data

                text = "=== Hardware Detection ===\n\n"

                # SPI
                spi = data.get('spi', {})
                text += f"SPI: {'Enabled' if spi.get('enabled') else 'Disabled'}\n"
                if spi.get('devices'):
                    text += f"  Devices: {', '.join(spi.get('devices', []))}\n"

                # I2C
                i2c = data.get('i2c', {})
                text += f"\nI2C: {'Enabled' if i2c.get('enabled') else 'Disabled'}\n"
                if i2c.get('devices'):
                    text += f"  Devices: {len(i2c.get('devices', []))} found\n"

                # Serial
                serial = data.get('serial', {})
                ports = serial.get('ports', [])
                text += f"\nSerial Ports: {len(ports)} found\n"
                for port in ports[:5]:  # Limit to 5
                    text += f"  - {port.get('device', 'Unknown')}\n"

                # Summary
                summary = data.get('summary', '')
                if summary:
                    text += f"\n{summary}"

                self.dialog.msgbox("Hardware Detection", text)
            else:
                self.dialog.msgbox("Error", f"Detection failed:\n{result.message}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Hardware detection failed:\n{e}")

    def _settings_menu(self):
        """Settings menu."""
        choices = [
            ("connection", "Meshtastic Connection"),
            ("gateway", "Gateway Settings"),
            ("hamclock", "HamClock Settings"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Settings",
                "Configure MeshForge:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "connection":
                self._configure_connection()
            elif choice == "gateway":
                self._configure_gateway()
            elif choice == "hamclock":
                self._configure_hamclock()

    def _configure_connection(self):
        """Configure Meshtastic connection."""
        choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
        ]

        choice = self.dialog.menu(
            "Meshtastic Connection",
            "Select connection type:",
            choices
        )

        if choice == "localhost":
            self.dialog.msgbox("Connection", "Connection set to localhost:4403")
        elif choice == "serial":
            port = self.dialog.inputbox("Serial Port", "Enter serial port:", "/dev/ttyUSB0")
            if port:
                self.dialog.msgbox("Connection", f"Connection set to {port}")
        elif choice == "remote":
            host = self.dialog.inputbox("Remote Host", "Enter host:port:", "192.168.1.100:4403")
            if host:
                self.dialog.msgbox("Connection", f"Connection set to {host}")

    def _configure_gateway(self):
        """Configure gateway settings."""
        self.dialog.msgbox(
            "Gateway Settings",
            "Gateway configuration is available in the full CLI.\n\n"
            "Run 'meshforge' and select Gateway from the menu."
        )

    def _configure_hamclock(self):
        """Configure HamClock settings."""
        host = self.dialog.inputbox(
            "HamClock Host",
            "Enter HamClock hostname or IP:",
            "localhost"
        )

        if host:
            port = self.dialog.inputbox(
                "HamClock API Port",
                "Enter API port (default 8082):",
                "8082"
            )

            if port:
                try:
                    sys.path.insert(0, str(self.src_dir))
                    from commands import hamclock
                    result = hamclock.configure(host, api_port=int(port))
                    self.dialog.msgbox("Result", result.message)
                except Exception as e:
                    self.dialog.msgbox("Error", f"Configuration failed:\n{e}")

    def _show_about(self):
        """Show about information."""
        text = f"""MeshForge v{__version__}
Network Operations Center

Bridges Meshtastic and Reticulum (RNS) mesh networks.

Features:
- Service management
- Hardware detection
- Space weather & propagation
- Gateway bridge (Mesh ↔ RNS)
- Node monitoring

GitHub: github.com/Nursedude/meshforge
License: MIT

Made with aloha for the mesh community
73 de WH6GXZ"""

        self.dialog.msgbox("About MeshForge", text)

    def _show_nodes(self):
        """Show connected nodes."""
        self.dialog.infobox("Nodes", "Fetching node list...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd

            result = mesh_cmd.get_nodes()
            if result.success:
                nodes = result.data.get('nodes', [])
                if not nodes:
                    self.dialog.msgbox("Nodes", "No nodes found.\n\nMake sure meshtasticd is running.")
                    return

                text = f"Found {len(nodes)} nodes:\n\n"
                for node in nodes[:15]:  # Limit display
                    node_id = node.get('id', '?')
                    name = node.get('name', 'Unknown')
                    snr = node.get('snr', 'N/A')
                    last_heard = node.get('last_heard', 'N/A')
                    text += f"  {name} ({node_id})\n"
                    text += f"    SNR: {snr} | Last: {last_heard}\n"

                if len(nodes) > 15:
                    text += f"\n... and {len(nodes) - 15} more"

                self.dialog.msgbox("Mesh Nodes", text)
            else:
                self.dialog.msgbox("Error", f"Failed to get nodes:\n{result.message}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Node fetch failed:\n{e}")

    def _messaging_menu(self):
        """Messaging menu."""
        choices = [
            ("view", "View Recent Messages"),
            ("send", "Send Message"),
            ("stats", "Message Statistics"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Messaging",
                "Mesh messaging:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "view":
                self._view_messages()
            elif choice == "send":
                self._send_message()
            elif choice == "stats":
                self._message_stats()

    def _view_messages(self):
        """View recent messages."""
        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import messaging

            result = messaging.get_messages(limit=20)
            if result.success:
                messages = result.data.get('messages', [])
                if not messages:
                    self.dialog.msgbox("Messages", "No messages yet.")
                    return

                text = ""
                for msg in messages[:10]:
                    ts = msg.get('timestamp', '')[:16]
                    from_id = msg.get('from_id', '?')
                    content = msg.get('content', '')[:40]
                    text += f"[{ts}] {from_id}\n  {content}\n\n"

                self.dialog.msgbox("Recent Messages", text)
            else:
                self.dialog.msgbox("Error", result.message)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to load messages:\n{e}")

    def _send_message(self):
        """Send a message."""
        try:
            # Get destination
            dest = self.dialog.inputbox(
                "Send Message",
                "Destination (node ID or leave empty for broadcast):",
                ""
            )

            if dest is None:
                return

            # Get message content
            content = self.dialog.inputbox(
                "Send Message",
                "Message (max 160 chars):",
                ""
            )

            if not content:
                return

            sys.path.insert(0, str(self.src_dir))
            from commands import messaging

            self.dialog.infobox("Sending", "Sending message...")
            result = messaging.send_message(
                content=content,
                destination=dest if dest else None,
                network="auto"
            )

            self.dialog.msgbox("Result", result.message)

        except Exception as e:
            self.dialog.msgbox("Error", f"Send failed:\n{e}")

    def _message_stats(self):
        """Show message statistics."""
        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import messaging

            result = messaging.get_stats()
            if result.success:
                data = result.data
                text = f"""Message Statistics:

Total Messages: {data.get('total', 0)}
Sent: {data.get('sent', 0)}
Received: {data.get('received', 0)}
Last 24h: {data.get('last_24h', 0)}

Storage: messages.db"""

                self.dialog.msgbox("Statistics", text)
            else:
                self.dialog.msgbox("Error", result.message)

        except Exception as e:
            self.dialog.msgbox("Error", f"Stats failed:\n{e}")

    def _rf_tools_menu(self):
        """RF tools menu."""
        choices = [
            ("fspl", "Free Space Path Loss"),
            ("link", "Link Budget Calculator"),
            ("fresnel", "Fresnel Zone"),
            ("power", "EIRP Calculator"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "RF Tools",
                "Radio frequency calculations:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "fspl":
                self._calc_fspl()
            elif choice == "link":
                self._calc_link_budget()
            elif choice == "fresnel":
                self._calc_fresnel()
            elif choice == "power":
                self._calc_eirp()

    def _calc_fspl(self):
        """Calculate Free Space Path Loss."""
        try:
            # Get distance
            dist_str = self.dialog.inputbox(
                "FSPL Calculator",
                "Distance (km):",
                "1"
            )
            if not dist_str:
                return

            # Get frequency
            freq_str = self.dialog.inputbox(
                "FSPL Calculator",
                "Frequency (MHz):",
                "915"
            )
            if not freq_str:
                return

            distance = float(dist_str)
            freq = float(freq_str)

            # FSPL formula: 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
            # Simplified: FSPL(dB) = 20*log10(d_km) + 20*log10(f_MHz) + 32.45
            import math
            fspl = 20 * math.log10(distance) + 20 * math.log10(freq) + 32.45

            text = f"""Free Space Path Loss:

Distance: {distance} km
Frequency: {freq} MHz

FSPL: {fspl:.1f} dB

Note: This is theoretical minimum loss.
Actual loss will be higher due to
terrain, vegetation, and atmospheric
conditions."""

            self.dialog.msgbox("FSPL Result", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _calc_link_budget(self):
        """Calculate link budget."""
        try:
            # Get TX power
            tx_pwr = self.dialog.inputbox("Link Budget", "TX Power (dBm):", "20")
            if not tx_pwr:
                return

            # Get TX antenna gain
            tx_gain = self.dialog.inputbox("Link Budget", "TX Antenna Gain (dBi):", "2")
            if not tx_gain:
                return

            # Get RX antenna gain
            rx_gain = self.dialog.inputbox("Link Budget", "RX Antenna Gain (dBi):", "2")
            if not rx_gain:
                return

            # Get path loss
            path_loss = self.dialog.inputbox("Link Budget", "Path Loss (dB):", "100")
            if not path_loss:
                return

            # Get RX sensitivity
            rx_sens = self.dialog.inputbox("Link Budget", "RX Sensitivity (dBm):", "-130")
            if not rx_sens:
                return

            tx_p = float(tx_pwr)
            tx_g = float(tx_gain)
            rx_g = float(rx_gain)
            pl = float(path_loss)
            rx_s = float(rx_sens)

            # Link budget: RX Power = TX Power + TX Gain + RX Gain - Path Loss
            rx_power = tx_p + tx_g + rx_g - pl
            link_margin = rx_power - rx_s

            status = "GOOD" if link_margin > 10 else "MARGINAL" if link_margin > 0 else "NO LINK"

            text = f"""Link Budget Analysis:

TX Power: {tx_p} dBm
TX Antenna: +{tx_g} dBi
RX Antenna: +{rx_g} dBi
Path Loss: -{pl} dB
RX Sensitivity: {rx_s} dBm

Received Power: {rx_power:.1f} dBm
Link Margin: {link_margin:.1f} dB

Status: {status}"""

            self.dialog.msgbox("Link Budget", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _calc_fresnel(self):
        """Calculate Fresnel zone."""
        try:
            dist_str = self.dialog.inputbox("Fresnel Zone", "Distance (km):", "5")
            if not dist_str:
                return

            freq_str = self.dialog.inputbox("Fresnel Zone", "Frequency (MHz):", "915")
            if not freq_str:
                return

            distance = float(dist_str) * 1000  # Convert to meters
            freq = float(freq_str) * 1e6  # Convert to Hz

            # Fresnel zone radius at midpoint
            # r = sqrt(n * wavelength * d1 * d2 / (d1 + d2))
            # At midpoint: r = sqrt(n * lambda * d / 4)
            c = 3e8
            wavelength = c / freq
            d1 = d2 = distance / 2

            import math
            r1 = math.sqrt(wavelength * d1 * d2 / (d1 + d2))

            # 60% clearance recommendation
            clearance = r1 * 0.6

            text = f"""Fresnel Zone Calculator:

Distance: {distance/1000:.1f} km
Frequency: {freq/1e6:.0f} MHz
Wavelength: {wavelength:.3f} m

1st Fresnel Zone Radius: {r1:.1f} m
60% Clearance Needed: {clearance:.1f} m

For best signal, ensure no obstacles
within {clearance:.1f}m of the line
of sight at the midpoint."""

            self.dialog.msgbox("Fresnel Zone", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _calc_eirp(self):
        """Calculate EIRP."""
        try:
            tx_pwr = self.dialog.inputbox("EIRP", "TX Power (dBm):", "20")
            if not tx_pwr:
                return

            cable_loss = self.dialog.inputbox("EIRP", "Cable Loss (dB):", "1")
            if not cable_loss:
                return

            ant_gain = self.dialog.inputbox("EIRP", "Antenna Gain (dBi):", "6")
            if not ant_gain:
                return

            tx = float(tx_pwr)
            loss = float(cable_loss)
            gain = float(ant_gain)

            eirp = tx - loss + gain

            # Convert to watts
            import math
            eirp_watts = 10 ** ((eirp - 30) / 10)

            # FCC limit for 915MHz ISM is 36dBm EIRP (4W) for frequency hopping
            legal = "LEGAL (under 36 dBm)" if eirp <= 36 else "EXCEEDS FCC LIMIT"

            text = f"""EIRP Calculator:

TX Power: {tx} dBm
Cable Loss: -{loss} dB
Antenna Gain: +{gain} dBi

EIRP: {eirp:.1f} dBm ({eirp_watts*1000:.0f} mW)

US 915MHz ISM: {legal}

Note: Check local regulations."""

            self.dialog.msgbox("EIRP Result", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    # =========================================================================
    # Meshtasticd Configuration
    # =========================================================================

    def _meshtasticd_menu(self):
        """Meshtasticd configuration menu."""
        while True:
            choices = [
                ("status", "Service Status"),
                ("presets", "Radio Presets (LoRa)"),
                ("hardware", "Hardware Config"),
                ("channels", "Channel Config"),
                ("gateway", "Gateway Template"),
                ("edit", "Edit Config Files"),
                ("restart", "Restart Service"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Meshtasticd Config",
                "Configure meshtasticd radio daemon:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._meshtasticd_status()
            elif choice == "presets":
                self._radio_presets_menu()
            elif choice == "hardware":
                self._hardware_config_menu()
            elif choice == "channels":
                self._channel_config_menu()
            elif choice == "gateway":
                self._gateway_template_menu()
            elif choice == "edit":
                self._edit_config_menu()
            elif choice == "restart":
                self._restart_meshtasticd()

    def _meshtasticd_status(self):
        """Show meshtasticd service status."""
        self.dialog.infobox("Status", "Checking meshtasticd status...")

        try:
            result = subprocess.run(
                ['systemctl', 'status', 'meshtasticd'],
                capture_output=True,
                text=True,
                timeout=10
            )

            # Parse status
            output = result.stdout
            is_running = "active (running)" in output
            is_enabled = subprocess.run(
                ['systemctl', 'is-enabled', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            ).returncode == 0

            # Get config file info
            config_path = Path('/etc/meshtasticd/config.yaml')
            config_exists = config_path.exists()

            # Check active configs
            config_d = Path('/etc/meshtasticd/config.d')
            active_configs = list(config_d.glob('*.yaml')) if config_d.exists() else []

            text = f"""Meshtasticd Service Status:

Service: {'RUNNING' if is_running else 'STOPPED'}
Enabled: {'Yes' if is_enabled else 'No'}

Config File: {config_path}
Config Exists: {'Yes' if config_exists else 'No'}

Active Hardware Configs: {len(active_configs)}"""

            for cfg in active_configs[:5]:
                text += f"\n  - {cfg.name}"

            if len(active_configs) > 5:
                text += f"\n  ... and {len(active_configs) - 5} more"

            self.dialog.msgbox("Meshtasticd Status", text)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get status:\n{e}")

    def _radio_presets_menu(self):
        """Radio/LoRa preset selection."""
        # Define modem presets with descriptions
        presets = [
            ("SHORT_TURBO", "500kHz SF7  - Max speed, <1km"),
            ("SHORT_FAST", "250kHz SF7  - Urban, 1-5km"),
            ("SHORT_SLOW", "125kHz SF7  - Reliable short"),
            ("MEDIUM_FAST", "250kHz SF10 - MtnMesh std, 5-20km"),
            ("MEDIUM_SLOW", "125kHz SF10 - Alt medium"),
            ("LONG_FAST", "250kHz SF11 - Default, 10-30km"),
            ("LONG_MODERATE", "125kHz SF11 - Extended, 15-40km"),
            ("LONG_SLOW", "125kHz SF12 - Max range, 20-50km"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Radio Presets",
            "Select LoRa modem preset:\n\n"
            "Higher speed = shorter range\n"
            "Lower speed = longer range",
            presets
        )

        if choice and choice != "back":
            self._apply_radio_preset(choice)

    def _apply_radio_preset(self, preset: str):
        """Apply a radio preset."""
        # Preset parameters
        preset_params = {
            "SHORT_TURBO": {"bw": 500, "sf": 7, "cr": 5},
            "SHORT_FAST": {"bw": 250, "sf": 7, "cr": 5},
            "SHORT_SLOW": {"bw": 125, "sf": 7, "cr": 8},
            "MEDIUM_FAST": {"bw": 250, "sf": 10, "cr": 5},
            "MEDIUM_SLOW": {"bw": 125, "sf": 10, "cr": 5},
            "LONG_FAST": {"bw": 250, "sf": 11, "cr": 5},
            "LONG_MODERATE": {"bw": 125, "sf": 11, "cr": 8},
            "LONG_SLOW": {"bw": 125, "sf": 12, "cr": 8},
        }

        params = preset_params.get(preset, {})
        if not params:
            return

        confirm = self.dialog.yesno(
            "Apply Preset",
            f"Apply {preset} preset?\n\n"
            f"Bandwidth: {params['bw']} kHz\n"
            f"Spreading Factor: SF{params['sf']}\n"
            f"Coding Rate: 4/{params['cr']}\n\n"
            "This will modify /etc/meshtasticd/config.yaml\n"
            "and restart the service.",
            default_no=True
        )

        if not confirm:
            return

        self.dialog.infobox("Applying", f"Applying {preset} preset...")

        try:
            config_path = Path('/etc/meshtasticd/config.yaml')

            if not config_path.exists():
                self.dialog.msgbox("Error", "Config file not found.\nRun installer first.")
                return

            # Read and modify config
            import yaml
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}

            if 'Lora' not in config:
                config['Lora'] = {}

            config['Lora']['Bandwidth'] = params['bw']
            config['Lora']['SpreadFactor'] = params['sf']
            config['Lora']['CodingRate'] = params['cr']

            # Backup and write
            backup_path = config_path.with_suffix('.yaml.bak')
            if config_path.exists():
                import shutil
                shutil.copy(config_path, backup_path)

            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)

            # Restart service
            subprocess.run(['systemctl', 'restart', 'meshtasticd'],
                           capture_output=True, timeout=30)

            self.dialog.msgbox("Success",
                f"{preset} preset applied!\n\n"
                f"Config: {config_path}\n"
                f"Backup: {backup_path}\n\n"
                "Service restarted.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to apply preset:\n{e}")

    def _hardware_config_menu(self):
        """Hardware configuration selection."""
        available_dir = Path('/etc/meshtasticd/available.d')
        config_d = Path('/etc/meshtasticd/config.d')

        if not available_dir.exists():
            self.dialog.msgbox("Error",
                "Hardware templates not found.\n\n"
                f"Expected: {available_dir}\n\n"
                "Run the installer to set up templates.")
            return

        # List available hardware configs
        available = list(available_dir.glob('*.yaml'))
        if not available:
            self.dialog.msgbox("Error", "No hardware templates found.")
            return

        # Get currently active configs
        active = set()
        if config_d.exists():
            active = {f.name for f in config_d.glob('*.yaml')}

        choices = []
        for cfg in sorted(available):
            status = "[ACTIVE]" if cfg.name in active else ""
            # Truncate name for display
            name = cfg.stem[:25]
            choices.append((cfg.name, f"{name} {status}"))

        choices.append(("view", "View Config Details"))
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "Hardware Config",
            "Select hardware configuration to activate:\n\n"
            f"Templates: {available_dir}\n"
            f"Active: {config_d}",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice == "view":
            self._view_hardware_config(available)
        else:
            self._activate_hardware_config(choice, available_dir, config_d)

    def _activate_hardware_config(self, config_name: str, available_dir: Path, config_d: Path):
        """Activate a hardware configuration."""
        src = available_dir / config_name

        if not src.exists():
            self.dialog.msgbox("Error", f"Config not found: {src}")
            return

        confirm = self.dialog.yesno(
            "Activate Config",
            f"Activate hardware config?\n\n"
            f"Template: {config_name}\n\n"
            "This will:\n"
            f"1. Copy to {config_d}/\n"
            "2. Restart meshtasticd service",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.dialog.infobox("Activating", f"Activating {config_name}...")

            # Create config.d if needed
            config_d.mkdir(parents=True, exist_ok=True)

            # Copy config
            import shutil
            dst = config_d / config_name
            shutil.copy(src, dst)

            # Restart service
            subprocess.run(['systemctl', 'daemon-reload'],
                           capture_output=True, timeout=10)
            subprocess.run(['systemctl', 'restart', 'meshtasticd'],
                           capture_output=True, timeout=30)

            self.dialog.msgbox("Success",
                f"Hardware config activated!\n\n"
                f"Config: {dst}\n\n"
                "Service restarted.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Activation failed:\n{e}")

    def _view_hardware_config(self, configs: list):
        """View details of a hardware config."""
        choices = [(cfg.name, cfg.stem[:30]) for cfg in sorted(configs)]
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "View Config",
            "Select config to view:",
            choices
        )

        if choice and choice != "back":
            config_path = Path('/etc/meshtasticd/available.d') / choice
            if config_path.exists():
                try:
                    content = config_path.read_text()[:1500]
                    self.dialog.msgbox(f"Config: {choice}", content)
                except Exception as e:
                    self.dialog.msgbox("Error", str(e))

    def _channel_config_menu(self):
        """Channel configuration menu."""
        choices = [
            ("view", "View Current Channels"),
            ("primary", "Set Primary Channel"),
            ("gateway", "Set Gateway Channel (Slot 8)"),
            ("psk", "Generate New PSK"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Channel Config",
                "Configure mesh channels:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "view":
                self._view_channels()
            elif choice == "primary":
                self._set_primary_channel()
            elif choice == "gateway":
                self._set_gateway_channel()
            elif choice == "psk":
                self._generate_psk()

    def _view_channels(self):
        """View current channel configuration."""
        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd

            result = mesh_cmd.get_channel_info(0)
            if result.success:
                self.dialog.msgbox("Channel Info", result.raw or "No channel info")
            else:
                self.dialog.msgbox("Error", result.message)
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get channels:\n{e}")

    def _set_primary_channel(self):
        """Set primary channel name."""
        name = self.dialog.inputbox(
            "Primary Channel",
            "Enter channel name (max 12 chars):",
            "MeshForge"
        )

        if not name:
            return

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd

            self.dialog.infobox("Setting", f"Setting channel name to {name}...")
            result = mesh_cmd.set_channel_name(0, name[:12])
            self.dialog.msgbox("Result", result.message)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed:\n{e}")

    def _set_gateway_channel(self):
        """Set up gateway channel on slot 8."""
        confirm = self.dialog.yesno(
            "Gateway Channel",
            "Set up gateway channel on slot 8?\n\n"
            "This is the recommended channel for\n"
            "MeshForge ↔ RNS gateway bridging.\n\n"
            "Channel 8 will be configured as:\n"
            "  Name: Gateway\n"
            "  Role: SECONDARY\n"
            "  PSK: [Generated or custom]",
            default_no=True
        )

        if not confirm:
            return

        # Get PSK choice
        psk_choices = [
            ("random", "Generate Random PSK"),
            ("default", "Use Default PSK (AQ==)"),
            ("custom", "Enter Custom PSK"),
        ]

        psk_choice = self.dialog.menu(
            "Gateway PSK",
            "Select PSK for gateway channel:",
            psk_choices
        )

        if not psk_choice:
            return

        psk = "AQ=="  # Default
        if psk_choice == "random":
            psk = "random"
        elif psk_choice == "custom":
            psk = self.dialog.inputbox("Custom PSK", "Enter PSK (hex or base64):", "")
            if not psk:
                return

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd

            self.dialog.infobox("Setting", "Configuring gateway channel...")

            # Set channel name
            mesh_cmd.set_channel_name(7, "Gateway")  # Index 7 = slot 8

            # Set PSK
            mesh_cmd.set_channel_psk(7, psk)

            self.dialog.msgbox("Success",
                "Gateway channel configured on slot 8!\n\n"
                "Use this channel for gateway bridging.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed:\n{e}")

    def _generate_psk(self):
        """Generate a new PSK."""
        import secrets
        import base64

        psk_bytes = secrets.token_bytes(32)
        psk_b64 = base64.b64encode(psk_bytes).decode()
        psk_hex = psk_bytes.hex()

        self.dialog.msgbox("Generated PSK",
            f"New 256-bit PSK:\n\n"
            f"Base64:\n{psk_b64}\n\n"
            f"Hex:\n{psk_hex[:32]}...\n\n"
            "Copy this PSK and share securely\n"
            "with your mesh network members.")

    def _gateway_template_menu(self):
        """Gateway template configuration."""
        templates = [
            ("standard", "Standard Gateway (Long Fast)"),
            ("turbo", "Turbo Gateway (Short Turbo + Ch8)"),
            ("mtnmesh", "MtnMesh Gateway (Medium Fast)"),
            ("custom", "Custom Gateway Setup"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Gateway Templates",
            "Pre-configured gateway setups:\n\n"
            "Templates configure radio preset,\n"
            "channel 8 for gateway, and optimize\n"
            "for RNS bridging.",
            templates
        )

        if choice and choice != "back":
            self._apply_gateway_template(choice)

    def _apply_gateway_template(self, template: str):
        """Apply a gateway template."""
        templates = {
            "standard": {
                "name": "Standard Gateway",
                "preset": "LONG_FAST",
                "bw": 250, "sf": 11, "cr": 5,
                "channel": "Gateway",
                "description": "Default Meshtastic settings with gateway channel"
            },
            "turbo": {
                "name": "Turbo Gateway",
                "preset": "SHORT_TURBO",
                "bw": 500, "sf": 7, "cr": 5,
                "channel": "GW-Turbo",
                "description": "Maximum speed for local gateway bridging"
            },
            "mtnmesh": {
                "name": "MtnMesh Gateway",
                "preset": "MEDIUM_FAST",
                "bw": 250, "sf": 10, "cr": 5,
                "channel": "MtnMesh-GW",
                "description": "MtnMesh community standard with gateway"
            },
        }

        if template == "custom":
            self.dialog.msgbox("Custom Gateway",
                "For custom gateway setup:\n\n"
                "1. Use Radio Presets to set LoRa params\n"
                "2. Use Channel Config > Gateway Channel\n"
                "3. Edit config files for advanced options")
            return

        tmpl = templates.get(template)
        if not tmpl:
            return

        confirm = self.dialog.yesno(
            tmpl["name"],
            f"Apply {tmpl['name']} template?\n\n"
            f"Preset: {tmpl['preset']}\n"
            f"Bandwidth: {tmpl['bw']} kHz\n"
            f"Spreading Factor: SF{tmpl['sf']}\n"
            f"Gateway Channel: {tmpl['channel']} (Slot 8)\n\n"
            f"{tmpl['description']}\n\n"
            "This will update config and restart service.",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.dialog.infobox("Applying", f"Applying {tmpl['name']}...")

            # Apply radio preset
            self._apply_radio_preset(tmpl['preset'])

            # Set gateway channel (index 7 = slot 8)
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd
            mesh_cmd.set_channel_name(7, tmpl['channel'])

            self.dialog.msgbox("Success",
                f"{tmpl['name']} applied!\n\n"
                f"Radio: {tmpl['preset']}\n"
                f"Gateway Channel: {tmpl['channel']} (Slot 8)\n\n"
                "Ready for RNS bridging.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Template failed:\n{e}")

    def _edit_config_menu(self):
        """Edit config files directly."""
        choices = [
            ("main", "Main Config (/etc/meshtasticd/config.yaml)"),
            ("active", "Active Hardware Configs"),
            ("templates", "Hardware Templates"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Edit Config Files",
            "Edit meshtasticd configuration files:\n\n"
            "Opens in nano editor.\n"
            "Save: Ctrl+O, Exit: Ctrl+X",
            choices
        )

        if choice is None or choice == "back":
            return

        if choice == "main":
            self._edit_file('/etc/meshtasticd/config.yaml')
        elif choice == "active":
            self._edit_config_d()
        elif choice == "templates":
            self._edit_available_d()

    def _edit_file(self, path: str):
        """Edit a file with nano."""
        if not Path(path).exists():
            self.dialog.msgbox("Error", f"File not found:\n{path}")
            return

        # Clear screen and run nano
        subprocess.run(['clear'], check=False)
        subprocess.run(['nano', path])

        # Ask to restart service
        if self.dialog.yesno(
            "Restart Service?",
            "Config file modified.\n\n"
            "Restart meshtasticd to apply changes?",
            default_no=False
        ):
            self._restart_meshtasticd()

    def _edit_config_d(self):
        """Edit files in config.d."""
        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            self.dialog.msgbox("Error", f"Directory not found:\n{config_d}")
            return

        configs = list(config_d.glob('*.yaml'))
        if not configs:
            self.dialog.msgbox("Info", "No active configs in config.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "Active Configs",
            "Select config to edit:",
            choices
        )

        if choice and choice != "back":
            self._edit_file(choice)

    def _edit_available_d(self):
        """Edit files in available.d."""
        available_d = Path('/etc/meshtasticd/available.d')
        if not available_d.exists():
            self.dialog.msgbox("Error", f"Directory not found:\n{available_d}")
            return

        configs = list(available_d.glob('*.yaml'))
        if not configs:
            self.dialog.msgbox("Info", "No templates in available.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "Hardware Templates",
            "Select template to edit:",
            choices
        )

        if choice and choice != "back":
            self._edit_file(choice)

    def _restart_meshtasticd(self):
        """Restart meshtasticd service."""
        confirm = self.dialog.yesno(
            "Restart Service",
            "Restart meshtasticd?\n\n"
            "This will:\n"
            "1. Reload systemd daemon\n"
            "2. Restart meshtasticd service\n"
            "3. Apply any config changes",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.dialog.infobox("Restarting", "Restarting meshtasticd...")

            subprocess.run(['systemctl', 'daemon-reload'],
                           capture_output=True, timeout=10)

            result = subprocess.run(
                ['systemctl', 'restart', 'meshtasticd'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                self.dialog.msgbox("Success", "meshtasticd restarted successfully!")
            else:
                self.dialog.msgbox("Error",
                    f"Restart failed:\n{result.stderr or result.stdout}")

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Restart timed out")
        except Exception as e:
            self.dialog.msgbox("Error", f"Restart failed:\n{e}")

    def _run_basic_launcher(self):
        """Fallback basic terminal launcher."""
        # Import and run the original launcher
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "launcher",
            self.src_dir / "launcher.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()


def main():
    """Main entry point."""
    launcher = MeshForgeLauncher()
    launcher.run()


if __name__ == '__main__':
    main()
