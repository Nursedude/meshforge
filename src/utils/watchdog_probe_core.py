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
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Tuple

# Same "watchdog" namespace the runner logs under, so a swallowed state-write
# failure lands where the operator already greps (honest_failure_modes #9).
logger = logging.getLogger("watchdog")

# Warn once per path per process — a broken state dir would otherwise spam
# every probe every tick.
_streak_write_warned: set = set()
# Last streak this process wrote, per state path. Lets _load_parity_streak
# HOLD a debounce across an unwritable-disk window instead of resetting to 0
# every tick, which silenced every debounced probe forever (found 07-21,
# witness-only fix; suppression closed 07-26).
_streak_mem_fallback: dict = {}

# ─────────────────────────────────────────────────────────────────────
# Closed enum of failure classes — one per persistent_issues.md entry.
# Each class maps to a probe function below. To add a class, add it
# here AND add a row to persistent_issues.md.
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
    "aredn_organ_undeclared",  # 2026-07-20: an AREDN node answers sysinfo on this box's LAN (localnode.local.mesh resolves — it cannot, without an AREDN node serving DNS) while the box neither configures aredn_node_ips NOR declares organ_expectations.aredn — the organ is physically available and dormant, which is the exact 2026-06-12 Phase-0 state that stayed invisible because BOTH existing legs require a statement the operator had to remember to make. Closes the last leg of the aredn_configured_source_only structural-dark row by POSITIVE evidence (a forgotten declaration is unfalsifiable by absence). Deliberately no subnet scan (the map collector dropped its default-host walk for cost); INERT the moment either statement exists, so one fault keeps one owner.
    "lxmf_propagation_unused",  # 2026-07-20: LXMF propagation nodes are ANNOUNCING on the RNS network (the gateway parses them into its own node cache) while gateway.json rns.propagation_node is empty — store-and-forward to OFFLINE peers is available and unadopted, so an LXMF message to a peer that is currently down simply fails. The second shape-C signal after aredn_organ_undeclared: found by the 2026-07-20 optional-organ sweep, which showed that of ~50 signal classes only ONE watched for an available-but-unadopted capability — everything else waits to be told. Evidence is config-free and deliberately NOT the journal (fleet boxes run Storage=volatile, so journal absence proves nothing): the operator-owned rns_nodes.json cache carries service_type + last_seen. INERT when propagation_node is set, when no gateway.json exists, or when no cache exists; a STALE cache HOLDS rather than claiming availability from dead bytes. Adoption is a TRUST decision (a propagation node sees stored-traffic metadata), so the probe reports and never prescribes a specific node.
    "lxmf_propagation_node_dark",  # 2026-07-20: the CONFIGURED gateway.json rns.propagation_node has gone quiet — either STALE (in the RNS node cache but no announce for several announce periods: it answered and stopped) or UNHEARD (the hash is not in the cache at all, i.e. a wrong/truncated hash, the failure adoption itself introduces). The shape-A companion that had to ship WITH adoption: the moment propagation_node is set, lxmf_propagation_unused goes INERT by design, so without this leg the fleet would trade a WATCHED gap for an UNWATCHED dependency — strictly worse than before. Same durable operator-owned rns_nodes.json evidence (never the journal, Storage=volatile). Guard that makes it honest: it fires ONLY when some OTHER propagation announce reached the box inside the window, which is positive proof the box can hear the class — an RNS-wide wedge therefore holds (its own probes own it) instead of being relabelled as this node's death. INERT when propagation_node is empty (one fault, one owner — slice 1 owns that gap), when no gateway.json exists, and when no cache exists.
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
    "gateway_dual_homed_exposure",  # 2026-07-19 row-8 accept, LEADING indicator: a recipient became reachable from >1 gateway — the PRECONDITION for a cross-gateway duplicate, not a duplicate. Row 8 was accepted-permanent on cost asymmetry (a dup is redundancy; a yield-protocol bug is silence, and emergency comms must fail toward redundancy), NOT on dups being rare — three human recipients were already dual-homed the day it was accepted. So the fleet instruments the CONDITION (always countable, moves first) instead of only the OUTCOME (rare, bursty, unobservable on the mesh leg). Fires on a NEWLY-observed dual-homed recipient, never on the count (which churns with the rollup window); once known a recipient stays known, so it cannot re-fire on churn. ⚠️ Derived from the CONFIRMED set, so it measures exposure in the CONFIRMABLE population only — mesh recipients never confirm and never appear; extending to attempted/routing state is gateway-side and remains the residual. No issue#.
    "host_frozen",  # 2026-06-17 Leg C: the dude-claw out-of-band witness (host_probe tool over NATS, on the watched box's own subnet) reports a target's verdict — HOST_FROZEN (IP stack answers but the app port serves no banner = kernel alive / userspace swap-wedged, the .32 class the self-petted HW watchdog can't catch), UNREACHABLE (no TCP answer = host/path/SoC down), or UNKNOWN (the claw witness itself couldn't be reached, sustained → lost visibility, not "healthy"). An out-of-band collector cron on the claw's brain box writes the verdict file; this probe reads it (no NATS in the sandboxed watchdog), mirroring fleet_box_unreachable's file-read pattern. Self-guards INERT off the brain box (no verdict file) and on a stale file. Alert-only (propose_escalation). No issue#.
    "ntfy_loopback",  # 2026-06-18 ntfy receipt-heartbeat Phase 2: the alerting spine's OWN liveness. A manager-box collector (scripts/fleet_ntfy_loopback.sh) publishes a nonce'd min-priority heartbeat to the FLEET topic + polls ntfy.sh to confirm it loops back, escalating via the Phase-1 EMAIL backbone on a miss (ntfy is the suspect channel, so it does NOT page back through ntfy); this READ-ONLY probe reads the verdict file and surfaces a miss into mini/+/fleet — the "send ≠ receipt" lesson aimed at the spine itself (the 2026-06-14→17 dark incident). Catches ntfy.sh-down / fleet-topic-publish-broken / sender-no-op; the operator-phone-on-wrong-topic case is Phase 3's tap-to-ack job. INERT off the manager box; stale verdict → cron_verdict_stale owns the dead-cron alert. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as fleet_box_unreachable/host_frozen). No issue#.
    "ntfy_ack_stale",  # 2026-06-18 ntfy receipt-heartbeat Phase 3: the only rung that confirms the HUMAN's DEVICE. A manager-box cron (scripts/fleet_ntfy_ack.sh) sends a WEEKLY tap-to-ack page to the fleet topic with an ntfy action button; the tap makes the PHONE POST to a dedicated ack-topic (<fleet>-ack), which the cron polls. consecutive_unacked_pings grows each unacked week (reset on ack); the cron escalates via the Phase-1 EMAIL backbone at ≥2 unacked (~2 weeks dark), and this READ-ONLY probe surfaces it into mini/+/fleet. Catches exactly the 2026-06-14→17 incident (phone on a wrong/dead topic, app killed, notifications off) — what loopback (Phase 2, a different subscriber) structurally cannot. INERT until first pinged; stale state → cron_verdict_stale owns the dead-cron alert. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as ntfy_loopback). No issue#.
    "meshtasticd_vsz_leak",  # 2026-07-10 (upstream meshtastic/firmware#10468, the operator's own 2026-05-13 report, re-confirmed live 07-10 on both Pi5 boxes): meshtasticd on Pi5+USB leaks exited pthread stacks (~110 GB VSZ/day, RSS bounded, mmap regions 70k+); the weekly meshtasticd-restart.timer band-aid was UNWATCHED — fires only past the weekly envelope (default 768 GB) = the restart missed or the rate worsened. Pi4/SPI boxes idle ~0.3 GB and cannot trip it. Documented inline (MF012 cap precedent). No own issue#.
    "gateway_delivery_degraded",  # 2026-06-20 gateway-reliability arc A2: the gateway's OWN self-report (att/del/drop journal block + its RNS resource/forward error channel) shows it is NOT delivering — OUTCOME monitoring, not shape-enumeration. Leg 1 = windowed delivered/attempted ratio collapse (recent, high-volume; conservative floor because the journal's total-dropped folds in benign Mesh→RNS broadcast misses — the precise lens is delivery_confirmation_stall); leg 2 = a spike of EROFS / resource-assembly / forward-to-secondary errors, the exact 2026-06-20 wx-total-loss witness class that HAD a journal witness but no probe consumer (honest_failure_modes #9 at the spine level; #60 sandbox class — gateway shared-instance RNS client couldn't write assembled Resources under /etc/reticulum/storage). INERT off a box that doesn't run meshforge-gateway (moc/moc3 only); journalctl-unobservable holds, observed-clean resets, 2-tick debounce. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as nomadnet_crashloop/calibration_drift). No own issue#.
    "resource_canary_degraded",  # 2026-06-20 gateway-reliability arc A1 (the OUTCOME source of truth): the synthetic RESOURCE round-trip canary (meshforge-gateway-resource-canary.timer → src/lab/gateway_resource_canary) FAILED its verdict or went DARK. Where A2 reads the gateway's self-report, A1 actively PROVES the gateway delivers a multi-chunk RNS Resource round-trip — the exact path the 2026-06-20 wx-total-loss EROFS broke while single-packet replies kept working (so every shape/liveness probe and the single-packet gateway_rt_canary read green). The canary fires a control PING + a PINGBIG whose reply is resource-sized; its own FAIL "control back, resource NOT" is the EROFS signature. This probe consumes the verdict envelope (last.json): FAIL/CONCERN verdict OR a stale file (silence = the failure mode for a fixed-cadence canary). degraded only (gateway_delivery_degraded/delivery_confirmation_stall own the hard-failure surface). INERT off a box that doesn't run the canary (state dir absent); 2-tick debounce; the "canary itself must be watched" pattern, mirroring synth_soak_degraded. Documented inline (no persistent_issues.md row — MF012 40k cap). No own issue#.
    "nomadnet_crashloop",  # 2026-06-19: the NomadNet USER systemd unit is crashlooping (systemd 'restart counter is at N' under the USER_UNIT= journal field) — probe_service_inactive is structurally BLIND to user units (root/system-context systemctl can't see them at all, and a unit thrashing in auto-restart is neither inactive nor failed), so the NRestarts=7842 loop went 10 days silent. Root-direct USER_UNIT= journal read (no sudo — watchdog sandbox); SHORT live-window + a newest-restart recency gate so post-fix history can't false-page. INERT on a healthy/disabled/never-installed unit (moc5) and when journalctl is unobservable. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as ntfy_loopback/ntfy_ack_stale). Cross-refs the #69-fix-regression (the rnstatus boot-gate fix); no own issue#.
    "oracle_delivery_degraded",  # 2026-06-22 mesh-oracle health: the read-only "ask dude-AI over the mesh" responder (src/oracle) answered queries but its confirmable delivery rate (delivered / (delivered + send_errors), over a recent ts window of its audit log) fell below threshold — the oracle was the one live service with NO automated probe (a blind spot for a service whose ethos is "silence is the failure mode"). Intentional declines (cooldown / not_allowlisted) and benign non-deliveries (reason-less delivered:false — RNS no-path to an unannounced ephemeral identity / MeshCore restart race) are EXCLUDED from the failure set + surfaced, so the rate is the #74 confirmation view, not a false alarm on a cooldowned/quiet channel. WITNESSED v1 blind spot: the RNS leg's send_to_rns swallows real send exceptions to a bare False, so an RNS send error lands in the benign bucket (not send_error) — v1 covers the Meshtastic/MQTT/MeshCore legs' send_error fully + makes the RNS gap visible via the benign count; closing it needs send_to_rns to distinguish no-path from crash (deferred out of the mf.5 RNS soak). degraded only (low-traffic read-only service); min-sample guard (no silence leg — a reactive service that nobody queried is not "broken"); INERT off a box where the oracle never wrote a log. Documented inline (no persistent_issues.md row — MF012 40k cap; same precedent as resource_canary_degraded). No own issue#.
    "inherited_app_drift",  # 2026-06-21 upstream-app ownership Action 5: an INHERITED (non-Nursedude-origin) upstream app checkout on this box carries an unversioned tracked-file CODE patch — a hand-edit that exists in NO repo we control and is one `git pull` from silent deletion (the rescued .32 + dev-box bot patches were exactly this; policy §4.2). LOCAL problem-class detection: scans the top level of the operator home + /opt, reads .git/config to classify owned-vs-inherited (no PINS.md coupling — honest_failure_modes #5), runs `git status --porcelain --untracked-files=no` so untracked config/build artifacts (Raven raven.conf, ucode build/) and machine-generated dependency manifests/lockfiles (MeshSense npm package*.json churn) never false-fire. The floating-`main`/pin-drift leg is deliberately NOT a local fire (the fleet's enforcement is "record the pin, never auto-pull" per PINS.md, NOT detached HEAD — firing on "on a branch" would contradict that and page every intentionally-pinned moc5 app). INERT (None) on a box with no inherited checkouts (moc1/2/3); git/origin-unreadable repos are skipped (indeterminate ≠ clean); 2-tick debounce rides an operator mid-edit. degraded only. Documented in .claude/plans/upstream_app_ownership_policy_2026_06_21.md §9; no own issue#.
    "router_scout_degraded",        # 2026-07-11 OpenWrt-router arc: a mirrored meshforge-scout tick (~/.local/share/meshforge/router_scout/<device>_tick.json, landed by scripts/router_scout_pull.sh over the existing ssh channel) shows the ROUTER-side agent degraded — fresh mirror but stale captured_at (the agent cron went dark on the router while the pull keeps re-copying the same old tick), tick ok=false (the agent's own tri-state witnesses: tmpfs data_dir, unreadable /proc, dead radio TCP), or an unparseable mirrored tick (the pull validates before writing, so garbage = writer/shape drift, not a torn read). DEFENSE-IN-DEPTH: the pull's own eval also FAILs cron_verdict on these — this probe adds the watchdog-spine surface (per-device subject into /fleet + mini) and covers mirrors landed by any other path. degraded only — every condition observed is REMOTE (the tracer_peer_unreachable lesson). INERT off boxes with no mirror dir; a STALE mirror file is skipped (dead pull cron = cron_verdict_stale's beat, router_scout is verdict-wired). Documented inline (no persistent_issues row — MF012 40k cap; same precedent as meshtasticd_vsz_leak). No own issue#.
    "gateway_dup_degraded",         # 2026-06-29 dedup/identity arc STEP 5 — the FIRST probe with a per-logical-message + cross-gateway dimension. Consumes the 4c cross-box rollup (/fleet/dups): a fleet DUPLICATE is the same (content_id, recipient) CONFIRMED by >1 DISTINCT gateway — the live dup-A (moc 3dfbdb5d + moc3 f68c2f56 both -> 6b1a0120 under two LXMF source hashes a stock client cannot collapse). degraded only (a dup is a quality/cost defect, not an outage — delivery still happened). Honest self-guards built on the 4c JOIN indeterminate gate: status!=ok (<2 contributing gateways reachable — the rollup only exists on the manager box running the collector cron) -> None+HOLD streak; freshness.stale (dead collector) -> None+HOLD; observed-clean resets; 2-tick debounce. ALERTS only — cross-gateway suppression is the separately-gated STEP 6. Documented inline (no persistent_issues row — MF012 40k cap; same precedent as resource_canary_degraded). No own issue#.
    "dream_ratification_stalled",   # 2026-07-22 second-brain arc WS-C: mini's dream propose->ratify VALUE loop runs but is not closing — >=N proposals sit UNRESOLVED past a long age WHILE the mini_cadence session is demonstrably alive (its cron verdict fresh + OK). The 2026-07-18 sweep found 0/164 ratified over 7 weeks and mini itself had no idea. NON-redundant with cron_verdict_stale (#78): that owns the cadence-DOWN case; this fires ONLY when the loop is up but the backlog isn't clearing. Scopes to the manager box for free (no mini_cadence verdict => no ratifier => INERT — a backlog on a gateway box is expected, not a stall). degraded only, escalate-only in seed (NO ntfy page) until soaked — the calibration_drift 34-day precedent. Reads the deltas file + ~/cron_verdicts.log root-safe; self-guards inert/indeterminate on absence/unreadable. Documented inline (no persistent_issues row — MF012 40k cap; same precedent as gateway_dup_degraded). No own issue#.
    "host_memory_pressure",         # 2026-07-24 manager-box hard-reset arc (8th): the box is running out of RAM and nobody was watching. Two legs — MemAvailable/MemTotal under 20% (wedge under 8%), and /proc/pressure/memory some/avg60 over 10% (wedge over 40%); worst wins, and either can fire alone. Born from a reset whose forensics proved the mechanism and lost the culprit: MemAvailable fell 9.85->2.56 GB in 2 min with ext5v flat at 5.05 V and throttled=0x0 (brownout/thermal positively excluded), then the box died mid-journal-line with ~2.5 GB still free and NO oom-kill — it was the HARDWARE watchdog, because a memory stall starved PID 1 past RuntimeWatchdogUSec=1min. Nothing detected it and nothing recorded WHO: /tmp is tmpfs (wiped by the reset), sysstat is ENABLED="false", and power_history.log logs how much went, never who took it. So the detail carries the top-5 RSS roster, making the signal the post-mortem witness that arc has never had. degraded debounced 2 ticks (a pytest run or a headless-chromium shot legitimately dips a small box under 20% for one tick); WEDGE fires immediately — the 07-24 reset landed 34 s after the first sub-20% sample. PSI absent => availability leg judges alone and the detail SAYS so (a one-legged verdict is never dressed as agreement). Documented inline (no persistent_issues row — MF012 40k cap; same precedent as meshtasticd_vsz_leak). No own issue#.
    "local_brain_regressed",        # 2026-07-22 second-brain arc WS-D: makes the LEARNING record observable. A local-brain eval case that passed >=N times before now FAILS — the tier-L model lost a capability it demonstrably had. NON-redundant with cron_verdict_stale (#78) + the weekly --gate: those judge only the AGGREGATE pass_rate, so a single case (or a whole thin kind can regress while the oracle-dominated aggregate stays green and the gate stays silent. PER-CASE + cursor-robust: compares each case's own pass/fail history across ledger records, so budget-chunked partial runs (different subset each week) don't cause false drops. MODEL-AWARE since 2026-07-25: history is scoped to the model of the most-recent run, because a backend/model A/B (the ditch-ollama plan's Step 2/3 method) would otherwise turn "a different model answered" into "the tier lost a capability"; a model swap resets the baseline, which under-fires rather than pages falsely. Scopes to the manager box for free (no ~/local_brain_evals.jsonl => tier-L not evaluated here => INERT). degraded only, escalate-only in seed (NO ntfy page). Reads the eval results ledger root-safe; self-guards inert/indeterminate on absence/unreadable. Documented inline (no persistent_issues row — MF012 40k cap; same precedent as dream_ratification_stalled). No own issue#.
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

