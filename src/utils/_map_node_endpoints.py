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
import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, unquote

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

_SRTMProvider, _LOSAnalyzer, _HAS_TERRAIN = safe_import(
    'utils.terrain', 'SRTMProvider', 'LOSAnalyzer'
)


# ── Terrain endpoint guards (frontier security pass 2026-09-14) ──────
#
# /api/coverage and /api/los are the map's two compute+download amplifiers:
# one maximal coverage request is 172,800 elevation lookups (1.7 s on a
# Pi 5, ~5-6 s on a Pi 4, GIL-bound) and, before this pass, any coordinate
# an unauthenticated client picked could trigger a synchronous 25 MB S3
# download inside the handler. Three bounds, applied in this order:
#
#   1. _reject_if_untrusted()  — loopback / configured LAN only. "LAN" is
#      whatever --cors-origins names: the fleet unit derives it from EVERY
#      IPv4 address the box holds (one /24 each), so on that unit the gate
#      keeps out a router port-forward and any client NOT on an attached
#      /24 — it does NOT keep out a future second interface's own /24,
#      because the unit would trust it on the next restart (a unit-file
#      decision, reviewed 2026-09-14). With no --cors-origins at all the
#      gate is loopback-only and LAN browsers get 403 here — the secure
#      default; pinned by TestTerrainEndpointsAreGated.
#   2. finite-range validation — lat/lon/alt/freq/radius/resolution must
#      be finite and inside physical bounds. `float("nan")` used to parse
#      and publish a confident `is_clear: true` beside a bare NaN token
#      that no browser can JSON.parse.
#   3. _TERRAIN_SLOTS — a per-BOX bound (not per-client: client IP is a
#      weak key behind NAT/AREDN). Beyond N concurrent terrain
#      computations the answer is 503 + Retry-After, so a burst costs the
#      Pi one GIL's worth, never the whole map.
#
# The request path's provider is SHARED and never downloads
# (`auto_download=False`): tiles arrive through SRTMProvider.warm_tiles()
# at map warm-up, bounded and off the request thread (map_data_service).
def _slots_from_env(default: int = 2) -> int:
    """MESHFORGE_TERRAIN_SLOTS as a positive int; a bad value keeps the default
    (and says so) rather than failing the whole map at import."""
    raw = os.environ.get("MESHFORGE_TERRAIN_SLOTS", "")
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("MESHFORGE_TERRAIN_SLOTS=%r is not an int; using %d", raw, default)
        return default


_TERRAIN_SLOTS = threading.BoundedSemaphore(_slots_from_env())
_TERRAIN_RETRY_AFTER_S = 5
_TERRAIN_MISSING_NOTE = (
    "part of this path has no terrain tile cached on this box; the request "
    "path never downloads (auto_download is off). The map warms tiles around "
    "its own local nodes once at start-up; to add tiles now run "
    "scripts/srtm_warm.py on this box (or restart the map)"
)

_provider_lock = threading.Lock()
_provider_singleton = None


def _terrain_provider():
    """The map process's ONE terrain provider (request path, no downloads).

    Shared so the decoded-tile LRU and the missing-tile memory are
    per-process, not per-request: a per-request provider re-read 25 MB per
    tile per request and forgot every negative answer. Tests patch THIS
    name to inject a synthetic provider.
    """
    global _provider_singleton
    with _provider_lock:
        if _provider_singleton is None:
            _provider_singleton = _SRTMProvider(auto_download=False)
        return _provider_singleton


