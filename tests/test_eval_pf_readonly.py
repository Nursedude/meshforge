"""scripts/eval_pf_readonly.sh — the EVAL-PF lab key's forced command.

EVAL-PF Q14/Q18 (plans/eval_pf_grilling_2026_11_13.md): a model session under
test reaches the lab box ONLY through this wrapper, so the wrapper — not a
Claude permission pattern (fragile on arguments, per the CLI docs) — is what
keeps the session read-only. This test is the refusal half of slice-1
acceptance (Q21): every write / restart / shell-escape / path-escape / secret
read is refused with exit 126 and NEVER reaches a program; the allowed reads
reach exactly the program with exactly the words asked.

Every program the wrapper can exec is a stub on PATH that records its argv,
and HOME is a tmp dir, so nothing here touches the real box.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "eval_pf_readonly.sh"

# `find` is NOT stubbed: the wrapper itself uses it to audit eval_pf (a
# helper; the recorder test below sees every program, helpers included).
PROGRAMS = ["systemctl", "journalctl", "ls", "cat", "ps", "ss", "df", "free",
            "uptime", "rm", "bash", "sh", "id", "echo", "kill"]


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    (home / ".local/share/meshforge").mkdir(parents=True)
    (home / ".config/meshforge").mkdir(parents=True)
    calls = tmp_path / "calls"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for prog in PROGRAMS:
        p = stubs / prog
        p.write_text(f'#!/bin/bash\nprintf "%s\\n" "{prog}${{*:+ $*}}" >> "{calls}"\n')
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "wrapper.log"

    def run(request):
        env = {
            "HOME": str(home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "EVAL_PF_LOG": str(log),
        }
        if request is not None:
            env["SSH_ORIGINAL_COMMAND"] = request
        proc = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=20, stdin=subprocess.DEVNULL)
        called = calls.read_text().splitlines() if calls.exists() else []
        logged = log.read_text().splitlines() if log.exists() else []
        return proc, called, logged

    run.home = home
    return run


REFUSED = [
    None,                                         # no command = interactive shell
    "",
    "rm -rf /",
    "bash -c id",
    "sh",
    "systemctl restart rnsd",
    "systemctl --user restart meshforge-mini-dudeai",
    "systemctl --user start foo.service",
    "systemctl kill foo.service",
    "systemctl rnsd",                             # a unit in the verb slot
    "systemctl --signal=9 status foo",
    "systemctl status foo.service && rm x",
    "systemctl status foo.service; id",
    "systemctl status foo | sh",
    "journalctl -f",
    "journalctl --vacuum-size=1M",
    "journalctl -u foo -n 1000000",
    "ls ~/.local/share/meshforge/../../.ssh",
    "cat ~/.local/share/meshforge/../../.ssh/id_ed25519",
    "cat ~/.config/meshforge/settings.json",      # configs can hold secrets
    "cat /etc/shadow",
    "ls /",
    "ls relative/path",
    "cat -v ~/.local/share/meshforge/x",
    "ls /home/x/~/y",
    "ps aux; id",
    "ps -o pid",
    "ss -K",
    "df",
    "find / -name x",
    "echo $(id)",
    "echo `id`",
    "ls ~/.local/share/meshforge\nrm x",
    "ls\t~/.local/share/meshforge",
    "cat '~/.local/share/meshforge/x'",
    "uptime now",
    # Fable 11-14 BLOCKER: systemctl's -p CONSUMES the next word, so a -p the
    # wrapper read as a bare flag let the real verb ride in the unit slot.
    "systemctl -p status restart rnsd.service",
    "systemctl --user -p status stop meshforge-echo.service",
    "systemctl --user -p x exit",
    "systemctl --user -p x mask lxmd",
    "systemctl -p x daemon-reload",
    "systemctl --failed -p x poweroff",
    "systemctl show -p",                          # -p needs its value
    "systemctl show -p -x foo.service",
    "systemctl show --property=-x foo.service",
    "systemctl status -- restart",
    "systemctl --state=-x list-units",
    "systemctl -H status status foo.service",     # remote host: -H/-M also consume a word
    "systemctl -M status status foo.service",
    "systemctl --host=x status foo.service",
    "systemctl --machine=x status foo.service",
    "journalctl -u -f",                           # a unit value may not start with '-'
    "journalctl --user-unit -x",
    "journalctl -o=short -n 1",                   # only --output=short* is a real form
    "cat  ~/.local/share/meshforge/x",            # runs of spaces: the logged request
    " uptime",                                    # must be exactly the words run
    "uptime ",
    "cat " + " " * 4100 + "~/.local/share/meshforge/x",
    "ls ~/.local/share/meshforge/" + "a" * 1100,  # over the request cap
    "cat ~/.local/share/meshforge/~/x",           # '~' only as the leading ~/
    "journalctl --output=shortX",
]


@pytest.mark.parametrize("request_", REFUSED, ids=repr)
def test_refused_never_reaches_a_program(box, request_):
    proc, called, logged = box(request_)
    assert proc.returncode == 126, (proc.returncode, proc.stderr)
    assert called == [], f"refused request reached a program: {called}"
    assert "refused" in proc.stderr
    assert logged and logged[-1].split("\t")[1].startswith("REFUSED"), logged


ALLOWED = [
    ("systemctl --user status foo.service", "systemctl --no-pager --user status foo.service"),
    ("systemctl --failed", "systemctl --no-pager --failed"),
    ("systemctl --user --failed", "systemctl --no-pager --user --failed"),
    ("systemctl --user list-timers --all", "systemctl --no-pager --user list-timers --all"),
    ("systemctl --user cat foo.service", "systemctl --no-pager --user cat foo.service"),
    ("systemctl show -p ActiveState foo.service", "systemctl --no-pager show -p ActiveState foo.service"),
    ("systemctl list-units meshforge-*", "systemctl --no-pager list-units meshforge-*"),
    ("systemctl show -p ActiveState,SubState foo.service",
     "systemctl --no-pager show -p ActiveState,SubState foo.service"),
    ("systemctl --state=failed,running list-units", "systemctl --no-pager --state=failed,running list-units"),
    ("systemctl show --property=MainPID foo.service",
     "systemctl --no-pager show --property=MainPID foo.service"),
    ("journalctl --user -u foo.service -n 50", "journalctl --no-pager --user -u foo.service -n 50"),
    ("journalctl -b -p err --since=-1h", "journalctl --no-pager -b -p err --since=-1h"),
    ("ps aux", "ps aux"),
    ("ss -ltn", "ss -ltn"),
    ("df -h", "df -h"),
    ("free -m", "free -m"),
    ("uptime", "uptime"),
]


@pytest.mark.parametrize("request_,expect", ALLOWED, ids=[a for a, _ in ALLOWED])
def test_allowed_reads_reach_exactly_the_program(box, request_, expect):
    proc, called, logged = box(request_)
    assert proc.returncode == 0, proc.stderr
    assert called == [expect]
    assert logged[-1].split("\t")[1] == "ALLOWED"
    assert logged[-1].split("\t")[2] == request_
    # the record names the EXACT argv exec'd (Fable 11-14 round 4)
    assert logged[-1].split("\t")[4] == expect


def test_paths_expand_tilde_and_stay_inside_roots(box):
    share = box.home / ".local/share/meshforge"
    conf = box.home / ".config/meshforge"
    proc, called, _ = box("cat ~/.local/share/meshforge/state.json")
    assert proc.returncode == 0 and called == [f"cat -- {share}/state.json"]
    proc, called, _ = box("ls -la ~/.config/meshforge")
    assert proc.returncode == 0 and called[-1] == f"ls -la -- {conf}"
    # A sibling directory sharing the prefix is NOT inside the root.
    proc, called, _ = box("ls ~/.local/share/meshforge-evil")
    assert proc.returncode == 126 and len(called) == 2   # no new call


def test_a_symlink_inside_a_root_does_not_reach_outside_it(box, tmp_path):
    """Fable 11-14: the root check was lexical, so a link under the share dir
    (one already exists on the manager) exposed whatever it points at."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("x")
    share = box.home / ".local/share/meshforge"
    (share / "lnk").symlink_to(outside)
    (share / "flink").symlink_to(outside / "secret")
    for req in ("cat ~/.local/share/meshforge/lnk/secret",
                "cat ~/.local/share/meshforge/flink",
                "ls ~/.local/share/meshforge/lnk"):
        proc, called, _ = box(req)
        assert proc.returncode == 126, (req, proc.stderr)
        assert called == [], (req, called)
    # Round 7: NO link is followed below a root — not even one that stays
    # inside it (three escapes in seven rounds were operator-made links).
    (share / "real.json").write_text("{}")
    (share / "inner").symlink_to(share / "real.json")
    proc, called, _ = box("cat ~/.local/share/meshforge/inner")
    assert proc.returncode == 126 and called == [], proc.stderr
    proc, called, logged = box("cat ~/.local/share/meshforge/real.json")
    assert proc.returncode == 0, proc.stderr
    assert logged[-1].split("\t")[4] == f"cat -- {os.path.realpath(share / 'real.json')}"


