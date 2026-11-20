"""Claude-session scratch held in RAM — measure ALL of it, name the idle part.

Why this exists (2026-11-19): ``/tmp`` is tmpfs on the fleet Pis, so every byte
a Claude session leaves in its scratchpad (``/tmp/claude-<uid>/<project>/<sid>``)
is UNRECLAIMABLE shmem charged to ``user-<uid>.slice``. A finished review
session left 2.07 GB of git-archive tree exports and mutant copies there; it
was the standing floor under six ``memory_cap_engaged`` fires in one day. The
only defence was a memory file ("delete review exports when the round ends"),
which did not hold. This module is the surface that names the leak instead.

Who can read it: the user that owns ``/tmp/claude-<uid>`` (mode 0700). The
watchdog CANNOT (``PrivateTmp=yes``: it can statvfs the shared tmpfs total but
not list this tree), so the warm-start brief, running as the user, consumes it.

TWO SEAMS (Fable review rounds 1–4, 2026-11-19 — each round found a new case of
the same two defect kinds, so the cases were replaced by invariants):

1. **Total-first accounting — nothing held can read as clean.** Every byte
   under the root lands in exactly ONE bucket, and the header is the MEASURED
   total, so a dir that is sub-floor, future-stamped, held by a process,
   undatable or unreached is still in the number and in a named bucket:

   ============  ==========================================================
   bucket        rule (first match wins, per ``<root>/<project>/<sid>``)
   ============  ==========================================================
   unscanned     walk budget spent before/while reaching it, or ANY part of
                 it unreadable — checked FIRST, before liveness (a partial
                 view measures a lower bound and always prints)
   in_use        the CALLING session (hook stdin id) — its own working set
   held          another process of this uid has a cwd/fd at/under it or at
                 its project dir (a live session — or a STRAY background task
                 left by a dead one: never assumed to be either)
   undatable     newest stamp in the FUTURE (clock stepped back / extracted
                 future mtimes)
   recent        idle < ``idle_s`` (transcript or tree written recently)
   idle          everything else — listed by name, largest first
   loose         regular files directly under the root or a project dir
   ============  ==========================================================

   The block prints when everything except the caller's own ``in_use`` and
   ``recent`` scratch reaches ``min_bytes``, or any dir is unscanned. ``held``
   COUNTS (Fable r5: an orphaned ``sleep`` holding a dead session's
   ``tasks/x.output`` silenced 300 MB); ``in_use``/``recent`` are reported
   alongside, never alone. A sub-floor idle dir is still in ``idle``'s total.
   Hard links are counted once per SCAN (one shared inode set).

2. **Measure, never prescribe; echo nothing untrusted.** No destructive
   command is ever printed (liveness is not provable from outside a session);
   only measurements and ``du``/``ls`` inspection commands. The output rides
   into a model's context, so the only paths echoed are the root — when it is
   the literal ``/tmp/claude-<uid>`` or a plain path — joined with name
   components that pass ``_PLAIN`` (≤ 80 chars of ``[A-Za-z0-9._-]``); anything
   else is "(unusual name, not echoed)". The root itself must be a real
   directory (not a symlink) owned by the uid, else UNKNOWN without echoing it.

RAM is MEASURED, not assumed: the root's mount fstype is read from
``/proc/self/mounts`` (longest-prefix match, last wins on an over-mount —
the same rule as the watchdog's ``_tmpfs_used_mb``). Not tmpfs → silent
(disk scratch is not this module's concern); unreadable → the block says so.
Not claimed (documented blind spots, not silent ones):
  * that pages are resident (swap may hold them) or which cgroup is capped —
    pages charge the cgroup that WROTE them;
  * deleted-but-still-open files: tmpfs RAM no directory walk can see (the
    holder's fd reads ``<path> (deleted)``) — the dir shows as ``held``;
  * WHICH dir a multi-link inode is credited to: once per scan, to the first
    dir in name order — the total is exact, the attribution is not;
  * ramfs: it keeps no ``st_blocks``, so it cannot be measured here; only
    tmpfs is treated as RAM (the same rule as the watchdog's note).

Tri-state, never collapsed: ``observable=False`` (could not look) vs looked.
Bounded per scan (it runs in a SessionStart hook; ~1.4 s for the full budget on
a Pi 5).
"""

