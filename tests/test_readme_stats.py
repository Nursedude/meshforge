"""README structural-count drift guard (2026-07-23 doc audit).

The README hardcodes filesystem-derived counts (test files, handler modules)
inside `<!--STAT:*-->` sentinels. `scripts/readme_stats.py` is their SSOT.
This test runs its `--check` so the CI run the repo already performs fails the
moment a sentinel drifts from the tree — the same "regenerate, never trust a
carried-forward number" discipline the code linter enforces, applied to docs.

These counts are pure filesystem globs, so they are identical in CI and on a
dev box (unlike the total test count, which depends on installed optional deps
and is therefore stated qualitatively in the README, not gated).
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "readme_stats.py"


def _load():
    spec = importlib.util.spec_from_file_location("readme_stats", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_readme_stats_script_present():
    assert SCRIPT.is_file(), "scripts/readme_stats.py is the README-count SSOT and must exist"


def test_readme_stat_sentinels_match_tree():
    mod = _load()
    rc = mod.check()
    if rc == 2:
        pytest.fail(
            "readme_stats --check could not compute a sentinel value (rc=2, UNKNOWN). "
            "Unobservable is not a pass — see output above."
        )
    assert rc == 0, (
        "README structural counts drifted from the tree. "
        "Run `python3 scripts/readme_stats.py --update` and commit the README change."
    )


class TestWiderSentinelScope:
    """The checker must follow its subject.

    2026-08-31: the-lab.md said 22 research docs, .claude/research/README.md
    said 26, and CLAUDE.md said 22 — against 53 real files. Three files citing
    one quantity, three different wrong answers, none of them checkable, and
    the worst offender is @-included into every conversation turn.
    """

    def test_claude_md_is_in_scope(self):
        mod = _load()
        assert "CLAUDE.md" in {f.name for f in mod._doc_files()}

    def test_research_readme_is_in_scope(self):
        mod = _load()
        paths = {str(f) for f in mod._doc_files()}
        assert any(p.endswith(".claude/research/README.md") for p in paths)

    def test_new_counter_is_registered(self):
        mod = _load()
        assert "researchdocs" in mod.COMPUTERS

    def test_counter_returns_a_positive_int_on_this_tree(self):
        mod = _load()
        val = mod.COMPUTERS["researchdocs"]()
        assert isinstance(val, int) and val > 0

    def test_research_count_excludes_its_own_readme(self):
        mod = _load()
        d = mod.ROOT / ".claude" / "research"
        assert mod.COMPUTERS["researchdocs"]() == len(list(d.glob("*.md"))) - 1

    def test_no_claudedocs_counter(self):
        """`.claude/` mixes tracked docs with untracked local scratch, so its
        count differs between a working box and a clone — 189 vs 188 in CI on
        2026-08-31. It has no environment-stable count and must not be
        sentinelled again."""
        mod = _load()
        assert "claudedocs" not in mod.COMPUTERS

    def test_every_sentinelled_count_is_stable_under_git(self):
        """A sentinel may only cite a quantity a fresh clone reproduces.
        This is the test that would have caught the claudedocs mistake."""
        import subprocess
        mod = _load()
        globs = {"researchdocs": ".claude/research/*.md",
                 "testfiles": "tests/test_*.py",
                 "handlers": "src/launcher_tui/handlers/*.py"}
        # Closed-enum gate (honest_failure_modes #7): a NEW computer must fail
        # this test until someone declares what git glob reproduces it. Without
        # this line the test skips unknown keys and a future unstable counter
        # slips through exactly as claudedocs did.
        assert set(mod.COMPUTERS) <= set(globs), (
            "new sentinel computer(s) %s have no declared git-stable basis"
            % sorted(set(mod.COMPUTERS) - set(globs)))
        for key, pattern in globs.items():
            if key not in mod.COMPUTERS:
                continue
            out = subprocess.run(["git", "ls-files", pattern], cwd=mod.ROOT,
                                 capture_output=True, text=True, timeout=30)
            tracked = [ln for ln in out.stdout.splitlines() if ln.strip()]
            if key == "researchdocs":
                tracked = [f for f in tracked if not f.endswith("README.md")]
            if key == "handlers":
                tracked = [f for f in tracked
                           if not f.endswith("__init__.py")]
            assert mod.COMPUTERS[key]() == len(tracked), (
                "%s counts files git does not track — it will differ in CI"
                % key)

    def test_only_the_root_readme_is_unconditional(self):
        """Every other file opts in by carrying a sentinel, so adding a doc
        cannot drag an unrelated file into --check."""
        mod = _load()
        for f in mod._doc_files():
            if f == mod.README:
                continue
            assert mod.SENTINEL_RE.search(
                f.read_text(encoding="utf-8", errors="ignore")), f

    def test_missing_optional_files_do_not_break_scope(self, tmp_path,
                                                       monkeypatch):
        """A repo with no CLAUDE.md / .claude must still check cleanly —
        MeshAnchor shares this script and has a different tree."""
        monkeypatch.setattr(_load(), "ROOT", tmp_path, raising=False)
        mod = _load()
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        monkeypatch.setattr(mod, "README", tmp_path / "README.md")
        assert mod._doc_files() == []
        assert mod.COMPUTERS["researchdocs"]() is None
