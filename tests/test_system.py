"""
Tests for system utilities (privilege detection, system info).

Run: python3 -m pytest tests/test_system.py -v
"""

import os
import pytest
import platform
from unittest.mock import patch, MagicMock, mock_open

from src.utils.system import (
    check_root,
    require_root,
    get_real_user,
    get_real_uid,
    get_real_gid,
    run_admin_command,
    get_system_info,
    is_raspberry_pi,
    get_board_model,
    is_linux_native_compatible,
    get_architecture_bits,
)


class TestCheckRoot:
    """Tests for check_root function."""

    def test_root_when_euid_zero(self):
        """Test returns True when effective UID is 0."""
        with patch('os.geteuid', return_value=0):
            assert check_root() is True

    def test_not_root_when_euid_nonzero(self):
        """Test returns False when effective UID is not 0."""
        with patch('os.geteuid', return_value=1000):
            assert check_root() is False


class TestRequireRoot:
    """Tests for require_root function."""

    def test_returns_true_when_root(self):
        """Test returns True when running as root."""
        with patch('src.utils.system.check_root', return_value=True):
            assert require_root(exit_on_fail=False) is True

    def test_returns_false_when_not_root_no_exit(self):
        """Test returns False when not root and exit_on_fail=False."""
        with patch('src.utils.system.check_root', return_value=False):
            result = require_root(exit_on_fail=False)
            assert result is False

    def test_exits_when_not_root_and_exit_on_fail(self):
        """Test exits with code 1 when not root and exit_on_fail=True."""
        with patch('src.utils.system.check_root', return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                require_root(exit_on_fail=True)
            assert exc_info.value.code == 1

    def test_custom_message(self):
        """Test custom error message is used."""
        messages = []

        def capture_print(msg):
            messages.append(msg)

        with patch('src.utils.system.check_root', return_value=False):
            require_root(
                exit_on_fail=False,
                message="Custom error",
                print_func=capture_print
            )

        assert len(messages) == 1
        assert "Custom error" in messages[0]


class TestGetRealUser:
    """Tests for get_real_user function."""

    def test_returns_sudo_user_when_set(self):
        """Test returns SUDO_USER when set."""
        with patch.dict(os.environ, {'SUDO_USER': 'realuser', 'USER': 'root'}):
            assert get_real_user() == 'realuser'

    def test_returns_user_when_no_sudo(self):
        """Test returns USER when SUDO_USER not set."""
        env = {'USER': 'normaluser'}
        with patch.dict(os.environ, env, clear=True):
            assert get_real_user() == 'normaluser'

    def test_returns_unknown_when_no_env(self):
        """Test returns 'unknown' when no user env vars."""
        with patch.dict(os.environ, {}, clear=True):
            assert get_real_user() == 'unknown'


class TestGetRealUid:
    """Tests for get_real_uid function."""

    def test_returns_sudo_uid_when_set(self):
        """Test returns SUDO_UID when set."""
        with patch.dict(os.environ, {'SUDO_UID': '1000'}):
            assert get_real_uid() == 1000

    def test_returns_current_uid_when_no_sudo(self):
        """Test returns current UID when SUDO_UID not set."""
        with patch.dict(os.environ, {}, clear=True):
            with patch('os.getuid', return_value=500):
                assert get_real_uid() == 500


class TestGetRealGid:
    """Tests for get_real_gid function."""

    def test_returns_sudo_gid_when_set(self):
        """Test returns SUDO_GID when set."""
        with patch.dict(os.environ, {'SUDO_GID': '1000'}):
            assert get_real_gid() == 1000

    def test_returns_current_gid_when_no_sudo(self):
        """Test returns current GID when SUDO_GID not set."""
        with patch.dict(os.environ, {}, clear=True):
            with patch('os.getgid', return_value=500):
                assert get_real_gid() == 500


class TestRunAdminCommand:
    """Tests for run_admin_command function."""

    def test_runs_directly_when_root(self):
        """Test command runs directly when already root."""
        with patch('src.utils.system.check_root', return_value=True):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="success",
                    stderr=""
                )

                success, stdout, stderr = run_admin_command(['echo', 'test'])

                assert success is True
                assert stdout == "success"
                mock_run.assert_called_once()
                # Should be called without sudo/pkexec prefix
                args = mock_run.call_args[0][0]
                assert args == ['echo', 'test']

    def test_uses_pkexec_with_gui(self):
        """Test uses pkexec when GUI available."""
        with patch('src.utils.system.check_root', return_value=False):
            with patch('shutil.which', return_value='/usr/bin/pkexec'):
                with patch.dict(os.environ, {'DISPLAY': ':0'}):
                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = MagicMock(
                            returncode=0,
                            stdout="done",
                            stderr=""
                        )

                        success, stdout, _ = run_admin_command(
                            ['systemctl', 'start', 'test'],
                            use_gui=True
                        )

                        assert success is True
                        args = mock_run.call_args[0][0]
                        assert args[0] == '/usr/bin/pkexec'

    def test_falls_back_to_sudo(self):
        """Test falls back to sudo when pkexec unavailable."""
        with patch('src.utils.system.check_root', return_value=False):
            with patch('shutil.which') as mock_which:
                mock_which.side_effect = lambda x: '/usr/bin/sudo' if x == 'sudo' else None
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(
                        returncode=0,
                        stdout="done",
                        stderr=""
                    )

                    success, _, _ = run_admin_command(['echo', 'test'])

                    args = mock_run.call_args[0][0]
                    assert args[0] == '/usr/bin/sudo'

    def test_timeout_handling(self):
        """Test timeout is passed to subprocess."""
        import subprocess

        with patch('src.utils.system.check_root', return_value=True):
            with patch('subprocess.run') as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired(['cmd'], 5)

                success, _, stderr = run_admin_command(['test'], timeout=5)

                assert success is False
                assert 'timed out' in stderr.lower()

    def test_pkexec_user_cancel(self):
        """Test handling of user cancelling pkexec dialog."""
        with patch('src.utils.system.check_root', return_value=False):
            with patch('shutil.which', return_value='/usr/bin/pkexec'):
                with patch.dict(os.environ, {'DISPLAY': ':0'}):
                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = MagicMock(
                            returncode=126,  # User cancelled
                            stdout="",
                            stderr=""
                        )

                        success, _, stderr = run_admin_command(['test'])

                        assert success is False
                        assert 'cancelled' in stderr.lower()


