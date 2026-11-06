"""fleet_hosts resolver — unit pins + shell↔python parity.

The tree converged ~13 independent copies of the fleet_hosts resolution
chain (2026-07-29) down to exactly TWO implementations:

    scripts/lib/fleet_hosts.sh      (sourced by the 8 shell consumers)
    src/utils/fleet_hosts.py        (imported by the 5 python consumers)

Two implementations is the floor (python cannot source bash), so this file
is the drift guard: the parity tests feed BOTH the same fixture tree and
require the same answer. Change one, this fails until you change the other.

Every assertion here is pinned — env, HOME, and (python-side) the /etc tier
are injected, so no verdict depends on the machine running the suite
(feedback_tests_must_pin_ambient_state). Shell parity cases are restricted
to resolutions that complete BEFORE the /etc tiers, which cannot be
injected into the shell lib without root.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_hosts import (  # noqa: E402
    _parse_fleet_hosts_text as parse_fleet_hosts_text,
    resolve_fleet_hosts,
    resolve_fleet_hosts_file,
)

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "fleet_hosts.sh"


# ── the shared parser ────────────────────────────────────────────────────


class TestParser:
    def test_plain_hosts(self):
        assert parse_fleet_hosts_text("moc\nmoc1\nmoc2\n") == ["moc", "moc1", "moc2"]

    def test_trailing_comment_yields_the_host_not_a_garbage_token(self):
        """'moc1  # retired' is host moc1 — the copy-time divergence that
        motivated the lib (the gate verified moc1 while the deployer ssh'd
        the whole commented line)."""
        assert parse_fleet_hosts_text("moc1  # retired 07-28\n") == ["moc1"]

    def test_full_line_comments_and_blanks_ignored(self):
        assert parse_fleet_hosts_text("# all\n\n  \nmoc\n#moc9\n") == ["moc"]

    def test_whitespace_splits_multiple_tokens(self):
        assert parse_fleet_hosts_text("moc moc1\tmoc2\n") == ["moc", "moc1", "moc2"]

    def test_empty_document(self):
        assert parse_fleet_hosts_text("") == []


# ── the python resolver, fully pinned ────────────────────────────────────


def _tree(tmp_path, files):
    """Build {relpath: text} under tmp_path; returns (home, etc)."""
    home = tmp_path / "home"
    etc = tmp_path / "etc"
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    home.mkdir(exist_ok=True)
    etc.mkdir(exist_ok=True)
    return home, etc


class TestPythonResolver:
    def test_chain_order_generic(self, tmp_path):
        home, etc = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts": "home-generic\n",
            "etc/fleet_hosts": "etc-generic\n",
        })
        got = resolve_fleet_hosts("/opt/meshforge",
                                  env={"HOME": str(home)}, etc_dir=str(etc))
        assert got == ["home-generic"]

    def test_per_repo_tier_wins_over_generic(self, tmp_path):
        home, etc = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts.meshforge": "per-repo\n",
            "home/.config/meshforge/fleet_hosts": "generic\n",
        })
        got = resolve_fleet_hosts("/opt/meshforge",
                                  env={"HOME": str(home)}, etc_dir=str(etc))
        assert got == ["per-repo"]

    def test_etc_tier_reached_when_home_empty(self, tmp_path):
        home, etc = _tree(tmp_path, {"etc/fleet_hosts": "etc-box\n"})
        got = resolve_fleet_hosts("/opt/meshforge",
                                  env={"HOME": str(home)}, etc_dir=str(etc))
        assert got == ["etc-box"]

    def test_home_unset_skips_user_tiers_never_defaults(self, tmp_path):
        """The old copies disagreed here (fleet_pull aborted, honest_status
        read /root) — the resolver skips, and still reaches /etc."""
        _, etc = _tree(tmp_path, {"etc/fleet_hosts": "etc-only\n"})
        got = resolve_fleet_hosts("/opt/meshforge", env={}, etc_dir=str(etc))
        assert got == ["etc-only"]

    def test_env_override_wins(self, tmp_path):
        home, etc = _tree(tmp_path, {
            "override": "ov-host\n",
            "home/.config/meshforge/fleet_hosts": "generic\n",
        })
        env = {"HOME": str(home), "MESHFORGE_FLEET_HOSTS": str(tmp_path / "override")}
        assert resolve_fleet_hosts(env=env, etc_dir=str(etc)) == ["ov-host"]

    def test_legacy_fleet_hosts_alias_honored(self, tmp_path):
        home, etc = _tree(tmp_path, {"override": "legacy\n"})
        env = {"HOME": str(home), "FLEET_HOSTS": str(tmp_path / "override")}
        assert resolve_fleet_hosts(env=env, etc_dir=str(etc)) == ["legacy"]

    def test_set_but_missing_override_is_authoritative_no_fallthrough(self, tmp_path):
        """A degraded override must not read as the box's real config
        (honest_failure_modes #1 — the rule mini's rollup pioneered)."""
        home, etc = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts": "real-config\n",
        })
        env = {"HOME": str(home),
               "MESHFORGE_FLEET_HOSTS": str(tmp_path / "nope")}
        assert resolve_fleet_hosts_file(env=env, etc_dir=str(etc)) is None
        assert resolve_fleet_hosts(env=env, etc_dir=str(etc)) == []

    def test_primary_env_key_outranks_legacy_alias(self, tmp_path):
        home, etc = _tree(tmp_path, {"a": "primary\n", "b": "legacy\n"})
        env = {"HOME": str(home),
               "MESHFORGE_FLEET_HOSTS": str(tmp_path / "a"),
               "FLEET_HOSTS": str(tmp_path / "b")}
        assert resolve_fleet_hosts(env=env, etc_dir=str(etc)) == ["primary"]

    def test_no_list_anywhere(self, tmp_path):
        home, etc = _tree(tmp_path, {})
        assert resolve_fleet_hosts_file(env={"HOME": str(home)},
                                        etc_dir=str(etc)) is None
        assert resolve_fleet_hosts(env={"HOME": str(home)},
                                   etc_dir=str(etc)) == []


# ── shell↔python parity — the 2-implementation drift guard ───────────────


def _run_shell(repo_dir: str, env: dict) -> tuple[str, list[str]]:
    """Drive the REAL shell lib. Returns (file, hosts); ('', []) on rc 1."""
    script = (
        'set -u; . "{lib}"; '
        'if fleet_hosts_resolve "$1"; then '
        '  printf "%s\\n" "$FLEET_HOSTS_FILE"; '
        '  printf "%s\\n" "$FLEET_HOSTS_LIST"; '
        'fi'
    ).format(lib=_LIB)
    full_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    full_env.update(env)
    proc = subprocess.run(["bash", "-c", script, "bash", repo_dir],
                          capture_output=True, text=True, timeout=30,
                          env=full_env)
    lines = [ln for ln in proc.stdout.splitlines()]
    if not lines:
        return "", []
    return lines[0], [h for h in lines[1:] if h]


