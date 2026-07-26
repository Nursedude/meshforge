# Row 8 design inputs — measured, before anyone designs coordination

> **This is NOT the design pass.** Row 8 (`cross_gateway_dups_unsuppressed`) is
> triaged frontier-tier, "design doc first, never straight to code", and its
> implementation is sequencing-blocked behind the RNS 1.3.8 roll. This file is
> the Opus-tier contribution: the **ground truth a frontier session would
> otherwise burn rationed budget re-deriving**, gathered 2026-07-19.
>
> Rows 3, 7 and 9 each turned out to have a STALE premise that only fell out of
> reading live data. Row 8's premise deserves the same check *before* the
> expensive pass, not during it.

## The row's premise

> "two gateways confirm-deliver the same content; copy #2 can't be cancelled —
> surfaced but not paged/fixed"

The proposed cure is distributed coordination: which gateway yields, race
windows, idempotency, partition behaviour. That is the most expensive and most
failure-prone thing in this codebase's backlog. **The measured question is
whether the problem justifies it.**

## What the detector can and cannot see

`fleet_dup_view.py` is coverage-honest and should be trusted on its own terms:

- a fleet duplicate = same `(content_id, recipient)` reaching CONFIRMED on
  **>1 distinct gateway**;
- a box whose view is absent/torn/stale lands in `uncovered[]` — never folded
  into a healthy zero;
- with <2 *contributing* gateways the verdict is `indeterminate`, and a probe
  must treat that as INERT, not green.

**Live state 2026-07-19**: `status ok`, `covered_hosts ["moc","moc3"]`,
`contributing_hosts ["moc","moc3"]`, `uncovered []`, 121 + 75 = 196 confirmed
pairs tracked, and:

```
fleet_duplicate_pairs        0
fleet_duplicate_deliveries   0
fleet_human_duplicate_pairs  0
fleet_infra_duplicate_pairs  0
```

So today's zero is a *real* observation, not an indeterminate one.

## The rate, from the durable record

The rollup is a window; the mini history is the durable log. Across every
gateway box's history, `gateway_dup_degraded_any` has fired **exactly once,
ever**:

| | |
|---|---|
| edge_up | 2026-06-29T23:59:41 — "2 (content_id, recipient) pair(s) confirmed by >1 gateway" |
| edge_down | 2026-06-30T07:22:31 |
| recipient | `…→58…` — the MeshAnchor infra hash (`58cecbd0`), which is in BOTH gateways' `peer_gateway_destinations` by design |
| classification | **pre-dates** the infra-vs-human classifier, which landed 06-30 (the clear coincides with it) |

**Zero HUMAN cross-gateway duplicates have ever been observed.** The only event
is the infra-to-infra case the existing rule annotation already calls "real but
benign AND structurally unsuppressable with zero added loss".

## ⚠️ The bound on all of the above

`unconfirmable_sent: 14117`.

