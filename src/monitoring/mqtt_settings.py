"""The one owner of the saved MQTT settings file (``mqtt_nodeless.json``).

MQTT-CFG (plan ``.claude/plans/mqtt_cfg_plan_2026_10_31.md``). The file used
to have several writers and two schemas, so every reader carried its own
translator. The cure is at the WRITE boundary:

* ``save()`` validates and stores INTENT only (what the operator chose);
  everything derivable (topic, TLS from ``"auto"``, reconnect delays, the
  JSON-leg channel) is derived, never stored.
* ``load()`` is three-state: ``ok`` / ``absent`` / ``unreadable``. An
  unreadable file is never turned into defaults (an operator setting we
  failed to honour is not "never configured").
* ``subscriptions()`` is the one derivation of the topics, from the
  SUBSCRIBER dict, so the subscriber and the TUI status cannot disagree.

This file is built to be BYTE-IDENTICAL in MeshForge and MeshAnchor
(MQTT-CFG S5 ports it and adds it to ``scripts/parity_check.py``; until
then the parity is a design intent, not a gated fact). It imports the standard library only; each
repo passes its own config directory and its own owner-preserving atomic
writer (``utils.paths.atomic_write_text_preserving``) — never a copy of it.

**Any new field = a ``SCHEMA_VERSION`` bump.** An unknown key is refused.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
FILENAME = "mqtt_nodeless.json"
PRE_V1_SUFFIX = ".pre-v1"

REQUIRED = ("broker", "port", "tls", "root_topic", "channel", "key")
OPTIONAL = {"username": "", "password": "",
            "auto_start": False, "auto_start_telemetry": True}
INTENT_FIELDS = REQUIRED + tuple(OPTIONAL)

# Public-broker values. The stdlib-only rule means they cannot be imported
# here; a test pins them to the repo's MQTT defaults module (hfm #5).
_PUBLIC = {"broker": "mqtt.meshtastic.org", "port": 8883,
           "root_topic": "msh/US/2/e", "channel": "LongFast", "key": "AQ=="}
_LOCAL_BROKERS = ("localhost", "127.0.0.1", "::1")

# Keys a legacy writer is known to have written that intent does not keep.
_LEGACY_DROPPED = ("topic", "use_tls", "regions", "json_channel",
                   "auto_reconnect", "reconnect_delay", "max_reconnect_delay",
                   "telemetry_poll_minutes")

Writer = Callable[[Path, str], None]


@dataclass
class Loaded:
    state: str                      # "ok" | "absent" | "unreadable"
    intent: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    legacy: bool = False            # converted on read; nothing was written


@dataclass
class SaveResult:
    ok: bool
    errors: List[str] = field(default_factory=list)   # validation refusals
    error: Optional[str] = None                       # one-line summary


def defaults() -> Dict[str, Any]:
    """The public-broker intent (used when the file is ABSENT)."""
    return {**_PUBLIC, "tls": "auto", **OPTIONAL}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _topic_segment_ok(s: str) -> bool:
    return bool(s) and "/" not in s and "#" not in s and "+" not in s \
        and not any(c.isspace() for c in s)


def validate(intent: Any) -> List[str]:
    """One message per field the author cannot have meant; [] when clean."""
    if not isinstance(intent, dict):
        return [f"settings: expected an object, got {type(intent).__name__}"]
    errs: List[str] = []
    for k in intent:
        if k not in INTENT_FIELDS:
            errs.append(f"{k}: unknown setting (allowed: {', '.join(INTENT_FIELDS)})")
    for k in REQUIRED:
        if k not in intent:
            errs.append(f"{k}: required")
    if "broker" in intent:
        b = intent["broker"]
        if not isinstance(b, str) or not b.strip() or any(c.isspace() for c in b):
            errs.append(f"broker: must be a host name with no spaces, got {b!r}")
    if "port" in intent:
        p = intent["port"]
        if not _is_int(p) or not 1 <= p <= 65535:
            errs.append(f"port: must be an integer 1-65535, got {p!r}")
    # `is`, not `in`: 1 == True, so a membership test would accept 1 and 0
    if "tls" in intent and not (intent["tls"] == "auto" or intent["tls"] is True
                                or intent["tls"] is False):
        errs.append(f"tls: must be \"auto\", true or false, got {intent['tls']!r}")
    if "root_topic" in intent:
        r = intent["root_topic"]
        parts = r.split("/") if isinstance(r, str) else []
        if len(parts) < 2 or parts[-1] != "e" or not all(_topic_segment_ok(s) for s in parts):
            hint = ""
            if parts and all(_topic_segment_ok(s) for s in parts) and "2" not in parts:
                # a RADIO's MQTT root (it publishes under <root>/2/e/...)
                hint = f" — for a radio root of {r!r} enter {r}/2/e"
            errs.append("root_topic: must be a topic ending in \"/e\" with no "
                        f"wildcards (e.g. msh/US/2/e), got {r!r}{hint}")
    if "channel" in intent:
        c = intent["channel"]
        if not (c == "+" or isinstance(c, str) and _topic_segment_ok(c)):
            errs.append(f"channel: must be one topic segment or \"+\", got {c!r}")
    if "key" in intent:
        k = intent["key"]
        if not isinstance(k, str) or not k.strip():
            errs.append(f"key: must be a non-empty string, got {k!r}")
    for k in ("username", "password"):
        if k in intent and not isinstance(intent[k], str):
            errs.append(f"{k}: must be a string (empty for none), got {type(intent[k]).__name__}")
    for k in ("auto_start", "auto_start_telemetry"):
        if k in intent and not isinstance(intent[k], bool):
            errs.append(f"{k}: must be true or false, got {intent[k]!r}")
    return errs


def _normalized(intent: Dict[str, Any]) -> Dict[str, Any]:
    return {**OPTIONAL, **intent}


def _read(path: Path) -> Tuple[Optional[Any], Optional[str], bool]:
    """(data, error, exists). Every step — the existence check included —
    stays inside the contract (a PermissionError on a parent is an error)."""
    try:
        if not path.exists():
            return None, None, False
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        return None, f"{path}: {type(e).__name__}: {e}", True
    try:
        return json.loads(text), None, True
    except ValueError as e:
        return None, f"{path}: not valid JSON: {e}", True


def _version(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    sv = data["schema_version"]
    if not _is_int(sv) or sv < 1:
        return None, f"schema_version: must be a positive integer, got {sv!r}"
    if sv > SCHEMA_VERSION:
        return None, (f"schema_version {sv}: written by a newer version "
                      f"(this one reads {SCHEMA_VERSION}); not reading it")
    return sv, None


def load(config_dir: Path) -> Loaded:
    path = Path(config_dir) / FILENAME
    data, err, exists = _read(path)
    if err:
        return Loaded("unreadable", error=err)
    if not exists:
        return Loaded("absent")
    if not isinstance(data, dict):
        return Loaded("unreadable",
                      error=f"{path}: expected a JSON object, got {type(data).__name__}")
    if "schema_version" not in data:
        intent, lerr = from_legacy(data)
        if lerr:
            return Loaded("unreadable", error=f"{path} (legacy): {lerr}")
        return Loaded("ok", intent=intent, legacy=True)
    _sv, verr = _version(data)
    if verr:
        return Loaded("unreadable", error=f"{path}: {verr}")
    intent = {k: v for k, v in data.items() if k != "schema_version"}
    errs = validate(intent)
    if errs:
        return Loaded("unreadable", error=f"{path}: " + "; ".join(errs))
    return Loaded("ok", intent=_normalized(intent))


def save(config_dir: Path, intent: Dict[str, Any], write: Writer) -> SaveResult:
    """Validate, then write ``intent`` through ``write`` and force 0600.

    ``write`` is the repo's owner-preserving atomic writer: the TUI runs as
    root, and a fresh root-owned file in the operator's home would lock out
    every operator-uid reader. That writer copies the OLD mode onto the new
    file, so an existing file is made 0600 BEFORE the write (credentials are
    never on disk at a wider mode) and again after. A NEWER file is never
    overwritten. Nothing the operator wrote is lost: a legacy file keeps its
    bytes once as ``.pre-v1``; a file that does not load (``unreadable`` —
    a typo in a hand-edit) keeps its bytes as ``.unreadable.<n>``, one per
    save, never overwritten.
    """
    errs = validate(intent)
    if errs:
        return SaveResult(False, errs, "; ".join(errs))
    config_dir = Path(config_dir)
    path = config_dir / FILENAME
    pre = config_dir / (FILENAME + PRE_V1_SUFFIX)
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        if path.exists():
            data, _err, _ = _read(path)
            if isinstance(data, dict) and "schema_version" in data:
                sv = data["schema_version"]
                if _is_int(sv) and sv > SCHEMA_VERSION:
                    return SaveResult(False, error=(
                        f"{path}: schema_version {sv} was written by a newer "
                        "version; refusing to overwrite it"))
            loaded = load(config_dir)
            backup = None
            if loaded.state == "unreadable":
                backup = _free_backup(config_dir)
            elif loaded.legacy and not pre.exists():
                backup = pre
            if backup is not None:
                raw = path.read_bytes().decode("utf-8", errors="backslashreplace")
                write(backup, raw)
                os.chmod(backup, 0o600)
            os.chmod(path, 0o600)   # before the writer copies its mode (review F8)
        body = {"schema_version": SCHEMA_VERSION, **_normalized(intent)}
        write(path, json.dumps(body, indent=2) + "\n")
        os.chmod(path, 0o600)
    except OSError as e:
        return SaveResult(False, error=f"{path}: {type(e).__name__}: {e}")
    handback_err = _hand_to_dir_owner(path, config_dir)
    if handback_err:
        return SaveResult(False, error=handback_err)
    return SaveResult(True)


def _owner(p: Path) -> Tuple[int, int]:
    st = os.stat(p)
    return st.st_uid, st.st_gid


def _hand_to_dir_owner(path: Path, config_dir: Path) -> Optional[str]:
    """As root, give ``path`` to the owner of ``config_dir`` (review F1).

    The writer KEEPS an existing file's owner, so a legacy file the old TUI
    wrote as root stays root-owned — and 0600 then locks every operator-uid
    reader out (the subscriber reads `unreadable` and refuses). The config
    dir is the operator's own; its owner is who must be able to read the
    file. A root-owned dir names no operator, so nothing is changed. None =
    nothing to do or done; a string = written but NOT readable by the
    operator, which must not be reported as saved."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    try:
        want = _owner(config_dir)
        have = _owner(path)
    except OSError as e:
        return (f"{path}: written, but its owner could not be checked: "
                f"{type(e).__name__}: {e}")
    if want[0] == 0 or have == want:
        return None
    try:
        os.chown(path, want[0], want[1])
    except OSError as e:
        return (f"{path}: written, but could not hand it to the config dir's "
                f"owner (uid {want[0]}): {type(e).__name__}: {e} — "
                "operator-uid readers cannot open it")
    return None


