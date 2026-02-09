"""
Map Data Service - Unified node GeoJSON from all available sources.

Collects node data from meshtasticd, MQTT, and RNS node tracker,
merges into a single GeoJSON FeatureCollection, and optionally
serves via HTTP for the live map to consume.

Usage:
    # Collector only (for TUI integration)
    from utils.map_data_service import MapDataCollector
    collector = MapDataCollector()
    geojson = collector.collect()

    # Full HTTP server (for browser access)
    from utils.map_data_service import MapServer
    server = MapServer(port=5000)
    server.start()  # Serves map + API at http://localhost:5000

This module provides the main server class and CLI. The implementation
is split across:
- map_data_collector.py: MapDataCollector class (data collection logic)
- map_http_handler.py: MapRequestHandler class (HTTP endpoint handling)
"""

# Ensure src/ is in path when running standalone
import sys
from pathlib import Path as _Path
_src_dir = _Path(__file__).resolve().parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

import json
import logging
import os
import socket
import threading
from http.server import HTTPServer
from pathlib import Path
from typing import List, Optional

# Re-export for backward compatibility
from utils.map_data_collector import MapDataCollector
from utils.map_http_handler import MapRequestHandler

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    'MapDataCollector',
    'MapRequestHandler',
    'MapServer',
    'get_all_ips',
    'get_lan_ip',
]


