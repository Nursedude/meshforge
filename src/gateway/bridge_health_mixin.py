"""Subsystem-state / circuit-breaker mixin for :class:`RNSMeshtasticBridge`.

Part of the 2026-06-09 ``rns_bridge.py`` split (1,500-line rule,
``CLAUDE.md``). Pure code motion — no behaviour change.
``RNSMeshtasticBridge`` in ``rns_bridge.py`` is the only consumer.

Provided methods: ``_update_subsystem_state``, ``get_subsystem_state``,
``is_fully_healthy`` (property), ``can_send_to``, ``record_send_success``,
``record_send_failure``, ``get_open_circuits``, ``_sync_subsystem_states``.
"""

import logging
from typing import Any, Dict

from .bridge_health import SubsystemState

logger = logging.getLogger(__name__)


class BridgeHealthMixin:
    """Mixin: subsystem state management + circuit-breaker gates (Phase 2)."""

    def _update_subsystem_state(self, subsystem: str, state: SubsystemState) -> None:
        """Update a subsystem's state and emit an event if it changed.

        Args:
            subsystem: "meshtastic" or "rns"
            state: New SubsystemState value
        """
        old_state = self.health.set_subsystem_state(subsystem, state)
        if old_state != state:
            # Emit event for StatusBar and other listeners.
            # HAS_EVENT_BUS is read lazily off gateway.rns_bridge (not
            # imported at module top) so the documented test idiom
            # patch("gateway.rns_bridge.HAS_EVENT_BUS", ...) keeps
            # governing this method after the 2026-06-09 split.
            from . import rns_bridge as _rns_bridge_module
            if _rns_bridge_module.HAS_EVENT_BUS:
                try:
                    from utils.event_bus import emit_service_status
                    emit_service_status(
                        f"bridge_{subsystem}",
                        available=(state == SubsystemState.HEALTHY),
                        message=f"{subsystem}: {state.value}",
                    )
                except Exception as e:
                    logger.debug(f"Failed to emit subsystem state event: {e}")

    def get_subsystem_state(self, subsystem: str) -> SubsystemState:
        """Get the current state of a bridge subsystem.

        Args:
            subsystem: "meshtastic", "rns", or "meshcore"

        Returns:
            Current SubsystemState.
        """
        return self.health.get_subsystem_state(subsystem)

    @property
    def is_fully_healthy(self) -> bool:
        """Check if bridge is fully operational (both networks up)."""
        return self.health.is_bridge_fully_healthy()

    def can_send_to(self, destination: str) -> bool:
        """
        Check if we can send to a destination (circuit breaker check).

        Args:
            destination: Target node/identity ID

        Returns:
            True if sending is allowed, False if circuit is open
        """
        if self._circuit_breaker is None:
            return True
        return self._circuit_breaker.can_send(destination)

    def record_send_success(self, destination: str) -> None:
        """Record successful send to destination (for circuit breaker)."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success(destination)

    def record_send_failure(self, destination: str, error: str = "") -> None:
        """Record failed send to destination (for circuit breaker)."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure(destination, error)

    def get_open_circuits(self) -> Dict[str, Any]:
        """Get destinations with open circuits (currently blocked)."""
        if self._circuit_breaker is None:
            return {}
        return self._circuit_breaker.get_open_circuits()

    def _sync_subsystem_states(self) -> None:
        """Synchronize subsystem states from connection status.

        Called each bridge loop iteration. Both handlers manage their own
        reconnection, so we observe connection states and update accordingly.
        The RNS subsystem state is also updated in _rns_loop, but we sync
        here too so the bridge loop has accurate state even when _rns_loop
        is not running (e.g., in tests).
        """
        # Meshtastic
        if not self._mesh_handler:
            self._update_subsystem_state("meshtastic", SubsystemState.DISABLED)
        elif self._mesh_handler.is_connected:
            self._update_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        else:
            self._update_subsystem_state("meshtastic", SubsystemState.DISCONNECTED)

        # RNS (also managed by _rns_loop, but kept in sync here)
        if self._rns_init_failed_permanently:
            self._update_subsystem_state("rns", SubsystemState.DISABLED)
        elif self._connected_rns:
            self._update_subsystem_state("rns", SubsystemState.HEALTHY)
        # Note: don't overwrite DISCONNECTED here — _rns_loop handles transitions

        # MeshCore
        if not self._meshcore_handler:
            self._update_subsystem_state("meshcore", SubsystemState.DISABLED)
        elif self._meshcore_handler.is_connected:
            self._update_subsystem_state("meshcore", SubsystemState.HEALTHY)
        else:
            self._update_subsystem_state("meshcore", SubsystemState.DISCONNECTED)