def _free_backup(config_dir: Path) -> Path:
    for n in range(1, 1000):
        cand = config_dir / f"{FILENAME}.unreadable.{n}"
        if not cand.exists():
            return cand
    raise OSError(f"{config_dir}: 999 unreadable-file backups already kept; "
                  "clear some before saving again")


def effective_tls(intent: Dict[str, Any]) -> bool:
    tls = intent.get("tls", "auto")
    if tls == "auto":
        return intent.get("port") == 8883
    return bool(tls)


def subscriber_config(intent: Dict[str, Any]) -> Dict[str, Any]:
    """The config dict ``MQTTNodelessSubscriber(config=...)`` takes."""
    i = _normalized(intent)
    local = i["broker"] in _LOCAL_BROKERS
    return {
        "broker": i["broker"],
        "port": i["port"],
        "username": i["username"],
        "password": i["password"],
        "root_topic": i["root_topic"],
        "channel": i["channel"],
        # a local broker hears every channel its radio uplinks (10-13)
        "json_channel": "+" if local else i["channel"],
        "key": i["key"],
        "use_tls": effective_tls(i),
        "auto_reconnect": True,
        "reconnect_delay": 2 if local else 5,
        "max_reconnect_delay": 30 if local else 60,
    }


def subscriptions(cfg: Dict[str, Any],
                  default_root: str = _PUBLIC["root_topic"],
                  default_channel: str = _PUBLIC["channel"]) -> List[str]:
    """``[json_topic, encrypted_topic]`` for a SUBSCRIBER dict.

    Takes the subscriber dict, not the intent, so overrides applied to a
    live subscriber's config (kilo ``--root-topic``/``--channel``, a daemon
    overlay) are what is shown and what is subscribed.
    """
    root = cfg.get("root_topic", default_root)
    channel = cfg.get("channel", default_channel)
    json_channel = cfg.get("json_channel", channel)
    return [f"{root.rsplit('/', 1)[0]}/json/{json_channel}/#",
            f"{root}/{channel}/#"]


