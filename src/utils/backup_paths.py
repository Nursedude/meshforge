"""Backup destinations that can never destroy an existing backup.

SINGLE source of backup-path reservation. Every code path that writes a
backup file or archive resolves its destination here, so the invariant
"a backup step never overwrites" is a property of one function instead of
a habit repeated at each call site.

Why this module exists (2026-08-05, a real loss on the manager box)
------------------------------------------------------------------
A session backing up MeshAnchor's ``delivery_counters.db`` reused the
generic ``~/delivery_db_backup_<date>/`` path this fleet documents as its
backup pattern, without listing the directory first. It already existed —
it held the *same day's MeshForge* backup — and ``sqlite3.Connection.backup()``
opened the existing file and overwrote it in place. The 98,304-byte
pre-purge MeshForge copy was unrecoverable. What was destroyed was the
safety net underneath an authorised deletion, which is the entire job of
a backup.

That was ``honest_failure_modes`` #8 exactly: *fixed shared names are a
collision, not a convention.* Two sister repos (MeshForge / MeshAnchor)
with identical artifact names and an identical documented backup pattern
collide by construction in one shared home directory.

The same class was already shipped in three places, none of which had
caused a loss yet only because their collision window is one second:
``commands/rns.py`` (``config_<%Y%m%d_%H%M%S>.bak`` — two config writes in
the same second and the second backup destroys the first, the one holding
the true original), ``commands/device_backup.py``, and
``commands/fleet_backup.py``.

The invariant
-------------
**The path returned by** ``reserve_backup_path`` **never names a file that
already had content.** Two ways to honour that, both non-destructive:

``on_exists="refuse"`` (default)
    Raise ``BackupDestinationExists``. Correct when the name is meaningful
    and a collision means the caller is confused about what it is backing
    up — that case. Failing loudly beats silently clobbering.

``on_exists="unique"``
    Return an adjacent free name (``foo.json`` -> ``foo-2.json``). Correct
    when the name is merely a timestamp and a collision just means "two
    backups in the same second" — a new backup should still be written, it
    simply must not land on the old one.

Reservation is ATOMIC (``O_CREAT | O_EXCL``), not check-then-write. A
check-then-write leaves a window in which two processes both see "free"
and both proceed — the very interleaving that makes a fixed shared name
dangerous. The reserved file is created empty and returned; the caller
writes over its own reservation.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

__all__ = [
    "BackupDestinationExists",
    "reserve_backup_dir",
    "reserve_backup_path",
]

#: Upper bound on ``on_exists="unique"`` disambiguation attempts. A caller
#: that has produced this many same-named backups is looping, not backing
#: up; fail loudly rather than spin.
_MAX_UNIQUE_ATTEMPTS = 1000


class BackupDestinationExists(OSError):
    """A backup destination already exists and would have been overwritten.

    Deliberately an ``OSError`` subclass: callers that already guard their
    backup step with ``except OSError`` keep working and report a failed
    backup, rather than letting this escape as an unhandled error and
    look like a crash in unrelated code.
    """

    def __init__(self, path: Path):
        self.path = path
        super().__init__(
            errno.EEXIST,
            f"refusing to overwrite an existing backup: {path} "
            f"(pass on_exists='unique' if a new adjacent name is correct)",
        )


def _candidate(path: Path, n: int) -> Path:
    """``foo.tar.gz`` -> ``foo-2.tar.gz``, preserving multi-part suffixes."""
    name = path.name
    # Split on the FIRST dot so ".tar.gz" survives intact; Path.stem/.suffix
    # would turn foo.tar.gz into foo.tar / .gz and produce foo.tar-2.gz.
    stem, dot, suffixes = name.partition(".")
    return path.with_name(f"{stem}-{n}{dot}{suffixes}")


def reserve_backup_path(
    directory: Path,
    name: str,
    *,
    on_exists: str = "refuse",
    mode: int = 0o600,
) -> Path:
    """Atomically reserve a backup destination that overwrites nothing.

    Args:
        directory: Destination directory. Created if absent.
        name: Desired file name, e.g. ``"delivery_counters.db"``.
        on_exists: ``"refuse"`` (default) raises ``BackupDestinationExists``;
            ``"unique"`` returns an adjacent free name instead.
        mode: Permission bits for the reserved file. Defaults to owner-only,
            because backups routinely contain more of the operator's state
            than the live artifact does.

    Returns:
        Path to a newly created, empty, exclusively-reserved file. The
        caller writes over it.

    Raises:
        BackupDestinationExists: ``on_exists="refuse"`` and the path is taken.
        ValueError: unknown ``on_exists`` value, or a ``name`` containing a
            path separator (a backup helper must not be a way to write
            outside the directory the caller named).
        OSError: the directory cannot be created or written.
    """
    if on_exists not in ("refuse", "unique"):
        raise ValueError(
            f"on_exists must be 'refuse' or 'unique', got {on_exists!r}")
    if not name or name in (".", "..") or os.sep in name or "/" in name:
        raise ValueError(f"backup name must be a bare filename, got {name!r}")

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name

    attempt = 1
    while True:
        try:
            # O_EXCL is the whole point: the check and the claim are one
            # syscall, so two racing processes cannot both win.
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        except FileExistsError:
            if on_exists == "refuse":
                raise BackupDestinationExists(target) from None
            attempt += 1
            if attempt > _MAX_UNIQUE_ATTEMPTS:
                raise BackupDestinationExists(target) from None
            target = _candidate(directory / name, attempt)
            continue
        os.close(fd)
        return target


def reserve_backup_dir(
    parent: Path,
    name: str,
    *,
    on_exists: str = "refuse",
) -> Path:
    """Reserve a backup DIRECTORY that overwrites nothing.

    The directory analogue of :func:`reserve_backup_path`, and the shape the
    manager-box loss actually took — the collision was a *directory* whose
    existing contents were then written into. ``mkdir`` is atomic and fails
    with ``FileExistsError``, so an existing directory is detected rather
    than silently adopted.

    A directory that exists but is EMPTY is accepted under either mode:
    nothing can be destroyed inside it, and refusing would break the common
    "operator pre-created the folder" case.
    """
    if on_exists not in ("refuse", "unique"):
        raise ValueError(
            f"on_exists must be 'refuse' or 'unique', got {on_exists!r}")
    if not name or name in (".", "..") or os.sep in name or "/" in name:
        raise ValueError(f"backup name must be a bare filename, got {name!r}")

    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / name

    attempt = 1
    while True:
        try:
            target.mkdir()
            return target
        except FileExistsError:
            if target.is_dir() and not any(target.iterdir()):
                return target          # empty: nothing at risk
            if on_exists == "refuse":
                raise BackupDestinationExists(target) from None
            attempt += 1
            if attempt > _MAX_UNIQUE_ATTEMPTS:
                raise BackupDestinationExists(target) from None
            target = _candidate(parent / name, attempt)
