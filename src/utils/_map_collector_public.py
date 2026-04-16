"""Public data source fallbacks for coverage maps.

Extracted to a mixin for file size compliance.

Provides fallback data from public mesh network maps when local/live sources
return sparse data. Each source is independently configurable via map_settings.json.

Sources:
- meshmap.net: Public Meshtastic node aggregation (JSON)
- RMAP.world: Public Reticulum node map (JSON, self-signed SSL)
- AREDN worldmap: Public AREDN node list (CSV)

Expects on the host class:
- self._is_valid_coordinate(lat, lon): coordinate validator
- self._make_feature(...): GeoJSON feature builder
- self._is_node_online(last_heard, source): online status check
- self._settings: SettingsManager instance (for enable flags)
"""

import csv
import io
import json
import logging
import ssl
import time
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MESHMAP_URL = "https://meshmap.net/nodes.json"
RMAP_WORLD_URL = "https://rmap.world/?json=1"
AREDN_WORLDMAP_URL = "https://worldmap.arednmesh.org/data/out.csv"

RNS_NODE_TYPES = {
    "rnode": "RNode (LoRa)",
    "nomadnet": "NomadNet",
    "rnsd": "RNSD",
    "tcp": "TCP Transport",
    "i2p": "I2P",
    "tnc": "TNC KiSS",
    "retibbs": "RetiBBS",
    "lxmf_group": "LXMF Group",
    "lxmf_peer": "LXMF Peer",
    "multi": "Multi-Interface",
    "yggdrasil": "Yggdrasil",
}


