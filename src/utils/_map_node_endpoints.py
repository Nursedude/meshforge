"""Node data endpoint mixin for :class:`MapRequestHandler`.

Holds the node-data surfaces of the map HTTP API:

- ``/api/nodes/geojson``    — live node GeoJSON (Issue #71 ResponseByteCache
                              hot path; bbox/region/preset filters compose)
- ``/api/nodes/directory``  — persistent node directory (Issue #49; Issue #70
                              single-flight DirectoryResponseCache hot path)
- ``/api/nodes/history``    — node history stats + unique nodes (24h)
- ``/api/nodes/trajectory/<id>`` — per-node trajectory GeoJSON
- ``/api/nodes/snapshot``   — historical network snapshot for playback
- ``/api/region-presets``   — region preset definitions
- ``/api/settings``         — GET/POST map settings (selected_region)
- ``/api/coverage/...``     — terrain-aware coverage prediction
- ``/api/los/...``          — line-of-sight analysis

Also carries the server-side View preset machinery (``VIEW_PRESETS`` +
``_apply_view_preset`` helpers) used by the geojson and directory
endpoints; ``map_http_handler`` re-exports those names so existing
imports keep working.

Extracted from ``map_http_handler.py`` to keep that file under the
1,500-line size cap (``CLAUDE.md``). No behaviour change — methods are
mixed into ``MapRequestHandler`` via inheritance and rely on the hub's
``self._serve_json`` / ``self._send_prebuilt_json`` / ``self._GZIP_MIN_BYTES``.
"""

import gzip
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, unquote

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

_SRTMProvider, _LOSAnalyzer, _HAS_TERRAIN = safe_import(
    'utils.terrain', 'SRTMProvider', 'LOSAnalyzer'
)


# ── Query parameter helper ────────────────────────────────────────────
def _safe_query_param(query, key, default=None):
    """Safely extract a single query parameter value."""
    values = query.get(key)
    if not values:
        return default
    return values[0] if values[0] else default


# ── Region presets ────────────────────────────────────────────────────
# Single source of truth lives in utils.region_presets so the MOC
# Analysis Tool and the map HTTP handler share the same bbox definitions.
from utils.region_presets import REGION_PRESETS  # noqa: E402,F401


# ── View presets (server-side filter mirror of web/node_map.html dropdown) ────
# Same six options the operator picks in the View dropdown. Moving these
# server-side shrinks /api/nodes/geojson + /api/nodes/directory from the
# 50K+-feature federated union to just the slice the preset wants. The
# client-side switch in node_map.html still runs as defense-in-depth.
#
# Each spec: optional `origins` (allowed source_origin values),
# `exclude_federated` (drop properties.federated=True), `max_age_s` (drop
# features whose numeric last_heard/last_seen is older than this).
# `custom`, `fleet_union`, `all_gps` are intentionally absent — they're
# no-ops on the server (everything passes through).
VIEW_PRESETS = {
    "live_rf": {
        "origins": {"local_radio"},
        "exclude_federated": True,
        "max_age_s": 300,
    },
    "live_rf_mqtt": {
        "origins": {"local_radio", "mqtt_local"},
        "exclude_federated": True,
        "max_age_s": 900,
    },
    "external_only": {
        "origins": {
            "meshcore_public", "aredn_worldmap",
            "public_fallback", "mqtt_global",
        },
    },
    "local_only": {
        "exclude_federated": True,
    },
    # Pass-through presets: validated as known so we can return a
    # 'preset_filtered' marker, but no predicate applies on the server.
    "fleet_union": {},
    "all_gps": {},
    "custom": {},
}


