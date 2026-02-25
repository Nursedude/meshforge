"""
AI Tools Mixin for MeshForge Launcher TUI.

Provides AI-powered diagnostics, knowledge base queries, and coverage map
generation for the TUI launcher.

Features:
- Intelligent Diagnostics (rule-based symptom analysis)
- Knowledge Base (mesh networking concepts and troubleshooting)
- Claude Assistant (PRO mode with API key)
- Coverage Map Generation (opens in browser)
"""

import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional

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
ReviewOrchestrator, ReviewScope, _HAS_AUTO_REVIEW = safe_import(
    'utils.auto_review', 'ReviewOrchestrator', 'ReviewScope'
)

# Import service helpers for privileged systemctl calls
from utils.service_check import _sudo_cmd, start_service

logger = logging.getLogger(__name__)


class AIToolsMixin:
    """Mixin providing AI tools for the TUI launcher."""

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
            choice = self.dialog.menu(
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
                self._safe_call(*entry)

    def _maybe_auto_start_map(self):
        """Start map server on TUI launch if user has enabled auto-open.

        Prefers systemd service (meshforge-map) for reliability.
        Falls back to in-process server if systemd unavailable.
        """
        import json
        import socket

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
            import logging
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
        import subprocess
        import socket
        import time

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

    def _open_live_map(self):
        """Open the live network map with real node data."""
        import json
        import socket

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

            choice = self.dialog.menu(
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
                self._safe_call(*entry)

    def _open_live_map_browser(self):
        """Generate browser snapshot of the live map with current node data."""
        # Browser mode: collect data, inject into HTML, open
        self.dialog.infobox("Loading", "Collecting node data from all sources...")

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
                self.dialog.msgbox(
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
            self.dialog.msgbox("Live Map", msg)
            self._open_in_browser(f"file://{output_file}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to generate live map: {e}")

    def _is_headless(self) -> bool:
        """Check if running without a display (headless/SSH)."""
        import os
        display = os.environ.get('DISPLAY')
        wayland = os.environ.get('WAYLAND_DISPLAY')
        ssh = os.environ.get('SSH_CONNECTION')
        return (not display and not wayland) or bool(ssh)

    def _start_map_server(self):
        """Start the map HTTP server for live-updating browser access.

        Prefers systemd service (meshforge-map) for reliability.
        Falls back to in-process server if systemd unavailable.
        """
        import socket
        import time

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
                self.dialog.msgbox(
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
            self.dialog.msgbox(
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
            import logging
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
            self.dialog.msgbox("Map Server Started", msg)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to start map server: {e}")

    def _try_start_map_service(self) -> bool:
        """Try to start map server via systemd service.

        Returns True if service started successfully.
        """
        import subprocess
        import socket
        import time

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
        import subprocess

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
        import json

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

            self.dialog.msgbox("Map Settings", msg)
        except OSError as e:
            self.dialog.msgbox("Error", f"Failed to save setting: {e}")

        # Return to caller — _open_live_map loop will re-show menu

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
            choice = self.dialog.menu(
                "Intelligent Diagnostics",
                "Select a symptom to diagnose:",
                symptom_choices
            )

            if choice is None or choice == "back":
                break

            symptom_text = None
            if choice == "custom":
                symptom_text = self.dialog.inputbox(
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
        self.dialog.infobox("Analyzing", f"Analyzing: {symptom[:40]}...")

        if not _HAS_DIAGNOSTICS:
            self.dialog.msgbox(
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

                self.dialog.msgbox(
                    "Diagnosis Result",
                    "\n".join(result_lines)
                )
            else:
                self.dialog.msgbox(
                    "Diagnosis",
                    f"No specific diagnosis found for:\n{symptom}\n\n"
                    "Try the Knowledge Base for general information,\n"
                    "or use Claude Assistant for detailed help."
                )
        except Exception as e:
            self.dialog.msgbox("Error", f"Diagnosis failed: {e}")

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
            choice = self.dialog.menu(
                "Knowledge Base",
                "Select a topic or enter custom query:",
                topic_choices
            )

            if choice is None or choice == "back":
                break

            query = None
            if choice == "custom":
                query = self.dialog.inputbox(
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
        self.dialog.infobox("Searching", f"Searching: {query[:40]}...")

        if not _HAS_KNOWLEDGE:
            self.dialog.msgbox(
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

                self.dialog.msgbox(
                    "Knowledge Base Results",
                    "\n".join(result_lines)
                )
            else:
                self.dialog.msgbox(
                    "No Results",
                    f"No knowledge base entries found for:\n{query}\n\n"
                    "Try different keywords or use Claude Assistant."
                )
        except Exception as e:
            self.dialog.msgbox("Error", f"Query failed: {e}")

    def _claude_assistant(self):
        """Interactive Claude Assistant for mesh help."""
        # Check for API key
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        mode = "PRO" if api_key else "Standalone"

        self.dialog.msgbox(
            "Claude Assistant",
            f"Mode: {mode}\n\n"
            f"{'PRO mode: Full Claude AI capabilities' if api_key else 'Standalone: Rule-based + knowledge base'}\n\n"
            f"{'Set ANTHROPIC_API_KEY for PRO features.' if not api_key else 'API key detected.'}"
        )

        while True:
            question = self.dialog.inputbox(
                f"Claude Assistant ({mode})",
                "Ask a question about mesh networking:\n(Enter blank to exit)"
            )

            if not question:
                break

            self._ask_assistant(question)

    def _ask_assistant(self, question: str):
        """Ask the Claude assistant."""
        self.dialog.infobox("Thinking", f"Processing: {question[:40]}...")

        if not _HAS_ASSISTANT:
            self.dialog.msgbox(
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

            self.dialog.msgbox(
                "Claude Assistant",
                "\n".join(result_lines)
            )
        except Exception as e:
            self.dialog.msgbox("Error", f"Assistant failed: {e}")

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

        choice = self.dialog.menu(
            "Coverage Map",
            "Select node data source:",
            source_choices
        )

        if choice is None or choice == "back":
            return

        self.dialog.infobox("Generating", "Creating coverage map...")

        if not _HAS_COVERAGE_MAP:
            self.dialog.msgbox(
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
                    self.dialog.msgbox("Error", "MapDataCollector not available.")
                    return
                collector = MapDataCollector()
                geojson = collector.collect()
                features = geojson.get('features', [])
                if features:
                    generator.add_nodes_from_geojson(geojson)
                    self.dialog.infobox(
                        "Generating",
                        f"Found {len(features)} nodes from all sources..."
                    )
                else:
                    self.dialog.msgbox(
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
                    self.dialog.infobox(
                        "Generating",
                        f"Found {len(features)} nodes from meshtasticd..."
                    )
                else:
                    self.dialog.msgbox(
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
                    self.dialog.infobox(
                        "Generating",
                        f"Found {len(features)} nodes from MQTT..."
                    )
                else:
                    self.dialog.msgbox(
                        "No Nodes",
                        "No nodes found from MQTT cache.\n\n"
                        "MQTT nodes are cached when monitoring is running."
                    )
                    return

            elif choice == "file":
                # Load from file
                file_path = self.dialog.inputbox(
                    "Node File",
                    "Enter path to node JSON file:"
                )
                if not file_path:
                    return
                try:
                    import json
                    with open(file_path) as f:
                        data = json.load(f)
                    generator.add_nodes_from_geojson(data)
                except Exception as e:
                    self.dialog.msgbox("Error", f"Failed to load file: {e}")
                    return

            # Generate map
            output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "coverage_map.html"

            generator.generate(str(output_file))

            # Open in browser
            self.dialog.msgbox(
                "Map Generated",
                f"Coverage map saved to:\n{output_file}\n\n"
                "Opening in browser..."
            )

            # Open browser in background
            self._open_in_browser(str(output_file))

        except Exception as e:
            self.dialog.msgbox("Error", f"Map generation failed: {e}")

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
        import threading
        import os

        # On headless/SSH, show URL instead of trying to open browser
        if self._is_headless():
            self.dialog.msgbox(
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
        self.dialog.infobox("Generating", "Creating node density heatmap...")

        if not _HAS_COVERAGE_MAP:
            self.dialog.msgbox(
                "Error",
                "Coverage map generator not available.\n\n"
                "You may need to install folium:\n"
                "pip3 install folium"
            )
            return

        if not _HAS_MAP_SERVICE:
            self.dialog.msgbox("Error", "MapDataCollector not available.")
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
                self.dialog.msgbox(
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
                self.dialog.msgbox(
                    "Error",
                    "Heatmap generation failed.\n\n"
                    "Folium with HeatMap plugin is required:\n"
                    "pip3 install folium"
                )
                return

            self.dialog.msgbox(
                "Heatmap Generated",
                f"Node density heatmap saved to:\n{result_path}\n\n"
                "Opening in browser..."
            )
            self._open_in_browser(result_path)

        except Exception as e:
            self.dialog.msgbox("Error", f"Heatmap generation failed: {e}")

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

            choice = self.dialog.menu(
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
                self._safe_call(*entry)

    def _tile_cache_stats(self):
        """Display tile cache statistics."""
        if not _HAS_TILE_CACHE:
            self.dialog.msgbox("Error", "Tile cache module not available.")
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

            self.dialog.msgbox("Tile Cache Stats", "\n".join(info))
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get cache stats: {e}")

    def _tile_cache_download(self):
        """Download tiles for a geographic region."""
        if not _HAS_TILE_CACHE:
            self.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            # Get bounds from user
            region_choices = [
                ("hawaii", "Hawaii              (18.5-22.5N, 160.5-154.5W)"),
                ("custom", "Custom Region       Enter coordinates"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Download Region",
                "Select region to cache tiles for:",
                region_choices
            )

            if choice is None or choice == "back":
                return

            if choice == "hawaii":
                bounds = HAWAII_BOUNDS
            elif choice == "custom":
                coords = self.dialog.inputbox(
                    "Custom Region",
                    "Enter bounds as: south,west,north,east\n"
                    "Example: 21.0,-158.5,21.7,-157.5"
                )
                if not coords:
                    return
                try:
                    parts = [float(x.strip()) for x in coords.split(',')]
                    if len(parts) != 4:
                        self.dialog.msgbox("Error", "Enter exactly 4 coordinates.")
                        return
                    bounds = tuple(parts)
                except ValueError:
                    self.dialog.msgbox("Error", "Invalid coordinates.")
                    return
            else:
                return

            # Estimate first
            estimate = TileCache.estimate_download_size(bounds)
            if 'error' in estimate:
                self.dialog.msgbox("Error", estimate['error'])
                return

            confirm = self.dialog.yesno(
                "Confirm Download",
                f"Tiles to download: {estimate['total_tiles']}\n"
                f"Estimated size: {estimate['estimated_mb']:.1f} MB\n\n"
                "Proceed with download?"
            )

            if not confirm:
                return

            self.dialog.infobox("Downloading", "Caching tiles... This may take a while.")

            cache = TileCache()
            result = cache.download_region(bounds)

            if 'error' in result:
                self.dialog.msgbox("Error", result['error'])
            else:
                self.dialog.msgbox(
                    "Download Complete",
                    f"Downloaded: {result['downloaded']} tiles\n"
                    f"Skipped (cached): {result['skipped']}\n"
                    f"Failed: {result['failed']}"
                )

        except Exception as e:
            self.dialog.msgbox("Error", f"Tile download failed: {e}")

    def _tile_cache_estimate(self):
        """Estimate download size for a region."""
        if not _HAS_TILE_CACHE:
            self.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            coords = self.dialog.inputbox(
                "Estimate Size",
                "Enter bounds as: south,west,north,east\n"
                "Example: 21.0,-158.5,21.7,-157.5\n"
                "(Leave empty for Hawaii)"
            )

            if coords:
                try:
                    parts = [float(x.strip()) for x in coords.split(',')]
                    if len(parts) != 4:
                        self.dialog.msgbox("Error", "Enter exactly 4 coordinates.")
                        return
                    bounds = tuple(parts)
                except ValueError:
                    self.dialog.msgbox("Error", "Invalid coordinates.")
                    return
            else:
                bounds = HAWAII_BOUNDS

            estimate = TileCache.estimate_download_size(bounds)

            if 'error' in estimate:
                self.dialog.msgbox("Error", estimate['error'])
            else:
                self.dialog.msgbox(
                    "Download Estimate",
                    f"Region: ({bounds[0]:.1f}, {bounds[1]:.1f}) to "
                    f"({bounds[2]:.1f}, {bounds[3]:.1f})\n"
                    f"Tile count: {estimate['total_tiles']}\n"
                    f"Estimated size: {estimate['estimated_mb']:.1f} MB\n"
                    f"Within limit: {'Yes' if estimate['within_limit'] else 'No'}"
                )

        except Exception as e:
            self.dialog.msgbox("Error", f"Estimation failed: {e}")

    def _tile_cache_clear(self):
        """Clear expired tiles from cache."""
        if not _HAS_TILE_CACHE:
            self.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            confirm = self.dialog.yesno(
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
            self.dialog.msgbox(
                "Cache Cleared",
                f"Removed: {result['removed']} expired tiles\n"
                f"Space freed: {freed_mb:.1f} MB"
            )

        except Exception as e:
            self.dialog.msgbox("Error", f"Cache clear failed: {e}")

    # =========================================================================
    # Auto-Review System
    # =========================================================================

    def _auto_review_menu(self):
        """Code review system - run automated code analysis."""
        while True:
            choices = [
                ("full", "Full Review         Run all review agents"),
                ("security", "Security Review     Command injection, creds"),
                ("redundancy", "Redundancy Review   Duplicate code, imports"),
                ("performance", "Performance Review  Timeouts, loops, memory"),
                ("reliability", "Reliability Review  Error handling, TODOs"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Code Review",
                "Automated code analysis agents:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "full": ("Full Review", lambda: self._run_auto_review("ALL")),
                "security": ("Security Review", lambda: self._run_auto_review("SECURITY")),
                "redundancy": ("Redundancy Review", lambda: self._run_auto_review("REDUNDANCY")),
                "performance": ("Performance Review", lambda: self._run_auto_review("PERFORMANCE")),
                "reliability": ("Reliability Review", lambda: self._run_auto_review("RELIABILITY")),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _run_auto_review(self, scope_name: str):
        """Execute an auto-review with the specified scope."""
        self.dialog.infobox("Reviewing", f"Running {scope_name.lower()} review...")

        if not _HAS_AUTO_REVIEW:
            self.dialog.msgbox(
                "Error",
                "Auto-review module not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            scope_map = {
                "ALL": ReviewScope.ALL,
                "SECURITY": ReviewScope.SECURITY,
                "REDUNDANCY": ReviewScope.REDUNDANCY,
                "PERFORMANCE": ReviewScope.PERFORMANCE,
                "RELIABILITY": ReviewScope.RELIABILITY,
            }
            scope = scope_map.get(scope_name, ReviewScope.ALL)

            orchestrator = ReviewOrchestrator()
            report = orchestrator.run_full_review(scope=scope)

            # Build summary
            lines = [
                f"Scope: {scope_name}",
                f"Files Scanned: {report.total_files_scanned}",
                f"Total Issues: {report.total_issues}",
                f"Fixes Applied: {report.total_fixes_applied}",
                "",
            ]

            for category, result in report.agent_results.items():
                lines.append(
                    f"  {category.value.upper()}: "
                    f"{result.total_issues} issues "
                    f"({result.critical_count} critical, "
                    f"{result.high_count} high)"
                )

            # Show top findings
            findings = report.get_all_findings()
            if findings:
                lines.append("")
                lines.append("Top findings:")
                for finding in findings[:10]:
                    lines.append(
                        f"  [{finding.severity.value.upper()}] "
                        f"{finding.file_path}:{finding.line_number or '?'}"
                    )
                    lines.append(f"    {finding.issue}")

                if len(findings) > 10:
                    lines.append(f"  ... and {len(findings) - 10} more")

            self.dialog.msgbox("Review Results", "\n".join(lines))

        except Exception as e:
            self.dialog.msgbox("Error", f"Review failed: {e}")
