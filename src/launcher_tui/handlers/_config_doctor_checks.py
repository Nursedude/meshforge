"""Config Doctor — pure check functions.

Each function is read-only and returns a ``CheckResult``. No TUI imports
and no fixes applied; the Config Doctor handler renders the results and
routes operators to existing fix handlers (RNS Diagnostics, NomadNet
Service Control, Gateway Preflight).

The checks validate per-box drift scenarios that have historically broken
gateway bridging (Issue #37 / #40 / #41 rpc_key pinning), NomadNet startup
(Issue #38 / #45 tmux supervision), and rnsd interface init (Issue #27
blocking interfaces).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


# Status constants — mirror the gateway_preflight convention so operators
# get a consistent vocabulary across diagnostic surfaces.
OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

_VALID_STATUSES = (OK, WARN, FAIL, SKIP)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class CheckResult:
    """One row in the Config Doctor report.

    Attributes:
        name:       short identifier shown in the summary table
        status:     one of OK / WARN / FAIL / SKIP
        message:    one-line human-readable state description
        fix_hint:   actionable remediation text (may be multi-line)
        route_hint: optional pointer to the handler menu path operators
                    should navigate to (e.g. ``"RNS > Repair RNS"``). Not
                    a live link — the TUI prints it as guidance.
        details:    supplementary data (file paths, values) for the
                    details view; never rendered in the summary table.
    """

    name: str
    status: str
    message: str
    fix_hint: str = ""
    route_hint: str = ""
    details: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"CheckResult.status must be one of {_VALID_STATUSES}, "
                f"got {self.status!r}"
            )


# ---------------------------------------------------------------------------
# rpc_key checks (Issue #37 / #40 / #41)
# ---------------------------------------------------------------------------

def check_rpc_key_pinned() -> CheckResult:
    """Verify rnsd's active config has an explicit ``rpc_key`` pin.

    Without a pin rnsd derives its RPC key from identity private bytes,
    and any MeshForge client writer using a different configdir
    (/tmp/meshforge_rns_client/, for example) gets a divergent key and
    every RPC fails with ``AuthenticationError`` (Issue #37).
    """
    from utils.paths import ReticulumPaths

    cfg_file = ReticulumPaths.get_config_file()
    if not cfg_file.exists():
        return CheckResult(
            name="rpc_key_pinned",
            status=SKIP,
            message=f"RNS config not found at {cfg_file}",
            fix_hint=(
                "rnsd has not been configured on this box yet. Install "
                "and start rnsd, then re-run Config Doctor."
            ),
            route_hint="RNS > RNS Config Setup",
            details=[f"checked: {cfg_file}"],
        )

    key = ReticulumPaths.get_shared_rpc_key()
    if key is None:
        return CheckResult(
            name="rpc_key_pinned",
            status=FAIL,
            message=f"rnsd config has no valid rpc_key: {cfg_file}",
            fix_hint=(
                "Generate a key and add it under [reticulum]:\n"
                "  openssl rand -hex 32\n"
                "Then: sudo systemctl restart rnsd\n"
                "See Issue #41 in persistent_issues.md for context."
            ),
            route_hint="RNS > Repair RNS",
            details=[f"checked: {cfg_file}"],
        )

    return CheckResult(
        name="rpc_key_pinned",
        status=OK,
        message=f"rnsd rpc_key pinned ({_fingerprint(key)})",
        details=[f"checked: {cfg_file}", f"key[:8]: {key[:8]}…"],
    )


# Client-only RNS configs that MeshForge writes on this box. Each must
# propagate the same rpc_key as rnsd or inbound/outbound link-packet RPC
# will fail. Listed as (config_path, owner_description).
_CLIENT_CONFIGS = (
    (Path("/tmp/meshforge_rns_client/config"), "gateway + node_tracker"),
    (Path("/tmp/meshforge_map_collector/config"), "map collector"),
    (Path("/tmp/meshforge_tui_rns/config"), "TUI RNS commands"),
)


def check_rpc_key_client_match() -> CheckResult:
    """Verify MeshForge client configs share rnsd's rpc_key."""
    from utils.paths import ReticulumPaths

    rnsd_key = ReticulumPaths.get_shared_rpc_key()
    if rnsd_key is None:
        return CheckResult(
            name="rpc_key_client_match",
            status=SKIP,
            message="rnsd has no rpc_key pinned — skipped client match",
            fix_hint="Pin rpc_key in rnsd first (see rpc_key_pinned check).",
            route_hint="RNS > Repair RNS",
        )

    present: List[Path] = []
    mismatched: List[str] = []
    for path, owner in _CLIENT_CONFIGS:
        if not path.exists():
            continue
        present.append(path)
        client_key = _read_rpc_key(path)
        if client_key is None:
            mismatched.append(f"{path} ({owner}) — no rpc_key line")
        elif client_key != rnsd_key:
            mismatched.append(
                f"{path} ({owner}) — key[:8]={client_key[:8]}… "
                f"(rnsd: {rnsd_key[:8]}…)"
            )

    if not present:
        return CheckResult(
            name="rpc_key_client_match",
            status=SKIP,
            message="no MeshForge client configs present yet",
            details=["nothing has run that writes /tmp/meshforge_*_rns/config"],
        )

    if mismatched:
        return CheckResult(
            name="rpc_key_client_match",
            status=FAIL,
            message=f"{len(mismatched)} client config(s) disagree with rnsd",
            fix_hint=(
                "Restart MeshForge services so client configs rewrite:\n"
                "  sudo systemctl restart meshforge-map meshforge-gateway\n"
                "Each writer calls ReticulumPaths.get_shared_rpc_key() at "
                "config-write time (Issue #41)."
            ),
            route_hint="System > Daemons",
            details=mismatched,
        )

    return CheckResult(
        name="rpc_key_client_match",
        status=OK,
        message=f"{len(present)} client config(s) match rnsd rpc_key",
        details=[str(p) for p in present],
    )


def _read_rpc_key(config_path: Path) -> Optional[str]:
    """Parse an rpc_key line out of a client RNS config. Mirror-of the
    ReticulumPaths.get_shared_rpc_key logic but operates on an arbitrary
    path so we can compare files."""
    try:
        text = config_path.read_text()
    except (OSError, PermissionError):
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() != "rpc_key":
            continue
        key = value.strip()
        if len(key) == 64 and all(c in "0123456789abcdefABCDEF" for c in key):
            return key.lower()
        return None
    return None


def _fingerprint(key: str) -> str:
    return f"{key[:8]}…{key[-4:]}"


# ---------------------------------------------------------------------------
# rnsd reachability + config drift
# ---------------------------------------------------------------------------

def check_rnsd_reachable() -> CheckResult:
    """Verify the RNS shared instance is actually listening."""
    from utils._port_detection import (
        check_rns_shared_instance,
        get_rns_shared_instance_info,
    )

    info = get_rns_shared_instance_info()
    if check_rns_shared_instance():
        method = info.get("detection_method", "unknown")
        return CheckResult(
            name="rnsd_reachable",
            status=OK,
            message=f"RNS shared instance available ({method})",
            details=[f"detection: {method}"],
        )

    return CheckResult(
        name="rnsd_reachable",
        status=FAIL,
        message="RNS shared instance not detected",
        fix_hint=(
            "Start rnsd:  sudo systemctl start rnsd\n"
            "If rnsd is active but not listening, a blocking interface is "
            "likely holding it in init — see rns_interface_devices check."
        ),
        route_hint="RNS > RNS Diagnostics",
        details=[f"tcp_check: {info.get('tcp', '?')}",
                 f"unix_socket: {info.get('unix_socket', '?')}"],
    )


def check_rnsd_config_drift() -> CheckResult:
    """Delegate to existing config-drift detector (Issue #41 context)."""
    from utils.config_drift import detect_rnsd_config_drift

    result = detect_rnsd_config_drift()
    if not result.drifted:
        return CheckResult(
            name="rnsd_config_drift",
            status=OK,
            message=result.message or "no config drift",
            details=[
                f"gateway dir: {result.gateway_config_dir}",
                f"rnsd dir:    {result.rnsd_config_dir}",
            ],
        )

    status = FAIL if result.severity == "error" else WARN
    return CheckResult(
        name="rnsd_config_drift",
        status=status,
        message=result.message or "config drift detected",
        fix_hint=result.fix_hint or "",
        route_hint="RNS > RNS Diagnostics > Config Drift Check",
        details=[
            f"gateway dir: {result.gateway_config_dir}",
            f"rnsd dir:    {result.rnsd_config_dir}",
        ],
    )


# ---------------------------------------------------------------------------
# RNS interface devices (reuses _rns_interface_mgr.find_blocking_interfaces)
# ---------------------------------------------------------------------------

def check_rns_interface_devices() -> CheckResult:
    """Flag enabled RNS interfaces whose device/host is missing.

    This is the exact check the repair wizard already runs, surfaced as
    read-only diagnosis so operators can see it without entering repair
    flow.
    """
    from ._rns_interface_mgr import find_blocking_interfaces

    blocking = find_blocking_interfaces()
    if not blocking:
        return CheckResult(
            name="rns_interface_devices",
            status=OK,
            message="all enabled RNS interfaces resolve their devices/hosts",
        )

    details = [f"{name}: {problem}" for name, problem, _fix in blocking]
    fix_lines = [f"{name}: {fix}" for name, _problem, fix in blocking]
    return CheckResult(
        name="rns_interface_devices",
        status=FAIL,
        message=f"{len(blocking)} blocking RNS interface(s)",
        fix_hint="\n".join(fix_lines),
        route_hint="RNS > Repair RNS",
        details=details,
    )


# ---------------------------------------------------------------------------
# Gateway default_lxmf_destination format
# ---------------------------------------------------------------------------

def check_gateway_default_lxmf_destination() -> CheckResult:
    """Verify gateway.json's ``rns.default_lxmf_destination`` is 32-hex.

    The gateway falls back to broadcast drops when this hash is empty or
    malformed (``rns_bridge.py`` warns "Invalid default_lxmf_destination
    hex in config"). The gateway_preflight handler already cross-checks
    against NomadNet's live identity; this check is strictly the format
    gate so it surfaces on boxes without a running NomadNet.
    """
    from utils.paths import get_real_user_home

    cfg_path = get_real_user_home() / ".config" / "meshforge" / "gateway.json"
    if not cfg_path.exists():
        return CheckResult(
            name="gateway_default_lxmf_destination",
            status=SKIP,
            message=f"gateway.json not found at {cfg_path}",
            details=["not a gateway host, or gateway never started"],
        )

    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, PermissionError, json.JSONDecodeError) as exc:
        return CheckResult(
            name="gateway_default_lxmf_destination",
            status=FAIL,
            message=f"gateway.json unreadable: {exc}",
            fix_hint="Restore from backup or re-run gateway setup.",
            route_hint="Mesh Networks > Gateway Preflight",
            details=[str(cfg_path)],
        )

    dest = (cfg.get("rns", {}) or {}).get("default_lxmf_destination", "")
    if not dest:
        return CheckResult(
            name="gateway_default_lxmf_destination",
            status=WARN,
            message="rns.default_lxmf_destination is empty",
            fix_hint=(
                "Set it to NomadNet's LXMF identity hash (32 hex chars).\n"
                "Get the hash: grep -A1 'LXMF Address' ~/.nomadnetwork/logfile"
            ),
            route_hint="Mesh Networks > Gateway Preflight",
            details=[str(cfg_path)],
        )

    if not _HEX32.match(dest.lower()):
        return CheckResult(
            name="gateway_default_lxmf_destination",
            status=FAIL,
            message=f"rns.default_lxmf_destination is not 32-hex: {dest!r}",
            fix_hint=(
                "LXMF destination hashes are exactly 32 lowercase hex "
                "characters. Edit gateway.json and correct the value."
            ),
            route_hint="Mesh Networks > Gateway Preflight",
            details=[str(cfg_path), f"value: {dest!r}"],
        )

    return CheckResult(
        name="gateway_default_lxmf_destination",
        status=OK,
        message=f"rns.default_lxmf_destination format OK ({dest[:8]}…)",
        details=[str(cfg_path)],
    )


