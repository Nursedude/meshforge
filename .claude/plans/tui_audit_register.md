# TUI Reliability Audit — Register & Rubric

> **This file is the persistent source of truth for the audit — not the model's
> context.** Every selection's verdict is written here as it is assessed. If the
> session compacts or is `/clear`ed, resume from this file: nothing is lost.
> Staged 2026-05-29 (warm-context scaffold); rows filled by the audit session.

Mission (operator-set): reliability-test **every selection** in the TUI —
verify each works, remove bloat, optimize functionality + workflow. Lens =
the In-Domain Principle (`foundations/in_domain_principle.md`): if the user has
to quit the app to complete or fix a selection, that's a defect.

---

## How to run it (self-guided; survives a /clear)

**Architecture rule — defends against silent context degradation:** the main
loop holds only this table, never file dumps. Fan-out one read-only Explore
subagent per `menu_section`; each returns structured rows; append them here.
Keep the main context lean. Re-read this file after any compaction.

- **Phase 0 — Inventory.** Enumerate every selection: `python3 scripts/_tui_inventory.py`
  (groups by section, counts selections, reports any handler that fails to
  construct or enumerate). → one row per (section, tag) below. This is the worklist.
- **Phase 1 — Assess.** One fan-out per section. Each selection scored on the
  rubric. Mark **READ vs RAN** honestly (see Verification).
- **Phase 2 — Triage.** keep / fix / merge / **cut**. Operator ratifies every
  cut (don't silently delete — "verify the work-holder before retiring": confirm
  nothing depends on it and it's truly dead first).
- **Phase 3 — Execute.** Fix arcs, each through the remediation surface
  (`remediation.py`) or the config-form pattern; decrement the **MF018 baseline
  (76→0)** in `scripts/lint.py` as escapes close; ship → test → lint →
  `scripts/fleet_sync.sh` → verify on moc (`git -C /opt/meshforge`). Close one
  arc fully before opening the next.

## Verification honesty (non-negotiable)

The TUI needs whiptail/dialog, usually sudo, sometimes hardware (radios). Most
selections can only be **READ** (static trace) headless. Every row states which:

- `READ` — traced the code path; no crash/escape found by inspection.
- `RAN` — actually executed in the TUI and observed correct behavior.
- `RAN(hw)` — exercised against real hardware (operator-in-the-loop).
- Never record `RAN` for a path only read. "Looks fine" ≠ "verified."

## Bloat taxonomy

- **dead** — registered but unreachable, or never actually works.
- **dup** — duplicates another selection's function (e.g. service-control overlap).
- **dubious** — claims/prints but doesn't deliver (read-only "diagnostics" dressed
  as fixes; copy-paste-the-command flows). MF018 escapes often live here.

## Degradation tells (operator watch-list)

Re-asking a settled point · contradicting an earlier row · citing a file:line a
fresh read doesn't match · verdicts going vague ("looks fine") · claiming
"verified" without READ/RAN. **Recovery: `/clear`, resume from this file.**

---

## Register

Legend — **verified**: READ | RAN | RAN(hw) · **in-app**: yes | escape(MF018) |
mixed · **rec**: keep | fix | merge→\<tag\> | cut · **status**: TODO | DONE

Phase 0 inventory (2026-05-29): **103 selections** across 11 sections, 75
handlers (`StartupHealthHandler` is a startup hook, no menu items). Worklist
enumerated by `scripts/_tui_inventory.py`. Phase 1 assessed via one read-only
Explore subagent per section; crash-class findings re-verified by direct read
in the main loop (cited below). All rows `READ` (TUI needs whiptail/sudo/hw;
headless can only static-trace).

