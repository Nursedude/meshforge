"""
Fleet Backup and Restore Commands

Provides Python-level backup/restore logic for TUI integration.
The underlying backup/restore can also be done via the standalone
bash scripts (scripts/fleet_backup.sh, scripts/fleet_restore.sh)
which work without Python installed.

Backed-up state:
  - /etc/reticulum/          (RNS config + transport identity)
  - /etc/meshtasticd/        (radio daemon config + HAT configs)
  - ~/.config/meshforge/     (gateway identity, settings, DBs)
  - ~/.claude/memory/        (AI memory files)
  - ~/.claude/projects/*/memory/ (project-specific AI memories)

Fleet config is read from ~/.config/meshforge/fleet.json (local only).
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from commands.base import CommandResult, ResultStatus
from utils.backup_paths import reserve_backup_path
from utils.paths import (
    MeshForgePaths,
    MeshtasticPaths,
    ReticulumPaths,
    get_real_user_home,
    get_real_username,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

BACKUP_DIR_NAME = ".meshforge-fleet-backups"


@dataclass
class BackupManifest:
    """Manifest for a fleet backup archive."""
    hostname: str
    timestamp: str
    meshforge_version: str = "unknown"
    meshforge_commit: str = "unknown"
    backup_type: str = "fleet_config"
    items_backed_up: int = 0
    files: List[Dict] = field(default_factory=list)
    total_size_bytes: int = 0
    created_by: str = "fleet_backup.py"


# ============================================================================
# Fleet config
# ============================================================================

def load_fleet_config() -> Optional[Dict]:
    """Load fleet configuration from ~/.config/meshforge/fleet.json.

    Returns None if fleet config doesn't exist (local-only mode).
    """
    config_path = MeshForgePaths.get_config_dir() / "fleet.json"
    if not config_path.is_file():
        return None

    try:
        with open(config_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load fleet config: %s", e)
        return None


def get_backup_base() -> Path:
    """Get the fleet backup base directory."""
    fleet_cfg = load_fleet_config()
    if fleet_cfg and "backup_dir" in fleet_cfg:
        raw = fleet_cfg["backup_dir"]
        return Path(raw.replace("~", str(get_real_user_home())))
    return get_real_user_home() / BACKUP_DIR_NAME


# ============================================================================
# Backup paths
# ============================================================================

def get_backup_paths() -> List[Dict]:
    """Return categorized list of paths to back up.

    Each entry: {"path": Path, "category": str, "critical": bool}
    Only includes paths that actually exist on the system.
    """
    home = get_real_user_home()
    paths = []

    # --- Reticulum (CRITICAL) ---
    rns_config = ReticulumPaths.get_config_file()
    if rns_config.is_file():
        paths.append({"path": rns_config, "category": "reticulum", "critical": True})

    rns_storage = ReticulumPaths.ETC_STORAGE
    for name in ("transport_identity", "known_destinations"):
        p = rns_storage / name
        if p.is_file():
            paths.append({"path": p, "category": "reticulum", "critical": name == "transport_identity"})

    for dirname in ("identities", "ratchets"):
        p = rns_storage / dirname
        if p.is_dir():
            paths.append({"path": p, "category": "reticulum", "critical": False})

    rns_interfaces = ReticulumPaths.ETC_INTERFACES
    if rns_interfaces.is_dir():
        paths.append({"path": rns_interfaces, "category": "reticulum", "critical": False})

    # --- meshtasticd ---
    if MeshtasticPaths.CONFIG_FILE.is_file():
        paths.append({"path": MeshtasticPaths.CONFIG_FILE, "category": "meshtasticd", "critical": True})
    if MeshtasticPaths.CONFIG_D.is_dir():
        paths.append({"path": MeshtasticPaths.CONFIG_D, "category": "meshtasticd", "critical": True})

    # --- MeshForge config ---
    mf_config = MeshForgePaths.get_config_dir()
    if mf_config.is_dir():
        paths.append({"path": mf_config, "category": "meshforge", "critical": True})

    # --- Claude Code memory ---
    claude_dir = home / ".claude"
    if (claude_dir / "memory").is_dir():
        paths.append({"path": claude_dir / "memory", "category": "claude", "critical": False})

    claude_projects = claude_dir / "projects"
    if claude_projects.is_dir():
        for proj_dir in claude_projects.iterdir():
            mem_dir = proj_dir / "memory"
            if mem_dir.is_dir():
                paths.append({"path": mem_dir, "category": "claude", "critical": False})

    if (claude_dir / "settings.json").is_file():
        paths.append({"path": claude_dir / "settings.json", "category": "claude", "critical": False})

    return paths


# ============================================================================
# SQLite safe copy
# ============================================================================

def _safe_sqlite_copy(src: Path, dst: Path) -> bool:
    """Copy a SQLite database safely using .backup command."""
    try:
        result = subprocess.run(
            ["sqlite3", str(src), f".backup '{dst}'"],
            capture_output=True, timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Fallback: plain copy
        shutil.copy2(src, dst)
        return True


# ============================================================================
# Create backup
# ============================================================================

def create_local_backup(backup_dir: Optional[Path] = None) -> CommandResult:
    """Create a backup archive of the local node.

    Args:
        backup_dir: Override backup destination. Defaults to fleet config or
                    ~/.meshforge-fleet-backups/{hostname}/

    Returns:
        CommandResult with data["archive_path"] on success.
    """
    hostname = _get_hostname()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_name = f"{hostname}-{timestamp}"

    if backup_dir is None:
        backup_dir = get_backup_base() / hostname
    backup_dir.mkdir(parents=True, exist_ok=True)

    home = get_real_user_home()

    try:
        with tempfile.TemporaryDirectory(prefix="meshforge-backup-") as tmpdir:
            staging = Path(tmpdir) / archive_name
            staging.mkdir()

            backed_up = 0
            file_inventory = []

            # Collect all backup paths
            for entry in get_backup_paths():
                src = entry["path"]
                category = entry["category"]

                try:
                    if category == "reticulum":
                        rel = src.relative_to(Path("/"))
                    elif category == "meshtasticd":
                        rel = src.relative_to(Path("/"))
                    elif category == "meshforge":
                        rel = Path("home/config/meshforge")
                    elif category == "claude":
                        rel = Path("home/claude") / src.relative_to(home / ".claude")
                    else:
                        continue

                    dst = staging / rel

                    if src.is_file():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        if src.suffix == ".db":
                            _safe_sqlite_copy(src, dst)
                        else:
                            shutil.copy2(src, dst)
                        backed_up += 1
                    elif src.is_dir():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        _copy_dir_contents(src, dst, category)
                        backed_up += 1
                except (OSError, ValueError) as e:
                    logger.warning("Skipping %s: %s", src, e)

            if backed_up == 0:
                return CommandResult(
                    success=False,
                    status=ResultStatus.WARNING,
                    message="No state found to back up",
                )

            # Build file inventory for manifest
            for root, _dirs, files in os.walk(str(staging)):
                for fname in files:
                    fpath = Path(root) / fname
                    rel = fpath.relative_to(staging)
                    stat = fpath.stat()
                    with open(fpath, "rb") as f:
                        sha = hashlib.sha256(f.read()).hexdigest()
                    file_inventory.append({
                        "path": str(rel),
                        "size": stat.st_size,
                        "sha256": sha,
                    })

            # Write manifest
            manifest = BackupManifest(
                hostname=hostname,
                timestamp=datetime.now().isoformat(),
                meshforge_version=_get_meshforge_version(),
                meshforge_commit=_get_meshforge_commit(),
                items_backed_up=backed_up,
                files=sorted(file_inventory, key=lambda x: x["path"]),
                total_size_bytes=sum(f["size"] for f in file_inventory),
            )

            manifest_path = staging / "manifest.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest.__dict__, f, indent=2)

            # Create tarball. archive_name is <hostname>-<second-resolution
            # timestamp>; two runs inside the same second used to overwrite
            # the first archive, which on this path contains identity keys.
            archive_path = reserve_backup_path(
                backup_dir, f"{archive_name}.tar.gz", on_exists="unique")
            with tarfile.open(str(archive_path), "w:gz") as tar:
                tar.add(str(staging), arcname=archive_name)

            # Restrictive permissions (contains identity keys)
            archive_path.chmod(0o600)

            # Update latest symlink
            latest = backup_dir / "latest.tar.gz"
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(archive_path.name)

            # Fix ownership
            _fix_ownership(backup_dir)

            return CommandResult(
                success=True,
                status=ResultStatus.SUCCESS,
                message=f"Backup created: {archive_path.name}",
                data={
                    "archive_path": str(archive_path),
                    "archive_name": archive_name,
                    "items": backed_up,
                    "total_size": manifest.total_size_bytes,
                    "hostname": hostname,
                },
            )

    except Exception as e:
        logger.exception("Backup failed")
        return CommandResult(
            success=False,
            status=ResultStatus.ERROR,
            message="Backup failed",
            error=str(e),
        )


# ============================================================================
# Push to peers
# ============================================================================

def push_to_peers(archive_path: str) -> CommandResult:
    """Push a backup archive to configured peer Pis via SCP.

    Requires fleet.json with peer definitions and SSH key.
    """
    fleet_cfg = load_fleet_config()
    if not fleet_cfg:
        return CommandResult(
            success=False,
            status=ResultStatus.NOT_AVAILABLE,
            message="No fleet config — cannot push to peers",
        )

    home = get_real_user_home()
    ssh_key_raw = fleet_cfg.get("ssh_key", "")
    ssh_key = Path(ssh_key_raw.replace("~", str(home)))

    if not ssh_key.is_file():
        return CommandResult(
            success=False,
            status=ResultStatus.ERROR,
            message=f"SSH key not found: {ssh_key}",
        )

    this_host = fleet_cfg.get("this_host", "")
    peers = fleet_cfg.get("peers", {})
    user = get_real_username()
    hostname = _get_hostname()

    targets = []
    if this_host in peers:
        target_names = peers[this_host].get("backup_targets", [])
        for name in target_names:
            if name in peers:
                targets.append((name, peers[name]["ip"]))

    if not targets:
        return CommandResult(
            success=False,
            status=ResultStatus.WARNING,
            message="No backup targets configured for this host",
        )

    archive_name = Path(archive_path).name
    pushed = []
    failed = []

    for peer_name, peer_ip in targets:
        remote_dir = f".meshforge-fleet-backups/{hostname}"

        ssh_base = [
            "ssh", "-i", str(ssh_key),
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
        ]

        # Create remote dir
        mkdir_result = subprocess.run(
            ssh_base + [f"{user}@{peer_ip}", f"mkdir -p ~/{remote_dir}"],
            capture_output=True, timeout=15,
        )
        if mkdir_result.returncode != 0:
            failed.append(f"{peer_name} ({peer_ip}): unreachable")
            continue

        # SCP archive
        scp_result = subprocess.run(
            [
                "scp", "-i", str(ssh_key),
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                archive_path,
                f"{user}@{peer_ip}:~/{remote_dir}/{archive_name}",
            ],
            capture_output=True, timeout=120,
        )
        if scp_result.returncode != 0:
            failed.append(f"{peer_name} ({peer_ip}): SCP failed")
            continue

        # Update latest symlink
        subprocess.run(
            ssh_base + [
                f"{user}@{peer_ip}",
                f"cd ~/{remote_dir} && ln -sf '{archive_name}' latest.tar.gz",
            ],
            capture_output=True, timeout=15,
        )

        pushed.append(f"{peer_name} ({peer_ip})")

    parts = []
    if pushed:
        parts.append(f"Pushed to {len(pushed)} peer(s)")
    if failed:
        parts.append(f"{len(failed)} failed")

    return CommandResult(
        success=len(pushed) > 0,
        status=ResultStatus.SUCCESS if pushed else ResultStatus.WARNING,
        message="; ".join(parts),
        data={"pushed": pushed, "failed": failed},
    )


# ============================================================================
# List backups
# ============================================================================

def list_backups(hostname: Optional[str] = None) -> CommandResult:
    """List available backup archives.

    Args:
        hostname: Filter by hostname. None lists all.
    """
    base = get_backup_base()
    if not base.is_dir():
        return CommandResult(
            success=True,
            status=ResultStatus.SUCCESS,
            message="No backups found",
            data={"hosts": {}},
        )

    hosts = {}
    for host_dir in sorted(base.iterdir()):
        if not host_dir.is_dir():
            continue
        host_name = host_dir.name
        if hostname and host_name != hostname:
            continue

        archives = sorted(host_dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        # Exclude the "latest" symlink from the list
        archives = [a for a in archives if not a.is_symlink()]

        if not archives:
            continue

        latest_target = None
        latest_link = host_dir / "latest.tar.gz"
        if latest_link.is_symlink():
            latest_target = latest_link.resolve().name

        host_backups = []
        for archive in archives:
            host_backups.append({
                "name": archive.name,
                "size": archive.stat().st_size,
                "modified": datetime.fromtimestamp(archive.stat().st_mtime).isoformat(),
                "is_latest": archive.name == latest_target,
            })

        hosts[host_name] = host_backups

    total = sum(len(v) for v in hosts.values())
    return CommandResult(
        success=True,
        status=ResultStatus.SUCCESS,
        message=f"{total} backup(s) across {len(hosts)} host(s)",
        data={"hosts": hosts},
    )


# ============================================================================
# Verify backup
# ============================================================================

def verify_backup(archive_path: str) -> CommandResult:
    """Verify a backup archive integrity."""
    path = Path(archive_path)
    if not path.is_file():
        return CommandResult(
            success=False,
            status=ResultStatus.ERROR,
            message=f"File not found: {archive_path}",
        )

    try:
        with tarfile.open(str(path), "r:gz") as tar:
            members = tar.getmembers()
            has_manifest = any("manifest.json" in m.name for m in members)
            file_count = len(members)

        return CommandResult(
            success=True,
            status=ResultStatus.SUCCESS,
            message=f"Archive OK: {file_count} entries, manifest={'yes' if has_manifest else 'no'}",
            data={
                "file_count": file_count,
                "has_manifest": has_manifest,
                "size": path.stat().st_size,
            },
        )
    except (tarfile.TarError, OSError) as e:
        return CommandResult(
            success=False,
            status=ResultStatus.ERROR,
            message="Archive is corrupted",
            error=str(e),
        )


# ============================================================================
# Rotate backups
# ============================================================================

def rotate_backups(keep: int = 7) -> CommandResult:
    """Remove old backups, keeping the most recent N per host."""
    base = get_backup_base()
    if not base.is_dir():
        return CommandResult(
            success=True,
            status=ResultStatus.SUCCESS,
            message="No backups to rotate",
        )

    removed_total = 0
    for host_dir in base.iterdir():
        if not host_dir.is_dir():
            continue

        archives = sorted(
            [a for a in host_dir.glob("*.tar.gz") if not a.is_symlink()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for old in archives[keep:]:
            try:
                old.unlink()
                removed_total += 1
            except OSError as e:
                logger.warning("Could not remove %s: %s", old, e)

    return CommandResult(
        success=True,
        status=ResultStatus.SUCCESS,
        message=f"Removed {removed_total} old backup(s)" if removed_total else "Nothing to rotate",
        data={"removed": removed_total},
    )


# ============================================================================
# Helpers
# ============================================================================

def _get_hostname() -> str:
    """Get short hostname."""
    try:
        result = subprocess.run(
            ["hostname", "-s"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def _get_meshforge_version() -> str:
    """Get MeshForge version string."""
    try:
        import importlib
        spec = importlib.util.spec_from_file_location(
            "__version__",
            "/opt/meshforge/src/__version__.py",
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "__version__", "unknown")
    except Exception:
        pass
    return "unknown"


def _get_meshforge_commit() -> str:
    """Get current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "-C", "/opt/meshforge", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def _copy_dir_contents(src: Path, dst: Path, category: str) -> None:
    """Copy directory contents, handling SQLite databases safely."""
    dst.mkdir(parents=True, exist_ok=True)

    for item in src.iterdir():
        dst_item = dst / item.name

        if item.is_dir():
            # Skip large/ephemeral subdirs in meshforge config
            if category == "meshforge" and item.name in ("logs", "diagnostics", "cache"):
                continue
            _copy_dir_contents(item, dst_item, category)
        elif item.is_file():
            # Skip WAL/SHM files (transient SQLite artifacts)
            if item.suffix in (".db-shm", ".db-wal"):
                continue
            # Skip fleet.json (contains SSH keys/IPs — will be recreated locally)
            if item.name == "fleet.json":
                continue
            if item.suffix == ".db":
                _safe_sqlite_copy(item, dst_item)
            else:
                shutil.copy2(item, dst_item)


def _fix_ownership(path: Path) -> None:
    """Fix ownership if running under sudo."""
    username = get_real_username()
    if username == "root" or username == "unknown":
        return

    try:
        import pwd
        pw = pwd.getpwnam(username)
        uid, gid = pw.pw_uid, pw.pw_gid

        for dirpath, dirnames, filenames in os.walk(str(path)):
            dp = Path(dirpath)
            if dp.stat().st_uid == 0:
                os.chown(str(dp), uid, gid)
            for fname in filenames:
                fp = dp / fname
                if fp.stat().st_uid == 0:
                    os.chown(str(fp), uid, gid)
    except (KeyError, OSError):
        pass
