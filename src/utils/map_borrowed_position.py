"""MAP-H H4a — a BORROWED position: one owner for what it is and how it counts.

A node THIS box's radio hears without a GPS fix is drawn at a federated
peer's fix (``_merge_federation``). That feature is ours (the hearing is
real) but its position is not, so:

* every count of "nodes with a position" excludes it and reports it beside;
* no store of this box's OWN state keeps the borrowed coordinates — the
  history DB keeps our hearing without them, the Source-7 disk cache (which
  replays positioned nodes as ours) does not get the feature at all.

Before this module the rule lived in four places (merge, collector count,
three geojson endpoint overwrites, the page's refresh gate) and review found
the same defect kind twice (r1 F5, r2 F2). Readers import from here.
"""

from typing import Any, Dict, Iterable

#: The lender's name on an adopted feature. Its PRESENCE marks it borrowed
#: (an empty lender name must still read borrowed — r2 F7).
POSITION_FROM = "position_from"
#: The lender's source_origin for that fix (radio-heard vs a firehose).
POSITION_ORIGIN = "position_origin"


def is_borrowed(feature: Dict[str, Any]) -> bool:
    return POSITION_FROM in ((feature or {}).get("properties") or {})


def position_counts(features: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """``nodes_with_position`` (ours, federated included as before) and
    ``nodes_with_borrowed_position`` — the ONE place either is computed."""
    own = borrowed = 0
    for f in features:
        if is_borrowed(f):
            borrowed += 1
        else:
            own += 1
    return {"nodes_with_position": own, "nodes_with_borrowed_position": borrowed}


def own_history_feature(feature: Dict[str, Any]) -> Dict[str, Any]:
    """History keeps THIS radio's hearing, never a peer's coordinates."""
    return {**feature, "geometry": {}} if is_borrowed(feature) else feature


def own_cache_geojson(geojson: Dict[str, Any]) -> Dict[str, Any]:
    """The disk cache replays positioned nodes as ours — borrowed ones are
    not ours to replay."""
    feats = geojson.get("features") or []
    if not any(is_borrowed(f) for f in feats):
        return geojson
    return {**geojson, "features": [f for f in feats if not is_borrowed(f)]}
