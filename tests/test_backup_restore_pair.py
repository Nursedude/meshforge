"""fleet_backup.sh and fleet_restore.sh must agree on what they handle.

Born 2026-09-11, from a defect introduced by the fix for another defect.

That morning ``docs/install.md`` gained a section warning that a box has TWO
Reticulum identities and that "copying only ``~/.reticulum`` is the common
half-migration". That evening a grep of our own ``fleet_backup.sh`` found 21
references to ``/etc/reticulum`` and ZERO to ``~/.reticulum`` -- the same class
the doc warned about, inverted. It was fixed.

The fix was then itself half a fix: ``fleet_backup.sh`` started capturing five
new categories and ``fleet_restore.sh`` had ZERO references to any of them. A
file that is captured but never restored is the SAME half-migration moved one
step later -- the archive looks complete, the restore looks successful, and the
box comes back missing exactly the thing you took the backup for.

That is honest_failure_modes #4: a mechanism with a producing half and a
consuming half must wire together or fail together. This test is the "fail
together" part. It is deliberately NOT a check that either script mentions some
string -- that would pin spelling, not behaviour. It compares the set of archive
paths the WRITER creates against the set the READER consumes, so a sixth
category added to the backup fails here until the restore learns to read it.
"""

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
BACKUP = SCRIPTS / "fleet_backup.sh"
RESTORE = SCRIPTS / "fleet_restore.sh"

# Paths the backup only ever creates as a PARENT for something else, so there
# is nothing of its own for the restore to read. Keep this list short and
# justified; an entry here is an assertion that the path carries no payload.
PARENT_ONLY = {
    "home",  # mkdir -p "$staging/home" before writing home/crontab.txt
}


def _categories(text: str, var: str) -> set:
    """Top two path segments of every archive path built from ``var``."""
    found = set()
    for m in re.finditer(rf'\${var}/([A-Za-z0-9_./-]+)', text):
        parts = [p for p in m.group(1).split("/") if p and not p.startswith("$")]
        if not parts:
            continue
        found.add("/".join(parts[:2]) if len(parts) > 1 else parts[0])
    return found


@pytest.fixture(scope="module")
def backup_categories() -> set:
    return _categories(BACKUP.read_text(), "staging") - PARENT_ONLY


@pytest.fixture(scope="module")
def restore_categories() -> set:
    return _categories(RESTORE.read_text(), "EXTRACT_DIR")


class TestBackupRestorePair:
    def test_both_scripts_exist(self):
        """Guard the guard: a rename must not silently pass this file."""
        assert BACKUP.is_file(), f"{BACKUP} missing — this test cannot check anything"
        assert RESTORE.is_file(), f"{RESTORE} missing — this test cannot check anything"

    def test_the_scan_finds_something(self, backup_categories, restore_categories):
        """A regex that matches nothing would make every assertion below vacuous."""
        assert len(backup_categories) >= 5, (
            f"only found {backup_categories} in fleet_backup.sh — the path regex has "
            "probably stopped matching, which would make this whole file a no-op"
        )
        assert len(restore_categories) >= 5, (
            f"only found {restore_categories} in fleet_restore.sh — see above"
        )

    def test_everything_backed_up_is_restorable(
        self, backup_categories, restore_categories
    ):
        """The invariant. Captured-but-never-restored is a silent half-migration."""
        orphaned = backup_categories - restore_categories
        assert not orphaned, (
            "fleet_backup.sh writes archive path(s) that fleet_restore.sh never "
            f"reads: {sorted(orphaned)}.\n"
            "A captured-but-never-restored file is worse than an uncaptured one: "
            "the archive looks complete and the restore reports success, while the "
            "box comes back missing the thing you took the backup for.\n"
            "Teach fleet_restore.sh to read it, or add it to PARENT_ONLY with a "
            "reason if it genuinely carries no payload."
        )

    def test_both_rns_identities_are_handled_on_both_sides(self):
        """The specific pair that started this, pinned by behaviour on both sides.

        Not a spelling check: each side must both NAME the user-side archive
        path and act on the on-disk location, so deleting either half fails.
        """
        b, r = BACKUP.read_text(), RESTORE.read_text()
        for name, text, ondisk in (
            ("fleet_backup.sh", b, r"\$\{REAL_HOME\}/\.reticulum"),
            ("fleet_restore.sh", r, r"\$\{TARGET_HOME\}/\.reticulum"),
        ):
            assert "home/reticulum" in text, (
                f"{name} no longer handles the archive's user-identity path. A box "
                "has TWO RNS identities; restoring one brings it back as a "
                "different node for the other half, and nothing warns you."
            )
            assert re.search(ondisk, text), (
                f"{name} names the archive path but no longer touches the on-disk "
                "~/.reticulum location — the leg is wired to nothing."
            )
