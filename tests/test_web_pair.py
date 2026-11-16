"""BIC-2 slice: re-pair a browser FROM THE TUI (operator go 2026-11-16).

Before this, a phone that lost its operator login was told — by the 401, the
login page AND the TUI — to run a shell command on the box. A ham without a
shell (or without Claude) could not get back in (standalone-user bar).

The trap this pins: the TUI usually runs under sudo, and a pairing code minted
as root leaves a root-owned 0600 ``web_sessions.json`` + ``.lock`` that the
map (``User=`` the operator) cannot open — every browser locked out, worse
than today. So the TUI never mints in-process: it runs the ONE minting
implementation (``scripts/meshforge_web.py pair``) as the map unit's
``User=``, read from systemd at the time, and refuses — saying why — when that
user cannot be resolved. After minting it re-reads the MAP's store and shows
the code only if that store holds THIS code's hash (the consumer's end state)
and its files are the map user's own.

Round-1 review (2026-11-16, non-author): live-store reads in the TUI tests,
status vs mint reading different stores, a missing unit read as "runs as
root", success without the code landing, the timeout orphan, path drift,
numeric User=, a clipped fallback, "TUI" said twice — each pinned below.
"""
import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

import pytest

from utils import web_auth, web_pair

# R6-2: every fixture plays "the map user = me"; as root the code correctly
# refuses a root map user, so this module cannot run as uid 0 (its sibling
# test_web_auth.py skips there too).
pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="fixtures need a non-root map user")

ME = pwd.getpwuid(os.getuid()).pw_name
MY_UID = os.getuid()
TESTS = os.path.dirname(__file__)
SRC_TUI = os.path.join(TESTS, "..", "src", "launcher_tui")
for _p in (TESTS, SRC_TUI):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Runner:
    """subprocess.run stand-in: records argv, replays queued results."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        r = self.results.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def show(user=ME, load="loaded", active="active", main_pid=os.getpid()):
    # Default MainPID = THIS test process (runs as ME): the real /proc uid check
    # runs in every test and matches, instead of being skipped (r7-3).
    """What `systemctl show UNIT -p LoadState -p User -p ActiveState -p MainPID` prints."""
    return Proc(0, f"LoadState={load}\nUser={user}\nActiveState={active}\nMainPID={main_pid}\n")


@pytest.fixture(autouse=True)
def no_live_store(monkeypatch):
    """F1: nothing here may read or write the operator's REAL session store."""
    def boom():
        raise AssertionError("test touched the live session store (default_store_path)")
    monkeypatch.setattr(web_auth, "default_store_path", boom)


# ── map_unit_user: the map's User=, from systemd, never guessed ────────────

def test_map_unit_user_reads_systemd_show_read_only():
    run = Runner(show())
    user, why = web_pair.map_unit_user(run=run)
    assert user == ME and why == ""
    argv, kw = run.calls[0]
    assert argv == ["systemctl", "show", web_pair.MAP_UNIT,
                    "-p", "LoadState", "-p", "User", "-p", "ActiveState", "-p", "MainPID"]
    assert "sudo" not in argv            # read-only query: never elevated
    assert kw.get("timeout")              # MF004


@pytest.mark.parametrize("user", ["", "root"])
def test_map_unit_user_refuses_root_or_unset(user):
    u, why = web_pair.map_unit_user(run=Runner(show(user=user)))
    assert u is None and "root" in why


@pytest.mark.parametrize("load", ["not-found", "masked", "error"])
def test_missing_unit_says_not_installed_not_root(load):
    """F3: `systemctl show` on a missing unit exits 0 with User= empty."""
    u, why = web_pair.map_unit_user(run=Runner(show(user="", load=load)))
    assert u is None
    assert "not installed" in why and load in why
    assert "runs as root" not in why


def test_load_state_absent_is_unobservable_not_root():
    u, why = web_pair.map_unit_user(run=Runner(Proc(0, "User=\n")))
    assert u is None and "runs as root" not in why


def test_numeric_user_resolves_as_a_uid():
    """F7: systemd accepts User=<uid>."""
    u, why = web_pair.map_unit_user(run=Runner(show(user=str(MY_UID))))
    if MY_UID == 0:
        assert u is None
    else:
        assert u == ME, why


def test_numeric_uid_not_in_passwd_is_refused():
    u, why = web_pair.map_unit_user(run=Runner(show(user="4000000000")))
    assert u is None and "passwd" in why


@pytest.mark.parametrize("res", [
    Proc(1, "", "Failed to connect to bus"),
    subprocess.TimeoutExpired(["systemctl"], 5),
    FileNotFoundError("systemctl"),
])
def test_map_unit_user_unobservable_is_none_with_reason(res):
    user, why = web_pair.map_unit_user(run=Runner(res))
    assert user is None and why


@pytest.mark.parametrize("bad", ["../etc", "a/b", "two words", "-n", "x" * 40])
def test_map_unit_user_rejects_names_that_cannot_be_a_user(bad):
    user, why = web_pair.map_unit_user(run=Runner(show(user=bad)))
    assert user is None and why


@pytest.mark.parametrize("name", ["WH6GXZ", "john.doe", "Operator", "svc$"])
def test_real_world_user_names_reach_the_passwd_lookup(name):
    """R2-F2: callsign-style and dotted names are legal for systemd + passwd;
    a refusal must say "not in passwd", never "not a valid user name"."""
    user, why = web_pair.map_unit_user(run=Runner(show(user=name)))
    assert user is None and "passwd" in why and "valid" not in why


