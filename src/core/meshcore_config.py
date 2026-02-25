"""
MeshForge MeshCore Configuration Manager

Manages the /etc/meshcore/ directory structure, mirroring the pattern
established by meshtasticd_config.py for Meshtastic radios.

Directory structure:
    /etc/meshcore/
    ├── available.d/     # Available radio connection configs (templates)
    │   ├── companion-usb.yaml
    │   ├── companion-tcp.yaml
    │   └── companion-ble.yaml
    ├── config.d/        # Enabled configs (symlinks to available.d)
    │   └── active.yaml -> ../available.d/companion-usb.yaml
    └── config.yaml      # Main configuration file

Unlike meshtasticd, MeshCore has no daemon process. MeshForge connects
directly to the companion radio via the meshcore_py library. This config
manager handles the connection settings and radio templates.

Usage:
    from core.meshcore_config import MeshcoreConfig

    config = MeshcoreConfig()
    config.ensure_structure()

    # List available configs
    available = config.list_available()

    # Enable a connection config
    config.enable("companion-usb")

    # Detect connected devices
    radio_type = config.detect_radio_type()
"""

import logging
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class MeshCoreRadioType(Enum):
    """Type of MeshCore companion radio connection."""
    USB_SERIAL = "usb_serial"    # Companion radio via USB (most common)
    TCP_BRIDGE = "tcp_bridge"    # WiFi firmware or serial-to-TCP bridge
    BLE = "ble"                  # Bluetooth LE (pending meshcore_py support)
    UNKNOWN = "unknown"


@dataclass
class MeshCoreRadioConfig:
    """Configuration for a MeshCore radio connection."""
    name: str
    radio_type: MeshCoreRadioType
    device_path: Optional[str] = None
    baud_rate: int = 115200
    tcp_host: str = ""
    tcp_port: int = 4000
    description: str = ""
    enabled: bool = False
    config_file: Optional[str] = None


# Default connection templates for MeshCore companion radios
MESHCORE_TEMPLATES = {
    "companion-usb": {
        "name": "companion-usb",
        "radio_type": MeshCoreRadioType.USB_SERIAL,
        "description": "MeshCore Companion Radio via USB Serial",
        "config": """\
# MeshCore Companion Radio — USB Serial Connection
# Most common setup: companion radio connected via USB cable.
# Device path is auto-detected from /dev/ttyUSB* and /dev/ttyACM*.
#
# Compatible devices: RAK4631, Heltec V3, T-Deck, Nano G2 Ultra
# Protocol: MeshCore Companion Radio (binary framed USB)
# Requires: pip install meshcore

Connection:
  Type: serial
  Device: auto          # or specify: /dev/ttyUSB0
  BaudRate: 115200

Radio:
  AutoFetchMessages: true
  BridgeChannels: true
  BridgeDMs: true
  ChannelPollIntervalSec: 5

Logging:
  LogLevel: info
""",
    },
    "companion-tcp": {
        "name": "companion-tcp",
        "radio_type": MeshCoreRadioType.TCP_BRIDGE,
        "description": "MeshCore Companion Radio via TCP/WiFi",
        "config": """\
# MeshCore Companion Radio — TCP/WiFi Connection
# For companion radios with WiFi firmware, or serial-to-TCP bridges
# (e.g., ser2net, socat).
#
# Default port: 4000 (meshcore-cli convention)

Connection:
  Type: tcp
  Host: localhost
  Port: 4000

Radio:
  AutoFetchMessages: true
  BridgeChannels: true
  BridgeDMs: true
  ChannelPollIntervalSec: 5

Logging:
  LogLevel: info
""",
    },
    "companion-ble": {
        "name": "companion-ble",
        "radio_type": MeshCoreRadioType.BLE,
        "description": "MeshCore Companion Radio via Bluetooth LE (experimental)",
        "config": """\
# MeshCore Companion Radio — Bluetooth LE Connection
# STATUS: Config ready, pending meshcore_py BLE transport support.
# BLE GATT service UUID: 6E400001-B5A3-F393-E0A9-E50E24DCCA9E
#
# This connection type will be available once meshcore_py adds BLE support.

Connection:
  Type: ble
  # DeviceName: MeshCore-XXXX
  # MacAddress: AA:BB:CC:DD:EE:FF

Radio:
  AutoFetchMessages: true
  BridgeChannels: true
  BridgeDMs: true

Logging:
  LogLevel: info
""",
    },
    "companion-simulation": {
        "name": "companion-simulation",
        "radio_type": MeshCoreRadioType.UNKNOWN,
        "description": "MeshCore Simulator (no hardware required)",
        "config": """\
# MeshCore Simulator — Testing Without Hardware
# Generates fake events for bridge and routing testing.
# No companion radio needed.

Connection:
  Type: simulation

Radio:
  AutoFetchMessages: true
  BridgeChannels: true
  BridgeDMs: true

Logging:
  LogLevel: debug
""",
    },
}


