"""Operator sessions for the map's web writes (BIC-3, 2026-10-13).

BYTE-IDENTICAL in MeshForge and MeshAnchor: the app is read from
``utils.common.CONFIG_DIR`` (``~/.config/meshforge`` or ``~/.config/meshanchor``),
which names both the store's directory and the CLI (``scripts/<app>_web.py``).

Viewing the map stays as it was (LAN trust, no login). Every state-changing
request needs an OPERATOR session, minted by redeeming a one-time pairing code
that only a shell on the box can create (``scripts/<app>_web.py pair``).

Spec: ``.claude/plans/bic3_web_auth_2026_10_13.md``.

The store is one JSON file holding only SHA-256 hashes. Honest failure: a
store that cannot be read refuses every session — it never reads as "no
sessions, admit everyone", and a corrupt one is moved aside (the corpse kept
as evidence), never silently re-created over.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

from utils.common import CONFIG_DIR

logger = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
CODE_LEN = 8
PAIRING_TTL_S = 5 * 60
PAIRING_MAX_TRIES = 5
SESSION_IDLE_S = 30 * 86400
TOUCH_EVERY_S = 3600          # last_used is persisted at most hourly
COOKIE_NAME = "mf_operator"
# ABSOLUTE, from where this checkout actually is: the operator reads these in a
# 401, the login page or the TUI and pastes them into whatever shell they have
# open — a repo-relative path failed from $HOME on first real use (2026-10-13).
WEB_CLI = Path(__file__).resolve().parents[2] / "scripts" / f"{CONFIG_DIR.name}_web.py"
PAIR_COMMAND = f"python3 {WEB_CLI} pair"
REVOKE_COMMAND = f"python3 {WEB_CLI} revoke-all"


class StoreUnavailable(Exception):
    """The session store could not be read; ``reason`` says why and what to do."""


def default_store_path() -> Path:
    return CONFIG_DIR / "web_sessions.json"


def _h(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _empty() -> dict:
    return {"version": 1, "sessions": [], "pairing": None}


class SessionStore:
    """Hashed operator sessions + at most one pending pairing code, in one file.

    Every mutation is read-modify-write under an exclusive flock on a sidecar
    ``.lock`` file (the data file is atomically REPLACED, so locking it would
    lock an inode that is about to vanish). The CLI and the map service share
    this class, so they share the lock.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_store_path()
        self._tlock = threading.Lock()
        self.store_errors = 0

    # ── file layer ──────────────────────────────────────────────────────

    def _lockfile(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _read(self) -> dict:
        """Missing file = never paired (a real, healthy state). Anything else
        that is not a well-formed store raises StoreUnavailable."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return _empty()
        except OSError as e:
            self.store_errors += 1
            raise StoreUnavailable(
                f"the operator session store {self.path} is unreadable "
                f"({e.__class__.__name__}); fix its owner/mode (0600, the map's "
                f"user), then run: {PAIR_COMMAND}") from e
        try:
            data = json.loads(raw.decode("utf-8"))
            if not (isinstance(data, dict) and isinstance(data.get("sessions"), list)):
                raise ValueError("not a session store")
            for s in data["sessions"]:
                if not (isinstance(s, dict) and isinstance(s.get("h"), str)):
                    raise ValueError("malformed session row")
            return data
        except (ValueError, UnicodeDecodeError) as e:
            self.store_errors += 1
            corpse = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            try:
                os.replace(self.path, corpse)
                where = f"moved to {corpse}"
            except OSError as move_err:
                where = f"could NOT be moved aside ({move_err})"
            logger.warning("Operator session store %s is corrupt (%s); %s. "
                           "All sessions are void until re-paired.", self.path, e, where)
            raise StoreUnavailable(
                f"the operator session store was corrupt and {where}; every "
                f"session is void. Re-pair on the box: {PAIR_COMMAND}") from e

    def _write(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".web_sessions.", dir=str(self.path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _mutate(self, fn):
        with self._tlock:
            fd = self._lockfile()
            try:
                data = self._read()
                result, changed = fn(data)
                if changed:
                    self._write(data)
                return result
            finally:
                os.close(fd)

    # ── operations ──────────────────────────────────────────────────────

    def start_pairing(self) -> str:
        """Create a fresh one-time code (replacing any pending one). The code
        itself is returned once and never stored."""
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))

        def fn(d):
            d["pairing"] = {"h": _h(code), "expires": time.time() + PAIRING_TTL_S, "tries": 0}
            return code, True
        return self._mutate(fn)

    def redeem(self, code: str) -> Optional[str]:
        """Trade a valid pending code for a new session token, or None. A wrong
        code spends one try; the fifth burns the code. Raises StoreUnavailable."""
        given = _h(_normalize_code(code))
        token = secrets.token_urlsafe(32)

        def fn(d):
            p = d.get("pairing")
            now = time.time()
            if not isinstance(p, dict):
                return None, False
            if now > float(p.get("expires", 0)):
                d["pairing"] = None
                return None, True
            if not hmac.compare_digest(given, str(p.get("h", ""))):
                p["tries"] = int(p.get("tries", 0)) + 1
                if p["tries"] >= PAIRING_MAX_TRIES:
                    d["pairing"] = None
                    logger.warning("Operator pairing code burned after %d wrong tries",
                                   PAIRING_MAX_TRIES)
                return None, True
            d["pairing"] = None
            d["sessions"].append({"h": _h(token), "created": now, "last_used": now})
            return token, True
        return self._mutate(fn)

    def validate(self, token: Optional[str]) -> bool:
        """True for a live session. Idle past SESSION_IDLE_S = expired. A clock
        that stepped BACKWARD never expires a session (RTC-less Pis)."""
        if not token:
            self._read()  # still surface an unreadable/corrupt store
            return False
        want = _h(token)

        def fn(d):
            now = time.time()
            for s in d["sessions"]:
                if hmac.compare_digest(want, s["h"]):
                    last = float(s.get("last_used", 0))
                    if now - last > SESSION_IDLE_S:
                        d["sessions"].remove(s)
                        return False, True
                    if now - last > TOUCH_EVERY_S:
                        s["last_used"] = now
                        return True, True
                    return True, False
            return False, False
        return self._mutate(fn)

    def end(self, token: Optional[str]) -> bool:
        if not token:
            return False
        want = _h(token)

        def fn(d):
            keep = [s for s in d["sessions"] if not hmac.compare_digest(want, s["h"])]
            changed = len(keep) != len(d["sessions"])
            d["sessions"] = keep
            return changed, changed
        return self._mutate(fn)

    def revoke_all(self) -> int:
        def fn(d):
            n = len(d["sessions"])
            d["sessions"], d["pairing"] = [], None
            return n, True
        return self._mutate(fn)

    def status(self) -> dict:
        """Counts and times only — never a hash. Raises StoreUnavailable."""
        d = self._read()
        now = time.time()
        live = [s for s in d["sessions"] if now - float(s.get("last_used", 0)) <= SESSION_IDLE_S]
        p = d.get("pairing")
        return {
            "sessions": len(live),
            "last_used": max((float(s.get("last_used", 0)) for s in live), default=None),
            "pairing_pending": bool(isinstance(p, dict) and now <= float(p.get("expires", 0))),
        }


def cookie_token(cookie_header: Optional[str]) -> Optional[str]:
    """Pull our session token out of a Cookie header (no SimpleCookie: it
    rejects the whole header on one malformed foreign cookie)."""
    for part in (cookie_header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name == COOKIE_NAME and value:
            return value
    return None


def set_cookie_value(token: str) -> str:
    return (f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age={SESSION_IDLE_S}")


def clear_cookie_value() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


#: F4 (adversarial pass 2026-10-14): one hostile LAN client could spend the
#: operator's shared 5-try pairing code in milliseconds. A per-client budget
#: of wrong codes sits AHEAD of that burn, so a single client can no longer
#: burn a code within its 5-minute life (two clients still can — accepted).
LOGIN_TRIES_PER_CLIENT = 3
LOGIN_TRY_WINDOW_S = 600.0


class TryBudget:
    """Sliding-window budget of WRONG tries per client key (in-memory, per
    process). ``wait_s(key)`` is 0 when the client may try, else the seconds
    until its oldest counted try leaves the window."""

    def __init__(self, limit: int = LOGIN_TRIES_PER_CLIENT,
                 window_s: float = LOGIN_TRY_WINDOW_S):
        self.limit = int(limit)
        self.window_s = float(window_s)
        self._tries: dict = {}
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> list:
        cut = now - self.window_s
        live = [t for t in self._tries.get(key, ()) if t > cut]
        if live:
            self._tries[key] = live
        else:
            self._tries.pop(key, None)
        return live

    def wait_s(self, key: str) -> float:
        now = time.monotonic()
        with self._lock:
            live = self._live(key, now)
            if len(live) < self.limit:
                return 0.0
            return max(0.0, live[0] + self.window_s - now)

    def take(self, key: str) -> float:
        """Reserve one try ATOMICALLY: 0 = a slot was taken (charge it now,
        ``refund`` it if the code was right), else the seconds to wait.
        Checking with ``wait_s`` and charging with ``wrong`` after the redeem
        let N concurrent wrong codes from ONE client all pass the check and
        spend the shared burn (non-author read 2026-10-15, N2)."""
        now = time.monotonic()
        with self._lock:
            live = self._live(key, now)
            if len(live) >= self.limit:
                return max(0.0, live[0] + self.window_s - now)
            live.append(now)
            self._tries[key] = live
            if len(self._tries) > 1024:      # a LAN cannot have this many clients
                self._tries = {key: live}
            return 0.0

    def refund(self, key: str) -> None:
        """Give back the newest reserved try (the code was right, or the store
        could not judge it — neither is a wrong guess)."""
        with self._lock:
            live = self._tries.get(key)
            if live:
                live.pop()
                if not live:
                    self._tries.pop(key, None)

    def wrong(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            live = self._live(key, now)
            live.append(now)
            self._tries[key] = live
            if len(self._tries) > 1024:      # a LAN cannot have this many clients
                self._tries.clear()


class RefusalLog:
    """Count every refusal (the witness on /api/status); log a client at most
    once a minute so a scripted hammer cannot flood the journal."""

    def __init__(self, every_s: float = 60.0):
        self.count = 0
        self._last: dict = {}
        self._every = every_s
        self._lock = threading.Lock()

    def note(self, client: str, method: str, path: str, why: str) -> Tuple[int, bool]:
        with self._lock:
            self.count += 1
            now = time.monotonic()
            logged = now - self._last.get(client, -1e9) >= self._every
            if logged:
                self._last[client] = now
                if len(self._last) > 1024:
                    self._last.clear()
        if logged:
            logger.warning("Web write refused (%s): %s %s from %s", why, method, path, client)
        return self.count, logged
