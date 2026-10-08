"""scripts/box_config_capture.sh — every box's config reaches the vault.

WHY (2026-10-04): the vault held a two-month-old one-shot capture of 7 Pi
boxes and nothing for the manager, while the restore runbook claimed
otherwise; the 08-11 capture tool had not been kept. This organ re-captures
every box monthly and commits, so fleet-vault (which refuses a dirty tree)
carries it.

These drive the REAL script. `ssh` is faked to run the collector LOCALLY
against a planted HOME; `sudo` is faked to refuse (the no-sudo leg) and
`crontab` to print a marker, so no live machine state decides a verdict.
The drill that matters most: a planted ssh PRIVATE key must never be
captured.
"""

import os
import stat
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "box_config_capture.sh")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}

SSH_SHIM = """#!/usr/bin/env bash
host=""; skip=0
for a in "$@"; do
  if [ "$skip" = 1 ]; then skip=0; continue; fi
  case "$a" in -o|-p|-i|-l) skip=1; continue;; -*) continue;; esac
  host="$a"; break
done
[ -f "$SHIMDIR/unreachable_$host" ] && exit 255
exec bash -s
"""


def _shim(d, name, body):
    p = d / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=30, check=True,
                          env=dict(os.environ, **GIT_ENV)).stdout


def _setup(tmp_path, hosts=("box-a", "box-b")):
    shim = tmp_path / "shim"
    shim.mkdir()
    _shim(shim, "ssh", SSH_SHIM)
    _shim(shim, "sudo", "#!/bin/sh\nexit 1\n")
    _shim(shim, "crontab", "#!/bin/sh\necho '# crontab marker'\n")
    home = tmp_path / "home"
    cfg = home / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    (cfg / "fleet_hosts").write_text("".join(f"{h}\n" for h in hosts))
    (cfg / "deployment.json").write_text('{"profile": "gateway"}\n')
    (cfg / "gateway_identity").write_bytes(b"\x01" * 64)
    (cfg / "big.db").write_bytes(b"\x00" * (300 * 1024))   # over the 256 KB cap
    ssh = home / ".ssh"
    ssh.mkdir()
    (ssh / "config").write_text("Host box-a\n  HostName 192.0.2.1\n")
    (ssh / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nPLANTED\n")
    (ssh / "fleet_np").write_text("PLANTED-NP-KEY\n")
    units = home / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "meshforge-echo.service").write_text("[Service]\nExecStart=/bin/true\n")
    dest = tmp_path / "fleet-configs"
    dest.mkdir()
    _git(dest, "init", "-q")
    (dest / "README.md").write_text("seed\n")
    _git(dest, "add", "README.md")
    _git(dest, "commit", "-q", "-m", "seed")
    env = dict(os.environ, **GIT_ENV,
               PATH=f"{shim}:{os.environ['PATH']}",
               SHIMDIR=str(shim),
               HOME=str(home),
               MESHFORGE_FLEET_HOSTS=str(cfg / "fleet_hosts"),
               BOX_CAPTURE_ROOT=str(dest),
               BOX_CAPTURE_SELF="self-box",
               CRON_VERDICT_LOG=str(tmp_path / "verdicts.log"))
    return dest, shim, home, env