def _feature_numeric_timestamp(props: Dict[str, Any]) -> Optional[float]:
    """Pick the numeric last-seen timestamp from a feature's properties.

    Live geojson features carry both `last_seen` (human string) and
    `last_heard` (numeric epoch). Directory snapshot features carry only
    `last_seen` (numeric epoch). Federated peer features carry whatever
    the peer pushed — could be either shape. Returns the first numeric
    candidate, or None if neither field is a number.
    """
    for key in ("last_heard", "last_seen"):
        v = props.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _apply_view_preset(features: List[Dict[str, Any]],
                       preset: Optional[str],
                       now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Filter a list of GeoJSON features by a View preset.

    Pure function — no I/O, no DB, no lock. Pass through if `preset` is
    None, unknown, or maps to a no-op spec (custom/fleet_union/all_gps).
    """
    if not preset:
        return features
    spec = VIEW_PRESETS.get(preset)
    if not spec:
        return features  # unknown or pass-through preset
    if not (spec.get("origins") or spec.get("exclude_federated") or spec.get("max_age_s")):
        return features  # explicit no-op (fleet_union/all_gps/custom)

    now = now if now is not None else time.time()
    origins = spec.get("origins")
    exclude_fed = spec.get("exclude_federated", False)
    max_age = spec.get("max_age_s")

    out: List[Dict[str, Any]] = []
    for f in features:
        props = f.get("properties") or {}
        if exclude_fed and props.get("federated"):
            continue
        if origins is not None and props.get("source_origin", "") not in origins:
            continue
        if max_age is not None:
            ts = _feature_numeric_timestamp(props)
            if ts is None or (now - ts) > max_age:
                continue
        out.append(f)
    return out


def _apply_view_preset_to_position_less(
    entries: List[Dict[str, Any]],
    preset: Optional[str],
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Apply a View preset to position-less directory entries.

    The position_less list shape is dicts (not Features) carrying the
    same id/network/source_origin/last_seen/federated keys. Wrap into
    a synthetic Feature shape just long enough to reuse `_apply_view_preset`.
    """
    if not preset or preset not in VIEW_PRESETS:
        return entries
    spec = VIEW_PRESETS[preset]
    if not (spec.get("origins") or spec.get("exclude_federated") or spec.get("max_age_s")):
        return entries
    wrapped = [{"properties": e} for e in entries]
    filtered = _apply_view_preset(wrapped, preset, now=now)
    return [w["properties"] for w in filtered]


def build_geojson_response(
    collector,
    region_key: Optional[str],
    preset_key: Optional[str],
    bbox_str: Optional[str],
    gzip_min_bytes: int,
) -> tuple:
    """Build ``(raw_bytes, gzip_bytes_or_None)`` for a geojson response slice:
    ``collector.collect()`` → View-preset filter → region/bbox filter →
    cross-protocol collapse → ``json.dumps`` + ``gzip``.

    Extracted from ``_serve_geojson`` (Issue #71 regional-slice precompute) so a
    background warmer (``MapDataCollector._warm_geojson_regions``) produces
    byte-identical bytes to the request path and can pre-populate the response
    cache for the box's hot region slice. This is the SINGLE source of the
    geojson build — the handler and the warmer must never diverge, or warmed
    bytes would differ from what a live request would compute for the same key.
    """
    geojson = collector.collect()

    # View preset filter — applied before bbox so federation's 50K features
    # collapse to the preset slice (often <5K) before any geometry walk.
    # Unknown/missing preset is a pass-through.
    if preset_key in VIEW_PRESETS:
        spec = VIEW_PRESETS[preset_key]
        if spec.get("origins") or spec.get("exclude_federated") or spec.get("max_age_s"):
            filtered_features = _apply_view_preset(
                geojson.get("features", []), preset_key
            )
            geojson = dict(geojson)
            geojson["features"] = filtered_features
            props = dict(geojson.get("properties", {}))
            props["preset_filtered"] = True
            props["preset"] = preset_key
            props["nodes_with_position"] = len(filtered_features)
            geojson["properties"] = props

    bboxes: list = []

    if region_key and region_key in REGION_PRESETS:
        preset_bbox = REGION_PRESETS[region_key]["bbox"]
        if preset_bbox is not None:
            if isinstance(preset_bbox[0], list):
                bboxes = preset_bbox
            else:
                bboxes = [preset_bbox]

    # Explicit ?bbox= overrides ?region=. Reject malformed or out-of-range
    # coordinates so a crafted query can't stall the server (NaN/inf
    # arithmetic) or bypass the region allowlist.
    if bbox_str:
        MAX_BBOXES = 8
        parsed_bboxes: List[List[float]] = []
        for part in bbox_str.split(";")[:MAX_BBOXES]:
            try:
                coords = [float(x) for x in part.split(",")]
            except (ValueError, TypeError):
                continue
            if len(coords) != 4:
                continue
            if not all(isinstance(c, float) and c == c and c not in (float("inf"), float("-inf")) for c in coords):
                continue
            south, west, north, east = coords
            if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
                continue
            if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
                continue
            if south >= north or west >= east:
                continue
            parsed_bboxes.append(coords)
        if parsed_bboxes:
            bboxes = parsed_bboxes

    if bboxes:
        filtered = []
        for f in geojson.get("features", []):
            gc = f.get("geometry", {}).get("coordinates", [])
            if len(gc) < 2:
                continue
            lon, lat = gc[0], gc[1]
            for south, west, north, east in bboxes:
                if south <= lat <= north and west <= lon <= east:
                    filtered.append(f)
                    break
        geojson = dict(geojson)
        geojson["features"] = filtered
        props = dict(geojson.get("properties", {}))
        props["nodes_with_position"] = len(filtered)
        props["bbox_filtered"] = True
        geojson["properties"] = props

    # Cross-protocol collapse (node count opt §C). Applied AFTER preset and bbox
    # filters so the cached bytes are post-collapse and per-request `?bbox=`
    # still slices the canonical collection correctly.
    try:
        from utils.cross_protocol_collapse import collapse_cross_protocol
        collapsed_features, collapsed_pairs = collapse_cross_protocol(
            geojson.get("features", [])
        )
        geojson = dict(geojson)
        geojson["features"] = collapsed_features
        props = dict(geojson.get("properties", {}))
        props["collapsed_pairs"] = collapsed_pairs
        props["nodes_with_position"] = len(collapsed_features)
        geojson["properties"] = props
    except Exception as e:
        logger.debug(f"cross-protocol collapse skipped: {e}")

    raw = json.dumps(geojson).encode()
    gz = (
        gzip.compress(raw, compresslevel=6)
        if len(raw) >= gzip_min_bytes
        else None
    )
    return raw, gz


class NodeDataEndpointsMixin:
    """Node-data endpoints for :class:`MapRequestHandler`.

    Provides ``_serve_geojson``, ``_serve_directory``,
    ``_serve_history_stats``, ``_serve_trajectory``, ``_serve_snapshot``,
    ``_serve_region_presets``, ``_serve_settings``,
    ``_handle_settings_update``, ``_serve_coverage``, ``_serve_los``.
    """

    def _serve_geojson(self):
        """Serve live node GeoJSON with optional bbox/region/preset filtering.

        Supports three orthogonal filters that compose: ?region= (named
        bbox preset), ?bbox= (explicit bbox), and ?preset= (View preset
        — origin/age/federation predicates). Preset is applied first so
        the bbox pass walks a smaller list.

        Wrapped in a short-TTL response cache (Issue #71 / GitHub #1168).
        ``collect()`` + ``json.dumps`` + ``gzip.compress`` on the ~47 MB
        body holds the GIL for tens of seconds under cold load; concurrent
        callers used to stack independently and starve the watchdog's
        ``/healthz`` probe (same wedge class Issue #70 closed for the
        directory endpoint). Cache key is ``(bbox_str, region_key,
        preset_key)`` — each materially alters the response.
        """
        query = getattr(self, '_query', {})
        # Normalize cache key inputs to ``None`` when absent so a hit on
        # the unparameterized request shares state across callers that
        # pass empty strings vs. omit the param entirely.
        preset_key = _safe_query_param(query, "preset") or None
        region_key = _safe_query_param(query, "region") or None
        bbox_str = _safe_query_param(query, "bbox") or None
        cache_key = (bbox_str, region_key, preset_key)

        if self.collector is None:
            # Without a collector there's nothing to cache or serve —
            # return the empty FeatureCollection inline rather than
            # caching a partial-state response.
            self._serve_json({"type": "FeatureCollection", "features": []})
            return

        cache = self.collector._geojson_response_cache

        def _build() -> tuple:
            # Single source of the geojson build (Issue #71): the same function
            # the background warmer calls, so warmed bytes are byte-identical to
            # what a live request computes for the same (bbox, region, preset).
            return build_geojson_response(
                self.collector, region_key, preset_key, bbox_str,
                self._GZIP_MIN_BYTES,
            )

        try:
            raw_bytes, gzip_bytes, _was_built = cache.get_or_build(
                cache_key, _build
            )
        except Exception as e:
            logger.error(f"geojson build failed: {e}")
            self._serve_json(
                {
                    "type": "FeatureCollection",
                    "features": [],
                    "properties": {"error": str(e)[:200]},
                },
                status=500,
            )
            return

        self._send_prebuilt_json(raw_bytes, gzip_bytes, status=200)

    def _serve_region_presets(self):
        """Serve available region preset definitions."""
        self._serve_json(REGION_PRESETS)

    def _serve_settings(self):
        """Serve current map settings (selected region)."""
        settings = {"selected_region": None}
        if self.collector:
            settings["selected_region"] = self.collector._settings.get(
                "selected_region"
            )
        self._serve_json(settings)

    def _handle_settings_update(self):
        """Handle POST /api/settings — save map settings."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0 or content_length > 4096:
                self._serve_json({"error": "Invalid payload"}, status=400)
                return
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            self._serve_json({"error": "Invalid JSON"}, status=400)
            return

        region = data.get("selected_region")
        if region is not None and region not in REGION_PRESETS:
            self._serve_json({"error": "Unknown region"}, status=400)
            return

        if self.collector:
            self.collector._settings.set("selected_region", region)
            self.collector._settings.save()

        self._serve_json({"status": "saved", "selected_region": region})

    def _serve_history_stats(self):
        """Serve node history summary and unique nodes list."""
        if not self.collector or not self.collector._history:
            self._serve_json({"error": "history not available", "nodes": []})
            return

        history = self.collector._history
        result = {
            "stats": history.get_stats(),
            "nodes": history.get_unique_nodes(hours=24),
        }
        self._serve_json(result)

    def _serve_directory(self):
        """Serve the persistent node directory as a GeoJSON FeatureCollection.

        Returns every node ever heard (within tier retention) — superset
        of `/api/nodes/geojson`, which only covers what the latest
        collect cycle saw. Position-less nodes (MeshCore adverts without
        GPS, RNS announces) surface in the sibling `nodes_without_position`
        array, mirroring the convention from Issue #43.

        Reuses the gzip + JSON helper used by /api/nodes/geojson — same
        threshold (10 KB) applies. With ~50k nodes at the count cap, the
        directory dump is ~5 MB raw / ~700 KB gzipped.
        """
        if not self.collector or not self.collector._history:
            self._serve_json({
                "type": "FeatureCollection",
                "features": [],
                "properties": {"error": "history not available"},
                "nodes_without_position": [],
            })
            return

        # Optional ?preset= filter (same shape the live geojson endpoint
        # accepts). Directory features carry numeric `last_seen` epoch
        # (Issue #49) so age-based presets work here without the
        # last_heard fallback the live path needs.
        query = getattr(self, '_query', {})
        preset_key = _safe_query_param(query, "preset")
        # active_preset is the cache key + the value passed to the
        # filter. Pass-through presets (empty spec) produce bytes
        # identical to the unfiltered case, so we collapse them to
        # None to share the cache entry.
        active_preset: Optional[str] = None
        if preset_key in VIEW_PRESETS:
            spec = VIEW_PRESETS[preset_key]
            if spec.get("origins") or spec.get("exclude_federated") or spec.get("max_age_s"):
                active_preset = preset_key

        history = self.collector._history
        cache = self.collector._directory_response_cache

        def _build() -> tuple:
            # Single-flight build (Issue #70): the cache calls this at
            # most once per TTL window per preset across all concurrent
            # callers. The expensive work — DB scan + json.dumps + gzip
            # — runs once and the bytes are reused for ~5 s.
            features, position_less = history.get_directory_snapshot(
                include_position_less=True
            )
            preset_applied = active_preset is not None
            if preset_applied:
                features = _apply_view_preset(features, active_preset)
                position_less = _apply_view_preset_to_position_less(
                    position_less, active_preset
                )

            # Per-network breakdown alongside the full list — same shape
            # /api/status uses, so dashboards can consume either.
            by_network: Dict[str, int] = {}
            for entry in position_less:
                net = entry.get("network", "unknown")
                by_network[net] = by_network.get(net, 0) + 1

            properties = {
                "generated_at": datetime.now().isoformat(),
                "total_features": len(features),
                "total_position_less": len(position_less),
            }
            if preset_applied:
                properties["preset_filtered"] = True
                properties["preset"] = active_preset

            body = {
                "type": "FeatureCollection",
                "features": features,
                "properties": properties,
                "nodes_without_position": position_less,
                "nodes_without_position_by_network": by_network,
            }
            raw = json.dumps(body).encode()
            gz = (
                gzip.compress(raw, compresslevel=6)
                if len(raw) >= self._GZIP_MIN_BYTES
                else None
            )
            return raw, gz

        try:
            raw_bytes, gzip_bytes, was_built = cache.get_or_build(
                active_preset, _build
            )
        except Exception as e:
            logger.error(f"directory snapshot failed: {e}")
            self._serve_json({
                "type": "FeatureCollection",
                "features": [],
                "properties": {"error": str(e)[:200]},
                "nodes_without_position": [],
            }, status=500)
            return

        if was_built:
            # Size-budget alarm (Issue #64): record the serialized byte
            # count so `get_directory_stats()` can surface size_alarm in
            # /api/status. Only fired on cache miss — cache hits reuse
            # the value recorded by the originating build.
            try:
                history.record_directory_serialized_size(
                    len(raw_bytes),
                    len(gzip_bytes) if gzip_bytes else None,
                )
            except Exception as e:
                logger.debug("record_directory_serialized_size failed: %s", e)

        self._send_prebuilt_json(raw_bytes, gzip_bytes, status=200)

    def _serve_trajectory(self, node_id: str):
        """Serve trajectory GeoJSON for a specific node."""
        if not self.collector or not self.collector._history:
            self._serve_json({"error": "history not available"})
            return

        # URL decode the node_id (! becomes %21 in URLs)

        node_id = unquote(node_id)

        history = self.collector._history
        geojson = history.get_trajectory_geojson(node_id, hours=24)
        self._serve_json(geojson)

    def _serve_coverage(self, parts: List[str]):
        """Serve terrain-aware coverage prediction for a location.

        URL: /api/coverage/<lat>/<lon>/<antenna_height_m>
        Optional query params: radius_km (default 10), freq_mhz (default 906)
        """
        try:
            if len(parts) < 3:
                self._serve_json({"error": "Usage: /api/coverage/<lat>/<lon>/<height_m>"})
                return

            lat = float(parts[0])
            lon = float(parts[1])
            alt = float(parts[2])

            # Parse query params

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            radius_km = float(params.get('radius_km', ['10'])[0])
            freq_mhz = float(params.get('freq_mhz', ['906'])[0])
            resolution = int(params.get('resolution', ['24'])[0])

            # Limit resolution for performance
            resolution = min(resolution, 48)
            radius_km = min(radius_km, 50)

            # Get coverage prediction from terrain analyzer
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"})
                return
            try:
                provider = _SRTMProvider()
                analyzer = _LOSAnalyzer(provider)
                coverage = analyzer.coverage_grid(
                    lat, lon, alt,
                    radius_km=radius_km,
                    freq_mhz=freq_mhz,
                    resolution=resolution
                )
            except Exception as e:
                logger.error(f"Coverage calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"})
                return

            # Convert to GeoJSON for map display
            features = []
            for point in coverage:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [point["lon"], point["lat"]]
                    },
                    "properties": {
                        "is_clear": point["is_clear"],
                        "total_loss_db": point["total_loss_db"],
                        "terrain_loss_db": point["terrain_loss_db"],
                        "fresnel_pct": point["fresnel_clearance_pct"],
                        "distance_m": point["distance_m"],
                        "bearing": point["bearing"],
                    }
                })

            result = {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "center": [lon, lat],
                    "antenna_height_m": alt,
                    "radius_km": radius_km,
                    "freq_mhz": freq_mhz,
                }
            }
            self._serve_json(result)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"Coverage endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_snapshot(self):
        """Serve a historical network snapshot for playback.

        URL: /api/nodes/snapshot?timestamp=<unix_ts>&window=300
        """
        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                timestamp = float(params.get('timestamp', [str(time.time())])[0])
            except (ValueError, TypeError):
                timestamp = time.time()
            # Clamp the window: an unbounded ?window= forces a large DB scan +
            # GIL-heavy serialization directly on the request thread (this
            # endpoint is not behind the ResponseByteCache), letting one crafted
            # request stall other request threads. 1h is ample for playback.
            try:
                window = int(params.get('window', ['300'])[0])
            except (ValueError, TypeError):
                window = 300
            window = max(1, min(window, 3600))

            if not self.collector or not self.collector._history:
                self._serve_json({"error": "history not available", "features": []})
                return

            history = self.collector._history
            observations = history.get_snapshot(timestamp=timestamp, window_seconds=window)

            # Convert observations to GeoJSON features
            features = []
            for obs in observations:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [obs.longitude, obs.latitude]
                    },
                    "properties": {
                        "id": obs.node_id,
                        "name": obs.name,
                        "network": obs.network,
                        "is_online": obs.is_online,
                        "snr": obs.snr,
                        "battery": obs.battery,
                        "hardware": obs.hardware,
                        "role": obs.role,
                        "via_mqtt": obs.via_mqtt,
                        "timestamp": obs.timestamp,
                    }
                })

            result = {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "snapshot_time": timestamp,
                    "window_seconds": window,
                    "node_count": len(features),
                }
            }
            self._serve_json(result)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"Snapshot endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_los(self, parts: List[str]):
        """Serve line-of-sight analysis between two points.

        URL: /api/los/<lat1>/<lon1>/<lat2>/<lon2>
        Optional query params: alt1, alt2 (antenna heights, default 10m), freq_mhz (default 906)
        """
        try:
            if len(parts) < 4:
                self._serve_json({"error": "Usage: /api/los/<lat1>/<lon1>/<lat2>/<lon2>"})
                return

            lat1 = float(parts[0])
            lon1 = float(parts[1])
            lat2 = float(parts[2])
            lon2 = float(parts[3])

            # Parse query params

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            alt1 = float(params.get('alt1', ['10'])[0])
            alt2 = float(params.get('alt2', ['10'])[0])
            freq_mhz = float(params.get('freq_mhz', ['906'])[0])

            # Calculate LOS
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"})
                return
            try:
                provider = _SRTMProvider()
                analyzer = _LOSAnalyzer(provider)
                result = analyzer.analyze(lat1, lon1, alt1, lat2, lon2, alt2, freq_mhz)
            except Exception as e:
                logger.error(f"LOS calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"})
                return

            # Build elevation profile for visualization
            profile = []
            if hasattr(result, 'profile') and result.profile:
                for p in result.profile:
                    profile.append({
                        "distance_m": p.distance_m,
                        "elevation_m": p.ground_elevation,
                        "los_height_m": p.los_height,
                        "fresnel_top": p.los_height + p.fresnel_radius,
                        "fresnel_bottom": p.los_height - p.fresnel_radius,
                    })

            response = {
                "is_clear": result.is_clear,
                "distance_m": result.distance_m,
                "total_loss_db": result.total_loss_db,
                "terrain_loss_db": result.terrain_loss_db,
                "fresnel_clearance_pct": result.fresnel_clearance_pct,
                "obstruction_count": len(result.obstructions) if hasattr(result, 'obstructions') else 0,
                "profile": profile,
                "endpoints": {
                    "from": {"lat": lat1, "lon": lon1, "alt": alt1},
                    "to": {"lat": lat2, "lon": lon2, "alt": alt2},
                }
            }
            self._serve_json(response)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"LOS endpoint error: {e}")
            self._serve_json({"error": str(e)})
