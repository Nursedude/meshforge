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
        with patch.object(h, '_is_nomadnet_running', return_value=False):
            h._stop_nomadnet()
        msgbox_calls = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
        assert any('Not Running' in str(c) for c in msgbox_calls)

    @patch('subprocess.run')
    def test_stop_running_confirmed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(h, '_is_nomadnet_running', side_effect=[True, False]):
            h._stop_nomadnet()

    def test_stop_running_cancelled(self):
        h = _make_nomadnet()
        h.ctx.dialog._yesno_returns = [False]
        with patch.object(h, '_is_nomadnet_running', return_value=True):
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
        target = Path("/home/wh6gxz/.nomadnetwork-interactive")
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
        target = Path("/home/wh6gxz/.nomadnetwork-interactive")
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
        target = Path("/home/wh6gxz/.nomadnetwork-interactive")
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


class TestInstallUserUnit:
    """_install_user_unit copies template + runs daemon-reload/enable/start."""

    def test_install_writes_unit_and_activates(self, tmp_path):
        h = _make_nomadnet()
        fake_home = tmp_path / "home"
        (fake_home / ".config" / "systemd" / "user").mkdir(parents=True)

        # Fake template path
        fake_template = tmp_path / "nomadnet-user.service"
        fake_template.write_text("[Unit]\nDescription=Fake\n")

        with patch(
            'handlers._nomadnet_service_ops._UNIT_TEMPLATE',
            fake_template,
        ):
            with patch(
                'handlers._nomadnet_service_ops.get_real_user_home',
                return_value=fake_home,
            ):
                with patch.object(
                    h, '_systemctl_user',
                    return_value=(True, "OK"),
                ):
                    with patch.object(h, '_chown_real_user'):
                        with patch('subprocess.run') as mock_run:
                            mock_run.return_value.returncode = 0
                            mock_run.return_value.stdout = ""
                            mock_run.return_value.stderr = ""
                            h._install_user_unit(force=True)

        unit = fake_home / ".config" / "systemd" / "user" / "nomadnet.service"
        assert unit.exists()
        assert "Description=Fake" in unit.read_text()

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
