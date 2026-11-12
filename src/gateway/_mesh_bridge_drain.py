"""The mesh bridge's queue DRAIN (MF025 split of mesh_bridge.py, 2026-11-11):
which queue is drained this tick, and which waits on a leg that cannot take a
write (BR-T #4 direction rule, #14 hold status, landscape §B #23). Mixed into
MeshtasticPresetBridge; reads its legs through ``self``.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("gateway.mesh_bridge")


class MeshBridgeDrainMixin:
    """Drain policy for MeshtasticPresetBridge (needs ``bridge_config``,
    ``_primary_to_secondary`` / ``_secondary_to_primary``, ``_primary_downlink``,
    ``_leg_can_write`` and ``_note_link_down`` on the host class)."""

    def _direction_allows(self, ingress: str) -> bool:
        """THE direction rule (BR-T #4): may text heard on ``ingress`` cross?
        One reader for the RX gate and the queue drain, so they cannot drift."""
        return self.bridge_config.direction in (
            "bidirectional", f"{ingress}_to_"
            f"{'secondary' if ingress == 'primary' else 'primary'}")

    def _expire_undrained(self):
        """Review F5: a queue the drain skips (one-way ``direction``) never
        reaches process_once's expiry, so rows already in it — from before #4
        or a direction change — would read pending forever. Age them out here,
        witnessed like any other."""
        for ingress, queue in (("primary", self._primary_to_secondary),
                               ("secondary", self._secondary_to_primary)):
            if not self._direction_allows(ingress):
                queue.expire_overdue()

    def _leg_blocked(self, leg: str) -> bool:
        """§B #23: no row bound for ``leg`` can be written now — the leg cannot
        take a write and (primary only) no broker downlink could carry one."""
        if leg == "primary" and self._primary_downlink is not None:
            return False
        return not self._leg_can_write(leg)

    def _drain_once(self) -> int:
        """One pass over both queues. A queue whose destination leg is
        blocked is NOT drained (§B #23: each tick re-deferred every row — two
        SQLite writes per row per 2 s on SD); its rows still age out, and the
        outage is logged once."""
        processed = 0
        for ingress, dest, queue in (
                ("primary", "secondary", self._primary_to_secondary),
                ("secondary", "primary", self._secondary_to_primary)):
            if not self._direction_allows(ingress):
                continue
            if self._leg_blocked(dest):
                queue.expire_overdue()
                if queue.get_queue_depth() > 0:
                    self._note_link_down(dest, "leg cannot take a write")
                continue
            processed += queue.process_once(batch_size=5)
        return processed

    def _held_link_down(self, queue, leg: str) -> bool:
        return queue.get_queue_depth() > 0 and not self._leg_can_write(leg)
