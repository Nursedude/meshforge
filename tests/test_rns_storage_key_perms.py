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
    assert _mode(cache) == 0o666          # caches keep shared write


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
