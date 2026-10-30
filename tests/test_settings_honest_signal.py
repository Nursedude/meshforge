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

import pytest

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


@pytest.fixture(autouse=True)
def _cli_save_ok(monkeypatch):
    """Pin ambient state: the CLI-connection half of a save is stubbed OK
    (tests that exercise it patch it themselves) — never ~/.config."""
    from commands.base import CommandResult
    monkeypatch.setattr("commands.meshtastic.save_connection",
                        lambda t, v, **k: CommandResult.ok("saved"))


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
# serial branch — since 10-30 it SAVES (validated); a refusal says why
# (the meshtastic CLI never auto-picks a USB port any more, so this is the
# only way a USB-only box gets one — and an RNode port is refused here)
# ---------------------------------------------------------------------------

def _serial_handler(save_result):
    from commands.base import CommandResult
    dialog = FakeDialog()
    dialog._menu_returns = ["serial"]
    dialog._inputbox_returns = ["/dev/ttyUSB0"]
    ctx = make_handler_context(dialog=dialog)
    handler = SettingsHandler()
    handler.set_context(ctx)
    calls = []
    res = (CommandResult.ok("saved") if save_result is True
           else CommandResult.fail(save_result))
    return handler, ctx, calls, (lambda t, v, **k: calls.append((t, v)) or res)


def test_serial_branch_saves_through_the_validated_setter():
    handler, ctx, calls, fake = _serial_handler(True)
    with patch("commands.meshtastic.save_connection", fake):
        handler._configure_connection()
    assert calls == [("serial", "/dev/ttyUSB0")]
    assert ctx.dialog.last_msgbox_title == "Connection"


def test_serial_refusal_is_shown_with_its_reason_never_as_set():
    reason = "Not saved: /dev/ttyUSB0 is a Reticulum interface port in /etc/reticulum/config"
    handler, ctx, calls, fake = _serial_handler(reason)
    with patch("commands.meshtastic.save_connection", fake):
        handler._configure_connection()
    assert ctx.dialog.last_msgbox_title == "Not Saved"
    assert "Reticulum interface port" in (ctx.dialog.last_msgbox_text or "")


@patch("utils.map_data_collector.MapDataCollector", _StubCollector)
def test_localhost_cli_save_failure_is_not_reported_as_set():
    # Both halves must persist: the map collector AND the CLI connection.
    from commands.base import CommandResult
    handler, ctx = _make_handler("localhost", save_result=True)
    with patch("commands.meshtastic.save_connection",
               lambda t, v, **k: CommandResult.fail("Not saved: settings file")):
        handler._configure_connection()
    assert ctx.dialog.last_msgbox_title == "Not Saved"


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


@patch("utils.map_data_collector.MapDataCollector", _StubCollector)
def test_half_saved_says_which_half():
    # r4 #6: map saved + CLI refused must not read "NOT saved" for the map.
    from commands.base import CommandResult
    handler, ctx = _make_handler("remote", save_result=True, inputbox="10.0.0.5:4403")
    with patch("commands.meshtastic.save_connection",
               lambda t, v, **k: CommandResult.fail("Not saved: settings file")):
        handler._configure_connection()
    text = ctx.dialog.last_msgbox_text or ""
    assert ctx.dialog.last_msgbox_title == "Not Saved"
    assert "map" in text.lower() and "was saved" in text.lower()
    assert "meshtastic cli" in text.lower() and "not saved: settings file" in text.lower()
