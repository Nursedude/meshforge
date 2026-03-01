"""Communication protocol for MeshForge Agent.

Based on NGINX Agent communication patterns - provides secure, reliable
communication between agent and management plane.

Supports:
- JSON-based message format
- Request/response and streaming patterns
- Authentication via tokens
- Message signing for integrity
- Reconnection with exponential backoff

Author: Claude Code (Opus 4.5)
Session: claude/config-api-restful-ETusS
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import secrets
import socket
import ssl
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# =============================================================================
# Message Types
# =============================================================================


class MessageType(Enum):
    """Types of protocol messages."""

    # Connection lifecycle
    HELLO = auto()           # Agent introduction
    HELLO_ACK = auto()       # Management acknowledgment
    GOODBYE = auto()         # Graceful disconnect
    HEARTBEAT = auto()       # Keep-alive ping
    HEARTBEAT_ACK = auto()   # Keep-alive response

    # Commands
    COMMAND = auto()         # Management -> Agent command
    COMMAND_RESULT = auto()  # Agent -> Management response

    # Data streams
    METRICS = auto()         # Agent -> Management metrics push
    HEALTH = auto()          # Agent -> Management health status
    EVENTS = auto()          # Agent -> Management event stream
    CONFIG_SYNC = auto()     # Bidirectional config sync

    # Errors
    ERROR = auto()           # Error notification
    NACK = auto()            # Negative acknowledgment


class CommandType(Enum):
    """Types of commands from management plane."""

    # Configuration
    CONFIG_GET = "config.get"
    CONFIG_SET = "config.set"
    CONFIG_DELETE = "config.delete"
    CONFIG_RESET = "config.reset"
    CONFIG_EXPORT = "config.export"
    CONFIG_IMPORT = "config.import"

    # Service control
    SERVICE_STATUS = "service.status"
    SERVICE_START = "service.start"
    SERVICE_STOP = "service.stop"
    SERVICE_RESTART = "service.restart"

    # Health
    HEALTH_CHECK = "health.check"
    HEALTH_PROBE = "health.probe"
    HEALTH_HISTORY = "health.history"

    # Metrics
    METRICS_GET = "metrics.get"
    METRICS_EXPORT = "metrics.export"

    # Agent control
    AGENT_STATUS = "agent.status"
    AGENT_RESTART = "agent.restart"
    AGENT_UPDATE = "agent.update"

    # System
    SYSTEM_INFO = "system.info"
    SYSTEM_LOGS = "system.logs"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class AuthToken:
    """Authentication token for agent-management communication."""
    token_id: str
    secret: str
    issued_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    scopes: List[str] = field(default_factory=list)

    def is_valid(self) -> bool:
        """Check if token is still valid."""
        if self.expires_at and time.time() > self.expires_at:
            return False
        return True

    def sign(self, data: bytes) -> str:
        """Sign data with token secret."""
        return hmac.new(
            self.secret.encode(),
            data,
            hashlib.sha256
        ).hexdigest()

    def verify(self, data: bytes, signature: str) -> bool:
        """Verify signature."""
        expected = self.sign(data)
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def generate(scopes: List[str] = None, ttl_hours: float = 24) -> AuthToken:
        """Generate a new authentication token."""
        return AuthToken(
            token_id=secrets.token_hex(16),
            secret=secrets.token_hex(32),
            issued_at=time.time(),
            expires_at=time.time() + (ttl_hours * 3600) if ttl_hours else None,
            scopes=scopes or ["*"]
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (excludes secret)."""
        return {
            "token_id": self.token_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "scopes": self.scopes,
        }


