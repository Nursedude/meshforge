"""Load an LXMF/RNS identity file, or create it — never silently REPLACE it.

BIC-15 F1 (2026-10-14). An identity file IS an address on the mesh. The
pattern this replaces (operator line, broadcast bridge, gateway) was::

    if path.exists():
        try:  return RNS.Identity.from_file(path)
        except Exception:  warn("recreating")
    identity = RNS.Identity(); identity.to_file(path)

Two defects in one shape, measured against the pinned library:

* ``RNS.Identity.from_file`` never raises — ``Identity.load`` catches every
  error and it returns ``None``. So for a power-loss zero-byte file (13 such
  corpses fleet-wide on 2026-08-27) the ``except`` was dead code and ``None``
  went on into ``register_delivery_identity``: an obscure start failure. Had
  it raised, the branch would have overwritten the corpse with a NEW address.
* ``to_file`` is a plain ``open("wb")`` — not atomic, umask mode — the very
  write that produces the zero-byte corpse, and a private key left 0644.

Now: an existing-but-unreadable file is REFUSED with the corpse untouched
(``IdentityUnreadable`` names the path and both ways out); an absent one is
created 0600, fsync'd, linked into place without clobbering a racer, and READ
BACK before it is trusted. The caller turns the refusal into a witness
(status field + counter) — honest_failure_modes #1/#9.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class IdentityUnreadable(RuntimeError):
    """An identity file exists but cannot be used. Never auto-replaced."""

    def __init__(self, label: str, path: Path, why: str) -> None:
        self.path = Path(path)
        self.why = why
        super().__init__(
            f"{label} identity {path} cannot be used ({why}); refusing to start "
            f"rather than replace it — a new identity is a NEW address and every "
            f"peer that knows this one loses it. Restore it from a backup, or "
            f"accept a NEW address: mv '{path}' '{path}.bad'"
        )


def _size(path: Path) -> str:
    try:
        return f"{path.stat().st_size} bytes"
    except OSError as e:
        return f"stat failed: {e.__class__.__name__}"


def _write_new(path: Path, key: bytes) -> None:
    """0600 temp → fsync → hard-link into place (fails if a racer won) → fsync dir."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.write(fd, key)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.link(tmp, str(path))          # FileExistsError rather than clobber
    finally:
        if fd != -1:
            os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError as e:  # a dir fsync is best-effort on some filesystems
        logger.debug("identity dir fsync %s: %s", path.parent, e)


def load_or_create_identity(rns: Any, path: Path, *, label: str) -> Any:
    """Return the identity at ``path``, creating it only when ABSENT.

    Raises ``IdentityUnreadable`` when a file (or dangling symlink) is there
    but does not load, and when a freshly written key does not read back.
    """
    path = Path(path)
    if os.path.lexists(path):
        if path.is_symlink() and not path.exists():
            raise IdentityUnreadable(label, path, "dangling symlink")
        try:
            ident = rns.Identity.from_file(str(path))
        except Exception as e:  # not the pinned library's behaviour, but refuse alike
            raise IdentityUnreadable(label, path, f"load raised {e.__class__.__name__}: {e}")
        if ident is None:
            raise IdentityUnreadable(label, path, f"not a valid identity, {_size(path)}")
        return ident

    path.parent.mkdir(parents=True, exist_ok=True)
    ident = rns.Identity()
    key = ident.get_private_key()
    if not isinstance(key, (bytes, bytearray)) or not key:
        raise IdentityUnreadable(label, path, "new identity produced no private key bytes")
    try:
        _write_new(path, bytes(key))
    except FileExistsError:
        # Another process created it between our check and our link: use
        # THEIRS (one address), and still verify it like any existing file.
        return load_or_create_identity(rns, path, label=label)
    back = rns.Identity.from_file(str(path))
    if back is None or getattr(back, "hash", None) != getattr(ident, "hash", None):
        raise IdentityUnreadable(label, path, "newly written key did not read back")
    logger.info("Created new %s identity at %s", label, path)
    return ident
