"""
Meshtastic Connection Manager

Provides resilient connection handling for Meshtastic radios via:
- TCP: Connect to meshtasticd (port 4403) - for SPI radios or when daemon is running
- Serial: Connect directly to USB radio - MeshForge owns the connection

Features:
- Connection locking to prevent concurrent access
- Retry logic for transient failures (Connection reset by peer)
- Safe connection cleanup that handles already-closed connections
- Cooldown period between connections to prevent rapid reconnect issues
- Timeout handling
- Auto-detection of USB devices
"""

import socket
import subprocess
import sys
import threading
import time
import logging
from contextlib import contextmanager
from enum import Enum
from typing import Optional, List, Dict, Any

from utils.safe_import import safe_import
from utils.service_check import restart_service

logger = logging.getLogger(__name__)

# Optional: meshtastic connection interfaces
_meshtastic_tcp, _HAS_TCP = safe_import('meshtastic.tcp_interface')
_meshtastic_serial, _HAS_SERIAL = safe_import('meshtastic.serial_interface')


def _install_meshtastic_thread_guard():
    """Install a threading excepthook to suppress known meshtastic library crashes.

    The meshtastic library runs a heartbeat timer in a background thread that calls
    sendHeartbeat() -> _sendToRadio() -> socket.send(). If the TCP connection to
    meshtasticd drops (e.g. service restart, network issue), the socket.send() raises
    BrokenPipeError in that timer thread. Since MeshForge doesn't own that thread,
    we can't wrap it in try/except. Instead, we install a threading.excepthook to
    catch and log these crashes instead of printing the traceback.
    """
    _original_excepthook = getattr(threading, 'excepthook', None)

    def _meshtastic_excepthook(args):
        # args: ExceptHookArgs(exc_type, exc_value, exc_traceback, thread)
        exc_type = args.exc_type
        exc_value = args.exc_value
        thread = args.thread

        # Suppress known meshtastic library BrokenPipeError from heartbeat timer
        if exc_type in (BrokenPipeError, ConnectionResetError, OSError):
            thread_name = getattr(thread, 'name', '') if thread else ''
            logger.warning(
                f"meshtasticd TCP connection lost (heartbeat failed): {exc_value}"
            )
            return

        # For other exceptions, call the original hook or log
        if _original_excepthook:
            _original_excepthook(args)
        else:
            logger.error(
                f"Unhandled exception in thread {getattr(thread, 'name', '?')}: "
                f"{exc_type.__name__}: {exc_value}"
            )

    threading.excepthook = _meshtastic_excepthook


# Install the guard on module import
_install_meshtastic_thread_guard()


class ConnectionMode(Enum):
    """Connection mode for Meshtastic radios."""
    TCP = "tcp"          # Connect to meshtasticd (port 4403)
    SERIAL = "serial"    # Direct USB serial connection
    AUTO = "auto"        # Auto-detect: try serial first, fall back to TCP

# Singleton instance
_connection_manager: Optional['MeshtasticConnectionManager'] = None
_manager_lock = threading.Lock()

# GLOBAL connection lock - meshtasticd only supports ONE TCP connection
# All code that connects to meshtasticd MUST acquire this lock first
MESHTASTIC_CONNECTION_LOCK = threading.Lock()

# Cooldown between connections (meshtasticd needs time to cleanup)
CONNECTION_COOLDOWN = 1.0  # seconds (increased from 0.5)

# Track last connection close time globally
_last_global_close_time = 0.0


class ConnectionError(Exception):
    """Exception raised when connection to meshtasticd fails"""
    pass


def get_connection_manager(
    host: str = 'localhost',
    port: int = 4403,
    mode: ConnectionMode = ConnectionMode.AUTO,
    serial_port: Optional[str] = None
) -> 'MeshtasticConnectionManager':
    """
    Get the singleton connection manager instance.

    Args:
        host: meshtasticd host for TCP mode (default: localhost)
        port: meshtasticd TCP port (default: 4403)
        mode: Connection mode - TCP, SERIAL, or AUTO
        serial_port: USB serial port for SERIAL mode (e.g., /dev/ttyUSB0)
                     If None, will auto-detect

    Returns:
        MeshtasticConnectionManager singleton instance
    """
    global _connection_manager
    with _manager_lock:
        if _connection_manager is None:
            _connection_manager = MeshtasticConnectionManager(
                host=host,
                port=port,
                mode=mode,
                serial_port=serial_port
            )
        return _connection_manager


