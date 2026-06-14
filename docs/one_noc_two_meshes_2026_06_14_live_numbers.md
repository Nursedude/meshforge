# "One NOC, Two Meshes" — live numbers

> **For Claude Design**: these are the facts the slides display. The presenter improvises the
> narrative and falls back on the slides for the numbers — so every figure here must be **verified**,
> not guessed. Pull this file (raw URL below) before the deck is locked to refresh stateful slides.
>
> **Raw URL** (after commit): `https://raw.githubusercontent.com/Nursedude/meshforge/main/docs/one_noc_two_meshes_2026_06_14_live_numbers.md`
>
> **Refresh pattern**: `scripts/render_birc_numbers.py` already regenerates the BIRC numbers file from
> live repo state. To make THIS file self-refreshing, generalize that script (or copy it) to emit the
> table below. For a quick one-off talk, hand-stamping (as below) + a version-stamp footer is fine.
>
> **Verified**: 2026-06-14 (version & test counts read live from the repo).

## Verified facts (stamp these onto the slides)

| Fact | Value | Goes on slide |
|------|-------|---------------|
| MeshForge version | `0.6.2-beta` (released 2026-06-12) | 11 (open source), 13 footer |
| MeshForge test files | **200** | 11 |
| MeshForge test functions | **6,079** (cite as "~6,000") | 11 |
| MeshAnchor version (sister) | `0.1.0-alpha` | 11 |
| Deployment profiles | `gateway` · `monitor` · `radio_maps` · `meshcore` · `full` | 8 |
| Dude-claw radios | WiFi + LoRa (RX+TX) + BLE passive-scan, on one ESP32 | 9 |
| Launch — NOC | `sudo python3 src/launcher_tui/main.py` | 12 |
| Launch — RF tools | `python3 src/standalone.py` (zero dependencies) | 12 |

## The growth story (optional, but it lands)

At the BIRC talk (2026-05-17) MeshForge was `0.6.0-beta` with **134 test files / 3,830 tests**.
Four weeks later: **200 files / 6,079 tests** — the test count nearly doubled. If you want a single
"this is actively field-hardened" number, that delta is it.

## Numbers deliberately NOT on slides

- **Live map node counts** (directory totals, by-network breakdown). These are box-specific and drift
  daily; they read great live in a demo but go stale on a static slide. If you want them, pull fresh
  from the federator's `/api/status` the morning of the talk and stamp slide 6 — otherwise let the
  live demo carry that number, not a slide.
- **Fleet box names / count / IPs** — intentionally off-slide (MF015 / portability). "A field-deployed
  fleet of Raspberry Pis" is the safe phrasing.

## Lock & version-stamp pattern

Footer on title + closing slides (and optionally every slide):

```
MeshForge 0.6.2-beta · rendered 2026-06-14 · github.com/Nursedude/meshforge
```

Any drift between deck and reality after lock is then *intentional* — the deck is a snapshot; live
state always lives at `github.com/Nursedude/meshforge`.