class MeshcoreConfig:
    """
    Manages MeshCore configuration directory structure.

    Mirrors the pattern of MeshtasticdConfig for consistency.
    MeshCore has no daemon — MeshForge connects directly to the
    companion radio. This manager handles connection templates
    and device detection.
    """

    DEFAULT_CONFIG_DIR = Path("/etc/meshcore")

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize config manager.

        Args:
            config_dir: Override config directory (default: /etc/meshcore)
        """
        self.config_dir = config_dir or self.DEFAULT_CONFIG_DIR
        self.available_dir = self.config_dir / "available.d"
        self.config_d_dir = self.config_dir / "config.d"
        self.main_config = self.config_dir / "config.yaml"

    def ensure_structure(self) -> bool:
        """Ensure the configuration directory structure exists.

        Returns:
            True if structure was created/exists, False on error.
        """
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            self.available_dir.mkdir(exist_ok=True)
            self.config_d_dir.mkdir(exist_ok=True)

            # Create default templates if available.d is empty
            if not list(self.available_dir.glob("*.yaml")):
                self._create_default_templates()

            # Create main config.yaml if missing
            if not self.main_config.exists():
                self._create_main_config()

            logger.info(f"MeshCore config structure ready at {self.config_dir}")
            return True

        except PermissionError as e:
            logger.error(f"Permission denied creating config structure: {e}")
            return False
        except OSError as e:
            logger.error(f"Failed to create config structure: {e}")
            return False

    def _create_default_templates(self):
        """Create default connection configuration templates."""
        for name, template in MESHCORE_TEMPLATES.items():
            config_file = self.available_dir / f"{name}.yaml"
            if not config_file.exists():
                config_file.write_text(template["config"])
                logger.debug(f"Created MeshCore template: {config_file}")

    def _create_main_config(self):
        """Create main config.yaml file ONLY if it does not already exist."""
        if self.main_config.exists():
            logger.debug("MeshCore config.yaml already exists, preserving")
            return

        config_content = """\
# MeshCore Configuration (MeshForge)
# Connection configs are in available.d/ — enable via config.d/ symlinks.
#
# MeshCore has no daemon. MeshForge connects directly to the
# companion radio via the meshcore_py library.
#
# Companion Radio Protocol: Binary framed (USB/BLE/TCP)
# Max hops: 64 (vs 7 for Meshtastic)
# Max text payload: ~160 bytes
# Python library: pip install meshcore (requires Python 3.10+)
---

# Default connection (override by enabling a config in config.d/)
Connection:
  Type: serial
  Device: auto
  BaudRate: 115200

# Message handling
Radio:
  AutoFetchMessages: true
  BridgeChannels: true
  BridgeDMs: true
  ChannelPollIntervalSec: 5

# General settings
General:
  SimulationMode: false
  ConfigDirectory: /etc/meshcore/config.d/
  AvailableDirectory: /etc/meshcore/available.d/

Logging:
  LogLevel: info
