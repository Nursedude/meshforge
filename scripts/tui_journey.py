#!/usr/bin/env python3
"""Drive TUI journeys on a LIVE box and judge each screen against an
independent oracle — a command the TUI does not use.

The render smoke (``tui_smoke.py``) proves screens DRAW; the leaf walk
(``tui_leaf_walk.py``) proves a leaf RETURNS; the truth sweep proves a
screen stays honest with every external DEAD. None of them asks the
question an operator actually asks: *with everything alive, is what this
screen says TRUE?* That is this driver.

A journey (``scripts/tui_journeys.py``) is:
  * ``path``   — the menu answers, in order, that walk to the screen;
  * ``check``  — reads the captured screen text, runs an oracle the TUI
                 does not use (``systemctl show``, ``rnstatus``, git …),
                 and returns ``[(ok, message), …]``;
  * ``plant``  — rewrites the captured text into one specific LIE.

THE CONTROL (why ``plant`` is mandatory). A check that cannot fail is not
evidence (a guard that never failed). After a journey's checks pass on the
real screen, they are re-run on ``plant(screen)`` and MUST fail there. If
the lie still passes, the journey reads **UNFALSIFIED** — never PASS. This
is also why the 09-22 regex porosity result (18 of 22 planted lie shapes
read "honest") does not carry over: the judge here is a measurement, not a
vocabulary.

SAFETY — the child dispatches real handlers on a real box, so it arms a
Python audit hook that REFUSES, before they happen:
  * subprocess argv carrying a mutating verb (systemctl start/stop/…,
    sudo, meshtastic --set*, rnodeconf, kill, reboot, pip, apt …);
  * ``os.system`` / ``os.kill`` / ``os.remove``-family calls;
  * any ``open()`` for writing outside a per-run scratch dir.
A refusal is recorded and reported as **BLOCKED** — a journey that tries
to write is a finding about the screen, never a pass. The hook is a
denylist and therefore a FLOOR, not a proof of read-only-ness: a C
extension or a socket write (e.g. a TCPInterface admin message) is not
seen. Only walk paths you believe read-only; the hook catches mistakes.

Verdicts: PASS (oracle agrees AND the planted lie was caught) · FAIL (the
screen disagrees with the oracle — a finding) · UNFALSIFIED · BLOCKED ·
DIVERGED (the script's answer did not match the prompt it met) · ERROR.
Exit 0 only when every selected journey is PASS; UNKNOWN is never a pass.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
TUI = SRC / "launcher_tui"
sys.path.insert(0, str(REPO / "scripts"))

# Titles safe_call shows when a handler raised — same set the leaf walk uses.
from tui_leaf_walk import SAFE_CALL_ERROR_TITLES  # noqa: E402

CHILD = r'''
import io, json, logging, os, sys, contextlib
sys.path.insert(0, %(src)r)
sys.path.insert(0, %(tui)r)
logging.disable(logging.CRITICAL)
SPEC = json.loads(%(spec)r)
SCRATCH = %(scratch)r
# Sandbox mode (set by scripts/tui_sandbox.sh inside unshare -rnm): the only
# meshtasticd reachable is the SimRadio, so radio writes are allowed — but
# ONLY to --host localhost/127.0.0.1, never a serial --port or a bare
# autodetect (/dev is NOT namespaced: the RNode's USB serial is still there).
SANDBOX = os.environ.get("MF_TUI_SANDBOX") == "1"
SANDBOX_RW = os.environ.get("MF_SANDBOX_CONFIG") if SANDBOX else None
blocked, screens = [], []

MUTATING = (("systemctl", ("start", "stop", "restart", "reload", "enable",
                           "disable", "mask", "unmask", "kill", "daemon-reload",
                           "reset-failed", "isolate", "reboot", "poweroff")),)
MUTATING_BINS = {"sudo", "pkexec", "rnodeconf", "kill", "pkill", "killall",
                 "reboot", "shutdown", "poweroff", "pip", "pip3", "apt",
                 "apt-get", "dpkg", "tee", "rm", "mv", "cp", "chown", "chmod",
                 "nmcli", "crontab", "git", "sqlite3", "useradd", "passwd"}

def _argv(a):
    if isinstance(a, (list, tuple)):
        return [str(x) for x in a]
    return str(a).split()

def _mutating(argv):
    if not argv:
        return None
    b = os.path.basename(argv[0])
    if b in MUTATING_BINS:
        return b
    if b == "systemctl":
        verbs = [x for x in argv[1:] if not x.startswith("-")]
        if verbs and verbs[0] in dict(MUTATING)["systemctl"]:
            return "systemctl " + verbs[0]
    if b == "meshtastic":
        host = argv[argv.index("--host") + 1] if "--host" in argv[:-1] else None
        if (host or "").rsplit(":", 1)[0] not in ("localhost", "127.0.0.1"):
            return "meshtastic without --host localhost (serial/autodetect)"
        if not SANDBOX and any(x.startswith(("--set", "--seturl", "--ch-",
                "--reboot", "--factory", "--remove", "--sendtext", "--configure",
                "--begin-edit", "--commit-edit")) for x in argv):
            return "meshtastic write"
    return None

_DEVICES = ("/dev/tty", "/dev/spidev", "/dev/gpiochip", "/dev/i2c", "/dev/serial")

def _writable(p):
    return p.startswith(SCRATCH) or (SANDBOX_RW and p.startswith(SANDBOX_RW))

def _own_child(pid):
    """True when pid's parent is this process (a subprocess we spawned)."""
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1]) == os.getpid()
    except (OSError, ValueError, IndexError):
        return False

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

