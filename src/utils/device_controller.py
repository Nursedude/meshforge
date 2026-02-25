"""
MeshForge Device Controller - Unified Connection Abstraction Layer

Inspired by meshtastic/standalone-ui ViewController pattern.
Provides a single interface for Serial, TCP, and USB connections.

Usage:
    # Auto-detect and connect
    controller = DeviceController()
    controller.connect()

    # Or specify connection type
    controller = DeviceController(connection_type=ConnectionType.TCP, host="localhost", port=4403)
    controller.connect()

    # Use the interface
    nodes = controller.get_nodes()
    controller.send_message("Hello mesh!", channel=0)

    # Clean up
    controller.disconnect()

Design:
    - MeshtasticBackend ABC defines the interface
    - Concrete backends: TCPBackend, SerialBackend, BLEBackend
    - DeviceController manages lifecycle and provides unified API
    - Integrates with DeviceScanner for auto-discovery
    - Thread-safe with proper locking
    - Centralized retry/reconnect logic
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Import device persistence (optional - graceful fallback)
from utils.safe_import import safe_import
_get_device_persistence, _DevicePersistence, PERSISTENCE_AVAILABLE = safe_import(
    'utils.device_persistence', 'get_device_persistence', 'DevicePersistence'
)
get_device_persistence = _get_device_persistence
DevicePersistence = _DevicePersistence


class ConnectionType(Enum):
    """Supported connection types."""
    AUTO = auto()      # Auto-detect best connection
    TCP = auto()       # TCP to meshtasticd daemon
    SERIAL = auto()    # Direct USB serial
    BLE = auto()       # Bluetooth LE


class ConnectionState(Enum):
    """Connection state machine."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    ERROR = auto()


@dataclass
class ConnectionConfig:
    """Configuration for device connections."""
    connection_type: ConnectionType = ConnectionType.AUTO

    # TCP settings
    host: str = "localhost"
    port: int = 4403

    # Serial settings
    serial_port: Optional[str] = None
    baud_rate: int = 115200

    # BLE settings
    ble_address: Optional[str] = None

    # Retry settings
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0
    max_retry_delay: float = 30.0

    # Connection settings
    timeout: float = 10.0
    cooldown: float = 1.0


@dataclass
class ConnectionStatus:
    """Current connection status."""
    state: ConnectionState = ConnectionState.DISCONNECTED
    connection_type: Optional[ConnectionType] = None
    device_info: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    last_connected: Optional[float] = None
    reconnect_attempts: int = 0


class MeshtasticBackend(ABC):
    """
    Abstract base class for Meshtastic connection backends.

    All connection types (TCP, Serial, BLE) implement this interface.
    Inspired by standalone-ui's SerialClient/UARTClient pattern.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection and clean up resources."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connection is active."""
        pass

    @abstractmethod
    def get_interface(self) -> Any:
        """Get the underlying meshtastic interface object."""
        pass

    @abstractmethod
    def get_device_info(self) -> Dict[str, Any]:
        """Get device information (node ID, firmware, etc.)."""
        pass

    @property
    @abstractmethod
    def connection_type(self) -> ConnectionType:
        """Return the connection type."""
        pass


