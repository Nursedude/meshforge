# MeshForge Persistent Issues & Resolution Patterns

> **Purpose**: Document recurring issues and their proper fixes to prevent regression.
> **Last audited**: 2026-04-22 — Trimmed to <40k chars; resolved issues archived.
>
> **Bloat guard**: lint rule MF012 (`scripts/lint.py --all`) fails when this file
> exceeds 40,000 chars. If it trips, move the oldest fully-resolved issues to
> `persistent_issues_archive.md` and leave a one-row summary in the table below —
> DO NOT raise the limit. The cap exists because the loaded-context overhead of
> this file scales with every conversation turn.

---

## Archived / Fully Resolved Issues

The following are **RESOLVED** with automated prevention (linter + regression tests).
Full history in `persistent_issues_archive.md`.

| Issue | Summary | Prevention |
|-------|---------|------------|
| Health Check Reconciliation | C1-C5, H1 all fixed (2026-02-20) | — |
| Handler Registry Migration | 49 mixins → 60 handler files (2026-02-28) | — |
| #1 Path.home() | Use `get_real_user_home()` | Lint MF001 + regression test |
| #5 Duplicate Utilities | `safe_import` for external deps only | Direct imports for first-party |
| #7 Missing File References | Create scripts before referencing them | — |
| #8 Outdated Fallback Versions | Search hardcoded versions on bump | `grep -rn "0\.[0-9]\.[0-9]" src/` |
| #9 Broad Exception Swallowing | 28/30 fixed; 2 benign by design | `grep except.*:.*pass` |
| #10 Map Scrollbar Overlap | Thin dark-themed scrollbar CSS | — |
| #4 Silent Debug Logging | ERROR/WARNING/INFO/DEBUG level guidance | Behavioral pattern |
| #16 Best-Effort Delivery | SQLite retry queue + "Sent (not guaranteed)" UI wording | Behavioral pattern |
| #17 TCP Connection Contention | Connection manager + `send_text_direct()` | Lint MF007 + `TestTCPConnectionContract` + `TestFromradioContract` |
| #18 Auto-Reconnect | Health monitor + exp. backoff in `rns_bridge.py` | Behavioral pattern |
| #19 RNS path_table discovery | `path_table` (not `destinations`), delayed re-checks | Implementation stable |
| #20 Service Detection / Status | systemctl-only SSOT; state vs. capability separation | Lint MF008 + `TestServiceCheckContract` + `TestKnownServicesConsistency` |
| #24 Python Env Mismatch (rnsd) | Install `meshtastic` module where rnsd's Python finds it | Upstream fix, stable |
| #25, #26, #28 | rnsd ratchets, ReticulumPaths copies, API proxy | — |
| #27 rnsd is OPTIONAL | Two independent transports (MQTT, RNS); preset bridging needs only mosquitto | Design principle |
| #30 NomadNet RPC ConnectionRefusedError | Pre-launch check in `_nomadnet_rns_checks.py`; auto-restart rnsd | Upstream-aware preflight |
| #31 Silent persistent startup changes | `auto_lock_port()` removed; explicit user action only | Design principle |
| #32 NomadNet "enabled but disconnected" | `/proc/cmdline` pgrep verify + shared-instance explicit check | Behavioral pattern |
| #33 Gateway first green end-to-end (2026-04-18) | RX field-validated; 5 install-path gotchas (LXMF pip, uplink default off, topic shape, local TX uplink, traffic.log perms) | Superseded by #34/#40 |
| #34 MQTT topic shape mismatch | Subscribe to `{root}/+/2/json/{ch}/#` AND `{root}/2/json/{ch}/#`; `region=""` default | Code `be6f411` |
| #36 Meshtastic_Interface plugin | Keep rnsd's plugin disabled; MeshForge gateway owns text-bridging | Decision record |
| #37 rnsd AuthenticationError on startup | Authkey derives from identity; `systemctl restart rnsd` after `/etc/reticulum/config` changes | Wrapper catch + Issue #41 pin |
| #38 NomadNet single-identity consolidation | One `~/.nomadnetwork/` per box; tmux-wrapped `nomadnet.service` systemd-user unit | `templates/systemd/nomadnet-user.service` |
| #39 Gateway bridge identifying + directable (2026-04-21) | Mesh→RNS shows long_name in subject; `@id`/`@short_name` parses for directed downlink; `meshforge_*` LXMF fields namespace | Body in archive |
| #3 Services Not Started/Verified | `check_service()` before connect; advisory (daemons) vs blocking (TUI). Body in archive | — |
| #6 Large Files — all under 1,500-line threshold | `knowledge_content.py` (1,993) is the only acceptable exception. Body in archive | — |
| #21 Meshtastic CLI Preset Bug (upstream) | Not a MeshForge bug; verify presets in `:9443` browser after CLI. Body in archive | — |
| GTK Issues (#2, #11, #13–#15) | GTK4 removed in v0.5.x | — |
| #35 Gateway LXMF indexing (2026-04-20) | Bridged messages aggregate under gateway's source hash, prefixed `[Mesh:xxxx]`. Body in archive | TUI Delivery Audit menu |
| #43 MeshCore + AREDN visibility on :5000 (2026-04-22) | Position filter ≠ protocol filter; `_record_diagnostic` taxonomy + operator position promotion. Body in archive | `tests/test_map_data_collector_diagnostics.py` (19 tests) |
| #44 Map server threading — RNS main-thread invariant (2026-04-22) | Pre-warm RNS on main thread; `get_instance()` not name-mangled attr; `_collect_lock` serializes. Body in archive | `TestCollectIsThreadSafe` + lint MF009 |
| #45 NomadNet TUI tmux-wrapped service first-class (2026-04-23) | SSOT `_nomadnet_service_state()`; never `pkill` supervised processes; unified logs. Body in archive | 33 assertions in `tests/test_nomadnet_handler.py` |
| #46 Fleet-wide RNS config alignment + canonical NomadNet installer (2026-04-25) | `rns_alignment.py audit/normalize`; pipx-first idempotent installer; wrapper v8 refuses-loud. Body in archive | `TestPlanNormalize::test_idempotent_on_already_normalized` |
| #54 Federation peer_name correlation (2026-05-17) | `FederationPeerStatus.peer_name` plumbed end-to-end so `/api/status.federation` rows line up with MA `/fleet/rollup` + tracer leaderboard. Body in archive | `TestPeerNamePlumbing` (6 tests) |
| #55 `/fleet/slo` latency cliff (2026-05-17) | TTL-cached + parallel `_systemctl_state` probes; cold call 2.4s → 400ms, well under MA's 3s peer-fetch budget. Body in archive | `tests/test_fleet_snapshot.py` (10 tests) |
| #56 Federation directory timeout (2026-05-17) | `DEFAULT_TIMEOUT` 5s→30s in `map_federation`; 35 MB `/api/nodes/directory` couldn't fit the old budget. Body in archive | `TestDefaultTimeout` (3 tests) |
| #47 NomadNet two-conversations UX (2026-04-25) | Known Nodes vs Conversations panel distinction; gateway thread vs peer LXMF thread. Operator seeding flow. Body in archive | UX/documentation |
| #53 meshforge-map stale daemon (2026-05-02) | fleet_sync.sh now restarts `meshforge-map.service` alongside `meshforge` + `meshforge-maps`. Body in archive | `scripts/fleet_sync.sh` (commit `660f26f`) |
| #40 RNS→Mesh bridge bytes-payload + MQTT topic (2026-04-21) | Decode bytes→str at `_process_rns_to_mesh()` entry; reroute MQTT-bridge enqueue to `destination="meshtastic"` (HTTP `/api/v1/toradio` SSOT). Body in archive | `tests/test_rns_bridge.py` (5 tests) |
| #41 rpc_key pinning for gateway inbound (2026-04-21) | Propagate rnsd's `rpc_key` into MeshForge's 3 client-only RNS configs (`paths.ReticulumPaths.get_shared_rpc_key()`); RNS 1.1.x literal `rpc_key`. Body in archive | — |
| #48 Phase-2 migration inherits WAL → cold-start stall (2026-04-27) | `PRAGMA wal_checkpoint(TRUNCATE)` on source DB before cp; new service first-open is fast. Body in archive | `tests/test_migrate_map_service.py` (6 assertions) |

---

## Development Checklist

Before committing, verify:

- [ ] No `Path.home()` — use `get_real_user_home()`
- [ ] Actionable error messages, appropriate log levels
- [ ] Services verified with `check_service()` before use
- [ ] `subprocess` calls have `timeout=` (MF004)
- [ ] Utilities from central location, not duplicated
- [ ] `safe_import` for external deps only; direct imports for first-party

---

## Quick Reference: Import Patterns

```python
# Paths
from utils.paths import get_real_user_home, get_real_username, MeshForgePaths, ReticulumPaths

# Settings / Logging
from utils.common import SettingsManager, CONFIG_DIR
from utils.logging_config import get_logger

# Service checks
from utils.service_check import check_service, check_port, ServiceState

# External deps (safe_import)
from utils.safe_import import safe_import
RNS, _HAS_RNS = safe_import('RNS')
_pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')

# First-party — ALWAYS direct import
from utils.service_check import check_service
from utils.event_bus import emit_message
from gateway.rns_bridge import RNSMeshtasticBridge
```

**Test patching**: Patch `_HAS_*` flags directly, not `sys.modules`:
```python
@patch('gateway.rns_bridge._HAS_RNS', True)  # CORRECT
def test_rns(self): ...
```

---

## Issue #12: RNS "Address Already in Use"

**Rule**: Never call `RNS.Reticulum()` without `configdir=` when rnsd is running.

MeshForge creates a client-only config in `/tmp/meshforge_rns_client/` with
`share_instance = Yes` and no interface definitions, allowing connection to
rnsd without binding ports.

Location: `src/gateway/node_tracker.py` — `_init_rns_main_thread()`

---

## Issue #22: Never Overwrite meshtasticd's config.yaml

**Rule**: Check for existing valid config before touching it.

```
/etc/meshtasticd/
├── config.yaml     # PROVIDED BY meshtasticd — DO NOT OVERWRITE
├── available.d/    # HAT templates — PROVIDED BY meshtasticd — DO NOT CREATE
└── config.d/       # User's active HAT config — COPY from available.d/
```

Radio parameters (Bandwidth, SpreadFactor, TXpower) are set via
`meshtastic --set lora.modem_preset` and stored internally — **NEVER in yaml files**.

MeshForge's job: Help users SELECT HATs from meshtasticd's `available.d/`, COPY to
`config.d/`. Never overwrite `config.yaml` if it has a `Webserver:` section.

---

## Issue #23: Post-Install Verification

**Rule**: Never mark install "complete" until verification passes.

`scripts/verify_post_install.sh` checks: meshtasticd binary, config.yaml validity,
Webserver section, port 9443, radio detection, config.d/, rnsd, udev rules.
Also available via `meshforge --verify-install`.

---

## Issue #29: Regression Prevention System — ACTIVE

100+ hours of circular regressions led to this 4-layer prevention system.

### Layer 1: Lint Rules (`scripts/lint.py`)
| Rule | Catches |
|------|---------|
| MF007 | Direct `TCPInterface()` outside connection infrastructure |
| MF008 | Raw `systemctl` for service state (use `service_check`) |
| MF009 | `RNS.Reticulum()` without `configdir=` |
| MF010 | `time.sleep()` in daemon loops |
| MF014 | Operator-specific values (hostnames, personal email, `/home/<user>/`) — break repo portability |

### Layer 2: Regression Guard Tests (`tests/test_regression_guards.py`)
- `TestTCPConnectionContract` — No new direct TCPInterface
- `TestFromradioContract` — TX uses `send_text_direct()`
- `TestServiceCheckContract` — Service state via `check_service()` only
- `TestPathHomeContract` — No `Path.home()` violations
- `TestNoShellTrue` — No `shell=True` in subprocess
- `TestKnownServicesConsistency` — KNOWN_SERVICES stays correct
- `TestOperatorValueContract` — No operator-specific values in source/templates/scripts/docs (MF014)

### Layer 3: Pre-Commit Hook (`.githooks/pre-commit`)
Setup: `git config core.hooksPath .githooks`

### Working With This System

**New file needs meshtasticd TCP:**
```python
# Short-lived:
from utils.connection_manager import MeshtasticConnection
with MeshtasticConnection() as conn:
    if conn: nodes = conn.nodes

# Long-lived:
from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK, wait_for_cooldown
if MESHTASTIC_CONNECTION_LOCK.acquire(timeout=10):
    wait_for_cooldown()
    interface = TCPInterface(hostname='localhost')
```

**Adding legitimate TCPInterface creation:**
1. Add to `ALLOWLISTED` in `TestTCPConnectionContract`
2. Add to `lock_aware_files` in lint.py MF007
3. Acquire `MESHTASTIC_CONNECTION_LOCK` before creating

---

## Issue #49: Lean node directory — split "what we know" from "what we observed" (2026-04-28)

**Why**: Single-table `node_observations` with 7d retention forced an
impossible trade — extend retention to keep quiet nodes cached, balloon
the time-series; cut retention to slim the DB, lose silent nodes after
a week. External references (rmap.world, map.meshcore.io, KN6PLV
MeshMap) all separate persistent node directory from time-series.
MeshForge now does too.

**Two tables in `node_history.db`**:
1. `nodes` (new) — one row per `(network, node_id)`. first_seen,
   last_seen, last_lat/lon/altitude, name, role, hardware,
   source_origin, protocol_meta JSON, obs_count. Position **nullable**
   so MeshCore adverts and RNS announces still produce a directory row.
2. `node_observations` (existing) — retention 7d → **48h**. Trajectories
   only; directory answers "did we ever hear this node?" on the long tail.

**Tiered retention** (drives the prune SQL — module-level
`EXTERNAL_BULK_ORIGINS` set is the SSOT):
- Local origins (local_radio, rns_path_table, aredn_local, mqtt_local,
  node_tracker, operator_positions): **30 days**.
- External-bulk (meshcore_public, aredn_worldmap, mqtt_global): **7 days**.
- Hard count cap **50_000 rows**, LRU evict by oldest last_seen.

**Sticky source-origin promotion**: priority lookup
(`local_radio`=100 > `rns_path_table`=90 > `aredn_local`=80 >
`mqtt_local`=70 > `node_tracker`=60 > `operator_positions`=50 >
external bulk=30 > `public_fallback`=20). UPSERT only overwrites
source_origin when incoming priority ≥ existing. A node first heard
via `meshcore_public` promotes to `local_radio` when the radio
actually hears it (moves to 30d tier); reverse demotion never happens.
SQL: `WHEN ? >= ({existing_case})` in the ON CONFLICT branch.

**Endpoints**:
- `GET /api/nodes/directory` — full directory dump as GeoJSON +
  `nodes_without_position` sibling. Superset of `/api/nodes/geojson`.
- `GET /api/status` extended with `directory` block: total, by_network,
  by_source_origin, with/without-position counts, oldest/newest
  last_seen, retention + cap config.

**Files**: `src/utils/node_history.py` (schema +
`_apply_features_to_directory()` + tiered `_maybe_prune()` +
`get_directory_stats/snapshot()`); `src/utils/map_data_collector.py`
(`_tag_source_origin()` per-merge-site, unified-tracker per-network);
`src/utils/map_http_handler.py` (`_serve_directory()` + status block);
`src/utils/db_inventory.py` (DBSpec note).

**Tests** (17 in `tests/test_node_history.py` + 3 in
`tests/test_map_data_collector_diagnostics.py`): UPSERT shape,
position-null preservation, protocol_meta 4 KB cap, sticky promotion
both directions, tiered prune at boundaries, count-cap LRU,
observation retention cut, status block, snapshot split, origin
priority invariant, malformed-feature tolerance.

**Backfill**: lazy. `_init_db()` creates the table; next collect cycle
populates it. No bulk replay from observations.

**Deferred** (call out, separate PRs): cross-fleet federation
("every map sees every box's nodes" — directory is the prerequisite,
user framed "live is another issue"); frontend "offline cached" badge;
meshforge-maps :8808 parallel directory.

**Operator recipe — see what's cached / verify tiers**:
```bash
curl -s http://<box>:5000/api/status | jq '.directory'
sqlite3 ~/.local/share/meshforge/node_history.db <<'EOF'
SELECT source_origin, COUNT(*) AS n,
       printf('%.1f', (julianday('now') - julianday(MIN(last_seen), 'unixepoch'))) AS oldest_d
FROM nodes GROUP BY source_origin ORDER BY n DESC;
EOF
# Expect: meshcore_public oldest_d ≤ 7.0; local_radio oldest_d ≤ 30.0
```


---

## Issue #50: Directory tier retention defeated by UPSERT last_seen rewrite (2026-04-30)

**Symptom (fleet-wide)**: After Issue #49 shipped, `node_history.db`
ballooned on every box that had external-bulk collectors enabled —
volcanoai 2.0 GB, moc3 803 MB, moc1 692 MB, moc 654 MB. moc2 stayed at
19 MB (no `meshcore_public` / `public_fallback` / `aredn_worldmap`).
4 of 5 boxes pinned at exactly 60,298 directory rows — 20% over the
50,000 LRU cap, with `oldest_last_seen == newest_last_seen` in
`/api/status`. The 7d external retention tier never fired.

**Root cause**: external-bulk sources republish their entire dataset
every collect cycle (meshcore.dev = 41k nodes, public_fallback = 14k).
`_apply_features_to_directory` stamped `last_seen = now` at INSERT and
unconditionally overwrote it with `excluded.last_seen = now` on
CONFLICT. Result: every row's tier clock reset to NOW each cycle, so
`directory_retention_external = 7d` could never fire. Only the 50k LRU
cap pruned anything; with 41k meshcore_public rows republished every
cycle, eviction churned >5k rows/cycle and the next cycle re-INSERTed
them immediately.

**Fix** (in `src/utils/node_history.py`):
1. `_build_directory_row` reads `properties.last_heard` from the feature
   for external-bulk origins (`meshcore_public`, `aredn_worldmap`,
   `mqtt_global`, `public_fallback`) and uses it as the row's `last_seen`
   when present and `0 < ts ≤ now`. Local origins always use `now`.
   Future-dated upstream timestamps are clamped to `now` so a misbehaving
   source can't poison the prune horizon.
2. ON CONFLICT clause changed from `last_seen = excluded.last_seen` to
   `last_seen = MAX(nodes.last_seen, excluded.last_seen)`. Re-publishing
   an unchanged upstream record leaves the tier clock alone; only a
   genuinely newer upstream observation advances `last_seen`.
3. `first_seen` decoupled from `last_seen` on INSERT — first_seen is
   always `now` ("when WE first inserted this row"), `last_seen` is the
   upstream-aware candidate. On a fresh row from an old upstream record,
   `first_seen > last_seen` is intentional and accurate.

**Why option 3 (upstream stamp)** over the alternatives in
`project_map_arc_findings.md`:
- "Skip directory writes for external bulk" loses the
  "did we ever hear about this node" answer that Issue #49 added the
  directory for.
- "Conditional last_seen update" required a side-channel signal for
  "is this fresh"; the upstream `last_heard` already encodes that.
- All three external-bulk parsers
  (`_parse_meshcore_public_node`, `_parse_worldmap_row`,
  `_parse_*_public` in `_map_collector_public.py`) already emit
  `properties.last_heard`, so option 3 was a precise low-blast-radius
  edit.

**Tests**: `TestDirectoryUpstreamTimestamp` in `tests/test_node_history.py`
(9 tests) — upstream-stamp wiring, republish-no-advance, newer-upstream-
advances, MAX-monotonic guard against regression, last_heard=0 fallback,
future-clamp, local-origin ignore, unknown-origin ignore, end-to-end
prune at 8d-old upstream.

**Operator recipe — verify the fix on a fleet box**:
```bash
# Pre-fix smoking gun: oldest == newest in /api/status.directory.
# Post-fix (after a collect cycle or two): oldest ages back to ~7d.
curl -s http://<box>:5000/api/status | jq '.directory | {oldest_last_seen, newest_last_seen, total}'
# Confirm divergence, then re-check after 24h: total should drop as
# external rows whose upstream stamps cross the 7d boundary get pruned.
```

**One-time cleanup of pre-fix bloat**: existing rows still carry
`last_seen ≈ NOW`, so the 7d tier won't catch up until they age out
naturally over the next week (or operators force a one-shot prune by
deleting rows where `last_seen > NOW - 60` for external origins, then
letting the next cycle repopulate with correct upstream stamps). Risky
on a busy DB — better to let the fix soak naturally; the hourly prune
+ 50k LRU cap keeps growth bounded in the meantime.


---

## Issue #51: Issue #50 wiring unreachable — meshcore parser emitted ISO-8601 string, not Unix epoch (2026-04-30)

**Symptom**: After fdee95e shipped Issue #50/F7 to the fleet, post-restart
verification showed 0% upstream-stamped rows on volcanoai. All 58,598
external-bulk rows had `last_seen ≈ NOW`. The 7d external-retention tier
still did not fire; the smoking-gun "oldest == newest" pattern persisted.

**Root cause**: `map.meshcore.dev/api/v1/nodes` returns `last_advert` as
ISO-8601 string (`'2026-04-27T19:45:54.000Z'`), not Unix epoch.
`_parse_meshcore_public_node` passed it through raw to
`properties.last_heard`. Then `_build_directory_row`'s `float(upstream)`
call raised `ValueError`, the bare `except (TypeError, ValueError)`
swallowed it, and `last_seen` silently fell back to `now`. The Issue #50
wiring was reachable in unit tests (which used already-normalized
numeric `time.time() - delta` fixtures) but unreachable in production
against the real upstream payload shape.

**Fix** (4a9985e): inline ISO-8601 → Unix epoch normalization in
`_parse_meshcore_public_node`, mirroring the existing pattern in
`_parse_worldmap_row` (`datetime.fromisoformat(...).timestamp()`).
Numeric pass-through preserves forward-compat. Failed parse →
`last_heard=0.0`, which `_build_directory_row` treats as "no credible
upstream stamp" → falls back to `now` (correct semantics for genuinely
unstamped rows).

