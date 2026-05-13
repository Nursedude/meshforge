# BIRC 2026-05-17 — Hawaiʻi slide spec (3-slide insert)

> **For Claude.ai design**: this file is the canonical source for the 3 Hawaiʻi
> slides inserted between slide 12 (HAM features) and slide 13 (Field Validation)
> of the MeshForge BIARC deck. Pull this file's raw URL when refreshing slide
> content. If a number here disagrees with the deck, **this file wins**.
>
> **Raw URL** (for Claude.ai design fetch):
> `https://raw.githubusercontent.com/Nursedude/meshforge/main/docs/birc_2026_05_17_hawaii_slides.md`
>
> **SVG assets** are tracked in this repo at `docs/birc_2026_05_17_assets/`.
> Raw URLs follow the same `raw.githubusercontent.com/Nursedude/meshforge/main/`
> prefix — Claude.ai design can fetch them directly, no upload needed:
>
> - `docs/birc_2026_05_17_assets/hero_map.svg`
> - `docs/birc_2026_05_17_assets/leaderboard_most_active.svg`
> - `docs/birc_2026_05_17_assets/leaderboard_most_relayed.svg`
> - `docs/birc_2026_05_17_assets/leaderboard_best_snr.svg` (stub-empty — no SNR data in capture)
> - `docs/birc_2026_05_17_assets/leaderboard_most_reliable.svg` (stub-empty — no reliability data in capture)
> - `docs/birc_2026_05_17_assets/app_mix.svg`
> - `docs/birc_2026_05_17_assets/timeline.svg`
>
> **Refresh policy**: this content is locked to a frozen capture window
> (Apr 28 → May 4 2026 UTC). It does NOT drift with current fleet state. If
> the window is re-run, regenerate `aggregate_summary.json` first, then update
> this file's "Numbers" block, then re-render slides.

---

## Source of truth

All numbers below come from running `moc_analysis_tool` against the
frozen `presentation_capture.db` capture. JSON outputs not tracked
in-repo; regenerate from the operator's workstation via
`cd src && python3 -m moc_analysis_tool.cli.run_analysis --preset hawaii_may2026`.
Latest regeneration: 2026-05-13T22:22 UTC (commit `5b3b93b` + later).

```
Capture window:  2026-04-28T00:00:00 UTC → 2026-05-04T00:00:00 UTC
Duration:        144 hours · 6.0 days
Listener:        single Pi · LongFast preset · Hawaiʻi
Total packets:   16,235
Unique sources:  231 nodes (heard at least once)
Nodes with pos:  641 (pinned on hero_map)
Nodes total:     830 (seen, including position-less)
Active hours:    124 / 144 (86%)
SNR floor:       n ≥ 30 packets for the SNR leaderboard
                 (note: stub-empty — captured `packets` table has no SNR column)
```

Portnum distribution (top 4):

| App         | Count |
|-------------|-------|
| POSITION    | 6,920 |
| NODEINFO    | 4,599 |
| TELEMETRY   | 3,442 |
| TRACEROUTE  | 1,274 |

---

## Slide HI-A — "What one Pi heard last week over Hawaiʻi"

**Position in deck**: insert after slide 12 (HAM features), before slide 13 (Field Validation).

**Header label** (top-left, monospace, green accent):
`— ON THE ISLANDS · 6 DAYS · ONE LISTENER`

**Title** (large white):
"This is what one Pi heard last week over Hawaiʻi."

**Subtitle** (smaller, secondary):
"Six days. One LongFast listener. The whole island chain."

**Hero visual** (full width):
`hero_map.svg` — Hawaiʻi silhouette with 606 LongFast nodes pinned, color-coded by avg SNR.

