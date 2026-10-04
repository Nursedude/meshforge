"""scripts/tui_smoke.py must draw the screens the operator SEES (2026-10-03).

Until this pin, the meshtasticd and rns submenus were rendered from the bare
registry section via the top-level builder: no handler-owned rows ("Service
Status", "RNS Status (rnstatus)"), no handler ordering — so "rns 13/13
painted" verified a screen that never appears, and three truth panes sitting
below the fold under the wrong header went unseen. The handlers now expose a
pure ``menu_choices()`` that their own loop AND the smoke driver both call.
Pure collection — no whiptail, no PTY — so this runs in CI.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src"), str(ROOT / "src" / "launcher_tui")]

import tui_smoke  # noqa: E402


def _screens():
    return {name: choices for name, _t, _s, choices in tui_smoke.collect_screens()}


def _labels(choices):
    return [d for _, d in choices]


def test_meshtasticd_screen_is_the_handlers_own_menu():
    labels = _labels(_screens()["meshtasticd"])
    assert "Service Status" in labels          # handler-owned row
    assert "--- Radio ---" in labels           # handler-owned header
    i = labels.index("--- Radio ---")
    assert labels[i + 1].startswith("TX Power Truth")


def test_rns_screen_is_the_handlers_own_menu():
    labels = _labels(_screens()["rns"])
    assert "RNS Status (rnstatus)" in labels
    assert labels[2].startswith("RNode Interference")


def test_every_registry_section_has_a_renderer():
    """Closed consumer: a new handler-built section must be taught here, or
    it is drawn as a bare-registry reconstruction (the defect above)."""
    sys.path.insert(0, str(ROOT / "src" / "launcher_tui"))
    import main as tui_main
    from handlers import get_all_handlers
    sections = {cls().menu_section for cls in get_all_handlers()} - {"main"}
    unrendered = sections - set(tui_main.SECTION_ORDERINGS) - set(tui_smoke.SUBMENU_BUILDERS)
    assert not unrendered, (
        f"sections with no renderer in tui_smoke: {sorted(unrendered)} — add "
        "the owning handler to SUBMENU_BUILDERS (it must expose menu_choices())")


def _drive_loop(handler, method, patches=()):
    shown = []
    handler.ctx = SimpleNamespace(
        registry=_registry(),
        dialog=SimpleNamespace(menu=lambda t, s, c, **k: (shown.append(list(c)), "back")[1]))
    ctxs = [patch(p) for p in patches]
    for c in ctxs:
        c.start()
    try:
        getattr(handler, method)()
    finally:
        for c in ctxs:
            c.stop()
    return shown[0]


def _registry():
    from handler_protocol import TUIContext
    from handler_registry import HandlerRegistry
    from handlers import get_all_handlers
    reg = HandlerRegistry(TUIContext(dialog=SimpleNamespace()))
    for cls in get_all_handlers():
        reg.register(cls())
    return reg


def test_meshtasticd_loop_renders_exactly_menu_choices():
    from handlers.meshtasticd_config import MeshtasticdConfigHandler
    h = MeshtasticdConfigHandler()
    shown = _drive_loop(h, "_meshtasticd_menu",
                        ["handlers.meshtasticd_config.ensure_meshtasticd_config"])
    assert shown == h.menu_choices()


def test_rns_loop_renders_exactly_menu_choices():
    from handlers.rns_menu import RNSMenuHandler
    h = RNSMenuHandler()
    shown = _drive_loop(h, "_rns_submenu")
    assert shown == h.menu_choices()


def test_meshtasticd_loop_still_dispatches_a_registry_row():
    """The full suite caught what the render-only tests above did not: the
    first cut of the menu_choices() extraction moved ``registry_tags`` out
    of the loop, so EVERY row crashed with NameError on selection."""
    from handlers.meshtasticd_config import MeshtasticdConfigHandler
    h = MeshtasticdConfigHandler()
    picks = iter(["txpower_truth", "back"])
    dispatched = []
    reg = _registry()
    reg.dispatch = lambda section, tag: dispatched.append((section, tag))
    h.ctx = SimpleNamespace(registry=reg, dialog=SimpleNamespace(
        menu=lambda t, s, c, **k: next(picks)))
    with patch("handlers.meshtasticd_config.ensure_meshtasticd_config"):
        h._meshtasticd_menu()
    assert dispatched == [("meshtasticd", "txpower_truth")]