def _both(repo_dir, tmp_path, env):
    """(shell_answer, python_answer) over the same fixture tree. env carries
    HOME/overrides; python gets the same mapping. Cases must resolve BEFORE
    the /etc tiers (not injectable into shell without root) — an /etc-tier
    parity case would depend on the machine's real /etc/meshforge."""
    sf, sh = _run_shell(repo_dir, env)
    pf = resolve_fleet_hosts_file(repo_dir, env=env,
                                  etc_dir=str(tmp_path / "no-etc"))
    ph = resolve_fleet_hosts(repo_dir, env=env,
                             etc_dir=str(tmp_path / "no-etc"))
    return (sf, sh), (str(pf) if pf else "", ph)


class TestShellPythonParity:
    def test_generic_home_tier(self, tmp_path):
        home, _ = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts": "moc\nmoc1  # retired\n",
        })
        shell, py = _both("/opt/meshforge", tmp_path, {"HOME": str(home)})
        assert shell == py
        assert shell[1] == ["moc", "moc1"]

    def test_per_repo_tier_wins_in_both(self, tmp_path):
        home, _ = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts.meshforge": "scoped\n",
            "home/.config/meshforge/fleet_hosts": "generic\n",
        })
        shell, py = _both("/opt/meshforge", tmp_path, {"HOME": str(home)})
        assert shell == py
        assert shell[1] == ["scoped"]

    def test_env_override_and_comment_parsing(self, tmp_path):
        _tree(tmp_path, {"ov": "a b\t# c d\nmoc2\n"})
        env = {"MESHFORGE_FLEET_HOSTS": str(tmp_path / "ov")}
        shell, py = _both("/opt/meshforge", tmp_path, env)
        assert shell == py
        assert shell[1] == ["a", "b", "moc2"]

    def test_legacy_alias_parity(self, tmp_path):
        _tree(tmp_path, {"ov": "legacy-host\n"})
        env = {"FLEET_HOSTS": str(tmp_path / "ov")}
        shell, py = _both("/opt/meshforge", tmp_path, env)
        assert shell == py
        assert shell[1] == ["legacy-host"]

    def test_missing_override_fails_both_no_fallthrough(self, tmp_path):
        home, _ = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts": "real\n",
        })
        env = {"HOME": str(home),
               "MESHFORGE_FLEET_HOSTS": str(tmp_path / "nope")}
        shell, py = _both("/opt/meshforge", tmp_path, env)
        assert shell == ("", [])
        assert py == ("", [])


# ── the delegating wrappers stay honest ──────────────────────────────────


class TestWrappersDelegate:
    def test_rollup_wrapper_delegates(self, tmp_path):
        from mini_dudeai.rollup import resolve_fleet_hosts as rollup_resolve
        home, _ = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts": "wrapped\n",
        })
        # env-injected: the wrapper must pass env through to the resolver.
        # /etc tier not injectable through this legacy signature — use an
        # authoritative override so the case cannot fall through to it.
        env = {"HOME": str(home),
               "MESHFORGE_FLEET_HOSTS":
                   str(home / ".config" / "meshforge" / "fleet_hosts")}
        assert rollup_resolve(env) == ["wrapped"]

    def test_dup_collector_has_no_parser_of_its_own(self):
        import utils.fleet_dup_collector as fdc
        assert not hasattr(fdc, "parse_fleet_hosts")   # r11: one reader


# ── r7e #2: a PRESENT-but-unreadable tier errors, never falls through ────
#
# Operator 2026-11-05: "quietly failing is not an answer". Before this, a
# tier that EXISTED but could not be read was skipped as if absent, so a
# root-only /etc per-repo list (or a chmod slip on the per-repo home list)
# silently handed every consumer the NEXT tier's list — a different fleet,
# reported as the fleet. Absent stays "next tier"; present-but-unreadable
# (permission, a directory, a dangling symlink) is an ERROR naming the path.

import pytest  # noqa: E402

from utils.fleet_hosts import FleetHostsUnreadable  # noqa: E402

_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
needs_nonroot = pytest.mark.skipif(_ROOT, reason="root reads a mode-000 file")


def _unreadable(p: Path) -> Path:
    p.chmod(0)
    return p


class TestPresentButUnreadableTier:
    @needs_nonroot
    def test_unreadable_higher_tier_raises_not_falls_through(self, tmp_path):
        home, etc = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts.meshforge": "per-repo\n",
            "home/.config/meshforge/fleet_hosts": "generic\n",
        })
        bad = _unreadable(home / ".config/meshforge/fleet_hosts.meshforge")
        env = {"HOME": str(home)}
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file("/opt/meshforge", env=env, etc_dir=str(etc))
        assert ei.value.path == bad and str(bad) in str(ei.value)
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts("/opt/meshforge", env=env, etc_dir=str(etc))

    @needs_nonroot
    def test_unreadable_etc_tier_raises_even_with_a_generic_home_list(self, tmp_path):
        """THE realistic shape: a root-only /etc per-repo list read by the
        operator — the old resolver silently used ~/.config's generic one."""
        home, etc = _tree(tmp_path, {
            "etc/fleet_hosts.meshforge": "etc-per-repo\n",
            "home/.config/meshforge/fleet_hosts": "generic\n",
        })
        _unreadable(etc / "fleet_hosts.meshforge")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts("/opt/meshforge", env={"HOME": str(home)},
                                etc_dir=str(etc))

    @needs_nonroot
    def test_unreadable_last_tier_raises_not_no_list(self, tmp_path):
        home, etc = _tree(tmp_path, {"etc/fleet_hosts": "x\n"})
        _unreadable(etc / "fleet_hosts")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts_file(env={"HOME": str(home)}, etc_dir=str(etc))

    def test_a_directory_at_a_tier_path_raises(self, tmp_path):
        home, etc = _tree(tmp_path, {"home/.config/meshforge/fleet_hosts": "g\n"})
        (home / ".config/meshforge/fleet_hosts.meshforge").mkdir()
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts_file(env={"HOME": str(home)}, etc_dir=str(etc))

    def test_a_dangling_symlink_at_a_tier_path_raises(self, tmp_path):
        home, etc = _tree(tmp_path, {"home/.config/meshforge/fleet_hosts": "g\n"})
        (home / ".config/meshforge/fleet_hosts.meshforge").symlink_to(tmp_path / "gone")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts_file(env={"HOME": str(home)}, etc_dir=str(etc))

    @needs_nonroot
    def test_unreadable_env_override_raises(self, tmp_path):
        home, etc = _tree(tmp_path, {"ov": "x\n"})
        _unreadable(tmp_path / "ov")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts_file(
                env={"HOME": str(home), "MESHFORGE_FLEET_HOSTS": str(tmp_path / "ov")},
                etc_dir=str(etc))

    def test_absent_tiers_still_fall_through(self, tmp_path):
        """Absent is not unreadable: the chain keeps its order."""
        home, etc = _tree(tmp_path, {"home/.config/meshforge/fleet_hosts": "g\n"})
        assert resolve_fleet_hosts(env={"HOME": str(home)}, etc_dir=str(etc)) == ["g"]

    def test_a_read_failure_after_resolve_raises_not_empty(self, tmp_path, monkeypatch):
        """resolve_fleet_hosts used to answer [] (= 'no fleet') on an OSError
        between resolve and read."""
        home, etc = _tree(tmp_path, {"home/.config/meshforge/fleet_hosts": "g\n"})

        def boom(self, *a, **k):
            raise PermissionError(13, "denied")
        monkeypatch.setattr(Path, "read_text", boom)
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts(env={"HOME": str(home)}, etc_dir=str(etc))

    def test_it_is_an_oserror(self):
        assert issubclass(FleetHostsUnreadable, OSError)


