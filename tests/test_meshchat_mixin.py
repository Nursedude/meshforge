"""
MeshChat handler, deployment profile, and diagnostics tests.

Tests MeshChat as a first-class LXMF client alongside NomadNet.

Updated from mixin-based tests to handler-based tests after the
mixin-to-registry migration (Batch 8).
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# Ensure src directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ============================================================================
# Deployment Profile Tests
# ============================================================================

class TestMeshChatDeploymentProfile:
    """Test the MeshChat deployment profile."""

    def test_meshchat_profile_name_exists(self):
        from utils.deployment_profiles import ProfileName
        assert hasattr(ProfileName, 'MESHCHAT')
        assert ProfileName.MESHCHAT.value == "meshchat"

    def test_meshchat_profile_in_profiles_dict(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        assert ProfileName.MESHCHAT in PROFILES

    def test_meshchat_profile_flags(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        profile = PROFILES[ProfileName.MESHCHAT]
        assert profile.feature_flags['meshchat'] is True
        assert profile.feature_flags['rns'] is True
        assert profile.feature_flags['gateway'] is True
        assert profile.feature_flags['meshtastic'] is True

    def test_meshchat_profile_services(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        profile = PROFILES[ProfileName.MESHCHAT]
        assert 'meshtasticd' in profile.required_services
        assert 'rnsd' in profile.required_services

    def test_meshchat_profile_optional_services(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        profile = PROFILES[ProfileName.MESHCHAT]
        assert 'reticulum-meshchat' in profile.optional_services or \
               'meshchat' in profile.optional_services

    def test_meshchat_in_list_profiles(self):
        from utils.deployment_profiles import list_profiles, ProfileName
        profiles = list_profiles()
        names = [p.name for p in profiles]
        assert ProfileName.MESHCHAT in names

    def test_meshchat_flag_in_full_profile(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        full = PROFILES[ProfileName.FULL]
        assert 'meshchat' in full.feature_flags
        assert full.feature_flags['meshchat'] is True

    def test_meshchat_flag_false_in_non_meshchat_profiles(self):
        from utils.deployment_profiles import ProfileName, PROFILES
        for name in [ProfileName.RADIO_MAPS, ProfileName.MONITOR, ProfileName.MESHCORE]:
            profile = PROFILES[name]
            assert profile.feature_flags.get('meshchat') is False, \
                f"Profile {name.value} should have meshchat=False"

    def test_get_profile_by_name_meshchat(self):
        from utils.deployment_profiles import get_profile_by_name, ProfileName
        profile = get_profile_by_name("meshchat")
        assert profile is not None
        assert profile.name == ProfileName.MESHCHAT

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.service_check.check_port', return_value=True)
    def test_detect_profile_meshchat(self, mock_port, mock_svc):
        """Auto-detect selects meshchat when meshtasticd + rnsd + port 8000."""
        from utils.deployment_profiles import detect_profile, ProfileName

        def service_available(name):
            return name in ('meshtasticd', 'rnsd')

        mock_svc.side_effect = service_available
        profile = detect_profile()
        assert profile.name == ProfileName.MESHCHAT

    @patch('utils.deployment_profiles._check_service_available')
    def test_detect_profile_gateway_without_meshchat(self, mock_svc):
        """Auto-detect selects gateway when no MeshChat port 8000."""
        from utils.deployment_profiles import detect_profile, ProfileName

        def service_available(name):
            return name in ('meshtasticd', 'rnsd')

        mock_svc.side_effect = service_available
        # check_port will fail (no MeshChat) → falls back to gateway
        profile = detect_profile()
        # Should be either meshchat or gateway depending on port
        assert profile.name in (ProfileName.MESHCHAT, ProfileName.GATEWAY)


# ============================================================================
# MeshChat Handler Tests (migrated from MeshChatClientMixin)
# ============================================================================

def _make_handler():
    """Create a MeshChatHandler with mocked TUIContext."""
    from launcher_tui.handlers.meshchat import MeshChatHandler
    handler = MeshChatHandler()
    ctx = MagicMock()
    ctx.dialog = MagicMock()
    ctx.registry = MagicMock()
    ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
    ctx.wait_for_enter = MagicMock()
    ctx.feature_enabled = lambda f: True
    handler.ctx = ctx
    return handler


class TestMeshChatHandler:
    """Test MeshChatHandler TUI methods."""

    def test_handler_creates(self):
        """MeshChatHandler can be instantiated with expected methods."""
        handler = _make_handler()
        assert hasattr(handler, '_meshchat_menu')
        assert hasattr(handler, '_meshchat_status')
        assert hasattr(handler, '_is_meshchat_installed')
        assert hasattr(handler, '_is_meshchat_running')

    @patch('shutil.which', return_value=None)
    def test_not_installed_when_no_binary(self, mock_which):
        """Reports not installed when no meshchat binary found."""
        handler = _make_handler()
        with patch('launcher_tui.handlers.meshchat._HAS_MESHCHAT_SERVICE', False):
            result = handler._is_meshchat_installed()
            assert result is False

    @patch('shutil.which', return_value='/usr/bin/meshchat')
    def test_installed_when_binary_found(self, mock_which):
        """Reports installed when meshchat binary found."""
        handler = _make_handler()
        result = handler._is_meshchat_installed()
        assert result is True

    def test_check_rns_preflight_no_rnsd(self):
        """Preflight check warns when rnsd not running."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = True
        handler._get_rnsd_user = lambda: None
        result = handler._check_rns_for_meshchat()
        assert result is True
        handler.ctx.dialog.yesno.assert_called_once()

    def test_check_rns_preflight_cancelled(self):
        """Preflight check returns False when user cancels."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = False
        handler._get_rnsd_user = lambda: None
        result = handler._check_rns_for_meshchat()
        assert result is False

    def test_check_rns_preflight_rnsd_running(self):
        """Preflight check passes when rnsd running as non-root."""
        handler = _make_handler()
        handler._get_rnsd_user = lambda: 'pi'
        result = handler._check_rns_for_meshchat()
        assert result is True


# ============================================================================
# LXMF App Conflict Detection Tests
# ============================================================================

class TestLXMFAppConflict:
    """Test _check_lxmf_app_conflict() on RNSDiagnosticsHandler."""

    def _make_diagnostics_handler(self):
        """Create a RNSDiagnosticsHandler with mocked TUIContext."""
        from launcher_tui.handlers.rns_diagnostics import RNSDiagnosticsHandler
        handler = RNSDiagnosticsHandler()
        ctx = MagicMock()
        ctx.dialog = MagicMock()
        ctx.registry = MagicMock()
        ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
        ctx.wait_for_enter = MagicMock()
        handler.ctx = ctx
        return handler

    @patch('subprocess.run')
    def test_detects_nomadnet(self, mock_run):
        """Detects NomadNet holding port."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")
        handler = self._make_diagnostics_handler()
        result = handler._check_lxmf_app_conflict()
        assert result == "NomadNet"

    @patch('subprocess.run')
    def test_detects_meshchat(self, mock_run):
        """Detects MeshChat when NomadNet not running."""
        def side_effect(cmd, **kwargs):
            mock = MagicMock()
            if 'nomadnet' in cmd:
                mock.returncode = 1
                mock.stdout = ""
            else:  # meshchat
                mock.returncode = 0
                mock.stdout = "5678\n"
            return mock

        mock_run.side_effect = side_effect
        handler = self._make_diagnostics_handler()
        result = handler._check_lxmf_app_conflict()
        assert result == "MeshChat"

    @patch('subprocess.run')
    def test_no_conflict(self, mock_run):
        """Returns None when no LXMF app running."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        handler = self._make_diagnostics_handler()
        result = handler._check_lxmf_app_conflict()
        assert result is None


# ============================================================================
# Gateway Diagnostic Tests
# ============================================================================

class TestMeshChatGatewayDiagnostic:
    """Test MeshChat integration in gateway diagnostics."""

    def test_check_meshchat_rns_integration_no_client(self):
        """Returns SKIP when MeshChat client not available."""
        from utils.gateway_diagnostic import GatewayDiagnostic, CheckStatus

        with patch('utils.gateway_diagnostic._HAS_MESHCHAT_CLIENT', False):
            diag = GatewayDiagnostic()
            result = diag.check_meshchat_rns_integration()
            assert result.status == CheckStatus.SKIP

    @patch('utils.gateway_diagnostic.MeshChatClient')
    @patch('utils.gateway_diagnostic._HAS_MESHCHAT_CLIENT', True)
    def test_check_meshchat_rns_not_running(self, MockClient):
        """Returns SKIP when MeshChat not running."""
        from utils.gateway_diagnostic import GatewayDiagnostic, CheckStatus

        MockClient.return_value.is_available.return_value = False
        diag = GatewayDiagnostic()
        result = diag.check_meshchat_rns_integration()
        assert result.status == CheckStatus.SKIP

    @patch('utils.gateway_diagnostic.MeshChatClient')
    @patch('utils.gateway_diagnostic._HAS_MESHCHAT_CLIENT', True)
    def test_check_meshchat_rns_connected(self, MockClient):
        """Returns PASS when MeshChat connected to RNS."""
        from utils.gateway_diagnostic import GatewayDiagnostic, CheckStatus

        mock_status = MagicMock()
        mock_status.rns_connected = True
        mock_status.peer_count = 5
        mock_status.message_count = 42
        MockClient.return_value.is_available.return_value = True
        MockClient.return_value.get_status.return_value = mock_status
        diag = GatewayDiagnostic()
        result = diag.check_meshchat_rns_integration()
        assert result.status == CheckStatus.PASS
        assert "5 peers" in result.message

    @patch('utils.gateway_diagnostic.MeshChatClient')
    @patch('utils.gateway_diagnostic._HAS_MESHCHAT_CLIENT', True)
    def test_check_meshchat_rns_disconnected(self, MockClient):
        """Returns FAIL when MeshChat running but RNS disconnected."""
        from utils.gateway_diagnostic import GatewayDiagnostic, CheckStatus

        mock_status = MagicMock()
        mock_status.rns_connected = False
        MockClient.return_value.is_available.return_value = True
        MockClient.return_value.get_status.return_value = mock_status
        diag = GatewayDiagnostic()
        result = diag.check_meshchat_rns_integration()
        assert result.status == CheckStatus.FAIL


# ============================================================================
# Automated Installer Tests (migrated from MeshChatClientMixin)
# ============================================================================

class TestMeshChatInstaller:
    """Test the automated MeshChat installation methods on MeshChatHandler."""

    def test_has_install_method(self):
        """Handler has automated _install_meshchat method."""
        handler = _make_handler()
        assert hasattr(handler, '_install_meshchat')
        assert callable(handler._install_meshchat)

    def test_has_uninstall_method(self):
        """Handler has _uninstall_meshchat method."""
        handler = _make_handler()
        assert hasattr(handler, '_uninstall_meshchat')
        assert callable(handler._uninstall_meshchat)

    def test_has_lxmf_exclusive_method(self):
        """Handler has _ensure_lxmf_exclusive method."""
        handler = _make_handler()
        assert hasattr(handler, '_ensure_lxmf_exclusive')
        assert callable(handler._ensure_lxmf_exclusive)

    def test_get_meshchat_install_dir(self):
        """Install dir is under user home, not /root."""
        handler = _make_handler()
        with patch('launcher_tui.handlers.meshchat.get_real_user_home') as mock_home:
            mock_home.return_value = __import__('pathlib').Path('/home/testuser')
            result = handler._get_meshchat_install_dir()
            assert str(result) == '/home/testuser/reticulum-meshchat'

    @patch('shutil.which', return_value='/usr/bin/meshchat')
    def test_install_skips_if_already_installed(self, mock_which):
        """Install shows 'already installed' if MeshChat is present."""
        handler = _make_handler()
        handler._install_meshchat()
        handler.ctx.dialog.msgbox.assert_called_once()
        assert "Already Installed" in str(handler.ctx.dialog.msgbox.call_args)

    def test_install_cancelled_by_user(self):
        """Install returns when user declines."""
        handler = _make_handler()
        with patch.object(handler, '_is_meshchat_installed', return_value=False):
            handler.ctx.dialog.yesno.return_value = False
            handler._install_meshchat()
            # Should not proceed to prerequisites
            assert handler.ctx.dialog.yesno.called

    @patch('shutil.which')
    def test_install_prerequisites_checks_git_node_npm(self, mock_which):
        """Prerequisites checker verifies git, node, npm."""
        handler = _make_handler()

        # All tools available
        mock_which.return_value = '/usr/bin/git'
        result = handler._install_meshchat_prerequisites()
        assert result is True

    @patch('shutil.which', return_value=None)
    @patch('subprocess.run')
    def test_install_prerequisites_installs_nodejs(self, mock_run, mock_which):
        """Prerequisites installs nodejs when not found."""
        handler = _make_handler()

        # First call: git not found, then found after install
        call_count = [0]
        def which_side_effect(tool):
            call_count[0] += 1
            # After apt install, tools are "found"
            if call_count[0] > 3:
                return f'/usr/bin/{tool}'
            return None

        mock_which.side_effect = which_side_effect
        mock_run.return_value = MagicMock(returncode=0)
        result = handler._install_meshchat_prerequisites()
        assert mock_run.called

    @patch('subprocess.run')
    def test_install_clone_new_repo(self, mock_run):
        """Clone creates new repo when dir doesn't exist."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(returncode=0)

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir) / 'reticulum-meshchat'
            result = handler._install_meshchat_clone(install_dir, None)
            assert result is True
            # Verify git clone was called
            clone_call = mock_run.call_args_list[0]
            assert 'git' in clone_call[0][0]
            assert 'clone' in clone_call[0][0]

    @patch('subprocess.run')
    def test_install_clone_pulls_existing(self, mock_run):
        """Clone pulls latest when dir already exists."""
        handler = _make_handler()
        mock_run.return_value = MagicMock(returncode=0, stderr='')

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)
            result = handler._install_meshchat_clone(install_dir, None)
            assert result is True
            pull_call = mock_run.call_args_list[0]
            assert 'pull' in pull_call[0][0]

    def test_install_service_creates_unit_file(self):
        """Service creation writes a valid systemd unit file."""
        handler = _make_handler()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = __import__('pathlib').Path(tmpdir)

            with patch('launcher_tui.handlers.meshchat.get_real_user_home',
                       return_value=__import__('pathlib').Path('/home/testuser')):
                with patch('subprocess.run', return_value=MagicMock(returncode=0)):
                    with patch('builtins.open', create=True) as mock_open:
                        mock_open.return_value.__enter__ = lambda s: s
                        mock_open.return_value.__exit__ = MagicMock(return_value=False)
                        mock_open.return_value.write = MagicMock()

                        result = handler._install_meshchat_service(install_dir, 'testuser')
                        # Verify write was called with unit file content
                        if mock_open.return_value.write.called:
                            content = mock_open.return_value.write.call_args[0][0]
                            assert 'User=testuser' in content
                            assert 'meshchat.py' in content
                            assert 'rnsd.service' in content

    def test_meshchat_repo_url(self):
        """MESHCHAT_REPO constant points to correct URL."""
        from launcher_tui.handlers.meshchat import MeshChatHandler
        assert 'liamcottle/reticulum-meshchat' in MeshChatHandler.MESHCHAT_REPO

    def test_meshchat_service_name(self):
        """MESHCHAT_SERVICE_NAME is correct."""
        from launcher_tui.handlers.meshchat import MeshChatHandler
        assert MeshChatHandler.MESHCHAT_SERVICE_NAME == "reticulum-meshchat"


