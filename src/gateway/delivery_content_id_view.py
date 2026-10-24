"""Per-box dup/miss aggregation over the delivery events ring (content_id view).

Split out of ``delivery_counters.py`` (MF025 1,500-line cap, 2026-10-23),
behaviour-neutral: ``delivery_counters`` re-exports both names, so
``dc.compute_content_id_view`` and ``dc.CONTENT_ID_VIEW_PAIR_CAP`` are
unchanged for every caller. Pure: no DB, no I/O.
"""

from typing import Any, Dict, List, Tuple


CONTENT_ID_VIEW_PAIR_CAP = 250
"""Max (content_id, recipient) pairs listed in the content_id_view
``confirmed`` array. Bounds the ``/api/gateway/delivery`` JSON; pairs are
sorted confirmed-count DESC so any local duplicate (count>1) sorts to the
top and is never truncated away, and ``confirmed_truncated`` surfaces the
drop honestly (no silent cap — honest_failure_modes #9). The events ring
itself is capped at ``RING_BUFFER_CAP`` rows, so confirmed pairs are
already bounded; this is a second belt for a pathological burst."""


def compute_content_id_view(
    agg_rows: "List[Tuple[Any, Any, Any, Any]]",
    pair_cap: int = CONTENT_ID_VIEW_PAIR_CAP,
) -> Dict[str, Any]:
    """Per-box dup/miss aggregation keyed by (content_id, recipient).

    The FIRST consumer of the dedup/identity-arc content_id substrate
    (STEP 4b, measure-only). ``agg_rows`` is the events ring grouped as
    ``(content_id, recipient, state, count)`` — see ``snapshot()``.

    A per-box DUPLICATE is the SAME (content_id, recipient) reaching the
    CONFIRMED state more than once: a real LOCAL double-delivery (e.g. a
    dual-path / peer-relay overlap landing the same logical message twice
    on one gateway). The by-design M→R fan-out delivers exactly one copy
    per DISTINCT recipient, so it confirms each (content_id, recipient)
    once and does NOT false-alarm here. The cross-GATEWAY dup-A (the same
    pair confirmed by >1 gateway — moc 3dfbdb5d AND moc3 f68c2f56 both →
    6b1a0120) is a DIFFERENT signal, detected by the 4c federation rollup
    that joins these per-box ``confirmed`` pairs across boxes.

    Honest blind-spot accounting (honest_failure_modes #2/#9):
      - ``untracked_events`` — events with NO content_id (the mesh leg and
        any not-yet-threaded path). Surfaced, never hidden: this is the
        coverage gap, not a clean zero.
      - ``unattributed_events`` — content_id known but recipient empty;
        cannot key a per-recipient dup, so NOT counted as a pair (an empty
        recipient must never masquerade as a real (content_id, recipient)).

    MEASURE-ONLY: aggregates and surfaces; never suppresses.
    """
    def _pos_int(v) -> int:
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    # Lazy: delivery_counters re-exports this module (no import cycle).
    from gateway.delivery_counters import DeliveryState
    confirmed_v = DeliveryState.CONFIRMED.value
    untracked_events = 0
    unattributed_events = 0
    tracked_events = 0
    # (content_id, recipient) -> {state: count}
    pairs: Dict[Tuple[str, str], Dict[str, int]] = {}

    for row in agg_rows:
        content_id, recipient, state, count = row
        n = _pos_int(count)
        cid = str(content_id or "")
        rcp = str(recipient or "")
        if not cid:
            untracked_events += n
            continue
        tracked_events += n
        if not rcp:
            unattributed_events += n
            continue
        bucket = pairs.setdefault((cid, rcp), {})
        bucket[state] = bucket.get(state, 0) + n

    confirmed_list: List[Dict[str, Any]] = []
    local_duplicate_pairs = 0
    local_duplicate_deliveries = 0
    for (cid, rcp), states in pairs.items():
        ck = _pos_int(states.get(confirmed_v, 0))
        if ck >= 1:
            confirmed_list.append(
                {"content_id": cid, "recipient": rcp, "confirmed": ck})
        if ck > 1:
            local_duplicate_pairs += 1
            local_duplicate_deliveries += ck - 1

    # confirmed-count DESC (then content_id) so a local dup (count>1) sorts
    # first and survives any truncation; cap for JSON size, drop surfaced.
    confirmed_list.sort(key=lambda p: (-p["confirmed"], p["content_id"]))
    confirmed_pairs = len(confirmed_list)
    cap = max(0, pair_cap)
    truncated = confirmed_pairs > cap
    if truncated:
        confirmed_list = confirmed_list[:cap]

    return {
        "tracked_events": tracked_events,
        "untracked_events": untracked_events,
        "unattributed_events": unattributed_events,
        "tracked_pairs": len(pairs),
        "confirmed_pairs": confirmed_pairs,
        "local_duplicate_pairs": local_duplicate_pairs,
        "local_duplicate_deliveries": local_duplicate_deliveries,
        "confirmed": confirmed_list,
        "confirmed_truncated": truncated,
    }
