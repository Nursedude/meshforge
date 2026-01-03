"""
MeshForge Version Checker

Checks installed versions of:
- meshtasticd (Linux native daemon)
- meshtastic CLI (Python package)
- Node firmware (via connected device)

And compares them against latest available versions.
"""

import re
import subprocess
import json
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache for version checks to avoid hitting APIs too frequently
_version_cache: Dict[str, Any] = {}
_cache_ttl = timedelta(hours=1)


@dataclass
class VersionInfo:
    """Version information for a component"""
    name: str
    installed: Optional[str] = None
    latest: Optional[str] = None
    update_available: bool = False
    install_command: Optional[str] = None
    update_command: Optional[str] = None
    error: Optional[str] = None


def parse_version(version_str: str) -> tuple:
    """Parse version string into comparable tuple"""
    if not version_str:
        return (0, 0, 0)

    # Remove common prefixes
    version_str = version_str.lstrip('v').strip()

    # Handle versions like "2.5.6.abcd123"
    match = re.match(r'(\d+)\.(\d+)\.(\d+)', version_str)
    if match:
        return tuple(int(x) for x in match.groups())

    return (0, 0, 0)


def compare_versions(installed: str, latest: str) -> bool:
    """Check if latest version is newer than installed"""
    if not installed or not latest:
        return False

    inst_tuple = parse_version(installed)
    latest_tuple = parse_version(latest)

    return latest_tuple > inst_tuple