from __future__ import annotations

import os
import re
import shlex
import stat as _stat
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

DEFAULT_IDLE_S = 2 * 3600          # a live session writes its transcript every turn
DEFAULT_MIN_BYTES = 100 << 20      # below this the RAM cost is noise on a 16 GB Pi
DEFAULT_TOTAL_ENTRIES = 400_000    # walk bound per SCAN (SessionStart latency)
FUTURE_SKEW_S = 300                # a stamp further ahead than this is "future"

RAM_FSTYPES = ("tmpfs",)   # ramfs: no st_blocks → unmeasurable (r6)
_PLAIN = re.compile(r"[A-Za-z0-9._-]{1,80}")
_PLAIN_PATH = re.compile(r"/[A-Za-z0-9._/-]{1,200}")
BUCKETS = ("idle", "held", "undatable", "recent", "in_use", "unscanned", "loose")


@dataclass(frozen=True)
class Dir:
    path: str
    project: str
    session_id: str
    size_bytes: int
    lower_bound: bool               # bound or unreadable subdir — true size LARGER
    idle_s: Optional[float]         # None for undatable / unscanned
    why: str = ""                   # undatable / unscanned reason


@dataclass
class ScratchScan:
    observable: bool
    reason: str = ""
    root_exists: bool = True
    root: str = ""
    uid: Optional[int] = None
    fstype: Optional[str] = None    # None = unreadable
    bytes: Dict[str, int] = field(default_factory=lambda: {b: 0 for b in BUCKETS})
    dirs: Dict[str, List[Dir]] = field(
        default_factory=lambda: {b: [] for b in BUCKETS if b != "loose"})
    proc_ok: bool = True
    idle_s: float = DEFAULT_IDLE_S
    procs_blind: int = 0            # same-uid processes with unreadable cwd/fds
    #   (systemd --user, sd-pam, ssh-agent are ALWAYS blind to their owner —
    #   a count, never a gate)

    @property
    def total(self) -> int:
        return sum(self.bytes.values())

    @property
    def not_in_use(self) -> int:
        return sum(v for k, v in self.bytes.items() if k not in ("in_use", "recent"))
    # NB: "held" COUNTS — a process holding a dir is not proof it is live.


def _real_identity() -> Tuple[int, str]:
    """(uid, home) from ONE source: the real operator (SUDO_USER-aware) home,
    and the uid that owns it — never euid paired with SUDO_USER's home."""
    from ._util import operator_home   # SUDO_USER-aware; same in both twins
    home = str(operator_home())
    try:
        uid = os.stat(home).st_uid
    except OSError:
        uid = os.getuid()
    return uid, home


def default_tmp_root(uid: int) -> str:
    return f"/tmp/claude-{uid}"


def _default_stamp(st: os.stat_result) -> float:
    """max(mtime, ctime): extraction/`cp -p` back-date mtime, never ctime."""
    return max(st.st_mtime, st.st_ctime)


