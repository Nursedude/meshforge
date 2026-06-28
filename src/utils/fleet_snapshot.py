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
            "healthy": bool,           # all TIMERS nominal? (timer-only)
            "stale_count": int,        # how many timers in red
            "reason": <str>?,          # present only on timer-probe FAILURE (M3)
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
            # Additive scheduled-work sub-sources (Phase-1 fleet visibility).
            # Each is independently honest-signalled (available/reason) and
            # does NOT affect the timer-only `healthy` above.
            "crontab": {"available": bool, "reason": <str>?,
                        "jobs": [{"schedule": str, "command": str}], "count": int},
            "verdicts": {"available": bool, "reason": <str>?,
                         "jobs": [{"name", "status", "ts_iso", "age_s",
                                   "stale", "message"}],
                         "fail_count": int, "concern_count": int},
            "loop_crons": {"available": bool, "reason": <str>?, "ephemeral": True,
                           "jobs": [{"id", "cron", "prompt", "next_fire_unix"}]},
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
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

REQUIRED_SERVICES = ("meshtasticd", "mosquitto")
OPTIONAL_SERVICES = (
    "rnsd",
    "meshforge",
    "meshforge-map",
    "meshforge-maps",
)

# TTL for cached `systemctl is-active` results in seconds. MA polls
# /fleet/slo every 5–15s; a 2.0s TTL coalesces overlapping handlers
# (dashboard tick + rollup tick that fire within the same window)
# without showing the operator stale service state. Driven by
# `project_fleet_monitor_reliability_assessment.md` finding #6 —
# "audit subprocess.run holding the GIL." Field-observed: moc2
# /fleet/slo at 2.43s right against MA's 3s peer-fetch timeout
# (2026-05-17), with all six service probes serial on Pi-class hardware.
_SYSTEMCTL_STATE_TTL_S = 2.0
# Parallel-fanout worker pool size for service probes. Matches the
# number of services we probe (REQUIRED + OPTIONAL = 6). Each worker
# blocks on its own systemctl fork; the GIL is released during the
# wait so concurrency is real. Cap at 6 even if we add services so
# we never fork more than one process per service.
_SERVICE_PROBE_MAX_WORKERS = 6

_systemctl_state_cache: Dict[str, Tuple[str, float]] = {}
_systemctl_state_cache_lock = threading.Lock()

# Timer unit prefixes we surface in the schedules block. System timers
# like apt-daily / man-db belong to the OS, not the fleet — they would
# be noise. Add a prefix here when a new fleet timer ships.
SCHEDULE_UNIT_PREFIXES = ("meshforge", "meshanchor", "moc-")

# Stale heuristic: timer flagged red when its last fire is older than
# this multiplier × the timer's nominal interval. The interval is
# derived from the gap between successive fires when available;
# otherwise the heuristic falls back to "next_fire is None" only.
SCHEDULE_STALE_MULTIPLIER = 2.0
# A timer with NEXT unset is only stale if it ALSO has no recent fire.
# systemd momentarily reports NEXT=0 at the fire instant while it
# recomputes the next elapse (notably monotonic OnUnitActiveSec timers);
# such a timer just ran and must not flicker the Subsystem Health banner.
SCHEDULE_NO_NEXT_GRACE_S = 3600.0


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


