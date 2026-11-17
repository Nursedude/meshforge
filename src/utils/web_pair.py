"""Pair a browser from the TUI — run the ONE minting CLI as the map's user.

Why not mint in-process: the TUI usually runs under sudo, and a code minted as
root leaves ``web_sessions.json`` + its ``.lock`` root-owned 0600, which the
map (``User=`` the operator) then cannot open — every browser locked out. The
CLI already refuses root for exactly that reason (``scripts/meshforge_web.py``).

So: read the map unit's ``LoadState`` + ``User=`` + ``ActiveState`` +
``MainPID`` from systemd now (never guessed), check the RUNNING process's uid
in /proc against ``User=``, run the CLI as that user with the store owner
pinned in its env (``sudo -n``: a password prompt would hang the TUI), then
re-read the MAP's store and call it success only if that store holds THIS
code's hash and its files are the map user's own 0600 (the consumer's end
state, not a proxy). Any step that cannot be observed refuses with its reason
and a ``remedy`` from the closed REMEDIES table — the one that can fix THAT
refusal (the shell command only where it can work). Pure over its seams
(``run``, ``euid``, ``me``, ``store_path``, ``clock``, ``proc_uid``).
"""
from __future__ import annotations

import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional, Tuple

from utils import web_auth
from utils.common import CONFIG_DIR
from utils.paths import _resolve_home_for_user

MAP_UNIT = "meshforge-map"
# The no-shell door. The 401, the login page and the TUI all render these, so
# the path is BUILT from the three strings the TUI shows (hfm #5). Lives here,
# not in web_auth.py: that file is byte-locked to MeshAnchor (parity_check).
PAIR_TUI_SECTION = "About"          # main.py: ("About", self._about_menu) — test-pinned
PAIR_TUI_LABEL = "Web Login"        # the About row (handlers/web_client.py)
PAIR_TUI_ACTION = "Pair a browser"  # the row inside Web Login
PAIR_TUI_PATH = f"{PAIR_TUI_SECTION} > {PAIR_TUI_LABEL} > {PAIR_TUI_ACTION}"
SHOW_TIMEOUT_S = 5
PAIR_TIMEOUT_S = 20      # the TUI's own wait
# The CLI's bound lives INSIDE the sudo argv (`timeout -k`): killing sudo never
# reaches the child behind sudo's pty, so an outer kill alone left a CLI that
# could mint a code nobody was shown (review r2-P1). Inner bound + grace < outer.
CLI_TIMEOUT_S = 15
CLI_KILL_GRACE_S = 2
# Every child is decoded leniently: a stray non-UTF-8 byte in sudo, coreutils
# or a traceback must never turn a refusal into a UnicodeDecodeError (r4-3).
_TEXT = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
# ActiveState is a CLOSED vocabulary read through ONE table (hfm #7, r4-4);
# anything not listed is "not running", never silently "running".
_RUNNING_STATES = frozenset({"active", "reloading"})
_STARTING_STATES = frozenset({"activating"})
# Shape only — passwd is the authority. Rejects what argv could misread (a
# leading '-', '/', whitespace); admits callsign-style and dotted names (r2-F2).
_USER_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.$-]{0,31}$")
_UID_RE = re.compile(r"^[0-9]{1,10}$")
_CODE_RE = re.compile(
    r"^Pairing code:\s+([%s]{4}-[%s]{4})\s*$" % ((web_auth.CODE_ALPHABET,) * 2), re.M)


@dataclass(frozen=True)
class PairResult:
    ok: bool
    code: Optional[str]
    message: str
    remedy: str = "shell"     # a key of REMEDIES: what can actually fix THIS outcome
    #: the pending code's expiry as the MAP's store holds it (epoch s), read
    #: back after the mint; None when it could not be read (LOGIN-UX 2)
    expires: Optional[float] = None


def expiry_hhmm(expires: Optional[float], now: Optional[float] = None) -> Optional[str]:
    """Box-local HH:MM for a pending code's expiry, or None when the stamp is
    not one a real mint could have written (missing, past, beyond now + TTL,
    or not renderable). ONE judge for the TUI box and the /login 429, so the
    two never disagree about what a real expiry is."""
    if expires is None:
        return None
    now = time.time() if now is None else now
    try:
        if not (now < float(expires) <= now + web_auth.PAIRING_TTL_S):
            return None
        return time.strftime("%H:%M", time.localtime(float(expires)))
    except (OverflowError, OSError, ValueError, TypeError):
        return None


