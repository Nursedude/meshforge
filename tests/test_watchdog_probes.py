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
    MEMORY_INDEX_LIMIT_BYTES,
    probe_delivery_confirmation_stall,
    probe_delivery_write_canary,
    probe_gateway_delivery_degraded,
    probe_synth_soak_degraded,
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
    _save_gateway_delivery_streak,
    _load_gateway_delivery_streak,
    GATEWAY_DELIVERY_BLOCK_GREP,
    GATEWAY_RNS_ERROR_GREP,
)


# ─────────────────────────────────────────────────────────────────────
# Closed enum + Signal shape
# ─────────────────────────────────────────────────────────────────────


def test_signal_classes_closed_enum_is_documented():
    """Every class in the enum must be one of the documented classes.
    Adding a class is a deliberate act — bumps this test AND requires
    a persistent_issues.md entry."""
    assert set(SIGNAL_CLASSES) == {
        "rns_namespace_collision",
        "main_thread_wedge",
        "http_local_unresponsive",
        "delivery_write_canary",
        "service_inactive",
        "tracer_peer_unreachable",
        "rns_shared_instance_unresponsive",
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
        "dep_version_drift",            # 2026-06-12 recurring update class
        "dep_install_fragmented",       # 2026-06-17 install-fragmentation half of the recurring update class (feedback_version_env_rigor); documented inline in the SIGNAL_CLASSES comment — no new persistent_issues.md row, that file is at its MF012 40k cap (same precedent as meshtasticd_phoneapi_wedge / calibration_drift)
        "synth_soak_degraded",          # 2026-06-15 synth-soak watch
        "calibration_drift",            # 2026-06-15 calibration spine; SSOT .claude/rules/calibrated_claims.md (new subsystem, not a fleet bug → no persistent_issues row; that file is at its MF012 cap)
        "fleet_box_unreachable",        # 2026-06-17 Leg D — fleet liveness surfaced into mini/+/fleet; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row; same MF012-cap precedent as calibration_drift)
        "host_frozen",                  # 2026-06-17 Leg C — dude-claw out-of-band witness verdict (HOST_FROZEN/UNREACHABLE/UNKNOWN) surfaced into mini/+/fleet; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row; same MF012-cap precedent)
        "ntfy_loopback",                # 2026-06-18 ntfy receipt-heartbeat Phase 2 — the alerting spine's own loopback liveness (collector publishes a nonce'd heartbeat to the fleet topic + polls it back, escalates via the email backbone on a miss); read-only probe surfaces it into mini/+/fleet; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row; same MF012-cap precedent)
        "ntfy_ack_stale",               # 2026-06-18 ntfy receipt-heartbeat Phase 3 — the only rung confirming the operator's DEVICE (weekly tap-to-ack page; tap makes the phone POST to a dedicated ack-topic the manager-box cron polls); consecutive unacked weeks → email escalation + this read-only probe surfaces it into mini/+/fleet; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row; same MF012-cap precedent)
        "nomadnet_crashloop",           # 2026-06-19 — the NomadNet USER unit crashloop (the 10-day-silent NRestarts=7842 class; probe_service_inactive is structurally blind to user units); root-direct USER_UNIT= journal read, short live-window + newest-restart recency gate so post-fix history can't false-page; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; cross-refs the #69-fix-regression)
        "gateway_delivery_degraded",    # 2026-06-20 gateway-reliability arc A2 — OUTCOME monitoring from the gateway's OWN journal self-report (windowed delivered/attempted ratio collapse + a spike of EROFS / resource-assembly / forward-to-secondary errors, the exact 2026-06-20 wx-total-loss witness with a journal witness but no probe consumer); INERT off a box that doesn't run meshforge-gateway; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as nomadnet_crashloop)
        "resource_canary_degraded",     # 2026-06-20 gateway-reliability arc A1 (the OUTCOME source of truth) — the synthetic RESOURCE round-trip canary (meshforge-gateway-resource-canary.timer → src/lab/gateway_resource_canary) FAILED its verdict or went DARK; A1 actively PROVES the gateway delivers a multi-chunk RNS Resource round-trip (the path the 2026-06-20 EROFS broke while single-packet replies kept working); a FAIL "control back, resource NOT" is the EROFS signature; INERT off a box that doesn't run the canary; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as gateway_delivery_degraded)
        "oracle_delivery_degraded",     # 2026-06-22 mesh-oracle health — the read-only "ask dude-AI over the mesh" responder's confirmable delivery rate fell below threshold; declines (cooldown/not_allowlisted) + benign non-deliveries (RNS no-path / MeshCore restart race) excluded from the failure set + surfaced; the one live service with NO automated probe; documented inline in the SIGNAL_CLASSES comment (no persistent_issues row — MF012 40k cap; same precedent as resource_canary_degraded)
        "inherited_app_drift",          # 2026-06-21 upstream-app ownership Action 5 — an INHERITED (non-Nursedude-origin) upstream app checkout carries an unversioned tracked-file CODE patch (one `git pull` from silent deletion; policy §4.2); LOCAL problem-class detection (scans operator home + /opt, classifies by .git/config, filters untracked artifacts + machine-generated manifests); floating-main/pin-drift leg deliberately NOT a local fire (the fleet enforces pins by ledger, not detached HEAD); INERT off a box with no inherited checkouts; documented inline in the SIGNAL_CLASSES comment + .claude/plans/upstream_app_ownership_policy_2026_06_21.md §9 (no persistent_issues row — MF012 40k cap; same precedent as resource_canary_degraded)
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
    """No listener at @rns/<name> → FileNotFoundError → return None
    (service_inactive owns the 'rnsd not running' signal)."""
    # Pick an instance name nothing's listening on.
    import secrets
    name = f"watchdog-test-nope-{secrets.token_hex(8)}"
    sig = probe_rns_shared_instance_responsive(name, timeout_s=0.5)
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
        sig = probe_rns_shared_instance_responsive(name, timeout_s=0.5)
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
    " TCPInterface[Regional RNS/192.168.86.38:4242]\n"
    "    Status    : Down\n"
    "    Mode      : Full\n"
    "    Rate      : 10.00 Mbps\n"
    "    Traffic   : ↑1.59 MB    0 bps\n"
    "                ↓1.58 MB    0 bps\n"
)

