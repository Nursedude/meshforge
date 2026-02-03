"""
TCP/IP Connection Monitor for MeshForge

Provides Wireshark-like visibility into TCP connections for Meshtastic networks.
Monitors connections to meshtasticd, web clients, and network devices.

Features:
- Real-time TCP connection tracking
- Socket state monitoring (ESTABLISHED, CLOSE_WAIT, TIME_WAIT, etc.)
- Connection latency measurement (RTT)
- Network device discovery (port scanning)
- Integration with Prometheus metrics

Usage:
    from monitoring.tcp_monitor import TCPMonitor, NetworkScanner

    # Monitor connections
    monitor = TCPMonitor()
    monitor.on_connection_change = lambda conn: print(f"Connection: {conn}")
    monitor.start()

    # Discover devices
    scanner = NetworkScanner()
    devices = scanner.scan_for_meshtasticd("192.168.1.0/24")
"""

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple
import ipaddress
import struct
import os

logger = logging.getLogger(__name__)

# Try to import psutil for cross-platform socket monitoring
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.warning("psutil not available - falling back to /proc/net/tcp parsing")


class TCPState(Enum):
    """TCP connection states (from Linux kernel)"""
    ESTABLISHED = "ESTABLISHED"
    SYN_SENT = "SYN_SENT"
    SYN_RECV = "SYN_RECV"
    FIN_WAIT1 = "FIN_WAIT1"
    FIN_WAIT2 = "FIN_WAIT2"
    TIME_WAIT = "TIME_WAIT"
    CLOSE = "CLOSE"
    CLOSE_WAIT = "CLOSE_WAIT"
    LAST_ACK = "LAST_ACK"
    LISTEN = "LISTEN"
    CLOSING = "CLOSING"
    UNKNOWN = "UNKNOWN"


# Map Linux kernel TCP state numbers to enum
TCP_STATE_MAP = {
    1: TCPState.ESTABLISHED,
    2: TCPState.SYN_SENT,
    3: TCPState.SYN_RECV,
    4: TCPState.FIN_WAIT1,
    5: TCPState.FIN_WAIT2,
    6: TCPState.TIME_WAIT,
    7: TCPState.CLOSE,
    8: TCPState.CLOSE_WAIT,
    9: TCPState.LAST_ACK,
    10: TCPState.LISTEN,
    11: TCPState.CLOSING,
}


@dataclass
class TCPConnection:
    """Represents a TCP connection"""
    local_addr: str
    local_port: int
    remote_addr: str
    remote_port: int
    state: TCPState
    pid: Optional[int] = None
    process_name: Optional[str] = None
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    bytes_sent: int = 0
    bytes_recv: int = 0
    rtt_ms: Optional[float] = None

    @property
    def connection_id(self) -> str:
        """Unique identifier for this connection"""
        return f"{self.local_addr}:{self.local_port}->{self.remote_addr}:{self.remote_port}"

    @property
    def is_meshtasticd(self) -> bool:
        """Check if this is a meshtasticd connection (port 4403)"""
        return self.local_port == 4403 or self.remote_port == 4403

    @property
    def is_web_interface(self) -> bool:
        """Check if this is a web interface connection (port 80 or 443)"""
        return self.local_port in (80, 443) or self.remote_port in (80, 443)

    @property
    def duration_seconds(self) -> float:
        """Connection duration in seconds"""
        return (self.last_seen - self.first_seen).total_seconds()

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "connection_id": self.connection_id,
            "local_addr": self.local_addr,
            "local_port": self.local_port,
            "remote_addr": self.remote_addr,
            "remote_port": self.remote_port,
            "state": self.state.value,
            "pid": self.pid,
            "process_name": self.process_name,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "bytes_sent": self.bytes_sent,
            "bytes_recv": self.bytes_recv,
            "rtt_ms": self.rtt_ms,
            "duration_seconds": self.duration_seconds,
            "is_meshtasticd": self.is_meshtasticd,
            "is_web_interface": self.is_web_interface,
        }


@dataclass
class NetworkDevice:
    """Represents a discovered network device"""
    ip_address: str
    hostname: Optional[str] = None
    ports: Dict[int, str] = field(default_factory=dict)  # port -> service name
    response_time_ms: Optional[float] = None
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    is_meshtasticd: bool = False
    is_web_enabled: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "ip_address": self.ip_address,
            "hostname": self.hostname,
            "ports": self.ports,
            "response_time_ms": self.response_time_ms,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "is_meshtasticd": self.is_meshtasticd,
            "is_web_enabled": self.is_web_enabled,
        }