class MapUnit(NamedTuple):
    """One read-only `systemctl show` of the map, judged."""
    user: Optional[str]       # None whenever why is set
    why: str
    active: Optional[str]
    remedy: str               # a key of REMEDIES
    main_pid: int
    note: str = ""            # said on success (e.g. the running uid was unconfirmable)


# ── remedies: ONE closed table (review r6-1) ─────────────────────────────────
# The fallback offered must be one that can work for THIS refusal. Before r7
# every refusal led with "run pair at a shell" — which with no unit mints a
# code no phone can use, and with a root-run unit lands in a store the map
# never reads. Option B (operator 2026-11-16): no unit -> no web login; the
# TUI's built-in map binds 127.0.0.1 and trusts loopback only (option A tried
# minting into it; review r5 ran it: unreachable from a phone + a root-owned
# store in the operator's home).
# Rules for every remedy (r8, a subtraction):
#  * (all but "shell", which only the failure box shows) at most TWO lines of
#    at most 66 columns, so the status block fits the
#    real 80x24 menu with the TUI's status bar (r8-4, test-pinned by ROWS);
#  * no absolute paths — the checkout can live anywhere (r8-1);
#  * never a command about files it has not inspected. Round 8 printed
#    `sudo chown` for the session files: it followed a planted symlink
#    (chmod 600 on a root-owned target) and looped on folder faults. The
#    permission remedy now SAYS what is wrong and stops there.
REMEDIES = {
    "install": ("Install the map service (scripts/install_noc.sh in MeshForge).\n"
                "The TUI's built-in map is local-only: a phone cannot log in."),
    "unmask": ("meshforge-map is masked (off on purpose). To use web login:\n"
               "  sudo systemctl unmask meshforge-map"),
    "unit": ("meshforge-map is installed but systemd cannot load it. Check:\n"
             "  systemctl status meshforge-map"),
    "migrate": ("From the MeshForge folder, move the map to the operator account:\n"
                "  sudo bash scripts/migrate_map_service_to_user.sh"),
    "restart": ("Restart meshforge-map so it runs as its configured user:\n"
                "  sudo systemctl restart meshforge-map"),
    "permission": ("The map's session files (or their folder) have the wrong owner\n"
                   "or permissions (see Why for which). Fix that, then pair again."),
    "shell": ("At a shell on this box AS THE OPERATOR:\n"
              f"  {web_auth.PAIR_COMMAND}\n"
              "then open {url} and type the code."),
}


def remedy_text(kind: str, url: str) -> str:
    """The operator-facing fix for a remedy kind. Unknown kind -> KeyError (hfm #7)."""
    return REMEDIES[kind].replace("{url}", url)


def proc_uid(pid: int) -> Optional[int]:
    """Real uid of a live process from /proc/<pid>/status, or None."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Uid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def my_name() -> str:
    """This process's user name; the bare uid when passwd has no row (r4-5)."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def map_running_note(active: Optional[str]) -> str:
    """'' when the map serves now; otherwise what the operator should know."""
    if active in _RUNNING_STATES:
        return ""
    if active in _STARTING_STATES:
        return (f"the map is starting on this box (ActiveState={active}); "
                "the code will work once it is up")
    return (f"the map is not running on this box (ActiveState={active or 'unknown'}); "
            "the code is in its store and will work once the map is started")


def _signame(n: int) -> str:
    try:
        return signal.Signals(n).name
    except ValueError:
        return f"signal {n}"


def classify_exit(rc: int, elapsed: float, stderr: str) -> Tuple[str, str]:
    """ONE reading of how the pairing CLI ended -> (kind, operator message).

    kind is "ok" | "timeout" | "signal" | "refused". The table that defines it
    is tests/test_web_pair.py EXIT_TABLE (rc x elapsed x stderr): the
    exit-status kind came back three review rounds running (137, then -9,
    then fast signal deaths), so it is decided here, from MEASURED elapsed
    time, and nowhere else.
      * timeout — coreutils `timeout` fired: rc 124 (TERM worked) or a KILL
        death (rc -9 seen by subprocess, 137 via a shell), AND the call took at
        least the bound. A fast 124/137/-9 is NOT a timeout.
      * signal  — any other death by signal (rc < 0, or 128+n).
      * refused — any other non-zero exit.
    Every non-ok message carries stderr's LAST line (where a traceback keeps
    its exception) and never stdout, which may hold a code.
    """
    if rc == 0:
        return "ok", ""
    tail = last_line(stderr)[:300]
    said = f": {tail}" if tail else ""
    bound_fired = elapsed >= CLI_TIMEOUT_S
    if bound_fired and (rc in (124, -signal.SIGKILL, 128 + signal.SIGKILL, -signal.SIGTERM)):
        return "timeout", (f"the pairing command was stopped after {CLI_TIMEOUT_S}s (its time "
                           f"limit; rc {rc}){said}; any code it made was never shown — pair again")
    sig = -rc if rc < 0 else (rc - 128 if 128 < rc <= 128 + 64 else None)
    if sig:
        return "signal", (f"the pairing command died by {_signame(sig)} after {elapsed:.1f}s"
                          f"{said}; any code it made was never shown — pair again")
    return "refused", f"pairing refused (rc {rc}){said or ': no error text on stderr'}"


