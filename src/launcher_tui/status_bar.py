"""
TUI Status Bar — persistent status line for whiptail/dialog --backtitle.

Collects and caches service status information to display a compact
status line at the top of every TUI dialog screen. Designed to be
fast (cached with TTL) since it runs on every menu interaction.

Usage:
    from status_bar import StatusBar

    bar = StatusBar()
    text = bar.get_status_line()
    # Returns: "MeshForge v0.4.7 | meshtasticd: ● | rnsd: ○ | mqtt: ○"

Enhanced in v0.4.8:
    - Integration with StartupChecker for conflict detection
    - Shows hardware status (SPI/USB)
    - Root/non-root indicator
"""

import time
import subprocess
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# Import centralized service checking
from utils.service_check import check_systemd_service, check_process_running, check_udp_port, check_rns_shared_instance

# Import startup checker for enhanced status
from startup_checks import StartupChecker, EnvironmentState, ServiceRunState
HAS_STARTUP_CHECKER = True

# Space weather API
from utils.space_weather import SpaceWeatherAPI

# EventBus for push-based updates
from utils.event_bus import event_bus

# Node tracker for initial node count seeding
from gateway.node_tracker import get_node_tracker

# RNS shared instance port (UDP) — rnsd must bind this to be functional
_RNS_PORT = 37428

# Cache TTL in seconds — how often to re-check service status
STATUS_CACHE_TTL = 10.0

# Space weather cache TTL — 5 minutes (matches NOAA update frequency)
SPACE_WEATHER_CACHE_TTL = 300.0

# Services to monitor
MONITORED_SERVICES = [
    ('meshtasticd', 'mesh'),
    ('rnsd', 'rns'),
    ('mosquitto', 'mqtt'),
]

# Status symbols (ASCII-safe for all terminals)
SYM_RUNNING = '*'
SYM_STOPPED = '-'
SYM_UNKNOWN = '?'


