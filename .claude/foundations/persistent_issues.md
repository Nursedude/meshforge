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
| GTK Issues (#2, #11, #13–#15) | GTK4 removed in v0.5.x | — |

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

## Issue #3: Services Not Started/Verified — MOSTLY RESOLVED

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

---

## Issue #6: Large Files — ALL UNDER THRESHOLD

Only `knowledge_content.py` (1,993 lines) exceeds 1,500 — acceptable as content file.
Monitor files approaching 1,400 lines. Split proactively at 1,000 lines when adding features.

Top files: `meshtastic_protobuf_client.py` (1,433), `service_check.py` (1,410),
`map_http_handler.py` (1,404), `prometheus_exporter.py` (1,399).

---

## Issue #12: RNS "Address Already in Use"

**Rule**: Never call `RNS.Reticulum()` without `configdir=` when rnsd is running.

MeshForge creates a client-only config in `/tmp/meshforge_rns_client/` with
`share_instance = Yes` and no interface definitions, allowing connection to
rnsd without binding ports.

Location: `src/gateway/node_tracker.py` — `_init_rns_main_thread()`

---

## Issue #21: Meshtastic CLI Preset Bug (Upstream)

**Not a MeshForge bug.** The Python meshtastic CLI doesn't always apply modem preset
changes correctly. Always verify in browser at `http://localhost:9443` after CLI changes.
Consider direct meshtasticd API calls instead of CLI.

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

### Layer 2: Regression Guard Tests (`tests/test_regression_guards.py`)
- `TestTCPConnectionContract` — No new direct TCPInterface
- `TestFromradioContract` — TX uses `send_text_direct()`
- `TestServiceCheckContract` — Service state via `check_service()` only
- `TestPathHomeContract` — No `Path.home()` violations
- `TestNoShellTrue` — No `shell=True` in subprocess
- `TestKnownServicesConsistency` — KNOWN_SERVICES stays correct

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
   TX contract. This is the same path used by `mesh_bridge.py`,
   `radio_failover.py`, `map_http_handler.py`, and `commands/meshtastic.py`.
   It avoids the meshtasticd "channel-literally-named-mqtt" requirement, the
   `from: 0` issue, the topic region-shape ambiguity, and any cross-fleet
   channel reconfig. No device-side changes required.

3. **`publish_to_mqtt()` is now dead code on the live TX path** and has been
   left in place for potential future resurrection (a true MQTT-only gateway
   with no `:9443` / :443 HTTP access). If resurrected, it would also need:
   topic change to `msh/{REGION}/2/json/mqtt/{gw_node_id}`, `from` filled with
   the gateway's real numeric node id, and removal of `sender`/`type` keys
   (not part of the downlink JSON spec).

**Tests added** (`tests/test_rns_bridge.py`):

- `test_bytes_content_is_decoded` — plain bytes body bridges cleanly.
- `test_bytes_content_with_at_prefix` — decode happens before `@address`
  parsing so directed downlink works with bytes.
- `test_invalid_utf8_uses_replacement` — non-UTF-8 bytes don't crash.
- `test_mqtt_bridge_mode_enqueues_to_meshtastic` — the MQTT-bridge-mode code
  path enqueues with `destination="meshtastic"`, not `"mqtt"`.
- `test_bytes_content_serializes_as_str` — retry-persist payload is JSON-safe.

**Field-validation note — Issue #37 blocks full end-to-end on fleet-host-3.** The
gateway's Reticulum instance (configdir `/tmp/meshforge_rns_client/`) has a
different identity than rnsd (configdir `/etc/reticulum/`), so their RPC keys
(derived from identity private bytes) diverge. When rnsd forwards an incoming
link packet to the gateway, the gateway's `RNS.Link.receive()` calls
`__update_phy_stats()` → `get_packet_rssi()` → RPC-to-rnsd, which fails with
`AuthenticationError: digest sent was rejected`. The exception propagates and
aborts inbound frame processing, so real LXMF deliveries never reach
`_on_lxmf_receive`. This is a broader manifestation of Issue #37 and was NOT
seen in Issue #38's testing because that direction was mesh→RNS (no inbound
link-packet RSSI query on the gateway's side of the ledger).

Proof-by-parts of the Issue #40 fix in the interim:
- Unit tests cover all three changed code paths (+5 new tests, all 175 pass).
- `send_text_direct()` direct-invoked against `localhost:443` (fleet-host-3's
  meshtasticd HTTPS port) returned `True` for both broadcast and directed DM
  (`dest=0xebfa1b11` — fleet-host-3's own HAT). The downstream path my fix routes to
  works end-to-end; only the RNS-inbound-link side is blocked by Issue #37.

**Prevention / future work**:
- **Unblock full end-to-end**: fix Issue #37 for the gateway. Either (a) make
  the gateway use `configdir=/etc/reticulum` so it shares rnsd's identity, or
  (b) pin `rpc_key` explicitly in both configs. NomadNet already uses
  pattern (a) via `--rnsconfig /etc/reticulum`.
- **Defensive catch for `AuthenticationError`** around the Issue #38 side
  observation is now doubly warranted — both in
  `gateway._rns_bridge_connection` (first_hop_timeout) AND in whatever owns
  the packet processing path. This is separate from the Issue #37 wrapper
  already shipped for NomadNet.
- **Never regress the LXMF bytes assumption**: a regression guard test that
  passes bytes content to `_process_rns_to_mesh` would catch any future
  recursion. Added as `test_bytes_content_is_decoded`.
- **Never regress away from the HTTP TX path**: `test_mqtt_bridge_mode_enqueues_to_meshtastic`
  guards against accidentally re-setting `destination="mqtt"`.


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