def _hook(event, args):
    if event == "subprocess.Popen":
        why = _mutating(_argv(args[1]) or _argv(args[0]))
        if why:
            blocked.append(f"subprocess {why}: {_argv(args[1])[:6]}")
            raise PermissionError(f"[tui_journey] refused mutating command ({why})")
    elif event == "os.kill" and args and _own_child(args[0]):
        return        # subprocess.run's own timeout kill — refusing it HANGS the TUI
    elif event in ("os.system", "os.kill", "os.remove", "os.rename",
                   "os.rmdir", "os.truncate", "shutil.rmtree", "os.chmod",
                   "os.chown", "os.symlink"):
        target = str(args[0]) if args else ""
        if event in ("os.remove", "os.rename", "os.truncate", "os.chmod") \
                and _writable(target):
            return
        blocked.append(f"{event}: {target[:120]}")
        raise PermissionError(f"[tui_journey] refused {event}")
    elif event == "open" and args:
        path, mode, flags = args[0], args[1], args[2] if len(args) > 2 else 0
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) \
            or (isinstance(flags, int) and flags & _WRITE_FLAGS)
        p = str(path)
        if isinstance(path, int):
            return
        if p.startswith(_DEVICES):
            blocked.append(f"open-device: {p[:120]}")
            raise PermissionError(f"[tui_journey] refused device {p}")
        if writes and not _writable(p) and p != "/dev/null":
            blocked.append(f"open-for-write: {p[:120]}")
            raise PermissionError(f"[tui_journey] refused write to {p}")

from handler_protocol import TUIContext
from handler_registry import HandlerRegistry
from handlers import get_all_handlers

answers = list(SPEC["path"])
diverged = []

def _next(kind, title, choices=None):
    """Consume the next scripted answer if it is meant for this prompt kind."""
    if not answers:
        return None
    a = answers[0]
    if a.get("kind") != kind:
        diverged.append(f"expected {a.get('kind')} got {kind} ({title!r})")
        answers.clear()
        return None
    answers.pop(0)
    if kind == "menu":
        want = a["pick"]
        for tag, label in choices or ():
            if tag == want or want.lower() in str(label).lower():
                return tag
        diverged.append(f"no menu item {want!r} in {title!r}: "
                        f"{[c[0] for c in choices or ()][:12]}")
        answers.clear()
        return None
    return a.get("answer")

def _rec(kind, title, text="", choices=None):
    screens.append({"kind": kind, "title": str(title), "text": str(text),
                    "choices": [[str(c[0]), str(c[1])] for c in (choices or [])]})

