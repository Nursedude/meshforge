"""
NodeMonitor - Core Meshtastic node monitoring class

Provides sudo-free monitoring of Meshtastic nodes via TCP interface.
Connects to meshtasticd on localhost:4403.

Features:
- Real-time node discovery and tracking
- Metrics collection (battery, voltage, signal strength)
- Position tracking
- Message monitoring
- Event callbacks for UI integration
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any
from enum import Enum

# Configure logging - default to WARNING to reduce noise
logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.WARNING)


class ConnectionState(Enum):
    """Monitor connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class NodeMetrics:
    """Node telemetry metrics"""
    battery_level: Optional[int] = None          # 0-100%
    voltage: Optional[float] = None              # Volts
    channel_utilization: Optional[float] = None  # 0-100%
    air_util_tx: Optional[float] = None          # 0-100%
    temperature: Optional[float] = None          # Celsius
    humidity: Optional[float] = None             # 0-100%
    pressure: Optional[float] = None             # hPa
    last_updated: Optional[datetime] = None


@dataclass
class NodePosition:
    """Node GPS position"""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[int] = None               # Meters
    precision_bits: Optional[int] = None
    time: Optional[datetime] = None


@dataclass
class NodeInfo:
    """Complete node information"""
    node_id: str                                 # e.g., "!abcd1234"
    node_num: int                                # Numeric node ID
    long_name: str = ""
    short_name: str = ""
    hardware_model: str = ""
    role: str = ""
    position: Optional[NodePosition] = None
    metrics: Optional[NodeMetrics] = None
    last_heard: Optional[datetime] = None
    snr: Optional[float] = None                  # Signal-to-noise ratio
    hops_away: Optional[int] = None
    via_mqtt: bool = False
    is_licensed: bool = False

    def __post_init__(self):
        if self.position is None:
            self.position = NodePosition()
        if self.metrics is None:
            self.metrics = NodeMetrics()


