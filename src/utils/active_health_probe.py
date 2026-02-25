"""
Active Health Probes for MeshForge Gateway Services.

Implements proactive health checking based on NGINX active health check pattern.
Unlike passive health (triggered by operations), active probes run periodically
to detect failures before user attempts connection.

Usage:
    from utils.active_health_probe import ActiveHealthProbe, HealthResult

    probe = ActiveHealthProbe(interval=30, fails=3, passes=2)
    probe.register_check("meshtastic", probe.check_meshtastic)
    probe.register_check("rns", probe.check_rns)

    probe.start()  # Background thread

    # Get current health state
    if probe.is_healthy("meshtastic"):
        connect_to_meshtastic()

    # Get detailed status
    status = probe.get_status("meshtastic")
    print(f"State: {status['state']}, Consecutive: {status['consecutive']}")

    probe.stop()

Reference:
    NGINX active health checks:
    https://docs.nginx.com/nginx/admin-guide/load-balancer/http-health-check/
"""

import logging
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, List
from enum import Enum

from utils.event_bus import emit_service_status
from utils.service_check import check_udp_port, check_rns_shared_instance

logger = logging.getLogger(__name__)


class HealthState(Enum):
    """Health state for a monitored service."""
    UNKNOWN = "unknown"    # Not yet checked
    HEALTHY = "healthy"    # Passing checks
    UNHEALTHY = "unhealthy"  # Failing checks
    RECOVERING = "recovering"  # Transitioning from unhealthy to healthy


@dataclass
class HealthResult:
    """Result of a single health check."""
    healthy: bool
    reason: str = ""
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def __bool__(self) -> bool:
        return self.healthy


@dataclass
class ServiceHealthState:
    """Tracks health state for a single service with hysteresis."""
    name: str
    state: HealthState = HealthState.UNKNOWN
    consecutive_passes: int = 0
    consecutive_fails: int = 0
    last_check: Optional[float] = None
    last_result: Optional[HealthResult] = None
    total_checks: int = 0
    total_passes: int = 0
    total_fails: int = 0

    @property
    def uptime_percent(self) -> float:
        """Calculate uptime percentage based on total checks."""
        if self.total_checks == 0:
            return 0.0
        return (self.total_passes / self.total_checks) * 100


