"""Red-first unit tests for four false-green / honest-signal fixes in TUI
handlers (and one analytics store contract).

Each fix replaced a value that *looked* healthy with one that tells the truth
(the #74 / honest_failure_modes class):

1. ANALYTICS  — AnalyticsStore.cleanup_old_data now returns the real count of
   rows deleted (was None), so the handler can say "N record(s) removed" vs
   "No data older than 30 days to remove" instead of asserting "removed" for a
   run that deleted nothing.
2. HARDWARE   — HardwareHandler._enable_spi titles the dialog "SPI Already
   Enabled" when the boot config already had dtparam=spi=on (nothing written
   this run), vs "SPI Enabled" only when it actually wrote a change; also
   appends a raspi-config caveat on a non-zero returncode.
3. WEB_CLIENT — WebClientHandler._offer_meshtasticd_restart no longer claims
   "Web UI should now work without warnings" after a restart; it tells the user
   to VERIFY in the browser.
4. NOMADNET   — NomadNetRNSChecksMixin._validate_nomadnet_config appends a
   WARNING about possible root-ownership when the post-append chown fails (was
   silently ignored).

Infra: handler_test_utils.FakeDialog records last_msgbox_title / last_msgbox_text.

Red-first evidence is documented in each test's docstring and was confirmed by
reverting the source line(s) to the documented OLD behavior, observing the
assertion fail, then restoring (see the agent report).
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# src + launcher_tui on path (same as the other handler tests)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Fix 1 — ANALYTICS: cleanup_old_data returns the real deleted-row count
# ─────────────────────────────────────────────────────────────────────


class TestCleanupReturnsDeletedCount:
    """``AnalyticsStore.cleanup_old_data`` returns the total rows deleted.

    RED-FIRST: the old method returned ``None`` (no ``return`` statement), so
    ``assert n == 3`` would fail with ``None != 3``. The handler's
    ``if removed:`` branch would always take the "No data ... to remove" path
    even when rows were purged.
    """

    def _store(self, tmp_path):
        from utils.analytics import AnalyticsStore
        return AnalyticsStore(tmp_path / "analytics.db")

    def _record_link_budget(self, store, ts):
        from utils.analytics import LinkBudgetSample
        store.record_link_budget(LinkBudgetSample(
            timestamp=ts, source_node="!aaaa", dest_node="!bbbb",
            rssi_dbm=-60.0, snr_db=6.0, distance_km=1.0,
            packet_loss_pct=0.0, link_quality="good",
        ))

    def _record_network_health(self, store, ts):
        from utils.analytics import NetworkHealthMetrics
        store.record_network_health(NetworkHealthMetrics(
            timestamp=ts, online_nodes=3, offline_nodes=1,
            avg_rssi_dbm=-60.0, avg_snr_db=6.0, avg_link_quality_pct=80.0,
            packet_success_rate=0.99, uptime_hours=1.0,
        ))

    def test_returns_exact_old_row_count(self, tmp_path):
        store = self._store(tmp_path)
        old_ts = (datetime.now() - timedelta(days=40)).isoformat()
        recent_ts = (datetime.now() - timedelta(days=2)).isoformat()

        # 3 OLD rows (2 link-budget, 1 health), 2 RECENT rows.
        self._record_link_budget(store, old_ts)
        self._record_link_budget(store, old_ts)
        self._record_network_health(store, old_ts)
        self._record_link_budget(store, recent_ts)
        self._record_network_health(store, recent_ts)

        removed = store.cleanup_old_data(days=30)

        assert isinstance(removed, int), "must return an int, not None"
        assert removed == 3, f"expected exactly 3 OLD rows deleted, got {removed!r}"

    def test_returns_zero_when_nothing_old(self, tmp_path):
        store = self._store(tmp_path)
        recent_ts = (datetime.now() - timedelta(days=2)).isoformat()
        self._record_link_budget(store, recent_ts)
        self._record_network_health(store, recent_ts)

        removed = store.cleanup_old_data(days=30)

        # The honest-zero branch: handler shows "No data older than 30 days".
        assert removed == 0, f"expected 0 when nothing is old, got {removed!r}"


# ─────────────────────────────────────────────────────────────────────
# Fix 2 — HARDWARE: "SPI Already Enabled" vs "SPI Enabled"
# ─────────────────────────────────────────────────────────────────────


class _FakeBootPath:
    """Path stand-in routed through hardware.Path.

    Real /dev/spidev* and /proc/cpuinfo lookups must behave as if absent (no
    SPI device, not-a-Pi handled separately); the boot-config path resolves to
    a caller-controlled tmp file. Only the methods _enable_spi touches are
    implemented.
    """

    def __init__(self, raw, boot_path: Path, boot_target):
        self._raw = str(raw)
        self._boot_path = boot_path
        self._boot_target = boot_target

    # --- /dev/spidev* glob: always empty so _enable_spi proceeds past the
    # "already enabled" device short-circuit ---
    def glob(self, pattern):
        return []

    def exists(self):
        # Only the boot-config candidate we point at "exists".
        return self._raw == self._boot_target

    def read_text(self):
        if self._raw == self._boot_target:
            return self._boot_path.read_text()
        raise FileNotFoundError(self._raw)

    def write_text(self, text):
        if self._raw == self._boot_target:
            return self._boot_path.write_text(text)
        raise FileNotFoundError(self._raw)


def _make_hardware_handler():
    from handlers.hardware import HardwareHandler
    h = HardwareHandler()
    h.set_context(make_handler_context(dialog=FakeDialog()))
    return h


def _drive_enable_spi(handler, boot_file: Path, boot_target: str, raspi_rc):
    """Run _enable_spi with the boot config pointed at *boot_file*.

    *boot_target* is which of the two discovery candidates "exists"
    ('/boot/firmware/config.txt' or '/boot/config.txt'). *raspi_rc* is the
    fake raspi-config returncode (None => raspi-config not present on PATH).
    """
    import handlers.hardware as hw

    def fake_path(arg):
        return _FakeBootPath(arg, boot_file, boot_target)

    run_mock = MagicMock()
    if raspi_rc is not None:
        run_mock.return_value = MagicMock(returncode=raspi_rc)
    which_ret = "/usr/bin/raspi-config" if raspi_rc is not None else None

    handler.ctx.dialog._yesno_returns = [True]  # confirm the enable prompt

    with patch.object(hw, "Path", side_effect=fake_path), \
         patch.object(hw.subprocess, "run", run_mock), \
         patch.object(hw.shutil, "which", return_value=which_ret), \
         patch.object(handler, "_is_raspberry_pi", return_value=True):
        handler._enable_spi()


class TestEnableSpiTitleHonest:
    """``_enable_spi`` distinguishes a no-op run from an actual write.

    RED-FIRST: the old code always titled the final dialog "SPI Enabled"
    regardless of whether ``needs_write`` was True — Case A (already-enabled
    config) would have asserted-equal against "SPI Already Enabled" and FAILED
    (the dialog said "SPI Enabled").
    """

    def test_already_enabled_config_titles_already_enabled(self, tmp_path):
        boot = tmp_path / "config.txt"
        boot.write_text("dtparam=spi=on\ndtoverlay=spi0-0cs\n")
        h = _make_hardware_handler()

        _drive_enable_spi(h, boot, "/boot/firmware/config.txt", raspi_rc=0)

        assert h.ctx.dialog.last_msgbox_title == "SPI Already Enabled", \
            f"got {h.ctx.dialog.last_msgbox_title!r}"
        # Nothing should have been re-written / mangled.
        assert boot.read_text() == "dtparam=spi=on\ndtoverlay=spi0-0cs\n"

    def test_empty_config_titles_spi_enabled(self, tmp_path):
        boot = tmp_path / "config.txt"
        boot.write_text("")  # nothing enabled yet
        h = _make_hardware_handler()

        _drive_enable_spi(h, boot, "/boot/firmware/config.txt", raspi_rc=0)

        assert h.ctx.dialog.last_msgbox_title == "SPI Enabled", \
            f"got {h.ctx.dialog.last_msgbox_title!r}"
        # The enabling lines were actually written.
        written = boot.read_text()
        assert "dtparam=spi=on" in written
        assert "dtoverlay=spi0-0cs" in written

    def test_nonzero_raspi_config_appends_caveat(self, tmp_path):
        """Non-zero raspi-config rc surfaces as a caveat, not hidden behind a
        green 'enabled'. (Secondary honest-signal leg of the same fix.)"""
        boot = tmp_path / "config.txt"
        boot.write_text("")
        h = _make_hardware_handler()

        _drive_enable_spi(h, boot, "/boot/firmware/config.txt", raspi_rc=1)

        assert h.ctx.dialog.last_msgbox_title == "SPI Enabled"
        assert "raspi-config returned a non-zero status" in h.ctx.dialog.last_msgbox_text


# ─────────────────────────────────────────────────────────────────────
# Fix 3 — WEB_CLIENT: post-restart dialog tells user to VERIFY, not "works"
# ─────────────────────────────────────────────────────────────────────


def _make_web_client_handler():
    from handlers.web_client import WebClientHandler
    h = WebClientHandler()
    h.set_context(make_handler_context(dialog=FakeDialog()))
    return h


class TestOfferRestartHonestSuccess:
    """``_offer_meshtasticd_restart`` no longer over-claims after restart.

    RED-FIRST: the old success dialog read "...the Web UI should now work
    without warnings." The new text tells the user to *verify* in the browser.
    The 'should now work without warnings' assertion below would have FAILED
    against the new text only because the old phrasing has been removed; and
    the 'verify' assertion would have FAILED against the OLD text (it had no
    'verify'). Both directions pin the wording change.
    """

    def test_success_dialog_asks_to_verify_not_asserts_works(self):
        h = _make_web_client_handler()
        h.ctx.dialog._yesno_returns = [True]  # yes, restart

        import utils.service_check as svc
        with patch.object(svc, "apply_config_and_restart",
                          return_value=(True, "ok")):
            h._offer_meshtasticd_restart()

        text = (h.ctx.dialog.last_msgbox_text or "")
        assert h.ctx.dialog.last_msgbox_title == "Restarted"
        assert "verify" in text.lower(), \
            f"success dialog should ask the user to verify; got: {text!r}"
        assert "should now work without warnings" not in text, \
            "success dialog must not over-claim the old wording"

    def test_failed_restart_still_shows_honest_failure(self):
        """Guard the other branch: a failed restart is reported, not glossed."""
        h = _make_web_client_handler()
        h.ctx.dialog._yesno_returns = [True]

        import utils.service_check as svc
        with patch.object(svc, "apply_config_and_restart",
                          return_value=(False, "unit failed")):
            h._offer_meshtasticd_restart()

        assert h.ctx.dialog.last_msgbox_title == "Restart Failed"
        assert "unit failed" in (h.ctx.dialog.last_msgbox_text or "")


class TestUpdateSslConfigRejectsInvalidYaml:
    """Bonus: ``_update_meshtasticd_ssl_config`` returns False when its regex
    edit produces invalid YAML (so the caller never claims the cert is wired).

    RED-FIRST: before the yaml.safe_load guard, a malformed edit was written
    and True returned, letting _offer_meshtasticd_restart run. We feed a config
    whose Webserver block, once the SSLCert/SSLKey lines are spliced in, parses
    as a YAML indentation error -> returns False.

    The method does a function-local ``from pathlib import Path`` and hardcodes
    ``Path("/etc/meshtasticd/config.yaml")``, so we patch ``pathlib.Path`` with
    a selective shim: only that one literal string is redirected to our tmp
    file; every other Path() call passes through untouched.
    """

    def test_invalid_yaml_after_edit_returns_false(self, tmp_path):
        pytest.importorskip("yaml")
        import pathlib
        import yaml as _yaml

        h = _make_web_client_handler()
        cfg = tmp_path / "config.yaml"
        # Tab-indented mapping value -> PyYAML raises on tabs as indentation
        # once the SSLCert/SSLKey lines are spliced under the Webserver block.
        original = "Webserver:\n\tPort: 9443\n"
        cfg.write_text(original)

        real_path = pathlib.Path

        def path_shim(arg=None, *rest):
            if arg == "/etc/meshtasticd/config.yaml":
                return cfg
            return real_path(arg, *rest) if arg is not None else real_path()

        with patch("pathlib.Path", side_effect=path_shim):
            result = h._update_meshtasticd_ssl_config()

        assert result is False, \
            "an edit that yields invalid YAML must report failure, not success"
        # The tmp file must be left UNwritten (no malformed config persisted).
        assert cfg.read_text() == original
        # Sanity: the post-edit shape really is un-loadable, proving teeth.
        with pytest.raises(Exception):
            _yaml.safe_load("Webserver:\n\tPort: 9443\n  SSLCert: x\n")


# ─────────────────────────────────────────────────────────────────────
# Fix 4 — NOMADNET: chown failure appends an ownership WARNING
# ─────────────────────────────────────────────────────────────────────


class _NomadHost:
    """Minimal host exposing the mixin method + the ctx/config-path hooks it
    needs. The mixin expects self.ctx.dialog and self._get_nomadnet_config_path.
    """

    def __init__(self, dialog, config_path):
        self.ctx = make_handler_context(dialog=dialog)
        self._config_path = config_path

    def _get_nomadnet_config_path(self):
        return self._config_path


def _make_nomad_host(config_path):
    from handlers._nomadnet_rns_checks import NomadNetRNSChecksMixin

    class _Host(_NomadHost, NomadNetRNSChecksMixin):
        pass

    return _Host(FakeDialog(), config_path)


class TestNomadnetConfigChownWarning:
    """``_validate_nomadnet_config`` warns when the post-append chown fails.

    RED-FIRST: the old code ran chown but ignored its returncode, so the
    "Config Updated" dialog read only "NomadNet text UI should now work." — no
    WARNING, no ownership note. Asserting WARNING/ownership in the text would
    have FAILED against the old behavior.
    """

    def _config_without_textui(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("[client]\nenable_node = no\n")  # no [textui] section
        return cfg

    def test_chown_failure_appends_ownership_warning(self, tmp_path, monkeypatch):
        cfg = self._config_without_textui(tmp_path)
        host = _make_nomad_host(cfg)
        host.ctx.dialog._yesno_returns = [True]  # yes, add [textui]

        monkeypatch.setenv("SUDO_USER", "meshuser")

        import handlers._nomadnet_rns_checks as nn

        def fake_run(cmd, *a, **kw):
            # the chown invocation -> simulate failure
            return MagicMock(returncode=1)

        with patch.object(nn.subprocess, "run", side_effect=fake_run):
            ok = host._validate_nomadnet_config()

        assert ok is True  # still proceeds to launch
        text = host.ctx.dialog.last_msgbox_text or ""
        assert host.ctx.dialog.last_msgbox_title == "Config Updated"
        assert "WARNING" in text, f"expected an ownership WARNING; got: {text!r}"
        assert ("ownership" in text.lower() or "root-owned" in text.lower()), \
            f"warning must name the ownership/root-owned hazard; got: {text!r}"
        # The [textui] section was actually appended.
        assert "[textui]" in cfg.read_text().lower()

    def test_chown_success_has_no_ownership_warning(self, tmp_path, monkeypatch):
        """Companion: a successful chown leaves NO scary warning."""
        cfg = self._config_without_textui(tmp_path)
        host = _make_nomad_host(cfg)
        host.ctx.dialog._yesno_returns = [True]

        monkeypatch.setenv("SUDO_USER", "meshuser")

        import handlers._nomadnet_rns_checks as nn

        with patch.object(nn.subprocess, "run",
                          return_value=MagicMock(returncode=0)):
            ok = host._validate_nomadnet_config()

        assert ok is True
        text = host.ctx.dialog.last_msgbox_text or ""
        assert host.ctx.dialog.last_msgbox_title == "Config Updated"
        assert "WARNING" not in text
        assert "ownership" not in text.lower()