def test_every_request_is_logged_in_order(box):
    box("uptime")
    box("rm -rf /")
    _, _, logged = box("df -h")
    verdicts = [line.split("\t")[1] for line in logged]
    assert verdicts[0] == "ALLOWED"
    assert verdicts[1].startswith("REFUSED")
    assert verdicts[2] == "ALLOWED"


# ── the grant line binds its fingerprint in as our own argv ─────────────

FP = "SHA256:" + "A" * 43


def _run_with_argv(box, argv, request="uptime"):
    import subprocess as sp
    env = {"HOME": str(box.home), "PATH": f"{box.home.parent / 'bin'}:/usr/bin:/bin",
           "EVAL_PF_LOG": str(box.home.parent / "wrapper.log"),
           "SSH_ORIGINAL_COMMAND": request}
    return sp.run(["/bin/bash", str(SCRIPT), *argv], env=env, capture_output=True,
                  text=True, timeout=20, stdin=sp.DEVNULL)


def test_grant_argv_is_accepted_and_logged(box):
    box(None)   # creates the stub dir/log paths
    p = _run_with_argv(box, ["--grant", FP])
    assert p.returncode == 0, p.stderr
    log = (box.home.parent / "wrapper.log").read_text().splitlines()
    assert log[-1].split("\t") [1:] == ["ALLOWED", "uptime", FP, "uptime"]


