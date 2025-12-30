"""Emoji utility with fallback support for Raspberry Pi OS terminals"""

import os
import sys


class EmojiHelper:
    """Helper class for emoji display with ASCII fallbacks"""

    def __init__(self):
        self.emoji_enabled = self._detect_emoji_support()

    def _detect_emoji_support(self):
        """Detect if terminal supports emoji

        Emojis are ENABLED by default for better visual experience.
        Can be disabled via DISABLE_EMOJI=true environment variable
        """
        # Allow explicit disable if requested
        if os.environ.get('DISABLE_EMOJI', '').lower() in ('1', 'true', 'yes'):
            return False

        # Emojis enabled by default for better UI
        # Fallbacks are available for terminals without emoji support
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
