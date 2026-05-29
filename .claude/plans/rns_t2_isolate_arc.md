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

**Convergence of the live fleet (watched — do with/after B+C, NOT auto):**
current `rns_version_check.py` state -> moc/moc1/moc2 = OK; **moc3 DRIFT** (1.1.4,
forward bump to 1.1.9 — low risk); **VolcanoAI DRIFT** (1.2.5 -> 1.1.9 is a
**DOWNGRADE**, config-format risk).
- moc3: `pip install 'rns==1.1.9' 'lxmf==0.9.6'` (mind venv vs `--break-system-packages`,
  see `updates.py:_pip_install_meshtastic` for the install-path logic) -> verify with
  `scripts/_noc_fix_probe.py` + `scripts/rns_version_check.py`.
- VolcanoAI: **DECISION 1** — downgrade-and-verify, OR rebaseline the pin to 1.2.5
  and converge the gateways UP instead. Do NOT auto-downgrade the federator.
- Goal: `rns_version_check.py` all-OK fleet-wide.

**Open decisions:** (1) pin reconciliation (downgrade VolcanoAI vs rebaseline to
1.2.5); (2) B scope (all 25 vs hot-paths first); (3) vendor depth = sub-arc D,
defer until a patch is needed.

**Done-when:** one guarded constructor, 25 sites routed (or hot-paths + plan for
rest), lint+guard banning raw construction, #68 fail-open verified, fleet
converged + `rns_version_check.py` green. Pre-push checklist + `fleet_sync.sh`
apply. This is meaty -> a fresh session with this file as warm-start is ideal.

## Status
Sub-arc **A DONE** (`e8c5d9c`): pinned `rns==1.1.9`/`lxmf==0.9.6`, drift-check
deployed fleet-wide (moc/moc1/moc2 OK, moc3+VolcanoAI flagged). **B+C scoped &
handed off above** — awaiting a fresh session. D deferred. This file survives `/clear`.
