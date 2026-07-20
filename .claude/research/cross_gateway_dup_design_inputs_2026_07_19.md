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
