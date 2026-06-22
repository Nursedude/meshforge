# Upstream App Ownership & Version-Control Policy

> **Status:** ACTIONS 1–5 DONE (2026-06-21). **DONE:** Action 1 (rescue → version control); Action 2 (inherited-app pins documented in `fleet-overlays/PINS.md`); Action 3 (moc5 meshchat → our fork, provenance no-restart); Action 4 (bot tier = T2, fork BUILT, VolcanoAI canary green); **Action 5 (drift-check probe `inherited_app_drift` — BUILT + real-data verified read-only across VolcanoAI/moc5/.32; §9)**. **GATED post-06-24:** the `meshing-around` `.32` deploy, the `meshchat` functional modernization, and Action 5's watchdog-restart + seed-promotion activation. **Remain:** only the low-prio on-box dirty-tree cleanups (which the new probe now surfaces).
> **Why:** Operator: *"we need to own more of this to avoid upstream issues — we do this in several instances."* Triggered by fixing `meshing_around_meshforge` #191 (INI-coercion startup crash) then finding upstream `SpudGunMan/meshing-around` carries the same root cause — and that we run a **mix** of owned forks and unversioned upstream checkouts across the fleet.
> **Companion to:** the RNS/LXMF fork governance (`persistent_issues.md` SSOT + the upstream-dependency-governance memo). This extends that discipline beyond the two protocol forks.

---

## 1. Ground-truth inventory (2026-06-21 fleet survey)

Read-only `git` survey across VolcanoAI + moc/moc1/moc2/moc3/moc5/meshanchor-server + `.32`.

### OWNED (`Nursedude/*`) — version-controlled, we push, healthy
| Repo | Pin | Notes |
|---|---|---|
| meshforge | `main` (60151d8b) | core NOC, on every box |
| meshforge-maps | `main` (f43fd86) | moc/moc1/moc2/.32 |
| meshanchor | `main` | sister project |
| meshing_around_meshforge | `main` (94d8c2c) | the **client/monitor** (NOT the bot) |
| RNS-Management-Tool | `main` | — |
| RNS-Meshtastic-Gateway-Tool | `main` | — |
| reticulum (RNS fork) | tag `1.2.5+mf.5` | T1, pinned, governed |
| lxmf (LXMF fork) | tag `0.9.4+mf.0` | T1, pinned, governed |
| reticulum-meshchat (fork) | `master`/`fix/datetime-…` | owned fork EXISTS |

### INHERITED upstream, run directly — the risk class
| Repo | Upstream | Where | State | Risk |
|---|---|---|---|---|
| **meshing-around** | SpudGunMan | VolcanoAI **v1.9.9.5** + .32 **v1.9.9.8** | floating `main`, **dirty (code)** | ⚠️⚠️ highest |
| **reticulum-meshchat** | liamcottle | **moc5** (v2.3.0, dirty=1) | upstream, while we have a FORK | ⚠️ inconsistent |
| **MeshSense** | Affirmatech | moc5 (dirty=5) | floating `master`, build edits | ⚠️ |
| Raven | kn6plv | moc5 (clean) | AREDN tool, floating `main` | low |
| ucode / usign | jow- / openwrt | moc5 (clean) | OpenWrt build tooling | low |
| raphael-kit | sunfounder | .32 (dirty=1) | HW kit examples, not load-bearing | low |

---

## 2. What the inventory PROVES (the findings)

**R1 — Live unversioned CODE patches on inherited apps (the core problem).**
The "dirty" trees are hand-edited **source**, not config:
- VolcanoAI `/opt/meshing-around`: `mesh_bot.py`, `modules/settings.py`, `pong_bot.py`
- `.32 ~/meshing-around`: `mesh_bot.py`, `modules/settings.py`
- moc5 MeshSense: 5× `package*.json` / `webbluetooth`
- moc5 reticulum-meshchat: `setup.py`

