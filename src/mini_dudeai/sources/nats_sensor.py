"""Poll WireClaw/OpenClaw sensors over NATS, emit Conditions on breach.

Phase A of the standalone ("dude-claw") variant: each tick this source does a
bounded request/reply `sensor_read` against `<device>.tool_exec` for every
configured sensor and emits a Condition ONLY while the reading breaches its
threshold — the engine's presence/edge model then gives edge_up on first
breach and edge_down on recovery, with grace_s/cooldown noise gates applying
as usual. Threshold evaluation lives HERE (the engine matches on condition
presence — kind/subject_glob/extras — not numeric predicates; same pattern as
"filtering needs a preset").

Honest failure modes (the #80 checklist, applied at write time):
  - transport error / `"ok": false` reply / unparseable reading → a
    `source_error` Condition, never a value. The engine sees the
    source_error and HOLDS this source's last-good conditions, so a sensor
    that was breaching cannot read as "recovered" because we went blind.
  - a reply with no extractable number is an error, not 0.0.
  - zero configured sensors is a construction error (a sensor poller whose
    whole purpose is sensors), caught at authoring time.

WireClaw reply shape (docs/OPENCLAW.md): {"ok": true, "result": "chip_temp: 33.2 C"}
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from ..nats_client import NatsConnection, NatsError
from .base import Condition, Source

_OPS = {
    "gt": lambda v, t: v > t,
    "ge": lambda v, t: v >= t,
    "lt": lambda v, t: v < t,
    "le": lambda v, t: v <= t,
}
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _extract_value(result) -> float | None:
    """Pull the numeric reading out of a tool_exec result.

    Accepts a bare number, or WireClaw's "name: 33.2 C" string (first number
    AFTER the colon, so a digit in the sensor name — "temp2" — can't be
    mistaken for the reading). Returns None when no number exists — the
    caller maps None to source_error, never to a value.
    """
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        return float(result)
    if not isinstance(result, str):
        return None
    text = result.split(":", 1)[1] if ":" in result else result
    m = _NUM_RE.search(text)
    return float(m.group(0)) if m else None


class NatsSensorSource(Source):
    """Per-tick OpenClaw `sensor_read` polls with source-side thresholds.

    Args:
        server: NATS server ('host', 'host:port', or 'nats://host:port')
        sensors: list of dicts, each requiring:
            device     WireClaw device name (NATS subject prefix)
            sensor     sensor label (Condition subject; the `name` sent to
                       sensor_read)
            threshold  numeric bound
          and optionally:
            op         gt|ge|lt|le (default gt — "breach when above")
            tool       tool_exec tool (default "sensor_read"; e.g.
                       "temperature_read" reads chip temp with NO device-side
                       sensor registration — the day-1 bring-up path)
        kind: Condition kind emitted on breach (default "sensor_breach")
        token: NATS auth token (None when the server is bind/firewall-guarded)
        timeout_s: per-request bound; one wedged device costs at most this
        name: source identity (defaults to "nats_sensor:<server>")
    """

    def __init__(self, server: str, sensors: list[dict],
                 kind: str = "sensor_breach", token: str | None = None,
                 timeout_s: float = 5.0, name: str | None = None) -> None:
        if not sensors:
            raise ValueError(
                "NatsSensorSource requires at least one sensor — a sensor "
                "poller with zero sensors observes nothing")
        for i, s in enumerate(sensors):
            if not isinstance(s, dict):
                raise ValueError(f"sensors[{i}] must be an object")
            for field in ("device", "sensor", "threshold"):
                if field not in s:
                    raise ValueError(
                        f"sensors[{i}] missing required field {field!r}")
            op = s.get("op", "gt")
            if op not in _OPS:
                raise ValueError(
                    f"sensors[{i}] op {op!r} not one of {sorted(_OPS)}")
            float(s["threshold"])  # authoring-time loud on a non-numeric bound
        self.server = server
        self.sensors = sensors
        self.kind = kind
        self.token = token
        self.timeout_s = float(timeout_s)
        self.name = name or f"nats_sensor:{server}"

    def collect(self) -> Iterable[Condition]:
        try:
            conn = NatsConnection(self.server, token=self.token,
                                  timeout_s=self.timeout_s)
            conn.connect()
        except NatsError as e:
            # the whole bus is unreachable — one source_error; the engine
            # holds last-good conditions (blindness is not recovery)
            yield Condition(
                kind="source_error", subject=self.name,
                detail=f"NATS unreachable: {e}", source=self.name,
            )
            return
        try:
            for spec in self.sensors:
                yield from self._poll_one(conn, spec)
        finally:
            conn.close()

    def _poll_one(self, conn: NatsConnection, spec: dict) -> Iterable[Condition]:
        device, sensor = str(spec["device"]), str(spec["sensor"])
        subject = f"{device}.{sensor}"
        tool = str(spec.get("tool", "sensor_read"))
        call: dict = {"tool": tool}
        if tool == "sensor_read":
            call["name"] = sensor
        try:
            reply = conn.request(f"{device}.tool_exec", json.dumps(call))
        except NatsError as e:
            yield Condition(
                kind="source_error", subject=subject,
                detail=f"sensor_read failed: {e}", source=self.name,
            )
            return
        if not isinstance(reply, dict):
            yield Condition(
                kind="source_error", subject=subject,
                detail=f"sensor_read returned non-object reply: {reply!r:.120}",
                source=self.name,
            )
            return
        if not reply.get("ok"):
            yield Condition(
                kind="source_error", subject=subject,
                detail=f"device reported failure: "
                       f"{reply.get('error') or reply.get('result') or reply!r:.120}",
                source=self.name,
            )
            return
        result = reply.get("result", reply.get("value"))
        value = _extract_value(result)
        if value is None:
            # no number in the reply — an error, never 0.0
            yield Condition(
                kind="source_error", subject=subject,
                detail=f"unparseable reading: {result!r:.120}",
                source=self.name,
            )
            return
        op = spec.get("op", "gt")
        threshold = float(spec["threshold"])
        if _OPS[op](value, threshold):
            yield Condition(
                kind=self.kind, subject=subject,
                detail=f"{sensor}={value} {op} {threshold} (raw: {result})",
                source=self.name,
                extras={
                    "device": device, "sensor": sensor, "value": value,
                    "op": op, "threshold": threshold,
                },
            )