def map_unit(run: Callable = subprocess.run, timeout: float = SHOW_TIMEOUT_S,
             proc_uid: Optional[Callable[[int], Optional[int]]] = None) -> MapUnit:
    """The map unit, judged from ONE read-only ``systemctl show``.

    ``systemctl show`` on a MISSING unit exits 0 with ``User=`` empty, so
    LoadState is read beside it: absent is "not installed", never "root".
    ActiveState rides along so a stopped map is SAID. MainPID lets the
    CONSUMER be checked, not its config (r6-P): ``User=`` can be changed and
    daemon-reloaded while the running process still serves the old user's
    store — then a code minted for the new user would be refused.
    """
    argv = ["systemctl", "show", MAP_UNIT,
            "-p", "LoadState", "-p", "User", "-p", "ActiveState", "-p", "MainPID"]
    try:
        p = run(argv, timeout=timeout, **_TEXT)
    except subprocess.TimeoutExpired:
        return MapUnit(None, f"systemctl show {MAP_UNIT} timed out after {timeout}s", None, "shell", 0)
    except OSError as e:
        return MapUnit(None, f"could not run systemctl ({e.__class__.__name__})", None, "shell", 0)
    if p.returncode != 0:
        err = (p.stderr or "").strip()[:200]
        return MapUnit(None, f"systemctl show {MAP_UNIT} failed (rc {p.returncode}): {err}",
                       None, "shell", 0)
    props = {}
    for line in (p.stdout or "").splitlines():
        k, sep, v = line.partition("=")
        if sep:
            props[k.strip()] = v.strip()
    active = props.get("ActiveState")
    try:
        pid = int(props.get("MainPID") or 0)
    except ValueError:
        pid = 0
    user, why, remedy = _user_from(props)
    if user is None or active not in _RUNNING_STATES:
        return MapUnit(user, why, active, remedy, pid)
    # Resolve the seam at CALL time: a default-bound function is a trap for the
    # next test author who patches web_pair.proc_uid (r7 plausible).
    reader = proc_uid if proc_uid is not None else globals()["proc_uid"]
    running = reader(pid) if pid > 0 else None
    want = pwd.getpwnam(user).pw_uid          # _user_from proved the row exists
    if running is None:
        # A running map with no readable process (MainPID=0, raced away, no
        # /proc): the check is UNKNOWN and said so, never skipped silently (r7-3).
        return MapUnit(user, "", active, "shell", pid,
                       note=(f"could not confirm the running map's uid (MainPID={pid}); "
                             "the code was still verified in its store"))
    if running != want:
        return MapUnit(None, (f"the running map (pid {pid}) runs as uid {running}, but its "
                              f"unit now says User={user} (uid {want}) — a code for {user} "
                              "would land in a store that process does not read"),
                       active, "restart", pid)
    return MapUnit(user, "", active, "shell", pid)


def map_unit_user(run: Callable = subprocess.run,
                  timeout: float = SHOW_TIMEOUT_S) -> Tuple[Optional[str], str]:
    """(user, "") — the map unit's ``User=`` — or (None, why)."""
    m = map_unit(run=run, timeout=timeout)
    return m.user, m.why


