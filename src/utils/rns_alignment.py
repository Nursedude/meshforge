"""RNS alignment audit + normalize logic.

The fleet's NomadNet+rnsd+MeshForge-clients triad must agree on
identity and rpc_key, or every cross-process RPC fails with
``AuthenticationError: digest sent was rejected`` (Issue #37, #40, #41,
#46). This module probes a single host's current alignment state and
brings it to the canonical layout.

Canonical layout:
    rnsd:        runs with --config /etc/reticulum (any User)
    NomadNet:    --rnsconfig /etc/reticulum
    MeshForge:   /tmp/meshforge_rns_client/config propagates
                 instance_name + rpc_key from /etc/reticulum/config
    rpc_key:     pinned in /etc/reticulum/config (64-hex)
    Ownership:   /etc/reticulum/config root:root (operator-managed),
                 /home/$USER/.reticulum if present must be $USER:$USER
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

# RNS-tree perms (configdir/logfile/storage ownership for a non-root rnsd) are the
# shared, app-agnostic SSOT carried byte-identical in MeshAnchor + parity-tracked
# (scripts/parity_check.py). rns_alignment delegates that layer here and keeps only
# its MeshForge-fleet-specific rpc_key/client-config alignment. The aliases below
# preserve rns_alignment's historical import surface (callers + tests unchanged).
from utils.rns_tree_perms import (  # noqa: F401
    CANONICAL_CONFIGDIR,
    RnsTreePerms,
    _group_writable,
    _USERNAME_RE,
    apply_logfile_perms,
    build_logfile_perms_script as _build_logfile_perms_script,
    logfile_perms_drift as _tree_logfile_perms_drift,
)

logger = logging.getLogger(__name__)


# ----- dataclasses ----------------------------------------------------------


@dataclass
class ConfigFileFacts:
    """What we can learn about a single RNS config file without leaking secrets."""
    path: Path
    exists: bool = False
    owner: Optional[str] = None  # "user:group"
    has_rpc_key: bool = False
    has_instance_name: bool = False
    instance_name: Optional[str] = None  # actual value (not secret)


@dataclass
class RNSAlignmentState:
    """Snapshot of a single host's RNS alignment."""
    hostname: str
    # rnsd unit
    rnsd_active: bool = False
    rnsd_user: Optional[str] = None  # e.g. "root", "<operator>"
    rnsd_exec_start: Optional[str] = None
    rnsd_configdir: Optional[Path] = None  # what configdir rnsd is *actually* using
    # config files at known locations
    etc_config: Optional[ConfigFileFacts] = None
    user_home_config: Optional[ConfigFileFacts] = None  # /home/<user>/.reticulum/config
    root_home_config: Optional[ConfigFileFacts] = None  # /root/.reticulum/config
    mfclient_config: Optional[ConfigFileFacts] = None   # /tmp/meshforge_rns_client/config
    # Logfile writability (mf.4 / Issue #73 guard). A non-root rnsd that cannot
    # write <configdir>/logfile made RNS.log() self-deadlock on the failed-write
    # fallback (pre-fork-mf.4) and silently loses logs (post-mf.4). Captured for
    # the canonical /etc/reticulum so normalize can repair perms a re-provision
    # left root-owned. None = not probed / inaccessible (never flagged).
    configdir_owner: Optional[str] = None  # "user:group" of /etc/reticulum
    configdir_mode: Optional[str] = None   # octal mode string, e.g. "1775"
    logfile_owner: Optional[str] = None    # "user:group" of /etc/reticulum/logfile
    logfile_exists: bool = False
    # NomadNet user-unit
    nomadnet_unit_installed: bool = False
    nomadnet_unit_rnsconfig: Optional[Path] = None  # what --rnsconfig points at
    # Drift verdict (filled by analyze_drift)
    drift_reasons: List[str] = field(default_factory=list)

    @property
    def aligned(self) -> bool:
        return not self.drift_reasons

    def to_dict(self) -> dict:
        d = asdict(self)
        # Path -> str for JSON
        for k, v in list(d.items()):
            if isinstance(v, Path):
                d[k] = str(v)
            if isinstance(v, dict) and 'path' in v:
                v['path'] = str(v['path']) if v['path'] else None
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ----- probe ---------------------------------------------------------------


