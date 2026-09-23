"""The success-truth SWEEP: every top-level TUI action, every external dead.

`test_all_tags_dispatch` (test_all_handlers_protocol.py) proves that all
registered actions ROUTE. This proves what their FIRST SCREEN says when
there is nothing to say it about: sockets refused, no subprocess, no tool on
PATH, the operator's home replaced by an empty one, and box state (/etc/reticulum,
/etc/meshtasticd, systemd units, /proc/net, device nodes) absent. A screen rendered under
those conditions must carry a word of uncertainty — or the action must be
declared LOCAL_ONLY in `launcher_tui/action_truth.py` with a why.

SCOPE, stated exactly: TWO dialog levels. Level one — every action's first
screen (`FakeDialog.menu` returns None, so an action whose first screen is
a menu is `navigation`). Level two (`test_level_two_items_tell_the_truth`,
2026-09-22) — every item of that first menu, one dispatch each, judged on
what the ITEM rendered: 480 items. Level two gates only the MEASURED legs
(crash, hang, status rows, home/box-state witnesses); its text verdicts are
dumped, not gated (see that test's docstring). Level three — a menu inside
an item — is `navigation`, unwalked.

Level two runs items for real, so the fixture also refuses os.kill (a
"Stop NomadNet" item finds the operator's live client through /proc),
name resolution (getaddrinfo reached real DNS) and webbrowser; Path.glob's
early-bound scandir is patched (it read this box's /dev/ttyACM0). A live
view is stopped with Ctrl+C after SLEEP_BUDGET sleeps or CTRL_C_AFTER_S,
as an operator would, and the cut is recorded (kind `ctrl_c`); the
suite's pytest-timeout alarm is saved and handed back around every item.
The real home is REFUSED, not only witnessed (a singleton an earlier test
built wrote the operator's mesh_alerts.json in suite order); first-party
singletons holding a real-home path are dropped for the test, and every
first-party singleton a test CREATED is stop()/close()d at teardown.
NOT contained (stated): a thread started by an object that is not a
module-global singleton; stderr; os.system / ctypes / a symlink out of the
fake home (no carriers in src today); items run in menu order on one fake
home, so an earlier item's writes are visible to a later one.

What it reads: every FakeDialog call INCLUDING infobox (an infobox can
claim "Connected"), AND everything the handler printed to stdout (about a
sixth of the actions draw with print() + input(); `input` is patched so
they render instead of dying on pytest's captured stdin — the first cut
counted that death's "Error" dialog as honesty, 18 times).

What it catches: the EAS Dashboard class — "Weather: no alerts" from a
fetch that never happened — and the space-weather "Quiet / Fair" defaults
this sweep found on its first run.

What it cannot catch (stated, not hidden): a screen that says one honest
word and three false ones passes; a handler that renders NOTHING is
`silent` and passes (ambiguous between "asked nothing" and "swallowed" —
the swallow class is MF027 / hfm #9's job). A `crashed` handler — an
exception escaped it into safe_call, MEASURED by wrapping safe_call — fails
unless it is in the frozen KNOWN_CRASHED baseline; a known crasher's screen
is not judged, so a lie before its crash is unseen.
Claims that a bad thing is ABSENT ("Errors: 0", "0 failures", "nothing
failed", "No drift detected") are stripped before the vocabulary is
applied (after ANSI colour codes are removed).

⚠️ THE TEXT CLASSIFIER IS POROUS, MEASURED — do not widen it to chase
this. A non-author Fable review (2026-09-22) planted 22 NEW lie shapes
after the scrub was widened once: 18 read `honest` (`Unreachable: 0 of
9`, `Timeouts: 0`, `Completed without errors`, `Failed nodes: []`,
`Never failed` …) — the vocabulary's own words recur as zero-count
claims. A regex over prose cannot converge on "is this sentence a
claim"; each widening buys a few shapes and costs over-scrubs of real
uncertainty. The decision (operator, same day): stop widening, state
the porosity here, and trust the MEASURED legs instead — the crash
witness at safe_call, status-shaped menu rows, dead box state and the
real-home witness. A `honest` verdict means "carries an uncertainty
word", nothing stronger. The vocabulary is a FLOOR: "disabled" / "n/a" still pass a claim
beside them; a menu ROW starting OK / PASS / ✓ is a claim (false-ok with
nothing observed), but menu header, yesno and inputbox text are still
`navigation` (second non-author review 2026-09-22).

The operator's real home is proven untouched, not assumed: an audit hook
fails any action that opens a path under it (the first cut patched only
the resolver and still read ~/.config via an import-time CONFIG_DIR).

Run with MF_TRUTH_SWEEP_DUMP=<path> to write every action's verdict and
rendered text as JSON — that is how the allowlists get their entries.
"""
from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
for p in (str(_SRC), str(_SRC / "launcher_tui"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from handler_registry import HandlerRegistry  # noqa: E402
from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from launcher_tui.action_truth import (  # noqa: E402
    KNOWN_CRASHED, KNOWN_CRASHED_L2, KNOWN_FALSE_OK, LOCAL_ONLY)
import utils.paths as _paths  # noqa: E402

# Words that make a screen honest when every external is dead. Broad on
# purpose: the sweep is a floor. Anchored on word starts so "error" also
# matches "errors" and "unreachable" matches "Unreachable:".
HONEST = re.compile(
    r"unknown|unreach|not available|unavailable|could ?n.t|cannot|can.t|"
    r"fail|error|not installed|not found|not running|not connected|"
    r"not reachable|not detected|not configured|not supported|not present|"
    r"refus|missing|offline|time[d ]?out|disabled|absent|no data|"
    r"not (?:yet )?(?:started|active|loaded|set)|n/a\b|unable|"
    r"no (?:\w+ ){0,3}(?:found|detected|available|configured|installed)|"
    r"needs admin|requires admin|admin mode|"  # a refusal is honest
    r"not verified|unverified|"  # a decision that says it is not yet proven
    r"❌|✗|⚠",
    re.IGNORECASE,
)

# Claims that a BAD THING is absent — "Errors: 0", "0 failures", "Errors:
# none", "nothing failed", "No drift detected", "no issues found" — are
# CLAIMS OF HEALTH, not uncertainty, but each carries an HONEST word
# ("error", "fail", "no … detected"). Stripped before HONEST is applied.
# Second non-author review 2026-09-22: 13 planted shapes of this kind all
# read "honest", including the `rns/drift` "No drift detected" the
# level-two walk exists to catch.
_BAD = (r"(?:errors?|fail(?:ures?|ed|s)?|missing|warnings?|problems?|issues?|"
        r"drift|alerts?|conflicts?|faults?|anomal(?:y|ies)|mismatch(?:es)?|"
        r"collisions?|leaks?|stalls?|wedges?)")
ZERO_COUNT = re.compile(
    rf"\b{_BAD}\s*[:=]\s*(?:0|none|nil)\b"          # Errors: 0 / Errors: none
    rf"|\b(?:0|zero)\s+{_BAD}"                        # 0 failures
    rf"|\bnothing\s+(?:failed|missing|wrong|broken)"  # nothing failed
    # "No drift detected" is a claim when the bad noun is followed by a
    # RESULT VERB (whatever comes after — "… today", "… (gateway vs rnsd)")
    # or by nothing word-like (end, punctuation, a dash, ✓). It is NOT a
    # claim when a noun follows ("No alert feed available", "No drift
    # baseline found" — adjectival, real uncertainty). History: unanchored
    # it over-scrubbed 6 shapes (Fable MED-3); end-anchored it let 8 claim
    # shapes back through, incl. the rns/drift target (Fable rev2 HIGH-2).
    rf"|\bno\s+(?:\w+\s+){{0,2}}{_BAD}\b(?:\s+(?:detected|found|reported|seen|"
    rf"present|active|pending|observed|recorded)\b|(?=\s*(?:[^\w\s]|$)))",
    re.IGNORECASE | re.MULTILINE,
)

# ANSI colour codes end in a word character ("\x1b[0;32m"), so "\bNo drift"
# had no boundary: the REAL rns/drift screen printed a green "No drift
# detected" that the scrub never saw (Fable review HIGH-1). Stripped first.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# A handler CRASHED when an exception escaped it into TUIContext.safe_call —
# safe_call told the truth, the handler did not (review finding 1). That is
# MEASURED, not read from text: the fixture wraps safe_call and records every
# exception that reaches it (`_CRASHES`). The first cut matched strings —
# its own "[truth-sweep] no subprocess" marker and a copy of safe_call's
# "Details logged to:" — so `system/shell`, which catches the failure and
# prints "Shell error: …" itself, read as crashed (finding #5, 2026-09-22).
_CRASHES: list = []
_SIGNALS: list = []
_LAST: dict = {}

# Dialog kinds that render only navigation / prompts, never a claim.
# infobox is NOT here: "Connected via USB: /dev/ttyACM0" was an infobox.
NAVIGATION = {"menu", "checklist", "inputbox", "yesno", "editbox"}

# A menu ITEM can be a verdict: Config Doctor's `system/details` rendered
# "OK     rnsd is not running; config drift check skipped" and "OK  all
# enabled RNS interfaces resolve" as menu rows with every external dead,
# and passed as `navigation` (second non-author review 2026-09-22, finding
# 4). A label that STARTS with a positive status token is a health claim —
# and with nothing to observe, a positive status is false-ok whatever the
# row's body says ("not running" beside OK is the contradiction, not an
# excuse). Case-sensitive: "Active Connections" is a label, "OK" is a status.
STATUS_OK = re.compile(r"^\s*(?:\[\s*(?:OK|PASS)\s*\]|OK|PASS(?:ED)?|✓|✔)(?=[\s:\]]|$)")


def _status_claims(calls) -> list:
    """Menu/checklist item labels that assert a positive status. Pure."""
    out = []
    for kind, args, _kw in calls:
        if kind not in ("menu", "checklist") or len(args) < 3:
            continue
        for choice in args[2] or ():
            label = str(choice[1]) if len(choice) > 1 else ""
            if STATUS_OK.match(label):
                out.append(label)
    return out


# --- real-home witness -------------------------------------------------
# The first cut asserted the RESOLVER was patched and called that proof;
# the files still opened under the real home through import-time constants.
# So the proof is now the thing itself: a Python audit hook records every
# filesystem event under the real home while a sweep test runs, and the
# test fails on any. (A hook cannot be removed; it is armed only inside
# the dead_externals fixture.)
_REAL_HOME = Path(_paths.get_real_user_home()).resolve()
_REPO = Path(__file__).resolve().parents[1]
_HOME_TOUCHES: list = []
_AUDIT = {"armed": False}
_FS_EVENTS = {"open", "os.listdir", "os.scandir", "os.mkdir", "os.rename",
              "os.remove", "os.rmdir", "os.truncate", "sqlite3.connect",
              "shutil.rmtree"}
# Interpreter/library reads under the home (a user-site install, the repo
# checked out under ~) are the harness, not the handler.
_EXEMPT = tuple(str(p) for p in {_REPO, *[Path(p).resolve() for p in sys.path
                                          if p and Path(p).is_absolute()]}
                if str(p).startswith(str(_REAL_HOME)))


def _fs_audit(event, args):
    if not _AUDIT["armed"] or event not in _FS_EVENTS or not args:
        return
    p = args[0]
    if isinstance(p, int) or p is None:
        return
    try:
        p = os.path.abspath(os.fsdecode(p))
    except (TypeError, ValueError):
        return
    # The home ITSELF counts: `os.listdir(home)` rendered 330 real entries
    # unwitnessed when only paths UNDER it matched (Fable review, finding 7).
    if (len(_REAL_HOME.parts) > 2
            and (p == str(_REAL_HOME) or p.startswith(str(_REAL_HOME) + os.sep))
            and not p.startswith(_EXEMPT) and not p.endswith((".py", ".pyc", ".so"))):
        _HOME_TOUCHES.append((event, p))
        # REFUSE, not just record: in the full suite's order, level two
        # renamed a file over the operator's real ~/.config/meshforge/
        # mesh_alerts.json (enabled_types: []) through a singleton an EARLIER
        # test had built with the real path — witnessed only after the write
        # landed (2026-09-22). Raising from the hook aborts the call.
        raise PermissionError(13, "[truth-sweep] the operator's real home is refused", p)
    # The os-layer patches raise BEFORE the real call, so any audited event
    # on box state here is a read that went around them. A LISTING of /dev
    # is box state too (the patches redirect it to an empty dir, so a real
    # /dev listing here also went around them).
    if _box_path(p) is not None or (
            event in ("os.listdir", "os.scandir") and p in _EMPTY_LISTING):
        _BOX_STATE_TOUCHES.append((event, p))


sys.addaudithook(_fs_audit)


# --- box state reads as ABSENT -------------------------------------------
# Sockets and subprocesses being dead did not make the BOX dead: the second
# non-author review saw the sweep read /proc/net/unix (this box's live rnsd
# socket) ×7, /etc/reticulum/config ×11, /etc/meshtasticd, systemd unit
# files — and attempt mkdir under /etc/meshtasticd — so `system/details`
# rendered "OK rnsd rpc_key pinned (<this box's key>)" and every verdict
# was a statement about whichever box ran the suite. Most sites are inline
# literals (Path('/etc/systemd/system/rnsd.service')), so the seam is the
# os layer: these paths are ABSENT, mkdir under them is refused, and /dev
# lists empty. Anything that still reaches them is caught by the audit
# witness below (e.g. an interpreter whose pathlib binds os.stat early).
BOX_STATE_DIRS = (
    "/etc/reticulum", "/etc/meshtasticd", "/etc/systemd", "/lib/systemd",
    "/usr/lib/systemd", "/proc/net", "/var/lib/meshforge", "/run/meshforge",
    "/dev/serial", "/sys/class", "/sys/bus",
)
BOX_STATE_NAMES = ("/dev/tty", "/dev/spidev", "/dev/gpiochip", "/dev/i2c")
_EMPTY_LISTING = ("/dev",)
_BOX_STATE_TOUCHES: list = []


def _box_path(p):
    """The absolute path string if `p` is box state, else None."""
    if isinstance(p, int) or p is None:
        return None
    try:
        s = os.fsdecode(p)
        # sqlite URI form (`connect_tuned(..., uri=True)`): "file:<path>?mode=ro"
        # was abspath'd cwd-relative and READ the box DB unwitnessed (Fable
        # review MED-5b). Take the path part.
        if s.startswith("file:"):
            s = s[5:].split("?", 1)[0].split("#", 1)[0]
            if s.startswith("//"):
                s = "/" + s[2:].split("/", 1)[-1]
        s = os.path.abspath(s)
    except (TypeError, ValueError):
        return None
    if any(s == d or s.startswith(d + "/") for d in BOX_STATE_DIRS) \
            or s.startswith(BOX_STATE_NAMES):
        return s
    return None


def _kill_box_state(monkeypatch, empty_dir: Path):
    import io

    def absent(p):
        raise FileNotFoundError(2, "[truth-sweep] box state is absent", p)

    def reads(real):
        def wrapper(path, *a, **k):
            s = _box_path(path)
            if s is not None:
                absent(s)
            return real(path, *a, **k)
        return wrapper

    def lists(real):
        def wrapper(path=".", *a, **k):
            if not isinstance(path, int) and os.path.abspath(os.fsdecode(path)) in _EMPTY_LISTING:
                return real(str(empty_dir), *a, **k)
            s = _box_path(path)
            if s is not None:
                absent(s)
            return real(path, *a, **k)
        return wrapper

    def refuses(real):
        def wrapper(path, *a, **k):
            s = _box_path(path)
            if s is not None:
                raise PermissionError(13, "[truth-sweep] box state is read-only", s)
            return real(path, *a, **k)
        return wrapper

    real_open = builtins.open
    opener = reads(real_open)
    monkeypatch.setattr(builtins, "open", opener)
    monkeypatch.setattr(io, "open", opener)
    for name in ("open", "stat", "lstat", "access", "readlink"):
        monkeypatch.setattr(os, name, reads(getattr(os, name)))
    for name in ("scandir", "listdir"):
        monkeypatch.setattr(os, name, lists(getattr(os, name)))
    for name in ("mkdir", "rename", "replace", "remove", "unlink", "rmdir", "chmod"):
        monkeypatch.setattr(os, name, refuses(getattr(os, name)))
    import sqlite3
    monkeypatch.setattr(sqlite3, "connect", reads(sqlite3.connect))
    # Python 3.12+ `Path.glob` goes through glob._StringGlobber, which binds
    # os.scandir / os.lstat at CLASS creation — the os patches above never
    # reach it, and the level-two walk rendered this box's real
    # /dev/ttyACM0 and spidev0.0 through `Path('/dev').glob(...)` (2026-09-22).
    import glob as _glob_mod
    globber = getattr(_glob_mod, "_StringGlobber", None)
    if globber is not None:
        monkeypatch.setattr(globber, "scandir", staticmethod(lists(os.scandir)))
        monkeypatch.setattr(globber, "lstat", staticmethod(reads(os.lstat)))


def _rehome(val, home: Path):
    """The fake-home equivalent of a Path/str rooted at the real home, else None."""
    if len(_REAL_HOME.parts) <= 2:
        return None
    # The repo and the interpreter's own paths are the HARNESS, even when
    # they live under the home: on GitHub the checkout is /home/runner/work/…
    # = under _REAL_HOME, and the first cut rewrote every repo-rooted global
    # (incl. __file__) into the empty fake home, so CI swept a different
    # program than this box (Fable review MED-6). Same exemption the witness
    # already used.
    if isinstance(val, Path):
        try:
            r = val.resolve()
            if str(r).startswith(_EXEMPT) or r == _REPO:
                return None
            return home / r.relative_to(_REAL_HOME)
        except (ValueError, OSError):
            return None
    if isinstance(val, str) and val.startswith(str(_REAL_HOME) + os.sep):
        if val.startswith(_EXEMPT):
            return None
        return str(home / Path(val).relative_to(_REAL_HOME))
    return None


_FIRST_PARTY_MOD: dict = {}


def _is_first_party_module(name: str) -> bool:
    """Cached: realpath per module global made every fixture setup ~0.4 s."""
    hit = _FIRST_PARTY_MOD.get(name)
    if hit is None:
        mod = sys.modules.get(name)
        if mod is None:
            return False  # not loaded yet: answer, but do not cache
        f = os.path.realpath(getattr(mod, "__file__", None) or "/")
        hit = _FIRST_PARTY_MOD[name] = f.startswith(str(_SRC.resolve()) + os.sep)
    return hit


def _is_first_party_instance(val) -> bool:
    if isinstance(val, (type, type(sys))):  # classes and modules are not singletons
        return False
    return _is_first_party_module(type(val).__module__)


def _holds_real_home(obj, depth: int = 2) -> bool:
    """True if a real-home Path/str sits in obj's attributes, `depth` levels down."""
    try:
        attrs = vars(obj)
    except TypeError:
        return False
    for v in list(attrs.values()):
        if _rehome(v, Path("/")) is not None:
            return True
        if depth > 1 and _is_first_party_instance(v) and _holds_real_home(v, depth - 1):
            return True
    return False


_STOP_FAILURES: list = []
_LAZY: dict = {}


def _lazy_singleton_names(mod_name: str) -> frozenset:
    """Globals some function in the module ASSIGNS via `global` — the lazy
    singletons (`global _x; if _x is None: _x = X()`). Read from bytecode
    (STORE_GLOBAL), cached per module."""
    hit = _LAZY.get(mod_name)
    if hit is None:
        import dis
        import types
        names = set()
        mod = sys.modules.get(mod_name)

        def scan(code):
            for ins in dis.get_instructions(code):
                if ins.opname == "STORE_GLOBAL":
                    names.add(ins.argval)
            for const in code.co_consts:
                if isinstance(const, types.CodeType):
                    scan(const)
        for val in list(vars(mod).values()) if mod else ():
            if isinstance(val, types.FunctionType) and val.__module__ == mod_name:
                scan(val.__code__)
        hit = _LAZY[mod_name] = frozenset(names)
    return hit


def _first_party_globals() -> dict:
    """{(module, attr): obj} for every first-party instance held in a
    first-party module global — the lazy singletons."""
    out = {}
    for mod_name, mod in list(sys.modules.items()):
        if not _is_first_party_module(mod_name):
            continue
        for attr, val in list(vars(mod).items()):
            if not attr.startswith("__") and _is_first_party_instance(val):
                out[(mod_name, attr)] = val
    return out


def _all_actions():
    from handlers import get_all_handlers
    ctx = make_handler_context()
    reg = HandlerRegistry(ctx)
    for cls in get_all_handlers():
        reg.register(cls())
    out = []
    for section in reg.section_names:
        for tag, _ in reg.get_menu_items(section):
            out.append((section, tag))
    return sorted(out)


ACTIONS = _all_actions()


@pytest.fixture
def dead_externals(no_network, monkeypatch, tmp_path):
    """Sockets refused (no_network), subprocess absent, nothing on PATH,
    input() answering nothing, and the REAL operator home replaced.

    `get_real_user_home()` resolves SUDO_USER / LOGNAME through pwd and
    ignores HOME (review finding 5: the first cut set HOME only and read —
    and tried to write — the operator's real ~/.config and ~/.local). So:
    SUDO_USER unset, LOGNAME set to a sentinel, and the resolver for that
    sentinel patched to the tmp dir; HOME too, for the expanduser fallback.
    """
    def _absent(*a, **k):
        raise FileNotFoundError("[truth-sweep] no subprocess: every external is dead")
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _absent)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "")

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("LOGNAME", "truthsweep")
    monkeypatch.setenv("USER", "truthsweep")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    real_resolve = _paths._resolve_home_for_user
    monkeypatch.setattr(
        _paths, "_resolve_home_for_user",
        lambda user: home if user == "truthsweep" else real_resolve(user))
    assert _paths.get_real_user_home() == home

    # Patching the resolver cannot reach a path FROZEN at import time
    # (`utils.common.CONFIG_DIR = get_real_user_home() / ...` and four
    # siblings) — the second non-author review caught the sweep reading
    # the operator's real map_settings.json / mesh_alerts.json that way.
    # Rewrite every home-rooted Path/str module global in first-party
    # modules, so a NEW frozen constant is covered by construction.
    for mod_name, mod in list(sys.modules.items()):
        # realpath: tests load src as `tests/../src/…`, which a bare prefix
        # match against _SRC misses (it did, on the first run of this fix).
        if not _is_first_party_module(mod_name):
            continue
        for attr, val in list(vars(mod).items()):
            if attr.startswith("__"):
                continue  # __file__ / __cached__ / __path__ are the module's identity
            moved = _rehome(val, home)
            if moved is not None:
                monkeypatch.setattr(mod, attr, moved)
            elif _is_first_party_instance(val) and (
                    attr in _lazy_singleton_names(mod_name) or _holds_real_home(val)):
                # A lazy singleton (`_engine = None` … `get_alert_engine()`)
                # built by an EARLIER test carries that test's world into the
                # sweep: the real home frozen in SettingsManager (it wrote the
                # operator's mesh_alerts.json), or 7 services fed to the
                # health scorer (dashboard/score read 69/100 in suite order,
                # passed alone). Drop it so the accessor builds a fresh one
                # under dead externals; restored after.
                monkeypatch.setattr(mod, attr, None)

    empty = tmp_path / "empty-listing"
    empty.mkdir()
    _kill_box_state(monkeypatch, empty)

    # No real process is ever signalled. /proc/<pid> is readable, so a
    # "Stop NomadNet" item one level down finds the operator's REAL client
    # through find_competing_clients() and would os.kill() it. Refused like
    # any other permission failure, and witnessed.
    def no_signal(pid, sig, *a):
        _SIGNALS.append((pid, sig))
        raise PermissionError(1, "[truth-sweep] signalling a real process is refused")
    monkeypatch.setattr(os, "kill", no_signal)
    monkeypatch.setattr(os, "killpg", no_signal)
    _SIGNALS.clear()

    # no_network blocks socket.socket only; name resolution goes through
    # libc (getaddrinfo) and REACHED the real DNS — `system/network > dns`
    # rendered a live meshtastic.org address (2026-09-22).
    import socket as _socket

    def no_dns(*a, **k):
        raise _socket.gaierror(-3, "[truth-sweep] name resolution is dead")
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        monkeypatch.setattr(_socket, name, no_dns)

    # Nothing opens on the operator's desktop. The real module would try a
    # browser binary (dead via subprocess anyway) — refuse at the API so a
    # screen that says "opened in browser" is judged against False.
    import webbrowser
    for name in ("open", "open_new", "open_new_tab"):
        monkeypatch.setattr(webbrowser, name, lambda *a, **k: False)

    # Record every exception that escapes a handler into safe_call, then
    # re-raise so safe_call renders its real dialog exactly as in the TUI.
    from handler_protocol import TUIContext
    real_safe_call = TUIContext.safe_call

    def recording_safe_call(self, name, method, *args, **kwargs):
        def witnessed(*a, **k):
            try:
                return method(*a, **k)
            except KeyboardInterrupt:
                raise
            except BaseException as e:
                _CRASHES.append((name, type(e).__name__, str(e)[:200]))
                raise
        return real_safe_call(self, name, witnessed, *args, **kwargs)
    monkeypatch.setattr(TUIContext, "safe_call", recording_safe_call)

    _CRASHES.clear()
    _HOME_TOUCHES.clear()
    _BOX_STATE_TOUCHES.clear()
    before = _first_party_globals()
    _AUDIT["armed"] = True
    try:
        yield home
    finally:
        # An item can START something that outlives the test — demo traffic
        # publishing onto the process-global event bus 7.7 s later, inside
        # an unrelated test (Fable review, finding 6). Stop every first-party
        # singleton this test created, while the patches are still live
        # (monkeypatch, a dependency of this fixture, tears down after it).
        for key, obj in _first_party_globals().items():
            if before.get(key) is obj:
                continue
            # stop() or, failing that, close() (MetricsHistory's hourly
            # cleanup thread ends only through close()).
            stop = getattr(obj, "stop", None) or getattr(obj, "close", None)
            if callable(stop):
                try:
                    stop()
                except Exception as e:  # a failed stop is reported, not hidden
                    _STOP_FAILURES.append((key, repr(e)[:120]))
        _AUDIT["armed"] = False


