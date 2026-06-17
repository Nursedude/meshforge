# Reliable "Node Went Dark" Alerting for a Small Self‑Hosted Fleet

*A cited design report on the dead‑man's‑switch pattern, witnessed delivery, re‑alert cadence, and watcher‑of‑the‑watcher — with a concrete redesign that fixes the four failures you observed.*

---

## 0. The core diagnosis (why your current approach failed)

Your design is a **push‑from‑a‑dying‑node side‑channel with fire‑once semantics and unwitnessed delivery.** Every one of those words is a known antipattern. The single sentence that captures the whole class:

> "The worst kind of outage in an observability system is the silent one — when your system stops receiving data or a pipeline stalls, and nobody notices because **the absence of data does not trigger any alert**." [How to Set Up Heartbeat and Dead Man's Switch Alerts — https://oneuptime.com/blog/post/2026-02-06-heartbeat-dead-man-switch-opentelemetry-pipeline/view]

The fix is to **invert the logic**: stop waiting for a failing thing to announce its own failure, and instead make *silence itself* the alarm — then make the alarm *repeat*, *prove it was received*, and *be itself watched*.

---

## A. Synthesis of the three patterns

### A.1 The dead‑man's‑switch / inverse‑alerting pattern

**Two opposite philosophies of monitoring:**

| Model | Assumption | Detects a vanished node? |
|---|---|---|
| **Presence / push‑on‑failure** (your current cron→ntfy) | "Everything is fine until something reports a problem" | **No** — a frozen/dark/unplugged node can't report |
| **Absence / heartbeat expiry** (dead‑man's‑switch) | "Guilty until proven innocent — assume failed unless it actively reports success" | **Yes** — that's the entire point |

> "Traditional monitoring works on presence — it assumes everything is fine until proven otherwise — but to catch a vanishing job, you need a tool that monitors for **absence** … heartbeat monitoring … operates on a principle of **guilty until proven innocent**." [Dead Man's Snitch: Detecting Silent Cron Job Failures — https://medium.com/@kinjaldand/your-cron-job-didnt-crash-it-vanished-here-s-how-to-catch-it-08b4d46d912c]

**Why absence‑based is strictly more robust for "node went dark":** the failure modes you care about — power loss, kernel freeze, network partition, OOM, SD‑card death — are exactly the modes in which the node **cannot transmit anything**, including its own alert. A push‑from‑the‑dying‑node alert is only as reliable as the dying node, which is the thing under suspicion. As the push‑vs‑pull literature puts it, an absent push is *ambiguous* on its own ("application breakdown, a network problem, or the application may be migrated"), so the **monitor**, not the node, must own the verdict by timing the silence. [Pull or Push: How to Select Monitoring Systems? — https://www.alibabacloud.com/blog/pull-or-push-how-to-select-monitoring-systems_599007] [NodePing | PUSH Check Type — https://nodeping.com/push_check.html]

**The canonical mechanism (heartbeat + period + grace):**
- The monitored node **checks in** on a known cadence ("I'm alive" ping).
- The monitor expects it within a **period**; if it doesn't arrive, the check goes **Late**.
- After an additional **grace time** (to absorb normal jitter), the check transitions to **Down** and *fires*. [Healthchecks.io Documentation — https://healthchecks.io/docs/] [Configuring Checks — https://healthchecks.io/docs/configuring_checks/]

> "Grace Time … is the additional time to wait before sending an alert when a check is late. … The 'Down' state means the 'success' signal has not arrived yet, and the Grace Time has elapsed. When a check transitions into the 'Down' state, Healthchecks.io sends alert messages." [Configuring Checks — https://healthchecks.io/docs/configuring_checks/]

A common, robust timeout choice is **~3× the heartbeat interval** before declaring dead, which tolerates one or two lost beats without false alarms. [Heartbeat: How Distributed Systems Know You're Still Alive — https://singhajit.com/distributed-systems/heartbeat/] [NodePing | PUSH Check Type — https://nodeping.com/push_check.html]

**When to use each (they're complementary, not exclusive):**
- **Dead‑man's‑switch (absence)** → liveness: "is the box up and reporting at all?" This is your primary need.
- **Push‑on‑event (presence)** → still useful for *conditions a live node can self‑diagnose* (disk 95% full, service crashed‑but‑host‑alive). Keep these, but **never** rely on them for liveness.

Note the directional subtlety: in the *fleet* topology, the **node pushes a heartbeat** and the **central monitor pulls/expires** it. The robustness comes not from push‑vs‑pull of the heartbeat itself, but from **who renders the verdict**: an independent timer that fires on silence, not the suspect node. (You can also have the central box actively *poll* nodes — your current SSH probe — but then the central box's verdict must itself be heartbeated outward; see §B.5.)

### A.2 Witnessed / acknowledged delivery

Two distinct things mature systems confirm, and they are easy to conflate:

**(1) Did the alert leave the building? (publisher‑side delivery receipt.)**
Your `curl … >/dev/null` discards the one cheap signal you already have: the HTTP status and response body. ntfy returns a JSON object containing a message `id` on a successful publish, and subscribers see the same `id` — so a 2xx + an `id` is proof the *server accepted and cached* the message. [Using the API — https://docs.ntfy.sh/subscribe/api/] [Sending messages — https://docs.ntfy.sh/publish/] **This is the single highest‑leverage fix to your "unwitnessed delivery" failure** and costs one `if` statement.

**(2) Did a human actually receive/acknowledge it? (the hard part.)**
This is where lightweight tools hit a wall. **ntfy has no read receipts or delivery acknowledgement** — confirmed across the docs (priority, click actions, and action buttons are documented; acknowledgement is *not*). [Sending messages — https://docs.ntfy.sh/publish/] [ntfy publish.md — https://github.com/binwiederhier/ntfy/blob/main/docs/publish.md] So you cannot get true "human ACK'd it" from ntfy alone.

**What the mature pattern looks like (PagerDuty‑style escalation):** an incident notifies one target at a time; **acknowledgement by a human stops the escalation**; if no ack within an **escalation timeout** (default 30 min), it escalates to the next tier. [Escalation Policy Basics — https://support.pagerduty.com/main/docs/escalation-policies] [Escalation Policies and Schedules — https://support.pagerduty.com/main/docs/escalation-policies-and-schedules]

> "The user who acknowledges an incident claims ownership … and halts the escalation process. Incidents will not escalate if they are acknowledged or resolved before the timeout is reached." [Escalation Policy Basics — https://support.pagerduty.com/main/docs/escalation-policies]

**What's achievable with lightweight self‑hosted tools:** you can't get true human‑ack cheaply, but you can *approximate* "escalate if unacknowledged" with **delivery tiers that change channel/urgency over time** (see §A.3 and §B), because a louder, different channel after N minutes substitutes for "nobody ack'd." ntfy.sh's hosted service adds two escalation channels that *do* approximate proof‑of‑reach: **email forwarding** (`Email:` header) and **phone calls with text‑to‑speech** (`X-Call`), the latter being a hosted paid feature — a ringing phone is far more "witnessed" than a silent push. [Sending messages — https://docs.ntfy.sh/publish/] [ntfy.sh phone‑call/email features — https://docs.ntfy.sh/] Use these as the top escalation tier.

### A.3 Re‑alert / repeat cadence without alert fatigue

Fire‑once is the wrong end of the spectrum; fire‑constantly is the other. The discipline lives in **Alertmanager's three timers** and three noise controls. Exact semantics:

- **`group_wait`** (default **30s**): how long to buffer a *new* group before the first notification, so related alerts batch.
- **`group_interval`** (default **5m**): minimum wait before sending an *updated* notification for an existing group (new/resolved members).
- **`repeat_interval`** (default **4h**): how long before **re‑sending an unchanged, still‑firing** alert — this is the anti‑fire‑once knob. [Configuration | Prometheus — https://prometheus.io/docs/alerting/latest/configuration/] [What's the difference between group_interval, group_wait, and repeat_interval? — https://www.robustperception.io/whats-the-difference-between-group_interval-group_wait-and-repeat_interval/]

Key constraint: `repeat_interval` is only evaluated at `group_interval` boundaries, so **it should be a multiple of `group_interval`** (Alertmanager rounds up otherwise). [What's the difference… — https://www.robustperception.io/whats-the-difference-between-group_interval-group_wait-and-repeat_interval/]

**The three fatigue controls:**
- **Grouping** — collapse many related alerts into one notification (10 dark nodes after a switch dies → one page, not ten).
- **Inhibition** — mute downstream alerts when an upstream cause is firing ("whole site unreachable" inhibits the per‑node "node down" alerts). [Alertmanager | Prometheus — https://prometheus.io/docs/alerting/latest/alertmanager/]
- **Silences** — operator‑set, matcher‑based, **time‑bounded** muting for maintenance, which **auto‑expires and preserves history** (unlike disabling). [Configuration | Prometheus — https://prometheus.io/docs/alerting/latest/configuration/]

**Best‑practice cadence:** shorter `repeat_interval` for critical, longer for low‑severity, so an ongoing critical outage keeps nudging without burying the operator. [Prometheus Alertmanager best practices — https://dev.to/sysdig/prometheus-alertmanager-best-practices-4872] healthchecks.io offers a simpler version of the same idea: **hourly or daily reminders while any check is still down.** [Configuring Notifications — https://healthchecks.io/docs/configuring_notifications/] **This directly fixes your fire‑once failure**: a 33‑hour outage should produce a *recurring* nudge (e.g., every 1–2h), not one lost push.

---

## B. Recommended design for a small self‑hosted fleet

This design is deliberately tiered: **Option 1 (Alertmanager/healthchecks)** is the "do it properly" path; **Option 2 (hardened DIY)** keeps your ntfy investment but fixes all four failures. Pick based on appetite (see §C).

### B.1 Heartbeat topology (who pushes, who pulls)

```
  each fleet node ──(push heartbeat every 1 min)──► HEARTBEAT MONITOR (central, self-hosted)
                                                       │  expects each node within period+grace
                                                       │  fires on SILENCE (dead-man's-switch)
                                                       ▼
                                              NOTIFIER (ntfy self-hosted + email/call tier)
                                                       │  witnessed (checks HTTP 200 + id)
                                                       │  re-alerts on a cadence while down
                                                       ▼
                                                  OPERATOR PHONE
                  ▲
  EXTERNAL DMS ───┘  the monitor itself heartbeats OUT to a 3rd-party/off-box watcher
  (who watches the watcher)
```

- **Nodes push, monitor expires.** Each node sends a lightweight check‑in (a `curl` to a unique heartbeat URL, or a Prometheus exporter that gets scraped). The **central monitor renders the dead/alive verdict by timing silence** — never the node. [Healthchecks.io Documentation — https://healthchecks.io/docs/]
- **Period + grace per node.** Heartbeat every 60s; declare **Down** after **~3 missed beats** (period 60s + grace ~120s ≈ 3 min), tuning grace up for nodes on flaky links. [NodePing PUSH Check — https://nodeping.com/push_check.html] [singhajit heartbeat — https://singhajit.com/distributed-systems/heartbeat/]
- **Keep your SSH active‑probe too, if you like** — it's a useful black‑box check — but treat it as a *second* signal whose own liveness is heartbeated outward (B.5). Do not let it be the only verdict‑maker.

### B.2 Re‑alert cadence (fixes failure #1: fire‑once)

- On **Down**: page immediately (`group_wait`‑style small batch ~30s to coalesce a multi‑node event).
- **While still Down**: re‑notify on a recurring cadence — **`repeat_interval` ≈ 1–2h** for a critical "node dark" so a 33h outage produces ~16–33 reminders, not one. [Configuration | Prometheus — https://prometheus.io/docs/alerting/latest/configuration/] [Configuring Notifications — https://healthchecks.io/docs/configuring_notifications/]
- **Escalation tiers** approximate "escalate if unacknowledged" without a true ack channel:
  - **T0 (0 min):** ntfy push, priority `high` (4).
  - **T1 (~15–30 min still down):** ntfy push priority `max`/`urgent` (5) **+ email** forward. [Sending messages — https://docs.ntfy.sh/publish/]
  - **T2 (~60 min still down):** **phone call** (ntfy.sh `X-Call` TTS, or a `call`/SMS integration). A ringing phone is your closest cheap proxy for "witnessed." [ntfy.sh phone call/email — https://docs.ntfy.sh/]
- Use **grouping + inhibition** so a site‑wide outage is *one* escalating incident, not N. [Alertmanager — https://prometheus.io/docs/alerting/latest/alertmanager/]

### B.3 Witnessed delivery (fixes failure #2: unwitnessed)

- **Stop discarding the curl result.** Capture HTTP status + the returned `id`; treat a non‑2xx or missing `id` as a *failed page* and **retry with backoff**, then fall through to the next channel. The publish endpoint returns the accepted message object (with `id`) — that's your delivery witness for "server accepted + cached." [Sending messages — https://docs.ntfy.sh/publish/] [Using the API — https://docs.ntfy.sh/subscribe/api/]
- **Close the loop end‑to‑end** by having the monitor (or a separate verifier) **poll the topic back** (`poll=1&since=<id>`) to confirm the message is actually retrievable from the server, not just that the POST returned 200. [Using the API — https://docs.ntfy.sh/subscribe/api/]
- **Accept the honest limit:** ntfy gives you *server‑accepted* and *server‑retrievable*, **not human‑acknowledged** (no read receipts). [ntfy publish.md — https://github.com/binwiederhier/ntfy/blob/main/docs/publish.md] The phone‑call tier (B.2 T2) is what gets you closest to human‑witnessed.

### B.4 Retention / TTL (fixes failure #3: aged‑off message)

- **Self‑host ntfy** and **persist the cache** so a message that fired at hour 0 of a 33h outage is still on the topic at hour 33. By default ntfy keeps messages **in‑memory for 12h** and **cached messages do not survive a restart**; override with `cache-file` (SQLite) or `database-url` (Postgres) and raise `cache-duration` well past your worst expected outage. [Configuration — https://ntfy.sh/docs/config/] [Self‑Hosted Ntfy Server — https://unifiedpush.org/users/troubleshooting/self-hosted-ntfy/]
- **Belt‑and‑suspenders:** because the *recurring re‑alert* (B.2) keeps re‑posting fresh messages every 1–2h, retention stops being load‑bearing — even with default 12h retention there's always a recent message. The re‑alert cadence and longer retention each independently defeat the age‑off bug; do both.

### B.5 Who watches the watcher (fixes failure #4: silent side‑channel + monitor SPOF)

The monitor is a single point of failure, and **"Prometheus and Grafana cannot monitor their own service's availability."** [Monitor and Alert with Prometheus and Grafana — https://docs.starrocks.io/docs/administration/management/monitoring/Monitor_and_Alert/] The fix is the **external dead‑man's‑switch / Watchdog**:

1. The monitor emits an **always‑firing heartbeat outward** to an *independent* watcher. In Alertmanager this is the **Watchdog alert** — an alert whose PromQL is literally `vector(1)`, *always* firing, routed to an external receiver on a short `repeat_interval`. [End‑to‑End Watchdog Alerts — https://training.promlabs.com/training/monitoring-and-debugging-prometheus/metrics-based-meta-monitoring/end-to-end-watchdog-alerts/] [Help with deadman switch · Discussion #3227 — https://github.com/prometheus/alertmanager/discussions/3227]

   ```yaml
   - alert: Watchdog
     expr: vector(1)            # always produces one series → always fires
     labels: { severity: none }
     annotations:
       description: Ensures the entire alerting pipeline is functional.
   ```
2. An **external service expects that heartbeat and alerts when it STOPS** — i.e., it's a dead‑man's‑switch *for the monitor*. This is exactly the "DeadMansSnitch" integration pattern: a 3rd party that pages **on absence** of the always‑firing alert. [End‑to‑End Watchdog Alerts — https://training.promlabs.com/…/end-to-end-watchdog-alerts/] [Help with deadman switch · #3227 — https://github.com/prometheus/alertmanager/discussions/3227]

   > "External monitoring services watch for the regular arrival of this alert. If the alert stops arriving within expected intervals, it signals a failure in the monitoring pipeline itself — that's the actual deadman switch mechanism." [Discussion #3227 — https://github.com/prometheus/alertmanager/discussions/3227]

3. **Make the external watcher genuinely independent** — different box, ideally different power/network/provider — otherwise the same outage that kills the monitor kills its watcher. Good cheap options: a **healthchecks.io** check the monitor pings every minute (self‑host it *elsewhere*, or use the hosted free tier here precisely because off‑box independence matters), **Dead Man's Snitch**, or a Cronitor/OpsGenie heartbeat. [Healthchecks.io — https://healthchecks.io/] [Dead Man's Snitch FAQ — https://deadmanssnitch.com/docs/faq] If you self‑host healthchecks for the *fleet*, use a *second, independent* hosted snitch for the *monitor itself*, so the watcher‑of‑the‑watcher isn't co‑located with what it watches.

4. **Surface it where the operator already looks** (fixes the side‑channel failure): the watcher's state must live on a habitually‑viewed surface — your `/fleet` panel, a pinned dashboard, a status page — not only an ntfy topic checked ad hoc. The lesson that "silence is the failure mode" means the *health of the monitor* has to be **visible at a glance when green**, so its absence is conspicuous. (This mirrors your own fleet's `cron_verdict_stale` / `channel_feed_dark` philosophy: a verdict that nothing actively reads is no verdict.)

### B.6 The four failures, explicitly closed

| # | Observed failure | Fix in this design |
|---|---|---|
| 1 | **Fire‑once** (33h → 1 push) | Recurring re‑alert (`repeat_interval` 1–2h) + escalation tiers T0→T2 (§B.2) |
| 2 | **Unwitnessed delivery** (curl discarded) | Check HTTP 2xx + returned `id`; retry/escalate on failure; poll topic back to confirm retrievable (§B.3) |
| 3 | **Age‑off** (~12h ntfy retention) | Self‑host ntfy with `cache-file`/`database-url` + longer `cache-duration`; recurring re‑alert keeps a fresh message present (§B.4) |
| 4 | **Silent side‑channel** + monitor SPOF | Always‑firing Watchdog → independent external dead‑man's‑switch; surface state on a habitually‑viewed panel (§B.5) |

---

## C. Tooling comparison

| Capability | **Prometheus Alertmanager** | **healthchecks.io** (self‑hostable) | **ntfy‑only** (self‑hosted) | **Hardened DIY** (your cron, fixed) |
|---|---|---|---|---|
| Dead‑man's‑switch (absence) | Via Watchdog + external snitch; native alerting is metric‑expiry | **Yes, native** — period + grace is the core model | No native expiry — pure notifier | Yes, if cron times silence and inverts logic |
| Heartbeat semantics | `up==0`/`absent()` + Watchdog | **Purpose‑built** (check‑in URLs) [healthchecks.io docs] | None | You implement period+grace |
| Re‑alert cadence | **Best‑in‑class** (`repeat_interval`, group/inhibit/silence) | Hourly/daily reminders while down [Configuring Notifications] | Manual (you re‑POST) | Manual loop |
| Witnessed delivery | Receiver send errors logged/retried; no human ack natively | Integration delivery; no human ack | **HTTP 2xx + `id`**, poll‑back; **no read receipt** [ntfy docs] | Same as ntfy, once you stop discarding output |
| Escalation / ack | Routing tiers; true ack via PagerDuty‑style receiver | Multiple integrations; escalate via downstream | priority + email + **phone‑call** tiers (ntfy.sh) [ntfy docs] | Whatever you script |
| Watcher‑of‑the‑watcher | **Canonical Watchdog pattern** [PromLabs] | Is itself a great *external* watcher for others | Needs an external snitch added | Needs an external snitch added |
| Self‑host / privacy | Yes (heavier: Prometheus stack) | **Yes, BSD‑licensed, docker‑compose** [healthchecks.io docs] | Yes, single binary [ntfy config] | Already self‑hosted |
| Operational weight | **Heaviest** | **Light–medium** | Light (notifier only) | Lightest |

**Recommendations:**

- **If you want the smallest correct system →** **self‑hosted healthchecks.io (for fleet liveness) + self‑hosted ntfy (as a notification channel) + one independent hosted snitch watching healthchecks.** healthchecks gives you native dead‑man's‑switch + grace + while‑down reminders out of the box [https://healthchecks.io/docs/]; ntfy delivers; the external snitch watches the watcher. This is the best effort‑to‑robustness ratio for a small fleet.
- **If you already run / want Prometheus →** **Alertmanager with the Watchdog + external DMS** is the textbook answer and gives you grouping/inhibition/silencing and tiered `repeat_interval` for free. [PromLabs Watchdog — https://training.promlabs.com/…/end-to-end-watchdog-alerts/] [Configuration — https://prometheus.io/docs/alerting/latest/configuration/]
- **If you must keep the DIY cron →** it's salvageable: invert to absence‑timing, add a re‑alert loop, check the curl result + poll‑back, self‑host ntfy with persistence, **and add an independent external snitch**. That fixes all four failures — but you're now re‑implementing healthchecks.io by hand, which argues for adopting it instead.

---

## Calibration / honesty notes

- **VERIFIED from primary docs:** ntfy default retention **12h in‑memory**, non‑persistent across restart, overridable via `cache-file`/`database-url` [https://ntfy.sh/docs/config/]; ntfy priority 1–5 and **no documented read‑receipt/ack** [https://github.com/binwiederhier/ntfy/blob/main/docs/publish.md]; healthchecks **period+grace→Down** model and **hourly/daily down‑reminders** [https://healthchecks.io/docs/configuring_checks/, https://healthchecks.io/docs/configuring_notifications/]; Alertmanager **`group_wait`=30s / `group_interval`=5m / `repeat_interval`=4h defaults** and the `vector(1)` Watchdog [https://prometheus.io/docs/alerting/latest/configuration/, https://www.robustperception.io/whats-the-difference-between-group_interval-group_wait-and-repeat_interval/, https://training.promlabs.com/…/end-to-end-watchdog-alerts/]; PagerDuty **ack‑halts‑escalation, 30‑min default timeout** [https://support.pagerduty.com/main/docs/escalation-policies].
- **BELIEVED, not independently re‑verified to a primary spec:** ntfy.sh's **`X-Call` phone‑call TTS** is a *hosted ntfy.sh feature* (and a paid one) rather than a generic self‑hosted capability — multiple ntfy pages reference "phone calls using text‑to‑speech," but I did not pull the exact billing/header spec page (it 403'd). Treat the call tier as "available on ntfy.sh hosted," and verify the self‑host story before depending on it. [https://docs.ntfy.sh/]
- **Contradiction flagged:** "push vs pull" sources disagree on which is "better" in general — that debate is about *metric collection*, and is **orthogonal** to this problem. For *liveness*, the robust property is not push‑vs‑pull but **who renders the verdict on silence** (an independent timer, never the suspect node). [Pull or Push — https://www.alibabacloud.com/blog/pull-or-push-how-to-select-monitoring-systems_599007]
- **Could not fetch (403):** the Google SRE book/workbook chapters, healthchecks.io docs root, ntfy publish page (HTML), and several blogs blocked WebFetch; where used, claims rest on the search‑surfaced excerpts plus a corroborating second source, and on the GitHub‑hosted ntfy docs/Alertmanager discussion that did load.

### Sources
- [How to Set Up Heartbeat and Dead Man's Switch Alerts — https://oneuptime.com/blog/post/2026-02-06-heartbeat-dead-man-switch-opentelemetry-pipeline/view]
- [Dead Man's Snitch: Detecting Silent Cron Job Failures — https://medium.com/@kinjaldand/your-cron-job-didnt-crash-it-vanished-here-s-how-to-catch-it-08b4d46d912c]
- [Dead Man's Snitch FAQ — https://deadmanssnitch.com/docs/faq]
- [Healthchecks.io Documentation — https://healthchecks.io/docs/]
- [Healthchecks.io — Configuring Checks — https://healthchecks.io/docs/configuring_checks/]
- [Healthchecks.io — Configuring Notifications — https://healthchecks.io/docs/configuring_notifications/]
- [Healthchecks.io home — https://healthchecks.io/]
- [ntfy — Sending messages — https://docs.ntfy.sh/publish/]
- [ntfy — publish.md (GitHub) — https://github.com/binwiederhier/ntfy/blob/main/docs/publish.md]
- [ntfy — Using the API — https://docs.ntfy.sh/subscribe/api/]
- [ntfy — Configuration — https://ntfy.sh/docs/config/]
- [Self‑Hosted Ntfy Server (UnifiedPush) — https://unifiedpush.org/users/troubleshooting/self-hosted-ntfy/]
- [ntfy docs root — https://docs.ntfy.sh/]
- [Prometheus — Alerting Configuration — https://prometheus.io/docs/alerting/latest/configuration/]
- [Prometheus — Alertmanager — https://prometheus.io/docs/alerting/latest/alertmanager/]
- [Robust Perception — group_wait vs group_interval vs repeat_interval — https://www.robustperception.io/whats-the-difference-between-group_interval-group_wait-and-repeat_interval/]
- [PromLabs — End‑to‑End Watchdog Alerts — https://training.promlabs.com/training/monitoring-and-debugging-prometheus/metrics-based-meta-monitoring/end-to-end-watchdog-alerts/]
- [Alertmanager Discussion #3227 (deadman switch) — https://github.com/prometheus/alertmanager/discussions/3227]
- [Sysdig — Alertmanager best practices — https://dev.to/sysdig/prometheus-alertmanager-best-practices-4872]
- [PagerDuty — Escalation Policy Basics — https://support.pagerduty.com/main/docs/escalation-policies]
- [PagerDuty — Escalation Policies and Schedules — https://support.pagerduty.com/main/docs/escalation-policies-and-schedules]
- [NodePing — PUSH Check Type — https://nodeping.com/push_check.html]
- [Pull or Push: How to Select Monitoring Systems? — https://www.alibabacloud.com/blog/pull-or-push-how-to-select-monitoring-systems_599007]
- [Heartbeat in Distributed Systems — https://singhajit.com/distributed-systems/heartbeat/]
- [StarRocks — Monitor and Alert (self‑monitoring limitation) — https://docs.starrocks.io/docs/administration/management/monitoring/Monitor_and_Alert/]
- [Securing Your Monitoring Stack with a Dead Man Switch — https://seifrajhi.github.io/blog/securing-monitoring-stack-dead-man-switch/]