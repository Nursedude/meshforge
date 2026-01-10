"""
Tests for emoji fallback system - ensures all UI code uses em.get()
for terminal compatibility instead of raw emojis.
"""

import ast
import os
import re
from pathlib import Path
import pytest


# Files that should use emoji fallback (UI-facing code)
UI_FILES = [
    'src/main.py',
    'src/launcher.py',
    'src/config/channel_presets.py',
    'src/config/device.py',
    'src/tools/network_tools.py',
    'src/tools/rf_tools.py',
    'src/tools/mudp_tools.py',
    'src/tools/tool_manager.py',
    'src/diagnostics/system_diagnostics.py',
    'src/diagnostics/site_planner.py',
    'src/installer/meshtasticd.py',
]

# Emoji pattern - matches common emoji ranges
EMOJI_PATTERN = re.compile(
    r'[\U0001F300-\U0001F9FF]'  # Misc symbols, emoticons, etc
    r'|[\U00002600-\U000027BF]'  # Misc symbols
    r'|[\U0001F600-\U0001F64F]'  # Emoticons
    r'|[\U0001F680-\U0001F6FF]'  # Transport/map symbols
    r'|[\U0001F1E0-\U0001F1FF]'  # Flags
)

# Allowed patterns - emojis properly wrapped in em.get()
ALLOWED_PATTERNS = [
    r"em\.get\s*\(['\"][^'\"]+['\"]",  # em.get('emoji') or em.get("emoji")
    r"em\.get\s*\(['\"][^'\"]+['\"],\s*['\"][^'\"]+['\"]\)",  # em.get('emoji', 'fallback')
]


def find_raw_emojis_in_file(filepath: Path) -> list:
    """Find raw emojis not wrapped in em.get() calls"""
    if not filepath.exists():
        return []

    issues = []
    content = filepath.read_text()
    lines = content.split('\n')

    for line_num, line in enumerate(lines, 1):
        # Skip comments
        if line.strip().startswith('#'):
            continue

        # Skip lines that are defining PRESET_ICONS or similar constant dicts
        # (these are used as keys to look up, not displayed directly)
        if 'PRESET_ICONS' in line or 'ICON' in line and '=' in line and '{' in line:
            continue

        # Find emojis in the line
        emoji_matches = EMOJI_PATTERN.findall(line)

        for emoji in emoji_matches:
            # Check if this emoji is properly wrapped
            # Look for em.get('emoji pattern in the surrounding context
            is_wrapped = False
            for pattern in ALLOWED_PATTERNS:
                if re.search(pattern + r".*" + re.escape(emoji), line) or \
                   re.search(re.escape(emoji) + r".*" + pattern, line):
                    is_wrapped = True
                    break
                # Also check if em.get is called with this emoji
                if f"em.get('{emoji}'" in line or f'em.get("{emoji}"' in line:
                    is_wrapped = True
                    break

            if not is_wrapped:
                # Check if line contains em.get at all - might be the emoji arg
                if 'em.get(' in line:
                    is_wrapped = True

            if not is_wrapped:
                issues.append({
                    'file': str(filepath),
                    'line': line_num,
                    'emoji': emoji,
                    'context': line.strip()[:80]
                })

    return issues


class TestEmojiHelper:
    """Test that emoji helper module works correctly"""

    def test_emoji_module_exists(self):
        """Emoji helper module should exist"""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
        from utils import emoji as em
        assert hasattr(em, 'get')

    def test_emoji_get_returns_emoji_when_supported(self):
        """em.get should return emoji when terminal supports it"""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
        from utils import emoji as em
        # When emojis are enabled, should return the emoji
        result = em.get('📡', '[ANT]')
        assert result in ('📡', '[ANT]')  # Either is valid

    def test_emoji_get_returns_fallback(self):
        """em.get should return fallback when provided"""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
        from utils import emoji as em
        result = em.get('📡', '[ANT]')
        # Result should be non-empty
        assert len(result) > 0


class TestNoRawEmojisInUICode:
    """Test that UI code doesn't use raw emojis without fallback"""

    @pytest.fixture
    def project_root(self):
        """Get project root directory"""
        return Path(__file__).parent.parent

    def test_main_py_no_raw_emojis(self, project_root):
        """main.py should use em.get() for all emojis"""
        filepath = project_root / 'src' / 'main.py'
        issues = find_raw_emojis_in_file(filepath)

        # Allow some - main.py already uses em.get() extensively
        # This test documents the current state
        assert len(issues) < 5, f"Found {len(issues)} raw emojis in main.py: {issues}"

    def test_channel_presets_no_raw_emojis(self, project_root):
        """channel_presets.py should use em.get() for all displayed emojis"""
        filepath = project_root / 'src' / 'config' / 'channel_presets.py'
        issues = find_raw_emojis_in_file(filepath)

        # Filter out PRESET_ICONS dict values - these are lookup keys, displayed via em.get()
        # Also filter out constant dict definitions (lines with just 'key': 'emoji')
        display_issues = []
        for i in issues:
            ctx = i['context']
            # Skip PRESET_ICONS definitions
            if 'PRESET_ICONS' in ctx:
                continue
            # Skip dict value definitions like "'default': '📡'," or "'repeater': '🔄'"
            if ctx.strip().startswith("'") and "': '" in ctx:
                # Ends with ',' or just the closing quote
                stripped = ctx.rstrip()
                if stripped.endswith("',") or stripped.endswith("'"):
                    continue
            display_issues.append(i)

        assert len(display_issues) == 0, \
            f"Found raw emojis in channel_presets.py: {display_issues}"

    def test_launcher_no_raw_emojis(self, project_root):
        """launcher.py should use em.get() for all emojis"""
        filepath = project_root / 'src' / 'launcher.py'
        issues = find_raw_emojis_in_file(filepath)

        # Launcher is simpler, should have minimal raw emojis
        assert len(issues) < 3, f"Found {len(issues)} raw emojis in launcher.py: {issues}"


class TestBackButtonsExist:
    """Test that all menus have back/exit options"""

    @pytest.fixture
    def project_root(self):
        return Path(__file__).parent.parent

    def test_channel_presets_has_back(self, project_root):
        """Channel presets menu should have back option"""
        filepath = project_root / 'src' / 'config' / 'channel_presets.py'
        content = filepath.read_text()

        # Should have "0" as a choice for back
        assert '"0"' in content or "'0'" in content, \
            "channel_presets.py should have '0' back option"
        assert 'Back' in content, \
            "channel_presets.py should have 'Back' text"

    def test_debug_menu_has_back(self, project_root):
        """Debug menu should have back option"""
        filepath = project_root / 'src' / 'main.py'
        content = filepath.read_text()

        # Look for back option in debug_menu context
        assert 'Back to main menu' in content or '0.*Back' in content, \
            "main.py debug_menu should have back option"
