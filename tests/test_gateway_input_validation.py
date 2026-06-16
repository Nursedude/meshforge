"""Input-validation guards for the Gateway config handler.

The gateway config menu writes a meshtastic host, a meshtastic port, and an
RNS config-directory PATH straight to the in-memory GatewayConfig from
unvalidated whiptail inputbox text. This pins the guards:

  - host        -> ctx.validate_hostname() before assigning meshtastic.host
  - port        -> ctx.validate_port() (1..65535) before assigning meshtastic.port
  - config_dir  -> absolute-path, no-'..'-traversal guard before assigning rns.config_dir

An invalid value must surface an actionable msgbox and NOT be written to the
config object; a valid value must flow through exactly as before.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402


def _make_handler(dialog):
    """Build a GatewayHandler wired to a FakeDialog-backed context."""
    from handlers.gateway import GatewayHandler

    handler = GatewayHandler()
    handler.set_context(make_handler_context(dialog=dialog))
    return handler


def _make_config():
    """A minimal stand-in carrying the two namespaces the methods mutate.

    Uses the real MeshtasticConfig/RNSConfig defaults so the attributes match
    production exactly, without invoking GatewayConfig.load()/save() side effects.
    """
    from gateway.config import MeshtasticConfig, RNSConfig

    return SimpleNamespace(meshtastic=MeshtasticConfig(), rns=RNSConfig())


def _last_msgbox_titles(dialog):
    return [c[1][0] for c in dialog.calls if c[0] == 'msgbox']


# --------------------------------------------------------------------------
# Host guard
# --------------------------------------------------------------------------

class TestMeshtasticHostGuard:
    def test_invalid_host_rejected_not_written(self):
        dialog = FakeDialog()
        # Sequence: choose "host", supply a bad host, then "back" out.
        dialog._menu_returns = ["host", "back"]
        dialog._inputbox_returns = ["bad host;rm -rf /"]  # spaces + ';' are illegal
        handler = _make_handler(dialog)
        config = _make_config()
        original = config.meshtastic.host

        handler._config_meshtastic(config)

        assert config.meshtastic.host == original, "invalid host must NOT be written"
        assert "Invalid Host" in _last_msgbox_titles(dialog)

    def test_valid_host_flows_through(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["host", "back"]
        dialog._inputbox_returns = ["192.168.1.50"]
        handler = _make_handler(dialog)
        config = _make_config()

        handler._config_meshtastic(config)

        assert config.meshtastic.host == "192.168.1.50"
        assert "Invalid Host" not in _last_msgbox_titles(dialog)


# --------------------------------------------------------------------------
# Port guard
# --------------------------------------------------------------------------

class TestMeshtasticPortGuard:
    def test_out_of_range_port_rejected_not_written(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["port", "back"]
        dialog._inputbox_returns = ["70000"]  # > 65535
        handler = _make_handler(dialog)
        config = _make_config()
        original = config.meshtastic.port

        handler._config_meshtastic(config)

        assert config.meshtastic.port == original, "out-of-range port must NOT be written"
        assert "Invalid Port" in _last_msgbox_titles(dialog)

    def test_zero_port_rejected(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["port", "back"]
        dialog._inputbox_returns = ["0"]  # below 1
        handler = _make_handler(dialog)
        config = _make_config()
        original = config.meshtastic.port

        handler._config_meshtastic(config)

        assert config.meshtastic.port == original
        assert "Invalid Port" in _last_msgbox_titles(dialog)

    def test_valid_port_flows_through(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["port", "back"]
        dialog._inputbox_returns = ["4404"]
        handler = _make_handler(dialog)
        config = _make_config()

        handler._config_meshtastic(config)

        assert config.meshtastic.port == 4404
        assert "Invalid Port" not in _last_msgbox_titles(dialog)


# --------------------------------------------------------------------------
# RNS config-dir PATH guard
# --------------------------------------------------------------------------

class TestRNSConfigDirGuard:
    def test_traversal_path_rejected_not_written(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["config_dir", "back"]
        dialog._inputbox_returns = ["/etc/reticulum/../../root/.ssh"]
        handler = _make_handler(dialog)
        config = _make_config()
        original = config.rns.config_dir

        handler._config_rns(config)

        assert config.rns.config_dir == original, "traversal path must NOT be written"
        assert "Invalid Path" in _last_msgbox_titles(dialog)

    def test_relative_path_rejected_not_written(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["config_dir", "back"]
        dialog._inputbox_returns = ["reticulum"]  # relative, not absolute
        handler = _make_handler(dialog)
        config = _make_config()
        original = config.rns.config_dir

        handler._config_rns(config)

        assert config.rns.config_dir == original, "relative path must NOT be written"
        assert "Invalid Path" in _last_msgbox_titles(dialog)

    def test_dotdot_in_relative_path_rejected(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["config_dir", "back"]
        dialog._inputbox_returns = ["../secrets"]
        handler = _make_handler(dialog)
        config = _make_config()
        original = config.rns.config_dir

        handler._config_rns(config)

        assert config.rns.config_dir == original
        assert "Invalid Path" in _last_msgbox_titles(dialog)

    def test_absolute_clean_path_accepted(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["config_dir", "back"]
        dialog._inputbox_returns = ["/etc/reticulum"]
        handler = _make_handler(dialog)
        config = _make_config()

        handler._config_rns(config)

        assert config.rns.config_dir == "/etc/reticulum"
        assert "Invalid Path" not in _last_msgbox_titles(dialog)

    def test_home_expanded_path_accepted(self):
        dialog = FakeDialog()
        dialog._menu_returns = ["config_dir", "back"]
        dialog._inputbox_returns = ["~/.reticulum"]
        handler = _make_handler(dialog)
        config = _make_config()

        handler._config_rns(config)

        # The '~' form resolves to an absolute path; the entered text is kept.
        assert config.rns.config_dir == "~/.reticulum"
        assert "Invalid Path" not in _last_msgbox_titles(dialog)
