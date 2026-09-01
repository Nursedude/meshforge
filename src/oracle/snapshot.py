"""Read-only NOC state snapshot for the mesh oracle (Phase 0).

The oracle answers mesh queries from live state WITHOUT touching the radio,
contacting services, or mutating anything. This module performs the (read-only)
state load — reading the same files ``/api/status`` reads — and produces a plain
``NocSnapshot`` that the pure intent functions in ``oracle.intents`` operate on.
Keeping the load here and the logic there makes the intents trivially testable
with fixture snapshots (no files, no clock).

Honest-failure contract (honest_failure_modes.md): a missing/unreadable source
yields an explicit ``installed=False`` / ``stale`` marker, NEVER a fabricated
healthy value; "stale" (the tick clock stopped past threshold) is surfaced, not
averaged into an ok. This mirrors
``utils._map_status_endpoints._read_watchdog_block`` /
``_read_mini_state_block`` — those stay the canonical ``/api/status`` readers;
this is a dependency-free echo so the oracle has no gateway/collector import.
The stale thresholds intentionally match the canonical readers (300s = 10x the
30s tick); ``tests/test_oracle.py`` documents that pairing.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Canonical state files (read-only). Defaults overridable for tests/Phase-1.
WATCHDOG_STATE_PATH = Path("/var/lib/meshforge/watchdog.json")
WATCHDOG_STALE_S = 300.0  # mirror _map_status_endpoints._WATCHDOG_STALE_S
MINI_STALE_S = 300.0      # mirror _map_status_endpoints.MINI_STALE_S (module SSOT)
DEFAULT_STATUS_URL = "http://localhost:5000/api/status"


def _http_get(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (localhost only)
        if getattr(resp, "status", 200) != 200:
            return None
        return resp.read()


# Short-TTL cache for the blocking /api/status GET. The oracle calls this
# SYNCHRONOUSLY on the transport RX thread (paho on_message / the LXMF
# callback / the MeshCore asyncio loop), so when the map is wedged each
# allowed query would otherwise stall that thread up to `timeout` seconds —
# back-pressuring /e/ ACK consumption and bridged delivery exactly when
# operators query 'status' most (#70/#71 GIL-wedge class). Facts tick at a
# 30s cadence, so a few-second TTL is behavior-preserving and collapses a
# burst of queries onto one GET. Keyed by url so tests/Phase-1 overrides
# don't collide.
_STATUS_CACHE_TTL_S = 5.0
_status_cache: Dict[str, Tuple[float, Optional[dict]]] = {}


def fetch_api_status(url: str = DEFAULT_STATUS_URL, timeout: float = 3.0,
                     _get=None, _now=time.monotonic,
                     use_cache: bool = True) -> Optional[dict]:
    """Read-only fetch of the local ``/api/status`` summary, or ``None`` on any
    failure.

    A short, bounded localhost GET that degrades to ``None`` on timeout / non-200
    / bad JSON — so the snapshot stays honestly *unknown* (``nodes:?``) rather
    than fabricating fleet numbers. This is read-only (the same data the map
    serves) and never perturbs the radio. Cached for ``_STATUS_CACHE_TTL_S`` so
    a burst of queries on the RX thread costs at most one GET. ``_get``/``_now``
    are injectable for tests; ``use_cache=False`` forces a fresh read.
    """
    # An injected getter (tests / Phase-1) always reads fresh — the shared
    # cache is a production-only optimization for the real localhost GET.
    cache = use_cache and _get is None
    now = _now()
    if cache:
        hit = _status_cache.get(url)
        if hit is not None and (now - hit[0]) < _STATUS_CACHE_TTL_S:
            return hit[1]
    getter = _get or _http_get
    try:
        raw = getter(url, timeout)
        result = None
        if raw:
            payload = json.loads(raw)
            result = payload if isinstance(payload, dict) else None
    except Exception:
        result = None
    if cache:
        _status_cache[url] = (now, result)
    return result


@dataclass
class NodeRec:
    """One known mesh node, as much as we can say read-only (None = unknown)."""

    node_id: str
    name: Optional[str] = None
    last_seen_age_s: Optional[float] = None
    snr: Optional[float] = None
    hops: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    height_m: Optional[float] = None


@dataclass
class NocSnapshot:
    """A read-only point-in-time view of NOC state the intents answer from.

    Every field is either a real value or an explicit unknown (``None`` /
    ``installed=False`` / ``stale``). The intents must never present an unknown
    as healthy.
    """

    now: float
    box: Optional[str] = None

    # watchdog (/var/lib/meshforge/watchdog.json)
    wd_installed: bool = False
    wd_ok: Optional[bool] = None
    wd_age_s: Optional[float] = None
    wd_stale: bool = False
    wd_signals: List[Dict[str, Any]] = field(default_factory=list)
    wd_reason: Optional[str] = None

    # mini-dudeai (~/mini_dudeai_state.json)
    mini_installed: bool = False
    mini_ok: Optional[bool] = None
    mini_age_s: Optional[float] = None
    mini_stale: bool = False
    mini_active: List[Dict[str, Any]] = field(default_factory=list)
    mini_error_count: Optional[int] = None
    mini_reason: Optional[str] = None

    # fleet posture (optional; from an injected /api/status dict)
    directory_total: Optional[int] = None
    federation_ok: Optional[int] = None
    federation_peers: Optional[int] = None

    # node directory (optional; node_id -> NodeRec). Phase-1 populates this.
    nodes: Dict[str, NodeRec] = field(default_factory=dict)


def _load_dict(path: Path) -> Tuple[Optional[dict], Optional[str], bool]:
    """Read a JSON object read-only.

    Returns ``(payload, reason, existed)`` where ``existed`` distinguishes a
    truly-absent file (not installed) from a present-but-broken one (installed,
    not ok) — the same split the canonical readers make.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "no_state_file", False
    except OSError as exc:
        return None, f"read_error: {exc}", False
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"malformed_json: {exc}", True
    if not isinstance(payload, dict):
        return None, "malformed_json: not an object", True
    return payload, None, True