class NodeMonitor:
    """
    Meshtastic Node Monitor

    Connects to meshtasticd via TCP and provides real-time node monitoring
    without requiring sudo privileges.

    Example:
        monitor = NodeMonitor(host="localhost", port=4403)
        monitor.on_node_update = lambda node: print(f"Node updated: {node.long_name}")
        monitor.connect()

        # Get all nodes
        for node in monitor.get_nodes():
            print(f"{node.short_name}: Battery {node.metrics.battery_level}%")

        monitor.disconnect()
    """

    def __init__(self, host: str = "localhost", port: int = 4403):
        """
        Initialize NodeMonitor.

        Args:
            host: Hostname of meshtasticd (default: localhost)
            port: TCP port (default: 4403)
        """
        self.host = host
        self.port = port
        self.interface = None
        self._nodes: Dict[str, NodeInfo] = {}
        self._lock = threading.Lock()
        self._state = ConnectionState.DISCONNECTED
        self._running = False
        self._reconnect_thread = None

        # Callbacks
        self.on_node_update: Optional[Callable[[NodeInfo], None]] = None
        self.on_node_added: Optional[Callable[[NodeInfo], None]] = None
        self.on_node_removed: Optional[Callable[[str], None]] = None
        self.on_message: Optional[Callable[[dict], None]] = None
        self.on_connection_change: Optional[Callable[[ConnectionState], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None

        # My node info
        self.my_node_id: Optional[str] = None
        self.my_node_num: Optional[int] = None

    @property
    def state(self) -> ConnectionState:
        """Current connection state"""
        return self._state

    @state.setter
    def state(self, value: ConnectionState):
        self._state = value
        if self.on_connection_change:
            try:
                self.on_connection_change(value)
            except Exception as e:
                logger.error(f"Error in connection change callback: {e}")

    @property
    def is_connected(self) -> bool:
        """Check if currently connected"""
        if self._state != ConnectionState.CONNECTED or self.interface is None:
            return False
        # Also verify the socket is still valid
        try:
            if hasattr(self.interface, 'socket') and self.interface.socket:
                # Check if socket is still open (non-blocking check)
                self.interface.socket.getpeername()
                return True
            return self.interface.isConnected if hasattr(self.interface, 'isConnected') else False
        except (OSError, AttributeError):
            # Socket is closed or invalid
            self.state = ConnectionState.DISCONNECTED
            return False

    def connect(self, timeout: float = 10.0) -> bool:
        """
        Connect to meshtasticd.

        Args:
            timeout: Connection timeout in seconds

        Returns:
            True if connected successfully
        """
        if self.is_connected:
            logger.warning("Already connected")
            return True

        self.state = ConnectionState.CONNECTING
        self._running = True

        try:
            from meshtastic.tcp_interface import TCPInterface
            from pubsub import pub

            # Subscribe to meshtastic events
            pub.subscribe(self._on_receive, "meshtastic.receive")
            pub.subscribe(self._on_connection, "meshtastic.connection.established")
            pub.subscribe(self._on_disconnect, "meshtastic.connection.lost")
            pub.subscribe(self._on_node_update_event, "meshtastic.node.updated")

            # Pre-check: Test if port is reachable (fail fast)
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(min(timeout, 5.0))
            try:
                sock.connect((self.host, self.port))
                sock.close()
            except (socket.timeout, socket.error, OSError) as e:
                raise ConnectionError(f"Cannot reach {self.host}:{self.port} - {e}")

            # Connect
            logger.info(f"Connecting to {self.host}:{self.port}...")
            self.interface = TCPInterface(
                hostname=self.host,
                portNumber=self.port
            )

            # Wait for connection
            start = time.time()
            while self.interface.myInfo is None and (time.time() - start) < timeout:
                time.sleep(0.1)

            if self.interface.myInfo:
                self.my_node_num = self.interface.myInfo.my_node_num
                self.my_node_id = f"!{self.my_node_num:08x}"
                self.state = ConnectionState.CONNECTED
                self._load_initial_nodes()
                logger.info(f"Connected. My node: {self.my_node_id}")
                return True
            else:
                raise TimeoutError("Connection timeout")

        except ImportError as e:
            logger.error(f"meshtastic package not installed: {e}")
            self.state = ConnectionState.ERROR
            if self.on_error:
                self.on_error(e)
            return False

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.state = ConnectionState.ERROR
            if self.on_error:
                self.on_error(e)
            return False

    def disconnect(self, timeout: float = 5.0):
        """Disconnect from meshtasticd and wait for threads to finish

        Args:
            timeout: Seconds to wait for threads to finish
        """
        self._running = False
        self.state = ConnectionState.DISCONNECTED  # Set state first to prevent reconnect attempts

        # Wait for reconnect thread to finish if running
        if hasattr(self, '_reconnect_thread') and self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_thread.join(timeout=timeout)
            if self._reconnect_thread.is_alive():
                logger.warning("Reconnect thread did not stop in time")

        if self.interface:
            # Unsubscribe from events first to prevent callbacks during close
            try:
                from pubsub import pub
                pub.unsubscribe(self._on_receive, "meshtastic.receive")
                pub.unsubscribe(self._on_connection, "meshtastic.connection.established")
                pub.unsubscribe(self._on_disconnect, "meshtastic.connection.lost")
                pub.unsubscribe(self._on_node_update_event, "meshtastic.node.updated")
            except Exception:
                pass

            # Stop heartbeat timer if it exists
            try:
                if hasattr(self.interface, '_heartbeatTimer') and self.interface._heartbeatTimer:
                    self.interface._heartbeatTimer.cancel()
                    self.interface._heartbeatTimer = None
            except Exception:
                pass

            # Close the interface
            try:
                self.interface.close()
            except (BrokenPipeError, OSError, Exception) as e:
                # Ignore broken pipe on disconnect - expected if connection already lost
                if not isinstance(e, BrokenPipeError):
                    logger.debug(f"Error closing interface: {e}")

            self.interface = None

        logger.info("Disconnected")

    def _load_initial_nodes(self):
        """Load existing nodes from interface"""
        if not self.interface or not self.interface.nodes:
            return

        with self._lock:
            for node_id, node_data in self.interface.nodes.items():
                node_info = self._parse_node_data(node_id, node_data)
                if node_info:
                    self._nodes[node_info.node_id] = node_info
                    if self.on_node_added:
                        try:
                            self.on_node_added(node_info)
                        except Exception as e:
                            logger.error(f"Error in node_added callback: {e}")

        logger.info(f"Loaded {len(self._nodes)} nodes")

    def _parse_node_data(self, node_id: str, data: dict) -> Optional[NodeInfo]:
        """Parse node data from meshtastic interface"""
        try:
            user = data.get('user', {})
            position = data.get('position', {})
            device_metrics = data.get('deviceMetrics', {})
            env_metrics = data.get('environmentMetrics', {})

            # Extract node number - handle None values
            node_num = data.get('num')
            if node_num is None:
                node_num = 0
            if isinstance(node_id, str) and node_id.startswith('!'):
                try:
                    node_num = int(node_id[1:], 16)
                except (ValueError, TypeError):
                    pass

            # Ensure node_num is valid for formatting
            if not isinstance(node_num, int):
                node_num = 0

            node_info = NodeInfo(
                node_id=f"!{node_num:08x}" if node_num else str(node_id),
                node_num=node_num,
                long_name=user.get('longName', ''),
                short_name=user.get('shortName', ''),
                hardware_model=user.get('hwModel', ''),
                role=user.get('role', ''),
                snr=data.get('snr'),
                hops_away=data.get('hopsAway'),
                via_mqtt=data.get('viaMqtt', False),
                is_licensed=user.get('isLicensed', False),
            )

            # Position - handle both float (latitude) and integer (latitudeI) formats
            if position:
                # Prefer float format, fall back to integer format (divide by 1e7)
                lat = position.get('latitude')
                if lat is None:
                    lat_i = position.get('latitudeI')
                    lat = lat_i / 1e7 if lat_i is not None else None

                lon = position.get('longitude')
                if lon is None:
                    lon_i = position.get('longitudeI')
                    lon = lon_i / 1e7 if lon_i is not None else None

                # Handle timestamp - may be None or invalid
                pos_time = None
                if 'time' in position and position['time']:
                    try:
                        pos_time = datetime.fromtimestamp(position['time'])
                    except (TypeError, ValueError, OSError):
                        pass

                node_info.position = NodePosition(
                    latitude=lat,
                    longitude=lon,
                    altitude=position.get('altitude'),
                    precision_bits=position.get('precisionBits'),
                    time=pos_time,
                )

            # Metrics
            node_info.metrics = NodeMetrics(
                battery_level=device_metrics.get('batteryLevel'),
                voltage=device_metrics.get('voltage'),
                channel_utilization=device_metrics.get('channelUtilization'),
                air_util_tx=device_metrics.get('airUtilTx'),
                temperature=env_metrics.get('temperature'),
                humidity=env_metrics.get('relativeHumidity'),
                pressure=env_metrics.get('barometricPressure'),
                last_updated=datetime.now(),
            )

            # Last heard - handle None or invalid timestamps
            if 'lastHeard' in data and data['lastHeard']:
                try:
                    node_info.last_heard = datetime.fromtimestamp(data['lastHeard'])
                except (TypeError, ValueError, OSError):
                    pass

            return node_info

        except Exception as e:
            logger.error(f"Error parsing node data: {e}")
            return None

    def _on_receive(self, packet, interface):
        """Handle received packets"""
        try:
            if self.on_message:
                self.on_message(packet)
        except Exception as e:
            logger.error(f"Error in message callback: {e}")

    def _on_connection(self, interface, topic=None):
        """Handle connection established"""
        logger.info("Connection established")
        self.state = ConnectionState.CONNECTED

    def _on_disconnect(self, interface, topic=None):
        """Handle connection lost"""
        logger.warning("Connection lost")
        self.state = ConnectionState.DISCONNECTED

        # Auto-reconnect if still running
        if self._running:
            self._start_reconnect()

    def _on_node_update_event(self, node, interface):
        """Handle node update from meshtastic"""
        try:
            if isinstance(node, dict):
                node_id = node.get('num', 0)
                node_info = self._parse_node_data(str(node_id), node)
            else:
                node_id = getattr(node, 'num', 0)
                node_info = self._parse_node_data(str(node_id), node.__dict__ if hasattr(node, '__dict__') else {})

            if node_info:
                with self._lock:
                    is_new = node_info.node_id not in self._nodes
                    self._nodes[node_info.node_id] = node_info

                if is_new and self.on_node_added:
                    self.on_node_added(node_info)
                elif self.on_node_update:
                    self.on_node_update(node_info)

        except Exception as e:
            logger.error(f"Error handling node update: {e}")

    def _start_reconnect(self):
        """Start reconnection thread"""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return

        self.state = ConnectionState.RECONNECTING

        def reconnect_loop():
            delay = 1
            max_delay = 30
            while self._running and self._state == ConnectionState.RECONNECTING:
                logger.info(f"Attempting reconnect in {delay}s...")
                time.sleep(delay)
                if self.connect(timeout=5):
                    break
                delay = min(delay * 2, max_delay)

        self._reconnect_thread = threading.Thread(target=reconnect_loop, daemon=True)
        self._reconnect_thread.start()

    def get_nodes(self, refresh: bool = False) -> List[NodeInfo]:
        """Get all known nodes

        Args:
            refresh: If True, re-sync from interface before returning
        """
        if refresh:
            self.sync_nodes()
        with self._lock:
            return list(self._nodes.values())

    def sync_nodes(self):
        """Re-sync nodes from interface (picks up any nodes we missed)"""
        if not self.interface or not hasattr(self.interface, 'nodes'):
            return

        try:
            interface_nodes = self.interface.nodes or {}
            with self._lock:
                for node_id, node_data in interface_nodes.items():
                    node_info = self._parse_node_data(node_id, node_data)
                    if node_info and node_info.node_id not in self._nodes:
                        self._nodes[node_info.node_id] = node_info
                        logger.debug(f"Synced new node: {node_info.node_id}")
            logger.info(f"Sync complete: {len(self._nodes)} nodes")
        except Exception as e:
            logger.error(f"Error syncing nodes: {e}")

    def get_node(self, node_id: str) -> Optional[NodeInfo]:
        """Get a specific node by ID"""
        with self._lock:
            return self._nodes.get(node_id)

    def get_node_count(self) -> int:
        """Get total number of known nodes"""
        with self._lock:
            return len(self._nodes)

    def get_my_node(self) -> Optional[NodeInfo]:
        """Get this node's info"""
        if self.my_node_id:
            return self.get_node(self.my_node_id)
        return None

    def send_text(self, text: str, destination: Optional[str] = None) -> bool:
        """
        Send a text message.

        Args:
            text: Message text
            destination: Destination node ID (None for broadcast)

        Returns:
            True if sent successfully
        """
        if not self.is_connected:
            logger.error("Not connected")
            return False

        try:
            dest_num = None
            if destination:
                if destination.startswith('!'):
                    dest_num = int(destination[1:], 16)
                else:
                    dest_num = int(destination)

            self.interface.sendText(text, destinationId=dest_num)
            logger.info(f"Sent message: {text[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    def request_position(self, node_id: str) -> bool:
        """Request position from a node"""
        if not self.is_connected:
            return False

        try:
            if node_id.startswith('!'):
                dest_num = int(node_id[1:], 16)
            else:
                dest_num = int(node_id)

            self.interface.sendPosition(destinationId=dest_num, wantResponse=True)
            return True
        except Exception as e:
            logger.error(f"Failed to request position: {e}")
            return False

    def to_dict(self) -> Dict[str, Any]:
        """Export monitor state as dictionary"""
        return {
            'host': self.host,
            'port': self.port,
            'state': self.state.value,
            'my_node_id': self.my_node_id,
            'node_count': self.get_node_count(),
            'nodes': [
                {
                    'node_id': n.node_id,
                    'long_name': n.long_name,
                    'short_name': n.short_name,
                    'hardware': n.hardware_model,
                    'battery': n.metrics.battery_level if n.metrics else None,
                    'last_heard': n.last_heard.isoformat() if n.last_heard else None,
                }
                for n in self.get_nodes()
            ]
        }


# Convenience function
def create_monitor(host: str = "localhost", port: int = 4403) -> NodeMonitor:
    """Create and return a NodeMonitor instance"""
    return NodeMonitor(host=host, port=port)
