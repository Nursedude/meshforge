"""
AREDN Hardware Configuration Module

Comprehensive hardware support for AREDN mesh network devices,
focusing on MikroTik routers and other supported platforms.

Features:
- Complete device database with specifications
- Configuration templates for different use cases
- Firmware compatibility checking
- Port mapping and VLAN configuration
- Network simulation for infrastructure testing
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from pathlib import Path
import json
import ipaddress
import logging

logger = logging.getLogger(__name__)


class DeviceType(Enum):
    """AREDN-supported device types"""
    ROUTER = "router"
    SECTOR = "sector"
    DISH = "dish"
    OMNI = "omni"
    PANEL = "panel"


class FrequencyBand(Enum):
    """RF frequency bands for AREDN"""
    BAND_900MHZ = "900MHz"      # 33cm amateur band
    BAND_2GHZ = "2.4GHz"        # 13cm amateur band
    BAND_3GHZ = "3.4GHz"        # 9cm amateur band
    BAND_5GHZ = "5.8GHz"        # 5cm amateur band


class InterfaceType(Enum):
    """Network interface types"""
    ETHERNET = "ethernet"
    WIFI_24 = "wifi_2.4ghz"
    WIFI_5 = "wifi_5ghz"
    SFP = "sfp"


@dataclass
class PortConfig:
    """Configuration for a network port"""
    port_number: int
    name: str
    interface_type: InterfaceType
    vlan: int = 0  # 0 = untagged
    role: str = "lan"  # wan, lan, dtd, mesh
    poe_out: bool = False
    speed: str = "1Gbps"


@dataclass
class DeviceSpec:
    """Hardware specifications for an AREDN device"""
    manufacturer: str
    model: str
    device_type: DeviceType
    frequency_bands: List[FrequencyBand]

    # Hardware specs
    cpu: str = ""
    ram_mb: int = 0
    flash_mb: int = 0

    # Network interfaces
    ethernet_ports: int = 0
    wifi_radios: int = 0
    sfp_ports: int = 0

    # Power
    poe_input: bool = False
    poe_output: bool = False
    power_watts: float = 0.0

    # RF specs
    max_tx_power_dbm: int = 0
    antenna_gain_dbi: float = 0.0
    antenna_type: str = ""
    beamwidth_h: int = 0
    beamwidth_v: int = 0

    # AREDN support
    supported: bool = True
    min_firmware: str = ""
    notes: str = ""

    # Port configurations
    default_ports: List[PortConfig] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'manufacturer': self.manufacturer,
            'model': self.model,
            'device_type': self.device_type.value,
            'frequency_bands': [b.value for b in self.frequency_bands],
            'cpu': self.cpu,
            'ram_mb': self.ram_mb,
            'flash_mb': self.flash_mb,
            'ethernet_ports': self.ethernet_ports,
            'wifi_radios': self.wifi_radios,
            'max_tx_power_dbm': self.max_tx_power_dbm,
            'antenna_gain_dbi': self.antenna_gain_dbi,
            'supported': self.supported
        }


class DeviceDatabase:
    """
    Database of AREDN-compatible devices with specifications.

    Comprehensive catalog of MikroTik and other supported hardware.
    """

    _devices: Dict[str, DeviceSpec] = {}

    @classmethod
    def _init_database(cls):
        """Initialize device database with known hardware"""
        if cls._devices:
            return

        # ============================================
        # MikroTik Routers
        # ============================================

        cls._devices["mikrotik_hap_ac3"] = DeviceSpec(
            manufacturer="MikroTik",
            model="hAP ac3",
            device_type=DeviceType.ROUTER,
            frequency_bands=[FrequencyBand.BAND_2GHZ, FrequencyBand.BAND_5GHZ],
            cpu="IPQ4019 (4x 716MHz)",
            ram_mb=256,
            flash_mb=128,
            ethernet_ports=5,
            wifi_radios=2,
            poe_input=True,
            poe_output=True,  # Port 5 passive PoE out
            power_watts=24,
            max_tx_power_dbm=23,
            min_firmware="3.23.4.0",
            notes="Most popular AREDN router. Dual-band concurrent.",
            default_ports=[
                PortConfig(1, "Internet", InterfaceType.ETHERNET, vlan=1, role="wan"),
                PortConfig(2, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(3, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(4, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(5, "DtD/PoE", InterfaceType.ETHERNET, vlan=2, role="dtd", poe_out=True),
            ]
        )

        cls._devices["mikrotik_hap_ac2"] = DeviceSpec(
            manufacturer="MikroTik",
            model="hAP ac2",
            device_type=DeviceType.ROUTER,
            frequency_bands=[FrequencyBand.BAND_2GHZ, FrequencyBand.BAND_5GHZ],
            cpu="IPQ4018 (4x 716MHz)",
            ram_mb=128,
            flash_mb=16,
            ethernet_ports=5,
            wifi_radios=2,
            poe_input=True,
            power_watts=12,
            max_tx_power_dbm=23,
            min_firmware="3.22.8.0",
            notes="Budget dual-band option. Less RAM than ac3.",
            default_ports=[
                PortConfig(1, "Internet", InterfaceType.ETHERNET, vlan=1, role="wan"),
                PortConfig(2, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(3, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(4, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(5, "DtD", InterfaceType.ETHERNET, vlan=2, role="dtd"),
            ]
        )

        cls._devices["mikrotik_hap_ac_lite"] = DeviceSpec(
            manufacturer="MikroTik",
            model="hAP ac lite",
            device_type=DeviceType.ROUTER,
            frequency_bands=[FrequencyBand.BAND_2GHZ, FrequencyBand.BAND_5GHZ],
            cpu="QCA9531 (650MHz)",
            ram_mb=64,
            flash_mb=16,
            ethernet_ports=5,
            wifi_radios=2,
            poe_input=True,
            power_watts=8,
            max_tx_power_dbm=22,
            min_firmware="3.21.4.0",
            notes="Entry-level dual-band. Limited RAM for large networks.",
            default_ports=[
                PortConfig(1, "Internet", InterfaceType.ETHERNET, vlan=1, role="wan"),
                PortConfig(2, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(3, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(4, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(5, "DtD", InterfaceType.ETHERNET, vlan=2, role="dtd"),
            ]
        )

        # ============================================
        # MikroTik Sector/Directional
        # ============================================

        cls._devices["mikrotik_mantbox_15s"] = DeviceSpec(
            manufacturer="MikroTik",
            model="mANTBox 15s",
            device_type=DeviceType.SECTOR,
            frequency_bands=[FrequencyBand.BAND_5GHZ],
            cpu="QCA9557 (720MHz)",
            ram_mb=64,
            flash_mb=16,
            ethernet_ports=1,
            wifi_radios=1,
            poe_input=True,
            power_watts=9,
            max_tx_power_dbm=31,
            antenna_gain_dbi=15,
            antenna_type="120° sector",
            beamwidth_h=120,
            beamwidth_v=8,
            min_firmware="3.22.1.0",
            notes="Excellent sector antenna for coverage nodes.",
            default_ports=[
                PortConfig(1, "PoE", InterfaceType.ETHERNET, vlan=0, role="lan"),
            ]
        )

        cls._devices["mikrotik_lhg_5"] = DeviceSpec(
            manufacturer="MikroTik",
            model="LHG 5",
            device_type=DeviceType.DISH,
            frequency_bands=[FrequencyBand.BAND_5GHZ],
            cpu="QCA9531 (650MHz)",
            ram_mb=64,
            flash_mb=16,
            ethernet_ports=1,
            wifi_radios=1,
            poe_input=True,
            power_watts=6,
            max_tx_power_dbm=25,
            antenna_gain_dbi=24.5,
            antenna_type="Grid dish",
            beamwidth_h=7,
            beamwidth_v=7,
            min_firmware="3.22.1.0",
            notes="Long-range point-to-point links. Narrow beam.",
            default_ports=[
                PortConfig(1, "PoE", InterfaceType.ETHERNET, vlan=0, role="lan"),
            ]
        )

        cls._devices["mikrotik_sxtsq_5_ac"] = DeviceSpec(
            manufacturer="MikroTik",
            model="SXTsq 5 ac",
            device_type=DeviceType.PANEL,
            frequency_bands=[FrequencyBand.BAND_5GHZ],
            cpu="IPQ4018 (4x 716MHz)",
            ram_mb=256,
            flash_mb=16,
            ethernet_ports=1,
            wifi_radios=1,
            poe_input=True,
            power_watts=8,
            max_tx_power_dbm=25,
            antenna_gain_dbi=16,
            antenna_type="Flat panel",
            beamwidth_h=28,
            beamwidth_v=28,
            min_firmware="3.23.4.0",
            notes="802.11ac wave2 for high-throughput links.",
            default_ports=[
                PortConfig(1, "PoE", InterfaceType.ETHERNET, vlan=0, role="lan"),
            ]
        )

        # ============================================
        # Ubiquiti Devices (common AREDN hardware)
        # ============================================

        cls._devices["ubnt_nanostation_m5"] = DeviceSpec(
            manufacturer="Ubiquiti",
            model="NanoStation M5",
            device_type=DeviceType.PANEL,
            frequency_bands=[FrequencyBand.BAND_5GHZ],
            cpu="Atheros AR7241 (400MHz)",
            ram_mb=32,
            flash_mb=8,
            ethernet_ports=2,
            wifi_radios=1,
            poe_input=True,
            poe_output=True,  # Secondary port
            power_watts=8,
            max_tx_power_dbm=27,
            antenna_gain_dbi=16,
            antenna_type="Dual-pol panel",
            beamwidth_h=43,
            beamwidth_v=41,
            min_firmware="3.19.3.0",
            notes="Legacy device. Limited memory for modern AREDN.",
            default_ports=[
                PortConfig(1, "Main", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(2, "Secondary", InterfaceType.ETHERNET, vlan=0, role="lan", poe_out=True),
            ]
        )

        cls._devices["ubnt_rocket_m5"] = DeviceSpec(
            manufacturer="Ubiquiti",
            model="Rocket M5",
            device_type=DeviceType.SECTOR,
            frequency_bands=[FrequencyBand.BAND_5GHZ],
            cpu="Atheros AR7241 (400MHz)",
            ram_mb=64,
            flash_mb=8,
            ethernet_ports=2,
            wifi_radios=1,
            poe_input=True,
            power_watts=8,
            max_tx_power_dbm=27,
            antenna_gain_dbi=0,  # Requires external antenna
            antenna_type="N-type connector",
            min_firmware="3.19.3.0",
            notes="Requires external sector antenna. High power output.",
            default_ports=[
                PortConfig(1, "Main", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(2, "Secondary", InterfaceType.ETHERNET, vlan=0, role="lan"),
            ]
        )

        cls._devices["ubnt_litebeam_5ac"] = DeviceSpec(
            manufacturer="Ubiquiti",
            model="LiteBeam 5AC Gen2",
            device_type=DeviceType.DISH,
            frequency_bands=[FrequencyBand.BAND_5GHZ],
            cpu="QCA9563 (750MHz)",
            ram_mb=64,
            flash_mb=16,
            ethernet_ports=1,
            wifi_radios=1,
            poe_input=True,
            power_watts=7,
            max_tx_power_dbm=25,
            antenna_gain_dbi=23,
            antenna_type="Integrated dish",
            beamwidth_h=6,
            beamwidth_v=6,
            min_firmware="3.22.6.0",
            notes="802.11ac for long-range point-to-point.",
            default_ports=[
                PortConfig(1, "PoE", InterfaceType.ETHERNET, vlan=0, role="lan"),
            ]
        )

        # ============================================
        # GL.iNet Devices (portable/mobile)
        # ============================================

        cls._devices["glinet_ar750s"] = DeviceSpec(
            manufacturer="GL.iNet",
            model="AR750S (Slate)",
            device_type=DeviceType.ROUTER,
            frequency_bands=[FrequencyBand.BAND_2GHZ, FrequencyBand.BAND_5GHZ],
            cpu="QCA9563 (775MHz)",
            ram_mb=128,
            flash_mb=16,
            ethernet_ports=3,
            wifi_radios=2,
            poe_input=False,
            power_watts=6,
            max_tx_power_dbm=20,
            min_firmware="3.23.4.0",
            notes="Portable travel router. Good for mobile AREDN.",
            default_ports=[
                PortConfig(1, "WAN", InterfaceType.ETHERNET, vlan=1, role="wan"),
                PortConfig(2, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
                PortConfig(3, "LAN", InterfaceType.ETHERNET, vlan=0, role="lan"),
            ]
        )

    @classmethod
    def get_device(cls, device_id: str) -> Optional[DeviceSpec]:
        """Get device specification by ID"""
        cls._init_database()
        return cls._devices.get(device_id)

    @classmethod
    def get_all_devices(cls) -> Dict[str, DeviceSpec]:
        """Get all devices in database"""
        cls._init_database()
        return cls._devices.copy()

    @classmethod
    def get_devices_by_manufacturer(cls, manufacturer: str) -> List[DeviceSpec]:
        """Get all devices from a manufacturer"""
        cls._init_database()
        return [d for d in cls._devices.values()
                if d.manufacturer.lower() == manufacturer.lower()]

    @classmethod
    def get_devices_by_type(cls, device_type: DeviceType) -> List[DeviceSpec]:
        """Get all devices of a specific type"""
        cls._init_database()
        return [d for d in cls._devices.values()
                if d.device_type == device_type]

    @classmethod
    def get_devices_by_band(cls, band: FrequencyBand) -> List[DeviceSpec]:
        """Get all devices supporting a frequency band"""
        cls._init_database()
        return [d for d in cls._devices.values()
                if band in d.frequency_bands]


@dataclass
class MikroTikConfig:
    """
    MikroTik AREDN configuration template.

    Generates configuration for initial setup and advanced options.
    """
    device: DeviceSpec
    hostname: str
    callsign: str = ""
    mesh_ip: str = ""
    channel: int = 0
    channel_width: int = 20
    tx_power: int = 17
    wan_mode: str = "dhcp"  # dhcp, static, disabled
    wan_ip: str = ""
    wan_gateway: str = ""
    lan_dhcp: bool = True
    lan_ip: str = "10.0.0.1"
    lan_netmask: str = "255.255.255.0"
    dtd_enabled: bool = True
    tunnel_enabled: bool = False

    def validate(self) -> List[str]:
        """Validate configuration"""
        errors = []

        # Hostname validation
        if not self.hostname or len(self.hostname) < 3:
            errors.append("Hostname must be at least 3 characters")
        if not self.hostname.replace('-', '').replace('_', '').isalnum():
            errors.append("Hostname contains invalid characters")

        # Callsign validation (amateur radio)
        if self.callsign:
            import re
            if not re.match(r'^[A-Z]{1,2}\d[A-Z]{1,3}$', self.callsign.upper()):
                errors.append("Invalid amateur radio callsign format")

        # Channel validation
        valid_5ghz_channels = [
            36, 40, 44, 48,  # UNII-1
            52, 56, 60, 64,  # UNII-2
            100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140,  # UNII-2 Extended
            149, 153, 157, 161, 165, 169, 173, 177,  # UNII-3 / ISM
        ]
        if self.channel and self.channel not in valid_5ghz_channels:
            errors.append(f"Invalid 5GHz channel: {self.channel}")

        # Power validation
        if self.tx_power > self.device.max_tx_power_dbm:
            errors.append(f"TX power {self.tx_power} exceeds device max {self.device.max_tx_power_dbm}")

        # IP validation
        if self.mesh_ip:
            try:
                ip = ipaddress.ip_address(self.mesh_ip)
                # AREDN uses 10.x.x.x space
                if not str(ip).startswith('10.'):
                    errors.append("Mesh IP should be in 10.0.0.0/8 range")
            except ValueError:
                errors.append(f"Invalid mesh IP: {self.mesh_ip}")

        return errors

    def generate_setup_script(self) -> str:
        """Generate UCI setup commands for initial configuration"""
        lines = [
            "#!/bin/sh",
            "# AREDN Configuration Script",
            f"# Generated for: {self.device.model}",
            f"# Hostname: {self.hostname}",
            "",
            "# Set hostname",
            f"uci set system.@system[0].hostname='{self.hostname}'",
            "",
        ]

        # Callsign in description
        if self.callsign:
            lines.extend([
                "# Amateur radio callsign",
                f"uci set aredn.@settings[0].callsign='{self.callsign}'",
                "",
            ])

        # Mesh IP
        if self.mesh_ip:
            lines.extend([
                "# Mesh IP configuration",
                f"uci set network.lan.ipaddr='{self.mesh_ip}'",
                "",
            ])

        # Channel configuration
        if self.channel:
            lines.extend([
                "# RF channel configuration",
                f"uci set wireless.radio0.channel='{self.channel}'",
                f"uci set aredn.@radio[0].channel='{self.channel}'",
                f"uci set aredn.@radio[0].chanbw='{self.channel_width}'",
                "",
            ])

        # TX Power
        lines.extend([
            "# TX power (dBm)",
            f"uci set aredn.@radio[0].txpower='{self.tx_power}'",
            "",
        ])

        # WAN configuration
        lines.append("# WAN configuration")
        if self.wan_mode == "dhcp":
            lines.append("uci set network.wan.proto='dhcp'")
        elif self.wan_mode == "static":
            lines.extend([
                "uci set network.wan.proto='static'",
                f"uci set network.wan.ipaddr='{self.wan_ip}'",
                f"uci set network.wan.gateway='{self.wan_gateway}'",
            ])
        elif self.wan_mode == "disabled":
            lines.append("uci set network.wan.proto='none'")
        lines.append("")

        # DtD configuration
        if self.dtd_enabled:
            lines.extend([
                "# Device-to-Device (DtD) linking",
                "uci set aredn.@settings[0].dtdlink='1'",
                "",
            ])

        # Apply and commit
        lines.extend([
            "# Apply configuration",
            "uci commit",
            "echo 'Configuration applied. Reboot to activate.'",
            "",
        ])

        return "\n".join(lines)

    def generate_port_config(self) -> Dict[int, Dict]:
        """Generate port VLAN configuration"""
        config = {}
        for port in self.device.default_ports:
            config[port.port_number] = {
                'name': port.name,
                'role': port.role,
                'vlan': port.vlan,
                'tagged': port.vlan > 0,
                'poe_out': port.poe_out
            }
        return config


@dataclass
class SimulatedNode:
    """Represents a node in network simulation"""
    node_id: str
    hostname: str
    device: DeviceSpec
    position: Tuple[float, float]  # x, y coordinates
    ip_address: str
    links: List[str] = field(default_factory=list)  # List of connected node IDs
    link_qualities: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'node_id': self.node_id,
            'hostname': self.hostname,
            'model': self.device.model,
            'position': self.position,
            'ip_address': self.ip_address,
            'links': self.links,
            'link_qualities': self.link_qualities
        }


class NetworkSimulator:
    """
    AREDN Network Simulation Engine

    Simulates mesh network topology and RF propagation for
    infrastructure testing and planning.
    """

    def __init__(self):
        self.nodes: Dict[str, SimulatedNode] = {}
        self._next_ip = 2  # Start at 10.0.0.2

    def _get_next_ip(self) -> str:
        """Generate next mesh IP address"""
        ip = f"10.0.0.{self._next_ip}"
        self._next_ip += 1
        return ip

    def add_node(self, hostname: str, device_id: str,
                 position: Tuple[float, float] = (0, 0)) -> SimulatedNode:
        """
        Add a node to the simulation.

        Args:
            hostname: Node hostname
            device_id: Device ID from DeviceDatabase
            position: X, Y coordinates for RF calculation

        Returns:
            SimulatedNode object
        """
        device = DeviceDatabase.get_device(device_id)
        if not device:
            raise ValueError(f"Unknown device: {device_id}")

        node_id = f"node_{len(self.nodes) + 1}"
        node = SimulatedNode(
            node_id=node_id,
            hostname=hostname,
            device=device,
            position=position,
            ip_address=self._get_next_ip()
        )
        self.nodes[node_id] = node
        return node

    def create_link(self, node1_id: str, node2_id: str,
                   quality: float = 100.0) -> bool:
        """
        Create a bidirectional link between nodes.

        Args:
            node1_id: First node ID
            node2_id: Second node ID
            quality: Link quality percentage (0-100)

        Returns:
            True if link created successfully
        """
        if node1_id not in self.nodes or node2_id not in self.nodes:
            return False

        node1 = self.nodes[node1_id]
        node2 = self.nodes[node2_id]

        # Add bidirectional link
        if node2_id not in node1.links:
            node1.links.append(node2_id)
        if node1_id not in node2.links:
            node2.links.append(node1_id)

        # Store quality
        node1.link_qualities[node2_id] = quality
        node2.link_qualities[node1_id] = quality

        return True

    def calculate_link_quality(self, node1_id: str, node2_id: str) -> float:
        """
        Calculate theoretical link quality based on distance and hardware.

        Uses free-space path loss model with device specifications.

        Args:
            node1_id: First node ID
            node2_id: Second node ID

        Returns:
            Link quality percentage (0-100)
        """
        import math

        node1 = self.nodes.get(node1_id)
        node2 = self.nodes.get(node2_id)
        if not node1 or not node2:
            return 0.0

        # Calculate distance
        dx = node2.position[0] - node1.position[0]
        dy = node2.position[1] - node1.position[1]
        distance_m = math.sqrt(dx * dx + dy * dy)

        if distance_m < 1:
            return 100.0  # Same location

        # Free-space path loss at 5.8 GHz
        # FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
        # At 5.8 GHz: FSPL ≈ 20*log10(d_km) + 107.5 dB
        distance_km = distance_m / 1000
        fspl = 20 * math.log10(distance_km) + 107.5 if distance_km > 0 else 0

        # Link budget calculation
        tx_power = node1.device.max_tx_power_dbm
        tx_gain = node1.device.antenna_gain_dbi
        rx_gain = node2.device.antenna_gain_dbi

        # Received signal strength
        rx_signal = tx_power + tx_gain + rx_gain - fspl

        # Sensitivity threshold (typical for AREDN: -90 dBm)
        sensitivity = -90
        fade_margin = 10  # dB

        # Quality based on signal margin
        margin = rx_signal - sensitivity - fade_margin

        if margin > 20:
            quality = 100.0
        elif margin > 0:
            quality = 50 + (margin * 2.5)  # Linear scaling
        elif margin > -20:
            quality = max(0, 50 + (margin * 2.5))
        else:
            quality = 0.0

        return min(100.0, max(0.0, quality))

    def auto_create_links(self, max_distance_m: float = 10000,
                         min_quality: float = 50.0) -> int:
        """
        Automatically create links between nodes within range.

        Args:
            max_distance_m: Maximum link distance in meters
            min_quality: Minimum link quality to create link

        Returns:
            Number of links created
        """
        import math

        links_created = 0
        node_ids = list(self.nodes.keys())

        for i, node1_id in enumerate(node_ids):
            for node2_id in node_ids[i+1:]:
                node1 = self.nodes[node1_id]
                node2 = self.nodes[node2_id]

                # Calculate distance
                dx = node2.position[0] - node1.position[0]
                dy = node2.position[1] - node1.position[1]
                distance = math.sqrt(dx * dx + dy * dy)

                if distance <= max_distance_m:
                    quality = self.calculate_link_quality(node1_id, node2_id)
                    if quality >= min_quality:
                        self.create_link(node1_id, node2_id, quality)
                        links_created += 1

        return links_created

    def find_path(self, start_id: str, end_id: str) -> Optional[List[str]]:
        """
        Find shortest path between two nodes using BFS.

        Args:
            start_id: Starting node ID
            end_id: Destination node ID

        Returns:
            List of node IDs in path, or None if no path exists
        """
        if start_id not in self.nodes or end_id not in self.nodes:
            return None

        from collections import deque

        visited = {start_id}
        queue = deque([(start_id, [start_id])])

        while queue:
            current, path = queue.popleft()

            if current == end_id:
                return path

            node = self.nodes[current]
            for neighbor_id in node.links:
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, path + [neighbor_id]))

        return None

    def analyze_network(self) -> Dict[str, Any]:
        """
        Analyze network topology and generate report.

        Returns:
            Dict with network analysis results
        """
        analysis = {
            'node_count': len(self.nodes),
            'total_links': 0,
            'average_links_per_node': 0.0,
            'isolated_nodes': [],
            'hub_nodes': [],  # Nodes with > average links
            'weak_links': [],  # Links < 70% quality
            'network_diameter': 0,
            'is_connected': False
        }

        if not self.nodes:
            return analysis

        # Count links and find isolated nodes
        total_links = 0
        link_counts = {}
        for node_id, node in self.nodes.items():
            link_count = len(node.links)
            link_counts[node_id] = link_count
            total_links += link_count

            if link_count == 0:
                analysis['isolated_nodes'].append(node_id)

            # Check for weak links
            for linked_id, quality in node.link_qualities.items():
                if quality < 70:
                    analysis['weak_links'].append({
                        'from': node_id,
                        'to': linked_id,
                        'quality': quality
                    })

        # Deduplicate links (each counted twice)
        analysis['total_links'] = total_links // 2
        analysis['average_links_per_node'] = total_links / len(self.nodes) if self.nodes else 0

        # Find hub nodes (above average)
        avg = analysis['average_links_per_node']
        for node_id, count in link_counts.items():
            if count > avg * 1.5:
                analysis['hub_nodes'].append({
                    'node_id': node_id,
                    'link_count': count
                })

        # Check connectivity (can all nodes reach each other?)
        if self.nodes:
            first_node = list(self.nodes.keys())[0]
            reachable = self._find_reachable(first_node)
            analysis['is_connected'] = len(reachable) == len(self.nodes)

            # Calculate network diameter (longest shortest path)
            if analysis['is_connected']:
                max_path = 0
                for start in self.nodes:
                    for end in self.nodes:
                        if start != end:
                            path = self.find_path(start, end)
                            if path:
                                max_path = max(max_path, len(path) - 1)
                analysis['network_diameter'] = max_path

        return analysis

    def _find_reachable(self, start_id: str) -> set:
        """Find all nodes reachable from start using DFS"""
        visited = set()
        stack = [start_id]

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            node = self.nodes.get(current)
            if node:
                for neighbor in node.links:
                    if neighbor not in visited:
                        stack.append(neighbor)

        return visited

    def export_topology(self) -> Dict:
        """Export network topology as JSON-serializable dict"""
        return {
            'nodes': {k: v.to_dict() for k, v in self.nodes.items()},
            'analysis': self.analyze_network()
        }

    def generate_graphviz(self) -> str:
        """Generate Graphviz DOT format for visualization"""
        lines = [
            "graph AREDNMesh {",
            "  rankdir=LR;",
            "  node [shape=box, style=filled];",
            ""
        ]

        # Add nodes
        for node_id, node in self.nodes.items():
            color = "#90EE90"  # Light green for routers
            if node.device.device_type == DeviceType.SECTOR:
                color = "#87CEEB"  # Light blue for sectors
            elif node.device.device_type == DeviceType.DISH:
                color = "#DDA0DD"  # Plum for dishes

            lines.append(f'  {node_id} [label="{node.hostname}\\n{node.device.model}", fillcolor="{color}"];')

        lines.append("")

        # Add edges (avoid duplicates)
        added_edges = set()
        for node_id, node in self.nodes.items():
            for linked_id in node.links:
                edge = tuple(sorted([node_id, linked_id]))
                if edge not in added_edges:
                    added_edges.add(edge)
                    quality = node.link_qualities.get(linked_id, 100)

                    # Color based on quality
                    if quality >= 80:
                        color = "green"
                    elif quality >= 50:
                        color = "orange"
                    else:
                        color = "red"

                    lines.append(f'  {node_id} -- {linked_id} [color={color}, label="{quality:.0f}%"];')

        lines.append("}")
        return "\n".join(lines)


def create_sample_network() -> NetworkSimulator:
    """
    Create a sample AREDN network for testing/demonstration.

    Returns:
        NetworkSimulator with pre-configured nodes
    """
    sim = NetworkSimulator()

    # Create hub node
    hub = sim.add_node("KK6ABC-TOWER", "mikrotik_hap_ac3", (0, 0))

    # Create sector coverage nodes
    sector1 = sim.add_node("KK6ABC-SECTOR-N", "mikrotik_mantbox_15s", (100, 500))
    sector2 = sim.add_node("KK6ABC-SECTOR-S", "mikrotik_mantbox_15s", (100, -500))

    # Create remote sites
    remote1 = sim.add_node("KK6XYZ-HOME", "mikrotik_hap_ac2", (2000, 300))
    remote2 = sim.add_node("KK6DEF-SITE", "mikrotik_sxtsq_5_ac", (3000, -200))
    remote3 = sim.add_node("KK6GHI-MOBILE", "glinet_ar750s", (1500, 1000))

    # Create long-haul link
    backhaul = sim.add_node("KK6ABC-BACKHAUL", "mikrotik_lhg_5", (0, 100))
    far_site = sim.add_node("W6FAR-HILLTOP", "mikrotik_lhg_5", (8000, 2000))

    # Create links
    sim.create_link(hub.node_id, sector1.node_id, 100)
    sim.create_link(hub.node_id, sector2.node_id, 100)
    sim.create_link(hub.node_id, backhaul.node_id, 100)

    sim.create_link(sector1.node_id, remote1.node_id, 85)
    sim.create_link(sector1.node_id, remote3.node_id, 72)
    sim.create_link(sector2.node_id, remote2.node_id, 78)

    sim.create_link(backhaul.node_id, far_site.node_id, 65)

    return sim
