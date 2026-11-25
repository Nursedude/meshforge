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
import stat
import sys
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


def root_owned_chain(path, home, lstat=None) -> list:
    """Every ROOT-OWNED real directory (or the path itself) from ``path`` up
    to — NOT including — ``home``, nearest first.

    Why the chain and not the leaf (gate-5 stranger drill 45652768967): a
    fresh account has no ``~/.config``; root made it on the way to a deeper
    path, the leaf was handed back, and the operator could never create
    ``~/.config/meshforge`` ("Permission denied"). The home itself, anything
    outside it and a ``..`` path are never in the chain; a symlinked home is
    resolved once (r3) and a home that is — or resolves to — ``/``, or is
    relative, yields no chain at all (r2). The walk runs
    top-down from just below ``home`` and STOPS at the first symlink:
    everything deeper resolves through the link, possibly out of the home
    (review r1). A component that cannot be stat'ed is skipped. A relative
    path is made absolute against the cwd (no symlink resolution). Pure: no
    chown here.
    """
    lstat = lstat or os.lstat
    p, home = Path(os.path.abspath(path)) if not Path(path).is_absolute() else Path(path), Path(home)
    if ".." in p.parts or ".." in home.parts:
        return []
    try:
        rel = p.relative_to(home)
    except ValueError:
        return []
    # A symlinked home (/home/ham -> /mnt/ssd/ham) is RESOLVED once and the
    # walk runs on the real tree (Fable r3: refusing it dropped a working
    # install); every check below applies to the REAL home. Same rule as
    # mf_mkdir_for_user.
    try:
        if stat.S_ISLNK(lstat(home).st_mode):
            home = Path(os.path.realpath(home))
    except OSError:
        return []
    # A home of "/" (a service account's passwd entry, or a link to it) makes
    # EVERY path "under" it — the walk would hand /etc to that account (r2).
    if not home.is_absolute() or len(home.parts) < 2:
        return []
    out, cur = [], home
    for part in rel.parts:       # top-down from just below the (real) home
        cur = cur / part
        try:
            st = lstat(cur)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode):
            break
        if st.st_uid == 0:
            out.append(cur)
    return out[::-1]


def chown_to_operator(*paths) -> None:
    """Hand root-created config artifacts back to the real operator.

    Under sudo a file written by root in the operator's home stays
    root-owned, which breaks every later user-mode reader/writer (MF001
    class). Any writer that persists to ``get_real_user_home()`` while
    possibly running as root must call this — ONE implementation instead of
    drifting copies. Hands back each path AND every root-owned directory
    above it up to the operator's home (``root_owned_chain``): a parent root
    made with ``parents=True`` (``~/.config``, ``~/.local``) is as
    unwritable as the leaf. The path itself keeps the old rule (chowned if
    root-owned, wherever it is); the chain above it is inside the home only. Best-effort: a chown failure leaves the file
    root-usable, never raises."""
    sudo_user = os.environ.get('SUDO_USER', '')
    if not sudo_user or sudo_user == 'root' or '/' in sudo_user \
            or '..' in sudo_user:
        return
    try:
        import pwd
        pw = pwd.getpwnam(sudo_user)
    except (KeyError, OSError):
        return
    for p in paths:
        p = Path(p)
        try:
            # the path itself: the rule this function always had (any
            # root-owned path it is handed — /etc callers rely on it, r1)
            if p.exists() and p.stat().st_uid == 0:
                os.chown(str(p), pw.pw_uid, pw.pw_gid)
            # NEW: the root-made directories above it, inside the home only
            for d in root_owned_chain(p.parent, pw.pw_dir):
                # never through a link swapped in after the lstat (r3)
                os.chown(str(d), pw.pw_uid, pw.pw_gid, follow_symlinks=False)
        except OSError:
            pass  # non-critical: still root-usable


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


class PathUnjudged(OSError):
    """We cannot say what ANOTHER account sees at a path (r13.8, Fable r19
    #1): the observer cannot stat a component, or an ACL decides. An OSError
    so existing catchers degrade to UNKNOWN; never the observer's answer."""


# Indirection so tests can present modes/owners/ACLs without root.
_stat = os.stat
_getxattr = getattr(os, "getxattr", None)
_ACL_XATTR = "system.posix_acl_access"


