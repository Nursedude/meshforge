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

    A systemd-launched daemon inherits a bare environment with no
    ``XDG_RUNTIME_DIR``, so ``systemctl --user`` can't find the user
    manager's socket and returns nothing. Inject the standard
    ``/run/user/<euid>`` path when calling user-scope; linger keeps
    the user manager up, so the path exists on the fleet boxes
    (verified: `loginctl show-user wh6gxz Linger=yes`).

    Root-firing-operator case: when this process runs as root (e.g.
    a map daemon with ``User=root``) and ``scope == "user"``, root's
    own /run/user/0 has no bus socket. Drop privilege to the
    operator user via ``sudo -n -u <op> env XDG_RUNTIME_DIR=...
    systemctl --user list-timers ...`` so the call lands on the
    operator's user systemd manager. Mirrors the fire_unit pattern
    from c6d7609. Requires a sudoers entry on root-daemon hosts.
    """
    cmd: List[str]
    env: Optional[Dict[str, str]] = None

    if scope == "user" and os.geteuid() == 0:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
        if op is None:
            return []
        op_uid, op_name = op
        cmd = [
            "sudo", "-n", "-u", op_name,
            "env", f"XDG_RUNTIME_DIR=/run/user/{op_uid}",
            "systemctl", "--user", "list-timers", "--all", "--output=json",
        ]
    else:
        cmd = ["systemctl"]
        if scope == "user":
            cmd.append("--user")
            if "XDG_RUNTIME_DIR" not in os.environ:
                env = os.environ.copy()
                env["XDG_RUNTIME_DIR"] = f"/run/user/{os.geteuid()}"
        cmd.extend(["list-timers", "--all", "--output=json"])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, env=env,
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


def _show_unit_props(unit: str, scope: str,
                     props: List[str]) -> Dict[str, str]:
    """Return ``systemctl [--user] show <unit> -p <props>`` as a dict.

    Used by ``_serve_fleet_tests_list`` to (a) detect ``LoadState ==
    not-found`` so the dashboard can render "not installed on this
    host" instead of silently failing, and (b) read
    ``ExecMainExitTimestamp`` / ``ActiveEnterTimestamp`` so a manual
    ``systemctl start <unit>.service`` advances the chip (the timer's
    ``LastTriggerUSec`` doesn't update on manual fires).

    ``--timestamp=unix`` returns ``@<unix-int>`` for timestamps, which
    parses unambiguously across locales (systemd 250+; Bookworm ships
    252, Trixie 257). Empty values come back as ``Key=`` — preserved
    as empty strings so callers can use ``or None`` semantics.

    Same root→operator drop-priv pattern as ``_list_timers_scope``.
    Returns ``{}`` on any error rather than raising — the dashboard
    treats missing properties as "no extra signal."
    """
    cmd: List[str]
    env: Optional[Dict[str, str]] = None
    prop_arg = ",".join(props)

    if scope == "user" and os.geteuid() == 0:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
        if op is None:
            return {}
        op_uid, op_name = op
        cmd = [
            "sudo", "-n", "-u", op_name,
            "env", f"XDG_RUNTIME_DIR=/run/user/{op_uid}",
            "systemctl", "--user", "show", unit,
            "-p", prop_arg, "--timestamp=unix",
        ]
    else:
        cmd = ["systemctl"]
        if scope == "user":
            cmd.append("--user")
            if "XDG_RUNTIME_DIR" not in os.environ:
                env = os.environ.copy()
                env["XDG_RUNTIME_DIR"] = f"/run/user/{os.geteuid()}"
        cmd.extend(["show", unit, "-p", prop_arg, "--timestamp=unix"])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}

    if result.returncode != 0:
        return {}

    out: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out


def _parse_unix_at(value: str) -> Optional[float]:
    """Parse ``--timestamp=unix`` output (``@<int>``) to a unix float.

    Empty string or missing ``@`` prefix → ``None``. Zero values
    (``@0``) → ``None`` to match the ``0-as-unset`` convention used
    by ``_normalize_timer``.
    """
    if not value:
        return None
    if value.startswith("@"):
        value = value[1:]
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return float(n) if n > 0 else None


# Ecosystem CI status — the timer twice-daily fires write
# ``~/.meshforge-ci-status`` (see ``scripts/ecosystem_ci_status.sh``).
# Only the fleet box(es) that enable the timer write the file; other
# boxes return ``available=False`` and the dashboard picks the
# freshest peer. Bump if the timer cadence changes (currently
# 08:00/18:00 → ~10h gap).
CI_STATUS_STALE_AFTER_S = 14 * 3600


def _operator_home() -> "Optional[Any]":
    """Resolve the operator user's home directory.

    Non-root daemon: ``get_real_user_home()`` returns the operator's
    home. Root daemon (any host that runs the map daemon as
    ``User=root``): walk ``/run/user/<uid>/bus`` via
    ``_find_operator_user`` — same helper used by the schedules
    root→operator drop. Returns ``None`` when no operator can be
    resolved (block becomes ``available=False``).
    """
    from pathlib import Path
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


def _parse_ci_status_file(text: str) -> Dict[str, Any]:
    """Parse the plain-text status file into a structured block.

    Format (one repo per line, after a single ``# header — generated <ts>``):
    ``  <repo-name>  <state>  <sha7>  <commit-title>``

    Unknown lines, blank lines, and a trailing "# Overdue open PRs"
    section are silently ignored — the surface is repos + overall.
    """
    from datetime import datetime
    generated_at: Optional[str] = None
    generated_unix: Optional[float] = None
    repos: List[Dict[str, str]] = []
    in_overdue_section = False

    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            # Header line carries the generation timestamp; later '#'
            # lines (e.g. "# Overdue open PRs") gate the repos block.
            if "Overdue" in line:
                in_overdue_section = True
                continue
            if "generated" in line and generated_at is None:
                # "# MeshForge ecosystem CI status — generated <iso>"
                marker = "generated "
                idx = line.find(marker)
                if idx >= 0:
                    generated_at = line[idx + len(marker):].strip()
                    try:
                        generated_unix = datetime.fromisoformat(generated_at).timestamp()
                    except (ValueError, TypeError):
                        generated_unix = None
            continue
        if in_overdue_section:
            continue
        parts = line.split()
        # Repo line: name, state, [sha (7 hex)], [title…]. The "no-runs"
        # state has no sha (script doesn't emit one). The pill doesn't
        # surface the title — dashboard can grow a tooltip later.
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        if state == "no-runs":
            repos.append({"name": name, "state": state, "sha": ""})
            continue
        if len(parts) < 3:
            continue
        sha = parts[2]
        # 7-char hex sanity check on sha so we don't capture a stray
        # status word as a sha.
        if len(sha) == 7 and all(c in "0123456789abcdef" for c in sha):
            repos.append({"name": name, "state": state, "sha": sha})

    overall = _ci_overall(repos)
    return {
        "available": True,
        "generated_at": generated_at,
        "generated_unix": generated_unix,
        "overall": overall,
        "red_count": sum(1 for r in repos if r["state"] == "failure"),
        "in_progress_count": sum(1 for r in repos if r["state"] == "in_progress"),
        "repos": repos,
    }


def _ci_overall(repos: List[Dict[str, str]]) -> str:
    """Aggregate per-repo state to a single pill color.

    Precedence: failure > in_progress > anything-not-success > success.
    Empty list (no parseable lines) returns ``"unknown"``.
    """
    if not repos:
        return "unknown"
    states = {r["state"] for r in repos}
    if "failure" in states:
        return "failure"
    if "in_progress" in states:
        return "in_progress"
    if states <= {"success"}:
        return "success"
    return "degraded"


def _ci_status_block() -> Dict[str, Any]:
    """Read ``~/.meshforge-ci-status`` (operator home) and structure it.

    Returns ``{"available": False, "reason": ...}`` when:
    - operator home can't be resolved (root daemon, no operator user)
    - the file doesn't exist (most fleet boxes won't have it — only
      the box that runs the meshforge-ci-status timer writes it)
    - the read fails (permissions, IO)

    The dashboard JS picks the freshest ``available=True`` block
    across all peers — single source of truth wins.
    """
    home = _operator_home()
    if home is None:
        return {"available": False, "reason": "no_operator_home"}
    path = home / ".meshforge-ci-status"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"available": False, "reason": "no_file"}
    except (PermissionError, OSError) as e:
        return {"available": False, "reason": f"read_error: {e.__class__.__name__}"}
    block = _parse_ci_status_file(text)
    if block.get("generated_unix") is not None:
        age = time.time() - block["generated_unix"]
        block["age_s"] = round(age, 1)
        block["stale"] = age > CI_STATUS_STALE_AFTER_S
    else:
        block["age_s"] = None
        block["stale"] = False
    return block


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


def _path_table_summary() -> Dict[str, Any]:
    """Compact summary of the cached RNS path_table for /fleet/slo.

    Returns `{available, count, ts}` rather than dumping the whole
    path list (which can be hundreds of entries). The consumer drills
    into `/api/network/rns/paths` for detail.

    Track 2.6 of the federation→DB pressure→wedge cascade arc.
    """
    try:
        from utils._map_collector_rns import get_cached_path_table_snapshot
        snap = get_cached_path_table_snapshot()
        return {
            "available": bool(snap.get("available")),
            "reason": snap.get("reason"),
            "count": len(snap.get("paths", [])),
            "ts": snap.get("ts", 0.0),
        }
    except Exception as e:
        return {"available": False, "reason": f"summary_failed: {e!r}", "count": 0, "ts": 0.0}


def _interfaces_summary() -> Dict[str, Any]:
    """Compact summary of the cached RNS interfaces for /fleet/slo.

    Returns `{available, count, online_count, ts}`. Drill into
    `/api/network/interfaces` for per-interface RX/TX bytes.
    """
    try:
        from utils._map_collector_rns import get_cached_interface_snapshot
        snap = get_cached_interface_snapshot()
        interfaces = snap.get("interfaces", [])
        return {
            "available": bool(snap.get("available")),
            "reason": snap.get("reason"),
            "count": len(interfaces),
            "online_count": sum(1 for i in interfaces if i.get("online")),
            "ts": snap.get("ts", 0.0),
        }
    except Exception as e:
        return {"available": False, "reason": f"summary_failed: {e!r}",
                "count": 0, "online_count": 0, "ts": 0.0}


def _cascade_summary() -> Dict[str, Any]:
    """Compact summary of the cascade detector state for /fleet/slo.

    Returns `{total, clean, suspected, pre_fail, wedged, degraded}`.
    Drill into `/fleet/cascade` for per-fingerprint evidence + metric.
    """
    try:
        from utils.cascade_detector import get_singleton
        counts = get_singleton().summary()
        # Normalize: always present keys consumers can rely on.
        return {
            "total": sum(counts.values()),
            "clean": counts.get("clean", 0),
            "suspected": counts.get("suspected", 0),
            "pre_fail": counts.get("pre_fail", 0),
            "wedged": counts.get("wedged", 0),
            "degraded": counts.get("degraded", 0),
        }
    except Exception as e:
        return {"total": 0, "clean": 0, "suspected": 0,
                "pre_fail": 0, "wedged": 0, "degraded": 0,
                "_error": f"summary_failed: {e!r}"}


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

    # Observability surface for the goal: "where did this message go +
    # why did it fail" — three compact summary blocks consumers (MeshAnchor
    # dashboard) merge across the fleet. Track 2.6 of the
    # we-have-a-cycle-jolly-wadler stability arc.
    path_table = _path_table_summary()
    interfaces = _interfaces_summary()
    cascade = _cascade_summary()

    # Overall status downgrades when cascade fingerprints have escalated.
    if cascade["pre_fail"] > 0 or cascade["wedged"] > 0:
        overall_status = "degraded"
        errors.append(
            f"cascade fingerprints: pre_fail={cascade['pre_fail']} "
            f"wedged={cascade['wedged']}"
        )

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
        "ci_status": _ci_status_block(),
        # Observability blocks (Track 2.6) — additive, never break
        # existing consumers that don't read these keys.
        "path_table": path_table,
        "interfaces": interfaces,
        "cascade": cascade,
    }
