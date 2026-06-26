"""MeshCore public-map node collection for coverage maps.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).

Reads https://map.meshcore.dev/api/v1/nodes (~12 MB / ~30 k global nodes,
"changes hourly" by contract). MeshCore advertisements carry no GPS by
protocol design, so without this source MeshCore is invisible on the map.

Expects on the host class:
- self._settings: SettingsManager
- self._is_node_online(last_heard, source): online status check
- self._record_diagnostic(...): per-source diagnostic recorder
- self._meshcore_public_cache, self._meshcore_public_cache_ts: cache state
- self.DEFAULT_MESHCORE_PUBLIC_CACHE_TTL_SECONDS, self.DEFAULT_SOURCE_TIMEOUT_SECONDS
"""

import json
import logging
import time
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class MeshCorePublicCollectorMixin:
    """Mixin providing MeshCore public-map collection with two-tier caching.

    The 30 s outer cache (`collect()`'s `max_age_seconds`) prevents
    within-burst refetches; this mixin's per-source cache (default 600 s)
    absorbs the cache-miss case so a slow MeshCore fetch doesn't block
    `_collect_lock` for 30 s on every cache-miss collect.
    """

    # MeshCore node type ids from the public map API schema.
    _MESHCORE_NODE_TYPES = {1: "client", 2: "repeater", 3: "room_server"}
    _MESHCORE_MAP_URL = "https://map.meshcore.dev/api/v1/nodes"

    def _collect_meshcore_public(self) -> List[Dict]:
        """Return MeshCore public-map features, served from cache when fresh.

        On fetch error within the TTL, serve the stale cache with a
        `stale_cached` diagnostic instead of going dark.
        """
        if not self._settings or not self._settings.get("enable_meshcore_public", True):
            self._record_diagnostic(
                "meshcore_public",
                attempted=0, yielded=0,
                reason_if_zero="source_disabled",
                notes="set enable_meshcore_public=true in map_settings.json",
            )
            return []

        ttl = int(self._settings.get(
            "meshcore_public_cache_ttl_seconds",
            self.DEFAULT_MESHCORE_PUBLIC_CACHE_TTL_SECONDS,
        ))
        now = time.time()
        cached = self._meshcore_public_cache
        cached_ts = self._meshcore_public_cache_ts
        if cached is not None and cached_ts is not None and now - cached_ts < ttl:
            age = int(now - cached_ts)
            self._record_diagnostic(
                "meshcore_public",
                attempted=len(cached), yielded=len(cached),
                reason_if_zero=None if cached else "no_positions",
                notes=f"cached ({age}s old, ttl {ttl}s)",
            )
            return cached

        features, attempted, ok, fetch_notes = self._fetch_meshcore_public_uncached()
        if ok:
            self._meshcore_public_cache = features
            self._meshcore_public_cache_ts = now
            reason = None
            if not features:
                # `attempted > 0` and `yielded == 0` would naturally produce
                # "no_positions", but the API list itself was empty when
                # attempted == 0 — call that "unreachable" so operators can
                # distinguish "API down" from "API healthy, no parseable nodes".
                reason = "no_positions" if attempted > 0 else "unreachable"
            self._record_diagnostic(
                "meshcore_public",
                attempted=attempted, yielded=len(features),
                reason_if_zero=reason,
                notes=fetch_notes,
            )
            return features

        # Fetch failed. Serve stale cache if we have one (otherwise empty).
        if cached is not None:
            stale_age = int(now - cached_ts) if cached_ts else -1
            self._record_diagnostic(
                "meshcore_public",
                attempted=len(cached), yielded=len(cached),
                reason_if_zero="stale_cached",
                notes=f"fetch failed, serving stale ({stale_age}s old): {fetch_notes}",
            )
            return cached
        self._record_diagnostic(
            "meshcore_public",
            attempted=0, yielded=0,
            reason_if_zero="unreachable",
            notes=fetch_notes,
        )
        return []

    def _fetch_meshcore_public_uncached(self) -> tuple:
        """Fetch + parse MeshCore public map without consulting the cache.

        Returns (features, attempted, ok, notes).
          * `features` — parsed GeoJSON features (post-filter).
          * `attempted` — count of raw API entries the response contained
            (pre-filter); preserved separately so diagnostics distinguish
            "API returned 30 k, we kept 28 k" from "API returned 0".
          * `ok=True` — HTTP fetch succeeded AND response had expected shape,
            even if parsed feature count is zero (legitimate empty list).
          * `ok=False` — caller should consider serving cached data.
        """
        features: List[Dict] = []
        timeout = int(self._settings.get(
            "source_timeout_seconds",
            self.DEFAULT_SOURCE_TIMEOUT_SECONDS,
        )) if self._settings else self.DEFAULT_SOURCE_TIMEOUT_SECONDS
        try:
            req = urllib.request.Request(
                self._MESHCORE_MAP_URL,
                headers={"Accept": "application/json", "User-Agent": "MeshForge/1.0"},
            )
            # Bound the TOTAL read with a monotonic wall-clock deadline, not just
            # the per-socket `timeout`. urlopen(timeout=) fires only on a TOTAL
            # stall (no bytes for `timeout` s); a server trickling the ~12 MB body
            # steadily (each recv within `timeout`) reads UNBOUNDED — 2026-06-26 a
            # slow map.meshcore.dev took 533 s and held the federator cold collect
            # at HTTP 503 for ~9 min. Read in chunks, abort past the deadline
            # (monotonic = clock-step-immune), and let the caller serve stale cache
            # (this is a non-critical fallback source).
            max_total = int(self._settings.get(
                "meshcore_public_max_seconds",
                self.DEFAULT_MESHCORE_PUBLIC_MAX_SECONDS,
            )) if self._settings else self.DEFAULT_MESHCORE_PUBLIC_MAX_SECONDS
            deadline = time.monotonic() + max_total
            # Cap at 64 MB — current global MeshCore map is ~12 MB for ~30k nodes.
            # An 8 MB cap truncates mid-JSON and leaves an "Unterminated string"
            # parse error rather than complete data.
            CAP = 64 * 1024 * 1024
            CHUNK = 256 * 1024
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                buf: List[bytes] = []
                total = 0
                while total < CAP:
                    if time.monotonic() > deadline:
                        return [], 0, False, (
                            f"fetch exceeded {max_total}s deadline after "
                            f"{total // 1024} KB (slow/trickling source) — served cache"
                        )
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    buf.append(chunk)
                    total += len(chunk)
            data = json.loads(b"".join(buf).decode("utf-8", errors="replace"))

            if not isinstance(data, list):
                return [], 0, False, "unexpected response shape (not a list)"

            attempted = len(data)
            for node in data:
                feature = self._parse_meshcore_public_node(node)
                if feature:
                    features.append(feature)

            if features:
                logger.info(f"MeshCore public map: {len(features)}/{attempted} nodes")
            return features, attempted, True, self._MESHCORE_MAP_URL

        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug(f"MeshCore public map unavailable: {e}")
            return features, len(features), False, str(e)[:120]

    def _parse_meshcore_public_node(self, node: Dict) -> Optional[Dict]:
        """Parse one node from map.meshcore.dev into a GeoJSON feature."""
        lat = node.get("adv_lat")
        lon = node.get("adv_lon")
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        if lat == 0.0 and lon == 0.0:
            return None  # null-island filter

        pubkey = node.get("public_key") or ""
        if not pubkey:
            return None

        name = node.get("adv_name") or pubkey[:16]
        node_type_id = node.get("type", 0)
        node_type = self._MESHCORE_NODE_TYPES.get(node_type_id, "unknown")
        params = node.get("params") or {}
        # Upstream `last_advert` is ISO-8601 ('2026-04-27T19:45:54.000Z'),
        # not Unix epoch. Normalize here so downstream consumers (online
        # check, F7 directory upstream-stamp in node_history._build_directory_row)
        # see a numeric Unix timestamp. Numeric pass-through preserves
        # forward-compat if upstream ever switches representation.
        raw_last_advert = node.get("last_advert")
        last_ts: float = 0.0
        if isinstance(raw_last_advert, str):
            try:
                last_ts = datetime.fromisoformat(
                    raw_last_advert.replace("Z", "+00:00")
                ).timestamp()
            except (TypeError, ValueError):
                last_ts = 0.0
        elif isinstance(raw_last_advert, (int, float)):
            try:
                last_ts = float(raw_last_advert)
            except (TypeError, ValueError):
                last_ts = 0.0

        is_online = (
            self._is_node_online(last_ts, source="public_fallback")
            if last_ts > 0 else False
        )

        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": f"meshcore:{pubkey}",
                "name": name,
                "network": "meshcore",
                "node_type": node_type,
                "is_online": is_online,
                "is_local": False,
                "is_gateway": node_type_id == 2,  # repeater
                "last_heard": last_ts,
                "hardware": "",
                "role": node_type,
                "frequency": params.get("freq"),
                "spreading_factor": params.get("sf"),
                "coding_rate": params.get("cr"),
                "bandwidth": params.get("bw"),
                "source": "meshcore_public_map",
            },
        }
