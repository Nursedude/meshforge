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


def tracker_diagnostic(tracked, served_pos, dropped_pos, listed, dropped_listed, window_h):
    """(reason_if_zero, notes) for the unified-tracker source — ONE function
    of the counts (TRACKER-FORGET r3: the note was wrong TWICE in branches
    nobody enumerated — "nothing current" beside 543 listed entries, then
    "0 have GPS" when the GPS records were dropped for age). Every phrase is
    a claim about what THIS reader served; tests/test_map_tracker_snapshot_age
    checks it over the full count table. reason None = "ok" (served GPS).

    tracked        nodes in the tracker (incl. position-less Meshtastic, which
                   the HTTP collector serves — never counted here)
    served_pos     positioned features served       dropped_pos     ... dropped for age
    listed         position-less entries listed      dropped_listed  ... dropped for age
    """
    if not tracked:
        return "no_data", "unified tracker empty — no peers known to this box"
    dropped = dropped_pos + dropped_listed
    dn = (f"; {dropped} older than {window_h:g} h dropped (node_cache_max_age_hours)"
          if dropped else "")
    if served_pos:
        return None, f"{tracked} tracked total{dn}"
    if not listed and dropped:
        return "no_data", f"{tracked} tracked, this reader serves nothing current{dn}"
    if dropped_pos:
        return "no_positions", f"{tracked} tracked, 0 current with GPS{dn}"
    return "no_positions", f"{tracked} tracked nodes, 0 have GPS{dn}"


class TrackerDataCollectorMixin:
    """UnifiedNodeTracker, MQTT subscriber, and cache-file sources for MapDataCollector."""

    def _normalize_meshtastic_props(self, props: Dict) -> None:
        """ONE rule for a Meshtastic marker from a non-radio source (2026-11-08):
        the canonical ``!xxxxxxxx`` id (H15) and is_online re-judged from the
        node's own heard time with the map's window (firmware's 2 h). The
        tracker / MQTT subscriber / node cache each judged by their OWN window
        (15 min). No heard time -> never online: a cache is not evidence."""
        if not isinstance(props, dict) or (props.get("network") or "").lower() != "meshtastic":
            return
        if props.get("id") in (None, ""):
            return  # id-less: the collect drops it; never mint an 'unknown' key (review r3)
        props["id"] = self._canonical_meshtastic_id(props.get("id"), 0)
        props["is_online"] = self._is_node_online(props.get("last_heard"), source="meshtastic")

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

            # TRACKER-FORGET slice 1 (2026-11-21, MEASURED on the lab manager box): the
            # map's singleton is constructed, never started — it serves the
            # node_cache.json snapshot it loaded (there: saved 2026-08-03, 236
            # positioned records all >180 d, tagged local_radio below). The
            # file reader for the SAME artifact refuses a file older than
            # get_node_cache_max_age_seconds(); this reader shares that ONE
            # window per node (hfm #5) and counts what it drops (hfm #9).
            # Unknown age is held, never dropped (hfm #2).
            window_s = self.get_node_cache_max_age_seconds()
            now = time.time()

            def _too_old(epoch) -> bool:
                return isinstance(epoch, (int, float)) and now - epoch > window_s

            kept = [f for f in features
                    if not _too_old((f.get("properties") or {}).get("last_heard"))]
            dropped_pos = len(features) - len(kept)
            dropped_listed = 0
            features = kept

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
            listed = 0   # position-less entries THIS reader kept (the all-stale note's other half)
            for node in all_nodes:
                if node.id in with_pos_ids:
                    continue
                if node.network == "meshtastic":
                    continue  # handled by HTTP collector
                ls = getattr(node, "last_seen", None)
                if ls is not None and hasattr(ls, "timestamp") and _too_old(ls.timestamp()):
                    dropped_listed += 1
                    continue
                try:
                    last_seen = node.get_age_string() if hasattr(node, "get_age_string") else None
                except Exception:
                    last_seen = None
                listed += 1
                self._nodes_without_position.append({
                    "id": node.id,
                    "name": getattr(node, "name", node.id),
                    "network": node.network,
                    "is_online": getattr(node, "is_online", False),
                    "last_seen": last_seen,
                    # MAP-H H4a: same origin rule as the positioned tracker
                    # features above — untagged lost every federation merge.
                    "source_origin": ("rns_path_table" if node.network == "rns"
                                      else "local_radio"),
                })

            self._tracker_stale_dropped = dropped_pos + dropped_listed
            reason, notes = tracker_diagnostic(
                len(all_nodes), len(features), dropped_pos, listed, dropped_listed,
                window_s / 3600)
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
            self._tracker_stale_dropped = None   # unobservable this cycle, never 0
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
        have_subscriber = False
        live_features: List[Dict] = []
        # Try live subscriber first (has real-time sensor data)
        try:
            subscriber = _hub().get_local_subscriber()
            have_subscriber = subscriber is not None
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

        # H12 r2 (Fable F3): connected-but-empty ANSWERED (no_data); a subscriber
        # that exists but is not connected is a DOWN broker (unreachable), not
        # "off by design"; only no subscriber at all is not_configured.
        if live_connected:
            reason, note = "no_data", "live subscriber connected, no nodes with position"
        elif have_subscriber:
            reason, note = "unreachable", "subscriber not connected to the broker and no fresh cache"
        else:
            reason, note = "not_configured", "no MQTT subscriber on this box and no fresh cache"
        self._record_diagnostic("mqtt", attempted=0, yielded=0,
                                reason_if_zero=reason, notes=note)
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
