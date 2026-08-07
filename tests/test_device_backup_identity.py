"""Backup identity must follow the FILE, not the pre-reservation label.

Born 2026-08-06 from the guard-spine-arc ultra review (bug_001): create_backup
built BackupMetadata BEFORE reserve_backup_path could step aside to a
disambiguated name, so a same-second second backup wrote `<id>-2.json` whose
on-disk metadata still said `<id>`. list_backups keys on metadata['backup_id'],
so both entries listed under ONE id — and restore/delete resolve
`<id>.json` first, meaning selecting the SECOND backup silently restored or
DELETED the FIRST. A wrong-artifact delete inside the module the 2026-08-05
backup-chokepoint arc shipped to prevent exactly that class.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

import commands.device_backup as db


class _FixedDatetime(datetime):
    """datetime.now() pinned so two create_backup calls collide by design."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 6, 12, 0, 0)


class _FakeProc:
    returncode = 0
    stdout = "Owner: Test Node (TST)\nMyNodeInfo: !deadbeef\n"
    stderr = ""


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "get_backup_dir", lambda: tmp_path)
    monkeypatch.setattr(db, "find_meshtastic_cli", lambda: "/bin/true")
    monkeypatch.setattr(db, "datetime", _FixedDatetime)
    monkeypatch.setattr(db.subprocess, "run", lambda *a, **k: _FakeProc())
    return tmp_path


def test_same_second_backups_get_distinct_ids_on_disk(backup_env):
    first = db.create_backup()
    second = db.create_backup()
    assert first["success"], first["error"]
    assert second["success"], second["error"]
    assert first["backup_id"] != second["backup_id"], \
        "the reservation stepped aside — the reported ids must differ"
    for r in (first, second):
        data = json.loads(Path(r["file_path"]).read_text())
        assert data["metadata"]["backup_id"] == Path(r["file_path"]).stem, (
            "on-disk metadata must carry the id of the file actually "
            "written, not the pre-reservation label")


def test_listed_ids_are_unique_and_delete_targets_the_selected_file(backup_env):
    first = db.create_backup()
    second = db.create_backup()
    assert first["success"] and second["success"]

    ids = [m["backup_id"] for m in db.list_backups()]
    assert len(ids) == len(set(ids)), \
        f"list_backups must never show two backups under one id: {ids}"

    # Deleting the SECOND backup must remove the second FILE — under the
    # stale-label defect it resolved <id>.json and unlinked the FIRST.
    res = db.delete_backup(second["backup_id"])
    assert res["success"], res["error"]
    assert Path(first["file_path"]).exists(), \
        "deleting the second backup destroyed the FIRST file"
    assert not Path(second["file_path"]).exists()
