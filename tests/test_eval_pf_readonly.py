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
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "eval_pf_readonly.sh"

PROGRAMS = ["systemctl", "journalctl", "ls", "cat", "ps", "ss", "df", "free",
            "uptime", "rm", "bash", "sh", "id", "find", "echo", "kill"]


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
    # A link that stays inside the root is still fine.
    (share / "real.json").write_text("{}")
    (share / "inner").symlink_to(share / "real.json")
    proc, called, _ = box("cat ~/.local/share/meshforge/inner")
    assert proc.returncode == 0, proc.stderr


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
    assert log[-1].split("\t") [1:] == ["ALLOWED", "uptime", FP]


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
