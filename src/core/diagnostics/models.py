"""
Unified Diagnostic Data Models

These data structures are used across ALL diagnostic implementations:
- CLI, GTK, Web all use the same models
- JSON serialization built-in for API/storage
- Thread-safe by design (immutable dataclasses)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Callable


# === Status Enums ===

class CheckStatus(Enum):
    """Status of a single diagnostic check."""
    PASS = "pass"       # Check passed successfully
    FAIL = "fail"       # Check failed - action required
    WARN = "warn"       # Check passed with warnings - review recommended
    SKIP = "skip"       # Check skipped - not applicable


class HealthStatus(Enum):
    """Aggregate health status for subsystems."""
    HEALTHY = "healthy"       # All checks passing
    DEGRADED = "degraded"     # Some checks warning/failing but functional
    UNHEALTHY = "unhealthy"   # Critical failures
    UNKNOWN = "unknown"       # Unable to determine


class EventSeverity(Enum):
    """Severity levels for diagnostic events."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class CheckCategory(Enum):
    """Categories for diagnostic checks."""
    SERVICES = "services"       # meshtasticd, rnsd, nomadnet, bluetooth
    NETWORK = "network"         # internet, DNS, gateway, MQTT, ports
    RNS = "rns"                 # installed, config, daemon, port, interface
    MESHTASTIC = "meshtastic"   # library, CLI, TCP, web UI
    SERIAL = "serial"           # ports, permissions, devices
    HARDWARE = "hardware"       # SPI, I2C, GPIO, LoRa, temp, SDR
    SYSTEM = "system"           # Python, packages, memory, disk, CPU
    HAM_RADIO = "ham_radio"     # callsign, NomadNet identity
    LOGS = "logs"               # log analysis, error counts


# === Core Result Types ===

@dataclass
class CheckResult:
    """
    Result of a single diagnostic check.

    This is the fundamental unit of diagnostic output.
    Every check produces exactly one CheckResult.

    Attributes:
        name: Human-readable check name (e.g., "meshtasticd service")
        category: Which category this check belongs to
        status: PASS, FAIL, WARN, or SKIP
        message: Short description of result
        fix_hint: Actionable fix suggestion (if failed)
        details: Additional structured data
        duration_ms: How long the check took
        timestamp: When the check was run
    """
    name: str
    category: CheckCategory
    status: CheckStatus
    message: str
    fix_hint: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def is_ok(self) -> bool:
        """Return True if check passed or is just a warning/skip."""
        return self.status in (CheckStatus.PASS, CheckStatus.WARN, CheckStatus.SKIP)

    def is_failure(self) -> bool:
        """Return True if check failed."""
        return self.status == CheckStatus.FAIL

    def to_dict(self) -> dict:
        """Serialize for API/JSON output."""
        return {
            "name": self.name,
            "category": self.category.value,
            "status": self.status.value,
            "message": self.message,
            "fix_hint": self.fix_hint,
            "details": self.details,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CheckResult':
        """Deserialize from dict."""
        return cls(
            name=data['name'],
            category=CheckCategory(data['category']),
            status=CheckStatus(data['status']),
            message=data['message'],
            fix_hint=data.get('fix_hint'),
            details=data.get('details'),
            duration_ms=data.get('duration_ms'),
            timestamp=datetime.fromisoformat(data['timestamp']) if data.get('timestamp') else None
        )


@dataclass
class SubsystemHealth:
    """
    Health status of a subsystem (e.g., 'meshtastic', 'rns').

    Aggregates multiple CheckResults into overall health status.

    Attributes:
        name: Subsystem identifier
        status: Overall health (HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN)
        message: Summary message
        checks: List of all checks in this subsystem
        last_check: When health was last evaluated
        fix_hint: First actionable fix from failed checks
    """
    name: str
    status: HealthStatus
    message: str
    checks: List[CheckResult] = field(default_factory=list)
    last_check: datetime = field(default_factory=datetime.now)
    fix_hint: Optional[str] = None

    @property
    def pass_count(self) -> int:
        """Number of passing checks."""
        return sum(1 for c in self.checks if c.status == CheckStatus.PASS)

    @property
    def fail_count(self) -> int:
        """Number of failing checks."""
        return sum(1 for c in self.checks if c.status == CheckStatus.FAIL)

    @property
    def warn_count(self) -> int:
        """Number of warning checks."""
        return sum(1 for c in self.checks if c.status == CheckStatus.WARN)

    def to_dict(self) -> dict:
        """Serialize for API/JSON output."""
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "check_count": len(self.checks),
            "passed": self.pass_count,
            "failed": self.fail_count,
            "warnings": self.warn_count,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "fix_hint": self.fix_hint,
            "checks": [c.to_dict() for c in self.checks]
        }


