"""
Unit tests for NomadNet handler and pre-launch readiness gate.

Tests cover:
  - Pure-logic RNS readiness decision matrix (Phase 1)
  - Pre-launch check integration with dialog (Phase 2)
  - Launch error handling (Phase 3)
  - Handler structure, menu navigation, status display (Phase 4)
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context

from handlers._nomadnet_prelaunch import RNSReadiness, check_rns_readiness


# ======================================================================
# Phase 1: Pure-logic readiness gate
# ======================================================================

class TestRNSReadinessDecisionMatrix:
    """Test every cell of the RNS readiness decision matrix."""

    def test_rnsd_running_shared_instance_users_match(self):
        """Happy path: everything is ready."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.rnsd_running is True
        assert r.shared_instance is True
        assert r.user_match is True
        assert r.warning is None

    def test_rnsd_running_shared_instance_users_mismatch(self):
        """User mismatch: can launch with warning."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_user="root",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.user_match is False
        assert r.warning is not None
        assert "root" in r.warning
        assert "pi" in r.warning

    def test_rnsd_running_no_shared_instance(self):
        """rnsd running but shared instance not available."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=False,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is False
        assert r.rnsd_running is True
        assert r.shared_instance is False
        assert "initializing" in r.reason.lower() or "not available" in r.reason.lower()

    def test_rnsd_not_running_no_shared_instance(self):
        """Nothing running: cannot launch."""
        r = check_rns_readiness(
            rnsd_running=False,
            shared_instance_available=False,
        )
        assert r.can_launch is False
        assert r.rnsd_running is False
        assert r.shared_instance is False
        assert "not running" in r.reason.lower()

    def test_rnsd_not_running_shared_instance_available(self):
        """Standalone RNS instance: can launch."""
        r = check_rns_readiness(
            rnsd_running=False,
            shared_instance_available=True,
        )
        assert r.can_launch is True
        assert r.rnsd_running is False
        assert r.shared_instance is True

    def test_no_user_info_rnsd_running(self):
        """rnsd running, no user info: can launch if shared instance up."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_user=None,
            launch_user=None,
        )
        assert r.can_launch is True
        assert r.user_match is None
        assert r.warning is None

    def test_no_launch_user_with_rnsd_user(self):
        """rnsd has user but launch user unknown: user_match is None."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_user="pi",
            launch_user=None,
        )
        assert r.can_launch is True
        assert r.user_match is None

    def test_suggestion_points_to_diagnostics_when_blocked(self):
        """Blocked results suggest using RNS Diagnostics."""
        r = check_rns_readiness(
            rnsd_running=False,
            shared_instance_available=False,
        )
        assert r.can_launch is False
        assert "diagnostics" in r.suggestion.lower()

    def test_readiness_dataclass_fields(self):
        """Verify all expected fields exist on RNSReadiness."""
        r = check_rns_readiness(True, True, "pi", "pi")
        assert hasattr(r, 'can_launch')
        assert hasattr(r, 'reason')
        assert hasattr(r, 'suggestion')
        assert hasattr(r, 'warning')
        assert hasattr(r, 'rnsd_running')
        assert hasattr(r, 'shared_instance')
        assert hasattr(r, 'user_match')


# ======================================================================
# Phase 2: Pre-launch check integration with TUI dialog
# ======================================================================

def _make_nomadnet():
    """Create a NomadNetHandler with test context."""
    from handlers.nomadnet import NomadNetHandler
    h = NomadNetHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


class TestPrelaunchCheckIntegration:
    """Test _check_rns_for_nomadnet dialog integration."""

    @pytest.fixture(autouse=True)
    def _stub_mesh_iface_probes(self):
        """Stub Meshtastic-iface probes so these tests only exercise the
        downstream RNS readiness path. Mesh-iface probes are covered by
        TestCheckMeshIfaceBeforeLaunch."""
        with patch(
            'handlers._rns_interface_mgr.find_blocking_interfaces',
            return_value=[],
        ):
            with patch(
                'handlers._rns_diagnostics_engine.check_rns_interface_health',
                return_value=[],
            ):
                yield

    @patch('handlers._nomadnet_rns_checks.socket')
    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_passes_when_ready(self, mock_info, mock_socket):
        """When RNS is ready, check returns True with no dialog."""
        mock_info.return_value = {'available': True}
        h = _make_nomadnet()
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is True
        # No menu dialog should have been shown (only possibly infobox)
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert len(menu_calls) == 0

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_blocked_user_picks_diagnostics(self, mock_info):
        """When blocked, user picks diagnostics -> returns False."""
        mock_info.return_value = {'available': False}
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["diagnostics"]
        mock_diag = MagicMock()
        with patch.object(h, '_get_rnsd_user', return_value=None):
            with patch.object(h, '_get_rns_diagnostics_handler', return_value=mock_diag):
                result = h._check_rns_for_nomadnet()
        assert result is False

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_blocked_user_picks_continue(self, mock_info):
        """When blocked, user picks continue -> returns True."""
        mock_info.return_value = {'available': False}
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["continue"]
        with patch.object(h, '_get_rnsd_user', return_value=None):
            result = h._check_rns_for_nomadnet()
        assert result is True

    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_blocked_user_cancels(self, mock_info):
        """When blocked, user cancels -> returns False."""
        mock_info.return_value = {'available': False}
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]  # Default = cancel
        with patch.object(h, '_get_rnsd_user', return_value=None):
            result = h._check_rns_for_nomadnet()
        assert result is False

    @patch('handlers._nomadnet_rns_checks.socket')
    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_user_mismatch_warning(self, mock_info, mock_socket):
        """User mismatch shows a menu and, on 'continue', allows launch."""
        mock_info.return_value = {'available': True}
        h = _make_nomadnet()
        # First menu call is the user-mismatch prompt; pick "continue".
        h.ctx.dialog._menu_returns = ["continue"]
        with patch.object(h, '_get_rnsd_user', return_value='root'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is True
        # Verify a mismatch menu was shown with both users named.
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert menu_calls, "Expected a mismatch menu to be shown"
        mismatch = menu_calls[0]
        title, body, _ = mismatch[1]
        assert 'mismatch' in title.lower()
        assert 'root' in body and 'pi' in body


# ======================================================================
# Phase 3: stderr capture on launch failure
# ======================================================================

class TestLaunchErrorHandling:
    """Test NomadNet launch error capture."""

    @patch('subprocess.run')
    @patch('handlers.nomadnet.clear_screen')
    def test_launch_textui_stderr_on_failure(self, mock_clear, mock_run):
        """When NomadNet exits non-zero, stderr is shown."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="ConnectionRefusedError: [Errno 111]",
        )
        h = _make_nomadnet()
        with patch.object(h, '_find_nomadnet_binary', return_value='/usr/bin/nomadnet'):
            with patch.object(h, '_ensure_lxmf_exclusive', return_value=True):
                with patch.object(h, '_fix_user_directory_ownership', return_value=True):
                    with patch.object(h, '_validate_nomadnet_config', return_value=True):
                        with patch.object(h, '_check_rns_for_nomadnet', return_value=True):
                            with patch.object(h, '_get_rns_config_for_user', return_value=None):
                                with patch.object(h, '_get_wrapper_command',
                                                  return_value=['/usr/bin/nomadnet', '--textui']):
                                    with patch('builtins.input', return_value=''):
                                        with patch.dict(os.environ, {}, clear=False):
                                            # Remove SUDO_USER if present
                                            os.environ.pop('SUDO_USER', None)
                                            h._launch_nomadnet_textui()

    @patch('subprocess.run')
    @patch('handlers.nomadnet.clear_screen')
    def test_launch_textui_success(self, mock_clear, mock_run):
        """When NomadNet exits 0, clean exit message."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        h = _make_nomadnet()
        with patch.object(h, '_find_nomadnet_binary', return_value='/usr/bin/nomadnet'):
            with patch.object(h, '_ensure_lxmf_exclusive', return_value=True):
                with patch.object(h, '_fix_user_directory_ownership', return_value=True):
                    with patch.object(h, '_validate_nomadnet_config', return_value=True):
                        with patch.object(h, '_check_rns_for_nomadnet', return_value=True):
                            with patch.object(h, '_get_rns_config_for_user', return_value=None):
                                with patch.object(h, '_get_wrapper_command',
                                                  return_value=['/usr/bin/nomadnet', '--textui']):
                                    with patch('builtins.input', return_value=''):
                                        with patch.dict(os.environ, {}, clear=False):
                                            os.environ.pop('SUDO_USER', None)
                                            h._launch_nomadnet_textui()


# ======================================================================
# Phase 4: Handler structure and navigation
# ======================================================================

class TestNomadNetHandlerStructure:
    """Test handler registration and structure."""

    def test_handler_id(self):
        h = _make_nomadnet()
        assert h.handler_id == "nomadnet"

    def test_menu_section(self):
        h = _make_nomadnet()
        assert h.menu_section == "mesh_networks"

    def test_menu_items(self):
        h = _make_nomadnet()
        items = h.menu_items()
        assert len(items) >= 1
        tag, desc, flag = items[0]
        assert tag == "nomadnet"

    def test_execute_dispatches_to_menu(self):
        h = _make_nomadnet()
        with patch.object(h, '_nomadnet_menu') as mock:
            h.execute("nomadnet")
            mock.assert_called_once()

    def test_execute_ignores_unknown_action(self):
        h = _make_nomadnet()
        # Should not raise
        h.execute("unknown_action")

    def test_nomadnet_menu_back_exits(self):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]
        h._nomadnet_menu()


