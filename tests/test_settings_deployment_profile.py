"""The Deployment Profile menu item must be able to succeed.

THE DEFECT PINNED (2026-09-15): ``_configure_deployment_profile`` did

    from utils.deployment_profiles import get_profile, save_profile
    profile = get_profile(choice)
    save_profile(choice)

``get_profile`` does not exist in that module (the real names are
``get_profile_by_name`` / ``load_profile`` / ``load_or_detect_profile``) and
``save_profile()`` takes a ``ProfileDefinition``, not a string. The
``except Exception`` handler turned the ImportError into
"Failed to set profile: ..." on EVERY attempt, so the item was decorative
while its own help text claimed "Profiles control which menu sections are
visible" — false twice over.

RED-FIRST: ``test_profile_selection_actually_succeeds`` fails against the
pre-fix tree with the Error dialog.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from handlers.settings import SettingsHandler  # noqa: E402
from utils.deployment_profiles import list_profiles, get_profile_by_name  # noqa: E402


def _handler(menu_returns):
    h = SettingsHandler()
    dialog = FakeDialog()
    dialog._menu_returns = list(menu_returns)
    h.ctx = make_handler_context(dialog=dialog)
    return h, dialog


def _titles(dialog):
    return [c[1][0] for c in dialog.calls if c[0] == 'msgbox']


class TestProfileSwitcherWorks:

    def test_profile_selection_actually_succeeds(self):
        """The red-first test: pre-fix this always hit the Error dialog."""
        h, dialog = _handler(['gateway'])
        with patch('utils.deployment_profiles.save_profile',
                   return_value=True) as save:
            h._configure_deployment_profile()

        titles = _titles(dialog)
        assert titles, "the action must say something"
        assert not any("Error" in t or "Failed" in t for t in titles), (
            f"pre-fix behaviour: every selection errored. Got {titles}"
        )
        assert any("Updated" in t for t in titles)

        # and it must pass a ProfileDefinition, never a bare string
        (arg,), _ = save.call_args
        assert not isinstance(arg, str), (
            "save_profile() takes a ProfileDefinition; passing the name "
            "string is the original bug"
        )
        assert arg.name.value == 'gateway'

    def test_context_flags_are_updated_from_the_chosen_profile(self):
        h, dialog = _handler(['gateway'])
        with patch('utils.deployment_profiles.save_profile', return_value=True):
            h._configure_deployment_profile()
        expected = get_profile_by_name('gateway').feature_flags
        assert h.ctx.feature_flags == expected
        assert h.ctx.feature_flags is not expected, "must not alias the registry"


class TestProfileSwitcherIsHonestAboutFailure:

    def test_a_refused_write_is_not_reported_as_success(self):
        """save_profile() returns False when deployment.json is unreadable —
        it refuses rather than clobber the fleet role. Claiming success over
        that refusal is the MF020 class."""
        h, dialog = _handler(['gateway'])
        with patch('utils.deployment_profiles.save_profile', return_value=False):
            h._configure_deployment_profile()
        titles = _titles(dialog)
        assert any("Not Saved" in t for t in titles), titles
        assert not any("Updated" in t for t in titles)

    def test_a_raising_write_is_reported(self):
        h, dialog = _handler(['gateway'])
        with patch('utils.deployment_profiles.save_profile',
                   side_effect=OSError("read-only fs")):
            h._configure_deployment_profile()
        titles = _titles(dialog)
        assert any("Not Saved" in t for t in titles)

    def test_failed_save_does_not_mutate_the_live_context(self):
        h, dialog = _handler(['gateway'])
        before = dict(h.ctx.feature_flags)
        with patch('utils.deployment_profiles.save_profile', return_value=False):
            h._configure_deployment_profile()
        assert h.ctx.feature_flags == before, (
            "the in-memory profile must not drift from what is on disk"
        )


class TestProfileListComesFromTheSSOT:

    def test_menu_offers_exactly_the_registered_profiles(self):
        """The screen used to carry its own hardcoded five-entry list, which
        would go stale the moment a profile was added (hfm #5)."""
        h, dialog = _handler([None])
        h._configure_deployment_profile()
        menus = [c for c in dialog.calls if c[0] == 'menu']
        assert menus
        tags = [t for t, _ in menus[0][1][2]]
        assert tags == [p.name.value for p in list_profiles()]

    def test_cancelling_changes_nothing(self):
        h, dialog = _handler([None])
        with patch('utils.deployment_profiles.save_profile') as save:
            h._configure_deployment_profile()
        save.assert_not_called()
