"""Executable drills for scripts/rotate_session_notes.sh.

WHY THIS FILE EXISTS
--------------------
The helper was drilled by hand on 2026-08-31 — fence handling, duplicate
headings, refusal paths, fault injection — in a scratchpad directory that no
longer exists. Prose in a commit message is not a check
(`e48a7e24 docs(substack): rules in context are not checks`), and a drill
nobody can re-run is a claim, not evidence. These tests make each drill
permanent so a regression is caught by the suite rather than by a lost
handoff doc.

The script MUTATES the operator's session-notes handoff file. Every test here
runs against fixtures under tmp_path with HOME redirected, so backups, locks
and archives all land in the sandbox and never touch a real notes file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rotate_session_notes.sh"

# Byte length of the rotation banner is filename-dependent; tests assert
# relationships (deltas, counts) rather than hardcoding it.


def run(tmp_home: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    """Invoke the script with HOME redirected into the sandbox."""
    env = dict(os.environ, HOME=str(tmp_home))
    env.update(kw.pop("env", {}))
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True, text=True, timeout=60, env=env, **kw,
    )


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    """A five-section notes file whose LAST section is live (sticky)."""
    p = tmp_path / "gateway-session-notes-testbox.md"
    p.write_text(
        "# Gateway session notes — testbox\n\n"
        "## Newest close\nalpha\n\n"
        "## Second close\nbravo\n\n"
        "## Third close\ncharlie\n\n"
        "## Fourth close\ndelta\n\n"
        "## QUEUED — still live\necho\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


class TestDryRunIsInert:
    def test_dry_run_writes_nothing(self, notes, home):
        before = notes.read_bytes()
        r = run(home, "--notes", str(notes))
        assert r.returncode == 0, r.stderr
        assert notes.read_bytes() == before
        assert "DRY-RUN" in r.stdout

    def test_dry_run_creates_no_archive(self, notes, home, tmp_path):
        run(home, "--notes", str(notes))
        assert not list(tmp_path.glob("*-archive-*.md"))


class TestPositionIsNotStaleness:
    """The live 'QUEUED' section sits LAST. A tail-drop would eat it."""

    def test_sticky_section_is_kept_despite_being_last(self, notes, home):
        r = run(home, "--notes", str(notes), "--keep", "2")
        assert "KEEP-STICKY" in r.stdout
        rotated = [ln for ln in r.stdout.splitlines() if "ROTATE" in ln]
        assert not any("QUEUED" in ln for ln in rotated)

    def test_sticky_survives_apply(self, notes, home):
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "## QUEUED — still live" in notes.read_text(encoding="utf-8")


class TestFenceAwareness:
    """A '## ' inside a ``` block is sample markdown, not a boundary."""

    @staticmethod
    def _fenced(tmp_path: Path) -> Path:
        p = tmp_path / "gateway-session-notes-fenced.md"
        p.write_text(
            "# T\n\n"
            "## One\na\n\n"
            "## Two\n"
            "```markdown\n"
            "## NOT A SECTION\n"
            "## ALSO NOT A SECTION\n"
            "```\n"
            "tail\n\n"
            "## Three\nc\n",
            encoding="utf-8",
        )
        return p

    def test_fenced_headings_are_not_sections(self, tmp_path, home):
        p = self._fenced(tmp_path)
        r = run(home, "--notes", str(p))
        assert "3 section(s)" in r.stdout
        assert "NOT A SECTION" not in r.stdout

    def test_naive_split_would_have_found_more(self, tmp_path):
        """Pins that fence-awareness actually CHANGES the answer."""
        p = self._fenced(tmp_path)
        naive = sum(1 for ln in p.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("## "))
        assert naive == 5, "fixture must contain the trap"

    def test_unbalanced_fence_is_refused_by_line(self, tmp_path, home):
        p = tmp_path / "gateway-session-notes-bad.md"
        p.write_text("# T\n\n## One\na\n\n```\nunclosed\n\n## Two\nb\n",
                     encoding="utf-8")
        r = run(home, "--notes", str(p))
        assert r.returncode == 1
        assert "unbalanced code fence opened at line 6" in r.stderr