| # | section | tag | handler | what it does | verified | in-app | risk | bloat | rec | notes | status |
|---|---------|-----|---------|--------------|----------|--------|------|-------|-----|-------|--------|
| 1 | about | `version` | AboutHandler | static version/feature msgbox | READ | yes | low | none | keep | about.py:45 | DONE |
| 2 | about | `changelog` | AboutHandler | prints last 8 releases, wait_for_enter | READ | yes | low | none | keep | wait_for_enter is the canonical interactive pattern — NOT a hang defect | DONE |
| 3 | about | `help` | AboutHandler | prints help text + shortcuts | READ | yes | low | none | keep | same pattern as changelog | DONE |
| 4 | about | `sysinfo` | AboutHandler | OS/mem/disk/uptime msgbox | READ | yes | low | none | keep | about.py:97, all reads try/except | DONE |
| 5 | about | `deps` | AboutHandler | checks 9 py module imports | READ | yes | low | none | keep | about.py:168 graceful on missing | DONE |
| 6 | about | `web` | WebClientHandler | open web UI / SSL config; headless-aware | READ | mixed | low | none | keep | web_client.py:35; ssl config writes /etc config, no backup before regex sub (web_client.py:338) — minor | DONE |
| 7 | configuration | `backup` | BackupHandler | create/list/restore/delete device backups | READ | yes | low | none | keep | device_backup.py | DONE |
| 8 | configuration | `channels` | ChannelConfigHandler | edit Meshtastic channels via CLI | READ | yes | low | dup | fix | _view_all/_view_single dup view logic | DONE |
| 9 | configuration | `config-api` | ConfigAPIHandler | start/stop REST config API :8081 | READ | yes | low | none | keep | LifecycleHandler, clean | DONE |
| 10 | configuration | `wizard` | FirstRunHandler | first-run hw/region/service setup | READ | yes | low | none | keep | first_run.py, all in-app | DONE |
| 11 | configuration | `fleet_backup` | FleetBackupHandler | local + peer-push fleet backup | READ | yes | low | none | keep | fleet_backup.py | DONE |
| 12 | configuration | `meshtasticd` | MeshtasticdConfigHandler | unified meshtasticd submenu | READ | mixed | medium | none | fix | config edit spawns `nano` (meshtasticd_config.py:543) — MF018 escape; see meshtasticd section | DONE |
| 13 | configuration | `rnode` | RNodeHandler | RNode detect/firmware/RNS recommend | READ | yes | low | none | keep | rnode.py | DONE |
| 14 | configuration | `meshforge` | SettingsHandler | app prefs (conn/propagation/loglevel) | READ | yes | low | none | keep | settings.py | DONE |
| 15 | configuration | `updates` | UpdatesHandler | one-click git pull + pip update | READ | yes | low | none | fix | **FIXED**: rnsd dual-pip-install discarded its result (subprocess.run doesn't raise on nonzero) → reported full success when the rnsd-critical copy failed. Now checks returncode + surfaces "rnsd Install Incomplete" msgbox w/ impact + in-app re-run. Pinned by tests/test_updates.py (4). | DONE |
| 16 | configuration | `webhooks` | WebhooksHandler | manage/test webhook endpoints | READ | yes | low | dup | keep | minor list/toggle loop dup | DONE |
| 17 | dashboard | `analytics` | AnalyticsHandler | link/health/coverage trends | READ | yes | low | none | keep | safe_import guarded | DONE |
| 18 | dashboard | `alerts` | DashboardHandler | current system/mesh/wx alerts | READ | yes | low | none | keep | graceful fallbacks | DONE |
| 19 | dashboard | `datapath` | DashboardHandler | tests 6 data sources | READ | yes | low | none | keep | comprehensive probes | DONE |
| 20 | dashboard | `nodes` | DashboardHandler | Meshtastic + RNS node counts | READ | yes | low | none | keep | both paths exception-handled | DONE |
| 21 | dashboard | `reports` | DashboardHandler | generate/view/save status report | READ | yes | low | none | keep | safe_import | DONE |
| 22 | dashboard | `score` | DashboardHandler | network health snapshot | READ | yes | low | none | keep | health_scorer | DONE |
| 23 | dashboard | `status` | DashboardHandler | all services + breakers + failover | READ | yes | low | none | keep | env_state/systemctl fallback | DONE |
| 24 | dashboard | `weather` | DashboardHandler | space weather SFI/Kp/bands | READ | yes | low | none | keep | propagation.get_space_weather | DONE |
| 25 | dashboard | `demo` | DemoHandler | start/stop simulated traffic | READ | yes | low | none | keep | demo_manager | DONE |
| 26 | dashboard | `stack_health` | FleetHealthHandler | 10 local-stack probes | READ | yes | low | none | keep | per-probe try/except, robust | DONE |
| 27 | dashboard | `latency` | LatencyHandler | service RTT/jitter/loss | READ | yes | low | none | keep | latency_monitor | DONE |
| 28 | dashboard | `moc_analysis` | MOCAnalysisHandler | slide-ready SVG analysis pack | READ | yes | low | none | keep | uses raw input() at 7 sites instead of wait_for_enter (moc_analysis.py:66+) — abstraction inconsistency, works in TTY; low | DONE |
| 29 | dashboard | `metrics` | MetricsHandler | historical metric trends | READ | yes | low | none | keep | safe_import guarded | DONE |
| 30 | dashboard | `mini_dudeai` | MiniDudeaiHandler | findings → fixes via surface | READ | yes | low | none | keep | in-app remediation, re-verified | DONE |
| 31 | dashboard | `mini_dudeai_rules` | MiniDudeaiHandler | in-app rule editor → candidate | READ | yes | low | none | keep | propose→ratify, no escape | DONE |
| 32 | dashboard | `health` | NodeHealthHandler | battery/signal/latency | READ | yes | low | none | keep | safe_import guarded | DONE |
| 33 | main | `e` | EmergencyModeHandler | EMCOMM submenu (broadcast/SOS/alerts) | READ | yes | low | none | keep | subprocess try/except; SOS polls 1s not sleep(60) | DONE |
| 34 | main | `q` | QuickActionsHandler | 13 single-key NOC shortcuts | READ | mixed | low | dup | keep | `_qa_node_list`/`_qa_follow_logs` dup CLI error-handling; works | DONE |
| 35 | main | `t` | TacticalOpsHandler | SITREP/zones/QR/ATAK | READ | yes | low | none | keep | safe_import degrades gracefully | DONE |
| 36 | extensions | `meshing` | ExtensionsHandler | install/manage meshing_around bot | READ | escape(MF018) | medium | none | fix | long install subprocs (git/venv/pip) show spinner once then silent (extensions.py:216); root-gated; consider infobox progress | DONE |
| 37 | maps_viz | `ai` | AIToolsHandler | AI/diagnostics submenu | READ | yes | low | none | keep | safe_import guards | DONE |
| 38 | maps_viz | `coverage` | AIToolsHandler | folium coverage map → browser | READ | yes | low | none | keep | writes ~/.local/share, daemon thread open | DONE |
| 39 | maps_viz | `heatmap` | AIToolsHandler | node density heatmap → browser | READ | yes | low | none | keep | folium+HeatMap safe_import | DONE |
| 40 | maps_viz | `livemap` | AIToolsHandler | start/stop live NOC map server | READ | yes | low | none | keep | systemd or in-proc fallback (#29) | DONE |
| 41 | maps_viz | `mfmaps` | AIToolsHandler | install/control meshforge-maps ext | READ | yes | medium | none | keep | root checks; git/systemctl/pip | DONE |
| 42 | maps_viz | `tiles` | AIToolsHandler | offline tile cache mgmt | READ | yes | low | none | keep | TileCache safe_import, confirm on destructive | DONE |
| 43 | maps_viz | `quality` | LinkQualityHandler | link quality analysis | READ | yes | low | none | keep | read-only topology queries | DONE |
| 44 | maps_viz | `export` | TopologyHandler | export GeoJSON/CSV/GraphML | READ | yes | low | dup | defer | **OVER-FLAG corrected**: `_export_topology` (topology.py:862) is NOT dead — reachable via Network Topology submenu (topology.py:99); top-level `export` tag → `_export_data_menu`. Genuine dup (two backends), but behavior-preserving consolidation w/ regression risk; defer. | DONE |
| 45 | maps_viz | `topology` | TopologyHandler | D3.js topology graph | READ | yes | low | none | keep | lynx fallback for headless | DONE |
| 46 | maps_viz | `traffic` | TrafficInspectorHandler | packet capture & analysis | READ | yes | medium | none | keep | diag handling for missing meshtasticd/pubsub | DONE |
| 47 | mesh_networks | `aredn` | AREDNHandler | AREDN node status/neighbors/scan | READ | yes | low | none | keep | safe socket probing | DONE |
| 48 | mesh_networks | `ham` | AmateurRadioHandler | callsign/Part97/ARES tools | READ | yes | low | dup | fix | `_ares_races_menu` dup of top-level ics213/netchecklist | DONE |
| 49 | mesh_networks | `automation` | AutomationHandler | auto-ping/traceroute/welcome | READ | yes | low | none | keep | engine init, in-app config | DONE |
| 50 | mesh_networks | `broker-menu` | BrokerHandler | MQTT broker profiles/restart | READ | yes | low | none | keep | safe_call wrapped | DONE |
| 51 | mesh_networks | `traffic` | ClassifierHandler | routing/notification stats | READ | yes | low | none | keep | _HAS_CLASSIFIER guard | DONE |
| 52 | mesh_networks | `dual_failover` | DualRadioFailoverHandler | dual-radio failover config/deploy | READ | yes | low | dup | fix | status shown twice; preflight dup of gateway_preflight | DONE |
| 53 | mesh_networks | `favorites` | FavoritesHandler | manage favorite nodes | READ | yes | low | none | keep | node_tracker delegation | DONE |
| 54 | mesh_networks | `gateway` | GatewayHandler | RNS-Meshtastic-MeshCore bridge cfg | READ | yes | low | none | keep | comprehensive, save/load proper | DONE |
| 55 | mesh_networks | `check` | GatewayPreflightHandler | bridge readiness validation | READ | yes | low | none | keep | colored output, in-app | DONE |
| 56 | mesh_networks | `export` | GatewayPreflightHandler | snapshot state as template | READ | yes | low | none | keep | works | DONE |
| 57 | mesh_networks | `load_balancer` | LoadBalancerHandler | dual-radio TX distribution | READ | yes | low | none | keep | safe optional import | DONE |
| 58 | mesh_networks | `mqtt` | MQTTHandler | nodeless mesh observation | READ | yes | low | none | keep | works | DONE |
| 59 | mesh_networks | `mesh_alerts` | MeshAlertsHandler | battery/emergency/disconnect alerts | READ | yes | low | none | keep | engine init, full log | DONE |
| 60 | mesh_networks | `meshchatx` | MeshChatXHandler | MeshChatX web client :8000 | READ | mixed | low | none | keep | xdg-open on desktop, in-app headless | DONE |
| 61 | mesh_networks | `meshcore` | MeshCoreHandler | companion radio detect/config | READ | yes | low | none | keep | sub-handler delegation | DONE |
| 62 | mesh_networks | `messaging` | MessagingHandler | send/recv/history/feed | READ | yes | low | none | keep | safe_call wrapped | DONE |
| 63 | mesh_networks | `nomadnet` | NomadNetHandler | NomadNet install/config/launch | READ | mixed | low | dup | fix | tmux/terminal launch escape; 7 helper mixins; _nomadnet_submenus dup menu layer | DONE |
| 64 | mesh_networks | `rns` | RNSMenuHandler | RNS submenu dispatcher | READ | yes | low | none | keep | clean dispatcher | DONE |
| 65 | mesh_networks | `meshtastic` | RadioMenuHandler | Meshtastic radio/channels/CLI | READ | mixed | low | none | keep | subprocess CLI output (clear_screen+print) | DONE |
| 66 | mesh_networks | `services` | ServiceMenuHandler | start/stop/restart services | READ | yes | low | none | fix | **CUT DONE (operator-ratified 2026-05-29)**: NOT a split — subagent's "168-line/split" was wrong (file was 1403). Real issue = ~350 dead lines: the in-TUI gateway-bridge launcher (`_run_bridge`+8 helpers) had NO menu path (verified: not in `_service_menu` dispatch, only a self-test called it; prod bridge = meshforge-gateway.service). Cut block + dead imports (sys, commands.rns, _sudo_cmd, check_udp_port). File 1403→1044. 3 concerns left (svc-control/OpenHamClock/MQTT) — no split needed. | DONE |
| 67 | mesh_networks | `test_gateway_rx` | TestGatewayRxHandler | MQTT→RNS→NomadNet e2e test | READ | mixed | low | none | keep | mosquitto_pub subproc; valuable cross-service check | DONE |
| 68 | meshtasticd | `mqtt` | MeshtasticdDeviceMQTTHandler | device MQTT via meshtastic CLI | READ | yes | low | none | keep | no config.yaml overwrite; device_config_store | DONE |
| 69 | meshtasticd | `lora` | MeshtasticdLoRaHandler | SPI/GPIO pins → overrides overlay | READ | yes | low | none | keep | overlay only, _offer_restart via surface | DONE |
| 70 | meshtasticd | `cleanup` | MeshtasticdNodeDBHandler | phantom node cleanup + MaxNodes | READ | yes | low | dup | fix | dup write_overlay+restart pattern w/ radio handler | DONE |
| 71 | meshtasticd | `hardware` | MeshtasticdRadioHandler | HAT template → config.d/ sanitized | READ | yes | low | none | keep | _sanitize_hat_overlay (#58) + apply_config_and_restart in-app | DONE |
| 72 | meshtasticd | `owner` | MeshtasticdRadioHandler | set long/short name via CLI | READ | yes | low | none | keep | first-party meshtastic cmds, no file writes | DONE |
| 73 | meshtasticd | `presets` | MeshtasticdRadioHandler | LoRa modem preset via CLI | READ | yes | low | none | keep | cli.set_lora_preset in-app, remediation on error | DONE |
| 74 | rf_sdr | `weather` | PropagationHandler | space weather + HF bands (NOAA) | READ | yes | low | none | keep | propagation.py:66 REST | DONE |
| 75 | rf_sdr | `antenna` | RFToolsHandler | compare antenna types/gain | READ | yes | low | none | keep | antenna_patterns safe_import, _HAS_ANTENNA flag | DONE |
| 76 | rf_sdr | `freq` | RFToolsHandler | LoRa channel freq calc (djb2) | READ | yes | low | none | keep | pure math, bounded | DONE |
| 77 | rf_sdr | `link` | RFToolsHandler | FSPL/link-budget/Fresnel/EIRP submenu | READ | yes | low | none | keep | rf_tools.py:227-365 — self-contained, formulas CORRECT (km/MHz/+32.45) | DONE |
| 78 | rf_sdr | `sdr` | SDRHandler | SDR spectrum/waterfall (Airspy) | READ | yes | medium | none | keep | numpy/SoapySDR safe_import + mock fallback | DONE |
| 79 | rf_sdr | `site` | SitePlannerHandler | site planner: link/range/Fresnel/presets | READ | yes | low | dup | fix | **FIXED**: `_calc_fresnel` now `fresnel_radius(d, f/1000)` (was nonexistent `fresnel_zone_radius` → ImportError); `_calc_link_budget` now `free_space_path_loss(d*1000, f)` (was swapped → ~60dB off). Verified 91.7dB/20.2m. Still a dup of rf_tools — consolidation deferred. | DONE |
| 80 | rns | `check` | RNSConfigHandler | validate RNS setup, offer migrate | READ | yes | low | none | keep | rns_config.py:378 | DONE |
| 81 | rns | `config` | RNSConfigHandler | view Reticulum config | READ | yes | low | none | keep | read-only | DONE |
| 82 | rns | `edit` | RNSConfigHandler | edit config in external editor | READ | escape(MF018) | low | none | keep | nano/vim spawn (rns_config.py:197); acceptable for raw config edit; post-edit drift check in-app | DONE |
| 83 | rns | `logging` | RNSConfigHandler | set loglevel → "Restart rnsd now" | READ | yes | low | none | keep | remediation surface, no shell escape (re-verified) | DONE |
| 84 | rns | `diag` | RNSDiagnosticsHandler | full RNS diagnostics | READ | yes | low | none | keep | delegates to engine; **but engine's repair offer crashes — see below** | DONE |
| 85 | rns | `drift` | RNSDiagnosticsHandler | config path drift, in-app fix | READ | yes | low | none | keep | in-app migrate/clear/restart/verify | DONE |
| 86 | rns | `repair` | RNSDiagnosticsHandler | RNS repair wizard | READ | yes | low | none | keep | `_rns_repair_menu` (rns_diagnostics.py:803), fully in-app | DONE |
| 87 | rns | `ifaces` | RNSInterfacesHandler | manage RNS interfaces | READ | yes | low | none | fix | **FIXED**: rns_interfaces.py:276 + _rns_diagnostics_engine.py:355 now call `_rns_repair_menu()` (was nonexistent `_repair_rns_shared_instance` → AttributeError on the repair path, both reachable). MF009 OK. | DONE |
| 88 | rns | `monitor` | RNSMonitorHandler | live RNS status auto-refresh | READ | yes | low | none | keep | Event().wait countdown, offers restart | DONE |
| 89 | rns | `sniffer` | RNSSnifferHandler | RNS packet sniffer | READ | yes | low | none | keep | delegates to monitoring.rns_sniffer | DONE |
| 90 | rns | `tools` | RNSToolsHandler | rnstatus/rnpath/identity/probe | READ | yes | low | none | keep | subprocess timeouts, hex validation | DONE |
| 91 | system | `review` | AutoReviewHandler | auto-review codebase | READ | yes | low | none | keep | ReviewOrchestrator delegation | DONE |
| 92 | system | `details` | ConfigDoctorHandler | drill into last Config Doctor run | READ | yes | low | none | keep | lazy init | DONE |
| 93 | system | `run` | ConfigDoctorHandler | audit per-box config drift | READ | yes | low | none | keep | read-only checks | DONE |
| 94 | system | `db_health` | DBAuditHandler | audit all SQLite DBs | READ | yes | low | none | keep | dynamic import db_audit.py, null-checked | DONE |
| 95 | system | `daemon` | DaemonHandler | start/stop/status headless NOC | READ | yes | low | none | keep | **OVER-FLAG corrected**: `from daemon_config import ...` (daemon.py:171) IS inside try/except (lines 170-217) AND `src/daemon_config.py` exists on the path — no NameError, graceful in-app error worst-case. Logs via journalctl/file is by-design. No action. | DONE |
| 96 | system | `diagnose` | DiagnosticsHandler | one-shot health via cli/diagnose.py | READ | yes | low | none | keep | subproc timeout=30; vague on nonzero rc (stderr hidden) — minor | DONE |
| 97 | system | `status` | DiagnosticsHandler | one-shot status via cli/status.py | READ | yes | low | none | keep | same vague-on-error as diagnose | DONE |
| 98 | system | `hardware` | HardwareHandler | detect SPI/I2C/USB; SPI enable | READ | yes | low | none | keep | writes /boot config.txt by design (SPI enable) | DONE |
| 99 | system | `logs` | LogsHandler | view/follow journalctl + app logs | READ | yes | low | none | keep | journalctl -f (terminal-native, by design); reads whole file then slices — risky on multi-MB logs | DONE |
| 100 | system | `network` | NetworkToolsHandler | ping/dns/traceroute/ports/ifaces | READ | yes | low | none | keep | `_detect_network_range` parses `ip route` without guards (network_tools.py:437) — minor | DONE |
| 101 | system | `reboot` | RebootHandler | safe reboot/shutdown | READ | yes | low | none | keep | confirm + _sudo_cmd | DONE |
| 102 | system | `discover` | ServiceDiscoveryHandler | auto-discover mesh services | READ | yes | medium | none | keep | nmap 120s timeout can stall UI; network regex loose | DONE |
| 103 | system | `shell` | SystemToolsHandler | drop to bash | READ | yes | low | none | keep | `shell` kept (intentional escape-hatch). **CUT DONE (operator-ratified 2026-05-29)**: removed dead `_system_tools_menu` + 30 submethods (1329 lines, file 1381→66). Verified unreachable: execute() only dispatches `shell`, 0 external refs to the menu or any leaf helper (`_user_systemctl_argv` collisions are independent copies in meshchatx/nomadnet). No capability lost — network/hardware/logs/services have live handlers; rest reachable via shell. | DONE |

### Already touched this session (expect green; re-verify, don't assume)
- `dashboard / mini_dudeai` (MiniDudeaiHandler) — findings → fixes via surface. In-app. RAN(tests).
- `dashboard / mini_dudeai_rules` (MiniDudeaiHandler) — rule-knob editor → candidate. In-app. RAN(tests).
- `rns_config` loglevel apply — now "Restart rnsd now" via surface. In-app. READ.
- `meshtasticd_radio` hardware-templates bootstrap — "Create templates now" via surface. In-app. READ.

---

## NOC Workflow Arc (started 2026-05-29) — IA / "who is this for"

The per-selection audit measured functionality + reliability but NOT **workflow**
(findability / journeys). Operator reframed: the TUI is "the command center for
humans"; the real pain is hunting for where things are. Assessment: the menu is
organized by **technical domain** (mirrors the handler code), not user task —
builder-centric, grown by accretion (22 handler batches). The dup findings are a
symptom (no task spine), not the disease.

- **Persona decision (operator-ratified)**: primary spine = **NOC operator**
  (daily monitor→diagnose→fix loop; composes the other roles).
- **Core diagnosis**: the daily loop is broken across the IA — you SEE a degraded
  service on the Dashboard, then hunt the fix across Mesh Networks / System /
  Configuration (rnsd repair is depth-4). Monitoring is findable; the *fix for
  what monitoring shows* is not.
- **Direction (operator-ratified)**: BOTH inline-Fix routing + menu reframe, in
  that order — fix-routing is the substance, the reframe layers on top.

### Shipped — first instance (commit `5d74eb2`, fleet-synced)
- NEW `service_remediation.py` — reusable primitive `service_fix_actions(svc,
  running)` + `offer_service_fix()`, generalizing mini-dudeai's `_fixes_for`
  to the service domain, feeding the existing `remediation` surface. Conservative
  (only box-owned services).
- `dashboard.py _service_status_display` — collects degraded services, offers a
  "Fix a Degraded Service" chooser in place of bare "press Enter". The fix comes
  to the operator (In-Domain extended: never even leave the status view to fix it).
- Tests: `test_service_remediation.py` (6) + 2 dashboard-path tests. `scripts/_noc_fix_probe.py` on-box validator.
- **On-box verified** (VolcanoAI/moc1/moc3): routing correct. The Dashboard's
  service set = `startup_checks.SERVICES_TO_CHECK` = **{meshtasticd, rnsd}** only
  (+ mosquitto in the no-startup-checks fallback) — so intended-off services
  (e.g. moc3 meshforge-map) are NEVER surfaced/nagged. Safe on all profiles.
  Interactive whiptail UX = operator hands-on test pending.

### Next steps (NOC Workflow Arc)
1. **Stack Health (FleetHealthHandler)** — wire the same primitive. ⚠️ Stack
   Health DOES surface gateway/map/bridge — so it needs an "expected on this
   box's profile" gate before offering to start them, or gateway-only boxes
   (moc3) get nagged to start intentionally-off services. (Dashboard is immune
   only because its set is the {meshtasticd,rnsd} core.)
2. **rnsd guided repair** — add the `_rns_repair_menu` wizard as a 2nd action
   alongside restart (richer fix for "running but shared-instance wedged").
3. **mini-dudeai dedup** — fold its `_restart_action`/`_fixes_for` onto the shared
   `service_remediation` primitive.
4. **NOC Home reframe** — once fix-routing is everywhere, restructure the top
   menu around the monitor→fix spine (presentation-layer; handlers intact).

---

## Rollup (Phase 1 complete — 2026-05-29)

- selections total: **103** (75 handlers; StartupHealthHandler = hook, no items)
- keep / fix / cut: **~88 / 14 / 1** (merge folded into fix; cut needs ratify)
- MF018 escapes remaining: 76 (baseline in `scripts/lint.py`) — unchanged; the
  defects found this pass are crash/logic bugs, not in-domain escapes, so the
  ratchet doesn't move yet.
- RAN vs READ coverage: **0 / 103** — entire pass is static-trace (headless box,
  no whiptail/sudo/radio). RAN/RAN(hw) requires operator-in-the-loop on a box.

### Phase 2 triage — confirmed defects (re-verified by direct read)

**Crash-class — ALL FIXED 2026-05-29** (pinned by `tests/test_tui_handler_wiring.py`, 6 tests; lint green; test_rf 107 + regression-guards 20 green):
1. **#79 `site` `_calc_fresnel`** — `from utils.rf import fresnel_zone_radius`
   (site_planner.py:120); only `fresnel_radius(distance_km, freq_ghz)` exists →
   ImportError every time. Args also swapped + MHz-not-GHz.
2. **#79 `site` `_calc_link_budget`** — `free_space_path_loss(f, d)`
   (site_planner.py:99) but sig is `(distance_m, freq_mhz)`; passes freq(915) as
   metres and dist-km(1.0) as MHz → ~60 dB wrong. Dangerous for HAM link planning.
3. **#87 `ifaces` + #84 `diag`** — `diag._repair_rns_shared_instance()`
   (rns_interfaces.py:276 AND _rns_diagnostics_engine.py:355); method does not
   exist (is `_rns_repair_menu`) → AttributeError exactly when operator clicks
   "repair" on a wedged shared instance. Two reachable call sites, one fix.

**Cut candidate (operator-ratify before deleting):**
- **#103 `_system_tools_menu`** + submethods (system_tools.py:56-1351, ~1300
  lines) — orphaned; `execute()` only dispatches `shell`. Verify-the-work-holder
  then cut.

### Fix-backlog arc — disposition after direct re-verification (2026-05-29)

The Phase-1 subagents flagged 10 backlog items. Re-verifying each by direct
read (the discipline that confirmed the crashers) found the backlog was mostly
behavior-preserving refactors + two over-flags — only ONE real reliability bug.

**FIXED (real bug):**
- **#15 updates** — rnsd dual-pip-install silent false-success. Fixed + 4 tests.
  (commit pending push)

**OVER-FLAGS (no defect — corrected in rows above, no code change):**
- **#95 daemon** — import IS guarded + module exists. Not unguarded.
- **#44 topology** — `_export_topology` is NOT dead; reachable via submenu.

**CONSOLIDATION refactors (dup, behavior-preserving — DEFERRED by judgment):**
these add regression risk for maintainability gain (dev-principle #3, lowest
priority) and none is a crash/wrong-result. Do one-per-commit with operator
steering, not a blind batch:
- #8 channels view-dup · #44 topology two-backend export dup · #48 ham ARES
  submenu dup · #52 dual_failover status-dup · #63 nomadnet submenu-layer dup ·
  #70 cleanup restart-dup.
- **#66 services — RESOLVED via CUT (not split)**: the "split candidate" was a
  mis-diagnosis. Underneath it was ~350 lines of dead in-TUI bridge launcher;
  cut it (1403→1044), and the split need dissolved. Pattern: when a "split"
  flag fires, check for dead code first — the file may be big because it carries
  unreachable weight, not because its live concerns need separating.

**UX hardening (DEFERRED):**
- #36 meshing-around install shows spinner once then goes silent during long
  git/venv/pip steps — wants per-step infobox progress. Cosmetic, not a bug.

**Cut DONE (operator-ratified):** #103 `_system_tools_menu` — 1329 dead lines
removed (file 1381→66). Verify-the-work-holder confirmed: unreachable + no
capability loss + `_user_systemctl_argv` external refs are independent copies.

**Noted (not acted):** `_user_systemctl_argv` is independently duplicated in
`_meshchatx_service_ops.py`, `_nomadnet_service_ops.py` (+ meshchatx.py) — a
real root→real-user systemctl helper that wants promoting to a shared util
(`utils.service_check`?). Future dedup candidate, low priority.

**Lesson:** subagent "dup/dead" verdicts ran ~30% over-flag (2 of 6 dead/crash
claims I spot-checked were wrong); crash-class verdicts I personally verified
were 100% real. Trust the fan-out for breadth; re-verify before acting.
