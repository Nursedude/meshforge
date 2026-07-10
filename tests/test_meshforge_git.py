"""Tests for updates.meshforge_git — the git-layer SSOT for MeshForge
self-update (2026-07-10 follow-up: "I have not successfully updated
MeshForge").

Pins the redesign's contracts:
  1. update state = HEAD vs origin/main after a real fetch — never release
     version strings (which only move on releases and hid every update);
  2. a failed fetch is UNKNOWN, never "up to date";
  3. git runs AS THE REPO OWNER when euid is root (no root-owned objects
     in an operator-owned checkout);
  4. dirty tree / diverged branch refuse loudly — ``--ff-only`` only;
  5. "updated" is claimed only when HEAD is RE-READ equal to the remote SHA.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import updates.meshforge_git as mg
from updates.meshforge_git import (
    MeshForgeGitState,
    get_meshforge_git_state,
    meshforge_services_to_restart,
    requirements_changed,
    run_meshforge_git_update,
)

SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _cp(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


def _git_router(responses):
    """Route mg._git calls by the first distinctive git arg."""
    def fake(repo_dir, args, timeout=30, as_owner=True):
        joined = " ".join(args)
        for key, resp in responses.items():
            if key in joined:
                if isinstance(resp, list):
                    return resp.pop(0)
                return resp
        raise AssertionError(f"unexpected git args: {args}")
    return fake


def _repo(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


class TestGetState:
    def test_not_a_repo(self, tmp_path):
        state = get_meshforge_git_state(repo_dir=tmp_path)
        assert state.is_git_repo is False

    def test_behind_flags_update(self, tmp_path):
        repo = _repo(tmp_path)
        responses = {
            "fetch": _cp(0),
            "rev-parse HEAD": _cp(0, SHA_A + "\n"),
            "rev-parse origin/main": _cp(0, SHA_B + "\n"),
            "rev-list": _cp(0, "0\t3\n"),
            "status": _cp(0, ""),
        }
        with patch.object(mg, "_git", side_effect=_git_router(responses)):
            state = get_meshforge_git_state(repo_dir=repo)
        assert state.update_available is True
        assert state.behind == 3
        assert state.ahead == 0
        assert state.dirty is False
        assert state.fetch_ok is True

    def test_fetch_failure_is_unknown_not_up_to_date(self, tmp_path):
        """Offline must never read as verified-current."""
        repo = _repo(tmp_path)
        responses = {
            "fetch": _cp(128, "", "could not resolve host"),
            "rev-parse HEAD": _cp(0, SHA_A + "\n"),
            "rev-parse origin/main": _cp(0, SHA_A + "\n"),
            "rev-list": _cp(0, "0\t0\n"),
            "status": _cp(0, ""),
        }
        with patch.object(mg, "_git", side_effect=_git_router(responses)):
            state = get_meshforge_git_state(repo_dir=repo)
        assert state.fetch_ok is False
        assert "fetch failed" in state.error

    def test_dirty_tree_detected(self, tmp_path):
        repo = _repo(tmp_path)
        responses = {
            "fetch": _cp(0),
            "rev-parse HEAD": _cp(0, SHA_A + "\n"),
            "rev-parse origin/main": _cp(0, SHA_A + "\n"),
            "rev-list": _cp(0, "0\t0\n"),
            "status": _cp(0, " M src/foo.py\n"),
        }
        with patch.object(mg, "_git", side_effect=_git_router(responses)):
            state = get_meshforge_git_state(repo_dir=repo)
        assert state.dirty is True
        assert state.update_available is False


class TestOwnerDrop:
    def test_git_runs_as_repo_owner_under_root(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg.os, "geteuid", return_value=0), \
                patch.object(mg, "_repo_owner", return_value=(1000, "op")), \
                patch.object(mg.subprocess, "run",
                             return_value=_cp(0, "")) as run:
            mg._git(repo, ["status", "--porcelain"])
        cmd = run.call_args.args[0]
        assert cmd[:4] == ["sudo", "-u", "op", "-H"]
        assert cmd[4] == "git"

    def test_no_drop_when_repo_is_root_owned(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg.os, "geteuid", return_value=0), \
                patch.object(mg, "_repo_owner", return_value=(0, "root")), \
                patch.object(mg.subprocess, "run",
                             return_value=_cp(0, "")) as run:
            mg._git(repo, ["status", "--porcelain"])
        assert run.call_args.args[0][0] == "git"

    def test_no_drop_when_not_root(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg.os, "geteuid", return_value=1000), \
                patch.object(mg.subprocess, "run",
                             return_value=_cp(0, "")) as run:
            mg._git(repo, ["status", "--porcelain"])
        assert run.call_args.args[0][0] == "git"


class TestRunUpdate:
    def _state(self, **kw):
        defaults = dict(is_git_repo=True, head=SHA_A, remote_head=SHA_B,
                        behind=2, ahead=0, dirty=False,
                        update_available=True, fetch_ok=True)
        defaults.update(kw)
        return MeshForgeGitState(**defaults)

    def test_verified_fast_forward(self, tmp_path):
        repo = _repo(tmp_path)
        after = self._state(head=SHA_B)
        with patch.object(mg, "get_meshforge_git_state",
                          side_effect=[self._state(), after]), \
                patch.object(mg, "_git", return_value=_cp(0, "Fast-forward")):
            ok, detail = run_meshforge_git_update(repo_dir=repo)
        assert ok is True
        assert "verified" in detail

    def test_head_not_moving_is_caught(self, tmp_path):
        """git exit 0 but HEAD unchanged: the silent no-op must not read as
        an applied update."""
        repo = _repo(tmp_path)
        after = self._state(head=SHA_A)  # did not move
        with patch.object(mg, "get_meshforge_git_state",
                          side_effect=[self._state(), after]), \
                patch.object(mg, "_git", return_value=_cp(0, "")):
            ok, detail = run_meshforge_git_update(repo_dir=repo)
        assert ok is False
        assert "silent no-op" in detail

    def test_dirty_refuses(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg, "get_meshforge_git_state",
                          return_value=self._state(dirty=True)):
            ok, detail = run_meshforge_git_update(repo_dir=repo)
        assert ok is False
        assert "local modifications" in detail

    def test_ahead_refuses_to_merge(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg, "get_meshforge_git_state",
                          return_value=self._state(ahead=1)):
            ok, detail = run_meshforge_git_update(repo_dir=repo)
        assert ok is False
        assert "AHEAD" in detail

    def test_offline_refuses(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg, "get_meshforge_git_state",
                          return_value=self._state(fetch_ok=False,
                                                   error="fetch failed")):
            ok, detail = run_meshforge_git_update(repo_dir=repo)
        assert ok is False

    def test_already_current_is_success_noop(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg, "get_meshforge_git_state",
                          return_value=self._state(behind=0,
                                                   update_available=False)):
            ok, detail = run_meshforge_git_update(repo_dir=repo)
        assert ok is True
        assert "up to date" in detail


class TestRequirementsChanged:
    def test_detects_requirements_diff(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg, "_git",
                          return_value=_cp(0, "src/a.py\nrequirements/core.txt\n")):
            assert requirements_changed(SHA_A, SHA_B, repo_dir=repo) is True

    def test_no_requirements_diff(self, tmp_path):
        repo = _repo(tmp_path)
        with patch.object(mg, "_git", return_value=_cp(0, "src/a.py\n")):
            assert requirements_changed(SHA_A, SHA_B, repo_dir=repo) is False

    def test_unknown_diff_returns_none(self, tmp_path):
        """Unknown must NOT read as 'no change' — the caller runs the deps
        step on None (skipping on unknown is the silent-failure direction)."""
        repo = _repo(tmp_path)
        with patch.object(mg, "_git", return_value=_cp(128, "", "bad sha")):
            assert requirements_changed(SHA_A, SHA_B, repo_dir=repo) is None


class TestServicesToRestart:
    def test_parses_active_units(self):
        out = ("meshforge.service loaded active running MeshForge NOC\n"
               "meshforge-map.service loaded active running MeshForge Map\n")
        with patch.object(mg.subprocess, "run", return_value=_cp(0, out)):
            assert meshforge_services_to_restart() == [
                "meshforge", "meshforge-map"]

    def test_failure_returns_empty(self):
        with patch.object(mg.subprocess, "run", side_effect=OSError("no systemctl")):
            assert meshforge_services_to_restart() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
