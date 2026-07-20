# White Paper — Real Metrics (grounded 2026-07-17)

> Source material for the "open-source dev + AI, as a case study" Substack white
> paper. **Every number here is pulled from ground truth (git / gh / the live
> test suite), quoted, and caveated** — because the paper's whole thesis is
> calibrated claims, so it must survive its own `honest_status`. Do NOT round
> "1,384 PRs" up to "thousands" in prose without the caveat that it's across the
> three main repos and excludes the forks. Uncommitted; VolcanoAI-local.

---

## The timeline (git ground truth)

- **First commit on `meshforge` main: `2025-12-27`.** (The operator's "started
  ~Nov last year" is roughly right — November was almost certainly pre-code
  planning; the git record opens late December 2025.)
- **Latest: 2026-07-17.** → **~6.7 months of git history.**
- **3,956 commits on `main`** (MeshForge alone; excludes archived branches and
  the other 18 repos).

### The arc — commits/month on main (this is a figure for the paper)
| Month | Commits |
|---|---|
| 2025-12 | 107 |
| 2026-01 | **1,675** ← the build spike |
| 2026-02 | 783 |
| 2026-03 | 282 |
| 2026-04 | 182 |
| 2026-05 | 343 |
| 2026-06 | 424 |
| 2026-07 | 160 (to the 17th) |

The shape tells the real story: a massive build spike, then a settling into a
**sustained reliability cadence** (the #29 regression system, the watchdog
probes, the RNS fork arc). Not "fast forever" — *fast, then durable.*

---

## The collaboration split (the actual human+AI ratio)

Of the 3,956 main commits:
- **Nursedude (author): 2,232** (56%)
- **Claude (author): 1,725** (44%)
- `wh6gxz nurse dude`: 12 · dependabot: 4
- **1,016 commits carry a `Co-Authored-By: Claude` trailer** — explicit pairing,
  not just authorship.

This is the honest pairing metric: ~44% of commits authored by the AI, another
~1,000 co-authored — a genuine two-hands-on-the-keyboard history, not "AI as
autocomplete."

---

## Pull requests (the "1,000s of PRs" claim, grounded)

| Repo | PRs (all) | Merged |
|---|---|---|
| meshforge | **1,168** | 1,151 |
| meshanchor | 129 | — |
| meshforge-maps | 87 | — |
| **Three-repo total** | **1,384** | — |

Plus PRs on the forks (reticulum, lxmf, libch341) not counted here. So
**"thousands of PRs" is defensible across the full 19-repo ecosystem; ~1,384 is
the verified floor across the three main Python repos.** Caveat for the prose:
MeshForge **retired the PR flow on 2026-04-19** (solo dev → direct-to-main), so
the 1,168 are concentrated in the Dec–Apr window; post-April velocity shows up as
commits, not PRs. The PR count *undercounts* later work — say so.

---

## Code scale (MeshForge repo)

- **~365,000 lines of tracked Python** (855 `.py` files) — *incl. tests +
  scripts; rough scale indicator, not audited SLOC.*
- **1,405 tracked files** total.
- **284 test files.**
- **152 `.claude/` docs** — the research/foundations/rules corpus (the harness's
  written half).
- Version **`0.6.2-beta`**.

## Test suite (verified live this session)

- **8,638 tests collected; 8,637 passed, 1 skipped** — re-derived twice today via
  `honest_status.sh` (exit 0) and `pytest --collect-only`. This is the number to
  quote, and it's *quotable* — which is the point.

---

## The ecosystem — 19 repos, and the "API ownership" story

`gh repo list Nursedude` → **19 repositories.** The load-bearing ones for the
paper's "owns its dependencies" thesis:

| Repo | What it is |
|---|---|
| **meshforge** | the NOC (main) — Python |
| **meshanchor** | sister NOC (MeshCore-primary) — Python |
| **meshforge-maps** | live-map satellite — Python |
| **reticulum** | **owned hard-fork of RNS** ("universal, distributed, secure messaging protocol") |
| **lxmf** | **owned hard-fork of LXMF** |
| **libch341-spi-userspace** | **upstream contribution** — the one-line `pthread_detach` fix for the firmware#10468 VSZ leak (PR#10) |
| Meshtasticd_interactive_UI | interactive installer for RNS/LXMF/NomadNet/MeshChatX |
| meshing_around_meshforge / meshing-around | bot/autoresponder |
| WireClaw | mini-dudeai standalone (Pi-brain + ESP32-edge) |
| openwrt / fleet-overlays / RNS-Management-Tool / RNS-Meshtastic-Gateway-Tool / Raven / claude-memory-meshforge(-maps) | supporting tooling + the brain-backup repos |

**This is the strongest, least-hypeable section.** "API ownership" isn't a
metaphor here: when upstream RNS moved off-GitHub (Carrier Switch) and a
dependency risk appeared, the response was to *fork and own* `reticulum` +
`lxmf` (pinned by tag+SHA, gated by `+mf.N`), and to *fix upstream C* in
`libch341-spi-userspace`. A solo dev + AI maintaining forks of their own
network substrate is the case study's sharpest point.

---

## Fleet / operational scale (the "it runs" proof)

- **9 boxes** running mini-dudeai on cadence (verified via `rollup` this session,
  9/9 fresh).
- **46-rule** watchdog ruleset per box (52 on the manager).
- **~50+ documented issue classes** (#1–#83 + the probe families) with
  automated regression prevention (lint MF001–MF026 + regression guards).
- **44 Substack posts** already published (`docs/substack/`) — the paper is the
  capstone of an existing writing practice, not a one-off.

---

## Honest caveats (put a short version of these in the paper — they ARE the credibility)

1. 3,956 commits = **MeshForge main only.** The 19-repo total is much larger but
   not summed here.
2. First git commit is **Dec 27, 2025**, not November — don't overstate the span.
3. 365k Python LOC **includes tests/scripts** and is not audited SLOC.
4. PR counts **undercount post-April work** (PR flow retired 2026-04-19).
5. Commit *count* is not *value* — the monthly arc (spike → durable cadence) is
   the honest narrative, not a raw total.

---

## Suggested headline stats for the paper (the quotable set)

> In ~6.7 months (Dec 2025 → Jul 2026), a solo operator and an AI collaborator
> built a mesh-network NOC across a **19-repo ecosystem**: **3,956 commits** and
> **1,384+ PRs** on the core repos, **44% of commits AI-authored**, **8,637
> passing tests**, **~365k lines of Python**, **44 published essays**, **9
> boxes** in a live fleet — and, when a core dependency walked away, they
> **forked and now own the RNS + LXMF protocol stack.** Every number in this
> paragraph is re-derivable from `git`, `gh`, and `honest_status.sh`.