def _may_search(st, uid: int, gids: frozenset) -> bool:
    """POSIX DAC search (x) on one dir: owner bits, else group, else other.
    uid 0 searches anything (CAP_DAC_READ_SEARCH)."""
    if uid == 0:
        return True
    if st.st_uid == uid:
        return bool(st.st_mode & 0o100)
    if st.st_gid in gids:
        return bool(st.st_mode & 0o010)
    return bool(st.st_mode & 0o001)


def _has_acl(path: Path) -> bool:
    import errno
    if _getxattr is None:
        raise PathUnjudged(f"cannot read ACLs on {path} on this platform")
    try:
        _getxattr(str(path), _ACL_XATTR)
    except OSError as e:
        if e.errno in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
            return False
        raise PathUnjudged(f"cannot read the ACL on {path} ({e})") from None
    return True


_MAXSYMLINKS = 40   # Linux's own hop limit → ELOOP


def _dirs_walked(path: Path):
    """The directories the kernel searches resolving ``path``, walked as the
    kernel does (r13.8.1, Fable r20 #2): component by component, every
    symlink (incl. the last) expanded hop by hop via ``readlink`` with its
    ``.``/``..`` taken PHYSICALLY from where it lands — no realpath, no
    lexical normalisation. Returns the searched dirs in order, or None when
    resolution cannot finish for ANY uid (a missing component, a non-dir
    mid-path, > 40 hops). An lstat/readlink the OBSERVER is refused raises
    ``PathUnjudged``."""
    import stat as stat_mod
    from collections import deque
    raw = str(path)
    if not raw.startswith('/'):
        raw = os.getcwd() + '/' + raw
    comps = deque(raw.split('/'))
    cur, searched, hops = '/', [], 0
    while comps:
        name = comps.popleft()
        if name == '':
            continue
        searched.append(cur)            # every lookup needs search on cur
        if name == '.':
            continue
        if name == '..':
            cur = os.path.dirname(cur) or '/'
            continue
        nxt = cur.rstrip('/') + '/' + name
        try:
            st = os.lstat(nxt)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as e:
            raise PathUnjudged(f"cannot lstat {nxt} ({e})") from None
        if stat_mod.S_ISLNK(st.st_mode):
            hops += 1
            if hops > _MAXSYMLINKS:
                return None
            try:
                target = os.readlink(nxt)
            except OSError as e:
                raise PathUnjudged(f"cannot readlink {nxt} ({e})") from None
            if target.startswith('/'):
                cur = '/'
            comps.extendleft(reversed(target.split('/')))
            continue
        if comps and any(c for c in comps) and not stat_mod.S_ISDIR(st.st_mode):
            return None                 # ENOTDIR mid-path
        cur = nxt
    return list(dict.fromkeys(searched))


