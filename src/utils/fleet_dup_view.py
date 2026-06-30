"""Cross-box dedup JOIN over per-box content_id_view state (STEP 4c-ii).

The SECOND consumer of the dedup/identity-arc content_id substrate. Each
active gateway publishes its 4b ``content_id_view`` to a state file (STEP
4c-i); this module JOINs those per-box views into a fleet view.

A fleet DUPLICATE is the same ``(content_id, recipient)`` reaching the
CONFIRMED state on >1 DISTINCT gateway — the live dup-A (moc ``3dfbdb5d``
AND moc3 ``f68c2f56`` both delivering to ``6b1a0120``). The by-design M→R
fan-out delivers each (content_id, recipient) once per DISTINCT recipient,
so it does NOT false-alarm here; a per-box local double-delivery
(distinct_hosts == 1) is the 4b signal, not this one.

PURE + measure-only. The discipline this module enforces is HONESTY
(honest_failure_modes #2 — absence of evidence is not evidence of absence):

  * A box whose view is absent / torn / stale is an explicit COVERAGE GAP
    in ``uncovered[]``, NEVER folded into a healthy-looking zero.
  * With <2 CONTRIBUTING gateways (covered boxes that actually delivered a
    confirmed pair) the fleet-dup verdict is ``indeterminate`` — you cannot
    observe a cross-gateway dup when you can see fewer than two gateways.
    A wired probe (STEP 5) must treat ``indeterminate`` as INERT, not green.
  * The mesh blind spot (``unconfirmable_sent``) and the not-yet-threaded /
    unattributed coverage gaps stay on their OWN lines, never averaged into
    the dup or miss numbers (the #74 confirmation_rate lesson, in rollup
    form).

Lead-repo home (MeshForge owns the dedup arc logic); the MeshAnchor twin
mirrors this shape. The collector that gathers ``box_views`` (ssh-cat for
gateway-only boxes like moc3, local read on the manager) lives in the
map/fleet endpoint layer (STEP 4c-iii).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

# Default freshness window for a published view. The producer republishes
# every ~60s; a view older than this is treated as a coverage gap rather
# than read as live (a down gateway must not look like "0 dups"). Generous
# vs the collector cadence so a single slow run doesn't false-gap.
DEFAULT_FRESH_WINDOW_S = 600.0

# Max fleet-duplicate pairs listed; pairs sort widest-dup-first so a real
# multi-gateway dup is never truncated away, and the drop is surfaced
# (honest_failure_modes #9 — no silent cap).
FLEET_DUP_PAIR_CAP = 250


def _pos_int(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def classify_box_view(
    item: Dict[str, Any],
    *,
    now: float,
    fresh_window_s: float,
) -> Dict[str, Any]:
    """Classify one collected box item into a coverage verdict.

    ``item`` shape (from the collector):
      ``{"host": str, "state": <envelope>|None, "error": str|None}``
    where ``<envelope>`` is the STEP 4c-i published dict
      ``{"schema", "host", "ts", "content_id_view", "unconfirmable_sent"}``.

    Returns ``{host, status, reason, age_s, ts, view, unconfirmable_sent}``
    with ``status`` in {covered, error, absent, stale, malformed}. Only a
    ``covered`` box contributes to the JOIN; everything else is a gap.
    """
    host = str(item.get("host") or "?")
    error = item.get("error")
    out: Dict[str, Any] = {
        "host": host, "status": "covered", "reason": None,
        "age_s": None, "ts": None, "view": None, "unconfirmable_sent": 0,
        "tracked_pairs": 0, "confirmed_pairs": 0,
    }
    if error:
        out["status"] = "error"
        out["reason"] = str(error)
        return out

    state = item.get("state")
    if not isinstance(state, dict):
        out["status"] = "absent"
        out["reason"] = "no state published"
        return out

    view = state.get("content_id_view")
    if not isinstance(view, dict):
        out["status"] = "malformed"
        out["reason"] = "content_id_view missing or not an object"
        return out

    ts = state.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        out["status"] = "malformed"
        out["reason"] = "ts missing or non-numeric"
        return out
    age_s = max(0.0, float(now) - float(ts))
    out["ts"] = float(ts)
    out["age_s"] = age_s
    if age_s > fresh_window_s:
        out["status"] = "stale"
        out["reason"] = (
            f"view {age_s:.0f}s old (window {fresh_window_s:.0f}s) — "
            f"producer may have stopped"
        )
        return out

    # Covered.
    out["view"] = view
    out["unconfirmable_sent"] = _pos_int(state.get("unconfirmable_sent"))
    out["tracked_pairs"] = _pos_int(view.get("tracked_pairs"))
    out["confirmed_pairs"] = _pos_int(view.get("confirmed_pairs"))
    return out


def compute_fleet_dup_view(
    box_views: List[Dict[str, Any]],
    *,
    now: Optional[float] = None,
    fresh_window_s: float = DEFAULT_FRESH_WINDOW_S,
    pair_cap: int = FLEET_DUP_PAIR_CAP,
) -> Dict[str, Any]:
    """JOIN per-box content_id_view state into the fleet dup/miss view.

    See the module docstring for the honesty contract. MEASURE-ONLY.
    """
    now = float(now) if now is not None else time.time()

    classified = [
        classify_box_view(it, now=now, fresh_window_s=fresh_window_s)
        for it in (box_views or [])
        if isinstance(it, dict)
    ]

    covered = [c for c in classified if c["status"] == "covered"]
    uncovered = [
        {"host": c["host"], "status": c["status"], "reason": c["reason"]}
        for c in classified if c["status"] != "covered"
    ]
    covered_hosts = sorted({c["host"] for c in covered})

    # (content_id, recipient) -> {host: confirmed_count}. Built ONLY from
    # covered boxes' confirmed-pair lists.
    pairs: Dict[Tuple[str, str], Dict[str, int]] = {}
    unconfirmable_sent = 0
    untracked_events = 0
    unattributed_events = 0
    rns_unconfirmed_pairs = 0

    for c in covered:
        view = c["view"]
        unconfirmable_sent += c["unconfirmable_sent"]
        untracked_events += _pos_int(view.get("untracked_events"))
        unattributed_events += _pos_int(view.get("unattributed_events"))
        # RNS-confirmable miss proxy: tracked (recipient-attributed) pairs
        # that never reached confirmed in-window. Per-box; kept separate
        # from the cross-box dup join (a different, RNS-side quantity).
        rns_unconfirmed_pairs += max(
            0, c["tracked_pairs"] - c["confirmed_pairs"])

        for entry in view.get("confirmed") or []:
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("content_id") or "")
            rcp = str(entry.get("recipient") or "")
            if not cid or not rcp:
                # An empty content_id/recipient can't key a real pair —
                # never let it masquerade as a cross-box duplicate.
                continue
            n = _pos_int(entry.get("confirmed"))
            if n <= 0:
                continue
            bucket = pairs.setdefault((cid, rcp), {})
            bucket[c["host"]] = bucket.get(c["host"], 0) + n

    # A FLEET duplicate = a pair confirmed by >1 DISTINCT covered host.
    dups: List[Dict[str, Any]] = []
    fleet_duplicate_deliveries = 0
    for (cid, rcp), per_host in pairs.items():
        distinct = len(per_host)
        if distinct > 1:
            total = sum(per_host.values())
            fleet_duplicate_deliveries += distinct - 1  # extra copies x-gateway
            dups.append({
                "content_id": cid,
                "recipient": rcp,
                "distinct_hosts": distinct,
                "total_confirmed": total,
                "hosts": [{"host": h, "confirmed": k}
                          for h, k in sorted(per_host.items())],
            })

    # Widest dup first (then total, then id) so a real multi-gateway dup
    # survives truncation; surface the drop.
    dups.sort(key=lambda d: (-d["distinct_hosts"], -d["total_confirmed"],
                             d["content_id"], d["recipient"]))
    fleet_duplicate_pairs = len(dups)
    cap = max(0, pair_cap)
    truncated = fleet_duplicate_pairs > cap
    if truncated:
        dups = dups[:cap]

    # CONTRIBUTING = covered boxes that actually delivered a confirmed pair
    # (an idle map-only box with an empty view is covered but contributes
    # no dup evidence). <2 contributing => can't observe a cross-box dup.
    contributing_hosts = sorted(
        {h for per_host in pairs.values() for h in per_host})
    if len(contributing_hosts) < 2:
        status = "indeterminate"
        indeterminate_reason = (
            f"only {len(contributing_hosts)} gateway view(s) with confirmed "
            f"deliveries reachable (need >=2 to observe a cross-gateway "
            f"duplicate)"
        )
        if uncovered:
            indeterminate_reason += (
                "; uncovered: "
                + ", ".join(f"{g['host']}({g['status']})" for g in uncovered)
            )
    else:
        status = "ok"
        indeterminate_reason = None

    return {
        "generated_at": now,
        "fresh_window_s": fresh_window_s,
        "status": status,
        "indeterminate_reason": indeterminate_reason,
        "covered_hosts": covered_hosts,
        "contributing_hosts": contributing_hosts,
        "uncovered": uncovered,
        "boxes": [
            {"host": c["host"], "status": c["status"], "age_s": c["age_s"],
             "tracked_pairs": c["tracked_pairs"],
             "confirmed_pairs": c["confirmed_pairs"]}
            for c in classified
        ],
        "fleet_duplicate_pairs": fleet_duplicate_pairs,
        "fleet_duplicate_deliveries": fleet_duplicate_deliveries,
        "fleet_duplicates": dups,
        "fleet_duplicates_truncated": truncated,
        # RNS-side miss proxy — its OWN number, never averaged with dups.
        "rns_unconfirmed_pairs": rns_unconfirmed_pairs,
        # Blind spots — each on its OWN line (never folded into dup/miss).
        "unconfirmable_sent": unconfirmable_sent,
        "untracked_events": untracked_events,
        "unattributed_events": unattributed_events,
    }


__all__ = [
    "compute_fleet_dup_view",
    "classify_box_view",
    "DEFAULT_FRESH_WINDOW_S",
    "FLEET_DUP_PAIR_CAP",
]