@pytest.mark.parametrize("argv", [
    ["--grant"], ["--grant", "SHA256:short"], ["--grant", FP, "extra"],
    ["-c", "id"], ["--grant", FP.replace("A", ";", 1)],
])
def test_unexpected_wrapper_argv_is_refused(box, argv):
    box(None)
    p = _run_with_argv(box, argv)
    assert p.returncode == 126


def test_a_symlink_under_the_config_root_does_not_reach_outside_it(box, tmp_path):
    """Round 2 (Fable 11-14): the resolved-path fix was pinned for the share root only."""
    outside = tmp_path / "outside2"
    outside.mkdir()
    (box.home / ".config/meshforge/lnk").symlink_to(outside)
    proc, called, _ = box("ls -l ~/.config/meshforge/lnk")
    assert proc.returncode == 126 and called == [], proc.stderr


def test_an_unwritable_audit_log_refuses_the_request(box, tmp_path):
    """Round 2: 'log every request' — a log that cannot be written must not
    become silent, unaudited access."""
    box(None)
    env = {"HOME": str(box.home), "PATH": f"{box.home.parent / 'bin'}:/usr/bin:/bin",
           "EVAL_PF_LOG": "/proc/nonexistent/x.log", "SSH_ORIGINAL_COMMAND": "uptime"}
    p = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                       timeout=20, stdin=subprocess.DEVNULL)
    calls = box.home.parent / "calls"
    assert p.returncode == 126, p.stderr
    assert not calls.exists() or "uptime" not in calls.read_text()


