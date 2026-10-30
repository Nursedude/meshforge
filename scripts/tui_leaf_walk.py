#!/usr/bin/env python3
"""DISPATCH allowlisted TUI leaves and prove each one returns to its parent.

The sibling driver ``scripts/tui_smoke.py`` renders every screen through
real whiptail and states plainly that it NEVER dispatches. This is the
other half, kept in a separate file on purpose: the safe tool and the
risky one must not share a binary, so nobody reaches for this one by
accident.

WHAT "RETURNS TO ITS PARENT" MEANS HERE, EXACTLY
------------------------------------------------
``HandlerRegistry.dispatch`` wraps every ``execute()`` in
``TUIContext.safe_call``, which CATCHES the exception and shows an error
dialog. So "it came back" is not the question — a handler that raises
comes back too. This driver requires BOTH:

  * ``execute()`` returned within the timeout, AND
  * it surfaced none of ``safe_call``'s seven error dialogs.

Otherwise a crashing leaf would score as a pass, which is the fail-dark
class this whole arc exists to stop shipping.

DEFAULT-DENY (the operator's call, 2026-09-16)
-----------------------------------------------
An action is dispatched ONLY if it is in ``ALLOWLIST`` below, with a
written reason. Everything else reports **UNPROVEN** and is never
counted as a pass. Unobservable is not healthy.

⚠️ THE ALLOWLIST CANNOT BE BUILT BY A SCANNER. Scanning handler modules
for mutation markers (subprocess, *_service, _sudo_*, file writes)
returns 48 "read-only" actions, and ``meshtasticd_radio`` — which sets
the radio's owner name, presets and hardware config — is INSIDE that
result. It mutates through ``utils.device_config_store``,
``core.meshtasticd_config`` and ``remediation``, every one a
function-local import one or two hops away, across 22 sibling ``_*.py``
handler modules plus utils/, core/, commands/ and remediation/. What a
file CONTAINS says almost nothing about what its action DOES. A checker
that forecasts the defect is the re-check, not the gate. So each entry
below is hand-read, and the reason names what was followed.

The dialog layer is a scripted stand-in, not whiptail: every prompt
answers "cancel/back" and nothing is typed. That is deliberate — this
driver proves CONTROL FLOW, and tui_smoke.py already proves the same
screens PAINT. Neither alone is the whole claim.

⚠️ DEPTH IS ONE LEVEL, and the report says so on every run. Cancelling
the first prompt means a leaf that opens a submenu is proven to open it
and return — NOT that its contents work. Going deeper means dispatching
sub-actions, which is exactly where the mutating ones live, so depth is
earned per tree the same way entry to this allowlist is. Claiming
"every leaf works" off a one-level walk would be the same shape as
claiming a socket that listens means a feature that functions.

Usage:
    python3 scripts/tui_leaf_walk.py              # walk the allowlist
    python3 scripts/tui_leaf_walk.py --list       # what is/is not allowed
    python3 scripts/tui_leaf_walk.py --only TAG

Exit: 0 when every allowlisted leaf returned clean, 1 on any failure,
2 when the driver could not run OR a screen said UNKNOWN (classify()).
UNPROVEN never affects the exit code in either direction — it is
reported, loudly, as the size of the gap.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
TUI = SRC / "launcher_tui"

#: Titles ``TUIContext.safe_call`` uses when it catches an exception.
#: Seeing one of these means the leaf FAILED, however politely it
#: returned afterwards.
SAFE_CALL_ERROR_TITLES = {
    "Module Not Available", "Operation Timed Out", "Permission Denied",
    "File Not Found", "Connection Failed", "Error",
}

#: Actions that may NEVER be dispatched by this driver, on any box, even
#: if someone adds them to ALLOWLIST below. A rule written only in a
#: comment is a rule the next session can talk itself out of; DENYLIST
#: wins over ALLOWLIST in ``main()`` and says so out loud.
#:
#: The criterion is not "risky" — plenty of allowlisted work is risky in
#: the ordinary sense. It is: **the blast radius includes the thing
#: running the drill, or the operator's ability to intervene.** Never arm
#: a guard whose blast radius holds the session arming it.
DENYLIST: dict[tuple[str, str], str] = {
    ("system", "reboot"):
        "Reboot / Shutdown takes down the host running this walk, along "
        "with every service on it and the operator's way in. No allowlist "
        "entry can make that observable — the process that would report "
        "the result is inside the blast radius.",
}

#: (section, tag) -> why dispatching this is safe ON THIS BOX.
#: Hand-read, one at a time, following the call graph to a terminal that
#: provably only reads. STARTS EMPTY AND GROWS BY EVIDENCE.
#:
#: ⚠️ Service control is a SEPARATE judgement from "does it mutate". A box
#: whose declaration says a unit is stopped has been configured that way
#: ON PURPOSE, and restarting it from a drill would overwrite a human
#: decision with a test fixture. Read the box's own declaration
#: (``service_overrides`` in deployment.json) before allowlisting
#: anything that starts or stops a unit — a deliberate absence is not a
#: gap to be filled.
ALLOWLIST: dict[tuple[str, str], str] = {
    # ---- about/ — read 2026-09-16, handlers/about.py in full (234 lines).
    ("about", "version"):
        "reads __version__ and shows a msgbox. No I/O beyond the import.",
    ("about", "changelog"):
        "reads VERSION_HISTORY from __version__.py, formats, prints, waits "
        "for Enter. clear_screen() writes escape codes to the tty and "
        "nothing else.",
    ("about", "sysinfo"):
        "platform.*, /proc/uptime, /proc/meminfo, os.statvfs('/'), a stat() "
        "sweep of the log dir, and ONE subprocess — `lsb_release -ds`, "
        "capture_output, timeout=5, argument list (no shell). Every leg "
        "reads.",
    ("about", "deps"):
        "__import__ of 9 optional modules to report presence + __version__. "
        "Imports only; none of them is constructed (notably RNS: the module "
        "import does not build a Reticulum instance, which is the #68/#69 "
        "hazard).",
    ("about", "help"):
        "prints a static string and waits for Enter.",

    # ---- dashboard/ delivery — read 2026-10-30, handlers/delivery.py in
    # full (75 lines) + utils/delivery_view.py (746): json reads of the
    # gateway's published snapshots, socket.gethostname(), and two
    # subprocess legs — `systemctl show` via
    # watchdog_probe_core._resolve_main_pid_status and `systemctl
    # is-enabled` (argument lists, timeout=5). No write, no DB, no radio.
    # ⚠️ the handler catches its own exception and renders UNKNOWN, which
    # is why classify() reads dialog TEXT — a title-only walk scored that
    # screen clean.
    ("dashboard", "delivery"):
        "Delivery — renders delivery_view.gather(): published json + "
        "systemctl show / is-enabled. Read-only.",
    ("dashboard", "delivery_legs"):
        "Delivery legs (BIC-2) — render_route_legs() over the gateway's "
        "published legs snapshot in a textbox. Read-only.",

    # ---- diagnostics batch — read 2026-10-30.
    ("system", "db_health"):
        "handlers/db_audit.py (87 lines) loads scripts/db_audit.py by path "
        "and calls audit_db() per utils.db_inventory spec: sqlite3 "
        "'file:…?mode=ro' URI (no journal_mode conversion, no PRAGMA "
        "writes) + stat. The same audit CLAUDE.md has run by hand for months.",
    ("rf_sdr", "sdr_watch"):
        "handlers/sdr_watch.py (53) -> utils.sdr_view load/render (541): "
        "json/stat reads of the meshforge-sdr timer's JSONL. No subprocess, "
        "no USB reset (those live in the writer, not here).",
    ("rns", "rnode_interference"):
        "handlers/rns_rnode_interference.py (62) -> "
        "utils.rnode_interference.read_all (274): ONE subprocess, `rnstatus "
        "-j` (argument list, timeout) — an RPC read of the shared rnsd the "
        "watchdog already makes every tick; never opens the serial port and "
        "never runs rnodeconf (its -x/-X is quoted as advice text only).",
    ("meshtasticd", "txpower_truth"):
        "handlers/meshtasticd_txpower.py (76) -> utils.radio_txpower_truth "
        "(527): `systemctl show` + `journalctl -u meshtasticd` (argument "
        "lists) + a prefs-file read. Never opens a radio session (#17); its "
        "own tests carry an AST no-write guard. ⚠️ first read scans the whole "
        "journal — may exceed the walk timeout on a small box.",

    # ---- dashboard.py (740 lines) — read 2026-10-30. Dispatch map at
    # execute(); every leg below traced to its terminal call.
    ("dashboard", "status"):
        "Service Status — check_service()/env_state, a 2 s TCP probe of the "
        "meshtasticd API port, a config.yaml read; then the shared fix "
        "chooser (service_remediation.offer_service_fix_chooser), which is "
        "a MENU first — the stand-in cancels it, so no fix is ever run.",
    ("dashboard", "weather"):
        "Space Weather — commands.propagation.get_space_weather(): outbound "
        "HTTP GET to the NOAA feeds; no cache write, no local state.",
    ("dashboard", "nodes"):
        "Node Count — utils.node_counts: urllib GET of the local map's "
        "http://127.0.0.1:5000/api/status. No radio session.",
    ("dashboard", "reports"):
        "Reports — a menu loop; the stand-in's cancel breaks it before any "
        "report is generated or written.",
    ("dashboard", "alerts"):
        "View Alerts — get_alert_engine() constructs MeshAlertEngine (its "
        "disconnect thread is declared, not started, in __init__) and reads "
        "current alerts.",
    # ---- platform_posture.py (338) — read 2026-10-30.
    ("system", "platform_posture"):
        "Platform Posture — docs/fleet_platform.yaml + declarations, "
        "/etc/os-release, platform.python_version(). Local box only, no ssh "
        "fan-out (by design, see the handler).",
    ("system", "platform_pins"):
        "Dependency Pins — the catalog plus `apt-mark showhold` (argument "
        "list, timeout). Reads.",
    ("system", "platform_updates"):
        "Update Readiness — `dpkg-query -W`, `apt-get -s upgrade` (-s = "
        "SIMULATE, changes nothing), `apt-mark showhold`. ⚠️ apt-get -s may "
        "take up to its 60 s timeout, above the walk's 45 s default.",

    # ---- rns_diagnostics.py (833) + _rns_diagnostics_engine.py (679) —
    # read 2026-10-30. Every mutation in both files (stop/start rnsd, pkill,
    # create_identities, _fix_rnsd_user, the repair wizard, the drift
    # migration) sits behind a yesno/menu the stand-in declines; the
    # unprompted legs are check_service, pgrep, rnstatus, get_status /
    # check_connectivity (commands.rns: pgrep + service_check, no
    # RNS.Reticulum construction) and utils.config_drift.
    ("rns", "diag"):
        "RNS Diagnostics — run_rns_diagnostics(): read legs only until a "
        "yesno/menu; every fix is behind one (the stand-in declines).",
    ("rns", "drift"):
        "Config Drift Check — detect_rnsd_config_drift() reads; the fix "
        "offer (_offer_drift_fix) prompts before migrating or restarting.",

    # ---- configuration/ — all 9 read 2026-10-30 to the FIRST prompt. Every
    # one opens with a menu (or the wizard's yesno) the stand-in cancels;
    # every writer, editor hand-off, update and radio command sits behind
    # it. DEPTH IS ONE LEVEL: the menus' contents (editbox hand-offs, apt,
    # channel writes) are NOT proven here.
    ("configuration", "meshforge"):
        "settings.py _settings_menu: a menu first; connection/propagation/"
        "loglevel/profile writers are behind it.",
    ("configuration", "webhooks"):
        "webhooks.py _webhooks_menu: a menu first; add/remove/toggle/test "
        "(the test sends HTTP) are behind it.",
    ("configuration", "config-api"):
        "config_api.py _config_api_menu: a 0.5 s TCP connect probe of "
        "127.0.0.1:8081 for the status line, then a menu; start/stop behind "
        "it. Its on_startup() auto-start runs only from "
        "HandlerRegistry.startup_all (handler_registry.py:571), never from "
        "register() — the walk child only registers.",
    ("configuration", "rnode"):
        "rnode.py _rnode_menu: a menu first; detect/deep-scan (serial "
        "probe) behind it.",
    ("configuration", "backup"):
        "device_backup.py _device_backup_menu: a menu first; create/full/"
        "restore behind it.",
    ("configuration", "channels"):
        "channel_config.py: commands.meshtastic.ensure_connection() BEFORE "
        "the menu — a 1 s bare TCP connect_ex/close on 127.0.0.1:4403 (no "
        "protocol, no PhoneAPI handshake beyond accept; same class as "
        "check_port) or a /dev glob; set_connection() writes module "
        "globals only, nothing persisted. Then a menu; every channel write "
        "behind it.",
    ("configuration", "meshtasticd"):
        "meshtasticd_config.py _meshtasticd_menu: menu_choices() is a pure "
        "list, then a menu; service/config/editor actions behind it.",
    ("configuration", "updates"):
        "updates.py _updates_menu: version-checker import check, then a "
        "menu; every apt/pip/git update behind it.",
    ("configuration", "wizard"):
        "first_run.py _run_first_run_wizard: yesno 'run the wizard?' — the "
        "stand-in declines — then yesno 'skip and mark complete?', declined "
        "too, so _mark_setup_complete() never runs. ⚠️ safe ONLY because "
        "the stand-in declines both; a yes would write the setup marker.",

    # ---- system/ — read 2026-10-30, each to its FIRST prompt.
    ("system", "diagnose"):
        "diagnostics.py: runs src/cli/diagnose.py (timeout 30): systemctl "
        "is-active, lsof -i, `meshtastic --version` (opens no device), "
        "rnstatus, pgrep, df, ip addr, rtl_test -t, a journal read. Reads.",
    ("system", "run"):
        "config_doctor.py _run_and_render: the _config_doctor_checks module "
        "(789 lines, no subprocess/write/sudo/radio session) — check_service, "
        "unit-file lookup, RNS shared-instance socket check, config reads. "
        "'Read-only — no fixes applied' is its own banner.",
    ("system", "details"):
        "config_doctor.py _details_menu: the last results (or none), then a "
        "menu the stand-in cancels.",
    ("system", "daemon"):
        "daemon.py _daemon_menu: reads /run/meshforge/meshforged.pid and "
        "os.kill(pid, 0) — signal 0 is an existence check, sends nothing — "
        "then a menu; start/stop behind it.",
    ("system", "logs"):
        "logs.py _logs_menu: a menu first; live follows / journal reads "
        "behind it.",
    ("system", "hardware"):
        "hardware.py _hardware_menu: a menu first; detect / RNode probe / "
        "Enable SPI (a config write) behind it.",
    ("system", "network"):
        "network_tools.py _network_menu: a menu first; traceroute / ss / "
        "ping behind it.",
    ("system", "review"):
        "auto_review.py _auto_review_menu: a menu first; the code-review "
        "agents behind it.",
    ("system", "discover"):
        "service_discovery.py: a menu first; local/network/USB scans "
        "behind it.",
    ("system", "starlink_status"):
        "starlink.py -> utils.starlink_dish.get_dish_status: ONE gRPC "
        "request, get_status (code 1004), to the dish. The module builds no "
        "reboot/stow request — only 1004 and 2008 exist in it.",
    ("system", "starlink_skymap"):
        "starlink.py -> get_obstruction_map: request code 2008 "
        "(dish_get_obstruction_map), read-only.",

    # ---- rf_sdr/ — read 2026-09-16. handlers/rf_tools.py (565 lines) and
    # handlers/site_planner.py (294) contain ZERO subprocess, file-write,
    # network or sqlite calls, and their imports were followed by hand
    # (including the function-local ones, which is where the
    # meshtasticd_radio counterexample hid): utils/rf.py (914 lines),
    # utils/preset_impact.py (531) and utils/antenna_patterns.py (651) are
    # likewise free of every one of those markers. 2,096 lines of RF maths.
    ("rf_sdr", "link"):
        "Link Budget — a menu over FSPL / Fresnel / link-budget calculators "
        "in utils.rf. Arithmetic and a msgbox; the dialog stand-in cancels "
        "the submenu, so it returns immediately.",
    ("rf_sdr", "freq"):
        "Frequency-slot calculator. Pure arithmetic over region tables.",
    ("rf_sdr", "antenna"):
        "Antenna comparison from the static table in utils.antenna_patterns, "
        "reached through safe_import so an absent module degrades in-app "
        "rather than raising.",
    ("rf_sdr", "site"):
        "Site Planner — coverage estimation over utils.rf and "
        "utils.preset_impact. Reads DeployEnvironment/BuildingType tables "
        "and computes; writes nothing.",
}


#: (section, tag) -> why this action was READ and is deliberately NOT
#: dispatched (e.g. it acts before its first prompt, so "cancel the first
#: prompt" is not a safe stand-in). Reported, never a pass. A new action
#: goes here or in ALLOWLIST — tests/test_tui_leaf_walk_gate.py fails on an
#: action in neither.
NOT_WALKED: dict[tuple[str, str], str] = {
    ("system", "status"):
        "Quick Status runs src/cli/status.py, which (when :4403 answers) runs "
        "`meshtastic --host localhost --info` and `--nodes` before any prompt "
        "— a TCP radio session on meshtasticd, the #17/#75 class. Read "
        "2026-10-30 (it was a BARE CLI that could open the RNode until "
        "99c4a15a).",
    ("system", "shell"):
        "Linux Shell runs an interactive $SHELL with no timeout after its "
        "msgbox — it would block the walk on the child's stdin. Read "
        "2026-10-30.",
    ("dashboard", "datapath"):
        "Data Path Check runs `meshtastic --host localhost --info` and "
        "MapDataCollector.collect() BEFORE any prompt: a radio session on "
        "meshtasticd, the #17/#75 class that starves PhoneAPI (:9443 deaf). "
        "Not a mutation, but not a session a drill may open on a live box. "
        "(Read 2026-10-30; whether a diagnostic should open it at all is a "
        "separate finding.)",
}

#: The 10-30 baseline of actions NO ONE has read for this walk. CLOSED and
#: SHRINK-ONLY (the MF025 shape): move an entry out by reading its handler
#: into ALLOWLIST or NOT_WALKED, then delete it here. Never add to it — the
#: gate test pins the count and rejects new entries.
LEGACY_UNPROVEN: frozenset = frozenset({
    ('about', 'web'),
    ('dashboard', 'analytics'),
    ('dashboard', 'demo'),
    ('dashboard', 'health'),
    ('dashboard', 'latency'),
    ('dashboard', 'mini_dudeai'),
    ('dashboard', 'mini_dudeai_chat'),
    ('dashboard', 'mini_dudeai_rules'),
    ('dashboard', 'moc_analysis'),
    ('dashboard', 'offline_oracle'),
    ('dashboard', 'stack_health'),
    ('dashboard', 'traffic_pulse'),
    ('extensions', 'meshing'),
    ('fleet', 'fleet_backup'),
    ('fleet', 'fleet_membership'),
    ('fleet', 'fleet_posture'),
    ('fleet', 'fleet_provision'),
    ('fleet', 'fleet_watchers'),
    ('fleet', 'noc_grants'),
    ('main', 'e'),
    ('main', 'n'),
    ('main', 'q'),
    ('main', 't'),
    ('maps_viz', 'ai'),
    ('maps_viz', 'coverage'),
    ('maps_viz', 'export'),
    ('maps_viz', 'heatmap'),
    ('maps_viz', 'livemap'),
    ('maps_viz', 'mfmaps'),
    ('maps_viz', 'quality'),
    ('maps_viz', 'tiles'),
    ('maps_viz', 'topology'),
    ('maps_viz', 'traffic'),
    ('mesh_networks', 'aredn'),
    ('mesh_networks', 'automation'),
    ('mesh_networks', 'broker-menu'),
    ('mesh_networks', 'check'),
    ('mesh_networks', 'dual_failover'),
    ('mesh_networks', 'export'),
    ('mesh_networks', 'favorites'),
    ('mesh_networks', 'gateway'),
    ('mesh_networks', 'ham'),
    ('mesh_networks', 'load_balancer'),
    ('mesh_networks', 'mesh_alerts'),
    ('mesh_networks', 'meshcore'),
    ('mesh_networks', 'meshtastic'),
    ('mesh_networks', 'messaging'),
    ('mesh_networks', 'mqtt'),
    ('mesh_networks', 'nomadnet'),
    ('mesh_networks', 'rns'),
    ('mesh_networks', 'services'),
    ('mesh_networks', 'test_gateway_rx'),
    ('mesh_networks', 'wizard'),
    ('meshtasticd', 'channel_load'),
    ('meshtasticd', 'cleanup'),
    ('meshtasticd', 'hardware'),
    ('meshtasticd', 'lora'),
    ('meshtasticd', 'mqtt'),
    ('meshtasticd', 'owner'),
    ('meshtasticd', 'presets'),
    ('rf_sdr', 'sdr'),
    ('rf_sdr', 'vna'),
    ('rf_sdr', 'weather'),
    ('rns', 'check'),
    ('rns', 'config'),
    ('rns', 'edit'),
    ('rns', 'ifaces'),
    ('rns', 'logging'),
    ('rns', 'monitor'),
    ('rns', 'repair'),
    ('rns', 'sniffer'),
    ('rns', 'tools'),
})



def _load_registry(profile_name=None):
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(TUI))
    import logging
    logging.disable(logging.INFO)
    from handlers.manifest import HANDLER_MANIFEST
    return HANDLER_MANIFEST


def partition(actions):
    """Split actions into the five buckets. DENYLIST wins over ALLOWLIST;
    ``unclassified`` is anything in NO set — never folded into legacy
    (review 10-30 #6: that would hide exactly what the gate forbids)."""
    b = {"allowed": [], "denied": [], "not_walked": [], "legacy": [],
         "unclassified": []}
    for s, t, d in actions:
        k = (s, t)
        if k in DENYLIST:
            b["denied"].append((s, t, d))
        elif k in ALLOWLIST:
            b["allowed"].append((s, t, d))
        elif k in NOT_WALKED:
            b["not_walked"].append((s, t, d))
        elif k in LEGACY_UNPROVEN:
            b["legacy"].append((s, t, d))
        else:
            b["unclassified"].append((s, t, d))
    return b


def all_actions():
    """Every (section, tag, label) the TUI can dispatch, from the manifest."""
    out = []
    for h in _load_registry():
        for tag, desc, _flag in h["menu_items"]:
            out.append((h["menu_section"], tag, desc.strip()))
    return sorted(out)


# ------------------------------------------------------------- the child

CHILD = r'''
import json, logging, sys, os
sys.path.insert(0, %(src)r)
sys.path.insert(0, %(tui)r)
logging.disable(logging.CRITICAL)
from types import SimpleNamespace
from handler_protocol import TUIContext
from handler_registry import HandlerRegistry
from handlers import get_all_handlers

section, tag = %(section)r, %(tag)r
seen = {"dialogs": [], "errors": []}

class ScriptedDialog:
    """Answers every prompt with cancel/back and types nothing.

    Records what it was asked so the parent can tell a leaf that chose
    to explain itself from one that safe_call had to rescue.
    """
    def _note(self, kind, title, text=""):
        # TEXT too, not just the title: a handler that catches its own
        # exception renders it as a polite screen (classify() reads this).
        seen["dialogs"].append([kind, title, str(text or "")])
        if kind == "msgbox" and title in %(errtitles)r:
            seen["errors"].append(title)
    def set_status_bar(self, *a, **k): return None
    def available(self): return True
    def msgbox(self, title, text, height=None, width=None):
        self._note("msgbox", title, text); return None
    def infobox(self, title, text): self._note("infobox", title, text); return None
    def textbox(self, title, text, height=None, width=None):
        self._note("textbox", title, text); return None
    def editbox(self, title, file_path, height=None, width=None):
        self._note("editbox", title); return None
    def yesno(self, title, text, default_no=False, height=None, width=None):
        self._note("yesno", title, text); return False    # always decline
    def menu(self, title, text, choices, height=None, width=None,
             list_height=None):
        self._note("menu", title, text); return None            # always cancel
    def inputbox(self, title, text, init="", height=None, width=None):
        self._note("inputbox", title, text); return None
    def checklist(self, title, text, choices, height=None, width=None,
                  list_height=None):
        self._note("checklist", title, text); return None
    def radiolist(self, title, text, choices, height=None, width=None,
                  list_height=None):
        self._note("radiolist", title, text); return None
    def gauge(self, *a, **k): self._note("gauge", a[0] if a else ""); return None

ctx = TUIContext(dialog=ScriptedDialog())
ctx.wait_for_enter = lambda msg="": None   # never block on stdin
registry = HandlerRegistry(ctx)
for cls in get_all_handlers():
    registry.register(cls())
ctx.registry = registry

import io, contextlib
_buf = io.StringIO()
# print()-based screens, and their stderr. ⚠️ a subprocess writing straight
# to the inherited fd is NOT captured here (stated blind spot).
with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
    owned = registry.dispatch(section, tag)
print("__RESULT__" + json.dumps(
    {"owned": bool(owned), "dialogs": seen["dialogs"],
     "errors": seen["errors"], "stdout": _buf.getvalue()}))
'''


def walk_one(section, tag, timeout=90):
    src = CHILD % {"src": str(SRC), "tui": str(TUI), "section": section,
                   "tag": tag, "errtitles": sorted(SAFE_CALL_ERROR_TITLES)}
    try:
        # stdin closed: a leaf that calls input() (diagnose.py's "Press
        # Enter") gets EOF instead of blocking until the timeout (10-30).
        p = subprocess.run([sys.executable, "-c", src], cwd=str(REPO),
                           capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "note": f"DID NOT RETURN within {timeout}s "
                                     f"— a leaf that hangs is the worst kind "
                                     f"of not-returning to its parent"}
    r = extract_result(p.stdout)
    if r is None:
        tail = (p.stderr or p.stdout).strip().splitlines()
        return {"ok": False, "note": "child produced no result: "
                                     + (tail[-1] if tail else "no output")}
    return classify(r)


def extract_result(stdout):
    """The child's __RESULT__ json, wherever it sits on its line — a leaf
    that printed a prompt without a newline glues it on (10-30)."""
    for ln in (stdout or "").splitlines():
        i = ln.find("__RESULT__")
        if i >= 0:
            return json.loads(ln[i + len("__RESULT__"):])
    return None


#: A screen admitting it could not see. ``safe_call`` is not the only
#: rescuer — handlers such as delivery.py catch their own exception and
#: render "UNKNOWN … KeyError: x", then return normally. Upper-case tokens
#: only (UNKNOWN, UNOBSERVABLE): the screens' contract words; prose
#: "unknowns" is not one.
_SAID_UNKNOWN = re.compile(
    r"\bUNKNOWN\b|\bUNOBSERVABLE\b|Traceback \(most recent call last\)"
    r"|\b[A-Z]\w*(?:Error|Exception): ")
_EXC_LINE = re.compile(r"Traceback|\b[A-Z]\w*(?:Error|Exception): ")


def classify(r):
    """``ok`` | ``said-unknown`` | ``fail`` for one child result.

    said-unknown is honest (the screen told the operator it could not see)
    so it is not a failure (exit 1) — but it is not a pass either: exit 2,
    and the note quotes the line that said so."""
    if not r.get("owned"):
        return {"status": "fail", "ok": False,
                "note": "no handler owns this tag"}
    if r.get("errors"):
        return {"status": "fail", "ok": False,
                "note": "safe_call caught an exception and "
                        f"showed: {', '.join(r['errors'])}"}
    dialogs = r.get("dialogs") or []
    hits = [(d[1], ln.strip()) for d in dialogs
            for ln in (d[2] if len(d) > 2 else "").splitlines()
            if _SAID_UNKNOWN.search(ln)]
    # print()-based screens (Drift Check, Service Status) say it on stdout.
    hits += [("stdout", ln.strip())
             for ln in (r.get("stdout") or "").splitlines()
             if _SAID_UNKNOWN.search(ln)]
    if hits:
        # Quote the exception line when there is one — "KeyError: 'legs'"
        # is the witness; the "— UNKNOWN" heading above it is only a label.
        title, ln = next((h for h in hits if _EXC_LINE.search(h[1])), hits[0])
        return {"status": "said-unknown", "ok": False,
                "note": f"[{title}] said: {ln[:90]}"}
    return {"status": "ok", "ok": True,
            "note": f"{len(dialogs)} dialog(s), returned clean"}


def counts_as_clean(status):
    return status == "ok"


def is_failure(status):
    return status == "fail"


def exit_code(failures, said_unknown, allowed):
    """0 PASS · 1 a leaf failed · 2 UNKNOWN (empty allowlist, or a screen
    that said it could not see) — exit 2 is never a pass."""
    if failures:
        return 1
    if not allowed or said_unknown:
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="show allowed vs unproven and exit")
    ap.add_argument("--only", default=None, help="one tag")
    # 90 s: apt-get -s (60 s cap) and the first journal scan of TX Power
    # Truth exceed 45 s on a small Pi (review 10-30 #4).
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args(argv)

    actions = all_actions()
    if args.only:
        actions = [a for a in actions if a[1] == args.only]
        if not actions:
            print(f"UNKNOWN: no action tagged {args.only!r}", file=sys.stderr)
            return 2

    # DENYLIST wins, always — an entry in both is a mistake worth naming
    # rather than silently resolving in the permissive direction.
    both = sorted(set(ALLOWLIST) & set(DENYLIST))
    for s_, t_ in both:
        print(f"REFUSING {s_}/{t_}: it is in DENYLIST and ALLOWLIST both. "
              f"DENYLIST wins. ({DENYLIST[(s_, t_)]})", file=sys.stderr)
    b = partition(actions)
    allowed = b["allowed"]
    if b["unclassified"]:
        print(f"UNCLASSIFIED ({len(b['unclassified'])}) — in no set; "
              f"tests/test_tui_leaf_walk_gate.py fails until each is read "
              f"and placed:", file=sys.stderr)
        for s, t, d in b["unclassified"]:
            print(f"  {s}/{t:<18} {d[:46]}", file=sys.stderr)

    if args.list:
        print(f"allowlisted ({len(allowed)}):")
        for s, t, d in allowed:
            print(f"  {s}/{t}\n      {ALLOWLIST[(s, t)]}")
        print(f"\nNEVER dispatched by rule ({len(b['denied'])}):")
        for s, t, _d in b["denied"]:
            print(f"  {s}/{t}\n      {DENYLIST[(s, t)]}")
        print(f"\nREAD, NOT WALKED — deliberately not dispatched "
              f"({len(b['not_walked'])}):")
        for s, t, _d in b["not_walked"]:
            print(f"  {s}/{t}\n      {NOT_WALKED[(s, t)]}")
        print(f"\nUNPROVEN — never read, not dispatched, not a pass "
              f"({len(b['legacy'])}; LEGACY_UNPROVEN, shrink-only):")
        for s, t, d in b["legacy"]:
            print(f"  {s}/{t:<18} {d[:46]}")
        return 0

    print(f"tui_leaf_walk — dispatching {len(allowed)} of {len(actions)} "
          f"action(s); not walked {len(b['not_walked'])}, denied "
          f"{len(b['denied'])}, never read {len(b['legacy'])}, unclassified "
          f"{len(b['unclassified'])}\n")
    failures = clean = said_unknown = 0
    for s, t, _d in allowed:
        r = walk_one(s, t, args.timeout)
        st = r.get("status", "fail")   # a timeout/no-result has no status
        failures += is_failure(st)
        clean += counts_as_clean(st)
        said_unknown += st == "said-unknown"
        mark = {"ok": "ok  ", "said-unknown": "UNKN"}.get(st, "FAIL")
        print(f"  {mark} {s}/{t:<18} {r['note']}")

    print(f"\nreturned to parent clean: {clean}/{len(allowed)} dispatched")
    if said_unknown:
        print(f"SAID UNKNOWN: {said_unknown} — returned, but the screen "
              f"admitted it could not see. Honest, not a pass; read each "
              f"UNKN line (is it blind by design on this box, or broken?)")
    unwalked = len(actions) - len(allowed)
    print(f"NOT WALKED: {unwalked}/{len(actions)} (never read "
          f"{len(b['legacy'])}, read-not-walked {len(b['not_walked'])}, "
          f"denied {len(b['denied'])}, unclassified "
          f"{len(b['unclassified'])}) — NOT counted as passing")
    print("\nDEPTH — what this number does NOT say: the stand-in answers the\n"
          "  FIRST prompt with cancel, so a leaf that opens a submenu is\n"
          "  proven to open it and come back, not that anything INSIDE it\n"
          "  works. A crash behind submenu item 3 is untouched here. Walking\n"
          "  deeper means dispatching sub-actions, which is where the\n"
          "  mutating ones live — so depth is earned per tree, the same way\n"
          "  this allowlist is, and is not yet claimed for any of them.")
    rc = exit_code(failures, said_unknown, len(allowed))
    if failures:
        print(f"\nFAIL — {failures} allowlisted leaf/leaves did not come back "
              f"clean")
    elif not allowed:
        print("\nUNKNOWN: the allowlist is empty — nothing was proven. "
              "That is the honest state, not a pass.")
    elif said_unknown:
        print(f"\nUNKNOWN — no leaf failed, but {said_unknown} screen(s) "
              f"said they could not see. Not a pass (exit 2).")
    else:
        print("\nPASS — every allowlisted leaf returned to its parent")
    return rc


if __name__ == "__main__":
    sys.exit(main())
