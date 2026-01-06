"""
MeshForge Network Diagnostics System

Comprehensive diagnostic and logging infrastructure providing:
- Network event logging (connections, disconnections, errors)
- Security audit logging (login attempts, policy violations)
- Performance monitoring (latency, throughput, resource usage)
- Health checks for all subsystems
- Diagnostic report generation

Design Principles:
1. User-visible: All diagnostics accessible through UI
2. Actionable: Every issue includes fix suggestions
3. Auditable: Complete trail for compliance/forensics
4. Real-time: Live monitoring of network state
5. Comprehensive: Cover RNS, Meshtastic, system health
"""

import os
import json
import socket
import threading
import subprocess
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from collections import deque
import logging

logger = logging.getLogger(__name__)


def _get_real_user_home() -> Path:
    """Get real user's home even when running with sudo."""
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        return Path(f'/home/{sudo_user}')
    return Path.home()


# Diagnostic data directory
DIAG_DIR = _get_real_user_home() / ".config" / "meshforge" / "diagnostics"


class EventCategory(Enum):
    """Categories for diagnostic events."""
    NETWORK = "network"      # Connection events, data transfer
    SECURITY = "security"    # Auth, access control, suspicious activity
    PERFORMANCE = "perf"     # Latency, throughput, resource usage
    SYSTEM = "system"        # Service status, hardware, config
    ERROR = "error"          # Failures, exceptions
    AUDIT = "audit"          # User actions, config changes


class EventSeverity(Enum):
    """Severity levels for events."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class HealthStatus(Enum):
    """Health status for subsystems."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class DiagnosticEvent:
    """A single diagnostic event."""
    timestamp: datetime
    category: EventCategory
    severity: EventSeverity
    source: str  # Component that generated event (e.g., "rns", "meshtastic", "hamclock")
    message: str
    details: Optional[Dict[str, Any]] = None
    fix_hint: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "category": self.category.value,
            "severity": self.severity.value,
            "source": self.source,
            "message": self.message,
            "details": self.details,
            "fix_hint": self.fix_hint
        }

    def to_log_line(self) -> str:
        """Format as single log line."""
        sev = self.severity.value.upper()[:4]
        cat = self.category.value[:4].upper()
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return f"{ts} | {sev} | {cat} | {self.source}: {self.message}"


@dataclass
class HealthCheck:
    """Result of a health check."""
    name: str
    status: HealthStatus
    message: str
    last_check: datetime
    details: Optional[Dict[str, Any]] = None
    fix_hint: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "last_check": self.last_check.isoformat(),
            "details": self.details,
            "fix_hint": self.fix_hint
        }