def _run_shell_rc(repo_dir: str, env: dict):
    script = ('set -u; . "{lib}"; fleet_hosts_resolve "$1"; rc=$?; '
              'printf "rc=%s\\nfile=%s\\nerr=%s\\n" "$rc" "$FLEET_HOSTS_FILE" '
              '"${{FLEET_HOSTS_ERROR:-}}"').format(lib=_LIB)
    full_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    full_env.update(env)
    p = subprocess.run(["bash", "-c", script, "bash", repo_dir],
                       capture_output=True, text=True, timeout=30, env=full_env)
    out = dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln)
    return out, p.stderr


class TestShellUnreadableParity:
    @needs_nonroot
    def test_unreadable_higher_tier_rc2_names_the_path(self, tmp_path):
        home, _ = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts.meshforge": "per-repo\n",
            "home/.config/meshforge/fleet_hosts": "generic\n",
        })
        bad = _unreadable(home / ".config/meshforge/fleet_hosts.meshforge")
        out, err = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
        assert out["rc"] == "2" and out["file"] == ""
        assert str(bad) in out["err"] and str(bad) in err, (out, err)
        with pytest.raises(FleetHostsUnreadable) as ei:   # python agrees
            resolve_fleet_hosts_file(env={"HOME": str(home)},
                                     etc_dir=str(tmp_path / "no-etc"))
        assert ei.value.path == bad

    def test_directory_at_tier_rc2(self, tmp_path):
        home, _ = _tree(tmp_path, {"home/.config/meshforge/fleet_hosts": "g\n"})
        (home / ".config/meshforge/fleet_hosts.meshforge").mkdir()
        out, _ = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
        assert out["rc"] == "2"

    def test_dangling_symlink_rc2(self, tmp_path):
        home, _ = _tree(tmp_path, {"home/.config/meshforge/fleet_hosts": "g\n"})
        (home / ".config/meshforge/fleet_hosts.meshforge").symlink_to(tmp_path / "gone")
        out, _ = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
        assert out["rc"] == "2"

    @needs_nonroot
    def test_unreadable_override_rc2(self, tmp_path):
        _tree(tmp_path, {"ov": "x\n"})
        _unreadable(tmp_path / "ov")
        out, _ = _run_shell_rc("/opt/meshforge",
                               {"MESHFORGE_FLEET_HOSTS": str(tmp_path / "ov")})
        assert out["rc"] == "2"

    def test_no_list_is_still_rc1_with_no_error(self, tmp_path):
        home, _ = _tree(tmp_path, {})
        out, err = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
        assert out["rc"] == "1" and out["err"] == ""

    def test_missing_override_is_still_rc1(self, tmp_path):
        home, _ = _tree(tmp_path, {"home/.config/meshforge/fleet_hosts": "real\n"})
        out, _ = _run_shell_rc("/opt/meshforge",
                               {"HOME": str(home), "MESHFORGE_FLEET_HOSTS": str(tmp_path / "nope")})
        assert out["rc"] == "1"


# ── r7e #2 review (r7f): the twins agree on an UNCHECKABLE tier ──────────


class TestUncheckableTierParity:
    """r7f F2: a tier under a directory the caller cannot search cannot be
    proven absent. Measured before the fix: python raised, the shell lib read
    it as ABSENT (rc 1) and would fall through — the two twins disagreed on
    the exact r7e #2 class. Both must refuse."""

    @needs_nonroot
    def test_both_refuse_a_tier_under_an_unsearchable_dir(self, tmp_path):
        home, _ = _tree(tmp_path, {
            "home/.config/meshforge/fleet_hosts.meshforge": "per\n",
        })
        locked = home / ".config" / "meshforge"
        locked.chmod(0)
        try:
            out, err = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
            with pytest.raises(FleetHostsUnreadable) as ei:
                resolve_fleet_hosts_file(env={"HOME": str(home)},
                                         etc_dir=str(tmp_path / "no-etc"))
        finally:
            locked.chmod(0o755)
        assert out["rc"] == "2", (out, err)
        assert "not searchable" in out["err"]
        assert "not searchable" in str(ei.value)
        # the message must not claim knowledge it cannot have
        assert "exists" not in str(ei.value) and "exists" not in out["err"]

    @needs_nonroot
    def test_unsearchable_grandparent_also_refuses_in_both(self, tmp_path):
        home, _ = _tree(tmp_path, {})
        (home / ".config").mkdir()
        (home / ".config").chmod(0)
        try:
            out, _ = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
            with pytest.raises(FleetHostsUnreadable):
                resolve_fleet_hosts_file(env={"HOME": str(home)},
                                         etc_dir=str(tmp_path / "no-etc"))
        finally:
            (home / ".config").chmod(0o755)
        assert out["rc"] == "2"

    def test_a_file_where_a_parent_dir_should_be_is_absent_in_both(self, tmp_path):
        """ENOTDIR = the path cannot exist; both twins pass to the next tier."""
        home, _ = _tree(tmp_path, {"home/.config": "i am a file\n"})
        out, _ = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
        assert out["rc"] == "1"
        assert resolve_fleet_hosts_file(env={"HOME": str(home)},
                                        etc_dir=str(tmp_path / "no-etc")) is None


