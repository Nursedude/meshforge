"""
MeshForge Diagnostic Intelligence Engine.

Provides intelligent, context-aware diagnostics for mesh network operations.
Works standalone (rule-based) or enhanced with Claude API (PRO mode).

The engine correlates symptoms, applies domain knowledge, and provides
actionable diagnoses that help users understand and fix issues.

Architecture:
    Symptoms → Context → Analysis → Diagnosis → Suggestions

Usage:
    engine = DiagnosticEngine()

    # Report a symptom
    diagnosis = engine.diagnose(
        symptom="Connection refused to meshtasticd",
        context={"port": 4403, "service_running": True}
    )

    print(diagnosis.explanation)
    print(diagnosis.suggestions)
"""

import logging
import re
import time
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Tuple
from collections import deque
from pathlib import Path
import threading

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Use centralized service checker
_check_service, _check_systemd_service, _ServiceState, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'check_systemd_service', 'ServiceState'
)

_get_predictive_analyzer, _PredictiveAlert, _HAS_ANALYTICS = safe_import(
    'utils.analytics', 'get_predictive_analyzer', 'PredictiveAlert'
)
# Re-export under original names for use throughout this module
if _HAS_SERVICE_CHECK:
    check_service = _check_service
    ServiceState = _ServiceState


# =============================================================================
# EVIDENCE CHECK FUNCTIONS
# These functions verify actual system state and return evidence strings
# =============================================================================

def check_port_open(host: str, port: int, timeout: float = 2.0) -> Optional[str]:
    """Check if a TCP port is open. Returns evidence string or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            if result == 0:
                return f"Port {port} on {host} is open and accepting connections"
            else:
                return None  # Port closed - no positive evidence
    except (socket.error, OSError):
        return None


def check_port_closed(host: str, port: int, timeout: float = 2.0) -> Optional[str]:
    """Check if a TCP port is closed. Returns evidence string or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            if result != 0:
                return f"Port {port} on {host} is NOT accepting connections"
            else:
                return None  # Port open - no evidence of problem
    except (socket.error, OSError):
        return f"Port {port} on {host} is unreachable"


def check_process_running(process_name: str) -> Optional[str]:
    """Check if a process is running. Returns evidence string or None."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", process_name],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            return f"Process '{process_name}' is running (PID: {', '.join(pids)})"
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def check_process_not_running(process_name: str) -> Optional[str]:
    """Check if a process is NOT running. Returns evidence string or None."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", process_name],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return f"Process '{process_name}' is NOT running"
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return f"Could not verify process '{process_name}' status"


def check_systemd_service_active(service_name: str) -> Optional[str]:
    """Check if a systemd service is active. Returns evidence string or None."""
    try:
        # Use centralized service checker if available
        if _HAS_SERVICE_CHECK:
            is_running, is_enabled = _check_systemd_service(service_name)
            if is_running:
                return f"Systemd service '{service_name}' is active"
            return None
        else:
            # Fallback to direct systemctl call
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and "active" in result.stdout:
                return f"Systemd service '{service_name}' is active"
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def check_systemd_service_inactive(service_name: str) -> Optional[str]:
    """Check if a systemd service is inactive. Returns evidence string or None."""
    try:
        # Use centralized service checker if available
        if _HAS_SERVICE_CHECK:
            is_running, is_enabled = _check_systemd_service(service_name)
            if not is_running:
                return f"Systemd service '{service_name}' is NOT active"
            return None
        else:
            # Fallback to direct systemctl call
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0 or "inactive" in result.stdout:
                return f"Systemd service '{service_name}' is NOT active"
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return f"Systemd service '{service_name}' status unknown"


def check_file_exists(file_path: str) -> Optional[str]:
    """Check if a file exists. Returns evidence string or None."""
    path = Path(file_path)
    if path.exists():
        return f"Config file exists: {file_path}"
    return None


def check_file_missing(file_path: str) -> Optional[str]:
    """Check if a file is missing. Returns evidence string or None."""
    path = Path(file_path)
    if not path.exists():
        return f"Config file missing: {file_path}"
    return None


