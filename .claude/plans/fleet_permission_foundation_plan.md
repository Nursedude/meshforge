# Fleet Permission Foundation — architecture planning doc

> **Status:** planning input, 2026-06-01. Drafted to frame the architecture
> meeting that follows the RNS mf.4 arc. Surfaced by the mf.4 logfile-perms
> trigger ([[project_rns_mf4_sigterm_investigation_2026_05_31]]); inputs captured
> in [[project_fleet_perms_foundation_arch_inputs_2026_06_01]].
> **Decides:** the canonical service-user / permission model, who *establishes*
> it (so a fresh box is born correct), and how the root boxes converge to it.
> **Relates to:** `provisioner_scope.md`, `fleet_etc_reconcile_design.md`,
> `standalone_wireclaw_variant.md`, `docs/fleet_roles.yaml`, `install_noc.sh`.

---

## ✅ DECISIONS RATIFIED — meeting 2026-06-01 (WH6GXZ + Dude AI)

| # | Decision | Outcome |
|---|----------|---------|
| **D2** | Gateway hardware access | **Non-issue (evidence).** Serial devices are `root:dialout` (even 666); operator user `wh6gxz` is already in `dialout`/`plugdev`/`gpio`/`i2c`/`spi`; **no service binds a privileged port**. Non-root opens the hardware with zero extra grants. |
| **D1** | Canonical service-user model | **Non-root operator user (`wh6gxz`) everywhere**, gateways included. moc3 + meshanchor-server are convergence targets. |
| **D3** | Who establishes the foundation | **One SSOT** (service user + configdir/logfile/storage ownership+mode), applied **at birth** (`install_noc.sh`) **and at converge** (provisioner + `rns_alignment`). **Retire `_fix_rnsd_user`'s bespoke logic** → call the SSOT. The `rns_alignment` canonical layout is the SSOT seed. |
| **D4** | Standalone variant | **A topology *profile* of the one SSOT** (`fleet` vs `standalone`), not a fork. SSOT takes a topology parameter from the start. |
| **D5** | Convergence sequence | **Prove on meshanchor-server canary, then codify.** ✅ Canary DONE (below). Then codify SSOT → converge moc3 → bake into installer. |