def _read_systemd_unit(*paths: str) -> dict:
    """Parse first ExecStart= and User= from rnsd unit + drop-ins.

    Returns dict with 'user', 'exec_start' (last wins per drop-in semantics).
    """
    user = None
    exec_start = None
    for p in paths:
        try:
            with open(p) as f:
                for raw in f:
                    line = raw.strip()
                    if line.startswith('User='):
                        user = line[len('User='):].strip()
                    elif line.startswith('ExecStart='):
                        # systemd allows ExecStart= (empty) to reset; drop-ins
                        # use that pattern. Keep latest non-empty.
                        val = line[len('ExecStart='):].strip()
                        if val:
                            exec_start = val
        except OSError:
            continue
    return {'user': user, 'exec_start': exec_start}


def rnsd_unit_paths() -> List[str]:
    """The rnsd unit file + its drop-ins, in systemd's own read order.

    SSOT so callers can't drift on which drop-ins count (last non-empty
    ``ExecStart=`` wins, so missing one silently changes the answer).
    """
    paths = [
        '/etc/systemd/system/rnsd.service',
        '/lib/systemd/system/rnsd.service',
    ]
    dropin_dir = Path('/etc/systemd/system/rnsd.service.d')
    try:
        if dropin_dir.is_dir():
            paths.extend(str(p) for p in sorted(dropin_dir.glob('*.conf')))
    except OSError:
        pass
    return paths


def rnsd_configdir_of_record() -> Optional[Path]:
    """The configdir the RUNNING rnsd actually uses, or None if no unit.

    The consumer-of-record answer to "which config is authoritative on
    this box" (calibrated_claims #7). Asking ``~/.reticulum`` instead is
    how the watchdog spent 8.8 days probing an RNS instance name that had
    no listener behind it (2026-08-05): under a ROOT systemd service
    ``get_real_user_home()`` is ``/root``, not the operator, and a stale
    ``/root/.reticulum/config`` answered for a daemon running
    ``--config /etc/reticulum``.

    Returns None when no rnsd unit is readable — the caller must treat
    that as "don't know", never as a default.
    """
    try:
        unit = _read_systemd_unit(*rnsd_unit_paths())
        if not unit.get('user') and not unit.get('exec_start'):
            return None
        return _resolve_rnsd_configdir(unit.get('user'), unit.get('exec_start'))
    except OSError:
        return None


def read_rns_instance_name() -> Optional[str]:
    """This box's RNS instance_name, from the config rnsd RUNS AGAINST first.

    Order matters and is the whole point: asking
    ``get_real_user_home()/.reticulum/config`` first is what blinded both
    RNS watchdog probes for 8.8 days — under a ROOT systemd service that
    path is ``/root``, not the operator, and a stale root config answered
    for a daemon started with ``--config /etc/reticulum``. Full account:
    the ``rns_instance_name_mismatch`` entry in
    ``watchdog_probe_core.SIGNAL_CLASSES``.

    Returns None when unreadable/unconfigured — callers must treat that as
    "don't know" (mark dependent probes inert), never as a default name.
    """
    # Lazy import: rns_init pulls in no RNS/heavy modules at import time,
    # and this keeps the two modules acyclic.
    from utils.rns_init import _read_instance_name_from_config

    candidates: List[Path] = []
    rnsd_dir = rnsd_configdir_of_record()
    if rnsd_dir is not None:
        candidates.append(rnsd_dir)
    try:
        from utils.paths import get_real_user_home
        candidates.append(get_real_user_home() / '.reticulum')
    except Exception:
        pass
    candidates.append(Path('/etc/reticulum'))

    for configdir in candidates:
        name = _read_instance_name_from_config(configdir)
        if name:
            return name
    return None


def _resolve_rnsd_configdir(user: Optional[str], exec_start: Optional[str]) -> Path:
    """Decide which configdir rnsd is *actually* using.

    Priority:
      1. ``--config <dir>`` flag in ExecStart
      2. ``$HOME/.reticulum/`` for the systemd User=
    """
    if exec_start:
        m = re.search(r'--config\s+(\S+)', exec_start)
        if m:
            return Path(m.group(1))
    # Default: $HOME/.reticulum/
    if user == 'root':
        return Path('/root/.reticulum')
    if user:
        return Path(f'/home/{user}/.reticulum')
    # No User= → default depends on systemd; treat as root-equivalent
    return Path('/root/.reticulum')