def test_map_unit_user_rejects_a_user_not_in_passwd():
    user, why = web_pair.map_unit_user(run=Runner(show(user="mf-no-such-user-x")))
    assert user is None and "passwd" in why


# ── build_pair_argv: the CLI, run as the map's user, env pinned ────────────

BOUND = ["timeout", "-k", str(web_pair.CLI_KILL_GRACE_S), str(web_pair.CLI_TIMEOUT_S)]


def test_root_hops_to_the_map_user_without_a_password_prompt():
    argv, why = web_pair.build_pair_argv("op", euid=0, me="root", python="/py")
    assert argv == ["sudo", "-n", "-u", "op", "-H"] + BOUND + [
        "env", "SUDO_USER=op", "LOGNAME=op", "/py", str(web_auth.WEB_CLI), "pair"]
    assert why == ""


def test_running_as_the_map_user_still_pins_the_store_owner_env():
    """F4: an inherited SUDO_USER/LOGNAME must not steer CONFIG_DIR elsewhere."""
    argv, _ = web_pair.build_pair_argv("op", euid=1000, me="op", python="/py")
    assert argv == BOUND + ["env", "SUDO_USER=op", "LOGNAME=op",
                            "/py", str(web_auth.WEB_CLI), "pair"]


def test_the_cli_is_killed_inside_sudo_before_the_tui_stops_waiting():
    """R2-P1: killing sudo never reaches the CLI behind its pty, so the bound
    lives INSIDE the sudo argv and must fire (plus its kill grace) before the
    outer wait gives up — no CLI outlives the TUI's answer."""
    assert web_pair.CLI_TIMEOUT_S + web_pair.CLI_KILL_GRACE_S < web_pair.PAIR_TIMEOUT_S


def test_a_different_non_root_user_is_refused_with_the_command():
    argv, why = web_pair.build_pair_argv("op", euid=1001, me="guest", python="/py")
    assert argv is None
    assert "op" in why and "guest" in why and "pair" in why


# ── parse_code: only a code from the real alphabet counts ──────────────────

def test_parse_code_reads_the_cli_line():
    out = "Pairing code:  ABCD-EF23\nValid for 5 minutes, once;\n"
    assert web_pair.parse_code(out) == "ABCD-EF23"


@pytest.mark.parametrize("out", ["", "garbage", "Pairing code:  ABC0-EFGH\n",
                                 "Pairing code:  ABCD-EFG\n"])
def test_parse_code_rejects_anything_else(out):
    assert web_pair.parse_code(out) is None


# ── check_store: owner/mode AND this code's hash, in the MAP's store ───────

CODE = "ABCD-EF23"


def _store(tmp_path, code=CODE, mode=0o600, lock_mode=0o600, name="web_sessions.json"):
    p = tmp_path / name
    pairing = None if code is None else {
        "h": web_auth._h(web_auth._normalize_code(code)), "expires": 9e12, "tries": 0}
    p.write_text(json.dumps({"sessions": [], "pairing": pairing}))
    os.chmod(p, mode)
    lk = tmp_path / (name + ".lock")
    lk.write_text("")
    os.chmod(lk, lock_mode)
    return p


def test_store_holding_this_code_passes(tmp_path):
    ok, why = web_pair.check_store(ME, _store(tmp_path), CODE)
    assert ok, why


def test_store_holding_a_different_code_fails(tmp_path):
    """F4: a correctly owned but STALE store is not success."""
    ok, why = web_pair.check_store(ME, _store(tmp_path, code="ZZZZ-ZZZZ"), CODE)
    assert not ok and "does not hold" in why


def test_store_with_no_pending_code_fails(tmp_path):
    ok, why = web_pair.check_store(ME, _store(tmp_path, code=None), CODE)
    assert not ok and "does not hold" in why


def test_corrupt_store_fails_and_is_not_moved(tmp_path):
    p = _store(tmp_path)
    p.write_text("{not json")
    os.chmod(p, 0o600)
    ok, why = web_pair.check_store(ME, p, CODE)
    assert not ok and p.exists()          # a check never renames the store


def test_store_owned_by_someone_else_fails(tmp_path):
    ok, why = web_pair.check_store("root", _store(tmp_path), CODE)
    assert not ok and "root" in why


def test_missing_store_fails(tmp_path):
    ok, why = web_pair.check_store(ME, tmp_path / "web_sessions.json", CODE)
    assert not ok and why


def test_symlinked_store_fails(tmp_path):
    real = _store(tmp_path)
    link = tmp_path / "linked.json"
    link.symlink_to(real)
    lk = tmp_path / "linked.json.lock"
    lk.write_text("")
    os.chmod(lk, 0o600)                 # the link is the ONLY defect here
    ok, why = web_pair.check_store(ME, link, CODE)
    assert not ok and "regular file" in why


def test_world_readable_store_fails(tmp_path):
    ok, why = web_pair.check_store(ME, _store(tmp_path, mode=0o644), CODE)
    assert not ok and "mode" in why


def test_lock_not_a_regular_file_fails(tmp_path):
    p = _store(tmp_path)
    os.unlink(str(p) + ".lock")
    (tmp_path / "web_sessions.json.lock").mkdir()
    ok, why = web_pair.check_store(ME, p, CODE)
    assert not ok and "lock" in why


# ── pair_browser: end to end over the seams ───────────────────────────────

