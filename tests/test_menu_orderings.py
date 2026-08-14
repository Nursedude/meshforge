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
