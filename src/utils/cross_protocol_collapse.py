"""
Cross-protocol collapse for /api/nodes/geojson responses.

The `nodes` table stores one row per (network, node_id) because that
tuple uniquely identifies a node within a protocol — Meshtastic, RNS,
MeshCore, AREDN each define their own ID space, and a radio that
participates in multiple stacks legitimately exists in all of them.
For the on-screen map though, the same operator showing up as two
pins (e.g., "WH6GXZ-Home" on Meshtastic AND RNS) is visual noise.

This module collapses cross-protocol duplicates at API response time.
The row model in node_history.db stays untouched — that's what
federation peers consume, and they need raw (network, node_id) rows
to merge cleanly. Collapse only runs for /api/nodes/geojson (the
operator-facing surface and the static :8808 cloud mirror).

Identity heuristic — explicit, narrow, two tiers:

    Tier 1: same normalized name (.strip().casefold()) AND both
            features have positions AND Haversine distance ≤ 50 m.
    Tier 2: same normalized name AND exactly one feature has a
            position (RNS announces don't carry GPS by protocol
            design — they collapse onto the same operator's
            Meshtastic pin).
    No match: leave separate.

3+ way collapse falls out of the bucket-by-name pass naturally —
a name with M features can produce up to M-1 merges in one cycle.

Merged feature shape:
    properties.collapsed = True
    properties.networks = sorted unique list of network strings
    properties.protocol_meta = {network: <orig protocol_meta>, ...}
    properties.alt_ids = [list of non-primary (network, id) tuples]
    properties.last_seen = max across merged
    geometry = primary's geometry (highest source_origin priority)
    properties.id, properties.name = primary's

Primary selection: highest source_origin priority via
node_history._origin_priority (local_radio > rns_path_table >
aredn_local > mqtt_local > node_tracker > meshcore_public ≈
aredn_worldmap ≈ mqtt_global > public_fallback).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from utils.node_history import _origin_priority


# Tier-1 distance threshold. 50 m is generous enough to absorb GPS
# noise (typical consumer GPS ±5 m, MeshCore advert position rounding
# can be coarser) while still keeping co-sited-but-distinct repeaters
# separate.
_TIER_1_DISTANCE_M = 50.0

_EARTH_RADIUS_M = 6_371_000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_M * c


def _feature_position(f: Dict[str, Any]) -> Tuple[float, float] | None:
    geom = f.get("geometry") or {}
    if not isinstance(geom, dict):
        return None
    coords = geom.get("coordinates")
    if not (isinstance(coords, (list, tuple)) and len(coords) >= 2):
        return None
    try:
        lon = float(coords[0])
        lat = float(coords[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    return (lat, lon)


def _normalized_name(f: Dict[str, Any]) -> str:
    name = (f.get("properties") or {}).get("name")
    if not isinstance(name, str):
        return ""
    return name.strip().casefold()


def _origin_rank(f: Dict[str, Any]) -> int:
    origin = (f.get("properties") or {}).get("source_origin")
    return _origin_priority(origin)


def _merge_pair(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a new feature carrying both networks' identities.

    Caller has already chosen which side is `primary` (higher
    source_origin priority wins; tie → keep `primary` as given).
    Does not mutate inputs; returns a new dict.
    """
    p_props = dict(primary.get("properties") or {})
    s_props = secondary.get("properties") or {}

    # networks list — union, sorted for determinism.
    p_networks = p_props.get("networks")
    if isinstance(p_networks, list):
        networks_set = set(p_networks)
    else:
        net = p_props.get("network")
        networks_set = {net} if net else set()
    s_net = s_props.get("network")
    if s_net:
        networks_set.add(s_net)
    p_props["networks"] = sorted(n for n in networks_set if n)

    # protocol_meta — promote to {network: blob} on first merge. The
    # "is this already promoted?" check piggy-backs on the collapsed
    # flag rather than guessing from key names (a node's raw
    # protocol_meta could itself be a dict with string keys like
    # "hops" — those aren't network names, but we can't tell without
    # the flag).
    already_promoted = bool(p_props.get("collapsed"))
    p_pm = p_props.get("protocol_meta")
    if already_promoted and isinstance(p_pm, dict):
        pm_dict = dict(p_pm)
    else:
        pm_dict = {}
        if p_pm:
            primary_net = p_props.get("network")
            if primary_net:
                pm_dict[primary_net] = p_pm
    s_pm = s_props.get("protocol_meta")
    if s_pm and s_net:
        pm_dict[s_net] = s_pm
    if pm_dict:
        p_props["protocol_meta"] = pm_dict

    # alt_ids — list of (network, id) tuples for non-primary feature(s).
    alt_ids = list(p_props.get("alt_ids") or [])
    s_id = s_props.get("id")
    if s_net and s_id:
        alt_ids.append([s_net, s_id])
    if alt_ids:
        p_props["alt_ids"] = alt_ids

    # last_seen: MAX across both, treating None as -inf.
    p_ls = p_props.get("last_seen")
    s_ls = s_props.get("last_seen")
    candidates = [v for v in (p_ls, s_ls) if isinstance(v, (int, float))]
    if candidates:
        p_props["last_seen"] = max(candidates)

    p_props["collapsed"] = True

    return {
        "type": "Feature",
        "geometry": primary.get("geometry"),
        "properties": p_props,
    }