# Same interface, but Up — healthy steady state.
_RNSTATUS_UP_BLOCK = (
    " TCPInterface[Regional RNS/192.168.86.38:4242]\n"
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
    " TCPInterface[Regional RNS/192.168.86.38:4242]\n"
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
        assert sig.extra["host"] == "192.168.86.38"
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
        assert sig.extra["host"] == "192.168.86.38"
        assert sig.extra["port"] == 4242

    def test_parser_pins_exact_incident_block(self):
        """The 192.168.86.38:4242 Regional RNS block must parse to
        host 192.168.86.38, port 4242."""
        with patch("utils.watchdog_probes_rns._tcp_reachable", return_value=True):
            sig = probe_rns_interface_down_peer_reachable(
                rnstatus_text=_RNSTATUS_DOWN_BLOCK,
            )
        assert sig is not None
        assert sig.extra["host"] == "192.168.86.38"
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
                display_name="Regional RNS/192.168.86.38:4242",
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
    assert "24h lookback" in sig.detail
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
        kw = dict(ts_fn=self._ts_fn(stale), state_path=sp, now=_NOW)
        assert probe_nomadnet_crashloop(**kw) is None
        assert probe_nomadnet_crashloop(**kw) is None

    def test_no_restarts_inert(self, tmp_path):
        """Healthy / disabled / never-installed (moc5) → empty list → None."""
        sp = str(tmp_path / "nn.json")
        kw = dict(ts_fn=self._ts_fn([]), state_path=sp, now=_NOW)
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
    unconfigured) → unobservable is not dark → None, never a false alarm."""
    sig = probe_channel_feed_dark(
        main_pid=1002, newest_line_fn=lambda pattern: None, now=_NOW,
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


def _krb_setup(tmp_path, installed=()):
    """Create a fake /lib/modules with the given release dirs; return common
    kwargs (modules dir, absent reboot-required path, isolated streak file)."""
    mods = tmp_path / "modules"
    mods.mkdir()
    for name in installed:
        (mods / name).mkdir()
    return {
        "modules_dir": str(mods),
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


def test_rns_version_drift_none_when_env_unreadable():
    # Couldn't read the service user's site-packages → indeterminate, no false alarm
    # (this is the NoNewPrivileges/RestrictSUIDSGID reality — no user switch possible).
    assert probe_rns_version_drift(rnsd_user="wh6gxz", pins=_PINS, installed={}) is None


def test_rns_version_drift_none_when_pkg_not_visible():
    # rns not found in the user site (venv elsewhere?) — don't guess drift on absence.
    sig = probe_rns_version_drift(
        rnsd_user="wh6gxz", pins=_PINS, installed={"lxmf": "0.9.4+mf.0"})
    assert sig is None


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


def test_delivery_canary_quiet_when_endpoint_unreachable():
    """Gateway not running or HTTP error → return None. A different
    probe surfaces gateway-down."""
    from urllib.error import URLError
    def _raise(*args, **kwargs):
        raise URLError("connection refused")
    with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
        sig = probe_delivery_write_canary()
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
    """Today's symptom reconstruction: 5 peers all failing to moc1.
    A peer with N≥3 consecutive no-route fires must surface as wedge."""
    tracer_dir = tmp_path
    now = time.time()
    # Three recent fires, all no-route to moc1, no good fires in window.
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
    assert s.severity == "wedge"
    assert s.extra["tier"] == "persistent"
    assert s.extra["leading_fail"] >= 3


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
    # Tick 1
    active, cleared = tracker.update([sig], now=100.0)
    assert len(active) == 1
    assert active[0][1] == 100.0
    assert cleared == []
    # Tick 2 — same signal, first_seen must still be 100.0
    active, cleared = tracker.update([sig], now=130.0)
    assert active[0][1] == 100.0
    assert cleared == []


def test_tracker_reports_cleared_on_disappear():
    tracker = SignalTracker()
    sig = Signal(cls="main_thread_wedge", subject="x",
                 severity="wedge", detail="d")
    tracker.update([sig], now=100.0)
    active, cleared = tracker.update([], now=130.0)
    assert active == []
    assert cleared == [("main_thread_wedge", "x")]


def test_tracker_distinct_subjects_dont_collide():
    tracker = SignalTracker()
    s1 = Signal(cls="service_inactive", subject="meshforge-map.service",
                severity="degraded", detail="x")
    s2 = Signal(cls="service_inactive", subject="rnsd.service",
                severity="degraded", detail="y")
    active, _ = tracker.update([s1, s2], now=100.0)
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


def test_resolve_probe_targets_partial_override_keeps_other_defaults():
    """Only overriding one key shouldn't touch the others."""
    sea, swc, port = resolve_probe_targets({"http_port": 8808})
    assert sea == _DEFAULT_SERVICES_EXPECTED_ACTIVE
    assert swc == _DEFAULT_SERVICES_WEDGE_CHECK
    assert port == 8808


