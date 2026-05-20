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
| #49 Lean node directory — split persistent dir from time-series (2026-04-28) | New `nodes` table in `node_history.db` (one row per network,node_id) decoupled from time-series `node_observations`; tiered retention (local 30d / external 7d) + 50k LRU cap; sticky source-origin promotion. Body in archive | `tests/test_node_history.py` (17) + diagnostics (3) |
| #50 Directory tier retention defeated by UPSERT `last_seen=now` (2026-04-30) | External-bulk republish reset tier clock every cycle; fix uses upstream `last_heard` + `MAX(nodes.last_seen, excluded.last_seen)` ON CONFLICT. Body in archive | `TestDirectoryUpstreamTimestamp` in `tests/test_node_history.py` (9 tests) |
| #51 Issue #50 wiring unreachable — meshcore parser emitted ISO-8601 not Unix epoch (2026-04-30) | Inline ISO→epoch normalization in `_parse_meshcore_public_node`. Tests must use real upstream payload shape. Body in archive | 5 new in `TestMeshCorePublicCollector` |
| #57 Gateway data-path watchdog — `bounded_call` over RNS RPC hot path (2026-05-17) | `bounded_call` wraps 11 RNS RPC sites; wedged peer trips circuit breaker + `os._exit(2)`; systemd restarts the gateway (better than silent hang). Body in archive | `test_wedge_events.py` (17) + `test_bounded_rpc.py` (19) + `test_circuit_breaker.py` (+6) |
| #12, #22, #23 | RNS configdir= (#12, lint MF009), don't overwrite meshtasticd `config.yaml` (#22, inverse companion of #58), post-install verification via `scripts/verify_post_install.sh` (#23). Bodies in archive. | — |

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


---

## Issue #62: Config-layering — saved defaults block future default bumps (2026-05-18)

**Symptom**: Issue #56's `DEFAULT_TIMEOUT` 5→30 bump never took effect
on the fleet — every box's `map_settings.json` had stale
`federation_timeout_seconds: 5` pinned. Required manual `jq` edits per
box on 2026-05-18 deploy. Same trap for every future default bump.
Reliability backlog #6.

**Root cause**: `SettingsManager.save()` persisted the **entire**
merged dict (`defaults | overrides`). First save() for any change made
every default a "saved value" — code-default bumps could never climb
over the stale persisted value. Secondary dual-default hazard at
`map_data_collector.py:330` (`.get(..., 5)` while SettingsManager
default was 30).

**Fix** (`src/utils/common.py`): `SettingsManager` now tracks
`_explicit_keys` — which keys are user-set vs default-derived.
`save()` only persists explicit keys, so defaults never get baked in.
Plus `stale_defaults={key: [old_value, ...]}` constructor param:
load() drops saved matches and reverts to current default, then
auto-rewrites the file once so the stale value is purged from disk
(no repeated log spam, no resurfacing). Dual-default cleanup aligns
the `.get()` fallback with the SettingsManager default.

**Live validation** against VolcanoAI's stale file (2026-05-18):
`federation_timeout_seconds`: 5 on-disk → 30 in-memory; on-disk key
removed; `selected_region=hawaii` preserved.

**Tests** (15): `TestExplicitKeyTrackingIssue62` (5),
`TestStaleDefaultsRegistryIssue62` (6) in `tests/test_common.py`;
`TestStaleFederationTimeoutMigrationIssue62` (3) in
`tests/test_map_collector_federation.py`; plus
`test_no_file_means_no_auto_save_on_fresh_install`.