"""
        self.main_config.write_text(config_content)
        logger.info(f"Created MeshCore main config: {self.main_config}")

    def list_available(self) -> List[MeshCoreRadioConfig]:
        """List available connection configurations.

        Returns:
            List of MeshCoreRadioConfig objects.
        """
        configs = []

        if not self.available_dir.exists():
            return configs

        for config_file in sorted(self.available_dir.glob("*.yaml")):
            name = config_file.stem

            # Check if enabled (symlink exists in config.d)
            enabled = (self.config_d_dir / config_file.name).exists()

            # Get template info if available
            template = MESHCORE_TEMPLATES.get(name, {})
            radio_type = template.get("radio_type", MeshCoreRadioType.UNKNOWN)
            description = template.get(
                "description", f"MeshCore config: {name}"
            )

            configs.append(MeshCoreRadioConfig(
                name=name,
                radio_type=radio_type,
                description=description,
                enabled=enabled,
                config_file=str(config_file),
            ))

        return configs

    def list_enabled(self) -> List[MeshCoreRadioConfig]:
        """List enabled (active) configurations."""
        return [c for c in self.list_available() if c.enabled]

    def enable(self, config_name: str) -> bool:
        """Enable a connection configuration.

        Creates a symlink in config.d/ pointing to available.d/.

        Args:
            config_name: Name of config (without .yaml extension)

        Returns:
            True if enabled successfully.
        """
        source = self.available_dir / f"{config_name}.yaml"
        target = self.config_d_dir / f"{config_name}.yaml"

        if not source.exists():
            logger.error(f"MeshCore config not found: {source}")
            return False

        try:
            if target.exists() or target.is_symlink():
                target.unlink()

            target.symlink_to(f"../available.d/{config_name}.yaml")
            logger.info(f"Enabled MeshCore config: {config_name}")
            return True

        except OSError as e:
            logger.error(f"Failed to enable {config_name}: {e}")
            return False

    def disable(self, config_name: str) -> bool:
        """Disable a connection configuration.

        Removes symlink from config.d/.

        Args:
            config_name: Name of config (without .yaml extension)

        Returns:
            True if disabled successfully.
        """
        target = self.config_d_dir / f"{config_name}.yaml"

        try:
            if target.exists() or target.is_symlink():
                target.unlink()
                logger.info(f"Disabled MeshCore config: {config_name}")
            return True

        except OSError as e:
            logger.error(f"Failed to disable {config_name}: {e}")
            return False

    def detect_radio_type(self) -> MeshCoreRadioType:
        """Auto-detect MeshCore companion radio connection type.

        Scans for USB serial devices that could be companion radios.
        Does NOT verify the device is running MeshCore firmware.

        Returns:
            MeshCoreRadioType enum value.
        """
        usb_devices = list(Path("/dev").glob("ttyUSB*")) + \
                      list(Path("/dev").glob("ttyACM*"))

        if usb_devices:
            logger.info(f"Detected USB serial device(s): {usb_devices}")
            return MeshCoreRadioType.USB_SERIAL

        return MeshCoreRadioType.UNKNOWN

    def detect_devices(self) -> List[str]:
        """Scan for potential MeshCore companion radio serial devices.

        Returns:
            List of device paths (e.g., ['/dev/ttyUSB0', '/dev/ttyACM1']).
        """
        import glob as glob_mod
        devices = []
        for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
            devices.extend(sorted(glob_mod.glob(pattern)))
        return devices

    def read_config(self, name: str) -> Optional[str]:
        """Read a configuration file's content.

        Args:
            name: Configuration name (without .yaml)

        Returns:
            File content or None if not found.
        """
        config_file = self.available_dir / f"{name}.yaml"
        if config_file.exists():
            return config_file.read_text()
        return None

    def add_custom_config(self, name: str, content: str) -> bool:
        """Add a custom connection configuration.

        Args:
            name: Configuration name (will be saved as {name}.yaml)
            content: YAML configuration content

        Returns:
            True if saved successfully.
        """
        self.ensure_structure()

        config_file = self.available_dir / f"{name}.yaml"
        try:
            config_file.write_text(content)
            logger.info(f"Created custom MeshCore config: {config_file}")
            return True
        except OSError as e:
            logger.error(f"Failed to save MeshCore config: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status of MeshCore configuration.

        Returns:
            Dictionary with status information.
        """
        radio_type = self.detect_radio_type()

        return {
            "config_dir": str(self.config_dir),
            "structure_exists": self.config_dir.exists(),
            "radio_type": radio_type.value,
            "available_configs": len(self.list_available()),
            "enabled_configs": len(self.list_enabled()),
            "detected_devices": self.detect_devices(),
            "meshcore_py_installed": _check_meshcore_py(),
        }


def _check_meshcore_py() -> bool:
    """Check if meshcore_py library is installed."""
    try:
        import importlib
        importlib.import_module('meshcore')
        return True
    except ImportError:
        return False


# ─────────────────────────────────────────────────────────────────
# Convenience Functions
# ─────────────────────────────────────────────────────────────────

_default_config: Optional[MeshcoreConfig] = None


def get_meshcore_config() -> MeshcoreConfig:
    """Get or create default MeshCore config manager instance."""
    global _default_config
    if _default_config is None:
        _default_config = MeshcoreConfig()
    return _default_config


def setup_meshcore() -> bool:
    """Quick setup: ensure MeshCore config structure exists.

    Returns:
        True if setup successful.
    """
    config = get_meshcore_config()
    return config.ensure_structure()


def print_status():
    """Print MeshCore configuration status to stdout."""
    config = get_meshcore_config()
    status = config.get_status()

    print("\n=== MeshCore Configuration Status ===\n")
    print(f"Config Directory: {status['config_dir']}")
    print(f"Structure Exists: {status['structure_exists']}")
    print(f"Radio Type: {status['radio_type']}")
    print(f"meshcore_py Installed: {status['meshcore_py_installed']}")
    print(f"Available Configs: {status['available_configs']}")
    print(f"Enabled Configs: {status['enabled_configs']}")
    print(f"Detected Devices: {status['detected_devices']}")
    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print_status()
