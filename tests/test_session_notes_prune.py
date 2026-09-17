"""Guards for scripts/prune_session_notes_backups.sh.

The script DELETES files, so its gate is the test surface. Born 2026-09-17:
21 hand-made backups (2.2 MB) had accumulated in ~/.claude/plans, and 22 of
their sections existed in NEITHER the live notes NOR the archive — hand-edits
during the 09-11..09-14 network incident trimmed sections without rotating
them. An age-based prune would have destroyed ~6 sessions' handoffs silently,
so the deletable test is COVERAGE, and these tests pin both directions.
"""
from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prune_session_notes_backups.sh"
COVERED = "gateway-session-notes-{h}.md.bak-covered"
ORPHAN = "gateway-session-notes-{h}.md.bak-orphan"


def _host() -> str:
    return socket.gethostname().lower()


def _run(plans: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), "--dir", str(plans), *args],
        capture_output=True, text=True, timeout=60,
    )


@pytest.fixture()
def plans(tmp_path: Path) -> Path:
    """A notes dir with one covered backup and one orphan-bearing backup."""
    h = _host()
    d = tmp_path / "plans"
    d.mkdir()
    (d / f"gateway-session-notes-{h}.md").write_text("## SECTION ALPHA\nbody a\n")
    (d / f"gateway-session-notes-{h}-archive-2026H1.md").write_text("## SECTION BETA\nbody b\n")
    cov = d / COVERED.format(h=h)
    cov.write_text("## SECTION ALPHA\nbody a\n## SECTION BETA\nbody b\n")
    orp = d / ORPHAN.format(h=h)
    orp.write_text("## SECTION ALPHA\nbody a\n## SECTION GAMMA\nonly copy\n")
    # Age both past the default 3-day gate so coverage is the only variable.
    old = 1600000000
    import os
    for f in (cov, orp):
        os.utime(f, (old, old))
    return d


def test_dry_run_deletes_nothing(plans: Path) -> None:
    before = sorted(p.name for p in plans.iterdir())
    res = _run(plans)
    assert res.returncode == 0, res.stderr
    assert "DRY-RUN" in res.stdout
    assert sorted(p.name for p in plans.iterdir()) == before


def test_apply_removes_only_the_covered_backup(plans: Path) -> None:
    h = _host()
    res = _run(plans, "--apply")
    assert res.returncode == 0, res.stderr
    assert not (plans / COVERED.format(h=h)).exists(), "covered backup should be deleted"
    assert (plans / ORPHAN.format(h=h)).exists(), "orphan-bearing backup must survive"


def test_orphan_is_reported_loudly(plans: Path) -> None:
    """An unpreserved section is a FINDING, not silent garbage."""
    res = _run(plans, "--apply")
    assert "KEEP-ORPHAN" in res.stdout
    assert "preserved NOWHERE ELSE" in res.stdout
    assert ORPHAN.format(h=_host()) in res.stdout


def test_live_notes_and_archive_are_never_selected(plans: Path) -> None:
    h = _host()
    _run(plans, "--apply")
    assert (plans / f"gateway-session-notes-{h}.md").exists()
    assert (plans / f"gateway-session-notes-{h}-archive-2026H1.md").exists()


def test_young_backup_survives_even_when_covered(plans: Path) -> None:
    h = _host()
    young = plans / f"gateway-session-notes-{h}.md.bak-young"
    young.write_text("## SECTION ALPHA\nbody a\n")     # covered, but fresh
    res = _run(plans, "--apply")
    assert young.exists(), "a fresh backup is a session's safety net"
    assert "KEEP-YOUNG" in res.stdout


def test_absent_notes_reads_inert_not_error(tmp_path: Path) -> None:
    """Absent by design is inert — most fleet boxes have no session notes."""
    empty = tmp_path / "empty"
    empty.mkdir()
    res = _run(empty)
    assert res.returncode == 0
    assert "inert" in res.stdout


def test_bad_min_age_is_refused(plans: Path) -> None:
    res = _run(plans, "--min-age", "abc")
    assert res.returncode == 1
    assert "refused" in res.stderr
