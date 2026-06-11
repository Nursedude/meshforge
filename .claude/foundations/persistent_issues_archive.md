# MeshForge Persistent Issues — Archive

> **Purpose**: Historical record of resolved issues.
> These were moved from `persistent_issues.md` to reduce file size.
> Last updated: 2026-05-18
>
> **Note**: GTK-specific issues (#2, #11, #13, #14, #15) were removed during
> the 2026-02-21 cleanup. GTK4 was removed in v0.5.x; TUI is the only interface.

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

## Issue #12: RNS "Address Already in Use" (archived 2026-05-20)

**Rule**: Never call `RNS.Reticulum()` without `configdir=` when rnsd is running.

MeshForge creates a client-only config in `/tmp/meshforge_rns_client/` with
`share_instance = Yes` and no interface definitions, allowing connection to
rnsd without binding ports.

Location: `src/gateway/node_tracker.py` — `_init_rns_main_thread()`

Enforced by lint MF009 and regression test `TestRNSReticulumNoConfigdir`.

---

## Issue #22: Never Overwrite meshtasticd's config.yaml (archived 2026-05-20)

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

Superseded in part by Issue #58's HAT-overlay sanitizer (forbidden-keys list) that
catches the inverse failure: a HAT template smuggling `Webserver:` overrides into
`config.d/`.

---

## Issue #23: Post-Install Verification (archived 2026-05-20)

**Rule**: Never mark install "complete" until verification passes.

`scripts/verify_post_install.sh` checks: meshtasticd binary, config.yaml validity,
Webserver section, port 9443, radio detection, config.d/, rnsd, udev rules.
Also available via `meshforge --verify-install`.

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
  "preflight_ok": true,
  "preflight_ts": 1779148881.1,
  "preflight_error": null,
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

# Archived 2026-06-07 (MF012 trim — body moved verbatim from persistent_issues.md)

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

**FIXED AT SOURCE (2026-05-30, fork `rns 1.2.5+mf.1`, `6fb9a9ec`)**: now an
in-library cure, not just a MeshForge guard. `LocalClientInterface.connect()`
brackets the shared-instance connect with `settimeout(5s, env
RNS_LOCAL_CONNECT_TIMEOUT)` → a wedged rnsd raises `socket.timeout` (reconnect
retries / falls back to standalone) instead of hanging in `unix_stream_connect`.
Fork test `tests/meshforge_local_connect.py` (4). The `rns_init.py` probe +
`os._exit` backstop STAY as defense-in-depth until soak-proven. See
[[project_rns_fork_shipped_2026_05_30]].




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

Allowlist deliberately narrow ("rnsd"/"reticulum") — any foreign
RNS-hosting daemon gets caught.

**Boot-race addendum (2026-06-06, `84a79ca` + MA `9065d973`)**: same
class, NO foreign daemon needed — lab echo started 4s before rnsd at
boot, found no listener, boot-claimed; rnsd joined as interface-less
client → ALL destinations no-route (federator + moc2, same day). Two
chokepoint fixes: (a) listener absent + rnsd ENABLED → bounded wait
(30s) for rnsd; lab path fails loud, `open_reticulum` returns None
even when `require_listener=False`; (b) `ss` truncates spaced
instance names at the first space, so the owner guard was silently
DEAD on such boxes — parser now matches the truncated token. New
`service_check.is_service_enabled()`.

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

**FIXED AT SOURCE (2026-05-30, fork `rns 1.2.5+mf.2`, `11227832`)**: the
watchdog above *detects* the wedge; the fork now *prevents* it. All 20
client-side RPC recvs route through `_rpc_recv()` → `poll(8s, env RNS_RPC_TIMEOUT)`
before `recv()`, so a wedged-but-accepting rnsd raises `TimeoutError` (EOF
fast-fails too) instead of blocking forever. Server `rpc_loop` recv untouched.
Fork test `tests/meshforge_rpc_timeout.py` (3); watchdog probe STAYS as
defense-in-depth. See [[project_rns_fork_shipped_2026_05_30]].

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


---

## Issue #73: meshforge-map fd-leak → [Errno 24] → :5000 wedge; + proactive fd probe (2026-05-31) — archived from persistent_issues 2026-06-09

**Symptom**: meshanchor-server `:5000` browser-spin; map `active`, rnsd healthy
(rnstatus OK, `@rns` owned by rnsd) — NOT the #68/#72 RNS class. Journal:
`[Errno 24] Too many open files`. The map process held **1024 fds (298 ESTAB to
`[::1]:1883`)** against the 1024 soft `RLIMIT_NOFILE` — `accept()` on `:5000`
couldn't get an fd, so even `/healthz` hung.

**Root cause**: shared `monitoring/mqtt_subscriber.py::_connect()` created a new
paho `Client` every call without tearing down the prior one; the reconnect loop
orphaned a client per cycle and `loop_start()` kept its socket alive. Fixed in
BOTH repos (MeshForge `5712b56`, MeshAnchor `6e1d2306`): `_connect()` calls
`_disconnect()` before re-creating; atexit registered once. Deployed fleet-wide
+ verified (mqtt socks back to 1, fds low). Tests `TestReconnectFdLeak` (2) in
each repo's `tests/test_mqtt_robustness.py`.

**Detection gap closed**: `http_local_unresponsive` (#61) caught the *symptom*
only AFTER `:5000` went dark, and pointed at thread stacks (wrong cause). New
**`probe_fd_exhaustion`** (signal class `fd_exhaustion`, issue_ref 73) is the
proactive companion: counts `/proc/<MainPID>/fd` vs the soft `Max open files`
from `/proc/<pid>/limits`, fires `degraded` ≥80% / `wedge` ≥95%, names the
fd-leak cause. Read-only, bounded, None on inactive/unreadable/unlimited. Wired
in `watchdog_runner` next to `probe_http_local` (gated on map expected-active).
Tests: 6 in `tests/test_watchdog_probes.py` (`test_fd_exhaustion_*`) + closed-
enum gate bumped.

**Operator recipe**:
```bash
curl -s http://127.0.0.1:5000/api/status | jq '.watchdog.signals[]? | select(.class=="fd_exhaustion")'
P=$(systemctl show meshforge-map.service -p MainPID --value)
sudo ls /proc/$P/fd | wc -l   # vs soft limit in /proc/$P/limits
```
Decision tell: `[Errno 24]`/climbing fds = fd leak (restart map, find leak);
`rnstatus` wedged = RNS class (restart rnsd).
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