def _user_from(props: dict) -> Tuple[Optional[str], str, str]:
    """(user, why, remedy) — remedy is the REMEDIES key that can fix it."""
    load = props.get("LoadState")
    if load is None:
        return None, f"systemctl show {MAP_UNIT} printed no LoadState — cannot tell", "shell"
    if load != "loaded":
        # Install ONLY for a missing unit (r5-3, r6-1); a masked unit is off on
        # purpose and a bad-setting one is installed but broken (r7-4).
        kind = "install" if load == "not-found" else ("unmask" if load == "masked" else "unit")
        return None, (f"the {MAP_UNIT} unit is not installed or not loadable here "
                      f"(LoadState={load})"), kind
    user = props.get("User", "")
    if user in ("", "root", "0"):
        return None, (f"the {MAP_UNIT} unit declares no non-root User= (it runs as "
                      "root), so no operator store owner can be named"), "migrate"
    if _UID_RE.match(user):
        try:
            name = pwd.getpwuid(int(user)).pw_name
        except (KeyError, OverflowError, ValueError):
            return None, f"the {MAP_UNIT} unit's User={user} (a uid) is not in passwd", "shell"
        if name == "root":
            return None, f"the {MAP_UNIT} unit's User={user} is root", "migrate"
        return name, "", "shell"
    if not _USER_RE.match(user):
        return None, f"the {MAP_UNIT} unit's User= {user!r} is not a valid user name", "shell"
    try:
        pwd.getpwnam(user)
    except KeyError:
        return None, f"the {MAP_UNIT} unit's User={user} is not in passwd", "shell"
    return user, "", "shell"


def build_pair_argv(map_user: str, euid: int, me: str,
                    python: str = sys.executable) -> Tuple[Optional[List[str]], str]:
    """argv that runs the pairing CLI AS ``map_user`` — or (None, why).

    CONFIG_DIR resolves from SUDO_USER then LOGNAME, so BOTH branches pin them
    to the store's owner: under ``sudo -u`` sudo sets SUDO_USER=root, and a
    TUI run as the map user may still carry another user's SUDO_USER.
    """
    pinned = ["timeout", "-k", str(CLI_KILL_GRACE_S), str(CLI_TIMEOUT_S),
              "env", f"SUDO_USER={map_user}", f"LOGNAME={map_user}",
              python, str(web_auth.WEB_CLI), "pair"]
    if euid == 0:
        return ["sudo", "-n", "-u", map_user, "-H"] + pinned, ""
    if me == map_user:
        return pinned, ""
    return None, (f"the map runs as {map_user} but this TUI runs as {me}; "
                  f"pair as {map_user}: {web_auth.PAIR_COMMAND}")


def store_access_problem(map_user: str, euid: Optional[int] = None,
                         me: Optional[str] = None) -> Optional[str]:
    """Why THIS process cannot read the map's 0600 store, or None (r2-F3):
    a non-root TUI user other than the map's is told to use sudo, never to
    'fix' a healthy file."""
    euid = os.geteuid() if euid is None else euid
    if me is None:
        me = my_name()
    if euid == 0 or me == map_user:
        return None
    return (f"this TUI runs as {me}; the map's session store belongs to {map_user} "
            "(0600) — open the TUI with sudo, or as that user, to read or pair")


def last_line(text: str) -> str:
    """The last non-empty line — where a traceback keeps its exception (r2-F1)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def parse_code(stdout: str) -> Optional[str]:
    m = _CODE_RE.search(stdout or "")
    return m.group(1) if m else None


def store_path_for(user: str) -> Path:
    """The store the MAP reads: its user's home, same layout as CONFIG_DIR."""
    return _resolve_home_for_user(user) / ".config" / CONFIG_DIR.name / "web_sessions.json"


def check_store(map_user: str, store: Path, code: str) -> Tuple[bool, str]:
    """Does the MAP's store hold THIS code, in files the map user owns 0600?"""
    ok, why, _kind = _check_store(map_user, store, code)
    return ok, why


