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

**F8 v2 — the EDIT LEASE** (grill ``.claude/plans/mqtt_f8_state_model_2026_11_02.md``
§ F8 v2, V1-V14). One editor at a time: ``acquire_lease()`` takes an
``fcntl.flock`` on a file BESIDE the config dir; a second opener reads
``busy`` and the holder's record (or ``None`` = unknown — never "free").
``save()`` refuses without a held lease for THIS dir (V9), and refuses when
the file changed since the lease was taken — someone wrote it without the
lease (V7) — except Reset over a file that BROKE after open, which rebuilds
from the screen (V2). The saved password never leaves the file: a lease's
``view`` carries ``KEEP`` (Q3), and KEEP across an account change refuses
(Q7, account = normalized host + user, Q4/Q8). The merge model this replaces
(per-field conflicts, Q10/Q17/Q18) was parked after five review rounds.
"""
import fcntl
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

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
    # why it was refused — one of: no_lease, changed_without_lease, invalid,
    # newer, unreadable, password_owner_mismatch, password_unknown, io,
    # handback (written, but not readable by the operator). None when ok.
    code: Optional[str] = None
    now_unreadable: bool = False    # changed_without_lease AND the file broke


class _Keep:
    """The saved password, unchanged — a screen never holds the value (Q3)."""
    __slots__ = ()

    def __repr__(self) -> str:
        return "KEEP"


KEEP = _Keep()


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


def save(config_dir: Path, intent: Dict[str, Any], write: Writer,
         lease: Optional["EditLease"], reset: bool = False) -> SaveResult:
    """Under a held ``lease``: check, validate, then write ``intent`` through
    ``write`` and force 0600.

    Refusals, in order (each writes nothing): ``no_lease`` (V9);
    ``changed_without_lease`` — the file is not what the lease saw (V7);
    ``reset`` overrides that ONLY when the file now fails to load (V2: the
    screen is the last copy), never over another writer's readable file;
    ``invalid``; ``newer``; ``unreadable`` — a file that did not load needs
    an explicit ``reset`` (Q15); ``password_unknown`` — KEEP with no
    readable saved password; ``password_owner_mismatch`` — KEEP across an
    account change (Q7).

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
    config_dir = Path(config_dir)
    path = config_dir / FILENAME
    pre = config_dir / (FILENAME + PRE_V1_SUFFIX)
    if lease is None or not lease.held or not lease.covers(config_dir):
        return SaveResult(False, error=(
            "not saved: this screen does not hold the MQTT edit lease "
            f"for {config_dir}"), code="no_lease")
    loaded = load(config_dir)
    if _fingerprint(path) != lease.fingerprint:
        broke = loaded.state == "unreadable"
        if not (reset and broke):
            return SaveResult(False, error=(
                f"{path} changed since this screen opened — written without "
                "the edit lease" + (" and it no longer loads: "
                                    f"{loaded.error}" if broke else
                                    "; reopen to see it (nothing written)")),
                code="changed_without_lease", now_unreadable=broke)
    intent = dict(intent)
    keep = intent.get("password") is KEEP
    errs = validate({**intent, "password": ""} if keep else intent)
    if errs:
        return SaveResult(False, errs, "; ".join(errs), code="invalid")
    data, _err, _ = _read(path)
    if isinstance(data, dict) and "schema_version" in data:
        sv = data["schema_version"]
        if _is_int(sv) and sv > SCHEMA_VERSION:
            return SaveResult(False, error=(
                f"{path}: schema_version {sv} was written by a newer "
                "version; refusing to overwrite it"), code="newer")
    if loaded.state == "unreadable" and not reset:
        return SaveResult(False, error=(
            f"{path} does not load ({loaded.error}); Reset writes this "
            "screen's settings and keeps the file as a backup"),
            code="unreadable")
    if keep:
        who = "@".join(reversed(account(intent)))
        if loaded.state == "unreadable":
            return SaveResult(False, error=(
                f"the saved password for {who} cannot be read (the file does "
                "not load) — enter it (blank = none)"), code="password_unknown")
        saved = loaded.intent if loaded.state == "ok" else None
        saved_pw = saved["password"] if saved else ""
        if saved_pw and account(saved) != account(intent):
            return SaveResult(False, error=(
                f"the saved password belongs to "
                f"{'@'.join(reversed(account(saved)))}, not {who} — enter the "
                f"password for {who} (blank = none)"),
                code="password_owner_mismatch")
        intent["password"] = saved_pw
    try:
        if not config_dir.is_dir():
            # as root, a dir created here must be the operator's, or their
            # own next save cannot write into it (review r1 #1)
            config_dir.mkdir(parents=True, exist_ok=True)
            dir_err = _hand_to_dir_owner(config_dir, config_dir.parent)
            if dir_err:
                return SaveResult(False, error=dir_err, code="handback")
        if path.exists():
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
        lease.fingerprint = _fingerprint(path)   # whatever landed is ours
        return SaveResult(False, error=f"{path}: {type(e).__name__}: {e}",
                          code="io")
    lease.fingerprint = _fingerprint(path)       # our own write, not a move
    handback_err = _hand_to_dir_owner(path, config_dir)
    if handback_err:
        return SaveResult(False, error=handback_err, code="handback")
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


# --- the edit lease (F8 v2) ------------------------------------------------

LEASE_SUFFIX = ".mqtt-edit.lease"
_LOCAL_FOLD = "localhost"


