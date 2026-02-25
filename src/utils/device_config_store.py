"""
MeshForge Device Config Store

Persists device-level Meshtastic settings (modem preset, channels, owner name,
etc.) to a MeshForge-managed YAML file. These settings are NOT supported by
meshtasticd's config.d/ YAML overlay — they can only be applied via the
meshtastic CLI or protobuf admin API after the daemon starts.

After meshtasticd restarts, this store enables MeshForge to re-apply saved
settings automatically so the user doesn't lose their configuration.

Usage:
    from utils.device_config_store import save_device_setting, apply_saved_config

    # Save a setting after successful CLI apply
    save_device_setting('lora', 'modem_preset', 'LONG_FAST')

    # Re-apply all saved settings after meshtasticd restart
    ok, msg = apply_saved_config(cli)
"""

import logging
import os
import time
import yaml
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from utils.paths import MeshForgePaths, atomic_write_text

logger = logging.getLogger(__name__)

DEVICE_CONFIG_FILE = 'device_config.yaml'
DEVICE_CONFIG_HEADER = (
    "# MeshForge saved device settings\n"
    "# Re-applied automatically after meshtasticd restart\n"
    "# Edit via MeshForge TUI, not directly\n"
)


def _get_config_path() -> Path:
    """Get path to device config file."""
    return MeshForgePaths.get_config_dir() / DEVICE_CONFIG_FILE


def load_device_config() -> Dict[str, Any]:
    """Load saved device settings from disk.

    Returns:
        Dict of saved settings, or empty dict if no file or parse error.
    """
    path = _get_config_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Failed to read device config store: %s", e)
        return {}


def save_device_setting(section: str, key: str, value: Any) -> bool:
    """Save a single device setting to the config store.

    Args:
        section: Config section (e.g., 'lora', 'owner', 'mqtt')
        key: Setting key (e.g., 'modem_preset', 'long_name')
        value: Setting value

    Returns:
        True if saved successfully.
    """
    try:
        config = load_device_config()
        if section not in config:
            config[section] = {}
        config[section][key] = value

        content = DEVICE_CONFIG_HEADER + "\n" + yaml.dump(
            config, default_flow_style=False, sort_keys=False
        )
        atomic_write_text(_get_config_path(), content)

        # Fix ownership if running under sudo
        _fix_file_ownership(_get_config_path())

        logger.info("Saved device setting: %s.%s = %s", section, key, value)
        return True
    except Exception as e:
        logger.error("Failed to save device setting %s.%s: %s", section, key, e)
        return False


def save_device_settings(settings: Dict[str, Dict[str, Any]]) -> bool:
    """Save multiple device settings at once.

    Args:
        settings: Nested dict like {'lora': {'modem_preset': 'LONG_FAST'}}

    Returns:
        True if saved successfully.
    """
    try:
        config = load_device_config()
        for section, values in settings.items():
            if section not in config:
                config[section] = {}
            config[section].update(values)

        content = DEVICE_CONFIG_HEADER + "\n" + yaml.dump(
            config, default_flow_style=False, sort_keys=False
        )
        atomic_write_text(_get_config_path(), content)
        _fix_file_ownership(_get_config_path())

        logger.info("Saved device settings: %s", list(settings.keys()))
        return True
    except Exception as e:
        logger.error("Failed to save device settings: %s", e)
        return False


def clear_device_config() -> bool:
    """Remove the saved device config file.

    Returns:
        True if removed or didn't exist.
    """
    path = _get_config_path()
    try:
        if path.exists():
            path.unlink()
            logger.info("Cleared device config store")
        return True
    except Exception as e:
        logger.error("Failed to clear device config: %s", e)
        return False