class _SweepDialog(FakeDialog):
    """FakeDialog that remembers how much stdout existed at each call, so the
    level-two walk can judge ONLY what the sub-item rendered — a level-one
    honest word printed before the menu must not vouch for the screen behind
    it."""

    def __init__(self, out: io.StringIO):
        super().__init__()
        self._out = out
        self.marks: list = []

    def _mark(self):
        while len(self.marks) < len(self.calls):
            self.marks.append(len(self._out.getvalue()))

    def menu(self, *a, **k):
        r = super().menu(*a, **k)
        self._mark()
        return r

    def __getattribute__(self, name):
        attr = super().__getattribute__(name)
        if name in ("msgbox", "yesno", "inputbox", "checklist", "textbox",
                    "editbox", "infobox"):
            def marked(*a, **k):
                try:
                    return attr(*a, **k)
                finally:
                    self._mark()
            return marked
        return attr


def _dispatch(section: str, tag: str, menu_script=()):
    """Dispatch one action on a fresh registry with the given menu answers
    (then None forever = "back"). Returns (routed, dialog, stdout text)."""
    from handlers import get_all_handlers
    out = io.StringIO()
    dialog = _SweepDialog(out)
    dialog._menu_returns = list(menu_script)
    _LAST["dialog"], _LAST["out"] = dialog, out  # reachable if dispatch never returns
    ctx = make_handler_context(dialog=dialog)
    reg = HandlerRegistry(ctx)
    ctx.registry = reg  # main.py wires this; handlers reach siblings through it
    for cls in get_all_handlers():
        reg.register(cls())
    _CRASHES.clear()
    with contextlib.redirect_stdout(out):
        routed = reg.dispatch(section, tag)
    return routed, dialog, out.getvalue()