class StatusBar:
    """Collects and formats service status for the TUI backtitle.

    Caches results to avoid calling systemctl on every dialog render.

    When the ActiveHealthProbe is running, the status bar receives push
    updates via the EventBus instead of polling systemctl directly.
    Polling is kept as fallback for when the probe is not started.
    """

    def __init__(self, version: str = ""):
        """Initialize status bar.

        Args:
            version: MeshForge version string to display.
        """
        self.version = version
        self._cache: Dict[str, str] = {}
        self._cache_time: float = 0.0
        self._node_count: Optional[int] = None
        self._bridge_running: Optional[bool] = None
        # Subsystem states (Phase 2: Circuit Breakers)
        self._subsystem_states: Dict[str, str] = {}  # e.g. {"meshtastic": "healthy"}
        # Space weather (separate cache with longer TTL)
        self._space_weather: Optional[str] = None
        self._space_weather_time: float = 0.0
        # Enhanced startup checker (v0.4.8)
        self._startup_checker: Optional[StartupChecker] = None
        self._env_state: Optional[EnvironmentState] = None
        if HAS_STARTUP_CHECKER:
            self._startup_checker = StartupChecker()
        # Event-driven status updates from ActiveHealthProbe
        self._event_subscribed = False
        # Track which services have received at least one event push
        self._event_updated_services: set = set()
        # Unread message counter (Issue #17 Phase 3)
        self._unread_messages = 0
        self._subscribe_to_events()
        self._seed_node_count()

    def get_status_line(self) -> str:
        """Get the formatted status line for --backtitle.

        Returns:
            Compact status string suitable for terminal top bar.
        """
        self._refresh_if_stale()

        parts = []

        # Version
        if self.version:
            parts.append(f"MeshForge v{self.version}")
        else:
            parts.append("MeshForge")

        # Service statuses
        for service_name, short_name in MONITORED_SERVICES:
            status = self._cache.get(service_name, SYM_UNKNOWN)
            parts.append(f"{short_name}:{status}")

        # Node count if available
        if self._node_count is not None:
            parts.append(f"nodes:{self._node_count}")

        # Bridge status with subsystem detail
        if self._bridge_running is not None:
            bridge_label = self._format_bridge_status()
            parts.append(bridge_label)

        # Unread message count (Issue #17 Phase 3)
        if self._unread_messages > 0:
            parts.append(f"msg:{self._unread_messages}")

        # Space weather (compact format: SFI:125 K:2)
        if self._space_weather:
            parts.append(self._space_weather)

        return " | ".join(parts)

    def _refresh_if_stale(self) -> None:
        """Refresh cache if TTL has expired."""
        now = time.time()
        if now - self._cache_time >= STATUS_CACHE_TTL:
            self._cache_time = now
            self._check_services()
            self._check_bridge()

        # Space weather has separate (longer) TTL
        if now - self._space_weather_time >= SPACE_WEATHER_CACHE_TTL:
            self._space_weather_time = now
            self._check_space_weather()

    def _check_services(self) -> None:
        """Check status of all monitored services.

        Skips systemctl polling for services already receiving push
        updates via the EventBus (from ActiveHealthProbe).
        """
        for service_name, _ in MONITORED_SERVICES:
            if service_name in self._event_updated_services:
                continue  # Event bus is authoritative for this service
            self._cache[service_name] = self._check_systemd_active(service_name)

    def _check_systemd_active(self, service: str) -> str:
        """Check if a systemd service is active.

        Uses centralized service_check module when available.
        For rnsd, also verifies the RNS shared instance is reachable —
        systemd can report "active" while the service fails to bind its
        socket (zombie).

        Args:
            service: Service unit name.

        Returns:
            Status symbol character.
        """
        try:
            is_running, _ = check_systemd_service(service)
            if not is_running:
                return SYM_STOPPED
            # rnsd zombie detection: systemd active but shared instance not available
            if service == 'rnsd':
                if not check_rns_shared_instance():
                    logger.debug("rnsd active but shared instance not available")
                    return SYM_STOPPED
            return SYM_RUNNING
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return SYM_UNKNOWN

    def _check_bridge(self) -> None:
        """Check if the RNS-Meshtastic bridge process is running.

        Uses centralized service_check module when available.
        """
        try:
            self._bridge_running = check_process_running('rns_bridge')
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            self._bridge_running = None

    def _check_space_weather(self) -> None:
        """Fetch space weather from NOAA SWPC (non-blocking, cached).

        Uses a short timeout and runs in the calling thread since this
        is already called infrequently (5-min TTL). Falls back gracefully
        if network is unavailable.
        """
        try:
            api = SpaceWeatherAPI(timeout=5)  # Short timeout for TUI
            data = api.get_current_conditions()

            # Build compact status: "SFI:125 K:2"
            parts = []
            if data.solar_flux:
                parts.append(f"SFI:{int(data.solar_flux)}")
            if data.k_index is not None:
                parts.append(f"K:{data.k_index}")

            if parts:
                self._space_weather = " ".join(parts)
            else:
                self._space_weather = None

        except Exception as e:
            # Network error or API failure - don't break status bar
            logger.debug(f"Space weather fetch failed: {e}")
            self._space_weather = None

    def _format_bridge_status(self) -> str:
        """Format bridge status with subsystem detail.

        Returns compact status like:
        - bridge:* (both healthy)
        - bridge:DEGRADED(rns) (one side down)
        - bridge:- (bridge not running)
        """
        if not self._bridge_running:
            return f"bridge:{SYM_STOPPED}"

        if not self._subsystem_states:
            return f"bridge:{SYM_RUNNING}"

        mesh_state = self._subsystem_states.get("meshtastic", "disconnected")
        rns_state = self._subsystem_states.get("rns", "disconnected")

        mesh_ok = mesh_state == "healthy"
        rns_ok = rns_state == "healthy"

        if mesh_ok and rns_ok:
            return f"bridge:{SYM_RUNNING}"

        # Build degraded indicator showing which side(s) are down
        down = []
        if not mesh_ok:
            down.append("mesh")
        if not rns_ok:
            down.append("rns")

        if len(down) == 2:
            return "bridge:OFFLINE"

        return f"bridge:DEGRADED({down[0]})"

    def set_subsystem_states(self, states: Dict[str, str]) -> None:
        """Update subsystem states from bridge health data.

        Args:
            states: Dict of subsystem name → state value string.
                   e.g. {"meshtastic": "healthy", "rns": "disconnected"}
        """
        self._subsystem_states = states

    def set_node_count(self, count: int) -> None:
        """Update the displayed node count.

        Args:
            count: Number of known mesh nodes.
        """
        self._node_count = count

    def invalidate(self) -> None:
        """Force refresh on next access."""
        self._cache_time = 0.0

    def get_service_status(self, service_name: str) -> str:
        """Get cached status symbol for a service.

        Args:
            service_name: Systemd service name.

        Returns:
            Status symbol character.
        """
        self._refresh_if_stale()
        return self._cache.get(service_name, SYM_UNKNOWN)

    # =========================================================================
    # Event Bus Integration (Phase 1 — reliability engineering)
    # =========================================================================

    def _subscribe_to_events(self) -> None:
        """Subscribe to EventBus for push-based status updates.

        When the ActiveHealthProbe is running, it emits ServiceEvents on
        state changes. We listen for those and update our cache immediately
        instead of waiting for the next polling cycle.

        Also subscribes to node events for automatic node count updates
        and message events for unread message tracking.
        """
        if self._event_subscribed:
            return
        event_bus.subscribe('service', self._on_service_event)
        event_bus.subscribe('message', self._on_message_event)
        event_bus.subscribe('node', self._on_node_event)
        self._event_subscribed = True
        logger.debug("StatusBar subscribed to EventBus service+message+node events")

    def _seed_node_count(self) -> None:
        """Pull initial node count from the node tracker singleton.

        Without this, the status bar shows no node count until a new
        'discovered' event arrives from the EventBus.
        """
        try:
            tracker = get_node_tracker()
            nodes = tracker.get_all_nodes()
            if nodes:
                self._node_count = len(nodes)
                logger.debug(f"StatusBar seeded node count: {self._node_count}")
        except Exception as e:
            logger.debug(f"Failed to seed node count: {e}")

    def _on_service_event(self, event) -> None:
        """Handle a ServiceEvent from the EventBus.

        Updates the cache immediately so the next dialog render shows
        the new state without waiting for the TTL-based refresh.
        Also handles bridge subsystem state events (bridge_meshtastic,
        bridge_rns) emitted by the Phase 2 circuit breaker code.
        """
        service_name = getattr(event, 'service_name', None)
        if not service_name:
            return

        # Handle bridge subsystem state updates (Phase 2)
        if service_name.startswith('bridge_'):
            subsystem = service_name.removeprefix('bridge_')
            message = getattr(event, 'message', '')
            # Extract state from message like "meshtastic: healthy"
            if ':' in message:
                state_str = message.split(':', 1)[1].strip()
                self._subsystem_states[subsystem] = state_str
                logger.debug(f"StatusBar subsystem {subsystem}: {state_str}")
            return

        available = getattr(event, 'available', None)
        if available is not None:
            self._cache[service_name] = SYM_RUNNING if available else SYM_STOPPED
            self._event_updated_services.add(service_name)
            logger.debug(
                f"StatusBar updated {service_name} via event: "
                f"{'running' if available else 'stopped'}"
            )

    def _on_message_event(self, event) -> None:
        """Handle a MessageEvent from the EventBus.

        Increments the unread message counter shown in the status bar.
        Counter is reset when the user views messages.
        """
        direction = getattr(event, 'direction', '')
        if direction == 'rx':
            self._unread_messages += 1
            logger.debug(f"StatusBar unread count: {self._unread_messages}")

    def _on_node_event(self, event) -> None:
        """Handle a NodeEvent from the EventBus.

        Tracks node count for display in the status bar. Increments on
        'discovered'/'updated' events, decrements on 'lost' events.
        """
        event_type = getattr(event, 'event_type', '')
        if event_type == 'discovered':
            if self._node_count is None:
                self._node_count = 1
            else:
                self._node_count += 1
            logger.debug(f"StatusBar node count: {self._node_count}")

    def clear_unread(self) -> None:
        """Reset unread message counter (called when user views messages)."""
        self._unread_messages = 0

    # =========================================================================
    # Enhanced Status Methods (v0.4.8)
    # =========================================================================

    def get_environment(self, use_cache: bool = True) -> Optional[EnvironmentState]:
        """Get full environment state from startup checker.

        Args:
            use_cache: Use cached result if available

        Returns:
            EnvironmentState or None if startup checker unavailable
        """
        if not self._startup_checker:
            return None

        if use_cache and self._env_state:
            return self._env_state

        self._env_state = self._startup_checker.check_all(use_cache=use_cache)
        return self._env_state

    def get_alerts(self) -> List[str]:
        """Get list of current alerts (conflicts, failures, etc.).

        Returns:
            List of alert message strings
        """
        env = self.get_environment()
        if env:
            return env.get_alerts()
        return []

    def has_conflicts(self) -> bool:
        """Check if there are any port conflicts.

        Returns:
            True if conflicts detected
        """
        env = self.get_environment()
        if env:
            return env.has_conflicts
        return False

    def get_enhanced_status_line(self) -> str:
        """Get enhanced status line with hardware and conflict info.

        Returns:
            Status string with additional context
        """
        env = self.get_environment()
        if not env:
            return self.get_status_line()

        parts = []

        # Version
        if self.version:
            parts.append(f"MeshForge v{self.version}")
        else:
            parts.append("MeshForge")

        # Root indicator
        if not env.is_root:
            parts.append("[user]")

        # Service statuses from enhanced checker
        for name, info in env.services.items():
            if info.state == ServiceRunState.RUNNING:
                parts.append(f"{name[:4]}:{SYM_RUNNING}")
            elif info.state == ServiceRunState.FAILED:
                parts.append(f"{name[:4]}:!")
            else:
                parts.append(f"{name[:4]}:{SYM_STOPPED}")

        # Hardware indicator
        hw = env.hardware
        if hw.usb_serial_devices:
            parts.append("USB:*")
        elif hw.spi_available:
            parts.append("SPI:*")

        # Conflict warning
        if env.conflicts:
            parts.append(f"WARN:{len(env.conflicts)}")

        # Node count if available
        if self._node_count is not None:
            parts.append(f"nodes:{self._node_count}")

        # Space weather (compact)
        if self._space_weather:
            parts.append(self._space_weather)

        return " | ".join(parts)

    def refresh_environment(self) -> None:
        """Force refresh of environment state."""
        if self._startup_checker:
            self._startup_checker.invalidate_cache()
            self._env_state = None