def apply_saved_config(cli=None) -> Tuple[bool, str]:
    """Re-apply all saved device settings via meshtastic CLI.

    Should be called after meshtasticd restart to restore device config.

    Args:
        cli: MeshtasticCLI instance (auto-created if None)

    Returns:
        Tuple of (all_succeeded: bool, summary_message: str)
    """
    config = load_device_config()
    if not config:
        return True, "No saved device settings to apply"

    if cli is None:
        try:
            from core.meshtastic_cli import get_cli
            cli = get_cli()
        except Exception as e:
            return False, f"Cannot create CLI: {e}"

    results = []
    all_ok = True

    # Apply LoRa settings
    lora = config.get('lora', {})
    if lora.get('modem_preset'):
        ok = _apply_setting(cli, 'lora.modem_preset', str(lora['modem_preset']))
        results.append(f"modem_preset={lora['modem_preset']}: {'OK' if ok else 'FAILED'}")
        all_ok = all_ok and ok

    if 'channel_num' in lora:
        ok = _apply_setting(cli, 'lora.channel_num', str(lora['channel_num']))
        results.append(f"channel_num={lora['channel_num']}: {'OK' if ok else 'FAILED'}")
        all_ok = all_ok and ok

    if lora.get('region'):
        ok = _apply_setting(cli, 'lora.region', str(lora['region']))
        results.append(f"region={lora['region']}: {'OK' if ok else 'FAILED'}")
        all_ok = all_ok and ok

    if 'hop_limit' in lora:
        ok = _apply_setting(cli, 'lora.hop_limit', str(lora['hop_limit']))
        results.append(f"hop_limit={lora['hop_limit']}: {'OK' if ok else 'FAILED'}")
        all_ok = all_ok and ok

    # Apply owner settings
    owner = config.get('owner', {})
    if owner.get('long_name'):
        ok = _apply_owner(cli, owner['long_name'], short=False)
        results.append(f"long_name={owner['long_name']}: {'OK' if ok else 'FAILED'}")
        all_ok = all_ok and ok

    if owner.get('short_name'):
        ok = _apply_owner(cli, owner['short_name'], short=True)
        results.append(f"short_name={owner['short_name']}: {'OK' if ok else 'FAILED'}")
        all_ok = all_ok and ok

    # Apply MQTT settings
    mqtt = config.get('mqtt', {})
    if 'enabled' in mqtt:
        val = 'true' if mqtt['enabled'] else 'false'
        ok = _apply_setting(cli, 'mqtt.enabled', val)
        results.append(f"mqtt.enabled={val}: {'OK' if ok else 'FAILED'}")
        all_ok = all_ok and ok

    if mqtt.get('address'):
        ok = _apply_setting(cli, 'mqtt.address', mqtt['address'])
        results.append(f"mqtt.address={mqtt['address']}: {'OK' if ok else 'FAILED'}")
        all_ok = all_ok and ok

    summary = "\n".join(results) if results else "No settings to apply"
    return all_ok, summary


def verify_setting(cli, key: str, expected: str) -> bool:
    """Verify a setting took effect by reading it back.

    Args:
        cli: MeshtasticCLI instance
        key: Full setting key (e.g., 'lora.modem_preset')
        expected: Expected value string

    Returns:
        True if the expected value was found in the readback.
    """
    section = key.split('.')[0] if '.' in key else key
    try:
        result = cli.run(['--get', section])
        if result.success and expected.lower() in result.output.lower():
            return True
        logger.debug("Verify failed for %s: expected '%s' in output", key, expected)
        return False
    except Exception as e:
        logger.debug("Verify exception for %s: %s", key, e)
        return False


def _apply_setting(cli, key: str, value: str) -> bool:
    """Apply a single --set setting with retry."""
    for attempt in range(2):
        result = cli.run(['--set', key, value])
        if result.success:
            # Brief pause to let device process
            time.sleep(0.3)
            return True
        if attempt == 0:
            time.sleep(1)
    logger.warning("Failed to apply %s=%s after 2 attempts", key, value)
    return False


def _apply_owner(cli, name: str, short: bool = False) -> bool:
    """Apply owner name setting."""
    flag = '--set-owner-short' if short else '--set-owner'
    for attempt in range(2):
        result = cli.run([flag, name])
        if result.success:
            time.sleep(0.3)
            return True
        if attempt == 0:
            time.sleep(1)
    return False


def _fix_file_ownership(path: Path) -> None:
    """Fix ownership of config file when running under sudo."""
    sudo_user = os.environ.get('SUDO_USER', '')
    if not sudo_user or sudo_user == 'root' or '/' in sudo_user or '..' in sudo_user:
        return
    try:
        import pwd
        pw = pwd.getpwnam(sudo_user)
        if path.exists() and path.stat().st_uid == 0:
            os.chown(str(path), pw.pw_uid, pw.pw_gid)
    except (KeyError, OSError):
        pass
