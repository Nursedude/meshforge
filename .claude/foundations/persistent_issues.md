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

## RNS / LXMF are MeshForge-owned forks (SSOT, 2026-05-30)

RNS and LXMF are now **hard forks owned by MeshForge** (`Nursedude/reticulum`,
`Nursedude/lxmf`), pinned in `requirements/rns.txt` by tag **and** SHA with a
`# MF-FORK-PIN` SSOT line; `scripts/rns_version_check.py` gates the fleet on the
`+mf.N` marker. Fleet baseline: **rns `1.2.5+mf.2` / lxmf `0.9.4+mf.0`**. This is
the meta-resolution for the entire **rnsd-RPC fragility class** (#58/#61/#63/#68/
#69/#72): fragility that we used to work *around* in `utils/rns_init.py` can now be
fixed *at the source* (see #68 and #72 "FIXED AT SOURCE" notes below).

- **Wire-compat invariant (non-negotiable)**: never change crypto primitives
  (Ed25519/X25519/AES-256-CBC/Fernet) or the packet/announce/path-table wire
  format — that forks the *network*, not the code. Fork = maintenance + isolation.
- **Upstream tracking**: stock RNS ships off-GitHub now (Carrier Switch). To adopt
  a future release: `git merge <upstream-tag>` into `meshforge`, re-run Phase-1
  parity (version marker, rnsd ownership, gateway/map/tracer, **public-net interop
  proof**), canary one box, then fleet-roll. Full procedure in each fork's
  `FORK.md`; governance triggers (CVE-no-upstream / wire break / activity ceases)
  in [[project_upstream_dependency_governance_2026_05_29]].
- **MeshForge-side guards STAY** (`rns_init.py` probe, MF009/MF019 lint, watchdog
  `os._exit` backstop) as defense-in-depth — remove a backstop only after its
  in-library fix has held over a long soak.

See [[project_rns_fork_shipped_2026_05_30]] and
`.claude/plans/do-some-deep-research-delightful-dongarra.md`.

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
| #49 Lean node directory — split persistent dir from time-series (2026-04-28) | New `nodes` table in `node_history.db` (one row per network,node_id) decoupled from time-series `node_observations`; tiered retention (local 30d / external 7d) + 50k LRU cap; sticky source-origin promotion. Body in archive | `tests/test_node_history.py` (17) + diagnostics (3) |
| #50 Directory tier retention defeated by UPSERT `last_seen=now` (2026-04-30) | External-bulk republish reset tier clock every cycle; fix uses upstream `last_heard` + `MAX(nodes.last_seen, excluded.last_seen)` ON CONFLICT. Body in archive | `TestDirectoryUpstreamTimestamp` in `tests/test_node_history.py` (9 tests) |
| #51 Issue #50 wiring unreachable — meshcore parser emitted ISO-8601 not Unix epoch (2026-04-30) | Inline ISO→epoch normalization in `_parse_meshcore_public_node`. Tests must use real upstream payload shape. Body in archive | 5 new in `TestMeshCorePublicCollector` |
| #57 Gateway data-path watchdog — `bounded_call` over RNS RPC hot path (2026-05-17) | `bounded_call` wraps 11 RNS RPC sites; wedged peer trips circuit breaker + `os._exit(2)`; systemd restarts the gateway (better than silent hang). Body in archive | `test_wedge_events.py` (17) + `test_bounded_rpc.py` (19) + `test_circuit_breaker.py` (+6) |
| #12, #22, #23 | RNS configdir= (#12, lint MF009), don't overwrite meshtasticd `config.yaml` (#22, inverse companion of #58), post-install verification via `scripts/verify_post_install.sh` (#23). Bodies in archive. | — |
| #58 (2026-05-18) | Upstream HAT template `Webserver: Port: 443` smuggled into `config.d/`, silently moved meshtasticd off `:9443`. `_sanitize_hat_overlay` strips forbidden top-level blocks; 7 tests pin the moc3-actual broken template. Body in archive. | `tests/test_hat_overlay_sanitizer.py` |
| #59 (2026-05-18) | Federation polled permanently-failing peers (e.g. moc3 gateway-only) every cycle, drowning real failures. Exponential backoff per peer (`backoff_threshold=3`, cap `10×poll_interval`); `_compute_backoff` pure helper; `/api/status` exposes `in_backoff`/`backoff_multiplier`/`next_eligible_poll_ts`. 13 tests pin the math + isolation. Body in archive. Tier-2 long-outage cap added in #65. | `TestBackoff*` in `tests/test_map_federation.py` |
| #60 (2026-05-18) | Systemd sandbox path drift — hardened unit's `ReadWritePaths=` drifts from where code writes; service stays `active` while writes silently fail in an exception handler. Cure: runtime preflight (`sandbox_check.py` + `assert_writable_or_exit`) + MF017 lint audit of unit files for the three meshforge buckets. Body in archive. | `tests/test_sandbox_check.py` (15) + `tests/test_lint_mf017.py` (8) |
| #61 (2026-05-18) | `meshforge-map` daemon-mode SIGTERM deadlock — `socketserver.BaseServer.shutdown()` invariant broken because `serve_forever()` ran on the main thread (signal target). Fix: signal handler spawns `map-shutdown` daemon thread; main thread observes `__shutdown_request` naturally. Body in archive. | `tests/test_map_daemon_shutdown.py` (6) |
| #62 (2026-05-18) | `SettingsManager.save()` persisted entire merged `defaults | overrides` dict — first save baked every default as a saved value, blocking future code-default bumps (#56 timeout bump didn't take). Fix: `_explicit_keys` tracking + `stale_defaults={"k": [OLD]}` migration registry. Body in archive. | `TestExplicitKeyTrackingIssue62` (5) + `TestStaleDefaultsRegistryIssue62` (6) + `TestStaleFederationTimeoutMigrationIssue62` (3) in `tests/test_common.py` + `tests/test_map_collector_federation.py` |
| #63 (2026-05-18) | `delivery_counters` silent write-path failures (Issue #58 burned 18h before detection). Cure: startup preflight at `__init__` writes/reads `meta.preflight_*` and surfaces via `snapshot()["health"]`; runtime `consecutive_write_errors` counter; ERROR-throttle on first fail, INFO on recovery. Body in archive. | `tests/test_delivery_counters.py` (11): `TestPreflightHealthy` (3), `TestPreflightFailureSurfaces` (2), `TestRuntimeWriteErrorTracking` (4), `TestLastSuccessfulWriteTsHeartbeat` (2) |
| #70 (2026-05-22) | `meshforge-map` steady-state `http_local_unresponsive` wedges every few hours. Root cause: `/api/nodes/directory` 35 MB body's `json.dumps`+`gzip.compress` are C extensions that hold GIL ~6-10 s; concurrent federation polls pile up under it, `/healthz` stalls past the watchdog's 2 s probe. Fix: short-TTL response cache (`DirectoryResponseCache`, 5 s) with single-flight rebuild on `MapDataCollector`; cache hits skip json.dumps+gzip entirely. 18 tests including 8-thread/1-build single-flight race + 6-thread handler coalescing. Cache stats surface in `/api/status.directory.cache`. Pattern-audit found `/api/nodes/geojson` is the next instance (47 MB, ~35 s wedge under cold collect+gzip); deferred to Issue #71. Commits `1a52aab` (cache) + handler cache-stats surface. | `tests/test_directory_response_cache.py` (13) + `TestDirectoryHandlerCacheIssue70` (5) + `TestStatusExposesDirectoryCache` (2) |
| #71 (2026-05-22, GitHub #1168) | Pattern extended to the two remaining instances of the same GIL-bound serialization wedge class. `DirectoryResponseCache` promoted to `ResponseByteCache` (key widened to `Hashable` for tuple keys). New `_geojson_response_cache` (TTL=2s, keyed by `(bbox, region, preset)` since each materially alters the response) wraps `/api/nodes/geojson` (47 MB, ~35 s cold). New `_topology_response_cache` (TTL=5s, single `None` key — no query params) wraps `/api/network/topology` (24 MB, 1.4 s every request). Both surface stats blocks at `/api/status.geojson.cache` and `/api/status.topology.cache` matching the directory shape. Closes the last two known instances of the wedge class — any future `http_local_unresponsive` signal is now a NEW class. Commits `7d0b8e5` (class promotion + alias) + `000201f` (wire both endpoints). | `tests/test_response_byte_cache.py` (32: 19 mirrored + 7 new + 6 alias) + `TestGeojsonHandlerCacheIssue71` (5) + `TestTopologyHandlerCacheIssue71` (5) + `TestStatusExposesGeojsonCache` (2) + `TestStatusExposesTopologyCache` (2) |

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
| MF019 | `RNS.Reticulum()` constructed outside the chokepoint (use `open_reticulum()` from `utils.rns_init`; #68/#69) |

### Layer 2: Regression Guard Tests (`tests/test_regression_guards.py`)
- `TestTCPConnectionContract` — No new direct TCPInterface
- `TestFromradioContract` — TX uses `send_text_direct()`
- `TestServiceCheckContract` — Service state via `check_service()` only
- `TestPathHomeContract` — No `Path.home()` violations
- `TestNoShellTrue` — No `shell=True` in subprocess
- `TestKnownServicesConsistency` — KNOWN_SERVICES stays correct
- `TestOperatorValueContract` — No operator-specific values in source/templates/scripts/docs (MF014)
- `TestRNSReticulumChokepoint` — `RNS.Reticulum()` constructed only in `utils/rns_init.py` (MF019; #68 fail-open / #69 fail-loud)

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

## Issues #60–#63: bodies in archive (trimmed 2026-05-22)

Full investigation narratives + tests + operator detection recipes for
Issues #60 (sandbox path drift), #61 (map daemon SIGTERM deadlock), #62
(saved-defaults bump trap), and #63 (delivery_counters write canary)
live in `persistent_issues_archive.md`. Table rows above carry the
diagnostic essence; archive carries the reasoning. Trimmed here to
keep MF012 ≤40k chars headroom open for future entries.

---

## Issue #65: Two-tier federation backoff cap — long-outage cadence (2026-05-18)

**Class**: Issue #59 gave the federation collector exponential backoff
with a single `max_multiplier` (default 10× = 10 min between polls).
Operational survey on 2026-05-18 showed moc3 — a permanently gateway-
only box with no `/api/nodes/directory` listener — sitting at `cf=9
mult=10` within ~9 cycles and polling every 10 min forever (144
doomed polls/day). The same single value picks the *transient outage*
case (reboot/blip — needs ≤10 min recovery detection) and the
*permanent outage* case (gateway-only box, never coming back) — two
cases with different "right answers."

**Fix** (`src/utils/map_federation.py`): second-tier cap. New defaults
`DEFAULT_BACKOFF_EXTENDED_THRESHOLD=40` (failures past which tier-2
kicks in, ≈6 h continuous failure at tier-1 cap + default 60 s poll
interval) and `DEFAULT_BACKOFF_EXTENDED_MAX_MULTIPLIER=60` (≈1 h
between polls). `_compute_backoff` picks tier-2 cap when
`consecutive_failures ≥ extended_threshold`; tier-1 below. Exponential
ramp continues smoothly toward whichever cap is active. New
`backoff_extended_threshold` / `backoff_extended_max_multiplier`
constructor knobs; both clamp so tier-2 is always strictly past tier-1
(`threshold+1`, `≥ tier-1 cap`). Tier-2 entry logs at INFO exactly
once on the transition where prior multiplier ≤ tier-1 cap and new
multiplier > tier-1 cap — same one-shot pattern as the tier-1 engage
log. `backoff_multiplier=60` in `/api/status.federation.peer_status[]`
is the operator-visible signal "this peer has been gone for a long
time" — different shape from tier-1 `=10`.

**Why these numbers**: with default `poll_interval=60s` and tier-1 cap
of 10, a peer reaches the cap at cf≈7 (~30 min of continuous failure)
and then accumulates failures every 10 min. cf=40 corresponds to
about 6 hours of continuous failure — well past any reboot/network
blip, and the threshold above which "this is a real, long outage" is
a safe inference. Tier-2 cap of 60 (= 1 hr between polls) keeps the
peer in the rotation so genuine recovery is still detected, while
cutting wasted traffic 6× compared to tier-1 cap.

**Tests** (8 new in
`tests/test_map_federation.py::TestBackoffExtendedCapIssue65`):
below-extended-threshold uses tier-1 cap; at extended threshold steps
to tier-2 cap; far above stays at tier-2 cap; clamping (extended
threshold ≤ primary threshold ⇒ clamp; extended cap < tier-1 cap ⇒
clamp); recovery clears tier-2 state; tier-2 entry logs exactly once;
defaults lock test pinning the documented values. Existing
`test_far_above_threshold_caps_at_max` updated to set
`backoff_extended_threshold=10**6` so it still pins tier-1 behavior
in isolation.

**Operator detection recipe**:
```bash
curl -s http://<box>:5000/api/status | jq \
  '.federation.peer_status[] |
   select(.backoff_multiplier == 60) |
   {peer_name, hostname, consecutive_failures, last_error,
    next_eligible_poll_ts}'
# Empty = no peers in long-outage cadence.
# Rows = peers down ≥ ~6h continuous; check last_error/peer_name to
# decide if it's a known-gateway-only box vs an unexpected long outage.
```

**Closes the federation triad's last open knob** (reliability backlog
#1): Issues #54 (peer_name correlation), #55 (`/fleet/slo` budget),
#56 (timeout for big bodies), #59 (single-tier backoff), #64 (gzip +
size alarm), and now #65 (two-tier cap) collectively turn federation-
persistent-issues from a recurring class into a closed loop.


---

## Issue #64: `/api/nodes/directory` gzip negotiation + size-budget alarm (2026-05-18)

**Symptom**: `/api/nodes/directory` at 35 MB on moc; Issue #56 bumped
federation timeout 5→30s to fit; trajectory unbounded (~30× since the
1 MB original). Reliability backlog #5.

**Root cause**: `_serve_json()` already supported gzip when clients
sent `Accept-Encoding`, but `fetch_peer_directory` (`map_federation.py`)
never asked. urllib doesn't auto-decode like `requests` — needed
manual handling, so server-side gzip was wasted on the federation
hot path.

**Fix**:
- `map_federation.fetch_peer_directory` now sends
  `Accept-Encoding: gzip`, decodes via `gzip.decompress()`, with a
  `max_decompressed_bytes` cap (10× wire cap) for zip-bomb defense.
  Uncompressed responses still work for pre-fix peers.
- `node_history.record_directory_serialized_size()` called by
  `_serve_directory()` via a new `size_observer` callback on
  `_serve_json()`. `get_directory_stats()` returns
  `size_bytes_raw`/`_compressed`, `size_compression_ratio`,
  `size_alarm_threshold_bytes`, `size_alarm`. Threshold
  `DEFAULT_DIRECTORY_SIZE_ALARM_BYTES = 40 MB` (~80% of the federation
  client's 50 MB hard cap). Cache invalidates on record so operators
  see fresh bytes, not stale 5-min snapshots.

**Live measurement on moc** (2026-05-18): 35.7 MB raw → 4.7 MB on the
wire = **7.6× compression**. Federation poll wall-time well under the
60s cycle again.

**Tests** (15 new): `TestGzipNegotiationIssue64` (5) in
`tests/test_map_federation.py`; `TestDirectorySizeBudgetAlarmIssue64`
(6) in `tests/test_node_history.py`;
`TestServeJsonSizeObserverIssue64` (4) in
`tests/test_map_http_handler.py`.

**Operator recipes**:
```bash
# Wire savings:
curl -sv -H "Accept-Encoding: gzip" http://<box>:5000/api/nodes/directory \
  -o /dev/null 2>&1 | grep -E "Content-(Length|Encoding)"
# Size-budget gauge:
curl -s http://<box>:5000/api/status | jq '.directory | {size_bytes_raw,
  size_bytes_compressed, size_alarm, size_alarm_threshold_bytes}'
```

**Deferred**: cursor pagination + since-timestamp incremental sync
remain available if/when gzip's multi-year runway runs out.


---

## Issue #68: rnsd hard-wedge → meshforge-map main thread silent-stuck in `unix_stream_connect` (2026-05-20)

**Symptom**: moc1's `meshforge-map.service` was `active (running)` for
56 min but never bound `:5000`. Background threads (WebSocket :5001,
MQTT) kept logging normally. No error, no traceback. Visible only via
federation peer_status showing moc1 unreachable.

**Root cause**: rnsd hard-wedged — `active (running)` per systemd,
but `rnstatus` timed out, journal silent, kernel showed `SYN-SENT`
piling up on `@rns/default`. `MapServer.start()`
(`src/utils/map_data_service.py:590`) calls `init_rns_singleton()`
(`src/utils/_map_collector_rns.py:259`) → `_RNS.Reticulum(configdir=...)`
which `connect()`s the shared-instance Unix socket uncapped. When
rnsd doesn't accept, the syscall blocks indefinitely. Main thread
wedged → HTTP bind never ran. Background threads were started before
RNS init, so they kept logging.

**Detection** — kernel signals are the only honest ones:
```bash
PID=$(systemctl show meshforge-map.service -p MainPID --value)
sudo cat /proc/$PID/task/$PID/stack   # unix_wait_for_peer / unix_stream_connect
timeout 5 rnstatus || echo "rnsd RPC wedged"
sudo ss -xnp | grep '@rns/' | grep -c SYN-SENT   # >0 = clients piled up
```

**Recovery** (deterministic):
```bash
sudo systemctl stop meshforge-map.service     # release blocked connect()
sudo systemctl restart rnsd.service           # may SIGKILL after TimeoutStopSec
sudo systemctl start meshforge-map.service    # binds :5000 in ~1.4s
```

**Class**: variant of "service-running-but-not-serving" (cf #58, #61,
#63). Novel shape: *main thread* stuck in a kernel syscall while
background threads keep logging — all userspace signals say healthy.

**Prevention (IMPLEMENTED 2026-05-29, RNS T2-isolate arc sub-arc C)**: the
deferred pre-flight is now real and central. `utils/rns_init.py::open_reticulum`
— the project-wide guarded RNS-init chokepoint — runs a bounded `socket.AF_UNIX`
connect probe (`_probe_shared_instance_connect`, default 5s) against
`@rns/<instance>` BEFORE constructing. `settimeout()` makes the connect
interruptible (unlike RNS's internal uninterruptible connect that hangs the
thread); on timeout it returns `None` (degrade) so the caller keeps serving its
other legs instead of hanging. A passive `/proc/net/unix` presence scan can't
tell "accepting" from "wedged" — only the active connect can. All in-process
callers (map `init_rns_singleton`, gateway `_rns_bridge_connection`,
`node_tracker`, `commands/rns`) route through it; raw construction is banned by
**lint MF019 + `TestRNSReticulumChokepoint`**. The construct still carries the
`os._exit` watchdog backstop for the rare "probe passed, then wedged" race.
See `.claude/plans/rns_t2_isolate_arc.md`.

**FIXED AT SOURCE (2026-05-30, fork `rns 1.2.5+mf.1`, commit `6fb9a9ec`)**: the
root cause now has an in-library cure, not just a MeshForge-side guard. Since
RNS is a MeshForge-owned fork ([[project_rns_fork_shipped_2026_05_30]]),
`LocalClientInterface.connect()` brackets the shared-instance connect with
`settimeout(CONNECT_TIMEOUT=5s, env RNS_LOCAL_CONNECT_TIMEOUT)` and restores
blocking after — so a wedged rnsd raises `socket.timeout` (the reconnect loop
retries; `Reticulum.__init__` falls back to standalone) instead of hanging the
calling thread in an uninterruptible kernel `unix_stream_connect`. Fork test
`tests/meshforge_local_connect.py` (4). The `rns_init.py` probe + `os._exit`
backstop above STAY as defense-in-depth until this is soak-proven — remove a
backstop only after its in-library fix has held over a long soak.


---

## Issue #69: Foreign daemon claims `@rns/<instance>` shared-instance listener — every RNS client EOFs (2026-05-20)

**Symptom**: VolcanoAI's `/fleet/lab-rollup` showed every fleet box's
tracer reporting **100% failure** to VolcanoAI for the previous hour
(5 rows of `meshforge-* -> VolcanoAI ... 100.0 timeout=6` / `no-route=6`).
The cross-fleet whole-of-rollup signal pointed at VolcanoAI as the
broken target — `<box> -> moc/moc1/moc2/moc3` pairs were all 0% failure,
only VolcanoAI as target was failing. Federation HTTP `/api/status`
between every box was healthy in parallel.

**Root cause**: `meshforge-tracer.service` on VolcanoAI crashed with
unhandled `EOFError` from `rpc_connection.recv()` deep in RNS's
`_used_destination_data()`:

```
File ".../RNS/Reticulum.py", line 1239, in _used_destination_data
    response = rpc_connection.recv()
File ".../multiprocessing/connection.py", line 399, in _recv
    raise EOFError
```

The `@rns/<volcano ai rns>` abstract Unix socket LISTEN owner was NOT
rnsd — it was `python3 /opt/meshanchor/src/daemon.py start --foreground`
(PID 129485, then 200825 after restart), running as root via
`meshanchor-daemon.service` which had been silently enabled+running on
VolcanoAI since 2026-05-17. Both MeshAnchor and rnsd register
`share_instance = Yes` against the same `instance_name`; whichever
starts first claims the listener. When MeshAnchor's daemon wins, its
RPC subprocess answers tracer's destination-lookup queries with EOF
(dialect mismatch with rnsd's RPC protocol), the EOF unwinds through
`RNS.Identity.recall()` to `run_trace()`, kills the process. The whole
fleet's `<*> -> VolcanoAI` lab-tracer rows go 100% failure because no
`lab-tracer (VolcanoAI)` destination is announced while the tracer is
crashing every fire.

The cross-tenant invariant: **only one RNS host per `instance_name`
per box**. Two daemons that both run RNS with `share_instance = Yes`
under the same name will collide; the loser is whoever's RPC dialect
the listener can't speak.

**Fix (operational, VolcanoAI)**: per [[meshanchor-server-deployment]]
the canonical MeshAnchor production host is `meshanchor-server`, not
VolcanoAI. VolcanoAI's `meshanchor-daemon` data dirs hadn't been
written since 2026-05-08 (12 days idle, bound only to localhost:8081).
Stopped + disabled the unit; restarted rnsd; tracer immediately turned
green with all 6 peers `result=ok` (RTTs 1-9s, normal). `/opt/meshanchor`
remains as a working clone for code/mirror work.

**Fix (code prevention)**: `src/lab/_lab_common.py`:
- `_parse_ss_listener_line(line, instance_name)` — extract `(pid, cmd)`
  from `ss -xnpl` output anchored on the `@rns/<instance>` token.
- `_read_instance_name_from_config(configdir)` — parse `instance_name =`
  out of the configdir's `config` file; returns None silently when
  absent (first-ever RNS init).
- `check_rns_listener_owner(instance_name)` — runs `ss -xnpl`, reads
  `/proc/<pid>/cmdline` for the full cmdline (ss reports binary
  basename only, so "python3" identifies nothing), raises `RuntimeError`
  with the offending PID + cmdline + `sudo kill <PID>` recovery
  command when the cmdline matches none of the narrow allowlist
  `_RNS_LISTENER_ALLOWED_PATTERNS = ("rnsd", "reticulum")`. Returns
  None when no listener exists (RNS will create one).
- `init_reticulum_with_watchdog()` calls the preflight before the
  `RNS.Reticulum()` constructor. On collision the constructor never
  runs — the calling service fails loud with the operator-actionable
  RuntimeError instead of the 30+-minute-to-debug EOFError stack.

The allowlist is deliberately narrow. MeshAnchor's daemon cmdline
contains "meshanchor" but NOT "rnsd"/"reticulum" — so it gets caught.
Any future foreign RNS-hosting daemon will be caught the same way.

**Tests** (12 new in `tests/test_lab_common.py`):
- `test_parse_ss_listener_line_*` (3) — parser pins against real
  2026-05-20 `ss` output, including the rogue line.
- `test_preflight_passes_when_rnsd_owns_listener` — happy path.
- `test_preflight_raises_when_meshanchor_daemon_owns_listener` —
  pins the actual incident shape; asserts the error message includes
  `@rns/volcano`, the PID, `EOFError`, and a `sudo kill` recovery
  command.
- `test_preflight_passes_when_no_listener_present` — first-init.
- `test_preflight_handles_ss_missing` — container/minimal-environment
  fallback (skip silently, let RNS handle).
- `test_preflight_handles_process_vanished_between_ss_and_proc` —
  race window: ss saw the process but `/proc/<pid>/cmdline` reads
  fail because it exited. Still surfaces a diagnostic.
- `test_read_instance_name_from_config_*` (2) — config parsing.
- `test_init_reticulum_with_watchdog_invokes_preflight` — wire-up
  proof: the preflight is actually engaged in the prod init path.
- `test_init_reticulum_with_watchdog_propagates_preflight_failure`
  — preflight RuntimeError must NOT proceed to `RNS.Reticulum()`.

**Class**: 5th variant of "rnsd RPC fragility" — siblings #58
(HAT-overlay port hijack), #61 (single-thread socketserver deadlock),
#63 (delivery_counters write canary), #68 (unix_stream_connect main-
thread silent wedge). #69's distinguishing shape: not rnsd itself
malfunctioning, but a FOREIGN daemon hijacking the namespace rnsd
would have claimed.

**Operator detection recipe**:
```bash
# Who owns @rns/<instance> right now?
sudo ss -xnpl | grep "@rns/"
# Healthy: users:(("rnsd",pid=...))
# Hijacked: anything else — get the PID, then:
sudo cat /proc/<pid>/cmdline | tr '\0' ' '

# If a foreign daemon owns the listener, stop the foreign service,
# then `sudo systemctl restart rnsd.service` to reclaim, then restart
# the tracer (or whichever RNS client was crashing).
```

**Companion to fleet topology rule**: each box runs **one** RNS host
(rnsd). All RNS-using daemons on that box join as clients via
`share_instance = Yes` pointing at rnsd's `instance_name`. A box that
needs to host *both* MeshForge and MeshAnchor radio work (currently
not a supported topology) would need separate `instance_name`s and
careful coordination — but the canonical deployment is one project
per box.


---

## Issue #72: wedged rnsd RPC — rnstatus hangs though the socket accepts (2026-05-30)

**Class**: 6th variant of the rnsd-RPC fragility family (siblings #58,
#61, #63, #68, #69). The watchdog already had two RNS probes but a real
gap between them: `probe_rns_shared_instance_responsive` is a bare
`connect()` timer — it catches a connect that never completes (the #68
SYN-SENT pile-up) but returns **healthy the instant the socket
accepts**. `probe_rns_interface_down_peer_reachable` runs `rnstatus` but
**bails on any `parse_error`** (and its comment wrongly claimed
"shared-instance probes own that"). So the case where rnsd **accepts the
connection but the RPC round-trip hangs/EOFs** (`rpc_connection.recv()`
deep in `RNS.Reticulum`, the #69 mechanism) was unowned: `rnstatus`
itself times out, yet no probe surfaced it.

**Fix**: structured `RNSStatus.timed_out` flag set **only** on a
`run_rnstatus` subprocess TIMEOUT (`rns_status_parser.py`); `run_rnstatus`
gained a `timeout_s` arg (default 15s for existing callers). New
`probe_rns_rpc_responsive` (signal class `rns_rpc_unresponsive`,
severity wedge, subject `rnsd`, issue_ref 68) fires iff `timed_out` —
distinct from a fast error (binary missing / "no shared instance" /
refused), which leaves `timed_out` False so `service_inactive` owns
rnsd-down and RNS-less boxes never false-alarm. `run_all_probes` now
makes **one** bounded `run_rnstatus(timeout_s=8.0)` per tick and shares
the parsed result with both rnstatus-consuming probes (a wedged rnsd
can't stall the 30s tick with two long-timeout subprocesses).

**FIXED AT SOURCE (2026-05-30, fork `rns 1.2.5+mf.2`, commit `11227832`)**:
the watchdog above *detects* the wedge; the fork now *prevents* it. Since RNS
is a MeshForge-owned fork ([[project_rns_fork_shipped_2026_05_30]]), all 20
client-side RPC recvs route through a new `_rpc_recv()` helper that does
`poll(RPC_TIMEOUT=8s, env RNS_RPC_TIMEOUT)` before `recv()` — a
wedged-but-accepting rnsd raises `TimeoutError` and an EOF (the #69 mechanism)
fast-fails, instead of blocking forever in `rpc_connection.recv()`. The server
`rpc_loop` recv is untouched (it must block). `rnstatus` exercises this path,
so the canary green on VolcanoAI is direct proof. Fork test
`tests/meshforge_rpc_timeout.py` (3). The watchdog probe STAYS as
defense-in-depth (surfaces any residual wedge the timeout can't cure).

**Recovery**: `sudo systemctl restart rnsd.service`, then restart
RNS-using services (meshforge-map, meshforge-echo, tracer).

**Operator detection recipe**:
```bash
# rnstatus hangs but the listener still accepts? (the #72 shape)
timeout 8 rnstatus >/dev/null 2>&1 || echo "rnstatus RPC wedged"
sudo ss -xnpl | grep '@rns/'   # listener present + owned by rnsd
# Watchdog signal:
curl -s http://127.0.0.1:5000/api/status | jq \
  '.watchdog.signals[]? | select(.class=="rns_rpc_unresponsive")'
```

**Tests**: `TestProbeRnsRpcResponsive` in `tests/test_watchdog_probes.py`
(9): timed_out→wedge; healthy/binary-missing/clean-down→None; standalone
run_rnstatus path; plus parser-level timed_out flag + timeout_s plumbing.
The closed-enum gate `test_signal_classes_closed_enum_is_documented` was
bumped with the new class.