def _read_pairing(store: Path) -> dict:
    """The store's pending pairing row as raw JSON ({} when none). Raw on
    purpose: never ``SessionStore._read``, which renames a corrupt store
    aside. Raises OSError / ValueError / AttributeError. The ONE reader for
    both the hash check and the expiry read-back."""
    data = json.loads(Path(store).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a session store")
    p = data.get("pairing") or {}
    if not isinstance(p, dict):
        raise ValueError("malformed pairing row")
    return p


def _stored_expires(store: Path) -> Optional[float]:
    try:
        return float(_read_pairing(store)["expires"])
    except (OSError, ValueError, AttributeError, KeyError, TypeError, OverflowError):
        return None


def _check_store(map_user: str, store: Path, code: str) -> Tuple[bool, str, str]:
    """(ok, why, remedy kind). Reads the raw file (never ``SessionStore._read``,
    which renames a corrupt store aside) and lstat()s — a symlink names whoever
    planted it. Owner and mode faults are "permission" (r7-1); the rest "shell"."""
    try:
        uid = pwd.getpwnam(map_user).pw_uid
    except KeyError:
        return False, f"{map_user} is not in passwd", "shell"
    store = Path(store)
    for label, p in (("store", store), ("lock", Path(str(store) + ".lock"))):
        try:
            st = os.lstat(p)
        except OSError as e:
            return False, f"the session {label} {p} is unreadable ({e.__class__.__name__})", "shell"
        if not stat.S_ISREG(st.st_mode):
            return False, f"the session {label} {p} is not a regular file", "shell"
        if st.st_uid != uid:
            try:
                owner = pwd.getpwuid(st.st_uid).pw_name
            except KeyError:
                owner = str(st.st_uid)
            return False, (f"the session {label} {p} is owned by {owner}, not "
                           f"{map_user} — the map cannot open it"), "permission"
        # Only the STORE (session + pairing hashes) must be 0600. The lock is
        # an empty flock target: the map works with it at 0644, and refusing
        # it was stricter than the consumer and looped (r9-1).
        if label == "store" and stat.S_IMODE(st.st_mode) & 0o077:
            return False, (f"the session {label} {p} has mode "
                           f"{oct(stat.S_IMODE(st.st_mode))}, not 0600"), "permission"
    try:
        held = _read_pairing(store).get("h")
    except (OSError, ValueError, AttributeError) as e:
        return False, (f"the session store {store} could not be read back "
                       f"({e.__class__.__name__})"), "shell"
    if held != web_auth._h(web_auth._normalize_code(code)):
        return False, (f"the map's session store {store} does not hold this code — "
                       "it was minted somewhere the map does not read"), "shell"
    return True, "", "shell"


def pair_browser(run: Callable = subprocess.run, euid: Optional[int] = None,
                 me: Optional[str] = None, store_path: Optional[Path] = None,
                 clock: Callable[[], float] = time.monotonic,
                 proc_uid: Optional[Callable[[int], Optional[int]]] = None) -> PairResult:
    """Mint a one-time code via the CLI as the map's user, then verify it landed."""
    euid = os.geteuid() if euid is None else euid
    if me is None:
        me = my_name()
    m = map_unit(run=run, proc_uid=proc_uid)
    if m.user is None:
        # nothing minted, anywhere; the remedy is the one that can fix THIS
        return PairResult(False, None, m.why, remedy=m.remedy)
    user, active = m.user, m.active
    argv, why = build_pair_argv(user, euid, me)
    if argv is None:
        return PairResult(False, None, why)
    t0 = clock()
    try:
        p = run(argv, timeout=PAIR_TIMEOUT_S,
                stdin=subprocess.DEVNULL,          # never the TUI's tty (r2-P2)
                **_TEXT)
    except subprocess.TimeoutExpired:
        # The inner `timeout -k` normally fires first. Reaching here means the
        # wait expired around it (e.g. sudo stalled BEFORE exec, so the inner
        # clock started late): a code could still be minted after this answer.
        return PairResult(False, None, (
            f"the pairing command did not answer within {PAIR_TIMEOUT_S}s; any code it "
            "made was never shown — pair again"))
    except OSError as e:
        return PairResult(False, None, f"could not run the pairing command ({e.__class__.__name__})")
    store = Path(store_path or store_path_for(user))
    kind, msg = classify_exit(p.returncode, clock() - t0, p.stderr)
    if kind != "ok":
        # The CLI could not open the map's own files (or their folder): the
        # shell command would fail identically (r7-1). The remedy SAYS so and
        # prescribes no command (r8: a chown it printed followed symlinks).
        remedy = "permission" if (kind == "refused" and "PermissionError" in (p.stderr or "")) else "shell"
        return PairResult(False, None, msg, remedy=remedy)
    code = parse_code(p.stdout)
    if code is None:
        return PairResult(False, None, "the pairing command printed no code: "
                          + (p.stdout or "").strip()[:200])
    ok, why, remedy = _check_store(user, store, code)
    if not ok:
        return PairResult(False, code, f"a code was minted but the map may not accept it: {why}",
                          remedy=remedy)
    return PairResult(True, code, "; ".join(n for n in (map_running_note(active), m.note) if n),
                      expires=_stored_expires(store))
