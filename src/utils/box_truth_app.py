"""The per-app half of the NOC read grant seam — MeshForge's values.

``utils/box_truth{,_reader,_grants,_center,_collect}.py`` are byte-identical
with MeshAnchor (``scripts/parity_check.py`` BYTE_IDENTICAL). This module is
the ONE file of the arc that differs per repo: both repos define the same
NAMES (pinned by ``tests/test_box_truth_app_seam.py``), each with its own
VALUES. A shared module never names an app; it asks this module.

Two kinds of value live here, and the difference matters:

* **this box's own app** — where its watchdog / mini / deployment state is,
  which units are its own, the export a grant written HERE forces, the
  center's key and targets file when this box is a center;
* **the target's app** — a center pushing a grant runs the TARGET's grant
  CLI, and a target may run either app (or both). ``GRANT_CLI_SEARCH`` is
  tried in order on the target: this app's install first, then the
  sibling's. Never a third directory.
"""
from __future__ import annotations

from pathlib import Path

APP = "meshforge"
INSTALL_DIR = "/opt/meshforge"
#: ``~/.config/<d>``, ``~/.local/share/<d>``, ``~/.local/state/<d>``.
CONFIG_DIRNAME = "meshforge"
#: The box-health watchdog's state file (absent on a box that runs none).
WATCHDOG_PATH = Path("/var/lib/meshforge/watchdog.json")
#: This app's own system units, for the export's ``services`` section.
UNIT_GLOB = "meshforge-*.service"
#: mini-dudeai's state file, relative to the operator's home.
MINI_STATE_RELPATH = "mini_dudeai_state.json"
#: A center's restricted read key: ``~/.ssh/<this>``. Per app, so a box
#: running both centers holds two keys and each target two grant lines.
NOC_KEY_BASENAME = "meshforge_noc_ed25519"
#: Where a center looks for the TARGET's grant CLI, in order.
GRANT_CLI_SEARCH = ("/opt/meshforge", "/opt/meshanchor")
#: The box judges its OWN role (session 3 step 2, 2026-10-14): the export's
#: ``role`` section resolves this app's declared role through this app's
#: provisioner + catalog and reports whether these two units are expected.
#: The unit whose absence removes this app's whole HTTP truth surface
#: (``/api/status`` + ``/fleet/slo``).
HTTP_SURFACE_UNIT = "meshforge-map"
#: The box-health watchdog unit a role declares enabled / disabled / absent.
#: ONE box organ for both apps (see WATCHDOG_PATH), so both seams name it.
WATCHDOG_UNIT = "meshforge-watchdog"
#: This app's role catalog, and the provisioner that resolves a role in it.
ROLE_CATALOG = Path(INSTALL_DIR) / "docs" / "fleet_roles.yaml"
PROVISIONER = Path(INSTALL_DIR) / "scripts" / "provision_role.py"


def app_version() -> str:
    try:
        from __version__ import __version__
        return str(__version__)
    except Exception:  # an export must answer even if the version is unreadable
        return "unknown"
