# RNS-Meshtastic Gateway Setup Guide

> ## ⛔ SUPERSEDED (2026-06-04) — do not follow
>
> This guide predates the composable-bridges refactor, mesh_bridge
> (dual-radio), the MQTT-RX recommended path, Theme-A, and downlink
> injection. Current docs:
> - **Canonical runbook (bare box → bridged message)**: `docs/GATEWAY_DEPLOYMENT.md`
> - **Variants + config schema**: `docs/GATEWAY_BRIDGE_CONFIG_GUIDE.md`
> - **Validated templates**: `docs/gateway_config_templates/`
> - **Architecture/why**: `research/fleet_architecture_2026_06_03.md`
>
> Kept only so existing links (`INDEX.md`, `research/README.md`,
> `dude_ai_university.md`, the 2026-02-26 audit) don't 404.

---

> **The step-by-step body was removed 2026-07-07** (audit). It documented a
> config shape that no longer matches the code — `bridge_mode: "message_bridge"`,
> hard-coded `channel: 0`, a `/api/gateway/*` REST surface, and a
> `src.commands.gateway` import path that were all replaced by the
> composable-bridges model. Following it would have produced a silently-broken
> gateway. **Use `docs/GATEWAY_DEPLOYMENT.md`** — it is the single, field-tested,
> SF ↔ MeshForge ↔ RNS runbook (knob map + green-but-dead probes included).
