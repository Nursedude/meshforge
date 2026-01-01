"""Emoji utility with fallback support for Raspberry Pi OS terminals"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _check_emoji_fonts_installed():
    """Check if emoji fonts are installed on the system"""
    # Check for common emoji font packages
    emoji_fonts = [
        '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',
        '/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf',
        '/usr/share/fonts/truetype/noto-color-emoji/NotoColorEmoji.ttf',
        '/usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf',
    ]

    for font in emoji_fonts:
        if Path(font).exists():
            return True

    # Also check via fc-list if fontconfig is available
    try:
        result = subprocess.run(
            ['fc-list', ':family', 'emoji'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True

        # Check for Noto Color Emoji specifically
        result = subprocess.run(
            ['fc-list', ':family=Noto Color Emoji'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    return False


def _is_raspberry_pi():
    """Check if running on Raspberry Pi"""
    try:
        with open('/proc/device-tree/model', 'r') as f:
            return 'raspberry pi' in f.read().lower()
    except (FileNotFoundError, IOError):
        pass

    # Alternative check
    try:
        with open('/proc/cpuinfo', 'r') as f:
            cpuinfo = f.read().lower()
            return 'raspberry' in cpuinfo or 'bcm' in cpuinfo
    except (FileNotFoundError, IOError):
        pass

    return False


def install_emoji_fonts():
    """Install emoji fonts on Debian/Raspberry Pi OS

    Returns:
        bool: True if fonts were installed successfully
    """
    if not shutil.which('apt-get'):
        return False

    try:
        # Update package list and install fonts
        subprocess.run(
            ['apt-get', 'update', '-qq'],
            capture_output=True, timeout=60
        )
        result = subprocess.run(
            ['apt-get', 'install', '-y', 'fonts-noto-color-emoji'],
            capture_output=True, timeout=120
        )

        if result.returncode == 0:
            # Refresh font cache
            subprocess.run(['fc-cache', '-f', '-v'], capture_output=True, timeout=60)
            return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    return False


class EmojiHelper:
    """Helper class for emoji display with ASCII fallbacks"""

    def __init__(self):
        self.emoji_enabled = self._detect_emoji_support()

    def _detect_emoji_support(self):
        """Detect if terminal supports emoji

        Emojis are ENABLED if:
        1. ENABLE_EMOJI=true is set, OR
        2. Terminal supports UTF-8 AND emoji fonts are installed

        Emojis are DISABLED if:
        1. DISABLE_EMOJI=true is set, OR
        2. Terminal is dumb/vt100, OR
        3. No emoji fonts are installed on RPi
        """
        # Allow explicit disable if requested
        if os.environ.get('DISABLE_EMOJI', '').lower() in ('1', 'true', 'yes'):
            return False

        # Allow explicit enable - overrides all other checks
        if os.environ.get('ENABLE_EMOJI', '').lower() in ('1', 'true', 'yes'):
            return True

        # Check terminal type - disable for truly limited terminals
        term = os.environ.get('TERM', '').lower()
        if term in ('dumb', 'vt100', 'vt220', ''):
            return False

        # Check if we have UTF-8 support
        has_utf8 = False
        try:
            encoding = getattr(sys.stdout, 'encoding', None)
            if encoding and 'utf' in encoding.lower():
                has_utf8 = True
        except Exception:
            pass

        if not has_utf8:
            # Check locale
            lang = os.environ.get('LANG', '').lower()
            lc_all = os.environ.get('LC_ALL', '').lower()
            lc_ctype = os.environ.get('LC_CTYPE', '').lower()
            if any('utf' in loc for loc in [lang, lc_all, lc_ctype] if loc):
                has_utf8 = True

        if not has_utf8:
            return False

        # On Raspberry Pi, check if emoji fonts are actually installed
        if _is_raspberry_pi():
            if not _check_emoji_fonts_installed():
                return False

        # For SSH sessions, be more conservative
        if os.environ.get('SSH_CONNECTION'):
            # Only enable if fonts are installed and locale is UTF-8
            if not _check_emoji_fonts_installed():
                return False

        # Enable for modern terminals with UTF-8 support
        return True

    # Emoji mappings with ASCII fallbacks
    EMOJI_MAP = {
        # Status indicators
        '🔴': '[ ]',    # Stopped/Error
        '🟢': '[*]',    # Running/Success
        '🟡': '[~]',    # Warning
        '🔵': '[i]',    # Info

        # UI Elements
        '📊': '[DASH]',     # Dashboard
        '📦': '[PKG]',      # Package/Install
        '⬆️': '[UP]',       # Update/Upgrade
        '⚙️': '[CFG]',      # Configuration
        '📻': '[RADIO]',    # Radio/Channel
        '📋': '[LIST]',     # Template/List
        '🔍': '[FIND]',     # Search/Check
        '🔌': '[HW]',       # Hardware
        '🐛': '[DEBUG]',    # Debug
        '🚪': '[EXIT]',     # Exit
        '❓': '[?]',        # Help
        '🌐': '[MESH]',     # Network/Mesh
        '📡': '[ANT]',      # Antenna/Signal
        '✓': '[OK]',        # Success
        '✗': '[X]',         # Fail
        '⚠': '[!]',         # Warning
        '⚠️': '[!]',        # Warning (alternate)

        # Hardware
        '🔧': '[CFG]',      # Tools/Config
        '🎛️': '[CTRL]',     # Controls
        '🌡️': '[TEMP]',     # Temperature
        '💾': '[MEM]',      # Memory/Storage
        '💿': '[DISK]',     # Disk

        # Network
        '🏔️': '[MTN]',      # Mountain (MtnMesh)
        '🚨': '[SOS]',      # Emergency
        '🏙️': '[CITY]',     # Urban
        '📢': '[BCST]',     # Broadcast
        '🌍': '[NET]',      # World/Network
        '🔗': '[LINK]',     # Link/Connection

        # Actions
        '⬅️': '[<-]',       # Back
        '➡️': '[->]',       # Forward
        '🔄': '[RFRSH]',    # Sync/Refresh
        '🔁': '[RSTRT]',    # Restart
        '🔐': '[LOCK]',     # Security
        '📜': '[LOG]',      # Logs
        '📝': '[EDIT]',     # Edit
        '⚡': '[FAST]',     # Fast/Quick
        '👋': '[BYE]',      # Goodbye
        'ℹ️': '[i]',        # Information
        '⏰': '[TIME]',     # Time/Clock
        '⏱️': '[TIME]',     # Timer
        '📂': '[DIR]',      # Directory
        '📄': '[FILE]',     # File
        '🎉': '[NEW]',      # Celebration/New
        '✨': '[STAR]',     # Sparkle/Star
        '🎨': '[EMJ]',      # Emoji/Color
        '🗑️': '[DEL]',      # Delete/Trash
        '💻': '[CLI]',      # CLI/Terminal
        '📍': '[LOC]',      # Location/Pin
        '🗺️': '[MAP]',      # Map
        '🤙': '[SHAKA]',    # Shaka/Hang loose
        '📁': '[FOLDER]',   # Folder
    }

    def get(self, emoji, fallback=None):
        """Get emoji or ASCII fallback

        Args:
            emoji: The emoji character
            fallback: Optional custom fallback (uses default if None)

        Returns:
            Emoji if supported, otherwise ASCII fallback
        """
        if self.emoji_enabled:
            return emoji

        if fallback:
            return fallback

        return self.EMOJI_MAP.get(emoji, emoji)

    def enable(self):
        """Force enable emoji"""
        self.emoji_enabled = True

    def disable(self):
        """Force disable emoji"""
        self.emoji_enabled = False

    def is_enabled(self):
        """Check if emoji is enabled"""
        return self.emoji_enabled


# Global instance
_emoji = EmojiHelper()


def get(emoji, fallback=None):
    """Get emoji or fallback (convenience function)"""
    return _emoji.get(emoji, fallback)


def enable():
    """Enable emoji globally"""
    _emoji.enable()


def disable():
    """Disable emoji globally"""
    _emoji.disable()


def is_enabled():
    """Check if emoji is enabled"""
    return _emoji.is_enabled()


# Common emoji shortcuts
def status_running():
    """Running status indicator"""
    return get('🟢', '[*]')


def status_stopped():
    """Stopped status indicator"""
    return get('🔴', '[ ]')


def status_warning():
    """Warning status indicator"""
    return get('🟡', '[~]')


def status_info():
    """Info status indicator"""
    return get('🔵', '[i]')


def check_emoji_status():
    """Get detailed emoji support status

    Returns:
        dict: Status information about emoji support
    """
    return {
        'enabled': _emoji.is_enabled(),
        'fonts_installed': _check_emoji_fonts_installed(),
        'is_raspberry_pi': _is_raspberry_pi(),
        'term': os.environ.get('TERM', ''),
        'lang': os.environ.get('LANG', ''),
        'encoding': getattr(sys.stdout, 'encoding', 'unknown'),
        'ssh_session': bool(os.environ.get('SSH_CONNECTION')),
    }


def setup_emoji_support(console=None):
    """Interactive setup for emoji support on Raspberry Pi

    Args:
        console: Optional Rich console for output

    Returns:
        bool: True if emojis are now enabled
    """
    if console is None:
        from rich.console import Console
        console = Console()

    status = check_emoji_status()

    console.print("\n[bold cyan]Emoji Support Status[/bold cyan]")
    console.print(f"  Enabled: {'[green]Yes[/green]' if status['enabled'] else '[red]No[/red]'}")
    console.print(f"  Fonts installed: {'[green]Yes[/green]' if status['fonts_installed'] else '[yellow]No[/yellow]'}")
    console.print(f"  Raspberry Pi: {'Yes' if status['is_raspberry_pi'] else 'No'}")
    console.print(f"  Terminal: {status['term']}")
    console.print(f"  Encoding: {status['encoding']}")
    console.print(f"  SSH session: {'Yes' if status['ssh_session'] else 'No'}")

    if status['enabled']:
        console.print("\n[green]Emojis are working![/green] 🎉 ✨ 🟢")
        return True

    if not status['fonts_installed']:
        console.print("\n[yellow]Emoji fonts are not installed.[/yellow]")
        console.print("To enable emojis, run:")
        console.print("[cyan]  sudo apt install fonts-noto-color-emoji && fc-cache -f[/cyan]")
        console.print("\nOr set [cyan]ENABLE_EMOJI=true[/cyan] to force enable.")
    else:
        console.print("\n[yellow]Fonts are installed but emojis are disabled.[/yellow]")
        console.print("Try setting: [cyan]export ENABLE_EMOJI=true[/cyan]")

    return False


def reinitialize():
    """Re-detect emoji support (call after installing fonts)"""
    global _emoji
    _emoji = EmojiHelper()