class TestShellReadAfterResolve:
    def test_a_failed_read_after_resolve_is_rc2_not_an_empty_list(self, tmp_path, monkeypatch, capsys):
        """r7f F4, moved with the read (r8): a read that fails AFTER the
        resolve is a refusal (rc 11 from ``--shell`` -> lib rc 2), never
        rc 0 with an empty list."""
        import utils.fleet_hosts as fhm
        f = tmp_path / "fl"
        f.write_text("g\n")
        monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(f))

        def boom(self, *a, **k):
            raise PermissionError(13, "Permission denied")
        monkeypatch.setattr(Path, "read_text", boom)
        rc = fhm._shell_main(["--shell"])
        out = capsys.readouterr().out
        assert rc == fhm.SHELL_RC_UNREADABLE and str(f) in out and "unreadable" in out


# ── r7g (non-author pass on the MA port): symlink reasons agree ───────────


class TestSymlinkReasonParity:
    """r7g F1: a symlink LOOP read "a parent directory is not searchable" in
    python and "dangling symlink" in the shell — the twins promise the same
    reason in the same words, and the operator's next move depends on it."""

    def _both(self, tmp_path, link):
        out, err = _run_shell_rc("/opt/meshforge",
                                 {"MESHFORGE_FLEET_HOSTS": str(link),
                                  "HOME": str(tmp_path)})
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file(env={"MESHFORGE_FLEET_HOSTS": str(link)})
        assert out["rc"] == "2", (out, err)
        return ei.value.reason, out["err"]

    def test_symlink_loop(self, tmp_path):
        link = tmp_path / "loop"
        link.symlink_to(link)
        py, sh = self._both(tmp_path, link)
        assert py == "symlink loop" and "(symlink loop)" in sh, (py, sh)

    def test_dangling(self, tmp_path):
        link = tmp_path / "lnk"
        link.symlink_to(tmp_path / "gone")
        py, sh = self._both(tmp_path, link)
        assert py == "dangling symlink" and "(dangling symlink)" in sh, (py, sh)

    @needs_nonroot
    def test_target_under_an_unsearchable_dir(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "t").write_text("x\n")
        link = tmp_path / "lnk"
        link.symlink_to(locked / "t")
        locked.chmod(0)
        try:
            py, sh = self._both(tmp_path, link)
        finally:
            locked.chmod(0o755)
        want = "symlink target cannot be checked: a parent directory is not searchable"
        assert py == want and "(%s)" % want in sh, (py, sh)


# ── r7h (non-author pass on the r7g fixes) ────────────────────────────────


class TestSymlinkOnThePath:
    """r7h #1/#2: a symlink LOOP in a PARENT component read as ABSENT in the
    shell (rc 1 -> fell through) while python refused with the raw strerror
    (measured, both twins). Dangling parents stay absent in both (ENOENT)."""

    def test_parent_loop_refuses_in_both_with_one_reason(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config").mkdir(parents=True)
        d = home / ".config" / "meshforge"
        d.symlink_to(d)
        out, err = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file(env={"HOME": str(home)},
                                     etc_dir=str(tmp_path / "no-etc"))
        assert out["rc"] == "2", (out, err)
        assert ei.value.reason == "symlink loop" and "(symlink loop)" in out["err"]

    def test_parent_dangling_is_absent_in_both(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config").mkdir(parents=True)
        (home / ".config" / "meshforge").symlink_to(tmp_path / "gone")
        out, err = _run_shell_rc("/opt/meshforge", {"HOME": str(home)})
        assert out["rc"] in ("0", "1") and out["err"] == "", (out, err)
        assert resolve_fleet_hosts_file(env={"HOME": str(home)},
                                        etc_dir=str(tmp_path / "no-etc")) is None

    def test_the_paths_own_text_cannot_steer_the_reason(self, tmp_path):
        """r7h #3: only the strerror after the last ': ' is matched."""
        link = tmp_path / "x Permission denied symbolic links"
        link.symlink_to(tmp_path / "gone")
        out, _ = _run_shell_rc("/opt/meshforge", {"MESHFORGE_FLEET_HOSTS": str(link), "HOME": str(tmp_path)})
        assert out["rc"] == "2" and "(dangling symlink)" in out["err"], out

    def test_an_unknown_errno_passes_through_raw_in_both(self, tmp_path):
        """r7h #5: ENAMETOOLONG is neither dangling nor a loop."""
        link = tmp_path / "lnk"
        link.symlink_to(tmp_path / ("n" * 300))
        out, _ = _run_shell_rc("/opt/meshforge", {"MESHFORGE_FLEET_HOSTS": str(link), "HOME": str(tmp_path)})
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file(env={"MESHFORGE_FLEET_HOSTS": str(link)})
        assert ei.value.reason == "File name too long"
        assert out["rc"] == "2" and "(File name too long)" in out["err"], out

    def test_the_probe_never_opens_the_target(self, tmp_path):
        """r7h #4: a list that is a link to a FIFO must be refused at once —
        a reader would block with no writer. Through the REAL resolve path."""
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        link = tmp_path / "lnk"
        link.symlink_to(fifo)
        out, err = _run_shell_rc("/opt/meshforge", {"MESHFORGE_FLEET_HOSTS": str(link), "HOME": str(tmp_path)})
        assert out["rc"] == "2" and "(not a regular file)" in out["err"], (out, err)

    def test_a_too_long_name_refuses_in_both(self, tmp_path):
        """r7i #1: ENAMETOOLONG on a PLAIN path read ABSENT in the shell
        (the ancestor walk found a searchable parent) while python refused."""
        long = tmp_path / ("n" * 300)
        out, err = _run_shell_rc("/opt/meshforge", {"MESHFORGE_FLEET_HOSTS": str(long), "HOME": str(tmp_path)})
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts_file(env={"MESHFORGE_FLEET_HOSTS": str(long)})
        assert ei.value.reason == "File name too long"
        assert out["rc"] == "2" and "(File name too long)" in out["err"], (out, err)


# ── r8: the shell lib ASKS the python resolver (operator 2026-11-05) ─────


class TestShellAsksPython:
    """After four review rounds (r7f..r7i) each found a new path shape on
    which a shell re-implementation disagreed with python, the shell lib
    became a caller of ``fleet_hosts.py --shell``. These pin the seam."""

    def test_a_resolver_crash_is_a_refusal_never_no_list(self, tmp_path):
        """python's own failure codes (1 traceback, 2 usage) must not read
        as rc 1 "no list" — a resolver that could not run says so."""
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "python3").write_text("#!/bin/sh\nexit 1\n")
        (fake / "python3").chmod(0o755)
        path = "%s:%s" % (fake, os.environ.get("PATH", "/usr/bin:/bin"))
        out, err = _run_shell_rc("/opt/meshforge", {"PATH": path, "HOME": str(tmp_path)})
        assert out["rc"] == "2" and "could not be resolved" in out["err"], (out, err)

    def test_an_unexported_override_still_wins(self, tmp_path):
        """The lib read shell variables; the python child only sees the
        environment — so the lib must pass the overrides explicitly."""
        f = tmp_path / "ov"
        f.write_text("only-me\n")
        script = ('MESHFORGE_FLEET_HOSTS="$1"; export -n MESHFORGE_FLEET_HOSTS; '
                  '. "{lib}"; fleet_hosts_resolve "/opt/meshforge"; echo "rc=$? list=$FLEET_HOSTS_LIST"'
                  ).format(lib=_LIB)
        p = subprocess.run(["bash", "-c", script, "bash", str(f)], capture_output=True,
                           text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                "HOME": str(tmp_path)})
        assert "rc=0 list=only-me" in p.stdout, (p.stdout, p.stderr)

    def test_a_trailing_slash_override_gets_one_answer(self, tmp_path):
        """r7i review #2 (measured): 'file/' — python used the file, the
        shell read it absent. One implementation now; pin the agreement."""
        f = tmp_path / "ov"
        f.write_text("a b\n")
        val = str(f) + "/"
        out, err = _run_shell_rc("/opt/meshforge", {"MESHFORGE_FLEET_HOSTS": val, "HOME": str(tmp_path)})
        py = resolve_fleet_hosts_file(env={"MESHFORGE_FLEET_HOSTS": val})
        assert out["rc"] == "0" and out["file"] == str(py), (out, err, py)

    def test_the_list_is_parsed_once_crlf_and_comments(self, tmp_path):
        f = tmp_path / "ov"
        f.write_bytes(b"moc1\r\nmoc2  # retired\r\n#x\n")
        p = subprocess.run(["bash", "-c", '. "$1"; fleet_hosts_resolve "/opt/meshforge"; printf "%s|" $FLEET_HOSTS_LIST',
                            "bash", str(_LIB)], capture_output=True, text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                "MESHFORGE_FLEET_HOSTS": str(f)})
        assert p.stdout == "moc1|moc2|", (p.stdout, p.stderr)


