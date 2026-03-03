"""
MeshForge Meshtasticd Configuration Manager

Manages the /etc/meshtasticd/ directory structure:
  - available.d/  - Available radio configurations (templates)
  - config.d/     - Active/enabled configurations (symlinks)
  - config.yaml   - Main configuration file
  - ssl/          - SSL certificates

Supports both:
  - USB Serial radios (T-Beam, Heltec, RAK USB) → Python CLI
  - Native SPI radios (Meshtoad, RAK HAT) → Native meshtasticd binary

Usage:
    from core.meshtasticd_config import MeshtasticdConfig

    config = MeshtasticdConfig()

    # List available radio configs
    available = config.list_available()

    # Enable a config
    config.enable("meshtoad-spi")

    # Check radio type
    radio_type = config.detect_radio_type()
"""

import os
import shutil
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from utils.cli import find_meshtastic_cli

logger = logging.getLogger(__name__)


from ._meshtasticd_templates import RadioType, RADIO_TEMPLATES  # noqa: E402


@dataclass
class RadioConfig:
    """Configuration for a radio device."""
    name: str
    radio_type: RadioType
    device_path: Optional[str] = None
    chip: Optional[str] = None  # e.g., "sx1262", "sx1276"
    description: str = ""
    enabled: bool = False
    config_file: Optional[str] = None



class MeshtasticdConfig:
    """
    Manages meshtasticd configuration directory structure.

    Directory structure:
        /etc/meshtasticd/
        ├── available.d/     # Available radio configs (36 templates)
        │   ├── heltec-usb.yaml, tbeam-usb.yaml, ...  (9 USB)
        │   └── meshtoad-spi.yaml, rak-hat-spi.yaml, ... (27 SPI)
        ├── config.d/        # Enabled configs (symlinks to available.d)
        │   └── active.yaml -> ../available.d/meshtoad-spi.yaml
        ├── config.yaml      # Main config (merged from config.d)
        └── ssl/             # SSL certificates
    """

    DEFAULT_CONFIG_DIR = Path("/etc/meshtasticd")
    MESHFORGE_CONFIG_DIR = Path("/etc/meshforge")

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize config manager.

        Args:
            config_dir: Override config directory (default: /etc/meshtasticd)
        """
        self.config_dir = config_dir or self.DEFAULT_CONFIG_DIR
        self.available_dir = self.config_dir / "available.d"
        self.config_d_dir = self.config_dir / "config.d"
        self.ssl_dir = self.config_dir / "ssl"
        self.main_config = self.config_dir / "config.yaml"

    def ensure_structure(self) -> bool:
        """
        Ensure the configuration directory structure exists.

        Returns:
            True if structure was created/exists, False on error
        """
        try:
            # Create directories
            self.config_dir.mkdir(parents=True, exist_ok=True)
            self.available_dir.mkdir(exist_ok=True)
            self.config_d_dir.mkdir(exist_ok=True)
            self.ssl_dir.mkdir(mode=0o700, exist_ok=True)

            # Create default templates if available.d is empty
            if not list(self.available_dir.glob("*.yaml")):
                self._create_default_templates()

            # Create main config.yaml if missing
            if not self.main_config.exists():
                self._create_main_config()

            logger.info(f"Config structure ready at {self.config_dir}")
            return True

        except PermissionError as e:
            logger.error(f"Permission denied creating config structure: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to create config structure: {e}")
            return False

    def _create_default_templates(self):
        """Create default radio configuration templates.

        Prefers repo templates from templates/available.d/ (richer content
        with comments) over the inline RADIO_TEMPLATES dict.
        """
        # Try repo templates first (ship with MeshForge, have full comments)
        repo_templates = Path(__file__).parent.parent.parent / 'templates' / 'available.d'
        if repo_templates.exists():
            deployed = 0
            for tmpl in repo_templates.glob('*.yaml'):
                dest = self.available_dir / tmpl.name
                if not dest.exists():
                    shutil.copy2(tmpl, dest)
                    deployed += 1
                    logger.debug(f"Deployed repo template: {dest}")
            if deployed:
                logger.info(f"Deployed {deployed} templates from {repo_templates}")
            return

        # Fallback: generate from built-in RADIO_TEMPLATES dict
        for name, template in RADIO_TEMPLATES.items():
            config_file = self.available_dir / f"{name}.yaml"
            if not config_file.exists():
                config_file.write_text(template["config"])
                logger.debug(f"Created template: {config_file}")

    def _create_main_config(self):
        """Create main config.yaml file ONLY if it does not already exist.

        SAFETY: NEVER overwrites an existing config.yaml. Users hand-edit
        this file (e.g., MaxNodes: 400). MeshForge runtime changes go to
        config.d/meshforge-overrides.yaml instead.

        Prefers repo template from templates/config.yaml over inline fallback.
        """
        # Defense-in-depth: NEVER overwrite user's config.yaml
        if self.main_config.exists():
            logger.debug("config.yaml already exists, preserving: %s", self.main_config)
            return

        # Try repo template first
        repo_config = Path(__file__).parent.parent.parent / 'templates' / 'config.yaml'
        if repo_config.exists():
            shutil.copy2(repo_config, self.main_config)
            logger.info(f"Deployed config.yaml from {repo_config}")
            return

        # Fallback: generate inline (matches upstream config-dist.yaml)
        config_content = """\
