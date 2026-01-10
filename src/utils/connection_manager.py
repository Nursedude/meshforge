"""
Meshtasticd Connection Manager

Solves the problem of meshtasticd only supporting ONE TCP connection at a time.
Provides:
- Global lock to prevent concurrent connections
- Connection reuse where possible
- Graceful handling of connection conflicts
- Fallback to cached data when connection busy

Usage:
    # Context manager (recommended)
    with MeshtasticConnection() as conn:
        if conn:
            nodes = conn.nodes

    # Manual lock management
    try:
        conn = get_connection(blocking=True, timeout=5)
        # use connection
    finally:
        release_connection()

    # Non-blocking with fallback
    try:
        conn = get_connection(blocking=False)
    except ConnectionBusy:
        nodes = get_cached_nodes()  # Use cached data instead
"""
import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Union

logger = logging.getLogger(__name__)

# Cooldown between connections (meshtasticd needs time to cleanup)
CONNECTION_COOLDOWN = 1.0  # seconds

# Try to import paths helper for proper home directory resolution
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home():
        import os
        return Path(os.environ.get('SUDO_USER', os.environ.get('USER', 'root'))).home()


class ConnectionBusy(Exception):
    """Raised when connection is busy and non-blocking mode requested."""
    pass


class ConnectionError(Exception):
    """Raised when connection fails."""
    pass