class TCPBackend(MeshtasticBackend):
    """TCP connection to meshtasticd daemon.

    Uses the global MESHTASTIC_CONNECTION_LOCK to respect meshtasticd's
    single TCP client constraint (Issue #17). Direct TCPInterface creation
    without the lock would contend with the gateway bridge and break :9443.
    """

    def __init__(self, host: str = "localhost", port: int = 4403, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._interface = None
        self._connected = False
        self._holds_lock = False

    def connect(self) -> bool:
        try:
            from meshtastic.tcp_interface import TCPInterface
            from utils.meshtastic_connection import (
                MESHTASTIC_CONNECTION_LOCK, wait_for_cooldown
            )

            # Acquire global lock — meshtasticd only supports ONE TCP client
            if not MESHTASTIC_CONNECTION_LOCK.acquire(timeout=self.timeout):
                logger.error("Connection lock busy — another component owns TCP")
                return False
            self._holds_lock = True

            wait_for_cooldown()
            self._interface = TCPInterface(hostname=self.host, portNumber=self.port)
            self._connected = True
            logger.info(f"TCP connected to {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"TCP connection failed: {e}")
            self._connected = False
            self._release_lock()
            return False

    def _release_lock(self):
        """Release the global connection lock if we hold it."""
        if self._holds_lock:
            try:
                from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
                MESHTASTIC_CONNECTION_LOCK.release()
            except (RuntimeError, ImportError):
                pass
            self._holds_lock = False

    def disconnect(self) -> None:
        if self._interface:
            try:
                self._interface.close()
            except Exception as e:
                logger.debug(f"Error closing TCP interface: {e}")
            finally:
                self._interface = None
                self._connected = False
                self._release_lock()

    def is_connected(self) -> bool:
        return self._connected and self._interface is not None

    def get_interface(self) -> Any:
        return self._interface

    def get_device_info(self) -> Dict[str, Any]:
        if not self._interface:
            return {}
        try:
            my_info = self._interface.myInfo
            return {
                "node_id": getattr(my_info, "my_node_num", None),
                "firmware": getattr(self._interface, "metadata", {}).get("firmwareVersion", "unknown"),
                "connection": f"tcp://{self.host}:{self.port}"
            }
        except Exception:
            return {}

    @property
    def connection_type(self) -> ConnectionType:
        return ConnectionType.TCP


class SerialBackend(MeshtasticBackend):
    """Direct USB serial connection."""

    def __init__(self, port: str, baud_rate: int = 115200, timeout: float = 10.0):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self._interface = None
        self._connected = False

    def connect(self) -> bool:
        try:
            from meshtastic.serial_interface import SerialInterface
            self._interface = SerialInterface(devPath=self.port)
            self._connected = True
            logger.info(f"Serial connected to {self.port}")
            return True
        except Exception as e:
            logger.error(f"Serial connection failed: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._interface:
            try:
                self._interface.close()
            except Exception as e:
                logger.debug(f"Error closing serial interface: {e}")
            finally:
                self._interface = None
                self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._interface is not None

    def get_interface(self) -> Any:
        return self._interface

    def get_device_info(self) -> Dict[str, Any]:
        if not self._interface:
            return {}
        try:
            my_info = self._interface.myInfo
            return {
                "node_id": getattr(my_info, "my_node_num", None),
                "firmware": getattr(self._interface, "metadata", {}).get("firmwareVersion", "unknown"),
                "connection": f"serial://{self.port}"
            }
        except Exception:
            return {}

    @property
    def connection_type(self) -> ConnectionType:
        return ConnectionType.SERIAL


class BLEBackend(MeshtasticBackend):
    """Bluetooth LE connection."""

    def __init__(self, address: str, timeout: float = 10.0):
        self.address = address
        self.timeout = timeout
        self._interface = None
        self._connected = False

    def connect(self) -> bool:
        try:
            from meshtastic.ble_interface import BLEInterface
            self._interface = BLEInterface(address=self.address)
            self._connected = True
            logger.info(f"BLE connected to {self.address}")
            return True
        except Exception as e:
            logger.error(f"BLE connection failed: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._interface:
            try:
                self._interface.close()
            except Exception as e:
                logger.debug(f"Error closing BLE interface: {e}")
            finally:
                self._interface = None
                self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._interface is not None

    def get_interface(self) -> Any:
        return self._interface

    def get_device_info(self) -> Dict[str, Any]:
        if not self._interface:
            return {}
        try:
            my_info = self._interface.myInfo
            return {
                "node_id": getattr(my_info, "my_node_num", None),
                "firmware": getattr(self._interface, "metadata", {}).get("firmwareVersion", "unknown"),
                "connection": f"ble://{self.address}"
            }
        except Exception:
            return {}

    @property
    def connection_type(self) -> ConnectionType:
        return ConnectionType.BLE


class DeviceController:
    """
    Unified controller for Meshtastic device connections.

    Inspired by standalone-ui's ViewController pattern:
    - Manages connection lifecycle
    - Provides consistent API regardless of connection type
    - Integrates with DeviceScanner for auto-discovery
    - Thread-safe with proper locking
    - Centralized retry/reconnect logic
    """

    _lock = threading.RLock()

    def __init__(self, config: Optional[ConnectionConfig] = None, **kwargs):
        """
        Initialize DeviceController.

        Args:
            config: ConnectionConfig object, or pass individual settings as kwargs
            **kwargs: Override config settings (host, port, serial_port, etc.)
        """
        self.config = config or ConnectionConfig()

        # Apply kwargs overrides
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

        self._backend: Optional[MeshtasticBackend] = None
        self._status = ConnectionStatus()
        self._callbacks: Dict[str, List[Callable]] = {
            "on_connect": [],
            "on_disconnect": [],
            "on_message": [],
            "on_error": [],
        }
        self._last_disconnect_time: float = 0

    @property
    def status(self) -> ConnectionStatus:
        """Get current connection status."""
        return self._status

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return (self._backend is not None and
                self._backend.is_connected() and
                self._status.state == ConnectionState.CONNECTED)

    def connect(self) -> bool:
        """
        Establish connection to Meshtastic device.

        Uses config.connection_type to determine method:
        - AUTO: Try TCP → Serial → BLE
        - TCP: Connect to meshtasticd
        - SERIAL: Direct USB connection
        - BLE: Bluetooth connection

        Returns:
            True if connection successful, False otherwise
        """
        with self._lock:
            # Check cooldown
            if time.time() - self._last_disconnect_time < self.config.cooldown:
                time.sleep(self.config.cooldown)

            self._status.state = ConnectionState.CONNECTING
            self._status.error_message = None

            if self.config.connection_type == ConnectionType.AUTO:
                return self._auto_connect()
            else:
                return self._connect_specific(self.config.connection_type)

    def _auto_connect(self) -> bool:
        """Auto-detect and connect to best available device.

        Priority:
        1. Last known successful device (if persistence enabled)
        2. TCP to meshtasticd (most common)
        3. Serial ports
        4. BLE (slowest)
        """
        # Try last known device first (if available)
        if PERSISTENCE_AVAILABLE:
            persistence = get_device_persistence()
            if persistence.has_last_device():
                last = persistence.get_last_device()
                logger.info(f"Trying last known device: {last.get('connection_type')}://{last.get('address')}")
                if self._try_last_device(last):
                    return True
                logger.info("Last device unavailable, trying other options")

        # Try TCP first (meshtasticd is most common)
        if self._try_tcp():
            return True

        # Try serial ports
        if self._try_serial():
            return True

        # Try BLE (slowest)
        if self._try_ble():
            return True

        self._status.state = ConnectionState.ERROR
        self._status.error_message = "No Meshtastic device found"
        return False

    def _try_last_device(self, last: dict) -> bool:
        """Try to connect to last known device."""
        try:
            conn_type = last.get("connection_type")
            address = last.get("address")

            if conn_type == "tcp":
                parts = address.split(":")
                host = parts[0] if parts else "localhost"
                port = int(parts[1]) if len(parts) > 1 else 4403
                self.config.host = host
                self.config.port = port
                return self._connect_specific(ConnectionType.TCP)

            elif conn_type == "serial":
                self.config.serial_port = address
                return self._connect_specific(ConnectionType.SERIAL)

            elif conn_type == "ble":
                self.config.ble_address = address
                return self._connect_specific(ConnectionType.BLE)

        except Exception as e:
            logger.debug(f"Failed to connect to last device: {e}")
        return False

    def _try_tcp(self) -> bool:
        """Try TCP connection to meshtasticd."""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            result = sock.connect_ex((self.config.host, self.config.port))
            sock.close()

            if result == 0:
                return self._connect_specific(ConnectionType.TCP)
        except Exception:
            pass
        return False

    def _try_serial(self) -> bool:
        """Try serial connection using device scanner."""
        try:
            from utils.device_scanner import DeviceScanner
            scanner = DeviceScanner()
            result = scanner.scan_all()

            for port in result.get('serial_ports', []):
                if port.meshtastic_compatible:
                    self.config.serial_port = port.device
                    if self._connect_specific(ConnectionType.SERIAL):
                        return True
        except Exception as e:
            logger.debug(f"Serial scan failed: {e}")
        return False

    def _try_ble(self) -> bool:
        """Try BLE connection."""
        # BLE scanning is slow and complex - skip for now
        # Future: Implement BLE device discovery
        return False

    def _connect_specific(self, conn_type: ConnectionType) -> bool:
        """Connect using specific connection type."""
        backend: Optional[MeshtasticBackend] = None

        try:
            if conn_type == ConnectionType.TCP:
                backend = TCPBackend(
                    host=self.config.host,
                    port=self.config.port,
                    timeout=self.config.timeout
                )
            elif conn_type == ConnectionType.SERIAL:
                if not self.config.serial_port:
                    raise ValueError("Serial port not specified")
                backend = SerialBackend(
                    port=self.config.serial_port,
                    baud_rate=self.config.baud_rate,
                    timeout=self.config.timeout
                )
            elif conn_type == ConnectionType.BLE:
                if not self.config.ble_address:
                    raise ValueError("BLE address not specified")
                backend = BLEBackend(
                    address=self.config.ble_address,
                    timeout=self.config.timeout
                )
            else:
                raise ValueError(f"Unknown connection type: {conn_type}")

            if backend.connect():
                self._backend = backend
                self._status.state = ConnectionState.CONNECTED
                self._status.connection_type = conn_type
                self._status.device_info = backend.get_device_info()
                self._status.last_connected = time.time()
                self._status.reconnect_attempts = 0

                # Record successful connection for persistence
                self._record_connection(conn_type, success=True)

                self._fire_callback("on_connect", self._status)
                return True
            else:
                # Record failed connection attempt
                self._record_connection(conn_type, success=False,
                                       error_message="Connection refused")
                return False

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self._status.state = ConnectionState.ERROR
            self._status.error_message = str(e)
            # Record failed connection attempt
            self._record_connection(conn_type, success=False, error_message=str(e))
            self._fire_callback("on_error", e)
            return False

    def _record_connection(self, conn_type: ConnectionType, success: bool,
                          error_message: Optional[str] = None) -> None:
        """Record connection attempt for persistence."""
        if not PERSISTENCE_AVAILABLE:
            return

        try:
            persistence = get_device_persistence()

            # Build address string based on connection type
            if conn_type == ConnectionType.TCP:
                address = f"{self.config.host}:{self.config.port}"
                type_str = "tcp"
            elif conn_type == ConnectionType.SERIAL:
                address = self.config.serial_port or ""
                type_str = "serial"
            elif conn_type == ConnectionType.BLE:
                address = self.config.ble_address or ""
                type_str = "ble"
            else:
                return  # Don't record AUTO type

            persistence.record_connection(
                connection_type=type_str,
                address=address,
                device_info=self._status.device_info if success else {},
                success=success,
                error_message=error_message,
            )
        except Exception as e:
            logger.debug(f"Failed to record connection: {e}")

    def disconnect(self) -> None:
        """Disconnect from device."""
        with self._lock:
            if self._backend:
                self._backend.disconnect()
                self._backend = None

            self._status.state = ConnectionState.DISCONNECTED
            self._last_disconnect_time = time.time()
            self._fire_callback("on_disconnect", self._status)

    def reconnect(self) -> bool:
        """Attempt to reconnect with exponential backoff."""
        with self._lock:
            self._status.state = ConnectionState.RECONNECTING
            self._status.reconnect_attempts += 1

            delay = min(
                self.config.retry_delay * (self.config.retry_backoff ** (self._status.reconnect_attempts - 1)),
                self.config.max_retry_delay
            )

            logger.info(f"Reconnect attempt {self._status.reconnect_attempts} in {delay:.1f}s")
            time.sleep(delay)

            self.disconnect()
            return self.connect()

    def get_interface(self) -> Any:
        """Get the underlying meshtastic interface object."""
        if self._backend:
            return self._backend.get_interface()
        return None

    # High-level API methods

    def get_nodes(self) -> Dict[str, Any]:
        """Get all known nodes."""
        iface = self.get_interface()
        if iface and hasattr(iface, 'nodes'):
            return iface.nodes or {}
        return {}

    def get_my_node_info(self) -> Dict[str, Any]:
        """Get local node information."""
        return self._status.device_info

    def send_message(self, text: str, destination: str = "^all", channel: int = 0) -> bool:
        """
        Send a text message.

        Args:
            text: Message text
            destination: Node ID or "^all" for broadcast
            channel: Channel index

        Returns:
            True if message sent successfully
        """
        iface = self.get_interface()
        if not iface:
            return False

        try:
            iface.sendText(text, destinationId=destination, channelIndex=channel)
            return True
        except Exception as e:
            logger.error(f"Send message failed: {e}")
            self._fire_callback("on_error", e)
            return False

    def get_channels(self) -> List[Dict[str, Any]]:
        """Get channel configuration."""
        iface = self.get_interface()
        if not iface:
            return []

        try:
            channels = []
            node = iface.getNode("^local")
            if node and hasattr(node, 'channels'):
                for ch in node.channels:
                    channels.append({
                        "index": ch.index,
                        "name": ch.settings.name if ch.settings else "",
                        "role": ch.role,
                    })
            return channels
        except Exception as e:
            logger.error(f"Get channels failed: {e}")
            return []

    # Callback management

    def on(self, event: str, callback: Callable) -> None:
        """Register event callback."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def off(self, event: str, callback: Callable) -> None:
        """Unregister event callback."""
        if event in self._callbacks and callback in self._callbacks[event]:
            self._callbacks[event].remove(callback)

    def _fire_callback(self, event: str, data: Any) -> None:
        """Fire all callbacks for an event."""
        for callback in self._callbacks.get(event, []):
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    # Context manager support

    def __enter__(self) -> "DeviceController":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()


# Singleton instance for global access
_controller_instance: Optional[DeviceController] = None
_controller_lock = threading.Lock()


def get_device_controller(config: Optional[ConnectionConfig] = None, **kwargs) -> DeviceController:
    """
    Get the global DeviceController instance.

    Thread-safe singleton pattern for shared connection management.
    """
    global _controller_instance

    with _controller_lock:
        if _controller_instance is None:
            _controller_instance = DeviceController(config, **kwargs)
        return _controller_instance


def reset_device_controller() -> None:
    """Reset the global DeviceController instance."""
    global _controller_instance

    with _controller_lock:
        if _controller_instance:
            _controller_instance.disconnect()
            _controller_instance = None