# ── r8 review (one pass, stop rule pre-set): real-box wrong decisions ─────


def _sh_full(env, lib=None):
    script = ('. "$1"; fleet_hosts_resolve "/opt/meshforge"; rc=$?; '
              'printf "rc=%s\\nfile=%s\\nerr=%s\\nwhy=%s\\n" "$rc" "$FLEET_HOSTS_FILE" '
              '"$FLEET_HOSTS_ERROR" "$FLEET_HOSTS_WHY"')
    full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    full.update(env)
    p = subprocess.run(["bash", "-c", script, "bash", str(lib or _LIB)],
                       capture_output=True, text=True, timeout=30, env=full,
                       errors="surrogateescape")
    return dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln), p.stderr


class TestShellSeamReview:
    def test_a_non_utf8_path_is_used_not_refused(self, tmp_path):
        """r8 #1 (reviewer measured): str(path) with surrogates crashed the
        locale encoder under en_US.UTF-8 -> rc 2 for a READABLE list."""
        d = os.fsencode(str(tmp_path)) + b"/l\xff"
        with open(d, "wb") as fh:
            fh.write(b"a b\n")
        val = os.fsdecode(d)
        out, err = _sh_full({"MESHFORGE_FLEET_HOSTS": val, "LC_ALL": "en_US.UTF-8",
                             "HOME": str(tmp_path)})
        assert out["rc"] == "0" and out["file"] == val, (out, err)

    def test_a_lib_without_its_python_refuses_by_name(self, tmp_path):
        """r8 #4: never build the resolver path from a failed cd."""
        lib = tmp_path / "scripts" / "lib" / "fleet_hosts.sh"
        lib.parent.mkdir(parents=True)
        lib.write_text(_LIB.read_text())
        out, err = _sh_full({"HOME": str(tmp_path)}, lib=lib)
        assert out["rc"] == "2" and "not found beside this lib" in out["err"], (out, err)

    def test_a_hung_resolver_ends_as_a_refusal(self, tmp_path):
        """r8 #3: a hung interpreter must not hang every deploy script."""
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "python3").write_text("#!/bin/sh\nexec sleep 20\n")
        (fake / "python3").chmod(0o755)
        import time
        t0 = time.monotonic()
        out, err = _sh_full({"PATH": "%s:%s" % (fake, os.environ.get("PATH", "")),
                             "FLEET_HOSTS_RESOLVE_TIMEOUT": "1", "HOME": str(tmp_path)})
        assert time.monotonic() - t0 < 10
        assert out["rc"] == "2" and "exited 124" in out["err"], (out, err)

    def test_no_list_says_what_was_checked_from_the_resolver(self, tmp_path):
        """r8 #2: the WHY comes from python's own tier list — with HOME
        unset it must not name a per-user file it never reads."""
        out, _ = _sh_full({"MESHFORGE_FLEET_HOSTS": str(tmp_path / "nope")})
        assert out["rc"] == "1" and "MESHFORGE_FLEET_HOSTS=" in out["why"] \
            and "does not exist" in out["why"], out
        out, _ = _sh_full({})
        if out["rc"] == "1":            # the box's real /etc tier may answer
            assert "HOME is unset" in out["why"] and ".config" not in out["why"], out

    def test_a_silent_rc0_is_a_refusal_not_an_empty_list(self, tmp_path):
        """r8 suite catch: a shadowed `timeout` stub exited 0 without running
        python; the lib returned rc 0 with an EMPTY list ("lists nobody")."""
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "timeout").write_text("#!/bin/sh\nexit 0\n")
        (fake / "timeout").chmod(0o755)
        out, err = _sh_full({"PATH": "%s:%s" % (fake, os.environ.get("PATH", "")),
                             "HOME": str(tmp_path)})
        assert out["rc"] == "2" and "rc 0 with no file" in out["err"], (out, err)



# ── r9 (non-author pass on the r8 fixes) ───────────────────────────────────


