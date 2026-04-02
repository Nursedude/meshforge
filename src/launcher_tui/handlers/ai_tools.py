"""
AI Tools Handler — Maps, coverage, diagnostics, knowledge base, Claude assistant.

Converted from ai_tools_mixin.py as part of the mixin-to-registry migration (Batch 8).

Provides:
- Live NOC Map (browser snapshot + HTTP server + auto-start)
- Coverage Map generation (all sources, meshtasticd, MQTT, file)
- Node density heatmap
- Offline tile caching
- Intelligent diagnostics (rule-based symptom analysis)
- Knowledge base queries
- Claude Assistant (Standalone + PRO)

Implements LifecycleHandler for on_startup (auto-start map server).
"""

import json
import logging
import os
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from handler_protocol import BaseHandler
from utils.safe_import import safe_import

# --- Optional dependencies (safe_import returns (*attrs, available_bool)) ---
diagnose, Category, Severity, _HAS_DIAGNOSTICS = safe_import(
    'utils.diagnostic_engine', 'diagnose', 'Category', 'Severity'
)
get_knowledge_base, _HAS_KNOWLEDGE = safe_import(
    'utils.knowledge_base', 'get_knowledge_base'
)
ClaudeAssistant, _HAS_ASSISTANT = safe_import(
    'utils.claude_assistant', 'ClaudeAssistant'
)
CoverageMapGenerator, MapNode, _HAS_COVERAGE_MAP = safe_import(
    'utils.coverage_map', 'CoverageMapGenerator', 'MapNode'
)
MapDataCollector, get_all_ips, _HAS_MAP_SERVICE = safe_import(
    'utils.map_data_service', 'MapDataCollector', 'get_all_ips'
)
TileCache, HAWAII_BOUNDS, _HAS_TILE_CACHE = safe_import(
    'utils.tile_cache', 'TileCache', 'HAWAII_BOUNDS'
)

# Import service helpers for privileged systemctl calls
from utils.service_check import _sudo_cmd, start_service

logger = logging.getLogger(__name__)


