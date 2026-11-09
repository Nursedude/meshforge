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
from typing import Dict, Optional

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
