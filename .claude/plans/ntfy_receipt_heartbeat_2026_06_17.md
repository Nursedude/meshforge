# Scoping: ntfy receipt-heartbeat — closing the "send ≠ receipt" gap (2026-06-17)

> **Born from a live incident.** 2026-06-14 → 06-17 the operator's phone received
> **zero** fleet pages, yet every `fleet_ntfy_push.sh` / mini `NtfyAction` publish
> returned HTTP 200. Root cause: the phone was subscribed to a stale `mf-drill-*`
> throwaway topic, not the fleet topic `mf-fleet-*`. **The fleet could not tell —
> ntfy.sh *accepting* the message was mistaken for the human *receiving* it.**
> Four days dark, undetected. This is the honest-failure-modes class in its purest
> form: "absence of evidence is not evidence of absence," aimed at the alerting
> spine itself. Fixed the immediate cause (re-subscribed + propagated the topic
> to all boxes); this doc scopes the *durable* fix so it can't recur silently.

## The defect class

`publish → HTTP 200` proves ntfy.sh **accepted** the message. It proves nothing
about:
1. the message reaching the operator's **device** (subscription correct, app
   alive, push/APNs working, notifications permitted); or
2. the device watching the **same topic** the fleet publishes to.

Every sender in the fleet today stops at `200 = success`. The receipt half is
unmonitored. **An alerting system whose own liveness is unverified is a house of
cards** — the one signal you must never lose is the one that says you've lost
signal.

## Failure classes to cover

| Class | Example (this incident = **T**) | Fleet-observable today? |
|---|---|---|
| A. Server/topic down | ntfy.sh outage; topic typo in config | No |
| B. Topic mismatch | phone on `mf-drill-*`, fleet on `mf-fleet-*` (**T**) | No |
| C. Device-side | app killed, notifications off, APNs lapsed | No |
| D. Sender no-op | box missing `fleet_push_topic` → silent `exit 0` | No — *just closed* by topic propagation (06-17) |

## Options

**Option 1 — Loopback receipt monitor (catches A, D, fleet-side B).**
A fleet box *subscribes* to the fleet topic (ntfy stream/poll API); the fleet
publishes a periodic heartbeat; the monitor confirms it **receives** each
heartbeat within a window. Miss → the topic's delivery path is broken → escalate
via a second channel. Fully automatable + observable; wire as a watchdog probe +
cron-verdict. Does **not** prove the *phone* receives (different subscriber) —
but proves the server/topic deliver.

**Option 2 — Operator-ack heartbeat (catches C and the device-side of B — i.e. T).**
A low-rate (e.g. weekly) heartbeat page carrying a tap-to-ack ntfy **action
button** that hits an ack endpoint on a fleet box. Ack within N days → phone
confirmed receiving. No ack → escalate + surface on `/fleet`. **The only
mechanism that confirms the human's device** — because the human's tap is the
proof. Cost: an HTTP ack receiver + state + a second escalation path.

**Option 3 — Redundant second channel (mitigates all, confirms none).**
Route RED/crash pages to ntfy **and** an independent path (a 2nd ntfy
topic/server, email via the connected Gmail MCP, or a Pi-local SMS gateway).
Redundancy, not confirmation — if one is dark the other still reaches you. It is
also the escalation backbone Options 1 & 2 require.

## Recommended design (phased — they compose; none alone suffices)

- **Phase 1 — the second channel (Opt 3), the backbone.** Pick one independent
  path; simplest is **email via the Gmail MCP** (already connected) or a 2nd
  ntfy topic on a different server. Everything else escalates through it.
  **Exercise it on a schedule too — a channel you never test is already dark.**
- **Phase 2 — server/topic liveness: loopback monitor (Opt 1).** A
  `probe_ntfy_loopback` watchdog probe: publish a heartbeat to the fleet topic,
  confirm a fleet-box subscriber receives it within the window; `degraded` →
  escalate via Phase 1. Catches A + D + fleet-side B. Reuses the watchdog→mini→
  `/fleet` spine.
- **Phase 3 — the human's device: operator-ack heartbeat (Opt 2).** Weekly
  tap-to-ack page; unacked past threshold → escalate via Phase 1 + a `/fleet`
  card. Catches C + the exact incident T (device on a wrong/old topic, dead app).

## Open questions (decide before building)

- **Second channel:** Gmail MCP (lowest friction, already wired) vs a 2nd ntfy
  server vs SMS (most reliable, needs a gateway)? Lean Gmail for Phase 1.
- **Ack-endpoint host:** VolcanoAI (manager) — but it must itself be monitored
  (who watches the watcher → the Phase-1 channel + a cross-box check).
- **Cadence vs fatigue:** daily loopback + weekly ack feels right; tune.
- **Reuse, don't reinvent:** Phase 2 belongs in the watchdog-probe spine; Phase 3
  escalation belongs in mini's action layer. No new parallel framework.

## Honest-failure-modes checklist (applied at design time)

- Every heartbeat miss leaves a **probe-visible witness**, never a swallowed gap.
- The monitors' **own** liveness is covered by the second channel + cron-verdict
  (#78) — the watcher is watched.
- "No ack yet" is held as **UNKNOWN**, never read as "fine" (unobservable ≠
  healthy — the exact lie that hid this for 4 days).

## Status

**SCOPED, not built** (2026-06-17). Companion to the
`dudeclaw_second_brain_2026_06_17.md` arc (this surfaced while wiring the scout
discharge cron). Implementation is a future arc; Phase 1 is the cheapest first
rung and the prerequisite for the other two.
