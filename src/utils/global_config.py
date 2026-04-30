"""
MeshForge ecosystem-wide shared identity / config layer (read side).

Reads ``~/.config/meshforge/global.ini`` — the canonical source of truth
for values that span multiple MeshForge apps (NOC, maps, meshing_around,
MeshAnchor).  The NOC consumes it as a *fallback* before its own
``daemon.yaml`` and per-component ``settings.json`` files load, so
per-app values still take precedence.

Contract spec lives in the meshing_around_meshforge repo at
``docs/global_config.md`` — that's the canonical schema.  This module
mirrors its INI reader but emits a flat dict keyed to NOC's
:class:`DaemonConfig` field names (e.g. global ``[mqtt] broker`` →
``mqtt_broker``) so callers can directly merge into a DaemonConfig
instance via ``setattr``.

Layering: dataclass defaults < deployment profile < global.ini < system
``daemon.yaml`` < user ``daemon.yaml`` < explicit path.

Missing file → empty overrides, current behavior preserved.  Malformed
INI → log DEBUG and bail; never raise (every NOC service would die at
boot if global.ini got corrupted).
"""

import configparser
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

GLOBAL_CONFIG_FILENAME = "global.ini"
GLOBAL_CONFIG_DIRNAME = "meshforge"


def global_config_path() -> Path:
    """Canonical path: ``~/.config/meshforge/global.ini``.

    Uses :func:`utils.paths.get_real_user_home` so sudo / systemd never
    redirect to ``/root``.
    """
    return get_real_user_home() / ".config" / GLOBAL_CONFIG_DIRNAME / GLOBAL_CONFIG_FILENAME


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> Optional[bool]:
    """Return None on missing/blank; bool otherwise.

    ``None`` lets the seeding logic distinguish "global said nothing"
    from "global said False" — important because ``mqtt_enabled=False``
    is a real setting we don't want to skip.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == "":
        return None
    return s in ("true", "yes", "1", "on")


def load_global_overrides(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read ``global.ini`` and return a flat dict of NOC daemon-config overrides.

    Keys in the returned dict match :class:`DaemonConfig` field names so
    callers can ``setattr(config, key, value)`` directly.

    Currently emits keys (only when the corresponding INI value is set):

    - ``mqtt_enabled`` — inferred ``True`` whenever ``[mqtt] broker`` is
      set (an operator who configured a broker wants MQTT).  Explicit
      false in the per-app ``daemon.yaml`` still wins.
    - ``mqtt_broker``, ``mqtt_port`` — direct copies from ``[mqtt]``.

    Future fields (region preset, identity strings, data_dir) are not
    yet consumed by NOC; this reader only emits keys that have a
    matching destination today, to keep the surface honest.

    Missing or malformed file → empty dict, never raises.
    """
    target = Path(path) if path else global_config_path()
    overrides: Dict[str, Any] = {}

    if not target.exists():
        return overrides

    parser = configparser.ConfigParser()
    try:
        parser.read(str(target))
    except (configparser.Error, OSError, UnicodeDecodeError) as e:
        logger.debug("MeshForge global.ini parse failed (%s): %s", type(e).__name__, e)
        return overrides

    if parser.has_section("mqtt"):
        broker = parser.get("mqtt", "broker", fallback="").strip()
        if broker:
            overrides["mqtt_broker"] = broker
            # An operator who set a broker wants MQTT enabled.  Per-app
            # daemon.yaml can still override this back to False.
            overrides["mqtt_enabled"] = True

        port = _coerce_int(parser.get("mqtt", "port", fallback=""), 0)
        if port:
            overrides["mqtt_port"] = port

        # Explicit mqtt_enabled in [mqtt] (rare — usually inferred from
        # broker presence) takes precedence over the inference above.
        explicit_enabled = _coerce_bool(parser.get("mqtt", "enabled", fallback=None))
        if explicit_enabled is not None:
            overrides["mqtt_enabled"] = explicit_enabled

    if overrides:
        logger.info(
            "MeshForge global config applied %d override(s) from %s",
            len(overrides), target,
        )

    return overrides
