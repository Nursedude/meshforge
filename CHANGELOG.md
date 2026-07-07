# Changelog

All notable changes to MeshForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Entries 0.5.5-beta through 0.6.2-beta below were reconstructed 2026-07-07 from
> the `VERSION_HISTORY` list in `src/__version__.py` (the change SSOT) after an
> audit found this file frozen at 0.5.4-beta. `src/__version__.py` remains the
> authoritative changelog; keep both in sync (guarded by
> `scripts/version_consistency_check.py`).

## [0.6.2-beta] - 2026-06-12

### Added
- **AREDN**: Meshtastic↔AREDN bridge field-proven via Raven (the AREDN core team's ucode bridge) on the AREDN-site collector — both directions verified over real RF, then a 24h soak clean (0 restarts, flat RSS). Raven's Meshtastic leg rides UDP-over-LAN multicast (224.0.0.69:4403), never the PhoneAPI TCP stream, so Issue #17 contention is structurally absent. Pilot log + build recipe: `.claude/plans/aredn_raven_moc5_pilot.md`.

### Changed
- **Fleet**: bridge box `device.role` flipped CLIENT_MUTE→CLIENT so the reverse leg reaches the RF mesh; Raven soak watch wired into the cron-verdict regime (Issue #78).

## [0.6.1-beta] - 2026-06-04

### Added
- **Fleet**: first *routed* RNS leaf onboarded — an AREDN-sited collector behind a MikroTik hAP joins via `TCPClientInterface`, federates ~2.7k mesh-side nodes.
- **Watchdog**: `probe_channel_feed_dark` — the silence canary; hours of json-uplink silence while the pipeline is alive fires `channel_feed_dark` (the tell for a missed PSK re-key, a deaf radio, or a dead uplink). Born from the 2026-06-04 dark-feed incident.

### Fixed
- **Lab**: `lxmf_echo` routed-leaf ACK fix — bounded `RNS.Transport.request_path()` on `Identity.recall()` miss.
- **Federation**: `peer_name` plumbing completed for every peer (Issue #54 close-out).

### Changed
- meshtasticd 2.7.24 rolled fleet-wide (24h soak, 0 crash signatures; 2.7.15 double-free class confirmed fixed).

## [0.6.0-beta] - 2026-05-02

### Added
- **Gateway**: dual-gateway cross-preset bridging proof-of-concept — two active gateways (moc LongFast + moc3 SHORT_TURBO), each with a distinct `rns.gateway_name` + per-box `meshtastic.gateway_node_id` self-echo filter.
- **MeshChatX**: side-by-side LXMF web client integrated as an opt-in NomadNet sibling — idempotent installer, refuse-loud on `rpc_key` drift, `Type=simple` systemd-user unit on 127.0.0.1:8000 (+51 tests).

### Changed
- **Gateway**: `scripts/configure_gateway.sh` canonicalizes two device-level meshtasticd MQTT prereqs — `mqtt.json_enabled = true` and `mqtt.address = localhost` (found when a clean deploy bridged 0/0 for half an hour).
- `scripts/fleet_sync.sh` now restarts `meshforge-map.service` alongside `meshforge-gateway` + `meshforge-maps` (Issue #53 stale-daemon prevention).

## [0.5.7-beta] - 2026-04-24

### Changed
- **Gateway**: composable bridges — `bridge_mode` is now an advisory display label, not a selector; each bridge is gated by its own `.enabled` flag (`rns_bridge_enabled`, `mesh_bridge.enabled`, `rns_transport.enabled`) and any combination runs concurrently.
- **Gateway**: refusal-on-inconsistency contract — `validate_bridge_conflicts()` exits code 2 with a CONFIG ERRORS block instead of silently auto-correcting.

### Added
- **Gateway**: `connection_type="serial"` — dual-radio gateways bridge a HAT primary with a USB-attached Meshtastic secondary.
- **Gateway**: `scripts/configure_gateway.sh` idempotent deployment helper (DRY_RUN=1 preview).
- **Docs**: `docs/GATEWAY_DEPLOYMENT.md` — canonical deployment guide (architecture, prereqs, composable bridges, RNode variant, fleet truth table, gotchas).

## [0.5.6-beta] - 2026-04-21

### Fixed
- **Gateway**: RNS→Mesh (R→M) path unblocked end-to-end — bytes content decoded, HTTP TX route forced (Issue #40).
- **Gateway**: MQTT bridge topic shape now matches meshtasticd 2.7.x publishes (region-less + region-ful both subscribed; `region=""` default) (Issue #34).
- **Gateway**: `rpc_key` propagation into gateway client configs closes `AuthenticationError` on identity-split boxes (Issue #41).
- **NomadNet**: `AuthenticationError` wrapper so rnsd/NomadNet `rpc_key` drift no longer crashes the TUI at startup (Issue #37).

### Added
- **Gateway**: identifying, two-way-directable bridge — originating mesh node in the LXMF title/fields; `@!id`/`@shortname` from RNS parses to a directed Meshtastic DM (Issue #39).
- **NomadNet**: single-identity tmux-detached systemd-user pattern for fleet rollout (Issue #38).
- **Tools**: `scripts/validate_rns_to_mesh.py` — shell-runnable LXMF sender for R→M acceptance without the NomadNet TUI.

## [0.5.5-beta] - 2026-03-09

### Removed
- MeshChat handler (2,683 lines), plugin, deployment profile (6→5 profiles), and 114 tests — upstream unmaintained (11+ bugs).

### Changed
- NomadNet is MeshForge's supported LXMF messaging client; core mission refocused on gateway/bridge, maps, monitoring, and RF tools.

## [0.5.4-beta] - 2026-02-11

### Changed
- Gateway bridge rewritten to use MQTT transport (zero interference with web client)
- MQTT bridge is now default mode (web client on :9443 works uninterrupted)
- Existing configs preserved; TCP bridge still available as legacy option

### Added
- MQTTBridgeHandler — subscribes to meshtasticd MQTT, sends via CLI
- Deployment templates: mosquitto.conf, rnsd-user.service, setup script
- MQTT bridge settings menu in TUI with setup guide
- template_mqtt_bridge() configuration template (recommended)

### Fixed
- Gateway no longer holds persistent TCP:4403 connection
- Web client no longer blocked when gateway bridge is running

### Deprecated
- meshtastic_api_proxy.py (source of web client interference)

## [0.5.3-beta] - 2026-02-08

### Added
- 136 unit tests for rns_bridge.py (core bridge logic)
- 97 unit tests for rns_transport.py (packet fragmentation, reassembly, callbacks)
- 45 unit tests for reconnect.py (exponential backoff, jitter, slow start recovery)
- 72 unit tests for message_queue.py (persistent queue, retry policy, circuit breaker)

## [0.5.2-beta] - 2026-02-08

### Added
- EAS Alerts accessible from Emergency Mode and Dashboard
- Favorites menu in Mesh Networks (BaseUI 2.7+ node favorites)

### Changed
- 16 mixin dispatch loops converted to _safe_call pattern
- All top-level TUI menus now catch exceptions gracefully
- Quick Actions, RF Tools, Site Planner, AI, Channel Config protected
- Traffic Inspector, Metrics, Logs, Network Tools, AREDN protected
- Hardware, Backup, Updates, Settings, SDR, Config menus protected

### Fixed
- Gateway bridge mode auto-fix now persists corrected mode
- bridge_cli.py no longer restores stale mesh_bridge after auto-correction

## [0.5.1-beta] - 2026-02-06

### Added
- Full telemetry pipeline — sensor data through Prometheus, InfluxDB, Grafana
- Gateway auto-starts metrics server, MQTT connect with timeout
- Auto-fix RNS shared instance on 'no shared' error
- Meshtastic 2.7+ favorites management, PKI status, health metrics
- Wireshark-grade RNS packet sniffer
- MQTT auto-start and local broker multi-consumer architecture
- MQTT to WebSocket bridge for web UI access
- Startup warning for root without SUDO_USER

### Fixed
- Gateway bridge connects to RNS as shared instance client
- MQTT subscriber hang on exit (4 root causes)
- Bind metrics server to localhost only (security)
- Path.home() violations in 6 files (MF001 audit)
- shell=True in updates_mixin.py (MF002)
- License mismatch in TUI about screen (MIT to GPL-3.0)
- _frequency_calculator() undefined method replaced with _calc_frequency_slot()

### Changed
- TUI Maps & Viz menu routes directly to map functions
- Removed dead _handle_choice() and _network_tools_submenu()
- Extracted rns_sniffer_mixin, metrics modules, diagnostic checks

## [0.5.0-beta] - 2026-02-01

### Changed
- Promoted to beta — TUI stable across 6+ fresh installs

### Added
- RNS config path auto-detection with --rnsconfig fallback
- Interactive /etc/reticulum permission fix menu

### Fixed
- NomadNet /etc/reticulum permission issues (auto-detect + bypass)
- rnsd user/root identity mismatch detection and repair
- AREDN/Folium startup error suppression
- rnsd systemd user override for consistent RPC auth

### Improved
- NomadNet error diagnostics with specific fix suggestions
- User directory ownership auto-repair for sudo scenarios

## [0.4.8-alpha] - 2026-01-30

### Added
- RF Calculator with Hawaii location presets (Big Island, Oahu, Maui)
- Leaflet map in RF LOS calculator with path visualization
- Elevation profile chart with Fresnel zone clearance
- Node count shows Total Seen / Mapped / No GPS breakdown

### Fixed
- TUI crash when launching map server (stdout/stderr suppression)
- SQLite 'readonly database' errors (permission fix documented)
- Browser caching old HTML files (no-cache headers added)
- Page scroll on RF Calculator for smaller screens

### Improved
- HTTP request logging silenced to prevent TUI corruption
- Static HTML files served with cache-control headers

## [0.4.7-beta] - 2026-01-17

### Added
- UDP port 37428 check for reliable rnsd detection
- 13 regression tests to prevent status drift across UIs
- Pre-commit hooks (security lint, critical tests, type checking)
- API dependencies documentation
- Auto-review allowlist for known false positives
- CODEOWNERS file for critical file review requirements
- mypy.ini and pyproject.toml for gradual type checking
- Predictive analytics engine for proactive network health monitoring
- Message lifecycle state machine (CREATED, QUEUED, SENT, DELIVERED, ACK)
- Message tracing API for debugging delivery issues
- 45 new tests for predictive analytics and message lifecycle

### Changed
- All UIs (GTK, TUI, CLI) now use single check_service() for status
- Exception handlers now log instead of silently swallowing
- service_check.py exports public API via __all__
- commands/service.py correctly handles UDP vs TCP ports

### Fixed
- Status consistency — eliminated conflicting rnsd status displays

## [0.4.6-beta] - 2026-01-17

### Added
- AI Tools integration in TUI (intelligent diagnostics, knowledge base, Claude assistant)
- Coverage map generation with Folium
- Example configuration files in `examples/`
- Visual documentation guide (`docs/VISUAL_GUIDE.md`)
- GitHub Actions CI pipeline
- Pre-commit hooks configuration
- Systemd service file for running as a service
- Docker container support
- Man page documentation

### Changed
- README redesigned with clearer problem/solution structure
- Simplified ASCII diagrams for cross-platform rendering

### Fixed
- Added REGION_ENUM_MAP for proper region integer-to-string conversion

## [0.4.5-beta] - 2026-01-16

### Added
- Intelligent diagnostics system (standalone + PRO mode)
- Knowledge base for mesh networking concepts
- Claude assistant integration for natural language queries
- Coverage map generator using Folium
- Auto-review system for code quality

### Changed
- Refactored launcher_tui/main.py using mixin pattern
- Refactored hamclock.py using mixin pattern
- Improved GTK panel organization

## [0.4.4-beta] - 2026-01-15

### Added
- Full radio configuration panel in GTK
- Channel configuration with 8-channel support
- Frequency slot calculator (djb2 hash)
- Gateway templates (Standard, Turbo, MtnMesh)

### Fixed
- WebKit root sandbox issue (added browser fallback)
- Path.home() issues in multiple files

## [0.4.3-beta] - 2026-01-14

### Added
- MQTT dashboard panel
- Node tracker with unified view
- Position sharing between networks

### Changed
- Improved service management UI
- Better error messages for service failures

## [0.4.2-beta] - 2026-01-13

### Added
- RF tools (FSPL, Fresnel zone, link budget calculator)
- Site planner with range estimation
- Hardware detection improvements

### Fixed
- SPI HAT detection on Raspberry Pi 5

## [0.4.1-beta] - 2026-01-12

### Added
- TUI interface (raspi-config style)
- Web monitor dashboard
- Basic gateway bridge functionality

### Changed
- Reorganized project structure
- Moved commands to dedicated layer

## [0.4.0-beta] - 2026-01-10

### Added
- Initial public release
- GTK4 desktop interface
- Meshtastic-RNS gateway bridge
- Service management (meshtasticd)
- Hardware configuration

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| 0.5.4-beta | 2026-02-11 | MQTT bridge architecture, zero interference |
| 0.5.3-beta | 2026-02-08 | 350 unit tests for core gateway |
| 0.5.2-beta | 2026-02-08 | EAS alerts, _safe_call reliability |
| 0.5.1-beta | 2026-02-06 | Telemetry pipeline, RNS sniffer, security fixes |
| 0.5.0-beta | 2026-02-01 | Beta milestone, NomadNet fixes |
| 0.4.8-alpha | 2026-01-30 | RF Calculator, map improvements |
| 0.4.7-beta | 2026-01-17 | Service consistency, predictive analytics |
| 0.4.6-beta | 2026-01-17 | AI tools in TUI, Docker, CI/CD |
| 0.4.5-beta | 2026-01-16 | AI diagnostics system |
| 0.4.4-beta | 2026-01-15 | Radio configuration |
| 0.4.3-beta | 2026-01-14 | MQTT dashboard |
| 0.4.2-beta | 2026-01-13 | RF tools |
| 0.4.1-beta | 2026-01-12 | TUI interface |
| 0.4.0-beta | 2026-01-10 | Initial release |
