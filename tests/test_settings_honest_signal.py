"""Honest-signal tests for the meshtasticd-connection settings handler (#74 class).

The connection-settings dialog used to show an unconditional "Connection set
to …" message even when the persistence call was a silent no-op or a failed
save — a false-green. These tests pin the honest contract:

  * a save that returns False -> the FAILURE dialog ("Not Saved"), never
    "Connection".
  * a save that returns True  -> the success dialog ("Connection").

Red-first proof: against the old unconditional-success code, the failure test
shows ``last_msgbox_title == "Connection"`` and fails the assertion.
"""

import os
import sys
from unittest.mock import patch

# Ensure src + launcher_tui + tests dir importable (mirrors sibling tests).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402

from handlers.settings import SettingsHandler  # noqa: E402


class _StubCollector:
    """Stand-in for MapDataCollector with a programmable persistence result."""

    _save_result = True

    def __init__(self, *args, **kwargs):
        pass

    def set_meshtasticd_connection(self, host, port):
        return type(self)._save_result


def _make_handler(menu_choice, save_result, inputbox=None):
    """Build a SettingsHandler wired to a FakeDialog + stub collector.

    menu_choice drives the connection-type menu; save_result is what the
    stubbed ``set_meshtasticd_connection`` returns; inputbox (if given) is the
    value returned by the inputbox prompt (the remote host:port path).
    """
    dialog = FakeDialog()
    dialog._menu_returns = [menu_choice]
    if inputbox is not None:
        dialog._inputbox_returns = [inputbox]
    ctx = make_handler_context(dialog=dialog)

    handler = SettingsHandler()
    handler.set_context(ctx)

    _StubCollector._save_result = save_result
    return handler, ctx


# ---------------------------------------------------------------------------
# localhost branch
# ---------------------------------------------------------------------------

@patch("utils.map_data_collector.MapDataCollector", _StubCollector)
def test_localhost_failed_save_shows_failure_not_success():
    """A persistence that returns False must NOT read as 'Connection' set."""
    handler, ctx = _make_handler("localhost", save_result=False)
    handler._configure_connection()
    assert ctx.dialog.last_msgbox_title == "Not Saved"
    assert ctx.dialog.last_msgbox_title != "Connection"


@patch("utils.map_data_collector.MapDataCollector", _StubCollector)
def test_localhost_successful_save_shows_success():
    """A persisted save shows the success dialog."""
    handler, ctx = _make_handler("localhost", save_result=True)
    handler._configure_connection()
    assert ctx.dialog.last_msgbox_title == "Connection"


# ---------------------------------------------------------------------------
# remote host:port branch
# ---------------------------------------------------------------------------

@patch("utils.map_data_collector.MapDataCollector", _StubCollector)
def test_remote_failed_save_shows_failure_not_success():
    """The remote-host path must surface the failure, not a false green."""
    handler, ctx = _make_handler(
        "remote", save_result=False, inputbox="10.0.0.5:4403"
    )
    handler._configure_connection()
    assert ctx.dialog.last_msgbox_title == "Not Saved"
    assert ctx.dialog.last_msgbox_title != "Connection"


@patch("utils.map_data_collector.MapDataCollector", _StubCollector)
def test_remote_successful_save_shows_success():
    """A persisted remote save shows the success dialog."""
    handler, ctx = _make_handler(
        "remote", save_result=True, inputbox="10.0.0.5:4403"
    )
    handler._configure_connection()
    assert ctx.dialog.last_msgbox_title == "Connection"


# ---------------------------------------------------------------------------
# serial branch — makes NO write, so it must NOT claim the connection was set
# ---------------------------------------------------------------------------

def test_serial_branch_does_not_claim_connection_set():
    """The serial branch performs no write; it must say nothing was saved."""
    dialog = FakeDialog()
    dialog._menu_returns = ["serial"]
    dialog._inputbox_returns = ["/dev/ttyUSB0"]
    ctx = make_handler_context(dialog=dialog)
    handler = SettingsHandler()
    handler.set_context(ctx)

    handler._configure_connection()

    # Must not falsely claim the connection was set/saved.
    assert ctx.dialog.last_msgbox_title != "Connection"
    assert "Not Saved" in ctx.dialog.last_msgbox_title
    assert "nothing was saved" in (ctx.dialog.last_msgbox_text or "").lower()


# ---------------------------------------------------------------------------
# _save_meshtasticd_connection returns False when the dependency is missing
# ---------------------------------------------------------------------------

@patch.dict(sys.modules, {"utils.map_data_collector": None})
def test_save_returns_false_when_collector_unavailable():
    """No collector backend -> the save is a no-op and reports False.

    handlers.settings imports MapDataCollector inside the save method (a
    TUI-startup deferral); a None entry in sys.modules makes that import
    raise ImportError, exercising the unavailable branch.
    """
    dialog = FakeDialog()
    ctx = make_handler_context(dialog=dialog)
    handler = SettingsHandler()
    handler.set_context(ctx)

    assert handler._save_meshtasticd_connection("localhost", 4403) is False
