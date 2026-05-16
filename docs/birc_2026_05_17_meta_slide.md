# BIRC 2026-05-17 — META slide spec ("the deck is already stale")

> **For Claude.ai design**: this file specs ONE new slide to APPEND at the
> end of the deck — as a new slide 24, AFTER current slide 23 ("Three
> repos. Three jobs. One domain."). Acts as the closing slide so the deck
> ends on the staleness punchline + canonical-source URL strip. Pulls from
> the same visual system as the rest of the deck.
>
> **Deck context (current 23-slide PDF, 2026-05-16)**:
> - Slide 20 = "Nursedude + AI, every commit on the record" (HOW THIS GOT BUILT)
> - Slide 21 = "Three commands. Then you're in the dialog." (GETTING STARTED)
> - Slide 22 = "MeshForge is a domain, not a repo." (ECOSYSTEM · 5 REPOS)
> - Slide 23 = "Three repos. Three jobs. One domain." (DEEP DIVE)
> - **Slide 24 (NEW, this spec)** = staleness punchline + canonical-source URLs
>
> **Raw URL**:
> `https://raw.githubusercontent.com/Nursedude/meshforge/main/docs/birc_2026_05_17_meta_slide.md`

---

## Why this slide exists

The deck is rendered ahead of talk day. The project is moving fast enough
that some numbers in the deck will be wrong by the time it's projected.
This slide names the gap explicitly — turning a bug into a punchline.

It also points the room at the canonical source of truth (`github.com/Nursedude/meshforge`)
so anyone who wants current numbers knows where to look without having to
ask the operator at the back of the room.

---

## Slide META — "This deck was already wrong by Wednesday"

**Position**: NEW slide 24 (append at end), AFTER current slide 23 ("Three
repos. Three jobs. One domain."). Becomes the deck's closing slide — the
last thing the audience sees is the canonical-source URL strip and the
"the deck is a snapshot, the repo is the artifact" punchline.

**Header label** (top-left, monospace, orange/amber accent — different from
the other meta slides to flag this one as a punchline):
`— META · DECK FRESHNESS · INTENTIONALLY STALE`

**Title** (large white):
"This deck was already wrong by Wednesday."

**Subtitle** (smaller, secondary gray):
"That's the actual point."

**Body** (three-column layout, deck's existing kibble cell style):

### Column 1 · `RENDERED`
- Header: `RENDERED` (gray, monospace)
- Big value (green): `2026-05-16`
- Caption: "MeshForge version + ecosystem test totals frozen here."

### Column 2 · `TRUE TODAY`
- Header: `LIVE` (cyan or blue)
- Big value (cyan): `2026-05-17`
- Caption: "Push to main hasn't paused for the talk."

### Column 3 · `DELTA`
- Header: `Δ` (amber, with the literal delta symbol)
- Big value (amber): one of these formats — operator's pick:
  - `~24 hrs`  *(simple)*
  - `1 commit. 12 tests. 1 minor.`  *(specific — fill in real values from `git log` on 5/17 morning)*
- Caption: "What changed between render and stage."

**Center band** (full-width, slightly elevated, orange accent border —
matches the deck's existing call-out style):

> **The deck is a snapshot. The repo is the artifact.**
> If a number on a slide disagrees with `github.com/Nursedude/meshforge`,
> trust the repo. This deck was true the day it was rendered, and that's
> the strongest claim a 6-day-old snapshot can honestly make.

**Bottom strip** (small kibble cells, deck's existing footer style):

| Cell | Value | Label |
|------|-------|-------|
| 1 | `github.com/Nursedude/meshforge` | CANONICAL · SOURCE |
| 2 | `wh6gxznursedude.substack.com` | FIELD · LOGS |
| 3 | `docs/birc_2026_05_17_live_numbers.md` | LIVE · NUMBERS |

---

## Speaker beats (not on slide)

> "Quick admission. The deck you're looking at was rendered yesterday.
> The repo doesn't stop for slide-prep. By the time we get to Q&A, that
> version number" — point back to slide 10 ('STATUS · v0.6.0-beta') or
> slide 16 ('3,229 tests · 81 files') — "is probably already a commit
> out of date. The github.com URL on this slide has what's true at this
> exact second. The deck is a snapshot. The repo is the artifact. That
> mismatch isn't a bug — it's what the talk's actually about."

The line to land: **"the deck is a snapshot, the repo is the artifact."**
That's the takeaway, and it generalizes — it's what every project should
be able to say but most can't.

---

## Why this works in a HAM-club room

Most BIRC talks open with "here's a thing I built." This one closes with
"here's a thing that's moving faster than I can describe." A working HAM
spending 30 minutes listening to a club talk knows that demos break and
slides go stale. Naming the staleness up-front shows you respect their
time and don't expect them to fact-check you against a moving target.

Closing on this slide makes the canonical-source URL the final image —
once the room knows github is the source of truth, "go look at the repo"
lands as the actual call-to-action, not a generic close.

---

## Implementation notes for design

- Match the deck's existing "how this got built" visual style (slide 20 —
  HOW THIS GOT BUILT · LIVING HISTORY, "Nursedude + AI, every commit on the
  record") so this reads as a sibling, not an outlier.
- The amber/orange accent (versus the green/cyan used elsewhere) is intentional —
  signals "this is the punchline slide" without breaking the visual system.
- If the design system has trouble with the delta value being editable,
  default to `~24 hrs` and let the operator update it at the podium verbally.
- Keep the slide LIGHT — don't fill it with copy. The title carries the line;
  the columns are the proof; the URL strip is the takeaway.