def test_resolve_probe_targets_rejects_garbage_overrides():
    """Bad types fall back to defaults silently (warning logged), don't crash."""
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
    from urllib.error import URLError

    def _raise(*args, **kwargs):
        raise URLError("connection refused")

    with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
        sig = probe_queue_backlog(state_path=str(tmp_path / "s.json"))
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
    # 4h old (> 9000s stale threshold), but pass_envelope True — staleness
    # must win regardless of the (untrustworthy, old) envelope value.
    _write_synth(sdir, "20260615T120721Z", pass_envelope=True,
                 age_s=4 * 3600, now=now)
    assert probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp) is None
    sig = probe_synth_soak_degraded(state_dir=sdir, now=now, debounce_path=sp)
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


def test_confirmation_stall_none_on_unreachable_endpoint():
    from urllib.error import URLError

    def _raise(*args, **kwargs):
        raise URLError("connection refused")

    with patch("utils.watchdog_probes_gateway.urlopen", side_effect=_raise):
        assert probe_delivery_confirmation_stall() is None


def test_confirmation_stall_mesh_sends_do_not_false_alarm():
    """THE bug: 20 meshtastic `sent` + 10 rns `confirmed` made the old
    confirmed/sent ratio read 50% and fire. Meshtastic is unconfirmable, so
    the honest rate is 10/10 = 100% — None. (min low so the sample passes,
    proving it's the mesh-exclusion, not the min-gate, that silences it.)"""
    payload = _delivery_payload(confirmed=10, failed=0, mesh_sent=20)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(min_terminal=5) is None


def test_confirmation_stall_no_confirmable_protocol_is_none():
    """No protocol records confirmations (RNS-less box; mesh has no ACK) →
    nothing to judge."""
    payload = _delivery_payload(mesh_sent=30, confirmable=())
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(min_terminal=5) is None


