"""Tracer-fire drilldown data layer.

Each tracer fire writes a per-fire JSON to
``~/.local/state/meshforge/tracer/tracer-<unix>.json``. Format
(schema_version=1) carries the firing host's ``self_short`` plus a
``results`` list, one entry per peer. The lab rollup aggregates these
across the fleet via ssh + cat; this module reads ONE host's tracer
state for the dashboard drilldown surface.

The dashboard's Federation Round-Trip table shows aggregate stats
per (src, dst) pair. Click a cell → fetch this module's data for
the corresponding direction → expand a table of the recent fires.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _operator_home() -> Optional[Path]:
    """Mirror of fleet_snapshot._operator_home.

    Reuses the same root-daemon→operator-uid resolution so the
    dashboard's drilldown works regardless of whether the map daemon
    runs as the operator user or as root. Kept local rather than
    imported to avoid a circular dep with fleet_snapshot.
    """
    if os.geteuid() != 0:
        from utils.paths import get_real_user_home
        return get_real_user_home()
    try:
        from utils.fleet_test_runner import _find_operator_user
    except ImportError:
        return None
    op = _find_operator_user()
    if op is None:
        return None
    op_uid, _ = op
    try:
        import pwd
        return Path(pwd.getpwuid(op_uid).pw_dir)
    except (KeyError, ImportError, OSError):
        return None


def _tracer_dir() -> Optional[Path]:
    """Resolve the directory where this host's tracer fires live.

    Honors ``XDG_STATE_HOME`` like the rest of the lab tooling.
    Returns ``None`` when the operator home can't be resolved.
    """
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "meshforge" / "tracer"
    home = _operator_home()
    if home is None:
        return None
    return home / ".local" / "state" / "meshforge" / "tracer"


def _parse_fire_file(path: Path) -> Optional[Dict[str, Any]]:
    """Parse one tracer-<unix>.json file. Returns ``None`` on any
    error so callers can skip silently — a single bad file shouldn't
    poison the drilldown."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    # Minimum shape sanity check — schema_version + fire_at_unix +
    # results list. Skip rather than crash if a future format change
    # ships before this module updates.
    if not isinstance(data.get("results"), list):
        return None
    if not isinstance(data.get("fire_at_unix"), (int, float)):
        return None
    return data


def get_recent_fires(
    *,
    peer: str,
    since_unix: float,
    limit: int = 60,
) -> Dict[str, Any]:
    """Return this host's tracer fires targeting ``peer`` since
    ``since_unix``, newest-first.

    Output shape (one element per fire):
    ``{"fire_at_unix": float, "fire_at_iso": str, "self_short": str,
       "result": "ok"|"fail"|..., "rtt_ms": int|None, "seq": int}``

    ``limit`` caps the list so an operator clicking a stale cell
    doesn't pull 1000 fires. The cap is enforced after time-window
    filtering, newest first.

    Empty list when the tracer dir doesn't exist (host runs no
    tracer) or when no matching entries fall in the window. Returns
    a dict carrying ``fires`` + ``host`` + ``peer`` + ``since_unix``
    so the response is self-describing.
    """
    tracer_dir = _tracer_dir()
    host = _self_short_default()
    payload: Dict[str, Any] = {
        "host": host,
        "peer": peer,
        "since_unix": since_unix,
        "fires": [],
        "fires_total_seen": 0,
    }
    if tracer_dir is None or not tracer_dir.is_dir():
        payload["reason"] = "no_tracer_dir"
        return payload

    fires: List[Dict[str, Any]] = []
    seen = 0
    for fp in tracer_dir.iterdir():
        if not fp.name.startswith("tracer-") or not fp.name.endswith(".json"):
            continue
        # Cheap pre-filter from the filename: tracer-<unix>.json. If
        # the stem ts is before `since_unix - 30s` skip — saves
        # a json.load on the cold half of the directory. The 30s
        # buffer handles drift between filename and the actual
        # fire_at_unix recorded inside.
        stem = fp.stem  # "tracer-<unix>"
        try:
            file_unix = float(stem.split("-", 1)[1])
        except (IndexError, ValueError):
            file_unix = None
        if file_unix is not None and file_unix < since_unix - 30:
            continue

        data = _parse_fire_file(fp)
        if data is None:
            continue
        seen += 1
        fire_at = float(data["fire_at_unix"])
        if fire_at < since_unix:
            continue
        for r in data["results"]:
            if not isinstance(r, dict):
                continue
            if r.get("peer") != peer:
                continue
            fires.append({
                "fire_at_unix": fire_at,
                "fire_at_iso": data.get("fire_at_iso"),
                "self_short": data.get("self_short") or host,
                "seq": r.get("seq"),
                "result": r.get("result"),
                "rtt_ms": r.get("rtt_ms"),
            })

    fires.sort(key=lambda e: e["fire_at_unix"], reverse=True)
    payload["fires_total_seen"] = seen
    payload["fires"] = fires[:limit]
    payload["truncated"] = len(fires) > limit
    return payload


def _self_short_default() -> str:
    """Best-effort short hostname for the payload. Matches the
    convention used elsewhere in fleet_snapshot."""
    import socket
    return socket.gethostname()


def parse_query(qs: Dict[str, List[str]]) -> Tuple[Optional[str], Optional[float], Optional[int], Optional[str]]:
    """Parse query-string params for the HTTP handler.

    Returns ``(peer, since_unix, limit, error)``. On error, the first
    three are None and the fourth carries a user-facing message.
    Centralized so MF and MA handlers share the parse path.
    """
    peer_vals = qs.get("peer", [])
    peer = peer_vals[0].strip() if peer_vals else ""
    if not peer:
        return None, None, None, "peer is required"
    # Allowlist: ascii letters, digits, dash, underscore, dot.
    # Tracer peer names are friendly short hostnames; reject anything
    # that looks like a path traversal attempt or a shell escape.
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_." for c in peer):
        return None, None, None, "peer name contains disallowed characters"
    if len(peer) > 64:
        return None, None, None, "peer name too long"

    since_vals = qs.get("since", [])
    try:
        since_unix = float(since_vals[0]) if since_vals else time.time() - 3600
    except (TypeError, ValueError):
        return None, None, None, "since must be a unix timestamp"
    # Clamp `since` to a 24h floor — anything older than that and the
    # drilldown isn't useful (and the tracer dir is too big to walk).
    now = time.time()
    if since_unix < now - 24 * 3600:
        since_unix = now - 24 * 3600

    limit_vals = qs.get("limit", [])
    try:
        limit = int(limit_vals[0]) if limit_vals else 60
    except (TypeError, ValueError):
        return None, None, None, "limit must be an integer"
    limit = max(1, min(limit, 200))

    return peer, since_unix, limit, None
