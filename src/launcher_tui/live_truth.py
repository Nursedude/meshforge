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
}


def live_class(section: str, tag: str) -> str:
    """The Live-column cell: blank when never checked live."""
    e = LIVE_VERIFIED.get((section, tag))
    if not e:
        return ""
    # Explicit, never inferred from the prose: a keyword guess marked a fully
    # verified entry partial because its scope said "(ESP32-only API)".
    return f"{'◐' if e['partial'] else '✓'} {e['date']} {e['box']}"