# ---------------------------------------------------------------------------
# NomadNet service + unit checks (Issue #38 / #45)
# ---------------------------------------------------------------------------

_NOMADNET_UNIT_PATH = ".config/systemd/user/nomadnet.service"


def check_nomadnet_unit_tmux() -> CheckResult:
    """If the NomadNet user unit references a tmux binary, verify it exists.

    The Issue #45 supervised path uses a tmux wrapper
    (``ExecStart=/usr/bin/tmux new-session …``). Boxes provisioned before
    tmux was installed fail silently with ``systemctl --user status``
    showing ``status=203/EXEC``.
    """
    from utils.paths import get_real_user_home

    unit_path = get_real_user_home() / _NOMADNET_UNIT_PATH
    if not unit_path.exists():
        return CheckResult(
            name="nomadnet_unit_tmux",
            status=SKIP,
            message="nomadnet user unit not installed",
            details=[f"expected: {unit_path}"],
        )

    try:
        unit_text = unit_path.read_text()
    except (OSError, PermissionError) as exc:
        return CheckResult(
            name="nomadnet_unit_tmux",
            status=WARN,
            message=f"cannot read {unit_path}: {exc}",
            details=[str(unit_path)],
        )

    tmux_refs = re.findall(r"(/\S*tmux\S*)", unit_text)
    if not tmux_refs:
        # Unit exists but doesn't wrap tmux — nothing to verify.
        return CheckResult(
            name="nomadnet_unit_tmux",
            status=OK,
            message="nomadnet unit does not reference tmux",
            details=[str(unit_path)],
        )

    missing = [p for p in tmux_refs if not Path(p).exists()]
    if missing:
        return CheckResult(
            name="nomadnet_unit_tmux",
            status=FAIL,
            message=f"nomadnet unit references missing tmux: {missing[0]}",
            fix_hint=(
                "Install tmux:\n"
                "  sudo apt install -y tmux\n"
                "Then restart the unit:\n"
                "  systemctl --user daemon-reload && "
                "systemctl --user restart nomadnet"
            ),
            route_hint="NomadNet Client > Service Control",
            details=[str(unit_path)] + missing,
        )

    # Belt-and-braces: also check PATH resolution, in case the unit
    # references a bare name.
    if shutil.which("tmux") is None:
        return CheckResult(
            name="nomadnet_unit_tmux",
            status=FAIL,
            message="tmux not on PATH (nomadnet unit expects it)",
            fix_hint="sudo apt install -y tmux",
            route_hint="NomadNet Client > Service Control",
            details=[str(unit_path)] + tmux_refs,
        )

    return CheckResult(
        name="nomadnet_unit_tmux",
        status=OK,
        message=f"nomadnet tmux binary resolved: {tmux_refs[0]}",
        details=[str(unit_path)] + tmux_refs,
    )


