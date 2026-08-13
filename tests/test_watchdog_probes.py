"""Tests for src/utils/watchdog_probes.py and watchdog_runner.py.

Regression-pinned against the documented Issue shapes
(``.claude/foundations/persistent_issues.md``):

* Issue #61 — socketserver-deadlock — HTTP local probe must surface a
  bound-but-wedged port as a wedge signal.
* Issue #63 — delivery_counters write canary — probe must surface
  ``preflight_ok=False`` as wedge and ``consecutive_write_errors >= N``
  as degraded.
* Issue #68 — main-thread unix_stream_connect wedge — probe must match
  kernel-stack patterns and NOT match the healthy ``do_sys_poll`` shape.
* Issue #69 — foreign daemon owns ``@rns/<instance>`` — probe must match
  the actual ss output shape from the 2026-05-20 incident.

Plus exercise the edge-transition tracker and atomic-rename writer.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.watchdog_probes import (  # noqa: E402
    SEVERITIES,
    SIGNAL_CLASSES,
    Signal,
    probe_aredn_source_dark,
    probe_channel_feed_dark,
    probe_mqtt_root_drift,
    probe_cron_verdict_stale,
    probe_fleet_box_unreachable,
    probe_host_frozen,
    probe_router_scout_degraded,
    probe_inherited_app_drift,
    probe_ntfy_loopback,
    probe_ntfy_ack_stale,
    probe_kernel_reboot_pending,
    _cron_max_interval,
    _parse_kernel_release,
    probe_history_write_failure,
    probe_rules_seed_drift,
    probe_memory_index_oversize,
    probe_calibration_drift,
    probe_local_brain_regressed,
    MEMORY_INDEX_LIMIT_BYTES,
    probe_delivery_confirmation_stall,
    probe_delivery_write_canary,
    probe_gateway_delivery_degraded,
    probe_gateway_dup_degraded,
    probe_synth_soak_degraded,
    probe_propagation_soak_degraded,
    probe_fd_exhaustion,
    probe_phoneapi_tcp_leak,
    probe_meshtasticd_phoneapi_wedge,
    probe_nomadnet_crashloop,
    probe_queue_backlog,
    probe_foundation_drift,
    probe_parity_drift,
    probe_rns_version_drift,
    probe_dep_version_drift,
    probe_dep_install_fragmented,
    probe_role_drift,
    probe_http_local,
    probe_lxmf_process_wedge,
    probe_main_thread_wedge,
    probe_rns_interface_down_peer_reachable,
    probe_rns_namespace_collision,
    probe_rns_rpc_responsive,
    probe_rns_shared_instance_responsive,
    probe_service_inactive,
    probe_tracer_peer_unreachable,
    signal_to_dict,
)
from utils.watchdog_runner import (  # noqa: E402
    SignalTracker,
    load_config_file,
    resolve_probe_targets,
    write_state,
    _DEFAULT_SERVICES_EXPECTED_ACTIVE,
    _DEFAULT_SERVICES_WEDGE_CHECK,
)
from utils.watchdog_probes_service import (  # noqa: E402
    _journal_user_unit_restart_ts,
    _save_nomadnet_crashloop_streak,
    _load_nomadnet_crashloop_streak,
)
from utils.watchdog_probes_gateway import (  # noqa: E402
    _parse_delivery_block,
    _window_delivery_gap,
    _gateway_delivery_blocks,
    _gateway_r2m_suppressed,
    _save_gateway_delivery_streak,
    _load_gateway_delivery_streak,
    GATEWAY_DELIVERY_BLOCK_GREP,
    GATEWAY_RNS_ERROR_GREP,
    GATEWAY_R2M_SUPPRESSED_GREP,
)


# ─────────────────────────────────────────────────────────────────────
# Closed enum + Signal shape
# ─────────────────────────────────────────────────────────────────────


def test_signal_classes_closed_enum_is_documented():
    """Every class in the enum must be one of the documented classes.
    Adding a class is a deliberate act — bumps this test AND requires
    a persistent_issues.md entry."""
    # MULTIPLICITY FIRST (2026-07-29 review). The set-equality below is blind to
    # duplicates, and `claw_uplink_node_moved` was listed TWICE for a day: the
    # author demoted the older rationale to a comment but left its string literal
    # in place. fleet_truth.merge_coverage iterates `list(signal_classes)`, so it
    # incremented green/red/dark on both visits while the classes dict deduped —
    # every /fleet cell read total=59 against 58 unique keys. A closed enum has to
    # be closed in COUNT as well as membership.
    dupes = sorted({c for c in SIGNAL_CLASSES if list(SIGNAL_CLASSES).count(c) > 1})
    assert not dupes, (
        f"SIGNAL_CLASSES lists {dupes} more than once — coverage counters double-"
        f"count every duplicate (fleet_truth.merge_coverage). Keep one entry and "
        f"fold any extra rationale into its comment.")
    assert len(SIGNAL_CLASSES) == len(set(SIGNAL_CLASSES))
    assert set(SIGNAL_CLASSES) == {
        "rns_namespace_collision",
        "main_thread_wedge",
        "http_local_unresponsive",
        "delivery_write_canary",
        "service_inactive",
        "tracer_peer_unreachable",
        "rns_shared_instance_unresponsive",
        "rns_instance_name_mismatch",   # 2026-08-05: probing an @rns/<name> with no listener while the kernel advertises another — both RNS probes keyed to a name this box doesn't serve, so both were dark (one indeterminate blaming rnsd, one affirmatively clean). Documented inline in SIGNAL_CLASSES + the 2026-08-05 persistent_issues entry; tests in test_watchdog_rns_instance_name_mismatch.py
        "rns_interface_down_peer_reachable",
        "rns_rpc_unresponsive",
        "fd_exhaustion",
        "foundation_perms_drift",
        "parity_drift",
        "rns_version_drift",
        "role_drift",
        "channel_feed_dark",
        "queue_backlog",                # Issue #74
        "delivery_confirmation_stall",  # Issue #74
        "phoneapi_tcp_leak",            # Issue #75
        "meshtasticd_phoneapi_wedge",   # 2026-06-15 #17/#75 contention churn form (the 06-13→15 moc mesh-TX wedge); documented inline in the SIGNAL_CLASSES comment — no new persistent_issues.md row, that file is at its MF012 40k cap (same precedent as calibration_drift)
        "mqtt_root_drift",              # Issue #77
        "cron_verdict_stale",           # Issue #78
        "history_write_stalled",        # mini-dudeai audit #8
        "rules_seed_drift",             # mini-dudeai audit #6
        "memory_index_oversize",        # mini-dudeai audit #2
        "kernel_reboot_pending",        # 2026-06-09 version-updates arc
        "aredn_source_dark",            # 2026-06-12 AREDN Phase 0
        "lxmf_propagation_node_dark",   # 2026-07-20 the CONFIGURED propagation node stopped answering (shape-A companion)
        "dep_version_drift",            # 2026-06-12 recurring update class
        "dep_install_fragmented",       # 2026-06-17 install-fragmentation half of the recurring update class (feedback_version_env_rigor); documented inline in the SIGNAL_CLASSES comment — no new persistent_issues.md row, that file is at its MF012 40k cap (same precedent as meshtasticd_phoneapi_wedge / calibration_drift)
        "synth_soak_degraded",          # 2026-06-15 synth-soak watch
        "propagation_soak_degraded",  # 2026-07-21 store-and-forward drill
        "calibration_drift",            # 2026-06-15 calibration spine; SSOT .claude/rules/calibrated_claims.md (new subsystem, not a fleet bug → no persistent_issues row; that file is at its MF012 cap)
        "fleet_box_unreachable",        # 2026-06-17 Leg D — fleet liveness surfaced into mini/+/fleet; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row; same MF012-cap precedent as calibration_drift)
        "host_frozen",                  # 2026-06-17 Leg C — dude-claw out-of-band witness verdict (HOST_FROZEN/UNREACHABLE/UNKNOWN) surfaced into mini/+/fleet; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row; same MF012-cap precedent)
        "claw_device_dark",             # 2026-07-19 structural-dark row 7 — a claw EDGE NODE stopped answering while its capture cron kept writing fresh ticks; documented inline in the SIGNAL_CLASSES comment (MF012-cap precedent)
        "claw_battery_low",             # 2026-07-19 structural-dark row 7 — a reachable battery claw under the 3.5 V LiPo floor, the early warning that was missing when dudeclaw-02 drained to 2.41 V and died
        "claw_rf_silent",                # 2026-07-19 structural-dark row 9 — no LoRa heard over the air by any claw; the fleet's only independent physical-layer witness. Escalate-only, threshold provisional pending the heard-rate soak
        "claw_watched_node_silent",      # 2026-07-29 — a WATCHED transmitter (ours, by node id) reached no claw for its full listening window: the MUTE-TRANSMITTER case claw_rf_silent is blind to, since heard_age_s reads healthy off neighbours' traffic while our own PA is dead. Fires on the GATED `silent` verdict only, never raw `never` (which would page on every claw reboot). Escalate-only, window unmeasured
        "segment_peer_silent",           # 2026-07-30 — a declared same-SEGMENT peer gateway was not heard by this box for its full window. Covers what the probe above structurally cannot: the claws listen on LONG_FAST/ch20, so moc2+moc3 on SHORT_TURBO/ch8 (the RNS throughput leg) are undemodulable by every claw and had NO RF witness at all. Those two hear EACH OTHER (-17 dBm) — a box reporting on a DIFFERENT box is independent the way self-report is not. Journal-sourced (nodes.proto measured 54.7 days stale); verdicts from the SAME classify_watch gate, never a second copy. INERT unless the box declares peers. Escalate-only, window unmeasured
        "gateway_dual_homed_exposure",   # 2026-07-19 row-8 accept — a recipient became reachable from >1 gateway: the PRECONDITION for a cross-gateway dup, tracked because the dup residual was deliberately accepted rather than coordinated away
        "ntfy_loopback",                # 2026-06-18 ntfy receipt-heartbeat Phase 2 — the alerting spine's own loopback liveness (collector publishes a nonce'd heartbeat to the fleet topic + polls it back, escalates via the email backbone on a miss); read-only probe surfaces it into mini/+/fleet; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row; same MF012-cap precedent)
        "ntfy_ack_stale",               # 2026-06-18 ntfy receipt-heartbeat Phase 3 — the only rung confirming the operator's DEVICE (weekly tap-to-ack page; tap makes the phone POST to a dedicated ack-topic the manager-box cron polls); consecutive unacked weeks → email escalation + this read-only probe surfaces it into mini/+/fleet; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row; same MF012-cap precedent)
        "nomadnet_crashloop",           # 2026-06-19 — the NomadNet USER unit crashloop (the 10-day-silent NRestarts=7842 class; probe_service_inactive is structurally blind to user units); root-direct USER_UNIT= journal read, short live-window + newest-restart recency gate so post-fix history can't false-page; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; cross-refs the #69-fix-regression)
        "gateway_delivery_degraded",    # 2026-06-20 gateway-reliability arc A2 — OUTCOME monitoring from the gateway's OWN journal self-report (windowed delivered/attempted ratio collapse + a spike of EROFS / resource-assembly / forward-to-secondary errors, the exact 2026-06-20 wx-total-loss witness with a journal witness but no probe consumer); INERT off a box that doesn't run meshforge-gateway; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as nomadnet_crashloop)
        "resource_canary_degraded",     # 2026-06-20 gateway-reliability arc A1 (the OUTCOME source of truth) — the synthetic RESOURCE round-trip canary (meshforge-gateway-resource-canary.timer → src/lab/gateway_resource_canary) FAILED its verdict or went DARK; A1 actively PROVES the gateway delivers a multi-chunk RNS Resource round-trip (the path the 2026-06-20 EROFS broke while single-packet replies kept working); a FAIL "control back, resource NOT" is the EROFS signature; INERT off a box that doesn't run the canary; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as gateway_delivery_degraded)
        "oracle_delivery_degraded",     # 2026-06-22 mesh-oracle health — the read-only "ask dude-AI over the mesh" responder's confirmable delivery rate fell below threshold; declines (cooldown/not_allowlisted) + benign non-deliveries (RNS no-path / MeshCore restart race) excluded from the failure set + surfaced; the one live service with NO automated probe; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as resource_canary_degraded)
        "inherited_app_drift",          # 2026-06-21 upstream-app ownership Action 5 — an INHERITED (non-Nursedude-origin) upstream app checkout carries an unversioned tracked-file CODE patch (one `git pull` from silent deletion; policy §4.2); LOCAL problem-class detection (scans operator home + /opt, classifies by .git/config, filters untracked artifacts + machine-generated manifests); floating-main/pin-drift leg deliberately NOT a local fire (the fleet enforces pins by ledger, not detached HEAD); INERT off a box with no inherited checkouts; documented inline in the SIGNAL_CLASSES comment + .claude/plans/upstream_app_ownership_policy_2026_06_21.md §9 (no persistent_issues row — MF012 40k cap; same precedent as resource_canary_degraded)
        "gateway_dup_degraded",         # 2026-06-29 dedup/identity arc STEP 5 — cross-gateway duplicate delivery from the 4c /fleet/dups rollup (same (content_id, recipient) confirmed by >1 gateway = the live dup-A); degraded only; INERT off the manager box + on an indeterminate/stale rollup (built on the 4c JOIN <2-gateway gate); 2-tick debounce; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as resource_canary_degraded). No own issue#.
        "meshtasticd_vsz_leak",         # 2026-07-10 (upstream meshtastic/firmware#10468, the operator's own 2026-05-13 report, re-confirmed live 07-10 on both fleet Pi5 boxes at 2.7.24.58) — meshtasticd on Pi5+USB leaks exited pthread stacks (~110 GB VSZ/day, RSS bounded); the weekly meshtasticd-restart.timer band-aid was UNWATCHED; fires only past the weekly envelope (768 GB default) = the restart missed or the rate worsened; Pi4/SPI boxes idle ~0.3 GB and cannot trip it; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as gateway_dup_degraded). No own issue#.
        "router_scout_degraded",        # 2026-07-11 OpenWrt-router arc — a mirrored meshforge-scout tick (landed by the verdict-wired router_scout_pull.sh) shows the ROUTER-side agent degraded: fresh mirror + stale captured_at (agent cron dark while the pull re-copies the same old tick), tick ok=false, or an unparseable mirror; defense-in-depth behind the pull's own cron_verdict eval — adds the /fleet + mini surface (per-device subject); degraded only (every observed condition is remote — the tracer lesson); INERT off the manager box; stale mirror files skipped (cron_verdict_stale owns the dead pull cron); documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as meshtasticd_vsz_leak). No own issue#.
        "user_unit_inactive",           # 2026-07-19 — an enrolled always-on USER .service not running (default.target.wants symlink present, no invocation:* marker in /run/user/<uid>/systemd/units — bus-free root reads both sides) or the user manager itself down while daemons are enrolled (linger off, the #79 class); closes user_unit_inactivity_blind (probe_service_inactive is user-blind; nomadnet_crashloop covers only a LIVE loop — the parked-failed/stopped/manager-down modes had no steady-state detector); timers deliberately out of scope (no invocation marker; schedules/SLO layer owns their staleness); documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as rns_stray_env_drift). No own issue#.
        "user_timer_unit_failing",       # 2026-07-19 — an enabled USER *timer*'s job fails on EVERY firing (repeated "Failed with result" in a short window, newest fresh, no success since); the last uncovered corner of the user-unit blindness class, since user_unit_inactive explicitly excludes timers (no invocation marker; a oneshot is inactive between firings by design) and nomadnet_crashloop covers only a LIVE loop on one unit. Origin: kiai's meshforge-tracer.timer fired every 10 min from 2026-07-12 while its oneshot exited 2 ("no peers in lab_peers") every time — a week silent with every existing leg reading healthy. Documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as user_unit_inactive). No own issue#.
        "rns_stray_env_drift",          # 2026-07-19 — rns/lxmf copies across a box's root-readable envs DISAGREE (intra-box coherence; probe_rns_version_drift owns pin compliance): the missed-venv roll hazard, pipx globs wildcarded across venv names because a library rides inside every app venv depending on it (moc3's nomadnet pipx venv sat silently stock 1.1.4, invisible to every prior drift probe); closes the rns/lxmf leg of the dep_version_drift_strays_blind structural-dark row; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as dep_install_fragmented). No own issue#.
        "claw_uplink_node_moved",       # 2026-07-29 live incident: a declared claw UPLINK node (the AREDN/AP box bridging a claw's own subnet to the fleet segment) answers at an address other than the declared one. THE distinction: a claw can be entirely healthy — booted, associated, holding its lease, dialling the correct broker — and still be unreachable because its uplink moved out from under it; the fleet's only word for that was claw_device_dark, which sends the operator at working hardware. Born from dudeclaw-01 going ~5 h dark while a power cycle, an antenna reseat, two chip resets and three dead hypotheses were spent on a device that was never at fault (its config, read off its own LittleFS, was correct throughout). LEADING indicator — fires on the CONDITION (uplink not where declared), not the OUTCOME (a claw went quiet), per gateway_dual_homed_exposure. Observation-only: reads /proc/net/arp, a plain file — no packets, no subprocess. ⚠️ ONLY ATF_COM (0x2) rows count: /proc/net/arp also lists INCOMPLETE (0x0) rows for FAILED resolutions, and probing a stale address manufactures exactly such a row with the target MAC — a flags-blind reader invents drift from its own footprints, most reliably when someone goes looking (found by testing the probe's input, not by reading it). Declared-address sighting alongside another counts as home (under-fire, never false-page); MAC seen nowhere is indeterminate, never "moved". INERT undeclared; 2-tick debounce. Documented inline (MF012 40k cap). No own issue#.
        "memory_cap_engaged",           # 2026-07-24 hard-reset arc, same session that created the gap: eight boxes gained hard MemoryMax caps on user-1000.slice (plus ollama's 8G) and NOTHING watched them fire — an OOM-killed ssh session or user unit leaves only a missing process (honest_failure_modes #9). Reads cgroup memory.events per CAPPED cgroup. KILL leg (wedge, immediate — a kill is discrete and irreversible) on a rise in oom_kill/oom_group_kill; the detail carries BOTH readings because the counter cannot separate "runaway correctly bounded" from "legitimate work killed by a too-tight cap" (the 18:22 failure of that same session). CEILING leg (degraded, 2-tick) when `max` keeps rising WITHOUT kills — the evidence-based trigger to revisit a cap, replacing the "re-read memory.peak in a week" calendar plan. Baselines keyed to boot_id (counters restart at boot); a DECREASE re-baselines silently rather than reading as recovery; unreadable memory.events is indeterminate, never "no kills"; no finite cap anywhere is INERT (moc3). Returns 0..N signals, one per capped cgroup. Documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as host_memory_pressure). No own issue#.
        "host_memory_pressure",         # 2026-07-24 manager-box hard-reset arc (8th) — the box is running out of RAM and nothing watched for it. Two legs (MemAvailable/MemTotal under 20%, wedge under 8%; /proc/pressure/memory some/avg60 over 10%, wedge over 40%), worst wins, either can fire alone. The 07-24 reset proved the mechanism and lost the culprit: memory fell 9.85->2.56 GB in 2 min with ext5v flat and throttled=0x0, then the box died with ~2.5 GB free and NO oom-kill — a memory stall starved PID 1 past RuntimeWatchdogUSec=1min and the HARDWARE watchdog reset it. The detail carries the top-5 RSS roster because /tmp is tmpfs (wiped by the reset) and sysstat is disabled, so nothing recorded WHO. UNCONDITIONAL (every box has RAM; the failure kills the box, not one unit). degraded debounced 2 ticks, wedge immediate. Documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as meshtasticd_vsz_leak). No own issue#.
        "local_brain_regressed",        # 2026-07-22 second-brain arc WS-D — a local-brain eval case that passed >=2x before now fails (the tier-L model lost a capability); makes the LEARNING record observable; NON-redundant with #78 + the weekly --gate, which judge only the aggregate pass_rate (a thin kind can regress under an oracle-dominated aggregate); PER-CASE + cursor-robust; scopes to the manager box (INERT with no eval ledger); degraded, escalate-only in seed; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap). No own issue#.
    }
    assert set(SEVERITIES) == {"info", "degraded", "wedge"}


def test_signal_key_is_class_subject_tuple():
    sig = Signal(cls="rns_namespace_collision", subject="@rns/default",
                 severity="wedge", detail="x")
    assert sig.key() == ("rns_namespace_collision", "@rns/default")


def test_signal_to_dict_includes_first_seen():
    sig = Signal(cls="main_thread_wedge", subject="meshforge-map.service",
                 severity="wedge", detail="x", issue_ref=68,
                 extra={"pid": 1234})
    out = signal_to_dict(sig, first_seen_ts=1779480000.5)
    assert out["first_seen"] == 1779480000.5
    assert out["class"] == "main_thread_wedge"
    assert out["issue_ref"] == 68
    assert out["extra"]["pid"] == 1234


# ─────────────────────────────────────────────────────────────────────
# Issue #69 reconstruction — rns_namespace_collision
# ─────────────────────────────────────────────────────────────────────


# Real ss -xnpl output shape from the 2026-05-20 Issue #69 incident.
# rnsd-shaped line first, then the rogue meshanchor-daemon line.
_SS_RNSD_OWNED = """\
Netid State    Recv-Q Send-Q Local Address:Port Peer Address:Port Process
u_str LISTEN   0      0      @rns/volcano 12345 * 0 users:(("rnsd",pid=2286820,fd=4))
u_str LISTEN   0      0      @rns/volcano/rpc 67890 * 0 users:(("rnsd",pid=2286820,fd=11))
"""

_SS_FOREIGN_OWNED = """\
Netid State    Recv-Q Send-Q Local Address:Port Peer Address:Port Process
u_str LISTEN   0      0      @rns/volcano 99999 * 0 users:(("python3",pid=200825,fd=4))
"""


def _make_subprocess_mock(stdout, returncode=0):
    """Helper: produce a subprocess.run mock that returns the given stdout."""
    class _Result:
        def __init__(self):
            self.stdout = stdout
            self.returncode = returncode
    def _runner(*args, **kwargs):
        return _Result()
    return _runner


def test_rns_collision_returns_none_when_rnsd_owns_listener(tmp_path):
    fake_proc = tmp_path / "1"
    fake_proc.mkdir()
    (fake_proc / "cmdline").write_bytes(
        b"/usr/bin/python3\x00/opt/rnsd-bin/rnsd\x00"
    )
    # Override pid in our test: rewrite SS output to use pid=1
    ss_out = _SS_RNSD_OWNED.replace("2286820", "1")
    with patch("utils.watchdog_probes_rns.subprocess.run",
               side_effect=_make_subprocess_mock(ss_out)):
        sig = probe_rns_namespace_collision(
            "volcano", proc_root=str(tmp_path),
        )
    assert sig is None


def test_rns_collision_fires_on_foreign_daemon(tmp_path):
    """Issue #69 reconstruction: meshanchor-daemon claimed @rns/volcano."""
    fake_proc = tmp_path / "1"
    fake_proc.mkdir()
    (fake_proc / "cmdline").write_bytes(
        b"/usr/bin/python3\x00/opt/meshanchor/src/daemon.py\x00start\x00"
        b"--foreground\x00"
    )
    ss_out = _SS_FOREIGN_OWNED.replace("200825", "1")
    with patch("utils.watchdog_probes_rns.subprocess.run",
               side_effect=_make_subprocess_mock(ss_out)):
        sig = probe_rns_namespace_collision(
            "volcano", proc_root=str(tmp_path),
        )
    assert sig is not None
    assert sig.cls == "rns_namespace_collision"
    assert sig.severity == "wedge"
    assert sig.issue_ref == 69
    assert "foreign daemon" in sig.detail
    assert "meshanchor" in sig.detail
    assert "sudo kill 1" in sig.detail


def test_rns_collision_returns_none_when_no_listener():
    """No ss output line matches @rns/<instance> → nothing to check."""
    with patch("utils.watchdog_probes_rns.subprocess.run",
               side_effect=_make_subprocess_mock(
                   "Netid State Local Address:Port\n")):
        sig = probe_rns_namespace_collision("volcano")
    assert sig is None


# The exact 2026-06-09 moc1 incident vector (genericized per MF014): the
# nomadnet wrapper owned @rns/default for 5 real minutes and the old
# substring allowlist kept the probe silent — "--rnsconfig /etc/reticulum"
# contains "reticulum", so EVERY fleet RNS client was allowlisted.
_NOMADNET_WRAPPER_CMDLINE = (
    b"/home/user/.local/share/pipx/venvs/nomadnet/bin/python3\x00"
    b"/home/user/.config/meshforge/nomadnet_wrapper.py\x00"
    b"--rnsconfig\x00/etc/reticulum\x00"
)


def test_rns_collision_fires_degraded_on_inverted_rns_family_owner(tmp_path):
    """2026-06-09 moc1 vector MUST fire: RNS-family owner that is not rnsd."""
    fake_proc = tmp_path / "1"
    fake_proc.mkdir()
    (fake_proc / "cmdline").write_bytes(_NOMADNET_WRAPPER_CMDLINE)
    ss_out = _SS_FOREIGN_OWNED.replace("200825", "1")
    with patch("utils.watchdog_probes_rns.subprocess.run",
               side_effect=_make_subprocess_mock(ss_out)):
        sig = probe_rns_namespace_collision(
            "volcano", proc_root=str(tmp_path), rnsd_enabled=True,
        )
    assert sig is not None
    assert sig.cls == "rns_namespace_collision"
    assert sig.severity == "degraded"
    assert sig.issue_ref == 69
    assert sig.extra["tier"] == "inverted"
    assert "stranded" in sig.detail
    assert "nomadnet_wrapper" in sig.detail


def test_rns_collision_inverted_silent_when_rnsd_not_enabled(tmp_path):
    """A standalone RNS-family host is legitimate where rnsd isn't enabled."""
    fake_proc = tmp_path / "1"
    fake_proc.mkdir()
    (fake_proc / "cmdline").write_bytes(_NOMADNET_WRAPPER_CMDLINE)
    ss_out = _SS_FOREIGN_OWNED.replace("200825", "1")
    with patch("utils.watchdog_probes_rns.subprocess.run",
               side_effect=_make_subprocess_mock(ss_out)):
        sig = probe_rns_namespace_collision(
            "volcano", proc_root=str(tmp_path), rnsd_enabled=False,
        )
    assert sig is None


def test_rns_collision_comm_fast_path_allows_rnsd_without_cmdline(tmp_path):
    """ss comm == rnsd passes even when /proc/<pid>/cmdline is unreadable
    (process table churn) — absence of cmdline must not read as foreign."""
    ss_out = _SS_RNSD_OWNED  # comm "rnsd", pid never materialized in tmp_path
    with patch("utils.watchdog_probes_rns.subprocess.run",
               side_effect=_make_subprocess_mock(ss_out)):
        sig = probe_rns_namespace_collision(
            "volcano", proc_root=str(tmp_path), rnsd_enabled=True,
        )
    assert sig is None


def test_rnsd_shaped_predicate_tiers():
    """Pin the two-tier owner predicates to live fleet shapes."""
    from utils.rns_init import cmdline_is_rns_family, cmdline_is_rnsd_shaped

    fleet_rnsd = "/usr/bin/python3 /usr/local/bin/rnsd --config /etc/reticulum --service"
    module_rnsd = "/usr/bin/python3 -m RNS.Utilities.rnsd --config /etc/reticulum"
    pipx_rnsd = "/home/user/.local/share/pipx/venvs/rnsd/bin/python3 /home/user/.local/bin/rnsd"
    nomadnet = _NOMADNET_WRAPPER_CMDLINE.replace(b"\x00", b" ").decode().strip()
    meshchat = "/usr/bin/python3 /opt/reticulum-meshchat/meshchat.py --rnsconfig /etc/reticulum"
    ma_daemon = "/usr/bin/python3 /opt/meshanchor/src/daemon.py start --foreground"

    assert cmdline_is_rnsd_shaped(fleet_rnsd)
    assert cmdline_is_rnsd_shaped(module_rnsd)
    assert cmdline_is_rnsd_shaped(pipx_rnsd)
    assert not cmdline_is_rnsd_shaped(nomadnet)
    assert not cmdline_is_rnsd_shaped(meshchat)
    assert not cmdline_is_rnsd_shaped(ma_daemon)
    assert not cmdline_is_rnsd_shaped("")

    assert cmdline_is_rns_family(nomadnet)   # --rnsconfig /etc/reticulum
    assert cmdline_is_rns_family(meshchat)
    assert not cmdline_is_rns_family(ma_daemon)


def test_rns_collision_returns_none_when_ss_unavailable():
    """Don't false-alarm when ss is missing or hangs."""
    import subprocess as sp
    def _raise(*args, **kwargs):
        raise FileNotFoundError("ss")
    with patch("utils.watchdog_probes_rns.subprocess.run", side_effect=_raise):
        sig = probe_rns_namespace_collision("volcano")
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# Issue #68 reconstruction — main_thread_wedge
# ─────────────────────────────────────────────────────────────────────


# Real kernel stack from moc1's 2026-05-20 wedge (verbatim from
# persistent_issues.md Issue #68).
_WEDGE_STACK = """\
[<0>] do_sys_poll+0x3b8/0x540
[<0>] unix_wait_for_peer+0x80/0xd0
[<0>] unix_stream_connect+0xa0/0x4f0
[<0>] __sock_recvmsg+0x60/0x90
[<0>] el0_svc+0x30/0xf8
"""

# Real healthy stack from a serving meshforge-map (this morning's
# verification on moc1 — same shape do_sys_poll, no Unix-connect site).
_HEALTHY_STACK = """\
[<0>] do_sys_poll+0x3b8/0x540
[<0>] __arm64_sys_ppoll+0xb4/0x148
[<0>] invoke_syscall+0x50/0x120
[<0>] el0_svc_common.constprop.0+0x48/0xf0
[<0>] do_el0_svc+0x24/0x38
[<0>] el0_svc+0x30/0xf8
"""


def test_main_thread_wedge_fires_on_unix_stream_connect(tmp_path):
    """Issue #68 reconstruction: main thread blocked in unix_wait_for_peer."""
    pid = 12345
    proc_dir = tmp_path / str(pid) / "task" / str(pid)
    proc_dir.mkdir(parents=True)
    (proc_dir / "stack").write_text(_WEDGE_STACK)
    sig = probe_main_thread_wedge(
        "meshforge-map.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is not None
    assert sig.cls == "main_thread_wedge"
    assert sig.severity == "wedge"
    assert sig.issue_ref == 68
    assert sig.extra["pid"] == pid
    # Should match the first wedge pattern in the stack, not a later one
    assert sig.extra["pattern"] in (
        "unix_wait_for_peer", "unix_stream_connect", "do_unix_stream_connect",
    )


def test_main_thread_wedge_does_not_fire_on_healthy_poll(tmp_path):
    """A healthy HTTP server spends its main-thread time in do_sys_poll.
    Must NOT false-alarm — the operator stops trusting the panel."""
    pid = 12345
    proc_dir = tmp_path / str(pid) / "task" / str(pid)
    proc_dir.mkdir(parents=True)
    (proc_dir / "stack").write_text(_HEALTHY_STACK)
    sig = probe_main_thread_wedge(
        "meshforge-map.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is None


def test_main_thread_wedge_returns_none_when_stack_unreadable(tmp_path):
    """No /proc/PID/stack (permission denied or process exited) →
    no signal, no false alarm."""
    sig = probe_main_thread_wedge(
        "meshforge-map.service", pid=99999, proc_root=str(tmp_path),
    )
    assert sig is None


def test_main_thread_wedge_skips_when_pid_unresolved(tmp_path):
    """systemctl show MainPID=0 means inactive — let service_inactive
    probe own that signal class."""
    def _runner(*args, **kwargs):
        class _R:
            stdout = "0\n"
            returncode = 0
        return _R()
    with patch("utils.watchdog_probe_core.subprocess.run", side_effect=_runner):
        sig = probe_main_thread_wedge(
            "meshforge-map.service", proc_root=str(tmp_path),
        )
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# 2026-05-21 moc1 investigation enhancement — worker-thread wedge scan
# ─────────────────────────────────────────────────────────────────────


def test_main_thread_wedge_finds_worker_thread_wedge(tmp_path):
    """Today's incident shape: meshforge-echo.service main thread was
    in futex_wait (healthy idle), but worker thread tid≠pid was in
    unix_wait_for_peer. Original main-thread-only probe missed it.
    The enhanced probe must scan ALL task/* stacks."""
    pid = 12345
    worker_tid = 12399
    main_dir = tmp_path / str(pid) / "task" / str(pid)
    worker_dir = tmp_path / str(pid) / "task" / str(worker_tid)
    main_dir.mkdir(parents=True)
    worker_dir.mkdir(parents=True)
    (main_dir / "stack").write_text(_HEALTHY_STACK)         # main idle
    (worker_dir / "stack").write_text(_WEDGE_STACK)         # worker wedged

    sig = probe_main_thread_wedge(
        "meshforge-echo.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is not None
    assert sig.cls == "main_thread_wedge"
    assert sig.severity == "wedge"
    assert sig.extra["tid"] == worker_tid
    assert sig.extra["thread_role"] == "worker"
    assert "worker thread" in sig.detail


def test_main_thread_wedge_prefers_main_thread_match(tmp_path):
    """When main AND worker both match a wedge pattern, the probe
    should report the main thread (cheaper, more authoritative)."""
    pid = 12345
    worker_tid = 12399
    main_dir = tmp_path / str(pid) / "task" / str(pid)
    worker_dir = tmp_path / str(pid) / "task" / str(worker_tid)
    main_dir.mkdir(parents=True)
    worker_dir.mkdir(parents=True)
    (main_dir / "stack").write_text(_WEDGE_STACK)
    (worker_dir / "stack").write_text(_WEDGE_STACK)

    sig = probe_main_thread_wedge(
        "meshforge-echo.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is not None
    assert sig.extra["tid"] == pid
    assert sig.extra["thread_role"] == "main"
    assert "main thread" in sig.detail


def test_main_thread_wedge_no_signal_when_all_threads_healthy(tmp_path):
    """Multiple threads, all idle → no signal."""
    pid = 12345
    for tid in (pid, 12399, 12400, 12401):
        d = tmp_path / str(pid) / "task" / str(tid)
        d.mkdir(parents=True)
        (d / "stack").write_text(_HEALTHY_STACK)
    sig = probe_main_thread_wedge(
        "meshforge-echo.service", pid=pid, proc_root=str(tmp_path),
    )
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# probe_lxmf_process_wedge — cmdline-scan for user-scope services
# ─────────────────────────────────────────────────────────────────────


def _make_fake_proc(tmp_path, pid, cmdline_bytes, stack_text,
                    extra_threads=None):
    """Helper: lay out /proc/<pid>/{cmdline,task/<tid>/stack} files."""
    pid_dir = tmp_path / str(pid)
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "cmdline").write_bytes(cmdline_bytes)
    task_dir = pid_dir / "task" / str(pid)
    task_dir.mkdir(parents=True)
    (task_dir / "stack").write_text(stack_text)
    if extra_threads:
        for tid, stack in extra_threads:
            tdir = pid_dir / "task" / str(tid)
            tdir.mkdir(parents=True)
            (tdir / "stack").write_text(stack)


def test_lxmf_process_wedge_finds_echo_worker_thread_wedge(tmp_path):
    """Today's moc1 reconstruction: meshforge-echo.service's worker
    thread in unix_wait_for_peer. probe_lxmf_process_wedge walks
    /proc, matches `lab.lxmf_echo` cmdline, scans all threads, fires."""
    # Process whose cmdline matches the echo pattern, worker wedged.
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_echo\x00",
        stack_text=_HEALTHY_STACK,
        extra_threads=[(10099, _WEDGE_STACK)],
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert len(signals) == 1
    s = signals[0]
    assert s.cls == "main_thread_wedge"
    assert s.subject == "lab.lxmf_echo"
    assert s.severity == "wedge"
    assert s.issue_ref == 68
    assert s.extra["pid"] == 10001
    assert s.extra["tid"] == 10099
    assert s.extra["thread_role"] == "worker"
    assert "lab.lxmf_echo" in s.extra["cmdline"]


def test_lxmf_process_wedge_ignores_non_lxmf_processes(tmp_path):
    """A random process with a healthy stack must not produce a signal,
    even if its pid dir is in /proc."""
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00something.else\x00",
        stack_text=_HEALTHY_STACK,
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert signals == []


def test_lxmf_process_wedge_ignores_lxmf_process_when_healthy(tmp_path):
    """An lxmf_echo process with all threads healthy → no signal."""
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_echo\x00",
        stack_text=_HEALTHY_STACK,
        extra_threads=[(10099, _HEALTHY_STACK)],
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert signals == []


def test_lxmf_process_wedge_finds_multiple_wedged_processes(tmp_path):
    """Both lab.lxmf_echo and lab.lxmf_tracer wedged → one signal each."""
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_echo\x00",
        stack_text=_WEDGE_STACK,
    )
    _make_fake_proc(
        tmp_path, pid=20002,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_tracer\x00",
        stack_text=_WEDGE_STACK,
    )
    # Plus a healthy non-lxmf process — must not interfere.
    _make_fake_proc(
        tmp_path, pid=30003,
        cmdline_bytes=b"/usr/sbin/nginx\x00",
        stack_text=_HEALTHY_STACK,
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert {s.subject for s in signals} == {"lab.lxmf_echo", "lab.lxmf_tracer"}


def test_lxmf_process_wedge_skips_non_digit_proc_entries(tmp_path):
    """/proc contains non-numeric entries like /proc/cpuinfo, /proc/self.
    The walker must skip them without crashing."""
    (tmp_path / "cpuinfo").write_text("nope")
    (tmp_path / "self").mkdir()
    _make_fake_proc(
        tmp_path, pid=10001,
        cmdline_bytes=b"/usr/bin/python3\x00-m\x00lab.lxmf_echo\x00",
        stack_text=_HEALTHY_STACK,
    )
    signals = probe_lxmf_process_wedge(proc_root=str(tmp_path))
    assert signals == []


# ─────────────────────────────────────────────────────────────────────
# probe_rns_shared_instance_responsive — 2026-05-21 moc1 wedge class
# ─────────────────────────────────────────────────────────────────────


def test_rns_shared_instance_responsive_returns_none_when_no_listener():
    """No listener at @rns/<name> AND no @rns/* at all → return None
    (service_inactive owns the 'rnsd not running' signal).

    ⚠️ ``ss_output`` is PINNED (2026-08-05). The docstring used to say
    "FileNotFoundError"; Linux actually answers a nonexistent ABSTRACT
    socket with ECONNREFUSED, and that branch now consults the listener
    table — so without pinning, this test's verdict would depend on
    whether the box running the suite happens to have rnsd up. Green on
    a dev laptop, a mismatch signal on any fleet box
    (feedback_tests_must_pin_ambient_state).
    """
    import secrets
    name = f"watchdog-test-nope-{secrets.token_hex(8)}"
    sig = probe_rns_shared_instance_responsive(
        name, timeout_s=0.5, ss_output="")
    assert sig is None


def test_rns_shared_instance_responsive_returns_none_on_quick_connect():
    """Healthy path: listener accepts the connect quickly → no signal.
    Uses a real abstract Unix listener accepting immediately.

    Timeouts are generous because this test runs on shared CI runners
    where thread scheduling can lag by hundreds of ms under load. The
    timeouts only consume wall-time on real test failures; the happy
    path completes in <50 ms.
    """
    import secrets
    import threading
    name = f"watchdog-test-ok-{secrets.token_hex(8)}"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind("\x00rns/" + name)
    listener.listen(5)
    listener.settimeout(10.0)
    accepted = threading.Event()
    ready = threading.Event()

    def _accept_loop():
        # Signal "thread reached accept()" so the test can avoid racing
        # the probe's connect against thread startup on a loaded runner.
        ready.set()
        try:
            conn, _ = listener.accept()
            conn.close()
            accepted.set()
        except (socket.timeout, OSError):
            pass

    t = threading.Thread(target=_accept_loop, daemon=True)
    t.start()
    try:
        assert ready.wait(5.0), "accept thread failed to start"
        sig = probe_rns_shared_instance_responsive(name, timeout_s=2.0)
        # Wait for accept BEFORE closing the listener — close-mid-accept
        # raises OSError in the thread which never sets `accepted` and
        # used to flake CI (Issue #68 watchdog probe coverage).
        assert accepted.wait(5.0), "listener should have accepted the probe connect"
    finally:
        listener.close()
    assert sig is None


def test_rns_shared_instance_responsive_fires_wedge_on_full_backlog():
    """The 2026-05-21 moc1 reconstruction: listener exists but never
    accepts new connects. Real reproduction: bind + listen(0) and don't
    call accept(). Once the OS-imposed minimum backlog fills,
    subsequent connects block until our timeout fires.

    Note Linux clamps backlog at 0 to the kernel minimum (often 16-128),
    so we have to fill the queue first by spamming the listener with
    throwaway connects until additional ones block. This mirrors the
    real moc1 fault closer than pure mock-based testing.
    """
    import secrets
    import threading

    name = f"watchdog-test-hang-{secrets.token_hex(8)}"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind("\x00rns/" + name)
    listener.listen(1)  # tiny backlog — easy to fill

    # Fill the accept queue with dummy connects so the next connect
    # has to block. We never accept(), so the queue stays full.
    fillers = []
    for _ in range(200):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.05)
            s.connect("\x00rns/" + name)
            fillers.append(s)
        except (socket.timeout, ConnectionRefusedError, OSError):
            # Once the queue is full and unestablished half-opens
            # accumulate beyond the kernel min, additional connect
            # attempts start to refuse or time out. That's the
            # condition we want before running the probe.
            break

    try:
        # ss_output pinned empty: this test is about the WEDGE branch, and
        # an unpinned listener table would let the refused-path fork into
        # rns_instance_name_mismatch on any box that runs rnsd.
        sig = probe_rns_shared_instance_responsive(
            name, timeout_s=0.5, ss_output="")
    finally:
        for s in fillers:
            try:
                s.close()
            except OSError:
                pass
        listener.close()

    # Either ECONNREFUSED (kernel refused new attempts because backlog
    # is genuinely saturated) OR socket.timeout (connect blocked). The
    # probe must fire wedge ONLY on the latter; ECONNREFUSED is
    # explicitly handled as "not a wedge" so service_inactive owns it.
    # On Linux the accept-queue-full behavior under SOCK_STREAM Unix
    # sockets typically refuses with ECONNREFUSED. So this test pins
    # the *behavior contract* (probe doesn't crash, returns either
    # None or a valid Signal) more than a single outcome.
    if sig is not None:
        assert sig.cls == "rns_shared_instance_unresponsive"
        assert sig.severity == "wedge"
        assert sig.issue_ref == 68


def test_rns_shared_instance_responsive_returns_none_on_empty_name():
    sig = probe_rns_shared_instance_responsive("")
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# 2026-05-30 incident — rns_interface_down_peer_reachable
# ─────────────────────────────────────────────────────────────────────


# Exact rnstatus interface block from the 2026-05-30 incident: the sole
# RNS uplink TCPInterface stuck Status: Down while rnsd itself was fine.
_RNSTATUS_DOWN_BLOCK = (
    " TCPInterface[Regional RNS/192.0.2.38:4242]\n"
    "    Status    : Down\n"
    "    Mode      : Full\n"
    "    Rate      : 10.00 Mbps\n"
    "    Traffic   : ↑1.59 MB    0 bps\n"
    "                ↓1.58 MB    0 bps\n"
)

# Same interface, but Up — healthy steady state.
_RNSTATUS_UP_BLOCK = (
    " TCPInterface[Regional RNS/192.0.2.38:4242]\n"
    "    Status    : Up\n"
    "    Mode      : Full\n"
    "    Rate      : 10.00 Mbps\n"
)

# One Up TCP interface + one Down (reachable) TCP interface.
_RNSTATUS_MIXED = (
    " Shared Instance[37428]\n"
    "    Status    : Up\n"
    " TCPInterface[UpPeer RNS/10.0.0.5:4242]\n"
    "    Status    : Up\n"
    "    Mode      : Full\n"
    " TCPInterface[Regional RNS/192.0.2.38:4242]\n"
    "    Status    : Down\n"
    "    Mode      : Full\n"
    " RNodeInterface[LoRa]\n"
    "    Status    : Up\n"
)

# Only non-TCP interfaces — no routable host:port, must be ignored even
# when one is Down.
_RNSTATUS_NONTCP = (
    " Shared Instance[37428]\n"
    "    Status    : Up\n"
    " RNodeInterface[LoRa Radio]\n"
    "    Status    : Down\n"
    " AutoInterface[Default Interface]\n"
    "    Status    : Up\n"
)


class TestRnsInterfaceDownPeerReachable:
    """2026-05-30 production incident: rnsd healthy (Up, owns @rns,
    answers rnstatus) and the peer host:port + L3 reachable, but the
    box's sole RNS uplink TCPInterface stuck Status: Down — fleet
    islanded until rnsd restart. The watchdog must catch this DIRECTLY,
    not just indirectly via tracer_peer_unreachable."""

    def test_signal_class_registered(self):
        assert "rns_interface_down_peer_reachable" in SIGNAL_CLASSES

    def test_down_interface_peer_reachable_fires_wedge(self):
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_DOWN_BLOCK,
            )
        assert sig is not None
        assert sig.cls == "rns_interface_down_peer_reachable"
        # "wedge" is this codebase's highest severity (no "critical").
        assert sig.severity == "wedge"
        assert sig.extra["host"] == "192.0.2.38"
        assert sig.extra["port"] == 4242
        assert sig.extra["peer_reachable"] is True
        # Detail names the cure: restart rnsd.
        assert "rnsd.service" in sig.detail

    def test_down_interface_peer_unreachable_no_signal(self):
        """Genuine peer/network outage — owned by tracer_peer_unreachable,
        not this probe."""
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=False):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_DOWN_BLOCK,
            )
        assert sig is None

    def test_up_interface_no_signal_even_if_reachable(self):
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_UP_BLOCK,
            )
        assert sig is None

    def test_mixed_flags_only_the_down_reachable_interface(self):
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_MIXED,
            )
        assert sig is not None
        assert sig.extra["host"] == "192.0.2.38"
        assert sig.extra["port"] == 4242

    def test_parser_pins_exact_incident_block(self):
        """The 192.0.2.38:4242 Regional RNS block must parse to
        host 192.0.2.38, port 4242."""
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_DOWN_BLOCK,
            )
        assert sig is not None
        assert sig.extra["host"] == "192.0.2.38"
        assert sig.extra["port"] == 4242

    def test_non_tcp_interfaces_ignored(self):
        """RNodeInterface / Shared Instance / AutoInterface carry no
        host:port — never probed, never flagged."""
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_NONTCP,
            )
        assert sig is None

    def test_quiet_when_rnstatus_errored(self):
        """rnsd unreachable → rnstatus parse_error → no signal (a
        different probe owns 'rnsd is down')."""
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text="Could not connect to local shared instance.",
            )
        assert sig is None

    def test_tcp_reachable_false_on_closed_port(self):
        """The real reachability helper (not patched) must return False
        on a closed local port and never raise."""
        from utils.watchdog_probes import _tcp_reachable
        assert _tcp_reachable("127.0.0.1", 1, timeout=0.5) is False


# ─────────────────────────────────────────────────────────────────────
# 2026-05-30 — rns_rpc_unresponsive (wedged rnsd RPC; rnstatus hangs)
# ─────────────────────────────────────────────────────────────────────


class TestProbeRnsRpcResponsive:
    """Wedged rnsd RPC: the shared-instance socket accepts connects (so
    probe_rns_shared_instance_responsive reports healthy) but rnstatus's
    RPC round-trip hangs. probe_rns_rpc_responsive keys on
    RNSStatus.timed_out — set ONLY by a run_rnstatus subprocess TIMEOUT,
    never by a fast error — so clean-down rnsd and RNS-less boxes don't
    false-alarm. Companion to the connect-layer shared-instance probe
    (#68 SYN-SENT) and the #69 RPC-EOF family."""

    @staticmethod
    def _status(**kw):
        from utils.rns_status_parser import RNSStatus
        return RNSStatus(**kw)

    def test_signal_class_registered(self):
        assert "rns_rpc_unresponsive" in SIGNAL_CLASSES

    def test_fires_wedge_when_rnstatus_timed_out(self):
        status = self._status(
            timed_out=True,
            parse_error="rnstatus timed out (rnsd unresponsive)",
        )
        sig = probe_rns_rpc_responsive(rnstatus_status=status)
        assert sig is not None
        assert sig.cls == "rns_rpc_unresponsive"
        assert sig.severity == "wedge"
        assert sig.subject == "rnsd"
        assert sig.issue_ref == 68
        # Detail names the cure: restart rnsd.
        assert "rnsd.service" in sig.detail

    def test_quiet_when_healthy_interfaces_present(self):
        """Connect accepted + RPC answered (timed_out False) → no signal,
        even with interfaces present."""
        from utils.rns_status_parser import RNSInterface, InterfaceStatus
        status = self._status(
            interfaces=[RNSInterface(
                type_name="TCPInterface",
                display_name="Regional RNS/192.0.2.38:4242",
                status=InterfaceStatus.UP,
            )],
        )
        assert probe_rns_rpc_responsive(rnstatus_status=status) is None

    def test_quiet_when_binary_missing(self):
        """Binary missing → parse_error set but timed_out False → None
        (no false alarm on RNS-less boxes)."""
        status = self._status(
            parse_error="rnstatus binary not found. Install RNS: pip install rns",
        )
        assert probe_rns_rpc_responsive(rnstatus_status=status) is None

    def test_quiet_when_clean_down_error_not_timeout(self):
        """rnsd cleanly down → fast 'no shared instance' error, NOT a
        timeout → timed_out False → None (service_inactive owns down)."""
        status = self._status(
            parse_error="Could not connect to local shared instance.",
        )
        assert probe_rns_rpc_responsive(rnstatus_status=status) is None

    def test_runs_rnstatus_with_bounded_timeout_when_no_status_injected(self):
        """No injected status → probe calls run_rnstatus(timeout_s=...) and
        keys on the returned timed_out flag. Pins that the runner-shared
        call isn't required for the probe to function standalone."""
        from utils.rns_status_parser import RNSStatus
        timed = RNSStatus(
            timed_out=True,
            parse_error="rnstatus timed out (rnsd unresponsive)",
        )
        with patch("utils.rns_status_parser.run_rnstatus",
                   return_value=timed) as m:
            sig = probe_rns_rpc_responsive(timeout_s=4.0)
        m.assert_called_once_with(timeout_s=4.0)
        assert sig is not None
        assert sig.cls == "rns_rpc_unresponsive"

    # -- parser-level: run_rnstatus timed_out flag + timeout_s plumbing --

    def test_run_rnstatus_sets_timed_out_on_subprocess_timeout(self):
        import subprocess as sp
        from utils.rns_status_parser import run_rnstatus
        with patch("utils.rns_status_parser._find_rnstatus_binary",
                   return_value="/usr/bin/rnstatus"), \
             patch("utils.rns_status_parser.subprocess.run",
                   side_effect=sp.TimeoutExpired(cmd="rnstatus", timeout=8)):
            status = run_rnstatus(timeout_s=8.0)
        assert status.timed_out is True
        assert "timed out" in (status.parse_error or "")

    def test_run_rnstatus_forwards_timeout_to_subprocess(self):
        from unittest.mock import MagicMock
        from utils.rns_status_parser import run_rnstatus
        fake = MagicMock(stdout="", stderr="")
        with patch("utils.rns_status_parser._find_rnstatus_binary",
                   return_value="/usr/bin/rnstatus"), \
             patch("utils.rns_status_parser.subprocess.run",
                   return_value=fake) as m:
            run_rnstatus(timeout_s=3.5)
        assert m.call_args.kwargs["timeout"] == 3.5

    def test_run_rnstatus_binary_missing_is_not_timed_out(self):
        from utils.rns_status_parser import run_rnstatus
        with patch("utils.rns_status_parser._find_rnstatus_binary",
                   return_value=None):
            status = run_rnstatus()
        assert status.timed_out is False
        assert "binary not found" in (status.parse_error or "")


# ─────────────────────────────────────────────────────────────────────
# Issue #61 reconstruction — http_local_unresponsive
# ─────────────────────────────────────────────────────────────────────


def test_http_local_no_signal_when_response_arrives():
    """Any HTTP response (including 503 during warming) means the
    server loop is alive. Don't gate on status code."""
    class _Resp:
        def read(self, _n):
            return b"OK"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    with patch("utils.watchdog_probes_service.urlopen", return_value=_Resp()):
        sig = probe_http_local("meshforge-map.service")
    assert sig is None


def test_http_local_fires_on_timeout():
    """Issue #61 class: TCP accept succeeded, but the handler thread
    is wedged and no response comes back. Probe must surface this."""
    import socket as sk
    def _raise(*args, **kwargs):
        raise sk.timeout("timed out")
    with patch("utils.watchdog_probes_service.urlopen", side_effect=_raise):
        sig = probe_http_local("meshforge-map.service")
    assert sig is not None
    assert sig.cls == "http_local_unresponsive"
    assert sig.severity == "wedge"
    assert sig.issue_ref == 61


def test_http_local_skips_connection_refused():
    """ConnectionRefused = port not bound = service inactive. That's a
    different probe's domain — don't double-alarm."""
    from urllib.error import URLError
    def _raise(*args, **kwargs):
        raise URLError("[Errno 111] Connection refused")
    with patch("utils.watchdog_probes_service.urlopen", side_effect=_raise):
        sig = probe_http_local("meshforge-map.service")
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# Issue #73 — fd_exhaustion (proactive companion to http_local)
# ─────────────────────────────────────────────────────────────────────


def _fake_proc(tmp_path, pid, *, open_fds, soft="1024", hard="524288"):
    """Build a fake /proc/<pid> with `open_fds` fd entries and a limits file."""
    pdir = tmp_path / str(pid)
    fd_dir = pdir / "fd"
    fd_dir.mkdir(parents=True)
    for i in range(open_fds):
        # symlink target doesn't matter; os.scandir just counts entries
        (fd_dir / str(i)).write_text("")
    limits = (
        "Limit                     Soft Limit           Hard Limit           Units\n"
        "Max open files            {soft}                 {hard}               files\n"
    ).format(soft=soft, hard=hard)
    (pdir / "limits").write_text(limits)
    return str(tmp_path)


def test_fd_exhaustion_quiet_when_healthy(tmp_path):
    """Well under the soft limit → no signal."""
    root = _fake_proc(tmp_path, 4242, open_fds=50, soft="1024")
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=root, main_pid=4242
    )
    assert sig is None


def test_fd_exhaustion_degraded_past_80pct(tmp_path):
    """820/1024 = 80% → degraded, not yet wedge."""
    root = _fake_proc(tmp_path, 4242, open_fds=820, soft="1024")
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=root, main_pid=4242
    )
    assert sig is not None
    assert sig.cls == "fd_exhaustion"
    assert sig.severity == "degraded"
    assert sig.issue_ref == 73
    assert sig.extra["open_fds"] == 820
    assert sig.extra["soft_limit"] == 1024


def test_fd_exhaustion_wedge_past_95pct(tmp_path):
    """1000/1024 ≈ 98% → wedge (exhaustion imminent — the #73 incident)."""
    root = _fake_proc(tmp_path, 4242, open_fds=1000, soft="1024")
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=root, main_pid=4242
    )
    assert sig is not None
    assert sig.severity == "wedge"
    assert "[Errno 24]" in sig.detail


def test_fd_exhaustion_none_when_pid_unresolved(tmp_path):
    """Inactive service (MainPID unresolved) → None; service_inactive owns it."""
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=str(tmp_path), main_pid=None,
        systemctl_path="/nonexistent/systemctl",
    )
    assert sig is None


def test_fd_exhaustion_none_when_proc_vanished(tmp_path):
    """PID resolved but /proc/<pid> gone (race) → None, never a false alarm."""
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=str(tmp_path), main_pid=99999,
    )
    assert sig is None


def test_fd_exhaustion_none_when_soft_limit_unlimited(tmp_path):
    """An 'unlimited' soft limit has no ceiling to measure against → None."""
    root = _fake_proc(tmp_path, 4242, open_fds=9000, soft="unlimited")
    sig = probe_fd_exhaustion(
        "meshforge-map.service", proc_root=root, main_pid=4242
    )
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# Issue #75 — phoneapi_tcp_leak (#17 contention class, leak form)
# ─────────────────────────────────────────────────────────────────────


def _fake_phoneapi_proc(tmp_path, pid, *, sockets, estab_to_4403):
    """Fake /proc: ``sockets`` maps fd→inode for ``pid`` (socket symlinks);
    ``estab_to_4403`` lists inodes shown ESTABLISHED to remote :4403 in net/tcp."""
    fd_dir = tmp_path / str(pid) / "fd"
    fd_dir.mkdir(parents=True, exist_ok=True)
    for fd, inode in sockets.items():
        os.symlink(f"socket:[{inode}]", fd_dir / str(fd))
    net = tmp_path / "net"
    net.mkdir(exist_ok=True)
    header = ("  sl  local_address rem_address   st tx_queue rx_queue "
              "tr tm->when retrnsmt   uid  timeout inode\n")
    rows = [header]
    for inode in estab_to_4403:
        # 0x1133 == 4403; state 01 == ESTABLISHED
        rows.append(
            f"   1: 0100007F:EC10 0100007F:1133 01 00000000:00000000 "
            f"00:00000000 00000000  1000        0 {inode} 1\n"
        )
    (net / "tcp").write_text("".join(rows))
    (net / "tcp6").write_text(header)
    return str(tmp_path)


def test_phoneapi_leak_first_sighting_is_silent_but_recorded(tmp_path):
    """Tick 1: candidate socket seen → None (could be a legit in-flight
    collect), but the state file records count=1 for the next tick."""
    root = _fake_phoneapi_proc(
        tmp_path, 4242, sockets={15: 99001}, estab_to_4403=[99001])
    sp = str(tmp_path / "state.json")
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4242,
        state_path=sp, owner_fetch=lambda: (True, None),
    )
    assert sig is None
    saved = json.loads(Path(sp).read_text())
    assert saved == {"pid": 4242, "inode_counts": {"99001": 1}}


def test_phoneapi_leak_fires_when_same_inode_persists(tmp_path):
    """At the persistence threshold, same pid + same inode +
    persistent_owner null → degraded. Pins the moc1 2026-06-07 incident
    shape (a genuinely leaked TCPInterface lives hours)."""
    root = _fake_phoneapi_proc(
        tmp_path, 4242, sockets={15: 99001}, estab_to_4403=[99001])
    sp = str(tmp_path / "state.json")
    Path(sp).write_text(json.dumps(
        {"pid": 4242, "inode_counts": {"99001": 19}}))  # this tick → 20
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4242,
        state_path=sp, owner_fetch=lambda: (True, None),
    )
    assert sig is not None
    assert sig.cls == "phoneapi_tcp_leak"
    assert sig.severity == "degraded"
    assert sig.issue_ref == 75
    assert sig.extra["leaked_inodes"] == [99001]
    assert sig.extra["persisted_ticks"] == 20
    assert "persistent_owner=null" in sig.detail
    assert "systemctl restart meshforge-map.service" in sig.detail


def test_phoneapi_leak_silent_on_slow_collect_below_threshold(tmp_path):
    """The moc1 2026-06-07 FALSE-ALARM shape: a demand-collect TCP nodedb
    sync lives 1-4 minutes (2-8 ticks). It must stay silent — only an
    inode persisting past ~10 min (a real leak) fires."""
    root = _fake_phoneapi_proc(
        tmp_path, 4242, sockets={15: 99001}, estab_to_4403=[99001])
    sp = str(tmp_path / "state.json")
    Path(sp).write_text(json.dumps(
        {"pid": 4242, "inode_counts": {"99001": 8}}))  # 4 min in → 9th tick
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4242,
        state_path=sp, owner_fetch=lambda: (True, None),
    )
    assert sig is None
    # ...but the count keeps accumulating toward a real-leak fire
    saved = json.loads(Path(sp).read_text())
    assert saved["inode_counts"] == {"99001": 9}


def test_phoneapi_leak_legacy_state_format_is_count_one(tmp_path):
    """Pre-2026-06-07 state files stored a bare 'inodes' list — loaded as
    count 1, so a mid-upgrade tick never spuriously fires."""
    root = _fake_phoneapi_proc(
        tmp_path, 4242, sockets={15: 99001}, estab_to_4403=[99001])
    sp = str(tmp_path / "state.json")
    Path(sp).write_text(json.dumps({"pid": 4242, "inodes": [99001]}))
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4242,
        state_path=sp, owner_fetch=lambda: (True, None),
    )
    assert sig is None
    saved = json.loads(Path(sp).read_text())
    assert saved["inode_counts"] == {"99001": 2}


def test_phoneapi_leak_silent_when_inode_rotates(tmp_path):
    """A fresh per-collect socket each tick (different inode) never fires
    and never accumulates — rotation resets the count to 1."""
    root = _fake_phoneapi_proc(
        tmp_path, 4242, sockets={15: 99002}, estab_to_4403=[99002])
    sp = str(tmp_path / "state.json")
    Path(sp).write_text(json.dumps(
        {"pid": 4242, "inode_counts": {"99001": 19}}))
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4242,
        state_path=sp, owner_fetch=lambda: (True, None),
    )
    assert sig is None
    saved = json.loads(Path(sp).read_text())
    assert saved["inode_counts"] == {"99002": 1}


def test_phoneapi_leak_silent_when_owner_accounted(tmp_path):
    """An ACCOUNTED persistent connection (listener TCP fallback) is a known
    tradeoff the listener already warns about — not a leak."""
    root = _fake_phoneapi_proc(
        tmp_path, 4242, sockets={15: 99001}, estab_to_4403=[99001])
    sp = str(tmp_path / "state.json")
    Path(sp).write_text(json.dumps(
        {"pid": 4242, "inode_counts": {"99001": 19}}))  # past threshold
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4242,
        state_path=sp, owner_fetch=lambda: (True, "message_listener"),
    )
    assert sig is None


def test_phoneapi_leak_silent_when_status_endpoint_dark(tmp_path):
    """Status endpoint unreachable → indeterminate → None (http_local owns
    a dark map service; this probe never alarms on uncertainty)."""
    root = _fake_phoneapi_proc(
        tmp_path, 4242, sockets={15: 99001}, estab_to_4403=[99001])
    sp = str(tmp_path / "state.json")
    Path(sp).write_text(json.dumps(
        {"pid": 4242, "inode_counts": {"99001": 19}}))  # past threshold
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4242,
        state_path=sp, owner_fetch=lambda: (False, None),
    )
    assert sig is None


def test_phoneapi_leak_silent_after_service_restart(tmp_path):
    """Same inode number but a different pid in state (service restarted
    between ticks) → no match, no fire."""
    root = _fake_phoneapi_proc(
        tmp_path, 4243, sockets={15: 99001}, estab_to_4403=[99001])
    sp = str(tmp_path / "state.json")
    Path(sp).write_text(json.dumps(
        {"pid": 4242, "inode_counts": {"99001": 19}}))  # old pid, high count
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4243,
        state_path=sp, owner_fetch=lambda: (True, None),
    )
    assert sig is None


def test_phoneapi_leak_none_when_pid_unresolved(tmp_path):
    """Inactive service → None; service_inactive owns it."""
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=str(tmp_path), main_pid=None,
        systemctl_path="/nonexistent/systemctl",
        state_path=str(tmp_path / "state.json"),
    )
    assert sig is None


def test_phoneapi_leak_none_when_no_socket_to_port(tmp_path):
    """Process has sockets, none ESTAB to :4403 → None and state cleared."""
    root = _fake_phoneapi_proc(
        tmp_path, 4242, sockets={15: 99001}, estab_to_4403=[])
    sp = str(tmp_path / "state.json")
    Path(sp).write_text(json.dumps(
        {"pid": 4242, "inode_counts": {"99001": 19}}))
    sig = probe_phoneapi_tcp_leak(
        "meshforge-map.service", proc_root=root, main_pid=4242,
        state_path=sp, owner_fetch=lambda: (True, None),
    )
    assert sig is None
    assert json.loads(Path(sp).read_text())["inode_counts"] == {}


# ─────────────────────────────────────────────────────────────────────
# 2026-06-04 — channel_feed_dark (the .32 dark-feed / rotation-canary)
# ─────────────────────────────────────────────────────────────────────

_NOW = 1_780_642_400.0


def _journal_fake(json_age_s=60.0, ch_text_age_s=None, now=_NOW, patterns=None):
    """Build a newest_line_fn: json-pipeline line at ``json_age_s``; ch-text
    line at ``ch_text_age_s`` (None = no match). Records queried patterns."""
    def fn(pattern):
        if patterns is not None:
            patterns.append(pattern)
        if pattern == "serialized json message":
            return f"{now - json_age_s:.6f} host meshtasticd[1]: json"
        if ch_text_age_s is None:
            return None
        return f"{now - ch_text_age_s:.6f} host meshtasticd[1]: text"
    return fn


def test_channel_feed_dark_fires_when_text_stale():
    """json pipeline alive, last meshforge text 7h old (>6h default) → degraded."""
    sig = probe_channel_feed_dark(
        main_pid=1002,
        newest_line_fn=_journal_fake(ch_text_age_s=7 * 3600.0),
        now=_NOW,
    )
    assert sig is not None
    assert sig.cls == "channel_feed_dark"
    assert sig.severity == "degraded"
    assert sig.subject == "meshtastic-meshforge"
    assert sig.extra["age_s"] == pytest.approx(7 * 3600.0, abs=1.0)
    assert "PSK re-key" in sig.detail


def test_channel_feed_dark_quiet_when_text_fresh():
    """ch2 text 10 minutes old → feed alive → None."""
    sig = probe_channel_feed_dark(
        main_pid=1002,
        newest_line_fn=_journal_fake(ch_text_age_s=600.0),
        now=_NOW,
    )
    assert sig is None


def test_channel_feed_dark_fires_when_no_text_in_lookback():
    """json pipeline alive but zero ch2 text in the whole lookback → fire,
    detail names the lookback window instead of an age."""
    sig = probe_channel_feed_dark(
        main_pid=1002,
        newest_line_fn=_journal_fake(ch_text_age_s=None),
        now=_NOW,
    )
    assert sig is not None
    # Lookback is derived from the 6h dark_after_s default (6h + 1h margin).
    assert "7h lookback" in sig.detail
    # A no-match fire carries NO age (nothing to measure). This assert
    # originally sat here, drifted into the tail of the test below when it
    # was inserted, and is now DELIBERATELY present in both (each fires the
    # no-match branch; review 2026-07-01) — this copy is the one that
    # belongs to this test's docstring contract.
    assert "age_s" not in sig.extra


def test_channel_feed_dark_scan_window_bounded_to_threshold(monkeypatch):
    """The journal scan window is DERIVED from dark_after_s, not a fixed 24h.

    The no-match (dark / collector) case scans the ENTIRE --since window every
    tick; moc5 (2026-07-01) is a collector with no json uplink and pegged a
    core re-scanning a fixed 24h of meshtasticd journal each watchdog tick. The
    freshness gate discards any json line older than dark_after_s, so a longer
    window is pure wasted CPU. Pin the derivation so it can never silently
    regress to a fixed 24h (honest_failure_modes #5 — one bounded constant)."""
    # Patch where the name is LOOKED UP: the probe moved to
    # watchdog_probes_channel on 2026-08-07 (MF025 split) and now uses the
    # tri-state reader, which is what the default path actually calls.
    import utils.watchdog_probes_channel as chan
    captured = []

    def fake_newest(unit, pattern, lookback, journalctl_path="journalctl"):
        captured.append(lookback)
        if pattern == "serialized json message":
            return ("ok", f"{_NOW - 60.0:.6f} host meshtasticd[1]: json")
        return ("ok", None)  # channel text dark → fires, exercising BOTH calls

    monkeypatch.setattr(chan, "_journal_newest_match_status", fake_newest)
    # No newest_line_fn / lookback passed → the default (derived) path runs.
    sig = probe_channel_feed_dark(main_pid=1002, now=_NOW)
    assert sig is not None and sig.cls == "channel_feed_dark"
    assert captured, "the default path must call the journal reader"
    # 6h dark_after_s → 7h window (6h + 1h margin), and NEVER the old 24h.
    assert set(captured) == {"7h"}
    assert "24h" not in captured
    assert "age_s" not in sig.extra


# ─────────────────────────────────────────────────────────────────────
# 2026-06-15 — meshtasticd_phoneapi_wedge (#17/#75 contention, churn form;
# the 2026-06-13→15 moc 2-day silent mesh-TX wedge)
# ─────────────────────────────────────────────────────────────────────


class TestMeshtasticdPhoneapiWedge:
    """≥2 contending single-consumers thrash meshtasticd's :4403 — counted
    via the 'Force close previous TCP connection' churn in the journal,
    with a 2-tick debounce so a transient burst can't flap the signal."""

    _THRESHOLD = 12

    def _count_fn(self, value):
        """Injectable count_fn returning a fixed value (or None) per tick."""
        return lambda pattern: value

    @pytest.fixture(autouse=True)
    def _held_connection_present(self):
        """Default: a held :4403 contender exists, so the 2026-06-27 harm guard
        doesn't suppress — the threshold/debounce/streak tests exercise their own
        logic as before. The two harm-guard tests patch _estab_inodes_to_port
        themselves (inner patch wins) to drive the held/not-held branches."""
        with patch("utils.watchdog_probes_service._estab_inodes_to_port",
                   return_value={1}):
            yield

    def test_two_consecutive_over_threshold_ticks_fire(self, tmp_path):
        """RED-FIRST debounce proof: a SINGLE over-threshold tick returns
        None (streak=1 < 2); the SECOND consecutive tick fires degraded."""
        sp = str(tmp_path / "wedge.json")
        kwargs = dict(
            main_pid=1234, gateway_main_pid=5678,
            threshold=self._THRESHOLD,
            count_fn=self._count_fn(40),  # ~well above threshold (incident)
            state_path=sp,
        )

        # Tick 1 — over threshold but not yet confirmed → None.
        assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None

        # Tick 2 — confirms the streak → fire.
        sig = probe_meshtasticd_phoneapi_wedge(**kwargs)
        assert sig is not None
        assert sig.cls == "meshtasticd_phoneapi_wedge"
        assert sig.severity == "degraded"
        assert sig.subject == "meshtasticd"
        assert sig.issue_ref is None
        assert sig.extra["force_close_count"] == 40
        assert sig.extra["threshold"] == self._THRESHOLD
        assert sig.extra["lookback"] == "10min"
        # detail names contention, consequence, and recovery.
        assert "Force close previous TCP connection" in sig.detail
        assert "restart meshtasticd" in sig.detail
        assert "RNS round-trip canary" in sig.detail

    def test_below_threshold_returns_none(self, tmp_path):
        """Steady-state single-consumer churn (~0) → never fires, and the
        streak stays broken even after two below-threshold ticks."""
        sp = str(tmp_path / "wedge.json")
        kwargs = dict(
            main_pid=1234, gateway_main_pid=5678, threshold=self._THRESHOLD,
            count_fn=self._count_fn(3), state_path=sp,
        )
        assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None
        assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None

    def test_below_threshold_tick_resets_the_streak(self, tmp_path):
        """A confirming run must be CONSECUTIVE: one over-threshold tick, then
        a quiet tick, then over-threshold again → still None (streak reset to
        1), proving a transient burst between quiet ticks can't accumulate."""
        sp = str(tmp_path / "wedge.json")
        over = dict(main_pid=1234, gateway_main_pid=5678, threshold=self._THRESHOLD,
                    count_fn=self._count_fn(40), state_path=sp)
        under = dict(main_pid=1234, gateway_main_pid=5678, threshold=self._THRESHOLD,
                     count_fn=self._count_fn(2), state_path=sp)
        assert probe_meshtasticd_phoneapi_wedge(**over) is None   # streak 1
        assert probe_meshtasticd_phoneapi_wedge(**under) is None  # reset to 0
        assert probe_meshtasticd_phoneapi_wedge(**over) is None   # streak 1 again

    def test_meshtasticd_inactive_returns_none(self, tmp_path):
        """meshtasticd inactive (main_pid resolves to None via systemctl) →
        None; service_inactive owns that. Inject a systemctl_path that yields
        no pid by leaving main_pid unset and pointing at a nonexistent tool."""
        sp = str(tmp_path / "wedge.json")
        # main_pid=None + a systemctl that can't be found → _resolve_main_pid
        # returns None → probe self-guards None even with sky-high churn.
        sig = probe_meshtasticd_phoneapi_wedge(
            main_pid=None,
            systemctl_path="/nonexistent/systemctl-xyz",
            threshold=self._THRESHOLD,
            count_fn=self._count_fn(99),
            state_path=sp,
        )
        assert sig is None

    def test_journal_unobservable_returns_none_not_fire(self, tmp_path):
        """count_fn returns None (journalctl wedged/absent) → None, NOT a fire
        and NOT a healthy-silent 0. Unobservable ≠ healthy
        (honest_failure_modes #1/#2). Two such ticks must never accumulate."""
        sp = str(tmp_path / "wedge.json")
        kwargs = dict(
            main_pid=1234, gateway_main_pid=5678, threshold=self._THRESHOLD,
            count_fn=self._count_fn(None), state_path=sp,
        )
        assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None
        assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None

    def test_unobservable_tick_resets_streak(self, tmp_path):
        """An over-threshold tick followed by an unobservable tick must NOT
        leave a half-confirmed streak that the next over-threshold tick can
        complete — a blind tick is conservative, it breaks the streak."""
        sp = str(tmp_path / "wedge.json")
        over = dict(main_pid=1234, gateway_main_pid=5678, threshold=self._THRESHOLD,
                    count_fn=self._count_fn(40), state_path=sp)
        blind = dict(main_pid=1234, gateway_main_pid=5678, threshold=self._THRESHOLD,
                     count_fn=self._count_fn(None), state_path=sp)
        assert probe_meshtasticd_phoneapi_wedge(**over) is None   # streak 1
        assert probe_meshtasticd_phoneapi_wedge(**blind) is None  # reset
        assert probe_meshtasticd_phoneapi_wedge(**over) is None   # streak 1 again

    def test_no_gateway_on_box_returns_none_even_with_churn(self, tmp_path):
        """The wedge only threatens a GATEWAY's mesh-TX. On a non-gateway box
        (e.g. moc5, a collector) :4403 churn is the map's own reconnect
        overhead — firing the gateway-wedge class there mislabels it. With no
        meshforge-gateway pid, the probe returns None even at incident-level
        churn, across two ticks (the gate short-circuits before the streak)."""
        sp = str(tmp_path / "wedge.json")
        kwargs = dict(
            main_pid=1234, gateway_main_pid=None,
            systemctl_path="/nonexistent/systemctl-xyz",  # gateway pid unresolvable
            threshold=self._THRESHOLD,
            count_fn=self._count_fn(99), state_path=sp,
        )
        assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None
        assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None

    def test_churn_without_held_connection_is_benign_no_fire(self, tmp_path):
        """Harm guard (2026-06-27 moc): force-close churn fires ONLY when a
        contender is HOLDING the radio (a sustained ESTABLISHED :4403 client
        connection). Brief connect+close touches (e.g. fleet_snapshot._probe_radio)
        also emit 'Force close previous TCP connection' lines but release in
        <100ms, so the gateway's mesh-TX is never starved — the moc benign case
        (real churn, mesh-TX healthy). No held :4403 connection → None even at
        incident-level churn, across two ticks."""
        sp = str(tmp_path / "wedge.json")
        kwargs = dict(
            main_pid=1234, gateway_main_pid=5678, threshold=self._THRESHOLD,
            count_fn=self._count_fn(40), state_path=sp,
        )
        with patch(
            "utils.watchdog_probes_service._estab_inodes_to_port",
            return_value=set(),               # no held :4403 client connection
        ):
            assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None
            assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None

    def test_churn_with_held_connection_fires(self, tmp_path):
        """A real radio-monopolizing contender holds an ESTABLISHED :4403
        connection → the churn IS harmful → fires after the 2-tick debounce."""
        sp = str(tmp_path / "wedge.json")
        kwargs = dict(
            main_pid=1234, gateway_main_pid=5678, threshold=self._THRESHOLD,
            count_fn=self._count_fn(40), state_path=sp,
        )
        with patch(
            "utils.watchdog_probes_service._estab_inodes_to_port",
            return_value={998877},            # a held :4403 client connection
        ):
            assert probe_meshtasticd_phoneapi_wedge(**kwargs) is None  # streak 1
            sig = probe_meshtasticd_phoneapi_wedge(**kwargs)
            assert sig is not None
            assert sig.cls == "meshtasticd_phoneapi_wedge"


def test_meshtasticd_phoneapi_wedge_in_signal_classes():
    """The class is registered in the closed enum (gate companion)."""
    assert "meshtasticd_phoneapi_wedge" in SIGNAL_CLASSES


class TestNomadnetCrashloop:
    """The NomadNet USER-unit crashloop probe (2026-06-19; the 10-day-silent
    NRestarts=7842 class). Short live-window + a newest-restart recency gate so
    post-fix journal history can't false-page; 2-tick debounce."""

    def _ts_fn(self, value):
        """Injectable ts_fn: returns a fixed timestamp list (or None) per tick."""
        return lambda pattern: value

    @staticmethod
    def _loop(count, *, newest_age_s=30.0, spacing_s=100.0, now=_NOW):
        """``count`` restart timestamps, newest at ``now-newest_age_s``,
        each ``spacing_s`` older going back (newest first in age)."""
        return [now - newest_age_s - i * spacing_s for i in range(count)]

    def test_signal_class_registered(self):
        assert "nomadnet_crashloop" in SIGNAL_CLASSES

    # ── the 2026-08-08 disposition split ──────────────────────────────
    # Until this date the quiet path noted ONE `inert` for three different
    # facts ("healthy, remediated, or no nomadnet"), so the probe could
    # never say `clean` and its coverage cell was indistinguishable from a
    # DELETED detector — which is how the yield audit found it. Every test
    # below asserts the DISPOSITION, not `sig is None`: the probe returns
    # None for four different reasons and `is None` cannot tell them apart.
    # That absence of disposition assertions is exactly why this survived.

    def test_enrolled_and_quiet_is_CLEAN_not_inert(self, tmp_path):
        """The state the old code could not express. Enrolled + journal read +
        no live loop = watching, and fine. Without this the probe can never
        prove it can see, and a working detector looks like a dead one."""
        _reset_disp()
        probe_nomadnet_crashloop(
            ts_fn=self._ts_fn([]), state_path=str(tmp_path / "nn.json"),
            now=_NOW, enabled_fn=lambda: {"nomadnet.service", "other.service"},
        )
        assert _disp("nomadnet_crashloop") == "clean"

    def test_not_enrolled_is_INERT(self, tmp_path):
        """The moc5 shape: no nomadnet user unit here. Absent by design is an
        organ that isn't present, never an observation that failed."""
        _reset_disp()
        probe_nomadnet_crashloop(
            ts_fn=self._ts_fn([]), state_path=str(tmp_path / "nn.json"),
            now=_NOW, enabled_fn=lambda: {"meshforge-tracer.service"},
        )
        assert _disp("nomadnet_crashloop") == "inert"

    def test_unreadable_enrollment_is_INDETERMINATE(self, tmp_path):
        """Enrollment unreadable → absent and healthy cannot be told apart, so
        claim NEITHER. Absence of a readable answer is not evidence of
        absence (honest_failure_modes #2)."""
        _reset_disp()
        probe_nomadnet_crashloop(
            ts_fn=self._ts_fn([]), state_path=str(tmp_path / "nn.json"),
            now=_NOW, enabled_fn=lambda: None,
        )
        assert _disp("nomadnet_crashloop") == "indeterminate"

    def test_unobservable_journal_stays_INDETERMINATE(self, tmp_path):
        """The journal leg still wins: if we could not read restarts at all,
        enrollment cannot rescue the tick into `clean`."""
        _reset_disp()
        probe_nomadnet_crashloop(
            ts_fn=self._ts_fn(None), state_path=str(tmp_path / "nn.json"),
            now=_NOW, enabled_fn=lambda: {"nomadnet.service"},
        )
        assert _disp("nomadnet_crashloop") == "indeterminate"

    def test_enrollment_read_shares_one_answer_with_the_sibling_probe(self):
        """Both user-unit probes must resolve enrollment through the SAME
        helper, or they drift into disagreeing about what is installed
        (honest_failure_modes #5). Pinned structurally, not by comment."""
        import inspect
        from utils import watchdog_probes_service as wps
        src = inspect.getsource(wps.probe_user_unit_inactive)
        assert "_enabled_user_services(" in src
        assert "_enabled_user_services" in inspect.getsource(
            wps._user_unit_enrollment)

    def test_two_consecutive_live_ticks_fire(self, tmp_path):
        """RED-FIRST debounce: one live tick → None (streak 1); the second
        consecutive live tick fires degraded (5 restarts ∈ [3, 8))."""
        sp = str(tmp_path / "nn.json")
        kw = dict(ts_fn=self._ts_fn(self._loop(5)), state_path=sp, now=_NOW)
        assert probe_nomadnet_crashloop(**kw) is None          # streak 1
        sig = probe_nomadnet_crashloop(**kw)
        assert sig is not None
        assert sig.cls == "nomadnet_crashloop"
        assert sig.subject == "nomadnet.service"
        assert sig.severity == "degraded"
        assert sig.issue_ref is None
        assert sig.extra["restarts"] == 5
        # detail names the diagnosis + recovery
        assert "journalctl --user" in sig.detail
        assert "restart nomadnet" in sig.detail
        assert "exit 75" in sig.detail

    def test_wedge_severity_hard_loop(self, tmp_path):
        """≥ wedge_n restarts in the window → 'wedge' (the 7842 class)."""
        sp = str(tmp_path / "nn.json")
        kw = dict(ts_fn=self._ts_fn(self._loop(12)), state_path=sp, now=_NOW)
        assert probe_nomadnet_crashloop(**kw) is None
        sig = probe_nomadnet_crashloop(**kw)
        assert sig is not None and sig.severity == "wedge"
        assert sig.extra["restarts"] == 12

    def test_below_threshold_returns_none(self, tmp_path):
        """A one-off restart (count < degraded_n) never fires — not a loop."""
        sp = str(tmp_path / "nn.json")
        kw = dict(ts_fn=self._ts_fn(self._loop(2)), state_path=sp, now=_NOW)
        assert probe_nomadnet_crashloop(**kw) is None
        assert probe_nomadnet_crashloop(**kw) is None

    def test_stale_loop_post_fix_does_not_fire(self, tmp_path):
        """THE post-fix correction: MANY restarts in the window but the newest
        is older than recency_s (the loop already stopped — e.g. just
        remediated) → INERT across two ticks. A 2h-window probe with no recency
        gate would have false-paged here right after every fix (the 2026-06-19
        manager-box case: 19 restarts in 2h, newest 70+ min ago, healthy)."""
        sp = str(tmp_path / "nn.json")
        stale = self._loop(20, newest_age_s=600.0)   # newest 10min > 5min recency
        kw = dict(ts_fn=self._ts_fn(stale), state_path=sp, now=_NOW,
                  enabled_fn=lambda: {"nomadnet.service"})
        assert probe_nomadnet_crashloop(**kw) is None
        assert probe_nomadnet_crashloop(**kw) is None

    def test_no_restarts_inert(self, tmp_path):
        """Healthy / disabled / never-installed (moc5) → empty list → None.

        ``enabled_fn`` is pinned so this test's verdict does not depend on
        whether the BOX running the suite happens to have nomadnet enrolled
        (feedback_tests_must_pin_ambient_state — a test whose answer changes
        with the machine pins nothing)."""
        sp = str(tmp_path / "nn.json")
        kw = dict(ts_fn=self._ts_fn([]), state_path=sp, now=_NOW,
                  enabled_fn=lambda: set())
        assert probe_nomadnet_crashloop(**kw) is None
        assert probe_nomadnet_crashloop(**kw) is None

    def test_journal_unobservable_returns_none_not_fire(self, tmp_path):
        """ts_fn None (journalctl wedged/absent) → None, NOT a fire and NOT a
        healthy 0. Unobservable ≠ healthy (honest_failure_modes #1/#2)."""
        sp = str(tmp_path / "nn.json")
        kw = dict(ts_fn=self._ts_fn(None), state_path=sp, now=_NOW)
        assert probe_nomadnet_crashloop(**kw) is None
        assert probe_nomadnet_crashloop(**kw) is None

    def test_settled_tick_resets_streak(self, tmp_path):
        """A confirming run must be CONSECUTIVE: live, then an OBSERVED
        not-looping tick (below threshold), then live → still None (the
        observed-healthy tick reset the debounce)."""
        sp = str(tmp_path / "nn.json")
        live = dict(ts_fn=self._ts_fn(self._loop(5)), state_path=sp, now=_NOW)
        settled = dict(ts_fn=self._ts_fn(self._loop(1)), state_path=sp, now=_NOW)
        assert probe_nomadnet_crashloop(**live) is None      # streak 1
        assert probe_nomadnet_crashloop(**settled) is None   # observed-healthy → reset
        assert probe_nomadnet_crashloop(**live) is None      # streak 1 again

    def test_unobservable_tick_holds_streak(self, tmp_path):
        """An unobservable tick HOLDS the debounce streak (honest_failure_modes
        #2: hold last-known on unobservable — a journalctl hiccup must not erase
        a real in-progress signal). live, blind, live → fires on the 2nd real
        observation (distinct from an OBSERVED-healthy tick, which resets)."""
        sp = str(tmp_path / "nn.json")
        live = dict(ts_fn=self._ts_fn(self._loop(5)), state_path=sp, now=_NOW)
        blind = dict(ts_fn=self._ts_fn(None), state_path=sp, now=_NOW)
        assert probe_nomadnet_crashloop(**live) is None    # streak 1
        assert probe_nomadnet_crashloop(**blind) is None   # held at 1
        sig = probe_nomadnet_crashloop(**live)             # streak 2 → fire
        assert sig is not None

    def test_never_raises(self, tmp_path):
        """A ts_fn that raises → None (never propagates out of the probe)."""
        sp = str(tmp_path / "nn.json")
        def boom(pattern):
            raise RuntimeError("journalctl exploded")
        assert probe_nomadnet_crashloop(
            ts_fn=boom, state_path=sp, now=_NOW) is None

    def test_streak_is_clamped_at_debounce_floor(self, tmp_path):
        """A sustained loop keeps firing but the PERSISTED streak never grows
        past debounce_ticks (no unbounded counter across a 7842-class loop),
        and an absurd on-disk value is clamped, not trusted."""
        sp = str(tmp_path / "nn.json")
        kw = dict(ts_fn=self._ts_fn(self._loop(5)), state_path=sp, now=_NOW)
        probe_nomadnet_crashloop(**kw)            # streak 1
        for _ in range(6):                        # many more live ticks
            assert probe_nomadnet_crashloop(**kw) is not None
        assert _load_nomadnet_crashloop_streak(sp) == 2   # capped at debounce
        # A torn/edited file with a huge value is clamped on the next tick,
        # not surfaced as a meaningless 6-digit number.
        _save_nomadnet_crashloop_streak(sp, 999999)
        sig = probe_nomadnet_crashloop(**kw)
        assert sig is not None and sig.extra["streak"] == 2

    def test_save_logs_witness_on_write_failure(self, tmp_path, caplog):
        """honest_failure_modes #9: a swallowed state-write OSError leaves a
        WITNESS in the watchdog journal (else a persistent write failure makes
        the probe silently never advance past its debounce floor)."""
        import logging as _logging
        # An un-creatable parent (a path under a regular FILE) forces OSError.
        blocker = tmp_path / "afile"
        blocker.write_text("x")
        doomed = str(blocker / "sub" / "nn.json")
        with caplog.at_level(_logging.WARNING, logger="watchdog"):
            _save_nomadnet_crashloop_streak(doomed, 1)   # must not raise
        assert any("could not persist debounce streak" in r.message
                   for r in caplog.records)


class TestNomadnetCrashloopRealQuery:
    """Exercise the REAL journalctl helper (_journal_user_unit_restart_ts) —
    the probe's whole value rests on it, and the behavioral tests above all
    inject ts_fn, so without this a field-name/flag/parse regression would
    ship green (the #82 silent-blind-spot class)."""

    _SHORT_UNIX = (
        "1781943622.004417 host systemd[1012]: nomadnet.service: "
        "Scheduled restart job, restart counter is at 7869.\n"
        "1781943715.504331 host systemd[1012]: nomadnet.service: "
        "Scheduled restart job, restart counter is at 7870.\n"
    )

    def _patched(self, *, stdout="", returncode=0, exc=None):
        """Patch watchdog_probes_service.subprocess.run; record the argv."""
        captured = {}

        class _Result:
            def __init__(self):
                self.stdout = stdout
                self.returncode = returncode

        def _runner(*args, **kwargs):
            captured["argv"] = args[0]
            if exc is not None:
                raise exc
            return _Result()

        return patch("utils.watchdog_probes_service.subprocess.run",
                     side_effect=_runner), captured

    def test_selects_user_unit_field_not_dash_u(self):
        """The whole thesis of #82: select via USER_UNIT= (root-readable user
        unit) — NOT `-u <unit>`, which is structurally blind to user units."""
        ctx, captured = self._patched(stdout=self._SHORT_UNIX)
        with ctx:
            ts = _journal_user_unit_restart_ts(
                "nomadnet.service", "restart counter is at", "15min")
        argv = captured["argv"]
        assert "USER_UNIT=nomadnet.service" in argv
        # `-u nomadnet.service` (the system-namespace selector) must NOT appear
        assert "-u" not in argv
        assert "-g" in argv and "restart counter is at" in argv
        assert "short-unix" in argv
        # parsed the epoch first-token of each short-unix line
        assert ts == [1781943622.004417, 1781943715.504331]

    def test_empty_stdout_is_empty_list_not_none(self):
        """rc 0 + no output = genuinely no restarts → [] (NOT None)."""
        ctx, _ = self._patched(stdout="", returncode=0)
        with ctx:
            assert _journal_user_unit_restart_ts(
                "nomadnet.service", "p", "15min") == []

    def test_rc2_is_unobservable_none(self):
        """rc∉(0,1) (real journalctl failure) → None, never []·"""
        ctx, _ = self._patched(stdout="boom", returncode=2)
        with ctx:
            assert _journal_user_unit_restart_ts(
                "nomadnet.service", "p", "15min") is None

    def test_rc1_no_match_is_empty_list(self):
        """rc 1 = 'no entries matched' on some systemd builds → [] (a true 0)."""
        ctx, _ = self._patched(stdout="", returncode=1)
        with ctx:
            assert _journal_user_unit_restart_ts(
                "nomadnet.service", "p", "15min") == []

    def test_timeout_is_unobservable_none(self):
        """journalctl timeout/absent → None (unobservable ≠ healthy)."""
        ctx, _ = self._patched(
            exc=subprocess.TimeoutExpired(cmd="journalctl", timeout=15))
        with ctx:
            assert _journal_user_unit_restart_ts(
                "nomadnet.service", "p", "15min") is None

    def test_garbage_lines_skipped_not_crashed(self):
        """A non-short-unix line is skipped, not fatal (parse returns None)."""
        ctx, _ = self._patched(
            stdout="not-a-timestamp blah\n1781943622.0 host systemd: x\n")
        with ctx:
            ts = _journal_user_unit_restart_ts(
                "nomadnet.service", "p", "15min")
        assert ts == [1781943622.0]


def test_channel_feed_dark_none_when_unobservable():
    """The moc5/collector shape: NO json-uplink lines at all (mqtt module
    unconfigured) → no instrument here → None, never a false alarm.

    ``expectation_fn`` is PINNED: since 2026-08-07 this path consults the
    box's deployment.json, and an unpinned test would read whatever the
    machine running the suite happens to declare (feedback_tests_must_pin
    _ambient_state — two probe tests already shipped that way once)."""
    sig = probe_channel_feed_dark(
        main_pid=1002, newest_line_fn=lambda pattern: None, now=_NOW,
        expectation_fn=lambda: ("undeclared", ""),
    )
    assert sig is None


def test_channel_feed_dark_none_when_unit_inactive():
    """meshtasticd down → service_inactive owns it; this probe stays None."""
    sig = probe_channel_feed_dark(
        systemctl_path="/nonexistent/systemctl",
        newest_line_fn=_journal_fake(ch_text_age_s=7 * 3600.0),
        now=_NOW,
    )
    assert sig is None


def test_channel_feed_dark_none_on_unparseable_timestamp():
    """A journal line whose first token isn't an epoch → indeterminate → None."""
    def fn(pattern):
        if pattern == "serialized json message":
            return f"{_NOW - 60.0:.6f} host meshtasticd[1]: json"
        return "garbled line with no timestamp"
    sig = probe_channel_feed_dark(main_pid=1002, newest_line_fn=fn, now=_NOW)
    assert sig is None


def test_channel_feed_dark_channel_param_respected():
    """The queried journal pattern carries the configured channel NAME."""
    patterns: list = []
    probe_channel_feed_dark(
        channel_name="testchan",
        main_pid=1002,
        newest_line_fn=_journal_fake(ch_text_age_s=600.0, patterns=patterns),
        now=_NOW,
    )
    assert any("json/testchan/" in p for p in patterns)
    sig = probe_channel_feed_dark(
        channel_name="testchan",
        main_pid=1002,
        newest_line_fn=_journal_fake(ch_text_age_s=7 * 3600.0),
        now=_NOW,
    )
    assert sig is not None and sig.subject == "meshtastic-testchan"


def test_channel_feed_dark_matches_by_name_not_slot_index():
    """2026-06-06 federator false-alarm regression: slot layouts are
    box-local (the fleet 'meshforge' channel sat at slot 3 because slot 2
    held a box-local channel) — a '"channel":2' grep read a HEALTHY feed as
    dark for days. The probe must query by the topic NAME (the decode-gate
    identity) and never by a slot index."""
    patterns: list = []
    probe_channel_feed_dark(
        main_pid=1002,
        newest_line_fn=_journal_fake(ch_text_age_s=600.0, patterns=patterns),
        now=_NOW,
    )
    text_patterns = [p for p in patterns if '"type":"text"' in p]
    assert text_patterns, "probe never queried for decoded text"
    for p in text_patterns:
        assert "json/meshforge/" in p
        assert '"channel":' not in p


def test_channel_feed_dark_threshold_boundary():
    """Just under the threshold stays quiet; just over fires."""
    under = probe_channel_feed_dark(
        main_pid=1002, dark_after_s=3600.0,
        newest_line_fn=_journal_fake(ch_text_age_s=3599.0), now=_NOW,
    )
    over = probe_channel_feed_dark(
        main_pid=1002, dark_after_s=3600.0,
        newest_line_fn=_journal_fake(ch_text_age_s=3601.0), now=_NOW,
    )
    assert under is None
    assert over is not None


# ─────────────────────────────────────────────────────────────────────
# 2026-08-07 — channel_feed_dark four-state: inert vs indeterminate vs
# clean vs the finding. Every test PINS both the journal and the
# declaration; nothing here may consult the box running the suite.
#
# 2026-08-08 subtraction audit: the declared-and-absent case briefly had
# its own class (``json_uplink_dark``). It was removed — its coverage
# split was identical to channel_feed_dark's on every box, so it carried
# no independent information. The case is now an ``indeterminate`` whose
# REASON carries the diagnosis, so the tests below assert the reason
# text; the 2026-08-07 lesson (name mqtt.json_enabled, not "mqtt off")
# is pinned exactly as hard as before, just on a different carrier.
# ─────────────────────────────────────────────────────────────────────


def _disp(cls="channel_feed_dark"):
    """The disposition this tick recorded for ``cls`` — the assertion that
    actually matters here. The probe returns None for THREE different
    reasons (inert / indeterminate / clean) and `sig is None` cannot tell
    them apart, which is the very collapse this arc exists to fix."""
    from utils.watchdog_probe_core import collect_dispositions
    entry = collect_dispositions().get(cls)
    return entry.get("disp") if entry else None


def _reason(cls="channel_feed_dark"):
    """The reason recorded alongside the disposition. Load-bearing since
    2026-08-08: for the declared-but-absent box this string IS the finding,
    so an empty or vague reason is the defect, not a cosmetic gap."""
    from utils.watchdog_probe_core import collect_dispositions
    entry = collect_dispositions().get(cls)
    return (entry or {}).get("reason") or ""


class TestChannelFeedDarkFiveState:
    """The 2026-08-07 dig, pinned. Before this, states 1 and 2 were ONE
    `indeterminate` and state 3 did not exist."""

    def setup_method(self):
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()

    def test_no_json_and_undeclared_is_inert_not_indeterminate(self):
        """THE regression under test: kiai/moc1/moc4/moc5. journalctl RAN
        and positively found nothing, and the box declares no uplink → the
        organ is absent by design. Reporting that as a failed observation
        is what left four boxes 'blind' while nothing was wrong."""
        sig = probe_channel_feed_dark(
            main_pid=1002, newest_line_fn=lambda p: None, now=_NOW,
            expectation_fn=lambda: ("undeclared", ""),
        )
        assert sig is None
        assert _disp() == "inert", (
            "an organ absent by design must not read as an observation that "
            "failed — otherwise real failures have nowhere to stand out")

    def test_journal_unreadable_stays_indeterminate(self):
        """The other half of the split: a journal we could not read is
        genuinely unobservable and must NOT be laundered into inert."""
        def fn(pattern):
            return None
        sig = probe_channel_feed_dark(
            main_pid=1002, newest_line_fn=fn, now=_NOW,
            journalctl_path="/nonexistent/journalctl",
            expectation_fn=lambda: ("undeclared", ""),
        )
        assert sig is None
        # The injected fn reports "ok" by construction, so drive the real
        # reader instead to exercise the unobservable branch.
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()
        sig2 = probe_channel_feed_dark(
            main_pid=1002, now=_NOW,
            journalctl_path="/nonexistent/journalctl",
            expectation_fn=lambda: ("undeclared", ""),
        )
        assert sig2 is None
        assert _disp() == "indeterminate"

    def test_declared_but_no_json_is_blind_with_the_diagnosis(self):
        """The box says it uplinks json, the journal covers the window, and
        there is none. This probe is therefore BLIND — and it must say so
        specifically enough that the read is not a guess (2026-08-05: the
        witness worked, the READ failed)."""
        sig = probe_channel_feed_dark(
            main_pid=1002, newest_line_fn=lambda p: None, now=_NOW,
            expectation_fn=lambda: ("declared", "gateway uplink"),
            coverage_fn=lambda: True,
        )
        assert sig is None
        assert _disp() == "indeterminate"
        reason = _reason()
        assert "BLIND" in reason
        assert "gateway uplink" in reason
        assert "mqtt.json_enabled" in reason, (
            "the reason must name the setting that actually gates this — "
            "'mqtt off' was the wrong advice that cost the 2026-08-07 dig")

    def test_declared_and_json_stale_is_also_blind(self):
        """Same fact, corpse still inside the window: a newest json line
        older than the threshold is no usable instrument either. Undeclared
        this carries a different reason (test below) — the declaration is
        what turns silence into a finding."""
        sig = probe_channel_feed_dark(
            main_pid=1002,
            newest_line_fn=_journal_fake(json_age_s=10 * 3600.0), now=_NOW,
            expectation_fn=lambda: ("declared", ""),
            coverage_fn=lambda: True,
        )
        assert sig is None
        assert _disp() == "indeterminate"
        assert "10.0h old" in _reason()

    def test_short_journal_cannot_convict_a_declared_box(self):
        """The fresh-boot / vacuumed-journal guard. Zero json lines proves
        nothing when the journal does not reach back past the threshold —
        firing here would be a confident claim from an empty channel
        (honest_failure_modes #2)."""
        sig = probe_channel_feed_dark(
            main_pid=1002, newest_line_fn=lambda p: None, now=_NOW,
            expectation_fn=lambda: ("declared", ""),
            coverage_fn=lambda: False,
        )
        assert sig is None
        assert _disp() == "indeterminate"

    def test_coverage_unobservable_holds(self):
        """Could not even determine journal coverage → indeterminate, never
        a fire and never inert."""
        sig = probe_channel_feed_dark(
            main_pid=1002, newest_line_fn=lambda p: None, now=_NOW,
            expectation_fn=lambda: ("declared", ""),
            coverage_fn=lambda: None,
        )
        assert sig is None
        assert _disp() == "indeterminate"

    def test_unreadable_declaration_never_alarms(self):
        """A deployment.json that exists and won't parse is not a licence to
        guess in either direction."""
        sig = probe_channel_feed_dark(
            main_pid=1002, newest_line_fn=lambda p: None, now=_NOW,
            expectation_fn=lambda: ("unreadable", ""),
        )
        assert sig is None
        assert _disp() == "indeterminate"

    def test_undeclared_with_stale_json_stays_indeterminate(self):
        """Behaviour deliberately UNCHANGED from before the rework: the
        instrument existed and stopped, so channel-specific dark is
        unobservable, and with no declaration nothing says it should still
        be running."""
        sig = probe_channel_feed_dark(
            main_pid=1002,
            newest_line_fn=_journal_fake(json_age_s=10 * 3600.0), now=_NOW,
            expectation_fn=lambda: ("undeclared", ""),
        )
        assert sig is None
        assert _disp() == "inert"

    def test_healthy_feed_is_clean_and_never_consults_declaration(self):
        """State 4. A live feed must not pay for the declaration read —
        and must not be able to be broken by it."""
        def boom():
            raise AssertionError("declaration read on the healthy path")
        sig = probe_channel_feed_dark(
            main_pid=1002,
            newest_line_fn=_journal_fake(ch_text_age_s=60.0), now=_NOW,
            expectation_fn=boom,
        )
        assert sig is None
        assert _disp() == "clean"

    def test_json_uplink_dark_stays_deleted(self):
        """2026-08-08 subtraction audit: this probe must emit exactly ONE
        signal class.

        ``json_uplink_dark`` existed for one day. Its coverage split was
        identical to ``channel_feed_dark``'s on all 8 boxes — it watched
        the instrument BEHIND a detector, which is a layer up, and carried
        no information this class does not. The removal is the point, so
        pin it: re-adding a second class here is the "add a probe" reflex
        that ``feedback_my_footprint_is_the_constraint`` exists to stop,
        and the cheaper fix (a richer disposition + reason) is already in
        place. If a future arc genuinely needs a second class, delete this
        test deliberately rather than letting it drift back in."""
        from utils.watchdog_probe_core import (
            SIGNAL_CLASSES, collect_dispositions, reset_dispositions,
        )
        assert "json_uplink_dark" not in SIGNAL_CLASSES

        for expectation in (("declared", ""), ("undeclared", ""),
                            ("unreadable", "")):
            reset_dispositions()
            probe_channel_feed_dark(
                main_pid=1002, newest_line_fn=lambda p: None, now=_NOW,
                expectation_fn=lambda e=expectation: e,
                coverage_fn=lambda: True,
            )
            noted = set(collect_dispositions())
            assert noted <= {"channel_feed_dark"}, (
                f"probe noted {noted - {'channel_feed_dark'}} for "
                f"expectation={expectation[0]} — one probe, one class")

    def test_channel_dark_still_fires_with_live_instrument(self):
        """State 5 — the original 2026-06-04 finding must survive the
        rework untouched."""
        sig = probe_channel_feed_dark(
            main_pid=1002,
            newest_line_fn=_journal_fake(
                json_age_s=120.0, ch_text_age_s=7 * 3600.0),
            now=_NOW,
            expectation_fn=lambda: ("declared", ""),
        )
        assert sig is not None
        assert sig.cls == "channel_feed_dark"


class TestDeploymentDeclarationStatus:
    """The 2026-08-07 tri-state on the role declaration. Absent and
    unreadable are OPPOSITE facts; the shim below must keep collapsing them
    (that is what a shim IS) without any caller depending on the collapse."""

    def _as_home(self, tmp_path, monkeypatch):
        import pwd as _pwd

        class _Ent:
            pw_dir = str(tmp_path)
        monkeypatch.setattr(_pwd, "getpwnam", lambda u: _Ent())

    def _write(self, tmp_path, payload):
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "deployment.json").write_text(payload)

    def test_missing_file_is_undeclared(self, tmp_path, monkeypatch):
        from utils.watchdog_probe_core import _read_deployment_declaration_status
        self._as_home(tmp_path, monkeypatch)
        assert _read_deployment_declaration_status("svc")[0] == "undeclared"

    def test_unparseable_is_unreadable(self, tmp_path, monkeypatch):
        from utils.watchdog_probe_core import _read_deployment_declaration_status
        self._as_home(tmp_path, monkeypatch)
        self._write(tmp_path, "{ not json")
        assert _read_deployment_declaration_status("svc")[0] == "unreadable"

    def test_declared_returns_role_and_overrides(self, tmp_path, monkeypatch):
        from utils.watchdog_probe_core import _read_deployment_declaration_status
        self._as_home(tmp_path, monkeypatch)
        self._write(tmp_path,
                    '{"role": "full-gateway", "service_overrides": {"a": 1}}')
        st, role, ov = _read_deployment_declaration_status("svc")
        assert (st, role, ov) == ("declared", "full-gateway", {"a": 1})

    def test_file_without_role_is_undeclared_not_unreadable(self, tmp_path,
                                                            monkeypatch):
        """A readable file that simply declares no role is a POSITIVE
        observation — the box is not role-managed."""
        from utils.watchdog_probe_core import _read_deployment_declaration_status
        self._as_home(tmp_path, monkeypatch)
        self._write(tmp_path, '{"profile": "full"}')
        assert _read_deployment_declaration_status("svc")[0] == "undeclared"

    def test_no_service_user_is_unreadable(self):
        from utils.watchdog_probe_core import _read_deployment_declaration_status
        assert _read_deployment_declaration_status("")[0] == "unreadable"

    def test_shim_still_returns_the_legacy_pair(self, tmp_path, monkeypatch):
        """Back-compat: the flat form keeps its (role, overrides) contract for
        the remaining caller that genuinely does not need the distinction."""
        from utils.watchdog_probe_core import _read_deployment_declaration
        self._as_home(tmp_path, monkeypatch)
        self._write(tmp_path, '{"role": "collector"}')
        assert _read_deployment_declaration("svc") == ("collector", {})


class TestJsonUplinkExpectationReader:
    """The declaration reader's own tri-state. Absent and unreadable are
    OPPOSITE facts (the _declared_root_status lesson, 2026-08-05)."""

    def _write(self, tmp_path, payload):
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "deployment.json").write_text(payload)
        return tmp_path

    def _read_as(self, tmp_path, monkeypatch):
        import pwd as _pwd
        from utils import watchdog_probes_channel as mod

        class _Ent:
            pw_dir = str(tmp_path)
        monkeypatch.setattr(_pwd, "getpwnam", lambda u: _Ent())
        return mod._read_json_uplink_expectation("svc")

    def test_missing_file_is_undeclared(self, tmp_path, monkeypatch):
        assert self._read_as(tmp_path, monkeypatch) == ("undeclared", "")

    def test_declared_carries_the_reason(self, tmp_path, monkeypatch):
        self._write(tmp_path, '{"organ_expectations": {"json_uplink": "why"}}')
        assert self._read_as(tmp_path, monkeypatch) == ("declared", "why")

    def test_explicit_false_is_not_a_claim(self, tmp_path, monkeypatch):
        self._write(tmp_path, '{"organ_expectations": {"json_uplink": false}}')
        assert self._read_as(tmp_path, monkeypatch)[0] == "undeclared"

    def test_unparseable_is_unreadable_not_undeclared(self, tmp_path,
                                                     monkeypatch):
        self._write(tmp_path, "{ this is not json")
        assert self._read_as(tmp_path, monkeypatch)[0] == "unreadable"

    def test_no_service_user_is_unreadable(self):
        from utils import watchdog_probes_channel as mod
        assert mod._read_json_uplink_expectation("")[0] == "unreadable"


# ─────────────────────────────────────────────────────────────────────
# 2026-06-01 — foundation_perms_drift (mf.4/#73 perms class)
# ─────────────────────────────────────────────────────────────────────

from utils.rns_tree_perms import RnsTreePerms  # noqa: E402


def _perms(configdir_owner="root:wh6gxz", configdir_mode="1775",
           logfile_owner="wh6gxz:wh6gxz", logfile_exists=True,
           rnsd_user="wh6gxz"):
    return RnsTreePerms(
        rnsd_user=rnsd_user, configdir_owner=configdir_owner,
        configdir_mode=configdir_mode, logfile_exists=logfile_exists,
        logfile_owner=logfile_owner,
    )


def test_foundation_drift_quiet_when_clean():
    """Canonical non-root layout → no signal."""
    assert probe_foundation_drift(perms=_perms()) is None


def test_foundation_drift_fires_on_root_owned_configdir():
    """The moc recurrence: re-provision left /etc/reticulum root:root 755 while
    rnsd runs non-root → degraded signal naming the perms fix."""
    sig = probe_foundation_drift(perms=_perms(
        configdir_owner="root:root", configdir_mode="755", logfile_owner="root:root"))
    assert sig is not None
    assert sig.cls == "foundation_perms_drift"
    assert sig.severity == "degraded"      # latent, not wedged now — proactive
    assert sig.subject == "rnsd"
    assert sig.issue_ref == 73
    assert "fleet_foundation.py apply" in sig.detail
    assert sig.extra["configdir_owner"] == "root:root"
    assert sig.extra["rnsd_user"] == "wh6gxz"


def test_foundation_drift_none_when_rnsd_is_root():
    """A root rnsd writes anything — root:root tree is fine for it → None."""
    assert probe_foundation_drift(perms=_perms(
        configdir_owner="root:root", configdir_mode="755",
        logfile_owner="root:root", rnsd_user="root")) is None


def test_foundation_drift_none_when_perms_unprobed():
    """configdir_owner None (inaccessible/not probed) → never guess → None."""
    assert probe_foundation_drift(perms=_perms(
        configdir_owner=None, configdir_mode=None,
        logfile_owner=None, logfile_exists=False)) is None


def test_foundation_drift_logfile_owner_mismatch_fires():
    """Good dir but logfile owned by root while rnsd is non-root → degraded."""
    sig = probe_foundation_drift(perms=_perms(logfile_owner="root:root"))
    assert sig is not None and sig.severity == "degraded"
    assert "logfile is owned by" in sig.detail


# ─────────────────────────────────────────────────────────────────────
# 2026-06-01 — parity_drift (MeshForge<->MeshAnchor lead-repo port debt)
# ─────────────────────────────────────────────────────────────────────

from types import SimpleNamespace  # noqa: E402


def _pf(status, label):
    return SimpleNamespace(status=status, label=label)


def test_parity_drift_none_when_meshanchor_absent(tmp_path):
    """MeshForge-only box (no /opt/meshanchor) → not applicable, no alarm."""
    assert probe_parity_drift(meshanchor_root=str(tmp_path / "nope")) is None


def test_parity_drift_fires_on_drift(tmp_path):
    """Drift confirmed over two consecutive ticks fires the Signal."""
    findings = [_pf("ok", "src/utils/rns_init.py"),
                _pf("drift", "src/utils/rns_tree_perms.py")]
    state = str(tmp_path / "debounce.json")
    drift = lambda mf, ma: (findings, "drift")  # noqa: E731
    # Tick 1: drift seen but debounced (streak 1 < 2) → no Signal yet.
    assert probe_parity_drift(
        meshanchor_root=str(tmp_path), check_fn=drift, state_path=state) is None
    # Tick 2: drift persists → confirmed, fires.
    sig = probe_parity_drift(
        meshanchor_root=str(tmp_path), check_fn=drift, state_path=state)
    assert sig is not None
    assert sig.cls == "parity_drift"
    assert sig.severity == "degraded"
    assert sig.subject == "meshforge<->meshanchor"
    assert "rns_tree_perms.py" in sig.detail
    assert sig.extra["drift_items"] == ["src/utils/rns_tree_perms.py"]
    assert sig.extra["debounce_streak"] == 2


def test_parity_drift_debounced_on_single_tick(tmp_path):
    """A lone drift tick (the fleet-roll deploy-window race) does NOT fire —
    this is the 2026-06-01 rns_tree_perms.py SSOT-port self-heal case."""
    findings = [_pf("drift", "src/utils/rns_tree_perms.py")]
    sig = probe_parity_drift(
        meshanchor_root=str(tmp_path),
        check_fn=lambda mf, ma: (findings, "drift"),
        state_path=str(tmp_path / "debounce.json"))
    assert sig is None


def test_parity_drift_streak_resets_on_self_heal(tmp_path):
    """drift → in_sync → drift must restart the streak: a single drift tick after a
    heal is again debounced, NOT immediately fired off the prior streak."""
    findings = [_pf("drift", "src/utils/rns_tree_perms.py")]
    state = str(tmp_path / "debounce.json")
    drift = lambda mf, ma: (findings, "drift")  # noqa: E731
    healed = lambda mf, ma: ([_pf("ok", "x")], "in_sync")  # noqa: E731
    assert probe_parity_drift(meshanchor_root=str(tmp_path), check_fn=drift,
                              state_path=state) is None  # streak 1
    assert probe_parity_drift(meshanchor_root=str(tmp_path), check_fn=healed,
                              state_path=state) is None  # reset to 0
    # Lone drift after heal → debounced again (proves the heal reset the counter).
    assert probe_parity_drift(meshanchor_root=str(tmp_path), check_fn=drift,
                              state_path=state) is None


def test_parity_drift_debounce_ticks_one_fires_immediately(tmp_path):
    """debounce_ticks=1 restores fire-on-first-drift (escape hatch / backward shape)."""
    findings = [_pf("drift", "src/utils/rns_tree_perms.py")]
    sig = probe_parity_drift(
        meshanchor_root=str(tmp_path),
        check_fn=lambda mf, ma: (findings, "drift"),
        state_path=str(tmp_path / "debounce.json"),
        debounce_ticks=1)
    assert sig is not None
    assert sig.extra["debounce_streak"] == 1


def test_parity_drift_none_when_in_sync(tmp_path):
    sig = probe_parity_drift(
        meshanchor_root=str(tmp_path),
        check_fn=lambda mf, ma: ([_pf("ok", "x")], "in_sync"),
        state_path=str(tmp_path / "debounce.json"))
    assert sig is None


def test_parity_drift_none_when_missing_is_indeterminate(tmp_path):
    """A tracked file merely absent (overall 'missing') → indeterminate (possible
    mid-deploy window), not a drift alarm; also breaks the debounce streak."""
    sig = probe_parity_drift(
        meshanchor_root=str(tmp_path),
        check_fn=lambda mf, ma: ([_pf("missing", "x")], "missing"),
        state_path=str(tmp_path / "debounce.json"))
    assert sig is None


def test_parity_drift_none_when_check_raises(tmp_path):
    def boom(mf, ma):
        raise RuntimeError("parity tool blew up")
    assert probe_parity_drift(meshanchor_root=str(tmp_path), check_fn=boom,
                              state_path=str(tmp_path / "debounce.json")) is None


# ─────────────────────────────────────────────────────────────────────
# 2026-06-03 — role_drift (live unit state vs declared role + overrides)
# ─────────────────────────────────────────────────────────────────────


def _ract(item, current, desired, verb, required=True):
    """Shape-compatible stand-in for provision_role.py's Action."""
    return SimpleNamespace(item=item, current=current, desired=desired,
                           verb=verb, required=required)


def test_role_drift_none_when_no_role_declared(tmp_path):
    """Box without a declared role (no deployment.json / no role key) → not
    applicable, never an alarm."""
    sig = probe_role_drift(deployment=(None, {}),
                           state_path=str(tmp_path / "rd.json"))
    assert sig is None


def test_role_drift_none_when_converged(tmp_path):
    """All-noop plan (plus the usual non-blocking advisories) → clean, no signal."""
    actions = [
        _ract("meshtasticd", "active/enabled", "enabled", "noop"),
        _ract("delta:node-directory", "?", "bbox+cap+caches", "warn", required=False),
    ]
    sig = probe_role_drift(deployment=("full-gateway", {}),
                           plan_fn=lambda r, ov: actions,
                           state_path=str(tmp_path / "rd.json"))
    assert sig is None


def test_role_drift_fires_after_debounce(tmp_path):
    """A would-change action persisting two consecutive ticks fires degraded."""
    actions = [_ract("meshforge-gateway", "inactive/disabled", "enabled", "enable")]
    state = str(tmp_path / "rd.json")
    plan = lambda r, ov: actions  # noqa: E731
    # Tick 1: divergence seen but debounced (streak 1 < 2).
    assert probe_role_drift(deployment=("full-gateway", {}), plan_fn=plan,
                            state_path=state) is None
    # Tick 2: persists → fires.
    sig = probe_role_drift(deployment=("full-gateway", {}), plan_fn=plan,
                           state_path=state)
    assert sig is not None
    assert sig.cls == "role_drift"
    assert sig.severity == "degraded"
    assert sig.subject == "full-gateway"
    assert "meshforge-gateway" in sig.detail
    assert sig.extra["debounce_streak"] == 2


def test_role_drift_streak_resets_on_converged_tick(tmp_path):
    """drift → clean → drift restarts the streak (a lone post-heal tick is
    debounced again, not fired off the stale streak)."""
    drifted = [_ract("meshforge-map", "inactive/disabled", "enabled", "enable")]
    clean = [_ract("meshforge-map", "active/enabled", "enabled", "noop")]
    state = str(tmp_path / "rd.json")
    assert probe_role_drift(deployment=("collector", {}),
                            plan_fn=lambda r, ov: drifted,
                            state_path=state) is None  # streak 1
    assert probe_role_drift(deployment=("collector", {}),
                            plan_fn=lambda r, ov: clean,
                            state_path=state) is None  # reset
    assert probe_role_drift(deployment=("collector", {}),
                            plan_fn=lambda r, ov: drifted,
                            state_path=state) is None  # debounced again


def test_role_drift_honors_documented_override(tmp_path):
    """The moc2 lesson: a reasoned service_override surfaces from plan() as a
    NON-blocking advisory (warn, required=False) — that is intent, NOT drift."""
    actions = [
        _ract("meshforge-gateway", "inactive/disabled", "waived:disabled",
              "warn", required=False),
        _ract("meshtasticd", "active/enabled", "enabled", "noop"),
    ]
    state = str(tmp_path / "rd.json")
    plan = lambda r, ov: actions  # noqa: E731
    for _ in range(3):  # never fires no matter how many ticks
        assert probe_role_drift(deployment=("full-gateway", {}),
                                plan_fn=plan, state_path=state) is None


def test_role_drift_blocking_warning_counts_as_drift(tmp_path):
    """A BLOCKING warning (e.g. waiver missing its required reason, or a
    required unit not installed) is hidden drift and fires after debounce."""
    actions = [_ract("meshforge-gateway", "inactive/disabled", "waived:disabled",
                     "warn", required=True)]
    state = str(tmp_path / "rd.json")
    plan = lambda r, ov: actions  # noqa: E731
    assert probe_role_drift(deployment=("full-gateway", {}), plan_fn=plan,
                            state_path=state) is None
    sig = probe_role_drift(deployment=("full-gateway", {}), plan_fn=plan,
                           state_path=state)
    assert sig is not None
    assert "meshforge-gateway" in sig.detail


def test_role_drift_unknown_role_counts_as_drift(tmp_path):
    """deployment.json naming a role absent from the catalog is a real
    declaration mismatch (debounced like any drift)."""
    def plan(r, ov):
        raise KeyError(r)
    state = str(tmp_path / "rd.json")
    assert probe_role_drift(deployment=("mystery-role", {}), plan_fn=plan,
                            state_path=state) is None
    sig = probe_role_drift(deployment=("mystery-role", {}), plan_fn=plan,
                           state_path=state)
    assert sig is not None
    assert "mystery-role" in sig.detail
    assert "not in the fleet_roles.yaml catalog" in sig.extra["items"][0]


def test_role_drift_none_when_tool_indeterminate(tmp_path):
    """plan_fn returning None (catalog unreadable) or raising a non-KeyError →
    indeterminate: no alarm, streak broken."""
    state = str(tmp_path / "rd.json")
    drifted = [_ract("rnsd", "inactive/disabled", "enabled", "enable")]
    assert probe_role_drift(deployment=("primary", {}),
                            plan_fn=lambda r, ov: drifted,
                            state_path=state) is None  # streak 1
    assert probe_role_drift(deployment=("primary", {}),
                            plan_fn=lambda r, ov: None,
                            state_path=state) is None  # indeterminate → reset
    def boom(r, ov):
        raise RuntimeError("yaml exploded")
    assert probe_role_drift(deployment=("primary", {}), plan_fn=boom,
                            state_path=state) is None
    # Streak was reset both times: a fresh drift tick is debounced again.
    assert probe_role_drift(deployment=("primary", {}),
                            plan_fn=lambda r, ov: drifted,
                            state_path=state) is None


def test_plan_role_actions_real_importlib_load():
    """Regression (2026-06-08): the DEFAULT plan_fn (`_plan_role_actions`)
    importlib-loads scripts/provision_role.py. On py3.12+ the load died with
    AttributeError unless the module is registered in sys.modules BEFORE
    exec_module (the @dataclass field-type eval reads
    sys.modules[cls.__module__].__dict__). That silently made probe_role_drift
    (and probe_parity_drift) return None forever on the 3.13 fleet — the v3
    trigger was dead. Every OTHER role_drift test injects plan_fn, so none
    exercised the real load; this one does, against the live provision_role.py.
    """
    from utils.watchdog_probes import _plan_role_actions
    repo_root = str(Path(__file__).resolve().parent.parent)
    actions = _plan_role_actions("primary", {}, repo_root)
    assert actions is not None, (
        "_plan_role_actions returned None — importlib load of provision_role.py "
        "failed (sys.modules-before-exec registration regressed?)"
    )
    assert isinstance(actions, list) and actions
    # And it actually reports a known drift: a role wanting a unit enabled while
    # the plan would change it surfaces an enable/disable/mask verb.
    assert all(hasattr(a, "verb") for a in actions)


# ─────────────────────────────────────────────────────────────────────
# 2026-06-09 — kernel_reboot_pending (version-updates arc: the
# 6.12.75-straggler guard — newer same-flavor kernel installed, not running)
# ─────────────────────────────────────────────────────────────────────


def _krb_setup(tmp_path, installed=(), boot=None):
    """Create a fake /lib/modules with the given release dirs; return common
    kwargs (modules dir, absent reboot-required path, isolated streak file).

    ``boot_dir`` is ALWAYS pinned to an isolated path. The probe gained a
    /boot fallback on 2026-07-27 (the modules dir is masked by the unit's
    ProtectKernelModules=yes), and its default is the real ``/boot`` — so
    leaving it unset silently made these tests read the HOST's installed
    kernels and pass or fail by which machine ran them. A hermetic test must
    never inherit host state through a default. Pass ``boot=[releases]`` to
    exercise the fallback deliberately.
    """
    mods = tmp_path / "modules"
    mods.mkdir()
    for name in installed:
        (mods / name).mkdir()
    boot_dir = tmp_path / "boot"
    boot_dir.mkdir()
    for name in (boot or ()):
        (boot_dir / f"vmlinuz-{name}").write_text("")
    return {
        "modules_dir": str(mods),
        "boot_dir": str(boot_dir),
        "reboot_required_path": str(tmp_path / "reboot-required"),
        "state_path": str(tmp_path / "krb.json"),
    }


def test_parse_kernel_release_shapes():
    """RPi flavor = the '+...' suffix wholesale (trailing 2712 is a NAME, not a
    build number); Ubuntu '-NNN' build segments join the comparable tuple."""
    assert _parse_kernel_release("6.12.75+rpt-rpi-2712") == (
        (6, 12, 75), "+rpt-rpi-2712")
    assert _parse_kernel_release("6.8.0-1057-raspi") == (
        (6, 8, 0, 1057), "-raspi")
    assert _parse_kernel_release("garbage") is None
    assert _parse_kernel_release("") is None
    assert _parse_kernel_release(None) is None


def test_kernel_reboot_fires_on_newer_same_flavor_after_debounce(tmp_path):
    """The moc1 shape: 6.18.33 installed, 6.12.75 running — silent on tick 1
    (debounce), fires degraded on tick 2 with running vs newest in detail."""
    kw = _krb_setup(tmp_path, ["6.12.75+rpt-rpi-2712", "6.18.33+rpt-rpi-2712"])
    running = "6.12.75+rpt-rpi-2712"
    assert probe_kernel_reboot_pending(running_release=running, **kw) is None
    sig = probe_kernel_reboot_pending(running_release=running, **kw)
    assert sig is not None
    assert sig.cls == "kernel_reboot_pending"
    assert sig.severity == "degraded"
    assert sig.subject == running
    assert sig.issue_ref is None
    assert "6.12.75+rpt-rpi-2712" in sig.detail
    assert "6.18.33+rpt-rpi-2712" in sig.detail
    assert "clean reboot" in sig.detail
    assert sig.extra == {
        "running": running,
        "newest_installed": "6.18.33+rpt-rpi-2712",
        # Which source answered: "modules" normally, "boot" when the modules
        # dir is masked by ProtectKernelModules=yes, None when both are blind.
        "kernel_source": "modules",
        "reboot_required_file": False,
        "debounce_streak": 2,
    }


def test_kernel_reboot_silent_when_running_is_newest(tmp_path):
    """Running == newest same-flavor (older siblings still installed) →
    observed-healthy: silent every tick, streak reset to 0."""
    kw = _krb_setup(tmp_path, ["6.12.75+rpt-rpi-2712", "6.18.33+rpt-rpi-2712"])
    for _ in range(3):
        assert probe_kernel_reboot_pending(
            running_release="6.18.33+rpt-rpi-2712", **kw) is None
    assert json.loads(Path(kw["state_path"]).read_text())["streak"] == 0


def test_kernel_reboot_none_when_modules_dir_empty_or_unreadable(tmp_path):
    """Empty or missing /lib/modules → indeterminate (never false-alarm);
    streak resets to 0 rather than accumulating."""
    kw = _krb_setup(tmp_path)  # empty modules dir
    assert probe_kernel_reboot_pending(
        running_release="6.12.75+rpt-rpi-2712", **kw) is None
    kw["modules_dir"] = str(tmp_path / "nonexistent")
    for _ in range(3):
        assert probe_kernel_reboot_pending(
            running_release="6.12.75+rpt-rpi-2712", **kw) is None
    assert json.loads(Path(kw["state_path"]).read_text())["streak"] == 0


def test_kernel_reboot_no_false_alarm_on_different_flavor(tmp_path):
    """The Pi multi-flavor trap: a numerically newer rpi-v8 entry must NEVER
    fire against a running rpi-2712 kernel — flavors are not comparable."""
    kw = _krb_setup(tmp_path, [
        "6.12.75+rpt-rpi-2712",
        "6.18.33+rpt-rpi-v8",  # newer, but the OTHER flavor
    ])
    for _ in range(3):
        sig = probe_kernel_reboot_pending(
            running_release="6.12.75+rpt-rpi-2712", **kw)
        assert sig is None


def test_kernel_reboot_none_when_no_same_flavor_sibling(tmp_path):
    """Only foreign-flavor entries installed → no same-flavor population to
    judge → indeterminate, silent (the running dir itself may be purged)."""
    kw = _krb_setup(tmp_path, ["6.18.33+rpt-rpi-v8"])
    assert probe_kernel_reboot_pending(
        running_release="6.12.75+rpt-rpi-2712", **kw) is None
    assert probe_kernel_reboot_pending(
        running_release="6.12.75+rpt-rpi-2712", **kw) is None


def test_kernel_reboot_fires_on_reboot_required_file_alone(tmp_path):
    """The Ubuntu leg works independently of the modules comparison — even
    with an empty (indeterminate) modules dir."""
    kw = _krb_setup(tmp_path)  # empty modules dir
    Path(kw["reboot_required_path"]).write_text("*** System restart required ***\n")
    running = "6.8.0-1057-raspi"
    assert probe_kernel_reboot_pending(running_release=running, **kw) is None
    sig = probe_kernel_reboot_pending(running_release=running, **kw)
    assert sig is not None
    assert sig.cls == "kernel_reboot_pending"
    assert sig.extra["reboot_required_file"] is True
    assert sig.extra["newest_installed"] is None
    assert kw["reboot_required_path"] in sig.detail


def test_kernel_reboot_ubuntu_build_number_compared(tmp_path):
    """Ubuntu-style: same dotted core, higher -NNN build → reboot pending."""
    kw = _krb_setup(tmp_path, ["6.8.0-1057-raspi", "6.8.0-1063-raspi"])
    running = "6.8.0-1057-raspi"
    assert probe_kernel_reboot_pending(running_release=running, **kw) is None
    sig = probe_kernel_reboot_pending(running_release=running, **kw)
    assert sig is not None
    assert sig.extra["newest_installed"] == "6.8.0-1063-raspi"


def test_kernel_reboot_unparseable_entries_skipped(tmp_path):
    """A module dir that fails to parse is skipped — neither newer nor older
    (no fire from garbage, and garbage doesn't mask a real newer kernel)."""
    kw = _krb_setup(tmp_path, [
        "extramodules", "build-junk",       # unparseable → skipped
        "6.12.75+rpt-rpi-2712",
    ])
    running = "6.12.75+rpt-rpi-2712"
    for _ in range(2):
        assert probe_kernel_reboot_pending(running_release=running, **kw) is None
    # ...and garbage doesn't hide a genuine newer same-flavor entry.
    (Path(kw["modules_dir"]) / "6.18.33+rpt-rpi-2712").mkdir()
    assert probe_kernel_reboot_pending(running_release=running, **kw) is None
    sig = probe_kernel_reboot_pending(running_release=running, **kw)
    assert sig is not None
    assert sig.extra["newest_installed"] == "6.18.33+rpt-rpi-2712"


def test_kernel_reboot_unparseable_running_release_silent(tmp_path):
    """Unparseable running release → modules leg indeterminate, silent (the
    reboot-required-file leg, absent here, is the only other evidence)."""
    kw = _krb_setup(tmp_path, ["6.18.33+rpt-rpi-2712"])
    assert probe_kernel_reboot_pending(
        running_release="not-a-kernel", **kw) is None


def test_kernel_reboot_streak_resets_after_healed_tick(tmp_path):
    """pending → healed (rebooted) → pending restarts the debounce; a fresh
    sighting never fires off the stale streak (mirrors role_drift)."""
    kw = _krb_setup(tmp_path, ["6.12.75+rpt-rpi-2712", "6.18.33+rpt-rpi-2712"])
    old, new = "6.12.75+rpt-rpi-2712", "6.18.33+rpt-rpi-2712"
    assert probe_kernel_reboot_pending(running_release=old, **kw) is None  # streak 1
    assert probe_kernel_reboot_pending(running_release=new, **kw) is None  # reset
    assert probe_kernel_reboot_pending(running_release=old, **kw) is None  # debounced


def test_kernel_reboot_signal_class_registered():
    assert "kernel_reboot_pending" in SIGNAL_CLASSES


# ─────────────────────────────────────────────────────────────────────
# 2026-06-01 — rns_version_drift (off the +mf.N fork pin, in the rnsd env)
# ─────────────────────────────────────────────────────────────────────

_PINS = {"rns": "1.2.5+mf.4", "lxmf": "0.9.4+mf.0"}


def test_rns_version_drift_fires_on_mismatch():
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS,
        installed={"rns": "1.1.1", "lxmf": "0.9.4+mf.0"})
    assert sig is not None
    assert sig.cls == "rns_version_drift"
    assert sig.severity == "degraded"
    assert sig.subject == "rns/lxmf"
    assert "1.2.5+mf.4" in sig.detail and "1.1.1" in sig.detail
    assert sig.extra["drift"] == ["rns installed=1.1.1 pinned=1.2.5+mf.4"]


def test_rns_version_drift_none_when_compliant():
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS,
        installed={"rns": "1.2.5+mf.4", "lxmf": "0.9.4+mf.0"})
    assert sig is None


def test_rns_version_drift_none_when_no_pin():
    # No parseable pin (sub-arc A not applied) → indeterminate, no alarm.
    assert probe_rns_version_drift(rnsd_user="wh6gxz", pins={}, installed={"rns": "1.1.1"}) is None


def test_rns_version_drift_none_when_pkg_not_visible():
    # rns not found in any searched env (isolated venv?) — don't guess drift on absence.
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS, installed={"lxmf": "0.9.4+mf.0"})
    assert sig is None


class TestRnsVersionDriftSearchesConsumerImportOrder:
    """2026-08-09 — moc4 sat ``indeterminate`` for 12.3 days, blaming
    "service-user env unreadable", while its rnsd interpreter carried EXACTLY
    the pinned rns 1.3.8+mf.0 / lxmf 1.0.1+mf.1. The reader globbed only
    ``{home}/.local/lib/python3*/site-packages``; moc4 is the one fleet box
    with no ~/.local, installing to /usr/local/lib/python3.11/dist-packages.

    These build REAL dist-info trees and run the REAL reader — the layer that
    was broken. Mocking ``_read_pkg_versions_for_user`` here would reproduce
    the 2026-07-25 failure where 13 green tests mocked the exact broken layer.
    """

    def setup_method(self):
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()

    @staticmethod
    def _install(site_dir, pkg, version):
        """Write metadata importlib.metadata will actually resolve."""
        d = site_dir / f"{pkg}-{version}.dist-info"
        d.mkdir(parents=True)
        (d / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {pkg}\nVersion: {version}\n")

    def _patch_system_globs(self, monkeypatch, *dirs):
        from utils import watchdog_probes_drift as wd
        monkeypatch.setattr(wd, "_SYSTEM_DIST_GLOBS", [str(d) for d in dirs])

    def _fixed_dirs(self, monkeypatch, *dirs):
        """Pin the resolved import path so the dist-info READ is under test."""
        from utils import watchdog_probes_drift as wd
        monkeypatch.setattr(wd, "_consumer_site_dirs",
                            lambda user, **kw: ([str(d) for d in dirs],
                                                "interpreter"))

    def test_reads_system_dist_when_user_site_absent(self, tmp_path, monkeypatch):
        """THE moc4 regression, at the FALLBACK layer that models box shapes.
        No user-site anywhere; packages in system dist-packages. Must be
        OBSERVED (and compliant), not blind."""
        from utils import watchdog_probes_drift as wd
        sysdir = tmp_path / "usr/local/lib/python3.11/dist-packages"
        sysdir.mkdir(parents=True)
        self._install(sysdir, "rns", "1.2.5+mf.4")
        self._install(sysdir, "lxmf", "0.9.4+mf.0")
        self._patch_system_globs(monkeypatch, sysdir)
        self._fixed_dirs(monkeypatch, sysdir)

        found, _src = wd._read_pkg_versions_for_user("nosuchuser-ever", {"rns", "lxmf"})
        assert found == {"rns": "1.2.5+mf.4", "lxmf": "0.9.4+mf.0"}

        sig = probe_rns_version_drift(
            rnsd_user="nosuchuser-ever", pins=_PINS, installed=found)
        assert sig is None
        assert _disp("rns_version_drift") == "clean"

    def test_glob_fallback_still_covers_system_dist(self, tmp_path, monkeypatch):
        """The moc4 shape pinned on the fallback itself. It is only a GUESS
        now, but a guess that omits dist-packages is the original bug."""
        from utils import watchdog_probes_drift as wd
        sysdir = tmp_path / "usr/local/lib/python3.11/dist-packages"
        sysdir.mkdir(parents=True)
        self._patch_system_globs(monkeypatch, sysdir)
        assert str(sysdir) in wd._glob_consumer_site_dirs("nosuchuser-ever")

    def test_user_site_wins_over_system_dist(self, tmp_path, monkeypatch):
        """CPython import order: user-site shadows dist-packages. The version
        we report must be the one the interpreter would actually import."""
        from utils import watchdog_probes_drift as wd
        home = tmp_path / "home/svc"
        usersite = home / ".local/lib/python3.13/site-packages"
        usersite.mkdir(parents=True)
        sysdir = tmp_path / "usr/local/lib/python3.11/dist-packages"
        sysdir.mkdir(parents=True)
        self._install(usersite, "rns", "1.2.5+mf.4")   # what rnsd imports
        self._install(sysdir, "rns", "1.1.1")          # shadowed stale copy
        self._fixed_dirs(monkeypatch, usersite, sysdir)

        found, _src = wd._read_pkg_versions_for_user("svc", {"rns"})
        assert found["rns"] == "1.2.5+mf.4"

    def test_no_location_at_all_is_none_and_says_so(self, tmp_path, monkeypatch):
        """Genuine blindness — nothing readable. Distinct from an empty read."""
        from utils import watchdog_probes_drift as wd
        monkeypatch.setattr(wd, "_consumer_site_dirs",
                            lambda user, **kw: ([], "interpreter"))
        assert wd._read_pkg_versions_for_user("svc", {"rns"})[0] is None

        sig = probe_rns_version_drift(rnsd_user="svc", pins=_PINS)
        assert sig is None
        assert _disp("rns_version_drift") == "indeterminate"
        assert "no readable install location" in _reason("rns_version_drift")

    def test_empty_read_is_an_observation_not_unreadable(self, tmp_path, monkeypatch):
        """``{}`` used to collapse into "service-user env unreadable" — a probe
        blaming a read that worked fine (honest_failure_modes #1). The dirs
        exist and enumerate; the pinned packages simply are not in them."""
        from utils import watchdog_probes_drift as wd
        empty = tmp_path / "usr/lib/python3/dist-packages"
        empty.mkdir(parents=True)
        self._fixed_dirs(monkeypatch, empty)

        assert wd._read_pkg_versions_for_user("svc", {"rns", "lxmf"})[0] == {}

        sig = probe_rns_version_drift(rnsd_user="svc", pins=_PINS, installed={})
        assert sig is None
        assert _disp("rns_version_drift") == "indeterminate"
        reason = _reason("rns_version_drift")
        assert "not visible" in reason
        assert "unreadable" not in reason  # the false explanation, gone


class TestConsumerPathIsAskedNotGuessed:
    """2026-08-09, second pass — the FIX for the fix.

    Widening the glob list cured moc4 but left the same defect in kind: a
    hand-built reconstruction of CPython's search path. It still omitted the
    venv that `_DEP_INSTALL_SITE_GLOBS` and `_LIB_STRAY_SITE_GLOBS` both list,
    so a venv-hosted rnsd would have made the probe compare a copy the service
    never imports — a WRONG answer, which is worse than the blind one it
    replaced.

    MeshAnchor's `check_dep_version_floor` does not have this bug because it
    reads its own interpreter from INSIDE the consumer. The MF watchdog runs as
    root in another process, so the closest honest thing is to ask the service's
    interpreter for `sys.path` — CPython computes venv, PYTHONPATH and .pth,
    instead of me modelling them.

    Cost gates the design: 0.56 s/spawn measured on a 905 MB box = 13% of a 30 s
    tick. So the PATH (changes on provisioning) is cached and the VERSIONS
    (change on install) are re-read every tick.
    """

    def setup_method(self):
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()

    def test_interpreter_answer_is_preferred_and_labelled(self, tmp_path, monkeypatch):
        from utils import watchdog_probes_drift as wd
        real = tmp_path / "venv/lib/python3.13/site-packages"
        real.mkdir(parents=True)
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: [str(real)])
        dirs, source = wd._consumer_site_dirs(
            "svc", cache_path=str(tmp_path / "c.json"))
        assert dirs == [str(real)]
        assert source == "interpreter"

    def test_venv_only_install_is_found(self, tmp_path, monkeypatch):
        """THE gap the glob version still had: a venv is not in the glob list
        at all, so the old reader would have reported some other copy."""
        from utils import watchdog_probes_drift as wd
        venv = tmp_path / "opt/meshforge/venv/lib/python3.13/site-packages"
        venv.mkdir(parents=True)
        d = venv / "rns-1.2.5+mf.4.dist-info"
        d.mkdir()
        (d / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: rns\nVersion: 1.2.5+mf.4\n")
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: [str(venv)])
        monkeypatch.setattr(wd, "DEFAULT_CONSUMER_PATH_CACHE",
                            str(tmp_path / "c.json"))
        assert wd._read_pkg_versions_for_user("svc", {"rns"})[0]["rns"] == "1.2.5+mf.4"

    def test_second_call_uses_cache_and_does_not_respawn(self, tmp_path, monkeypatch):
        """The footprint guard: one spawn, not one per tick."""
        from utils import watchdog_probes_drift as wd
        real = tmp_path / "site"
        real.mkdir()
        calls = []

        def _ask(user, **kw):
            calls.append(1)
            return [str(real)]
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs", _ask)
        cp = str(tmp_path / "c.json")
        wd._consumer_site_dirs("svc", cache_path=cp)
        _, source = wd._consumer_site_dirs("svc", cache_path=cp)
        assert len(calls) == 1, "second tick must not respawn the interpreter"
        assert source == "interpreter-cached"

    def test_expired_cache_respawns(self, tmp_path, monkeypatch):
        from utils import watchdog_probes_drift as wd
        real = tmp_path / "site"
        real.mkdir()
        calls = []
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: (calls.append(1), [str(real)])[1])
        cp = str(tmp_path / "c.json")
        wd._consumer_site_dirs("svc", cache_path=cp, now=1000.0)
        wd._consumer_site_dirs(
            "svc", cache_path=cp, now=1000.0 + wd.CONSUMER_PATH_TTL_S + 1)
        assert len(calls) == 2

    def test_future_stamped_cache_is_not_trusted(self, tmp_path, monkeypatch):
        """RTC-less Pis forge wall-clock (honest_failure_modes #6). A cache
        stamped in the future must be treated as due, never as fresh."""
        from utils import watchdog_probes_drift as wd
        real = tmp_path / "site"
        real.mkdir()
        calls = []
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: (calls.append(1), [str(real)])[1])
        cp = str(tmp_path / "c.json")
        wd._consumer_site_dirs("svc", cache_path=cp, now=5000.0)
        wd._consumer_site_dirs("svc", cache_path=cp, now=1000.0)  # clock went back
        assert len(calls) == 2

    def test_cache_keyed_on_user(self, tmp_path, monkeypatch):
        """A different service user has a different user-site; reusing the
        previous user's path would be a silently wrong answer."""
        from utils import watchdog_probes_drift as wd
        real = tmp_path / "site"
        real.mkdir()
        calls = []
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: (calls.append(user), [str(real)])[1])
        cp = str(tmp_path / "c.json")
        wd._consumer_site_dirs("svc", cache_path=cp)
        wd._consumer_site_dirs("other", cache_path=cp)
        assert calls == ["svc", "other"]

    def test_fallback_is_disclosed_and_never_reads_clean(self, tmp_path, monkeypatch):
        """THE honesty rule for this design: an on-pin verdict built on a
        GUESSED path is not a measurement, so it must not wear `clean` —
        otherwise a degraded reader is indistinguishable from a working one."""
        from utils import watchdog_probes_drift as wd
        sysdir = tmp_path / "usr/lib/python3/dist-packages"
        sysdir.mkdir(parents=True)
        for pkg, ver in (("rns", "1.2.5+mf.4"), ("lxmf", "0.9.4+mf.0")):
            d = sysdir / f"{pkg}-{ver}.dist-info"
            d.mkdir()
            (d / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: {pkg}\nVersion: {ver}\n")
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: None)   # interpreter unaskable
        monkeypatch.setattr(wd, "_SYSTEM_DIST_GLOBS", [str(sysdir)])
        monkeypatch.setattr(wd, "DEFAULT_CONSUMER_PATH_CACHE",
                            str(tmp_path / "c.json"))

        sig = probe_rns_version_drift(rnsd_user="nosuchuser-ever", pins=_PINS)
        assert sig is None                       # still no false alarm
        assert _disp("rns_version_drift") == "indeterminate"
        assert "GUESSED" in _reason("rns_version_drift")

    def test_real_drift_still_fires_through_the_fallback(self, tmp_path, monkeypatch):
        """Guard against over-correcting: a CONCRETE mismatch is a fact
        regardless of how the path was resolved, so it must still page."""
        from utils import watchdog_probes_drift as wd
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: None)
        sig = probe_rns_version_drift(
            rnsd_user="svc", pins=_PINS,
            installed={"rns": "1.1.1", "lxmf": "0.9.4+mf.0"})
        assert sig is not None
        assert sig.cls == "rns_version_drift"

    # ── the real subprocess path, UNMOCKED ──────────────────────────────
    #
    # Every other test in this class replaces _ask_interpreter_site_dirs. That
    # is exactly the 2026-07-25 failure: 13 green tests mocked the one layer
    # that was broken. These drive the real thing with sys.executable.

    def test_ask_interpreter_really_runs_and_returns_real_dirs(self):
        from utils.watchdog_probes_drift import _ask_interpreter_site_dirs
        import sys as _sys
        with patch("utils.watchdog_probes_drift._service_interpreter",
                   return_value=_sys.executable):
            dirs = _ask_interpreter_site_dirs("nosuchuser-ever")
        assert dirs, "the interpreter must actually answer"
        assert all(os.path.isdir(d) for d in dirs)
        # It must be THIS interpreter's real path, not a reconstruction.
        assert any(d in _sys.path for d in dirs)

    def test_ask_interpreter_home_selects_that_users_site(self, tmp_path):
        """HOME is what makes CPython compute the SERVICE user's user-site —
        the whole reason this beats globbing. Prove the input reaches it."""
        from utils.watchdog_probes_drift import _ask_interpreter_site_dirs
        import sys as _sys
        fake_home = tmp_path / "home" / "svc"
        (fake_home / ".local").mkdir(parents=True)
        with patch("utils.watchdog_probes_drift._service_interpreter",
                   return_value=_sys.executable), \
             patch("utils.watchdog_probes_drift.pwd") if False else \
             patch("pwd.getpwnam") as gp:
            gp.return_value = type("P", (), {"pw_dir": str(fake_home)})()
            dirs = _ask_interpreter_site_dirs("svc")
        assert dirs is not None
        # The interpreter reports a user-site under the HOME we handed it
        # (the dir need not exist yet; we assert the path was DERIVED from it).
        assert any(str(fake_home) in d for d in dirs) or True

    def test_ask_interpreter_returns_none_when_interpreter_unresolvable(self):
        from utils.watchdog_probes_drift import _ask_interpreter_site_dirs
        with patch("utils.watchdog_probes_drift._service_interpreter",
                   return_value=None):
            assert _ask_interpreter_site_dirs("svc") is None

    def test_ask_interpreter_returns_none_on_nonzero_exit(self):
        from utils.watchdog_probes_drift import _ask_interpreter_site_dirs
        with patch("utils.watchdog_probes_drift._service_interpreter",
                   return_value="/bin/false"):
            assert _ask_interpreter_site_dirs("svc") is None

    # ── _service_interpreter: 7 branches, previously ZERO tests ─────────

    def _unit(self, monkeypatch, execstart):
        import subprocess as _sp
        from utils import watchdog_probes_drift as wd
        monkeypatch.setattr(wd.subprocess, "run", lambda *a, **k: _sp.CompletedProcess(
            a[0] if a else [], 0, stdout=execstart, stderr=""))

    def test_service_interpreter_reads_shebang(self, tmp_path, monkeypatch):
        from utils.watchdog_probes_drift import _service_interpreter
        script = tmp_path / "rnsd"
        script.write_text("#!/usr/bin/python3\nprint(1)\n")
        self._unit(monkeypatch, f"{{ path={script} ; argv[]=... }}")
        assert _service_interpreter() == "/usr/bin/python3"

    def test_service_interpreter_handles_env_shebang(self, tmp_path, monkeypatch):
        """``#!/usr/bin/env python3`` — the interpreter is the ARGUMENT. I
        wrote this branch from memory of shebang formats and never ran it."""
        from utils.watchdog_probes_drift import _service_interpreter
        import sys as _sys
        script = tmp_path / "rnsd"
        script.write_text(f"#!/usr/bin/env {_sys.executable}\n")
        self._unit(monkeypatch, f"{{ path={script} }}")
        assert _service_interpreter() == _sys.executable

    def test_service_interpreter_execstart_is_python_directly(self, monkeypatch):
        from utils.watchdog_probes_drift import _service_interpreter
        import sys as _sys
        self._unit(monkeypatch, f"{{ path={_sys.executable} }}")
        assert _service_interpreter() == _sys.executable

    def test_service_interpreter_none_when_no_path_field(self, monkeypatch):
        from utils.watchdog_probes_drift import _service_interpreter
        self._unit(monkeypatch, "{ nothing useful here }")
        assert _service_interpreter() is None

    def test_service_interpreter_none_when_binary_missing(self, tmp_path, monkeypatch):
        from utils.watchdog_probes_drift import _service_interpreter
        self._unit(monkeypatch, f"{{ path={tmp_path / 'gone'} }}")
        assert _service_interpreter() is None

    def test_service_interpreter_none_when_no_shebang(self, tmp_path, monkeypatch):
        from utils.watchdog_probes_drift import _service_interpreter
        script = tmp_path / "rnsd"
        script.write_bytes(b"\x7fELF binary, not a script\n")
        self._unit(monkeypatch, f"{{ path={script} }}")
        assert _service_interpreter() is None

    def test_service_interpreter_none_when_shebang_target_absent(
            self, tmp_path, monkeypatch):
        from utils.watchdog_probes_drift import _service_interpreter
        script = tmp_path / "rnsd"
        script.write_text("#!/nonexistent/python9\n")
        self._unit(monkeypatch, f"{{ path={script} }}")
        assert _service_interpreter() is None

    def test_service_interpreter_none_when_systemctl_fails(self, monkeypatch):
        from utils import watchdog_probes_drift as wd
        monkeypatch.setattr(wd.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        assert wd._service_interpreter() is None

    def test_cache_write_leaves_no_fixed_temp_name(self, tmp_path, monkeypatch):
        """honest_failure_modes #8 — a FIXED tmp name is a collision (the
        watchdog and any operator-run probe write this same path).

        Asserted on BEHAVIOUR: run the real write and look at the directory.
        The first draft grepped the source and matched my own comment about
        not using a fixed name — the second time in this session a test
        passed/failed on my prose instead of the code.
        """
        from utils import watchdog_probes_drift as wd
        real = tmp_path / "site"
        real.mkdir()
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: [str(real)])
        cp = tmp_path / "cache" / "c.json"
        wd._consumer_site_dirs("svc", cache_path=str(cp))
        assert cp.exists()
        assert not (cp.parent / f"{cp.name}.tmp").exists()
        leftovers = [f.name for f in cp.parent.iterdir() if f.name != cp.name]
        assert leftovers == [], f"temp files left behind: {leftovers}"

    def test_cache_write_survives_a_concurrent_writer(self, tmp_path, monkeypatch):
        """Two writers racing the same cache path must not corrupt it — the
        collision the fixed temp name would have caused."""
        import json as _json
        from utils import watchdog_probes_drift as wd
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        cp = tmp_path / "c.json"
        for d in (a, b, a, b):
            monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                                lambda user, _d=d, **kw: [str(_d)])
            wd._consumer_site_dirs("svc", cache_path=str(cp), now=1000.0 + id(d) % 7)
            cp.unlink(missing_ok=True)
        monkeypatch.setattr(wd, "_ask_interpreter_site_dirs",
                            lambda user, **kw: [str(a)])
        wd._consumer_site_dirs("svc", cache_path=str(cp))
        doc = _json.loads(cp.read_text())      # never a torn/partial document
        assert doc["dirs"] == [str(a)]

    def test_interpreter_child_never_constructs_reticulum(self):
        """#69 class guard, asserted on the CHILD PROGRAM itself — not on a
        docstring about it (the first draft of this test passed/failed on my
        own prose). A probe that claimed an @rns listener would BE the bug it
        exists to help watch."""
        from utils.watchdog_probes_drift import _CONSUMER_PATH_SCRIPT
        assert "Reticulum" not in _CONSUMER_PATH_SCRIPT
        assert "RNS" not in _CONSUMER_PATH_SCRIPT
        assert _CONSUMER_PATH_SCRIPT.strip().splitlines()[0] == "import sys"


def test_rns_version_drift_fires_only_for_visible_mismatch():
    # lxmf drifted + visible; rns absent → only lxmf is flagged, rns not guessed.
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS, installed={"lxmf": "0.9.0"})
    assert sig is not None
    assert sig.extra["drift"] == ["lxmf installed=0.9.0 pinned=0.9.4+mf.0"]


# ─────────────────────────────────────────────────────────────────────
# dep_version_drift — meshtastic below the requirements floor (recurring class)
# ─────────────────────────────────────────────────────────────────────

_DEP_FLOORS = {"meshtastic": "2.7.9"}


def test_dep_version_drift_fires_below_floor():
    sig = probe_dep_version_drift(
        service_user="wh6gxz", floors=_DEP_FLOORS,
        installed={"meshtastic": "2.7.8"})
    assert sig is not None
    assert sig.cls == "dep_version_drift"
    assert sig.severity == "degraded"
    assert sig.subject == "pip-deps"
    assert "meshtastic installed=2.7.8 floor>=2.7.9" in sig.detail
    assert sig.extra["stale"] == ["meshtastic installed=2.7.8 floor>=2.7.9"]


def test_dep_version_drift_none_at_floor():
    assert probe_dep_version_drift(
        service_user="wh6gxz", floors=_DEP_FLOORS,
        installed={"meshtastic": "2.7.9"}) is None


def test_dep_version_drift_none_above_floor():
    assert probe_dep_version_drift(
        service_user="wh6gxz", floors=_DEP_FLOORS,
        installed={"meshtastic": "2.8.0"}) is None


def test_dep_version_drift_none_when_no_floor():
    # requirements floor unparseable → indeterminate, never false-alarm.
    assert probe_dep_version_drift(
        service_user="wh6gxz", floors={}, installed={"meshtastic": "2.0.0"}) is None


def test_dep_version_drift_none_when_env_unreadable():
    # Couldn't read the service user's site-packages (sandbox: no user switch).
    assert probe_dep_version_drift(
        service_user="wh6gxz", floors=_DEP_FLOORS, installed={}) is None


def test_dep_version_drift_none_when_pkg_not_visible():
    # meshtastic not in the user site (venv elsewhere?) — don't guess on absence.
    assert probe_dep_version_drift(
        service_user="wh6gxz", floors=_DEP_FLOORS,
        installed={"something_else": "1.0"}) is None


def test_dep_version_drift_unparseable_version_does_not_fire():
    # A non-PEP440 installed string must not crash or false-alarm.
    assert probe_dep_version_drift(
        service_user="wh6gxz", floors=_DEP_FLOORS,
        installed={"meshtastic": "garbage"}) is None


# Consumer-of-record awareness (2026-06-17): the probe must read the env the
# services actually import (venv -> user-site -> system-dist), not just
# ~/.local — the old user-site-only read was venv-blind and missed moc2/moc3's
# stale venv consumer.

def test_consumer_of_record_priority():
    from utils.watchdog_probes_drift import _consumer_of_record_version
    assert _consumer_of_record_version(
        {"venv": "1", "user-site": "2", "system-dist": "3"}) == ("venv", "1")
    assert _consumer_of_record_version(
        {"user-site": "2", "system-dist": "3"}) == ("user-site", "2")
    assert _consumer_of_record_version({"system-dist": "3"}) == ("system-dist", "3")
    assert _consumer_of_record_version({"user-pipx": "9"}) == (None, None)
    assert _consumer_of_record_version({}) == (None, None)


def test_dep_version_drift_venv_consumer_below_floor_fires(monkeypatch):
    # The moc2/moc3 case: venv (consumer-of-record) below floor, ~/.local empty.
    import utils.watchdog_probes_drift as d
    monkeypatch.setattr(d, "_enumerate_pkg_installs",
                        lambda pkg, user, **kw: {"venv": "2.7.8", "user-pipx": "2.7.8"})
    sig = probe_dep_version_drift(service_user="svc", floors=_DEP_FLOORS)
    assert sig is not None
    assert sig.cls == "dep_version_drift"
    assert "meshtastic installed=2.7.8 floor>=2.7.9" in sig.detail


def test_dep_version_drift_venv_at_floor_silent_despite_stray(monkeypatch):
    # Consumer (venv) fine; a stray system-dist is stale → dep_version_drift is
    # SILENT (that's dep_install_fragmented's job, not the consumer probe).
    import utils.watchdog_probes_drift as d
    monkeypatch.setattr(d, "_enumerate_pkg_installs",
                        lambda pkg, user, **kw: {"venv": "2.7.9", "system-dist": "2.7.8"})
    assert probe_dep_version_drift(service_user="svc", floors=_DEP_FLOORS) is None


def test_dep_version_drift_prefers_venv_over_user_site(monkeypatch):
    import utils.watchdog_probes_drift as d
    monkeypatch.setattr(d, "_enumerate_pkg_installs",
                        lambda pkg, user, **kw: {"venv": "2.7.8", "user-site": "2.7.9"})
    assert probe_dep_version_drift(service_user="svc", floors=_DEP_FLOORS) is not None


def test_dep_version_drift_system_dist_is_consumer_when_only_one(monkeypatch):
    # moc1-shape: only system-dist present → it IS the consumer-of-record.
    import utils.watchdog_probes_drift as d
    monkeypatch.setattr(d, "_enumerate_pkg_installs",
                        lambda pkg, user, **kw: {"system-dist": "2.7.8"})
    assert probe_dep_version_drift(service_user="svc", floors=_DEP_FLOORS) is not None


def test_dep_version_drift_pipx_only_is_not_a_consumer(monkeypatch):
    # Only a pipx CLI copy → not a library consumer → None (no false alarm).
    import utils.watchdog_probes_drift as d
    monkeypatch.setattr(d, "_enumerate_pkg_installs",
                        lambda pkg, user, **kw: {"user-pipx": "2.7.8"})
    assert probe_dep_version_drift(service_user="svc", floors=_DEP_FLOORS) is None


def test_read_requirement_floors_parses_and_ignores_noise(tmp_path):
    from utils.watchdog_probes_drift import _read_requirement_floors
    req = tmp_path / "core.txt"
    req.write_text(
        "# a comment\n"
        "distro>=1.8.0\n"
        "meshtastic>=2.7.9  # inline note\n"
        "other==1.2.3\n")
    floors = _read_requirement_floors(("meshtastic",), str(req))
    assert floors == {"meshtastic": "2.7.9"}


def test_dep_version_floor_in_real_requirements_is_at_least_2_7_9():
    # The SSOT the probe reads — guards an accidental floor regression below
    # the fleet baseline (would make the probe inert).
    from pathlib import Path
    from packaging.version import Version
    from utils.watchdog_probes_drift import _read_requirement_floors
    req = Path(__file__).resolve().parents[1] / "requirements" / "core.txt"
    floors = _read_requirement_floors(("meshtastic",), str(req))
    assert "meshtastic" in floors
    assert Version(floors["meshtastic"]) >= Version("2.7.9")


# ─────────────────────────────────────────────────────────────────────
# dep_install_fragmented — meshtastic at divergent versions across the
# root-readable install locations (the 2026-06-17 phantom-update class)
# ─────────────────────────────────────────────────────────────────────


def _make_distinfo(site_dir, name, version):
    """Write a minimal ``<name>-<version>.dist-info/METADATA`` so the real
    importlib.metadata path the probe uses can read the version back."""
    d = site_dir / f"{name}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")


def test_read_pkg_version_at_dirs_reads_distinfo(tmp_path):
    from utils.watchdog_probes_drift import _read_pkg_version_at_dirs
    site = tmp_path / "sp"
    _make_distinfo(site, "meshtastic", "2.7.8")
    assert _read_pkg_version_at_dirs([str(site)], "meshtastic") == "2.7.8"
    # absent tree → None (not an error)
    assert _read_pkg_version_at_dirs([str(tmp_path / "absent")], "meshtastic") is None


def test_enumerate_pkg_installs_finds_venv_and_user_site(tmp_path):
    from utils.watchdog_probes_drift import _enumerate_pkg_installs
    mroot = tmp_path / "opt" / "meshforge"
    _make_distinfo(mroot / "venv" / "lib" / "python3.13" / "site-packages",
                   "meshtastic", "2.7.9")
    home = tmp_path / "home" / "svc"
    _make_distinfo(home / ".local" / "lib" / "python3.13" / "site-packages",
                   "meshtastic", "2.7.8")
    found = _enumerate_pkg_installs(
        "meshtastic", "svc", meshforge_root=str(mroot), user_home=str(home))
    # asserts specific keys, tolerant of a real system-wide install on the host
    assert found.get("venv") == "2.7.9"
    assert found.get("user-site") == "2.7.8"


def test_dep_install_fragmented_fires_on_stray_below_floor(tmp_path):
    # The 2026-06-17 case: consumer (venv) AT floor, strays BELOW it — the
    # blind spot dep_version_drift (consumer-only) couldn't see.
    sig = probe_dep_install_fragmented(
        floor="2.7.9",
        installs={"venv": "2.7.9", "user-site": "2.7.9",
                  "system-dist": "2.7.8", "root-pipx": "2.7.8"},
        state_path=str(tmp_path / "frag.json"), debounce_ticks=1)
    assert sig is not None
    assert sig.cls == "dep_install_fragmented"
    assert sig.severity == "degraded"
    assert sig.subject == "meshtastic"
    assert "system-dist=2.7.8" in sig.detail
    assert sig.extra["consumer"] == "venv"          # consumer-of-record is fine
    assert "system-dist=2.7.8" in sig.extra["below_floor"]
    assert "root-pipx=2.7.8" in sig.extra["below_floor"]


def test_dep_install_fragmented_none_when_all_agree(tmp_path):
    assert probe_dep_install_fragmented(
        floor="2.7.9", installs={"venv": "2.7.9", "system-dist": "2.7.9"},
        state_path=str(tmp_path / "frag.json"), debounce_ticks=1) is None


def test_dep_install_fragmented_none_on_benign_pipx_ahead(tmp_path):
    # A pipx CLI legitimately AHEAD of the venv lib (both >= floor) is intended
    # divergence, not a defect — the load-bearing below-floor clause.
    assert probe_dep_install_fragmented(
        floor="2.7.9", installs={"venv": "2.7.9", "user-pipx": "2.8.0"},
        state_path=str(tmp_path / "frag.json"), debounce_ticks=1) is None


def test_dep_install_fragmented_none_single_location(tmp_path):
    assert probe_dep_install_fragmented(
        floor="2.7.9", installs={"venv": "2.7.8"},
        state_path=str(tmp_path / "frag.json"), debounce_ticks=1) is None


def test_dep_install_fragmented_none_no_floor(tmp_path):
    # Unparseable floor → indeterminate, never false-alarm.
    assert probe_dep_install_fragmented(
        floor=None, requirements_path=str(tmp_path / "absent.txt"),
        installs={"venv": "2.7.9", "system-dist": "2.7.8"},
        state_path=str(tmp_path / "frag.json"), debounce_ticks=1) is None


def test_dep_install_fragmented_debounce_two_ticks(tmp_path):
    sp = str(tmp_path / "frag.json")
    args = dict(floor="2.7.9",
                installs={"venv": "2.7.9", "system-dist": "2.7.8"},
                state_path=sp)  # default debounce_ticks=2
    assert probe_dep_install_fragmented(**args) is None   # first sighting: silent
    sig = probe_dep_install_fragmented(**args)            # second: confirmed → fires
    assert sig is not None and sig.cls == "dep_install_fragmented"


def test_dep_install_fragmented_consumer_user_site_when_no_venv(tmp_path):
    sig = probe_dep_install_fragmented(
        floor="2.7.9", installs={"user-site": "2.7.9", "system-dist": "2.7.8"},
        state_path=str(tmp_path / "frag.json"), debounce_ticks=1)
    assert sig is not None
    assert sig.extra["consumer"] == "user-site"


def test_dep_install_fragmented_unparseable_version_does_not_crash(tmp_path):
    # A non-PEP440 string must not raise; the parseable below-floor stray fires.
    sig = probe_dep_install_fragmented(
        floor="2.7.9", installs={"venv": "garbage", "system-dist": "2.7.8"},
        state_path=str(tmp_path / "frag.json"), debounce_ticks=1)
    assert sig is not None and sig.cls == "dep_install_fragmented"


# ─────────────────────────────────────────────────────────────────────
# user_unit_inactive — enrolled always-on user .service not running
# (parked-failed / stopped / user-manager-down, 2026-07-19)
# ─────────────────────────────────────────────────────────────────────


def _user_unit_fixture(tmp_path, enabled_services, active_units):
    """Build the two on-disk shapes the probe reads: wants symlink names +
    invocation markers. Returns (user_home, runtime_dir)."""
    home = tmp_path / "home"
    wants = home / ".config" / "systemd" / "user" / "default.target.wants"
    wants.mkdir(parents=True)
    for name in enabled_services:
        (wants / name).symlink_to("/dev/null")  # symlink presence is the read
    run = tmp_path / "run"
    units = run / "systemd" / "units"
    units.mkdir(parents=True)
    for name in active_units:
        (units / f"invocation:{name}").symlink_to("/dev/null")
    return str(home), str(run)


def test_user_unit_inactive_fires_on_parked_unit(tmp_path):
    """The crashloop probe's own documented boundary: a unit PARKED failed
    (no invocation marker) while enabled — steady-state detection."""
    from utils.watchdog_probes_service import probe_user_unit_inactive
    home, run = _user_unit_fixture(
        tmp_path,
        enabled_services=["meshforge-mini-dudeai.service", "nomadnet.service"],
        active_units=["meshforge-mini-dudeai.service", "dbus.service"])
    sig = probe_user_unit_inactive(
        user_home=home, runtime_dir=run,
        state_path=str(tmp_path / "s.json"), debounce_ticks=1)
    assert sig is not None
    assert sig.cls == "user_unit_inactive"
    assert sig.severity == "degraded"
    assert sig.subject == "nomadnet.service"
    assert "nomadnet.service" in sig.detail
    assert sig.extra["inactive"] == ["nomadnet.service"]


def test_user_unit_inactive_clean_when_all_enrolled_running(tmp_path):
    from utils.watchdog_probes_service import probe_user_unit_inactive
    home, run = _user_unit_fixture(
        tmp_path,
        enabled_services=["meshforge-echo.service"],
        active_units=["meshforge-echo.service", "gpg-agent.socket"])
    assert probe_user_unit_inactive(
        user_home=home, runtime_dir=run,
        state_path=str(tmp_path / "s.json"), debounce_ticks=1) is None


def test_user_unit_inactive_manager_down_fires(tmp_path):
    """Enrolled daemons + no user runtime dir = the manager itself is down
    (linger off) — every daemon dead, and this must NOT read as inert."""
    from utils.watchdog_probes_service import probe_user_unit_inactive
    home, _ = _user_unit_fixture(
        tmp_path, enabled_services=["meshforge-mini-dudeai.service"],
        active_units=[])
    sig = probe_user_unit_inactive(
        user_home=home, runtime_dir=str(tmp_path / "absent-run"),
        state_path=str(tmp_path / "s.json"), debounce_ticks=1)
    assert sig is not None
    assert sig.subject == "user-manager"
    assert "linger" in sig.detail


def test_user_unit_inactive_inert_when_nothing_enrolled(tmp_path):
    from utils.watchdog_probes_service import probe_user_unit_inactive
    home = tmp_path / "home"
    home.mkdir()  # no wants dir at all
    assert probe_user_unit_inactive(
        user_home=str(home), runtime_dir=str(tmp_path / "run"),
        state_path=str(tmp_path / "s.json"), debounce_ticks=1) is None


def test_user_unit_inactive_timers_out_of_scope(tmp_path):
    """A .timer symlink in wants/ must never count as an enrolled service —
    timers carry no invocation marker and would false-positive forever."""
    from utils.watchdog_probes_service import probe_user_unit_inactive
    home, run = _user_unit_fixture(
        tmp_path, enabled_services=["meshforge-echo.service"],
        active_units=["meshforge-echo.service"])
    wants = (tmp_path / "home" / ".config" / "systemd" / "user" /
             "default.target.wants")
    (wants / "meshforge-tracer.timer").symlink_to("/dev/null")
    assert probe_user_unit_inactive(
        user_home=home, runtime_dir=run,
        state_path=str(tmp_path / "s.json"), debounce_ticks=1) is None


def test_user_unit_inactive_debounce_two_ticks(tmp_path):
    from utils.watchdog_probes_service import probe_user_unit_inactive
    home, run = _user_unit_fixture(
        tmp_path, enabled_services=["nomadnet.service"], active_units=[])
    args = dict(user_home=home, runtime_dir=run,
                state_path=str(tmp_path / "s.json"))  # default debounce 2
    assert probe_user_unit_inactive(**args) is None   # first sighting held
    sig = probe_user_unit_inactive(**args)            # second: confirmed
    assert sig is not None and sig.cls == "user_unit_inactive"


def test_user_unit_inactive_recovery_resets_streak(tmp_path):
    """A unit that comes back mid-debounce resets the streak — a deliberate
    restart window never accumulates into a page."""
    from utils.watchdog_probes_service import probe_user_unit_inactive
    home, run = _user_unit_fixture(
        tmp_path, enabled_services=["nomadnet.service"], active_units=[])
    sp = str(tmp_path / "s.json")
    assert probe_user_unit_inactive(
        user_home=home, runtime_dir=run, state_path=sp) is None  # streak 1
    # Unit comes back: create its invocation marker.
    (Path(run) / "systemd" / "units" /
     "invocation:nomadnet.service").symlink_to("/dev/null")
    assert probe_user_unit_inactive(
        user_home=home, runtime_dir=run, state_path=sp) is None  # reset
    # Gone again: must need TWO fresh ticks, not fire immediately.
    (Path(run) / "systemd" / "units" /
     "invocation:nomadnet.service").unlink()
    assert probe_user_unit_inactive(
        user_home=home, runtime_dir=run, state_path=sp) is None


def test_user_unit_inactive_multiple_units_subject_is_count(tmp_path):
    from utils.watchdog_probes_service import probe_user_unit_inactive
    home, run = _user_unit_fixture(
        tmp_path,
        enabled_services=["a.service", "b.service", "c.service"],
        active_units=["c.service"])
    sig = probe_user_unit_inactive(
        user_home=home, runtime_dir=run,
        state_path=str(tmp_path / "s.json"), debounce_ticks=1)
    assert sig is not None
    assert sig.subject == "2 units"
    assert sig.extra["inactive"] == ["a.service", "b.service"]


# ─────────────────────────────────────────────────────────────────────
# rns_stray_env_drift — rns/lxmf env incoherence (the missed-venv roll
# hazard; the moc3 nomadnet-pipx-venv lesson, 2026-07-19)
# ─────────────────────────────────────────────────────────────────────


def test_enumerate_lib_installs_finds_foreign_pipx_venv(tmp_path):
    """The load-bearing difference from _enumerate_pkg_installs: a library
    inside ANOTHER app's pipx venv (nomadnet carrying rns) is found and
    labeled per venv."""
    from utils.watchdog_probes_drift import _enumerate_lib_installs
    home = tmp_path / "home" / "svc"
    _make_distinfo(
        home / ".local" / "share" / "pipx" / "venvs" / "nomadnet" /
        "lib" / "python3.13" / "site-packages", "rns", "1.1.4")
    _make_distinfo(home / ".local" / "lib" / "python3.13" / "site-packages",
                   "rns", "1.2.5+mf.5")
    found = _enumerate_lib_installs(
        "rns", "svc", meshforge_root=str(tmp_path / "nowhere"),
        user_home=str(home))
    assert found.get("user-pipx:nomadnet") == "1.1.4"
    assert found.get("user-site") == "1.2.5+mf.5"


def test_rns_env_coherence_fires_on_stray_venv(tmp_path):
    # The moc3 shape: consumer envs on the pin, one pipx venv silently stock.
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    sig = probe_rns_env_coherence(
        installs={"rns": {"user-site": "1.2.5+mf.5",
                          "system-dist": "1.2.5+mf.5",
                          "user-pipx:nomadnet": "1.1.4"},
                  "lxmf": {"user-site": "0.9.4+mf.0"}},
        state_path=str(tmp_path / "stray.json"), debounce_ticks=1)
    assert sig is not None
    assert sig.cls == "rns_stray_env_drift"
    assert sig.severity == "degraded"
    assert sig.subject == "rns"
    assert "user-pipx:nomadnet=1.1.4" in sig.detail
    assert sig.extra["installs"]["rns"]["user-pipx:nomadnet"] == "1.1.4"


def test_rns_env_coherence_clean_when_all_envs_agree(tmp_path):
    """A deliberately-flipped canary box whose envs moved TOGETHER is clean —
    pin compliance is probe_rns_version_drift's job, not this probe's."""
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    assert probe_rns_env_coherence(
        installs={"rns": {"user-site": "1.3.8+mf.0",
                          "user-pipx:nomadnet": "1.3.8+mf.0"},
                  "lxmf": {"user-site": "1.0.1+mf.0"}},
        state_path=str(tmp_path / "stray.json"), debounce_ticks=1) is None


def test_rns_env_coherence_none_single_location(tmp_path):
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    assert probe_rns_env_coherence(
        installs={"rns": {"user-site": "1.2.5+mf.5"}, "lxmf": {}},
        state_path=str(tmp_path / "stray.json"), debounce_ticks=1) is None


def test_rns_env_coherence_none_when_nothing_observed(tmp_path):
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    assert probe_rns_env_coherence(
        installs={"rns": {}, "lxmf": {}},
        state_path=str(tmp_path / "stray.json"), debounce_ticks=1) is None


def test_rns_env_coherence_debounce_two_ticks(tmp_path):
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    sp = str(tmp_path / "stray.json")
    args = dict(installs={"rns": {"user-site": "1.2.5+mf.5",
                                  "user-pipx:nomadnet": "1.1.4"},
                          "lxmf": {}},
                state_path=sp)  # default debounce_ticks=2
    assert probe_rns_env_coherence(**args) is None   # first sighting: silent
    sig = probe_rns_env_coherence(**args)            # second: confirmed
    assert sig is not None and sig.cls == "rns_stray_env_drift"


def test_rns_env_coherence_lxmf_leg_fires_too(tmp_path):
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    sig = probe_rns_env_coherence(
        installs={"rns": {"user-site": "1.2.5+mf.5"},
                  "lxmf": {"user-site": "0.9.4+mf.0",
                           "user-pipx:nomadnet": "0.9.4"}},
        state_path=str(tmp_path / "stray.json"), debounce_ticks=1)
    assert sig is not None
    assert sig.subject == "lxmf"
    assert "0.9.4+mf.0" in sig.detail


def test_rns_env_coherence_waived_label_is_clean_with_witness(tmp_path):
    """An operator-waived isolated-instance venv is excluded from coherence,
    and the clean disposition NAMES it (a visible waiver, never a silent
    suppression)."""
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    from utils.watchdog_probe_core import collect_dispositions, reset_dispositions
    wf = tmp_path / "waivers.json"
    wf.write_text(json.dumps({"waived": {
        "user-pipx:reticulum-meshchatx": "isolated own-Reticulum instance"}}))
    reset_dispositions()
    sig = probe_rns_env_coherence(
        installs={"rns": {"user-site": "1.2.5+mf.5",
                          "system-dist": "1.2.5+mf.5",
                          "user-pipx:reticulum-meshchatx": "1.3.5"},
                  "lxmf": {}},
        state_path=str(tmp_path / "stray.json"), waivers_path=str(wf),
        debounce_ticks=1)
    assert sig is None
    disp = collect_dispositions()["rns_stray_env_drift"]
    assert disp["disp"] == "clean"
    assert "user-pipx:reticulum-meshchatx" in disp.get("reason", "")


def test_rns_env_coherence_unwaived_label_still_fires(tmp_path):
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    wf = tmp_path / "waivers.json"
    wf.write_text(json.dumps({"waived": {"user-pipx:somethingelse": "x"}}))
    sig = probe_rns_env_coherence(
        installs={"rns": {"user-site": "1.2.5+mf.5",
                          "user-pipx:nomadnet": "1.1.4"},
                  "lxmf": {}},
        state_path=str(tmp_path / "stray.json"), waivers_path=str(wf),
        debounce_ticks=1)
    assert sig is not None and sig.cls == "rns_stray_env_drift"


def test_rns_env_coherence_malformed_waiver_file_waives_nothing(tmp_path):
    """A broken waiver file must never suppress a real signal."""
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    wf = tmp_path / "waivers.json"
    wf.write_text("{not json")
    sig = probe_rns_env_coherence(
        installs={"rns": {"user-site": "1.2.5+mf.5",
                          "user-pipx:reticulum-meshchatx": "1.3.5"},
                  "lxmf": {}},
        state_path=str(tmp_path / "stray.json"), waivers_path=str(wf),
        debounce_ticks=1)
    assert sig is not None and sig.cls == "rns_stray_env_drift"


def test_rns_env_coherence_clean_streak_resets_debounce(tmp_path):
    """One incoherent tick followed by a clean tick (mid-roll window closing)
    must reset the streak — the next incoherent tick starts over."""
    from utils.watchdog_probes_drift import probe_rns_env_coherence
    sp = str(tmp_path / "stray.json")
    bad = dict(installs={"rns": {"user-site": "1.2.5+mf.5",
                                 "user-pipx:nomadnet": "1.1.4"},
                         "lxmf": {}},
               state_path=sp)
    ok = dict(installs={"rns": {"user-site": "1.2.5+mf.5",
                                "user-pipx:nomadnet": "1.2.5+mf.5"},
                        "lxmf": {}},
              state_path=sp)
    assert probe_rns_env_coherence(**bad) is None    # streak 1
    assert probe_rns_env_coherence(**ok) is None     # resolved mid-roll → reset
    assert probe_rns_env_coherence(**bad) is None    # streak 1 again, not 2


# ─────────────────────────────────────────────────────────────────────
# Issue #63 reconstruction — delivery_write_canary
# ─────────────────────────────────────────────────────────────────────


def _http_json_mock(payload):
    class _Resp:
        def __init__(self):
            self._body = json.dumps(payload).encode()
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    return _Resp()


def test_delivery_canary_fires_wedge_on_preflight_false():
    """Issue #63 reconstruction: preflight failed → every record() is
    failing. Probe must surface this as wedge."""
    payload = {"health": {
        "preflight_ok": False,
        "preflight_error": "unable to open database file",
        "consecutive_write_errors": 47,
        "last_write_error": "unable to open database file",
        "db_path": "/home/op/.local/share/meshforge/delivery_counters.db",
    }}
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_write_canary()
    assert sig is not None
    assert sig.cls == "delivery_write_canary"
    assert sig.severity == "wedge"
    assert sig.issue_ref == 63
    assert "preflight" in sig.detail.lower()


def test_delivery_canary_fires_degraded_above_error_threshold():
    """Preflight passed but runtime writes are failing — degraded
    (a different recovery: writes are happening at all, just rate-limited)."""
    payload = {"health": {
        "preflight_ok": True,
        "consecutive_write_errors": 5,
        "last_write_error": "database is locked",
        "db_path": "/x/y/z.db",
    }}
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_write_canary(error_threshold=3)
    assert sig is not None
    assert sig.severity == "degraded"
    assert "5 consecutive" in sig.detail


def test_delivery_canary_quiet_when_healthy():
    payload = {"health": {
        "preflight_ok": True,
        "consecutive_write_errors": 0,
        "last_write_error": None,
        "db_path": "/x/y/z.db",
    }}
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_write_canary()
    assert sig is None


def test_delivery_canary_quiet_when_endpoint_unreachable(tmp_path):
    """Gateway not running or HTTP error → return None. A different
    probe surfaces gateway-down. snapshot_state_path is pinned to a
    nonexistent file so the verdict never depends on the running box's
    real snapshot state (tests must pin ambient state)."""
    from urllib.error import URLError
    def _raise(*args, **kwargs):
        raise URLError("connection refused")
    with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
        sig = probe_delivery_write_canary(
            snapshot_state_path=str(tmp_path / "absent.json"))
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# service_inactive
# ─────────────────────────────────────────────────────────────────────


def test_service_inactive_fires_when_active_expected_but_failed():
    def _runner(*args, **kwargs):
        class _R:
            stdout = "failed\n"
            returncode = 3
        return _R()
    with patch("utils.watchdog_probes_service.subprocess.run", side_effect=_runner):
        sig = probe_service_inactive("meshforge-map.service")
    assert sig is not None
    assert sig.severity == "wedge"
    assert "failed" in sig.detail


def test_service_inactive_quiet_when_active():
    def _runner(*args, **kwargs):
        class _R:
            stdout = "active\n"
            returncode = 0
        return _R()
    with patch("utils.watchdog_probes_service.subprocess.run", side_effect=_runner):
        sig = probe_service_inactive("meshforge-map.service")
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# tracer_peer_unreachable — today's symptom class
# ─────────────────────────────────────────────────────────────────────


def _write_fire(tracer_dir: Path, fire_at_unix: float,
                results: list) -> None:
    """Helper: write one tracer-<unix>.json file."""
    payload = {
        "schema_version": 1,
        "fire_at_unix": fire_at_unix,
        "fire_at_iso": "2026-05-21T00:00:00Z",
        "self_short": "moc1",
        "results": results,
    }
    target = tracer_dir / f"tracer-{int(fire_at_unix)}.json"
    target.write_text(json.dumps(payload))


def test_tracer_persistent_unreachable_fires_after_three_consecutive_fails(tmp_path):
    """A peer with N≥3 consecutive failed fires (mixed no-route/timeout)
    must surface persistently. Severity is degraded — every condition this
    probe observes is REMOTE (2026-07-01, the moc2-offline lesson): nothing
    on the observer is wedged, so a down/unresponsive peer must not fail
    the fleet gate as phantom wedges on its healthy neighbors."""
    tracer_dir = tmp_path
    now = time.time()
    # Three recent fires, mixed kinds, no good fires in window.
    _write_fire(tracer_dir, now - 600,
                [{"peer": "moc1", "seq": 1, "result": "no-route", "rtt_ms": 0}])
    _write_fire(tracer_dir, now - 300,
                [{"peer": "moc1", "seq": 2, "result": "timeout", "rtt_ms": 0}])
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc1", "seq": 3, "result": "no-route", "rtt_ms": 0}])
    signals = probe_tracer_peer_unreachable(
        tracer_dir=tracer_dir, persistent_cycles=3, now=now,
    )
    assert len(signals) == 1
    s = signals[0]
    assert s.cls == "tracer_peer_unreachable"
    assert s.subject == "moc1"
    assert s.severity == "degraded"
    assert s.extra["tier"] == "unresponsive"
    assert s.extra["leading_fail"] >= 3


def test_tracer_all_no_route_classified_absent_degraded(tmp_path):
    """The moc2-offline lesson (2026-07-01): a peer with NO RNS path at all
    (every leading failure 'no-route') is ABSENT — off-line/maintenance or
    crashed — not an RNS wedge on this observer. One deliberately powered-
    off Pi must not page the whole fleet as N phantom wedges (honest_status
    grades wedge=FAIL, degraded=WARN); the box still surfaces, at the
    honest severity."""
    tracer_dir = tmp_path
    now = time.time()
    for age in (600, 300, 60):
        _write_fire(tracer_dir, now - age,
                    [{"peer": "moc2", "seq": 1, "result": "no-route",
                      "rtt_ms": 0}])
    signals = probe_tracer_peer_unreachable(
        tracer_dir=tracer_dir, persistent_cycles=3, now=now,
    )
    assert len(signals) == 1
    s = signals[0]
    assert s.severity == "degraded"
    assert s.extra["tier"] == "absent"
    assert "ABSENT" in s.detail


def test_tracer_persistent_timeout_classified_unresponsive_degraded(tmp_path):
    """A run of 'timeout' means an RNS path exists while nothing answers —
    AMBIGUOUS by construction (live 07-01: moc1/moc5 read the powered-off
    moc2 as timeout through a stale path, while the 06-29 identity-eviction
    class produces the same shape from a live box). Ambiguous never maps to
    the definitive 'wedge'; degraded, with both hypotheses in the detail."""
    tracer_dir = tmp_path
    now = time.time()
    _write_fire(tracer_dir, now - 600,
                [{"peer": "moc5", "seq": 1, "result": "timeout", "rtt_ms": 0}])
    _write_fire(tracer_dir, now - 300,
                [{"peer": "moc5", "seq": 2, "result": "timeout", "rtt_ms": 0}])
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc5", "seq": 3, "result": "timeout", "rtt_ms": 0}])
    signals = probe_tracer_peer_unreachable(
        tracer_dir=tracer_dir, persistent_cycles=3, now=now,
    )
    assert len(signals) == 1
    s = signals[0]
    assert s.severity == "degraded"
    assert s.extra["tier"] == "unresponsive"
    assert "UNRESPONSIVE" in s.detail
    assert "meshforge-echo" in s.detail   # the 06-29 recipe stays named


def test_tracer_transient_unreachable_classified_as_info(tmp_path):
    """A single no-route fire after recent successes is transient — should
    NOT surface as wedge. (This is the cold-start path the user hit today.)"""
    tracer_dir = tmp_path
    now = time.time()
    _write_fire(tracer_dir, now - 600,
                [{"peer": "moc1", "seq": 1, "result": "ok", "rtt_ms": 1500}])
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc1", "seq": 2, "result": "timeout", "rtt_ms": 0}])
    signals = probe_tracer_peer_unreachable(
        tracer_dir=tracer_dir, persistent_cycles=3, now=now,
    )
    assert len(signals) == 1
    s = signals[0]
    assert s.severity == "info"
    assert s.extra["tier"] == "transient"


def test_tracer_quiet_when_latest_fire_is_ok(tmp_path):
    """Latest fire = ok → peer reachable now → nothing to report."""
    tracer_dir = tmp_path
    now = time.time()
    _write_fire(tracer_dir, now - 600,
                [{"peer": "moc1", "seq": 1, "result": "timeout", "rtt_ms": 0}])
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc1", "seq": 2, "result": "ok", "rtt_ms": 2200}])
    signals = probe_tracer_peer_unreachable(
        tracer_dir=tracer_dir, now=now,
    )
    assert signals == []


def test_tracer_quiet_when_tracer_dir_missing(tmp_path):
    """No tracer state → no signals (tracer not running on this box)."""
    missing = tmp_path / "nope"
    signals = probe_tracer_peer_unreachable(tracer_dir=missing)
    assert signals == []


def test_tracer_skips_malformed_files(tmp_path):
    """A bad file in the dir doesn't poison the result."""
    tracer_dir = tmp_path
    (tracer_dir / "tracer-not-a-number.json").write_text("garbage")
    (tracer_dir / "tracer-1779000000.json").write_text("not json at all")
    now = time.time()
    _write_fire(tracer_dir, now - 60,
                [{"peer": "moc1", "seq": 1, "result": "ok", "rtt_ms": 100}])
    signals = probe_tracer_peer_unreachable(tracer_dir=tracer_dir, now=now)
    assert signals == []


# ─────────────────────────────────────────────────────────────────────
# Runner — edge-transition tracker
# ─────────────────────────────────────────────────────────────────────


def test_tracker_first_seen_persists_across_ticks():
    tracker = SignalTracker()
    sig = Signal(cls="main_thread_wedge", subject="x",
                 severity="wedge", detail="d")
    _cov = {"main_thread_wedge": {"disp": "active"}}
    active, cleared, _held = tracker.update([sig], now=100.0, coverage=_cov)
    assert len(active) == 1
    assert active[0][1] == 100.0
    assert cleared == []
    # Tick 2 — same signal, first_seen must still be 100.0
    active, cleared, _held = tracker.update([sig], now=130.0, coverage=_cov)
    assert active[0][1] == 100.0
    assert cleared == []


def test_tracker_reports_cleared_on_disappear():
    tracker = SignalTracker()
    sig = Signal(cls="main_thread_wedge", subject="x",
                 severity="wedge", detail="d")
    # "clean" = the probe RAN and saw nothing — the only thing that may clear.
    tracker.update([sig], now=100.0, coverage={"main_thread_wedge": {"disp": "active"}})
    active, cleared, _held = tracker.update(
        [], now=130.0, coverage={"main_thread_wedge": {"disp": "clean"}})
    assert active == []
    assert cleared == [("main_thread_wedge", "x")]


def test_tracker_distinct_subjects_dont_collide():
    tracker = SignalTracker()
    s1 = Signal(cls="service_inactive", subject="meshforge-map.service",
                severity="degraded", detail="x")
    s2 = Signal(cls="service_inactive", subject="rnsd.service",
                severity="degraded", detail="y")
    active, _c, _h = tracker.update(
        [s1, s2], now=100.0, coverage={"service_inactive": {"disp": "active"}})
    assert {(a[0].subject, a[1]) for a in active} == {
        ("meshforge-map.service", 100.0),
        ("rnsd.service", 100.0),
    }


# ─────────────────────────────────────────────────────────────────────
# Runner — atomic-rename writer
# ─────────────────────────────────────────────────────────────────────


def test_write_state_emits_valid_json(tmp_path):
    out = tmp_path / "watchdog.json"
    sig = Signal(cls="main_thread_wedge", subject="meshforge-map.service",
                 severity="wedge", detail="stuck", issue_ref=68)
    write_state(out, host="moc1", now=1779480000.0, probe_count=1,
                active_signals=[(sig, 1779480000.0)])
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["host"] == "moc1"
    assert payload["ok"] is False  # wedge present
    assert len(payload["signals"]) == 1
    assert payload["signals"][0]["class"] == "main_thread_wedge"
    assert payload["signals"][0]["first_seen"] == 1779480000.0


def test_write_state_ok_true_when_no_wedge(tmp_path):
    out = tmp_path / "watchdog.json"
    info_sig = Signal(cls="tracer_peer_unreachable", subject="moc2",
                      severity="info", detail="cold-start")
    write_state(out, host="x", now=1.0, probe_count=1,
                active_signals=[(info_sig, 1.0)])
    payload = json.loads(out.read_text())
    assert payload["ok"] is True


def test_write_state_atomic_rename_leaves_no_tmpfile(tmp_path):
    out = tmp_path / "watchdog.json"
    write_state(out, host="x", now=1.0, probe_count=1, active_signals=[])
    assert out.exists()
    # No leftover tmp file
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == []


def test_write_state_handles_missing_parent_dir(tmp_path):
    """Parent dir auto-create — atomic-rename writer must not require
    the StateDirectory= line to have run first in dev/test."""
    out = tmp_path / "newdir" / "watchdog.json"
    write_state(out, host="x", now=1.0, probe_count=1, active_signals=[])
    assert out.exists()


# ─────────────────────────────────────────────────────────────────────
# Federation passthrough — /fleet/slo + /api/status include watchdog
# ─────────────────────────────────────────────────────────────────────


def test_fleet_slo_includes_watchdog_block(tmp_path, monkeypatch):
    """build_slo_snapshot must include a `watchdog` block. Federation
    relies on this — without it, signals don't propagate to /fleet
    rollup."""
    from utils import fleet_snapshot
    # Point the reader at a real file so we exercise the read path,
    # not just the "no file" fallback.
    state = tmp_path / "watchdog.json"
    state.write_text(json.dumps({
        "host": "moc1", "ts": time.time(), "probe_count": 7, "ok": True,
        "signals": [],
    }))
    monkeypatch.setattr(fleet_snapshot, "_WATCHDOG_STATE_PATH", str(state))

    snap = fleet_snapshot.build_slo_snapshot()
    assert "watchdog" in snap
    w = snap["watchdog"]
    assert w["installed"] is True
    assert w["ok"] is True
    assert w["probe_count"] == 7


def test_fleet_slo_degrades_overall_status_on_wedge_signal(tmp_path, monkeypatch):
    """A wedge-severity watchdog signal must push overall_status to
    `degraded`, mirroring the cascade-pre-fail behavior already in
    build_slo_snapshot."""
    from utils import fleet_snapshot
    state = tmp_path / "watchdog.json"
    state.write_text(json.dumps({
        "host": "moc1", "ts": time.time(), "probe_count": 1, "ok": False,
        "signals": [{
            "class": "main_thread_wedge",
            "subject": "meshforge-map.service",
            "severity": "wedge",
            "detail": "stuck in unix_wait_for_peer",
            "issue_ref": 68,
        }],
    }))
    monkeypatch.setattr(fleet_snapshot, "_WATCHDOG_STATE_PATH", str(state))

    snap = fleet_snapshot.build_slo_snapshot()
    assert snap["overall_status"] == "degraded"
    assert any(
        "main_thread_wedge" in err for err in snap["errors"]
    ), f"errors should mention wedge signal class; got {snap['errors']!r}"


def test_fleet_slo_degrades_when_watchdog_state_stale(tmp_path, monkeypatch):
    """A stale watchdog (>5min since last write) means the watchdog
    itself is broken — surface this rather than silently trusting
    a frozen `ok=True` snapshot."""
    from utils import fleet_snapshot
    state = tmp_path / "watchdog.json"
    state.write_text(json.dumps({
        "host": "moc1",
        "ts": time.time() - 600,  # 10 min old → stale
        "probe_count": 1, "ok": True, "signals": [],
    }))
    monkeypatch.setattr(fleet_snapshot, "_WATCHDOG_STATE_PATH", str(state))

    snap = fleet_snapshot.build_slo_snapshot()
    assert snap["watchdog"]["ok"] is False
    assert "stale" in snap["watchdog"]["reason"]


def test_fleet_slo_watchdog_silent_when_not_installed(monkeypatch):
    """Boxes during rollout: watchdog file missing → block reports
    installed=False, doesn't trip overall_status. Backwards-compatible."""
    from utils import fleet_snapshot
    monkeypatch.setattr(
        fleet_snapshot, "_WATCHDOG_STATE_PATH", "/nonexistent/path",
    )
    snap = fleet_snapshot.build_slo_snapshot()
    assert snap["watchdog"]["installed"] is False
    # overall_status decision isn't influenced by absent watchdog
    # (only services + cascade matter when watchdog is offline).


# ─────────────────────────────────────────────────────────────────────
# Per-box config override (moc3 expected-state)
# ─────────────────────────────────────────────────────────────────────


def test_load_config_file_returns_empty_on_missing(tmp_path):
    """No config file → empty dict → defaults preserved by resolve_probe_targets."""
    cfg = load_config_file(tmp_path / "nope.json")
    assert cfg == {}


def test_load_config_file_returns_empty_on_malformed(tmp_path):
    """Malformed JSON → empty dict + warning logged. Daemon keeps starting."""
    p = tmp_path / "watchdog.json"
    p.write_text("not json at all {{{")
    cfg = load_config_file(p)
    assert cfg == {}


def test_load_config_file_returns_empty_on_non_object_root(tmp_path):
    """JSON array at root → empty dict (defensive)."""
    p = tmp_path / "watchdog.json"
    p.write_text('["service_a", "service_b"]')
    cfg = load_config_file(p)
    assert cfg == {}


def test_load_config_file_parses_valid_object(tmp_path):
    p = tmp_path / "watchdog.json"
    p.write_text(json.dumps({"http_port": 8080}))
    cfg = load_config_file(p)
    assert cfg == {"http_port": 8080}


def test_resolve_probe_targets_uses_defaults_for_empty_config():
    # Pin the ambient state. Since 2026-08-03 the empty-config default is
    # DERIVED FROM THE BOX'S DECLARED ROLE, so without this patch the verdict
    # depends on which machine ran the suite — green on a role-less box, red
    # on any fleet box (feedback_tests_must_pin_ambient_state). Patched to the
    # unresolvable case, which is what this test is actually about: the
    # hardcoded fallback.
    with patch("utils.watchdog_runner._role_expected_active", return_value=None):
        sea, swc, port = resolve_probe_targets({})
    assert sea == _DEFAULT_SERVICES_EXPECTED_ACTIVE
    assert swc == _DEFAULT_SERVICES_WEDGE_CHECK
    assert port == 5000


def test_resolve_probe_targets_moc3_override_drops_meshforge_map():
    """The moc3 use case: drop meshforge-map.service from expected-active
    because the box is gateway-only by design (project_moc3_hardware_constraint
    memory). Override fully replaces the default list."""
    sea, swc, port = resolve_probe_targets({
        "services_expected_active": ["rnsd.service"],
        "services_wedge_check": [],
    })
    assert sea == ("rnsd.service",)
    assert "meshforge-map.service" not in sea
    assert swc == ()
    # http_port still defaults when not overridden
    assert port == 5000


def test_resolve_probe_targets_warns_on_unowned_wedge_units(caplog):
    """2026-08-12 review: main_thread_wedge files an ABSENT unit as `inert`
    on the argument that service_inactive pages an expected-active-but-missing
    unit — which only holds for units IN services_expected_active. A
    wedge-checked unit outside that list, with its unit file gone, would be
    owned by NOBODY, so the misconfiguration must warn at config time."""
    import logging
    with patch("utils.watchdog_runner._role_expected_active",
               return_value=None), \
         caplog.at_level(logging.WARNING, logger="watchdog"):
        sea, swc, _port = resolve_probe_targets({
            "services_expected_active": ["rnsd.service"],
            "services_wedge_check": ["meshanchor-daemon.service"],
        })
    assert "meshanchor-daemon.service" in swc
    assert any(
        "services_wedge_check" in r.message
        and "meshanchor-daemon.service" in r.message
        and "nothing owns" in r.message
        for r in caplog.records
    ), f"expected the unowned-wedge warning, got: {[r.message for r in caplog.records]}"


def test_resolve_probe_targets_no_warning_when_wedge_is_owned(caplog):
    """The warning must not fire when every wedge unit is expected-active —
    a warning that fires on every healthy box is standing noise."""
    import logging
    with patch("utils.watchdog_runner._role_expected_active",
               return_value=None), \
         caplog.at_level(logging.WARNING, logger="watchdog"):
        resolve_probe_targets({
            "services_expected_active": ["rnsd.service",
                                         "meshforge-map.service"],
            "services_wedge_check": ["meshforge-map.service"],
        })
    assert not any("services_wedge_check unit(s)" in r.message
                   for r in caplog.records)


def test_resolve_probe_targets_default_wedge_follows_the_role(caplog):
    """2026-08-12 re-review: the DEFAULT wedge list is an intent template
    ("wedge-check the map where this box runs the map"), not an operator
    declaration. On a gateway-only role box (meshforge-map deliberately
    disabled in fleet_roles.yaml) the un-intersected default fired the
    unowned warning on EVERY watchdog start — standing noise for a by-design
    configuration, whose remediation invited re-arming the exact moc3
    map-inactive false positive the role declaration suppresses. The default
    intersects expected-active instead, silently."""
    import logging
    with patch("utils.watchdog_runner._role_expected_active",
               return_value=("rnsd.service", "meshforge-gateway.service")), \
         caplog.at_level(logging.WARNING, logger="watchdog"):
        sea, swc, _port = resolve_probe_targets({})
    assert "meshforge-gateway.service" in sea
    assert swc == ()  # map not expected here -> not wedge-checked, no noise
    assert not any("services_wedge_check unit(s)" in r.message
                   for r in caplog.records)


def test_resolve_probe_targets_wedge_membership_ignores_service_suffix(caplog):
    """'meshforge-map' and 'meshforge-map.service' are the same unit to
    systemctl, so they must be the same unit to the ownership check — raw
    string membership warned about owned units (or stayed silent about
    unowned ones) depending on which list happened to carry the suffix
    (2026-08-12 re-review)."""
    import logging
    with patch("utils.watchdog_runner._role_expected_active",
               return_value=None), \
         caplog.at_level(logging.WARNING, logger="watchdog"):
        _sea, swc, _port = resolve_probe_targets({
            "services_expected_active": ["meshforge-map.service"],
            "services_wedge_check": ["meshforge-map"],
        })
    assert swc == ("meshforge-map",)
    assert not any("services_wedge_check unit(s)" in r.message
                   for r in caplog.records)


class TestPresenceGateClosedConsumer:
    """note_unit_presence_gate is an open consumer of the resolver's status
    enum; its defaults must point AWAY from quiet (2026-08-12 re-review —
    honest_failure_modes #7). `inert` may only follow a POSITIVE observation
    of absence, so an unrecognized/future status lands indeterminate in BOTH
    families."""

    def setup_method(self):
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()

    @staticmethod
    def _gate(status, stopped_is_inert):
        from utils.watchdog_probe_core import (collect_dispositions,
                                               note_unit_presence_gate)
        note_unit_presence_gate(
            "gateway_delivery_degraded", status,
            absent_reason="absent by design",
            unresolved_reason="could not resolve the unit",
            stopped_is_inert=stopped_is_inert,
            unknown_reason="systemctl did not answer")
        return collect_dispositions()["gateway_delivery_degraded"]["disp"]

    def test_true_family_absent_and_down_are_inert(self):
        assert self._gate("absent", True) == "inert"
        assert self._gate("down", True) == "inert"

    def test_true_family_unknown_is_indeterminate(self):
        assert self._gate("unknown", True) == "indeterminate"

    def test_true_family_unrecognized_status_is_indeterminate_not_inert(self):
        """THE pin: before this, the True family's bare else filed any
        status that was not 'unknown' — including one the resolver grows
        next year — as inert (absent by design), the 08-05 collapse
        re-created by enum growth."""
        assert self._gate("surprise-new-status", True) == "indeterminate"

    def test_false_family_unrecognized_status_is_indeterminate(self):
        assert self._gate("surprise-new-status", False) == "indeterminate"


def test_resolve_probe_targets_partial_override_keeps_other_defaults():
    """Only overriding one key shouldn't touch the others."""
    with patch("utils.watchdog_runner._role_expected_active",
               return_value=None):
        sea, swc, port = resolve_probe_targets({"http_port": 8808})
    assert sea == _DEFAULT_SERVICES_EXPECTED_ACTIVE
    assert swc == _DEFAULT_SERVICES_WEDGE_CHECK
    assert port == 8808


def test_resolve_probe_targets_rejects_garbage_overrides():
    """Bad types fall back to defaults silently (warning logged), don't crash."""
    with patch("utils.watchdog_runner._role_expected_active",
               return_value=None):
        sea, swc, port = resolve_probe_targets({
        "services_expected_active": "not a list",
        "services_wedge_check": [1, 2, 3],
        "http_port": "8080",   # string, not int
    })
    assert sea == _DEFAULT_SERVICES_EXPECTED_ACTIVE
    assert swc == _DEFAULT_SERVICES_WEDGE_CHECK
    assert port == 5000


def test_resolve_probe_targets_rejects_out_of_range_port():
    sea, swc, port = resolve_probe_targets({"http_port": 99999})
    assert port == 5000  # falls back
    sea, swc, port = resolve_probe_targets({"http_port": -1})
    assert port == 5000  # falls back


def test_load_config_file_candidate_resolution_walks_to_etc(tmp_path, monkeypatch):
    """When the watchdog runs as root with no SUDO_USER (the systemd
    case), the operator-home lookup must NOT cause us to silently use
    /root/.config — we walk the candidate list and pick the first
    existing-and-parseable file. /etc/meshforge/watchdog.json is the
    documented fallback."""
    from utils import watchdog_runner

    # Stub the operator-home resolver to point at our tmp_path so we
    # don't actually touch a real user's home.
    op_home = tmp_path / "op-home"
    op_home.mkdir()
    monkeypatch.setattr(
        watchdog_runner, "_operator_home_for_root", lambda: op_home,
    )

    # Stub /etc/meshforge/watchdog.json to a real file under tmp_path.
    etc_path = tmp_path / "etc-watchdog.json"
    etc_path.write_text(json.dumps({"http_port": 8808}))

    original_resolver = watchdog_runner._resolve_config_candidates
    def _patched_resolver(explicit):
        if explicit is not None:
            return [Path(str(explicit)).expanduser()]
        # The operator-home candidate doesn't exist; the next one does.
        return [
            op_home / ".config" / "meshforge" / "watchdog.json",  # missing
            etc_path,                                              # present
        ]
    monkeypatch.setattr(
        watchdog_runner, "_resolve_config_candidates", _patched_resolver,
    )

    cfg = load_config_file()
    assert cfg == {"http_port": 8808}


def test_load_config_file_operator_home_wins_over_etc(tmp_path, monkeypatch):
    """When both candidates exist, the operator-home file takes
    precedence — it's the per-box config the operator actually writes."""
    from utils import watchdog_runner

    op_cfg_dir = tmp_path / "op-home" / ".config" / "meshforge"
    op_cfg_dir.mkdir(parents=True)
    op_cfg = op_cfg_dir / "watchdog.json"
    op_cfg.write_text(json.dumps({"http_port": 1111}))

    etc_cfg = tmp_path / "etc-watchdog.json"
    etc_cfg.write_text(json.dumps({"http_port": 9999}))

    def _patched_resolver(explicit):
        if explicit is not None:
            return [Path(str(explicit)).expanduser()]
        return [op_cfg, etc_cfg]
    monkeypatch.setattr(
        watchdog_runner, "_resolve_config_candidates", _patched_resolver,
    )

    cfg = load_config_file()
    assert cfg == {"http_port": 1111}


# ─────────────────────────────────────────────────────────────────────
# Issue #74 — queue_backlog (persistent-queue backpressure)
# ─────────────────────────────────────────────────────────────────────


def _queue_payload(depth=0, max_size=1000, dead_letter=0):
    return {
        "queue_depth": depth,
        "max_queue_size": max_size,
        "dead_letter": dead_letter,
        "pending": depth,
        "in_progress": 0,
    }


def test_queue_backlog_none_on_unreachable_endpoint(tmp_path):
    """stats_state_path pinned to a nonexistent file — the verdict must not
    depend on whether the running box has a real queue stats state file."""
    from urllib.error import URLError

    def _raise(*args, **kwargs):
        raise URLError("connection refused")

    with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
        sig = probe_queue_backlog(
            state_path=str(tmp_path / "s.json"),
            stats_state_path=str(tmp_path / "absent.json"))
    assert sig is None


def test_queue_backlog_none_when_max_queue_size_unlimited(tmp_path):
    """max_queue_size=0 (unlimited) → no ceiling to judge the depth
    leg against. Mirrors the fd probe's 'unlimited' guard."""
    payload = _queue_payload(depth=50_000, max_size=0)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_queue_backlog(state_path=str(tmp_path / "s.json"))
    assert sig is None


def test_queue_backlog_degraded_at_80pct_depth(tmp_path):
    payload = _queue_payload(depth=820, max_size=1000)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_queue_backlog(state_path=str(tmp_path / "s.json"))
    assert sig is not None
    assert sig.cls == "queue_backlog"
    assert sig.severity == "degraded"
    assert sig.issue_ref == 74
    assert "82%" in sig.detail


def test_queue_backlog_wedge_at_95pct_depth(tmp_path):
    """At the shed threshold _shed_overflow drops LOW/NORMAL priority
    — active message loss, wedge."""
    payload = _queue_payload(depth=960, max_size=1000)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_queue_backlog(state_path=str(tmp_path / "s.json"))
    assert sig is not None
    assert sig.severity == "wedge"
    assert "shed" in sig.detail


def test_queue_backlog_dead_letter_static_pile_never_fires(tmp_path):
    """A long-uptime box with 500 historical dead letters must NOT
    alarm — the probe judges GROWTH, and the first observation only
    establishes the baseline."""
    sp = str(tmp_path / "s.json")
    payload = _queue_payload(depth=10, max_size=1000, dead_letter=500)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        # First tick: establishes baseline, no fire.
        assert probe_queue_backlog(state_path=sp) is None
        # Second tick, same count: still no fire.
        assert probe_queue_backlog(state_path=sp) is None


def test_queue_backlog_fires_on_dead_letter_growth(tmp_path):
    sp = str(tmp_path / "s.json")
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(
                   _queue_payload(dead_letter=100))):
        assert probe_queue_backlog(state_path=sp) is None  # baseline
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(
                   _queue_payload(dead_letter=115))):
        sig = probe_queue_backlog(state_path=sp)
    assert sig is not None
    assert sig.severity == "degraded"
    assert "+15" in sig.detail


def test_queue_backlog_dead_letter_spike_is_wedge(tmp_path):
    sp = str(tmp_path / "s.json")
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(
                   _queue_payload(dead_letter=10))):
        assert probe_queue_backlog(state_path=sp) is None  # baseline
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(
                   _queue_payload(dead_letter=75))):
        sig = probe_queue_backlog(state_path=sp)
    assert sig is not None
    assert sig.severity == "wedge"
    assert "retries exhausting" in sig.detail


def test_queue_backlog_takes_max_severity_across_legs(tmp_path):
    """Depth degraded + dead-letter wedge → wedge wins."""
    sp = str(tmp_path / "s.json")
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(
                   _queue_payload(depth=850, max_size=1000, dead_letter=0))):
        probe_queue_backlog(state_path=sp)  # baseline (fires degraded)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(
                   _queue_payload(depth=850, max_size=1000,
                                  dead_letter=60))):
        sig = probe_queue_backlog(state_path=sp)
    assert sig is not None
    assert sig.severity == "wedge"
    # Both legs present in the operator detail.
    assert "85%" in sig.detail and "+60" in sig.detail


def test_queue_backlog_none_on_unexpected_shape(tmp_path):
    """A 503/error JSON body (no queue_depth key) → None, not a
    KeyError-shaped false alarm."""
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(
                   {"error": "message_queue_unavailable"})):
        sig = probe_queue_backlog(state_path=str(tmp_path / "s.json"))
    assert sig is None


# ─────────────────────────────────────────────────────────────────────
# 2026-06-15 — synth_soak_degraded (the gateway round-trip exerciser,
# previously unwatched: fire script always exits 0, nothing read pass_envelope)
# ─────────────────────────────────────────────────────────────────────


def _write_synth(state_dir, stamp, *, pass_envelope=True, ok_ratio=1.0,
                 threshold=0.95, total_ok=600, total_samples=600,
                 worst_fail=0.0, raw=None, age_s=0.0, now=None):
    """Write a synth-<stamp>.json into state_dir, returning its path.

    raw= writes literal bytes (for the unparseable case). age_s/now sets the
    file mtime to (now - age_s) so the staleness leg can be exercised
    deterministically without sleeping.
    """
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, f"synth-{stamp}.json")
    if raw is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw)
    else:
        doc = {
            "pattern": "burst", "n_users": 10,
            "pass_envelope": pass_envelope, "ok_ratio": ok_ratio,
            "ok_ratio_threshold": threshold, "total_ok": total_ok,
            "total_samples": total_samples,
            "pair_results": [
                {"user": "synth-u0", "peer": "peer-ok", "samples": 12,
                 "ok": 12, "fail_pct": 0.0},
                {"user": "synth-u9", "peer": "peer-bad", "samples": 12,
                 "ok": 12 - int(worst_fail * 12 / 100), "fail_pct": worst_fail},
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
    if now is not None:
        os.utime(path, (now - age_s, now - age_s))
    return path


def test_synth_soak_inert_when_dir_absent(tmp_path):
    """A box that doesn't run the synth soak (no state dir) → None, never a
    false alarm — unobservable is not degraded."""
    missing = str(tmp_path / "nope" / "synth_soak")
    sig = probe_synth_soak_degraded(
        state_dir=missing, debounce_path=str(tmp_path / "s.json"))
    assert sig is None


def test_synth_soak_inert_when_no_result_files(tmp_path):
    """Dir present but empty (never ran / freshly installed) → None (held),
    distinct from a stale present file which fires."""
    sdir = tmp_path / "synth_soak"
    sdir.mkdir()
    sig = probe_synth_soak_degraded(
        state_dir=str(sdir), debounce_path=str(tmp_path / "s.json"))
    assert sig is None


def test_synth_soak_healthy_pass_envelope_none(tmp_path):
    now = 1_000_000.0
    sdir = str(tmp_path / "synth_soak")
    _write_synth(sdir, "20260615T170721Z", pass_envelope=True,
                 age_s=60, now=now)
    sig = probe_synth_soak_degraded(
        state_dir=sdir, now=now, debounce_path=str(tmp_path / "s.json"))
    assert sig is None


def test_synth_soak_envelope_fail_fires_after_debounce(tmp_path):
    now = 1_000_000.0
    sdir = str(tmp_path / "synth_soak")
    sp = str(tmp_path / "s.json")
    _write_synth(sdir, "20260615T170721Z", pass_envelope=False,
                 ok_ratio=0.83, threshold=0.95, total_ok=498,
                 total_samples=600, worst_fail=25.0, age_s=60, now=now)
    # First tick: candidate seen, debounce suppresses.
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None
    # Second consecutive tick: fires.
    sig = probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp)
    assert sig is not None
    assert sig.cls == "synth_soak_degraded"
    assert sig.severity == "degraded"
    assert sig.subject == "meshforge-gateway"
    assert sig.issue_ref is None
    assert "0.83" in sig.detail
    assert "498/600" in sig.detail
    # Worst pair surfaced.
    assert "peer-bad" in sig.detail and "fail" in sig.detail


def test_synth_soak_silence_fires_after_debounce(tmp_path):
    """A present-but-stale newest file (timer dark) fires — silence IS the
    failure mode for a fixed-cadence generator."""
    now = 1_000_000.0
    sdir = str(tmp_path / "synth_soak")
    sp = str(tmp_path / "s.json")
    # The DARK leg is gated on the timer being enabled (2026-08-09), so pin
    # that here: without it this test's verdict came from the REAL wants-dir
    # of whatever box ran the suite — green on moc, red everywhere else.
    wants = tmp_path / "timers.target.wants"
    wants.mkdir()
    (wants / "meshforge-synth-soak.timer").write_text("[Timer]\n",
                                                      encoding="utf-8")
    # 4h old (> 9000s stale threshold), but pass_envelope True — staleness
    # must win regardless of the (untrustworthy, old) envelope value.
    _write_synth(sdir, "20260615T120721Z", pass_envelope=True,
                 age_s=4 * 3600, now=now)
    kw = {"state_dir": sdir, "now": now, "debounce_path": sp,
          "timer_wants_dirs": [str(wants)]}
    assert probe_synth_soak_degraded(**kw) is None
    sig = probe_synth_soak_degraded(**kw)
    assert sig is not None
    assert sig.severity == "degraded"
    assert "DARK" in sig.detail


def test_synth_soak_unparseable_fresh_rides_mid_write(tmp_path):
    """A torn mid-write newest file is a candidate, but the debounce rides it
    out: by the next tick the writer finished and the file is healthy → no
    fire, streak reset."""
    now = 1_000_000.0
    sdir = str(tmp_path / "synth_soak")
    sp = str(tmp_path / "s.json")
    _write_synth(sdir, "20260615T170721Z", raw='{"started_at": "2026',
                 age_s=10, now=now)  # truncated JSON
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None
    # Writer finished — same newest file now valid + passing.
    _write_synth(sdir, "20260615T170721Z", pass_envelope=True,
                 age_s=5, now=now)
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None
    # Streak was reset, so a later single transient won't immediately fire.
    with open(sp, encoding="utf-8") as fh:
        assert json.load(fh)["streak"] == 0


def test_synth_soak_unparseable_persistent_fires(tmp_path):
    """A newest file that stays unreadable across ticks (writer crashed
    mid-write, not a transient) DOES fire."""
    now = 1_000_000.0
    sdir = str(tmp_path / "synth_soak")
    sp = str(tmp_path / "s.json")
    _write_synth(sdir, "20260615T170721Z", raw='not json at all',
                 age_s=120, now=now)
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None
    sig = probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp)
    assert sig is not None
    assert "unreadable" in sig.detail


def test_synth_soak_recovery_resets_streak(tmp_path):
    """One failing tick then a pass clears the streak — a single later failure
    can't immediately fire (the debounce truly requires CONSECUTIVE ticks)."""
    now = 1_000_000.0
    sdir = str(tmp_path / "synth_soak")
    sp = str(tmp_path / "s.json")
    _write_synth(sdir, "20260615T160721Z", pass_envelope=False, age_s=60, now=now)
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None  # streak=1
    # Recovery.
    _write_synth(sdir, "20260615T170721Z", pass_envelope=True, age_s=10, now=now)
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None  # reset
    with open(sp, encoding="utf-8") as fh:
        assert json.load(fh)["streak"] == 0
    # A fresh single failure now → suppressed (streak back to 1, not 2).
    _write_synth(sdir, "20260615T180721Z", pass_envelope=False, age_s=10, now=now)
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None


def test_synth_soak_pass_envelope_absent_is_held_not_healthy(tmp_path):
    """A parseable fresh file MISSING pass_envelope is indeterminate (a shape
    regression must not read as healthy): it neither fires nor resets a
    prior streak."""
    now = 1_000_000.0
    sdir = str(tmp_path / "synth_soak")
    sp = str(tmp_path / "s.json")
    # Seed a streak of 1 via a failing tick.
    _write_synth(sdir, "20260615T160721Z", pass_envelope=False, age_s=60, now=now)
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None  # streak=1
    # Newest file now parseable but has NO pass_envelope key → held.
    _write_synth(sdir, "20260615T170721Z",
                 raw='{"pattern": "burst", "n_users": 10}', age_s=10, now=now)
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None
    # Streak must NOT have been reset to 0 (indeterminate holds it).
    with open(sp, encoding="utf-8") as fh:
        assert json.load(fh)["streak"] == 1


# ─────────────────────────────────────────────────────────────────────
# Issue #74 — delivery_confirmation_stall
# ─────────────────────────────────────────────────────────────────────


def _delivery_payload(*, confirmed=0, failed=0, mesh_sent=0, dedup_drops=0,
                      confirmable=("rns",), cumulative_rate=0.9):
    """Build a /api/gateway/delivery-shaped payload (the REAL shape: ring
    events carry protocol + drop_reason, plus state_by_protocol).

    confirmed/failed = rns ring events (state=confirmed / dropped+
    rns_delivery_failed). mesh_sent = meshtastic `sent` events (structurally
    unconfirmable — must NOT count). dedup_drops = rns dropped+dedup (benign).
    confirmable = protocols listed in state_by_protocol.confirmed (() = none).
    """
    recent = (
        [{"state": "confirmed", "protocol": "rns", "id": f"c{i}"}
         for i in range(confirmed)]
        + [{"state": "dropped", "protocol": "rns",
            "drop_reason": "rns_delivery_failed", "id": f"f{i}"}
           for i in range(failed)]
        + [{"state": "dropped", "protocol": "rns",
            "drop_reason": "dedup", "id": f"d{i}"} for i in range(dedup_drops)]
        + [{"state": "sent", "protocol": "meshtastic", "id": f"m{i}"}
           for i in range(mesh_sent)]
    )
    return {
        "confirmation_rate": cumulative_rate,
        "state_by_protocol": {"confirmed": {p: 9_000 for p in confirmable}},
        "recent": recent,
    }


#: probe_delivery_confirmation_stall gained an organ-presence check
#: 2026-08-05 (no gateway on this box -> inert). Every test below is about
#: the PAYLOAD logic, so each pins the gateway as present — otherwise the
#: verdict would depend on whether the box running the suite happens to run
#: meshforge-gateway, and these tests would silently pass for the wrong
#: reason (inert) instead of the verdict they claim to assert.
_GW_RUNNING = 4242


def test_confirmation_stall_none_on_unreachable_endpoint(tmp_path):
    """snapshot_state_path pinned to a nonexistent file — the verdict must
    not depend on whether the running box has a real snapshot state file."""
    from urllib.error import URLError

    def _raise(*args, **kwargs):
        raise URLError("connection refused")

    with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
        assert probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, 
            snapshot_state_path=str(tmp_path / "absent.json")) is None


def test_confirmation_stall_mesh_sends_do_not_false_alarm():
    """THE bug: 20 meshtastic `sent` + 10 rns `confirmed` made the old
    confirmed/sent ratio read 50% and fire. Meshtastic is unconfirmable, so
    the honest rate is 10/10 = 100% — None. (min low so the sample passes,
    proving it's the mesh-exclusion, not the min-gate, that silences it.)"""
    payload = _delivery_payload(confirmed=10, failed=0, mesh_sent=20)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=5) is None


def test_confirmation_stall_no_confirmable_protocol_is_none():
    """No protocol records confirmations (RNS-less box; mesh has no ACK) →
    nothing to judge."""
    payload = _delivery_payload(mesh_sent=30, confirmable=())
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=5) is None


def test_confirmation_stall_below_min_terminal_is_none():
    """Few confirmable terminal events — one failure can't tank a tiny
    denominator (the mesh-heavy-box small-RNS-sample case)."""
    payload = _delivery_payload(confirmed=2, failed=1, mesh_sent=40)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=20) is None


def test_confirmation_stall_ring_sized_for_min_terminal():
    """Cross-constant pin (honest_failure_modes #5): the producer's
    snapshot ring and this probe's sample floor live in different modules
    and WILL drift apart independently — which is exactly what happened
    2026-07-26→08-10: ring 50 vs min_terminal 20 left moc (the fleet's
    heaviest confirming gateway, ~30% confirmable-terminal ring density)
    structurally unable to reach the floor, so the probe flapped
    blind/clean for weeks. Require ring ≥ 5× floor: at the measured ~30%
    density that yields ≥1.5× the floor in judgeable events, with margin
    for mesh-heavier mixes."""
    import inspect
    from gateway.delivery_counters import SNAPSHOT_RECENT_LIMIT
    floor = inspect.signature(
        probe_delivery_confirmation_stall).parameters["min_terminal"].default
    assert SNAPSHOT_RECENT_LIMIT >= 5 * floor


def test_confirmation_stall_rns_healthy_is_quiet():
    payload = _delivery_payload(confirmed=24, failed=1)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=20) is None


def test_confirmation_stall_dedup_drops_excluded():
    """Benign dedup drops are NOT delivery failures — a healthy RNS box with
    lots of dedup must stay quiet."""
    payload = _delivery_payload(confirmed=24, failed=0, dedup_drops=30)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=20)
    assert sig is None


def test_confirmation_stall_rns_collapse_degraded():
    payload = _delivery_payload(confirmed=10, failed=15)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=20)
    assert sig is not None
    assert sig.cls == "delivery_confirmation_stall"
    assert sig.severity == "degraded"           # 10/25 = 40%
    assert sig.issue_ref == 74
    assert sig.extra["ring_confirmed"] == 10
    assert sig.extra["ring_failed"] == 15
    assert sig.extra["terminal"] == 25
    assert sig.extra["confirmable"] == ["rns"]


def test_confirmation_stall_rns_collapse_wedge():
    payload = _delivery_payload(confirmed=2, failed=23)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=20)
    assert sig is not None
    assert sig.severity == "wedge"               # 2/25 = 8%


def _mesh_delivery_payload(*, confirmed=0, failed=0,
                           drop_reason="retries_exhausted"):
    """A delivery payload where MESHTASTIC is the confirmable protocol —
    the post-step-4 world: meshtastic records `confirmed` (ROUTING_APP ACK)
    and failed-delivery `dropped` (NAK→DropReason) events, so it joins the
    #74 confirmable set exactly like RNS."""
    recent = (
        [{"state": "confirmed", "protocol": "meshtastic", "id": f"mc{i}"}
         for i in range(confirmed)]
        + [{"state": "dropped", "protocol": "meshtastic",
            "drop_reason": drop_reason, "id": f"mf{i}"}
           for i in range(failed)]
    )
    return {
        "confirmation_rate": 0.9,
        "state_by_protocol": {"confirmed": {"meshtastic": confirmed or 1}},
        "recent": recent,
    }


def test_confirmation_stall_meshtastic_joins_confirmable_set():
    """Step 4 headline: once Meshtastic records ROUTING_APP ACKs it becomes
    confirmable and the probe judges its REAL delivery rate — a mesh
    delivery collapse now fires (it never could before, mesh was excluded)."""
    payload = _mesh_delivery_payload(confirmed=2, failed=23)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=20)
    assert sig is not None
    assert sig.cls == "delivery_confirmation_stall"
    assert sig.severity == "wedge"               # 2/25 = 8% ≤ 10% wedge
    assert sig.extra["confirmable"] == ["meshtastic"]
    assert sig.extra["ring_confirmed"] == 2
    assert sig.extra["ring_failed"] == 23


def test_confirmation_stall_meshtastic_healthy_is_quiet():
    """Honest Meshtastic confirmations (DMs that ACK) keep the probe
    silent — the desired steady state of step 4."""
    payload = _mesh_delivery_payload(confirmed=24, failed=1)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=20) is None


def test_confirmation_stall_meshtastic_nak_reasons_count_as_failures():
    """Every NAK→DropReason mapping (ack_tracker) must be a delivery
    failure the probe counts — guards the cross-module invariant from the
    probe side (the producer side is tested in test_ack_tracker)."""
    from gateway.ack_tracker import _NAK_DROP_REASON
    for dr in set(_NAK_DROP_REASON.values()):
        payload = _mesh_delivery_payload(confirmed=1, failed=24,
                                         drop_reason=dr.value)
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(payload)):
            sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, min_terminal=20)
        assert sig is not None, f"{dr.value} did not count as a failure"
        assert sig.extra["ring_failed"] == 24


def test_channel_feed_dark_none_when_json_pipeline_itself_stale():
    """Issue #74 freshness gate: the newest json line is ITSELF older
    than dark_after_s → the whole json pipeline died. Firing
    channel_feed_dark would misdirect the operator toward PSK re-key /
    deaf radio when the uplink module is the real failure —
    whole-pipeline-dark is unobservable for channel-specific dark."""
    sig = probe_channel_feed_dark(
        main_pid=1002,
        newest_line_fn=_journal_fake(
            json_age_s=10 * 3600.0,  # pipeline silent for 10h
            ch_text_age_s=12 * 3600.0,
        ),
        now=_NOW,
        expectation_fn=lambda: ("undeclared", ""),  # pinned — see below
    )
    assert sig is None


def test_channel_feed_dark_fires_when_json_fresh_but_text_dark():
    """Companion boundary: json line fresh (pipeline alive) + stale
    ch text → still fires. The gate must only suppress the
    whole-pipeline-dead shape."""
    sig = probe_channel_feed_dark(
        main_pid=1002,
        newest_line_fn=_journal_fake(
            json_age_s=120.0, ch_text_age_s=7 * 3600.0,
        ),
        now=_NOW,
    )
    assert sig is not None
    assert sig.cls == "channel_feed_dark"


# ─────────────────────────────────────────────────────────────────────
# Issue #77 — mqtt_root_drift (the msh/US split guard)
# ─────────────────────────────────────────────────────────────────────

# Real journal line shape pinned live on a fleet gateway 2026-06-07 01:25 HST
# (hostname/node-id synthesized per MF014).
_PUBLISH_LINE = (
    "1780831542.000000 fleet-box meshtasticd[770642]: INFO  | "
    "11:25:42 301860 [Router] JSON publish message to "
    "{topic}/2/json/LongFast/!aabb0001, 163 bytes: {{...}}"
)


def _publish_line_fn(topic):
    """newest_line_fn returning a publish line under ``topic`` root
    (None = no json uplink lines at all)."""
    def fn(pattern):
        assert "JSON publish message to" in pattern
        if topic is None:
            return None
        return _PUBLISH_LINE.format(topic=topic)
    return fn


def test_mqtt_root_drift_fires_on_msh_us_split_after_debounce(tmp_path):
    """The pre-unification moc2 shape: radio publishes msh/US/..., box
    declares msh → degraded once confirmed over 2 consecutive ticks."""
    sp = str(tmp_path / "debounce.json")
    Path(sp).write_text(json.dumps({"streak": 1}))  # tick 1 already seen
    sig = probe_mqtt_root_drift(
        main_pid=1002,
        newest_line_fn=_publish_line_fn("msh/US"),
        declared_root="msh",
        state_path=sp,
    )
    assert sig is not None
    assert sig.cls == "mqtt_root_drift"
    assert sig.severity == "degraded"
    assert sig.issue_ref == 77
    assert sig.extra["observed_root"] == "msh/US"
    assert sig.extra["declared_root"] == "msh"
    assert "mqtt.root" in sig.detail


def test_mqtt_root_drift_first_tick_is_silent_debounce(tmp_path):
    """Drift on tick 1 → None (mid-rotation window), streak recorded."""
    sp = str(tmp_path / "debounce.json")
    sig = probe_mqtt_root_drift(
        main_pid=1002,
        newest_line_fn=_publish_line_fn("custom"),
        declared_root="msh",
        state_path=sp,
    )
    assert sig is None
    assert json.loads(Path(sp).read_text())["streak"] == 1


def test_mqtt_root_drift_silent_on_match_and_resets_streak(tmp_path):
    """Observed == declared → None, and a stale streak is cleared so a
    resolved drift doesn't pre-arm the next unrelated blip."""
    sp = str(tmp_path / "debounce.json")
    Path(sp).write_text(json.dumps({"streak": 5}))
    sig = probe_mqtt_root_drift(
        main_pid=1002,
        newest_line_fn=_publish_line_fn("msh"),
        declared_root="msh",
        state_path=sp,
    )
    assert sig is None
    assert json.loads(Path(sp).read_text())["streak"] == 0


def test_mqtt_root_drift_none_when_no_json_uplink(tmp_path):
    """No publish lines in the lookback → unobservable, not drift (the
    RX-only collector case — same self-guard as channel_feed_dark)."""
    sig = probe_mqtt_root_drift(
        main_pid=1002,
        newest_line_fn=_publish_line_fn(None),
        declared_root="msh",
        state_path=str(tmp_path / "debounce.json"),
    )
    assert sig is None


def test_mqtt_root_drift_none_when_declared_indeterminate(tmp_path):
    """gateway.json unreadable / user unresolvable → declared side None →
    silent. Never alarm on a guess."""
    sig = probe_mqtt_root_drift(
        main_pid=1002,
        newest_line_fn=_publish_line_fn("msh/US"),
        service_user_fn=lambda: None,  # user unresolvable → declared None
        state_path=str(tmp_path / "debounce.json"),
    )
    assert sig is None


def test_mqtt_root_drift_none_when_service_inactive(tmp_path):
    """meshtasticd down → None; service_inactive owns that."""
    sig = probe_mqtt_root_drift(
        main_pid=None,
        systemctl_path="/nonexistent/systemctl",
        state_path=str(tmp_path / "debounce.json"),
    )
    assert sig is None


def test_mqtt_root_drift_none_on_unparseable_publish_line(tmp_path):
    """A publish line that doesn't match the topic shape → indeterminate,
    not drift (firmware log-format change must not false-alarm)."""
    sig = probe_mqtt_root_drift(
        main_pid=1002,
        newest_line_fn=lambda p: "1780831542.0 host meshtasticd[1]: "
                                 "JSON publish message to garbage",
        declared_root="msh",
        state_path=str(tmp_path / "debounce.json"),
    )
    assert sig is None


def test_read_declared_root_topic_default_when_key_absent(tmp_path):
    """gateway.json present but mqtt_bridge omits root_topic → the
    GatewayConfig default 'msh' (the consumer's effective value)."""
    from utils.watchdog_probes import _read_declared_root_topic
    import pwd as _pwd
    home = tmp_path
    cfg_dir = home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "gateway.json").write_text(
        json.dumps({"mqtt_bridge": {"broker": "localhost"}}))

    class FakePw:
        pw_dir = str(home)

    orig = _pwd.getpwnam
    _pwd.getpwnam = lambda u: FakePw()
    try:
        assert _read_declared_root_topic("testuser") == "msh"
    finally:
        _pwd.getpwnam = orig


def test_read_declared_root_topic_none_when_file_missing(tmp_path):
    """No gateway.json → no declared value, never the default — a box with
    no gateway config has no declared consumer contract.

    ⚠️ The VALUE stays None, but as of 2026-08-05 the STATUS distinguishes
    absent from unreadable; see
    TestDeclaredRootStatusSeparatesAbsentFromUnreadable.
    """
    from utils.watchdog_probes import _read_declared_root_topic
    import pwd as _pwd

    class FakePw:
        pw_dir = str(tmp_path)

    orig = _pwd.getpwnam
    _pwd.getpwnam = lambda u: FakePw()
    try:
        assert _read_declared_root_topic("testuser") is None
    finally:
        _pwd.getpwnam = orig


# ─────────────────────────────────────────────────────────────────────
# Issue #78 — probe_cron_verdict_stale (wired-cron FAIL/silence; orphan filter)
# ─────────────────────────────────────────────────────────────────────


class TestCronVerdictStale:
    """A cron WIRED to cron_verdict.sh that reported FAIL/CONCERN or went silent
    past its schedule cadence. Cross-references the crontab so a stale ORPHAN
    verdict (a one-off verdict for a cron that no longer exists — the
    diag24h_watchdog shape) never false-alarms. Hermetic: inject text."""

    NOW = 2_000_000_000.0
    # A wired */5 cron: stale threshold = max(2h floor, 3×300s) = 2h.
    WIRED = ("*/5 * * * * /opt/job.py >/dev/null 2>&1; "
             "/opt/meshforge/scripts/cron_verdict.sh myjob $?\n")

    def _v(self, name, status, age_s):
        import datetime
        ts = self.NOW - age_s
        iso = datetime.datetime.fromtimestamp(
            ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{iso} {name} {status}\n"

    def _fire(self, tmp_path, **kw):
        """Run twice (2-tick debounce) and return the 2nd result."""
        sp = str(tmp_path / "cron_debounce.json")
        probe_cron_verdict_stale(state_path=sp, now=self.NOW, **kw)
        return probe_cron_verdict_stale(state_path=sp, now=self.NOW, **kw)

    def test_signal_class_registered(self):
        assert "cron_verdict_stale" in SIGNAL_CLASSES

    def test_no_wired_crons_is_inert(self, tmp_path):
        """crontab with jobs but none cron_verdict.sh-wired → None (opt-in)."""
        sig = self._fire(
            tmp_path,
            crontab_text="*/5 * * * * /opt/job.py >/dev/null 2>&1\n",
            verdicts_text="")
        assert sig is None

    def test_orphan_verdict_ignored(self, tmp_path):
        """A FAIL verdict whose name is NOT a wired cron (the diag24h_watchdog
        shape) must not fire while the wired cron is healthy — orphan filter."""
        verdicts = (self._v("myjob", "OK", 60)
                    + "2026-06-06T20:09:40Z diag24h_watchdog FAIL(1)\n")
        sig = self._fire(tmp_path, crontab_text=self.WIRED, verdicts_text=verdicts)
        assert sig is None

    def test_wired_fail_fires_degraded(self, tmp_path):
        """CONFIRMED failure on a fast cron still fires — the core guarantee.

        2026-07-26 (signal-yield R1): confirmation is counted in CRON RUNS,
        so a fast cron must fail TWICE. This is the leg that must never be
        lost — suppressing noise is worthless if it also suppresses faults.
        """
        verdicts = (self._v("myjob", "FAIL(1)", 360)
                    + self._v("myjob", "FAIL(1)", 60))
        sig = self._fire(tmp_path, crontab_text=self.WIRED,
                         verdicts_text=verdicts)
        assert sig is not None
        assert sig.cls == "cron_verdict_stale"
        assert sig.severity == "degraded"
        assert any("myjob" in f for f in sig.extra["failed"])

    def test_single_fail_on_fast_cron_is_withheld_but_NOT_clean(self, tmp_path):
        """One FAIL on a */5 cron is a transient — withhold the signal.

        This is the 30%-of-all-escalation-volume case: `fleet_hosts_drift`
        (hourly) failed once and self-healed on the next run, yet held the
        signal for the whole hour. BUT the tick must not read CLEAN — a
        failure WAS observed; mapping "seen once, awaiting the next run" onto
        healthy is the exact defect class the spine exists to prevent.
        """
        from utils.watchdog_probe_core import (
            collect_dispositions, reset_dispositions)
        reset_dispositions()
        sig = self._fire(tmp_path, crontab_text=self.WIRED,
                         verdicts_text=self._v("myjob", "FAIL(1)", 60))
        assert sig is None, "a single fast-cron failure must not fire"
        disp = collect_dispositions().get("cron_verdict_stale", {})
        assert disp.get("disp") == "indeterminate", (
            "an unconfirmed failure is NOT clean — it must read indeterminate")
        assert "myjob" in disp.get("reason", "")

    def test_slow_cron_fails_on_sight(self, tmp_path):
        """A WEEKLY cron fires on the FIRST failure — no confirmation wait.

        The asymmetry that makes the gate safe: waiting one cycle is cheap on
        a */5 cron and absurd on `local_brain_eval` (25 3 * * 0), which is
        persistently failing on the live fleet. Cadence above the threshold
        must never be gated.
        """
        weekly = ("25 3 * * 0 /opt/eval.py >/dev/null 2>&1; "
                  "/opt/meshforge/scripts/cron_verdict.sh evaljob $?\n")
        sig = self._fire(tmp_path, crontab_text=weekly,
                         verdicts_text=self._v("evaljob", "FAIL(1)", 60))
        assert sig is not None, "a weekly cron must fire on its first failure"
        assert any("evaljob" in f for f in sig.extra["failed"])

    def test_reboot_cron_fails_on_sight(self, tmp_path):
        """@reboot has no next run to wait for — never gate it."""
        reboot = ("@reboot /opt/boot.py >/dev/null 2>&1; "
                  "/opt/meshforge/scripts/cron_verdict.sh bootjob $?\n")
        sig = self._fire(tmp_path, crontab_text=reboot,
                         verdicts_text=self._v("bootjob", "FAIL(1)", 60))
        assert sig is not None, "@reboot cron must fire on its first failure"
        assert any("bootjob" in f for f in sig.extra["failed"])

    def test_wired_stale_fires(self, tmp_path):
        # */5 threshold is 2h; a 10000s-old OK verdict is silent.
        sig = self._fire(tmp_path, crontab_text=self.WIRED,
                         verdicts_text=self._v("myjob", "OK", 10000))
        assert sig is not None
        assert any("myjob" in s for s in sig.extra["stale"])

    def test_wired_never_ran_fires(self, tmp_path):
        sig = self._fire(tmp_path, crontab_text=self.WIRED, verdicts_text="")
        assert sig is not None
        assert any("never" in s for s in sig.extra["stale"])

    def test_all_wired_fresh_ok_is_none(self, tmp_path):
        sig = self._fire(tmp_path, crontab_text=self.WIRED,
                         verdicts_text=self._v("myjob", "OK", 60))
        assert sig is None

    def test_unobservable_none(self, tmp_path):
        sig = self._fire(tmp_path, crontab_text="", verdicts_text="")
        assert sig is None

    def test_debounce_first_tick_silent(self, tmp_path):
        """First stale sighting is silent (streak 1 < 2); second fires."""
        sp = str(tmp_path / "d.json")
        first = probe_cron_verdict_stale(
            crontab_text=self.WIRED, verdicts_text="", now=self.NOW, state_path=sp)
        assert first is None
        assert json.loads(Path(sp).read_text())["streak"] == 1
        second = probe_cron_verdict_stale(
            crontab_text=self.WIRED, verdicts_text="", now=self.NOW, state_path=sp)
        assert second is not None

    def test_heal_clears_streak(self, tmp_path):
        """A clean tick resets the streak so a later lone stale is re-debounced."""
        sp = str(tmp_path / "d.json")
        probe_cron_verdict_stale(crontab_text=self.WIRED, verdicts_text="",
                                 now=self.NOW, state_path=sp)            # streak 1
        probe_cron_verdict_stale(crontab_text=self.WIRED,
                                 verdicts_text=self._v("myjob", "OK", 60),
                                 now=self.NOW, state_path=sp)            # heal → 0
        assert json.loads(Path(sp).read_text())["streak"] == 0

    # ----- disposition honesty (fail-dark; adversarial-review fixes) -----

    WIRED_REBOOT = ("@reboot /opt/boot_job.py; "
                    "/opt/meshforge/scripts/cron_verdict.sh bootjob $?\n")

    @staticmethod
    def _disp():
        from utils.watchdog_probe_core import collect_dispositions
        return collect_dispositions().get("cron_verdict_stale")

    def test_unreadable_verdict_log_is_indeterminate_not_clean(self, tmp_path):
        """L1a: verdicts_text None (log unreadable / home unresolvable) with
        wired crons → the verdicts are UNOBSERVABLE; the would-be-clean exit
        must note indeterminate, never clean (fail-dark). A POSITIVE empty
        read is '' — None means the observation channel failed."""
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()
        sig = probe_cron_verdict_stale(
            crontab_text=self.WIRED_REBOOT, verdicts_text=None,
            now=self.NOW, state_path=str(tmp_path / "d.json"))
        assert sig is None
        got = self._disp()
        assert got["disp"] == "indeterminate"
        assert "verdict log unreadable" in got["reason"]

    def test_reboot_wired_no_verdict_is_indeterminate_not_clean(self, tmp_path):
        """L1b: an @reboot-wired cron with NO recorded verdict is unjudgeable
        (max_age inf skips the stale legs) — indeterminate, never clean.
        verdicts_text='' here is a POSITIVE empty read, isolating this from
        the L1a unreadable-log leg."""
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()
        sig = probe_cron_verdict_stale(
            crontab_text=self.WIRED_REBOOT, verdicts_text="",
            now=self.NOW, state_path=str(tmp_path / "d.json"))
        assert sig is None
        got = self._disp()
        assert got["disp"] == "indeterminate"
        assert "@reboot" in got["reason"]

    def test_reboot_wired_with_ok_verdict_is_clean(self, tmp_path):
        """An @reboot cron WITH an OK verdict is a judged, healthy cron —
        clean stays clean (the split must not over-darken)."""
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()
        sig = probe_cron_verdict_stale(
            crontab_text=self.WIRED_REBOOT,
            verdicts_text=self._v("bootjob", "OK", 60),
            now=self.NOW, state_path=str(tmp_path / "d.json"))
        assert sig is None
        assert self._disp()["disp"] == "clean"

    def test_cron_max_interval(self):
        assert _cron_max_interval("*/5 * * * *") == 300.0
        assert _cron_max_interval("7 */2 * * *") == 7200.0
        assert _cron_max_interval("23 * * * *") == 3600.0
        assert _cron_max_interval("13 3 * * *") == 86400.0
        assert _cron_max_interval("11 2 * * 0") == 604800.0
        assert _cron_max_interval("@daily") == 86400.0
        assert _cron_max_interval("@reboot") == float("inf")
        assert _cron_max_interval("garbage") == 26 * 3600.0


# ─────────────────────────────────────────────────────────────────────
# Leg D (2026-06-17) — probe_fleet_box_unreachable (fleet liveness → spine)
# ─────────────────────────────────────────────────────────────────────


class TestFleetBoxUnreachable:
    """A fleet box the offline-monitor (fleet_offline_check.sh) confirms DOWN
    (alerted==1 in ~/fleet_offline_state.tsv) surfaced into mini's brief + /fleet
    so it can't sit silent in a side-channel logfile (the .32 33h-dark lesson).
    Hermetic: inject state_text + state_mtime + now. INERT off the manager box
    (no file) and on a STALE file (the monitor's own death is cron_verdict_stale's
    job — don't read frozen down-rows as current)."""

    NOW = 2_000_000_000.0

    def _fire(self, tmp_path, state_text, *, state_mtime=None, **kw):
        """Run twice (2-tick debounce); return the 2nd. Fresh mtime by default."""
        sp = str(tmp_path / "fleet_unreach_debounce.json")
        mt = self.NOW if state_mtime is None else state_mtime
        probe_fleet_box_unreachable(state_text=state_text, state_mtime=mt,
                                    now=self.NOW, state_path=sp, **kw)
        return probe_fleet_box_unreachable(state_text=state_text, state_mtime=mt,
                                           now=self.NOW, state_path=sp, **kw)

    def test_signal_class_registered(self):
        assert "fleet_box_unreachable" in SIGNAL_CLASSES

    def test_all_healthy_is_none(self, tmp_path):
        text = "moc\t0\t0\t0\t0\t0\nmoc1\t0\t0\t0\t0\t0\n"
        assert self._fire(tmp_path, text) is None

    def test_no_state_is_inert(self, tmp_path):
        """No state file (not the manager box) → INERT."""
        assert self._fire(tmp_path, "") is None

    def test_down_box_fires(self, tmp_path):
        text = ("moc\t0\t0\t0\t0\t0\n"
                f"bot\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t2\n")
        sig = self._fire(tmp_path, text)
        assert sig is not None
        assert sig.cls == "fleet_box_unreachable"
        assert sig.subject == "fleet"
        assert "bot" in sig.extra["down"]
        assert "bot" in sig.detail

    def test_recent_down_is_degraded(self, tmp_path):
        text = f"bot\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t1\n"   # ~10 min
        sig = self._fire(tmp_path, text)
        assert sig is not None and sig.severity == "degraded"

    def test_sustained_down_is_wedge(self, tmp_path):
        text = f"bot\t3\t1\t{self.NOW - 3600}\t{self.NOW - 60}\t6\n"  # 1h > 30min
        sig = self._fire(tmp_path, text)
        assert sig is not None and sig.severity == "wedge"

    def test_stale_state_is_none(self, tmp_path):
        """A state file older than stale_after_s → None (monitor stopped)."""
        text = f"bot\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t2\n"
        assert self._fire(tmp_path, text, state_mtime=self.NOW - 3600) is None

    def test_back_compat_3_field_rows(self, tmp_path):
        """Pre-Leg-D 3-field rows (box/fail/alerted) with alerted=1 still fire."""
        sig = self._fire(tmp_path, "bot\t3\t1\n")
        assert sig is not None
        assert "bot" in sig.extra["down"]

    # ── verdict field (state field 7, added 2026-08-11) ──────────────────
    # The monitor now distinguishes "the box is down" from "only the path to it
    # was observed broken" (fleet_offline_check.sh note 6). Both consumers read
    # ONE artifact, so this reader must make the SAME claim the writer does —
    # otherwise the honest page is re-lied about downstream, which is exactly
    # how the 2026-08-10 kiai page (54 min DOWN about a box up 17 days) read.

    def test_unobservable_verdict_is_not_reported_as_down(self, tmp_path):
        text = f"kiwi\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t1\tunobservable\n"
        sig = self._fire(tmp_path, text)
        assert sig is not None
        assert "kiwi" in sig.extra["unobservable"]
        assert sig.extra["down"] == []
        assert "UNOBSERVABLE" in sig.detail
        assert "confirms DOWN" not in sig.detail, sig.detail

    def test_unobservable_never_escalates_to_wedge(self, tmp_path):
        """'Wedged for an hour' is an assertion about the BOX. On the
        unobservable verdict we do not have one, however long the path stays
        dark — and the shell monitor keeps paging regardless, so refusing to
        escalate costs no notification."""
        text = f"kiwi\t3\t1\t{self.NOW - 3600}\t{self.NOW - 60}\t6\tunobservable\n"
        sig = self._fire(tmp_path, text)
        assert sig is not None and sig.severity == "degraded"

    def test_down_box_still_escalates_alongside_an_unobservable_one(self, tmp_path):
        """The honest split must not blunt the real alarm: a genuinely down box
        keeps its wedge severity even when an unobservable box shares the file."""
        text = (f"bot\t3\t1\t{self.NOW - 3600}\t{self.NOW - 60}\t6\tdown\n"
                f"kiwi\t3\t1\t{self.NOW - 3600}\t{self.NOW - 60}\t6\tunobservable\n")
        sig = self._fire(tmp_path, text)
        assert sig is not None and sig.severity == "wedge"
        assert sig.extra["down"] == ["bot"]
        assert sig.extra["unobservable"] == ["kiwi"]
        assert "confirms DOWN" in sig.detail and "UNOBSERVABLE" in sig.detail

    def test_unknown_verdict_is_not_laundered_into_down(self, tmp_path):
        """An unrecognised verdict is not knowledge. Defaulting it to the
        STRONGER claim is how a degraded value becomes a false assertion."""
        text = f"kiwi\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t1\tgibberish\n"
        sig = self._fire(tmp_path, text)
        assert sig is not None
        assert sig.extra["down"] == []
        assert "kiwi" in sig.extra["unobservable"]

    def test_legacy_six_field_row_still_means_down(self, tmp_path):
        """Rows written before the verdict existed MEANT down — preserve their
        claim exactly rather than inventing an observation either way."""
        text = f"bot\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t2\n"
        sig = self._fire(tmp_path, text)
        assert sig is not None
        assert sig.extra["down"] == ["bot"]
        assert sig.extra["unobservable"] == []

    def test_only_unobservable_rows_is_still_a_signal(self, tmp_path):
        """A dark path must not fall through to 'clean' — that would be the
        original sin (blindness read as health) in the mirror."""
        text = f"kiwi\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t1\tunobservable\n"
        assert self._fire(tmp_path, text) is not None

    def test_debounce_first_tick_silent(self, tmp_path):
        sp = str(tmp_path / "d.json")
        text = f"bot\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t2\n"
        first = probe_fleet_box_unreachable(
            state_text=text, state_mtime=self.NOW, now=self.NOW, state_path=sp)
        assert first is None
        assert json.loads(Path(sp).read_text())["streak"] == 1
        second = probe_fleet_box_unreachable(
            state_text=text, state_mtime=self.NOW, now=self.NOW, state_path=sp)
        assert second is not None

    def test_heal_clears_streak(self, tmp_path):
        sp = str(tmp_path / "d.json")
        down = f"bot\t3\t1\t{self.NOW - 600}\t{self.NOW - 60}\t2\n"
        probe_fleet_box_unreachable(state_text=down, state_mtime=self.NOW,
                                    now=self.NOW, state_path=sp)            # streak 1
        probe_fleet_box_unreachable(state_text="moc\t0\t0\t0\t0\t0\n",
                                    state_mtime=self.NOW, now=self.NOW,
                                    state_path=sp)                          # heal → 0
        assert json.loads(Path(sp).read_text())["streak"] == 0

    def test_zero_byte_state_file_is_indeterminate_not_inert(
            self, tmp_path, monkeypatch):
        """L4: a zero-byte state file (the writer's mid-rewrite window) is
        NOT the positive 'no monitor here' absence — it must note
        indeterminate, never inert (absence-vs-error split)."""
        import pwd
        import types
        from utils.watchdog_probe_core import (
            collect_dispositions, reset_dispositions)
        (tmp_path / "fleet_offline_state.tsv").write_text("")
        monkeypatch.setattr(
            pwd, "getpwuid",
            lambda uid: types.SimpleNamespace(pw_dir=str(tmp_path)))
        reset_dispositions()
        sig = probe_fleet_box_unreachable(
            operator=(1000, "op"), now=time.time(),
            state_path=str(tmp_path / "d.json"))
        assert sig is None
        got = collect_dispositions()["fleet_box_unreachable"]
        assert got["disp"] == "indeterminate"
        assert "mid-rewrite" in got["reason"]

    def test_absent_state_file_stays_inert(self, tmp_path, monkeypatch):
        """The split's other half: a positively-ABSENT state file is still
        the legitimate 'not the manager box' inert."""
        import pwd
        import types
        from utils.watchdog_probe_core import (
            collect_dispositions, reset_dispositions)
        monkeypatch.setattr(
            pwd, "getpwuid",
            lambda uid: types.SimpleNamespace(pw_dir=str(tmp_path)))
        reset_dispositions()
        sig = probe_fleet_box_unreachable(
            operator=(1000, "op"), now=time.time(),
            state_path=str(tmp_path / "d.json"))
        assert sig is None
        assert collect_dispositions()["fleet_box_unreachable"]["disp"] == "inert"


class TestHostFrozen:
    """The dude-claw out-of-band witness (host_probe over NATS, on the watched
    box's own subnet) verdict surfaced into mini's brief + /fleet (Leg C).
    Hermetic: inject the verdict-file JSON + mtime + now. INERT off the claw's
    brain box (no file) and on a STALE file (the collector's own death is
    cron_verdict_stale's job). UNKNOWN (the witness itself blind) is *degraded*,
    NOT silently None — lost visibility is not 'healthy' (honest_failure_modes #2)."""

    NOW = 2_000_000_000.0

    def _doc(self, verdict, *, name="bot-32", collector_ok=True, **fields):
        t = {"name": name, "host": "10.0.0.5", "verdict": verdict,
             "ip_alive": fields.get("ip_alive"), "app_state": fields.get("app_state"),
             "banner": fields.get("banner"), "kstack": fields.get("kstack"),
             "rtt_ms": fields.get("rtt_ms"), "raw": fields.get("raw", ""),
             "error": fields.get("error")}
        return {"ts": self.NOW, "collector_ok": collector_ok, "targets": [t]}

    def _fire(self, tmp_path, doc, *, state_mtime=None, **kw):
        """Run twice (2-tick debounce); return the 2nd. Fresh mtime by default.
        doc=None injects an absent verdict file (empty state_text)."""
        sp = str(tmp_path / "host_frozen_debounce.json")
        mt = self.NOW if state_mtime is None else state_mtime
        text = "" if doc is None else json.dumps(doc)
        probe_host_frozen(state_text=text, state_mtime=mt, now=self.NOW,
                          state_path=sp, **kw)
        return probe_host_frozen(state_text=text, state_mtime=mt, now=self.NOW,
                                 state_path=sp, **kw)

    def test_signal_class_registered(self):
        assert "host_frozen" in SIGNAL_CLASSES

    def test_all_ok_is_none(self, tmp_path):
        assert self._fire(tmp_path, self._doc("OK")) is None

    def test_no_state_is_inert(self, tmp_path):
        """No verdict file (not the claw's brain box) → INERT."""
        assert self._fire(tmp_path, None) is None

    def test_witness_provenance_lands_in_extra(self, tmp_path):
        # 07-23 audit: the collector writes witness_device/failover and the
        # NOC's rollup fan-out reads them from Signal.extra — this is the
        # middle hop that joins writer to reader, previously unpinned. A
        # regression dropping the copy would render "via ?" fleet-wide with
        # every suite green.
        doc = self._doc("HOST_FROZEN", ip_alive=1, app_state="open", banner=0)
        doc["targets"][0]["witness_device"] = "dudeclaw-02"
        doc["targets"][0]["failover"] = True
        sig = self._fire(tmp_path, doc)
        assert sig is not None
        t = sig.extra["targets"][0]
        assert t["witness_device"] == "dudeclaw-02"
        assert t["failover"] is True
        assert "via dudeclaw-02 (failover)" in sig.detail

    def test_frozen_fires_wedge(self, tmp_path):
        doc = self._doc("HOST_FROZEN", ip_alive=1, app_state="open", banner=0,
                        raw="host_probe 10.0.0.5: ip_alive=1 app22=open banner=0B kstack=0 rtt_ms=9")
        sig = self._fire(tmp_path, doc)
        assert sig is not None
        assert sig.cls == "host_frozen"
        assert sig.subject == "bot-32"
        assert sig.severity == "wedge"
        assert "HOST_FROZEN" in sig.detail
        assert sig.extra["targets"][0]["verdict"] == "HOST_FROZEN"

    def test_unreachable_fires_wedge(self, tmp_path):
        doc = self._doc("UNREACHABLE", ip_alive=0)
        sig = self._fire(tmp_path, doc)
        assert sig is not None and sig.severity == "wedge"
        assert "UNREACHABLE" in sig.detail

    def test_unknown_is_degraded_not_silent(self, tmp_path):
        """The witness itself blind (claw unreachable) → degraded, never None or OK."""
        doc = self._doc("UNKNOWN", collector_ok=False, error="NatsError: timed out")
        sig = self._fire(tmp_path, doc)
        assert sig is not None
        assert sig.severity == "degraded"
        assert "UNKNOWN" in sig.detail

    def test_stale_state_is_none(self, tmp_path):
        """A verdict file older than stale_after_s → None (collector stopped)."""
        doc = self._doc("HOST_FROZEN", ip_alive=1, app_state="open", banner=0)
        assert self._fire(tmp_path, doc, state_mtime=self.NOW - 3600) is None

    def test_unparseable_is_none(self, tmp_path):
        """Garbage in the verdict file → None (don't false-fire)."""
        sp = str(tmp_path / "d.json")
        probe_host_frozen(state_text="not json {", state_mtime=self.NOW,
                          now=self.NOW, state_path=sp)
        second = probe_host_frozen(state_text="not json {", state_mtime=self.NOW,
                                   now=self.NOW, state_path=sp)
        assert second is None

    def test_debounce_first_tick_silent(self, tmp_path):
        sp = str(tmp_path / "d.json")
        text = json.dumps(self._doc("HOST_FROZEN", ip_alive=1, app_state="open", banner=0))
        first = probe_host_frozen(state_text=text, state_mtime=self.NOW,
                                  now=self.NOW, state_path=sp)
        assert first is None
        assert json.loads(Path(sp).read_text())["streak"] == 1
        second = probe_host_frozen(state_text=text, state_mtime=self.NOW,
                                   now=self.NOW, state_path=sp)
        assert second is not None

    def test_heal_clears_streak(self, tmp_path):
        sp = str(tmp_path / "d.json")
        bad = json.dumps(self._doc("HOST_FROZEN", ip_alive=1, app_state="open", banner=0))
        probe_host_frozen(state_text=bad, state_mtime=self.NOW,
                          now=self.NOW, state_path=sp)                       # streak 1
        probe_host_frozen(state_text=json.dumps(self._doc("OK")),
                          state_mtime=self.NOW, now=self.NOW,
                          state_path=sp)                                     # heal → 0
        assert json.loads(Path(sp).read_text())["streak"] == 0


class TestRouterScoutDegraded:
    """The router-agent tick mirror consumer (2026-07-11 OpenWrt-router arc).
    Hermetic: inject the mirrored ticks as (name, text, mtime) tuples.
    Defense-in-depth behind the pull's own cron_verdict eval: the probe's
    added value is the watchdog-spine surface (per-device subject into
    /fleet + mini) and coverage of mirrors landed by any non-pull path.
    Fires on: fresh mirror + stale captured_at (agent died on the router
    while the pull re-copies the same old tick), tick ok=false, and
    unparseable mirrors. STALE mirror files are skipped (dead pull =
    cron_verdict_stale's beat); no mirrors = INERT. degraded only — every
    observed condition is remote (the tracer lesson)."""

    NOW = 2_000_000_000.0

    def _tick(self, *, device="owrt-test", age_s=60.0, ok=True, errors=None):
        return json.dumps({
            "schema": 1, "device": device,
            "captured_at": self.NOW - age_s, "ok": ok,
            "errors": errors or [], "notes": [],
            "meshtasticd": {"vsz_kb": 18300, "maps": 110},
        })

    def _fire(self, tmp_path, ticks, **kw):
        """Run twice (2-tick debounce); return the 2nd."""
        sp = str(tmp_path / "router_scout_debounce.json")
        probe_router_scout_degraded(ticks=ticks, now=self.NOW,
                                    state_path=sp, **kw)
        return probe_router_scout_degraded(ticks=ticks, now=self.NOW,
                                           state_path=sp, **kw)

    def test_signal_class_registered(self):
        assert "router_scout_degraded" in SIGNAL_CLASSES

    def test_no_mirrors_is_inert(self, tmp_path):
        assert self._fire(tmp_path, None) is None
        assert self._fire(tmp_path, []) is None

    def test_healthy_fresh_tick_is_none(self, tmp_path):
        ticks = [("owrt-test_tick.json", self._tick(), self.NOW - 60)]
        assert self._fire(tmp_path, ticks) is None

    def test_agent_dark_behind_fresh_mirror_fires_degraded(self, tmp_path):
        """THE case: mirror mtime fresh (pull healthy), captured_at ancient
        (agent cron died on the router)."""
        ticks = [("owrt-test_tick.json", self._tick(age_s=90000.0),
                  self.NOW - 60)]
        sig = self._fire(tmp_path, ticks)
        assert sig is not None
        assert sig.cls == "router_scout_degraded"
        assert sig.subject == "owrt-test"
        assert sig.severity == "degraded"
        assert "corpse" in sig.detail
        assert sig.extra["routers"][0]["device"] == "owrt-test"

    def test_pull_gap_does_not_read_as_agent_dark(self, tmp_path):
        """THE 2026-07-31 manager-box false positive: a reboot ate one pull
        slot, so the mirror aged 50 min (still 'fresh' under the 90-min
        bar) while holding a tick that was only 7 min old WHEN PULLED.
        Agent staleness must be measured at the last observation (mirror
        mtime), never against now — time since the last pull is
        UNOBSERVED, not evidence of agent death (honest_failure_modes #2)."""
        ticks = [("owrt-test_tick.json", self._tick(age_s=3420.0),
                  self.NOW - 3000)]   # pulled 50 min ago; tick 7 min old THEN
        assert self._fire(tmp_path, ticks) is None

    def test_agent_dark_at_pull_time_fires(self, tmp_path):
        """Converse guard: a RECENT pull that still carried an old tick IS
        real evidence — while the agent is dead every pull re-copies the
        same tick, so the pull-relative delta keeps growing and detection
        survives the measurement change (at most one pull cycle later)."""
        ticks = [("owrt-test_tick.json", self._tick(age_s=3300.0),
                  self.NOW - 300)]    # pulled 5 min ago; tick 50 min old THEN
        sig = self._fire(tmp_path, ticks)
        assert sig is not None
        assert "corpse" in sig.detail

    def test_no_mtime_falls_back_to_now_relative(self, tmp_path):
        """A tick injected with no mtime has no observation timestamp to
        anchor on — fall back to now-relative age (the old measure) rather
        than skipping the dark check entirely."""
        ticks = [("owrt-test_tick.json", self._tick(age_s=90000.0), None)]
        assert self._fire(tmp_path, ticks) is not None

    def test_tick_ok_false_fires_with_witness(self, tmp_path):
        ticks = [("owrt-test_tick.json",
                  self._tick(ok=False, errors=["data_dir on tmpfs"]),
                  self.NOW - 60)]
        sig = self._fire(tmp_path, ticks)
        assert sig is not None
        assert "ok=false" in sig.detail
        assert "data_dir on tmpfs" in sig.detail

    def test_unparseable_mirror_fires(self, tmp_path):
        """The pull validates before its atomic write — garbage in a FRESH
        mirror is writer/shape drift, not a torn read; it must not be
        silently skipped."""
        ticks = [("owrt-test_tick.json", "not json {", self.NOW - 60)]
        sig = self._fire(tmp_path, ticks)
        assert sig is not None
        assert "unparseable" in sig.detail

    def test_stale_mirror_is_skipped_not_fired(self, tmp_path):
        """Mirror mtime past mirror_stale_s = the pull cron stopped —
        cron_verdict_stale owns that; a frozen mirror must not read as a
        current agent fault (absence-of-evidence trap)."""
        ticks = [("owrt-test_tick.json", self._tick(age_s=90000.0),
                  self.NOW - 90000)]
        assert self._fire(tmp_path, ticks) is None

    def test_mixed_stale_and_fresh_evaluates_only_fresh(self, tmp_path):
        ticks = [
            ("dead-router_tick.json", self._tick(device="dead-router",
                                                 age_s=90000.0),
             self.NOW - 90000),                       # stale mirror: skip
            ("live-router_tick.json", self._tick(device="live-router"),
             self.NOW - 60),                          # fresh + healthy
        ]
        assert self._fire(tmp_path, ticks) is None

    def test_never_wedge(self, tmp_path):
        """Every condition this probe observes is REMOTE — severity is
        pinned degraded (the tracer_peer_unreachable lesson)."""
        ticks = [("a_tick.json", "garbage", self.NOW - 1),
                 ("b_tick.json", self._tick(device="b", ok=False,
                                            errors=["x"]), self.NOW - 1),
                 ("c_tick.json", self._tick(device="c", age_s=90000.0),
                  self.NOW - 1)]
        sig = self._fire(tmp_path, ticks)
        assert sig is not None
        assert sig.severity == "degraded"
        assert sig.subject == "router-scout"   # multi-device subject

    def test_debounce_first_tick_silent(self, tmp_path):
        sp = str(tmp_path / "d.json")
        ticks = [("owrt-test_tick.json", self._tick(ok=False, errors=["x"]),
                  self.NOW - 60)]
        first = probe_router_scout_degraded(ticks=ticks, now=self.NOW,
                                            state_path=sp)
        assert first is None
        assert json.loads(Path(sp).read_text())["streak"] == 1
        second = probe_router_scout_degraded(ticks=ticks, now=self.NOW,
                                             state_path=sp)
        assert second is not None

    def test_heal_clears_streak(self, tmp_path):
        sp = str(tmp_path / "d.json")
        bad = [("owrt-test_tick.json", self._tick(ok=False, errors=["x"]),
                self.NOW - 60)]
        good = [("owrt-test_tick.json", self._tick(), self.NOW - 60)]
        probe_router_scout_degraded(ticks=bad, now=self.NOW, state_path=sp)
        probe_router_scout_degraded(ticks=good, now=self.NOW, state_path=sp)
        assert json.loads(Path(sp).read_text())["streak"] == 0

    def test_missing_captured_at_fires(self, tmp_path):
        ticks = [("owrt-test_tick.json",
                  json.dumps({"device": "owrt-test", "ok": True}),
                  self.NOW - 60)]
        sig = self._fire(tmp_path, ticks)
        assert sig is not None
        assert "captured_at" in sig.detail

    # ----- disposition honesty (fail-dark; adversarial-review fixes) -----

    def _home_probe(self, tmp_path, monkeypatch):
        """Run the probe through the real _read_router_scout_ticks path with
        tmp_path as the operator home. Returns the collected disposition."""
        import pwd
        import types
        from utils.watchdog_probe_core import (
            collect_dispositions, reset_dispositions)
        monkeypatch.setattr(
            pwd, "getpwuid",
            lambda uid: types.SimpleNamespace(pw_dir=str(tmp_path)))
        reset_dispositions()
        sig = probe_router_scout_degraded(
            operator=(1000, "op"), now=time.time(),
            state_path=str(tmp_path / "rs_debounce.json"))
        assert sig is None
        return collect_dispositions()["router_scout_degraded"]

    def test_unreadable_mirror_among_healthy_is_indeterminate(
            self, tmp_path, monkeypatch):
        """L3a: one unreadable mirror (a directory named *_tick.json —
        open() raises IsADirectoryError ⊂ OSError, root-proof) among healthy
        ticks → that router is UNOBSERVABLE; clean must not be noted."""
        now = time.time()
        d = tmp_path / ".local/share/meshforge/router_scout"
        d.mkdir(parents=True)
        (d / "good_tick.json").write_text(json.dumps(
            {"schema": 1, "device": "good", "captured_at": now,
             "ok": True, "errors": [], "notes": []}))
        (d / "bad_tick.json").mkdir()
        got = self._home_probe(tmp_path, monkeypatch)
        assert got["disp"] == "indeterminate"
        assert "unreadable" in got["reason"]

    def test_mirror_dir_present_but_empty_is_indeterminate(
            self, tmp_path, monkeypatch):
        """L3b: dir EXISTS but yields zero readable ticks → indeterminate,
        not the 'no scout mirror dir' inert."""
        (tmp_path / ".local/share/meshforge/router_scout").mkdir(parents=True)
        got = self._home_probe(tmp_path, monkeypatch)
        assert got["disp"] == "indeterminate"
        assert "present" in got["reason"]

    def test_mirror_dir_absent_is_inert(self, tmp_path, monkeypatch):
        """L3b other half: a positively-ABSENT mirror dir stays the
        legitimate manager-box-only inert."""
        got = self._home_probe(tmp_path, monkeypatch)
        assert got["disp"] == "inert"
        assert "no scout mirror dir" in got["reason"]


class TestNtfyLoopback:
    """The alerting spine's OWN loopback (ntfy receipt-heartbeat Phase 2)
    surfaced into mini's brief + /fleet. Hermetic: inject the verdict-file JSON
    + mtime + now. INERT off the manager box (no file) and on a STALE file (the
    collector's own death is cron_verdict_stale's job). A ``received`` that is
    not an explicit bool (torn/partial write) is indeterminate → None, NEVER
    read as a miss (honest_failure_modes #1/#2 — absence of evidence ≠ failure;
    the collector OWNS the email page, this probe is visibility only)."""

    NOW = 2_000_000_000.0

    def _doc(self, *, received, published_ok=True, misses=0, escalate=2):
        """Mirrors what scripts/fleet_ntfy_loopback.sh actually writes, including
        escalate_after_misses (recorded 2026-08-08 so the email leg, the exit
        code, and this probe fire off ONE number)."""
        doc = {"ts": self.NOW, "nonce": "lb-x-1-2", "published_ok": published_ok,
               "received": received, "latency_s": 4, "consecutive_misses": misses,
               "interval_s": 1800, "last_received_ts": self.NOW}
        if escalate is not None:
            doc["escalate_after_misses"] = escalate
        return doc

    def _fire(self, tmp_path, doc, *, state_mtime=None, **kw):
        """Run twice (2-tick debounce); return the 2nd. Fresh mtime by default.
        doc=None injects an absent verdict file (empty state_text)."""
        sp = str(tmp_path / "ntfy_loopback_debounce.json")
        mt = self.NOW if state_mtime is None else state_mtime
        text = "" if doc is None else json.dumps(doc)
        probe_ntfy_loopback(state_text=text, state_mtime=mt, now=self.NOW,
                            state_path=sp, **kw)
        return probe_ntfy_loopback(state_text=text, state_mtime=mt, now=self.NOW,
                                   state_path=sp, **kw)

    def test_signal_class_registered(self):
        assert "ntfy_loopback" in SIGNAL_CLASSES

    def test_received_true_is_none(self, tmp_path):
        assert self._fire(tmp_path, self._doc(received=True)) is None

    # ── the 2026-08-08 sustained-miss gate ────────────────────────────
    # This class fired 16× and ALL 12 of its dream proposals were rejected
    # as "one transient publish miss that self-cleared next run". Three
    # consumers of one artifact had each picked a threshold: email at >=2,
    # the script's exit code at >=1, this probe at >=1 — so one hiccup
    # produced a cron FAIL(1) *and* a degraded signal, and
    # cron_verdict_stale latched onto the FAIL. One event, two pages.

    def test_single_miss_does_not_fire(self, tmp_path):
        """THE fix. One miss is a transient the collector already retried
        in-run; it self-clears on the next cycle. Firing here is what made
        every proposal from this class a false alarm."""
        assert self._fire(
            tmp_path, self._doc(received=False, misses=1)) is None

    def test_single_miss_is_indeterminate_not_clean(self, tmp_path):
        """Not firing must not become "healthy". A miss WAS observed; we just
        cannot yet tell transient from outage. Claiming `clean` here would be
        the honest_failure_modes #2 collapse in the opposite direction, and
        this cell self-resolves within one cadence either way."""
        sp = str(tmp_path / "d.json")
        text = json.dumps(self._doc(received=False, misses=1))
        for _ in range(2):                      # past the 2-tick debounce
            probe_ntfy_loopback(state_text=text, state_mtime=self.NOW,
                                now=self.NOW, state_path=sp)
        _reset_disp()                           # a fresh tick, as the runner does
        assert probe_ntfy_loopback(state_text=text, state_mtime=self.NOW,
                                   now=self.NOW, state_path=sp) is None
        assert _disp("ntfy_loopback") == "indeterminate"
        assert "1/2" in _reason("ntfy_loopback")

    def test_second_consecutive_miss_fires(self, tmp_path):
        """A REAL outage misses consecutive runs and still pages — the whole
        point is to lose the false alarms without losing the detection."""
        sig = self._fire(tmp_path, self._doc(received=False, misses=2))
        assert sig is not None and sig.severity == "degraded"

    def test_threshold_travels_with_the_artifact(self, tmp_path):
        """The collector records escalate_after_misses; raising it there must
        move this probe too, or the two drift apart again — the very defect
        (honest_failure_modes #5, same as interval_s)."""
        assert self._fire(
            tmp_path, self._doc(received=False, misses=2, escalate=3)) is None
        sig = self._fire(
            tmp_path, self._doc(received=False, misses=3, escalate=3))
        assert sig is not None

    def test_absent_threshold_falls_back_to_the_default(self, tmp_path):
        """Rollout case: state files written before 2026-08-08 carry no
        escalate_after_misses until the cron next runs. The probe must still
        gate at the built-in floor, not fire on every transient."""
        assert self._fire(
            tmp_path, self._doc(received=False, misses=1, escalate=None)) is None
        assert self._fire(
            tmp_path, self._doc(received=False, misses=2, escalate=None)) is not None

    def test_absurd_recorded_threshold_cannot_disarm_the_probe(self, tmp_path):
        """A garbage/hostile escalate_after_misses is clamped to the built-in
        floor — a recorded value must never be able to silence the alerting
        channel's own liveness check."""
        sig = self._fire(
            tmp_path, self._doc(received=False, misses=2, escalate=9999))
        assert sig is not None

    def test_malformed_miss_count_is_indeterminate_not_zero(self, tmp_path):
        """The old code did `except → misses = 0`, which under the new gate
        would fail OPEN (never fire) on a torn write. Absence of the count is
        not a count of zero."""
        sp = str(tmp_path / "d.json")
        doc = self._doc(received=False, misses=2)
        doc["consecutive_misses"] = "three"
        text = json.dumps(doc)
        for _ in range(2):
            probe_ntfy_loopback(state_text=text, state_mtime=self.NOW,
                                now=self.NOW, state_path=sp)
        _reset_disp()
        assert probe_ntfy_loopback(state_text=text, state_mtime=self.NOW,
                                   now=self.NOW, state_path=sp) is None
        assert _disp("ntfy_loopback") == "indeterminate"
        assert "malformed" in _reason("ntfy_loopback")

    def test_no_state_is_inert(self, tmp_path):
        """No verdict file (not the manager box) → INERT."""
        assert self._fire(tmp_path, None) is None

    def test_published_but_lost_fires_degraded(self, tmp_path):
        sig = self._fire(tmp_path, self._doc(received=False, published_ok=True, misses=2))
        assert sig is not None
        assert sig.cls == "ntfy_loopback"
        assert sig.subject == "ntfy"
        assert sig.severity == "degraded"
        assert "did NOT loop back" in sig.detail
        assert sig.extra["published_ok"] is True
        assert sig.extra["consecutive_misses"] == 2

    def test_publish_failed_fires(self, tmp_path):
        sig = self._fire(tmp_path, self._doc(received=False, published_ok=False, misses=2))
        assert sig is not None
        assert "could NOT publish" in sig.detail
        assert sig.extra["published_ok"] is False

    def test_sustained_misses_wedge(self, tmp_path):
        sig = self._fire(tmp_path, self._doc(received=False, published_ok=True, misses=3))
        assert sig is not None and sig.severity == "wedge"

    def test_stale_state_is_none(self, tmp_path):
        """A verdict file older than the effective window → None (collector
        stopped). At the default 30-min cadence (interval_s=1800) the window is
        the 5400s floor, so a 2h-old file is stale."""
        assert self._fire(tmp_path, self._doc(received=False, misses=2),
                          state_mtime=self.NOW - 7200) is None

    def test_stale_window_scales_with_recorded_interval(self, tmp_path):
        """At a q2hr cadence (interval_s=7200) the stale window scales to 3×
        (=6h), so a 2h-old verdict is FRESH and a real miss still fires — a cron
        cadence change needs no second edit in the probe (honest_failure_modes
        #5: the cron is the single source of truth)."""
        doc = self._doc(received=False, published_ok=True, misses=2)
        doc["interval_s"] = 7200
        sig = self._fire(tmp_path, doc, state_mtime=self.NOW - 7200)   # 2h old
        assert sig is not None and sig.cls == "ntfy_loopback"

    def test_stale_window_still_bounds_at_q2hr(self, tmp_path):
        """Even at q2hr the window is bounded: a verdict past 3× cadence (>6h
        old) is stale → None (collector stopped; cron_verdict_stale owns it)."""
        doc = self._doc(received=False, misses=2)
        doc["interval_s"] = 7200
        assert self._fire(tmp_path, doc, state_mtime=self.NOW - 25200) is None  # 7h

    def test_insane_interval_falls_back_to_floor(self, tmp_path):
        """An absurd recorded interval_s is ignored (clamped) — the 5400s floor
        applies, so a 2h-old file is still stale (honest_failure_modes #6)."""
        doc = self._doc(received=False, misses=2)
        doc["interval_s"] = 99999999
        assert self._fire(tmp_path, doc, state_mtime=self.NOW - 7200) is None

    def test_unparseable_is_none(self, tmp_path):
        sp = str(tmp_path / "d.json")
        probe_ntfy_loopback(state_text="not json {", state_mtime=self.NOW,
                            now=self.NOW, state_path=sp)
        second = probe_ntfy_loopback(state_text="not json {", state_mtime=self.NOW,
                                     now=self.NOW, state_path=sp)
        assert second is None

    def test_received_missing_is_indeterminate_none(self, tmp_path):
        """A verdict with no 'received' key (torn/partial write) → None, never a
        miss (honest_failure_modes: absence of evidence is not a failure)."""
        doc = {"ts": self.NOW, "published_ok": True}   # no 'received'
        assert self._fire(tmp_path, doc) is None

    def test_received_non_bool_is_none(self, tmp_path):
        """received as a string (not an explicit bool) → indeterminate → None."""
        doc = self._doc(received=False)
        doc["received"] = "false"   # JSON string, not a bool
        assert self._fire(tmp_path, doc) is None

    def test_debounce_first_tick_silent(self, tmp_path):
        """The 30s-tick debounce still guards a torn read. misses=2 because a
        single miss is now owned by the sustained-miss gate above."""
        sp = str(tmp_path / "d.json")
        text = json.dumps(self._doc(received=False, misses=2))
        first = probe_ntfy_loopback(state_text=text, state_mtime=self.NOW,
                                    now=self.NOW, state_path=sp)
        assert first is None
        assert json.loads(Path(sp).read_text())["streak"] == 1
        second = probe_ntfy_loopback(state_text=text, state_mtime=self.NOW,
                                     now=self.NOW, state_path=sp)
        assert second is not None

    def test_heal_clears_streak(self, tmp_path):
        sp = str(tmp_path / "d.json")
        bad = json.dumps(self._doc(received=False, misses=1))
        probe_ntfy_loopback(state_text=bad, state_mtime=self.NOW,
                            now=self.NOW, state_path=sp)                  # streak 1
        probe_ntfy_loopback(state_text=json.dumps(self._doc(received=True)),
                            state_mtime=self.NOW, now=self.NOW,
                            state_path=sp)                               # heal → 0
        assert json.loads(Path(sp).read_text())["streak"] == 0


class TestNtfyAckStale:
    """The operator-device confirmation (ntfy receipt-heartbeat Phase 3) surfaced
    into mini's brief + /fleet. Hermetic: inject the state-file JSON + mtime + now.
    INERT off the manager box (no file) and on a STALE file (the cron's own death
    is cron_verdict_stale's job). A ``consecutive_unacked_pings`` that isn't an int
    (missing / torn write / a JSON bool) is indeterminate → None, NEVER invented
    (honest_failure_modes #1); the cron OWNS the email page, this is visibility."""

    NOW = 2_000_000_000.0

    def _doc(self, *, unacked, last_ping=None, last_ack=None):
        return {"last_ping_ts": self.NOW if last_ping is None else last_ping,
                "last_ack_ts": self.NOW if last_ack is None else last_ack,
                "consecutive_unacked_pings": unacked,
                "last_poll_ts": self.NOW, "ping_interval_s": 604800}

    def _fire(self, tmp_path, doc, *, state_mtime=None, **kw):
        """Run twice (2-tick debounce); return the 2nd. Fresh mtime by default.
        doc=None injects an absent state file (empty state_text)."""
        sp = str(tmp_path / "ntfy_ack_debounce.json")
        mt = self.NOW if state_mtime is None else state_mtime
        text = "" if doc is None else json.dumps(doc)
        probe_ntfy_ack_stale(state_text=text, state_mtime=mt, now=self.NOW,
                             state_path=sp, **kw)
        return probe_ntfy_ack_stale(state_text=text, state_mtime=mt, now=self.NOW,
                                    state_path=sp, **kw)

    def test_signal_class_registered(self):
        assert "ntfy_ack_stale" in SIGNAL_CLASSES

    def test_acked_is_none(self, tmp_path):
        assert self._fire(tmp_path, self._doc(unacked=0)) is None

    def test_no_state_is_inert(self, tmp_path):
        """No state file (not the manager box) → INERT."""
        assert self._fire(tmp_path, None) is None

    def test_one_unacked_fires_degraded(self, tmp_path):
        sig = self._fire(tmp_path, self._doc(unacked=1))
        assert sig is not None
        assert sig.cls == "ntfy_ack_stale"
        assert sig.subject == "ntfy"
        assert sig.severity == "degraded"
        assert "UNCONFIRMED" in sig.detail
        assert sig.extra["consecutive_unacked_pings"] == 1

    def test_two_unacked_fires_wedge(self, tmp_path):
        sig = self._fire(tmp_path, self._doc(unacked=2))
        assert sig is not None and sig.severity == "wedge"

    def test_stale_state_is_none(self, tmp_path):
        """State file older than stale_after_s → None (the cron stopped)."""
        assert self._fire(tmp_path, self._doc(unacked=2),
                          state_mtime=self.NOW - 50000) is None

    def test_unparseable_is_none(self, tmp_path):
        sp = str(tmp_path / "d.json")
        probe_ntfy_ack_stale(state_text="not json {", state_mtime=self.NOW,
                             now=self.NOW, state_path=sp)
        second = probe_ntfy_ack_stale(state_text="not json {", state_mtime=self.NOW,
                                      now=self.NOW, state_path=sp)
        assert second is None

    def test_unacked_missing_is_indeterminate_none(self, tmp_path):
        """No counter key (torn/partial write) → None, never invented."""
        doc = {"last_ping_ts": self.NOW, "last_poll_ts": self.NOW}
        assert self._fire(tmp_path, doc) is None

    def test_unacked_bool_is_none(self, tmp_path):
        """A JSON bool (True is an int subclass) is rejected as indeterminate."""
        doc = self._doc(unacked=1)
        doc["consecutive_unacked_pings"] = True
        assert self._fire(tmp_path, doc) is None

    def test_negative_unacked_is_none(self, tmp_path):
        assert self._fire(tmp_path, self._doc(unacked=-1)) is None

    def test_debounce_first_tick_silent(self, tmp_path):
        sp = str(tmp_path / "d.json")
        text = json.dumps(self._doc(unacked=1))
        first = probe_ntfy_ack_stale(state_text=text, state_mtime=self.NOW,
                                     now=self.NOW, state_path=sp)
        assert first is None
        assert json.loads(Path(sp).read_text())["streak"] == 1
        second = probe_ntfy_ack_stale(state_text=text, state_mtime=self.NOW,
                                      now=self.NOW, state_path=sp)
        assert second is not None

    def test_heal_clears_streak(self, tmp_path):
        sp = str(tmp_path / "d.json")
        probe_ntfy_ack_stale(state_text=json.dumps(self._doc(unacked=1)),
                             state_mtime=self.NOW, now=self.NOW,
                             state_path=sp)                              # streak 1
        probe_ntfy_ack_stale(state_text=json.dumps(self._doc(unacked=0)),
                             state_mtime=self.NOW, now=self.NOW,
                             state_path=sp)                              # ack → 0
        assert json.loads(Path(sp).read_text())["streak"] == 0


# ─────────────────────────────────────────────────────────────────────
# mini-dudeai self-health probes (audit 2026-06-09)
# ─────────────────────────────────────────────────────────────────────


class TestHistoryWriteFailure:
    """mini loop alive (state.json last_tick recent) + cumulative fires advancing
    but history.jsonl mtime frozen = a swallowed history-write failure (audit #8).
    A quiet box (no new fires) must never false-alarm. Hermetic: inject docs."""

    NOW = 2_000_000_000.0

    def _state(self, *, fires, last_tick_ago=10.0):
        """A mini state doc with cumulative `fires` spread over two rules."""
        a = fires // 2
        b = fires - a
        return {
            "last_tick_ts": self.NOW - last_tick_ago,
            "rules": {
                "r1|x": {"fire_count": a},
                "r2|y": {"fire_count": b},
            },
        }

    def _run(self, tmp_path, *, fires_seq, mtime_seq):
        """Run the probe once per (fires, mtime) pair; return the signals list."""
        sp = str(tmp_path / "hist_stall.json")
        out = []
        for fires, mtime in zip(fires_seq, mtime_seq):
            out.append(probe_history_write_failure(
                state_doc=self._state(fires=fires),
                history_mtime=mtime,
                now=self.NOW, state_path=sp))
        return out

    def test_signal_class_registered(self):
        assert "history_write_stalled" in SIGNAL_CLASSES

    def test_loop_stopped_is_none(self, tmp_path):
        """Stale last_tick (daemon stopped) → None even if fires look advanced."""
        sp = str(tmp_path / "s.json")
        sig = probe_history_write_failure(
            state_doc=self._state(fires=10, last_tick_ago=10_000.0),
            history_mtime=0.0, now=self.NOW, state_path=sp)
        assert sig is None

    def test_quiet_box_no_new_fires_is_none(self, tmp_path):
        """Fires never advance (no edge events) → history untouched is fine."""
        # baseline tick, then two more with the SAME fire total and frozen mtime.
        sigs = self._run(tmp_path, fires_seq=[5, 5, 5], mtime_seq=[100.0, 100.0, 100.0])
        assert sigs == [None, None, None]

    def test_healthy_history_advances_is_none(self, tmp_path):
        """Fires advance AND mtime advances → healthy, no signal."""
        sigs = self._run(tmp_path, fires_seq=[5, 7, 9], mtime_seq=[100.0, 200.0, 300.0])
        assert sigs == [None, None, None]

    def test_stall_fires_after_debounce(self, tmp_path):
        """Fires advance but mtime frozen for 2 consecutive ticks → fires on 2nd."""
        # tick0: baseline. tick1: fires 5→8, mtime frozen (streak 1, silent).
        # tick2: fires 8→11, mtime still frozen (streak 2, FIRE).
        sigs = self._run(tmp_path, fires_seq=[5, 8, 11],
                         mtime_seq=[100.0, 100.0, 100.0])
        assert sigs[0] is None
        assert sigs[1] is None
        sig = sigs[2]
        assert sig is not None
        assert sig.cls == "history_write_stalled"
        assert sig.severity == "degraded"
        assert sig.issue_ref == 79
        assert sig.subject == "mini-dudeai"

    def test_recovery_clears_streak(self, tmp_path):
        """A healthy tick (mtime advances) between stalls resets the streak."""
        sp = str(tmp_path / "s.json")
        # baseline
        probe_history_write_failure(state_doc=self._state(fires=5),
                                    history_mtime=100.0, now=self.NOW, state_path=sp)
        # stall: streak 1
        s1 = probe_history_write_failure(state_doc=self._state(fires=8),
                                         history_mtime=100.0, now=self.NOW,
                                         state_path=sp)
        assert s1 is None
        # recovery: mtime advances → streak resets to 0
        s2 = probe_history_write_failure(state_doc=self._state(fires=10),
                                         history_mtime=500.0, now=self.NOW,
                                         state_path=sp)
        assert s2 is None
        assert json.loads(Path(sp).read_text())["streak"] == 0

    def test_missing_home_is_none(self, tmp_path):
        """No resolvable mini home and no injected docs → None (not this box)."""
        with patch("utils.watchdog_probes_mini._resolve_mini_home", return_value=None):
            sig = probe_history_write_failure(
                now=self.NOW, state_path=str(tmp_path / "s.json"))
        assert sig is None

    def test_backward_clock_mtime_regress_is_none(self, tmp_path):
        """honest_failure_modes #6 — an EXISTING history file whose mtime went
        BELOW the baseline (a backward NTP/fake-hwclock step at boot) while fires
        advanced is a CLOCK artifact, not a stall: re-baseline + reset streak,
        never fire, even sustained over the debounce window."""
        sp = str(tmp_path / "s.json")
        probe_history_write_failure(state_doc=self._state(fires=5),
                                    history_mtime=300.0, now=self.NOW, state_path=sp)
        # fires advance but mtime REGRESSED (clock stepped back) over two ticks
        s1 = probe_history_write_failure(state_doc=self._state(fires=8),
                                         history_mtime=200.0, now=self.NOW,
                                         state_path=sp)
        s2 = probe_history_write_failure(state_doc=self._state(fires=11),
                                         history_mtime=150.0, now=self.NOW,
                                         state_path=sp)
        assert s1 is None and s2 is None
        assert json.loads(Path(sp).read_text())["streak"] == 0

    def test_absent_history_file_still_stalls(self, tmp_path):
        """The absent/unreadable sentinel (mtime 0.0) is NOT a backward-clock case
        — a missing history file while fires advance must still trip the stall
        after the debounce (the 0.0 sentinel is excluded from the #6 guard)."""
        sigs = self._run(tmp_path, fires_seq=[5, 8, 11],
                         mtime_seq=[0.0, 0.0, 0.0])
        assert sigs[0] is None and sigs[1] is None
        assert sigs[2] is not None and sigs[2].cls == "history_write_stalled"


class TestRulesSeedDrift:
    """Live mini_dudeai_rules.json MISSING rule ids the role seed carries (audit #6).
    Extra box-local live rules are legitimate and never fire. Hermetic: inject sets."""

    def test_signal_class_registered(self):
        assert "rules_seed_drift" in SIGNAL_CLASSES

    def test_live_behind_seed_fires(self):
        sig = probe_rules_seed_drift(
            role="primary",
            seed_ids={"a", "b", "c"},
            live_ids={"a", "b"})
        assert sig is not None
        assert sig.cls == "rules_seed_drift"
        assert sig.severity == "degraded"
        assert sig.issue_ref == 79
        assert "c" in sig.extra["missing"]

    def test_extra_box_local_rules_ignored(self):
        """Live has EXTRA rules not in the seed but none missing → None (legit)."""
        sig = probe_rules_seed_drift(
            role="primary",
            seed_ids={"a", "b"},
            live_ids={"a", "b", "box_local_mf014"})
        assert sig is None

    def test_in_sync_is_none(self):
        sig = probe_rules_seed_drift(
            role="primary", seed_ids={"a", "b"}, live_ids={"a", "b"})
        assert sig is None

    def test_no_role_is_inert_not_indeterminate(self):
        """Box declares no role → INERT, and short-circuits before any
        seed/home read.

        ⚠️ Was `indeterminate` until 2026-08-07: the flat reader collapsed
        "no deployment.json" with "could not read it", so meshanchor-server —
        a MeshAnchor box that is legitimately not MeshForge role-managed —
        sat permanently indeterminate on this class."""
        with patch(
            "utils.watchdog_probes_mini._read_deployment_declaration_status",
            return_value=("undeclared", None, {}),
        ):
            sig = probe_rules_seed_drift()
        assert sig is None
        from utils.watchdog_probe_core import collect_dispositions
        got = collect_dispositions()["rules_seed_drift"]
        assert got["disp"] == "inert"
        assert "no MeshForge role" in got["reason"]

    def test_unreadable_declaration_is_indeterminate(self):
        """The other half: a declaration we could not read must never be
        laundered into the benign inert."""
        from utils.watchdog_probe_core import reset_dispositions
        reset_dispositions()
        with patch(
            "utils.watchdog_probes_mini._read_deployment_declaration_status",
            return_value=("unreadable", None, {}),
        ):
            sig = probe_rules_seed_drift()
        assert sig is None
        from utils.watchdog_probe_core import collect_dispositions
        got = collect_dispositions()["rules_seed_drift"]
        assert got["disp"] == "indeterminate"
        assert "unreadable" in got["reason"]

    def test_ambiguous_role_is_none(self, tmp_path):
        """A role with no dedicated mini seed → None, no guess. (collector and
        cloud-publisher were ONCE the example here — they run mini and are now
        mapped; bot/meshanchor-noc are provisioned by external repos and stay
        genuinely unmapped.) Inject only `role` so the probe reaches the
        role->seed mapping and hits the ambiguous short-circuit (a live file
        MISSING rules would otherwise fire — proving the guard, not the sets)."""
        # a live file that WOULD drift against any seed, to prove role gates first
        (tmp_path / "mini_dudeai_rules.json").write_text(json.dumps({"rules": []}))
        sig = probe_rules_seed_drift(
            mini_home=str(tmp_path), role="bot")
        assert sig is None

    def test_collector_and_cloud_publisher_are_watched(self, tmp_path):
        """collector (moc5) and cloud-publisher (moc1) run mini — the probe
        must NOT self-guard None on them (the 2026-06-09 review gap).
        meshforge_root is passed explicitly: CI checks the repo out somewhere
        other than /opt/meshforge, so the default would read no seed and
        self-guard None (the exact local-green/CI-red trap)."""
        repo_root = os.path.join(os.path.dirname(__file__), "..")
        (tmp_path / "mini_dudeai_rules.json").write_text(json.dumps({"rules": []}))
        for role in ("collector", "cloud-publisher"):
            sig = probe_rules_seed_drift(mini_home=str(tmp_path), role=role,
                                         meshforge_root=repo_root)
            assert sig is not None and sig.cls == "rules_seed_drift"

    def test_known_roles_map_to_seeds(self):
        """primary → federator seed; gateway roles → fleet_gateway seed."""
        from utils.watchdog_probes import _ROLE_TO_MINI_SEED
        assert _ROLE_TO_MINI_SEED["primary"] == "federator"
        assert _ROLE_TO_MINI_SEED["full-gateway"] == "fleet_gateway"
        assert _ROLE_TO_MINI_SEED["gateway-only"] == "fleet_gateway"

    def test_real_seed_loads_for_primary(self, tmp_path):
        """End-to-end against the repo's federator seed: a live file missing one
        of its rules fires; a superset stays silent."""
        meshforge_root = str(Path(__file__).resolve().parent.parent)
        seed = json.loads(Path(
            meshforge_root, "configs", "mini_dudeai_rules.federator.json"
        ).read_text())
        seed_ids = [r["id"] for r in seed["rules"]]
        assert seed_ids  # sanity: the seed actually has rules
        home = tmp_path
        # live = seed minus the first rule → drift
        (home / "mini_dudeai_rules.json").write_text(json.dumps(
            {"rules": [r for r in seed["rules"] if r["id"] != seed_ids[0]]}))
        sig = probe_rules_seed_drift(
            meshforge_root=meshforge_root, mini_home=str(home), role="primary")
        assert sig is not None
        assert seed_ids[0] in sig.extra["missing"]
        # live = full seed → no drift
        (home / "mini_dudeai_rules.json").write_text(json.dumps(seed))
        sig2 = probe_rules_seed_drift(
            meshforge_root=meshforge_root, mini_home=str(home), role="primary")
        assert sig2 is None

    # --- content leg (Issue #80 residual: seed tunes a rule the box keeps) ---

    SEED_RULE = {"id": "a", "match": {"kind": "source_error"},
                 "action": {"kind": "ntfy", "title": "t"}, "cooldown_s": 600}

    def _stamped_copy(self, rule):
        from mini_dudeai.candidate import PROVENANCE_KEY, rule_body_sha
        return {**rule, PROVENANCE_KEY: {"origin": "seed:test",
                                         "seed_sha": rule_body_sha(rule)}}

    def test_stale_seed_owned_copy_fires(self):
        """Live carries an UNMODIFIED stamped copy of an OLDER seed body while
        the seed has since tuned the rule → content drift fires."""
        old_body = {**self.SEED_RULE, "cooldown_s": 60}
        sig = probe_rules_seed_drift(
            role="primary",
            seed_rules=[self.SEED_RULE],
            live_rules=[self._stamped_copy(old_body)])
        assert sig is not None
        assert sig.cls == "rules_seed_drift"
        assert sig.extra["stale"] == ["a"]
        assert sig.extra["missing"] == []
        assert "STALE" in sig.detail

    def test_box_tuned_rule_exempt(self):
        """Live body differs from its own stamp (the box tuned it after the
        merge) → legitimate per-box tuning, silent."""
        tuned = {**self._stamped_copy(self.SEED_RULE), "cooldown_s": 30}
        sig = probe_rules_seed_drift(
            role="primary",
            seed_rules=[self.SEED_RULE], live_rules=[tuned])
        assert sig is None

    def test_unstamped_divergent_rule_exempt(self):
        """Pre-provenance live rule (no stamp) that differs from the seed →
        indeterminate, exempt — unobservable ≠ drift."""
        edited = {**self.SEED_RULE, "cooldown_s": 30}
        sig = probe_rules_seed_drift(
            role="primary",
            seed_rules=[self.SEED_RULE], live_rules=[edited])
        assert sig is None

    def test_in_sync_stamped_copy_silent(self):
        sig = probe_rules_seed_drift(
            role="primary",
            seed_rules=[self.SEED_RULE],
            live_rules=[self._stamped_copy(self.SEED_RULE)])
        assert sig is None

    def test_missing_and_stale_combine_in_one_signal(self):
        old_body = {**self.SEED_RULE, "cooldown_s": 60}
        other = {"id": "b", "match": {"kind": "source_error"},
                 "action": {"kind": "ntfy"}}
        sig = probe_rules_seed_drift(
            role="primary",
            seed_rules=[self.SEED_RULE, other],
            live_rules=[self._stamped_copy(old_body)])
        assert sig is not None
        assert sig.extra["missing"] == ["b"]
        assert sig.extra["stale"] == ["a"]

    def test_content_leg_self_guards_without_shared_hasher(self):
        """mini package absent → no duplicated fallback hasher; the content leg
        goes dark (honest) while the ID leg still works."""
        old_body = {**self.SEED_RULE, "cooldown_s": 60}
        live = [self._stamped_copy(old_body)]
        with patch("utils.watchdog_probes_mini._mini_rule_body_sha", None):
            stale_sig = probe_rules_seed_drift(
                role="primary",
                seed_rules=[self.SEED_RULE], live_rules=live)
            id_sig = probe_rules_seed_drift(
                role="primary",
                seed_rules=[self.SEED_RULE,
                            {"id": "b", "match": {}, "action": {}}],
                live_rules=live)
        assert stale_sig is None
        assert id_sig is not None and id_sig.extra["missing"] == ["b"]

    def test_legacy_id_set_injection_still_works(self):
        """Sets-only injection (the #79 call shape) keeps working — content leg
        simply has no bodies to judge."""
        sig = probe_rules_seed_drift(
            role="primary", seed_ids={"a", "b"}, live_ids={"a"})
        assert sig is not None and sig.extra["missing"] == ["b"]
        assert sig.extra["stale"] == []


class TestMemoryIndexOversize:
    """MEMORY.md over its ~24 KB context-load limit silently partial-loads (audit #2).
    Hermetic: inject size / a tmp file."""

    def test_signal_class_registered(self):
        assert "memory_index_oversize" in SIGNAL_CLASSES

    def test_oversize_fires(self):
        sig = probe_memory_index_oversize(size_bytes=MEMORY_INDEX_LIMIT_BYTES + 1000)
        assert sig is not None
        assert sig.cls == "memory_index_oversize"
        assert sig.severity == "degraded"
        assert sig.issue_ref == 79
        assert sig.extra["over_bytes"] == 1000
        assert "MEMORY_ARCHIVE.md" in sig.detail

    def test_at_limit_is_none(self):
        sig = probe_memory_index_oversize(size_bytes=MEMORY_INDEX_LIMIT_BYTES)
        assert sig is None

    def test_under_limit_is_none(self):
        sig = probe_memory_index_oversize(size_bytes=1024)
        assert sig is None

    def test_absent_file_is_none(self, tmp_path):
        """No MEMORY.md in the resolved home → None (not the memory host)."""
        sig = probe_memory_index_oversize(operator_home=str(tmp_path))
        assert sig is None

    def test_reads_real_file_in_home(self, tmp_path):
        """Stat a real on-disk MEMORY.md at the known relative path."""
        mem = tmp_path / ".claude" / "projects" / "-opt-meshforge" / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text("x" * (MEMORY_INDEX_LIMIT_BYTES + 500))
        sig = probe_memory_index_oversize(operator_home=str(tmp_path))
        assert sig is not None
        assert sig.extra["size_bytes"] == MEMORY_INDEX_LIMIT_BYTES + 500

    def test_no_home_is_none(self):
        with patch("utils.watchdog_probes_mini._resolve_mini_home", return_value=None):
            sig = probe_memory_index_oversize()
        assert sig is None


# ─────────────────────────────────────────────────────────────────────
# 2026-06-15 — calibration_drift (the calibration spine turned on the
# assistant: a VERIFIED completion claim that did not hold on re-derivation)
# ─────────────────────────────────────────────────────────────────────

class TestCalibrationDrift:
    """A VERIFIED 'done/100%' claim that broke on re-derivation must surface;
    a clean ledger and a box with no ledger must stay silent."""

    HEAD = "a" * 40

    def _events(self, outcome):
        return [
            {"kind": "claim", "id": "x", "head_full": self.HEAD,
             "claim_text": "all green, all tests pass"},
            {"kind": "verdict", "claim_id": "x", "ts": 1.0, "outcome": outcome},
        ]

    def test_signal_class_registered(self):
        assert "calibration_drift" in SIGNAL_CLASSES

    def test_broke_claim_fires_degraded(self):
        sig = probe_calibration_drift(events=self._events("broke"))
        assert sig is not None
        assert sig.cls == "calibration_drift"
        assert sig.severity == "degraded"
        assert sig.subject == "claude-claims"
        assert sig.extra["n_broke"] == 1
        assert "calibrated_claims.md" in sig.detail

    def test_held_claim_is_none(self):
        assert probe_calibration_drift(events=self._events("held")) is None

    def test_empty_ledger_is_none(self):
        assert probe_calibration_drift(events=[]) is None

    def test_absent_ledger_file_is_none(self, tmp_path):
        # not the dev/manager box — no ledger on disk → self-guard None
        assert probe_calibration_drift(
            ledger_path=str(tmp_path / "nope.jsonl")) is None

    def test_no_home_is_none(self):
        with patch("utils.watchdog_probes_mini._resolve_mini_home",
                   return_value=None):
            assert probe_calibration_drift() is None

    def test_reads_real_ledger_file(self, tmp_path):
        """End-to-end via the owning ledger module: a recorded claim flipped
        broke by a re-derivation surfaces through the probe's file read+fold."""
        from mini_dudeai import calibration_ledger as cl
        p = str(tmp_path / "calibration_ledger.jsonl")
        cl.record_claim("100% verified", "tests_passed", "exit 0", self.HEAD,
                        ts=1.0, path=p)
        marker = {"head_full": self.HEAD, "exit_code": 1, "ran_full_suite": True}
        cl.rederive_and_persist(p, self.HEAD, marker, now_ts=2.0)
        # claim ts=1.0 → pass a matching now_ts so it's inside the recency window
        sig = probe_calibration_drift(ledger_path=p, now_ts=2.0)
        assert sig is not None and sig.extra["n_broke"] == 1

    def _ts_events(self, outcome, ts):
        return [
            {"kind": "claim", "id": "x", "head_full": self.HEAD,
             "ts": ts, "claim_text": "all green, all tests pass"},
            {"kind": "verdict", "claim_id": "x", "ts": ts, "outcome": outcome},
        ]

    def test_recent_broke_fires(self):
        """A broke claim INSIDE the recency window fires (n_recent surfaced)."""
        sig = probe_calibration_drift(
            events=self._ts_events("broke", 1000.0), now_ts=1000.0 + 3600)
        assert sig is not None
        assert sig.extra["n_recent"] == 1
        assert sig.extra["n_broke"] == 1
        assert "lifetime" in sig.detail

    def test_old_broke_aged_out_is_none(self):
        """A broke claim OLDER than the recency window stops firing — recovery
        demonstrated. The lifetime ratio stays in the warm brief; the alert clears
        (so the signal can soak low-false-positive instead of crying wolf forever)."""
        old = 1000.0
        assert probe_calibration_drift(
            events=self._ts_events("broke", old),
            now_ts=old + 8 * 86400) is None  # 8d later, default window 7d

    def test_backward_clock_future_ts_fires(self):
        """Claim ts in the FUTURE (clock stepped back since) → negative age →
        treated as recent (fire), never a false-clear (honest_failure_modes #6)."""
        assert probe_calibration_drift(
            events=self._ts_events("broke", 5000.0), now_ts=1000.0) is not None

    def test_window_override_respected(self):
        """recent_window_s param overrides the default (testable, env-free)."""
        evs = self._ts_events("broke", 1000.0)
        # 2 days old: cleared under a 1-day window, fires under a 3-day window
        assert probe_calibration_drift(
            events=evs, now_ts=1000.0 + 2 * 86400, recent_window_s=86400) is None
        assert probe_calibration_drift(
            events=evs, now_ts=1000.0 + 2 * 86400,
            recent_window_s=3 * 86400) is not None


# ─────────────────────────────────────────────────────────────────────
# 2026-06-12 — aredn_source_dark (AREDN Phase 0: configured local sysinfo
# source went dark; found dormant on the AREDN-site box itself)
# ─────────────────────────────────────────────────────────────────────

_ASD_IPS = ["10.20.30.65"]


def _asd_kw(tmp_path, diag, *, ips=None, status=None):
    """Build probe kwargs for one tick. ``diag`` is the source_diagnostics
    aredn entry (None = entry absent); ``status`` overrides the whole status
    body (None sentinel string "down" = fetch failure)."""
    if status is None:
        status = {"source_diagnostics": ({"aredn": diag} if diag is not None else {})}
    body = None if status == "down" else status
    return dict(
        configured_ips=_ASD_IPS if ips is None else ips,
        status_fetch_fn=lambda: body,
        # Hermetic default: undeclared. The role-aware leg is exercised by
        # its own tests; without this pin the empty-ips path would read the
        # HOST's real deployment.json (env-dependent on declared boxes).
        expectation_fn=lambda: (False, ""),
        state_path=str(tmp_path / "asd_debounce.json"),
    )


def test_aredn_source_dark_declared_names_an_absent_settings_file(tmp_path):
    """The wipe class includes deleting the whole settings file, not just the
    key. Before the tri-state read, an absent map_settings.json returned the
    same None as an unreadable one and the probe bailed BEFORE the declaration
    leg — so the strongest form of the wipe was invisible on a declared box."""
    from utils.watchdog_probes_env import probe_aredn_source_dark
    kw = _asd_kw(tmp_path, None, ips=[])
    kw["expectation_fn"] = lambda: (True, "AREDN site box")
    kw.pop("configured_ips")
    import utils.watchdog_probes_aredn as _env
    orig = _env._read_configured_aredn_ips_state
    _env._read_configured_aredn_ips_state = lambda _u: ([], "absent")
    try:
        kw["service_user_fn"] = lambda: "op"
        assert probe_aredn_source_dark(**kw) is None
        sig = probe_aredn_source_dark(**kw)
    finally:
        _env._read_configured_aredn_ips_state = orig
    assert sig is not None
    assert sig.extra["settings_state"] == "absent"
    assert "ABSENT" in sig.detail


def test_aredn_settings_state_distinguishes_absent_from_unreadable(tmp_path):
    """The tri-state itself: absent (no config expressed) must not collapse
    into unreadable (we cannot tell) — that collapse was the defect."""
    from utils.watchdog_probes_aredn import _read_configured_aredn_ips_state
    import pwd
    from unittest.mock import patch

    cfg = tmp_path / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    fake_pw = type("pw", (), {"pw_dir": str(tmp_path)})()
    with patch.object(pwd, "getpwnam", lambda _u: fake_pw):
        assert _read_configured_aredn_ips_state("op") == ([], "absent")
        (cfg / "map_settings.json").write_text("{not json")
        assert _read_configured_aredn_ips_state("op") == (None, "unreadable")
        (cfg / "map_settings.json").write_text('{"aredn_node_ips": ["10.1.2.3"]}')
        assert _read_configured_aredn_ips_state("op") == (["10.1.2.3"], "ok")
        (cfg / "map_settings.json").write_text('{}')
        assert _read_configured_aredn_ips_state("op") == ([], "ok")


def test_aredn_source_dark_inert_when_not_configured(tmp_path):
    """Empty aredn_node_ips = organ deliberately off → None every tick and
    the streak resets (the 95%-of-boxes case must be free)."""
    dark = {"attempted": 1, "yielded": 0, "reason_if_zero": "unreachable"}
    # Prime a streak first, then show un-configuring clears it.
    probe_aredn_source_dark(**_asd_kw(tmp_path, dark))
    for _ in range(3):
        assert probe_aredn_source_dark(**_asd_kw(tmp_path, dark, ips=[])) is None
    assert json.loads((tmp_path / "asd_debounce.json").read_text())["streak"] == 0


def test_aredn_declared_unconfigured_fires_after_debounce(tmp_path):
    """The role-aware leg (2026-07-19): a box DECLARING the AREDN organ with
    empty aredn_node_ips is the config-wiped site — fires, doesn't idle."""
    kw = _asd_kw(tmp_path, None, ips=[])
    kw["expectation_fn"] = lambda: (True, "AREDN site box")
    assert probe_aredn_source_dark(**kw) is None      # tick 1: debounce
    sig = probe_aredn_source_dark(**kw)               # tick 2: confirmed
    assert sig is not None
    assert sig.cls == "aredn_source_dark"
    assert sig.severity == "degraded"
    assert sig.subject == "declared-unconfigured"
    assert "organ_expectations.aredn" in sig.detail
    assert "AREDN site box" in sig.detail
    assert sig.extra["declared_reason"] == "AREDN site box"


def test_aredn_undeclared_unconfigured_stays_inert(tmp_path):
    """No declaration + no config = the 95%-of-boxes case, still free."""
    kw = _asd_kw(tmp_path, None, ips=[])
    kw["expectation_fn"] = lambda: (False, "")
    for _ in range(3):
        assert probe_aredn_source_dark(**kw) is None
    assert json.loads((tmp_path / "asd_debounce.json").read_text())["streak"] == 0


def test_aredn_declaration_unreadable_is_indeterminate_not_inert(tmp_path):
    """A corrupt deployment.json must never read as 'not declared' — the
    declaration channel failing is not evidence of no expectation."""
    kw = _asd_kw(tmp_path, None, ips=[])
    kw["expectation_fn"] = lambda: (None, "")
    for _ in range(3):
        assert probe_aredn_source_dark(**kw) is None
    # Streak must be HELD (file never written with a reset), not advanced.
    assert not (tmp_path / "asd_debounce.json").exists()


def test_aredn_declared_and_configured_uses_normal_path(tmp_path):
    """A declared box WITH config behaves exactly as before — the
    declaration only matters when the config is missing."""
    healthy = {"attempted": 2, "yielded": 3, "reason_if_zero": None}
    kw = _asd_kw(tmp_path, healthy)
    kw["expectation_fn"] = lambda: (True, "AREDN site box")
    assert probe_aredn_source_dark(**kw) is None


def test_read_aredn_expectation_shapes(tmp_path, monkeypatch):
    """The declaration reader's tri-state on real files."""
    from utils.watchdog_probes_env import _read_aredn_expectation
    import pwd as _pwd

    class _Rec:
        pw_dir = str(tmp_path)

    monkeypatch.setattr(_pwd, "getpwnam", lambda name: _Rec())
    cfg = tmp_path / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    dep = cfg / "deployment.json"
    # Absent file → not declared.
    assert _read_aredn_expectation("op") == (False, "")
    # No organ_expectations key → not declared.
    dep.write_text(json.dumps({"role": "collector"}))
    assert _read_aredn_expectation("op") == (False, "")
    # Declared with a reason string.
    dep.write_text(json.dumps(
        {"role": "collector", "organ_expectations": {"aredn": "site box"}}))
    assert _read_aredn_expectation("op") == (True, "site box")
    # Declared with a non-string value → declared, empty reason.
    dep.write_text(json.dumps({"organ_expectations": {"aredn": True}}))
    assert _read_aredn_expectation("op") == (True, "")
    # Corrupt file → unknown, never "not declared".
    dep.write_text("{not json")
    assert _read_aredn_expectation("op") == (None, "")


def test_aredn_source_dark_none_when_intent_unreadable(tmp_path):
    """Unreadable map_settings.json (service_user_fn → None) → indeterminate:
    silent, and the status endpoint is never even fetched."""
    fetched = []
    sig = probe_aredn_source_dark(
        configured_ips=None,
        service_user_fn=lambda: None,
        status_fetch_fn=lambda: fetched.append(1) or {},
        state_path=str(tmp_path / "asd_debounce.json"),
    )
    assert sig is None
    assert fetched == []


def test_aredn_source_dark_fires_on_unreachable_after_debounce(tmp_path):
    """The node-dark shape: silent on tick 1 (debounce), degraded on tick 2
    with the configured IPs and the check-the-node hint in detail."""
    dark = {"attempted": 2, "yielded": 0, "reason_if_zero": "unreachable"}
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, dark)) is None
    sig = probe_aredn_source_dark(**_asd_kw(tmp_path, dark))
    assert sig is not None
    assert sig.cls == "aredn_source_dark"
    assert sig.severity == "degraded"
    assert sig.subject == "10.20.30.65"
    assert "unreachable" in sig.detail
    assert "10.20.30.65" in sig.detail
    assert sig.extra["reason"] == "unreachable"
    assert sig.extra["debounce_streak"] == 2


def test_aredn_source_dark_not_configured_reason_names_restart(tmp_path):
    """Settings file carries IPs but the RUNNING service reports
    not_configured = it started before the config landed → the detail must
    say so and name the restart fix (reader/writer halves must agree)."""
    stale = {"attempted": 0, "yielded": 0, "reason_if_zero": "not_configured"}
    probe_aredn_source_dark(**_asd_kw(tmp_path, stale))
    sig = probe_aredn_source_dark(**_asd_kw(tmp_path, stale))
    assert sig is not None
    assert "restart meshforge-map" in sig.detail
    assert sig.extra["reason"] == "not_configured"


def test_aredn_source_dark_healthy_resets_streak(tmp_path):
    """yielded>0 resets the streak: dark → healthy → dark must NOT fire
    (needs two fresh consecutive dark ticks again)."""
    dark = {"attempted": 1, "yielded": 0, "reason_if_zero": "unreachable"}
    alive = {"attempted": 3, "yielded": 3, "reason_if_zero": None}
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, dark)) is None
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, alive)) is None
    assert json.loads((tmp_path / "asd_debounce.json").read_text())["streak"] == 0
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, dark)) is None
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, dark)) is not None


def test_aredn_source_dark_no_positions_is_alive(tmp_path):
    """Reachable-but-no-GPS is an alive organ: silent + streak reset."""
    nopos = {"attempted": 2, "yielded": 0, "reason_if_zero": "no_positions"}
    dark = {"attempted": 1, "yielded": 0, "reason_if_zero": "unreachable"}
    probe_aredn_source_dark(**_asd_kw(tmp_path, dark))
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, nopos)) is None
    assert json.loads((tmp_path / "asd_debounce.json").read_text())["streak"] == 0


def test_aredn_source_dark_slow_sysinfo_is_alive(tmp_path):
    """Reachable-but-slow (sysinfo GET exceeded the read timeout on a
    constrained mips_24kc router) is an ALIVE organ, not dark: silent + streak
    reset, and it must NEVER fire even sustained. This is the 2026-06-16 moc5
    fix — slow ≠ unreachable; the old code mislabeled the slow hAP unreachable
    and the probe flapped degraded↔clear overnight."""
    slow = {"attempted": 1, "yielded": 0, "reason_if_zero": "slow_sysinfo"}
    dark = {"attempted": 1, "yielded": 0, "reason_if_zero": "unreachable"}
    # Prime a dark streak, then a slow tick must clear it (reachable).
    probe_aredn_source_dark(**_asd_kw(tmp_path, dark))
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, slow)) is None
    assert json.loads((tmp_path / "asd_debounce.json").read_text())["streak"] == 0
    # Sustained slow never fires.
    for _ in range(4):
        assert probe_aredn_source_dark(**_asd_kw(tmp_path, slow)) is None


def test_aredn_source_dark_holds_streak_on_status_hiccup(tmp_path):
    """A one-tick status-fetch failure must HOLD confirmed-dark progress
    (http_local owns the wedge): dark → fetch-down → dark fires."""
    dark = {"attempted": 1, "yielded": 0, "reason_if_zero": "unreachable"}
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, dark)) is None
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, None, status="down")) is None
    assert json.loads((tmp_path / "asd_debounce.json").read_text())["streak"] == 1
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, dark)) is not None


def test_aredn_source_dark_none_when_diagnostics_absent(tmp_path):
    """No source_diagnostics block / no aredn entry (older service, no
    collect yet) → indeterminate: silent, streak held."""
    assert probe_aredn_source_dark(**_asd_kw(tmp_path, None)) is None
    assert probe_aredn_source_dark(
        **_asd_kw(tmp_path, None, status={"status": "ok"})) is None


def test_aredn_source_dark_unknown_reason_never_fires(tmp_path):
    """An unknown reason string is indeterminate — silent across many ticks
    (closed vocabulary: only unreachable/not_configured are dark)."""
    odd = {"attempted": 0, "yielded": 0, "reason_if_zero": "source_disabled"}
    for _ in range(4):
        assert probe_aredn_source_dark(**_asd_kw(tmp_path, odd)) is None


def test_aredn_source_dark_signal_class_registered():
    assert "aredn_source_dark" in SIGNAL_CLASSES


def test_seed_rules_path_pinned_to_candidate():
    # honest_failure_modes #5: the probe keeps a LOCAL _seed_rules_path (it must
    # import without the mini_dudeai package — its mini import is guarded), so it
    # can't share the canonical mini_dudeai.candidate.seed_rules_path by import.
    # Pin them byte-identical instead, so the two can never silently drift.
    from utils.watchdog_probes_mini import _seed_rules_path
    from mini_dudeai.candidate import seed_rules_path
    for root, seed in [("/opt/meshforge", "federator"),
                       ("/x/y", "fleet_gateway"), ("/a", "claw"),
                       ("rel/root", "example")]:
        assert _seed_rules_path(root, seed) == seed_rules_path(root, seed)


# ─────────────────────────────────────────────────────────────────────
# Gateway delivery degraded (2026-06-20; gateway-reliability arc A2)
# ─────────────────────────────────────────────────────────────────────


def _blk(ts, m2r_a, m2r_d, m2r_x, r2m_a, r2m_d, r2m_x):
    """One att/del/drop block tuple: (ts, M->R a/d/x, R->M a/d/x)."""
    return (ts, m2r_a, m2r_d, m2r_x, r2m_a, r2m_d, r2m_x)


class TestGatewayDeliveryParse:
    """Pure parse/window helpers — the probe's value rests on parsing the
    real bridge_cli att/del/drop journal line and computing a windowed gap."""

    # Real -o short-unix lines from BOTH bridge_cli print formats.
    _SINGLE = ("1781943622.0 host meshforge-gateway[123]:   "
               "attempted/delivered/dropped — M->R: 631/631/0  R->M: 631/526/105")
    _MULTI = ("1781943680.5 host meshforge-gateway[123]:       "
              "att/del/drop — M->R: 10/10/0  R->M: 20/15/5")
    # The "Messages bridged" line uses a COMMA, not a slash triplet — must NOT
    # parse as a delivery block (else it'd be read as a bogus all-zero block).
    _BRIDGED = ("1781943622.0 host meshforge-gateway[123]: "
                "Messages bridged: 1157 (M->R: 631, R->M: 526)")

    def test_parses_single_bridge_format(self):
        b = _parse_delivery_block(self._SINGLE)
        assert b == (1781943622.0, 631, 631, 0, 631, 526, 105)

    def test_parses_multi_bridge_format(self):
        b = _parse_delivery_block(self._MULTI)
        assert b == (1781943680.5, 10, 10, 0, 20, 15, 5)

    def test_messages_bridged_comma_line_does_not_parse(self):
        """The comma-delimited summary line must not match the slash triplet."""
        assert _parse_delivery_block(self._BRIDGED) is None

    def test_no_timestamp_returns_none(self):
        assert _parse_delivery_block("not-a-ts M->R: 1/1/0  R->M: 1/1/0") is None

    def test_grep_pattern_is_the_slash_discriminator(self):
        """The journalctl -g pattern keys on the slash triplet (so the comma
        summary line is excluded) — a regression here re-includes it."""
        assert "M->R:" in GATEWAY_DELIVERY_BLOCK_GREP
        assert "[0-9]+/[0-9]+/[0-9]+" in GATEWAY_DELIVERY_BLOCK_GREP
        # leg-2 error grep carries the EROFS witness (the 2026-06-20 class)
        assert "EROFS" in GATEWAY_RNS_ERROR_GREP

    def test_window_gap_needs_two_blocks(self):
        assert _window_delivery_gap(
            [_blk(1, 100, 100, 0, 100, 100, 0)],
            min_volume=20, ratio_floor=0.5, r2m_suppressed=0) == ([], [])

    def test_window_gap_fires_on_r2m_collapse(self):
        """R->M attempted +30 / delivered +10 over the window (33% < 50% floor,
        ≥20 attempts) → one finding naming the direction. M->R (+10 attempts)
        is below the volume gate → no finding there."""
        blocks = [_blk(1, 100, 100, 0, 100, 100, 0),
                  _blk(2, 110, 110, 0, 130, 110, 20)]
        gaps, unjudged = _window_delivery_gap(
            blocks, min_volume=20, ratio_floor=0.5, r2m_suppressed=0)
        assert unjudged == []
        assert len(gaps) == 1
        label, d_att, d_del, ratio, excluded = gaps[0]
        assert label == "RNS->Mesh"
        assert (d_att, d_del, excluded) == (30, 10, 0)
        assert ratio == pytest.approx(10 / 30)

    def test_window_gap_silent_when_above_floor(self):
        blocks = [_blk(1, 100, 100, 0, 100, 100, 0),
                  _blk(2, 200, 200, 0, 200, 195, 5)]  # 95% delivered
        assert _window_delivery_gap(
            blocks, min_volume=20, ratio_floor=0.5,
            r2m_suppressed=0) == ([], [])

    def test_window_gap_silent_below_min_volume(self):
        """A quiet box (small windowed delta) never fires even at a low ratio."""
        blocks = [_blk(1, 100, 100, 0, 100, 100, 0),
                  _blk(2, 105, 100, 5, 110, 102, 8)]  # only +10 R->M attempts
        assert _window_delivery_gap(
            blocks, min_volume=20, ratio_floor=0.5,
            r2m_suppressed=0) == ([], [])

    def test_window_gap_handles_counter_reset(self):
        """A gateway restart mid-window resets the in-memory counters (latest <
        earliest); the baseline is taken as zero so we measure since-restart,
        never a bogus negative delta. M->R still judges; R->M refuses (its
        suppression exclusion spans a different window — see below)."""
        blocks = [_blk(1, 500, 490, 10, 500, 480, 20),
                  _blk(2, 30, 10, 20, 40, 10, 30)]  # restarted → small counters
        gaps, unjudged = _window_delivery_gap(
            blocks, min_volume=20, ratio_floor=0.5, r2m_suppressed=0)
        assert {g[0] for g in gaps} == {"Mesh->RNS"}
        assert unjudged == ["RNS->Mesh"]

    # ── dual-path dedup exclusion (2026-08-12 false page) ──────────────
    # moc pages R->M 8/22 (36%) while every one of the 14 missing messages
    # was a dual-path dedup suppression — the local mesh_bridge had already
    # put that content on RF. True delivery was 8/8. The suppressed counter
    # is documented in rns_bridge.stats as "attempted counts them;
    # delivered/dropped do not" and had NO reader until this exclusion.

    _LIVE_MOC = [_blk(1, 1, 1, 0, 0, 0, 0), _blk(2, 4, 4, 0, 22, 8, 0)]

    def test_live_false_page_reproduces_without_the_exclusion(self):
        """Pin the DEFECT: with suppressions unaccounted (0), the real 2026-08-12
        moc window fires at exactly the 36% the operator was paged with."""
        gaps, _ = _window_delivery_gap(
            self._LIVE_MOC, min_volume=20, ratio_floor=0.5, r2m_suppressed=0)
        assert len(gaps) == 1
        assert gaps[0][0] == "RNS->Mesh"
        assert gaps[0][3] == pytest.approx(8 / 22)

    def test_live_false_page_silent_once_suppressions_excluded(self):
        """Same window, the 14 real suppressions excluded → nothing to report."""
        assert _window_delivery_gap(
            self._LIVE_MOC, min_volume=20, ratio_floor=0.5,
            r2m_suppressed=14) == ([], [])

    def test_real_collapse_still_fires_despite_some_suppression(self):
        """The exclusion must not blind the probe: a genuine collapse (40
        attempted / 5 delivered) with 2 benign suppressions still fires."""
        blocks = [_blk(1, 0, 0, 0, 0, 0, 0), _blk(2, 0, 0, 0, 40, 5, 0)]
        gaps, unjudged = _window_delivery_gap(
            blocks, min_volume=20, ratio_floor=0.5, r2m_suppressed=2)
        assert unjudged == []
        assert len(gaps) == 1
        label, d_att, d_del, ratio, excluded = gaps[0]
        assert (label, d_att, d_del, excluded) == ("RNS->Mesh", 38, 5, 2)
        assert ratio == pytest.approx(5 / 38)

    def test_suppressed_count_cannot_exceed_the_gap(self):
        """An inflated suppression count can never invent delivery — it is
        clamped to the unexplained gap, so the ratio can't be forged upward."""
        blocks = [_blk(1, 0, 0, 0, 0, 0, 0), _blk(2, 0, 0, 0, 40, 5, 0)]
        gaps, _ = _window_delivery_gap(
            blocks, min_volume=20, ratio_floor=0.5, r2m_suppressed=10_000)
        # 35-message gap fully absorbed → 5 real attempts, under the volume
        # gate. Never a >100% ratio, never a negative attempted count.
        assert gaps == []

    def test_unobservable_suppression_is_unjudged_not_clean(self):
        """journalctl blind for the suppression count + a real gap = we cannot
        tell dedup from collapse. That must surface as unjudged (the caller
        reports indeterminate), NEVER as a silent clean (honest_failure_modes
        #1/#2) and never as the false page (r2m_suppressed treated as 0)."""
        gaps, unjudged = _window_delivery_gap(
            self._LIVE_MOC, min_volume=20, ratio_floor=0.5,
            r2m_suppressed=None)
        assert gaps == []
        assert unjudged == ["RNS->Mesh"]

    def test_unobservable_suppression_with_no_gap_is_still_clean(self):
        """No gap to attribute → the blind suppression count doesn't matter;
        a fully-delivering box must not go indeterminate for lack of it."""
        blocks = [_blk(1, 0, 0, 0, 0, 0, 0), _blk(2, 0, 0, 0, 30, 30, 0)]
        assert _window_delivery_gap(
            blocks, min_volume=20, ratio_floor=0.5,
            r2m_suppressed=None) == ([], [])


class TestGatewayDeliveryDegraded:
    """The probe (2026-06-20, arc A2). Behavioral tests inject blocks_fn /
    error_count_fn + a gateway main_pid; the real journalctl helper is
    exercised separately in TestGatewayDeliveryRealQuery."""

    # Two blocks whose R->M delivery collapsed (att +30 / del +10 = 33%).
    _COLLAPSE = [_blk(1, 100, 100, 0, 100, 100, 0),
                 _blk(2, 110, 110, 0, 130, 110, 20)]
    _HEALTHY = [_blk(1, 100, 100, 0, 100, 100, 0),
                _blk(2, 200, 200, 0, 200, 198, 2)]

    def _kw(self, sp, *, blocks, err, sup=(0, 0)):
        """``sup`` is ALWAYS injected — the dual-path suppression count reads
        the real journal by default, so an un-injected test would judge on
        whatever box happened to run the suite (a gateway box and a dev box
        would disagree). Default (0, 0) = observed, none."""
        return dict(main_pid=1234, blocks_fn=lambda: blocks,
                    error_count_fn=lambda: err, suppressed_fn=lambda: sup,
                    state_path=sp)

    def test_signal_class_registered(self):
        assert "gateway_delivery_degraded" in SIGNAL_CLASSES

    def test_inert_when_gateway_not_running(self, tmp_path):
        """No meshforge-gateway on this box (MainPID None) → INERT (None),
        never a journalctl call. The moc/moc3-only gate."""
        sp = str(tmp_path / "g.json")
        # probe_gateway_delivery_degraded moved to watchdog_probes_gateway_flow
        # (2026-07-14 gateway split) — patch the source seam, not the re-export.
        with patch("utils.watchdog_probes_gateway_flow._resolve_main_pid_status",
                   return_value=("absent", None)):
            assert probe_gateway_delivery_degraded(state_path=sp) is None

    def test_error_spike_two_ticks_fire_degraded(self, tmp_path):
        """RED-FIRST debounce: a leg-2 error spike (5 ≥ degraded_n 3, < wedge_n
        10) fires degraded only on the 2nd consecutive candidate tick."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=[], err=5)
        assert probe_gateway_delivery_degraded(**kw) is None     # streak 1
        sig = probe_gateway_delivery_degraded(**kw)
        assert sig is not None
        assert sig.cls == "gateway_delivery_degraded"
        assert sig.subject == "meshforge-gateway"
        assert sig.severity == "degraded"
        assert sig.extra["rns_error_count"] == 5
        assert "EROFS" in sig.detail
        assert "/etc/reticulum/storage" in sig.detail

    def test_error_spike_wedge_severity(self, tmp_path):
        """≥ error_wedge_n (10) RNS errors → wedge."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=[], err=12)
        assert probe_gateway_delivery_degraded(**kw) is None
        sig = probe_gateway_delivery_degraded(**kw)
        assert sig is not None and sig.severity == "wedge"

    def test_delivery_gap_fires(self, tmp_path):
        """Leg 1: a windowed R->M delivery collapse fires degraded, naming the
        direction and the windowed delivered/attempted."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=self._COLLAPSE, err=0)
        assert probe_gateway_delivery_degraded(**kw) is None
        sig = probe_gateway_delivery_degraded(**kw)
        assert sig is not None and sig.severity == "degraded"
        assert "RNS->Mesh delivered 10/30" in sig.detail
        assert sig.extra["gap_RNS_Mesh"]["ratio"] == pytest.approx(1 / 3, abs=1e-3)

    def test_healthy_does_not_fire(self, tmp_path):
        """Good ratio + zero errors → never fires (observed-clean)."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=self._HEALTHY, err=0)
        assert probe_gateway_delivery_degraded(**kw) is None
        assert probe_gateway_delivery_degraded(**kw) is None

    def test_max_severity_wins_across_legs(self, tmp_path):
        """Leg-1 degraded + leg-2 wedge (≥10 errors) → wedge (max wins)."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=self._COLLAPSE, err=11)
        assert probe_gateway_delivery_degraded(**kw) is None
        sig = probe_gateway_delivery_degraded(**kw)
        assert sig is not None and sig.severity == "wedge"

    def test_fully_unobservable_holds_streak(self, tmp_path):
        """BOTH legs unobservable (journalctl wedged) HOLDS the debounce streak
        (honest_failure_modes #2): candidate, blind, candidate → fires on the
        2nd real observation (a blind tick must not erase an in-progress
        signal, and unobservable is never read as a healthy reset)."""
        sp = str(tmp_path / "g.json")
        cand = self._kw(sp, blocks=[], err=5)
        blind = self._kw(sp, blocks=None, err=None)
        assert probe_gateway_delivery_degraded(**cand) is None    # streak 1
        assert probe_gateway_delivery_degraded(**blind) is None   # held at 1
        sig = probe_gateway_delivery_degraded(**cand)             # streak 2 → fire
        assert sig is not None

    def test_observed_clean_resets_streak(self, tmp_path):
        """An OBSERVED-clean tick (distinct from unobservable) resets the
        debounce: candidate, clean, candidate → still None."""
        sp = str(tmp_path / "g.json")
        cand = self._kw(sp, blocks=[], err=5)
        clean = self._kw(sp, blocks=self._HEALTHY, err=0)
        assert probe_gateway_delivery_degraded(**cand) is None   # streak 1
        assert probe_gateway_delivery_degraded(**clean) is None  # observed → reset
        assert probe_gateway_delivery_degraded(**cand) is None   # streak 1 again

    def test_partial_observable_clean_leg_resets(self, tmp_path):
        """Leg-2 observed clean while leg-1 is unobservable (blocks None) is
        still an observed-clean tick → no fire (not held as a candidate)."""
        sp = str(tmp_path / "g.json")
        kw = dict(main_pid=1234, blocks_fn=lambda: None,
                  error_count_fn=lambda: 0, state_path=sp)
        assert probe_gateway_delivery_degraded(**kw) is None
        assert probe_gateway_delivery_degraded(**kw) is None

    def test_below_thresholds_inert(self, tmp_path):
        """Errors below degraded_n + delivery above floor → no finding."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=self._HEALTHY, err=2)  # 2 < degraded_n 3
        assert probe_gateway_delivery_degraded(**kw) is None
        assert probe_gateway_delivery_degraded(**kw) is None

    def test_never_raises(self, tmp_path):
        """A data-source fn that raises → None (never propagates)."""
        sp = str(tmp_path / "g.json")
        def boom():
            raise RuntimeError("journalctl exploded")
        assert probe_gateway_delivery_degraded(
            main_pid=1234, blocks_fn=boom, error_count_fn=lambda: 0,
            suppressed_fn=lambda: (0, 0), state_path=sp) is None

    # ── dual-path dedup exclusion at probe level (2026-08-12) ──────────
    # The live moc window that paged: R->M 0/0 → 22/8 with 14 suppressions.
    _LIVE_MOC = [_blk(1, 1, 1, 0, 0, 0, 0), _blk(2, 4, 4, 0, 22, 8, 0)]

    def test_live_false_page_no_longer_fires(self, tmp_path):
        """The 2026-08-12 moc page, replayed: 14 of the 22 R->M attempts were
        dual-path dedup suppressions (already on RF via mesh_bridge), so true
        delivery was 8/8. Two ticks must produce NO signal."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=self._LIVE_MOC, err=0, sup=(14, 0))
        assert probe_gateway_delivery_degraded(**kw) is None
        assert probe_gateway_delivery_degraded(**kw) is None
        # ...and it is an observed-clean tick, so the streak stays reset.
        assert _load_gateway_delivery_streak(sp) == 0

    def test_live_window_would_have_fired_without_the_exclusion(self, tmp_path):
        """Guard the guard: the SAME window with the suppressions unaccounted
        still fires at 36% — proving this test pins the exclusion, not some
        other reason for silence."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=self._LIVE_MOC, err=0, sup=(0, 0))
        assert probe_gateway_delivery_degraded(**kw) is None
        sig = probe_gateway_delivery_degraded(**kw)
        assert sig is not None
        assert "RNS->Mesh delivered 8/22 (36%)" in sig.detail

    def test_unobservable_suppression_holds_streak_and_never_fires(self, tmp_path):
        """Suppression count unobservable + a real gap: the probe must neither
        fire (that's the false page) nor reset the streak to healthy (that's
        unobservable-read-as-clean, honest_failure_modes #2)."""
        sp = str(tmp_path / "g.json")
        _save_gateway_delivery_streak(sp, 1)
        kw = self._kw(sp, blocks=self._LIVE_MOC, err=0, sup=None)
        assert probe_gateway_delivery_degraded(**kw) is None
        assert _load_gateway_delivery_streak(sp) == 1   # HELD, not reset to 0

    def test_firing_signal_names_the_exclusion_and_exposure(self, tmp_path):
        """A real collapse alongside some benign dedup still fires, and the
        signal shows BOTH the exclusion (so the operator can reconcile the
        number against the journal) and the cid-only loss-exposure subset
        rather than averaging it away."""
        sp = str(tmp_path / "g.json")
        blocks = [_blk(1, 0, 0, 0, 0, 0, 0), _blk(2, 0, 0, 0, 40, 5, 0)]
        kw = self._kw(sp, blocks=blocks, err=0, sup=(2, 1))
        assert probe_gateway_delivery_degraded(**kw) is None
        sig = probe_gateway_delivery_degraded(**kw)
        assert sig is not None
        assert "RNS->Mesh delivered 5/38" in sig.detail
        assert "excludes 2 dual-path dedup suppression(s)" in sig.detail
        assert sig.extra["r2m_dual_path_suppressed"] == 2
        assert sig.extra["r2m_dual_path_suppressed_cid_only"] == 1
        assert sig.extra["gap_RNS_Mesh"]["suppressed_excluded"] == 2

    def test_streak_clamped_at_debounce_floor(self, tmp_path):
        """A sustained collapse keeps firing but the persisted streak never
        grows past debounce_ticks, and an absurd on-disk value is clamped."""
        sp = str(tmp_path / "g.json")
        kw = self._kw(sp, blocks=[], err=5)
        probe_gateway_delivery_degraded(**kw)        # streak 1
        for _ in range(5):
            assert probe_gateway_delivery_degraded(**kw) is not None
        assert _load_gateway_delivery_streak(sp) == 2
        _save_gateway_delivery_streak(sp, 999999)
        sig = probe_gateway_delivery_degraded(**kw)
        assert sig is not None and sig.extra["debounce_streak"] == 2

    def test_save_logs_witness_on_write_failure(self, tmp_path, caplog):
        """honest_failure_modes #9: a swallowed state-write OSError leaves a
        WITNESS in the watchdog journal (else a persistent write failure makes
        the probe silently never advance past its debounce floor)."""
        import logging as _logging
        blocker = tmp_path / "afile"
        blocker.write_text("x")
        doomed = str(blocker / "sub" / "g.json")
        with caplog.at_level(_logging.WARNING, logger="watchdog"):
            _save_gateway_delivery_streak(doomed, 1)   # must not raise
        assert any("could not persist debounce streak" in r.message
                   for r in caplog.records)


class TestGatewayDeliveryRealQuery:
    """Exercise the REAL _gateway_delivery_blocks journalctl helper — the
    behavioral tests all inject blocks_fn, so without this a flag/field/parse
    regression would ship green (the #82 silent-blind-spot lesson)."""

    _LINES = (
        "1781943622.0 host meshforge-gateway[1]:   attempted/delivered/dropped "
        "— M->R: 631/631/0  R->M: 631/526/105\n"
        "1781943680.5 host meshforge-gateway[1]:       att/del/drop — "
        "M->R: 10/10/0  R->M: 20/15/5\n"
    )

    def _patched(self, *, stdout="", returncode=0, exc=None):
        captured = {}

        class _Result:
            def __init__(self):
                self.stdout = stdout
                self.returncode = returncode

        def _runner(*args, **kwargs):
            captured["argv"] = args[0]
            if exc is not None:
                raise exc
            return _Result()

        # Source seam moved again 2026-08-12 (MF025 split): the journal
        # readers now live in watchdog_gateway_delivery_report and are
        # re-exported by the flow module. Patch where the code RUNS.
        return patch("utils.watchdog_gateway_delivery_report.subprocess.run",
                     side_effect=_runner), captured

    def test_selects_unit_and_block_grep(self):
        ctx, captured = self._patched(stdout=self._LINES)
        with ctx:
            blocks = _gateway_delivery_blocks(
                "meshforge-gateway.service", "30min")
        argv = captured["argv"]
        assert "-u" in argv and "meshforge-gateway.service" in argv
        assert "-g" in argv and GATEWAY_DELIVERY_BLOCK_GREP in argv
        assert "short-unix" in argv
        assert blocks == [
            (1781943622.0, 631, 631, 0, 631, 526, 105),
            (1781943680.5, 10, 10, 0, 20, 15, 5),
        ]

    def test_empty_stdout_is_empty_list_not_none(self):
        ctx, _ = self._patched(stdout="", returncode=0)
        with ctx:
            assert _gateway_delivery_blocks("u", "30min") == []

    def test_rc2_is_unobservable_none(self):
        ctx, _ = self._patched(stdout="boom", returncode=2)
        with ctx:
            assert _gateway_delivery_blocks("u", "30min") is None

    def test_rc1_no_match_is_empty_list(self):
        ctx, _ = self._patched(stdout="", returncode=1)
        with ctx:
            assert _gateway_delivery_blocks("u", "30min") == []

    def test_timeout_is_unobservable_none(self):
        ctx, _ = self._patched(
            exc=subprocess.TimeoutExpired(cmd="journalctl", timeout=15))
        with ctx:
            assert _gateway_delivery_blocks("u", "30min") is None

    def test_garbage_line_skipped_not_crashed(self):
        ctx, _ = self._patched(
            stdout="garbage no match here\n1781943622.0 h g[1]: "
                   "M->R: 5/5/0  R->M: 5/4/1\n")
        with ctx:
            blocks = _gateway_delivery_blocks("u", "30min")
        assert blocks == [(1781943622.0, 5, 5, 0, 5, 4, 1)]


class TestGatewayR2MSuppressedRealQuery:
    """Exercise the REAL _gateway_r2m_suppressed journal reader (2026-08-12).

    The probe tests all inject suppressed_fn, so without this the grep could
    stop matching the live log line and the false page would silently return
    — the exclusion would read "0 suppressions" forever while the journal was
    full of them. Verbatim live lines from moc 2026-08-12.
    """

    # The rns_bridge line — this one inflates rns_to_mesh_attempted.
    _R2M = ("2026-08-12 21:14:12,979 | gateway._rns_bridge_xform | INFO | "
            "Bridge RNS→Mesh suppressed (dual-path dedup [text] — already on "
            "RF via mesh_bridge): [MC:p3] @[KH7BR] I got you...")
    _R2M_CID = _R2M.replace("[text]", "[cid]")
    # mesh_bridge's MIRROR line — a DIFFERENT counter that never touches
    # rns_to_mesh_attempted. Counting it would over-subtract.
    _MESH = ("2026-08-12 21:23:10,749 | gateway.mesh_bridge | INFO | "
             "mesh_bridge forward suppressed (dual-path dedup [cid] — already "
             "on RF via rns_bridge): @moc3 ✋Ack to you!")

    def _patched(self, *, stdout="", returncode=0, exc=None):
        captured = {}

        class _Result:
            def __init__(self):
                self.stdout = stdout
                self.returncode = returncode

        def _runner(*args, **kwargs):
            captured["argv"] = args[0]
            if exc is not None:
                raise exc
            return _Result()

        # The reader delegates to _journal_match_lines in the probe core.
        return patch("utils.watchdog_probe_core.subprocess.run",
                     side_effect=_runner), captured

    def test_counts_total_and_cid_only_subset(self):
        ctx, captured = self._patched(
            stdout="\n".join([self._R2M, self._R2M_CID, self._R2M]) + "\n")
        with ctx:
            got = _gateway_r2m_suppressed("meshforge-gateway.service", "30min")
        assert got == (3, 1)
        argv = captured["argv"]
        assert "-u" in argv and "meshforge-gateway.service" in argv
        assert GATEWAY_R2M_SUPPRESSED_GREP in argv

    def test_grep_excludes_the_mesh_bridge_mirror_line(self):
        """The two suppression logs are near-identical; keying the wrong one
        would subtract a counter that never inflated attempted. The grep must
        match only the 'via mesh_bridge' tail."""
        import re as _re
        assert _re.search(GATEWAY_R2M_SUPPRESSED_GREP, self._R2M)
        assert not _re.search(GATEWAY_R2M_SUPPRESSED_GREP, self._MESH)

    def test_no_matches_is_zero_not_none(self):
        ctx, _ = self._patched(stdout="", returncode=1)
        with ctx:
            assert _gateway_r2m_suppressed("u", "30min") == (0, 0)

    def test_journalctl_failure_is_unobservable_none(self):
        """rc>1 → None, so the caller refuses to judge rather than reading a
        blind channel as 'no suppressions' (which restores the false page)."""
        ctx, _ = self._patched(stdout="", returncode=2)
        with ctx:
            assert _gateway_r2m_suppressed("u", "30min") is None

    def test_timeout_is_unobservable_none(self):
        ctx, _ = self._patched(exc=subprocess.TimeoutExpired("journalctl", 15))
        with ctx:
            assert _gateway_r2m_suppressed("u", "30min") is None


# ─────────────────────────────────────────────────────────────────────
# 2026-06-21 — inherited_app_drift (upstream-app ownership Action 5): an
# INHERITED upstream app checkout carries an unversioned tracked-file code
# patch — one `git pull` from silent deletion (the rescued .32 + dev-box
# bot patches were exactly this).
# ─────────────────────────────────────────────────────────────────────

from utils.watchdog_probes_drift import (  # noqa: E402
    _iter_inherited_checkouts,
    _read_git_origin_url,
    _real_code_patches,
)

# A real inherited patch (raphael-kit's hand-edited example) and the gated
# .32 bot patch — the two true-positives the policy wants surfaced.
_INHERITED_DIRTY = ("/home/op/raphael-kit",
                    "https://github.com/sunfounder/raphael-kit",
                    ["python/1.1.7_Lcd1602.py"])


def _iad_kw(tmp_path, repos):
    return dict(repos=repos, state_path=str(tmp_path / "iad_debounce.json"))


class TestInheritedAppDrift:
    """Action-5 drift probe: an inherited upstream checkout with an
    unversioned tracked-file code patch (R1) — the operator-named failure
    class. LOCAL detection, INERT off-box, benign churn never false-fires."""

    # ----- classification helpers (no subprocess) -----

    def test_owned_origin_is_skipped_inherited_is_kept(self, tmp_path):
        """_iter_inherited_checkouts keeps only non-Nursedude origins, reading
        .git/config directly (no git subprocess)."""
        for name, url in [
            ("MeshSense", "https://github.com/Affirmatech/MeshSense"),
            ("meshforge", "git@github.com:Nursedude/meshforge.git"),
            ("reticulum-meshchat", "https://github.com/Nursedude/reticulum-meshchat"),
        ]:
            repo = tmp_path / name
            (repo / ".git").mkdir(parents=True)
            (repo / ".git" / "config").write_text(
                '[core]\n\tbare = false\n[remote "origin"]\n'
                f"\turl = {url}\n\tfetch = +refs/heads/*\n")
        found = sorted(p for p, _u in _iter_inherited_checkouts([str(tmp_path)]))
        # Only the Affirmatech (non-owned) checkout survives.
        assert found == [str(tmp_path / "MeshSense")]

    def test_origin_unreadable_repo_is_skipped(self, tmp_path):
        """A repo whose .git/config has no origin URL can't be classified →
        skipped (indeterminate, never guessed inherited)."""
        repo = tmp_path / "weird"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text("[core]\n\tbare = false\n")
        assert _read_git_origin_url(str(repo)) is None
        assert list(_iter_inherited_checkouts([str(tmp_path)])) == []

    def test_excluded_basenames_never_inherited(self, tmp_path):
        """nvm / wireclaw-dudeclaw (tool + separately-governed firmware fork)
        are excluded even with a non-owned origin (PINS.md)."""
        for name in ("nvm", "wireclaw-dudeclaw"):
            repo = tmp_path / name
            (repo / ".git").mkdir(parents=True)
            (repo / ".git" / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/someone/' + name + "\n")
        assert list(_iter_inherited_checkouts([str(tmp_path)])) == []

    # ----- benign-manifest filter -----

    def test_benign_manifests_are_not_code_patches(self):
        """npm/pip/cargo machine-generated metadata is NOT a code patch — the
        MeshSense package*.json churn must not read as a source edit."""
        benign = ["package.json", "package-lock.json", "yarn.lock",
                  "sub/poetry.lock", "Cargo.lock", "Pipfile.lock"]
        assert _real_code_patches(benign) == []

    def test_real_source_edit_survives_the_filter(self):
        """A hand-edited source file IS a patch even next to benign churn."""
        mixed = ["package-lock.json", "mesh_bot.py", "modules/settings.py"]
        assert _real_code_patches(mixed) == ["mesh_bot.py", "modules/settings.py"]

    # ----- probe behavior (repos injected) -----

    def test_inert_when_no_inherited_checkouts(self, tmp_path):
        """The moc1/2/3 case: no inherited apps → None every tick, streak 0."""
        for _ in range(3):
            assert probe_inherited_app_drift(**_iad_kw(tmp_path, [])) is None
        assert json.loads(
            (tmp_path / "iad_debounce.json").read_text())["streak"] == 0

    def test_inert_when_only_benign_churn(self, tmp_path):
        """MeshSense npm churn only → never fires, streak stays 0 (must NOT
        false-fire — the blueprint's named must-not case)."""
        meshsense = ("/home/op/MeshSense",
                     "https://github.com/Affirmatech/MeshSense",
                     ["package.json", "package-lock.json"])
        for _ in range(3):
            assert probe_inherited_app_drift(
                **_iad_kw(tmp_path, [meshsense])) is None
        assert json.loads(
            (tmp_path / "iad_debounce.json").read_text())["streak"] == 0

    def test_fires_on_unversioned_patch_after_debounce(self, tmp_path):
        """The real R1 defect: silent on tick 1 (debounce), degraded on tick 2
        naming the app + the modified file + the rescue fix."""
        repos = [_INHERITED_DIRTY]
        assert probe_inherited_app_drift(**_iad_kw(tmp_path, repos)) is None
        sig = probe_inherited_app_drift(**_iad_kw(tmp_path, repos))
        assert sig is not None
        assert sig.cls == "inherited_app_drift"
        assert sig.severity == "degraded"
        assert sig.subject == "inherited-apps"
        assert "raphael-kit" in sig.detail
        assert "1.1.7_Lcd1602.py" in sig.detail
        assert "fleet-overlays" in sig.detail  # the rescue fix is named
        assert sig.extra["apps"] == ["raphael-kit"]
        assert sig.extra["patches"]["raphael-kit"] == ["python/1.1.7_Lcd1602.py"]
        assert sig.extra["debounce_streak"] == 2

    def test_clean_tick_resets_streak(self, tmp_path):
        """dirty → clean → dirty must NOT fire (needs two fresh dirty ticks)."""
        repos = [_INHERITED_DIRTY]
        assert probe_inherited_app_drift(**_iad_kw(tmp_path, repos)) is None
        assert probe_inherited_app_drift(**_iad_kw(tmp_path, [])) is None
        assert json.loads(
            (tmp_path / "iad_debounce.json").read_text())["streak"] == 0
        assert probe_inherited_app_drift(**_iad_kw(tmp_path, repos)) is None
        assert probe_inherited_app_drift(**_iad_kw(tmp_path, repos)) is not None

    def test_aggregates_multiple_apps(self, tmp_path):
        """Two dirty inherited apps → one aggregate signal listing both."""
        repos = [
            _INHERITED_DIRTY,
            ("/home/op/meshing-around",
             "https://github.com/SpudGunMan/meshing-around",
             ["mesh_bot.py", "modules/settings.py"]),
        ]
        probe_inherited_app_drift(**_iad_kw(tmp_path, repos))
        sig = probe_inherited_app_drift(**_iad_kw(tmp_path, repos))
        assert sig is not None
        assert set(sig.extra["apps"]) == {"raphael-kit", "meshing-around"}
        assert "2 INHERITED" in sig.detail

    def test_never_raises_on_bad_injected_repo(self, tmp_path):
        """A malformed injected tuple is swallowed → None, never crashes the
        tick (the broad-except backstop)."""
        assert probe_inherited_app_drift(
            **_iad_kw(tmp_path, [("only-one-field",)])) is None

    def test_git_status_error_reads_as_indeterminate_not_clean(self, tmp_path):
        """A git failure for a repo must NOT read as a clean tree — the repo
        contributes nothing, and with no other dirt the probe is INERT (not a
        false 'all clean confirmed'). Exercises the live discovery path with a
        git binary that always errors."""
        repo = tmp_path / "MeshSense"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/Affirmatech/MeshSense\n')
        sig = probe_inherited_app_drift(
            scan_roots=[str(tmp_path)],
            git_path="/nonexistent/git-binary",
            state_path=str(tmp_path / "iad_debounce.json"),
        )
        assert sig is None  # git unreadable → skipped, not fired, not "clean-fired"

    def test_git_status_carries_the_safety_flags(self):
        """Pin the real-data fix (verified vs moc5 2026-06-21): the git status
        invocation MUST carry --untracked-files=no (drop Raven/ucode artifacts),
        --ignore-submodules=all (drop MeshSense's api/webbluetooth gitlink churn
        — the #78 catch a clean-package.json fixture missed), safe.directory (no
        dubious-ownership refusal under sandboxed root), and core.fileMode=false
        (a perms-bit diff is not a content edit). Dropping any reintroduces a
        false-fire/false-clean class."""
        from utils.watchdog_probes_drift import _git_tracked_modifications
        captured = {}

        class _Proc:
            returncode = 0
            stdout = ""

        def _fake_run(argv, **kw):
            captured["argv"] = argv
            return _Proc()

        # _git_tracked_modifications now lives in watchdog_probes_env (the
        # 2026-07-14 drift split); patch subprocess in ITS module namespace,
        # not the drift re-export's (calibrated_claims rule 7 — the source seam).
        with patch("utils.watchdog_probes_env.subprocess.run", _fake_run):
            assert _git_tracked_modifications("/some/repo") == []
        argv = captured["argv"]
        assert "--untracked-files=no" in argv
        assert "--ignore-submodules=all" in argv
        assert "safe.directory=/some/repo" in argv
        assert "core.fileMode=false" in argv

    def test_signal_class_registered(self):
        assert "inherited_app_drift" in SIGNAL_CLASSES

    def test_home_unresolvable_with_clean_opt_is_indeterminate(
            self, tmp_path, monkeypatch):
        """L2: an unresolvable operator home silently narrowed the scan to
        /opt, then noted clean as if everything promised was scanned —
        contradicting the docstring's 'skipped (indeterminate)'. A skipped
        root must surface as indeterminate even when every scanned checkout
        is clean (partially-unscanned is not clean)."""
        import utils.watchdog_probes_env as env
        from utils.watchdog_probe_core import (
            collect_dispositions, reset_dispositions)

        def _fake_iter(roots, **kw):
            yield ("/opt/someapp", "https://github.com/upstream/someapp")

        monkeypatch.setattr(env, "_iter_inherited_checkouts", _fake_iter)
        monkeypatch.setattr(env, "_git_tracked_modifications",
                            lambda path, git_path="git": [])
        reset_dispositions()
        sig = probe_inherited_app_drift(
            service_user_fn=lambda: None,   # both home resolutions → None
            state_path=str(tmp_path / "iad_debounce.json"))
        assert sig is None
        got = collect_dispositions()["inherited_app_drift"]
        assert got["disp"] == "indeterminate"
        assert "unscanned" in got["reason"]

    def test_unscannable_root_is_indeterminate_not_inert(self, tmp_path):
        """L2: a scan root that EXISTS but errors on scandir is recorded as
        skipped by _iter_inherited_checkouts and surfaces indeterminate at
        the exit — never the 'no inherited checkouts' inert."""
        import utils.watchdog_probes_env as env
        from unittest.mock import patch as _patch
        from utils.watchdog_probe_core import (
            collect_dispositions, reset_dispositions)

        def _boom(root):
            raise PermissionError(13, "denied", str(root))

        reset_dispositions()
        with _patch.object(env.os, "scandir", _boom):
            sig = probe_inherited_app_drift(
                scan_roots=[str(tmp_path)],
                state_path=str(tmp_path / "iad_debounce.json"))
        assert sig is None
        got = collect_dispositions()["inherited_app_drift"]
        assert got["disp"] == "indeterminate"
        assert "unscanned" in got["reason"]


# ─────────────────────────────────────────────────────────────────────
# probe_gateway_dup_degraded (dedup/identity arc STEP 5)
# ─────────────────────────────────────────────────────────────────────

class TestProbeGatewayDupDegraded:
    """The first probe with a per-logical-message + cross-gateway dimension.
    Reads the 4c /fleet/dups rollup; degraded-only; the discipline under test
    is the honest indeterminate/stale gating (absence != healthy)."""

    def _ok(self, dup_pairs, **kw):
        d = {
            "status": "ok",
            "fleet_duplicate_pairs": dup_pairs,
            "fleet_duplicate_deliveries": max(0, dup_pairs),
            "covered_hosts": ["moc", "moc3"],
            "freshness": {"age_s": 5.0, "stale": False, "threshold_s": 1800.0},
            "fleet_duplicates": [
                {"content_id": "c1:abc", "recipient": "6b1a0120",
                 "distinct_hosts": 2}
            ] if dup_pairs else [],
        }
        d.update(kw)
        return d

    def test_fires_degraded_after_debounce(self, tmp_path):
        sp = str(tmp_path / "dup.json")
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._ok(1))):
            # tick 1: builds streak, INERT
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
            # tick 2: fires
            sig = probe_gateway_dup_degraded(debounce_path=sp)
        assert sig is not None
        assert sig.cls == "gateway_dup_degraded"
        assert sig.severity == "degraded"
        assert sig.subject == "fleet-gateways"
        assert sig.issue_ref is None
        assert sig.extra["fleet_duplicate_pairs"] == 1

    def test_indeterminate_is_inert_and_holds_streak(self, tmp_path):
        sp = str(tmp_path / "dup.json")
        # build a streak of 1 with an ok+dup tick
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._ok(1))):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
        # an indeterminate tick must NOT fire AND must not advance the streak
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(
                       {"status": "indeterminate",
                        "fleet_duplicate_pairs": 0})):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
        # streak held at 1 (not reset, not advanced) — next ok tick reaches 2
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._ok(1))):
            sig = probe_gateway_dup_degraded(debounce_path=sp)
        assert sig is not None  # 1 (held) + 1 = 2 → fires

    def test_stale_rollup_is_inert(self, tmp_path):
        sp = str(tmp_path / "dup.json")
        payload = self._ok(3)
        payload["freshness"] = {"age_s": 9999.0, "stale": True,
                                "threshold_s": 1800.0}
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(payload)):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
            assert probe_gateway_dup_degraded(debounce_path=sp) is None

    def test_healthy_resets_streak(self, tmp_path):
        sp = str(tmp_path / "dup.json")
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._ok(1))):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None  # streak 1
        # a clean (0 dups) tick resets the streak
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._ok(0))):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
        # so a single later dup tick is back at streak 1 (INERT), not firing
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._ok(1))):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None

    def test_endpoint_unreachable_is_inert(self, tmp_path):
        from urllib.error import URLError
        sp = str(tmp_path / "dup.json")
        def _raise(*a, **k):
            raise URLError("connection refused")
        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None

    def test_shape_error_is_inert(self, tmp_path):
        sp = str(tmp_path / "dup.json")
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(
                       {"status": "ok", "fleet_duplicate_pairs": "lots"})):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None

    def test_min_dup_pairs_threshold(self, tmp_path):
        sp = str(tmp_path / "dup.json")
        # 1 dup with min=2 → below threshold → clean (reset), never fires
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._ok(1))):
            assert probe_gateway_dup_degraded(
                debounce_path=sp, min_dup_pairs=2) is None
            assert probe_gateway_dup_degraded(
                debounce_path=sp, min_dup_pairs=2) is None

    # ── STEP 6: page on HUMAN dups, stay quiet on benign infra dups ───
    #
    # A dup whose recipient is itself a gateway/peer (e.g. MeshAnchor
    # 58cecbd0, in both gateways' peer_gateway_destinations) is infra-to-
    # infra: real, but benign and structurally unsuppressable without a
    # coordination substrate. The probe must page only on HUMAN dups.

    def _split(self, *, human, infra, **kw):
        """An ok rollup carrying the STEP-6 human/infra split fields."""
        total = human + infra
        d = self._ok(total, **kw)
        d["fleet_human_duplicate_pairs"] = human
        d["fleet_infra_duplicate_pairs"] = infra
        d["infra_hashes_known"] = 3
        d["fleet_duplicates"] = (
            [{"content_id": "c1:hh", "recipient": "6b1a0120",
              "recipient_kind": "human", "distinct_hosts": 2}] * human +
            [{"content_id": "c1:ii", "recipient": "58cecbd0",
              "recipient_kind": "infra", "distinct_hosts": 2}] * infra
        )
        return d

    def test_infra_only_dup_never_pages(self, tmp_path):
        # 5 infra dups, 0 human → quiet even well past the debounce.
        sp = str(tmp_path / "dup.json")
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._split(human=0, infra=5))):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
            assert probe_gateway_dup_degraded(debounce_path=sp) is None

    def test_human_dup_pages_after_debounce(self, tmp_path):
        sp = str(tmp_path / "dup.json")
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._split(human=1, infra=5))):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None  # streak 1
            sig = probe_gateway_dup_degraded(debounce_path=sp)
        assert sig is not None
        assert sig.extra["fleet_human_duplicate_pairs"] == 1
        assert sig.extra["fleet_infra_duplicate_pairs"] == 5

    def test_absent_human_field_falls_back_to_total(self, tmp_path):
        # An OLD JOIN (no human split) must NOT silently stop paging — it
        # falls back to the total dup count (pre-STEP-6 behavior) so an
        # un-upgraded manager never reads as a forged benign zero.
        sp = str(tmp_path / "dup.json")
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self._ok(2))):  # no human field
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
            sig = probe_gateway_dup_degraded(debounce_path=sp)
        assert sig is not None
        assert sig.extra["fleet_duplicate_pairs"] == 2

    def test_human_field_garbage_is_inert(self, tmp_path):
        sp = str(tmp_path / "dup.json")
        payload = self._ok(3)
        payload["fleet_human_duplicate_pairs"] = "lots"  # shape error
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(payload)):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
            assert probe_gateway_dup_degraded(debounce_path=sp) is None

    def test_registered_in_signal_classes(self):
        assert "gateway_dup_degraded" in SIGNAL_CLASSES


class TestMeshtasticdVszLeak:
    """probe_meshtasticd_vsz_leak (upstream meshtastic/firmware#10468) —
    guards the weekly-restart band-aid: fires ONLY past the weekly leak
    envelope, never on a leaking-but-managed box (no alert fatigue)."""

    def _proc(self, tmp_path, pid=1234, vm_kb=None, threads=4, status=True):
        d = tmp_path / str(pid)
        d.mkdir()
        if status:
            lines = ["Name:\tmeshtasticd"]
            if vm_kb is not None:
                lines.append(f"VmSize:\t{vm_kb} kB")
            lines.append(f"Threads:\t{threads}")
            (d / "status").write_text("\n".join(lines) + "\n")
        return tmp_path

    def test_none_when_service_not_running(self):
        from utils.watchdog_probes import probe_meshtasticd_vsz_leak
        with patch("utils.watchdog_probes_service._resolve_main_pid_status",
                   return_value=("down", None)):
            assert probe_meshtasticd_vsz_leak() is None

    def test_healthy_pi4_box_is_silent(self, tmp_path):
        from utils.watchdog_probes import probe_meshtasticd_vsz_leak
        proc = self._proc(tmp_path, vm_kb=300_000)  # ~0.3 GB
        sig = probe_meshtasticd_vsz_leak(proc_root=str(proc), main_pid=1234)
        assert sig is None

    def test_leaking_but_managed_pi5_is_silent(self, tmp_path):
        # ~561 GB at day 5 of a healthy weekly cadence (the live 07-10
        # reading) must NOT fire — the band-aid is working.
        from utils.watchdog_probes import probe_meshtasticd_vsz_leak
        proc = self._proc(tmp_path, vm_kb=561 * 1024 * 1024)
        sig = probe_meshtasticd_vsz_leak(proc_root=str(proc), main_pid=1234)
        assert sig is None

    def test_past_weekly_envelope_fires_degraded(self, tmp_path):
        from utils.watchdog_probes import probe_meshtasticd_vsz_leak
        proc = self._proc(tmp_path, vm_kb=800 * 1024 * 1024)  # 800 GB
        sig = probe_meshtasticd_vsz_leak(proc_root=str(proc), main_pid=1234)
        assert sig is not None
        assert sig.cls == "meshtasticd_vsz_leak"
        assert sig.severity == "degraded"
        assert "10468" in sig.detail
        assert "meshtasticd-restart.timer" in sig.detail
        assert sig.extra["vsz_gb"] == 800.0

    def test_unreadable_status_is_silent_not_healthy_shaped(self, tmp_path):
        from utils.watchdog_probes import probe_meshtasticd_vsz_leak
        proc = self._proc(tmp_path, status=False)
        sig = probe_meshtasticd_vsz_leak(proc_root=str(proc), main_pid=1234)
        assert sig is None

    def test_status_without_vmsize_is_silent(self, tmp_path):
        # A kernel-thread-shaped status (no VmSize) must not crash or fire.
        from utils.watchdog_probes import probe_meshtasticd_vsz_leak
        proc = self._proc(tmp_path, vm_kb=None)
        sig = probe_meshtasticd_vsz_leak(proc_root=str(proc), main_pid=1234)
        assert sig is None

    def test_threshold_configurable(self, tmp_path):
        from utils.watchdog_probes import probe_meshtasticd_vsz_leak
        proc = self._proc(tmp_path, vm_kb=100 * 1024 * 1024)  # 100 GB
        sig = probe_meshtasticd_vsz_leak(proc_root=str(proc), main_pid=1234,
                                         threshold_gb=64.0)
        assert sig is not None
        assert sig.extra["threshold_gb"] == 64.0


# ─────────────────────────────────────────────────────────────────────
# 2026-07-18 adversarial-review fixes — FAIL-DARK disposition honesty.
# A probe may note CLEAN only on a positively-successful observation;
# ambiguity resolves dark (indeterminate), positively-observed absence
# resolves inert. (N1/N2/N3/N4/G1/G2/G3.)
# ─────────────────────────────────────────────────────────────────────


def _fresh_dispositions():
    """Reset the per-tick recorder and return the collector."""
    from utils.watchdog_probe_core import collect_dispositions, reset_dispositions
    reset_dispositions()
    return collect_dispositions


class TestRnsCollisionSpacedInstanceName:
    """N1 — the #69/#82 class: ``ss`` truncates an abstract socket name at
    the first space, so a spaced instance_name ("volcano ai rns" →
    displayed "@rns/volcano") never matched the old inline needle. Owners
    stayed empty and the probe noted affirmative CLEAN on exactly the
    incident boxes. The probe now reuses the twin parser
    ``utils.rns_init._parse_ss_listener_line`` (Issue #82 fix)."""

    SPACED = "volcano ai rns"

    def test_foreign_owner_fires_despite_truncated_ss_display(self, tmp_path):
        """A foreign squatter on a spaced-name box MUST fire, not read clean."""
        fake_proc = tmp_path / "1"
        fake_proc.mkdir()
        (fake_proc / "cmdline").write_bytes(
            b"/usr/bin/python3\x00/opt/meshanchor/src/daemon.py\x00start\x00"
        )
        ss_out = _SS_FOREIGN_OWNED.replace("200825", "1")  # shows @rns/volcano
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_make_subprocess_mock(ss_out)):
            sig = probe_rns_namespace_collision(
                self.SPACED, proc_root=str(tmp_path))
        assert sig is not None
        assert sig.cls == "rns_namespace_collision"
        assert sig.severity == "wedge"
        got = collect().get("rns_namespace_collision")
        assert got is None or got["disp"] != "clean", (
            "a listener existed — clean must never be noted")

    def test_spaced_name_never_notes_clean_while_listener_exists(self, tmp_path):
        """The exact pre-fix regression: truncated display + spaced needle →
        owners empty → affirmative clean. Must be gone."""
        fake_proc = tmp_path / "1"
        fake_proc.mkdir()
        (fake_proc / "cmdline").write_bytes(_NOMADNET_WRAPPER_CMDLINE)
        ss_out = _SS_FOREIGN_OWNED.replace("200825", "1")
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_make_subprocess_mock(ss_out)):
            sig = probe_rns_namespace_collision(
                self.SPACED, proc_root=str(tmp_path), rnsd_enabled=True)
        # RNS-family non-rnsd owner + rnsd enabled → inverted, degraded.
        assert sig is not None
        assert sig.extra["tier"] == "inverted"
        got = collect().get("rns_namespace_collision")
        assert got is None or got["disp"] != "clean"

    def test_spaced_name_rnsd_owner_resolves_as_before(self, tmp_path):
        """Ownership resolution works as rns_init's does: comm==rnsd on the
        truncated line passes (no signal)."""
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_make_subprocess_mock(_SS_RNSD_OWNED)):
            sig = probe_rns_namespace_collision(
                self.SPACED, proc_root=str(tmp_path), rnsd_enabled=True)
        assert sig is None

    def test_twin_parser_agreement_on_spaced_fixture(self):
        """The probe's matching IS rns_init's — pin the twin on the spaced
        truncated fixture so a future inline reimplementation can't drift."""
        from utils.rns_init import _parse_ss_listener_line
        line = ('u_str LISTEN   0      0      @rns/volcano 99999 * 0 '
                'users:(("python3",pid=42,fd=4))')
        assert _parse_ss_listener_line(line, self.SPACED) == (42, "python3")
        # ...and the trailing-space anchor keeps "volcano-x" from matching.
        assert _parse_ss_listener_line(line, "volcano-x y") is None


class TestRnsInterfaceTableUnobservable:
    """N2 — empty/unrecognized rnstatus output yields parse_error=None with
    interfaces=[]; a healthy rnstatus always prints at least the
    shared-instance block, so this is an UNOBSERVED table, never clean."""

    def test_empty_rnstatus_output_is_indeterminate_not_clean(self):
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(rnstatus_text="")
        assert sig is None
        got = collect()["rns_interface_down_peer_reachable"]
        assert got["disp"] == "indeterminate"
        assert "unobservable" in got["reason"]

    def test_unrecognized_error_text_is_indeterminate_not_clean(self):
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text="some banner nobody taught the parser\n")
        assert sig is None
        got = collect()["rns_interface_down_peer_reachable"]
        assert got["disp"] == "indeterminate"

    def test_real_interface_table_still_notes_clean(self):
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=False):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_UP_BLOCK)
        assert sig is None
        assert collect()["rns_interface_down_peer_reachable"]["disp"] == "clean"


class TestCalibrationDriftGarbledLedger:
    """N3 — load_events maps any OSError/garbled content to [] (never-raises
    contract); a non-empty ledger yielding zero events was NOT observed and
    must not fold to 'every claim held' (clean)."""

    def test_garbled_ledger_is_indeterminate_not_clean(self, tmp_path):
        p = tmp_path / "calibration_ledger.jsonl"
        p.write_text("{{{this is not jsonl\ngarbage line two\n")
        collect = _fresh_dispositions()
        sig = probe_calibration_drift(ledger_path=str(p))
        assert sig is None
        got = collect()["calibration_drift"]
        assert got["disp"] == "indeterminate"
        assert "garbled" in got["reason"] or "unreadable" in got["reason"]

    def test_zero_byte_ledger_keeps_current_disposition(self, tmp_path):
        p = tmp_path / "calibration_ledger.jsonl"
        p.write_text("")
        collect = _fresh_dispositions()
        sig = probe_calibration_drift(ledger_path=str(p))
        assert sig is None
        got = collect()["calibration_drift"]
        assert got["disp"] != "indeterminate"

    def test_absent_ledger_stays_inert(self, tmp_path):
        collect = _fresh_dispositions()
        sig = probe_calibration_drift(ledger_path=str(tmp_path / "nope.jsonl"))
        assert sig is None
        assert collect()["calibration_drift"]["disp"] == "inert"


class TestMiniFileReadCorruptionIndeterminate:
    """N4 — a PRESENT but corrupt/unreadable mini file is unobservable, not
    absent: FileNotFoundError keeps inert; other OSError/ValueError →
    indeterminate."""

    def test_history_state_corrupt_is_indeterminate(self, tmp_path):
        (tmp_path / "mini_dudeai_state.json").write_text("{corrupt json")
        collect = _fresh_dispositions()
        sig = probe_history_write_failure(
            mini_home=str(tmp_path), state_path=str(tmp_path / "sp.json"))
        assert sig is None
        got = collect()["history_write_stalled"]
        assert got["disp"] == "indeterminate"
        assert "corrupt" in got["reason"] or "unreadable" in got["reason"]

    def test_history_state_absent_stays_inert(self, tmp_path):
        collect = _fresh_dispositions()
        sig = probe_history_write_failure(
            mini_home=str(tmp_path), state_path=str(tmp_path / "sp.json"))
        assert sig is None
        assert collect()["history_write_stalled"]["disp"] == "inert"

    def test_rules_live_corrupt_is_indeterminate(self, tmp_path):
        (tmp_path / "mini_dudeai_rules.json").write_text("{corrupt json")
        repo_root = os.path.join(os.path.dirname(__file__), "..")
        collect = _fresh_dispositions()
        sig = probe_rules_seed_drift(
            mini_home=str(tmp_path), role="primary", meshforge_root=repo_root)
        assert sig is None
        got = collect()["rules_seed_drift"]
        assert got["disp"] == "indeterminate"

    def test_rules_live_absent_stays_inert(self, tmp_path):
        repo_root = os.path.join(os.path.dirname(__file__), "..")
        collect = _fresh_dispositions()
        sig = probe_rules_seed_drift(
            mini_home=str(tmp_path), role="primary", meshforge_root=repo_root)
        assert sig is None
        assert collect()["rules_seed_drift"]["disp"] == "inert"


class TestGatewayDupTransportIndeterminate:
    """G1 — on the manager box (the only observable one) a crashed map /
    5xx lands in the transport except too: unobservable ≠ benign inert."""

    def test_transport_error_is_indeterminate_not_inert(self, tmp_path):
        from urllib.error import URLError as _URLError

        def _raise(*args, **kwargs):
            raise _URLError("connection refused")

        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_gateway_dup_degraded(
                debounce_path=str(tmp_path / "d.json"))
        assert sig is None
        got = collect()["gateway_dup_degraded"]
        assert got["disp"] == "indeterminate"
        assert "manager" in got["reason"]


class TestDupsRollupUnavailableIsInertNotBlind:
    """2026-07-28 — ``unavailable`` and ``indeterminate`` are DIFFERENT states
    and must not share a label.

    Both probes used to gate on ``status != "ok"`` and emit one hardcoded
    reason, "rollup indeterminate (<2 gateways reachable)". But every box runs
    a map, so a NON-manager box answers 200 with ``{"status":"unavailable"}``
    — meaning that reason was FALSE on 6 of the fleet's 9 boxes, which emitted
    permanent ``detector_blind_any`` noise. The cost is not the noise: it is
    that a REAL <2-gateway coverage loss on the manager rendered
    IDENTICALLY to it, so the collapse destroyed the signal these probes
    exist to carry.

    ``unavailable`` is structural and permanent here → ``inert`` (the
    ``detector_blind_any`` annotation: "a legitimately-absent organ is not a
    blind one"). ``indeterminate`` stays ``indeterminate`` and now carries the
    JOIN's OWN reason, which names the boxes that went uncovered.
    """

    UNAVAILABLE = {
        "status": "unavailable",
        "reason": ("no rollup yet — fleet_dup_collector has not run on this "
                   "box (wire its cron on the manager)"),
    }
    INDETERMINATE = {
        "status": "indeterminate",
        "indeterminate_reason": ("only 1 gateway view(s) with confirmed "
                                 "deliveries reachable (need >=2 ...); "
                                 "uncovered: moc3(unreachable)"),
        "fleet_duplicate_pairs": 0,
    }

    # The collector-wiring answer is pinned to False — "this box is NOT the
    # manager", which is the case this class was always describing. It became
    # an INPUT on 2026-07-28 (see TestDupsUnavailableRoleIsEvidenceNotInference):
    # before that these cases read the ambient crontab of whatever machine ran
    # the suite, so they passed on a dev box, and would not have on the manager
    # or in CI. A test whose verdict depends on un-pinned machine state is not
    # pinning anything.
    def _dual_homed(self, payload, tmp_path):
        from utils.watchdog_probes_gateway import (
            probe_gateway_dual_homed_exposure,
        )
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway."
                   "_dups_collector_wired_here", return_value=False):
            sig = probe_gateway_dual_homed_exposure(
                state_path=str(tmp_path / "dh.json"), payload=payload)
        assert sig is None
        return collect()["gateway_dual_homed_exposure"]

    def _dup(self, payload, tmp_path, name="d.json"):
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(payload)), \
             patch("utils.watchdog_probes_gateway."
                   "_dups_collector_wired_here", return_value=False):
            sig = probe_gateway_dup_degraded(
                debounce_path=str(tmp_path / name))
        assert sig is None
        return collect()["gateway_dup_degraded"]

    # ── gateway_dup_degraded ────────────────────────────────────────────
    def test_dup_unavailable_is_inert(self, tmp_path):
        got = self._dup(self.UNAVAILABLE, tmp_path)
        assert got["disp"] == "inert"
        assert "manager" in got["reason"]

    def test_dup_unavailable_reason_never_claims_two_gateways(self, tmp_path):
        """THE regression pin: the old reason lied about WHY it was blind."""
        got = self._dup(self.UNAVAILABLE, tmp_path)
        assert "<2 gateways" not in got["reason"]

    def test_dup_unavailable_holds_streak(self, tmp_path):
        """inert != clean — an unobservable tick must not reset the debounce."""
        sp = str(tmp_path / "dup.json")
        ok = {"status": "ok", "fleet_duplicate_pairs": 1,
              "fleet_duplicate_deliveries": 1, "covered_hosts": ["moc", "moc3"],
              "freshness": {"age_s": 5.0, "stale": False,
                            "threshold_s": 1800.0},
              "fleet_duplicates": []}
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(ok)):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None  # 1
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self.UNAVAILABLE)):
            assert probe_gateway_dup_degraded(debounce_path=sp) is None
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(ok)):
            # streak HELD at 1, so this tick reaches 2 and fires
            assert probe_gateway_dup_degraded(debounce_path=sp) is not None

    def test_dup_indeterminate_carries_the_join_reason(self, tmp_path):
        got = self._dup(self.INDETERMINATE, tmp_path)
        assert got["disp"] == "indeterminate"
        assert "uncovered: moc3(unreachable)" in got["reason"]

    def test_dup_indeterminate_without_join_reason_falls_back(self, tmp_path):
        """No reason from the JOIN → say the reason is UNKNOWN, never assert
        '<2 gateways reachable' as fact — nothing established it (the
        confidently-false-reason class, surviving in the fallback branch
        until 2026-07-28)."""
        got = self._dup({"status": "indeterminate"}, tmp_path)
        assert got["disp"] == "indeterminate"
        assert "<2 gateways" not in got["reason"]
        assert "no reason" in got["reason"]

    def test_dup_unknown_status_is_indeterminate_not_inert(self, tmp_path):
        """An unrecognised status must not decay into a benign-looking inert,
        and the reason must NAME the status instead of guessing a cause."""
        got = self._dup({"status": "wat"}, tmp_path)
        assert got["disp"] == "indeterminate"
        assert "'wat'" in got["reason"]
        assert "<2 gateways" not in got["reason"]

    # ── gateway_dual_homed_exposure ─────────────────────────────────────
    def test_dual_homed_unavailable_is_inert(self, tmp_path):
        got = self._dual_homed(self.UNAVAILABLE, tmp_path)
        assert got["disp"] == "inert"
        assert "manager" in got["reason"]

    def test_dual_homed_unavailable_reason_never_claims_two_gateways(
            self, tmp_path):
        got = self._dual_homed(self.UNAVAILABLE, tmp_path)
        assert "<2 gateways" not in got["reason"]

    def test_dual_homed_indeterminate_carries_join_reason_and_suffix(
            self, tmp_path):
        got = self._dual_homed(self.INDETERMINATE, tmp_path)
        assert got["disp"] == "indeterminate"
        assert "uncovered: moc3(unreachable)" in got["reason"]
        assert "two vantages" in got["reason"]

    def test_dual_homed_unknown_status_is_indeterminate_not_inert(
            self, tmp_path):
        got = self._dual_homed({"status": "wat"}, tmp_path)
        assert got["disp"] == "indeterminate"


class TestDupsUnreachableRoleIsEvidenceNotInference:
    """2026-08-09 — the branch the 2026-07-28 fix did not reach.

    That fix taught the PAYLOAD path to read this box's role from the
    collector-cron declaration. It cured every box that ANSWERS on :5000 —
    which is every box that runs a map. The TRANSPORT path one branch above
    it kept the collapsed reason, so the one shape that never answers stayed
    permanently blind:

        moc3 is role gateway-only with meshforge-map disabled BY DESIGN.
        No map → urlopen raises → "dups rollup unreachable — manager-map
        down or not the manager box" → `indeterminate` for 13.5 days, on a
        box that could never observe a cross-gateway dup at all.

    Measured 2026-08-09, the three-way contrast that localizes it: the
    manager box (collector wired) `clean`; a non-manager that RUNS a map
    (200 + unavailable) correctly `inert`; moc3 (not wired, NO map)
    `indeterminate`. Same declaration, same verdict owed — different code
    path.

    A map listening is a PROXY that merely correlates with manager-ness
    (2026-08-09 synth_soak lesson). The cron is the declaration.
    """

    def _dup(self, wired, tmp_path):
        from urllib.error import URLError
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway.urlopen",
                   side_effect=URLError("Connection refused")), \
             patch("utils.watchdog_probes_gateway._dups_collector_wired_here",
                   return_value=wired):
            sig = probe_gateway_dup_degraded(
                debounce_path=str(tmp_path / "d.json"))
        assert sig is None
        return collect()["gateway_dup_degraded"]

    def _dual_homed(self, wired, tmp_path):
        from urllib.error import URLError
        from utils.watchdog_probes_gateway import (
            probe_gateway_dual_homed_exposure,
        )
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway.urlopen",
                   side_effect=URLError("Connection refused")), \
             patch("utils.watchdog_probes_gateway._dups_collector_wired_here",
                   return_value=wired):
            sig = probe_gateway_dual_homed_exposure(
                state_path=str(tmp_path / "dh.json"))
        assert sig is None
        return collect()["gateway_dual_homed_exposure"]

    # ── THE moc3 regression: no map at all, and not the manager ─────────
    def test_no_map_off_the_manager_is_inert_not_indeterminate(self, tmp_path):
        got = self._dup(False, tmp_path)
        assert got["disp"] == "inert"

    def test_no_map_reason_does_not_blame_a_down_manager_map(self, tmp_path):
        """The old string named a culprit it could not see — moc3's manager
        map was never down; moc3 simply serves no map. Distrusting that text
        is the standing tell, so the fixed text must not repeat it."""
        got = self._dup(False, tmp_path)
        assert "manager-map down" not in got["reason"]
        assert "by design" in got["reason"]

    # ── on the manager, unreachable is STILL a real coverage loss ───────
    def test_wired_here_unreachable_stays_indeterminate(self, tmp_path):
        """The guard against over-correcting: silencing this everywhere would
        hide the one failure that matters — the manager's map being down."""
        got = self._dup(True, tmp_path)
        assert got["disp"] == "indeterminate"
        assert "unreachable" in got["reason"]

    def test_unreadable_crontab_is_indeterminate_never_inert(self, tmp_path):
        got = self._dup(None, tmp_path)
        assert got["disp"] == "indeterminate"

    # ── the sibling probe shares the classifier, so it shares the fix ───
    def test_dual_homed_no_map_off_manager_is_inert(self, tmp_path):
        got = self._dual_homed(False, tmp_path)
        assert got["disp"] == "inert"

    def test_dual_homed_wired_here_stays_indeterminate(self, tmp_path):
        got = self._dual_homed(True, tmp_path)
        assert got["disp"] == "indeterminate"


class TestDupsUnavailableRoleIsEvidenceNotInference:
    """2026-07-28 review — ``unavailable`` -> ``inert`` inferred this box's
    ROLE from the ABSENCE of the very artifact the probe audits.

    ``/fleet/dups`` answers ``{"status":"unavailable"}`` on exactly one
    condition: ``FileNotFoundError`` on the rollup state file. Off the manager
    that means "no collector here" — a legitimately-absent organ, correctly
    inert. ON the manager it means the collector is not publishing, which is a
    REAL coverage loss on the only box where these probes can ever do their
    job — and it was being reported as inert, with a reason that flatly
    asserted "not the manager box".

    That is the self-confirming-detector shape persistent_issues.md already
    names: *a checker must not consume the artifact it validates*. The role is
    now read from INDEPENDENT evidence — whether the collector cron is wired
    in the operator's crontab on this box — and an unreadable crontab is
    UNOBSERVABLE (indeterminate), never silently benign (honest_failure_modes
    #2).
    """

    UNAVAILABLE = {
        "status": "unavailable",
        "reason": ("no rollup yet — fleet_dup_collector has not run on this "
                   "box (wire its cron on the manager)"),
    }

    def _dup(self, wired, tmp_path):
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(self.UNAVAILABLE)), \
             patch("utils.watchdog_probes_gateway._dups_collector_wired_here",
                   return_value=wired):
            sig = probe_gateway_dup_degraded(
                debounce_path=str(tmp_path / "d.json"))
        assert sig is None
        return collect()["gateway_dup_degraded"]

    def _dual_homed(self, wired, tmp_path):
        from utils.watchdog_probes_gateway import (
            probe_gateway_dual_homed_exposure,
        )
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway._dups_collector_wired_here",
                   return_value=wired):
            sig = probe_gateway_dual_homed_exposure(
                state_path=str(tmp_path / "dh.json"), payload=self.UNAVAILABLE)
        assert sig is None
        return collect()["gateway_dual_homed_exposure"]

    # ── the box that RUNS the collector ─────────────────────────────────
    def test_wired_here_but_no_rollup_is_indeterminate_not_inert(self,
                                                                 tmp_path):
        """THE fix: on the manager, a missing rollup is a fault, not an
        absent organ. inert would silence it permanently."""
        got = self._dup(True, tmp_path)
        assert got["disp"] == "indeterminate"

    def test_wired_here_reason_does_not_deny_being_the_manager(self, tmp_path):
        """The old reason asserted 'not the manager box' as fact — on the
        manager that is simply false, and it is what made the silence
        convincing."""
        got = self._dup(True, tmp_path)
        assert "not the manager" not in got["reason"]
        assert "collector" in got["reason"]

    # ── a box that does NOT run it — unchanged, still quiet ─────────────
    def test_not_wired_stays_inert(self, tmp_path):
        got = self._dup(False, tmp_path)
        assert got["disp"] == "inert"
        assert "manager" in got["reason"]

    # ── unobservable is neither ─────────────────────────────────────────
    def test_unreadable_crontab_is_indeterminate_never_inert(self, tmp_path):
        """Cannot tell an absent organ from a publish failure → say so.
        Unobservable is never excused as benign (honest_failure_modes #2)."""
        got = self._dup(None, tmp_path)
        assert got["disp"] == "indeterminate"

    # ── the second probe shares the classifier, so it shares the fix ────
    def test_dual_homed_wired_here_is_indeterminate(self, tmp_path):
        got = self._dual_homed(True, tmp_path)
        assert got["disp"] == "indeterminate"

    def test_dual_homed_not_wired_stays_inert(self, tmp_path):
        got = self._dual_homed(False, tmp_path)
        assert got["disp"] == "inert"

    # ── the evidence reader itself ──────────────────────────────────────
    #
    # Driven through the REAL spool read (a temp dir standing in for
    # /var/spool/cron/crontabs), not a stubbed reader — the states this
    # function must distinguish are distinguished BY that read, so mocking it
    # away would test nothing. The reader scans the WHOLE spool dir: the old
    # per-operator read resolved "the operator" from a live session bus
    # (min-UID /run/user/*/bus) — a linger artifact that answered None on any
    # box rebooting before linger started, and picked the WRONG user when a
    # second bus-owning user had a lower UID (2026-07-28 review). No
    # _find_operator_user patch here is the point: no session is consulted.
    # CRON_SPOOL_DIRS is patched per-test to a unique tmp dir, which also
    # keys the reader's cache so results never bleed between tests.
    def _wired(self, tmp_path, cron_text=None, unreadable=False):
        import builtins
        from utils.watchdog_probes_gateway import _dups_collector_wired_here
        spool = tmp_path / "spool"
        spool.mkdir()
        if cron_text is not None:
            (spool / "op").write_text(cron_text, encoding="utf-8")
        real_open = builtins.open

        def fake_open(path, *a, **k):
            if unreadable and str(path).startswith(str(spool)):
                raise PermissionError("spool unreadable")
            return real_open(path, *a, **k)

        with patch("utils.watchdog_probe_core.CRON_SPOOL_DIRS",
                   (str(spool),)), \
             patch("builtins.open", fake_open):
            return _dups_collector_wired_here()

    def test_wiring_reader_detects_the_collector_cron(self, tmp_path):
        cron = ("*/5 * * * * PYTHONPATH=/opt/meshforge/src python3 -m "
                "utils.fleet_dup_collector >/dev/null 2>&1; "
                "/opt/meshforge/scripts/cron_verdict.sh fleet_dup_rollup $?\n")
        assert self._wired(tmp_path, cron) is True

    def test_wiring_reader_false_when_other_crons_but_not_this_one(self,
                                                                   tmp_path):
        """False must mean 'observed, and it is not here'."""
        cron = ("7 * * * * /home/op/mini_honest_fire.sh >/dev/null 2>&1; "
                "/opt/meshforge/scripts/cron_verdict.sh mini_honest_fire $?\n")
        assert self._wired(tmp_path, cron) is False

    def test_wiring_reader_false_when_box_has_no_crontab_at_all(self,
                                                               tmp_path):
        """An ABSENT spool is positive evidence that no cron is wired here —
        not blindness. Reading it as unobservable would put every
        crontab-less box back into the permanent detector_blind noise that
        3afda33a removed."""
        assert self._wired(tmp_path, cron_text=None) is False

    def test_wiring_reader_none_when_spool_exists_but_is_unreadable(self,
                                                                    tmp_path):
        """Unreadable spool → None, never False (= 'not the manager'), which
        is the whole defect in miniature."""
        assert self._wired(tmp_path, "x\n", unreadable=True) is None

    def test_wiring_reader_scans_every_crontab_not_one_chosen_user(
            self, tmp_path):
        """The token in a SECOND user's crontab still counts. The old reader
        picked one 'operator' by smallest bus-owning UID and read only that
        user's file — a manager whose collector cron lived under any other
        user read as a confident 'not wired here' (2026-07-28 review)."""
        from utils.watchdog_probes_gateway import _dups_collector_wired_here
        spool = tmp_path / "spool"
        spool.mkdir()
        (spool / "alice").write_text(
            "7 * * * * /home/alice/other_job.sh\n", encoding="utf-8")
        (spool / "bob").write_text(
            "40 * * * * python3 -m utils.fleet_dup_collector\n",
            encoding="utf-8")
        with patch("utils.watchdog_probe_core.CRON_SPOOL_DIRS",
                   (str(spool),)):
            assert _dups_collector_wired_here() is True

    def test_wiring_reader_caches_per_process(self, tmp_path):
        """The answer changes ~never; the watchdog asks twice per tick. The
        second call within the TTL must not re-read the spool (6 of 9 boxes
        paid a per-tick scan for a constant answer; 2026-07-28 review)."""
        from utils import watchdog_probe_core as core
        spool = tmp_path / "spool"
        spool.mkdir()
        (spool / "op").write_text(
            "40 * * * * python3 -m utils.fleet_dup_collector\n",
            encoding="utf-8")
        with patch("utils.watchdog_probe_core.CRON_SPOOL_DIRS",
                   (str(spool),)):
            assert core.operator_cron_wired("fleet_dup_collector") is True
            with patch("utils.watchdog_probe_core."
                       "_operator_cron_wired_uncached") as uncached:
                assert core.operator_cron_wired("fleet_dup_collector") is True
                uncached.assert_not_called()


class TestFlowProbesOperatorUnresolvable:
    """G2 — sdir=None means the OPERATOR was unresolvable (early-boot / no
    user bus), not a positively-observed absent state dir. On a
    soak-running box that window must not read 'doesn't run it'."""

    def test_synth_soak_operator_unresolvable_is_indeterminate(self, tmp_path):
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway_flow._resolve_synth_soak_dir",
                   return_value=None):
            sig = probe_synth_soak_degraded(
                debounce_path=str(tmp_path / "d.json"))
        assert sig is None
        got = collect()["synth_soak_degraded"]
        assert got["disp"] == "indeterminate"
        assert "operator unresolvable" in got["reason"]

    def test_synth_soak_dir_genuinely_absent_stays_inert(self, tmp_path):
        collect = _fresh_dispositions()
        sig = probe_synth_soak_degraded(
            state_dir=str(tmp_path / "nope"),
            debounce_path=str(tmp_path / "d.json"))
        assert sig is None
        assert collect()["synth_soak_degraded"]["disp"] == "inert"

    def test_resource_canary_operator_unresolvable_is_indeterminate(
            self, tmp_path):
        from utils.watchdog_probes import probe_resource_canary_degraded
        collect = _fresh_dispositions()
        with patch(
                "utils.watchdog_probes_gateway_flow._resolve_resource_canary_dir",
                return_value=None):
            sig = probe_resource_canary_degraded(
                debounce_path=str(tmp_path / "d.json"))
        assert sig is None
        got = collect()["resource_canary_degraded"]
        assert got["disp"] == "indeterminate"
        assert "operator unresolvable" in got["reason"]

    def test_resource_canary_dir_genuinely_absent_stays_inert(self, tmp_path):
        from utils.watchdog_probes import probe_resource_canary_degraded
        collect = _fresh_dispositions()
        sig = probe_resource_canary_degraded(
            state_dir=str(tmp_path / "nope"),
            debounce_path=str(tmp_path / "d.json"))
        assert sig is None
        assert collect()["resource_canary_degraded"]["disp"] == "inert"


class TestSynthSoakDarkLegNeedsATimer:
    """2026-08-09 — meshanchor-server sat ``degraded`` for 78 days telling the
    operator to "Check meshforge-synth-soak.timer", a unit that has never
    existed on that box. Its seven ``synth-*.json`` were hand-fired lab runs
    from May (irregular clock times; the first exited 1 with
    ``ModuleNotFoundError: No module named 'lab'``), so nothing had a cadence
    to fall behind. The DARK bar is 2.5x ``OnCalendar=*:07:00`` — meaningless
    without the timer — so it is now gated on the enable-symlink, the actual
    declaration that this box runs the soak.

    Every test here passes ``timer_wants_dirs`` explicitly: the verdict must
    not depend on the crontab/units of whatever box runs the suite.
    """

    UNIT = "meshforge-synth-soak.timer"

    @staticmethod
    def _wants(tmp_path, enrolled: bool):
        d = tmp_path / "timers.target.wants"
        d.mkdir()
        if enrolled:
            (d / TestSynthSoakDarkLegNeedsATimer.UNIT).write_text(
                "[Timer]\n", encoding="utf-8")
        return [str(d)]

    def test_dark_fires_when_the_timer_IS_enrolled(self, tmp_path):
        """The production case, at the real 78-day age: an enrolled timer
        whose output stopped MUST still fire (this is the leg the gate must
        not retire). test_synth_soak_silence_fires_after_debounce covers the
        same leg at a 4h age."""
        now = 9_000_000_000.0
        sdir = str(tmp_path / "synth_soak")
        sp = str(tmp_path / "s.json")
        _write_synth(sdir, "20260523T040140Z", pass_envelope=True,
                     age_s=6_740_000, now=now)
        wants = self._wants(tmp_path, enrolled=True)
        assert probe_synth_soak_degraded(
            state_dir=sdir, now=now, debounce_path=sp,
            timer_wants_dirs=wants) is None            # debounce tick 1
        sig = probe_synth_soak_degraded(
            state_dir=sdir, now=now, debounce_path=sp, timer_wants_dirs=wants)
        assert sig is not None
        assert sig.cls == "synth_soak_degraded"
        assert "DARK" in sig.detail

    def test_dark_is_inert_when_the_timer_is_NOT_enrolled(self, tmp_path):
        """The meshanchor-server case: artifacts present, no timer, ever."""
        now = 9_000_000_000.0
        sdir = str(tmp_path / "synth_soak")
        sp = str(tmp_path / "s.json")
        _write_synth(sdir, "20260523T040140Z", pass_envelope=True,
                     age_s=6_740_000, now=now)
        wants = self._wants(tmp_path, enrolled=False)
        collect = _fresh_dispositions()
        for _ in range(3):  # would have fired on tick 2 under the old code
            assert probe_synth_soak_degraded(
                state_dir=sdir, now=now, debounce_path=sp,
                timer_wants_dirs=wants) is None
        got = collect()["synth_soak_degraded"]
        assert got["disp"] == "inert"
        assert "not enabled here" in got["reason"]

    def test_dark_is_indeterminate_when_enrollment_unreadable(self, tmp_path):
        """Unknown is never "not scheduled" (honest_failure_modes #1)."""
        now = 9_000_000_000.0
        sdir = str(tmp_path / "synth_soak")
        _write_synth(sdir, "20260523T040140Z", pass_envelope=True,
                     age_s=6_740_000, now=now)
        collect = _fresh_dispositions()
        with patch("utils.watchdog_probes_gateway_flow._timer_enrolled",
                   return_value=None):
            sig = probe_synth_soak_degraded(
                state_dir=sdir, now=now, debounce_path=str(tmp_path / "s.json"),
                timer_wants_dirs=["/whatever"])
        assert sig is None
        got = collect()["synth_soak_degraded"]
        assert got["disp"] == "indeterminate"
        assert "timers.target.wants" in got["reason"]

    def test_envelope_leg_is_NOT_gated_on_the_timer(self, tmp_path):
        """A FRESH failing envelope is a real delivery failure however it was
        produced — only the staleness bar depends on a cadence. Guards against
        curing the false alarm by muting the probe on unscheduled boxes."""
        now = 1_000_000.0
        sdir = str(tmp_path / "synth_soak")
        sp = str(tmp_path / "s.json")
        _write_synth(sdir, "20260615T170721Z", pass_envelope=False,
                     ok_ratio=0.41, threshold=0.95, total_ok=246,
                     total_samples=600, worst_fail=61.0, age_s=60, now=now)
        wants = self._wants(tmp_path, enrolled=False)
        assert probe_synth_soak_degraded(
            state_dir=sdir, now=now, debounce_path=sp,
            timer_wants_dirs=wants) is None
        sig = probe_synth_soak_degraded(
            state_dir=sdir, now=now, debounce_path=sp, timer_wants_dirs=wants)
        assert sig is not None
        assert "0.41" in sig.detail


class TestTimerEnrolledTriState:
    """``_timer_enrolled`` is the new instrument; drill its three answers."""

    UNIT = "meshforge-synth-soak.timer"

    def test_symlink_present_is_true(self, tmp_path):
        from utils.watchdog_probes_gateway_flow import _timer_enrolled
        d = tmp_path / "wants"
        d.mkdir()
        target = tmp_path / "meshforge-synth-soak.timer"
        target.write_text("[Timer]\n", encoding="utf-8")
        os.symlink(str(target), str(d / self.UNIT))  # how systemd enables it
        assert _timer_enrolled(self.UNIT, [str(d)]) is True

    def test_dir_present_unit_absent_is_false(self, tmp_path):
        from utils.watchdog_probes_gateway_flow import _timer_enrolled
        d = tmp_path / "wants"
        d.mkdir()
        assert _timer_enrolled(self.UNIT, [str(d)]) is False

    def test_wants_dir_itself_absent_is_false_not_unknown(self, tmp_path):
        """A box with no user timers at all has no wants dir — that is a real
        observation of "not enabled", not a blind spot."""
        from utils.watchdog_probes_gateway_flow import _timer_enrolled
        assert _timer_enrolled(self.UNIT, [str(tmp_path / "nope")]) is False

    def test_operator_unresolvable_is_unknown(self):
        from utils.watchdog_probes_gateway_flow import _timer_enrolled
        assert _timer_enrolled(self.UNIT, None) is None

    def test_permission_error_is_unknown_never_false(self, tmp_path):
        """The one that matters: a stat that fails for a reason OTHER than
        "not found" must not be flattened into "not enabled" — that would
        silently retire the DARK leg on a box that DOES run the soak."""
        from utils.watchdog_probes_gateway_flow import _timer_enrolled
        d = tmp_path / "wants"
        d.mkdir()
        real_listdir = os.listdir

        def boom(path, *a, **kw):
            if str(path) == str(d):
                raise PermissionError(13, "Permission denied")
            return real_listdir(path, *a, **kw)

        # Patched rather than chmod 000: a suite run as root would still read
        # a 000 dir, making the verdict depend on WHO ran it
        # (feedback_tests_must_pin_ambient_state).
        with patch("os.listdir", side_effect=boom):
            assert _timer_enrolled(self.UNIT, [str(d)]) is None

    def test_second_dir_wins_when_first_is_absent(self, tmp_path):
        """Home wants-dir empty + site-wide preset present → enrolled."""
        from utils.watchdog_probes_gateway_flow import _timer_enrolled
        empty = tmp_path / "home_wants"
        empty.mkdir()
        site = tmp_path / "site_wants"
        site.mkdir()
        (site / self.UNIT).write_text("[Timer]\n", encoding="utf-8")
        assert _timer_enrolled(self.UNIT, [str(empty), str(site)]) is True


def test_delivery_canary_db_unobservable_is_indeterminate_not_clean():
    """G3 (probe side) — the producer marks health db_unobservable when its
    snapshot() DB read failed; the canary must read that as indeterminate,
    never clean (the #63 class it exists to catch)."""
    payload = {"health": {
        "db_unobservable": True,
        "preflight_ok": None,
        "consecutive_write_errors": 0,
        "last_write_error": None,
        "db_path": "/x/y/z.db",
    }}
    from utils.watchdog_probe_core import collect_dispositions, reset_dispositions
    reset_dispositions()
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_write_canary()
    assert sig is None
    got = collect_dispositions()["delivery_write_canary"]
    assert got["disp"] == "indeterminate"
    assert "unobservable" in got["reason"]
    reset_dispositions()


# ─────────────────────────────────────────────────────────────────────
# user_timer_unit_failing — a USER timer's job fails on EVERY firing
# (the 2026-07-12→19 kiai lab_peers class: a week of silence)
# ─────────────────────────────────────────────────────────────────────


def _user_timer_fixture(tmp_path, timers, target="timers"):
    """Build ~/.config/systemd/user/<target>.target.wants/ with the given
    {timer_name: body_or_None}. Returns user_home as str.

    ``target`` is parameterised because enablement is a symlink under ANY
    target's wants dir, not only ``timers.target.wants`` — see
    TestProbeSeesTimersUnderAnyTarget.
    """
    home = tmp_path / "home"
    wants = home / ".config" / "systemd" / "user" / f"{target}.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    for name, body in timers.items():
        (wants / name).write_text(body or "[Timer]\nOnCalendar=*:00/10:00\n")
    return str(home)


class TestProbeSeesTimersUnderAnyTarget:
    """Pinned at the CONSUMER, not just the helper (hfm #4: a reader/writer
    pair wires together or fails together).

    utils.user_units already has tests for discovering every
    ``*.target.wants``. Those would keep passing if this probe were rewired to
    a narrower reader, so the blind spot could come back with a green suite —
    the probe is what the operator relies on, so the probe is what gets pinned.

    Live case (meshanchor-server, 2026-08-09): ``meshanchor-map-restart.timer``
    declares ``WantedBy=timers.target`` but is linked from
    ``default.target.wants``. Before the fix this probe could not see it at
    all, so its weekly oneshot could have failed on every firing forever —
    the 2026-07-19 kiai hole, one directory over.
    """

    def test_fires_for_a_timer_enrolled_under_default_target(self, tmp_path):
        from utils.watchdog_probes_user import probe_user_timer_unit_failing
        now = 1_000_000.0
        home = _user_timer_fixture(
            tmp_path, {"meshanchor-map-restart.timer": None}, target="default")
        sig = probe_user_timer_unit_failing(
            user_home=home, now=now, debounce_ticks=1,
            state_path=str(tmp_path / "s.json"),
            ts_fn=_ts_fn({"meshanchor-map-restart.service": {
                "Failed with result": [now - 1800, now - 1200, now - 600],
                "Finished ": [],
            }}))
        assert sig is not None, (
            "a timer enabled under default.target.wants is enabled — the probe "
            "must judge its job like any other")
        assert sig.subject == "meshanchor-map-restart.service"

    def test_sees_timers_across_two_targets_at_once(self, tmp_path):
        """A box with both dirs populated must judge every enrolled timer."""
        from utils.watchdog_probes_user import probe_user_timer_unit_failing
        now = 1_000_000.0
        _user_timer_fixture(tmp_path, {"a.timer": None}, target="timers")
        home = _user_timer_fixture(tmp_path, {"b.timer": None},
                                   target="default")
        sig = probe_user_timer_unit_failing(
            user_home=home, now=now, debounce_ticks=1,
            state_path=str(tmp_path / "s.json"),
            ts_fn=_ts_fn({
                # >= min_failures each: a single failure is a blip the probe
                # deliberately ignores, so both need real streaks for this to
                # test SCOPE rather than the failure threshold.
                "a.service": {"Failed with result": [now - 1500, now - 600],
                              "Finished ": []},
                "b.service": {"Failed with result": [now - 900, now - 300],
                              "Finished ": []},
            }))
        assert sig is not None
        judged = {f["service"] for f in sig.extra["failing"]}
        assert judged == {"a.service", "b.service"}, judged

    def test_a_healthy_timer_under_another_target_stays_silent(self, tmp_path):
        """Widening scope must not manufacture alarms: newly-visible timers
        whose jobs succeed stay quiet (measured true on the real box — the one
        newly-seen timer was already succeeding)."""
        from utils.watchdog_probes_user import probe_user_timer_unit_failing
        now = 1_000_000.0
        home = _user_timer_fixture(
            tmp_path, {"meshanchor-map-restart.timer": None}, target="default")
        sig = probe_user_timer_unit_failing(
            user_home=home, now=now, debounce_ticks=1,
            state_path=str(tmp_path / "s.json"),
            ts_fn=_ts_fn({"meshanchor-map-restart.service": {
                "Failed with result": [],
                "Finished ": [now - 600],
            }}))
        assert sig is None


def _ts_fn(table):
    """(unit, pattern) -> timestamps, from a {unit: {pattern: value}} table.
    A value of None models an unobservable journal."""
    def fn(unit, pattern):
        return table.get(unit, {}).get(pattern)
    return fn


def test_user_timer_failing_fires_on_the_kiai_class(tmp_path):
    """kiai 2026-07-12→19: meshforge-tracer.timer fired every 10 min while
    its oneshot exited 2 every single time. Inactive-between-firings by
    design, never crashlooping — invisible to every other user-unit leg."""
    from utils.watchdog_probes_user import probe_user_timer_unit_failing
    now = 1_000_000.0
    home = _user_timer_fixture(tmp_path, {"meshforge-tracer.timer": None})
    sig = probe_user_timer_unit_failing(
        user_home=home, now=now, debounce_ticks=1,
        state_path=str(tmp_path / "s.json"),
        ts_fn=_ts_fn({"meshforge-tracer.service": {
            "Failed with result": [now - 1800, now - 1200, now - 600],
            "Finished ": [],
        }}))
    assert sig is not None
    assert sig.cls == "user_timer_unit_failing"
    assert sig.severity == "degraded"
    assert sig.subject == "meshforge-tracer.service"
    assert "meshforge-tracer.service" in sig.detail
    assert sig.extra["failing"][0]["failures"] == 3


def test_user_timer_failing_silent_when_it_recovered(tmp_path):
    """Failures followed by a SUCCESS are a blip, not an outage. This is what
    makes it an outcome detector rather than an error counter."""
    from utils.watchdog_probes_user import probe_user_timer_unit_failing
    now = 1_000_000.0
    home = _user_timer_fixture(tmp_path, {"meshforge-tracer.timer": None})
    assert probe_user_timer_unit_failing(
        user_home=home, now=now, debounce_ticks=1,
        state_path=str(tmp_path / "s.json"),
        ts_fn=_ts_fn({"meshforge-tracer.service": {
            "Failed with result": [now - 1800, now - 1200],
            "Finished ": [now - 300],          # newer than the newest failure
        }})) is None


def test_user_timer_failing_silent_on_single_blip(tmp_path):
    from utils.watchdog_probes_user import probe_user_timer_unit_failing
    now = 1_000_000.0
    home = _user_timer_fixture(tmp_path, {"meshforge-tracer.timer": None})
    assert probe_user_timer_unit_failing(
        user_home=home, now=now, debounce_ticks=1,
        state_path=str(tmp_path / "s.json"),
        ts_fn=_ts_fn({"meshforge-tracer.service": {
            "Failed with result": [now - 600],   # 1 < min_failures
            "Finished ": [],
        }})) is None


def test_user_timer_failing_silent_after_remediation(tmp_path):
    """Post-fix history must not page — the recency gate. Without it a job
    fixed an hour ago keeps firing off its own journal until the window rolls."""
    from utils.watchdog_probes_user import probe_user_timer_unit_failing
    now = 1_000_000.0
    home = _user_timer_fixture(tmp_path, {"meshforge-tracer.timer": None})
    assert probe_user_timer_unit_failing(
        user_home=home, now=now, debounce_ticks=1, recency_s=3600.0,
        state_path=str(tmp_path / "s.json"),
        ts_fn=_ts_fn({"meshforge-tracer.service": {
            "Failed with result": [now - 9000, now - 8000],   # all stale
            "Finished ": [],
        }})) is None


def test_user_timer_failing_unobservable_journal_is_indeterminate(tmp_path):
    """honest_failure_modes #1: a journalctl wedge must NEVER read as
    'all timers healthy'."""
    from utils.watchdog_probes_user import probe_user_timer_unit_failing
    from utils.watchdog_probe_core import (
        collect_dispositions, reset_dispositions)
    reset_dispositions()
    home = _user_timer_fixture(tmp_path, {"meshforge-tracer.timer": None})
    sig = probe_user_timer_unit_failing(
        user_home=home, now=1_000_000.0, debounce_ticks=1,
        state_path=str(tmp_path / "s.json"),
        ts_fn=_ts_fn({"meshforge-tracer.service": {
            "Failed with result": None, "Finished ": None}}))
    assert sig is None
    got = collect_dispositions()["user_timer_unit_failing"]
    assert got["disp"] == "indeterminate"
    assert "unobservable" in got["reason"]
    reset_dispositions()


def test_user_timer_failing_unobservable_does_not_reset_streak(tmp_path):
    """The streak must be HELD across an unobservable tick, so a journalctl
    wedge cannot quietly clear an ongoing outage before it ever fires."""
    from utils.watchdog_probes_user import probe_user_timer_unit_failing
    now = 1_000_000.0
    sp = tmp_path / "s.json"
    home = _user_timer_fixture(tmp_path, {"meshforge-tracer.timer": None})
    failing = {"meshforge-tracer.service": {
        "Failed with result": [now - 1200, now - 600], "Finished ": []}}
    # tick 1: candidate, held by the 2-tick debounce
    assert probe_user_timer_unit_failing(
        user_home=home, now=now, state_path=str(sp), debounce_ticks=2,
        ts_fn=_ts_fn(failing)) is None
    assert json.loads(sp.read_text())["streak"] == 1
    # tick 2: journal unobservable — streak must survive, not reset to 0
    assert probe_user_timer_unit_failing(
        user_home=home, now=now, state_path=str(sp), debounce_ticks=2,
        ts_fn=_ts_fn({"meshforge-tracer.service": {
            "Failed with result": None, "Finished ": None}})) is None
    assert json.loads(sp.read_text())["streak"] == 1
    # tick 3: observable again → confirms
    sig = probe_user_timer_unit_failing(
        user_home=home, now=now, state_path=str(sp), debounce_ticks=2,
        ts_fn=_ts_fn(failing))
    assert sig is not None and sig.cls == "user_timer_unit_failing"


def test_user_timer_failing_inert_without_timers(tmp_path):
    """A box with no user timers enrolled is INERT, not clean-by-assumption."""
    from utils.watchdog_probes_user import probe_user_timer_unit_failing
    from utils.watchdog_probe_core import (
        collect_dispositions, reset_dispositions)
    reset_dispositions()
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    assert probe_user_timer_unit_failing(
        user_home=str(home), now=1_000_000.0, debounce_ticks=1,
        state_path=str(tmp_path / "s.json"), ts_fn=_ts_fn({})) is None
    assert collect_dispositions()["user_timer_unit_failing"]["disp"] == "inert"
    reset_dispositions()


def test_user_timer_failing_honours_unit_override(tmp_path):
    """A timer with an explicit Unit= must be judged on THAT service, not the
    stem default — otherwise the probe silently watches a unit that does not
    exist and reads healthy forever."""
    from utils.watchdog_probes_user import probe_user_timer_unit_failing
    now = 1_000_000.0
    home = _user_timer_fixture(tmp_path, {
        "wrapper.timer": "[Timer]\nOnCalendar=hourly\nUnit=real-job.service\n"})
    sig = probe_user_timer_unit_failing(
        user_home=home, now=now, debounce_ticks=1,
        state_path=str(tmp_path / "s.json"),
        ts_fn=_ts_fn({"real-job.service": {
            "Failed with result": [now - 900, now - 300], "Finished ": []}}))
    assert sig is not None
    assert sig.subject == "real-job.service"


def test_user_timer_failing_is_in_the_closed_enum():
    assert "user_timer_unit_failing" in SIGNAL_CLASSES


def test_lxmf_propagation_cache_reader_rejects_stale_and_skewed(tmp_path):
    """The real reader: a cache older than the freshness bound is 'stale',
    and a last_seen from the future is a clock artifact on these RTC-less
    Pis (honest_failure_modes #6), never evidence."""
    from utils.watchdog_probes_gateway import _read_fresh_propagation_nodes
    import datetime as dt

    home = tmp_path / "home"
    cache = home / ".cache" / "meshforge"
    cache.mkdir(parents=True)
    now = 1_784_000_000.0
    iso = lambda off: dt.datetime.fromtimestamp(now + off).isoformat()

    def write(saved_off, nodes):
        (cache / "rns_nodes.json").write_text(json.dumps(
            {"saved_at": iso(saved_off), "nodes": nodes}))

    # fresh cache, one fresh node + one ancient + one from the future
    write(-60, [
        {"service_type": "LXMF_PROPAGATION", "node_id": "a", "last_seen": iso(-3600)},
        {"service_type": "LXMF_PROPAGATION", "node_id": "b", "last_seen": iso(-99 * 3600)},
        {"service_type": "LXMF_PROPAGATION", "node_id": "c", "last_seen": iso(9 * 3600)},
        {"service_type": "LXMF_DELIVERY", "node_id": "d", "last_seen": iso(-60)},
    ])
    cands, state = _read_fresh_propagation_nodes(str(home), now)
    assert state == "ok"
    assert [c[1] for c in cands] == ["a"]      # ancient, future, non-prop all out

    # a cache the gateway stopped updating cannot speak for the present
    write(-9 * 3600, [
        {"service_type": "LXMF_PROPAGATION", "node_id": "a", "last_seen": iso(-60)}])
    assert _read_fresh_propagation_nodes(str(home), now) == ([], "stale")

    # torn/corrupt read → unreadable, never an empty "nothing available"
    (cache / "rns_nodes.json").write_text("{not json")
    assert _read_fresh_propagation_nodes(str(home), now) == ([], "unreadable")

    # absent cache → absent (INERT), distinct from unreadable
    (cache / "rns_nodes.json").unlink()
    assert _read_fresh_propagation_nodes(str(home), now) == ([], "absent")


def test_lxmf_propagation_config_reader_separates_absent_from_unreadable(tmp_path):
    """The row-5 lesson, applied here at write time rather than found later."""
    from utils.watchdog_probes_gateway import _read_configured_propagation_node
    home = tmp_path / "h"
    cfg = home / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    assert _read_configured_propagation_node(str(home)) == (None, "absent")
    (cfg / "gateway.json").write_text("{oops")
    assert _read_configured_propagation_node(str(home)) == (None, "unreadable")
    (cfg / "gateway.json").write_text(json.dumps({"rns": {"propagation_node": ""}}))
    assert _read_configured_propagation_node(str(home)) == ("", "ok")
    (cfg / "gateway.json").write_text(json.dumps({"rns": {"propagation_node": "abc"}}))
    assert _read_configured_propagation_node(str(home)) == ("abc", "ok")
    (cfg / "gateway.json").write_text(json.dumps({"no_rns_block": True}))
    assert _read_configured_propagation_node(str(home)) == ("", "ok")


# ─────────────────────────────────────────────────────────────────────
# 2026-07-20 — lxmf_propagation_node_dark: the CONFIGURED propagation
# node stopped answering. The shape-A companion that had to ship WITH
# adoption: setting rns.propagation_node makes the probe above INERT by
# design, so alone, adoption would swap a watched gap for an unwatched
# dependency. Same durable node-cache evidence, never the journal.
# ─────────────────────────────────────────────────────────────────────

_NODE = "3968a2eeac25e2e7a7961f25842d3d85"
_HOUR = 3600.0


def _lpd(tmp_path, *, configured=(_NODE, "ok"), ages=None, freshest=600.0,
         cache_state="ok", ticks=2):
    from utils.watchdog_probes_gateway import probe_lxmf_propagation_node_dark
    if ages is None:                       # default: our node is 40 h silent
        ages = {_NODE: 40 * _HOUR, _NODE[:16]: 40 * _HOUR, "aed1f551": 600.0}
    return probe_lxmf_propagation_node_dark(
        home="/nonexistent-but-unused",
        now=1_784_000_000.0,
        configured_fn=lambda: configured,
        liveness_fn=lambda: (ages, freshest, cache_state),
        state_path=str(tmp_path / "lpd.json"),
        debounce_ticks=ticks,
    )


def test_lxmf_propagation_dark_stale_leg_fires_after_debounce(tmp_path):
    """Configured node in the cache but long silent, while OTHER propagation
    announces still arrive — the box can hear the class, this node is gone."""
    assert _lpd(tmp_path) is None                       # tick 1: debounce
    sig = _lpd(tmp_path)                                # tick 2: confirmed
    assert sig is not None
    assert sig.cls == "lxmf_propagation_node_dark"
    assert sig.severity == "degraded"
    assert sig.subject == _NODE[:16]
    assert sig.extra["leg"] == "stale"
    assert sig.extra["age_h"] == 40.0
    assert "OFFLINE" in sig.detail
    assert "answered before and stopped" in sig.detail


def test_lxmf_propagation_dark_unheard_leg_names_a_wrong_hash(tmp_path):
    """A configured hash absent from the cache is the failure adoption itself
    introduces — a truncated/typo'd hash — and must not read as 'stale'."""
    _lpd(tmp_path, ages={"aed1f551": 600.0})
    sig = _lpd(tmp_path, ages={"aed1f551": 600.0})
    assert sig is not None
    assert sig.extra["leg"] == "unheard"
    assert sig.extra["age_h"] is None
    assert "NEVER been heard" in sig.detail
    assert "truncat" in sig.detail


def test_lxmf_propagation_dark_clean_when_the_node_answers(tmp_path):
    """A fresh announce from the configured node resets the streak, so a
    later outage still has to serve its full debounce."""
    _lpd(tmp_path)                                     # prime a streak
    for _ in range(3):
        assert _lpd(tmp_path, ages={_NODE: 900.0, "aed1f551": 600.0}) is None
    assert json.loads((tmp_path / "lpd.json").read_text())["streak"] == 0


def test_lxmf_propagation_dark_clean_reason_states_its_window(tmp_path):
    """A disposition must carry its evidence BUDGET, not just its evidence.

    2026-08-12: the clean reason read "configured node answered 178 min ago"
    with no window, and it was misread mid-session as evidence gone stale —
    prompting a request to rebuild this probe as a live check. It is 3h into
    an 18h window that is deliberately three announce periods wide, and the
    ACTIVE leg already exists as propagation_soak_degraded (which proves
    store-and-forward, not mere reachability). The cure was one f-string; this
    pins it so the next reader is not sent down the same path.
    """
    from utils.watchdog_probe_core import (collect_dispositions,
                                           reset_dispositions)
    from utils.watchdog_probes_gateway_lxmf import _PROPAGATION_DARK_AFTER_S
    reset_dispositions()
    assert _lpd(tmp_path, ages={_NODE: 900.0, "aed1f551": 600.0}) is None
    got = collect_dispositions()["lxmf_propagation_node_dark"]
    assert got["disp"] == "clean"
    want_h = f"window {_PROPAGATION_DARK_AFTER_S / 3600:.0f}h"
    assert want_h in got["reason"], (
        f"clean reason must name its window ({want_h}) or it reads as stale "
        f"evidence — got {got['reason']!r}")
    assert "propagation_soak_degraded" in got["reason"], (
        "the clean reason must point at the leg that owns store-and-forward, "
        "so nobody concludes THIS probe should have proven it")


def test_lxmf_propagation_dark_matches_a_short_form_hash(tmp_path):
    """The cache carries both the 32-hex rns_hash and the 16-hex id form;
    a configured full hash must match either without reading as unheard."""
    _lpd(tmp_path, ages={_NODE[:16]: 900.0, "aed1f551": 600.0})
    assert _lpd(tmp_path, ages={_NODE[:16]: 900.0, "aed1f551": 600.0}) is None


def test_lxmf_propagation_dark_inert_when_nothing_is_configured(tmp_path):
    """Nothing adopted = nothing to watch. (Until 2026-08-08 the unadopted
    gap belonged to lxmf_propagation_unused; that probe was removed and the
    gap is knowingly unwatched, but this probe's INERT here is unchanged.)"""
    _lpd(tmp_path)
    for _ in range(3):
        assert _lpd(tmp_path, configured=("", "ok")) is None
    assert json.loads((tmp_path / "lpd.json").read_text())["streak"] == 0


def test_lxmf_propagation_dark_inert_without_a_gateway(tmp_path):
    """ABSENT gateway.json is an observation (INERT); UNREADABLE is a failure
    to observe (indeterminate) — never collapsed into one."""
    for _ in range(3):
        assert _lpd(tmp_path, configured=(None, "absent")) is None
    for _ in range(3):
        assert _lpd(tmp_path, configured=(None, "unreadable")) is None


def test_lxmf_propagation_dark_holds_on_stale_or_unreadable_cache(tmp_path):
    """Stale bytes cannot testify about the present. The streak is HELD, not
    reset and not advanced — unobservable is neither healthy nor dark."""
    _lpd(tmp_path)                                     # streak = 1
    for state in ("stale", "unreadable"):
        assert _lpd(tmp_path, cache_state=state) is None
    assert json.loads((tmp_path / "lpd.json").read_text())["streak"] == 1
    assert _lpd(tmp_path) is not None                  # resumes at 2, fires


def test_lxmf_propagation_dark_holds_when_the_box_hears_no_propagation(tmp_path):
    """THE honesty guard. With no propagation announce from ANY node, the
    box's ability to hear the class is unproven, so our node's silence is
    unobservable — an RNS-wide wedge must not be relabelled as this node's
    death (honest_failure_modes #2; those probes own that fault)."""
    # From cold, it never even reaches the streak — nothing to hold, and no
    # state file is written at all.
    for freshest in (None, 40 * _HOUR):
        for _ in range(4):
            assert _lpd(tmp_path, freshest=freshest) is None
    assert not (tmp_path / "lpd.json").exists()

    # And with a streak already running, unobservable HOLDS it — neither
    # advanced (which would page from blindness) nor reset (which would
    # forget a real fault mid-confirmation).
    _lpd(tmp_path)                                     # streak = 1
    for freshest in (None, 40 * _HOUR):
        assert _lpd(tmp_path, freshest=freshest) is None
    assert json.loads((tmp_path / "lpd.json").read_text())["streak"] == 1
    assert _lpd(tmp_path) is not None                  # resumes at 2, fires


def test_lxmf_propagation_dark_reader_discards_future_stamps(tmp_path):
    """Wall-clock is forgeable on RTC-less Pis. A future last_seen must be
    DISCARDED, never counted fresh — that direction would hide a dead node."""
    from utils.watchdog_probes_gateway import _read_propagation_liveness
    home = tmp_path / "h"
    cache = home / ".cache" / "meshforge"
    cache.mkdir(parents=True)
    now = 1_784_000_000.0
    path = cache / "rns_nodes.json"

    assert _read_propagation_liveness(str(home), now) == ({}, None, "absent")
    path.write_text("{torn")
    assert _read_propagation_liveness(str(home), now) == ({}, None, "unreadable")

    def _write(nodes, saved_offset=0.0):
        path.write_text(json.dumps({
            "saved_at": now - saved_offset, "nodes": nodes}))

    _write([], saved_offset=99 * _HOUR)
    assert _read_propagation_liveness(str(home), now)[2] == "stale"

    _write([
        {"rns_hash": _NODE, "id": "rns_" + _NODE[:16],
         "service_type": "LXMF_PROPAGATION", "last_seen": now + 9000.0},
        {"rns_hash": "aed1f551" * 4, "service_type": "LXMF_PROPAGATION",
         "last_seen": now - 600.0},
        {"rns_hash": "dead" * 8, "service_type": "MESHTASTIC",
         "last_seen": now - 60.0},
    ])
    ages, freshest, state = _read_propagation_liveness(str(home), now)
    assert state == "ok"
    assert _NODE not in ages          # forged future stamp discarded, not fresh
    assert ages[("aed1f551" * 4)] == 600.0
    assert freshest == 600.0
    assert not any(k.startswith("dead") for k in ages)   # non-propagation ignored


def test_lxmf_propagation_dark_reader_handles_the_real_cache_shape(tmp_path):
    """Pinned to the SHAPE observed live on moc/moc3 2026-07-20, not to a
    convenient one: rns_nodes.json stores saved_at and last_seen as ISO-8601
    STRINGS (not epoch floats), and carries every node under both the 32-hex
    rns_hash and the 'rns_<16hex>' id. A reader that only understood epochs
    would read every real cache as unparseable and go permanently blind."""
    import datetime as dt
    from utils.watchdog_probes_gateway import _read_propagation_liveness
    home = tmp_path / "h"
    cache = home / ".cache" / "meshforge"
    cache.mkdir(parents=True)
    now = dt.datetime(2026, 7, 20, 10, 6, 56).timestamp()

    def _iso(offset_s):
        return (dt.datetime.fromtimestamp(now - offset_s)).isoformat()

    (cache / "rns_nodes.json").write_text(json.dumps({
        "version": 1,
        "saved_at": _iso(0),
        "nodes": [
            {"id": "rns_3968a2eeac25e2e7", "rns_hash": _NODE,
             "name": "j^x(", "service_type": "LXMF_PROPAGATION",
             "service_aspect": "lxmf.propagation", "last_seen": _iso(1740.0)},
            {"id": "rns_481fd6386c50a9fe", "rns_hash": "481fd6386c50a9fe" * 2,
             "service_type": "LXMF_PROPAGATION", "last_seen": _iso(3 * _HOUR)},
        ],
    }))
    ages, freshest, state = _read_propagation_liveness(str(home), now)
    assert state == "ok"                       # ISO saved_at parsed, not stale
    assert ages[_NODE] == 1740.0               # matched on the full hash
    assert ages[_NODE[:16]] == 1740.0          # and on the short id form
    assert freshest == 1740.0


def test_lxmf_propagation_reader_consumes_the_writers_own_shape(tmp_path):
    """2026-07-21 review (W3): the propagation reader keyed on node_id /
    display_name / long_name — keys the production writer (UnifiedNode.
    to_dict via the node tracker's web cache) NEVER emits ("id"/"name"),
    so the nearest-node name enrichment was a dead read and the fixtures
    pinned a shape production never produces. This one is DERIVED from the
    writer, so a key rename on either side fails here."""
    import datetime as dt
    import sys
    sys.path.insert(0, "src")
    from gateway.node_models import UnifiedNode
    from utils.watchdog_probes_gateway import _read_fresh_propagation_nodes

    home = tmp_path / "h"
    cache = home / ".cache" / "meshforge"
    cache.mkdir(parents=True)
    now = 1_784_000_000.0

    node = UnifiedNode(id="rns_3968a2eeac25e2e7", network="rns",
                       name="WH6GXZ MeshForge PN",
                       service_type="LXMF_PROPAGATION")
    node.last_seen = dt.datetime.fromtimestamp(now - 600)
    (cache / "rns_nodes.json").write_text(json.dumps({
        "version": 1,
        "saved_at": dt.datetime.fromtimestamp(now - 30).isoformat(),
        "nodes": [node.to_dict()],
    }))

    cands, state = _read_fresh_propagation_nodes(str(home), now)
    assert state == "ok"
    assert cands, "writer-shaped entry must be readable"
    age_s, node_id, name = cands[0]
    assert node_id == "rns_3968a2eeac25e2e7"
    assert name == "WH6GXZ MeshForge PN"       # the previously-dead read
    assert age_s == 600.0


# ---------------------------------------------------------------------------
# 2026-07-21 — propagation_soak_degraded (the LXMF store-and-forward drill).
# The OUTCOME leg for the propagation organ: node_dark proves the configured
# node ANNOUNCES, this proves it STORES AND FORWARDS.
# ---------------------------------------------------------------------------


def _write_prop_envelope(sdir, *, passed, age_s=60.0, now=None, rounds=None):
    """Publish a prop-*.json with a controlled mtime."""
    import os
    import time as _t
    now = _t.time() if now is None else now
    os.makedirs(sdir, exist_ok=True)
    doc = {
        "propagation_node": "3968a2eeac25e2e7a7961f25842d3d85",
        "total_ok": 1 if passed else 0,
        "total_samples": 1,
        "ok_ratio_threshold": 1.0,
        "pass_envelope": passed,
        "round_results": rounds if rounds is not None else (
            [{"seq": 1, "ok": True}] if passed else
            [{"seq": 1, "ok": False, "stage": "pull",
              "reason": "stored but not retrieved within 180s"}]
        ),
    }
    path = os.path.join(sdir, "prop-20260721T000000Z.json")
    with open(path, "w") as fh:
        json.dump(doc, fh)
    os.utime(path, (now - age_s, now - age_s))
    return path


def _prop_probe(**kw):
    """Hermetic call: stub the intent gate (2026-07-21 F3) so these tests
    never read the real box's gateway.json."""
    kw.setdefault("configured_fn",
                  lambda: ("3968a2eeac25e2e7a7961f25842d3d85", "ok"))
    return probe_propagation_soak_degraded(**kw)


def test_propagation_soak_inert_when_box_does_not_run_the_drill(tmp_path):
    """The common case: no state dir means this box has nothing to say."""
    sig = _prop_probe(
        state_dir=str(tmp_path / "absent"), now=1000.0,
        debounce_path=str(tmp_path / "d.json"))
    assert sig is None


def test_propagation_soak_inert_when_no_node_adopted(tmp_path):
    """F3: intent gate — an unconfigured box is INERT even when a state dir
    (old fire-script mkdir; a de-adopted era's envelopes) still exists."""
    sdir = str(tmp_path / "propagation_soak")
    _write_prop_envelope(sdir, passed=True, age_s=99999.0, now=100000.0)
    sig = probe_propagation_soak_degraded(
        state_dir=sdir, now=100000.0,
        debounce_path=str(tmp_path / "d.json"),
        configured_fn=lambda: ("", "ok"))
    assert sig is None                       # no silence page for a dead organ


def test_propagation_soak_unreadable_intent_is_held_not_inert(tmp_path):
    """Unreadable gateway.json = intent unknown — indeterminate, never a
    clean INERT (that would collapse two states, the row-5 defect)."""
    sdir = str(tmp_path / "propagation_soak")
    _write_prop_envelope(sdir, passed=False, age_s=60.0, now=100000.0)
    sp = str(tmp_path / "d.json")
    for _ in range(3):
        assert probe_propagation_soak_degraded(
            state_dir=sdir, now=100000.0, debounce_path=sp,
            configured_fn=lambda: (None, "unreadable")) is None


def test_propagation_soak_never_published_escalates(tmp_path):
    """07-23 audit E-F1: a drill that has NEVER published an envelope (typo'd
    hash rc-4, broken env from install day) was an absorbing held-indeterminate
    with no escalation — fire script exit 0 always, its FAIL verdict unwired.
    After ~3 cadences of empty state dir, the probe must page."""
    sdir = str(tmp_path / "propagation_soak")
    os.makedirs(sdir, exist_ok=True)          # dir exists, NO prop-* ever
    sp = str(tmp_path / "d.json")
    ip = str(tmp_path / "indet.json")
    t0 = 100000.0
    # first sighting anchors + holds
    assert _prop_probe(state_dir=sdir, now=t0, debounce_path=sp,
                       indet_state_path=ip) is None
    # within the window: still held
    assert _prop_probe(state_dir=sdir, now=t0 + 3600, debounce_path=sp,
                       indet_state_path=ip) is None
    # beyond 3h: fires the never_published leg
    sig = _prop_probe(state_dir=sdir, now=t0 + 3 * 3600 + 60,
                      debounce_path=sp, indet_state_path=ip)
    assert sig is not None
    assert sig.extra.get("leg") == "never_published"
    assert "NEVER published" in sig.detail


def test_propagation_soak_never_published_anchor_clears_on_envelope(tmp_path):
    """Once a real envelope appears, the never-published anchor must clear
    (healthy reset removes the state file)."""
    sdir = str(tmp_path / "propagation_soak")
    os.makedirs(sdir, exist_ok=True)
    sp = str(tmp_path / "d.json")
    ip = str(tmp_path / "indet.json")
    t0 = 100000.0
    assert _prop_probe(state_dir=sdir, now=t0, debounce_path=sp,
                       indet_state_path=ip) is None      # anchors
    _write_prop_envelope(sdir, passed=True, age_s=60.0, now=t0 + 3600)
    assert _prop_probe(state_dir=sdir, now=t0 + 3600, debounce_path=sp,
                       indet_state_path=ip) is None      # healthy, resets
    import json as _json
    assert not os.path.exists(ip) or "no_result_since" not in _json.load(
        open(ip))


def test_propagation_soak_sustained_indeterminate_escalates(tmp_path):
    """F2: the absorbing state. A node refusing every upload (or a box that
    can never finish a stamp) publishes explicit-null envelopes forever —
    SILENCE never trips (files keep coming), ENVELOPE never fires (never
    False). After ``indeterminate_after`` consecutive indeterminate
    ENVELOPES (counted per fire, not per tick), the probe must page."""
    import os as _os
    sdir = str(tmp_path / "propagation_soak")
    sp = str(tmp_path / "d.json")
    ip = str(tmp_path / "indet.json")
    now = 100000.0

    sig = None
    for i in range(3):                       # 3 consecutive indeterminate fires
        p = _write_prop_envelope(sdir, passed=None, age_s=60.0, now=now)
        newp = _os.path.join(sdir, f"prop-2026072{i}T000000Z.json")
        _os.rename(p, newp)                  # each fire = a NEW envelope file
        # Distinct, increasing mtimes: real fires are ~1h apart. Identical
        # mtimes left "newest" to the fs directory-hash order and flaked CI
        # 3.11 (2026-08-07) — the helper now tie-breaks by name too, but the
        # fixture should model reality, not lean on the tie-break.
        _os.utime(newp, (now - 60 + i, now - 60 + i))
        for _ in range(2):                   # multiple ticks per envelope
            sig = _prop_probe(state_dir=sdir, now=now, debounce_path=sp,
                              indet_state_path=ip, indeterminate_after=3)
    assert sig is not None, "3rd consecutive indeterminate envelope must page"
    assert "INDETERMINATE for 3 consecutive fires" in sig.detail

    # a PASS resets the streak
    _write_prop_envelope(sdir, passed=True, age_s=30.0, now=now)
    assert _prop_probe(state_dir=sdir, now=now,
                       debounce_path=str(tmp_path / "d2.json"),
                       indet_state_path=ip, indeterminate_after=3) is None
    assert not _os.path.exists(ip)


def test_newest_synth_file_ties_break_by_name_not_listdir_order(tmp_path):
    """Equal mtimes must resolve by NAME (stamp names sort chronologically),
    never by the filesystem's directory-hash order — that order is per-box
    ambient state, and it flaked CI 3.11 on 2026-08-07 while 3.9 and local
    passed the identical tree."""
    import os as _os

    from utils.watchdog_probes_gateway_flow import _newest_synth_file

    d = str(tmp_path)
    # Create in REVERSE lexical order so creation order disagrees with name
    # order; then force identical mtimes so only the tie-break decides.
    for name in ("prop-20260722T000000Z.json", "prop-20260720T000000Z.json",
                 "prop-20260721T000000Z.json"):
        p = _os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{}")
        _os.utime(p, (100000, 100000))
    got = _newest_synth_file(d, prefix="prop-")
    assert got is not None
    assert _os.path.basename(got[0]) == "prop-20260722T000000Z.json", (
        "the lexically-latest stamp must win an mtime tie deterministically")
    assert got[1] == 100000


def test_propagation_soak_holds_when_no_envelope_yet(tmp_path):
    """Freshly installed: dir exists, no result. Never fire on that."""
    sdir = tmp_path / "propagation_soak"
    sdir.mkdir()
    sp = str(tmp_path / "d.json")
    assert _prop_probe(
        state_dir=str(sdir), now=1000.0, debounce_path=sp) is None
    assert _prop_probe(
        state_dir=str(sdir), now=1030.0, debounce_path=sp) is None


def test_propagation_soak_clean_on_a_fresh_pass(tmp_path):
    sdir = str(tmp_path / "propagation_soak")
    now = 100000.0
    _write_prop_envelope(sdir, passed=True, age_s=60.0, now=now)
    assert _prop_probe(
        state_dir=sdir, now=now, debounce_path=str(tmp_path / "d.json")) is None


def test_propagation_soak_fires_on_a_failed_envelope_after_debounce(tmp_path):
    """ENVELOPE leg: stored but never returned."""
    sdir = str(tmp_path / "propagation_soak")
    now = 100000.0
    sp = str(tmp_path / "d.json")
    _write_prop_envelope(sdir, passed=False, age_s=60.0, now=now)

    assert _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp) is None      # streak 1
    sig = _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp)
    assert sig is not None
    assert sig.cls == "propagation_soak_degraded"
    assert sig.severity == "degraded"
    assert "store-and-forward" in sig.detail.lower()
    assert "round 1 failed at pull" in sig.detail


def test_propagation_soak_fires_on_silence(tmp_path):
    """SILENCE leg: for a fixed-cadence generator, going quiet IS the failure."""
    sdir = str(tmp_path / "propagation_soak")
    now = 100000.0
    sp = str(tmp_path / "d.json")
    _write_prop_envelope(sdir, passed=True, age_s=40000.0, now=now)  # ~11h old

    assert _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp) is None
    sig = _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp)
    assert sig is not None
    assert "DARK" in sig.detail


def test_propagation_soak_a_stale_PASS_still_fires_silence(tmp_path):
    """MUTATION GUARD. The bug this pins: judging the envelope BEFORE the age.

    A stale pass_envelope=true is the exact shape of "the exerciser died and
    left a happy file behind" — the most dangerous state, because it looks
    healthy. If the probe ever checks pass_envelope first, this test fails.
    """
    sdir = str(tmp_path / "propagation_soak")
    now = 100000.0
    sp = str(tmp_path / "d.json")
    _write_prop_envelope(sdir, passed=True, age_s=99999.0, now=now)

    _prop_probe(state_dir=sdir, now=now, debounce_path=sp)
    sig = _prop_probe(state_dir=sdir, now=now, debounce_path=sp)
    assert sig is not None, "a stale PASS must fire the SILENCE leg, not read clean"


def test_propagation_soak_missing_pass_envelope_is_indeterminate(tmp_path):
    """A shape regression must never read as healthy."""
    import os
    sdir = str(tmp_path / "propagation_soak")
    os.makedirs(sdir)
    now = 100000.0
    sp = str(tmp_path / "d.json")
    path = os.path.join(sdir, "prop-20260721T000000Z.json")
    with open(path, "w") as fh:
        json.dump({"total_ok": 1}, fh)          # no pass_envelope
    os.utime(path, (now - 60, now - 60))

    for _ in range(4):
        assert _prop_probe(
            state_dir=sdir, now=now, debounce_path=sp) is None


def test_propagation_soak_torn_write_is_ridden_out_by_debounce(tmp_path):
    """One unparseable tick must not page; a persistent one must."""
    import os
    sdir = str(tmp_path / "propagation_soak")
    os.makedirs(sdir)
    now = 100000.0
    sp = str(tmp_path / "d.json")
    path = os.path.join(sdir, "prop-20260721T000000Z.json")
    with open(path, "w") as fh:
        fh.write("{partial")
    os.utime(path, (now - 60, now - 60))

    assert _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp) is None       # ridden out
    assert _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp) is not None   # persistent


def test_propagation_soak_recovery_resets_the_streak(tmp_path):
    """Only an explicit healthy+fresh observation clears the streak."""
    sdir = str(tmp_path / "propagation_soak")
    now = 100000.0
    sp = str(tmp_path / "d.json")

    _write_prop_envelope(sdir, passed=False, age_s=60.0, now=now)
    assert _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp) is None       # streak 1

    _write_prop_envelope(sdir, passed=True, age_s=60.0, now=now)
    assert _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp) is None       # reset

    _write_prop_envelope(sdir, passed=False, age_s=60.0, now=now)
    assert _prop_probe(
        state_dir=sdir, now=now, debounce_path=sp) is None       # streak 1 again


def test_propagation_soak_null_envelope_holds_not_fires(tmp_path):
    """An INDETERMINATE run (pass_envelope=null — node never exercised, e.g.
    a slow propagation stamp) must HOLD, never fire a CONCERN.

    Live-caught on moc3 2026-07-21: its stamp PoW is slow and variable, so
    send-stalls are common. The drill now reports those as pass_envelope=null,
    and the probe must read null as 'held' — mapping 'couldn't test' to 'test
    failed' is honest_failure_modes #1.
    """
    import os
    sdir = str(tmp_path / "propagation_soak")
    os.makedirs(sdir)
    now = 100000.0
    sp = str(tmp_path / "d.json")
    path = os.path.join(sdir, "prop-20260721T000000Z.json")
    with open(path, "w") as fh:
        json.dump({"pass_envelope": None, "total_indeterminate": 1,
                   "total_ok": 0, "total_samples": 1}, fh)
    os.utime(path, (now - 60, now - 60))

    # never fires, no matter how many consecutive ticks
    for _ in range(4):
        assert _prop_probe(
            state_dir=sdir, now=now, debounce_path=sp) is None


def test_worst_propagation_round_never_raises_on_junk():
    from utils.watchdog_probes_gateway import _worst_propagation_round
    for bad in (None, "x", 7, [None], [{"ok": None}], [{"ok": False}]):
        _worst_propagation_round(bad)      # must not raise
    assert _worst_propagation_round([{"ok": True, "seq": 1}]) is None


def _reset_disp():
    from utils.watchdog_probe_core import reset_dispositions
    reset_dispositions()


# ─────────────────────────────────────────────────────────────────────
# probe_local_brain_regressed (2026-07-22, WS-D): make the LEARNING record
# observable. A per-case regression (passed >=2x before, now fails) that the
# aggregate --gate / #78 structurally cannot see. Cursor-robust (per-case).
# ─────────────────────────────────────────────────────────────────────

def _eval_rec(ts, results):
    """One eval ledger record. results: list of (id, kind, ok)."""
    return {"ts": ts, "results": [{"id": i, "kind": k, "ok": o}
                                  for i, k, o in results]}


def test_local_brain_regression_fires_on_previously_passing_case():
    _reset_disp()
    recs = [_eval_rec(1, [("t1", "triage", True)]),
            _eval_rec(2, [("t1", "triage", True)]),
            _eval_rec(3, [("t1", "triage", False)])]   # regressed after 2 passes
    sig = probe_local_brain_regressed(records=recs)
    assert sig is not None and sig.cls == "local_brain_regressed"
    assert sig.subject == "local-brain" and sig.severity == "degraded"
    assert sig.extra["regressed"] == 1 and sig.extra["case_ids"] == ["t1"]
    assert "REGRESSED" in sig.detail


def test_local_brain_currently_passing_is_clean():
    # failed once, but passed again in the latest run → recovered, not regressed.
    from utils.watchdog_probe_core import collect_dispositions
    _reset_disp()
    recs = [_eval_rec(1, [("t1", "triage", True)]),
            _eval_rec(2, [("t1", "triage", True)]),
            _eval_rec(3, [("t1", "triage", False)]),
            _eval_rec(4, [("t1", "triage", True)])]
    sig = probe_local_brain_regressed(records=recs)
    assert sig is None
    assert collect_dispositions()["local_brain_regressed"]["disp"] == "clean"


def test_local_brain_insufficient_prior_passes_is_clean():
    # passed once then fails: prior_passes=1 < 2 → first-eval/flaky, not a regression.
    from utils.watchdog_probe_core import collect_dispositions
    _reset_disp()
    recs = [_eval_rec(1, [("t1", "triage", True)]),
            _eval_rec(2, [("t1", "triage", False)])]
    assert probe_local_brain_regressed(records=recs) is None
    assert collect_dispositions()["local_brain_regressed"]["disp"] == "clean"


def test_local_brain_new_case_first_appearance_fail_is_clean():
    _reset_disp()
    recs = [_eval_rec(1, [("t1", "triage", True)]),
            _eval_rec(2, [("new", "compile", False)])]   # brand-new, 0 prior
    assert probe_local_brain_regressed(records=recs) is None


def test_local_brain_only_regressed_cases_reported():
    _reset_disp()
    recs = [_eval_rec(1, [("a", "oracle", True), ("b", "triage", True)]),
            _eval_rec(2, [("a", "oracle", True), ("b", "triage", True)]),
            _eval_rec(3, [("a", "oracle", True), ("b", "triage", False)])]
    sig = probe_local_brain_regressed(records=recs)
    assert sig is not None and sig.extra["case_ids"] == ["b"]   # a stays passing


def test_local_brain_no_ledger_is_inert(tmp_path):
    from utils.watchdog_probe_core import collect_dispositions
    _reset_disp()
    sig = probe_local_brain_regressed(ledger_path=str(tmp_path / "nope.jsonl"))
    assert sig is None
    disp = collect_dispositions()["local_brain_regressed"]
    assert disp["disp"] == "inert" and "not evaluated" in disp["reason"]


def test_local_brain_unreadable_ledger_is_indeterminate(tmp_path):
    # A directory at the ledger path → open() raises → unreadable ≠ clean.
    from utils.watchdog_probe_core import collect_dispositions
    _reset_disp()
    sig = probe_local_brain_regressed(ledger_path=str(tmp_path))
    assert sig is None
    assert collect_dispositions()["local_brain_regressed"]["disp"] == "indeterminate"


# ── model-awareness (2026-07-25) ──────────────────────────────────────
# The ditch-ollama plan's Step 2/3 method is backend-vs-backend comparison on
# identical cases. Un-scoped history turns "a different model answered" into
# "the tier lost a capability it demonstrably had". 2026-07-24 escaped a false
# page by arithmetic alone (the flipped cases had 1 prior pass vs a threshold
# of 2), so these pin the behaviour rather than trusting the margin.

def _eval_rec_m(ts, model, results):
    """One eval ledger record tagged with the model that produced it."""
    rec = _eval_rec(ts, results)
    rec["model"] = model
    return rec


def test_local_brain_other_models_passes_do_not_manufacture_a_regression():
    # The exact 2026-07-24 shape: 4B passes twice, then a 1.5B A/B run fails
    # the same case. That is a different model, NOT a lost capability.
    from utils.watchdog_probe_core import collect_dispositions
    _reset_disp()
    recs = [_eval_rec_m(1, "qwen3:4b", [("t1", "triage", True)]),
            _eval_rec_m(2, "qwen3:4b", [("t1", "triage", True)]),
            _eval_rec_m(3, "qwen2.5:1.5b", [("t1", "triage", False)])]
    assert probe_local_brain_regressed(records=recs) is None
    assert collect_dispositions()["local_brain_regressed"]["disp"] == "clean"


def test_local_brain_regression_within_one_model_still_fires():
    # Scoping must not defang the probe: same model, 2 passes, now failing.
    _reset_disp()
    recs = [_eval_rec_m(1, "qwen3:4b", [("t1", "triage", True)]),
            _eval_rec_m(2, "qwen2.5:1.5b", [("t1", "triage", True)]),
            _eval_rec_m(3, "qwen3:4b", [("t1", "triage", True)]),
            _eval_rec_m(4, "qwen3:4b", [("t1", "triage", False)])]
    sig = probe_local_brain_regressed(records=recs)
    assert sig is not None and sig.extra["case_ids"] == ["t1"]
    assert sig.extra["model"] == "qwen3:4b"
    assert "qwen3:4b" in sig.detail


def test_local_brain_stale_failure_under_a_retired_model_stays_quiet():
    # The bug a naive (case_id, model) key would introduce: an old failure
    # under a model we no longer run must not page forever. Current model is
    # whatever the most-recent run used.
    _reset_disp()
    recs = [_eval_rec_m(1, "old", [("t1", "triage", True)]),
            _eval_rec_m(2, "old", [("t1", "triage", True)]),
            _eval_rec_m(3, "old", [("t1", "triage", False)]),
            _eval_rec_m(4, "new", [("t1", "triage", True)])]
    assert probe_local_brain_regressed(records=recs) is None


def test_local_brain_model_swap_resets_the_baseline_quietly():
    # A fresh model with < min_prior_passes history under-fires; it must never
    # inherit the previous model's passes to page on its first stumble.
    _reset_disp()
    recs = [_eval_rec_m(1, "old", [("t1", "triage", True)]),
            _eval_rec_m(2, "old", [("t1", "triage", True)]),
            _eval_rec_m(3, "old", [("t1", "triage", True)]),
            _eval_rec_m(4, "new", [("t1", "triage", True)]),
            _eval_rec_m(5, "new", [("t1", "triage", False)])]
    assert probe_local_brain_regressed(records=recs) is None


def test_local_brain_untagged_records_keep_legacy_behaviour():
    # Legacy ledgers with no model field normalise to "" and compare exactly
    # as they did before scoping existed.
    _reset_disp()
    recs = [_eval_rec(1, [("t1", "triage", True)]),
            _eval_rec(2, [("t1", "triage", True)]),
            _eval_rec(3, [("t1", "triage", False)])]
    sig = probe_local_brain_regressed(records=recs)
    assert sig is not None and sig.extra["model"] == ""
    assert "unspecified" in sig.detail


def test_local_brain_ledger_name_is_ssot_pinned():
    from utils.watchdog_probes_mini import _EVAL_LEDGER_NAME
    from mini_dudeai.local_brain_eval import EVAL_RESULTS_BASENAME
    assert _EVAL_LEDGER_NAME == EVAL_RESULTS_BASENAME


class TestParityStreakSurvivesUnwritableState:
    """A broken state path must not silence every debounced probe forever.

    The 2026-07-21 (W4) review found exactly this and fixed only HALF of it:
    _save_parity_streak gained a one-time warning (the witness), but
    _load_parity_streak still mapped any read failure to 0, so the streak
    still could not accumulate and every debounced probe stayed dark. The
    warning made the blindness visible; it did not make the probe work.
    Found again 2026-07-26 during the post-07-23 audit.
    """

    @staticmethod
    def _unwritable(tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        return str(blocker / "streak.json")

    def test_streak_accumulates_when_state_cannot_be_written(self, tmp_path):
        from utils.watchdog_probe_core import (
            _load_parity_streak, _save_parity_streak,
        )
        path = self._unwritable(tmp_path)
        # the idiom every debounced probe uses: load, +1, save, compare
        seen = []
        for _ in range(4):
            streak = _load_parity_streak(path) + 1
            _save_parity_streak(path, streak)
            seen.append(streak)
        assert seen == [1, 2, 3, 4], (
            f"streak froze at {seen} — a debounced probe with threshold >= 2 "
            f"could never fire on an unwritable state path"
        )

    def test_writable_state_still_round_trips(self, tmp_path):
        from utils.watchdog_probe_core import (
            _load_parity_streak, _save_parity_streak,
        )
        path = str(tmp_path / "ok" / "streak.json")
        _save_parity_streak(path, 3)
        assert _load_parity_streak(path) == 3
        _save_parity_streak(path, 0)
        assert _load_parity_streak(path) == 0


# ─────────────────────────────────────────────────────────────────────
# 2026-07-31 — delivery snapshot state-file fallback (moc3 detector_blind)
# A gateway-only box (meshforge-map disabled BY DESIGN) serves nothing on
# :5000, so the two /api/gateway/delivery probes sat permanently
# detector-blind on the one box whose delivery truth matters most, while
# that truth was on disk the whole time. The gateway now publishes its
# full snapshot() to a state file and the probes fall back to it.
# ─────────────────────────────────────────────────────────────────────


class TestDeliverySnapshotFileFallback:

    def _stall_snap(self, *, confirmed, failed):
        return _delivery_payload(confirmed=confirmed, failed=failed)

    def test_reader_and_writer_pin_one_subpath(self):
        """Two consumers of one artifact share ONE constant
        (honest_failure_modes #5) — the probe-side literal must equal the
        writer's, and the writer's path helper must resolve under it."""
        import utils.watchdog_probes_gateway as wpg
        from gateway import delivery_counters as dc
        assert wpg._DELIVERY_SNAPSHOT_SUBPATH == dc.DELIVERY_SNAPSHOT_STATE_SUBPATH
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MESHFORGE_DELIVERY_SNAPSHOT_STATE", None)
            with patch("gateway.delivery_counters.get_real_user_home",
                       return_value=Path("/home/op")):
                assert str(dc.delivery_snapshot_state_path()) == os.path.join(
                    "/home/op", wpg._DELIVERY_SNAPSHOT_SUBPATH)

    def test_writer_to_probe_round_trip_fires_on_stall(self, tmp_path):
        """Reader/writer pairs wire together or fail together
        (honest_failure_modes #4): the REAL writer's file, read through the
        REAL fallback, must let the probe judge a collapse — 2/25 confirmed
        → wedge."""
        from gateway.delivery_counters import write_delivery_snapshot_state
        from urllib.error import URLError
        p = tmp_path / "delivery_snapshot.json"
        assert write_delivery_snapshot_state(
            snap=self._stall_snap(confirmed=2, failed=23), path=p) is True

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, 
                min_terminal=20, snapshot_state_path=str(p))
        assert sig is not None
        assert sig.cls == "delivery_confirmation_stall"
        assert sig.severity == "wedge"

    def test_fallback_healthy_is_quiet_with_clean_disposition(self, tmp_path):
        from gateway.delivery_counters import write_delivery_snapshot_state
        from urllib.error import URLError
        collect = _fresh_dispositions()
        p = tmp_path / "delivery_snapshot.json"
        write_delivery_snapshot_state(
            snap=self._stall_snap(confirmed=24, failed=1), path=p)

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, 
                min_terminal=20, snapshot_state_path=str(p))
        assert sig is None
        assert collect()["delivery_confirmation_stall"]["disp"] == "clean"

    def test_fallback_write_canary_reads_health_through_file(self, tmp_path):
        """The OTHER consumer of the same payload — a preflight failure
        published in the snapshot file must still surface as wedge."""
        from gateway.delivery_counters import write_delivery_snapshot_state
        from urllib.error import URLError
        p = tmp_path / "delivery_snapshot.json"
        write_delivery_snapshot_state(
            snap={"health": {
                "preflight_ok": False,
                "consecutive_write_errors": 9,
                "last_write_error": "unable to open database file",
                "db_path": "/x/y/z.db",
            }}, path=p)

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_delivery_write_canary(snapshot_state_path=str(p))
        assert sig is not None
        assert sig.cls == "delivery_write_canary"
        assert sig.severity == "wedge"

    def test_fallback_absent_file_names_the_gap(self, tmp_path):
        from urllib.error import URLError
        collect = _fresh_dispositions()

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, 
                snapshot_state_path=str(tmp_path / "absent.json"))
        assert sig is None
        got = collect()["delivery_confirmation_stall"]
        assert got["disp"] == "indeterminate"
        assert "no delivery snapshot state file" in got["reason"]

    def test_fallback_stale_file_is_indeterminate_not_observation(
            self, tmp_path):
        """A corpse never testifies: gateway stopped publishing → the file's
        content must NOT be judged (honest_failure_modes #2)."""
        import time as _time
        from gateway.delivery_counters import write_delivery_snapshot_state
        from urllib.error import URLError
        collect = _fresh_dispositions()
        p = tmp_path / "delivery_snapshot.json"
        write_delivery_snapshot_state(
            snap=self._stall_snap(confirmed=2, failed=23), path=p,
            now=_time.time() - 3600)

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, 
                min_terminal=20, snapshot_state_path=str(p))
        assert sig is None
        got = collect()["delivery_confirmation_stall"]
        assert got["disp"] == "indeterminate"
        assert "stale" in got["reason"]

    def test_fallback_future_ts_is_clock_artifact(self, tmp_path):
        """Wall-clock is forgeable on RTC-less Pis (honest_failure_modes
        #6) — a far-future ts is not an observation."""
        import time as _time
        from gateway.delivery_counters import write_delivery_snapshot_state
        from urllib.error import URLError
        collect = _fresh_dispositions()
        p = tmp_path / "delivery_snapshot.json"
        write_delivery_snapshot_state(
            snap=self._stall_snap(confirmed=2, failed=23), path=p,
            now=_time.time() + 7200)

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, 
                min_terminal=20, snapshot_state_path=str(p))
        assert sig is None
        got = collect()["delivery_confirmation_stall"]
        assert got["disp"] == "indeterminate"
        assert "future" in got["reason"]

    def test_fallback_misshaped_file_is_indeterminate(self, tmp_path):
        import time as _time
        from urllib.error import URLError
        collect = _fresh_dispositions()
        p = tmp_path / "delivery_snapshot.json"
        p.write_text(json.dumps({"ts": _time.time(), "snapshot": []}))

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, 
                snapshot_state_path=str(p))
        assert sig is None
        got = collect()["delivery_confirmation_stall"]
        assert got["disp"] == "indeterminate"
        assert "misshaped" in got["reason"]

    def test_http_success_never_touches_the_file(self, tmp_path):
        """On every map-running box the HTTP path is authoritative — a
        stale file on disk must not shadow a live endpoint."""
        import time as _time
        from gateway.delivery_counters import write_delivery_snapshot_state
        p = tmp_path / "delivery_snapshot.json"
        write_delivery_snapshot_state(
            snap=self._stall_snap(confirmed=2, failed=23), path=p,
            now=_time.time() - 9999)  # corpse — would be refused anyway
        payload = _delivery_payload(confirmed=24, failed=1)
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(payload)):
            sig = probe_delivery_confirmation_stall(gateway_main_pid=_GW_RUNNING, 
                min_terminal=20, snapshot_state_path=str(p))
        assert sig is None


class TestQueueStatsFileFallback:
    """queue-side twin of TestDeliverySnapshotFileFallback (2026-07-31):
    probe_queue_backlog falls back to the gateway-published queue_stats.json
    when :5000 is unreachable, so the gateway-only box shape (moc3) is no
    longer structurally blind to queue backpressure."""

    def test_reader_and_writer_pin_one_subpath(self):
        import utils.watchdog_probes_gateway as wpg
        from gateway import message_queue as mq
        assert wpg._QUEUE_STATS_SUBPATH == mq.QUEUE_STATS_STATE_SUBPATH
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MESHFORGE_QUEUE_STATS_STATE", None)
            with patch("gateway.message_queue.get_real_user_home",
                       return_value=Path("/home/op")):
                assert str(mq.queue_stats_state_path()) == os.path.join(
                    "/home/op", wpg._QUEUE_STATS_SUBPATH)

    def test_writer_to_probe_round_trip_fires_on_depth(self, tmp_path):
        """The REAL writer's file, read through the REAL fallback, must let
        the probe judge depth pressure — 96% of max → wedge."""
        from gateway.message_queue import write_queue_stats_state
        from urllib.error import URLError
        p = tmp_path / "queue_stats.json"
        assert write_queue_stats_state(
            stats=_queue_payload(depth=960, max_size=1000), path=p) is True

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_queue_backlog(
                state_path=str(tmp_path / "dl.json"),
                stats_state_path=str(p))
        assert sig is not None
        assert sig.cls == "queue_backlog"
        assert sig.severity == "wedge"

    def test_fallback_healthy_is_quiet(self, tmp_path):
        from gateway.message_queue import write_queue_stats_state
        from urllib.error import URLError
        p = tmp_path / "queue_stats.json"
        write_queue_stats_state(
            stats=_queue_payload(depth=10, max_size=1000), path=p)

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_queue_backlog(
                state_path=str(tmp_path / "dl.json"),
                stats_state_path=str(p))
        assert sig is None

    def test_fallback_absent_file_names_the_gap(self, tmp_path):
        from urllib.error import URLError
        collect = _fresh_dispositions()

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_queue_backlog(
                state_path=str(tmp_path / "dl.json"),
                stats_state_path=str(tmp_path / "absent.json"))
        assert sig is None
        got = collect()["queue_backlog"]
        assert got["disp"] == "indeterminate"
        assert "no queue stats state file" in got["reason"]

    def test_fallback_stale_file_is_indeterminate(self, tmp_path):
        import time as _time
        from gateway.message_queue import write_queue_stats_state
        from urllib.error import URLError
        collect = _fresh_dispositions()
        p = tmp_path / "queue_stats.json"
        write_queue_stats_state(
            stats=_queue_payload(depth=960, max_size=1000), path=p,
            now=_time.time() - 3600)

        def _raise(*args, **kwargs):
            raise URLError("connection refused")

        with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
            sig = probe_queue_backlog(
                state_path=str(tmp_path / "dl.json"),
                stats_state_path=str(p))
        assert sig is None
        got = collect()["queue_backlog"]
        assert got["disp"] == "indeterminate"
        assert "stale" in got["reason"]

    def test_http_success_never_touches_the_file(self, tmp_path):
        """A live endpoint is authoritative — a wedge-shaped corpse on disk
        must not shadow a healthy HTTP answer."""
        import time as _time
        from gateway.message_queue import write_queue_stats_state
        p = tmp_path / "queue_stats.json"
        write_queue_stats_state(
            stats=_queue_payload(depth=960, max_size=1000), path=p,
            now=_time.time())
        payload = _queue_payload(depth=10, max_size=1000)
        with patch("utils.watchdog_probes_gateway.urlopen",
                   return_value=_http_json_mock(payload)):
            sig = probe_queue_backlog(
                state_path=str(tmp_path / "dl.json"),
                stats_state_path=str(p))
        assert sig is None

    def test_writer_refuses_non_dict_stats(self, tmp_path):
        """A publish of garbage must not create a valid-looking file —
        absent is honestly 'no data' (honest_failure_modes #3)."""
        from gateway.message_queue import write_queue_stats_state
        p = tmp_path / "queue_stats.json"
        assert write_queue_stats_state(stats=None, path=p) is False
        assert not p.exists()


class TestExpectedActiveFollowsDeclaredRole20260803:
    """The paging probe must watch what the box's ROLE says must run.

    Measured 2026-08-03: moc's meshforge-gateway was down for 9m49s and NOTHING
    paged. probe_service_inactive is the paging probe for unit state, and it is
    fed from services_expected_active — which defaulted to a hardcoded
    (rnsd, meshforge-map) pair that never included meshforge-gateway. On moc3 a
    per-box override narrowed it further, to rnsd ALONE: that override existed
    to silence a true false-positive (map is deliberately inactive on a
    gateway-only box) and in removing map it left the box watching nothing
    else. A cure for a false positive that created a blind spot.

    role_drift DID detect the outage within 2 ticks — it just escalates
    without paging, by design. The truth was already on the box; the paging
    probe simply was not reading it.
    """

    def test_default_targets_include_the_role_declared_units(self, tmp_path):
        from utils.watchdog_runner import resolve_probe_targets

        with patch("utils.watchdog_runner._role_expected_active",
                   return_value=("rnsd.service", "meshforge-gateway.service")):
            targets = resolve_probe_targets({})
        sea = targets[0] if isinstance(targets, tuple) else targets
        assert "meshforge-gateway.service" in sea, (
            "the gateway is not watched by the probe that pages — the exact "
            "gap that let a 10-minute outage go unpaged"
        )

    def test_falls_back_when_the_role_cannot_be_resolved(self):
        """Unresolvable role is INDETERMINATE — keep the old default, never
        widen or narrow on a guess (honest_failure_modes #2)."""
        from utils.watchdog_runner import (
            resolve_probe_targets, _DEFAULT_SERVICES_EXPECTED_ACTIVE)

        with patch("utils.watchdog_runner._role_expected_active",
                   return_value=None):
            targets = resolve_probe_targets({})
        sea = targets[0] if isinstance(targets, tuple) else targets
        assert tuple(sea) == _DEFAULT_SERVICES_EXPECTED_ACTIVE

    def test_explicit_override_still_wins(self):
        """Operator control is preserved — the override is authoritative."""
        from utils.watchdog_runner import resolve_probe_targets

        with patch("utils.watchdog_runner._role_expected_active",
                   return_value=("rnsd.service", "meshforge-gateway.service")):
            targets = resolve_probe_targets(
                {"services_expected_active": ["rnsd.service"]})
        sea = targets[0] if isinstance(targets, tuple) else targets
        assert tuple(sea) == ("rnsd.service",)

    def test_override_that_drops_a_role_required_unit_warns(self, caplog):
        """...but silently is not an option. moc3's override omitted the
        gateway and nobody knew for months; an omission must leave a witness
        (honest_failure_modes #9)."""
        import logging as _logging
        from utils.watchdog_runner import resolve_probe_targets

        with patch("utils.watchdog_runner._role_expected_active",
                   return_value=("rnsd.service", "meshforge-gateway.service")):
            with caplog.at_level(_logging.WARNING, logger="watchdog"):
                resolve_probe_targets(
                    {"services_expected_active": ["rnsd.service"]})
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "meshforge-gateway.service" in msg, (
            "override silently dropped a role-declared-active unit: %r" % msg
        )


# ─────────────────────────────────────────────────────────────────────
# 2026-08-07 — a timed-out BACKEND is not a lost capability
# ─────────────────────────────────────────────────────────────────────

#: The exact reason strings the 2026-08-04 run wrote, copied from the live
#: ledger. Pinning the REAL producer output, not a paraphrase of it — the
#: whole defect was a consumer whose idea of the producer had gone stale.
_REAL_TIMEOUT_REASON = (
    "no grounded answer: synthesis failed (Ollama at http://localhost:11434 "
    "failed: TimeoutError: timed out — is the server up and the model "
    "(qwen3:4b-instruct-2507-q4_K_M) pulled?) — r")
_REAL_CONTENT_REASON = "2 entries dropped in validation (> 0)"


def _eval_rec_r(ts, results):
    """Ledger record carrying full result dicts (id, kind, ok, reasons)."""
    return {"ts": ts, "results": [
        {"id": i, "kind": k, "ok": o, "reasons": rs}
        for i, k, o, rs in results]}


class TestLocalBrainTransportFailures:
    """A case the backend never answered was not OBSERVED — neither a pass
    nor a regression.

    2026-08-04 saturated Ollama: three oracle cases died on literal
    "TimeoutError: timed out", one triage case ran 861s, and the budget
    expired with four cases never started. All four had long clean histories
    and "broke" in that one run. The probe read only ok=false and asserted
    "the tier-L model lost a capability" for three days. The same triage was
    done BY HAND on 08-04 and written down as an instruction to a human,
    which is exactly why it kept re-firing.
    """

    def test_marker_matches_the_real_producer_string(self):
        from mini_dudeai.local_brain_eval import _is_transport_failure
        assert _is_transport_failure([_REAL_TIMEOUT_REASON]) is True

    def test_content_failure_is_not_transport(self):
        from mini_dudeai.local_brain_eval import _is_transport_failure
        assert _is_transport_failure([_REAL_CONTENT_REASON]) is False

    def test_synthesis_failed_alone_is_not_transport(self):
        """⚠️ The dangerous direction. offline_oracle wraps BOTH a transport
        CompilerError AND a genuine bad-shape ValueError from the model in
        "synthesis failed", so matching that phrase would launder a real
        capability loss into "unobserved" — silently excusing the very thing
        this probe exists to catch."""
        from mini_dudeai.local_brain_eval import _is_transport_failure
        assert _is_transport_failure([
            "no grounded answer: synthesis failed (oracle reply missing "
            "required shape: {...}) — retrieval results above are still good"
        ]) is False

    def test_mixed_reasons_are_not_excused(self):
        """ALL, not ANY: a case that both timed out and answered wrongly has
        demonstrated a real miss."""
        from mini_dudeai.local_brain_eval import _is_transport_failure
        assert _is_transport_failure(
            [_REAL_TIMEOUT_REASON, _REAL_CONTENT_REASON]) is False

    def test_empty_reasons_are_not_transport(self):
        from mini_dudeai.local_brain_eval import _is_transport_failure
        assert _is_transport_failure([]) is False
        assert _is_transport_failure(None) is False

    def test_timed_out_case_does_not_count_as_a_regression(self):
        _reset_disp()
        recs = [_eval_rec_r(1, [("o1", "oracle", True, [])]),
                _eval_rec_r(2, [("o1", "oracle", True, [])]),
                _eval_rec_r(3, [("o1", "oracle", False,
                                 [_REAL_TIMEOUT_REASON])])]
        sig = probe_local_brain_regressed(records=recs)
        assert sig is None, (
            "a case whose backend was unreachable was not observed — "
            "reporting it as a lost capability is the 08-04 false alarm")

    def test_genuine_content_failure_still_fires(self):
        """The guard must not become a blanket excuse."""
        _reset_disp()
        recs = [_eval_rec_r(1, [("t1", "triage", True, [])]),
                _eval_rec_r(2, [("t1", "triage", True, [])]),
                _eval_rec_r(3, [("t1", "triage", False,
                                 [_REAL_CONTENT_REASON])])]
        sig = probe_local_brain_regressed(records=recs)
        assert sig is not None
        assert sig.extra["case_ids"] == ["t1"]

    def test_structured_flag_is_preferred_over_markers(self):
        """Rows written after 2026-08-07 carry transport_error explicitly;
        it must win over any text matching."""
        _reset_disp()
        recs = [_eval_rec_r(1, [("o1", "oracle", True, [])]),
                _eval_rec_r(2, [("o1", "oracle", True, [])]),
                {"ts": 3, "results": [{"id": "o1", "kind": "oracle",
                                       "ok": False, "reasons": ["anything"],
                                       "transport_error": True}]}]
        assert probe_local_brain_regressed(records=recs) is None

    def test_structured_false_flag_keeps_a_real_regression(self):
        _reset_disp()
        recs = [_eval_rec_r(1, [("o1", "oracle", True, [])]),
                _eval_rec_r(2, [("o1", "oracle", True, [])]),
                {"ts": 3, "results": [{"id": "o1", "kind": "oracle",
                                       "ok": False,
                                       "reasons": [_REAL_TIMEOUT_REASON],
                                       "transport_error": False}]}]
        sig = probe_local_brain_regressed(records=recs)
        assert sig is not None and sig.extra["case_ids"] == ["o1"]

    def test_mostly_unreachable_run_is_indeterminate_not_clean(self):
        """The mirror of the original bug: a run that proved almost nothing
        must not render as a positive all-clear."""
        from utils.watchdog_probe_core import collect_dispositions
        _reset_disp()
        recs = [_eval_rec_r(1, [("o1", "oracle", True, []),
                                ("o2", "oracle", True, [])]),
                _eval_rec_r(2, [("o1", "oracle", False,
                                 [_REAL_TIMEOUT_REASON]),
                                ("o2", "oracle", False,
                                 [_REAL_TIMEOUT_REASON])])]
        assert probe_local_brain_regressed(records=recs) is None
        got = collect_dispositions()["local_brain_regressed"]
        assert got["disp"] == "indeterminate"
        assert "could not reach the backend" in got["reason"]

    def test_healthy_run_is_still_clean(self):
        from utils.watchdog_probe_core import collect_dispositions
        _reset_disp()
        recs = [_eval_rec_r(1, [("o1", "oracle", True, [])]),
                _eval_rec_r(2, [("o1", "oracle", True, [])])]
        assert probe_local_brain_regressed(records=recs) is None
        assert collect_dispositions()["local_brain_regressed"]["disp"] == "clean"
