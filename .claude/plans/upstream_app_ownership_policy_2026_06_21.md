# Upstream App Ownership & Version-Control Policy

> **Status:** IN PROGRESS (2026-06-21) — inventory complete, policy + decisions approved, **Action 1 (rescue → version control) DONE**. Actions 2–5 remain.
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
2. **Pin every inherited app to its current SHA** (T3 step 1) — stops the floating-`main` bleed immediately, zero behavior change. Start with `.32 ~/meshing-around` (the running bot).
3. **Reconcile R3:** decide reticulum-meshchat = our fork everywhere, and converge moc5 onto it.
4. **Decide the bot's tier** (T2 fork vs T3 pin+contribute) — §5, separate session.
5. **Add the drift check** (rule §4.5) once the trees are clean.

---

## 7. Decisions — operator-approved 2026-06-21
- **A. ✅ BUILT `Nursedude/fleet-overlays`** (PRIVATE, commit `cb3a37a`, 2026-06-21) — a thin repo of tracked per-app patch-sets (lighter than forking each app). All four rescued patches landed with per-app READMEs (base SHAs, the load-bearing union, apply procedure). Local clone `~/fleet-overlays`.
- **B. ⚠️ REVISIT** — was approved **T3** (pin+contribute, don't fork the bot). BUT §8 then showed the bot is **already a de-facto fork**: 4 load-bearing unversioned MeshForge features on `.32` (dual-bridge reply-dedup, delivery ACK/NAK logging, bridge tag-stripping, ignoreDMs/antiSpam). A T3 "overlay" for the bot is non-trivial and must be reconciled across two diverged boxes — so **T2 (real fork) deserves a second look**. Flagged for the operator; not unilaterally changed.
- **C. ✅ Yes** — standardize reticulum-meshchat on our `Nursedude` fork fleet-wide; converge moc5 off upstream `liamcottle`.
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
