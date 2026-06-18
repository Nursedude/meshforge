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
    "aredn_source_dark",  # 2026-06-12 AREDN Phase 0: a box with aredn_node_ips configured whose local sysinfo collection reports unreachable/not_configured — the AREDN organ went blind (or the running service predates the config); found dormant on the AREDN-site box itself
    "dep_version_drift",  # 2026-06-12: a critical pip dep (meshtastic) installed BELOW the requirements/core.txt floor in the service user's env — a box that missed/failed an update. rns/lxmf have their own fork-pin probe; this covers the meshtastic-lib gap nothing watched (the recurring update class, feedback_version_env_rigor)
    "dep_install_fragmented",  # 2026-06-17: meshtastic installed at DIVERGENT versions across root-readable locations (venv vs system-wide dist-packages vs root/user pipx vs user-site) with ≥1 copy BELOW the core.txt floor — the service consumer-of-record can be fine while the TUI-as-root reads a stale stray and shows phantom "update available". The install-fragmentation half of the recurring update class; dep_version_drift watches only the service-user consumer and is blind to strays (feedback_version_env_rigor)
    "synth_soak_degraded",  # 2026-06-15: the hourly LXMF synth soak (meshforge-synth-soak.timer) FAILED its delivery envelope or went DARK — the gateway's real round-trip exerciser writes a pass/fail envelope but the fire script always exits 0 and nothing consumed it, so a delivery regression or a silent timer was invisible (the "canary itself unwatched" gap; silence-as-failure for a fixed-cadence generator)
    "calibration_drift",  # 2026-06-15: a VERIFIED completion claim (Claude's "done/100%/all green") did NOT hold when re-derived against external ground truth — the calibration spine turned on the assistant itself, so "you said 100% and the math was wrong" becomes a tracked number instead of a private impression. SSOT .claude/rules/calibrated_claims.md (NOT a code/fleet bug → no issue#). Self-guards None off the dev/manager box (no ledger).
    "fleet_box_unreachable",  # 2026-06-17 Leg D: a fleet box the offline-monitor (fleet_offline_check.sh on the manager box) has confirmed DOWN past its 3-fail (~15min) threshold and is re-paging — surfaced into mini's brief + /fleet so a dark box can't sit silent in a side-channel logfile (the .32 33h-dark lesson; the monitor owns the ntfy, this is visibility). Self-guards INERT off the manager box (no state file) and on a stale state file (cron_verdict_stale owns the dead-monitor case — fleet_offline_check is verdict-wired). No issue#.
    "host_frozen",  # 2026-06-17 Leg C: the dude-claw out-of-band witness (host_probe tool over NATS, on the watched box's own subnet) reports a target's verdict — HOST_FROZEN (IP stack answers but the app port serves no banner = kernel alive / userspace swap-wedged, the .32 class the self-petted HW watchdog can't catch), UNREACHABLE (no TCP answer = host/path/SoC down), or UNKNOWN (the claw witness itself couldn't be reached, sustained → lost visibility, not "healthy"). An out-of-band collector cron on the claw's brain box writes the verdict file; this probe reads it (no NATS in the sandboxed watchdog), mirroring fleet_box_unreachable's file-read pattern. Self-guards INERT off the brain box (no verdict file) and on a stale file. Alert-only (propose_escalation). No issue#.
)

SEVERITIES = ("info", "degraded", "wedge")


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