class AIToolsHandler(BaseHandler):
    """TUI handler for maps, coverage, diagnostics, knowledge base, and Claude assistant."""

    handler_id = "ai_tools"
    menu_section = "maps_viz"

    def menu_items(self):
        return [
            ("livemap",   "Live NOC Map        Real-time browser view", None),
            ("mfmaps",    "MeshForge Maps      Multi-source map ext.", None),
            ("coverage",  "Coverage Map        Generate coverage map",  None),
            ("heatmap",   "Heatmap             Node density heatmap",   None),
            ("tiles",     "Offline Tiles       Cache map tiles",        None),
            ("ai",        "AI Diagnostics      Knowledge base, assistant", None),
        ]

    def execute(self, action):
        dispatch = {
            "livemap": ("Live NOC Map", self._open_live_map),
            "mfmaps": ("MeshForge Maps", self._open_meshforge_maps),
            "coverage": ("Coverage Map", self._generate_coverage_map),
            "heatmap": ("Heatmap", self._generate_heatmap),
            "tiles": ("Offline Tile Cache", self._tile_cache_menu),
            "ai": ("AI Diagnostics", self._ai_tools_menu),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    # -- Lifecycle hooks (LifecycleHandler protocol) --

    def on_startup(self):
        """Start map server on TUI launch if user has enabled auto-open."""
        self._maybe_auto_start_map()

    # =========================================================================
    # AI Tools sub-menu
    # =========================================================================

    def _ai_tools_menu(self):
        """Maps and coverage tools menu."""
        choices = [
            ("livemap", "Live Network Map"),
            ("coverage", "Generate Coverage Map (All Sources)"),
            ("diagnose", "Intelligent Diagnostics"),
            ("knowledge", "Knowledge Base Query"),
            ("assistant", "Claude Assistant"),
            ("back", "Back"),
        ]

        while True:
            choice = self.ctx.dialog.menu(
                "Maps & Coverage",
                "Network mapping and analysis tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "livemap": ("Live Network Map", self._open_live_map),
                "diagnose": ("Intelligent Diagnostics", self._intelligent_diagnostics),
                "knowledge": ("Knowledge Base Query", self._knowledge_base_query),
                "assistant": ("Claude Assistant", self._claude_assistant),
                "coverage": ("Coverage Map", self._generate_coverage_map),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # =========================================================================
    # Map auto-start (LifecycleHandler)
    # =========================================================================

    def _maybe_auto_start_map(self):
        """Start map server on TUI launch if user has enabled auto-open.

        Prefers systemd service (meshforge-map) for reliability.
        Falls back to in-process server if systemd unavailable.
        """
        settings_file = self._get_map_settings_file()
        if not settings_file.exists():
            return

        try:
            with open(settings_file) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        if not settings.get("auto_open_map", False):
            return

        # Check if server already running (port 5000)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', 5000))
            sock.close()
            if result == 0:
                return  # Already running
        except OSError:
            pass

        # Try to start via systemd service first (preferred for reliability)
        if self._try_start_map_service_quiet():
            return  # Successfully started via systemd

        # Fall back to in-process server (non-systemd environments)
        # Suppress console output to prevent TUI corruption, keep file logging
        try:
            from contextlib import redirect_stdout, redirect_stderr
            from io import StringIO

            root_logger = logging.getLogger()
            old_handler_levels = []
            for handler in root_logger.handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                    old_handler_levels.append((handler, handler.level))
                    handler.setLevel(logging.CRITICAL + 1)

            try:
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    from utils.map_data_service import MapServer
                    server = MapServer(port=5000)
                    server.start_background()
            finally:
                for handler, level in old_handler_levels:
                    handler.setLevel(level)

            self._map_server = server
        except Exception as e:
            logger.warning("Map server auto-start failed: %s", e)

    def _try_start_map_service_quiet(self) -> bool:
        """Try to start map server via systemd (quiet, no TUI output).

        Returns True if service started successfully.
        """
        try:
            # Check if systemd is available
            result = subprocess.run(
                ['systemctl', 'is-enabled', 'meshforge-map'],
                capture_output=True, timeout=5
            )
            if result.returncode != 0:
                return False  # Service not installed

            # Start the service
            start_service('meshforge-map')

            # Wait briefly for service to start
            for _ in range(5):
                time.sleep(0.5)
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(('localhost', 5000))
                    sock.close()
                    if result == 0:
                        return True
                except OSError:
                    pass

            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Map systemd service start failed: %s", e)
            return False

    def _get_map_settings_file(self) -> Path:
        """Get the map settings file path."""
        from utils.paths import get_real_user_home
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "map_settings.json"

    # =========================================================================
    # Live Map
    # =========================================================================

    def _open_live_map(self):
        """Open the live network map with real node data."""
        while True:
            # Check current auto-open setting (refresh each loop)
            auto_enabled = False
            settings_file = self._get_map_settings_file()
            if settings_file.exists():
                try:
                    with open(settings_file) as f:
                        auto_enabled = json.load(f).get("auto_open_map", False)
                except (json.JSONDecodeError, OSError):
                    pass

            auto_label = "ON" if auto_enabled else "OFF"
            choices = [
                ("browser", "Open map in browser (snapshot)"),
                ("server", "Start map server (live updates)"),
                ("autostart", f"Auto-open on launch [{auto_label}]"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Live Network Map",
                "Select map mode:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "browser": ("Browser Map Snapshot", self._open_live_map_browser),
                "server": ("Map Server", self._start_map_server),
                "autostart": ("Toggle Auto-open", self._toggle_auto_map),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _open_live_map_browser(self):
        """Generate browser snapshot of the live map with current node data."""
        # Browser mode: collect data, inject into HTML, open
        self.ctx.dialog.infobox("Loading", "Collecting node data from all sources...")

        try:
            from utils.map_data_service import MapDataCollector

            collector = MapDataCollector()
            geojson = collector.collect()
            node_count = len(geojson.get("features", []))
            sources = geojson.get("properties", {}).get("sources", {})

            # Find the map template
            src_dir = Path(__file__).parent.parent.parent
            map_template = src_dir / "web" / "node_map.html"

            if not map_template.exists():
                self.ctx.dialog.msgbox(
                    "Map Not Found",
                    f"Map template not found at:\n{map_template}"
                )
                return

            # Read template and inject data
            with open(map_template, 'r') as f:
                html_content = f.read()

            if node_count > 0:
                geojson_str = json.dumps(geojson)
                inject_script = (
                    f'\n<script>\n'
                    f'// MeshForge: {node_count} nodes from '
                    f'meshtasticd({sources.get("meshtasticd", 0)}) '
                    f'mqtt({sources.get("mqtt", 0)}) '
                    f'tracker({sources.get("node_tracker", 0)})\n'
                    f'window.meshforgeData = {geojson_str};\n'
                    f'</script>\n</body>'
                )
                html_content = html_content.replace('</body>', inject_script)

            # Write to user-accessible location
            from utils.paths import get_real_user_home
            output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "live_map.html"

            with open(output_file, 'w') as f:
                f.write(html_content)

            # Build detailed source breakdown
            source_info = []
            source_info.append(f"meshtasticd: {sources.get('meshtasticd', 0)}")
            source_info.append(f"MQTT: {sources.get('mqtt', 0)}")
            source_info.append(f"node_tracker: {sources.get('node_tracker', 0)}")

            msg = (
                f"Map saved: {output_file}\n\n"
                f"Total nodes: {node_count}\n"
                f"Sources:\n  " + "\n  ".join(source_info) + "\n\n"
                "Opening in browser..."
            )
            self.ctx.dialog.msgbox("Live Map", msg)
            self._open_in_browser(f"file://{output_file}")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to generate live map: {e}")

    def _is_headless(self) -> bool:
        """Check if running without a display (headless/SSH)."""
        display = os.environ.get('DISPLAY')
        wayland = os.environ.get('WAYLAND_DISPLAY')
        ssh = os.environ.get('SSH_CONNECTION')
        return (not display and not wayland) or bool(ssh)

    # =========================================================================
    # MeshForge Maps Extension Management
    # =========================================================================

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
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', self._MFMAPS_PORT))
            sock.close()
            return result == 0
        except OSError:
            return False

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

        # Check User= exists on this system
        user_match = re.search(r'^User=(\S+)', content, re.MULTILINE)
        if user_match:
            try:
                pwd.getpwnam(user_match.group(1))
            except KeyError:
                return f"User '{user_match.group(1)}' not found on this system", True

        # Check ExecStartPre + ProtectSystem conflict
        if 'ExecStartPre=' in content and 'ProtectSystem=strict' in content:
            return "ExecStartPre incompatible with ProtectSystem", True

        # Check WorkingDirectory exists
        wd_match = re.search(r'^WorkingDirectory=(\S+)', content, re.MULTILINE)
        if wd_match and not Path(wd_match.group(1)).exists():
            return f"WorkingDirectory not found: {wd_match.group(1)}", False

        # Check venv exists
        if not Path(f"{self._MFMAPS_DIR}/venv/bin/python").exists():
            return "Virtual environment missing", True

        # Check systemctl failed state
        try:
            result = subprocess.run(
                ["systemctl", "is-failed", self._MFMAPS_SERVICE],
                capture_output=True, text=True, timeout=5)
            if result.stdout.strip() == "failed":
                return "Service in failed state", True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

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
        try:
            result = subprocess.run(
                ["journalctl", "-u", self._MFMAPS_SERVICE,
                 "-n", "50", "--no-pager"],
                capture_output=True, text=True, timeout=10)
            if result.stdout:
                self.ctx.dialog.msgbox(
                    "MeshForge Maps Logs (last 50 lines)",
                    result.stdout, width=78)
            else:
                self.ctx.dialog.msgbox("No Logs", "No log entries found.")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.ctx.dialog.msgbox("Error", "Could not retrieve logs.")

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

        # Load current settings
        current = {}
        if config_path.exists():
            try:
                current = json.loads(config_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

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
                "Run MeshForge with sudo to install extensions.")
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

            # Step 1b: Fix ownership (git clone as root → chown to real user)
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

            # Ensure working directory is writable by the service
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

    def _start_map_server(self):
        """Start the map HTTP server for live-updating browser access.

        Prefers systemd service (meshforge-map) for reliability.
        Falls back to in-process server if systemd unavailable.
        """
        port = 5000

        # Get all available IPs for display
        from utils.map_data_service import get_all_ips
        all_ips = get_all_ips()

        # Check if port is already in use
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            if result == 0:
                urls = "\n".join(f"  http://{ip}:{port}" for ip in all_ips)
                service_status = self._get_map_service_status()
                self.ctx.dialog.msgbox(
                    "Map Server",
                    f"Map server already running!\n\n"
                    f"Access via:\n{urls}\n\n"
                    f"Service: {service_status}\n\n"
                    "Open any URL in your browser.\n"
                    "The map auto-refreshes every 30 seconds."
                )
                return
        except OSError:
            pass

        # Try systemd service first (preferred for reliability)
        service_started = self._try_start_map_service()

        if service_started:
            urls = "\n".join(f"  http://{ip}:{port}" for ip in all_ips)
            self.ctx.dialog.msgbox(
                "Map Server Started",
                f"Map server running as system service!\n\n"
                f"Access via:\n{urls}\n\n"
                "Open any URL in your browser.\n"
                "The map pulls fresh data every 30 seconds.\n\n"
                "Service persists after TUI exits.\n"
                "Manage with: meshforge-map start|stop|status"
            )
            return

        # Fall back to in-process server
        try:
            from contextlib import redirect_stdout, redirect_stderr
            from io import StringIO

            captured_out = StringIO()
            captured_err = StringIO()

            root_logger = logging.getLogger()
            old_handler_levels = []
            for handler in root_logger.handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                    old_handler_levels.append((handler, handler.level))
                    handler.setLevel(logging.CRITICAL + 1)

            try:
                with redirect_stdout(captured_out), redirect_stderr(captured_err):
                    from utils.map_data_service import MapServer

                    server = MapServer(port=port)  # Binds to 0.0.0.0
                    server.start_background()

                    time.sleep(0.1)
            finally:
                for handler, level in old_handler_levels:
                    handler.setLevel(level)

            self._map_server = server

            urls = "\n".join(f"  http://{ip}:{port}" for ip in all_ips)
            msg = (
                f"Live map server running (in-process)!\n\n"
                f"Access via:\n{urls}\n\n"
                "Open any URL in your browser.\n"
                "The map pulls fresh data every 30 seconds.\n"
                "Server runs until MeshForge exits.\n\n"
                "Tip: Install meshforge-map service for\n"
                "persistent operation."
            )
            self.ctx.dialog.msgbox("Map Server Started", msg)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to start map server: {e}")

    def _try_start_map_service(self) -> bool:
        """Try to start map server via systemd service.

        Returns True if service started successfully.
        """
        try:
            # Check if systemd service is available
            result = subprocess.run(
                ['systemctl', 'is-enabled', 'meshforge-map'],
                capture_output=True, timeout=5
            )
            if result.returncode != 0:
                return False  # Service not installed

            # Start the service
            start_service('meshforge-map')

            # Wait for service to start (up to 3 seconds)
            for _ in range(6):
                time.sleep(0.5)
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex(('localhost', 5000))
                    sock.close()
                    if result == 0:
                        return True
                except OSError:
                    pass

            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Map service restart failed: %s", e)
            return False

    def _get_map_service_status(self) -> str:
        """Get map server service status for display."""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'meshforge-map'],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            if status == "active":
                return "systemd service (active)"
            elif result.returncode != 0:
                return "in-process (TUI)"
            return f"systemd ({status})"
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Map service status check failed: %s", e)
            return "in-process (TUI)"

    def _toggle_auto_map(self):
        """Toggle the auto-open map on launch setting."""
        settings_file = self._get_map_settings_file()
        settings = {}

        if settings_file.exists():
            try:
                with open(settings_file) as f:
                    settings = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        current = settings.get("auto_open_map", False)
        settings["auto_open_map"] = not current

        try:
            with open(settings_file, 'w') as f:
                json.dump(settings, f, indent=2)

            state = "ENABLED" if settings["auto_open_map"] else "DISABLED"
            msg = (
                f"Auto-open map: {state}\n\n"
            )
            if settings["auto_open_map"]:
                from utils.map_data_service import get_all_ips
                ips = get_all_ips()
                urls = ", ".join(f"http://{ip}:5000" for ip in ips[:2])
                if len(ips) > 2:
                    urls += ", ..."
                msg += (
                    "The map server will start automatically\n"
                    "when MeshForge launches.\n\n"
                    f"Access at: {urls}"
                )
            else:
                msg += "Map server will not start automatically."

            self.ctx.dialog.msgbox("Map Settings", msg)
        except OSError as e:
            self.ctx.dialog.msgbox("Error", f"Failed to save setting: {e}")

        # Return to caller — _open_live_map loop will re-show menu

    # =========================================================================
    # Intelligent Diagnostics
    # =========================================================================

    def _intelligent_diagnostics(self):
        """Run intelligent diagnostics with symptom analysis."""
        # Common symptoms to diagnose
        symptom_choices = [
            ("connection", "Connection refused to meshtasticd"),
            ("no_nodes", "No nodes visible in mesh"),
            ("weak_signal", "Weak signal / low SNR"),
            ("timeout", "Message timeouts"),
            ("service", "Service not starting"),
            ("custom", "Describe custom symptom"),
            ("back", "Back"),
        ]

        while True:
            choice = self.ctx.dialog.menu(
                "Intelligent Diagnostics",
                "Select a symptom to diagnose:",
                symptom_choices
            )

            if choice is None or choice == "back":
                break

            symptom_text = None
            if choice == "custom":
                symptom_text = self.ctx.dialog.inputbox(
                    "Custom Symptom",
                    "Describe the issue you're experiencing:"
                )
                if not symptom_text:
                    continue
            else:
                # Map choice to symptom text
                symptom_map = {
                    "connection": "Connection refused to meshtasticd on port 4403",
                    "no_nodes": "No nodes visible in mesh network",
                    "weak_signal": "Weak signal with low SNR values",
                    "timeout": "Message timeouts when sending",
                    "service": "Service meshtasticd failed to start",
                }
                symptom_text = symptom_map.get(choice, choice)

            self._run_diagnosis(symptom_text)

    def _run_diagnosis(self, symptom: str):
        """Run diagnosis on a symptom."""
        self.ctx.dialog.infobox("Analyzing", f"Analyzing: {symptom[:40]}...")

        if not _HAS_DIAGNOSTICS:
            self.ctx.dialog.msgbox(
                "Error",
                "Diagnostic engine not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            # Run diagnosis
            diagnosis_result = diagnose(
                symptom,
                category=Category.CONNECTIVITY,
                severity=Severity.ERROR
            )

            if diagnosis_result:
                # Format diagnosis for display
                result_lines = [
                    f"SYMPTOM: {symptom}",
                    "",
                    f"LIKELY CAUSE:",
                    f"  {diagnosis_result.likely_cause}",
                    "",
                    f"CONFIDENCE: {diagnosis_result.confidence:.0%}",
                    "",
                ]

                if diagnosis_result.evidence:
                    result_lines.append("EVIDENCE:")
                    for ev in diagnosis_result.evidence[:3]:
                        result_lines.append(f"  - {ev}")
                    result_lines.append("")

                if diagnosis_result.suggestions:
                    result_lines.append("SUGGESTIONS:")
                    for i, sug in enumerate(diagnosis_result.suggestions[:5], 1):
                        result_lines.append(f"  {i}. {sug}")
                    result_lines.append("")

                if diagnosis_result.auto_recoverable:
                    result_lines.append(f"AUTO-RECOVERY: {diagnosis_result.recovery_action}")

                self.ctx.dialog.msgbox(
                    "Diagnosis Result",
                    "\n".join(result_lines)
                )
            else:
                self.ctx.dialog.msgbox(
                    "Diagnosis",
                    f"No specific diagnosis found for:\n{symptom}\n\n"
                    "Try the Knowledge Base for general information,\n"
                    "or use Claude Assistant for detailed help."
                )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Diagnosis failed: {e}")

    # =========================================================================
    # Knowledge Base
    # =========================================================================

    def _knowledge_base_query(self):
        """Query the knowledge base for mesh networking concepts."""
        # Common topics
        topic_choices = [
            ("snr", "What is SNR?"),
            ("rssi", "What is RSSI?"),
            ("lora", "How does LoRa work?"),
            ("meshtastic", "Meshtastic basics"),
            ("reticulum", "Reticulum basics"),
            ("antenna", "Antenna selection"),
            ("range", "Improving range"),
            ("custom", "Custom query"),
            ("back", "Back"),
        ]

        while True:
            choice = self.ctx.dialog.menu(
                "Knowledge Base",
                "Select a topic or enter custom query:",
                topic_choices
            )

            if choice is None or choice == "back":
                break

            query = None
            if choice == "custom":
                query = self.ctx.dialog.inputbox(
                    "Knowledge Query",
                    "Enter your question about mesh networking:"
                )
                if not query:
                    continue
            else:
                query_map = {
                    "snr": "What is SNR?",
                    "rssi": "What is RSSI?",
                    "lora": "How does LoRa modulation work?",
                    "meshtastic": "What is Meshtastic and how does it work?",
                    "reticulum": "What is Reticulum Network Stack?",
                    "antenna": "How do I choose the right antenna?",
                    "range": "How can I improve my mesh range?",
                }
                query = query_map.get(choice, choice)

            self._query_knowledge(query)

    def _query_knowledge(self, query: str):
        """Query the knowledge base."""
        self.ctx.dialog.infobox("Searching", f"Searching: {query[:40]}...")

        if not _HAS_KNOWLEDGE:
            self.ctx.dialog.msgbox(
                "Error",
                "Knowledge base not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            kb = get_knowledge_base()
            results = kb.query(query)

            if results:
                # Format results for display
                result_lines = [f"QUERY: {query}", ""]

                for i, result in enumerate(results[:3], 1):
                    result_lines.append(f"--- Result {i}: {result.title} ---")
                    # Truncate content for dialog display
                    content = result.content.strip()
                    if len(content) > 800:
                        content = content[:800] + "..."
                    result_lines.append(content)
                    result_lines.append("")

                self.ctx.dialog.msgbox(
                    "Knowledge Base Results",
                    "\n".join(result_lines)
                )
            else:
                self.ctx.dialog.msgbox(
                    "No Results",
                    f"No knowledge base entries found for:\n{query}\n\n"
                    "Try different keywords or use Claude Assistant."
                )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Query failed: {e}")

    # =========================================================================
    # Claude Assistant
    # =========================================================================

    def _claude_assistant(self):
        """Interactive Claude Assistant for mesh help."""
        # Check for API key
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        mode = "PRO" if api_key else "Standalone"

        self.ctx.dialog.msgbox(
            "Claude Assistant",
            f"Mode: {mode}\n\n"
            f"{'PRO mode: Full Claude AI capabilities' if api_key else 'Standalone: Rule-based + knowledge base'}\n\n"
            f"{'Set ANTHROPIC_API_KEY for PRO features.' if not api_key else 'API key detected.'}"
        )

        while True:
            question = self.ctx.dialog.inputbox(
                f"Claude Assistant ({mode})",
                "Ask a question about mesh networking:\n(Enter blank to exit)"
            )

            if not question:
                break

            self._ask_assistant(question)

    def _ask_assistant(self, question: str):
        """Ask the Claude assistant."""
        self.ctx.dialog.infobox("Thinking", f"Processing: {question[:40]}...")

        if not _HAS_ASSISTANT:
            self.ctx.dialog.msgbox(
                "Error",
                "Claude assistant not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            assistant = ClaudeAssistant()
            response = assistant.ask(question)

            # Format response
            result_lines = [
                f"Q: {question}",
                "",
                "ANSWER:",
                response.answer,
                "",
            ]

            if response.suggested_actions:
                result_lines.append("SUGGESTED ACTIONS:")
                for action in response.suggested_actions[:3]:
                    result_lines.append(f"  - {action}")
                result_lines.append("")

            result_lines.append(f"Mode: {response.mode.value.upper()}")
            if response.confidence > 0:
                result_lines.append(f"Confidence: {response.confidence:.0%}")

            self.ctx.dialog.msgbox(
                "Claude Assistant",
                "\n".join(result_lines)
            )
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Assistant failed: {e}")

    # =========================================================================
    # Coverage Map
    # =========================================================================

    def _generate_coverage_map(self):
        """Generate a coverage map and open in browser."""
        # Get node data source
        source_choices = [
            ("all", "All sources (recommended)"),
            ("live", "Live from meshtasticd only"),
            ("mqtt", "From MQTT broker"),
            ("file", "From saved node file"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Coverage Map",
            "Select node data source:",
            source_choices
        )

        if choice is None or choice == "back":
            return

        self.ctx.dialog.infobox("Generating", "Creating coverage map...")

        if not _HAS_COVERAGE_MAP:
            self.ctx.dialog.msgbox(
                "Error",
                "Coverage map generator not available.\n\n"
                "You may need to install folium:\n"
                "pip3 install folium"
            )
            return

        try:
            from utils.paths import get_real_user_home

            generator = CoverageMapGenerator()

            if choice == "all":
                # Use MapDataCollector to get nodes from ALL sources
                # (meshtasticd, MQTT, node_cache.json, RNS cache)
                if not _HAS_MAP_SERVICE:
                    self.ctx.dialog.msgbox("Error", "MapDataCollector not available.")
                    return
                collector = MapDataCollector()
                geojson = collector.collect()
                features = geojson.get('features', [])
                if features:
                    generator.add_nodes_from_geojson(geojson)
                    self.ctx.dialog.infobox(
                        "Generating",
                        f"Found {len(features)} nodes from all sources..."
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "No Nodes",
                        "No nodes found from any source.\n\n"
                        "Check meshtasticd, MQTT, or node cache."
                    )
                    return

            elif choice == "live":
                # Get nodes from meshtasticd only
                geojson = self._get_nodes_geojson_by_source("meshtasticd")
                features = geojson.get('features', [])
                if features:
                    generator.add_nodes_from_geojson(geojson)
                    self.ctx.dialog.infobox(
                        "Generating",
                        f"Found {len(features)} nodes from meshtasticd..."
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "No Nodes",
                        "No nodes found from meshtasticd.\n\n"
                        "Ensure meshtasticd is running and has nodes with GPS."
                    )
                    return

            elif choice == "mqtt":
                # Get nodes from MQTT cache only
                geojson = self._get_nodes_geojson_by_source("mqtt")
                features = geojson.get('features', [])
                if features:
                    generator.add_nodes_from_geojson(geojson)
                    self.ctx.dialog.infobox(
                        "Generating",
                        f"Found {len(features)} nodes from MQTT..."
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "No Nodes",
                        "No nodes found from MQTT cache.\n\n"
                        "MQTT nodes are cached when monitoring is running."
                    )
                    return

            elif choice == "file":
                # Load from file
                file_path = self.ctx.dialog.inputbox(
                    "Node File",
                    "Enter path to node JSON file:"
                )
                if not file_path:
                    return
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    generator.add_nodes_from_geojson(data)
                except Exception as e:
                    self.ctx.dialog.msgbox("Error", f"Failed to load file: {e}")
                    return

            # Generate map
            output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "coverage_map.html"

            generator.generate(str(output_file))

            # Open in browser
            self.ctx.dialog.msgbox(
                "Map Generated",
                f"Coverage map saved to:\n{output_file}\n\n"
                "Opening in browser..."
            )

            # Open browser in background
            self._open_in_browser(str(output_file))

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Map generation failed: {e}")

    def _get_nodes_geojson_by_source(self, source: str) -> dict:
        """Get nodes from a specific source using MapDataCollector.

        Args:
            source: Source filter - "meshtasticd", "mqtt", or "rns"

        Returns:
            GeoJSON FeatureCollection filtered to the specified source.
        """
        if not _HAS_MAP_SERVICE:
            return {"type": "FeatureCollection", "features": []}

        try:
            collector = MapDataCollector()
            geojson = collector.collect()

            # Filter features by source
            filtered_features = [
                f for f in geojson.get('features', [])
                if f.get('properties', {}).get('source') == source
            ]

            return {
                "type": "FeatureCollection",
                "features": filtered_features,
                "properties": {
                    "source": source,
                    "count": len(filtered_features)
                }
            }
        except Exception as e:
            logger.debug("GeoJSON collection failed: %s", e)
            return {"type": "FeatureCollection", "features": []}

    def _open_in_browser(self, url: str):
        """Open URL in browser (in background thread).

        Handles running as root by using sudo -u to run browser as real user.
        On headless/SSH sessions, shows the URL for manual access instead.
        """
        # On headless/SSH, show URL instead of trying to open browser
        if self._is_headless():
            self.ctx.dialog.msgbox(
                "No Display",
                f"No graphical display detected (headless/SSH).\n\n"
                f"Open this URL in your local browser:\n{url}"
            )
            return

        def do_open():
            try:
                # When running as root, use sudo -u to run as real user
                real_user = os.environ.get('SUDO_USER')
                if os.geteuid() == 0 and real_user:
                    subprocess.run(
                        ['sudo', '-u', real_user, 'xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
                else:
                    # Not root or no SUDO_USER - try xdg-open directly
                    subprocess.run(
                        ['xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                try:
                    webbrowser.open(url)
                except (webbrowser.Error, OSError):
                    pass

        threading.Thread(target=do_open, daemon=True).start()

    # =========================================================================
    # Heatmap Generation
    # =========================================================================

    def _generate_heatmap(self):
        """Generate a node density heatmap and open in browser."""
        self.ctx.dialog.infobox("Generating", "Creating node density heatmap...")

        if not _HAS_COVERAGE_MAP:
            self.ctx.dialog.msgbox(
                "Error",
                "Coverage map generator not available.\n\n"
                "You may need to install folium:\n"
                "pip3 install folium"
            )
            return

        if not _HAS_MAP_SERVICE:
            self.ctx.dialog.msgbox("Error", "MapDataCollector not available.")
            return

        try:
            from utils.paths import get_real_user_home

            generator = CoverageMapGenerator()

            # Collect nodes from all sources
            collector = MapDataCollector()
            geojson = collector.collect()
            features = geojson.get('features', [])
            if features:
                generator.add_nodes_from_geojson(geojson)
            else:
                self.ctx.dialog.msgbox(
                    "No Nodes",
                    "No nodes found from any source.\n\n"
                    "Check meshtasticd, MQTT, or node cache."
                )
                return

            # Generate heatmap
            output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = str(output_dir / "coverage_heatmap.html")

            result_path = generator.generate_heatmap(output_path=output_file)

            if not result_path:
                import importlib.util
                if importlib.util.find_spec('folium'):
                    detail = (
                        "Folium is installed but heatmap generation returned empty.\n"
                        "Try restarting MeshForge to reload the module."
                    )
                else:
                    detail = (
                        "Folium with HeatMap plugin is required:\n"
                        "pip3 install folium"
                    )
                self.ctx.dialog.msgbox(
                    "Error",
                    f"Heatmap generation failed.\n\n{detail}"
                )
                return

            self.ctx.dialog.msgbox(
                "Heatmap Generated",
                f"Node density heatmap saved to:\n{result_path}\n\n"
                "Opening in browser..."
            )
            self._open_in_browser(result_path)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Heatmap generation failed: {e}")

    # =========================================================================
    # Tile Cache Manager
    # =========================================================================

    def _tile_cache_menu(self):
        """Manage offline tile cache for maps."""
        while True:
            choices = [
                ("stats", "Cache Stats         View tile cache status"),
                ("download", "Download Region     Cache tiles for area"),
                ("estimate", "Estimate Size       Preview download size"),
                ("clear", "Clear Expired       Remove old tiles"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Offline Tile Cache",
                "Manage cached map tiles for offline use:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "stats": ("Cache Stats", self._tile_cache_stats),
                "download": ("Download Region", self._tile_cache_download),
                "estimate": ("Estimate Size", self._tile_cache_estimate),
                "clear": ("Clear Expired", self._tile_cache_clear),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _tile_cache_stats(self):
        """Display tile cache statistics."""
        if not _HAS_TILE_CACHE:
            self.ctx.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            cache = TileCache()
            stats = cache.get_stats()

            info = [
                f"Cached Tiles: {stats['tile_count']}",
                f"Cache Size:   {stats['size_mb']:.1f} MB",
            ]
            if stats.get('oldest'):
                info.append(f"Oldest Tile:  {stats['oldest']}")
            if stats.get('newest'):
                info.append(f"Newest Tile:  {stats['newest']}")
            if stats['tile_count'] == 0:
                info.append("")
                info.append("No tiles cached yet. Use 'Download Region'")
                info.append("to cache tiles for offline map viewing.")

            self.ctx.dialog.msgbox("Tile Cache Stats", "\n".join(info))
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to get cache stats: {e}")

    def _tile_cache_download(self):
        """Download tiles for a geographic region."""
        if not _HAS_TILE_CACHE:
            self.ctx.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            # Get bounds from user
            region_choices = [
                ("hawaii", "Hawaii              (18.5-22.5N, 160.5-154.5W)"),
                ("custom", "Custom Region       Enter coordinates"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Download Region",
                "Select region to cache tiles for:",
                region_choices
            )

            if choice is None or choice == "back":
                return

            if choice == "hawaii":
                bounds = HAWAII_BOUNDS
            elif choice == "custom":
                coords = self.ctx.dialog.inputbox(
                    "Custom Region",
                    "Enter bounds as: south,west,north,east\n"
                    "Example: 21.0,-158.5,21.7,-157.5"
                )
                if not coords:
                    return
                try:
                    parts = [float(x.strip()) for x in coords.split(',')]
                    if len(parts) != 4:
                        self.ctx.dialog.msgbox("Error", "Enter exactly 4 coordinates.")
                        return
                    bounds = tuple(parts)
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid coordinates.")
                    return
            else:
                return

            # Estimate first
            estimate = TileCache.estimate_download_size(bounds)
            if 'error' in estimate:
                self.ctx.dialog.msgbox("Error", estimate['error'])
                return

            confirm = self.ctx.dialog.yesno(
                "Confirm Download",
                f"Tiles to download: {estimate['total_tiles']}\n"
                f"Estimated size: {estimate['estimated_mb']:.1f} MB\n\n"
                "Proceed with download?"
            )

            if not confirm:
                return

            self.ctx.dialog.infobox("Downloading", "Caching tiles... This may take a while.")

            cache = TileCache()
            result = cache.download_region(bounds)

            if 'error' in result:
                self.ctx.dialog.msgbox("Error", result['error'])
            else:
                self.ctx.dialog.msgbox(
                    "Download Complete",
                    f"Downloaded: {result['downloaded']} tiles\n"
                    f"Skipped (cached): {result['skipped']}\n"
                    f"Failed: {result['failed']}"
                )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Tile download failed: {e}")

    def _tile_cache_estimate(self):
        """Estimate download size for a region."""
        if not _HAS_TILE_CACHE:
            self.ctx.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            coords = self.ctx.dialog.inputbox(
                "Estimate Size",
                "Enter bounds as: south,west,north,east\n"
                "Example: 21.0,-158.5,21.7,-157.5\n"
                "(Leave empty for Hawaii)"
            )

            if coords:
                try:
                    parts = [float(x.strip()) for x in coords.split(',')]
                    if len(parts) != 4:
                        self.ctx.dialog.msgbox("Error", "Enter exactly 4 coordinates.")
                        return
                    bounds = tuple(parts)
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid coordinates.")
                    return
            else:
                bounds = HAWAII_BOUNDS

            estimate = TileCache.estimate_download_size(bounds)

            if 'error' in estimate:
                self.ctx.dialog.msgbox("Error", estimate['error'])
            else:
                self.ctx.dialog.msgbox(
                    "Download Estimate",
                    f"Region: ({bounds[0]:.1f}, {bounds[1]:.1f}) to "
                    f"({bounds[2]:.1f}, {bounds[3]:.1f})\n"
                    f"Tile count: {estimate['total_tiles']}\n"
                    f"Estimated size: {estimate['estimated_mb']:.1f} MB\n"
                    f"Within limit: {'Yes' if estimate['within_limit'] else 'No'}"
                )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Estimation failed: {e}")

    def _tile_cache_clear(self):
        """Clear expired tiles from cache."""
        if not _HAS_TILE_CACHE:
            self.ctx.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            confirm = self.ctx.dialog.yesno(
                "Clear Expired Tiles",
                "Remove tiles older than 30 days?\n\n"
                "This frees disk space but requires re-download\n"
                "for offline use."
            )

            if not confirm:
                return

            cache = TileCache()
            result = cache.clear_expired()

            freed_mb = result['bytes_freed'] / (1024 * 1024)
            self.ctx.dialog.msgbox(
                "Cache Cleared",
                f"Removed: {result['removed']} expired tiles\n"
                f"Space freed: {freed_mb:.1f} MB"
            )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Cache clear failed: {e}")