class JourneyDialog:
    def set_status_bar(self, *a, **k): return None
    def available(self): return True
    def msgbox(self, title, text, height=None, width=None):
        _rec("msgbox", title, text); _next("msgbox", title); return None
    def infobox(self, title, text): _rec("infobox", title, text); return None
    def textbox(self, title, text, height=None, width=None):
        _rec("textbox", title, text); _next("textbox", title); return None
    def editbox(self, title, file_path, height=None, width=None):
        _rec("editbox", title, file_path); return None
    def yesno(self, title, text, default_no=False, height=None, width=None):
        _rec("yesno", title, text)
        return bool(_next("yesno", title)) if answers else False
    def menu(self, title, text, choices, height=None, width=None, list_height=None):
        _rec("menu", title, text, choices)
        return _next("menu", title, choices)
    def inputbox(self, title, text, init="", height=None, width=None):
        _rec("inputbox", title, f"{text}\n[pre-filled: {init!r}]")
        a = _next("inputbox", title)
        return init if a == "__INIT__" else a   # "__INIT__" = one Enter
    def checklist(self, title, text, choices, height=None, width=None, list_height=None):
        _rec("checklist", title, text, [(c[0], c[1]) for c in choices]); return None
    def radiolist(self, title, text, choices, height=None, width=None, list_height=None):
        _rec("radiolist", title, text, [(c[0], c[1]) for c in choices]); return None
    def gauge(self, *a, **k): return None

ctx = TUIContext(dialog=JourneyDialog())
ctx.wait_for_enter = lambda msg="": None
registry = HandlerRegistry(ctx)
for cls in get_all_handlers():
    registry.register(cls())
ctx.registry = registry

import subprocess as _sp, socket as _so, time as _t

def _sim_up(deadline=45):
    """A --set on the sim can reboot it; wait for 4403 to answer again."""
    end = _t.time() + deadline
    while _t.time() < end:
        try:
            _so.create_connection(("127.0.0.1", 4403), timeout=1).close()
            return True
        except OSError:
            _t.sleep(1)
    return False

readback_errors = []

def _run_all(cmds):
    """Output per argv; a failed read is "" for the check (UNKNOWN there) AND
    a witness in readback_errors — never a silent empty."""
    out = {}
    for argv in cmds:
        k = " ".join(argv)
        try:
            p = _sp.run(argv, capture_output=True, text=True, timeout=60)
            out[k] = p.stdout if p.returncode == 0 else ""
            if p.returncode != 0:
                readback_errors.append(f"{k}: rc={p.returncode} {(p.stderr or '').strip()[-200:]}")
        except (OSError, _sp.TimeoutExpired, PermissionError) as e:
            out[k] = ""
            readback_errors.append(f"{k}: {type(e).__name__}: {e}")
    return out

# SETUP (sandbox only, BEFORE the guard is armed): give the sim a fleet-shaped
# state, so a hazard that a fresh sim's defaults would hide becomes visible.
setup_error = None
if SPEC.get("setup") and not SANDBOX:
    setup_error = "setup requested outside a sandbox — refused"
for argv in (SPEC.get("setup") or []) if SANDBOX else []:
    p = _sp.run(argv, capture_output=True, text=True, timeout=90)
    _t.sleep(3)
    if p.returncode != 0 or not _sim_up():
        setup_error = f"setup failed: {' '.join(argv)} rc={p.returncode} {p.stderr.strip()[-200:]}"
        break
readback_before = {} if setup_error else _run_all(SPEC.get("readback", []))

sys.addaudithook(_hook)       # armed AFTER imports: module loading is not the journey
if SPEC["section"] == "__guard_selftest__":
    import subprocess as _sp
    for attempt in (lambda: _sp.run(["systemctl", "restart", "nonexistent-unit"], timeout=5),
                    lambda: _sp.run(["sudo", "-n", "true"], timeout=5),
                    lambda: open(os.path.expanduser("~/.tui_journey_selftest"), "w"),
                    lambda: os.system("true"),
                    # A NONEXISTENT device path: if the guard were broken this
                    # fails with FileNotFoundError and still touches no radio.
                    lambda: _sp.run(["meshtastic", "--port", "/dev/ttyMFSELFTEST0",
                                     "--info"], timeout=5),
                    lambda: open("/dev/ttyMFSELFTEST0", "rb"),
                    # signal 0 to pid 1: a no-op even if the guard were broken
                    lambda: os.kill(1, 0)):
        try:
            attempt()
        except (PermissionError, OSError, _sp.SubprocessError):
            pass
    try:                                   # a READ must still work
        open("/etc/hostname").read(); read_ok = True
    except PermissionError:
        read_ok = False
    # Our OWN subprocess timeout must still be able to kill its child — the
    # guard refusing that kill hung a journey forever (2026-09-27).
    _t0 = _t.monotonic()
    try:
        _sp.run(["sleep", "5"], timeout=1)
        timeout_ok = False
    except _sp.TimeoutExpired:
        timeout_ok = _t.monotonic() - _t0 < 3
    except PermissionError:
        timeout_ok = False
    print("__RESULT__" + json.dumps({"selftest": True, "blocked": blocked,
          "read_ok": read_ok, "timeout_ok": timeout_ok}), file=sys.__stdout__)
    sys.exit(0)
