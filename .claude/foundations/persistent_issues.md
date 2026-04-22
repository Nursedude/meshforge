# MeshForge Persistent Issues & Resolution Patterns

> **Purpose**: Document recurring issues and their proper fixes to prevent regression.
> **Last audited**: 2026-04-21 — Trimmed to <40k chars; resolved issues archived.

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
| #36 Meshtastic_Interface plugin | Keep rnsd's plugin disabled; MeshForge gateway owns text-bridging | Decision record |
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
future work, not part of this ship.


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
resilience.

**Prevention**: new installs pick up the consolidated pattern automatically because
`templates/systemd/nomadnet-user.service` is now the tmux-wrapped version and
`install_noc.sh` adds `tmux` to the apt install list. Do NOT manually re-introduce
a `--daemon` NomadNet alongside the tmux one.


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