class NetworkDiagnostics:
    """
    Central diagnostic and monitoring system for MeshForge.

    Provides:
    - Event logging with categories and severity
    - Real-time health monitoring
    - Audit trail for security compliance
    - Diagnostic report generation
    """

    # Singleton instance
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Event storage - keep last 1000 events in memory
        self._events: deque = deque(maxlen=1000)
        self._events_lock = threading.Lock()

        # Health check results
        self._health: Dict[str, HealthCheck] = {}
        self._health_lock = threading.Lock()

        # Callbacks for real-time updates
        self._event_callbacks: List[Callable[[DiagnosticEvent], None]] = []
        self._health_callbacks: List[Callable[[str, HealthCheck], None]] = []

        # Ensure diagnostic directory exists
        self._ensure_dirs()

        # Start background health monitor
        self._monitor_running = True
        self._monitor_thread = threading.Thread(target=self._health_monitor_loop, daemon=True)
        self._monitor_thread.start()

        logger.info("NetworkDiagnostics initialized")

    def _ensure_dirs(self):
        """Create diagnostic directories with proper ownership."""
        try:
            DIAG_DIR.mkdir(parents=True, exist_ok=True)
            # Fix ownership if running as root
            self._fix_ownership(DIAG_DIR)
        except Exception as e:
            logger.error(f"Failed to create diagnostic directory: {e}")

    def _fix_ownership(self, path: Path):
        """Fix directory ownership for sudo user."""
        import pwd
        sudo_user = os.environ.get('SUDO_USER')
        if not sudo_user or sudo_user == 'root':
            return
        try:
            pw = pwd.getpwnam(sudo_user)
            os.chown(path, pw.pw_uid, pw.pw_gid)
        except (KeyError, OSError):
            pass

    # ==================== Event Logging ====================

    def log_event(
        self,
        category: EventCategory,
        severity: EventSeverity,
        source: str,
        message: str,
        details: Optional[Dict] = None,
        fix_hint: Optional[str] = None
    ) -> DiagnosticEvent:
        """Log a diagnostic event."""
        event = DiagnosticEvent(
            timestamp=datetime.now(),
            category=category,
            severity=severity,
            source=source,
            message=message,
            details=details,
            fix_hint=fix_hint
        )

        with self._events_lock:
            self._events.append(event)

        # Write to log file
        self._write_event_to_file(event)

        # Notify callbacks
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

        # Also log to standard logger
        log_level = {
            EventSeverity.DEBUG: logging.DEBUG,
            EventSeverity.INFO: logging.INFO,
            EventSeverity.WARNING: logging.WARNING,
            EventSeverity.ERROR: logging.ERROR,
            EventSeverity.CRITICAL: logging.CRITICAL,
        }.get(severity, logging.INFO)

        logger.log(log_level, f"[{category.value}] {source}: {message}")

        return event

    def _write_event_to_file(self, event: DiagnosticEvent):
        """Persist event to log file."""
        try:
            log_file = DIAG_DIR / f"events_{datetime.now().strftime('%Y%m%d')}.log"
            with open(log_file, 'a') as f:
                f.write(event.to_log_line() + "\n")
            self._fix_ownership(log_file)
        except Exception as e:
            logger.error(f"Failed to write event to file: {e}")

    # Convenience methods for common event types
    def log_connection(self, source: str, target: str, success: bool, error: str = None):
        """Log a connection event."""
        if success:
            self.log_event(
                EventCategory.NETWORK, EventSeverity.INFO, source,
                f"Connected to {target}"
            )
        else:
            self.log_event(
                EventCategory.NETWORK, EventSeverity.ERROR, source,
                f"Failed to connect to {target}: {error}",
                fix_hint=self._get_connection_fix_hint(source, error)
            )

    def log_disconnection(self, source: str, target: str, reason: str = "unknown"):
        """Log a disconnection event."""
        self.log_event(
            EventCategory.NETWORK, EventSeverity.WARNING, source,
            f"Disconnected from {target}: {reason}"
        )

    def log_security_event(self, source: str, message: str, severity: EventSeverity = EventSeverity.WARNING):
        """Log a security-related event."""
        self.log_event(EventCategory.SECURITY, severity, source, message)

    def log_performance(self, source: str, metric: str, value: Any, unit: str = ""):
        """Log a performance metric."""
        self.log_event(
            EventCategory.PERFORMANCE, EventSeverity.DEBUG, source,
            f"{metric}: {value}{unit}",
            details={"metric": metric, "value": value, "unit": unit}
        )

    def log_config_change(self, source: str, setting: str, old_value: Any, new_value: Any):
        """Log a configuration change for audit trail."""
        self.log_event(
            EventCategory.AUDIT, EventSeverity.INFO, source,
            f"Config changed: {setting} = {new_value} (was: {old_value})",
            details={"setting": setting, "old": old_value, "new": new_value}
        )

    def _get_connection_fix_hint(self, source: str, error: str) -> str:
        """Get fix suggestion based on connection error."""
        error_lower = (error or "").lower()

        if "address already in use" in error_lower:
            return "Another process is using this port. Check with: sudo lsof -i :PORT"
        elif "connection refused" in error_lower:
            return f"Service not running. Start the {source} service first."
        elif "timeout" in error_lower:
            return "Network unreachable or service not responding. Check firewall."
        elif "permission denied" in error_lower:
            return "Insufficient permissions. Try running with sudo."
        elif "no such file" in error_lower or "not found" in error_lower:
            return f"{source} may not be installed. Check installation."
        else:
            return f"Check {source} configuration and logs for details."

    # ==================== Health Monitoring ====================

    def update_health(
        self,
        name: str,
        status: HealthStatus,
        message: str,
        details: Optional[Dict] = None,
        fix_hint: Optional[str] = None
    ):
        """Update health status of a subsystem."""
        check = HealthCheck(
            name=name,
            status=status,
            message=message,
            last_check=datetime.now(),
            details=details,
            fix_hint=fix_hint
        )

        with self._health_lock:
            old_status = self._health.get(name)
            self._health[name] = check

        # Log status changes
        if old_status and old_status.status != status:
            self.log_event(
                EventCategory.SYSTEM,
                EventSeverity.WARNING if status != HealthStatus.HEALTHY else EventSeverity.INFO,
                name,
                f"Health status changed: {old_status.status.value} -> {status.value}"
            )

        # Notify callbacks
        for cb in self._health_callbacks:
            try:
                cb(name, check)
            except Exception as e:
                logger.error(f"Health callback error: {e}")

    def get_health(self, name: str = None) -> Dict[str, HealthCheck]:
        """Get health status. If name provided, get single; otherwise get all."""
        with self._health_lock:
            if name:
                return {name: self._health.get(name)} if name in self._health else {}
            return dict(self._health)

    def get_overall_health(self) -> HealthStatus:
        """Get overall system health."""
        with self._health_lock:
            if not self._health:
                return HealthStatus.UNKNOWN

            statuses = [h.status for h in self._health.values()]

            if any(s == HealthStatus.UNHEALTHY for s in statuses):
                return HealthStatus.UNHEALTHY
            elif any(s == HealthStatus.DEGRADED for s in statuses):
                return HealthStatus.DEGRADED
            elif all(s == HealthStatus.HEALTHY for s in statuses):
                return HealthStatus.HEALTHY
            else:
                return HealthStatus.UNKNOWN

    def _health_monitor_loop(self):
        """Background thread for periodic health checks."""
        while self._monitor_running:
            try:
                self._run_health_checks()
            except Exception as e:
                logger.error(f"Health monitor error: {e}")
            time.sleep(30)  # Check every 30 seconds

    def _run_health_checks(self):
        """Run all health checks."""
        # Check meshtasticd
        self._check_meshtasticd_health()
        # Check RNS
        self._check_rns_health()
        # Check system resources
        self._check_system_health()
        # Check network connectivity
        self._check_network_health()

    def _check_meshtasticd_health(self):
        """Check meshtasticd service health."""
        try:
            # Check if port 4403 is open
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()

            if result == 0:
                self.update_health(
                    "meshtasticd", HealthStatus.HEALTHY,
                    "Service running on port 4403"
                )
            else:
                self.update_health(
                    "meshtasticd", HealthStatus.UNHEALTHY,
                    "Service not responding on port 4403",
                    fix_hint="Start meshtasticd: sudo systemctl start meshtasticd"
                )
        except Exception as e:
            self.update_health(
                "meshtasticd", HealthStatus.UNKNOWN,
                f"Check failed: {e}"
            )

    def _check_rns_health(self):
        """Check RNS/Reticulum health."""
        try:
            # Check if rnsd is running
            result = subprocess.run(
                ['pgrep', '-f', 'rnsd'],
                capture_output=True, timeout=5
            )
            rnsd_running = result.returncode == 0

            # Check if RNS module is available
            try:
                import RNS
                rns_installed = True
            except ImportError:
                rns_installed = False

            if rnsd_running:
                self.update_health(
                    "rns", HealthStatus.HEALTHY,
                    "RNS daemon running"
                )
            elif rns_installed:
                self.update_health(
                    "rns", HealthStatus.DEGRADED,
                    "RNS installed but rnsd not running",
                    fix_hint="Start RNS daemon: rnsd"
                )
            else:
                self.update_health(
                    "rns", HealthStatus.UNHEALTHY,
                    "RNS not installed",
                    fix_hint="Install RNS: pip install rns"
                )
        except Exception as e:
            self.update_health("rns", HealthStatus.UNKNOWN, f"Check failed: {e}")

    def _check_system_health(self):
        """Check system resource health."""
        try:
            # Check disk space
            stat = os.statvfs('/')
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)

            if free_gb < 1:
                self.update_health(
                    "disk", HealthStatus.UNHEALTHY,
                    f"Low disk space: {free_gb:.1f} GB free",
                    fix_hint="Free up disk space or expand storage"
                )
            elif free_gb < 5:
                self.update_health(
                    "disk", HealthStatus.DEGRADED,
                    f"Disk space getting low: {free_gb:.1f} GB free"
                )
            else:
                self.update_health(
                    "disk", HealthStatus.HEALTHY,
                    f"Disk space OK: {free_gb:.1f} GB free"
                )

            # Check memory
            with open('/proc/meminfo', 'r') as f:
                meminfo = f.read()
            mem_total = int([l for l in meminfo.split('\n') if 'MemTotal' in l][0].split()[1])
            mem_avail = int([l for l in meminfo.split('\n') if 'MemAvailable' in l][0].split()[1])
            mem_pct = (mem_avail / mem_total) * 100

            if mem_pct < 10:
                self.update_health(
                    "memory", HealthStatus.UNHEALTHY,
                    f"Low memory: {mem_pct:.0f}% available",
                    fix_hint="Close unused applications or add more RAM"
                )
            elif mem_pct < 25:
                self.update_health(
                    "memory", HealthStatus.DEGRADED,
                    f"Memory getting low: {mem_pct:.0f}% available"
                )
            else:
                self.update_health(
                    "memory", HealthStatus.HEALTHY,
                    f"Memory OK: {mem_pct:.0f}% available"
                )

        except Exception as e:
            self.update_health("system", HealthStatus.UNKNOWN, f"Check failed: {e}")

    def _check_network_health(self):
        """Check network connectivity health."""
        try:
            # Check internet connectivity
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            result = sock.connect_ex(('8.8.8.8', 53))
            sock.close()

            if result == 0:
                self.update_health(
                    "internet", HealthStatus.HEALTHY,
                    "Internet connectivity OK"
                )
            else:
                self.update_health(
                    "internet", HealthStatus.UNHEALTHY,
                    "No internet connectivity",
                    fix_hint="Check network configuration and cables"
                )
        except Exception as e:
            self.update_health(
                "internet", HealthStatus.UNKNOWN,
                f"Check failed: {e}"
            )

    # ==================== Event Queries ====================

    def get_events(
        self,
        category: EventCategory = None,
        severity: EventSeverity = None,
        source: str = None,
        since: datetime = None,
        limit: int = 100
    ) -> List[DiagnosticEvent]:
        """Query events with optional filters."""
        with self._events_lock:
            events = list(self._events)

        # Apply filters
        if category:
            events = [e for e in events if e.category == category]
        if severity:
            events = [e for e in events if e.severity == severity]
        if source:
            events = [e for e in events if e.source == source]
        if since:
            events = [e for e in events if e.timestamp >= since]

        # Return most recent first, limited
        return sorted(events, key=lambda e: e.timestamp, reverse=True)[:limit]

    def get_errors(self, since: datetime = None) -> List[DiagnosticEvent]:
        """Get error and critical events."""
        events = self.get_events(since=since)
        return [e for e in events if e.severity in (EventSeverity.ERROR, EventSeverity.CRITICAL)]

    # ==================== Callbacks ====================

    def register_event_callback(self, callback: Callable[[DiagnosticEvent], None]):
        """Register callback for real-time event notifications."""
        self._event_callbacks.append(callback)

    def register_health_callback(self, callback: Callable[[str, HealthCheck], None]):
        """Register callback for health status changes."""
        self._health_callbacks.append(callback)

    # ==================== Report Generation ====================

    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive diagnostic report."""
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)

        # Get recent events
        recent_events = self.get_events(since=hour_ago)
        recent_errors = [e for e in recent_events if e.severity in (EventSeverity.ERROR, EventSeverity.CRITICAL)]

        # Get health status
        health = self.get_health()
        overall = self.get_overall_health()

        # Count events by category
        category_counts = {}
        for cat in EventCategory:
            category_counts[cat.value] = len([e for e in recent_events if e.category == cat])

        report = {
            "generated_at": now.isoformat(),
            "overall_health": overall.value,
            "summary": {
                "total_events_1h": len(recent_events),
                "errors_1h": len(recent_errors),
                "events_by_category": category_counts,
            },
            "health_checks": {name: check.to_dict() for name, check in health.items()},
            "recent_errors": [e.to_dict() for e in recent_errors[:20]],
            "recommendations": self._generate_recommendations(health, recent_errors)
        }

        return report

    def _generate_recommendations(
        self,
        health: Dict[str, HealthCheck],
        errors: List[DiagnosticEvent]
    ) -> List[str]:
        """Generate actionable recommendations based on diagnostics."""
        recommendations = []

        # Health-based recommendations
        for name, check in health.items():
            if check.status == HealthStatus.UNHEALTHY and check.fix_hint:
                recommendations.append(f"[{name}] {check.fix_hint}")
            elif check.status == HealthStatus.DEGRADED and check.fix_hint:
                recommendations.append(f"[{name}] Consider: {check.fix_hint}")

        # Error-based recommendations
        error_sources = set(e.source for e in errors)
        for source in error_sources:
            source_errors = [e for e in errors if e.source == source]
            if len(source_errors) > 5:
                recommendations.append(
                    f"[{source}] Multiple errors detected ({len(source_errors)}). Review logs."
                )
            # Add specific fix hints
            for err in source_errors:
                if err.fix_hint and err.fix_hint not in recommendations:
                    recommendations.append(f"[{source}] {err.fix_hint}")

        return recommendations[:10]  # Limit to top 10

    def save_report(self, filename: str = None) -> Path:
        """Save diagnostic report to file."""
        if not filename:
            filename = f"diag_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        report_path = DIAG_DIR / filename
        report = self.generate_report()

        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)

        self._fix_ownership(report_path)
        return report_path

    # ==================== Cleanup ====================

    def shutdown(self):
        """Stop background monitoring."""
        self._monitor_running = False
        if self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)


# Global instance accessor
def get_diagnostics() -> NetworkDiagnostics:
    """Get the singleton NetworkDiagnostics instance."""
    return NetworkDiagnostics()