def _render(section: str, tag: str) -> tuple[bool, list, str]:
    """Dispatch one action on a fresh registry; return (routed, kinds, text).

    text = every dialog's title+body, then everything printed to stdout.
    """
    routed, dialog, printed = _dispatch(section, tag)
    kinds, text = _judge(dialog.calls, printed)
    return routed, kinds, text


def _judge(calls, printed: str) -> tuple[list, str]:
    """(kinds, text) for a slice of dialog calls + the stdout they produced."""
    kinds = [c[0] for c in calls]
    text = "\n".join(
        " ".join(str(part) for part in c[1] if part is not None) for c in calls
    )
    if printed.strip():
        kinds.append("stdout")
        text = text + "\n" + printed
    for label in _status_claims(calls):
        kinds.append("status_ok")
        text = text + "\n[status item] " + label
    for name, etype, msg in _CRASHES:
        kinds.append("crashed")
        text = text + f"\n[crashed] {name}: {etype}: {msg}"
    return kinds, text


def _verdict(kinds: list, text: str) -> str:
    """Classify one rendered action. Pure; unit-tested below."""
    if not kinds:
        return "silent"
    if "crashed" in kinds:
        return "crashed"
    if "hung" in kinds:
        return "hung"
    if "status_ok" in kinds:
        return "false-ok"
    if all(k in NAVIGATION for k in kinds):
        return "navigation"
    scrubbed = ZERO_COUNT.sub("", _ANSI.sub("", text))
    if HONEST.search(scrubbed):
        return "honest"
    return "false-ok"


