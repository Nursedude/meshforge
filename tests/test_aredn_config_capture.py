"""scripts/aredn_config_capture.sh — captures must reach the vault.

WHY (2026-10-04): the capture wrote node configs into ~/fleet-configs and never
committed them. fleet-vault's sync.sh bundles commits only and refuses a dirty
tree (correctly — a dirty tree would look backed up while not being), so the
09-05 capture of three real config changes sat uncommitted until the 10-03
vault run refused with FAIL(1), leaving the off-site copy seven weeks stale.
A producer and a consumer that were never wired together (honest_failure_modes
#4).

These drive the REAL script with a fake `ssh` early on PATH. The consumer's
own precondition — `git status --porcelain` empty — is what the tests assert,
not a paraphrase of it.
"""

import os
import stat
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "aredn_config_capture.sh")

SSH_SHIM = """#!/usr/bin/env bash
# Fake ssh: prints a fixed >10-line config for the host, or fails when told to.
host=""
skip=0
for a in "$@"; do
  if [ "$skip" = 1 ]; then skip=0; continue; fi
  case "$a" in
    -o|-i|-p|-l|-F) skip=1; continue;;
    -*) continue;;
  esac
  host="$a"; break
done
host="${host#*@}"
[ -f "$SHIMDIR/unreachable_$host" ] && exit 255
variant="$(cat "$SHIMDIR/variant_$host" 2>/dev/null || echo base)"
for i in $(seq 1 15); do echo "config line $i for $host ($variant)"; done
"""


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=30, check=True).stdout


def _setup(tmp_path, nodes=("node-a", "node-b"), git_repo=True):
    shim = tmp_path / "shim"
    shim.mkdir()
    ssh = shim / "ssh"
    ssh.write_text(SSH_SHIM)
    ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    home.mkdir()
    dest = tmp_path / "fleet-configs"
    dest.mkdir()
    if git_repo:
        _git(dest, "init", "-q")
        (dest / "README").write_text("seed\n")
        _git(dest, "add", "README")
        _git(dest, "commit", "-q", "-m", "seed")
    node_file = tmp_path / "nodes.txt"
    node_file.write_text("".join(f"{n} {n}.host\n" for n in nodes))
    env = dict(os.environ,
               PATH=f"{shim}:{os.environ['PATH']}",
               SHIMDIR=str(shim),
               HOME=str(home),
               AREDN_NODES_FILE=str(node_file),
               AREDN_CAPTURE_ROOT=str(dest),
               CRON_VERDICT_LOG=str(tmp_path / "verdicts.log"),
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return dest, shim, env


def _run(env, tmp_path):
    r = subprocess.run(["bash", SCRIPT], env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return (tmp_path / "verdicts.log").read_text().strip().splitlines()[-1]


def _commits(dest):
    return int(_git(dest, "rev-list", "--count", "HEAD").strip())


def test_capture_is_committed_and_tree_clean(tmp_path):
    dest, _, env = _setup(tmp_path)
    verdict = _run(env, tmp_path)
    assert "OK" in verdict and "NOT COMMITTED" not in verdict
    # the vault's exact precondition
    assert _git(dest, "status", "--porcelain") == ""
    assert _commits(dest) == 2
    assert "node-a" in _git(dest, "log", "-1", "--format=%s")


def test_changed_capture_commits_again_and_rotates_prev(tmp_path):
    dest, shim, env = _setup(tmp_path)
    _run(env, tmp_path)
    (shim / "variant_node-a.host").write_text("changed")
    verdict = _run(env, tmp_path)
    assert "OK" in verdict
    assert _git(dest, "status", "--porcelain") == ""
    assert _commits(dest) == 3
    assert "(base)" in (dest / "node-a" / "uci-export.prev.txt").read_text()
    assert "(changed)" in (dest / "node-a" / "uci-export.txt").read_text()


def test_unchanged_capture_makes_no_empty_commit(tmp_path):
    dest, _, env = _setup(tmp_path)
    _run(env, tmp_path)
    # run 2 still commits: it creates uci-export.prev.txt. From run 3 on the
    # fake ssh output and prev are identical, so there is nothing to commit.
    _run(env, tmp_path)
    before = _commits(dest)
    verdict = _run(env, tmp_path)
    assert "OK" in verdict
    assert _commits(dest) == before


def test_unrelated_human_edit_is_left_alone(tmp_path):
    dest, _, env = _setup(tmp_path)
    (dest / "README").write_text("operator's half-done edit\n")
    _run(env, tmp_path)
    # captures committed, the human's edit neither committed nor staged
    assert _git(dest, "status", "--porcelain").strip() == "M README"
    assert "README" not in _git(dest, "show", "--name-only", "--format=", "HEAD")


def test_not_a_git_repo_is_concern_not_ok(tmp_path):
    _, _, env = _setup(tmp_path, git_repo=False)
    verdict = _run(env, tmp_path)
    assert "CONCERN" in verdict and "NOT COMMITTED" in verdict


def test_commit_failure_is_concern_not_ok(tmp_path):
    dest, _, env = _setup(tmp_path)
    hook = dest / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    verdict = _run(env, tmp_path)
    assert "CONCERN" in verdict and "NOT COMMITTED" in verdict
    assert _commits(dest) == 1


def test_uncaptured_node_still_concern_and_captured_ones_committed(tmp_path):
    dest, shim, env = _setup(tmp_path)
    (shim / "unreachable_node-b.host").touch()
    verdict = _run(env, tmp_path)
    assert "CONCERN" in verdict and "UNCAPTURED=node-b" in verdict
    assert _git(dest, "status", "--porcelain") == ""
    assert (dest / "node-a" / "uci-export.txt").exists()