These patches (a) exist in **no repo we control**, (b) **differ box-to-box** (VolcanoAI patched `pong_bot.py`, .32 didn't), and (c) are **one `git pull` from silent deletion**. This is the failure mode the operator named. **These patches are at risk RIGHT NOW.**

**R2 — Floating-`main` version drift.** `meshing-around` is at **v1.9.9.5** on VolcanoAI and **v1.9.9.8** on `.32` — two different upstreams running the "same" app. No pin → every box drifts independently; "works on my box" is unreproducible.

**R3 — Fork-vs-upstream inconsistency.** We maintain a `Nursedude/reticulum-meshchat` fork, yet **moc5 runs upstream `liamcottle/…`**. The fleet isn't even consistent about which copy of an app it runs.

**R4 — The owned forks are the model.** RNS/LXMF are pinned to tags (`+mf.N`), clean trees, governed. That discipline works — the gap is that it stops at the protocol layer.

---

## 3. Proposed tiered ownership policy

| Tier | Definition | Mechanism | Members |
|---|---|---|---|
| **T1 — Own (hard fork)** | Protocol/substrate where a bug is catastrophic or a wire change forks the network | `Nursedude/*` fork, tag-pinned `+mf.N`, FORK.md merge procedure, governance triggers | **RNS, LXMF** (done) |
| **T2 — Own the app (fork)** | Mission-critical app whose *philosophy* we must override, or that we must harden faster than upstream moves | `Nursedude/*` fork, our `main`, CI, deployed fleet-wide from the fork only | meshing_around_meshforge (client), reticulum-meshchat, RNS-Management-Tool. **Candidate: the bot — see §5** |
| **T3 — Pin + monitor + contribute** | Healthy upstream; our needs are generic | Pin to a **chosen SHA/tag** (never floating `main`); patches go **upstream as a PR** or into a **tracked overlay** in a repo we control; auto-check for upstream releases | meshing-around (bot, today), MeshSense, Raven, ucode/usign |

**The decision rule:** *fork (T2) only when we must override upstream's design or upstream is unresponsive; otherwise pin-and-contribute (T3). Forking adds a permanent merge treadmill — don't pay it for a preference.*

---

## 4. Version-control discipline (the rules — this is what kills "upstream issues")

These apply to **every** inherited app, independent of tier:

1. **No floating `main` on anything we run.** Pin to a SHA or tag we chose. Upgrades are deliberate (pin-bump + canary), never a surprise `git pull`.
2. **No unversioned local code patch — ever.** A patch lives in exactly one of: (a) a `Nursedude/*` fork, (b) an upstream PR, or (c) a tracked patch-set/overlay in a repo we own. A `dirty` working tree on an inherited checkout is a **defect to remediate**, not a deployment state.
3. **Config ≠ code.** Per-deployment config (`config.ini`, keys, IPs) is gitignored / kept outside the source repo, so a clean `git status` means "no unversioned code." (Today config + code patches are mixed in the same dirty tree — that ambiguity hides R1.)
4. **One source of truth per app across the fleet.** If we fork it, *every* box runs the fork (fixes R3).
5. **A fleet check that fails on drift.** Extend `parity_check.py` / a watchdog probe to flag: any inherited repo on floating `main`, any unversioned code patch, any cross-box version mismatch. (Mirrors how `rns_version_check.py` gates the `+mf.N` pin.)

---

## 5. The `meshing-around` bot case (operator: DOCUMENT ONLY for now)

- **Same root cause as #191, but a different *failure philosophy*.** Upstream wraps all config reads in one `try:` → `except Exception → print "check config.ini against template, Exiting" → exit(1)`. So a bad INI value **fails closed gracefully** (clean exit + guidance), NOT the fork's pre-#191 traceback. Our #191 chose **fail open** (per-field fallback, keep the radio up) — arguably better for emergency comms, but a genuine design choice, not a bug upstream "got wrong." **No code action taken (operator decision 2026-06-21).**
- **The fork can't simply replace it.** `meshing_around_meshforge` has **no `mesh_bot.py`/`launch.sh`** — it's the client/monitor + maps-writer. Upstream `mesh_bot` (command bot) and our `mesh-client` are **complementary**. "Own the bot" (T2) = porting the whole large SpudGunMan bot into the fork (big surface: armv6 cryptography divergence, command modules, etc.). **Deferred decision.**

---

## 6. Recommended actions (prioritized)

1. **✅ DONE 2026-06-21 — patches RESCUED into version control.** Captured to `~/fleet_overlays_rescue_2026_06_21/` (read-only `git diff`), then committed to **`Nursedude/fleet-overlays`** (PRIVATE, `main`, commit `cb3a37a`; decision A). Verified on remote: 9 files, all 4 per-box patches + per-app READMEs documenting base SHAs / the load-bearing union / apply procedure. The `.32` + VolcanoAI bot patches (the gateway-arc bridge layer) are no longer one `git pull` from deletion. Local clone at `~/fleet-overlays`. Originals still untouched in the live trees (rescue was read-only).
2. **✅ DONE 2026-06-21 (documented pins) — `fleet-overlays/PINS.md`.** Read-only fleet survey recorded the chosen SHA for every remaining inherited app: MeshSense/Raven/ucode/usign (moc5) + raphael-kit (`.32`). The load-bearing inherited apps are already fork-managed (bot = Action 4, meshchat = Action 3), so the pin universe is just **low-value moc5 tooling + a HW-kit example** — almost all "dirty" flags are untracked config/build artifacts, not code. moc/moc1/moc2/moc3 have **no** inherited apps. **Enforcement = the ledger + the Action-5 drift check, NOT detached HEAD** (which is fragile). Surfaced two R1 cleanups (deliberate, low-prio): `meshing_around_meshforge`'s `config.enhanced.ini` drift on `.32` (our *own* fork) + raphael-kit's example edit. On-box dirty-tree cleanup is the only residual, and it's not urgent.
3. **✅ DONE 2026-06-21 (provenance) — R3 reconciled on moc5.** Repointed moc5's `~/reticulum-meshchat` origin `liamcottle` → **`Nursedude/reticulum-meshchat`** (upstream link preserved), ff'd to fork `master` `6ae50f1` (the fork's only delta is a docs-only `CLAUDE.md`). **No restart** — meshchat pid 1086 unchanged, `rnstatus OK`, zero soak impact. Decision C satisfied. ⚠️ The fork is itself **9 behind upstream** and its datetime fix (`2f519d9e`) is on a side branch, not `master` — so making moc5 *current+fixed* is a **functional** modernization (fork-sync + datetime fix + meshchat restart = the #69 risk the provenance step avoided), gated post-06-24 in `~/deferred_work.json` (`meshchat-fork-modernization`). moc5 was already at this old base pre-convergence, so no regression.
4. **✅ DECIDED + BUILT 2026-06-21 — bot tier = T2 (real fork).** Operator chose T2 (§7B). **Phase A (build) DONE:** forked `SpudGunMan/meshing-around` → **`Nursedude/meshing-around`** (public); `meshforge` branch based on `fde22f75ea` (v1.9.9.8, the newer running version) carries the reconciled **union** (commit `22915ce`: `.32`'s dual-bridge dedup + ACK/NAK + tag-strip applied at its native base = byte-faithful; + VolcanoAI's `ignoreDMs` + `antiSpam`-config-overridable, which supersedes `.32`'s hardcode) + `FORK.md` (`a15ca63e`). `python3 -m py_compile` clean on all 3 files (BELIEVED-correct; runtime-proven at deploy). `main` mirrors upstream. **Phase B (deploy): ✅ VolcanoAI canary GREEN 2026-06-21** — switched `/opt/meshing-around` onto the fork (`meshforge@a15ca63`); `venv` `py_compile` rc=0 (Python 3.13.5) + `import modules.settings` resolves `antiSpam=False`/`ignoreDMs=True` config-driven = the fork VERIFIED-loads under the real runtime and the FORK.md deploy steps are proven on a real box (no bot runs on the dev box → VolcanoAI now converged onto the fork). **`.32` production deploy remains** (operator-timing; live radio runtime proven there); tracked in `~/deferred_work.json` (`meshing-around-fork-deploy`).
5. **✅ DONE 2026-06-21 — the drift check** (rule §4.5). `probe_inherited_app_drift` built, wired, seeded, tested, and REAL-DATA VERIFIED read-only across VolcanoAI/moc5/.32 (caught + fixed a MeshSense submodule false-positive in the process). Local detection, not PINS.md-coupled. Activation gated post-06-24 (mf.5 soak). Full record in §9.

---

## 7. Decisions — operator-approved 2026-06-21
- **A. ✅ BUILT `Nursedude/fleet-overlays`** (PRIVATE, commit `cb3a37a`, 2026-06-21) — a thin repo of tracked per-app patch-sets (lighter than forking each app). All four rescued patches landed with per-app READMEs (base SHAs, the load-bearing union, apply procedure). Local clone `~/fleet-overlays`.
- **B. ✅ RESOLVED → T2 (real fork), operator-chosen 2026-06-21.** Initially approved T3, but §8 showed the bot is **already a de-facto fork** (4 load-bearing unversioned features on `.32`, a different set on VolcanoAI, two diverged versions) — a T3 overlay carried most of that reconciliation pain without the clean-tree/CI/single-source benefits. Operator chose **T2**. **Built (Phase A):** `Nursedude/meshing-around` `meshforge` branch (see §6.4 + the fork's `FORK.md`). Deploy (Phase B) gated.
- **C. ✅ EXECUTED 2026-06-21 (provenance)** — moc5 converged off upstream `liamcottle` onto our `Nursedude/reticulum-meshchat` fork, soak-safe (no restart). See §6.3. Functional modernization (fork-sync + datetime fix + restart) gated post-06-24. (`fleet-wide`: moc5 was the identified off-fork box; a fleet `pgrep meshchat` sweep is folded into the modernization task to catch any straggler.)
- **D. ✅ SHA-based pins for T3** (we don't control upstream's tags); reserve `+mf.N` tags for T1/T2 owned forks.

## 8. Rescued-patch catalog (captured 2026-06-21 → `~/fleet_overlays_rescue_2026_06_21/`)

**`.32` bot (`~/meshing-around`, base `fde22f7`) — +70/-1, ALL load-bearing (gateway-arc):**
- `_meshforge_reply_is_dup()` + dual-bridge **reply dedup** — both bridge paths (`[MC:..]`/`[ch0:..]`) kept for redundancy (~55% combined delivery, measured 2026-05-26), reply once. Env `MESHFORGE_REPLY_DEDUP_S` (default 30s).
- **Delivery ACK/NAK logging** (2026-06-19) — logs `ROUTING_APP` outcome for the bot's own `wantAck` replies (observability only, no flow change).
- **Bridge routing-tag stripping** — strips `[RNS:..]`/`[Mesh:..]`/`[chN:..]` so bridged commands parse. *Without this the bridge doesn't work.*
- `antiSpam = False` (hardcoded in settings.py).

**VolcanoAI dev (`/opt/meshing-around`, base `530d784`) — +9/-1, DIVERGED from .32:**
- `ignoreDMs` feature (mesh_bot.py + pong_bot.py + settings.py) + `antiSpam` made **config-overridable** (the clean approach vs .32's hardcode).

**⚠️ The two boxes' patch-sets are DIFFERENT.** The canonical overlay must be the **UNION** (dual-bridge dedup + ACK + tag-strip + ignoreDMs + antiSpam-config-overridable), with **both boxes converged onto it**. Note version drift too: .32 base `fde22f7` (v1.9.9.8) vs VolcanoAI `530d784` (v1.9.9.5).

**Non-substantive (no rescue needed):** moc5 MeshSense = npm-regenerated `package*.json` churn (review the one `package.json` for intent); moc5 reticulum-meshchat = `setup.py` 0-line/whitespace no-op.

---

## 9. Action 5 — drift-check probe ✅ BUILT + REAL-DATA VERIFIED 2026-06-21

**DONE.** `probe_inherited_app_drift` (signal class `inherited_app_drift`)
shipped: closed-enum entry + documented-set test, `run_all_probes` wiring,
BOTH role seeds (`inherited_app_drift_any`, propose_escalation/no-ntfy) + the
seed-coverage + reachability gates green, honest-failure-modes pass, `degraded`
only, 2-tick debounce, INERT off-box. **14 probe tests + the full local suite
(501) + lint exit 0 + CI `success` (commit `48adab26`).** Lives in
`src/utils/watchdog_probes_drift.py`.

**Design decision settled = LOCAL problem-class detection** (the lean): scans
the top level of the operator home + `/opt`, reads `.git/config` to classify
owned-vs-inherited (NO `PINS.md` coupling — honest_failure_modes #5; PINS.md
isn't even on moc5), runs `git status --porcelain --untracked-files=no
--ignore-submodules=all`, then filters machine-generated manifests/lockfiles.
The **floating-`main`/pin-drift leg is deliberately NOT a local fire** — the
fleet enforces pins by ledger + never-auto-pull (PINS.md), NOT detached HEAD,
so firing on "on a branch" would contradict the policy and false-page every
intentionally-pinned moc5 app. That leg is a future ledger/cross-box check.

**REAL-DATA VERIFIED (read-only, 2026-06-21) — the #78 synthetic-vs-real
discipline, and it paid off:**
- **VolcanoAI**: all 13 top-level checkouts are `Nursedude/*` (incl. the
  converged `/opt/meshing-around`) → owned → **INERT** ✓ (full probe run).
- **moc5**: MeshSense / Raven / ucode-src / usign-src classified INHERITED;
  Raven/ucode/usign clean; MeshSense's churn is `package*.json` (benign) **plus
  `api/webbluetooth` — a git SUBMODULE** (`160000`/`S.M.`). A clean-package.json
  fixture MISSED this; the real tree caught it. Fix: added `--ignore-submodules=all`
  (a submodule's churn is a dependency state, not the parent app's source).
  Post-fix MeshSense → `_real_code_patches=[]` → **INERT** ✓ (must-not-false-fire,
  honored). Pinned by a flag-presence regression test.
- **.32** (`wh6gxzTRDEV`): fires `degraded` on the 2 expected true-positives —
  `meshing-around` (`mesh_bot.py`,`modules/settings.py`; gated deploy) +
  `raphael-kit` (`python/1.1.7_Lcd1602.py`) ✓.

**Activation GATED** (watchdog restart + `promote_seed_rules.py --apply`) — same
pattern as the resource-canary (A1); the code ships INERT (probe-activation
runbook). Recorded in `deferred_work.json` (post-06-24, after the mf.5 RNS soak).
`rules_seed_drift` is the designed convergence nudge until the seed is promoted.

**What it enforces (policy §4):** for each INHERITED (non-`Nursedude`-origin)
git checkout on a box —
1. **No floating `main`** — flag an inherited checkout that tracks `origin/main`
   un-pinned (the R2 bleed).
2. **No unversioned code patch** — flag **tracked-file** modifications. Untracked
   config/build artifacts (Raven's `raven.conf`, ucode's `build/`) are OK;
   tracked-code edits are the R1 defect.
3. *(stretch)* cross-box version mismatch for the same app.

**Key design decision to settle first:** detect the problem-CLASSES *locally*
per box (cleaner; nothing to deploy/drift) vs. compare each checkout against the
`PINS.md` SHA (needs the ledger on the box). **Lean: local problem-class
detection** — the probe enforces the invariants; `PINS.md` stays the human
record. (honest-failure-modes: avoid two-consumers-of-one-constant drift.)

**Scope/expectations:** moc5 is the only box with inherited apps today (where it
will fire); moc1/2/3 = none (INERT). Known-benign, must NOT false-fire:
MeshSense npm churn, Raven/ucode untracked artifacts. Decide whether the probe
should surface the 2 R1 findings (`meshing_around_meshforge` config drift,
raphael example edit) or they get cleaned up first. ⚠️ Calibration: tests green
= BELIEVED; **run it against real fleet state (read-only) before "VERIFIED"** —
the #78 synthetic-vs-real lesson.
