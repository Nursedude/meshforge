# RNS T2-Isolate Arc — Scope & Plan

> Staged 2026-05-29. Owns the consequence of the RNS dependency-risk research
> ([[project-rns-upstream-withdrawal-2026-05-29]]): upstream withdrew public
> support (Carrier Switch, Dec 2025), so RNS is now a **vendored dependency in
> spirit** — we control the version, contain its failures, and carry our own
> patches, because no one upstream will. Principle (operator): version control
> of open-source dependencies is a core domain-health responsibility.

## Goal
Make RNS **owned and degradable**, not load-bearing-and-floating:
1. We pick the RNS version; it cannot drift or auto-upgrade under us.
2. An rnsd wedge **degrades** (mesh↔meshcore keep working) — never hangs or
   crashes a MeshForge process.
3. We can carry local RNS patches, since upstream won't merge them.

## Grounded current state (read 2026-05-29)
- **Unpinned**: `requirements/rns.txt` has `rns>=0.7.0`, `lxmf>=0.4.0` — floating
  lower bound, NO ceiling. Every `pip install` grabs latest (now 1.3.x).
- **Live fleet skew**: VolcanoAI `rns 1.2.5`, moc3 `rns 1.1.4` (both `lxmf 0.9.4`).
  Unintended — direct evidence the floating pin already failed us.
- **Scattered init**: **25** `RNS.Reticulum()` construction sites across ~10
  files (cascade_fingerprints, _map_collector_rns, commands/rns, gateway_diagnostic,
  watchdog_runner, meshtastic_broadcast_bridge, _lab_common, _rns_bridge_connection,
  node_tracker, rns_interfaces). Not one chokepoint.
- **Partial chokepoint already exists**: `_lab_common.init_reticulum_with_watchdog`
  (+ `check_rns_listener_owner`, the #69 preflight) and `_map_collector_rns.init_rns_singleton`
  — guarded init, but used in only some paths.
- **Transport abstraction exists**: `gateway/rns_transport.py`; routing is already
  transport-agnostic (`message_routing.py` meshtastic/meshcore/rns pairs) — the
  T3 hedge substrate.
- **Already shipped (T1 + partial T2)**: watchdog, #69 listener-owner preflight,
  `bounded_call` over RNS RPC (#57), circuit breakers, MF009 lint (configdir
  required), in-app guided repair (this session). We're not starting from zero.
- **Known crash class NOT yet fixed**: #68 — RNS init on the main thread can hang
  in `unix_stream_connect` when rnsd is wedged (the map server bound :5000 never
  ran). The #68 "deferred prevention" (bounded connect w/ timeout before
  `RNS.Reticulum()`) was never implemented. **This is the cascading failure T2-C kills.**

---

## Sub-arcs (sequenced quick-win → structural → own-it)

### A. Pin & converge the RNS version  ·  ✅ DONE (commit `e8c5d9c`, 2026-05-29)  ·  effort: SMALL  ·  risk: LOW  ·  value: HIGH
The version-control quick win.
- Pin `rns==<known-good>`, `lxmf==<known-good>` (exact, not floating) in
  `requirements/rns.txt`, with a comment citing the research (upstream withdrawn;
  pinned deliberately; bumps are a reviewed decision, not automatic).
- Converge the fleet to the one pinned version; brief soak; that becomes known-good.
- Add a **fleet version-consistency check** (config_doctor check or a small script)
  that flags any box drifting from the pin — so skew is caught, not discovered.
- Tradeoff to state plainly: pinning forgoes auto security updates — but upstream
  has NO security-disclosure channel anyway (research open-question), so
  pin + in-house-patch is the realistic posture, not a regression.