def isfile_as(path: Path, uid: int, gids) -> bool:
    """``os.path.isfile(path)`` as the account (uid, groups) would get it —
    RNS asks it as rnsd's uid; the observer may be another uid (the root
    watchdog, r13.8). stat needs search on the dirs only, so file bits never
    matter; a dir the account cannot search → False, as EACCES is for RNS.

    Absence (ENOENT/ENOTDIR) is the same for every uid → False. Raises
    ``PathUnjudged`` when the observer cannot see what the account sees:
    it cannot stat a component (and is not that very account), or an ACL
    sits on a dir in the walk."""
    import stat as stat_mod
    gids = frozenset(gids)
    same = uid == os.geteuid() and gids == frozenset(os.getgroups()) | {os.getegid()}
    import errno
    try:
        st = _stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except PermissionError:
        if same:
            return False   # the kernel answered for these very creds
        raise PathUnjudged(f"cannot stat {path} to judge it as uid {uid}") from None
    except OSError as e:
        if e.errno == errno.ELOOP:
            return False   # a symlink loop is False for every uid (r20 #8)
        raise PathUnjudged(f"cannot stat {path} ({e})") from None
    if not stat_mod.S_ISREG(st.st_mode):
        return False
    if same:
        return True        # reached with the account's own creds
    walked = _dirs_walked(path)
    if walked is None:
        return False       # unresolvable for any uid
    for d in walked:
        try:
            dst = _stat(d)
        except OSError as e:
            raise PathUnjudged(f"cannot stat {d} ({e})") from None
        if uid != 0 and _has_acl(d):
            raise PathUnjudged(f"{d} carries an ACL: uid {uid}'s search right "
                               f"is the kernel's call, not the mode bits'")
        if not _may_search(dst, uid, gids):
            return False
    return True


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
            # A symlinked storage/ is refused BEFORE any mkdir (fourth read,
            # F4): as root these mkdirs followed it and created ratchets/,
            # cache/ ... inside the link's target. The walk refuses it too.
            if cls.ETC_STORAGE.is_symlink():
                import logging
                logging.getLogger(__name__).warning(
                    "RNS storage %s is a symlink — not creating directories "
                    "through it. Replace it with a real directory owned by "
                    "rnsd's user.", cls.ETC_STORAGE)
                return False
            # Save and clear umask so mkdir gets the actual mode we request.
            # Default umask 0o022 would turn 0o777 into 0o755.
            old_umask = os.umask(0)
            try:
                cls.ETC_BASE.mkdir(mode=0o755, parents=True, exist_ok=True)
                # Born 0755, NOT world-writable (B2, 2026-10-15): the walk
                # below hands the tree to rnsd's own user, so a writer outside
                # that user is not needed and a world-writable dir lets any
                # local user rename rnsd's key away.
                cls.ETC_STORAGE.mkdir(mode=0o755, parents=True, exist_ok=True)
                cls.ETC_RATCHETS.mkdir(mode=0o755, parents=True, exist_ok=True)
                cls.ETC_RESOURCES.mkdir(mode=0o755, parents=True, exist_ok=True)
                cls.ETC_CACHE.mkdir(mode=0o755, parents=True, exist_ok=True)
                cls.ETC_ANNOUNCE_CACHE.mkdir(mode=0o755, parents=True, exist_ok=True)
                cls.ETC_DISCOVERY.mkdir(mode=0o755, parents=True, exist_ok=True)
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
    def _rnsd_storage_owner(cls):
        """(uid, gid) rnsd runs as, read from its OWN key — or None.

        rnsd creates ``storage/transport_identity`` itself, so the key's owner
        IS rnsd's user (measured 2026-10-15: equal to the unit's ``User=`` on
        all 9 rnsd boxes). None when there is no key yet (fresh box), the
        owner is root (rnsd as root: nothing measured says who else writes),
        or the "key" is not a regular file — a symlink there names whoever
        planted it, not rnsd (``lstat``, never ``stat``: W2, 2026-10-15).
        No subprocess, no systemctl: the file is the witness.
        """
        import stat
        try:
            st = (cls.ETC_STORAGE / "transport_identity").lstat()
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode) or st.st_uid == 0:
            return None
        return (st.st_uid, st.st_gid)

    @classmethod
    def _fix_storage_file_permissions(cls):
        """Keep /etc/reticulum/storage/ usable by rnsd WITHOUT world-write.

        B2 closed by OWNERSHIP (operator "go", 2026-10-15). Measured on all 9
        rnsd boxes: rnsd and every non-root RNS consumer (nomadnet, lxmd, the
        gateway, map services, mini, the MeshAnchor daemon) run as ONE user,
        and that user owns rnsd's key. So when that owner is known
        (``_rnsd_storage_owner``) and is not root:
          * as root, every entry not owned by it is chowned to it — root-made
            files (sudo TUI, a root lxmd) can no longer lock rnsd out, the
            failure the old 0666/0777 widening existed to prevent;
          * dirs are set 0755, files lose group/other WRITE, and a key-shaped
            file (identity-named, or exactly 64 bytes — an RNS private
            identity's size) is set 0600.
        Not root: no chown; modes are narrowed only on entries this process
        owns.

        Owner unknown: the LEGACY shared-write fallback (dirs 0777, files
        0666, never widening a key) runs ONLY when the key is ABSENT — a fresh
        box where rnsd must still create it. A key that exists but names no
        usable owner (root-owned after one ``sudo cp``, or not a regular file)
        changes NOTHING but narrowing (W3, 2026-10-15). A sticky root was
        tried and reverted (rnsd's tmp + os.replace of known_destinations
        fails under it once that file is another uid's).

        **Symlink-safe** (non-author read 2026-10-15, W1/W2/W6). This runs as
        ROOT on every sudo TUI start over a tree rnsd's user owns, so that user
        can plant or swap links in it; a check-then-chmod followed a swapped
        link 671/3000 times (measured) and a swapped PARENT dir redirected
        ``cache/<name>`` outside. So: a symlinked storage root is refused with
        a warning; the tree is walked with ``os.fwalk`` (directory fds, no
        path re-resolution); each file is opened relative to its dir with
        ``O_NOFOLLOW|O_NONBLOCK`` and must be a regular file; every change is
        ``fchmod``/``fchown`` on that fd; a multi-link file is never touched at
        all — neither owner nor mode (W7: a hardlink is another name for an
        OUTSIDE inode, and ``fs.protected_hardlinks`` still lets rnsd's user
        link any file it holds r+w on). The config-dir tail gets the same
        treatment — /etc/reticulum is group-writable, so a link planted as
        ``identity`` would otherwise be chmod-ed through.

        **A root-owned file that is not world-readable is left exactly as it
        is** (W8, operator's call 2026-10-15): rnsd's user can MOVE such a
        file into the tree (root 0600 fleet backups sit in its home), and
        handing it over — or widening it in the legacy branch — would give
        away a root secret. The walk cannot tell "root made it here" from
        "moved in", so it refuses both and names each in one WARNING; a
        root-made 0600 file rnsd needed would then show on the record, not
        outside the app. Root-made SHARED files (0644 caches, the lock-out
        the ownership rule exists for) are still handed over. A subtree the
        walk cannot open is named too (W9), never skipped silently.

        Runs on every gateway connect and sudo TUI start, so it must agree
        with the hand roll — the old walk put 0777/0666 straight back.
        """
        import stat

        summary = {"left_alone": [], "unopened": [], "refused": None}
        storage_root = cls.ETC_STORAGE
        try:
            root_st = storage_root.lstat()
        except OSError:
            return summary
        if stat.S_ISLNK(root_st.st_mode):
            import logging
            logging.getLogger(__name__).warning(
                "RNS storage %s is a symlink — permission walk REFUSED (it "
                "runs as root; a link would redirect it outside the tree). "
                "Replace it with a real directory owned by rnsd's user.",
                storage_root)
            summary["refused"] = f"{storage_root} is a symlink"
            return summary
        if not stat.S_ISDIR(root_st.st_mode):
            return summary
        owner = cls._rnsd_storage_owner()
        try:
            (storage_root / "transport_identity").lstat()
            key_absent = False
        except FileNotFoundError:
            key_absent = True
        except OSError:
            key_absent = False
        legacy = owner is None and key_absent
        as_root = os.geteuid() == 0
        shed_write = stat.S_IWGRP | stat.S_IWOTH
        nofollow = (os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    | getattr(os, "O_CLOEXEC", 0))

        def hand_over(fd: int, st) -> None:
            if owner is not None and as_root and (st.st_uid, st.st_gid) != owner:
                os.fchown(fd, owner[0], owner[1])

        def root_private(st) -> bool:
            return st.st_uid == 0 and not (st.st_mode & stat.S_IROTH)

        left_alone = summary["left_alone"]   # root-private, never handed over
        unopened = summary["unopened"]       # subtrees left unchecked
        dir_probe = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                     | getattr(os, "O_CLOEXEC", 0))
        here = [str(storage_root)]           # the dir being processed

        def _unopened(err: OSError) -> None:
            # Only a race reaches this (each subdir is probed first, below);
            # fwalk gives a bare name, so the best context is the last dir.
            unopened.append(f"{here[0]}/…/{getattr(err, 'filename', '?')} "
                            f"({err.strerror or err})")

        def dir_mode(current: int) -> int:
            if owner is not None:
                return 0o755
            return 0o777 if legacy else current & ~shed_write

        def file_mode(fname: str, st) -> int:
            current = stat.S_IMODE(st.st_mode)
            if "identity" in fname.lower() or st.st_size == 64:
                return 0o600 if owner is not None else current & ~shed_write
            if legacy:
                return current | 0o666
            return current & ~shed_write

        try:
            for dp, dirs, filenames, dfd in os.fwalk(
                    str(storage_root), follow_symlinks=False,
                    onerror=_unopened):
                here[0] = dp
                try:
                    dst = os.fstat(dfd)
                except OSError:
                    continue
                if root_private(dst):
                    # Left exactly as it is, SUBTREE included: a root 0700
                    # dir of root files is one root-made thing (third read).
                    left_alone.append(dp)
                    dirs[:] = []
                    continue
                try:
                    hand_over(dfd, dst)
                    want = dir_mode(stat.S_IMODE(dst.st_mode))
                    if stat.S_IMODE(dst.st_mode) != want:
                        os.fchmod(dfd, want)
                except OSError:
                    pass
                # Probe each subdir HERE, where its full path is known: a
                # failure (EACCES, or EMFILE at the fd limit — the one root can
                # hit) is named by path and pruned, instead of killing fwalk's
                # generator outside its onerror (third non-author read).
                keep = []
                for name in dirs:
                    try:
                        os.close(os.open(name, dir_probe, dir_fd=dfd))
                        keep.append(name)
                    except OSError as e:
                        unopened.append(f"{os.path.join(dp, name)} "
                                        f"({e.strerror or e})")
                dirs[:] = keep

                for fname in filenames:
                    try:
                        fd = os.open(fname, nofollow, dir_fd=dfd)
                    except OSError:
                        continue    # a link (ELOOP), gone, or unreadable
                    try:
                        st = os.fstat(fd)
                        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                            continue
                        if root_private(st):
                            left_alone.append(os.path.join(dp, fname))
                            continue
                        hand_over(fd, st)
                        want = file_mode(fname, st)
                        if want != stat.S_IMODE(st.st_mode):
                            os.fchmod(fd, want)
                    except OSError:
                        pass        # Best effort — some files may be locked
                    finally:
                        os.close(fd)
        except OSError as e:
            unopened.append(f"{here[0]} (walk stopped: {e.strerror or e})")

        if left_alone or unopened:
            import logging
            log = logging.getLogger(__name__)
            if left_alone:
                log.warning(
                    "RNS storage walk left %d root-owned private entr(y/ies) as "
                    "they are (never handed to rnsd's user): %s%s — if rnsd "
                    "needs one, fix its owner by hand.", len(left_alone),
                    ", ".join(left_alone[:3]),
                    " …" if len(left_alone) > 3 else "")
            if unopened:
                log.warning(
                    "RNS storage walk could not open %d subtree(s), left "
                    "unchecked: %s%s", len(unopened), ", ".join(unopened[:3]),
                    " …" if len(unopened) > 3 else "")

        # Also fix files in the config directory itself (identity, config).
        # NomadNet and other RNS clients need to read the identity file to
        # authenticate with rnsd's shared instance.  If the identity was
        # created by root (via sudo MeshForge), non-root users can't read
        # it and RNS generates a different identity → auth mismatch.
        # No-follow (W6): this dir is group-writable, so a planted link here
        # would make its target world-readable.
        for fname in ('identity', 'config'):
            try:
                fd = os.open(str(cls.ETC_BASE / fname), nofollow)
            except OSError:
                continue
            try:
                st = os.fstat(fd)
                if (stat.S_ISREG(st.st_mode) and st.st_nlink == 1
                        and not (st.st_mode & stat.S_IROTH)):
                    os.fchmod(fd, stat.S_IMODE(st.st_mode)
                              | stat.S_IROTH | stat.S_IRGRP)
            except OSError:
                pass
            finally:
                os.close(fd)
        return summary

    @staticmethod
    def storage_walk_note(summary) -> Optional[str]:
        """One operator-facing line for a walk summary, or None when clean.

        The TUI logs to a FILE only, so the walk's WARNING never reaches the
        screen; every caller prints this instead (third non-author read)."""
        if not summary:
            return None
        parts = []
        if summary.get("refused"):
            parts.append(f"REFUSED — {summary['refused']}")
        for key, label in (("left_alone", "root-owned private entr(y/ies) "
                            "left as they are"),
                           ("unopened", "subtree(s) not checked")):
            items = summary.get(key) or []
            if items:
                more = " …" if len(items) > 3 else ""
                parts.append(f"{len(items)} {label}: "
                             f"{', '.join(items[:3])}{more}")
        return "; ".join(parts) or None

    @classmethod
    def get_config_dir(cls) -> Path:
        """Get Reticulum config directory.

        Checks locations in the same order as RNS.Reticulum.__init__:
          1. MESHFORGE_RNS_CONFIGDIR env override (sandbox/virtual-fleet)
          2. /etc/reticulum/ (system-wide)
          3. ~/.config/reticulum/ (XDG-style)
          4. ~/.reticulum/ (traditional, default)

        The env override exists for lab.virtual_fleet: RNS is a per-process
        singleton, so whichever component inits first pins the instance for
        everyone (the 2026-06-27 resourcepath race, rediscovered 2026-08-27
        when a sandboxed gateway's node_tracker attached to the BOX's real
        instance before the bridge's explicit configdir was consulted). One
        process-wide resolution root is the only coherent lever. Unset =
        production behavior, unchanged.
        """
        override = os.environ.get('MESHFORGE_RNS_CONFIGDIR')
        if override:
            return Path(override)
        return cls.rnsd_config_dir_for_home(get_real_user_home())

    @classmethod
    def rnsd_config_dir_for_home(cls, home: Path, *, as_ids=None) -> Path:
        """The configdir rnsd started as the user whose home is ``home``
        loads — RNS ``Reticulum.__init__``'s own order: /etc/reticulum,
        ``home``/.config/reticulum, ``home``/.reticulum — the first whose
        ``config`` is ``os.path.isfile`` AS RNSD'S UID. File bits never
        matter; a dir rnsd cannot search makes it False (Fable r19 #1).

        ``as_ids`` = rnsd's ``(uid, gids)``: judged as that account by
        ``isfile_as``, which raises ``PathUnjudged`` (an OSError) when the
        observer cannot see what rnsd sees. None = the observer's own view
        (right when the observer IS rnsd's user, or asks for its own config).

        The ONE answer (r13.6, Fable r17 #1): no env override (rnsd ignores
        ``MESHFORGE_RNS_CONFIGDIR``) and an EXPLICIT home (no SUDO_USER /
        LOGNAME inference — r17 NIT 3). ``get_config_dir`` delegates here.
        """
        etc = cls.ETC_BASE
        xdg_dir = Path(home) / '.config' / 'reticulum'
        if as_ids is not None:
            uid, gids = as_ids
            for d in (etc, xdg_dir):
                if isfile_as(d / 'config', uid, gids):
                    return d
            return Path(home) / '.reticulum'
        if etc.is_dir() and (etc / 'config').is_file():
            return etc
        if xdg_dir.is_dir() and (xdg_dir / 'config').is_file():
            return xdg_dir
        return Path(home) / '.reticulum'

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
        # rnsd's lines (bytes, BOM stripped, split on \n only) — read_text()
        # raised UnicodeDecodeError through every caller on non-UTF-8 (r11).
        # Lazy: importing the reader at module load would import RNS.
        # r13.1: the key rnsd USES — [reticulum] rpc_key as rnsd's own parser
        # reads it (rns_config_reader.rnsd_rpc_key). The old line scan took
        # the first `rpc_key =` anywhere (a top-level / [logging] line rnsd
        # ignores) and missed `<hex>  # c`, quoted keys (Fable r13 #1).
        from utils.rns_config_reader import RNSConfigError, RNSConfigUnjudged, rnsd_rpc_key
        cfg = cls.get_config_file()
        try:
            raw_bytes = cfg.read_bytes()
        except FileNotFoundError:
            raw_bytes = b""          # no config: rnsd runs on defaults (derives)
        except OSError:
            # UNREADABLE is not ABSENT: rnsd may pin a key we cannot see —
            # deriving would hand clients a key rnsd does not use (Fable r14 #2).
            return None
        try:
            state, value = rnsd_rpc_key(raw_bytes)
        except RNSConfigUnjudged as e:
            # THIS python cannot judge the file (no RNS importable) — rnsd may
            # pin a key here; never derive over it. Not "cannot load": said
            # as UNKNOWN in the log so the None has a witness (r17 NIT 1).
            import logging
            logging.getLogger(__name__).warning(
                "rpc_key UNKNOWN — %s cannot judge %s (%s); install RNS for "
                "this python", sys.executable, cfg, e)
            return None
        except RNSConfigError:
            # rnsd cannot load its config: it runs with no key we could
            # name — never derive one for clients over that.
            return None
        if state == "valid":
            return value
        if state == "invalid":
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
        from utils.rns_config_reader import config_lines   # lazy (see above)
        try:
            raw_bytes = cls.get_config_file().read_bytes()
        except (OSError, PermissionError):
            return 'default'
        for raw in config_lines(raw_bytes):
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
        import stat
        import tempfile

        # Sandbox override (lab.virtual_fleet): the override dir IS a valid
        # shared-instance client configdir (rnsd + clients sharing one
        # configdir is the fleet's own pattern), and using it directly keeps
        # a sandbox gateway from writing into the FIXED /tmp path a real
        # gateway on the same box may own (honest_failure_modes #8).
        override = os.environ.get('MESHFORGE_RNS_CONFIGDIR')
        if override:
            return override

        d = Path(tempfile.gettempdir()) / cls.RNS_CLIENT_DIRNAME
        # The config holds rnsd's rpc_key (Issue #41) — a shared-instance
        # management credential. This dir has a FIXED, world-predictable
        # name in a possibly-shared /tmp (the gateway unit sets PrivateTmp,
        # but map/TUI/manual callers reach this helper WITHOUT it). Own the
        # dir 0700 and REFUSE a pre-existing dir owned by someone else —
        # otherwise a local user pre-creates it (or a 'config' symlink) and
        # either harvests the key or redirects our write (a /tmp symlink
        # attack; write_text follows links).
        d.mkdir(mode=0o700, exist_ok=True)
        st = d.lstat()
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
            raise RuntimeError(
                f"RNS client configdir {d} is not a real directory "
                f"(symlink or file) — refusing to use it")
        if st.st_uid != os.getuid():
            raise RuntimeError(
                f"RNS client configdir {d} is owned by uid {st.st_uid}, "
                f"not this process ({os.getuid()}) — refusing to write "
                f"rnsd's rpc_key into a foreign-owned dir")
        # Re-assert 0700 in case an old run created it world-readable.
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
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
        cfg = d / "config"
        # Refuse to write THROUGH a symlink (the classic /tmp redirect);
        # then atomic write at 0600 so the credential is never world-
        # readable and never torn by a concurrent writer.
        if cfg.is_symlink():
            raise RuntimeError(
                f"{cfg} is a symlink — refusing to write rnsd's rpc_key "
                f"through it")
        atomic_write_text(cfg, "\n".join(lines) + "\n")
        try:
            os.chmod(cfg, 0o600)
        except OSError:
            pass
        return str(d)


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
    def rns_nodes_cache_path(cls) -> Path:
        """RNS nodes position cache — written by gateway.node_tracker, read by
        the RNS + tracker map collectors. SINGLE source of this path (writer and
        readers import it here so they can't drift — honest_failure_modes #5).
        Under ~/.cache/meshforge (operator-owned) instead of the world-writable
        /tmp so another local user can't pre-create it with hostile node data.
        (QA deferred low/perf, 2026-07-06.)"""
        return cls.get_cache_dir() / 'rns_nodes.json'

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

        # Under sudo: hand back each dir AND the root-made parents above it
        # (~/.config, ~/.local, ~/.local/share) — the one helper.
        chown_to_operator(*dirs)


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