class TCPMonitor:
    """
    TCP Connection Monitor

    Tracks TCP connections related to Meshtastic networking.
    Provides real-time visibility into socket states and connection metrics.

    Example:
        monitor = TCPMonitor()
        monitor.on_connection_added = lambda c: print(f"New: {c.connection_id}")
        monitor.on_connection_removed = lambda c: print(f"Closed: {c.connection_id}")
        monitor.start()

        # Get current connections
        for conn in monitor.get_connections():
            print(f"{conn.remote_addr}:{conn.remote_port} - {conn.state.value}")

        monitor.stop()
    """

    # Default ports to monitor
    MESHTASTIC_PORTS = {4403}  # meshtasticd TCP
    WEB_PORTS = {80, 443, 8080}  # Web interfaces
    ALL_MONITORED_PORTS = MESHTASTIC_PORTS | WEB_PORTS

    def __init__(
        self,
        poll_interval: float = 1.0,
        filter_ports: Optional[Set[int]] = None,
        filter_processes: Optional[Set[str]] = None,
    ):
        """
        Initialize TCP Monitor.

        Args:
            poll_interval: How often to poll for connection changes (seconds)
            filter_ports: Only track connections involving these ports (None = all monitored)
            filter_processes: Only track connections from these process names (None = all)
        """
        self.poll_interval = poll_interval
        self.filter_ports = filter_ports or self.ALL_MONITORED_PORTS
        self.filter_processes = filter_processes

        self._connections: Dict[str, TCPConnection] = {}
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_connection_added: Optional[Callable[[TCPConnection], None]] = None
        self.on_connection_removed: Optional[Callable[[TCPConnection], None]] = None
        self.on_connection_state_change: Optional[Callable[[TCPConnection, TCPState], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

        # Statistics
        self._stats = {
            "total_connections_seen": 0,
            "active_connections": 0,
            "meshtasticd_connections": 0,
            "web_connections": 0,
            "last_poll_time": None,
            "errors": 0,
        }

    def start(self):
        """Start monitoring TCP connections"""
        if self._running:
            logger.warning("TCP monitor already running")
            return

        self._running = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="TCPMonitor"
        )
        self._monitor_thread.start()
        logger.info("TCP monitor started")

    def stop(self):
        """Stop monitoring TCP connections"""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)

        logger.info("TCP monitor stopped")

    def get_connections(self, filter_state: Optional[TCPState] = None) -> List[TCPConnection]:
        """
        Get current TCP connections.

        Args:
            filter_state: Only return connections in this state

        Returns:
            List of TCPConnection objects
        """
        with self._lock:
            connections = list(self._connections.values())

        if filter_state:
            connections = [c for c in connections if c.state == filter_state]

        return connections

    def get_meshtasticd_connections(self) -> List[TCPConnection]:
        """Get connections to/from meshtasticd (port 4403)"""
        return [c for c in self.get_connections() if c.is_meshtasticd]

    def get_web_connections(self) -> List[TCPConnection]:
        """Get web interface connections"""
        return [c for c in self.get_connections() if c.is_web_interface]

    def get_connection_by_remote(self, remote_addr: str, remote_port: int) -> Optional[TCPConnection]:
        """Get a specific connection by remote address and port"""
        with self._lock:
            for conn in self._connections.values():
                if conn.remote_addr == remote_addr and conn.remote_port == remote_port:
                    return conn
        return None

    def get_stats(self) -> dict:
        """Get monitoring statistics"""
        with self._lock:
            self._stats["active_connections"] = len(self._connections)
            self._stats["meshtasticd_connections"] = sum(
                1 for c in self._connections.values() if c.is_meshtasticd
            )
            self._stats["web_connections"] = sum(
                1 for c in self._connections.values() if c.is_web_interface
            )
            return dict(self._stats)

    def _monitor_loop(self):
        """Main monitoring loop"""
        while self._running and not self._stop_event.is_set():
            try:
                self._poll_connections()
                self._stats["last_poll_time"] = datetime.now().isoformat()
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Error polling connections: {e}")
                if self.on_error:
                    try:
                        self.on_error(e)
                    except Exception:
                        pass

            self._stop_event.wait(self.poll_interval)

    def _poll_connections(self):
        """Poll for current TCP connections"""
        current_connections = self._get_tcp_connections()
        now = datetime.now()

        with self._lock:
            current_ids = set()

            for conn_data in current_connections:
                conn_id = conn_data["connection_id"]
                current_ids.add(conn_id)

                if conn_id in self._connections:
                    # Update existing connection
                    existing = self._connections[conn_id]
                    old_state = existing.state
                    existing.last_seen = now
                    existing.state = conn_data["state"]

                    if old_state != existing.state and self.on_connection_state_change:
                        try:
                            self.on_connection_state_change(existing, old_state)
                        except Exception:
                            pass
                else:
                    # New connection
                    conn = TCPConnection(
                        local_addr=conn_data["local_addr"],
                        local_port=conn_data["local_port"],
                        remote_addr=conn_data["remote_addr"],
                        remote_port=conn_data["remote_port"],
                        state=conn_data["state"],
                        pid=conn_data.get("pid"),
                        process_name=conn_data.get("process_name"),
                        first_seen=now,
                        last_seen=now,
                    )
                    self._connections[conn_id] = conn
                    self._stats["total_connections_seen"] += 1

                    if self.on_connection_added:
                        try:
                            self.on_connection_added(conn)
                        except Exception:
                            pass

            # Find removed connections
            removed_ids = set(self._connections.keys()) - current_ids
            for conn_id in removed_ids:
                conn = self._connections.pop(conn_id)
                if self.on_connection_removed:
                    try:
                        self.on_connection_removed(conn)
                    except Exception:
                        pass

    def _get_tcp_connections(self) -> List[dict]:
        """Get current TCP connections from the system"""
        if HAS_PSUTIL:
            return self._get_connections_psutil()
        else:
            return self._get_connections_proc()

    def _get_connections_psutil(self) -> List[dict]:
        """Get TCP connections using psutil"""
        connections = []

        for conn in psutil.net_connections(kind='tcp'):
            # Filter by port
            lport = conn.laddr.port if conn.laddr else 0
            rport = conn.raddr.port if conn.raddr else 0

            if self.filter_ports:
                if lport not in self.filter_ports and rport not in self.filter_ports:
                    continue

            # Get process info if available
            process_name = None
            if conn.pid:
                try:
                    proc = psutil.Process(conn.pid)
                    process_name = proc.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Filter by process name
            if self.filter_processes and process_name not in self.filter_processes:
                continue

            # Map state
            state = TCPState.UNKNOWN
            if conn.status:
                state_name = conn.status.upper().replace("-", "_")
                try:
                    state = TCPState[state_name]
                except KeyError:
                    state = TCPState.UNKNOWN

            local_addr = conn.laddr.ip if conn.laddr else "0.0.0.0"
            remote_addr = conn.raddr.ip if conn.raddr else "0.0.0.0"

            conn_id = f"{local_addr}:{lport}->{remote_addr}:{rport}"

            connections.append({
                "connection_id": conn_id,
                "local_addr": local_addr,
                "local_port": lport,
                "remote_addr": remote_addr,
                "remote_port": rport,
                "state": state,
                "pid": conn.pid,
                "process_name": process_name,
            })

        return connections

    def _get_connections_proc(self) -> List[dict]:
        """Get TCP connections by parsing /proc/net/tcp (Linux only)"""
        connections = []

        for proc_file in ["/proc/net/tcp", "/proc/net/tcp6"]:
            if not os.path.exists(proc_file):
                continue

            try:
                with open(proc_file, "r") as f:
                    lines = f.readlines()[1:]  # Skip header

                for line in lines:
                    parts = line.split()
                    if len(parts) < 10:
                        continue

                    # Parse local address
                    local = parts[1]
                    local_addr, local_port = self._parse_proc_addr(local)

                    # Parse remote address
                    remote = parts[2]
                    remote_addr, remote_port = self._parse_proc_addr(remote)

                    # Filter by port
                    if self.filter_ports:
                        if local_port not in self.filter_ports and remote_port not in self.filter_ports:
                            continue

                    # Parse state
                    state_num = int(parts[3], 16)
                    state = TCP_STATE_MAP.get(state_num, TCPState.UNKNOWN)

                    # Get uid for potential process lookup
                    # uid = int(parts[7])

                    conn_id = f"{local_addr}:{local_port}->{remote_addr}:{remote_port}"

                    connections.append({
                        "connection_id": conn_id,
                        "local_addr": local_addr,
                        "local_port": local_port,
                        "remote_addr": remote_addr,
                        "remote_port": remote_port,
                        "state": state,
                        "pid": None,
                        "process_name": None,
                    })

            except Exception as e:
                logger.debug(f"Error reading {proc_file}: {e}")

        return connections

    def _parse_proc_addr(self, addr_str: str) -> Tuple[str, int]:
        """Parse address from /proc/net/tcp format (hex IP:port)"""
        try:
            addr_hex, port_hex = addr_str.split(":")
            port = int(port_hex, 16)

            # Parse IPv4 address (stored in little-endian)
            if len(addr_hex) == 8:  # IPv4
                addr_int = int(addr_hex, 16)
                addr = socket.inet_ntoa(struct.pack("<I", addr_int))
            else:  # IPv6
                # Convert hex to bytes in correct order
                addr_bytes = bytes.fromhex(addr_hex)
                # Reorder for proper IPv6 representation
                addr = socket.inet_ntop(socket.AF_INET6, addr_bytes)

            return addr, port
        except Exception:
            return "0.0.0.0", 0