CODE_OUT = f"Pairing code:  {CODE}\nValid for 5 minutes, once;\n"


def test_pair_browser_shows_the_code_when_everything_holds(tmp_path):
    store = _store(tmp_path)
    run = Runner(show(), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=store)
    assert r.ok and r.code == CODE, r.message
    assert run.calls[1][0][:4] == ["sudo", "-n", "-u", ME]
    assert run.calls[1][1].get("timeout")
    assert run.calls[1][1].get("stdin") is subprocess.DEVNULL     # R2-P2: never the TUI's tty


def test_a_cli_crash_shows_its_last_line_not_the_traceback_head(tmp_path):
    """R2-F1: the exception is the LAST line of a traceback."""
    tb = ("Traceback (most recent call last):\n"
          + "".join(f'  File "/opt/x/mod{i}.py", line {i}, in f{i}\n    call_{i}()\n' for i in range(12))
          + "PermissionError: [Errno 13] Permission denied: '/home/op/.config/meshforge/web_sessions.json.lock'\n")
    run = Runner(show(), Proc(1, "", tb))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    assert not r.ok and "PermissionError: [Errno 13]" in r.message
    assert "Traceback" not in r.message


@pytest.mark.parametrize("rc", [124, 137, -9, -15])
def test_the_inner_bound_firing_says_stopped_and_pair_again(tmp_path, rc):
    ticks = iter([0.0, web_pair.CLI_TIMEOUT_S + web_pair.CLI_KILL_GRACE_S + 0.1])
    run = Runner(show(), Proc(rc, CODE_OUT, ""))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path),
                              clock=lambda: next(ticks))
    assert not r.ok and r.code is None
    assert "stopped" in r.message and "pair again" in r.message.lower()
    assert CODE not in r.message          # a half-printed code is never shown as a "reason"


def test_a_refusal_reason_never_comes_from_stdout(tmp_path):
    """R3-C1: stdout can carry a code line; the reason is stderr's last line only."""
    run = Runner(show(), Proc(1, CODE_OUT, ""))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    assert not r.ok and CODE not in r.message and "no error text" in r.message


FAKE_CLI = """
import signal, sys, time
print("Pairing code:  ABCD-EF23", flush=True)
if sys.argv[1] == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(30)
"""


@pytest.mark.parametrize("mode", ["ignore-term", "obey-term"])
def test_the_REAL_timeout_tool_is_read_as_stopped(tmp_path, monkeypatch, mode):
    """R3-C1: pin the TOOL, not a model of it. coreutils `timeout -k` that
    must KILL dies with its child (rc -9 to subprocess); a TERM'd one gives 124."""
    fake = tmp_path / "fake_cli.py"
    fake.write_text(FAKE_CLI)
    monkeypatch.setattr(web_pair, "CLI_TIMEOUT_S", 1)
    monkeypatch.setattr(web_pair, "CLI_KILL_GRACE_S", 1)
    seen = {}

    def run(argv, **kw):
        if argv[0] == "systemctl":
            return show()
        real = argv[:-3] + [sys.executable, str(fake), mode]   # keep the real `timeout` prefix
        assert real[:4] == ["timeout", "-k", "1", "1"]
        p = subprocess.run(real, **kw)
        seen["rc"] = p.returncode
        return p

    r = web_pair.pair_browser(run=run, euid=os.geteuid() or 1, me=ME, store_path=_store(tmp_path))
    assert seen["rc"] in ((-9, 137) if mode == "ignore-term" else (124,)), seen
    assert not r.ok and r.code is None and "stopped" in r.message, r.message


# ── R4 / the exit-status KIND three times (137, -9, fast signal deaths) ──────
# One classifier, tested over the WHOLE input space: rc × elapsed × stderr.
# B = the bound (CLI_TIMEOUT_S). "kind" is the classification; "has"/"not"
# are substrings the operator must / must never see.
B = web_pair.CLI_TIMEOUT_S
EXIT_TABLE = [
    # rc,   elapsed, stderr,                        kind,      has,                               not
    (0,     0.2,  "",                              "ok",      [],                                []),
    (124,   B + .1, "",                            "timeout", [f"{B}s", "pair again"],          ["signal"]),
    (-9,    B + 2.1, "",                           "timeout", [f"{B}s", "pair again"],          ["signal"]),
    (137,   B + 2.1, "",                           "timeout", [f"{B}s", "pair again"],          ["signal"]),
    (-15,   B + .1, "",                            "timeout", [f"{B}s"],                         ["signal"]),
    (-11,   0.01, "",                              "signal",  ["SIGSEGV", "0.0s", "pair again"], [f"after {B}s"]),
    (-9,    0.01, "",                              "signal",  ["SIGKILL", "pair again"],         [f"after {B}s"]),
    (-2,    0.3,  "",                              "signal",  ["SIGINT"],                        [f"after {B}s"]),
    (137,   0.02, "",                              "signal",  ["SIGKILL"],                       [f"after {B}s"]),
    (139,   0.02, "Segmentation fault",            "signal",  ["SIGSEGV", "Segmentation fault"], []),
    (124,   0.02, "real reason: disk full",        "refused", ["rc 124", "disk full"],           [f"after {B}s"]),
    (1,     0.1,  "Traceback\n  x\nOSError: boom", "refused", ["rc 1", "OSError: boom"],         ["Traceback"]),
    (2,     0.1,  "UNKNOWN: store corrupt",        "refused", ["rc 2", "store corrupt"],         []),
    (1,     0.1,  "",                              "refused", ["rc 1", "no error text"],         []),
    (-11,   0.01, "fatal: oops",                   "signal",  ["SIGSEGV", "fatal: oops"],        []),
    (-9,    B + 2.1, "late words",                 "timeout", ["late words"],                    ["signal"]),
]


