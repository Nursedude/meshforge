# MeshForge Persistent Issues & Resolution Patterns

> **Purpose**: Document recurring issues and their proper fixes to prevent regression.
> **Last audited**: 2026-03-13 — Trimmed to <40k chars; resolved issues archived.

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
| #25, #26, #28 | rnsd ratchets, ReticulumPaths copies, API proxy | — |
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

## Issue #4: Silent Debug-Level Logging

Use appropriate log levels — don't hide errors at DEBUG:
- **ERROR**: Something broke | **WARNING**: Unusual | **INFO**: User-visible ops | **DEBUG**: Dev internals

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

## Issue #16: Gateway Message Routing Reliability

Delivery is **best-effort** — inherent to mesh networking. Message queue persists to SQLite for retry.
Always show "Sent (delivery not guaranteed)" or "Queued" status.

Files: `commands/messaging.py`, `gateway/rns_bridge.py`, `gateway/message_queue.py`

---

## Issue #17: Meshtastic Connection Contention (Single-Client TCP)

**meshtasticd only supports ONE TCP client at a time.** Multiple components creating
independent connections causes thrashing every 1-2 seconds.

### Fix: Shared Connection Manager
All components share ONE persistent connection via `get_connection_manager()`.
Short-lived reads use `MeshtasticConnection` context manager.
Long-lived connections acquire `MESHTASTIC_CONNECTION_LOCK`.

### HTTP fromradio Contention Fix
The `/api/v1/fromradio` endpoint is also single-consumer. `send_text_direct()` POSTs
directly to `/api/v1/toradio` without ever reading fromradio. All TX paths use this.

### Prevention
- **NEVER** create `TCPInterface()` directly — use connection manager
- **NEVER** read `/api/v1/fromradio` in TX paths — use `send_text_direct()`
- Reserve session-based `connect()` + `start_polling()` for config reads only

---

## Issue #18: Auto-Reconnect on Connection Drop

Gateway uses health monitoring + exponential backoff (1s → 2s → 4s → ... → 30s max)
in `rns_bridge.py`. All persistent connections should have health monitoring.
Release connection manager resources on disconnect.

---

## Issue #19: RNS Node Discovery from path_table

Use `RNS.Transport.path_table` (not just `destinations`) for complete routing info.
**path_table may be empty immediately after connect** — use delayed checks (5s) and
periodic re-checks (30s).

Location: `src/gateway/node_tracker.py`

---

## Issue #20: Service Detection & Status Display — ALL DONE

All 3 components resolved:

1. **Service Detection**: Simplified to systemctl-only for systemd services (SSOT)
2. **Status Display**: Separates "service state" from "detection capability" —
   never shows "FAILED" when service is running
3. **RX Messages**: `event_bus.py` → `websocket_server.py` → TUI live feed

### RNS Socket Detection
RNS uses abstract Unix domain sockets (`\0rns/{instance_name}`), not UDP port 37428.
Use `check_rns_shared_instance()` (3-tier: Unix socket → TCP → UDP fallback).

### Prevention
- UI must always distinguish "service state" from "detection capability"
- Use `check_rns_shared_instance()` for all rnsd checks (never raw UDP)

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

## Issue #24: Python Environment Mismatch (rnsd + meshtastic module)

rnsd's `Meshtastic_Interface.py` plugin requires the `meshtastic` Python module.
pipx isolation, different Python versions, or user vs system site-packages can
make the module invisible to rnsd.

**Fix**: `sudo pip3 install --break-system-packages --ignore-installed meshtastic`
or install to the same Python that rnsd uses:
`head -1 $(which rnsd)` then use that interpreter's pip.

**Diagnose**: `sudo python3 -c "import meshtastic; print(meshtastic.__version__)"`

---

## Issue #27: rnsd is OPTIONAL

MeshForge supports two independent transports:
- **MQTT** (mosquitto) — Meshtastic native. Used for preset bridging, monitoring.
- **RNS** (rnsd) — Reticulum. Used for LXMF messaging, cross-protocol bridging.

**Meshtastic preset bridging** (LF ↔ ST) needs only mosquitto — both radios MQTT
uplink/downlink to the same broker with same channel/PSK. No gateway code needed.

**Full NOC** (Meshtastic + RNS) uses both transports. They coexist independently.

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

## Issue #30: NomadNet RPC ConnectionRefusedError (2026-03-11)

NomadNet crashes on startup when `get_interface_stats()` can't connect to rnsd's RPC socket.

**Root causes**: RNS version mismatch (pipx venv vs system rnsd), user mismatch
(root rnsd vs user NomadNet), rnsd still initializing, or stale state.

**Fix**: Pre-launch check in `_nomadnet_rns_checks.py` uses NomadNet's own Python
interpreter to test RPC (not system rnstatus). Detects version mismatches and
suggests `pipx upgrade nomadnet`. Auto-restarts rnsd if needed.

Post-failure diagnosis in `nomadnet.py:_diagnose_nomadnet_error` detects
`ConnectionRefusedError` / `Errno 111` patterns in NomadNet logfile.

---

## Issue #31: No Silent Persistent System Changes on Startup (2026-03-12)

**Rule**: NEVER make persistent system changes silently on startup.

MeshForge's `auto_lock_port()` was silently adding iptables REJECT rules on port 9443
every TUI launch, persisting after exit. This broke the Meshtastic web UI.

**Prohibited on startup**: iptables rules, cron jobs, udev rules, systemd unit mods,
config file overwrites (see also Issue #22).

MeshForge **observes and assists** — it does not take over infrastructure.
Explicit user actions (e.g., service_menu lock/unlock) are acceptable.

**Cleanup for affected users**: `sudo iptables -D INPUT -p tcp --dport 9443 ! -s 127.0.0.1 -j REJECT`

---

## Issue #32: NomadNet "Enabled but Disconnected" Interfaces (2026-03-13)

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

4. **Local HAT TX does NOT uplink to MQTT**. Only RX from other nodes is uplinked. To exercise
   the RX path without a second physical node: publish a simulated packet directly to MQTT:
   ```
   mosquitto_pub -h 127.0.0.1 -t 'msh/US/2/json/meshforge/!deadbeef' \
     -m '{"payload":{"text":"test"},"sender":"!deadbeef","type":"text","channel":2,"to":4294967295,"from":3735928559}'
   ```
   Gateway parses JSON, routes `bridge_to_rns`, delivers LXMF to `default_lxmf_destination`,
   NomadNet creates a conversation dir under `~/.nomadnetwork/storage/conversations/<gw_hash>`.

5. **traffic.log permission noise**. `monitoring/traffic_storage.py` tries to write to
   `~/.cache/meshforge/logs/traffic.log`; fails with `Errno 13` when the dir doesn't exist or
   is root-owned. Non-fatal, gateway still bridges — but noisy. Create dir at install time or
   handle `FileNotFoundError` by creating parents.

**Regression guards** (all 15 pass as of 2026-04-18):
- `tests/test_regression_guards.py` — TCP/MQTT/service-check/PathHome/shell/event-bus contracts
- `python3 scripts/lint.py --all` — MF001-010 clean

**Rollback**: `/etc/reticulum/config.bak.20260418-074417` preserves pre-change state.
