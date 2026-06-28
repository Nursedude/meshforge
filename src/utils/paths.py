"""
MeshForge Path Constants

Centralized path definitions to reduce hardcoding across the codebase.

IMPORTANT: Always use get_real_user_home() instead of Path.home() when
the path should be in the user's home directory. This handles the case
where MeshForge is run with sudo but needs to access the real user's
config files, not root's.
"""

from pathlib import Path
from typing import Optional
import os
import tempfile


# ============================================================================
# Core utility functions - use these instead of Path.home()
# ============================================================================

def _resolve_home_for_user(username: str) -> Path:
    """Resolve the home directory for a username via pwd (not hardcoded /home)."""
    try:
        import pwd
        return Path(pwd.getpwnam(username).pw_dir)
    except (KeyError, ImportError):
        # Fallback if user not in passwd or pwd unavailable (non-POSIX)
        return Path(f'/home/{username}')


def get_real_user_home() -> Path:
    """
    Get the real user's home directory, even when running as root via sudo.

    IMPORTANT: Use this instead of Path.home() for user config files.
    When MeshForge is run with 'sudo python3 src/launcher.py', Path.home()
    returns /root, but we want /home/<actual_user>.

    Returns:
        Path to the real user's home directory
    """
    # Check SUDO_USER first (with path traversal protection)
    sudo_user = os.environ.get('SUDO_USER', '')
    if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
        return _resolve_home_for_user(sudo_user)

    # Try LOGNAME as secondary
    logname = os.environ.get('LOGNAME', '')
    if logname and logname != 'root' and '/' not in logname and '..' not in logname:
        return _resolve_home_for_user(logname)

    # Fallback to current user (may be /root under sudo)
    return Path.home()


def get_real_username() -> str:
    """
    Get the real username, even when running as root via sudo.

    Returns:
        The real username string
    """
    sudo_user = os.environ.get('SUDO_USER', '')
    if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
        return sudo_user

    logname = os.environ.get('LOGNAME', '')
    if logname and logname != 'root' and '/' not in logname and '..' not in logname:
        return logname

    return os.environ.get('USER', 'unknown')


# ============================================================================
# Path classes
# ============================================================================

class MeshtasticPaths:
    """Paths related to meshtasticd configuration"""

    ETC_BASE = Path('/etc/meshtasticd')
    CONFIG_FILE = ETC_BASE / 'config.yaml'
    CONFIG_D = ETC_BASE / 'config.d'
    AVAILABLE_D = ETC_BASE / 'available.d'

    @classmethod
    def ensure_config_dirs(cls) -> bool:
        """Create configuration directories if they don't exist. Returns True on success."""
        try:
            cls.CONFIG_D.mkdir(parents=True, exist_ok=True)
            cls.AVAILABLE_D.mkdir(parents=True, exist_ok=True)
            return True
        except PermissionError:
            return False


