"""BIC-2 TUI pane — the Delivery handler's "Delivery legs" row.

Work lands IN the app (operator 10-11): the per-route counts are not done
until the TUI shows them. The handler stays a thin surface over
``utils.delivery_view.render_route_legs`` — read-only, and a render that
fails says UNKNOWN on screen instead of nothing.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "launcher_tui"))

from launcher_tui.handlers.delivery import DeliveryHandler  # noqa: E402


def _handler():
    h = DeliveryHandler()
    ctx = MagicMock()
    h.set_context(ctx)
    return h, ctx


def test_the_legs_row_is_on_the_menu():
    h, _ = _handler()

    assert "delivery_legs" in [tag for tag, _label, _flag in h.menu_items()]


def test_the_legs_row_renders_the_route_view_in_a_scrollable_box(monkeypatch):
    import utils.delivery_view as dv
    monkeypatch.setattr(dv, "render_route_legs", lambda: "DELIVERY LEGS — x")
    h, ctx = _handler()

    h.execute("delivery_legs")

    ctx.dialog.textbox.assert_called_once()
    title, text = ctx.dialog.textbox.call_args[0][:2]
    assert title == "Delivery legs" and text == "DELIVERY LEGS — x"


def test_a_render_failure_says_unknown_on_screen(monkeypatch):
    import utils.delivery_view as dv

    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr(dv, "render_route_legs", boom)
    h, ctx = _handler()

    h.execute("delivery_legs")

    text = ctx.dialog.textbox.call_args[0][1]
    assert "UNKNOWN" in text and "OSError" in text
