# BIRC 2026-05-17 — NOC screenshot slide spec

> **For Claude.ai design**: this file specs ONE new slide to insert
> between slide 13 (Field Validation · 5 Pis · Hawaiʻi) and slide 14
> (Running Now · meshforge-maps · 53,234 nodes). The pair forms a
> visual arc — "operations layer" (this new slide) → "data layer"
> (slide 14). Match the deck's existing kibble system.
>
> **Raw URL**:
> `https://raw.githubusercontent.com/Nursedude/meshforge/main/docs/birc_2026_05_17_noc_screenshot_slide.md`
>
> **Asset**: one screenshot, captured from
> `http://localhost:5000/fleet` on meshanchor-server (operator handles
> capture + MF015 scrub; details below). Drop into Claude.ai design
> separately from this markdown.

---

## Why this slide exists

Slide 14 already shows the **data layer** — 53k nodes on the global
meshforge-maps view, the public-facing visualization. Slide 13 shows
the **fleet inventory** — 5 boxes with role/preset cards. There's no
slide showing the **operations layer**: what does an operator see
when they SSH into one of these boxes to ask "is anything stuck?"

The NOC rollup view answers that. Pairing this new slide with slide
14 in adjacent positions lets the audience see the full stack:
operations → data → map.

This also makes concrete the "fleet IS the QA" thesis that runs
through slides 9, 10, 12, 13 — the NOC is the surface where that
thesis lives in practice.

---

## Slide NOC — "Fleet status, one URL"

**Position**: between slide 13 and slide 14.

**Header label** (top-left, monospace, cyan accent — matches the deck's
"RUNNING NOW" / "FIELD VALIDATION" voice):
`— OPERATIONS · LIVE NOC · 192.0.2.X / FLEET`

**Title** (large white):
"Fleet status, one URL."

**Subtitle** (smaller, secondary):
"The operator's first SSH on any box. Truth state across six peers."

**Main visual** (full-width, centered):
A screenshot of the `/fleet` page rendered in a browser. The page
shows three panels stacked vertically:
1. Self panel (the box you're looking at, fully populated)
2. Fleet rollup (the 6 peer table)
3. Federation panel (the 20 RNS announces)

**Number strip** (bottom of slide, 4 kibble cells, deck's existing
style — pull these from the live rollup at slide-prep time):

| Cell | Value | Label |
|------|-------|-------|
| 1 | `6` | PEERS · 5 RUNNING · 1 DELIBERATELY DISABLED |
| 2 | `20` | RNS ANNOUNCES · 24H WINDOW |
| 3 | `BW62 / SF7` | MESHCORE RADIO · 910.525 MHz |
| 4 | `4 / 4` | REQUIRED SERVICES UP |

**Speaker beat** (note for operator):
> "This is what I look at when I SSH into any of the five Pis. One
> URL — port five-thousand slash fleet — and the whole fleet is in
> front of me. Six peers across two NOCs. Two radio stacks, real-time.
> The deliberate disable on moc3 — that's a Pi-Zero-class box that
> can only host one daemon — shows up as **Connection refused** in
> the panel. That isn't an error to silence; it's the network telling
> me my topology decision is still in effect. **Failures are signal,
> not noise.**"

**The line to land**: "Failures are signal, not noise."

---

## Why this works for a HAM-club room

The NOC screenshot has a HAM-familiar shape — color-coded status,
peer list, time-since-last-seen. Operators who've ever stared at a
repeater controller or a packet BBS recognize the pattern
immediately. It also visually anchors all the architectural slides
that came before (composable bridges, 5-Pi fleet, AI memory) into
"here's the screen that ties it together."

For Reticulum-curious hams: the federation panel showing 20 RNS
announces is concrete proof the network is alive. They've heard
"Reticulum" mentioned in slide 2 / 8; now they see RNS hashes
ticking in real time.

---

## Screenshot capture — operator workflow

**Capture host**: meshanchor-server (this is the box that hosts the
self panel + sees the full peer rollup). Capture from `localhost` so
no LAN IP appears in the URL bar:

```bash
ssh meshanchor-server
# In a graphical session or via SSH tunnel:
#   firefox http://localhost:5000/fleet     # or chromium
# Take the screenshot via the browser's built-in tool (Firefox:
# right-click → Take Screenshot → Save Visible) or the OS screenshot
# binding. Save as a PNG.
```

**Visual cleanup before slide-prep** (MF015 audit):
1. **URL bar** — must read `localhost:5000/fleet` or `127.0.0.1:5000/fleet`,
   never a `192.0.2.x` LAN address. Capturing from `localhost` enforces this.
2. **Peer host:port column** — scrub `192.0.2.38`, `.34`, `.41`,
   `.20`, `.249` in the rendered panel. Two options:
   - **Recommended**: take the screenshot, then in any image editor,
     replace the IP column with `<box1>:5000`, `<box2>:5000`, etc.
     Preserves the layout, hides the LAN.
   - **Alternative**: rename the entries in
     `~/.config/meshanchor/fleet.json` from `192.0.2.x` to
     hostnames (`moc:5000`, `moc1:5000`, etc.) BEFORE taking the
     screenshot. Works if the meshanchor-server can resolve those
     hostnames (verified earlier in session — names didn't resolve
     from .29, so this path needs an `/etc/hosts` addition first).
3. **Browser tab title** — scrub any text containing operator-identifying
   hostnames if visible.
4. **Federation panel display names** — peer node names like
   `meshforge moc1 nomad` are fine (no PII). Names like
   `j1@p@` (raw LXMF identifiers) are fine.

**Run the audit before slide locks 2026-05-16**:
```bash
# Visual check — open the screenshot, search the rendered image for
# any pattern matching 192.168., 10., 172.16-31.
# This is the same audit as MF015 for substack posts.
```

---

## Layout sketch (text mockup for the design pass)

```
┌────────────────────────────────────────────────────────────────────┐
│ — OPERATIONS · LIVE NOC · 192.0.2.X / FLEET                     │
│                                                                    │
│ Fleet status, one URL.                                             │
│ The operator's first SSH on any box. Truth state across six peers. │
│                                                                    │
│ ┌────────────────────────────────────────────────────────────────┐ │
│ │                                                                │ │
│ │             [ NOC SCREENSHOT — full-width crop ]               │ │
│ │     self panel + peer rollup table + federation list           │ │
│ │                                                                │ │
│ └────────────────────────────────────────────────────────────────┘ │
│                                                                    │
│ ┌──────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐                  │
│ │  6   │ │   20     │ │ BW62/SF7   │ │  4 / 4   │                  │
│ │PEERS │ │ RNS 24h  │ │ 910.525 MHz│ │ REQUIRED │                  │
│ └──────┘ └──────────┘ └────────────┘ └──────────┘                  │
└────────────────────────────────────────────────────────────────────┘
```

---

## Implementation notes

- **Pairs with slide 14**: the deck flow runs slide 13 (inventory) →
  this slide (operations) → slide 14 (data layer · 53k nodes). Three
  consecutive slides that move from "what" to "how" to "where it
  surfaces."
- **Visual continuity with slide 14**: slide 14 is a 1440x810 screenshot
  with a sidebar legend. This new slide should sit at a similar zoom
  level so the audience's eye doesn't jump in resolution between them.
- **Don't shrink the screenshot too small**: the federation panel
  rows need to be readable. If the rollup table eats most of the
  width, crop the federation list to top-5 with a "+15 more" indicator
  rather than squeezing all 20.
- **If the deck's color system has a "telemetry green" or "operations
  cyan"**: use that for the header accent, distinct from the slide-14
  data-layer color treatment.
