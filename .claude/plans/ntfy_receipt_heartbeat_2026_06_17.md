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
- **Phase 3 — the human's device: operator-ack heartbeat (Opt 2). BUILT
  2026-06-18.** `scripts/fleet_ntfy_ack.sh` (hourly manager-box cron) sends a
  **weekly** tap-to-ack page to the fleet topic carrying an ntfy `http` **action
  button** ("Confirm receipt"); tapping it makes the **phone POST an ack** — no
  public ingress needed, since VolcanoAI has none (MF015).
  **⚠️ Re-aimed 2026-09-03:** the ack used to go to a dedicated side topic
  (`<fleet>-ack`) only the poller read, so a tap had NO visible effect on the
  device and, one minute later, the fleet paged "ack UNCONFIRMED" (the cron
  judges LAST week's page in the same run that sends this week's). The operator
  tapped four times in four seconds and reported the button broken while the
  server held all four acks — a record nobody can see is not a receipt (MF018).
  Now the tap publishes a low-priority **"Receipt confirmed"** message onto the
  **fleet topic itself**, so the phone sees its own ack land within a second;
  the poller matches that title exactly + a body starting `ack` (a loose
  substring on the fleet topic would let `backoff` in a real page forge a
  receipt), still polls the legacy `-ack` topic for pages delivered before the
  change, and records `unacked_ping_ts` so the probe names WHICH page went
  un-acked. Drill-verified on a throwaway topic (simulated tap counted, decoy
  ignored); the phone-side render is again only provable by a live tap.
  The cron polls for the ack, tracks `consecutive_unacked_pings`, escalates via
  the Phase-1 **email** backbone at ≥2 unacked weeks, and writes
  `~/ntfy_ack_state.json`. `probe_ntfy_ack_stale` (read-only) surfaces unacked
  weeks into mini's brief + `/fleet` (degraded; wedge at ≥2); INERT until first
  pinged; stale state → `cron_verdict_stale`. Catches C + the exact incident T
  (device on a wrong/old topic, dead app, notifications off) — what loopback (a
  different subscriber) structurally cannot. Signal class `ntfy_ack_stale`; seed
  `ntfy_ack_stale_any` in both role seeds; 12 probe tests. **⚠️ The defining proof
  — the phone rendering the button + the tap producing an ack — can only be
  confirmed by a live tap-test on the operator's device** (the ack *rendezvous*
  poll mechanics are verified by simulating the POST).

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

> ✅ **DONE on VolcanoAI 2026-06-18** — all three steps below are wired/live
> (loopback cron `*/30`, watchdog restarted, seeds promoted on all 6). Retained
> as the reproducible record / for any future manager-box rebuild.

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

## Activation (Phase 3 — manager box only; needs a live tap-test)

> ✅ **DONE on VolcanoAI 2026-06-18** — live tap-test passed (operator tapped
> "Got it", ack POSTed to `<fleet>-ack`, poller ingested it), hourly cron `:17`
> wired (cron_verdict-tailed), and the **escalation email path was drill-verified
> end-to-end** (primed unacked=2 on throwaway state/topic → `fleet_alert_email.sh`
> exit 0 → operator confirmed the drill email landed). Retained as the
> reproducible record.

Unlike Phase 2, the weekly ack page **notifies** (default priority — it's the one
you tap), so this is opt-in to a recurring weekly interaction.

1. **Live tap-test FIRST** (proves the button works on your device). Send one ping
   now and tap it:
   `MESHFORGE_ACK_PING_INTERVAL_S=1 /opt/meshforge/scripts/fleet_ntfy_ack.sh`
   → a "Fleet alert check" page hits your phone → tap **Got it** → run the script
   again (normal interval) and confirm `~/ntfy_ack_state.json` shows
   `last_ack_ts` advanced + `consecutive_unacked_pings: 0`.
2. **Wire the hourly cron** (polls acks + sends the weekly ping; cron_verdict tail
   makes `cron_verdict_stale` watch it):
   ```
   17 * * * * /opt/meshforge/scripts/fleet_ntfy_ack.sh; /opt/meshforge/scripts/cron_verdict.sh ntfy_ack $?
   ```
3. The watchdog restart + seed promote from the Phase-2 steps already cover
   `ntfy_ack_stale` (same `promote_seed_rules.py --apply`; the probe + seed shipped
   together). If you skipped those, run them.

Cadence/escalation are env-tunable: `MESHFORGE_ACK_PING_INTERVAL_S` (default 7d),
and the email fires at ≥2 unacked weeks.

## Status

**Phase 1 BUILT + VERIFIED 2026-06-17/18** (`scripts/fleet_alert_email.sh`,
end-to-end: send `exit 0` + operator receipt). **Phase 2 BUILT + VERIFIED
2026-06-18** (`scripts/fleet_ntfy_loopback.sh` + `probe_ntfy_loopback`; collector
live-tested against a throwaway topic, `received:true latency 4s`; lint + full
affected suites green). **Phase 3 BUILT 2026-06-18** (`scripts/fleet_ntfy_ack.sh` +
`probe_ntfy_ack_stale`; ack-rendezvous poll mechanics verified by simulating the
phone's POST — `last_ack_ts` updated, unacked=0; 12 probe tests + the
closed-enum / seed-coverage / wiring gates; lint + 422 affected-suite green).
**✅ ARC COMPLETE + LIVE on VolcanoAI 2026-06-18.** All three rungs are activated:
P1 email backbone (send + receipt verified), P2 loopback (`*/30` cron, real
round-trip), P3 device-confirm (`:17` cron; device round-trip verified by the
operator's live tap, AND the escalation email path drill-verified end-to-end to
the operator's inbox). Both ntfy crons are `cron_verdict.sh`-tailed so
`cron_verdict_stale` watches the monitors themselves; signal classes
`ntfy_loopback` + `ntfy_ack_stale` route in both role seeds (promoted on all 6
boxes). `honest_status.sh` is `exit 0` at HEAD. The ntfy ack-topic rendezvous (no
public ingress) replaced the originally-scoped "HTTP ack receiver." **No pending
steps** — the next weekly ack page is ~7 days out; normal operation from here.
