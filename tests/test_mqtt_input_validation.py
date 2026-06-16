"""
Input-validation tests for src/launcher_tui/handlers/mqtt.py.

User-entered broker hostnames and ports flow into the persisted MQTT config.
Before this guard they were saved unvalidated: a hostname with illegal chars
(spaces) or an out-of-range port ("0"/"99999") would be written verbatim and
later fed into network commands (the MF002/MF-input-validation class).

Each site now guards with the existing TUIContext validators
(``validate_hostname`` / ``validate_port``), shows an actionable error dialog,
and skips the assignment / save so the bad value never persists.

RED-FIRST proof (reported in the task summary): with the guard removed, the
invalid-input tests below FAIL — save_mqtt_config gets called with the bad
value and no "Invalid Host"/"Invalid Port" dialog appears.

Two methods, two value kinds each:
  _configure_mqtt          (menu loop: invalid -> msgbox + continue; valid -> assign)
  _configure_private_broker (linear: invalid -> msgbox + return; valid -> save)

Run: python3 -m pytest tests/test_mqtt_input_validation.py -v
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402


def _make_handler():
    from handlers.mqtt import MQTTHandler
    ctx = make_handler_context()
    handler = MQTTHandler()
    handler.set_context(ctx)
    return handler, ctx


# ---------------------------------------------------------------------------
# Site A: _configure_mqtt menu -> "broker" branch (host).
# Menu loop: feed the action tag, then "back" so the loop exits after the
# invalid-input branch loops back to the menu (continue).
# ---------------------------------------------------------------------------

@patch('handlers.mqtt.load_mqtt_config', return_value={'broker': 'mqtt.meshtastic.org'})
@patch('handlers.mqtt.save_mqtt_config')
def test_configure_mqtt_invalid_broker_not_saved(mock_save, mock_load):
    """RED-FIRST: a hostname with a space is rejected, never assigned/saved."""
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["broker", "save"]
    ctx.dialog._inputbox_returns = ["bad host!"]  # illegal space char

    handler._configure_mqtt()

    # Error dialog shown for the bad host.
    titles = [c[1][0] for c in ctx.dialog.calls if c[0] == 'msgbox']
    assert "Invalid Host" in titles
    # The bad value never reached the persisted config: only the valid
    # "save" path called save_mqtt_config, and its config has the ORIGINAL
    # broker (the invalid edit was skipped via continue).
    assert mock_save.call_count == 1
    saved_cfg = mock_save.call_args[0][0]
    assert saved_cfg['broker'] == 'mqtt.meshtastic.org'
    assert saved_cfg['broker'] != 'bad host!'


@patch('handlers.mqtt.load_mqtt_config', return_value={'broker': 'mqtt.meshtastic.org'})
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_configure_mqtt_valid_broker_proceeds(mock_save, mock_load):
    """Mirror: a valid hostname is assigned and saved unchanged."""
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["broker", "save"]
    ctx.dialog._inputbox_returns = ["broker.example.com"]

    handler._configure_mqtt()

    titles = [c[1][0] for c in ctx.dialog.calls if c[0] == 'msgbox']
    assert "Invalid Host" not in titles
    assert mock_save.call_count == 1
    assert mock_save.call_args[0][0]['broker'] == "broker.example.com"


# ---------------------------------------------------------------------------
# Site B: _configure_mqtt menu -> "port" branch.
# Old gate was .isdigit() only — accepted "0" and "99999".
# ---------------------------------------------------------------------------

@patch('handlers.mqtt.load_mqtt_config', return_value={'broker': 'localhost', 'port': 1883})
@patch('handlers.mqtt.save_mqtt_config')
def test_configure_mqtt_out_of_range_port_not_saved(mock_save, mock_load):
    """RED-FIRST: "99999" passes .isdigit() but fails validate_port."""
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["port", "save"]
    ctx.dialog._inputbox_returns = ["99999"]

    handler._configure_mqtt()

    titles = [c[1][0] for c in ctx.dialog.calls if c[0] == 'msgbox']
    assert "Invalid Port" in titles
    assert mock_save.call_count == 1
    saved_cfg = mock_save.call_args[0][0]
    assert saved_cfg['port'] == 1883          # original, unchanged
    assert saved_cfg['port'] != 99999


@patch('handlers.mqtt.load_mqtt_config', return_value={'broker': 'localhost', 'port': 1883})
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_configure_mqtt_valid_port_proceeds(mock_save, mock_load):
    """Mirror: an in-range port is assigned as an int and saved."""
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["port", "save"]
    ctx.dialog._inputbox_returns = ["8883"]

    handler._configure_mqtt()

    titles = [c[1][0] for c in ctx.dialog.calls if c[0] == 'msgbox']
    assert "Invalid Port" not in titles
    assert mock_save.call_count == 1
    assert mock_save.call_args[0][0]['port'] == 8883


# ---------------------------------------------------------------------------
# Site C: _configure_private_broker (host) — linear, invalid -> return.
# Inputbox sequence: broker, [port, username, password, root_topic, channel].
# On an invalid broker the method returns immediately, so only one input
# is consumed and save is never reached.
# ---------------------------------------------------------------------------

@patch('handlers.mqtt.save_mqtt_config')
def test_private_broker_invalid_host_not_saved(mock_save):
    """RED-FIRST: an illegal private-broker hostname is rejected before save."""
    handler, ctx = _make_handler()
    ctx.dialog._inputbox_returns = ["bad host!"]  # only the broker is read

    handler._configure_private_broker({})

    titles = [c[1][0] for c in ctx.dialog.calls if c[0] == 'msgbox']
    assert "Invalid Host" in titles
    assert not mock_save.called


@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_private_broker_valid_host_proceeds_to_save(mock_save):
    """Mirror: a valid private broker + port flows through to a real save."""
    handler, ctx = _make_handler()
    # broker, port, username, password, root_topic, channel
    ctx.dialog._inputbox_returns = [
        "broker.example.com", "1883", "", "", "msh/US/2/e", "LongFast",
    ]

    handler._configure_private_broker({})

    titles = [c[1][0] for c in ctx.dialog.calls if c[0] == 'msgbox']
    assert "Invalid Host" not in titles
    assert "Invalid Port" not in titles
    assert mock_save.called
    saved_cfg = mock_save.call_args[0][0]
    assert saved_cfg['broker'] == "broker.example.com"
    assert saved_cfg['port'] == 1883


# ---------------------------------------------------------------------------
# Site D: _configure_private_broker (port) — out-of-range rejected.
# Broker valid so flow reaches the port; "99999" must return before save.
# ---------------------------------------------------------------------------

@patch('handlers.mqtt.save_mqtt_config')
def test_private_broker_out_of_range_port_not_saved(mock_save):
    """RED-FIRST: "99999" passes the old .isdigit() gate but fails validate_port."""
    handler, ctx = _make_handler()
    ctx.dialog._inputbox_returns = ["broker.example.com", "99999"]

    handler._configure_private_broker({})

    titles = [c[1][0] for c in ctx.dialog.calls if c[0] == 'msgbox']
    assert "Invalid Port" in titles
    assert not mock_save.called