def account(intent: Dict[str, Any]) -> Tuple[str, str]:
    """(host, user) a password belongs to (Q4/Q8). Host trimmed, lowercased,
    trailing dot dropped, loopback names folded; no DNS. A blank user is
    still an account."""
    host = str(intent.get("broker") or "").strip().lower().rstrip(".")
    if host in _LOCAL_BROKERS:
        host = _LOCAL_FOLD
    return host, str(intent.get("username") or "")


def lease_path(config_dir: Path) -> Path:
    """BESIDE the config dir (r3 #4): the dir may not exist before the first
    save, and a stable target is what two openers must agree on."""
    config_dir = Path(config_dir)
    return config_dir.parent / f".{config_dir.name}{LEASE_SUFFIX}"


def _fingerprint(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "absent"
    except OSError as e:
        return f"error:{type(e).__name__}:{getattr(e, 'errno', '')}"


@dataclass
class Holder:
    pid: int
    user: str
    tty: str
    since: str


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def _read_holder(fd: int) -> Optional[Holder]:
    """The holder's own record — None when it cannot be trusted (V12)."""
    try:
        raw = os.pread(fd, 4096, 0).decode("utf-8")
        rec = json.loads(raw)
        h = Holder(int(rec["pid"]), str(rec["user"]), str(rec["tty"]),
                   str(rec["since"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return h if h.pid > 0 and _pid_alive(h.pid) else None


class EditLease:
    """A HELD lease: what the screen opened on, and the lock that keeps it so.

    ``view`` is the intent to edit — the defaults when the file is absent or
    unreadable (``loaded_state`` says which) — with the password as KEEP.
    ``fingerprint`` is the file as the lease saw it; only ``save()`` under
    this lease moves it, to what it wrote."""

    _fd: Optional[int] = None       # a half-built lease holds nothing (r1 #7)

    def __init__(self, config_dir: Path, path: Path, fd: int):
        self.config_dir = Path(config_dir)
        self.path = str(path)
        self._fd = fd
        file = self.config_dir / FILENAME
        self.fingerprint = _fingerprint(file)
        loaded = load(self.config_dir)
        self.loaded_state = loaded.state
        self.loaded_error = loaded.error
        self.legacy = loaded.legacy
        base = loaded.intent if loaded.state == "ok" else defaults()
        self.password_saved = bool(loaded.state == "ok" and base["password"])
        self.saved_account = account(base) if loaded.state == "ok" else None
        self.view: Dict[str, Any] = {**base, "password": KEEP}

    @property
    def held(self) -> bool:
        return self._fd is not None

    def covers(self, config_dir: Path) -> bool:
        return Path(config_dir).resolve() == self.config_dir.resolve()

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.ftruncate(fd, 0)          # no stale holder record (V12)
        except OSError:
            pass                         # the record is checked for liveness
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def __enter__(self) -> "EditLease":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    def __del__(self):
        self.release()


@dataclass
class LeaseResult:
    state: str                          # "held" | "busy" | "error"
    lease: Optional[EditLease] = None
    holder: Optional[Holder] = None     # busy: None = an unknown session
    error: Optional[str] = None


def acquire_lease(config_dir: Path, user: str, tty: str) -> LeaseResult:
    """Try ONCE, no wait (V12): busy is answered at once, never a pause.

    A missing parent (a fresh headless home with no ~/.config) is created —
    save() always created its dir, so refusing here would block every edit
    on such a box — and, as root, handed to the home's owner."""
    path = lease_path(config_dir)
    try:
        if not path.parent.is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
            err = _hand_to_dir_owner(path.parent, path.parent.parent)
            if err:
                return LeaseResult("error", error=err)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    except OSError as e:
        return LeaseResult("error", error=(
            f"{path}: cannot open the MQTT edit lease: {type(e).__name__}: {e}"))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = _read_holder(fd)
        os.close(fd)
        return LeaseResult("busy", holder=holder)
    except OSError as e:
        os.close(fd)
        return LeaseResult("error", error=f"{path}: flock: {type(e).__name__}: {e}")
    try:
        rec = json.dumps({"pid": os.getpid(), "user": user, "tty": tty,
                          "since": time.strftime("%Y-%m-%d %H:%M:%S %Z")})
        os.ftruncate(fd, 0)
        os.pwrite(fd, rec.encode("utf-8"), 0)
        handback_err = _hand_to_dir_owner(path, Path(config_dir).parent)
        if handback_err:   # held for THIS session; others may be locked out (r1 #5)
            _log.warning("MQTT edit lease: %s", handback_err)
        return LeaseResult("held", lease=EditLease(config_dir, path, fd))
    except OSError as e:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        return LeaseResult("error", error=(
            f"{path}: cannot record the MQTT edit lease: {type(e).__name__}: {e}"))


def saved_password_relation(lease: EditLease, candidate: str) -> str:
    """none / same / differs / unknown — the saved password vs a value a
    preset or profile would write (Q19). Read from the file under the lease;
    the value never leaves this function."""
    if not lease.held:
        return "unknown"
    loaded = load(lease.config_dir)
    if loaded.state == "absent":
        return "none"
    if loaded.state != "ok":
        return "unknown"
    saved = loaded.intent["password"]
    if not saved:
        return "none"
    return "same" if hmac.compare_digest(saved.encode(), candidate.encode()) \
        else "differs"


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