class _ConnectionManager:
    """Singleton connection manager for meshtasticd."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._reset()

    def _reset(self):
        """Reset manager state. Used for testing."""
        # Connection lock - use Lock not RLock to prevent same-thread reacquisition
        self._conn_lock = threading.Lock()
        self._lock_holder: Optional[str] = None
        self._lock_time: Optional[float] = None

        # Configuration
        self._host = '127.0.0.1'
        self._port = 4403

        # Active connection
        self._connection = None
        self._last_close_time = 0.0

        # Cache paths
        home = get_real_user_home()
        self._cache_dir = home / '.local' / 'share' / 'meshforge'
        self._nodes_cache = self._cache_dir / 'nodes_cache.json'
        self._info_cache = self._cache_dir / 'device_info.json'

    def configure(self, host: str = None, port: int = None):
        """Configure connection parameters."""
        if host is not None:
            self._host = host
        if port is not None:
            self._port = port

    def get_connection_info(self) -> Dict[str, Any]:
        """Get current connection info."""
        return {
            'locked': self._conn_lock.locked() if hasattr(self._conn_lock, 'locked') else self._lock_holder is not None,
            'holder': self._lock_holder,
            'host': self._host,
            'port': self._port,
            'lock_time': self._lock_time,
        }

    def is_locked(self) -> bool:
        """Check if connection lock is held."""
        return self._lock_holder is not None

    def is_connected(self) -> bool:
        """Check if actually connected to meshtasticd."""
        if self._connection is None:
            return False
        try:
            # Check if connection is still valid
            return hasattr(self._connection, 'nodes')
        except Exception:
            return False

    def get_connection(
        self,
        blocking: bool = True,
        timeout: float = 30,
        connect: bool = False,
        caller: str = None
    ):
        """
        Acquire connection lock and optionally connect.

        Args:
            blocking: If True, wait for lock. If False, raise ConnectionBusy immediately.
            timeout: Max seconds to wait for lock (only if blocking=True)
            connect: If True, establish actual TCP connection
            caller: Identifier for debugging

        Returns:
            Connection object if connect=True, else "locked" string

        Raises:
            ConnectionBusy: If non-blocking and lock unavailable
            ConnectionError: If connect=True and connection fails
        """
        caller_id = caller or threading.current_thread().name

        acquired = self._conn_lock.acquire(blocking=blocking, timeout=timeout if blocking else -1)

        if not acquired:
            raise ConnectionBusy(f"Connection busy, held by {self._lock_holder}")

        self._lock_holder = caller_id
        self._lock_time = time.time()
        logger.debug(f"Connection lock acquired by {caller_id}")

        if connect:
            try:
                return self._establish_connection()
            except Exception as e:
                self.release_connection()
                raise ConnectionError(f"Failed to connect: {e}") from e

        return "locked"

    def _establish_connection(self, max_retries: int = 3, retry_delay: float = 1.0):
        """
        Establish actual TCP connection to meshtasticd with retries.

        Args:
            max_retries: Number of connection attempts
            retry_delay: Initial delay between retries (uses exponential backoff)
        """
        # Wait for cooldown before connecting
        self._wait_for_cooldown()

        last_error = None
        for attempt in range(max_retries):
            try:
                import meshtastic.tcp_interface
                self._connection = meshtastic.tcp_interface.TCPInterface(
                    hostname=self._host,
                    portNumber=self._port
                )
                return self._connection
            except ImportError:
                raise ConnectionError("meshtastic package not installed")
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                last_error = e
                logger.warning(f"Connection attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 1.5  # Exponential backoff
            except Exception as e:
                raise ConnectionError(f"Connection failed: {e}")

        raise ConnectionError(f"Connection failed after {max_retries} retries: {last_error}")

    def release_connection(self):
        """Release connection lock and close connection if open."""
        if self._connection is not None:
            self._safe_close(self._connection)
            self._connection = None
            self._last_close_time = time.time()

        if self._lock_holder is not None:
            logger.debug(f"Connection lock released by {self._lock_holder}")
            self._lock_holder = None
            self._lock_time = None

        try:
            self._conn_lock.release()
        except RuntimeError:
            # Lock wasn't held
            pass

    def _safe_close(self, interface) -> None:
        """Safely close interface, handling already-closed connections."""
        if interface is None:
            return
        try:
            interface.close()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            # Connection already closed by server - this is fine
            logger.debug(f"Connection already closed during cleanup: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error during interface cleanup: {e}")

    def _wait_for_cooldown(self):
        """Wait for cooldown period since last connection close."""
        elapsed = time.time() - self._last_close_time
        if elapsed < CONNECTION_COOLDOWN:
            wait_time = CONNECTION_COOLDOWN - elapsed
            logger.debug(f"Waiting {wait_time:.2f}s for meshtasticd cooldown")
            time.sleep(wait_time)

    def is_available(self, timeout: float = 2.0) -> bool:
        """Check if meshtasticd port is reachable."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((self._host, self._port))
                return True
        except (socket.error, socket.timeout, OSError):
            return False

    def get_cached_nodes(self) -> Optional[Dict]:
        """Get cached node data without requiring connection."""
        try:
            if self._nodes_cache.exists():
                with open(self._nodes_cache) as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Could not read nodes cache: {e}")
        return None

    def get_cached_info(self) -> Optional[Dict]:
        """Get cached device info without requiring connection."""
        try:
            if self._info_cache.exists():
                with open(self._info_cache) as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Could not read info cache: {e}")
        return None

    def save_to_cache(self, nodes: List[Dict] = None, info: Dict = None):
        """Save data to cache files."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            if nodes is not None:
                with open(self._nodes_cache, 'w') as f:
                    json.dump(nodes, f)
            if info is not None:
                with open(self._info_cache, 'w') as f:
                    json.dump(info, f)
        except Exception as e:
            logger.debug(f"Could not save cache: {e}")

    def get_nodes(self, use_cache_on_busy: bool = True) -> List[Dict]:
        """
        Get nodes from meshtasticd, with cache fallback.

        Args:
            use_cache_on_busy: Return cached data if connection busy

        Returns:
            List of node dicts
        """
        try:
            conn = self.get_connection(blocking=False, connect=True, caller="get_nodes")
            try:
                nodes = []
                if hasattr(conn, 'nodes') and conn.nodes:
                    for node_id, node in conn.nodes.items():
                        node_info = {'id': node_id, 'name': '', 'short': ''}
                        if hasattr(node, 'user') and node.user:
                            node_info['name'] = getattr(node.user, 'longName', '') or ''
                            node_info['short'] = getattr(node.user, 'shortName', '') or ''
                        nodes.append(node_info)
                self.save_to_cache(nodes=nodes)
                return nodes
            finally:
                self.release_connection()
        except ConnectionBusy:
            if use_cache_on_busy:
                cached = self.get_cached_nodes()
                return cached if cached else []
            raise
        except Exception as e:
            logger.warning(f"Failed to get nodes: {e}")
            if use_cache_on_busy:
                cached = self.get_cached_nodes()
                return cached if cached else []
            return []

    def get_device_info(self, use_cache_on_busy: bool = True) -> Dict:
        """
        Get device info from meshtasticd, with cache fallback.

        Args:
            use_cache_on_busy: Return cached data if connection busy

        Returns:
            Device info dict
        """
        try:
            conn = self.get_connection(blocking=False, connect=True, caller="get_device_info")
            try:
                info = {}
                if hasattr(conn, 'localNode') and conn.localNode:
                    local_node = conn.localNode
                    if hasattr(local_node, 'nodeNum'):
                        info['node_num'] = local_node.nodeNum
                    if hasattr(local_node, 'user'):
                        user = local_node.user
                        info['long_name'] = getattr(user, 'longName', '')
                        info['short_name'] = getattr(user, 'shortName', '')
                        info['node_id'] = getattr(user, 'id', '')
                        info['hardware'] = str(getattr(user, 'hwModel', ''))
                self.save_to_cache(info=info)
                return info
            finally:
                self.release_connection()
        except ConnectionBusy:
            if use_cache_on_busy:
                cached = self.get_cached_info()
                return cached if cached else {}
            raise
        except Exception as e:
            logger.warning(f"Failed to get device info: {e}")
            if use_cache_on_busy:
                cached = self.get_cached_info()
                return cached if cached else {}
            return {}


# Global instance
_manager = _ConnectionManager()


# Public API functions
def get_connection(
    blocking: bool = True,
    timeout: float = 30,
    connect: bool = False,
    caller: str = None
):
    """Acquire connection lock. See _ConnectionManager.get_connection for details."""
    return _manager.get_connection(blocking=blocking, timeout=timeout, connect=connect, caller=caller)


def release_connection():
    """Release connection lock."""
    _manager.release_connection()


def is_connected() -> bool:
    """Check if connected to meshtasticd."""
    return _manager.is_connected()


def is_locked() -> bool:
    """Check if connection lock is held."""
    return _manager.is_locked()


def get_connection_info() -> Dict[str, Any]:
    """Get connection info."""
    return _manager.get_connection_info()


def configure(host: str = None, port: int = None):
    """Configure connection parameters."""
    _manager.configure(host=host, port=port)


def get_cached_nodes() -> Optional[Dict]:
    """Get cached node data."""
    return _manager.get_cached_nodes()


def get_cached_info() -> Optional[Dict]:
    """Get cached device info."""
    return _manager.get_cached_info()


def reset():
    """Reset manager state. Used for testing."""
    _manager._reset()


def is_available(timeout: float = 2.0) -> bool:
    """Check if meshtasticd port is reachable."""
    return _manager.is_available(timeout=timeout)


def get_nodes(use_cache_on_busy: bool = True) -> List[Dict]:
    """Get nodes from meshtasticd with cache fallback."""
    return _manager.get_nodes(use_cache_on_busy=use_cache_on_busy)


def get_device_info(use_cache_on_busy: bool = True) -> Dict:
    """Get device info from meshtasticd with cache fallback."""
    return _manager.get_device_info(use_cache_on_busy=use_cache_on_busy)


class MeshtasticConnection:
    """
    Context manager for meshtasticd connection.

    Usage:
        with MeshtasticConnection() as conn:
            if conn:
                nodes = conn.nodes

        # With actual connection
        with MeshtasticConnection(connect=True) as conn:
            print(conn.myInfo)
    """

    def __init__(
        self,
        connect: bool = False,
        blocking: bool = True,
        timeout: float = 30,
        caller: str = None
    ):
        self.connect = connect
        self.blocking = blocking
        self.timeout = timeout
        self.caller = caller
        self._connection = None

    def __enter__(self):
        result = get_connection(
            blocking=self.blocking,
            timeout=self.timeout,
            connect=self.connect,
            caller=self.caller
        )
        if self.connect:
            self._connection = result
            return result
        return None

    def __exit__(self, exc_type, exc_val, exc_tb):
        release_connection()
        return False  # Don't suppress exceptions