**Tests**: 5 new in `TestMeshCorePublicCollector` covering real-shape
ISO-8601 with `.000Z` suffix, numeric pass-through (forward-compat),
invalid-string fallback to 0.0, missing field, and end-to-end round-trip
through `_build_directory_row` to confirm the F7 contract holds against
real upstream payload shape.

**Verification post-rollout** (volcanoai, 2026-04-30 22:50 UTC):
- 25.6% of meshcore_public rows are now upstream-stamped (`first_seen >
  last_seen`); the remaining 74.4% are pre-fix-NOW rows that will age
  out over 7d via the now-functional tier prune.
- last_seen range stretches from 56-year-old upstream stamps (1970-ish
  legitimate-or-zero entries upstream) to NOW.
- Next prune cycle is expected to evict tens of thousands of rows whose
  upstream `last_heard` is already > 7d old.

**Deployment cost** (per-box, observed): ~7 min warming on volcanoai
(480 MB WAL fsync on Pi-class SD). Other fleet boxes have
`enable_meshcore_public: false` and clear in 60-90s — they federate
meshcore_public rows from volcanoai instead of fetching independently.

**Prevention**: tests must use real upstream payload shape, not
synthetic already-normalized stand-ins. Audit other parsers for the
same gap when introducing analogous schema-aware flow logic. The
`TestMeshCorePublicCollector` round-trip pattern (parser → directory
row tuple) is the regression-prevention shape for similar future work.


