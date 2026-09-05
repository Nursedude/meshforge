"""Meshtastic node collection for coverage maps.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).

Expects the following on the host class:
- self._is_valid_coordinate(lat, lon): coordinate validator
- self._make_feature(...): GeoJSON feature builder
- self._is_node_online(last_heard, source): online status check
- self._record_diagnostic(...): per-source diagnostic recorder
- self.get_meshtasticd_host() / self.get_meshtasticd_port()
- self.get_meshtasticd_tcp_collect_timeout_seconds()
- self.get_online_threshold_seconds()
- self._nodes_without_position: list to extend
- self._total_nodes_seen: int
"""

import json
import logging
import socket
import subprocess
import threading
import time
from typing import Dict, List, Optional

from utils.meshtastic_http import get_http_client
from utils.meshtastic_connection import (
    get_connection_manager, safe_close_interface,
    ConnectionMode, reset_connection_manager,
)

logger = logging.getLogger(__name__)

# A lastHeard more than this many seconds in the FUTURE is implausible (clock
# skew / forged stamp) → treated as unknown, not fresh. Matches the ±300s skew
# tolerance used by cloud_map_freshness.sh.
_FUTURE_SKEW_TOLERANCE_S = 300


class MeshtasticDataCollectorMixin:
    """Mixin providing meshtasticd / direct-radio collection methods.

    Strategy ordering for `_collect_meshtasticd`:
      1. HTTP API (preferred — no TCP lock, doesn't conflict with gateway bridge)
      2. TCP interface via connection manager — needs exclusive lock
      3. CLI parsing — fallback when Python module unavailable
    """

    def _gateway_owns_phoneapi(self) -> bool:
        """True iff meshforge-gateway is running on this box.

        The gateway holds the meshtasticd PhoneAPI (:4403), which is a
        SINGLE-consumer stream (#17). When it is up, this collector must NOT
        open a second TCP connection: the contention thrashes the PhoneAPI
        ("Force close previous TCP connection") and can wedge the gateway's
        mesh-TX — the 2026-06-13→15 incident where the bot's mesh output went
        dark for 2 days behind a green RNS canary. On a gateway box the MQTT
        source supplies mesh nodes instead. Unknown state → False (don't block
        collection; probe_meshtasticd_phoneapi_wedge catches any contention).
        """
        try:
            from utils.service_check import check_service, ServiceState
            state = check_service("meshforge-gateway").state
            return state in (ServiceState.AVAILABLE, ServiceState.DEGRADED)
        except Exception as e:  # noqa: BLE001 — can't tell → don't block
            logger.debug("gateway-owns-phoneapi check failed: %s", e)
            return False

    def _meshtasticd_present(self) -> bool:
        """True iff a local meshtasticd service is running.

        The direct-USB-radio fallback (_collect_direct_radio) is for usb-direct
        boxes where meshtasticd is ABSENT. Gating it on "meshtasticd returned no
        nodes" instead conflated a WEDGED or gateway-deferred daemon (empty
        result, daemon present) with an absent one — so the fallback opened
        /dev/ttyACM0, which on a gateway/claw box is a DIFFERENT radio (the
        dude-claw USB radio), starving it (#17 contention class, 2026-06-23).

        Unknown state → True (assume PRESENT). The conservative default here is
        the opposite of _gateway_owns_phoneapi's: when we can't tell, we must
        NOT grab the serial radio — a wrongly-skipped direct collect on a true
        usb-direct box just yields no local nodes that cycle (recoverable),
        whereas a wrongly-taken one seizes another subsystem's hardware.
        """
        try:
            from utils.service_check import check_service, ServiceState
            state = check_service("meshtasticd").state
            # Only a genuinely NOT_INSTALLED daemon means "this is a usb-direct
            # box". Running / degraded / failed / stopped / UNKNOWN all mean
            # "don't seize the radio" — installed-but-not-serving is NOT a
            # usb-direct box, and UNKNOWN must not overlap the absent domain
            # (honest-failure-modes #1: degraded value ≠ healthy/absent value).
            return state != ServiceState.NOT_INSTALLED
        except Exception as e:  # noqa: BLE001 — can't tell → assume present
            logger.debug("meshtasticd-present check failed: %s", e)
            return True

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

        # #17 single-consumer guard: defer to the gateway when it owns :4403,
        # so a second TCP consumer can't thrash/wedge it (the 06-13→15 bot-dark
        # incident). Mesh nodes still arrive via the MQTT source on a gateway box.
        if self._gateway_owns_phoneapi():
            logger.debug(
                "meshtasticd TCP collect skipped — meshforge-gateway owns "
                ":4403 (#17 single-consumer); using MQTT source"
            )
            self._record_diagnostic(
                "meshtasticd",
                attempted=0, yielded=0,
                reason_if_zero="deferred_to_gateway",
                notes="meshforge-gateway active — :4403 single-consumer (#17); nodes via MQTT",
            )
            return []

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

        # On a TCP-collect timeout the daemon is wedged; the CLI fallback hits
        # the same daemon and only adds latency to the held _collect_lock.
        # _collect_via_tcp_interface already recorded a collect_timeout
        # diagnostic, so skip the fallback this cycle.
        if getattr(self, "_meshtasticd_collect_wedged", False):
            return []

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

    def _drain_interface_nodes(self, interface, source, post_feature=None):
        """Read nodes off a connected interface into LOCAL lists.

        Pure with respect to ``self``'s mutable collection state (it only
        reads self's parse helpers), so it is SAFE to run inside a worker
        that the caller may abandon — the caller publishes the results into
        ``self`` only after a clean join. ``post_feature`` (optional) mutates
        each positioned feature in place (e.g. tag source=direct_radio).

        Returns ``(features, no_position_nodes, total_nodes)``.
        """
        features = []
        no_position = []
        total = 0
        if hasattr(interface, 'nodes') and interface.nodes:
            now = time.time()
            online_threshold = self.get_online_threshold_seconds()
            total = len(interface.nodes)
            for node_id, node_data in interface.nodes.items():
                feature = self._parse_tcp_node(node_id, node_data, now, online_threshold)
                if feature:
                    if post_feature is not None:
                        post_feature(feature)
                    features.append(feature)
                else:
                    no_pos_info = self._extract_node_info_without_position(
                        node_id, node_data, now, online_threshold
                    )
                    if no_pos_info:
                        no_position.append(no_pos_info)
            logger.debug(
                "%s: %d with GPS, %d without GPS (total: %d)",
                source, len(features), len(no_position), total,
            )
        return features, no_position, total

    def _collect_interface_bounded(self, manager, source, timeout, post_feature=None):
        """Connect + read the nodedb + close, bounded to ``timeout`` wall-clock.

        The meshtastic interface constructor blocks until the nodedb sync
        completes; a wedged daemon can stall it up to its ~900s idle cap,
        holding the map collector's ``_collect_lock`` and wedging every
        geojson/topology request behind it (the 2026-06-23 moc1 spin). We run
        the full connect -> read -> close lifecycle in a worker that OWNS the
        connection-manager lock for its whole life; the caller waits at most
        ``timeout``. On timeout we return ``([], 0, True)`` and let the worker
        finish and clean up in the background — it keeps the connection-manager
        lock so a concurrent collect fails-fast (5s) instead of opening a
        second contending PhoneAPI stream (#17).

        The worker writes ONLY to its local ``result`` dict, never to ``self``'s
        mutable node lists, so an abandoned worker can never corrupt a later
        collect cycle (honest-failure-modes #8). The caller publishes results
        into ``self`` only on a clean join. A timeout always leaves a witness:
        a WARNING log plus a ``collect_timeout`` diagnostic.

        Returns ``(features, total_nodes, timed_out)``.
        """
        result = {"features": [], "no_position": [], "total": 0,
                  "lock_contended": False}

        def _worker():
            # Full lifecycle under the connection-manager lock. acquire_lock
            # is bounded (5s); a concurrent/abandoned holder makes us fail-fast.
            if not manager.acquire_lock(timeout=5.0):
                logger.debug("Could not acquire %s lock (in use)", source)
                result["lock_contended"] = True
                return
            try:
                manager._wait_for_cooldown()
                interface = manager._create_interface()  # may block on nodedb sync
                try:
                    feats, no_pos, total = self._drain_interface_nodes(
                        interface, source, post_feature
                    )
                    result["features"] = feats
                    result["no_position"] = no_pos
                    result["total"] = total
                finally:
                    safe_close_interface(interface)
            except Exception as e:  # noqa: BLE001 — degraded source, not fatal
                logger.debug("%s interface collection error: %s", source, e)
            finally:
                manager.release_lock()

        worker = threading.Thread(
            target=_worker, daemon=True, name=f"{source}-collect"
        )
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            logger.warning(
                "%s collect exceeded %.0fs — likely wedged meshtasticd; served "
                "no nodes this cycle (cache + other sources unaffected). Worker "
                "finishes + holds the lock so concurrent collects fail-fast.",
                source, timeout,
            )
            self._record_diagnostic(
                source, attempted=0, yielded=0,
                reason_if_zero="collect_timeout",
                notes=(f"nodedb sync exceeded {timeout:.0f}s — abandoned; "
                       "worker cleans up + holds lock in background"),
            )
            return [], 0, True
        if result["lock_contended"]:
            # A prior cycle's worker still holds the connection lock — almost
            # always an abandoned worker on a wedged meshtasticd keeping a
            # PhoneAPI TCP open (up to ~300s). Report WEDGED (timed_out=True) so
            # _collect_meshtasticd SKIPS the CLI fallback; otherwise the fallback
            # opens ANOTHER `meshtastic --host` PhoneAPI stream against the same
            # wedged daemon every cycle — the #17/#75 leak (persistent
            # unaccounted TCP starving :9443). Before, this branch returned
            # timed_out=False with NO diagnostic, so the degraded state had no
            # witness and the fallback thrashed the daemon.
            self._record_diagnostic(
                source, attempted=0, yielded=0,
                reason_if_zero="lock_contended",
                notes="prior collect still holds the connection lock (wedged daemon?)",
            )
            return [], 0, True
        # Clean join — publish from THIS thread. The worker never touches self's
        # mutable lists, so this is the only writer (no interleave, #8).
        self._nodes_without_position.extend(result["no_position"])
        return result["features"], result["total"], False

    def _collect_via_tcp_interface(self) -> List[Dict]:
        """Collect nodes using the meshtastic Python TCP interface.

        Uses MeshtasticConnectionManager for safe locking and cleanup, and
        bounds the connect + nodedb sync to
        ``get_meshtasticd_tcp_collect_timeout_seconds()`` so a wedged daemon
        can't pin ``_collect_lock`` (the 2026-06-23 moc1 spin).
        Returns list of GeoJSON features for nodes with valid positions.
        Also populates self._nodes_without_position for nodes lacking GPS.
        """
        host = self.get_meshtasticd_host()
        port = self.get_meshtasticd_port()
        manager = get_connection_manager(host=host, port=port)
        timeout = self.get_meshtasticd_tcp_collect_timeout_seconds()

        features, total, timed_out = self._collect_interface_bounded(
            manager, "meshtasticd", timeout
        )
        # Surface a wedge so _collect_meshtasticd skips the CLI fallback (it
        # hits the same wedged daemon and only adds latency to the held lock).
        self._meshtasticd_collect_wedged = timed_out
        if total:
            self._total_nodes_seen = total
        return features

    def _collect_direct_radio(self) -> List[Dict]:
        """Collect nodes directly from USB radio (serial connection).

        Used when meshtasticd is not running (usb-direct mode). MeshForge
        connects directly to the radio via USB serial. Bounded by the same
        wall-clock cap as the TCP path so a wedged radio can't pin
        ``_collect_lock``.
        """
        import glob
        usb_devices = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
        if not usb_devices:
            logger.debug("No USB radio devices found")
            return []

        # Reset manager to ensure SERIAL mode
        # (in case a previous TCP connection left it in TCP mode)
        reset_connection_manager()
        manager = get_connection_manager(mode=ConnectionMode.SERIAL)
        timeout = self.get_meshtasticd_tcp_collect_timeout_seconds()

        def _tag_direct(feature):
            feature["properties"]["source"] = "direct_radio"

        features, total, _timed_out = self._collect_interface_bounded(
            manager, "direct_radio", timeout, post_feature=_tag_direct
        )
        # Only claim _total_nodes_seen when meshtasticd didn't already report;
        # the no-position list always accumulates (handled in the helper) so
        # MeshCore / RNS entries survive.
        if total and not self._total_nodes_seen:
            self._total_nodes_seen = total
        return features

    @staticmethod
    def _canonical_meshtastic_id(id_val, num):
        """Canonical ``!hex`` meshtastic node id.

        Prefer an explicit ``!hex`` string; else format the numeric node number
        as ``!{num:08x}``; else stringify what we have. A numeric ``num`` must
        NEVER become the id verbatim — the decimal string breaks dedup against
        the same node's ``!hex`` id from MQTT/tracker sources and double-writes
        the directory (the fleet-wide numeric-key bug class, K1's _ensure_node
        lesson)."""
        if isinstance(id_val, str) and id_val.startswith('!'):
            return id_val
        try:
            n = int(num)
        except (TypeError, ValueError):
            n = 0
        if n:
            return f"!{n:08x}"
        return str(id_val) if id_val not in (None, '') else 'unknown'

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
        # Wall clock is forgeable (RTC-less Pis, NTP steps, a hostile sender): a
        # future lastHeard makes (now - last_heard) negative → "online" forever
        # and a nonsense "-42s ago" string. Treat a meaningfully-future stamp as
        # unknown; a small negative (benign skew) rounds to "0s ago".
        age_seconds = int(now - last_heard) if last_heard else None
        if age_seconds is not None and age_seconds < -_FUTURE_SKEW_TOLERANCE_S:
            last_heard = 0
            age_seconds = None

        is_online = (age_seconds is not None
                     and age_seconds <= online_threshold_seconds)

        if age_seconds is not None:
            age_seconds = max(0, age_seconds)
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

        formatted_id = self._canonical_meshtastic_id(node_id, data.get('num', 0))

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
            # POSITIONED path — this is the one that reaches the observation
            # time-series. record_observations() requires lat/lon, so the
            # position-less builder below never contributes a row to it; a fix
            # applied only there populates nothing (learned the hard way
            # 2026-09-05: the column shipped and 377 rows landed with NULL).
            voltage=device_metrics.get('voltage'),
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

        formatted_id = self._canonical_meshtastic_id(node_id, data.get('num', 0))

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
            # Pack voltage rides the SAME DeviceMetrics packet as batteryLevel
            # and was simply never read here (2026-09-05). batteryLevel is a
            # 1/16-quantized SoC percentage — one 6.25% step is ~3h on a
            # 4000mAh pack, so it cannot draw a discharge curve; voltage is
            # mV-resolution and separates charging from discharging.
            # node_history._clean_voltage() rejects the 0.0 "no pack measured"
            # sentinel that USB-powered nodes report.
            "voltage": device_metrics.get('voltage'),
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
                                node_id=self._canonical_meshtastic_id(
                                    data.get('id') or user.get('id'), data.get('num')),
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
