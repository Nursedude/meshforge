"""
Input-validation tests for BrokerHandler._setup_custom_broker.

The custom-broker setup takes a hostname + port from the operator and persists
them into a connection profile (create_custom_profile -> save_profiles). Before
this guard the host flowed in completely unvalidated, and the port was only
``isdigit()``-gated — so an out-of-range value like "70000" reached the saved
profile and an active MQTT config, then surfaced later as opaque connection
failures.

These tests pin the contract: an INVALID host or port MUST surface the
actionable error dialog AND must NOT reach the profile writer (save_profiles).
The mirror case proves a valid host+port still flows through to the save.

RED-FIRST (verified): with the validate_hostname / validate_port guards removed
from _setup_custom_broker, the bad values reach save_profiles and no error
dialog is shown — both failure-path tests FAIL. With the guards in place they
pass. Evidence captured in the task report.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context


def _make_broker():
    from handlers.broker import BrokerHandler
    h = BrokerHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


# ---------------------------------------------------------------------------
# Invalid host MUST be rejected before the profile write
# ---------------------------------------------------------------------------

class TestCustomBrokerHostValidation:
    def test_invalid_host_shows_error_and_skips_save(self):
        """A host with illegal characters must surface 'Invalid Host' and NOT save."""
        h = _make_broker()
        # name, host(INVALID) -> rejected before port is ever prompted.
        h.ctx.dialog._inputbox_returns = ["my_broker", "bad host!"]

        with patch("handlers.broker.save_profiles") as mock_save, \
             patch.object(h, "_apply_profile_to_mqtt"):
            h._setup_custom_broker()

        assert h.ctx.dialog.last_msgbox_title == "Invalid Host", (
            f"expected 'Invalid Host', got {h.ctx.dialog.last_msgbox_title!r}"
        )
        mock_save.assert_not_called()

    def test_valid_host_and_port_reach_save(self):
        """Mirror: a valid host+port flows through to save_profiles."""
        h = _make_broker()
        # name, host, port, username(blank), region, channel, root_topic
        h.ctx.dialog._inputbox_returns = [
            "my_broker", "mqtt.example.com", "1883", "", "US", "LongFast", "msh/US/2/e",
        ]

        with patch("handlers.broker.save_profiles", return_value=True) as mock_save, \
             patch.object(h, "_apply_profile_to_mqtt"):
            h._setup_custom_broker()

        mock_save.assert_called_once()
        assert h.ctx.dialog.last_msgbox_title == "Custom Broker Saved"


# ---------------------------------------------------------------------------
# Out-of-range port MUST be rejected before the profile write
# ---------------------------------------------------------------------------

class TestCustomBrokerPortValidation:
    def test_out_of_range_port_shows_error_and_skips_save(self):
        """Port 70000 is all-digits but out of range — must reject, not save."""
        h = _make_broker()
        # name, host(valid), port(70000 -> isdigit() True but > 65535)
        h.ctx.dialog._inputbox_returns = ["my_broker", "mqtt.example.com", "70000"]

        with patch("handlers.broker.save_profiles") as mock_save, \
             patch.object(h, "_apply_profile_to_mqtt"):
            h._setup_custom_broker()

        assert h.ctx.dialog.last_msgbox_title == "Invalid Port", (
            f"expected 'Invalid Port', got {h.ctx.dialog.last_msgbox_title!r}"
        )
        mock_save.assert_not_called()

    def test_valid_port_reaches_save(self):
        """Mirror: an in-range port flows through to save_profiles."""
        h = _make_broker()
        h.ctx.dialog._inputbox_returns = [
            "my_broker", "mqtt.example.com", "8883", "", "US", "LongFast", "msh/US/2/e",
        ]

        with patch("handlers.broker.save_profiles", return_value=True) as mock_save, \
             patch.object(h, "_apply_profile_to_mqtt"):
            h._setup_custom_broker()

        mock_save.assert_called_once()
        assert h.ctx.dialog.last_msgbox_title == "Custom Broker Saved"
