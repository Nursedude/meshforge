"""DOC-1 — how-to guides readable INSIDE the TUI (About -> How-to Guides).

Plan: `.claude/plans/doc1_in_app_howtos_2026_11_03.md`. One copy per guide
(`docs/howto/*.md`), read on GitHub and shown offline here, so a ham without
internet or Claude can follow it. The guard below fails when a guide's
`**Where:**` names a menu label the TUI no longer has — a stale guide is a
lie in the operator's hand.
"""
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOWTO = REPO / "docs" / "howto"


def _about():
    from handlers.about import AboutHandler
    h = AboutHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h, ctx


def _guides():
    return sorted(HOWTO.glob("*.md"))


def test_about_offers_how_to_guides_and_the_section_orders_it():
    from launcher_tui.main import SECTION_ORDERINGS
    h, _ = _about()
    assert "howto" in [t for t, *_ in h.menu_items()]
    assert "howto" in SECTION_ORDERINGS["about"]


def test_every_guide_is_listed_by_its_title_and_shown_whole():
    h, ctx = _about()
    first = _guides()[0]
    ctx.dialog._menu_returns = [first.stem, None]
    h.execute("howto")
    menu = [c for c in ctx.dialog.calls if c[0] == "menu"][0]
    tags = [t for t, _ in menu[1][2]]
    assert tags == [g.stem for g in _guides()]
    titles = dict(menu[1][2])
    assert titles[first.stem] == first.read_text().splitlines()[0].lstrip("# ").strip()
    shown = [c for c in ctx.dialog.calls if c[0] == "textbox"]
    assert shown and shown[0][1][1] == first.read_text()


def test_no_guides_folder_says_where_it_looked(tmp_path):
    h, ctx = _about()
    with patch("handlers.about._HOWTO_DIR", tmp_path / "missing"):
        h.execute("howto")
    assert "missing" in ctx.dialog.last_msgbox_text


def test_every_guide_names_where_it_lives_in_the_menu():
    for g in _guides():
        assert re.search(r"^\*\*Where:\*\* `[^`]+`", g.read_text(), re.M), g.name


def test_every_where_path_matches_real_menu_labels():
    """Each `→` segment (number prefix dropped) must appear in a menu label
    string somewhere under src/launcher_tui — rename a row, fix the guide."""
    src = "\n".join(p.read_text() for p in (REPO / "src" / "launcher_tui").rglob("*.py"))
    missing = []
    for g in _guides():
        where = re.search(r"^\*\*Where:\*\* `([^`]+)`", g.read_text(), re.M).group(1)
        for seg in where.split("→"):
            label = re.sub(r"^\s*[0-9a-z]\.\s*", "", seg).strip()
            if label and label not in src:
                missing.append(f"{g.name}: {label!r}")
    assert not missing, missing
