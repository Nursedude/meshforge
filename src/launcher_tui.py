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
        """Run dialog command and return (returncode, output)."""
        try:
            result = subprocess.run(
                [self.backend] + args,
                capture_output=True,
                text=True,
                timeout=300
            )
            # whiptail/dialog output goes to stderr
            return result.returncode, result.stderr.strip()
        except subprocess.TimeoutExpired:
            return 1, ""
        except Exception as e:
            return 1, str(e)

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
            choices.append(("diag", "Run Diagnostics"))
            choices.append(("bridge", "Start Gateway Bridge"))
            choices.append(("monitor", "Node Monitor"))
            choices.append(("space", "Space Weather (HamClock)"))

            # Config section
            choices.append(("---", "──────────── Config ───────────"))
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
            self._run_diagnostics()
        elif choice == "bridge":
            self._run_bridge()
        elif choice == "monitor":
            self._run_monitor()
        elif choice == "space":
            self._show_space_weather()
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

    def _run_diagnostics(self):
        """Run diagnostics."""
        # Clear screen and run diagnostics
        subprocess.run(['clear'], check=False)
        subprocess.run([sys.executable, str(self.src_dir / 'cli' / 'diagnose.py')])
        input("\nPress Enter to continue...")

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