@pytest.mark.parametrize("rc,elapsed,stderr,kind,has,never", EXIT_TABLE)
def test_exit_classification_over_the_whole_table(rc, elapsed, stderr, kind, has, never):
    got_kind, msg = web_pair.classify_exit(rc, elapsed, stderr)
    assert got_kind == kind, (rc, elapsed, msg)
    for s in has:
        assert s in msg, (s, msg)
    for s in never:
        assert s not in msg, (s, msg)
    assert CODE not in msg


def test_pair_browser_times_the_call_with_a_monotonic_clock(tmp_path):
    ticks = iter([100.0, 100.01])
    run = Runner(show(), Proc(-11, CODE_OUT, ""))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path),
                              clock=lambda: next(ticks))
    assert not r.ok and "SIGSEGV" in r.message and f"after {B}s" not in r.message


def test_child_output_is_decoded_with_replacement(tmp_path):
    """R4-3: strict decoding raised UnicodeDecodeError out of the action."""
    run = Runner(show(), Proc(1, "", "boom ��"))
    web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    for argv, kw in run.calls:
        assert kw.get("errors") == "replace" and kw.get("encoding") == "utf-8", (argv, kw)


def test_real_undecodable_bytes_do_not_escape(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import sys; sys.stderr.buffer.write(b'boom \\xff\\xfe\\n'); sys.exit(1)")

    def run(argv, **kw):
        if argv[0] == "systemctl":
            return show()
        return subprocess.run([sys.executable, str(bad)], **kw)
    r = web_pair.pair_browser(run=run, euid=os.geteuid() or 1, me=ME, store_path=_store(tmp_path))
    assert not r.ok and "boom" in r.message


# ActiveState is a CLOSED vocabulary (hfm #7): every value gets a verdict.
ACTIVE_TABLE = [
    ("active", None), ("reloading", None),
    ("activating", "starting"),
    ("inactive", "not running"), ("failed", "not running"), ("deactivating", "not running"),
    ("maintenance", "not running"), (None, "not running"), ("weird-new-state", "not running"),
]


@pytest.mark.parametrize("state,word", ACTIVE_TABLE)
def test_active_state_note_over_the_whole_vocabulary(state, word):
    note = web_pair.map_running_note(state)
    if word is None:
        assert note == ""
    else:
        assert word in note and str(state or "unknown") in note


def test_my_name_survives_a_uid_with_no_passwd_row(monkeypatch):
    """R4-5: containers can run a uid with no passwd row."""
    def missing(uid):
        raise KeyError(uid)
    monkeypatch.setattr(web_pair.pwd, "getpwuid", missing)
    assert web_pair.my_name() == str(os.getuid())


# ── option B (operator 2026-11-16, after review r5 ran option A) ─────────────
# No meshforge-map unit = no web pairing. The TUI's in-process map binds
# 127.0.0.1 and trusts loopback only (no phone can reach /login), and a root
# TUI minting into it left a root-owned store in the operator's home.

# LoadState is a closed-ish vocabulary; each non-loaded state gets the remedy
# that fits it (r7-4: masked / bad-setting were told to "install").
LOAD_TABLE = [("not-found", "install"), ("masked", "unmask"), ("error", "unit"),
              ("bad-setting", "unit"), ("stub", "unit"), ("merged", "unit"), ("", "unit"),
              ("weird-new-state", "unit")]


@pytest.mark.parametrize("load,kind", LOAD_TABLE)
def test_no_unit_refuses_with_the_true_reason_and_mints_nothing(tmp_path, load, kind):
    run = Runner(show(user="", load=load))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=tmp_path / "s.json")
    assert not r.ok and r.code is None
    assert r.remedy == kind and f"LoadState={load}" in r.message
    assert len(run.calls) == 1                       # no CLI, no sudo, no in-process mint
    assert list(tmp_path.iterdir()) == []            # nothing written anywhere


# R6-1: the remedy comes FROM the refusal, through ONE closed table. The shell
# fallback is offered only where it can work. R8 (a subtraction): no remedy
# prescribes a command about files it has not inspected, none carries an
# absolute path, and none is longer than two lines.
URL = "http://192.0.2.7:5000/login"
REMEDY_TABLE = [
    # kind,         must show,                                               must never show
    ("install",    ["Install the map service", "local-only", "scripts/install_noc.sh"],
                                                                             [web_auth.PAIR_COMMAND]),
    ("unmask",     ["masked", "sudo systemctl unmask meshforge-map"],        [web_auth.PAIR_COMMAND]),
    ("unit",       ["cannot load", "systemctl status meshforge-map"],        [web_auth.PAIR_COMMAND]),
    ("migrate",    ["sudo bash scripts/migrate_map_service_to_user.sh"],     [web_auth.PAIR_COMMAND]),
    ("restart",    ["Restart", "sudo systemctl restart meshforge-map"],      [web_auth.PAIR_COMMAND]),
    ("permission", ["the wrong owner", "or permissions", "see Why"],
                                                    [web_auth.PAIR_COMMAND, "chown", "chmod", "sudo"]),
    ("shell",      [web_auth.PAIR_COMMAND, URL],                             []),
]