---

## Issue #57: Gateway data-path watchdog — `bounded_call` over RNS RPC hot path (2026-05-17)

**Symptom (latent, not yet field-validated post-fix)**: The gateway's
hot-path RNS RPC calls (`RNS.Transport.has_path/request_path`,
`Identity.recall`, `LXMRouter.handle_outbound`, `LXMF.LXMessage()`
ctor) ran under `call_boundary()` which **logs slow calls but never
aborts them**. If rnsd's RPC listener wedged after init (the kernel
`unix_wait_for_peer` hang documented in
`project_rnsd_rpc_listener_wedge`), gateway threads hung silently
forever — no exception, downlinks just stopped, systemd reported
`active (running)`. Lab tracer survived this via
`_lab_common.bounded_block`; the actual production bridge didn't.

**Fix (PR-1 of the A+B+C arc, Plan
`fix-federation-persistent-issues-2026-05-17.md`)**:

- New `src/utils/wedge_events.py` — bounded `deque(maxlen=200)` +
  `publish/recent/subscribe` pub-sub. Forensic trail consumed by
  `bridge_cli` (live debug) and Fork B's recovery actor (future PR).
- New `src/gateway/bounded_rpc.py` — `bounded_call(label, fn, *args,
  target, timeout_s, threshold_s, on_wedge, exit_on_wedge, **kwargs)`.
  Composes `_lab_common.bounded_block` over `timed_boundary` so
  p50/p95/p99 metrics keep populating. Default `on_wedge` bumps
  `rns_call_wedge_total[label]` + publishes a `WedgeEvent`, then the
  watchdog `os._exit(2)`s — systemd restarts the gateway (strictly
  better than silent hang). Env-var per-label overrides
  (`MESHFORGE_BOUNDED_RPC_TIMEOUT_*`) for operator escape.