_DUMP: dict = {}


@pytest.mark.parametrize("section,tag", ACTIONS, ids=[f"{s}/{t}" for s, t in ACTIONS])
def test_action_tells_the_truth_with_every_external_dead(section, tag, dead_externals):
    routed, kinds, text = _render(section, tag)
    assert routed is True, f"{section}/{tag} did not route"
    assert not _HOME_TOUCHES, (
        f"{section}/{tag} touched the operator's REAL home — its verdict is a "
        f"statement about this box's files, not the code: {_HOME_TOUCHES[:5]}. "
        f"Find the path frozen outside get_real_user_home() (a class attribute "
        f"or default argument the fixture's module-global rewrite cannot reach) "
        f"and resolve it at call time.")
    assert not _BOX_STATE_TOUCHES, (
        f"{section}/{tag} read box state AROUND the fixture's os-layer patches: "
        f"{_BOX_STATE_TOUCHES[:5]} — its verdict depends on which box ran the "
        f"suite. Find the call that bypasses os.stat/io.open/os.scandir (a C "
        f"extension, an early-bound alias) and patch it in _kill_box_state.")
    verdict = _verdict(kinds, text)
    key = (section, tag)
    _DUMP[f"{section}/{tag}"] = {"verdict": verdict, "kinds": kinds, "text": text}

    crashes = [ln for ln in text.splitlines() if ln.startswith("[crashed] ")]
    if key in KNOWN_CRASHED:
        assert verdict == "crashed", (
            f"{section}/{tag} no longer crashes (renders '{verdict}'). Remove it "
            f"from KNOWN_CRASHED in launcher_tui/action_truth.py — the baseline "
            f"only shrinks — and the sweep will then judge its screen.")
        return
    assert verdict != "crashed", (
        f"{section}/{tag} let an exception escape into safe_call with every "
        f"external dead:\n" + "\n".join(crashes[:5]) + "\n"
        f"safe_call's dialog is honest, but the handler did not handle its own "
        f"failure (honest_failure_modes #1). Catch it where the external is "
        f"asked and say UNKNOWN / not installed. KNOWN_CRASHED is frozen: do "
        f"not add to it to make this pass.")

    if key in KNOWN_FALSE_OK:
        assert verdict == "false-ok", (
            f"{section}/{tag} now renders '{verdict}' — it tells the truth. "
            f"Remove it from KNOWN_FALSE_OK (the baseline only shrinks).")
        return
    if key in LOCAL_ONLY:
        # A declared local action may say anything; it asked no external.
        return
    if "status_ok" in kinds:
        items = [ln for ln in text.splitlines() if ln.startswith("[status item] ")]
        assert verdict != "false-ok", (
            f"{section}/{tag} renders menu rows that assert a POSITIVE status with "
            f"every external dead:\n" + "\n".join(items[:8]) + "\n"
            f"Nothing was observed, so OK/PASS/✓ is a claim the check did not "
            f"earn: return SKIP (or UNKNOWN) when the input was absent, not OK — "
            f"see _config_doctor_checks.check_rnsd_config_drift for the pattern.")
    assert verdict != "false-ok", (
        f"{section}/{tag} rendered a confident screen with every external dead "
        f"and no word of uncertainty:\n{text[:500]}\n"
        f"Either the screen lies (fix it: say UNKNOWN when the source could not "
        f"be asked) or the action is local-only (declare it in "
        f"launcher_tui/action_truth.py LOCAL_ONLY with a why).")


