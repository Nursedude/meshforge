"""The map collector's per-source reason vocabulary -- ONE table.

Every map source records a ``reason_if_zero`` with its diagnostic (see
``MapDataCollector._record_diagnostic``). The browser turns that reason into a
health badge and a source dot. Until 2026-11-06 the browser kept its own short
list and drew any reason it did not know as healthy, so ``collect_timeout``,
``lock_contended``, ``stale_cached`` and ``slow_sysinfo`` all rendered as
``ok`` (maps audit H2).

This table is the vocabulary. ``web/node_map.html`` carries a byte-equal copy
(``SOURCE_REASON_STATE``), pinned by ``tests/test_map_source_honesty.py``,
which also fails when a collector emits a reason this table lacks.

States:
  ok           -- the source answered (an empty answer can still be healthy)
  inactive     -- off by design (not configured, disabled, deferred to the
                  gateway that owns the radio) -- not a fault
  stale        -- the source FAILED and the map is holding its last-known data
  warn         -- the source failed or could not be read in time
  unobservable -- the reason is not in this table: we do not know what it
                  means, so we never say healthy
"""
from typing import Any, Dict, Iterable, Mapping, Optional

STATES = ("ok", "inactive", "stale", "warn", "unobservable")

#: The meshtasticd leg's reason when the map hands the radio to the gateway
#: (#17). Federation reads it to say a gateway box's map is not a radio
#: witness by design. The collector writes it as a LITERAL so
#: test_map_source_honesty's scan sees it; a test pins the two equal.
DEFERRED_TO_GATEWAY = "deferred_to_gateway"

# Written as plain string pairs so the JS copy can be strict JSON.
SOURCE_REASON_STATE: Dict[str, str] = {
    "ok": "ok",
    "no_positions": "ok",
    "no_data": "ok",
    "not_configured": "inactive",
    "source_disabled": "inactive",
    "deferred_to_gateway": "inactive",
    "stale_cached": "stale",
    "unreachable": "warn",
    "collect_timeout": "warn",
    "lock_contended": "warn",
    "slow_sysinfo": "warn",
}


def reason_state(reason: Optional[str]) -> str:
    """State for a reason; anything outside the vocabulary is unobservable."""
    if not reason:
        return "unobservable"
    return SOURCE_REASON_STATE.get(reason, "unobservable")


def is_known_reason(reason: Optional[str]) -> bool:
    return bool(reason) and reason in SOURCE_REASON_STATE


#: MAP-H H12 (review F1/F3, MEASURED on the manager 2026-11-20): sources that
#: cannot TESTIFY that this box sees, so they never vote on "blind". Every
#: source ``_collect_locked`` runs must be a voter or listed here with why
#: (``tests/test_map_all_dark_is_blind.py`` fails on a new unlisted source).
NON_WITNESS_SOURCES: Dict[str, str] = {
    "unified_tracker": "derived: an in-memory tracker fed by the other sources",
    "node_tracker": "replay of this box's own last-known cache",
    "public_fallback": "records no diagnostic of its own (H10)",
}


def _is_lit(diag: Mapping[str, Any]) -> bool:
    """A source LIT this cycle: it answered (state ok, an empty answer counts),
    or it yielded features and is not merely holding last-known data."""
    state = reason_state(diag.get("reason_if_zero"))
    yielded = diag.get("yielded")
    has_yield = (isinstance(yielded, int) and not isinstance(yielded, bool)
                 and yielded > 0)
    return state == "ok" or (has_yield and state != "stale")


def all_dark_reason(diagnostics: Mapping[str, Mapping[str, Any]],
                    attempted: Iterable[str]) -> Optional[str]:
    """MAP-H H12: why this collect cycle is BLIND, or None when it is not.

    Judges only the sources ATTEMPTED THIS CYCLE (diagnostics carry the last
    cycle's values for sources that did not run). Sources ``inactive`` by
    design and ``NON_WITNESS_SOURCES`` do not vote. Blind = at least one voter and none lit. A source
    attempted with no diagnostic at all is dark (it left no witness). Nothing
    attempted, or every source inactive, is not blind -- a different question.
    """
    dark = []
    for src in sorted(set(attempted)):
        if src in NON_WITNESS_SOURCES:
            continue
        diag = diagnostics.get(src) or {}
        reason = diag.get("reason_if_zero")
        if reason_state(reason) == "inactive":
            continue
        if _is_lit(diag):
            return None
        dark.append(f"{src} {reason or 'no reason'}")
    if not dark:
        return None
    return "every map source dark: " + ", ".join(dark)