def test_the_remedy_table_is_closed_and_complete():
    assert {k for k, _h, _n in REMEDY_TABLE} == set(web_pair.REMEDIES)


@pytest.mark.parametrize("kind,has,never", REMEDY_TABLE)
def test_remedy_text_over_the_whole_table(kind, has, never):
    text = web_pair.remedy_text(kind, URL)
    for s in has:
        assert s in text, (kind, s, text)
    for s in never:
        assert s not in text, (kind, s, text)
    assert "{" not in text


@pytest.mark.parametrize("kind", [k for k, _h, _n in REMEDY_TABLE if k != "shell"])
def test_remedies_are_short_and_path_free(kind):
    """R8-1/R8-4: <= 2 lines of <= 66 columns, no absolute path (the checkout
    can live anywhere — a CI runner path made the old width test lie)."""
    lines = web_pair.remedy_text(kind, URL).splitlines()
    assert len(lines) <= 2, (kind, lines)
    for line in lines:
        assert len(line) <= 66, (kind, len(line), line)
        assert "/opt/" not in line and "/home/" not in line, line


# R8-4: what must fit is the WHOLE status text, in ROWS, inside the real menu
# with the TUI's status bar: backend.menu keeps 22 rows, 4 list rows for 2
# choices -> 11 text rows of 68 columns. Driven by the REAL map_unit strings.
MENU_TEXT_ROWS, MENU_TEXT_COLS = 11, 68
REVOKE_AT_OPT = "python3 /opt/meshforge/scripts/meshforge_web.py revoke-all"   # the deployed path
STATUS_CASES = {
    "install": (show(user="", load="not-found"), None),
    "unmask":  (show(user="", load="masked"), None),
    "unit":    (show(user="", load="bad-setting"), None),
    "migrate": (show(user="root"), None),
    "restart": (show(main_pid=4242), lambda pid: 0),
}


def _rows(text):
    return sum(max(1, -(-len(line) // MENU_TEXT_COLS)) for line in text.splitlines())


@pytest.mark.parametrize("kind", sorted(STATUS_CASES))
def test_every_status_remedy_fits_the_real_menu_in_rows(monkeypatch, tmp_path, kind):
    out, reader = STATUS_CASES[kind]
    if reader is not None:
        monkeypatch.setattr(web_pair, "proc_uid", reader)
    m = web_pair.map_unit(run=Runner(out))
    assert m.user is None and m.remedy == kind, m
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["back"],
                      map_user=(None, m.why), remedy=m.remedy)
    monkeypatch.setattr(web_auth, "REVOKE_COMMAND", REVOKE_AT_OPT)
    h.execute("weblogin")
    text = _texts(dlg, "menu")[0]
    assert web_pair.remedy_text(kind, "") in text
    assert _rows(text) <= MENU_TEXT_ROWS, (kind, _rows(text), text)


# R7-1: a store/lock (or its folder) the map user cannot open gets the
# permission remedy, never the shell command (which fails identically) — and,
# since r8, never a prescribed chown (it followed planted symlinks).
PERM_STDERR = [
    (1, "Traceback (most recent call last):\n  x\nPermissionError: [Errno 13] Permission denied: "
        "'/home/op/.config/meshforge/web_sessions.json.lock'"),
    (2, "UNKNOWN: the operator session store /home/op/.config/meshforge/web_sessions.json is "
        "unreadable (PermissionError); fix its owner/mode (0600, the map's user), then run: x"),
    (1, "Traceback (most recent call last):\n  x\nPermissionError: [Errno 13] Permission denied: "
        "'/home/op/.config/meshforge/.web_sessions.abc123'"),       # the FOLDER is the fault
]


@pytest.mark.parametrize("rc,stderr", PERM_STDERR)
def test_a_permission_refusal_gets_the_permission_remedy(tmp_path, rc, stderr):
    run = Runner(show(), Proc(rc, "", stderr))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    assert not r.ok and r.remedy == "permission"


def test_an_owner_or_mode_fault_after_minting_gets_the_permission_remedy(tmp_path):
    store = _store(tmp_path, mode=0o644)
    run = Runner(show(), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=store)
    assert not r.ok and r.remedy == "permission"


def test_a_store_owned_by_another_user_is_a_permission_fault(tmp_path):
    """The owner branch (a non-root test cannot chown, so the MAP user differs)."""
    ok, why, kind = web_pair._check_store("root", _store(tmp_path), CODE)
    assert not ok and kind == "permission" and "owned by" in why


def test_no_remedy_ever_prescribes_chown_or_chmod():
    """R8-3: a printed chown followed a planted symlink. Never again."""
    for kind, text in web_pair.REMEDIES.items():
        assert "chown" not in text and "chmod" not in text, kind


def test_tui_failure_box_shows_the_permission_remedy(monkeypatch, tmp_path):
    h, dlg = _handler(monkeypatch, tmp_path,
                      web_pair.PairResult(False, None, "pairing refused (rc 1): PermissionError",
                                          remedy="permission"), ["pair", "back"])
    h.execute("weblogin")
    text = _texts(dlg, "msgbox")[0]
    assert web_pair.remedy_text("permission", "") in text and "sudo" not in text

def test_a_0644_lock_owned_by_the_map_user_is_accepted(tmp_path):
    """R9-1: the map's own redeem works with a 0644 lock (an empty file, no
    secret); refusing it was stricter than the consumer AND looped (pairing
    never changes the lock's mode). Only the STORE, which holds hashes, must be 0600."""
    store = _store(tmp_path, lock_mode=0o644)
    ok, why = web_pair.check_store(ME, store, CODE)
    assert ok, why
    run = Runner(show(), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=store)
    assert r.ok, r.message
    assert web_auth.SessionStore(store).redeem(CODE)     # the consumer agrees


def test_a_code_in_the_wrong_store_is_not_a_permission_fault(tmp_path):
    run = Runner(show(), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path, code="ZZZZ-ZZZZ"))
    assert not r.ok and r.remedy == "shell"


def test_a_plain_refusal_keeps_the_shell_remedy(tmp_path):
    run = Runner(show(), Proc(2, "", "UNKNOWN: the operator session store was corrupt and moved"))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    assert not r.ok and r.remedy == "shell"


def test_running_map_without_a_main_pid_is_noted_not_assumed(tmp_path):
    """R7-3: active with MainPID=0 must not skip the uid check silently."""
    run = Runner(show(main_pid=0), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path),
                              proc_uid=lambda pid: pytest.fail("no pid to read"))
    assert r.ok and "could not confirm" in r.message and "MainPID" in r.message


