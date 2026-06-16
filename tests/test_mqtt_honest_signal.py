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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402  (FakeDialog via ctx)


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

@patch('handlers.mqtt.load_mqtt_config', return_value={'broker': 'localhost'})
@patch('handlers.mqtt.save_mqtt_config', return_value=False)
def test_save_exit_write_failure_shows_honest_failure(mock_save, mock_load):
    """RED-FIRST: a failed write must NOT show the 'Saved' success dialog."""
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["save"]

    handler._configure_mqtt()

    assert mock_save.called
    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Saved"


@patch('handlers.mqtt.load_mqtt_config', return_value={'broker': 'localhost'})
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_save_exit_write_success_shows_saved(mock_save, mock_load):
    """Mirror: a successful write shows the 'Saved' success dialog."""
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["save"]

    handler._configure_mqtt()

    assert mock_save.called
    assert ctx.dialog.last_msgbox_title == "Saved"


# ---------------------------------------------------------------------------
# Site: "Local Mode Set"  (inside _configure_mqtt menu loop)
# Driven by FakeDialog menu return "local".
# ---------------------------------------------------------------------------

@patch('handlers.mqtt.load_mqtt_config', return_value={'broker': 'mqtt.meshtastic.org'})
@patch('handlers.mqtt.save_mqtt_config', return_value=False)
def test_local_mode_write_failure_shows_honest_failure(mock_save, mock_load):
    """RED-FIRST: a failed write must NOT show 'Local Mode Set'."""
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["local"]

    # _detect_local_channel touches the connection layer; stub it out.
    with patch.object(handler, '_detect_local_channel', return_value=None):
        handler._configure_mqtt()

    assert mock_save.called
    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Local Mode Set"


@patch('handlers.mqtt.load_mqtt_config', return_value={'broker': 'mqtt.meshtastic.org'})
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_local_mode_write_success_shows_set(mock_save, mock_load):
    """Mirror: a successful write shows 'Local Mode Set'."""
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["local"]

    with patch.object(handler, '_detect_local_channel', return_value=None):
        handler._configure_mqtt()

    assert mock_save.called
    assert ctx.dialog.last_msgbox_title == "Local Mode Set"


# ---------------------------------------------------------------------------
# Site: "Private Broker Configured"  (_configure_private_broker, called direct)
# ---------------------------------------------------------------------------

@patch('handlers.mqtt.save_mqtt_config', return_value=False)
def test_private_broker_write_failure_shows_honest_failure(mock_save):
    """RED-FIRST: a failed write must NOT show 'Private Broker Configured'."""
    handler, ctx = _make_handler()
    # inputbox sequence: broker, port, username, password, root_topic, channel
    ctx.dialog._inputbox_returns = [
        "broker.example", "1883", "", "", "msh/US/2/e", "LongFast",
    ]

    handler._configure_private_broker({})

    assert mock_save.called
    assert ctx.dialog.last_msgbox_title == "Save Failed"
    assert ctx.dialog.last_msgbox_title != "Private Broker Configured"


@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_private_broker_write_success_shows_configured(mock_save):
    """Mirror: a successful write shows 'Private Broker Configured'."""
    handler, ctx = _make_handler()
    ctx.dialog._inputbox_returns = [
        "broker.example", "1883", "", "", "msh/US/2/e", "LongFast",
    ]

    handler._configure_private_broker({})

    assert mock_save.called
    assert ctx.dialog.last_msgbox_title == "Private Broker Configured"
