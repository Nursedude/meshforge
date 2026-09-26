"""Live-truth ledger — which TUI actions were ever checked against a LIVE answer.

Born 2026-09-25 from the operator's question about the TUI audit — "going
through each function/feature making sure it's wired and working as it's
supposed to". The truth sweep (`action_truth.py`) proves every action ROUTES
and tells the truth when every external is DEAD. It does not prove a screen is
RIGHT when the externals are alive: Node Health read rnsd DOWN on every
healthy box for months, and no sweep could see it (level-two text is not
gated). This table is the record of which actions a human or a session
actually checked against the real system, when, how far, and on what evidence.

DATA, not behaviour. Rules for an entry (pinned by
`tests/test_live_truth_ledger.py`):
  * the key is a LIVE (section, tag) — a retired action fails the test;
  * `date` is ISO; `box` names WHERE by role ("dev/manager box", "Airspy
    host") — repo source carries no fleet hostnames (MF014); the exact box is
    in the provenance line `evidence` points to;
  * `partial` is explicit: True when the scope left something unchecked;
  * `scope` says exactly what was checked — "banner only" is an honest entry,
    a bare "works" is not;
  * `evidence` points at something another reader can re-check (a commit, a
    provenance line, a quoted output), never "I looked at it".

An action with NO entry has never been checked live. That is the honest
default, not a failure; the rendered Live column in the capability index
(`scripts/gen_capability_index.py`) shows which rows are still blank.
Entries age: a screen verified before its code changed is history, so
re-verify after substantive edits and update the date.
"""
from __future__ import annotations

from typing import Dict, Tuple

Action = Tuple[str, str]   # (menu_section, action_tag)