class TestNomadNetStatusDisplay:
    """Test status display variations."""

    @patch('shutil.which', return_value=None)
    def test_status_not_installed(self, mock_which):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]  # Exit after status
        # Should handle gracefully when not installed
        with patch.object(h, '_is_nomadnet_installed', return_value=False):
            with patch.object(h, '_nomadnet_status') as mock_status:
                # Just verify the handler can be created and invoked
                pass


class TestNomadNetBinaryDetection:
    """Test binary path discovery."""

    @patch('shutil.which', return_value='/usr/local/bin/nomadnet')
    def test_found_in_path(self, mock_which):
        h = _make_nomadnet()
        result = h._find_nomadnet_binary()
        assert result == '/usr/local/bin/nomadnet'

    @patch('shutil.which', return_value=None)
    def test_found_in_local_bin(self, mock_which):
        h = _make_nomadnet()
        with patch('handlers._nomadnet_install_utils.get_real_user_home',
                   return_value=Path('/home/pi')):
            with patch.object(Path, 'exists', return_value=True):
                result = h._find_nomadnet_binary()
                assert result is not None

    @patch('shutil.which', return_value=None)
    def test_not_found_shows_dialog(self, mock_which):
        h = _make_nomadnet()
        with patch('handlers._nomadnet_install_utils.get_real_user_home',
                   return_value=Path('/home/pi')):
            # Make candidate not exist
            result = h._find_nomadnet_binary()
            if result is None:
                # Should have shown msgbox
                msgbox_calls = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
                assert len(msgbox_calls) >= 1


class TestNomadNetStopFlow:
    """Test stop process flow."""

    def test_stop_not_running(self):
        h = _make_nomadnet()
        # Issue #45: _stop_nomadnet now consults _nomadnet_service_state
        # before the pkill path; mock it so the test doesn't pick up
        # live `systemctl --user is-active` state on the dev box.
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=False)):
            with patch.object(h, '_is_nomadnet_running',
                              return_value=False):
                h._stop_nomadnet()
        msgbox_calls = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
        assert any('Not Running' in str(c) for c in msgbox_calls)

    @patch('subprocess.run')
    def test_stop_running_confirmed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=False)):
            with patch.object(h, '_is_nomadnet_running',
                              side_effect=[True, False]):
                h._stop_nomadnet()

    def test_stop_running_cancelled(self):
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [False]
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=False)):
            with patch.object(h, '_is_nomadnet_running',
                              return_value=True):
                h._stop_nomadnet()


# ======================================================================
# Phase 2b: Degraded rnsd (rnsd_healthy parameter)
# ======================================================================