class _SubItemHung(BaseException):
    """Raised by the per-item alarm. BaseException so a handler's
    `except Exception` cannot swallow it and loop on."""


def _first_menu(dialog):
    for i, c in enumerate(dialog.calls):
        if c[0] == "menu":
            return i, c
    return None, None


def _walk_level_two(section: str, tag: str) -> dict:
    """Pick every item of the action's first menu, one dispatch each, and
    judge ONLY what that item rendered (calls after the first menu, stdout
    printed after it, re-shows of the parent menu excluded). Returns
    {item_tag: (verdict, kinds, text)}."""
    import signal
    import threading
    import time as _time
    _, dialog, _ = _dispatch(section, tag)
    i, top = _first_menu(dialog)
    if top is None:
        return {}
    out = {}
    real_sleep = _time.sleep
    main = threading.main_thread()
    for choice in top[1][2] or ():
        item = choice[0]
        if item in BACK_TAGS or not str(item).strip():
            continue
        where = f"{section}/{tag} > {item}"
        slept = {"n": 0}
        stage = {"ctrl_c": False}

        # A live view ("monitor until Ctrl+C", "sweep for 10 s") is stopped
        # the way an operator stops it: Ctrl+C — after SLEEP_BUDGET sleeps
        # (sleeps cost nothing here) or CTRL_C_AFTER_S of wall time, since
        # some wait with Event().wait. Its post-Ctrl+C screen is part of what
        # is judged, and the cut is RECORDED (kind `ctrl_c`) so a partial
        # screen is never mistaken for a finished one. Still running
        # HUNG_AFTER_S later = it ignored Ctrl+C; the alarm then RE-FIRES
        # every second, so a handler that swallows one _SubItemHung cannot
        # loop forever (Fable review, finding 1).
        def ctrl_c(_stage=stage):
            _stage["ctrl_c"] = True
            signal.setitimer(signal.ITIMER_REAL, HUNG_AFTER_S, 1.0)
            raise KeyboardInterrupt

        def fast_sleep(s, _slept=slept, _stage=stage):
            # Only the item's own (main) thread is budgeted: a background
            # thread's sleeps must not spend the item's budget or deliver
            # its Ctrl+C (Fable review, finding 9) — they sleep for real.
            if threading.current_thread() is not main:
                return real_sleep(s)
            _slept["n"] += 1
            if _slept["n"] > SLEEP_BUDGET and not _stage["ctrl_c"]:
                ctrl_c()

        def on_alarm(*_a, _where=where, _stage=stage):
            if not _stage["ctrl_c"]:
                ctrl_c()
            raise _SubItemHung(f"{_where}: still running {HUNG_AFTER_S}s after Ctrl+C")

        # alarm()/setitimer share ONE timer with pytest-timeout (signal
        # method): cancelling ours cancelled the suite's 600 s guard after
        # the first item (Fable review, finding 1, reproduced). Save the
        # outer timer + handler and give back what is left of it.
        outer_delay, outer_interval = signal.getitimer(signal.ITIMER_REAL)
        t0 = _time.monotonic()
        prev = signal.signal(signal.SIGALRM, on_alarm)
        # Never arm past the outer deadline: if it comes first, the item is
        # stopped and the outer handler is fired below.
        signal.setitimer(signal.ITIMER_REAL, min(CTRL_C_AFTER_S, outer_delay)
                         if outer_delay > 0 else CTRL_C_AFTER_S)
        _time.sleep = fast_sleep
        hung = None
        d2 = None
        try:
            _, d2, printed = _dispatch(section, tag, menu_script=[item])
        except KeyboardInterrupt:
            hung = None  # Ctrl+C escaped the handler: an exit, not a hang
        except _SubItemHung as e:
            hung = str(e)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            _time.sleep = real_sleep
            signal.signal(signal.SIGALRM, prev)
            if outer_delay > 0:
                left = outer_delay - (_time.monotonic() - t0)
                if left <= 0:
                    # The outer deadline passed while this item ran. Re-arming
                    # a tiny timer lost it to the NEXT item's handler (drilled:
                    # 20.8 s passed under --timeout=3) — deliver it now.
                    signal.raise_signal(signal.SIGALRM)
                else:
                    signal.setitimer(signal.ITIMER_REAL, left, outer_interval)
        if d2 is None:
            # Stopped mid-render: judge what it had drawn by then. The stop
            # itself passed through safe_call's witness — it is not a crash.
            d2, printed = _LAST["dialog"], _LAST["out"].getvalue()
            _CRASHES[:] = [c for c in _CRASHES
                           if c[1] not in ("_SubItemHung", "KeyboardInterrupt")]
        j, _ = _first_menu(d2)
        # Drop only an UNCHANGED re-show of the parent (same title, text and
        # rows). A re-show whose text or rows changed IS the item's result —
        # "Status: CAPTURING", a toggled row — and dropping every same-title
        # menu let a planted "OK  all good" row pass (Fable review, finding 2).
        calls = [c for c in d2.calls[j + 1:]
                 if not (c[0] == "menu" and c[1] == top[1])]
        # j's mark = stdout length when the parent menu returned.
        after = printed[d2.marks[j]:] if j is not None and j < len(d2.marks) else printed
        kinds, text = _judge(calls, after)
        if hung:
            kinds.append("hung")
            text += f"\n[hung] {hung}"
        verdict = _verdict(kinds, text)
        if stage["ctrl_c"]:
            kinds.append("ctrl_c")  # annotation, after the verdict: not a claim
        out[item] = (verdict, kinds, text)
    return out


