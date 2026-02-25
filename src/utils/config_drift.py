"""
Config Drift Detection for MeshForge

Detects when the gateway's resolved RNS config path diverges from what rnsd
is actually using. This prevents silent misconfigurations where the bridge
reads one config while rnsd operates on another.

Active fix: When rnsd is a systemd service using /etc/reticulum/config,
the gateway should prefer that path for system deploys.

Usage:
    from utils.config_drift import detect_rnsd_config_drift, DriftResult

    result = detect_rnsd_config_drift()
    if result.drifted:
        logger.warning(result.message)
        if result.fix_hint:
            logger.info(result.fix_hint)

    # Active fix: get the correct config dir for gateway to use
    config_dir = get_rnsd_effective_config_dir()
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Import centralized path utility
from utils.paths import ReticulumPaths, get_real_user_home


@dataclass
class DriftResult:
    """Result of a config drift check."""
    drifted: bool
    gateway_config_dir: Optional[Path]   # What the gateway resolved
    rnsd_config_dir: Optional[Path]      # What rnsd is actually using
    rnsd_pid: Optional[int] = None
    detection_method: str = ""           # How we determined rnsd's path
    message: str = ""
    fix_hint: str = ""
    severity: str = "info"               # "info", "warning", "error"

    @property
    def can_auto_fix(self) -> bool:
        """Whether the drift can be resolved by migrating to /etc/reticulum/.

        Auto-fix is possible when drift is detected and at least one path
        is not already /etc/reticulum (meaning migration would help).
        """
        if not self.drifted:
            return False
        # Resolve both sides consistently (handles symlinks)
        etc_path = Path('/etc/reticulum').resolve()
        gw_is_etc = (self.gateway_config_dir
                      and self.gateway_config_dir.resolve() == etc_path)
        rnsd_is_etc = (self.rnsd_config_dir
                        and self.rnsd_config_dir.resolve() == etc_path)
        # If both already point to /etc, migration won't help
        return not (gw_is_etc and rnsd_is_etc)


def _get_rnsd_pid() -> Optional[int]:
    """Get the PID of the running rnsd process.

    Returns:
        PID as int, or None if rnsd is not running.
    """
    try:
        result = subprocess.run(
            ['pgrep', '-x', 'rnsd'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split('\n')[0])
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        pass

    # Fallback: check for python-based rnsd
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'python.*rnsd'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid_str in pids:
                try:
                    return int(pid_str)
                except ValueError:
                    continue
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return None


def _get_rnsd_config_from_proc(pid: int) -> Optional[Path]:
    """Extract rnsd's config directory from /proc/<pid>/cmdline.

    rnsd accepts --config <path> as a command-line argument.
    If not specified, it uses RNS's default resolution.

    Args:
        pid: Process ID of rnsd.

    Returns:
        Config directory path, or None if not determinable from cmdline.
    """
    cmdline_path = Path(f'/proc/{pid}/cmdline')
    if not cmdline_path.exists():
        return None

    try:
        # /proc/pid/cmdline uses null bytes as separators
        raw = cmdline_path.read_bytes()
        args = raw.decode('utf-8', errors='replace').split('\0')
        args = [a for a in args if a]  # Remove empty strings

        # Look for --config or -c followed by a path
        for i, arg in enumerate(args):
            if arg in ('--config', '-c') and i + 1 < len(args):
                config_path = Path(args[i + 1])
                # If it's a file, return its parent directory
                if config_path.is_file():
                    return config_path.parent
                # If it's a directory, return it directly
                if config_path.is_dir():
                    return config_path
                # Path doesn't exist yet but was specified
                return config_path
            # Handle --config=<path> format
            if arg.startswith('--config='):
                config_path = Path(arg.split('=', 1)[1])
                if config_path.is_file():
                    return config_path.parent
                if config_path.is_dir():
                    return config_path
                return config_path

    except (OSError, UnicodeDecodeError) as e:
        logger.debug("Could not read /proc/%d/cmdline: %s", pid, e)

    return None


def _get_rnsd_config_from_systemd() -> Optional[Path]:
    """Extract rnsd's config directory from its systemd unit file.

    Parses the ExecStart line for --config arguments.

    Returns:
        Config directory path from systemd unit, or None.
    """
    try:
        result = subprocess.run(
            ['systemctl', 'show', 'rnsd', '--property=ExecStart', '--no-pager'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        exec_line = result.stdout.strip()
        # ExecStart line format varies; look for --config
        parts = exec_line.split()
        for i, part in enumerate(parts):
            if part in ('--config', '-c') and i + 1 < len(parts):
                config_val = parts[i + 1].rstrip(';')
                config_path = Path(config_val)
                if config_path.is_file():
                    return config_path.parent
                if config_path.is_dir():
                    return config_path
                return config_path
            if part.startswith('--config='):
                config_val = part.split('=', 1)[1].rstrip(';')
                config_path = Path(config_val)
                if config_path.is_file():
                    return config_path.parent
                if config_path.is_dir():
                    return config_path
                return config_path

    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return None


def _get_rnsd_effective_config() -> tuple:
    """Determine what config directory rnsd is actually using.

    Resolution order:
    1. From /proc/<pid>/cmdline (most accurate, running process)
    2. From systemd unit file (if rnsd is a systemd service)
    3. From RNS's default resolution (same as ReticulumPaths)

    Returns:
        Tuple of (config_dir: Optional[Path], pid: Optional[int],
                  detection_method: str)
    """
    pid = _get_rnsd_pid()

    # Method 1: Check running process cmdline
    if pid is not None:
        config_dir = _get_rnsd_config_from_proc(pid)
        if config_dir is not None:
            return config_dir, pid, "proc_cmdline"

    # Method 2: Check systemd unit file
    systemd_config = _get_rnsd_config_from_systemd()
    if systemd_config is not None:
        return systemd_config, pid, "systemd_unit"

    # Method 3: rnsd uses RNS default resolution (no explicit --config)
    # This means rnsd will find config the same way we do,
    # BUT if rnsd runs as root its ~ differs from the sudo user's ~
    if pid is not None:
        # rnsd is running without --config flag
        # Check if rnsd runs as root (systemd services typically do)
        try:
            stat = os.stat(f'/proc/{pid}')
            if stat.st_uid == 0:
                # rnsd runs as root - its default resolution starts at /etc/reticulum
                # then falls to /root/.config/reticulum, then /root/.reticulum
                if Path('/etc/reticulum/config').is_file():
                    return Path('/etc/reticulum'), pid, "rnsd_root_default"
                elif Path('/root/.config/reticulum/config').is_file():
                    return Path('/root/.config/reticulum'), pid, "rnsd_root_default"
                elif Path('/root/.reticulum/config').is_file():
                    return Path('/root/.reticulum'), pid, "rnsd_root_default"
        except OSError:
            pass

        # rnsd runs as non-root (unusual but possible)
        return None, pid, "rnsd_default_unknown"

    # rnsd is not running
    return None, None, "rnsd_not_running"


def detect_rnsd_config_drift() -> DriftResult:
    """Detect if the gateway's RNS config path diverges from rnsd's actual path.

    Compares what ReticulumPaths.get_config_dir() resolves (the gateway's view)
    against what rnsd is actually using (from /proc, systemd, or root defaults).

    Returns:
        DriftResult with drift status, paths, and remediation hints.
    """
    gateway_dir = ReticulumPaths.get_config_dir()
    rnsd_dir, rnsd_pid, method = _get_rnsd_effective_config()

    # rnsd not running - no drift to detect
    if rnsd_pid is None:
        return DriftResult(
            drifted=False,
            gateway_config_dir=gateway_dir,
            rnsd_config_dir=None,
            rnsd_pid=None,
            detection_method="rnsd_not_running",
            message="rnsd is not running; config drift check skipped",
            severity="info",
        )

    # rnsd running but couldn't determine its config
    if rnsd_dir is None:
        return DriftResult(
            drifted=False,
            gateway_config_dir=gateway_dir,
            rnsd_config_dir=None,
            rnsd_pid=rnsd_pid,
            detection_method=method,
            message=(f"rnsd running (PID {rnsd_pid}) but config dir not "
                     "determinable; assuming default resolution matches"),
            severity="info",
        )

    # Compare resolved paths
    try:
        gw_resolved = gateway_dir.resolve()
        rnsd_resolved = rnsd_dir.resolve()
    except OSError:
        gw_resolved = gateway_dir
        rnsd_resolved = rnsd_dir

    if gw_resolved == rnsd_resolved:
        return DriftResult(
            drifted=False,
            gateway_config_dir=gateway_dir,
            rnsd_config_dir=rnsd_dir,
            rnsd_pid=rnsd_pid,
            detection_method=method,
            message=(f"Config aligned: gateway and rnsd both use "
                     f"{gateway_dir}"),
            severity="info",
        )

    # DRIFT DETECTED
    return DriftResult(
        drifted=True,
        gateway_config_dir=gateway_dir,
        rnsd_config_dir=rnsd_dir,
        rnsd_pid=rnsd_pid,
        detection_method=method,
        message=(
            f"CONFIG DRIFT: Gateway resolves to {gateway_dir} but rnsd "
            f"(PID {rnsd_pid}) uses {rnsd_dir} "
            f"(detected via {method})"
        ),
        fix_hint=(
            f"Migrate config to /etc/reticulum/config (system-wide, preferred). "
            f"This ensures rnsd, gateway, and all RNS clients use the same config."
        ),
        severity="warning",
    )


def get_rnsd_effective_config_dir() -> Path:
    """Get the config directory the gateway should use, preferring rnsd's actual path.

    Active fix: If rnsd is running and using a different config than what
    ReticulumPaths would resolve, return rnsd's path instead. This ensures
    the gateway reads the same config as the running daemon.

    For system deploys (rnsd as systemd service), this prefers /etc/reticulum/.

    Returns:
        Path to the config directory the gateway should use.
    """
    rnsd_dir, rnsd_pid, method = _get_rnsd_effective_config()

    if rnsd_dir is not None:
        logger.debug("Using rnsd's config dir: %s (detected via %s)", rnsd_dir, method)
        return rnsd_dir

    # rnsd not running or config not determinable - prefer system path
    # for system deploys, fall back to ReticulumPaths default resolution
    if os.geteuid() == 0 and Path('/etc/reticulum/config').is_file():
        logger.debug("Running as root, preferring /etc/reticulum")
        return Path('/etc/reticulum')

    return ReticulumPaths.get_config_dir()


def validate_gateway_rns_config(config) -> list:
    """Validate gateway config against rnsd's actual state.

    Runs drift detection and returns ConfigValidationError-compatible warnings
    that integrate with GatewayConfig.validate().

    Args:
        config: GatewayConfig instance to validate.

    Returns:
        List of ConfigValidationError instances (imported from gateway.config).
    """
    from gateway.config import ConfigValidationError

    errors = []

    # Check for config drift
    drift = detect_rnsd_config_drift()

    if drift.drifted:
        errors.append(ConfigValidationError(
            field="rns.config_dir",
            message=drift.message,
            severity="warning",
        ))
        if drift.fix_hint:
            errors.append(ConfigValidationError(
                field="rns.config_dir",
                message=f"Fix: {drift.fix_hint}",
                severity="info",
            ))

    # Check if explicit config_dir in gateway.json matches rnsd
    if config.rns.config_dir:
        explicit_dir = Path(config.rns.config_dir)
        if drift.rnsd_config_dir and explicit_dir.resolve() != drift.rnsd_config_dir.resolve():
            errors.append(ConfigValidationError(
                field="rns.config_dir",
                message=(
                    f"gateway.json sets config_dir='{config.rns.config_dir}' "
                    f"but rnsd uses '{drift.rnsd_config_dir}'"
                ),
                severity="warning",
            ))

    # Check if config file actually exists at the resolved path
    gateway_dir = ReticulumPaths.get_config_dir()
    config_file = gateway_dir / 'config'
    if not config_file.is_file():
        errors.append(ConfigValidationError(
            field="rns.config_dir",
            message=f"RNS config file not found at {config_file}",
            severity="error",
        ))

    return errors
