# BIRC 2026-05-17 — live numbers

> **For Claude.ai design**: this file is regenerated from live repo state by `scripts/render_birc_numbers.py`. Pull this raw URL before lock to refresh the deck's stateful slides.
>
> **Raw URL**: `https://raw.githubusercontent.com/Nursedude/meshforge/main/docs/birc_2026_05_17_live_numbers.md`
>
> **Generated**: 2026-05-16 20:46 UTC

## Ecosystem versions + test counts

| Repo | Version | Test files | Test functions | Notes |
|------|---------|-----------:|---------------:|-------|
| `meshforge` | `0.6.0-beta` | 134 | 3,830 |  |
| `meshforge-maps` | `0.7.4-beta` | 45 | 1,117 |  |
| `meshing_around_meshforge` | `0.6.0` | 20 | 961 |  |
| `meshanchor` | `0.1.0-alpha` | 144 | 3,891 |  |
| `RNS-Management-Tool` | `—` | 0 | 0 |  |
| **Total** | — | **343** | **9,799** | across ecosystem |

## Live map state — `localhost:5000`

- Directory nodes total: **83,501**
  - with position: 81,440
  - without position: 2,061
- By network: aredn: 2,785 · meshcore: 51,006 · meshtastic: 27,841 · rns: 1,869
- Retention: 2.0 d (local) · 7 d (external)

> **Note on `/api/status.history`**: `total_observations` is `MAX(rowid)` on `node_observations` — a lifetime insert high-water mark, NOT a current-window count. Pruning shrinks `COUNT(*)` but not rowid. `unique_nodes` there is the directory size, not distinct observers. For an audience-facing "live mesh" number, cite the directory total above; for a real activity number, query `node_observations` directly (~hundreds of active observers, ~30k rows in a 2-day window on the publishing box).

## Slide-by-slide refresh hints

These are the slides whose numbers should be replaced from the values above before the deck is locked on 2026-05-16.

- **Slide 4 / footer** — `MeshForge — Nursedude / WH6GXZ` title — version stamp.
- **Slide 10** — STATUS · vX.Y.Z-BETA → use `meshforge` version. Test count → use **MeshForge test function total**, not ecosystem-wide total.
- **Slide 13** — Field Validation — `3,160 tests · 90 files` → replace with MeshForge file + function counts. `vX.Y.Z-β` → MeshForge version.
- **Slide 14** — `meshforge-maps vX.Y.Z-b · NN,NNN nodes` → meshforge-maps version + map directory total.
- **Slide 16** — `~3,000 TESTS` → MeshForge test function total (round to nearest 100).
- **Slide 18** — repo grid version stamps → use the table above.
- **Slide 22** — `5,619 TESTS Across all five repos` → ecosystem total from the table above.

## Lock & version-stamp pattern

Add to footer of every slide (or just title + closing slides):

```
MeshForge 0.6.0-beta · data window 2026-04-28 → 2026-05-04 · rendered 2026-05-16
```

Any drift between deck and reality after lock becomes intentional — the deck is a snapshot, the live state is always at `github.com/Nursedude/meshforge`.
