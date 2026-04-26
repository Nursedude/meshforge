"""
Automation Engine - Periodic network automation tasks (MeshMonitor-inspired).

Provides configurable auto-ping, auto-traceroute, and auto-welcome features
for mesh network monitoring and node greeting.

Usage:
    from utils.automation_engine import AutomationEngine

    engine = AutomationEngine()
    engine.start()

    # Check status
    print(engine.get_status())

    # On-demand traceroute
    result = engine.run_single_traceroute("!abc12345")

    # View persistent history
    history = engine.get_traceroute_store().get_recent(limit=20)

    # Later
    engine.stop()

Configuration persisted at ~/.config/meshforge/automation.json
Traceroute history persisted at ~/.local/share/meshforge/traceroute_history.db
Traceroute log at ~/.cache/meshforge/logs/traceroute.log
"""

import json
import logging
import logging.handlers
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from utils.safe_import import safe_import
from utils.common import SettingsManager
from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

_get_node_tracker, _HAS_NODE_TRACKER = safe_import(
    'gateway.node_tracker', 'get_node_tracker'
)
_NodeInventory, _HAS_NODE_INVENTORY = safe_import(
    'utils.node_inventory', 'NodeInventory'
)
_MeshtasticProtobufClient, _HAS_PROTOBUF_CLIENT = safe_import(
    'gateway.meshtastic_protobuf_client', 'MeshtasticProtobufClient'
)

# Rate limiting constants
MAX_PINGS_PER_MINUTE = 2
MAX_TRACEROUTES_PER_MINUTE = 1
MIN_REQUEST_INTERVAL_SECONDS = 5

# Traceroute history retention
TRACEROUTE_RETENTION_DAYS = 30
TRACEROUTE_LOG_MAX_BYTES = 1_048_576  # 1 MB
TRACEROUTE_LOG_BACKUP_COUNT = 3

# Node ID validation pattern: !hex_chars (8 hex digits)
_NODE_ID_PATTERN = re.compile(r'^![0-9a-fA-F]{1,8}$')

# Default configuration
AUTOMATION_DEFAULTS = {
    "auto_ping": {
        "enabled": False,
        "interval_minutes": 15,
        "targets": [],
        "timeout_seconds": 30,
    },
    "auto_traceroute": {
        "enabled": False,
        "interval_minutes": 60,
        "targets": [],
        "timeout_seconds": 60,
        "auto_discover": True,
    },
    "auto_welcome": {
        "enabled": False,
        "message": "Welcome to the mesh!",
        "cooldown_hours": 24,
    },
}


def _get_traceroute_log_path() -> Path:
    """Get path for the traceroute log file."""
    log_dir = get_real_user_home() / ".cache" / "meshforge" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "traceroute.log"


def _get_traceroute_db_path() -> Path:
    """Get path for the traceroute SQLite database."""
    db_dir = get_real_user_home() / ".local" / "share" / "meshforge"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "traceroute_history.db"


def get_traceroute_log_path() -> Path:
    """Public accessor for traceroute log file path."""
    return _get_traceroute_log_path()


def _setup_traceroute_logger() -> logging.Logger:
    """Create a dedicated logger for traceroute results."""
    tr_logger = logging.getLogger("meshforge.traceroute")
    if tr_logger.handlers:
        return tr_logger
    tr_logger.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            str(_get_traceroute_log_path()),
            maxBytes=TRACEROUTE_LOG_MAX_BYTES,
            backupCount=TRACEROUTE_LOG_BACKUP_COUNT,
        )
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        tr_logger.addHandler(handler)
    except OSError as e:
        logger.warning(f"Could not set up traceroute log file: {e}")
    return tr_logger


def validate_node_id(node_id: str) -> bool:
    """Validate a Meshtastic node ID format."""
    return bool(_NODE_ID_PATTERN.match(node_id))


def _node_id_to_int(node_id: str) -> Optional[int]:
    """Convert a !hex node ID string to an integer for protobuf API."""
    try:
        return int(node_id.lstrip("!"), 16)
    except (ValueError, AttributeError):
        return None


