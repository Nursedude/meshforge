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

Coverage map, heatmap, terrain, and tile cache functions are in _ai_maps.py.
"""

import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

from handler_protocol import BaseHandler
from utils.safe_import import safe_import

from . import _ai_maps

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
            ("coverage",  "Coverage Map        Generate coverage map",  None),
            ("heatmap",   "Heatmap             Signal quality / density", None),
            ("terrain",   "Terrain Coverage    RF prediction with terrain", None),
            ("tiles",     "Offline Tiles       Cache map tiles",        None),
            ("ai",        "AI Diagnostics      Knowledge base, assistant", None),
        ]

    def execute(self, action):
        dispatch = {
            "livemap": ("Live NOC Map", self._open_live_map),
            "coverage": ("Coverage Map", self._generate_coverage_map),
            "heatmap": ("Heatmap", self._generate_heatmap),
            "terrain": ("Terrain Coverage", self._generate_terrain_coverage),
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
        ]
        dispatch = {
            "livemap": ("Live Network Map", self._open_live_map),
            "diagnose": ("Intelligent Diagnostics", self._intelligent_diagnostics),
            "knowledge": ("Knowledge Base Query", self._knowledge_base_query),
            "assistant": ("Claude Assistant", self._claude_assistant),
            "coverage": ("Coverage Map", self._generate_coverage_map),
        }
        self.run_menu_loop("Maps & Coverage", "Network mapping and analysis tools:", choices, dispatch)

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
        def _map_choices():
            auto_enabled = False
            settings_file = self._get_map_settings_file()
            if settings_file.exists():
                try:
                    with open(settings_file) as f:
                        auto_enabled = json.load(f).get("auto_open_map", False)
                except (json.JSONDecodeError, OSError):
                    pass
            auto_label = "ON" if auto_enabled else "OFF"
            return [
                ("browser", "Open map in browser (snapshot)"),
                ("server", "Start map server (live updates)"),
                ("autostart", f"Auto-open on launch [{auto_label}]"),
            ]

        dispatch = {
            "browser": ("Browser Map Snapshot", self._open_live_map_browser),
            "server": ("Map Server", self._start_map_server),
            "autostart": ("Toggle Auto-open", self._toggle_auto_map),
        }
        self.run_menu_loop("Live Network Map", "Select map mode:", _map_choices, dispatch)

    def _open_live_map_browser(self):
        """Generate browser snapshot of the live map with current node data."""
        _ai_maps.open_live_map_browser(self)

    def _is_headless(self) -> bool:
        """Check if running without a display (headless/SSH)."""
        display = os.environ.get('DISPLAY')
        wayland = os.environ.get('WAYLAND_DISPLAY')
        ssh = os.environ.get('SSH_CONNECTION')
        return (not display and not wayland) or bool(ssh)

    def _start_map_server(self):
        """Start the map HTTP server for live-updating browser access."""
        _ai_maps.start_map_server(self)

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
        choices = [
            ("connection", "Connection refused to meshtasticd"),
            ("no_nodes", "No nodes visible in mesh"),
            ("weak_signal", "Weak signal / low SNR"),
            ("timeout", "Message timeouts"),
            ("service", "Service not starting"),
            ("custom", "Describe custom symptom"),
        ]
        dispatch = {
            "connection": ("Diagnose Connection", self._run_diagnosis, "Connection refused to meshtasticd on port 4403"),
            "no_nodes": ("Diagnose No Nodes", self._run_diagnosis, "No nodes visible in mesh network"),
            "weak_signal": ("Diagnose Weak Signal", self._run_diagnosis, "Weak signal with low SNR values"),
            "timeout": ("Diagnose Timeouts", self._run_diagnosis, "Message timeouts when sending"),
            "service": ("Diagnose Service", self._run_diagnosis, "Service meshtasticd failed to start"),
            "custom": ("Custom Symptom", self._diagnose_custom),
        }
        self.run_menu_loop("Intelligent Diagnostics", "Select a symptom to diagnose:", choices, dispatch)

    def _diagnose_custom(self):
        """Prompt for a custom symptom and run diagnosis."""
        symptom_text = self.ctx.dialog.inputbox(
            "Custom Symptom",
            "Describe the issue you're experiencing:"
        )
        if symptom_text:
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
        choices = [
            ("snr", "What is SNR?"),
            ("rssi", "What is RSSI?"),
            ("lora", "How does LoRa work?"),
            ("meshtastic", "Meshtastic basics"),
            ("reticulum", "Reticulum basics"),
            ("antenna", "Antenna selection"),
            ("range", "Improving range"),
            ("custom", "Custom query"),
        ]
        dispatch = {
            "snr": ("Knowledge: SNR", self._query_knowledge, "What is SNR?"),
            "rssi": ("Knowledge: RSSI", self._query_knowledge, "What is RSSI?"),
            "lora": ("Knowledge: LoRa", self._query_knowledge, "How does LoRa modulation work?"),
            "meshtastic": ("Knowledge: Meshtastic", self._query_knowledge, "What is Meshtastic and how does it work?"),
            "reticulum": ("Knowledge: Reticulum", self._query_knowledge, "What is Reticulum Network Stack?"),
            "antenna": ("Knowledge: Antenna", self._query_knowledge, "How do I choose the right antenna?"),
            "range": ("Knowledge: Range", self._query_knowledge, "How can I improve my mesh range?"),
            "custom": ("Custom Query", self._knowledge_custom_query),
        }
        self.run_menu_loop("Knowledge Base", "Select a topic or enter custom query:", choices, dispatch)

    def _knowledge_custom_query(self):
        """Prompt for a custom knowledge base query."""
        query = self.ctx.dialog.inputbox(
            "Knowledge Query",
            "Enter your question about mesh networking:"
        )
        if query:
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
    # Coverage Map, Heatmap, Terrain, Tile Cache — delegated to _ai_maps.py
    # =========================================================================

    def _generate_coverage_map(self):
        """Generate a coverage map and open in browser."""
        _ai_maps.generate_coverage_map(self)

    def _get_nodes_geojson_by_source(self, source: str) -> dict:
        """Get nodes from a specific source using MapDataCollector."""
        return _ai_maps.get_nodes_geojson_by_source(self, source)

    def _open_in_browser(self, url: str):
        """Open URL in browser (in background thread)."""
        _ai_maps.open_in_browser(self, url)

    def _generate_heatmap(self):
        """Generate a heatmap weighted by density or signal quality."""
        _ai_maps.generate_heatmap(self)

    def _generate_terrain_coverage(self):
        """Generate terrain-aware RF coverage prediction."""
        _ai_maps.generate_terrain_coverage(self)

    def _tile_cache_menu(self):
        """Manage offline tile cache for maps."""
        _ai_maps.tile_cache_menu(self)

    def _tile_cache_stats(self):
        """Display tile cache statistics."""
        _ai_maps.tile_cache_stats(self)

    def _tile_cache_download(self):
        """Download tiles for a geographic region."""
        _ai_maps.tile_cache_download(self)

    def _tile_cache_estimate(self):
        """Estimate download size for a region."""
        _ai_maps.tile_cache_estimate(self)

    def _tile_cache_clear(self):
        """Clear expired tiles from cache."""
        _ai_maps.tile_cache_clear(self)