@dataclass
class DiagnosticEvent:
    """
    A timestamped diagnostic event for logging/audit trail.

    Events are used for:
    - Tracking state changes
    - Logging errors and warnings
    - Audit trail for troubleshooting

    Attributes:
        timestamp: When the event occurred
        severity: DEBUG, INFO, WARNING, ERROR, CRITICAL
        source: Component/module that generated the event
        message: Human-readable event description
        category: Optional check category association
        details: Additional structured data
        fix_hint: Actionable fix suggestion
    """
    timestamp: datetime
    severity: EventSeverity
    source: str
    message: str
    category: Optional[CheckCategory] = None
    details: Optional[Dict[str, Any]] = None
    fix_hint: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize for API/JSON output."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity.value,
            "source": self.source,
            "message": self.message,
            "category": self.category.value if self.category else None,
            "details": self.details,
            "fix_hint": self.fix_hint
        }

    def to_log_line(self) -> str:
        """Format as single log line for file output."""
        sev = self.severity.value.upper()[:4]
        cat = self.category.value[:4].upper() if self.category else "----"
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return f"{ts} | {sev} | {cat} | {self.source}: {self.message}"

    @classmethod
    def from_dict(cls, data: dict) -> 'DiagnosticEvent':
        """Deserialize from dict."""
        return cls(
            timestamp=datetime.fromisoformat(data['timestamp']),
            severity=EventSeverity(data['severity']),
            source=data['source'],
            message=data['message'],
            category=CheckCategory(data['category']) if data.get('category') else None,
            details=data.get('details'),
            fix_hint=data.get('fix_hint')
        )


@dataclass
class DiagnosticReport:
    """
    Complete diagnostic report.

    Contains all check results, health status, events, and recommendations.
    Can be serialized to JSON for storage or API response.
    """
    generated_at: datetime
    overall_health: HealthStatus
    subsystems: Dict[str, SubsystemHealth]
    all_checks: List[CheckResult]
    recent_events: List[DiagnosticEvent]
    recommendations: List[str]
    summary: Dict[str, int]  # counts by status

    def to_dict(self) -> dict:
        """Serialize for API/JSON output."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "overall_health": self.overall_health.value,
            "summary": self.summary,
            "subsystems": {k: v.to_dict() for k, v in self.subsystems.items()},
            "checks": [c.to_dict() for c in self.all_checks],
            "recent_events": [e.to_dict() for e in self.recent_events],
            "recommendations": self.recommendations
        }

    @property
    def is_healthy(self) -> bool:
        """True if overall health is HEALTHY."""
        return self.overall_health == HealthStatus.HEALTHY

    @property
    def has_failures(self) -> bool:
        """True if any checks failed."""
        return self.summary.get('failed', 0) > 0


# === Callback Types ===

CheckCallback = Callable[[CheckResult], None]
HealthCallback = Callable[[str, SubsystemHealth], None]
EventCallback = Callable[[DiagnosticEvent], None]
ProgressCallback = Callable[[str, int, int], None]  # (category, current, total)
