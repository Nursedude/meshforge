"""Tests for scripts/lib/unit_compare.sh — Phase A of the
fleet_etc_reconcile design (see .claude/plans/fleet_etc_reconcile_design.md).

The lib is bash; tests drive it via subprocess. Each case sets up a
fake repo layout under tmp_path with template dir(s) + an installed
unit, then asserts the result string emitted by `unit_compare_check`.

The six contract cases pinned here:

1. Identical files → MATCH.
2. Comment-only difference → MATCH (closes the
   meshforge-map.service false-positive class that motivated Phase A).
3. Substantive difference → DRIFT + md5s + template path.
4. Placeholder `__MESHFORGE_USER__` substituted before compare.
5. No matching template → NO_TEMPLATE.
6. Missing installed file → MISSING.
"""

import subprocess
from pathlib import Path

import pytest


LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "unit_compare.sh"


def run_check(tmp_path: Path, installed_path: Path, extra_template_dirs=()):
    """Run `unit_compare_check <installed_path>` against an override
    UNIT_COMPARE_TEMPLATE_DIRS pointing at tmp_path's template dirs.

    Returns the trimmed stdout line.
    """
    dirs = " ".join(f'"{d}"' for d in [tmp_path / "templates"] + list(extra_template_dirs))
    script = f"""
        source {LIB}
        UNIT_COMPARE_TEMPLATE_DIRS=({dirs})
        unit_compare_check "{installed_path}"
    """
    result = subprocess.run(
        ["bash", "-c", script],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def fake_repo(tmp_path: Path):
    """Build a tmp_path/templates/ + tmp_path/installed/ pair."""
    (tmp_path / "templates").mkdir()
    (tmp_path / "installed").mkdir()
    return tmp_path


def test_identical_files_match(fake_repo: Path):
    body = "[Service]\nUser=wh6gxz\nExecStart=/bin/true\n"
    (fake_repo / "templates" / "x.service").write_text(body)
    inst = fake_repo / "installed" / "x.service"
    inst.write_text(body)

    assert run_check(fake_repo, inst) == "MATCH"


def test_comment_only_diff_matches(fake_repo: Path):
    """The motivating case: pure comment drift should NOT trigger DRIFT."""
    template = (
        "# Updated 2026-05-13 — added explanatory block\n"
        "# (this comment block doesn't change behavior)\n"
        "\n"
        "[Service]\n"
        "User=wh6gxz\n"
        "ExecStart=/bin/true\n"
    )
    installed = (
        "[Service]\n"
        "User=wh6gxz\n"
        "ExecStart=/bin/true\n"
    )
    (fake_repo / "templates" / "x.service").write_text(template)
    inst = fake_repo / "installed" / "x.service"
    inst.write_text(installed)

    assert run_check(fake_repo, inst) == "MATCH"


def test_substantive_diff_reports_drift(fake_repo: Path):
    """Real behavior change — the TimeoutStopSec=60 vs 300 class (observed
    on the canonical fleet writer 2026-05-13 after the WAL-checkpoint fix)."""
    template = "[Service]\nUser=wh6gxz\nTimeoutStopSec=300\n"
    installed = "[Service]\nUser=wh6gxz\nTimeoutStopSec=60\n"
    (fake_repo / "templates" / "x.service").write_text(template)
    inst = fake_repo / "installed" / "x.service"
    inst.write_text(installed)

    out = run_check(fake_repo, inst)
    parts = out.split()
    assert parts[0] == "DRIFT"
    assert len(parts) == 4  # DRIFT <md5> <md5> <path>
    assert parts[1] != parts[2]  # md5s differ
    assert parts[3].endswith("/x.service")


def test_placeholder_substituted_before_compare(fake_repo: Path):
    """Template has __MESHFORGE_USER__; installed has the real user.
    After install-time substitution they're identical → MATCH.
    """
    template = "[Service]\nUser=__MESHFORGE_USER__\nExecStart=/bin/true\n"
    installed = "[Service]\nUser=wh6gxz\nExecStart=/bin/true\n"
    (fake_repo / "templates" / "x.service").write_text(template)
    inst = fake_repo / "installed" / "x.service"
    inst.write_text(installed)

    assert run_check(fake_repo, inst) == "MATCH"


def test_no_template_returns_no_template(fake_repo: Path):
    inst = fake_repo / "installed" / "orphan.service"
    inst.write_text("[Service]\nExecStart=/bin/true\n")
    # No matching template in the fake repo.
    assert run_check(fake_repo, inst) == "NO_TEMPLATE"


def test_missing_installed_returns_missing(fake_repo: Path):
    inst = fake_repo / "installed" / "ghost.service"  # not created
    (fake_repo / "templates" / "ghost.service").write_text("anything\n")
    assert run_check(fake_repo, inst) == "MISSING"


def test_blank_lines_collapsed(fake_repo: Path):
    """Blank-line differences shouldn't trigger DRIFT — same class as comments."""
    template = "[Service]\n\n\nUser=wh6gxz\n\nExecStart=/bin/true\n"
    installed = "[Service]\nUser=wh6gxz\nExecStart=/bin/true\n"
    (fake_repo / "templates" / "x.service").write_text(template)
    inst = fake_repo / "installed" / "x.service"
    inst.write_text(installed)

    assert run_check(fake_repo, inst) == "MATCH"


def test_trailing_whitespace_ignored(fake_repo: Path):
    template = "[Service]\nUser=wh6gxz   \nExecStart=/bin/true\t\n"
    installed = "[Service]\nUser=wh6gxz\nExecStart=/bin/true\n"
    (fake_repo / "templates" / "x.service").write_text(template)
    inst = fake_repo / "installed" / "x.service"
    inst.write_text(installed)

    assert run_check(fake_repo, inst) == "MATCH"
