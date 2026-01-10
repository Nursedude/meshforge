"""
Tests for launcher.py

Tests the launcher wizard functionality including:
- Preferences management
- Environment detection
- Interface recommendation
- Menu display logic
"""

import pytest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestColors:
    """Test the Colors class"""

    def test_color_codes_defined(self):
        """All color codes should be defined"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import Colors

        assert Colors.CYAN.startswith('\033[')
        assert Colors.GREEN.startswith('\033[')
        assert Colors.YELLOW.startswith('\033[')
        assert Colors.RED.startswith('\033[')
        assert Colors.BOLD.startswith('\033[')
        assert Colors.DIM.startswith('\033[')
        assert Colors.NC == '\033[0m'

    def test_nc_resets_color(self):
        """NC should be the reset code"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import Colors

        assert Colors.NC == '\033[0m'


class TestPreferences:
    """Test preference loading and saving"""

    def test_load_preferences_empty(self, tmp_path):
        """Loading from non-existent file returns empty dict"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import load_preferences

        with patch('launcher.CONFIG_FILE', tmp_path / 'nonexistent.json'):
            prefs = load_preferences()
            assert prefs == {}

    def test_save_and_load_preferences(self, tmp_path):
        """Saved preferences can be loaded back"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import save_preferences, load_preferences

        config_file = tmp_path / 'prefs.json'

        with patch('launcher.CONFIG_DIR', tmp_path):
            with patch('launcher.CONFIG_FILE', config_file):
                test_prefs = {'interface': '1', 'auto_launch': True}
                save_preferences(test_prefs)

                assert config_file.exists()

                loaded = load_preferences()
                assert loaded == test_prefs

    def test_load_corrupted_preferences(self, tmp_path):
        """Corrupted preferences file returns empty dict"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import load_preferences

        config_file = tmp_path / 'prefs.json'
        config_file.write_text('{ invalid json }')

        with patch('launcher.CONFIG_FILE', config_file):
            prefs = load_preferences()
            assert prefs == {}


class TestEnvironmentDetection:
    """Test environment detection"""

    def test_detect_environment_returns_dict(self):
        """detect_environment returns a dictionary with expected keys"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import detect_environment

        env = detect_environment()

        assert isinstance(env, dict)
        assert 'has_display' in env
        assert 'display_type' in env
        assert 'is_ssh' in env
        assert 'has_gtk' in env
        assert 'has_textual' in env
        assert 'is_root' in env
        assert 'terminal' in env

    def test_detect_display_x11(self):
        """X11 display is detected"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import detect_environment

        with patch.dict(os.environ, {'DISPLAY': ':0'}, clear=False):
            with patch.dict(os.environ, {'WAYLAND_DISPLAY': ''}, clear=False):
                env = detect_environment()
                assert env['has_display'] is True
                assert env['display_type'] == 'X11'

    def test_detect_display_wayland(self):
        """Wayland display is detected"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import detect_environment

        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-0'}, clear=False):
            env = detect_environment()
            assert env['has_display'] is True
            assert env['display_type'] == 'Wayland'

    def test_detect_no_display(self):
        """No display detected when env vars not set"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import detect_environment

        env_no_display = {k: v for k, v in os.environ.items()
                         if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}

        with patch.dict(os.environ, env_no_display, clear=True):
            env = detect_environment()
            assert env['has_display'] is False

    def test_detect_ssh_session(self):
        """SSH session is detected via SSH_CLIENT"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import detect_environment

        with patch.dict(os.environ, {'SSH_CLIENT': '192.168.1.1 12345 22'}, clear=False):
            env = detect_environment()
            assert env['is_ssh'] is True

    def test_detect_ssh_tty(self):
        """SSH session is detected via SSH_TTY"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import detect_environment

        with patch.dict(os.environ, {'SSH_TTY': '/dev/pts/0'}, clear=False):
            env = detect_environment()
            assert env['is_ssh'] is True

    def test_is_root_detection(self):
        """Root detection works"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import detect_environment

        env = detect_environment()
        # Just verify the key exists and is boolean
        assert isinstance(env['is_root'], bool)


class TestRecommendation:
    """Test interface recommendation logic"""

    def test_recommend_gtk_with_display(self):
        """GTK recommended with display and GTK available"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import get_recommendation

        env = {
            'has_display': True,
            'has_gtk': True,
            'is_ssh': False,
            'has_textual': True,
        }
        assert get_recommendation(env) == '1'

    def test_recommend_tui_ssh_with_textual(self):
        """Textual TUI recommended over SSH with textual installed"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import get_recommendation

        env = {
            'has_display': False,
            'has_gtk': False,
            'is_ssh': True,
            'has_textual': True,
        }
        assert get_recommendation(env) == '2'

    def test_recommend_web_ssh_no_textual(self):
        """Web interface recommended over SSH without textual"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import get_recommendation

        env = {
            'has_display': False,
            'has_gtk': False,
            'is_ssh': True,
            'has_textual': False,
        }
        assert get_recommendation(env) == '3'

    def test_recommend_cli_fallback(self):
        """CLI recommended as fallback"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import get_recommendation

        env = {
            'has_display': False,
            'has_gtk': False,
            'is_ssh': False,
            'has_textual': False,
        }
        assert get_recommendation(env) == '4'


class TestFirstRun:
    """Test first run detection"""

    def test_first_run_no_marker(self, tmp_path):
        """First run detected when no marker exists"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import check_first_run

        # Point to a temp path without the marker
        with patch('launcher.get_real_user_home', return_value=tmp_path):
            assert check_first_run() is True

    def test_not_first_run_with_marker(self, tmp_path):
        """Not first run when marker exists"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import check_first_run

        # Create the marker
        marker = tmp_path / ".meshforge" / ".setup_complete"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("completed")

        with patch('launcher.get_real_user_home', return_value=tmp_path):
            assert check_first_run() is False


class TestMenuPrinting:
    """Test menu display functions"""

    def test_print_banner_no_crash(self, capsys):
        """print_banner executes without error"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import print_banner

        print_banner()
        captured = capsys.readouterr()
        assert 'MeshForge' in captured.out or 'Meshtasticd' in captured.out

    def test_print_environment_info(self, capsys):
        """print_environment_info displays environment info"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import print_environment_info

        env = {
            'has_display': True,
            'display_type': 'X11',
            'is_ssh': False,
            'has_gtk': True,
            'has_textual': False,
        }

        print_environment_info(env)
        captured = capsys.readouterr()

        assert 'Display' in captured.out or 'display' in captured.out
        assert 'X11' in captured.out

    def test_print_menu_shows_options(self, capsys):
        """print_menu shows all interface options"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import print_menu

        env = {
            'has_display': True,
            'display_type': 'X11',
            'is_ssh': False,
            'has_gtk': True,
            'has_textual': True,
        }

        print_menu(env, recommended='1', saved_pref=None)
        captured = capsys.readouterr()

        # Check that options are shown
        assert 'GTK4' in captured.out or 'GUI' in captured.out
        assert 'TUI' in captured.out or 'Textual' in captured.out
        assert 'Web' in captured.out
        assert 'CLI' in captured.out


class TestVersionImport:
    """Test version import handling"""

    def test_version_exists(self):
        """Version should be defined"""
        import sys
        sys.path.insert(0, 'src')
        from launcher import __version__

        assert __version__ is not None
        assert isinstance(__version__, str)
        # Should be a version-like string
        assert '.' in __version__