def get_all_ips() -> list:
    """Get all local IP addresses for this machine.

    Returns list of IPs from all interfaces, useful when machine
    has multiple networks (LAN, AREDN, VPN, etc.).
    """
    ips = []
    try:
        import subprocess
        result = subprocess.run(
            ['hostname', '-I'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            ips = [ip.strip() for ip in result.stdout.split() if ip.strip()]
    except Exception:
        pass

    # Fallback: try socket method
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(("8.8.8.8", 80))
            ips = [s.getsockname()[0]]
            s.close()
        except Exception:
            pass

    return ips if ips else ["127.0.0.1"]


def get_lan_ip() -> str:
    """Get a local IP address for this machine.

    Returns first available IP. For machines with multiple interfaces
    (AREDN, VPN, etc.), use get_all_ips() to see all options.
    """
    ips = get_all_ips()
    return ips[0] if ips else "127.0.0.1"


class MapServer:
    """MeshForge HTTP server for network monitoring and radio control.

    Serves:
    - GET /              -> node_map.html (the live map)
    - GET /api/nodes/geojson  -> live node GeoJSON from all sources
    - GET /api/nodes/history  -> node history stats + unique nodes (24h)
    - GET /api/nodes/trajectory/<id> -> trajectory GeoJSON for a node
    - GET /api/nodes/snapshot -> historical network snapshot for playback
    - GET /api/messages/queue -> pending messages from gateway queue
    - GET /api/network/topology -> network topology for D3.js visualization
    - GET /api/status    -> server health check + history stats
    - GET /*             -> static files from web/

    Meshtastic API Proxy (MeshForge-owned):
    - GET  /api/v1/fromradio -> multiplexed protobuf from meshtasticd
    - PUT  /api/v1/toradio   -> forwarded to meshtasticd
    - GET  /json/nodes       -> proxied from meshtasticd
    - GET  /json/report      -> proxied from meshtasticd
    - GET  /mesh/*           -> meshtastic web client (proxied)
    - GET  /api/proxy/status -> proxy health + stats

    Radio Control API (MeshForge-owned):
    - GET /api/radio/info     -> radio device information
    - GET /api/radio/nodes    -> nodes from connected radio
    - GET /api/radio/channels -> channels from connected radio
    - GET /api/radio/status   -> radio connection status
    - POST /api/radio/message -> send message via radio

    WebSocket:
    - ws://localhost:5001/  -> real-time message stream

    Usage:
        server = MapServer(port=5000)
        server.start()     # Blocks
        # or
        server.start_background()  # Returns immediately
    """

    def __init__(self, port: int = 5000, host: str = "0.0.0.0",
                 cors_origins: Optional[List[str]] = None,
                 enable_message_listener: bool = True,
                 enable_websocket: bool = True,
                 websocket_port: int = 5001,
                 enable_api_proxy: bool = True,
                 meshtasticd_host: str = 'localhost',
                 meshtasticd_port: int = 9443,
                 meshtasticd_tls: bool = True):
        """Initialize map server.

        Args:
            port: Port to listen on (default 5000)
            host: Bind address. Options:
                  - "0.0.0.0": All interfaces (default, works with AREDN/VPN/LAN)
                  - "localhost" or "127.0.0.1": Local only
                  - Specific IP: Bind to that IP only
            cors_origins: CORS allowed origins. Options:
                  - None: Allow all origins (*) - best for LAN/AREDN access
                  - List: Only allow specified origins, e.g.,
                    ["http://localhost", "http://192.168.1."]
            enable_message_listener: Start MessageListener for inbound messages (default True)
            enable_websocket: Start WebSocket server for real-time message push (default True)
            websocket_port: WebSocket server port (default 5001)
            enable_api_proxy: Start Meshtastic API proxy for web client (default True)
            meshtasticd_host: meshtasticd host for API proxy (default 'localhost')
            meshtasticd_port: meshtasticd web port for API proxy (default 9443)
            meshtasticd_tls: Use TLS for API proxy (default True)
        """
        self.port = port
        self.host = host
        self.cors_origins = cors_origins
        self.enable_message_listener = enable_message_listener
        self.enable_websocket = enable_websocket
        self.websocket_port = websocket_port
        self.enable_api_proxy = enable_api_proxy
        self.meshtasticd_host = meshtasticd_host
        self.meshtasticd_port = meshtasticd_port
        self.meshtasticd_tls = meshtasticd_tls
        self.collector = MapDataCollector()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._message_listener_started = False
        self._websocket_started = False
        self._api_proxy_started = False

        # Find web directory
        src_dir = Path(__file__).parent.parent
        self.web_dir = str(src_dir.parent / "web")

    def _start_websocket_server(self):
        """Start the WebSocket server for real-time message broadcast."""
        if not self.enable_websocket:
            return

        try:
            from utils.websocket_server import (
                get_websocket_server, is_websocket_available
            )

            if not is_websocket_available():
                logger.info("WebSocket: Not available (install websockets library)")
                print("  WebSocket: Not available (pip install websockets)")
                return

            ws_server = get_websocket_server(port=self.websocket_port)
            if ws_server.start():
                self._websocket_started = True
                logger.info(f"WebSocket server started on port {self.websocket_port}")
                print(f"  WebSocket: ws://localhost:{self.websocket_port}/")
            else:
                logger.warning("WebSocket server failed to start")
                print("  WebSocket: Failed to start")

        except ImportError as e:
            logger.debug(f"WebSocket server not available: {e}")
            print("  WebSocket: Not available")
        except Exception as e:
            logger.warning(f"Error starting WebSocket server: {e}")

    def _stop_websocket_server(self):
        """Stop the WebSocket server."""
        if not self._websocket_started:
            return

        try:
            from utils.websocket_server import stop_websocket_server
            stop_websocket_server()
            self._websocket_started = False
            logger.info("WebSocket server stopped")
        except Exception as e:
            logger.debug(f"Error stopping WebSocket server: {e}")

    def _start_api_proxy(self):
        """Start the Meshtastic API proxy for web client ownership.

        MeshForge becomes the sole consumer of meshtasticd's HTTP API,
        multiplexing packets to all connected web clients. This fixes
        the 'waiting for delivery' bug and enables multi-client access.
        """
        if not self.enable_api_proxy:
            return

        try:
            from gateway.meshtastic_api_proxy import MeshtasticApiProxy

            proxy = MeshtasticApiProxy(
                host=self.meshtasticd_host,
                port=self.meshtasticd_port,
                tls=self.meshtasticd_tls,
            )

            # Auto-detect port if default doesn't work
            if not proxy._probe():
                logger.info("API proxy: default port failed, auto-detecting...")
                if proxy.auto_detect_port():
                    logger.info(f"API proxy: found meshtasticd at {proxy._base_url}")
                else:
                    logger.warning(
                        "API proxy: meshtasticd web server not found. "
                        "Proxy will start anyway and retry on connection."
                    )

            if proxy.start():
                self._api_proxy_started = True
                MapRequestHandler.api_proxy = proxy
                logger.info(
                    f"Meshtastic API proxy started → "
                    f"{proxy._base_url}"
                )
                print(f"  API Proxy: {proxy._base_url} (meshtastic web client at /mesh/)")
            else:
                logger.warning("Meshtastic API proxy failed to start")
                print("  API Proxy: Failed to start")

        except ImportError as e:
            logger.debug(f"API proxy not available: {e}")
            print("  API Proxy: Not available")
        except Exception as e:
            logger.warning(f"Error starting API proxy: {e}")
            print(f"  API Proxy: Error - {e}")

    def _stop_api_proxy(self):
        """Stop the Meshtastic API proxy."""
        if not self._api_proxy_started:
            return

        try:
            # Stop the actual instance we started (not the singleton)
            if MapRequestHandler.api_proxy:
                MapRequestHandler.api_proxy.stop()
            MapRequestHandler.api_proxy = None
            self._api_proxy_started = False
            logger.info("Meshtastic API proxy stopped")
        except Exception as e:
            logger.debug(f"Error stopping API proxy: {e}")

    def _start_message_listener(self):
        """Start the MessageListener for receiving inbound mesh messages."""
        if not self.enable_message_listener:
            return

        try:
            from utils.message_listener import start_listener, get_listener_status, get_listener

            # Start the listener
            success = start_listener(host="localhost")
            if success:
                self._message_listener_started = True
                logger.info("MessageListener started - inbound messages enabled")
                print("  Message RX: Enabled (listening for inbound messages)")

                # Register WebSocket broadcast callback
                self._register_websocket_callback()
            else:
                status = get_listener_status()
                logger.warning(f"MessageListener failed to start: {status.get('error', 'unknown')}")
                print("  Message RX: Failed to start (check meshtasticd)")
        except ImportError as e:
            logger.debug(f"MessageListener not available: {e}")
            print("  Message RX: Not available")
        except Exception as e:
            logger.warning(f"Error starting MessageListener: {e}")

    def _register_websocket_callback(self):
        """Register callback to broadcast messages to WebSocket clients."""
        if not self._websocket_started:
            return

        try:
            from utils.message_listener import get_listener
            from utils.websocket_server import broadcast_message

            listener = get_listener()

            def on_message(msg_data):
                """Callback to broadcast messages to WebSocket clients."""
                broadcast_message(msg_data)

            listener.add_callback(on_message)
            logger.info("WebSocket broadcast callback registered")

        except ImportError as e:
            logger.debug(f"Could not register WebSocket callback: {e}")
        except Exception as e:
            logger.warning(f"Error registering WebSocket callback: {e}")

    def _stop_message_listener(self):
        """Stop the MessageListener."""
        if not self._message_listener_started:
            return

        try:
            from utils.message_listener import stop_listener
            stop_listener()
            self._message_listener_started = False
            logger.info("MessageListener stopped")
        except Exception as e:
            logger.debug(f"Error stopping MessageListener: {e}")

    def _stop_all_services(self):
        """Stop all background services (MessageListener, WebSocket, API Proxy)."""
        self._stop_message_listener()
        self._stop_websocket_server()
        self._stop_api_proxy()

    def start(self):
        """Start server (blocking)."""
        MapRequestHandler.collector = self.collector
        MapRequestHandler.web_dir = self.web_dir
        MapRequestHandler.allowed_origins = self.cors_origins

        # Start Meshtastic API proxy (owns the web client API)
        self._start_api_proxy()

        # Start WebSocket server first (so callback can be registered)
        self._start_websocket_server()

        # Start message listener for inbound messages
        self._start_message_listener()

        self._server = HTTPServer((self.host, self.port), MapRequestHandler)
        logger.info(f"Map server starting on http://{self.host}:{self.port}")
        print(f"MeshForge NOC Server running on port {self.port}")
        if self.host == "0.0.0.0":
            # Show all available IPs when binding to all interfaces
            ips = get_all_ips()
            print("  Access via any of these URLs:")
            for ip in ips:
                print(f"    NOC Map:    http://{ip}:{self.port}/")
                print(f"    Mesh Client: http://{ip}:{self.port}/mesh/")
        elif self.host in ("127.0.0.1", "localhost"):
            print(f"  NOC Map:     http://localhost:{self.port}/")
            print(f"  Mesh Client: http://localhost:{self.port}/mesh/")
        else:
            print(f"  NOC Map:     http://{self.host}:{self.port}/")
            print(f"  Mesh Client: http://{self.host}:{self.port}/mesh/")
        print("  Press Ctrl+C to stop")

        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down map server...")
            self._stop_all_services()
            self._server.shutdown()

    def start_background(self):
        """Start server in background thread."""
        MapRequestHandler.collector = self.collector
        MapRequestHandler.web_dir = self.web_dir
        MapRequestHandler.allowed_origins = self.cors_origins

        # Start Meshtastic API proxy (owns the web client API)
        self._start_api_proxy()

        # Start WebSocket server first (so callback can be registered)
        self._start_websocket_server()

        # Start message listener for inbound messages
        self._start_message_listener()

        self._server = HTTPServer((self.host, self.port), MapRequestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"Map server running in background on port {self.port}")

    def stop(self):
        """Stop the server."""
        self._stop_all_services()
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def url(self) -> str:
        """Get the server URL."""
        host = self.host
        if host == "0.0.0.0":
            host = "localhost"
        return f"http://{host}:{self.port}"


def main():
    """Run the map server standalone.

    Designed for both interactive use and systemd service deployment.

    Examples:
        # Interactive
        python -m utils.map_data_service

        # As service
        python -m utils.map_data_service --daemon

        # Collect GeoJSON only
        python -m utils.map_data_service --collect-only
    """
    import argparse
    import signal

    parser = argparse.ArgumentParser(
        description="MeshForge Live Map Server - NOC Web Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m utils.map_data_service           # Start on port 5000
  python -m utils.map_data_service -p 8080   # Use custom port
  python -m utils.map_data_service --daemon  # Run as background service
  python -m utils.map_data_service --status  # Check if running
  python -m utils.map_data_service --collect-only  # Just get GeoJSON
        """
    )
    parser.add_argument("-p", "--port", type=int, default=5000,
                        help="Port (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address (default: 0.0.0.0 for all interfaces)")
    parser.add_argument("--collect-only", action="store_true",
                        help="Just collect and print GeoJSON, then exit")
    parser.add_argument("--daemon", action="store_true",
                        help="Run in daemon mode (for systemd)")
    parser.add_argument("--status", action="store_true",
                        help="Check if map server is running")
    parser.add_argument("--pid-file", type=str,
                        default="/run/meshforge/map-server.pid",
                        help="PID file location (default: /run/meshforge/map-server.pid)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose logging")
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Status check
    if args.status:
        return _check_server_status(args.port)

    # Collect-only mode
    if args.collect_only:
        collector = MapDataCollector()
        geojson = collector.collect()
        print(json.dumps(geojson, indent=2))

        # Summary to stderr so it doesn't pollute JSON output
        import sys
        props = geojson.get("properties", {})
        print(f"\n# {len(geojson['features'])} nodes with position", file=sys.stderr)
        print(f"# {props.get('nodes_without_position_count', 0)} nodes without position", file=sys.stderr)
        print(f"# Sources: {props.get('sources', {})}", file=sys.stderr)
        return 0

    # Check if port is already in use
    if _is_port_in_use(args.port):
        print(f"ERROR: Port {args.port} is already in use")
        print(f"  Check: lsof -i :{args.port}")
        print(f"  Or use: --port <different_port>")
        return 1

    # Daemon mode - write PID file for service management
    if args.daemon:
        _write_pid_file(args.pid_file)

    # Create and start server
    server = MapServer(port=args.port, host=args.host)

    # Signal handlers for graceful shutdown
    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        print(f"\nReceived {sig_name}, shutting down...")
        server.stop()
        _remove_pid_file(args.pid_file)
        import sys
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        server.start()
    finally:
        _remove_pid_file(args.pid_file)

    return 0


def _is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _check_server_status(port: int) -> int:
    """Check if map server is running and report status."""
    if _is_port_in_use(port):
        print(f"MeshForge Map Server is running on port {port}")
        # Try to get status from API
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=2) as resp:
                data = json.loads(resp.read().decode())
                print(f"  Collector: {'active' if data.get('collector') else 'inactive'}")
                if data.get('radio'):
                    radio = data['radio']
                    print(f"  Radio: {radio.get('mode', 'unknown')} "
                          f"({'connected' if radio.get('connected') else 'disconnected'})")
        except Exception:
            pass
        return 0
    else:
        print(f"MeshForge Map Server is not running on port {port}")
        return 1


def _write_pid_file(pid_file: str) -> None:
    """Write PID file for service management."""
    try:
        pid_dir = Path(pid_file).parent
        pid_dir.mkdir(parents=True, exist_ok=True)
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
        logger.debug(f"PID file written: {pid_file}")
    except PermissionError:
        # Running as non-root, use alternative location
        alt_pid = Path("/tmp/meshforge-map-server.pid")
        with open(alt_pid, 'w') as f:
            f.write(str(os.getpid()))
        logger.debug(f"PID file written: {alt_pid}")
    except Exception as e:
        logger.warning(f"Could not write PID file: {e}")


def _remove_pid_file(pid_file: str) -> None:
    """Remove PID file on shutdown."""
    for path in [pid_file, "/tmp/meshforge-map-server.pid"]:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    sys.exit(main() or 0)