def _run(env, tmp_path):
    r = subprocess.run(["bash", SCRIPT], env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return (tmp_path / "verdicts.log").read_text().strip().splitlines()[-1]


def _snapshot_files(dest, box):
    root = dest / box / "snapshot"
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def test_every_box_captured_committed_tree_clean(tmp_path):
    dest, _, _, env = _setup(tmp_path)
    verdict = _run(env, tmp_path)
    for box in ("self-box", "box-a", "box-b"):
        files = _snapshot_files(dest, box)
        assert any(f.endswith(".config/meshforge/deployment.json") for f in files), files
        assert any(f.endswith(".ssh/config") for f in files)
        assert any(f.endswith("meshforge-echo.service") for f in files)
        assert "_meta/crontab-user.txt" in files
        assert "_meta/capture.meta" in files
    # the vault's exact precondition
    assert _git(dest, "status", "--porcelain") == ""
    assert "box-a" in _git(dest, "log", "-1", "--format=%s")
    # sudo was refused in this drill, so /etc is NOT captured — said so, not OK
    assert "CONCERN" in verdict and "NO-SUDO" in verdict


def test_private_keys_are_never_captured(tmp_path):
    dest, _, _, env = _setup(tmp_path)
    _run(env, tmp_path)
    for box in ("self-box", "box-a", "box-b"):
        files = _snapshot_files(dest, box)
        assert not any("id_ed25519" in f or f.endswith("fleet_np") for f in files), files
        blob = b"".join((dest / box / "snapshot" / f).read_bytes() for f in files)
        assert b"PLANTED" not in blob


def test_files_over_the_size_cap_are_skipped(tmp_path):
    dest, _, _, env = _setup(tmp_path)
    _run(env, tmp_path)
    assert not any(f.endswith("big.db") for f in _snapshot_files(dest, "box-a"))
    assert any(f.endswith("gateway_identity") for f in _snapshot_files(dest, "box-a"))


def test_unreachable_box_keeps_last_snapshot_and_is_named(tmp_path):
    dest, shim, home, env = _setup(tmp_path)
    _run(env, tmp_path)
    before = _snapshot_files(dest, "box-b")
    (shim / "unreachable_box-b").touch()
    (home / ".config" / "meshforge" / "deployment.json").write_text('{"profile": "monitor"}\n')
    verdict = _run(env, tmp_path)
    assert "UNCAPTURED=box-b" in verdict and "CONCERN" in verdict
    assert _snapshot_files(dest, "box-b") == before          # last good retained
    dep = next((dest / "box-a" / "snapshot").rglob("deployment.json"))
    assert "monitor" in dep.read_text()                       # reachable box refreshed
    assert _git(dest, "status", "--porcelain") == ""


def test_nothing_captured_is_fail(tmp_path):
    # Every collector's tar fails (create mode refused; extract still real).
    # The first cut emptied HOME instead and still "captured" 41 files —
    # /etc on the machine RUNNING the test is readable without sudo, so the
    # verdict depended on ambient state.
    dest, shim, _, env = _setup(tmp_path)
    _shim(shim, "tar", '#!/bin/sh\ncase " $* " in *" -czf "*) exit 1;; esac\n'
                       'exec /bin/tar "$@"\n')
    verdict = _run(env, tmp_path)
    assert "FAIL" in verdict and "nothing captured" in verdict
    assert not (dest / "box-a" / "snapshot").exists()


def test_not_a_git_repo_is_concern(tmp_path):
    dest, _, _, env = _setup(tmp_path)
    import shutil
    shutil.rmtree(dest / ".git")
    verdict = _run(env, tmp_path)
    assert "CONCERN" in verdict and "NOT COMMITTED" in verdict


def test_etc_meshforge_is_captured(tmp_path):
    """2026-10-07: moc's /etc/meshforge/noc.yaml existed nowhere but the box —
    the capture never listed /etc/meshforge, and the only other copy (a March
    manual backup) had just been retired. Planted via the collector's
    BOX_CAPTURE_ETC_MESHFORGE seam (unset over real ssh = /etc/meshforge)."""
    dest, _, _, env = _setup(tmp_path)
    etc = tmp_path / "etc-meshforge"
    etc.mkdir()
    (etc / "noc.yaml").write_text("noc: planted\n")
    (etc / "huge.bin").write_bytes(b"\x00" * (300 * 1024))   # size cap still holds
    env["BOX_CAPTURE_ETC_MESHFORGE"] = str(etc)
    _run(env, tmp_path)
    for box in ("self-box", "box-a", "box-b"):
        files = _snapshot_files(dest, box)
        assert any(f.endswith("etc-meshforge/noc.yaml") for f in files), files
        assert not any(f.endswith("huge.bin") for f in files)


def test_etc_meshanchor_is_captured(tmp_path):
    """Sister gap, found the same day: meshanchor-server's /etc/meshanchor
    (daemon.yaml, noc.yaml) was captured nowhere either."""
    dest, _, _, env = _setup(tmp_path)
    etc = tmp_path / "etc-meshanchor"
    etc.mkdir()
    (etc / "daemon.yaml").write_text("daemon: planted\n")
    env["BOX_CAPTURE_ETC_MESHANCHOR"] = str(etc)
    _run(env, tmp_path)
    for box in ("self-box", "box-a", "box-b"):
        assert any(f.endswith("etc-meshanchor/daemon.yaml")
                   for f in _snapshot_files(dest, box))