def _utf8_locale():
    out = subprocess.run(["locale", "-a"], capture_output=True, text=True, timeout=10).stdout.split()
    for want in ("en_US.utf8", "en_US.UTF-8"):
        if want in out:
            return want
    return None


def test_charset_does_not_depend_on_the_client_locale(box):
    """Round 2: sshd passes the client's LANG/LC_*; under en_US.UTF-8 the
    [A-Za-z] range matched 'é'. The wrapper pins LC_ALL=C."""
    loc = _utf8_locale()
    if loc is None:
        pytest.skip("no en_US UTF-8 locale installed")
    box(None)
    env = {"HOME": str(box.home), "PATH": f"{box.home.parent / 'bin'}:/usr/bin:/bin",
           "EVAL_PF_LOG": str(box.home.parent / "wrapper.log"),
           "SSH_ORIGINAL_COMMAND": "systemctl is-active \u00e9", "LC_ALL": loc, "LANG": loc}
    p = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                       timeout=20, stdin=subprocess.DEVNULL)
    assert p.returncode == 126, p.stderr


def test_log_lines_carry_no_control_bytes_and_are_capped(box):
    box("id \x1b[2J\r FAKE".encode().decode("unicode_escape"))
    _, _, logged = box("x" * 100000)
    raw = (box.home.parent / "wrapper.log").read_bytes()
    assert b"\x1b" not in raw and b"\r" not in raw
    assert max(len(line) for line in raw.splitlines()) < 4200


def test_the_logged_request_names_everything_that_ran(box):
    """Round 3 (Fable 11-14): with a log cap and space-padding, an ALLOWED
    read could push its real argument past the cap. Requests are now bounded
    below the cap and single-spaced, so the log is never cut."""
    share = box.home / ".local/share/meshforge"
    (share / ("f" * 200)).write_text("x")
    req = "cat " + " ".join(["~/.local/share/meshforge/" + "f" * 200] * 4)
    proc, called, logged = box(req)
    assert proc.returncode == 0, proc.stderr
    assert logged[-1].split("\t")[2] == req


def test_log_has_only_printable_ascii(box):
    box("id \u009b2J \u202e x")
    raw = (box.home.parent / "wrapper.log").read_bytes()
    assert all(b == 9 or b == 10 or 32 <= b < 127 for b in raw), raw


def test_unwritable_log_message_is_ours_alone(box):
    box(None)
    env = {"HOME": str(box.home), "PATH": f"{box.home.parent / 'bin'}:/usr/bin:/bin",
           "EVAL_PF_LOG": "/proc/nonexistent/x.log", "SSH_ORIGINAL_COMMAND": "uptime"}
    p = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                       timeout=20, stdin=subprocess.DEVNULL)
    assert p.returncode == 126
    assert p.stderr.startswith("eval_pf_readonly: refused (audit log"), p.stderr


def test_an_exec_record_that_would_be_cut_refuses(tmp_path):
    """The exec'd argv (~/ expands to $HOME) can be far longer than the
    request; it is logged whole or not run at all."""
    home = tmp_path
    for i in range(10):                     # ~2.4 KB: one path fits EXEC_CAP 3000
        home = home / (chr(97 + i) * 240)
    (home / ".local/share/meshforge").mkdir(parents=True)
    (home / ".local/share/meshforge/f").write_text("x")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "cat").write_text("#!/bin/bash\necho ran\n")
    (stubs / "cat").chmod(0o755)
    log = tmp_path / "w.log"

    def go(req):
        env = {"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "EVAL_PF_LOG": str(log),
               "SSH_ORIGINAL_COMMAND": req}
        return subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=20, stdin=subprocess.DEVNULL)
    p = go("cat " + " ".join(["~/.local/share/meshforge/f"] * 3))
    assert p.returncode == 126 and "ran" not in p.stdout, p.stderr
    assert "record" in log.read_text().splitlines()[-1].split("\t")[1]
    p = go("cat ~/.local/share/meshforge/f")
    assert p.returncode == 0 and "ran" in p.stdout, p.stderr


