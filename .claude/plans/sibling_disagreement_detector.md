# Sibling-disagreement detector — investigated 2026-09-08, **recommendation: DON'T build it**

> Written at the operator's request as the next-session note. He asked the
> right four questions — why / what / does it replace / does it watch
> something that was silent — and asking them killed the idea I had pitched
> an hour earlier. The measurement that killed it is below; the thing worth
> building instead is in §5.

## 1. Why I proposed it

The fact that cracked the 2026-09-08 RNS-RPC incident was a **contradiction
between two detectors of one claim**: the cascade fingerprint `rns_rpc_wedge`
fired hourly on moc while `rns_rpc_unresponsive` — an actual RPC round-trip,
the more direct oracle — read `clean` on that box for the entire week. It sat
in the journals unread because nothing compares sibling verdicts. Generalising
that seemed obviously right: *two probes of one subject that disagree for N
ticks is its own finding.*

## 2. What killed it — the coverage measurement (do not re-derive, re-run)

```
distinct subject expressions emitting Signals : 46
subjects with >1 signal class                 :  8
```

But **shared subject is not shared claim**, and on inspection every one of the
8 groups is a set of probes making *different* claims about one thing:

| subject | classes | same claim? |
|---|---|---|
| `rnsd` | `foundation_perms_drift`, `rns_rpc_unresponsive` | no — file modes vs RPC latency |
| `service_name` | `fd_exhaustion`, `service_inactive`, `meshtasticd_vsz_leak`, `http_local_unresponsive`, `main_thread_wedge`, `phoneapi_tcp_leak` | no — six different failure modes |
| `meshtasticd` | `meshtasticd_phoneapi_wedge`, `mqtt_root_drift` | no |
| `ntfy` | `ntfy_ack_stale`, `ntfy_loopback` | adjacent, not substitutable |
| `@rns/<name>` | `rns_instance_name_mismatch`, `rns_namespace_collision`, `rns_shared_instance_unresponsive` | related layers, not substitutable |
| `meshforge-gateway`, `subj`, `mini-dudeai` | 6 / 2 / 2 | no |

**Genuinely substitutable oracle pairs in the whole tree: approximately zero.**
Disagreement between probes making different claims is not a finding — it is
the normal state of a healthy system.

### And the killer detail

**The motivating pair is not in that table at all**, because
`rns_rpc_wedge` is a *cascade fingerprint*, not a `Signal`. The two live in
**different processes**: `cascade_detector` is hosted in `meshforge-map`,
`rns_rpc_unresponsive` in `meshforge-watchdog`. So the simple version — compare
dispositions inside the watchdog tick — **would not have caught the incident
that motivated it.** Catching it requires bridging two subsystems across a
process boundary, for a population of ~zero other pairs.

## 3. Answering the operator's four questions honestly

* **Does it replace something?** No. It is a pure addition, and the 09-03
  volume brake says add a class only by replacing one. Fails.
* **Is it an upgrade?** No — it is a new organ with new failure modes of its
  own (a meta-detector can go blind exactly like the detectors it watches, and
  then nothing watches *it*).
* **Does it watch something that was silent?** Only for one pair, and that pair
  is now fixed. Any future pair would have to be hand-curated — and if you know
  enough to name the pair, you already know enough to fix the weaker probe.
* **Is it machinery watching machinery?** **Yes, literally** — against the
  standing doctrine that instruments serve the product and never watch each
  other. `detector_blind_any` is the domain's one sanctioned exception, and it
  earns that by watching *blindness*, which has direct product consequence.
  This would not clear that bar.

## 4. The correction to my own reasoning

I generalised from **one** vivid instance without measuring the population —
the same error shape as the bug I had just fixed (a claim correctly derived
from a measurement of the wrong quantity). The mechanism that actually caught
the bug was **not** "two detectors disagreed". It was *comparing a detector's
verdict against an independent, more-direct observation of its subject* — the
gateway's own `rpc[rnsd.path_table_read] ok 0.000s` lines. The sibling was
merely one available source of that. The general principle is already written
down as rule 2 of `feedback_detector_blind_is_a_finding`: **the discriminator
is ground truth.** It did not need new machinery; it needed me to apply it.

## 5. Build this instead — the falsifier rides WITH the claim

Zero new classes, zero new processes, no meta-detection:

> **A probe emitting a strong claim must also record the independent
> observation that would falsify it.**

Today's fix already does this and it is why the next reader will not repeat the
week we just spent: `rns_rpc_unresponsive` now carries `loadavg` + CPU PSI on
the signal itself, which is precisely the falsifier for *"is this rnsd, or was
the box buried?"* The operator reads the answer **on the page**, instead of
reconstructing it from journals afterwards — which was the entire cost of this
incident.

That is not an instrument watching an instrument; it is an instrument carrying
its own control. Next session's work, in order:

1. **Sweep the strong-claim probes for a missing falsifier.** For each probe
   emitting `severity="wedge"`, ask: what one cheap observation would tell the
   operator this is NOT the named subject? Attach it to `extra` + `detail`.
   Candidates to check first: `main_thread_wedge`, `http_local_unresponsive`,
   `fd_exhaustion`, `meshtasticd_phoneapi_wedge` — all of them convert a
   symptom into a named culprit, which is this defect class's exact shape.
2. **Weigh cutting `rns_rpc_wedge` entirely** (see §6). Subtraction, not
   addition, is the honest response to a detector that has only ever lied.
3. Consider a write-time rule in `honest_failure_modes.md` once (1) shows
   whether the pattern generalises — **not before**; do not promote a rule from
   one instance again.

## 6. Open decision: should `rns_rpc_wedge` exist at all?

Measured over the observable journal (Sep 06 15:09 → the 06:56 fix): **9 fires,
9 false, 0 true positives.** Its claim is fully covered, more directly, by
`rns_rpc_unresponsive`. The subtraction rule sorts detectors into *`clean`
everywhere = armed backstop, keep* and *`inert` everywhere = cut*; this is a
third category it does not name: **fires, and has only ever been wrong.**

Honest argument to keep: it watches the *connect* layer, which the rnstatus
probe cannot see, so a future wedge shape might trip it first. Honest argument
to cut: it never once has, and every detector is new surface for exactly the
defect this session was about. I lean **keep for now, decide at the next
subtraction pass** — but flag that this is a judgement call I made, not an
obvious one, and that cutting it would also have removed the disagreement that
started this whole note.

## 7. If a future session still wants the detector

Do not build it until the population argument changes. It becomes worth
revisiting only if BOTH hold:
* three or more genuinely substitutable oracle pairs exist (re-run the §2
  measurement — never trust this file's number), **and**
* at least one live disagreement is found that no existing probe reports.

Until then this file is the answer, and the answer is no.
