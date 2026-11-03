"""
Honest-signal tests for src/launcher_tui/handlers/mqtt.py (#74 class).

A "Saved" / "Set" / "Configured" success dialog must NOT appear when the
config write to disk failed. Each save site now routes through
``ctx.report_action(ok, ...)`` so a False write shows the honest failure
dialog instead of a false-green success.

RED-FIRST proof: the FAILURE-path tests below assert the dialog title is the
honest failure ("Save Failed"), which can ONLY pass once the unconditional
``msgbox(success_title, ...)`` is replaced by ``report_action``. Reverting any
one site to the old unconditional-success behavior makes its failure test fail.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402  (FakeDialog via ctx)
from monitoring import mqtt_settings as ms  # noqa: E402
from utils.paths import atomic_write_text_preserving as W  # noqa: E402


# F8 (2026-11-02): screens write CHANGES through mqtt_settings.save_changes;
# these drive the real file in a tmp dir. A failed write is a REAL OSError
# from the writer, not a mocked False.
@pytest.fixture
def cfg(tmp_path):
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=tmp_path):
        yield tmp_path


def _seed(d, **kw):
    assert ms.save(d, {**ms.defaults(), **kw}, W).ok
    return (d / ms.FILENAME).read_bytes()


def _write_fails():
    return patch("utils.paths.atomic_write_text_preserving",
                 side_effect=OSError(28, "No space left on device"))


def _make_handler():
    from handlers.mqtt import MQTTHandler
    ctx = make_handler_context()
    handler = MQTTHandler()
    handler.set_context(ctx)
    return handler, ctx


# ---------------------------------------------------------------------------
# Site: "Save & Exit" -> "Saved"  (inside _configure_mqtt menu loop)
# Driven by FakeDialog menu return "save".
# ---------------------------------------------------------------------------

def test_save_exit_write_failure_shows_honest_failure(cfg):
    """RED-FIRST: a failed write must NOT show the 'Saved' success dialog."""
    before = _seed(cfg, broker="localhost")
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["save"]

    with _write_fails():
        handler._configure_mqtt()

    assert (cfg / ms.FILENAME).read_bytes() == before
    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Saved"


def test_save_exit_write_success_shows_saved(cfg):
    """Mirror: a successful write shows the 'Saved' success dialog."""
    _seed(cfg, broker="localhost")
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["save"]

    handler._configure_mqtt()

    assert ms.load(cfg).state == "ok"
    assert ctx.dialog.last_msgbox_title == "Saved"


# ---------------------------------------------------------------------------
# Site: "Local Mode Set"  (inside _configure_mqtt menu loop)
# Driven by FakeDialog menu return "local".
# ---------------------------------------------------------------------------

def test_local_mode_write_failure_shows_honest_failure(cfg):
    """RED-FIRST: a failed write must NOT show 'Local Mode Set'."""
    before = _seed(cfg)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["local"]

    # _detect_local_channel touches the connection layer; stub it out.
    with patch.object(handler, '_detect_local_channel', return_value=None), _write_fails():
        handler._configure_mqtt()

    assert (cfg / ms.FILENAME).read_bytes() == before
    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Local Mode Set"


def test_local_mode_write_success_shows_set(cfg):
    """Mirror: a successful write shows 'Local Mode Set'."""
    _seed(cfg)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["local"]

    with patch.object(handler, '_detect_local_channel', return_value=None):
        handler._configure_mqtt()

    assert ms.load(cfg).intent["broker"] == "localhost"
    assert ctx.dialog.last_msgbox_title == "Local Mode Set"


# ---------------------------------------------------------------------------
# Site: "Private Broker Configured"  (the Configure list's "private" row)
# ---------------------------------------------------------------------------

def test_private_broker_write_failure_shows_honest_failure(cfg):
    """RED-FIRST: a failed write must NOT show 'Private Broker Configured'."""
    before = _seed(cfg)
    handler, ctx = _make_handler()
    # inputbox sequence: broker, port, username, password, root_topic, channel
    ctx.dialog._inputbox_returns = [
        "broker.example", "1883", "", "", "msh/US/2/e", "LongFast",
    ]

    ctx.dialog._menu_returns = ["private", "auto"]   # TLS prompt (2026-11-01)
    with _write_fails():
        handler._configure_mqtt()

    assert (cfg / ms.FILENAME).read_bytes() == before
    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Private Broker Configured"


def test_private_broker_write_success_shows_configured(cfg):
    """Mirror: a successful write shows 'Private Broker Configured'."""
    _seed(cfg)
    handler, ctx = _make_handler()
    ctx.dialog._inputbox_returns = [
        "broker.example", "1883", "", "", "msh/US/2/e", "LongFast",
    ]

    ctx.dialog._menu_returns = ["private", "auto"]   # TLS prompt (2026-11-01)
    handler._configure_mqtt()

    assert ms.load(cfg).intent["broker"] == "broker.example"
    assert ctx.dialog.last_msgbox_title == "Private Broker Configured"


# ---------------------------------------------------------------------------
# Site: the toggles (autostart / autotelem) and the Cancel row.
# Edits live in memory until "Save & Exit"; the form must SAY so at the
# moment it matters — on the popup (pending tense) and on the menu the
# user is about to Cancel out of (2026-09-18, cancel-label review F1 follow-up).
# ---------------------------------------------------------------------------

def _menu_calls(ctx):
    return [c for c in ctx.dialog.calls if c[0] == 'menu']


def test_toggle_then_cancel_writes_nothing_and_the_form_said_so(cfg):
    before = _seed(cfg, broker="localhost")
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["autostart", "cancel"]

    handler._configure_mqtt()

    assert (cfg / ms.FILENAME).read_bytes() == before
    # The popup speaks in the future tense and names the discard path.
    assert "will be ENABLED after 'Save & Exit'" in ctx.dialog.last_msgbox_text
    assert "discards" in ctx.dialog.last_msgbox_text
    # The SECOND render (after the toggle) carries the unsaved marker on the
    # subtitle AND on the Cancel row the user is about to pick.
    first, second = _menu_calls(ctx)
    assert "UNSAVED" not in first[1][1]
    assert "UNSAVED CHANGES" in second[1][1]
    cancel_rows = [d for t, d in second[1][2] if t == "cancel"]
    assert cancel_rows == ["Cancel              (discard unsaved changes)"]


def test_clean_form_carries_no_unsaved_marker(cfg):
    before = _seed(cfg, broker="localhost")
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["cancel"]

    handler._configure_mqtt()

    (only,) = _menu_calls(ctx)
    assert "UNSAVED" not in only[1][1]
    assert [d for t, d in only[1][2] if t == "cancel"] == ["Cancel"]
    assert (cfg / ms.FILENAME).read_bytes() == before


def test_toggle_twice_is_clean_again(cfg):
    """Dirty is a COMPARISON against disk, not a flag: undoing the edit clears it.

    The key must exist on disk for the round trip to be a no-op — a key the
    file never had is a real (if harmless) change, and the form says so."""
    _seed(cfg, broker="localhost", auto_start_telemetry=True)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["autotelem", "autotelem", "cancel"]

    handler._configure_mqtt()

    calls = _menu_calls(ctx)
    assert "UNSAVED" in calls[1][1][1]
    assert "UNSAVED" not in calls[2][1][1]
