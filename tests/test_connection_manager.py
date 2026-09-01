"""
TDD Tests for Meshtasticd Connection Manager

Problem: meshtasticd only supports ONE TCP connection at a time.
Multiple MeshForge components fighting for connection causes:
- BrokenPipeError: [Errno 32] Broken pipe
- Connection reset by peer [Errno 104]
- "Force close previous TCP connection" logs

Solution: Centralized connection manager with:
- Global lock to prevent concurrent connections
- Connection reuse where possible
- Graceful handling of connection conflicts
"""
import pytest
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


@pytest.fixture(autouse=True)
def reset_connection_manager():
    """Reset connection manager state before and after each test."""
    try:
        from utils.connection_manager import reset
        reset()
    except ImportError:
        pass
    yield
    try:
        from utils.connection_manager import reset
        reset()
    except ImportError:
        pass


class TestConnectionManagerExists:
    """Test that the connection manager module exists and has required interface."""

    def test_module_exists(self):
        """Connection manager module should exist."""
        from utils import connection_manager
        assert connection_manager is not None

    def test_has_get_connection_function(self):
        """Should have get_connection() function."""
        from utils.connection_manager import get_connection
        assert callable(get_connection)

    def test_has_release_connection_function(self):
        """Should have release_connection() function."""
        from utils.connection_manager import release_connection
        assert callable(release_connection)

    def test_has_is_connected_function(self):
        """Should have is_connected() function."""
        from utils.connection_manager import is_connected
        assert callable(is_connected)

    def test_has_connection_context_manager(self):
        """Should have MeshtasticConnection context manager."""
        from utils.connection_manager import MeshtasticConnection
        assert MeshtasticConnection is not None


class TestConnectionLocking:
    """Test that only one connection is allowed at a time."""

    def test_lock_prevents_concurrent_connections(self):
        """Second connection attempt should wait or fail while first holds lock."""
        from utils.connection_manager import get_connection, release_connection, ConnectionBusy

        # First connection should succeed
        conn1 = get_connection(timeout=1, blocking=False)
        assert conn1 is not None or conn1 == "locked"  # Either connected or got lock

        # Second non-blocking attempt should raise ConnectionBusy
        with pytest.raises(ConnectionBusy):
            get_connection(timeout=0, blocking=False)

        # Release first connection
        release_connection()

    def test_blocking_connection_waits(self):
        """Blocking connection should wait for lock to be released."""
        from utils.connection_manager import get_connection, release_connection

        results = []

        def first_connection():
            get_connection(blocking=False)
            results.append("first_acquired")
            time.sleep(0.2)
            release_connection()
            results.append("first_released")

        def second_connection():
            time.sleep(0.05)  # Ensure first starts first
            get_connection(blocking=True, timeout=1)
            results.append("second_acquired")
            release_connection()

        t1 = threading.Thread(target=first_connection)
        t2 = threading.Thread(target=second_connection)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Second should acquire after first releases
        assert results.index("second_acquired") > results.index("first_released")


class TestContextManager:
    """Test the context manager interface."""

    def test_context_manager_acquires_and_releases(self):
        """Context manager should acquire on enter and release on exit."""
        from utils.connection_manager import MeshtasticConnection, is_locked

        assert not is_locked()

        with MeshtasticConnection() as conn:
            assert is_locked()

        assert not is_locked()

    def test_context_manager_releases_on_exception(self):
        """Lock should be released even if exception occurs."""
        from utils.connection_manager import MeshtasticConnection, is_locked

        try:
            with MeshtasticConnection() as conn:
                assert is_locked()
                raise ValueError("Test exception")
        except ValueError:
            pass

        assert not is_locked()


class TestConnectionInfo:
    """Test connection status and info functions."""

    def test_is_connected_false_initially(self):
        """Should report not connected when no connection exists."""
        from utils.connection_manager import is_connected, release_connection
        release_connection()  # Ensure clean state
        # Note: is_connected checks actual TCP connection, not just lock
        result = is_connected()
        assert isinstance(result, bool)

    def test_get_connection_info(self):
        """Should return connection info dict."""
        from utils.connection_manager import get_connection_info

        info = get_connection_info()
        assert isinstance(info, dict)
        assert 'locked' in info
        assert 'holder' in info
        assert 'host' in info
        assert 'port' in info