def atomic_write_text_preserving(path: Path, content: str) -> None:
    """``atomic_write_text`` that KEEPS the existing file's owner and mode.

    ``atomic_write_text``'s temp file is 0600 and owned by the WRITER; as
    root (the TUI runs under sudo) the renamed file would lock out a service
    that reads it as the operator. A NEW file is handed to the operator.
    """
    if path.is_symlink():
        # os.replace() would swap the LINK itself for a regular file and
        # orphan its target (review S6, 2026-09-28) while stat() below
        # follows the link. Write through to what the link points at.
        path = path.resolve()
    st = path.stat() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.',
                               suffix='.tmp')
    try:
        os.write(fd, content.encode('utf-8'))
        os.fsync(fd)
        if st is not None:
            # Skip a call whose target the temp file ALREADY has: on vfat
            # (/boot/firmware) owner and mode come from mount options, a new
            # file already matches, and fchmod/fchown there can raise.
            cur = os.fstat(fd)
            if (cur.st_mode & 0o7777) != (st.st_mode & 0o7777):
                os.fchmod(fd, st.st_mode & 0o7777)
            if os.geteuid() == 0 and (cur.st_uid, cur.st_gid) != (st.st_uid, st.st_gid):
                os.fchown(fd, st.st_uid, st.st_gid)
        os.close(fd)
        fd = None
        os.replace(tmp, path)
    except Exception:
        if fd is not None:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    if st is None:
        chown_to_operator(path)
