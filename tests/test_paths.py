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
        # Clear LOGNAME too — get_real_user_home() falls through to LOGNAME
        # before Path.home(), and GitHub Actions runners export
        # LOGNAME=runner which would otherwise short-circuit the test.
        with patch.dict(os.environ, {'SUDO_USER': 'root', 'LOGNAME': 'root'}):
            with patch('pathlib.Path.home') as mock_home:
                mock_home.return_value = Path('/root')
                result = get_real_user_home()

                # Should fall back to Path.home() when SUDO_USER is root
                assert result == Path('/root')

    def test_empty_sudo_user(self):
        """Test handles empty SUDO_USER."""
        # See test_sudo_user_root — clear LOGNAME so the Path.home() fall-
        # through is actually exercised on CI hosts that set LOGNAME.
        with patch.dict(os.environ, {'SUDO_USER': '', 'LOGNAME': ''}):
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

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/<user>'))
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

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/<user>'))
    def test_traditional_fallback(self, mock_home):
        """~/.reticulum is the fallback when nothing else exists."""
        from utils.paths import ReticulumPaths

        with patch.object(Path, 'is_dir', return_value=False):
            with patch.object(Path, 'is_file', return_value=False):
                result = ReticulumPaths.get_config_dir()
                assert result == Path('/home/<user>/.reticulum')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/<user>'))
    def test_config_file_returned_from_config_dir(self, mock_home):
        """get_config_file() appends 'config' to get_config_dir()."""
        from utils.paths import ReticulumPaths

        with patch.object(Path, 'is_dir', return_value=False):
            with patch.object(Path, 'is_file', return_value=False):
                result = ReticulumPaths.get_config_file()
                assert result == Path('/home/<user>/.reticulum/config')

    def test_shared_rpc_key_present(self, tmp_path):
        """Pinned rpc_key is returned (64 lowercase hex)."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        key = "bea1bf1aab671abb5aa9a0b7b013c1ddcbe5c7b71dd87e15bd0a7ebdc64fc96a"
        cfg.write_text(f"[reticulum]\n  share_instance = Yes\n  rpc_key = {key}\n")

        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_shared_rpc_key() == key

    def test_shared_rpc_key_legacy_name_ignored(self, tmp_path):
        """Legacy ``shared_instance_rpc_key`` option (ignored by RNS) must NOT be
        honored — RNS 1.1.x parses only ``rpc_key``. Treating the old name as a
        hit would make the helper claim identity-independence that RNS does not
        actually deliver.
        """
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        key = "bea1bf1aab671abb5aa9a0b7b013c1ddcbe5c7b71dd87e15bd0a7ebdc64fc96a"
        cfg.write_text(f"[reticulum]\n  shared_instance_rpc_key = {key}\n")

        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_shared_rpc_key() is None

    def test_shared_rpc_key_absent(self, tmp_path):
        """No key line in config => None (not an error)."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n  share_instance = Yes\n")

        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_shared_rpc_key() is None

    def test_shared_rpc_key_malformed_rejected(self, tmp_path):
        """Non-hex or wrong-length key is treated as absent, not returned verbatim."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n  rpc_key = not-a-hex-string-xyz\n")

        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_shared_rpc_key() is None

    def test_shared_rpc_key_comment_ignored(self, tmp_path):
        """Commented-out key lines are ignored."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        cfg.write_text(
            "[reticulum]\n"
            "# rpc_key = aa" + "bb" * 31 + "\n"
        )
        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_shared_rpc_key() is None

    def test_shared_rpc_key_uppercase_normalized(self, tmp_path):
        """Uppercase hex is accepted and normalized to lowercase."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        key_upper = "BEA1BF1AAB671ABB5AA9A0B7B013C1DDCBE5C7B71DD87E15BD0A7EBDC64FC96A"
        cfg.write_text(f"[reticulum]\n  rpc_key = {key_upper}\n")
        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_shared_rpc_key() == key_upper.lower()

    def test_shared_rpc_key_missing_file(self, tmp_path):
        """Missing config file => None, no exception."""
        from utils.paths import ReticulumPaths

        missing = tmp_path / "does-not-exist"
        with patch.object(ReticulumPaths, 'get_config_file', return_value=missing):
            assert ReticulumPaths.get_shared_rpc_key() is None

    def test_shared_rpc_key_derives_when_no_explicit(self, tmp_path):
        """No explicit rpc_key in config + transport_identity present at
        ``<configdir>/storage/transport_identity`` => derive
        ``Identity.full_hash(transport_identity.private_key)`` (RNS 1.2.0+
        default behavior). Sister-project MeshAnchor commits e226ccbb +
        0a6502b6 traced inbound LXMF DM drop to rpc_key mismatch when this
        derivation fallback was missing.
        """
        try:
            import RNS  # type: ignore
        except ImportError:
            pytest.skip("RNS not installed")
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n  share_instance = Yes\n")
        storage = tmp_path / "storage"
        storage.mkdir()
        identity = RNS.Identity()
        identity_path = storage / "transport_identity"
        identity.to_file(str(identity_path))
        expected = RNS.Identity.full_hash(identity.get_private_key()).hex()

        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_shared_rpc_key() == expected

    def test_shared_rpc_key_explicit_wins_over_derivation(self, tmp_path):
        """Explicit rpc_key in config beats the derivation fallback —
        operator pinning is intentional and identity-independent.
        """
        try:
            import RNS  # type: ignore
        except ImportError:
            pytest.skip("RNS not installed")
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        explicit = "ab" * 32
        cfg.write_text(
            "[reticulum]\n"
            f"  rpc_key = {explicit}\n"
        )
        # Lay down a transport_identity that would derive to something else.
        storage = tmp_path / "storage"
        storage.mkdir()
        RNS.Identity().to_file(str(storage / "transport_identity"))

        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_shared_rpc_key() == explicit

    def test_configured_instance_name_default_when_unset(self, tmp_path):
        """Missing ``instance_name`` option => 'default'."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n  share_instance = Yes\n")
        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_configured_instance_name() == 'default'

    def test_configured_instance_name_explicit(self, tmp_path):
        """Set ``instance_name`` is returned exactly (trimmed)."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n  instance_name = volcano ai rns\n")
        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_configured_instance_name() == 'volcano ai rns'

    def test_configured_instance_name_comment_ignored(self, tmp_path):
        """Commented-out line is ignored — fall back to 'default'."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n# instance_name = shadow\n")
        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_configured_instance_name() == 'default'

    def test_configured_instance_name_empty_value_falls_back(self, tmp_path):
        """``instance_name =`` (no value) => 'default', not empty string."""
        from utils.paths import ReticulumPaths

        cfg = tmp_path / "config"
        cfg.write_text("[reticulum]\n  instance_name = \n")
        with patch.object(ReticulumPaths, 'get_config_file', return_value=cfg):
            assert ReticulumPaths.get_configured_instance_name() == 'default'

    def test_configured_instance_name_missing_file(self, tmp_path):
        """Missing config file => 'default', no exception."""
        from utils.paths import ReticulumPaths

        missing = tmp_path / "does-not-exist"
        with patch.object(ReticulumPaths, 'get_config_file', return_value=missing):
            assert ReticulumPaths.get_configured_instance_name() == 'default'

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

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/<user>'))
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