out = io.StringIO()
owned, err = False, None
if setup_error:
    print("__RESULT__" + json.dumps({"error": setup_error}), file=sys.__stdout__)
    sys.exit(0)
try:
    with contextlib.redirect_stdout(out):
        owned = registry.dispatch(SPEC["section"], SPEC["tag"])
except BaseException as e:  # report, never hide
    err = f"{type(e).__name__}: {e}"
# Read-back runs IN the child, so in sandbox mode it sees the same SimRadio
# the journey wrote to (a fresh namespace would be a fresh, empty sim).
_sim_up() if SANDBOX else None
readback = _run_all(SPEC.get("readback", []))
print("__RESULT__" + json.dumps({"owned": bool(owned), "screens": screens,
      "readback_before": readback_before, "readback_errors": readback_errors,
      "stdout": out.getvalue(), "blocked": blocked, "diverged": diverged,
      "error": err, "unused_answers": answers, "readback": readback,
      "sandbox": SANDBOX}), file=sys.__stdout__)
'''


def run_child(journey, timeout=60):
    """Dispatch one journey in a fresh interpreter. Returns the child's dict,
    or {'error': …} when it hung or produced nothing."""
    scratch = tempfile.mkdtemp(prefix="tui_journey_")
    spec = json.dumps({"section": journey["section"], "tag": journey["tag"],
                       "path": journey.get("path", []),
                       "readback": journey.get("readback", []),
                       "setup": journey.get("setup", [])})
    src = CHILD % {"src": str(SRC), "tui": str(TUI), "spec": spec,
                   "scratch": scratch}
    # No bytecode writes: a lazy import after the guard is armed would try to
    # refresh a stale .pyc and read as BLOCKED — the harness, not the journey.
    env = dict(os.environ, TMPDIR=scratch, PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-c", src]
    if journey.get("sandbox"):
        sys.path.insert(0, str(SRC))
        from utils.paths import get_real_user_home
        env["MF_REAL_HOME"] = str(get_real_user_home())
        cmd = ["unshare", "-rnm", "bash", str(REPO / "scripts" / "tui_sandbox.sh"),
               scratch] + cmd
    # Own session: on timeout the WHOLE group dies (unshare → bash → sim +
    # child); subprocess.run's timeout kills only `unshare` and orphans the
    # rest (a hung 2026-09-27 run left the sim and the child running).
    p = subprocess.Popen(cmd, cwd=str(REPO), env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        return {"error": f"did not return within {timeout}s (process group killed)"}
    line = [l for l in stdout.splitlines() if l.startswith("__RESULT__")]
    if not line:
        tail = (stderr or stdout).strip().splitlines()
        return {"error": "child produced no result: " + (tail[-1] if tail else "no output")}
    return json.loads(line[0][len("__RESULT__"):])


def screen_text(r) -> str:
    """Everything the operator would have read: titles, bodies, menu rows, stdout."""
    parts = []
    for s in r.get("screens", []):
        parts.append(f"[{s['kind']}] {s['title']}\n{s['text']}")
        parts += [f"  {t}  {l}" for t, l in s["choices"]]
    if r.get("stdout", "").strip():
        parts.append("[stdout]\n" + r["stdout"])
    return "\n".join(parts)


def oracle(argv, timeout=15) -> str:
    """Run an independent command (list argv, no shell). Output or '' on failure;
    a check that needs the output must treat '' as UNKNOWN, not as agreement."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout if p.returncode == 0 else ""


def judge(journey, r) -> tuple[str, list]:
    """Pure-ish verdict for one journey result. Returns (verdict, notes)."""
    if r.get("error"):
        return "ERROR", [r["error"]]
    if not r.get("owned"):
        return "ERROR", ["no handler owns this tag"]
    if r.get("blocked"):
        return "BLOCKED", r["blocked"]
    if r.get("diverged") or r.get("unused_answers"):
        return "DIVERGED", (r.get("diverged") or []) + \
            [f"unused answers: {r.get('unused_answers')}"] * bool(r.get("unused_answers"))
    errs = [s["title"] for s in r["screens"]
            if s["kind"] == "msgbox" and s["title"] in SAFE_CALL_ERROR_TITLES]
    if errs:
        return "ERROR", [f"safe_call rescued a crash: {errs}"]
    if journey.get("sandbox") and not r.get("sandbox"):
        return "ERROR", ["journey marked sandbox but the child did not run in one"]
    ask = oracle
    if journey.get("sandbox"):
        # Oracle = what the child read back from the SAME SimRadio it wrote;
        # a host command here would ask the REAL radio.
        rb, before = r.get("readback") or {}, r.get("readback_before") or {}

        def ask(argv, timeout=15):
            # ["BEFORE", *argv] asks for the state captured before the action
            if argv and argv[0] == "BEFORE":
                return before.get(" ".join(argv[1:]), "")
            return rb.get(" ".join(argv), "")
    text = screen_text(r)
    results = journey["check"](text, ask)
    if not results:
        return "ERROR", ["check returned no results — nothing was judged"]
    unknown = [m for ok, m in results if ok is None]
    failed = [m for ok, m in results if ok is False]
    if failed:
        return "FAIL", failed + unknown
    if unknown:
        return "UNKNOWN", unknown
    lie = journey["plant"](text)
    if lie == text:
        return "UNFALSIFIED", ["plant() changed nothing — the control never ran"]
    caught = [m for ok, m in journey["check"](lie, ask) if ok is False]
    if not caught:
        return "UNFALSIFIED", ["the planted lie PASSED the check — the oracle cannot fail"]
    return "PASS", [m for _, m in results] + [f"control: lie caught — {caught[0]}"]


# systemctl restart, sudo, open-for-write in ~, os.system, meshtastic
# without --host localhost, open of a serial device, os.kill of a non-child
GUARD_EXPECTED = 7


def selftest() -> int:
    """A guard that has never refused is not evidence. Plant GUARD_EXPECTED
    writes inside a guarded child; all must be BLOCKED and a plain read must
    still pass."""
    r = run_child({"section": "__guard_selftest__", "tag": "", "path": []})
    blocked = r.get("blocked") or []
    ok = (r.get("selftest") and len(blocked) == GUARD_EXPECTED and r.get("read_ok")
          and r.get("timeout_ok"))
    for b in blocked:
        print(f"  refused  {b}")
    print(f"guard selftest: {len(blocked)}/{GUARD_EXPECTED} writes refused, "
          f"read {'ok' if r.get('read_ok') else 'BROKEN'}, own-timeout "
          f"{'ok' if r.get('timeout_ok') else 'HANGS/BROKEN'} — {'PASS' if ok else 'FAIL'}"
          + (f" ({r['error']})" if r.get("error") else ""))
    return 0 if ok else 1


def main(argv=None):
    from tui_journeys import JOURNEYS
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="one journey name")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dump", action="store_true",
                    help="print each journey's captured screen text")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--selftest", action="store_true",
                    help="prove the write guard REFUSES before trusting it")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    js = [j for j in JOURNEYS if not args.only or j["name"] == args.only]
    if args.list:
        for j in JOURNEYS:
            print(f"  {j['name']:<24} {j['section']}/{j['tag']}  — {j['why']}")
        return 0
    if not js:
        print(f"UNKNOWN: no journey named {args.only!r}", file=sys.stderr)
        return 2
    verdicts = []
    for j in js:
        r = run_child(j, args.timeout)
        v, notes = judge(j, r)
        verdicts.append(v)
        print(f"{v:<12} {j['name']:<24} {j['section']}/{j['tag']}")
        for n in notes:
            print(f"    {n}")
        if args.dump:
            print("    --- screen ---")
            print("\n".join("    " + l for l in screen_text(r).splitlines()))
            for e in r.get("readback_errors") or []:
                print(f"    --- readback ERROR: {e}")
            for label in ("readback_before", "readback"):
                for k, v in (r.get(label) or {}).items():
                    print(f"    --- {label}: {k}")
                    print("\n".join("      " + l for l in v.splitlines()[:40]))
    passed = verdicts.count("PASS")
    rest = ", ".join(f"{verdicts.count(v)} {v}" for v in sorted(set(verdicts)) if v != "PASS")
    print(f"\n{passed}/{len(verdicts)} PASS" + (f" — {rest}" if rest else ""))
    return 0 if passed == len(verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