def _fill_watchdog(snap: NocSnapshot, path: Path) -> Optional[dict]:
    payload, reason, existed = _load_dict(path)
    if payload is None:
        snap.wd_installed = existed
        snap.wd_reason = reason
        if existed:
            snap.wd_ok = False
        return None
    ts = payload.get("ts")
    age = max(0.0, snap.now - float(ts)) if isinstance(ts, (int, float)) else None
    stale = bool(age is not None and age > WATCHDOG_STALE_S)
    signals = payload.get("signals")
    snap.wd_installed = True
    snap.wd_age_s = age
    snap.wd_stale = stale
    snap.wd_signals = signals if isinstance(signals, list) else []
    snap.wd_ok = bool(payload.get("ok", True)) and not stale
    if stale and age is not None:
        snap.wd_reason = f"stale {age:.0f}s (threshold {WATCHDOG_STALE_S:.0f}s)"
    return payload


def _fill_mini(snap: NocSnapshot, path: Path) -> Optional[dict]:
    payload, reason, existed = _load_dict(path)
    if payload is None:
        snap.mini_installed = existed
        snap.mini_reason = reason
        if existed:
            snap.mini_ok = False
        return None
    ts = payload.get("last_tick_ts")
    age = max(0.0, snap.now - float(ts)) if isinstance(ts, (int, float)) else None
    stale = bool(age is not None and age > MINI_STALE_S)
    rules = payload.get("rules") or {}
    active = [
        {
            "rule_id": rs.get("rule_id"),
            "subject": rs.get("subject"),
            "detail": rs.get("last_detail", ""),
        }
        for rs in rules.values()
        if isinstance(rs, dict) and rs.get("currently_active")
    ]
    snap.mini_installed = True
    snap.mini_age_s = age
    snap.mini_stale = stale
    snap.mini_active = active
    snap.mini_error_count = payload.get("error_count")
    snap.mini_ok = not stale
    if stale and age is not None:
        snap.mini_reason = f"stale {age:.0f}s (threshold {MINI_STALE_S:.0f}s)"
    return payload


def _fill_fleet(snap: NocSnapshot, status: dict) -> None:
    directory = status.get("directory")
    if isinstance(directory, dict) and isinstance(directory.get("total"), int):
        snap.directory_total = directory["total"]
    fed = status.get("federation")
    if isinstance(fed, dict):
        peers = fed.get("peer_status")
        if not isinstance(peers, list):
            peers = fed.get("peers")
        if isinstance(peers, list):
            snap.federation_peers = len(peers)
            snap.federation_ok = sum(
                1 for p in peers if isinstance(p, dict) and p.get("ok")
            )
        elif isinstance(fed.get("count"), int):
            snap.federation_peers = fed["count"]


def oracle_log_path() -> Path:
    """Path to the oracle's append-only continuity log.

    Lives under the MeshForge DATA dir (``~/.local/share/meshforge``), NOT the
    bare home, so the write succeeds under the gateway's systemd sandbox
    (``ProtectHome=read-only`` with ``.local/share/meshforge`` in
    ``ReadWritePaths`` — #60). Writing to ``~/mesh_oracle_log.jsonl`` silently
    failed on every fleet gateway (read-only home; append_jsonl swallows the
    OSError). Pure: returns a Path; the caller does the (sandbox-allowed) write.
    """
    from utils.paths import MeshForgePaths
    return MeshForgePaths.get_data_dir() / "mesh_oracle_log.jsonl"


def read_snapshot(
    *,
    home: Optional[Path] = None,
    watchdog_path: Optional[Path] = None,
    status: Optional[dict] = None,
    nodes: Optional[Dict[str, NodeRec]] = None,
    now: Optional[float] = None,
    box: Optional[str] = None,
) -> NocSnapshot:
    """Build a read-only ``NocSnapshot`` from on-disk state (the only I/O path).

    All sources are optional and read-only: a missing source becomes an honest
    unknown, never a fabricated healthy value. ``status`` (an ``/api/status``
    dict) and ``nodes`` are injected by the caller in Phase 1; in Phase 0 tests
    they are fixtures. Paths/clock are injectable so unit tests are hermetic.
    """
    now = time.time() if now is None else now
    snap = NocSnapshot(now=now, box=box)

    wd_payload = _fill_watchdog(snap, watchdog_path or WATCHDOG_STATE_PATH)

    if home is None:
        # MF001: never Path.home() (returns /root under sudo) — use the helper.
        from utils.paths import get_real_user_home

        home = get_real_user_home()
    mini_payload = _fill_mini(snap, Path(home) / "mini_dudeai_state.json")

    if isinstance(status, dict):
        _fill_fleet(snap, status)

    if nodes:
        snap.nodes = dict(nodes)

    if snap.box is None:
        for payload in (wd_payload, mini_payload):
            if isinstance(payload, dict) and payload.get("host"):
                snap.box = payload["host"]
                break

    return snap