class NetworkScanner:
    """
    Network device scanner for discovering Meshtastic devices.

    Scans network ranges to find meshtasticd instances and web interfaces.

    Example:
        scanner = NetworkScanner()

        # Scan a subnet
        devices = scanner.scan_subnet("192.168.1.0/24")

        # Scan specific IPs
        devices = scanner.scan_hosts(["192.168.1.100", "192.168.1.101"])
    """

    # Default ports to scan
    DEFAULT_PORTS = {
        4403: "meshtasticd",
        80: "http",
        443: "https",
        8080: "http-alt",
    }

    def __init__(
        self,
        timeout: float = 1.0,
        max_threads: int = 50,
        ports: Optional[Dict[int, str]] = None,
    ):
        """
        Initialize network scanner.

        Args:
            timeout: Connection timeout in seconds
            max_threads: Maximum concurrent scan threads
            ports: Ports to scan (dict of port -> service name)
        """
        self.timeout = timeout
        self.max_threads = max_threads
        self.ports = ports or self.DEFAULT_PORTS

        self._devices: Dict[str, NetworkDevice] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # Callbacks
        self.on_device_found: Optional[Callable[[NetworkDevice], None]] = None
        self.on_scan_complete: Optional[Callable[[List[NetworkDevice]], None]] = None
        self.on_progress: Optional[Callable[[int, int], None]] = None  # (current, total)

    def scan_host(self, ip_address: str) -> Optional[NetworkDevice]:
        """
        Scan a single host for open ports.

        Args:
            ip_address: IP address to scan

        Returns:
            NetworkDevice if any ports are open, None otherwise
        """
        open_ports = {}
        min_response_time = None

        for port, service in self.ports.items():
            is_open, response_time = self._check_port(ip_address, port)
            if is_open:
                open_ports[port] = service
                if min_response_time is None or response_time < min_response_time:
                    min_response_time = response_time

        if not open_ports:
            return None

        # Try to resolve hostname
        hostname = None
        try:
            hostname = socket.gethostbyaddr(ip_address)[0]
        except socket.herror:
            pass

        device = NetworkDevice(
            ip_address=ip_address,
            hostname=hostname,
            ports=open_ports,
            response_time_ms=min_response_time,
            is_meshtasticd=4403 in open_ports,
            is_web_enabled=bool(open_ports.keys() & {80, 443, 8080}),
        )

        with self._lock:
            self._devices[ip_address] = device

        if self.on_device_found:
            try:
                self.on_device_found(device)
            except Exception:
                pass

        return device

    def scan_hosts(self, ip_addresses: List[str]) -> List[NetworkDevice]:
        """
        Scan multiple hosts in parallel.

        Args:
            ip_addresses: List of IP addresses to scan

        Returns:
            List of discovered NetworkDevice objects
        """
        self._stop_event.clear()
        self._devices.clear()

        threads = []
        semaphore = threading.Semaphore(self.max_threads)
        completed = [0]
        total = len(ip_addresses)

        def scan_with_semaphore(ip: str):
            with semaphore:
                if self._stop_event.is_set():
                    return
                self.scan_host(ip)
                completed[0] += 1
                if self.on_progress:
                    try:
                        self.on_progress(completed[0], total)
                    except Exception:
                        pass

        for ip in ip_addresses:
            if self._stop_event.is_set():
                break
            t = threading.Thread(target=scan_with_semaphore, args=(ip,))
            t.start()
            threads.append(t)

        # Wait for all threads
        for t in threads:
            t.join()

        devices = list(self._devices.values())

        if self.on_scan_complete:
            try:
                self.on_scan_complete(devices)
            except Exception:
                pass

        return devices

    def scan_subnet(self, cidr: str) -> List[NetworkDevice]:
        """
        Scan a subnet for Meshtastic devices.

        Args:
            cidr: Subnet in CIDR notation (e.g., "192.168.1.0/24")

        Returns:
            List of discovered NetworkDevice objects
        """
        try:
            network = ipaddress.ip_network(cidr, strict=False)
            # Skip network and broadcast addresses for /24 and larger
            if network.prefixlen <= 30:
                hosts = [str(ip) for ip in network.hosts()]
            else:
                hosts = [str(ip) for ip in network]

            logger.info(f"Scanning {len(hosts)} hosts in {cidr}")
            return self.scan_hosts(hosts)

        except ValueError as e:
            logger.error(f"Invalid CIDR notation: {cidr}: {e}")
            return []

    def scan_local_network(self) -> List[NetworkDevice]:
        """
        Scan the local network for Meshtastic devices.

        Automatically detects local network interface and scans the subnet.

        Returns:
            List of discovered NetworkDevice objects
        """
        local_networks = self._get_local_networks()

        all_devices = []
        for cidr in local_networks:
            logger.info(f"Scanning local network: {cidr}")
            devices = self.scan_subnet(cidr)
            all_devices.extend(devices)

        return all_devices

    def stop(self):
        """Stop any ongoing scan"""
        self._stop_event.set()

    def get_discovered_devices(self) -> List[NetworkDevice]:
        """Get all discovered devices"""
        with self._lock:
            return list(self._devices.values())

    def get_meshtasticd_devices(self) -> List[NetworkDevice]:
        """Get discovered meshtasticd devices (port 4403 open)"""
        return [d for d in self.get_discovered_devices() if d.is_meshtasticd]

    def _check_port(self, ip: str, port: int) -> Tuple[bool, Optional[float]]:
        """
        Check if a port is open on a host.

        Returns:
            Tuple of (is_open, response_time_ms)
        """
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)

        try:
            result = sock.connect_ex((ip, port))
            response_time = (time.time() - start) * 1000  # Convert to ms
            return (result == 0, response_time if result == 0 else None)
        except socket.error:
            return (False, None)
        finally:
            sock.close()

    def _get_local_networks(self) -> List[str]:
        """Get local network CIDRs from network interfaces"""
        networks = []

        if HAS_PSUTIL:
            try:
                for iface, addrs in psutil.net_if_addrs().items():
                    for addr in addrs:
                        if addr.family == socket.AF_INET:
                            ip = addr.address
                            netmask = addr.netmask

                            # Skip loopback
                            if ip.startswith("127."):
                                continue

                            # Calculate CIDR
                            try:
                                network = ipaddress.ip_network(
                                    f"{ip}/{netmask}", strict=False
                                )
                                networks.append(str(network))
                            except ValueError:
                                pass
            except Exception as e:
                logger.debug(f"Error getting network interfaces: {e}")

        # Fallback: common private networks
        if not networks:
            networks = ["192.168.1.0/24"]

        return networks