- `src/gateway/circuit_breaker.py` — added
  `CircuitBreaker.trip_open(reason)` + registry wrapper. Single-event
  fast-path to OPEN; idempotent on already-open. Used by the bridge's
  composite `on_wedge` so a wedged destination is shed before
  process abort.
- `src/gateway/rns_bridge.py` — 11 call sites migrated from
  `call_boundary` to `bounded_call` with per-kind budgets: `has_path`
  3 s, `request_path` 5 s, `Identity.recall` 3 s, `LXMessage()` ctor
  5 s, `handle_outbound` 15 s. Composite `on_wedge` hook trips the
  circuit breaker on the destination's hash before calling default
  publish-+-counter.
- `src/gateway/node_tracker.py` — `path_table` access wrapped in
  `bounded_call` (`rnsd.path_table`, 2 s) — the property has been
  observed to block under wedge.

**Tests** (42 new): `test_wedge_events.py` (17), `test_bounded_rpc.py`
(19), `test_circuit_breaker.py` (+6 for `trip_open`).

**Verify**: `ssh <box> 'pkill -STOP rnsd'` + gateway send → journal
shows WEDGED + process exit + systemd restart; release with `-CONT`.
PR-2 (Fork B) and PR-3 (Fork C) ship next on this substrate. Plan:
`~/.claude/plans/fix-federation-persistent-issues-2026-05-17.md`.