def _fstype(path: str, mounts_path: str) -> Optional[str]:
    """fstype of the mount holding ``path`` (longest mountpoint prefix)."""
    try:
        with open(mounts_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except (OSError, ValueError):
        return None
    best, best_len = None, -1
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mp = parts[1].replace("\\040", " ")
        if (path == mp or path.startswith(mp.rstrip("/") + "/")) and len(mp) >= best_len:   # last wins (over-mounts)
            best, best_len = parts[2], len(mp)
    return best


def _live_handles(root: str, uid: int, proc: str) -> Tuple[Set[str], int, bool]:
    """(dirs a process of ``uid`` has a cwd/fd at or under, blind, proc_ok).

    Dirs are ``<root>/<project>`` or ``<root>/<project>/<sid>``; readlink
    targets are canonical, so ``root`` must be too."""
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return set(), 0, False
    prefix = root.rstrip("/") + "/"
    live: Set[str] = set()
    blind: Set[str] = set()

    def _note(target: str) -> None:
        if target.startswith(prefix):
            parts = [p for p in target[len(prefix):].split("/") if p]
            if parts:
                live.add(os.path.join(root, *parts[:2]))

    for pid in pids:
        base = os.path.join(proc, pid)
        try:
            if os.stat(base).st_uid != uid:
                continue
        except OSError:
            continue                    # exited between listdir and stat
        try:
            _note(os.readlink(os.path.join(base, "cwd")) + "/")
        except FileNotFoundError:
            pass
        except OSError:
            blind.add(pid)
        try:
            fds = os.listdir(os.path.join(base, "fd"))
        except FileNotFoundError:
            continue
        except OSError:
            blind.add(pid)
            continue
        for fd in fds:
            try:
                _note(os.readlink(os.path.join(base, "fd", fd)))
            except OSError:
                continue                # fd closed mid-walk
    return live, len(blind), True


def _entry(e: os.DirEntry, seen: Set[Tuple[int, int]],
           dev: int) -> Tuple[str, int, Optional[os.stat_result]]:
    """Classify ONE directory entry — the single rule both walkers share
    (Fable r7: _list and _walk_size had diverged, so one of them measured an
    unreadable entry as 0). Returns (kind, bytes, stat):

      kind "gone"   vanished between listing and stat (ENOENT) — holds nothing,
                    is NOT blindness (r7: churn in a live dir read as unreadable)
      kind "blind"  stat failed any other way, or a mount boundary (another
                    st_dev — its blocks are not this tmpfs; never descended)
      kind "dir"    a directory on the root's filesystem
      kind "data"   anything else (file, symlink, fifo, socket): its allocated
                    blocks, hard links counted once per scan
    """
    try:
        st = e.stat(follow_symlinks=False)
    except FileNotFoundError:
        return "gone", 0, None
    except OSError:
        return "blind", 0, None
    if st.st_dev != dev:
        return "blind", 0, st
    if _stat.S_ISDIR(st.st_mode):
        return "dir", 0, st
    if not _once(st, seen):
        return "data", 0, st
    blocks = getattr(st, "st_blocks", None)
    # st_blocks = what tmpfs actually holds (sparse-safe)
    return "data", (blocks * 512 if blocks is not None else st.st_size), st


def _walk_size(path: str, budget: int,
               stamp: Callable[[os.stat_result], float],
               seen: Set[Tuple[int, int]], dev: int) -> Tuple[int, int, float, bool, bool]:
    """(bytes, entries_used, newest_stamp, truncated, blind) — no symlink
    following, no mount crossing; hard links counted once per scan."""
    total = 0
    used = 0
    newest = 0.0
    blind = False
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            continue                    # vanished: holds nothing
        except OSError:
            blind = True
            continue
        newest = max(newest, stamp(st))
        try:
            with os.scandir(cur) as it:
                for e in it:
                    used += 1
                    if used > budget:
                        return total, used, newest, True, blind
                    kind, n, est = _entry(e, seen, dev)
                    if est is not None:
                        newest = max(newest, stamp(est))
                    if kind == "blind":
                        blind = True
                    elif kind == "dir":
                        stack.append(e.path)
                    total += n
        except FileNotFoundError:
            continue
        except OSError:
            blind = True
            continue
    return total, used, newest, False, blind


def _once(st: os.stat_result, seen: Set[Tuple[int, int]]) -> bool:
    """True the first time an inode is met in this SCAN (hard-link dedup)."""
    if st.st_nlink <= 1:
        return True
    key = (st.st_dev, st.st_ino)
    if key in seen:
        return False
    seen.add(key)
    return True


def _list(path: str, seen: Set[Tuple[int, int]],
          dev: int) -> Tuple[int, List[os.DirEntry], bool, bool]:
    """(bytes of non-dir entries directly in ``path``, its subdirs, listable,
    blind) — the same per-entry rule as the walk (``_entry``)."""
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name)
    except OSError:
        return 0, [], False, True
    loose = 0
    dirs = []
    blind = False
    for e in entries:
        kind, n, _st = _entry(e, seen, dev)
        if kind == "dir":
            dirs.append(e)
        elif kind == "blind":
            blind = True
        loose += n
    return loose, dirs, True, blind


