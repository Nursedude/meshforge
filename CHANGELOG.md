# Changelog

All notable changes to MeshForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
