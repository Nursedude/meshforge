# Harness Restraint — a dated freeze, and the rule the freeze is really about

> **ACTIVE 2026-09-09 → 2026-10-09.** This file is `@`-included into CLAUDE.md,
> so it costs every turn on every box. It carries its own expiry: **on or after
> 2026-10-09, delete this file and its `@` line** unless the operator renews it.
> A temporary constraint that outlives its date becomes furniture.

## Why (measured 2026-09-09, not asserted)

- **316 commits in 30 days** (~10.5/day), **53% harness-subject**; 121 distinct
  harness files touched against 59 product files. **Zero of the twelve
  most-touched files were product** — number one was
  `.claude/audits/review_provenance.md` at 57 touches, a file that tracks
  reviews. Nothing in that list moves a message.
- The detector layer is NOT the problem. 61 signal classes across 9 boxes:
  **314 clean, 234 inert, 1 indeterminate**, and exactly **one** class inert on
  every box (`oracle_delivery_degraded`). The 08-08 subtraction arc worked.
- The judgment layer IS. Of 38 `known_benign` rejections, **7 sit on blindness
  subjects**, covering three distinct classes —
  `rns_shared_instance_unresponsive`, `delivery_confirmation_stall`,
  `mqtt_root_drift`. **All three were later found to be real detector defects**
  (fixed 07-31 and 08-05). Measured miss rate on that class: **3 of 3.** Worse,
  the 08-02 rejections cite a *memory written to justify the 07-26 rejection* —
  the bug became policy, the policy became memory, the memory re-justified the
  dismissal.

## 1. The freeze

**No new signal class, probe, gate, audit, drill, or detector until
2026-10-09.** Not "replace one to add one" — that brake already existed and 316
commits happened anyway.

EXEMPT, because they *remove* work rather than add it: deleting an instrument;
narrowing one that false-fires; fixing a detector that is blind or misaimed;
and anything whose stated END is not `harness`.

If something truly must be added during the freeze, **that is evidence worth
having** — record what forced it in the session note, and do it anyway. The
freeze is a measuring instrument, not a cage.

## 2. An escalation about the harness is a NOTE, not work

⚠️ Aimed at the SESSION, not at the rules file. On 2026-09-09 a session spent
its first hour on `cron_verdict_stale` → `harness_audit` → the notes file grew
→ because a session had written a handoff. The system alarmed about its own
normal operation and the "cure" was moving text between two files.

**The paging layer was already correct and must not be "fixed".** Verified that
day: `cron_verdict_stale_any` is `propose_escalation` — side-effect-free, it
paged nobody. `detector_blind_any` is `annotate_digest`. The harness classified
it right; the session read the brief and manufactured the work.

So the rule is on the reader:

- A brief escalation whose subject is a **harness housekeeping artifact** (a
  notes file, a verdict log, a ledger, an audit's own bookkeeping) is a thing
  to RECORD, not to fix — unless it names a product END.
- Before acting on any escalation, ask: **which END does this serve?** If the
  only honest answer is `harness`, note it and move on.

⚠️ **Do NOT extend this to instruments that report they cannot SEE.**
`source_error_watchdog`, `source_error_federator` and every `detector_blind`
subject stay exactly as loud as they are: unobservable ≠ healthy, and demoting
them recreates the 08-05 failure above. Likewise `synth_soak` and
`propagation_soak` page for a reason — they measure an LXMF round trip, i.e.
whether a **message arrived**, which is the domain's actual END.

## 3. `known_benign` is not available for a blindness subject

Measured 3-for-3 wrong. A `detector_blind` / `indeterminate` subject may be
resolved as benign **only** when the organ is absent BY DESIGN — and then the
probe must be taught to say `inert`, which is a code fix, not a rejection.

**A rejection may never cite a prior rejection, or a memory written from one,
as its warrant.**

## How this ends

On 2026-10-09, re-run the two measurements at the top (commit split; the
disposition census). If the harness share has not moved, the freeze was not the
binding constraint and something else is — say so plainly rather than renewing
out of habit.

*Slow wins the race.*
