"""Per-box watchdog daemon — ticks every 30s, runs probes, writes JSON.

Companion to ``utils/watchdog_probes.py``. Entry point: ``main()`` runs
the loop until SIGTERM/SIGINT. Writes its aggregated signal state to
``/var/lib/meshforge/watchdog.json`` (atomic-rename) so the map server's
``/api/status`` endpoint can read it without locking. Federation already
polls ``/api/status`` across peers — watchdog signals ride that channel
to the ``/fleet`` rollup with no new HTTP plumbing (Issue #54
peer_name correlation labels rows for free).

Phase 1 commitment per the approved plan:

* No service restarts. Signal only. The watchdog is the observability
  layer; whether to act on signals is a human call this week and a
  later opt-in flag (Phase 3).
* Edge-transition logging: a signal that stays active stays quiet on
  the journal; only first-seen and cleared transitions hit INFO/WARNING.
* Closed enum of failure classes — see ``watchdog_probes.SIGNAL_CLASSES``.

Configuration:

* Probe target list is hard-coded conservatively here (the services and
  endpoints that exist on every fleet box). A future refinement could
  read per-box config from ``~/.config/meshforge/watchdog.json``;
  Phase 1 keeps it minimal so the deployment is one ``systemctl enable``
  per box, no config writing.

Why root: ``/proc/<pid>/task/<pid>/stack`` requires CAP_SYS_PTRACE on
most kernels, and the wedge-detection probe is the highest-ROI signal.
The runner does no other privileged work and never writes outside
``/var/lib/meshforge/``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.rns_status_parser import run_rnstatus
from utils.watchdog_probes import (
    SIGNAL_CLASSES,
    Signal,
    collect_dispositions,
    note_disposition,
    reset_dispositions,
    probe_aredn_organ_undeclared,
    probe_aredn_source_dark,
    probe_calibration_drift,
    probe_dream_ratification_stalled,
    probe_local_brain_regressed,
    probe_channel_feed_dark,
    probe_mqtt_root_drift,
    probe_cron_verdict_stale,
    probe_fleet_box_unreachable,
    probe_host_frozen,
    probe_claw_device_dark,
    probe_claw_battery_low,
    probe_claw_rf_silent,
    probe_claw_watched_node_silent,
    probe_claw_uplink_node_moved,
    probe_router_scout_degraded,
    probe_ntfy_loopback,
    probe_ntfy_ack_stale,
    probe_delivery_confirmation_stall,
    probe_delivery_write_canary,
    probe_gateway_delivery_degraded,
    probe_gateway_dup_degraded,
    probe_gateway_dual_homed_exposure,
    probe_lxmf_propagation_node_dark,
    probe_lxmf_propagation_unused,
    probe_fd_exhaustion,
    probe_history_write_failure,
    probe_inherited_app_drift,
    probe_host_memory_pressure,
    probe_memory_cap_engaged,
    probe_kernel_reboot_pending,
    probe_memory_index_oversize,
    probe_meshtasticd_phoneapi_wedge,
    probe_oracle_delivery_degraded,
    probe_meshtasticd_vsz_leak,
    probe_phoneapi_tcp_leak,
    probe_queue_backlog,
    probe_rules_seed_drift,
    probe_foundation_drift,
    probe_parity_drift,
    probe_rns_version_drift,
    probe_dep_version_drift,
    probe_dep_install_fragmented,
    probe_rns_env_coherence,
    probe_role_drift,
    probe_http_local,
    probe_lxmf_process_wedge,
    probe_main_thread_wedge,
    probe_rns_interface_down_peer_reachable,
    probe_rns_namespace_collision,
    probe_nomadnet_crashloop,
    probe_user_unit_inactive,
    probe_user_timer_unit_failing,
    probe_rns_rpc_responsive,
    probe_rns_shared_instance_responsive,
    probe_resource_canary_degraded,
    probe_service_inactive,
    probe_synth_soak_degraded,
    probe_propagation_soak_degraded,
    probe_tracer_peer_unreachable,
    signal_to_dict,
)
# Signal lifecycle + the unobserved HOLD (split 2026-07-26, MF025). Re-exported
# here so existing importers of SignalTracker keep working unchanged.
from utils.watchdog_tracker import (  # noqa: F401  (re-export)
    SignalTracker,
    observed_only,
)
from utils.watchdog_actions import (
    Phase2Config,
    RestartHistory,
    decide_restarts,
    execute_restart,
    execute_reconverge,
    parse_phase2_config,
)


logger = logging.getLogger("watchdog")

DEFAULT_OUTPUT_PATH = Path("/var/lib/meshforge/watchdog.json")
DEFAULT_TICK_S = 30.0

# Optional per-box override file. JSON, lives in operator home next to
# the other meshforge config. Allows boxes with intentionally-different
# service topology (moc3 is gateway-only — no meshforge-map.service)
# to suppress false-positive signals without changing the closed-enum
# probe set. Schema:
#   {
#     "services_expected_active": ["rnsd.service"],       # optional, full replacement
#     "services_wedge_check": ["meshforge-map.service"],   # optional, full replacement
#     "http_port": 5000,                                   # optional
#     "phase2_auto_restart": {                             # optional, default OFF
#       "enabled": false,                                  # master switch
#       "dry_run": false,                                  # log decisions, skip execute
#       "allowlist": [
#         { "signal_class": "rns_shared_instance_unresponsive",
#           "service": "rnsd.service",
#           "consecutive_ticks": 5,
#           "cooldown_s": 600,
#           "max_restarts_per_hour": 2 },
#         { "signal_class": "role_drift",                     # v3 self-healing
#           "service": "<this box's role, e.g. full-gateway>",# = the signal subject
#           "action": "reconverge",                          # provision_role.py --apply
#           "consecutive_ticks": 5,
#           "cooldown_s": 1800,                               # generous: re-applies whole role
#           "max_restarts_per_hour": 1 }
#       ]
#     }
#   }
# Missing keys fall back to hardcoded defaults. Missing file = pure
# defaults. Malformed file logs a WARNING and falls back to defaults —
# never blocks the watchdog from starting.
#
# Phase 2 auto-restart: `enabled` MUST be literal JSON true (truthy
# non-bools rejected); see `watchdog_actions.parse_phase2_config` for
# the safety-gate inventory. Default state across the fleet on
# 2026-05-21 ship: Phase 2 OFF on every box.
DEFAULT_CONFIG_FILE = Path("~/.config/meshforge/watchdog.json")

# Services every fleet box has and that should be active in the
# standard ``full``/``gateway`` profiles. Boxes that intentionally don't
# run a unit (e.g. moc3 is gateway-only, meshforge-map is disabled
# there) will surface a ``service_inactive`` signal at severity=degraded
# — operator can suppress per-box via a future config flag, but in
# Phase 1 we want the signal so an unintentional inactive doesn't
# silently match an intentional one.
_DEFAULT_SERVICES_EXPECTED_ACTIVE: Tuple[str, ...] = (
    "rnsd.service",
    "meshforge-map.service",
)

# Services to probe for main-thread wedge (Issue #68). Limited to the
# ones most likely to wedge on a stuck rnsd Unix socket — these all
# call RNS.Reticulum() on their main thread.
_DEFAULT_SERVICES_WEDGE_CHECK: Tuple[str, ...] = (
    "meshforge-map.service",
)


# ─────────────────────────────────────────────────────────────────────
# Probe dispatch
# ─────────────────────────────────────────────────────────────────────


def run_all_probes(
    *,
    rns_instance_name: Optional[str],
    services_expected_active: Tuple[str, ...] = _DEFAULT_SERVICES_EXPECTED_ACTIVE,
    services_wedge_check: Tuple[str, ...] = _DEFAULT_SERVICES_WEDGE_CHECK,
    http_port: int = 5000,
) -> List[Signal]:
    """Run every probe, return aggregated signals.

    Order is deliberate: cheap probes first so an expensive probe
    failing late doesn't delay the cheap ones' results in case of a
    future timeout/parallelization refactor.

    Coverage side-channel (fleet-truth Phase 0): the disposition recorder
    is reset here; runner-level gates note ``inert`` for the probes they
    skip; probes note their own clean/inert/indeterminate at return sites.
    ``build_coverage`` assembles the per-class map after the tick.
    """
    reset_dispositions()
    signals: List[Signal] = []

    # Service-state probes — cheap, one subprocess each.
    for unit in services_expected_active:
        sig = probe_service_inactive(unit)
        if sig is not None:
            signals.append(sig)

    # NomadNet USER-unit crashloop (2026-06-19) — probe_service_inactive is
    # structurally blind to user units (root/system-context systemctl can't
    # see them, and a thrashing unit is neither inactive nor failed), so the
    # NRestarts=7842 loop went 10 days silent. Reads systemd restart-counter
    # lines under the USER_UNIT= journal field (root-direct, no sudo). NOT
    # gated on services_expected_active (user units aren't system units);
    # self-guards None on a healthy/disabled/never-installed unit and when
    # journalctl is unobservable.
    sig = probe_nomadnet_crashloop()
    if sig is not None:
        signals.append(sig)

    # Enrolled always-on user .service daemons that are NOT running — the
    # parked-failed / stopped-and-forgotten / user-manager-down modes the
    # crashloop probe's own docstring names as uncovered. Bus-free reads:
    # default.target.wants symlinks (enrollment) vs /run/user invocation
    # markers (liveness). Self-guards INERT with no operator/enrollment.
    sig = probe_user_unit_inactive()
    if sig is not None:
        signals.append(sig)

    # Timer-triggered user jobs that fail on EVERY firing. The leg above
    # cannot judge these (a oneshot is inactive between firings by design and
    # timers carry no invocation marker), which is how kiai's tracer failed
    # every 10 min for a week in silence. Outcome-based: repeated failures,
    # newest fresh, and no success since.
    sig = probe_user_timer_unit_failing()
    if sig is not None:
        signals.append(sig)

    # RNS namespace collision — single ``ss -xnpl`` call.
    if rns_instance_name:
        sig = probe_rns_namespace_collision(rns_instance_name)
        if sig is not None:
            signals.append(sig)
    else:
        note_disposition("rns_namespace_collision", "inert",
                         reason="no rns instance_name declared on this box")

    # RNS shared-instance responsive — Unix socket connect with timeout.
    # Catches today's (2026-05-21 moc1) wedge class where rnsd is alive
    # but new shared-instance connects hang. Cheap: 2s timeout worst case.
    if rns_instance_name:
        sig = probe_rns_shared_instance_responsive(rns_instance_name)
        if sig is not None:
            signals.append(sig)
    else:
        note_disposition("rns_shared_instance_unresponsive", "inert",
                         reason="no rns instance_name declared on this box")

    # RNS rnstatus-consuming probes share ONE bounded rnstatus call so a
    # wedged rnsd can't stall the 30s tick with two long-timeout
    # subprocesses. 8s is plenty for a healthy rnstatus (~1-2s) and well
    # under the tick. Not gated on rns_instance_name: rnstatus enumerates
    # interfaces regardless, and both probes self-guard when rnsd is
    # unreachable (binary lookup fails fast on RNS-less boxes — no
    # subprocess spawned).
    rns_status = run_rnstatus(timeout_s=8.0)

    # RNS RPC unresponsive (2026-05-30): rnstatus itself hung though the
    # shared-instance socket accepts — the wedged-rnsd-RPC class the
    # connect-only shared-instance probe can't see (#68/#69 family).
    sig = probe_rns_rpc_responsive(rnstatus_status=rns_status)
    if sig is not None:
        signals.append(sig)

    # RNS interface Down while peer reachable (2026-05-30 incident).
    # Bounded TCP-connect to any Down TCPInterface's peer. Reuses the
    # shared rnstatus result; self-guards on parse_error when rnsd is
    # unreachable. Catches the stuck-uplink class directly at the
    # interface layer (previously only via tracer_peer_unreachable).
    sig = probe_rns_interface_down_peer_reachable(rnstatus_status=rns_status)
    if sig is not None:
        signals.append(sig)

    # Main-thread wedge — /proc read, root only. Now scans ALL task
    # threads (not just main) so worker-thread wedges (today's class)
    # surface too.
    for unit in services_wedge_check:
        sig = probe_main_thread_wedge(unit)
        if sig is not None:
            signals.append(sig)

    # User-scope RNS-using processes — root can't easily query
    # `systemctl --user`, so walk /proc and match by cmdline pattern.
    # Catches meshforge-echo.service and similar user-scope units that
    # would otherwise need DBUS env setup to query by name.
    signals.extend(probe_lxmf_process_wedge())

    # meshtasticd VSZ leak (upstream meshtastic/firmware#10468) — guards the
    # weekly restart band-aid: fires only past the weekly leak envelope
    # (768 GB default), i.e. meshtasticd-restart.timer missed or the leak
    # rate worsened. Unconditional: self-guards None when meshtasticd isn't
    # running; Pi4/SPI boxes idle ~0.3 GB and cannot trip it.
    sig = probe_meshtasticd_vsz_leak()
    if sig is not None:
        signals.append(sig)

    # HTTP local probe — only if the map service is supposed to be
    # active. A bound-but-wedged port is the Issue #61 class.
    if "meshforge-map.service" in services_expected_active:
        sig = probe_http_local(
            "meshforge-map.service",
            port=http_port,
            path="/healthz",
        )
        if sig is not None:
            signals.append(sig)

        # FD-exhaustion probe (Issue #73) — proactive companion to the
        # http_local wedge probe above. Catches a leaking fd count
        # climbing toward the soft RLIMIT_NOFILE *before* it starves
        # accept() and wedges :5000. Read-only /proc walk, root only.
        sig = probe_fd_exhaustion("meshforge-map.service")
        if sig is not None:
            signals.append(sig)

        # PhoneAPI TCP-leak probe (Issue #75) — a leaked TCPInterface to
        # meshtasticd :4403 outside the connection manager's accounting
        # silently starves the :9443 web client of inbound texts + ACKs
        # (#17 contention class, leak form — the moc1 2026-06-07 evening).
        # Same-inode-across-ticks debounce: the collector's legit
        # per-collect socket lives seconds; only a stuck one fires.
        sig = probe_phoneapi_tcp_leak(
            "meshforge-map.service", status_port=http_port,
        )
        if sig is not None:
            signals.append(sig)

        # AREDN local-source dark (Phase 0 AREDN organ, 2026-06-12) — a box
        # with aredn_node_ips configured whose local sysinfo collection went
        # dark: node unreachable, or the RUNNING service predates the config
        # (reports not_configured while the settings file carries IPs).
        # Self-guards None when the organ isn't configured (the 95% case);
        # gating on the map service here keeps gateway-only boxes (moc3)
        # from ever running it.
        sig = probe_aredn_source_dark(
            status_url=f"http://127.0.0.1:{http_port}/api/status",
        )
        if sig is not None:
            signals.append(sig)

        # AREDN organ available but never adopted (2026-07-20) — the leg for a
        # box that neither configured NOR declared the organ, which both probes
        # above structurally cannot see. Positive evidence only: an AREDN node
        # answering sysinfo on this box's own LAN. Goes INERT (one bounded
        # resolve, no scan) on the 95% box with no AREDN path.
        sig = probe_aredn_organ_undeclared()
        if sig is not None:
            signals.append(sig)
    else:
        for _cls in ("http_local_unresponsive", "fd_exhaustion",
                     "phoneapi_tcp_leak", "aredn_source_dark",
                     "aredn_organ_undeclared"):
            note_disposition(_cls, "inert",
                             reason="meshforge-map not expected active on this box")

    # meshtasticd PhoneAPI wedge (2026-06-15) — the COMPANION to the
    # phoneapi_tcp_leak probe above: ≥2 contending single-consumers thrash
    # meshtasticd's :4403 ('Force close previous TCP connection' churn) and
    # the gateway's stateless-HTTP-protobuf mesh-TX wedges while the RNS
    # round-trip canary stays green (the 2026-06-13→15 moc 2-day silent
    # wedge). Watches MESHTASTICD (not the map), so it is NOT gated on the
    # map service — it self-guards None when meshtasticd is inactive and
    # must still run on gateway-only boxes (moc3) whose map is disabled.
    sig = probe_meshtasticd_phoneapi_wedge()
    if sig is not None:
        signals.append(sig)

    # Permission-foundation drift (mf.4/#73 perms class) — only meaningful on a
    # box that runs rnsd. Derives the rnsd user from its unit, so it's correct in
    # this root context; surfaces a re-provision that recreated /etc/reticulum
    # root:root while rnsd is non-root (the moc1/moc2/moc recurrence) before the
    # next logfile rotation wedges RNS.log(). Cheap: one sudo stat of the tree.
    if "rnsd.service" in services_expected_active:
        sig = probe_foundation_drift()
        if sig is not None:
            signals.append(sig)

        # RNS/LXMF fork-pin version drift — runs the version-check tool in the
        # rnsd user's env (root's may carry a different rns → false drift). Only
        # where rnsd is expected; a slow-changing pin, but cheap to confirm.
        sig = probe_rns_version_drift()
        if sig is not None:
            signals.append(sig)
    else:
        for _cls in ("foundation_perms_drift", "rns_version_drift"):
            note_disposition(_cls, "inert",
                             reason="rnsd not expected active on this box")

    # MeshForge<->MeshAnchor parity drift — self-guards on /opt/meshanchor being
    # present (only the box holding both repos checks; MeshForge-only boxes no-op).
    # Maintenance-hygiene signal so a forgotten port surfaces in the mini rollup.
    sig = probe_parity_drift()
    if sig is not None:
        signals.append(sig)

    # pip dependency version-floor drift — a critical dep (meshtastic) installed
    # BELOW the requirements/core.txt floor in the service user's env (a box that
    # missed/failed an update; the recurring class rns_version_drift didn't
    # cover). Env-independent floor, reads the service user's site-packages;
    # self-guards None when compliant / unreadable / not visible.
    sig = probe_dep_version_drift()
    if sig is not None:
        signals.append(sig)

    # meshtastic install fragmentation — the SAME pip lib installed at divergent
    # versions across the root-readable locations (venv / system-wide dist-
    # packages / root+user pipx / user-site) with ≥1 copy below the core.txt
    # floor. dep_version_drift watches only the service-user consumer and was
    # BLIND to a stray system-wide/pipx copy the TUI-as-root reads (the
    # 2026-06-17 phantom-update; feedback_version_env_rigor). Self-guards None
    # when not fragmented / no floor / single location; 2-tick debounced.
    sig = probe_dep_install_fragmented()
    if sig is not None:
        signals.append(sig)

    # RNS/LXMF stray-env coherence — every root-readable rns/lxmf copy on the
    # box (incl. copies inside OTHER apps' pipx venvs — the moc3 nomadnet-venv
    # lesson) must agree; a roll that missed one env pages here within 2
    # ticks. Pin compliance stays probe_rns_version_drift's job. Runs
    # unconditionally: strays matter on every box with any RNS client.
    # Self-guards None on 0/1 locations / unobservable user scope.
    sig = probe_rns_env_coherence()
    if sig is not None:
        signals.append(sig)

    # Declared-role drift — this box's live unit state vs its effective
    # declaration (fleet_roles.yaml base role + deployment.json overrides),
    # via provision_role.py's own dry-run plan (the converge SSOT). Self-guards:
    # no declared role → None. Documented service_overrides are honored (the
    # moc2 lesson, 2026-06-03) — only undeclared divergence fires, debounced
    # 2 ticks to ride out fleet-roll windows.
    sig = probe_role_drift()
    if sig is not None:
        signals.append(sig)

    # Inherited-app drift (Action 5, 2026-06-21 upstream-app ownership) — an
    # INHERITED (non-Nursedude-origin) upstream app checkout carrying an
    # unversioned tracked-file CODE patch: a hand-edit that exists in no repo we
    # control and is one `git pull` from silent deletion (the rescued
    # .32 + dev-box bot patches were exactly this; policy §4.2). Scans the top
    # level of the operator home + /opt, classifies owned-vs-inherited by
    # .git/config, and filters untracked artifacts + machine-generated manifests
    # so only a real source patch fires. NOT gated on any service — inherited
    # apps can live on any box; self-guards None (INERT) when there are none
    # (moc1/2/3). 2-tick debounced.
    sig = probe_inherited_app_drift()
    if sig is not None:
        signals.append(sig)

    # Channel-feed dark — the .32 dark-feed lesson (2026-06-04 PSK rotation):
    # silence is the failure mode. Watches meshtasticd's json-uplink journal
    # for decoded fleet-channel text BY NAME (slot indexes are box-local;
    # the 2026-06-06 federator false-alarm had the fleet channel at slot 3);
    # hours of silence on a box whose json pipeline is otherwise alive =
    # missed re-key / deaf radio / dead uplink path.
    # Self-guards: None when meshtasticd is down (service_inactive owns that)
    # or when the box emits no json-uplink lines at all (unobservable —
    # e.g. mqtt module unconfigured; a collector that only RXes).
    sig = probe_channel_feed_dark()
    if sig is not None:
        signals.append(sig)

    # MQTT root-topic drift (Issue #77) — the radio's observed publish root
    # (json-uplink journal lines) vs the box's declared consumer root
    # (gateway.json mqtt_bridge.root_topic). Catches a zero-config radio
    # join reintroducing the msh/US split — the CAUSE, hours before
    # channel_feed_dark can prove the symptom. Journal-only (never queries
    # the radio — #17); self-guards None on no-json-uplink boxes and an
    # unreadable declared side; 2-tick debounce rides out mid-rotation.
    sig = probe_mqtt_root_drift()
    if sig is not None:
        signals.append(sig)

    # Delivery write canary — reads gateway's self-reported health.
    sig = probe_delivery_write_canary(port=http_port)
    if sig is not None:
        signals.append(sig)

    # Queue backpressure (Issue #74) — depth near the shed threshold /
    # dead-letter growth via /api/gateway/queue. Self-guards None on
    # transport errors (http_local/service_inactive own those) and on
    # an unlimited queue (no ceiling to judge).
    sig = probe_queue_backlog(port=http_port)
    if sig is not None:
        signals.append(sig)

    # Delivery confirmation stall (Issue #74) — sends flowing but
    # confirmations collapsed, judged from the recent-events ring of
    # /api/gateway/delivery. The watchdog-layer answer to the bridge's
    # self-reported HEALTHY-while-nothing-confirms gap. Self-guards
    # None at zero/low traffic (silence is NOT failure here — the
    # explicit inversion of channel_feed_dark).
    sig = probe_delivery_confirmation_stall(port=http_port)
    if sig is not None:
        signals.append(sig)

    # Cross-gateway duplicate delivery (2026-06-29, dedup/identity arc STEP 5) —
    # the FIRST probe with a per-logical-message + cross-gateway dimension.
    # Reads the 4c cross-box rollup at /fleet/dups: a fleet DUP = the same
    # (content_id, recipient) confirmed by >1 gateway (the live dup-A). degraded
    # only. Naturally INERT off the manager box (the rollup only exists where the
    # collector cron runs) and on an indeterminate/stale rollup — the 4c JOIN's
    # own <2-gateway gate means a thin-coverage view never reads as a healthy
    # "0 dups" (honest_failure_modes #2). 2-tick debounce.
    sig = probe_gateway_dup_degraded(port=http_port)
    if sig is not None:
        signals.append(sig)

    # Dual-homed recipients (2026-07-19) — the LEADING indicator beside that
    # outcome detector. Row 8 accepted the duplicate residual on cost
    # asymmetry, not on dups being rare, so the fleet tracks the PRECONDITION
    # (a recipient reachable from >1 gateway) which is always countable and
    # moves before any duplicate occurs. Fires only on a NEWLY-observed
    # dual-homed recipient; same manager-box/indeterminate guards as above.
    sig = probe_gateway_dual_homed_exposure(port=http_port)
    if sig is not None:
        signals.append(sig)

    # LXMF propagation available but unadopted (2026-07-20) — the second
    # shape-C probe: positive evidence that a capability is THERE and nobody
    # took it up. The gateway already hears propagation-node announces and
    # files them in its node cache; this reads that cache against
    # gateway.json's empty rns.propagation_node. Self-guards INERT on a box
    # with no gateway.json and no node cache, so it costs nothing off the
    # gateway boxes; a stale cache HOLDS rather than claiming availability.
    sig = probe_lxmf_propagation_unused()
    if sig is not None:
        signals.append(sig)

    # The CONFIGURED propagation node stopped answering (2026-07-20) — the
    # shape-A companion to the leg above, and the reason adoption was never
    # allowed to ship alone: setting rns.propagation_node makes that probe
    # INERT by design, so without this one the fleet would swap a watched gap
    # for an unwatched dependency. Reads the same durable node cache; INERT
    # when nothing is configured (one fault, one owner) and HOLDS when no
    # propagation announce reached the box at all, so an RNS-wide wedge is
    # never relabelled as this node's death.
    sig = probe_lxmf_propagation_node_dark()
    if sig is not None:
        signals.append(sig)

    # Synth soak degraded / dark (2026-06-15) — the hourly LXMF round-trip
    # exerciser (meshforge-synth-soak.timer) FAILED its delivery envelope or
    # stopped firing. Closes the "canary itself unwatched" gap: the synth
    # fire script always exits 0 and nothing consumed pass_envelope, so a
    # delivery regression OR a silent timer was invisible. Reads the
    # operator's ~/.local/state synth_soak dir directly (root-context safe);
    # self-guards None (INERT) on boxes that don't run the soak. 2-tick
    # debounce rides out a torn mid-write / one slow run.
    sig = probe_synth_soak_degraded()
    if sig is not None:
        signals.append(sig)

    # LXMF store-and-forward drill degraded / dark (2026-07-21, propagation
    # arc slice 3). The OUTCOME leg for the propagation organ: the node_dark
    # probe only proves the configured propagation node ANNOUNCES, so a node
    # that announces while silently dropping every stored message reads clean
    # forever. The hourly drill manufactures offline-peer traffic and this
    # consumes its envelope. INERT where the drill doesn't run; 2-tick
    # debounce rides out a torn mid-write envelope.
    sig = probe_propagation_soak_degraded()
    if sig is not None:
        signals.append(sig)

    # Resource canary degraded / dark (2026-06-20, gateway-reliability arc A1 —
    # the OUTCOME source of truth). The synthetic RESOURCE round-trip canary
    # (meshforge-gateway-resource-canary.timer) actively PROVES the gateway
    # delivers a multi-chunk RNS Resource round-trip — the exact path the
    # 2026-06-20 wx-total-loss EROFS broke while single-packet replies kept
    # working. Consumes the canary's verdict envelope (last.json): a FAIL
    # "control back, resource NOT" is the EROFS signature; a stale file is the
    # canary going silent. Reads the operator's ~/.local/state dir directly
    # (root-context safe); self-guards None (INERT) on boxes that don't run the
    # canary. 2-tick debounce rides out a torn mid-write / one slow run.
    sig = probe_resource_canary_degraded()
    if sig is not None:
        signals.append(sig)

    # Gateway delivery degraded (2026-06-20, gateway-reliability arc A2) —
    # OUTCOME monitoring from the gateway's OWN journal self-report: the
    # windowed delivered/attempted ratio (att/del/drop block) collapsed, OR a
    # spike of its RNS resource/forward error lines (EROFS / resource-assembly
    # / forward-to-secondary). Leg 2 catches the 2026-06-20 wx-total-loss
    # EROFS shape that had a journal witness but no probe consumer; those
    # failures never reach the att/del counters, so only the error channel
    # sees them. Self-guards INERT (None) on a box that doesn't run the
    # gateway (gw MainPID None — moc/moc3 only); journalctl-unobservable holds
    # the debounce, observed-clean resets, 2-tick debounce. NOT gated on the
    # map service — gateway-only boxes (moc3) must run it.
    sig = probe_gateway_delivery_degraded()
    if sig is not None:
        signals.append(sig)

    # Oracle delivery degraded (2026-06-22, the mesh-oracle health leg) — the
    # read-only "ask dude-AI over the mesh" responder (src/oracle) answered
    # queries but its confirmable delivery rate (delivered / (delivered +
    # send_errors), over a recent ts window of its audit log) fell below
    # threshold. Declines (cooldown / not_allowlisted) + benign non-deliveries
    # (RNS no-path / MeshCore restart race) are excluded from the failure set
    # and surfaced, so the rate is the #74 confirmation view, not a false alarm
    # on a cooldowned/quiet channel. Reads the operator's ~/.local/share dir
    # directly (root-context safe); self-guards None (INERT) off a box where the
    # oracle never wrote a log (disabled / never queried) — the common case.
    # degraded only; min-sample guard (no silence leg — a reactive service
    # nobody queried isn't "broken"); 2-tick debounce.
    sig = probe_oracle_delivery_degraded()
    if sig is not None:
        signals.append(sig)

    # Cron-verdict coverage (Issue #78) — a cron WIRED to cron_verdict.sh
    # that reported FAIL/CONCERN or went silent past its schedule cadence.
    # Reads the operator's crontab spool + ~/cron_verdicts.log directly as
    # root (no sudo — sandbox); cross-references the crontab so stale ORPHAN
    # verdicts never false-alarm. INERT (None) until crons are wired — opt-in.
    sig = probe_cron_verdict_stale()
    if sig is not None:
        signals.append(sig)

    # Fleet box unreachable (2026-06-17, Leg D) — surface a box the offline-
    # monitor (fleet_offline_check.sh, manager-box-only) has confirmed DOWN into
    # mini's brief + /fleet, so it can't sit silent in a side-channel logfile
    # (the .32 33h-dark lesson). Reads ~/fleet_offline_state.tsv as root;
    # self-guards INERT off the manager box (no file) and on a stale file (the
    # monitor's own death is cron_verdict_stale's job — it's verdict-wired).
    sig = probe_fleet_box_unreachable()
    if sig is not None:
        signals.append(sig)

    # host_frozen (Leg C): the dude-claw out-of-band witness verdict for a box on
    # its own subnet — HOST_FROZEN (kernel alive, userspace wedged) the box's own
    # self-petted HW watchdog can't catch. Reads ~/host_probe_state.json written
    # by the out-of-band host_probe_check collector; INERT off the claw's brain box.
    sig = probe_host_frozen()
    if sig is not None:
        signals.append(sig)

    # claw edge hardware (2026-07-19, structural-dark row 7): the claw's OWN
    # vocabulary. device_dark = a fresh capture tick says the DEVICE didn't
    # answer (a hardware/power fact, not a failing cron); battery_low = a
    # reachable pack under the LiPo floor, the warning that lands while the
    # node can still be saved. Both read the tick files the capture cron
    # already writes — no second NATS poll. INERT off a claw-brain box.
    sig = probe_claw_device_dark()
    if sig is not None:
        signals.append(sig)

    sig = probe_claw_battery_low()
    if sig is not None:
        signals.append(sig)

    # claw LoRa ears (row 9): the ONLY over-the-air witness in the fleet — a
    # separate radio on separate silicon reporting what it actually heard, so
    # "we think we transmitted" can be told from "the air is quiet". Escalate
    # only; the quiet-window threshold is provisional pending a heard-rate soak.
    sig = probe_claw_rf_silent()
    if sig is not None:
        signals.append(sig)

    # watched-node silence (2026-07-29) — the MUTE-TRANSMITTER case the probe
    # above is structurally blind to. claw_rf_silent reads heard_age_s (the last
    # packet from ANYONE), so a busy channel reads healthy while OUR OWN radio is
    # off the air; this reads the per-node watch verdicts, gated on the claw's
    # listening window so `never` after a reboot is never mistaken for silence.
    sig = probe_claw_watched_node_silent()
    if sig is not None:
        signals.append(sig)

    # claw UPLINK (2026-07-29) — the witness's own path home. A claw can be
    # healthy in every way it can self-report and still be unreachable because
    # the node bridging its subnet moved; claw_device_dark renders exactly that
    # as "the device is silent" and sends the operator at working hardware.
    # Leading indicator, observation-only (/proc/net/arp), INERT undeclared.
    sig = probe_claw_uplink_node_moved()
    if sig is not None:
        signals.append(sig)

    # router_scout_degraded (2026-07-11 OpenWrt-router arc) — a mirrored
    # meshforge-scout tick shows the ROUTER-side agent degraded: fresh mirror
    # but stale captured_at (agent cron dark while the pull re-copies the same
    # old tick), tick ok=false, or an unparseable mirror. Defense-in-depth
    # behind the pull's own cron_verdict eval — this adds the /fleet + mini
    # surface (per-device subject). Reads
    # ~/.local/share/meshforge/router_scout/ as root; INERT off the manager
    # box; stale mirror files are skipped (cron_verdict_stale owns the dead
    # pull cron — router_scout is verdict-wired). degraded only (remote).
    sig = probe_router_scout_degraded()
    if sig is not None:
        signals.append(sig)

    # ntfy loopback (2026-06-18, receipt-heartbeat Phase 2) — the alerting
    # spine's OWN liveness. A manager-box collector (fleet_ntfy_loopback.sh)
    # publishes a nonce'd min-priority heartbeat to the fleet topic + polls
    # ntfy.sh to confirm it loops back, escalating via the EMAIL backbone on a
    # miss (ntfy is the suspect channel). This reads ~/ntfy_loopback_state.json
    # (root, no sudo) and surfaces a miss into mini/+/fleet; INERT off the
    # manager box, stale file → cron_verdict_stale owns the dead-cron alert.
    sig = probe_ntfy_loopback()
    if sig is not None:
        signals.append(sig)

    # ntfy ack stale (2026-06-18, receipt-heartbeat Phase 3) — the only rung that
    # confirms the operator's DEVICE. A manager-box cron (fleet_ntfy_ack.sh) sends
    # a WEEKLY tap-to-ack page; the tap makes the phone POST to a dedicated
    # ack-topic the cron polls. This reads ~/ntfy_ack_state.json (root, no sudo)
    # and surfaces consecutive unacked weeks into mini/+/fleet; INERT until first
    # pinged, stale file → cron_verdict_stale owns the dead-cron alert. The cron
    # owns the email escalation (this is visibility — propose_escalation).
    sig = probe_ntfy_ack_stale()
    if sig is not None:
        signals.append(sig)

    # Kernel reboot pending (2026-06-09 version-updates arc) — a newer
    # same-flavor kernel installed under /lib/modules than the running one,
    # or the distro reboot-required flag. Read-only, no sudo; flavor-aware
    # (rpi-v8 vs rpi-2712 never compared); indeterminate shapes stay silent;
    # 2-tick debounce rides out a tick landing mid-upgrade.
    sig = probe_kernel_reboot_pending()
    if sig is not None:
        signals.append(sig)

    # Host memory pressure (2026-07-24 manager-box hard-reset arc) — the box is
    # running out of RAM. UNCONDITIONAL: every box has memory, and the failure
    # it catches is fatal to the whole box rather than to one service, so it is
    # deliberately not gated on any unit being active. Read-only /proc, no
    # subprocess (works inside the hardened sandbox). Fires BEFORE the hardware
    # watchdog resets the box, and its detail names the top RSS consumers —
    # the "who took the memory" witness the 07-24 post-mortem could not find,
    # because /tmp is tmpfs (wiped by the reset) and sysstat is disabled.
    sig = probe_host_memory_pressure()
    if sig is not None:
        signals.append(sig)

    # Did a MemoryMax cap actually BITE? (2026-07-24, same arc.) The companion to
    # the probe above: that one warns the box is running out of RAM, this one
    # reports the cap ENGAGING — a kill inside a capped cgroup, or a slice living
    # at its ceiling. Wired because the 07-24 rollout put hard caps on eight boxes
    # with nothing observing them: an OOM-killed ssh session or user unit leaves no
    # trace but a missing process. Returns 0..N signals (one per capped cgroup, so
    # each cap keeps a stable identity); INERT on a box with no finite cap.
    signals.extend(probe_memory_cap_engaged())

    # Tracer peer unreachable — reads tracer-<unix>.json files.
    # Returns 0..N signals depending on peer count.
    signals.extend(probe_tracer_peer_unreachable())

    # mini-dudeai self-health (Issue #79) — three read-only, self-guarding
    # probes that return None when mini isn't on this box. They watch the
    # observer's own reliability surfaces: a swallowed history-write failure
    # while the loop ticks, a live rules file fallen behind its role seed, and
    # the operator memory index (MEMORY.md) over its context-load limit.
    sig = probe_history_write_failure()
    if sig is not None:
        signals.append(sig)
    sig = probe_rules_seed_drift()
    if sig is not None:
        signals.append(sig)
    sig = probe_memory_index_oversize()
    if sig is not None:
        signals.append(sig)

    # Calibration spine (2026-06-15) — turns the self-observation pattern on the
    # assistant: a VERIFIED claim that did not hold on re-derivation. Self-guards
    # None off the dev/manager box (no ledger).
    sig = probe_calibration_drift()
    if sig is not None:
        signals.append(sig)

    # Second-brain value loop (2026-07-22, WS-C): mini's dream propose->ratify
    # loop runs but is not closing (proposals unresolved while the mini_cadence
    # ratifier is alive). Self-guards inert off the manager box (no cadence
    # verdict = no ratifier) and defers the cadence-DOWN case to #78.
    sig = probe_dream_ratification_stalled()
    if sig is not None:
        signals.append(sig)

    # Learning observability (2026-07-22, WS-D): a local-brain eval case that
    # passed before now fails — the tier-L model lost a capability the aggregate
    # --gate/#78 can miss. Self-guards inert off the manager box (no eval ledger).
    sig = probe_local_brain_regressed()
    if sig is not None:
        signals.append(sig)

    return signals


# ─────────────────────────────────────────────────────────────────────
# Coverage assembly — the per-class disposition map (fleet-truth Phase 0)
# ─────────────────────────────────────────────────────────────────────


def build_coverage(signals: List[Signal]) -> Dict[str, Dict[str, str]]:
    """Assemble the per-``SIGNAL_CLASSES`` disposition map for this tick.

    Consumed by ``fleet_truth.merge_coverage`` via the ``coverage`` key of
    watchdog.json → ``/api/status.watchdog``. Precedence per class:

      1. ``active``  — a Signal of this class was emitted (worst).
      2. whatever the probes/runner noted (``clean|inert|indeterminate``).
      3. ``unknown`` — nothing noted: honest fail-dark, renders dark. A
         probe that hasn't adopted the disposition contract stays dark
         rather than silently reading green.

    Every member of the closed enum gets an entry — a class silently
    missing from the map would be indistinguishable from "not watched".
    """
    noted = collect_dispositions()
    active = {s.cls for s in signals}
    coverage: Dict[str, Dict[str, str]] = {}
    for cls in SIGNAL_CLASSES:
        if cls in active:
            entry: Dict[str, str] = {"disp": "active"}
            # active-but-partial (B3, 2026-07-26): a multi-subject probe can
            # emit subject B while noting indeterminate because subject A's
            # channel failed. Stamping bare "active" made the class read
            # fully-observed and the tracker CLEARED the vanished subject.
            # `partial` tells `_class_was_observed` this observation does not
            # permit clearing; the emitted signals themselves still count.
            noted_entry = noted.get(cls)
            if (isinstance(noted_entry, dict)
                    and noted_entry.get("disp") == "indeterminate"):
                entry["partial"] = True
                if noted_entry.get("reason"):
                    entry["reason"] = noted_entry["reason"]
            coverage[cls] = entry
        elif cls in noted:
            coverage[cls] = noted[cls]
        else:
            coverage[cls] = {"disp": "unknown",
                             "reason": "no disposition reported this tick"}
    return coverage


# ─────────────────────────────────────────────────────────────────────
# Output writer
# ─────────────────────────────────────────────────────────────────────


def write_state(
    output_path: Path,
    *,
    host: str,
    now: float,
    probe_count: int,
    active_signals: List[Tuple[Signal, float]],
    coverage: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    """Write atomic-rename JSON. Never raises — best-effort.

    Schema:
        {
          "host": "moc1",
          "ts": <unix float>,
          "probe_count": <int>,
          "ok": <bool: any wedge-severity signal>,
          "signals": [
            { "class": "...", "subject": "...", "severity": "...",
              "detail": "...", "issue_ref": <int|null>,
              "first_seen": <unix float>, "extra": {...} },
            ...
          ],
          "coverage": {  # fleet-truth Phase 0; absent on legacy writers
            "<signal_class>": {"disp": "active|clean|inert|indeterminate|unknown",
                                "reason": "..."},
            ...
          }
        }
    """
    has_wedge = any(s.severity == "wedge" for s, _ in active_signals)
    payload = {
        "host": host,
        "ts": now,
        "probe_count": probe_count,
        "ok": not has_wedge,
        "signals": [signal_to_dict(s, first_seen_ts=fs) for s, fs in active_signals],
    }
    if coverage is not None:
        payload["coverage"] = coverage

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("watchdog: state dir create failed: %s", exc)
        return

    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
            fh.write("\n")
        # 0644 so map server (running as operator user) can read.
        os.chmod(tmp, 0o644)
        os.replace(tmp, output_path)
    except OSError as exc:
        logger.warning("watchdog: state write failed: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_prior_signals(output_path: Path) -> List[dict]:
    """The previous process's ``signals`` list, for tracker rehydration (B2).

    Absent, unreadable, or corrupt state starts empty — a fresh box must boot
    and a torn file must never crash the daemon or fabricate signals.
    """
    try:
        raw = output_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.info("watchdog: prior state %s unreadable (%s) — starting empty",
                    output_path, exc)
        return []
    try:
        payload = json.loads(raw)
    # TypeError: read_text yielded a non-str (test doubles, exotic Path
    # types) — same posture as corrupt: never crash the daemon at startup.
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.info("watchdog: prior state %s corrupt (%s) — starting empty",
                    output_path, exc)
        return []
    sigs = payload.get("signals") if isinstance(payload, dict) else None
    return sigs if isinstance(sigs, list) else []


# ─────────────────────────────────────────────────────────────────────
# Loop
# ─────────────────────────────────────────────────────────────────────


def _operator_home_for_root() -> Optional[Path]:
    """Resolve the operator's home dir when this daemon runs as root.

    systemd starts us with ``User=root`` and ``LOGNAME=root`` — neither
    ``SUDO_USER`` nor a non-root ``LOGNAME`` is set, so the standard
    ``get_real_user_home()`` falls back to ``/root``. The watchdog's
    config file lives in the operator's home (consistent with
    ``~/.config/meshforge/`` everywhere else), so we use the same
    UID-1000 / pwd-lookup pattern ``tracer_fires._operator_home`` uses.
    Returns None when no operator user can be resolved.
    """
    import os
    if os.geteuid() != 0:
        try:
            from utils.paths import get_real_user_home
            return get_real_user_home()
        except Exception:
            return None
    try:
        from utils.fleet_test_runner import _find_operator_user
    except ImportError:
        return None
    op = _find_operator_user()
    if op is None:
        return None
    op_uid, _ = op
    try:
        import pwd
        return Path(pwd.getpwuid(op_uid).pw_dir)
    except (KeyError, ImportError, OSError):
        return None


def _resolve_config_candidates(
    explicit_path: Optional[Path],
) -> List[Path]:
    """Build the ordered candidate list for the config file.

    Order:
      1. Explicit ``--config`` path (CLI override) — taken as-is.
      2. ``<operator_home>/.config/meshforge/watchdog.json`` — primary
         per-box location. Same dir as ``~/.config/meshforge/lab_peers``,
         settings.json, etc.
      3. ``/etc/meshforge/watchdog.json`` — system fallback for boxes
         where the operator home isn't resolvable (mirrors how
         ``_read_rns_instance_name`` falls back to ``/etc/reticulum/config``).
    """
    if explicit_path is not None:
        return [Path(str(explicit_path)).expanduser()]

    candidates: List[Path] = []
    op_home = _operator_home_for_root()
    if op_home is not None:
        candidates.append(op_home / ".config" / "meshforge" / "watchdog.json")
    candidates.append(Path("/etc/meshforge/watchdog.json"))
    return candidates


def load_config_file(
    config_path: Optional[Path] = None,
) -> Dict[str, object]:
    """Load per-box override config from operator home or /etc.

    Returns an empty dict on any read/parse failure — the watchdog must
    keep starting even if the override file is bad. Logs a WARNING so
    the operator sees the failure without burying it.

    Tries multiple candidate paths (see ``_resolve_config_candidates``).
    The first existing-and-parseable file wins; subsequent candidates
    are not consulted.
    """
    candidates = _resolve_config_candidates(config_path)
    last_attempted: Optional[Path] = None

    for path in candidates:
        last_attempted = path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # try next candidate
        except OSError as exc:
            logger.warning(
                "watchdog: config file %s unreadable: %s — trying next candidate",
                path, exc,
            )
            continue

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "watchdog: config file %s malformed: %s — falling back to defaults",
                path, exc,
            )
            return {}

        if not isinstance(data, dict):
            logger.warning(
                "watchdog: config file %s root is not a JSON object — "
                "falling back to defaults", path,
            )
            return {}
        logger.info("watchdog: loaded per-box overrides from %s", path)
        return data

    # No candidate existed — quiet (the file is optional).
    return {}


def resolve_probe_targets(
    config: Dict[str, object],
) -> Tuple[Tuple[str, ...], Tuple[str, ...], int]:
    """Layer config-file overrides on top of hardcoded defaults.

    Returns ``(services_expected_active, services_wedge_check, http_port)``.

    A list override is a *full replacement* of the default list. We
    deliberately don't do "add to default" or "subtract from default"
    semantics — the operator sees exactly what's probed by reading the
    file. Cuts ambiguity at the cost of slightly more typing.
    """
    services_expected = config.get("services_expected_active")
    if isinstance(services_expected, list) and all(
        isinstance(s, str) for s in services_expected
    ):
        sea: Tuple[str, ...] = tuple(services_expected)
    else:
        if services_expected is not None:
            logger.warning(
                "watchdog: services_expected_active override is not a list "
                "of strings — ignoring",
            )
        sea = _DEFAULT_SERVICES_EXPECTED_ACTIVE

    services_wedge = config.get("services_wedge_check")
    if isinstance(services_wedge, list) and all(
        isinstance(s, str) for s in services_wedge
    ):
        swc: Tuple[str, ...] = tuple(services_wedge)
    else:
        if services_wedge is not None:
            logger.warning(
                "watchdog: services_wedge_check override is not a list "
                "of strings — ignoring",
            )
        swc = _DEFAULT_SERVICES_WEDGE_CHECK

    port_raw = config.get("http_port")
    if isinstance(port_raw, int) and 1 <= port_raw <= 65535:
        port = port_raw
    else:
        if port_raw is not None:
            logger.warning(
                "watchdog: http_port override %r is not a valid port — "
                "ignoring", port_raw,
            )
        port = 5000
    return sea, swc, port


def _read_rns_instance_name() -> Optional[str]:
    """Best-effort lookup of this box's RNS instance_name.

    Reads ``~<operator>/.reticulum/config`` (the canonical fleet path).
    Returns None if not readable or not configured.
    """
    candidates: List[Path] = []
    try:
        from utils.paths import get_real_user_home
        candidates.append(get_real_user_home() / ".reticulum" / "config")
    except Exception:
        pass
    candidates.append(Path("/etc/reticulum/config"))

    for cfg_path in candidates:
        try:
            text = cfg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("instance_name"):
                _, _, rhs = line.partition("=")
                name = rhs.strip()
                if name:
                    return name
    return None


def run_loop(
    *,
    output_path: Path,
    tick_s: float,
    stop_event: threading.Event,
    services_expected_active: Tuple[str, ...] = _DEFAULT_SERVICES_EXPECTED_ACTIVE,
    services_wedge_check: Tuple[str, ...] = _DEFAULT_SERVICES_WEDGE_CHECK,
    http_port: int = 5000,
    phase2_config: Optional[Phase2Config] = None,
) -> None:
    """Main probe loop. Returns on stop_event.

    ``phase2_config`` defaults to disabled — Phase 2 auto-restart is
    only invoked when the parsed config sets ``enabled=True``. The
    runner enforces this independently of the per-decision check in
    ``decide_restarts`` (belt-and-suspenders: two independent layers).
    """
    host = socket.gethostname().split(".")[0] or "unknown"
    tracker = SignalTracker()
    # B2: adopt the prior process's signals so a routine restart cannot read
    # as a fleet-wide recovery. A rehydrated signal is unobserved until a
    # probe re-observes it: the first tick clears it (positive observation),
    # holds it with extra.unobserved_hold (still blind), or re-fires it.
    rehydrated = tracker.rehydrate(_load_prior_signals(output_path))
    logger.info("watchdog: rehydrated %d signal(s) from prior state", rehydrated)
    probe_count = 0
    rns_instance = _read_rns_instance_name()
    if rns_instance:
        logger.info("watchdog: rns instance_name resolved to %r", rns_instance)
    else:
        logger.info(
            "watchdog: no rns instance_name in config; namespace-collision "
            "probe disabled until config readable"
        )

    phase2_config = phase2_config or Phase2Config()
    restart_history = RestartHistory()
    if phase2_config.enabled:
        logger.warning(
            "watchdog: PHASE 2 auto-restart ENABLED dry_run=%s rules=%d",
            phase2_config.dry_run, len(phase2_config.rules),
        )
        for rule in phase2_config.rules:
            logger.info(
                "watchdog: phase2 rule %s/%s ticks=%d cooldown=%.0fs rate=%d/h",
                rule.signal_class, rule.service,
                rule.consecutive_ticks, rule.cooldown_s, rule.max_restarts_per_hour,
            )

    while not stop_event.is_set():
        now = time.time()
        probe_count += 1
        try:
            signals = run_all_probes(
                rns_instance_name=rns_instance,
                services_expected_active=services_expected_active,
                services_wedge_check=services_wedge_check,
                http_port=http_port,
            )
        except Exception as exc:
            # Never let a probe bug kill the watchdog. Log and continue.
            logger.error("watchdog: probe dispatch failed: %s", exc, exc_info=True)
            signals = []
            coverage = None  # dispatch crashed mid-tick — coverage unknown, not clean
        else:
            coverage = build_coverage(signals)

        active_with_first_seen, newly_cleared, newly_held = tracker.update(
            signals, now=now, coverage=coverage,
        )

        # A hold must never be silent — otherwise "this signal did not clear"
        # is invisible and we have traded a false CLEARED for a mystery.
        for cls, subject in newly_held:
            logger.warning(
                "watchdog: signal HELD class=%s subject=%s — class not observed "
                "this tick (disposition unobserved); not clearing",
                cls, subject,
            )

        # Log only edge transitions.
        for sig, fs_ts in active_with_first_seen:
            if fs_ts == now:
                # First-seen transition.
                level = logging.WARNING if sig.severity == "wedge" else logging.INFO
                logger.log(
                    level,
                    "watchdog: signal NEW class=%s subject=%s severity=%s detail=%s",
                    sig.cls, sig.subject, sig.severity, sig.detail,
                )
        for cls, subject in newly_cleared:
            logger.info(
                "watchdog: signal CLEARED class=%s subject=%s",
                cls, subject,
            )

        write_state(
            output_path,
            host=host,
            now=now,
            probe_count=probe_count,
            active_signals=active_with_first_seen,
            coverage=coverage,
        )

        # Phase 2 — auto-restart actions. Skipped entirely when
        # phase2_config.enabled is False (the default and only state
        # this codebase ships in as of 2026-05-21). The decision logic
        # also re-checks enabled internally; that's intentional belt-
        # and-suspenders.
        if phase2_config.enabled:
            decisions = decide_restarts(
                # Report a held signal, never act on it — see observed_only().
                signals_with_first_seen=observed_only(active_with_first_seen),
                config=phase2_config,
                history=restart_history,
                now=now,
                tick_s=tick_s,
            )
            for d in decisions:
                action = getattr(d.rule, "action", "restart")
                logger.warning(
                    "watchdog: PHASE 2 %s action=%s target=%s reason=%s",
                    "DRY-RUN" if d.dry_run else action.upper(),
                    action, d.service, d.reason,
                )
                if d.dry_run:
                    continue
                # Dispatch the remediation: "reconverge" re-applies the whole
                # role via provision_role.py --apply (role_drift, v3); anything
                # else is a systemctl restart of the matched service.
                ok = (execute_reconverge() if action == "reconverge"
                      else execute_restart(d.service))
                if ok:
                    restart_history.record(d.service, now)
                    logger.info("watchdog: phase2 %s succeeded target=%s", action, d.service)
                else:
                    logger.error("watchdog: phase2 %s FAILED target=%s", action, d.service)

        stop_event.wait(tick_s)


def _oneshot_default_output(base: Path = DEFAULT_OUTPUT_PATH) -> Path:
    """Default ``--one-shot`` output — distinct from the daemon's live file.

    B9: one-shot wrote ``DEFAULT_OUTPUT_PATH`` through the same fixed ``.tmp``
    sibling ``write_state`` uses, so a debug invocation raced the daemon and
    transiently published a hold-free doc over the daemon's state. A distinct
    default path also gets a distinct tmp name.
    """
    return base.with_suffix(".oneshot.json")


def _resolve_output_paths(explicit: Optional[Path], *, one_shot: bool) -> Path:
    """Output path from the (optional) explicit ``--output`` and the mode.

    An explicit ``--output`` is always honored unchanged (both modes); without
    one, the daemon writes the live path and one-shot writes its own file.
    """
    if explicit is not None:
        return explicit
    return _oneshot_default_output() if one_shot else DEFAULT_OUTPUT_PATH


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MeshForge per-box watchdog daemon")
    parser.add_argument(
        "--output", type=Path, default=None,
        help=(
            f"Output JSON path (daemon default {DEFAULT_OUTPUT_PATH}; "
            f"--one-shot defaults to {_oneshot_default_output()} so a debug "
            f"run never races the live daemon file)"
        ),
    )
    parser.add_argument(
        "--tick", type=float, default=DEFAULT_TICK_S,
        help=f"Seconds between probe ticks (default {DEFAULT_TICK_S})",
    )
    parser.add_argument(
        "--http-port", type=int, default=None,
        help=(
            "Local map server HTTP port. Defaults to 5000 unless overridden "
            "by the config file's http_port field. CLI takes precedence over "
            "config."
        ),
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help=(
            f"Per-box override file (JSON). Defaults to "
            f"{DEFAULT_CONFIG_FILE} resolved against the operator home. "
            f"Schema: services_expected_active (list[str]), "
            f"services_wedge_check (list[str]), http_port (int). "
            f"Missing file = pure defaults; malformed file logs WARNING "
            f"and falls back."
        ),
    )
    parser.add_argument(
        "--loglevel", default="INFO",
        help="Python logging level (default INFO)",
    )
    parser.add_argument(
        "--one-shot", action="store_true",
        help="Run probes once, write state, exit. For ops debugging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    stop_event = threading.Event()

    def _on_signal(signum, _frame):
        logger.info("watchdog: received signal %d, stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # Resolve probe targets: hardcoded defaults → config file → CLI args.
    config = load_config_file(args.config)
    services_expected, services_wedge, config_port = resolve_probe_targets(config)
    # CLI --http-port wins over config file when explicitly set.
    http_port = args.http_port if args.http_port is not None else config_port
    phase2_config = parse_phase2_config(config)
    output_path = _resolve_output_paths(args.output, one_shot=args.one_shot)

    if args.one_shot:
        if args.output is None:
            logger.info(
                "watchdog: one-shot writing %s — distinct from the daemon's "
                "live %s (pass --output to override)",
                output_path, DEFAULT_OUTPUT_PATH,
            )
        host = socket.gethostname().split(".")[0] or "unknown"
        tracker = SignalTracker()
        now = time.time()
        rns_instance = _read_rns_instance_name()
        signals = run_all_probes(
            rns_instance_name=rns_instance,
            services_expected_active=services_expected,
            services_wedge_check=services_wedge,
            http_port=http_port,
        )
        coverage = build_coverage(signals)
        # Fresh tracker in --once mode, so nothing can clear or be held; the
        # coverage argument is still passed explicitly rather than defaulted
        # (see SignalTracker.update — the contract has no silent default).
        active_with_first_seen, _cleared, _held = tracker.update(
            signals, now=now, coverage=coverage,
        )
        write_state(
            output_path, host=host, now=now, probe_count=1,
            active_signals=active_with_first_seen,
            coverage=coverage,
        )
        for sig, _ in active_with_first_seen:
            print(
                f"  [{sig.severity}] {sig.cls} subject={sig.subject} "
                f"detail={sig.detail}"
            )
        if not active_with_first_seen:
            print("  (no signals — system looks healthy)")
        return 0

    try:
        run_loop(
            output_path=output_path,
            tick_s=args.tick,
            stop_event=stop_event,
            services_expected_active=services_expected,
            services_wedge_check=services_wedge,
            http_port=http_port,
            phase2_config=phase2_config,
        )
    except Exception as exc:
        logger.error("watchdog: loop crashed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
