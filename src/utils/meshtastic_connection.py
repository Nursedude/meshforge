"""
Meshtastic TCP Connection Manager

Provides resilient connection handling for meshtasticd which only supports
one TCP client connection at a time. Features:
- Connection locking to prevent concurrent access
- Retry logic for transient failures (Connection reset by peer)
- Safe connection cleanup that handles already-closed connections
- Cooldown period between connections to prevent rapid reconnect issues
- Timeout handling
"""

import socket
import threading
import time
import logging
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Singleton instance
_connection_manager: Optional['MeshtasticConnectionManager'] = None
_manager_lock = threading.Lock()

# Cooldown between connections (meshtasticd needs time to cleanup)
CONNECTION_COOLDOWN = 0.5  # seconds


class ConnectionError(Exception):
    """Exception raised when connection to meshtasticd fails"""
    pass


def get_connection_manager(host: str = 'localhost', port: int = 4403) -> 'MeshtasticConnectionManager':
    """Get the singleton connection manager instance"""
    global _connection_manager
    with _manager_lock:
        if _connection_manager is None:
            _connection_manager = MeshtasticConnectionManager(host, port)
        return _connection_manager


def reset_connection_manager():
    """Reset the singleton (for testing)"""
    global _connection_manager
    with _manager_lock:
        _connection_manager = None


def safe_close_interface(interface) -> None:
    """
    Safely close a meshtastic interface, handling already-closed connections.

    The meshtastic library can raise BrokenPipeError or ConnectionResetError
    when trying to send the disconnect message if the connection is already gone.
    """
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


class MeshtasticConnectionManager:
    """
    Manages TCP connections to meshtasticd.

    meshtasticd only supports one TCP client at a time, so this manager:
    - Uses a lock to prevent concurrent connection attempts
    - Retries on transient failures like "Connection reset by peer"
    - Adds cooldown between connections to let meshtasticd cleanup
    - Provides convenience methods for common operations
    """

    def __init__(self, host: str = 'localhost', port: int = 4403):
        """
        Initialize the connection manager.

        Args:
            host: meshtasticd host (default: localhost)
            port: meshtasticd TCP port (default: 4403)
        """
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._interface = None
        self._last_close_time = 0.0

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

    def _create_interface(self):
        """
        Create a new meshtastic TCP interface.

        Returns:
            TCPInterface instance

        Raises:
            ConnectionError: If connection fails
        """
        try:
            import meshtastic.tcp_interface
            return meshtastic.tcp_interface.TCPInterface(hostname=self.host)
        except ImportError:
            raise ConnectionError("meshtastic library not installed")
        except Exception as e:
            raise ConnectionError(f"Failed to connect: {e}")

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
