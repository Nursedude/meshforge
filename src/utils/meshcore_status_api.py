"""MeshCore status for OUT-OF-PROCESS readers — ``GET /api/json/meshcore``.

Served on the gateway's existing :9090 listener (``utils.metrics_server``,
bound to 127.0.0.1) by ``MetricsHTTPHandler`` delegating here, so the TUI in
another process can render the radio's contact table (roadmap 1b), its
firmware fact (1c) and the oracle posture (1d) from the process that holds
the radio. Born 2026-09-22 (roadmap 1e): MeshForge's live gateway shape is
``bridge_cli.py``, which had NO surface for any of this — the TUI's
Statistics pane read an in-process handle that is always empty from a
separate process and reported "Gateway bridge is not running" forever.

Routes:
    GET /api/json/meshcore            posture + device info + bridge counters
    GET /api/json/meshcore/contacts   the radio's own contact table

Every leg is tri-state. ``observable=False`` means this process cannot see
the thing (no bridge, no MeshCore handler) and is NEVER rendered as "off"
or "empty" — unobservable ≠ absent (honest_failure_modes #1/#2).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

_MISSING = object()


def _bridges() -> List[Any]:
    try:
        from gateway.gateway_cli import registered_bridges
    except ImportError:
        return []
    try:
        return list(registered_bridges())
    except Exception:
        return []


def _meshcore_bridge(bridges: List[Any]):
    """The bridge that owns a MeshCore handler, else the first bridge, else None."""
    for b in bridges:
        if getattr(b, "_meshcore_handler", None) is not None:
            return b
    return bridges[0] if bridges else None


def oracle_posture(meshcore_handler) -> Dict[str, Any]:
    """The oracle leg's posture, as the DAEMON actually built it.

    Published by the daemon, never recomputed from a TUI's own environment:
    the posture is decided by env vars in the gateway's process (on the
    fleet they arrive via a systemd drop-in), so only the process that built
    the responder can say what it built (calibrated_claims rule 7).

    Three outcomes kept distinct — collapsing any pair reports a broken or
    unobservable oracle as a deliberately disabled one:
      observable=False        no MeshCore handler here; UNKNOWN, never off
      enabled=False + error   asked for, FAILED to build
      enabled=False           off by design (MESHFORGE_ORACLE_ENABLED unset)
    """
    if meshcore_handler is None:
        return {"observable": False,
                "reason": "no meshcore handler on this bridge"}
    err = getattr(meshcore_handler, "_oracle_error", None)
    if err:
        return {"observable": True, "enabled": False, "error": err}
    oracle = getattr(meshcore_handler, "_oracle", None)
    if oracle is None:
        return {"observable": True, "enabled": False}

    # ⚠️ MeshOracleResponder stores these PRIVATELY (_allowlist,
    # _allowed_channels, _answer_all, _cooldown_s, _transport); only
    # `consume` is public. Reading the constructor's kwarg names instead
    # (a proxy for the object) shipped allowlist=0 channels=[] on the twin
    # while its build log said allowlist=1 — and "allowlist: 0" is a REAL
    # fail-closed posture, so the default was a confident wrong answer.
    # A field we cannot read is NAMED in `unreadable`, never defaulted.
    missing: List[str] = []

    def _read(*names, default=None):
        for n in names:
            v = getattr(oracle, n, _MISSING)
            if v is not _MISSING:
                return v
        missing.append(names[0].lstrip("_"))
        return default

    posture = {
        "observable": True,
        "enabled": True,
        "answer_all": bool(_read("_answer_all", "answer_all", default=False)),
        # Count, never the tokens: node keys need no wider audience.
        "allowlist": len(_read("_allowlist", "allowlist", default=()) or ()),
        "channels": sorted(
            str(c) for c in (_read("_allowed_channels", "allowed_channels",
                                   default=()) or ())),
        "cooldown_s": _read("_cooldown_s", "cooldown_s"),
        "consume": bool(_read("consume", "_consume", default=False)),
        "transport": _read("_transport", "transport"),
    }
    if missing:
        posture["unreadable"] = sorted(missing)
    return posture


def _bridge_counters(bridge) -> Dict[str, Any]:
    """``bridge.stats`` copied under its lock, with start_time → uptime."""
    try:
        lock = getattr(bridge, "_stats_lock", None)
        if lock is not None:
            with lock:
                snap = dict(bridge.stats)
        else:
            snap = dict(bridge.stats)
    except Exception as e:
        return {"observable": False, "reason": f"stats read failed: {e}"}
    start = snap.pop("start_time", None)
    uptime = None
    start_iso = None
    if start is not None:
        try:
            uptime = (datetime.now() - start).total_seconds()
            start_iso = start.isoformat()
        except Exception:
            uptime = start_iso = None
    return {"observable": True, "uptime_seconds": uptime,
            "start_time": start_iso, "stats": snap}


def build_meshcore_status(bridges: Optional[List[Any]] = None) -> Dict[str, Any]:
    """The full posture payload for ``/api/json/meshcore``."""
    bridges = _bridges() if bridges is None else list(bridges)
    bridge = _meshcore_bridge(bridges)
    if bridge is None:
        return {"observable": False,
                "reason": "no gateway bridge registered in this process",
                "bridges": 0}
    handler = getattr(bridge, "_meshcore_handler", None)
    cfg = getattr(getattr(bridge, "config", None), "meshcore", None)
    payload: Dict[str, Any] = {
        "observable": True,
        "bridges": len(bridges),
        "running": bool(getattr(bridge, "_running", False)),
        "enabled": bool(cfg is not None and getattr(cfg, "enabled", False)),
        "simulation": bool(cfg is not None and getattr(cfg, "simulation_mode", False)),
        "handler": handler is not None,
        "connected": bool(handler.is_connected) if handler is not None else False,
        "oracle": oracle_posture(handler),
        "counters": _bridge_counters(bridge),
    }
    if handler is None:
        payload["device"] = {"observed": False,
                             "reason": "no meshcore handler on this bridge"}
    else:
        try:
            payload["device"] = handler.get_device_info_snapshot()
        except Exception as e:
            payload["device"] = {"observed": False,
                                 "reason": f"device info failed: {e}"}
    return payload


def build_contacts(bridges: Optional[List[Any]] = None) -> Dict[str, Any]:
    """The payload for ``/api/json/meshcore/contacts``."""
    bridges = _bridges() if bridges is None else list(bridges)
    bridge = _meshcore_bridge(bridges)
    handler = getattr(bridge, "_meshcore_handler", None) if bridge is not None else None
    if handler is None:
        return {"observed": False, "count": 0, "contacts": [],
                "reason": ("no gateway bridge registered in this process"
                           if bridge is None else
                           "no meshcore handler on this bridge")}
    try:
        return handler.get_contacts_snapshot()
    except Exception as e:
        return {"observed": False, "count": 0, "contacts": [],
                "reason": f"contacts snapshot failed: {e}"}


def handle_get(handler: Any) -> None:
    """Serve ``/api/json/meshcore[/contacts]`` on a MetricsHTTPHandler."""
    path = handler.path.split("?", 1)[0].rstrip("/")
    if path == "/api/json/meshcore":
        body, status = build_meshcore_status(), 200
    elif path == "/api/json/meshcore/contacts":
        body, status = build_contacts(), 200
    else:
        body, status = {"error": f"unknown meshcore path: {path}"}, 404
    raw = json.dumps(body, indent=2, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(raw)