def _resolve_main_pid(
    service_name: str, *, systemctl_path: str = "systemctl",
) -> Optional[int]:
    """``systemctl show -p MainPID <service>`` parser. Returns None
    on any failure (including inactive service which reports
    ``MainPID=0``)."""
    try:
        proc = subprocess.run(
            [systemctl_path, "show", "-p", "MainPID", "--value", service_name],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        pid = int(proc.stdout.strip())
    except (ValueError, TypeError):
        return None
    return pid if pid > 1 else None


def _read_deployment_declaration(service_user) -> Tuple[Optional[str], dict]:
    """Read ``(role, service_overrides)`` from the service user's deployment.json.

    The watchdog runs as sandboxed root: ``get_real_user_home()`` (which
    ``provision_role.py`` uses at import time) would resolve to ``/root`` here,
    so the home is derived from the service user and READ directly — never
    escalate/switch user (the rns_version_drift lesson). Any unreadability →
    ``(None, {})`` = indeterminate, never false-alarm.
    """
    if not service_user:
        return None, {}
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "deployment.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        role = data.get("role")
        ov = data.get("service_overrides") or {}
        return (role if isinstance(role, str) and role else None,
                ov if isinstance(ov, dict) else {})
    except (KeyError, OSError, ValueError, TypeError):
        return None, {}

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
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit, "--since", f"-{lookback}",
                "-g", pattern, "-r", "-n", "1", "-o", "short-unix",
                "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    # rc 1 = "no entries matched" on some systemd builds; only >1 is an error.
    if proc.returncode not in (0, 1):
        return None
    lines = proc.stdout.strip().splitlines()
    return lines[0] if lines else None


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

    So: fall back to the last value this process wrote. The file exists to
    survive a RESTART; it was never the per-tick mechanism, and the runner is
    long-lived. Uncertainty still favours silence — but 'I could not read the
    disk' is no longer allowed to masquerade as 'the drift just started'
    (honest_failure_modes #1/#2).
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return max(0, int(_streak_mem_fallback.get(state_path, 0)))


def _save_parity_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises).

    Records the value in-process FIRST, so ``_load_parity_streak`` can hold it
    when the disk write fails — otherwise the warning below correctly reports
    that debounced probes cannot reach their threshold, and nothing makes them
    able to.
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
        # state dir (the #60 sandbox-drift class) froze every debounced
        # probe's streak below its threshold, silencing them all FOREVER
        # with nothing anywhere saying so. Still never raises (a probe must
        # not crash on bookkeeping), but the blindness is now visible.
        if state_path not in _streak_write_warned:
            _streak_write_warned.add(state_path)
            logger.warning(
                "watchdog streak state unwritable (%s): %s — debounced "
                "probes using this path CANNOT reach their fire threshold "
                "until this is fixed", state_path, e)
