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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_hosts import (  # noqa: E402
    parse_fleet_hosts_text,
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

    def test_dup_collector_parser_delegates(self):
        from utils.fleet_dup_collector import parse_fleet_hosts
        assert parse_fleet_hosts("moc  # x\nmoc3\n") == ["moc", "moc3"]