# Menu answers that leave rather than act. The parent menu's re-show after
# "back" is excluded anyway; skipping these only saves dispatches.
BACK_TAGS = {"back", "exit", "quit", "cancel", "done", "main", "return"}
CTRL_C_AFTER_S = 5  # 2 s already cut CPU-bound items on a Pi 5 (Fable finding 8)
HUNG_AFTER_S = 3
SLEEP_BUDGET = 50


@pytest.mark.parametrize("section,tag", ACTIONS, ids=[f"{s}/{t}" for s, t in ACTIONS])
def test_level_two_items_tell_the_truth(section, tag, dead_externals):
    """Level two: every item one menu below a top-level action, every
    external dead. A sub-item that opens its own menu is `navigation`
    (level three is out of scope, stated).

    GATED on the MEASURED legs only — crash at safe_call (frozen
    KNOWN_CRASHED_L2), hang (ignored Ctrl+C), status-shaped menu rows, and
    the real-home / box-state witnesses. The TEXT verdict is recorded in
    the MF_TRUTH_SWEEP_DUMP `.level2` file but NOT gated: at level two it
    read ~110 of 480 screens `false-ok` (re-derive from the dump; never carry
    the number), nearly all calculators, reference
    text and honest empty states ("No nodes discovered yet") the porous
    vocabulary does not know — the operator's 2026-09-22 decision not to
    widen it applies here too. The real lies that triage found were fixed
    at the source instead."""
    import threading
    before = set(threading.enumerate())
    results = _walk_level_two(section, tag)
    leaked = [t.name for t in threading.enumerate() if t not in before and t.is_alive()]
    for item, (verdict, kinds, text) in results.items():
        _DUMP2[f"{section}/{tag} > {item}"] = {"verdict": verdict, "kinds": kinds,
                                               "text": text[:2000]}
    _DUMP2[f"{section}/{tag} :: signals"] = list(_SIGNALS)
    _DUMP2[f"{section}/{tag} :: threads"] = leaked
    assert not _HOME_TOUCHES, (
        f"{section}/{tag} level two touched the operator's REAL home: {_HOME_TOUCHES[:5]}")
    assert not _BOX_STATE_TOUCHES, (
        f"{section}/{tag} level two read box state around the fixture: {_BOX_STATE_TOUCHES[:5]}")

    problems = []
    for item, (verdict, kinds, text) in results.items():
        key = (section, tag, str(item))
        if key in KNOWN_CRASHED_L2:
            if verdict != "crashed":
                problems.append(f"> {item}: no longer crashes ('{verdict}') — remove it "
                                f"from KNOWN_CRASHED_L2 (the baseline only shrinks)")
            continue
        if verdict == "crashed":
            problems.append(f"> {item}: exception escaped into safe_call — "
                            + "; ".join(ln for ln in text.splitlines()
                                        if ln.startswith("[crashed] ")))
        elif verdict == "hung":
            problems.append(f"> {item}: " + text.splitlines()[-1])
        elif "status_ok" in kinds:
            problems.append(f"> {item}: positive status row with nothing observed — "
                            + "; ".join(ln for ln in text.splitlines()
                                        if ln.startswith("[status item] ")))
    stale = [k for k in KNOWN_CRASHED_L2 if k[:2] == (section, tag) and k[2] not in
             {str(i) for i in results}]
    for k in stale:
        problems.append(f"> {k[2]}: listed in KNOWN_CRASHED_L2 but no longer a menu item")
    assert not problems, (
        f"{section}/{tag} level two:\n  " + "\n  ".join(problems) + "\n"
        f"A crash is fixed in the handler (catch where the external is asked; "
        f"say UNKNOWN / not installed) — KNOWN_CRASHED_L2 is frozen.")


