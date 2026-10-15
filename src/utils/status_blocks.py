"""Status blocks shared by both apps — byte-identical with MeshAnchor
(parity BYTE_IDENTICAL), split out of MeshForge's _map_status_endpoints on
2026-10-15 (NOC read grant, the deferred move).

Pure transforms from a box's RAW state (watchdog.json, mini state, the radio
LISTEN/usb probe) into the ``/api/status`` / ``/fleet/slo`` blocks the
fleet-truth builder reads. Every consumer — each app's own status endpoint
AND each app's ssh-spool fallback for a map-less box — calls THESE, so one
artifact has one transform and one staleness threshold (honest_failure_modes
#5). MeshAnchor's private copy of the mini transform had already drifted:
it lacked the missing-timestamp and future-timestamp legs below.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


WATCHDOG_STALE_S = 300.0  # 5 min — 10x the watchdog's 30s tick
MINI_STALE_S = 300.0      # 5 min — 10x mini-dudeai's 30s tick (SSOT for both consumers)
# B7: `age_s = max(0.0, now - ts)` clamps a FUTURE-stamped tick to age 0, so it
# reads fresh/ok for the whole skew window (RTC-less fleet: fake-hwclock steps
# forward, NTP corrects back). A ts more than this far ahead of now is its own
# unobservable leg, never freshness. Generous enough for ordinary NTP jitter.
FUTURE_TS_SLACK_S = 120.0


def watchdog_block_from_payload(
    payload: Dict[str, Any],
    *,
    now: Optional[float] = None,
    stale_after_s: float = WATCHDOG_STALE_S,
) -> Dict[str, Any]:
    """Shape a raw watchdog.json payload into the ``/api/status.watchdog``
    block, re-deriving staleness at read time.

    Module-level so BOTH consumers — the status handler's
    ``_read_watchdog_block`` and the fleet-truth ssh-spool fallback (which
    reads a map-less box's raw watchdog.json over ssh) — share ONE transform
    and ONE staleness threshold (honest_failure_modes #5: two consumers of
    one artifact share one constant). A stale payload reads ok=False with a
    ``stale:`` reason, which the fleet-truth classifier maps to DARK.
    """
    if not isinstance(payload, dict):
        return {"installed": True, "ok": False,
                "reason": "malformed_json: not an object"}

    ts = payload.get("ts")
    now_v = now if now is not None else time.time()
    age_s: Optional[float] = None
    future = False
    if isinstance(ts, (int, float)):
        # the mini transform's B7 legs, now that this feeds a CROSS-BOX view
        # (step-8 non-author pass 2026-10-15, F2): a frozen, future-stamped
        # file on an RTC-less box read fresh for the whole skew window
        future = float(ts) > now_v + FUTURE_TS_SLACK_S
        age_s = max(0.0, now_v - float(ts))
    no_ts = age_s is None
    stale = bool(age_s is not None and age_s > stale_after_s)
    # only a real True is ok; an absent `ok` (legacy writer) still reads ok
    ok_field = payload.get("ok", True) is True
    block = {
        "installed": True,
        "ok": ok_field and not stale and not no_ts and not future,
        "ts": ts,
        "age_s": age_s,
        "probe_count": payload.get("probe_count"),
        "signals": payload.get("signals", []),
    }
    # Per-class disposition map (fleet-truth Phase 0). Passed through
    # verbatim for fleet_truth.merge_coverage; absent on legacy watchdog
    # writers — consumers treat absence as pre-coverage (every non-active
    # class dark), never as clean.
    if isinstance(payload.get("coverage"), dict):
        block["coverage"] = payload["coverage"]
    if no_ts:
        block["reason"] = ("no_tick_timestamp: watchdog state lacks a numeric ts — "
                           "freshness unobservable, treating as not-ok")
    elif future:
        block["reason"] = (f"tick timestamp in the future ({float(ts) - now_v:.0f}s ahead, "
                           f"slack {FUTURE_TS_SLACK_S:.0f}s) — clock skew; freshness "
                           "unobservable until the clock settles")
    elif stale:
        block["reason"] = (
            f"stale: last write {age_s:.0f}s ago "
            f"(threshold {stale_after_s:.0f}s) — watchdog "
            f"daemon may have crashed"
        )
    return block


def mini_block_from_payload(
    payload: Any,
    *,
    now: Optional[float] = None,
    stale_after_s: float = MINI_STALE_S,
) -> Dict[str, Any]:
    """Shape a raw ``mini_dudeai_state.json`` payload into the
    ``/api/status.mini_dudeai`` block, re-deriving staleness at read time.

    Module-level so BOTH consumers — the status handler's
    ``_read_mini_state_block`` and the fleet-truth ssh-spool fallback (which
    reads a map-less gateway's raw mini state over ssh) — share ONE transform
    and ONE staleness threshold (honest_failure_modes #5: two consumers of one
    artifact share one constant). A stale payload reads ``ok=False`` with a
    ``stale:`` reason, which the fleet-truth classifier maps to DARK — never
    green-with-old-numbers.
    """
    if not isinstance(payload, dict):
        return {"installed": True, "ok": False,
                "reason": "malformed_json: not an object"}

    ts = payload.get("last_tick_ts")
    now_v = now if now is not None else time.time()
    age_s: Optional[float] = None
    future = False
    if isinstance(ts, (int, float)):
        # Beyond-slack future ts = clock skew (B7): the max(0.0, ...) clamp
        # below would otherwise read it fresh/ok for the whole skew window.
        future = float(ts) > now_v + FUTURE_TS_SLACK_S
        age_s = max(0.0, now_v - float(ts))
    # Missing/non-numeric last_tick_ts is NOT "fresh forever": a valid-JSON
    # state file without the freshness field (schema drift, foreign writer)
    # must read not-ok, not green (honest_failure_modes #2; 07-23 audit —
    # this block now feeds a second consumer via the ssh-spool path, so the
    # false-green would render fleet-wide).
    no_ts = age_s is None
    stale = bool(age_s is not None and age_s > stale_after_s)

    rules = payload.get("rules") or {}
    active = [
        {"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
         "detail": rs.get("last_detail", "")}
        for rs in rules.values()
        if isinstance(rs, dict) and rs.get("currently_active")
    ]
    top = sorted(
        ({"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
          "fire_count_24h": rs.get("fire_count_24h", 0)}
         for rs in rules.values()
         if isinstance(rs, dict) and rs.get("fire_count_24h")),
        key=lambda r: r["fire_count_24h"], reverse=True,
    )[:5]

    block = {
        "installed": True,
        "ok": not stale and not no_ts and not future,
        "ts": ts,
        "last_tick_iso": payload.get("last_tick_iso"),
        "age_s": age_s,
        "host": payload.get("host"),
        "rule_count": payload.get("rule_count"),
        "error_count": payload.get("error_count"),
        "active_rules": active,
        "top_rules_24h": top,
    }
    if no_ts:
        block["reason"] = (
            "no_tick_timestamp: state lacks a numeric last_tick_ts — "
            "freshness unobservable, treating as not-ok (schema drift?)"
        )
    elif future:
        block["reason"] = (
            f"tick timestamp in the future ({float(ts) - now_v:.0f}s ahead, "
            f"slack {FUTURE_TS_SLACK_S:.0f}s) — clock skew; freshness "
            f"unobservable until the clock settles"
        )
    elif stale:
        block["reason"] = (
            f"stale: last tick {age_s:.0f}s ago "
            f"(threshold {stale_after_s:.0f}s) — mini-dudeai "
            f"daemon may have crashed"
        )
    return block


def radio_connection_from_probe(tcp_listening: bool,
                                usb_present: bool) -> "tuple[bool, str]":
    """The (connected, mode) decision for the radio cell, from a NON-perturbing
    probe: meshtasticd's :4403 LISTEN state (read from /proc, never a connect —
    a connect seizes the single-consumer PhoneAPI and wedges mesh-TX, #17/#75)
    or a USB radio device. Module-level so the map's own ``_read_radio_block``
    and the ssh-spool fallback (which reads a map-less gateway's listen/usb
    state over ssh) reach the identical verdict (honest_failure_modes #5)."""
    if tcp_listening:
        return True, "tcp"
    if usb_present:
        return True, "serial"
    return False, "none"
