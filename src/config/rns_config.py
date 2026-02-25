"""
RNS Configuration Generator

Generate Reticulum configuration files for various network topologies.
Supports TCPServerInterface, TCPClientInterface, and RNodeInterface.

Usage:
    from config.rns_config import RNSConfigGenerator

    gen = RNSConfigGenerator()
    config = gen.generate_server_config(
        name="HawaiiNet RNS",
        port=4242,
        rnode_port="/dev/ttyACM0",
        frequency=903625000
    )
    gen.write_config(config, "~/.reticulum/config")
"""

import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from utils.paths import ReticulumPaths

logger = logging.getLogger(__name__)


# US frequency slots for 900 MHz band (902-928 MHz)
# Based on Meshtastic channel_num mapping
US_FREQUENCY_SLOTS = {
    0: 903_875_000,   # Default slot
    1: 903_875_000,
    2: 906_125_000,
    3: 908_375_000,
    4: 910_625_000,
    5: 912_875_000,
    6: 915_125_000,
    7: 917_375_000,
    8: 919_625_000,
    9: 921_875_000,
    10: 924_125_000,
    11: 926_375_000,
    # Extended slots
    12: 903_625_000,  # HawaiiNet frequency
    13: 905_875_000,
    14: 907_125_000,
}

# Common bandwidth/SF combinations
MODULATION_PRESETS = {
    "long_fast": {"bandwidth": 250000, "spreadingfactor": 7, "codingrate": 5},
    "long_moderate": {"bandwidth": 125000, "spreadingfactor": 8, "codingrate": 5},
    "long_slow": {"bandwidth": 125000, "spreadingfactor": 11, "codingrate": 8},
    "medium_fast": {"bandwidth": 500000, "spreadingfactor": 7, "codingrate": 5},
    "medium_slow": {"bandwidth": 250000, "spreadingfactor": 10, "codingrate": 5},
    "short_fast": {"bandwidth": 500000, "spreadingfactor": 7, "codingrate": 5},
    "short_turbo": {"bandwidth": 500000, "spreadingfactor": 6, "codingrate": 5},
    # RNS defaults
    "rns_default": {"bandwidth": 250000, "spreadingfactor": 7, "codingrate": 5},
}


@dataclass
class RNodeConfig:
    """RNode interface configuration."""
    name: str = "rnode"
    port: str = "/dev/ttyACM0"
    frequency: int = 903_625_000  # Hz
    txpower: int = 22  # dBm
    bandwidth: int = 250_000  # Hz
    spreadingfactor: int = 7
    codingrate: int = 5
    enabled: bool = True

    def to_config_section(self) -> str:
        """Generate config section for ~/.reticulum/config."""
        return f"""[[{self.name}]]
    type = RNodeInterface
    interface_enabled = {str(self.enabled)}
    port = {self.port}
    frequency = {self.frequency}
    txpower = {self.txpower}
    bandwidth = {self.bandwidth}
    spreadingfactor = {self.spreadingfactor}
    codingrate = {self.codingrate}
    name = {self.name}
    selected_interface_mode = 1
    configured_bitrate = None
"""


