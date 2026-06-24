# Cross-Domain Fleet Presence — Design Session (2026-06-23)

> Planning session for the item seeded in memory
> `project_cross_domain_fleet_presence_planning_2026_06_23`. Operator ask
> (06-23): *"meshanchor needs a better fleet presence … there should be some
> option to see the MeshForge ⟷ MeshAnchor ⟷ standalone — fleet variable."*
> The two load-bearing unknowns flagged were the **`:5000` collision** and the
> **app-presence map**. This doc settles the findings (VERIFIED this session)
> and proposes a layered design. **No code shipped yet** — execution is gated
> on the decisions in §6 and on the post-06-24 soak window.

---

## 1. The problem, stated precisely

The fleet is a **shared Pi substrate running multiple apps**, but every tool
sees one app's fleet in isolation. The concrete failure mode (observed while
porting `honest_status.sh` to MeshAnchor on 06-23):

> MeshAnchor's `honest_status` with `HONEST_BOXES=moc…` returned `moc:0.974` —
> but `0.974` is **MeshForge's** `confirmation_rate` on moc's `:5000`. MA's
> probe read the wrong app's data and reported it as MA's own.

This is the **honest-failure-modes class** (`.claude/rules/honest_failure_modes.md`
#1/#2): an ambiguous observation (`:5000` answered, JSON parsed) mapped to a
confident-but-wrong claim (this is MA's number). The observation channel
couldn't tell *whose* `:5000` it hit.

Three coupled defects:
- **D1 — no app self-identification.** Neither app names itself in `/api/status`
  or the HTTP `Server:` header. (VERIFIED §2.)
- **D2 — no cross-domain inventory.** Nothing answers "which of
  {MeshForge, MeshAnchor, standalone} is installed/running on each box, at what
  SHA/role/health." The closest existing thing — `gather_fleet_roles()` — is
  single-app (`{host: role}`, MeshForge only).
- **D3 — probes assume `:5000` == their own app.** `honest_status.sh` (both
  repos) hardcodes the assumption. Safe-ish today (foreign app → UNKNOWN, never
  false-PASS) but it's a latent house-of-cards: the moment MA deploys to a fleet
  box, the misread becomes silent and wrong.

---

## 2. Findings (VERIFIED 2026-06-23 — live probe + code read)

| # | Finding | Evidence |
|---|---------|----------|
| F1 | MeshForge `/api/status` has **no** `app`/`name`/`version` field | live `curl` → `{"status":"running","collector":true,…}`; `_map_status_endpoints.py:74` |
| F2 | MeshAnchor `/api/status` is the **byte-identical-opening mirror** | `/opt/meshanchor/src/utils/_map_status_endpoints.py:42-48`, docstring "Mirrors MeshForge's" |
| F3 | HTTP `Server:` header = default Python; no app name | no `server_version` override in `map_http_handler.py`/`map_data_service.py` |
| F4 | Only structural fingerprint IDs the app today | keys `mini_dudeai`/`claw`/`federation`; `/` HTML `<title>Network Map - MeshForge` |
| F5 | Port is configurable (`map_server_port`, default 5000) | `daemon_config.py:51`, `daemon.py:850`, `/etc/meshforge/daemon.yaml` |
| F6 | Role catalog already carries `repo:`/`provisioned_by:` + `meshanchor` data_roots | `docs/fleet_roles.yaml` `foundation.data_roots.meshanchor`, `roles.*.repo` |
| F7 | `gather_fleet_roles(hosts)` collects `{host: role}` over ssh — single-app | `scripts/provision_role.py:443` |
| F8 | Canonical enumeration = `fleet_hosts` 3-tier; `honest_status` is the outlier (hardcodes 5 boxes, omits `meshanchor-server`) | `rollup.py:61 resolve_fleet_hosts()`; `honest_status.sh:36` |
| F9 | `fleet_hosts` lists `meshanchor-server` but it has **no ssh Host entry** — aspirational | `~/.config/meshforge/fleet_hosts`; `~/.ssh/config` |
| F10 | `watchdog.json` = `{host, ts, probe_count, ok, signals[]}`; MA's would be `/var/lib/meshanchor/watchdog.json` (absent on fleet) | `watchdog_runner.py:652`; live `ls` |
| F11 | MA is a single-box dev checkout (VolcanoAI); `/opt/meshanchor` `unreach` on all 5 moc boxes | memory + live `ls`/`pgrep` |
| F12 | `parity_check.py` already governs MF⟷MA SSOT (byte/shape/vocab tiers) | `scripts/parity_check.py:62-90` |

**Key reframe from F11/F12:** the collision is **not** two apps fighting for a
port on one box (the role model already gives each box *one* NOC domain). It's
**tooling pointed at the fleet reading the wrong app's endpoint.** That reframe
makes self-identification — not port reallocation — the right-sized cure.

---

## 3. The design — three layers, each independently useful

### Layer 0 — App self-identification (the load-bearing primitive)

Add an additive `app` block to `/api/status` in **both** repos:

```json
"app": {
  "name": "meshforge",        // | "meshanchor"
  "version": "0.6.2-beta",    // from src/__version__.py
  "repo": "meshforge",
  "host": "moc1",             // already available via watchdog stitch
  "role": "full-gateway",     // from deployment.json (optional)
  "sha": "802f8694"           // git HEAD short (optional)
}
```

- **Additive** — no existing key changes, so no shape-pin test breaks.
- **Cheap** — ~15 lines, imports `__app_name__`/`__version__` (already exist).
- **Parity-natural** — the two files mirror; `parity_check.py` shape-tier pins
  `"app"` presence + that `name` differs correctly per repo (a *negative* parity
  assertion: MF says `meshforge`, MA says `meshanchor`).
- **Unblocks everything** — every downstream probe can now ask "whose `:5000`
  is this?" before trusting a number. This is the cure for D1 **and** the
  observed `0.974` misread.

Also override the HTTP `Server:` header (`server_version`) to
`MeshForge/<ver>` so even a `HEAD /` discloses identity cheaply.

### Layer 1 — App-presence inventory ("the fleet variable")

New tool `scripts/fleet_app_presence.py` (or a `fleet_snapshot` extension) that,
for each host in `resolve_fleet_hosts()` (canonical — fixes the F8 outlier):

1. **Installed?** ssh test `/opt/meshforge`, `/opt/meshanchor`, standalone marker.
2. **Running?** ssh → for each candidate port, `curl /api/status` → read
   `app.name` (Layer 0). The self-id *confirms* which app answered.
3. **Record** `{box: {meshforge: {installed, running, port, sha, version, role,
   watchdog_ok}, meshanchor: {…}, standalone: {…}}}`.

Output: a JSON "fleet variable" + a human table:

```
box     meshforge          meshanchor      standalone
moc      ● full-gateway     ○ absent        ○
moc1     ● primary          ○ absent        ○
volcano  ● primary(:5000)   ◐ installed/idle ○
.32      ○                  ○               ● rf-tools
```

This **generalizes** `gather_fleet_roles` from `{host: role}` (single-app) to
`{host: {app: presence}}` (cross-domain). Cures D2. This is literally the
"option to see MeshForge ⟷ MeshAnchor ⟷ standalone" the operator asked for.

### Layer 2 — Disambiguation hardening + rollup

- `honest_status.sh` (both repos): before trusting `:5000`'s `confirmation_rate`,
  assert `/api/status.app.name == <this repo>`; else UNKNOWN-with-reason
  (`"foreign app 'meshanchor' on :5000"`). Cures D3 at the source of the 06-23
  misread.
- Migrate `honest_status.sh` to canonical `fleet_hosts` (fix F8 outlier; one
  enumeration SSOT fleet-wide).
- Optional `/fleet/presence` endpoint or `fleet_app_presence.py --rollup`: the
  single cross-domain view.

### Standalone as a third presence class

Standalone (the `.32`-style RF-tools foothold) has **no NOC stack / no `:5000`**.
Presence = a positive marker, not an endpoint. Options: a tiny
`~/.config/meshforge/presence.json` written by `standalone.py` at startup, or
inferred ("repo present, no map service, RF tools reachable"). Lowest-risk: an
explicit declared marker, since absence-inference is exactly the honest-failure
trap (#2: unobservable ≠ absent).

---

## 4. Why self-id over port reallocation (the reframe defended)

Distinct default ports (MF `:5000`, MA `:5001`) only helps if MF and MA
**genuinely co-run on one box**. The role model (`fleet_roles.yaml`) gives each
box one NOC domain; F11 confirms MA isn't co-resident anywhere today. So
distinct ports solve a problem that may never exist, while adding a port-registry
maintenance burden. **Self-identification** fixes the *actual observed* defect
(wrong-app misread) and stays correct even if a port is misconfigured. Distinct
ports remain a clean **contingency** if co-residency ever becomes intentional —
and Layer 0 is a prerequisite for that anyway.

A cheaper structural guard is also available: a provisioner **assertion** that at
most one NOC claims `:5000` per box (mutual-exclusion check in
`provision_role.py`), making co-residency a loud error rather than a silent
misread.

---

## 5. Honest-failure-modes audit of the design itself

Walking `.claude/rules/honest_failure_modes.md` over the proposal:

- **#1/#2 (degraded → valid-looking):** the presence probe must distinguish
  *"app absent"* from *"ssh failed"* from *"app present but `:5000` unanswered."*
  Tri-state every leg; never render an unreachable box as "absent."
- **#3 (validators reject the impossible):** `app.name` must be a closed enum
  {meshforge, meshanchor, standalone}; an unknown name is a LOUD finding, not
  silently bucketed.
- **#5 (two consumers, one constant):** the app-name vocabulary and the
  fleet-host enumeration must each be ONE SSOT shared by both repos —
  `parity_check.py` pins them, mirroring the calibration-vocab tier (F12).
- **#9 (every swallow leaves a witness):** a box that's unreachable during the
  presence sweep is reported as `unreachable`, never dropped.

---

## 6. Open decisions (for the operator — see the session questions)

1. **Co-residency stance** — design for one-NOC-per-box (self-id + provisioner
   assertion, *recommended*) vs. design for MF+MA co-residency (distinct ports).
2. **Scope this window** — given the post-06-24 soak gate on map-service
   touches: plan-only now, vs. build the low-risk Layer-0 self-id primitive now,
   vs. full build (Layers 0–2). *Recommended:* land Layer 0 (additive, parity-
   tracked, soak-safe — `/api/status` field doesn't touch RNS or the gateway
   data path), defer Layers 1–2 to post-soak.

---

## 7. Execution sketch (once decided)

- **Layer 0** (½ day): `app` block in `_map_status_endpoints.py` (both repos) +
  `server_version` override + `parity_check.py` shape/negative-name assertion +
  tests (presence of `app.name`, correct per-repo value). Restart `meshforge-map`.
- **Layer 1** (1 day): `scripts/fleet_app_presence.py` reusing
  `resolve_fleet_hosts()` + the `gather_fleet_roles` ssh pattern; JSON + table;
  standalone marker decision.
- **Layer 2** (½ day): `honest_status.sh` self-id guard + `fleet_hosts`
  migration, in both repos; optional `/fleet/presence` rollup.

Each layer is a separate commit, each independently shippable, each parity-ported
to MeshAnchor (MeshForge leads, per the parity discipline).
