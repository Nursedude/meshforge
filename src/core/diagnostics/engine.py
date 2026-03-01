"""
Unified Diagnostic Engine for MeshForge

This is the single source of truth for ALL diagnostics.
CLI, GTK, Web, and TUI all consume this engine.

Design Principles:
1. Singleton pattern - one engine per process
2. Thread-safe - supports concurrent UI updates
3. Callback-driven - real-time notifications for GUI/Web
4. Persistent logging - events written to disk
5. Category-based - checks organized by subsystem

Usage:
    engine = DiagnosticEngine.get_instance()
    engine.register_check_callback(my_handler)
    results = engine.run_all()
"""

import os
import threading
import time
import json
import logging
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .models import (
    CheckResult, CheckStatus, CheckCategory,
    SubsystemHealth, HealthStatus,
    DiagnosticEvent, EventSeverity,
    DiagnosticReport,
    CheckCallback, HealthCallback, EventCallback, ProgressCallback
)

# Import check implementations from checks module
from .checks import (
    # Services
    check_service,
    check_process,
    check_service_logs,
    # Network
    check_tcp_port,
    check_internet,
    check_dns,
    # RNS
    check_rns_installed,
    check_rns_config,
    check_rns_port,
    check_rns_storage_permissions,
    check_meshtastic_interface_file,
    # Meshtastic
    check_meshtastic_installed,
    check_meshtastic_cli,
    check_meshtastic_connection,
    find_serial_devices,
    # Serial
    check_serial_ports,
    check_dialout_group,
    # Hardware
    check_spi,
    check_i2c,
    check_temperature,
    check_sdr,
    # System
    check_python_version,
    check_pip_packages,
    check_memory,
    check_disk_space,
    check_cpu_load,
    # HAM Radio
    check_callsign,
)

logger = logging.getLogger(__name__)


# Import centralized path utility for sudo compatibility
from utils.paths import get_real_user_home
from utils.safe_import import safe_import

# Backward compatibility alias
_get_real_user_home = get_real_user_home

# Module-level safe imports — notification classifier for prioritization
_NotificationClassifier, _NotificationCategory, _create_notification_system, _ClassificationResult, _HAS_CLASSIFIER = safe_import(
    'utils.classifier',
    'NotificationClassifier', 'NotificationCategory',
    'create_notification_system', 'ClassificationResult'
)