def _safe_root(spelled: str, uid: int, default: str) -> Optional[str]:
    """The root as it may be ECHOED, or None (not echoed)."""
    if spelled == default or _PLAIN_PATH.fullmatch(spelled):
        return spelled
    return None


def scan_stale_session_scratch(
    *,
    tmp_root: Optional[str] = None,
    home: Optional[str] = None,
    uid: Optional[int] = None,
    now: float,
    idle_s: float = DEFAULT_IDLE_S,
    total_entries: int = DEFAULT_TOTAL_ENTRIES,
    proc_root: str = "/proc",
    mounts_path: str = "/proc/self/mounts",
    exclude: Iterable[str] = (),
    stamp: Optional[Callable[[os.stat_result], float]] = None,
    min_bytes: int = DEFAULT_MIN_BYTES,   # accepted for API stability; the
    #   floor is applied by the formatter to the not-in-use TOTAL, never per dir
    max_entries: Optional[int] = None,    # legacy alias of total_entries
) -> ScratchScan:
    """Measure everything under the root into BUCKETS (see module docstring).

    ``exclude`` = session ids never treated as idle (the caller's own).
    ``stamp`` exists for tests only (utime cannot back-date ctime)."""
    del min_bytes
    if max_entries is not None:
        total_entries = min(total_entries, max_entries)
    stamp = stamp or _default_stamp
    excluded = set(exclude)
    if home is None or uid is None:
        r_uid, r_home = _real_identity()
        uid = r_uid if uid is None else uid
        home = r_home if home is None else home
    default = default_tmp_root(uid)
    spelled = tmp_root or default
    echo = _safe_root(spelled, uid, default)
    try:
        lst = os.lstat(spelled)
    except FileNotFoundError:
        # No Claude session ever ran as this uid since boot: absent by design.
        return ScratchScan(observable=False, root_exists=False, uid=uid,
                           root=echo or "", reason="root does not exist")
    except OSError as exc:
        return ScratchScan(observable=False, uid=uid, root=echo or "",
                           reason=f"root unreadable ({type(exc).__name__})")
    if not _stat.S_ISDIR(lst.st_mode) or lst.st_uid != uid:
        # A symlink, a file, or someone else's dir at OUR path: never followed,
        # never echoed (r4 #1 — /tmp is world-writable and empty after boot).
        return ScratchScan(observable=False, uid=uid, root=echo or "",
                           reason=("root is not a real directory owned by uid "
                                   f"{uid} (symlink, file or foreign dir)"))
    root = os.path.realpath(spelled)
    scan = ScratchScan(observable=True, uid=uid, root=echo or "",
                       fstype=_fstype(root, mounts_path), idle_s=idle_s)
    if scan.fstype is not None and scan.fstype not in RAM_FSTYPES:
        return scan                       # on disk: not RAM, not our concern
    seen: Set[Tuple[int, int]] = set()
    dev = lst.st_dev
    loose, projects, ok, r_blind = _list(root, seen, dev)
    if not ok:
        scan.observable = False
        scan.reason = "root unreadable"
        return scan
    scan.bytes["loose"] += loose
    live, scan.procs_blind, scan.proc_ok = _live_handles(root, uid, proc_root)
    remaining = max(total_entries, 0)

    def _put(bucket: str, d: Dir) -> None:
        scan.bytes[bucket] += d.size_bytes
        scan.dirs[bucket].append(d)

    if r_blind:
        # Listable but an entry could not be stat'ed (r without x, or a mount):
        # its bytes are unknown — say so, never a measured 0 (Fable r7 #1).
        _put("unscanned", Dir(root, "", "", 0, True, None,
                              "unreadable entry directly under the root"))

    for proj in projects:
        p_loose, sessions, p_ok, p_blind = _list(proj.path, seen, dev)
        scan.bytes["loose"] += p_loose
        if not p_ok or p_blind:
            _put("unscanned", Dir(proj.path, proj.name, "", 0, True, None,
                                  "unreadable project dir or entry"))
            if not p_ok:
                continue
        for sess in sessions:
            base = dict(path=sess.path, project=proj.name, session_id=sess.name)
            if remaining <= 0:
                _put("unscanned", Dir(**base, size_bytes=0, lower_bound=True,
                                      idle_s=None, why="walk budget spent"))
                continue
            size, used, newest, truncated, blind = _walk_size(sess.path, remaining, stamp, seen, dev)
            remaining -= used
            # Table order, first match wins: a partial view is UNSCANNED before
            # any liveness call (Fable r6: a blind walk measured 0 MB and a
            # held/idle bucket at 0 MB rendered a clean "").
            if truncated or blind:
                _put("unscanned", Dir(**base, size_bytes=size, lower_bound=True,
                                      idle_s=None,
                                      why=("walk budget spent mid-dir" if truncated
                                           else "unreadable dir/subdir")))
                continue
            if sess.name in excluded:
                _put("in_use", Dir(**base, size_bytes=size, lower_bound=False,
                                   idle_s=None))
                continue
            if sess.path in live or proj.path in live:
                _put("held", Dir(**base, size_bytes=size, lower_bound=False,
                                 idle_s=None, why="a process holds it"))
                continue
            tr = os.path.join(home, ".claude", "projects", proj.name, f"{sess.name}.jsonl")
            has_tr = False
            try:
                newest = max(newest, stamp(os.stat(tr)))
                has_tr = True
            except OSError:
                pass
            idle = now - newest
            if idle < -FUTURE_SKEW_S:
                _put("undatable", Dir(**base, size_bytes=size, lower_bound=False,
                                      idle_s=None, why="future timestamp"))
            elif idle < idle_s:
                _put("recent", Dir(**base, size_bytes=size, lower_bound=False,
                                   idle_s=max(idle, 0.0)))
            else:
                _put("idle", Dir(**base, size_bytes=size, lower_bound=False,
                                 idle_s=idle))
    for b in scan.dirs:
        scan.dirs[b].sort(key=lambda d: d.size_bytes, reverse=True)
    return scan


