"""
MeshForge Daemon Configuration

Loads daemon-specific settings from:
  1. /etc/meshforge/daemon.yaml (system-wide)
  2. ~/.config/meshforge/daemon.yaml (user override)
  3. CLI arguments (highest priority)

Settings control which services run and their parameters.

Usage:
    from daemon_config import DaemonConfig

    config = DaemonConfig.load()
    config = DaemonConfig.load(config_path=Path("/etc/meshforge/daemon.yaml"))
    config = DaemonConfig.load(profile=profile)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from utils.safe_import import safe_import
from utils.paths import get_real_user_home

_yaml, _HAS_YAML = safe_import('yaml')

logger = logging.getLogger(__name__)

# Config search paths (first found wins, later overrides earlier)
SYSTEM_CONFIG = Path("/etc/meshforge/daemon.yaml")
USER_CONFIG_RELATIVE = Path(".config/meshforge/daemon.yaml")


@dataclass
class DaemonConfig:
    """Configuration for daemon mode services."""

    # --- Service toggles ---
    gateway_enabled: bool = True
    health_probe_enabled: bool = True
    health_probe_interval: int = 30
    mqtt_enabled: bool = False
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    config_api_enabled: bool = True
    config_api_port: int = 8081
    map_server_enabled: bool = False
    map_server_port: int = 5000
    telemetry_enabled: bool = False
    telemetry_poll_interval_minutes: int = 30
    node_tracker_enabled: bool = True

    # --- Watchdog ---
    watchdog_interval: int = 60
    max_restarts: int = 5

    # --- Status reporting ---
    status_file_interval: int = 30  # seconds between status file writes

    # --- Logging ---
    log_level: str = "INFO"
    log_file: Optional[str] = None  # None = journald or stderr

    # --- PID ---
    pid_dir: str = "/run/meshforge"

    @classmethod
    def load(
        cls,
        config_path: Optional[Path] = None,
        profile=None,
    ) -> "DaemonConfig":
        """Load config from YAML files, apply profile defaults, merge.

        Priority (highest wins):
          1. Explicit config_path argument
          2. User config (~/.config/meshforge/daemon.yaml)
          3. System config (/etc/meshforge/daemon.yaml)
          4. Deployment profile feature_flags
          5. Dataclass defaults

        Args:
            config_path: Explicit YAML config file path.
            profile: Deployment profile (has .feature_flags dict).

        Returns:
            Populated DaemonConfig instance.
        """
        config = cls()

        # Apply deployment profile defaults first (lowest priority)
        if profile is not None:
            config._apply_profile(profile)

        # Load YAML configs (system first, then user override)
        for path in cls._config_search_paths(config_path):
            if path.exists():
                config._load_yaml(path)

        return config

    @staticmethod
    def _config_search_paths(explicit: Optional[Path] = None):
        """Return config file paths in load order (first = lowest priority)."""
        paths = []
        if SYSTEM_CONFIG.exists():
            paths.append(SYSTEM_CONFIG)

        user_config = get_real_user_home() / USER_CONFIG_RELATIVE
        if user_config.exists():
            paths.append(user_config)

        if explicit is not None:
            paths.append(explicit)

        return paths

    def _apply_profile(self, profile) -> None:
        """Apply deployment profile feature flags as defaults."""
        flags = getattr(profile, 'feature_flags', {})
        if not flags:
            return

        # Map profile feature flags to daemon config fields
        flag_map = {
            'gateway': 'gateway_enabled',
            'mqtt': 'mqtt_enabled',
            'maps': 'map_server_enabled',
            'meshtastic': 'gateway_enabled',
            'rns': 'gateway_enabled',
        }
        for flag_name, config_attr in flag_map.items():
            if flag_name in flags:
                setattr(self, config_attr, flags[flag_name])

    def _load_yaml(self, path: Path) -> None:
        """Load and merge a YAML config file."""
        if not _HAS_YAML:
            logger.warning(f"PyYAML not installed, skipping config: {path}")
            return

        try:
            with open(path, 'r') as f:
                data = _yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Failed to load config {path}: {e}")
            return

        if not isinstance(data, dict):
            return

        # Flat mapping from YAML keys to dataclass fields
        field_map = {
            'gateway': 'gateway_enabled',
            'health_probe': 'health_probe_enabled',
            'health_probe_interval': 'health_probe_interval',
            'mqtt': 'mqtt_enabled',
            'mqtt_broker': 'mqtt_broker',
            'mqtt_port': 'mqtt_port',
            'config_api': 'config_api_enabled',
            'config_api_port': 'config_api_port',
            'map_server': 'map_server_enabled',
            'map_server_port': 'map_server_port',
            'telemetry': 'telemetry_enabled',
            'telemetry_poll_interval_minutes': 'telemetry_poll_interval_minutes',
            'node_tracker': 'node_tracker_enabled',
            'watchdog_interval': 'watchdog_interval',
            'max_restarts': 'max_restarts',
            'status_file_interval': 'status_file_interval',
            'log_level': 'log_level',
            'log_file': 'log_file',
            'pid_dir': 'pid_dir',
        }

        for yaml_key, attr_name in field_map.items():
            if yaml_key in data:
                setattr(self, attr_name, data[yaml_key])

        logger.debug(f"Loaded daemon config from {path}")

    def to_dict(self) -> dict:
        """Serialize config to dict (for status reporting)."""
        return {
            'gateway_enabled': self.gateway_enabled,
            'health_probe_enabled': self.health_probe_enabled,
            'health_probe_interval': self.health_probe_interval,
            'mqtt_enabled': self.mqtt_enabled,
            'config_api_enabled': self.config_api_enabled,
            'map_server_enabled': self.map_server_enabled,
            'telemetry_enabled': self.telemetry_enabled,
            'node_tracker_enabled': self.node_tracker_enabled,
            'watchdog_interval': self.watchdog_interval,
            'log_level': self.log_level,
        }
