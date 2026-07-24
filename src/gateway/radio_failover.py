"""
Dual-Radio Failover & Load Balancing for MeshForge Gateway.

Monitors two meshtasticd instances and automatically switches the active
transmitter when channel utilization exceeds safe thresholds. Designed to
address Meshtastic's firmware behavior of skipping position/telemetry
sends at >25% channel utilization.

Architecture:
    Primary radio  (port 4403) ─┐
                                ├─> FailoverManager ─> active_port()
    Secondary radio (port 4404) ─┘

States:
    PRIMARY_ACTIVE    → Normal operation, primary radio is the TX path
    FAILOVER_PENDING  → Primary overloaded, evaluating secondary
    SECONDARY_ACTIVE  → Secondary is the active TX path
    RECOVERY_PENDING  → Primary recovered, stabilizing before switchback

Thresholds:
    - Meshtastic firmware skips sends at >25% channel utilization
    - Pure ALOHA theoretical max is ~18.4% before collision dominance
    - SENSOR/TRACKER roles bypass the 25% throttle

Features:
    - Utilization-based failover (channel >25% sustained)
    - Service watchdog: auto-restart crashed meshtasticd instances
    - Crash-based failover: immediate failover when service unreachable
    - Reachability-based recovery: detect service restart and switchback
    - EventBus integration: emit service events on state changes
    - Persistent events: write transitions to SharedHealthState (SQLite)
    - LB coordination: RadioLoadBalancer defers to failover state

Requires:
    - Two meshtasticd instances on ports 4403 and 4404
    - HTTP API enabled on both (Webserver.Port in config.yaml)

Usage:
    from gateway.radio_failover import FailoverManager, FailoverConfig

    config = FailoverConfig(enabled=True)
    manager = FailoverManager(config)
    manager.start()

    # Get current active port for TX
    port = manager.active_port  # 4403 or 4404

    # Check state
    print(manager.state)        # FailoverState.PRIMARY_ACTIVE
    print(manager.get_status()) # Dict with full status
"""

import collections
import json
import logging
import random
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from utils.ports import MESHTASTICD_PORT, MESHTASTICD_ALT_PORT, MESHTASTICD_WEB_PORT

# HTTP client for polling radio health (non-blocking, no TCP lock)
try:
    from utils.meshtastic_http import get_http_client, DeviceReport
    _HAS_HTTP = True
except ImportError:
    _HAS_HTTP = False

# EventBus for broadcasting failover state changes
try:
    from utils.event_bus import emit_service_status
    _HAS_EVENT_BUS = True
except ImportError:
    _HAS_EVENT_BUS = False

# SharedHealthState for persistent event storage (SQLite)
try:
    from utils.shared_health_state import get_shared_health_state
    _HAS_SHARED_STATE = True
except ImportError:
    _HAS_SHARED_STATE = False

# Service management for watchdog restarts
try:
    from utils.service_check import restart_service, check_service
    _HAS_SERVICE_CHECK = True
except ImportError:
    _HAS_SERVICE_CHECK = False

logger = logging.getLogger(__name__)


class FailoverState(Enum):
    """Dual-radio failover states."""
    PRIMARY_ACTIVE = "primary_active"
    FAILOVER_PENDING = "failover_pending"
    SECONDARY_ACTIVE = "secondary_active"
    RECOVERY_PENDING = "recovery_pending"
    DISABLED = "disabled"


@dataclass
class FailoverConfig:
    """Configuration for dual-radio failover behavior."""
    enabled: bool = False

    # Radio ports (meshtasticd TCP API)
    primary_port: int = MESHTASTICD_PORT      # 4403
    secondary_port: int = MESHTASTICD_ALT_PORT  # 4404

    # HTTP ports for health polling (meshtasticd web server)
    primary_http_port: int = MESHTASTICD_WEB_PORT  # 9443
    secondary_http_port: int = 9444                # Alt web port

    # Failover thresholds
    utilization_threshold: float = 25.0   # % channel utilization trigger
    utilization_duration: int = 30        # Seconds sustained above threshold
    tx_utilization_threshold: float = 20.0  # % TX airtime trigger

    # Recovery thresholds
    recovery_threshold: float = 15.0   # % utilization for switchback
    recovery_duration: int = 60        # Seconds stable below threshold

    # Health polling
    health_poll_interval: float = 5.0  # Seconds between health checks
    http_timeout: float = 3.0          # Seconds for HTTP request timeout

    # Safety
    max_failovers_per_hour: int = 6    # Prevent flapping
    cooldown_after_failover: int = 30  # Minimum seconds between state changes

    # Service watchdog — auto-restart crashed meshtasticd
    watchdog_enabled: bool = True
    restart_after_failures: int = 5     # Consecutive poll failures before restart
    max_restarts_per_hour: int = 3      # Prevent restart loops
    restart_cooldown: int = 60          # Seconds between restart attempts
    primary_service: str = "meshtasticd"       # systemd service name (primary)
    secondary_service: str = "meshtasticd-alt"  # systemd service name (secondary)


