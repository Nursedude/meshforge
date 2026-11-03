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
invalid-input tests below FAIL — the bad value reaches the saved file
(F8 2026-11-02: these read the REAL file in a tmp dir, not a mocked save) and no "Invalid Host"/"Invalid Port" dialog appears.

Two methods, two value kinds each:
  _configure_mqtt          (menu loop: invalid -> msgbox + continue; valid -> assign)
  _configure_private_broker (linear: invalid -> msgbox + return; valid -> save)

Run: python3 -m pytest tests/test_mqtt_input_validation.py -v
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from monitoring import mqtt_settings as ms  # noqa: E402
from utils.paths import atomic_write_text_preserving as W  # noqa: E402


def _make_handler():
    from handlers.mqtt import MQTTHandler
    ctx = make_handler_context()
    handler = MQTTHandler()
    handler.set_context(ctx)
    return handler, ctx


@pytest.fixture
def cfg(tmp_path):
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=tmp_path):
        yield tmp_path


def _seed(d, **kw):
    assert ms.save(d, {**ms.defaults(), **kw}, W).ok
    return (d / ms.FILENAME).read_bytes()


def _saved(d):
    return json.loads((d / ms.FILENAME).read_text())


def _titles(ctx):
    return [c[1][0] for c in ctx.dialog.calls if c[0] == 'msgbox']


# ---------------------------------------------------------------------------
# Site A: _configure_mqtt menu -> "broker" branch (host).
# Menu loop: invalid -> msgbox + continue; then "save" writes the changes.
# ---------------------------------------------------------------------------

def test_configure_mqtt_invalid_broker_not_saved(cfg):
    """RED-FIRST: a hostname with a space is rejected, never assigned/saved."""
    _seed(cfg)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["broker", "save"]
    ctx.dialog._inputbox_returns = ["bad host!"]  # illegal space char
    handler._configure_mqtt()
    assert "Invalid Host" in _titles(ctx)
    assert _saved(cfg)['broker'] == 'mqtt.meshtastic.org'


def test_configure_mqtt_valid_broker_proceeds(cfg):
    """Mirror: a valid hostname is assigned and saved unchanged."""
    _seed(cfg)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["broker", "save"]
    ctx.dialog._inputbox_returns = ["broker.example.com"]
    handler._configure_mqtt()
    assert "Invalid Host" not in _titles(ctx)
    assert _saved(cfg)['broker'] == "broker.example.com"


# ---------------------------------------------------------------------------
# Site B: _configure_mqtt menu -> "port" branch.
# Old gate was .isdigit() only — accepted "0" and "99999".
# ---------------------------------------------------------------------------

def test_configure_mqtt_out_of_range_port_not_saved(cfg):
    """RED-FIRST: "99999" passes .isdigit() but fails validate_port."""
    _seed(cfg, broker="localhost", port=1883)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["port", "save"]
    ctx.dialog._inputbox_returns = ["99999"]
    handler._configure_mqtt()
    assert "Invalid Port" in _titles(ctx)
    assert _saved(cfg)['port'] == 1883          # original, unchanged


def test_configure_mqtt_valid_port_proceeds(cfg):
    """Mirror: an in-range port is assigned as an int and saved."""
    _seed(cfg, broker="localhost", port=1883)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["port", "save"]
    ctx.dialog._inputbox_returns = ["8883"]
    handler._configure_mqtt()
    assert "Invalid Port" not in _titles(ctx)
    assert _saved(cfg)['port'] == 8883


# ---------------------------------------------------------------------------
# Site C: _configure_private_broker (host) — linear, invalid -> return.
# Reached from the Configure list's "private" row (F8: the form takes the
# screen's view + changes, so it is driven through that row).
# ---------------------------------------------------------------------------

def test_private_broker_invalid_host_not_saved(cfg):
    """RED-FIRST: an illegal private-broker hostname is rejected before save."""
    before = _seed(cfg)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["private"]
    ctx.dialog._inputbox_returns = ["bad host!"]  # only the broker is read
    handler._configure_mqtt()
    assert not [c for c in ctx.dialog.calls if c[0] == 'menu' and c[1][0] == "TLS"]   # never reached
    assert "Invalid Host" in _titles(ctx)
    assert (cfg / ms.FILENAME).read_bytes() == before


def test_private_broker_valid_host_proceeds_to_save(cfg):
    """Mirror: a valid private broker + port flows through to a real save."""
    _seed(cfg)
    handler, ctx = _make_handler()
    # broker, port, username, password (blank), root_topic, channel
    ctx.dialog._inputbox_returns = [
        "broker.example.com", "1883", "", "", "msh/US/2/e", "LongFast",
    ]
    ctx.dialog._menu_returns = ["private", "auto"]   # TLS prompt (2026-11-01)
    handler._configure_mqtt()
    assert "Invalid Host" not in _titles(ctx)
    assert "Invalid Port" not in _titles(ctx)
    saved = _saved(cfg)
    assert saved['broker'] == "broker.example.com"
    assert saved['port'] == 1883


# ---------------------------------------------------------------------------
# Site D: _configure_private_broker (port) — out-of-range rejected.
# Broker valid so flow reaches the port; "99999" must return before save.
# ---------------------------------------------------------------------------

def test_private_broker_out_of_range_port_not_saved(cfg):
    """RED-FIRST: "99999" passes the old .isdigit() gate but fails validate_port."""
    before = _seed(cfg)
    handler, ctx = _make_handler()
    ctx.dialog._menu_returns = ["private"]
    ctx.dialog._inputbox_returns = ["broker.example.com", "99999"]
    handler._configure_mqtt()
    assert not [c for c in ctx.dialog.calls if c[0] == 'menu' and c[1][0] == "TLS"]   # never reached
    assert "Invalid Port" in _titles(ctx)
    assert (cfg / ms.FILENAME).read_bytes() == before