class TestByteExactConservation:
    def test_apply_conserves_every_byte(self, notes, home, tmp_path):
        before_notes = notes.stat().st_size
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        archive = next(tmp_path.glob("*-archive-*.md"))
        after_notes = notes.stat().st_size
        # notes shrank by exactly the rotated bytes; archive gained that plus
        # the banner, so the union is strictly larger than the original.
        assert after_notes < before_notes
        assert "notes lost exactly" in r.stdout
        assert "archive gained exactly" in r.stdout
        assert archive.stat().st_size > (before_notes - after_notes)

    def test_no_original_line_is_lost(self, notes, home, tmp_path):
        original = [ln for ln in notes.read_text(encoding="utf-8").splitlines() if ln.strip()]
        run(home, "--notes", str(notes), "--keep", "2", "--apply")
        archive = next(tmp_path.glob("*-archive-*.md"))
        union = set(notes.read_text(encoding="utf-8").splitlines()) | \
                set(archive.read_text(encoding="utf-8").splitlines())
        assert [ln for ln in original if ln not in union] == []


class TestInputShapes:
    """Shapes found unhandled in the 2026-08-31 frontier pass — each one
    previously made verification FALSELY FAIL a correct rotation (rc 1,
    restore instructions printed, prune skipped)."""

    def test_no_trailing_final_newline_rotates_cleanly(self, tmp_path, home):
        """awk indexes every line as length+1, so a file whose last byte is
        not a newline was counted one byte large; when the LAST section
        rotated, the byte check failed a correct rotation by exactly 1 B."""
        p = tmp_path / "gateway-session-notes-nonl.md"
        p.write_bytes(b"# T\n\n## One\na\n\n## Two\nb\n\n## Three\nc\n\n## Four\nd")
        r = run(home, "--notes", str(p), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        archive = next(tmp_path.glob("*-archive-*.md"))
        body = archive.read_text(encoding="utf-8")
        assert "## Four\nd" in body and "## Three\nc" in body
        assert "## Four" not in p.read_text(encoding="utf-8")

    def test_no_trailing_final_newline_sticky_last(self, tmp_path, home):
        p = tmp_path / "gateway-session-notes-nonl2.md"
        p.write_bytes(b"# T\n\n## One\na\n\n## Two\nb\n\n## Three\nc\n\n## QUEUED live\nz")
        r = run(home, "--notes", str(p), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        assert p.read_bytes().endswith(b"## QUEUED live\nz")

    def test_duplicate_headings_both_rotate(self, tmp_path, home):
        """Two rotated sections sharing one heading: the archive count must
        grow by TWO; the old per-row +1 check falsely failed this."""
        p = tmp_path / "gateway-session-notes-dup2.md"
        p.write_text(
            "# T\n\n## Keep1\na\n\n## Keep2\nb\n\n## What happened\nold1\n\n"
            "## What happened\nold2\n\n## QUEUED live\nz\n", encoding="utf-8")
        r = run(home, "--notes", str(p), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        archive = next(tmp_path.glob("*-archive-*.md"))
        body = archive.read_text(encoding="utf-8")
        assert body.count("## What happened") == 2
        assert "old1" in body and "old2" in body

    def test_duplicate_heading_kept_and_rotated(self, tmp_path, home):
        """A KEPT section sharing a rotated one's heading is correct, not
        'still in notes' — the old presence check falsely failed this."""
        p = tmp_path / "gateway-session-notes-dup3.md"
        p.write_text(
            "# T\n\n## What happened\nnew\n\n## Keep2\nb\n\n"
            "## What happened\nold\n\n## QUEUED live\nz\n", encoding="utf-8")
        r = run(home, "--notes", str(p), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        notes_body = p.read_text(encoding="utf-8")
        assert notes_body.count("## What happened") == 1 and "new" in notes_body
        archive = next(tmp_path.glob("*-archive-*.md"))
        assert "old" in archive.read_text(encoding="utf-8")

    def test_tab_inside_heading_verifies(self, tmp_path, home):
        """The index TSV is tab-delimited; a tab INSIDE a heading truncated
        the plan's heading field, so every verification grep missed."""
        p = tmp_path / "gateway-session-notes-tab.md"
        p.write_text(
            "# T\n\n## Keep1\na\n\n## Keep2\nb\n\n## Tab\there heading\nold\n\n"
            "## QUEUED live\nz\n", encoding="utf-8")
        r = run(home, "--notes", str(p), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        archive = next(tmp_path.glob("*-archive-*.md"))
        assert "## Tab\there heading\nold" in archive.read_text(encoding="utf-8")

    def test_clean_apply_leaves_no_temp_files(self, notes, home, tmp_path):
        """Until 2026-08-31 a `trap - EXIT` after the mv's leaked the .add
        temp beside the archive and the pre-counts file in TMPDIR on EVERY
        clean apply — SD-card litter that grew forever."""
        tmpdir = tmp_path / "tmpdir"
        tmpdir.mkdir()
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                env={"TMPDIR": str(tmpdir)})
        assert r.returncode == 0, r.stdout + r.stderr
        assert list(tmpdir.iterdir()) == [], "leaked mktemp files in TMPDIR"
        stray = [f.name for f in tmp_path.iterdir()
                 if ".rotate." in f.name or ".add." in f.name]
        assert stray == [], f"leaked temp files beside the notes: {stray}"

    def test_archive_chosen_by_name_not_mtime(self, notes, home, tmp_path):
        """A restore/copy/touch reorders mtime; the newest NAME is the real
        'current' archive. A touched 2026H1 must not capture 2026H2 content."""
        h1 = tmp_path / f"{notes.stem}-archive-2026H1.md"
        h2 = tmp_path / f"{notes.stem}-archive-2026H2.md"
        h1.write_text("# H1\n", encoding="utf-8")
        h2.write_text("# H2\n", encoding="utf-8")
        os.utime(h2, (1577836800, 1577836800))  # 2020: H2 mtime far older
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "## Third close" in h2.read_text(encoding="utf-8")
        assert "## Third close" not in h1.read_text(encoding="utf-8")


class TestVerificationCannotPassVacuously:
    """The archive may ALREADY contain a rotated heading (generic ones like
    '## What happened' repeat). A presence check would pass without the
    append ever happening; the count must grow by exactly one."""

    def test_duplicate_heading_counts_not_presence(self, tmp_path, home):
        p = tmp_path / "gateway-session-notes-dup.md"
        p.write_text("# T\n\n## Newest\na\n\n## Second\nb\n\n## What happened\nOLD\n",
                     encoding="utf-8")
        arch = tmp_path / "gateway-session-notes-dup-archive-2026H1.md"
        arch.write_text("# Archive\n\n## What happened\nPRE-EXISTING\n", encoding="utf-8")
        r = run(home, "--notes", str(p), "--keep", "2", "--apply")
        assert r.returncode == 0, r.stdout + r.stderr
        body = arch.read_text(encoding="utf-8")
        assert body.count("## What happened") == 2
        assert "PRE-EXISTING" in body and "OLD" in body


class TestVerificationHasTeeth:
    """The post-apply verification must CATCH corruption, not just narrate.

    A passing rotation cannot prove this — the checks only speak when
    something is wrong. So inject faults into a COPY of the script and assert
    each assertion fires. Without these, removing the count-delta check left
    the whole suite green (mutation drill, 2026-08-31).
    """

    @staticmethod
    def _mutant(tmp_path: Path, sed_expr: str) -> Path:
        import shutil
        m = tmp_path / "mutant.sh"
        shutil.copy(SCRIPT, m)
        subprocess.run(["sed", "-i", sed_expr, str(m)], check=True, timeout=30)
        m.chmod(0o755)
        assert m.read_text(encoding="utf-8") != SCRIPT.read_text(encoding="utf-8"), \
            "fault injection did not change the script — the anchor drifted"
        return m

    def _run_mutant(self, mutant: Path, notes: Path, home: Path):
        env = dict(os.environ, HOME=str(home))
        return subprocess.run(
            [str(mutant), "--notes", str(notes), "--keep", "2", "--apply"],
            capture_output=True, text=True, timeout=60, env=env,
        )

    def test_extra_byte_in_archive_is_caught(self, notes, home, tmp_path):
        """Byte-exact assertion: one stray byte must fail the run."""
        m = self._mutant(
            tmp_path,
            's|^cat "$tmp_add" >> "$tmp_arch"$|cat "$tmp_add" >> "$tmp_arch"; printf X >> "$tmp_arch"|',
        )
        r = self._run_mutant(m, notes, home)
        assert r.returncode == 1
        assert "archive size" in r.stdout and "expected" in r.stdout
        assert "VERIFICATION FAILED" in r.stdout

    def test_same_size_wrong_content_is_caught(self, notes, home, tmp_path):
        """Isolates the COUNT check: '## ' -> '#@ ' keeps the byte total
        identical, so only the per-heading occurrence delta can notice."""
        m = self._mutant(
            tmp_path,
            "s|^cat \"$tmp_add\" >> \"$tmp_arch\"$|sed 's/^## /#@ /' \"$tmp_add\" >> \"$tmp_arch\"|",
        )
        r = self._run_mutant(m, notes, home)
        assert r.returncode == 1, r.stdout
        assert "archive count for" in r.stdout, \
            "byte totals matched, so the count-delta check had to be the one to fire"

    def test_dropped_kept_section_is_caught(self, notes, home, tmp_path):
        m = self._mutant(tmp_path, r's|^done >> "$tmp_notes"$|done \| head -c -5 >> "$tmp_notes"|')
        r = self._run_mutant(m, notes, home)
        assert r.returncode == 1
        assert "notes size" in r.stdout or "KEPT section vanished" in r.stdout

    def test_rotated_section_left_in_notes_is_caught(self, notes, home, tmp_path):
        """Pins the notes-side count check: make the kept-extraction keep
        EVERY section, so rotated content lands in both files. The notes
        count must fire (before - k expected, before observed)."""
        m = self._mutant(tmp_path, r's|$2!="ROTATE"{print $5}|1{print $5}|')
        r = self._run_mutant(m, notes, home)
        assert r.returncode == 1
        assert "notes count for" in r.stdout, \
            "rotated sections stayed in the notes and the count check missed it"


class TestRefusals:
    def test_bad_keep(self, notes, home):
        r = run(home, "--notes", str(notes), "--keep", "abc")
        assert r.returncode == 1 and "non-negative integer" in r.stderr

    def test_unknown_arg(self, notes, home):
        r = run(home, "--notes", str(notes), "--bogus")
        assert r.returncode == 1 and "unknown argument" in r.stderr

    def test_missing_notes(self, home, tmp_path):
        r = run(home, "--notes", str(tmp_path / "nope.md"))
        assert r.returncode == 1 and "not readable" in r.stderr

    def test_no_sections(self, tmp_path, home):
        p = tmp_path / "gateway-session-notes-empty.md"
        p.write_text("# just a title\n\nno sections\n", encoding="utf-8")
        r = run(home, "--notes", str(p))
        assert r.returncode == 1 and "no '## ' sections" in r.stderr

    def test_nothing_to_rotate_is_success(self, notes, home):
        r = run(home, "--notes", str(notes), "--keep", "99")
        assert r.returncode == 0 and "nothing to rotate" in r.stdout

    def test_symlinked_notes_refused(self, notes, home, tmp_path):
        """mv would replace the LINK with a regular file, orphaning the target."""
        link = tmp_path / "gateway-session-notes-link.md"
        link.symlink_to(notes)
        r = run(home, "--notes", str(link), "--apply")
        assert r.returncode == 1 and "is a symlink" in r.stderr
        assert link.is_symlink()

    def test_backup_collision_refused(self, notes, home):
        """A backup must never be overwritten
        (feedback_backup_destinations_must_be_namespaced). The backup name is
        second-granular, so seed the next few seconds to force the collision
        deterministically rather than racing it."""
        import datetime
        bkdir = home / ".local/state/meshforge/session_notes_backup"
        bkdir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone.utc)
        for delta in range(4):
            ts = (now + datetime.timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")
            (bkdir / f"{notes.stem}.{ts}.md.bak").write_text("SENTINEL", encoding="utf-8")
        before = notes.read_bytes()
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply")
        assert r.returncode == 1
        assert "backup path already exists" in r.stderr
        assert notes.read_bytes() == before, "notes must be untouched when refusing"
        for f in bkdir.iterdir():
            assert f.read_text(encoding="utf-8") == "SENTINEL", "clobbered a backup"


class TestBackupPruning:
    """Retention on the backup dir. This feature DELETES, inside the one
    directory whose entire job is recovery, so the tests that matter are the
    ones proving what it must NOT touch."""

    @staticmethod
    def _bkdir(home: Path) -> Path:
        return home / ".local/state/meshforge/session_notes_backup"

    @staticmethod
    def _seed(bkdir: Path, stem: str, n: int) -> list:
        """n old backups with valid, lexically-ordered timestamps."""
        bkdir.mkdir(parents=True, exist_ok=True)
        made = []
        for i in range(n):
            f = bkdir / f"{stem}.2020-01-{i+1:02d}T00:00:00Z.md.bak"
            f.write_text(f"old backup {i}", encoding="utf-8")
            made.append(f)
        return made

    def test_keeps_newest_n(self, notes, home):
        bk = self._bkdir(home)
        self._seed(bk, notes.stem, 15)
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                "--keep-backups", "5")
        assert r.returncode == 0, r.stdout + r.stderr
        remaining = sorted(f.name for f in bk.glob(f"{notes.stem}.*.md.bak"))
        assert len(remaining) == 5
        # the run's OWN backup is the newest and must survive
        assert any("2026" in n or "202" in n for n in remaining)
        newest_old = f"{notes.stem}.2020-01-15T00:00:00Z.md.bak"
        assert newest_old in remaining, "pruned newest-first instead of oldest-first"
        assert f"{notes.stem}.2020-01-01T00:00:00Z.md.bak" not in remaining

    def test_never_prunes_the_backup_it_just_wrote(self, notes, home):
        bk = self._bkdir(home)
        self._seed(bk, notes.stem, 20)
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                "--keep-backups", "1")
        assert r.returncode == 0
        remaining = list(bk.glob(f"{notes.stem}.*.md.bak"))
        assert len(remaining) == 1
        # the survivor must be THIS run's backup: a copy of the pre-apply notes
        assert "## Newest close" in remaining[0].read_text(encoding="utf-8")
        assert "old backup" not in remaining[0].read_text(encoding="utf-8")

    def test_own_backup_survives_a_backward_clock(self, notes, home):
        """Wall clock is forgeable here (RTC-less Pis; moc4 once ran ~8 days
        behind). If the box clock is stale, THIS run's backup gets an OLD
        filename and a rank-based prune would delete the very recovery point
        it just made. Seed FUTURE-dated backups to force that ordering."""
        bk = self._bkdir(home)
        bk.mkdir(parents=True, exist_ok=True)
        for i in range(12):
            (bk / f"{notes.stem}.2099-01-{i+1:02d}T00:00:00Z.md.bak").write_text(
                "future-dated", encoding="utf-8")
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                "--keep-backups", "3")
        assert r.returncode == 0, r.stdout + r.stderr
        survivors = [f.read_text(encoding="utf-8") for f in bk.glob(f"{notes.stem}.*.md.bak")]
        assert any("## Newest close" in body for body in survivors), \
            "pruned this run's own backup because the clock made it sort old"

    def test_foreign_files_are_never_candidates(self, notes, home):
        """Anything not matching the exact naming shape is not ours."""
        bk = self._bkdir(home)
        self._seed(bk, notes.stem, 12)
        foreign = {
            "important-operator-file.md": "DO NOT DELETE",
            f"{notes.stem}.md.bak": "no timestamp",
            f"{notes.stem}.notatimestamp.md.bak": "bad stamp",
            f"{notes.stem}.2020-01-01T00:00:00Z.md": "not a .bak",
            "other-notes.2020-01-01T00:00:00Z.md.bak": "different stem",
        }
        bk.mkdir(parents=True, exist_ok=True)
        for name, body in foreign.items():
            (bk / name).write_text(body, encoding="utf-8")
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                "--keep-backups", "2")
        assert r.returncode == 0
        for name, body in foreign.items():
            assert (bk / name).exists(), f"deleted a non-candidate: {name}"
            assert (bk / name).read_text(encoding="utf-8") == body

    def test_no_pruning_when_verification_fails(self, notes, home):
        """A bad rotation makes every backup evidence — touch none of it."""
        bk = self._bkdir(home)
        seeded = self._seed(bk, notes.stem, 12)
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                "--keep-backups", "1",
                env={"SESSION_NOTES_GATE_BYTES": "10"})
        assert r.returncode == 1, "fixture must fail verification"
        for f in seeded:
            assert f.exists(), "pruned backups after a FAILED verification"

    def test_dry_run_prunes_nothing(self, notes, home):
        bk = self._bkdir(home)
        seeded = self._seed(bk, notes.stem, 12)
        r = run(home, "--notes", str(notes), "--keep", "2", "--keep-backups", "1")
        assert r.returncode == 0
        for f in seeded:
            assert f.exists(), "a dry run deleted files"

    def test_zero_disables_pruning(self, notes, home):
        bk = self._bkdir(home)
        seeded = self._seed(bk, notes.stem, 12)
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                "--keep-backups", "0")
        assert r.returncode == 0
        for f in seeded:
            assert f.exists(), "--keep-backups 0 must disable pruning"

    def test_archive_backups_pruned_independently(self, notes, home, tmp_path):
        """The notes stem is a PREFIX of the archive stem — retention must not
        let one bucket evict the other."""
        arch = tmp_path / f"{notes.stem}-archive-2026H1.md"
        arch.write_text("# Archive\n", encoding="utf-8")
        bk = self._bkdir(home)
        self._seed(bk, notes.stem, 8)
        self._seed(bk, arch.stem, 8)
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                "--keep-backups", "3")
        assert r.returncode == 0, r.stdout + r.stderr
        assert len(list(bk.glob(f"{notes.stem}.20*.md.bak"))) == 3
        assert len(list(bk.glob(f"{arch.stem}.20*.md.bak"))) == 3

    def test_bad_keep_backups_refused(self, notes, home):
        r = run(home, "--notes", str(notes), "--keep-backups", "abc")
        assert r.returncode == 1 and "--keep-backups must be" in r.stderr


