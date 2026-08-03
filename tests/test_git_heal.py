"""Tests for scripts/lib/git_heal.sh — the pre-pull .git ownership repair.

Born 2026-08-02 (moc3): a `sudo git` run on 07-31 left 24 root-owned objects
under /opt/meshforge/.git, and every unprivileged pull afterwards died with
`insufficient permission for adding an object to repository database`. The box
silently stopped receiving code and sat 4 commits behind until a human tried
to deploy. fleet_sync.sh had warned against `sudo git pull` in a COMMENT since
2026-05-03 and it happened anyway.

The tokens are a contract read by the deploy scripts, so they are pinned here.
The tri-state matters more than the happy path: a box that CANNOT heal must
never be indistinguishable from a box that had NOTHING to heal
(honest_failure_modes #9), and an unreadable .git must never read as "none"
(#2 — unobservable is not healthy).
"""

import os
import subprocess

LIB = os.path.join(os.path.dirname(__file__), "..", "scripts", "lib",
                   "git_heal.sh")


def _run(repo_dir, path_prepend=None):
    """Invoke the function the way the deploy scripts do: source, then call."""
    env = dict(os.environ)
    if path_prepend:
        env["PATH"] = f"{path_prepend}:{env['PATH']}"
    script = f'. "{os.path.abspath(LIB)}"; git_heal_ownership "{repo_dir}"'
    proc = subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60, env=env)
    return proc.stdout.strip(), proc.returncode


class TestObservability:
    """#2: unobservable is never 'none'."""

    def test_missing_gitdir_is_unobservable_not_none(self, tmp_path):
        out, rc = _run(str(tmp_path))
        assert out.startswith("HEAL_UNOBSERVABLE"), out
        assert "no_gitdir" in out
        assert rc != 0, "an unobservable repo must not exit 0"

    def test_unreadable_gitdir_is_unobservable_not_none(self, tmp_path):
        gitdir = tmp_path / ".git"
        gitdir.mkdir()
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
        os.chmod(gitdir, 0o000)
        try:
            out, rc = _run(str(tmp_path))
        finally:
            os.chmod(gitdir, 0o755)
        if os.geteuid() == 0:
            # root ignores the mode bits, so this box cannot exercise the leg
            return
        assert out.startswith("HEAL_UNOBSERVABLE"), out
        assert rc != 0


class TestCleanRepo:
    """The healthy case must be silent, cheap, and exit 0 — this runs before
    every deploy on every box."""

    def test_all_mine_is_heal_none(self, tmp_path):
        gitdir = tmp_path / ".git"
        (gitdir / "objects" / "ab").mkdir(parents=True)
        (gitdir / "objects" / "ab" / "cafe").write_text("x")
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
        out, rc = _run(str(tmp_path))
        assert out == "HEAL_NONE", out
        assert rc == 0

    def test_clean_repo_never_invokes_sudo(self, tmp_path):
        """A no-op deploy must not shell out to sudo at all. Planted a `sudo`
        on PATH that fails loudly if called."""
        gitdir = tmp_path / ".git"
        gitdir.mkdir()
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        sentinel = tmp_path / "sudo_was_called"
        fake_sudo = fakebin / "sudo"
        fake_sudo.write_text(
            f'#!/bin/sh\ntouch "{sentinel}"\nexit 1\n')
        os.chmod(fake_sudo, 0o755)

        out, rc = _run(str(tmp_path), path_prepend=str(fakebin))
        assert out == "HEAL_NONE", out
        assert rc == 0
        assert not sentinel.exists(), (
            "clean repo called sudo — every deploy would pay for it")


class TestTokenContract:
    """The deploy scripts parse the first field. Renaming a token silently
    changes what a poisoned box reports."""

    def _src(self):
        with open(os.path.abspath(LIB), encoding="utf-8") as fh:
            return fh.read()

    def test_all_five_tokens_present(self):
        src = self._src()
        for tok in ("HEAL_NONE", "HEAL_OK", "HEAL_PARTIAL", "HEAL_DENIED",
                    "HEAL_UNOBSERVABLE"):
            assert f'echo "{tok}' in src, f"{tok} no longer emitted"

    def test_sudo_is_non_interactive(self):
        """A deploy must never block on a password prompt — a hung fleet
        deploy is worse than a reported failure."""
        src = self._src()
        assert "sudo -n chown" in src, (
            "sudo lost -n: a poisoned box would hang the deploy on a prompt")

    def test_denied_is_distinct_from_none(self):
        """#9: a box that cannot heal must not look like one with nothing to
        heal. Pin that the denied branch reports rather than falling through."""
        src = self._src()
        assert "HEAL_DENIED" in src
        denied_block = src.split("sudo -n chown", 1)[1][:220]
        assert "HEAL_DENIED" in denied_block, (
            "the sudo failure path no longer reports — it now swallows")

    def test_standalone_guard_requires_an_argument(self):
        """Callers embed this file's TEXT into a remote script run by
        `bash -s`, where $0 and BASH_SOURCE[0] are both 'bash'. Without the
        arg check the heal fires against a defaulted path at the top of every
        remote script."""
        src = self._src()
        assert '[ "$#" -gt 0 ]' in src, (
            "standalone guard lost its argument check — embedding this file "
            "would fire a stray heal on every remote script")


class TestPreventionWasRejected:
    """A/B drilled on moc3 2026-08-02 and REJECTED — pinned so nobody
    'simplifies' the chown away in favour of the config-only approach.

    control   (no shared mode): 20/20 root-owned dirs unwritable
    treatment (shared+setgid):   0/20 unwritable  <-- object store IS fixed
    ...but BOTH arms then died on:
        error: cannot open '.git/FETCH_HEAD': Permission denied
    because FETCH_HEAD is written with root's umask, outside what
    core.sharedRepository governs. There is no repo-config-only cure."""

    def _src(self):
        with open(os.path.abspath(LIB), encoding="utf-8") as fh:
            return fh.read()

    def test_rejection_rationale_is_recorded(self):
        src = self._src()
        assert "FETCH_HEAD" in src, (
            "the reason core.sharedRepository is insufficient is no longer "
            "recorded — the next reader will re-propose it")

    def test_heal_covers_the_whole_gitdir_not_just_objects(self):
        """Chowning only objects/ would reproduce the drill's failure."""
        src = self._src()
        assert 'chown -R "$me:$grp" "$gitdir"' in src, (
            "heal narrowed below the whole .git — FETCH_HEAD and refs are "
            "exactly what the config-only approach missed")