### Canary result — meshanchor-server converged to non-root (2026-06-01) ✅
Full converge executed live: stopped (clients→host), chowned data + RNS tree to
`wh6gxz` (`/etc/reticulum` root:wh6gxz 1775; `~/.local/share`, `~/.config`,
**`~/.cache`** /meshanchor; `/opt/meshanchor`), `User=wh6gxz` drop-ins on rnsd +
daemon + map, ordered restart. **Result:** all 3 services active as `wh6gxz`; rnsd
owns `@rns` (no #69); daemon opens `/dev/ttyACM0` as non-root → **MeshCore bridge +
LXMF discovery healthy**; map :5000=200; 0 permission errors; 0 root-owned data left.
**The non-root model is proven end-to-end on a real hardware gateway.**
> Lesson for the SSOT: the converge had to chown **four** data roots
> (`/opt/meshanchor`, `~/.local/share`, `~/.config`, **`~/.cache`/meshanchor`) +
> the RNS tree. The `.cache` dir was missed on the first pass and the acceptance
> test (a live `Permission denied` on `traffic.log`) caught it — the SSOT's
> data-root list must be **complete**, and converge must verify-by-running.

### Post-meeting implementation — ✅ COMPLETE (2026-06-01)
1. ✅ **Extracted the SSOT** — `src/utils/fleet_foundation.py` (data-roots engine,
   topology-parameterized) + `src/utils/rns_tree_perms.py` (the app-agnostic RNS-tree
   perms layout, **byte-identical across MeshForge+MeshAnchor + parity-tracked**). The
   RNS-tree subset was extracted *out of* `rns_alignment` (which is MeshForge-specific:
   it carries `/tmp/meshforge_rns_client` rpc_key logic) so MeshAnchor shares the
   perms definition without dragging MeshForge-only logic — one definition, not two.
2. ✅ **Baked into `install_noc.sh`** (`e5cd883`, born-correct) + **provisioner**
   enforces it on every converge (`947dcde`, `foundation_actions()`); `_fix_rnsd_user`
   bespoke perms retired (`b2c64a5`).
3. ✅ **moc3 converged** to `wh6gxz` (first session); all 5 MeshForge boxes +
   meshanchor-server now run non-root and audit clean.
4. ✅ **Canonical model recorded** in `docs/fleet_roles.yaml` (`57c70aa`, `foundation:`
   block + `born_correct_permissions` invariant). Parity gate: `fleet_foundation.py
   audit` clean fleet-wide (achieved). MeshAnchor port: `33abc729`.

> First-run win: the fleet audit caught a latent mf.4 trigger on **moc**
> (`/etc/reticulum root:root 755`, not group-writable by its non-root rnsd) that the
> mf.4 session had missed; fixed live via `fleet_foundation.py apply` (no restart).

---

## Why now

The mf.4 hang's *trigger* was a permissions defect: moc1/moc2 ran rnsd as
`wh6gxz` but `/etc/reticulum/logfile` was `root:root` — so every rnsd log write
failed, frozen since a 2026-05-03 provision. We fixed the two boxes by hand and
shipped a converge/repair guard (commit `1487357`: `rns_alignment` +
`rns_diagnostics._fix_rnsd_user`). But the operator named the deeper class:

> *"the fresh install has to have that foundation env. in place that includes
> permissions — continuity and parity amongst the fleet is critical."*

The repair half exists. The **foundation** half — a fresh box *born* with the
correct permission environment — does not. This is the same class as
`fleet_etc_reconcile_design.md` ("git pull brings the template; nothing
reconciles /etc/"), one layer down: **nothing establishes or reconciles
ownership/mode of the RNS config tree against a declared model.**

## Current state (verified 2026-06-01)

| boxes | rnsd / daemon user | /etc/reticulum | logfile | storage |
|-------|--------------------|----------------|---------|---------|
| moc, moc1, moc2, VolcanoAI | **wh6gxz** (non-root) | root:wh6gxz **1775** | wh6gxz:wh6gxz | wh6gxz:wh6gxz |
| moc3 (gateway) | **root** | root:root 755 | root:root | root:root |
| meshanchor-server | **root** (rnsd + daemon) | root:root 755 | root:root | root:root |

Each box is **internally consistent** — no box is broken today. The divergence
is the **service-user model**, and it is *born at install*:

- `install_noc.sh` creates `/etc/reticulum` `root:root 755` and writes
  `rnsd.service` with `User=root` — correct **for a root rnsd**.
- The TUI `rns_diagnostics._fix_rnsd_user` later flips rnsd to `wh6gxz` via a
  `user.conf` drop-in. Until commit `1487357` it did **not** repair perms — that
  seam is where the drift was born.

So a box's final user depends on whether someone ran the TUI step, and the perms
matched only by luck. That is the reproducibility debt to close.

## Principle (to ratify)

1. **Born-correct foundation.** A fresh install establishes the *complete*
   permission environment — service user, and ownership+mode of the config tree
   (configdir, logfile, storage) — directly, parameterized by the canonical
   user. No after-the-fact patching, no TUI step required for correctness.
2. **Parity & continuity.** Every fleet box of the same role has the same
   foundation. Drift is detectable and convergeable, not discovered by outage.
3. **Repair stays as defense-in-depth.** `rns_alignment normalize` (and the
   provisioner) converge a drifted box; they are the safety net, not the source
   of truth.

## Decisions for the meeting

### D1 — Canonical service-user model
**Context:** 4 boxes run services as the non-root operator user; moc3 +
meshanchor-server run as root. Operator's stated end-state: **full parity, services
as the non-root operator user fleet-wide.**
- **Option A (recommended): non-root operator user everywhere.** Least privilege;
  matches the majority; consistent RNS identity/RPC; the foundation the guard
  already assumes. Cost: gateways need a hardware-access answer (D2).
- **Option B: root for gateway/hardware boxes, non-root elsewhere.** Codifies
  today's moc3/meshanchor-server reality as intentional. Simpler for hardware, but
  two foundations to maintain and a permanent parity exception.

### D2 — Gateway hardware-access path (the real blocker for A)
**Context:** meshanchor-server's daemon (and gateway boxes) drive MeshCore/RNode
over serial/USB; `/dev/ttyUSB*`, `/dev/ttyACM*` are the reason root was chosen.
- **Option A (recommended): run non-root, grant device groups.** Add the operator
  user to `dialout` (serial) and `plugdev` (USB); optionally a udev rule for the
  RNode. Standard least-privilege pattern; unblocks D1-A. Must be **tested on a
  live gateway** before fleet-wide.
- **Option B: keep gateways root.** No hardware risk, but it's D1-B (permanent
  exception).

### D3 — Who establishes the foundation (close the seam)
**Context:** today it's split across `install_noc.sh` (root:root) and the TUI
flip. Candidates already exist: the installer, `fleet_etc_reconcile_design.md`
(systemd units), and `provision_role.py` (`provisioner_scope.md`, role converge).
- **Recommendation:** the **installer** establishes the foundation at birth,
  parameterized by the canonical operator user (same `__MESHFORGE_USER__` /
  `resolve_operator_user()` mechanism the reconcile + map units already use):
  create `/etc/reticulum` `root:<user> 1775`, logfile + storage `<user>:<user>`,
  and write `rnsd.service` with `User=<user>` directly. **Retire
  `_fix_rnsd_user` as a correctness path** (keep only as a manual repair).
  Add the config tree to the **provisioner**'s converge set so drift is enforced,
  and to `fleet_etc_reconcile_design`'s scope if it grows beyond systemd units.
- This makes `rns_alignment`'s logfile guard a true safety net, not the only line.

### D4 — Standalone variant foundation
**Context:** the standalone build (`standalone_wireclaw_variant.md`, Pi-brain +
ESP32-edge) is a different topology — single box, likely a single operator user,
possibly no separate fleet rnsd-user distinction, no fleet parity to hold.
- **Decision needed:** does standalone share the fleet foundation (subset), or
  define its own? **Recommendation:** standalone is the *degenerate case* of the
  fleet foundation (one user owns everything; same ownership/mode rules), so one
  parameterized foundation covers both — but confirm it doesn't need root for any
  single-box hardware shortcut.

### D5 — Convergence of the root boxes (after D1/D2 ratified)
Sequence, one box at a time, deliberately tested — **not** a blind flip:
1. Add operator user to `dialout`/`plugdev` (D2); verify device access as that user.
2. `chown root:<user> /etc/reticulum; chmod 1775`; `chown <user>:<user>` logfile +
   storage tree.
3. Write `User=<user>` (drop-in or base), `daemon-reload`, **one** restart, verify
   `@rns` owned by rnsd (#69), rnstatus Up, **and the gateway still bridges
   real hardware traffic** (the acceptance test that matters).
4. meshanchor-server first as the canary (it's a single MeshAnchor box, lower
   blast radius than a fleet gateway), then moc3.
- `rns_alignment audit` + the provisioner verify parity after.

## Risks

- **Hardware regression** (D2): a non-root gateway that can't open its serial/USB
  device fails silently-ish. Mitigation: device-access acceptance test in D5 step 1
  + 3; converge one box, soak, then proceed.
- **@rns race on restart** (#69): every service-user change restarts rnsd. mf.4
  makes the stop clean, but verify `@rns` ownership after each (the D5 step 3 check).
- **Two-foundation drift** if D1-B/D2-B is chosen: must be *declared* in
  `fleet_roles.yaml` (role = gateway ⇒ root) so it's intentional + checkable, not
  ad-hoc.

## Proposed post-meeting sequence
1. Ratify D1–D4 → record the canonical model in `docs/fleet_roles.yaml` (or a
   `foundations/` doc) as the SSOT.
2. Bake the foundation into `install_noc.sh` (D3); retire the `_fix_rnsd_user`
   correctness path.
3. Add the config-tree perms to the provisioner's converge set.
4. Converge meshanchor-server (canary) → soak → moc3 (D5).
5. `rns_alignment audit --fleet` clean = parity achieved.

## Open questions
- Is `wh6gxz` the canonical operator user name fleet-wide, or is it
  per-box-resolved (`SUDO_USER`)? (Installer already resolves it; confirm parity
  vs. literal.)
- Does any current gateway rely on root for something beyond serial/USB (raw
  sockets, GPIO, `/dev/mem`)? Audit before D5.
- Should the standalone variant ship its own installer profile, or a flag on
  `install_noc.sh`?
