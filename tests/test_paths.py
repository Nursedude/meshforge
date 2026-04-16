"""
Tests for path utilities.

Run: python3 -m pytest tests/test_paths.py -v
"""

import pytest
import os
from pathlib import Path
from unittest.mock import patch

from utils.paths import get_real_user_home, get_real_username


class TestGetRealUserHome:
    """Tests for get_real_user_home function."""

    def test_normal_user(self):
        """Test returns home when running as normal user."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove SUDO_USER if present
            if 'SUDO_USER' in os.environ:
                del os.environ['SUDO_USER']

            result = get_real_user_home()

            assert isinstance(result, Path)
            assert result.exists() or str(result).startswith('/home/')

    def test_with_sudo_user(self):
        """Test returns real user home when running with sudo."""
        with patch.dict(os.environ, {'SUDO_USER': 'testuser'}):
            result = get_real_user_home()

            assert result == Path('/home/testuser')

    def test_sudo_user_root(self):
        """Test handles SUDO_USER=root correctly."""
        with patch.dict(os.environ, {'SUDO_USER': 'root'}):
            with patch('pathlib.Path.home') as mock_home:
                mock_home.return_value = Path('/root')
                result = get_real_user_home()

                # Should fall back to Path.home() when SUDO_USER is root
                assert result == Path('/root')

    def test_empty_sudo_user(self):
        """Test handles empty SUDO_USER."""
        with patch.dict(os.environ, {'SUDO_USER': ''}):
            with patch('pathlib.Path.home') as mock_home:
                mock_home.return_value = Path('/home/default')
                result = get_real_user_home()

                # Empty SUDO_USER should fall back
                assert result == Path('/home/default')


class TestGetRealUsername:
    """Tests for get_real_username function."""

    def test_normal_user(self):
        """Test returns current user when not running with sudo."""
        with patch.dict(os.environ, {'USER': 'normaluser'}, clear=False):
            if 'SUDO_USER' in os.environ:
                del os.environ['SUDO_USER']

            result = get_real_username()

            assert isinstance(result, str)
            assert len(result) > 0

    def test_with_sudo_user(self):
        """Test returns real username when running with sudo."""
        with patch.dict(os.environ, {'SUDO_USER': 'realuser', 'USER': 'root'}):
            result = get_real_username()

            assert result == 'realuser'


class TestReticulumPathsDiscovery:
    """Tests for ETC_DISCOVERY directory in ReticulumPaths."""

    def test_discovery_constant_exists(self):
        """ETC_DISCOVERY path constant is defined."""
        from utils.paths import ReticulumPaths
        assert ReticulumPaths.ETC_DISCOVERY == (
            Path('/etc/reticulum/storage/discovery')
        )

    def test_ensure_system_dirs_creates_discovery(self, tmp_path):
        """ensure_system_dirs creates the discovery directory."""
        from utils.paths import ReticulumPaths

        # Temporarily override class paths to use tmp_path
        orig_base = ReticulumPaths.ETC_BASE
        orig_storage = ReticulumPaths.ETC_STORAGE
        orig_discovery = ReticulumPaths.ETC_DISCOVERY
        orig_ratchets = ReticulumPaths.ETC_RATCHETS
        orig_resources = ReticulumPaths.ETC_RESOURCES
        orig_cache = ReticulumPaths.ETC_CACHE
        orig_announce = ReticulumPaths.ETC_ANNOUNCE_CACHE
        orig_ifaces = ReticulumPaths.ETC_INTERFACES

        try:
            ReticulumPaths.ETC_BASE = tmp_path / 'reticulum'
            ReticulumPaths.ETC_STORAGE = tmp_path / 'reticulum' / 'storage'
            ReticulumPaths.ETC_DISCOVERY = (
                tmp_path / 'reticulum' / 'storage' / 'discovery'
            )
            ReticulumPaths.ETC_RATCHETS = (
                tmp_path / 'reticulum' / 'storage' / 'ratchets'
            )
            ReticulumPaths.ETC_RESOURCES = (
                tmp_path / 'reticulum' / 'storage' / 'resources'
            )
            ReticulumPaths.ETC_CACHE = (
                tmp_path / 'reticulum' / 'storage' / 'cache'
            )
            ReticulumPaths.ETC_ANNOUNCE_CACHE = (
                tmp_path / 'reticulum' / 'storage' / 'cache' / 'announces'
            )
            ReticulumPaths.ETC_INTERFACES = (
                tmp_path / 'reticulum' / 'interfaces'
            )

            result = ReticulumPaths.ensure_system_dirs()

            assert result is True
            assert ReticulumPaths.ETC_DISCOVERY.is_dir()
        finally:
            ReticulumPaths.ETC_BASE = orig_base
            ReticulumPaths.ETC_STORAGE = orig_storage
            ReticulumPaths.ETC_DISCOVERY = orig_discovery
            ReticulumPaths.ETC_RATCHETS = orig_ratchets
            ReticulumPaths.ETC_RESOURCES = orig_resources
            ReticulumPaths.ETC_CACHE = orig_cache
            ReticulumPaths.ETC_ANNOUNCE_CACHE = orig_announce
            ReticulumPaths.ETC_INTERFACES = orig_ifaces


class TestPathConsistency:
    """Test consistency between path functions."""

    def test_home_contains_username(self):
        """Test that home path is consistent with username."""
        with patch.dict(os.environ, {'SUDO_USER': 'wh6gxz'}):
            home = get_real_user_home()
            user = get_real_username()

            assert user in str(home)


# =============================================================================
# ReticulumPaths Discovery (added for diagnostics/Q&A)
# =============================================================================


class TestReticulumPathsResolution:
    """Test ReticulumPaths config resolution order and edge cases.

    These tests validate the 3-tier resolution that mirrors RNS.Reticulum.__init__:
      1. /etc/reticulum/config (system-wide)
      2. ~/.config/reticulum/config (XDG-style)
      3. ~/.reticulum/config (traditional fallback)

    Critical for diagnostics: when a user says "I edited my config but nothing
    changed", the answer is usually that they edited the wrong file.
    """

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/wh6gxz'))
    def test_etc_reticulum_highest_priority(self, mock_home):
        """System-wide /etc/reticulum wins when both dir and config file exist."""
        from utils.paths import ReticulumPaths

        def mock_is_dir(self_path):
            return str(self_path) == '/etc/reticulum'

        def mock_is_file(self_path):
            return str(self_path) == '/etc/reticulum/config'

        with patch.object(Path, 'is_dir', mock_is_dir):
            with patch.object(Path, 'is_file', mock_is_file):
                assert ReticulumPaths.get_config_dir() == Path('/etc/reticulum')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/wh6gxz'))
    def test_xdg_when_etc_missing(self, mock_home):
        """XDG config wins when /etc/reticulum doesn't exist."""
        from utils.paths import ReticulumPaths

        def mock_is_dir(self_path):
            return str(self_path) in ('/home/<user>/.config/reticulum',)

        def mock_is_file(self_path):
            return str(self_path) == '/home/<user>/.config/reticulum/config'

        with patch.object(Path, 'is_dir', mock_is_dir):
            with patch.object(Path, 'is_file', mock_is_file):
                assert ReticulumPaths.get_config_dir() == Path('/home/<user>/.config/reticulum')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/wh6gxz'))
    def test_traditional_fallback(self, mock_home):
        """~/.reticulum is the fallback when nothing else exists."""
        from utils.paths import ReticulumPaths

        with patch.object(Path, 'is_dir', return_value=False):
            with patch.object(Path, 'is_file', return_value=False):
                result = ReticulumPaths.get_config_dir()
                assert result == Path('/home/<user>/.reticulum')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/wh6gxz'))
    def test_config_file_returned_from_config_dir(self, mock_home):
        """get_config_file() appends 'config' to get_config_dir()."""
        from utils.paths import ReticulumPaths

        with patch.object(Path, 'is_dir', return_value=False):
            with patch.object(Path, 'is_file', return_value=False):
                result = ReticulumPaths.get_config_file()
                assert result == Path('/home/<user>/.reticulum/config')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/wh6gxz'))
    def test_interfaces_dir_under_config_dir(self, mock_home):
        """get_interfaces_dir() returns config_dir/interfaces."""
        from utils.paths import ReticulumPaths

        def mock_is_dir(self_path):
            return str(self_path) == '/etc/reticulum'

        def mock_is_file(self_path):
            return str(self_path) == '/etc/reticulum/config'

        with patch.object(Path, 'is_dir', mock_is_dir):
            with patch.object(Path, 'is_file', mock_is_file):
                result = ReticulumPaths.get_interfaces_dir()
                assert result == Path('/etc/reticulum/interfaces')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/wh6gxz'))
    def test_sudo_user_gets_correct_home(self, mock_home):
        """Under sudo, paths resolve to real user's home, not /root."""
        from utils.paths import ReticulumPaths

        with patch.object(Path, 'is_dir', return_value=False):
            with patch.object(Path, 'is_file', return_value=False):
                result = ReticulumPaths.get_config_dir()
                # Should be /home/wh6gxz, NOT /root
                assert '/root' not in str(result)
                assert 'wh6gxz' in str(result)

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/wh6gxz'))
    def test_etc_dir_without_config_file_skipped(self, mock_home):
        """/etc/reticulum exists but has no config file => skip to next tier."""
        from utils.paths import ReticulumPaths

        def mock_is_dir(self_path):
            return str(self_path) == '/etc/reticulum'

        def mock_is_file(self_path):
            return False

        with patch.object(Path, 'is_dir', mock_is_dir):
            with patch.object(Path, 'is_file', mock_is_file):
                result = ReticulumPaths.get_config_dir()
                assert result == Path('/home/<user>/.reticulum')

    def test_system_paths_are_absolute(self):
        """All static system paths should be absolute."""
        from utils.paths import ReticulumPaths
        assert ReticulumPaths.ETC_BASE.is_absolute()
        assert ReticulumPaths.ETC_STORAGE.is_absolute()
        assert ReticulumPaths.ETC_RATCHETS.is_absolute()
        assert ReticulumPaths.ETC_CACHE.is_absolute()
        assert ReticulumPaths.ETC_INTERFACES.is_absolute()

    def test_storage_subdirs_under_etc_base(self):
        """Storage, ratchets, cache are all under /etc/reticulum."""
        from utils.paths import ReticulumPaths
        assert str(ReticulumPaths.ETC_STORAGE).startswith(str(ReticulumPaths.ETC_BASE))
        assert str(ReticulumPaths.ETC_RATCHETS).startswith(str(ReticulumPaths.ETC_STORAGE))
        assert str(ReticulumPaths.ETC_CACHE).startswith(str(ReticulumPaths.ETC_STORAGE))

    @patch.dict(os.environ, {'SUDO_USER': 'wh6gxz'}, clear=False)
    def test_meshforge_paths_use_real_user_home(self):
        """MeshForgePaths should use get_real_user_home, not Path.home()."""
        from utils.paths import MeshForgePaths

        config_dir = MeshForgePaths.get_config_dir()
        data_dir = MeshForgePaths.get_data_dir()
        cache_dir = MeshForgePaths.get_cache_dir()

        # All should be under /home/wh6gxz, not /root
        for d in (config_dir, data_dir, cache_dir):
            assert '/root' not in str(d), f"{d} should not be under /root"
            assert 'wh6gxz' in str(d), f"{d} should be under wh6gxz's home"

    def test_resolve_home_for_user_uses_pwd(self):
        """_resolve_home_for_user uses pwd module for real home lookup."""
        from utils.paths import _resolve_home_for_user
        import pwd

        # Test with current user - should match pwd database
        try:
            current_user = os.environ.get('USER', 'root')
            expected = Path(pwd.getpwnam(current_user).pw_dir)
            result = _resolve_home_for_user(current_user)
            assert result == expected
        except KeyError:
            pytest.skip("Current user not in passwd database")

    def test_resolve_home_for_nonexistent_user(self):
        """_resolve_home_for_user falls back to /home/<user> for unknown users."""
        from utils.paths import _resolve_home_for_user

        result = _resolve_home_for_user('nonexistent_user_xyz_12345')
        assert result == Path('/home/nonexistent_user_xyz_12345')