@dataclass
class RadioHealth:
    """Health snapshot for a single radio."""
    port: int = 0
    http_port: int = 0
    reachable: bool = False
    channel_utilization: float = 0.0
    tx_utilization: float = 0.0
    battery_percent: int = 0
    has_battery: bool = False
    seconds_since_boot: int = 0
    last_check: float = 0.0
    consecutive_failures: int = 0

    @property
    def is_overloaded(self) -> bool:
        """Check if utilization exceeds safe operating threshold."""
        return self.channel_utilization >= 25.0

    @property
    def is_healthy(self) -> bool:
        """Check if radio is reachable and below recovery threshold."""
        return self.reachable and self.channel_utilization < 15.0


@dataclass
class FailoverEvent:
    """Record of a failover state transition."""
    timestamp: datetime
    from_state: FailoverState
    to_state: FailoverState
    reason: str
    primary_utilization: float
    secondary_utilization: float


class FailoverManager:
    """
    Monitors two meshtasticd instances and manages automatic failover.

    Polls both radios via HTTP /json/report (non-blocking, no TCP lock)
    and transitions between states based on channel utilization thresholds.

    Includes:
    - Service watchdog: detects crashed meshtasticd and auto-restarts
    - Crash-based failover: immediate failover on service unreachable
    - Reachability-based recovery: switches back when crashed service recovers
    - EventBus integration: emits service status on state changes
    - Persistent events: writes to SharedHealthState (SQLite) for post-incident analysis
    """

    def __init__(
        self,
        config: Optional[FailoverConfig] = None,
        on_state_change: Optional[Callable[[FailoverState, FailoverState, str], None]] = None,
    ):
        self._config = config or FailoverConfig()
        self._on_state_change = on_state_change

        # State
        self._state = FailoverState.DISABLED if not self._config.enabled else FailoverState.PRIMARY_ACTIVE
        self._state_lock = threading.Lock()

        # Health tracking
        self._primary = RadioHealth(
            port=self._config.primary_port,
            http_port=self._config.primary_http_port,
        )
        self._secondary = RadioHealth(
            port=self._config.secondary_port,
            http_port=self._config.secondary_http_port,
        )

        # Timing
        self._overload_start: Optional[float] = None  # When primary exceeded threshold
        self._recovery_start: Optional[float] = None   # When primary dropped below threshold
        self._last_state_change: float = 0.0
        self._failover_count_window: List[float] = []  # Timestamps of recent failovers

        # Crash tracking — detect when primary was unreachable and comes back
        self._primary_was_down: bool = False
        self._primary_down_since: Optional[float] = None
        self._secondary_was_down: bool = False
        self._secondary_down_since: Optional[float] = None

        # Service watchdog — restart tracking per radio
        self._restart_timestamps: Dict[str, List[float]] = {
            'primary': [],
            'secondary': [],
        }
        self._last_restart_attempt: Dict[str, float] = {
            'primary': 0.0,
            'secondary': 0.0,
        }

        # Event history
        self._events: List[FailoverEvent] = []
        self._max_events = 100

        # Polling thread
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def state(self) -> FailoverState:
        """Current failover state."""
        with self._state_lock:
            return self._state

    @property
    def active_port(self) -> int:
        """TCP API port of the currently active radio for TX."""
        with self._state_lock:
            if self._state in (FailoverState.SECONDARY_ACTIVE, FailoverState.RECOVERY_PENDING):
                return self._config.secondary_port
            return self._config.primary_port

    @property
    def active_http_port(self) -> int:
        """HTTP API port of the currently active radio for TX."""
        with self._state_lock:
            if self._state in (FailoverState.SECONDARY_ACTIVE, FailoverState.RECOVERY_PENDING):
                return self._config.secondary_http_port
            return self._config.primary_http_port

    @property
    def primary_health(self) -> RadioHealth:
        """Health snapshot of the primary radio."""
        return self._primary

    @property
    def secondary_health(self) -> RadioHealth:
        """Health snapshot of the secondary radio."""
        return self._secondary

    @property
    def events(self) -> List[FailoverEvent]:
        """Recent failover events (newest first)."""
        return list(reversed(self._events))

    def start(self) -> None:
        """Start the health polling loop in a background thread."""
        if not self._config.enabled:
            logger.info("Radio failover disabled by configuration")
            return

        if not _HAS_HTTP:
            logger.warning("Radio failover requires meshtastic_http module — disabled")
            self._state = FailoverState.DISABLED
            return

        if self._thread and self._thread.is_alive():
            logger.warning("Failover manager already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="radio-failover",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Radio failover started: primary=%d, secondary=%d, threshold=%.0f%%, "
            "watchdog=%s",
            self._config.primary_port, self._config.secondary_port,
            self._config.utilization_threshold,
            "enabled" if self._config.watchdog_enabled else "disabled",
        )

    def stop(self) -> None:
        """Stop the health polling loop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("Radio failover stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive failover status for TUI dashboard."""
        with self._state_lock:
            state = self._state

        return {
            'state': state.value,
            'active_port': self.active_port,
            'enabled': self._config.enabled,
            'primary': {
                'port': self._primary.port,
                'reachable': self._primary.reachable,
                'channel_utilization': round(self._primary.channel_utilization, 1),
                'tx_utilization': round(self._primary.tx_utilization, 1),
                'overloaded': self._primary.is_overloaded,
            },
            'secondary': {
                'port': self._secondary.port,
                'reachable': self._secondary.reachable,
                'channel_utilization': round(self._secondary.channel_utilization, 1),
                'tx_utilization': round(self._secondary.tx_utilization, 1),
                'overloaded': self._secondary.is_overloaded,
            },
            'last_event': self._events[-1].reason if self._events else None,
            'failover_count_1h': len(self._failover_count_window),
            'watchdog': {
                'enabled': self._config.watchdog_enabled,
                'primary_restarts_1h': len(self._restart_timestamps.get('primary', [])),
                'secondary_restarts_1h': len(self._restart_timestamps.get('secondary', [])),
                'primary_down': self._primary_was_down,
                'secondary_down': self._secondary_was_down,
            },
            'thresholds': {
                'utilization': self._config.utilization_threshold,
                'recovery': self._config.recovery_threshold,
                'duration': self._config.utilization_duration,
            },
        }

    # ── Internal polling loop ──────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Background loop: poll both radios, run watchdog, evaluate state."""
        while not self._stop_event.is_set():
            try:
                self._poll_radio_health(self._primary)
                self._poll_radio_health(self._secondary)
                self._track_reachability()
                self._run_watchdog()
                self._evaluate_state()
            except Exception as e:
                logger.error("Failover poll error: %s", e)

            self._stop_event.wait(timeout=self._config.health_poll_interval)

    def _poll_radio_health(self, radio: RadioHealth) -> None:
        """Poll a single radio's health via HTTP /json/report."""
        try:
            client = get_http_client(
                host='localhost',
                port=radio.http_port,
                auto_detect=False,
            )
            report = client.get_device_report()
            if report:
                radio.channel_utilization = report.channel_utilization
                radio.tx_utilization = report.tx_utilization
                radio.battery_percent = report.battery_percent
                radio.has_battery = report.has_battery
                radio.seconds_since_boot = report.seconds_since_boot
                radio.reachable = True
                radio.consecutive_failures = 0
                radio.last_check = time.time()
            else:
                radio.consecutive_failures += 1
                if radio.consecutive_failures >= 3:
                    radio.reachable = False
        except Exception as e:
            logger.debug("Health check failed for %s:%d: %s", radio.host, radio.port, e)
            radio.consecutive_failures += 1
            if radio.consecutive_failures >= 3:
                radio.reachable = False

    # ── Crash tracking ─────────────────────────────────────────────────

    def _track_reachability(self) -> None:
        """Track when radios go down and come back for crash-based failover."""
        now = time.monotonic()  # duration anchor — see _evaluate_state (Pri-13)

        # Primary tracking
        if not self._primary.reachable and not self._primary_was_down:
            self._primary_was_down = True
            self._primary_down_since = now
            logger.warning("Primary radio became unreachable at %s",
                         datetime.now().strftime("%H:%M:%S"))
        elif self._primary.reachable and self._primary_was_down:
            downtime = now - (self._primary_down_since or now)
            logger.info("Primary radio recovered after %.0fs downtime", downtime)
            # Don't clear _primary_was_down here — _evaluate_secondary_active uses it

        # Secondary tracking
        if not self._secondary.reachable and not self._secondary_was_down:
            self._secondary_was_down = True
            self._secondary_down_since = now
            logger.warning("Secondary radio became unreachable at %s",
                         datetime.now().strftime("%H:%M:%S"))
        elif self._secondary.reachable and self._secondary_was_down:
            downtime = now - (self._secondary_down_since or now)
            logger.info("Secondary radio recovered after %.0fs downtime", downtime)
            self._secondary_was_down = False
            self._secondary_down_since = None

    # ── Service watchdog ───────────────────────────────────────────────

    def _run_watchdog(self) -> None:
        """Detect crashed meshtasticd services and attempt restart.

        Promotes the surviving radio IMMEDIATELY on crash detection,
        then attempts restart in background. Recovery follows the normal
        RECOVERY_PENDING path once the restarted service becomes reachable.
        """
        if not self._config.watchdog_enabled or not _HAS_SERVICE_CHECK:
            return

        now = time.monotonic()  # duration anchor — see _evaluate_state (Pri-13)

        for label, radio, service, peer_radio in [
            ('primary', self._primary, self._config.primary_service, self._secondary),
            ('secondary', self._secondary, self._config.secondary_service, self._primary),
        ]:
            if not (radio.consecutive_failures >= self._config.restart_after_failures
                    and not radio.reachable):
                continue

            # Immediate failover: promote surviving radio before restart
            # Note: _transition handles its own locking, so read state first
            current_state = self.state  # property acquires/releases lock
            if label == 'primary' and peer_radio.reachable:
                if current_state == FailoverState.PRIMARY_ACTIVE:
                    self._transition(
                        FailoverState.SECONDARY_ACTIVE,
                        "Watchdog: primary crashed, promoting secondary",
                    )
            elif label == 'secondary' and peer_radio.reachable:
                if current_state == FailoverState.SECONDARY_ACTIVE:
                    self._transition(
                        FailoverState.RECOVERY_PENDING,
                        "Watchdog: secondary crashed, recovering to primary",
                    )

            # Attempt restart — service recovers via normal RECOVERY_PENDING path
            self._attempt_restart(label, service, now)

    def _attempt_restart(self, label: str, service_name: str, now: float) -> None:
        """Attempt to restart a crashed meshtasticd service."""
        # Cooldown check (now is monotonic — Pri-13). Guard the 0.0 "never
        # attempted" sentinel so a fresh boot doesn't gate the first restart.
        if (self._last_restart_attempt[label] > 0
                and now - self._last_restart_attempt[label] < self._config.restart_cooldown):
            return

        # Rate limit — max restarts per hour
        self._restart_timestamps[label] = [
            t for t in self._restart_timestamps[label] if now - t < 3600
        ]
        if len(self._restart_timestamps[label]) >= self._config.max_restarts_per_hour:
            logger.warning(
                "WATCHDOG: max restarts/hour (%d) reached for %s — skipping",
                self._config.max_restarts_per_hour, label,
            )
            return

        logger.warning(
            "WATCHDOG: %s meshtasticd (%s) unreachable for %d consecutive checks — "
            "attempting restart",
            label, service_name,
            self._primary.consecutive_failures if label == 'primary'
            else self._secondary.consecutive_failures,
        )

        self._last_restart_attempt[label] = now

        try:
            success, msg = restart_service(service_name, timeout=30)
            self._restart_timestamps[label].append(now)

            if success:
                logger.info("WATCHDOG: %s service %s restarted successfully: %s",
                           label, service_name, msg)
                self._emit_event(
                    f"watchdog_{label}",
                    True,
                    f"Service {service_name} restarted successfully",
                )
            else:
                logger.error("WATCHDOG: %s service %s restart failed: %s",
                            label, service_name, msg)
                self._emit_event(
                    f"watchdog_{label}",
                    False,
                    f"Service {service_name} restart failed: {msg}",
                )
        except Exception as e:
            logger.error("WATCHDOG: %s restart error: %s", label, e)
            self._emit_event(
                f"watchdog_{label}",
                False,
                f"Restart error: {e}",
            )

    # ── State machine ──────────────────────────────────────────────────

    def _evaluate_state(self) -> None:
        """Evaluate whether a state transition should occur."""
        with self._state_lock:
            current = self._state

        if current == FailoverState.DISABLED:
            return

        # Monotonic clock (gateway review Pri-13, 2026-07-23): every timing
        # decision below is a DURATION (cooldown, per-hour rate windows,
        # overload/recovery/downtime elapsed) and wall-clock durations are
        # forgeable on the fleet's RTC-less Pis (honest_failure_modes #6, the
        # #74/Pri-1 class — here an NTP step could forge a failover or an
        # early switch-back, i.e. split-brain). All anchors are stamped from
        # and compared to this `now`, so a single monotonic source keeps the
        # whole state machine internally consistent. Absolute display
        # timestamps (radio.last_check, log strftime) stay wall-clock.
        now = time.monotonic()

        # Cooldown check — prevent flapping. Guard the 0.0 "never transitioned"
        # sentinel: under monotonic `now` (uptime) a bare `now - 0.0` would
        # spuriously gate for the first cooldown seconds after boot.
        if (self._last_state_change > 0
                and now - self._last_state_change < self._config.cooldown_after_failover):
            return

        # Rate limit — max failovers per hour
        self._failover_count_window = [
            t for t in self._failover_count_window if now - t < 3600
        ]

        if current == FailoverState.PRIMARY_ACTIVE:
            self._evaluate_primary_active(now)
        elif current == FailoverState.FAILOVER_PENDING:
            self._evaluate_failover_pending(now)
        elif current == FailoverState.SECONDARY_ACTIVE:
            self._evaluate_secondary_active(now)
        elif current == FailoverState.RECOVERY_PENDING:
            self._evaluate_recovery_pending(now)

    def _evaluate_primary_active(self, now: float) -> None:
        """Check if primary needs failover (utilization OR crash)."""
        if not self._primary.reachable:
            # Primary unreachable — immediate failover if secondary is up
            if self._secondary.reachable:
                self._transition(
                    FailoverState.SECONDARY_ACTIVE,
                    "Primary radio unreachable, secondary available"
                )
            return

        if self._primary.channel_utilization >= self._config.utilization_threshold:
            if self._overload_start is None:
                self._overload_start = now
                logger.info(
                    "Primary radio utilization %.1f%% exceeds %.0f%% threshold — monitoring",
                    self._primary.channel_utilization, self._config.utilization_threshold,
                )
            elif now - self._overload_start >= self._config.utilization_duration:
                self._transition(
                    FailoverState.FAILOVER_PENDING,
                    f"Primary sustained >{self._config.utilization_threshold}% "
                    f"for {self._config.utilization_duration}s"
                )
                self._overload_start = None
        else:
            self._overload_start = None

    def _evaluate_failover_pending(self, now: float) -> None:
        """Evaluate if secondary is ready to take over."""
        if not self._secondary.reachable:
            logger.warning("Secondary radio unreachable — staying on primary")
            self._transition(
                FailoverState.PRIMARY_ACTIVE,
                "Secondary unreachable, reverting to primary"
            )
            return

        if self._secondary.is_overloaded:
            logger.warning(
                "Secondary also overloaded (%.1f%%) — staying on primary",
                self._secondary.channel_utilization
            )
            self._transition(
                FailoverState.PRIMARY_ACTIVE,
                "Both radios overloaded, staying on primary"
            )
            return

        # Rate limit check
        if len(self._failover_count_window) >= self._config.max_failovers_per_hour:
            logger.warning(
                "Max failovers/hour (%d) reached — staying on primary",
                self._config.max_failovers_per_hour
            )
            self._transition(
                FailoverState.PRIMARY_ACTIVE,
                "Failover rate limit reached"
            )
            return

        # Secondary looks good — activate it
        self._failover_count_window.append(now)
        self._transition(
            FailoverState.SECONDARY_ACTIVE,
            f"Failover: primary at {self._primary.channel_utilization:.1f}%, "
            f"secondary at {self._secondary.channel_utilization:.1f}%"
        )

    def _evaluate_secondary_active(self, now: float) -> None:
        """Check if primary has recovered (utilization-based OR crash recovery)."""
        if not self._primary.reachable:
            self._recovery_start = None
            return

        # Crash recovery: primary was down and has come back online
        if self._primary_was_down and self._primary.reachable:
            downtime = now - (self._primary_down_since or now)
            self._transition(
                FailoverState.RECOVERY_PENDING,
                f"Primary service recovered after {downtime:.0f}s downtime"
            )
            self._primary_was_down = False
            self._primary_down_since = None
            self._recovery_start = None
            return

        # Utilization-based recovery
        if self._primary.channel_utilization < self._config.recovery_threshold:
            if self._recovery_start is None:
                self._recovery_start = now
                logger.info(
                    "Primary utilization %.1f%% below recovery threshold %.0f%% — monitoring",
                    self._primary.channel_utilization, self._config.recovery_threshold,
                )
            elif now - self._recovery_start >= self._config.recovery_duration:
                self._transition(
                    FailoverState.RECOVERY_PENDING,
                    f"Primary below {self._config.recovery_threshold}% "
                    f"for {self._config.recovery_duration}s"
                )
                self._recovery_start = None
        else:
            self._recovery_start = None

    def _evaluate_recovery_pending(self, now: float) -> None:
        """Confirm primary is stable before switching back."""
        if not self._primary.reachable or self._primary.is_overloaded:
            self._transition(
                FailoverState.SECONDARY_ACTIVE,
                "Primary not stable during recovery — staying on secondary"
            )
            return

        # Primary confirmed stable
        self._transition(
            FailoverState.PRIMARY_ACTIVE,
            f"Recovery complete: primary at {self._primary.channel_utilization:.1f}%"
        )

    # ── State transition with event bus + persistence ──────────────────

    def _transition(self, new_state: FailoverState, reason: str) -> None:
        """Execute a state transition with logging, events, and persistence."""
        with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return
            self._state = new_state

        # Monotonic: read as a cooldown DURATION in _evaluate_state (Pri-13).
        # The event's own displayed timestamp uses datetime.now() below.
        self._last_state_change = time.monotonic()

        event = FailoverEvent(
            timestamp=datetime.now(),
            from_state=old_state,
            to_state=new_state,
            reason=reason,
            primary_utilization=self._primary.channel_utilization,
            secondary_utilization=self._secondary.channel_utilization,
        )
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

        logger.warning(
            "FAILOVER: %s -> %s | %s | primary=%.1f%% secondary=%.1f%%",
            old_state.value, new_state.value, reason,
            self._primary.channel_utilization, self._secondary.channel_utilization,
        )

        # Emit EventBus service status
        self._emit_event(
            "radio_failover",
            new_state != FailoverState.DISABLED,
            f"{old_state.value} -> {new_state.value}: {reason}",
        )

        # Persist to SharedHealthState (SQLite)
        self._persist_event(new_state, reason)

        # User callback
        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state, reason)
            except Exception as e:
                logger.error("Failover callback error: %s", e)

    def _emit_event(self, service_name: str, available: bool, message: str) -> None:
        """Emit a service status event via EventBus."""
        if _HAS_EVENT_BUS:
            try:
                emit_service_status(service_name, available, message)
            except Exception as e:
                logger.debug("EventBus emit error: %s", e)

    def _persist_event(self, state: FailoverState, reason: str) -> None:
        """Persist failover state to SharedHealthState (SQLite)."""
        if _HAS_SHARED_STATE:
            try:
                shs = get_shared_health_state()
                shs.update_service(
                    "radio_failover",
                    state=state.value,
                    reason=reason,
                )
            except Exception as e:
                logger.debug("SharedHealthState persist error: %s", e)


