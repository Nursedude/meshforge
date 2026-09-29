"""The tests/conftest.py HOST FENCE must actually refuse (2026-09-27).

That day a mutation drill's mutants ran REAL `sudo systemctl stop/start/
restart rnsd` on the manager box twice (mid CR A/B experiment), and — once a
sudo-only fence existed — a mutant's non-systemd fallback `pkill -x rnsd`
killed the live rnsd anyway (it runs as the operator's user). A guard that
has never been seen to refuse is not evidence; these tests make it refuse.
"""

import os
import shutil
import subprocess

import pytest


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=20)


def test_fence_is_installed_first_on_path():
    d = os.environ["PATH"].split(os.pathsep)[0]
    assert "mf-host-fence-" in d
    assert shutil.which("sudo").startswith(d)
    assert shutil.which("systemctl").startswith(d)


def test_sudo_is_refused():
    r = _run(["sudo", "-n", "systemctl", "restart", "rnsd"])
    assert r.returncode != 0
    assert "refused under pytest" in r.stderr


@pytest.mark.parametrize("verb", ["stop", "start", "restart", "daemon-reload"])
def test_systemctl_mutating_verbs_are_refused(verb):
    r = _run(["systemctl", verb, "rnsd"])
    assert r.returncode == 1
    assert "refused under pytest" in r.stderr


def test_systemctl_user_mutations_are_refused_too():
    r = _run(["systemctl", "--user", "stop", "nomadnet"])
    assert r.returncode == 1 and "refused under pytest" in r.stderr


def test_systemctl_read_only_passes_through():
    # a real answer from the real binary, not the fence's refusal
    r = _run(["systemctl", "is-active", "no-such-unit-mf-fence-test.service"])
    assert "refused" not in r.stderr
    assert r.stdout.strip() in ("inactive", "unknown")


@pytest.mark.parametrize("name", ["pkill", "killall", "rnsd", "reboot",
                                  "kill", "systemd-run", "busctl", "dbus-send"])
def test_killers_and_daemons_are_refused(name):
    if name == "kill":
        argv = ["kill", "-TERM", "1"]          # the exec'd /bin/kill, not the builtin
    elif name == "systemd-run":
        argv = ["systemd-run", "--user", "/bin/true"]
    elif name in ("pkill", "killall"):
        argv = [name, "-x", "rnsd"]
    else:
        argv = [name]
    r = _run(argv)
    assert r.returncode == 1 and "refused under pytest" in r.stderr


def test_sudo_of_a_pytest_tmp_stub_runs_unprivileged(tmp_path):
    stub = tmp_path / "stub.sh"
    stub.write_text("#!/bin/sh\nid -u\n")
    stub.chmod(0o755)
    r = _run(["sudo", "-n", str(stub)])
    assert r.returncode == 0
    assert r.stdout.strip() == str(os.getuid())  # NOT root
