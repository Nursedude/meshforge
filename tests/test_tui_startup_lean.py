"""TUI startup leanness gate (2026-07-13 perf arc, commit 5aeebcc6).

Importing + instantiating every TUI handler must NOT pull the heavy
dependency stacks (RNS/LXMF/meshtastic/folium/numpy/PIL/qrcode) — those
load at menu time via deferred imports. Before the arc, module-level
imports dragged ~1,500 modules (~1 s on a Pi 5, worse on Pi 4) into every
TUI launch for menus the session might never open.

This is the executable form of that work: a regression — any handler (or
anything in its import chain, including the gateway/monitoring/commands
package __init__s, which are PEP 562 lazy) re-importing a heavy stack at
module level — fails HERE, in the same commit, on any model driving the
session (model-agnostic-harness principle).

The check runs in a FRESH interpreter: the test process itself may already
have heavy modules loaded, which would mask the regression.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Stacks that must stay out of TUI startup. Deliberately top-level package
# names: any submodule import loads the package root too.
FORBIDDEN_AT_STARTUP = [
    "RNS", "LXMF", "meshtastic", "folium", "numpy", "PIL", "qrcode",
]

_PROBE = """
import json
import sys

sys.path.insert(0, {src!r})
sys.path.insert(0, {launcher!r})

from handlers import get_all_handlers
instances = [cls() for cls in get_all_handlers()]

forbidden = {forbidden!r}
loaded = sorted(
    m for m in forbidden
    if m in sys.modules and sys.modules[m] is not None
)
print(json.dumps({{"handlers": len(instances), "loaded": loaded}}))
"""


def test_handler_startup_loads_no_heavy_stacks():
    """Fresh interpreter: import + instantiate all handlers, then assert
    none of the heavy stacks appeared in sys.modules."""
    probe = _PROBE.format(
        src=str(REPO_ROOT / "src"),
        launcher=str(REPO_ROOT / "src" / "launcher_tui"),
        forbidden=FORBIDDEN_AT_STARTUP,
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, (
        f"handler import/instantiate failed in a fresh interpreter:\n"
        f"{result.stderr[-2000:]}"
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["handlers"] > 0, "no handlers registered — probe is broken"
    assert payload["loaded"] == [], (
        f"heavy stacks loaded at TUI startup: {payload['loaded']} — a "
        f"module-level import crept back in. Defer it to menu time "
        f"(see the 2026-07-13 perf arc pattern: _load_x() helper or "
        f"function-local import with an honest ImportError dialog)."
    )