class ReticulumPaths:
    """Paths related to Reticulum/RNS configuration.

    Uses get_real_user_home() so that .reticulum resolves to the real
    user's home (e.g. /home/user/.reticulum) even when running under sudo.

    Resolution order (mirrors RNS.Reticulum.__init__):
      1. /etc/reticulum/config (system-wide)
      2. ~/.config/reticulum/config (XDG-style)
      3. ~/.reticulum/config (traditional fallback)
    """

    # System-wide paths
    ETC_BASE = Path('/etc/reticulum')
    ETC_STORAGE = ETC_BASE / 'storage'
    ETC_RATCHETS = ETC_STORAGE / 'ratchets'
    ETC_RESOURCES = ETC_STORAGE / 'resources'
    ETC_CACHE = ETC_STORAGE / 'cache'
    ETC_ANNOUNCE_CACHE = ETC_CACHE / 'announces'
    ETC_DISCOVERY = ETC_STORAGE / 'discovery'
    ETC_INTERFACES = ETC_BASE / 'interfaces'

    @classmethod
    def ensure_system_dirs(cls) -> bool:
        """Create system-wide Reticulum directories if they don't exist.

        RNS requires a 'storage' subdirectory in its config directory.
        When using /etc/reticulum/config, this means /etc/reticulum/storage
        must exist with proper permissions before rnsd can start.

        The 'ratchets' subdirectory is required by RNS Identity.persist_job()
        for key ratcheting support. The 'resources' subdirectory is required
        by RNS Reticulum.__init__() for resource storage. The 'cache/announces'
        subdirectory is required by Transport jobs for announce caching.
        Without these, rnsd crashes with PermissionError.

        Also fixes file permissions inside storage/ — if files were created
        by a different user (e.g. root vs rnsd), Transport jobs fail with
        PermissionError on individual announce cache files.

        Returns:
            True if directories exist or were created, False on permission error.

        Note:
            Requires root/sudo to create directories in /etc.
        """
        try:
            # Save and clear umask so mkdir gets the actual mode we request.
            # Default umask 0o022 would turn 0o777 into 0o755.
            old_umask = os.umask(0)
            try:
                cls.ETC_BASE.mkdir(mode=0o755, parents=True, exist_ok=True)
                # Storage directories need world-writable so rnsd (which may
                # run as a non-root service user) can create and modify cache
                # files, ratchets, and announce entries.
                cls.ETC_STORAGE.mkdir(mode=0o777, parents=True, exist_ok=True)
                cls.ETC_RATCHETS.mkdir(mode=0o777, parents=True, exist_ok=True)
                cls.ETC_RESOURCES.mkdir(mode=0o777, parents=True, exist_ok=True)
                cls.ETC_CACHE.mkdir(mode=0o777, parents=True, exist_ok=True)
                cls.ETC_ANNOUNCE_CACHE.mkdir(mode=0o777, parents=True, exist_ok=True)
                cls.ETC_DISCOVERY.mkdir(mode=0o777, parents=True, exist_ok=True)
                cls.ETC_INTERFACES.mkdir(mode=0o755, parents=True, exist_ok=True)
            finally:
                os.umask(old_umask)

            # Fix file permissions inside storage/ — rnsd Transport jobs
            # need read/write on all files under cache/announces/ and
            # ratchets/.  If files were created by a different user,
            # rnsd crashes with PermissionError on individual files.
            cls._fix_storage_file_permissions()

            return True
        except PermissionError:
            return False

    @classmethod
    def _fix_storage_file_permissions(cls):
        """Make all files under /etc/reticulum/storage/ world-readable/writable.

        rnsd may run as root or as a service user. When MeshForge (running
        as sudo) creates files, they may be owned by root and inaccessible
        to rnsd's service user. Rather than guessing which user rnsd runs
        as, we set 0o666 on files and 0o777 on dirs within storage/ so
        any local user can read/write. This is acceptable because:
        - /etc/reticulum/storage/ contains caches and ephemeral data
        - The actual secrets (identity, keys) are in the parent config dir
        - This matches RNS's own behavior of creating world-readable storage

        Uses os.walk for full recursion — RNS may create subdirectories
        beyond the ones we explicitly know about (e.g. new cache categories).
        """
        import stat

        storage_root = cls.ETC_STORAGE
        if not storage_root.is_dir():
            return

        try:
            for dirpath, dirnames, filenames in os.walk(str(storage_root)):
                dp = Path(dirpath)
                # Fix directory permissions
                try:
                    if dp.stat().st_mode & 0o777 != 0o777:
                        dp.chmod(0o777)
                except (PermissionError, OSError):
                    pass

                # Fix file permissions
                for fname in filenames:
                    try:
                        fpath = dp / fname
                        current = fpath.stat().st_mode
                        if not (current & stat.S_IWOTH):
                            fpath.chmod(0o666)
                    except (PermissionError, OSError):
                        pass  # Best effort — some files may be locked
        except (PermissionError, OSError):
            pass  # Best effort

        # Also fix files in the config directory itself (identity, config).
        # NomadNet and other RNS clients need to read the identity file to
        # authenticate with rnsd's shared instance.  If the identity was
        # created by root (via sudo MeshForge), non-root users can't read
        # it and RNS generates a different identity → auth mismatch.
        for fname in ('identity', 'config'):
            fpath = cls.ETC_BASE / fname
            try:
                if fpath.exists():
                    current = fpath.stat().st_mode
                    # Make world-readable (not writable — only rnsd writes)
                    if not (current & stat.S_IROTH):
                        fpath.chmod(current | stat.S_IROTH | stat.S_IRGRP)
            except (PermissionError, OSError):
                pass

    @classmethod
    def get_config_dir(cls) -> Path:
        """Get Reticulum config directory.

        Checks locations in the same order as RNS.Reticulum.__init__:
          1. /etc/reticulum/ (system-wide)
          2. ~/.config/reticulum/ (XDG-style)
          3. ~/.reticulum/ (traditional, default)
        """
        # System-wide config
        if Path('/etc/reticulum').is_dir() and Path('/etc/reticulum/config').is_file():
            return Path('/etc/reticulum')

        # XDG-style user config
        user_home = get_real_user_home()
        xdg_dir = user_home / '.config' / 'reticulum'
        if xdg_dir.is_dir() and (xdg_dir / 'config').is_file():
            return xdg_dir

        # Traditional fallback
        return user_home / '.reticulum'

    @classmethod
    def get_config_file(cls) -> Path:
        """Get main RNS config file"""
        return cls.get_config_dir() / 'config'

    @classmethod
    def get_interfaces_dir(cls) -> Path:
        """Get RNS custom interfaces directory (for plugins like Meshtastic_Interface)"""
        return cls.get_config_dir() / 'interfaces'

    @classmethod
    def get_shared_rpc_key(cls) -> Optional[str]:
        """Resolve the rnsd shared-instance rpc_key for client config writers.

        rnsd derives its RPC key from the transport identity's private bytes by
        default. Any client using a different configdir (e.g. the gateway's
        /tmp/meshforge_rns_client/) gets a different identity and therefore a
        different key — every RPC to rnsd then fails with
        ``AuthenticationError: digest sent was rejected`` (Issue #37, #40).

        Resolution order:
          1. Explicit ``rpc_key`` line in the active RNS config — return it
             verbatim. (Pinning makes the key deterministic and
             identity-independent. Operators on RNS 1.1.x typically pin.)
          2. Derive from rnsd's transport identity (RNS 1.2.0 default
             behavior): ``Identity.full_hash(transport_identity.private_key)``
             read from ``<configdir>/storage/transport_identity``. RNS 1.2.0
             requires this match for inbound Link packets to clear
             ``Link.__update_phy_stats`` without
             ``AuthenticationError: digest sent was rejected``, which would
             otherwise drop ALL inbound LXMF DMs to the daemon
             (sister-project MeshAnchor commits e226ccbb + 0a6502b6).
          3. Return None — caller writes no rpc_key line and falls back to
             RNS's own derivation (which works only when client and rnsd
             share the same configdir/identity).

        Note: the RNS option name is literally ``rpc_key``
        (``Reticulum.py`` line ~477). An earlier helper variant used
        ``shared_instance_rpc_key`` which RNS silently ignores, causing
        the pin to be a no-op and the AuthenticationError to recur on
        boxes with identity-split between rnsd and clients.

        Returns the 64-char lowercase hex string, or None.
        """
        # Step 1: explicit rpc_key line in config
        cfg = cls.get_config_file()
        try:
            text = cfg.read_text()
        except (OSError, PermissionError):
            text = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            name, _, value = line.partition('=')
            if name.strip() != 'rpc_key':
                continue
            key = value.strip()
            if len(key) == 64 and all(c in '0123456789abcdefABCDEF' for c in key):
                return key.lower()
            # Explicit but malformed — don't silently fall through to
            # derivation; operator's intent was the (broken) explicit value.
            return None

        # Step 2: derive from rnsd's transport identity (RNS 1.2.0+ default).
        # Use get_config_file().parent (not get_config_dir()) so tests that
        # patch get_config_file stay self-isolated against the real /etc.
        try:
            import RNS  # type: ignore
            identity_path = cls.get_config_file().parent / 'storage' / 'transport_identity'
            if identity_path.is_file():
                identity = RNS.Identity.from_file(str(identity_path))
                if identity is not None:
                    return RNS.Identity.full_hash(identity.get_private_key()).hex()
        except Exception:
            return None

        return None

    @classmethod
    def get_configured_instance_name(cls) -> str:
        """Read the ``instance_name`` option from the active RNS config.

        RNS namespaces its shared-instance socket as ``@rns/<instance_name>``.
        The default is ``default`` when the option is omitted. If rnsd runs
        under a non-default instance_name (e.g. ``volcano ai rns``) and a
        MeshForge-written client config omits the name, the client binds
        its OWN fresh shared-instance socket instead of attaching to rnsd —
        the path table comes up empty and every caller sees "no RNS peers"
        even though rnsd is healthy.

        Every client-config writer in the codebase must propagate whatever
        rnsd is actually using. Passed both into the client's
        ``[reticulum]`` config and into ``check_rns_shared_instance()``.
        """
        try:
            text = cls.get_config_file().read_text()
        except (OSError, PermissionError):
            return 'default'
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            name, _, value = line.partition('=')
            if name.strip() == 'instance_name':
                return value.strip() or 'default'
        return 'default'

    # FIXED dir name under tempdir for the canonical clean-client RNS config —
    # makes the gateway process's RNS.Reticulum resourcepath deterministic
    # (gw-resourcepath-determinism, 2026-06-27).
    RNS_CLIENT_DIRNAME = 'meshforge_rns_client'

    @classmethod
    def ensure_rns_client_configdir(cls) -> str:
        """Idempotently build + return the canonical clean-client RNS configdir.

        Writes a NO-INTERFACE shared-instance client config to
        ``<tmpdir>/meshforge_rns_client/config`` (``share_instance = Yes``, the
        box ``instance_name``, the shared ports, and rnsd's ``rpc_key`` when
        pinned) and returns the directory.

        Every RNS client in the GATEWAY process (the RNS↔Meshtastic bridge AND
        the node tracker) MUST init the process-wide RNS singleton through THIS
        configdir so ``RNS.Reticulum.resourcepath`` is DETERMINISTIC — not
        "whichever client won the singleton-init race" (the 2026-06-27 finding:
        the bridge resolved to /etc/reticulum OR an unwritable ~/.reticulum,
        node_tracker to /tmp, and the resourcepath was whoever ran first).
        Anchoring on a tmp (PrivateTmp under the gateway unit) client config
        also designs OUT the #60 EROFS-on-resourcepath class — PrivateTmp is
        always writable. The gateway's DELIVERY identity + LXMF storage live
        under ~/.config/meshforge (persistent), independent of this RNS
        configdir, so the ephemeral RNS state never touches delivery.

        "No interfaces" keeps the client from binding ports rnsd owns; the FIXED
        location makes the resourcepath deterministic. Pinned by
        ``TestReticulumClientConfigdir`` + the determinism regression guard.
        """
        import tempfile

        d = Path(tempfile.gettempdir()) / cls.RNS_CLIENT_DIRNAME
        d.mkdir(exist_ok=True)
        instance_name = cls.get_configured_instance_name()
        lines = [
            "# MeshForge gateway RNS client config (auto-generated — NO interfaces).",
            "# FIXED location so the gateway process's RNS.Reticulum resourcepath",
            "# is deterministic (ReticulumPaths.ensure_rns_client_configdir).",
            "[reticulum]",
            "share_instance = Yes",
            "shared_instance_port = 37428",
            "instance_control_port = 37429",
            f"instance_name = {instance_name}",
        ]
        rpc_key = cls.get_shared_rpc_key()
        if rpc_key:
            lines.append(f"rpc_key = {rpc_key}")
        (d / "config").write_text("\n".join(lines) + "\n")
        return str(d)


