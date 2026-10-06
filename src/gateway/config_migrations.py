"""Gateway config migrations — split out of gateway/config.py (MF025 cap)."""

import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger("gateway.config")

# gateway.json schema (1.0 gate 2). Bump ONLY with a migration from the
# previous version; a file stamped higher than this was written by a newer
# MeshForge and is refused, never reinterpreted. Absent = 0 (pre-gate-2).
CONFIG_SCHEMA_VERSION = 1


def migrate_stale_http_port(meshtastic_data: dict) -> dict:
    """Migrate the stale http_port=443 default (Issue #62 pattern).

    http_port defaulted to 443 for a long stretch and got baked into
    every rendered/saved gateway.json, while meshtasticd's web API
    lives on 9443 — so the primary stateless TX path was dead
    (connection refused, circuit breaker permanently flapping) and
    every send rode the legacy session fallback (#17 contention
    class). 443 was never a valid value for us (Issue #58 treats a
    :443 webserver override as forbidden), so it is safe to treat
    a saved 443 as the stale default rather than operator intent.
    """
    if meshtastic_data.get('http_port') == 443:
        meshtastic_data = dict(meshtastic_data)
        meshtastic_data['http_port'] = 9443
        logger.info(
            "Migrated stale http_port 443 -> 9443 "
            "(saved default predating the 9443 fix)")
    return meshtastic_data


def check_schema_version(data: dict) -> int:
    """Return the file's schema version; RAISE on one this build cannot read.

    Raising lands in GatewayConfig.load()'s handler, so the result is the
    existing refusal path: defaults marked with ``load_error`` (shown in the
    TUI gateway/NOC panes and the launcher) and a save() that refuses to
    write over the file.
    """
    if 'schema_version' not in data:
        return 0
    version = data['schema_version']
    # bool is an int subclass: true would read as 1.
    if type(version) is not int or version < 0:
        raise ValueError(
            f"gateway.json schema_version {version!r} is not a whole number "
            "≥ 0 — refusing to guess which format the file is in; set it to "
            f"the number this file was written as (current: "
            f"{CONFIG_SCHEMA_VERSION}), or remove the key if it predates it")
    if version > CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"gateway.json schema_version {version} was written by a newer "
            f"MeshForge (this version reads up to {CONFIG_SCHEMA_VERSION}) — "
            "refusing to read it; upgrade MeshForge, or restore an older copy")
    return version


def keep_pre_migration_copy(config_path: Path) -> bool:
    """Before a save rewrites an OLDER-schema file, copy it once to
    ``<name>.pre-v<CURRENT>``. Returns False only when that copy was owed
    and could not be made — the caller must then refuse to save.

    An existing copy is never replaced: it is the operator's original, and
    every later save is over a current-version file anyway. A file that
    does not parse is not this function's case (load() marked it, and
    save() refuses it before reaching here).
    """
    try:
        on_disk = json.loads(config_path.read_text())
    except (OSError, ValueError):
        return True
    if not isinstance(on_disk, dict):
        return True
    try:
        if check_schema_version(on_disk) >= CONFIG_SCHEMA_VERSION:
            return True
    except ValueError:
        return True  # load() refuses such a file; not a migration
    keep = config_path.with_name(
        f"{config_path.name}.pre-v{CONFIG_SCHEMA_VERSION}")
    if keep.exists():
        return True
    try:
        shutil.copy2(config_path, keep)
    except OSError as e:
        logger.error("REFUSING to save %s: could not keep the pre-migration "
                     "copy %s (%s)", config_path, keep, e)
        return False
    logger.info("Kept pre-migration copy of %s as %s", config_path, keep)
    return True
