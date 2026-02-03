"""
MeshForge WebSocket Server - Real-time message broadcast.

Solves the "one client" limitation of Meshtastic HTTP API by providing
a WebSocket endpoint that broadcasts messages to multiple web clients.

MeshForge becomes the single authoritative client to meshtasticd, and
this server pushes messages to all connected browsers in real-time.

Usage:
    from utils.websocket_server import MessageWebSocketServer

    # Create and start server
    ws_server = MessageWebSocketServer(port=5001)
    ws_server.start()  # Non-blocking, runs in background thread

    # Broadcast a message to all clients
    ws_server.broadcast({
        'from_id': '!abc123',
        'content': 'Hello mesh!',
        'timestamp': '2026-02-03T10:30:00'
    })

    # Stop server
    ws_server.stop()
"""

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Try to import websockets - graceful fallback if not available
try:
    import websockets
    from websockets.server import serve
    from websockets.exceptions import ConnectionClosed
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.debug("websockets library not available - WebSocket server disabled")


@dataclass
class WebSocketStats:
    """Statistics for WebSocket server."""
    connected_clients: int = 0
    total_connections: int = 0
    messages_broadcast: int = 0
    started_at: Optional[datetime] = None


class MessageWebSocketServer:
    """
    WebSocket server for real-time message broadcast.

    Runs in a background thread with its own asyncio event loop.
    Thread-safe broadcast method can be called from any thread.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 5001):
        """
        Initialize WebSocket server.

        Args:
            host: Bind address (0.0.0.0 for all interfaces)
            port: WebSocket port (default 5001, one above HTTP)
        """
        self.host = host
        self.port = port
        self._clients: Set = set()
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._running = False
        self._stats = WebSocketStats()

        # Message history for new connections (ring buffer)
        self._history: List[Dict[str, Any]] = []
        self._history_max = 50

    @property
    def stats(self) -> WebSocketStats:
        """Get server statistics."""
        with self._lock:
            self._stats.connected_clients = len(self._clients)
        return self._stats

    def start(self) -> bool:
        """
        Start WebSocket server in background thread.

        Returns:
            True if started successfully, False if websockets unavailable
        """
        if not WEBSOCKETS_AVAILABLE:
            logger.warning("WebSocket server not started - websockets library not installed")
            return False

        if self._running:
            logger.warning("WebSocket server already running")
            return True

        self._running = True
        self._thread = threading.Thread(
            target=self._run_server,
            daemon=True,
            name="meshforge-websocket-server"
        )
        self._thread.start()

        # Wait briefly for server to start
        for _ in range(10):
            if self._loop is not None:
                break
            threading.Event().wait(0.1)

        if self._loop:
            self._stats.started_at = datetime.now()
            logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")
            return True
        else:
            logger.error("WebSocket server failed to start")
            self._running = False
            return False

    def stop(self):
        """Stop WebSocket server."""
        if not self._running:
            return

        self._running = False

        # Close all client connections
        if self._loop and self._clients:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._close_all_clients(),
                    self._loop
                ).result(timeout=5)
            except Exception as e:
                logger.debug(f"Error closing clients: {e}")

        # Stop the event loop
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self._loop = None
        self._thread = None
        self._server = None
        logger.info("WebSocket server stopped")

    def broadcast(self, message: Dict[str, Any]):
        """
        Broadcast a message to all connected clients.

        Thread-safe - can be called from any thread.

        Args:
            message: Message dict with from_id, content, timestamp, etc.
        """
        if not self._running or not self._loop:
            return

        # Add to history
        with self._lock:
            self._history.append(message)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max:]

        # Schedule broadcast on event loop
        try:
            asyncio.run_coroutine_threadsafe(
                self._broadcast_async(message),
                self._loop
            )
            self._stats.messages_broadcast += 1
        except Exception as e:
            logger.debug(f"Broadcast scheduling error: {e}")

    def _run_server(self):
        """Run the asyncio event loop in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._serve())
        except Exception as e:
            logger.error(f"WebSocket server error: {e}")
        finally:
            self._loop.close()
            self._loop = None

    async def _serve(self):
        """Async server coroutine."""
        async with serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
        ) as server:
            self._server = server
            # Keep running until stopped
            while self._running:
                await asyncio.sleep(1)

    async def _handle_client(self, websocket):
        """Handle a new WebSocket client connection."""
        client_id = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        logger.info(f"WebSocket client connected: {client_id}")

        with self._lock:
            self._clients.add(websocket)
            self._stats.total_connections += 1

        try:
            # Send message history on connect
            if self._history:
                await websocket.send(json.dumps({
                    'type': 'history',
                    'messages': list(self._history)
                }))

            # Send connection confirmation
            await websocket.send(json.dumps({
                'type': 'connected',
                'message': 'Connected to MeshForge message stream',
                'timestamp': datetime.now().isoformat()
            }))

            # Keep connection alive and handle incoming messages
            async for message in websocket:
                # Handle client messages (e.g., ping, subscribe filters)
                try:
                    data = json.loads(message)
                    await self._handle_client_message(websocket, data)
                except json.JSONDecodeError:
                    logger.debug(f"Invalid JSON from client: {message[:100]}")

        except ConnectionClosed:
            logger.debug(f"WebSocket client disconnected: {client_id}")
        except Exception as e:
            logger.error(f"WebSocket client error: {e}")
        finally:
            with self._lock:
                self._clients.discard(websocket)
            logger.info(f"WebSocket client removed: {client_id}")

    async def _handle_client_message(self, websocket, data: Dict[str, Any]):
        """Handle a message from a client."""
        msg_type = data.get('type')

        if msg_type == 'ping':
            await websocket.send(json.dumps({
                'type': 'pong',
                'timestamp': datetime.now().isoformat()
            }))
        elif msg_type == 'get_history':
            limit = min(data.get('limit', 50), self._history_max)
            await websocket.send(json.dumps({
                'type': 'history',
                'messages': list(self._history[-limit:])
            }))
        elif msg_type == 'get_stats':
            await websocket.send(json.dumps({
                'type': 'stats',
                'connected_clients': len(self._clients),
                'messages_broadcast': self._stats.messages_broadcast,
                'started_at': self._stats.started_at.isoformat() if self._stats.started_at else None
            }))

    async def _broadcast_async(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients (async)."""
        if not self._clients:
            return

        payload = json.dumps({
            'type': 'message',
            'data': message
        })

        # Copy clients to avoid modification during iteration
        with self._lock:
            clients = set(self._clients)

        # Broadcast to all clients concurrently
        if clients:
            await asyncio.gather(
                *[self._send_to_client(client, payload) for client in clients],
                return_exceptions=True
            )

    async def _send_to_client(self, websocket, payload: str):
        """Send message to a single client."""
        try:
            await websocket.send(payload)
        except ConnectionClosed:
            with self._lock:
                self._clients.discard(websocket)
        except Exception as e:
            logger.debug(f"Error sending to client: {e}")

    async def _close_all_clients(self):
        """Close all client connections gracefully."""
        with self._lock:
            clients = set(self._clients)

        for client in clients:
            try:
                await client.close(1001, "Server shutting down")
            except Exception:
                pass

        with self._lock:
            self._clients.clear()


# Singleton instance
_ws_server: Optional[MessageWebSocketServer] = None


def get_websocket_server(port: int = 5001) -> MessageWebSocketServer:
    """Get or create the global WebSocket server."""
    global _ws_server
    if _ws_server is None:
        _ws_server = MessageWebSocketServer(port=port)
    return _ws_server


def start_websocket_server(port: int = 5001) -> bool:
    """Start the global WebSocket server."""
    server = get_websocket_server(port)
    return server.start()


def stop_websocket_server():
    """Stop the global WebSocket server."""
    if _ws_server:
        _ws_server.stop()


def broadcast_message(message: Dict[str, Any]):
    """Broadcast a message via the global WebSocket server."""
    if _ws_server:
        _ws_server.broadcast(message)


def is_websocket_available() -> bool:
    """Check if WebSocket functionality is available."""
    return WEBSOCKETS_AVAILABLE