- **DECISION 1**: which version to pin to (see Decisions).
- **DECISION 2**: pin-only, or also **vendor** the wheels/source (mirror so we
  don't depend on PyPI/GitHub availability — releases are migrating off GitHub)?

### B. One guarded RNS-init chokepoint  ·  effort: MED-HIGH  ·  risk: MED  ·  value: HIGH
Route all 25 `RNS.Reticulum()` sites through a single guarded constructor (extend
`init_reticulum_with_watchdog` / fold into `rns_transport`) that always:
configdir (MF009) · listener-owner preflight (#69) · **bounded connect w/ timeout
(#68 fix, see C)** · shared-instance reuse.
- Add a **lint rule + regression guard**: no raw `RNS.Reticulum()` outside the
  chokepoint (mirror MF007/TestTCPConnectionContract for TCPInterface). This is
  "own it" enforced in code — the same pattern that tamed the meshtasticd TCP
  contention class (#17/#29).
- Migrate the 25 sites incrementally; each is independently testable.

### C. Bounded init = non-cascading degradation  ·  effort: MED  ·  risk: MED  ·  value: HIGH
Implement the #68 deferred prevention IN the chokepoint (B): a pre-flight
`socket.AF_UNIX` probe with a short timeout before `RNS.Reticulum()`; on timeout,
fail OPEN (RNS unavailable → degraded) instead of blocking the calling thread.
Coupled with B (the chokepoint carries the timeout). This is what turns "rnsd
wedged ⇒ whole process hangs" (#68) into "rnsd wedged ⇒ RNS leg degraded, rest
keeps serving" — and it's exactly what NOC Home's "still routing on the other
transport(s)" line promises the operator.
- Verify with the #68 recovery recipe (wedge rnsd, confirm caller fails-open
  within timeout, :5000 still binds).

### D. In-house patch capability  ·  effort: MED  ·  risk: LOW-MED  ·  value: MED (when needed)
Own it for real, since upstream won't merge fixes.
- A `patches/rns/` mechanism (apply local patches at install) OR a vendored fork
  we control. Decide vendor strategy (couples to A-DECISION-2).
- Track community forks (RetiNet/AGPL, Reticulum-rs/Rust) as candidate future
  upstreams; re-evaluate when one reaches production-completeness.
- Do this when a patch is actually needed, not speculatively.

---

## Sequencing
**A now** (1 commit + a check + fleet converge — this week). **Then B+C together**
(the structural core — coupled; a dedicated session, the chokepoint + timeout +
lint guard + 25-site migration). **D when a real patch need arises.**

## Decisions for the operator
1. **Pin target**: survey all 5 boxes, then pin to the version proven on the
   GATEWAY boxes (the cornerstone) — moc3=1.1.4 today, VolcanoAI=1.2.5. Converge
   up to 1.2.5 (newer, last GitHub-published = easiest to vendor) after a soak,
   or hold at the lowest-common proven version? Recommendation: survey → pick the
   newest version already running on a gateway box → converge + soak.
2. **Vendor depth**: pin-only (cheap, good) vs pin + vendor wheels/fork (max
   ownership, more upkeep). Recommendation: pin now; vendor when we either need a
   patch (D) or PyPI access becomes unreliable.
3. **Scope of B**: migrate all 25 sites in one arc, or just the hot paths
   (gateway bridge + map server, the #68 cascade surface) first?

---

## ▶ B+C HANDOFF — START HERE (fresh session)

**Load first:** this file + memory `project_rns_upstream_withdrawal_2026_05_29`.
Sub-arc A (pin + drift-check) is DONE. B+C are coupled — build together.

**Goal:** ONE guarded RNS-init chokepoint with a **bounded connect**, so a wedged
rnsd **degrades instead of hanging**, and raw `RNS.Reticulum()` is banned outside
it. This makes NOC Home's "still routing on the other transport(s)" line literally
true.

**Grounded facts (verified 2026-05-29):**
- **25** `RNS.Reticulum()` sites across ~10 files: `utils/cascade_fingerprints.py`,
  `utils/_map_collector_rns.py`, `commands/rns.py`, `utils/gateway_diagnostic.py`,
  `utils/watchdog_runner.py`, `gateway/meshtastic_broadcast_bridge.py`,
  `lab/_lab_common.py`, `gateway/_rns_bridge_connection.py`, `gateway/node_tracker.py`,
  `launcher_tui/handlers/rns_interfaces.py`.
- **Build on existing primitives:** `_lab_common.init_reticulum_with_watchdog`
  (+ `check_rns_listener_owner`, the #69 listener-owner preflight),
  `_map_collector_rns.init_rns_singleton`. Transport home: `gateway/rns_transport.py`.
  Lint MF009 already requires `configdir=`.
- **The #68 cascade (deferred, still UNFIXED) is the target:** a main-thread
  `RNS.Reticulum()` hangs in `unix_stream_connect` when rnsd is wedged → the map
  server's `:5000` bind never runs (running-but-not-serving). Fix = a timed
  `socket.AF_UNIX` pre-probe before constructing, fail-OPEN on timeout.

**Steps:**
1. **(C, the core)** Define the canonical guarded constructor — extend
   `init_reticulum_with_watchdog` OR add `rns_transport.open_reticulum(configdir=...)`
   — doing, every time: `configdir` (MF009) · #69 listener-owner preflight ·
   **timed AF_UNIX pre-probe -> fail-open on timeout (#68 fix)** · shared-instance
   reuse. The timeout lives HERE.
2. **(B)** Migrate the 25 sites to call it. DECISION 3: all 25 in one arc, OR
   hot paths first (`_rns_bridge_connection.py` + `_map_collector_rns.py` /
   `map_data_service.py` — the actual #68 surface) then the rest.
3. **(B)** Add a lint rule (next MF number) + regression guard: no raw
   `RNS.Reticulum()` outside the chokepoint allowlist — mirror MF007 /
   `TestTCPConnectionContract`. Add to CLAUDE.md's "NEVER" list.
4. **Verify C** with the #68 recipe (persistent_issues #68): wedge rnsd, confirm
   the caller fails-open within the timeout and `:5000` still binds.

**DECISION 1 RESOLVED (2026-05-29): rebaselined the pin to `rns==1.2.5` /
`lxmf==0.9.4`** — converge the gateways UP to the federator-proven, last-GitHub-
published version rather than downgrade VolcanoAI. Reasoning recorded inline in
`requirements/rns.txt`. The federator is now the pin source-of-truth (no-op to
converge), and 1.2.5 doubles as the vendoring anchor for sub-arc D.

**Convergence of the live fleet (watched — do with/after B+C, NOT auto):**
new pin = `rns==1.2.5`/`lxmf==0.9.4`. Expected `rns_version_check.py` state:
**VolcanoAI = OK** (already 1.2.5+0.9.4, no-op); **moc3 DRIFT** (RNS 1.1.4->1.2.5
forward bump, LXMF already 0.9.4); **moc/moc1/moc2 DRIFT** (RNS 1.1.9->1.2.5 UP
**and LXMF 0.9.6->0.9.4 DOWN** — the LXMF leg is a downgrade, soak before/after).
- moc3: ✅ DONE 2026-05-29 (`sudo pip3 install --break-system-packages -r
  requirements/rns.txt`; crypto/pyopenssl already in-bounds so it was a clean
  RNS 1.1.4->1.2.5 bump). Controlled restart (stop gateway -> restart rnsd ->
  verify `@rns` owner + `rnstatus` -> start gateway) avoided the `@rns` host-race.
  Verified: drift-check OK, rnsd interfaces up (AutoIface 3 peers, TCP :4242,
  RNode LoRa), gateway log `Meshtastic: connected`/`RNS: connected`,
  `rpc[rnsd.path_table] ok`. The low-risk converge is the proof-of-recipe.
- moc: ✅ CONVERGED + SOAKING (2026-05-29). The LXMF-downgrade canary (only moc
  runs `meshforge-gateway` → exercises the LXMF bridging path). Verified runtime:
  drift-check OK *in the service env*, rnsd reclaimed `@rns` (AutoIface 3 peers,
  HawaiiNet TCP :4242 Up), gateway `Meshtastic: connected`/`RNS: connected`, MQTT
  + broadcast bridge up, NO LXMF errors on the 0.9.6->0.9.4 downgrade, map :5000
  healthz/api_status=200, shared instance serving 4 programs. ⚠️ see SPLIT-ENV below.
  SOAK baseline 2026-05-29 23:16Z: federator reaches moc (cf=0, no backoff), gateway
  0 errors, rnsd pid 2532792 stable. SOAK RE-CHECK (clean = converge moc1/moc2):
  `ssh moc 'python3 /opt/meshforge/scripts/rns_version_check.py'` still OK; rnsd
  MainPID unchanged (no silent wedge/restart); `journalctl -u meshforge-gateway`
  no new error/traceback + "Messages bridged" M->R/R->M incrementing once mesh
  traffic flows (the real LXMF-0.9.4 proof); federator `/api/status` keeps moc cf=0.
- moc1, moc2: map-only (no gateway bridge), moc1 also = cloud-push. STILL PENDING
  — converge after moc soak is clean. Re-run the SPLIT-ENV check on each first.
- VolcanoAI: no action (already on the pin).
- Goal: `rns_version_check.py` all-OK fleet-wide. (federator + moc3 OK, moc soaking; moc1/moc2 left.)

**⚠️ SPLIT-ENV FINDING (2026-05-29) — drift-check has a per-user blind spot.**
moc runs ALL RNS services (rnsd, gateway, map, maps) as `User=wh6gxz`, importing
from `/home/wh6gxz/.local/lib/python3.13/site-packages`. moc3 runs them as `root`
from `/usr/local/...`. So "what version is the service actually running" depends
on the SERVICE'S user — and `rns_version_check.py` reports the env of whoever runs
it. On moc it was GREEN as root (where `sudo pip3` had installed) while every
service was still on 1.1.9 in wh6gxz's `~/.local`. Converge fix: install into the
SERVICE's env — `pip3 install --break-system-packages rns==1.2.5 lxmf==0.9.4` AS
the service user (non-sudo → ~/.local), not `sudo -r requirements`. Two follow-ups:
(a) **step-2 generalization** must make the drift-check service-env-aware (resolve
each RNS unit's `User=` and check that user's env), and (b) fleet has inconsistent
service `User=` (root vs wh6gxz) + a `~/.local` shadow on moc — a standardization
cleanup (single install location / consistent service user) belongs in that pass.

**Open decisions:** (1) ~~pin reconciliation~~ RESOLVED above (rebaselined to
1.2.5+0.9.4); (2) B scope (all 25 vs hot-paths first); (3) vendor depth = sub-arc
D, defer until a patch is needed.

**Done-when:** one guarded constructor, 25 sites routed (or hot-paths + plan for
rest), lint+guard banning raw construction, #68 fail-open verified, fleet
converged + `rns_version_check.py` green. Pre-push checklist + `fleet_sync.sh`
apply. This is meaty -> a fresh session with this file as warm-start is ideal.

## Status
Sub-arc **A DONE** (`e8c5d9c`): pinned + drift-check deployed fleet-wide.
**DECISION 1 RESOLVED (2026-05-29)**: pin REBASELINED `rns==1.1.9`/`lxmf==0.9.6`
-> `rns==1.2.5`/`lxmf==0.9.4` (converge gateways UP to the federator-proven, last-
GitHub-published combo; VolcanoAI now a no-op). **moc3 CONVERGED + verified;
moc CONVERGED + SOAKING (the LXMF-downgrade canary) — 2026-05-29.** moc1/moc2
still pending (map-only; converge after moc soak is clean; re-check SPLIT-ENV per
box). ⚠️ SPLIT-ENV finding logged (drift-check is per-user; install into the
service's env). **B+C scoped & handed off
above** — awaiting a fresh session. D deferred. This file survives `/clear`.
