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
`+mf.N` marker. Fleet baseline: **rns `1.2.5+mf.3` / lxmf `0.9.4+mf.0`**. This is
the meta-resolution for the entire **rnsd-RPC fragility class** (#58/#61/#63/#68/
#69/#72): fragility that we used to work *around* in `utils/rns_init.py` can now be
fixed *at the source*. Phase-2 source fixes shipped: `+mf.1` #68 connect-hang,
`+mf.2` #72 RPC-hang (see "FIXED AT SOURCE" notes below), and **`+mf.3` the
rnsd-SIGTERM graceful-shutdown hang** — `Transport.detach_interfaces()` is bounded
to `DETACH_TIMEOUT` (default 5s, env `RNS_DETACH_TIMEOUT`) so a busy node's SIGTERM
teardown reaches `RNS.exit()`/`os._exit()` gracefully instead of systemd waiting
the full `TimeoutStopSec`. **mf.3 bounds ONLY the `detach_interfaces()` hang — it
is NOT a complete fix.** An active proof on 2026-05-30 (deliberate rnsd restart
cycles) caught moc1 hanging the **full 15s → SIGKILL** (`result=timeout`,
`status=9/KILL`) WITH mf.3 loaded; the `DETACH_TIMEOUT` warning never fired, so the
hang is in a SECOND shutdown-path location mf.3's detach bound does not cover
(likely an uninterruptible main-thread wedge before/around the SIGTERM handler, or
downstream in `exit_handler` — only SIGKILL ends it). **Therefore the
`rnsd.service.d/10-stop-timeout.conf` 15s cap is REQUIRED — it is the genuine cure
for that residual hang. DO NOT RETIRE IT** (this reverses the earlier
retire-after-soak plan; commit `0cb935d` framing is superseded). Bounding the
second hang path at the source is a candidate **mf.4** (needs controlled
reproduction + a live main-thread stack capture). ⚠️ Also: do NOT rapid-cycle rnsd
restarts fleet-wide — each 15s-hang+SIGKILL plus the slow rebind opens an `@rns`
race window for periodic RNS clients (the lab tracer), stranding rnsd as a client
(#69-adjacent); space restarts and verify host-binding before the next.

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
| #68 (2026-05-20) | rnsd hard-wedge → map main thread silent-stuck in `unix_stream_connect`; bg threads kept logging, `:5000` never bound. Cure: bounded AF_UNIX probe in `open_reticulum()` chokepoint (MF019) + FIXED AT SOURCE in fork `rns 1.2.5+mf.1` (`LocalClientInterface.connect` settimeout). Detection/recovery recipes + body in archive. | `TestRNSReticulumChokepoint` + fork test `meshforge_local_connect.py` (4) |

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
(`/etc/reticulum` root:root under a non-root rnsd; fix `fleet_foundation.py apply`),
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

## Issue #74: gateway health-check review — decorative breaker, dead canary branch, 2 new probes (2026-06-06)

Code review of the gateway health core found and fixed four honest-signal defects:
(1) **circuit breaker was write-only** — `can_send_to`/`record_send_*` had ZERO
callers; sends never gated, organic failures never fed threshold-OPEN; only
`trip_open` from the wedge hook touched it. Now both RNS send paths gate + record;
`_queue_send_rns` raises a retriable-pattern error ("temporarily unavailable") on
open circuit so RetryPolicy backs off; reconnect success `reset_all()`s stale OPEN
state. (2) **wall-clock recovery math** — `time.time()` froze OPEN circuits on
post-boot NTP backsteps; now `time.monotonic()` (+ HALF_OPEN off-by-one fixed: the
transitioning caller now takes the trial slot; `half_open_max_calls` clamped 1).
(3) **delivery_write_canary degraded branch was dead** — `consecutive_write_errors`
was writer-local; the map daemon serving `snapshot()` always read 0. Now persisted
to `meta.*` keys + merged `max(local, db)`. (4) Two probe-layer blind spots closed:
`probe_queue_backlog` (`queue_backlog`; depth ≥80%/95% of max + dead-letter GROWTH
per tick via new `/api/gateway/queue`; static piles never fire) and
`probe_delivery_confirmation_stall` (`delivery_confirmation_stall`; recent-ring
confirm rate ≤50%/≤10% with ≥20 ring sends; None at low/zero traffic — silence is
NOT failure here, inversion of `channel_feed_dark`). Also fixed: map handler
imported nonexistent `MessageQueue` symbol (queue endpoint was dead code) →
`PersistentMessageQueue`. Tests: `TestCircuitBreakerWiringIssue74` (8),
`TestMonotonicClockIssue74` (6), `TestCrossProcessWriteErrorTruthIssue74` (3),
probe tests (16) + closed-enum gate bump.

**FIX 2026-06-09: `probe_delivery_confirmation_stall` was measuring disjoint protocol
populations (false-alarmed on moc).** delivery_counters uses different lifecycle states
per protocol — RNS `queued→confirmed` (never `sent`), Meshtastic `queued→sent` (never
`confirmed`; no ACK consumption). So `confirmed/sent` was (RNS-confirmed ÷ Meshtastic-sent),
two different populations → ~50% on any mesh-heavy box (cumulative 181%). Rewrote to judge
ONLY confirmable protocols (those in `state_by_protocol.confirmed`; RNS today, Meshtastic
once step-4 ACK lands) comparing their REAL terminal outcomes — `confirmed` vs failed-delivery
`dropped` (`_DELIVERY_FAILURE_REASONS`; benign `dedup` excluded); `min_sent`→`min_terminal`;
no-confirmable / small-sample → None. Ported to MeshAnchor `check_delivery_confirmation_stall`
(same bug). ⚠️ residual: the `/api/gateway/delivery.confirmation_rate` DISPLAY metric is still
the cross-population ratio (181%) — operator-facing only, not a pager; separate slice. Real
completeness = Meshtastic ACK consumption (Thread-2 step 4). No new signal class.


---

## Issue #75: leaked TCPInterface starves :9443 web client — phoneapi_tcp_leak probe (2026-06-07)

moc1's web client showed no inbound texts/ACKs while the radio journal proved RX
healthy ("waiting for delivery", bot replies invisible). Cause: the map service
held an UNACCOUNTED persistent TCP to meshtasticd :4403 (`/api/radio/status`
`persistent_owner: null`) — a leaked `TCPInterface` whose reader thread drained
the PhoneAPI stream (#17 contention class, leak form). Restarting meshforge-map
cured it instantly; leak origin not yet identified (recurrence will be caught).
Diagnosis trap that cost the evening: **json-journal greps cannot see
`via_mqtt`/downlinked traffic** (firmware loop guard) — the honest RX record is
`grep 'Received text msg'` router lines. New `probe_phoneapi_tcp_leak`
(`phoneapi_tcp_leak`, degraded, issue_ref 75): MainPID's socket inodes
(`/proc/<pid>/fd`) ∩ `/proc/net/tcp*` ESTAB-to-:4403, fires only when the SAME
inode persists across ticks (legit per-collect sockets live seconds) AND
persistent_owner is null; accounted owners (listener TCP fallback) and a dark
status endpoint stay silent. Read-only, sandbox-safe (no ss/sudo). Recovery:
`sudo systemctl restart meshforge-map.service`. Tests: 8 in
`tests/test_watchdog_probes.py` (`test_phoneapi_leak_*`) + closed-enum bump.
**False-alarm fix (2026-06-07)**: probe flapped NEW/CLEARED every 1-4 min on moc1 —
demand-collect TCP nodedb syncs live MINUTES (rotating inodes), so the 2-tick bar
was too low. Now consecutive-tick counts per inode; fires at ≥20 ticks (~10 min).
Legacy state loads as count 1. +2 tests (slow-collect silent, legacy format).


---

## Issue #76: /json/* was NEVER served by meshtasticd — honest-absent probe; fromradio probe-leak killed (2026-06-07)

Research verdict (firmware source): `/json/report`+`/json/nodes` are **ESP32-only**
(`ContentHandler.cpp`, HAS_WIFI-gated). meshtasticd's `PiWebServer.cpp` only ever
registered `/api/v1/fromradio|toradio` + a 404 static fallback — not a 2.7.24
regression; the HTTP leg was dead from day one (open FR: firmware#9164). Worse: the
old availability probe fell through to **GET /api/v1/fromradio on 404**, reporting
the dead API "available" forever AND consuming a PhoneAPI packet per 60s re-check
(#17 contention class); `PROBE_PORTS` also HTTP-probed :4403. Fix
(`meshtastic_http.py`): tri-state probe (`ok`/`absent`/`down`); alive-but-404 →
sticky `json_api_absent`, `is_available=False`, 1h recheck; fromradio probe deleted;
4403 depinned; `availability_reason` surfaces in `/api/status.radio_config`.
ESP32-over-WiFi hosts still work. Residual: `radio_failover` HTTP health polls never
worked against meshtasticd (needs a non-/json source if dual-radio failover revives);
moc5's `collect_cache_max_age_seconds: 290` stays (TCP leg is the only leg).
**Tests**: `TestJsonApiAbsentIssue76` (8) in `tests/test_meshtastic_http.py`.


---

## Issue #77: mqtt_root_drift probe — the msh/US split guard (2026-06-07)

After the 06-06 fleet unification on explicit `mqtt.root msh` (moc2 was the last
holdout), the remaining hole: a zero-config radio join or factory reset silently
reintroduces a divergent root and consumers pinned to the declared root go
partially deaf — the dark-feed class, but with the CAUSE visible hours before
`channel_feed_dark` proves the symptom. New `probe_mqtt_root_drift`
(`mqtt_root_drift`, degraded, issue_ref 77): compares the radio's OBSERVED
publish root — parsed from meshtasticd's journal `JSON publish message to
<root>[/<region>]/2/json/<ch>/!<id>` lines (journal-only: never queries the
radio, which would open a PhoneAPI TCP connection — #17) — against the box's
DECLARED consumer root (`gateway.json mqtt_bridge.root_topic`; absent key →
the GatewayConfig default `msh`, the effective value). Local invariant only
(brokers are per-box islands — no fleet consensus needed). Self-guards None:
meshtasticd inactive, no json uplink in lookback (unobservable ≠ drift, the
RX-only case), gateway.json unreadable / service user unresolvable. 2-tick
debounce (parity-style streak file) rides out an operator mid-rotation. Fix:
`meshtastic --host localhost --set mqtt.root <declared>` (or correct
gateway.json if the radio is the intended truth). Tests: 9
(`test_mqtt_root_drift_*` + `test_read_declared_root_topic_*`) + closed-enum
gate bump. Journal line shape pinned live on moc 2026-06-07 01:25 HST.


---

## Issue #78: cron_verdict_stale probe — the silent-cron guard (2026-06-08)

The `/fleet` "Scheduled & Running" panel surfaced a stale `diag24h_watchdog`
`FAIL` verdict on moc → investigating it found **7 dead crons firing into a
deleted `routine-bin/` since ~May 21**, silently failing under `>/dev/null
2>&1`. The cron-verdict regime (`scripts/cron_verdict.sh`, the "every cron
leaves a dated verdict" recorder) existed but had **no active alerter** — the
"silence is the failure mode" class with no watchdog-LAYER alerter. New
`probe_cron_verdict_stale` (`cron_verdict_stale`, degraded, issue_ref 78) is
that alerter — built as a probe so it flows to mini + the `/fleet` panel,
per-box, integrated with the observability spine. (Complements — does NOT
replace — the cron-based `cron_verdict_freshness.sh` ntfy monitor that DOES
exist on the manager box VolcanoAI; defense in depth, different surfaces.)

**Load-bearing design — cross-references the crontab, not just the log:** a
verdict-log-only probe would FALSE-ALARM on stale ORPHAN verdicts (the
`diag24h_watchdog` line was a one-off test verdict for a cron that no longer
exists). So the probe reads the operator's crontab spool
(`/var/spool/cron/crontabs/<user>`) directly as root (no sudo — the watchdog
NoNewPrivileges sandbox forbids it; same in-process-read pattern as
`probe_rns_version_drift`/`probe_role_drift`), finds crons WIRED to
`cron_verdict.sh <name>`, and judges ONLY those — a verdict whose name is not
a currently-wired cron is ignored. Per-cron staleness cadence is derived coarse
from the schedule (`_cron_max_interval`; `@reboot`→inf, unparseable→26h),
`× CADENCE_MULT=3` with a 2h FLOOR. Fires on a wired cron whose latest verdict
is FAIL/CONCERN, or that went silent past threshold, or never reported. **INERT
(None) on any box with no wired crons — the regime is opt-in**, so it would NOT
auto-catch *unwired* dead crons (the panel + per-box triage own those); it
guarantees a WIRED cron can never silently fail again. 2-tick debounce. Reuses
the `_parse_crontab`/`_parse_cron_verdicts` parsers from `fleet_snapshot`.
Tests: `TestCronVerdictStale` (11) incl. the orphan-filter + inert-when-unwired
cases, + closed-enum gate bump. Regime remainder (wire live crons; triage the
7 dead) = per-box operator passes.


---

## Issue #79: mini-dudeai hardening — deploy gap, memory guards, rotation, self-probes (2026-06-09)

Deep-research audit of mini-dudeai (MeshForge-OWNED deterministic rule-loop agent;
NO MeshAnchor twin — MA's `src/agent/` is an unrelated command-exec daemon) found 1
defect + several risks, all fixed in one pass. (1) **DEPLOY GAP** (defect): nothing
restarted the mini USER daemon after `git pull` (fleet_sync/update.sh only restart the
3 SYSTEM units) — added user-bus `sync_user_unit`/`sync_local_user_unit`, an update.sh
user-unit restart, and install_noc enrollment of all 3 mini user units (XDG_RUNTIME_DIR
bridge, no hardcoded user). (2) **MEMORY.md over the ~24KB load limit, unguarded**
(defect): `memory_apply.check_index_size` warns (NEVER blocks an append) + `demote_memory`
atomically moves a stale pointer to MEMORY_ARCHIVE.md + `probe_memory_index_oversize`.
(3) unbounded append-only growth: `_rotate_if_needed` (atomic, keep-newest, valid JSONL)
on history/deltas/ledger (1/1/2 MB caps). (4) cadence pinned `--model`
(`MINI_DUDEAI_CADENCE_MODEL`, default opus). (5) observation-only invariant (no
subprocess/systemctl in engine+sources+actions) now **lint-pinned MF021** + test-pinned.
(6) rules promotion writes a `.bak` (rollback) + `probe_rules_seed_drift` (live behind
role seed). (8) `probe_history_write_failure` (loop alive + fires advancing but history
mtime frozen). All 3 new probes wired in `watchdog_runner.run_all_probes`, classes in the
closed enum (issue_ref 79). (7) schema-vs-validator drift test pins
`mini_dudeai_config.schema.json` to the hand-rolled validator. Tests: +~90 across
`test_mini_dudeai_*` / `test_watchdog_probes`. ⚠️ Probes are DEGRADED-only + self-guard
None off-box; deploy user-bus restart needs linger (install_noc enables it).


---

## Issue #80: mini-dudeai honest-failure-modes review — one defect class, 18/18 confirmed (2026-06-09)

Full-effort review of the engine: 18 candidates verified, 18 survived — ALL
one class: **a degraded internal state mapped to a valid-looking value**
(read error→empty ruleset→"nothing active"; source error→absent conditions→
"recovered"/false CLEARED; `{"rules": null}`→zero validation errors→promotion
wipes alerting; typo'd match key→extras filter→rule silently dead). Fixed in
one pass; pinned by `tests/test_mini_dudeai_honest_failure_modes.py` (30) +
seed-coverage gate. Key cures: unreadable rules / erroring sources now **HOLD
edge state** (last-good ruleset cache + per-source condition hold persisted in
`state.source_hold` — unobservable ≠ resolved); empty/null candidates can't
wipe canonical; `lint_rules_document` warns on non-structural match keys at
authoring+promotion; grace needs OBSERVED ticks (`pending_ticks`, NTP/restart
can't forge the span) + cooldown clamps backward clock steps + run() resets
streaks; single-instance flock (`--once` vs daemon race); `_util._atomic_write`
(mkstemp+fsync) + shared `append_jsonl` (torn-tail repair) across history/
audit/dreams; boot_health latches on kernel **boot_id**, definitive verdicts
immutable, `indeterminate` re-assessed post-NTP from STORED pre-boot facts
(catches the short-power-cut class the ±120s time key missed); **both role
seeds now route all 23 signal classes** (6 were firing into a void:
phoneapi_tcp_leak #75, mqtt_root_drift #77, cron_verdict_stale #78 + the 3 #79
self-probes) and `TestSeedCoversSignalClasses` FAILS on any future unrouted
class; probe fixes — `_ROLE_TO_MINI_SEED` covers collector/cloud-publisher,
history-stall baseline = `state.history_appends_total` (counts edge_downs),
memory-index limit imports the writer's constant; config-mode boot_health
plumbs `clean_exit_path` (reader/writer pair). **THE LESSON — apply at WRITE
time:** `.claude/rules/honest_failure_modes.md` (9-point checklist; every
`except`/`or []`/`.get(default)` must answer "what does the consumer SEE?").
**Residual closed (`1899261`, same day): seed-CONTENT drift.** `seed_provenance`
stamped in-document by `candidate.merge_seed_rules` (THE merge path); probe
gains a STALE leg via the writer's `rule_body_sha` (guarded import, no fallback
hasher): live==stamp but seed moved → fire; tuned/unstamped → exempt (stamps
ratchet in via merges; unstamped boxes can't false-alarm).
