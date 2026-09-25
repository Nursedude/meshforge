"""The live-truth ledger (launcher_tui/live_truth.py) stays a record anyone can re-check.

A closed table keyed by (section, tag): every key must be a LIVE action (a
retired action's entry is a claim about nothing), and every entry must say
when, where, how far and on what evidence — a bare "works" is not an entry.
"""
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
for p in (str(_SRC), str(_SRC / "launcher_tui"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from handler_registry import HandlerRegistry  # noqa: E402
from handler_test_utils import make_handler_context  # noqa: E402
from launcher_tui.live_truth import LIVE_VERIFIED, live_class  # noqa: E402


def _live_actions():
    from handlers import get_all_handlers
    reg = HandlerRegistry(make_handler_context())
    for cls in get_all_handlers():
        reg.register(cls())
    return {(s, t) for s in reg.section_names for t, _ in reg.get_menu_items(s)}


def test_every_ledger_key_is_a_live_action():
    stale = set(LIVE_VERIFIED) - _live_actions()
    assert not stale, f"live_truth.py records actions that no longer exist: {sorted(stale)}"


def test_every_entry_says_when_where_how_far_and_on_what():
    for key, e in LIVE_VERIFIED.items():
        assert set(e) >= {"date", "box", "scope", "evidence", "partial"}, key
        assert isinstance(e["partial"], bool), key
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"]), key
        assert e["box"].strip() and len(e["scope"]) >= 20 and len(e["evidence"]) >= 10, key
        assert e["scope"].strip().lower() not in ("works", "ok", "verified", "checked"), key


def test_the_live_cell_marks_partial_scope_and_blank_for_unchecked():
    assert live_class("nope", "nope") == ""
    for (s, t), e in LIVE_VERIFIED.items():
        cell = live_class(s, t)
        assert e["date"] in cell and e["box"] in cell
    assert live_class("rf_sdr", "sdr").startswith("◐")          # banner only
    assert live_class("dashboard", "nodes").startswith("✓")     # "(ESP32-only API)" is not partial