class TestRNSReadinessDegraded:
    """Test rnsd_healthy parameter in the readiness gate."""

    def test_rnsd_running_shared_instance_degraded(self):
        """rnsd running + shared instance + unhealthy: can launch with warning."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_healthy=False,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.rnsd_healthy is False
        assert r.warning is not None
        assert "degraded" in r.warning.lower()

    def test_rnsd_healthy_unknown(self):
        """rnsd running + shared instance + health unknown: can launch, no warning."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_healthy=None,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.rnsd_healthy is None
        assert r.warning is None

    def test_rnsd_healthy_true(self):
        """rnsd running + shared instance + healthy: can launch, no warning."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_healthy=True,
            rnsd_user="pi",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert r.rnsd_healthy is True
        assert r.warning is None

    def test_degraded_warning_overrides_user_mismatch(self):
        """Degraded warning takes priority over user mismatch."""
        r = check_rns_readiness(
            rnsd_running=True,
            shared_instance_available=True,
            rnsd_healthy=False,
            rnsd_user="root",
            launch_user="pi",
        )
        assert r.can_launch is True
        assert "degraded" in r.warning.lower()
        # User mismatch warning is not shown when degraded
        assert "root" not in r.warning


class TestPrelaunchDegradedFlow:
    """Test _handle_degraded_rnsd dialog flow."""

    @pytest.fixture(autouse=True)
    def _stub_mesh_iface_probes(self):
        with patch(
            'handlers._rns_interface_mgr.find_blocking_interfaces',
            return_value=[],
        ):
            with patch(
                'handlers._rns_diagnostics_engine.check_rns_interface_health',
                return_value=[],
            ):
                yield

    @patch('handlers._nomadnet_rns_checks.socket')
    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_degraded_user_picks_restart(self, mock_info, mock_socket):
        """Degraded rnsd, user picks restart -> restart_rnsd called."""
        mock_info.return_value = {'available': True}
        # Socket connect raises -> degraded
        mock_sock_inst = MagicMock()
        mock_sock_inst.connect.side_effect = OSError("Connection refused")
        mock_socket.socket.return_value = mock_sock_inst
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["restart"]
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                with patch('handlers._rns_repair.restart_rnsd', return_value=True):
                    result = h._check_rns_for_nomadnet()
        assert result is True

    @patch('handlers._nomadnet_rns_checks.socket')
    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_degraded_user_picks_continue(self, mock_info, mock_socket):
        """Degraded rnsd, user picks continue -> launches anyway."""
        mock_info.return_value = {'available': True}
        mock_sock_inst = MagicMock()
        mock_sock_inst.connect.side_effect = OSError("Connection refused")
        mock_socket.socket.return_value = mock_sock_inst
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["continue"]
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is True

    @patch('handlers._nomadnet_rns_checks.socket')
    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_degraded_user_cancels(self, mock_info, mock_socket):
        """Degraded rnsd, user cancels -> returns False."""
        mock_info.return_value = {'available': True}
        mock_sock_inst = MagicMock()
        mock_sock_inst.connect.side_effect = OSError("Connection refused")
        mock_socket.socket.return_value = mock_sock_inst
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]  # cancel
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is False

    @patch('handlers._nomadnet_rns_checks.socket')
    @patch('handlers._nomadnet_rns_checks.get_rns_shared_instance_info')
    def test_prelaunch_healthy_skips_degraded_dialog(self, mock_info, mock_socket):
        """Healthy rnsd skips degraded dialog entirely."""
        mock_info.return_value = {'available': True}
        # Socket connects fine -> healthy
        h = _make_nomadnet()
        with patch.object(h, '_get_rnsd_user', return_value='pi'):
            with patch.dict(os.environ, {'SUDO_USER': 'pi'}):
                result = h._check_rns_for_nomadnet()
        assert result is True
        # No menu dialog should have been shown
        menu_calls = [c for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert len(menu_calls) == 0


class TestConfigValidation:
    """Test NomadNet config validation."""

    def test_validate_no_config(self):
        """No config file: should return True (NomadNet creates default)."""
        h = _make_nomadnet()
        with patch.object(h, '_get_nomadnet_config_path', return_value=Path('/tmp/nonexistent')):
            result = h._validate_nomadnet_config()
        assert result is True

    def test_validate_config_has_textui(self):
        """Config with [textui] section: should return True."""
        h = _make_nomadnet()
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='_config', delete=False) as f:
            f.write("[reticulum]\n\n[textui]\ntheme = dark\n")
            f.flush()
            with patch.object(h, '_get_nomadnet_config_path', return_value=Path(f.name)):
                result = h._validate_nomadnet_config()
        os.unlink(f.name)
        assert result is True


# ======================================================================
# Phase 5: User-mismatch detection
# ======================================================================

class TestCheckRnsdUserMatch:
    """_check_rnsd_user_match warns only when rnsd_user != nomadnet_user."""

    def test_no_rnsd_skips_check_silently(self):
        h = _make_nomadnet()
        with patch.object(h, '_get_rnsd_user', return_value=None):
            result = h._check_rnsd_user_match()
        assert result is True
        assert not any(c[0] == 'menu' for c in h.ctx.dialog.calls)

    def test_match_skips_check_silently(self):
        h = _make_nomadnet()
        with patch.object(h, '_get_rnsd_user', return_value='wh6gxz'):
            with patch.dict(os.environ, {'SUDO_USER': 'wh6gxz'}):
                result = h._check_rnsd_user_match()
        assert result is True
        assert not any(c[0] == 'menu' for c in h.ctx.dialog.calls)

    def test_mismatch_shows_menu_and_continue_returns_true(self):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["continue"]
        with patch.object(h, '_get_rnsd_user', return_value='root'):
            with patch.dict(os.environ, {'SUDO_USER': 'wh6gxz'}):
                result = h._check_rnsd_user_match()
        assert result is True
        menu = [c for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert len(menu) == 1
        title, body, _ = menu[0][1]
        assert 'mismatch' in title.lower()
        assert 'root' in body and 'wh6gxz' in body

    def test_mismatch_cancel_returns_false(self):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]
        with patch.object(h, '_get_rnsd_user', return_value='root'):
            with patch.dict(os.environ, {'SUDO_USER': 'wh6gxz'}):
                result = h._check_rnsd_user_match()
        assert result is False

    def test_mismatch_diagnostics_invokes_handler_and_returns_false(self):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["diagnostics"]
        mock_diag = MagicMock()
        with patch.object(h, '_get_rnsd_user', return_value='root'):
            with patch.object(h, '_get_rns_diagnostics_handler',
                              return_value=mock_diag):
                with patch.dict(os.environ, {'SUDO_USER': 'wh6gxz'}):
                    result = h._check_rnsd_user_match()
        assert result is False
        mock_diag._rns_diagnostics.assert_called_once()


# ======================================================================
# Phase 6: Meshtastic RNS-interface pre-launch probe
# ======================================================================

class TestCheckMeshIfaceBeforeLaunch:
    """_check_mesh_iface_before_launch — warn only on Meshtastic blockers."""

    def test_no_blockers_returns_true_silently(self):
        h = _make_nomadnet()
        with patch(
            'handlers._rns_interface_mgr.find_blocking_interfaces',
            return_value=[],
        ):
            with patch(
                'handlers._rns_diagnostics_engine.check_rns_interface_health',
                return_value=[],
            ):
                result = h._check_mesh_iface_before_launch()
        assert result is True
        assert not any(c[0] == 'menu' for c in h.ctx.dialog.calls)

    def test_non_mesh_blocker_is_ignored(self):
        h = _make_nomadnet()
        with patch(
            'handlers._rns_interface_mgr.find_blocking_interfaces',
            return_value=[('DeadTCP', 'host unreachable', 'bring host up')],
        ):
            with patch(
                'handlers._rns_diagnostics_engine.check_rns_interface_health',
                return_value=[],
            ):
                result = h._check_mesh_iface_before_launch()
        assert result is True
        assert not any(c[0] == 'menu' for c in h.ctx.dialog.calls)

    def test_mesh_blocker_continue_returns_true(self):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["continue"]
        with patch(
            'handlers._rns_interface_mgr.find_blocking_interfaces',
            return_value=[('MeshLF', 'meshtasticd not running', 'sudo start')],
        ):
            result = h._check_mesh_iface_before_launch()
        assert result is True
        menu = [c for c in h.ctx.dialog.calls if c[0] == 'menu']
        assert len(menu) == 1
        title, body, _ = menu[0][1]
        assert 'meshtastic' in title.lower()
        assert 'meshtasticd not running' in body

    def test_mesh_blocker_cancel_returns_false(self):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = [None]
        with patch(
            'handlers._rns_interface_mgr.find_blocking_interfaces',
            return_value=[
                ('MeshLF', 'meshtasticd running but TCP port closed', 'wait'),
            ],
        ):
            result = h._check_mesh_iface_before_launch()
        assert result is False

    def test_mesh_blocker_diagnostics_delegates(self):
        h = _make_nomadnet()
        h.ctx.dialog._menu_returns = ["diagnostics"]
        mock_diag = MagicMock()
        with patch(
            'handlers._rns_interface_mgr.find_blocking_interfaces',
            return_value=[
                ('MeshLF', 'meshtasticd not running', 'sudo start'),
            ],
        ):
            with patch.object(h, '_get_rns_diagnostics_handler',
                              return_value=mock_diag):
                result = h._check_mesh_iface_before_launch()
        assert result is False
        mock_diag._rns_diagnostics.assert_called_once()


# ======================================================================
# Phase 7: Interactive config seeding (never overwrite, user-owned)
# ======================================================================

class TestEnsureInteractiveConfigDirSeeding:
    """_ensure_interactive_config_dir seeds from default, never overwrites."""

    def test_seed_from_default_when_missing(self, tmp_path):
        h = _make_nomadnet()
        default_home = tmp_path / "home"
        default_dir = default_home / ".nomadnetwork"
        default_dir.mkdir(parents=True)
        default_cfg = default_dir / "config"
        default_cfg.write_text("[client]\nenable_node = yes\n")

        interactive_dir = default_home / ".nomadnetwork-interactive"

        with patch(
            'handlers.nomadnet.get_real_user_home', return_value=default_home,
        ):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('SUDO_USER', None)
                ok = h._ensure_interactive_config_dir(interactive_dir)

        assert ok is True
        assert (interactive_dir / "config").exists()
        assert (interactive_dir / "config").read_text() == default_cfg.read_text()
        # User saw the seed confirmation dialog
        assert any(
            c[0] == 'msgbox' and 'Seeded' in c[1][0]
            for c in h.ctx.dialog.calls
        )

    def test_does_not_overwrite_existing_interactive_config(self, tmp_path):
        h = _make_nomadnet()
        default_home = tmp_path / "home"
        default_dir = default_home / ".nomadnetwork"
        default_dir.mkdir(parents=True)
        (default_dir / "config").write_text("DEFAULT_CONTENT\n")

        interactive_dir = default_home / ".nomadnetwork-interactive"
        interactive_dir.mkdir()
        existing = interactive_dir / "config"
        existing.write_text("EXISTING_USER_EDITS\n")

        with patch(
            'handlers.nomadnet.get_real_user_home', return_value=default_home,
        ):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('SUDO_USER', None)
                ok = h._ensure_interactive_config_dir(interactive_dir)

        assert ok is True
        # Critical: user's existing edits preserved.
        assert existing.read_text() == "EXISTING_USER_EDITS\n"
        # No "Seeded" confirmation because no seeding happened.
        assert not any(
            c[0] == 'msgbox' and 'Seeded' in c[1][0]
            for c in h.ctx.dialog.calls
        )

    def test_no_default_means_no_seeding(self, tmp_path):
        h = _make_nomadnet()
        default_home = tmp_path / "home"
        default_home.mkdir()
        # No ~/.nomadnetwork/config
        interactive_dir = default_home / ".nomadnetwork-interactive"

        with patch(
            'handlers.nomadnet.get_real_user_home', return_value=default_home,
        ):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('SUDO_USER', None)
                ok = h._ensure_interactive_config_dir(interactive_dir)

        assert ok is True
        assert interactive_dir.exists()
        # No config file seeded (NomadNet will generate blank on first launch)
        assert not (interactive_dir / "config").exists()


# ======================================================================
# Phase 8: Identity-scoped stop
# ======================================================================

class TestStopNomadnetScoped:
    """_stop_nomadnet(config_dir=X) only kills processes using X."""

    def test_no_competing_clients_msgbox(self):
        h = _make_nomadnet()
        target = Path("/home/<user>/.nomadnetwork-interactive")
        with patch(
            'handlers._lxmf_utils.find_competing_clients', return_value=[],
        ):
            h._stop_nomadnet(config_dir=target)
        # Should show "Not Running" dialog, no kill invoked.
        assert any(
            c[0] == 'msgbox' and 'Not Running' in c[1][0]
            for c in h.ctx.dialog.calls
        )

    def test_kills_identified_pids_on_confirm(self):
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [True]
        target = Path("/home/<user>/.nomadnetwork-interactive")
        # find_competing_clients returns (pid, client, cfg) tuples; strings.
        with patch(
            'handlers._lxmf_utils.find_competing_clients',
            side_effect=[
                [('12345', 'nomadnet', str(target))],  # initial — pids found
                [],                                    # post-kill — clean
            ],
        ):
            with patch.object(h, '_kill_nomadnet_pids') as mock_kill:
                h._stop_nomadnet(config_dir=target)
        mock_kill.assert_called_once_with([12345])
        # "Stopped" message shown because nothing remains.
        assert any(
            c[0] == 'msgbox' and 'Stopped' in c[1][0]
            for c in h.ctx.dialog.calls
        )

    def test_cancel_does_not_kill(self):
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [False]
        target = Path("/home/<user>/.nomadnetwork-interactive")
        with patch(
            'handlers._lxmf_utils.find_competing_clients',
            return_value=[('12345', 'nomadnet', str(target))],
        ):
            with patch.object(h, '_kill_nomadnet_pids') as mock_kill:
                h._stop_nomadnet(config_dir=target)
        mock_kill.assert_not_called()


# ======================================================================
# Phase 9: Menu subtitle — Meshtastic iface state
# ======================================================================

class TestMeshIfaceSubtitleState:
    """_mesh_iface_subtitle_state summarizes for the main NomadNet menu."""

    def test_no_config_returns_empty_string(self, tmp_path):
        h = _make_nomadnet()
        missing = tmp_path / "nonexistent_config"
        with patch(
            'utils.paths.ReticulumPaths.get_config_file',
            return_value=missing,
        ):
            assert h._mesh_iface_subtitle_state() == ""

    def test_no_mesh_iface_says_not_configured(self, tmp_path):
        h = _make_nomadnet()
        cfg = tmp_path / "config"
        cfg.write_text("[[SomeOther]]\n  type = TCPClientInterface\n")
        with patch(
            'utils.paths.ReticulumPaths.get_config_file',
            return_value=cfg,
        ):
            assert h._mesh_iface_subtitle_state() == "Meshtastic iface: not configured"

    def test_blocked_mesh_iface_is_shown(self, tmp_path):
        h = _make_nomadnet()
        cfg = tmp_path / "config"
        cfg.write_text(
            "[[MeshLF]]\n  type = Meshtastic_Interface\n  enabled = yes\n"
        )
        with patch(
            'utils.paths.ReticulumPaths.get_config_file',
            return_value=cfg,
        ):
            with patch(
                'handlers._rns_interface_mgr.find_blocking_interfaces',
                return_value=[
                    ('MeshLF', 'meshtasticd not running', 'sudo start'),
                ],
            ):
                result = h._mesh_iface_subtitle_state()
        assert result.startswith("Meshtastic iface: BLOCKED")
        assert "meshtasticd not running" in result


# ======================================================================
# Issue #45 — tmux-wrapped systemd user service awareness
# ======================================================================


def _service_state(**overrides):
    """Default service-state dict; override keys per-test."""
    base = {
        "unit_installed": False,
        "active": False,
        "enabled": False,
        "sub_state": "",
        "main_pid": 0,
        "n_restarts": 0,
        "tmux_session": False,
        "error": None,
    }
    base.update(overrides)
    return base


class TestServiceStateDetection:
    """_nomadnet_service_state() single source of truth."""

    def test_state_not_installed(self):
        h = _make_nomadnet()
        with patch('pathlib.Path.exists', return_value=False):
            with patch.object(h, '_user_systemctl_text',
                              return_value=(4, "")):
                with patch.object(h, '_tmux_has_session',
                                  return_value=False):
                    state = h._nomadnet_service_state()
        assert state["unit_installed"] is False
        assert state["active"] is False
        assert state["tmux_session"] is False

    def test_state_active_with_tmux(self):
        h = _make_nomadnet()

        def fake_text(verbs, timeout=10):
            if verbs[:2] == ['is-active', 'nomadnet']:
                return 0, "active"
            if verbs[:2] == ['is-enabled', 'nomadnet']:
                return 0, "enabled"
            if verbs[:2] == ['show', 'nomadnet']:
                return 0, (
                    "SubState=running\nMainPID=12345\nNRestarts=0"
                )
            return 4, ""

        with patch('pathlib.Path.exists', return_value=True):
            with patch.object(h, '_user_systemctl_text',
                              side_effect=fake_text):
                with patch.object(h, '_tmux_has_session',
                                  return_value=True):
                    state = h._nomadnet_service_state()
        assert state["unit_installed"] is True
        assert state["active"] is True
        assert state["enabled"] is True
        assert state["sub_state"] == "running"
        assert state["main_pid"] == 12345
        assert state["n_restarts"] == 0
        assert state["tmux_session"] is True

    def test_state_crash_loop_signals(self):
        h = _make_nomadnet()

        def fake_text(verbs, timeout=10):
            if verbs[:2] == ['is-active', 'nomadnet']:
                return 3, "activating"
            if verbs[:2] == ['is-enabled', 'nomadnet']:
                return 0, "enabled"
            if verbs[:2] == ['show', 'nomadnet']:
                return 0, (
                    "SubState=auto-restart\nMainPID=0\nNRestarts=5"
                )
            return 4, ""

        with patch('pathlib.Path.exists', return_value=True):
            with patch.object(h, '_user_systemctl_text',
                              side_effect=fake_text):
                with patch.object(h, '_tmux_has_session',
                                  return_value=False):
                    state = h._nomadnet_service_state()
        assert state["active"] is False
        assert state["n_restarts"] == 5
        assert state["sub_state"] == "auto-restart"

    def test_service_state_line_inactive(self):
        h = _make_nomadnet()
        line = h._service_state_line(
            _service_state(unit_installed=True, enabled=True),
        )
        assert "inactive" in line.lower()
        assert "enabled" in line.lower()

    def test_service_state_line_active(self):
        h = _make_nomadnet()
        line = h._service_state_line(_service_state(
            unit_installed=True, active=True, sub_state="running",
            main_pid=42, tmux_session=True,
        ))
        assert "active" in line.lower()
        assert "42" in line
        assert "tmux" in line.lower()


class TestWarnIfServiceActive:
    """_warn_if_service_active returns True when caller may proceed."""

    def test_proceeds_when_service_inactive(self):
        h = _make_nomadnet()
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=False)):
            assert h._warn_if_service_active("t", "b") is True
        # No dialog emitted when service is inactive
        kinds = [c[0] for c in h.ctx.dialog.calls]
        assert "yesno" not in kinds

    def test_prompts_when_service_active(self):
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [False]
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=True)):
            assert h._warn_if_service_active("t", "b") is False
        kinds = [c[0] for c in h.ctx.dialog.calls]
        assert "yesno" in kinds

    def test_proceeds_when_operator_confirms(self):
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=True)):
            assert h._warn_if_service_active("t", "b") is True


class TestStopRefusesWhenServiceManaged:
    """_stop_nomadnet short-circuits when the user unit is active."""

    def test_global_stop_refuses_when_active(self):
        h = _make_nomadnet()
        # config_dir=None triggers the "global stop" pkill path.
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(
                              unit_installed=True, active=True,
                              sub_state="running",
                          )):
            with patch('subprocess.run') as mock_run:
                h._stop_nomadnet()
                # pkill must NOT have been called
                for call in mock_run.call_args_list:
                    args = call.args[0] if call.args else []
                    assert 'pkill' not in args
        # Operator was told to use Service Control
        text = (h.ctx.dialog.last_msgbox_text or "").lower()
        assert "systemd" in text or "service control" in text

    def test_global_stop_runs_when_inactive(self):
        h = _make_nomadnet()
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=False)):
            with patch.object(h, '_is_nomadnet_running',
                              return_value=True):
                h.ctx.dialog._yesno_returns = [True]
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value.returncode = 0
                    with patch('time.sleep'):
                        h._stop_nomadnet()
                # At least one subprocess.run invocation
                assert mock_run.called


class TestLaunchRefusesWhenServiceManaged:
    """Raw launches abort early when the tmux-wrapped service is up."""

    def test_textui_refuses_without_proceed(self):
        h = _make_nomadnet()
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=True)):
            h.ctx.dialog._yesno_returns = [False]  # decline "Proceed anyway?"
            with patch.object(h, '_find_nomadnet_binary') as mock_find:
                h._launch_nomadnet_textui()
                mock_find.assert_not_called()

    def test_daemon_refuses_without_proceed(self):
        h = _make_nomadnet()
        with patch.object(h, '_nomadnet_service_state',
                          return_value=_service_state(active=True)):
            h.ctx.dialog._yesno_returns = [False]
            with patch.object(h, '_find_nomadnet_binary') as mock_find:
                h._launch_nomadnet_daemon()
                mock_find.assert_not_called()


class TestConfigToggles:
    """_toggle_config_bool + _write_config_value + section append."""

    def test_toggle_flips_yes_to_no(self, tmp_path):
        h = _make_nomadnet()
        cfg = tmp_path / "config"
        cfg.write_text(
            "[node]\n  enable_node = yes\n  announce_at_start = yes\n"
        )
        with patch.object(h, '_default_config_path', return_value=cfg):
            h.ctx.dialog._yesno_returns = [True]
            h._toggle_config_bool("enable_node")
        out = cfg.read_text()
        assert "enable_node = no" in out
        # Other key untouched
        assert "announce_at_start = yes" in out

    def test_toggle_flips_no_to_yes(self, tmp_path):
        h = _make_nomadnet()
        cfg = tmp_path / "config"
        cfg.write_text("[node]\n  enable_node = no\n")
        with patch.object(h, '_default_config_path', return_value=cfg):
            h.ctx.dialog._yesno_returns = [True]
            h._toggle_config_bool("enable_node")
        assert "enable_node = yes" in cfg.read_text()

    def test_toggle_appends_under_section_when_missing(self, tmp_path):
        h = _make_nomadnet()
        cfg = tmp_path / "config"
        cfg.write_text("[node]\n  display_name = test\n")
        with patch.object(h, '_default_config_path', return_value=cfg):
            h.ctx.dialog._yesno_returns = [True]
            h._toggle_config_bool("enable_node")
        out = cfg.read_text()
        assert "enable_node = yes" in out
        # Appended inside [node] section
        lines = out.splitlines()
        node_idx = next(i for i, line in enumerate(lines)
                        if line.strip() == "[node]")
        # The new line is somewhere after [node] and before EOF
        assert any(
            "enable_node = yes" in line
            for line in lines[node_idx:]
        )

    def test_toggle_refuses_when_config_missing(self, tmp_path):
        h = _make_nomadnet()
        cfg = tmp_path / "config"  # never created
        with patch.object(h, '_default_config_path', return_value=cfg):
            h._toggle_config_bool("enable_node")
        # msgbox issued, no file created
        assert not cfg.exists()
        assert h.ctx.dialog.last_msgbox_title == "No Config"

    def test_toggle_cancel_leaves_config_unchanged(self, tmp_path):
        h = _make_nomadnet()
        cfg = tmp_path / "config"
        cfg.write_text("[node]\n  enable_node = yes\n")
        original = cfg.read_text()
        with patch.object(h, '_default_config_path', return_value=cfg):
            h.ctx.dialog._yesno_returns = [False]  # decline
            h._toggle_config_bool("enable_node")
        assert cfg.read_text() == original

    def test_read_key_values_skips_comments_and_sections(self, tmp_path):
        h = _make_nomadnet()
        cfg = tmp_path / "config"
        cfg.write_text(
            "# comment line\n[node]\n  enable_node = yes\n"
            "  # display_name = ignored\n  node_name = MyNode\n"
        )
        result = h._read_key_values(cfg)
        assert result["enable_node"] == "yes"
        assert result["node_name"] == "MyNode"
        assert "display_name" not in result


class TestNomadNetServiceOpsSudoBridging:
    """_user_systemctl_argv bridges root→real-user for user-scope systemctl."""

    def test_direct_when_no_sudo_user(self):
        h = _make_nomadnet()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SUDO_USER', None)
            argv = h._user_systemctl_argv(['is-active', 'nomadnet'])
        assert argv == ['systemctl', '--user', 'is-active', 'nomadnet']

    def test_wraps_with_sudo_u_when_sudo_user_set(self):
        h = _make_nomadnet()
        fake_pwent = MagicMock()
        fake_pwent.pw_uid = 1000
        with patch.dict(os.environ, {'SUDO_USER': 'pi'}, clear=False):
            with patch('pwd.getpwnam', return_value=fake_pwent):
                argv = h._user_systemctl_argv(['start', 'nomadnet'])
        assert argv[0] == 'sudo'
        assert '-u' in argv and 'pi' in argv
        assert any(
            a == 'XDG_RUNTIME_DIR=/run/user/1000' for a in argv
        )
        assert 'systemctl' in argv and '--user' in argv
        assert argv[-2:] == ['start', 'nomadnet']

    def test_falls_back_when_pwent_lookup_fails(self):
        h = _make_nomadnet()
        with patch.dict(os.environ, {'SUDO_USER': 'ghost'},
                        clear=False):
            with patch('pwd.getpwnam', side_effect=KeyError('ghost')):
                argv = h._user_systemctl_argv(['is-active', 'nomadnet'])
        # Graceful fallback: plain systemctl --user
        assert argv == ['systemctl', '--user', 'is-active', 'nomadnet']

    def test_daemon_reload_omits_unit_name(self):
        """Manager-scoped verbs must NOT append the unit (systemd rejects it)."""
        h = _make_nomadnet()
        captured = []

        def fake_run(argv, **kw):
            captured.append(argv)
            rv = MagicMock()
            rv.returncode = 0
            rv.stdout = ""
            rv.stderr = ""
            return rv

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SUDO_USER', None)
            with patch('subprocess.run', side_effect=fake_run):
                ok, _ = h._systemctl_user('daemon-reload')
        assert ok is True
        assert captured, "subprocess.run never called"
        argv = captured[0]
        assert argv == ['systemctl', '--user', 'daemon-reload']
        assert 'nomadnet' not in argv

    def test_unit_scoped_verb_appends_unit_name(self):
        """start/stop/restart still take the unit name as argv[-1]."""
        h = _make_nomadnet()
        captured = []

        def fake_run(argv, **kw):
            captured.append(argv)
            rv = MagicMock()
            rv.returncode = 0
            rv.stdout = ""
            rv.stderr = ""
            return rv

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SUDO_USER', None)
            with patch('subprocess.run', side_effect=fake_run):
                h._systemctl_user('restart')
        assert captured[0] == [
            'systemctl', '--user', 'restart', 'nomadnet',
        ]


class TestInstallUserUnit:
    """_install_user_unit prereq chain + path substitution.

    The installer must work on a fresh box whether nomadnet came from
    pipx, pip --user, or apt. These tests exercise the prerequisite
    layer (tmux, wrapper, binary discovery) and the ExecStart
    substitution that replaces the `__NOMADNET_EXEC__` placeholder.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _template_with_placeholder(path: Path) -> None:
        """Write a minimal unit file containing the substitution token."""
        path.write_text(
            "[Unit]\nDescription=Fake\n\n[Service]\n"
            "ExecStart=/usr/bin/tmux new-session -d -s nomadnet "
            "'__NOMADNET_EXEC__'\n"
        )

    def _install_env(self, h, fake_template, fake_home,
                     wrapper_argv=None):
        """Context-manager stack of patches common to every install test.

        Caller supplies optional ``wrapper_argv`` to drive the
        path-substitution assertions; default mimics a pip-user install.
        """
        if wrapper_argv is None:
            wrapper_argv = [
                '/home/pi/.local/bin/nomadnet',
                '--rnsconfig', '/etc/reticulum',
            ]
        patches = [
            patch(
                'handlers._nomadnet_service_ops._UNIT_TEMPLATE',
                fake_template,
            ),
            patch(
                'handlers._nomadnet_service_ops.get_real_user_home',
                return_value=fake_home,
            ),
            patch.object(h, '_systemctl_user',
                         return_value=(True, "OK")),
            patch.object(h, '_chown_real_user'),
            patch.object(h, '_find_nomadnet_binary',
                         return_value='/home/pi/.local/bin/nomadnet'),
            patch.object(h, '_create_nomadnet_wrapper',
                         return_value=Path('/home/pi/.config/meshforge/nomadnet_wrapper.py')),
            patch.object(h, '_get_wrapper_command',
                         return_value=wrapper_argv),
        ]
        return patches

    @staticmethod
    def _enter_all(patches):
        mgr = []
        for p in patches:
            mgr.append(p.__enter__())
        return patches

    @staticmethod
    def _exit_all(patches):
        for p in reversed(patches):
            p.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # Happy path + placeholder substitution
    # ------------------------------------------------------------------

    def test_install_writes_unit_and_activates(self, tmp_path):
        """Default pip-user argv substitutes into ExecStart; unit goes active."""
        h = _make_nomadnet()
        fake_home = tmp_path / "home"
        (fake_home / ".config" / "systemd" / "user").mkdir(parents=True)
        fake_template = tmp_path / "nomadnet-user.service"
        self._template_with_placeholder(fake_template)

        patches = self._enter_all(self._install_env(h, fake_template,
                                                     fake_home))
        try:
            with patch('shutil.which', return_value='/usr/bin/tmux'):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value.returncode = 0
                    mock_run.return_value.stdout = ""
                    mock_run.return_value.stderr = ""
                    h._install_user_unit(force=True)
        finally:
            self._exit_all(patches)

        unit = (fake_home / ".config" / "systemd" / "user" /
                "nomadnet.service")
        assert unit.exists()
        text = unit.read_text()
        assert "Description=Fake" in text
        # Placeholder must be gone; substituted argv present
        assert '__NOMADNET_EXEC__' not in text
        assert '/home/pi/.local/bin/nomadnet' in text
        assert '--rnsconfig /etc/reticulum' in text

    def test_install_substitutes_pipx_venv_command(self, tmp_path):
        """Pipx install: venv python + wrapper path land in ExecStart."""
        h = _make_nomadnet()
        fake_home = tmp_path / "home"
        fake_template = tmp_path / "nomadnet-user.service"
        self._template_with_placeholder(fake_template)

        pipx_argv = [
            '/home/pi/.local/share/pipx/venvs/nomadnet/bin/python3',
            '/home/pi/.config/meshforge/nomadnet_wrapper.py',
            '--rnsconfig', '/etc/reticulum',
        ]

        patches = self._enter_all(self._install_env(
            h, fake_template, fake_home, wrapper_argv=pipx_argv,
        ))
        try:
            with patch('shutil.which', return_value='/usr/bin/tmux'):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value.returncode = 0
                    mock_run.return_value.stdout = ""
                    mock_run.return_value.stderr = ""
                    h._install_user_unit(force=True)
        finally:
            self._exit_all(patches)

        unit = (fake_home / ".config" / "systemd" / "user" /
                "nomadnet.service")
        text = unit.read_text()
        assert 'pipx/venvs/nomadnet/bin/python3' in text
        assert 'nomadnet_wrapper.py' in text
        assert '__NOMADNET_EXEC__' not in text
        # shlex.join should keep the argv parts space-separated for tmux
        import shlex as _shlex
        assert _shlex.join(pipx_argv) in text

    # ------------------------------------------------------------------
    # Prerequisite guards
    # ------------------------------------------------------------------

    def test_install_refuses_when_tmux_missing_and_declined(self, tmp_path):
        """Operator declines tmux apt-install → no unit written, no apt call."""
        h = _make_nomadnet()
        fake_home = tmp_path / "home"
        fake_template = tmp_path / "nomadnet-user.service"
        self._template_with_placeholder(fake_template)

        patches = self._enter_all(self._install_env(h, fake_template,
                                                     fake_home))
        try:
            h.ctx.dialog._yesno_returns = [False]  # decline install
            with patch('shutil.which', return_value=None):
                with patch.object(h, '_apt_install') as mock_apt:
                    h._install_user_unit(force=True)
                    mock_apt.assert_not_called()
        finally:
            self._exit_all(patches)

        unit = (fake_home / ".config" / "systemd" / "user" /
                "nomadnet.service")
        assert not unit.exists()

    def test_install_apt_installs_tmux_when_confirmed(self, tmp_path):
        """Operator confirms → _apt_install('tmux') runs before unit write."""
        h = _make_nomadnet()
        fake_home = tmp_path / "home"
        fake_template = tmp_path / "nomadnet-user.service"
        self._template_with_placeholder(fake_template)

        # shutil.which returns None on the first call (gate), then
        # '/usr/bin/tmux' on the summary recheck after apt install.
        which_results = [None, '/usr/bin/tmux', '/usr/bin/tmux']

        def fake_which(_name):
            return (which_results.pop(0) if which_results
                    else '/usr/bin/tmux')

        patches = self._enter_all(self._install_env(h, fake_template,
                                                     fake_home))
        try:
            h.ctx.dialog._yesno_returns = [True]  # accept install
            with patch('shutil.which', side_effect=fake_which):
                with patch.object(
                    h, '_apt_install',
                    return_value=(True, "tmux installed"),
                ) as mock_apt:
                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value.returncode = 0
                        mock_run.return_value.stdout = ""
                        mock_run.return_value.stderr = ""
                        h._install_user_unit(force=True)
                    mock_apt.assert_called_once_with('tmux')
        finally:
            self._exit_all(patches)

        unit = (fake_home / ".config" / "systemd" / "user" /
                "nomadnet.service")
        assert unit.exists()

    def test_install_creates_wrapper_before_writing_unit(self, tmp_path):
        """_create_nomadnet_wrapper must run before the unit file is written."""
        h = _make_nomadnet()
        fake_home = tmp_path / "home"
        fake_template = tmp_path / "nomadnet-user.service"
        self._template_with_placeholder(fake_template)

        call_log: list = []

        def fake_wrapper():
            call_log.append('wrapper')
            return Path('/home/pi/.config/meshforge/nomadnet_wrapper.py')

        real_write_text = Path.write_text

        def recording_write_text(self, *args, **kwargs):
            if self.name == 'nomadnet.service':
                call_log.append('unit_write')
            return real_write_text(self, *args, **kwargs)

        patches = self._enter_all(self._install_env(h, fake_template,
                                                     fake_home))
        try:
            with patch('shutil.which', return_value='/usr/bin/tmux'):
                with patch.object(h, '_create_nomadnet_wrapper',
                                  side_effect=fake_wrapper):
                    with patch.object(Path, 'write_text',
                                      recording_write_text):
                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value.returncode = 0
                            mock_run.return_value.stdout = ""
                            mock_run.return_value.stderr = ""
                            h._install_user_unit(force=True)
        finally:
            self._exit_all(patches)

        assert call_log == ['wrapper', 'unit_write'], (
            f"Expected wrapper before unit_write, got: {call_log}"
        )

    def test_install_refuses_when_placeholder_missing(self, tmp_path):
        """Template without __NOMADNET_EXEC__ triggers the Template Broken guard."""
        h = _make_nomadnet()
        fake_home = tmp_path / "home"
        fake_template = tmp_path / "nomadnet-user.service"
        # Deliberately NO placeholder — should refuse
        fake_template.write_text(
            "[Unit]\nDescription=Fake\n\n[Service]\n"
            "ExecStart=/usr/bin/nomadnet\n"
        )

        patches = self._enter_all(self._install_env(h, fake_template,
                                                     fake_home))
        try:
            with patch('shutil.which', return_value='/usr/bin/tmux'):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value.returncode = 0
                    mock_run.return_value.stdout = ""
                    mock_run.return_value.stderr = ""
                    h._install_user_unit(force=True)
        finally:
            self._exit_all(patches)

        unit = (fake_home / ".config" / "systemd" / "user" /
                "nomadnet.service")
        assert not unit.exists()
        assert "Template" in (h.ctx.dialog.last_msgbox_title or "")

    def test_install_refuses_when_template_missing(self, tmp_path):
        h = _make_nomadnet()
        missing = tmp_path / "nope.service"
        with patch(
            'handlers._nomadnet_service_ops._UNIT_TEMPLATE',
            missing,
        ):
            h._install_user_unit()
        # Message box shown, no exception
        assert "Template" in (h.ctx.dialog.last_msgbox_title or "")

    def test_real_template_has_exactly_one_placeholder_token(self):
        """The shipped template must contain the placeholder exactly once.

        A stray ``__NOMADNET_EXEC__`` in a comment would race the real
        ExecStart substitution and land an unsubstituted unit on disk —
        exactly the bug the rephrased template comment block fixes.
        """
        from handlers._nomadnet_service_ops import _UNIT_TEMPLATE
        assert _UNIT_TEMPLATE.exists(), (
            f"template missing: {_UNIT_TEMPLATE}"
        )
        text = _UNIT_TEMPLATE.read_text()
        occurrences = text.count('__NOMADNET_EXEC__')
        assert occurrences == 1, (
            f"Expected __NOMADNET_EXEC__ exactly once in {_UNIT_TEMPLATE}, "
            f"got {occurrences} occurrences"
        )

    def test_install_refuses_when_binary_not_found(self, tmp_path):
        """_find_nomadnet_binary returning None aborts before unit write."""
        h = _make_nomadnet()
        fake_home = tmp_path / "home"
        fake_template = tmp_path / "nomadnet-user.service"
        self._template_with_placeholder(fake_template)

        with patch(
            'handlers._nomadnet_service_ops._UNIT_TEMPLATE',
            fake_template,
        ):
            with patch(
                'handlers._nomadnet_service_ops.get_real_user_home',
                return_value=fake_home,
            ):
                with patch('shutil.which', return_value='/usr/bin/tmux'):
                    with patch.object(h, '_find_nomadnet_binary',
                                      return_value=None):
                        h._install_user_unit(force=True)

        unit = (fake_home / ".config" / "systemd" / "user" /
                "nomadnet.service")
        assert not unit.exists()