**Going-forward recipe** for the next default bump:
1. Change the value in the `defaults={}` block.
2. Add `stale_defaults={"key": [OLD_VALUE]}` to the constructor
   (extend the list if there's already a stale history).
3. Next fleet pull → migrates automatically; operator does nothing.


---

## Issue #63: delivery_counters write-path canary — surface silent failures (2026-05-18)

**Symptom**: Issue #58 was "fixed" by patching the sandbox ReadWritePaths,
but verification was a synthetic write inside the systemd profile. If
something else broke the write path (mid-run permission change, schema
corruption, disk full), nothing surfaced until natural traffic flowed
hours later — Issue #58 itself burned 18h of silent `sqlite3.OperationalError`
warnings before detection. Reliability backlog #2.

**Root cause class**: `DeliveryCounters.record()` wraps `_persist()` in
`try/except sqlite3.Error: logger.warning(...)`. Operator visibility into
write-path failures required grep'ing journald — too slow when the
delivery counters are the operator's primary view into bridge behavior.

**Fix** (`src/gateway/delivery_counters.py`):
1. **Startup preflight** at `DeliveryCounters.__init__`: writes `meta.preflight_ts` + `meta.preflight_ok` and reads back. Failure logs at **ERROR** and surfaces in `snapshot()["health"]`. Catches Issue #58 class at construction, not 18h later.
2. **Runtime write-error tracking**: `record()` increments `consecutive_write_errors` on every failure, clears it on every success. First failure logs ERROR; subsequent throttle to DEBUG; recovery logs INFO with the prior failure count. snapshot surfaces `consecutive_write_errors`, `last_write_error_ts`, `last_write_error`.
3. **Cross-process visibility**: preflight result persists to the DB so the map daemon's reader-side `snapshot()` sees the gateway's writer-side preflight. `health.last_successful_write_ts` aliases `meta.last_event_ts` — the natural heartbeat for "writes are flowing."

**Health block shape** (returned in `/api/gateway/delivery.health`):
```json
{
  "db_path": "/home/<op>/.local/share/meshforge/delivery_counters.db",
  "preflight_ok": true,            // last writer's preflight result
  "preflight_ts": 1779148881.1,    // when it ran
  "preflight_error": null,         // populated in writer process only
  "last_successful_write_ts": 1779148881.1,
  "consecutive_write_errors": 0,
  "last_write_error_ts": null,
  "last_write_error": null
}
```

**Tests** (11 new in `tests/test_delivery_counters.py`):
- `TestPreflightHealthy` (3) — preflight runs at construction, populates health block, persists for cross-process reads.
- `TestPreflightFailureSurfaces` (2) — failure logs at ERROR with the actual sqlite3 message; snapshot reflects `preflight_ok=False`.
- `TestRuntimeWriteErrorTracking` (4) — counter increments, recovery clears, first-failure ERROR throttling, recovery INFO log.
- `TestLastSuccessfulWriteTsHeartbeat` (2) — heartbeat aliases `last_event_ts`; doesn't advance on write failure (so stale heartbeat = real signal).

**Operator detection recipes**:
```bash
# Is the write path working right now on a fleet box?
curl -s http://<box>:5000/api/gateway/delivery \
  | jq '.health | {preflight_ok, consecutive_write_errors, last_write_error,
                   age_s: (now - .last_successful_write_ts)}'
# Healthy: preflight_ok=true, consecutive_write_errors=0,
#          last_write_error=null, age_s < expected traffic interval.
# Broken: any field other than that.

# Startup-time preflight failures:
sudo journalctl -u meshforge-gateway --since "5 min ago" \
  | grep -E "delivery_counters preflight (OK|FAILED)"
```

**Why not also a periodic heartbeat thread**: considered, deferred.
The combination of (1) startup preflight + (2) runtime error tracking
+ (3) last-successful-write heartbeat already catches every Issue #58
class. A dedicated heartbeat thread would catch the "process running,
zero natural traffic, write path silently fails" case — currently
unfalsifiable on a quiet fleet. If that case bites in practice, add a
thread that bumps `meta.heartbeat_ts` every 60s; the test harness
already handles instance state vs persisted state.


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

**Prevention (deferred)**: pre-flight `socket.AF_UNIX` probe with 5s
timeout before `_RNS.Reticulum()`; on timeout fall through to the
non-fatal `except Exception` in `_init_rns_main_thread`.
