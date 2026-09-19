# The END-citation rule — a deletion criterion that does not depend on judgement

> **STATUS: PROPOSAL. Not adopted.** Queued for the **2026-10-09 freeze
> review** (`.claude/rules/harness_restraint.md`), alongside the inert-tier
> cut (`.claude/audits/review_provenance.md` → `QUEUED 2026-09-17`). It
> sharpens that cut rather than competing with it.
> **Author**: Opus 5 (1M), 2026-09-18, at the operator's direction
> ("the END-citation rule — write it up for next session… let's build better").
> ⚠️ Adopting this DURING the freeze would itself need justifying: it adds a
> lint. That is precisely why it is queued for the date the freeze is re-decided.

---

## 1. The problem, stated as physics rather than discipline

`harness_restraint.md` measured the disease exactly: **316 commits in 30 days,
53% harness-subject, and zero of the twelve most-touched files were product.**
Its cure was a freeze — a brake applied by willpower.

A brake already existed before those 316 commits ("replace one to add one")
and they happened anyway. So the honest reading is not indiscipline:

* The **product's END** — *a message arrives* — is rare, distributed across
  two or three protocols, and expensive to observe. `synth_soak` and
  `propagation_soak` exist because catching it at all takes an LXMF round trip.
* An **instrument's END** returns a value every tick, on every box, for free.

Effort flows downhill. A freeze fights the gradient; it does not change it.
That is why the 10-09 review may well find the harness share unmoved — and
`harness_restraint.md` already instructs us to say so plainly rather than
renew out of habit.

**This proposal changes the gradient instead.**

---

## 2. The four layers — the distinction the freeze currently blurs

The freeze counts "harness-subject" commits. That bucket is too wide, and on
2026-09-18 it mislabelled a session's own work (including in this author's
closing summary, corrected by the operator's question):

| Layer | What it is | Example, 2026-09-18 |
|---|---|---|
| **1 Product** | a message arrives; truth told in-app | the gateway bridge itself |
| **2 Delivery** | CI, tests, deploy — gets 1 to the boxes | `fleet_sync` skew blindness: a deploy that silently did not deploy |
| **3 Observability** | probes, briefs — tells us about 1 and 2 | `wan_path` aggregation fix (a NARROWING) |
| **4 Meta** | instruments about instruments | index tuning, provenance bookkeeping, handoff trimming |

Layer 2 is not machinery watching machinery — **a fix that never reaches a box
is not a fix**. Layer 4 is the 09-03 audit's real target. A rule that cannot
tell 2 from 4 will cut the wrong thing.

---

## 3. The rule

> **Every signal class must name the END it protects. A class that can only
> answer `harness` is deleted at the next review.**

Allowed ENDs — deliberately few, and 1–2 are the only ones that are the
product's own (`feedback_domain_clarity_two_offerings`):

| END | Means |
|---|---|
| `message-arrives` | a real message crossed a protocol boundary, or failed to |
| `truth-told-in-app` | the operator learns something IN the app rather than outside it (MF018, `in_domain_principle.md`) |
| `delivery` | code/config actually reaches the boxes and runs (layer 2) |
| `operator-safety` | prevents an irreversible or destructive act |
| `harness` | **watches the machinery. This value is a deletion candidate, not a category.** |

A class may cite exactly one END. Citing two is the tell that it is really two
classes, or that the author could not decide — both worth surfacing.

---

## 4. The mechanism — it must REFUSE, not advise

Prose does not refuse (`harness_restraint.md` §2: *"Prose cannot refuse you"*;
`feedback_falsifiability_terminates_rechecking_does_not`). So:

* `SIGNAL_CLASSES` (`src/utils/watchdog_probe_core.py:59`, **61 classes**) is
  already the single registry, with a doc comment per class.
* Add a structured first token to each comment: `# END:<end> — <existing prose>`.
* A lint rule (next free `MFxxx`) reads the registry and **fails** on any class
  with no `END:` tag or an unknown value. Same shape as MF027, which already
  greps probe bodies.