def test_patching_the_module_proc_uid_reaches_map_unit(monkeypatch):
    """r7 plausible: a default-bound seam is a trap for the next test author."""
    monkeypatch.setattr(web_pair, "proc_uid", lambda pid: 0)
    m = web_pair.map_unit(run=Runner(show(main_pid=4242)))
    assert m.user is None and m.remedy == "restart"


def test_an_unknown_remedy_kind_fails_loudly():
    with pytest.raises(KeyError):
        web_pair.remedy_text("bogus", URL)


@pytest.mark.parametrize("out,kind", [
    (show(user="root"), "migrate"), (show(user=""), "migrate"),
    (show(user="mf-no-such-user-x"), "shell"),
    (Proc(1, "", "Failed to connect to bus"), "shell"),
    (subprocess.TimeoutExpired(["systemctl"], 5), "shell"),
    (show(user="", load="not-found"), "install"),
])
def test_each_refusal_carries_the_remedy_that_can_fix_it(out, kind):
    """R5-3 + R6-1: install advice only for a missing unit; a root-run unit
    names the migrate script (its CLI code would land in the wrong store)."""
    r = web_pair.pair_browser(run=Runner(out), euid=0, me="root")
    assert not r.ok and r.remedy == kind, (r.remedy, r.message)


# R6-P: the CONSUMER, not its config. `User=` can be changed + daemon-reloaded
# while the old process still serves the old user's store.

def test_running_map_with_a_different_uid_is_refused_before_minting(tmp_path):
    run = Runner(show(main_pid=4242))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path),
                              proc_uid=lambda pid: 0 if pid == 4242 else None)
    assert not r.ok and r.remedy == "restart"
    assert "4242" in r.message and "uid 0" in r.message
    assert len(run.calls) == 1                       # the CLI never ran


def test_running_map_with_the_configured_uid_pairs(tmp_path):
    run = Runner(show(main_pid=4242), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path),
                              proc_uid=lambda pid: MY_UID)
    assert r.ok, r.message


def test_an_unreadable_process_is_noted_not_assumed(tmp_path):
    """The pid raced away (restart in flight): the check is UNKNOWN, said so;
    the store-hash check still guards the result."""
    run = Runner(show(main_pid=4242), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path),
                              proc_uid=lambda pid: None)
    assert r.ok and "could not confirm" in r.message


def test_proc_uid_reads_the_real_uid_of_a_live_process():
    assert web_pair.proc_uid(os.getpid()) == os.getuid()
    assert web_pair.proc_uid(0) is None and web_pair.proc_uid(2 ** 31) is None


def test_option_a_is_gone_for_good():
    """Option A (mint into a TUI-hosted in-process map) proved unreachable
    from a phone and a root-owned-store lockout. Nothing of it may return."""
    for name in ("register_in_process_map", "in_process_store", "login_port", "_pair_in_process"):
        assert not hasattr(web_pair, name), name
    src = Path(SRC_TUI, "handlers", "ai_tools.py").read_text()
    assert "web_pair" not in src


def test_a_stopped_map_still_mints_but_says_it_is_not_running(tmp_path):
    """A box whose map is off by design: the code is real, the URL won't load
    until the map runs — say so instead of a dead end."""
    run = Runner(show(active="inactive"), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    assert r.ok and r.code == CODE
    assert "not running" in r.message and "inactive" in r.message


def test_pair_browser_never_mints_when_the_map_user_is_unknown(tmp_path):
    run = Runner(show(user=""))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=tmp_path / "s.json")
    assert not r.ok and r.code is None and "root" in r.message
    assert len(run.calls) == 1               # the minting CLI never ran


def test_pair_browser_never_mints_when_the_unit_is_missing(tmp_path):
    run = Runner(show(user="", load="not-found"))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=tmp_path / "s.json")
    assert not r.ok and "not installed" in r.message and len(run.calls) == 1


def test_pair_browser_surfaces_the_cli_refusal(tmp_path):
    run = Runner(show(), Proc(2, "", "UNKNOWN: the operator session store was corrupt"))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    assert not r.ok and r.code is None
    assert "corrupt" in r.message


