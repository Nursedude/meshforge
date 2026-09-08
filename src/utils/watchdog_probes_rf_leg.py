"""watchdog_probes_rf_leg — is the RF leg actually CARRYING traffic?

Born 2026-09-08 from a self-inflicted outage. A capture tool left two RNodes in
promiscuous mode; both stopped receiving, and stayed that way. Every surface we
owned still said healthy: the interface read ``Status: Up``, the service was
active, the box was reachable, and traffic kept flowing — over TCP. The dead RF
leg was invisible for hours because a redundant path was masking it.

``Status: Up`` is PRESENCE. This probe asks for FUNCTION.

⚠️ Why this matters most for the pure-RF gateway variant: with IP backhaul, a
dead RF leg is an invisible degradation. Without one, it is a total outage. A
fallback mode that is never exercised alone rots silently, and IP being up is
exactly what hides the rot.

The discriminator (this is the whole design)
--------------------------------------------
"transmitting with zero received" is NOT a fault by itself — it is the normal,
correct state of a box whose RF network has no other node on it. The fleet's
only RNode sat at ``↑1.15 MB / ↓0 B`` for months, by design, because nothing
else was on the air. A probe that fired on that would be wrong every day.

What IS a fault is a leg that used to hear and has gone deaf. So:

* the interface must have been observed **receiving at least once**, ever —
  otherwise ``inert``, with the honest reason ("no RF peer has ever been heard
  here"), which is information rather than an alarm;
* then TX must be **growing** while RX stays **exactly flat**;
* sustained over ``DEBOUNCE_TICKS`` consecutive observations, so an rnsd
  restart (which zeroes both counters) cannot fire it.

A counter that goes BACKWARDS is a restart, not deafness: the streak resets and
the tick is not counted against the interface.

State
-----
Per-interface bookkeeping is persisted, and a write failure goes through
``note_state_write_failure`` rather than a bare swallow — on 2026-09-02 an
unwritable ``/var/lib/meshforge`` froze three debounce streaks one below their
threshold, so the probes could never fire and the reason named the symptom
instead of the cause (#60 sandbox class, lint MF028).
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from utils.watchdog_probe_core import (
    Signal,
    note_disposition,
    note_state_write_failure,
)

CLASS = "rf_leg_silent"

DEFAULT_STATE_PATH = "/var/lib/meshforge/rf_leg_state.json"

#: TX growth (bytes) within one tick that counts as "we are actually using it".
MIN_TX_DELTA_BYTES = 200.0

#: Consecutive qualifying observations before the signal fires. An rnsd restart
#: zeroes the counters, so a single tick is never enough.
DEBOUNCE_TICKS = 3


def _iface_key(iface) -> str:
    return f"{iface.type_name}[{iface.display_name}]"


def _bytes_of(counter) -> float:
    """rnstatus reports a value plus a unit; normalise to bytes."""
    unit = (getattr(counter, "bytes_unit", "B") or "B").upper()
    scale = {"B": 1.0, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}.get(unit, 1.0)
    return float(getattr(counter, "bytes_total", 0.0)) * scale


def _load_state(state_path: str) -> Dict[str, dict]:
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # Absent or corrupt state is not an alarm; it just means no history yet.
        return {}


def _save_state(state_path: str, state: Dict[str, dict]) -> None:
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError as e:
        note_state_write_failure(state_path, e)


def probe_rf_leg_silent(
    *,
    status=None,
    state_path: str = DEFAULT_STATE_PATH,
    min_tx_delta: float = MIN_TX_DELTA_BYTES,
    debounce: int = DEBOUNCE_TICKS,
) -> Optional[Signal]:
    """Judge each RNode RF interface on bytes MOVED, not on interface state.

    ``status`` is an ``RNSStatus`` (injectable for tests); None → run rnstatus.
    """
    if status is None:
        try:
            from utils.rns_status_parser import run_rnstatus
            status = run_rnstatus()
        except Exception as e:  # noqa: BLE001 - unobservable, never "healthy"
            note_disposition(CLASS, "indeterminate",
                             reason=f"rnstatus unavailable: {e}")
            return None

    ifaces = [i for i in getattr(status, "interfaces", [])
              if "RNodeInterface" in getattr(i, "type_name", "")]
    if not ifaces:
        note_disposition(CLASS, "inert",
                         reason="no RNode RF interface configured on this box")
        return None

    state = _load_state(state_path)
    findings: List[str] = []
    judged = 0

    for iface in ifaces:
        key = _iface_key(iface)
        prior = state.get(key, {})
        tx = _bytes_of(iface.tx)
        rx = _bytes_of(iface.rx)

        ever_rx = bool(prior.get("ever_rx")) or rx > 0
        last_tx = float(prior.get("last_tx", 0.0))
        last_rx = float(prior.get("last_rx", 0.0))
        streak = int(prior.get("flat_streak", 0))

        status_name = getattr(getattr(iface, "status", None), "name", "")
        if status_name != "UP":
            # An interface that is down is another probe's subject entirely.
            streak = 0
        elif tx < last_tx or rx < last_rx:
            # Counters went backwards: rnsd restarted. Not deafness.
            streak = 0
        elif not ever_rx:
            # Never heard a peer. True, and not a fault — this is what a
            # single-node RF network looks like.
            streak = 0
        elif (tx - last_tx) >= min_tx_delta and rx == last_rx:
            streak += 1
        else:
            streak = 0

        if ever_rx and status_name == "UP":
            judged += 1

        state[key] = {"ever_rx": ever_rx, "last_tx": tx,
                      "last_rx": rx, "flat_streak": streak}

        if streak >= debounce:
            findings.append(
                f"{key}: transmitted {tx - last_tx:.0f}+ B/tick for {streak} "
                f"consecutive ticks with RX pinned at {rx:.0f} B, on a leg that "
                "has received before")

    _save_state(state_path, state)

    if findings:
        note_disposition(CLASS, "degraded", reason="; ".join(findings))
        return Signal(
            cls=CLASS,
            subject=findings[0].split(":")[0],
            severity="degraded",
            detail=(
                "RF leg is transmitting into silence — " + "; ".join(findings) +
                ". This leg has received before, so a peer exists. Check the "
                "radio has not been left in a non-default MODE — promiscuous "
                "is the known cause, RNS never clears it, and a power cycle or "
                "an explicit CMD_PROMISC 0x00 is the fix. ⚠️ If IP backhaul is "
                "up, traffic is still flowing and NOTHING ELSE will report this."
            ),
            extra={"interfaces": findings},
        )

    if judged == 0:
        note_disposition(CLASS, "inert",
                         reason="no RF peer has ever been heard on this box's "
                                "RNode leg(s); nothing to judge yet")
        return None

    note_disposition(CLASS, "clean")
    return None
