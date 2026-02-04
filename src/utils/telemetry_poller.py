"""
TelemetryPoller - Automatic telemetry request for silent Meshtastic 2.7+ nodes.

Meshtastic 2.7.13+ no longer sends telemetry by default to reduce mesh
congestion. This class automatically identifies nodes that haven't reported
telemetry recently and sends targeted telemetry requests.

Usage:
    from utils.telemetry_poller import TelemetryPoller

    poller = TelemetryPoller(poll_interval_minutes=30)
    poller.start()

    # Later
    poller.stop()

Reference:
    CLI command: meshtastic --request-telemetry --dest '!nodeID'
    https://meshtastic.org/docs/software/python/cli/
"""

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)

# Rate limiting to avoid mesh congestion
MIN_POLL_INTERVAL_SECONDS = 10  # Minimum time between requests
MAX_REQUESTS_PER_MINUTE = 4     # Maximum 4 requests per minute
BATCH_SIZE = 5                  # Max nodes to poll per cycle


@dataclass
class PollRecord:
    """Record of telemetry poll for a node."""
    node_id: str
    last_poll_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    poll_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_error: Optional[str] = None


class TelemetryPoller:
    """
    Automatic telemetry polling for silent Meshtastic 2.7+ nodes.

    Identifies nodes that are online but haven't reported telemetry recently,
    and sends rate-limited telemetry requests.
    """

    def __init__(
        self,
        poll_interval_minutes: int = 30,
        meshtastic_host: str = "localhost",
        on_telemetry_received: Optional[Callable[[str, dict], None]] = None,
    ):
        """
        Initialize the TelemetryPoller.

        Args:
            poll_interval_minutes: How often to check for silent nodes (default: 30)
            meshtastic_host: Host for meshtastic CLI --host flag (default: localhost)
            on_telemetry_received: Optional callback when telemetry is received
        """
        self.poll_interval = poll_interval_minutes * 60
        self.meshtastic_host = meshtastic_host
        self.on_telemetry_received = on_telemetry_received

        self._running = False
        self._stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None

        # Track polling history per node
        self._poll_records: Dict[str, PollRecord] = {}
        self._records_lock = threading.Lock()

        # Rate limiting (protected by _rate_limit_lock)
        self._last_request_time: float = 0
        self._requests_this_minute: int = 0
        self._minute_start: float = 0
        self._rate_limit_lock = threading.Lock()

        # Statistics
        self._stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "rate_limited": 0,
            "nodes_polled": 0,
            "last_poll_cycle": None,
        }
        self._stats_lock = threading.Lock()

    def start(self) -> bool:
        """Start the telemetry poller background thread."""
        if self._running:
            logger.warning("TelemetryPoller already running")
            return True

        self._running = True
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="TelemetryPoller"
        )
        self._poll_thread.start()
        logger.info(f"TelemetryPoller started (interval: {self.poll_interval // 60}min)")
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the telemetry poller."""
        if not self._running:
            return

        logger.info("Stopping TelemetryPoller...")
        self._running = False
        self._stop_event.set()

        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=timeout)
            if self._poll_thread.is_alive():
                logger.warning("TelemetryPoller thread did not stop in time")

        logger.info("TelemetryPoller stopped")

    def poll_node_now(self, node_id: str) -> bool:
        """
        Immediately request telemetry from a specific node.

        Args:
            node_id: Meshtastic node ID (e.g., "!ba4bf9d0")

        Returns:
            True if request was sent successfully
        """
        if not self._can_send_request():
            logger.debug(f"Rate limited: cannot poll {node_id} now")
            with self._stats_lock:
                self._stats["rate_limited"] += 1
            return False

        return self._request_telemetry(node_id)

    def get_poll_record(self, node_id: str) -> Optional[PollRecord]:
        """Get the poll record for a specific node."""
        with self._records_lock:
            return self._poll_records.get(node_id)

    def get_stats(self) -> dict:
        """Get polling statistics."""
        with self._stats_lock:
            return dict(self._stats)

    def identify_silent_nodes(
        self,
        nodes: List[dict],
        telemetry_age_threshold: Optional[int] = None
    ) -> List[str]:
        """
        Identify nodes that are online but have stale telemetry.

        Args:
            nodes: List of node dicts with 'id', 'is_online', and optionally
                   'telemetry_timestamp' or 'last_telemetry'
            telemetry_age_threshold: Override default poll_interval for threshold

        Returns:
            List of node IDs that should be polled
        """
        threshold = telemetry_age_threshold or self.poll_interval
        silent_nodes = []
        now = datetime.now()

        for node in nodes:
            node_id = node.get('id') or node.get('node_id') or node.get('meshtastic_id')
            if not node_id:
                continue

            # Skip if not online
            is_online = node.get('is_online', True)
            if not is_online:
                continue

            # Check telemetry age
            telemetry_ts = (
                node.get('telemetry_timestamp') or
                node.get('last_telemetry') or
                node.get('telemetry', {}).get('timestamp')
            )

            if telemetry_ts is None:
                # No telemetry ever - definitely should poll
                silent_nodes.append(node_id)
            elif isinstance(telemetry_ts, datetime):
                age = (now - telemetry_ts).total_seconds()
                if age > threshold:
                    silent_nodes.append(node_id)
            elif isinstance(telemetry_ts, (int, float)):
                # Assume Unix timestamp
                try:
                    ts_dt = datetime.fromtimestamp(telemetry_ts)
                    age = (now - ts_dt).total_seconds()
                    if age > threshold:
                        silent_nodes.append(node_id)
                except (ValueError, OSError):
                    pass

        return silent_nodes

    def _poll_loop(self) -> None:
        """Background loop that periodically polls silent nodes."""
        # Initial delay to let the system stabilize
        if self._stop_event.wait(30):
            return

        while self._running:
            try:
                self._run_poll_cycle()
            except Exception as e:
                logger.error(f"Error in poll cycle: {e}")

            # Wait for next cycle
            if self._stop_event.wait(self.poll_interval):
                break

    def _run_poll_cycle(self) -> None:
        """Run a single poll cycle."""
        logger.debug("Starting telemetry poll cycle")

        with self._stats_lock:
            self._stats["last_poll_cycle"] = datetime.now().isoformat()

        # Get list of silent nodes from node tracker
        silent_nodes = self._get_silent_nodes_from_tracker()

        if not silent_nodes:
            logger.debug("No silent nodes found")
            return

        logger.info(f"Found {len(silent_nodes)} silent nodes to poll")

        # Poll up to BATCH_SIZE nodes per cycle
        polled = 0
        for node_id in silent_nodes[:BATCH_SIZE]:
            if not self._running:
                break

            if not self._can_send_request():
                logger.debug("Rate limit reached for this cycle")
                break

            if self._request_telemetry(node_id):
                polled += 1

            # Wait between requests
            time.sleep(MIN_POLL_INTERVAL_SECONDS)

        with self._stats_lock:
            self._stats["nodes_polled"] += polled

        logger.info(f"Polled {polled} nodes this cycle")

    def _get_silent_nodes_from_tracker(self) -> List[str]:
        """Get silent nodes from the node tracker if available."""
        try:
            # Try to import and use the node tracker
            from gateway.node_tracker import UnifiedNodeTracker

            # Try to get singleton instance if available
            tracker = None
            try:
                from gateway.node_tracker import get_node_tracker
                tracker = get_node_tracker()
            except (ImportError, AttributeError):
                pass

            if tracker is None:
                return []

            # Build node list for identification
            nodes = []
            for node in tracker.get_meshtastic_nodes():
                nodes.append({
                    'id': node.meshtastic_id,
                    'is_online': node.is_online,
                    'telemetry_timestamp': node.telemetry.timestamp if node.telemetry else None
                })

            return self.identify_silent_nodes(nodes)

        except ImportError:
            logger.debug("Node tracker not available")
            return []
        except Exception as e:
            logger.error(f"Error getting silent nodes: {e}")
            return []

    def _can_send_request(self) -> bool:
        """Check if we can send a request (rate limiting)."""
        with self._rate_limit_lock:
            now = time.time()

            # Reset minute counter if needed
            if now - self._minute_start >= 60:
                self._minute_start = now
                self._requests_this_minute = 0

            # Check rate limits
            if self._requests_this_minute >= MAX_REQUESTS_PER_MINUTE:
                return False

            if now - self._last_request_time < MIN_POLL_INTERVAL_SECONDS:
                return False

            return True

    def _request_telemetry(self, node_id: str) -> bool:
        """
        Send telemetry request to a node using meshtastic CLI.

        Args:
            node_id: Meshtastic node ID (e.g., "!ba4bf9d0")

        Returns:
            True if request was sent successfully
        """
        # Validate node_id format
        if not node_id.startswith('!'):
            node_id = f"!{node_id}"

        # Update rate limiting (thread-safe)
        with self._rate_limit_lock:
            self._last_request_time = time.time()
            self._requests_this_minute += 1

        with self._stats_lock:
            self._stats["total_requests"] += 1

        # Get or create poll record
        with self._records_lock:
            if node_id not in self._poll_records:
                self._poll_records[node_id] = PollRecord(node_id=node_id)
            record = self._poll_records[node_id]
            record.last_poll_time = datetime.now()
            record.poll_count += 1

        try:
            # Find meshtastic CLI
            cli_path = self._find_meshtastic_cli()
            if not cli_path:
                logger.error("meshtastic CLI not found")
                return False

            # Build command
            cmd = [
                cli_path,
                "--host", self.meshtastic_host,
                "--request-telemetry",
                "--dest", node_id
            ]

            logger.debug(f"Requesting telemetry from {node_id}")

            # Execute command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                logger.info(f"Telemetry request sent to {node_id}")
                with self._stats_lock:
                    self._stats["successful_requests"] += 1
                with self._records_lock:
                    record.success_count += 1
                    record.last_success_time = datetime.now()
                    record.last_error = None
                return True
            else:
                error_msg = result.stderr.strip() or "Unknown error"
                logger.warning(f"Telemetry request to {node_id} failed: {error_msg}")
                with self._stats_lock:
                    self._stats["failed_requests"] += 1
                with self._records_lock:
                    record.failure_count += 1
                    record.last_error = error_msg
                return False

        except subprocess.TimeoutExpired:
            logger.warning(f"Telemetry request to {node_id} timed out")
            with self._stats_lock:
                self._stats["failed_requests"] += 1
            with self._records_lock:
                record.failure_count += 1
                record.last_error = "Timeout"
            return False
        except Exception as e:
            logger.error(f"Error requesting telemetry from {node_id}: {e}")
            with self._stats_lock:
                self._stats["failed_requests"] += 1
            with self._records_lock:
                record.failure_count += 1
                record.last_error = str(e)
            return False

    def _find_meshtastic_cli(self) -> Optional[str]:
        """Find the meshtastic CLI binary."""
        import os
        from pathlib import Path
        from utils.paths import get_real_user_home

        # Check common locations
        candidates = [
            # pipx install location (uses real user home, not /root with sudo)
            get_real_user_home() / ".local" / "bin" / "meshtastic",
            # System-wide
            Path("/usr/local/bin/meshtastic"),
            Path("/usr/bin/meshtastic"),
        ]

        # Also check PATH
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate)

        # Try which command
        try:
            result = subprocess.run(
                ["which", "meshtastic"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

        return None


# Module-level singleton instance
_poller_instance: Optional[TelemetryPoller] = None
_poller_lock = threading.Lock()


def get_telemetry_poller(
    poll_interval_minutes: int = 30,
    auto_start: bool = False
) -> TelemetryPoller:
    """
    Get or create the singleton TelemetryPoller instance.

    Args:
        poll_interval_minutes: Poll interval for new instance
        auto_start: If True, start the poller automatically

    Returns:
        The TelemetryPoller singleton
    """
    global _poller_instance

    with _poller_lock:
        if _poller_instance is None:
            _poller_instance = TelemetryPoller(
                poll_interval_minutes=poll_interval_minutes
            )
            if auto_start:
                _poller_instance.start()

    return _poller_instance


if __name__ == "__main__":
    # Test the poller
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("TelemetryPoller Test")
    print("=" * 50)

    poller = TelemetryPoller(poll_interval_minutes=1)

    # Test identify_silent_nodes
    test_nodes = [
        {"id": "!ba4bf9d0", "is_online": True, "telemetry_timestamp": None},
        {"id": "!12345678", "is_online": True, "telemetry_timestamp": datetime.now()},
        {"id": "!deadbeef", "is_online": False, "telemetry_timestamp": None},
    ]

    silent = poller.identify_silent_nodes(test_nodes)
    print(f"Silent nodes: {silent}")

    # Test single poll
    if len(sys.argv) > 1:
        node_id = sys.argv[1]
        print(f"\nPolling node: {node_id}")
        success = poller.poll_node_now(node_id)
        print(f"Success: {success}")
        print(f"Stats: {poller.get_stats()}")
