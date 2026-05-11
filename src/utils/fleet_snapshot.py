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
    }

`boundaries_top` is empty for now — MF doesn't instrument the timed
systemd boundaries MA uses for SLO histograms. Future work: hook into
`utils.service_check` to record p50/p95/p99 of `systemctl is-active`
calls. The empty list keeps the shape valid; MA renders peers with
no boundaries fine.
"""

from __future__ import annotations

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
    }
