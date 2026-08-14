"""MeshForge Maps extension management for AIToolsHandler.

Extracted from ai_tools.py for file size compliance (CLAUDE.md #6).

Owns the MFMAPS_* constants and every `_mfmaps_*` method. The host class
must provide `self.ctx` (TUIContext) and `self._open_in_browser(url)`.
"""

import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class MeshForgeMapsExtensionMixin:
    """Mixin: install/configure/control the meshforge-maps systemd extension."""

    _MFMAPS_SERVICE = "meshforge-maps"
    _MFMAPS_PORT = 8808
    _MFMAPS_WS_PORT = 8809
    _MFMAPS_DIR = "/opt/meshforge-maps"
    _MFMAPS_SERVICE_FILE = "/opt/meshforge-maps/scripts/meshforge-maps.service"

    def _open_meshforge_maps(self):
        """MeshForge Maps extension management sub-menu."""
        while True:
            installed = Path(self._MFMAPS_DIR).exists()
            svc_installed = Path(f"/etc/systemd/system/{self._MFMAPS_SERVICE}.service").exists()
            running = self._mfmaps_is_running()

            if not installed:
                choices = [
                    ("install", "Install MeshForge Maps"),
                    ("back", "Back"),
                ]
                subtitle = "Not installed"
            elif not svc_installed:
                choices = [
                    ("svc_install", "Install Systemd Service"),
                    ("open", "Open in Browser"),
                    ("back", "Back"),
                ]
                subtitle = "Installed — service not configured"
            else:
                if running:
                    status = "Running"
                    problem = None
                else:
                    problem, fixable = self._mfmaps_diagnose_service()
                    status = f"FAILED — {problem}" if problem else "Stopped"

                choices = []
                if problem and fixable:
                    choices.append(
                        ("fix", f"Fix Service    {problem}"))
                choices.extend([
                    ("open", "Open in Browser"),
                    ("status", "Service Status"),
                    ("start", "Start Service"),
                    ("stop", "Stop Service"),
                    ("restart", "Restart Service"),
                    ("logs", "View Logs"),
                    ("health", "Health Check"),
                    ("config", "Configure"),
                    ("enable", "Enable at Boot"),
                    ("disable", "Disable at Boot"),
                    ("back", "Back"),
                ])
                subtitle = f"MeshForge Maps v0.7 — {status} (:{self._MFMAPS_PORT})"

            choice = self.ctx.dialog.menu(
                "MeshForge Maps", subtitle, choices)

            if choice is None or choice == "back":
                break
            elif choice == "install":
                self._mfmaps_install()
            elif choice == "svc_install":
                self._mfmaps_install_service()
            elif choice == "fix":
                self._mfmaps_install_service()
            elif choice == "open":
                self._mfmaps_open_browser()
            elif choice == "status":
                self._mfmaps_show_status()
            elif choice in ("start", "stop", "restart"):
                self._mfmaps_service_action(choice)
            elif choice == "logs":
                self._mfmaps_show_logs()
            elif choice == "health":
                self._mfmaps_health_check()
            elif choice == "config":
                self._mfmaps_config_menu()
            elif choice == "enable":
                self._mfmaps_service_action("enable")
            elif choice == "disable":
                self._mfmaps_service_action("disable")

    def _mfmaps_is_running(self) -> bool:
        """Check if meshforge-maps is listening on its port."""
        from utils.service_check import check_port
        return check_port(self._MFMAPS_PORT, host='127.0.0.1', timeout=2)

    def _mfmaps_diagnose_service(self):
        """Diagnose why the meshforge-maps service is failing.

        Returns:
            (problem_description, can_auto_fix) or (None, False) if healthy.
        """
        import pwd
        import re

        svc_path = Path(f"/etc/systemd/system/{self._MFMAPS_SERVICE}.service")
        if not svc_path.exists():
            return None, False

        content = svc_path.read_text()

        user_match = re.search(r'^User=(\S+)', content, re.MULTILINE)
        if user_match:
            try:
                pwd.getpwnam(user_match.group(1))
            except KeyError:
                return f"User '{user_match.group(1)}' not found on this system", True

        if 'ExecStartPre=' in content and 'ProtectSystem=strict' in content:
            return "ExecStartPre incompatible with ProtectSystem", True

        wd_match = re.search(r'^WorkingDirectory=(\S+)', content, re.MULTILINE)
        if wd_match and not Path(wd_match.group(1)).exists():
            return f"WorkingDirectory not found: {wd_match.group(1)}", False

        if not Path(f"{self._MFMAPS_DIR}/venv/bin/python").exists():
            return "Virtual environment missing", True

        try:
            result = subprocess.run(
                ["systemctl", "is-failed", self._MFMAPS_SERVICE],
                capture_output=True, text=True, timeout=5)
            if result.stdout.strip() == "failed":
                return "Service in failed state", True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug("Service status check failed: %s", e)

        return None, False

    def _mfmaps_open_browser(self):
        """Open meshforge-maps in browser, start service if needed."""
        if not self._mfmaps_is_running():
            self._mfmaps_service_action("start")
            time.sleep(2)
            if not self._mfmaps_is_running():
                self.ctx.dialog.msgbox(
                    "Error", "Service failed to start. Check logs.")
                return

        from utils.map_data_service import get_all_ips
        all_ips = get_all_ips()
        urls = "\n".join(f"  http://{ip}:{self._MFMAPS_PORT}" for ip in all_ips)
        self.ctx.dialog.msgbox(
            "MeshForge Maps",
            f"Access via:\n{urls}\n\n"
            f"WebSocket: ws://localhost:{self._MFMAPS_WS_PORT}\n\n"
            "Opening in browser...")
        self._open_in_browser(f"http://localhost:{self._MFMAPS_PORT}")

    def _mfmaps_show_status(self):
        """Show systemd service status."""
        try:
            result = subprocess.run(
                ["systemctl", "status", self._MFMAPS_SERVICE, "--no-pager"],
                capture_output=True, text=True, timeout=10)
            output = result.stdout or result.stderr or "No status available."
            self.ctx.dialog.msgbox("MeshForge Maps Status", output, width=78)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.ctx.dialog.msgbox("Error", "Could not retrieve status.")

    def _mfmaps_service_action(self, action: str):
        """Start/stop/restart/enable/disable the meshforge-maps service."""
        try:
            result = subprocess.run(
                ["systemctl", action, self._MFMAPS_SERVICE],
                capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                label = {"enable": "Enabled", "disable": "Disabled"}.get(
                    action, f"{action.title()}ed")
                self.ctx.dialog.msgbox(
                    "MeshForge Maps", f"Service {label} successfully.")
            else:
                self.ctx.dialog.msgbox(
                    "Error",
                    f"Failed to {action} service:\n{result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Timeout", "Command timed out.")
        except FileNotFoundError:
            self.ctx.dialog.msgbox("Error", "systemctl not found.")

    def _mfmaps_show_logs(self):
        """Show recent meshforge-maps logs."""
        from ._service_ops_common import journal_tail_text
        self.ctx.dialog.msgbox(
            "MeshForge Maps Logs (last 50 lines)",
            journal_tail_text(self._MFMAPS_SERVICE, lines=50,
                              empty_text="No log entries found."),
            width=78)

    def _mfmaps_health_check(self):
        """Query the meshforge-maps health endpoint."""
        if not self._mfmaps_is_running():
            self.ctx.dialog.msgbox("Health", "Service is not running.")
            return
        try:
            import requests
            resp = requests.get(
                f"http://127.0.0.1:{self._MFMAPS_PORT}/api/health",
                timeout=5)
            data = resp.json()

            status = data.get("status", "unknown").upper()
            score = data.get("score", "?")
            age = data.get("data_age_seconds")
            age_str = f"{int(age)}s" if age is not None else "no data"
            sources = data.get("sources_reporting", {})
            src_lines = "\n".join(
                f"  {k}: {v} nodes" for k, v in sources.items()
            ) if sources else "  (none reporting)"
            components = data.get("components", {})
            comp_lines = "\n".join(
                f"  {k}: {v.get('score', '?')}/{v.get('max', '?')}"
                for k, v in components.items()
            ) if components else ""

            msg = (
                f"Status: {status}  (score: {score}/100)\n"
                f"Data age: {age_str}\n\n"
                f"Sources:\n{src_lines}"
            )
            if comp_lines:
                msg += f"\n\nScoring:\n{comp_lines}"

            self.ctx.dialog.msgbox("MeshForge Maps Health", msg, width=60)
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Health check failed: {e}")

    def _mfmaps_config_menu(self):
        """Configure meshforge-maps settings."""
        config_path = (
            Path(os.path.expanduser("~/.config/meshforge"))
            / "plugins" / "org.meshforge.extension.maps" / "settings.json"
        )

        current = {}
        if config_path.exists():
            try:
                current = json.loads(config_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("Config read failed: %s", e)

        while True:
            mqtt_broker = current.get("mqtt_broker", "mqtt.meshtastic.org")
            mqtt_topic = current.get("mqtt_topic", "msh/#")
            http_host = current.get("http_host", "127.0.0.1")
            tile = current.get("default_tile_provider", "carto_dark")

            choices = [
                ("mqtt_broker", f"MQTT Broker       [{mqtt_broker}]"),
                ("mqtt_topic", f"MQTT Topic        [{mqtt_topic}]"),
                ("http_host", f"Bind Address      [{http_host}]"),
                ("tile", f"Tile Provider     [{tile}]"),
                ("layers", "Data Layers       Toggle sources"),
                ("show", "Show Full Config"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MeshForge Maps Config", "Extension settings", choices)

            if choice is None or choice == "back":
                break
            elif choice == "show":
                self.ctx.dialog.msgbox(
                    "Current Config",
                    json.dumps(current, indent=2) if current
                    else "(using defaults — no settings.json yet)",
                    width=70)
            elif choice == "layers":
                self._mfmaps_toggle_layers(current, config_path)
            elif choice == "tile":
                tiles = [
                    ("carto_dark", "Carto Dark"),
                    ("osm_standard", "OpenStreetMap"),
                    ("osm_topo", "OpenTopo"),
                    ("esri_satellite", "ESRI Satellite"),
                    ("esri_topo", "ESRI Topo"),
                    ("stadia_terrain", "Stadia Terrain"),
                ]
                pick = self.ctx.dialog.menu("Tile Provider", f"Current: {tile}", tiles)
                if pick:
                    current["default_tile_provider"] = pick
                    self._mfmaps_save_config(current, config_path)
            else:
                label = {"mqtt_broker": "MQTT Broker",
                         "mqtt_topic": "MQTT Topic",
                         "http_host": "Bind Address"}.get(choice, choice)
                val = self.ctx.dialog.inputbox(label, current.get(choice, ""))
                if val is not None:
                    current[choice] = val
                    self._mfmaps_save_config(current, config_path)

    def _mfmaps_toggle_layers(self, current: dict, config_path: Path):
        """Toggle data source layers on/off."""
        layers = [
            ("enable_meshtastic", "Meshtastic"),
            ("enable_reticulum", "Reticulum/RMAP"),
            ("enable_hamclock", "HamClock"),
            ("enable_aredn", "AREDN"),
        ]
        choices = [
            (key, f"{label:20s} [{'ON' if current.get(key, True) else 'OFF'}]")
            for key, label in layers
        ]
        choices.append(("back", "Back"))

        pick = self.ctx.dialog.menu("Data Layers", "Toggle sources", choices)
        if pick and pick != "back":
            current[pick] = not current.get(pick, True)
            self._mfmaps_save_config(current, config_path)

    def _mfmaps_save_config(self, config: dict, config_path: Path):
        """Save meshforge-maps settings and prompt for restart."""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2))
        if self._mfmaps_is_running():
            if self.ctx.dialog.yesno(
                "Restart?",
                "Settings saved. Restart service to apply?"
            ):
                self._mfmaps_service_action("restart")

    def _mfmaps_install(self):
        """Clone, set up, and start meshforge-maps."""
        if os.geteuid() != 0:
            self.ctx.dialog.msgbox(
                "Root Required",
                "Run MeshForge with sudo to install extensions.")  # in-domain-ok: privilege separation — install (clone + system service) needs root (in_domain_principle.md)
            return

        if not self.ctx.dialog.yesno(
            "Install MeshForge Maps",
            "Install the MeshForge Maps extension?\n\n"
            "This will:\n"
            "  1. Clone the repository to /opt/meshforge-maps\n"
            "  2. Create a Python virtual environment\n"
            "  3. Install dependencies\n"
            "  4. Install and start the systemd service\n\n"
            "Requires internet access."
        ):
            return

        try:
            # Step 1: Clone
            self.ctx.dialog.infobox("Installing", "Cloning meshforge-maps...")
            result = subprocess.run(
                ["git", "clone", "-q",
                 "https://github.com/Nursedude/meshforge-maps.git",
                 self._MFMAPS_DIR],
                capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                self.ctx.dialog.msgbox(
                    "Error", f"git clone failed:\n{result.stderr[:500]}")
                return

            # Step 1b: chown (git clone as root → real user)
            from utils.paths import get_real_username
            username = get_real_username()
            subprocess.run(
                ["chown", "-R", f"{username}:{username}", self._MFMAPS_DIR],
                timeout=30)

            # Step 2: Venv
            self.ctx.dialog.infobox("Installing", "Creating virtual environment...")
            result = subprocess.run(
                ["python3", "-m", "venv",
                 f"{self._MFMAPS_DIR}/venv", "--system-site-packages"],
                capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                self.ctx.dialog.msgbox(
                    "Error", f"venv creation failed:\n{result.stderr[:500]}")
                return

            # Step 3: Dependencies
            self.ctx.dialog.infobox("Installing", "Installing dependencies...")
            result = subprocess.run(
                [f"{self._MFMAPS_DIR}/venv/bin/pip", "install", "-q",
                 "--timeout", "60", "-r",
                 f"{self._MFMAPS_DIR}/requirements.txt"],
                capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                self.ctx.dialog.msgbox(
                    "Error", f"pip install failed:\n{result.stderr[:500]}")
                return

            # Step 4: Install and start service
            self.ctx.dialog.infobox("Installing", "Setting up systemd service...")
            self._mfmaps_install_service(interactive=False)

            if self._mfmaps_is_running():
                self.ctx.dialog.msgbox(
                    "Installed",
                    "MeshForge Maps installed and running!\n\n"
                    f"Access: http://localhost:{self._MFMAPS_PORT}\n"
                    f"WebSocket: ws://localhost:{self._MFMAPS_WS_PORT}\n\n"
                    "Use 'Configure' to set MQTT broker, layers, etc.")
            else:
                self.ctx.dialog.msgbox(
                    "Installed",
                    "MeshForge Maps installed but service may not have started.\n"
                    "Check 'View Logs' for details.")

        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Timeout", "Installation step timed out.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Installation failed:\n{e}")

    def _mfmaps_install_service(self, interactive: bool = True):
        """Install the meshforge-maps systemd service.

        Args:
            interactive: If True, prompt for confirmation. False when
                         called from the full install flow.
        """
        svc_src = Path(self._MFMAPS_SERVICE_FILE)
        if not svc_src.exists():
            self.ctx.dialog.msgbox(
                "Error", f"Service file not found:\n{svc_src}")
            return

        if interactive and not self.ctx.dialog.yesno(
            "Install Service",
            "Install meshforge-maps systemd service?\n\n"
            "This copies the service file to /etc/systemd/system/\n"
            "and enables it to start at boot."
        ):
            return

        try:
            from utils.paths import get_real_username, get_real_user_home

            username = get_real_username()
            home = str(get_real_user_home())

            # Read template and fix user/paths for this system
            svc_content = svc_src.read_text()
            svc_content = svc_content.replace("User=pi", f"User={username}")
            svc_content = svc_content.replace("Group=pi", f"Group={username}")
            svc_content = svc_content.replace("/home/pi", home)

            # Remove ExecStartPre __pycache__ cleanup (causes permission errors)
            lines = svc_content.splitlines()
            lines = [ln for ln in lines if "ExecStartPre=" not in ln]
            svc_content = "\n".join(lines) + "\n"

            # Move StartLimit directives from [Service] to [Unit] (systemd compat)
            for key in ("StartLimitIntervalSec", "StartLimitBurst"):
                for ln in list(lines):
                    if ln.strip().startswith(f"{key}="):
                        val = ln.strip()
                        svc_content = svc_content.replace(ln + "\n", "")
                        svc_content = svc_content.replace(
                            "[Unit]\n", f"[Unit]\n{val}\n", 1)
                        break

            if "ReadWritePaths=/opt/meshforge-maps" not in svc_content:
                svc_content = svc_content.replace(
                    "[Install]",
                    f"ReadWritePaths={self._MFMAPS_DIR}\n\n[Install]")

            dest = f"/etc/systemd/system/{self._MFMAPS_SERVICE}.service"
            Path(dest).write_text(svc_content)

            subprocess.run(
                ["systemctl", "daemon-reload"], timeout=10, check=True)
            result = subprocess.run(
                ["systemctl", "enable", "--now", self._MFMAPS_SERVICE],
                capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                self.ctx.dialog.msgbox(
                    "Error",
                    f"Service enable failed:\n{result.stderr.strip()}")
                return

            if interactive:
                self.ctx.dialog.msgbox(
                    "Installed",
                    "meshforge-maps service installed and started!\n\n"
                    f"Access: http://localhost:{self._MFMAPS_PORT}")
        except subprocess.CalledProcessError as e:
            stderr = getattr(e, 'stderr', '') or ''
            self.ctx.dialog.msgbox(
                "Error", f"Service install failed:\n{stderr or e}")
        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Timeout", "Command timed out.")