def _systemctl_state_uncached(unit: str) -> str:
    """Return the systemd unit state from a fresh `systemctl is-active` call.

    Maps to MA's vocabulary:

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


def _systemctl_state(unit: str, ttl_s: float = _SYSTEMCTL_STATE_TTL_S) -> str:
    """TTL-cached wrapper around `_systemctl_state_uncached`.

    The cache is module-level + lock-protected so concurrent /fleet/slo
    and /api/status handlers share the same result within the TTL window.
    `ttl_s <= 0` skips the cache entirely (used by tests that want a
    deterministic fresh call).
    """
    if ttl_s <= 0:
        return _systemctl_state_uncached(unit)
    now = time.monotonic()
    with _systemctl_state_cache_lock:
        cached = _systemctl_state_cache.get(unit)
        if cached is not None and (now - cached[1]) < ttl_s:
            return cached[0]
    # Fork/wait happens outside the lock so concurrent callers for
    # *different* units don't serialize on the cache lock.
    state = _systemctl_state_uncached(unit)
    with _systemctl_state_cache_lock:
        _systemctl_state_cache[unit] = (state, now)
    return state


def _probe_services_parallel(units: Tuple[str, ...]) -> Dict[str, str]:
    """Probe `_systemctl_state` for every unit concurrently.

    On Pi-class hardware each `systemctl is-active` costs ~300–400 ms
    of wall time (subprocess fork + systemd RPC). Six serial calls
    pushed `/fleet/slo` to 2.43 s — within 19% of MA's 3 s peer-fetch
    timeout, so any contention tipped it over to "peer fetch:
    timeout: timed out" and the host dropped out of the rollup.

    Fanning out keeps total wall time at max(unit_cost) ≈ 400 ms
    regardless of unit count. Bounded at `_SERVICE_PROBE_MAX_WORKERS`
    workers (= one per service) so a future service addition can't
    fork an unbounded pool.
    """
    if not units:
        return {}
    n_workers = min(len(units), _SERVICE_PROBE_MAX_WORKERS)
    with ThreadPoolExecutor(
        max_workers=n_workers, thread_name_prefix="fleet-slo-probe"
    ) as ex:
        futures = {ex.submit(_systemctl_state, u): u for u in units}
        out: Dict[str, str] = {}
        for fut in futures:
            unit = futures[fut]
            try:
                out[unit] = fut.result()
            except Exception:
                # _systemctl_state already swallows its own errors and
                # returns "not_running"; this branch defends against a
                # future refactor that lets exceptions escape.
                out[unit] = "not_running"
    return out


def _services_rollup() -> Dict[str, Any]:
    """Roll required + optional services into the MA shape."""
    all_states = _probe_services_parallel(REQUIRED_SERVICES + OPTIONAL_SERVICES)
    req_states = {svc: all_states[svc] for svc in REQUIRED_SERVICES}
    opt_states = {svc: all_states[svc] for svc in OPTIONAL_SERVICES}

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

    # #17 single-consumer defer: when meshforge-gateway owns the local :4403
    # PhoneAPI, a connect_ex() probe here is itself a contender — meshtasticd
    # accepts it as a PhoneAPI client and force-closes the gateway's stream
    # ("Force close previous TCP connection"). This snapshot is rebuilt on every
    # status poll (~15s), so on a gateway box the probe produced sustained
    # force-close churn that tripped probe_meshtasticd_phoneapi_wedge (the
    # 2026-06-27 moc finding — the gateway itself never touches :4403; it uses
    # MQTT + HTTP-toradio). Report the radio from meshtasticd's service state
    # instead — non-contending, and honest: a down daemon still reads False.
    if _systemctl_state("meshforge-gateway") == "available":
        return {
            "connected": _systemctl_state("meshtasticd") == "available",
            "name": "meshtasticd",
            "preset": None,
            "battery_pct": None,
        }

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


def _list_timers_scope(scope: str) -> Optional[List[Dict[str, Any]]]:
    """Return systemctl timers in the given scope, or None if the probe FAILED.

    Uses ``systemctl [--user] list-timers --all --output=json`` —
    available on systemd 247+ (Bookworm and later). **None (probe failed) is
    deliberately distinct from [] (ran OK, no timers)** so a wedged systemctl
    can't read as a clean box downstream (honest-signal M3, parity with
    MeshAnchor's fleet_aggregator). A host without a user session contributes
    its system timers; a no-operator root drop returns [] (nothing to read,
    not a failure).

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
        if result.returncode != 0:
            # Probe FAILED (systemctl errored) — distinct from "ran OK, no
            # timers". Returning [] for both made a wedged probe read as a
            # clean box (honest-signal M3 — parity with MeshAnchor).
            return None
        if not result.stdout.strip():
            return []
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError,
            json.JSONDecodeError):
        return None


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

    # Stale signature 1: NEXT is unset AND there is no recent fire to
    # vouch for the timer. moc1's wedged tracer.timer (2026-05-14 12:30
    # → 2026-05-15 06:48 HST) sat here ~18h with NEXT unset and a stale
    # `last` — genuinely dead. But systemd ALSO momentarily reports
    # NEXT=0 at the fire instant while it recomputes the next elapse
    # (notably monotonic OnUnitActiveSec timers), and that timer's
    # `last` is ~now — it just ran. Gate the missing-NEXT signal on a
    # recent fire so a healthy timer doesn't flicker the banner stale.
    if next_unix is None:
        stale = age_s is None or age_s > SCHEDULE_NO_NEXT_GRACE_S
    # Stale signature 2: last fire is older than 2× the nominal
    # interval (interval ≈ next - last). For timers with no last_unix
    # yet (boxes that just booted) skip — they're not stale, just
    # fresh. Negative age (clock skew on a just-fired timer) is never
    # stale — the comparison handles it.
    elif last_unix is not None:
        interval = next_unix - last_unix
        stale = bool(interval > 0 and age_s is not None
                     and age_s > SCHEDULE_STALE_MULTIPLIER * interval)
    else:
        stale = False

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


