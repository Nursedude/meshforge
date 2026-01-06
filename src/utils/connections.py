"""
Multi-mode device connection abstraction for Meshtastic devices.

Version: 0.4.3-beta
Updated: 2026-01-06

Provides unified interface for connecting to Meshtastic devices via:
- USB Serial (most common)
- Bluetooth LE (wireless)
- TCP/IP (network, meshtasticd daemon)

Patterns adapted from RNS_Over_Meshtastic_Gateway.
"""

import time
import logging
from enum import Enum
from typing import Optional, Callable, Any, List
from dataclasses import dataclass
from abc import ABC, abstractmethod

from .system import get_serial_ports, check_dependency

logger = logging.getLogger(__name__)


class ConnectionMode(Enum):
    """Available connection modes for Meshtastic devices."""
    SERIAL = "serial"
    BLE = "ble"
    TCP = "tcp"
    AUTO = "auto"


@dataclass
class ConnectionConfig:
    """Configuration for device connection."""
    mode: ConnectionMode = ConnectionMode.AUTO
    serial_port: Optional[str] = None  # e.g., /dev/ttyUSB0, COM3
    ble_address: Optional[str] = None  # e.g., "AA:BB:CC:DD:EE:FF"
    tcp_host: str = "127.0.0.1"
    tcp_port: int = 4403
    timeout: float = 10.0
    retry_count: int = 3
    retry_delay: float = 2.0


@dataclass
class ConnectionStatus:
    """Current connection status."""
    connected: bool = False
    mode: Optional[ConnectionMode] = None
    address: Optional[str] = None
    device_info: Optional[dict] = None
    error: Optional[str] = None


class DeviceConnection(ABC):
    """Abstract base class for device connections."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connection is active."""
        pass

    @abstractmethod
    def get_interface(self) -> Any:
        """Get the underlying meshtastic interface."""
        pass


class SerialConnection(DeviceConnection):
    """Serial/USB connection to Meshtastic device."""

    def __init__(self, port: str, timeout: float = 10.0):
        self.port = port
        self.timeout = timeout
        self._interface = None

    def connect(self) -> bool:
        try:
            from meshtastic.serial_interface import SerialInterface
            logger.info(f"Connecting to serial port {self.port}...")
            self._interface = SerialInterface(devPath=self.port)
            time.sleep(2)  # Wait for connection to stabilize
            return self._interface is not None
        except Exception as e:
            logger.error(f"Serial connection failed: {e}")
            return False

    def disconnect(self) -> None:
        if self._interface:
            try:
                self._interface.close()
            except Exception:
                pass
            self._interface = None

    def is_connected(self) -> bool:
        return self._interface is not None

    def get_interface(self) -> Any:
        return self._interface


class BLEConnection(DeviceConnection):
    """Bluetooth LE connection to Meshtastic device."""

    def __init__(self, address: str, timeout: float = 10.0):
        self.address = address
        self.timeout = timeout
        self._interface = None

    def connect(self) -> bool:
        try:
            from meshtastic.ble_interface import BLEInterface
            logger.info(f"Connecting to BLE device {self.address}...")
            self._interface = BLEInterface(address=self.address)
            time.sleep(2)
            return self._interface is not None
        except Exception as e:
            logger.error(f"BLE connection failed: {e}")
            return False

    def disconnect(self) -> None:
        if self._interface:
            try:
                self._interface.close()
            except Exception:
                pass
            self._interface = None

    def is_connected(self) -> bool:
        return self._interface is not None

    def get_interface(self) -> Any:
        return self._interface