### Many device configs have been moved to /etc/meshtasticd/available.d
### To activate, simply copy or link the appropriate file into /etc/meshtasticd/config.d

### Define your devices here using Broadcom pin numbering
### Module is set by hardware templates in config.d/
### DO NOT set Module: auto — it triggers meshtasticd autoconf crashes
### Select your radio via TUI or: sudo cp available.d/<radio>.yaml config.d/
---
Lora:
#  Module: sx1262

GPS:
#  SerialPath: /dev/ttyS0

I2C:
#  I2CDevice: /dev/i2c-1

Display:

Touchscreen:

Input:

Logging:
  LogLevel: info

Webserver:
#  Port: 9443
#  RootPath: /usr/share/meshtasticd/web

HostMetrics:
#  ReportInterval: 30

Config:
#  DisplayMode: TWOCOLOR

General:
  MaxNodes: 200
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
#  MACAddress: AA:BB:CC:DD:EE:FF
#  MACAddressSource: eth0
"""
        self.main_config.write_text(config_content)
        logger.info(f"Created main config: {self.main_config}")

    def list_available(self) -> List[RadioConfig]:
        """
        List available radio configurations.

        Returns:
            List of RadioConfig objects
        """
        configs = []

        if not self.available_dir.exists():
            return configs

        for config_file in sorted(self.available_dir.glob("*.yaml")):
            name = config_file.stem

            # Check if enabled (symlink exists in config.d)
            enabled = (self.config_d_dir / config_file.name).exists()

            # Get template info if available
            template = RADIO_TEMPLATES.get(name, {})
            radio_type = template.get("radio_type", RadioType.UNKNOWN)
            description = template.get("description", f"Radio config: {name}")
            chip = template.get("chip")

            configs.append(RadioConfig(
                name=name,
                radio_type=radio_type,
                chip=chip,
                description=description,
                enabled=enabled,
                config_file=str(config_file),
            ))

        return configs

    def list_enabled(self) -> List[RadioConfig]:
        """List enabled (active) configurations."""
        return [c for c in self.list_available() if c.enabled]

    def enable(self, config_name: str) -> bool:
        """
        Enable a radio configuration.

        Creates a symlink in config.d/ pointing to available.d/

        Args:
            config_name: Name of config (without .yaml extension)

        Returns:
            True if enabled successfully
        """
        source = self.available_dir / f"{config_name}.yaml"
        target = self.config_d_dir / f"{config_name}.yaml"

        if not source.exists():
            logger.error(f"Config not found: {source}")
            return False

        try:
            # Remove existing symlink if present
            if target.exists() or target.is_symlink():
                target.unlink()

            # Create relative symlink
            target.symlink_to(f"../available.d/{config_name}.yaml")
            logger.info(f"Enabled config: {config_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to enable {config_name}: {e}")
            return False

    def disable(self, config_name: str) -> bool:
        """
        Disable a radio configuration.

        Removes symlink from config.d/

        Args:
            config_name: Name of config (without .yaml extension)

        Returns:
            True if disabled successfully
        """
        target = self.config_d_dir / f"{config_name}.yaml"

        try:
            if target.exists() or target.is_symlink():
                target.unlink()
                logger.info(f"Disabled config: {config_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to disable {config_name}: {e}")
            return False

    def detect_radio_type(self) -> RadioType:
        """
        Auto-detect the type of radio connected.

        Checks:
        1. USB serial devices (/dev/ttyUSB*, /dev/ttyACM*)
        2. SPI devices (via CH341 or native GPIO)

        Returns:
            RadioType enum value
        """
        # Check for USB serial devices
        usb_devices = list(Path("/dev").glob("ttyUSB*")) + \
                      list(Path("/dev").glob("ttyACM*"))

        if usb_devices:
            # Check if this is a CH341 (USB-to-SPI for Meshtoad)
            for dev in usb_devices:
                if self._is_ch341_spi(dev):
                    logger.info(f"Detected CH341 SPI adapter: {dev}")
                    return RadioType.NATIVE_SPI

            # Regular USB serial
            logger.info(f"Detected USB serial radio: {usb_devices[0]}")
            return RadioType.USB_SERIAL

        # Check for native SPI (Raspberry Pi GPIO)
        if self._has_native_spi():
            logger.info("Detected native SPI interface")
            return RadioType.NATIVE_SPI

        return RadioType.UNKNOWN

    def _is_ch341_spi(self, device: Path) -> bool:
        """Check if a USB device is a CH341 USB-to-SPI adapter."""
        try:
            # Check dmesg for CH341 mention
            result = subprocess.run(
                ["dmesg"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "ch341" in result.stdout.lower():
                # Check if it's in SPI mode vs serial
                # CH341 in SPI mode shows up differently
                if "spi" in result.stdout.lower():
                    return True
        except Exception:
            pass
        return False

    def _has_native_spi(self) -> bool:
        """Check if native SPI is available (Raspberry Pi)."""
        spi_devices = list(Path("/dev").glob("spidev*"))
        return len(spi_devices) > 0

    def get_daemon_type(self) -> str:
        """
        Determine which daemon type to use.

        Returns:
            "native" for meshtasticd binary
            "python" for meshtastic Python CLI
        """
        radio_type = self.detect_radio_type()

        if radio_type == RadioType.NATIVE_SPI:
            return "native"
        elif radio_type == RadioType.USB_SERIAL:
            return "python"
        else:
            # Default to Python for unknown
            return "python"

    def is_native_installed(self) -> bool:
        """Check if native meshtasticd binary is installed."""
        return shutil.which("meshtasticd") is not None

    def is_python_cli_installed(self) -> bool:
        """Check if Python meshtastic CLI is installed."""
        return find_meshtastic_cli() is not None

    def get_native_deb_url(self, arch: str = "arm64") -> str:
        """
        Get download URL for native meshtasticd .deb package.

        Args:
            arch: Architecture (arm64, armhf, amd64)

        Returns:
            GitHub release URL
        """
        # Latest stable release
        version = "2.5.19.f77f1d6"
        base_url = "https://github.com/meshtastic/firmware/releases/download"
        return f"{base_url}/v{version}/meshtasticd_{version}_{arch}.deb"

    def add_custom_config(self, name: str, content: str) -> bool:
        """
        Add a custom radio configuration.

        Args:
            name: Configuration name (will be saved as {name}.yaml)
            content: YAML configuration content

        Returns:
            True if saved successfully
        """
        self.ensure_structure()

        config_file = self.available_dir / f"{name}.yaml"
        try:
            config_file.write_text(content)
            logger.info(f"Created custom config: {config_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def read_config(self, name: str) -> Optional[str]:
        """
        Read a configuration file's content.

        Args:
            name: Configuration name (without .yaml)

        Returns:
            File content or None if not found
        """
        config_file = self.available_dir / f"{name}.yaml"
        if config_file.exists():
            return config_file.read_text()
        return None

    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status of meshtasticd configuration.

        Returns:
            Dictionary with status information
        """
        radio_type = self.detect_radio_type()
        daemon_type = self.get_daemon_type()

        return {
            "config_dir": str(self.config_dir),
            "structure_exists": self.config_dir.exists(),
            "radio_type": radio_type.value,
            "daemon_type": daemon_type,
            "native_installed": self.is_native_installed(),
            "python_cli_installed": self.is_python_cli_installed(),
            "available_configs": len(self.list_available()),
            "enabled_configs": len(self.list_enabled()),
            "usb_devices": [str(d) for d in Path("/dev").glob("ttyUSB*")],
            "acm_devices": [str(d) for d in Path("/dev").glob("ttyACM*")],
            "spi_devices": [str(d) for d in Path("/dev").glob("spidev*")],
        }


