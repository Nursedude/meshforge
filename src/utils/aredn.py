"""
AREDN Mesh Network Utilities

Provides interfaces for AREDN mesh network nodes, including:
- Node discovery and inventory
- API communication (sysinfo)
- Link quality monitoring
- Service discovery
- MikroTik router configuration helpers

Reference: https://docs.arednmesh.org/en/latest/arednHow-toGuides/devtools.html
"""

import json
import socket
import logging
import threading
import subprocess
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

try:
    import urllib.request
    import urllib.error
except ImportError:
    urllib = None

logger = logging.getLogger(__name__)


class LinkType(Enum):
    """AREDN link types"""
    RF = "RF"           # Radio frequency link
    DTD = "DTD"         # Device-to-device (Ethernet)
    TUN = "TUN"         # Tunnel (internet/VPN)
    UNKNOWN = "Unknown"


@dataclass
class AREDNLink:
    """Represents a link to another AREDN node"""
    ip: str
    hostname: str
    link_type: LinkType
    link_quality: float = 0.0
    neighbor_link_quality: float = 0.0
    signal: int = 0
    noise: int = 0
    snr: int = 0
    tx_rate: int = 0
    interface: str = ""

    def __post_init__(self):
        if self.signal and self.noise:
            self.snr = self.signal - self.noise

    def to_dict(self) -> Dict:
        return {
            'ip': self.ip,
            'hostname': self.hostname,
            'link_type': self.link_type.value,
            'link_quality': self.link_quality,
            'neighbor_link_quality': self.neighbor_link_quality,
            'signal': self.signal,
            'noise': self.noise,
            'snr': self.snr,
            'tx_rate': self.tx_rate,
            'interface': self.interface
        }


@dataclass
class AREDNService:
    """Represents an advertised AREDN service"""
    name: str
    protocol: str
    host: str
    port: int
    url: str = ""

    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'protocol': self.protocol,
            'host': self.host,
            'port': self.port,
            'url': self.url
        }


@dataclass
class AREDNNode:
    """Represents an AREDN mesh node"""
    hostname: str
    ip: str = ""
    firmware_version: str = ""
    model: str = ""
    board_id: str = ""
    description: str = ""
    uptime: str = ""
    loads: List[float] = field(default_factory=list)
    ssid: str = ""
    channel: int = 0
    frequency: str = ""
    channel_width: str = ""
    mesh_status: str = ""
    tunnel_count: int = 0
    links: List[AREDNLink] = field(default_factory=list)
    services: List[AREDNService] = field(default_factory=list)
    api_version: str = ""
    last_update: float = 0.0

    @property
    def base_url(self) -> str:
        if self.ip:
            return f"http://{self.ip}"
        return f"http://{self.hostname}.local.mesh"

    def to_dict(self) -> Dict:
        return {
            'hostname': self.hostname,
            'ip': self.ip,
            'firmware_version': self.firmware_version,
            'model': self.model,
            'board_id': self.board_id,
            'description': self.description,
            'uptime': self.uptime,
            'loads': self.loads,
            'ssid': self.ssid,
            'channel': self.channel,
            'frequency': self.frequency,
            'channel_width': self.channel_width,
            'mesh_status': self.mesh_status,
            'tunnel_count': self.tunnel_count,
            'links': [l.to_dict() for l in self.links],
            'services': [s.to_dict() for s in self.services],
            'api_version': self.api_version
        }


