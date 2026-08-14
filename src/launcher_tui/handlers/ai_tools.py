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

# Map service helpers are imported inside the methods that use them:
# utils.map_data_service transitively pulls the RNS/LXMF collector stack
# (~700 modules, ~400 ms) and must not load at TUI startup.

# Privileged systemctl helpers used by the live-map control methods.
from utils.service_check import (
    _sudo_cmd,
    check_service as _check_service,
    is_service_unit_installed,
    start_service,
    stop_service,
)

# Extracted handler mixins (file-size compliance, CLAUDE.md #6).
from ._ai_tools_mfmaps import MeshForgeMapsExtensionMixin
from ._ai_tools_diagnostics import DiagnosticsAndAssistantMixin
from ._ai_tools_coverage import CoverageMapAndHeatmapMixin
from ._ai_tools_tilecache import TileCacheMixin

# Service controlled by the map-server TUI actions. Centralized so it can't
# drift — see Issue #29 (no raw systemctl for state changes).
MAP_SERVER_SERVICE = "meshforge-map"

logger = logging.getLogger(__name__)


class AIToolsHandler(
    MeshForgeMapsExtensionMixin,
    DiagnosticsAndAssistantMixin,
    CoverageMapAndHeatmapMixin,
    TileCacheMixin,
    BaseHandler,
):
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

    def on_shutdown(self):
        """Stop the in-process fallback map server, if this TUI started one.

        Without this hook the handler is on_startup-only, which
        ``startup_all()``'s LifecycleHandler isinstance (both hooks) skips —
        so the map auto-start silently never ran (step-2 review FINDING-3).
        The systemd-owned server (meshforge-map) is deliberately left alone;
        it outlives the TUI.
        """
        server = getattr(self, "_map_server", None)
        if server:
            try:
                server.stop()
            except Exception as e:
                logger.warning("In-process map server stop failed: %s", e)
            self._map_server = None

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
        from utils.service_check import check_port
        if check_port(5000, timeout=1):
            return  # Already running

        # If the systemd unit is installed, IT owns :5000. Do not fall back
        # to an in-process server even if systemd fails to start — an
        # in-process bind would race systemd's Restart=always loop forever,
        # leaving :5000 pinned to this TUI process while the service unit
        # keeps failing (2026-04-22 service-supervision incident). If systemd
        # is broken, the operator needs to see it — not be quietly papered over.
        if is_service_unit_installed(MAP_SERVER_SERVICE):
            self._try_start_map_service(polls=5)
            return

        # No systemd unit installed on this box — in-process fallback only.
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

    def _try_start_map_service(self, polls: int = 6) -> bool:
        """Start the map server via systemd and wait for :5000 to answer.

        Single implementation (Q2 dedup 2026-08-14): this method existed
        twice in this file differing only in the poll count, each with its
        own inline `systemctl is-enabled` + raw socket probe. Enablement
        goes through service_enabled_here (never start an intentionally-off
        unit — the moc3 doctrine) and the probe through check_port.
        """
        try:
            from service_remediation import service_enabled_here
            from utils.service_check import check_port
            if not service_enabled_here('meshforge-map'):
                return False  # absent or intentionally disabled on this box

            start_service('meshforge-map')

            for _ in range(polls):
                time.sleep(0.5)
                if check_port(5000, timeout=1):
                    return True
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
                except (json.JSONDecodeError, OSError) as e:
                    logger.debug("Settings load failed: %s", e)

            auto_label = "ON" if auto_enabled else "OFF"
            running = self._is_map_server_running()
            status_label = "RUNNING" if running else "STOPPED"
            choices = [
                ("browser", "Open map in browser (snapshot)"),
                ("start", "Start map server"),
                ("stop", "Stop map server"),
                ("restart", "Restart map server"),
                ("logs", "View map server logs"),
                ("autostart", f"Auto-open on launch [{auto_label}]"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                f"Live Network Map [{status_label}]",
                "Select map mode:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "browser": ("Browser Map Snapshot", self._open_live_map_browser),
                "start": ("Start Map Server", self._start_map_server),
                "stop": ("Stop Map Server", self._stop_map_server),
                "restart": ("Restart Map Server", self._restart_map_server),
                "logs": ("Map Server Logs", self._view_map_server_logs),
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
        from utils.service_check import check_port
        if check_port(port, host='127.0.0.1', timeout=1):
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

        # If the systemd unit is installed, IT owns :5000 — try systemd and
        # do NOT fall back to in-process. An in-process bind would collide
        # with systemd's Restart=always loop and leave the service stuck.
        # If systemd fails, surface it so the operator can fix the unit.
        if is_service_unit_installed(MAP_SERVER_SERVICE):
            if self._try_start_map_service():
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
            self.ctx.dialog.msgbox(
                "Map Server Failed to Start",
                "meshforge-map.service is installed but failed to start.\n\n"
                "Diagnose: use 'View Map Server Logs' in this menu to see\n"
                "the captured logs in-app.\n\n"
                "Common causes:\n"
                "  - Port 5000 already bound by another process\n"
                "  - Python import error (check dependencies)\n"
                "  - Bad config in ~/.config/meshforge/\n\n"
                "The TUI will not start an in-process server while the\n"
                "systemd unit is installed (avoids port conflicts that\n"
                "trap the service in a restart loop)."
            )
            return

        # No systemd unit installed on this box — in-process is the only option.
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

    # (second _try_start_map_service copy deleted 2026-08-14 — Q2/E5; the
    # single implementation lives above)

    def _get_map_service_status(self) -> str:
        """Get map server service status for display (read-only)."""
        try:
            status = _check_service(MAP_SERVER_SERVICE)
            if status.available:
                return "systemd service (active)"
            if status.state.name == "NOT_RUNNING":
                return "in-process (TUI)"
            return f"systemd ({status.state.name.lower()})"
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Map service status check failed: %s", e)
            return "in-process (TUI)"

    def _is_map_server_running(self) -> bool:
        """Check if map server is listening on port 5000."""
        from utils.service_check import check_port
        return check_port(5000, host='127.0.0.1', timeout=1)

    def _stop_map_server(self):
        """Stop the running map server."""
        if not self._is_map_server_running():
            self.ctx.dialog.msgbox("Map Server", "Map server is not running.")
            return

        # Try systemd first via the central service helper (Issue #29).
        try:
            status = _check_service(MAP_SERVER_SERVICE)
            if status.available:
                ok, msg = stop_service(MAP_SERVER_SERVICE)
                if ok:
                    self.ctx.dialog.msgbox(
                        "Map Server", "Map server service stopped."
                    )
                    return
                logger.debug("stop_service(%s) failed: %s", MAP_SERVER_SERVICE, msg)
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("service stop failed: %s", e)

        # Stop in-process server
        if hasattr(self, '_map_server') and self._map_server:
            try:
                self._map_server.stop()
                self._map_server = None
                self.ctx.dialog.msgbox("Map Server", "In-process map server stopped.")
                return
            except Exception as e:
                logger.debug("In-process stop failed: %s", e)

        # Last resort: find and kill the process on port 5000
        try:
            result = subprocess.run(
                ['fuser', '5000/tcp'],
                capture_output=True, text=True, timeout=5
            )
            pids = result.stdout.strip().split()
            if pids:
                for pid in pids:
                    pid = pid.strip()
                    if pid.isdigit():
                        subprocess.run(['kill', pid], timeout=5)
                self.ctx.dialog.msgbox("Map Server", "Map server process stopped.")
                return
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("fuser/kill failed: %s", e)

        self.ctx.dialog.msgbox("Map Server", "Could not stop map server.")

    def _restart_map_server(self):
        """Restart the map server (stop then start)."""
        if self._is_map_server_running():
            self._stop_map_server()
            time.sleep(1)
        self._start_map_server()

    def _view_map_server_logs(self):
        """View map server logs."""
        log_lines = ""

        # Try systemd journal first (read-only — journalctl does not change state)
        try:
            result = subprocess.run(
                ['journalctl', '-u', MAP_SERVER_SERVICE, '-n', '50', '--no-pager'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                log_lines = result.stdout.strip()
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("journalctl failed: %s", e)

        if not log_lines:
            # Check if server is running at all
            if self._is_map_server_running():
                status = self._get_map_service_status()
                log_lines = (
                    f"Map server is running ({status})\n"
                    f"No systemd journal available.\n\n"
                    f"For in-process servers, logs go to the\n"
                    f"MeshForge log file or console."
                )
            else:
                log_lines = "Map server is not running.\nNo logs available."

        self.ctx.dialog.msgbox("Map Server Logs (last 50 lines)", log_lines)

    def _toggle_auto_map(self):
        """Toggle the auto-open map on launch setting."""
        settings_file = self._get_map_settings_file()
        settings = {}

        if settings_file.exists():
            try:
                with open(settings_file) as f:
                    settings = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("Settings load failed: %s", e)

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
                # The setting above is already saved — an unimportable map
                # stack must not raise past the OSError handler and misreport
                # the committed toggle as failed (#74 class).
                try:
                    from utils.map_data_service import get_all_ips
                    ips = get_all_ips()
                    urls = ", ".join(f"http://{ip}:5000" for ip in ips[:2])
                    if len(ips) > 2:
                        urls += ", ..."
                except ImportError:
                    urls = "http://<this-box>:5000 (map stack not installed yet)"
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
