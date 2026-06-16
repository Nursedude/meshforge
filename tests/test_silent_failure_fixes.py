"""Red-first unit tests for four SILENT-FAILURE fixes in MeshForge TUI handlers.

The mirror of the false-green class (calibrated_claims.md / honest_failure_modes.md):
a user ACTION whose underlying operation FAILED, but the user was told nothing —
or worse, was told it succeeded. Each fix here turns one such silent failure into
a visible Error/Warning dialog. These tests drive the FAILURE path and assert the
dialog is now shown.

Each test documents its RED-FIRST proof inline: what the OLD (pre-fix) code did
and why this assertion would have failed against it.

Test infra: handler_test_utils.FakeDialog + make_handler_context.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Ensure src + launcher_tui + the tests/ dir itself are importable
# (handler_test_utils lives in tests/; the others mirror its own header).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402


# =========================================================================
# Fix 1: meshtasticd_lora._lora_set_txrx — non-integer enable pin must Error
#        + RETURN before write_overlay (was: silent pass, then "TX/RX updated")
# =========================================================================

class TestLoraTxRxNonIntegerPin:
    """A non-integer TXen/RXen enable pin must surface an Error and NOT write.

    RED-FIRST: the pre-fix code's ``except ValueError: pass`` silently dropped
    the bad value, then fell through to ``write_overlay(...)`` +
    ``_offer_restart("TX/RX pins updated")`` — claiming success for a value that
    was never stored. With the OLD body, ``fd.last_msgbox_title`` would be None
    (no Error dialog) and ``write_overlay`` WOULD have been called, so BOTH
    assertions below fail against the old behavior. Confirmed by reverting the
    two ``msgbox("Error", ...) ; return`` lines to ``pass`` (see report).
    """

    def _make_handler(self, fd):
        from handlers.meshtasticd_lora import MeshtasticdLoRaHandler
        handler = MeshtasticdLoRaHandler()
        handler.set_context(make_handler_context(dialog=fd))
        return handler

    def test_non_integer_txen_errors_and_skips_write(self):
        fd = FakeDialog()
        handler = self._make_handler(fd)

        # The method reads two inputboxes: TXen first, then RXen.
        # Give a non-integer TXen ("GPIO13") — it must Error and return BEFORE
        # the RXen prompt and BEFORE write_overlay.
        fd._inputbox_returns = ["GPIO13", "21"]

        with patch('handlers.meshtasticd_lora.read_overlay', return_value={}), \
             patch('handlers.meshtasticd_lora.write_overlay') as mock_write:
            handler._lora_set_txrx()

        # Error dialog shown...
        assert fd.last_msgbox_title == "Error"
        assert "integer" in fd.last_msgbox_text.lower()
        # ...and we returned BEFORE writing the overlay (no false "updated").
        mock_write.assert_not_called()

    def test_non_integer_rxen_errors_and_skips_write(self):
        """TXen valid but RXen non-integer — same Error+return contract."""
        fd = FakeDialog()
        handler = self._make_handler(fd)
        fd._inputbox_returns = ["12", "GPIO_BAD"]

        with patch('handlers.meshtasticd_lora.read_overlay', return_value={}), \
             patch('handlers.meshtasticd_lora.write_overlay') as mock_write:
            handler._lora_set_txrx()

        assert fd.last_msgbox_title == "Error"
        assert "integer" in fd.last_msgbox_text.lower()
        mock_write.assert_not_called()

    def test_valid_integer_pins_do_write(self):
        """Sanity / non-regression: valid integers DO reach write_overlay."""
        fd = FakeDialog()
        handler = self._make_handler(fd)
        fd._inputbox_returns = ["12", "21"]

        with patch('handlers.meshtasticd_lora.read_overlay', return_value={}), \
             patch('handlers.meshtasticd_lora.write_overlay',
                   return_value=False) as mock_write:
            handler._lora_set_txrx()

        # No Error dialog for the happy path; write was attempted.
        assert fd.last_msgbox_title != "Error"
        mock_write.assert_called_once()


# =========================================================================
# Fix 2: channel_config._apply_gateway_template — preset-set returncode != 0
#        but channel-name set OK must Error "did NOT apply"
#        (was: ignored returncode, showed "Success ... Radio: {preset}")
# =========================================================================

class TestGatewayTemplatePresetFailure:
    """Preset CLI exits nonzero, channel name succeeds -> honest Error, not Success.

    RED-FIRST: the pre-fix code did
    ``if name_result.success: msgbox("Success", "... Radio: {preset} ...")``
    with NO check of ``preset_result.returncode``. With a returncode=1 preset
    and a successful channel-name set, the OLD body shows title "Success" — so
    ``fd.last_msgbox_title == "Error"`` and the "did NOT apply" text assertion
    both fail against it. Confirmed by reverting the branch to the old
    unconditional Success msgbox (see report).
    """

    def _make_handler(self, fd):
        from handlers.channel_config import ChannelConfigHandler
        handler = ChannelConfigHandler()
        # src_dir must point at the real src/ so the in-method
        # ``from commands import meshtastic`` resolves.
        src_dir = Path(__file__).resolve().parent.parent / "src"
        handler.set_context(make_handler_context(dialog=fd, src_dir=src_dir))
        return handler

    def test_preset_failure_with_name_success_shows_error(self):
        fd = FakeDialog()
        handler = self._make_handler(fd)
        fd._yesno_returns = [True]  # confirm "Apply ... template?"

        preset_run = Mock(returncode=1, stderr=b"boom: preset rejected")
        name_ok = Mock(success=True, message="channel set")

        with patch('subprocess.run', return_value=preset_run), \
             patch('commands.meshtastic.set_channel_name', return_value=name_ok):
            handler._apply_gateway_template("standard")

        assert fd.last_msgbox_title == "Error"
        text = fd.last_msgbox_text.lower()
        assert "did not apply" in text or "preset" in text
        # Must NOT have claimed success.
        assert "ready for rns bridging" not in text

    def test_both_succeed_shows_success(self):
        """Sanity / non-regression: returncode 0 + name OK -> real Success."""
        fd = FakeDialog()
        handler = self._make_handler(fd)
        fd._yesno_returns = [True]

        preset_run = Mock(returncode=0, stderr=b"")
        name_ok = Mock(success=True, message="channel set")

        with patch('subprocess.run', return_value=preset_run), \
             patch('commands.meshtastic.set_channel_name', return_value=name_ok):
            handler._apply_gateway_template("standard")

        assert fd.last_msgbox_title == "Success"


# =========================================================================
# Fix 3: first_run._wizard_step_region — settings write OSError must
#        msgbox("Region Not Saved", ...) (was: logger.debug only, no dialog)
# =========================================================================

class TestWizardRegionWriteFailure:
    """A failed settings.json write must surface "Region Not Saved".

    RED-FIRST: the pre-fix ``except (OSError, ValueError)`` body was only
    ``logger.debug("Failed to save region setting: %s", e)`` — no dialog. With
    the OLD body the OSError is swallowed silently, ``fd.last_msgbox_title``
    stays None, and ``== "Region Not Saved"`` fails. Confirmed by reverting the
    except body to the lone logger.debug (see report).
    """

    def _make_handler(self, fd):
        from handlers.first_run import FirstRunHandler
        handler = FirstRunHandler()
        handler.set_context(make_handler_context(dialog=fd))
        return handler

    def test_oserror_on_write_shows_region_not_saved(self):
        fd = FakeDialog()
        handler = self._make_handler(fd)
        # Menu returns a valid region code (US is the first MESHTASTIC_REGIONS row).
        fd._menu_returns = ["US"]

        real_write_text = Path.write_text

        def fail_settings_write(self, data, *a, **kw):
            # Only the settings.json write should explode; everything else
            # (mkdir etc.) is left alone. mkdir does not call write_text.
            if str(self).endswith("settings.json"):
                raise OSError("disk full")
            return real_write_text(self, data, *a, **kw)

        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td)
            with patch('handlers.first_run.get_real_user_home',
                       return_value=fake_home), \
                 patch.object(Path, 'write_text', fail_settings_write):
                handler._wizard_step_region()

        assert fd.last_msgbox_title == "Region Not Saved"
        assert "US" in fd.last_msgbox_text

    def test_successful_write_shows_no_error_dialog(self):
        """Sanity / non-regression: a clean write surfaces no dialog at all."""
        fd = FakeDialog()
        handler = self._make_handler(fd)
        fd._menu_returns = ["US"]

        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td)
            with patch('handlers.first_run.get_real_user_home',
                       return_value=fake_home):
                handler._wizard_step_region()

            # No "Region Not Saved" dialog on the happy path...
            assert fd.last_msgbox_title != "Region Not Saved"
            # ...and the region landed on disk (assert inside the tmpdir scope —
            # TemporaryDirectory deletes the tree on __exit__).
            saved = (fake_home / ".config" / "meshforge" / "settings.json")
            assert saved.exists()
            assert '"region": "US"' in saved.read_text()


# =========================================================================
# Fix 4: _nomadnet_io_ops._open_editor — chown after edit fails (returncode!=0)
#        must msgbox("Ownership Warning", ...) (was: logger.debug swallow)
# =========================================================================

class TestNomadNetEditChownFailure:
    """A failed post-edit chown must surface an "Ownership Warning".

    RED-FIRST: the pre-fix code did ``except (...): logger.debug(...)`` and had
    NO dialog when chown returned nonzero — a root-owned config that NomadNet
    can't read on next launch, with the user told nothing. With the OLD body
    ``fd.last_msgbox_title`` stays None, so ``== "Ownership Warning"`` fails.
    Confirmed by reverting the ``if not chown_ok:`` block to a bare
    logger.debug (see report).
    """

    def _make_host(self, fd):
        # _open_editor lives on the NomadNetIOOpsMixin; build a tiny host that
        # carries it plus a ctx with our FakeDialog.
        from handlers._nomadnet_io_ops import NomadNetIOOpsMixin

        class _Host(NomadNetIOOpsMixin):
            def __init__(self, ctx):
                self.ctx = ctx

        return _Host(make_handler_context(dialog=fd))

    def test_chown_failure_shows_ownership_warning(self):
        fd = FakeDialog()
        host = self._make_host(fd)

        def run_side_effect(argv, *a, **kw):
            # The editor invocation succeeds; the chown call returns nonzero.
            if argv and argv[0] == 'chown':
                return Mock(returncode=1, stderr=b"Operation not permitted")
            return Mock(returncode=0)

        with tempfile.NamedTemporaryFile(suffix="config", delete=False) as tf:
            cfg_path = Path(tf.name)
        try:
            with patch.dict(os.environ, {"SUDO_USER": "meshuser"}), \
                 patch('handlers._nomadnet_io_ops.shutil.which',
                       return_value="/bin/nano"), \
                 patch('handlers._nomadnet_io_ops.subprocess.run',
                       side_effect=run_side_effect):
                host._open_editor(cfg_path)
        finally:
            cfg_path.unlink(missing_ok=True)

        assert fd.last_msgbox_title == "Ownership Warning"
        assert "meshuser" in fd.last_msgbox_text

    def test_chown_raises_shows_ownership_warning(self):
        """An OSError raised by the chown subprocess also warns (chown_ok stays False)."""
        fd = FakeDialog()
        host = self._make_host(fd)

        def run_side_effect(argv, *a, **kw):
            if argv and argv[0] == 'chown':
                raise OSError("chown blew up")
            return Mock(returncode=0)

        with tempfile.NamedTemporaryFile(suffix="config", delete=False) as tf:
            cfg_path = Path(tf.name)
        try:
            with patch.dict(os.environ, {"SUDO_USER": "meshuser"}), \
                 patch('handlers._nomadnet_io_ops.shutil.which',
                       return_value="/bin/nano"), \
                 patch('handlers._nomadnet_io_ops.subprocess.run',
                       side_effect=run_side_effect):
                host._open_editor(cfg_path)
        finally:
            cfg_path.unlink(missing_ok=True)

        assert fd.last_msgbox_title == "Ownership Warning"

    def test_chown_success_shows_no_warning(self):
        """Sanity / non-regression: chown returncode 0 -> no warning dialog."""
        fd = FakeDialog()
        host = self._make_host(fd)

        with tempfile.NamedTemporaryFile(suffix="config", delete=False) as tf:
            cfg_path = Path(tf.name)
        try:
            with patch.dict(os.environ, {"SUDO_USER": "meshuser"}), \
                 patch('handlers._nomadnet_io_ops.shutil.which',
                       return_value="/bin/nano"), \
                 patch('handlers._nomadnet_io_ops.subprocess.run',
                       return_value=Mock(returncode=0)):
                host._open_editor(cfg_path)
        finally:
            cfg_path.unlink(missing_ok=True)

        assert fd.last_msgbox_title != "Ownership Warning"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
