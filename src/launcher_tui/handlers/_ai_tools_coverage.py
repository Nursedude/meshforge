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

from utils.safe_import import safe_import

CoverageMapGenerator, MapNode, _HAS_COVERAGE_MAP = safe_import(
    'utils.coverage_map', 'CoverageMapGenerator', 'MapNode'
)
MapDataCollector, get_all_ips, _HAS_MAP_SERVICE = safe_import(
    'utils.map_data_service', 'MapDataCollector', 'get_all_ips'
)

logger = logging.getLogger(__name__)


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
        """
        if not _HAS_MAP_SERVICE:
            return {"type": "FeatureCollection", "features": []}

        try:
            collector = MapDataCollector()
            geojson = collector.collect()

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
