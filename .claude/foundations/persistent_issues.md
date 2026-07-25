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
`+mf.N` marker. Fleet baseline: **rns `1.2.5+mf.5` / lxmf `0.9.4+mf.0`** (rolled
2026-06-09, all 7 rnsd hosts). This is the meta-resolution for the entire
**rnsd-RPC fragility class** (#58/#61/#63/#68/#69/#72): fragility we used to work
*around* in `utils/rns_init.py` is now fixed *at the source*. Shipped: `+mf.1` #68
connect-hang, `+mf.2` #72 RPC-hang (see "FIXED AT SOURCE" notes below), `+mf.3`
bounds `detach_interfaces()` (PARTIAL — a second shutdown-path wedge remained).
**`+mf.4` (2026-06-01) root-cause cure for that second path**: `logging_lock`
Lock→RLock + signal handlers defer detach off signal context; canary-verified ~1s
clean stop on moc1. **The `rnsd.service.d/10-stop-timeout.conf` 15s cap + the mf.3
bound STAY as defense-in-depth until mf.4 is fleet-soak-proven.**
**`+mf.5` (2026-06-09) cures the #69 stranded-client class**: a wanted-host client
(rnsd that lost the `@rns` bind race) exits **75** after ~24s when its host dies
with NO listener remaining (`/proc/net/unix`; unknown ≠ absence) → systemd restarts
it into the host role. OPT-IN via `RNS_EXIT_ON_HOST_LOSS=1` — rnsd unit drop-in
`20-exit-on-host-loss.conf` ONLY, fleet-deployed; embedded clients keep stock
reconnect-forever. Canary moc3: deliberate inversion self-healed in 29s. ⚠️ Still
do NOT rapid-cycle rnsd restarts fleet-wide — a 15s-hang+SIGKILL plus slow rebind
opens the `@rns` race window; mf.5 makes a stranding self-healing (~30s outage),
but space restarts and verify host-binding before the next box anyway.

- **Wire-compat invariant (non-negotiable)**: never change crypto primitives
  (Ed25519/X25519/AES-256-CBC/Fernet) or the packet/announce/path-table wire
  format — that forks the *network*, not the code. Fork = maintenance + isolation.
- **Upstream tracking**: stock RNS ships off-GitHub now (Carrier Switch). To adopt
  a future release: `git merge <upstream-tag>` into `meshforge`, re-run Phase-1
  parity (version marker, rnsd ownership, gateway/map/tracer, **public-net interop
  proof**), canary one box, then fleet-roll. Full procedure in each fork's
  `FORK.md`; governance triggers (CVE-no-upstream / wire break / activity ceases)
  in [[project_upstream_dependency_governance_2026_05_29]].
- **1.3.8 / 1.0.1 merge arc — PHASES 1-3 DONE, CANARY LIVE ON moc3, NOT
  FLEET-ROLLED (2026-07-17)**. Full record:
  `.claude/research/rns_138_merge_eval_2026_07_16.md`.
  Merges on **integration branches** `meshforge-138` (RNS `1.3.8+mf.0`) +
  `meshforge-101` (LXMF `1.0.1+mf.0`), pushed; **deployed `meshforge` + fleet
  still run `1.2.5+mf.5`/`0.9.4+mf.0`** and the SSOT pins (`requirements/rns.txt`
  MF-FORK-PIN, `rns_version_check`) are UNCHANGED — do NOT bump until the roll or
  every un-upgraded box fails. Wire-compat cleared + **interop PROVEN 07-17**
  (cross-version LXMF round-trips: direct, public-transport-node, real-net
  tracer). **Findings: (1) #72 NOT subsumed** — `_rpc_recv`
  re-ported onto the msgpack framing (21 sites). **(2) mf.4 re-ported, not
  carried** — RLock flaked LOG_EXTREME (A/B-proven; plain Lock, fallback
  re-log outside it). LXMF byte-identical, MF↔MA lockstep safe.
  ⚠️ **moc3 IS THE CANARY: all 3 envs flipped (user site + root + nomadnet pipx
  venv — silently stock 1.1.4, invisible to the drift probe; check every box's
  at roll). Its `rns_version_drift` page is DELIBERATE — do NOT converge moc3
  back to the pin during the soak.** mf.5 exit-75 live-fired + self-healed the
  #69 drill race. **REMAINING: multi-day soak → per-box roll (rnsd + ALL
  clients + pipx venvs together) → ff + bump SSOT.**
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
| #49 Lean node directory (2026-04-28) | Persistent `nodes` table split from time-series; tiered retention (local 30d / external 7d) + 50k LRU cap; sticky source-origin promotion. Body in archive | `tests/test_node_history.py` (17) + diagnostics (3) |
| #50 Directory tier retention defeated by UPSERT `last_seen=now` (2026-04-30) | Fix: upstream `last_heard` + `MAX(nodes.last_seen, excluded.last_seen)` ON CONFLICT. Body in archive | `TestDirectoryUpstreamTimestamp` in `tests/test_node_history.py` (9 tests) |
| #51 meshcore parser emitted ISO-8601 not Unix epoch (2026-04-30) | Inline ISO→epoch normalization in `_parse_meshcore_public_node`. Body in archive | 5 new in `TestMeshCorePublicCollector` |
| #57 Gateway data-path watchdog (2026-05-17) | `bounded_call` wraps 11 RNS RPC sites; wedged peer trips breaker + `os._exit(2)` → systemd restart. Body in archive | `test_wedge_events.py` (17) + `test_bounded_rpc.py` (19) + `test_circuit_breaker.py` (+6) |
| #12, #22, #23 | RNS configdir= (#12, lint MF009), don't overwrite meshtasticd `config.yaml` (#22, inverse companion of #58), post-install verification via `scripts/verify_post_install.sh` (#23). Bodies in archive. | — |
| #58 (2026-05-18) | Upstream HAT template `Webserver: Port: 443` smuggled into `config.d/`, silently moved meshtasticd off `:9443`. `_sanitize_hat_overlay` strips forbidden top-level blocks; 7 tests pin the moc3-actual broken template. Body in archive. | `tests/test_hat_overlay_sanitizer.py` |
| #59 (2026-05-18) | Federation per-peer exponential backoff (`backoff_threshold=3`, cap `10×poll_interval`); `/api/status` exposes backoff fields; tier-2 cap in #65. Body in archive. | `TestBackoff*` in `tests/test_map_federation.py` |
| #60 (2026-05-18) | Systemd sandbox path drift — `ReadWritePaths=` drifts from where code writes; writes silently fail while the service stays `active`. Cure: runtime preflight (`sandbox_check.py` + `assert_writable_or_exit`) + MF017 lint audit. Body in archive. | `tests/test_sandbox_check.py` (15) + `tests/test_lint_mf017.py` (8) |
| #61 (2026-05-18) | `meshforge-map` daemon-mode SIGTERM deadlock — `socketserver.BaseServer.shutdown()` invariant broken because `serve_forever()` ran on the main thread (signal target). Fix: signal handler spawns `map-shutdown` daemon thread; main thread observes `__shutdown_request` naturally. Body in archive. | `tests/test_map_daemon_shutdown.py` (6) |
| #62 (2026-05-18) | `SettingsManager.save()` baked every default as a saved value, blocking code-default bumps (#56 timeout bump didn't take). Fix: `_explicit_keys` tracking + `stale_defaults={"k": [OLD]}` migration registry. Body in archive. | `TestExplicitKeyTrackingIssue62` (5) + `TestStaleDefaultsRegistryIssue62` (6) + `TestStaleFederationTimeoutMigrationIssue62` (3) in `tests/test_common.py` + `tests/test_map_collector_federation.py` |
| #63 (2026-05-18) | `delivery_counters` silent write-path failures (Issue #58 burned 18h before detection). Cure: startup preflight at `__init__` writes/reads `meta.preflight_*` and surfaces via `snapshot()["health"]`; runtime `consecutive_write_errors` counter; ERROR-throttle on first fail, INFO on recovery. Body in archive. | `tests/test_delivery_counters.py` (11): `TestPreflightHealthy` (3), `TestPreflightFailureSurfaces` (2), `TestRuntimeWriteErrorTracking` (4), `TestLastSuccessfulWriteTsHeartbeat` (2) |
| #64 + #65 (2026-05-18) | Federation directory gzip (35→4.7 MB wire, `size_alarm`) + two-tier backoff cap (`backoff_multiplier=60` = permanent-outage tell). Bodies in archive | `TestGzipNegotiationIssue64` + `TestBackoffExtendedCapIssue65` |
| #70 (2026-05-22) | `meshforge-map` steady-state `http_local_unresponsive` wedges every few hours. Root cause: `/api/nodes/directory` 35 MB body's `json.dumps`+`gzip.compress` are C extensions that hold GIL ~6-10 s; concurrent federation polls pile up under it, `/healthz` stalls past the watchdog's 2 s probe. Fix: short-TTL response cache (`DirectoryResponseCache`, 5 s) with single-flight rebuild on `MapDataCollector`; cache hits skip json.dumps+gzip entirely. 18 tests including 8-thread/1-build single-flight race + 6-thread handler coalescing. Cache stats surface in `/api/status.directory.cache`. Pattern-audit found `/api/nodes/geojson` is the next instance (47 MB, ~35 s wedge under cold collect+gzip); deferred to Issue #71. Commits `1a52aab` (cache) + handler cache-stats surface. | `tests/test_directory_response_cache.py` (13) + `TestDirectoryHandlerCacheIssue70` (5) + `TestStatusExposesDirectoryCache` (2) |
| #71 (2026-05-22, GitHub #1168) | Pattern extended to the two remaining instances of the same GIL-bound serialization wedge class. `DirectoryResponseCache` promoted to `ResponseByteCache` (key widened to `Hashable` for tuple keys). New `_geojson_response_cache` (TTL=2s, keyed by `(bbox, region, preset)` since each materially alters the response) wraps `/api/nodes/geojson` (47 MB, ~35 s cold). New `_topology_response_cache` (TTL=5s, single `None` key — no query params) wraps `/api/network/topology` (24 MB, 1.4 s every request). Both surface stats blocks at `/api/status.geojson.cache` and `/api/status.topology.cache` matching the directory shape. Closes the last two known instances of the wedge class — any future `http_local_unresponsive` signal is now a NEW class. Commits `7d0b8e5` (class promotion + alias) + `000201f` (wire both endpoints). | `tests/test_response_byte_cache.py` (32: 19 mirrored + 7 new + 6 alias) + `TestGeojsonHandlerCacheIssue71` (5) + `TestTopologyHandlerCacheIssue71` (5) + `TestStatusExposesGeojsonCache` (2) + `TestStatusExposesTopologyCache` (2) |
| #68 (2026-05-20) | rnsd hard-wedge → map main thread silent-stuck in `unix_stream_connect`; bg threads kept logging, `:5000` never bound. Cure: bounded AF_UNIX probe in `open_reticulum()` chokepoint (MF019) + FIXED AT SOURCE in fork `rns 1.2.5+mf.1` (`LocalClientInterface.connect` settimeout). Detection/recovery recipes + body in archive. | `TestRNSReticulumChokepoint` + fork test `meshforge_local_connect.py` (4) |
| #74 (2026-06-06, stall-probe fix 06-09) | Gateway health core: write-only circuit breaker, NTP-backstep recovery freeze (→`time.monotonic()`), dead write-canary branch, 2 probes (`queue_backlog`, `delivery_confirmation_stall`); 06-09 stall probe stopped comparing disjoint protocol populations. Body in archive. DISPLAY residual FIXED 2026-06-15: `confirmation_rate` was cross-population (`confirmed/sent`, read 1.64=">164%" while mesh had zero proof) → now `confirmed/(confirmed+failures)` over the confirmable pop (bounded, live 0.99) + `unconfirmable_sent` surfaces the mesh blind spot (`compute_confirmation_view`; snapshot+pulse fallback; failure-set pinned to watchdog). Real mesh-completeness still = ACK consumption (T2 step 4). | `TestComputeConfirmationView` + `TestConfirmationRate` (rewritten) + `TestDeliveryFailureReasonsParity` + circuit/probe tests |

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
| MF021 | `subprocess`/`systemctl`/`os.system`/`Popen`/`shell=True` in mini-dudeai engine + built-in sources/actions (observation-only invariant; #79) |

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

---


## Issue #69: Foreign daemon / boot race claims `@rns/<instance>` — RESOLVED, body in archive (trimmed 2026-06-07)

5th rnsd-RPC-fragility variant. MeshAnchor daemon hijacked VolcanoAI's `@rns`
listener (every RNS client EOF'd, fleet tracer 100% fail to VolcanoAI); boot-race
addendum (06-06, `84a79ca`): a client starting before rnsd boot-claims the
listener — chokepoint now waits for enabled rnsd + the spaced-instance ss-
truncation parser fix. Prevention: `check_rns_listener_owner` preflight in
`_lab_common.py` (allowlist rnsd/reticulum, fail-loud RuntimeError), 12 tests
in `tests/test_lab_common.py`. Detection recipe + invariant (one RNS host per
instance_name per box) in `persistent_issues_archive.md`.
Quick check: `sudo ss -xnpl | grep "@rns/"` — owner must be rnsd.
---

## Issue #72: wedged rnsd RPC — rnstatus hangs though the socket accepts (2026-05-30)

6th rnsd-RPC-fragility variant — rnsd **accepts the connection but the RPC round-trip hangs/EOFs**, a gap between the two existing RNS probes. Cure: `RNSStatus.timed_out` (set only on a `run_rnstatus` subprocess TIMEOUT) + `probe_rns_rpc_responsive` (`rns_rpc_unresponsive`, wedge), and **FIXED AT SOURCE** in fork `rns 1.2.5+mf.2` (`_rpc_recv()` poll(8s) before recv). Recovery: restart rnsd then RNS-using services. Quick check: `timeout 8 rnstatus >/dev/null 2>&1 || echo wedged`. **Full body + detection recipe + tests in `persistent_issues_archive.md`** (trimmed 2026-06-09 for MF012 headroom).


---

## Issue #73 (2026-05-31): meshforge-map fd-leak — RESOLVED, body in archive (trimmed 2026-06-09)

mqtt_subscriber `_connect()` leaked a paho Client per reconnect → 1024 fds →
`[Errno 24]` wedged `:5000` (NOT the RNS class). Fixed both repos (MF `5712b56`,
MA `6e1d2306`); proactive `probe_fd_exhaustion` (degraded ≥80% / wedge ≥95% of
soft RLIMIT_NOFILE). Decision tell: `[Errno 24]`/climbing fds = fd leak (restart
map, find leak); `rnstatus` wedged = RNS class (restart rnsd). Full body +
operator recipe in `persistent_issues_archive.md`.

Five watchdog probes 2026-06-01→06-04, all `degraded` (**trap: derive context
from the SERVICE, not the root watchdog env** — never sudo when euid==0):
`probe_foundation_drift`, `probe_parity_drift`, `probe_rns_version_drift`,
`probe_role_drift` (fix `provision_role.py --apply`), `probe_channel_feed_dark`
(match by channel NAME never slot index). Bodies in archive (07-14).


---

## Issue #75: leaked TCPInterface starves :9443 — RESOLVED, body in archive (trimmed 2026-06-12)

Map service held an UNACCOUNTED persistent TCP to :4403 — leaked `TCPInterface`
drained the PhoneAPI stream (#17 leak form); moc1's web client went deaf while
RX was healthy. `probe_phoneapi_tcp_leak` (same-inode ≥20 ticks + null
persistent_owner) catches recurrence; restart meshforge-map cures. ⚠️ Diagnosis
trap: json-journal greps can't see `via_mqtt`/downlinked traffic — honest RX
record is `grep 'Received text msg'`. Leak origin still unfound. Tests:
`test_phoneapi_leak_*` (10). Full body in `persistent_issues_archive.md`.


---

## Issue #76: /json/* NEVER served by meshtasticd — RESOLVED, body in archive (trimmed 2026-06-12)

`/json/report`+`/json/nodes` are ESP32-only; meshtasticd's HTTP leg was dead from
day one (firmware#9164), and the old availability probe fell through to GET
`/api/v1/fromradio` on 404 — "available" forever + a PhoneAPI packet eaten per
60s re-check (#17 class). Fix (`meshtastic_http.py`): tri-state `ok`/`absent`/
`down` probe, sticky `json_api_absent` + 1h recheck, fromradio probe deleted,
4403 depinned. Residual: `radio_failover` HTTP polls never worked vs meshtasticd.
Missed consumer fixed 2026-07-19 (`f07480d2`, MA `e89a516a` dormant): TUI
data-path check read `absent` as FAIL — now N/A via `_classify_http_unavailable`.
Tests: `TestJsonApiAbsentIssue76` (8) + `TestDataPathHttpTriState` (4). Full
body in `persistent_issues_archive.md`.


---

## Issue #77 (2026-06-07): mqtt_root_drift probe — RESOLVED, body in archive (trimmed 2026-06-16)

`probe_mqtt_root_drift` (degraded, issue_ref 77): radio's OBSERVED publish root
(meshtasticd journal `JSON publish message to <root>/…` — journal-only, never
queries the radio #17) vs the DECLARED consumer root (`gateway.json
mqtt_bridge.root_topic`, default `msh`) — catches a zero-config/factory-reset
radio reintroducing a divergent root before `channel_feed_dark` proves it. Local
invariant; 2-tick debounce; fix `meshtastic --host localhost --set mqtt.root
<declared>`. Full body + tests in `persistent_issues_archive.md`.


---

## Issue #78: cron_verdict_stale probe — RESOLVED, body in archive (trimmed 2026-07-14)

`probe_cron_verdict_stale` (degraded, issue_ref 78): watchdog-layer alerter for
the wired-cron silence/FAIL class (origin: 7 dead crons undetected ~1 month).
Judges ONLY crons wired to `cron_verdict.sh` (orphans ignored); cadence from
schedule ×3, 2h floor; INERT when none wired; 2-tick debounce. ⚠️ **The
"silent(never)" leg was FALSE until 07-10** (global log-cap truncated daily
crons' verdicts → manufactured never-reported pages; fixed `d0254dae` per-name
retention) — post-07-10 a silent(never) page is REAL, investigate the cron.
Eval case `oracle-cron-silent-never-was-false`. Full body in
`persistent_issues_archive.md`.


---

## Issue #79: mini-dudeai hardening + the deploy-restart gap class — RESOLVED, body in archive (trimmed 2026-07-14)

Headline = the **DEPLOY-RESTART GAP class**: nothing restarted USER daemons
after `git pull` (only the 3 SYSTEM units) → `sync_user_unit` in fleet_sync +
update.sh restarts + install_noc enrollment of all 3 mini user units; extended
06-15 to `meshforge-echo` + `nomadnet-silence-watch` (`TestDeployRestartHook`
pins red-test-first; MA parity same). Also shipped: MEMORY.md 24KB
warn+demote, JSONL rotation, **MF021** observation-only lint, rules `.bak` +
`probe_rules_seed_drift`, `probe_history_write_failure`, schema-vs-validator
pin. ⚠️ Probes self-guard None off-box; user-bus restart needs linger. Full
body in `persistent_issues_archive.md`.


---

## Issue #80: mini-dudeai honest-failure-modes review — RESOLVED, body in archive (trimmed 2026-07-09)

18/18 confirmed findings, ALL one class: **degraded internal state mapped to
a valid-looking value** (error reads as empty/recovered/valid). Cures pinned
by `tests/test_mini_dudeai_honest_failure_modes.py` (30) + seed-coverage
gate; key patterns: HOLD edge state on unobservable, observed-tick grace,
atomic writes + torn-tail repair, boot_id latching, all signal classes
seed-routed. **THE LESSON** = `.claude/rules/honest_failure_modes.md` (the
write-time checklist, now 10 points). Residual seed-CONTENT drift closed same
day (`1899261`). Full body + cure inventory in `persistent_issues_archive.md`.


---

## kernel_reboot_pending probe — the 6.12.75-straggler guard (2026-06-09)

`probe_kernel_reboot_pending` (`kernel_reboot_pending`, degraded, no issue#):
newer same-flavor kernel under `/lib/modules` than the running `os.uname()`, OR
`/var/run/reboot-required`. Flavor-aware (rpi-v8 ≠ rpi-2712); read-only; 2-tick
debounce; both seeds; 12 tests. **Full body trimmed to
`persistent_issues_archive.md` 2026-06-15 (MF012 headroom).**


---

## synth_soak_degraded probe — RESOLVED, body in archive (trimmed 2026-07-21)

`probe_synth_soak_degraded` (degraded, no issue#): the hourly LXMF synth soak
exercised the gateway round-trip but **watched nothing** — fire script always
`exit 0`, no `cron_verdict` (fixed 2026-06-27 `c68ed0c0`), and
`probe_lxmf_process_wedge` checks the *process* not the *result*. Two legs:
**SILENCE** (newest `synth-*.json` >~2.5 cadences old — silence IS the failure
for a fixed-cadence generator) + **ENVELOPE** (`pass_envelope` false). Full
body + self-guards in `persistent_issues_archive.md`.


---

## aredn_source_dark probe — the dormant-AREDN-organ guard (2026-06-12, Phase 0)

`probe_aredn_source_dark` (`aredn_source_dark`, degraded, no issue#): intent =
map-user `map_settings.json` `aredn_node_ips`; observation = local `/api/status`
`source_diagnostics.aredn`. Fires (2-tick) on `unreachable`/`not_configured`-by-
a-running-service-with-IPs (fix: restart meshforge-map). Self-guards INERT/held;
runner-gated on meshforge-map active. Both seeds; 10 tests. **Full body trimmed
to `persistent_issues_archive.md` 2026-06-15 (MF012 headroom).**


---

## Issue #81 (2026-06-11): mini paging honesty — RESOLVED, body in archive (trimmed 2026-07-12)

Both real 06-11 crash pages lost to one defect pair: failed sends never
retried (cure: `pending_sends` queue, retried per tick, 10-attempt cap, loud
exhaustion, survives restarts) + back-to-back crash boots coalesced by
cooldown (cure: subject = `host@boot_id[:8]` — each crash boot is a fresh
state key). LIVE-DRILL VERIFIED 06-11 (ntfy blocked → 3 held attempts →
delivered on unblock). Tests: 10 send-retry + 5 per-boot identity. Full body
+ drill transcript in `persistent_issues_archive.md`.

---

## Issue #82: NomadNet boot-race gate hardcoded `@rns/default` — RESOLVED, body in archive (trimmed 2026-07-21)

The #69 fix became a worse fleet-wide bug: the nomadnet user-unit `ExecStartPre`
hardcoded `@rns/default`, so every box whose rnsd `instance_name` differs
crashlooped (NRestarts=7842, ExecStart never ran, **UNDETECTED 10 days**). Cure:
instance-agnostic `rnstatus` host-wait, fail-CLOSED `exit 75`, 120s window (MF
`96aa3d78` + `c3a62c01`). Prevention, 2 layers: `TestNoHardcodedRnsDefaultSocket`
blocks the CODE regression, and **`probe_nomadnet_crashloop`** closes the
DETECTION gap (`probe_service_inactive` is structurally blind to USER units).
Bonus: the "multi-chunk RNS reply drops chunks" symptom was downstream of this —
the bridge was fine, the box's own NomadNet was the broken reader. Full body +
detection recipe in `persistent_issues_archive.md`.

---

## Issue #83: TUI updates — apt truth, holds, mismatched repo — RESOLVED, body in archive (trimmed 2026-07-21)

"meshtasticd update failed" audit, 6 causes: stale `Debian_Testing` OBS repo
published the same version built against a newer libc (apt bound the candidate
to an uninstallable stanza → "held broken packages"); `apt upgrade` without
`-y`; GitHub firmware tags ≠ apt candidate; fleet-wide apt hold invisible;
exit 0 read as success when the package was kept back; and a pip `--user`
script SHADOWING the pipx shim. Cure: `updates/meshtasticd_apt.py` SSOT
(candidate/hold/dry-run, guided repo repair, verified upgrade with re-read) +
floor-pinned `pipx install --force`. ⚠️ apt dry-run banner "NOTE:" ends in
'E:' — error matching must be line-anchored. Quick check: `apt-get -s install
--only-upgrade meshtasticd`; `head -1 ~/.local/bin/meshtastic`. Full body in
`persistent_issues_archive.md`.

---

## meshtasticd VSZ leak (firmware#10468) — pthread stacks stranded, USB-radio boxes only (2026-07-10)

Symptom: hundreds of GB of **virtual memory** (VSZ) with normal RSS — tens of
thousands of paired 8MB+64KB **anonymous mappings** in `/proc/<pid>/maps`.
Portduino meshtasticd on a **USB (CH341) radio** leaks one joinable 8 MB
pthread **thread stack** per interrupt cycle (~9/min): the CH341 poll thread
runs the RadioLib ISR on ITSELF, so `pinedio_deattach_interrupt`'s self-join
guard SKIPS the join and the stack strands (`pine64/libch341-spi-userspace`;
strace/gdb-pinned 07-10). Live: ~561 GB VSZ / 71k anon maps @ day 5 (Pi5+USB);
SPI-radio boxes clean. Neither 2.7.24 nor 2.7.26 fixes **#10468**. Cures:
(1) upstream PR pine64/libch341-spi-userspace#10 (one-line
`pthread_detach(pthread_self())`) — patched 2.7.24 builds deployed on all 3
USB boxes via `/usr/local/sbin/meshtasticd-patched` + `50-canary-pinedio-fix.conf`
drop-in, validated flat; (2) **weekly restart** band-aid
`meshtasticd-restart.timer` STAYS until soak proven (backstop-outlives-fix);
(3) `probe_meshtasticd_vsz_leak` fires only past the 768 GB weekly-restart
envelope (leaking-but-managed stays silent). Quick check:
`wc -l /proc/$(pgrep -x meshtasticd)/maps` — climbing over 30 min = leaking;
flat (≈8 stack pairs) = patched. Detail:
[[project_updates_design_arc_2026_07_10]].


---


## node cache dropped `service_type` on load — the false "NEVER heard" page (2026-07-21)

`to_dict()` wrote `service_type`; `_load_cache()` restored 14 other fields and
dropped it — **honest_failure_modes #4, a writer with no reader**. Every gateway
restart erased every cached node's RNS service type, so
`probe_lxmf_propagation_node_dark` fired UNHEARD (*"configured node has NEVER
been heard"*) against a node that box had heard 7× in 25h. Adoption mandates a
restart, so the false page was structural. Fixed MF `e383547c` / MA `87cae734`.

**Decision tell**: UNHEARD + node otherwise alive = this cache gap, NOT a
typo'd hash. ⚠️ **Three defects, all needed**: the loader drop; `_merge_node()`
never refreshing `service_*` (unrecoverable once lost, NOT self-healing); and
`_merge_node` treating a once-recorded name as PERMANENT, hiding the parser
fix (cure: `name_is_self_reported`, MF `48f5497d` / MA `0657c993`).
⚠️ **`rnprobe lxmf.propagation` is NOT a delivery test** — 100% loss against a
healthy node; prove delivery at the LXMF layer
(`.claude/plans/propagation_drill.py`). Full body in archive (trimmed 07-25).

---

## manager_deadman transient — NAT beat-delivery gap, NOT a dead manager (2026-07-16)

FALSE `MANAGER DARK` page that self-cleared in 10 min: the manager was up the
whole time. A transient gap on the manager→peer ssh push path (manager → NAT
hop → peer on a DHCP'd subnet) let `ssh peer "date > ~/.manager_heartbeat"`
exit 0 while the write never landed, so the peer file aged past `STALE_S`
(25 min). Staleness was REAL, not a clock artifact. The deadman behaved
CORRECTLY — beat-loss is page-worthy whatever the cause.

**Decision tell**: `manager_deadman FAIL` + manager box UP + a manual beat
lands live = transient delivery gap, self-heals next cycle. A REAL
manager-down has the box unreachable and `manager_heartbeat` FAIL
manager-side. **Quick check** — peer:
`echo $(( $(date +%s) - $(stat -c %Y ~/.manager_heartbeat) ))` (>1500 = stale).
Durable cure is DHCP reservations / steadier transport, NOT a threshold bump.
Companion defect (the 2 MISSING verdicts) was the `cron_verdict.sh` truncation
race, FIXED 2026-07-16 (MF `2d34877d`, MA `a1f32f93`). Full body in
`persistent_issues_archive.md` (trimmed 2026-07-25).

---

## mf.internal AAAA forwards to the WAN — the 900ms fleet-name tax (2026-07-25)

m1 answers only exact `(name, type)` static matches locally and **forwards
everything else to its WAN upstream**. Fleet names carry A records only, so
every AAAA for `<name>.mf.internal` goes to the internet and returns
NODATA — **with no SOA, so systemd-resolved cannot negatively cache it** and
pays that round trip forever. Every real tool (ssh, curl, urllib, getent)
uses `getaddrinfo` AF_UNSPEC and asks both families:

    m1  moc.mf.internal A     1.1ms      (local static entry)
    m1  moc.mf.internal AAAA 75.5ms      (forwarded; WAN baseline 75.8ms)
    12-host sweep  AF_UNSPEC 902ms  vs  AF_INET 1.7ms

So resolution was **coupled to internet reachability** — a WAN hiccup makes
healthy boxes look dead. Router-side is tidier but m1 admin access isn't
available from the fleet boxes. Cure: `scripts/gen_fleet_hosts.py --apply`
writes a delimited `/etc/hosts` block (nss `files` precedes `dns`), live on
7 boxes; 902ms → 4ms, and names resolve with DNS or the uplink down. Hourly
`fleet_hosts_drift` cron per box. ⚠️ **moc3 excluded** (RNS soak).

**Decision tell**: fleet-wide ~75-90ms per name lookup with A at ~1ms = this,
not a sick resolver. **Quick check**: compare
`getaddrinfo(name, AF_INET)` vs `AF_UNSPEC` timing — a ~75ms gap is the AAAA
leg. ⚠️ `/etc/hosts` SHADOWS DNS, so the block is seeded from **live DNS**,
never from the registry's `ip_fallback` snapshot (that would bake in a stale
copy and shadow the truth — the moc5 reshuffle class).

---

## A detector that reads what it audits is self-confirming (2026-07-25)

The `gen_fleet_hosts.py --check` drift detector used `socket.getaddrinfo()`
to fetch "what DNS says". But nss consults `files` (`/etc/hosts`) **before**
`dns`, and systemd-resolved also answers from `/etc/hosts` — so the check
compared the generated block **against itself**. A deliberately corrupted
entry reported `in sync` (rc=0), and `--apply` then said "already current"
and **refused to heal it**, because the corrupted file WAS the notion of
truth. It could never detect, nor repair, the one thing it exists to catch.

13 unit tests passed throughout: they mocked `resolve_a`, so the mock stood
in for the exact layer that was broken. **Only a live drill — corrupt a real
entry, run the real check — exposed it.**

Cure: query the upstream server DIRECTLY over UDP (servers discovered from
the resolved drop-in, never hardcoded — MF014), bypassing NSS. A silent
server is UNKNOWN and falls through, never NXDOMAIN. The test that had
**pinned the broken behaviour** now asserts the opposite: calling
`getaddrinfo` at all is a failure.

**The general rule** (calibrated_claims #7, in checker form): *a checker must
not consume the artifact it validates.* Ask what input would make the
detector and the thing it watches disagree — then feed it that input.

