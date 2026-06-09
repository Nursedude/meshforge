"""Regression guard: the mini-dudeai observation-only invariant (MF021 twin).

mini-dudeai is a deterministic, dependency-free stdlib rule-loop agent. Its
governing doctrine is that the engine and ALL built-in sources/actions
*observe* the system (read files, parse /proc, http GET) but NEVER *execute*
it — no subprocess, no systemctl, no os.system/popen/exec, no Popen, no
shell=True. That used to be true only by grep; this file pins it as a
codebase-scanning regression test, mirroring tests/test_regression_guards.py.

Pinned two ways (defense in depth): the lint rule MF021 in scripts/lint.py and
this test scan the same fixed file set independently.

Out of scope (NOT engine/sources/actions, intentionally allowed to execute):
  * rollup.py  — cloud-session fleet ssh-fan tool
  * dreams.py  — cloud-session synthesis tool

Usage:
    python3 -m pytest tests/test_mini_dudeai_observation_only.py -v
"""

from __future__ import annotations

import os
import re

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), '..')
MINI_DIR = os.path.join(REPO_ROOT, 'src', 'mini_dudeai')

# The forbidden execution surfaces — word-boundary matched on CODE lines only.
FORBIDDEN = (
    (re.compile(r'\bsubprocess\b'), 'subprocess'),
    (re.compile(r'\bsystemctl\b'), 'systemctl'),
    (re.compile(r'\bos\.system\b'), 'os.system'),
    (re.compile(r'\bos\.popen\b'), 'os.popen'),
    (re.compile(r'\bos\.exec\w*\b'), 'os.exec*'),
    (re.compile(r'\bPopen\b'), 'Popen'),
    (re.compile(r'\bshell\s*=\s*True\b'), 'shell=True'),
)


def _observation_only_files():
    """Return the absolute paths of every engine/sources/actions .py file."""
    files = [os.path.join(MINI_DIR, 'engine.py')]
    for subdir in ('sources', 'actions'):
        d = os.path.join(MINI_DIR, subdir)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if fname.endswith('.py'):
                files.append(os.path.join(d, fname))
    return [f for f in files if os.path.isfile(f)]


def _code_lines(content):
    """Yield (lineno, code) for lines outside comments and docstrings.

    Tracks triple-quoted docstring state so a token mentioned in a docstring
    (e.g. boot_health.py's "observation-only (no subprocess)") is not flagged.
    """
    in_doc = False
    delim = ''
    for lineno, raw in enumerate(content.splitlines(), 1):
        stripped = raw.strip()
        if in_doc:
            if delim in stripped:
                in_doc = False
            continue
        opened = False
        for d in ('"""', "'''"):
            if (stripped.startswith(d) or stripped.startswith('r' + d)
                    or stripped.startswith('f' + d)):
                body = stripped.split(d, 1)[1]
                if d not in body:
                    in_doc = True
                    delim = d
                opened = True
                break
        if opened:
            continue
        if stripped.startswith('#'):
            continue
        yield lineno, raw.split('#', 1)[0]


def _scan(filepath):
    """Return list of (lineno, token, code) forbidden-token hits in code."""
    hits = []
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    for lineno, code in _code_lines(content):
        for pattern, token in FORBIDDEN:
            if pattern.search(code):
                hits.append((lineno, token, code.strip()))
    return hits


class TestObservationOnlyInvariant:
    """Engine + sources + actions must contain no execution surfaces in code."""

    def test_files_under_scan_exist(self):
        """Guard the guard: the scan set must be non-empty (paths didn't move)."""
        files = _observation_only_files()
        assert files, "no mini_dudeai engine/sources/actions files found to scan"
        # engine.py is the anchor — it must be present.
        assert any(f.endswith('engine.py') for f in files)

    def test_no_execution_surface_in_engine_sources_actions(self):
        """The load-bearing invariant: zero forbidden tokens in code lines."""
        violations = []
        for filepath in _observation_only_files():
            for lineno, token, code in _scan(filepath):
                rel = os.path.relpath(filepath, REPO_ROOT)
                violations.append(f"{rel}:{lineno}: '{token}' -> {code}")
        assert not violations, (
            "mini-dudeai observation-only invariant broken — engine/sources/"
            "actions must observe, never execute:\n" + "\n".join(violations)
        )

    def test_boot_health_docstring_mention_is_allowed(self):
        """The legitimate docstring mention must NOT count as a violation."""
        boot = os.path.join(MINI_DIR, 'sources', 'boot_health.py')
        assert os.path.isfile(boot)
        # The substring exists somewhere in the file...
        assert 'no subprocess' in open(boot, encoding='utf-8').read()
        # ...but no code line is flagged.
        assert _scan(boot) == []

    @pytest.mark.parametrize("subdir", ["sources", "actions"])
    def test_each_action_and_source_clean(self, subdir):
        """Per-file assertion so a failure points at the exact module."""
        d = os.path.join(MINI_DIR, subdir)
        for fname in sorted(os.listdir(d)):
            if not fname.endswith('.py'):
                continue
            hits = _scan(os.path.join(d, fname))
            assert hits == [], f"{subdir}/{fname} has execution surfaces: {hits}"


class TestMF021LintRulePinsSameInvariant:
    """The lint rule and this test must agree (two-way pin)."""

    def _load_lint(self):
        import importlib.util
        lint_path = os.path.join(REPO_ROOT, 'scripts', 'lint.py')
        spec = importlib.util.spec_from_file_location("_mf_lint", lint_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_lint_rule_reports_clean_tree(self):
        lint = self._load_lint()
        issues = lint.check_mini_dudeai_observation_only(REPO_ROOT)
        codes = [(i.file, i.line, i.message) for i in issues]
        assert issues == [] or not codes, (
            f"MF021 flags the live tree (unexpected): {codes}"
        )

    def test_lint_and_test_agree(self):
        """MF021 lint hits and this test's scan agree on the same file set."""
        lint = self._load_lint()
        lint_hits = {
            (os.path.basename(i.file), i.line)
            for i in lint.check_mini_dudeai_observation_only(REPO_ROOT)
        }
        test_hits = set()
        for filepath in _observation_only_files():
            for lineno, _token, _code in _scan(filepath):
                test_hits.add((os.path.basename(filepath), lineno))
        assert lint_hits == test_hits