---

## Issue #59: Federation polls a permanently-failing peer every cycle — exponential backoff (2026-05-18)

**Symptom**: Operational survey of moc/moc1/moc2/moc3 showed every box's
`/api/status.federation.peer_status[]` had moc3 stuck at `ok=false,
last_error=Connection refused, consecutive_failures` climbing.
Reason: moc3 is gateway-only per the fleet topology — it has no
`meshforge-map.service`, no `:5000` listener. Federation kept hitting
it every 60s anyway, every cycle failed identically, the counter
climbed forever. The row drowned out genuine failures that operators
needed to spot in the rollup.

Broader pattern: any peer that's been unreachable for a while (a box
in a long reboot, a network partition, an intentionally-decommissioned
host that's still in fleet.json) produces the same noise — fixed-
interval polling has no way to step back.

**Root cause**: `FederationCollector.poll_once` polled every peer in
`self._peers` on every cycle, regardless of failure history. The
`consecutive_failures` counter was already tracked (Issue #54), but
nothing acted on it.

**Fix** (Option B per the 2026-05-18 lab-vs-handoff design call —
preferred over surgical roster cleanup because the lab is now a
handoff target for an external dev, and code-shape fixes beat
config-shape fixes when state drift across deployments is a risk):

`src/utils/map_federation.py`:
- New `FederationPeerStatus` fields: `in_backoff: bool`,
  `next_eligible_poll_ts: Optional[float]`, `backoff_multiplier: int`.
- New `FederationCollector` knobs (defaults exposed at module level):
  `backoff_threshold=3` (failures before engaging), `backoff_base=2`
  (exponential factor), `backoff_max_multiplier=10` (cap at 10×
  poll_interval). `time_fn` injected for testable clock control.
- New `_compute_backoff(consecutive_failures, now)` pure helper —
  shared by both branches, math pinned by `TestComputeBackoffPure`.
- `poll_once` now buckets peers into `due_peers` (next_eligible_poll_ts
  is None or ≤ now) and `skipped_peers`. Skipped peers carry forward
  their last status unchanged except for `peer_name` refresh, so
  `/api/status` shows the labeled row, not a phantom-absent peer.
- Engage/clear transitions log at INFO level once each so operators
  see the state change without log flooding.

`src/utils/map_http_handler.py`:
- `/api/status.federation.peer_status[]` now serializes `in_backoff`,
  `backoff_multiplier`, `next_eligible_poll_ts` per peer. Visibility
  per the design principle: "A failed system saying I'm failing in
  *this specific way* is worth more than a quiet one."

**Tests** (13 new in `tests/test_map_federation.py`):
- `TestBackoffEngages` (2): below-threshold stays active; threshold-th
  failure flips in_backoff with multiplier=1 and stamps next_eligible.
- `TestBackoffEscalates` (2): multiplier doubles per failure (1→2→4→8);
  capped at `max_multiplier` past the integer-overflow horizon.
- `TestBackoffSkipsPolls` (2): peer in active backoff window is NOT
  fetched (call_count unchanged); becomes eligible exactly when clock
  crosses next_eligible_poll_ts.
- `TestBackoffRecovery` (1): one successful poll clears all backoff
  state (multiplier=1, in_backoff=False, next_eligible=None,
  consecutive_failures=0).
- `TestBackoffMultiPeerIsolation` (1): healthy peer keeps getting
  polled while a separate peer is in backoff — the bad peer doesn't
  suppress the good one.
- `TestBackoffStateInCarriedSnapshot` (1): a skipped peer still has
  its row in `/api/status` with `in_backoff=True` (so the operator
  sees "labeled, paused" not "missing").
- `TestComputeBackoffPure` (3): the math helper is pinned in isolation
  — below-threshold, at-threshold, far-above (capped).

**Operator detection recipe**:
```bash
curl -s http://<box>:5000/api/status | \
  jq '.federation.peer_status[] | select(.in_backoff) |
      {peer_name, hostname, consecutive_failures, backoff_multiplier,
       next_eligible_poll_ts, last_error}'
# Empty output = no peers in backoff = federation is healthy
# Rows = peers the collector has self-quieted; check last_error to see why
```

**Companion to Issues #54 (peer_name correlation) and #55 (`/fleet/slo`
latency cliff)**: the federation triad — diagnostics (#54), raw budget
(#55), and now self-pacing (#59) — collectively turn "federation
persistent issues" from a recurring class into a closed loop. moc3's
roster cleanup (the design call deferred at the start of this turn)
is now moot: the system handles it.


---

## Issue #60: Systemd sandbox path drift class — preflight + audit (2026-05-18)

**Class**: A hardened systemd unit (``ProtectHome=read-only`` +
curated ``ReadWritePaths=``) drifts from the dirs the code actually
writes when a refactor moves state to a new bucket. The service stays
``active (running)`` while every write fails inside a callback
exception. Issue #58 was the canonical instance — commit ``a420829``
moved delivery_counters to ``~/.local/share/meshforge/`` but the unit
only whitelisted ``~/.config`` and ``~/.cache``. moc3 logged
``sqlite3.OperationalError: unable to open database file`` ~48/hr for
18h before detection — the entire "honest delivery counters" feature
non-functional in production.

**Why this class is dangerous**:
- Code change (the path move) and ops change (ReadWritePaths) live in
  separate files connected only by convention.
- Runtime failure is in an exception handler, not main loop — service
  reports `active`, monitoring doesn't fire.
- Unit tests run outside the sandbox, so they can't see the trap.
- Detection requires noticing the side-effect (empty table) or
  grepping journal for the silent exception.

**Surface audit (2026-05-18)**: Two MeshForge services run hardened
— ``meshforge-gateway.service`` (this repo) and
``meshforge-maps.service`` (sister :8808). Five SQLite DBs live in
``_meshforge_data_dir() = ~/.local/share/meshforge/``: ``node_history``,
``offline_sync``, ``traceroute_history``, ``presentation_capture``,
``delivery_counters``. Eleven more DBs and settings/fleet.json live in
``_meshforge_config_dir()``. The data-dir bucket is the newer addition
(Issue #29 / db_inventory) and the most likely to be missed by
predates-data-dir unit templates.

**Cure (this commit) — two layers**:

1. **Runtime preflight** (Option B). New
   ``src/utils/sandbox_check.py`` with ``meshforge_writable_paths()``,
   ``verify_writable_paths()``, and ``assert_writable_or_exit()``.
   Wired into ``src/gateway/bridge_cli.py:main()`` near the top, before
   any DB open. On drift, the gateway exits with code 2 and a precise
   operator-actionable error message naming the missing bucket and the
   ReadWritePaths line to add. Tests:
   ``tests/test_sandbox_check.py`` (15 assertions) including a verbatim
   reconstruction of the moc3 incident shape under
   ``TestIssue58ClassRegression``.

2. **CI audit** (Option A). New MF017 in ``scripts/lint.py`` walks
   ``contrib/systemd/*.service.in``; any unit with
   ``ProtectHome=read-only`` must include all three meshforge buckets
   in ``ReadWritePaths=``. Inline ``# audit-skip: <reason>`` comment
   opts out explicitly — the marker is the signal that the omission is
   deliberate, not drift. Tests: ``tests/test_lint_mf017.py`` (8
   assertions) including locking the real repo content via
   ``TestMF017RealRepoUnits::test_real_contrib_systemd_passes``.

**Latent risk closed in same turn**: moc1 and moc2's live
``meshforge-gateway.service`` units still had the pre-Issue-#58
ReadWritePaths shape (gateway is inactive on those boxes, so the
trap was dormant). Patched in place via ``sed`` + ``daemon-reload``
— now matches the repo template. The whole fleet is consistent.

**Operator detection recipe** (for the next instance of this class):
```bash
# On startup: gateway either runs cleanly or exits with a specific
# missing-path error. No more silent-callback-exception class.
sudo journalctl -u meshforge-gateway --since "5 min ago" --no-pager \
  | grep -E "sandbox writable-path check FAILED|ReadWritePaths"
# At PR time: MF017 audit refuses to merge a hardened unit that
# omits a required bucket without an # audit-skip: marker.
python3 scripts/lint.py --all
```

**Design note — why both layers**: A catches the gap at PR time so it
never ships; B catches it at install time on a fleet box whose unit
predates the audit. A's failure mode is "your PR fails CI"; B's is
"your service refuses to start with a precise error message." Two
cheap layers, neither expensive. The "lab today, deploys tomorrow"
framing (Issue #59's design call) drove the choice — the next operator
inheriting this codebase doesn't need to know which file to update; the
system either runs cleanly or tells them exactly what's wrong.


---

## Issue #58: Upstream HAT template smuggled `Webserver: Port: 443` → :9443 silently moved (2026-05-18)

**Symptom**: moc3's dashboard showed `meshtasticd` as the active-but-
unreachable yellow ◐ surfaced by the new probe in commit `8b06ebd`.
Daemon reported `active (running)` for 18h, `:4403` was bound, but
nothing answered on `:9443`. Every MeshForge consumer that posts to
`https://127.0.0.1:9443/api/v1/toradio` (the gateway TX SSOT in
`meshtastic_protobuf_client.send_text_direct`) failed silently — moc3
couldn't bridge a single RNS→Mesh downlink the whole time.

**Root cause**: `/etc/meshtasticd/available.d/lora-MeshAdv-900M30S.yaml`
shipped by `chrismyers2000/MeshAdv-Pi-Hat` (vendored into meshtasticd
2.7.15) carries a stray `Webserver: Port: 443` block that has nothing
to do with the HAT's radio config. The standard activation flow —
`cp available.d/<hat>.yaml config.d/` — merges this overlay on top of
`/etc/meshtasticd/config.yaml`, where `Port: 9443` lives. Overlay wins.
Meshtasticd happily binds `:443` instead. The base config file is
untouched (Issue #22's invariant holds), so visual inspection of
`config.yaml` doesn't reveal the override; only checking the live
listening port catches it. moc and moc2 use the same HAT name but
installed earlier, before the upstream template gained this stray
block, so their `config.d/` copies are clean.

**Fix (code)**: `src/launcher_tui/handlers/meshtasticd_config.py` —
`_HAT_OVERLAY_FORBIDDEN_KEYS = {Webserver, TCP, Logging, MQTT,
Bluetooth, General}` plus `_sanitize_hat_overlay(text) -> (yaml,
stripped[])`. `activate_hardware_config` now reads the source, strips
forbidden top-level blocks, writes the cleaned text, and warns with
the stripped key list. YAML parse errors pass through untouched —
meshtasticd will surface them loudly rather than silently mangling
operator content.

**Fix (moc3)**: copied moc's clean `config.d/lora-MeshAdv-900M30S.yaml`
over the broken one, restarted meshtasticd. `:9443` rebound in <5s,
gateway HTTPS handshake + `/api/v1/fromradio` both returned 200, status
bar flipped from `'-'` (SYM_STOPPED) back to `'*'` (SYM_RUNNING).

**Tests** (`tests/test_hat_overlay_sanitizer.py`, 7 assertions):
the moc3-actual broken template content is pinned inline as
`MOC3_BROKEN_TEMPLATE`. `TestSanitizerStripsTheMoc3Webserver` asserts
(a) Webserver vanishes from the parsed output, (b) the literal byte
string `Port: 443` is nowhere in the sanitized text, (c) the Lora
block survives intact. `TestSanitizerLeavesCleanTemplatesAlone` keeps
the sanitizer from rewriting healthy templates. `TestSanitizerFailsSafe`
covers YAML parse error + non-mapping top-level (both pass through
unchanged). `TestForbiddenKeysContract::test_webserver_is_forbidden`
locks `Webserver` in the forbidden set so a future cleanup can't
silently drop it.

**Operator detection recipe** (works on any fleet box):
```bash
ssh <box> "ss -tnlp 2>/dev/null | grep meshtasticd"
# Healthy:    LISTEN ... 0.0.0.0:9443 + 0.0.0.0:4403
# Zombie:     LISTEN ... 0.0.0.0:443  + 0.0.0.0:4403   ← upstream template bit you
```

**Companion to Issue #8b06ebd** (status_bar/dashboard distinguishing
active-but-unreachable from running): the UI surfaces the zombie; the
sanitizer prevents it on first activation; the upstream issue to
`chrismyers2000/MeshAdv-Pi-Hat` will fix the source.


---

## Issue #61: `meshforge-map.service` daemon-mode SIGTERM deadlock (2026-05-18)

**Symptom**: `meshforge-map.service` stuck `deactivating` 5+ min on
moc2 during 2026-05-18 deploy. Sub-systems stopped cleanly at 10:55:05;
main thread refused to join (`futex_wait` per `/proc/PID/stack`); past
`TimeoutStopSec=300` → manual SIGKILL. Reliability backlog #3.

**Root cause**: stdlib `socketserver.BaseServer.shutdown()` invariant —
"must be called while serve_forever() is running in a different thread
otherwise it will deadlock." MeshForge's daemon path (`--daemon` →
`server.start()`) runs `serve_forever()` on the **main thread**
(`map_data_service.py`). Python routes signals to the main thread, so
the SIGTERM handler synchronously called `server.stop()` →
`_server.shutdown()` → blocked on `__is_shut_down.wait()`, which only
the `serve_forever` loop can set. Loop couldn't iterate because main
was inside `wait()`. Classic single-thread `socketserver` deadlock;
every restart since daemon-path introduction silently hit `TimeoutStopSec`
and got SIGKILL'd — operator only caught it during a watched deploy.

**Fix**: `_build_daemon_signal_handler()` (module-level, testable) —
handler spawns a daemon thread `map-shutdown` for `server.stop()`. Main
thread stays free to observe `__shutdown_request` next poll cycle and
exit `serve_forever()` naturally. Second signal during shutdown →
`os._exit(1)` so a wedged cleanup thread can't trap the process.

**Tests** (`tests/test_map_daemon_shutdown.py`, 6 assertions):
regression-pinning test deadlocks under pytest timeout if the handler
ever inlines `stop()` again; thread-shape lock (name `map-shutdown`,
daemon=True); escalation path; PID-file removal under both success
and exception; positive control proving cross-thread `ThreadingHTTPServer.shutdown()` works.

**Operator detection** (for the next instance of this class):
```bash
ssh <box> "ps -eLo pid,tid,comm | grep map-shutdown"
# Thread present during a hang → something else wedged.
# Thread absent during a hang → regression; re-run
# tests/test_map_daemon_shutdown.py.
```

**Fleet exposure**: every box running `meshforge-map.service` (moc,
moc1, moc2, moc3, volcanoai) was silently hitting this on every
restart. Post-fix expected shutdown <1s.