class AREDNClient:
    """
    Client for communicating with AREDN mesh nodes.

    Usage:
        client = AREDNClient("KK6XXX-node")
        info = client.get_sysinfo()
        neighbors = client.get_neighbors()
    """

    DEFAULT_TIMEOUT = 5

    def __init__(self, hostname_or_ip: str, timeout: int = DEFAULT_TIMEOUT):
        """
        Initialize AREDN client.

        Args:
            hostname_or_ip: Node hostname (without .local.mesh) or IP address
            timeout: Request timeout in seconds
        """
        self.hostname = hostname_or_ip
        self.timeout = timeout

        # Determine base URL
        if self._is_ip(hostname_or_ip):
            self.base_url = f"http://{hostname_or_ip}"
            self.ip = hostname_or_ip
        else:
            self.base_url = f"http://{hostname_or_ip}.local.mesh"
            self.ip = None

    def _is_ip(self, value: str) -> bool:
        """Check if value is an IP address"""
        try:
            socket.inet_aton(value)
            return True
        except socket.error:
            return False

    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make HTTP request to node API"""
        if urllib is None:
            logger.error("urllib not available")
            return None

        url = f"{self.base_url}{endpoint}"
        if params:
            param_str = "&".join(f"{k}={v}" for k, v in params.items())
            url += f"?{param_str}"

        logger.debug(f"AREDN request: {url}")

        try:
            req = urllib.request.Request(url, method='GET')
            req.add_header('User-Agent', 'MeshForge/1.0')

            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = response.read().decode('utf-8')
                return json.loads(data)

        except urllib.error.HTTPError as e:
            logger.error(f"AREDN HTTP error: {e.code} {e.reason}")
            return None
        except urllib.error.URLError as e:
            logger.error(f"AREDN URL error: {e.reason}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"AREDN JSON parse error: {e}")
            return None
        except Exception as e:
            logger.error(f"AREDN request error: {e}")
            return None

    def get_sysinfo(self, hosts: bool = False, services: bool = False,
                    services_local: bool = False, link_info: bool = False,
                    lqm: bool = False) -> Optional[Dict]:
        """
        Get node system information via API.

        Args:
            hosts: Include all mesh nodes and devices
            services: Include all mesh services
            services_local: Include local services only
            link_info: Include RF/DTD/TUN link details
            lqm: Include Link Quality Manager data

        Returns:
            Dict with node information or None on error
        """
        params = {}
        if hosts:
            params['hosts'] = '1'
        if services:
            params['services'] = '1'
        if services_local:
            params['services_local'] = '1'
        if link_info:
            params['link_info'] = '1'
        if lqm:
            params['lqm'] = '1'

        return self._make_request('/a/sysinfo', params if params else None)

    def get_node_info(self) -> Optional[AREDNNode]:
        """
        Get parsed node information.

        Returns:
            AREDNNode object or None on error
        """
        data = self.get_sysinfo(link_info=True, services_local=True)
        if not data:
            return None

        node = AREDNNode(hostname=self.hostname)
        if self.ip:
            node.ip = self.ip

        # Parse node details
        if 'node_details' in data:
            details = data['node_details']
            node.firmware_version = details.get('firmware_version', '')
            node.model = details.get('model', '')
            node.board_id = details.get('board_id', '')
            node.description = details.get('description', '')

        # Parse sysinfo
        if 'sysinfo' in data:
            sysinfo = data['sysinfo']
            node.uptime = sysinfo.get('uptime', '')
            node.loads = sysinfo.get('loads', [])

        # Parse mesh RF info
        if 'meshrf' in data:
            meshrf = data['meshrf']
            node.ssid = meshrf.get('ssid', '')
            node.channel = meshrf.get('channel', 0)
            node.frequency = meshrf.get('freq', '')
            node.channel_width = meshrf.get('chanbw', '')
            node.mesh_status = meshrf.get('status', '')

        # Parse tunnels
        if 'tunnels' in data:
            tunnels = data['tunnels']
            node.tunnel_count = tunnels.get('active_tunnel_count', 0)

        # Parse API version
        node.api_version = data.get('api_version', '')

        # Parse link info
        if 'link_info' in data:
            for ip, link_data in data['link_info'].items():
                link_type_str = link_data.get('linkType', 'Unknown')
                try:
                    link_type = LinkType(link_type_str)
                except ValueError:
                    link_type = LinkType.UNKNOWN

                link = AREDNLink(
                    ip=ip,
                    hostname=link_data.get('hostname', ''),
                    link_type=link_type,
                    link_quality=link_data.get('linkQuality', 0.0),
                    neighbor_link_quality=link_data.get('neighborLinkQuality', 0.0),
                    signal=link_data.get('signal', 0),
                    noise=link_data.get('noise', 0),
                    tx_rate=link_data.get('tx_rate', 0),
                    interface=link_data.get('olsrInterface', '')
                )
                node.links.append(link)

        # Parse services
        if 'services_local' in data:
            for svc_data in data['services_local']:
                service = AREDNService(
                    name=svc_data.get('name', ''),
                    protocol=svc_data.get('protocol', ''),
                    host=svc_data.get('host', ''),
                    port=svc_data.get('port', 0),
                    url=svc_data.get('url', '')
                )
                node.services.append(service)

        import time
        node.last_update = time.time()

        return node

    def get_neighbors(self) -> List[AREDNLink]:
        """
        Get list of neighbor nodes with link quality.

        Returns:
            List of AREDNLink objects
        """
        node = self.get_node_info()
        return node.links if node else []

    def check_connectivity(self) -> bool:
        """
        Check if node is reachable.

        Returns:
            True if node responds to API request
        """
        data = self.get_sysinfo()
        return data is not None


class AREDNScanner:
    """
    Scanner for discovering AREDN nodes on the network.

    Usage:
        scanner = AREDNScanner()
        nodes = scanner.scan_subnet("10.0.0.0/24")
    """

    def __init__(self, timeout: int = 2):
        self.timeout = timeout
        self._stop_scan = False

    def scan_subnet(self, subnet: str, callback: Optional[Callable[[AREDNNode], None]] = None,
                    max_threads: int = 20) -> List[AREDNNode]:
        """
        Scan a subnet for AREDN nodes.

        Args:
            subnet: Subnet in CIDR notation (e.g., "10.0.0.0/24")
            callback: Optional callback for each discovered node
            max_threads: Maximum concurrent scan threads

        Returns:
            List of discovered AREDNNode objects
        """
        import ipaddress
        from concurrent.futures import ThreadPoolExecutor, as_completed

        network = ipaddress.ip_network(subnet, strict=False)
        nodes = []
        self._stop_scan = False

        def check_host(ip: str) -> Optional[AREDNNode]:
            if self._stop_scan:
                return None
            try:
                client = AREDNClient(ip, timeout=self.timeout)
                node = client.get_node_info()
                if node:
                    node.ip = ip
                    if callback:
                        callback(node)
                    return node
            except Exception as e:
                logger.debug(f"Scan {ip}: {e}")
            return None

        with ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = {executor.submit(check_host, str(ip)): ip for ip in network.hosts()}

            for future in as_completed(futures):
                if self._stop_scan:
                    break
                result = future.result()
                if result:
                    nodes.append(result)

        return nodes

    def scan_common_ranges(self, callback: Optional[Callable[[AREDNNode], None]] = None) -> List[AREDNNode]:
        """
        Scan common AREDN IP ranges.

        AREDN typically uses 10.x.x.x addresses within the amateur radio allocation.

        Returns:
            List of discovered AREDNNode objects
        """
        # Common AREDN subnets - scan first 254 hosts of each
        ranges = [
            "10.0.0.0/24",
            "10.1.0.0/24",
            "10.10.0.0/24",
            "10.20.0.0/24",
        ]

        all_nodes = []
        for subnet in ranges:
            if self._stop_scan:
                break
            nodes = self.scan_subnet(subnet, callback)
            all_nodes.extend(nodes)

        return all_nodes

    def stop(self):
        """Stop ongoing scan"""
        self._stop_scan = True


class MikroTikAREDN:
    """
    Helper class for MikroTik router AREDN configuration.

    Provides utilities for:
    - Pre-flight checks
    - VLAN configuration
    - Firmware update guidance
    """

    SUPPORTED_MODELS = [
        "hAP ac lite",
        "hAP ac2",
        "hAP ac3",
        "mANTbox 12-2",
        "RBLHG-5HPnD-XL",
    ]

    VLAN_CONFIG = {
        'wan': {'vlan': 1, 'description': 'Gateway to internet/home network'},
        'lan': {'vlan': 0, 'description': 'Local devices (untagged)'},
        'dtd': {'vlan': 2, 'description': 'Device-to-device mesh routing'},
    }

    DEFAULT_PORTS = {
        'hap_ac3': {
            1: 'wan',   # WAN port
            2: 'lan',   # LAN ports
            3: 'lan',
            4: 'lan',
            5: 'dtd',   # DtD port
        }
    }

    @staticmethod
    def check_tftp_server() -> Dict[str, Any]:
        """
        Check if TFTP server is available for firmware installation.

        Returns:
            Dict with status and details
        """
        result = {
            'available': False,
            'method': None,
            'path': None,
            'instructions': []
        }

        # Check for dnsmasq (Linux)
        if Path('/usr/sbin/dnsmasq').exists():
            result['available'] = True
            result['method'] = 'dnsmasq'
            result['instructions'] = [
                "1. Create TFTP directory: sudo mkdir -p /tftpboot",
                "2. Copy firmware files to /tftpboot/",
                "3. Start dnsmasq:",
                "   sudo dnsmasq --no-daemon --interface=eth0 --bind-interfaces",
                "   --dhcp-range=192.168.1.100,192.168.1.200,12h",
                "   --enable-tftp --tftp-root=/tftpboot"
            ]
            return result

        # Check for atftpd
        if Path('/usr/sbin/atftpd').exists() or Path('/usr/sbin/in.tftpd').exists():
            result['available'] = True
            result['method'] = 'atftpd'
            result['path'] = '/srv/tftp'
            return result

        # Provide installation instructions
        result['instructions'] = [
            "Install dnsmasq for TFTP server:",
            "  sudo apt install dnsmasq",
            "",
            "Or on Windows, use Tiny PXE Server:",
            "  https://reboot.pro/files/file/303-tiny-pxe-server/"
        ]

        return result

    @staticmethod
    def get_installation_steps(model: str = "hAP ac3") -> List[str]:
        """
        Get firmware installation steps for a MikroTik device.

        Args:
            model: Device model name

        Returns:
            List of installation step strings
        """
        return [
            f"MikroTik {model} AREDN Firmware Installation",
            "=" * 50,
            "",
            "Prerequisites:",
            "  - Download AREDN firmware (.elf and .bin files)",
            "  - Computer with Ethernet port",
            "  - TFTP server (dnsmasq or Tiny PXE)",
            "",
            "Steps:",
            "",
            "1. Configure computer network:",
            "   - Set static IP: 192.168.1.10",
            "   - Subnet mask: 255.255.255.0",
            "",
            "2. Start TFTP server with firmware files",
            "",
            "3. Connect to MikroTik:",
            "   - Power off the device",
            "   - Connect Ethernet to Port 1 (ETH1)",
            "   - Hold reset button",
            "   - Apply power while holding reset",
            "   - Release after ~5 seconds (LEDs flash)",
            "",
            "4. Device will request .elf file via TFTP",
            "   - Watch TFTP server logs for request",
            "   - Device boots into RAM-only AREDN",
            "",
            "5. Flash permanent firmware:",
            "   - Connect to device (http://localnode.local.mesh)",
            "   - Upload .bin file via web interface",
            "   - Or: scp firmware.bin root@localnode:/tmp/",
            "   - Run: sysupgrade -n /tmp/firmware.bin",
            "",
            "6. Device reboots with AREDN firmware",
            "",
            "Note: Newer hAP ac3 (RouterOS v7) may need custom build.",
            "Check: https://github.com/aredn/aredn/issues/919"
        ]

    @staticmethod
    def validate_vlan_config(config: Dict) -> List[str]:
        """
        Validate VLAN configuration.

        Args:
            config: VLAN configuration dict

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        required_vlans = {1, 2}  # WAN and DtD
        configured_vlans = set()

        for port, settings in config.items():
            if isinstance(settings, dict):
                vlan = settings.get('vlan')
                if vlan is not None:
                    configured_vlans.add(vlan)

        missing = required_vlans - configured_vlans
        if missing:
            errors.append(f"Missing required VLANs: {missing}")

        return errors


# Convenience functions

def discover_aredn_nodes(subnet: str = "10.0.0.0/24", timeout: int = 2) -> List[AREDNNode]:
    """
    Discover AREDN nodes on a subnet.

    Args:
        subnet: Subnet in CIDR notation
        timeout: Request timeout in seconds

    Returns:
        List of discovered AREDNNode objects
    """
    scanner = AREDNScanner(timeout=timeout)
    return scanner.scan_subnet(subnet)


def get_aredn_node(hostname_or_ip: str, timeout: int = 5) -> Optional[AREDNNode]:
    """
    Get information about a specific AREDN node.

    Args:
        hostname_or_ip: Node hostname or IP address
        timeout: Request timeout in seconds

    Returns:
        AREDNNode object or None
    """
    client = AREDNClient(hostname_or_ip, timeout=timeout)
    return client.get_node_info()
