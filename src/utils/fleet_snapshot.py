"""Fleet SLO snapshot — MeshForge's side of the MeshAnchor peer contract.

MeshAnchor's `/fleet/rollup` endpoint polls `/fleet/slo` on each peer it
knows about (see `MA src/monitoring/fleet_rollup.py:_fetch_peer_snapshot`).
This module produces the same shape MA emits for itself, so a MF box
plugged into MA's `fleet.json` shows up in the rollup with the same
visual treatment as a MA-native peer.

Schema (parity with MA's `slo_view`):
    {
        "generated_at": <unix float>,
        "host": <hostname>,
        "uptime_s": <float — daemon uptime, not system uptime>,
        "overall_status": "ready" | "degraded",
        "services": {
            "total": int,
            "available": int,
            "by_state": {<state>: count, ...},
            "required": {"total": int, "available": int, "by_state": {...}},
            "optional": {"total": int, "available": int, "by_state": {...}},
        },
        "boundaries_top": [],
        "radio": {"connected": bool, "name": <str|None>,
                  "preset": <str|None>, "battery_pct": <int|None>},
        "errors": [<str>, ...],
        "schedules": {
            "healthy": bool,           # all timers nominal?
            "stale_count": int,        # how many in red
            "units": [
                {
                    "name": "meshforge-tracer.timer",
                    "scope": "user" | "system",
                    "next_fire_unix": <float|None>,  # None = NEXT/LEFT show "-"
                    "last_fire_unix": <float|None>,
                    "age_s": <float|None>,           # now - last_fire
                    "stale": bool,                   # next is None OR overdue
                },
                ...
            ],
        },
    }

`boundaries_top` is empty for now — MF doesn't instrument the timed
systemd boundaries MA uses for SLO histograms. Future work: hook into
`utils.service_check` to record p50/p95/p99 of `systemctl is-active`
calls. The empty list keeps the shape valid; MA renders peers with
no boundaries fine.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

REQUIRED_SERVICES = ("meshtasticd", "mosquitto")
OPTIONAL_SERVICES = (
    "rnsd",
    "meshforge",
    "meshforge-map",
    "meshforge-maps",
)

# Timer unit prefixes we surface in the schedules block. System timers
# like apt-daily / man-db belong to the OS, not the fleet — they would
# be noise. Add a prefix here when a new fleet timer ships.
SCHEDULE_UNIT_PREFIXES = ("meshforge", "meshanchor", "moc-")

# Stale heuristic: timer flagged red when its last fire is older than
# this multiplier × the timer's nominal interval. The interval is
# derived from the gap between successive fires when available;
# otherwise the heuristic falls back to "next_fire is None" only.
SCHEDULE_STALE_MULTIPLIER = 2.0


def _process_uptime_s() -> float:
    """Read true daemon uptime from /proc/self/stat regardless of import timing.

    Module-level `time.monotonic()` doesn't work when the module is
    lazy-imported by the HTTP handler — first request sets the zero
    point. /proc/self/stat field 22 is starttime in clock ticks since
    boot; subtract from /proc/uptime to get seconds since process start.
    Linux-only by design (the fleet runs on Pi).
    """
    try:
        with open("/proc/self/stat") as f:
            fields = f.read().split()
        starttime_ticks = int(fields[21])
        with open("/proc/uptime") as f:
            system_uptime_s = float(f.read().split()[0])
        hz = os.sysconf("SC_CLK_TCK")
        return max(0.0, system_uptime_s - (starttime_ticks / hz))
    except (OSError, ValueError, IndexError):
        return 0.0


def _systemctl_state(unit: str) -> str:
    """Return the systemd unit state. Maps to MA's vocabulary:

    - "available"    — `is-active` returns "active"
    - "not_running"  — anything else (inactive, failed, not-found, error)

    Never raises. systemctl absence (unlikely on a Pi) → "not_running".
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=3,
        )
        return "available" if result.stdout.strip() == "active" else "not_running"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "not_running"


def _services_rollup() -> Dict[str, Any]:
    """Roll required + optional services into the MA shape."""
    req_states = {svc: _systemctl_state(svc) for svc in REQUIRED_SERVICES}
    opt_states = {svc: _systemctl_state(svc) for svc in OPTIONAL_SERVICES}

    def bucket(states: Dict[str, str]) -> Dict[str, Any]:
        by_state: Dict[str, int] = {}
        for s in states.values():
            by_state[s] = by_state.get(s, 0) + 1
        available = by_state.get("available", 0)
        return {
            "total": len(states),
            "available": available,
            "by_state": by_state,
        }

    req = bucket(req_states)
    opt = bucket(opt_states)
    all_by_state: Dict[str, int] = {}
    for s in (*req_states.values(), *opt_states.values()):
        all_by_state[s] = all_by_state.get(s, 0) + 1

    return {
        "total": req["total"] + opt["total"],
        "available": req["available"] + opt["available"],
        "by_state": all_by_state,
        "required": req,
        "optional": opt,
        "_detail": {**req_states, **opt_states},
    }


