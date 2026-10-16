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
    # Recorded by INODE: since the symlink-safe walk (W1) every chown is an
    # fchown on an fd opened no-follow, so there is no path to record.
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    chowned = []
    monkeypatch.setattr(os, "fchown",
                        lambda fd, u, g: chowned.append((os.fstat(fd).st_ino, u, g)))
    monkeypatch.setattr(os, "chown", lambda *a, **k: chowned.append(("path", a)))
    storage, key, table = _tight_tree(tmp_path, monkeypatch)
    ReticulumPaths._fix_storage_file_permissions()
    got = {i for i, u, g in chowned if (u, g) == (4242, 4242)}
    assert {storage.stat().st_ino, key.stat().st_ino, table.stat().st_ino} <= got
    assert not [c for c in chowned if c[0] == "path"], "a path chown follows links"


def test_not_root_never_chowns(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    chowned = []
    monkeypatch.setattr(os, "chown", lambda *a: chowned.append(a))
    _tight_tree(tmp_path, monkeypatch)
    ReticulumPaths._fix_storage_file_permissions()
    assert chowned == []


def test_unknown_owner_keeps_the_shared_write_fallback(tmp_path, monkeypatch):
    # No key YET (fresh box): rnsd must still create it, so the legacy
    # shared-write stays — and any other identity-shaped file is never
    # widened. Since W3 (2026-10-15) a key that EXISTS but names no usable
    # owner does NOT reach this path; see the root-owned-key test.
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    storage, key, table = _tight_tree(tmp_path, monkeypatch, owner=None)
    key.unlink()
    other = storage / "identities_backup_identity"
    other.write_bytes(b"k" * 64)
    os.chmod(other, 0o644)
    os.chmod(table, 0o644)
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(table) == 0o666 and _mode(storage) == 0o777
    assert not (_mode(other) & (stat.S_IWGRP | stat.S_IWOTH))


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


# ── Symlink-safe walk (non-author read 2026-10-15, W1/W2/W3/W6) ──────────────
# The walk runs as ROOT on every sudo TUI start, over a tree rnsd's user owns
# (0755, theirs since the B2 roll) — so that user can plant or swap links in
# it. A check-then-chmod followed a swapped link 671/3000 times (measured), a
# symlinked storage/ root was walked through, and the identity/config tail
# chmod-ed through a link planted in the group-writable /etc/reticulum.
# These tests are implementation-agnostic: they plant the attack on a real
# tree and read the VICTIM outside it, never the walk's internals.

import threading  # noqa: E402


def _owned_tree(tmp_path, monkeypatch, *, as_root=False, owner="me"):
    storage = tmp_path / "etc" / "storage"
    (storage / "cache").mkdir(parents=True)
    key = storage / "transport_identity"
    key.write_bytes(b"k" * 64)
    os.chmod(key, 0o600)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(ReticulumPaths, "ETC_STORAGE", storage)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", tmp_path / "etc")
    who = (os.getuid(), os.getgid()) if owner == "me" else owner
    monkeypatch.setattr(ReticulumPaths, "_rnsd_storage_owner",
                        classmethod(lambda cls: who))
    if as_root:
        monkeypatch.setattr(os, "geteuid", lambda: 0)
    return storage, outside


def _race(storage, outside, swap, rounds=600):
    """Run the walk ``rounds`` times while ``swap`` flips a link in a thread;
    return how many rounds changed the victim outside the tree."""
    victim = outside / "victim"
    stop = threading.Event()

    def flipper():
        while not stop.is_set():
            swap()

    t = threading.Thread(target=flipper, daemon=True)
    t.start()
    hits = 0
    try:
        for _ in range(rounds):
            os.chmod(victim, 0o666)
            ReticulumPaths._fix_storage_file_permissions()
            if _mode(victim) != 0o666:
                hits += 1
    finally:
        stop.set()
        t.join(5)
    return hits


def test_a_file_swapped_for_a_link_mid_walk_never_reaches_outside(tmp_path, monkeypatch):
    storage, outside = _owned_tree(tmp_path, monkeypatch)
    (outside / "victim").write_bytes(b"v")
    entry = storage / "cache" / "entry"
    entry.write_bytes(b"c")
    os.chmod(entry, 0o644)
    staged = storage / "cache" / ".staged"

    def swap():
        try:
            os.symlink(outside / "victim", staged)
            os.replace(staged, entry)          # entry -> symlink to victim
            staged.write_bytes(b"c")
            os.chmod(staged, 0o644)
            os.replace(staged, entry)          # back to a real file
        except OSError:
            pass
    assert _race(storage, outside, swap) == 0


def test_a_DIRECTORY_swapped_for_a_link_mid_walk_never_reaches_outside(tmp_path, monkeypatch):
    """Final-component no-follow is not enough: a swapped PARENT dir makes
    storage/cache/<name> resolve to outside/<name>."""
    storage, outside = _owned_tree(tmp_path, monkeypatch)
    (outside / "victim").write_bytes(b"v")
    cache = storage / "cache"
    (cache / "victim").write_bytes(b"c")
    os.chmod(cache / "victim", 0o644)
    real = storage / ".cache_real"
    link = storage / ".cache_link"
    os.symlink(outside, link)

    def swap():
        try:
            os.replace(cache, real)
            os.replace(link, cache)            # cache -> symlink to outside
            os.replace(cache, link)
            os.replace(real, cache)            # back to the real dir
        except OSError:
            pass
    assert _race(storage, outside, swap) == 0


def test_a_symlinked_storage_root_is_refused(tmp_path, monkeypatch, caplog):
    storage, outside = _owned_tree(tmp_path, monkeypatch)
    victim_dir = outside / "victim_dir"
    victim_dir.mkdir()
    os.chmod(victim_dir, 0o700)
    inner = victim_dir / "inner"
    inner.write_bytes(b"x")
    os.chmod(inner, 0o666)
    real = storage.parent / "storage.real"
    os.replace(storage, real)
    os.symlink(victim_dir, storage)
    with caplog.at_level("WARNING"):
        ReticulumPaths._fix_storage_file_permissions()
    assert _mode(victim_dir) == 0o700 and _mode(inner) == 0o666
    assert any("symlink" in r.getMessage() for r in caplog.records), caplog.text


def test_a_symlinked_key_names_no_owner(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    target = tmp_path / "elsewhere"
    target.write_bytes(b"k" * 64)
    os.symlink(target, storage / "transport_identity")
    monkeypatch.setattr(ReticulumPaths, "ETC_STORAGE", storage)
    assert ReticulumPaths._rnsd_storage_owner() is None


def test_root_never_chowns_through_a_link_or_a_hardlink(tmp_path, monkeypatch):
    """As root, ownership changes must land only on regular single-link files
    and dirs inside the tree. A hardlink is another name for an OUTSIDE file."""
    storage, outside = _owned_tree(tmp_path, monkeypatch, as_root=True,
                                   owner=(4242, 4242))
    victim = outside / "victim"
    victim.write_bytes(b"v")
    os.symlink(victim, storage / "cache" / "link")
    os.link(victim, storage / "cache" / "hard")
    victim_ino = victim.stat().st_ino
    touched = []

    def rec_path(p, u, g, *a, **k):
        touched.append(os.stat(p, follow_symlinks=k.get("follow_symlinks", True)).st_ino)

    def rec_fd(fd, u, g):
        touched.append(os.fstat(fd).st_ino)
    monkeypatch.setattr(os, "chown", rec_path)
    monkeypatch.setattr(os, "lchown", lambda p, u, g: touched.append(os.lstat(p).st_ino))
    monkeypatch.setattr(os, "fchown", rec_fd)
    ReticulumPaths._fix_storage_file_permissions()
    assert victim_ino not in touched
    assert touched, "nothing was handed over at all — the test proves nothing"


def test_a_root_owned_key_never_reopens_the_widening_path(tmp_path, monkeypatch):
    """W3: owner unknown BECAUSE the key is root-owned (one ``sudo cp``) used
    to widen the whole tree to 0777/0666. Only an ABSENT key (fresh box) may."""
    storage, _ = _owned_tree(tmp_path, monkeypatch, owner=None)
    table = storage / "destination_table"
    table.write_bytes(b"t")
    os.chmod(table, 0o644)
    os.chmod(storage, 0o755)
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(table) == 0o644, oct(_mode(table))
    assert _mode(storage) == 0o755, oct(_mode(storage))


def test_the_config_dir_tail_never_follows_a_planted_link(tmp_path, monkeypatch):
    """W6 (found while building this fix): /etc/reticulum is root:<rnsd user>
    1775 and ``identity`` is usually ABSENT, so rnsd's user can plant
    ``identity -> /etc/shadow``; the tail's chmod(+S_IROTH) made the target
    world-readable on the next sudo TUI start."""
    storage, outside = _owned_tree(tmp_path, monkeypatch)
    secret = outside / "shadow"
    secret.write_bytes(b"s")
    os.chmod(secret, 0o600)
    os.symlink(secret, storage.parent / "identity")
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(secret) == 0o600, oct(_mode(secret))