def reset_connection_manager():
    """Reset the singleton (for testing)"""
    global _connection_manager
    with _manager_lock:
        _connection_manager = None


def wait_for_cooldown():
    """Wait for the global connection cooldown period"""
    global _last_global_close_time
    elapsed = time.time() - _last_global_close_time
    if elapsed < CONNECTION_COOLDOWN:
        wait_time = CONNECTION_COOLDOWN - elapsed
        logger.debug(f"Waiting {wait_time:.2f}s for meshtasticd cooldown")
        time.sleep(wait_time)


def clear_stale_connections(port: int = 4403) -> bool:
    """
    Detect and clear CLOSE-WAIT zombie connections on the meshtasticd port.

    meshtasticd only allows ONE TCP client. If a previous connection wasn't
    cleaned up properly, it lingers in CLOSE-WAIT state on meshtasticd's side,
    blocking all new connections. This function detects that state and restarts
    meshtasticd to clear it.

    Args:
        port: meshtasticd TCP port (default 4403)

    Returns:
        True if a stale connection was found and meshtasticd was restarted,
        False if no action was needed or restart failed.
    """
    try:
        # Use ss to check for CLOSE-WAIT connections on the meshtasticd port.
        # CLOSE-WAIT means the remote side (our client) closed, but meshtasticd
        # hasn't released the socket — it's stuck.
        result = subprocess.run(
            ['ss', '-tn', 'state', 'close-wait', f'sport = :{port}'],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout.strip()

        # ss output has a header line; actual connections appear on subsequent lines
        lines = [l for l in output.split('\n') if l.strip() and not l.startswith('Recv-Q')]
        if not lines:
            return False

        logger.warning(
            f"Detected {len(lines)} CLOSE-WAIT zombie connection(s) on port {port}, "
            f"restarting meshtasticd to clear"
        )

        # Restart meshtasticd to clear the zombie — uses service_check helpers
        success, msg = restart_service('meshtasticd')
        if success:
            logger.info("meshtasticd restarted successfully, zombie connections cleared")
            # Give meshtasticd time to fully start and open its TCP port
            time.sleep(3)
            return True
        else:
            logger.error("Failed to restart meshtasticd: %s", msg)
            return False

    except FileNotFoundError:
        logger.debug("ss command not available, cannot check for stale connections")
        return False
    except subprocess.TimeoutExpired:
        logger.debug("Timed out checking for stale connections")
        return False
    except Exception as e:
        logger.debug(f"Error checking for stale connections: {e}")
        return False


def safe_close_interface(interface) -> None:
    """
    Safely close a meshtastic interface, handling already-closed connections.

    The meshtastic library can raise BrokenPipeError or ConnectionResetError
    when trying to send the disconnect message if the connection is already gone.

    CRITICAL: After interface.close(), we must also force-close the underlying
    TCP socket. The meshtastic library's close() tries to send a disconnect
    message — if the pipe is broken, it catches the error but may NOT close
    the raw socket. This leaves meshtasticd with a CLOSE-WAIT zombie connection
    that blocks all new TCP clients (meshtasticd only allows one).
    """
    global _last_global_close_time

    if interface is None:
        return

    try:
        # Try to close normally
        interface.close()
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        # Connection already closed by server - this is fine
        logger.debug(f"Connection already closed during cleanup: {e}")
    except Exception as e:
        # Log other errors but don't raise
        logger.warning(f"Unexpected error during interface cleanup: {e}")
    finally:
        # Force-close the underlying TCP socket to prevent CLOSE-WAIT zombies.
        # meshtasticd only allows ONE TCP client — a lingering CLOSE-WAIT socket
        # blocks reconnection indefinitely until meshtasticd is restarted.
        _force_close_socket(interface)
        # Always update global close time
        _last_global_close_time = time.time()


def _force_close_socket(interface) -> None:
    """
    Force-close the underlying TCP socket on a meshtastic interface.

    The meshtastic library's TCPInterface stores the socket as _socket
    (inherited from StreamInterface). If interface.close() failed to
    properly tear down the TCP connection, this sends RST to the peer
    so meshtasticd immediately frees the client slot.
    """
    raw_socket = getattr(interface, '_socket', None)
    if raw_socket is None:
        return

    try:
        # shutdown() sends RST if there's pending data, ensuring
        # meshtasticd sees the connection as fully closed
        raw_socket.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # Already closed or not connected

    try:
        raw_socket.close()
    except OSError:
        pass  # Already closed


class MeshtasticConnectionManager:
    """
    Manages connections to Meshtastic radios.

    Supports two modes:
    - TCP: Connect to meshtasticd (for SPI radios or when daemon is preferred)
    - Serial: Direct USB connection (MeshForge owns the radio)

    Features:
    - Uses a lock to prevent concurrent connection attempts
    - Retries on transient failures like "Connection reset by peer"
    - Adds cooldown between connections
    - Provides convenience methods for common operations
    """

    def __init__(
        self,
        host: str = 'localhost',
        port: int = 4403,
        mode: ConnectionMode = ConnectionMode.AUTO,
        serial_port: Optional[str] = None
    ):
        """
        Initialize the connection manager.

        Args:
            host: meshtasticd host for TCP mode (default: localhost)
            port: meshtasticd TCP port (default: 4403)
            mode: Connection mode - TCP, SERIAL, or AUTO
            serial_port: USB serial port for SERIAL mode (auto-detected if None)
        """
        self.host = host
        self.port = port
        self.mode = mode
        self.serial_port = serial_port
        self._resolved_mode: Optional[ConnectionMode] = None  # Actual mode after AUTO resolution
        self._lock = threading.Lock()
        self._interface = None
        self._last_close_time = 0.0
        # Persistent connection for long-running services (like gateway bridge)
        self._persistent_interface = None
        self._persistent_owner: Optional[str] = None

    def is_available(self, timeout: float = 2.0) -> bool:
        """
        Check if meshtasticd is reachable.

        Args:
            timeout: Connection timeout in seconds

        Returns:
            True if port is reachable, False otherwise
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((self.host, self.port))
                return True
        except (socket.error, socket.timeout, OSError):
            return False

    def acquire_lock(self, timeout: float = 30.0) -> bool:
        """
        Acquire the connection lock.

        Args:
            timeout: How long to wait for lock (seconds)

        Returns:
            True if lock acquired, False if timeout
        """
        return self._lock.acquire(timeout=timeout)

    def release_lock(self):
        """Release the connection lock"""
        try:
            self._lock.release()
        except RuntimeError:
            pass  # Lock not held

    def acquire_persistent(self, owner: str = "bridge") -> bool:
        """
        Acquire a persistent connection for long-running services.

        The persistent connection stays open until release_persistent() is called.
        While a persistent connection exists, other code should use get_interface()
        to access it instead of creating new connections.

        Args:
            owner: Identifier for the owner (for debugging)

        Returns:
            True if persistent connection acquired, False if failed
        """
        if not self.acquire_lock(timeout=10.0):
            logger.warning(f"Could not acquire lock for persistent connection (owner={owner})")
            return False

        try:
            if self._persistent_interface is not None:
                logger.warning(f"Persistent connection already held by {self._persistent_owner}")
                return False

            self._wait_for_cooldown()
            self._persistent_interface = self._create_interface()
            self._persistent_owner = owner
            logger.info(f"Persistent connection acquired by {owner}")
            return True
        except Exception as e:
            logger.error(f"Failed to create persistent connection: {e}")
            return False
        finally:
            self.release_lock()

    def release_persistent(self):
        """
        Release the persistent connection.

        Safe to call even if no persistent connection exists.
        """
        if self._persistent_interface is not None:
            owner = self._persistent_owner
            try:
                safe_close_interface(self._persistent_interface)
            except Exception as e:
                logger.debug(f"Error closing persistent interface: {e}")
            self._persistent_interface = None
            self._persistent_owner = None
            self._last_close_time = time.time()
            logger.info(f"Persistent connection released (was owned by {owner})")

    def get_interface(self):
        """
        Get the current interface (persistent if available).

        Returns:
            The persistent interface if one exists, None otherwise.
            Other code should use this to check for an existing connection
            before creating a new one.
        """
        return self._persistent_interface

    def has_persistent(self) -> bool:
        """Check if a persistent connection exists."""
        return self._persistent_interface is not None

    def get_persistent_owner(self) -> Optional[str]:
        """Get the owner of the persistent connection, if any."""
        return self._persistent_owner

    def _detect_usb_device(self) -> Optional[str]:
        """
        Auto-detect USB serial device for Meshtastic radio.

        Returns:
            Device path (e.g., /dev/ttyUSB0) or None if not found
        """
        import glob
        # Common Meshtastic USB device patterns
        patterns = ['/dev/ttyUSB*', '/dev/ttyACM*']
        for pattern in patterns:
            devices = glob.glob(pattern)
            if devices:
                # Return first available device
                device = sorted(devices)[0]
                logger.debug(f"Auto-detected USB device: {device}")
                return device
        return None

    def _resolve_mode(self) -> ConnectionMode:
        """
        Resolve AUTO mode to actual connection mode.

        AUTO mode priority:
        1. TCP if meshtasticd is available (preferred - daemon manages the radio)
        2. SERIAL if USB device detected and TCP unavailable

        Returns:
            Resolved ConnectionMode (TCP or SERIAL)
        """
        if self.mode != ConnectionMode.AUTO:
            return self.mode

        # AUTO mode: prefer TCP if meshtasticd is running
        # This avoids conflicts when meshtasticd holds the serial port
        if self.is_available():
            logger.info("AUTO mode: meshtasticd available on TCP, using TCP mode")
            return ConnectionMode.TCP

        # Fall back to serial if USB device available
        if self.serial_port or self._detect_usb_device():
            logger.info("AUTO mode: no meshtasticd, USB device detected, using SERIAL mode")
            return ConnectionMode.SERIAL

        # Default to TCP (will fail with clear message if no meshtasticd)
        logger.info("AUTO mode: no USB device, defaulting to TCP mode")
        return ConnectionMode.TCP

    def _create_interface(self):
        """
        Create a new meshtastic interface (TCP or Serial).

        Returns:
            TCPInterface or SerialInterface instance

        Raises:
            ConnectionError: If connection fails
        """
        # Resolve mode if AUTO
        resolved = self._resolve_mode()
        self._resolved_mode = resolved

        if resolved == ConnectionMode.SERIAL:
            return self._create_serial_interface()
        else:
            return self._create_tcp_interface()

    def _create_tcp_interface(self):
        """Create TCP interface to meshtasticd."""
        if not _HAS_TCP:
            raise ConnectionError("meshtastic library not installed")
        try:
            logger.debug(f"Creating TCP interface to {self.host}:{self.port}")
            return _meshtastic_tcp.TCPInterface(hostname=self.host)
        except Exception as e:
            raise ConnectionError(f"TCP connection failed: {e}")

    def _create_serial_interface(self):
        """Create direct serial interface to USB radio."""
        if not _HAS_SERIAL:
            raise ConnectionError("meshtastic library not installed")
        try:
            device = self.serial_port or self._detect_usb_device()
            if not device:
                raise ConnectionError(
                    "No USB device found. Connect a Meshtastic radio or specify serial_port."
                )
            logger.debug(f"Creating Serial interface to {device}")
            return _meshtastic_serial.SerialInterface(devPath=device)
        except Exception as e:
            raise ConnectionError(f"Serial connection failed: {e}")

    def get_mode(self) -> str:
        """Get the current connection mode (after resolution)."""
        if self._resolved_mode:
            return self._resolved_mode.value
        return self.mode.value

    def _wait_for_cooldown(self):
        """Wait for cooldown period since last connection close"""
        elapsed = time.time() - self._last_close_time
        if elapsed < CONNECTION_COOLDOWN:
            wait_time = CONNECTION_COOLDOWN - elapsed
            logger.debug(f"Waiting {wait_time:.2f}s for connection cooldown")
            time.sleep(wait_time)

    @contextmanager
    def with_connection(self, max_retries: int = 3, retry_delay: float = 1.0, lock_timeout: float = 30.0):
        """
        Context manager for safe connection handling.

        Acquires lock, creates connection, and ensures cleanup.
        Retries on transient connection failures.
        Includes cooldown to prevent rapid reconnection issues.

        Args:
            max_retries: Maximum number of connection attempts
            retry_delay: Delay between retries (seconds)
            lock_timeout: How long to wait for connection lock

        Yields:
            TCPInterface instance

        Raises:
            ConnectionError: If connection fails after all retries
        """
        if not self.acquire_lock(timeout=lock_timeout):
            raise ConnectionError("Could not acquire connection lock (another operation in progress)")

        interface = None
        last_error = None

        try:
            # Wait for cooldown before connecting
            self._wait_for_cooldown()

            for attempt in range(max_retries):
                try:
                    interface = self._create_interface()
                    yield interface
                    return
                except (ConnectionResetError, BrokenPipeError, OSError) as e:
                    last_error = e
                    logger.warning(f"Connection attempt {attempt + 1}/{max_retries} failed: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        # Exponential backoff
                        retry_delay *= 1.5
                except ConnectionError:
                    raise
                finally:
                    if interface is not None:
                        # Use safe close to handle already-closed connections
                        safe_close_interface(interface)
                        self._last_close_time = time.time()
                        interface = None

            raise ConnectionError(f"Connection failed after max retries: {last_error}")

        finally:
            self.release_lock()

    def close(self):
        """
        Close any active connection and cleanup resources.

        Safe to call multiple times.
        """
        try:
            if self._interface is not None:
                safe_close_interface(self._interface)
                self._interface = None
                self._last_close_time = time.time()
        except Exception as e:
            logger.debug(f"Error during connection manager close: {e}")

    def get_nodes(self, max_retries: int = 2) -> List[Dict[str, Any]]:
        """
        Get list of nodes from meshtasticd.

        Args:
            max_retries: Number of connection retries

        Returns:
            List of node dictionaries, empty list on error
        """
        try:
            with self.with_connection(max_retries=max_retries) as iface:
                nodes = []
                if hasattr(iface, 'nodes') and iface.nodes:
                    for node_id, node in iface.nodes.items():
                        node_info = {
                            'id': node_id,
                            'name': '',
                            'short': '',
                        }
                        if hasattr(node, 'user') and node.user:
                            node_info['name'] = getattr(node.user, 'longName', '') or ''
                            node_info['short'] = getattr(node.user, 'shortName', '') or ''
                            if hasattr(node.user, 'id'):
                                node_info['id'] = node.user.id
                        nodes.append(node_info)
                return nodes
        except Exception as e:
            logger.warning(f"Failed to get nodes: {e}")
            return []

    def get_channels(self, max_retries: int = 2) -> List[Dict[str, Any]]:
        """
        Get list of channels from meshtasticd.

        Args:
            max_retries: Number of connection retries

        Returns:
            List of channel dictionaries, empty list on error
        """
        try:
            with self.with_connection(max_retries=max_retries) as iface:
                channels = []
                if hasattr(iface, 'localNode') and iface.localNode:
                    local_node = iface.localNode
                    if hasattr(local_node, 'channels'):
                        for idx, ch in enumerate(local_node.channels):
                            channel_info = {
                                'index': idx,
                                'role': 'DISABLED',
                                'name': '',
                                'psk': False
                            }

                            if hasattr(ch, 'role'):
                                role_map = {0: 'DISABLED', 1: 'PRIMARY', 2: 'SECONDARY'}
                                try:
                                    role_int = int(ch.role)
                                    channel_info['role'] = role_map.get(role_int, str(role_int))
                                except (ValueError, TypeError):
                                    channel_info['role'] = str(ch.role)

                            if hasattr(ch, 'settings'):
                                settings = ch.settings
                                if hasattr(settings, 'name'):
                                    channel_info['name'] = settings.name or f"Channel {idx}"
                                if hasattr(settings, 'psk'):
                                    channel_info['psk'] = bool(settings.psk)

                            channels.append(channel_info)
                return channels
        except Exception as e:
            logger.warning(f"Failed to get channels: {e}")
            return []

    def get_radio_info(self, max_retries: int = 2) -> Dict[str, Any]:
        """
        Get radio information from meshtasticd.

        Args:
            max_retries: Number of connection retries

        Returns:
            Dictionary with radio info, or error dict on failure
        """
        try:
            with self.with_connection(max_retries=max_retries) as iface:
                info = {}
                if hasattr(iface, 'localNode') and iface.localNode:
                    local_node = iface.localNode
                    if hasattr(local_node, 'nodeNum'):
                        info['node_num'] = local_node.nodeNum
                    if hasattr(local_node, 'user'):
                        user = local_node.user
                        if hasattr(user, 'longName'):
                            info['long_name'] = user.longName
                        if hasattr(user, 'shortName'):
                            info['short_name'] = user.shortName
                        if hasattr(user, 'id'):
                            info['node_id'] = user.id
                        if hasattr(user, 'hwModel'):
                            info['hardware'] = str(user.hwModel)
                return info
        except Exception as e:
            logger.warning(f"Failed to get radio info: {e}")
            return {'error': str(e)}

    def send_message(self, text: str, destination: str = '^all', max_retries: int = 2) -> bool:
        """
        Send a message via meshtasticd.

        Args:
            text: Message text
            destination: Destination node ID or ^all for broadcast
            max_retries: Number of connection retries

        Returns:
            True if sent successfully, False on error
        """
        try:
            with self.with_connection(max_retries=max_retries) as iface:
                if destination == '^all':
                    iface.sendText(text)
                else:
                    iface.sendText(text, destinationId=destination)
                return True
        except Exception as e:
            logger.warning(f"Failed to send message: {e}")
            return False
