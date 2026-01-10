"""
RNS Configuration Utilities

Default configuration templates and config file management for RNS.
Extracted from rns.py for maintainability.
"""

import os
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default RNS config template
RNS_DEFAULT_CONFIG = '''# Reticulum Network Stack Configuration
# Reference: https://reticulum.network/manual/interfaces.html

[reticulum]
# Enable this node to act as a transport node
# and route traffic for other peers
enable_transport = False

# Share the Reticulum instance with locally
# running clients via a local socket
share_instance = Yes

# If running multiple instances, give them
# unique names to avoid conflicts
# instance_name = default

# Panic and forcibly close if a hardware
# interface experiences an unrecoverable error
panic_on_interface_error = No


[logging]
# Valid log levels are 0 through 7:
#   0: Log only critical information
#   1: Log errors and lower log levels
#   2: Log warnings and lower log levels
#   3: Log notices and lower log levels
#   4: Log info and lower (default)
#   5: Verbose logging
#   6: Debug logging
#   7: Extreme logging
loglevel = 4


[interfaces]
# Default AutoInterface for local network discovery
# Uses link-local UDP broadcasts for peer discovery
[[Default Interface]]
    type = AutoInterface
    enabled = Yes


# ===== RNS TESTNET CONNECTIONS =====
# Uncomment to connect to the public Reticulum Testnet

# [[RNS Testnet Dublin]]
#     type = TCPClientInterface
#     enabled = yes
#     target_host = dublin.connect.reticulum.network
#     target_port = 4965

# [[RNS Testnet BetweenTheBorders]]
#     type = TCPClientInterface
#     enabled = yes
#     target_host = reticulum.betweentheborders.com
#     target_port = 4242


# ===== TCP INTERFACES =====
# For hosting your own connectable node

# [[TCP Server Interface]]
#     type = TCPServerInterface
#     enabled = no
#     listen_ip = 0.0.0.0
#     listen_port = 4242

# [[TCP Client Interface]]
#     type = TCPClientInterface
#     enabled = no
#     target_host = example.com
#     target_port = 4242


# ===== RNODE LORA INTERFACE =====
# For LoRa communication using RNode devices

# [[RNode LoRa Interface]]
#     type = RNodeInterface
#     interface_enabled = False
#     port = /dev/ttyUSB0
#     frequency = 867200000
#     bandwidth = 125000
#     txpower = 7
#     spreadingfactor = 8
#     codingrate = 5

# BLE RNode connection (must be paired first):
# [[RNode BLE]]
#     type = RNodeInterface
#     interface_enabled = False
#     port = ble://RNode 3B87


# ===== MESHTASTIC INTERFACE =====
# RNS over Meshtastic LoRa mesh
# Install: Click "Install Interface" in MeshForge RNS panel
# Source: https://github.com/Nursedude/RNS_Over_Meshtastic_Gateway

# [[Meshtastic Interface]]
#     type = Meshtastic_Interface
#     enabled = False
#     mode = gateway
#     # Connection: choose ONE (port, ble_port, or tcp_port)
#     port = /dev/ttyUSB0
#     # ble_port = RNode_1234
#     # tcp_port = 127.0.0.1:4403
#     # Speed: 0=LongFast, 1=LongSlow, 6=ShortFast, 8=Turbo
#     data_speed = 8
#     hop_limit = 3
#     bitrate = 500
'''

# Default NomadNet config template
NOMADNET_DEFAULT_CONFIG = '''# NomadNet Configuration
# Reference: https://github.com/markqvist/NomadNet

[client]
enable_client = yes

# User-defined display name
user_name = MeshForge User

# Preferred propagation node (optional)
# propagation_node = <hash>

# Text UI settings
textui = yes
# graphics_mode = True


[node]
# Enable this device to serve content to the network
enable_node = no

# Announce interval in seconds (0 = only on startup)
announce_interval = 360

# Pages directory for hosting content
# pages_path = ~/.nomadnetwork/pages


[peers]
# Auto-add discovered peers
auto_add_discovered = yes
'''


def get_real_username() -> str:
    """Get the real username even when running as root via sudo."""
    return os.environ.get('SUDO_USER', os.environ.get('USER', 'root'))


def fix_ownership(path: Path, username: str = None) -> bool:
    """Fix file ownership when running as root.

    Args:
        path: Path to fix ownership for
        username: Username to set ownership to (defaults to real user)

    Returns:
        True if successful, False otherwise
    """
    if os.geteuid() != 0:
        return True  # Not root, no fix needed

    username = username or get_real_username()
    if username == 'root':
        return True  # Already root, no change needed

    try:
        subprocess.run(
            ['chown', '-R', f'{username}:{username}', str(path)],
            capture_output=True, timeout=10
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to fix ownership for {path}: {e}")
        return False


def create_default_rns_config(config_file: str) -> tuple[bool, str]:
    """Create a default RNS config file.

    Args:
        config_file: Path to config file to create

    Returns:
        Tuple of (success, message)
    """
    try:
        config_path = Path(config_file)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(RNS_DEFAULT_CONFIG)
        fix_ownership(config_path.parent)

        logger.info(f"Created default RNS config: {config_path}")
        return True, f"Created {config_path}"

    except Exception as e:
        logger.error(f"Failed to create RNS config: {e}")
        return False, str(e)


def create_default_nomadnet_config(config_file: str) -> tuple[bool, str]:
    """Create a default NomadNet config file.

    Args:
        config_file: Path to config file to create

    Returns:
        Tuple of (success, message)
    """
    try:
        config_path = Path(config_file)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(NOMADNET_DEFAULT_CONFIG)
        fix_ownership(config_path.parent)

        logger.info(f"Created default NomadNet config: {config_path}")
        return True, f"Created {config_path}"

    except Exception as e:
        logger.error(f"Failed to create NomadNet config: {e}")
        return False, str(e)


def get_rns_config_path() -> Path:
    """Get the RNS config file path."""
    # Try to get real user home
    try:
        from utils.paths import get_real_user_home
        home = get_real_user_home()
    except ImportError:
        username = get_real_username()
        home = Path(f'/home/{username}') if username != 'root' else Path.home()

    return home / '.reticulum' / 'config'


def get_nomadnet_config_path() -> Path:
    """Get the NomadNet config file path."""
    try:
        from utils.paths import get_real_user_home
        home = get_real_user_home()
    except ImportError:
        username = get_real_username()
        home = Path(f'/home/{username}') if username != 'root' else Path.home()

    return home / '.nomadnetwork' / 'config'