def _stat_owner(path: Path, sudo: bool = False) -> Optional[str]:
    """Return 'user:group' for path, or None if not accessible."""
    try:
        if sudo:
            res = subprocess.run(
                ['sudo', '-n', 'stat', '-c', '%U:%G', str(path)],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            return None
        st = path.stat()
        import grp
        import pwd
        try:
            return f"{pwd.getpwuid(st.st_uid).pw_name}:{grp.getgrgid(st.st_gid).gr_name}"
        except KeyError:
            return f"{st.st_uid}:{st.st_gid}"
    except (OSError, subprocess.SubprocessError):
        return None


def _stat_mode(path: Path, sudo: bool = False) -> Optional[str]:
    """Return the octal mode string (e.g. '1775') for path, or None."""
    try:
        if sudo:
            res = subprocess.run(
                ['sudo', '-n', 'stat', '-c', '%a', str(path)],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            return None
        return oct(path.stat().st_mode & 0o7777)[2:]
    except (OSError, subprocess.SubprocessError):
        return None


def _scan_config_file(path: Path, sudo: bool = False) -> ConfigFileFacts:
    """Look at an RNS config file without leaking the rpc_key value."""
    f = ConfigFileFacts(path=path)
    if sudo:
        # Use sudo to peek at permission-restricted files
        if subprocess.run(
            ['sudo', '-n', 'test', '-f', str(path)],
            capture_output=True, timeout=5,
        ).returncode != 0:
            return f
        f.exists = True
        f.owner = _stat_owner(path, sudo=True)
        try:
            # Match rpc_key whether at column 0 OR indented under a
            # sub-section (RNS configs often indent under [reticulum]).
            # The earlier `^rpc_key` anchor missed indented entries and
            # caused the planner to insert duplicate keys that ConfigObj
            # rejected. See rns_alignment regression test.
            res = subprocess.run(
                ['sudo', '-n', 'grep', '-cE', r'^[[:space:]]*rpc_key',
                 str(path)],
                capture_output=True, text=True, timeout=5,
            )
            f.has_rpc_key = res.returncode == 0 and res.stdout.strip() != '0'
        except subprocess.SubprocessError:
            pass
        try:
            res = subprocess.run(
                ['sudo', '-n', 'grep', '-E', '^instance_name', str(path)],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0:
                line = res.stdout.strip().splitlines()[0] if res.stdout else ''
                if '=' in line:
                    f.has_instance_name = True
                    f.instance_name = line.split('=', 1)[1].strip() or None
        except (subprocess.SubprocessError, IndexError):
            pass
        return f

    # Direct read (no sudo)
    if not path.is_file():
        return f
    f.exists = True
    f.owner = _stat_owner(path)
    try:
        text = path.read_text()
        for raw in text.splitlines():
            # Lstrip so we detect rpc_key whether it's at column 0 or
            # indented inside a sub-section (RNS configs commonly
            # indent under [reticulum]). Without this, the planner would
            # think rpc_key is missing and insert a duplicate, which
            # ConfigObj rejects with "Could not parse the configuration".
            line = raw.lstrip().rstrip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, _, value = line.partition('=')
            name = name.strip()
            if name == 'rpc_key':
                v = value.strip()
                f.has_rpc_key = (
                    len(v) == 64
                    and all(c in '0123456789abcdefABCDEF' for c in v)
                )
            elif name == 'instance_name':
                f.has_instance_name = True
                f.instance_name = value.strip() or None
    except (OSError, PermissionError):
        pass
    return f


def _parse_nomadnet_unit(unit_path: Path) -> Optional[Path]:
    """Pull --rnsconfig <dir> from NomadNet user-unit's ExecStart."""
    if not unit_path.is_file():
        return None
    try:
        text = unit_path.read_text()
    except OSError:
        return None
    m = re.search(r'--rnsconfig\s+(\S+)', text)
    if not m:
        return None
    # Strip any trailing shell quoting (the ExecStart often wraps the inner
    # command in single quotes for the tmux new-session arg).
    raw = m.group(1).rstrip("'\"")
    return Path(raw)


def probe_local() -> RNSAlignmentState:
    """Probe the current host's RNS alignment state. Read-only."""
    hostname = subprocess.run(
        ['hostname'], capture_output=True, text=True, timeout=2,
    ).stdout.strip() or 'unknown'

    unit = _read_systemd_unit(*rnsd_unit_paths())
    rnsd_user = unit['user']
    rnsd_exec = unit['exec_start']
    rnsd_configdir = _resolve_rnsd_configdir(rnsd_user, rnsd_exec)

    rnsd_active = subprocess.run(
        ['systemctl', 'is-active', 'rnsd'],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip() == 'active'

    state = RNSAlignmentState(
        hostname=hostname,
        rnsd_active=rnsd_active,
        rnsd_user=rnsd_user,
        rnsd_exec_start=rnsd_exec,
        rnsd_configdir=rnsd_configdir,
    )

    # Scan known config locations (sudo for /etc and /root)
    state.etc_config = _scan_config_file(
        Path('/etc/reticulum/config'), sudo=True,
    )
    user_home = os.environ.get('SUDO_USER') or os.environ.get('USER') or ''
    if user_home and user_home != 'root':
        state.user_home_config = _scan_config_file(
            Path(f'/home/{user_home}/.reticulum/config'),
        )
    state.root_home_config = _scan_config_file(
        Path('/root/.reticulum/config'), sudo=True,
    )
    state.mfclient_config = _scan_config_file(
        Path('/tmp/meshforge_rns_client/config'),
    )

    # Logfile writability facts for the canonical configdir (mf.4 / #73 guard).
    cd = CANONICAL_CONFIGDIR
    state.configdir_owner = _stat_owner(cd, sudo=True)
    state.configdir_mode = _stat_mode(cd, sudo=True)
    logfile = cd / 'logfile'
    try:
        if subprocess.run(
            ['sudo', '-n', 'test', '-e', str(logfile)],
            capture_output=True, timeout=5,
        ).returncode == 0:
            state.logfile_exists = True
            state.logfile_owner = _stat_owner(logfile, sudo=True)
    except subprocess.SubprocessError:
        pass

    # NomadNet user-unit
    if user_home and user_home != 'root':
        unit_path = Path(f'/home/{user_home}/.config/systemd/user/nomadnet.service')
        if unit_path.is_file():
            state.nomadnet_unit_installed = True
            state.nomadnet_unit_rnsconfig = _parse_nomadnet_unit(unit_path)

    return state


# ----- gateway-startup preflight ---------------------------------------------


def _read_rpc_key_value(path: Path, sudo: bool = False) -> Optional[str]:
    """Return the lowercased 64-hex rpc_key from path, or None.

    Strict: rejects malformed/commented/missing. Used by the gateway
    startup preflight (Hardening F) to compare rnsd's pinned key against
    the gateway's client-config copy and detect divergence — the silent
    Issue #41 failure where rnsd and the gateway have different keys
    and every inbound link-packet RPC AuthError-aborts before LXMF
    delivery.

    The full 64-hex value never appears in any returned dataclass or
    log line; callers fingerprint or compare-equal in-memory only.
    """
    if sudo:
        try:
            res = subprocess.run(
                ['sudo', '-n', 'cat', str(path)],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode != 0:
                return None
            text = res.stdout
        except subprocess.SubprocessError:
            return None
    else:
        try:
            text = path.read_text()
        except (OSError, PermissionError):
            return None

    for raw in text.splitlines():
        line = raw.lstrip().rstrip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, _, value = line.partition('=')
        if name.strip() != 'rpc_key':
            continue
        v = value.strip()
        if len(v) == 64 and all(c in '0123456789abcdefABCDEF' for c in v):
            return v.lower()
        return None
    return None


def check_gateway_rpc_key_alignment(
    etc_path: Path = Path('/etc/reticulum/config'),
    client_path: Path = Path('/tmp/meshforge_rns_client/config'),
) -> Optional[str]:
    """Hardening F: gateway preflight — refuse-loud on rpc_key drift.

    Returns None when the two configs are aligned (either both have the
    same key, or rnsd has no key pinned — the unpinned-but-aligned
    mode the project documents). Returns a human-readable reason string
    when the gateway must refuse to start because inbound RNS traffic
    will silently AuthError.

    Three failure modes:

      1. rnsd has rpc_key, client config exists but has none →
         every inbound link-packet RPC fails (Issue #41 shape).
      2. rnsd has rpc_key K1, client config has rpc_key K2 (drift after
         rnsd identity regen, or after a partial Issue #41 rollout).
      3. Client config exists but is unreadable (permissions wrong) and
         rnsd has a key — same effective failure as case 1.

    Aligned cases (return None):
      - Both have the same key (canonical Issue #46 layout).
      - rnsd has no key (legacy unpinned mode); client copy doesn't matter.
      - Client config doesn't exist yet (first start; gateway will write
        it after this check, propagating the key — covered upstream).

    The full 64-hex key value is never returned in any field. The
    reason string only mentions presence/absence and divergence.
    """
    # Sudo is needed to read /etc/reticulum/config when the gateway
    # service runs as a non-root user; harmless if already root.
    rnsd_key = _read_rpc_key_value(etc_path, sudo=True)
    if rnsd_key is None:
        return None  # rnsd unpinned ⇒ no preflight to enforce

    if not client_path.exists():
        return None  # First start; client config will be written downstream

    client_key = _read_rpc_key_value(client_path, sudo=False)
    if client_key is None:
        return (
            f"rnsd has rpc_key pinned in {etc_path}, but {client_path} has "
            f"no readable rpc_key. The gateway's RNS client and rnsd will "
            f"derive different RPC authkeys, causing every inbound link "
            f"packet to AuthError-abort before LXMF delivery (Issue #41). "
            f"Fix: regenerate the client config (gateway restart usually "
            f"does this), or run scripts/rns_alignment.py normalize."
        )

    if rnsd_key != client_key:
        return (
            f"rpc_key drift: {etc_path} and {client_path} have different "
            f"keys. RPC handshake will fail every time. Fix: delete "
            f"{client_path} and restart gateway so it's regenerated from "
            f"the canonical /etc/reticulum/config, or run "
            f"scripts/rns_alignment.py normalize."
        )

    return None


# ----- analyze ---------------------------------------------------------------


def _logfile_perms_drift(state: RNSAlignmentState) -> Optional[str]:
    """mf.4 / Issue #73 guard — adapter onto the shared RNS-tree-perms SSOT.

    The detection logic (and the canonical layout it enforces) lives in
    ``utils.rns_tree_perms.logfile_perms_drift`` so MeshForge and MeshAnchor share
    one definition. Here we just project the perms-relevant fields out of the
    fuller ``RNSAlignmentState`` and delegate.
    """
    return _tree_logfile_perms_drift(RnsTreePerms(
        rnsd_user=state.rnsd_user,
        configdir_owner=state.configdir_owner,
        configdir_mode=state.configdir_mode,
        logfile_exists=state.logfile_exists,
        logfile_owner=state.logfile_owner,
    ))


def analyze_drift(state: RNSAlignmentState) -> List[str]:
    """Return list of human-readable drift reasons. Empty list = aligned."""
    reasons: List[str] = []

    # rnsd must be using the canonical configdir
    if state.rnsd_configdir != CANONICAL_CONFIGDIR:
        reasons.append(
            f"rnsd uses {state.rnsd_configdir} (canonical: {CANONICAL_CONFIGDIR}); "
            f"add `--config /etc/reticulum` to ExecStart drop-in"
        )

    # /etc/reticulum/config must exist with rpc_key
    if not state.etc_config or not state.etc_config.exists:
        reasons.append(
            f"/etc/reticulum/config missing (canonical alignment file)"
        )
    elif not state.etc_config.has_rpc_key:
        reasons.append(
            f"/etc/reticulum/config has no rpc_key — RPC handshake will derive "
            f"per-identity authkeys, breaking cross-process RPC"
        )

    # NomadNet unit (if installed) must point at canonical configdir
    if state.nomadnet_unit_installed and state.nomadnet_unit_rnsconfig != CANONICAL_CONFIGDIR:
        reasons.append(
            f"NomadNet --rnsconfig is {state.nomadnet_unit_rnsconfig} "
            f"(canonical: {CANONICAL_CONFIGDIR})"
        )

    # MeshForge client must propagate rpc_key
    if state.mfclient_config and state.mfclient_config.exists:
        if not state.mfclient_config.has_rpc_key and (
            state.etc_config and state.etc_config.has_rpc_key
        ):
            reasons.append(
                f"/tmp/meshforge_rns_client/config has no rpc_key but "
                f"/etc/reticulum/config does — clients won't authenticate to rnsd"
            )

    # User-home Reticulum config, if present, must be user-owned
    if state.user_home_config and state.user_home_config.exists:
        owner = state.user_home_config.owner or ''
        if owner.startswith('root:'):
            # Derive the expected owner from the path: /home/<user>/.reticulum/...
            path_parts = state.user_home_config.path.parts
            home_user = (
                path_parts[2] if len(path_parts) >= 3 and path_parts[1] == 'home'
                else '<user>'
            )
            reasons.append(
                f"{state.user_home_config.path} is owned by {owner} — "
                f"should be user-owned (chown {home_user}:{home_user})"
            )

    # Logfile writability for a non-root rnsd (mf.4 / Issue #73 guard)
    logfile_reason = _logfile_perms_drift(state)
    if logfile_reason:
        reasons.append(logfile_reason)

    return reasons


# ----- normalize -------------------------------------------------------------


@dataclass
class NormalizeAction:
    """One step the normalizer plans to take."""
    description: str
    cmd: List[str]  # subprocess argv for the shell action
    requires_sudo: bool = True


def plan_normalize(state: RNSAlignmentState) -> List[NormalizeAction]:
    """Compute the set of state-changing steps to bring this host to canonical.

    Idempotent: running plan_normalize after a successful normalize_local
    on the same host returns an empty list.
    """
    actions: List[NormalizeAction] = []

    # Step 1: ensure /etc/reticulum/config exists.
    # Source preference: existing /etc/reticulum/config > /root/.reticulum/config
    # > /home/<user>/.reticulum/config > generate fresh.
    needs_config_promote = (
        not state.etc_config or not state.etc_config.exists
    )
    if needs_config_promote:
        src = None
        if state.root_home_config and state.root_home_config.exists:
            src = state.root_home_config.path
            src_dir = src.parent
        elif state.user_home_config and state.user_home_config.exists:
            src = state.user_home_config.path
            src_dir = src.parent
        if src is not None:
            actions.append(NormalizeAction(
                description=f"Promote {src} -> /etc/reticulum/config (preserves identity)",
                cmd=['sudo', 'cp', str(src), '/etc/reticulum/config'],
            ))
            # Storage subdir holds identity material
            actions.append(NormalizeAction(
                description=f"Promote {src_dir}/storage -> /etc/reticulum/storage",
                cmd=['sudo', 'cp', '-an', f'{src_dir}/storage/.', '/etc/reticulum/storage/'],
            ))
        else:
            # No existing config to promote — generate a fresh one
            actions.append(NormalizeAction(
                description="Create empty /etc/reticulum/config (rnsd will populate)",
                cmd=['sudo', 'install', '-m', '644', '-o', 'root', '-g', 'root',
                     '/dev/null', '/etc/reticulum/config'],
            ))

    # Step 2: pin rpc_key if missing
    if (
        not state.etc_config
        or not state.etc_config.exists
        or not state.etc_config.has_rpc_key
    ):
        key = secrets.token_hex(32)
        # Use sed to insert under [reticulum]; if [reticulum] is absent, append
        # a stanza. We try the [reticulum]-anchored insert first.
        actions.append(NormalizeAction(
            description="Pin rpc_key in /etc/reticulum/config (64-hex, never logged)",
            # Heredoc-ish: write a small awk that inserts rpc_key after [reticulum]
            # OR appends a [reticulum]\nrpc_key= stanza if missing.
            cmd=['sudo', 'bash', '-c', _build_rpc_key_insert_script(key)],
        ))

    # Step 3: rnsd systemd drop-in for --config /etc/reticulum
    if state.rnsd_configdir != CANONICAL_CONFIGDIR:
        actions.append(NormalizeAction(
            description="Install rnsd drop-in: --config /etc/reticulum",
            cmd=['sudo', 'bash', '-c', _build_rnsd_dropin_script()],
        ))

    # Step 4: fix ownership of user-home Reticulum dir if root-owned
    if state.user_home_config and state.user_home_config.exists:
        owner = state.user_home_config.owner or ''
        if owner.startswith('root:'):
            user_dir = state.user_home_config.path.parent
            user_name = user_dir.parts[2]  # /home/<user>/.reticulum
            actions.append(NormalizeAction(
                description=f"chown {user_dir} back to {user_name}",
                cmd=['sudo', 'chown', '-R', f'{user_name}:{user_name}', str(user_dir)],
            ))

    # Step 5: clean up root-owned /tmp/meshforge_rns_client (will be recreated)
    if (
        state.mfclient_config
        and state.mfclient_config.exists
        and state.mfclient_config.owner
        and state.mfclient_config.owner.startswith('root:')
    ):
        actions.append(NormalizeAction(
            description="Remove root-owned /tmp/meshforge_rns_client (regenerated as user)",
            cmd=['sudo', 'rm', '-rf', '/tmp/meshforge_rns_client'],
        ))

    # Step 5b: logfile writability for a non-root rnsd (mf.4 / Issue #73 guard).
    # A re-provision can recreate the canonical configdir root:root, so a
    # non-root rnsd can't create/rotate/append its logfile -> RNS.log() fails on
    # every write (self-deadlock pre-fork-mf.4 / lost logs). Repair to the proven
    # federator layout. The description deliberately omits "rnsd"/"/etc/reticulum"
    # so Step 6 does NOT trigger a restart: perms take effect on rnsd's next
    # logfile open — no bounce needed for a perms-only converge.
    if _logfile_perms_drift(state):
        user = state.rnsd_user  # validated as a safe username by the drift check
        actions.append(NormalizeAction(
            description=(
                f"Make the RNS configdir + logfile writable by {user} "
                f"(chown/chmod; effective on next write, no restart)"
            ),
            cmd=['sudo', 'bash', '-c', _build_logfile_perms_script(user)],
        ))

    # Step 6: daemon-reload + restart rnsd if any rnsd-touching change happened
    if any('rnsd' in a.description.lower() or '/etc/reticulum' in a.description
           for a in actions):
        actions.append(NormalizeAction(
            description="systemctl daemon-reload",
            cmd=['sudo', 'systemctl', 'daemon-reload'],
        ))
        actions.append(NormalizeAction(
            description="Restart rnsd to pick up new config",
            cmd=['sudo', 'systemctl', 'restart', 'rnsd'],
        ))

    return actions


def _build_rpc_key_insert_script(key: str) -> str:
    """Bash one-liner that inserts `rpc_key = <key>` under [reticulum].

    Idempotent: if rpc_key already present, no-op. If [reticulum] absent,
    append a [reticulum]\\nrpc_key=<key> stanza.
    """
    return f"""
set -e
CFG=/etc/reticulum/config
# Belt-and-suspenders: detect rpc_key whether at column 0 or indented
# inside [reticulum]. RNS commonly stores it indented, and a column-0
# anchor (`^rpc_key`) misses those — producing a duplicate-key insert
# that ConfigObj rejects with "Could not parse the configuration".
if grep -qE '^[[:space:]]*rpc_key' "$CFG"; then exit 0; fi
if grep -qE '^\\[reticulum\\]' "$CFG"; then
  awk -v k='{key}' '
    {{ print }}
    /^\\[reticulum\\]/ && !done {{ print "rpc_key = " k; done=1 }}
  ' "$CFG" > "$CFG.new"
  mv "$CFG.new" "$CFG"
  chmod 644 "$CFG"
else
  printf '\\n[reticulum]\\nrpc_key = %s\\n' '{key}' >> "$CFG"
fi
"""


# _build_logfile_perms_script and apply_logfile_perms now live in the shared
# utils.rns_tree_perms SSOT (imported at module top as aliases) so MeshForge and
# MeshAnchor carry one byte-identical definition of the RNS-tree perms layout.


def _build_rnsd_dropin_script() -> str:
    """Bash that installs/refreshes the rnsd configdir drop-in."""
    return """
set -e
mkdir -p /etc/systemd/system/rnsd.service.d
cat > /etc/systemd/system/rnsd.service.d/configdir.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/bin/rnsd --config /etc/reticulum --service
EOF
chmod 644 /etc/systemd/system/rnsd.service.d/configdir.conf
"""


def normalize_local(state: RNSAlignmentState, dry_run: bool = False) -> List[str]:
    """Apply the normalize plan. Returns list of executed action descriptions.

    Stops on first failure and re-raises subprocess.CalledProcessError.
    """
    actions = plan_normalize(state)
    executed: List[str] = []
    for action in actions:
        if dry_run:
            executed.append(f"[DRY-RUN] {action.description}")
            continue
        logger.info("normalize: %s", action.description)
        subprocess.run(action.cmd, check=True, timeout=60)
        executed.append(action.description)
    return executed