class MeshChatXPaths:
    """Paths related to MeshChatX (third-party RNS web chat client).

    MeshChatX is an LXMF web client that runs as a long-lived HTTP daemon
    bound to ``127.0.0.1:8000`` by default. It coexists with NomadNet —
    each has its own LXMF identity stored in a separate directory.

    The storage_dir holds the MeshChatX identity, message history DB,
    and runtime state. We pin it under ``~/.local/share/meshchatx/``
    rather than MeshChatX's upstream default of ``./storage`` so the
    install survives ``cwd`` changes and is reachable from the unit's
    ``WorkingDirectory``-independent ExecStart.
    """

    @classmethod
    def get_storage_dir(cls) -> Path:
        return get_real_user_home() / '.local' / 'share' / 'meshchatx'

    @classmethod
    def get_identity_path(cls) -> Path:
        return cls.get_storage_dir() / 'identity'

    @classmethod
    def get_log_path(cls) -> Path:
        return cls.get_storage_dir() / 'meshchatx.log'

    @classmethod
    def get_rns_client_configdir(cls) -> Path:
        """Client-only Reticulum configdir for MeshChatX.

        Mirrors the gateway's ``/tmp/meshforge_rns_client/`` pattern so
        MeshChatX attaches to rnsd's shared instance instead of binding
        its own port. The pinned ``rpc_key`` (Issue #41) is propagated
        into this config by the installer.
        """
        return Path('/tmp/meshforge_meshchatx_rns_client')


