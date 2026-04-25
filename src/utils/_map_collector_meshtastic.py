"""Meshtastic node collection for coverage maps.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).

Expects the following on the host class:
- self._is_valid_coordinate(lat, lon): coordinate validator
- self._make_feature(...): GeoJSON feature builder
- self._is_node_online(last_heard, source): online status check
- self._record_diagnostic(...): per-source diagnostic recorder
- self.get_meshtasticd_host() / self.get_meshtasticd_port()
- self.get_online_threshold_seconds()
- self._nodes_without_position: list to extend
- self._total_nodes_seen: int
"""

import json
import logging
import socket
import subprocess
import time
from typing import Dict, List, Optional

from utils.meshtastic_http import get_http_client
from utils.meshtastic_connection import (
    get_connection_manager, safe_close_interface,
    ConnectionMode, reset_connection_manager,
)

logger = logging.getLogger(__name__)


class MeshtasticDataCollectorMixin:
    """Mixin providing meshtasticd / direct-radio collection methods.

    Strategy ordering for `_collect_meshtasticd`:
      1. HTTP API (preferred — no TCP lock, doesn't conflict with gateway bridge)
      2. TCP interface via connection manager — needs exclusive lock
      3. CLI parsing — fallback when Python module unavailable
    """

    def _collect_meshtasticd(self) -> List[Dict]:
        """Collect nodes from meshtasticd.

        Uses configurable host/port (default: localhost:4403 TCP, 9443 HTTP).
        """
        host = self.get_meshtasticd_host()

        # Strategy 1: HTTP API (preferred — doesn't conflict with gateway bridge)
        features = self._collect_via_http(host)
        if features:
            self._record_diagnostic(
                "meshtasticd",
                attempted=self._total_nodes_seen or len(features),
                yielded=len(features),
                notes="via http",
            )
            return features

        # Strategy 2: TCP interface (needs lock)
        port = self.get_meshtasticd_port()

        # Quick check if TCP port is open before attempting connection
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result != 0:
                logger.debug(f"meshtasticd not reachable at {host}:{port}")
                self._record_diagnostic(
                    "meshtasticd",
                    attempted=0, yielded=0,
                    reason_if_zero="unreachable",
                    notes=f"{host}:{port} closed",
                )
                return []
        except OSError:
            self._record_diagnostic(
                "meshtasticd",
                attempted=0, yielded=0,
                reason_if_zero="unreachable",
                notes="socket check failed",
            )
            return []

        features = self._collect_via_tcp_interface()
        if features:
            self._record_diagnostic(
                "meshtasticd",
                attempted=len(features),
                yielded=len(features),
                notes="via tcp",
            )
            return features

        # Strategy 3: Fall back to CLI parsing
        features = self._collect_via_cli()
        if features:
            logger.debug(f"meshtasticd (CLI): {len(features)} nodes with position")

        return features

    def _collect_via_http(self, host: str) -> List[Dict]:
        """Collect nodes via meshtasticd's HTTP JSON API.

        Uses GET /json/nodes which returns all known mesh nodes without
        needing the TCP connection lock. Preferred — doesn't conflict
        with the gateway bridge.
        """
        try:
            client = get_http_client(host=host)
            if not client.is_available:
                logger.debug("meshtasticd HTTP API not available")
                return []

            nodes = client.get_nodes()
            if not nodes:
                return []

            features = []
            no_position_nodes = []

            for node in nodes:
                if node.has_position:
                    last_heard = node.last_heard or 0
                    is_online = self._is_node_online(last_heard, source="meshtastic")

                    feature = {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [node.longitude, node.latitude],
                        },
                        "properties": {
                            "id": node.node_id,
                            "name": node.long_name or node.short_name or node.node_id,
                            "short_name": node.short_name,
                            "network": "meshtastic",
                            "hardware": node.hw_model,
                            "snr": node.snr,
                            "last_heard": last_heard,
                            "via_mqtt": node.via_mqtt,
                            "role": node.role or "node",
                            "is_online": is_online,
                            "is_local": getattr(node, 'hops_away', None) == 0,
                            "is_gateway": getattr(node, 'role', '') in ('ROUTER', 'ROUTER_CLIENT'),
                            "hops_away": getattr(node, 'hops_away', None),
                            "altitude": node.altitude,
                            "source": "meshtasticd_http",
                        },
                    }
                    features.append(feature)
                else:
                    no_position_nodes.append({
                        "id": node.node_id,
                        "name": node.long_name or node.short_name or node.node_id,
                        "network": "meshtastic",
                        "hw_model": node.hw_model,
                        "snr": node.snr,
                        "last_heard": node.last_heard,
                    })

            # Append (not overwrite) so entries added by other sources
            # (MeshCore from _collect_unified_tracker, etc.) survive.
            self._nodes_without_position.extend(no_position_nodes)
            self._total_nodes_seen = len(nodes)

            logger.debug(
                f"meshtasticd (HTTP): {len(features)} with GPS, "
                f"{len(no_position_nodes)} without GPS (total: {len(nodes)})"
            )
            return features

        except Exception as e:
            logger.debug(f"HTTP collection error: {e}")
            return []

    def _collect_via_tcp_interface(self) -> List[Dict]:
        """Collect nodes using the meshtastic Python TCP interface.

        Uses MeshtasticConnectionManager for safe locking and cleanup.
        Returns list of GeoJSON features for nodes with valid positions.
        Also populates self._nodes_without_position for nodes lacking GPS.
        """
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
                            no_pos_info = self._extract_node_info_without_position(
                                node_id, node_data, now, online_threshold
                            )
                            if no_pos_info:
                                no_position_nodes.append(no_pos_info)

                    self._nodes_without_position.extend(no_position_nodes)
                    self._total_nodes_seen = total_nodes

                    logger.debug(
                        f"meshtasticd (TCP): {len(features)} with GPS, "
                        f"{len(no_position_nodes)} without GPS (total: {total_nodes})"
                    )
            finally:
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
        """
        import glob
        usb_devices = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
        if not usb_devices:
            logger.debug("No USB radio devices found")
            return []

        features = []
        no_position_nodes = []

        # Reset manager to ensure SERIAL mode
        # (in case a previous TCP connection left it in TCP mode)
        reset_connection_manager()
        manager = get_connection_manager(mode=ConnectionMode.SERIAL)

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
                            feature["properties"]["source"] = "direct_radio"
                            features.append(feature)
                        else:
                            no_pos_info = self._extract_node_info_without_position(
                                node_id, node_data, now, online_threshold
                            )
                            if no_pos_info:
                                no_position_nodes.append(no_pos_info)

                    # Extend (not overwrite). The `if not self._total_nodes_seen`
                    # gate stays — we still only want to claim _total_nodes_seen
                    # when meshtasticd didn't report, but the no-position list
                    # should always accumulate so MeshCore / RNS entries survive.
                    self._nodes_without_position.extend(no_position_nodes)
                    if not self._total_nodes_seen:
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
        """
        position = data.get('position', {})
        if not position:
            return None

        lat = position.get('latitude')
        if lat is None:
            lat_i = position.get('latitudeI')
            lat = lat_i / 1e7 if lat_i is not None else None

        lon = position.get('longitude')
        if lon is None:
            lon_i = position.get('longitudeI')
            lon = lon_i / 1e7 if lon_i is not None else None

        if not self._is_valid_coordinate(lat, lon):
            return None

        user = data.get('user', {})
        device_metrics = data.get('deviceMetrics', {})

        last_heard = data.get('lastHeard', 0)
        if last_heard and (now - last_heard) <= online_threshold_seconds:
            is_online = True
        elif last_heard:
            is_online = False
        else:
            is_online = False

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

        node_num = data.get('num', 0)
        if isinstance(node_id, str) and node_id.startswith('!'):
            formatted_id = node_id
        elif node_num:
            formatted_id = f"!{node_num:08x}"
        else:
            formatted_id = str(node_id)

        env_metrics = data.get('environmentMetrics', {})

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
            last_heard=last_heard,
            temperature=env_metrics.get('temperature'),
            humidity=env_metrics.get('relativeHumidity'),
            pressure=env_metrics.get('barometricPressure'),
            channel_utilization=device_metrics.get('channelUtilization'),
            air_util_tx=device_metrics.get('airUtilTx'),
        )

    def _extract_node_info_without_position(self, node_id: str, data: dict, now: float,
                                            online_threshold_seconds: int = 900) -> Optional[Dict]:
        """Extract basic info for a node that has no valid GPS position."""
        user = data.get('user', {})
        device_metrics = data.get('deviceMetrics', {})

        node_num = data.get('num', 0)
        if isinstance(node_id, str) and node_id.startswith('!'):
            formatted_id = node_id
        elif node_num:
            formatted_id = f"!{node_num:08x}"
        else:
            formatted_id = str(node_id)

        last_heard = data.get('lastHeard', 0)
        is_online = self._is_node_online(last_heard, source="meshtastic")

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
            "network": "meshtastic",
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
        Fallback only — the TCP interface is preferred.
        """
        features = []
        lines = output.split('\n')

        for line in lines:
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

                        if self._is_valid_coordinate(lat, lon):
                            user = data.get('user', {})
                            device_metrics = data.get('deviceMetrics', {})
                            cli_last_heard = data.get('lastHeard', 0)
                            feature = self._make_feature(
                                node_id=data.get('num', data.get('id', 'unknown')),
                                name=user.get('longName', ''),
                                lat=lat, lon=lon,
                                network='meshtastic',
                                is_online=self._is_node_online(cli_last_heard, source="meshtastic"),
                                snr=data.get('snr'),
                                battery=device_metrics.get('batteryLevel'),
                                hardware=user.get('hwModel', ''),
                                role=user.get('role', ''),
                                last_heard=cli_last_heard,
                            )
                            features.append(feature)
                except (json.JSONDecodeError, ValueError, IndexError):
                    continue

        return features