@dataclass
class PingResult:
    """Result from an auto-ping operation."""
    node_id: str
    timestamp: datetime
    success: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class TracerouteResult:
    """Result from an auto-traceroute operation."""
    node_id: str
    timestamp: datetime
    success: bool
    hops: int = 0
    output: str = ""
    error: Optional[str] = None
    node_name: str = ""
    route: List[int] = field(default_factory=list)
    snr_towards: List[float] = field(default_factory=list)
    route_back: List[int] = field(default_factory=list)
    snr_back: List[float] = field(default_factory=list)

    def format_route(self) -> str:
        """Format the route as a human-readable string."""
        if not self.route:
            return self.output or "(no route data)"
        parts = []
        parts.append("Local")
        for i, hop in enumerate(self.route):
            snr = ""
            if i < len(self.snr_towards):
                snr = f" ({self.snr_towards[i]:+.1f}dB)"
            parts.append(f"!{hop:08x}{snr}")
        return " -> ".join(parts)

    def format_return_route(self) -> str:
        """Format the return route as a human-readable string."""
        if not self.route_back:
            return "(no return route)"
        parts = []
        for i, hop in enumerate(self.route_back):
            snr = ""
            if i < len(self.snr_back):
                snr = f" ({self.snr_back[i]:+.1f}dB)"
            parts.append(f"!{hop:08x}{snr}")
        parts.append("Local")
        return " -> ".join(parts)

    def format_log_line(self) -> str:
        """Format as a single log line for the traceroute log file."""
        name = f" ({self.node_name})" if self.node_name else ""
        if self.success:
            route_str = ""
            if self.route:
                hops = [f"!{h:08x}" for h in self.route]
                route_str = f" [{' -> '.join(hops)}]"
            snr_str = ""
            if self.snr_towards:
                snr_str = f" SNR: {self.snr_towards}"
            return (
                f"TRACEROUTE {self.node_id}{name} -> "
                f"{self.hops} hops{route_str}{snr_str} OK"
            )
        return (
            f"TRACEROUTE {self.node_id}{name} -> "
            f"FAIL: {self.error or 'unknown'}"
        )