class TestConnectionConfig:
    """Test connection configuration."""

    def test_default_host_and_port(self):
        """Should use localhost:4403 by default."""
        from utils.connection_manager import get_connection_info

        info = get_connection_info()
        assert info['host'] == '127.0.0.1'
        assert info['port'] == 4403

    def test_custom_host_and_port(self):
        """Should allow custom host and port."""
        from utils.connection_manager import configure, get_connection_info

        configure(host='192.168.1.100', port=4404)
        info = get_connection_info()
        assert info['host'] == '192.168.1.100'
        assert info['port'] == 4404

        # Reset to default
        configure(host='127.0.0.1', port=4403)


class TestCachedDataFallback:
    """Test fallback to cached data when connection unavailable."""

    def test_get_cached_nodes_when_busy(self):
        """Should return cached node data when connection is busy."""
        from utils.connection_manager import get_cached_nodes

        # Should return dict (possibly empty) without requiring connection
        nodes = get_cached_nodes()
        assert isinstance(nodes, (dict, list, type(None)))

    def test_get_cached_info_when_busy(self):
        """Should return cached device info when connection is busy."""
        from utils.connection_manager import get_cached_info

        info = get_cached_info()
        assert isinstance(info, (dict, type(None)))


class TestErrorHandling:
    """Test error handling for connection issues."""

    def test_connection_refused_handled(self):
        """Should handle connection refused gracefully."""
        from utils.connection_manager import MeshtasticConnection, ConnectionError as ConnErr

        # Configure to non-existent port
        from utils.connection_manager import configure
        configure(host='127.0.0.1', port=59999)  # Unlikely to be listening

        with pytest.raises((ConnErr, ConnectionRefusedError, OSError)):
            with MeshtasticConnection(connect=True) as conn:
                pass

        # Reset
        configure(host='127.0.0.1', port=4403)

    def test_timeout_handled(self):
        """Should handle connection timeout gracefully."""
        from utils.connection_manager import get_connection, ConnectionBusy, release_connection

        # Hold the lock
        get_connection(blocking=False)

        # Attempt with short timeout should raise
        with pytest.raises(ConnectionBusy):
            get_connection(timeout=0.1, blocking=True)

        release_connection()


class TestFirstHeartbeatGrace:
    """2026-09-01: the library sets isConnected BEFORE it sends the first
    heartbeat, so a short-lived connection that closes on the wake-up shuts
    the socket under that write ('Socket send error ... Broken pipe' + a
    traceback, 166/day on the manager's map). _safe_close now waits, bounded,
    for the library's own `meshtastic.connection.established` message for
    THAT interface — the honest signal the heartbeat write has returned."""

    class _Iface:
        def __init__(self, connected=True):
            self.isConnected = threading.Event()
            if connected:
                self.isConnected.set()
            self.closed_at = None

        def close(self):
            self.closed_at = time.monotonic()

    def test_close_waits_for_the_established_message(self):
        from pubsub import pub
        from utils.connection_manager import _manager
        iface = self._Iface()
        delivered = {}

        def deliver():
            delivered["at"] = time.monotonic()
            pub.sendMessage("meshtastic.connection.established", interface=iface)

        threading.Timer(0.3, deliver).start()
        t0 = time.monotonic()
        _manager._safe_close(iface)
        assert iface.closed_at is not None
        assert iface.closed_at >= delivered["at"]           # closed AFTER delivery
        assert iface.closed_at - t0 < 1.5                    # and not on the timeout

    def test_close_is_immediate_when_already_established(self):
        from pubsub import pub
        from utils.connection_manager import _manager
        iface = self._Iface()
        pub.sendMessage("meshtastic.connection.established", interface=iface)
        t0 = time.monotonic()
        _manager._safe_close(iface)
        assert iface.closed_at - t0 < 0.2

    def test_close_is_immediate_when_never_connected(self):
        from utils.connection_manager import _manager
        iface = self._Iface(connected=False)
        t0 = time.monotonic()
        _manager._safe_close(iface)
        assert iface.closed_at - t0 < 0.2

    def test_grace_is_bounded_when_the_message_never_comes(self, monkeypatch):
        import utils.connection_manager as cm
        monkeypatch.setattr(cm, "FIRST_HEARTBEAT_GRACE_S", 0.25)
        iface = self._Iface()
        t0 = time.monotonic()
        cm._manager._safe_close(iface)
        assert 0.2 <= iface.closed_at - t0 < 1.0             # waited the grace, then closed

    def test_mocks_never_wait(self):
        # a MagicMock has every attribute; the isinstance(Event) guard keeps
        # the whole mocked suite from paying the grace on every close
        from utils.connection_manager import _manager
        m = MagicMock()
        t0 = time.monotonic()
        _manager._safe_close(m)
        assert time.monotonic() - t0 < 0.2 and m.close.called
