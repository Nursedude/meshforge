# mini-dudeai Standalone — the WireClaw-like personal agent (design)

> **Status:** Phases A + B SHIPPED 2026-06-11 (one day): A = `a171fec`
> (nats_sensor/nats_action/standalone preset, end-to-end proven on a Heltec
> V4 "dudeclaw-01"; brain on moc2 — topology forced a re-home, see
> `dudeclaw_heltec_v4_bringup.md`); B = `9d6c09f`+`aa9a521` (chat-compiler:
> English → Ollama qwen2.5:3b → ratify → write_candidate → live promotion,
> proven in production incl. a mis-compile caught at review → prompt fix).
> Bonus: display fork `0.4.0+dudeclaw.1` (not in this design — OLED status
> screen + display_print tool + fleet metrics pusher). Remaining: Phase C
> demo polish, Phase D onboarding, TUI chat front-end.
> Original design (2026-05-29) below, unchanged.

## What it is

A personal, chat-configurable, runs-24/7-offline agent on the operator's own
hardware — mini-dudeai's second mission alongside the fleet NOC watcher. The
same engine; a different producer/adapter bundle and a conversational front-end.
Modeled on [WireClaw](https://github.com/M64GitHub/WireClaw)'s insight: **the LLM
is a compiler (one-time, English → rules), the rule-loop is the runtime (24/7, no
LLM in the hot path).** mini-dudeai already embodies that split; this adds the
sensors, actuators, and the conversational compiler that make it personal.

## Decisions locked this session (operator)

- **Hardware: Pi-brain + ESP32-edge.** A Pi/uConsole runs mini-dudeai as the
  brain; ESP32 boards run **real WireClaw firmware** as sensor/actuator nodes.
- **Role: general personal agent**, adapter set grows; start with whichever demo
  is most satisfying.
- **Federate, don't reimplement.** WireClaw already speaks **NATS** and ships
  **OpenClaw** (direct hardware access over NATS, no LLM). mini-dudeai is the
  "other system" OpenClaw is built to let in. We do not rebuild firmware, sensor
  drivers, or a flasher — the ESP32 side is solved by WireClaw itself.

```
  uConsole / Pi  ───────────── NATS bus ───────────── ESP32 nodes (real WireClaw fw)
  mini-dudeai (BRAIN)                                  sensors  → NATS subjects
   • rule runtime 24/7 (no LLM in loop)                OpenClaw ← GPIO/LED/relay
   • dreams/synthesis (proposes rules back)
   • chat-compiler (Ollama, offline) ─┐
   • in-app form editor ──────────────┴─→ write_candidate() → runtime ratifies
```

## Reuse map — most of this already exists

| Need | Status |
|------|--------|
| Pure rule engine, edge-triggered, candidate-promotion | ✅ `engine.py` |
| Sources (`file_mtime`, `json_file`, `http_json`, `boot_health`) | ✅ `sources/` |
| Actions (`ntfy`, `annotate`, `propose_escalation`, `none`) | ✅ `actions/` |
| Adapter extension API | ✅ `register_source(kind, builder)` / `register_action(kind, builder)` |
| Preset/config launch | ✅ `python3 -m mini_dudeai --preset <name> | --config <json>` |
| Candidate authoring (propose→ratify) | ✅ `write_candidate()` / `validate_rules_document()` (`0c09f6e`) |
| In-app rule form (manual compiler) | ✅ MiniDudeaiHandler "edit rules" (`0c09f6e`) |
| Nightly synthesis that proposes rules back | ✅ dreams loop (`dreams.py`) |
| In-app config/fix surface (no shell) | ✅ remediation surface (`remediation.py`) |
| **NATS sensor source** | ❌ NEW (Phase A) |
| **NATS/OpenClaw actuator action** | ❌ NEW (Phase A) |
| **Chat-compiler (English → candidate)** | ❌ NEW (Phase B) |
| **Standalone preset** | ❌ NEW (Phase A) |

The whole standalone delta is **three pieces** (NATS in, NATS out, chat-compile)
plus a preset. Everything else is reuse.

## New adapter contracts (Phase A)

Core stays **stdlib-only**; the NATS adapter is an opt-in module that
`safe_import`s the NATS client and degrades loudly if absent (the SDK still runs
without it). Register at import time, same as built-ins.

**`nats_sensor` Source** — subscribe to NATS subjects, emit Conditions:
```
{"kind": "nats_sensor", "server": "nats://localhost:4222",
 "subjects": ["sensors.shop.temp"],
 "condition_kind": "sensor_reading",   # cond.kind for rules to match
 "subject_field": "node",              # → cond.subject
 "detail_field": "value"}              # → cond.detail; full payload in cond.extras
```
`collect()` drains buffered messages each tick → one Condition per reading. A
WireClaw NATS "virtual sensor" reading becomes a mini-dudeai Condition.

**`nats_action` (OpenClaw) Action** — publish to a NATS subject to drive a node:
```
{"kind": "nats_action", "server": "nats://localhost:4222",
 "subject": "openclaw.shop.gpio",
 "payload": {"pin": 5, "value": 1}}    # templated from the matched condition
```
`apply()`/`fire()` publishes → WireClaw's OpenClaw flips the GPIO/relay/LED,
**no LLM, no MeshForge code on the ESP32.** Returns `(ok, message)` — the same
contract the remediation surface uses, so an actuator can also be a "fix."

## The chat-compiler (Phase B) — the WireClaw magic

English → a rule candidate, ratified, promoted. The conversational sibling of
the Arc 4 form; both end at `write_candidate`.

Flow:
1. Operator types intent: *"when the shop node's temp is over 28°C for a minute,
   turn on the fan relay."*
2. A **local LLM (Ollama, offline)** is prompted with: the rule JSON schema, the
   live `registered_source_kinds()` / `registered_action_kinds()`, and the known
   node subjects → emits a candidate rule dict.
3. **Ratify in-app:** show the compiled rule (human-readable) → operator
   confirms. (LLM proposes, operator ratifies — same trust model as the form.)
4. `write_candidate()` → the runtime validates + `os.replace`-promotes within a
   tick. If the LLM emits something invalid, `validate_rules_document` rejects it
   at authoring time, surfaced in-app — never a silent bad rule.

Front-ends: a TUI handler ("mini-dudeai: describe a rule") and a standalone CLI
(`python3 -m mini_dudeai chat`). Both reuse the shipped substrate.

## Cross-node rules — richer than WireClaw

WireClaw rules are single-device. With the Pi as broker, a rule's condition can
come from node A and its action drive node B:
> *when `sensors.porch.pir` trips AND it's after sunset → publish
> `openclaw.shop.relay {on}`*

Condition from `nats_sensor` (porch), action via `nats_action` (shop), brokered
by the brain. Neither a pile of ESP32s nor WireClaw-alone can do this. Plus the
**dreams loop** watches the spoken-in rules fire over weeks and *proposes
refinements back* ("the porch rule flapped 6× at dusk — add `grace_s: 90`?"),
which the operator ratifies. WireClaw's memory is passive recall; ours proposes.

## Standalone preset

`presets/standalone.py` wires `nats_sensor`(s) + `nats_action` + `ntfy`/`annotate`
+ a personal rules seed, built into a `RuleEngine`. Launched
`python3 -m mini_dudeai --preset standalone`. Ships with the **noise gates baked
in** (`grace_s` debounce, dedup, the brief's 24h window) so the standalone
product starts where the fleet daemon took weeks to get — the In-Domain noise
lessons become its defaults.

## In-Domain alignment

The standalone variant honors the same principle: configure by form or chat (no
nano), fix by the remediation surface (no shell). MF018 governs its TUI surface
too. A spoken rule that fails to apply is reported in-app, never punted to a log.

## Phased build (one arc at a time, close each)

- **A — Edge adapters + preset.** `nats_sensor`, `nats_action`, `presets/standalone.py`;
  federate with one real WireClaw node; author rules via the existing Arc 4 form.
  *Proves: sensor in → rule → actuator out, offline.*
- **B — Chat-compiler.** Ollama → candidate via `write_candidate`; in-app + CLI
  front-ends; ratify-before-write. *Proves: English → running rule.*
- **C — Cross-node + the demo.** *"shop temp > 28°C for 60s → fan relay,"* spoken,
  ratified, surviving reboot. Dreams proposes a refinement; operator ratifies.
- **D — Onboarding.** Standalone in the launcher install path (parallels the
  fleet variant per `in_domain_principle.md` arc 5); document pairing real
  WireClaw nodes (their web flasher) to the brain's NATS bus.

## Open decisions (resolve at Phase A kickoff)

- **NATS server:** external `nats-server` on the Pi vs embedded. (Lean external —
  it's what WireClaw nodes already expect.)
- **Local LLM:** Ollama model + whether the compiler is allowed any network.
  (Lean fully-offline; that's the WireClaw promise.)
- **NATS auth/security:** token vs nkey; the bus carries actuator commands.
- **Node provisioning:** real WireClaw web flasher (wireclaw.io/flash.html) for
  the ESP32s; document the subject naming convention the brain expects.

## Dependencies

- New runtime dep (optional): a NATS client (`nats-py`) — `safe_import`ed, core
  stays stdlib-only. Add to an extras group, not the base `requirements.txt`.
- `ollama` (or an HTTP call to a local Ollama server) for the chat-compiler —
  also optional/extra.

---

*The substrate is in place. Phase A is a clean first arc whenever the next
build session opens.*