class TracerouteStore:
    """SQLite-backed persistent storage for traceroute results."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = str(db_path or _get_traceroute_db_path())
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema and prune old entries."""
        with self._lock:
            try:
                conn = connect_tuned(self._db_path, busy_timeout_seconds=5.0)
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS traceroute_results (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            node_id TEXT NOT NULL,
                            node_name TEXT DEFAULT '',
                            timestamp REAL NOT NULL,
                            success INTEGER NOT NULL,
                            hops INTEGER DEFAULT 0,
                            route_json TEXT DEFAULT '[]',
                            snr_towards_json TEXT DEFAULT '[]',
                            route_back_json TEXT DEFAULT '[]',
                            snr_back_json TEXT DEFAULT '[]',
                            raw_output TEXT DEFAULT '',
                            error TEXT DEFAULT ''
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_tr_node
                        ON traceroute_results(node_id)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_tr_time
                        ON traceroute_results(timestamp)
                    """)
                    conn.commit()
                finally:
                    conn.close()
                self.prune()
            except sqlite3.Error as e:
                logger.warning(f"TracerouteStore init failed: {e}")

    def store(self, result: TracerouteResult) -> None:
        """Persist a traceroute result."""
        with self._lock:
            try:
                conn = connect_tuned(self._db_path, busy_timeout_seconds=5.0)
                try:
                    conn.execute(
                        """INSERT INTO traceroute_results
                           (node_id, node_name, timestamp, success, hops,
                            route_json, snr_towards_json, route_back_json,
                            snr_back_json, raw_output, error)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            result.node_id,
                            result.node_name,
                            result.timestamp.timestamp(),
                            1 if result.success else 0,
                            result.hops,
                            json.dumps(result.route),
                            json.dumps(result.snr_towards),
                            json.dumps(result.route_back),
                            json.dumps(result.snr_back),
                            result.output,
                            result.error or "",
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except sqlite3.Error as e:
                logger.warning(f"TracerouteStore.store failed: {e}")

    def get_recent(self, limit: int = 50) -> List[dict]:
        """Get most recent traceroute results."""
        with self._lock:
            try:
                conn = connect_tuned(self._db_path, busy_timeout_seconds=5.0)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        """SELECT * FROM traceroute_results
                           ORDER BY timestamp DESC LIMIT ?""",
                        (limit,),
                    ).fetchall()
                    return [self._row_to_dict(r) for r in rows]
                finally:
                    conn.close()
            except sqlite3.Error as e:
                logger.warning(f"TracerouteStore.get_recent failed: {e}")
                return []

    def get_for_node(self, node_id: str, limit: int = 20) -> List[dict]:
        """Get traceroute history for a specific node."""
        with self._lock:
            try:
                conn = connect_tuned(self._db_path, busy_timeout_seconds=5.0)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        """SELECT * FROM traceroute_results
                           WHERE node_id = ?
                           ORDER BY timestamp DESC LIMIT ?""",
                        (node_id, limit),
                    ).fetchall()
                    return [self._row_to_dict(r) for r in rows]
                finally:
                    conn.close()
            except sqlite3.Error as e:
                logger.warning(f"TracerouteStore.get_for_node failed: {e}")
                return []

    def get_summary(self) -> List[dict]:
        """Get per-node summary: success rate, avg hops, last seen."""
        with self._lock:
            try:
                conn = connect_tuned(self._db_path, busy_timeout_seconds=5.0)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute("""
                        SELECT
                            node_id,
                            node_name,
                            COUNT(*) as total,
                            SUM(success) as successes,
                            AVG(CASE WHEN success = 1 THEN hops END) as avg_hops,
                            MAX(timestamp) as last_seen
                        FROM traceroute_results
                        GROUP BY node_id
                        ORDER BY last_seen DESC
                    """).fetchall()
                    return [
                        {
                            "node_id": r["node_id"],
                            "node_name": r["node_name"] or "",
                            "total": r["total"],
                            "successes": r["successes"] or 0,
                            "success_rate": (
                                (r["successes"] or 0) / r["total"] * 100
                                if r["total"] > 0 else 0
                            ),
                            "avg_hops": round(r["avg_hops"], 1) if r["avg_hops"] else 0,
                            "last_seen": datetime.fromtimestamp(
                                r["last_seen"]
                            ).strftime("%Y-%m-%d %H:%M") if r["last_seen"] else "never",
                        }
                        for r in rows
                    ]
                finally:
                    conn.close()
            except sqlite3.Error as e:
                logger.warning(f"TracerouteStore.get_summary failed: {e}")
                return []

    def prune(self, days: int = TRACEROUTE_RETENTION_DAYS) -> int:
        """Remove entries older than N days. Returns count removed."""
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()
        try:
            conn = connect_tuned(self._db_path, busy_timeout_seconds=5.0)
            try:
                cursor = conn.execute(
                    "DELETE FROM traceroute_results WHERE timestamp < ?",
                    (cutoff,),
                )
                conn.commit()
                removed = cursor.rowcount
                if removed > 0:
                    logger.info(f"Pruned {removed} traceroute entries older than {days}d")
                return removed
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.warning(f"TracerouteStore.prune failed: {e}")
            return 0

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a database row to a dict with parsed JSON fields."""
        d = dict(row)
        d["success"] = bool(d.get("success"))
        d["timestamp_dt"] = datetime.fromtimestamp(
            d["timestamp"]
        ).strftime("%Y-%m-%d %H:%M:%S") if d.get("timestamp") else ""
        for json_field in ("route_json", "snr_towards_json",
                           "route_back_json", "snr_back_json"):
            try:
                d[json_field] = json.loads(d.get(json_field, "[]"))
            except (json.JSONDecodeError, TypeError):
                d[json_field] = []
        return d


class AutomationEngine:
    """
    Periodic network automation tasks for mesh monitoring.

    Runs configurable background tasks:
    - Auto-ping: Periodically ping target nodes, track latency
    - Auto-traceroute: Periodically trace routes to target nodes
    - Auto-welcome: Send welcome message to newly discovered nodes
    """

    def __init__(self, meshtastic_host: str = "localhost"):
        """
        Initialize the automation engine.

        Args:
            meshtastic_host: Host for meshtastic CLI --host flag
        """
        self._settings = SettingsManager("automation", defaults=AUTOMATION_DEFAULTS)
        self._meshtastic_host = meshtastic_host

        self._running = False
        self._stop_event = threading.Event()
        self._threads: Dict[str, threading.Thread] = {}

        # Rate limiting
        self._last_request_time: float = 0
        self._rate_lock = threading.Lock()

        # Ping history (last N results per node)
        self._ping_history: Dict[str, List[PingResult]] = {}
        self._ping_lock = threading.Lock()

        # Traceroute history (in-memory + persistent SQLite)
        self._traceroute_history: Dict[str, List[TracerouteResult]] = {}
        self._traceroute_lock = threading.Lock()
        self._traceroute_store = TracerouteStore()
        self._traceroute_logger = _setup_traceroute_logger()

        # Welcome tracking (nodes we've already greeted)
        self._welcomed_nodes: Set[str] = set()
        self._welcome_lock = threading.Lock()

        # Statistics
        self._stats = {
            "pings_sent": 0,
            "pings_success": 0,
            "pings_failed": 0,
            "traceroutes_sent": 0,
            "traceroutes_success": 0,
            "traceroutes_failed": 0,
            "welcomes_sent": 0,
            "last_ping_cycle": None,
            "last_traceroute_cycle": None,
            "last_welcome_check": None,
        }
        self._stats_lock = threading.Lock()

    def start(self) -> bool:
        """Start all enabled automation tasks."""
        if self._running:
            logger.warning("AutomationEngine already running")
            return True

        self._running = True
        self._stop_event.clear()

        config = self._settings.all()

        if config.get("auto_ping", {}).get("enabled", False):
            self._start_thread("ping", self._ping_loop)

        if config.get("auto_traceroute", {}).get("enabled", False):
            self._start_thread("traceroute", self._traceroute_loop)

        if config.get("auto_welcome", {}).get("enabled", False):
            self._start_thread("welcome", self._welcome_loop)

        if not self._threads:
            logger.info("AutomationEngine: no tasks enabled")
            self._running = False
            return False

        logger.info(
            f"AutomationEngine started with tasks: "
            f"{', '.join(self._threads.keys())}"
        )
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop all automation tasks."""
        if not self._running:
            return

        logger.info("Stopping AutomationEngine...")
        self._running = False
        self._stop_event.set()

        for name, thread in self._threads.items():
            if thread.is_alive():
                thread.join(timeout=timeout)
                if thread.is_alive():
                    logger.warning(f"Automation thread '{name}' did not stop in time")

        self._threads.clear()
        logger.info("AutomationEngine stopped")

    def is_alive(self) -> bool:
        """Check if any automation threads are running."""
        return self._running and any(t.is_alive() for t in self._threads.values())

    def get_status(self) -> dict:
        """Get current automation status."""
        config = self._settings.all()
        with self._stats_lock:
            stats = dict(self._stats)

        return {
            "running": self._running,
            "active_threads": [
                name for name, t in self._threads.items() if t.is_alive()
            ],
            "config": config,
            "stats": stats,
        }

    def get_settings(self) -> SettingsManager:
        """Get the settings manager for external configuration."""
        return self._settings

    def get_ping_history(self, node_id: Optional[str] = None) -> dict:
        """Get ping history, optionally for a specific node."""
        with self._ping_lock:
            if node_id:
                return {node_id: list(self._ping_history.get(node_id, []))}
            return {k: list(v) for k, v in self._ping_history.items()}

    def get_traceroute_history(self, node_id: Optional[str] = None) -> dict:
        """Get traceroute history, optionally for a specific node."""
        with self._traceroute_lock:
            if node_id:
                return {node_id: list(self._traceroute_history.get(node_id, []))}
            return {k: list(v) for k, v in self._traceroute_history.items()}

    def get_traceroute_store(self) -> TracerouteStore:
        """Get the persistent traceroute store for history queries."""
        return self._traceroute_store

    def run_single_traceroute(
        self, node_id: str, timeout: int = 60
    ) -> TracerouteResult:
        """Run a single on-demand traceroute (not part of the periodic loop).

        Args:
            node_id: Target node ID (e.g. "!abc12345")
            timeout: Seconds to wait for response

        Returns:
            TracerouteResult with route data
        """
        if not validate_node_id(node_id):
            return TracerouteResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error="Invalid node ID format (expected !hex)",
            )

        result = self._send_traceroute(node_id, timeout)
        self._record_traceroute(result)
        return result

    def _discover_active_nodes(self) -> List[str]:
        """Discover active mesh nodes via NodeInventory."""
        if not _HAS_NODE_INVENTORY or _NodeInventory is None:
            return []
        try:
            inv = _NodeInventory()
            online = inv.get_online_nodes()
            return [n.node_id for n in online if n.node_id]
        except Exception as e:
            logger.debug(f"Node auto-discovery failed: {e}")
            return []

    # --- Internal helpers ---

    def _start_thread(self, name: str, target) -> None:
        """Start a named daemon thread."""
        thread = threading.Thread(
            target=target,
            daemon=True,
            name=f"Automation-{name}",
        )
        thread.start()
        self._threads[name] = thread

    def _can_send_request(self) -> bool:
        """Rate-limit outbound requests."""
        with self._rate_lock:
            now = time.monotonic()
            if now - self._last_request_time < MIN_REQUEST_INTERVAL_SECONDS:
                return False
            self._last_request_time = now
            return True

    # --- Ping loop ---

    def _ping_loop(self) -> None:
        """Periodically ping configured target nodes."""
        config = self._settings.get("auto_ping", {})
        interval = config.get("interval_minutes", 15) * 60
        targets = config.get("targets", [])
        timeout = config.get("timeout_seconds", 30)

        logger.info(
            f"Auto-ping started: {len(targets)} targets, "
            f"interval {interval // 60}min"
        )

        while self._running:
            for node_id in targets:
                if not self._running:
                    break
                if not self._can_send_request():
                    # Wait for rate limit to clear
                    if self._stop_event.wait(timeout=MIN_REQUEST_INTERVAL_SECONDS):
                        return
                    continue

                result = self._send_ping(node_id, timeout)
                self._record_ping(result)

            with self._stats_lock:
                self._stats["last_ping_cycle"] = datetime.now().isoformat()

            if self._stop_event.wait(timeout=interval):
                return

    def _send_ping(self, node_id: str, timeout: int = 30) -> PingResult:
        """Send a ping to a node via meshtastic CLI."""
        start = time.monotonic()
        try:
            result = subprocess.run(
                [
                    "meshtastic",
                    "--host", self._meshtastic_host,
                    "--sendping",
                    "--dest", node_id,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            success = result.returncode == 0

            with self._stats_lock:
                self._stats["pings_sent"] += 1
                if success:
                    self._stats["pings_success"] += 1
                else:
                    self._stats["pings_failed"] += 1

            return PingResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=success,
                latency_ms=elapsed_ms if success else None,
                error=result.stderr.strip() if not success else None,
            )
        except subprocess.TimeoutExpired:
            with self._stats_lock:
                self._stats["pings_sent"] += 1
                self._stats["pings_failed"] += 1
            return PingResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error=f"Timeout after {timeout}s",
            )
        except Exception as e:
            with self._stats_lock:
                self._stats["pings_sent"] += 1
                self._stats["pings_failed"] += 1
            return PingResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error=str(e),
            )

    def _record_ping(self, result: PingResult) -> None:
        """Record a ping result, keeping last 100 per node."""
        with self._ping_lock:
            if result.node_id not in self._ping_history:
                self._ping_history[result.node_id] = []
            history = self._ping_history[result.node_id]
            history.append(result)
            if len(history) > 100:
                self._ping_history[result.node_id] = history[-100:]

        status = "OK" if result.success else f"FAIL: {result.error}"
        latency = f" ({result.latency_ms:.0f}ms)" if result.latency_ms else ""
        logger.debug(f"Auto-ping {result.node_id}: {status}{latency}")

    # --- Traceroute loop ---

    def _traceroute_loop(self) -> None:
        """Periodically trace routes to target nodes (or all active)."""
        config = self._settings.get("auto_traceroute", {})
        interval = config.get("interval_minutes", 60) * 60
        static_targets = config.get("targets", [])
        timeout = config.get("timeout_seconds", 60)
        auto_discover = config.get("auto_discover", True)

        logger.info(
            f"Auto-traceroute started: "
            f"{'auto-discover' if auto_discover else f'{len(static_targets)} targets'}, "
            f"interval {interval // 60}min"
        )

        while self._running:
            # Build target list: static targets + auto-discovered nodes
            targets = list(static_targets)
            if auto_discover or not targets:
                discovered = self._discover_active_nodes()
                for nid in discovered:
                    if nid not in targets:
                        targets.append(nid)

            if not targets:
                logger.debug("Auto-traceroute: no targets found, waiting...")
            else:
                logger.info(
                    f"Auto-traceroute cycle: {len(targets)} target(s)"
                )

            for node_id in targets:
                if not self._running:
                    break
                if not self._can_send_request():
                    if self._stop_event.wait(timeout=MIN_REQUEST_INTERVAL_SECONDS):
                        return
                    continue

                result = self._send_traceroute(node_id, timeout)
                self._record_traceroute(result)

            with self._stats_lock:
                self._stats["last_traceroute_cycle"] = datetime.now().isoformat()

            if self._stop_event.wait(timeout=interval):
                return

    def _send_traceroute(self, node_id: str, timeout: int = 60) -> TracerouteResult:
        """Send a traceroute — protobuf API first, CLI fallback."""
        result = self._send_traceroute_protobuf(node_id, timeout)
        if result is not None:
            return result
        return self._send_traceroute_cli(node_id, timeout)

    def _send_traceroute_protobuf(
        self, node_id: str, timeout: int = 60
    ) -> Optional[TracerouteResult]:
        """Traceroute via HTTP protobuf API (richer data, no TCP lock)."""
        if not _HAS_PROTOBUF_CLIENT or _MeshtasticProtobufClient is None:
            return None

        dest_num = _node_id_to_int(node_id)
        if dest_num is None:
            return None

        try:
            client = _MeshtasticProtobufClient(
                host=self._meshtastic_host
            )
            if not client.connect():
                return None

            try:
                pb_result = client.send_traceroute(
                    dest_num=dest_num,
                    timeout=float(timeout),
                )
            finally:
                client.disconnect()

            if pb_result is None:
                return None

            with self._stats_lock:
                self._stats["traceroutes_sent"] += 1
                if pb_result.completed:
                    self._stats["traceroutes_success"] += 1
                else:
                    self._stats["traceroutes_failed"] += 1

            return TracerouteResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=pb_result.completed,
                hops=len(pb_result.route),
                route=list(pb_result.route),
                snr_towards=list(pb_result.snr_towards),
                route_back=list(pb_result.route_back),
                snr_back=list(pb_result.snr_back),
            )
        except Exception as e:
            logger.debug(f"Protobuf traceroute to {node_id} failed: {e}")
            return None

    def _send_traceroute_cli(
        self, node_id: str, timeout: int = 60
    ) -> TracerouteResult:
        """Traceroute via meshtastic CLI (fallback)."""
        try:
            result = subprocess.run(
                [
                    "meshtastic",
                    "--host", self._meshtastic_host,
                    "--traceroute", node_id,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            success = result.returncode == 0
            output = result.stdout.strip()

            # Count hops from output (lines with node IDs)
            hops = 0
            if success and output:
                for line in output.split("\n"):
                    if "!" in line or "node" in line.lower():
                        hops += 1

            with self._stats_lock:
                self._stats["traceroutes_sent"] += 1
                if success:
                    self._stats["traceroutes_success"] += 1
                else:
                    self._stats["traceroutes_failed"] += 1

            return TracerouteResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=success,
                hops=hops,
                output=output,
                error=result.stderr.strip() if not success else None,
            )
        except subprocess.TimeoutExpired:
            with self._stats_lock:
                self._stats["traceroutes_sent"] += 1
                self._stats["traceroutes_failed"] += 1
            return TracerouteResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error=f"Timeout after {timeout}s",
            )
        except Exception as e:
            with self._stats_lock:
                self._stats["traceroutes_sent"] += 1
                self._stats["traceroutes_failed"] += 1
            return TracerouteResult(
                node_id=node_id,
                timestamp=datetime.now(),
                success=False,
                error=str(e),
            )

    def _record_traceroute(self, result: TracerouteResult) -> None:
        """Record traceroute to in-memory cache, SQLite, and log file."""
        # In-memory history (kept for backward compatibility)
        with self._traceroute_lock:
            if result.node_id not in self._traceroute_history:
                self._traceroute_history[result.node_id] = []
            history = self._traceroute_history[result.node_id]
            history.append(result)
            if len(history) > 50:
                self._traceroute_history[result.node_id] = history[-50:]

        # Persistent SQLite storage
        self._traceroute_store.store(result)

        # Dedicated log file
        self._traceroute_logger.info(result.format_log_line())

        # Standard logger
        status = f"{result.hops} hops" if result.success else f"FAIL: {result.error}"
        logger.debug(f"Auto-traceroute {result.node_id}: {status}")

    # --- Welcome loop ---

    def _welcome_loop(self) -> None:
        """Monitor for new nodes and send welcome messages."""
        config = self._settings.get("auto_welcome", {})
        message = config.get("message", "Welcome to the mesh!")
        cooldown_hours = config.get("cooldown_hours", 24)
        check_interval = 60  # Check every minute for new nodes

        logger.info(
            f"Auto-welcome started: cooldown {cooldown_hours}h, "
            f"check interval {check_interval}s"
        )

        while self._running:
            if _HAS_NODE_TRACKER and _get_node_tracker:
                try:
                    tracker = _get_node_tracker()
                    if tracker:
                        self._check_new_nodes(tracker, message)
                except Exception as e:
                    logger.debug(f"Auto-welcome check failed: {e}")

            with self._stats_lock:
                self._stats["last_welcome_check"] = datetime.now().isoformat()

            if self._stop_event.wait(timeout=check_interval):
                return

    def _check_new_nodes(self, tracker, message: str) -> None:
        """Check for new nodes and send welcome messages."""
        try:
            nodes = tracker.get_all_nodes() if hasattr(tracker, 'get_all_nodes') else {}
        except Exception:
            return

        for node_id in nodes:
            if not self._running:
                break

            with self._welcome_lock:
                if node_id in self._welcomed_nodes:
                    continue
                self._welcomed_nodes.add(node_id)

            if not self._can_send_request():
                if self._stop_event.wait(timeout=MIN_REQUEST_INTERVAL_SECONDS):
                    return
                continue

            self._send_welcome(node_id, message)

    def _send_welcome(self, node_id: str, message: str) -> None:
        """Send a welcome message to a new node."""
        try:
            result = subprocess.run(
                [
                    "meshtastic",
                    "--host", self._meshtastic_host,
                    "--sendtext", message,
                    "--dest", node_id,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                with self._stats_lock:
                    self._stats["welcomes_sent"] += 1
                logger.info(f"Welcomed new node {node_id}")
            else:
                logger.warning(
                    f"Failed to welcome {node_id}: {result.stderr.strip()}"
                )
        except subprocess.TimeoutExpired:
            logger.warning(f"Welcome to {node_id} timed out")
        except Exception as e:
            logger.warning(f"Welcome to {node_id} failed: {e}")


# Module-level singleton
_engine: Optional[AutomationEngine] = None
_engine_lock = threading.Lock()


def get_automation_engine() -> AutomationEngine:
    """Get or create the global AutomationEngine instance."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = AutomationEngine()
    return _engine
