"""Tests for updates.meshtasticd_apt — the apt-layer SSOT for meshtasticd
updates (2026-07-10 TUI-updates audit).

Pins the four defect classes the audit found live:
  1. "latest" must be the apt CANDIDATE, not a GitHub tag;
  2. apt holds (deliberate fleet pins) are first-class state, not invisible;
  3. an unsatisfiable candidate (distro-mismatched OBS repo) is detected by
     dry-run BEFORE the operator is promised an update;
  4. "updated" is claimed only from a RE-DERIVED version change — apt exit 0
     with the package kept back is a caught silent no-op, not a success.
"""

import subprocess
from unittest.mock import patch

import pytest

import updates.meshtasticd_apt as ma
from updates.meshtasticd_apt import (
    MeshtasticdAptState,
    RepoLine,
    _parse_policy,
    disable_repo_lines,
    find_mismatched_meshtastic_repos,
    get_meshtasticd_apt_state,
    run_meshtasticd_upgrade,
)


def _cp(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


POLICY = """meshtasticd:
  Installed: 2.7.24.58~obs472b14c~alpha
  Candidate: 2.7.26.61~obs54e0d8d~beta
  Version table:
"""

POLICY_CURRENT = """meshtasticd:
  Installed: 2.7.26.61~obs54e0d8d~beta
  Candidate: 2.7.26.61~obs54e0d8d~beta
"""

POLICY_NONE = """meshtasticd:
  Installed: (none)
  Candidate: 2.7.26.61~obs54e0d8d~beta
"""

# The field failure (2026-07-10 audit): the Debian_Testing stanza of
# the SAME version string needs libc6 2.42 while the box has 2.41.
SIM_BROKEN = """Reading package lists...
The following packages have unmet dependencies:
 meshtasticd : Depends: libc6 (>= 2.42) but 2.41-12+rpt1+deb13u3 is to be installed
E: Unable to correct problems, you have held broken packages.
"""


def _router(responses):
    """Route ma._run calls by substring of the joined command."""
    def fake(cmd, timeout=30):
        joined = " ".join(cmd)
        for key, resp in responses.items():
            if key in joined:
                if isinstance(resp, list):
                    return resp.pop(0)
                return resp
        raise AssertionError(f"unexpected command: {cmd}")
    return fake


class TestParsePolicy:
    def test_extracts_installed_and_candidate(self):
        assert _parse_policy(POLICY) == (
            "2.7.24.58~obs472b14c~alpha", "2.7.26.61~obs54e0d8d~beta")

    def test_none_maps_to_python_none(self):
        installed, candidate = _parse_policy(POLICY_NONE)
        assert installed is None
        assert candidate == "2.7.26.61~obs54e0d8d~beta"


class TestGetState:
    def test_apt_missing_reports_unavailable(self):
        with patch.object(ma.shutil, "which", return_value=None):
            state = get_meshtasticd_apt_state()
        assert state.apt_available is False

    def test_update_available_with_installable_candidate(self):
        responses = {
            "apt-cache policy": _cp(0, POLICY),
            "apt-mark showhold": _cp(0, ""),
            "dpkg --compare-versions": _cp(0),
            "apt-get -s install": _cp(0, "Inst meshtasticd ..."),
        }
        with patch.object(ma.shutil, "which", return_value="/usr/bin/x"), \
                patch.object(ma, "_run", side_effect=_router(responses)):
            state = get_meshtasticd_apt_state(simulate=True)
        assert state.update_available is True
        assert state.held is False
        assert state.candidate_installable is True
        assert state.candidate == "2.7.26.61~obs54e0d8d~beta"

    def test_hold_is_surfaced(self):
        responses = {
            "apt-cache policy": _cp(0, POLICY),
            "apt-mark showhold": _cp(0, "meshtasticd\n"),
            "dpkg --compare-versions": _cp(0),
            "apt-get -s install": _cp(0, "Inst"),
        }
        with patch.object(ma.shutil, "which", return_value="/usr/bin/x"), \
                patch.object(ma, "_run", side_effect=_router(responses)):
            state = get_meshtasticd_apt_state(simulate=True)
        assert state.held is True
        # The raw state still reports the newer candidate; policy (nag or
        # not) is the version_checker's call.
        assert state.update_available is True

    def test_broken_candidate_caught_by_simulation(self):
        responses = {
            "apt-cache policy": _cp(0, POLICY),
            "apt-mark showhold": _cp(0, ""),
            "dpkg --compare-versions": _cp(0),
            "apt-get -s install": _cp(100, SIM_BROKEN),
        }
        sentinel = [RepoLine("/etc/apt/sources.list.d/meshtastic.list", 1, "deb ...")]
        with patch.object(ma.shutil, "which", return_value="/usr/bin/x"), \
                patch.object(ma, "_run", side_effect=_router(responses)), \
                patch.object(ma, "find_mismatched_meshtastic_repos",
                             return_value=sentinel):
            state = get_meshtasticd_apt_state(simulate=True)
        assert state.candidate_installable is False
        assert "libc6" in state.blocking_detail
        assert state.mismatched_repos == sentinel

    def test_healthy_sim_with_note_banner_is_installable(self):
        """apt's dry-run always prints 'NOTE: This is only a simulation!' —
        whose tail is 'E:'. A substring check read EVERY healthy simulation
        as a broken candidate (caught live 2026-07-10); only line-anchored
        E: lines mean failure."""
        healthy = ("NOTE: This is only a simulation!\n"
                   "1 upgraded, 0 newly installed, 0 to remove\n"
                   "Inst meshtasticd [2.7.24] (2.7.26 ...)\n")
        responses = {
            "apt-cache policy": _cp(0, POLICY),
            "apt-mark showhold": _cp(0, ""),
            "dpkg --compare-versions": _cp(0),
            "apt-get -s install": _cp(0, healthy),
        }
        with patch.object(ma.shutil, "which", return_value="/usr/bin/x"), \
                patch.object(ma, "_run", side_effect=_router(responses)):
            state = get_meshtasticd_apt_state(simulate=True)
        assert state.candidate_installable is True

    def test_no_simulation_when_already_current(self):
        responses = {
            "apt-cache policy": _cp(0, POLICY_CURRENT),
            "apt-mark showhold": _cp(0, ""),
        }
        with patch.object(ma.shutil, "which", return_value="/usr/bin/x"), \
                patch.object(ma, "_run", side_effect=_router(responses)):
            state = get_meshtasticd_apt_state(simulate=True)
        assert state.update_available is False
        assert state.candidate_installable is None  # unknown, never assumed

    def test_unreadable_hold_state_is_surfaced_not_guessed(self):
        def fake(cmd, timeout=30):
            joined = " ".join(cmd)
            if "apt-cache policy" in joined:
                return _cp(0, POLICY_CURRENT)
            if "apt-mark" in joined:
                raise OSError("apt-mark missing")
            raise AssertionError(joined)
        with patch.object(ma.shutil, "which", return_value="/usr/bin/x"), \
                patch.object(ma, "_run", side_effect=fake):
            state = get_meshtasticd_apt_state(simulate=False)
        assert state.held is False
        assert "hold state unreadable" in state.error


class TestMismatchedRepos:
    def _os_release(self, tmp_path, version_id='"13"'):
        p = tmp_path / "os-release"
        p.write_text(f'ID=debian\nVERSION_ID={version_id}\nVERSION_CODENAME=trixie\n')
        return p

    def test_flags_testing_repo_on_stable_box(self, tmp_path):
        osr = self._os_release(tmp_path)
        src = tmp_path / "sources"
        src.mkdir()
        (src / "meshtastic.list").write_text(
            "deb https://download.opensuse.org/repositories/network:/Meshtastic:/beta/Debian_Testing/ /\n")
        (src / "beta.list").write_text(
            "deb http://download.opensuse.org/repositories/network:/Meshtastic:/beta/Debian_13/ /\n")
        result = find_mismatched_meshtastic_repos(src, osr)
        assert len(result) == 1
        assert "Debian_Testing" in result[0].line
        assert result[0].lineno == 1

    def test_flags_older_debian_release(self, tmp_path):
        osr = self._os_release(tmp_path)
        src = tmp_path / "sources"
        src.mkdir()
        (src / "old.list").write_text(
            "deb http://download.opensuse.org/repositories/network:/Meshtastic:/alpha/Debian_12/ /\n")
        result = find_mismatched_meshtastic_repos(src, osr)
        assert len(result) == 1

    def test_matching_release_and_comments_not_flagged(self, tmp_path):
        osr = self._os_release(tmp_path)
        src = tmp_path / "sources"
        src.mkdir()
        (src / "ok.list").write_text(
            "# deb .../network:/Meshtastic:/beta/Debian_Testing/ /\n"
            "deb http://download.opensuse.org/repositories/network:/Meshtastic:/beta/Debian_13/ /\n"
            "deb http://deb.debian.org/debian trixie main\n")
        assert find_mismatched_meshtastic_repos(src, osr) == []

    def test_unreadable_os_release_refuses_to_guess(self, tmp_path):
        src = tmp_path / "sources"
        src.mkdir()
        (src / "meshtastic.list").write_text(
            "deb .../network:/Meshtastic:/beta/Debian_Testing/ /\n")
        assert find_mismatched_meshtastic_repos(
            src, tmp_path / "missing-os-release") == []


class TestDisableRepoLines:
    def _direct_write(self, path, content, timeout=10):
        from pathlib import Path
        Path(path).write_text(content)
        return True, "ok"

    def test_comments_out_only_target_line(self, tmp_path):
        f = tmp_path / "meshtastic.list"
        f.write_text("deb bad Debian_Testing /\ndeb good Debian_13 /\n")
        entry = RepoLine(str(f), 1, "deb bad Debian_Testing /")
        with patch.object(ma, "_sudo_write", self._direct_write):
            ok, detail = disable_repo_lines([entry])
        assert ok is True
        lines = f.read_text().splitlines()
        assert lines[0].startswith("# disabled by MeshForge")
        assert lines[1] == "deb good Debian_13 /"
        backup = tmp_path / "meshtastic.list.meshforge-bak"
        assert backup.exists()
        assert backup.read_text().splitlines()[0] == "deb bad Debian_Testing /"

    def test_existing_backup_not_clobbered(self, tmp_path):
        f = tmp_path / "meshtastic.list"
        f.write_text("deb bad Debian_Testing /\n")
        backup = tmp_path / "meshtastic.list.meshforge-bak"
        backup.write_text("ORIGINAL FIRST BACKUP\n")
        entry = RepoLine(str(f), 1, "deb bad Debian_Testing /")
        with patch.object(ma, "_sudo_write", self._direct_write):
            ok, _ = disable_repo_lines([entry])
        assert ok is True
        assert backup.read_text() == "ORIGINAL FIRST BACKUP\n"

    def test_stale_lineno_fails_loud(self, tmp_path):
        f = tmp_path / "meshtastic.list"
        f.write_text("deb only-one-line /\n")
        entry = RepoLine(str(f), 5, "deb phantom /")
        with patch.object(ma, "_sudo_write", self._direct_write):
            ok, detail = disable_repo_lines([entry])
        assert ok is False
        assert "changed underneath" in detail

    def test_empty_input_is_noop(self):
        ok, detail = disable_repo_lines([])
        assert ok is True


class TestRunUpgrade:
    """Success is claimed ONLY from a re-derived version change."""

    def test_verified_upgrade_success(self):
        responses = {
            "apt-cache policy": [_cp(0, POLICY), _cp(0, POLICY_CURRENT)],
            "apt-get update": _cp(0),
            "apt-get install": _cp(0, "Setting up meshtasticd..."),
        }
        with patch.object(ma, "_run", side_effect=_router(responses)):
            ok, detail = run_meshtasticd_upgrade()
        assert ok is True
        assert "2.7.24.58" in detail and "2.7.26.61" in detail
        assert "verified" in detail

    def test_silent_noop_is_caught(self):
        # apt exit 0 but the version did not move (kept back): the old code
        # reported "Update Complete" and restarted the daemon for nothing.
        responses = {
            "apt-cache policy": [_cp(0, POLICY), _cp(0, POLICY)],
            "apt-get update": _cp(0),
            "apt-get install": _cp(0, "0 upgraded, 0 newly installed"),
        }
        with patch.object(ma, "_run", side_effect=_router(responses)):
            ok, detail = run_meshtasticd_upgrade()
        assert ok is False
        assert "kept back" in detail

    def test_apt_failure_surfaces_stderr(self):
        responses = {
            "apt-cache policy": [_cp(0, POLICY), _cp(0, POLICY)],
            "apt-get update": _cp(0),
            "apt-get install": _cp(100, "", "E: broken packages"),
        }
        with patch.object(ma, "_run", side_effect=_router(responses)):
            ok, detail = run_meshtasticd_upgrade()
        assert ok is False
        assert "E: broken packages" in detail

    def test_not_installed_refuses(self):
        responses = {"apt-cache policy": _cp(0, POLICY_NONE)}
        with patch.object(ma, "_run", side_effect=_router(responses)):
            ok, detail = run_meshtasticd_upgrade()
        assert ok is False
        assert "not installed" in detail

    def test_unhold_failure_aborts_before_install(self):
        calls = []
        def fake(cmd, timeout=30):
            joined = " ".join(cmd)
            calls.append(joined)
            if "apt-cache policy" in joined:
                return _cp(0, POLICY)
            if "apt-mark unhold" in joined:
                return _cp(1, "", "permission denied")
            raise AssertionError(joined)
        with patch.object(ma, "_run", side_effect=fake):
            ok, detail = run_meshtasticd_upgrade(unhold=True, rehold=True)
        assert ok is False
        assert "unhold failed" in detail
        assert not any("apt-get install" in c for c in calls)

    def test_unhold_upgrade_rehold_sequence(self):
        calls = []
        def fake(cmd, timeout=30):
            joined = " ".join(cmd)
            calls.append(joined)
            if "apt-mark unhold" in joined:
                return _cp(0, "meshtasticd was already not held? no: unheld")
            if "apt-mark hold" in joined:
                return _cp(0, "meshtasticd set on hold.")
            if "apt-cache policy" in joined:
                return _cp(0, POLICY if len([c for c in calls if "policy" in c]) <= 1
                           else POLICY_CURRENT)
            if "apt-get update" in joined:
                return _cp(0)
            if "apt-get install" in joined:
                return _cp(0, "Setting up...")
            raise AssertionError(joined)
        with patch.object(ma, "_run", side_effect=fake):
            ok, detail = run_meshtasticd_upgrade(unhold=True, rehold=True)
        assert ok is True
        unhold_idx = next(i for i, c in enumerate(calls) if "unhold" in c)
        install_idx = next(i for i, c in enumerate(calls) if "apt-get install" in c)
        hold_idx = next(i for i, c in enumerate(calls)
                        if "apt-mark hold" in c)
        assert unhold_idx < install_idx < hold_idx

    def test_failed_rehold_is_a_loud_witness(self):
        # A lost pin is a posture change; it must reach the operator's eyes.
        def fake(cmd, timeout=30):
            joined = " ".join(cmd)
            if "apt-mark unhold" in joined:
                return _cp(0)
            if "apt-mark hold" in joined:
                return _cp(1, "", "dpkg database locked")
            if "apt-cache policy" in joined:
                fake.n = getattr(fake, "n", 0) + 1
                return _cp(0, POLICY if fake.n == 1 else POLICY_CURRENT)
            if "apt-get update" in joined:
                return _cp(0)
            if "apt-get install" in joined:
                return _cp(0)
            raise AssertionError(joined)
        with patch.object(ma, "_run", side_effect=fake):
            ok, detail = run_meshtasticd_upgrade(unhold=True, rehold=True)
        assert ok is True
        assert "UNPINNED" in detail

    def test_apt_update_problem_noted_but_upgrade_proceeds(self):
        responses = {
            "apt-cache policy": [_cp(0, POLICY), _cp(0, POLICY_CURRENT)],
            "apt-get update": _cp(100, "", "Err: repo unreachable"),
            "apt-get install": _cp(0),
        }
        with patch.object(ma, "_run", side_effect=_router(responses)):
            ok, detail = run_meshtasticd_upgrade()
        assert ok is True
        assert "apt-get update reported a problem" in detail

    def test_noninteractive_and_conffile_guards_present(self):
        # The old command hung on prompts (no TTY under capture_output) and
        # could clobber operator config on conffile conflicts (#22/#58).
        seen = []
        responses = {
            "apt-cache policy": [_cp(0, POLICY), _cp(0, POLICY_CURRENT)],
            "apt-get update": _cp(0),
            "apt-get install": _cp(0),
        }
        router = _router(responses)
        def spy(cmd, timeout=30):
            seen.append(cmd)
            return router(cmd, timeout=timeout)
        with patch.object(ma, "_run", side_effect=spy):
            run_meshtasticd_upgrade()
        install = next(c for c in seen if "install" in c)
        joined = " ".join(install)
        assert "DEBIAN_FRONTEND=noninteractive" in joined
        assert "--only-upgrade" in joined
        assert "-y" in install
        assert "--force-confold" in joined


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