def test_pair_browser_sudo_needing_a_password_is_named(tmp_path):
    run = Runner(show(), Proc(1, "", "sudo: a password is required"))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    assert not r.ok and "password" in r.message


def test_pair_browser_outer_timeout_says_pair_again(tmp_path):
    """Outer wait expired (sudo itself stalled): the CLI is bounded inside, and
    whatever it made was never shown — say so, and what to do."""
    run = Runner(show(), subprocess.TimeoutExpired(["py"], 20))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=_store(tmp_path))
    assert not r.ok and r.code is None
    assert "never shown" in r.message and "pair again" in r.message.lower()


def test_pair_browser_code_not_in_the_map_store_withholds_success(tmp_path):
    store = _store(tmp_path, code="ZZZZ-ZZZZ")
    run = Runner(show(), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=store)
    assert not r.ok and r.code == CODE and "does not hold" in r.message


def test_pair_browser_wrong_store_mode_withholds_success(tmp_path):
    store = _store(tmp_path, mode=0o644)
    run = Runner(show(), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root", store_path=store)
    assert not r.ok and "mode" in r.message


def test_pair_browser_default_store_is_the_map_users(monkeypatch):
    """With no override, the store checked is the MAP user's, not the TUI's."""
    seen = {}
    monkeypatch.setattr(web_pair, "_check_store",
                        lambda u, p, c: (seen.update(u=u, p=p), (True, "", "shell"))[1])
    run = Runner(show(), Proc(0, CODE_OUT))
    r = web_pair.pair_browser(run=run, euid=0, me="root")
    assert r.ok
    assert str(seen["p"]).startswith(pwd.getpwnam(ME).pw_dir)
    assert seen["p"].name == "web_sessions.json"


# ── the doors name ONE path, built from the constants the TUI renders ──────

def test_tui_path_is_built_from_the_three_rendered_constants():
    assert web_pair.PAIR_TUI_PATH == (
        f"{web_pair.PAIR_TUI_SECTION} > {web_pair.PAIR_TUI_LABEL} > {web_pair.PAIR_TUI_ACTION}")
    assert "TUI" not in web_pair.PAIR_TUI_PATH          # F9: the doors say "TUI" once


def test_the_about_section_title_is_the_one_the_path_names():
    main_src = Path(SRC_TUI, "main.py").read_text()
    assert f'("{web_pair.PAIR_TUI_SECTION}", self._about_menu)' in main_src
    # R2-F5: the row the operator SEES on the main menu, not only the dispatch tuple
    assert re.search(r'\("a", "%s\s' % re.escape(web_pair.PAIR_TUI_SECTION), main_src)


def test_store_access_problem_only_for_a_non_root_stranger():
    """R2-F3: a TUI user who cannot read the map's store is told THAT, not to
    'fix' a healthy file."""
    why = web_pair.store_access_problem("op", euid=1001, me="guest")
    assert why and "guest" in why and "op" in why and "sudo" in why
    assert web_pair.store_access_problem("op", euid=0, me="root") is None
    assert web_pair.store_access_problem("op", euid=1000, me="op") is None


def test_tui_menu_label_is_the_one_the_doors_name():
    from handlers.web_client import WebClientHandler
    h = WebClientHandler()
    assert h.menu_section == "about"
    rows = {tag: lbl for tag, lbl, _f in h.menu_items()}
    assert rows["weblogin"].startswith(web_pair.PAIR_TUI_LABEL)


def test_twin_locked_web_auth_carries_no_meshforge_tui_path():
    """web_auth.py is byte-locked to MeshAnchor (parity_check): the
    MeshForge-only TUI path must not leak into it (the 11-16 suite caught it)."""
    assert not hasattr(web_auth, "PAIR_TUI_PATH")
    assert not hasattr(web_auth, "PAIR_TUI_LABEL")


# ── the TUI action, on a fake dialog, against a TEMP map store ─────────────

def _handler(monkeypatch, tmp_path, result, menu_returns, map_user=(ME, ""), active="active",
             remedy="shell"):
    from handler_test_utils import make_handler_context
    from handlers.web_client import WebClientHandler
    store = _store(tmp_path)
    ctx = make_handler_context()
    ctx.dialog._menu_returns = list(menu_returns)
    h = WebClientHandler()
    h.set_context(ctx)
    monkeypatch.setattr(web_pair, "map_unit", lambda **kw: web_pair.MapUnit(
        map_user[0], map_user[1], active, remedy, 0))
    monkeypatch.setattr(web_pair, "store_path_for", lambda u: store)
    monkeypatch.setattr(web_pair, "pair_browser", lambda **kw: result)
    monkeypatch.setattr(h, "_local_ip", lambda: "192.0.2.7")
    return h, ctx.dialog


def _texts(dlg, kind):
    return [c[1][1] for c in dlg.calls if c[0] == kind]


def test_tui_pair_shows_code_and_login_url(monkeypatch, tmp_path):
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["pair", "back"])
    h.execute("weblogin")
    text = _texts(dlg, "msgbox")[0]
    assert CODE in text and "http://192.0.2.7:5000/login" in text


def test_tui_status_reads_the_map_users_store(monkeypatch, tmp_path):
    """F2: the status block shows the store the pairing writes to."""
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["back"])
    h.execute("weblogin")
    assert "Pairing code pending:   yes" in _texts(dlg, "menu")[0]