def _legacy_port(v: Any) -> Tuple[Optional[int], Optional[str]]:
    if _is_int(v):
        return v, None
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip()), None
    return None, f"port: must be an integer, got {v!r}"


def _legacy_topic(topic: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(root_topic, channel, error) from a legacy subscription topic."""
    if not isinstance(topic, str):
        return None, None, f"topic: must be a string, got {topic!r}"
    parts = topic.rstrip("#").rstrip("/").split("/")
    if len(parts) < 4 or not parts[-1] or parts[-1] == "#":
        return None, None, f"topic: too short to name a root and a channel: {topic!r}"
    channel, base = parts[-1], parts[:-1]
    if base[-1] == "json":
        base = base[:-1] + ["e"]
    return "/".join(base), channel, None


def from_legacy(raw: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Convert a pre-v1 file ONLY when unambiguous (Q13); never guess.

    Known shapes: the TUI local/public presets, Configure (``topic`` edited,
    maybe beside a stale ``root_topic``/``channel`` pair — ``topic`` is the
    newer edit and wins), the private-broker form (pair + matching topic),
    broker profiles (``/e/`` or ``/json/`` topics), and the subscriber's own
    schema (``root_topic``/``channel``, no topic).
    """
    if not isinstance(raw, dict):
        return None, f"expected an object, got {type(raw).__name__}"
    for k in raw:
        if k not in INTENT_FIELDS and k not in _LEGACY_DROPPED:
            return None, f"{k}: unknown setting in a legacy file"
    out: Dict[str, Any] = {}
    if not isinstance(raw.get("broker"), str):
        return None, f"broker: required, got {raw.get('broker')!r}"
    out["broker"] = raw["broker"]
    port, err = _legacy_port(raw.get("port", 8883))
    if err:
        return None, err
    out["port"] = port
    pair_agrees = (isinstance(raw.get("topic"), str)
                   and isinstance(raw.get("root_topic"), str)
                   and isinstance(raw.get("channel"), str)
                   and raw["topic"].rstrip("#").rstrip("/")
                   == f"{raw['root_topic']}/{raw['channel']}")
    if pair_agrees:
        # the private form writes topic == root/channel/#: the pair IS the
        # intent, so a bad root is reported as the root (not "short topic")
        root, channel = raw["root_topic"], raw["channel"]
    elif "topic" in raw:
        root, channel, err = _legacy_topic(raw["topic"])
        if err:
            return None, err
    elif "root_topic" in raw:
        root = raw["root_topic"]
        if "channel" not in raw:
            return None, "channel: a legacy root_topic without a channel"
        channel = raw["channel"]
    else:
        return None, "topic: nothing to subscribe to (no topic or root_topic)"
    out["root_topic"], out["channel"] = root, channel
    if "use_tls" in raw:
        t = raw["use_tls"]
        if not isinstance(t, bool):
            return None, f"use_tls: must be true or false, got {t!r}"
        out["tls"] = "auto" if t == (port == 8883) else t
    else:
        out["tls"] = "auto"
    out["key"] = raw.get("key") or _PUBLIC["key"]
    for k in ("username", "password"):
        v = raw.get(k)
        out[k] = "" if v is None else v
    for k in ("auto_start", "auto_start_telemetry"):
        if k in raw:
            out[k] = raw[k]
    errs = validate(out)
    if errs:
        return None, "; ".join(errs)
    return _normalized(out), None
