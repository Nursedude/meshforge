"""Fleet test runner — manual oneshot fires for allowlisted lab units.

T1.5 of the fleet dashboard roadmap. The operator clicks "Run tracer"
in the dashboard and this module shells out to ``systemctl
[--user] start <unit>``. Unit-allowlist enforcement lives in the
HTTP handler (`map_http_handler._FLEET_TESTS`); this module trusts
the unit name it gets.

Why we fire the SAME unit that the timer fires
----------------------------------------------
No separate test harness. The operator and the timer hit the exact
same code path, so a manual fire validates the production path. The
timer's `last_fire` advances; the schedule panel reflects the
manual fire too (which is correct — it WAS a fire).

XDG_RUNTIME_DIR injection is the same daemon-context fix as
``fleet_snapshot._list_timers_scope`` and the original ``fleet_logs``
(later removed; see ``a084cc6``). Here we still need it because
``systemctl --user start`` must talk to the user systemd manager
to enqueue the activation.
"""

from __future__ import annotations

import os
import pwd
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _find_operator_user() -> Optional[Tuple[int, str]]:
    """Find an operator user with a live systemd --user bus.

    Scans ``/run/user/`` for non-root UID directories that have a
    ``bus`` socket (i.e. ``systemctl --user`` is reachable for that
    UID). Returns ``(uid, username)`` for the smallest matching UID,
    or ``None`` if no candidate is found.

    Used when this process runs as root and needs to fire a user-scope
    unit on the operator's behalf (the meshanchor-server case — see
    ``project_ma_runtest_user_mismatch``). Root has no ``/run/user/0/bus``
    by default, so the daemon must drop privilege to whichever
    operator UID owns the user manager.
    """
    run_user = Path("/run/user")
    try:
        entries = list(run_user.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return None
    candidates: list[int] = []
    for entry in entries:
        try:
            uid = int(entry.name)
        except ValueError:
            continue
        if uid == 0:
            continue
        if (entry / "bus").exists():
            candidates.append(uid)
    if not candidates:
        return None
    uid = min(candidates)
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        return None
    return uid, name


def fire_unit(*, unit: str, scope: str) -> Dict[str, Any]:
    """Run `systemctl [--user] start <unit>` and return the result.

    Returns a stable shape regardless of subprocess outcome:
        {
            "ok": bool,
            "unit": str, "scope": str,
            "started_at_unix": float,
            "stderr": str | None,
            "error": str | None,
        }

    Note: ``systemctl start`` returns BEFORE the unit finishes for
    oneshot Type=oneshot units (which all our tests are). The
    operator should refresh the Logs / Schedules panel to see the
    fire complete.

    Root-firing-operator case: when this process runs as root (e.g.
    meshanchor-map.service with ``User=root``) and ``scope == "user"``,
    we drop privilege via ``sudo -n -u <operator>`` so the call lands
    on the operator's user systemd bus instead of root's nonexistent
    one. Requires a sudoers entry allowing
    ``/usr/bin/sudo -n -u <op> /usr/bin/env XDG_RUNTIME_DIR=* /usr/bin/systemctl --user --no-block start *``.
    """
    cmd: list[str]
    env: Optional[Dict[str, str]] = None
    started_at = time.time()

    if scope == "user" and os.geteuid() == 0:
        op = _find_operator_user()
        if op is None:
            return {
                "ok": False, "unit": unit, "scope": scope,
                "started_at_unix": started_at,
                "stderr": None,
                "error": (
                    "running as root and no operator user with a live "
                    "/run/user/<uid>/bus was found — cannot reach a "
                    "user systemd manager"
                ),
            }
        op_uid, op_name = op
        # --no-block: enqueue the start and return immediately. See
        # rationale in the same-scope branch below.
        cmd = [
            "sudo", "-n", "-u", op_name,
            "env", f"XDG_RUNTIME_DIR=/run/user/{op_uid}",
            "systemctl", "--user", "--no-block", "start", unit,
        ]
    else:
        cmd = ["systemctl"]
        if scope == "user":
            cmd.append("--user")
            if "XDG_RUNTIME_DIR" not in os.environ:
                env = os.environ.copy()
                env["XDG_RUNTIME_DIR"] = f"/run/user/{os.geteuid()}"
        # --no-block: enqueue the start and return immediately. Without
        # this, `systemctl start <oneshot>` blocks until the unit
        # finishes, which for tracer is ~10-15s (PING all peers + ACK
        # wait). The HTTP request would then time out before returning
        # to the dashboard. The operator can refresh the Logs / Schedules
        # panel to see the fire complete.
        cmd.extend(["--no-block", "start", unit])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "unit": unit, "scope": scope,
            "started_at_unix": started_at,
            "stderr": None, "error": "systemctl start timed out (10s)",
        }
    except (FileNotFoundError, OSError) as exc:
        return {
            "ok": False, "unit": unit, "scope": scope,
            "started_at_unix": started_at,
            "stderr": None, "error": f"systemctl exec error: {exc}",
        }

    ok = (result.returncode == 0)
    return {
        "ok": ok,
        "unit": unit,
        "scope": scope,
        "started_at_unix": started_at,
        "stderr": result.stderr.strip()[:400] if result.stderr else None,
        "error": None if ok else f"systemctl returned {result.returncode}",
    }
