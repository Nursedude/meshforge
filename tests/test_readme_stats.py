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
