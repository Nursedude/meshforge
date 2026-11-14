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


def test_every_request_is_logged_in_order(box):
    box("uptime")
    box("rm -rf /")
    _, _, logged = box("df -h")
    verdicts = [line.split("\t")[1] for line in logged]
    assert verdicts[0] == "ALLOWED"
    assert verdicts[1].startswith("REFUSED")
    assert verdicts[2] == "ALLOWED"
