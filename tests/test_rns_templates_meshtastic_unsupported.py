"""The Meshtastic_Interface templates are labelled UNSUPPORTED (2026-10-10).

The plugin they configure was retired in May 2026 (62c43328: "broken plugin
path"; MF no longer ships Meshtastic_Interface.py) and other users report the
same unreliability (research artifact rev 4.14). Operator: relabel, not remove,
not block. So: the label is visible in the TUI picker (which truncates at 60
chars), the reason is shown on selection, and applying is still ALLOWED.
"""
import pytest

from commands.rns_templates import get_interface_templates

KEYS = ("meshtastic", "meshtastic_dual")


@pytest.fixture
def templates():
    r = get_interface_templates()
    assert r.success
    return r.data["templates"]


@pytest.mark.parametrize("key", KEYS)
def test_meshtastic_templates_say_unsupported_first(templates, key):
    t = templates[key]
    assert t["description"].startswith("UNSUPPORTED"), t["description"]
    why = t.get("unsupported", "")
    assert "retired" in why and "Meshtastic_Interface.py" in why, why


@pytest.mark.parametrize("key", KEYS)
def test_label_survives_the_pickers_60_char_truncation(templates, key):
    """Mirror of rns_interfaces._rns_add_interface's label rule."""
    t = templates[key]
    label = (f"Multi - {t['description']}" if t.get("multi_interface")
             else f"{t['type']} - {t['description']}")
    if len(label) > 60:
        label = label[:57] + "..."
    assert "UNSUPPORTED" in label, label


@pytest.mark.parametrize("key", KEYS)
def test_relabel_is_not_a_block(templates, key):
    """`unavailable` makes apply refuse; the operator asked for a label only."""
    assert "unavailable" not in templates[key]


def test_no_other_template_is_marked_unsupported(templates):
    marked = {k for k, t in templates.items()
              if t.get("unsupported") or str(t.get("description", "")).startswith("UNSUPPORTED")}
    assert marked == set(KEYS), marked


def test_tui_shows_the_reason_and_lets_the_operator_continue():
    """The picker's selection path must show `unsupported` (source pin: the
    handler reads it, and does not return on it)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "src/launcher_tui/handlers/rns_interfaces.py").read_text()
    i = src.index("template.get('unsupported')")
    block = src[i:i + 600]
    assert "msgbox" in block or "yesno" in block, block
