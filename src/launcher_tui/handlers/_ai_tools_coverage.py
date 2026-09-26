"""Coverage map and node-density heatmap menus for AIToolsHandler.

Extracted from ai_tools.py for file size compliance (CLAUDE.md #6).

Host class must provide:
- self.ctx (TUIContext)
- self._open_in_browser(url)
- self._is_headless()
"""

import json
import logging
import os
import subprocess
import threading
import webbrowser

logger = logging.getLogger(__name__)

#: Coverage-map source choice -> the collector's `source_origin` tag
#: (utils.map_data_collector._tag_source_origin). One table, so a new origin
#: name fails a test rather than silently matching nothing.
_ORIGIN_FOR_SOURCE = {
    "meshtasticd": "local_radio",
    "mqtt": "mqtt_local",
    "rns": "rns_path_table",
}


def _load_coverage_map_generator():
    """Deferred import: utils.coverage_map needs folium (optional dep).

    Returns the class, or None when folium is missing (minimal-deps profile).
    """
    try:
        from utils.coverage_map import CoverageMapGenerator
        return CoverageMapGenerator
    except ImportError:
        return None


def _load_map_data_collector():
    """Deferred import: utils.map_data_service transitively pulls the RNS/LXMF
    collector stack (~1000 modules, ~900 ms — the single largest TUI startup
    cost when this lived at module level). The cost belongs to the map menus,
    not the launcher. Returns the class, or None when the map stack is missing.
    """
    try:
        from utils.map_data_service import MapDataCollector
        return MapDataCollector
    except ImportError:
        return None


#: The running map service's merged view. Loopback is always inside its read
#: gate. 30 s: a federated box serves ~30 MB (measured 2026-09-26).
_SERVICE_GEOJSON_URL = "http://127.0.0.1:5000/api/nodes/geojson"


def _collect_geojson():
    """(geojson, via) — the running map service's collection when :5000
    answers, else an in-process MapDataCollector; (None, reason) when neither.

    Why the service first (2026-09-26): an in-process collector reads
    meshtasticd over TCP :4403 guarded only by an in-process threading lock,
    so the TUI opened a SECOND PhoneAPI client beside the map service's own
    (the #17 single-consumer class). The service already holds the merged
    answer; reading it touches no radio.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(_SERVICE_GEOJSON_URL, timeout=30) as r:
            data = json.load(r)
        if isinstance(data, dict) and isinstance(data.get("features"), list):
            return data, "running map service (:5000)"
        logger.debug("map service geojson had no feature list; collecting in-process")
    except (OSError, ValueError) as e:
        logger.debug("map service geojson unavailable (%s); collecting in-process", e)
    MapDataCollector = _load_map_data_collector()
    if MapDataCollector is None:
        return None, "map service not answering and the map stack is not installed"
    return MapDataCollector().collect(), "in-process collector (map service not answering)"


class CoverageMapAndHeatmapMixin:
    """Mixin: coverage map generation, source filtering, heatmap, browser opener."""

    def _generate_coverage_map(self):
        """Generate a coverage map and open in browser."""
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

        CoverageMapGenerator = _load_coverage_map_generator()
        if CoverageMapGenerator is None:
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
                geojson, via = _collect_geojson()
                if geojson is None:
                    self.ctx.dialog.msgbox("Error", f"No node data: {via}.")
                    return
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

            output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "coverage_map.html"

            generator.generate(str(output_file))

            self.ctx.dialog.msgbox(
                "Map Generated",
                f"Coverage map saved to:\n{output_file}\n\n"
                "Opening in browser..."
            )

            self._open_in_browser(str(output_file))

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Map generation failed: {e}")

    def _get_nodes_geojson_by_source(self, source: str) -> dict:
        """Get nodes from a specific source using MapDataCollector.

        Args:
            source: Source filter — "meshtasticd", "mqtt", or "rns".

        Filters on ``source_origin`` (the collector's own provenance tag),
        never ``source``: no feature carries ``source == "meshtasticd"`` or
        ``"mqtt"`` (measured on 5 fleet boxes 2026-09-26 — local radio nodes
        read ``source`` null / ``unified_tracker``), so both menu choices
        answered "No nodes found" beside 196 radio nodes. Federated features
        copy the PEER's origin, so they are excluded — "live from meshtasticd"
        means THIS box's radio.
        """
        origin = _ORIGIN_FOR_SOURCE.get(source)
        if origin is None:
            return {"type": "FeatureCollection", "features": []}

        try:
            geojson, _via = _collect_geojson()
            if geojson is None:
                return {"type": "FeatureCollection", "features": []}

            filtered_features = [
                f for f in geojson.get('features', [])
                if f.get('properties', {}).get('source_origin') == origin
                and f.get('properties', {}).get('source') != 'federation'
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
        if self._is_headless():
            self.ctx.dialog.msgbox(
                "No Display",
                f"No graphical display detected (headless/SSH).\n\n"
                f"Open this URL in your local browser:\n{url}"
            )
            return

        def do_open():
            try:
                real_user = os.environ.get('SUDO_USER')
                if os.geteuid() == 0 and real_user:
                    subprocess.run(
                        ['sudo', '-u', real_user, 'xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
                else:
                    subprocess.run(
                        ['xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                try:
                    webbrowser.open(url)
                except (webbrowser.Error, OSError) as e:
                    logger.warning("Could not open browser: %s", e)

        threading.Thread(target=do_open, daemon=True).start()

    def _generate_heatmap(self):
        """Generate a node density heatmap and open in browser."""
        self.ctx.dialog.infobox("Generating", "Creating node density heatmap...")

        CoverageMapGenerator = _load_coverage_map_generator()
        if CoverageMapGenerator is None:
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

            geojson, via = _collect_geojson()
            if geojson is None:
                self.ctx.dialog.msgbox("Error", f"No node data: {via}.")
                return
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
