"""
Honest-signal tests for BrokerHandler success dialogs (#74 class).

The broker profile dialogs used to claim "Activated"/"Saved"/"Setup Complete"
unconditionally, discarding the real bool returned by the writers
(save_profiles / set_active_profile / save_mqtt_config). A profile write that
hit OSError would still show a green success dialog — a silent no-op reading
as a win.

These tests pin the honest contract: a failed writer (returns False) MUST
surface the FAILURE dialog title, not the success title. The mirror cases
prove a successful writer still shows the success title.

RED-FIRST: each failure-path test FAILS against the old
unconditional-success code (verified by temporarily reverting a site).
"""

import os
import sys
from unittest.mock import patch

import pytest

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


def _make_profile(name="meshforge_private", broker_type="private", is_active=False):
    """A minimal stand-in for a BrokerProfile usable by the detail/delete sites."""
    from utils.broker_profiles import BrokerType

    class _Stub:
        pass

    p = _Stub()
    p.name = name
    p.broker_type = broker_type
    p.is_active = is_active
    p.host = "localhost"
    p.port = 1883
    p.username = ""
    p.password = ""
    p.use_tls = False
    p.channel = "LongFast"
    p.region = "US"
    p.topic_filter = "msh/US/2/e/#"
    p.description = "stub"
    p.display_name = name
    return p, BrokerType


# ---------------------------------------------------------------------------
# set_active_profile "Activated" site (_profile_detail_menu)
# ---------------------------------------------------------------------------

class TestActivateHonestSignal:
    def test_activate_write_failure_shows_failure_title(self):
        """set_active_profile False MUST NOT read as 'Activated'."""
        h = _make_broker()
        profile, _BrokerType = _make_profile()
        profiles = {profile.name: profile}

        # Drive _profile_detail_menu: choose 'activate', then 'back'.
        h.ctx.dialog._menu_returns = ["activate", "back"]

        with patch("handlers.broker.set_active_profile", return_value=False):
            h._profile_detail_menu(profile.name, profiles)

        assert h.ctx.dialog.last_msgbox_title == "Save Failed", (
            f"expected failure dialog, got {h.ctx.dialog.last_msgbox_title!r}"
        )

    def test_activate_write_success_shows_activated(self):
        h = _make_broker()
        profile, _BrokerType = _make_profile()
        profiles = {profile.name: profile}

        h.ctx.dialog._menu_returns = ["activate", "back"]

        with patch("handlers.broker.set_active_profile", return_value=True):
            h._profile_detail_menu(profile.name, profiles)

        assert h.ctx.dialog.last_msgbox_title == "Activated"


# ---------------------------------------------------------------------------
# profile delete "Deleted" site (_profile_detail_menu)
# ---------------------------------------------------------------------------

class TestDeleteHonestSignal:
    def test_delete_write_failure_shows_failure_title(self):
        h = _make_broker()
        profile, _BrokerType = _make_profile(is_active=False)
        profiles = {profile.name: profile}

        # menu -> 'delete', then yesno -> confirm True
        h.ctx.dialog._menu_returns = ["delete"]
        h.ctx.dialog._yesno_returns = [True]

        with patch("handlers.broker.save_profiles", return_value=False):
            h._profile_detail_menu(profile.name, profiles)

        assert h.ctx.dialog.last_msgbox_title == "Save Failed"

    def test_delete_write_success_shows_deleted(self):
        h = _make_broker()
        profile, _BrokerType = _make_profile(is_active=False)
        profiles = {profile.name: profile}

        h.ctx.dialog._menu_returns = ["delete"]
        h.ctx.dialog._yesno_returns = [True]

        with patch("handlers.broker.save_profiles", return_value=True):
            h._profile_detail_menu(profile.name, profiles)

        assert h.ctx.dialog.last_msgbox_title == "Deleted"


# ---------------------------------------------------------------------------
# _setup_public_broker "Public Broker Set" site (narrow save_profiles site)
# ---------------------------------------------------------------------------

class TestSetupPublicBrokerHonestSignal:
    def test_public_save_failure_shows_failure_title(self):
        h = _make_broker()
        # region, channel inputbox returns
        h.ctx.dialog._inputbox_returns = ["US", "LongFast"]

        with patch("handlers.broker.save_profiles", return_value=False), \
             patch.object(h, "_apply_profile_to_mqtt"):
            h._setup_public_broker()

        assert h.ctx.dialog.last_msgbox_title == "Save Failed"

    def test_public_save_success_shows_public_broker_set(self):
        h = _make_broker()
        h.ctx.dialog._inputbox_returns = ["US", "LongFast"]

        with patch("handlers.broker.save_profiles", return_value=True), \
             patch.object(h, "_apply_profile_to_mqtt"):
            h._setup_public_broker()

        assert h.ctx.dialog.last_msgbox_title == "Public Broker Set"


# ---------------------------------------------------------------------------
# _setup_custom_broker "Custom Broker Saved" site
# ---------------------------------------------------------------------------

class TestSetupCustomBrokerHonestSignal:
    def test_custom_save_failure_shows_failure_title(self):
        h = _make_broker()
        # name, host, port, username, password?(skipped, username blank),
        # region, channel, root_topic
        h.ctx.dialog._inputbox_returns = [
            "my_broker", "mqtt.example.com", "1883", "", "US", "LongFast", "msh/US/2/e",
        ]

        with patch("handlers.broker.save_profiles", return_value=False), \
             patch.object(h, "_apply_profile_to_mqtt"):
            h._setup_custom_broker()

        assert h.ctx.dialog.last_msgbox_title == "Save Failed"

    def test_custom_save_success_shows_custom_broker_saved(self):
        h = _make_broker()
        h.ctx.dialog._inputbox_returns = [
            "my_broker", "mqtt.example.com", "1883", "", "US", "LongFast", "msh/US/2/e",
        ]

        with patch("handlers.broker.save_profiles", return_value=True), \
             patch.object(h, "_apply_profile_to_mqtt"):
            h._setup_custom_broker()

        assert h.ctx.dialog.last_msgbox_title == "Custom Broker Saved"


# ---------------------------------------------------------------------------
# _apply_profile_to_mqtt save_mqtt_config site (imported from handlers.mqtt)
# ---------------------------------------------------------------------------

class TestApplyProfileHonestSignal:
    def _profile(self):
        class _P:
            name = "p"

            def to_tui_config(self):
                return {}

        return _P()

    def test_apply_write_failure_shows_failure_dialog(self):
        h = _make_broker()
        with patch("handlers.mqtt.load_mqtt_config", return_value={}), \
             patch("handlers.mqtt.save_mqtt_config", return_value=False):
            h._apply_profile_to_mqtt(self._profile())

        assert h.ctx.dialog.last_msgbox_title == "Apply Failed"

    def test_apply_write_success_is_silent(self):
        """On success the apply stays quiet (callers show their own success)."""
        h = _make_broker()
        with patch("handlers.mqtt.load_mqtt_config", return_value={}), \
             patch("handlers.mqtt.save_mqtt_config", return_value=True):
            h._apply_profile_to_mqtt(self._profile())

        # No failure dialog surfaced.
        assert h.ctx.dialog.last_msgbox_title is None
