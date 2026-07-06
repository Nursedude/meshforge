"""Visualization endpoint mixin for :class:`MapRequestHandler`.

Holds the two visualization overlays — D3.js network topology graph and
NOAA space weather — that map_http_handler.py serves under
``/api/network/topology`` and ``/api/weather``. Both are self-contained
read-only endpoints with no shared state with the rest of the handler
beyond ``self.collector``, ``self._serve_json``, and ``self._haversine``
(provided by ``RadioEndpointsMixin``).

Extracted from ``map_http_handler.py`` to keep that file under the
1,500-line size cap (``CLAUDE.md``). No behaviour change — methods are
mixed into ``MapRequestHandler`` via inheritance.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class VisualizationEndpointsMixin:
    """Visualization overlay endpoints for :class:`MapRequestHandler`.

    Provides:

    - :meth:`_serve_network_topology` — ``/api/network/topology``: D3.js
      node + link graph including AREDN RF/DTD/TUN link types and
      gateway↔gateway proximity links.
    - :meth:`_serve_weather` — ``/api/weather``: NOAA SWPC space-weather
      snapshot (Kp, SFI, X-ray, band conditions) with mesh-impact
      assessment, cached 15 min.
    """

    # Cache space weather data (refreshes every 15 minutes). Class-level state
    # shared across ThreadingHTTPServer request threads, so guard the
    # check-and-set with a lock: without it, N concurrent cold-cache requests
    # each fire an independent NOAA fetch (thundering herd) and an unsynchronized
    # reader can observe a half-populated cache dict.
    _weather_cache: Optional[Dict] = None
    _weather_cache_time: float = 0
    _weather_cache_lock = threading.Lock()
    _WEATHER_CACHE_TTL = 900  # 15 minutes

    def _serve_network_topology(self):
        """Serve network topology data for D3.js visualization.

        Wrapped in the short-TTL response cache (Issue #71 / GitHub
        #1168). The endpoint takes no query params, so a single
        ``None`` cache key covers every caller. The build closure does
        the full nodes/links/network_counts walk + ``json.dumps`` +
        ``gzip.compress`` — same wedge mechanics as the directory and
        geojson endpoints, just smaller body (~24 MB) and faster
        rebuild (~1.4 s).
        """
        if not self.collector:
            self._serve_json({"error": "collector not available", "nodes": [], "links": []})
            return

        cache = self.collector._topology_response_cache

        def _build() -> tuple:
            geojson = self.collector.collect()
            nodes: list = []
            links: list = []
            aredn_links_added = set()  # Track AREDN links to avoid duplicates

            # Build nodes
            for feature in geojson.get("features", []):
                props = feature["properties"]
                coords = feature["geometry"]["coordinates"]
                node_id = props.get("id", f"{coords[0]}_{coords[1]}")

                network = "gateway" if props.get("is_gateway") else props.get("network", "meshtastic")

                node = {
                    "id": node_id,
                    "name": props.get("name", node_id),
                    "network": network,
                    "is_online": props.get("is_online", False),
                    "is_gateway": props.get("is_gateway", False),
                    "is_router": props.get("role") in ("ROUTER", "ROUTER_CLIENT", "REPEATER", "AREDN"),
                    "lat": coords[1],
                    "lon": coords[0],
                    "snr": props.get("snr"),
                    "battery": props.get("battery"),
                    # AREDN-specific properties
                    "link_type": props.get("link_type"),  # RF, DTD, TUN
                    "link_quality": props.get("link_quality"),
                }
                nodes.append(node)

            # Build AREDN links from actual link data
            aredn_nodes = [n for n in nodes if n["network"] == "aredn"]
            if aredn_nodes:
                local_aredn = [n for n in aredn_nodes if not n.get("link_type")]
                neighbor_aredn = [n for n in aredn_nodes if n.get("link_type")]

                for local in local_aredn:
                    for neighbor in neighbor_aredn:
                        link_key = tuple(sorted([local["id"], neighbor["id"]]))
                        if link_key not in aredn_links_added:
                            dist = self._haversine(local["lat"], local["lon"],
                                                   neighbor["lat"], neighbor["lon"])
                            link_type_str = neighbor.get("link_type", "RF")
                            links.append({
                                "source": local["id"],
                                "target": neighbor["id"],
                                "type": f"aredn_{link_type_str.lower()}",
                                "link_quality": neighbor.get("link_quality", 0),
                                "snr": neighbor.get("snr"),
                                "distance_km": round(dist, 2)
                            })
                            aredn_links_added.add(link_key)

            # Build links based on proximity and network relationships for non-AREDN nodes
            gateways = [n for n in nodes if (n["is_gateway"] or n["is_router"]) and n["network"] != "aredn"]
            regular_nodes = [n for n in nodes if not n["is_gateway"] and not n["is_router"] and n["network"] != "aredn"]

            # Connect regular nodes to nearest gateway/router
            for node in regular_nodes:
                if not node["is_online"]:
                    continue

                nearest = None
                min_dist = float("inf")

                for gw in gateways:
                    if not gw["is_online"]:
                        continue
                    dist = self._haversine(node["lat"], node["lon"], gw["lat"], gw["lon"])
                    if dist < min_dist and dist < 50:  # 50km max
                        min_dist = dist
                        nearest = gw

                if nearest:
                    link_type = "gateway" if node["network"] != nearest["network"] else node["network"]
                    links.append({
                        "source": node["id"],
                        "target": nearest["id"],
                        "type": link_type,
                        "distance_km": round(min_dist, 2)
                    })

            # Connect gateways to each other
            for i, gw1 in enumerate(gateways):
                for gw2 in gateways[i+1:]:
                    if not gw1["is_online"] or not gw2["is_online"]:
                        continue
                    dist = self._haversine(gw1["lat"], gw1["lon"], gw2["lat"], gw2["lon"])
                    if dist < 100:  # 100km for gateway-gateway
                        links.append({
                            "source": gw1["id"],
                            "target": gw2["id"],
                            "type": "gateway",
                            "distance_km": round(dist, 2)
                        })

            body = {
                "nodes": nodes,
                "links": links,
                "network_counts": {
                    "meshtastic": len([n for n in nodes if n["network"] == "meshtastic"]),
                    "rns": len([n for n in nodes if n["network"] == "rns"]),
                    "aredn": len([n for n in nodes if n["network"] == "aredn"]),
                    "gateway": len([n for n in nodes if n["is_gateway"]])
                },
                "timestamp": datetime.now().isoformat()
            }
            raw = json.dumps(body).encode()
            gz = (
                gzip.compress(raw, compresslevel=6)
                if len(raw) >= self._GZIP_MIN_BYTES
                else None
            )
            return raw, gz

        try:
            raw_bytes, gzip_bytes, _was_built = cache.get_or_build(None, _build)
        except Exception as e:
            logger.error(f"topology build failed: {e}")
            self._serve_json(
                {"error": str(e)[:200], "nodes": [], "links": []},
                status=500,
            )
            return

        self._send_prebuilt_json(raw_bytes, gzip_bytes, status=200)

    def _serve_weather(self):
        """Serve space weather and HF band conditions for map overlay.

        Returns NOAA SWPC data: SFI, Kp, A-index, X-ray class,
        geomagnetic storm level, and per-band HF conditions.

        Cached for 15 minutes (space weather changes slowly).
        """
        now = time.time()

        # Fast path: serve a fresh cache without the lock (a single dict read is
        # atomic under CPython's GIL).
        cls = type(self)
        cached = cls._weather_cache
        if cached and (now - cls._weather_cache_time) < self._WEATHER_CACHE_TTL:
            self._serve_json(cached)
            return

        # Miss: serialize the refetch so a burst of concurrent misses collapses
        # to a single NOAA round-trip instead of a thundering herd.
        with cls._weather_cache_lock:
            now = time.time()
            cached = cls._weather_cache
            if cached and (now - cls._weather_cache_time) < self._WEATHER_CACHE_TTL:
                self._serve_json(cached)
                return
            self._fetch_and_serve_weather(cls, now)

    def _fetch_and_serve_weather(self, cls, now):
        """Fetch space weather from NOAA and serve it, refreshing the shared
        cache. Called under ``_weather_cache_lock`` (single-flight)."""
        try:
            from commands.propagation import get_space_weather, get_band_conditions

            weather_result = get_space_weather()
            band_result = get_band_conditions()

            if weather_result.success:
                data = weather_result.data or {}
                # Merge band conditions if available
                if band_result.success and band_result.data:
                    data["band_conditions"] = band_result.data.get(
                        "bands", data.get("band_conditions", {})
                    )
                    data["overall_condition"] = band_result.data.get("overall", "Unknown")

                # Add mesh-relevant assessment
                kp = data.get("k_index")
                sfi = data.get("solar_flux")
                if kp is not None and kp >= 5:
                    data["mesh_impact"] = "degraded"
                    data["mesh_impact_note"] = (
                        f"Kp={kp} — Geomagnetic storm may cause "
                        "increased noise on LoRa frequencies"
                    )
                elif sfi and sfi >= 200:
                    data["mesh_impact"] = "elevated"
                    data["mesh_impact_note"] = (
                        f"SFI={int(sfi)} — High solar activity, "
                        "monitor for interference"
                    )
                else:
                    data["mesh_impact"] = "nominal"
                    data["mesh_impact_note"] = "Conditions favorable for mesh operations"

                data["cached_at"] = now

                # Cache the result
                cls._weather_cache = data
                cls._weather_cache_time = now

                self._serve_json(data)
            else:
                self._serve_json({
                    "error": weather_result.error or "Space weather data unavailable",
                    "mesh_impact": "unknown",
                    "cached_at": now,
                })
        except Exception as e:
            logger.warning(f"Space weather fetch failed: {e}")
            self._serve_json({
                "error": str(e),
                "mesh_impact": "unknown",
                "cached_at": now,
            })
