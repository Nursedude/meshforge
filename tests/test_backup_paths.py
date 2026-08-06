"""Tests for utils.backup_paths — a backup step must never overwrite.

Born from a real loss on the manager box 2026-08-05: a generic
``~/delivery_db_backup_<date>/`` path was reused across two sister repos in
one shared home, and ``sqlite3.Connection.backup()`` overwrote the existing
backup in place. See the module docstring.
"""

import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.backup_paths import (  # noqa: E402
    BackupDestinationExists,
    reserve_backup_dir,
    reserve_backup_path,
)


class TestRefusesToOverwrite:
    """The invariant: the returned path never named an existing file."""

    def test_reserves_a_free_name(self, tmp_path):
        p = reserve_backup_path(tmp_path, "counters.db")
        assert p == tmp_path / "counters.db"
        assert p.exists() and p.stat().st_size == 0

    def test_refuses_when_destination_exists(self, tmp_path):
        (tmp_path / "counters.db").write_bytes(b"the only copy")
        with pytest.raises(BackupDestinationExists):
            reserve_backup_path(tmp_path, "counters.db")

    def test_refusal_leaves_the_existing_file_untouched(self, tmp_path):
        """The point of the whole module: the prior backup survives."""
        victim = tmp_path / "counters.db"
        victim.write_bytes(b"the only copy")
        with pytest.raises(BackupDestinationExists):
            reserve_backup_path(tmp_path, "counters.db")
        assert victim.read_bytes() == b"the only copy"

    def test_refusal_is_an_oserror(self, tmp_path):
        """Callers guarding with `except OSError` must keep working."""
        (tmp_path / "counters.db").write_bytes(b"x")
        with pytest.raises(OSError):
            reserve_backup_path(tmp_path, "counters.db")

    def test_refusal_names_the_path_and_the_way_out(self, tmp_path):
        (tmp_path / "counters.db").write_bytes(b"x")
        with pytest.raises(BackupDestinationExists) as e:
            reserve_backup_path(tmp_path, "counters.db")
        assert "counters.db" in str(e.value)
        assert "unique" in str(e.value)

    def test_creates_missing_directory(self, tmp_path):
        p = reserve_backup_path(tmp_path / "a" / "b", "x.db")
        assert p.exists()

    def test_reserved_file_is_owner_only_by_default(self, tmp_path):
        p = reserve_backup_path(tmp_path, "secret.db")
        assert p.stat().st_mode & 0o777 == 0o600


class TestUniqueMode:
    """`unique` still never destroys — it steps aside instead."""

    def test_disambiguates_instead_of_overwriting(self, tmp_path):
        (tmp_path / "config.bak").write_bytes(b"original")
        p = reserve_backup_path(tmp_path, "config.bak", on_exists="unique")
        assert p == tmp_path / "config-2.bak"
        assert (tmp_path / "config.bak").read_bytes() == b"original"

    def test_walks_past_several_collisions(self, tmp_path):
        (tmp_path / "c.bak").write_bytes(b"1")
        (tmp_path / "c-2.bak").write_bytes(b"2")
        (tmp_path / "c-3.bak").write_bytes(b"3")
        assert reserve_backup_path(
            tmp_path, "c.bak", on_exists="unique") == tmp_path / "c-4.bak"

    def test_preserves_multipart_suffix(self, tmp_path):
        """foo.tar.gz -> foo-2.tar.gz, not foo.tar-2.gz."""
        (tmp_path / "litter.tar.gz").write_bytes(b"x")
        p = reserve_backup_path(tmp_path, "litter.tar.gz", on_exists="unique")
        assert p.name == "litter-2.tar.gz"

    def test_unique_never_clobbers_any_prior(self, tmp_path):
        """Ten backups of the same name leave ten distinct files."""
        paths = []
        for i in range(10):
            p = reserve_backup_path(tmp_path, "s.db", on_exists="unique")
            p.write_text(str(i))
            paths.append(p)
        assert len(set(paths)) == 10
        assert sorted(p.read_text() for p in paths) == sorted(
            str(i) for i in range(10))


class TestValidation:

    @pytest.mark.parametrize("bad", ["a/b", "../escape", "/abs", "", ".", ".."])
    def test_rejects_non_bare_names(self, tmp_path, bad):
        """A backup helper must not be a way to write outside `directory`."""
        with pytest.raises(ValueError):
            reserve_backup_path(tmp_path, bad)

    def test_rejects_unknown_on_exists(self, tmp_path):
        with pytest.raises(ValueError):
            reserve_backup_path(tmp_path, "x.db", on_exists="overwrite")


class TestAtomicity:

    def test_concurrent_reservations_never_collide(self, tmp_path):
        """The reason this is O_EXCL and not check-then-write.

        A check-then-write leaves a window where two threads both see the
        path as free and both proceed — one silently destroying the other's
        backup. Twenty threads racing one name must produce twenty distinct
        paths.
        """
        results = []
        errors = []
        barrier = threading.Barrier(20)

        def worker():
            try:
                barrier.wait(timeout=10)
                results.append(
                    reserve_backup_path(tmp_path, "race.db", on_exists="unique"))
            except Exception as e:  # noqa: BLE001 - surfaced by the assert below
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"reservation raised under contention: {errors}"
        assert len(results) == 20
        assert len(set(results)) == 20, "two threads reserved the same path"

    def test_refuse_mode_wins_exactly_once_under_contention(self, tmp_path):
        """Exactly one racer may claim the name; the rest must be refused."""
        won, refused = [], []
        barrier = threading.Barrier(12)

        def worker():
            barrier.wait(timeout=10)
            try:
                won.append(reserve_backup_path(tmp_path, "once.db"))
            except BackupDestinationExists:
                refused.append(1)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(won) == 1
        assert len(refused) == 11


class TestReserveBackupDir:
    """The shape the real loss actually took."""

    def test_reserves_a_new_directory(self, tmp_path):
        d = reserve_backup_dir(tmp_path, "delivery_db_backup_2026-08-05")
        assert d.is_dir()

    def test_refuses_a_directory_with_content(self, tmp_path):
        """Verbatim replay of the incident."""
        existing = tmp_path / "delivery_db_backup_2026-08-05"
        existing.mkdir()
        (existing / "delivery_counters.db").write_bytes(b"meshforge's only copy")
        with pytest.raises(BackupDestinationExists):
            reserve_backup_dir(tmp_path, "delivery_db_backup_2026-08-05")
        assert (existing / "delivery_counters.db").read_bytes() == \
            b"meshforge's only copy"

    def test_accepts_an_empty_existing_directory(self, tmp_path):
        """Operator pre-created the folder: nothing is at risk."""
        (tmp_path / "b").mkdir()
        assert reserve_backup_dir(tmp_path, "b") == tmp_path / "b"

    def test_unique_mode_steps_aside(self, tmp_path):
        existing = tmp_path / "b"
        existing.mkdir()
        (existing / "keep").write_text("x")
        d = reserve_backup_dir(tmp_path, "b", on_exists="unique")
        assert d == tmp_path / "b-2"
        assert (existing / "keep").read_text() == "x"

    @pytest.mark.parametrize("bad", ["a/b", "..", ""])
    def test_rejects_non_bare_names(self, tmp_path, bad):
        with pytest.raises(ValueError):
            reserve_backup_dir(tmp_path, bad)
