# Proposal — one domain roadmap that every repo and every session can find (2026-10-19)

**Status: DECIDED 2026-10-19 (operator, grilling — 25 decisions, recorded below; they SUPERSEDE
the draft text where they differ).** **MIGRATED 2026-10-22** (operator "go ahead"; same session as
other work, by the operator's call over Q13) — gates `scripts/roadmap.py` + `tests/test_roadmap.py`;
record in that day's provenance row. MA + maps pointer lines land in their own repos. Operator framing: *"continuity matters — users could
run maps via meshforge or standalone. it just works."* Origin: the outside research
review artifact (2026-10-18) noted the domain would benefit from a more cohesive
roadmap.

## Verdict

Not a new document. `.claude/ROADMAP.md` is already declared the domain's one
roadmap (MA's copy was marked superseded 2026-10-18). It is cohesive in NAME and
MeshForge-only in CONTENT, and the other repos cannot find it. Five small changes
make it the domain's in fact.

## Measured state (2026-10-19, not recalled)

| Repo | Roadmap file | Its CLAUDE.md points to the SSOT? | Mentions in the SSOT |
|---|---|---|---|
| meshforge | `.claude/ROADMAP.md` 29 KB (SSOT) + superseded `plans/v1.0_roadmap.md` | — (is the SSOT) | — |
| meshanchor | superseded `plans/v1.0_roadmap.md` only | **0** | MeshAnchor 5 |
| meshforge-maps | none | **0** | **0** |
| meshing_around_meshforge | none | no CLAUDE.md | **0** |
| RNS-Management-Tool / Gateway-Tool | — | — | **0** |

"What's next" lives in **four** places: ROADMAP Now/Next/Later · `review_provenance.md`
`## QUEUED` rows (3 added today alone) · per-box handoff notes · `~/deferred_work.json`.
Now/Next/Later is ~190 lines and still carries finished items (`~~MET 10-05~~`).

## The five decisions (grill these)

**R1 — Every item carries a stable ID and its reach.** Reuse the IDs we already
speak in (`C21`, `BIC-3`, `R8`, `M5`); never renumber, so handoffs and commits can
cite them. Each item line names:
- **repos** it touches: `[MF] [MA] [maps] [bot] [rns-tools]`
- **twin owed**: `twin→MA` when MF lands it first and MeshAnchor must follow
  (the most frequent recurring work in the domain, today tracked only in handoffs)
- **offering**: `S` standalone · `F` fleet · `S+F`
- **maps mode** (operator, 2026-10-19): an item touching `[maps]` declares
  `maps:both` — it is accepted only when it works as a MeshForge plugin AND
  standalone (8808/8809). *It just works* in both is the contract, so it is
  part of the acceptance sentence, not a footnote.

**R2 — One home for "next".** The ROADMAP is the only place a future item lives.
Provenance records what HAPPENED (with the ID); handoff notes say which ID is in
flight and point at it; `deferred_work.json` holds only timed reminders for an ID.
A provenance `## QUEUED` row becomes either a roadmap item or an explicit
"won't do — because". Continuity: a fresh session reads ONE list.

**R3 — Done leaves the list the day it's done.** Same growth rule that fixed
`persistent_issues.md`: a finished item moves to provenance as one line
(`C21 DONE <sha> — verdict`), never struck-through in place. History is in
provenance and git, not in the plan.

**R4 — Every repo points at it.** One line in each sister repo's CLAUDE.md:
*"Domain roadmap SSOT: meshforge `.claude/ROADMAP.md` — items tagged [MA]/[maps]
are this repo's."* The sister repos keep NO roadmap of their own.

**R5 — Enforced, not remembered** (build:fix doctrine — gates, not prose):
- lint: ROADMAP size cap (like MF012 for persistent_issues); fails loudly
- test: every Now/Next item has an ID and a repo tag; `[maps]` items carry `maps:both`
- ~~`parity_check.py` pointer leg~~ — DROPPED (Q25: machinery watching one line; the
  session-start NOW print does the real job)

## Before / after — three real items

**Before** (today, NOW §, prose with history inline):
> 2. ~~**rnsd onto mf.4**~~ **MET 10-05** — honest_status leg `running substrate`
> PASS twice on 10-05 (14:20, 15:06 runs): "60 units on 10 boxes run …" ⚠️ Two caveats…
>
> 1. **BIC-3 web auth** (token/session, viewer/operator roles) BEFORE anything on …

**After:**
```
NOW
- C21  [MF] S+F twin→MA   Cross-preset bridge proven: 24 h run c21r1 → reconcile → capabilities row
                          (smoke3 PASS 16/16; plan: plans/c21_cross_preset_run_2026_10_18.md)
- BIC-3 [MF][MA] S+F      Web auth (viewer/operator) before any write surface — fixes DEPLOYED 10-15,
                          non-author read owed
NEXT
- R8   [MF][maps] S+F maps:both   One RF-leg pane: airtime vs budget, per-hop success, dead-hop
```
…and the `rnsd onto mf.4` line is gone from the list — it lives in provenance as
`mf.4 DONE 10-05 — running substrate PASS ×2 (caveats: 24 units unjudged, lehua UNKNOWN)`.

## What this deliberately does NOT do
- No new document, no per-repo roadmaps, no tracker tool, no web board.
- No re-prioritisation — moving content is separate from changing order; the
  operator's priorities stay exactly as written until a grilling changes them.
- No change to `persistent_issues.md` (that is live state, not plans).

## Migration (one session, after the grilling)
1. Tag every existing Now/Next/Later item (IDs + R1 fields); move finished items to provenance.
2. Fold today's provenance QUEUED rows into items or "won't do" (frequency.py slot map,
   installer role-awareness, mini enrollment, wedged-leg detection, MA dedup port,
   MeshCore POLL path).
3. Pointer line in MA + maps CLAUDE.md (each repo's own commit, its own CI).
4. The R5 gates — test-first, each shown failing on today's file.
5. Delete the two superseded `v1.0_roadmap.md` copies (their v1.0 criteria already
   live in ROADMAP "1.0 = these checks pass").

## Open questions for the grilling
1. Is `meshing_around_meshforge` in the domain roadmap, or its own upstream-fork world?
2. Do the RNS tool repos still earn a tag, or are they retired (0 mentions today)?
3. Does `twin→MA` mean "MA within the same week" or "MA before the next MF release"?
4. Should a `[maps]` item that only touches standalone (no plugin path) be allowed at all?
5. Who may move an item between Now/Next/Later — operator only, or any session with a reason?

## DECIDED — the 25 answers (operator, 2026-10-19; all "yes" to the recommendation)

**Scope & home**
- Q1 Repos in the one roadmap: MF, MA, maps, bot (`meshing_around_meshforge`), RNS/LXMF forks (`[fork]`). OUT: `RNS-Management-Tool`, `RNS-Meshtastic-Gateway-Tool` (ARCHIVED 2026-09-25, measured) — fix `foundations/meshforge_ecosystem.md`, which still lists them active.
- Q2 Home stays meshforge `.claude/ROADMAP.md`; sister repos keep none, one pointer line each.

**Items**
- Q6 Every item: S / F / S+F.
- Q10 Stable IDs (existing kept; new = family prefix); a test keeps them unique; never reused.
- Q7 + Q18 Twin tags BOTH ways — `twin→MA` (MF leads: RNS, bridge, harness) / `twin→MF` (MA leads: MeshCore). The lead lands first; the item is DONE only when the twin lands too (or the operator declares the twin not applicable).
- Q8 `[maps]` items: `maps:both` by default (plugin AND standalone 8808/8809 = acceptance); single-mode only with a written reason.
- Q9 Upstream offer-backs = `[fork] offer-back` items in LATER; posting needs the operator's go.
- Q14 An item = anything that outlives the session it was found in; same-session fixes go straight to provenance.

**Flow**
- Q3 The ROADMAP is the ONLY list of future work. Provenance = what happened; its `## QUEUED` sections are retired; handoffs cite IDs; `deferred_work.json` holds only timed reminders pointing at an ID.
- Q4 Done leaves the list the same day → one provenance line `<ID> DONE <sha> — verdict`; never struck through in place.
- Q5 NOW is the operator's (only the operator moves items in/out); sessions may ADD to NEXT/LATER with a reason and must say so in the handoff.
- Q15 "Won't do": sessions propose with a reason; the operator confirms; until then the item stays.
- Q16 The session-start hook prints ROADMAP NOW (ID + one line) in MF AND MA checkouts.

**Structure & gates** (each built test-first, shown failing on today's file; non-author review of the gates)
- Q17 Strategy sections (What we are / Why we win / 1.0 checks / Rules) stay, stale-fixes only; only Now/Next/Later is restructured; they count toward the cap.
- Q11 Size cap = measured post-migration size × 1.25, lint-enforced, ratchet down only.
- Q25 Pointer-parity gate DROPPED.

**Public roadmap for users and testers**
- Q19 `docs/roadmap.md`, linked from the top of the README; plain language; by reader — *If you run one box* (S) · *If you run several boxes* (F) · *Maps* ("works inside MeshForge or on its own") · *Sister projects*; a small `(ref ID)` per item.
- Q20 GENERATED from the internal roadmap; an item appears only with a `public:` one-sentence line; a test fails if the committed file differs from the generator's output.
- Q21 Three statuses, no dates: **Works today** (evidence required) · **Being tested — help wanted** · **Planned**.
- Q22 "Help wanted: <hardware/setup> — <what to try>" on being-tested items, linking a **Test report** issue template (hardware, firmware, offering, steps, result, logs).
- Q23 The template lives in MeshForge AND MeshAnchor, label `tester-report`; a report that moves a status is cited in provenance by issue number.
- Q24 The public file is under MF014/MF015; the generator REFUSES a `public:` line that would fail them.

**Migration** (Q12/Q13): fresh session · one content commit · today's 6 queued rows triaged (operator confirms any won't-do) · the two `v1.0_roadmap.md` copies deleted only after a line-by-line diff into "1.0 = these checks pass" · gates test-first + non-author review · MA/maps pointer lines via their own repos' commits and CI.

**Open, not a design question:** whether testers come. A page does not recruit; linking it where hams already gather is the operator's call.