def measure_connection_rtt(host: str, port: int, count: int = 3) -> Optional[float]:
    """
    Measure round-trip time to a host:port using TCP handshake.

    Args:
        host: Target hostname or IP
        port: Target port
        count: Number of measurements to average

    Returns:
        Average RTT in milliseconds, or None if connection failed
    """
    rtts = []

    for _ in range(count):
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)

        try:
            sock.connect((host, port))
            rtt = (time.time() - start) * 1000
            rtts.append(rtt)
        except socket.error:
            pass
        finally:
            sock.close()

    if rtts:
        return sum(rtts) / len(rtts)
    return None


# Convenience functions for quick access

def get_meshtasticd_connections() -> List[TCPConnection]:
    """Quick function to get current meshtasticd connections"""
    monitor = TCPMonitor()
    return monitor._get_tcp_connections_filtered(ports={4403})


def discover_meshtasticd_devices(subnet: Optional[str] = None) -> List[NetworkDevice]:
    """
    Quick function to discover meshtasticd devices on the network.

    Args:
        subnet: CIDR subnet to scan (e.g., "192.168.1.0/24").
                If None, scans local network automatically.

    Returns:
        List of discovered devices with meshtasticd running
    """
    scanner = NetworkScanner()
    if subnet:
        devices = scanner.scan_subnet(subnet)
    else:
        devices = scanner.scan_local_network()
    return scanner.get_meshtasticd_devices()


# For backwards compatibility
TCPConnectionMonitor = TCPMonitor
