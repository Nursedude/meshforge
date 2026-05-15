"""Journalctl fetcher for the fleet dashboard `/fleet/logs` endpoint.

T1 of the fleet dashboard roadmap (see
``docs/fleet_dashboard_architecture.md``). Returns a recent slice of a
systemd unit's journal in a normalized shape — operator scans
ERROR/WARN across the fleet from the dashboard rather than
ssh-tailing per box.

Design notes
------------
- The HTTP handler owns the unit allowlist. This module trusts the
  unit name it's given and just runs journalctl. Caller validates.
- ``--output=short-iso`` is the most predictable parseable shape across
  systemd versions; ``-o json`` would be cleaner but inflates the
  response 5× with metadata most of which the panel never displays.
- Caching is intentional. A 60s in-process cache fronts journalctl so
  that 5 dashboards refreshing the same panel don't fork 5×N
  journalctl subprocesses per minute. Cache key includes priority + n
  + scope so different views don't collide.
- Sudo handling: for ``system`` scope we shell out to ``sudo -n
  /usr/bin/journalctl`` to read other-unit journals; the meshforge-map
  unit's sudoers drop-in must allowlist this. For ``user`` scope, no
  sudo — the daemon's own user systemd manager owns the user journal.

Inject ``XDG_RUNTIME_DIR`` for user-scope calls (same daemon-context
issue as ``fleet_snapshot._list_timers_scope`` — see commit 2dfca78).
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

# Cache: (unit, scope, n, priority) -> (timestamp, payload). 60s TTL.
_CACHE: Dict[tuple, tuple] = {}
_CACHE_TTL_S = 60.0

# journalctl short-iso line: "2026-05-15T07:31:02-1000 hostname unit[pid]: message"
# We only parse the timestamp and message; level comes from --priority filter,
# so we know it's at-least the requested level.
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{4}|Z)?)\s+"
    r"\S+\s+\S+\[\d+\]:\s*(?P<msg>.*)$"
)

# Priority level → upper-case display label.
_LEVEL_LABEL = {
    "emerg": "EMERG", "alert": "ALERT", "crit": "CRIT",
    "err": "ERROR", "warning": "WARN", "notice": "NOTICE",
    "info": "INFO", "debug": "DEBUG",
}


def _parse_iso_to_unix(iso: str) -> Optional[float]:
    """Parse short-iso journalctl timestamp into unix float. None on failure."""
    if not iso:
        return None
    try:
        # Python 3.11+ handles "2026-05-15T07:31:02-1000" but 3.9 needs
        # the timezone in ±HH:MM format. Fix the colon-less zone.
        if len(iso) >= 5 and iso[-5] in "+-" and iso[-3] != ":":
            iso = iso[:-2] + ":" + iso[-2:]
        # Python's fromisoformat handles "Z" only in 3.11+
        iso = iso.replace("Z", "+00:00")
        from datetime import datetime
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None


def fetch_unit_logs(
    *, unit: str, scope: str, n: int, priority: str,
) -> Dict[str, Any]:
    """Return recent journalctl lines for ``unit`` at ``priority`` (or higher).

    Always returns a dict shape — never raises on subprocess failure.

    Output:
        {
            "unit": str, "scope": str, "n": int, "priority": str,
            "host": str, "fetched_at": float, "from_cache": bool,
            "lines": [{"ts": float|None, "level": str, "msg": str}, ...],
            "error": str | None,
        }
    """
    key = (unit, scope, n, priority)
    cached = _CACHE.get(key)
    now = time.time()
    if cached and (now - cached[0]) < _CACHE_TTL_S:
        payload = dict(cached[1])
        payload["from_cache"] = True
        payload["fetched_at"] = cached[0]
        return payload

    cmd: List[str] = []
    env: Optional[Dict[str, str]] = None
    if scope == "user":
        # Read user-unit logs from the SYSTEM journal via the
        # `_SYSTEMD_USER_UNIT=<unit>.service` field match. Works
        # without sudo for any user in the `adm` or `systemd-journal`
        # group (default on Debian/Bookworm). Avoids the gotcha that
        # `journalctl --user -u <unit>` requires the user journal to
        # be persistent (`Storage=persistent`), which the fleet
        # doesn't enable — it's in-memory only and rotates with
        # systemd-user restart.
        cmd.extend([
            "journalctl",
            f"_SYSTEMD_USER_UNIT={unit}.service",
        ])
    else:
        # System scope: sudo (-n = non-interactive; daemon must have a
        # sudoers drop-in allowing journalctl with no password). Same
        # `adm` group covers `journalctl -u <system-unit>` without
        # sudo too — try the unprivileged path first.
        cmd.extend(["journalctl", "-u", unit])
    cmd.extend([
        "-n", str(n), "-p", priority,
        "--no-pager", "--output=short-iso",
    ])

    err_msg = None
    lines: List[Dict[str, Any]] = []
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=8, env=env,
        )
        if result.returncode != 0:
            err_msg = result.stderr.strip()[:200] or f"rc={result.returncode}"
        else:
            for raw in result.stdout.splitlines():
                raw = raw.strip()
                if not raw or raw.startswith("--"):
                    # Skip "-- Boot 12345 --" markers and empty lines.
                    continue
                m = _LINE_RE.match(raw)
                if m:
                    lines.append({
                        "ts": _parse_iso_to_unix(m.group("ts")),
                        "level": _LEVEL_LABEL.get(priority, priority.upper()),
                        "msg": m.group("msg"),
                    })
                else:
                    # Non-standard line (e.g. journalctl info). Surface
                    # raw so the operator can still see *something*
                    # rather than a silent drop.
                    lines.append({"ts": None, "level": "RAW", "msg": raw})
    except subprocess.TimeoutExpired:
        err_msg = "journalctl timed out (8s)"
    except (FileNotFoundError, OSError) as exc:
        err_msg = f"journalctl exec error: {exc}"

    payload = {
        "unit": unit,
        "scope": scope,
        "n": n,
        "priority": priority,
        "host": socket.gethostname(),
        "fetched_at": now,
        "from_cache": False,
        "lines": lines,
        "error": err_msg,
    }
    _CACHE[key] = (now, dict(payload))
    return payload