The mesh (Meshtastic) leg sends without delivery confirmation, so a duplicate
delivered over it **cannot be detected** — there is no confirmation to join on.
Every number here is therefore a statement about the **confirmable population
only** (RNS/LXMF). Stated the honest way, and never averaged into a clean bill
of health (#74's lesson, applied to row 8's own evidence):

- confirmable population: 0 human dups, ever;
- mesh population: **structurally unobservable** — unknown, not zero.

Closing the mesh half is the *same_* dependency as row 9's residual and #74 T2
step 4: **ACK consumption**. Until then no amount of coordination design can be
validated on the mesh leg, because you cannot measure whether it worked.

## What this implies for the design pass (input, not verdict)

1. **The row may be a row-3, not a row-8.** With 0 observed human instances in
   3+ weeks of coverage, "accept-as-permanent with a dated note + keep the
   detector" is a live option that costs nothing and risks nothing. Distributed
   coordination introduces race windows, split-brain behaviour and idempotency
   surface to suppress a fault not yet observed to affect a human.
2. **The infra dup is out of scope by construction.** Two gateways legitimately
   relay to a shared peer destination; copy #2 cannot be cancelled after the
   fact without a coordination substrate that itself adds a loss mode. The
   existing rule already declines to page on it, deliberately.
3. **Any coordination design is unverifiable on the mesh leg today.** Sequence
   ACK consumption first, or scope the design explicitly to the RNS/LXMF leg
   and say so.
4. **Sequencing still binds.** This touches `meshforge-gateway`, which waits
   for the RNS 1.3.8 soak → roll.

## Recommended next step

Do **not** open with a coordination design. Open by deciding, with the numbers
above, whether row 8 is a build or an accept — and if accept, whether the
detector's honest coverage (plus the named mesh blind spot) is the whole
deliverable. That decision is cheap, is the operator's, and it may retire the
hardest row in the backlog without writing a distributed algorithm.

---

# ADDENDUM — the question was wrong, and the answer survived anyway (2026-07-19)

The operator pushed back on the framing above: *"is time going to change this
scenario — is the solution not understood because we aren't asking the right
question?"* That was correct, and it falsifies the reasoning while confirming
the decision.

## What I measured above was a RATE. Rates decay as evidence.

"0 human dups in 3+ weeks" is a claim about an arrival process. If dups are
random arrivals, then accepting on a 3-week zero is just a bet that the soak
was long enough — and time is exactly what breaks that bet.

## So I tested the structural alternative: are dups even POSSIBLE right now?

A cross-gateway dup needs the same `(content_id, recipient)` confirm-delivered
by both gateways — so the recipient must be reachable from both. If the
recipient sets were disjoint, 0 would be a topological guarantee, not luck.

**They are not disjoint.** Per-gateway confirmed recipients:

| gateway | recipients |
|---|---|
| moc | `f68c2f56`×56, `58cecbd0`×53, **`6b1a0120`×3**, **`7cda0fab`×3**, **`9217147e`×3**, `f0a6899b`×3 |
| moc3 | `3dfbdb5d`×21, **`6b1a0120`×18**, **`7cda0fab`×18**, **`9217147e`×18** |

`infra_hashes = [3dfbdb5d, f68c2f56, 58cecbd0]` — the two gateway hashes plus
MeshAnchor. So the three shared recipients are **NOT infra**: they are
human-class, and **both gateways are confirm-delivering to all three today**.
`6b1a0120` is the very recipient this module's docstring cites as "the live
dup-A" — the case that motivated the whole dedup arc.

**Conclusion: the precondition is LIVE.** The zero is not structural immunity;
it is that the two gateways have not lately carried the *same content* to those
shared recipients. Coincident ingest is traffic-dependent, so **yes — time can
and eventually will change the rate.** The operator's instinct was right and the
rate-based justification was the wrong question.

## The decision stands, on a reason that does not decay

Accept, because of **failure-cost asymmetry**, not frequency:

- an unsuppressed duplicate = the message arrived **twice**. Redundancy.
- a yield protocol that misfires = gateway A suppresses expecting B to deliver,
  B fails or the partition heals wrong, and the message arrives **zero** times.
  Silence.

Coordination converts a benign cost defect into a possible loss mode. On
emergency-comms infrastructure the correct direction to fail is redundancy.
That argument is time-invariant: it is just as true at 100 dups/day as at 0, so
it does not weaken as the soak ages — which is precisely what a rate-based
justification would do.

## The better question to keep asking

Not *"how many dups occurred"* (an outcome, and partially unobservable — the
mesh leg has no confirmation to join on), but:

> **"how many recipients are reachable from more than one gateway?"**

That is the PRECONDITION, and it is **routing/configuration state, not a
confirmation** — so it is observable on the mesh leg where outcome-based dup
detection structurally cannot see. It is a leading indicator: dual-homing count
rising means dup exposure rising, visible *before* any duplicate occurs, and it
answers "is time changing this?" with a number instead of a wait.

Today that number is **3**.

**Recommended next slice** (map/fleet layer — reads published views on the
manager, touches no gateway code, so NOT blocked by the RNS roll): surface
`dual_homed_recipients` in the dup rollup and give it a probe. Keep the
outcome detector as-is; add the leading indicator beside it.

---

# VERDICT — ACCEPT RATIFIED AT FRONTIER TIER (2026-07-26, Fable 5)

Row 8 (`cross_gateway_dups_unsuppressed`) is **ACCEPT, ratified**: keep the
detectors, do NOT build cross-gateway delivery coordination. The operator's
2026-07-19 accept stands, on the ground the addendum above moved it to — the
failure-cost asymmetry — and explicitly NOT on the observed rate.

## Inputs re-derived live at ratification (2026-07-26)

`/fleet/dups` fetched this session: `status ok`, `uncovered []` (full 2/2
gateway coverage), moc 127 / moc3 126 confirmed pairs, ALL dup counters 0
(`fleet_human_duplicate_pairs`, `fleet_infra_duplicate_pairs`,
`rns_unconfirmed_pairs`), `dual_homed_recipients: 3` (the same three
human-class hashes as 07-19), `unconfirmable_sent: 15936` (14,117 on 07-19,
+12.9% in 6 days), `untracked_events: 588`.

## The adversarial pass (what was attacked, and what survived)

1. **"0 observed is worthless — the unobservable mesh population grew
   +12.9%/6d."** Correct, and it defeats only the RATE argument, which the
   07-19 addendum already withdrew as load-bearing. The standing reason is
   time-invariant; the growing blind population changes nothing it rests on.
2. **The strongest BUILD counter — static recipient ownership** ("assign each
   recipient a primary gateway by hash; secondary suppresses unless primary's
   heartbeat is stale; no race window"). **Refuted by this fleet's own
   incident record.** The dominant observed failure mode here is PARTIAL
   failure — a unit active but wedged (#68/#72 rnsd accepting connections
   while RPC hangs, #63 services active with dead write paths). A heartbeat
   is a representation of delivery capability, not delivery
   (calibrated_claims #7 in protocol form). Under exactly the fleet's
   most-common failure, the secondary suppresses while the primary delivers
   nothing: **silence, with everything looking healthy**. Static ownership
   does not remove the loss mode; it relocates it into the failure class this
   fleet hits most, and hides it.
3. **Receiver-side dedup middle ground.** Refuted structurally: recipients
   are external clients (NomadNet inboxes, mesh nodes) we ship no code to,
   and the two gateways' copies are distinct LXMF messages with distinct
   source hashes — there is no field for the receiver to join on.
4. **"Accept means the class goes unwatched."** Refuted by live wiring,
   verified this session: `probe_gateway_dup_degraded` gates on HUMAN pairs
   with honest fallbacks (indeterminate on unreachable/stale/garbage, falls
   back to the TOTAL on a pre-split JOIN so it never silently stops paging);
   mini rule `gateway_dup_degraded_any` pages on it;
   `probe_gateway_dual_homed_exposure` edge-fires on each NEW dual-homed
   recipient — the leading indicator, observable on the mesh leg because
   dual-homing is routing state, not a confirmation.

## The verdict, stated with both populations (never averaged)

- **Confirmable population (RNS/LXMF):** 0 human dups ever, ~27 days of full
  2/2 coverage. One fire ever (2026-06-29), infra-class, pre-classifier,
  structurally unsuppressable and deliberately unpaged.
- **Mesh population:** structurally unobservable — **unknown, not zero**, and
  growing (`unconfirmable_sent` 15,936). This accept is NOT a claim that no
  mesh dups exist; it is a claim that suppression would cost more than the
  defect where we can see, and cannot be validated where we can't.
- **Why accept:** an unsuppressed dup fails toward REDUNDANCY (message arrives
  twice — visible, recoverable by the human). A coordination misfire fails
  toward SILENCE (message arrives zero times — invisible, on emergency-comms
  infrastructure). The asymmetry is as true at 100 dups/day as at 0, so it
  does not decay as the soak ages.

## Revisit triggers (each names its first lever — none of them is "design a protocol")

1. **First human-class `gateway_dup_degraded` fire.** The probe pages; decide
   again with a real instance in hand (`/fleet/dups` `fleet_duplicates[]`
   where `recipient_kind==human`).
2. **Material dual-homing growth** (`gateway_dual_homed_exposure` fires
   accumulating). First lever is TOPOLOGY, not coordination: single-home the
   recipient in routing config, or scope suppression to the RNS leg only,
   where confirms exist and a yield can be validated.
3. **ACK consumption lands (#74 T2 step 4).** The mesh leg becomes
   dup-observable; that upgrades the EVIDENCE (the blind population shrinks),
   not the decision direction — the asymmetry argument is unchanged. Re-read,
   don't re-open by default.

Deliverable is complete as of this section: detector (outcome, human-gated) +
leading indicator (precondition, mesh-observable) + this dated decision record
+ eval case `oracle-cross-gateway-dup-accept-cost-asymmetry`.