# ======================================================================
# Issue #46: NomadNet Service Control > Repair RNS alignment
# ======================================================================


class TestRepairRnsAlignment:
    """Repair RNS alignment dialog flow (Issue #46)."""

    def _aligned_state(self):
        from utils.rns_alignment import (
            CANONICAL_CONFIGDIR, ConfigFileFacts, RNSAlignmentState,
        )
        return RNSAlignmentState(
            hostname='fleet-host-1-test',
            rnsd_active=True,
            rnsd_user='root',
            rnsd_exec_start='/usr/local/bin/rnsd --config /etc/reticulum --service',
            rnsd_configdir=CANONICAL_CONFIGDIR,
            etc_config=ConfigFileFacts(
                path=Path('/etc/reticulum/config'),
                exists=True, owner='root:root', has_rpc_key=True,
                has_instance_name=False,
            ),
            nomadnet_unit_installed=True,
            nomadnet_unit_rnsconfig=CANONICAL_CONFIGDIR,
        )

    def _drifted_state(self):
        from utils.rns_alignment import ConfigFileFacts
        s = self._aligned_state()
        s.rnsd_configdir = Path('/root/.reticulum')
        s.rnsd_exec_start = '/usr/local/bin/rnsd --service'
        s.etc_config = ConfigFileFacts(
            path=Path('/etc/reticulum/config'), exists=False,
        )
        return s

    def test_aligned_host_short_circuits_with_msgbox(self):
        h = _make_nomadnet()
        with patch('utils.rns_alignment.probe_local',
                   return_value=self._aligned_state()):
            h._repair_rns_alignment()
        assert "ALIGNED" in (h.ctx.dialog.last_msgbox_title or "")

    def test_drifted_host_offers_yesno_with_plan(self):
        """Drift triggers a confirm dialog listing planned actions."""
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [False]
        called_subprocess = []
        with patch('utils.rns_alignment.probe_local',
                   return_value=self._drifted_state()):
            with patch('subprocess.run',
                       side_effect=lambda *a, **k: called_subprocess.append(a)):
                h._repair_rns_alignment()
        assert called_subprocess == [], "decline must not invoke normalize"

    def test_yes_to_normalize_invokes_sudo_cli(self):
        """Accepting the dialog runs sudo python3 scripts/rns_alignment.py normalize --yes."""
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [True]
        captured_argv = []

        def fake_run(argv, **kwargs):
            captured_argv.append(argv)
            class R:
                returncode = 0
                stdout = "Plan applied.\n"
                stderr = ""
            return R()

        with patch('utils.rns_alignment.probe_local',
                   return_value=self._drifted_state()):
            with patch('subprocess.run', side_effect=fake_run):
                h._repair_rns_alignment()

        assert any(
            'sudo' in argv and 'normalize' in argv and '--yes' in argv
            for argv in captured_argv
        ), f"expected sudo normalize --yes invocation, got {captured_argv}"

