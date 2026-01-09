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


class TestPathConsistency:
    """Test consistency between path functions."""

    def test_home_contains_username(self):
        """Test that home path is consistent with username."""
        with patch.dict(os.environ, {'SUDO_USER': 'wh6gxz'}):
            home = get_real_user_home()
            user = get_real_username()

            assert user in str(home)