def _age(s: float) -> str:
    return f"{int(s // 3600)}h" if s >= 3600 else f"{int(s // 60)}m"


def _mbs(n: int, lb: bool = False) -> str:
    return f"{'≥' if lb else ''}{n >> 20} MB"


def format_stale_scratch_line(scan: ScratchScan, *, limit: int = 3,
                              min_bytes: int = DEFAULT_MIN_BYTES) -> str:
    """One warm-start block; "" when nothing outside live use reaches the floor,
    or when no session ever ran here, or when the scratch is on disk.

    Prints measurements and INSPECTION commands only — never a destructive one,
    and never an unvalidated path or name."""
    root = scan.root
    q_root = shlex.quote(root) if root else None
    du = (f"`du -sm {q_root}/*/* | sort -rn | head`" if q_root
          else "`du -sm /tmp/claude-*/*/* | sort -rn | head`")
    if not scan.observable:
        if not scan.root_exists:
            return ""
        look = f"`ls -ld {q_root}`" if q_root else "`ls -ld /tmp/claude-*`"
        return (f"🧹 ⚠️ **Claude-session scratch scan UNKNOWN** — {scan.reason}. "
                f"Check by hand (does not follow the root): {look}")
    if scan.fstype is not None and scan.fstype not in RAM_FSTYPES:
        return ""
    unscanned = scan.dirs["unscanned"]
    if scan.not_in_use < min_bytes and not unscanned:
        return ""

    def _name(d: Dir) -> Optional[str]:
        if _PLAIN.fullmatch(d.project) and (not d.session_id or _PLAIN.fullmatch(d.session_id)):
            return f"{d.project}/{d.session_id}" if d.session_id else d.project
        return None

    def _inspect(d: Dir) -> str:
        n = _name(d)
        if n is None or q_root is None:
            return "(unusual name, not echoed)"
        return f"`{n}` — `du -sh {shlex.quote(os.path.join(root, n))}`"

    rows: List[str] = []
    idle = scan.dirs["idle"]
    big = [d for d in idle if d.size_bytes >= min_bytes][:limit]
    for d in big:
        rows.append(f"  - {_mbs(d.size_bytes, d.lower_bound)} idle {_age(d.idle_s)} · "
                    f"{_inspect(d)}")
    rest = [d for d in idle if d not in big]
    if rest:
        rows.append(f"  - {_mbs(sum(d.size_bytes for d in rest), any(d.lower_bound for d in rest))} in "
                    f"{len(rest)} more idle dir(s) — {du}")
    for b, label in (("held", "held by a process (live session or stray task)"),
                     ("undatable", "could NOT be dated"),
                     ("unscanned", "NOT fully scanned")):
        ds = scan.dirs[b]
        if not ds:
            continue
        whys = sorted({d.why for d in ds if d.why and b != "held"})
        part = any(d.lower_bound for d in ds) or b == "unscanned"
        why = f" ({'; '.join(whys)})" if whys else ""
        rows.append(f"  - ⚠️ {_mbs(scan.bytes[b], part)} in {len(ds)} dir(s) "
                    f"{label}{why} — {du}")
    if scan.bytes["loose"]:
        lr = f"`ls -la {q_root}`" if q_root else du
        loose_mb = (_mbs(scan.bytes['loose']) if scan.bytes['loose'] >= 1 << 20
                    else "<1 MB")
        rows.append(f"  - {loose_mb} in loose files directly under "
                    f"the root or its project dirs — {lr}")
    alongside = []
    if scan.bytes["in_use"] or scan.dirs["in_use"]:
        alongside.append(f"{_mbs(scan.bytes['in_use'])} is this session's own")
    if scan.dirs["recent"]:
        alongside.append(f"{_mbs(scan.bytes['recent'])} recent "
                         f"(< {_age(scan.idle_s)} idle) in {len(scan.dirs['recent'])} dir(s)")
    if alongside:
        rows.append("  - not counted as idle: " + "; ".join(alongside))
    lb = any(d.lower_bound for ds in scan.dirs.values() for d in ds) or bool(unscanned)
    if scan.fstype is None:
        where = ("in /tmp (filesystem type UNKNOWN — mounts unreadable; it is "
                 "RAM only if tmpfs)")
    else:
        where = (f"in {scan.fstype} (RAM or swap; charged to the cgroup that "
                 f"wrote it — normally user-{scan.uid}.slice, under its MemoryMax "
                 f"if capped)")
    handles = ("open-handle check did NOT run (/proc unreadable) — treat every "
               "dir as possibly in use"
               if not scan.proc_ok else
               f"open-handle check is best-effort ({scan.procs_blind} process(es) "
               f"unreadable) and never proves a session closed")
    return (f"🧹 **{_mbs(scan.total, lb)} of Claude-session scratch {where}** — "
            f"{_mbs(scan.not_in_use, lb)} of it not this session's own and not "
            f"recent. Delete only what "
            f"you KNOW is finished; {handles}:\n" + "\n".join(rows))