class ActiveHealthProbe:
    """
    Proactive health checking for mesh services.

    Based on NGINX active health check pattern:
    - Periodic checks independent of traffic
    - Hysteresis: Multiple consecutive fails before marking unhealthy
    - Recovery: Multiple consecutive passes before marking healthy

    Attributes:
        interval: Seconds between health checks
        fails: Consecutive failures to mark service unhealthy
        passes: Consecutive passes to mark service healthy
    """

    def __init__(
        self,
        interval: int = 30,
        fails: int = 3,
        passes: int = 2,
    ):
        """
        Initialize active health probe.

        Args:
            interval: Seconds between health checks (default: 30)
            fails: Consecutive failures to mark unhealthy (default: 3)
            passes: Consecutive passes to mark healthy (default: 2)
        """
        self.interval = interval
        self.fails = fails
        self.passes = passes

        self._checks: Dict[str, Callable[[], HealthResult]] = {}
        self._states: Dict[str, ServiceHealthState] = {}
        self._callbacks: Dict[str, List[Callable[[str, HealthState], None]]] = {
            "on_healthy": [],
            "on_unhealthy": [],
            "on_state_change": [],
        }

        self._stop_event = threading.Event()  # Set to signal stop
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    def register_check(
        self,
        service_name: str,
        check_fn: Callable[[], HealthResult],
    ) -> None:
        """
        Register a health check function for a service.

        Args:
            service_name: Unique name for the service
            check_fn: Function that returns HealthResult
        """
        with self._lock:
            self._checks[service_name] = check_fn
            self._states[service_name] = ServiceHealthState(name=service_name)
            logger.debug(f"Registered health check: {service_name}")

    def register_callback(
        self,
        event: str,
        callback: Callable[[str, HealthState], None],
    ) -> None:
        """
        Register a callback for health state changes.

        Args:
            event: Event type - "on_healthy", "on_unhealthy", "on_state_change"
            callback: Function(service_name, new_state)
        """
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _run_check(self, service_name: str) -> HealthResult:
        """Run health check for a service and update state."""
        check_fn = self._checks.get(service_name)
        if not check_fn:
            return HealthResult(healthy=False, reason="no_check_registered")

        start_time = time.time()
        try:
            result = check_fn()
            result.latency_ms = (time.time() - start_time) * 1000
            result.timestamp = time.time()
        except Exception as e:
            result = HealthResult(
                healthy=False,
                reason=f"check_exception: {e}",
                latency_ms=(time.time() - start_time) * 1000,
            )

        # Update state with hysteresis
        with self._lock:
            state = self._states[service_name]
            old_state = state.state

            state.last_check = result.timestamp
            state.last_result = result
            state.total_checks += 1

            if result.healthy:
                state.consecutive_passes += 1
                state.consecutive_fails = 0
                state.total_passes += 1

                # Check if we should transition to healthy
                if state.state != HealthState.HEALTHY:
                    if state.consecutive_passes >= self.passes:
                        state.state = HealthState.HEALTHY
                        state.consecutive_passes = 0
                        logger.info(f"Health probe: {service_name} is now HEALTHY")
                    elif state.state == HealthState.UNHEALTHY:
                        state.state = HealthState.RECOVERING
                        logger.debug(
                            f"Health probe: {service_name} recovering "
                            f"({state.consecutive_passes}/{self.passes})"
                        )
            else:
                state.consecutive_fails += 1
                state.consecutive_passes = 0
                state.total_fails += 1

                # Check if we should transition to unhealthy
                if state.state != HealthState.UNHEALTHY:
                    if state.consecutive_fails >= self.fails:
                        state.state = HealthState.UNHEALTHY
                        state.consecutive_fails = 0
                        logger.warning(
                            f"Health probe: {service_name} is now UNHEALTHY: "
                            f"{result.reason}"
                        )
                    elif state.state == HealthState.RECOVERING:
                        # Reset to unhealthy if we fail during recovery
                        state.state = HealthState.UNHEALTHY
                        logger.debug(
                            f"Health probe: {service_name} recovery failed"
                        )

            # Fire callbacks on state change
            new_state = state.state
            if old_state != new_state:
                self._fire_callbacks(service_name, new_state)

        return result

    def _fire_callbacks(self, service_name: str, new_state: HealthState) -> None:
        """Fire registered callbacks for state change."""
        # Always fire state_change
        for callback in self._callbacks["on_state_change"]:
            try:
                callback(service_name, new_state)
            except Exception as e:
                logger.debug(f"Health callback error: {e}")

        # Fire specific event callbacks
        if new_state == HealthState.HEALTHY:
            for callback in self._callbacks["on_healthy"]:
                try:
                    callback(service_name, new_state)
                except Exception as e:
                    logger.debug(f"Health callback error: {e}")
        elif new_state == HealthState.UNHEALTHY:
            for callback in self._callbacks["on_unhealthy"]:
                try:
                    callback(service_name, new_state)
                except Exception as e:
                    logger.debug(f"Health callback error: {e}")

    def _probe_loop(self) -> None:
        """Background thread that runs periodic health checks."""
        logger.info(
            f"Active health probe started (interval={self.interval}s, "
            f"fails={self.fails}, passes={self.passes})"
        )

        while not self._stop_event.is_set():
            services = list(self._checks.keys())
            for service_name in services:
                if self._stop_event.is_set():
                    break
                try:
                    self._run_check(service_name)
                except Exception as e:
                    logger.debug(f"Health check error for {service_name}: {e}")

            # Wait for interval or until stop is signaled
            # wait() returns True if event was set, False on timeout
            self._stop_event.wait(self.interval)

        logger.info("Active health probe stopped")

    def start(self) -> None:
        """Start the background health probe thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._probe_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background health probe thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def check_now(self, service_name: str) -> HealthResult:
        """
        Run an immediate health check (bypass interval).

        Args:
            service_name: Service to check

        Returns:
            HealthResult from the check
        """
        return self._run_check(service_name)

    def is_healthy(self, service_name: str) -> bool:
        """
        Check if a service is currently healthy.

        Args:
            service_name: Service to check

        Returns:
            True if service state is HEALTHY
        """
        with self._lock:
            state = self._states.get(service_name)
            if not state:
                return False
            return state.state == HealthState.HEALTHY

    def get_status(self, service_name: str) -> Optional[Dict]:
        """
        Get detailed health status for a service.

        Args:
            service_name: Service to get status for

        Returns:
            Dict with state info, or None if service not registered
        """
        with self._lock:
            state = self._states.get(service_name)
            if not state:
                return None

            return {
                "name": state.name,
                "state": state.state.value,
                "consecutive_passes": state.consecutive_passes,
                "consecutive_fails": state.consecutive_fails,
                "last_check": state.last_check,
                "last_result": {
                    "healthy": state.last_result.healthy,
                    "reason": state.last_result.reason,
                    "latency_ms": state.last_result.latency_ms,
                } if state.last_result else None,
                "uptime_percent": round(state.uptime_percent, 1),
                "total_checks": state.total_checks,
            }

    def get_all_status(self) -> Dict[str, Dict]:
        """Get health status for all registered services."""
        result = {}
        for service_name in self._checks:
            status = self.get_status(service_name)
            if status:
                result[service_name] = status
        return result

    # =========================================================================
    # Built-in health check functions
    # =========================================================================

    def check_meshtastic(self) -> HealthResult:
        """
        Probe meshtasticd with lightweight request.

        Uses 'meshtastic --info' as a quick connectivity test.
        This is lightweight and doesn't modify device state.
        """
        try:
            result = subprocess.run(
                ["meshtastic", "--info"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                return HealthResult(healthy=True, reason="info_success")

            # Check for specific error patterns
            stderr = result.stderr.lower()
            if "no device" in stderr or "not found" in stderr:
                return HealthResult(healthy=False, reason="no_device")
            if "connection refused" in stderr:
                return HealthResult(healthy=False, reason="connection_refused")
            if "permission denied" in stderr:
                return HealthResult(healthy=False, reason="permission_denied")

            return HealthResult(
                healthy=False,
                reason=f"exit_code_{result.returncode}",
            )

        except subprocess.TimeoutExpired:
            return HealthResult(healthy=False, reason="timeout")
        except FileNotFoundError:
            return HealthResult(healthy=False, reason="meshtastic_cli_not_found")
        except Exception as e:
            return HealthResult(healthy=False, reason=str(e)[:100])

    def check_rns_port(self, port: int = 37428, host: str = "127.0.0.1") -> HealthResult:
        """
        Probe RNS shared instance availability.

        Uses check_rns_shared_instance() which checks abstract Unix domain
        sockets (Linux default), TCP, and UDP for reliable detection.

        Args:
            port: RNS shared instance port for TCP/UDP fallback (default: 37428)
            host: Host to check (default: 127.0.0.1)
        """
        try:
            if check_rns_shared_instance(port=port):
                return HealthResult(healthy=True, reason="shared_instance_available")
            return HealthResult(healthy=False, reason="shared_instance_unavailable")
        except Exception as e:
            return HealthResult(healthy=False, reason=f"check_error: {e}")

    def check_systemd_service(self, service_name: str) -> HealthResult:
        """
        Check if a systemd service is active and running.

        Args:
            service_name: Name of the systemd service
        """
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                status = result.stdout.strip()
                if status == "active":
                    return HealthResult(healthy=True, reason="active")
                return HealthResult(healthy=False, reason=f"status_{status}")

            status = result.stdout.strip()
            return HealthResult(healthy=False, reason=f"inactive_{status}")

        except subprocess.TimeoutExpired:
            return HealthResult(healthy=False, reason="timeout")
        except FileNotFoundError:
            return HealthResult(healthy=False, reason="systemctl_not_found")
        except Exception as e:
            return HealthResult(healthy=False, reason=str(e)[:100])

    def check_tcp_port(self, port: int, host: str = "localhost") -> HealthResult:
        """
        Check if a TCP port is accepting connections.

        Args:
            port: TCP port number
            host: Host to check (default: localhost)
        """
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            if result == 0:
                return HealthResult(healthy=True, reason="connected")
            return HealthResult(healthy=False, reason=f"connect_failed_{result}")
        except socket.timeout:
            return HealthResult(healthy=False, reason="timeout")
        except socket.error as e:
            return HealthResult(healthy=False, reason=f"socket_error: {e}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


def _emit_state_change(service_name: str, new_state: HealthState) -> None:
    """Callback that bridges health probe state changes to the EventBus.

    Emits a ServiceEvent whenever a service transitions between states,
    enabling the status bar and other subscribers to react without polling.
    """
    available = new_state == HealthState.HEALTHY
    emit_service_status(
        service_name=service_name,
        available=available,
        message=f"{service_name}: {new_state.value}",
    )


# Module-level singleton so all callers share one probe instance
_health_probe: Optional[ActiveHealthProbe] = None
_probe_lock = threading.Lock()


def get_health_probe(
    interval: int = 30,
    fails: int = 3,
    passes: int = 2,
) -> ActiveHealthProbe:
    """
    Get the singleton health probe, creating it on first call.

    Returns the same instance to every caller so that all components
    share one background monitoring thread. The probe is NOT started
    automatically — call .start() when ready.

    Args:
        interval: Seconds between checks (only used on first call)
        fails: Consecutive failures for unhealthy (only used on first call)
        passes: Consecutive passes for healthy (only used on first call)

    Returns:
        Configured ActiveHealthProbe (call .start() to begin monitoring)
    """
    global _health_probe
    with _probe_lock:
        if _health_probe is not None:
            return _health_probe
        _health_probe = create_gateway_health_probe(
            interval=interval, fails=fails, passes=passes,
        )
        return _health_probe


def create_gateway_health_probe(
    interval: int = 30,
    fails: int = 3,
    passes: int = 2,
) -> ActiveHealthProbe:
    """
    Create a pre-configured health probe for gateway services.

    Convenience factory that sets up standard checks for:
    - meshtasticd (systemd + TCP port 4403)
    - rnsd (UDP port 37428)
    - mosquitto (TCP port 1883)

    Automatically wires state changes to the EventBus so that
    status_bar and other subscribers get push updates.

    Args:
        interval: Seconds between checks
        fails: Consecutive failures for unhealthy
        passes: Consecutive passes for healthy

    Returns:
        Configured ActiveHealthProbe ready to start()
    """
    probe = ActiveHealthProbe(interval=interval, fails=fails, passes=passes)

    # Register standard gateway service checks
    probe.register_check(
        "meshtasticd",
        lambda: probe.check_tcp_port(4403),
    )
    probe.register_check(
        "rnsd",
        lambda: probe.check_rns_port(37428),
    )
    probe.register_check(
        "mosquitto",
        lambda: probe.check_tcp_port(1883),
    )

    # Wire state changes to EventBus for push-based status updates
    probe.register_callback("on_state_change", _emit_state_change)

    return probe