def test_run_allowed_is_the_only_exec():
    """The seam: a new branch that execs directly would run unlogged-argv.
    Exactly one `exec` statement exists, inside run_allowed."""
    import re
    code = [ln.strip() for ln in SCRIPT.read_text().splitlines()
            if not ln.lstrip().startswith("#")]
    execs = [ln for ln in code if re.search(r'(^|[;&|\s])exec(\s|$)', ln)]
    assert execs == ['exec "$@"'], execs
    assert not [ln for ln in code if "log ALLOWED" in ln and '"$x"' not in ln]


def test_the_eval_answer_keys_are_never_readable(box, tmp_path):
    """Round 5 (Fable 11-14) + EVAL-PF Q8/Q30: cases (with the answer key)
    and results live under <share>/eval_pf — the session under test must
    never read or list them, by direct path or through a link."""
    share = box.home / ".local/share/meshforge"
    cases = share / "eval_pf" / "cases"
    cases.mkdir(parents=True)
    (cases / "case1.json").write_text('{"key": {"verdict": "x"}}')
    (share / "innocent").symlink_to(cases / "case1.json")
    for req in ("cat ~/.local/share/meshforge/eval_pf/cases/case1.json",
                "ls ~/.local/share/meshforge/eval_pf",
                "ls ~/.local/share/meshforge/eval_pf/cases",
                "cat ~/.local/share/meshforge/innocent"):
        proc, called, _ = box(req)
        assert proc.returncode == 126 and called == [], (req, proc.stderr)
    # the share root itself and its siblings stay listable
    proc, called, _ = box("ls ~/.local/share/meshforge")
    assert proc.returncode == 0, proc.stderr
    (share / "eval_pf-notes").write_text("x")
    proc, called, _ = box("cat ~/.local/share/meshforge/eval_pf-notes")
    assert proc.returncode == 0, proc.stderr


def test_eval_pf_that_is_itself_a_link_is_still_refused(box):
    """Round 6 (Fable 11-14): the deny root was compared UNRESOLVED, so
    eval_pf -> eval_pf_v2 exposed the key. Every root is resolved."""
    share = box.home / ".local/share/meshforge"
    real = share / "eval_pf_v2"
    real.mkdir()
    (real / "key.json").write_text("SECRET2")
    (share / "eval_pf").symlink_to(real)
    for req in ("cat ~/.local/share/meshforge/eval_pf/key.json",
                "ls ~/.local/share/meshforge/eval_pf",
                "cat ~/.local/share/meshforge/eval_pf_v2/key.json"):
        proc, called, _ = box(req)
        assert proc.returncode == 126 and called == [], (req, proc.stderr)


def test_every_program_arm_ends_in_run_allowed_or_refuse():
    """Round 7: pinning the word `exec` let `date) date; exit 0 ;;` run a
    program with no log line. Every arm of `case "$PROG"` must END in
    run_allowed or refuse, and none may exit on its own."""
    lines = SCRIPT.read_text().splitlines()
    start = lines.index('case "$PROG" in')
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "esac")
    arms, cur = {}, None
    for ln in lines[start + 1:end]:
        m = re.match(r"^(\S[^)]*\))\s*(.*)$", ln)   # an arm label at column 0
        if m:
            cur = m.group(1)
            arms[cur] = [m.group(2).strip()] if m.group(2).strip() else []
        elif ln.strip() and not ln.strip().startswith("#"):
            assert cur is not None, f"code between `case` and its first arm: {ln!r}"
            arms[cur].append(ln.strip())
    # CLOSED: a new program is a deliberate, reviewed change to this list.
    assert list(arms) == ["systemctl)", "journalctl)", "ls | cat)", "ps)", "ss)", "df)",
                          "free)", "uptime)", "*)"], list(arms)
    for arm, body in arms.items():
        last = body[-1].removesuffix(";;").strip() or body[-2].removesuffix(";;").strip()
        assert last.startswith(("run_allowed ", "refuse ")), (arm, last)
        assert not any(re.search(r"(^|[;&|\s])exit(\s|$)", b) for b in body), (arm, body)


