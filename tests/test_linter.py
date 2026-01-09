"""
Tests for MeshForge linter.

Run: python3 -m pytest tests/test_linter.py -v
"""

import pytest
import sys
import os
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from lint import MeshForgeLinter, LintIssue, Severity


@pytest.fixture
def linter():
    """Create a fresh linter instance."""
    return MeshForgeLinter()


class TestMF001PathHome:
    """Tests for MF001: Path.home() violation detection."""

    def test_detects_path_home(self, linter, tmp_path):
        """Test detection of Path.home() usage."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
from pathlib import Path
config = Path.home() / ".config" / "app"
""")
        issues = linter.lint_file(str(test_file))

        mf001_issues = [i for i in issues if i.code == "MF001"]
        assert len(mf001_issues) == 1
        assert "get_real_user_home" in mf001_issues[0].message

    def test_allows_fallback_pattern(self, linter, tmp_path):
        """Test that fallback pattern is allowed."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
def get_real_user_home():
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        return Path(f'/home/{sudo_user}')
    return Path.home()
""")
        issues = linter.lint_file(str(test_file))

        mf001_issues = [i for i in issues if i.code == "MF001"]
        assert len(mf001_issues) == 0

    def test_allows_ternary_fallback(self, linter, tmp_path):
        """Test that ternary fallback pattern is allowed."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
home = Path(f'/home/{sudo_user}') if sudo_user else Path.home()
""")
        issues = linter.lint_file(str(test_file))

        mf001_issues = [i for i in issues if i.code == "MF001"]
        assert len(mf001_issues) == 0

    def test_allows_import_fallback(self, linter, tmp_path):
        """Test that import with fallback pattern is allowed."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
try:
    from utils.paths import get_real_user_home
except ImportError:
    home = Path.home()
""")
        issues = linter.lint_file(str(test_file))

        mf001_issues = [i for i in issues if i.code == "MF001"]
        assert len(mf001_issues) == 0


class TestMF002ShellTrue:
    """Tests for MF002: shell=True detection."""

    def test_detects_shell_true(self, linter, tmp_path):
        """Test detection of shell=True in subprocess."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
import subprocess
result = subprocess.run("ls -la", shell=True)
""")
        issues = linter.lint_file(str(test_file))

        mf002_issues = [i for i in issues if i.code == "MF002"]
        assert len(mf002_issues) == 1

    def test_ignores_comments(self, linter, tmp_path):
        """Test that shell=True in comments is ignored."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
import subprocess
# Never use shell=True for security reasons
result = subprocess.run(["ls", "-la"])
""")
        issues = linter.lint_file(str(test_file))

        mf002_issues = [i for i in issues if i.code == "MF002"]
        assert len(mf002_issues) == 0

    def test_ignores_docstrings(self, linter, tmp_path):
        """Test that shell=True in docstrings is ignored."""
        test_file = tmp_path / "test.py"
        test_file.write_text('''
import subprocess
def run_command():
    """
    Security: Uses argument lists instead of shell=True to prevent injection.
    """
    return subprocess.run(["ls"])
''')
        issues = linter.lint_file(str(test_file))

        mf002_issues = [i for i in issues if i.code == "MF002"]
        assert len(mf002_issues) == 0


class TestMF003BareExcept:
    """Tests for MF003: bare except detection."""

    def test_detects_bare_except(self, linter, tmp_path):
        """Test detection of bare except clause."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
try:
    risky_operation()
except:
    pass
""")
        issues = linter.lint_file(str(test_file))

        mf003_issues = [i for i in issues if i.code == "MF003"]
        assert len(mf003_issues) == 1
        assert mf003_issues[0].severity == Severity.WARNING

    def test_allows_specific_exception(self, linter, tmp_path):
        """Test that specific exception types are allowed."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
try:
    risky_operation()
except Exception as e:
    logger.error(e)
""")
        issues = linter.lint_file(str(test_file))

        mf003_issues = [i for i in issues if i.code == "MF003"]
        assert len(mf003_issues) == 0


class TestMF004SubprocessTimeout:
    """Tests for MF004: subprocess timeout detection."""

    def test_detects_missing_timeout(self, linter, tmp_path):
        """Test detection of subprocess.run without timeout."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
import subprocess
result = subprocess.run(["ls", "-la"])
""")
        issues = linter.lint_file(str(test_file))

        mf004_issues = [i for i in issues if i.code == "MF004"]
        assert len(mf004_issues) == 1
        assert mf004_issues[0].severity == Severity.WARNING

    def test_allows_with_timeout(self, linter, tmp_path):
        """Test that subprocess with timeout is allowed."""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
import subprocess
result = subprocess.run(["ls", "-la"], timeout=30)
""")
        issues = linter.lint_file(str(test_file))

        mf004_issues = [i for i in issues if i.code == "MF004"]
        assert len(mf004_issues) == 0


class TestLinterOutput:
    """Tests for linter output formatting."""

    def test_lint_issue_str(self):
        """Test LintIssue string representation."""
        issue = LintIssue(
            file="test.py",
            line=42,
            severity=Severity.ERROR,
            code="MF001",
            message="Test message"
        )

        result = str(issue)

        assert "test.py:42" in result
        assert "MF001" in result
        assert "Test message" in result

    def test_severity_icons(self):
        """Test that severity is shown in output."""
        error = LintIssue("f", 1, Severity.ERROR, "E", "error")
        warning = LintIssue("f", 1, Severity.WARNING, "W", "warning")

        assert "[E]" in str(error)
        assert "[W]" in str(warning)
