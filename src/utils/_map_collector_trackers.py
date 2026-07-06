"""Tracker/MQTT/cache-file node sources for the map data collector.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).
Pure code motion — no behavior change.

Expects the following on the host class (MapDataCollector):
- self._record_diagnostic(...): per-source diagnostics recorder
- self._nodes_without_position: position-less node sidebar list
- self._cache_dir: cache directory Path
- self.get_node_cache_max_age_seconds() / self.get_rns_cache_max_age_seconds()
- self._node_cache_to_feature(...) / self._rns_cache_to_feature(...)
  (from RNSDataCollectorMixin)
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


def _hub():
    """Return the import hub module (utils.map_data_collector).

    Lazy on purpose: the moved methods must resolve module globals
    (``get_node_tracker``, ``get_local_subscriber``, ``get_real_user_home``)
    through the hub at call time so existing test patch targets on
    ``utils.map_data_collector.*`` keep applying to this extracted code.
    The import is deferred because the hub imports this module at top
    level (circular otherwise).
    """
    from utils import map_data_collector
    return map_data_collector


class TrackerDataCollectorMixin:
    """UnifiedNodeTracker, MQTT subscriber, and cache-file sources for MapDataCollector."""

    def _collect_unified_tracker(self) -> List[Dict]:
        """Collect nodes from the UnifiedNodeTracker singleton.

        The UnifiedNodeTracker is the richest data source — it merges nodes from
        RNS path table, meshtasticd, and the gateway bridge into a unified view.
        This is the same data the Topology view displays.

        Also appends non-Meshtastic nodes WITHOUT position (primarily MeshCore —
        its advertisements carry no GPS) to self._nodes_without_position, so the
        map UI can render them as a sidebar list and operators can assign
        positions via map_settings.json meshcore_positions.

        Returns:
            List of GeoJSON features for nodes with valid positions.
        """
        try:
            tracker = _hub().get_node_tracker()
            all_nodes = tracker.get_all_nodes()
            geojson = tracker.to_geojson()
            features = geojson.get("features", [])

            if features:
                # Enrich with additional properties the map expects
                for f in features:
                    props = f.get("properties", {})
                    if "via_mqtt" not in props:
                        props["via_mqtt"] = False
                    if "hardware" not in props:
                        props["hardware"] = ""
                    if "role" not in props:
                        props["role"] = ""
                    if "source" not in props:
                        props["source"] = "unified_tracker"
                    # Tier-aware tagging for the directory (Issue #49):
                    # the unified tracker mixes RNS + Meshtastic; route
                    # each by its protocol so retention applies correctly.
                    if not props.get("source_origin"):
                        net = (props.get("network") or "").lower()
                        if net == "meshtastic":
                            props["source_origin"] = "local_radio"
                        elif net == "rns":
                            props["source_origin"] = "rns_path_table"
                        else:
                            # MeshCore via tracker is local-RX (gateway bridge);
                            # everything else falls into local_radio bucket.
                            props["source_origin"] = "local_radio"

            # Capture non-Meshtastic nodes without position. Meshtastic's own
            # no-GPS nodes are handled by _collect_via_http (richer data available there);
            # for MeshCore/RNS/etc. the tracker is the only source.
            with_pos_ids = {n.id for n in tracker.get_nodes_with_position()}
            for node in all_nodes:
                if node.id in with_pos_ids:
                    continue
                if node.network == "meshtastic":
                    continue  # handled by HTTP collector
                try:
                    last_seen = node.get_age_string() if hasattr(node, "get_age_string") else None
                except Exception:
                    last_seen = None
                self._nodes_without_position.append({
                    "id": node.id,
                    "name": getattr(node, "name", node.id),
                    "network": node.network,
                    "is_online": getattr(node, "is_online", False),
                    "last_seen": last_seen,
                })

            # Distinguish three zero-yield cases for operator legibility:
            #   all_nodes=[], features=[]    → tracker simply empty (boot, no peers yet)
            #   all_nodes>0, features=0      → tracked peers exist but none have GPS
            #   (exception path below)       → tracker actually unreachable
            if not all_nodes:
                reason = "no_data"
                notes = "unified tracker empty — no peers known to this box"
            elif not features:
                reason = "no_positions"
                notes = f"{len(all_nodes)} tracked nodes, 0 have GPS"
            else:
                reason = None  # "ok" computed by _record_diagnostic
                notes = f"{len(all_nodes)} tracked total"
            self._record_diagnostic(
                "unified_tracker",
                attempted=len(all_nodes),
                yielded=len(features),
                reason_if_zero=reason,
                notes=notes,
            )
            return features

        except Exception as e:
            logger.debug(f"UnifiedNodeTracker collection error: {e}")
            self._record_diagnostic(
                "unified_tracker",
                attempted=0,
                yielded=0,
                reason_if_zero="unreachable",
                notes=str(e)[:200],
            )
            return []

    def _collect_mqtt(self) -> List[Dict]:
        """Collect nodes from MQTT subscriber if available.

        Tries the live subscriber singleton first (best data, includes sensors),
        then falls back to cached GeoJSON file.
        """
        live_connected = False
        live_features: List[Dict] = []
        # Try live subscriber first (has real-time sensor data)
        try:
            subscriber = _hub().get_local_subscriber()
            if subscriber.is_connected():
                live_connected = True
                geojson = subscriber.get_geojson()
                live_features = geojson.get("features", [])
                if live_features:
                    logger.debug(f"MQTT live: {len(live_features)} nodes with position")
                    self._record_diagnostic(
                        "mqtt",
                        attempted=len(live_features),
                        yielded=len(live_features),
                        notes="live subscriber",
                    )
                    return live_features
        except Exception as e:
            logger.debug(f"MQTT live collection error: {e}")

        # Fallback: cached MQTT node file
        try:
            mqtt_cache = self._cache_dir / "mqtt_nodes.json"
            if mqtt_cache.exists():
                age = time.time() - mqtt_cache.stat().st_mtime
                if age < 300:  # Less than 5 minutes old
                    with open(mqtt_cache) as f:
                        data = json.load(f)
                    if data.get("type") == "FeatureCollection":
                        cached = data.get("features", [])
                        self._record_diagnostic(
                            "mqtt",
                            attempted=len(cached),
                            yielded=len(cached),
                            notes=f"cached ({int(age)}s old)",
                        )
                        return cached
        except Exception as e:
            logger.debug(f"MQTT cache collection error: {e}")

        self._record_diagnostic(
            "mqtt",
            attempted=0, yielded=0,
            reason_if_zero="unreachable" if live_connected else "not_configured",
            notes="no live subscriber and no fresh cache",
        )
        return []

    def _collect_node_tracker(self) -> List[Dict]:
        """Collect nodes from UnifiedNodeTracker cache files."""
        features = []

        # Check node_cache.json
        cache_path = _hub().get_real_user_home() / ".config" / "meshforge" / "node_cache.json"

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

        # Check RNS nodes cache (operator-owned; shared path via the writer)
        from utils.paths import MeshForgePaths
        rns_cache = MeshForgePaths.rns_nodes_cache_path()
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