# ============================================================================
# Uninstall (Stop + Disable) Tests
# ============================================================================

class TestMeshChatUninstall:
    """Test MeshChat uninstall functionality on MeshChatHandler."""

    def test_uninstall_cancelled(self):
        """Uninstall does nothing when user cancels."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = False
        handler._uninstall_meshchat()
        # Should only call yesno (confirmation), nothing else

    @patch('subprocess.run')
    def test_uninstall_stops_and_disables(self, mock_run):
        """Uninstall calls systemctl stop and disable."""
        handler = _make_handler()
        handler.ctx.dialog.yesno.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        handler._uninstall_meshchat()

        # Verify systemctl calls
        calls = [str(c) for c in mock_run.call_args_list]
        stop_called = any('stop' in c and 'reticulum-meshchat' in c for c in calls)
        disable_called = any('disable' in c and 'reticulum-meshchat' in c for c in calls)
        assert stop_called, "systemctl stop not called"
        assert disable_called, "systemctl disable not called"


# ============================================================================
# LXMF Exclusive Toggle Tests (migrated to _lxmf_utils.ensure_lxmf_exclusive)
# ============================================================================

class TestLXMFExclusiveToggle:
    """Test ensure_lxmf_exclusive() one-app-at-a-time enforcement."""

    @patch('subprocess.run')
    def test_meshchat_start_no_conflict(self, mock_run):
        """Starting MeshChat succeeds when NomadNet not running."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_run.return_value = MagicMock(returncode=1, stdout='')

        with patch('launcher_tui.handlers._lxmf_utils._HAS_SERVICE_CHECK', False):
            result = ensure_lxmf_exclusive(mock_dialog, "meshchat")
            assert result is True

    @patch('subprocess.run')
    def test_meshchat_start_stops_nomadnet(self, mock_run):
        """Starting MeshChat offers to stop NomadNet."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_dialog.yesno.return_value = True

        # First pgrep finds NomadNet, second pkill succeeds
        call_count = [0]
        def run_side_effect(cmd, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:  # pgrep for nomadnet
                result.returncode = 0
                result.stdout = "1234\n"
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        mock_run.side_effect = run_side_effect

        with patch('launcher_tui.handlers._lxmf_utils._HAS_SERVICE_CHECK', False):
            result = ensure_lxmf_exclusive(mock_dialog, "meshchat")
            assert result is True
            mock_dialog.yesno.assert_called_once()

    @patch('subprocess.run')
    def test_meshchat_start_user_declines(self, mock_run):
        """User declines to stop NomadNet, MeshChat start cancelled."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_dialog.yesno.return_value = False

        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")

        with patch('launcher_tui.handlers._lxmf_utils._HAS_SERVICE_CHECK', False):
            result = ensure_lxmf_exclusive(mock_dialog, "meshchat")
            assert result is False

    @patch('subprocess.run')
    def test_nomadnet_start_stops_meshchat(self, mock_run):
        """Starting NomadNet offers to stop MeshChat."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_dialog.yesno.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        result = ensure_lxmf_exclusive(
            mock_dialog, "nomadnet",
            is_meshchat_running_fn=lambda: True,
        )
        assert result is True
        mock_dialog.yesno.assert_called_once()
        assert "MeshChat" in str(mock_dialog.yesno.call_args)

    def test_nomadnet_start_no_conflict(self):
        """Starting NomadNet succeeds when MeshChat not running."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()

        result = ensure_lxmf_exclusive(
            mock_dialog, "nomadnet",
            is_meshchat_running_fn=lambda: False,
        )
        assert result is True

    @patch('subprocess.run')
    def test_nomadnet_start_user_declines(self, mock_run):
        """User declines to stop MeshChat, NomadNet start cancelled."""
        from launcher_tui.handlers._lxmf_utils import ensure_lxmf_exclusive
        mock_dialog = MagicMock()
        mock_dialog.yesno.return_value = False

        result = ensure_lxmf_exclusive(
            mock_dialog, "nomadnet",
            is_meshchat_running_fn=lambda: True,
        )
        assert result is False


# ============================================================================
# Service INSTALL_HINT Tests
# ============================================================================

class TestMeshChatServiceHint:
    """Test that service.py INSTALL_HINT references TUI install."""

    def test_install_hint_mentions_tui(self):
        """INSTALL_HINT references TUI automated install path."""
        from plugins.meshchat.service import MeshChatService
        assert "TUI" in MeshChatService.INSTALL_HINT

    def test_install_hint_mentions_npm(self):
        """INSTALL_HINT mentions npm for manual install."""
        from plugins.meshchat.service import MeshChatService
        assert "npm" in MeshChatService.INSTALL_HINT

    def test_install_hint_mentions_nodejs(self):
        """INSTALL_HINT mentions nodejs prerequisite."""
        from plugins.meshchat.service import MeshChatService
        assert "nodejs" in MeshChatService.INSTALL_HINT