class TestReticulumClientConfigdir:
    """ReticulumPaths.ensure_rns_client_configdir() — the ONE source of the
    gateway's RNS client configdir, so the process singleton's resourcepath is
    deterministic (gw-resourcepath-determinism, 2026-06-27). The FIXED location
    + NO-interface config are the two load-bearing invariants."""

    def _call(self, tmp_path, instance="volcano", rpc="deadbeef"):
        from utils.paths import ReticulumPaths
        with patch("tempfile.gettempdir", return_value=str(tmp_path)), \
             patch.object(ReticulumPaths, "get_configured_instance_name",
                          return_value=instance), \
             patch.object(ReticulumPaths, "get_shared_rpc_key",
                          return_value=rpc):
            return ReticulumPaths.ensure_rns_client_configdir()

    def test_returns_canonical_meshforge_rns_client(self, tmp_path):
        d = self._call(tmp_path)
        assert d == os.path.join(str(tmp_path), "meshforge_rns_client")

    def test_deterministic_same_path_each_call(self, tmp_path):
        # The whole point: two callers (bridge + node_tracker) get the SAME dir.
        assert self._call(tmp_path) == self._call(tmp_path)

    def test_writes_no_interface_client_config(self, tmp_path):
        d = self._call(tmp_path)
        with open(os.path.join(d, "config"), encoding="utf-8") as fh:
            cfg = fh.read()
        assert "share_instance = Yes" in cfg
        assert "[reticulum]" in cfg
        # Invariant: NO interface section ([[...]]) — would bind ports rnsd
        # owns. (The header comment mentions "interfaces" by design, so match
        # the section marker, not the word.)
        assert "[[" not in cfg

    def test_includes_instance_name_and_rpc_key(self, tmp_path):
        d = self._call(tmp_path, instance="volcano ai rns", rpc="ab12cd")
        with open(os.path.join(d, "config"), encoding="utf-8") as fh:
            cfg = fh.read()
        assert "instance_name = volcano ai rns" in cfg
        assert "rpc_key = ab12cd" in cfg

    def test_omits_rpc_key_when_unpinned(self, tmp_path):
        d = self._call(tmp_path, rpc=None)
        with open(os.path.join(d, "config"), encoding="utf-8") as fh:
            cfg = fh.read()
        assert "rpc_key" not in cfg
