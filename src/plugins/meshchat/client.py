"""
MeshChat HTTP Client

Provides Python interface to MeshChat's HTTP/WebSocket API.
All network operations have timeouts and proper error handling.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class MeshChatError(Exception):
    """Base exception for MeshChat client errors."""
    pass


class MeshChatConnectionError(MeshChatError):
    """Failed to connect to MeshChat service."""
    pass


class MeshChatAPIError(MeshChatError):
    """MeshChat API returned an error."""
    pass


@dataclass
class MeshChatPeer:
    """Represents a peer discovered by MeshChat."""
    destination_hash: str
    display_name: Optional[str] = None
    last_announce: Optional[datetime] = None
    is_online: bool = False
    app_data: Optional[Dict[str, Any]] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> 'MeshChatPeer':
        """Create peer from API response."""
        last_announce = None
        if data.get('last_announce'):
            try:
                last_announce = datetime.fromisoformat(data['last_announce'])
            except (ValueError, TypeError):
                pass

        return cls(
            destination_hash=data.get('destination_hash', data.get('hash', '')),
            display_name=data.get('display_name', data.get('name')),
            last_announce=last_announce,
            is_online=data.get('is_online', False),
            app_data=data.get('app_data')
        )


@dataclass
class MeshChatMessage:
    """Represents an LXMF message."""
    message_id: str
    source_hash: str
    destination_hash: str
    content: str
    timestamp: datetime
    is_incoming: bool
    delivered: bool = False
    read: bool = False

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> 'MeshChatMessage':
        """Create message from API response."""
        timestamp = datetime.now()
        if data.get('timestamp'):
            try:
                timestamp = datetime.fromisoformat(data['timestamp'])
            except (ValueError, TypeError):
                pass

        return cls(
            message_id=data.get('id', ''),
            source_hash=data.get('source_hash', data.get('from', '')),
            destination_hash=data.get('destination_hash', data.get('to', '')),
            content=data.get('content', data.get('message', '')),
            timestamp=timestamp,
            is_incoming=data.get('is_incoming', data.get('incoming', False)),
            delivered=data.get('delivered', False),
            read=data.get('read', False)
        )


@dataclass
class MeshChatStatus:
    """MeshChat service status."""
    version: Optional[str] = None
    identity_hash: Optional[str] = None
    display_name: Optional[str] = None
    peer_count: int = 0
    message_count: int = 0
    propagation_node: bool = False
    rns_connected: bool = False
    uptime_seconds: int = 0


class MeshChatClient:
    """
    HTTP client for MeshChat API.

    MeshChat exposes a REST API and WebSocket for real-time updates.
    This client provides read/write access to peers, messages, and status.

    Usage:
        client = MeshChatClient()
        if client.is_available():
            peers = client.get_peers()
            client.send_message(destination_hash, "Hello from MeshForge!")
    """

    DEFAULT_HOST = '127.0.0.1'
    DEFAULT_PORT = 8000
    DEFAULT_TIMEOUT = 5  # seconds

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._base_url = f"http://{host}:{port}"
        self._session = None

    def _get_session(self):
        """Get or create requests session (lazy import)."""
        if self._session is None:
            try:
                import requests
                self._session = requests.Session()
                self._session.headers.update({
                    'User-Agent': 'MeshForge/1.0',
                    'Accept': 'application/json'
                })
            except ImportError:
                raise MeshChatError("requests library not installed")
        return self._session

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to MeshChat API."""
        url = urljoin(self._base_url, endpoint)
        session = self._get_session()

        try:
            if method.upper() == 'GET':
                response = session.get(url, params=params, timeout=self.timeout)
            elif method.upper() == 'POST':
                response = session.post(url, json=data, timeout=self.timeout)
            elif method.upper() == 'DELETE':
                response = session.delete(url, timeout=self.timeout)
            else:
                raise MeshChatError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()

            if response.content:
                return response.json()
            return {}

        except ImportError:
            raise MeshChatError("requests library not installed")
        except Exception as e:
            if 'ConnectionError' in type(e).__name__ or 'ConnectTimeout' in type(e).__name__:
                raise MeshChatConnectionError(
                    f"Cannot connect to MeshChat at {self._base_url}: {e}"
                )
            elif 'HTTPError' in type(e).__name__:
                raise MeshChatAPIError(f"MeshChat API error: {e}")
            elif 'Timeout' in type(e).__name__:
                raise MeshChatConnectionError(f"MeshChat request timed out: {e}")
            else:
                raise MeshChatError(f"MeshChat request failed: {e}")

    def is_available(self) -> bool:
        """Check if MeshChat service is reachable."""
        try:
            self._request('GET', '/api/status')
            return True
        except MeshChatError:
            return False

    def get_status(self) -> MeshChatStatus:
        """Get MeshChat service status."""
        try:
            data = self._request('GET', '/api/status')
            return MeshChatStatus(
                version=data.get('version'),
                identity_hash=data.get('identity_hash', data.get('identity')),
                display_name=data.get('display_name', data.get('name')),
                peer_count=data.get('peer_count', 0),
                message_count=data.get('message_count', 0),
                propagation_node=data.get('propagation_node', False),
                rns_connected=data.get('rns_connected', True),
                uptime_seconds=data.get('uptime', 0)
            )
        except MeshChatConnectionError:
            raise
        except Exception as e:
            logger.debug(f"Failed to get MeshChat status: {e}")
            return MeshChatStatus()

    def get_peers(self) -> List[MeshChatPeer]:
        """Get list of discovered peers."""
        try:
            data = self._request('GET', '/api/peers')
            peers = data if isinstance(data, list) else data.get('peers', [])
            return [MeshChatPeer.from_api(p) for p in peers]
        except MeshChatError:
            raise
        except Exception as e:
            logger.debug(f"Failed to get peers: {e}")
            return []

    def get_messages(
        self,
        destination_hash: Optional[str] = None,
        limit: int = 50
    ) -> List[MeshChatMessage]:
        """Get messages, optionally filtered by conversation."""
        try:
            params = {'limit': limit}
            if destination_hash:
                params['destination'] = destination_hash

            data = self._request('GET', '/api/messages', params=params)
            messages = data if isinstance(data, list) else data.get('messages', [])
            return [MeshChatMessage.from_api(m) for m in messages]
        except MeshChatError:
            raise
        except Exception as e:
            logger.debug(f"Failed to get messages: {e}")
            return []

    def send_message(self, destination_hash: str, content: str) -> bool:
        """
        Send LXMF message via MeshChat.

        Args:
            destination_hash: Target peer's RNS destination hash
            content: Message text content

        Returns:
            True if message was queued for delivery
        """
        try:
            self._request('POST', '/api/messages', data={
                'destination': destination_hash,
                'content': content
            })
            logger.info(f"Message queued to {destination_hash[:16]}...")
            return True
        except MeshChatError as e:
            logger.error(f"Failed to send message: {e}")
            return False

    def send_announce(self) -> bool:
        """Send LXMF announce to network."""
        try:
            self._request('POST', '/api/announce')
            logger.info("Announce sent via MeshChat")
            return True
        except MeshChatError as e:
            logger.error(f"Failed to send announce: {e}")
            return False

    def get_identity(self) -> Optional[str]:
        """Get MeshChat's RNS identity hash."""
        try:
            status = self.get_status()
            return status.identity_hash
        except MeshChatError:
            return None

    def close(self):
        """Close the HTTP session."""
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __repr__(self):
        return f"MeshChatClient({self._base_url})"