def collapse_cross_protocol(
    features: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Collapse same-name features across protocols.

    Returns ``(collapsed_features, collapsed_pair_count)``. The pair
    count surfaces in the geojson response's properties so operators
    can see how aggressive the heuristic is being for their fleet.

    Stable ordering: features with no name (or unique names) come out
    in their input order. Within a bucket, the highest-priority
    origin wins; ties keep the earlier feature as primary.
    """
    if not features:
        return [], 0

    # Bucket indices by normalized name. Empty names never collapse.
    buckets: Dict[str, List[int]] = {}
    for idx, f in enumerate(features):
        name = _normalized_name(f)
        if not name:
            continue
        buckets.setdefault(name, []).append(idx)

    # Decide per index whether it merges into another and into which.
    # primary_for[i] = j means feature i collapses into feature j;
    # j is the eventual emitter for that group.
    primary_for: Dict[int, int] = {}
    pair_count = 0

    for name, idxs in buckets.items():
        if len(idxs) < 2:
            continue
        # Sort indices by (origin_rank desc, original index asc) so
        # the highest-priority origin is the primary; ties go to
        # the earliest feature.
        sorted_idxs = sorted(
            idxs,
            key=lambda i: (-_origin_rank(features[i]), i),
        )
        primary_idx = sorted_idxs[0]
        primary_pos = _feature_position(features[primary_idx])
        for other_idx in sorted_idxs[1:]:
            other_pos = _feature_position(features[other_idx])
            # Tier 1: both positioned AND within 50 m.
            if primary_pos is not None and other_pos is not None:
                d = _haversine_m(*primary_pos, *other_pos)
                if d <= _TIER_1_DISTANCE_M:
                    primary_for[other_idx] = primary_idx
                    pair_count += 1
                    continue
                # Both have positions but they're far apart — leave separate
                # (different physical sites that happen to share a name).
                continue
            # Tier 2: one has a position, the other doesn't.
            if (primary_pos is None) != (other_pos is None):
                primary_for[other_idx] = primary_idx
                pair_count += 1
                continue
            # Both position-less → no spatial check possible. Collapse
            # by name still — RNS announces routinely lack GPS and
            # benefit from this rule.
            if primary_pos is None and other_pos is None:
                primary_for[other_idx] = primary_idx
                pair_count += 1
                continue

    # Build outputs preserving input order. For each primary that owns
    # collapsed members, merge them in left-to-right order.
    members_of: Dict[int, List[int]] = {}
    for member_idx, primary_idx in primary_for.items():
        members_of.setdefault(primary_idx, []).append(member_idx)

    out: List[Dict[str, Any]] = []
    consumed: set = set(primary_for.keys())
    for i, f in enumerate(features):
        if i in consumed:
            continue
        if i in members_of:
            merged = f
            for member_idx in sorted(members_of[i]):
                merged = _merge_pair(merged, features[member_idx])
            # If the primary feature carried no name at this point but
            # was identified by bucket-by-name, retain its original
            # name in properties — _merge_pair preserves it via
            # dict(primary["properties"]).
            out.append(merged)
        else:
            out.append(f)
    return out, pair_count
