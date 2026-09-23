"""The success-truth SWEEP: every top-level TUI action, every external dead.

`test_all_tags_dispatch` (test_all_handlers_protocol.py) proves that all
registered actions ROUTE. This proves what their FIRST SCREEN says when
there is nothing to say it about: sockets refused, no subprocess, no tool on
PATH, the operator's home replaced by an empty one, and box state (/etc/reticulum,
/etc/meshtasticd, systemd units, /proc/net, device nodes) absent. A screen rendered under
those conditions must carry a word of uncertainty — or the action must be
declared LOCAL_ONLY in `launcher_tui/action_truth.py` with a why.

SCOPE, stated exactly (non-author review 2026-09-22 corrected the first
cut's "116/116" to this): ONE dialog level. `FakeDialog.menu` returns
None, so nothing behind a sub-menu is run — an action whose only output is
a menu is `navigation` and passes without a claim being checked. Walking
one level down is queued, not done.

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
the swallow class is MF027 / hfm #9's job); a `crashed` handler (safe_call
caught an exception) passes because the error dialog IS honest, but it is
its own verdict so it never counts as the handler telling the truth.
Claims that a bad thing is ABSENT ("Errors: 0", "0 failures", "nothing
failed", "No drift detected") are stripped before the vocabulary is
applied. The vocabulary is a FLOOR: "disabled" / "n/a" still pass a claim
beside them, and claims inside menu items / yesno / inputbox text are
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
from launcher_tui.action_truth import KNOWN_FALSE_OK, LOCAL_ONLY  # noqa: E402
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
    rf"|\bno\s+(?:\w+\s+){{0,2}}{_BAD}\b(?:\s+(?:detected|found|reported|seen|"
    rf"present|active|pending|observed|recorded))?",  # No drift detected
    re.IGNORECASE,
)

# The fixture's own marker, and pytest's captured-stdin message: text that
# reaches a dialog only because the harness intervened. A handler whose
# dialog carries one of these CRASHED — it did not tell the truth, safe_call
# did (review finding 1).
CRASH_MARKERS = (
    "[truth-sweep] no subprocess",
    "reading from stdin while output is captured",
    "Details logged to:",
)

# Dialog kinds that render only navigation / prompts, never a claim.
# infobox is NOT here: "Connected via USB: /dev/ttyACM0" was an infobox.
NAVIGATION = {"menu", "checklist", "inputbox", "yesno", "editbox"}


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
    if (len(_REAL_HOME.parts) > 2 and p.startswith(str(_REAL_HOME) + os.sep)
            and not p.startswith(_EXEMPT) and not p.endswith((".py", ".pyc", ".so"))):
        _HOME_TOUCHES.append((event, p))
    # The os-layer patches raise BEFORE the real call, so any audited event
    # on box state here is a read that went around them.
    if _box_path(p) is not None:
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
        s = os.path.abspath(os.fsdecode(p))
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


def _rehome(val, home: Path):
    """The fake-home equivalent of a Path/str rooted at the real home, else None."""
    if len(_REAL_HOME.parts) <= 2:
        return None
    if isinstance(val, Path):
        try:
            return home / val.resolve().relative_to(_REAL_HOME)
        except (ValueError, OSError):
            return None
    if isinstance(val, str) and val.startswith(str(_REAL_HOME) + os.sep):
        return str(home / Path(val).relative_to(_REAL_HOME))
    return None


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
        mod_file = os.path.realpath(getattr(mod, "__file__", None) or "/")
        if not mod_file.startswith(str(_SRC.resolve()) + os.sep):
            continue
        for attr, val in list(vars(mod).items()):
            moved = _rehome(val, home)
            if moved is not None:
                monkeypatch.setattr(mod, attr, moved)

    empty = tmp_path / "empty-listing"
    empty.mkdir()
    _kill_box_state(monkeypatch, empty)

    _HOME_TOUCHES.clear()
    _BOX_STATE_TOUCHES.clear()
    _AUDIT["armed"] = True
    try:
        yield home
    finally:
        _AUDIT["armed"] = False


def _render(section: str, tag: str) -> tuple[bool, list, str]:
    """Dispatch one action on a fresh registry; return (routed, kinds, text).

    text = every dialog's title+body, then everything printed to stdout.
    """
    from handlers import get_all_handlers
    dialog = FakeDialog()
    ctx = make_handler_context(dialog=dialog)
    reg = HandlerRegistry(ctx)
    for cls in get_all_handlers():
        reg.register(cls())
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        routed = reg.dispatch(section, tag)
    kinds = [c[0] for c in dialog.calls]
    text = "\n".join(
        " ".join(str(part) for part in c[1] if part is not None) for c in dialog.calls
    )
    printed = out.getvalue()
    if printed.strip():
        kinds.append("stdout")
        text = text + "\n" + printed
    return routed, kinds, text


def _verdict(kinds: list, text: str) -> str:
    """Classify one rendered action. Pure; unit-tested below."""
    if not kinds:
        return "silent"
    if all(k in NAVIGATION for k in kinds):
        return "navigation"
    if any(m in text for m in CRASH_MARKERS):
        return "crashed"
    scrubbed = ZERO_COUNT.sub("", text)
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
    _DUMP[f"{section}/{tag}"] = {"verdict": verdict, "kinds": kinds, "text": text[:800]}

    if key in KNOWN_FALSE_OK:
        assert verdict == "false-ok", (
            f"{section}/{tag} now renders '{verdict}' — it tells the truth. "
            f"Remove it from KNOWN_FALSE_OK (the baseline only shrinks).")
        return
    if key in LOCAL_ONLY:
        # A declared local action may say anything; it asked no external.
        return
    assert verdict != "false-ok", (
        f"{section}/{tag} rendered a confident screen with every external dead "
        f"and no word of uncertainty:\n{text[:500]}\n"
        f"Either the screen lies (fix it: say UNKNOWN when the source could not "
        f"be asked) or the action is local-only (declare it in "
        f"launcher_tui/action_truth.py LOCAL_ONLY with a why).")


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
    assert not probe.exists()
    with pytest.raises(FileNotFoundError):
        open(probe)
    assert ("open", str(probe)) in _HOME_TOUCHES
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
        (["msgbox"], "Error Failed: [truth-sweep] no subprocess: every external is dead", "crashed"),
        (["msgbox"], "Error OSError: pytest: reading from stdin while output is captured!", "crashed"),
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
        assert _verdict(["msgbox"], "✓ Connected\nDetails logged to: /x") == "crashed"
        assert _verdict(["menu"], "Doctor [('a', 'OK  rnsd reachable')]") == "navigation"


def test_allowlists_name_only_live_actions():
    """A closed enum's consumers must not outlive its members."""
    live = set(ACTIONS)
    stale = [k for k in list(LOCAL_ONLY) + list(KNOWN_FALSE_OK) if k not in live]
    assert not stale, f"action_truth lists actions that no longer exist: {stale}"


def test_every_local_only_entry_says_why():
    empty = [k for k, why in LOCAL_ONLY.items() if not str(why).strip()]
    assert not empty, f"LOCAL_ONLY entries without a why: {empty}"


@pytest.fixture(scope="session", autouse=True)
def _write_dump():
    yield
    path = os.environ.get("MF_TRUTH_SWEEP_DUMP")
    if path and _DUMP:
        Path(path).write_text(json.dumps(_DUMP, indent=1, sort_keys=True))
