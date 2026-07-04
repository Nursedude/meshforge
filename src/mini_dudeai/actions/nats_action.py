"""Drive a WireClaw node over NATS (OpenClaw `tool_exec`) when a rule fires.

Phase A of the standalone ("dude-claw") variant: request/reply against
`<device>.tool_exec` with a flat JSON tool call —
    {"tool": "gpio_write", "pin": 5, "value": 1}
    {"tool": "led_set", "r": 255, "g": 0, "b": 0}

Idempotency constraint: the engine's send-retry queue (#81) is
action-generic — a failed fire is re-attempted on later ticks. Re-applying a
STATE-SET ("set pin 5 high") minutes late is safe; re-firing a pulse is not.
Phase A therefore only allows state-set tools (IDEMPOTENT_TOOLS); anything
else is refused loudly at authoring/execute time rather than retried wrongly.

Edge semantics: edge_up sends `payload`; edge_down sends `payload_down` when
configured (e.g. relay on while hot, off on recovery) and otherwise records an
explicit skipped outcome — never a fake actuation.

Honest failure modes: a transport success carrying `"ok": false` is a FAILED
outcome (no valid-looking value from a degraded state); no reply is an error,
not a silent success.
"""
from __future__ import annotations

import json

from ..nats_client import NatsConnection, NatsError
from ..sources.base import Condition
from .base import Action, Outcome

# State-set tools only — safe under #81 retry re-application. Grows
# deliberately, not by default. display_print qualifies: re-writing the same
# text to the same row is a state-set (dude-claw fork +dudeclaw.1).
# display_alert qualifies the same way (+dudeclaw.15). NOTE the retry-queue
# ordering contract alone is NOT sufficient for state-sets: it only held
# when BOTH edges failed — a failed edge_up followed by a SUCCESSFUL
# edge_down left the stale edge_up queued, and its retry re-painted a
# recovered condition (banner resurrection). NatsAction therefore declares
# supersedes_pending_sends: the engine retires any older queued send the
# moment a newer transition exists (latest state wins, witnessed in
# history as send_retry_superseded).
# display_tier is DELIBERATELY absent: the tier glyph is an evidence-based
# claim owned by claw_metrics_push's live probes — a static rule payload
# cannot prove a cognition tier, so rules may never assert one.
IDEMPOTENT_TOOLS = frozenset({"gpio_write", "led_set", "actuator_set",
                              "display_print", "display_alert"})


def _check_payload(payload, where: str) -> str | None:
    """Return an error string when the payload is not a retry-safe tool call."""
    if not isinstance(payload, dict):
        return f"{where}: payload must be a JSON object, got {type(payload).__name__}"
    tool = payload.get("tool")
    if not tool:
        return f"{where}: payload missing required 'tool' field"
    if tool not in IDEMPOTENT_TOOLS:
        return (f"{where}: tool {tool!r} is not in the idempotent allowlist "
                f"{sorted(IDEMPOTENT_TOOLS)} — the retry queue re-applies "
                f"failed fires, which is only safe for state-set tools")
    return None


class NatsAction(Action):
    """OpenClaw tool_exec actuator.

    Args:
        server: NATS server ('host', 'host:port', or 'nats://host:port')
        device: default WireClaw device name (rule.action.device overrides)
        payload: default edge-up tool call (rule.action.payload overrides)
        payload_down: default edge-down tool call (rule.action.payload_down
            overrides). Absent → edge_down records an explicit skip.
        token: NATS auth token (None when the server is bind/firewall-guarded)
        timeout_s: per-request bound (a wedged device costs at most this)
    """

    name = "nats"
    # Actuators/displays are STATE-SETS: only the latest state is truth, so
    # the engine supersedes older queued sends on any newer transition (see
    # IDEMPOTENT_TOOLS note — the banner-resurrection fix).
    supersedes_pending_sends = True

    def __init__(self, server: str, device: str | None = None,
                 payload: dict | None = None, payload_down: dict | None = None,
                 token: str | None = None, timeout_s: float = 5.0) -> None:
        if not server:
            raise ValueError("NatsAction requires a server")
        # authoring-time loud: defaults are validated at construction so a
        # bad preset/config fails before the daemon ever runs a tick
        for p, label in ((payload, "payload"), (payload_down, "payload_down")):
            if p is not None:
                err = _check_payload(p, label)
                if err:
                    raise ValueError(err)
        self.server = server
        self.device = device
        self.payload = payload
        self.payload_down = payload_down
        self.token = token
        self.timeout_s = float(timeout_s)

    def execute(self, rule: dict, cond: Condition, transition: str) -> Outcome:
        action_cfg = rule.get("action") or {}
        outcome_name = f"nats_{transition}"
        device = action_cfg.get("device") or self.device
        if transition == "edge_up":
            payload = action_cfg.get("payload") or self.payload
        else:
            payload = action_cfg.get("payload_down") or self.payload_down
            if payload is None:
                # legitimate config (nothing to actuate on clear) — recorded
                # explicitly, never a fake actuation success
                return Outcome(action=outcome_name, ok=True,
                               extras={"skipped": "no payload_down configured"})
        if not device:
            return Outcome(action=outcome_name, ok=False,
                           error="no device configured (set action.device on "
                                 "the rule or device= on the action)")
        err = _check_payload(payload, f"rule {rule.get('id', '?')!r}")
        if err:
            return Outcome(action=outcome_name, ok=False, error=err)
        try:
            with NatsConnection(self.server, token=self.token,
                                timeout_s=self.timeout_s) as nc:
                reply = nc.request(f"{device}.tool_exec", json.dumps(payload))
        except NatsError as e:
            return Outcome(action=outcome_name, ok=False,
                           error=f"tool_exec transport failed: {e}")
        if not isinstance(reply, dict):
            return Outcome(action=outcome_name, ok=False,
                           error=f"non-object reply: {reply!r:.120}")
        if not reply.get("ok"):
            return Outcome(
                action=outcome_name, ok=False,
                error=f"device refused {payload.get('tool')!r}: "
                      f"{reply.get('error') or reply.get('result') or reply!r:.120}",
            )
        return Outcome(action=outcome_name, ok=True,
                       extras={"device": device, "tool": payload.get("tool"),
                               "reply": reply.get("result")})
