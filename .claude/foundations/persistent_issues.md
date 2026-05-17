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

## Issue #40: RNS→Mesh bridge — bytes payload crash + wrong-topic MQTT downlink (2026-04-21)

**Resolved 2026-04-21**. Two independent defects on the R→M=0 path:
(1) `_process_rns_to_mesh()` crashed on `bytes` LXMF content (str ops on
bytes); (2) `publish_to_mqtt()` topic shape didn't match meshtasticd's
literal `mqtt` channel-name subscription (`msh/{REGION}/2/json/mqtt/#`,
firmware PR #3183 convention). **Fix**: decode bytes→str at entry to
`_process_rns_to_mesh()` + `_requeue_failed_message()`; reroute
MQTT-bridge-mode enqueue from `destination="mqtt"` to `destination="meshtastic"`
(uses the Issue #29 HTTP `/api/v1/toradio` SSOT instead of MQTT downlink).
`publish_to_mqtt()` is now dead code, retained for hypothetical pure-MQTT
gateway. Inbound RNS link-packet auth follow-up landed in Issue #41.
**Tests** in `tests/test_rns_bridge.py`: `test_bytes_content_is_decoded`,
`test_bytes_content_with_at_prefix`, `test_invalid_utf8_uses_replacement`,
`test_mqtt_bridge_mode_enqueues_to_meshtastic`,
`test_bytes_content_serializes_as_str`.
**Full body**: `persistent_issues_archive.md`.

---

## Issue #41: rpc_key pinning closes the Issue #37/#40 gateway inbound gap (2026-04-21)

**Resolved 2026-04-21**. MeshForge's three client-only RNS configs
(gateway, TUI commands, map collector) each generated a fresh
transport identity, producing divergent multiprocessing-RPC authkeys
that rnsd rejected. **Fix**: propagate rnsd's `rpc_key` into each
client config — `src/utils/paths.py:ReticulumPaths.get_shared_rpc_key()`
+ callers in `src/commands/rns.py`, `src/gateway/node_tracker.py`,
`src/utils/_map_collector_rns.py`. RNS 1.1.x parses only the literal
`rpc_key` name (not `shared_instance_rpc_key`); migration fleets must
`sed s/shared_instance_rpc_key/rpc_key/` every RNS config.
**Full body**: `persistent_issues_archive.md`.


---

## Issue #47: NomadNet operator confusion — two kinds of conversation in a gateway-equipped fleet (2026-04-25)

**Symptom**: After single-gateway topology + multi-recipient deploy,
operators report "fleet-host-1/fleet-host-2/fleet-host-3 NomadNets don't see each other."
Mesh↔NomadNet gateway path is healthy, `rnpath` resolves peer LXMF
hashes, every box's `~/.nomadnetwork/storage/directory` has the current
peer hashes. Substrate is fine.

**Root cause — UX, not transport.** NomadNet's **Conversations** panel
populates only when an LXMF *message* arrives/sends; **Network / Known
Nodes** populates from `lxmf.delivery` *announces*. Peers that have
only exchanged announces show under Known Nodes but not Conversations.
The peer is one keystroke away, not missing.

**Two-conversation rubric** in a gateway-equipped fleet:

- **MeshForge Gateway (\<hostname\>)** — single thread indexed by the
  gateway's hash (e.g. `f68c2f56…`). All Mesh-bridged content,
  `[Mesh:xxxx]` prefixed (Issue #35). One per gateway.
- **Peer-NomadNet conversations** — one per operator, indexed by their
  `lxmf.delivery` hash (e.g. `522c4ac1…`). Direct LXMF over RNS
  Transport, no gateway, no `[Mesh:]` prefix.

Both coexist; neither replaces the other.

**Operator seeding flow** — Known-Nodes → Conversations:
1. `ssh <box> -t 'tmux attach -t nomadnet'`
2. Navigate to Network/Known Nodes panel (help bar shows the keybind).
3. Highlight peer ("meshforge fleet-host-2 nomad"); press "Converse" key.
4. Send a one-line hello. Recipient's Conversations panel auto-creates
   within ~30s. One round trip per pair seeds both directions.

**Verification — peer LXMF didn't accidentally route via gateway**:
```bash
ssh fleet-host-3 'sudo journalctl -u meshforge-gateway --since "5 min ago" \
  --no-pager | grep -E "Bridge|delivery confirmed"'
```
Should stay quiet during peer-to-peer sends. Gateway in the data path
for peer NomadNet would be a topology bug.

**Future**: TUI helper to walk Known-Nodes and seed conversations
(matches `feedback_user_audience_ux_bar.md`). Add to
`docs/GATEWAY_DEPLOYMENT.md`. Open question: drop legacy
`[[Regional RNS]]` TCPClient on fleet-host-1/fleet-host-2 to force the fleet-host-3 hub
path; today both interfaces work, RNS picks whichever responds first.


---

## Issue #48: Phase-2 migration inherits source WAL → cold-start stall (2026-04-27)

**Symptom**: After Phase-2 migration (User=root → User=$op), service
restart hung ~3 min in D-state (`ext4_sync_file`) before binding
`:5000`. WAL was 350 MB on the migrated DB.

**Cause**: Migration cp's `*-wal`/`*-shm` sidecars verbatim. New
service's first open checkpoints the inherited WAL → multi-min SD
fsync stall. Phase 1.5 dedup bounds observation-table writes, not
WAL between checkpoints.

**Fix** (Phase A of map-arc): run `PRAGMA wal_checkpoint(TRUNCATE)` on
source DB before cp. WAL → 0 bytes; new service first-open is fast.
Idempotent on clean DB; non-fatal on busy-reader (warns, copies as-is).

**Tests**: `tests/test_migrate_map_service.py` — 6 assertions
(PRAGMA contract, script structure, idempotence, non-fatal path,
end-to-end via subprocess).

**Companion deferred** to Phase D (F3 in `project_map_arc_findings`):
bind-first + 503/warming handler swap so cold starts surface as
"warming" rather than "connection refused" to monitoring.

**Operator diagnosis — stalled `:5000`**:
```bash
sudo cat /proc/$(systemctl show meshforge-map -p MainPID --value)/stack
# ext4_sync_file → SD fsync stall (WAL replay)
ls -lh ~/.local/share/meshforge/node_history.db-wal
# >50MB = active replay, resolves 1-3 min on SD
```


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

## Issue #53: meshforge-map.service stale daemon → :4403 contention starves :9443 web UI (2026-05-02)

**Symptom**: User reported `<ip>:9443` "not functional" on a fleet box.
The web UI's HTML shell loaded but the SPA's data calls hung. Survey
showed `python pid=N (utils.map_data_service) ESTABLISHED` to
`127.0.0.1:4403` on moc, moc1, and volcanoai (moc2 had a
CLOSE-WAIT remnant; moc3 unaffected — gateway profile, no
meshforge-map). `/api/v1/fromradio?all=true` returned `size=0` on
the affected boxes — exactly the
`project_tcp_contention_pattern` shape.

**Root cause**: stale daemon, not a current-code regression. The
running `meshforge-map.service` had been live since 2026-05-01 (~21h
on moc1) and was loading pre-fix module code. Subsequent fleet
syncs updated the working tree to current `main` but did NOT
restart `meshforge-map.service` — `scripts/fleet_sync.sh` only
restarts `meshforge` (gateway, this-repo) and `meshforge-maps`
(sister :8808 service); the singular `meshforge-map.service`
(:5000 map from this repo) is omitted from the restart loop.

The map collector's HTTP path
(`_collect_via_http` in `src/utils/_map_collector_meshtastic.py`)
talks to meshtasticd on :9443. When :4403 is contended, that fetch
is starved → returns empty → the collector falls through to
`_collect_via_tcp_interface` (line 86), which opens its OWN
:4403 socket via `get_connection_manager`, which the singleton
caches between cycles → self-reinforcing starvation.

**Fix** (immediate): `sudo systemctl restart meshforge-map.service`
on each affected box. Post-restart, current code keeps :4403 clear
across at least one full collect cycle (verified 75s on each box,
moc / moc1 / volcanoai).

**Operator diagnostic**:
```bash
sudo ss -tnp | grep ":4403" | grep python   # any output = contention
curl -sk -o /dev/null -w "%{size_download}\n" \
    "https://127.0.0.1:9443/api/v1/fromradio?all=true" -m 5
# size=0 with python on :4403 = the failure mode
# size=0 with no python on :4403 = normal empty-state (benign)
```

**Prevention** (shipped 2026-05-02, commit `660f26f`):
`scripts/fleet_sync.sh` now restarts `meshforge-map.service`
alongside the existing `meshforge` and `meshforge-maps` units —
three sync_repo calls instead of two. The second call against
`/opt/meshforge` re-pulls (no-op, ~50ms LAN) and try-restarts the
map daemon only when it's already active, preserving operator-
disabled state. Verified end-to-end on all five fleet boxes:
PIDs changed and :4403 stayed clear through one full collect
cycle. Tracker memory:
`project_meshforge_map_stale_daemon_pattern.md`.


---

## Issue #54: Federation peer_status keyed by IP; cross-view diagnostics need name (2026-05-17)

**Symptom**: Operator-visible state across three fleet diagnostic
surfaces uses different identifiers for the same peer, forcing manual
IP↔hostname mapping during incidents:
- LXMF tracer leaderboard (`lab-traffic-rollup-leaderboard.md`) — fleet
  hostnames (`fleet-host-2`, etc.).
- MA `/fleet/rollup` — names from MA's `fleet.json` (`<host>-MF`).
- MF `/api/status.federation.peer_status[]` — `hostname` was the
  literal connection target, which is the IP from MF's `fleet.json`.

When one box goes black-hole (the 2026-05-17 trigger: every peer
reported 100% tracer timeout to one fleet host while MF federation
showed the same host as `ok=true` 208 ms), the operator's first
instinct — search `/api/status` for that hostname — returned no hits
because federation only knew the IP. Diagnosing took longer than the
underlying state change warranted.

**Root cause**: `_bootstrap_federation_peers` in
`src/utils/map_data_collector.py` extracts `peers.<name>.ip` from
`fleet.json` and discards the name. `FederationCollector` accepted only
a flat list of endpoints, with no slot for a friendly identifier.

**Fix**:
- `FederationPeerStatus.peer_name: Optional[str] = None` (the carry-
  through field). Serialized by `_serve_status()` alongside `hostname`.
- `FederationCollector(..., peer_names: Optional[Dict[str, str]] = None)`
  — endpoint → friendly-name mapping. Stamps `peer_name` on the initial
  per-peer status entries and on every status that flows through
  `poll_once` (success, soft-fail, and executor-crash branches all
  refreshed — easy to miss the crash branch, hence the explicit test).
- New `MapDataCollector._load_fleet_peer_names()` reads `fleet.json` at
  `_init_federation` time and passes the mapping. Cached settings
  schema unchanged — backwards-compatible.

**Diagnostic flow after this fix**:
```bash
curl -s http://localhost:5000/api/status | \
  jq '.federation.peer_status[] | select(.ok == false) |
      {peer_name, hostname, last_error, consecutive_failures}'
# Now returns rows operators can correlate against the tracer
# leaderboard and MA rollup without a fleet.json round-trip.
```

**Tests**: 6 new in `TestPeerNamePlumbing` (`tests/test_map_federation.py`)
— construction-time stamping, default-None for unmapped peers, name
stamped after successful poll, after soft-fail, after executor crash
(the easy-to-miss branch), and the mixed-fleet case where one peer is
mapped and another isn't (name doesn't leak across rows).

**What this does NOT fix**: the underlying RNS/LXMF isolation that left
one fleet host's tracer column at 100% timeout. That's an upstream
transport state — the box itself remained healthy at the HTTP layer
throughout, and federation continued to poll its `/api/nodes/directory`
successfully. The fix here is purely operator-visibility: when the
*next* black-hole happens (and per
`project_fleet_monitor_reliability_assessment.md` it will), the cross-
view correlation is no longer guesswork.


---

## Issue #55: `/fleet/slo` serial systemctl probes consumed MA's peer-fetch budget (2026-05-17)

**Symptom — `peer fetch: timeout: timed out`** in MA `/fleet/rollup`
for one or more MF peers, even when those peers were healthy at the
HTTP layer. Reproduced 2026-05-17: moc2's `/fleet/slo` from VolcanoAI
returned `200 OK size=1106` but took **2.43 s** — only 19% headroom
under MA's `PEER_HTTP_TIMEOUT_S = 3.0`. Any contention (a concurrent
dashboard tick, a slow disk read in the schedules block) tipped it
past 3 s and the host dropped out of the rollup with the "peer fetch:
timeout" string.

**Root cause**: `_services_rollup()` ran 6 × `_systemctl_state()` =
6 × `subprocess.run(["systemctl", "is-active", svc], timeout=3)`
serially. On Pi-class hardware each fork+systemd-RPC round trip
costs ~300–400 ms. Six in series = ~2 s before any other block
(`_probe_radio`, `_schedules_block`, `_path_table_summary`, etc.)
got a turn. The 3 s MA budget had no margin.

**Fix** (`src/utils/fleet_snapshot.py`):
1. Module-level **TTL cache** for `_systemctl_state` (default 2.0 s).
   MA polls `/fleet/slo` every 5–15 s; a 2 s TTL coalesces overlapping
   handlers (dashboard fast-tick + rollup slow-tick that fire within
   the same window) so each unit gets probed at most once per cache
   window. `_systemctl_state_uncached` is the bypass primitive; tests
   that want a guaranteed fresh fork use it.
2. **Parallel fanout** via `_probe_services_parallel(units)` — small
   `ThreadPoolExecutor` (cap = 6 workers, one per service). Total
   wall-time drops from `Σ(unit_cost)` to `max(unit_cost)` ≈ 400 ms.
   `subprocess.run` releases the GIL during the wait, so concurrency
   is real.

Together: cold `/fleet/slo` is now ~400 ms on Pi (~6× headroom under
the 3 s MA timeout); a warm second call within 2 s is sub-50 ms (all
services served from cache, no forks).

**Tests** (10 new in `tests/test_fleet_snapshot.py`):
- `TestSystemctlStateCache` (6 tests): cache hit skips subprocess; per-
  unit independence; `ttl_s=0` bypasses cache; TTL expiry triggers
  refresh; default-TTL value locked at 2.0 s; `_systemctl_state_uncached`
  bypass exists and works.
- `TestProbeServicesParallel` (5 tests): every unit returns; empty
  input is a no-op (no executor spawn); per-unit states stay distinct;
  a worker that raises gets normalized to `"not_running"` rather than
  corrupting the result dict; `_services_rollup` is wired to the
  parallel path (`patch` interception confirms it's no longer the
  serial dict-comprehension).
- Autouse fixture `_clear_systemctl_cache` resets state between tests
  so the cache doesn't leak across them.

**Operator verification**:
```bash
# Before fix (any Pi-class peer): ~2.4 s
curl -sS -m 8 -o /dev/null \
  -w "%{time_total}s code=%{http_code}\n" \
  http://<peer-ip>:5000/fleet/slo
# After fix on cold cache: ~400 ms.
# Hit it again within 2 s: ~50 ms (all units cached).
```

**Companion to Issue #54**: #54 added operator-visibility for a peer
that drops out of the rollup (peer_name correlation). #55 keeps the
peer *in* the rollup in the first place by removing the latency cliff
that was tipping marginal hosts over MA's timeout. Both close the
"federation persistent issues" gap from different angles —
diagnostics + raw latency budget.


---

## Issue #56: Federation peer timeout sized for ancient directories (2026-05-17)

**Symptom**: After deploying #55 and restarting moc3, federation
showed `consecutive_failures: 1, last_error: "TimeoutError: timed out"`
on moc (and reciprocally, moc → moc3). All four MF peers had healthy
`/fleet/slo` (sub-200 ms post-#55), so the diagnostic puzzle: which
endpoint federation actually hits is `/api/nodes/directory`, not
`/fleet/slo`.

**Root cause**: `DEFAULT_TIMEOUT = 5.0` in `src/utils/map_federation.py`
was set when `/api/nodes/directory` returned ~1 MB. After Issue #49
(directory split) and the external-bulk collectors (Issue #50/#51 —
meshcore_public, worldmap, etc.), the directory has grown ~30× —
moc's response is **35 MB in 5.37 s** measured 2026-05-17. urllib's
`urlopen(timeout=)` is a per-recv timeout, not a whole-request
timeout, so a stream that takes >5 s total can succeed as long as
each chunk arrives within 5 s; one slow chunk and the whole fetch
fails. moc ↔ moc3 saw symmetric `TimeoutError` because each box's
directory was too big for the other's 5 s budget.

**Fix**:
- `map_federation.DEFAULT_TIMEOUT`: 5.0 → **30.0** s.
- `map_data_collector` bootstrap default `federation_timeout_seconds`:
  5 → 30. `TestDefaultTimeout` (3 tests) pins both, asserts they stay
  in sync, and pins `DEFAULT_TIMEOUT < DEFAULT_POLL_INTERVAL` so a
  future bump can't make poll cycles overlap themselves.
- Operator can override per-box via `map_settings.json`.

The 50 MB `DEFAULT_MAX_RESPONSE_BYTES` cap still protects against
unbounded directory growth — this isn't a slippery slope to a 60 s
timeout. Follow-up roadmap (gzip on `/api/nodes/directory`,
pagination) tracked in `project_federation_peer_name_correlation`
memory.

**Companion to Issue #55**: both are "timeout sized for a response
that has since grown." #55 fixed `/fleet/slo` by making it faster;
#56 gives the federation collector more time to consume the bigger
`/api/nodes/directory`. Pairing matters — either alone leaves a
class of timeouts uncovered.


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
