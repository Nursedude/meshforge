"""
MeshForge Radio Mode Abstraction

Determines which LoRa radio firmware is the primary radio for this MeshForge
instance. This controls gateway bridge startup order, TUI menu layout, and
default configurations.

Modes:
  MESHTASTIC  -> meshtasticd is primary (current default, unchanged behavior)
  MESHCORE    -> MeshCore companion radio is primary, MeshForge manages directly
  DUAL        -> Both radios active as equal peers, gateway bridges all networks

Persistence:
  - User preference: ~/.config/meshforge/radio_mode.json
  - Admin override: /etc/meshforge/noc.yaml (takes precedence when running as root)

Usage:
    from core.radio_mode import get_radio_mode, set_radio_mode, RadioMode

    mode = get_radio_mode()
    if mode == RadioMode.MESHCORE:
        # MeshCore is primary radio
        ...

    set_radio_mode(RadioMode.DUAL)
"""

import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class RadioMode(Enum):
    """Primary radio mode for this MeshForge instance."""
    MESHTASTIC = "meshtastic"   # meshtasticd is primary (default)
    MESHCORE = "meshcore"       # MeshCore companion radio is primary
    DUAL = "dual"               # Both radios active, no hierarchy


# Default mode preserves current behavior
DEFAULT_MODE = RadioMode.MESHTASTIC

# Config file locations
_USER_CONFIG_FILE = "radio_mode.json"
_ADMIN_CONFIG_DIR = Path("/etc/meshforge")
_ADMIN_CONFIG_KEY = "radio_mode"


def _get_user_config_path() -> Path:
    """Get user-level radio mode config path (MF001 compliant)."""
    return get_real_user_home() / ".config" / "meshforge" / _USER_CONFIG_FILE


def _load_admin_mode() -> Optional[RadioMode]:
    """Load radio mode from admin config (/etc/meshforge/noc.yaml).

    Returns:
        RadioMode if set in admin config, None otherwise.
    """
    noc_config = _ADMIN_CONFIG_DIR / "noc.yaml"
    if not noc_config.exists():
        return None

    try:
        # Use yaml if available, fallback to manual parsing
        content = noc_config.read_text()
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("radio_mode:"):
                value = line.split(":", 1)[1].strip().strip('"').strip("'")
                return RadioMode(value)
    except (ValueError, OSError) as e:
        logger.debug(f"Could not read admin radio mode: {e}")
    return None


def _load_user_mode() -> Optional[RadioMode]:
    """Load radio mode from user config (~/.config/meshforge/radio_mode.json).

    Returns:
        RadioMode if set in user config, None otherwise.
    """
    config_path = _get_user_config_path()
    if not config_path.exists():
        return None

    try:
        data = json.loads(config_path.read_text())
        value = data.get("radio_mode", "")
        if value:
            return RadioMode(value)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.debug(f"Could not read user radio mode: {e}")
    return None


def get_radio_mode() -> RadioMode:
    """Get the active radio mode.

    Resolution order:
      1. Admin config (/etc/meshforge/noc.yaml) -- highest priority
      2. User config (~/.config/meshforge/radio_mode.json)
      3. DEFAULT_MODE (meshtastic)

    Returns:
        Active RadioMode enum value.
    """
    # Admin override takes precedence (when running as root/sudo)
    admin_mode = _load_admin_mode()
    if admin_mode is not None:
        return admin_mode

    # User preference
    user_mode = _load_user_mode()
    if user_mode is not None:
        return user_mode

    return DEFAULT_MODE


def set_radio_mode(mode: RadioMode, admin: bool = False) -> bool:
    """Persist radio mode selection.

    Args:
        mode: RadioMode to set.
        admin: If True, write to /etc/meshforge/noc.yaml (requires root).
               If False, write to user config.

    Returns:
        True if saved successfully, False on error.
    """
    if admin:
        return _save_admin_mode(mode)
    return _save_user_mode(mode)


def _save_user_mode(mode: RadioMode) -> bool:
    """Save radio mode to user config."""
    config_path = _get_user_config_path()
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Merge with existing config if present
        data = {}
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        data["radio_mode"] = mode.value
        config_path.write_text(json.dumps(data, indent=2) + "\n")
        logger.info(f"Radio mode set to {mode.value} (user config)")
        return True
    except OSError as e:
        logger.error(f"Failed to save radio mode: {e}")
        return False


def _save_admin_mode(mode: RadioMode) -> bool:
    """Save radio mode to admin config (/etc/meshforge/noc.yaml)."""
    noc_config = _ADMIN_CONFIG_DIR / "noc.yaml"
    try:
        _ADMIN_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        # Read existing content or start fresh
        lines = []
        found = False
        if noc_config.exists():
            for line in noc_config.read_text().splitlines():
                if line.strip().startswith("radio_mode:"):
                    lines.append(f"radio_mode: {mode.value}")
                    found = True
                else:
                    lines.append(line)

        if not found:
            lines.append(f"radio_mode: {mode.value}")

        noc_config.write_text("\n".join(lines) + "\n")
        logger.info(f"Radio mode set to {mode.value} (admin config)")
        return True
    except OSError as e:
        logger.error(f"Failed to save admin radio mode: {e}")
        return False


def get_default_bridge_mode(radio_mode: Optional[RadioMode] = None) -> str:
    """Get the default gateway bridge mode for a given radio mode.

    Args:
        radio_mode: RadioMode to check. If None, uses current active mode.

    Returns:
        Bridge mode string for GatewayConfig.bridge_mode.
    """
    if radio_mode is None:
        radio_mode = get_radio_mode()

    mode_to_bridge = {
        RadioMode.MESHTASTIC: "mqtt_bridge",
        RadioMode.MESHCORE: "meshcore_primary",
        RadioMode.DUAL: "tri_bridge",
    }
    return mode_to_bridge.get(radio_mode, "mqtt_bridge")


def is_meshcore_primary() -> bool:
    """Check if MeshCore is the primary radio."""
    return get_radio_mode() == RadioMode.MESHCORE


def is_meshtastic_primary() -> bool:
    """Check if Meshtastic is the primary radio (default)."""
    return get_radio_mode() == RadioMode.MESHTASTIC


def is_dual_mode() -> bool:
    """Check if both radios are active."""
    return get_radio_mode() == RadioMode.DUAL
