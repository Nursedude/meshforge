"""
Map Panel - Unified node map for RNS and Meshtastic networks
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import json
import urllib.parse
from pathlib import Path

# Import logging - try comprehensive utils first, fall back to standard
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Import network diagnostics for event logging
try:
    from utils.network_diagnostics import get_diagnostics, EventCategory, EventSeverity
    diag = get_diagnostics()
except ImportError:
    diag = None

# Try to import WebKit for embedded map
# Note: WebKit doesn't work when running as root (sandbox issues)
import os
_is_root = os.geteuid() == 0

try:
    if _is_root:
        # WebKit doesn't work as root due to sandbox restrictions
        HAS_WEBKIT = False
        logger.info("WebKit disabled (running as root)")
    else:
        gi.require_version('WebKit', '6.0')
        from gi.repository import WebKit
        HAS_WEBKIT = True
except (ValueError, ImportError):
    try:
        if not _is_root:
            gi.require_version('WebKit2', '4.1')
            from gi.repository import WebKit2 as WebKit
            HAS_WEBKIT = True
        else:
            HAS_WEBKIT = False
    except (ValueError, ImportError):
        HAS_WEBKIT = False
        logger.info("WebKit not available, map will open in browser")


class MapPanel(Gtk.Box):
    """Map panel showing nodes from both RNS and Meshtastic networks"""

    # Connection settings - short-lived to avoid blocking web client
    _monitor = None
    _monitor_lock = threading.Lock()
    _last_connect_attempt = 0
    _connect_backoff = 5  # Minimum seconds between connection attempts
    _use_persistent_connection = False  # Set True for persistent (may block web client)

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self.node_tracker = None
        self.webview = None
        self._current_geojson = {"type": "FeatureCollection", "features": []}
        self._refresh_timer_id = None  # Track timer for cleanup

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._init_node_tracker()
        self._build_ui()
        # Store timer ID for cleanup - refresh every 30 seconds to reduce memory pressure
        self._refresh_timer_id = GLib.timeout_add_seconds(30, self._auto_refresh)

    def _init_node_tracker(self):
        """Initialize the node tracker for RNS discovery"""
        try:
            # Try relative import first (when run as package)
            from ...gateway.node_tracker import UnifiedNodeTracker
        except ImportError:
            try:
                # Fallback for direct execution
                from gateway.node_tracker import UnifiedNodeTracker
            except ImportError:
                logger.info("Node tracker not available - RNS nodes won't be shown")
                return

        try:
            self.node_tracker = UnifiedNodeTracker()
            # Start the tracker - it will reuse existing RNS instance if rnsd is running
            self.node_tracker.start()
            logger.info("Node tracker started - RNS nodes will be discovered")
        except Exception as e:
            logger.error(f"Failed to initialize node tracker: {e}")

    @classmethod
    def _get_monitor(cls):
        """Get or create persistent NodeMonitor"""
        import time

        with cls._monitor_lock:
            # Check if monitor exists and is connected
            if cls._monitor is not None:
                try:
                    if cls._monitor.is_connected:
                        return cls._monitor, None
                except (BrokenPipeError, OSError, Exception) as e:
                    logger.debug(f"Monitor connection check failed: {e}")

                # Disconnect old monitor safely
                try:
                    cls._monitor.disconnect()
                except (BrokenPipeError, OSError, Exception):
                    pass
                cls._monitor = None

            # Backoff: Don't retry too frequently
            now = time.time()
            if now - cls._last_connect_attempt < cls._connect_backoff:
                return None, "Waiting to reconnect..."
            cls._last_connect_attempt = now

            # Create new monitor
            try:
                from ...monitoring.node_monitor import NodeMonitor
            except ImportError:
                try:
                    from monitoring.node_monitor import NodeMonitor
                except ImportError:
                    return None, "NodeMonitor not available"

            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3.0)
                if sock.connect_ex(('localhost', 4403)) != 0:
                    sock.close()
                    error_msg = "meshtasticd not running (port 4403)"
                    if diag:
                        diag.log_connection("map", "meshtasticd:4403", False, error_msg)
                    return None, error_msg
                sock.close()
            except Exception as e:
                error_msg = f"Cannot check port: {e}"
                if diag:
                    diag.log_connection("map", "meshtasticd:4403", False, str(e))
                return None, error_msg

            try:
                monitor = NodeMonitor(host='localhost', port=4403)
                if monitor.connect(timeout=15.0):
                    # Wait for nodes to load - MQTT meshes can have 100+ nodes
                    # Poll until node count stabilizes or timeout
                    last_count = 0
                    stable_count = 0
                    for _ in range(10):  # Max 10 seconds
                        time.sleep(1.0)
                        count = monitor.get_node_count()
                        if count == last_count:
                            stable_count += 1
                            if stable_count >= 2:  # Stable for 2 seconds
                                break
                        else:
                            stable_count = 0
                            last_count = count
                    cls._monitor = monitor
                    logger.info(f"NodeMonitor connected, {monitor.get_node_count()} nodes")
                    if diag:
                        diag.log_connection("map", "meshtasticd:4403", True)
                        diag.log_event(
                            EventCategory.NETWORK, EventSeverity.INFO, "map",
                            f"Discovered {monitor.get_node_count()} Meshtastic nodes"
                        )
                    return monitor, None
                else:
                    error_msg = "Failed to connect to meshtasticd"
                    if diag:
                        diag.log_connection("map", "meshtasticd:4403", False, error_msg)
                    return None, error_msg
            except Exception as e:
                error_str = str(e).lower()
                # Detect common connection conflicts
                if 'connection refused' in error_str or 'refused' in error_str:
                    error_msg = "Connection refused - another client may be connected"
                elif 'timed out' in error_str or 'timeout' in error_str:
                    error_msg = "Connection timeout - meshtasticd may be busy with another client"
                elif 'broken pipe' in error_str:
                    error_msg = "Connection lost - another client took over"
                elif 'already in use' in error_str:
                    error_msg = "Port in use by another client (meshing-around, nomadnet?)"
                else:
                    error_msg = f"Connection error: {e}"

                logger.warning(f"[Map] {error_msg}")
                if diag:
                    diag.log_connection("map", "meshtasticd:4403", False, str(e))
                return None, error_msg

    def _build_ui(self):
        """Build the map panel UI"""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        title = Gtk.Label(label="Network Node Map")
        title.add_css_class("title-1")
        title.set_xalign(0)
        title.set_hexpand(True)
        header_box.append(title)

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", self._on_refresh)
        header_box.append(refresh_btn)

        # Open in browser button
        browser_btn = Gtk.Button(label="Open in Browser")
        browser_btn.connect("clicked", self._on_open_browser)
        header_box.append(browser_btn)

        self.append(header_box)

        subtitle = Gtk.Label(label="Unified view of Meshtastic and RNS mesh networks")
        subtitle.add_css_class("dim-label")
        subtitle.set_xalign(0)
        self.append(subtitle)

        # Statistics row
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        stats_box.set_margin_top(10)
        stats_box.set_margin_bottom(10)

        self.stat_total = self._create_stat_label("Total", "0")
        stats_box.append(self.stat_total)

        self.stat_meshtastic = self._create_stat_label("Meshtastic", "0")
        stats_box.append(self.stat_meshtastic)

        self.stat_rns = self._create_stat_label("RNS", "0")
        # Add tooltip explaining RNS status
        if self.node_tracker is None:
            self.stat_rns.set_tooltip_text("RNS not available - install RNS and configure TCPClientInterface")
        else:
            self.stat_rns.set_tooltip_text(
                "RNS nodes discovered via announces. "
                "Nodes appear in list below. Map markers require position data."
            )
        stats_box.append(self.stat_rns)

        self.stat_online = self._create_stat_label("Online", "0")
        stats_box.append(self.stat_online)

        self.stat_with_pos = self._create_stat_label("With Position", "0")
        stats_box.append(self.stat_with_pos)

        self.append(stats_box)

        # Main content area
        if HAS_WEBKIT:
            self._build_webkit_map()
        else:
            self._build_fallback_ui()

        # Status bar
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_xalign(0)
        self.append(self.status_label)

        # Initial data load
        GLib.idle_add(self._refresh_data)

    def _create_stat_label(self, name, value):
        """Create a statistics label widget"""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        name_label = Gtk.Label(label=f"{name}:")
        name_label.add_css_class("dim-label")
        box.append(name_label)

        value_label = Gtk.Label(label=value)
        value_label.add_css_class("heading")
        value_label.set_name(f"value_{name.lower()}")
        box.append(value_label)

        return box

    def _update_stat(self, stat_box, value):
        """Update a stat box value"""
        for child in stat_box:
            if child.get_name() and child.get_name().startswith("value_"):
                child.set_label(str(value))
                return

    def _build_webkit_map(self):
        """Build embedded WebKit map view"""
        frame = Gtk.Frame()
        frame.set_vexpand(True)

        # Create WebView
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)

        # Load map HTML
        map_path = self._get_map_path()
        if map_path and map_path.exists():
            self.webview.load_uri(f"file://{map_path}")
        else:
            # Create inline HTML fallback
            self._load_inline_map()

        frame.set_child(self.webview)
        self.append(frame)

    def _build_fallback_ui(self):
        """Build fallback UI when WebKit is not available"""
        frame = Gtk.Frame()
        frame.set_label("Node List")
        frame.set_vexpand(True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Node list view
        self.node_list = Gtk.ListBox()
        self.node_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.set_child(self.node_list)

        frame.set_child(scrolled)
        self.append(frame)

        # Add info about opening in browser
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        info_box.set_margin_top(10)

        info_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        info_box.append(info_icon)

        info_label = Gtk.Label(
            label="WebKit not available. Click 'Open in Browser' for interactive map."
        )
        info_label.add_css_class("dim-label")
        info_box.append(info_label)

        self.append(info_box)

    def _get_map_path(self):
        """Get path to map HTML file"""
        # Try relative to this file
        current_dir = Path(__file__).parent.parent.parent.parent
        map_path = current_dir / "web" / "node_map.html"
        if map_path.exists():
            return map_path

        # Try installed location
        map_path = Path("/opt/meshforge/web/node_map.html")
        if map_path.exists():
            return map_path

        return None

    def _load_inline_map(self):
        """Load a simple inline map if external file not found"""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {
                    font-family: sans-serif;
                    background: #1a1a2e;
                    color: #eee;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .message {
                    text-align: center;
                    padding: 40px;
                }
                h2 { color: #4fc3f7; }
            </style>
        </head>
        <body>
            <div class="message">
                <h2>Map Loading...</h2>
                <p>Interactive map requires web/node_map.html</p>
                <p>Click 'Open in Browser' for full map experience.</p>
            </div>
        </body>
        </html>
        """
        self.webview.load_html(html, "file:///")

    def _refresh_data(self):
        """Refresh node data from Meshtastic"""
        self.status_label.set_label("Refreshing...")

        def fetch_data():
            from datetime import datetime

            stats = {"total": 0, "meshtastic": 0, "rns": 0, "online": 0, "with_position": 0, "via_mqtt": 0}
            geojson = {"type": "FeatureCollection", "features": []}
            nodes_raw = []
            error_msg = None

            # Get persistent monitor
            monitor, error_msg = MapPanel._get_monitor()

            if monitor:
                try:
                    features = []
                    total = 0
                    online = 0
                    with_position = 0

                    for node in monitor.get_nodes(refresh=True):
                        total += 1
                        nodes_raw.append(node)

                        # Check if online (seen in last 15 minutes)
                        is_online = False
                        last_seen = "Unknown"
                        if node.last_heard:
                            delta = datetime.now() - node.last_heard
                            if delta.total_seconds() < 60:
                                last_seen = f"{int(delta.total_seconds())}s ago"
                                is_online = True
                            elif delta.total_seconds() < 3600:
                                last_seen = f"{int(delta.total_seconds() / 60)}m ago"
                                is_online = True
                            elif delta.total_seconds() < 86400:
                                last_seen = f"{int(delta.total_seconds() / 3600)}h ago"
                            else:
                                last_seen = f"{int(delta.total_seconds() / 86400)}d ago"

                        if is_online:
                            online += 1

                        # Count MQTT nodes
                        if node.via_mqtt:
                            stats["via_mqtt"] += 1

                        # Check position
                        if node.position and (node.position.latitude or node.position.longitude):
                            lat = node.position.latitude
                            lon = node.position.longitude
                            if lat and lon and not (lat == 0 and lon == 0):
                                with_position += 1
                                feature = {
                                    "type": "Feature",
                                    "geometry": {
                                        "type": "Point",
                                        "coordinates": [lon, lat]
                                    },
                                    "properties": {
                                        "id": node.node_id,
                                        "name": node.long_name or node.short_name or node.node_id,
                                        "network": "meshtastic",
                                        "is_online": is_online,
                                        "is_local": node.node_id == monitor.my_node_id,
                                        "is_gateway": (node.role or '').upper() in ['ROUTER', 'REPEATER', 'ROUTER_CLIENT'],
                                        "via_mqtt": node.via_mqtt,
                                        "snr": node.snr,
                                        "battery": node.metrics.battery_level if node.metrics else None,
                                        "last_seen": last_seen,
                                        "hardware": node.hardware_model,
                                        "role": node.role,
                                    }
                                }
                                features.append(feature)

                    stats = {
                        "total": total,
                        "meshtastic": total,
                        "rns": 0,
                        "online": online,
                        "with_position": with_position,
                        "via_mqtt": stats.get("via_mqtt", 0)
                    }
                    geojson = {"type": "FeatureCollection", "features": features}

                    # Disconnect after reading to avoid blocking web client
                    if not MapPanel._use_persistent_connection:
                        with MapPanel._monitor_lock:
                            try:
                                if MapPanel._monitor:
                                    MapPanel._monitor.disconnect()
                                    logger.debug("Disconnected monitor (non-persistent mode)")
                            except Exception:
                                pass
                            MapPanel._monitor = None

                except (BrokenPipeError, OSError) as e:
                    # Connection lost - clear monitor for reconnect next time
                    # Use debug level to avoid log spam during reconnection
                    logger.debug(f"Meshtastic connection lost: {e}")
                    with MapPanel._monitor_lock:
                        try:
                            if MapPanel._monitor:
                                MapPanel._monitor.disconnect()
                        except Exception:
                            pass
                        MapPanel._monitor = None
                    error_msg = "Connection lost - will retry"
                except Exception as e:
                    logger.debug(f"Error fetching Meshtastic nodes: {e}")
                    error_msg = str(e)

            # Also fetch RNS nodes from the unified tracker
            rns_count = 0
            if self.node_tracker:
                try:
                    rns_nodes = self.node_tracker.get_rns_nodes()
                    for rns_node in rns_nodes:
                        rns_count += 1
                        stats["total"] += 1

                        # Check if has valid position
                        if rns_node.position and rns_node.position.is_valid():
                            stats["with_position"] += 1
                            feature = {
                                "type": "Feature",
                                "geometry": {
                                    "type": "Point",
                                    "coordinates": [
                                        rns_node.position.longitude,
                                        rns_node.position.latitude
                                    ]
                                },
                                "properties": {
                                    "id": rns_node.id,
                                    "name": rns_node.name or rns_node.short_name or rns_node.id,
                                    "network": "rns",
                                    "is_online": rns_node.is_online,
                                    "is_local": rns_node.is_local,
                                    "is_gateway": rns_node.is_gateway,
                                    "via_mqtt": False,
                                    "snr": rns_node.snr,
                                    "battery": rns_node.telemetry.battery_level if rns_node.telemetry else None,
                                    "last_seen": rns_node.get_age_string(),
                                    "hardware": rns_node.hardware_model,
                                    "role": rns_node.role,
                                }
                            }
                            geojson["features"].append(feature)

                        if rns_node.is_online:
                            stats["online"] += 1

                except Exception as e:
                    logger.warning(f"Error fetching RNS nodes: {e}")

            stats["rns"] = rns_count

            GLib.idle_add(self._update_ui, stats, geojson, nodes_raw, error_msg)

        threading.Thread(target=fetch_data, daemon=True).start()

    def _update_ui(self, stats, geojson, nodes, error_msg=None):
        """Update UI with fetched data"""
        # Store geojson for browser button
        self._current_geojson = geojson

        # Update statistics
        self._update_stat(self.stat_total, stats.get("total", 0))
        self._update_stat(self.stat_meshtastic, stats.get("meshtastic", 0))
        self._update_stat(self.stat_rns, stats.get("rns", 0))
        self._update_stat(self.stat_online, stats.get("online", 0))
        self._update_stat(self.stat_with_pos, stats.get("with_position", 0))

        # Update map if WebKit available
        if self.webview:
            try:
                # Send GeoJSON to JavaScript
                geojson_str = json.dumps(geojson)
                js_code = f"if(typeof loadNodes === 'function') loadNodes({geojson_str});"
                self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
            except Exception as e:
                logger.debug(f"Could not update webview: {e}")

        # Update node list if in fallback mode
        if hasattr(self, 'node_list'):
            # Get RNS nodes too
            rns_nodes_for_list = []
            if self.node_tracker:
                try:
                    rns_nodes_for_list = self.node_tracker.get_rns_nodes()
                except Exception:
                    pass
            self._update_node_list_raw(nodes, rns_nodes_for_list)

        # Update status with error or success
        if error_msg:
            self.status_label.set_label(f"Error: {error_msg}")
        else:
            mqtt_count = stats.get('via_mqtt', 0)
            mqtt_str = f", {mqtt_count} MQTT" if mqtt_count > 0 else ""
            self.status_label.set_label(
                f"Last updated: {stats.get('total', 0)} nodes "
                f"({stats.get('with_position', 0)} mapped, "
                f"{stats.get('online', 0)} online{mqtt_str})"
            )

        return False

    def _update_node_list(self, nodes):
        """Update the fallback node list"""
        # Clear existing
        while True:
            child = self.node_list.get_first_child()
            if child is None:
                break
            self.node_list.remove(child)

        # Add nodes
        for node in nodes:
            row = self._create_node_row(node)
            self.node_list.append(row)

        if not nodes:
            empty_label = Gtk.Label(label="No nodes found")
            empty_label.add_css_class("dim-label")
            empty_label.set_margin_top(20)
            empty_label.set_margin_bottom(20)
            self.node_list.append(empty_label)

    def _update_node_list_raw(self, nodes, rns_nodes=None):
        """Update the fallback node list with raw NodeInfo objects from NodeMonitor"""
        from datetime import datetime

        # Clear existing
        while True:
            child = self.node_list.get_first_child()
            if child is None:
                break
            self.node_list.remove(child)

        total_count = len(nodes) + (len(rns_nodes) if rns_nodes else 0)

        # Add Meshtastic nodes
        for node in nodes:
            row = self._create_node_row_raw(node)
            self.node_list.append(row)

        # Add RNS nodes
        if rns_nodes:
            for rns_node in rns_nodes:
                row = self._create_rns_node_row(rns_node)
                self.node_list.append(row)

        if total_count == 0:
            empty_label = Gtk.Label(label="No nodes found - is meshtasticd running?")
            empty_label.add_css_class("dim-label")
            empty_label.set_margin_top(20)
            empty_label.set_margin_bottom(20)
            self.node_list.append(empty_label)

    def _create_rns_node_row(self, node):
        """Create a list row for an RNS node"""
        from datetime import datetime

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Check if online
        is_online = node.is_online if hasattr(node, 'is_online') else False
        age_str = node.get_age_string() if hasattr(node, 'get_age_string') else "Unknown"

        # Status indicator
        status_icon = Gtk.Label()
        if is_online:
            status_icon.set_label("\u25CF")  # Filled circle
            status_icon.add_css_class("success")
        else:
            status_icon.set_label("\u25CB")  # Empty circle
            status_icon.add_css_class("warning")
        box.append(status_icon)

        # Node info
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)

        # Name
        name = node.name or node.short_name or "Unknown"
        name_label = Gtk.Label(label=name)
        name_label.set_xalign(0)
        name_label.add_css_class("heading")
        info_box.append(name_label)

        # Hash (short form)
        hash_str = node.rns_hash.hex()[:12] if node.rns_hash else "?"
        hash_label = Gtk.Label(label=f"<{hash_str}>")
        hash_label.set_xalign(0)
        hash_label.add_css_class("dim-label")
        hash_label.add_css_class("monospace")
        info_box.append(hash_label)

        box.append(info_box)

        # Network badge - RNS
        network_label = Gtk.Label(label="RNS")
        network_label.add_css_class("accent")
        box.append(network_label)

        # Age
        age_label = Gtk.Label(label=age_str)
        age_label.add_css_class("dim-label")
        box.append(age_label)

        return box

    def _create_node_row_raw(self, node):
        """Create a list row for a raw NodeInfo from NodeMonitor"""
        from datetime import datetime

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Check if online (seen in last 15 minutes)
        is_online = False
        age_str = "Unknown"
        if node.last_heard:
            delta = datetime.now() - node.last_heard
            if delta.total_seconds() < 900:  # 15 minutes
                is_online = True
            if delta.total_seconds() < 60:
                age_str = f"{int(delta.total_seconds())}s ago"
            elif delta.total_seconds() < 3600:
                age_str = f"{int(delta.total_seconds() / 60)}m ago"
            elif delta.total_seconds() < 86400:
                age_str = f"{int(delta.total_seconds() / 3600)}h ago"
            else:
                age_str = f"{int(delta.total_seconds() / 86400)}d ago"

        # Status indicator
        status_icon = Gtk.Label()
        if is_online:
            status_icon.set_label("\u25CF")  # Filled circle
            status_icon.add_css_class("success")
        else:
            status_icon.set_label("\u25CB")  # Empty circle
            status_icon.add_css_class("error")
        box.append(status_icon)

        # Name
        name = node.long_name or node.short_name or node.node_id
        name_label = Gtk.Label(label=name)
        name_label.set_xalign(0)
        name_label.set_hexpand(True)
        box.append(name_label)

        # Network badge
        network_label = Gtk.Label(label="MESH")
        network_label.add_css_class("accent")
        box.append(network_label)

        # Position indicator
        if node.position and (node.position.latitude or node.position.longitude):
            lat = node.position.latitude
            lon = node.position.longitude
            if lat and lon and not (lat == 0 and lon == 0):
                pos_label = Gtk.Label(label=f"{lat:.4f}, {lon:.4f}")
            else:
                pos_label = Gtk.Label(label="No position")
        else:
            pos_label = Gtk.Label(label="No position")
        pos_label.add_css_class("dim-label")
        box.append(pos_label)

        # Last seen
        seen_label = Gtk.Label(label=age_str)
        seen_label.add_css_class("dim-label")
        box.append(seen_label)

        return box

    def _create_node_row(self, node):
        """Create a list row for a node"""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Status indicator
        status_icon = Gtk.Label()
        if node.is_online:
            status_icon.set_label("\u25CF")  # Filled circle
            status_icon.add_css_class("success")
        else:
            status_icon.set_label("\u25CB")  # Empty circle
            status_icon.add_css_class("error")
        box.append(status_icon)

        # Name
        name_label = Gtk.Label(label=node.name or node.id)
        name_label.set_xalign(0)
        name_label.set_hexpand(True)
        box.append(name_label)

        # Network badge
        network_label = Gtk.Label(label=node.network.upper())
        if node.network == "meshtastic":
            network_label.add_css_class("accent")
        elif node.network == "rns":
            network_label.add_css_class("warning")
        box.append(network_label)

        # Position indicator
        if node.position and node.position.is_valid():
            pos_label = Gtk.Label(
                label=f"{node.position.latitude:.4f}, {node.position.longitude:.4f}"
            )
            pos_label.add_css_class("dim-label")
        else:
            pos_label = Gtk.Label(label="No position")
            pos_label.add_css_class("dim-label")
        box.append(pos_label)

        # Last seen
        seen_label = Gtk.Label(label=node.get_age_string())
        seen_label.add_css_class("dim-label")
        box.append(seen_label)

        return box

    def _on_refresh(self, button):
        """Handle refresh button click"""
        self._refresh_data()

    def _auto_refresh(self):
        """Auto-refresh timer callback"""
        self._refresh_data()
        return True  # Continue timer

    def _on_open_browser(self, button):
        """Open map in external browser"""
        import os
        import tempfile

        map_path = self._get_map_path()
        if not map_path or not map_path.exists():
            self.status_label.set_label("Error: Map file not found")
            return

        # Use stored geojson from last refresh (populated by NodeMonitor)
        geojson = self._current_geojson
        node_count = len(geojson.get('features', []))

        # For large datasets, generate a self-contained HTML with embedded data
        # (URL params truncate with 80+ nodes, and fetch('file://') is blocked)
        try:
            # Read the template HTML
            with open(map_path, 'r') as f:
                html_content = f.read()

            # Inject the GeoJSON data before </body>
            geojson_str = json.dumps(geojson)
            inject_script = f'''
<script>
// Injected by MeshForge - {node_count} nodes
window.meshforgeData = {geojson_str};
document.addEventListener('DOMContentLoaded', function() {{
    if (typeof loadNodes === 'function' && window.meshforgeData) {{
        console.log('Loading {node_count} nodes from embedded data');
        loadNodes(window.meshforgeData);
    }}
}});
</script>
</body>'''
            html_content = html_content.replace('</body>', inject_script)

            # Write to temp file
            temp_html = Path(tempfile.gettempdir()) / "meshforge_map.html"
            with open(temp_html, 'w') as f:
                f.write(html_content)

            url = f"file://{temp_html}"
            logger.info(f"Generated map with {node_count} nodes at {temp_html}")

        except Exception as e:
            logger.error(f"Failed to generate map: {e}")
            # Fallback to original URL with params (will be truncated for large data)
            params = urllib.parse.urlencode({'data': json.dumps(geojson)})
            url = f"file://{map_path}?{params}"

        def try_open():
            user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

            # Try xdg-open
            try:
                result = subprocess.run(
                    ['sudo', '-u', user, 'xdg-open', url],
                    capture_output=True, timeout=10
                )
                if result.returncode == 0:
                    GLib.idle_add(
                        self.status_label.set_label,
                        "Opened map in browser"
                    )
                    return
            except Exception:
                pass

            # Try common browsers
            browsers = ['chromium-browser', 'firefox', 'epiphany-browser']
            for browser in browsers:
                try:
                    subprocess.Popen(
                        ['sudo', '-u', user, browser, url],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    GLib.idle_add(
                        self.status_label.set_label,
                        f"Opened map in {browser}"
                    )
                    return
                except Exception:
                    continue

            GLib.idle_add(
                self.status_label.set_label,
                f"Could not open browser. URL: {url[:50]}..."
            )

        threading.Thread(target=try_open, daemon=True).start()

    def cleanup(self):
        """Cleanup when panel is destroyed"""
        # Remove auto-refresh timer to prevent memory leaks
        if self._refresh_timer_id:
            try:
                GLib.source_remove(self._refresh_timer_id)
                self._refresh_timer_id = None
            except Exception:
                pass

        # Stop node tracker
        if self.node_tracker:
            try:
                self.node_tracker.stop()
            except Exception:
                pass

        # Clear WebView to free memory
        if self.webview:
            try:
                self.webview.load_uri("about:blank")
            except Exception:
                pass

        # Clear cached data
        self._current_geojson = {"type": "FeatureCollection", "features": []}
