"""Host-input validation tests for two TUI handlers.

A user-entered host flows from an inputbox into either a subprocess argv
(meshtasticd MQTT broker address) or a persisted config call (propagation
source host). Both must be rejected with an actionable dialog and an early
exit BEFORE the side effect, using the shared ``ctx.validate_hostname()``
guard (rejects empty / >253 / leading '-' / chars outside [A-Za-z0-9.\\-:]).

RED-FIRST: with the guards removed, the bad host reaches subprocess.run /
configure_source — these tests fail (the "NOT called" assertions trip).

Covers both sites:
  - meshtasticd_mqtt._mqtt_set_broker     -> subprocess.run argv
  - propagation._toggle_rest_source       -> propagation.configure_source
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from handlers.meshtasticd_mqtt import MeshtasticdDeviceMQTTHandler  # noqa: E402
from handlers.propagation import PropagationHandler  # noqa: E402
from commands.propagation import DataSource  # noqa: E402


def _ok_run(*_args, **_kwargs):
    """A subprocess.run result whose radio write succeeded (returncode 0)."""
    res = MagicMock()
    res.returncode = 0
    res.stdout = ""
    res.stderr = ""
    return res


def _last_msgbox_says_invalid(dialog):
    """True if the most recent msgbox is the 'Invalid Host' rejection dialog."""
    return (dialog.last_msgbox_title or "") == "Invalid Host"


# --------------------------------------------------------------------------- #
# Site 1 — meshtasticd MQTT broker setter
# --------------------------------------------------------------------------- #
class TestMqttBrokerHostValidation(unittest.TestCase):
    """_mqtt_set_broker: invalid host must not reach subprocess.run."""

    def setUp(self):
        self.dialog = FakeDialog()
        self.ctx = make_handler_context(dialog=self.dialog)
        self.ctx._meshtastic_path = "/usr/bin/true"
        self.handler = MeshtasticdDeviceMQTTHandler()
        self.handler.set_context(self.ctx)

    def _drive(self, broker):
        # inputbox returns the (in)valid broker the operator typed.
        self.dialog._inputbox_returns = [broker]
        run_mock = MagicMock(side_effect=_ok_run)
        with patch('handlers.meshtasticd_mqtt.subprocess.run', run_mock), \
             patch.object(type(self.ctx), 'wait_for_enter', lambda *a, **k: None):
            self.handler._mqtt_set_broker()
        return run_mock

    def test_invalid_host_blocked(self):
        """A host with a space + '!' is rejected; subprocess.run not called."""
        run_mock = self._drive("bad host!")
        self.assertTrue(
            _last_msgbox_says_invalid(self.dialog),
            f"expected 'Invalid Host' dialog, got: "
            f"{self.dialog.last_msgbox_title!r}",
        )
        run_mock.assert_not_called()

    def test_valid_host_reaches_subprocess(self):
        """A valid host flows through to subprocess.run unchanged."""
        run_mock = self._drive("mqtt.meshtastic.org")
        self.assertFalse(
            _last_msgbox_says_invalid(self.dialog),
            "valid host must not trigger the Invalid Host dialog",
        )
        run_mock.assert_called()
        # The broker is the last positional in the argv list.
        argv = run_mock.call_args.args[0]
        self.assertIn("mqtt.meshtastic.org", argv)


# --------------------------------------------------------------------------- #
# Site 2 — propagation REST-source host setter
# --------------------------------------------------------------------------- #
class TestPropagationHostValidation(unittest.TestCase):
    """_toggle_rest_source: invalid host must not reach configure_source."""

    def setUp(self):
        self.dialog = FakeDialog()
        self.ctx = make_handler_context(dialog=self.dialog)
        self.handler = PropagationHandler()
        self.handler.set_context(self.ctx)

    def _drive(self, host):
        # Source currently disabled -> the enable branch prompts host then port.
        get_sources = MagicMock()
        get_sources.success = True
        get_sources.data = {'sources': {}}

        cfg_result = MagicMock()
        cfg_result.message = "OpenHamClock enabled."
        cfg_result.data = {'persisted': True}
        configure = MagicMock(return_value=cfg_result)

        # inputbox sequence: host, then port.
        self.dialog._inputbox_returns = [host, "3000"]
        with patch('handlers.propagation.propagation.get_sources',
                   return_value=get_sources), \
             patch('handlers.propagation.propagation.configure_source',
                   configure):
            self.handler._toggle_rest_source(
                DataSource.OPENHAMCLOCK, "OpenHamClock", 3000)
        return configure

    def test_invalid_host_blocked(self):
        """A host with a space + '!' is rejected; configure_source not called."""
        configure = self._drive("bad host!")
        self.assertTrue(
            _last_msgbox_says_invalid(self.dialog),
            f"expected 'Invalid Host' dialog, got: "
            f"{self.dialog.last_msgbox_title!r}",
        )
        configure.assert_not_called()

    def test_valid_host_reaches_configure_source(self):
        """A valid host flows through to configure_source unchanged."""
        configure = self._drive("localhost")
        self.assertFalse(
            _last_msgbox_says_invalid(self.dialog),
            "valid host must not trigger the Invalid Host dialog",
        )
        configure.assert_called_once()
        self.assertEqual(configure.call_args.kwargs.get('host'), "localhost")


if __name__ == '__main__':
    unittest.main()