# ─── Phase-1 fleet visibility: crontab + cron verdicts + loop crons ──────
#
# Additive scheduled-work sources beyond systemd timers. Each follows the
# honest-signal contract (``available``/``reason``) so a read FAILURE renders
# "unavailable" on the dashboard, never a clean "nothing scheduled". The
# operator asked for truthful reporting; silence-as-health is the bug.
CRONTAB_PROBE_TIMEOUT_S = 5
VERDICT_LOG_FILE = "cron_verdicts.log"
VERDICT_STALE_AFTER_S = 26 * 3600  # > daily cadence; older = stale
LOOP_CRONS_FILE = ".claude_loop_crons.json"


def _parse_crontab(text: str) -> List[Dict[str, str]]:
    """Parse ``crontab -l`` output into ``[{schedule, command}]``.

    Skips blank lines, ``#`` comments, and environment-assignment lines
    (``MAILTO=``/``PATH=``/``FOO=bar`` — a first token containing ``=``).
    A normal entry is 5 schedule fields + command, or an ``@keyword``
    (e.g. ``@daily``) + command. Malformed short lines are dropped.
    """
    jobs: List[Dict[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        first = s.split(None, 1)[0]
        if "=" in first and not first.startswith("@"):
            continue  # env assignment, not a job
        if s.startswith("@"):
            parts = s.split(None, 1)
            schedule = parts[0]
            command = parts[1] if len(parts) > 1 else ""
        else:
            parts = s.split(None, 5)
            if len(parts) < 6:
                continue  # need 5 schedule fields + a command
            schedule = " ".join(parts[:5])
            command = parts[5]
        jobs.append({"schedule": schedule, "command": command})
    return jobs


def _read_crontab() -> Dict[str, Any]:
    """Read the operator's crontab. Honest-signal contract:

    - ``crontab -l`` rc=0              -> ``{available:True, jobs:[...], count}``
    - rc!=0 + stderr 'no crontab for'  -> ``{available:True, jobs:[], count:0}`` (genuinely empty)
    - rc!=0 other / missing bin / timeout / OSError
                                       -> ``{available:False, reason:...}``

    A failed read must NEVER render as "no cron jobs". List argv (MF002),
    bounded timeout (MF004), root→operator drop mirrors ``_list_timers_scope``.
    """
    if os.geteuid() == 0:
        try:
            from utils.fleet_test_runner import _find_operator_user
        except ImportError:
            return {"available": False, "reason": "no_operator"}
        op = _find_operator_user()
        if op is None:
            return {"available": False, "reason": "no_operator"}
        _, op_name = op
        cmd = ["sudo", "-n", "-u", op_name, "crontab", "-l"]
    else:
        cmd = ["crontab", "-l"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=CRONTAB_PROBE_TIMEOUT_S)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return {"available": False,
                "reason": f"probe_error: {e.__class__.__name__}"}
    if r.returncode != 0:
        stderr = (r.stderr or "").strip()
        if "no crontab for" in stderr.lower():
            return {"available": True, "jobs": [], "count": 0}
        reason = stderr.splitlines()[0] if stderr else f"exit_{r.returncode}"
        return {"available": False,
                "reason": f"crontab unavailable ({reason[:80]})"}
    jobs = _parse_crontab(r.stdout)
    return {"available": True, "jobs": jobs, "count": len(jobs)}


def _parse_cron_verdicts(text: str, now_unix: float) -> List[Dict[str, Any]]:
    """Parse ``~/cron_verdicts.log`` -> last verdict per job name.

    Line format: ``<ISO8601> <name> <STATUS> <message...>``. Last line per
    name wins. Garbage/short lines skipped. Computes ``age_s`` from the ts.
    A FAIL/CONCERN verdict is truthful red DATA (available stays True),
    distinct from "can't read the log".
    """
    from datetime import datetime
    latest: "Dict[str, Dict[str, Any]]" = {}
    for line in text.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        ts_iso, name, status = parts[0], parts[1], parts[2]
        message = parts[3] if len(parts) > 3 else ""
        try:
            ts_unix = datetime.fromisoformat(
                ts_iso.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        age = now_unix - ts_unix
        latest[name] = {
            "name": name,
            "status": status,
            "ts_iso": ts_iso,
            "age_s": round(age, 1),
            "stale": age > VERDICT_STALE_AFTER_S,
            "message": message.strip(),
        }
    return sorted(latest.values(), key=lambda v: v["name"])


# Matches the verdict-emitting call in a crontab command:
#   <job> >/dev/null 2>&1 ; /opt/meshforge/scripts/cron_verdict.sh <name> $?
# The captured <name> is the cron the verdict belongs to.
_VERDICT_CALL_RE = re.compile(r"cron_verdict\.sh\s+(\S+)")


def _verdict_names_in_command(command: str) -> List[str]:
    """All cron names a crontab command WIRES via ``cron_verdict.sh``.

    A single command may chain several verdict calls (``jobA; cron_verdict.sh a
    $?; jobB; cron_verdict.sh b $?``) — ALL are wired (``finditer``, not
    ``search``: a second wired cron is still a real cron). This is the SSOT for
    "which crons are wired", shared by this module's orphan filter and Issue
    #78's ``probe_cron_verdict_stale`` so the two can never drift (one regex,
    one extractor — honest_failure_modes #5).
    """
    return [m.group(1) for m in _VERDICT_CALL_RE.finditer(command or "")]


def _wired_verdict_names(crontab_block: Dict[str, Any]) -> Optional[set]:
    """Cron names currently WIRED to ``cron_verdict.sh`` in the live crontab.

    A verdict whose name is NOT in this set is an ORPHAN candidate — but note
    that "wired" only sees names on a crontab COMMAND line. A script that emits
    a *second* verdict from inside its body (e.g. ``mf5_soak_watch.sh`` emits
    ``mf5_soak_verdict`` — the final soak PASS/FAIL) is active yet unwired, as
    is a verdict from a non-user-crontab emitter. So the caller drops an orphan
    only when it is ALSO stale: a parked/removed cron leaves a STALE verdict,
    while a FRESH unwired verdict is a live signal that must not be hidden.
    Mirrors Issue #78's ``cron_verdict_stale``, which judges only wired crons.

    Returns ``None`` when the crontab is unavailable — the caller must then NOT
    filter (we can't prove a verdict is orphan if we can't read the crontab;
    absence of evidence ≠ orphan, honest_failure_modes #2).
    """
    if not crontab_block.get("available"):
        return None
    names: set = set()
    for job in crontab_block.get("jobs", []):
        names.update(_verdict_names_in_command(job.get("command", "")))
    return names


def _read_cron_verdicts(wired_names: Optional[set] = None) -> Dict[str, Any]:
    """Read ``~/cron_verdicts.log`` (the silent-cron detection log).

    Missing file / unreadable -> ``available:False`` + reason; present ->
    parsed (a failing job is truthful red data, still ``available:True``).
    """
    home = _operator_home()
    if home is None:
        return {"available": False, "reason": "no_operator_home"}
    path = home / VERDICT_LOG_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"available": False, "reason": "no_file"}
    except (PermissionError, OSError) as e:
        return {"available": False,
                "reason": f"read_error: {e.__class__.__name__}"}
    jobs = _parse_cron_verdicts(text, time.time())
    orphan_filtered = 0
    orphan_dropped: List[Dict[str, str]] = []
    if wired_names is not None:
        # Drop a verdict only when it is BOTH unwired AND stale. A parked/
        # removed cron leaves a STALE verdict (the #78 dead-cron lesson); it
        # should not linger in the fleet view as a false CONCERN/FAIL. But an
        # unwired verdict that is still FRESH is a LIVE signal — a secondary
        # verdict emitted from inside a wrapper (e.g. mf5_soak_watch.sh's
        # mf5_soak_verdict, the final soak PASS/FAIL) or a non-user-crontab
        # emitter. Dropping a fresh orphan would bury a live FAIL
        # (honest_failure_modes #2: absence-of-wiring ≠ inactive). When
        # wired_names is None (crontab unreadable) we keep everything.
        kept: List[Dict[str, Any]] = []
        for j in jobs:
            if j["name"] in wired_names or not j.get("stale", False):
                kept.append(j)
            else:
                # ITEMIZED witness (honest_failure_modes #9): a dropped verdict
                # is necessarily stale (a dead cron), but record its name AND
                # status so a dropped stale FAIL/CONCERN stays VISIBLE here
                # instead of being folded into a bare count that reads clean.
                orphan_dropped.append({"name": j["name"], "status": j["status"]})
        orphan_filtered = len(orphan_dropped)
        jobs = kept
    return {
        "available": True,
        "jobs": jobs,
        "fail_count": sum(1 for j in jobs
                          if j["status"].upper().startswith("FAIL")),
        "concern_count": sum(1 for j in jobs
                             if j["status"].upper() == "CONCERN"),
        "orphan_filtered": orphan_filtered,
        "orphan_dropped": orphan_dropped,
    }


def _read_loop_crons() -> Dict[str, Any]:
    """Read ``~/.claude_loop_crons.json`` (ephemeral Claude /loop crons).

    These are SESSION-ONLY — they vanish when the Claude session ends, so
    ``no_file`` is the NORMAL state on virtually every box (rendered muted,
    not an error). ``ephemeral:True`` is always set so the web labels it.
    """
    home = _operator_home()
    if home is None:
        return {"available": False, "reason": "no_operator_home",
                "ephemeral": True}
    path = home / LOOP_CRONS_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"available": False, "reason": "no_file", "ephemeral": True}
    except (PermissionError, OSError) as e:
        return {"available": False,
                "reason": f"read_error: {e.__class__.__name__}",
                "ephemeral": True}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {"available": False, "reason": "malformed_json",
                "ephemeral": True}
    raw_jobs = (data if isinstance(data, list)
                else data.get("jobs", []) if isinstance(data, dict) else [])
    jobs: List[Dict[str, Any]] = []
    for j in raw_jobs:
        if not isinstance(j, dict):
            continue
        jobs.append({
            "id": str(j.get("id", "")),
            "cron": str(j.get("cron", "")),
            "prompt": str(j.get("prompt", ""))[:200],
            "next_fire_unix": j.get("next_fire_unix"),
        })
    return {"available": True, "ephemeral": True, "jobs": jobs}


def _schedules_block() -> Dict[str, Any]:
    """Build the schedules block for the SLO snapshot.

    Surfaces fleet-relevant timers (prefix match against
    SCHEDULE_UNIT_PREFIXES) PLUS three additive scheduled-work sub-sources
    (``crontab``/``verdicts``/``loop_crons``), each honest-signalled with
    its own ``available``/``reason``. Empty units list with healthy=true is
    the legitimate "host runs no fleet timers" state. A failed timer-scope
    probe returns ``healthy:False`` + ``reason`` (M3) — and the sub-sources
    never flip the timer-only ``healthy`` (a broken crontab read is its own
    "unavailable", not a timer fault).
    """
    now = time.time()
    units: List[Dict[str, Any]] = []
    failed_scopes: List[str] = []
    for scope in ("system", "user"):
        raw_timers = _list_timers_scope(scope)
        if raw_timers is None:
            failed_scopes.append(scope)
            continue
        for raw in raw_timers:
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
    if failed_scopes:
        # A failed timer-state probe must not read as "all healthy" — an
        # empty unit list from a wedged systemctl is otherwise
        # indistinguishable from a genuinely-clean box (honest-signal M3).
        block: Dict[str, Any] = {
            "healthy": False,
            "stale_count": stale_count,
            "units": units,
            "reason": ("timer state unavailable ("
                       + ", ".join(failed_scopes) + " scope probe failed)"),
        }
    else:
        block = {
            "healthy": stale_count == 0,
            "stale_count": stale_count,
            "units": units,
        }
    # Additive sub-sources — each independently honest-signalled and
    # guarded so one failing source can never blank the block. They do
    # NOT affect the timer-only ``healthy`` above; the web aggregates each
    # sub-block's availability into the card.
    try:
        block["crontab"] = _read_crontab()
    except Exception as e:  # never let a source break the snapshot
        block["crontab"] = {"available": False,
                            "reason": f"block_error: {e.__class__.__name__}"}
    try:
        # Pass the live crontab's wired cron names so verdicts for parked/
        # removed (orphan) crons are dropped from the fleet view rather than
        # lingering as false CONCERN/FAIL (#78 orphan-ignore, for the display).
        block["verdicts"] = _read_cron_verdicts(
            wired_names=_wired_verdict_names(block.get("crontab", {}))
        )
    except Exception as e:
        block["verdicts"] = {"available": False,
                             "reason": f"block_error: {e.__class__.__name__}"}
    try:
        block["loop_crons"] = _read_loop_crons()
    except Exception as e:
        block["loop_crons"] = {"available": False, "ephemeral": True,
                               "reason": f"block_error: {e.__class__.__name__}"}
    return block


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

    # Watchdog block (Phase 1 reliability layer — Issue stack #58–#69).
    # Same JSON the watchdog writes to /var/lib/meshforge/watchdog.json
    # rides /fleet/slo so MA's /fleet/rollup carries cross-box wedge
    # signals without new HTTP plumbing.
    watchdog = _watchdog_block()
    if watchdog.get("installed") and not watchdog.get("ok", True):
        overall_status = "degraded"
        wedge_signals = [
            s for s in watchdog.get("signals") or []
            if s.get("severity") == "wedge"
        ]
        if wedge_signals:
            errors.append(
                f"watchdog wedge signals: "
                + ", ".join(
                    f"{s.get('class')}={s.get('subject')}"
                    for s in wedge_signals[:5]
                )
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
        "watchdog": watchdog,
    }


# ─────────────────────────────────────────────────────────────────────
# Watchdog passthrough (Phase 1 reliability layer)
# ─────────────────────────────────────────────────────────────────────

_WATCHDOG_STATE_PATH = "/var/lib/meshforge/watchdog.json"
_WATCHDOG_STALE_S = 300.0


def _watchdog_block() -> Dict[str, Any]:
    """Read /var/lib/meshforge/watchdog.json into the SLO snapshot.

    Same shape as ``_serve_status``'s ``_read_watchdog_block`` so
    consumers see one schema regardless of which endpoint they poll.
    Degrades silently to ``{"installed": False}`` on boxes where the
    watchdog hasn't been enabled yet.
    """
    import json
    from pathlib import Path

    p = Path(_WATCHDOG_STATE_PATH)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"installed": False, "reason": "no_state_file"}
    except OSError as exc:
        return {"installed": False, "reason": f"read_error: {exc}"}

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"installed": True, "ok": False,
                "reason": f"malformed_json: {exc}"}

    if not isinstance(payload, dict):
        return {"installed": True, "ok": False,
                "reason": "malformed_json: not an object"}

    ts = payload.get("ts")
    age_s = None
    if isinstance(ts, (int, float)):
        age_s = max(0.0, time.time() - float(ts))

    stale = bool(age_s is not None and age_s > _WATCHDOG_STALE_S)
    block = {
        "installed": True,
        "ok": bool(payload.get("ok", True)) and not stale,
        "ts": ts,
        "age_s": age_s,
        "probe_count": payload.get("probe_count"),
        "signals": payload.get("signals", []),
    }
    if stale:
        block["reason"] = (
            f"stale: last write {age_s:.0f}s ago "
            f"(threshold {_WATCHDOG_STALE_S:.0f}s) — watchdog daemon "
            f"may have crashed"
        )
    return block