def get_meshtasticd_version() -> Optional[str]:
    """Get installed meshtasticd version"""
    try:
        # Try dpkg first (Debian/Ubuntu)
        result = subprocess.run(
            ['dpkg', '-s', 'meshtasticd'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()

        # Try meshtasticd --version
        result = subprocess.run(
            ['meshtasticd', '--version'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Parse version from output
            match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)

    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"Error getting meshtasticd version: {e}")

    return None


def get_meshtastic_cli_version() -> Optional[str]:
    """Get installed meshtastic CLI version"""
    try:
        # Find meshtastic CLI
        cli_paths = [
            '/root/.local/bin/meshtastic',
            Path.home() / '.local/bin/meshtastic',
        ]

        import os
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            cli_paths.insert(0, Path(f'/home/{sudo_user}/.local/bin/meshtastic'))

        cli_path = None
        for path in cli_paths:
            if Path(path).exists():
                cli_path = str(path)
                break

        if not cli_path:
            # Try which
            result = subprocess.run(['which', 'meshtastic'], capture_output=True, text=True)
            if result.returncode == 0:
                cli_path = result.stdout.strip()

        if not cli_path:
            return None

        # Get version
        result = subprocess.run(
            [cli_path, '--version'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Parse version - format is usually "meshtastic 2.3.4"
            match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)

    except Exception as e:
        logger.debug(f"Error getting meshtastic CLI version: {e}")

    return None


def get_node_firmware_version() -> Optional[str]:
    """Get firmware version from connected node via meshtastic CLI"""
    try:
        import socket

        # Quick check if port is available
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        if sock.connect_ex(('localhost', 4403)) != 0:
            sock.close()
            return None
        sock.close()

        # Find CLI
        cli_paths = [
            '/root/.local/bin/meshtastic',
            str(Path.home() / '.local/bin/meshtastic'),
        ]

        import os
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            cli_paths.insert(0, f'/home/{sudo_user}/.local/bin/meshtastic')

        cli_path = None
        for path in cli_paths:
            if Path(path).exists():
                cli_path = path
                break

        if not cli_path:
            return None

        result = subprocess.run(
            [cli_path, '--host', 'localhost', '--info'],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            # Parse firmware version from JSON
            match = re.search(r'"firmwareVersion":\s*"([^"]+)"', result.stdout)
            if match:
                return match.group(1)

    except Exception as e:
        logger.debug(f"Error getting firmware version: {e}")

    return None


def get_latest_meshtasticd_version() -> Optional[str]:
    """Get latest meshtasticd version from GitHub releases"""
    cache_key = 'meshtasticd_latest'

    # Check cache
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['version']

    try:
        import urllib.request
        import ssl

        # Create SSL context
        ctx = ssl.create_default_context()

        url = 'https://api.github.com/repos/meshtastic/firmware/releases/latest'
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshForge'})

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            data = json.loads(response.read().decode())
            version = data.get('tag_name', '').lstrip('v')

            # Cache result
            _version_cache[cache_key] = {
                'version': version,
                'timestamp': datetime.now()
            }

            return version

    except Exception as e:
        logger.debug(f"Error getting latest meshtasticd version: {e}")

    return None


def get_latest_meshtastic_cli_version() -> Optional[str]:
    """Get latest meshtastic CLI version from PyPI"""
    cache_key = 'meshtastic_cli_latest'

    # Check cache
    if cache_key in _version_cache:
        cached = _version_cache[cache_key]
        if datetime.now() - cached['timestamp'] < _cache_ttl:
            return cached['version']

    try:
        import urllib.request
        import ssl

        ctx = ssl.create_default_context()

        url = 'https://pypi.org/pypi/meshtastic/json'
        req = urllib.request.Request(url, headers={'User-Agent': 'MeshForge'})

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            data = json.loads(response.read().decode())
            version = data.get('info', {}).get('version')

            # Cache result
            _version_cache[cache_key] = {
                'version': version,
                'timestamp': datetime.now()
            }

            return version

    except Exception as e:
        logger.debug(f"Error getting latest CLI version: {e}")

    return None


def get_latest_firmware_version() -> Optional[str]:
    """Get latest Meshtastic firmware version from GitHub"""
    # Same as meshtasticd for now - they share the firmware repo
    return get_latest_meshtasticd_version()


def check_all_versions() -> Dict[str, VersionInfo]:
    """Check all component versions and return status"""
    results = {}

    # meshtasticd
    meshtasticd = VersionInfo(name='meshtasticd')
    meshtasticd.installed = get_meshtasticd_version()
    meshtasticd.latest = get_latest_meshtasticd_version()
    if meshtasticd.installed and meshtasticd.latest:
        meshtasticd.update_available = compare_versions(meshtasticd.installed, meshtasticd.latest)
    meshtasticd.update_command = 'sudo apt update && sudo apt upgrade meshtasticd'
    results['meshtasticd'] = meshtasticd

    # Meshtastic CLI
    cli = VersionInfo(name='Meshtastic CLI')
    cli.installed = get_meshtastic_cli_version()
    cli.latest = get_latest_meshtastic_cli_version()
    if cli.installed and cli.latest:
        cli.update_available = compare_versions(cli.installed, cli.latest)
    cli.update_command = 'pipx upgrade meshtastic'
    cli.install_command = 'pipx install meshtastic'
    results['cli'] = cli

    # Node Firmware
    firmware = VersionInfo(name='Node Firmware')
    firmware.installed = get_node_firmware_version()
    firmware.latest = get_latest_firmware_version()
    if firmware.installed and firmware.latest:
        firmware.update_available = compare_versions(firmware.installed, firmware.latest)
    firmware.update_command = 'Use Meshtastic Web Flasher or meshtastic-flasher'
    results['firmware'] = firmware

    return results


def get_version_summary() -> Dict[str, Any]:
    """Get version summary for API/UI consumption"""
    versions = check_all_versions()

    summary = {
        'components': [],
        'updates_available': 0,
        'checked_at': datetime.now().isoformat(),
    }

    for key, info in versions.items():
        component = {
            'id': key,
            'name': info.name,
            'installed': info.installed or 'Not installed',
            'latest': info.latest or 'Unknown',
            'update_available': info.update_available,
            'update_command': info.update_command,
        }
        summary['components'].append(component)

        if info.update_available:
            summary['updates_available'] += 1

    return summary


if __name__ == '__main__':
    """Test version checker"""
    import pprint

    print("Checking versions...")
    summary = get_version_summary()
    pprint.pprint(summary)