LIVE_VERIFIED: Dict[Action, Dict[str, str]] = {
    ("dashboard", "status"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "each listed unit's state (meshtasticd, rnsd, mosquitto 'running') against "
                 "`systemctl is-active` — all three active, all three match",
        "partial": False,
        "evidence": "live render + is-active 06:55 HST, session of 2026-09-25",
    },
    ("dashboard", "nodes"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified: radio's own 90 online/334 known vs its journal "
                 "telemetry; map view 191 vs /api/status source_diagnostics; RNS 43 vs "
                 "`rnpath -t` (same parser as Stack Health). Before the fix it read RNS 0 "
                 "(rnstatus -a lists interfaces) and 'HTTP API unavailable' (ESP32-only API)",
        "partial": False,
        "evidence": "utils/node_counts.py + tests/test_node_counts.py, this commit; live "
                    "render 07:0x HST",
    },
    ("dashboard", "weather"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified against NOAA's live feeds: Kp 2 (feed estimated_kp "
                 "1.67), Planetary A 19 (daily-geomagnetic-indices.txt), storm level derived "
                 "from Kp. Before: Kp None (NOAA changed the feed to objects), A -1 "
                 "(Fredericksburg's not-computed column), 'Quiet' as a default",
        "partial": False,
        "evidence": "utils/space_weather.py parse_planetary_a + Kp object format, "
                    "tests/test_space_weather_truth.py real-row cases, this commit",
    },
    ("dashboard", "delivery"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "every section 'inert' against the box: no meshforge-gateway unit, synth "
                 "and propagation soak timers `not-found` — inert is the true state here; "
                 "a gateway box (moc/moc3) not yet checked",
        "partial": True,
        "evidence": "live render + `systemctl cat` / `is-enabled` 06:55 HST",
    },
    ("dashboard", "stack_health"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "uptimes vs systemd ActiveEnterTimestamp (rnsd 2.0 d, map 1.8 d, "
                 "meshtasticd 5.2 d), NomadNet active (`systemctl --user`), path table 43 "
                 "network destinations (rnpath) — all match",
        "partial": False,
        "evidence": "live render + systemctl/rnpath 06:55–07:0x HST",
    },
    ("dashboard", "alerts"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified: Weather now resolves the OPERATOR's point (the retired "
                 "EAS panel's eas_location.json, orphaned by the GTK removal) — NWS confirmed a "
                 "Hurricane Watch + Tropical Storm Warning for Big Island East that the old "
                 "screen, checking the template's Washington point, could never show. "
                 "Mesh now UNKNOWN (the TUI's alert engine has no feed "
                 "attached — was 'No active alerts' from an engine that observed nothing); "
                 "Weather now UNKNOWN (location = the TEMPLATE's example point 48.50,-123.0 "
                 "on every box — the Washington coast; NWS had a Small Craft Advisory there "
                 "the plugin's severity filter dropped). System line NOT verified (harness "
                 "has no env_state)",
        "partial": True,
        "evidence": "plugins/eas_alerts.py location_is_template + mesh_alert_engine.has_feed, "
                    "tests/test_dashboard_alerts_truth.py; NWS api.weather.gov cross-check",
    },
    ("dashboard", "datapath"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "all six checks vs the system: :4403 accepting, CLI responds, /json N/A "
                 "(#76), pubsub importable, collector 476 mapped / 560 unpositioned / radio "
                 "334 (now labelled — was '334 nodes (476 with GPS)', two scopes mixed), "
                 "rnpath 48 = 43 + 5 IPC; the fake '~677 node refs' dropped",
        "partial": False,
        "evidence": "collector properties dumped live; dashboard.py this commit",
    },
    ("dashboard", "latency"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "probe results vs listening sockets (4403, 9443, 1883 all listening, all "
                 "read OK); rnsd no longer TCP-probed (a unix-socket shared instance — it "
                 "read DOWN on every healthy box); status/degraded honest when cold",
        "partial": False,
        "evidence": "utils/latency_monitor.py NOT_TCP_PROBED; live render 2026-09-25",
    },
    ("dashboard", "health"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "latency probe as above; battery (4 judged) and signal (117 nodes, 6 judged, "
                 "55 SNR readings) match an independent SQL recount using the code's "
                 "consecutive-repeat collapse. Both delegate to the Analytics screens; "
                 "labels renamed to match the screens they open",
        "partial": False,
        "evidence": "recount + live render 2026-09-25; node_health.py labels this commit",
    },
    ("dashboard", "analytics"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified: Health History read 'Known 18' for an hour holding no "
                 "snapshot (the collector snapshots every ~63 min, so ~1 hour in 21 gets only "
                 "stragglers) — now '— (no snapshot this hour)'; the header called 20k rows "
                 "'snapshots'. Coverage 428 positioned nodes matches the DB; trends/alerts are "
                 "the same code as Node Health signal/battery (verified there)",
        "partial": False,
        "evidence": "node_history_analytics._snapshots_per_hour + tests; snapshot gaps measured "
                    "over 48 h (median 63 min)",
    },
    ("dashboard", "traffic_pulse"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified (snapshot view): DIAG 'no active signals' = /api/status "
                 "watchdog.signals []; queue 4 pending / 62 dead-letter = message_queue.db, "
                 "every row from a test 51 d earlier — now shows 'last activity 51 d ago'; QA "
                 "read 'Sending traffic' with sent=0 and no event ever — now 'quiet'. "
                 "TELEMETRY/RF lines and the live auto-refresh loop not cross-checked",
        "partial": True,
        "evidence": "monitoring/traffic_pulse.py _iso_age_s + never-active branch, "
                    "tests/test_traffic_pulse.py TestQueueAge",
    },
    ("dashboard", "reports"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "REBUILT then verified: radio 88/335 vs its journal telemetry line; RNS 43 "
                 "vs rnpath; history/trends/predictive from the same code as Analytics; "
                 "watchdog vs /api/status; RF reference vs the LoRa formula and firmware "
                 "2.7.26's preset table (MEDIUM_FAST was SF10 in three tables). Old report: "
                 "'No nodes tracked' + 'Network health is degraded' from nothing measured. "
                 "Generate & View and Generate & Save both exercised",
        "partial": False,
        "evidence": "utils/report_generator.py, utils/meshtastic_modem.py, "
                    "tests/test_meshtastic_modem.py (planted SF10 -> 4 failures)",
    },
    ("rns", "ifaces"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified: Interface Status showed the RNode leg DISABLED while "
                 "rnsd ran it (~650 KB each way) — MeshForge read only `enabled`, RNS reads "
                 "`interface_enabled` OR `enabled`; the list showed 'enabled = ?' and a counter "
                 "treated a missing key as enabled. Now one interface_enabled() mirroring RNS "
                 "Reticulum.py; status rows vs rnstatus match. add/enable/disable/remove/fix/"
                 "plugin not exercised (they write)",
        "partial": True,
        "evidence": "commands.rns.interface_enabled + tests; live render: RNode UP",
    },
    ("rns", "config"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "shows /etc/reticulum/config verbatim — the file rnsd was started with "
                 "(--config /etc/reticulum)",
        "partial": False,
        "evidence": "live render vs file; rnsd ExecStart",
    },
    ("rns", "diag"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified: 'RNS identity: not created' on a node whose rnsd has run "
                 "for days — seven call sites looked at <configdir>/identity, a file RNS never "
                 "reads; rnsd's identity is <configdir>/storage/transport_identity (RNS "
                 "Transport.py). create_identities no longer writes the stray file. Now also "
                 "warns when the private key is group/world accessible (mode 666 here and on 6 "
                 "other boxes — permissions NOT changed, operator's call). Interface traffic "
                 "rows match rnstatus. FIXED too: 'NomadNet: RUNNING (port conflict!)' fired whenever "
                 "nomadnet ran (the normal CLIENT state) and its Fix flow offered `pkill -f "
                 "nomadnet`; now only when NomadNet OWNS @rns/<name> (live: owner rnsd). "
                 "Interface/tool rows beyond traffic not re-checked",
        "partial": True,
        "evidence": "commands.rns.rnsd_identity_path / identity_exposure; "
                    "tests/test_rns_identities.py (8 fail on the old code)",
    },
    ("mesh_networks", "messaging"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified: Statistics printed 'Total: 0' beside 'Received: 19185' "
                 "(read a key the command never returns); now strict keys + the newest "
                 "message's age (50 d). Diagnose/Routing called this CLIENT_MUTE radio "
                 "(radio-read) 'Unknown role' + rebroadcast True — the CLI prints the role "
                 "NUMBER; now mapped via the protobuf enum. FOUND, not changed: the store is "
                 "~all TEST fixtures written before 08-06 (leak closed: 2,148 messaging tests "
                 "left it untouched); the live MQTT listener sees only the one uplinked channel. "
                 "Send / live feed / RX control not exercised (they transmit or hold the radio)",
        "partial": True,
        "evidence": "tests/test_messaging_truth.py (fail on old code); radio role + MQTT "
                    "config read via MeshtasticConnection; mosquitto_sub 90 s = 0 messages",
    },
    ("mesh_networks", "check"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified: the gateway LXMF hash got a green check and 'send from "
                 "NomadNet to this address' on a box with no gateway running, and the verdict "
                 "said 'bridge should work' with no gateway.json. Now the hash line depends on "
                 "the service being RUNNING (control: gateway box reads running) and the "
                 "verdict says 'no gateway configured yet'. Template drift rows vs the radio "
                 "config match (LONG_FAST ch20, uplink on 'meshforge')",
        "partial": False,
        "evidence": "gateway_preflight._gateway_running + tests (fail on old code); gateway "
                    "box: installed+available, systemctl is-active = active",
    },
    ("system", "logs"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified: rnsd Logs read the journal only, but `rnsd --service` "
                 "logs to <configdir>/logfile — it now shows that file (817 lines, 947 NUL bytes "
                 "stripped; last entries = the 09-22 RNode serial drops). Error/boot/live views "
                 "name units absent on this box and read user-scope units with --user-unit "
                 "(`-u <absent>` printed '-- No entries --', reading as quiet). meshtasticd (live "
                 "router lines), kernel (dmesg), app-log list (48 files, newest 09-23) and crash "
                 "log (tui_errors.log, 5,012 lines) render real data; level / cleanup not "
                 "exercised (they write)",
        "partial": True,
        "evidence": "handlers/logs.py _mesh_journal_args + _view_rnsd_recent; "
                    "tests/test_logs_truth.py (all 4 fail on the old code)",
    },
    ("rf_sdr", "link"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "RF Tools calculators rendered with defaults + edge inputs, checked by hand: "
                 "FSPL 1 km/915 = 91.7 dB; Fresnel 5 km = 20.2 m (60% 12.1); link budget "
                 "arithmetic; EIRP 25 dBm = 316 mW. FIXED: EIRP called 33 dBm into 0 dBi "
                 "'LEGAL' (now FCC 15.247 conducted + EIRP); slot calculator 0-based (ch20 -> "
                 "907.125, radio says 906.875). Antenna Comparison = the rf_sdr/antenna screens",
        "partial": False,
        "evidence": "rf.fcc_part15_247_check, rf_tools._calc_frequency_slot, tests in "
                    "test_rf.py + test_meshtastic_modem.py (plants fail)",
    },
    ("rf_sdr", "antenna"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "compare / coverage / specs rendered; range factors checked by hand "
                 "(8 dBi: 10^(5.85/20) = 1.96x; 13 dBi Yagi 3.49x). They assume FREE-SPACE "
                 "scaling and did not say so — now stated, with the forest contrast (n~5: the "
                 "same Yagi ~1.65x). Antenna gain/beamwidth catalogue values not verified "
                 "against manufacturer datasheets",
        "partial": True,
        "evidence": "rf_tools._antenna_compare_all / _antenna_coverage_estimate notes; live "
                    "renders 10:4x HST",
    },
    ("rf_sdr", "freq"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "FIXED then verified against the fleet's own radios: LongFast default and "
                 "channel_num 20 -> 906.875 MHz (this box's radio: LONG_FAST ch20); ShortTurbo "
                 "ch8 -> 905.750 (the ShortTurbo segment); out-of-range channel_num refused with the reason. "
                 "Regions = firmware RDEF (v2.7.26)",
        "partial": False,
        "evidence": "utils/meshtastic_modem.slot_centre_mhz; live radio read via "
                    "commands.rnode.local_meshtastic_lora",
    },
    ("rf_sdr", "site"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "link budget (22+4.3-91.7 = -65.4 dBm, margin 64.6) and preset comparison "
                 "(sensitivities = formula; free-space rows = radio horizon 11.7 km at 2 m, now "
                 "footnoted). Forest model (n=5, 20 dB fade) predicts 82 m at 22 dBm / 65 m at "
                 "17 dBm for the SF7 RNode leg — consistent with the operator's '17 dBm too low' "
                 "at ~76 m through ohia; NOT changed. Range estimator, Fresnel, antenna, "
                 "frequency reference, external tools not checked",
        "partial": True,
        "evidence": "live renders 10:3x HST; preset_impact.format_comparison_table footnote",
    },
    ("rf_sdr", "weather"): {
        "date": "2026-09-25", "box": "dev/manager box",
        "scope": "summary / space weather / band conditions / NOAA alerts vs NOAA's own feeds: "
                 "SFI 115, Kp 2, A 18 (same parsers as Dashboard › weather), alerts = NOAA "
                 "text. FIXED: sunspot number was never fetched (always 'None') — now SESC 124 "
                 "from daily-solar-indices.txt, matching NOAA's 09-24 line; 'Updated' was our "
                 "fetch time (now 'Fetched'); band table was MeshForge's rule of thumb labelled "
                 "'NOAA SWPC' and scored a missing SFI 'very poor' / missing Kp 'excellent' — "
                 "now attributed honestly and refused unless both are observed. DX spots, "
                 "ionosonde, VOACAP, sources not checked",
        "partial": True,
        "evidence": "space_weather.parse_daily_sunspot + tests (planted column fails); "
                    "commands.propagation.get_band_conditions refusal test",
    },
    ("rf_sdr", "sdr_watch"): {
        "date": "2026-09-25",
        "box": "Airspy host",
        "scope": "view rendered from the Airspy host's REAL interference.jsonl through the real "
                 "sdr_view code (witness freshness, per-window classes, class D, blind "
                 "spots); the handler itself not launched inside a live TUI session",
        "partial": True,
        "evidence": "render at 00:04 HST on 4c7866c5 quoted in session; provenance touch "
                    "line 2026-09-24 23:46:36 (the Airspy host)",
    },
    ("rf_sdr", "sdr"): {
        "date": "2026-09-24",
        "box": "Airspy host",
        "scope": "MOCK-mode banner only (reason + USB bus line: 'SoapySDR is not installed "
                 "… An Airspy IS attached'); the monitor's capture screens NOT verified",
        "partial": True,
        "evidence": "commit 6f5249f7; banner rendered on the Airspy host at 21:1x HST on 14c565e6",
    },
    ("maps_viz", "livemap"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "menu [RUNNING] vs :5000 + the unit's active state; 'Auto-open [ON]' "
                 "vs map_settings.json auto_open_map; View logs vs the unit's journal "
                 "(same last lines). Browser snapshot NOT run (writes a "
                 "file + opens a browser); its data path now reads the running service, "
                 "unit-tested only. start/stop/restart not exercised",
        "partial": True,
        "evidence": "live render 09:2x HST, session of 2026-09-26; this commit",
    },
    ("maps_viz", "mfmaps"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "box where meshforge-maps is installed but stopped+disabled: menu "
                 "'Stopped', Service Status, Logs ('-- No entries --') and Health "
                 "('not running') all match the unit's state; Health's field names matched a "
                 "RUNNING box's live /api/health (status/score/data_age_seconds/"
                 "sources_reporting/components). Not rendered on a box where it runs; "
                 "the health screen omits node_history_write_error_* (null fleet-wide)",
        "partial": True,
        "evidence": "live render + the unit's active/enabled state + :8808/api/health on "
                    "four boxes, 09:2x HST",
    },
    ("maps_viz", "coverage"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "FIXED then verified: 'Live from meshtasticd only' said 'No nodes found' "
                 "beside 196 radio nodes (filtered on a `source` value no feature carries, "
                 "5 boxes measured); now 'Found 360 nodes' = the service geojson's "
                 "local_radio non-federated count (360), map generated. 'All sources', "
                 "MQTT (0 is true: not_configured) and file NOT run",
        "partial": True,
        "evidence": "live render 09:32 HST + jq over /api/nodes/geojson; this commit",
    },
    ("maps_viz", "tiles"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "Cache Stats 0 tiles vs the cache dir; the screens now say what "
                 "coverage_map.py measured 09-15 — the cache is written and never read, "
                 "no map serves it. Download / Clear NOT run",
        "partial": True,
        "evidence": "live render 09:3x HST; tests/test_tile_cache_honesty.py; this commit",
    },
    ("maps_viz", "quality"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "FIXED then verified: all five graph panes read 'No links found' / 'No "
                 "link quality data' from the TUI's own empty topology (the graph lives in "
                 "the gateway process); now they say NOT OBSERVABLE like the Topology "
                 "sibling (cured 09-23). Score Single Link (a calculator) not run",
        "partial": True,
        "evidence": "live render 09:4x HST; tests/test_link_quality_observability.py; "
                    "this commit",
    },
    ("maps_viz", "traffic"): {
        "date": "2026-09-26", "box": "dev/manager box + both gateway boxes",
        "scope": "FIXED then verified: opening the screen truncated traffic.log (the "
                 "gateway's live log on gateway boxes; every gateway restart did too). "
                 "After deploy both gateways restarted and their earlier header + packet "
                 "lines survived (202->281, 166->235 lines, 2 headers, new header names "
                 "the new MainPID). Statistics/Archive (0/DISABLED) match on the dev box. "
                 "FIXED: on a gateway box (rendered on one) the menu said 'Capture: STOPPED' "
                 "beside Statistics of 5,027 gateway-captured packets; it now says 'in this "
                 "TUI' plus the shared DB's newest-packet age (dev box: 49d = the DB mtime). "
                 "Sniffer rows read source 'local' for every packet — FIXED 417a16e9 "
                 "(verified: real sender hashes after the gateway restarts). Seen, NOT "
                 "fixed: each announce is stored twice (counts doubled); size 0 for "
                 "announces is 'no raw bytes', not measured",
        "partial": True,
        "evidence": "commit fe5c4ef5; ssh line counts before/after the 10:24 HST restarts",
    },
    ("maps_viz", "topology"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "Statistics (820 nodes, links NOT OBSERVABLE, source node_cache.json "
                 "'saved 54d ago' = its mtime 08-03); Links/ASCII say NOT OBSERVABLE. "
                 "FIXED: View Nodes said 'Found 820 nodes' as if live while listing 50 of a "
                 "54-day-old cache — now says 'first 50 by name' + source + age. The cache "
                 "holds a test fixture '!deadbeef' (first_seen 07-21, the test-leak window) — "
                 "operator's call. Events/Trace/Browser/Export-from-here not run",
        "partial": True,
        "evidence": "live render 10:4x HST + jq over node_cache.json; this commit",
    },
    ("maps_viz", "export"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "FIXED then verified: GeoJSON failed 'Permission denied' on a ROOT-owned "
                 "topology.geojson (24 root-owned files in the user's meshforge dirs, "
                 "Feb–Apr sudo runs — operator's call); CSV reported 'Exported' for 1 node "
                 "row + a header-only edges file — the TUI's graph is empty by "
                 "construction. Now every format refuses with NOT OBSERVABLE and points at "
                 "the map's /api/nodes/geojson",
        "partial": True,
        "evidence": "live run 10:51 HST (files + ls -la); tests/test_topology_nodes_label.py; "
                    "this commit",
    },
    ("system", "platform_updates"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "Reboot 'not owed' vs no reboot-required + running kernel = newest "
                 "installed; Pending 19 = `apt-get -s upgrade` Inst lines (the 20th is the "
                 "held package, kept back — the screen does not say '+1 held'); Lists "
                 "'refreshed 9h ago' = apt-daily's 03:38 success (update-stamp); Holds "
                 "meshtasticd = apt-mark showhold",
        "partial": False,
        "evidence": "live render + apt/dpkg/journal 12:5x HST, session of 2026-09-26",
    },
    ("system", "platform_pins"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "rns 1.3.8+mf.0 / lxmf 1.0.1+mf.1 in BOTH the system python and the venv; "
                 "meshtasticd the 54e0d8d build (dpkg adds a packaging suffix) and HELD. "
                 "The predicates' 'watched' legs were not re-evaluated",
        "partial": True,
        "evidence": "live render + importlib.metadata in both interpreters + dpkg-query",
    },
    ("system", "platform_posture"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "Base trixie / python 3.13.5 vs /etc/os-release + python3 --version; "
                 "verdict OK on target",
        "partial": False,
        "evidence": "live render 12:5x HST",
    },
    ("system", "db_health"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "20 DBs listed; traffic_capture 76K = its 77,824 bytes; metrics owner now "
                 "the user (after the approved chown). Pragmas/retention columns not "
                 "independently re-derived",
        "partial": True,
        "evidence": "live render + ls -la",
    },
    ("system", "starlink_status"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "latency/drop/obstruction agree with mini's independent uplink reading the "
                 "same morning (58 ms / 0.00 % / 0.39 %); dish identity/software not "
                 "checked against the dish app",
        "partial": True,
        "evidence": "live render vs the rollup's uplink line 07:22 HST",
    },
    ("system", "hardware"): {
        "date": "2026-09-26", "box": "dev/manager box",
        "scope": "FIXED then verified: Detect lists = /dev spidev/i2c/ttyACM + lsusb; Radio "
                 "Health warned 'Web module mismatch: HTTP API shows 0 nodes but CLI sees "
                 "340' — meshtasticd never serves /json/nodes (#76), unavailable was mapped "
                 "to 0. Now 'Nodes: 341 (CLI) · No issues detected'. Note: the CLI count "
                 "opens a short TCP client to :4403 (no gateway-ownership guard). RNode "
                 "setup / Enable SPI not run",
        "partial": True,
        "evidence": "live render before/after, 12:5x-13:1x HST; this commit",
    },
}


def live_class(section: str, tag: str) -> str:
    """The Live-column cell: blank when never checked live."""
    e = LIVE_VERIFIED.get((section, tag))
    if not e:
        return ""
    # Explicit, never inferred from the prose: a keyword guess marked a fully
    # verified entry partial because its scope said "(ESP32-only API)".
    return f"{'◐' if e['partial'] else '✓'} {e['date']} {e['box']}"