def check_serial_device_exists(device_pattern: str = "/dev/ttyUSB*") -> Optional[str]:
    """Check if any serial device exists. Returns evidence string or None."""
    from pathlib import Path
    devices = list(Path("/dev").glob(device_pattern.replace("/dev/", "")))
    devices.extend(list(Path("/dev").glob("ttyACM*")))
    if devices:
        return f"Serial devices found: {', '.join(str(d) for d in devices[:3])}"
    return None


def check_no_serial_device() -> Optional[str]:
    """Check if NO serial devices exist. Returns evidence string or None."""
    from pathlib import Path
    devices = list(Path("/dev").glob("ttyUSB*"))
    devices.extend(list(Path("/dev").glob("ttyACM*")))
    if not devices:
        return "No serial devices (/dev/ttyUSB*, /dev/ttyACM*) found"
    return None


def check_meshtasticd_clients() -> Optional[str]:
    """Check for other meshtastic clients that might be blocking connection."""
    try:
        result = subprocess.run(
            ["pgrep", "-af", "meshtastic|nomadnet"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            clients = result.stdout.strip().split('\n')
            return f"Found {len(clients)} potential Meshtastic client(s) running"
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def check_rns_config_exists() -> Optional[str]:
    """Check if RNS config exists at standard location."""
    from utils.paths import ReticulumPaths
    config_path = ReticulumPaths.get_config_file()
    if config_path.exists():
        return f"RNS config exists at {config_path}"
    return None


def check_rns_config_missing() -> Optional[str]:
    """Check if RNS config is missing."""
    from utils.paths import ReticulumPaths
    config_path = ReticulumPaths.get_config_file()
    if not config_path.exists():
        return f"RNS config missing at {config_path}"
    return None


# Evidence check factory functions (create checks with parameters)

def make_port_check(host: str, port: int) -> Callable[[Dict], Optional[str]]:
    """Factory: create a port open check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_port_open(host, port)
    return check


def make_port_closed_check(host: str, port: int) -> Callable[[Dict], Optional[str]]:
    """Factory: create a port closed check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_port_closed(host, port)
    return check


def make_process_check(process_name: str) -> Callable[[Dict], Optional[str]]:
    """Factory: create a process running check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_process_running(process_name)
    return check


def make_process_not_running_check(process_name: str) -> Callable[[Dict], Optional[str]]:
    """Factory: create a process NOT running check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_process_not_running(process_name)
    return check


def make_service_active_check(service_name: str) -> Callable[[Dict], Optional[str]]:
    """Factory: create a systemd service active check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_systemd_service_active(service_name)
    return check


def make_service_inactive_check(service_name: str) -> Callable[[Dict], Optional[str]]:
    """Factory: create a systemd service inactive check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_systemd_service_inactive(service_name)
    return check


class Severity(Enum):
    """Diagnostic severity levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Category(Enum):
    """Diagnostic categories for mesh operations."""
    CONNECTIVITY = "connectivity"
    HARDWARE = "hardware"
    PROTOCOL = "protocol"
    PERFORMANCE = "performance"
    SECURITY = "security"
    CONFIGURATION = "configuration"
    RESOURCE = "resource"
    PREDICTIVE = "predictive"  # Sprint B: Proactive alerts from trend analysis


@dataclass
class Symptom:
    """A reported symptom or event."""
    message: str
    category: Category
    severity: Severity
    timestamp: datetime = field(default_factory=datetime.now)
    context: Dict[str, Any] = field(default_factory=dict)
    source: str = ""  # Which component reported this

    def __hash__(self):
        return hash((self.message, self.category, self.source))


@dataclass
class Diagnosis:
    """Result of diagnostic analysis."""
    symptom: Symptom
    likely_cause: str
    confidence: float  # 0.0 to 1.0
    evidence: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    related_symptoms: List[Symptom] = field(default_factory=list)
    auto_recoverable: bool = False
    recovery_action: Optional[str] = None
    explanation: str = ""  # Human-readable explanation
    expertise_level: str = "intermediate"  # novice, intermediate, expert

    def to_log_format(self) -> str:
        """Format diagnosis for logging."""
        lines = [
            f"[DIAGNOSIS] {self.symptom.message}",
            f"├── Likely cause: {self.likely_cause}",
        ]
        for ev in self.evidence[:3]:
            lines.append(f"├── Evidence: {ev}")
        if self.suggestions:
            lines.append(f"├── Suggested fix: {self.suggestions[0]}")
        if self.auto_recoverable:
            lines.append(f"└── Auto-recovery: {self.recovery_action}")
        else:
            lines.append(f"└── Confidence: {self.confidence:.0%}")
        return "\n".join(lines)


@dataclass
class DiagnosticRule:
    """A rule for diagnosing symptoms."""
    name: str
    pattern: str  # Regex pattern to match symptom message
    category: Category
    cause_template: str
    evidence_checks: List[Callable[[Dict], Optional[str]]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    auto_recoverable: bool = False
    recovery_action: Optional[str] = None
    confidence_base: float = 0.7
    expertise_level: str = "intermediate"


class DiagnosticEngine:
    """
    Intelligent diagnostic engine for mesh network operations.

    Features:
    - Rule-based symptom analysis (standalone)
    - Symptom correlation over time
    - Context-aware diagnosis
    - Auto-recovery suggestions
    - Expertise-level explanations
    - Persistent diagnostic history (SQLite)
    """

    # Symptom history retention
    HISTORY_MAX_SIZE = 1000
    HISTORY_MAX_AGE = timedelta(hours=24)

    # Correlation window for related symptoms
    CORRELATION_WINDOW = timedelta(minutes=5)

    # Auto-prune retention for diagnostic_history.db.
    DIAGNOSTIC_RETENTION_DAYS = 30
    DIAGNOSTIC_PRUNE_INTERVAL_SECONDS = 3600

    def __init__(self, persist_history: bool = True):
        """Initialize the diagnostic engine.

        Args:
            persist_history: If True, save diagnoses to SQLite for history tracking
        """
        self._rules: List[DiagnosticRule] = []
        self._symptom_history: deque = deque(maxlen=self.HISTORY_MAX_SIZE)
        self._diagnosis_history: deque = deque(maxlen=500)
        self._lock = threading.Lock()
        self._persist_history = persist_history

        # Callbacks for auto-recovery
        self._recovery_handlers: Dict[str, Callable] = {}

        # Persistent DB connection (reused to avoid open/close per operation)
        self._db_conn = None
        self._db_lock = threading.Lock()
        # Auto-prune cadence — diagnostic_history.db had no retention before
        # this; would grow unbounded over months. Hourly prune of rows older
        # than 30 days mirrors the messages.db / node_history.db pattern.
        self._last_diagnostic_prune_ts: float = 0.0

        # Load built-in rules
        from . import diagnostic_rules
        diagnostic_rules.load_mesh_rules(self)

        # Initialize persistent storage
        if self._persist_history:
            self._init_db()

        # Stats
        self._stats = {
            "symptoms_processed": 0,
            "diagnoses_made": 0,
            "auto_recoveries": 0,
            "correlations_found": 0,
        }

    def _get_db_path(self) -> Path:
        """Get path to diagnostic history database."""
        from utils.paths import get_real_user_home
        db_dir = get_real_user_home() / ".config" / "meshforge"
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / "diagnostic_history.db"

    def _get_connection(self):
        """Get persistent SQLite connection (creates if needed).

        Tuned via utils.db_helpers.connect_tuned (WAL + sync=NORMAL +
        64MB journal cap). Phase 1 of post-fleet-host-2026-04-26 closure.
        All access is serialized via _db_lock.
        """
        if self._db_conn is None:
            from utils.db_helpers import connect_tuned
            self._db_conn = connect_tuned(
                self._get_db_path(),
                check_same_thread=False,
            )
        return self._db_conn

    def _init_db(self):
        """Initialize SQLite database for persistent history."""
        try:
            with self._db_lock:
                conn = self._get_connection()
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS diagnoses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        symptom_message TEXT NOT NULL,
                        symptom_category TEXT NOT NULL,
                        symptom_severity TEXT NOT NULL,
                        symptom_source TEXT,
                        likely_cause TEXT NOT NULL,
                        confidence REAL,
                        evidence TEXT,
                        suggestions TEXT,
                        auto_recoverable BOOLEAN,
                        rule_name TEXT
                    )
                ''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_diagnoses_timestamp
                    ON diagnoses(timestamp DESC)
                ''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_diagnoses_category
                    ON diagnoses(symptom_category)
                ''')
                conn.commit()
            logger.debug(f"Diagnostic history DB initialized at {self._get_db_path()}")
        except Exception as e:
            logger.warning(f"Failed to initialize diagnostic history DB: {e}")
            self._persist_history = False

    def _save_diagnosis(self, symptom: 'Symptom', diagnosis: 'Diagnosis', rule_name: str = ""):
        """Save a diagnosis to persistent storage."""
        if not self._persist_history:
            return

        import json
        try:
            with self._db_lock:
                conn = self._get_connection()
                conn.execute('''
                    INSERT INTO diagnoses
                    (symptom_message, symptom_category, symptom_severity, symptom_source,
                     likely_cause, confidence, evidence, suggestions, auto_recoverable, rule_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symptom.message,
                    symptom.category.value,
                    symptom.severity.value,
                    symptom.source,
                    diagnosis.likely_cause,
                    diagnosis.confidence,
                    json.dumps(diagnosis.evidence),
                    json.dumps(diagnosis.suggestions),
                    diagnosis.auto_recoverable,
                    rule_name,
                ))
                conn.commit()
        except Exception as e:
            logger.debug(f"Failed to save diagnosis: {e}")
        # Outside the DB lock — _maybe_prune_diagnoses re-acquires it.
        self._maybe_prune_diagnoses()

    def _maybe_prune_diagnoses(self, now: Optional[float] = None) -> None:
        """Hourly auto-prune of diagnoses older than retention.

        Same pattern as NodeHistoryDB._maybe_prune. Idempotent and cheap
        when the cadence window hasn't elapsed."""
        if not self._persist_history:
            return
        if self.DIAGNOSTIC_PRUNE_INTERVAL_SECONDS <= 0:
            return
        if now is None:
            import time
            now = time.time()
        if now - self._last_diagnostic_prune_ts < self.DIAGNOSTIC_PRUNE_INTERVAL_SECONDS:
            return
        try:
            with self._db_lock:
                conn = self._get_connection()
                cursor = conn.execute(
                    "DELETE FROM diagnoses WHERE timestamp < datetime('now', ? || ' days')",
                    (f'-{self.DIAGNOSTIC_RETENTION_DAYS}',),
                )
                conn.commit()
                deleted = cursor.rowcount or 0
            self._last_diagnostic_prune_ts = now
            if deleted > 0:
                logger.info(
                    f"Diagnostic history auto-prune: deleted {deleted} rows older than {self.DIAGNOSTIC_RETENTION_DAYS}d"
                )
        except Exception as e:
            logger.debug(f"Diagnostic auto-prune failed: {e}")

    def get_history(self, limit: int = 50, category: Optional[Category] = None,
                    since_hours: int = 24) -> List[Dict]:
        """
        Get recent diagnostic history.

        Args:
            limit: Maximum number of diagnoses to return
            category: Filter by category (None = all)
            since_hours: Only return diagnoses from last N hours

        Returns:
            List of diagnosis records as dicts
        """
        if not self._persist_history:
            return []

        import sqlite3
        import json
        try:
            with self._db_lock:
                conn = self._get_connection()
                conn.row_factory = sqlite3.Row

                query = '''
                    SELECT * FROM diagnoses
                    WHERE timestamp > datetime('now', ? || ' hours')
                '''
                params = [f'-{since_hours}']

                if category:
                    query += " AND symptom_category = ?"
                    params.append(category.value)

                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)

                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

            results = []
            for row in rows:
                results.append({
                    'id': row['id'],
                    'timestamp': row['timestamp'],
                    'symptom_message': row['symptom_message'],
                    'symptom_category': row['symptom_category'],
                    'symptom_severity': row['symptom_severity'],
                    'symptom_source': row['symptom_source'],
                    'likely_cause': row['likely_cause'],
                    'confidence': row['confidence'],
                    'evidence': json.loads(row['evidence']) if row['evidence'] else [],
                    'suggestions': json.loads(row['suggestions']) if row['suggestions'] else [],
                    'auto_recoverable': bool(row['auto_recoverable']),
                    'rule_name': row['rule_name'],
                })
            return results
        except Exception as e:
            logger.warning(f"Failed to get diagnostic history: {e}")
            return []

    def get_recurring_issues(self, threshold: int = 3, hours: int = 24) -> List[Dict]:
        """
        Find recurring issues (same symptom/cause appearing multiple times).

        Args:
            threshold: Minimum occurrences to be considered recurring
            hours: Time window to search

        Returns:
            List of recurring issues with count
        """
        if not self._persist_history:
            return []

        import sqlite3
        try:
            with self._db_lock:
                conn = self._get_connection()
                conn.row_factory = sqlite3.Row

                cursor = conn.execute('''
                    SELECT
                        likely_cause,
                        symptom_category,
                        COUNT(*) as occurrence_count,
                        MAX(timestamp) as last_seen,
                        MIN(timestamp) as first_seen
                    FROM diagnoses
                    WHERE timestamp > datetime('now', ? || ' hours')
                    GROUP BY likely_cause, symptom_category
                    HAVING COUNT(*) >= ?
                    ORDER BY occurrence_count DESC
                ''', (f'-{hours}', threshold))

                rows = cursor.fetchall()

            return [
                {
                    'likely_cause': row['likely_cause'],
                    'category': row['symptom_category'],
                    'count': row['occurrence_count'],
                    'first_seen': row['first_seen'],
                    'last_seen': row['last_seen'],
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning(f"Failed to get recurring issues: {e}")
            return []

    def clear_history(self, older_than_days: int = 30) -> int:
        """
        Clear old diagnostic history.

        Args:
            older_than_days: Delete records older than this

        Returns:
            Number of records deleted
        """
        if not self._persist_history:
            return 0

        try:
            with self._db_lock:
                conn = self._get_connection()
                cursor = conn.execute('''
                    DELETE FROM diagnoses
                    WHERE timestamp < datetime('now', ? || ' days')
                ''', (f'-{older_than_days}',))
                deleted = cursor.rowcount
                conn.commit()
            return deleted
        except Exception as e:
            logger.warning(f"Failed to clear history: {e}")
            return 0

    def add_rule(self, rule: DiagnosticRule) -> None:
        """Add a diagnostic rule."""
        self._rules.append(rule)

    def register_recovery_handler(self, action: str, handler: Callable) -> None:
        """Register a handler for auto-recovery actions."""
        self._recovery_handlers[action] = handler

    def report_symptom(self, message: str, category: Category = Category.CONNECTIVITY,
                       severity: Severity = Severity.WARNING,
                       context: Optional[Dict] = None, source: str = "") -> Optional[Diagnosis]:
        """
        Report a symptom and get diagnosis.

        Args:
            message: The symptom message
            category: Category of the symptom
            severity: Severity level
            context: Additional context (port numbers, node IDs, etc.)
            source: Source component

        Returns:
            Diagnosis if a matching rule was found
        """
        symptom = Symptom(
            message=message,
            category=category,
            severity=severity,
            context=context or {},
            source=source,
        )

        with self._lock:
            self._symptom_history.append(symptom)
            self._stats["symptoms_processed"] += 1

        return self.diagnose(symptom)

    def diagnose(self, symptom: Symptom) -> Optional[Diagnosis]:
        """
        Analyze a symptom and produce a diagnosis.

        Args:
            symptom: The symptom to diagnose

        Returns:
            Diagnosis with cause, evidence, and suggestions
        """
        # Find matching rules (match by category only)
        matching_rules = []
        for rule in self._rules:
            if rule.category == symptom.category:
                if re.search(rule.pattern, symptom.message, re.IGNORECASE):
                    matching_rules.append(rule)

        if not matching_rules:
            return None

        # Use the highest confidence matching rule
        best_rule = max(matching_rules, key=lambda r: r.confidence_base)

        # Build evidence
        evidence = []
        confidence = best_rule.confidence_base

        # Check evidence functions
        for check in best_rule.evidence_checks:
            try:
                result = check(symptom.context)
                if result:
                    evidence.append(result)
                    confidence = min(1.0, confidence + 0.05)
            except Exception as e:
                logger.debug(f"Evidence check failed: {e}")

        # Find related symptoms
        related = self._find_related_symptoms(symptom)
        if related:
            with self._lock:
                self._stats["correlations_found"] += 1
            confidence = min(1.0, confidence + 0.1 * len(related))

        # Build diagnosis
        diagnosis = Diagnosis(
            symptom=symptom,
            likely_cause=best_rule.cause_template,
            confidence=confidence,
            evidence=evidence,
            suggestions=list(best_rule.suggestions),
            related_symptoms=related,
            auto_recoverable=best_rule.auto_recoverable,
            recovery_action=best_rule.recovery_action,
            explanation=self._build_explanation(symptom, best_rule, related),
            expertise_level=best_rule.expertise_level,
        )

        with self._lock:
            self._diagnosis_history.append(diagnosis)
            self._stats["diagnoses_made"] += 1

        # Save to persistent history
        self._save_diagnosis(symptom, diagnosis, rule_name=best_rule.name)

        # Log the diagnosis
        logger.info(diagnosis.to_log_format())

        # Attempt auto-recovery if applicable
        if diagnosis.auto_recoverable and diagnosis.recovery_action:
            self._attempt_recovery(diagnosis)

        return diagnosis

    def _find_related_symptoms(self, symptom: Symptom) -> List[Symptom]:
        """Find symptoms related in time and category."""
        related = []
        cutoff = symptom.timestamp - self.CORRELATION_WINDOW

        with self._lock:
            for s in self._symptom_history:
                if s == symptom:
                    continue
                if s.timestamp >= cutoff:
                    # Same category symptoms are related
                    if s.category == symptom.category:
                        related.append(s)

        return related[:5]  # Limit to 5 most relevant

    def _build_explanation(self, symptom: Symptom, rule: DiagnosticRule,
                          related: List[Symptom]) -> str:
        """Build a human-readable explanation."""
        parts = [f"The symptom '{symptom.message}' indicates {rule.cause_template.lower()}."]

        if related:
            parts.append(f"This may be related to {len(related)} other recent issue(s).")

        if rule.suggestions:
            parts.append(f"The most likely fix is: {rule.suggestions[0]}")

        if rule.auto_recoverable:
            parts.append(f"MeshForge will automatically attempt recovery.")

        return " ".join(parts)

    def _attempt_recovery(self, diagnosis: Diagnosis) -> None:
        """Attempt automatic recovery for a diagnosis."""
        if diagnosis.recovery_action in self._recovery_handlers:
            try:
                handler = self._recovery_handlers[diagnosis.recovery_action]
                handler(diagnosis)
                with self._lock:
                    self._stats["auto_recoveries"] += 1
                logger.info(f"Auto-recovery initiated: {diagnosis.recovery_action}")
            except Exception as e:
                logger.error(f"Auto-recovery failed: {e}")

    def get_recent_diagnoses(self, limit: int = 10,
                            category: Optional[Category] = None) -> List[Diagnosis]:
        """Get recent diagnoses, optionally filtered by category."""
        with self._lock:
            diagnoses = list(self._diagnosis_history)

        if category:
            diagnoses = [d for d in diagnoses if d.symptom.category == category]

        return diagnoses[-limit:]

    def get_health_summary(self) -> Dict[str, Any]:
        """Get a summary of system health based on recent symptoms."""
        with self._lock:
            recent = [s for s in self._symptom_history
                     if s.timestamp > datetime.now() - timedelta(hours=1)]

        # Count by category and severity
        by_category = {}
        by_severity = {}

        for s in recent:
            by_category[s.category.value] = by_category.get(s.category.value, 0) + 1
            by_severity[s.severity.value] = by_severity.get(s.severity.value, 0) + 1

        # Determine overall health
        critical_count = by_severity.get("critical", 0)
        error_count = by_severity.get("error", 0)
        warning_count = by_severity.get("warning", 0)

        if critical_count > 0:
            health = "critical"
        elif error_count > 2:
            health = "degraded"
        elif warning_count > 5:
            health = "warning"
        else:
            health = "healthy"

        return {
            "overall_health": health,
            "symptoms_last_hour": len(recent),
            "by_category": by_category,
            "by_severity": by_severity,
            "stats": dict(self._stats),
        }

    def get_stats(self) -> Dict[str, int]:
        """Get diagnostic engine statistics."""
        return dict(self._stats)

    def check_predictive_alerts(self) -> List[Diagnosis]:
        """
        Check for predictive alerts from analytics data.

        This integrates the PredictiveAnalyzer to proactively detect
        network health issues before they become critical.

        Returns:
            List of Diagnosis objects for any predicted issues

        API Contract:
            - Returns list (may be empty if no predictions or insufficient data)
            - Thread-safe
            - Does not raise exceptions (logs failures)
            - Tests: tests/test_predictive_analytics.py
        """
        diagnoses: List[Diagnosis] = []

        if not _HAS_ANALYTICS:
            logger.debug("Predictive analytics not available")
            return diagnoses

        try:
            analyzer = _get_predictive_analyzer()
            alerts = analyzer.analyze_all()

            for alert in alerts:
                # Convert PredictiveAlert to Diagnosis
                severity = {
                    'info': Severity.INFO,
                    'warning': Severity.WARNING,
                    'critical': Severity.CRITICAL,
                }.get(alert.severity, Severity.WARNING)

                symptom = Symptom(
                    message=alert.message,
                    category=Category.PREDICTIVE,
                    severity=severity,
                    context={
                        'alert_type': alert.alert_type,
                        'predicted_time_hours': alert.predicted_time_hours,
                        'affected_nodes': alert.affected_nodes,
                    },
                    source='predictive_analyzer',
                )

                # Build cause based on alert type
                cause_map = {
                    'link_snr_degradation': "Link signal quality is degrading over time",
                    'link_packet_loss': "Link experiencing significant packet loss",
                    'metric_critical': "Network metric has reached critical threshold",
                    'metric_degradation': "Network metric showing degradation trend",
                    'node_count_decline': "Network losing active nodes",
                }
                cause = cause_map.get(alert.alert_type, "Predicted network issue detected")

                # Add time prediction to explanation if available
                explanation = f"PREDICTIVE ALERT: {alert.message}"
                if alert.predicted_time_hours:
                    if alert.predicted_time_hours < 24:
                        explanation += f" Expected to reach critical in ~{alert.predicted_time_hours:.0f} hours."
                    else:
                        days = alert.predicted_time_hours / 24
                        explanation += f" Expected to reach critical in ~{days:.1f} days."

                diagnosis = Diagnosis(
                    symptom=symptom,
                    likely_cause=cause,
                    confidence=alert.confidence,
                    evidence=alert.evidence,
                    suggestions=alert.suggestions,
                    related_symptoms=[],
                    auto_recoverable=False,
                    recovery_action=None,
                    explanation=explanation,
                    expertise_level="intermediate",
                )

                diagnoses.append(diagnosis)

                # Save to persistent history
                self._save_diagnosis(symptom, diagnosis, rule_name=f"predictive_{alert.alert_type}")

                # Log the predictive alert
                logger.info(f"[PREDICTIVE] {diagnosis.to_log_format()}")

        except Exception as e:
            logger.warning(f"Failed to check predictive alerts: {e}")

        return diagnoses

    def get_network_forecast(self, hours_ahead: int = 24) -> Dict[str, Any]:
        """
        Get a network health forecast.

        Args:
            hours_ahead: How many hours to forecast

        Returns:
            Dict with forecast data or error info
        """
        if not _HAS_ANALYTICS:
            return {'has_forecast': False, 'reason': 'Analytics module not available'}

        try:
            analyzer = _get_predictive_analyzer()
            return analyzer.get_network_forecast(hours_ahead)
        except Exception as e:
            return {'has_forecast': False, 'reason': str(e)}


# Singleton instance
_engine: Optional[DiagnosticEngine] = None
_engine_lock = threading.Lock()


def get_diagnostic_engine() -> DiagnosticEngine:
    """Get the global diagnostic engine instance."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = DiagnosticEngine()
        return _engine


def diagnose(message: str, category: Category = Category.CONNECTIVITY,
             severity: Severity = Severity.WARNING,
             context: Optional[Dict] = None, source: str = "") -> Optional[Diagnosis]:
    """
    Convenience function to diagnose a symptom.

    Usage:
        from utils.diagnostic_engine import diagnose, Category, Severity

        diagnosis = diagnose(
            "Connection refused to meshtasticd",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR
        )
        if diagnosis:
            print(diagnosis.explanation)

    API Contract:
        - Returns Diagnosis object if symptom matched, None otherwise
        - Callers MUST check 'if diagnosis:' before accessing attributes
        - Diagnosis.likely_cause: str explaining the probable cause
        - Diagnosis.suggestions: List[str] of actionable fixes (may be empty)
        - Diagnosis.auto_recovery: Optional[str] recovery action
        - Thread-safe (uses singleton engine)
        - Tests: tests/test_diagnostics.py
    """
    engine = get_diagnostic_engine()
    return engine.report_symptom(message, category, severity, context, source)
