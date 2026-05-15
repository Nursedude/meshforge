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
import subprocess
import time
from typing import Any, Dict


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
    """
    cmd = ["systemctl"]
    env = None
    if scope == "user":
        cmd.append("--user")
        if "XDG_RUNTIME_DIR" not in os.environ:
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.geteuid()}"
    cmd.extend(["start", unit])

    started_at = time.time()
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