def _finite(name: str, raw, lo: float, hi: float) -> float:
    """Parse ``raw`` as a finite float inside ``[lo, hi]`` or raise ValueError.

    `float()` accepts "nan", "inf" and "1e309"; none of those is a
    coordinate, an antenna height or a frequency, and every one of them
    reached the analyzer before 2026-09-14.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {raw!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {raw!r}")
    if value < lo or value > hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}, got {value:g}")
    return value


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
        if self._reject_if_untrusted():
            return
        try:
            if len(parts) < 3:
                self._serve_json({"error": "Usage: /api/coverage/<lat>/<lon>/<height_m>"},
                                 status=400)
                return

            lat = _finite("lat", parts[0], -90.0, 90.0)
            lon = _finite("lon", parts[1], -180.0, 180.0)
            alt = _finite("height_m", parts[2], 0.0, 10000.0)

            # Parse query params

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            radius_km = _finite("radius_km", params.get('radius_km', ['10'])[0], 0.1, 50.0)
            freq_mhz = _finite("freq_mhz", params.get('freq_mhz', ['906'])[0], 1.0, 100000.0)
            resolution = int(_finite("resolution", params.get('resolution', ['24'])[0], 1, 48))

            # Get coverage prediction from terrain analyzer
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"}, status=503)
                return
            if not _TERRAIN_SLOTS.acquire(blocking=False):
                self._serve_terrain_busy()
                return
            try:
                provider = _terrain_provider()
                analyzer = _LOSAnalyzer(provider)
                coverage = analyzer.coverage_grid(
                    lat, lon, alt,
                    radius_km=radius_km,
                    freq_mhz=freq_mhz,
                    resolution=resolution
                )
            except Exception as e:
                logger.error(f"Coverage calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"}, status=500)
                return
            finally:
                _TERRAIN_SLOTS.release()

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
            self._serve_json({"error": f"Invalid parameters: {e}"}, status=400)
        except Exception as e:
            logger.error(f"Coverage endpoint error: {e}")
            self._serve_json({"error": str(e)}, status=500)

    def _serve_terrain_busy(self):
        """503 + Retry-After: every terrain slot on this box is in use."""
        body = json.dumps({
            "error": "terrain busy",
            "detail": (f"this box is already running its maximum concurrent "
                       f"terrain computations; retry in {_TERRAIN_RETRY_AFTER_S}s"),
            "retry_after_s": _TERRAIN_RETRY_AFTER_S,
        }).encode()
        self.send_response(503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Retry-After', str(_TERRAIN_RETRY_AFTER_S))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

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
        if self._reject_if_untrusted():
            return
        try:
            if len(parts) < 4:
                self._serve_json({"error": "Usage: /api/los/<lat1>/<lon1>/<lat2>/<lon2>"},
                                 status=400)
                return

            lat1 = _finite("lat1", parts[0], -90.0, 90.0)
            lon1 = _finite("lon1", parts[1], -180.0, 180.0)
            lat2 = _finite("lat2", parts[2], -90.0, 90.0)
            lon2 = _finite("lon2", parts[3], -180.0, 180.0)

            # Parse query params

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            alt1 = _finite("alt1", params.get('alt1', ['10'])[0], 0.0, 10000.0)
            alt2 = _finite("alt2", params.get('alt2', ['10'])[0], 0.0, 10000.0)
            freq_mhz = _finite("freq_mhz", params.get('freq_mhz', ['906'])[0], 1.0, 100000.0)

            # Calculate LOS
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"}, status=503)
                return
            if not _TERRAIN_SLOTS.acquire(blocking=False):
                self._serve_terrain_busy()
                return
            try:
                provider = _terrain_provider()
                analyzer = _LOSAnalyzer(provider)
                result = analyzer.analyze(lat1, lon1, alt1, lat2, lon2, alt2, freq_mhz)
            except Exception as e:
                logger.error(f"LOS calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"}, status=500)
                return
            finally:
                _TERRAIN_SLOTS.release()

            # Build elevation profile for visualization.
            #
            # These lists are the LOSResult contract (elevation_profile /
            # los_heights / fresnel_radii), read by name. This block used to
            # guard on `hasattr(result, 'profile')` and `result.obstructions`
            # — attributes LOSResult has NEVER had — so `profile` was always
            # [] and `obstruction_count` always 0, on every call, with real
            # terrain loaded. A hasattr() guard against a sibling module's
            # shape cannot fail loudly; it just quietly publishes nothing.
            # TestLOSResponseMatchesAnalyzer pins the names instead.
            elevations = result.elevation_profile
            los_heights = result.los_heights
            fresnel_radii = result.fresnel_radii
            n = len(elevations)
            profile = []
            if n and len(los_heights) == n and len(fresnel_radii) == n:
                for i in range(n):
                    t = i / max(1, n - 1)
                    los_h = los_heights[i]
                    radius = fresnel_radii[i]
                    profile.append({
                        "distance_m": result.distance_m * t,
                        "elevation_m": elevations[i],
                        "los_height_m": los_h,
                        "fresnel_top": los_h + radius,
                        "fresnel_bottom": los_h - radius,
                    })
            elif n:
                logger.warning(
                    "LOS profile lists disagree (elev=%d los=%d fresnel=%d) "
                    "— omitting profile rather than publishing a ragged one",
                    n, len(los_heights), len(fresnel_radii),
                )

            response = {
                "is_clear": result.is_clear,
                "distance_m": result.distance_m,
                "total_loss_db": result.total_loss_db,
                "terrain_loss_db": result.terrain_loss_db,
                # Free-space component on its own, so a client can show it
                # without subtracting two published numbers (or worse,
                # re-implementing the FSPL formula as a fourth copy).
                "fspl_db": result.fspl_db,
                "fresnel_clearance_pct": result.fresnel_clearance_pct,
                "obstruction_count": result.num_obstructions,
                "profile": profile,
                # Coverage rides WITH the verdict, never separately: every
                # field above is an opinion about invented ground wherever a
                # sample was missing. A consumer that ignores this renders a
                # confident "Clear LOS" over terrain nobody measured.
                "terrain_complete": result.terrain_complete,
                "terrain_samples_total": result.terrain_samples_total,
                "terrain_samples_missing": result.terrain_samples_missing,
                "endpoints": {
                    "from": {"lat": lat1, "lon": lon1, "alt": alt1},
                    "to": {"lat": lat2, "lon": lon2, "alt": alt2},
                }
            }
            if not result.terrain_complete:
                # Say WHICH lever to pull, not just that the answer is
                # incomplete: the request path never downloads, so an
                # honest "unknown" here stays unknown until someone warms
                # the tiles. Name them when the provider can.
                response["terrain_note"] = _TERRAIN_MISSING_NOTE
                missing_for = getattr(provider, "missing_tiles_for", None)
                if callable(missing_for):
                    n = max(2, len(elevations))
                    pts = [(lat1 + (lat2 - lat1) * i / (n - 1),
                            lon1 + (lon2 - lon1) * i / (n - 1)) for i in range(n)]
                    response["terrain_tiles_missing"] = missing_for(pts)
            self._serve_json(response)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"}, status=400)
        except Exception as e:
            logger.error(f"LOS endpoint error: {e}")
            self._serve_json({"error": str(e)}, status=500)
