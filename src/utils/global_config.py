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
import re
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


#: ``[meshtastic] own_node_id`` (BIC-2 DM slice 2, operator 2026-11-27: lives
#: here, the per-box identity layer). STRICT at the authoring boundary (hfm
#: #3): exactly ``!`` + 8 hex digits, any case. The DM seam reads shorter
#: spellings off the wire (``dm_delivery.NODE_ID_RE``); a human writing config
#: copies the full id the radio prints, so a short one is a typo, not a node.
OWN_NODE_ID_RE = re.compile(r"^![0-9a-fA-F]{8}$")
OWN_NODE_ID_KEY = "own_node_id"
#: A key a human plausibly meant for ``own_node_id`` INSIDE ``[meshtastic]``
#: (normalised: lower case, letters/digits only) -- refused LOUDLY, never read
#: as "unset". Exact-normalised, never a substring, and never in another
#: section: global.ini is SHARED with sister apps (Fable r2 N4: a substring
#: test over every section refused a valid id beside ``[power] shutdown_idle``
#: / ``[gateway] gateway_node_id``). Outside ``[meshtastic]`` only the exact
#: key itself is refused (it was put in the wrong place).
_OWN_NODE_ID_LOOKALIKES = frozenset((
    "ownnodeid", "ownnodeids", "nodeid", "nodenum", "ownnode", "ownnodenum",
    "ownid", "myid", "mynode", "mynodeid", "mynodenum", "ownradioid", "radioid"))
#: ConfigParser's [DEFAULT] leaks into every section (Fable r1 #5): parse
#: with an unreachable default-section name so [DEFAULT] is an ordinary one.
_NO_DEFAULT = "\x00meshforge-no-default"


def _own_id_parser() -> configparser.ConfigParser:
    return configparser.ConfigParser(interpolation=None, default_section=_NO_DEFAULT)


def _is_meshtastic(section: str) -> bool:
    return section.strip().lower() == "meshtastic"


def _lookalike(key: str) -> bool:
    return (key != OWN_NODE_ID_KEY
            and re.sub(r"[^0-9a-z]", "", key.lower()) in _OWN_NODE_ID_LOOKALIKES)


def read_own_node_id(path: Optional[Path] = None) -> Dict[str, Any]:
    """This box's Meshtastic node id as the OPERATOR configured it.

    Tri-state plus refusal, never raises:
      ``{"state": "unset", "num": None, "why": ""}``      nothing configured
      ``{"state": "ok", "num": <int>, "why": "", "raw": "!xxxxxxxx"}``
      ``{"state": "invalid", "num": None, "why": ...}``   present, refused
      ``{"state": "unreadable", "num": None, "why": ...}`` file unreadable
    Unset is today's behaviour (the id is unknown). An invalid value is never
    absorbed into unset: the witness line says it was refused, and why. The
    section name matches in any case/padding (``[Meshtastic]``); the key is
    refused under any other section and beside a lookalike key.
    """
    p = global_config_path() if path is None else Path(path)
    unset = {"state": "unset", "num": None, "why": ""}
    try:
        if not p.exists():
            return unset
        parser = _own_id_parser()
        with open(p, encoding="utf-8-sig") as fh:     # a BOM is not a fault (r2 N4)
            parser.read_file(fh)
    except (OSError, UnicodeDecodeError, configparser.Error) as e:
        return {"state": "unreadable", "num": None,
                "why": f"{p.name} unreadable: {type(e).__name__}"}
    mine = [sec for sec in parser.sections() if _is_meshtastic(sec)]
    for sec in parser.sections():
        if sec in mine:
            continue
        stray = [k for k in parser.options(sec) if k == OWN_NODE_ID_KEY]
        if stray:
            return {"state": "invalid", "num": None,
                    "why": f"{stray[0]!r} is under [{sec}], not [meshtastic]"}
    if len(mine) > 1:
        return {"state": "invalid", "num": None,
                "why": f"{len(mine)} [meshtastic] sections ({', '.join(mine)})"}
    if not mine:
        return unset
    sec = mine[0]
    near = [k for k in parser.options(sec) if _lookalike(k)]
    if near:
        return {"state": "invalid", "num": None,
                "why": f"[meshtastic] has {near[0]!r} — the key is {OWN_NODE_ID_KEY!r}"}
    raw = parser.get(sec, OWN_NODE_ID_KEY, fallback=None)
    if raw is None:
        return unset
    v = raw.strip()
    if v == "":
        return unset
    if not OWN_NODE_ID_RE.fullmatch(v):
        return {"state": "invalid", "num": None,
                "why": f"{OWN_NODE_ID_KEY} {v[:24]!r} is not !xxxxxxxx (8 hex digits)"}
    num = int(v[1:], 16)
    if num in (0, 0xFFFFFFFF):
        return {"state": "invalid", "num": None,
                "why": f"{OWN_NODE_ID_KEY} {v} is never a node (zero / broadcast)"}
    return {"state": "ok", "num": num, "why": "", "raw": f"!{num:08x}"}


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


#: What the TUI adds when the operator opens the radio-id editor and the file
#: carries no ``[meshtastic]`` section yet (empty value = unset = today).
OWN_NODE_ID_TEMPLATE = (
    "[meshtastic]\n"
    "# This box's OWN radio id, exactly as the radio prints it: ! + 8 hex digits\n"
    "# (TUI: Radio -> node info, or `meshtastic --info` 'myNodeNum' in hex).\n"
    "# Used only to judge DM delivery (a DM to this radio is never 'delivered').\n"
    "# Leave empty if unsure: empty = unknown, never a guess.\n"
    f"{OWN_NODE_ID_KEY} =\n"
)


def ensure_own_node_id_section(path: Optional[Path] = None) -> Path:
    """Make sure global.ini has a ``[meshtastic]`` section to edit; returns the
    path. Never touches a file that already has one (any header ConfigParser
    accepts: ``[Meshtastic]``, ``[meshtastic] # radio`` — Fable r1 #3 dup'd
    those and broke the whole file); appends the template otherwise (creating
    the file if absent). A symlink is written THROUGH (r1 #7). An undecodable
    or malformed file is refused UNTOUCHED as ``OSError`` (r1 #6) — the caller
    says it. A root-created file goes back to the operator (sudo TUI)."""
    from utils.paths import atomic_write_text, chown_to_operator
    p = global_config_path() if path is None else Path(path)
    target = p.resolve() if p.is_symlink() else p
    try:
        existing = target.read_bytes().decode("utf-8-sig")   # same rule as the reader (r3 N3)
    except FileNotFoundError:
        existing = None
    except UnicodeDecodeError as e:
        raise OSError(f"{p} is not UTF-8 text ({e.reason}); not modified") from e
    if existing is not None:
        parser = _own_id_parser()
        try:
            parser.read_string(existing)
        except configparser.Error as e:
            raise OSError(f"{p} is not a readable INI file ({type(e).__name__}); "
                          "not modified") from e
        if any(_is_meshtastic(sec) for sec in parser.sections()):
            return p
    target.parent.mkdir(parents=True, exist_ok=True)
    body = OWN_NODE_ID_TEMPLATE if not existing else (
        existing.rstrip("\n") + "\n\n" + OWN_NODE_ID_TEMPLATE)
    atomic_write_text(target, body)
    chown_to_operator(target)
    return p


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