@dataclass
class MeshtasticInterfaceConfig:
    """Meshtastic_Interface configuration for RNS over Meshtastic LoRa.

    Requires the Meshtastic_Interface.py plugin from:
    https://github.com/landandair/RNS_Over_Meshtastic

    Plugin must be installed to ~/.reticulum/interfaces/ (or /etc/reticulum/interfaces/).

    Connection types (choose one):
      - port: USB serial (e.g., /dev/ttyUSB0, /dev/ttyACM0)
      - ble_port: Bluetooth LE (e.g., short_1234)
      - tcp_port: TCP to meshtasticd (e.g., 127.0.0.1:4403)

    data_speed presets:
      0 = LONG_FAST (8s delay, default Meshtastic)
      4 = MEDIUM_FAST (4s)
      5 = SHORT_SLOW (3s)
      6 = SHORT_FAST (1s)
      8 = SHORT_TURBO (0.4s, recommended for RNS)
    """
    name: str = "Meshtastic Gateway"
    enabled: bool = True
    mode: str = "gateway"
    # Connection - set exactly one
    port: Optional[str] = None          # USB serial: /dev/ttyUSB0
    ble_port: Optional[str] = None      # Bluetooth LE
    tcp_port: Optional[str] = "127.0.0.1:4403"  # TCP to meshtasticd
    # LoRa settings
    data_speed: int = 8                 # SHORT_TURBO (recommended)
    hop_limit: int = 3                  # Mesh hops 1-7
    bitrate: int = 500                  # Estimated bps

    def to_config_section(self) -> str:
        """Generate config section for ~/.reticulum/config."""
        lines = [f"[[{self.name}]]"]
        lines.append(f"    type = Meshtastic_Interface")
        lines.append(f"    enabled = {'yes' if self.enabled else 'no'}")
        lines.append(f"    mode = {self.mode}")

        # Connection type (only include the one that's set)
        if self.port:
            lines.append(f"    port = {self.port}")
        elif self.ble_port:
            lines.append(f"    ble_port = {self.ble_port}")
        elif self.tcp_port:
            lines.append(f"    tcp_port = {self.tcp_port}")

        lines.append(f"    data_speed = {self.data_speed}")
        lines.append(f"    hop_limit = {self.hop_limit}")
        lines.append(f"    bitrate = {self.bitrate}")
        return '\n'.join(lines) + '\n'


@dataclass
class TCPServerConfig:
    """TCP Server interface configuration."""
    name: str = "TCP Server"
    listen_ip: str = "0.0.0.0"
    listen_port: int = 4242
    enabled: bool = True

    def to_config_section(self) -> str:
        """Generate config section for ~/.reticulum/config."""
        return f"""[[{self.name}]]
    type = TCPServerInterface
    enabled = {str(self.enabled).lower()}
    listen_ip = {self.listen_ip}
    listen_port = {self.listen_port}
    name = {self.name}
    selected_interface_mode = 1
    configured_bitrate = None
"""


@dataclass
class TCPClientConfig:
    """TCP Client interface configuration."""
    name: str = "TCP Client"
    target_host: str = "127.0.0.1"
    target_port: int = 4242
    enabled: bool = True

    def to_config_section(self) -> str:
        """Generate config section for ~/.reticulum/config."""
        return f"""[[{self.name}]]
    type = TCPClientInterface
    enabled = {str(self.enabled).lower()}
    target_host = {self.target_host}
    target_port = {self.target_port}
    name = {self.name}
"""


@dataclass
class RNSConfig:
    """Complete RNS configuration."""
    identity_name: str = "meshforge"
    enable_transport: bool = True
    share_instance: bool = True
    shared_instance_port: int = 37428
    instance_control_port: int = 37429
    panic_on_interface_errors: bool = False
    interfaces: List[Any] = field(default_factory=list)

    def to_config(self) -> str:
        """Generate complete ~/.reticulum/config file."""
        config = f"""# Reticulum Configuration
# Generated by MeshForge
# https://github.com/Nursedude/meshforge

[reticulum]
  enable_transport = {str(self.enable_transport)}
  share_instance = {str(self.share_instance)}
  shared_instance_port = {self.shared_instance_port}
  instance_control_port = {self.instance_control_port}
  panic_on_interface_errors = {str(self.panic_on_interface_errors)}

[logging]
  loglevel = 4

[interfaces]
"""
        for interface in self.interfaces:
            config += "\n" + interface.to_config_section()

        return config


