"""SECTION_ORDERINGS must track the live registry — Q5 (audit W8).

Before this pin, 23 tags across 5 sections had drifted out of the inline
ordering lists: new handlers rendered as an unordered tail after the
curated entries. Now a new menu action FAILS this test until its author
decides where it belongs in the menu.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

import main as tui_main
from handlers import get_all_handlers

# Cross-section legacy entries (surviving Q1) that appear in a menu whose
# registry section does not own the tag.
CROSS_SECTION = {
    'dashboard': {'network'},
    'configuration': {'rns-config'},
    'extensions': {'mfmaps'},
}


def _registry_sections():
    sections = {}
    for cls in get_all_handlers():
        h = cls()
        for item in h.menu_items():
            sections.setdefault(h.menu_section, set()).add(item[0])
    return sections


class TestOrderingsTrackRegistry:
    def test_every_registry_tag_is_ordered(self):
        sections = _registry_sections()
        problems = []
        for section, ordering in tui_main.SECTION_ORDERINGS.items():
            missing = sections.get(section, set()) - set(ordering)
            if missing:
                problems.append(f"{section}: missing {sorted(missing)}")
        assert not problems, (
            "registry tags absent from SECTION_ORDERINGS — new menu "
            "actions must be placed deliberately, not rendered as an "
            f"unordered tail (audit W8): {problems}"
        )

    def test_no_stale_ordering_entries(self):
        sections = _registry_sections()
        problems = []
        for section, ordering in tui_main.SECTION_ORDERINGS.items():
            allowed = sections.get(section, set()) | CROSS_SECTION.get(section, set())
            stale = set(ordering) - allowed
            if stale:
                problems.append(f"{section}: stale {sorted(stale)}")
        assert not problems, (
            f"SECTION_ORDERINGS entries with no live tag behind them: {problems}"
        )

    def test_no_duplicates_within_a_section(self):
        for section, ordering in tui_main.SECTION_ORDERINGS.items():
            assert len(ordering) == len(set(ordering)), (
                f"duplicate entries in {section} ordering"
            )

    def test_back_is_never_ordered(self):
        # 'back' is appended by _build_section_menu, always last.
        for section, ordering in tui_main.SECTION_ORDERINGS.items():
            assert 'back' not in ordering, f"'back' hand-ordered in {section}"


class TestUnwiredTagTripwire:
    """Q5 (audit W17): an unowned menu tag must produce honest feedback,
    never a silent re-render."""

    def test_notify_unwired_shows_honest_dialog(self):
        from types import SimpleNamespace
        calls = []
        fake = SimpleNamespace(
            dialog=SimpleNamespace(msgbox=lambda t, b: calls.append((t, b))))
        tui_main.MeshForgeLauncher._notify_unwired(fake, 'ghost-tag')
        assert calls and 'ghost-tag' in calls[0][1]

    def test_every_submenu_wires_the_tripwire(self):
        import inspect
        for menu in ('dashboard', 'mesh_networks', 'rf_sdr', 'maps_viz',
                     'configuration', 'system', 'extensions', 'about'):
            src = inspect.getsource(
                getattr(tui_main.MeshForgeLauncher, f'_{menu}_menu'))
            assert '_notify_unwired' in src, (
                f'_{menu}_menu lost the W17 unknown-tag tripwire'
            )

    # ---------------------------------------------------------------- F9
    # The tripwire above covers the eight top-level submenus. Inside
    # handlers/ the idiom is `entry = dispatch.get(choice)` / `if entry:`
    # — 77 sites across 62 modules, and until 2026-09-16 exactly one of
    # them had an `else`. `dict.get` returns None both for "no handler
    # owns this tag" and for the ordinary path, so a wiring bug rendered
    # as a menu row that does nothing when pressed, forever, with no
    # witness (honest_failure_modes #1 and #9).

    @staticmethod
    def _dispatch_sites_by_function():
        """Every dispatch-table site, grouped by the function holding it.

        A site is `NAME = TABLE.get(VAR)` followed by `if NAME:`, where
        TABLE is a dict literal assigned in that same function. The
        literal-dict requirement is what keeps runtime lookups out — e.g.
        `registries.get(svc_name)` in gateway.py is not menu dispatch.
        """
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "src" / "launcher_tui"
        found = {}
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), str(path))
            for fn in [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                tables = {t.id for n in ast.walk(fn)
                          if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
                          for t in n.targets if isinstance(t, ast.Name)}
                results = set()
                for n in ast.walk(fn):
                    if (isinstance(n, ast.Assign) and len(n.targets) == 1
                            and isinstance(n.targets[0], ast.Name)
                            and isinstance(n.value, ast.Call)
                            and isinstance(n.value.func, ast.Attribute)
                            and n.value.func.attr == "get"
                            and len(n.value.args) == 1
                            and isinstance(n.value.args[0], ast.Name)
                            and isinstance(n.value.func.value, ast.Name)
                            and n.value.func.value.id in tables):
                        results.add(n.targets[0].id)
                sites = [n.lineno for n in ast.walk(fn)
                         if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                         and n.test.id in results]
                if sites:
                    key = f"{path.name}:{fn.name}"
                    found.setdefault(key, {"path": path, "fn": fn, "sites": []})
                    found[key]["sites"].extend(sites)
        return found

    def test_every_handler_dispatch_site_has_a_tripwire(self):
        """Every menu-dispatch site must be able to say 'not wired'.

        Shape-based on purpose: it asks whether the function CAN report an
        unowned tag, not whether any specific tag is unowned. A checker
        that predicts which tags are missing is a forecast; this is a gate.

        Some sites are hybrid — a dispatch dict for some tags and an
        if/elif chain for the rest — so the tripwire lives at the end of
        the chain rather than on `if entry:`. Counting calls per function
        rather than inspecting each `if` covers both shapes without
        needing to understand either.
        """
        import ast
        offenders = []
        for key, rec in self._dispatch_sites_by_function().items():
            n_sites = len(rec["sites"])
            n_guards = sum(
                1 for n in ast.walk(rec["fn"])
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("notify_unwired", "_notify_unwired"))
            if n_guards < n_sites:
                offenders.append(
                    f"{key} — {n_sites} dispatch site(s) at line(s) "
                    f"{sorted(rec['sites'])}, {n_guards} tripwire(s)")
        assert not offenders, (
            "menu-dispatch sites with no way to report an unowned tag "
            "(F9 — a silent re-render is a wiring bug the operator can "
            "never see):\n  " + "\n  ".join(offenders))

    def test_tuicontext_notify_unwired_shows_honest_dialog(self):
        """The handler-side twin of _notify_unwired, exercised for real."""
        from types import SimpleNamespace
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from launcher_tui.handler_protocol import TUIContext
        calls = []
        ctx = TUIContext(dialog=SimpleNamespace(
            msgbox=lambda t, b: calls.append((t, b))))
        ctx.notify_unwired("ghost-tag", "SomeHandler._some_menu")
        assert calls, "notify_unwired showed no dialog"
        title, body = calls[0]
        assert title == "Not wired"
        assert "ghost-tag" in body
        assert "SomeHandler._some_menu" in body