def test_tui_status_unknown_when_the_map_user_cannot_be_named(monkeypatch, tmp_path):
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["back"],
                      map_user=(None, "the meshforge-map unit is not installed here"))
    h.execute("weblogin")
    menu_text = _texts(dlg, "menu")[0]
    assert "UNKNOWN" in menu_text and "not installed" in menu_text


def test_tui_status_names_the_access_problem_not_a_store_fault(monkeypatch, tmp_path):
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["back"])
    monkeypatch.setattr(web_pair, "store_access_problem",
                        lambda user, **kw: "this TUI runs as guest; open it with sudo")
    h.execute("weblogin")
    t = _texts(dlg, "menu")[0]
    assert "UNKNOWN" in t and "open it with sudo" in t


DAMAGED_LAST_USED = ['"soon"', "1e300", "Infinity", "-Infinity", "NaN", "1e17",
                     "1" + "0" * 400, "null", "[]", "{}", "true"]


@pytest.mark.parametrize("raw", DAMAGED_LAST_USED)
def test_tui_status_damaged_row_is_unknown_not_a_crash(monkeypatch, tmp_path, raw):
    """R2-F4 then R3-C2, the SAME kind twice -> one seam: the whole read-AND-
    format step is guarded once; the reviewer's full value table is pinned.
    The menu (and its Pair action) must always draw."""
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["back"])
    store = web_pair.store_path_for(ME)
    store.write_text('{"sessions": [{"h": "x", "last_used": %s}], "pairing": null}' % raw)
    h.execute("weblogin")
    menus = _texts(dlg, "menu")
    assert menus, "the Web Login menu never drew"
    t = menus[0]
    assert ("UNKNOWN" in t) or ("Live operator sessions" in t)   # a summary or an honest UNKNOWN
    if "UNKNOWN" in t:
        assert "could not be summarised" in t or "could not be read" in t


def test_tui_status_with_no_unit_says_why_and_reads_no_store(monkeypatch, tmp_path):
    """Option B: no unit -> UNKNOWN with the true reason; no store is opened."""
    reason = "the meshforge-map unit is not installed or not loadable here (LoadState=not-found)"
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["back"],
                      map_user=(None, reason), remedy="install")
    opened = []
    monkeypatch.setattr(web_auth.SessionStore, "status",
                        lambda self: opened.append(self.path) or {})
    h.execute("weblogin")
    t = _texts(dlg, "menu")[0]
    assert "UNKNOWN" in t and "local-only" in t and opened == []


def test_revoke_command_is_drawn_above_the_status_block(monkeypatch, tmp_path):
    """R5-5: on a 24-row terminal a long UNKNOWN reason clipped the revoke
    command off the bottom; it now comes first."""
    long_reason = "x" * 400
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["back"],
                      map_user=(None, long_reason))
    h.execute("weblogin")
    t = _texts(dlg, "menu")[0]
    assert t.index(web_auth.REVOKE_COMMAND) < t.index("UNKNOWN")


def test_tui_status_shows_whether_the_map_runs(monkeypatch, tmp_path):
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["back"],
                      active="inactive")
    h.execute("weblogin")
    assert "Map service:            inactive" in _texts(dlg, "menu")[0]


def test_tui_success_box_carries_the_not_running_note(monkeypatch, tmp_path):
    note = "the map is not running on this box (ActiveState=inactive)"
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, note), ["pair", "back"])
    h.execute("weblogin")
    t = _texts(dlg, "msgbox")[0]
    assert CODE in t and note in t


def test_tui_refusal_puts_the_fallback_first_and_caps_the_reason(monkeypatch, tmp_path):
    """F8: whiptail clips a long box — the recovery command must survive."""
    long_reason = "pairing refused (rc 1): " + "x" * 600
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(False, None, long_reason),
                      ["pair", "back"])
    h.execute("weblogin")
    text = _texts(dlg, "msgbox")[0]
    assert text.index(web_auth.PAIR_COMMAND) < text.index("pairing refused")
    assert len(text) < 700


@pytest.mark.parametrize("kind,has,never", REMEDY_TABLE)
def test_tui_failure_box_leads_with_the_refusals_own_remedy(monkeypatch, tmp_path, kind, has, never):
    """R6-1: no unconditional shell fallback; the remedy is never clipped."""
    why = "the reason " + "y" * 300
    h, dlg = _handler(monkeypatch, tmp_path,
                      web_pair.PairResult(False, None, why, remedy=kind), ["pair", "back"])
    h.execute("weblogin")
    text = _texts(dlg, "msgbox")[0]
    remedy = web_pair.remedy_text(kind, URL)
    assert remedy in text and text.index(remedy) < text.index("the reason")
    for s in never:
        assert s not in text


def test_tui_weblogin_needs_no_meshtasticd(monkeypatch, tmp_path):
    """Pairing is reachable with meshtasticd down (the Web Client gate isn't)."""
    import utils.service_check as sc
    monkeypatch.setattr(sc, "check_port", lambda *a, **k: False)
    h, dlg = _handler(monkeypatch, tmp_path, web_pair.PairResult(True, CODE, ""), ["pair", "back"])
    h.execute("weblogin")
    assert any(CODE in t for t in _texts(dlg, "msgbox"))


def test_login_door_text_says_tui_once():
    from utils import _map_auth_endpoints as ep
    rendered = ep._LOGIN_PAGE.replace("__TUI__", web_pair.PAIR_TUI_PATH)
    assert "MeshForge TUI" in rendered
    assert len(re.findall(r"TUI", rendered)) == 1