def check_nomadnet_service_state(service_state: Optional[dict]) -> CheckResult:
    """Validate the NomadNet user-unit state SSOT.

    The handler passes in the dict from
    ``NomadNetServiceOpsMixin._nomadnet_service_state()`` so this pure
    function has no sudo/systemctl dependency of its own and stays
    unit-testable.
    """
    if service_state is None:
        return CheckResult(
            name="nomadnet_service_state",
            status=SKIP,
            message="service state unavailable (no NomadNet handler loaded)",
        )

    if not service_state.get("unit_installed"):
        return CheckResult(
            name="nomadnet_service_state",
            status=SKIP,
            message="nomadnet user unit not installed",
            fix_hint=(
                "Install the supervised unit from the TUI so NomadNet "
                "survives reboots and crashes."
            ),
            route_hint="NomadNet Client > Service Control > Install user unit",
        )

    n_restarts = int(service_state.get("n_restarts") or 0)
    sub_state = service_state.get("sub_state") or ""
    active = bool(service_state.get("active"))
    error = service_state.get("error")

    if error:
        return CheckResult(
            name="nomadnet_service_state",
            status=WARN,
            message=f"nomadnet service state unknown: {error}",
            fix_hint=(
                "Run under a non-root shell (loginctl enable-linger must "
                "be set) or attach the unit's user DBus socket."
            ),
            route_hint="NomadNet Client > Status",
        )

    if not active:
        return CheckResult(
            name="nomadnet_service_state",
            status=WARN,
            message=f"nomadnet unit installed but inactive (sub_state={sub_state or '?'})",
            fix_hint="systemctl --user start nomadnet",
            route_hint="NomadNet Client > Service Control",
            details=[f"n_restarts: {n_restarts}"],
        )

    if n_restarts > 0:
        return CheckResult(
            name="nomadnet_service_state",
            status=WARN,
            message=f"nomadnet active but has restarted {n_restarts} time(s)",
            fix_hint=(
                "Inspect journal + tmux pane via NomadNet > Logs > Health "
                "Snapshot; fix the crash cause before it hits rate-limit."
            ),
            route_hint="NomadNet Client > Logs",
            details=[f"sub_state: {sub_state}"],
        )

    return CheckResult(
        name="nomadnet_service_state",
        status=OK,
        message=f"nomadnet supervised and stable (sub_state={sub_state or 'running'})",
        details=[f"n_restarts: {n_restarts}"],
    )


# ---------------------------------------------------------------------------
# Orchestration helper
# ---------------------------------------------------------------------------

def run_all_checks(*, service_state: Optional[dict] = None) -> List[CheckResult]:
    """Run every Config Doctor check and return the ordered result list.

    ``service_state`` is the NomadNet SSOT dict (passed in because the
    source is a handler method, not a free function). If omitted, the
    NomadNet service check degrades to SKIP.
    """
    return [
        check_rpc_key_pinned(),
        check_rpc_key_client_match(),
        check_rnsd_reachable(),
        check_rnsd_config_drift(),
        check_rns_interface_devices(),
        check_gateway_default_lxmf_destination(),
        check_nomadnet_unit_tmux(),
        check_nomadnet_service_state(service_state),
    ]