# ── TX Load Balancer ────────────────────────────────────────────────────
#
# Replaces binary failover with weighted TX distribution across two radios.
# Triggers on tx_utilization (our TX contribution), NOT channel_utilization
# (identical on same-channel radios).
#
# Model:
#   Primary TX < threshold  → 100% primary (IDLE)
#   Primary TX >= threshold → split across both radios (BALANCING)
#   Both radios high TX     → hold weights, warn (SATURATED)
#
# Failover coordination:
#   When a FailoverManager reference is provided, the load balancer defers
#   to its state — e.g., routes 100% to secondary when failover is active,
#   and does not interfere during recovery.


class LoadBalancerState(Enum):
    """TX load balancer states."""
    IDLE = "idle"                # Primary handles 100% TX
    BALANCING = "balancing"      # Weights split between radios
    SATURATED = "saturated"      # Both radios high TX — warning
    DISABLED = "disabled"


@dataclass
class LoadBalancerConfig:
    """Configuration for dual-radio TX load balancing."""
    enabled: bool = False

    # Radio ports (meshtasticd TCP API)
    primary_port: int = MESHTASTICD_PORT        # 4403
    secondary_port: int = MESHTASTICD_ALT_PORT  # 4404

    # HTTP ports for health polling (meshtasticd web server)
    primary_http_port: int = MESHTASTICD_WEB_PORT  # 9443
    secondary_http_port: int = 9444

    # TX thresholds (based on tx_utilization, NOT channel_utilization)
    tx_threshold: float = 10.0   # % TX airtime to start splitting
    tx_max: float = 20.0         # % TX airtime for maximum offload

    # Polling
    health_poll_interval: float = 5.0  # Seconds between health checks
    http_timeout: float = 3.0          # Seconds for HTTP request timeout

    # Safety
    weight_change_rate: float = 10.0   # Max weight shift per poll cycle (%)
    min_primary_weight: float = 10.0   # Primary always keeps at least this %
    recovery_margin: float = 2.0       # Hysteresis: return to IDLE at threshold - margin


