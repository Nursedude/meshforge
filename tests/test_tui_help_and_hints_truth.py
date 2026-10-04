"""In-app navigation text must name menus that EXIST (TUI audit 2026-10-03).

The Help screen said "1-6 Quick access to main sections" while the main menu
had grown to 1-8 plus n/t; the dashboard's remediation hints named rows that
had been renamed ("Port Listening", "Broker Profiles" under MQTT, "Gateway
Bridge > Configure"). Both are hand-typed copies of the menu, so both are
pinned here to the live source of truth.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))


TUI = Path(__file__).resolve().parent.parent / "src" / "launcher_tui"


def _help_text():
    import inspect
    from handlers.about import AboutHandler
    return inspect.getsource(AboutHandler._show_help)


def test_help_names_every_main_menu_key():
    # imported here, not at module level, so pytest does not collect the
    # pinned class a second time under this file
    sys.path.insert(0, os.path.dirname(__file__))
    from test_menu_orderings import TestMainMenuPin
    text = _help_text()
    keys = [tag for tag, _ in TestMainMenuPin.EXPECTED]
    digits = sorted(int(k) for k in keys if k.isdigit())
    assert f"1-{digits[-1]}" in text, f"help must cover sections 1-{digits[-1]}"
    for k in (k for k in keys if not k.isdigit()):
        assert re.search(rf"^\s+{re.escape(k)}\s{{2,}}\S", text, re.M), (
            f"help screen omits main-menu key {k!r}")


def _all_labels():
    """Every quoted menu-row label start in the TUI source."""
    src = "\n".join(p.read_text() for p in TUI.rglob("*.py"))
    return set(re.findall(r'\(\s*"[^"]+",\s*f?"([^"\[{]+?)(?:\s{2,}|"|\[|\{)', src))


def test_every_remediation_hint_names_real_rows():
    """Each segment must be a row label that EXISTS somewhere in the TUI.

    ⚠️ Existence, not parentage: a hint naming a real row under the WRONG
    parent still passes (measured at write time — "MQTT > Broker Profiles"
    and "Gateway Bridge > Configure" did). Proving the path needs a submenu
    walk; this catches renamed/removed rows, which is how these drifted.
    """
    from handlers.dashboard import DashboardHandler
    labels = {l.strip() for l in _all_labels()}
    bad = []
    for key, path in DashboardHandler._REMEDIATION_HINTS.items():
        for seg in [s.strip() for s in path.split(">")][1:]:
            # a label may carry a parenthetical, e.g. "Listening Ports (ss -tlnp)"
            if not any(l == seg or l.startswith(seg + " ") for l in labels):
                bad.append(f"{key}: {seg!r} (in {path!r})")
    assert not bad, "remediation hints name rows that do not exist:\n  " + "\n  ".join(bad)


def test_wizard_does_not_point_at_a_missing_service_manager():
    assert "Use Service Manager from the main menu" not in (TUI / "handlers" / "first_run.py").read_text()