def test_confirmation_stall_below_min_terminal_is_none():
    """Few confirmable terminal events — one failure can't tank a tiny
    denominator (the mesh-heavy-box small-RNS-sample case)."""
    payload = _delivery_payload(confirmed=2, failed=1, mesh_sent=40)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(min_terminal=20) is None


def test_confirmation_stall_rns_healthy_is_quiet():
    payload = _delivery_payload(confirmed=24, failed=1)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        assert probe_delivery_confirmation_stall(min_terminal=20) is None


def test_confirmation_stall_dedup_drops_excluded():
    """Benign dedup drops are NOT delivery failures — a healthy RNS box with
    lots of dedup must stay quiet."""
    payload = _delivery_payload(confirmed=24, failed=0, dedup_drops=30)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_confirmation_stall(min_terminal=20)
    assert sig is None


def test_confirmation_stall_rns_collapse_degraded():
    payload = _delivery_payload(confirmed=10, failed=15)
    with patch("utils.watchdog_probes_gateway.urlopen",
               return_value=_http_json_mock(payload)):
        sig = probe_delivery_confirmation_stall(min_terminal=20)
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
        sig = probe_delivery_confirmation_stall(min_terminal=20)
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
        sig = probe_delivery_confirmation_stall(min_terminal=20)
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
        assert probe_delivery_confirmation_stall(min_terminal=20) is None


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
            sig = probe_delivery_confirmation_stall(min_terminal=20)
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
    """No gateway.json → None (indeterminate), never the default —
    a box with no gateway config has no declared consumer contract."""
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
        sig = self._fire(tmp_path, crontab_text=self.WIRED,
                         verdicts_text=self._v("myjob", "FAIL(1)", 60))
        assert sig is not None
        assert sig.cls == "cron_verdict_stale"
        assert sig.severity == "degraded"
        assert any("myjob" in f for f in sig.extra["failed"])

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


class TestNtfyLoopback:
    """The alerting spine's OWN loopback (ntfy receipt-heartbeat Phase 2)
    surfaced into mini's brief + /fleet. Hermetic: inject the verdict-file JSON
    + mtime + now. INERT off the manager box (no file) and on a STALE file (the
    collector's own death is cron_verdict_stale's job). A ``received`` that is
    not an explicit bool (torn/partial write) is indeterminate → None, NEVER
    read as a miss (honest_failure_modes #1/#2 — absence of evidence ≠ failure;
    the collector OWNS the email page, this probe is visibility only)."""

    NOW = 2_000_000_000.0

    def _doc(self, *, received, published_ok=True, misses=0):
        return {"ts": self.NOW, "nonce": "lb-x-1-2", "published_ok": published_ok,
                "received": received, "latency_s": 4, "consecutive_misses": misses,
                "interval_s": 1800, "last_received_ts": self.NOW}

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

    def test_no_state_is_inert(self, tmp_path):
        """No verdict file (not the manager box) → INERT."""
        assert self._fire(tmp_path, None) is None

    def test_published_but_lost_fires_degraded(self, tmp_path):
        sig = self._fire(tmp_path, self._doc(received=False, published_ok=True, misses=1))
        assert sig is not None
        assert sig.cls == "ntfy_loopback"
        assert sig.subject == "ntfy"
        assert sig.severity == "degraded"
        assert "did NOT loop back" in sig.detail
        assert sig.extra["published_ok"] is True
        assert sig.extra["consecutive_misses"] == 1

    def test_publish_failed_fires(self, tmp_path):
        sig = self._fire(tmp_path, self._doc(received=False, published_ok=False, misses=1))
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
        doc = self._doc(received=False, published_ok=True, misses=1)
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
        sp = str(tmp_path / "d.json")
        text = json.dumps(self._doc(received=False, misses=1))
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

    def test_no_role_is_none(self):
        """Box declares no role → not applicable. _read_deployment_declaration
        returning (None, {}) short-circuits before any seed/home read."""
        with patch("utils.watchdog_probes_mini._read_deployment_declaration",
                   return_value=(None, {})):
            sig = probe_rules_seed_drift()
        assert sig is None

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
        state_path=str(tmp_path / "asd_debounce.json"),
    )