# ─────────────────────────────────────────────────────────────────
# Convenience Functions
# ─────────────────────────────────────────────────────────────────

_default_config: Optional[MeshtasticdConfig] = None


def get_config() -> MeshtasticdConfig:
    """Get or create default config manager instance."""
    global _default_config
    if _default_config is None:
        _default_config = MeshtasticdConfig()
    return _default_config


def setup_meshtasticd() -> bool:
    """
    Quick setup: ensure config structure exists.

    Creates /etc/meshtasticd/{available.d,config.d,ssl} and deploys
    templates to available.d/ if missing. Does NOT auto-enable any
    radio config — the user must explicitly select their hardware.

    Returns:
        True if setup successful
    """
    config = get_config()
    return config.ensure_structure()


def print_status():
    """Print configuration status to stdout."""
    config = get_config()
    status = config.get_status()

    print("\n=== Meshtasticd Configuration Status ===\n")
    print(f"Config Directory: {status['config_dir']}")
    print(f"Structure Exists: {status['structure_exists']}")
    print(f"Radio Type: {status['radio_type']}")
    print(f"Daemon Type: {status['daemon_type']}")
    print(f"Native Installed: {status['native_installed']}")
    print(f"Python CLI Installed: {status['python_cli_installed']}")
    print(f"Available Configs: {status['available_configs']}")
    print(f"Enabled Configs: {status['enabled_configs']}")
    print(f"USB Devices: {status['usb_devices']}")
    print(f"ACM Devices: {status['acm_devices']}")
    print(f"SPI Devices: {status['spi_devices']}")
    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print_status()