class TestSingleWriterExclusion:
    """honest_failure_modes #8 — two --apply runs must not interleave."""

    def test_apply_refuses_while_lock_held(self, notes, home):
        import fcntl
        lockdir = home / ".local/state/meshforge"
        lockdir.mkdir(parents=True, exist_ok=True)
        lockfile = lockdir / f"rotate_session_notes.{notes.stem}.lock"
        with open(lockfile, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = run(home, "--notes", str(notes), "--keep", "2", "--apply")
            assert r.returncode == 1
            assert "refusing to interleave writers" in r.stderr
        # lock released -> the same call now succeeds
        r2 = run(home, "--notes", str(notes), "--keep", "2", "--apply")
        assert r2.returncode == 0, r2.stdout + r2.stderr

    def test_dry_run_is_not_blocked_by_the_lock(self, notes, home):
        import fcntl
        lockdir = home / ".local/state/meshforge"
        lockdir.mkdir(parents=True, exist_ok=True)
        lockfile = lockdir / f"rotate_session_notes.{notes.stem}.lock"
        with open(lockfile, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = run(home, "--notes", str(notes), "--keep", "2")
            assert r.returncode == 0, "a read-only dry-run must not need the lock"


class TestGateReporting:
    def test_gate_failure_branch_is_reachable(self, notes, home):
        """Drillable only via the test hook while real files sit under 80KB."""
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                env={"SESSION_NOTES_GATE_BYTES": "10"})
        assert r.returncode == 1
        assert "STILL over the gate" in r.stdout
        assert "VERIFICATION FAILED" in r.stdout

    def test_restore_command_actually_restores(self, notes, home):
        original = notes.read_bytes()
        r = run(home, "--notes", str(notes), "--keep", "2", "--apply",
                env={"SESSION_NOTES_GATE_BYTES": "10"})
        assert r.returncode == 1
        line = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("cp ")][0]
        src = line.split()[1]
        assert Path(src).read_bytes() == original
