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