_DUMP2: dict = {}


def test_sweep_never_touched_the_real_home(dead_externals):
    """The fixture's whole point; if this fails, every verdict above is
    a statement about THIS box's files, not about the code."""
    assert _paths.get_real_user_home() == dead_externals
    assert Path(os.path.expanduser("~")) == dead_externals
    # The import-time constant the second review caught, rewritten:
    import utils.common as _common
    assert Path(_common.CONFIG_DIR).is_relative_to(dead_externals)


def test_box_state_is_absent_and_the_witness_can_fail(dead_externals, tmp_path, monkeypatch):
    """Proves both halves on ANY box, including CI where /etc/reticulum does
    not exist: a real file under a prefix declared box state must read as
    absent through every path API the handlers use, mkdir under it must be
    refused, /dev must list empty — and a read that goes AROUND the patches
    (the real io.FileIO, which the patches do not wrap) must be witnessed."""
    import glob as _glob
    import io
    planted = tmp_path / "boxstate"
    planted.mkdir()
    (planted / "config").write_text("[reticulum]\n")
    monkeypatch.setattr(sys.modules[__name__], "BOX_STATE_DIRS",
                        BOX_STATE_DIRS + (str(planted),))
    f = planted / "config"
    assert not f.exists() and not os.path.exists(f) and not os.path.isfile(f)
    with pytest.raises(FileNotFoundError):
        f.read_text()
    with pytest.raises(FileNotFoundError):
        open(f)
    with pytest.raises(FileNotFoundError):
        list(planted.iterdir())
    assert _glob.glob(str(planted / "*")) == []
    with pytest.raises(PermissionError):
        (planted / "sub").mkdir()
    assert os.listdir("/dev") == [] and _glob.glob("/dev/ttyACM*") == []
    # Path.glob binds os.scandir at import on 3.12+ (level-two walk found
    # this box's /dev/ttyACM0 through it, 2026-09-22):
    assert list(Path("/dev").glob("tty*")) == [] and list(Path("/dev").glob("spidev*")) == []
    # Name resolution is dead too (it reached real DNS through getaddrinfo):
    import socket as _socket
    with pytest.raises(_socket.gaierror):
        _socket.getaddrinfo("meshtastic.org", 443)
    import webbrowser
    assert webbrowser.open("http://127.0.0.1:5000") is False

    # sqlite URI form (connect_tuned(..., uri=True)) — Fable review MED-5b:
    import sqlite3
    with pytest.raises(FileNotFoundError):
        sqlite3.connect(f"file:{f}?mode=ro", uri=True)

    _BOX_STATE_TOUCHES.clear()
    io.FileIO(str(f)).close()  # around the patches: must be witnessed
    assert any(p == str(f) for _, p in _BOX_STATE_TOUCHES)
    _BOX_STATE_TOUCHES.clear()


def test_real_home_witness_can_fail(dead_externals):
    """The witness must SEE a touch, or its silence proves nothing. Opens a
    path under the real home that does not exist — the audit event fires
    before the FileNotFoundError, and nothing is read or written."""
    if len(_REAL_HOME.parts) <= 2:
        pytest.skip("real home is too shallow to witness safely")
    probe = _REAL_HOME / ".truthsweep-witness-probe-does-not-exist"
    _AUDIT["armed"] = False
    assert not probe.exists()
    _AUDIT["armed"] = True
    # Refused by the hook BEFORE the open runs (PermissionError, not the
    # FileNotFoundError the real call would give) — and recorded.
    with pytest.raises(PermissionError, match="real home is refused"):
        open(probe)
    assert ("open", str(probe)) in _HOME_TOUCHES
    # The home ITSELF (Fable finding 7): a listing rendered 330 entries unseen.
    with pytest.raises(PermissionError, match="real home is refused"):
        os.listdir(_REAL_HOME)
    assert ("os.listdir", str(_REAL_HOME)) in _HOME_TOUCHES
    _HOME_TOUCHES.clear()


