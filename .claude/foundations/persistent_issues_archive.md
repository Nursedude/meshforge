# MeshForge Persistent Issues — Archive

> **Purpose**: Historical record of resolved issues.
> These were moved from `persistent_issues.md` to reduce file size.
> Last updated: 2026-05-18
>
> **Note**: GTK-specific issues (#2, #11, #13, #14, #15) were removed during
> the 2026-02-21 cleanup. GTK4 was removed in v0.5.x; TUI is the only interface.

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

## Issue #33: Gateway Bridge Field Validation — First Green End-to-End (2026-04-18)

**Status**: RX path (Meshtastic→NomadNet) validated end-to-end for the first time on hardware.
Remaining: field TX validation with a second Meshtastic radio on the `meshforge` channel.

**Environment**:
- HAT: US / SHORT_TURBO / channel_num=8 (already correct at start; Phase 1 was no-op)
- RNode: Silicon Labs CP2102 on `/dev/ttyUSB0` → added as `[[RNode LoRa]]` in `/etc/reticulum/config`
- rnsd runs as root; NomadNet runs as `wh6gxz` via pipx venv — `--rnsconfig /etc/reticulum` keeps them aligned
- NomadNet LXMF delivery hash: `d69f7e802960b39561768588fc6e6082` (matched pre-configured `default_lxmf_destination`)
- Gateway LXMF source hash: `0123456789abcdef0123456789abcdef` (from `~/.config/meshforge/gateway_identity`)

**Non-obvious gotchas** (recurring footguns — worth surfacing in install path):

1. **LXMF is NOT installed with RNS**. `pip install rns` does not pull `lxmf`. The gateway's
   `_rns_bridge_connection.py` logs `"RNS/LXMF library not installed - bridge cannot connect"`
   and continues with RNS subsystem marked `disabled`. Fix:
   ```
   pip3 install --user --break-system-packages lxmf
   ```
   The NomadNet pipx venv has its own LXMF but it is not on the system Python path.

2. **MQTT uplink/downlink default off**. Fresh Meshtastic devices ship with `uplinkEnabled=false`
   on all channels. Gateway `mqtt_bridge` mode receives NOTHING until at least one channel has
   uplink enabled. Preferred pattern: dedicated bridge channel (named `meshforge` in our config)
   with its own PSK, leave primary channel untouched for local mesh privacy:
   ```
   meshtastic --ch-index 2 --ch-set uplink_enabled true --ch-set downlink_enabled true
   ```

3. **gateway.json `mqtt_channel` must match channel NAME, not preset name.** The gateway
   subscribes to `msh/{REGION}/2/json/{CHANNEL_NAME}/#`. Default config ships with `"LongFast"`.
   Update both `meshtastic.mqtt_channel` and `mqtt_bridge.channel` to the actual channel name.

4. **Local HAT TX uplink to MQTT depends on firmware**. ~~Only RX from other nodes is uplinked.~~
   **UPDATED 2026-04-20 (fleet-host-3, meshtasticd 2.7.15)**: the local HAT's own TX *does* uplink when
   `mqtt.enabled=true`, `mqtt.json_enabled=true`, and the sending channel has `uplinkEnabled=true`.
   A send of `meshtastic --host localhost --sendtext 'probe' --ch-index 2 --dest '!ffffffff'`
   appears on `msh/US/2/json/meshforge/{local_node_id}` within ~1s and is bridged to NomadNet
   end-to-end (confirmed by `Message bridged` gateway log + conversation file under
   `~/.nomadnetwork/storage/conversations/<gateway_hash>/`). Older firmware may suppress local TX —
   if you hit a silent bridge on an older fleet box, upgrade meshtasticd before blaming the gateway.
   To exercise the RX path without a HAT at all (synthetic probe):
   ```
   mosquitto_pub -h 127.0.0.1 -t 'msh/US/2/json/meshforge/!deadbeef' \
     -m '{"payload":{"text":"test"},"sender":"!deadbeef","type":"text","channel":2,"to":4294967295,"from":3735928559}'
   ```

5. **traffic.log permission noise**. `monitoring/traffic_storage.py` tries to write to
   `~/.cache/meshforge/logs/traffic.log`; fails with `Errno 13` when the dir doesn't exist or
   is root-owned. Non-fatal, gateway still bridges — but noisy. Create dir at install time or
   handle `FileNotFoundError` by creating parents.

**Regression guards** (all 15 pass as of 2026-04-18):
- `tests/test_regression_guards.py` — TCP/MQTT/service-check/PathHome/shell/event-bus contracts
- `python3 scripts/lint.py --all` — MF001-010 clean

**Rollback**: `/etc/reticulum/config.bak.20260418-074417` preserves pre-change state.


---

## Issue #34: mqtt_bridge topic shape mismatch (2026-04-18)

**Symptom**: `bridge_mode=mqtt_bridge` gateway reports "MQTT bridge handler connected"
but never receives any real mesh traffic from local meshtasticd. Gateway appears green
in logs; actual RX count stays at zero. Meanwhile `bridge_mode=message_bridge` is the
only mode that delivers messages — at the cost of holding :4403 forever and starving
the :9443 web UI.

**Root cause**: `mqtt_bridge_handler._on_connect` subscribed to
`{root}/{REGION}/2/json/{channel}/#`, but meshtasticd 2.7.x publishes to
`{root}/2/json/{channel}/{node}` — no region segment. MQTT `+` is single-segment,
so one subscription pattern can't match both shapes.

**How it slipped through Issue #33's validation**: the "first green end-to-end"
on fleet-host-1 was validated with a crafted `mosquitto_pub -t 'msh/US/2/json/meshforge/...'`
that happened to match the region-ful pattern. Nobody ever observed meshtasticd's
real publishes go through the bridge.

**Fix** (`be6f411`):
- `_on_connect` now subscribes to both shapes for JSON and protobuf:
  `{root}/+/2/json/{channel}/#` and `{root}/2/json/{channel}/#`
- TX publish path omits the region segment when `mqtt_bridge.region == ""` to match
  what meshtasticd subscribes to for downlink.
- `MQTTBridgeConfig.region` default changed from `"US"` to `""`. Explicit `"US"` or
  similar stays valid for daemon builds that DO include region in the topic path.

**Validation** on fleet-host (meshtasticd 2.7.15):
```
mosquitto_pub -h 127.0.0.1 -u mesh_publish -P <pass> \
  -t 'msh/2/json/meshforge/!deadbeef' \
  -m '{"payload":{"text":"…"},"sender":"!deadbeef","type":"text","channel":3,"to":4294967295,"from":3735928559,"id":9999001}'
```
→ gateway logs `node_tracker | Added new node: !deadbeef` and
`gateway.cli | Message bridged: meshtastic -> !ffffffff`. :9443 stays healthy (200),
:4403 has no persistent TCP client.

**Fleet state post-fix** (2026-04-18):
- fleet-host, fleet-host-3: `bridge_mode=mqtt_bridge`, `region=""`, restarted, subscribed to both shapes
- moc, fleet-host-1, fleet-host-2: no `gateway.json` exists — picked up code change, ready for first config
- Backups preserved as `gateway.json.bak.20260418-*`

**Prevention**:
- Future MQTT-bridge validation must use real meshtasticd publishes, not crafted
  `mosquitto_pub` commands that assume a topic shape.
- When adding MQTT topic logic, default to subscribing to every plausible shape
  rather than one "correct" one — MQTT wildcards can't match structurally different paths.


---

## Issue #37: NomadNet AuthenticationError on startup — rnsd rpc_key mismatch (2026-04-20)

**Symptom**: NomadNet crashes on startup with a Python traceback ending in:
```
File ".../RNS/Reticulum.py", line 1094, in get_rpc_client
    return multiprocessing.connection.Client(
        self.rpc_addr, family=self.rpc_type, authkey=self.rpc_key)
File ".../multiprocessing/connection.py", line 964, in answer_challenge
    raise AuthenticationError('digest sent was rejected')
```
Observed on fleet-host-1 and fleet-host-2 with fresh NomadNet installs. Repeating at every launch attempt.

**Root cause**: RNS shared-instance RPC uses `multiprocessing.connection.Client` with an
authkey derived from the RNS config that NomadNet loaded. If the **rnsd daemon** currently
listening on `@rns/default` was started with a DIFFERENT config dir (or a config that has
since been regenerated), its authkey differs and it rejects the client's digest.

Common triggers:
1. rnsd was started before `/etc/reticulum/config` existed (systemd auto-start on first
   boot), so it generated a key from `~/.reticulum/config` which has since been replaced.
2. `/etc/reticulum/config` was regenerated/edited after rnsd already loaded an older
   version — rnsd is still running with the old key.
3. A stale rnsd process from a prior install is still bound to `@rns/default` while a
   newer rnsd's config is what we expect to see.
4. Two users on the same box each have their own `~/.reticulum/config` and whichever
   rnsd won the race owns the abstract socket; the loser's config diverges.

**Not the cause on this fleet**: missing `--rnsconfig` flag. MeshForge's TUI launcher
always passes `--rnsconfig /etc/reticulum` via `_get_rns_config_for_user()`
(`src/launcher_tui/handlers/nomadnet.py:188`). The flag is unconditionally present.

**Wrapper patch (shipped in version 6)**: `nomadnet_wrapper.py` now catches
`multiprocessing.context.AuthenticationError` in `_safe_get_interface_stats`. NomadNet
starts without interface stats instead of crashing. See
`src/launcher_tui/handlers/_nomadnet_install_utils.py:_WRAPPER_VERSION` to force
re-creation on each fleet box. The wrapper is regenerated on every NomadNet launch via
TUI if the version marker changed.

**Diagnostic checklist** when AuthenticationError reappears on a box:
1. `ps -ef | grep rnsd` — note PID, config flag (`-c` or absence), start time. If
   rnsd is older than `/etc/reticulum/config`, it loaded a pre-edit key.
2. `sudo systemctl status rnsd` — confirm it's the systemd-managed one, not a
   leftover. Start time here vs. file mtime of `/etc/reticulum/config` is the tell.
3. `grep -rE '^\s*rpc_key|^\s*shared_instance_rpc_key' /etc/reticulum ~/.reticulum 2>/dev/null` —
   any explicit `rpc_key` entries? If different between rnsd's and the client's config,
   that's the proof. Legacy `shared_instance_rpc_key` lines (silently ignored by RNS)
   also flagged — rename them to `rpc_key`.
4. `ls -la /home/*/.reticulum/config 2>/dev/null` — are there competing per-user
   configs any fleet script might race with?
5. `lsof 2>/dev/null | grep '@rns/default'` — identify which PID currently owns the
   abstract socket.

**Real fix** (manual, per-box): `sudo systemctl restart rnsd` **after** `/etc/reticulum/config`
is in its final state. That forces rnsd to re-derive the key from the current config, aligning
with what NomadNet loads on next start.

**Prevention**: MeshForge install paths should order "write /etc/reticulum/config" BEFORE
"start rnsd" (not the reverse). If the Issue #37 wrapper patch masks the crash, a follow-up
preflight check should explicitly probe the RPC socket and warn the operator — that's
future work, not part of this ship. (Issue #41 closes the gateway-side complement by
pinning `rpc_key` into all MeshForge-written client configs.)


---

## Issue #38: NomadNet single-identity consolidation (2026-04-20)

**Background**: fleet-host-3 was running TWO NomadNet processes in parallel — a `--daemon`
on the default storage dir (`~/.nomadnetwork/`) and an interactive TUI on a separate
dir (`~/.nomadnetwork-interactive/`) with its own identity. Gateway targeted the
daemon. Operator read the interactive. Bridged mesh messages landed in a directory
no human opened. Issue #35 documents the user-visible symptom.

**Resolution (fleet-host-3, 2026-04-20)**: consolidated to a single NomadNet per box, wrapped
in a detached tmux session managed by a systemd-user service. Deployed end-to-end
and validated with a real HAT probe (`consolidation-1776737308` → 1.0 s round-trip
→ visible in tmux TUI with the ✉ unread indicator).

**The pattern**:
- One NomadNet process per box, using the default storage dir (`~/.nomadnetwork/`).
- Detached tmux session `nomadnet` owns the TUI. Operator attaches on demand:
  `tmux attach -t nomadnet`. `Ctrl-b d` to detach without killing the process.
- Systemd-user unit `~/.config/systemd/user/nomadnet.service` manages lifecycle.
  See the canonical template at `templates/systemd/nomadnet-user.service`.
- `loginctl enable-linger $USER` so the service survives operator logout.
- rnsd remains a system service as root (no user rnsd.service needed or wanted).
- Gateway's `rns.default_lxmf_destination` points at this single identity's LXMF
  destination hash (NOT the root identity hash — see below).

**Fleet migration steps per box** (fleet-host-3 is the reference; fleet-host-1/fleet-host-2/fleet-host next):

1. `sudo apt install tmux` (now in `scripts/install_noc.sh` for new installs).
2. Backup: `tar -czf ~/nomadnet-backups/<host>-$(date +%Y%m%d-%H%M%S).tgz
   -C ~ .nomadnetwork .nomadnetwork-interactive` (include whichever dirs exist).
3. Choose the identity to keep. Recommendation: the one the operator has been
   actively using (peers already have its hash). On fleet-host-3 this was the interactive's
   identity at `~/.nomadnetwork-interactive/`.
4. Stop both old NomadNet processes cleanly (`sudo kill <pid>`).
5. Archive the old daemon dir: `mv ~/.nomadnetwork ~/.nomadnetwork-daemon-archived-<date>`.
6. Promote the kept dir: `mv ~/.nomadnetwork-interactive ~/.nomadnetwork`.
7. Install the systemd unit from `templates/systemd/nomadnet-user.service` into
   `~/.config/systemd/user/nomadnet.service`.
8. `systemctl --user daemon-reload && systemctl --user enable --now nomadnet`.
9. Verify tmux + NomadNet alive: `tmux capture-pane -t nomadnet -p | head -5`.
10. Resolve the kept identity's LXMF destination hash (see next section).
11. Edit `~/.config/meshforge/gateway.json` → set `rns.default_lxmf_destination` to
    that hash. Back up the old file as `.bak.<timestamp>-preconsolidate` first.
12. Restart gateway: `sudo systemctl restart meshforge-gateway`.
13. Validate with a real HAT probe:
    `meshtastic --host localhost --sendtext 'consolidation-probe' --ch-index <n> --dest '!ffffffff'`.
14. Confirm: gateway log shows `Message bridged` + `LXMF delivery confirmed`, and
    the tmux NomadNet UI shows ✉ unread on the "MeshForge Gateway" conversation.
15. Retain the daemon-archived dir for 7 days, then remove.

**How to resolve an LXMF destination hash offline** (needed for Step 10):
```python
# Via the nomadnet venv python (has LXMF + RNS installed):
python3 <<'PY'
import tempfile, os
cfg = tempfile.mkdtemp(prefix='rns_hashcalc_')
with open(os.path.join(cfg, 'config'), 'w') as f:
    f.write("[reticulum]\n  enable_transport = No\n  share_instance = No\n\n")
import RNS
RNS.Reticulum(configdir=cfg, loglevel=0)
ident = RNS.Identity.from_file('/home/<user>/.nomadnetwork/storage/identity')
dest = RNS.Destination(ident, RNS.Destination.IN, RNS.Destination.SINGLE, "lxmf", "delivery")
print("LXMF destination hash:", dest.hash.hex())
PY
```

**Backout** (if validation fails): stop the new unit, swap the dirs back
(`mv ~/.nomadnetwork ~/.nomadnetwork-consolidated-failed && mv ~/.nomadnetwork-daemon-archived-<date> ~/.nomadnetwork`),
restore `gateway.json` from the `.bak.<timestamp>-preconsolidate` copy, restart
gateway. Old daemon + interactive processes can be resurrected manually or via
prior TUI menu actions.

**Side observation (2026-04-20)**: the gateway itself also hit an
`AuthenticationError: digest sent was rejected` in its `first_hop_timeout` RPC
call during LXMF path lookup (same root cause as Issue #37). The gateway swallowed
it and delivery still completed in 0.1 s — so non-fatal, but worth a future
defensive patch in `src/gateway/_rns_bridge_connection.py` to match the wrapper's
resilience. (Closed by Issue #41 pin.)

**Prevention**: new installs pick up the consolidated pattern automatically because
`templates/systemd/nomadnet-user.service` is now the tmux-wrapped version and
`install_noc.sh` adds `tmux` to the apt install list. Do NOT manually re-introduce
a `--daemon` NomadNet alongside the tmux one.


---

## Issue #25: rnsd PermissionError on /etc/reticulum/storage/ratchets

### Symptom
rnsd crashes in a background thread with:
```
PermissionError: [Errno 13] Permission denied: '/etc/reticulum/storage/ratchets'
```
Additionally, `/etc/reticulum/identity` is never created, and the TUI "Show local identity" shows "No identity provided, cannot continue."

### Root Cause
RNS added **key ratcheting** support which requires a `ratchets/` subdirectory under storage. `Identity.persist_job()` runs in a background thread and calls `os.makedirs(ratchetdir)`. The install script didn't create this directory, and `ReticulumPaths.ensure_system_dirs()` was defined but never called at runtime.

### Fix (v0.5.x, 2026-02-09)
**Self-healing at runtime** — MeshForge now creates the directories automatically:
1. `startup_checks.check_all()` calls `ensure_system_dirs()` at TUI launch
2. `rns_bridge._init_rns_main_thread()` calls it before RNS init
3. `install_noc.sh` creates `storage/ratchets/` during install
4. `check_rns_storage_permissions()` diagnostic detects the issue
5. After fixing dirs, MeshForge auto-restarts rnsd via `apply_config_and_restart()`

### Files
- `src/utils/paths.py` — `ETC_RATCHETS`, `ensure_system_dirs()`
- `src/gateway/rns_bridge.py` — Self-heal in `_init_rns_main_thread()`
- `src/launcher_tui/startup_checks.py` — Self-heal in `check_all()`
- `src/core/diagnostics/checks/rns.py` — `check_rns_storage_permissions()`
- `scripts/install_noc.sh` — Pre-create dirs
- `src/launcher_tui/rns_menu_mixin.py` — Fixed `rnid` invocation

### Status: RESOLVED


---

## Issue #26: ReticulumPaths Fallback Copies Cause Config Divergence

### Symptom
`.reticulum` interface configuration is "lost" between sessions. RNS config changes made in the TUI have no effect. rnsd uses a different config file than what MeshForge reads/writes.

### Root Cause
**Four separate copies** of `ReticulumPaths` existed in the codebase:
1. `src/utils/paths.py` — **Canonical** (correct: checks `/etc/reticulum`, XDG, `~/.reticulum`)
2. `src/launcher_tui/main.py` — Fallback (missing `get_interfaces_dir`, `ensure_system_dirs`)
3. `src/launcher_tui/rns_menu_mixin.py` — Fallback (missing `ensure_system_dirs`)
4. `src/core/diagnostics/checks/rns.py` — Fallback (**WRONG: skipped `/etc/reticulum` and XDG entirely**)
5. `src/gateway/rns_bridge.py` — Fallback (missing `get_interfaces_dir`, `ensure_system_dirs`)

### Fix (v0.5.x, 2026-02-09)
**Eliminated all fallback copies.** Every file now imports directly:
```python
# NO try/except, NO fallback class
from utils.paths import ReticulumPaths
```

### Prevention
- **NEVER** duplicate `ReticulumPaths`. Always import from `utils/paths.py`.
- `utils/paths.py` is the SINGLE SOURCE OF TRUTH for all path resolution.

### Status: RESOLVED


---

## Issue #28: API Proxy Steals fromradio Packets from Native Web Client

**Date Identified**: 2026-02-10
**Severity**: Critical (breaks meshtasticd web client at :9443)

### Symptom
When MeshForge is running, the Meshtastic web client at `ip:9443` shows
no data. The gateway bridge works fine (RX green), NomadNet talks to other
RNS nodes normally. Only the native web client is broken.

### Root Cause
`MeshtasticApiProxy` was **enabled by default**. It continuously polls
`GET /api/v1/fromradio` from meshtasticd's HTTP API on port 9443.
This endpoint is **queue-based** — each GET pops the next protobuf packet.
MeshForge drained the queue before the native web client could read it.

### Fix Applied
1. **Default `enable_api_proxy` to `False`** in `MapServer.__init__`
2. **Added `--enable-api-proxy` CLI flag** for explicit opt-in
3. **`/mesh/` redirects to native `:9443`** when proxy is disabled

### Prevention
Never enable the API proxy by default. The gateway (TCP:4403) and
web client (HTTP:9443) are separate channels and should coexist.

### Status: RESOLVED


---

## Health Check Reconciliation (2026-02-20) — Moved from persistent_issues.md

The code review health check (2026-01-24) identified 5 critical (C1-C5) and 1 high (H1)
issues. All resolved:

| ID | Issue | Status | Evidence |
|----|-------|--------|----------|
| C1 | LXMF Source None after partial RNS init | **MITIGATED** | Guard at `rns_bridge.py:579-580` |
| C2 | reconnect.py raises None on early interruption | **FIXED** | `reconnect.py:176-178` |
| C3 | Unbounded node tracking dicts (memory leak) | **FIXED** | MAX_NODES caps + eviction |
| C4 | Stats dict race conditions (24 racy increments) | **FIXED** | threading.Lock added |
| C5 | Atomic write uses deterministic temp path | **FIXED** | `tempfile.mkstemp()` |
| H1 | Non-interruptible shutdown in daemon loops | **FIXED** | `_stop_event.wait()` everywhere |


---

## Issue #1: Path.home() Returns /root with sudo — RESOLVED (2026-02-20)

Zero `Path.home()` violations remain. Use `get_real_user_home()` from `utils/paths.py`.
Fixed last 3 violations in `mqtt_bridge_handler.py`, `cli.py`, `rns_config.py`.
Linter (`scripts/lint.py`) checks MF001. Regression test in `test_regression_guards.py`.

---

## Issue #5: Duplicate Utility Functions — RESOLVED (2026-02-20)

All 20 `safe_import` fallback copies consolidated to direct imports (-220 lines).
Rule: `safe_import` is for EXTERNAL deps only. First-party modules always use direct imports.
Follow-up: `startup_checks.py` converted from `safe_import('utils.service_check')` to direct import.

---

## Issue #6: Large Files — Extraction History (2026-03-02)

8 files split in Session 2 (2026-03-02):
- meshtasticd_config.py: 1,497 → 516 (meshtasticd_templates.py)
- rns.py: 1,505 → 1,306 (rns_templates.py)
- prometheus_exporter.py: 1,523 → 1,399 (metrics_server.py)
- map_http_handler.py: 1,557 → 1,404 (_map_meshtastic_proxy.py)
- map_data_collector.py: 1,568 → 1,320 (_map_collector_rns.py)
- service_check.py: 1,573 → 1,410 (_service_iptables.py)
- rns_bridge.py: 1,599 → 1,349 (_rns_bridge_connection.py)
- nomadnet.py: 1,610 → 1,315 (_nomadnet_rns_checks.py)

Previous extractions (2026-02-06):
- traffic_inspector.py: 2,194 → 442, main.py: 1,799 → 1,489
- node_tracker.py: 1,808 → 989, metrics_export.py: 1,762 → 96
- engine.py: 1,767 → 709, rns_menu_mixin.py: 1,524 → 1,210

---

## Issue #7: Missing File References — RESOLVED

Create scripts before referencing them in menu options. Use commands layer when possible.

---

## Issue #8: Outdated Fallback Versions — RESOLVED

Search for hardcoded version strings when bumping: `grep -rn "0\.[0-9]\.[0-9]" src/*.py`

---

## Issue #9: Broad Exception Swallowing — MOSTLY RESOLVED (2026-02-20)

28/30 fixed across 7 files (tcp_monitor, system_diagnostics, setup_wizard, hardware_config,
rns_sniffer, site_planner). 2 benign by design (packet_dissectors, pskreporter_subscriber).

---

## Issue #10: Map Control Panel Scrollbar Overlap — FIXED (2026-02-25)

Added thin dark-themed scrollbar CSS to `web/node_map.html`.

---

## Handler Registry Migration — COMPLETE (2026-02-28)

49-mixin inheritance chain replaced with handler registry pattern.
See `handler_protocol.py` (Protocol + BaseHandler + TUIContext) and
`handler_registry.py` (register/lookup/dispatch). 60 handler files in
`launcher_tui/handlers/`. `main.py` dropped from 1,947 to 1,148 lines.


---

## Issue #17: Meshtastic Connection Contention (Single-Client TCP) — RESOLVED (2026-04-21 archived)

**meshtasticd only supports ONE TCP client at a time.** Multiple components creating
independent connections causes thrashing every 1-2 seconds.

### Fix: Shared Connection Manager
All components share ONE persistent connection via `get_connection_manager()`.
Short-lived reads use `MeshtasticConnection` context manager.
Long-lived connections acquire `MESHTASTIC_CONNECTION_LOCK`.

### HTTP fromradio Contention Fix
The `/api/v1/fromradio` endpoint is also single-consumer. `send_text_direct()` POSTs
directly to `/api/v1/toradio` without ever reading fromradio. All TX paths use this.

### Prevention (automated)
- Lint `MF007` — no direct `TCPInterface()` outside connection infrastructure.
- `TestTCPConnectionContract` — regression guard against new violations.
- `TestFromradioContract` — TX must use `send_text_direct()`.


---

## Issue #18: Auto-Reconnect on Connection Drop — RESOLVED (2026-04-21 archived)

Gateway uses health monitoring + exponential backoff (1s → 2s → 4s → ... → 30s max)
in `rns_bridge.py`. All persistent connections should have health monitoring.
Release connection manager resources on disconnect.

### Status
Behavioral pattern; no regression guards required. Still the correct pattern for any
new persistent connection code — verify by reading the existing health-monitor in
`rns_bridge.py` before writing new reconnect logic.


---

## Issue #19: RNS Node Discovery from path_table — RESOLVED (2026-04-21 archived)

Use `RNS.Transport.path_table` (not just `destinations`) for complete routing info.
**path_table may be empty immediately after connect** — use delayed checks (5s) and
periodic re-checks (30s).

Location: `src/gateway/node_tracker.py`

### Status
Stable implementation detail since 2026-02. Retain pattern if rewriting node
discovery — don't re-introduce the `destinations`-only shortcut.


---

## Issue #20: Service Detection & Status Display — RESOLVED (2026-04-21 archived)

All 3 components resolved:

1. **Service Detection**: Simplified to systemctl-only for systemd services (SSOT)
2. **Status Display**: Separates "service state" from "detection capability" —
   never shows "FAILED" when service is running
3. **RX Messages**: `event_bus.py` → `websocket_server.py` → TUI live feed

### RNS Socket Detection
RNS uses abstract Unix domain sockets (`\0rns/{instance_name}`), not UDP port 37428.
Use `check_rns_shared_instance()` (3-tier: Unix socket → TCP → UDP fallback).

### Prevention (automated)
- Lint `MF008` — no raw `systemctl is-active` for service state (use `check_service()`).
- `TestServiceCheckContract` — regression guard against raw `systemctl` state checks.
- `TestKnownServicesConsistency` — keeps `KNOWN_SERVICES` correct.
- `TestEventBusThreadPool` — event-bus emission contract stable.
- UI-layer rule still in force: always distinguish "service state" from "detection
  capability" in any new status display code.


---

## Issue #16: Gateway Message Routing Reliability — RESOLVED (2026-04-21 archived)

Delivery is **best-effort** — inherent to mesh networking. Message queue persists
to SQLite for retry. UI always shows "Sent (delivery not guaranteed)" or "Queued".

Files: `commands/messaging.py`, `gateway/rns_bridge.py`, `gateway/message_queue.py`

### Status
Behavioral pattern, stable since early gateway bring-up. Retain UI wording and
SQLite retry path in any new messaging flow — don't show "Delivered" for
best-effort mesh transports.


---

## Issue #24: Python Environment Mismatch (rnsd + meshtastic module) — ARCHIVED (2026-04-21)

rnsd's `Meshtastic_Interface.py` plugin requires the `meshtastic` Python module.
pipx isolation, different Python versions, or user-vs-system site-packages can
make the module invisible to rnsd.

**Fix**: `sudo pip3 install --break-system-packages --ignore-installed meshtastic`
or install into the interpreter rnsd uses: `head -1 $(which rnsd)` then use that
interpreter's pip.

**Diagnose**: `sudo python3 -c "import meshtastic; print(meshtastic.__version__)"`

### Status
Upstream-driven; stable since initial bring-up. Still a relevant footgun for
fresh Pi installs — the plugin itself is disabled on the fleet per Issue #36.


---

## Issue #27: rnsd is OPTIONAL — ARCHIVED (2026-04-21)

MeshForge supports two independent transports:
- **MQTT** (mosquitto) — Meshtastic native. Used for preset bridging, monitoring.
- **RNS** (rnsd) — Reticulum. Used for LXMF messaging, cross-protocol bridging.

**Meshtastic preset bridging** (LF ↔ ST) needs only mosquitto — both radios MQTT
uplink/downlink to the same broker with same channel/PSK. No gateway code needed.

**Full NOC** (Meshtastic + RNS) uses both transports. They coexist independently.

### Status
Design principle, stable. When scoping a deployment, check whether RNS is
actually required — many preset-bridging use cases don't need it.


---

## Issue #36: Meshtastic_Interface rnsd plugin — keep disabled (2026-04-20, archived 2026-04-21)

**Decision**: the rnsd `Meshtastic_Interface.py` plugin is installed
(`/etc/reticulum/interfaces/Meshtastic_Interface.py.disabled`) but NOT loaded.
MeshForge's gateway owns the text-bridging contract.

**Why disabled**: if enabled, the plugin forwards only packets with
`decoded.portnum == RETICULUM_TUNNEL_APP` (native RNS-over-Meshtastic tunneling),
which would bypass `gateway/message_routing.py`, the `gateway.log` audit trail,
the SQLite retry queue, LXMF source identity aggregation, and all plain-text
Meshtastic support.

**Coexistence**: no port conflict with the current `mqtt_bridge` gateway (which
uses MQTT for RX and HTTP `/api/v1/toradio` for TX, never holding `:4403`). The
plugin could be enabled alongside without fighting for the TCP slot.

**If a future use case needs it** (e.g. native RNS peers communicating over
LoRa without any MQTT broker):
```
sudo mv /etc/reticulum/interfaces/Meshtastic_Interface.py.disabled \
        /etc/reticulum/interfaces/Meshtastic_Interface.py
sudo systemctl restart rnsd
sudo journalctl -u rnsd -n 50   # expect "Meshtastic: Opening tcp device..."
```
Requires `python3 -c 'import meshtastic'` from rnsd's interpreter (Issue #24).

### Status
Stable decision; do not re-enable casually. The plugin is complementary
infrastructure, not a gateway replacement.


---

## Issue #30: NomadNet RPC ConnectionRefusedError (2026-03-11, archived 2026-04-21)

NomadNet crashes on startup when `get_interface_stats()` can't connect to rnsd's RPC socket.

**Root causes**: RNS version mismatch (pipx venv vs system rnsd), user mismatch
(root rnsd vs user NomadNet), rnsd still initializing, or stale state.

**Fix**: Pre-launch check in `_nomadnet_rns_checks.py` uses NomadNet's own Python
interpreter to test RPC (not system rnstatus). Detects version mismatches and
suggests `pipx upgrade nomadnet`. Auto-restarts rnsd if needed.

Post-failure diagnosis in `nomadnet.py:_diagnose_nomadnet_error` detects
`ConnectionRefusedError` / `Errno 111` patterns in NomadNet logfile.

### Status: RESOLVED


---

## Issue #31: No Silent Persistent System Changes on Startup (2026-03-12, archived 2026-04-21)

**Rule**: NEVER make persistent system changes silently on startup.

MeshForge's `auto_lock_port()` was silently adding iptables REJECT rules on port 9443
every TUI launch, persisting after exit. This broke the Meshtastic web UI.

**Prohibited on startup**: iptables rules, cron jobs, udev rules, systemd unit mods,
config file overwrites (see also Issue #22).

MeshForge **observes and assists** — it does not take over infrastructure.
Explicit user actions (e.g., service_menu lock/unlock) are acceptable.

**Cleanup for affected users**: `sudo iptables -D INPUT -p tcp --dport 9443 ! -s 127.0.0.1 -j REJECT`

### Status: RESOLVED (behavioral design principle)


---

## Issue #32: NomadNet "Enabled but Disconnected" Interfaces (2026-03-13, archived 2026-04-21)

**Symptoms**: NomadNet shows interfaces as "enabled" but disconnected with no RX/TX.
MeshForge status says "rnsd: RUNNING (shared instance available)" when rnsd is actually dead.

**Root causes** (3 bugs):

1. **pgrep false positive**: `check_process_running('rnsd')` fallback used `pgrep -f 'python.*rnsd'`
   which matched any process mentioning "rnsd" (shell invocations, test runners, editors).

2. **Blind status display**: NomadNet status printed "(shared instance available)" without calling
   `check_rns_shared_instance()` — it assumed shared instance from process detection alone.

3. **No diagnostics when down**: Interface health checks (rnstatus, blocking interfaces) only
   ran when rnsd was detected as "running". When detection was wrong or rnsd was genuinely
   down, user got zero actionable diagnostic info.

**Fixes** (2026-03-13):

- `_port_detection.py`: Tightened pgrep regex, added `/proc/{pid}/cmdline` verification
  via `_verify_process_cmdline()` to eliminate self-matches. Same fix for `check_process_with_pid()`.
- `nomadnet.py`: Status display now calls `get_rns_shared_instance_info()` to verify shared
  instance. Shows three states: verified connected (with method), running but no shared instance,
  or not running (with systemd fix hint). Blocking interface diagnostics now shown even when
  rnsd is down.

**Prevention**:
- `check_process_running()` now verifies all pgrep hits via `/proc/cmdline`
- Status display always distinguishes process detection from shared instance availability
- `find_blocking_interfaces()` runs regardless of rnsd state for pre-startup diagnostics

### Status: RESOLVED
---

## Issue #39: Gateway bridge becomes identifying and two-way directable (2026-04-21)

**Background**: until this change, the Meshtastic↔RNS bridge was half-duplex
in both directions — RNS peers saw every bridged mesh message as coming from
the gateway's single LXMF identity with only `[Mesh:xxxx]` (last 4 hex of the
node id) in the body, and had no way to reply to a specific Meshtastic node.
Everything sent by an RNS peer went out as `!ffffffff` broadcast. Extends
Issue #35 (which documented the gateway-hash conversation indexing) with a
real fix for identity + addressability rather than just a UI workaround.

**What changed** (single commit, `src/gateway/rns_bridge.py` +
`src/gateway/mqtt_bridge_handler.py` + `src/gateway/node_tracker.py`):

1. **Mesh → RNS identity surfaces in the LXMF envelope, not the body.**
   `_process_mesh_to_rns` now consults `node_tracker.get_node_by_mesh_id()`
   (which has had the `long_name`/`short_name` since forever — it was simply
   never queried, see `rns_bridge.py:1442` standing TODO) and builds:
   - `title = "<long_name> (<!id>) via Meshtastic"` when name is known,
     else `"<!id> via Meshtastic"`.
   - `fields` dict with namespaced keys so future LXMF clients can parse
     without guessing which string is which:
     ```
     meshforge_from_id       e.g. "!ebfa1b11"
     meshforge_from_long     e.g. "HAT-fleet-host-3"
     meshforge_from_short    e.g. "HAT3"
     meshforge_channel       e.g. "meshforge"
     meshforge_source_network "meshtastic"
     ```
   - Body is now the clean original text — no `[Mesh:xxxx]` prefix.

2. **RNS → Mesh supports `@address` directed downlink.**
   `_process_rns_to_mesh` parses the leading token of the LXMF body:
   - `@!ebfa1b11 roger` → DM to node `!ebfa1b11`, body `[RNS:xxxx] roger`.
   - `@HAT3 roger`     → short_name resolved via
     `node_tracker.get_node_by_short_name()` (new helper); DM to resolved id.
   - `plain text`      → broadcast on the bridge channel (unchanged).
   - Unresolvable `@foo …` → logged, falls through to broadcast with the
     original content preserved (no silent drop, no accidental misdelivery).
   - The `destination` is threaded through the persistent-queue payload as
     a new `destination` key.

3. **MQTT publish honors the directed `to` field.** `publish_to_mqtt` in
   `mqtt_bridge_handler.py` converts a `!xxxxxxxx` destination to its
   numeric form (`int(hex, 16)`) and adds `"to": <numeric>` to the outbound
   JSON only when present. No destination = no `"to"` field (meshtasticd
   treats that as broadcast, preserving the current semantic).

**Operator recipe** (what this looks like from NomadNet):
- A mesh message from the HAT now shows with subject line
  `HAT-fleet-host-3 (!ebfa1b11) via Meshtastic` and body `<just the text>`. The
  conversation is still indexed under the gateway's LXMF source hash
  (`f68c2f56…` on fleet-host-3), because that is who LXMF thinks the sender is —
  but the subject line resolves Issue #35's "who actually sent this" gap.
- To reply DM to a specific Meshtastic node, type
  `@!ebfa1b11 <text>` or `@HAT3 <text>` in the NomadNet message box. The
  gateway sends it as a Meshtastic DM (not broadcast). Verify with
  `mosquitto_sub -v -t 'msh/#'` — the published JSON will contain
  `"to": 3958611729` (decimal of `0xebfa1b11`).
- To broadcast, send a plain message with no `@` prefix — same as before.

**Backward compatibility**:
- Existing LXMF clients that did not parse the old `[Mesh:xxxx]` body prefix
  (which is every client we know of on the fleet — grep confirmed nothing
  in meshforge or meshanchor code depends on the old prefix) see a cleaner
  body and a richer title. No regression.
- Tests that asserted on the old body prefix have been rewritten to assert
  title + clean body instead. Plain-broadcast reply semantics unchanged.
- `send_to_rns` gained optional `title=` and `fields=` kwargs; the
  persistent-queue retry path (`rns_bridge.py:810`) uses defaults and is
  unchanged.

**Known limits (deferred to a later plan)**:
- No per-Meshtastic-node RNS identities. Meshtastic nodes still do not
  appear as first-class peers in NomadNet's Known Nodes page. The single
  gateway identity is still the sender of every bridged message; the
  `[Mesh:…]` era just got replaced by a richer LXMF envelope. If we want
  distinct RNS peers per mesh node we'd need announces-per-node plus
  inbound routing-by-destination — larger architectural ship, held for
  field-feedback on Issue #39's `@address` convention first.

**Prevention / where future work hooks in**:
- The `meshforge_*` fields-dict namespace is intentionally reserved for
  gateway-authored metadata. Any future sidecar data (hops, SNR, RSSI,
  position, portnum) should use this namespace, not collide with LXMF's
  own `FIELD_*` reserved keys.
- When a future plan introduces per-node identities, the `@address`
  resolution helper `_resolve_mesh_destination` is the single choke point
  to extend — callers do not care whether a destination came from hex-id
  parsing, short-name resolution, or future RNS-identity-to-mesh-node
  routing.


---



## Issue #3: Services Not Started/Verified — MOSTLY RESOLVED (archived 2026-04-25)

**Rule**: Always call `check_service()` before connecting to services.

- **Advisory** (daemons): Warn + continue — service may run outside systemd
- **Blocking** (TUI actions): Show error + fix hint, don't proceed

**Note**: Gateway checks are ADVISORY. Blocking checks caused "waiting for delivery"
regression when mosquitto wasn't detectable via systemctl.

**Remaining** (acceptable): `system_tools_mixin.py` and `service_menu_mixin.py` use
`systemctl status` for display only, not state decisions.

| Service | Port | systemd name |
|---------|------|--------------|
| meshtasticd | 4403 | meshtasticd |
| rnsd | None | rnsd |
| hamclock | 8080 | hamclock |
| mosquitto | 1883 | mosquitto |


## Issue #6: Large Files — ALL UNDER THRESHOLD (archived 2026-04-25)

Only `knowledge_content.py` (1,993 lines) exceeds 1,500 — acceptable as content file.
Monitor files approaching 1,400 lines. Split proactively at 1,000 lines when adding features.

Top files: `meshtastic_protobuf_client.py` (1,433), `service_check.py` (1,410),
`map_http_handler.py` (1,404), `prometheus_exporter.py` (1,399).


## Issue #21: Meshtastic CLI Preset Bug (Upstream) (archived 2026-04-25)

**Not a MeshForge bug.** The Python meshtastic CLI doesn't always apply modem preset
changes correctly. Always verify in browser at `http://localhost:9443` after CLI changes.
Consider direct meshtasticd API calls instead of CLI.
## Issue #35: Gateway-delivered LXMF lands under GATEWAY's hash, not receiver's (2026-04-20)

**Symptom**: operator sends from local Meshtastic HAT on channel `meshforge`, gateway logs
`Message bridged: meshtastic -> !ffffffff` and `LXMF delivery confirmed`, but NomadNet appears
not to show the message. Classic "I sent it, where did it go?"

**Root cause (not a bug)**: the receiving NomadNet indexes the conversation by the LXMF
**sender** identity — which is the **gateway's own** source hash (`f68c2f56…` on fleet-host-3) —
NOT by the `default_lxmf_destination` (which is the RECEIVING NomadNet's own hash) or by the
original Meshtastic node id. Operators looking for a new "inbox" under their own identity, or a
new conversation per-Meshtastic-node, will not find one.

**Verification recipe**:
```
ls -lt ~/.nomadnetwork/storage/conversations/<gateway_lxmf_source_hash>/ | head
```
The newest file's mtime should match the send time. Content check:
```
strings -n 6 <that_file> | grep -E '^\[Mesh:'
```
Look for `[Mesh:<last-4-of-sender-nodeid>] <your text>`.

**Operator guidance**: open the conversation indexed by the GATEWAY's hash in NomadNet.
The text body is prefixed with `[Mesh:xxxx]` where `xxxx` is the last 4 hex chars of the
originating Meshtastic node id. All bridged traffic for a given gateway aggregates into that
single conversation — one-to-many from the NomadNet user's perspective.

**Prevention**: the TUI now has a **Delivery Audit** entry under the Gateway Bridge menu that
lists recent `Message bridged` / `LXMF delivery confirmed` log lines along with the gateway's
LXMF source hash and the NomadNet conversation path, so operators can navigate to the right
thread without filesystem spelunking. See `src/launcher_tui/handlers/gateway.py` → `_show_delivery_audit`.




---

## Issue #40: RNS→Mesh bridge — bytes payload crash + wrong-topic MQTT downlink (2026-04-21)

**Symptom**: Since Issue #33's first-green-end-to-end (2026-04-18), the bridge
stat was stuck at `R→M: 0` across every gateway restart. Meshtastic clients
never received messages sent by NomadNet/RNS peers. The broken path was
simultaneously hiding two independent defects; both had to fall to unblock TX.

**Root cause 1 — `_process_rns_to_mesh()` crashed on bytes LXMF content.**
LXMF delivers `message.content` as `bytes`. `_on_lxmf_receive` at
`src/gateway/rns_bridge.py:1191` stored it into `BridgedMessage.content` without
decoding. Then `_process_rns_to_mesh()` did `body = msg.content or ""` followed
by str operations (`body.startswith('@')`, `prefix + body`). On any real LXMF
message this raised `TypeError: can only concatenate str (not "bytes") to str`;
the `except` branch tried to `_requeue_failed_message()` but that serialized
bytes to JSON and raised `Object of type bytes is not JSON serializable`. Net
effect: every RNS-inbound message crashed twice and was dropped without trace
in the persistent queue.

**Root cause 2 — `publish_to_mqtt()` used an uplink-shaped topic meshtasticd
never subscribes to.** `src/gateway/mqtt_bridge_handler.py:746–750` published
to `msh/{REGION}/2/json/{CHANNEL_NAME}/meshforge`. Meshtasticd 2.7.x subscribes
to `msh/{REGION}/2/json/mqtt/#` — the channel segment is literally the string
`"mqtt"` (firmware PR #3183 convention), never the actual channel name. The
daemon's own log confirmed it was dropping our publishes with
`WARN | [mqtt] JSON downlink received on channel not called 'mqtt' or without
downlink enabled`. Even with cause #1 fixed, every message queued for MQTT
downlink would have been silently dropped by the daemon.

**Fix**:

1. **Decode bytes→str at function entry** in both `_process_rns_to_mesh()` and
   `_requeue_failed_message()`. Handle `bytes`, `str`, and None uniformly with
   `errors="replace"` to survive non-UTF-8 payloads.

2. **Reroute the MQTT-bridge-mode enqueue from `destination="mqtt"` to
   `destination="meshtastic"`.** The persistent queue already has a registered
   handler for `"meshtastic"` (`rns_bridge.py:384`) that dispatches to
   `MQTTBridgeHandler.queue_send()` → `send_text()` → `send_text_direct()` —
   the HTTP `/api/v1/toradio` path that is the Issue #29 blessed single-writer
   TX contract.

3. **`publish_to_mqtt()` is now dead code on the live TX path** and has been
   left in place for potential future resurrection (a true MQTT-only gateway
   with no `:9443` / :443 HTTP access).

**Tests added** (`tests/test_rns_bridge.py`):
`test_bytes_content_is_decoded`, `test_bytes_content_with_at_prefix`,
`test_invalid_utf8_uses_replacement`, `test_mqtt_bridge_mode_enqueues_to_meshtastic`,
`test_bytes_content_serializes_as_str`.

**Field-validation note**: Issue #37 manifests on the gateway side
(divergent rnsd/client identity → divergent rpc_key → AuthenticationError
in `__update_phy_stats()` → `get_packet_rssi()` RPC). Closed by Issue #41.

**Prevention**:
- Never regress the LXMF bytes assumption — `test_bytes_content_is_decoded`.
- Never regress away from the HTTP TX path — `test_mqtt_bridge_mode_enqueues_to_meshtastic`.

## Issues #43-#46 (archived 2026-04-30 from persistent_issues.md)

## Issue #43: MeshCore + AREDN visibility on :5000 map (2026-04-22)

**Symptom**: MeshCore + AREDN nodes visible on external `:8808` (`meshforge-maps`)
but never on built-in `:5000`. Operators assumed `:5000` was Meshtastic-only.

**Three distinct gaps, not one bug**:

1. **MeshCore — position filter, not protocol filter.** `_collect_unified_tracker`
   already ingested every protocol including MeshCore; `node_tracker.to_geojson()`
   filters to `get_nodes_with_position()`, and MeshCore advertisements carry no
   GPS by design → every MeshCore node silently dropped.
2. **AREDN — silent "not configured".** `_collect_aredn` returned `[]` at DEBUG
   when `aredn_node_ips` empty AND `localnode.local.mesh` unresolvable.
   Indistinguishable from "AREDN unsupported."
3. **Diagnostic gap.** `/api/status` never surfaced per-source
   attempt/yield/reason. "Why isn't my node there" meant reading source code.

**Fix** (commit reference; see `git log`): diagnostic dict + `_record_diagnostic`
helper per source (taxonomy: `ok | not_configured | unreachable | no_positions
| source_disabled`). `_collect_unified_tracker` appends non-Meshtastic
position-less nodes to `_nodes_without_position`. `_collect_via_http` switched
from `=` overwrite to `.extend()` so MeshCore entries aren't clobbered. New
`_apply_operator_positions()` promotes entries matching `meshcore_positions` in
`map_settings.json` (full id or prefix match) to real features. `/api/status`
now emits `source_diagnostics`, `nodes_without_position: {total, by_network}`,
and `radio_config` (local HAT preset + channel_num + region + frequency —
so operators can diff fleet boxes on incompatible presets without SSHing).
Map-module log levels raised to INFO so the per-source summary surfaces in
`journalctl -t meshforge` without `--verbose`.

**:8808 parity**: `meshforge-maps` consumes the same `/api/nodes/geojson` —
MeshCore nodes with operator-assigned positions appear on `:8808` automatically.
Position-less MeshCore surfaced via the sibling `nodes_without_position` array
in the response; UI rendering (sidebar list, legend) is an external-repo concern
for `Nursedude/meshforge-maps` CSS/JS.

**Operator recipe — expose a MeshCore node**:
```bash
curl http://localhost:5000/api/status | jq '.nodes_without_position, .source_diagnostics.unified_tracker'
# Grab the node id from nodes_without_position array in /api/nodes/geojson
$EDITOR ~/.config/meshforge/map_settings.json
# Add: "meshcore_positions": {"abc123": {"lat": 19.42, "lon": -155.28, "note": "Hilo"}}
sudo systemctl restart meshforge-map
# Verify:
curl -s http://localhost:5000/api/nodes/geojson | jq '.features[] | select(.properties.source=="operator_positions")'
```

**Operator recipe — "why is node X missing from box Y's map?"**:
1. `curl http://Y:5000/api/status | jq '.radio_config, .source_diagnostics'`.
2. If `radio_config` differs from the box where X is visible (e.g.
   `modem_preset` LongFast vs SHORT_TURBO), that's config-by-design —
   see `project_fleet_radio_heterogeneity` memory. Cross-preset visibility
   requires MQTT/RNS bridging, not RF.
3. Any `source_diagnostics[*].reason_if_zero != "ok"` is a concrete pointer.

**Tests**: `tests/test_map_data_collector_diagnostics.py` (19 tests) lock in
diagnostic shape, reason taxonomy, operator position promotion, AREDN
not_configured vs unreachable distinction, and the `_collect_via_http`
extend-not-overwrite contract.


---

## Issue #44: Map server threading — RNS main-thread invariant (2026-04-22)

**Background**: `meshforge-map` (`src/utils/map_data_service.py`) switched
from single-threaded `HTTPServer` to `ThreadingHTTPServer` to unblock the
stall where a slow `/api/nodes/geojson` (20MB for ~38k MeshCore features)
blocked every subsequent request (fleet-host-3, 2026-04-22). Throughput was
immediately better; a new regression surfaced in the same session.

**The trap — RNS.Reticulum() must init from the main thread**.
`RNS.Reticulum.__init__` at `RNS/Reticulum.py:349-350` unconditionally
calls `signal.signal(SIGINT/SIGTERM, handler)`. CPython only allows
signal handler registration from the main thread; any worker-thread
caller raises `ValueError: signal only works in main thread of the main
interpreter`. With `HTTPServer`, request handlers ran on the main thread
and this never fired. With `ThreadingHTTPServer`, every request runs on
a spawned worker; the first cache-miss `/api/status` or
`/api/nodes/geojson` triggered `_collect_rns_direct` which called
`Reticulum()` on the worker, and `rns_direct` ended up permanently
recording `reason_if_zero: unreachable, notes: "signal only works in
main thread..."`. Worse: at the point `signal.signal` raises,
`Reticulum.__instance` has **already** been assigned (line 226, before
the signal call at line 349), so the singleton is half-initialized and
subsequent in-process code paths may trust it.

**Design invariants** (must hold anywhere RNS + threaded HTTP coexist):

1. **Pre-warm RNS from the main thread before accepting HTTP requests.**
   `MapServer._prewarm_collector()` calls `collector.collect()` once on
   the main thread in `start()` / `start_background()` before creating
   `ThreadingHTTPServer`. This installs RNS signal handlers correctly;
   subsequent worker-thread calls hit the singleton-reuse branch and
   skip init.

2. **Check the singleton via `RNS.Reticulum.get_instance()`, not
   name-mangled attrs.** An earlier fix peeked at
   `_Reticulum__instance` directly and sometimes returned False in a
   worker even after main-thread init had set it (mechanism unknown —
   possibly RNS-version-dependent name mangling). The public
   `get_instance()` classmethod is stable across RNS 1.1.x.

3. **Treat "signal only works in main thread" as benign in init catch
   blocks.** If a worker-thread init does slip through, Transport is
   already running (instance was assigned before signal failed). The
   OSError handler in `_collect_rns_direct` now catches both "reinitialise"
   AND "main thread" patterns and falls through to read
   `Transport.path_table` instead of recording `unreachable`.

4. **`MapDataCollector.collect()` must serialize**. Under threading, two
   simultaneous cache-miss callers would stomp on `_nodes_without_position`
   and `_source_diagnostics` (both reset at the top of each collection).
   Fix: `threading.Lock`, cache-hit fast path outside the lock, inside-lock
   cache re-check so the second caller returns the first caller's fresh
   result.

**Files**:
- `src/utils/map_data_service.py` — `ThreadingHTTPServer`, `daemon_threads=True`,
  `_prewarm_collector()` called from main thread in both start paths.
- `src/utils/map_data_collector.py` — `self._collect_lock`, `collect()` +
  `_collect_locked()` split.
- `src/utils/_map_collector_rns.py` — `_rns_is_initialized()` uses
  `get_instance()`; init `except` covers both `reinitialise` and
  `main thread` OSError messages.

**Tests**: `tests/test_map_data_collector_diagnostics.py`
`TestCollectIsThreadSafe` — concurrent-caller dedup + cache-hit fast-path
non-blocking under held lock.

**Prevention**: any future code that calls `RNS.Reticulum()` (or any
other stdlib function that registers signal handlers) from inside a
web request handler must either (a) run on the main thread, or (b)
ensure a prior main-thread call has already installed the handlers.
The lint catalog (MF009 — "RNS.Reticulum() without configdir") is the
nearest enforcement point; if this becomes a second source of bugs,
extend MF009 to also flag calls outside known main-thread init sites.


---

## Issue #45: NomadNet TUI — tmux-wrapped service is first-class (2026-04-23)

**Background**: Issue #38 consolidated NomadNet onto a tmux-wrapped
systemd user unit (`templates/systemd/nomadnet-user.service` →
`~/.config/systemd/user/nomadnet.service`). On every fleet box the
operator attaches with `tmux attach -t nomadnet` and that's been stable.
The TUI menu, however, still exposed the pre-#38 world: Default
Identity / Interactive Client each offered "Launch Text UI / Start
Daemon / Stop" that went *directly* to the nomadnet binary via pkill +
subprocess, with no knowledge that a supervised process might already
own the identity.

**Three concrete failure modes this produced**:

1. **pkill vs `Restart=on-failure`**: `_stop_nomadnet()` without a
   `config_dir` ran `pkill -f bin/nomadnet`. If the systemd user unit
   was supervising the process, systemd's `Restart=on-failure` respawned
   it within `RestartSec=5`. MeshForge reported "Stopped." The operator
   walked away thinking it was off.
2. **Double-bound LXMF identity**: `_launch_nomadnet_textui()` /
   `_launch_nomadnet_daemon()` spawned a second nomadnet against
   `~/.nomadnetwork/` when the service was already using it. The LXMF
   exclusivity lock on port 37428 flapped; the tmux-wrapped instance
   often lost its delivery socket.
3. **Fragmented logs**: the NomadNet logfile only. `journalctl --user
   -u nomadnet` (service-level ImportError, wrapper crash, pipx venv
   drift) and `tmux capture-pane -p -t nomadnet` (live TUI state, which
   tells you whether the client actually rendered) were invisible. A
   crash-loop was undetectable from the TUI.

**Fix**:

* **SSOT**: `NomadNetServiceOpsMixin._nomadnet_service_state()` returns
  `{unit_installed, active, enabled, sub_state, main_pid, n_restarts,
  tmux_session, error}`. Every guard consults it; no ad-hoc
  `systemctl --user` checks.
* **Top-level menu**: Status + Attach + Service Control + Logs + Config
  + Advanced (holds the old Default Identity / Interactive Client
  submenus unchanged).
* **Never pkill a supervised process**: `_stop_nomadnet(config_dir=None)`
  short-circuits with a msgbox when `service_state.active` is true.
  Identity-scoped stop still pkills (correct for Interactive Client).
* **Raw launch guards**: `_launch_nomadnet_textui`/`_launch_nomadnet_daemon`
  yesno-warn when the service is active; operator must explicitly opt in.
* **Unified logs**: `_unified_logs_menu` in `_nomadnet_io_ops.py`. Health
  Snapshot = service state + journal-tail-20 + tmux-pane-tail-40 +
  logfile-tail-20 on one screen.
* **Sudo → real-user bridging**: `_user_systemctl_argv()` builds
  `sudo -u <user> -H env XDG_RUNTIME_DIR=/run/user/<uid>
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus systemctl
  --user <verb> nomadnet` — the documented root-to-user incantation.
  Falls back to plain `systemctl --user` when not sudo.
* **Unit installer**: copy template → `daemon-reload` + `enable --now`
  + `loginctl enable-linger $USER` for headless survival.
* **Inline config toggles**: yesno-flip for `enable_node`,
  `enable_client`, `announce_at_start`; inputbox setters for
  `display_name`, `node_name`. Existing `_configure_propagation_node`
  moved into the config_ops mixin.
* **`service_check.py` extension**: `user=False` kwarg across 9 helpers.
  User scope skips sudo and the Meshtastic placeholder heuristics.

**Files**:
- `src/launcher_tui/handlers/_nomadnet_service_ops.py` **NEW** — SSOT + service control + tmux attach + unit installer
- `src/launcher_tui/handlers/_nomadnet_config_ops.py` **NEW** — inline toggles + `_configure_propagation_node` (moved)
- `src/launcher_tui/handlers/nomadnet.py` — MRO, top-level menu, status block, stop/launch guards
- `src/launcher_tui/handlers/_nomadnet_io_ops.py` — unified logs menu + 5 focused views + journal/tmux capture helpers
- `src/launcher_tui/handlers/_nomadnet_submenus.py` — `_advanced_menu` + `_reset_identity_dir`
- `src/utils/service_check.py` — `user=False` kwarg across 9 helpers; `_systemctl_argv()` chooser
- `src/utils/_port_detection.py` — `check_systemd_service(user=False)`

**Tests** (33 new assertions): `TestServiceStateDetection`,
`TestWarnIfServiceActive`, `TestStopRefusesWhenServiceManaged`,
`TestLaunchRefusesWhenServiceManaged`, `TestConfigToggles`,
`TestNomadNetServiceOpsSudoBridging`, `TestInstallUserUnit` in
`tests/test_nomadnet_handler.py`. `TestUserScopeSystemctl` (10
assertions) in `tests/test_service_check.py`.

**Operator recipe — install the user unit on a fleet box**:
```
sudo python3 src/launcher_tui/main.py
# NomadNet Client > Service Control > Install systemd user unit
# Afterward: NomadNet Client > Attach tmux session
```

**Operator recipe — diagnose "I stopped it but it's still running"**:
```
sudo python3 src/launcher_tui/main.py
# NomadNet Client > Status — look for `Unit: ACTIVE / enabled`
# and `Restarts: N` under --- Service State ---.
# If Restarts > 0, Logs > Health snapshot shows journal + tmux pane.
```

**Prevention**:
- **Rule**: Never `pkill` NomadNet when `systemctl --user is-active
  nomadnet` returns `active`. `_stop_nomadnet()` enforces this at the
  TUI layer; CLI operators should use `systemctl --user stop
  nomadnet` for the service and reserve `pkill` for the Interactive
  Client (not service-managed).
- **Rule**: future code that needs to know "is NomadNet running" must
  call `_nomadnet_service_state()` before falling back to
  `find_competing_clients()`. The fallback is only valid when
  `unit_installed` is `False`.
- The `NomadNetServiceOpsMixin._user_systemctl_argv()` helper is the
  single choke point for root→user-scope bridging; don't build
  `['sudo', '-u', ..., 'systemctl', '--user', ...]` at call sites.
- File-size compliance: `nomadnet.py` sits at ~1,447 lines after the
  refactor; the new service_ops / config_ops mixins keep the main
  file under the 1,500-line cap. Future new functionality should
  land in the appropriate mixin, not in `nomadnet.py`.


---

## Issue #46: Fleet-wide RNS config alignment (2026-04-25)

**Background**: Issue #41 introduced `rpc_key` pinning to fix
AuthenticationError between rnsd and clients on a single box. This
session's fleet audit (`scripts/rns_alignment.py audit --fleet`)
showed only 1 of 5 boxes was even close to canonical: rnsd running
from `/root/.reticulum/`, NomadNet pointing at empty
`/etc/reticulum/`, MeshForge clients writing root-owned
`/tmp/meshforge_rns_client/config` with no rpc_key. fleet-host-1 had been in
a NomadNet 392-restart loop for five days. Issue #41 only ratchets
when configs already share a directory — the fleet-wide reality was
worse than that.

**Symptom shape**:
- NomadNet user-unit fails to stay up; "Restarts: N" climbs into the
  hundreds with no actionable signal in the journal.
- `/home/<user>/.cache/meshforge/logs/tui_errors.log`:
  `multiprocessing.context.AuthenticationError: digest sent was rejected`.
- TUI Status panel: "RPC auth failure (identity mismatch)".

**Canonical layout (enforced by tooling)**:
- `rnsd.service` ExecStart drop-in: `--config /etc/reticulum`.
- `/etc/reticulum/config`: exists, root:root, contains
  `rpc_key = <64-hex>` pinned.
- NomadNet user-unit: `--rnsconfig /etc/reticulum`.
- MeshForge clients: `/tmp/meshforge_rns_client/config` inherits
  `rpc_key` + `instance_name` via the Issue #41 helpers.
- `/home/<user>/.reticulum/` (if present): user-owned, NOT root.

**Tools shipped**:
- `src/utils/rns_alignment.py` — `probe_local()` snapshots a host
  without carrying the rpc_key VALUE in any dataclass (only `bool`
  presence — safe to log). `analyze_drift()` renders human reasons.
  `plan_normalize()` is idempotent: aligned host → empty plan.
- `scripts/rns_alignment.py` CLI:
  `probe | audit | audit --fleet | normalize [--dry-run|--yes]`.
- TUI: NomadNet → Service Control → Repair RNS alignment.
- `nomadnet_wrapper.py` v7: **refuses-loud** on AuthenticationError
  (exit 87 + repair instructions to stderr). Previously swallowed
  it and booted NomadNet with empty stats — hiding the bug for days.
- `templates/systemd/nomadnet-user.service`: `StartLimitBurst=5`,
  `StartLimitIntervalSec=300`. After 5 wrapper-87 exits in 5 min the
  unit parks in failed state — no more 392-restart silent loops.

**Operator recipe**:
```bash
python3 /opt/meshforge/scripts/rns_alignment.py audit --fleet
ssh <box> "sudo python3 /opt/meshforge/scripts/rns_alignment.py normalize"
```
Or via TUI on the box: `NomadNet → Service Control → Repair RNS alignment`.

**Prevention**:
- Audit catches drift before NomadNet enters a restart-loop.
- Wrapper exit-87 surfaces the actual failure in the journal.
- `StartLimitBurst` caps journal noise; failure becomes loud.
- `TestPlanNormalize::test_idempotent_on_already_normalized` guards
  the planner so re-running normalize on an aligned host is a no-op.
- Library/CLI never carry the rpc_key value in any field or log line;
  verified by `TestRpcKeyScriptDoesNotLeakViaDescription`.

**Sister concern — install-method coherence (2026-04-25)**: alignment
fixes rnsd/NomadNet identity + rpc_key match, but a parallel failure
mode is install-method drift on the NomadNet client itself (pip-user
vs pipx vs apt). Non-pipx installs put the binary where
`_get_wrapper_command()` cannot derive the venv python, so the unit
silently fell back to bare `nomadnet` — bypassing the refuse-loud
wrapper entirely and reproducing the exact "392 silent restarts"
mode that alignment was meant to kill.

Fix: `scripts/install_nomadnet.sh` is the canonical, idempotent,
pipx-first installer (modes: default | --check | --refresh |
--reinstall [--wipe-identity]). Exposed in TUI as
`NomadNet > Service Control > Reinstall NomadNet (idempotent)`.
Preconditions on `rns_alignment.py audit` returning OK; refreshes
the wrapper from `templates/python/nomadnet_wrapper.py` (now the
single source of truth shared with `_create_nomadnet_wrapper` —
wrapper bumped to v8); renders the user unit by substituting
`__NOMADNET_EXEC__` with `<venv-python> <wrapper> --rnsconfig
/etc/reticulum`; then `loginctl enable-linger` + `systemctl --user
enable --now nomadnet`. Re-running on a canonical install is a no-op.

Wrapper-bypass closure: `_get_wrapper_command` now returns `None`
when the pipx venv python isn't found, and every callsite refuses-
loud with a msgbox steering to the canonical installer. The pre-
fix silent fallback to `[nn_path]` is gone. `_find_nomadnet_binary`
similarly refuses anything that isn't `~/.local/bin/nomadnet` with
an adjacent venv `python3`. Tests in
`TestCanonicalNomadnetInstaller`.

**Operator recipe — install-method drift on a fleet box**:
```bash
ssh <box> "bash /opt/meshforge/scripts/install_nomadnet.sh --check"
# RESULT: drifted → run the installer (idempotent):
ssh <box> "bash /opt/meshforge/scripts/install_nomadnet.sh"
```
Or via TUI on the box: NomadNet → Service Control → Reinstall NomadNet.


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