class MeshForgePaths:
    """Paths related to MeshForge application"""

    @classmethod
    def get_config_dir(cls) -> Path:
        """Get MeshForge config directory"""
        return get_real_user_home() / '.config' / 'meshforge'

    @classmethod
    def get_data_dir(cls) -> Path:
        """Get MeshForge data directory"""
        return get_real_user_home() / '.local' / 'share' / 'meshforge'

    @classmethod
    def get_cache_dir(cls) -> Path:
        """Get MeshForge cache directory"""
        return get_real_user_home() / '.cache' / 'meshforge'

    @classmethod
    def get_plugins_dir(cls) -> Path:
        """Get user plugins directory"""
        return cls.get_config_dir() / 'plugins'

    @classmethod
    def ensure_user_dirs(cls) -> None:
        """Create user directories if they don't exist.

        When running under sudo, chown created dirs to the real user
        so they remain accessible without sudo later.
        """
        dirs = [
            cls.get_config_dir(),
            cls.get_data_dir(),
            cls.get_cache_dir(),
            cls.get_plugins_dir(),
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # Fix ownership if running under sudo
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            try:
                import pwd
                pw = pwd.getpwnam(sudo_user)
                uid, gid = pw.pw_uid, pw.pw_gid
                for d in dirs:
                    # Only chown if currently root-owned
                    if d.stat().st_uid == 0:
                        os.chown(str(d), uid, gid)
            except (KeyError, OSError):
                pass  # Non-critical: dirs still usable by root


class SystemPaths:
    """System-level paths"""

    # Boot configuration
    BOOT_CONFIG = Path('/boot/firmware/config.txt')
    BOOT_CONFIG_LEGACY = Path('/boot/config.txt')

    # Device paths
    SERIAL_DEVICES = Path('/dev')
    THERMAL_ZONE = Path('/sys/class/thermal/thermal_zone0/temp')

    # System files
    PROC_STAT = Path('/proc/stat')
    PROC_UPTIME = Path('/proc/uptime')
    PROC_MEMINFO = Path('/proc/meminfo')

    @classmethod
    def get_boot_config(cls) -> Path:
        """Get the appropriate boot config path"""
        if cls.BOOT_CONFIG.exists():
            return cls.BOOT_CONFIG
        return cls.BOOT_CONFIG_LEGACY

    @classmethod
    def get_serial_ports(cls) -> list:
        """Get list of serial port paths"""
        ports = []
        for pattern in ['ttyUSB*', 'ttyACM*', 'ttyAMA*']:
            ports.extend(cls.SERIAL_DEVICES.glob(pattern))
        return sorted(ports)


# ============================================================================
# Atomic file operations
# ============================================================================

def atomic_write_text(path: Path, content: str) -> None:
    """Write text to a file atomically using temp-file-then-rename.

    On POSIX systems, os.replace() is atomic, so either the old file
    remains intact or the new content is fully written. No partial writes.

    Uses a unique temp file (via tempfile) to avoid collisions between
    concurrent writers targeting the same path.

    Args:
        path: Target file path.
        content: Text content to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_path = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f'.{path.name}.',
            suffix='.tmp'
        )
        tmp_path = Path(tmp_name)
        os.write(fd, content.encode('utf-8'))
        os.fsync(fd)
        os.close(fd)
        fd = None
        tmp_path.replace(path)  # Atomic on POSIX
    except Exception:
        if fd is not None:
            os.close(fd)
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
