"""
Extensions Handler — Manage MeshForge domain extensions.

Provides TUI management for ecosystem extensions:
- Meshing Around (mesh bot framework)

Each extension follows the same lifecycle:
  Install → Diagnose → Fix → Configure → Start/Stop → Logs
"""

import json
import logging
import os
import pwd
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class ExtensionsHandler(BaseHandler):
    """TUI handler for MeshForge ecosystem extension management."""

    handler_id = "extensions"
    menu_section = "extensions"

    # Meshing Around constants
    _MA_UPSTREAM_DIR = "/opt/meshing-around"
    _MA_EXT_DIR = "/opt/meshing_around_meshforge"
    _MA_SERVICE = "mesh_bot"
    _MA_SERVICE_TEMPLATE = "/opt/meshing_around_meshforge/templates/mesh_bot.service"
    _MA_REPO_UPSTREAM = "https://github.com/SpudGunMan/meshing-around.git"
    _MA_REPO_EXT = "https://github.com/Nursedude/meshing_around_meshforge.git"

    def menu_items(self):
        return [
            ("meshing", "Meshing Around      Mesh bot framework", None),
        ]

    def execute(self, action):
        dispatch = {
            "meshing": ("Meshing Around", self._meshing_around_menu),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    # =========================================================================
    # Meshing Around Management
    # =========================================================================

    def _meshing_around_menu(self):
        """Meshing Around extension management sub-menu."""
        while True:
            upstream = Path(self._MA_UPSTREAM_DIR).exists()
            ext_installed = Path(self._MA_EXT_DIR).exists()
            svc_installed = Path(
                f"/etc/systemd/system/{self._MA_SERVICE}.service").exists()
            running = self._ma_is_running()

            if not upstream:
                choices = [
                    ("install", "Install Meshing Around"),
                    ("back", "Back"),
                ]
                subtitle = "Not installed"
            elif not svc_installed:
                choices = [
                    ("svc_install", "Install Systemd Service"),
                    ("logs", "View Logs"),
                    ("back", "Back"),
                ]
                subtitle = "Installed — service not configured"
            else:
                if running:
                    status = "Running"
                    problem = None
                else:
                    problem, fixable = self._ma_diagnose_service()
                    status = f"FAILED — {problem}" if problem else "Stopped"

                choices = []
                if problem and fixable:
                    choices.append(
                        ("fix", f"Fix Service    {problem}"))
                choices.extend([
                    ("status", "Service Status"),
                    ("start", "Start Service"),
                    ("stop", "Stop Service"),
                    ("restart", "Restart Service"),
                    ("logs", "View Logs"),
                    ("config", "Configure"),
                    ("enable", "Enable at Boot"),
                    ("disable", "Disable at Boot"),
                    ("update", "Update (git pull)"),
                    ("back", "Back"),
                ])
                subtitle = f"Meshing Around — {status}"

            choice = self.ctx.dialog.menu(
                "Meshing Around", subtitle, choices)

            if choice is None or choice == "back":
                break
            elif choice == "install":
                self._ma_install()
            elif choice == "svc_install":
                self._ma_install_service()
            elif choice == "fix":
                self._ma_install_service()
            elif choice == "status":
                self._ma_show_status()
            elif choice in ("start", "stop", "restart"):
                self._ma_service_action(choice)
            elif choice == "logs":
                self._ma_show_logs()
            elif choice == "config":
                self._ma_config_menu()
            elif choice == "enable":
                self._ma_service_action("enable")
            elif choice == "disable":
                self._ma_service_action("disable")
            elif choice == "update":
                self._ma_update()

    def _ma_is_running(self) -> bool:
        """Check if mesh_bot service is active."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", self._MA_SERVICE],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip() == "active"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _ma_diagnose_service(self) -> Tuple[Optional[str], bool]:
        """Diagnose why the mesh_bot service is failing."""
        svc_path = Path(
            f"/etc/systemd/system/{self._MA_SERVICE}.service")
        if not svc_path.exists():
            return None, False

        content = svc_path.read_text()

        # Check User= exists on this system
        user_match = re.search(r'^User=(\S+)', content, re.MULTILINE)
        if user_match:
            try:
                pwd.getpwnam(user_match.group(1))
            except KeyError:
                return (
                    f"User '{user_match.group(1)}' not found", True)

        # Check WorkingDirectory exists
        wd_match = re.search(
            r'^WorkingDirectory=(\S+)', content, re.MULTILINE)
        if wd_match and not Path(wd_match.group(1)).exists():
            return (
                f"WorkingDirectory not found: {wd_match.group(1)}", False)

        # Check venv exists
        if not Path(f"{self._MA_UPSTREAM_DIR}/venv/bin/python").exists():
            return "Virtual environment missing", True

        # Check launch.sh exists
        if not Path(f"{self._MA_UPSTREAM_DIR}/launch.sh").exists():
            return "launch.sh not found", False

        # Check systemctl failed state
        try:
            result = subprocess.run(
                ["systemctl", "is-failed", self._MA_SERVICE],
                capture_output=True, text=True, timeout=5)
            if result.stdout.strip() == "failed":
                return "Service in failed state", True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return None, False

    def _ma_install(self):
        """Clone and set up meshing-around + meshforge extension."""
        if os.geteuid() != 0:
            self.ctx.dialog.msgbox(
                "Root Required",
                "Run MeshForge with sudo to install extensions.")
            return

        if not self.ctx.dialog.yesno(
            "Install Meshing Around",
            "Install the Meshing Around mesh bot framework?\n\n"
            "This will:\n"
            "  1. Clone meshing-around (upstream) to /opt/meshing-around\n"
            "  2. Clone meshforge extension to /opt/meshing_around_meshforge\n"
            "  3. Create virtual environment + install dependencies\n"
            "  4. Install and start the systemd service\n\n"
            "Requires internet access."
        ):
            return

        from utils.paths import get_real_username

        username = get_real_username()

        try:
            # Step 1: Clone upstream (if not present)
            if not Path(self._MA_UPSTREAM_DIR).exists():
                self.ctx.dialog.infobox(
                    "Installing", "Cloning meshing-around (upstream)...")
                result = subprocess.run(
                    ["git", "clone", "-q", self._MA_REPO_UPSTREAM,
                     self._MA_UPSTREAM_DIR],
                    capture_output=True, text=True, timeout=120)
                if result.returncode != 0:
                    self.ctx.dialog.msgbox(
                        "Error",
                        f"Clone failed:\n{result.stderr[:500]}")
                    return

                # Fix ownership
                subprocess.run(
                    ["chown", "-R", f"{username}:{username}",
                     self._MA_UPSTREAM_DIR], timeout=30)
            else:
                self.ctx.dialog.infobox(
                    "Installing",
                    "meshing-around already at /opt/meshing-around")
                time.sleep(1)

            # Step 2: Clone extension (if not present)
            if not Path(self._MA_EXT_DIR).exists():
                self.ctx.dialog.infobox(
                    "Installing",
                    "Cloning meshing_around_meshforge extension...")
                result = subprocess.run(
                    ["git", "clone", "-q", self._MA_REPO_EXT,
                     self._MA_EXT_DIR],
                    capture_output=True, text=True, timeout=120)
                if result.returncode != 0:
                    self.ctx.dialog.msgbox(
                        "Error",
                        f"Clone failed:\n{result.stderr[:500]}")
                    return

                subprocess.run(
                    ["chown", "-R", f"{username}:{username}",
                     self._MA_EXT_DIR], timeout=30)

            # Step 3: Venv + deps (in upstream dir)
            if not Path(f"{self._MA_UPSTREAM_DIR}/venv").exists():
                self.ctx.dialog.infobox(
                    "Installing", "Creating virtual environment...")
                result = subprocess.run(
                    ["python3", "-m", "venv",
                     f"{self._MA_UPSTREAM_DIR}/venv",
                     "--system-site-packages"],
                    capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    self.ctx.dialog.msgbox(
                        "Error",
                        f"venv creation failed:\n{result.stderr[:500]}")
                    return

            self.ctx.dialog.infobox(
                "Installing", "Installing dependencies...")
            result = subprocess.run(
                [f"{self._MA_UPSTREAM_DIR}/venv/bin/pip", "install",
                 "-q", "--timeout", "60", "-r",
                 f"{self._MA_UPSTREAM_DIR}/requirements.txt"],
                capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                self.ctx.dialog.msgbox(
                    "Error",
                    f"pip install failed:\n{result.stderr[:500]}")
                return

            # Step 4: Copy default config if missing
            config_path = Path(f"{self._MA_UPSTREAM_DIR}/config.ini")
            template_path = Path(
                f"{self._MA_UPSTREAM_DIR}/config.template")
            if not config_path.exists() and template_path.exists():
                import shutil
                shutil.copy2(template_path, config_path)

            # Step 5: Install service
            self.ctx.dialog.infobox(
                "Installing", "Setting up systemd service...")
            self._ma_install_service(interactive=False)

            if self._ma_is_running():
                self.ctx.dialog.msgbox(
                    "Installed",
                    "Meshing Around installed and running!\n\n"
                    "Use 'Configure' to set your radio connection\n"
                    "(serial port, hostname, channel, etc.)")
            else:
                self.ctx.dialog.msgbox(
                    "Installed",
                    "Meshing Around installed.\n\n"
                    "The service may need radio configuration before\n"
                    "it can start. Use 'Configure' to set up your\n"
                    "radio connection, then 'Start Service'.")

        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Timeout", "Installation step timed out.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Installation failed:\n{e}")

    def _ma_install_service(self, interactive: bool = True):
        """Install the mesh_bot systemd service."""
        # Prefer meshforge extension template, fall back to upstream
        svc_src = Path(self._MA_SERVICE_TEMPLATE)
        if not svc_src.exists():
            # Try upstream etc/ directory
            svc_src = Path(f"{self._MA_UPSTREAM_DIR}/etc/mesh_bot.service")
        if not svc_src.exists():
            self.ctx.dialog.msgbox(
                "Error", "Service template not found.")
            return

        if interactive and not self.ctx.dialog.yesno(
            "Install Service",
            "Install mesh_bot systemd service?\n\n"
            "This creates the service file and enables it at boot."
        ):
            return

        try:
            from utils.paths import get_real_username

            username = get_real_username()

            content = svc_src.read_text()

            # Replace template placeholders
            content = content.replace("__USER__", username)
            content = content.replace("__BOT_DIR__", self._MA_UPSTREAM_DIR)

            # Also fix hardcoded pi user if present
            content = content.replace("User=pi", f"User={username}")
            content = content.replace("Group=pi", f"Group={username}")

            dest = f"/etc/systemd/system/{self._MA_SERVICE}.service"
            Path(dest).write_text(content)

            subprocess.run(
                ["systemctl", "daemon-reload"], timeout=10, check=True)
            result = subprocess.run(
                ["systemctl", "enable", "--now", self._MA_SERVICE],
                capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                if interactive:
                    self.ctx.dialog.msgbox(
                        "Warning",
                        f"Service enabled but may not have started:\n"
                        f"{result.stderr.strip()}\n\n"
                        f"Configure the radio connection first,\n"
                        f"then start the service.")
                return

            if interactive:
                self.ctx.dialog.msgbox(
                    "Installed",
                    "mesh_bot service installed and started!")
        except subprocess.CalledProcessError as e:
            stderr = getattr(e, 'stderr', '') or ''
            self.ctx.dialog.msgbox(
                "Error", f"Service install failed:\n{stderr or e}")
        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Timeout", "Command timed out.")

    def _ma_show_status(self):
        """Show systemd service status."""
        try:
            result = subprocess.run(
                ["systemctl", "status", self._MA_SERVICE, "--no-pager"],
                capture_output=True, text=True, timeout=10)
            output = result.stdout or result.stderr or "No status available."
            self.ctx.dialog.msgbox(
                "Meshing Around Status", output, width=78)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.ctx.dialog.msgbox("Error", "Could not retrieve status.")

    def _ma_service_action(self, action: str):
        """Start/stop/restart/enable/disable the mesh_bot service."""
        try:
            result = subprocess.run(
                ["systemctl", action, self._MA_SERVICE],
                capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                label = {"enable": "Enabled", "disable": "Disabled"}.get(
                    action, f"{action.title()}ed")
                self.ctx.dialog.msgbox(
                    "Meshing Around", f"Service {label} successfully.")
            else:
                self.ctx.dialog.msgbox(
                    "Error",
                    f"Failed to {action} service:\n{result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Timeout", "Command timed out.")
        except FileNotFoundError:
            self.ctx.dialog.msgbox("Error", "systemctl not found.")

    def _ma_show_logs(self):
        """Show recent mesh_bot logs."""
        try:
            result = subprocess.run(
                ["journalctl", "-u", self._MA_SERVICE,
                 "-n", "50", "--no-pager"],
                capture_output=True, text=True, timeout=10)
            if result.stdout:
                self.ctx.dialog.msgbox(
                    "Meshing Around Logs (last 50 lines)",
                    result.stdout, width=78)
            else:
                # Fall back to log file
                log_file = Path(
                    f"{self._MA_UPSTREAM_DIR}/logs/prior_prior_prior.log")
                if not log_file.exists():
                    log_file = Path(f"{self._MA_UPSTREAM_DIR}/prior_prior_prior.log")
                if log_file.exists():
                    lines = log_file.read_text().splitlines()[-50:]
                    self.ctx.dialog.msgbox(
                        "Meshing Around Logs",
                        "\n".join(lines), width=78)
                else:
                    self.ctx.dialog.msgbox("No Logs", "No log entries found.")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.ctx.dialog.msgbox("Error", "Could not retrieve logs.")

    def _ma_config_menu(self):
        """Configure meshing-around config.ini."""
        config_path = Path(f"{self._MA_UPSTREAM_DIR}/config.ini")
        if not config_path.exists():
            self.ctx.dialog.msgbox(
                "No Config",
                f"config.ini not found at:\n{config_path}\n\n"
                "Start the service once to generate defaults.")
            return

        import configparser
        config = configparser.ConfigParser()
        config.read(str(config_path))

        while True:
            # Show key settings
            iface_type = config.get("interface", "type", fallback="serial")
            port = config.get("interface", "port", fallback="/dev/ttyACM0")
            hostname = config.get("interface", "hostname", fallback="")
            channel = config.get("general", "defaultchannel", fallback="0")
            motd = config.get("general", "motd", fallback="")

            conn_info = (f"{iface_type} ({port})" if iface_type == "serial"
                         else f"{iface_type} ({hostname})"
                         if hostname else iface_type)

            choices = [
                ("iface", f"Interface         [{conn_info}]"),
                ("channel", f"Default Channel   [{channel}]"),
                ("motd", f"MOTD              [{motd[:30]}]"),
                ("sentry", "Sentry Settings   Alert radius, channel"),
                ("location", "Location          GPS coordinates"),
                ("show", "Show Full Config"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Meshing Around Config",
                f"Radio: {conn_info}", choices)

            if choice is None or choice == "back":
                break
            elif choice == "show":
                self.ctx.dialog.msgbox(
                    "config.ini", config_path.read_text(), width=70)
            elif choice == "iface":
                self._ma_config_interface(config, config_path)
            elif choice == "channel":
                val = self.ctx.dialog.inputbox(
                    "Default Channel", channel)
                if val is not None:
                    config.set("general", "defaultchannel", val)
                    self._ma_save_config(config, config_path)
            elif choice == "motd":
                val = self.ctx.dialog.inputbox("MOTD", motd)
                if val is not None:
                    config.set("general", "motd", val)
                    self._ma_save_config(config, config_path)
            elif choice == "sentry":
                self._ma_config_sentry(config, config_path)
            elif choice == "location":
                self._ma_config_location(config, config_path)

    def _ma_config_interface(self, config, config_path):
        """Configure the radio interface."""
        types = [
            ("serial", "USB Serial       /dev/ttyACM0, /dev/ttyUSB0"),
            ("tcp", "TCP              Connect to meshtasticd"),
            ("mqtt", "MQTT             No local radio needed"),
            ("back", "Back"),
        ]

        pick = self.ctx.dialog.menu(
            "Interface Type",
            f"Current: {config.get('interface', 'type', fallback='serial')}",
            types)

        if pick and pick != "back":
            config.set("interface", "type", pick)
            if pick == "serial":
                port = self.ctx.dialog.inputbox(
                    "Serial Port",
                    config.get("interface", "port",
                               fallback="/dev/ttyACM0"))
                if port:
                    config.set("interface", "port", port)
            elif pick == "tcp":
                host = self.ctx.dialog.inputbox(
                    "Hostname",
                    config.get("interface", "hostname",
                               fallback="localhost"))
                if host:
                    config.set("interface", "hostname", host)
            self._ma_save_config(config, config_path)

    def _ma_config_sentry(self, config, config_path):
        """Configure sentry (alert) settings."""
        enabled = config.getboolean(
            "sentry", "sentryenabled", fallback=False)
        channel = config.get("sentry", "sentrychannel", fallback="2")
        radius = config.get("sentry", "sentryradius", fallback="100")

        choices = [
            ("toggle",
             f"Enabled           [{'ON' if enabled else 'OFF'}]"),
            ("channel", f"Sentry Channel    [{channel}]"),
            ("radius", f"Alert Radius (m)  [{radius}]"),
            ("back", "Back"),
        ]

        pick = self.ctx.dialog.menu("Sentry Settings", "", choices)

        if pick == "toggle":
            config.set("sentry", "sentryenabled",
                        str(not enabled))
            self._ma_save_config(config, config_path)
        elif pick == "channel":
            val = self.ctx.dialog.inputbox("Sentry Channel", channel)
            if val is not None:
                config.set("sentry", "sentrychannel", val)
                self._ma_save_config(config, config_path)
        elif pick == "radius":
            val = self.ctx.dialog.inputbox("Alert Radius (meters)", radius)
            if val is not None:
                config.set("sentry", "sentryradius", val)
                self._ma_save_config(config, config_path)

    def _ma_config_location(self, config, config_path):
        """Configure location settings."""
        lat = config.get("location", "lat", fallback="0")
        lon = config.get("location", "lon", fallback="0")

        new_lat = self.ctx.dialog.inputbox("Latitude", lat)
        if new_lat is not None:
            config.set("location", "lat", new_lat)
        new_lon = self.ctx.dialog.inputbox("Longitude", lon)
        if new_lon is not None:
            config.set("location", "lon", new_lon)

        if new_lat is not None or new_lon is not None:
            self._ma_save_config(config, config_path)

    def _ma_save_config(self, config, config_path):
        """Save config.ini and prompt for restart."""
        with open(config_path, 'w') as f:
            config.write(f)
        if self._ma_is_running():
            if self.ctx.dialog.yesno(
                "Restart?",
                "Settings saved. Restart service to apply?"
            ):
                self._ma_service_action("restart")

    def _ma_update(self):
        """Update both repos via git pull."""
        msgs = []
        for label, path in [
            ("meshing-around", self._MA_UPSTREAM_DIR),
            ("meshing_around_meshforge", self._MA_EXT_DIR),
        ]:
            if not Path(path).exists():
                continue
            try:
                result = subprocess.run(
                    ["git", "-C", path, "pull", "-q"],
                    capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    msgs.append(f"{label}: updated")
                else:
                    msgs.append(
                        f"{label}: {result.stderr.strip()[:100]}")
            except subprocess.TimeoutExpired:
                msgs.append(f"{label}: timeout")

        self.ctx.dialog.msgbox("Update", "\n".join(msgs) or "Nothing to update.")

        if self._ma_is_running() and self.ctx.dialog.yesno(
            "Restart?", "Restart service to apply updates?"
        ):
            self._ma_service_action("restart")
