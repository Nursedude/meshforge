"""rnsd's private key lives at storage/transport_identity (b8d5a8d4), but
``ReticulumPaths._fix_storage_file_permissions`` still believed "the actual
secrets are in the parent config dir" and set 0666 on EVERY file under
storage/ and 0777 on every dir — undoing the 600 the permission foundation
sets (non-author pass 2026-10-15, B1: the manager box's key read 0666 live), and a
0777 storage/ let any local user rename the key away and drop in their own
(B2). Caches keep the shared-write behaviour rnsd needs."""
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.paths import ReticulumPaths  # noqa: E402


def _tree(tmp_path, monkeypatch, key_mode):
    storage = tmp_path / "storage"
    (storage / "cache").mkdir(parents=True)
    key = storage / "transport_identity"
    key.write_bytes(b"k" * 64)
    os.chmod(key, key_mode)
    cache = storage / "cache" / "entry"
    cache.write_bytes(b"c")
    os.chmod(cache, 0o644)
    monkeypatch.setattr(ReticulumPaths, "ETC_STORAGE", storage)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", tmp_path)
    ReticulumPaths._fix_storage_file_permissions()
    return storage, key, cache


def _mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_the_private_key_is_never_widened(tmp_path, monkeypatch):
    _, key, cache = _tree(tmp_path, monkeypatch, 0o600)
    assert _mode(key) == 0o600, oct(_mode(key))
    # Was "caches keep shared write (0666)". Since 2026-10-15 a known non-root
    # owner (here: the test user owns the key) means NO widening — the cache
    # stays as born; see the B2 ownership tests below.
    assert _mode(cache) & 0o444 and not (_mode(cache) & 0o022)


def test_a_world_writable_key_loses_its_write_bits(tmp_path, monkeypatch):
    _, key, _ = _tree(tmp_path, monkeypatch, 0o666)
    assert not (_mode(key) & (stat.S_IWGRP | stat.S_IWOTH)), oct(_mode(key))


def test_storage_root_is_not_made_sticky(tmp_path, monkeypatch):
    # Sticky was tried and REVERTED (non-author read 2026-10-15): rnsd saves
    # known_destinations by tmp + os.replace in this dir, which a sticky dir
    # refuses once that file belongs to another uid — rnsd's own persistence
    # would fail silently. The key-swap risk (B2) stays OPEN for a decision.
    storage, _, _ = _tree(tmp_path, monkeypatch, 0o600)
    assert not (_mode(storage) & stat.S_ISVTX)


def test_any_identity_sized_file_is_never_widened(tmp_path, monkeypatch):
    # storage/identities/rncp was a live 64-byte private identity widened to
    # 0666 by the name test; an RNS identity file is exactly 64 bytes.
    storage, _, _ = _tree(tmp_path, monkeypatch, 0o600)
    (storage / "identities").mkdir()
    k = storage / "identities" / "rncp"
    k.write_bytes(b"k" * 64)
    os.chmod(k, 0o600)
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(k) == 0o600, oct(_mode(k))


# ── B2 closed by OWNERSHIP, not stickiness (operator "go", 2026-10-15) ────────
# Measured on all 9 rnsd boxes: rnsd and every non-root RNS consumer run as one
# user, and that user owns rnsd's own key. So the walk makes the tree belong to
# THAT user and stops widening: dirs 0755, files lose group/other WRITE, keys
# 0600. The roll was done by hand first; this walk runs on every gateway
# connect and sudo TUI start, and the old one put 0777/0666 straight back.

def _tight_tree(tmp_path, monkeypatch, owner=(4242, 4242)):
    storage = tmp_path / "storage"
    (storage / "cache" / "announces").mkdir(parents=True)
    for d in (storage, storage / "cache", storage / "cache" / "announces"):
        os.chmod(d, 0o777)
    key = storage / "transport_identity"
    key.write_bytes(b"k" * 64)
    os.chmod(key, 0o644)
    table = storage / "destination_table"
    table.write_bytes(b"t" * 10)
    os.chmod(table, 0o666)
    monkeypatch.setattr(ReticulumPaths, "ETC_STORAGE", storage)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", tmp_path)
    monkeypatch.setattr(ReticulumPaths, "_rnsd_storage_owner", classmethod(lambda cls: owner))
    return storage, key, table


def test_known_owner_narrows_and_never_widens(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    storage, key, table = _tight_tree(tmp_path, monkeypatch)
    ReticulumPaths._fix_storage_file_permissions()
    for d in (storage, storage / "cache", storage / "cache" / "announces"):
        assert _mode(d) == 0o755, (d, oct(_mode(d)))
    assert _mode(table) == 0o644, oct(_mode(table))
    assert _mode(key) == 0o600, oct(_mode(key))


def test_as_root_the_tree_is_handed_to_rnsds_user(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    chowned = []
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowned.append((str(p), u, g)))
    storage, key, table = _tight_tree(tmp_path, monkeypatch)
    ReticulumPaths._fix_storage_file_permissions()
    got = {p for p, u, g in chowned if (u, g) == (4242, 4242)}
    assert {str(storage), str(key), str(table)} <= got


def test_not_root_never_chowns(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    chowned = []
    monkeypatch.setattr(os, "chown", lambda *a: chowned.append(a))
    _tight_tree(tmp_path, monkeypatch)
    ReticulumPaths._fix_storage_file_permissions()
    assert chowned == []


def test_unknown_owner_keeps_the_shared_write_fallback(tmp_path, monkeypatch):
    # No key yet (fresh box) or rnsd runs as root: nothing measured says who
    # must write, so the legacy shared-write stays — keys are still never widened.
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    storage, key, table = _tight_tree(tmp_path, monkeypatch, owner=None)
    os.chmod(table, 0o644)
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(table) == 0o666 and _mode(storage) == 0o777
    assert not (_mode(key) & (stat.S_IWGRP | stat.S_IWOTH))


def test_owner_is_read_from_rnsds_own_key(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(ReticulumPaths, "ETC_STORAGE", storage)
    assert ReticulumPaths._rnsd_storage_owner() is None          # no key: unknown
    (storage / "transport_identity").write_bytes(b"k" * 64)
    st = (storage / "transport_identity").stat()
    expect = None if st.st_uid == 0 else (st.st_uid, st.st_gid)
    assert ReticulumPaths._rnsd_storage_owner() == expect


def test_new_storage_dirs_are_not_born_world_writable():
    src = (Path(__file__).resolve().parent.parent / "src" / "utils" / "paths.py").read_text()
    assert "mkdir(mode=0o777" not in src
