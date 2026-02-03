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

    Radio Control API (MeshForge-owned):
    - GET /api/radio/info     -> radio device information
    - GET /api/radio/nodes    -> nodes from connected radio
    - GET /api/radio/channels -> channels from connected radio
    - GET /api/radio/status   -> radio connection status
    - POST /api/radio/message -> send message via radio

    Usage:
        server = MapServer(port=5000)
        server.start()     # Blocks
        # or
        server.start_background()  # Returns immediately
    """

    def __init__(self, port: int = 5000, host: str = "0.0.0.0",
                 cors_origins: Optional[List[str]] = None,
                 enable_message_listener: bool = True):
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
        """
        self.port = port
        self.host = host
        self.cors_origins = cors_origins
        self.enable_message_listener = enable_message_listener
        self.collector = MapDataCollector()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._message_listener_started = False

        # Find web directory
        src_dir = Path(__file__).parent.parent
        self.web_dir = str(src_dir.parent / "web")

    def _start_message_listener(self):
        """Start the MessageListener for receiving inbound mesh messages."""
        if not self.enable_message_listener:
            return

        try:
            from utils.message_listener import start_listener, get_listener_status
            success = start_listener(host="localhost")
            if success:
                self._message_listener_started = True
                logger.info("MessageListener started - inbound messages enabled")
                print("  Message RX: Enabled (listening for inbound messages)")
            else:
                status = get_listener_status()
                logger.warning(f"MessageListener failed to start: {status.get('error', 'unknown')}")
                print("  Message RX: Failed to start (check meshtasticd)")
        except ImportError as e:
            logger.debug(f"MessageListener not available: {e}")
            print("  Message RX: Not available")
        except Exception as e:
            logger.warning(f"Error starting MessageListener: {e}")

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

    def start(self):
        """Start server (blocking)."""
        MapRequestHandler.collector = self.collector
        MapRequestHandler.web_dir = self.web_dir
        MapRequestHandler.allowed_origins = self.cors_origins

        # Start message listener for inbound messages
        self._start_message_listener()

        self._server = HTTPServer((self.host, self.port), MapRequestHandler)
        logger.info(f"Map server starting on http://{self.host}:{self.port}")
        print(f"MeshForge Map Server running on port {self.port}")
        if self.host == "0.0.0.0":
            # Show all available IPs when binding to all interfaces
            ips = get_all_ips()
            print("  Access via any of these URLs:")
            for ip in ips:
                print(f"    http://{ip}:{self.port}/")
        elif self.host in ("127.0.0.1", "localhost"):
            print(f"  URL: http://localhost:{self.port}/")
        else:
            print(f"  URL: http://{self.host}:{self.port}/")
        print("  Press Ctrl+C to stop")

        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down map server...")
            self._stop_message_listener()
            self._server.shutdown()

    def start_background(self):
        """Start server in background thread."""
        MapRequestHandler.collector = self.collector
        MapRequestHandler.web_dir = self.web_dir
        MapRequestHandler.allowed_origins = self.cors_origins

        # Start message listener for inbound messages
        self._start_message_listener()

        self._server = HTTPServer((self.host, self.port), MapRequestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"Map server running in background on port {self.port}")

    def stop(self):
        """Stop the server."""
        self._stop_message_listener()
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def url(self) -> str:
        """Get the server URL."""
        return f"http://{self.host}:{self.port}"


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