class RadioLoadBalancer:
    """
    Weighted TX load balancer for dual-radio MeshForge gateways.

    Distributes outbound gateway traffic across two meshtasticd instances
    based on each radio's tx_utilization (its own TX airtime contribution).

    Unlike FailoverManager which uses channel_utilization (identical for
    same-channel radios), this uses tx_utilization — the metric that
    actually differs between two radios sharing a channel.

    Failover coordination:
        When failover_manager is provided, the load balancer defers to its
        state. If failover has switched to secondary, the load balancer
        routes 100% to secondary instead of using its own weight calculation.
        During recovery, the load balancer does not interfere.

    Usage:
        config = LoadBalancerConfig(enabled=True)
        lb = RadioLoadBalancer(config, failover_manager=fm)
        lb.start()

        # In TX path — call per message:
        http_port = lb.get_tx_port()  # 9443 or 9444
        send_text_direct(text=msg, host="localhost", port=http_port)
    """

    def __init__(
        self,
        config: Optional[LoadBalancerConfig] = None,
        on_state_change: Optional[Callable[[LoadBalancerState, LoadBalancerState, str], None]] = None,
        congested_node_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        failover_manager: Optional['FailoverManager'] = None,
    ):
        self._config = config or LoadBalancerConfig()
        self._on_state_change = on_state_change
        self._congested_node_provider = congested_node_provider
        self._failover_manager = failover_manager

        # State
        self._state = LoadBalancerState.DISABLED if not self._config.enabled else LoadBalancerState.IDLE
        self._state_lock = threading.Lock()

        # Weights (primary_weight + secondary_weight = 100)
        self._primary_weight: float = 100.0
        self._weight_lock = threading.Lock()

        # Health tracking (reuses RadioHealth from failover)
        self._primary = RadioHealth(
            port=self._config.primary_port,
            http_port=self._config.primary_http_port,
        )
        self._secondary = RadioHealth(
            port=self._config.secondary_port,
            http_port=self._config.secondary_http_port,
        )

        # Track reachability transitions for slow start
        self._primary_was_unreachable: bool = False
        self._secondary_was_unreachable: bool = False
        self._recovery_weight_target: Optional[float] = None

        # Slow start after failover recovery — ramp primary weight gradually
        self._failover_recovery_at: Optional[float] = None
        self._slow_start_duration: float = 30.0  # seconds to ramp from min to 100%
        self._prev_failover_state: Optional[FailoverState] = None

        # TX counters (per poll cycle, for status display)
        self._tx_count_primary: int = 0
        self._tx_count_secondary: int = 0
        self._counter_lock = threading.Lock()

        # Event history
        self._events: collections.deque = collections.deque(maxlen=100)
        self._last_state_change: float = 0.0

        # Polling thread
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def state(self) -> LoadBalancerState:
        """Current load balancer state."""
        with self._state_lock:
            return self._state

    @property
    def primary_weight(self) -> float:
        """Current primary TX weight (0-100)."""
        with self._weight_lock:
            return self._primary_weight

    @property
    def secondary_weight(self) -> float:
        """Current secondary TX weight (0-100)."""
        with self._weight_lock:
            return 100.0 - self._primary_weight

    @property
    def primary_health(self) -> RadioHealth:
        """Health snapshot of the primary radio."""
        return self._primary

    @property
    def secondary_health(self) -> RadioHealth:
        """Health snapshot of the secondary radio."""
        return self._secondary

    def get_tx_port(self) -> int:
        """Return HTTP port for next TX message based on current weights.

        Called per outbound message. Uses weighted random selection so that
        over many calls, the distribution matches the current weight ratio.

        If a FailoverManager is active and has switched to secondary, this
        routes 100% to the secondary radio's HTTP port.

        Returns:
            HTTP port (e.g. 9443 or 9444) for the selected radio.
        """
        with self._state_lock:
            if self._state == LoadBalancerState.DISABLED:
                # Even when disabled, check if failover has a preference
                if self._failover_manager:
                    return self._failover_manager.active_http_port
                return self._config.primary_http_port

        with self._weight_lock:
            weight = self._primary_weight

        if random.random() * 100.0 < weight:
            with self._counter_lock:
                self._tx_count_primary += 1
            return self._config.primary_http_port
        else:
            with self._counter_lock:
                self._tx_count_secondary += 1
            return self._config.secondary_http_port

    def start(self) -> None:
        """Start the health polling loop in a background thread."""
        if not self._config.enabled:
            logger.info("TX load balancer disabled by configuration")
            return

        if not _HAS_HTTP:
            logger.warning("TX load balancer requires meshtastic_http module — disabled")
            self._state = LoadBalancerState.DISABLED
            return

        if self._thread and self._thread.is_alive():
            logger.warning("TX load balancer already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="radio-load-balancer",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "TX load balancer started: primary=%d, secondary=%d, "
            "tx_threshold=%.0f%%, tx_max=%.0f%%, failover_aware=%s",
            self._config.primary_port, self._config.secondary_port,
            self._config.tx_threshold, self._config.tx_max,
            "yes" if self._failover_manager else "no",
        )

    def stop(self) -> None:
        """Stop the health polling loop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("TX load balancer stopped")

    def reset_counters(self) -> None:
        """Reset TX counters for per-session stats."""
        with self._counter_lock:
            self._tx_count_primary = 0
            self._tx_count_secondary = 0

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive load balancer status for TUI dashboard."""
        with self._state_lock:
            state = self._state
        with self._weight_lock:
            p_weight = self._primary_weight

        with self._counter_lock:
            tx_primary = self._tx_count_primary
            tx_secondary = self._tx_count_secondary

        status = {
            'state': state.value,
            'enabled': self._config.enabled,
            'primary_weight': round(p_weight, 1),
            'secondary_weight': round(100.0 - p_weight, 1),
            'primary': {
                'port': self._primary.port,
                'http_port': self._primary.http_port,
                'reachable': self._primary.reachable,
                'channel_utilization': round(self._primary.channel_utilization, 1),
                'tx_utilization': round(self._primary.tx_utilization, 1),
            },
            'secondary': {
                'port': self._secondary.port,
                'http_port': self._secondary.http_port,
                'reachable': self._secondary.reachable,
                'channel_utilization': round(self._secondary.channel_utilization, 1),
                'tx_utilization': round(self._secondary.tx_utilization, 1),
            },
            'tx_counts': {
                'primary': tx_primary,
                'secondary': tx_secondary,
            },
            'failover_aware': self._failover_manager is not None,
            'failover_state': (
                self._failover_manager.state.value
                if self._failover_manager else None
            ),
            'last_event': self._events[-1].reason if self._events else None,
            'thresholds': {
                'tx_threshold': self._config.tx_threshold,
                'tx_max': self._config.tx_max,
            },
        }

        # Congested node identification
        if self._congested_node_provider:
            try:
                status['congested_nodes'] = self._congested_node_provider()
            except Exception as e:
                logger.debug("Failed to get congested nodes: %s", e)
                status['congested_nodes'] = []
        else:
            status['congested_nodes'] = []

        return status

    # ── Internal polling loop ──────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Background loop: poll both radios and recalculate TX weights."""
        while not self._stop_event.is_set():
            try:
                self._poll_radio_health(self._primary)
                self._poll_radio_health(self._secondary)
                self._recalculate_weights()
            except Exception as e:
                logger.error("Load balancer poll error: %s", e)

            self._stop_event.wait(timeout=self._config.health_poll_interval)

    def _poll_radio_health(self, radio: RadioHealth) -> None:
        """Poll a single radio's health via HTTP /json/report."""
        try:
            client = get_http_client(
                host='localhost',
                port=radio.http_port,
                auto_detect=False,
            )
            report = client.get_device_report()
            if report:
                radio.channel_utilization = report.channel_utilization
                radio.tx_utilization = report.tx_utilization
                radio.battery_percent = report.battery_percent
                radio.has_battery = report.has_battery
                radio.seconds_since_boot = report.seconds_since_boot
                radio.reachable = True
                radio.consecutive_failures = 0
                radio.last_check = time.time()
            else:
                radio.consecutive_failures += 1
                if radio.consecutive_failures >= 3:
                    radio.reachable = False
        except Exception as e:
            logger.debug("Health check failed for %s:%d: %s", radio.host, radio.port, e)
            radio.consecutive_failures += 1
            if radio.consecutive_failures >= 3:
                radio.reachable = False

    def _recalculate_weights(self) -> None:
        """Recalculate TX weights based on failover state and tx_utilization."""
        # Phase 2: Defer to failover manager if active
        if self._failover_manager:
            fo_state = self._failover_manager.state

            # Detect failover recovery completion → start slow ramp
            if (self._prev_failover_state in (
                    FailoverState.RECOVERY_PENDING, FailoverState.SECONDARY_ACTIVE)
                    and fo_state == FailoverState.PRIMARY_ACTIVE):
                # Monotonic anchor for the slow-start ramp DURATION (Pri-13):
                # a wall-clock step here would forge ramp progress and blast a
                # just-recovered primary (mirror of the Pri-6 SlowStartRecovery
                # fix). Stamp/read pair; in-memory only.
                self._failover_recovery_at = time.monotonic()
                logger.info("LB slow start: failover recovery detected, ramping primary weight")
            self._prev_failover_state = fo_state

            if fo_state == FailoverState.SECONDARY_ACTIVE:
                # Failover active — route 100% to secondary
                self._failover_recovery_at = None  # Cancel any pending slow start
                self._set_weights(0.0, "Failover: secondary active")
                self._set_state(LoadBalancerState.BALANCING)
                return
            elif fo_state == FailoverState.RECOVERY_PENDING:
                # Recovery in progress — don't interfere with failover
                return

            # Slow start after failover recovery
            if (fo_state == FailoverState.PRIMARY_ACTIVE
                    and self._failover_recovery_at is not None):
                elapsed = time.monotonic() - self._failover_recovery_at  # Pri-13
                if elapsed < self._slow_start_duration:
                    min_w = self._config.min_primary_weight
                    ramp = min_w + (100.0 - min_w) * (elapsed / self._slow_start_duration)
                    self._set_weights(ramp, f"Slow start: {elapsed:.0f}s/{self._slow_start_duration:.0f}s")
                    self._set_state(LoadBalancerState.BALANCING)
                    return
                else:
                    self._failover_recovery_at = None  # Slow start complete

        # Track reachability transitions for gradual recovery
        if not self._primary.reachable:
            self._primary_was_unreachable = True
        if not self._secondary.reachable:
            self._secondary_was_unreachable = True

        # If secondary is unreachable, all traffic goes to primary
        if not self._secondary.reachable:
            self._set_weights(100.0, "Secondary unreachable")
            self._set_state(LoadBalancerState.IDLE)
            return

        # If primary is unreachable, all traffic goes to secondary
        if not self._primary.reachable:
            self._set_weights(self._config.min_primary_weight, "Primary unreachable")
            self._set_state(LoadBalancerState.BALANCING)
            return

        # Gradual reintroduction after radio recovery
        if self._primary_was_unreachable and self._primary.reachable:
            self._primary_was_unreachable = False
            # Start with low primary weight and ramp up gradually
            with self._weight_lock:
                if self._primary_weight < 30.0:
                    logger.info("Primary recovered — gradual weight ramp-up from %.0f%%",
                               self._primary_weight)

        if self._secondary_was_unreachable and self._secondary.reachable:
            self._secondary_was_unreachable = False
            logger.info("Secondary recovered — available for load balancing")

        p_tx = self._primary.tx_utilization
        s_tx = self._secondary.tx_utilization
        threshold = self._config.tx_threshold
        tx_max = self._config.tx_max

        # Both radios high TX — saturated
        if p_tx >= tx_max and s_tx >= tx_max:
            self._set_state(LoadBalancerState.SATURATED)
            # Hold current weights, just warn
            logger.warning(
                "TX SATURATED: primary=%.1f%% secondary=%.1f%% — "
                "both radios at capacity",
                p_tx, s_tx,
            )
            return

        # Hysteresis: enter BALANCING at tx_threshold, return to IDLE
        # at tx_threshold - recovery_margin to prevent boundary flapping
        with self._state_lock:
            current_state = self._state
        if current_state == LoadBalancerState.BALANCING:
            idle_threshold = threshold - self._config.recovery_margin
        else:
            idle_threshold = threshold

        # Primary below threshold — all traffic to primary
        if p_tx < idle_threshold:
            target = 100.0
            new_state = LoadBalancerState.IDLE
        else:
            # Scale: at tx_threshold → 100/0, at tx_max → min_primary/rest
            range_size = tx_max - threshold
            if range_size <= 0:
                ratio = 1.0
            else:
                ratio = min(1.0, (p_tx - threshold) / range_size)

            min_w = self._config.min_primary_weight

            # Factor secondary TX: reduce offload as secondary approaches tx_max
            if s_tx > threshold and s_tx < tx_max:
                secondary_headroom = (tx_max - s_tx) / (tx_max - threshold)
                ratio = ratio * secondary_headroom

            target = max(min_w, 100.0 - (ratio * (100.0 - min_w)))
            new_state = LoadBalancerState.BALANCING

        # Gradual adjustment — move toward target by weight_change_rate
        with self._weight_lock:
            current = self._primary_weight

        rate = self._config.weight_change_rate
        if current > target:
            new_weight = max(target, current - rate)
        elif current < target:
            new_weight = min(target, current + rate)
        else:
            new_weight = current

        self._set_weights(new_weight, f"p_tx={p_tx:.1f}% s_tx={s_tx:.1f}%")
        self._set_state(new_state)

    def _set_weights(self, primary_weight: float, reason: str) -> None:
        """Update TX weights with logging."""
        with self._weight_lock:
            old = self._primary_weight
            self._primary_weight = max(0.0, min(100.0, primary_weight))

        if abs(old - primary_weight) > 0.5:
            logger.info(
                "TX weights: primary=%.0f%% secondary=%.0f%% (%s)",
                primary_weight, 100.0 - primary_weight, reason,
            )

    def _set_state(self, new_state: LoadBalancerState) -> None:
        """Transition to a new state if different from current."""
        with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return
            self._state = new_state

        self._last_state_change = time.time()

        with self._weight_lock:
            p_weight = self._primary_weight

        event = FailoverEvent(
            timestamp=datetime.now(),
            from_state=old_state,  # type: ignore[arg-type]
            to_state=new_state,  # type: ignore[arg-type]
            reason=f"weights={p_weight:.0f}/{100-p_weight:.0f}",
            primary_utilization=self._primary.tx_utilization,
            secondary_utilization=self._secondary.tx_utilization,
        )
        self._events.append(event)

        logger.warning(
            "LOAD BALANCER: %s -> %s | primary_tx=%.1f%% secondary_tx=%.1f%% | "
            "weights=%.0f/%.0f",
            old_state.value, new_state.value,
            self._primary.tx_utilization, self._secondary.tx_utilization,
            p_weight, 100.0 - p_weight,
        )

        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state,
                                      f"weights={p_weight:.0f}/{100-p_weight:.0f}")
            except Exception as e:
                logger.error("Load balancer callback error: %s", e)