class TestCorruptListRefused:
    """r9 #1 (measured): a power-loss NUL fill read as ONE garbage host in
    python and as an EMPTY list (rc 0) in the shell; 'mo\\0\\0c2' read as
    'moc2' in the shell. A host name is ASCII — refuse, in both, the same way."""

    @pytest.mark.parametrize("data", [b"\0\0\0\0\n", b"mo\0\0c2\nmoc3\n",
                                      b"moc\xff\n", b"moc\x01x\n"])
    def test_refused_in_both(self, tmp_path, data):
        f = tmp_path / "fl"
        f.write_bytes(data)
        env = {"MESHFORGE_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}
        out, err = _sh_full(env)
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts(env=env)
        assert "not a host name" in ei.value.reason
        assert out["rc"] == "2" and "not a host name" in out["err"], (out, err)

    def test_bytes_inside_a_comment_stay_harmless(self, tmp_path):
        f = tmp_path / "fl"
        f.write_bytes(b"moc  # caf\xe9 \0\nmoc1\n")
        env = {"MESHFORGE_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}
        assert resolve_fleet_hosts(env=env) == ["moc", "moc1"]
        out, err = _sh_full(env)
        assert out["rc"] == "0", (out, err)

    def test_a_newline_in_the_path_is_refused_by_the_python_api_too(self, tmp_path):
        """r9 #7: the shell refused it; the python API returned the file."""
        d = tmp_path / "a\nb"
        d.write_text("moc\n")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts_file(env={"MESHFORGE_FLEET_HOSTS": str(d)})

    def test_cdpath_cannot_move_the_lib_off_its_python(self, tmp_path):
        """r9 #6: with CDPATH set, a relative source made `cd` print a path
        (two lines in the capture) or land in ANOTHER repo's src/utils."""
        f = tmp_path / "fl"
        f.write_text("moc\n")
        # CDPATH only bites when it holds the SAME relative path (measured
        # red on the r8 lib only with this tree present: rc 2).
        (tmp_path / "scripts" / "lib").mkdir(parents=True)
        (tmp_path / "src" / "utils").mkdir(parents=True)
        repo = _LIB.parent.parent.parent
        p = subprocess.run(["bash", "-c", '. scripts/lib/fleet_hosts.sh; fleet_hosts_resolve "/opt/meshforge"; echo "rc=$?"'],
                           cwd=str(repo), capture_output=True, text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", ""), "CDPATH": str(tmp_path),
                                "MESHFORGE_FLEET_HOSTS": str(f)})
        assert "rc=0" in p.stdout, (p.stdout, p.stderr)



# ── r11: ONE reader (operator 2026-11-05, after r10 + Fable found private
#    readers turning a refusal into "no fleet") ─────────────────────────────


class TestOneReader:
    """Every fleet-host-list read goes through utils.fleet_hosts. A module
    that parses the list itself can swallow the refusal (`except OSError:
    return []`) — r10 found five, the guard below found a sixth."""

    # Files that split '#' comments for a DIFFERENT file — not the fleet list.
    ALLOW = {
        "src/utils/fleet_hosts.py": "the reader itself",
        "src/mini_dudeai/dreams.py": "parses /etc/hosts lines, not the list",
        "src/utils/watchdog_probes_peer_cron.py": "parses truth_spool_targets",
        "scripts/gen_fleet_hosts.py": "parses the generated /etc/hosts block",
        "scripts/dep_advisory_check.py": "parses the advisory ACCEPT list (read_accepted)",
        "scripts/lint.py": "parses source comments, not the list",
    }

    def _py_files(self):
        root = _LIB.parent.parent.parent
        for top in ("src", "scripts"):
            for p in sorted((root / top).rglob("*.py")):
                yield p.relative_to(root).as_posix(), p.read_text(errors="replace")

    def test_nobody_else_names_the_parser(self):
        bad = [rel for rel, text in self._py_files()
               if rel != "src/utils/fleet_hosts.py" and "parse_fleet_hosts_text" in text]
        assert not bad, "parse the list via read_fleet_hosts_file, not privately: %s" % bad

    def test_no_private_comment_splitting_reader_of_the_list(self):
        bad = [rel for rel, text in self._py_files()
               if _py_private_parser(text) and rel not in self.ALLOW]
        assert not bad, ("a private fleet-list parser — read it with "
                         "utils.fleet_hosts.read_fleet_hosts_file: %s" % bad)


class TestReaderEdges:
    def test_a_bom_is_not_a_corrupt_list(self, tmp_path):
        """r10 (VERIFIED by both reviewers): an editor's UTF-8 BOM refused
        the whole fleet."""
        f = tmp_path / "fl"
        f.write_bytes(b"\xef\xbb\xbfmoc1\nmoc2\n")
        env = {"MESHFORGE_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}
        assert resolve_fleet_hosts(env=env) == ["moc1", "moc2"]
        out, err = _sh_full(env)
        assert out["rc"] == "0", (out, err)

    def test_absent_is_file_not_found_not_a_refusal(self, tmp_path):
        from utils.fleet_hosts import read_fleet_hosts_file
        with pytest.raises(FileNotFoundError):
            read_fleet_hosts_file(tmp_path / "nope")

    def test_the_lib_sourced_through_a_symlink_finds_its_python(self, tmp_path):
        """Fable r10 #7 (VERIFIED): BASH_SOURCE was the link's path."""
        link = tmp_path / "fh.sh"
        link.symlink_to(_LIB)
        f = tmp_path / "fl"
        f.write_text("moc\n")
        out, err = _sh_full({"MESHFORGE_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}, lib=link)
        assert out["rc"] == "0", (out, err)



# ── r13 (Fable r12) ────────────────────────────────────────────────────────


class TestR13ReaderEdges:
    def test_a_dangling_symlink_is_refused_by_the_reader_too(self, tmp_path):
        """Fable r12 #4 (VERIFIED): the reader called it ABSENT while
        _usable refused it — the TUI then read STANDALONE."""
        from utils.fleet_hosts import read_fleet_hosts_file
        link = tmp_path / "fl"
        link.symlink_to(tmp_path / "gone")
        with pytest.raises(FleetHostsUnreadable) as ei:
            read_fleet_hosts_file(link)
        assert ei.value.reason == "dangling symlink"
        with pytest.raises(FileNotFoundError):
            read_fleet_hosts_file(tmp_path / "absent")
        with pytest.raises(FileNotFoundError):     # ENOTDIR = absent, as _usable
            (tmp_path / "afile").write_text("x\n")
            read_fleet_hosts_file(tmp_path / "afile" / "fleet_hosts")

    @pytest.mark.parametrize("sep", ["\x0b", "\x0c", "\x1f", "\u0085", "\u2028", "\u00a0"])
    def test_an_odd_separator_is_refused_not_split(self, tmp_path, sep):
        """Fable r12 #7 (VERIFIED): 'moc\\x1f1' became ['moc', '1']."""
        f = tmp_path / "fl"
        f.write_text("a.invalid%sb.invalid\n" % sep, encoding="utf-8")
        with pytest.raises(FleetHostsUnreadable):
            resolve_fleet_hosts(env={"MESHFORGE_FLEET_HOSTS": str(f)})

    def test_space_tab_crlf_still_split(self, tmp_path):
        f = tmp_path / "fl"
        f.write_bytes(b"a.invalid b.invalid\tc.invalid\r\nd.invalid\n")
        assert resolve_fleet_hosts(env={"MESHFORGE_FLEET_HOSTS": str(f)}) == [
            "a.invalid", "b.invalid", "c.invalid", "d.invalid"]


class TestOneReaderShell:
    """r13: the one-reader guard covers SHELL too — Fable r12 found three
    `done < "$HOSTS_FILE"` readers the *.py guard could not see."""

    def test_no_shell_script_reads_a_hosts_file_itself(self):
        root = _LIB.parent.parent.parent
        bad = []
        for p in sorted((root / "scripts").rglob("*.sh")):
            if p == _LIB:
                continue
            bad += ["%s:%d" % (p.relative_to(root), n)
                    for n in _sh_private_reader_lines(p.read_text(errors="replace"))]
        assert not bad, "read the list via scripts/lib/fleet_hosts.sh: %s" % bad



# ── r15: the one-reader guards, widened and PROVEN to fail (Fable r14 O1) ──
# A guard that never failed is not evidence: TestGuardsAreFalsifiable plants
# every copy shape Fable showed slipping through and requires each flagged.

_SH_TOOLS = (r'\b(cat|sed|awk|grep|egrep|head|tail|xargs|mapfile|readarray|cut|tr|sort|uniq|wc|'
             r'while|python3?|perl)\b')
_SH_TOOL_RX = re.compile(_SH_TOOLS)
_SH_REDIR_RX = re.compile(r'(?<![<\d])\d?<\s*(?!\()(?P<t>\S+)')
_SH_LIB_OUT = re.compile(r'\$\{?FLEET_HOSTS_(LIST|ERROR|WHY)\b\}?')
_SH_LITERAL = re.compile(r'fleet_hosts\b(?!\.sh|\.example)', re.I)
_SH_HOSTVAR = re.compile(r'\$\{?(\w*(hosts|fleet_file)\w*)', re.I)          # redirect target: any
_SH_FILEVAR = re.compile(r'\$\{?\w*(hosts(_?(file|path))?|fleet_file)(?!\w)', re.I)  # tool argument
_SH_SEG_LEAD = re.compile(r'^[\s(]*(?:\w+=)?["\']?(?:\$\()?\s*(\S+)')
_SH_ASSIGN = re.compile(r'^\s*(?:local\s+|export\s+)?(\w+)=(.*)$')
_PY_SPLIT = re.compile(
    r"""(split|partition|rpartition|startswith|find|index)\(\s*\(?\s*['"]#['"]"""
    r"""|re\.sub\(\s*r?['"]#|==\s*['"]#['"]|comments\s*=\s*True|split\(\s*COMMENT\b""")


def _sh_private_reader_lines(text):
    """Line numbers of shell code that reads a fleet hosts FILE itself
    (r15, widened r17 after Fable r16 planted 16 realistic shapes). The lib's
    OUTPUT ($FLEET_HOSTS_LIST/ERROR/WHY) and here-strings are fine. KNOWN
    LIMIT: a generically named variable never assigned from a fleet_hosts
    path in this file (`done < "$LIST"`) is invisible to a line scanner."""
    lines = text.splitlines()
    suspect = set()
    for line in lines:
        m = _SH_ASSIGN.match(line)
        if m and _SH_LITERAL.search(m.group(2)):
            suspect.add(m.group(1))
        for seg in line.split(";"):
            m = _SH_ASSIGN.match(seg)
            if m and _SH_LITERAL.search(m.group(2)):
                suspect.add(m.group(1))
    sus_rx = re.compile(r'\$\{?(%s)\b' % "|".join(map(re.escape, sorted(suspect)))) if suspect else None

    def mentions_list(s, file_only=False):
        rx = _SH_FILEVAR if file_only else _SH_HOSTVAR
        return bool(_SH_LITERAL.search(s) or rx.search(s)
                    or (sus_rx and sus_rx.search(s)))

    def tool_reads_list(code):
        # per pipeline segment; a segment led by echo/printf only PRINTS
        # host CONTENTS ($BOXES, "$HOSTS") — that is not reading the file.
        for seg in re.split(r'(?<!\|)\|(?!\|)', code):
            m = _SH_SEG_LEAD.match(seg)
            lead = m.group(1) if m else ""
            if lead in ("echo", "printf"):
                continue
            if _SH_TOOL_RX.search(seg) and mentions_list(seg, file_only=True):
                return True
        return False

    out = []
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            continue
        code = _SH_LIB_OUT.sub("", line.replace("<<<", "\x00"))
        code = re.sub(r'(echo|printf|say|die|warn|log)\s+"[^"]*"', "", code)
        hit = any(mentions_list(m.group("t")) for m in _SH_REDIR_RX.finditer(code))
        if not hit and tool_reads_list(code):
            hit = True
        if hit:
            out.append(n)
    return out


def _py_private_parser(text):
    return bool(re.search("fleet_hosts", text, re.I)) and bool(_PY_SPLIT.search(text))


class TestGuardsAreFalsifiable:
    @pytest.mark.parametrize("snippet", [
        'done < "$HOSTS_FILE"',
        'done 3< "$HOSTS_FILE"',
        'while read -r h; do :; done < "$hosts_file"',
        'done < ${FLEET_FILE_HOSTS}',
        'cat "$HOSTS_FILE" | while read -r h; do :; done',
        'grep -v "^#" "$HOSTS_FILE"',
        "awk '{print $1}' \"$MY_HOSTS\"",
        'xargs -a "$HOSTS_FILE" -n1 echo',
        'mapfile -t hosts < "$FLEET_HOSTS_FILE"',
        'sed "s/#.*//" "$FLEET_HOSTS_FILE"',
        'hosts=$(cat "$fleet_file")',
        'done < ~/.config/meshforge/fleet_hosts',
        'done < "$HOME/.config/meshforge/fleet_hosts"',
        'F=~/.config/meshforge/fleet_hosts; done < "$F"',
        'done < "${CFG_DIR}/fleet_hosts"',
        "for h in $(grep -v '^#' ~/.config/meshforge/fleet_hosts); do :; done",
        'FLEET=~/.config/meshforge/fleet_hosts; cat "$FLEET"',
        'cat "${MESHFORGE_FLEET_HOSTS:-$HOME/.config/meshforge/fleet_hosts}"',
        'python3 - "$HOSTS_FILE" <<EOF',
        'ssh "$h" cat .config/meshforge/fleet_hosts',
        'HOSTS=$(sed -e "s/#.*//" -- "$FLEET_HOSTS_FILE")',
        'mapfile -t hosts <"$HOSTS_FILE"',
        'done < $HOSTS_FILE',
        'done <"${hosts_file}"',
        'while read -r h; do :; done <"$FLEET_HOSTS_FILE" ; cat x',
        'readarray -t H < <(grep -v "^#" "$FLEET_HOSTS_FILE")',
    ])
    def test_shell_guard_flags_every_copy_shape(self, snippet):
        assert _sh_private_reader_lines(snippet), snippet

    def test_the_known_limit_is_written_down(self):
        """`done < "$LIST"` with no fleet_hosts assignment in sight is the
        documented blind spot of a line scanner — pinned so a future widening
        that closes it updates the docstring too."""
        assert _sh_private_reader_lines('done < "$LIST"') == []
        assert "KNOWN" in (_sh_private_reader_lines.__doc__ or "")

    @pytest.mark.parametrize("snippet", [
        'done <<< "$FLEET_HOSTS_LIST"',
        'done 3< <(printf "%s\\n" "$FLEET_HOSTS_LIST")',
        'echo "fleet_hosts: $FLEET_HOSTS_ERROR" >&2',
        'BOXES="$(printf \'%s\\n\' $BOXES | awk \'NF\' | tr \'\\n\' \' \')"',
        'echo "probing $(echo "$HOSTS" | wc -w) hosts" >&2',
        'python3 - "$SORT_BY" "$hosts_seen" "$hosts_with_data"',
        'echo "no list - create ~/.config/meshforge/fleet_hosts" >&2',
        '    cp contrib/fleet/fleet_hosts.example ~/.config/meshforge/fleet_hosts',
        '. "$HERE/lib/fleet_hosts.sh"',
        '# done < "$HOSTS_FILE"  (a comment)',
    ])
    def test_shell_guard_passes_the_lib_output(self, snippet):
        assert _sh_private_reader_lines(snippet) == [], snippet

    @pytest.mark.parametrize("snippet", [
        "x = line.split('#', 1)[0]  # fleet_hosts",
        'h, _, _ = line.partition("#")  # fleet_hosts',
        "if s.startswith('#'): pass  # fleet_hosts",
        're.sub(r"#.*", "", line).split()  # fleet_hosts',
        'line[:line.find("#")].split()  # fleet_hosts',
        'p = os.environ.get("MESHFORGE_FLEET_HOSTS")\nh = [t for t in ln.split("#")[0].split()]',
        'shlex.split(line, comments=True)  # fleet_hosts',
        'line.strip().startswith(("#", ";"))  # fleet_hosts',
        'COMMENT = "#"\nline.split(COMMENT)[0]  # fleet_hosts',
        'tok[0] == "#"  # fleet_hosts',
        'line.split("#", maxsplit=1)[0]  # fleet_hosts',
        'line.split( "#" )[0]  # fleet_hosts',
        "line.split('#')[0]  # FLEET_HOSTS only uppercase",
    ])
    def test_py_guard_flags_every_copy_shape(self, snippet):
        assert _py_private_parser(snippet), snippet



# ── r17 (Fable r16): an EXISTING empty list is refused at the seam ─────────


class TestEmptyListRefused:
    @pytest.mark.parametrize("content", [b"", b"# nobody yet\n", b"\n  \n"])
    def test_python_and_shell_refuse_an_empty_list(self, tmp_path, content):
        f = tmp_path / "fl"
        f.write_bytes(content)
        env = {"MESHFORGE_FLEET_HOSTS": str(f), "HOME": str(tmp_path)}
        with pytest.raises(FleetHostsUnreadable) as ei:
            resolve_fleet_hosts(env=env)
        assert "lists no hosts" in ei.value.reason
        out, err = _sh_full(env)
        assert out["rc"] == "2" and "lists no hosts" in out["err"], (out, err)

    def test_the_reader_refuses_empty_by_default_and_shows_it_on_request(self, tmp_path):
        """r19 (Fable r18): consumers that resolve-then-READ bypassed the
        resolver's check, so the READER refuses by default; only the TUI
        wizard, which must SHOW the file, opts in with allow_empty."""
        from utils.fleet_hosts import read_fleet_hosts_file
        f = tmp_path / "fl"
        f.write_text("# nobody\n")
        with pytest.raises(FleetHostsUnreadable) as ei:
            read_fleet_hosts_file(f)
        assert "lists no hosts" in ei.value.reason
        assert read_fleet_hosts_file(f, allow_empty=True) == []

    def test_a_chatty_wrapper_line_never_becomes_the_file(self, tmp_path):
        """Fable r16 O3 (VERIFIED): a python3 wrapper printing a line first
        made that line FLEET_HOSTS_FILE and the real path a HOST."""
        fake = tmp_path / "bin"
        fake.mkdir()
        real = subprocess.run(["sh", "-c", "command -v python3"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
        (fake / "python3").write_text('#!/bin/sh\necho "pyenv: warning foo"\nexec %s "$@"\n' % real)
        (fake / "python3").chmod(0o755)
        f = tmp_path / "fl"
        f.write_text("a.invalid\n")
        out, err = _sh_full({"PATH": "%s:%s" % (fake, os.environ.get("PATH", "")),
                             "MESHFORGE_FLEET_HOSTS": str(f), "HOME": str(tmp_path)})
        assert out["rc"] == "2" and out["file"] == "", (out, err)



def test_the_reader_never_opens_a_fifo(tmp_path):
    """Fable standalone-arc #5 (VERIFIED): opening a FIFO blocked forever —
    it hung the whole TUI Fleet Membership menu. Run in a subprocess so the
    OLD code fails by timeout instead of hanging the suite."""
    fifo = tmp_path / "fl"
    os.mkfifo(fifo)
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys; sys.path.insert(0, %r)\n"
        "from utils.fleet_hosts import read_fleet_hosts_file, FleetHostsUnreadable\n"
        "try:\n    read_fleet_hosts_file(%r)\nexcept FleetHostsUnreadable as e:\n    print('REFUSED', e.reason)\n"
        % (str(_LIB.parent.parent.parent / "src"), str(fifo)))
    try:
        p = subprocess.run([sys.executable, "-I", str(probe)], capture_output=True,
                           text=True, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail("the reader blocked on a FIFO")
    assert "REFUSED not a regular file" in p.stdout, (p.stdout, p.stderr)