class TCPConnection(DeviceConnection):
    """TCP/IP connection to meshtasticd daemon."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4403, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._interface = None

    def connect(self) -> bool:
        try:
            from meshtastic.tcp_interface import TCPInterface
            logger.info(f"Connecting to TCP {self.host}:{self.port}...")
            self._interface = TCPInterface(hostname=self.host, portNumber=self.port)
            time.sleep(1)
            return self._interface is not None
        except Exception as e:
            logger.error(f"TCP connection failed: {e}")
            return False

    def disconnect(self) -> None:
        if self._interface:
            try:
                self._interface.close()
            except Exception:
                pass
            self._interface = None

    def is_connected(self) -> bool:
        return self._interface is not None

    def get_interface(self) -> Any:
        return self._interface


class MeshtasticConnector:
    """
    High-level connector that handles auto-detection and connection management.

    Example usage:
        connector = MeshtasticConnector()

        # Auto-detect and connect
        if connector.connect():
            interface = connector.get_interface()
            # Use interface...
            connector.disconnect()

        # Or specify connection
        config = ConnectionConfig(
            mode=ConnectionMode.SERIAL,
            serial_port="/dev/ttyUSB0"
        )
        connector.connect(config)
    """

    def __init__(self):
        self._connection: Optional[DeviceConnection] = None
        self._status = ConnectionStatus()
        self._on_connect: Optional[Callable] = None
        self._on_disconnect: Optional[Callable] = None

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    def set_callbacks(
        self,
        on_connect: Optional[Callable] = None,
        on_disconnect: Optional[Callable] = None
    ) -> None:
        """Set connection/disconnection callbacks."""
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    def connect(self, config: Optional[ConnectionConfig] = None) -> bool:
        """
        Connect to Meshtastic device.

        Args:
            config: Connection configuration. If None, auto-detects.

        Returns:
            True if connection successful.
        """
        if config is None:
            config = ConnectionConfig(mode=ConnectionMode.AUTO)

        # Check if meshtastic is available
        if not check_dependency('meshtastic'):
            self._status.error = "Meshtastic library not installed"
            return False

        # Handle connection based on mode
        if config.mode == ConnectionMode.AUTO:
            return self._auto_connect(config)
        elif config.mode == ConnectionMode.SERIAL:
            return self._serial_connect(config)
        elif config.mode == ConnectionMode.BLE:
            return self._ble_connect(config)
        elif config.mode == ConnectionMode.TCP:
            return self._tcp_connect(config)

        return False

    def _auto_connect(self, config: ConnectionConfig) -> bool:
        """Try all connection methods in order of preference."""
        # Try serial first (most common)
        ports = get_serial_ports()
        for port in ports:
            config.serial_port = port
            if self._serial_connect(config):
                return True

        # Try TCP (meshtasticd)
        if self._tcp_connect(config):
            return True

        self._status.error = "No device found"
        return False

    def _serial_connect(self, config: ConnectionConfig) -> bool:
        """Connect via serial port."""
        if not config.serial_port:
            self._status.error = "No serial port specified"
            return False

        self._connection = SerialConnection(config.serial_port, config.timeout)

        for attempt in range(config.retry_count):
            if self._connection.connect():
                self._status.connected = True
                self._status.mode = ConnectionMode.SERIAL
                self._status.address = config.serial_port
                self._status.error = None
                if self._on_connect:
                    self._on_connect(self._status)
                return True
            time.sleep(config.retry_delay)

        self._status.error = f"Failed to connect to {config.serial_port}"
        return False

    def _ble_connect(self, config: ConnectionConfig) -> bool:
        """Connect via Bluetooth LE."""
        if not config.ble_address:
            self._status.error = "No BLE address specified"
            return False

        self._connection = BLEConnection(config.ble_address, config.timeout)

        for attempt in range(config.retry_count):
            if self._connection.connect():
                self._status.connected = True
                self._status.mode = ConnectionMode.BLE
                self._status.address = config.ble_address
                self._status.error = None
                if self._on_connect:
                    self._on_connect(self._status)
                return True
            time.sleep(config.retry_delay)

        self._status.error = f"Failed to connect to BLE {config.ble_address}"
        return False

    def _tcp_connect(self, config: ConnectionConfig) -> bool:
        """Connect via TCP to meshtasticd."""
        self._connection = TCPConnection(config.tcp_host, config.tcp_port, config.timeout)

        for attempt in range(config.retry_count):
            if self._connection.connect():
                self._status.connected = True
                self._status.mode = ConnectionMode.TCP
                self._status.address = f"{config.tcp_host}:{config.tcp_port}"
                self._status.error = None
                if self._on_connect:
                    self._on_connect(self._status)
                return True
            time.sleep(config.retry_delay)

        self._status.error = f"Failed to connect to TCP {config.tcp_host}:{config.tcp_port}"
        return False

    def disconnect(self) -> None:
        """Disconnect from device."""
        if self._connection:
            self._connection.disconnect()
            self._connection = None

        self._status.connected = False
        self._status.mode = None
        self._status.address = None

        if self._on_disconnect:
            self._on_disconnect(self._status)

    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connection is not None and self._connection.is_connected()

    def get_interface(self) -> Optional[Any]:
        """Get the underlying meshtastic interface."""
        if self._connection:
            return self._connection.get_interface()
        return None

    def get_device_info(self) -> Optional[dict]:
        """Get device information if connected."""
        interface = self.get_interface()
        if interface and hasattr(interface, 'myInfo') and interface.myInfo:
            return {
                'node_num': interface.myInfo.my_node_num,
                'has_gps': interface.myInfo.has_gps,
                'num_bands': getattr(interface.myInfo, 'num_bands', None),
            }
        return None


def detect_devices() -> List[dict]:
    """
    Detect available Meshtastic devices.

    Returns:
        List of detected devices with connection info.
    """
    devices = []

    # Check serial ports
    for port in get_serial_ports():
        devices.append({
            'mode': ConnectionMode.SERIAL,
            'address': port,
            'description': f"Serial: {port}"
        })

    # Check for meshtasticd
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', 4403))
        sock.close()
        if result == 0:
            devices.append({
                'mode': ConnectionMode.TCP,
                'address': '127.0.0.1:4403',
                'description': 'TCP: meshtasticd (localhost)'
            })
    except Exception:
        pass

    return devices
