# Signal-class yield audit — the subtraction arc

**Date**: 2026-08-08 · **Scope**: all 62 members of `SIGNAL_CLASSES`
(`src/utils/watchdog_probe_core.py`) · **Deliverable**: a DELETE list, per
[[feedback_my_footprint_is_the_constraint]] ("prefer removing to adding").

No new code was written and no auditor was built. Every number below is
re-derived from artifacts that already existed:

| Source | What it gives |
|---|---|
| `SIGNAL_CLASSES` | the 62-member closed enum |
| `~/mini_dudeai_history.jsonl` on **all 8 MeshForge boxes** | every `edge_up` since 2026-05-28 → "has it EVER fired" |
| `/api/fleet/truth` (`coverage.classes`) | live per-class disposition × 8 boxes → "can it even look" |
| `~/mini_dudeai_memory_deltas.jsonl` (186 rows) | proposal ratify/reject + the prose reason |
| `~/morning-watchdog-reports/` (80 daily reports) | independent cross-check on which classes ever appeared |
| `configs/mini_dudeai_rules.*.json` + live rules | confound check: does every class have a rule that *could* fire |

---

## The headline

**The enum is not mostly wrong. It is mostly scoped.** Only a small tail is
genuinely dead weight, and the single test that separates the two was sitting
in the coverage map the whole time:

> **A class that has never fired but reports `clean` is an armed backstop —
> it is proving every tick that it can see. A class that has never fired and
> reports `inert` everywhere has never once been able to look.**
>
> Only the second is dead weight. `clean` and `inert` are not the same silence.

