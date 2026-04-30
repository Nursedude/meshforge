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

**Symptom**: After `ddb40de` the bridge stat `R→M` stayed at zero. Unit tests
proved the bytes-decode path was correct; real inbound LXMF still never fired
`_on_lxmf_receive`. Same root cause as Issue #37, but on the gateway side.

**Root cause**: MeshForge writes three client-only configs in
`/tmp/meshforge_rns_client/` (gateway, TUI RNS commands, map collector).
Each caused `RNS.Reticulum(configdir=…)` to generate a fresh transport
identity, and rnsd's `multiprocessing.connection` authkey is derived from
identity private bytes. Divergent identities → divergent authkeys → every
RPC to rnsd (`get_packet_rssi`, `first_hop_timeout`, etc.) fails
`AuthenticationError: digest sent was rejected`. On the gateway this
aborts inbound link-packet processing before LXMF delivery.

**Fix**: propagate rnsd's `rpc_key` into each client config when pinned.
Completes Issue #40's "Prevention / future work" option (b).

- `src/utils/paths.py` — `ReticulumPaths.get_shared_rpc_key()`: strict
  64-hex reader, lowercase-normalized, rejects malformed / commented-out
  / missing-file. 6 new tests.
- `src/commands/rns.py`, `src/gateway/node_tracker.py`,
  `src/utils/_map_collector_rns.py` — each appends the key when available.
  The `node_tracker.py` site is the R→M=0 unblocker.

**Operator preflight**: `grep '^  rpc_key' /etc/reticulum/config`. If absent,
generate (`openssl rand -hex 32`) and add under `[reticulum]`, then
`sudo systemctl restart rnsd` and any MeshForge consumers. Verify:
`grep '^  rpc_key\|^rpc_key' /tmp/meshforge_rns_client/config` matches.

**Correction (2026-04-21)**: initial implementation shipped with option name
`shared_instance_rpc_key`, which RNS 1.1.x silently ignores — only literal
`rpc_key` is parsed (see `RNS/Reticulum.py` line ~477). The helper and all
three callsites were renamed to write `rpc_key`. Any fleet box carrying the
old option name is equivalent to unpinned — apply `sed -i
s/shared_instance_rpc_key/rpc_key/` to every RNS config on the box (both
`/etc/reticulum/config` and, on split-identity boxes, `/root/.reticulum/config`
and `/home/*/.reticulum/config`) and restart rnsd.

**Prevention**: future client-config writers should call
`get_shared_rpc_key()` rather than hand-rolling a stanza. Worth adding a
preflight warning when the pinned key is absent — today's silent-None
regresses to Issue #37 behavior without a surface error. Cross-reference:
closes out Issue #37 (NomadNet side masked by wrapper `1856b58`) and
Issue #40 (bytes + TX landed in `ddb40de`; this is the inbound complement).


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
