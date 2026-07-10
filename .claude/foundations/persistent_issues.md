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
  in [[project_upstream_dependency_governance_2026_05_29]]. **Checked 2026-06-09**:
  GitHub mirror still receives releases — upstream at **1.3.5** (maintenance:
  announce-dedup, shared-instance RPC, AutoInterface roaming; no CVE/wire change)
  → DECISION: stay on the 1.2.5+mf.N line; a 1.3.5 merge eval is a future arc.
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

## Issues #64 + #65: federation directory gzip + two-tier backoff cap (2026-05-18)

**Resolved — bodies in `persistent_issues_archive.md` (MF012 trims).**
#64: `fetch_peer_directory` sends `Accept-Encoding: gzip` + decodes (35 MB →
4.7 MB wire) + 40 MB `size_alarm` gauge. #65: second-tier backoff cap in
`map_federation.py` (≈6 h continuous failure → multiplier cap 60 ≈1 h cadence)
for permanent outages (gateway-only moc3); `backoff_multiplier=60` is the
operator tell. Tests: `TestGzipNegotiationIssue64` etc. +
`TestBackoffExtendedCapIssue65` (8). Closed the federation triad
(#54/#55/#56/#59/#64/#65).


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

Three watchdog probes added 2026-06-01 (audit-organ→Signal→mini pattern,
[[project_mini_scales_via_watchdog_probes_2026_06_01]]; all `degraded`; **recurring
trap: derive context from the SERVICE, not the root watchdog env**): `probe_foundation_drift`
(`/etc/reticulum` root:root under a non-root rnsd; fix `fleet_foundation.py apply`;
⚠️ INERT 06-01→06-09: `rns_tree_perms` hardcoded `sudo=True` stats, which NoNewPrivileges
blocks → fields None → never fired; fixed `09bc14a`/MA `91663edb` — never sudo when euid==0),
`probe_parity_drift` (MeshForge↔MeshAnchor `check_parity()` drift; self-guards if
`/opt/meshanchor` absent), `probe_rns_version_drift` (rns/lxmf off the `+mf.N` pin —
reads the rnsd user's `~/.local` site-packages directly, since the watchdog sandbox
NoNewPrivileges+RestrictSUIDSGID blocks sudo/runuser; fix reviewed `pip install -r
requirements/rns.txt`). 4th probe 2026-06-03: `probe_role_drift` (`role_drift`) — live
unit state vs the box's effective declared role via `provision_role.py`'s own dry-run
`plan()` (base role + deployment.json overrides; documented overrides honored — the moc2
legibility case, see `.claude/research/fleet_architecture_2026_06_03.md` §7-B); 2-tick
debounce; fix `provision_role.py --apply` or correct the declared role. 5th probe
2026-06-04: `probe_channel_feed_dark` (`channel_feed_dark`) — the .32 dark-feed /
PSK-rotation-canary lesson: **silence is the failure mode**. Watches meshtasticd's
MQTT-json uplink journal lines (`json/<name>/…"type":"text"` — by channel NAME since
2026-06-06: `"channel":N` is the box-LOCAL slot index and slot layouts differ, which
false-alarmed the federator for days; doesn't touch single-consumer `/api/v1/fromradio`,
#17); no fleet-channel text for ≥6h while the json pipeline is alive → `degraded`. Tell
for a missed PSK re-key
(decode gate = hash(name,psk)), deaf radio (`channel_utilization=0.0`), or dead uplink.
Self-guards None on boxes with no json uplink at all (unobservable ≠ dark — e.g. moc5);
busy gateways (moc) canary the channel for the whole fleet via mini's signal_class flow.


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
Tests: `TestJsonApiAbsentIssue76` (8). Full body in `persistent_issues_archive.md`.


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

## Issue #78: cron_verdict_stale probe — the silent-cron guard (RESOLVED, condensed 2026-06-19)

`probe_cron_verdict_stale` (`cron_verdict_stale`, degraded, issue_ref 78): the
watchdog-LAYER alerter for the "silence is the failure mode" cron class. Origin:
**7 dead crons firing into a deleted `routine-bin/` undetected since ~May 21**
(`>/dev/null 2>&1`); the cron-verdict regime (`scripts/cron_verdict.sh`) recorded
verdicts but had no alerter. **Load-bearing design**: reads the operator crontab
spool (`/var/spool/cron/crontabs/<user>`) directly as root (no sudo — sandbox;
same pattern as `probe_rns_version_drift`/`probe_role_drift`), judges ONLY crons
WIRED to `cron_verdict.sh` (orphan verdicts ignored → no false-alarm), cadence
from the schedule (`_cron_max_interval × CADENCE_MULT=3`, 2h floor). Fires on a
wired cron that's FAIL/CONCERN, silent-past-threshold, or never-reported. **INERT
(None) when no crons wired** — opt-in regime. 2-tick debounce. Reuses
`_parse_crontab`/`_parse_cron_verdicts` from `fleet_snapshot`. Tests:
`TestCronVerdictStale` (11) + closed-enum gate bump.

**Addendum 2026-07-09 (QA audit): the "silent(never)" leg was FALSE until
07-10.** `cron_verdict.sh`'s GLOBAL log-cap retention truncated a daily cron's
verdicts out of the log faster than its cadence — manufacturing "never
reported" pages for healthy wired crons. Fix `d0254dae`: per-name retention
(each cron keeps its own newest verdicts). Post-07-10 a "silent(never)" page
is REAL — investigate the cron, don't suspect the probe. Tier-L eval case
`oracle-cron-silent-never-was-false` pins this lore.


---

## Issue #79: mini-dudeai hardening + the deploy-restart gap class (2026-06-09, extended 06-15)

Audit of mini-dudeai (MeshForge-OWNED rule-loop agent; no MA twin) found 1 defect +
risks, fixed in one pass. (1) **DEPLOY GAP** (defect):
nothing restarted the mini USER daemon after `git pull` (fleet_sync/update.sh restarted
only the 3 SYSTEM units) — added user-bus `sync_user_unit`/`sync_local_user_unit`, an
update.sh user-unit restart, and install_noc enrollment of all 3 mini user units.
**Extended 06-15 (the whole deploy-restart class):** MF `meshforge-echo` +
`nomadnet-silence-watch` wired into update.sh + fleet_sync; §3b-ii guard
`TestDeployRestartHook` pins it red-test-first. **MeshAnchor parity**: same fix + the
ported guard + MA's own #79 entry; MA `update.sh` restarts echo + SYSTEM
`meshanchor`/`-map` (CODE_CHANGED-gated, no MA fleet_sync). Arc: `honest_dev_env_arc.md`.
(2) **MEMORY.md over the ~24KB load limit** (defect): `check_index_size` warns (never
blocks) + `demote_memory` → MEMORY_ARCHIVE.md + `probe_memory_index_oversize`.
(3) unbounded append growth: `_rotate_if_needed` (atomic, keep-newest, valid JSONL) on
history/deltas/ledger (1/1/2 MB). (4) cadence pinned `--model`
(`MINI_DUDEAI_CADENCE_MODEL`, default opus). (5) observation-only invariant (no
subprocess/systemctl in engine+sources+actions) lint-pinned **MF021** + test-pinned.
(6) rules promotion writes a `.bak` + `probe_rules_seed_drift`. (8)
`probe_history_write_failure` (loop alive + fires advancing but history mtime frozen).
All 3 new probes wired in `run_all_probes`, classes in the closed enum (issue_ref 79).
(7) schema-vs-validator drift test pins the config schema to the validator. Tests:
+~90. ⚠️ Probes DEGRADED-only, self-guard None off-box; user-bus restart needs linger.


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

## synth_soak_degraded probe — the unwatched delivery canary (2026-06-15)

Gateway traffic-flow audit found the **hourly LXMF synth soak watched NOTHING**:
`meshforge-synth-soak.timer` exercises the gateway's real round-trip path + writes
a `pass_envelope` (0.95 ok-ratio), but the fire script **always `exit 0`** +
emitted no `cron_verdict` (FIXED 2026-06-27 `c68ed0c0`: now emits a `synth_soak`
OK/CONCERN/FAIL verdict on /fleet/slo via the orphan stale-gate; the probe still
owns alerting), and `probe_lxmf_process_wedge` checks the *process* not the
*result* — so an envelope regression OR a silent timer paged no one. New
`probe_synth_soak_degraded` (`synth_soak_degraded`, degraded, no issue#,
`watchdog_probes_gateway.py`), two legs: **SILENCE** (newest `synth-*.json` >~2.5
cadences old = exerciser stopped — silence IS the failure for a fixed-cadence
generator, inverse of `delivery_confirmation_stall`) + **ENVELOPE** (`pass_envelope`
false → ok_ratio + worst pair). Reads operator `~/.local/state/.../synth_soak`
direct (root-safe via `_find_operator_user`, never escalate). Self-guards: dir
absent → INERT; no file → held; unparseable newest → candidate but 2-tick debounce
rides a torn mid-write; `pass_envelope` absent → indeterminate (held, never reads
healthy). Both seeds (`synth_soak_degraded_any`); 9 tests + closed-enum bump.
Companion fix same day: the #74 `confirmation_rate` cross-population DISPLAY residual
(read 1.64) made honest — see the #74 row. Activation: `git pull --ff-only`
(soak-safe), then restart `meshforge-watchdog` + promote seeds (runbook).


---

## aredn_source_dark probe — the dormant-AREDN-organ guard (2026-06-12, Phase 0)

`probe_aredn_source_dark` (`aredn_source_dark`, degraded, no issue#): intent =
map-user `map_settings.json` `aredn_node_ips`; observation = local `/api/status`
`source_diagnostics.aredn`. Fires (2-tick) on `unreachable`/`not_configured`-by-
a-running-service-with-IPs (fix: restart meshforge-map). Self-guards INERT/held;
runner-gated on meshforge-map active. Both seeds; 10 tests. **Full body trimmed
to `persistent_issues_archive.md` 2026-06-15 (MF012 headroom).**


---

## Issue #81: mini paging honesty — failed-send retry + per-boot crash identity (2026-06-11)

Both real 06-11 crash pages were lost to one engine defect pair. (1) **Failed
sends were recorded honestly then never retried**: crash #4's [RED] ntfy died
in the boot+15s DNS race (2-for-2 on real crashes); the operator got only the
min-priority "cleared" notice. Cure: undelivered sends queue in rule state
(`pending_sends`), retried once per tick until delivered
(`send_retry_delivered`) or exhausted at 10 total attempts
(`send_retry_exhausted`, loud); retries bypass cooldown + never touch fire
bookkeeping; queue survives daemon restarts (a page queued just before a
second crash delivers from the next boot); per-state cap 4 drops OLDEST
loudly (`send_retry_dropped`); unknown-action-kind config errors not queued;
undelivered sends surface in the warm brief. (2) **Back-to-back
crashes coalesced**: edge state + cooldown key on (rule_id, subject) and the
subject was the bare hostname — crash #5 (18.5 min after #4, inside
`cooldown_s=3600`) produced no edge, no page, no digest record.
Cure: BootHealthSource subject is now `host@boot_id[:8]` from the LATCHED
assessment (fallback latched boot_time) — each crash boot is a fresh state
key, so neither `currently_active` nor cooldown carries across boots. Seeds
+ live rules all match `subject_glob "*"`; no rule changes. Tests:
10 send-retry + 5 per-boot identity incl. the end-to-end 06-11 double-crash
timeline (`test_back_to_back_crash_boots_both_fire`).

**LIVE-DRILL VERIFIED 06-11 10:33–10:35 HST** (production daemon, VolcanoAI):
ntfy.sh blocked via /etc/hosts (block A **and** AAAA — an IPv4-only entry
leaks through DNS on the AAAA lookup), temp min-priority rule promoted onto
moc3's steady backoff condition. edge_up ok=false → 3 held
attempts (state + brief witness) → unblock → `send_retry_delivered` attempt
4, ~90s after first failure; confirmed server-side via topic poll. fire_count
stayed 1; removed-while-active deactivated loudly, action not run; hosts/
ruleset/state/brief verified clean after.


---

## Issue #82: NomadNet boot-race gate hardcoded @rns/default — the #69-fix regression (2026-06-19)

The #69 boot-race gate (commit `121ac59a`) hardcoded `@rns/default` in the
nomadnet user-unit `ExecStartPre`. On every box whose rnsd `instance_name` ≠
`default` (VolcanoAI = `volcano ai rns` → rnsd binds `@rns/volcano`) the grep
matched nothing → gate timed out → `exit 75` → **crashloop, NRestarts=7842,
ExecStart never ran, UNDETECTED for 10 days**. The fix for #69 became a worse,
fleet-wide bug ("house of cards"). **Cure**: replace the brittle socket-grep with
the instance-agnostic `rnstatus` host-wait already proven by
`meshforge-map.service.d/10-wait-for-rnsd.conf` (fleet-stable since 2026-05-30),
fail-CLOSED (`exit 75`), 120s window + `TimeoutStartSec=180` so a slow-but-healthy
rnsd boot isn't parked by `StartLimitBurst`. Installer drops the stale
`10-wait-rnsd.conf` drop-in; `rns_interfaces.py` + `_port_detection.py`
de-hardcoded. Commits `96aa3d78` + `c3a62c01`; fleet-remediated all boxes (moc5
nomadnet intentionally disabled). **Prevention — 2 layers**: (1) lint/test guard
`TestNoHardcodedRnsDefaultSocket` (templates + src) blocks the CODE regression;
(2) **`probe_nomadnet_crashloop`** closes the 10-day-silent DETECTION gap —
`probe_service_inactive` is structurally blind to USER units, so it reads systemd
`restart counter is at N` lines under the `USER_UNIT=` journal field (root-direct,
no sudo), short live-window + newest-restart recency gate (post-fix history can't
false-page), INERT on disabled/unobservable, both seeds. **Bonus**: the
"multi-chunk RNS reply drops chunks 2..N" symptom was downstream of THIS — a
dest×chunk re-test proved the gateway delivers all 3 (LXMF-confirmed); the real
loss was the broken reading client (the box's own NomadNet), NOT the bridge.
