"""In-app install integration — MF018 Class 5 (install) close-out.

The In-Domain Principle (foundations/in_domain_principle.md) says the operator
fixes MeshForge from inside MeshForge — including installs. Class 5's gap was
install-failure fallbacks that punt to a shell ("Try manually: pipx install
nomadnet", "Install: sudo apt install mosquitto-clients").

These tests pin the close-out:
  * NomadNet install delegates to the canonical env-rigorous installer
    (scripts/install_nomadnet.sh — the SSOT, not a hand-rolled pipx duplicate;
    [[feedback_version_env_rigor]] / honest_failure_modes #5), captured in-app.
  * A zero exit with a missing binary FAILS LOUD (never reports a broken
    install as success — Issue #24 / version-env-rigor point 5).
  * An install failure offers an in-app RETRY (propose_remediation), never a
    shell instruction.
  * The broker connection-test, on a missing mosquitto_pub, offers the in-app
    installer instead of "sudo apt install mosquitto-clients".
  * No MF018 shell-escape pattern survives in _nomadnet_install_utils.py.
"""

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


def _make_nomadnet():
    from handlers.nomadnet import NomadNetHandler
    h = NomadNetHandler()
    h.set_context(make_handler_context())
    return h


def _make_broker():
    from handlers.broker import BrokerHandler
    h = BrokerHandler()
    h.set_context(make_handler_context())
    return h


