# Development

Working on MeshForge: tests, gates, and the contribution path.

## Development

<a id="branch-strategy"></a>

### Branch Strategy

| Branch | Version | Focus |
|--------|---------|-------|
| `main` | `0.6.2-beta` | Meshtastic-primary NOC, production use |

**Sister project:** [MeshAnchor](https://github.com/Nursedude/meshanchor) is the
MeshCore-primary NOC — extracted from this repo on 2026-04-01.

**MeshForge main** is the Meshtastic-primary NOC:
- Gateway config schema validation + MQTT message queue persistence
- Meshtastic API 2.7.x upgrade
- TUI security hardening, timeout module, circuit breaker
- MeshCore available as optional handler (gateway to MeshCore networks)

**Which should you run?**
- **MeshForge** for Meshtastic + RNS operation
- **[MeshAnchor](https://github.com/Nursedude/meshanchor)** if MeshCore is your primary radio

Development lands directly on `main` (solo-dev workflow); dependency bumps
arrive as Dependabot PRs that auto-merge once CI is green. The old alpha
branch is archived as tag `alpha-archived`.

```bash
git clone https://github.com/Nursedude/meshforge.git
cd meshforge
sudo bash scripts/install_noc.sh
sudo python3 src/launcher.py --verify-install  # Confirm everything works
```

For upgrade paths see [Upgrading MeshForge](#upgrading-meshforge).

### Gateway Deployments

Canonical deployment guide: **[docs/GATEWAY_DEPLOYMENT.md](GATEWAY_DEPLOYMENT.md)** — architecture diagram, prereqs, per-box recipe, fleet truth table, known gotchas.

Active gateway fleet (as of 2026-05-20): operator's 5-box LAN cluster + 1 cloud peer — all on the composable-bridges model below.

#### Deploy a new gateway box

Two idempotent scripts. Re-running either is safe.

```bash
# 1. Config side: install deps, derive LXMF hash, enable meshforge channel
#    MQTT flags, verify rpc_key pinning, render gateway.json from template.
sudo scripts/configure_gateway.sh            # uses $SUDO_USER
sudo DRY_RUN=1 scripts/configure_gateway.sh  # preview only, no writes

# 2. Systemd side: render + install + enable meshforge-gateway.service
sudo scripts/install_gateway_service.sh

# Watch first startup
sudo journalctl -u meshforge-gateway -f
```

#### Composable bridges — configure what you actually need

As of commit `4bae714` (2026-04-24), **`bridge_mode` is an advisory display label**, not a selector. Each bridge starts based on its own `enabled` flag, and any combination runs concurrently in one service:

| Flag in `gateway.json` | Default | What it runs | Typical use |
|------------------------|---------|--------------|-------------|
| `rns_bridge_enabled` | `true` | `RNSMeshtasticBridge` — Meshtastic ↔ RNS/LXMF | The common case. Bridges mesh messages to NomadNet. |
| `mesh_bridge.enabled` | `false` | `MeshtasticPresetBridge` — cross-preset mesh ↔ mesh | Dual-radio boxes bridging e.g. LongFast HAT with a ShortTurbo USB device via `connection_type: "serial"` |
| `rns_transport.enabled` | `false` | `RNSMeshtasticTransport` — RNS-over-Meshtastic transport | Specialist: use Meshtastic's LoRa as the physical layer for RNS packets |

A dual-radio gateway that also bridges to NomadNet sets both `rns_bridge_enabled: true` and `mesh_bridge.enabled: true` — two bridges run side-by-side with independent queues, threads, and connections. A pure cross-preset testbed with no NomadNet sets `rns_bridge_enabled: false` and `mesh_bridge.enabled: true`.

#### Refusal on inconsistency (no silent fallback)

The service runs `validate_bridge_conflicts()` before constructing any bridge. If the config is inconsistent it prints `CONFIG ERRORS` and exits with code 2 — the gateway will **not** silently "auto-correct to a different mode" the way the old single-enum code did. Current refusal conditions:

- No bridges enabled at all
- `mesh_bridge` primary + secondary both point to the same serial device
- Both `mesh_bridge.enabled` and `rns_transport.enabled` true (they both claim the Meshtastic radio's data path)
- `mesh_bridge.secondary.connection_type="serial"` with a `serial_device` path that does not exist on the box

On refusal, fix `gateway.json` and restart the service. Errors point at the exact key to change.

#### Legacy config migration

Gateways deployed before 2026-04-24 that used the single-enum pattern (e.g. `bridge_mode="mesh_bridge"` with `mesh_bridge.enabled=false`) are auto-migrated in-place at startup with a `MIGRATION:` journal warning — the section gets enabled and the gateway proceeds. Set the `enabled` flag explicitly in `gateway.json` to silence the warning.

#### Templates

- `templates/gateway/gateway.json.template` — rendered by `configure_gateway.sh` with per-box placeholders
- `templates/gateway-pair/` — dual-gateway preset bridging reference material
- `templates/meshforge-presets/` — per-node meshtasticd presets (channel PSK, etc.)
- `contrib/systemd/meshforge-gateway.service.in` — systemd unit template (rendered by `install_gateway_service.sh`)

---

## Code Health

### Test Coverage

**~9,300 tests** across <!--STAT:testfiles-->337<!--/STAT--> test files. Selected high-volume files
(per-file counts are a 2026-07 snapshot — run `python3 -m pytest tests/<file> --co -q` for the live number):

| Test File | Tests | Covers |
|-----------|-------|--------|
| `test_rns_bridge.py` | ~407 | Core bridge: routing, circuit breaker, message processing, callbacks, lifecycle |
| `test_message_queue.py` | ~114 | Persistent SQLite queue, retry policy, dead letter, overflow shedding |
| `test_rf.py` | ~107 | RF calculations: haversine, FSPL, Fresnel, link budget, signal classification |
| `test_rns_transport.py` | ~97 | Packet fragmentation, reassembly, transport stats, connection management |
| `test_node_tracker.py` | ~97 | Unified node tracking, RNS + Meshtastic state management |
| `test_meshtastic_handler.py` | ~88 | Meshtastic connection, message handling, node tracking |
| `test_mqtt_robustness.py` | ~83 | MQTT reconnection, message loss recovery, broker failover |
| `test_status_bar.py` | ~76 | TUI status bar rendering, health state display |
| `test_meshtastic_protobuf.py` | ~74 | Protobuf HTTP client, device config, channel management |
| `test_commands.py` | ~64 | CLI command handlers, output parsing |
| `test_bridge_health.py` | ~57 | Gateway health monitoring, circuit breaker patterns |
| `test_rns_status_parser.py` | ~56 | RNS status output parsing, edge cases |
| `test_reconnect.py` | ~45 | Exponential backoff, jitter, slow start recovery, thread safety |
| `test_deployment_profiles.py` | ~35 | Deployment profile system (5 profiles: radio_maps, monitor, meshcore, gateway, full) |
| `test_startup_health.py` | ~20 | Startup health checks, service verification |
| `test_compliance.py` | ~13 | HAM compliance validation, encryption modes |

*Note: the suite was trimmed from ~4,000 to ~1,400 in v0.5.4 to focus on gateway-essential coverage, then grew back to ~9,300 as new features and the Issues #58–#80 reliability arc shipped with regression-pinning tests. The exact file count above is kept honest by `scripts/readme_stats.py` (enforced in CI via `tests/test_readme_stats.py`); the total is deliberately approximate because it depends on which optional deps are installed. All tests use mocked external services — field validation with real hardware is a separate QA track.*

```bash
python3 -m pytest tests/ -v            # Run all tests
python3 -m pytest tests/ -v -x         # Stop on first failure
python3 -m pytest tests/test_rns_bridge.py -v  # Gateway bridge tests only
```

### Auto-Review

Auto-review system scans the `src/` tree (~470 Python files) for security, reliability, and performance issues:

```bash
cd src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
print(f'Issues: {report.total_issues}, Files scanned: {report.total_files_scanned}')
"
```

**Tracked issues** (see `.claude/foundations/persistent_issues.md`):

| Rule | Description | Status |
|------|-------------|--------|
| MF001 | `Path.home()` → use `get_real_user_home()` for sudo safety | Active monitoring |
| MF002 | No `shell=True` in subprocess calls | Active monitoring |
| MF003 | No bare `except:` — specify exception types | Active monitoring |
| MF004 | All subprocess calls need `timeout` parameter | Active monitoring |
| MF005 | *(Removed)* — was GLib.idle_add for GTK4 thread safety (GTK4 removed in v0.5.x) | Retired |
| MF006 | No `safe_import` for first-party modules — use direct imports | Active monitoring |
| MF007 | No direct `TCPInterface()` — use connection manager | Active monitoring |
| MF008 | No raw `systemctl` for service state — use `service_check` | Active monitoring |
| MF009 | `RNS.Reticulum()` must include `configdir=` parameter | Active monitoring |
| MF010 | No `time.sleep()` in daemon loops — use `_stop_event.wait()` | Active monitoring |
| MF013 | No raw `sqlite3.connect()` — use `connect_tuned()` + `DBSpec` inventory | Active monitoring |
| MF014/MF015 | No operator-specific values in source; no LAN IPs in published docs | Active monitoring |
| MF017 | systemd `ReadWritePaths=` must cover every dir the service writes | Active monitoring |
| MF019 | `RNS.Reticulum()` only via the `open_reticulum()` chokepoint | Active monitoring |
| MF021 | mini-dudeai engine stays observation-only (no subprocess/systemctl) | Active monitoring |

**Reliability patterns** (inspired by [Raspberry Pi systemd best practices](https://www.thedigitalpictureframe.com/ultimate-guide-systemd-autostart-scripts-raspberry-pi/)):
- Services use `Restart=on-failure` with `RestartSec=5` for auto-recovery
- Crash-loop protection: `StartLimitBurst=5` / `StartLimitIntervalSec=60` on rnsd
- Startup ordering: meshforge.service `After=rnsd.service` ensures identity exists
- Advisory pre-flight `check_service()` on all TCPInterface and MQTT connections (9 files hardened)
- RNS shared instance detection via abstract Unix domain socket (`@rns/default`), with TCP/UDP fallback
- RNS repair wizard pre-flight: validates `share_instance = Yes`, detects config drift, checks NomadNet conflicts
- RNS identity pre-flight: startup checks verify `~/.reticulum/storage/identities` exists
- Shared connection manager prevents TCP:4403 client contention
- Exponential backoff reconnection (1s → 2s → 4s → ... → 30s max)
- Canonical logging via `setup_logging()` — all 9 `basicConfig()` calls consolidated
- Handler registry pattern: all <!--STAT:handlers-->103<!--/STAT--> TUI handler modules use registry dispatch (mixin inheritance fully replaced)
- Connection failure logs upgraded to WARNING level for visibility (cleanup errors stay DEBUG)

---

## Contributing

```bash
python3 -m pytest tests/ -v      # Run tests
python3 scripts/lint.py --all    # Security linter
```

**Code rules:** No `shell=True`, no bare `except:`, use `get_real_user_home()` not `Path.home()`.

See [CLAUDE.md](../CLAUDE.md) for details.

---