def test_no_program_is_called_by_absolute_path():
    """Round 7 M6: `/usr/bin/head …` slipped past a PATH-based recorder."""
    code = [ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")]
    bad = [ln for ln in code if re.search(r"(^|[\s;|&(`$])/(usr/)?s?bin/", ln)]
    assert not bad, bad


HELPERS = {"date", "dirname", "mkdir", "realpath", "find"}


@pytest.fixture(scope="module")
def recorder(tmp_path_factory):
    """Every executable on /usr/bin and /bin, replaced by a recorder: the
    test sees EVERY program the wrapper runs, not only the 16 stubs."""
    d = tmp_path_factory.mktemp("rec")
    bindir, rec = d / "bin", d / "rec.txt"
    bindir.mkdir()
    for src in ("/usr/bin", "/bin"):
        for p in Path(src).iterdir():
            name = p.name
            if (bindir / name).exists() or not os.access(p, os.X_OK) or "/" in name:
                continue
            target = "true" if name in PROGRAMS else f'exec {p} "$@"'
            (bindir / name).write_text(f'#!/bin/bash\necho {shlex_q(name)} >> "{rec}"\n{target}\n')
            (bindir / name).chmod(0o755)
    home = d / "home"
    (home / ".local/share/meshforge").mkdir(parents=True)
    (home / ".config/meshforge").mkdir(parents=True)
    (home / ".local/share/meshforge/state.json").write_text("{}")

    def go(req):
        rec.write_text("")
        env = {"HOME": str(home), "PATH": str(bindir), "EVAL_PF_LOG": str(d / "w.log"),
               "SSH_ORIGINAL_COMMAND": req}
        p = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True,
                           text=True, timeout=30, stdin=subprocess.DEVNULL)
        return p, set(rec.read_text().split())
    return go


def shlex_q(s):
    import shlex
    return shlex.quote(s)


@pytest.mark.parametrize("request_", [a for a, _ in ALLOWED] + ["cat ~/.local/share/meshforge/state.json"])
def test_an_allowed_request_runs_only_its_program_and_helpers(recorder, request_):
    p, ran = recorder(request_)
    assert p.returncode == 0, p.stderr
    assert ran - HELPERS == {request_.split()[0]}, ran


@pytest.mark.parametrize("request_", [r for r in REFUSED if r] [:20])
def test_a_refused_request_runs_only_helpers(recorder, request_):
    p, ran = recorder(request_)
    assert p.returncode == 126
    assert ran <= HELPERS, ran


def test_links_or_hardlinks_inside_eval_pf_refuse_every_path_read(box):
    """Round 7: a link INSIDE eval_pf (cases -> ../store) put the key in a
    readable sibling; a hardlink cannot be seen by path at all. eval_pf
    must be a plain dir of plain files, or every ls/cat is refused."""
    share = box.home / ".local/share/meshforge"
    (share / "store").mkdir()
    (share / "store/key.json").write_text("SECRET_A")
    (share / "ok.json").write_text("{}")
    ev = share / "eval_pf"
    ev.mkdir()
    (ev / "cases").symlink_to("../store")
    for req in ("cat ~/.local/share/meshforge/eval_pf/cases/key.json",
                "cat ~/.local/share/meshforge/store/key.json",
                "cat ~/.local/share/meshforge/ok.json"):
        proc, called, _ = box(req)
        assert proc.returncode == 126 and called == [], (req, proc.stderr)
    (ev / "cases").unlink()
    (ev / "cases").mkdir()
    (ev / "cases/k.json").write_text("SECRET_B")
    os.link(ev / "cases/k.json", share / "hard.json")
    proc, called, _ = box("cat ~/.local/share/meshforge/hard.json")
    assert proc.returncode == 126 and called == [], proc.stderr
    (share / "hard.json").unlink()
    proc, called, _ = box("cat ~/.local/share/meshforge/ok.json")
    assert proc.returncode == 0, proc.stderr