18 of 62 classes have never fired anywhere. **Ten of those are `clean` on 8/8
boxes** — `fd_exhaustion` (#73), `phoneapi_tcp_leak` (#17/#75),
`meshtasticd_vsz_leak`, `delivery_write_canary` (#63), `queue_backlog`,
`foundation_perms_drift`, `history_write_stalled`, `user_unit_inactive`,
`rns_shared_instance_unresponsive`, `rns_instance_name_mismatch`. Those are
working exactly as designed and are **not** removal candidates. `meshtasticd_vsz_leak`
is explicitly threshold-set never to fire while the weekly restart holds the
envelope — silence there is the specification.

---

## DELETE list

### Tier 1 — remove now (2 classes, `inert` on 8/8 boxes)

These have never been observable on any box in the fleet.

| Class | Fires | Why it can never look |
|---|---|---|
| `aredn_organ_undeclared` | 1 (moc1, once) | `inert`×8, and for **two mutually exclusive reasons**: 4 boxes *"no AREDN LAN here"*, 3 boxes *"aredn_node_ips configured — the configured-source legs own this box."* Both the has-it and hasn't-it populations are covered by something else. There is no third state where it fires. |
| `lxmf_propagation_unused` | 2 | Same contradiction: `inert` on boxes with no gateway, and `inert` on the 2 boxes where *"propagation_node configured — capability adopted."* It is a one-time **adoption nudge**, already answered, running in a 30 s loop forever. |

**Cut**: the enum member, its probe, its rule in the role seeds, its tests.

> ⚠️ **`propagation_soak_degraded` was in this tier and it does not belong here.
> The delete recommendation was wrong.** See "The correction" below. It is a
> FIX, not a cut, and it is the most valuable finding in this audit.

### Tier 2 — remove, and say why out loud (2 classes)

| Class | Age / fires | Argument |
|---|---|---|
| `json_uplink_dark` | added **2026-08-07**, 0 fires | Its coverage split is `clean:4 / inert:4` — **byte-for-byte the same split as `channel_feed_dark`**, the detector it sits behind. It carries no information the class below it does not already have. This is the one the operator flagged in his own accounting: *"it watches the instrument behind another detector, which is literally a layer up. The cheaper design was a richer disposition on the existing class."* The measurement agrees with the instinct. |
| `dream_ratification_stalled` | added 2026-07-22, 0 fires in 17 days, observable on 1 box | Machinery watching machinery, by definition: it watches mini's own ratification cadence. The warm brief already prints the ratio on every session start. Nothing is added by a 30 s probe on 9 boxes. |

### Tier 3 — demote off the 30 s tick, don't delete (3 classes)

Real checks, wrong cadence. Move to a daily/6-hourly cron with a
`cron_verdict` wire; keep the finding, drop 2,880 ticks/day/box.

- `oracle_delivery_degraded` — 0 fires in 47 days; `inert`×7 (*"oracle never wrote a log (disabled/never queried)"*). The organ is effectively out of service fleet-wide.
- `inherited_app_drift` — 0 fires in 48 days; observable on **moc5 only**. Checkout drift changes on the timescale of deploys, not seconds.
- `claw_uplink_node_moved` — 0 fires; observable on **moc2 only**. Node-moved is a deploy-timescale fact.

(`claw_rf_silent`, same shape, is 20 days old and belongs to the live claw-RF
arc — **too young to judge**. Re-measure 2026-09.)

---

## The correction — I made the exact error this audit is about

`propagation_soak_degraded` was Tier 1 on this evidence: `inert` on 8/8 boxes,
reason *"no propagation node adopted — nothing to exercise."* I read that
string as a fact about the fleet. **It was the probe lying about itself**, and
the memory that warns about precisely this (*"distrust the probe's explanatory
text — both named culprits they could not see"*) was in context when I wrote it.

What execution turned up, from checking the thing instead of its description:

- The soak timer is **enabled and firing hourly on moc and moc3**.
- On moc3 it publishes healthy envelopes — `pass_envelope: true`, `ok_ratio 1.0`,
  a real `propagation_node`, newest one minutes old.
- Each fire costs **4 min 47 s of CPU** — on the **905 MB box**, hourly, ≈8 % of
  a core continuously.
- And the probe that exists to read those envelopes reported, every 30 s, that
  there was nothing to exercise.

**Root cause**: the intent gate resolved the operator home with
`get_real_user_home()`. Under `User=root` with no `SUDO_USER` and `LOGNAME=root`
that falls back to `/root`, and `/root/.config/meshforge/gateway.json` does not
exist → `absent` → `inert`. Verified on moc3: the operator-UID lookup returns
`/home/wh6gxz`, whose `gateway.json` holds exactly the node the drill uses;
`/root/.config/meshforge/gateway.json` is confirmed absent.

This is the **same root cause as the 2026-08-05 `rns_instance_name_mismatch`
dig** — a root service's `~` is `/root` — and
`watchdog_runner._operator_home_for_root` documents the trap in this very
codebase. Fixed by using the operator-UID resolver the sibling propagation
probes already use.

Two lessons, both already written down and both walked past:

1. *A fix applied to one instance is not applied to the class.* 08-05 fixed the
   RNS probe. Nobody grepped the other `get_real_user_home()` callers in probe
   code. There was exactly one left, and this is it.
2. *A long-running `inert` is a finding, not furniture* — and an audit that
   reads dispositions **must not accept the reason string as evidence**. The
   disposition told me where to look; only the live check told me what was true.

**Open question for the operator, not decided here**: now that the probe can
see, is an hourly 4m47s-CPU drill on the 905 MB box worth its cost? The drill
passes, so the probe will read `clean`. Options: keep, stretch the cadence, or
run it only on moc. That is a cost/coverage call, and it is yours.

## Two dark corners the audit surfaced

### 1. `nomadnet_crashloop` can never report `clean`

It is `inert` on 8/8 with the reason
*"no live restart loop (healthy, remediated, or no nomadnet)."*
That string **collapses healthy with absent** — the exact `honest_failure_modes`
#2 defect, and the same class as the 2026-08-05 and 08-07 findings.

The consequence is precise: this detector's coverage cell is
**indistinguishable from a detector that has been deleted**. It cannot pass
the "armed backstop" test above, not because it is broken, but because it has
no vocabulary to say "I am watching and it is fine."

⚠️ **Do not delete it** — it guards #82, which ran undetected for 10 days
(NRestarts=7842). **Fix the disposition**: split *no nomadnet user unit
enrolled here* (`inert`) from *enrolled, observed, not looping* (`clean`).
That is a 3-line change and it converts a dead-looking cell into a
provably-armed one.

### 2. The "49 rejected `unspecified`" statistic is misleading

The warm brief reports rejections *"by reason: unspecified ×49"*, and the arc
premise reads that as *"a detector whose proposals are always dismissed
without a reason."* Re-derived from the deltas file:

- **149 rejections. 149 of them carry a written `resolved_note`. Zero do not.**
- 107 lack the *structured* `resolved_reason` field; 42 have one
  (`known_benign` 19, `noisy_detector` 9, `duplicate` 7, `already_fixed` 6,
  `not_actionable` 1).

The notes are substantial — most record a live verification done at the time
("HTTP 200/2 ms, federation 0 failures, verified live this session"). The
reasons exist; only the machine-readable field is missing, and the brief
renders that absence as *"unspecified"*, which reads as *"nobody thought about
it."*

**Subtract the misleading stat, not the detectors.** Either backfill
`resolved_reason` at rejection time or stop printing the breakdown.

*Secondary discrepancy, flagged not resolved*: the brief reports **17/97**
ratified; the deltas file itself holds **37 ratified / 149 rejected = 186**
rows, all host `VolcanoAI`. The rollup and its own source disagree by roughly
half. UNKNOWN which is right — worth one look, since this ratio is quoted at
every session start.

---

## The noise ranking (yield's other half)

Not a delete list — a *tuning* list, ordered by fires-per-insight.

| Class | Fires (fleet) | Proposals | Finding |
|---|---|---|---|
| `tracer_peer_unreachable` | **200**, all 8 boxes | 12 rejected / 2 ratified | The loudest class on the fleet, and the notes are near-unanimous: *"transient RNS lab-tracer timeouts"* while the same box verifies healthy on ICMP, HTTP 200, and federation `ok=true, 0 consecutive_failures`. **Federation peer status already knows.** Gate the signal on the federation view disagreeing, or it stays a 200-fire tax on attention. |
| `ntfy_loopback` | 16 (1 box) | **12 rejected / 0 ratified** | Every rejection is the same shape: a *single* transient publish miss that self-cleared by the next 2 h run. Fires on `consecutive_misses=1`. **Raise to ≥2 consecutive.** |
| `cron_verdict_stale` | 125 | 12 rejected / 6 ratified | Genuinely useful — but it **compound-fires on `ntfy_loopback`'s verdict**, and the notes say so explicitly: *"Watcher-watching-the-watcher: cron_verdict_stale reports FAIL ONLY BECAUSE ntfy_loopback wrote FAIL(1) — same single transient."* One event, two pages. This is the operator's exact worry, already documented in his own rejection notes since 2026-06-25. **Decouple, or fixing `ntfy_loopback` alone halves this too.** |
| `rules_seed_drift` | 181, all 8 boxes | 11 rejected / 4 ratified | High volume, but the ratified ones were real un-merged seed bumps. Keep. |

**One cross-cutting noise source is not a signal class at all**: the
`new_subject` dream kind. Nearly every rejection reading *"first-ever sighting
of <a known fleet box>"* is a rule-birth or state-reset artifact — a fresh
rule, or a mini state reset, makes every long-standing subject look new. That
one heuristic accounts for the largest single block of rejected proposals.
Suppress `new_subject` for subjects present in the fleet registry, and for the
first N ticks after a rule is merged.

---

## What I did NOT measure

**Per-probe CPU cost — UNKNOWN.** There is no per-probe timing instrumentation,
and the only honest way to get it is to run the probe suite with a timing shim,
which writes to live debounce/streak state files and would perturb the running
detector spine on the box being measured. Not worth corrupting the spine for a
ranking. The cost argument here rests on tick count (62 classes × 2 ticks/min ×
9 boxes) and on the moc3 datum already on record (**4.3 s CPU per 30 s tick, 64 MB
= 7.1 % of a 905 MB box**), not on per-class attribution.

## What was executed — 2026-08-08

**4 classes deleted, 62 → 58** (Tier 1 minus the correction, plus Tier 2):

| Class | Disposition of the code |
|---|---|
| `json_uplink_dark` | probe folded back into `channel_feed_dark`: declared-and-absent is now `indeterminate` with the whole diagnosis in its reason (`detector_blind` escalates a persistent one). A regression test pins that this probe emits exactly ONE class. |
| `dream_ratification_stalled` | probe, its 3 private helpers, its tunables, and the `mini_dudeai.dreams` import removed from the watchdog entirely (−226 lines). |
| `aredn_organ_undeclared` | probe + `_resolve_aredn_localnode` / `_fetch_aredn_sysinfo` / `AREDN_LOCALNODE_NAME` removed (−211 lines). Its structural-dark row is marked **REOPENED BY CHOICE**, not deleted. |
| `lxmf_propagation_unused` | probe removed (−123 lines); the shape-A sibling `lxmf_propagation_node_dark` stays (it watches a LIVE dependency). Its "one fault, one owner" reason now says the gap is knowingly unwatched instead of naming a deleted probe. |

Plus: seed rules dropped from both role seeds; `traffic_pulse`, the
re-export hubs, and every ghost reference updated.

**1 blindness fixed**: `propagation_soak_degraded` intent gate now resolves the
operator home by UID instead of `get_real_user_home()`.

**Not yet done** (Tier 3 + the tuning list): `oracle_delivery_degraded`,
`inherited_app_drift`, `claw_uplink_node_moved` demotions; the
`nomadnet_crashloop` disposition split; `ntfy_loopback`'s threshold;
`new_subject` suppression; the `unspecified` stat.

Nothing removed coverage of any incident recorded in `persistent_issues.md`.

---

## Verification status

**VERIFIED for the code change** — checks run this turn on this working tree:

- `python3 -m pytest tests/ -q` → **`exit 0`, 10397 passed, 1 skipped** (baseline before the cuts: 836 passed on the watchdog subset, exit 0).
- `python3 scripts/lint.py --all` → **`exit 0`**.
- `python3 scripts/parity_check.py` → **`exit 0`** after porting the twinned `fleet_truth.py` to MeshAnchor (it caught the drift; that is the gate working).
- The `propagation_soak_degraded` diagnosis, on moc3 itself: operator-UID home → `gateway.json` → `3968a2ee…`, the same node the drill uses, and `/root/.config/meshforge/gateway.json` confirmed absent.

**BELIEVED, not verified**: that the `propagation_soak_degraded` cell actually
flips `inert` → `clean` in production. That requires the ROOT watchdog on moc3
to run this code, and the fleet deploys manually. The check that would upgrade
it: deploy, then read that box's coverage cell. Verifying the resolver from a
non-root shell is a proxy, and this whole audit is a lesson about proxies.

**UNKNOWN**: per-probe CPU cost (see above). The 4m47s figure is for the soak
*drill*, measured from systemd's own accounting on moc3 — not for a probe.