def _probe_radio() -> Dict[str, Any]:
    """Probe Meshtastic (TCP:4403) and MeshCore (/dev/ttyMeshCore).

    MF boxes are Meshtastic-primary, but the symlink-aware probe also
    surfaces MeshCore presence — fixes the MA-side blindness where
    `_get_radio_status_summary` returned `connected=False` on a
    MeshCore-only host because it excluded `/dev/ttyMeshCore` and never
    asked the MeshCore question.
    """
    connected = False
    name = None

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("localhost", 4403)) == 0:
                connected = True
                name = "meshtasticd"
    except (OSError, socket.timeout):
        pass

    if not connected:
        import os
        if os.path.exists("/dev/ttyMeshCore"):
            connected = True
            name = "meshcore"

    return {
        "connected": connected,
        "name": name,
        "preset": None,
        "battery_pct": None,
    }


def _list_timers_scope(scope: str) -> List[Dict[str, Any]]:
    """Return systemctl timers in the given scope (`system` or `user`).

    Uses ``systemctl [--user] list-timers --all --output=json`` —
    available on systemd 247+ (Bookworm and later). Failures return
    an empty list rather than raising; a host without a user session
    just contributes its system timers.
    """
    cmd = ["systemctl"]
    if scope == "user":
        cmd.append("--user")
    cmd.extend(["list-timers", "--all", "--output=json"])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError,
            json.JSONDecodeError):
        return []


def _normalize_timer(raw: Dict[str, Any], scope: str,
                     now_unix: float) -> Optional[Dict[str, Any]]:
    """Normalize one systemctl-list-timers JSON entry.

    systemctl emits ``next``/``last`` in **microseconds** since the
    unix epoch (or 0 when unset). We convert to seconds and surface
    the 0-as-None convention so JS can render "—" without ambiguity.
    """
    name = raw.get("unit")
    if not name:
        return None

    def _us_to_unix(us: Any) -> Optional[float]:
        try:
            us = int(us)
        except (TypeError, ValueError):
            return None
        return (us / 1_000_000.0) if us > 0 else None

    next_unix = _us_to_unix(raw.get("next"))
    last_unix = _us_to_unix(raw.get("last"))
    age_s = (now_unix - last_unix) if last_unix is not None else None

    # Stale signature 1: NEXT is unset. moc1's wedged tracer.timer
    # 2026-05-14 12:30 HST → 2026-05-15 06:48 HST sat exactly here.
    stale = next_unix is None

    # Stale signature 2: last fire is older than 2× the nominal
    # interval. Interval is inferred only when we have both next + last
    # (interval ≈ next - last). For timers with no last_unix yet (boxes
    # that just booted) skip — they're not stale, just fresh.
    if not stale and last_unix is not None and next_unix is not None:
        interval = next_unix - last_unix
        if interval > 0 and age_s is not None:
            stale = age_s > SCHEDULE_STALE_MULTIPLIER * interval

    return {
        "name": name,
        "scope": scope,
        "next_fire_unix": next_unix,
        "last_fire_unix": last_unix,
        "age_s": round(age_s, 1) if age_s is not None else None,
        "stale": stale,
    }


def _schedules_block() -> Dict[str, Any]:
    """Build the schedules block for the SLO snapshot.

    Surfaces fleet-relevant timers (prefix match against
    SCHEDULE_UNIT_PREFIXES). Empty units list with healthy=true is the
    legitimate "host runs no fleet timers" state — moc3 currently lacks
    meshforge-map, so its system list might be sparse.
    """
    now = time.time()
    units: List[Dict[str, Any]] = []
    for scope in ("system", "user"):
        for raw in _list_timers_scope(scope):
            entry = _normalize_timer(raw, scope, now)
            if entry is None:
                continue
            if not any(entry["name"].startswith(p)
                       for p in SCHEDULE_UNIT_PREFIXES):
                continue
            units.append(entry)
    # Sort: stale first (red badges surface together), then by name.
    units.sort(key=lambda u: (not u["stale"], u["name"]))
    stale_count = sum(1 for u in units if u["stale"])
    return {
        "healthy": stale_count == 0,
        "stale_count": stale_count,
        "units": units,
    }


def build_slo_snapshot(*, collector: Optional[Any] = None) -> Dict[str, Any]:
    """Build the SLO snapshot in MA's expected shape.

    `collector` is the running map-data collector (passed by the HTTP
    handler that owns it). Currently unused — kept in the signature so
    future fields like `radio.preset` can derive from collector state
    without changing call sites.
    """
    del collector  # reserved for future enrichment

    services = _services_rollup()
    errors: List[str] = []
    for unit, state in services["_detail"].items():
        if state != "available" and unit in REQUIRED_SERVICES:
            errors.append(f"{unit}: {state}")

    overall_status = "ready" if services["required"]["available"] == services["required"]["total"] else "degraded"

    return {
        "generated_at": time.time(),
        "host": socket.gethostname(),
        "uptime_s": _process_uptime_s(),
        "overall_status": overall_status,
        "services": {k: v for k, v in services.items() if k != "_detail"},
        "boundaries_top": [],
        "radio": _probe_radio(),
        "errors": errors,
        "schedules": _schedules_block(),
    }
