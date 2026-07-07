"""Tests for scripts/version_consistency_check.py — the MF024 anti-drift guard.

Born with the guard (2026-07-07). These pin the parse edges that are prone to the
honest-failure-mode class: a badge dialect the regex doesn't match reads as
"no badge" (blind spot, not mismatch), and a pyproject `version` outside
`[project]` must not be mistaken for the project version.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import version_consistency_check as vcc  # noqa: E402


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TestBadgeDialects(unittest.TestCase):
    """The three shields.io badge shapes the fleet actually uses must all parse."""

    def _badge(self, badge_line):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "README.md", f"# X\n{badge_line}\n")
            return vcc.read_readme_badge_version(d)

    def test_svg_suffix_with_escaped_dash(self):
        self.assertEqual(
            self._badge("![V](https://img.shields.io/badge/version-0.6.2--beta-blue.svg)"),
            "0.6.2-beta",
        )

    def test_no_svg_suffix(self):
        # The maps dialect — the one that slipped past the .svg-anchored regex.
        self.assertEqual(
            self._badge("![V](https://img.shields.io/badge/version-0.7.0--beta-blue)"),
            "0.7.0-beta",
        )

    def test_no_prerelease_suffix(self):
        self.assertEqual(
            self._badge("[![V](https://img.shields.io/badge/version-0.6.0-blue.svg)](x)"),
            "0.6.0",
        )

    def test_absent_badge_is_none_not_empty(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "README.md", "# X\nno badge here\n")
            self.assertIsNone(vcc.read_readme_badge_version(d))


class TestPyprojectScoping(unittest.TestCase):
    def test_project_version_read(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "pyproject.toml", '[project]\nname = "x"\nversion = "1.2.3"\n')
            self.assertEqual(vcc.read_pyproject_version(d), "1.2.3")

    def test_tool_version_is_not_project_version(self):
        # tool.black.target-version etc. must never be mistaken for the version.
        with tempfile.TemporaryDirectory() as d:
            _write(d, "pyproject.toml",
                   '[tool.black]\ntarget-version = ["py39"]\nversion = "9.9.9"\n')
            self.assertIsNone(vcc.read_pyproject_version(d))

    def test_project_version_after_tool_section(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "pyproject.toml",
                   '[tool.black]\nline-length = 120\n\n[project]\nname = "x"\nversion = "0.6.0"\n')
            self.assertEqual(vcc.read_pyproject_version(d), "0.6.0")


class TestCheck(unittest.TestCase):
    def test_consistent(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "src/__version__.py", '__version__ = "0.6.2-beta"\n')
            _write(d, "pyproject.toml", '[project]\nname="x"\nversion = "0.6.2-beta"\n')
            _write(d, "README.md",
                   "![V](https://img.shields.io/badge/version-0.6.2--beta-blue.svg)\n"
                   "## What Works (v0.6.2-beta)\n")
            ssot, problems = vcc.check(d)
            self.assertEqual(ssot, "0.6.2-beta")
            self.assertEqual(problems, [])

    def test_drift_is_reported_per_consumer(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "src/__version__.py", '__version__ = "0.6.2-beta"\n')
            _write(d, "pyproject.toml", '[project]\nname="x"\nversion = "0.5.5-beta"\n')
            _write(d, "README.md",
                   "![V](https://img.shields.io/badge/version-0.6.1--beta-blue.svg)\n")
            ssot, problems = vcc.check(d)
            self.assertEqual(ssot, "0.6.2-beta")
            self.assertEqual(len(problems), 2)  # pyproject + badge both drift

    def test_unreadable_ssot_fails_not_passes(self):
        # honest_failure_modes #2 — unobservable is never "consistent".
        with tempfile.TemporaryDirectory() as d:
            ssot, problems = vcc.check(d)  # no SSOT file at all
            self.assertIsNone(ssot)
            self.assertTrue(problems)

    def test_no_consumer_declares_version_fails(self):
        # A repo can't pass by declaring versions nowhere but the SSOT.
        with tempfile.TemporaryDirectory() as d:
            _write(d, "src/__version__.py", '__version__ = "0.6.2-beta"\n')
            _write(d, "README.md", "# no badge, no heading\n")
            ssot, problems = vcc.check(d)
            self.assertEqual(ssot, "0.6.2-beta")
            self.assertTrue(problems)


class TestAutoDetectSSOT(unittest.TestCase):
    """One byte-identical guard must find the SSOT wherever a repo keeps it."""

    def test_detects_src_init(self):
        # meshforge-maps keeps __version__ in src/__init__.py, not src/__version__.py.
        with tempfile.TemporaryDirectory() as d:
            _write(d, "src/__init__.py", '__version__ = "0.7.4-beta"\n')
            rel, ver = vcc.resolve_ssot(d)
            self.assertEqual((rel, ver), ("src/__init__.py", "0.7.4-beta"))

    def test_detects_meshing_around_client(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "meshing_around_clients/__init__.py", '__version__ = "0.6.0"\n')
            rel, ver = vcc.resolve_ssot(d)
            self.assertEqual((rel, ver), ("meshing_around_clients/__init__.py", "0.6.0"))

    def test_explicit_ssot_overrides_autodetect(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "src/__version__.py", '__version__ = "9.9.9"\n')
            _write(d, "custom/ver.py", '__version__ = "1.2.3"\n')
            rel, ver = vcc.resolve_ssot(d, "custom/ver.py")
            self.assertEqual((rel, ver), ("custom/ver.py", "1.2.3"))

    def test_no_candidate_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            rel, ver = vcc.resolve_ssot(d)
            self.assertIsNone(ver)  # caller treats None as failure, never "consistent"


if __name__ == "__main__":
    unittest.main()
