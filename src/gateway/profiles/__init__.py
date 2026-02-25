"""
MeshForge Gateway Profiles

Pre-configured profiles for optimizing Meshtastic devices as gateways.
These profiles configure official Meshtastic firmware for gateway operation.

Usage:
    from gateway.profiles import ProfileManager

    # List available profiles
    manager = ProfileManager()
    profiles = manager.list_profiles()

    # Get profile details
    info = manager.get_profile_info('rak4631_gateway')

    # Apply profile to device
    result = manager.apply_profile('heltec_v4_gateway', port='/dev/ttyUSB0')

    # Or via CLI:
    # meshtastic --configure /path/to/profile.yaml
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from utils.safe_import import safe_import

_yaml_mod, YAML_AVAILABLE = safe_import('yaml')
if YAML_AVAILABLE:
    yaml = _yaml_mod

from utils.cli import find_meshtastic_cli

logger = logging.getLogger(__name__)

# Profile directory
PROFILES_DIR = Path(__file__).parent


@dataclass
class ProfileInfo:
    """Information about a gateway profile"""
    name: str
    path: Path
    device: str
    description: str
    tx_power: int
    has_wifi: bool
    has_ethernet: bool
    has_gps: bool
    flash_method: str


# Profile metadata (extracted from YAML comments)
PROFILE_METADATA = {
    'rak4631_gateway': {
        'device': 'RAK4631 (nRF52840)',
        'description': 'Low-power gateway with excellent battery life',
        'tx_power': 22,
        'has_wifi': False,
        'has_ethernet': False,
        'has_gps': True,
        'flash_method': 'uf2',
    },
    'heltec_v3_gateway': {
        'device': 'Heltec V3 (ESP32-S3)',
        'description': 'WiFi-enabled gateway with standard TX power',
        'tx_power': 20,
        'has_wifi': True,
        'has_ethernet': False,
        'has_gps': False,
        'flash_method': 'esptool',
    },
    'heltec_v4_gateway': {
        'device': 'Heltec V4 (ESP32-S3 High Power)',
        'description': 'High-power gateway with 28dBm TX for maximum range',
        'tx_power': 27,
        'has_wifi': True,
        'has_ethernet': False,
        'has_gps': False,
        'flash_method': 'esptool',
    },
    'station_g2_gateway': {
        'device': 'Heltec Station G2',
        'description': 'Ethernet-enabled base station for fixed deployment',
        'tx_power': 22,
        'has_wifi': False,
        'has_ethernet': True,
        'has_gps': False,
        'flash_method': 'esptool',
    },
}


class ProfileManager:
    """
    Manage gateway configuration profiles.

    Profiles are YAML files that can be applied to Meshtastic devices
    using the `meshtastic --configure` command.
    """

    def __init__(self, profiles_dir: Optional[Path] = None):
        self.profiles_dir = profiles_dir or PROFILES_DIR

    def list_profiles(self) -> List[ProfileInfo]:
        """List all available gateway profiles"""
        profiles = []

        for yaml_file in self.profiles_dir.glob('*.yaml'):
            name = yaml_file.stem
            meta = PROFILE_METADATA.get(name, {})

            profiles.append(ProfileInfo(
                name=name,
                path=yaml_file,
                device=meta.get('device', 'Unknown'),
                description=meta.get('description', ''),
                tx_power=meta.get('tx_power', 0),
                has_wifi=meta.get('has_wifi', False),
                has_ethernet=meta.get('has_ethernet', False),
                has_gps=meta.get('has_gps', False),
                flash_method=meta.get('flash_method', 'unknown'),
            ))

        return sorted(profiles, key=lambda p: p.name)

    def get_profile_path(self, profile_name: str) -> Optional[Path]:
        """Get the path to a profile file"""
        # Add .yaml extension if not present
        if not profile_name.endswith('.yaml'):
            profile_name = f"{profile_name}.yaml"

        profile_path = self.profiles_dir / profile_name

        if profile_path.exists():
            return profile_path

        return None

    def get_profile_info(self, profile_name: str) -> Optional[ProfileInfo]:
        """Get detailed information about a profile"""
        path = self.get_profile_path(profile_name)
        if not path:
            return None

        name = path.stem
        meta = PROFILE_METADATA.get(name, {})

        return ProfileInfo(
            name=name,
            path=path,
            device=meta.get('device', 'Unknown'),
            description=meta.get('description', ''),
            tx_power=meta.get('tx_power', 0),
            has_wifi=meta.get('has_wifi', False),
            has_ethernet=meta.get('has_ethernet', False),
            has_gps=meta.get('has_gps', False),
            flash_method=meta.get('flash_method', 'unknown'),
        )

    def load_profile(self, profile_name: str) -> Optional[Dict[str, Any]]:
        """Load and parse a profile YAML file"""
        if not YAML_AVAILABLE:
            logger.error("PyYAML not installed. Install with: pip install pyyaml")
            return None

        path = self.get_profile_path(profile_name)
        if not path:
            logger.error(f"Profile not found: {profile_name}")
            return None

        try:
            with open(path, 'r') as f:
                return yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse profile {profile_name}: {e}")
            return None

    def validate_profile(self, profile_name: str) -> tuple:
        """
        Validate a profile for common issues.

        Returns:
            (is_valid, list_of_warnings)
        """
        warnings = []
        profile = self.load_profile(profile_name)

        if not profile:
            return False, ["Profile could not be loaded"]

        # Check for placeholder values that need to be changed
        network = profile.get('network', {})
        if network.get('wifi_enabled'):
            ssid = network.get('wifi_ssid', '')
            if 'YOUR_WIFI' in ssid or not ssid:
                warnings.append("WiFi SSID needs to be configured")
            psk = network.get('wifi_psk', '')
            if 'YOUR_WIFI' in psk or not psk:
                warnings.append("WiFi password needs to be configured")

        # Check fixed position
        position = profile.get('position', {})
        if position.get('fixed_position'):
            if not position.get('fixed_lat') and not position.get('fixed_lng'):
                warnings.append("Fixed position enabled but coordinates not set")

        # Check region
        lora = profile.get('lora', {})
        region = lora.get('region', 'US')
        if region == 'US':
            warnings.append("LoRa region set to US - verify this is correct for your location")

        return len(warnings) == 0 or all('verify' in w.lower() for w in warnings), warnings

    def apply_profile(
        self,
        profile_name: str,
        port: Optional[str] = None,
        host: Optional[str] = None,
        dry_run: bool = False
    ) -> dict:
        """
        Apply a profile to a connected Meshtastic device.

        Args:
            profile_name: Name of the profile to apply
            port: Serial port (e.g., /dev/ttyUSB0)
            host: TCP host (e.g., localhost for meshtasticd)
            dry_run: If True, show command but don't execute

        Returns:
            dict with 'success', 'message', and 'command' keys
        """
        path = self.get_profile_path(profile_name)
        if not path:
            return {
                'success': False,
                'message': f"Profile not found: {profile_name}",
                'command': None
            }

        # Find meshtastic CLI
        cli_path = find_meshtastic_cli()

        if not cli_path:
            return {
                'success': False,
                'message': "meshtastic CLI not found. Install with: pipx install meshtastic[cli]",
                'command': None
            }

        # Build command
        cmd = [cli_path, '--configure', str(path)]

        if port:
            cmd.extend(['--port', port])
        elif host:
            cmd.extend(['--host', host])

        if dry_run:
            return {
                'success': True,
                'message': f"Dry run - would execute: {' '.join(cmd)}",
                'command': cmd
            }

        # Execute
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                return {
                    'success': True,
                    'message': f"Profile {profile_name} applied successfully",
                    'command': cmd,
                    'stdout': result.stdout
                }
            else:
                return {
                    'success': False,
                    'message': f"Failed to apply profile: {result.stderr}",
                    'command': cmd,
                    'stderr': result.stderr
                }

        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'message': "Command timed out after 60 seconds",
                'command': cmd
            }
        except FileNotFoundError:
            return {
                'success': False,
                'message': "meshtastic CLI not found. Install with: pip install meshtastic",
                'command': cmd
            }
        except (subprocess.SubprocessError, OSError) as e:
            return {
                'success': False,
                'message': f"Error executing command: {e}",
                'command': cmd
            }

    def recommend_profile(self, device_info: dict) -> Optional[str]:
        """
        Recommend a profile based on detected device.

        Args:
            device_info: Dict from HardwareDetector with 'common_devices' or 'name'

        Returns:
            Recommended profile name or None
        """
        devices = device_info.get('common_devices', [])
        name = device_info.get('name', '')

        # Check for specific devices
        device_str = ' '.join(devices).lower() + ' ' + name.lower()

        if 'rak4631' in device_str or 'wisblock' in device_str:
            return 'rak4631_gateway'
        elif 'station g2' in device_str:
            return 'station_g2_gateway'
        elif 'heltec v4' in device_str:
            return 'heltec_v4_gateway'
        elif 'heltec v3' in device_str or 'heltec' in device_str:
            return 'heltec_v3_gateway'
        elif 't-beam' in device_str:
            return 'heltec_v3_gateway'  # Similar ESP32-S3 config

        return None


def get_profile_manager() -> ProfileManager:
    """Get a ProfileManager instance"""
    return ProfileManager()


# Convenience functions
def list_profiles() -> List[ProfileInfo]:
    """List all available gateway profiles"""
    return ProfileManager().list_profiles()


def apply_profile(profile_name: str, port: str = None, host: str = None) -> dict:
    """Apply a profile to a device"""
    return ProfileManager().apply_profile(profile_name, port=port, host=host)
