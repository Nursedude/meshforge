"""Honest-signal tests for MeshtasticdDeviceMQTTHandler persistence writes.

The radio-write half of each MQTT setter is honest (gated on
``result.returncode == 0``). The second side effect — ``save_device_setting``
/ ``save_device_settings``, which persists the setting so it survives a
meshtasticd restart — was fire-and-forgotten: its return bool was discarded,
so a change that won't survive a restart still read as fully successful.

This mirrors the honest pattern in the sibling owner-name handler
(``meshtasticd_radio.py`` ~L122-135): capture the persist bool and append a
"not saved for restart persistence" warning to the success dialog when it is
falsey.

RED-FIRST: the warning-assertion tests fail against the old code (no warning
text is ever appended). Covers 2 of the 4 sites (enable + topic).
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


def _ok_run(*_args, **_kwargs):
    """A subprocess.run result whose radio write succeeded (returncode 0)."""
    res = MagicMock()
    res.returncode = 0
    res.stdout = ""
    res.stderr = ""
    return res


class _Base(unittest.TestCase):
    def setUp(self):
        self.dialog = FakeDialog()
        self.ctx = make_handler_context(dialog=self.dialog)
        # Avoid resolving a real meshtastic CLI binary.
        self.ctx._meshtastic_path = "/usr/bin/true"
        self.handler = MeshtasticdDeviceMQTTHandler()
        self.handler.set_context(self.ctx)

    # The setters end with wait_for_enter(); make it a no-op in tests.
    def _patches(self, save_return):
        return (
            patch('handlers.meshtasticd_mqtt.subprocess.run', side_effect=_ok_run),
            patch.object(type(self.ctx), 'wait_for_enter', lambda *a, **k: None),
        )


class TestMqttEnabledPersistenceHonest(_Base):
    """Site: _mqtt_set_enabled (save_device_setting)."""

    def _drive(self, save_return):
        # yesno() confirms the enable/disable prompt.
        self.dialog._yesno_returns = [True]
        with patch('handlers.meshtasticd_mqtt.subprocess.run', side_effect=_ok_run), \
             patch.object(type(self.ctx), 'wait_for_enter', lambda *a, **k: None), \
             patch('utils.device_config_store.save_device_setting',
                   return_value=save_return):
            self.handler._mqtt_set_enabled(True)

    def test_warns_when_not_saved(self):
        """Radio write OK but persist failed -> dialog must say so."""
        self._drive(save_return=False)
        text = (self.dialog.last_msgbox_text or "")
        self.assertTrue(
            "not saved" in text.lower() or "restart" in text.lower(),
            f"expected persistence warning in success dialog, got: {text!r}",
        )

    def test_no_warning_when_saved(self):
        """Radio write OK and persist OK -> no persistence warning."""
        self._drive(save_return=True)
        text = (self.dialog.last_msgbox_text or "")
        self.assertNotIn("not saved", text.lower())
        self.assertNotIn("will be lost", text.lower())


class TestMqttTopicPersistenceHonest(_Base):
    """Site: _mqtt_set_topic (save_device_setting)."""

    def _drive(self, save_return):
        # inputbox() supplies the root topic.
        self.dialog._inputbox_returns = ["msh"]
        with patch('handlers.meshtasticd_mqtt.subprocess.run', side_effect=_ok_run), \
             patch.object(type(self.ctx), 'wait_for_enter', lambda *a, **k: None), \
             patch('utils.device_config_store.save_device_setting',
                   return_value=save_return):
            self.handler._mqtt_set_topic()

    def test_warns_when_not_saved(self):
        self._drive(save_return=False)
        text = (self.dialog.last_msgbox_text or "")
        self.assertTrue(
            "not saved" in text.lower() or "restart" in text.lower(),
            f"expected persistence warning in success dialog, got: {text!r}",
        )

    def test_no_warning_when_saved(self):
        self._drive(save_return=True)
        text = (self.dialog.last_msgbox_text or "")
        self.assertNotIn("not saved", text.lower())
        self.assertNotIn("will be lost", text.lower())


if __name__ == '__main__':
    unittest.main()