class TestIsRaspberryPi:
    """Tests for is_raspberry_pi function."""

    def test_detects_pi_from_device_tree(self):
        """Test detects Pi from /proc/device-tree/model."""
        model_content = "Raspberry Pi 4 Model B Rev 1.4\x00"
        with patch('builtins.open', mock_open(read_data=model_content)):
            assert is_raspberry_pi() is True

    def test_detects_non_pi(self):
        """Test returns False for non-Pi systems."""
        with patch('builtins.open', side_effect=FileNotFoundError):
            # No device tree and no BCM in cpuinfo
            with patch('builtins.open', side_effect=FileNotFoundError):
                assert is_raspberry_pi() is False

    def test_detects_pi_from_cpuinfo(self):
        """Test detects Pi from cpuinfo fallback."""
        def open_side_effect(path, *args, **kwargs):
            if 'device-tree' in path:
                raise FileNotFoundError
            return mock_open(read_data="Hardware: BCM2835\n")()

        with patch('builtins.open', side_effect=open_side_effect):
            assert is_raspberry_pi() is True


class TestGetBoardModel:
    """Tests for get_board_model function."""

    def test_returns_model_from_device_tree(self):
        """Test returns board model from device tree."""
        model = "Raspberry Pi 4 Model B Rev 1.4\x00"
        with patch('builtins.open', mock_open(read_data=model)):
            result = get_board_model()
            assert result == "Raspberry Pi 4 Model B Rev 1.4"

    def test_returns_none_when_no_device_tree(self):
        """Test returns None when no device tree."""
        with patch('builtins.open', side_effect=FileNotFoundError):
            assert get_board_model() is None