class TestVerdictIsFalsifiable:
    """The lies the non-author review planted (2026-09-22), pinned so the
    floor cannot silently drop. Each row is (kinds, text, verdict)."""

    @pytest.mark.parametrize("kinds,text,expected", [
        (["msgbox"], "Connected ✓ Connected to meshtasticd", "false-ok"),
        (["msgbox"], "Web server healthy on :5000. Errors: 0. Uptime 14 d.", "false-ok"),
        (["msgbox"], "Deps Missing: 0 — all good", "false-ok"),
        (["msgbox"], "DB FAIL: 0 PASS: 9", "false-ok"),
        (["infobox"], "Connected Connected via USB: /dev/ttyACM0", "false-ok"),
        (["stdout"], "✓ Connected to meshtasticd\nall healthy\n", "false-ok"),
        (["msgbox"], "Space Weather Could not fetch: UNKNOWN", "honest"),
        (["stdout"], "rnsd is not running; check skipped\n", "honest"),
        # `crashed` is MEASURED at safe_call (finding #5), never read from text:
        (["msgbox", "crashed"], "File Not Found … [crashed] x: FileNotFoundError", "crashed"),
        (["msgbox"], "Shell error: [truth-sweep] no subprocess: every external is dead", "honest"),
        (["menu"], "Main pick one", "navigation"),
        ([], "", "silent"),
        # Second non-author review (2026-09-22): a bad thing's ABSENCE is a
        # health claim. All six read "honest" before the scrub widened.
        (["stdout"], "rnsd drift check: No drift detected", "false-ok"),
        (["msgbox"], "Health: 0 errors, 0 failures. All services healthy.", "false-ok"),
        (["msgbox"], "Mesh: No problems found", "false-ok"),
        (["msgbox"], "Radio health: no issues detected", "false-ok"),
        (["msgbox"], "Errors: none. Gateway bridging OK.", "false-ok"),
        (["msgbox"], "Checks: 9 passed, nothing failed", "false-ok"),
        # Controls: an ABSENT DEVICE / tool / service is uncertainty, and
        # an honest word beside a scrubbed phrase survives the scrub.
        (["msgbox"], "No radio detected on /dev/ttyUSB*", "honest"),
        (["msgbox"], "No NanoVNA found", "honest"),
        (["stdout"], "rnsd: NOT RUNNING", "honest"),
        (["msgbox"], "No alerts — alert source unreachable", "honest"),
        # Fable review HIGH-1: colour codes hid the claim from \b.
        (["stdout"], "  \x1b[0;32mNo drift detected\x1b[0m\n", "false-ok"),
        (["stdout"], "\x1b[0;32mErrors: 0\x1b[0m", "false-ok"),
        (["stdout"], "\x1b[0;32m0 failures\x1b[0m", "false-ok"),
        # Fable review MED-3: a bad noun used ADJECTIVALLY is uncertainty.
        (["msgbox"], "No alert feed available", "honest"),
        (["msgbox"], "No drift baseline found", "honest"),
        (["msgbox"], "No error log available", "honest"),
        (["msgbox"], "No alerts feed configured", "honest"),
        (["msgbox"], "No warning threshold configured", "honest"),
        (["msgbox"], "No alert source configured", "honest"),
        # Fable rev2 HIGH-2: the end-anchor let these back through as honest.
        (["stdout"], "No drift detected — rnsd not running", "honest"),  # real honest words remain
        (["stdout"], "No drift detected (gateway vs rnsd)", "false-ok"),
        (["msgbox"], "No errors — all good", "false-ok"),
        (["msgbox"], "No issues found | uptime 14d", "false-ok"),
        (["stdout"], "No drift detected ✓", "false-ok"),
        (["msgbox"], "No problems found in 9 checks", "false-ok"),
        (["stdout"], "No drift detected\tOK", "false-ok"),
        (["stdout"], "no drift detected today", "false-ok"),
    ])
    def test_verdict(self, kinds, text, expected):
        assert _verdict(kinds, text) == expected

    def test_incidental_honest_word_still_passes_and_is_documented(self):
        # The stated floor: one honest word beside a false claim passes.
        assert _verdict(["msgbox"], "✓ web server is UP. Tip: if a page shows an error, press F5") == "honest"
        # Also still passing, stated not hidden (second review): "disabled"
        # and "n/a" are honest words, so a claim beside them passes; a
        # confident screen followed by a crash marker is `crashed`; claims
        # in menu items / yesno / inputbox text are `navigation`.
        assert _verdict(["msgbox"], "Bridge running. Failover: disabled") == "honest"
        assert _verdict(["msgbox", "crashed"], "✓ Connected\n[crashed] x: OSError") == "crashed"
        # Claims in menu HEADER text / yesno / inputbox prompts are still
        # `navigation`; only status-shaped menu ITEMS are read (finding 4).
        assert _verdict(["yesno"], "Radio connected and healthy. Reboot it?") == "navigation"


class TestMenuItemStatusClaims:
    """Finding 4 of the second non-author review, pinned."""

    def _calls(self, *labels):
        return [("menu", ("T", "pick", [(str(i), lab) for i, lab in enumerate(labels)]), {})]

    @pytest.mark.parametrize("label", [
        "OK     rnsd is not running; config drift check skipped",
        "OK     all enabled RNS interfaces resolve their devices/hosts",
        "[OK] meshtasticd reachable", "PASS   rpc_key pinned", "✓ Connected", "OK",
    ])
    def test_positive_status_item_is_a_claim(self, label):
        assert _status_claims(self._calls(label)) == [label]
        assert _verdict(["menu", "status_ok"], "[status item] " + label) == "false-ok"

    @pytest.mark.parametrize("label", [
        "Active Connections (ss -tunp)", "Okay, continue", "SKIP   RNS config not found",
        "FAIL   RNS shared instance not detected", "View Active Alerts", "Status  View radio health",
    ])
    def test_ordinary_or_honest_item_is_not(self, label):
        assert _status_claims(self._calls(label)) == []

    def test_crash_still_outranks(self):
        assert _verdict(["menu", "status_ok", "crashed"],
                        "[status item] OK x\n[crashed] y: OSError") == "crashed"


def test_allowlists_name_only_live_actions():
    """A closed enum's consumers must not outlive its members."""
    live = set(ACTIONS)
    stale = [k for k in list(LOCAL_ONLY) + list(KNOWN_FALSE_OK) + list(KNOWN_CRASHED)
             + [k[:2] for k in KNOWN_CRASHED_L2]
             if k not in live]
    assert not stale, f"action_truth lists actions that no longer exist: {stale}"


def test_known_crashed_is_frozen_not_just_documented():
    """Shrink-only by GATE, not prose (Fable rev2 MED-5): the membership
    asserts catch a stale entry, but nothing stopped ADDING one to make a
    new crash pass. Growing this set needs this literal changed in the same
    commit — a visible act, with a provenance row."""
    frozen = {("system", "status")}  # dashboard/score cured 2026-09-22
    assert set(KNOWN_CRASHED) <= frozen, (
        f"KNOWN_CRASHED grew: {sorted(set(KNOWN_CRASHED) - frozen)}. A new crash "
        f"is fixed in the handler, not baselined.")
    assert not KNOWN_FALSE_OK, "KNOWN_FALSE_OK is frozen EMPTY"
    frozen_l2 = {
        ("dashboard", "health", "latency"), ("dashboard", "latency", "probe"),
        ("dashboard", "reports", "generate"), ("dashboard", "reports", "save"),
        ("fleet", "fleet_backup", "setup"), ("system", "discover", "full"),
        ("system", "hardware", "detect"), ("system", "logs", "live-all"),
        ("system", "logs", "live-mesh"), ("system", "logs", "live-rns"),
    }
    assert set(KNOWN_CRASHED_L2) <= frozen_l2, (
        f"KNOWN_CRASHED_L2 grew: {sorted(set(KNOWN_CRASHED_L2) - frozen_l2)}. "
        f"A new level-two crash is fixed in the handler, not baselined.")


def test_every_local_only_entry_says_why():
    empty = [k for k, why in LOCAL_ONLY.items() if not str(why).strip()]
    assert not empty, f"LOCAL_ONLY entries without a why: {empty}"


@pytest.fixture(scope="session", autouse=True)
def _write_dump():
    yield
    path = os.environ.get("MF_TRUTH_SWEEP_DUMP")
    if path and _DUMP:
        Path(path).write_text(json.dumps(_DUMP, indent=1, sort_keys=True))
    if path and _DUMP2:
        Path(path + ".level2").write_text(json.dumps(_DUMP2, indent=1, sort_keys=True))