* The refusal message must say **what to do** — name the allowed ENDs and the
  file — because a gate that refuses without saying what to do gets satisfied
  the cheapest way (measured 2026-09-18: this author trimmed comments to fit a
  line cap rather than split a file).

⚠️ **`END:harness` must be WRITABLE.** If the only way to pass the lint is to
claim a product END, every author will claim one and the rule measures nothing.
Honesty has to be the cheap path; the consequence lands at review, not at commit.

---

## 5. What it must DELETE to earn its place

This proposal is itself an instrument. Its defence is that it replaces many
with one criterion — and that defence is void unless instruments actually get
cut. Bind it:

* **First pass must cut.** If the first END-census cuts **zero** classes, the
  rule has failed and should be deleted, not tuned.
* Baseline to beat: the 09-09 census read **314 clean / 234 inert / 1
  indeterminate**, one class (`oracle_delivery_degraded`) inert on every box.
  `feedback_my_footprint_is_the_constraint` already gives the discriminator —
  *never-fired + `clean` everywhere = armed backstop (KEEP); `inert`
  everywhere = CUT.* END-citation adds the second axis: a class that is inert
  everywhere **and** cites `harness` is an unambiguous cut.
* ⚠️ **Do NOT cut a blindness subject on this rule.** `source_error_watchdog`,
  `source_error_federator` and every `detector_blind` class stay exactly as
  loud as they are — unobservable ≠ healthy, and the 3-for-3 measured miss rate
  on that class is why `known_benign` is banned there.

---

## 6. Method — the first pass, in order

1. **Census, no edits.** For each of the 61 classes, write the END you believe
   it serves and whether it has fired in 30 days. Do not tag the file yet.
2. **Sort.** `harness` + inert-everywhere → cut list. `harness` + fires → the
   interesting column: it is doing something, for nobody.
3. **Operator review of the cut list.** Deletion is the operator's call, not a
   lint's. The lint enforces the TAG; a human does the cutting.
4. **Then** tag the registry and land the lint — so the lint arrives on a
   registry that already passes, never as a wall of new failures.
5. Record the counts in `review_provenance.md` so the next review can compare.

---

## 7. How this could be wrong (write the refutation down first)

* **Gaming.** Authors tag `truth-told-in-app` for everything. *Tell*: the END
  distribution has no `harness` entries at all. A census where nothing is
  honest is a census that measures nothing.
* **The product END is genuinely hard to observe**, so layer-3 classes that
  legitimately protect `message-arrives` look indirect and get cut. *Guard*:
  §5's blindness carve-out, plus requiring a named cut list a human approves.
* **It is layer 4 in a product costume.** Fully possible. §5's "first pass must
  cut zero → delete the rule" is the falsifier. Hold the author to it.
* **The freeze may already be working** and this is a second brake on a fixed
  problem. *Tell*: if the 10-09 commit-split measurement HAS moved, park this.

---

## 8. Acceptance criteria

Adopt only if, at the 10-09 review:

1. the commit split has **not** moved (the freeze was not the binding
   constraint — `harness_restraint.md`'s own exit test), **and**
2. a dry census of the 61 classes produces a non-empty cut list, **and**
3. the operator approves that list.

Otherwise: record the census numbers and drop this file. A proposal that
outlives its evidence becomes furniture — the same failure mode
`harness_restraint.md` guards against with its own expiry date.

---

## 9. The companion idea, deliberately NOT bundled

Making the product's END cheap to observe — every organ answering *"when did a
real message last pass through me?"*, folded into one per-protocol
time-since-last-delivered number on the brief, `honest_status` and the TUI —
is the other half of changing the gradient. It is a BUILD, not a subtraction,
so it is freeze-bound and belongs in its own proposal. Noted here only so the
next reader knows this file is half of a pair.

*Slow wins the race.*