def test_eval_pf_that_is_not_a_plain_dir_refuses_every_path_read(box):
    share = box.home / ".local/share/meshforge"
    (share / "ok.json").write_text("{}")
    (share / "eval_pf").write_text("not a dir")
    proc, called, _ = box("cat ~/.local/share/meshforge/ok.json")
    assert proc.returncode == 126 and called == [], proc.stderr
    assert "not a plain directory" in proc.stderr


ALLOWED_PROGS = {"systemctl", "journalctl", "ls", "cat", "ps", "ss", "df", "free", "uptime"}


def test_every_other_program_name_is_refused_with_no_output(recorder):
    """Round 8: a program handled OUTSIDE the case (`if [ $PROG = tail ]`),
    or a one-line arm the parser missed, ran unlogged. Behaviourally: every
    executable name on this box, sent as a bare request, refuses with no
    stdout and only helpers run — unless it is one of the allowed programs."""
    names = sorted({p.name for d in ("/usr/bin", "/bin") for p in Path(d).iterdir()
                    if re.fullmatch(r"[A-Za-z0-9._+-]+", p.name)} - ALLOWED_PROGS)
    assert len(names) > 100
    leaks = []
    for name in names:
        p, ran = recorder(name)
        if p.returncode != 126 or p.stdout or not ran <= HELPERS:
            leaks.append((name, p.returncode, p.stdout[:40], sorted(ran - HELPERS)))
    assert not leaks, leaks[:10]


@pytest.mark.parametrize("alias", ["share", "share-parent", "eval_pf", "cases"])
def test_a_config_root_that_aliases_into_the_share_never_reaches_eval_pf(tmp_path, alias):
    """Round 8 regression: with the deny literal-only, ~/.config/meshforge ->
    ~/.local/share/meshforge listed the answer keys through `ls`. The deny
    holds on the RESOLVED path too, whatever a root aliases to."""
    home = tmp_path / "home"
    share = home / ".local/share/meshforge"
    cases = share / "eval_pf" / "cases"
    cases.mkdir(parents=True)
    (cases / "key.json").write_text("K")
    (home / ".config").mkdir()
    target, via = {"share": (share, "eval_pf/cases"),
                   "share-parent": (share.parent, "meshforge/eval_pf/cases"),
                   "eval_pf": (share / "eval_pf", "cases"),
                   "cases": (cases, "")}[alias]
    (home / ".config/meshforge").symlink_to(target)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "ls").write_text("#!/bin/bash\necho LISTED\n")
    (stubs / "ls").chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "EVAL_PF_LOG": str(tmp_path / "w.log"),
           "SSH_ORIGINAL_COMMAND": ("ls -la ~/.config/meshforge/" + via).rstrip("/")}
    p = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                       timeout=20, stdin=subprocess.DEVNULL)
    assert p.returncode == 126 and "LISTED" not in p.stdout, (alias, p.returncode, p.stderr)


# ── the WHOLE root-layout space (rounds 1, 6, 7, 8, 9 were each one cell) ──
# Every layout an operator could make × every path request. Only a plain
# layout (HOME itself may be a link) ever reads; any root that is a link,
# aliases, nests, or resolves elsewhere refuses EVERY path read.

LAYOUTS = ["plain", "home-link", "share->conf", "share-parent->config", "conf->share",
           "conf->share-parent", "share->/", "conf->/", "share->elsewhere", "conf->elsewhere",
           "share->home", "local->elsewhere"]
