"""
Honest-signal tests for MeshAlertsHandler (#74 false-green class).

The alert-config dialogs must NOT claim "Updated" when the underlying
``MeshAlertEngine.update_config()`` returns False (the SettingsManager.save()
bool). The highest-stakes site is emergency_keywords: a life-safety alert
config that silently fails to persist must read as a failure, not a win.

These tests drive the narrowest per-site handler method with a stub engine
whose ``update_config`` we control, and assert the dialog title reflects the
REAL persistence result.
"""

import os
import sys
from unittest.mock import patch

# Ensure src + launcher_tui + tests dir importable (mirrors sibling tests).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402

from handlers.mesh_alerts import MeshAlertsHandler  # noqa: E402


class _StubEngine:
    """Minimal stand-in for MeshAlertEngine.

    ``config`` supplies current values the handler reads; ``update_config``
    returns a programmable bool (the persistence result) and records the call.
    """

    def __init__(self, save_ok: bool, config=None):
        self._save_ok = save_ok
        self.config = config or {
            "enabled_types": ["battery", "emergency"],
            "emergency_keywords": ["help", "sos"],
            "battery_threshold": 20,
            "disconnect_timeout_minutes": 30,
            "cooldown_seconds": 300,
        }
        self.calls = []

    def get_active_alerts(self):
        return []

    def update_config(self, key, value):
        self.calls.append((key, value))
        return self._save_ok


def _make_handler(engine, dialog=None):
    """Build a context-wired MeshAlertsHandler whose engine is the stub."""
    ctx = make_handler_context(dialog=dialog or FakeDialog())
    handler = MeshAlertsHandler()
    handler.set_context(ctx)
    return handler, ctx


# ── emergency_keywords (HIGHEST PRIORITY) ────────────────────────────────

def test_keywords_save_failure_shows_failure_not_updated():
    """RED-FIRST proof: a failed emergency-keyword save must NOT read 'Updated'."""
    engine = _StubEngine(save_ok=False)
    dialog = FakeDialog()
    dialog._inputbox_returns = ["help, mayday"]
    handler, ctx = _make_handler(engine, dialog)

    with patch("handlers.mesh_alerts.get_alert_engine", return_value=engine):
        handler._configure_keywords()

    # The save failed → honest failure title, never the success title.
    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Updated"
    # The fail body must make the life-safety persistence loss clear.
    assert "could NOT be saved" in ctx.dialog.last_msgbox_text
    assert "LIFE-SAFETY" in ctx.dialog.last_msgbox_text
    # The update was still attempted with the parsed keywords.
    assert engine.calls == [("emergency_keywords", ["help", "mayday"])]


def test_keywords_save_success_shows_updated():
    """Mirror: a persisted emergency-keyword save shows the success title."""
    engine = _StubEngine(save_ok=True)
    dialog = FakeDialog()
    dialog._inputbox_returns = ["help, mayday"]
    handler, ctx = _make_handler(engine, dialog)

    with patch("handlers.mesh_alerts.get_alert_engine", return_value=engine):
        handler._configure_keywords()

    assert ctx.dialog.last_msgbox_title == "Updated"
    assert "help, mayday" in ctx.dialog.last_msgbox_text


# ── battery_threshold (second covered site) ──────────────────────────────

def test_battery_save_failure_shows_failure_not_updated():
    engine = _StubEngine(save_ok=False)
    dialog = FakeDialog()
    dialog._inputbox_returns = ["15"]
    handler, ctx = _make_handler(engine, dialog)

    with patch("handlers.mesh_alerts.get_alert_engine", return_value=engine):
        handler._configure_battery()

    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Updated"
    assert "could NOT be saved" in ctx.dialog.last_msgbox_text
    assert engine.calls == [("battery_threshold", 15)]


def test_battery_save_success_shows_updated():
    engine = _StubEngine(save_ok=True)
    dialog = FakeDialog()
    dialog._inputbox_returns = ["15"]
    handler, ctx = _make_handler(engine, dialog)

    with patch("handlers.mesh_alerts.get_alert_engine", return_value=engine):
        handler._configure_battery()

    assert ctx.dialog.last_msgbox_title == "Updated"
    assert "15%" in ctx.dialog.last_msgbox_text


# ── enabled_types (third covered site) ───────────────────────────────────

def test_types_save_failure_shows_failure_not_updated():
    engine = _StubEngine(save_ok=False)
    dialog = FakeDialog()
    dialog._checklist_returns = [["battery", "emergency"]]
    handler, ctx = _make_handler(engine, dialog)

    with patch("handlers.mesh_alerts.get_alert_engine", return_value=engine):
        handler._toggle_alert_types()

    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Updated"
    assert "could NOT be saved" in ctx.dialog.last_msgbox_text
    assert engine.calls == [("enabled_types", ["battery", "emergency"])]


def test_types_save_success_shows_updated():
    engine = _StubEngine(save_ok=True)
    dialog = FakeDialog()
    dialog._checklist_returns = [["battery", "emergency"]]
    handler, ctx = _make_handler(engine, dialog)

    with patch("handlers.mesh_alerts.get_alert_engine", return_value=engine):
        handler._toggle_alert_types()

    assert ctx.dialog.last_msgbox_title == "Updated"
