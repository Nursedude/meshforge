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
topic/server, email via a dedicated Gmail + `curl`→SMTP, or a Pi-local SMS
gateway). Redundancy, not confirmation — if one is dark the other still reaches
you. It is also the escalation backbone Options 1 & 2 require.

## Recommended design (phased — they compose; none alone suffices)

- **Phase 1 — the second channel (Opt 3), the backbone. DECIDED 2026-06-17 +
  BUILT 2026-06-18: a dedicated throwaway Gmail + `curl`→SMTP**, via
  `scripts/fleet_alert_email.sh` (sibling of `fleet_ntfy_push.sh`, same 4-arg
  signature; creds in `~/.config/fleet_email_creds`, no-op-safe without them).
  The **Gmail MCP was rejected**: it has no send tool and is session-scoped, so
  cron/watchdog can't reach it. `curl` is already present on the boxes — no
  install. Everything else escalates through this.
  **Exercise it on a schedule too — a channel you never test is already dark.**
- **Phase 2 — server/topic liveness: loopback monitor (Opt 1). BUILT
  2026-06-18.** Two parts, mirroring the Leg-C/D collector→state-file→read-only-
  probe pattern (a probe must never send network traffic, so the side-effecting
  half is a cron): **(a)** `scripts/fleet_ntfy_loopback.sh` — a manager-box cron
  publishes a nonce'd, **min-priority** (silent-on-phone) heartbeat to the FLEET
  topic, polls ntfy.sh's poll API to confirm it loops back within ~20s, writes
  `~/ntfy_loopback_state.json`, and on a sustained miss (≥2 consecutive)
  escalates via the Phase-1 **email** backbone (`fleet_alert_email.sh`) — NOT
  back through ntfy, the suspect channel. **(b)** `probe_ntfy_loopback`
  (read-only) reads that verdict file → `degraded` (or `wedge` at ≥3 misses) into
  mini's brief + `/fleet`; INERT off the manager box, stale verdict → defers to
  `cron_verdict_stale`. Catches A (ntfy.sh down) + D (sender no-op) + fleet-side
  B (the fleet topic's publish path broken). Does NOT catch the operator's PHONE
  on a wrong topic — that's Phase 3. The cron is wired to `cron_verdict.sh` so
  `cron_verdict_stale` (#78) watches the monitor itself (who watches the
  watcher). Signal class `ntfy_loopback`; seed `ntfy_loopback_any`
  (propose_escalation) in both role seeds; 12 probe tests + the closed-enum /
  seed-coverage / wiring gates.
- **Phase 3 — the human's device: operator-ack heartbeat (Opt 2).** Weekly
  tap-to-ack page; unacked past threshold → escalate via Phase 1 + a `/fleet`
  card. Catches C + the exact incident T (device on a wrong/old topic, dead app).

## Open questions (decide before building)

- **Second channel:** ~~Gmail MCP vs 2nd ntfy server vs SMS~~ — **RESOLVED
  2026-06-17:** dedicated throwaway Gmail + `curl`→SMTP (the MCP has no send
  tool + is session-scoped; `curl` needs no install). Built 2026-06-18.
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

## Activation (Phase 2 — manager box only)

`probe_ntfy_loopback` is INERT until the collector cron writes its verdict file.
On the **manager box** (VolcanoAI — where the fleet topic SSOT lives):

1. **Wire the collector cron** (every 30 min; the `cron_verdict.sh` tail makes
   `cron_verdict_stale` watch the monitor itself):
   ```
   */30 * * * * /opt/meshforge/scripts/fleet_ntfy_loopback.sh; /opt/meshforge/scripts/cron_verdict.sh ntfy_loopback $?
   ```
2. **Restart the watchdog** so it loads the new probe (SYSTEM unit, soak-safe):
   `sudo systemctl restart meshforge-watchdog`
3. **Promote the seed** so mini routes `ntfy_loopback`:
   `python3 scripts/promote_seed_rules.py --apply` (clears the expected
   `rules_seed_drift`). Run on the other 5 boxes too (the probe is INERT there,
   but promotion keeps them in sync / quiets `rules_seed_drift`).

The heartbeat is **min-priority** → silent on the phone; set your ntfy
subscription's minimum priority to ignore it if the feed clutters.

## Status

**Phase 1 BUILT + VERIFIED 2026-06-17/18** (`scripts/fleet_alert_email.sh`,
end-to-end: send `exit 0` + operator receipt). **Phase 2 BUILT + VERIFIED
2026-06-18** (`scripts/fleet_ntfy_loopback.sh` + `probe_ntfy_loopback`; collector
live-tested against a throwaway topic, `received:true latency 4s`; lint + full
affected suites green). **Phase 3 (weekly tap-to-ack — the human's device) is
the remaining rung** — it's the only one that catches the exact 2026-06-14→17
incident (phone on a dead topic). Needs an HTTP ack receiver on the manager box
+ a second escalation path (the Phase-1 email backbone is ready for it).