def _proc(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ----------------------------------------------------------------------
# NomadNet: delegate to the canonical installer (env-rigorous SSOT)
# ----------------------------------------------------------------------
class TestNomadNetInstallDelegatesToCanonical:

    def test_install_runs_canonical_installer_when_missing(self):
        """Confirmed install shells `bash scripts/install_nomadnet.sh`."""
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [True]  # confirm install
        captured = []

        def fake_run(argv, **kwargs):
            captured.append(argv)
            return _proc(0, "nomadnet.service: ACTIVE\n")

        # not installed before; present after the install
        with patch.object(h, '_is_nomadnet_installed', side_effect=[False, True]):
            with patch('subprocess.run', side_effect=fake_run):
                h._install_nomadnet()

        assert any(
            isinstance(a, list) and a[0] == 'bash'
            and str(a[1]).endswith('scripts/install_nomadnet.sh')
            for a in captured
        ), f"expected canonical installer invocation, got {captured}"

    def test_install_already_installed_short_circuits(self):
        """Already installed → message, never runs the installer."""
        h = _make_nomadnet()
        called = []
        with patch.object(h, '_is_nomadnet_installed', return_value=True):
            with patch('subprocess.run', side_effect=lambda *a, **k: called.append(a)):
                h._install_nomadnet()
        assert called == [], "must not run installer when already installed"
        assert "already installed" in (h.ctx.dialog.last_msgbox_text or "").lower()

    def test_install_declined_does_nothing(self):
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [False]  # decline
        called = []
        with patch.object(h, '_is_nomadnet_installed', return_value=False):
            with patch('subprocess.run', side_effect=lambda *a, **k: called.append(a)):
                h._install_nomadnet()
        assert called == [], "declining must not run the installer"

    def test_install_failure_offers_in_app_retry_not_shell(self):
        """A failed install offers an in-app retry — never a shell command."""
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [True]   # confirm install
        h.ctx.dialog._menu_returns = ["0"]     # decline the retry menu
        with patch.object(h, '_is_nomadnet_installed', return_value=False):
            with patch('subprocess.run',
                       side_effect=lambda *a, **k: _proc(3, "alignment drifted")):
                h._install_nomadnet()

        # A remediation menu (the in-app retry) was offered.
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert menu_calls, "a failed install must offer an in-app remediation menu"

        # No dialog text tells the user to run pipx/apt by hand.
        all_text = " ".join(
            str(arg) for c in h.ctx.dialog.calls for arg in c[1]
        ).lower()
        assert "pipx install" not in all_text
        assert "apt install" not in all_text
        assert "try manually" not in all_text


class TestRunCanonicalInstallerFailsLoud:

    def test_zero_exit_but_missing_binary_fails_loud(self):
        """rc==0 yet nomadnet absent → (False, ...) — the #24 guard."""
        h = _make_nomadnet()
        with patch.object(h, '_is_nomadnet_installed', return_value=False):
            with patch('subprocess.run', side_effect=lambda *a, **k: _proc(0, "done")):
                ok, msg = h._run_canonical_installer()
        assert ok is False
        assert msg  # carries an explanation

    def test_success_when_rc_zero_and_binary_present(self):
        h = _make_nomadnet()
        with patch.object(h, '_is_nomadnet_installed', return_value=True):
            with patch('subprocess.run',
                       side_effect=lambda *a, **k: _proc(0, "ACTIVE")):
                ok, msg = h._run_canonical_installer()
        assert ok is True

    def test_nonzero_exit_fails_with_captured_output(self):
        h = _make_nomadnet()
        with patch.object(h, '_is_nomadnet_installed', return_value=True):
            with patch('subprocess.run',
                       side_effect=lambda *a, **k: _proc(4, "service slow")):
                ok, msg = h._run_canonical_installer()
        assert ok is False
        assert "service slow" in msg

    def test_missing_installer_script_fails_loud(self):
        h = _make_nomadnet()
        with patch.object(Path, 'is_file', return_value=False):
            ran = []
            with patch('subprocess.run', side_effect=lambda *a, **k: ran.append(a)):
                ok, msg = h._run_canonical_installer()
        assert ok is False
        assert ran == [], "must not run a missing installer"

    def test_installer_timeout_fails_loud(self):
        import subprocess as _sp
        h = _make_nomadnet()
        with patch('subprocess.run',
                   side_effect=_sp.TimeoutExpired(cmd="bash", timeout=600)):
            ok, msg = h._run_canonical_installer()
        assert ok is False
        assert "time" in msg.lower()


# ----------------------------------------------------------------------
# Broker: missing mosquitto_pub offers the in-app installer
# ----------------------------------------------------------------------
class TestBrokerMissingClientsOffersInAppInstall:

    def _profile(self):
        p = MagicMock()
        p.host = "localhost"
        p.port = 1883
        p.username = ""
        p.password = ""
        return p

    def test_missing_clients_offers_in_app_install(self):
        h = _make_broker()
        h.ctx.dialog._yesno_returns = [True]  # accept in-app install
        installed = []
        with patch('handlers.broker.load_profiles', return_value={}):
            with patch('handlers.broker.get_active_profile',
                       return_value=self._profile()):
                with patch('subprocess.run', side_effect=FileNotFoundError()):
                    with patch.object(h, '_install_mosquitto',
                                      side_effect=lambda: installed.append(True)):
                        h._test_mosquitto_connection()
        assert installed == [True], "should route to the in-app installer"

    def test_missing_clients_no_shell_instruction(self):
        h = _make_broker()
        h.ctx.dialog._yesno_returns = [False]  # decline
        with patch('handlers.broker.load_profiles', return_value={}):
            with patch('handlers.broker.get_active_profile',
                       return_value=self._profile()):
                with patch('subprocess.run', side_effect=FileNotFoundError()):
                    with patch.object(h, '_install_mosquitto'):
                        h._test_mosquitto_connection()
        all_text = " ".join(
            str(arg) for c in h.ctx.dialog.calls for arg in c[1]
        ).lower()
        assert "apt install" not in all_text


# ----------------------------------------------------------------------
# Source-scan: no MF018 shell-escape survives in the install utils
# ----------------------------------------------------------------------
class TestNoShellEscapesRemain:

    def _mf018_hits(self, rel):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        import lint
        path = os.path.join(os.path.dirname(__file__), '..', rel)
        count, _ = lint._count_in_domain_escapes(path)
        return count

    def test_nomadnet_install_utils_has_no_escapes(self):
        assert self._mf018_hits(
            'src/launcher_tui/handlers/_nomadnet_install_utils.py') == 0

    def test_broker_has_no_escapes(self):
        assert self._mf018_hits('src/launcher_tui/handlers/broker.py') == 0

    def test_system_tools_has_no_escapes(self):
        assert self._mf018_hits('src/launcher_tui/handlers/system_tools.py') == 0
