# Proposal — one domain roadmap that every repo and every session can find (2026-10-19)

**Status: DRAFT for operator review → then `mattpocock-skills:grilling` → then ROADMAP.md.**
Nothing here is applied yet. Operator framing: *"continuity matters — users could
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
- `parity_check.py` gains a leg: each sister repo's CLAUDE.md carries the pointer

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