class RNSConfigGenerator:
    """Generate RNS configuration files."""

    def __init__(self):
        self.templates_dir = Path(__file__).parent.parent / "gateway" / "templates" / "rns"

    def get_frequency_for_slot(self, slot: int, region: str = "US") -> int:
        """Get frequency in Hz for a channel slot."""
        if region == "US":
            return US_FREQUENCY_SLOTS.get(slot, US_FREQUENCY_SLOTS[0])
        # Add other regions as needed
        return US_FREQUENCY_SLOTS.get(slot, US_FREQUENCY_SLOTS[0])

    def get_modulation_preset(self, preset_name: str) -> Dict[str, int]:
        """Get modulation parameters for a preset."""
        return MODULATION_PRESETS.get(preset_name, MODULATION_PRESETS["rns_default"])

    def create_meshtastic_interface(
        self,
        name: str = "Meshtastic Gateway",
        port: Optional[str] = None,
        ble_port: Optional[str] = None,
        tcp_port: Optional[str] = "127.0.0.1:4403",
        data_speed: int = 8,
        hop_limit: int = 3,
        mode: str = "gateway"
    ) -> MeshtasticInterfaceConfig:
        """Create Meshtastic_Interface configuration.

        Bridges RNS over Meshtastic LoRa network. Requires the
        Meshtastic_Interface.py plugin installed in ~/.reticulum/interfaces/.

        Args:
            name: Interface display name
            port: USB serial port (e.g., /dev/ttyUSB0)
            ble_port: Bluetooth LE device ID
            tcp_port: TCP address for meshtasticd (default: 127.0.0.1:4403)
            data_speed: LoRa preset (8=SHORT_TURBO recommended)
            hop_limit: Mesh hop limit 1-7
            mode: Interface mode (gateway or client)
        """
        return MeshtasticInterfaceConfig(
            name=name,
            port=port,
            ble_port=ble_port,
            tcp_port=tcp_port,
            data_speed=data_speed,
            hop_limit=hop_limit,
            mode=mode
        )

    def create_rnode_interface(
        self,
        name: str = "rnode",
        port: str = "/dev/ttyACM0",
        frequency: int = 903_625_000,
        txpower: int = 22,
        modulation: str = "rns_default"
    ) -> RNodeConfig:
        """Create RNode interface configuration."""
        mod = self.get_modulation_preset(modulation)
        return RNodeConfig(
            name=name,
            port=port,
            frequency=frequency,
            txpower=txpower,
            bandwidth=mod["bandwidth"],
            spreadingfactor=mod["spreadingfactor"],
            codingrate=mod["codingrate"]
        )

    def create_tcp_server(
        self,
        name: str = "TCP Server",
        listen_ip: str = "0.0.0.0",
        listen_port: int = 4242
    ) -> TCPServerConfig:
        """Create TCP Server interface configuration."""
        return TCPServerConfig(
            name=name,
            listen_ip=listen_ip,
            listen_port=listen_port
        )

    def create_tcp_client(
        self,
        name: str = "TCP Client",
        target_host: str = "127.0.0.1",
        target_port: int = 4242
    ) -> TCPClientConfig:
        """Create TCP Client interface configuration."""
        return TCPClientConfig(
            name=name,
            target_host=target_host,
            target_port=target_port
        )

    def generate_server_config(
        self,
        name: str = "MeshForge Gateway",
        port: int = 4242,
        rnode_port: Optional[str] = None,
        frequency: int = 903_625_000,
        txpower: int = 22,
        modulation: str = "rns_default"
    ) -> RNSConfig:
        """
        Generate RNS server configuration.

        This is for the primary gateway that other nodes connect to.
        Example: HawaiiNet RNS server on RPi-A.
        """
        config = RNSConfig(
            enable_transport=True,
            share_instance=True
        )

        # Add TCP server for network clients
        config.interfaces.append(
            self.create_tcp_server(
                name=name,
                listen_port=port
            )
        )

        # Add RNode if port specified
        if rnode_port:
            config.interfaces.append(
                self.create_rnode_interface(
                    name=f"{name.lower().replace(' ', '_')}_rnode",
                    port=rnode_port,
                    frequency=frequency,
                    txpower=txpower,
                    modulation=modulation
                )
            )

        return config

    def generate_client_config(
        self,
        server_name: str = "MeshForge Gateway",
        server_host: str = "192.168.1.1",
        server_port: int = 4242,
        rnode_port: Optional[str] = None,
        frequency: int = 903_625_000,
        txpower: int = 22,
        modulation: str = "rns_default"
    ) -> RNSConfig:
        """
        Generate RNS client configuration.

        This is for nodes that connect to a gateway server.
        Example: wh6gxzpi3 connecting to HawaiiNet.
        """
        config = RNSConfig(
            enable_transport=False,  # Client doesn't need transport
            share_instance=True
        )

        # Add TCP client to connect to server
        config.interfaces.append(
            self.create_tcp_client(
                name=server_name,
                target_host=server_host,
                target_port=server_port
            )
        )

        # Add RNode if port specified
        if rnode_port:
            config.interfaces.append(
                self.create_rnode_interface(
                    name=f"local_rnode",
                    port=rnode_port,
                    frequency=frequency,
                    txpower=txpower,
                    modulation=modulation
                )
            )

        return config

    def generate_hawaiinet_server(self) -> RNSConfig:
        """
        Generate HawaiiNet server configuration.

        Based on RPi-A (RNSmeshgate) setup from user notes.
        """
        return self.generate_server_config(
            name="HawaiiNet RNS",
            port=4242,
            rnode_port="/dev/ttyACM0",
            frequency=903_625_000,
            txpower=22,
            modulation="long_fast"
        )

    def generate_hawaiinet_client(
        self,
        server_ip: str = "192.168.86.38"
    ) -> RNSConfig:
        """
        Generate HawaiiNet client configuration.

        Based on RPi-B (wh6gxzpi3) setup from user notes.
        """
        return self.generate_client_config(
            server_name="HawaiiNet RNS",
            server_host=server_ip,
            server_port=4242,
            rnode_port="/dev/ttyACM0",
            frequency=903_625_000,
            txpower=22,
            modulation="long_fast"
        )

    def write_config(
        self,
        config: RNSConfig,
        path: Optional[str] = None,
        backup: bool = True
    ) -> Path:
        """
        Write configuration to file.

        Args:
            config: RNSConfig object
            path: Path to write (default: ~/.reticulum/config)
            backup: Create backup of existing config

        Returns:
            Path to written config file
        """
        if path is None:
            config_path = ReticulumPaths.get_config_file()
            config_dir = config_path.parent
        else:
            # Resolve ~ using sudo-safe home directory (MF001)
            path_str = str(path)
            if path_str.startswith('~'):
                from utils.paths import get_real_user_home
                path_str = str(get_real_user_home()) + path_str[1:]
            config_path = Path(path_str)
            config_dir = config_path.parent

        # Create directory if needed
        config_dir.mkdir(parents=True, exist_ok=True)

        # Backup existing config
        if backup and config_path.exists():
            backup_path = config_path.with_suffix(".config.bak")
            import shutil
            shutil.copy(config_path, backup_path)
            logger.info(f"Backed up existing config to {backup_path}")

        # Write new config
        config_path.write_text(config.to_config())
        logger.info(f"Wrote RNS config to {config_path}")

        return config_path

    def get_config_path(self) -> Path:
        """Get default RNS config path."""
        return ReticulumPaths.get_config_file()

    def read_existing_config(self) -> Optional[str]:
        """Read existing RNS configuration if present."""
        config_path = self.get_config_path()
        if config_path.exists():
            return config_path.read_text()
        return None


# Convenience functions for CLI/API use
def generate_server(
    name: str = "MeshForge Gateway",
    port: int = 4242,
    rnode_port: Optional[str] = None,
    frequency: int = 903_625_000
) -> str:
    """Generate server config and return as string."""
    gen = RNSConfigGenerator()
    config = gen.generate_server_config(
        name=name,
        port=port,
        rnode_port=rnode_port,
        frequency=frequency
    )
    return config.to_config()


def generate_client(
    server_host: str,
    server_port: int = 4242,
    rnode_port: Optional[str] = None,
    frequency: int = 903_625_000
) -> str:
    """Generate client config and return as string."""
    gen = RNSConfigGenerator()
    config = gen.generate_client_config(
        server_host=server_host,
        server_port=server_port,
        rnode_port=rnode_port,
        frequency=frequency
    )
    return config.to_config()


def frequency_slot_to_hz(slot: int, region: str = "US") -> int:
    """Convert channel slot number to frequency in Hz."""
    gen = RNSConfigGenerator()
    return gen.get_frequency_for_slot(slot, region)


def hz_to_mhz(frequency: int) -> float:
    """Convert Hz to MHz for display."""
    return frequency / 1_000_000