class PublicDataFallbackMixin:
    """Mixin providing public data source fallbacks for MapDataCollector."""

    def _collect_public_fallbacks(self, current_feature_count: int = 0) -> List[Dict]:
        """Collect nodes from public data sources when local data is sparse.

        Only fetches if enabled AND local feature count is below threshold.

        Args:
            current_feature_count: Number of features already collected from
                local/live sources.

        Returns:
            List of GeoJSON features from public sources.
        """
        threshold = 3
        if self._settings:
            threshold = int(self._settings.get("public_fallback_threshold", 3))

        if current_feature_count >= threshold:
            logger.debug(
                "Public fallback skipped: %d local nodes >= threshold %d",
                current_feature_count, threshold,
            )
            return []

        features = []

        if self._settings and self._settings.get("enable_meshmap_fallback", False):
            features.extend(self._fetch_meshmap_nodes())

        if self._settings and self._settings.get("enable_rmap_fallback", False):
            features.extend(self._fetch_rmap_nodes())

        if self._settings and self._settings.get("enable_aredn_worldmap_fallback", False):
            features.extend(self._fetch_aredn_worldmap_nodes())

        if features:
            logger.debug("Public fallbacks returned %d total nodes", len(features))
        return features

    # -- meshmap.net (Meshtastic) ------------------------------------------

    def _fetch_meshmap_nodes(self) -> List[Dict]:
        """Fetch Meshtastic nodes from meshmap.net public API."""
        features = []
        try:
            req = Request(
                MESHMAP_URL,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "MeshForge/1.0",
                },
            )
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            for num_id, node in data.items():
                feature = self._parse_meshmap_node(num_id, node)
                if feature:
                    features.append(feature)
            if features:
                logger.debug("meshmap.net returned %d meshtastic nodes", len(features))
        except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug("meshmap.net unavailable: %s", e)
        return features

    def _parse_meshmap_node(
        self, num_id: str, node: Dict[str, Any]
    ) -> Optional[Dict]:
        """Parse a node from meshmap.net nodes.json format.

        Coordinate precedence:
          1. Explicit Meshtastic integer fields ``latitudeI`` / ``longitudeI``
             (most reliable — matches the Meshtastic protobuf encoding).
          2. Float ``latitude`` / ``longitude`` in normal decimal degrees.
          3. Legacy case: ``latitude`` / ``longitude`` stored as the same
             integer encoding without the ``I`` suffix. We detect this by
             out-of-range magnitude (>900) and scale by 1e7.
        """
        lat = lon = None
        lat_i = node.get("latitudeI")
        lon_i = node.get("longitudeI")
        if lat_i is not None and lon_i is not None:
            try:
                lat = float(lat_i) / 1e7
                lon = float(lon_i) / 1e7
            except (TypeError, ValueError):
                logger.debug("meshmap node %s: bad latitudeI/longitudeI", num_id)
                return None
        else:
            raw_lat = node.get("latitude")
            raw_lon = node.get("longitude")
            if raw_lat is None or raw_lon is None:
                return None
            try:
                lat = float(raw_lat)
                lon = float(raw_lon)
            except (TypeError, ValueError):
                logger.debug("meshmap node %s: bad latitude/longitude", num_id)
                return None
            # Legacy integer encoding without I suffix — only scale when
            # values are clearly out of normal [-90,90]/[-180,180] range.
            if abs(lat) > 900 or abs(lon) > 900:
                lat /= 1e7
                lon /= 1e7

        if not self._is_valid_coordinate(lat, lon):
            logger.debug("meshmap node %s: coords out of range", num_id)
            return None

        try:
            hex_id = f"!{int(num_id):08x}"
        except (ValueError, TypeError):
            return None

        last_heard = node.get("lastMapReport")
        if isinstance(last_heard, str):
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(last_heard.replace("Z", "+00:00"))
                last_heard = dt.timestamp()
            except (ValueError, TypeError):
                last_heard = 0
        elif not isinstance(last_heard, (int, float)):
            last_heard = 0

        is_online = self._is_node_online(last_heard, source="public_fallback")

        feature = self._make_feature(
            node_id=hex_id,
            name=node.get("longName", node.get("shortName", hex_id)),
            lat=lat,
            lon=lon,
            network="meshtastic",
            is_online=is_online,
            hardware=node.get("hwModel", ""),
            role=node.get("role", ""),
            battery=node.get("batteryLevel"),
            last_heard=last_heard,
            channel_utilization=node.get("chUtil"),
            air_util_tx=node.get("airUtilTx"),
        )
        feature["properties"]["source"] = "meshmap_net"
        return feature

    # -- RMAP.world (Reticulum) --------------------------------------------

    def _fetch_rmap_nodes(self) -> List[Dict]:
        """Fetch Reticulum nodes from RMAP.world public API.

        TLS verification is enabled by default. Operators who need to opt
        into an insecure connection (e.g., development against a self-signed
        endpoint on a trusted LAN) must set ``rmap_insecure_tls`` to True in
        map_settings.json AND acknowledge the MITM risk — the data drives
        node positions/names rendered in the map UI.
        """
        features = []
        insecure = False
        if self._settings:
            insecure = bool(self._settings.get("rmap_insecure_tls", False))

        try:
            req = Request(
                RMAP_WORLD_URL,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "MeshForge/1.0",
                },
            )
            ctx = ssl.create_default_context()
            if insecure:
                logger.warning(
                    "RMAP.world TLS verification disabled via rmap_insecure_tls "
                    "— map data is vulnerable to MITM injection."
                )
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with urlopen(req, timeout=15, context=ctx) as resp:
                data = json.loads(resp.read().decode())

            nodes = data.get("nodes", []) if isinstance(data, dict) else []
            for node in nodes:
                feature = self._parse_rmap_node(node)
                if feature:
                    features.append(feature)
            if features:
                logger.debug("RMAP.world returned %d rns nodes", len(features))
        except (ssl.SSLError, URLError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug("RMAP.world unavailable: %s", e)
        return features

    def _parse_rmap_node(self, node: Dict[str, Any]) -> Optional[Dict]:
        """Parse a node from RMAP.world API response."""
        lat = node.get("lat")
        lon = node.get("lon")
        if not self._is_valid_coordinate(lat, lon):
            return None

        node_id = node.get("hash") or node.get("identity_hash") or ""
        if not node_id:
            return None
        node_id = f"rns_{str(node_id)[:16]}"
        name = node.get("display_name") or str(node_id)[:16]

        last_seen = node.get("last_seen_ts")
        if isinstance(last_seen, (int, float)):
            is_online = self._is_node_online(last_seen, source="public_fallback")
            last_heard = last_seen
        else:
            is_online = False
            last_heard = 0

        feature = self._make_feature(
            node_id=node_id,
            name=name,
            lat=float(lat),
            lon=float(lon),
            network="rns",
            is_online=is_online,
            last_heard=last_heard,
            hardware=RNS_NODE_TYPES.get(
                (node.get("node_type") or "unknown").lower(), "RNS Node"
            ),
        )
        feature["properties"]["source"] = "rmap_world"
        return feature

    # -- AREDN worldmap (CSV) ----------------------------------------------

    def _fetch_aredn_worldmap_nodes(self) -> List[Dict]:
        """Fetch AREDN nodes from the public AREDN worldmap CSV."""
        features = []
        try:
            req = Request(
                AREDN_WORLDMAP_URL,
                headers={
                    "Accept": "text/csv",
                    "User-Agent": "MeshForge/1.0",
                },
            )
            with urlopen(req, timeout=20) as resp:
                text = resp.read().decode("utf-8", errors="replace")

            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                feature = self._parse_worldmap_row(row)
                if feature:
                    features.append(feature)
            if features:
                logger.debug("AREDN worldmap returned %d nodes", len(features))
        except (URLError, OSError, ValueError) as e:
            logger.debug("AREDN worldmap unavailable: %s", e)
        return features

    def _parse_worldmap_row(self, row: Dict[str, str]) -> Optional[Dict]:
        """Parse a row from the AREDN worldmap CSV."""
        node_name = (row.get("node") or "").strip()
        if not node_name:
            return None

        lat = row.get("lat")
        lon = row.get("lon")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        if not self._is_valid_coordinate(lat, lon):
            return None

        last_seen = row.get("last_seen") or None
        if last_seen:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                last_heard = dt.timestamp()
                is_online = self._is_node_online(last_heard, source="public_fallback")
            except (ValueError, TypeError):
                last_heard = 0
                is_online = False
        else:
            last_heard = 0
            is_online = False

        node_id = f"aredn_{node_name}"
        feature = self._make_feature(
            node_id=node_id,
            name=node_name,
            lat=lat,
            lon=lon,
            network="aredn",
            is_online=is_online,
            hardware=row.get("model", ""),
            last_heard=last_heard,
        )
        feature["properties"]["source"] = "aredn_worldmap"
        return feature
