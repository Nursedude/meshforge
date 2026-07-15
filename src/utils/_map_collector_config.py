"""Configuration accessors + node-validity predicates for the map data collector.

Extracted from map_data_collector.py 2026-07-14 for MF025 size compliance.
Pure code motion — no behavior change. These methods only touch
``self._settings``, the ``self.DEFAULT_*`` class attributes (defined on
MapDataCollector), and stdlib; they carry NO hub-global dependencies, so —
unlike the source-collector mixins — no ``_hub()`` indirection is needed.

Expects on the host class (MapDataCollector): ``self._settings`` (a
SettingsManager or None) and the ``DEFAULT_*`` threshold/host/port class
attributes.
"""
import logging
import math
import time

logger = logging.getLogger(__name__)

# A last_heard more than this many seconds in the FUTURE is implausible (upstream
# clock skew / a hostile injected stamp) and must not read as "online". Matches
# the ±300s tolerance used by _map_collector_meshtastic and cloud_map_freshness.
_ONLINE_FUTURE_SKEW_TOLERANCE_S = 300


class MapCollectorConfigMixin:
    """Settings/threshold accessors + coordinate/timestamp validity predicates
    for MapDataCollector (see module docstring for the host-class contract)."""

    @staticmethod
    def _is_valid_coordinate(lat, lon) -> bool:
        """Validate geographic coordinates.

        Rejects:
        - None values
        - NaN or Infinity
        - Out-of-range (lat must be -90..90, lon must be -180..180)
        - Default zero (both lat AND lon are exactly 0 — unset GPS)

        Accepts:
        - Nodes near the equator/prime meridian where only ONE coord is near zero
        - Any valid coordinate pair within range
        """
        if lat is None or lon is None:
            return False
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        if lat < -90 or lat > 90 or lon < -180 or lon > 180:
            return False
        # Reject default-zero GPS (both exactly 0.0 = unset), but allow
        # nodes where only one axis is near zero (legitimate equator/meridian)
        if lat == 0.0 and lon == 0.0:
            return False
        return True

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

    def get_source_threshold_seconds(self, source: str) -> int:
        """Get online threshold for a specific network source.

        Per-source thresholds allow different timeout windows per network type:
        - meshtastic: 15 min (frequent heartbeats)
        - mqtt: 15 min (real-time broker)
        - rns: 30 min (announces less frequently)
        - aredn: 60 min (scans are infrequent)

        Falls back to the global online_status_threshold_minutes setting.

        Args:
            source: Network source type ("meshtastic", "mqtt", "rns", "aredn")

        Returns:
            Threshold in seconds
        """
        key = f"{source}_threshold_minutes"
        defaults = {
            "meshtastic": self.DEFAULT_MESHTASTIC_THRESHOLD_MINUTES,
            "mqtt": self.DEFAULT_MQTT_THRESHOLD_MINUTES,
            "rns": self.DEFAULT_RNS_THRESHOLD_MINUTES,
            "aredn": self.DEFAULT_AREDN_THRESHOLD_MINUTES,
            "public_fallback": self.DEFAULT_PUBLIC_FALLBACK_THRESHOLD_MINUTES,
        }
        default = defaults.get(source, self.DEFAULT_ONLINE_THRESHOLD_MINUTES)
        if self._settings:
            minutes = self._settings.get(key, default)
        else:
            minutes = default
        return int(minutes * 60)

    @staticmethod
    def _coerce_epoch(v) -> float:
        """Best-effort convert a timestamp to a Unix-epoch float.

        Accepts int/float, a numeric string, or an ISO-8601 string; returns 0.0
        (== "unknown") for anything unparseable, and NEVER raises. A node cache
        can carry `last_seen` as an ISO string (MeshNode.to_dict); passing that
        straight into a `<= 0` comparison raises TypeError, which a collector's
        `except` turns into a DROPPED node — worse than rendering it offline.
        """
        if v is None:
            return 0.0
        if isinstance(v, bool):
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return 0.0
            try:
                return float(s)
            except ValueError:
                pass
            try:
                from datetime import datetime
                return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0
        return 0.0

    def _is_node_online(self, last_heard, source: str = "meshtastic") -> bool:
        """Determine if a node is online based on last_heard timestamp.

        Single source of truth for online status determination.
        Uses per-source thresholds for accurate status across network types.

        Args:
            last_heard: Unix timestamp of last communication (0/None/unparseable
                = unknown). Coerced defensively — a non-numeric value must never
                raise out of the SSOT (honest_failure_modes).
            source: Network source type for threshold lookup

        Returns:
            True if the node was heard within the source's threshold window
        """
        last_heard = self._coerce_epoch(last_heard)
        if not last_heard or last_heard <= 0:
            return False
        age = time.time() - last_heard
        # A forgeable/hostile FUTURE timestamp (upstream clock skew or an
        # injected "last seen 2099") makes age negative → online forever. Reject
        # anything meaningfully in the future; a small negative (benign skew)
        # still counts as fresh. Defends meshcore/public/CLI callers at the SSOT.
        if age < -_ONLINE_FUTURE_SKEW_TOLERANCE_S:
            return False
        threshold = self.get_source_threshold_seconds(source)
        return age < threshold

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

    def get_meshtasticd_tcp_collect_timeout_seconds(self) -> float:
        """Wall-clock cap for the meshtasticd TCP collect (connect + nodedb sync).

        Bounds how long a wedged daemon can hold ``_collect_lock``. See
        ``DEFAULT_MESHTASTICD_TCP_COLLECT_TIMEOUT_SECONDS`` for the why.
        """
        if self._settings:
            return float(self._settings.get(
                "meshtasticd_tcp_collect_timeout_seconds",
                self.DEFAULT_MESHTASTICD_TCP_COLLECT_TIMEOUT_SECONDS,
            ))
        return float(self.DEFAULT_MESHTASTICD_TCP_COLLECT_TIMEOUT_SECONDS)

    def set_meshtasticd_connection(self, host: str, port: int) -> bool:
        """Set meshtasticd connection parameters.

        Args:
            host: Hostname or IP address of meshtasticd
            port: TCP port (default: 4403)

        Returns:
            True iff persisted to disk; False if there is no settings backend
            (a silent no-op) or the save failed — so the caller can't report
            "Connection set" for a change that never happened (#74 class).
        """
        if not self._settings:
            logger.warning(
                "set_meshtasticd_connection(%s:%s): no settings backend; not persisted",
                host, port,
            )
            return False
        self._settings.set("meshtasticd_host", host)
        self._settings.set("meshtasticd_port", port)
        ok = self._settings.save()
        if ok:
            logger.info(f"Meshtasticd connection set to {host}:{port}")
        return ok