@dataclass
class Message:
    """Protocol message between agent and management."""
    msg_type: MessageType
    payload: Dict[str, Any] = field(default_factory=dict)
    msg_id: str = field(default_factory=lambda: secrets.token_hex(8))
    timestamp: float = field(default_factory=time.time)
    reply_to: Optional[str] = None  # For responses
    signature: Optional[str] = None

    def to_bytes(self) -> bytes:
        """Serialize message to bytes."""
        data = {
            "type": self.msg_type.name,
            "id": self.msg_id,
            "ts": self.timestamp,
            "payload": self.payload,
        }
        if self.reply_to:
            data["reply_to"] = self.reply_to

        return json.dumps(data, separators=(",", ":")).encode()

    @staticmethod
    def from_bytes(data: bytes) -> Message:
        """Deserialize message from bytes."""
        obj = json.loads(data.decode())
        return Message(
            msg_type=MessageType[obj["type"]],
            payload=obj.get("payload", {}),
            msg_id=obj["id"],
            timestamp=obj["ts"],
            reply_to=obj.get("reply_to"),
        )

    def sign(self, token: AuthToken) -> Message:
        """Sign this message with token."""
        data = self.to_bytes()
        self.signature = token.sign(data)
        return self

    def verify(self, token: AuthToken) -> bool:
        """Verify message signature."""
        if not self.signature:
            return False
        data = self.to_bytes()
        return token.verify(data, self.signature)

    def create_response(
        self,
        msg_type: MessageType,
        payload: Dict[str, Any] = None
    ) -> Message:
        """Create a response to this message."""
        return Message(
            msg_type=msg_type,
            payload=payload or {},
            reply_to=self.msg_id,
        )


@dataclass
class ConnectionState:
    """State of the protocol connection."""
    connected: bool = False
    authenticated: bool = False
    last_heartbeat: float = 0.0
    missed_heartbeats: int = 0
    reconnect_attempts: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    messages_sent: int = 0
    messages_received: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "connected": self.connected,
            "authenticated": self.authenticated,
            "last_heartbeat": self.last_heartbeat,
            "missed_heartbeats": self.missed_heartbeats,
            "reconnect_attempts": self.reconnect_attempts,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
        }


# =============================================================================
# Protocol Implementation
# =============================================================================