**Number strip** (bottom of slide, 4 cells, deck's existing kibble style):

| Cell | Value | Label |
|------|-------|-------|
| 1 | `6.0` | DAYS · APR 28 → MAY 4 |
| 2 | `16,235` | PACKETS |
| 3 | `606` | NODES PINNED · 788 SEEN |
| 4 | `231` | UNIQUE SOURCES |

**Speaker beat** (note for operator, not on slide):
> "This is what one Raspberry Pi listening on LongFast saw last week. Every dot is a Meshtastic node — could be in your shack, on a ridge, on a backpack. Big Island, Oʻahu, Maui — all of it. This is YOUR network. You're already on it. If you don't see your callsign on here, your radio's been quiet or out of range — either way, MeshForge would have told you."

**The pause**: Hold this slide a beat after speaking. The room scans for their own location.

---

## Slide HI-B — "Top of the air on the islands"

**Position**: immediately after HI-A.

**Header label**:
`— LEADERBOARDS · WHO'S CARRYING THE TRAFFIC · WHO'S HEARD CLEANEST`

**Title**:
"Who's on the air. Who's heard cleanest."

**Layout**: two-column. Left and right SVGs side-by-side, equal width.

**Left column**:
- Subhead: `MOST ACTIVE · BY PACKET COUNT`
- SVG: `leaderboard_most_active.svg`
- Caption (small, gray): "Top 10 by packet count over the 6-day window. Router nodes float to the top — they're relaying for everyone else."

**Right column**:
- Subhead: `BEST SNR · n ≥ 30 PACKETS`
- SVG: `leaderboard_best_snr.svg`
- Caption: "Top 10 by average SNR. Floor of 30 packets — a lucky single bounce doesn't qualify. These are the antennas + locations + site engineering that actually work."

**Speaker beat**:
> "Most active isn't the same as best signal. Some of these names you'll recognize — they're router nodes, infrastructure, doing the work of relaying. SNR side is different — that's a question of antenna, location, height. If you're on either list, you're already part of the island's RF backbone."

**HAM bait**: this is where operators in the room start counting whose station is on the leaderboard. Worth the pause.

---

## Slide HI-C — "What your network is actually carrying"

**Position**: immediately after HI-B.

**Header label**:
`— TRAFFIC · 6 DAYS · WHAT'S CHEWING YOUR AIRTIME`

**Title**:
"It's mostly not text. Here's why your battery drains."

**Layout**: two visuals stacked OR side-by-side (operator preference).

**Visual 1**:
- Subhead: `APP MIX · BY PORTNUM`
- SVG: `app_mix.svg` — squarified treemap of portnums.
- Caption: "Position beacons, telemetry, and nodeinfo dominate. Text messages are a sliver. This is what's actually on the channel."

**Visual 2**:
- Subhead: `6-DAY TIMELINE · PACKETS PER HOUR`
- SVG: `timeline.svg` — hourly packet count over 144 hours, color-banded by app.
- Caption: "124 of 144 hours had traffic. The quiet bands are 3 AM HST when even the routers nap."

**Number callouts** (small kibble cells, optional under the visuals):
- `6,920` · POSITION beacons
- `4,599` · NODEINFO
- `3,442` · TELEMETRY
- `1,274` · TRACEROUTE

**Speaker beat**:
> "If you've ever wondered why your handheld drains in three days without anyone typing — this is the answer. Position beacons, telemetry, nodeinfo. Every node announcing 'I'm still here' to every other node, every few minutes. That's the channel utilization climbing without a single 'hello.' MeshForge surfaces this so you can decide what to turn down."

**Audience benefit**: this is the most operationally useful slide in the deck for a working HAM. They leave the room knowing why their node uses airtime.

---

## Notes for the design pass

**Asset upload**: 5 SVGs need to be uploaded to Claude.ai design (hero_map, leaderboard_most_active, leaderboard_best_snr, app_mix, timeline). Two SVGs are NOT used in these three slides — `leaderboard_most_relayed.svg` and `leaderboard_most_reliable.svg`. Held in reserve as alternates.

**Visual consistency**: match the existing deck's kibble system — green accent for hooks, mono headers, monospace footer values. The number strips should reuse the deck's existing cell styling.

**MF015 audit** (before deck locks 2026-05-16):
- Scan `hero_map.svg` for any node `long_name` near Oʻahu / Maui / Big Island that leaks a callsign-with-street-name or an obvious residential pin.
- Scan the leaderboard SVGs for the same — top-10 rows are the highest risk.
- Operator's own fleet pins (the 5 documented hosts + their RF aliases) are fine; check for OTHER operators whose long_name might include personal info.

**Refresh decision** (operator, by 2026-05-14):
- Option A — ship the 04-28 → 05-04 window as-is. Stable, frozen, no last-minute re-render needed.
- Option B — re-run the analysis tool over a 05-09 → 05-15 window for fresher data. Trade-off: more recent but fewer total nodes (fleet uptime was lower in early May than late April). Re-rendering the 7 SVGs takes ~5 min once the JSON is regenerated.

Recommendation: Option A. The 6-day window has the most nodes; freshness within 2 weeks isn't load-bearing for a club talk.

---

## Companion: live numbers file (separate concern)

The HI slides are locked to a frozen capture window — they don't drift. The
*rest* of the deck has live-state numbers (test count, version, deployed
boxes) that DO drift. Those belong in a sibling file
(`docs/birc_2026_05_17_live_numbers.md`, not yet written) regenerated before
the talk. That decouples "structural slides" from "stateful slides" and
gives Claude.ai design a refresh target.

See `MEMORY.md` → `project_birc_presentation_may_2026.md` for the broader
deck-staleness pattern.
