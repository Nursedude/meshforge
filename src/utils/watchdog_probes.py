"""Per-box watchdog probes — pure functions, each returns Optional[Signal].

Companion to ``utils/watchdog_runner.py``. The runner ticks every 30s,
calls every probe in order, aggregates results into
``/var/lib/meshforge/watchdog.json`` (atomic-rename), and surfaces them
via ``/api/status.watchdog`` for the federation rollup.

Each probe targets one concrete failure shape already documented in
``.claude/foundations/persistent_issues.md``. Adding a new probe means
adding the failure class to the closed enum in ``watchdog_probe_core.py``
+ a probe function + a row in ``persistent_issues.md``. The surface
becomes the place new classes get categorized instead of each one
starting a fresh issue thread.

Design constraints:

* **Pure**: probes take primitive args, return a ``Signal`` (or None).
  No global state, no logger calls — the runner handles edge-transition
  logging.
* **Bounded**: every external call has an explicit timeout. A probe
  that hangs the runner is worse than a probe that returns None.
* **Read-only**: probes NEVER restart services, write files, or send
  network traffic that side-effects the system being probed.
* **Honest about indeterminacy**: a probe that can't reach its data
  source returns None (no signal). False alarms are worse than missed
  alarms in Phase 1 — the operator stops trusting the panel.

The Phase 1 watchdog does NOT auto-restart anything. It emits signal
only. Phase 3 (opt-in, narrow allowlist) is gated on a month of
trusted signal.

Layout (split 2026-06-09 at 3,143 lines vs the 1,500-line rule): this
module is the IMPORT HUB — every external consumer (runner, tests, mini
preset, MeshAnchor parity shape-check) imports from here. Probe bodies
live in the sibling modules and are re-exported below:

* ``watchdog_probe_core``     — Signal, SIGNAL_CLASSES, shared helpers
* ``watchdog_probes_rns``     — RNS substrate (#68/#69/#72 class)
* ``watchdog_probes_service`` — local service health (#61/#73/#75)
* ``watchdog_probes_drift``   — declared-vs-live drift (#77/#78 + parity)
* ``watchdog_probes_gateway`` — delivery path (#63/#74)
* ``watchdog_probes_mini``    — mini-dudeai self-observation (#79)

NOTE for tests: ``patch()`` targets the module where a name is LOOKED UP,
which after the split is the defining sibling module, not this hub
(e.g. ``utils.watchdog_probes_gateway.urlopen``).

NOTE for parity: ``scripts/parity_check.py`` asserts the two RNS-wedge
probe symbols appear in THIS file — keep the re-export lines below.
"""
from utils.watchdog_probe_core import (
    SEVERITIES,
    SIGNAL_CLASSES,
    Signal,
    signal_to_dict,
    _journal_newest_match,
    _read_deployment_declaration,
    _resolve_main_pid,
    _short_unix_ts,
)
from utils.watchdog_probes_rns import (
    probe_lxmf_process_wedge,
    probe_main_thread_wedge,
    probe_rns_interface_down_peer_reachable,
    probe_rns_namespace_collision,
    probe_rns_rpc_responsive,
    probe_rns_shared_instance_responsive,
    _tcp_reachable,
)
from utils.watchdog_probes_service import (
    probe_channel_feed_dark,
    probe_fd_exhaustion,
    probe_http_local,
    probe_meshtasticd_phoneapi_wedge,
    probe_nomadnet_crashloop,
    probe_phoneapi_tcp_leak,
    probe_service_inactive,
    probe_tracer_peer_unreachable,
)
from utils.watchdog_probes_drift import (
    probe_aredn_source_dark,
    probe_cron_verdict_stale,
    probe_dep_install_fragmented,
    probe_dep_version_drift,
    probe_fleet_box_unreachable,
    probe_host_frozen,
    probe_ntfy_loopback,
    probe_ntfy_ack_stale,
    probe_foundation_drift,
    probe_kernel_reboot_pending,
    probe_mqtt_root_drift,
    probe_parity_drift,
    probe_rns_version_drift,
    probe_role_drift,
    _cron_max_interval,
    _parse_kernel_release,
    _plan_role_actions,
    _read_declared_root_topic,
)
from utils.watchdog_probes_gateway import (
    probe_delivery_confirmation_stall,
    probe_delivery_write_canary,
    probe_gateway_delivery_degraded,
    probe_queue_backlog,
    probe_resource_canary_degraded,
    probe_synth_soak_degraded,
)
from utils.watchdog_probes_mini import (
    MEMORY_INDEX_LIMIT_BYTES,
    probe_calibration_drift,
    probe_history_write_failure,
    probe_memory_index_oversize,
    probe_rules_seed_drift,
    _ROLE_TO_MINI_SEED,
    _resolve_mini_home,
)

__all__ = [
    "SIGNAL_CLASSES",
    "SEVERITIES",
    "Signal",
    "signal_to_dict",
    "probe_rns_namespace_collision",
    "probe_main_thread_wedge",
    "probe_lxmf_process_wedge",
    "probe_rns_shared_instance_responsive",
    "probe_rns_interface_down_peer_reachable",
    "probe_rns_rpc_responsive",
    "_tcp_reachable",
    "probe_http_local",
    "probe_fd_exhaustion",
    "probe_phoneapi_tcp_leak",
    "probe_meshtasticd_phoneapi_wedge",
    "probe_foundation_drift",
    "probe_parity_drift",
    "probe_rns_version_drift",
    "probe_dep_version_drift",
    "probe_dep_install_fragmented",
    "probe_role_drift",
    "probe_channel_feed_dark",
    "probe_mqtt_root_drift",
    "probe_delivery_write_canary",
    "probe_queue_backlog",
    "probe_delivery_confirmation_stall",
    "probe_gateway_delivery_degraded",
    "probe_resource_canary_degraded",
    "probe_synth_soak_degraded",
    "probe_service_inactive",
    "probe_nomadnet_crashloop",
    "probe_tracer_peer_unreachable",
    "probe_cron_verdict_stale",
    "probe_kernel_reboot_pending",
    "probe_aredn_source_dark",
    "probe_fleet_box_unreachable",
    "probe_host_frozen",
    "probe_ntfy_loopback",
    "probe_ntfy_ack_stale",
    "probe_history_write_failure",
    "probe_rules_seed_drift",
    "probe_memory_index_oversize",
    "probe_calibration_drift",
]
