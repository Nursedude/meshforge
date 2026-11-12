"""The mesh bridge's queue DRAIN (MF025 split of mesh_bridge.py, 2026-11-11):
which queue is drained this tick (BR-T #4 direction rule) and the #14 hold
status read. Mixed into MeshtasticPresetBridge; reads its legs via ``self``.

⚠️ NO LEG GATE HERE (landscape §B #23, reverted 2026-11-11 after Fable r2 by
running): a drain that skips a queue because its leg is down runs AHEAD of the
sender's per-row decisions — dual-path dedup (content already on the far mesh
resolves now), DM refusal, the downlink path — and of process_once's clock-step
shift + guarded expiry. Same kind as #14 v1's ``ready_fn`` gate (rejected r1).
The deferral lives at the refusal, inside the sender, after all of them.
``TestDrainOffersEveryQueue`` pins it: every direction-allowed queue is offered
to its sender each pass, whatever the leg state.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("gateway.mesh_bridge")


class MeshBridgeDrainMixin:
    """Drain policy for MeshtasticPresetBridge (needs ``bridge_config``,
    ``_primary_to_secondary`` / ``_secondary_to_primary`` and
    ``_leg_can_write`` on the host class)."""

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

    def _drain_once(self) -> int:
        """One pass of the bridge loop: every direction-allowed queue is
        offered to its sender (process_once), whatever its leg's state — the
        sender decides each row (see the module docstring)."""
        processed = 0
        for ingress, queue in (("primary", self._primary_to_secondary),
                               ("secondary", self._secondary_to_primary)):
            if self._direction_allows(ingress):
                processed += queue.process_once(batch_size=5)
        return processed

    def _held_link_down(self, queue, leg: str) -> bool:
        return queue.get_queue_depth() > 0 and not self._leg_can_write(leg)
