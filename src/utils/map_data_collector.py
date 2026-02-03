"""
Map Data Collector - Unified node GeoJSON from all available sources.

Collects node data from meshtasticd, MQTT, RNS node tracker, and AREDN,
merges into a single GeoJSON FeatureCollection.

This module provides the data collection logic. For the HTTP server,
see map_data_service.py.

Usage:
    from utils.map_data_collector import MapDataCollector
    collector = MapDataCollector()
    geojson = collector.collect()
"""

import json
import logging
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MapDataCollector:
    """Collects node data from all available sources into unified GeoJSON.

    Sources (tried in order, all merged):
    1. meshtasticd TCP (localhost:4403) - local mesh nodes
    2. MQTT subscriber - global/regional nodes
    3. Node tracker cache - previously discovered RNS + Meshtastic nodes
    4. Last-known cache - persisted state from previous runs

    Settings (in ~/.config/meshforge/map_settings.json):
    - node_cache_max_age_hours: Max age for node_cache.json (default: 48)
    - rns_cache_max_age_hours: Max age for RNS temp cache (default: 1)
    - online_status_threshold_minutes: Minutes since lastHeard to consider online (default: 15)
    """

    # Default cache ages in hours
    DEFAULT_NODE_CACHE_MAX_AGE_HOURS = 48
    DEFAULT_RNS_CACHE_MAX_AGE_HOURS = 1
    DEFAULT_ONLINE_THRESHOLD_MINUTES = 15
    # Meshtasticd connection defaults
    DEFAULT_MESHTASTICD_HOST = "localhost"
    DEFAULT_MESHTASTICD_PORT = 4403

    def __init__(self, cache_dir: Optional[Path] = None, enable_history: bool = True):
        if cache_dir:
            self._cache_dir = cache_dir
        else:
            try:
                from utils.paths import get_real_user_home
                self._cache_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            except ImportError:
                self._cache_dir = Path("/tmp/meshforge")

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "map_nodes.geojson"
        self._last_collect: Optional[float] = None
        self._cached_geojson: Optional[Dict] = None

        # User-configurable cache age settings
        try:
            from utils.common import SettingsManager
            self._settings = SettingsManager(
                "map_settings",
                defaults={
                    "node_cache_max_age_hours": self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS,
                    "rns_cache_max_age_hours": self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS,
                    "online_status_threshold_minutes": self.DEFAULT_ONLINE_THRESHOLD_MINUTES,
                    "meshtasticd_host": self.DEFAULT_MESHTASTICD_HOST,
                    "meshtasticd_port": self.DEFAULT_MESHTASTICD_PORT,
                }
            )
        except ImportError:
            self._settings = None

        # Track nodes without GPS for reporting
        self._nodes_without_position: List[Dict] = []
        self._total_nodes_seen: int = 0  # Total from meshtasticd (with + without GPS)

        # Node history database for position/state tracking over time
        self._history = None
        if enable_history:
            try:
                from utils.node_history import NodeHistoryDB
                db_path = self._cache_dir / "node_history.db"
                self._history = NodeHistoryDB(db_path=db_path)
            except Exception as e:
                logger.debug(f"Node history disabled: {e}")

    def get_node_cache_max_age_seconds(self) -> int:
        """Get max age for node_cache.json in seconds."""
        if self._settings:
            hours = self._settings.get("node_cache_max_age_hours", self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS)
        else:
            hours = self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS
        return int(hours * 3600)

    def get_rns_cache_max_age_seconds(self) -> int:
        """Get max age for RNS temp cache in seconds."""
        if self._settings:
            hours = self._settings.get("rns_cache_max_age_hours", self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS)
        else:
            hours = self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS
        return int(hours * 3600)

    def set_node_cache_max_age_hours(self, hours: int) -> None:
        """Set max age for node_cache.json in hours."""
        if self._settings:
            self._settings.set("node_cache_max_age_hours", hours)
            self._settings.save()
            logger.info(f"Node cache max age set to {hours} hours")

    def set_rns_cache_max_age_hours(self, hours: int) -> None:
        """Set max age for RNS temp cache in hours."""
        if self._settings:
            self._settings.set("rns_cache_max_age_hours", hours)
            self._settings.save()
            logger.info(f"RNS cache max age set to {hours} hours")

    def get_online_threshold_seconds(self) -> int:
        """Get online status threshold in seconds.

        Nodes heard within this threshold are considered online.
        Default: 15 minutes (900 seconds).
        """
        if self._settings:
            minutes = self._settings.get("online_status_threshold_minutes", self.DEFAULT_ONLINE_THRESHOLD_MINUTES)
        else:
            minutes = self.DEFAULT_ONLINE_THRESHOLD_MINUTES
        return int(minutes * 60)

    def set_online_threshold_minutes(self, minutes: int) -> None:
        """Set online status threshold in minutes.

        Args:
            minutes: Consider nodes online if heard within this many minutes.
                    Use higher values for networks with longer update intervals.
        """
        if self._settings:
            self._settings.set("online_status_threshold_minutes", minutes)
            self._settings.save()
            logger.info(f"Online status threshold set to {minutes} minutes")

    def get_meshtasticd_host(self) -> str:
        """Get meshtasticd host setting."""
        if self._settings:
            return self._settings.get("meshtasticd_host", self.DEFAULT_MESHTASTICD_HOST)
        return self.DEFAULT_MESHTASTICD_HOST

    def get_meshtasticd_port(self) -> int:
        """Get meshtasticd port setting."""
        if self._settings:
            return int(self._settings.get("meshtasticd_port", self.DEFAULT_MESHTASTICD_PORT))
        return self.DEFAULT_MESHTASTICD_PORT

    def set_meshtasticd_connection(self, host: str, port: int) -> None:
        """Set meshtasticd connection parameters.

        Args:
            host: Hostname or IP address of meshtasticd
            port: TCP port (default: 4403)
        """
        if self._settings:
            self._settings.set("meshtasticd_host", host)
            self._settings.set("meshtasticd_port", port)
            self._settings.save()
            logger.info(f"Meshtasticd connection set to {host}:{port}")

    def get_nodes_without_position(self) -> List[Dict]:
        """Get list of nodes that have no GPS position.

        Returns list of dicts with id, name, last_seen, network info.
        Updated after each collect() call.
        """
        return self._nodes_without_position

    def collect(self, max_age_seconds: int = 30) -> Dict[str, Any]:
        """Collect nodes from all sources, merge, and return GeoJSON.

        Args:
            max_age_seconds: Use cached data if collected within this window.

        Returns:
            GeoJSON FeatureCollection with all known nodes.
        """
        # Use cache if fresh enough
        if (self._cached_geojson and self._last_collect and
                time.time() - self._last_collect < max_age_seconds):
            return self._cached_geojson

        features: Dict[str, Dict] = {}  # id -> feature (dedup by id)

        # Source 1: meshtasticd TCP
        tcp_features = self._collect_meshtasticd()
        for f in tcp_features:
            fid = f["properties"].get("id", "")
            if fid:
                features[fid] = f

        # Source 1.5: Direct USB radio (when meshtasticd not running)
        # Only try this if TCP returned nothing (avoids double-connection)
        direct_radio_features = []
        if not tcp_features:
            direct_radio_features = self._collect_direct_radio()
            for f in direct_radio_features:
                fid = f["properties"].get("id", "")
                if fid:
                    features[fid] = f

        # Source 2: MQTT subscriber (if running)
        mqtt_features = self._collect_mqtt()
        for f in mqtt_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f
            elif fid and fid in features:
                # Merge: prefer newer data
                self._merge_feature(features[fid], f)

        # Source 3: Node tracker cache files
        tracker_features = self._collect_node_tracker()
        for f in tracker_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 4: AREDN mesh network
        aredn_features = self._collect_aredn()
        for f in aredn_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 5: Last-known cache (fill gaps)
        if not features:
            cache_features = self._load_cache()
            for f in cache_features:
                fid = f["properties"].get("id", "")
                if fid:
                    features[fid] = f

        sources = self._get_source_summary(
            tcp_features, mqtt_features, tracker_features, aredn_features, direct_radio_features
        )
        geojson = {
            "type": "FeatureCollection",
            "features": list(features.values()),
            "properties": {
                "collected_at": datetime.now().isoformat(),
                "source_count": len(features),
                "sources": sources,
                "total_nodes": self._total_nodes_seen,
                "nodes_with_position": len(features),
                "nodes_without_position": self._nodes_without_position,
                "nodes_without_position_count": len(self._nodes_without_position),
                "online_threshold_minutes": self.get_online_threshold_seconds() // 60,
            }
        }

        # Log collection summary for debugging
        logger.debug(
            f"MapDataCollector: {len(features)} nodes "
            f"(meshtasticd:{sources.get('meshtasticd', 0)} "
            f"direct_radio:{sources.get('direct_radio', 0)} "
            f"mqtt:{sources.get('mqtt', 0)} "
            f"tracker:{sources.get('node_tracker', 0)})"
        )

        # Cache result
        self._cached_geojson = geojson
        self._last_collect = time.time()
        self._save_cache(geojson)

        # Record to history database
        if self._history and geojson["features"]:
            try:
                self._history.record_observations(geojson["features"])
            except Exception as e:
                logger.debug(f"History recording error: {e}")

        return geojson

    def _collect_meshtasticd(self) -> List[Dict]:
        """Collect nodes from meshtasticd via TCP.

        Uses configurable host/port (default: localhost:4403).

        Strategy:
        1. Try the Python TCP interface (structured data, most reliable)
        2. Fall back to CLI parsing if Python module unavailable
        """
        features = []
        host = self.get_meshtasticd_host()
        port = self.get_meshtasticd_port()

        # Quick check if port is open before attempting connection
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result != 0:
                logger.debug(f"meshtasticd not reachable at {host}:{port}")
                return []
        except OSError:
            return []

        # Strategy 1: Use Python TCP interface via connection manager
        features = self._collect_via_tcp_interface()
        if features:
            return features

        # Strategy 2: Fall back to CLI parsing
        features = self._collect_via_cli()
        if features:
            logger.debug(f"meshtasticd (CLI): {len(features)} nodes with position")

        return features

    def _collect_via_tcp_interface(self) -> List[Dict]:
        """Collect nodes using the meshtastic Python TCP interface.

        Uses MeshtasticConnectionManager for safe locking and cleanup.
        Returns list of GeoJSON features for nodes with valid positions.
        Also populates self._nodes_without_position for nodes lacking GPS.
        """
        try:
            from utils.meshtastic_connection import get_connection_manager
        except ImportError:
            logger.debug("meshtastic_connection module not available")
            return []

        features = []
        no_position_nodes = []
        host = self.get_meshtasticd_host()
        port = self.get_meshtasticd_port()
        manager = get_connection_manager(host=host, port=port)

        # Don't block if someone else holds the connection
        if not manager.acquire_lock(timeout=5.0):
            logger.debug("Could not acquire meshtasticd lock (in use)")
            return []

        try:
            manager._wait_for_cooldown()
            interface = manager._create_interface()

            try:
                if hasattr(interface, 'nodes') and interface.nodes:
                    now = time.time()
                    online_threshold = self.get_online_threshold_seconds()
                    total_nodes = len(interface.nodes)

                    for node_id, node_data in interface.nodes.items():
                        feature = self._parse_tcp_node(node_id, node_data, now, online_threshold)
                        if feature:
                            features.append(feature)
                        else:
                            # Track nodes without valid position
                            no_pos_info = self._extract_node_info_without_position(
                                node_id, node_data, now, online_threshold
                            )
                            if no_pos_info:
                                no_position_nodes.append(no_pos_info)

                    # Update the tracking lists
                    self._nodes_without_position = no_position_nodes
                    self._total_nodes_seen = total_nodes

                    logger.debug(
                        f"meshtasticd (TCP): {len(features)} with GPS, "
                        f"{len(no_position_nodes)} without GPS (total: {total_nodes})"
                    )
            finally:
                from utils.meshtastic_connection import safe_close_interface
                safe_close_interface(interface)

        except Exception as e:
            logger.debug(f"TCP interface collection error: {e}")
        finally:
            manager.release_lock()

        return features

    def _collect_direct_radio(self) -> List[Dict]:
        """Collect nodes directly from USB radio (serial connection).

        Used when meshtasticd is not running (usb-direct mode).
        MeshForge connects directly to the radio via USB serial.

        Returns list of GeoJSON features for nodes with valid positions.
        """
        try:
            from utils.meshtastic_connection import (
                get_connection_manager, safe_close_interface, ConnectionMode,
                reset_connection_manager
            )
        except ImportError:
            logger.debug("meshtastic_connection module not available")
            return []

        # Check if USB device is available
        import glob
        usb_devices = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
        if not usb_devices:
            logger.debug("No USB radio devices found")
            return []

        features = []
        no_position_nodes = []

        # Reset manager to ensure we get SERIAL mode
        # (in case a previous TCP connection left it in TCP mode)
        reset_connection_manager()
        manager = get_connection_manager(mode=ConnectionMode.SERIAL)

        # Don't block if someone else holds the connection
        if not manager.acquire_lock(timeout=5.0):
            logger.debug("Could not acquire radio lock (in use)")
            return []

        try:
            manager._wait_for_cooldown()
            interface = manager._create_interface()

            try:
                if hasattr(interface, 'nodes') and interface.nodes:
                    now = time.time()
                    online_threshold = self.get_online_threshold_seconds()
                    total_nodes = len(interface.nodes)

                    for node_id, node_data in interface.nodes.items():
                        feature = self._parse_tcp_node(node_id, node_data, now, online_threshold)
                        if feature:
                            # Mark as from direct radio
                            feature["properties"]["source"] = "direct_radio"
                            features.append(feature)
                        else:
                            # Track nodes without valid position
                            no_pos_info = self._extract_node_info_without_position(
                                node_id, node_data, now, online_threshold
                            )
                            if no_pos_info:
                                no_position_nodes.append(no_pos_info)

                    # Update the tracking lists (if meshtasticd didn't already)
                    if not self._total_nodes_seen:
                        self._nodes_without_position = no_position_nodes
                        self._total_nodes_seen = total_nodes

                    logger.debug(
                        f"Direct radio (USB): {len(features)} with GPS, "
                        f"{len(no_position_nodes)} without GPS (total: {total_nodes})"
                    )
            finally:
                safe_close_interface(interface)

        except Exception as e:
            logger.debug(f"Direct radio collection error: {e}")
        finally:
            manager.release_lock()

        return features

    def _parse_tcp_node(self, node_id: str, data: dict, now: float,
                        online_threshold_seconds: int = 900) -> Optional[Dict]:
        """Parse a single node from the TCP interface nodes dict.

        Handles both float (latitude) and integer (latitudeI) coordinate formats.

        Args:
            node_id: The node ID string
            data: Raw node data from meshtastic interface
            now: Current timestamp
            online_threshold_seconds: Consider online if heard within this many seconds
        """
        position = data.get('position', {})
        if not position:
            return None

        # Extract coordinates - prefer float, fall back to integer / 1e7
        lat = position.get('latitude')
        if lat is None:
            lat_i = position.get('latitudeI')
            lat = lat_i / 1e7 if lat_i is not None else None

        lon = position.get('longitude')
        if lon is None:
            lon_i = position.get('longitudeI')
            lon = lon_i / 1e7 if lon_i is not None else None

        # Skip nodes without valid coordinates
        if lat is None or lon is None:
            return None
        if abs(lat) < 0.001 and abs(lon) < 0.001:
            return None

        # Extract user info
        user = data.get('user', {})
        device_metrics = data.get('deviceMetrics', {})

        # Determine online status from lastHeard (configurable threshold)
        # Default to online if node exists in nodedb (matches other mesh maps)
        last_heard = data.get('lastHeard', 0)
        is_online = True  # Node exists in nodedb = online

        # Format last_seen as human-readable
        if last_heard:
            age_seconds = int(now - last_heard)
            if age_seconds < 60:
                last_seen = f"{age_seconds}s ago"
            elif age_seconds < 3600:
                last_seen = f"{age_seconds // 60}m ago"
            elif age_seconds < 86400:
                last_seen = f"{age_seconds // 3600}h ago"
            else:
                last_seen = f"{age_seconds // 86400}d ago"
        else:
            last_seen = "unknown"

        # Format node_id
        node_num = data.get('num', 0)
        if isinstance(node_id, str) and node_id.startswith('!'):
            formatted_id = node_id
        elif node_num:
            formatted_id = f"!{node_num:08x}"
        else:
            formatted_id = str(node_id)

        return self._make_feature(
            node_id=formatted_id,
            name=user.get('longName', '') or user.get('shortName', ''),
            lat=lat,
            lon=lon,
            network='meshtastic',
            is_online=is_online,
            snr=data.get('snr'),
            battery=device_metrics.get('batteryLevel'),
            hardware=user.get('hwModel', ''),
            role=user.get('role', ''),
            is_gateway=user.get('role', '') in ('ROUTER', 'ROUTER_CLIENT'),
            via_mqtt=data.get('viaMqtt', False),
            is_local=(data.get('hopsAway', 99) == 0),
            last_seen=last_seen,
        )

    def _extract_node_info_without_position(self, node_id: str, data: dict, now: float,
                                            online_threshold_seconds: int = 900) -> Optional[Dict]:
        """Extract basic info for a node that has no valid GPS position.

        Returns a dict with id, name, last_seen, etc. for display in a table/list.
        """
        user = data.get('user', {})
        device_metrics = data.get('deviceMetrics', {})

        # Format node_id
        node_num = data.get('num', 0)
        if isinstance(node_id, str) and node_id.startswith('!'):
            formatted_id = node_id
        elif node_num:
            formatted_id = f"!{node_num:08x}"
        else:
            formatted_id = str(node_id)

        # All nodes in nodedb are considered online (matches other mesh maps)
        last_heard = data.get('lastHeard', 0)
        is_online = True

        # Format last_seen
        if last_heard:
            age_seconds = int(now - last_heard)
            if age_seconds < 60:
                last_seen = f"{age_seconds}s ago"
            elif age_seconds < 3600:
                last_seen = f"{age_seconds // 60}m ago"
            elif age_seconds < 86400:
                last_seen = f"{age_seconds // 3600}h ago"
            else:
                last_seen = f"{age_seconds // 86400}d ago"
        else:
            last_seen = "unknown"

        name = user.get('longName', '') or user.get('shortName', '')

        return {
            "id": formatted_id,
            "name": name or formatted_id,
            "is_online": is_online,
            "last_seen": last_seen,
            "hardware": user.get('hwModel', ''),
            "role": user.get('role', ''),
            "snr": data.get('snr'),
            "battery": device_metrics.get('batteryLevel'),
            "hops_away": data.get('hopsAway'),
            "via_mqtt": data.get('viaMqtt', False),
        }

    def _collect_via_cli(self) -> List[Dict]:
        """Fall back to CLI parsing when Python TCP interface unavailable."""
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli()
            if not cli_path:
                logger.debug("meshtastic CLI not found")
                return []

            host = self.get_meshtasticd_host()
            port = self.get_meshtasticd_port()
            host_arg = f"{host}:{port}" if port != 4403 else host

            result = subprocess.run(
                [cli_path, '--host', host_arg, '--info'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return []
            return self._parse_meshtastic_info(result.stdout)
        except FileNotFoundError:
            logger.debug("meshtastic CLI not found")
        except subprocess.TimeoutExpired:
            logger.debug("meshtastic CLI timed out")
        except Exception as e:
            logger.debug(f"CLI collection error: {e}")
        return []

    def _parse_meshtastic_info(self, output: str) -> List[Dict]:
        """Parse meshtastic --info output for node positions.

        Handles JSON node data that some versions of the CLI output.
        This is a fallback - the TCP interface is preferred.
        """
        features = []
        lines = output.split('\n')

        for line in lines:
            # Try to parse JSON-like node data from --info output
            if '{' in line and ('position' in line.lower() or 'latitude' in line.lower()):
                try:
                    start = line.index('{')
                    data = json.loads(line[start:])
                    if 'position' in data:
                        pos = data['position']
                        lat = pos.get('latitude')
                        if lat is None:
                            lat_i = pos.get('latitudeI')
                            lat = lat_i / 1e7 if lat_i else None
                        lon = pos.get('longitude')
                        if lon is None:
                            lon_i = pos.get('longitudeI')
                            lon = lon_i / 1e7 if lon_i else None

                        if lat and lon and not (abs(lat) < 0.001 and abs(lon) < 0.001):
                            user = data.get('user', {})
                            device_metrics = data.get('deviceMetrics', {})
                            feature = self._make_feature(
                                node_id=data.get('num', data.get('id', 'unknown')),
                                name=user.get('longName', ''),
                                lat=lat, lon=lon,
                                network='meshtastic',
                                is_online=True,
                                snr=data.get('snr'),
                                battery=device_metrics.get('batteryLevel'),
                                hardware=user.get('hwModel', ''),
                                role=user.get('role', ''),
                            )
                            features.append(feature)
                except (json.JSONDecodeError, ValueError, IndexError):
                    continue

        return features

    def _collect_mqtt(self) -> List[Dict]:
        """Collect nodes from MQTT subscriber if available."""
        try:
            from monitoring.mqtt_subscriber import MQTTNodelessSubscriber
            # Check if there's a running instance with cached data
            # The subscriber stores nodes in memory, so we need a running instance
            # For now, check if there's a cached MQTT node file
            mqtt_cache = self._cache_dir / "mqtt_nodes.json"
            if mqtt_cache.exists():
                age = time.time() - mqtt_cache.stat().st_mtime
                if age < 300:  # Less than 5 minutes old
                    with open(mqtt_cache) as f:
                        data = json.load(f)
                    if data.get("type") == "FeatureCollection":
                        return data.get("features", [])
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"MQTT collection error: {e}")

        return []

    def _collect_node_tracker(self) -> List[Dict]:
        """Collect nodes from UnifiedNodeTracker cache files."""
        features = []

        # Check node_cache.json
        try:
            from utils.paths import get_real_user_home
            cache_path = get_real_user_home() / ".config" / "meshforge" / "node_cache.json"
        except ImportError:
            import os as _os
            sudo_user = _os.environ.get('SUDO_USER', '')
            # Path traversal protection (security)
            if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
                cache_path = Path(f'/home/{sudo_user}/.config/meshforge/node_cache.json')
            else:
                # Avoid Path.home() which returns /root under sudo (MF001)
                cache_path = Path('/tmp/meshforge/node_cache.json')

        if cache_path.exists():
            try:
                age = time.time() - cache_path.stat().st_mtime
                max_age = self.get_node_cache_max_age_seconds()
                if age < max_age:  # Configurable, default 48 hours
                    with open(cache_path) as f:
                        data = json.load(f)

                    # Count nodes for logging
                    total_nodes = 0
                    if isinstance(data, list):
                        total_nodes = len(data)
                        for node in data:
                            feature = self._node_cache_to_feature(node)
                            if feature:
                                features.append(feature)
                    elif isinstance(data, dict) and "nodes" in data:
                        total_nodes = len(data["nodes"])
                        for node in data["nodes"]:
                            feature = self._node_cache_to_feature(node)
                            if feature:
                                features.append(feature)
                    elif isinstance(data, dict):
                        # Dict without "nodes" key - log for debugging
                        logger.debug(f"node_cache.json has dict format without 'nodes' key: {list(data.keys())}")

                    if features:
                        logger.debug(f"node_cache: {len(features)}/{total_nodes} nodes with position")
                else:
                    # Cache too old
                    age_hours = age / 3600
                    max_hours = max_age / 3600
                    logger.debug(f"node_cache.json too old: {age_hours:.1f}h > {max_hours:.1f}h max")
            except json.JSONDecodeError as e:
                logger.warning(f"node_cache.json JSON parse error: {e}")
            except PermissionError as e:
                logger.warning(f"node_cache.json permission denied: {e}")
            except Exception as e:
                logger.debug(f"Node cache read error: {e}")
        else:
            logger.debug(f"node_cache.json not found at: {cache_path}")

        # Check RNS nodes temp file
        rns_cache = Path("/tmp/meshforge_rns_nodes.json")
        if rns_cache.exists():
            rns_count = 0
            try:
                age = time.time() - rns_cache.stat().st_mtime
                max_age = self.get_rns_cache_max_age_seconds()
                if age < max_age:  # Configurable, default 1 hour
                    with open(rns_cache) as f:
                        data = json.load(f)

                    # Handle both list and dict-with-nodes format
                    nodes_list = []
                    if isinstance(data, list):
                        nodes_list = data
                    elif isinstance(data, dict) and "nodes" in data:
                        nodes_list = data["nodes"]

                    for node in nodes_list:
                        feature = self._rns_cache_to_feature(node)
                        if feature:
                            features.append(feature)
                            rns_count += 1

                    if rns_count:
                        logger.debug(f"rns_cache: {rns_count}/{len(nodes_list)} nodes with position")
                else:
                    age_mins = age / 60
                    max_mins = max_age / 60
                    logger.debug(f"RNS cache too old: {age_mins:.0f}m > {max_mins:.0f}m max")
            except Exception as e:
                logger.debug(f"RNS cache read error: {e}")

        return features

    def _collect_aredn(self) -> List[Dict]:
        """Collect nodes from AREDN mesh network.

        Scans the local AREDN network for nodes with GPS coordinates.
        AREDN nodes may have location data configured by the operator.
        """
        features = []

        try:
            from utils.aredn import AREDNScanner, AREDNClient
        except ImportError:
            logger.debug("AREDN module not available")
            return []

        # First try to connect to the local AREDN node
        local_node_ip = self._get_aredn_node_ip()
        if not local_node_ip:
            logger.debug("No AREDN node found on local network")
            return []

        try:
            # Get the local node info (may have location)
            client = AREDNClient(local_node_ip, timeout=5)
            local_node = client.get_node_info()

            if local_node:
                feature = self._aredn_node_to_feature(local_node)
                if feature:
                    features.append(feature)

                # Get neighbor nodes through links
                for link in local_node.links:
                    if link.ip:
                        try:
                            neighbor_client = AREDNClient(link.ip, timeout=3)
                            neighbor_node = neighbor_client.get_node_info()
                            if neighbor_node:
                                neighbor_feature = self._aredn_node_to_feature(neighbor_node)
                                if neighbor_feature:
                                    # Add link quality info
                                    neighbor_feature["properties"]["link_type"] = link.link_type.value
                                    neighbor_feature["properties"]["link_quality"] = link.link_quality
                                    neighbor_feature["properties"]["snr"] = link.snr if link.snr else None
                                    features.append(neighbor_feature)
                        except Exception as e:
                            logger.debug(f"Error fetching AREDN neighbor {link.ip}: {e}")

            if features:
                logger.debug(f"AREDN: {len(features)} nodes with position")

        except Exception as e:
            logger.debug(f"AREDN collection error: {e}")

        return features

    def _get_aredn_node_ip(self) -> Optional[str]:
        """Find AREDN node on local network."""
        import socket

        # Try common AREDN addresses
        for host in ['localnode.local.mesh', '10.0.0.1', '10.1.0.1', 'localnode']:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    result = sock.connect_ex((host, 80))
                    if result == 0:
                        return host
                finally:
                    sock.close()
            except Exception:
                continue
        return None

    def _aredn_node_to_feature(self, node) -> Optional[Dict]:
        """Convert AREDNNode to GeoJSON feature.

        Args:
            node: AREDNNode object from utils.aredn

        Returns:
            GeoJSON Feature dict or None if no valid location
        """
        # Check for valid location
        if not node.has_location():
            return None

        # Determine online status (if we got data, it's online)
        is_online = True

        # Determine if this is a "gateway" type node
        # AREDN nodes with tunnels act as gateways
        is_gateway = node.tunnel_count > 0

        return self._make_feature(
            node_id=f"aredn_{node.hostname}",
            name=node.hostname,
            lat=node.latitude,
            lon=node.longitude,
            network="aredn",
            is_online=is_online,
            is_gateway=is_gateway,
            hardware=node.model,
            role=node.mesh_status or "AREDN",
            last_seen="online",
        )

    def _node_cache_to_feature(self, node: Dict) -> Optional[Dict]:
        """Convert a node cache entry to a GeoJSON feature."""
        lat = node.get("latitude") or node.get("lat")
        lon = node.get("longitude") or node.get("lon")

        if not lat or not lon:
            pos = node.get("position", {})
            if pos:
                lat = pos.get("latitude") or (pos.get("latitudeI", 0) / 1e7)
                lon = pos.get("longitude") or (pos.get("longitudeI", 0) / 1e7)

        if not lat or not lon or (abs(lat) < 0.001 and abs(lon) < 0.001):
            return None

        return self._make_feature(
            node_id=node.get("id", node.get("node_id", "unknown")),
            name=node.get("name", node.get("long_name", "")),
            lat=lat, lon=lon,
            network=node.get("network", "meshtastic"),
            is_online=node.get("is_online", False),
            snr=node.get("snr"),
            battery=node.get("battery", node.get("battery_level")),
            hardware=node.get("hardware", node.get("hardware_model", "")),
            role=node.get("role", ""),
            is_gateway=node.get("is_gateway", False),
            via_mqtt=node.get("via_mqtt", False),
            last_seen=node.get("last_seen", ""),
        )

    def _rns_cache_to_feature(self, node: Dict) -> Optional[Dict]:
        """Convert an RNS node cache entry to a GeoJSON feature."""
        lat = node.get("latitude") or node.get("lat")
        lon = node.get("longitude") or node.get("lon")

        if not lat or not lon:
            pos = node.get("position", {})
            if pos:
                lat = pos.get("latitude", 0)
                lon = pos.get("longitude", 0)

        if not lat or not lon or (abs(lat) < 0.001 and abs(lon) < 0.001):
            return None

        return self._make_feature(
            node_id=node.get("id", node.get("rns_hash", "unknown")),
            name=node.get("name", node.get("display_name", "")),
            lat=lat, lon=lon,
            network="rns",
            is_online=node.get("is_online", False),
            snr=node.get("snr"),
            battery=node.get("battery"),
            hardware=node.get("hardware_model", ""),
            role=node.get("role", ""),
            is_gateway=node.get("is_gateway", False),
            last_seen=node.get("last_seen", ""),
        )

    def _make_feature(self, node_id: str, name: str, lat: float, lon: float,
                      network: str = "meshtastic", is_online: bool = True,
                      snr: Optional[float] = None, battery: Optional[int] = None,
                      hardware: str = "", role: str = "",
                      is_gateway: bool = False, via_mqtt: bool = False,
                      is_local: bool = False, last_seen: str = "") -> Dict:
        """Create a GeoJSON Feature for a node."""
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            },
            "properties": {
                "id": str(node_id),
                "name": name or str(node_id),
                "network": network,
                "is_online": is_online,
                "is_local": is_local,
                "is_gateway": is_gateway,
                "via_mqtt": via_mqtt,
                "snr": snr,
                "battery": battery,
                "last_seen": last_seen or ("online" if is_online else "unknown"),
                "hardware": hardware,
                "role": role,
            }
        }

    def _merge_feature(self, existing: Dict, new: Dict) -> None:
        """Merge new feature data into existing (prefer non-null values)."""
        for key, value in new["properties"].items():
            if value is not None and value != "" and value != "unknown":
                existing_val = existing["properties"].get(key)
                if existing_val is None or existing_val == "" or existing_val == "unknown":
                    existing["properties"][key] = value

    def _load_cache(self) -> List[Dict]:
        """Load last-known node state from disk cache."""
        if self._cache_file.exists():
            try:
                age = time.time() - self._cache_file.stat().st_mtime
                if age < 86400:  # Less than 24 hours old
                    with open(self._cache_file) as f:
                        data = json.load(f)
                    if data.get("type") == "FeatureCollection":
                        # Mark all cached nodes as potentially offline
                        for feature in data.get("features", []):
                            if age > 900:  # 15 minutes
                                feature["properties"]["is_online"] = False
                                feature["properties"]["last_seen"] = "cached"
                        return data.get("features", [])
            except Exception as e:
                logger.debug(f"Cache load error: {e}")
        return []

    def _save_cache(self, geojson: Dict) -> None:
        """Persist current node state to disk."""
        try:
            with open(self._cache_file, 'w') as f:
                json.dump(geojson, f)
        except Exception as e:
            logger.debug(f"Cache save error: {e}")

    def _get_source_summary(
        self, tcp: List, mqtt: List, tracker: List, aredn: List = None, direct_radio: List = None
    ) -> Dict:
        """Summarize which sources contributed data."""
        return {
            "meshtasticd": len(tcp),
            "direct_radio": len(direct_radio) if direct_radio else 0,
            "mqtt": len(mqtt),
            "node_tracker": len(tracker),
            "aredn": len(aredn) if aredn else 0,
        }