REQS = {
    "cat share/x": ("cat ~/.local/share/meshforge/x.json", "SHAREX"),
    "ls share": ("ls ~/.local/share/meshforge", None),
    "ls conf": ("ls ~/.config/meshforge", None),
    "cat conf/secret": ("cat ~/.config/meshforge/secret.json", "CONFSECRET"),
    "cat share/secret": ("cat ~/.local/share/meshforge/secret.json", "CONFSECRET"),
    "cat share/etc": ("cat ~/.local/share/meshforge/etc/hostname", None),
    "ls share/eval_pf": ("ls ~/.local/share/meshforge/eval_pf", None),
}
# In a plain layout these RUN (share/secret + share/etc are absent files: cat
# runs and fails ENOENT — harmless). Never: cat in conf, anything in eval_pf.
ALLOW = {"cat share/x", "ls share", "ls conf", "cat share/secret", "cat share/etc"}


def _layout(tmp_path, name):
    real_home = tmp_path / "realhome"
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "x.json").write_text("SHAREX")
    home = real_home
    share = real_home / ".local/share/meshforge"
    conf = real_home / ".config/meshforge"
    for d in (share, conf):
        d.mkdir(parents=True)
    (share / "x.json").write_text("SHAREX")
    (conf / "secret.json").write_text("CONFSECRET")
    (share / "eval_pf").mkdir()
    (share / "eval_pf" / "k.json").write_text("KEY")

    def relink(path, target):
        import shutil
        shutil.rmtree(path)
        path.symlink_to(target)
    if name == "home-link":
        home = tmp_path / "homelink"
        home.symlink_to(real_home)
    elif name == "share->conf":
        relink(share, conf)
    elif name == "share-parent->config":
        relink(share.parent, real_home / ".config")
    elif name == "conf->share":
        relink(conf, share)
    elif name == "conf->share-parent":
        relink(conf, share.parent)
    elif name == "share->/":
        relink(share, "/")
    elif name == "conf->/":
        relink(conf, "/")
    elif name == "share->elsewhere":
        relink(share, other)
    elif name == "conf->elsewhere":
        relink(conf, other)
    elif name == "share->home":
        relink(share, real_home)
    elif name == "local->elsewhere":
        import shutil
        (other / "share/meshforge").mkdir(parents=True)
        (other / "share/meshforge/x.json").write_text("SHAREX")
        shutil.rmtree(real_home / ".local")
        (real_home / ".local").symlink_to(other)
    return home


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("req", list(REQS))
def test_root_layout_table(tmp_path, layout, req):
    home = _layout(tmp_path, layout)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for prog in ("ls", "cat"):
        (stubs / prog).write_text(f'#!/bin/bash\necho RAN-{prog} "$@"\nexec /bin/{prog} "$@"\n')
        (stubs / prog).chmod(0o755)
    request, secret = REQS[req]
    env = {"HOME": str(home), "PATH": f"{stubs}:/usr/bin:/bin", "EVAL_PF_LOG": str(tmp_path / "w.log"),
           "SSH_ORIGINAL_COMMAND": request}
    p = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                       timeout=20, stdin=subprocess.DEVNULL)
    should_run = layout in ("plain", "home-link") and req in ALLOW
    if should_run:
        assert "RAN-" in p.stdout, (layout, req, p.returncode, p.stderr)
    else:
        assert p.returncode == 126 and "RAN-" not in p.stdout, (layout, req, p.stdout[:80], p.stderr[:200])
    if secret == "CONFSECRET":
        assert "CONFSECRET" not in p.stdout, (layout, req)


def test_the_longest_possible_log_line_is_one_write():
    """Round 9 NIT: a line over 8192 bytes is split across write() calls."""
    src = SCRIPT.read_text()
    cap = int(re.search(r"^EXEC_CAP=(\d+)$", src, re.M).group(1))
    longest = 25 + 1 + 120 + 1 + 4000 + 1 + 71 + 1 + cap + 1
    assert longest < 8192, longest
