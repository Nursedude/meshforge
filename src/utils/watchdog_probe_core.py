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
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Tuple

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
    "dep_version_drift",  # 2026-06-12: a critical pip dep (meshtastic) installed BELOW the requirements/core.txt floor in the service user's env — a box that missed/failed an update. rns/lxmf have their own fork-pin probe; this covers the meshtastic-lib gap nothing watched (the recurring update class, feedback_version_env_rigor)
    "dep_install_fragmented",  # 2026-06-17: meshtastic installed at DIVERGENT versions across root-readable locations (venv vs system-wide dist-packages vs root/user pipx vs user-site) with ≥1 copy BELOW the core.txt floor — the service consumer-of-record can be fine while the TUI-as-root reads a stale stray and shows phantom "update available". The install-fragmentation half of the recurring update class; dep_version_drift watches only the service-user consumer and is blind to strays (feedback_version_env_rigor)
    "user_unit_inactive",  # 2026-07-19: an enrolled always-on USER .service is not running — enabled in default.target.wants but no invocation:* marker under /run/user/<uid>/systemd/units (bus-free, root-readable both sides), or the user manager itself is down while daemons are enrolled (linger off — the #79 class). Closes user_unit_inactivity_blind: probe_service_inactive can't see user units, nomadnet_crashloop covers only a LIVE loop on one unit; the parked-failed (StartLimitBurst) / stopped-and-forgotten / manager-down modes had NO steady-state detector. Timers deliberately out of scope (no invocation marker; schedules/SLO layer owns their staleness).
    "rns_stray_env_drift",  # 2026-07-19: rns/lxmf copies across this box's root-readable envs DISAGREE — intra-box coherence, the missed-venv half of the roll hazard (probe_rns_version_drift owns pin compliance). Pipx globs WILDCARDED across venv names because a library rides inside every app venv depending on it: moc3's nomadnet pipx venv sat silently stock 1.1.4 while the box's consumer ran the fork pin, invisible to every prior drift probe. One shared rnsd per box → every env must carry the identical RNS substrate (RPC framing mismatch = 8s timeouts). Closes the rns/lxmf leg of the dep_version_drift_strays_blind structural-dark row.
    "synth_soak_degraded",  # 2026-06-15: the hourly LXMF synth soak (meshforge-synth-soak.timer) FAILED its delivery envelope or went DARK — the gateway's real round-trip exerciser writes a pass/fail envelope but the fire script always exits 0 and nothing consumed it, so a delivery regression or a silent timer was invisible (the "canary itself unwatched" gap; silence-as-failure for a fixed-cadence generator)
    "calibration_drift",  # 2026-06-15: a VERIFIED completion claim (Claude's "done/100%/all green") did NOT hold when re-derived against external ground truth — the calibration spine turned on the assistant itself, so "you said 100% and the math was wrong" becomes a tracked number instead of a private impression. SSOT .claude/rules/calibrated_claims.md (NOT a code/fleet bug → no issue#). Self-guards None off the dev/manager box (no ledger).
    "fleet_box_unreachable",  # 2026-06-17 Leg D: a fleet box the offline-monitor (fleet_offline_check.sh on the manager box) has confirmed DOWN past its 3-fail (~15min) threshold and is re-paging — surfaced into mini's brief + /fleet so a dark box can't sit silent in a side-channel logfile (the .32 33h-dark lesson; the monitor owns the ntfy, this is visibility). Self-guards INERT off the manager box (no state file) and on a stale state file (cron_verdict_stale owns the dead-monitor case — fleet_offline_check is verdict-wired). No issue#.
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
    """Read the consecutive-drift streak counter. Best-effort: any error → 0.

    A missing/unreadable/garbage state means 'no confirmed streak yet', which
    suppresses a first-seen drift — exactly the conservative direction the
    debounce wants (favour silence on uncertainty, not a false page).
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_parity_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass
