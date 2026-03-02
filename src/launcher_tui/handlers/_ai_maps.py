"""AI Maps helpers — coverage map, heatmap, tile cache, browser open, live map.

Extracted from ai_tools.py for file size compliance (CLAUDE.md #6).
Functions take ``handler`` (the AIToolsHandler instance) as first parameter
and access TUI via handler.ctx.dialog, handler.ctx.registry, etc.
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

from utils.safe_import import safe_import
from utils.service_check import start_service

# --- Optional dependencies (mirrors ai_tools.py safe_import) ---
CoverageMapGenerator, MapNode, _HAS_COVERAGE_MAP = safe_import(
    'utils.coverage_map', 'CoverageMapGenerator', 'MapNode'
)
MapDataCollector, get_all_ips, _HAS_MAP_SERVICE = safe_import(
    'utils.map_data_service', 'MapDataCollector', 'get_all_ips'
)
TileCache, HAWAII_BOUNDS, _HAS_TILE_CACHE = safe_import(
    'utils.tile_cache', 'TileCache', 'HAWAII_BOUNDS'
)

logger = logging.getLogger(__name__)


def generate_coverage_map(handler):
    """Generate a coverage map and open in browser."""
    # Get node data source
    source_choices = [
        ("all", "All sources (recommended)"),
        ("live", "Live from meshtasticd only"),
        ("mqtt", "From MQTT broker"),
        ("file", "From saved node file"),
        ("back", "Back"),
    ]

    choice = handler.ctx.dialog.menu(
        "Coverage Map",
        "Select node data source:",
        source_choices
    )

    if choice is None or choice == "back":
        return

    handler.ctx.dialog.infobox("Generating", "Creating coverage map...")

    if not _HAS_COVERAGE_MAP:
        handler.ctx.dialog.msgbox(
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
                handler.ctx.dialog.msgbox("Error", "MapDataCollector not available.")
                return
            collector = MapDataCollector()
            geojson = collector.collect()
            features = geojson.get('features', [])
            if features:
                generator.add_nodes_from_geojson(geojson)
                handler.ctx.dialog.infobox(
                    "Generating",
                    f"Found {len(features)} nodes from all sources..."
                )
            else:
                handler.ctx.dialog.msgbox(
                    "No Nodes",
                    "No nodes found from any source.\n\n"
                    "Check meshtasticd, MQTT, or node cache."
                )
                return

        elif choice == "live":
            geojson = get_nodes_geojson_by_source(handler, "meshtasticd")
            features = geojson.get('features', [])
            if features:
                generator.add_nodes_from_geojson(geojson)
                handler.ctx.dialog.infobox(
                    "Generating",
                    f"Found {len(features)} nodes from meshtasticd..."
                )
            else:
                handler.ctx.dialog.msgbox(
                    "No Nodes",
                    "No nodes found from meshtasticd.\n\n"
                    "Ensure meshtasticd is running and has nodes with GPS."
                )
                return

        elif choice == "mqtt":
            geojson = get_nodes_geojson_by_source(handler, "mqtt")
            features = geojson.get('features', [])
            if features:
                generator.add_nodes_from_geojson(geojson)
                handler.ctx.dialog.infobox(
                    "Generating",
                    f"Found {len(features)} nodes from MQTT..."
                )
            else:
                handler.ctx.dialog.msgbox(
                    "No Nodes",
                    "No nodes found from MQTT cache.\n\n"
                    "MQTT nodes are cached when monitoring is running."
                )
                return

        elif choice == "file":
            file_path = handler.ctx.dialog.inputbox(
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
                handler.ctx.dialog.msgbox("Error", f"Failed to load file: {e}")
                return

        try:
            from utils.safe_import import safe_import as _si
            _get_topo, _has_topo = _si(
                'gateway.network_topology', 'get_network_topology'
            )
            if _has_topo:
                topo = _get_topo()
                topo_dict = topo.to_dict()
                edges = topo_dict.get('edges', [])
                if edges:
                    generator.set_rns_edges(edges)
                    logger.debug("Added %d RNS edges to map", len(edges))
        except Exception:
            pass  # Topology unavailable -- skip gracefully

        # Generate map
        output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "coverage_map.html"

        generator.generate(str(output_file))

        # Open in browser
        handler.ctx.dialog.msgbox(
            "Map Generated",
            f"Coverage map saved to:\n{output_file}\n\n"
            "Opening in browser..."
        )

        # Open browser in background
        open_in_browser(handler, str(output_file))

    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Map generation failed: {e}")


def get_nodes_geojson_by_source(handler, source: str) -> dict:
    """Get nodes from a specific source via MapDataCollector."""
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


def open_in_browser(handler, url: str):
    """Open URL in browser (background thread; handles root and headless)."""
    # On headless/SSH, show URL instead of trying to open browser
    if handler._is_headless():
        handler.ctx.dialog.msgbox(
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



def generate_heatmap(handler):
    """Generate a heatmap weighted by density or signal quality."""
    if not _HAS_COVERAGE_MAP:
        handler.ctx.dialog.msgbox(
            "Error",
            "Coverage map generator not available.\n\n"
            "You may need to install folium:\n"
            "pip3 install folium"
        )
        return

    if not _HAS_MAP_SERVICE:
        handler.ctx.dialog.msgbox("Error", "MapDataCollector not available.")
        return

    # Let user choose weighting mode
    weight_choice = handler.ctx.dialog.menu(
        "Heatmap Type",
        "Select heatmap weighting:",
        [
            ("snr", "Signal Quality (SNR)  Weight by signal-to-noise ratio"),
            ("rssi", "Signal Strength (RSSI) Weight by received power level"),
            ("density", "Node Density          Online/offline presence only"),
        ]
    )
    if weight_choice is None:
        return

    label = {"snr": "SNR signal quality", "rssi": "RSSI signal strength",
             "density": "node density"}.get(weight_choice, weight_choice)
    handler.ctx.dialog.infobox("Generating", f"Creating {label} heatmap...")

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
            handler.ctx.dialog.msgbox(
                "No Nodes",
                "No nodes found from any source.\n\n"
                "Check meshtasticd, MQTT, or node cache."
            )
            return

        # Generate heatmap
        output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = str(output_dir / "coverage_heatmap.html")

        result_path = generator.generate_heatmap(
            output_path=output_file, weight_by=weight_choice
        )

        if not result_path:
            handler.ctx.dialog.msgbox(
                "Error",
                "Heatmap generation failed.\n\n"
                "Folium with HeatMap plugin is required:\n"
                "pip3 install folium"
            )
            return

        handler.ctx.dialog.msgbox(
            "Heatmap Generated",
            f"{label.title()} heatmap saved to:\n{result_path}\n\n"
            "Opening in browser..."
        )
        open_in_browser(handler, result_path)

    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Heatmap generation failed: {e}")


def generate_terrain_coverage(handler):
    """Generate terrain-aware RF coverage prediction (delegates to Site Planner)."""
    # Try to delegate to site_planner handler (avoids code duplication)
    if handler.ctx.registry:
        site_planner = handler.ctx.registry.get_handler("site_planner")
        if site_planner and hasattr(site_planner, '_terrain_coverage'):
            site_planner._terrain_coverage()
            return

    # Fallback: inform user where to find it
    handler.ctx.dialog.msgbox(
        "Terrain Coverage",
        "Terrain-aware coverage prediction is available\n"
        "in the Site Planner menu.\n\n"
        "Go to: RF & SDR Tools > Site Planner > Terrain Coverage Map"
    )


def tile_cache_menu(handler):
    """Manage offline tile cache for maps."""
    choices = [
        ("stats", "Cache Stats         View tile cache status"),
        ("download", "Download Region     Cache tiles for area"),
        ("estimate", "Estimate Size       Preview download size"),
        ("clear", "Clear Expired       Remove old tiles"),
    ]
    dispatch = {
        "stats": ("Cache Stats", lambda: tile_cache_stats(handler)),
        "download": ("Download Region", lambda: tile_cache_download(handler)),
        "estimate": ("Estimate Size", lambda: tile_cache_estimate(handler)),
        "clear": ("Clear Expired", lambda: tile_cache_clear(handler)),
    }
    handler.run_menu_loop(
        "Offline Tile Cache",
        "Manage cached map tiles for offline use:",
        choices, dispatch
    )


def tile_cache_stats(handler):
    """Display tile cache statistics."""
    if not _HAS_TILE_CACHE:
        handler.ctx.dialog.msgbox("Error", "Tile cache module not available.")
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

        handler.ctx.dialog.msgbox("Tile Cache Stats", "\n".join(info))
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to get cache stats: {e}")


def tile_cache_download(handler):
    """Download tiles for a geographic region."""
    if not _HAS_TILE_CACHE:
        handler.ctx.dialog.msgbox("Error", "Tile cache module not available.")
        return

    try:
        # Get bounds from user
        region_choices = [
            ("hawaii", "Hawaii              (18.5-22.5N, 160.5-154.5W)"),
            ("custom", "Custom Region       Enter coordinates"),
            ("back", "Back"),
        ]

        choice = handler.ctx.dialog.menu(
            "Download Region",
            "Select region to cache tiles for:",
            region_choices
        )

        if choice is None or choice == "back":
            return

        if choice == "hawaii":
            bounds = HAWAII_BOUNDS
        elif choice == "custom":
            coords = handler.ctx.dialog.inputbox(
                "Custom Region",
                "Enter bounds as: south,west,north,east\n"
                "Example: 21.0,-158.5,21.7,-157.5"
            )
            if not coords:
                return
            try:
                parts = [float(x.strip()) for x in coords.split(',')]
                if len(parts) != 4:
                    handler.ctx.dialog.msgbox("Error", "Enter exactly 4 coordinates.")
                    return
                bounds = tuple(parts)
            except ValueError:
                handler.ctx.dialog.msgbox("Error", "Invalid coordinates.")
                return
        else:
            return

        # Estimate first
        estimate = TileCache.estimate_download_size(bounds)
        if 'error' in estimate:
            handler.ctx.dialog.msgbox("Error", estimate['error'])
            return

        confirm = handler.ctx.dialog.yesno(
            "Confirm Download",
            f"Tiles to download: {estimate['total_tiles']}\n"
            f"Estimated size: {estimate['estimated_mb']:.1f} MB\n\n"
            "Proceed with download?"
        )

        if not confirm:
            return

        handler.ctx.dialog.infobox("Downloading", "Caching tiles... This may take a while.")

        cache = TileCache()
        result = cache.download_region(bounds)

        if 'error' in result:
            handler.ctx.dialog.msgbox("Error", result['error'])
        else:
            handler.ctx.dialog.msgbox(
                "Download Complete",
                f"Downloaded: {result['downloaded']} tiles\n"
                f"Skipped (cached): {result['skipped']}\n"
                f"Failed: {result['failed']}"
            )

    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Tile download failed: {e}")


def tile_cache_estimate(handler):
    """Estimate download size for a region."""
    if not _HAS_TILE_CACHE:
        handler.ctx.dialog.msgbox("Error", "Tile cache module not available.")
        return

    try:
        coords = handler.ctx.dialog.inputbox(
            "Estimate Size",
            "Enter bounds as: south,west,north,east\n"
            "Example: 21.0,-158.5,21.7,-157.5\n"
            "(Leave empty for Hawaii)"
        )

        if coords:
            try:
                parts = [float(x.strip()) for x in coords.split(',')]
                if len(parts) != 4:
                    handler.ctx.dialog.msgbox("Error", "Enter exactly 4 coordinates.")
                    return
                bounds = tuple(parts)
            except ValueError:
                handler.ctx.dialog.msgbox("Error", "Invalid coordinates.")
                return
        else:
            bounds = HAWAII_BOUNDS

        estimate = TileCache.estimate_download_size(bounds)

        if 'error' in estimate:
            handler.ctx.dialog.msgbox("Error", estimate['error'])
        else:
            handler.ctx.dialog.msgbox(
                "Download Estimate",
                f"Region: ({bounds[0]:.1f}, {bounds[1]:.1f}) to "
                f"({bounds[2]:.1f}, {bounds[3]:.1f})\n"
                f"Tile count: {estimate['total_tiles']}\n"
                f"Estimated size: {estimate['estimated_mb']:.1f} MB\n"
                f"Within limit: {'Yes' if estimate['within_limit'] else 'No'}"
            )

    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Estimation failed: {e}")


def tile_cache_clear(handler):
    """Clear expired tiles from cache."""
    if not _HAS_TILE_CACHE:
        handler.ctx.dialog.msgbox("Error", "Tile cache module not available.")
        return

    try:
        confirm = handler.ctx.dialog.yesno(
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
        handler.ctx.dialog.msgbox(
            "Cache Cleared",
            f"Removed: {result['removed']} expired tiles\n"
            f"Space freed: {freed_mb:.1f} MB"
        )

    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Cache clear failed: {e}")


def open_live_map_browser(handler):
    """Generate browser snapshot of the live map with current node data."""
    handler.ctx.dialog.infobox("Loading", "Collecting node data from all sources...")

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
            handler.ctx.dialog.msgbox(
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
        source_info = [
            f"meshtasticd: {sources.get('meshtasticd', 0)}",
            f"MQTT: {sources.get('mqtt', 0)}",
            f"node_tracker: {sources.get('node_tracker', 0)}",
        ]

        msg = (
            f"Map saved: {output_file}\n\n"
            f"Total nodes: {node_count}\n"
            f"Sources:\n  " + "\n  ".join(source_info) + "\n\n"
            "Opening in browser..."
        )
        handler.ctx.dialog.msgbox("Live Map", msg)
        open_in_browser(handler, f"file://{output_file}")

    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to generate live map: {e}")


def start_map_server(handler):
    """Start map HTTP server (prefers systemd, falls back to in-process)."""
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
            service_status = handler._get_map_service_status()
            handler.ctx.dialog.msgbox(
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
    service_started = handler._try_start_map_service()

    if service_started:
        urls = "\n".join(f"  http://{ip}:{port}" for ip in all_ips)
        handler.ctx.dialog.msgbox(
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

        root_logger = logging.getLogger()
        old_handler_levels = []
        for h in root_logger.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                old_handler_levels.append((h, h.level))
                h.setLevel(logging.CRITICAL + 1)

        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                from utils.map_data_service import MapServer

                server = MapServer(port=port)  # Binds to 0.0.0.0
                server.start_background()

                time.sleep(0.1)
        finally:
            for h, level in old_handler_levels:
                h.setLevel(level)

        handler._map_server = server

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
        handler.ctx.dialog.msgbox("Map Server Started", msg)

    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to start map server: {e}")
