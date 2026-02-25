"""
Network Latency Monitor for MeshForge NOC.

Monitors TCP response times to mesh network services, tracks jitter,
and flags degradation. Used by diagnostics and AI assistant for
real-time network health assessment.

Services monitored:
- meshtasticd TCP (4403) - mesh radio daemon
- meshtasticd HTTPS (9443) - web client
- rnsd (37428) - Reticulum shared instance
- MQTT (1883) - message broker
"""

import socket
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class LatencySample:
    """Single latency measurement."""
    timestamp: float
    rtt_ms: float
    success: bool


@dataclass
class ServiceHealth:
    """Health summary for a monitored service."""
    name: str
    host: str
    port: int
    samples: deque = field(default_factory=lambda: deque(maxlen=120))

    @property
    def is_reachable(self) -> bool:
        """Service responded to last probe."""
        if not self.samples:
            return False
        return self.samples[-1].success

    @property
    def avg_rtt_ms(self) -> float:
        """Average RTT over recent samples."""
        successful = [s.rtt_ms for s in self.samples if s.success]
        if not successful:
            return 0.0
        return sum(successful) / len(successful)

    @property
    def jitter_ms(self) -> float:
        """Jitter: standard deviation of RTT."""
        successful = [s.rtt_ms for s in self.samples if s.success]
        if len(successful) < 2:
            return 0.0
        avg = sum(successful) / len(successful)
        variance = sum((x - avg) ** 2 for x in successful) / len(successful)
        return variance ** 0.5

    @property
    def packet_loss_pct(self) -> float:
        """Percentage of failed probes."""
        if not self.samples:
            return 100.0
        failed = sum(1 for s in self.samples if not s.success)
        return (failed / len(self.samples)) * 100.0

    @property
    def status(self) -> str:
        """HEALTHY, DEGRADED, or DOWN."""
        if not self.samples:
            return "UNKNOWN"
        if not self.is_reachable:
            return "DOWN"
        if self.jitter_ms > 50 or self.avg_rtt_ms > 200 or self.packet_loss_pct > 10:
            return "DEGRADED"
        return "HEALTHY"

    def summary(self) -> Dict:
        """Dict summary for JSON/AI consumption."""
        return {
            'name': self.name,
            'host': self.host,
            'port': self.port,
            'status': self.status,
            'reachable': self.is_reachable,
            'avg_rtt_ms': round(self.avg_rtt_ms, 1),
            'jitter_ms': round(self.jitter_ms, 1),
            'packet_loss_pct': round(self.packet_loss_pct, 1),
            'samples': len(self.samples),
        }


# Default services to monitor
DEFAULT_SERVICES = [
    ('meshtasticd_tcp', 'localhost', 4403),
    ('meshtasticd_http', 'localhost', 4403),
    ('rnsd', 'localhost', 37428),
    ('mqtt', 'localhost', 1883),
]


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> Tuple[bool, float]:
    """
    Measure TCP connection time to a service.

    Returns:
        (success, rtt_ms) - Whether connection succeeded and round-trip time
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    start = time.monotonic()
    try:
        sock.connect((host, port))
        rtt = (time.monotonic() - start) * 1000
        return True, rtt
    except (socket.timeout, ConnectionRefusedError, OSError):
        rtt = (time.monotonic() - start) * 1000
        return False, rtt
    finally:
        sock.close()


class LatencyMonitor:
    """
    Continuous network latency monitor for NOC services.

    Probes services at configurable intervals and maintains
    rolling history for trend analysis.

    Usage:
        monitor = LatencyMonitor()
        monitor.start()  # Background thread

        # Query anytime
        health = monitor.get_health()
        for name, svc in health.items():
            print(f"{name}: {svc.status} ({svc.avg_rtt_ms:.1f}ms)")

        monitor.stop()
    """

    def __init__(self, services: Optional[List[Tuple[str, str, int]]] = None,
                 interval_sec: float = 10.0):
        """
        Args:
            services: List of (name, host, port) tuples to monitor
            interval_sec: Seconds between probe cycles
        """
        self._services: Dict[str, ServiceHealth] = {}
        self._interval = interval_sec
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        for name, host, port in (services or DEFAULT_SERVICES):
            self._services[name] = ServiceHealth(name=name, host=host, port=port)

    def probe_once(self) -> Dict[str, ServiceHealth]:
        """Run one probe cycle across all services. Thread-safe."""
        with self._lock:
            for svc in self._services.values():
                success, rtt = probe_tcp(svc.host, svc.port)
                svc.samples.append(LatencySample(
                    timestamp=time.time(),
                    rtt_ms=rtt,
                    success=success,
                ))
            return dict(self._services)

    def get_health(self) -> Dict[str, ServiceHealth]:
        """Get current health status for all services."""
        with self._lock:
            return dict(self._services)

    def get_summary(self) -> List[Dict]:
        """Get JSON-serializable summary for all services."""
        with self._lock:
            return [svc.summary() for svc in self._services.values()]

    def get_degraded(self) -> List[str]:
        """Get names of services that are DEGRADED or DOWN."""
        with self._lock:
            return [
                svc.name for svc in self._services.values()
                if svc.status in ('DEGRADED', 'DOWN')
            ]

    def start(self):
        """Start background monitoring thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop background monitoring."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self):
        """Background probe loop."""
        while self._running and not self._stop_event.is_set():
            self.probe_once()
            self._stop_event.wait(self._interval)


# Module-level singleton for shared access
_monitor: Optional[LatencyMonitor] = None


def get_latency_monitor(auto_start: bool = True) -> LatencyMonitor:
    """Get or create the shared latency monitor singleton."""
    global _monitor
    if _monitor is None:
        _monitor = LatencyMonitor()
        if auto_start:
            _monitor.start()
    return _monitor