class TestIsLinuxNativeCompatible:
    """Tests for is_linux_native_compatible function."""

    def test_pi_always_compatible(self):
        """Test Raspberry Pi is always compatible."""
        with patch('src.utils.system.is_raspberry_pi', return_value=True):
            assert is_linux_native_compatible() is True

    def test_arm64_linux_compatible(self):
        """Test ARM64 Linux is compatible."""
        with patch('src.utils.system.is_raspberry_pi', return_value=False):
            with patch('platform.machine', return_value='aarch64'):
                with patch('platform.system', return_value='Linux'):
                    assert is_linux_native_compatible() is True

    def test_x86_64_linux_compatible(self):
        """Test x86_64 Linux is compatible."""
        with patch('src.utils.system.is_raspberry_pi', return_value=False):
            with patch('platform.machine', return_value='x86_64'):
                with patch('platform.system', return_value='Linux'):
                    assert is_linux_native_compatible() is True

    def test_windows_not_compatible(self):
        """Test Windows is not compatible."""
        with patch('src.utils.system.is_raspberry_pi', return_value=False):
            with patch('platform.machine', return_value='x86_64'):
                with patch('platform.system', return_value='Windows'):
                    assert is_linux_native_compatible() is False


class TestGetArchitectureBits:
    """Tests for get_architecture_bits function."""

    def test_aarch64_is_64bit(self):
        """Test aarch64 returns 64."""
        with patch('platform.machine', return_value='aarch64'):
            assert get_architecture_bits() == 64

    def test_arm64_is_64bit(self):
        """Test arm64 returns 64."""
        with patch('platform.machine', return_value='arm64'):
            assert get_architecture_bits() == 64

    def test_x86_64_is_64bit(self):
        """Test x86_64 returns 64."""
        with patch('platform.machine', return_value='x86_64'):
            assert get_architecture_bits() == 64

    def test_armv7_is_32bit(self):
        """Test armv7 returns 32."""
        with patch('platform.machine', return_value='armv7l'):
            assert get_architecture_bits() == 32

    def test_i686_is_32bit(self):
        """Test i686 returns 32."""
        with patch('platform.machine', return_value='i686'):
            assert get_architecture_bits() == 32


class TestGetSystemInfo:
    """Tests for get_system_info function."""

    def test_returns_dict_with_required_keys(self):
        """Test returns dictionary with all required keys."""
        with patch('src.utils.system.is_raspberry_pi', return_value=False):
            with patch('src.utils.system.get_board_model', return_value=None):
                with patch('src.utils.system.is_linux_native_compatible', return_value=True):
                    with patch('src.utils.system.get_architecture_bits', return_value=64):
                        info = get_system_info()

                        required_keys = [
                            'os', 'os_version', 'os_codename', 'arch',
                            'platform', 'python', 'kernel', 'is_pi',
                            'meshtastic_compatible', 'bits'
                        ]

                        for key in required_keys:
                            assert key in info, f"Missing key: {key}"

    def test_includes_board_model_when_available(self):
        """Test includes board_model when detected."""
        with patch('src.utils.system.is_raspberry_pi', return_value=True):
            with patch('src.utils.system.get_board_model', return_value='Pi 4'):
                with patch('src.utils.system.is_linux_native_compatible', return_value=True):
                    with patch('src.utils.system.get_architecture_bits', return_value=64):
                        info = get_system_info()
                        assert info.get('board_model') == 'Pi 4'

    def test_python_version_format(self):
        """Test Python version is in expected format."""
        with patch('src.utils.system.is_raspberry_pi', return_value=False):
            with patch('src.utils.system.get_board_model', return_value=None):
                with patch('src.utils.system.is_linux_native_compatible', return_value=True):
                    with patch('src.utils.system.get_architecture_bits', return_value=64):
                        info = get_system_info()
                        # Should be like "3.11.x"
                        assert '.' in info['python']