class AgentProtocol:
    """Communication protocol for agent-management communication.

    Handles:
    - Connection establishment with TLS
    - Authentication handshake
    - Message framing and serialization
    - Heartbeat management
    - Automatic reconnection
    """

    # Protocol constants
    PROTOCOL_VERSION = 1
    MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB
    HEADER_SIZE = 4  # 4-byte length prefix
    HEARTBEAT_INTERVAL = 30.0  # seconds
    HEARTBEAT_TIMEOUT = 90.0  # seconds (3 missed heartbeats)
    MAX_RECONNECT_DELAY = 300.0  # 5 minutes max backoff

    def __init__(
        self,
        instance_id: str,
        token: AuthToken,
        host: str = "localhost",
        port: int = 9443,
        use_tls: bool = True,
        verify_cert: bool = True,
    ):
        """Initialize protocol handler.

        Args:
            instance_id: Unique identifier for this agent instance
            token: Authentication token
            host: Management server host
            port: Management server port
            use_tls: Use TLS encryption (recommended)
            verify_cert: Verify server certificate
        """
        self.instance_id = instance_id
        self.token = token
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.verify_cert = verify_cert

        self._socket: Optional[socket.socket] = None
        self._state = ConnectionState()
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()

        # Message handling
        self._pending_responses: Dict[str, threading.Event] = {}
        self._response_data: Dict[str, Message] = {}
        self._message_handlers: Dict[MessageType, List[Callable[[Message], None]]] = {}
        self._outgoing_queue: queue.Queue = queue.Queue()

        # Background threads
        self._running = threading.Event()
        self._stop_event = threading.Event()
        self._receiver_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._sender_thread: Optional[threading.Thread] = None

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    def connect(self) -> bool:
        """Establish connection to management server.

        Returns:
            True if connection and authentication successful
        """
        with self._lock:
            if self._state.connected:
                return True

            try:
                # Create socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(30.0)

                # Wrap with TLS if enabled
                if self.use_tls:
                    context = ssl.create_default_context()
                    if not self.verify_cert:
                        context.check_hostname = False
                        context.verify_mode = ssl.CERT_NONE
                    sock = context.wrap_socket(sock, server_hostname=self.host)

                # Connect
                sock.connect((self.host, self.port))
                self._socket = sock
                self._state.connected = True
                self._state.reconnect_attempts = 0

                logger.info(f"Connected to management server {self.host}:{self.port}")

                # Perform handshake
                if not self._handshake():
                    self.disconnect()
                    return False

                self._state.authenticated = True
                return True

            except (socket.error, ssl.SSLError) as e:
                logger.error(f"Connection failed: {e}")
                self._state.reconnect_attempts += 1
                return False

    def disconnect(self) -> None:
        """Gracefully disconnect from management server."""
        with self._lock:
            if not self._state.connected:
                return

            # Send goodbye
            try:
                self._send_message(Message(
                    msg_type=MessageType.GOODBYE,
                    payload={"instance_id": self.instance_id}
                ))
            except Exception:
                pass  # Best effort

            # Close socket
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None

            self._state.connected = False
            self._state.authenticated = False
            logger.info("Disconnected from management server")

    def reconnect(self) -> bool:
        """Attempt to reconnect with exponential backoff.

        Returns:
            True if reconnection successful
        """
        self.disconnect()

        # Exponential backoff
        delay = min(
            2 ** self._state.reconnect_attempts,
            self.MAX_RECONNECT_DELAY
        )

        logger.info(f"Reconnecting in {delay:.1f}s (attempt {self._state.reconnect_attempts + 1})")
        time.sleep(delay)

        return self.connect()

    def _handshake(self) -> bool:
        """Perform authentication handshake.

        Returns:
            True if handshake successful
        """
        # Send HELLO
        hello = Message(
            msg_type=MessageType.HELLO,
            payload={
                "instance_id": self.instance_id,
                "protocol_version": self.PROTOCOL_VERSION,
                "token_id": self.token.token_id,
                "capabilities": ["config", "health", "metrics", "commands"],
            }
        ).sign(self.token)

        self._send_message(hello)

        # Wait for HELLO_ACK
        response = self._receive_message(timeout=30.0)
        if not response or response.msg_type != MessageType.HELLO_ACK:
            logger.error("Handshake failed: no HELLO_ACK received")
            return False

        # Verify response signature
        if not response.verify(self.token):
            logger.error("Handshake failed: invalid signature")
            return False

        logger.info(f"Handshake complete: {response.payload.get('message', 'OK')}")
        return True

    # -------------------------------------------------------------------------
    # Message Handling
    # -------------------------------------------------------------------------

    def _send_message(self, message: Message) -> bool:
        """Send a message over the connection.

        Args:
            message: Message to send

        Returns:
            True if send successful
        """
        if not self._socket or not self._state.connected:
            return False

        with self._send_lock:
            try:
                data = message.to_bytes()

                # Add signature if authenticated
                if self._state.authenticated and not message.signature:
                    message.sign(self.token)
                    data = message.to_bytes()
                    # Append signature
                    sig_data = json.dumps({"sig": message.signature}).encode()
                    data = data[:-1] + b',"sig":"' + message.signature.encode() + b'"}'

                # Length-prefix framing
                length = len(data)
                if length > self.MAX_MESSAGE_SIZE:
                    logger.error(f"Message too large: {length} bytes")
                    return False

                header = struct.pack(">I", length)
                self._socket.sendall(header + data)

                self._state.bytes_sent += length + self.HEADER_SIZE
                self._state.messages_sent += 1

                return True

            except (socket.error, ssl.SSLError) as e:
                logger.error(f"Send failed: {e}")
                self._state.connected = False
                return False

    def _receive_message(self, timeout: float = None) -> Optional[Message]:
        """Receive a message from the connection.

        Args:
            timeout: Optional receive timeout

        Returns:
            Received Message or None on error
        """
        if not self._socket or not self._state.connected:
            return None

        try:
            if timeout:
                self._socket.settimeout(timeout)

            # Read length header
            header = self._recv_exact(self.HEADER_SIZE)
            if not header:
                return None

            length = struct.unpack(">I", header)[0]
            if length > self.MAX_MESSAGE_SIZE:
                logger.error(f"Message too large: {length} bytes")
                return None

            # Read message data
            data = self._recv_exact(length)
            if not data:
                return None

            self._state.bytes_received += length + self.HEADER_SIZE
            self._state.messages_received += 1

            return Message.from_bytes(data)

        except socket.timeout:
            return None
        except (socket.error, ssl.SSLError, json.JSONDecodeError) as e:
            logger.error(f"Receive failed: {e}")
            self._state.connected = False
            return None

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes."""
        data = bytearray()
        while len(data) < n:
            chunk = self._socket.recv(n - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def send(self, message: Message) -> bool:
        """Queue a message for sending.

        Args:
            message: Message to send

        Returns:
            True if queued successfully
        """
        if not self._running.is_set():
            return self._send_message(message)

        self._outgoing_queue.put(message)
        return True

    def send_and_wait(
        self,
        message: Message,
        timeout: float = 30.0
    ) -> Optional[Message]:
        """Send a message and wait for response.

        Args:
            message: Message to send
            timeout: Response timeout in seconds

        Returns:
            Response message or None on timeout
        """
        event = threading.Event()
        self._pending_responses[message.msg_id] = event

        try:
            if not self.send(message):
                return None

            if event.wait(timeout):
                return self._response_data.pop(message.msg_id, None)
            else:
                logger.warning(f"Timeout waiting for response to {message.msg_id}")
                return None
        finally:
            self._pending_responses.pop(message.msg_id, None)

    def on_message(
        self,
        msg_type: MessageType,
        handler: Callable[[Message], None]
    ) -> None:
        """Register a message handler.

        Args:
            msg_type: Message type to handle
            handler: Handler function
        """
        if msg_type not in self._message_handlers:
            self._message_handlers[msg_type] = []
        self._message_handlers[msg_type].append(handler)

    def _dispatch_message(self, message: Message) -> None:
        """Dispatch received message to handlers."""
        # Check if this is a response to a pending request
        if message.reply_to and message.reply_to in self._pending_responses:
            self._response_data[message.reply_to] = message
            self._pending_responses[message.reply_to].set()
            return

        # Dispatch to registered handlers
        if message.msg_type in self._message_handlers:
            for handler in self._message_handlers[message.msg_type]:
                try:
                    handler(message)
                except Exception as e:
                    logger.error(f"Handler error for {message.msg_type}: {e}")

    # -------------------------------------------------------------------------
    # Background Operations
    # -------------------------------------------------------------------------

    def start(self) -> bool:
        """Start background protocol operations.

        Returns:
            True if started successfully
        """
        if self._running.is_set():
            return True

        if not self.connect():
            return False

        self._running.set()

        # Start receiver thread
        self._receiver_thread = threading.Thread(
            target=self._receiver_loop,
            name="AgentProtocol-Receiver",
            daemon=True
        )
        self._receiver_thread.start()

        # Start sender thread
        self._sender_thread = threading.Thread(
            target=self._sender_loop,
            name="AgentProtocol-Sender",
            daemon=True
        )
        self._sender_thread.start()

        # Start heartbeat thread
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="AgentProtocol-Heartbeat",
            daemon=True
        )
        self._heartbeat_thread.start()

        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop background operations and disconnect.

        Args:
            timeout: Shutdown timeout
        """
        self._running.clear()
        self._stop_event.set()

        # Unblock sender queue
        self._outgoing_queue.put(None)

        # Wait for threads
        if self._receiver_thread:
            self._receiver_thread.join(timeout)
        if self._sender_thread:
            self._sender_thread.join(timeout)
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout)

        self.disconnect()

    def _receiver_loop(self) -> None:
        """Background loop for receiving messages."""
        while self._running.is_set():
            if not self._state.connected:
                if not self.reconnect():
                    continue

            message = self._receive_message(timeout=1.0)
            if message:
                self._dispatch_message(message)

    def _sender_loop(self) -> None:
        """Background loop for sending queued messages."""
        while self._running.is_set():
            try:
                message = self._outgoing_queue.get(timeout=1.0)
                if message is None:
                    continue
                self._send_message(message)
            except queue.Empty:
                continue

    def _heartbeat_loop(self) -> None:
        """Background loop for heartbeat management."""
        while self._running.is_set():
            self._stop_event.wait(self.HEARTBEAT_INTERVAL)
            if not self._running.is_set():
                break

            if not self._state.connected:
                continue

            # Send heartbeat
            heartbeat = Message(
                msg_type=MessageType.HEARTBEAT,
                payload={"timestamp": time.time()}
            )
            if self._send_message(heartbeat):
                # Note: we don't wait for ACK, just track last send time
                pass

            # Check for missed heartbeats (peer hasn't sent anything)
            if self._state.last_heartbeat > 0:
                elapsed = time.time() - self._state.last_heartbeat
                if elapsed > self.HEARTBEAT_TIMEOUT:
                    logger.warning("Heartbeat timeout, reconnecting...")
                    self.reconnect()

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        return self._state.connected and self._state.authenticated
