"""Runtime self-test for systemd sandbox writable paths.

Companion to ``contrib/systemd/meshforge-gateway.service.in`` (and any
future hardened MeshForge unit). When a service runs with
``ProtectHome=read-only`` + a curated ``ReadWritePaths=`` list, a code
refactor that moves state to a new directory (e.g. Issue #29's
``_meshforge_data_dir()``) only manifests as runtime failure inside an
exception handler — the service stays ``active (running)`` while the
feature silently no-ops. moc3 ran in that state for 18h on 2026-05-18
(commit ``a420829`` shipped delivery_counters at
``~/.local/share/meshforge/`` but the unit template only whitelisted
``~/.config/meshforge`` and ``~/.cache/meshforge``).

This module turns that class of bug into a noisy startup failure with
an operator-actionable error message. Call ``assert_writable_or_exit``
near the top of a service's ``main()``, before any DB or state write.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


def meshforge_writable_paths() -> List[Path]:
    """Canonical writable buckets MeshForge services need.

    Returned in the conventional order (``.config`` first, the bucket most
    code uses; ``.local/share`` second, the DB bucket per
    ``utils.db_inventory._meshforge_data_dir``; ``.cache`` third). Hardened
    services that touch any state under ``~`` must whitelist all three —
    each new SQLite DB or persistence file lands in one of them, and a
    refactor that changes which bucket a feature uses must NOT depend on
    the systemd unit being updated in lockstep.
    """
    home = get_real_user_home()
    return [
        home / ".config" / "meshforge",
        home / ".local" / "share" / "meshforge",
        home / ".cache" / "meshforge",
    ]


def verify_writable_paths(paths: List[Path]) -> List[Tuple[Path, str]]:
    """Try to write a tempfile inside each path. Return list of failures.

    Each failure is a ``(path, error_message)`` tuple. Empty list means
    every probed path was writable. ``open(O_CREAT | O_RDWR)`` is the
    same primitive SQLite's WAL mode needs to create the ``-wal`` and
    ``-shm`` companions, so passing this probe is a strong signal that
    ``connect_tuned`` will succeed at the same path.

    A path that doesn't exist is reported as a failure (not silently
    skipped) — under ``ProtectSystem=strict + ProtectHome=read-only``,
    a missing dir can't be created at runtime by the service, so the
    operator needs to know.
    """
    failures: List[Tuple[Path, str]] = []
    for path in paths:
        if not path.exists():
            failures.append((path, "directory does not exist"))
            continue
        if not path.is_dir():
            failures.append((path, "exists but is not a directory"))
            continue
        try:
            # NamedTemporaryFile inside the target dir — tempfile picks
            # a unique name and tries O_CREAT|O_EXCL|O_RDWR. Auto-deletes
            # on context exit so we don't litter probes.
            with tempfile.NamedTemporaryFile(
                dir=str(path),
                prefix=".sandbox_probe_",
                suffix=".tmp",
                delete=True,
            ) as f:
                f.write(b"meshforge_sandbox_probe")
                f.flush()
        except OSError as e:
            # Don't smuggle errno through e.strerror alone — include the
            # path in the message so the operator can grep it directly.
            failures.append((path, f"{type(e).__name__}: {e.strerror or e}"))
    return failures


def assert_writable_or_exit(
    service_name: str,
    paths: List[Path] = None,
    *,
    exit_code: int = 2,
) -> None:
    """Verify writability or refuse to start.

    Designed to be called near the top of a service's ``main()``, before
    any database open or state write. On failure, logs the precise
    operator-actionable hint (which ReadWritePaths line to add) and
    exits with ``exit_code=2`` so systemd's ``Restart=on-failure`` +
    ``StartLimitBurst`` will eventually mark the unit failed rather
    than crashloop silently.
    """
    if paths is None:
        paths = meshforge_writable_paths()
    failures = verify_writable_paths(paths)
    if not failures:
        return

    home = get_real_user_home()
    logger.error(
        "%s: sandbox writable-path check FAILED (%d/%d paths). "
        "Service cannot persist state. Most likely the systemd unit's "
        "ReadWritePaths= is missing one of the meshforge data buckets "
        "(Issue #58 class — see persistent_issues.md).",
        service_name, len(failures), len(paths),
    )
    for path, err in failures:
        logger.error("  ✗ %s — %s", path, err)
    logger.error(
        "Hint: edit /etc/systemd/system/%s.service (or "
        "contrib/systemd/%s.service.in in the repo) and ensure "
        "ReadWritePaths= contains all of: %s",
        service_name, service_name,
        " ".join(str(p).replace(str(home), "@HOME@") for p in paths),
    )
    sys.exit(exit_code)