class DiagnosticEngine:
    """
    Central diagnostic engine for MeshForge.

    Thread-safe singleton that provides:
    - Comprehensive system checks (9 categories)
    - Real-time callbacks for GUI/Web updates
    - Persistent event logging
    - Health monitoring
    - Report generation
    """

    _instance = None
    _lock = threading.Lock()

    # === Singleton ===

    @classmethod
    def get_instance(cls) -> 'DiagnosticEngine':
        """Get the singleton engine instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        if DiagnosticEngine._instance is not None:
            raise RuntimeError("Use DiagnosticEngine.get_instance()")

        # Results storage
        self._results: Dict[str, CheckResult] = {}
        self._results_lock = threading.Lock()

        # Health by subsystem
        self._health: Dict[str, SubsystemHealth] = {}
        self._health_lock = threading.Lock()

        # Event log (ring buffer)
        self._events: deque = deque(maxlen=1000)
        self._events_lock = threading.Lock()

        # Callbacks
        self._check_callbacks: List[CheckCallback] = []
        self._health_callbacks: List[HealthCallback] = []
        self._event_callbacks: List[EventCallback] = []
        self._progress_callbacks: List[ProgressCallback] = []
        self._callbacks_lock = threading.Lock()

        # Background monitoring
        self._monitor_running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_interval = 30  # seconds

        # Paths
        self._diag_dir = _get_real_user_home() / '.config' / 'meshforge' / 'diagnostics'
        self._ensure_dirs()

        # Notification classifier for "tap on shoulder" pattern
        self._notification_classifier = None
        self._notification_callbacks: List[EventCallback] = []
        if _HAS_CLASSIFIER:
            fixes_path = self._diag_dir / 'notification_fixes.json'
            self._notification_classifier = _create_notification_system(
                bounce_threshold=0.2,
                fixes_path=fixes_path
            )
            logger.debug("Notification classifier initialized")

        logger.info("DiagnosticEngine initialized")

    def _ensure_dirs(self):
        """Create diagnostic directories."""
        try:
            self._diag_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create diagnostic directory: {e}")

    # === Callback Registration ===

    def register_check_callback(self, callback: CheckCallback):
        """Register callback for individual check results."""
        with self._callbacks_lock:
            self._check_callbacks.append(callback)

    def register_health_callback(self, callback: HealthCallback):
        """Register callback for subsystem health changes."""
        with self._callbacks_lock:
            self._health_callbacks.append(callback)

    def register_event_callback(self, callback: EventCallback):
        """Register callback for diagnostic events."""
        with self._callbacks_lock:
            self._event_callbacks.append(callback)

    def register_progress_callback(self, callback: ProgressCallback):
        """Register callback for progress updates."""
        with self._callbacks_lock:
            self._progress_callbacks.append(callback)

    def _notify_check(self, result: CheckResult):
        """Notify all check callbacks."""
        with self._callbacks_lock:
            callbacks = list(self._check_callbacks)
        for cb in callbacks:
            try:
                cb(result)
            except Exception as e:
                logger.error(f"Check callback error: {e}")

    def _notify_health(self, name: str, health: SubsystemHealth):
        """Notify all health callbacks."""
        with self._callbacks_lock:
            callbacks = list(self._health_callbacks)
        for cb in callbacks:
            try:
                cb(name, health)
            except Exception as e:
                logger.error(f"Health callback error: {e}")

    def _notify_progress(self, category: str, current: int, total: int):
        """Notify all progress callbacks."""
        with self._callbacks_lock:
            callbacks = list(self._progress_callbacks)
        for cb in callbacks:
            try:
                cb(category, current, total)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    # === Check Execution ===

    def run_all(self, async_mode: bool = False) -> List[CheckResult]:
        """
        Run all diagnostic checks across all categories.

        Args:
            async_mode: If True, run in background thread and return immediately

        Returns:
            List of all CheckResults (empty list if async_mode=True)
        """
        if async_mode:
            threading.Thread(target=self._run_all_internal, daemon=True).start()
            return []
        return self._run_all_internal()

    def _run_all_internal(self) -> List[CheckResult]:
        """Internal implementation of run_all."""
        all_results = []
        categories = list(CheckCategory)

        for i, category in enumerate(categories):
            self._notify_progress(category.value, i + 1, len(categories))
            results = self.run_category(category)
            all_results.extend(results)

        return all_results

    def run_category(self, category: CheckCategory) -> List[CheckResult]:
        """Run all checks in a specific category."""
        check_map = {
            CheckCategory.SERVICES: self._run_services_checks,
            CheckCategory.NETWORK: self._run_network_checks,
            CheckCategory.RNS: self._run_rns_checks,
            CheckCategory.MESHTASTIC: self._run_meshtastic_checks,
            CheckCategory.SERIAL: self._run_serial_checks,
            CheckCategory.HARDWARE: self._run_hardware_checks,
            CheckCategory.SYSTEM: self._run_system_checks,
            CheckCategory.HAM_RADIO: self._run_ham_radio_checks,
            CheckCategory.LOGS: self._run_logs_checks,
        }

        check_fn = check_map.get(category)
        if check_fn:
            return check_fn()
        return []

    # === Category Check Implementations ===

    def _run_services_checks(self) -> List[CheckResult]:
        """Check system services."""
        results = []
        results.append(check_service('meshtasticd', 'Meshtastic daemon'))
        results.append(check_process('rnsd', 'RNS daemon'))
        results.append(check_process('nomadnet', 'NomadNet'))
        results.append(check_service('bluetooth', 'Bluetooth'))
        self._update_subsystem_health('services', results)
        return results

    def _run_network_checks(self) -> List[CheckResult]:
        """Check network connectivity."""
        results = []
        results.append(check_internet())
        results.append(check_dns())
        results.append(check_tcp_port(4403, 'meshtasticd API'))
        results.append(check_tcp_port(9443, 'meshtasticd Web UI', optional=True))
        results.append(check_tcp_port(1883, 'MQTT broker', optional=True))
        self._update_subsystem_health('network', results)
        return results

    def _run_rns_checks(self) -> List[CheckResult]:
        """Check Reticulum/RNS."""
        results = []
        results.append(check_rns_installed())
        results.append(check_rns_config())
        results.append(check_process('rnsd', 'RNS daemon'))
        results.append(check_rns_port())
        results.append(check_rns_storage_permissions())
        results.append(check_meshtastic_interface_file())
        self._update_subsystem_health('rns', results)
        return results

    def _run_meshtastic_checks(self) -> List[CheckResult]:
        """Check Meshtastic."""
        results = []
        results.append(check_meshtastic_installed())
        results.append(check_meshtastic_cli())
        results.append(check_meshtastic_connection())
        self._update_subsystem_health('meshtastic', results)
        return results

    def _run_serial_checks(self) -> List[CheckResult]:
        """Check serial ports."""
        results = []
        results.append(check_serial_ports())
        results.append(check_dialout_group())
        self._update_subsystem_health('serial', results)
        return results

    def _run_hardware_checks(self) -> List[CheckResult]:
        """Check hardware interfaces."""
        results = []
        results.append(check_spi())
        results.append(check_i2c())
        results.append(check_temperature())
        results.append(check_sdr())
        self._update_subsystem_health('hardware', results)
        return results

    def _run_system_checks(self) -> List[CheckResult]:
        """Check system resources."""
        results = []
        results.append(check_python_version())
        results.append(check_pip_packages())
        results.append(check_memory())
        results.append(check_disk_space())
        results.append(check_cpu_load())
        self._update_subsystem_health('system', results)
        return results

    def _run_ham_radio_checks(self) -> List[CheckResult]:
        """Check HAM radio configuration."""
        results = []
        results.append(check_callsign())
        self._update_subsystem_health('ham_radio', results)
        return results

    def _run_logs_checks(self) -> List[CheckResult]:
        """Analyze logs for errors."""
        results = []
        results.append(check_service_logs('meshtasticd'))
        self._update_subsystem_health('logs', results)
        return results

    # === Health Management ===

    def _update_subsystem_health(self, name: str, checks: List[CheckResult]):
        """Update health status for a subsystem based on check results."""
        fail_count = sum(1 for c in checks if c.status == CheckStatus.FAIL)
        warn_count = sum(1 for c in checks if c.status == CheckStatus.WARN)

        if fail_count > 0:
            status = HealthStatus.UNHEALTHY
            message = f"{fail_count} failed check(s)"
        elif warn_count > 0:
            status = HealthStatus.DEGRADED
            message = f"{warn_count} warning(s)"
        else:
            status = HealthStatus.HEALTHY
            message = "All checks passed"

        # Get first fix hint from failed checks
        fix_hint = None
        for c in checks:
            if c.status == CheckStatus.FAIL and c.fix_hint:
                fix_hint = c.fix_hint
                break

        health = SubsystemHealth(
            name=name,
            status=status,
            message=message,
            checks=checks,
            last_check=datetime.now(),
            fix_hint=fix_hint
        )

        with self._health_lock:
            old_health = self._health.get(name)
            self._health[name] = health

        # Notify callbacks if status changed
        if old_health is None or old_health.status != status:
            self._notify_health(name, health)

        # Store results and notify
        with self._results_lock:
            for check in checks:
                self._results[f"{name}.{check.name}"] = check
                self._notify_check(check)

    def get_overall_health(self) -> HealthStatus:
        """Calculate overall system health from subsystems."""
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
            return HealthStatus.UNKNOWN

    # === Event Logging ===

    def log_event(
        self,
        severity: EventSeverity,
        source: str,
        message: str,
        category: Optional[CheckCategory] = None,
        details: Optional[Dict] = None,
        fix_hint: Optional[str] = None
    ) -> DiagnosticEvent:
        """Log a diagnostic event."""
        event = DiagnosticEvent(
            timestamp=datetime.now(),
            severity=severity,
            source=source,
            message=message,
            category=category,
            details=details,
            fix_hint=fix_hint
        )

        with self._events_lock:
            self._events.append(event)

        # Persist to file
        self._write_event_to_file(event)

        # Notify all event callbacks (always)
        with self._callbacks_lock:
            callbacks = list(self._event_callbacks)
        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

        # Classify for user notification ("tap on shoulder")
        self._maybe_notify_user(event)

        return event

    def _maybe_notify_user(self, event: DiagnosticEvent):
        """
        Classify event and notify user if warranted.

        Implements "tap on shoulder" pattern:
        - Only notify for important/critical events
        - High confidence threshold prevents alert fatigue
        - User corrections improve classification over time
        """
        if not self._notification_classifier:
            return

        event_id = f"{event.source}:{event.timestamp.isoformat()}"

        result = self._notification_classifier.classify(event_id, {
            'severity': event.severity.value.upper(),
            'source': event.source,
            'message': event.message,
            'category': event.category.value if event.category else ''
        })

        # Only notify user for high-priority, high-confidence events
        if self._notification_classifier.should_notify_user(result):
            self._notify_user_callbacks(event, result)

    def _notify_user_callbacks(self, event: DiagnosticEvent, classification):
        """Notify registered user notification callbacks."""
        with self._callbacks_lock:
            callbacks = list(self._notification_callbacks)
        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"User notification callback error: {e}")

    def register_notification_callback(self, callback: EventCallback):
        """
        Register callback for user-facing notifications.

        Unlike event callbacks (which receive all events), notification
        callbacks only fire for high-priority events that should
        interrupt the user - the "tap on shoulder" pattern.
        """
        with self._callbacks_lock:
            self._notification_callbacks.append(callback)

    def get_notification_stats(self) -> Dict:
        """Get notification classifier statistics."""
        if self._notification_classifier:
            return self._notification_classifier.get_stats()
        return {}

    def fix_notification(self, event_id: str, correct_priority: str) -> bool:
        """
        Record a user correction for notification priority.

        This is the 'fix button' - allows users to correct notification
        decisions and reduce alert fatigue over time.

        Args:
            event_id: The event identifier
            correct_priority: One of 'critical', 'important', 'info', 'background'
        """
        if not self._notification_classifier or not self._notification_classifier.fix_registry:
            return False

        result = _ClassificationResult(
            input_id=event_id,
            category="unknown",
            confidence=0.5
        )
        self._notification_classifier.fix_registry.add_fix(result, correct_priority)
        logger.info(f"Notification fix recorded: {event_id} -> {correct_priority}")
        return True

    def _write_event_to_file(self, event: DiagnosticEvent):
        """Write event to daily log file."""
        try:
            log_file = self._diag_dir / f"events_{datetime.now().strftime('%Y%m%d')}.log"
            with open(log_file, 'a') as f:
                f.write(event.to_log_line() + "\n")
        except Exception as e:
            logger.debug(f"Failed to write event to file: {e}")

    # === Queries ===

    def get_health(self, subsystem: Optional[str] = None) -> Dict[str, SubsystemHealth]:
        """Get health status. If subsystem specified, get just that one."""
        with self._health_lock:
            if subsystem:
                return {subsystem: self._health.get(subsystem)} if subsystem in self._health else {}
            return dict(self._health)

    def get_results(
        self,
        category: Optional[CheckCategory] = None,
        status: Optional[CheckStatus] = None
    ) -> List[CheckResult]:
        """Get check results with optional filtering."""
        with self._results_lock:
            results = list(self._results.values())

        if category:
            results = [r for r in results if r.category == category]
        if status:
            results = [r for r in results if r.status == status]

        return results

    def get_events(
        self,
        severity: Optional[EventSeverity] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[DiagnosticEvent]:
        """Get diagnostic events with optional filtering."""
        with self._events_lock:
            events = list(self._events)

        if severity:
            events = [e for e in events if e.severity == severity]
        if since:
            events = [e for e in events if e.timestamp >= since]

        return sorted(events, key=lambda e: e.timestamp, reverse=True)[:limit]

    # === Report Generation ===

    def generate_report(self) -> DiagnosticReport:
        """Generate comprehensive diagnostic report."""
        all_checks = self.get_results()

        # Summary counts
        summary = {
            'total': len(all_checks),
            'passed': sum(1 for c in all_checks if c.status == CheckStatus.PASS),
            'failed': sum(1 for c in all_checks if c.status == CheckStatus.FAIL),
            'warnings': sum(1 for c in all_checks if c.status == CheckStatus.WARN),
            'skipped': sum(1 for c in all_checks if c.status == CheckStatus.SKIP),
        }

        # Recommendations from failed checks
        recommendations = []
        for check in all_checks:
            if check.status == CheckStatus.FAIL and check.fix_hint:
                recommendations.append(f"[{check.category.value}] {check.fix_hint}")

        # Recent events
        hour_ago = datetime.now() - timedelta(hours=1)
        recent_events = self.get_events(since=hour_ago)

        return DiagnosticReport(
            generated_at=datetime.now(),
            overall_health=self.get_overall_health(),
            subsystems=self.get_health(),
            all_checks=all_checks,
            recent_events=recent_events,
            recommendations=recommendations[:10],
            summary=summary
        )

    def save_report(self, filename: Optional[str] = None) -> Path:
        """Save diagnostic report to JSON file."""
        if not filename:
            filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        report_path = self._diag_dir / filename
        report = self.generate_report()

        with open(report_path, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)

        return report_path

    # === Background Monitoring ===

    def start_monitoring(self, interval: int = 30):
        """Start background health monitoring."""
        self._monitor_interval = interval
        self._monitor_running = True
        self._monitor_stop_event = threading.Event()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info(f"Background monitoring started (interval: {interval}s)")

    def stop_monitoring(self):
        """Stop background monitoring."""
        self._monitor_running = False
        if hasattr(self, '_monitor_stop_event'):
            self._monitor_stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        logger.info("Background monitoring stopped")

    def _monitor_loop(self):
        """Background monitoring loop."""
        while self._monitor_running:
            try:
                # Run critical checks only (services, network)
                self._run_services_checks()
                self._run_network_checks()
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            if self._monitor_stop_event.wait(self._monitor_interval):
                break

    # === Wizard Support ===

    def run_wizard(self, wizard_type: str = 'gateway') -> Dict:
        """
        Run diagnostic wizard.

        Returns structured data for UI to render wizard flow.
        """
        # Run relevant checks
        if wizard_type == 'gateway':
            categories = [CheckCategory.RNS, CheckCategory.MESHTASTIC, CheckCategory.SERIAL]
        else:
            categories = list(CheckCategory)

        all_results = []
        for cat in categories:
            all_results.extend(self.run_category(cat))

        # Analyze for recommendations
        failures = [r for r in all_results if r.status == CheckStatus.FAIL]
        warnings = [r for r in all_results if r.status == CheckStatus.WARN]

        # Detect available connection types
        serial_devices = find_serial_devices()
        tcp_available = any(
            r.status == CheckStatus.PASS and '4403' in r.name
            for r in all_results
        )

        return {
            'results': [r.to_dict() for r in all_results],
            'failures': [r.to_dict() for r in failures],
            'warnings': [r.to_dict() for r in warnings],
            'connection_options': {
                'serial': serial_devices,
                'tcp': tcp_available,
            },
            'recommended_connection': 'tcp' if tcp_available else 'serial' if serial_devices else None,
            'ready': len(failures) == 0
        }