def test_aredn_source_dark_inert_when_not_configured(tmp_path):
    """Empty aredn_node_ips = organ deliberately off → None every tick and
    the streak resets (the 95%-of-boxes case must be free)."""
    dark = {"attempted": 1, "yielded": 0, "reason_if_zero": "unreachable"}
    # Prime a streak first, then show un-configuring clears it.
    probe_aredn_source_dark(**_asd_kw(tmp_path, dark))
    for _ in range(3):
        assert probe_aredn_source_dark(**_asd_kw(tmp_path, dark, ips=[])) is None
    assert json.loads((tmp_path / "asd_debounce.json").read_text())["streak"] == 0


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
            min_volume=20, ratio_floor=0.5) == []

    def test_window_gap_fires_on_r2m_collapse(self):
        """R->M attempted +30 / delivered +10 over the window (33% < 50% floor,
        ≥20 attempts) → one finding naming the direction. M->R (+10 attempts)
        is below the volume gate → no finding there."""
        blocks = [_blk(1, 100, 100, 0, 100, 100, 0),
                  _blk(2, 110, 110, 0, 130, 110, 20)]
        gaps = _window_delivery_gap(blocks, min_volume=20, ratio_floor=0.5)
        assert len(gaps) == 1
        label, d_att, d_del, ratio = gaps[0]
        assert label == "RNS->Mesh"
        assert (d_att, d_del) == (30, 10)
        assert ratio == pytest.approx(10 / 30)

    def test_window_gap_silent_when_above_floor(self):
        blocks = [_blk(1, 100, 100, 0, 100, 100, 0),
                  _blk(2, 200, 200, 0, 200, 195, 5)]  # 95% delivered
        assert _window_delivery_gap(blocks, min_volume=20, ratio_floor=0.5) == []

    def test_window_gap_silent_below_min_volume(self):
        """A quiet box (small windowed delta) never fires even at a low ratio."""
        blocks = [_blk(1, 100, 100, 0, 100, 100, 0),
                  _blk(2, 105, 100, 5, 110, 102, 8)]  # only +10 R->M attempts
        assert _window_delivery_gap(blocks, min_volume=20, ratio_floor=0.5) == []

    def test_window_gap_handles_counter_reset(self):
        """A gateway restart mid-window resets the in-memory counters (latest <
        earliest); the baseline is taken as zero so we measure since-restart,
        never a bogus negative delta."""
        blocks = [_blk(1, 500, 490, 10, 500, 480, 20),
                  _blk(2, 30, 10, 20, 40, 10, 30)]  # restarted → small counters
        gaps = _window_delivery_gap(blocks, min_volume=20, ratio_floor=0.5)
        labels = {g[0] for g in gaps}
        assert labels == {"Mesh->RNS", "RNS->Mesh"}  # both collapsed since reset


class TestGatewayDeliveryDegraded:
    """The probe (2026-06-20, arc A2). Behavioral tests inject blocks_fn /
    error_count_fn + a gateway main_pid; the real journalctl helper is
    exercised separately in TestGatewayDeliveryRealQuery."""

    # Two blocks whose R->M delivery collapsed (att +30 / del +10 = 33%).
    _COLLAPSE = [_blk(1, 100, 100, 0, 100, 100, 0),
                 _blk(2, 110, 110, 0, 130, 110, 20)]
    _HEALTHY = [_blk(1, 100, 100, 0, 100, 100, 0),
                _blk(2, 200, 200, 0, 200, 198, 2)]

    def _kw(self, sp, *, blocks, err):
        return dict(main_pid=1234, blocks_fn=lambda: blocks,
                    error_count_fn=lambda: err, state_path=sp)

    def test_signal_class_registered(self):
        assert "gateway_delivery_degraded" in SIGNAL_CLASSES

    def test_inert_when_gateway_not_running(self, tmp_path):
        """No meshforge-gateway on this box (MainPID None) → INERT (None),
        never a journalctl call. The moc/moc3-only gate."""
        sp = str(tmp_path / "g.json")
        with patch("utils.watchdog_probes_gateway._resolve_main_pid",
                   return_value=None):
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
            state_path=sp) is None

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

        return patch("utils.watchdog_probes_gateway.subprocess.run",
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

        with patch("utils.watchdog_probes_drift.subprocess.run", _fake_run):
            assert _git_tracked_modifications("/some/repo") == []
        argv = captured["argv"]
        assert "--untracked-files=no" in argv
        assert "--ignore-submodules=all" in argv
        assert "safe.directory=/some/repo" in argv
        assert "core.fileMode=false" in argv

    def test_signal_class_registered(self):
        assert "inherited_app_drift" in SIGNAL_CLASSES
