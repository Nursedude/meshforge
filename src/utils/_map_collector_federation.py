"""Fleet federation for the map data collector.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).
Pure code motion — no behavior change.

Expects the following on the host class (MapDataCollector):
- self._settings, self._cache_dir, self._federation
- self._is_valid_coordinate(lat, lon): coordinate validator
"""

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _hub():
    """Return the import hub module (utils.map_data_collector).

    Lazy on purpose: the moved methods must resolve module globals
    (``get_real_user_home``, ``_origin_priority``) through the hub at call
    time so existing test patch targets on ``utils.map_data_collector.*``
    keep applying to this extracted code. The import is deferred because
    the hub imports this module at top level (circular otherwise).
    """
    from utils import map_data_collector
    return map_data_collector


class FederationDataCollectorMixin:
    """Federation bootstrap/lifecycle + peer-directory merge for MapDataCollector."""

    def _bootstrap_federation_peers(self) -> List[str]:
        """Read fleet.json (if present) and derive a peer list with self filtered out.

        Returns [] if fleet.json doesn't exist or doesn't parse — federation
        is opt-in and a missing fleet config is the normal case for
        single-box installs.
        """
        try:
            from utils.map_federation import filter_self_from_peers
            fleet_path = _hub().get_real_user_home() / ".config" / "meshforge" / "fleet.json"
            if not fleet_path.exists():
                return []
            with open(fleet_path, "r") as f:
                cfg = json.load(f)
            peers_dict = cfg.get("peers") or {}
            this_host = cfg.get("this_host")
            this_host_lower = str(this_host).lower() if this_host else None

            # Two-pass self-elimination:
            #   1) Skip peer entries whose CONFIG NAME matches this_host —
            #      handles IP-vs-hostname mismatches (the peer's stored
            #      identifier might be its IP, but the name in the dict is
            #      the canonical hostname).
            #   2) Run the runtime hostname filter as a backup, in case
            #      this_host wasn't configured but the host's actual
            #      hostname matches one of the peer ids.
            peer_ids: List[str] = []
            for name, info in peers_dict.items():
                if this_host_lower and name.lower() == this_host_lower:
                    continue
                if isinstance(info, dict) and info.get("ip"):
                    peer_ids.append(info["ip"])
                else:
                    peer_ids.append(name)

            local_names = None
            if this_host:
                from utils.map_federation import get_local_hostnames
                local_names = list(set(get_local_hostnames() + [str(this_host).lower()]))
            return filter_self_from_peers(peer_ids, local_names=local_names)
        except (OSError, json.JSONDecodeError, KeyError, ImportError) as e:
            logger.debug(f"Federation bootstrap from fleet.json failed: {e}")
            return []

    def _load_fleet_peer_names(self) -> Dict[str, str]:
        """Build endpoint→friendly-name mapping from fleet.json.

        Best-effort companion to `_bootstrap_federation_peers`. Returns
        `{ip: name}` (and `{name: name}` when the entry has no IP) so the
        federation collector can stamp `peer_name` on each status without
        re-reading the settings every cycle. Empty dict when fleet.json is
        absent or malformed — federation still works, the `peer_name` field
        just stays None.
        """
        try:
            fleet_path = _hub().get_real_user_home() / ".config" / "meshforge" / "fleet.json"
            if not fleet_path.exists():
                return {}
            with open(fleet_path, "r") as f:
                cfg = json.load(f)
            peers_dict = cfg.get("peers") or {}
            out: Dict[str, str] = {}
            for name, info in peers_dict.items():
                if isinstance(info, dict) and info.get("ip"):
                    out[info["ip"]] = name
                else:
                    out[name] = name
            return out
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Federation peer-name load from fleet.json failed: {e}")
            return {}

    def _init_federation(self) -> None:
        """Construct the FederationCollector instance (does not start it).

        start_federation() is called by map_data_service after the warmup
        collect runs, so we don't compete with the cold-start collect for
        I/O budget on Pi-class hardware.
        """
        peers = self._settings.get("federation_peers") or []
        if not peers:
            return
        try:
            from utils.map_federation import FederationCollector
            # Pass node_history db_path so federation can backpressure-skip
            # polls when the WAL is oversize (project_db_recurring_class).
            # NB: _init_federation runs BEFORE self._history is set in
            # __init__, so derive the path the same way the history block
            # does (~line 215) rather than reading self._history.db_path.
            db_path = self._cache_dir / "node_history.db"
            self._federation = FederationCollector(
                peers=peers,
                poll_interval=int(self._settings.get(
                    "federation_poll_interval_seconds", 60
                )),
                # Fallback matches the SettingsManager default (30) — the
                # pre-fix `, 5` here disagreed with `, 30` above, which is
                # exactly the dual-default hazard the Issue #62 fix
                # eliminates. Keep this aligned with the defaults block.
                timeout=float(self._settings.get(
                    "federation_timeout_seconds", 30
                )),
                port=int(self._settings.get("federation_port", 5000)),
                db_path=db_path,
                peer_names=self._load_fleet_peer_names(),
            )
        except ImportError as e:
            logger.warning(f"Federation disabled (import failed): {e}")
            self._federation = None

    def start_federation(self) -> None:
        """Start the federation poll thread. Idempotent. Called by map service."""
        if self._federation:
            self._federation.start()

    def stop_federation(self) -> None:
        """Stop the federation poll thread. Called by map service shutdown."""
        if self._federation:
            self._federation.stop()

    def _merge_federation(self,
                          features: Dict[str, Dict],
                          position_less: List[Dict]) -> Dict[str, Any]:
        """Fold federated peer entries into local features + position_less.

        Local always wins on (network, node_id) collisions — federated
        entries from peers only fill gaps the local box doesn't already
        know about. Federated features carry `federated: True` and
        `federated_from: <peer>` so the frontend can style/filter them.

        Returns a federation summary block for geojson properties:
          - enabled (bool)
          - peers (list of configured peer hostnames)
          - peer_status (list of dicts: hostname, ok, last_sync, ...)
          - last_sync (most recent successful peer sync ts)
          - last_attempt (most recent attempt ts)
          - by_network (dict: network -> count of federated-only entries)
          - total / with_position / without_position (federated-only counts)

        Disabled (no federation collector configured) returns
        `{"enabled": False, ...}` with empty fields so the frontend can
        unconditionally read the block.
        """
        empty_block = {
            "enabled": False, "peers": [], "peer_status": [],
            "last_sync": None, "last_attempt": None, "by_network": {},
            "total": 0, "with_position": 0, "without_position": 0,
        }
        if not self._federation:
            return empty_block

        try:
            snap = self._federation.get_snapshot()
        except Exception as e:
            logger.debug(f"federation snapshot failed: {e}")
            return empty_block

        # Index local entries by (network, id) with source_origin priority,
        # so a federated peer carrying a HIGHER-trust origin can override a
        # lower-trust local entry. Without this, a node first heard by the
        # local meshcore_public collector (priority 30) blocks a federated
        # peer's local_radio observation (priority 100) for the same hash —
        # which defeats the purpose of cross-box federation when a sister
        # box is the only one with the radio. "Local always wins" stays
        # intact at equal priority (the original guarantee against peer
        # noise); a higher-priority federated entry strictly upgrades.
        # Index entry: (priority, feature_key | None, position_less_index | None).
        local_index: Dict[tuple, tuple] = {}
        for fkey, f in features.items():
            props = f.get("properties") or {}
            net = props.get("network")
            nid = props.get("id")
            if net and nid:
                origin = props.get("source_origin")
                local_index[(net, nid)] = (_hub()._origin_priority(origin), fkey, None)
        for idx, entry in enumerate(position_less):
            net = entry.get("network")
            nid = entry.get("id")
            if net and nid and (net, nid) not in local_index:
                origin = entry.get("source_origin")
                local_index[(net, nid)] = (_hub()._origin_priority(origin), None, idx)

        by_network: Dict[str, int] = {}
        with_pos = 0
        without_pos = 0

        # Track position_less indices to delete after the loop (deleting in-flight
        # would shift indices and break the second `enumerate` pass above).
        position_less_drop: set = set()

        for (net, nid), entry in snap.by_node.items():
            local = local_index.get((net, nid))
            if local is not None:
                local_priority, local_fkey, local_pl_idx = local
                fed_priority = _hub()._origin_priority(entry.get("source_origin"))
                if fed_priority <= local_priority:
                    continue  # local-wins — skip federated copy (original behavior)
                # Federated peer carries a higher-trust origin: drop the
                # local stub so the federated entry can take its place.
                if local_fkey is not None:
                    features.pop(local_fkey, None)
                elif local_pl_idx is not None:
                    position_less_drop.add(local_pl_idx)
            by_network[net] = by_network.get(net, 0) + 1

            lat = entry.get("lat")
            lon = entry.get("lon")
            seen_by = entry.get("seen_by_peers") or [entry.get("federated_from")]
            if self._is_valid_coordinate(lat, lon):
                # Federated-only feature: render as map marker. Use a
                # namespaced dict key to avoid clashing with local id-keys.
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [
                            lon, lat,
                            entry.get("altitude") if entry.get("altitude") is not None else 0,
                        ],
                    },
                    "properties": {
                        "id": entry["id"],
                        "name": entry.get("name") or entry["id"],
                        "network": entry["network"],
                        "role": entry.get("role", ""),
                        "hardware": entry.get("hardware", ""),
                        "last_seen": entry.get("last_seen"),
                        "source": "federation",
                        "source_origin": entry.get("source_origin") or "",
                        "federated": True,
                        "federated_from": entry.get("federated_from"),
                        "seen_by_peers": seen_by,
                        # Don't claim freshness — federation only knows
                        # what the peer claimed; let UI mark "stale" by
                        # rendering with reduced opacity.
                        "is_online": False,
                        "is_local": False,
                        "is_gateway": False,
                    },
                }
                features[f"federated:{net}:{nid}"] = feature
                with_pos += 1
            else:
                position_less.append({
                    "id": entry["id"],
                    "name": entry.get("name") or entry["id"],
                    "network": entry["network"],
                    "role": entry.get("role", ""),
                    "hardware": entry.get("hardware", ""),
                    "last_seen": entry.get("last_seen"),
                    "source_origin": entry.get("source_origin") or "",
                    "federated": True,
                    "federated_from": entry.get("federated_from"),
                    "seen_by_peers": seen_by,
                })
                without_pos += 1

        # Drop position_less stubs that got overridden by a higher-priority
        # federated entry (collected during the loop to avoid in-flight
        # index shifts).
        if position_less_drop:
            position_less[:] = [
                p for i, p in enumerate(position_less) if i not in position_less_drop
            ]

        peer_status_list = []
        for s in snap.peer_status.values():
            peer_status_list.append({
                "hostname": s.hostname,
                "peer_name": s.peer_name,
                "ok": s.ok,
                "last_sync": s.last_sync,
                "last_attempt": s.last_attempt,
                "last_error": s.last_error,
                "last_count": s.last_count,
                "last_latency_ms": s.last_latency_ms,
                "consecutive_failures": s.consecutive_failures,
                # Backoff state (Issue #59/#65). Present on FederationPeerStatus
                # but never plumbed to /api/status until the 2026-07-05 QA audit
                # — so the mini-dudeai FederationPeerSource (reads in_backoff /
                # backoff_multiplier) and the operator couldn't tell "actively
                # failing" from "in backoff, not polled for the next hour".
                "in_backoff": s.in_backoff,
                "backoff_multiplier": s.backoff_multiplier,
                "next_eligible_poll_ts": s.next_eligible_poll_ts,
            })

        return {
            "enabled": True,
            "peers": list(self._federation.peers),
            "peer_status": peer_status_list,
            "last_sync": snap.last_sync,
            "last_attempt": snap.last_attempt,
            "by_network": by_network,
            "total": with_pos + without_pos,
            "with_position": with_pos,
            "without_position": without_pos,
        }
