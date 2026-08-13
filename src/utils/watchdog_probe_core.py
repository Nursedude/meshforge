"""Watchdog probe core — Signal type, the closed signal-class enum, and
low-level helpers shared by 2+ probe modules.

Split out of ``watchdog_probes.py`` 2026-06-09 (file had reached 3,143
lines vs the 1,500-line rule). ``utils/watchdog_probes.py`` remains the
import hub — external code (runner, tests, mini preset) imports from THERE,
never from the split modules directly. See the hub docstring for the probe
design constraints (pure / bounded / read-only / honest-about-indeterminacy).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# First-party, imported DIRECTLY (never a swallowed try/except — a rename
# would silently turn every role-evidence answer into permanent "crontab
# unreadable" with no witness; 2026-07-28 review, honest_failure_modes #9).
from utils.fleet_snapshot import _parse_crontab

# Same "watchdog" namespace the runner logs under, so a swallowed state-write
# failure lands where the operator already greps (honest_failure_modes #9).
logger = logging.getLogger("watchdog")

# Consecutive write failures per state path (#63 witness pattern): ERROR on
# the first failure, debug while it persists, INFO with the count on recovery
# — a broken state dir must neither spam every tick nor go silent forever.
_streak_write_errors: dict = {}
# Last streak this process wrote, per state path. Lets _load_parity_streak
# HOLD a debounce across an unwritable-disk window instead of resetting to 0
# every tick, which silenced every debounced probe forever (found 07-21,
# witness-only fix; suppression closed 07-26).
_streak_mem_fallback: dict = {}

# ─────────────────────────────────────────────────────────────────────
# Closed enum of failure classes — one per persistent_issues.md entry.
# Each class maps to a probe function below. To add a class, add it
# here AND add a row to persistent_issues.md.
#
# ⚠️ TWO long-running processes cache this tuple at startup, and the coverage
# view is only coherent while they AGREE: meshforge-watchdog (the PRODUCER of
# the per-class dispositions) and meshforge-map (the CONSUMER serving
# /api/fleet/truth, whose copy is "what the server knows"). They go stale in
# OPPOSITE directions — a GROWING enum leaves the map behind, a SHRINKING one
# leaves the watchdog behind — and either way the disagreement surfaces as
# `unknown_to_server` / `server_class_skew` and turns the fleet verdict DARK
# with every box healthy (observed 2026-08-08, the first time the enum ever
# shrank). Editing this tuple therefore requires BOTH to be restarted, never
# one: locally the .githooks/post-commit hook does it, on every other box it is
# `fleet_pull.sh` + a watchdog restart. Pinned by
# TestPostCommitRefreshesBothEnumHolders.
# ─────────────────────────────────────────────────────────────────────

SIGNAL_CLASSES = (
    "rns_namespace_collision",        # Issue #69
    "main_thread_wedge",              # Issue #68 (renamed kept for backwards compat)
    "http_local_unresponsive",        # general; catches #61 socketserver-deadlock
    "delivery_write_canary",          # Issue #63
    "service_inactive",               # general; "should be running but isn't"
    "tracer_peer_unreachable",        # today's symptom; per-peer recurring no-route
    "rns_shared_instance_unresponsive",  # 2026-05-21: rnsd shared-instance hung
    "rns_interface_down_peer_reachable",  # 2026-05-30: stuck TCPInterface Down, peer reachable
    "rns_rpc_unresponsive",  # 2026-05-30: rnsd RPC wedged — rnstatus hangs though socket accepts (#68/#69)
    "fd_exhaustion",  # Issue #73 (2026-05-31): open fds approaching soft RLIMIT_NOFILE — fires BEFORE the wedge
    "foundation_perms_drift",  # 2026-06-01: born-correct permission foundation drifted (mf.4/#73) — non-root rnsd can't write its RNS tree
    "parity_drift",  # 2026-06-01: MeshForge<->MeshAnchor RNS-reliability parity diverged (lead-repo port debt)
    "rns_version_drift",  # 2026-06-01: rns/lxmf installed off the pinned +mf.N fork version (T2-isolate arc)
    "role_drift",  # 2026-06-03: live systemd unit state diverges from the box's effective declared role (fleet_roles.yaml + deployment.json overrides)
    "channel_feed_dark",  # 2026-06-04: no decoded text on a watched Meshtastic channel — the .32 dark-feed / PSK-rotation-canary lesson (silence is the failure mode)
    "queue_backlog",  # Issue #74 (2026-06-06): persistent-queue depth near shed threshold / dead-letter growth — backlog masks delivery failures
    "delivery_confirmation_stall",  # Issue #74 (2026-06-06): sends flow but confirmations collapsed — bridge self-report reads HEALTHY while shouting into a void
    "phoneapi_tcp_leak",  # Issue #75 (2026-06-07): map service holds an unaccounted persistent TCP to meshtasticd :4403 — leaked TCPInterface silently starves the :9443 web client (#17 contention class, leak form)
    "meshtasticd_phoneapi_wedge",  # 2026-06-15: ≥2 contending single-consumers thrash meshtasticd's PhoneAPI :4403 (journal 'Force close previous TCP connection' churn) — the gateway's stateless-HTTP-protobuf mesh-TX wedges, bot output stops reaching nodes while the RNS round-trip canary stays green (the 2026-06-13→15 moc incident; #17/#75 contention class, churn form)
    "mqtt_root_drift",  # Issue #77 (2026-06-07): radio's observed MQTT publish root prefix diverges from the box's declared mqtt_bridge.root_topic — a zero-config radio join silently reintroduces the msh/US split
    "cron_verdict_stale",  # Issue #78 (2026-06-08): a cron WIRED to cron_verdict.sh reported FAIL/CONCERN or went silent past its schedule cadence — silence is the failure mode (cross-references the crontab so stale ORPHAN verdicts never false-alarm)
    "history_write_stalled",  # mini-dudeai Issue #79 (2026-06-09): the mini loop is alive (state.json last_tick advancing) but its history/ledger files stopped accumulating — a swallowed-and-printed write failure with no fleet signal
    "rules_seed_drift",  # mini-dudeai Issue #79 (2026-06-09): the live ~/mini_dudeai_rules.json is MISSING rule ids the box-role seed (configs/mini_dudeai_rules.<role>.json) carries — the live file fell behind a seed bump (extra box-local rules are legitimate and ignored)
    "memory_index_oversize",  # mini-dudeai Issue #79 (2026-06-09): the operator memory index (MEMORY.md) is over its ~24 KB context-load limit and silently partial-loads — demote older/shipped entries to MEMORY_ARCHIVE.md
    "kernel_reboot_pending",  # 2026-06-09 version-updates arc: a NEWER same-flavor kernel is installed under /lib/modules than the running one (or /var/run/reboot-required exists) — moc1 ran 6.12.75 for days with 6.18.33 installed and nothing paged
    "aredn_source_dark",  # 2026-06-12 AREDN Phase 0: a box with aredn_node_ips configured whose local sysinfo collection reports unreachable/not_configured — the AREDN organ went blind (or the running service predates the config); found dormant on the AREDN-site box itself. Role-aware leg 2026-07-19 (closes aredn_configured_source_only): a box DECLARING the organ (deployment.json organ_expectations.aredn — per-box overrides layer, fleet_roles.yaml stays instance-free) with EMPTY/absent aredn_node_ips fires subject=declared-unconfigured — the config-wiped site the configured-only legs couldn't see; undeclared boxes stay INERT, unreadable declaration is indeterminate.
    "lxmf_propagation_node_dark",  # 2026-07-20: the CONFIGURED gateway.json rns.propagation_node has gone quiet — either STALE (in the RNS node cache but no announce for several announce periods: it answered and stopped) or UNHEARD (the hash is not in the cache at all, i.e. a wrong/truncated hash, the failure adoption itself introduces). The shape-A companion that had to ship WITH adoption: it keeps an ADOPTED node watched, so the fleet never trades a watched gap for an unwatched dependency. (Its shape-C sibling lxmf_propagation_unused, which watched for an available-but-unadopted node, was removed 2026-08-08 by the signal-class yield audit — INERT on all 8 boxes, a one-time adoption nudge on a 30s loop.) Same durable operator-owned rns_nodes.json evidence (never the journal, Storage=volatile). Guard that makes it honest: it fires ONLY when some OTHER propagation announce reached the box inside the window, which is positive proof the box can hear the class — an RNS-wide wedge therefore holds (its own probes own it) instead of being relabelled as this node's death. INERT when propagation_node is empty (nothing adopted to watch — that gap is knowingly unwatched since 2026-08-08), when no gateway.json exists, and when no cache exists.
    "dep_version_drift",  # 2026-06-12: a critical pip dep (meshtastic) installed BELOW the requirements/core.txt floor in the service user's env — a box that missed/failed an update. rns/lxmf have their own fork-pin probe; this covers the meshtastic-lib gap nothing watched (the recurring update class, feedback_version_env_rigor)
    "dep_install_fragmented",  # 2026-06-17: meshtastic installed at DIVERGENT versions across root-readable locations (venv vs system-wide dist-packages vs root/user pipx vs user-site) with ≥1 copy BELOW the core.txt floor — the service consumer-of-record can be fine while the TUI-as-root reads a stale stray and shows phantom "update available". The install-fragmentation half of the recurring update class; dep_version_drift watches only the service-user consumer and is blind to strays (feedback_version_env_rigor)
    "user_unit_inactive",  # 2026-07-19: an enrolled always-on USER .service is not running — enabled in default.target.wants but no invocation:* marker under /run/user/<uid>/systemd/units (bus-free, root-readable both sides), or the user manager itself is down while daemons are enrolled (linger off — the #79 class). Closes user_unit_inactivity_blind: probe_service_inactive can't see user units, nomadnet_crashloop covers only a LIVE loop on one unit; the parked-failed (StartLimitBurst) / stopped-and-forgotten / manager-down modes had NO steady-state detector. Timers deliberately out of scope (no invocation marker; schedules/SLO layer owns their staleness).
    "user_timer_unit_failing",  # 2026-07-19: an enabled USER *timer*'s job fails on EVERY firing (>=N "Failed with result" in a short window, newest fresh, and no success since) — the last uncovered corner of the user-unit blindness class. probe_service_inactive is blind to user units; nomadnet_crashloop watches one unit's LIVE restart loop; user_unit_inactive judges only always-on default.target.wants daemons via invocation markers and EXPLICITLY excludes timers (no invocation marker, and a oneshot is inactive between firings by design). Found the hard way: kiai's meshforge-tracer.timer fired every 10 min from 2026-07-12 while its oneshot exited 2 ("no peers in lab_peers") every time — a week silent, every existing leg reading it healthy. Bus-free root-readable USER_UNIT= journal read; recency gate so a fixed job stops paging; per-unit unobservable is skipped and all-unobservable holds the streak (journalctl wedge != all healthy); 2-tick debounce. Documented inline (MF012 40k cap precedent); no own issue#.
    "rns_stray_env_drift",  # 2026-07-19: rns/lxmf copies across this box's root-readable envs DISAGREE — intra-box coherence, the missed-venv half of the roll hazard (probe_rns_version_drift owns pin compliance). Pipx globs WILDCARDED across venv names because a library rides inside every app venv depending on it: moc3's nomadnet pipx venv sat silently stock 1.1.4 while the box's consumer ran the fork pin, invisible to every prior drift probe. One shared rnsd per box → every env must carry the identical RNS substrate (RPC framing mismatch = 8s timeouts). Closes the rns/lxmf leg of the dep_version_drift_strays_blind structural-dark row.
    "synth_soak_degraded",  # 2026-06-15: the hourly LXMF synth soak (meshforge-synth-soak.timer) FAILED its delivery envelope or went DARK — the gateway's real round-trip exerciser writes a pass/fail envelope but the fire script always exits 0 and nothing consumed it, so a delivery regression or a silent timer was invisible (the "canary itself unwatched" gap; silence-as-failure for a fixed-cadence generator)
    "propagation_soak_degraded",  # 2026-07-21 propagation arc slice 3: the LXMF store-and-forward drill FAILED its envelope or went DARK. The OUTCOME leg for the propagation organ -- probe_lxmf_propagation_node_dark only watches whether the configured node ANNOUNCES, so a node that announces while silently dropping every stored message reads clean forever. The hourly drill manufactures offline-peer traffic (a receiver that never announced, so no direct path can exist, is sent a PROPAGATED message and then pulls it back) and this probe consumes the envelope. ENVELOPE leg = pass_envelope false; SILENCE leg = newest prop-*.json older than ~2.5 cadences (a fixed-cadence generator going quiet IS the failure). degraded only -- offline-peer delivery is impaired, live delivery is not. INERT where the drill doesn't run. Documented inline (MF012 40k cap). No own issue#.
    "calibration_drift",  # 2026-06-15: a VERIFIED completion claim (Claude's "done/100%/all green") did NOT hold when re-derived against external ground truth — the calibration spine turned on the assistant itself, so "you said 100% and the math was wrong" becomes a tracked number instead of a private impression. SSOT .claude/rules/calibrated_claims.md (NOT a code/fleet bug → no issue#). Self-guards None off the dev/manager box (no ledger).
    "fleet_box_unreachable",  # 2026-06-17 Leg D: a fleet box the offline-monitor (fleet_offline_check.sh on the manager box) has confirmed DOWN past its 3-fail (~15min) threshold and is re-paging — surfaced into mini's brief + /fleet so a dark box can't sit silent in a side-channel logfile (the .32 33h-dark lesson; the monitor owns the ntfy, this is visibility). Self-guards INERT off the manager box (no state file) and on a stale state file (cron_verdict_stale owns the dead-monitor case — fleet_offline_check is verdict-wired). No issue#.
    "claw_device_dark",  # 2026-07-19 structural-dark row 7: a claw EDGE NODE stopped answering while its capture cron kept writing fresh ticks — the device is silent, not the job. Born from dudeclaw-02 draining to 2.41 V and going dark 17.4 h, where the fleet's only words were "cron_verdict_stale: claw02_metrics FAIL — fix the job": a dead battery-powered LoRa/witness node laundered into an infrastructure-noise signal, in the channel known to flap benignly. Reads the tick files the capture already writes (no second NATS poll — one poller, one threshold set, honest_failure_modes #5; MF021 observation-only). INERT with no claw on the box; stale/unparseable ticks are indeterminate (cron_verdict_stale owns the dead-capture page); 2-tick debounce. No issue#.
    "claw_battery_low",  # 2026-07-19 structural-dark row 7: a battery-powered claw's pack fell below the 3.5 V LiPo working floor — the WARNING that was missing while dudeclaw-02 sat ~38 h under floor before dying. The existing battery_v spec was bound to dudeclaw-01, which lives on USB at 4.06 V and can never breach it (the alarm pointed at the wrong device). Fires only on a concrete voltage from a REACHABLE claw; no gauge / pre-battery capture is indeterminate — unknown is NOT charged; an unreachable device belongs to claw_device_dark. 2-tick debounce. No issue#.
    "claw_rf_silent",  # 2026-07-19 structural-dark row 9: NO LoRa traffic heard over the air by any claw for the quiet window — the fleet's only INDEPENDENT physical-layer witness. Every other mesh-RF check is a box talking about itself: the gateway can read healthy (RNS canary green, service active, queue draining) while nothing leaves the antenna (deaf radio, wrong region/preset, dead PA, unplugged coax). The claws answer lora_stats from a SEPARATE radio on SEPARATE silicon, which no box can fabricate about itself. Fires only when EVERY reachable claw reporting a reading is silent (one deaf claw is that claw's problem; all of them is the channel). ⚠️ ESCALATE-ONLY, threshold PROVISIONAL — the operator staged it pending a heard-rate soak across overnight lulls and that data did not exist until this capture shipped; promote to a pager only from measured quiet-hours data (the calibration_drift 34-day precedent). No issue#.
    "segment_peer_silent",  # 2026-07-30: a declared same-SEGMENT peer gateway has not been heard by this box for its full listening window. The peer-witness twin of claw_watched_node_silent, for the boxes that probe structurally cannot serve: the dude-claws listen on LONG_FAST/ch20, and this fleet is deliberately two-preset, so moc2+moc3 on SHORT_TURBO/ch8 (the throughput leg for ST<>meshforge<>RNS) are undemodulable by every claw and had NO RF witness at all — the exact blind spot the ears exist to close, left open on the pair carrying the RNS leg. The witness already existed and was simply never read: those two boxes hear EACH OTHER (measured -17 dBm), and a box reporting what it heard from a DIFFERENT box is independent evidence in the way self-report is not. Source is the local meshtasticd JOURNAL, deliberately: nodes.proto on disk was measured 54.7 DAYS stale while the peer was being received continuously, and a gateway-only box's node_cache.json was 95 days stale — a stale file that looks like live state is the worst possible input to a silence detector. Journal-only also never touches the radio, so it cannot steal a PhoneAPI packet (#17, the mqtt_root_drift precedent). Scans INCREMENTALLY (a 30 min window per tick folded into a persisted last-heard) rather than re-reading the full silence window, because a repeated multi-hour journal scan is not a rounding error on a 905 MB Pi. Verdicts come from classify_watch — the SAME gate the claws use, never a second copy (honest_failure_modes #5). INERT unless the box declares peers in ~/.config/meshforge/rf_segment_peers.json; a config that exists but is unreadable/empty is LOUD, never an empty peer set. ⚠️ ESCALATE-ONLY, window UNMEASURED, same precedent as its claw twin. No issue#.
    "claw_watched_node_silent",  # 2026-07-29: a WATCHED transmitter (one of OUR fleet radios, by node id) has not reached any claw for its full listening window. This is the MUTE-TRANSMITTER case claw_rf_silent is structurally blind to: that probe reads heard_age_s, the age of the last packet from ANYONE, so with neighbours at 6-8 pkt/min the channel reads healthy (~5 s) while our own PA is dead, region/preset wrong, or coax unplugged. Per-node verdicts make a specific radio's silence visible against that busy backdrop — the per-source egress evidence the row-9 residual named as missing. Fires ONLY on the gated `silent` verdict (claw listened longer than the node's expected transmit interval, default 3x3 h, and heard nothing), NEVER on raw `never`: seconds after the watch field shipped, 3 of 4 watched radios read `never` purely because the claw had been up 10 s, so firing on that would page on every claw reboot/flash/power-cycle. HEARD by any claw settles it (positive physical evidence outranks silence); SILENT needs one qualified claw and no claw that heard it (deliberately NOT unanimity — one rebooted claw at `unobservable` must not mask a qualified finding); `unobservable` keeps its own column and is never folded into healthy or silent. ⚠️ ESCALATE-ONLY, window UNMEASURED — no real `silent` verdict has ever been observed on this fleet, so promote to a pager only from measured data (the claw_rf_silent / calibration_drift 34-day precedent). Reads watch_verdicts off the tick the capture already writes; the gate is NOT re-derived here (two copies of one threshold drift, honest_failure_modes #5). No issue#.
    "gateway_dual_homed_exposure",  # 2026-07-19 row-8 accept, LEADING indicator: a recipient became reachable from >1 gateway — the PRECONDITION for a cross-gateway duplicate, not a duplicate. Row 8 was accepted-permanent on cost asymmetry (a dup is redundancy; a yield-protocol bug is silence, and emergency comms must fail toward redundancy), NOT on dups being rare — three human recipients were already dual-homed the day it was accepted. So the fleet instruments the CONDITION (always countable, moves first) instead of only the OUTCOME (rare, bursty, unobservable on the mesh leg). Fires on a NEWLY-observed dual-homed recipient, never on the count (which churns with the rollup window); once known a recipient stays known, so it cannot re-fire on churn. ⚠️ Derived from the CONFIRMED set, so it measures exposure in the CONFIRMABLE population only — mesh recipients never confirm and never appear; extending to attempted/routing state is gateway-side and remains the residual. No issue#.
    "host_frozen",  # 2026-06-17 Leg C: the dude-claw out-of-band witness (host_probe tool over NATS, on the watched box's own subnet) reports a target's verdict — HOST_FROZEN (IP stack answers but the app port serves no banner = kernel alive / userspace swap-wedged, the .32 class the self-petted HW watchdog can't catch), UNREACHABLE (no TCP answer = host/path/SoC down), or UNKNOWN (the claw witness itself couldn't be reached, sustained → lost visibility, not "healthy"). An out-of-band collector cron on the claw's brain box writes the verdict file; this probe reads it (no NATS in the sandboxed watchdog), mirroring fleet_box_unreachable's file-read pattern. Self-guards INERT off the brain box (no verdict file) and on a stale file. Alert-only (propose_escalation). No issue#.
    "ntfy_loopback",  # 2026-06-18 ntfy receipt-heartbeat Phase 2: the alerting spine's OWN liveness. A manager-box collector (scripts/fleet_ntfy_loopback.sh) publishes a nonce'd min-priority heartbeat to the FLEET topic + polls ntfy.sh to confirm it loops back, escalating via the Phase-1 EMAIL backbone on a miss (ntfy is the suspect channel, so it does NOT page back through ntfy); this READ-ONLY probe reads the verdict file and surfaces a miss into mini/+/fleet — the "send ≠ receipt" lesson aimed at the spine itself (the 2026-06-14→17 dark incident). Catches ntfy.sh-down / fleet-topic-publish-broken / sender-no-op; the operator-phone-on-wrong-topic case is Phase 3's tap-to-ack job. INERT off the manager box; stale verdict → cron_verdict_stale owns the dead-cron alert. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as fleet_box_unreachable/host_frozen). No issue#.
    "ntfy_ack_stale",  # 2026-06-18 ntfy receipt-heartbeat Phase 3: the only rung that confirms the HUMAN's DEVICE. A manager-box cron (scripts/fleet_ntfy_ack.sh) sends a WEEKLY tap-to-ack page to the fleet topic with an ntfy action button; the tap makes the PHONE POST to a dedicated ack-topic (<fleet>-ack), which the cron polls. consecutive_unacked_pings grows each unacked week (reset on ack); the cron escalates via the Phase-1 EMAIL backbone at ≥2 unacked (~2 weeks dark), and this READ-ONLY probe surfaces it into mini/+/fleet. Catches exactly the 2026-06-14→17 incident (phone on a wrong/dead topic, app killed, notifications off) — what loopback (Phase 2, a different subscriber) structurally cannot. INERT until first pinged; stale state → cron_verdict_stale owns the dead-cron alert. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as ntfy_loopback). No issue#.
    "meshtasticd_vsz_leak",  # 2026-07-10 (upstream meshtastic/firmware#10468, the operator's own 2026-05-13 report, re-confirmed live 07-10 on both Pi5 boxes): meshtasticd on Pi5+USB leaks exited pthread stacks (~110 GB VSZ/day, RSS bounded, mmap regions 70k+); the weekly meshtasticd-restart.timer band-aid was UNWATCHED — fires only past the weekly envelope (default 768 GB) = the restart missed or the rate worsened. Pi4/SPI boxes idle ~0.3 GB and cannot trip it. Documented inline (MF012 cap precedent). No own issue#.
    "gateway_delivery_degraded",  # 2026-06-20 gateway-reliability arc A2: the gateway's OWN self-report (att/del/drop journal block + its RNS resource/forward error channel) shows it is NOT delivering — OUTCOME monitoring, not shape-enumeration. Leg 1 = windowed delivered/attempted ratio collapse (recent, high-volume; conservative floor because the journal's total-dropped folds in benign Mesh→RNS broadcast misses — the precise lens is delivery_confirmation_stall); leg 2 = a spike of EROFS / resource-assembly / forward-to-secondary errors, the exact 2026-06-20 wx-total-loss witness class that HAD a journal witness but no probe consumer (honest_failure_modes #9 at the spine level; #60 sandbox class — gateway shared-instance RNS client couldn't write assembled Resources under /etc/reticulum/storage). INERT off a box that doesn't run meshforge-gateway (moc/moc3 only); journalctl-unobservable holds, observed-clean resets, 2-tick debounce. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as nomadnet_crashloop/calibration_drift). No own issue#.
    "resource_canary_degraded",  # 2026-06-20 gateway-reliability arc A1 (the OUTCOME source of truth): the synthetic RESOURCE round-trip canary (meshforge-gateway-resource-canary.timer → src/lab/gateway_resource_canary) FAILED its verdict or went DARK. Where A2 reads the gateway's self-report, A1 actively PROVES the gateway delivers a multi-chunk RNS Resource round-trip — the exact path the 2026-06-20 wx-total-loss EROFS broke while single-packet replies kept working (so every shape/liveness probe and the single-packet gateway_rt_canary read green). The canary fires a control PING + a PINGBIG whose reply is resource-sized; its own FAIL "control back, resource NOT" is the EROFS signature. This probe consumes the verdict envelope (last.json): FAIL/CONCERN verdict OR a stale file (silence = the failure mode for a fixed-cadence canary). degraded only (gateway_delivery_degraded/delivery_confirmation_stall own the hard-failure surface). INERT off a box that doesn't run the canary (state dir absent); 2-tick debounce; the "canary itself must be watched" pattern, mirroring synth_soak_degraded. Documented inline (no persistent_issues.md row — MF012 40k cap). No own issue#.
    "nomadnet_crashloop",  # 2026-06-19: the NomadNet USER systemd unit is crashlooping (systemd 'restart counter is at N' under the USER_UNIT= journal field) — probe_service_inactive is structurally BLIND to user units (root/system-context systemctl can't see them at all, and a unit thrashing in auto-restart is neither inactive nor failed), so the NRestarts=7842 loop went 10 days silent. Root-direct USER_UNIT= journal read (no sudo — watchdog sandbox); SHORT live-window + a newest-restart recency gate so post-fix history can't false-page. INERT on a healthy/disabled/never-installed unit (moc5) and when journalctl is unobservable. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as ntfy_loopback/ntfy_ack_stale). Cross-refs the #69-fix-regression (the rnstatus boot-gate fix); no own issue#.
    "oracle_delivery_degraded",  # 2026-06-22 mesh-oracle health: the read-only "ask dude-AI over the mesh" responder (src/oracle) answered queries but its confirmable delivery rate (delivered / (delivered + send_errors), over a recent ts window of its audit log) fell below threshold — the oracle was the one live service with NO automated probe (a blind spot for a service whose ethos is "silence is the failure mode"). Intentional declines (cooldown / not_allowlisted) and benign non-deliveries (reason-less delivered:false — RNS no-path to an unannounced ephemeral identity / MeshCore restart race) are EXCLUDED from the failure set + surfaced, so the rate is the #74 confirmation view, not a false alarm on a cooldowned/quiet channel. WITNESSED v1 blind spot: the RNS leg's send_to_rns swallows real send exceptions to a bare False, so an RNS send error lands in the benign bucket (not send_error) — v1 covers the Meshtastic/MQTT/MeshCore legs' send_error fully + makes the RNS gap visible via the benign count; closing it needs send_to_rns to distinguish no-path from crash (deferred out of the mf.5 RNS soak). degraded only (low-traffic read-only service); min-sample guard (no silence leg — a reactive service that nobody queried is not "broken"); INERT off a box where the oracle never wrote a log, and (2026-08-08) INERT when it wrote one but has answered nothing — enrolled-but-idle is a successful observation, not a blind one; too few CONFIRMABLE answers stays indeterminate, because all-declines / all-benign-RNS is the row-2 blind spot and must not read as "nothing to watch". v2 (2026-08-08) rates the last N=8 confirmable answers however old (a COUNT window) instead of a 6h TIME window that the fleet's only oracle box could never fill — moc3 answers ~5 queries a month in bursts, so v1 was structurally unable to judge and sat indeterminate forever. A STALENESS GUARD replaces what the time window used to do: only a sample whose newest answer is within 24h may read clean or fire, a stale one is INERT with its rate + age in the reason (visible, unpaged), and firing additionally needs a send_error inside that 24h so a rate dragged down by month-old failures cannot page about history. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as resource_canary_degraded). No own issue#.
    "inherited_app_drift",  # 2026-06-21 upstream-app ownership Action 5: an INHERITED (non-Nursedude-origin) upstream app checkout on this box carries an unversioned tracked-file CODE patch — a hand-edit that exists in NO repo we control and is one `git pull` from silent deletion (the rescued .32 + dev-box bot patches were exactly this; policy §4.2). LOCAL problem-class detection: scans the top level of the operator home + /opt, reads .git/config to classify owned-vs-inherited (no PINS.md coupling — honest_failure_modes #5), runs `git status --porcelain --untracked-files=no` so untracked config/build artifacts (Raven raven.conf, ucode build/) and machine-generated dependency manifests/lockfiles (MeshSense npm package*.json churn) never false-fire. The floating-`main`/pin-drift leg is deliberately NOT a local fire (the fleet's enforcement is "record the pin, never auto-pull" per PINS.md, NOT detached HEAD — firing on "on a branch" would contradict that and page every intentionally-pinned moc5 app). INERT (None) on a box with no inherited checkouts (moc1/2/3); git/origin-unreadable repos are skipped (indeterminate ≠ clean); 2-tick debounce rides an operator mid-edit. degraded only. Documented in .claude/plans/upstream_app_ownership_policy_2026_06_21.md §9; no own issue#.
    "router_scout_degraded",        # 2026-07-11 OpenWrt-router arc: a mirrored meshforge-scout tick (~/.local/share/meshforge/router_scout/<device>_tick.json, landed by scripts/router_scout_pull.sh over the existing ssh channel) shows the ROUTER-side agent degraded — fresh mirror but stale captured_at (the agent cron went dark on the router while the pull keeps re-copying the same old tick), tick ok=false (the agent's own tri-state witnesses: tmpfs data_dir, unreadable /proc, dead radio TCP), or an unparseable mirrored tick (the pull validates before writing, so garbage = writer/shape drift, not a torn read). DEFENSE-IN-DEPTH: the pull's own eval also FAILs cron_verdict on these — this probe adds the watchdog-spine surface (per-device subject into /fleet + mini) and covers mirrors landed by any other path. degraded only — every condition observed is REMOTE (the tracer_peer_unreachable lesson). INERT off boxes with no mirror dir; a STALE mirror file is skipped (dead pull cron = cron_verdict_stale's beat, router_scout is verdict-wired). Documented inline (no persistent_issues row — MF012 40k cap; same precedent as meshtasticd_vsz_leak). No own issue#.
    "gateway_dup_degraded",         # 2026-06-29 dedup/identity arc STEP 5 — the FIRST probe with a per-logical-message + cross-gateway dimension. Consumes the 4c cross-box rollup (/fleet/dups): a fleet DUPLICATE is the same (content_id, recipient) CONFIRMED by >1 DISTINCT gateway — the live dup-A (moc 3dfbdb5d + moc3 f68c2f56 both -> 6b1a0120 under two LXMF source hashes a stock client cannot collapse). degraded only (a dup is a quality/cost defect, not an outage — delivery still happened). Honest self-guards built on the 4c JOIN indeterminate gate: status!=ok (<2 contributing gateways reachable — the rollup only exists on the manager box running the collector cron) -> None+HOLD streak; freshness.stale (dead collector) -> None+HOLD; observed-clean resets; 2-tick debounce. ALERTS only — cross-gateway suppression is the separately-gated STEP 6. Documented inline (no persistent_issues row — MF012 40k cap; same precedent as resource_canary_degraded). No own issue#.
    "host_memory_pressure",         # 2026-07-24 manager-box hard-reset arc (8th): the box is running out of RAM and nobody was watching. Two legs — MemAvailable/MemTotal under 20% (wedge under 8%), and /proc/pressure/memory some/avg60 over 10% (wedge over 40%); worst wins, and either can fire alone. Born from a reset whose forensics proved the mechanism and lost the culprit: MemAvailable fell 9.85->2.56 GB in 2 min with ext5v flat at 5.05 V and throttled=0x0 (brownout/thermal positively excluded), then the box died mid-journal-line with ~2.5 GB still free and NO oom-kill — it was the HARDWARE watchdog, because a memory stall starved PID 1 past RuntimeWatchdogUSec=1min. Nothing detected it and nothing recorded WHO: /tmp is tmpfs (wiped by the reset), sysstat is ENABLED="false", and power_history.log logs how much went, never who took it. So the detail carries the top-5 RSS roster, making the signal the post-mortem witness that arc has never had. degraded debounced 2 ticks (a pytest run or a headless-chromium shot legitimately dips a small box under 20% for one tick); WEDGE fires immediately — the 07-24 reset landed 34 s after the first sub-20% sample. PSI absent => availability leg judges alone and the detail SAYS so (a one-legged verdict is never dressed as agreement). Documented inline (no persistent_issues row — MF012 40k cap; same precedent as meshtasticd_vsz_leak). No own issue#.
    "local_brain_regressed",        # 2026-07-22 second-brain arc WS-D: makes the LEARNING record observable. A local-brain eval case that passed >=N times before now FAILS — the tier-L model lost a capability it demonstrably had. NON-redundant with cron_verdict_stale (#78) + the weekly --gate: those judge only the AGGREGATE pass_rate, so a single case (or a whole thin kind can regress while the oracle-dominated aggregate stays green and the gate stays silent. PER-CASE + cursor-robust: compares each case's own pass/fail history across ledger records, so budget-chunked partial runs (different subset each week) don't cause false drops. MODEL-AWARE since 2026-07-25: history is scoped to the model of the most-recent run, because a backend/model A/B (the ditch-ollama plan's Step 2/3 method) would otherwise turn "a different model answered" into "the tier lost a capability"; a model swap resets the baseline, which under-fires rather than pages falsely. Scopes to the manager box for free (no ~/local_brain_evals.jsonl => tier-L not evaluated here => INERT). degraded only, escalate-only in seed (NO ntfy page). Reads the eval results ledger root-safe; self-guards inert/indeterminate on absence/unreadable. Documented inline (no persistent_issues row — MF012 40k cap; same precedent as dream_ratification_stalled). No own issue#.
    "claw_uplink_node_moved",       # ⚠️ PRIMARY LEG = THE PINHOLE, added hours after the first version shipped flawed. The 2026-07-29 root cause was not "the node moved" in the abstract: moc2 default-denies NATS 4222 and admits a HARDCODED allowlist (WireClaw cannot send NATS credentials), the uplink's DHCP lease moved to a new address on the fleet segment while the pinhole kept the OLD one, and every SYN from a healthy claw was dropped for 6.5 h — proven live by /proc/net/nf_conntrack showing 4x SYN_SENT [UNREPLIED] from the claw on its own /28 to the brain box:4222, and by the cure (one IP changed -> session established in 30 s). The first version compared the observed address against an expected_ip hand-written into claw_uplink_nodes.json — a THIRD independent copy of that same constant, i.e. it REPRODUCED the drift class it exists to catch (honest_failure_modes #5); replayed against the real pre-fix ruleset it reported `clean` through the entire outage. So the probe now reads the pinhole as the AUTHORITY: the question is not "is it where I declared" but "is it at an address the firewall will actually admit". A pinhole that never mentions the port is NO OPINION (this box does not gate it), never an empty allowlist — "admits nobody" and "does not gate" are opposite facts. The declaration leg survives as the weaker secondary (traffic flows, declaration stale). Below: the original rationale.
    # ...original rationale, retained as documentation (the line below was a
    # SECOND enum entry until 2026-07-29 — see the dedup note above): 2026-07-29 live incident: a declared claw UPLINK node (the AREDN/AP box bridging a claw's own subnet to the fleet segment) is answering at an address other than the one declared. THE distinction: a claw can be entirely healthy — booted, associated, holding its DHCP lease, dialling the correct broker — and still be unreachable because the uplink moved out from under it; the fleet's only word for that state was claw_device_dark, which points the operator at hardware that is working. Born from dudeclaw-01 going ~5 h dark while the investigation spent a power cycle, an antenna reseat, two chip resets and three dead hypotheses on a device that was never at fault (config read off its own LittleFS proved nats_host correct the whole time). LEADING indicator by design — fires on the CONDITION (uplink not where declared), not the OUTCOME (a claw went quiet), the gateway_dual_homed_exposure reasoning: a relocated uplink may still route, and is worth naming BEFORE it strands the fleet's only out-of-band witness. Observation-only: reads /proc/net/arp, a plain file — no packets, no subprocess, nothing that perturbs what it measures. ⚠️ ONLY ATF_COM (0x2) rows are sightings: /proc/net/arp also lists INCOMPLETE (0x0) rows for resolution attempts that FAILED, and probing a stale address MANUFACTURES such a row carrying the target MAC — a reader ignoring flags invents drift from its own footprints, most reliably when someone goes looking (found by testing the probe's own input, not by reading it). Seen at the declared address AND elsewhere counts as home (under-fire, never false-page); MAC observed nowhere is indeterminate, never "moved". INERT on a box with no declaration (most of them); 2-tick debounce. Documented inline (no persistent_issues row — MF012 40k cap; same precedent as memory_cap_engaged). No own issue#.
    "rns_instance_name_mismatch",   # 2026-08-05: the watchdog is probing an @rns/<name> that has NO listener while the kernel advertises a different one — i.e. the RNS probes are keyed to a name this box does not serve, so BOTH of them are dark. Not an rnsd fault: rnsd is fine, the watchdog is looking in the wrong place. Found live on the federator box, where _read_rns_instance_name() asked ~/.reticulum/config FIRST via get_real_user_home() — which under this ROOT systemd service resolves to /root, not the operator — and read a stale /root/.reticulum/config (holding the box HOSTNAME) while rnsd ran --config /etc/reticulum (holding the real, spaced instance name). Cost: rns_shared_instance_unresponsive sat indeterminate 8.8 days (its detail blaming rnsd, "shutting down or not serving"), and rns_namespace_collision reported an affirmative CLEAN while matching zero listeners — the #69 detector blind and green on the very box #69 happened to. Two things hid it: (1) Linux answers a nonexistent ABSTRACT socket with ECONNREFUSED, never ENOENT, so the probe's "listener absent" branch was unreachable and permanent misconfiguration was indistinguishable from a transient rnsd shutdown; (2) the N1 spaced-name fix had hardened the ss PARSE while nothing checked the name had a listener behind it. mini DID escalate it (detector_blind_any, then three persistent_active dream proposals at 70m/170h/170h) — all rejected as unspecified, so the witness worked and the read failed. degraded, not wedge: no traffic is impaired, but the fleet's RNS eyes are shut. Fires only when the connect is REFUSED and the table shows some OTHER @rns/*; an unobservable table, a listener under our own (possibly space-truncated) name, and an empty table are each indeterminate — blindness about blindness is not a page. No own issue#.
    "memory_cap_engaged",           # 2026-07-24 hard-reset arc, same session that CREATED the blind spot: eight boxes gained hard MemoryMax caps on user-1000.slice (plus ollama's 8G) and NOTHING watched them fire. A cap that OOM-kills an ssh session or a user unit is invisible — the process is simply gone, the classic honest_failure_modes #9 witness-less event. Reads cgroup memory.events per CAPPED cgroup (finite memory.max only). KILL leg (wedge, no debounce — a kill is discrete and irreversible, delaying the page only delays the news) fires on a rise in oom_kill/oom_group_kill; the detail states BOTH readings because the counter cannot distinguish them: a runaway correctly bounded, or legitimate work killed by a too-tight cap (the failure this very session caused at 18:22, when a MemoryHigh+oomd pairing killed 49 processes with 7.7 GB free). CEILING leg (degraded, 2-tick) fires when the `max` counter keeps rising WITHOUT kills — the slice lives at its limit and reclaim absorbs it. That leg deliberately REPLACES a worse plan, "re-read memory.peak in a week and re-tighten": willpower rather than harness, pointed the wrong way (a too-generous cap still bounds a runaway; a too-tight one kills real work), and memory.peak is reset by the reboots this fleet actually has. Baselines keyed to boot_id because the counters restart at zero each boot; a DECREASE means the cgroup was recreated and re-baselines silently rather than reading as recovery; unreadable memory.events is indeterminate, never "no kills"; a box with no finite cap anywhere is INERT (moc3). Documented inline (no persistent_issues row — MF012 40k cap; same precedent as host_memory_pressure). No own issue#.
)

SEVERITIES = ("info", "degraded", "wedge")

# ─────────────────────────────────────────────────────────────────────
# Per-tick disposition recorder — the coverage side-channel (fleet-truth
# Phase 0). A probe returning None conflates three honest answers:
#
#     clean          — probe RAN, the observation succeeded, nothing wrong
#     inert          — the organ is LEGITIMATELY not present on this box
#     indeterminate  — the probe could NOT observe (journal unavailable,
#                      parse error, stale input, debounce-pending candidate)
#
# Probes disambiguate by calling ``note_disposition`` at their return
# sites. THE CONTRACT IS FAIL-DARK: a class nothing noted renders
# "unknown" (dark) in the coverage map — silence can never read green
# (honest_failure_modes #1/#2). NEVER note ``clean`` unless the
# observation positively succeeded this tick. Signal-emitting paths need
# no note — the runner derives ``active`` from the returned Signal, and
# an active signal outranks any note.
#
# Worst-wins merge per class per tick (indeterminate > inert > clean):
# a probe covering N subjects reads clean only if EVERY subject is clean.
# The recorder is module-global, reset by the runner at tick start —
# single-threaded within a tick like the probes themselves.
# ─────────────────────────────────────────────────────────────────────

DISPOSITIONS = ("clean", "inert", "indeterminate")
_DISP_RANK = {"clean": 0, "inert": 1, "indeterminate": 2}

_tick_dispositions: dict = {}


def reset_dispositions() -> None:
    """Clear the recorder. The runner calls this at the top of every tick."""
    _tick_dispositions.clear()


def note_disposition(cls: str, disp: str, *, reason: Optional[str] = None) -> None:
    """Record a probe's per-class disposition for this tick. Never raises.

    An invalid ``disp`` is recorded as ``indeterminate`` (a programming
    error must not silently become a healthy-looking value). Worst-wins:
    a later, worse note overrides; a later, better note does not.
    """
    if disp not in _DISP_RANK:
        reason = f"invalid disposition {disp!r} noted (probe bug)"
        disp = "indeterminate"
    prev = _tick_dispositions.get(cls)
    if prev is not None and _DISP_RANK[prev["disp"]] >= _DISP_RANK[disp]:
        return
    entry = {"disp": disp}
    if reason:
        entry["reason"] = reason
    _tick_dispositions[cls] = entry


def collect_dispositions() -> dict:
    """Snapshot the recorder (shallow copy — entries are never mutated)."""
    return dict(_tick_dispositions)


@dataclass
class Signal:
    """One active failure signal. Identity = (class, subject)."""
    cls: str               # one of SIGNAL_CLASSES
    subject: str           # e.g. "meshforge-echo", "<peer-short-name>"
    severity: str          # one of SEVERITIES
    detail: str            # human-readable; goes straight to /fleet panel
    issue_ref: Optional[int] = None   # GitHub-ish issue number for cross-ref
    extra: dict = field(default_factory=dict)  # probe-specific data

    def key(self) -> Tuple[str, str]:
        """Stable identity for edge-transition tracking."""
        return (self.cls, self.subject)

def _resolve_main_pid_status(
    service_name: str, *, systemctl_path: str = "systemctl",
) -> Tuple[str, Optional[int]]:
    """Tri-state (four-state) MainPID resolution: ``(status, pid)``.

    ``status`` is one of:

    ==============  ================================================
    ``"ok"``        unit is loaded and running; ``pid`` > 1
    ``"down"``      unit EXISTS on this box but has no MainPID
                    (inactive/failed) — ``service_inactive`` owns it
    ``"absent"``    no such unit here at all (``LoadState=not-found``)
                    — the organ is missing BY DESIGN on this box
    ``"unknown"``   systemctl could not be run or its answer could not
                    be parsed — unobservable, never "absent"
    ==============  ================================================

    ⚠️ Why this exists (2026-08-12). The flat ``_resolve_main_pid`` below
    collapsed all four into a single ``None``, and ~8 consumers each turned
    that None into the same claim: "MainPID unresolved; ``service_inactive``
    owns that". On meshanchor-server — the fleet's MeshCore-primary box,
    which has **zero** meshtasticd unit files and no binary on PATH — that
    handoff points at a probe that cannot own a unit which does not exist,
    so ``channel_feed_dark``, ``mqtt_root_drift``,
    ``meshtasticd_phoneapi_wedge`` and ``meshtasticd_vsz_leak`` sat
    ``indeterminate`` permanently, by construction. An organ absent by
    design must read ``inert``, or real failures have nowhere to stand out
    (persistent_issues, 2026-08-05).

    Exactly the same defect, on the same box, was already cured once for
    deployment.json — see ``_read_deployment_declaration_status`` above
    (2026-08-07). A fix applied to one instance is not applied to the class.

    ⚠️ ``absent`` is NOT universally benign. It is a correct ``inert`` only
    for probes that OBSERVE a service; for a probe whose job is to notice
    that a unit which SHOULD be running is not, ``absent`` may be the
    finding itself. Each call site needs its own semantic judgement — never
    sweep this.

    Measured discriminator (live, 2026-08-12): ``systemctl show`` exits 0 in
    ALL of these cases, so the return code carries no signal; ``LoadState``
    does. Both facts come from ONE subprocess, so the status form costs
    nothing extra per tick. Properties are parsed as ``KEY=value`` rather
    than with ``--value``: systemd emits them in its own canonical order,
    not the order they were requested, so positional parsing would be a
    latent mis-pairing.
    """
    try:
        proc = subprocess.run(
            [systemctl_path, "show", "-p", "MainPID", "-p", "LoadState",
             service_name],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ("unknown", None)
    if proc.returncode != 0:
        return ("unknown", None)

    props = {}
    for line in (proc.stdout or "").splitlines():
        key, sep, val = line.partition("=")
        if sep:
            props[key.strip()] = val.strip()

    load_state = props.get("LoadState")
    raw_pid = props.get("MainPID")
    if raw_pid is None:
        return ("unknown", None)
    try:
        pid = int(raw_pid)
    except (ValueError, TypeError):
        return ("unknown", None)

    if pid > 1:
        return ("ok", pid)
    # No MainPID. Only an explicit not-found proves the unit is absent; a
    # missing/odd LoadState (older systemd, unexpected output) falls back to
    # the pre-2026-08-12 meaning, which is the conservative one.
    if load_state == "not-found":
        return ("absent", None)
    return ("down", None)


def _resolve_main_pid(
    service_name: str, *, systemctl_path: str = "systemctl",
) -> Optional[int]:
    """``systemctl show -p MainPID <service>`` parser. Returns None
    on any failure (including inactive service which reports
    ``MainPID=0``).

    Back-compat shim over ``_resolve_main_pid_status`` (ONE implementation —
    honest_failure_modes #5). Callers that must tell "no such unit on this
    box" from "the unit is down" or "I could not look" need the status form;
    collapsing them is what this shim cannot avoid.

    As of 2026-08-12 NO probe module is left on this shim — the guard
    ``TestTriStateHelperContract`` enforces that, and it is what stopped the
    first cut of this fix at four of the eight call sites (the class-not-
    instance rule, mechanised). Every site was judged individually:

    * The four meshtasticd OBSERVERS (``channel_feed_dark``,
      ``mqtt_root_drift``, ``meshtasticd_phoneapi_wedge``,
      ``meshtasticd_vsz_leak``) — absent → ``inert``; this is the defect
      that prompted the split.
    * ``fd_exhaustion`` / ``phoneapi_tcp_leak`` / ``main_thread_wedge`` —
      observers of an arbitrary unit; with no unit there is no fd table, no
      owner process and no thread stack to read, so absent → ``inert``.
      Nothing is hidden: ``systemctl is-active`` answers ``inactive`` for a
      nonexistent unit (verified 2026-08-12), so a unit that is EXPECTED
      active and missing still pages via ``service_inactive``.
    * The two gateway-organ gates (``delivery_confirmation_stall``,
      ``gateway_delivery_degraded``) and the wedge probe's gateway leg —
      these already answered ``inert`` for a flat None, which quietly
      included "systemctl errored". Absent/stopped stay ``inert``; only the
      ``unknown`` case moved, to ``indeterminate``, because a state we could
      not READ is not an observation that this box has no gateway.

    Converting a site is always a per-site semantic call: for a probe whose
    job is to notice that a unit which SHOULD be running is not, ``absent``
    is the FINDING, and turning it into ``inert`` would silence a real
    detector. ``probe_service_inactive`` is that probe, and it deliberately
    does not resolve a MainPID at all.
    """
    return _resolve_main_pid_status(
        service_name, systemctl_path=systemctl_path
    )[1]


def note_unit_presence_gate(
    cls: str,
    pid_status: str,
    *,
    absent_reason: str,
    unresolved_reason: str,
    stopped_is_inert: bool = False,
    unknown_reason: Optional[str] = None,
) -> None:
    """ONE implementation of the absent→``inert`` / unknown→``indeterminate``
    disposition policy for a no-PID resolution (2026-08-12 review).

    The 08-12 conversion applied this policy as a hand-copied ``if/else`` at
    ten call sites across six modules — and the arc's own lesson ("a fix
    applied to one instance is not applied to the class") predicts the next
    probe author copies a pre-08-12 shape, destructures the status tuple,
    branches only on the pid, and quietly re-creates the collapse.
    ``TestTriStateHelperContract`` now requires every module that CALLS
    ``_resolve_main_pid_status`` to also call this gate, so the policy has a
    single implementation and a mechanised consumer.

    Two families, chosen by ``stopped_is_inert``:

    * ``False`` (service OBSERVERS — channel_feed_dark, mqtt_root_drift,
      fd_exhaustion, phoneapi_tcp_leak, meshtasticd_vsz_leak,
      main_thread_wedge, the wedge probe's meshtasticd leg):
      ``absent`` → ``inert`` (``absent_reason``); ``down``/``unknown`` →
      ``indeterminate`` (``unresolved_reason``). A unit that exists and is
      stopped is ``service_inactive``'s to page.
    * ``True`` (ORGAN-PRESENCE gates — delivery_confirmation_stall,
      gateway_delivery_degraded, the wedge probe's gateway leg):
      ``unknown`` → ``indeterminate`` (``unknown_reason``), because a
      systemctl we could not RUN is not an observation that this box has no
      organ; ``absent``/``down`` → ``inert`` (``absent_reason``).

    The SEMANTIC judgement stays at the call site (which family, and the
    exact reason strings); only the mechanism lives here. For a probe whose
    job is to notice that a unit which SHOULD be running is not, ``absent``
    is the FINDING — such a probe must not use this gate at all
    (``probe_service_inactive`` resolves no MainPID).
    """
    if stopped_is_inert:
        if pid_status in ("absent", "down"):
            note_disposition(cls, "inert", reason=absent_reason)
        else:
            # "unknown" — or any FUTURE resolver status. `inert` may only
            # follow a POSITIVE observation of absence; an unrecognized
            # status defaulting to the quiet value would be the 08-05
            # collapse re-created by enum growth (honest_failure_modes #7:
            # closed enums need closed consumers — this else is the closed
            # consumer, and it points AWAY from quiet).
            note_disposition(
                cls, "indeterminate",
                reason=unknown_reason or unresolved_reason)
    else:
        if pid_status == "absent":
            note_disposition(cls, "inert", reason=absent_reason)
        else:
            note_disposition(cls, "indeterminate", reason=unresolved_reason)


def _read_deployment_declaration_status(
    service_user,
) -> Tuple[str, Optional[str], dict]:
    """Tri-state read of ``role`` + ``service_overrides`` from deployment.json.

    Returns ``("declared", role, overrides)``, ``("undeclared", None, {})``
    when there is no deployment.json (or one that declares no role — a
    positive observation that this box is not role-managed), or
    ``("unreadable", None, {})`` when a file that should be readable isn't,
    or the service user can't be resolved.

    ⚠️ Why tri-state (2026-08-07): the flat form below collapsed *absent* and
    *unreadable* into ``(None, {})``, and both callers then had to pick the
    pessimistic meaning for both — ``probe_role_drift`` said so in a comment
    ("the two cannot be told apart here, so the merged note must be the
    worse"). Measured that day on meshanchor-server, which legitimately has
    NO deployment.json because it is a MeshAnchor box and not MeshForge
    role-managed: ``role_drift`` and ``rules_seed_drift`` both sat
    permanently ``indeterminate``, each naming "absent/unreadable" in its own
    reason string — the collapse spelled out loud, and a genuinely corrupt
    declaration on a REAL fleet box would have been invisible inside that
    standing noise. Same defect as the ``channel_feed_dark`` /
    ``mqtt_root_drift`` family: an organ absent by design must not read as an
    observation that failed. Mirrors
    ``watchdog_probes_channel._read_json_uplink_expectation``.

    The watchdog runs as sandboxed root: ``get_real_user_home()`` (which
    ``provision_role.py`` uses at import time) would resolve to ``/root``
    here, so the home is derived from the service user and READ directly —
    never escalate/switch user (the rns_version_drift lesson).
    """
    if not service_user:
        return ("unreadable", None, {})  # can't resolve user → can't observe
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "deployment.json")
    except (KeyError, OSError, TypeError):
        return ("unreadable", None, {})
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return ("undeclared", None, {})  # no file → box is not role-managed
    except (OSError, ValueError, TypeError):
        return ("unreadable", None, {})
    try:
        role = data.get("role")
        ov = data.get("service_overrides") or {}
        ov = ov if isinstance(ov, dict) else {}
        if isinstance(role, str) and role:
            return ("declared", role, ov)
        # File read fine and declares no role: a positive observation, not a
        # failed one.
        return ("undeclared", None, ov)
    except (AttributeError, TypeError):
        return ("unreadable", None, {})


def _read_deployment_declaration(service_user) -> Tuple[Optional[str], dict]:
    """Back-compat shim over ``_read_deployment_declaration_status`` (ONE
    implementation — honest_failure_modes #5). Callers that must distinguish
    "this box declares no role" from "I could not read the declaration" need
    the status form; collapsing them is what this shim cannot avoid."""
    status, role, ov = _read_deployment_declaration_status(service_user)
    return (role, ov if status != "unreadable" else {})

# ── incremental journal scanning: the cursor memo ────────────────────
# 2026-07-01 review finding 14, landed 2026-08-12. The moc5 pegged-core fix
# bounded probe_channel_feed_dark's WINDOW (24h -> a derived 7h) but left the
# CLASS untouched: the NO-MATCH case re-scanned that entire window every 30s
# tick, forever, and mqtt_root_drift did the same over a fixed 6h — two
# full-window scans per tick on a no-json-uplink box (the moc5 shape), and the
# next probe to clone the recipe would have re-created the peg.
#
# The cure: scan only what the journal has GROWN since the last SUCCESSFUL
# look, and carry the remembered newest match forward while it is still inside
# the caller's window. Correctness argument for carrying it: a full scan
# returns the NEWEST match in the window, so nothing exists between it and the
# scan time; every later tick covers (last_scan, now]; the union is therefore
# gapless, and the remembered match is the newest until it ages out of the
# window on its own timestamp.
#
# In-process, keyed by (unit, pattern, journalctl_path) — deliberately NOT a
# cursor FILE. The watchdog ticks every 30s; an SD-card write per probe per
# tick is a worse bill on a Pi than the scan it would save, and a restart
# simply costs one full scan, which is correct rather than merely cheap.
#
# ⚠️ The position NEVER advances on a scan that did not succeed. Advancing it
# on an unobservable read would permanently skip a stretch of journal that
# nothing ever looked at — a blind spot manufactured by the optimisation
# itself (honest_failure_modes #2: unobservable is not "nothing there").
_JOURNAL_MEMO: Dict[tuple, dict] = {}

_LOOKBACK_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def reset_journal_memo() -> None:
    """Forget every remembered scan position (next call per key full-scans).

    Tests MUST call this between cases — it is module-global state, and a memo
    leaking across cases is the ambient-state defect this repo has been bitten
    by before. Do NOT call it per tick: that reinstates the full scan it exists
    to remove (contrast ``reset_dispositions``, which is per-tick BY design).
    """
    _JOURNAL_MEMO.clear()


def _lookback_seconds(lookback: str) -> Optional[float]:
    """``'7h'`` -> 25200.0; None when the shape is not one we parse.

    None deliberately costs a full scan and no memo: a window we cannot
    measure must never be guessed at, because guessing SHORT would silently
    narrow what every probe sharing this helper is able to see.
    """
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd])\s*", lookback or "")
    if not m:
        return None
    return float(m.group(1)) * _LOOKBACK_UNITS[m.group(2)]


def _match_line_ts(line: Optional[str]) -> Optional[float]:
    """Epoch seconds off a ``-o short-unix`` line (its first token), or None."""
    if not line:
        return None
    try:
        return float(line.split(None, 1)[0])
    except (ValueError, IndexError):
        return None


def _journal_newest_match_status(
    unit: str,
    pattern: str,
    lookback: str,
    journalctl_path: str = "journalctl",
    now: Optional[float] = None,
) -> Tuple[str, Optional[str]]:
    """Tri-state form of ``_journal_newest_match``.

    Returns ``("ok", line)`` for a match, ``("ok", None)`` when journalctl
    RAN and positively found nothing, or ``("unobservable", None)`` when
    the query could not be answered at all.

    ⚠️ Why (2026-08-05): the flat form collapses "this unit genuinely
    logged no such line" into the same None as "journalctl is wedged", and
    callers then have to pick one meaning for both. ``mqtt_root_drift``
    picked the pessimistic one and consequently sat ``indeterminate``
    forever on every RX-only box — permanent noise that a REAL journal
    outage would have been invisible inside. Absence of evidence is only
    evidence of absence once you have shown the channel works
    (honest_failure_modes #2). Mirrors ``_journal_count_match``, which has
    always kept the distinction.
    """
    now_ts = time.time() if now is None else now
    key = (unit, pattern, journalctl_path)
    window_s = _lookback_seconds(lookback)
    horizon = None if window_s is None else (now_ts - window_s)

    # Decide the scan floor. Default is the caller's full window; a usable memo
    # narrows it to "since we last successfully looked".
    memo = _JOURNAL_MEMO.get(key) if window_s is not None else None
    if memo is not None and memo["scanned_through"] > now_ts:
        # Clock went backwards (RTC-less Pi, NTP step). A remembered position
        # in the future would suppress scanning of real journal — drop it and
        # take the honest full scan.
        _JOURNAL_MEMO.pop(key, None)
        memo = None
    since = f"-{lookback}"
    if memo is not None and memo["scanned_through"] > horizon:
        since = "@%d" % int(memo["scanned_through"])

    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit, "--since", since,
                "-g", pattern, "-r", "-n", "1", "-o", "short-unix",
                "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ("unobservable", None)   # memo untouched — never skip unread journal
    # rc 1 = "no entries matched" on some systemd builds; only >1 is an error.
    if proc.returncode not in (0, 1):
        return ("unobservable", None)
    # ⚠️ A MALFORMED --since also exits 1 with an empty stdout ("Failed to parse
    # timestamp: …" on stderr) — measured 2026-08-12 — which the rc check above
    # would otherwise read as an affirmative "nothing matched". That is the
    # error-reads-as-empty shape, and it would make this optimisation fail
    # silently and permanently: a probe would go dark and call it clean.
    # getattr, not proc.stderr: an injected double (or a caller passing a
    # lightweight stand-in) that omits the field must degrade to "no stderr",
    # never raise — an AttributeError here would take down the whole tick.
    if proc.returncode == 1 and (getattr(proc, "stderr", "") or "").strip():
        return ("unobservable", None)

    lines = proc.stdout.strip().splitlines()
    line = lines[0] if lines else None

    if window_s is None:
        return ("ok", line)         # unparseable window: answer, never memoize

    if line is not None:
        _JOURNAL_MEMO[key] = {"scanned_through": now_ts,
                              "match_ts": _match_line_ts(line),
                              "line": line}
        return ("ok", line)

    # Positively nothing NEW. The remembered match is still the newest in the
    # window until its own timestamp ages out of it.
    prev_ts = memo["match_ts"] if memo else None
    prev_line = memo["line"] if memo else None
    carried = prev_line if (prev_ts is not None and prev_ts >= horizon) else None
    _JOURNAL_MEMO[key] = {"scanned_through": now_ts,
                          "match_ts": prev_ts if carried else None,
                          "line": carried}
    return ("ok", carried)


def _journal_newest_match(
    unit: str,
    pattern: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[str]:
    """Newest journal line of ``unit`` matching ``pattern`` within ``lookback``.

    Returns the ``short-unix``-formatted line (epoch-seconds first token) or
    None on no match / journalctl unavailable / timeout. ``-r -n 1`` makes the
    busy-feed case cheap (journalctl stops at the first newest-first match);
    the no-match case is bounded by ``--since`` and the subprocess timeout.

    Callers that must tell "nothing logged" from "could not look" want
    ``_journal_newest_match_status`` — ONE implementation, this is its shim
    (honest_failure_modes #5).
    """
    return _journal_newest_match_status(
        unit, pattern, lookback, journalctl_path=journalctl_path)[1]


def _journal_count_match(
    unit: str,
    pattern: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[int]:
    """Count journal lines of ``unit`` matching ``pattern`` within ``lookback``.

    Returns the integer count of matching lines, or **None** on
    journalctl unavailable / timeout / non-trivial error. None is the
    honest *unobservable* answer — a probe must NEVER read it as ``0``
    (the healthy domain), or a journalctl wedge would mask the very
    contention this counts (honest_failure_modes #1: empty ≠ error).
    The watchdog runs as root, so journalctl needs no sudo. ``--since``
    plus the subprocess timeout bound the worst case on a busy unit.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit, "--since", f"-{lookback}",
                "-g", pattern, "-o", "cat", "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    # rc 1 = "no entries matched" on some systemd builds (a true 0, not an
    # error); only >1 is a real failure → unobservable.
    if proc.returncode not in (0, 1):
        return None
    out = proc.stdout
    if not out:
        return 0
    # Count non-empty lines; trailing newline must not inflate by one.
    return sum(1 for ln in out.splitlines() if ln)


def _journal_match_lines(
    unit: str,
    pattern: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[List[str]]:
    """The matching journal LINES (not just their count) — sibling of
    ``_journal_count_match`` for callers that must sub-classify a match.

    Same honest contract: ``[]`` = observed, nothing matched; **None** =
    unobservable (journalctl absent / timed out / rc > 1). A caller must
    never read None as ``[]`` — for a counter used to EXCLUDE benign events
    from a ratio, that collapse would silently restore the false signal the
    exclusion exists to prevent (honest_failure_modes #1).
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit, "--since", f"-{lookback}",
                "-g", pattern, "-o", "cat", "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    return [ln for ln in proc.stdout.splitlines() if ln]


def _journal_user_unit_has_lines(
    user_unit: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[bool]:
    """Does ``USER_UNIT=<user_unit>`` have ANY journal line in ``lookback``?

    The COVERAGE question for the reader above (2026-08-13). That reader
    honestly returns ``[]`` for "journalctl ran and nothing matched" — but
    ``[]`` also comes back when the unit has NO lines in the window at all, so
    a caller cannot tell "the job ran and logged no failures" from "nothing
    about this unit is visible here".

    **Measured on meshanchor-server**: of four enrolled timers, two returned
    empty for BOTH patterns and were folded into an affirmative healthy
    verdict. One, ``meshanchor-map-restart.service``, is a DAILY timer that
    had fired 19h earlier — outside the 3h lookback entirely, so "no failures"
    was never an observation about it (the slow-cadence residual documented in
    watchdog_probes_user's header). The other two had lines and were judged.

    ⚠️ Do NOT justify this by "the user journal is dark on that box".
    ``journalctl --user`` there reports *No journal files were found*, but that
    is the per-user client path; the root ``USER_UNIT=`` selector this helper
    uses works fine. Two different access routes — checked 2026-08-13 after an
    earlier read of mine conflated them.

    A unit that logged in the window is judgeable; one that logged nothing is
    not. Returns True (lines present), False (none at all — cannot judge), or
    **None** unobservable. Callers must treat both False and None as "say
    nothing about this unit", never as healthy
    (honest_failure_modes #2: absence of evidence is not evidence of absence).

    Cost note: intended to be asked ONLY when both pattern queries came back
    empty — the ambiguous case — so a busy, healthy box pays nothing extra
    ([[feedback_my_footprint_is_the_constraint]]).
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-q", f"USER_UNIT={user_unit}",
                "--since", f"-{lookback}", "-n", "1", "-o", "cat",
                "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    return bool(proc.stdout.strip())


def _short_unix_ts(line: str) -> Optional[float]:
    """Parse the epoch timestamp from a ``-o short-unix`` journal line."""
    try:
        return float(line.split(None, 1)[0])
    except (ValueError, IndexError):
        return None

def signal_to_dict(sig: Signal, *, first_seen_ts: Optional[float] = None) -> dict:
    """Serialize a Signal to the on-disk JSON shape.

    ``first_seen_ts`` (the runner's edge-transition tracker) is
    injected here so the on-disk record carries when this signal first
    appeared in addition to the latest probe tick.
    """
    out = {
        "class": sig.cls,
        "subject": sig.subject,
        "severity": sig.severity,
        "detail": sig.detail,
    }
    if sig.issue_ref is not None:
        out["issue_ref"] = sig.issue_ref
    if first_seen_ts is not None:
        out["first_seen"] = first_seen_ts
    if sig.extra:
        out["extra"] = sig.extra
    return out


# ─────────────────────────────────────────────────────────────────────
# Installed-package reads — shared by every probe that compares a DECLARED
# version (a pin, a floor) against what is actually on the box. Lives here for
# the same reason the streak counter does: drift, rns_env, and dep probes all
# need it, and independently-hardcoded copies WILL diverge (honest_failure_modes
# #5). The 2026-08-09 moc4 blindness was exactly that divergence — one copy
# listed system dist-packages, the other globbed only the user site.
# ─────────────────────────────────────────────────────────────────────

# Every root-readable location a SYSTEM-scope pip install can land.
_SYSTEM_DIST_GLOBS = [
    "/usr/local/lib/python3*/dist-packages",
    "/usr/lib/python3*/dist-packages",
    "/usr/lib/python3/dist-packages",
]


def _read_pkg_version_at_dirs(site_dirs, pkg):
    """Version of ``pkg`` found in the given site-packages dirs, or None.

    Reads in-process via ``importlib.metadata.distributions(path=...)`` — the
    watchdog sandbox (NoNewPrivileges + RestrictSUIDSGID) blocks sudo/runuser,
    but ProtectHome=no lets root READ any of these trees directly."""
    dirs = [d for d in dict.fromkeys(site_dirs) if os.path.isdir(d)]
    if not dirs:
        return None
    try:
        import importlib.metadata as _im
        for dist in _im.distributions(path=dirs):
            try:
                name = (dist.metadata["Name"] or "").lower()
            except Exception:
                continue
            if name == pkg.lower():
                return dist.version
    except Exception:
        return None
    return None


# ─────────────────────────────────────────────────────────────────────
# Debounce-streak persistence — a consecutive-drift counter used by every
# drift-family probe to suppress a first-seen transition. Historically named
# "parity_streak" (born in the parity probe) but generic; lives here so the
# drift/liveness/env probe modules share ONE copy instead of a per-module fork
# (honest_failure_modes #5 — one constant, not several).
# ─────────────────────────────────────────────────────────────────────


def _load_parity_streak(state_path: str) -> int:
    """Read the consecutive-drift streak counter.

    A MISSING file means 'no confirmed streak yet' → 0, which suppresses a
    first-seen drift: the conservative direction the debounce wants.

    But an UNWRITABLE path is a different question wearing the same answer.
    The 2026-07-21 (W4) review found that a broken state dir (the #60
    sandbox-drift class) froze every debounced probe's streak below its
    threshold and silenced them all forever — and fixed only the WITNESS half
    (the warning in ``_save_parity_streak``). The suppression itself survived:
    load→0 every tick means streak is always 1, and any probe with threshold ≥2
    could never fire. Re-found by drill 2026-07-26.

    So: prefer the value this process wrote. Within one process the in-memory
    copy is always at-least-as-new as the file (it is written unconditionally
    at the top of ``_save_parity_streak``), and the file exists to survive a
    RESTART; it was never the per-tick mechanism, and the runner is
    long-lived. Consulting memory only on a FAILED read left the readable-
    stale route open: a broken state dir usually keeps the OLD file readable
    while every write fails (ro-remount, ENOSPC, perms flip), so the stale
    disk value won every tick and the debounce stayed silenced anyway
    (drill-proven 2026-07-26). Disk is read only when this process has no
    entry yet — i.e. at process start. Uncertainty still favours silence —
    but 'I could not read the disk' is no longer allowed to masquerade as
    'the drift just started' (honest_failure_modes #1/#2).
    """
    if state_path in _streak_mem_fallback:
        return max(0, int(_streak_mem_fallback[state_path]))
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_parity_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises).

    Records the value in-process FIRST, so ``_load_parity_streak`` can hold it
    when the disk write fails — the streak then survives per-tick in-process;
    only restart survival is lost while the path stays broken.
    """
    _streak_mem_fallback[state_path] = max(0, int(streak))
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError as e:
        # 2026-07-21 review (W4): this swallow had NO witness — an unwritable
        # state dir (the #60 sandbox-drift class) went undetected. Still never
        # raises (a probe must not crash on bookkeeping), but the blindness is
        # visible: #63 pattern — ERROR on first failure, counted while
        # failing, INFO with the count on recovery.
        prior = _streak_write_errors.get(state_path, 0)
        _streak_write_errors[state_path] = prior + 1
        if prior == 0:
            logger.error(
                "watchdog streak state write FAILED (%s): %s — debounce "
                "streaks are held in-process only and will NOT survive a "
                "restart until this is fixed (#60)", state_path, e)
        else:
            logger.debug("watchdog streak state write still failing "
                         "(%d consecutive, %s): %s", prior + 1, state_path, e)
        return
    if _streak_write_errors.get(state_path):
        logger.info("watchdog streak state write RECOVERED after %d "
                    "consecutive failures (%s)",
                    _streak_write_errors[state_path], state_path)
        _streak_write_errors[state_path] = 0


# ── crontab spool: shared evidence for "does this box run X?" ─────────────
#
# The spool locations, Debian first then RHEL-style. ONE constant in the shared
# base because ≥2 probe modules read the same spool (liveness for #78's wired
# set, gateway for the dup collector), and two independent copies of a path
# WILL drift (honest_failure_modes #5). CRON_SPOOL_PATHS is the per-user
# template view of the SAME constant (liveness formats it with a username).
CRON_SPOOL_DIRS = ("/var/spool/cron/crontabs", "/var/spool/cron")
CRON_SPOOL_PATHS = tuple(d + "/{}" for d in CRON_SPOOL_DIRS)

# "Is this cron wired here" changes when the operator edits a crontab —
# roughly never — while the watchdog asks twice per 30s tick forever. Cached
# per (token, spool-dirs) on a monotonic clock (wall-clock is forgeable on
# this fleet, honest_failure_modes #6). 6 of 9 boxes paid a full spool scan
# per tick for a constant answer (2026-07-28 review).
_CRON_WIRED_TTL_S = 300.0
_cron_wired_cache: Dict[tuple, Tuple[float, Optional[bool]]] = {}


def operator_cron_wired(token: str) -> Optional[bool]:
    """Is a cron whose command contains ``token`` wired on THIS box?

    ``True`` / ``False`` / ``None`` when the spool cannot be (fully) read.

    Exists so a probe can answer "what is my ROLE here" from independent
    evidence instead of inferring it from the absence of the artifact it
    audits — *a checker must not consume the artifact it validates*
    (persistent_issues.md; 2026-07-28 review of the /fleet/dups probes).

    Scans EVERY crontab in the spool directories, deliberately NOT "the
    operator's": the first version resolved an operator via the smallest UID
    owning a live ``/run/user/<uid>/bus`` — a session/linger artifact with
    nothing to do with crontab existence. A box rebooting before linger
    started answered None forever (false "crontab unreadable" noise), and a
    second bus-owning user with a lower UID answered a confident False — the
    silenced-coverage-loss defect this evidence exists to end, one hop
    removed (2026-07-28 review). A file in the spool needs no session to be
    real evidence.

    The states are kept distinct on purpose:
      - token found in ANY crontab        → True
      - every crontab read, token absent  → False  (observed, not here; a box
        with no crontabs at all certainly runs no cron — NOT blindness, or
        every crontab-less box lands in permanent detector_blind noise)
      - any part of the spool unreadable
        and the token not found elsewhere → None   (genuine blindness;
        unobservable is never folded into "not here", honest_failure_modes #2)

    ⚠️ Matching is against the crontab COMMAND text. If you wrap a wired cron
    in a script, keep the token visible on the crontab line — cron passes the
    whole command to sh, where a trailing ``# <token>`` is a comment:
    ``40 * * * * /opt/x/dup_rollup.sh  # fleet_dup_collector``. Otherwise the
    evidence honestly reads "not wired here".

    Reads the spool in-process as root — the watchdog's NoNewPrivileges
    sandbox forbids a privilege change, so shelling out to `crontab -l` is not
    available to it.
    """
    key = (token, CRON_SPOOL_DIRS)
    now = time.monotonic()
    hit = _cron_wired_cache.get(key)
    if hit is not None and now - hit[0] < _CRON_WIRED_TTL_S:
        return hit[1]
    result = _operator_cron_wired_uncached(token)
    _cron_wired_cache[key] = (now, result)
    return result


def _operator_cron_wired_uncached(token: str) -> Optional[bool]:
    blind = False
    for d in CRON_SPOOL_DIRS:
        try:
            names = sorted(os.listdir(d))
        except (FileNotFoundError, NotADirectoryError):
            continue          # spool dir absent here — nothing to read
        except OSError:
            blind = True      # dir exists but cannot be listed
            continue
        for name in names:
            path = os.path.join(d, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except (IsADirectoryError, FileNotFoundError):
                continue      # e.g. Debian's crontabs/ subdir under /var/spool/cron
            except OSError:
                blind = True
                continue
            try:
                jobs = _parse_crontab(text)
            except Exception:
                blind = True
                continue
            for job in jobs:
                if token in (job.get("command") or ""):
                    return True
    return None if blind else False
