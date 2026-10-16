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


# ── Second non-author read of the symlink-safe walk (2026-10-15) ─────────────
# W7: only chown was gated on nlink; a hardlink to an OUTSIDE inode the
# attacker holds r+w on (protected_hardlinks=1 allows that) still had its MODE
# changed — widened to 0666 by the legacy branch (re-armed by deleting the key
# rnsd's user owns), and +o+r by the identity tail.
# W8 (operator chose "never hand over", 2026-10-15): a root-owned file that is
# NOT world-readable, moved into the tree by rnsd's user (32 root 0600 fleet
# backups sit in that user's home), was fchowned to them on the next sudo TUI
# start. Such a file is left exactly as it is, and a WARNING names it — so a
# lock-out it might cause is on the record, not found outside the app.
# W9: a subtree the walk cannot open (fd limit, unreadable dir) was skipped
# silently.


def _fake_root_owned(monkeypatch, paths):
    """Make os.fstat report uid 0 for these inodes — the walk's only source of
    ownership; a real root-owned file needs root to create."""
    inos = {os.stat(p).st_ino for p in paths}
    real = os.fstat

    def fstat(fd):
        st = real(fd)
        if st.st_ino in inos:
            vals = list(st[:10])
            vals[4] = 0
            return os.stat_result(vals)
        return st
    monkeypatch.setattr(os, "fstat", fstat)


def test_a_hardlinked_outside_file_is_never_widened_by_the_legacy_branch(tmp_path, monkeypatch):
    storage, outside = _owned_tree(tmp_path, monkeypatch, owner=None)
    (storage / "transport_identity").unlink()        # key absent -> legacy
    shared = outside / "root_grp_0660"
    shared.write_bytes(b"s")
    os.chmod(shared, 0o660)
    os.link(shared, storage / "cache" / "hard")
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(shared) == 0o660, oct(_mode(shared))


def test_a_hardlinked_outside_file_is_never_narrowed_either(tmp_path, monkeypatch):
    storage, outside = _owned_tree(tmp_path, monkeypatch)
    shared = outside / "grp_0660"
    shared.write_bytes(b"s")
    os.chmod(shared, 0o660)
    os.link(shared, storage / "cache" / "hard")
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(shared) == 0o660, oct(_mode(shared))


def test_the_tail_never_changes_a_hardlink_planted_as_identity(tmp_path, monkeypatch):
    storage, outside = _owned_tree(tmp_path, monkeypatch)
    shared = outside / "grp_0660"
    shared.write_bytes(b"s")
    os.chmod(shared, 0o660)
    os.link(shared, storage.parent / "identity")
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(shared) == 0o660, oct(_mode(shared))


def test_a_root_private_file_is_never_handed_over_and_is_named(tmp_path, monkeypatch, caplog):
    storage, _ = _owned_tree(tmp_path, monkeypatch, as_root=True, owner=(4242, 4242))
    private = storage / "cache" / "backup.tar.gz"
    private.write_bytes(b"secret")
    os.chmod(private, 0o600)
    shared = storage / "cache" / "announce"
    shared.write_bytes(b"a")
    os.chmod(shared, 0o644)
    _fake_root_owned(monkeypatch, [private, shared])
    handed = []
    real_fstat = os.fstat
    monkeypatch.setattr(os, "fchown", lambda fd, u, g: handed.append(real_fstat(fd).st_ino))
    with caplog.at_level("WARNING"):
        ReticulumPaths._fix_storage_file_permissions()
    assert private.stat().st_ino not in handed
    assert shared.stat().st_ino in handed, "a root-made SHARED cache file must still be handed over"
    assert "backup.tar.gz" in caplog.text, caplog.text


def test_a_root_private_file_is_never_widened_by_the_legacy_branch(tmp_path, monkeypatch):
    """The same file via the legacy branch (key deleted): 0600 -> 0666 would
    make a root secret world-readable."""
    storage, _ = _owned_tree(tmp_path, monkeypatch, owner=None)
    (storage / "transport_identity").unlink()
    private = storage / "cache" / "backup.tar.gz"
    private.write_bytes(b"secret")
    os.chmod(private, 0o600)
    _fake_root_owned(monkeypatch, [private])
    ReticulumPaths._fix_storage_file_permissions()
    assert _mode(private) == 0o600, oct(_mode(private))


def test_a_subtree_the_walk_cannot_open_is_named(tmp_path, monkeypatch, caplog):
    if os.geteuid() == 0:
        import pytest
        pytest.skip("root opens a 000 dir")
    storage, _ = _owned_tree(tmp_path, monkeypatch)
    sealed = storage / "cache" / "sealed"
    sealed.mkdir()
    os.chmod(sealed, 0)
    try:
        with caplog.at_level("WARNING"):
            ReticulumPaths._fix_storage_file_permissions()
    finally:
        os.chmod(sealed, 0o755)
    assert "could not" in caplog.text.lower(), caplog.text


# ── Third non-author read (2026-10-15): the follow-up's own gaps ─────────────

def test_a_root_private_dir_is_left_alone_INCLUDING_its_children(tmp_path, monkeypatch):
    """'Left exactly as it is' covers the SUBTREE: a root 0700 dir of root 0644
    files is one root-made thing (sudo cp -r). Mutant M2 (dir check deleted)
    survived the previous suite."""
    storage, _ = _owned_tree(tmp_path, monkeypatch, as_root=True, owner=(4242, 4242))
    d = storage / "cache" / "rootdir"
    d.mkdir()
    os.chmod(d, 0o700)
    child = d / "shared_0644"
    child.write_bytes(b"c")
    os.chmod(child, 0o644)
    # Fourth read, mutant survived: the direct child is protected by the
    # dir-level `continue` alone; only a NESTED entry pins the prune.
    (d / "nested").mkdir()
    deep = d / "nested" / "deep_0666"
    deep.write_bytes(b"d")
    os.chmod(deep, 0o666)
    _fake_root_owned(monkeypatch, [d, child])
    handed = []
    real_fstat = os.fstat
    monkeypatch.setattr(os, "fchown", lambda fd, u, g: handed.append(real_fstat(fd).st_ino))
    ReticulumPaths._fix_storage_file_permissions()
    assert child.stat().st_ino not in handed, "child of a root-private dir was handed over"
    assert d.stat().st_ino not in handed
    assert _mode(deep) == 0o666, "the walk descended into a root-private dir"


def test_a_walk_that_dies_at_the_fd_limit_is_named_not_silent(tmp_path, monkeypatch, caplog):
    """EMFILE surfaces in fwalk's scandir(topfd), OUTSIDE its onerror — the
    generator died into the walk's `except OSError: pass`, naming nothing.
    The only W9 failure ROOT can actually hit (root opens a 000 dir)."""
    import resource
    storage, _ = _owned_tree(tmp_path, monkeypatch)
    d = storage
    for i in range(60):
        d = d / f"L{i}"
        d.mkdir()
        f = d / "f"
        f.write_bytes(b"x")
        os.chmod(f, 0o666)
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (40, hard))
    try:
        with caplog.at_level("WARNING"):
            ReticulumPaths._fix_storage_file_permissions()
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    deepest = storage.joinpath(*[f"L{i}" for i in range(60)]) / "f"
    unchecked = _mode(deepest) == 0o666
    assert not unchecked or "could not" in caplog.text.lower(), (
        f"deep file left 0o666 and nothing named it; log={caplog.text!r}")


def test_an_unopened_subtree_is_named_by_its_FULL_path(tmp_path, monkeypatch, caplog):
    if os.geteuid() == 0:
        import pytest
        pytest.skip("root opens a 000 dir")
    storage, _ = _owned_tree(tmp_path, monkeypatch)
    sealed = storage / "cache" / "deep" / "sealed"
    sealed.mkdir(parents=True)
    os.chmod(sealed, 0)
    try:
        with caplog.at_level("WARNING"):
            ReticulumPaths._fix_storage_file_permissions()
    finally:
        os.chmod(sealed, 0o755)
    assert os.path.join("cache", "deep", "sealed") in caplog.text, caplog.text


def test_the_walk_returns_what_it_left_alone(tmp_path, monkeypatch):
    """The TUI logs to FILE only (setup_logging log_to_console=False), so a
    WARNING never reaches the operator's screen; callers print the summary."""
    storage, _ = _owned_tree(tmp_path, monkeypatch, as_root=True, owner=(4242, 4242))
    private = storage / "cache" / "backup.tar.gz"
    private.write_bytes(b"s")
    os.chmod(private, 0o600)
    _fake_root_owned(monkeypatch, [private])
    monkeypatch.setattr(os, "fchown", lambda *a: None)
    summary = ReticulumPaths._fix_storage_file_permissions()
    assert any("backup.tar.gz" in p for p in summary["left_alone"]), summary
    note = ReticulumPaths.storage_walk_note(summary)
    assert note and "backup.tar.gz" in note, note
    assert ReticulumPaths.storage_walk_note({"left_alone": [], "unopened": []}) is None


def test_no_tui_handler_chmods_storage_world_writable():
    """W10: nomadnet.py re-set storage/ 0777 whenever it was NOT world-writable
    — i.e. on every sudo NomadNet launch after the B2 roll — and as root that
    chmod followed a symlinked storage/. The walk owns storage's modes now."""
    import re
    handlers = Path(__file__).resolve().parent.parent / "src" / "launcher_tui" / "handlers"
    hits = []
    for f in handlers.glob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"storage\w*\.(chmod\(0o777|mkdir\(mode=0o777)", code):
                hits.append(f"{f.name}:{n}")
            # ...nor tell the operator to do it by hand (fourth read: MA's
            # failure dialog still said `sudo chmod 777 /etc/reticulum/storage`).
            if re.search(r"chmod\s+0?777\s+\S*reticulum/storage", line):
                hits.append(f"{f.name}:{n} (cure text)")
    assert hits == [], hits


def test_ensure_system_dirs_refuses_a_symlinked_storage(tmp_path, monkeypatch):
    """Fourth read F4: as root (the repair path) the mkdirs followed a
    symlinked storage/ and created ratchets/, cache/ ... inside its target."""
    base = tmp_path / "etc"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, base / "storage")
    for name, rel in (("ETC_BASE", ""), ("ETC_STORAGE", "storage"),
                      ("ETC_RATCHETS", "storage/ratchets"),
                      ("ETC_RESOURCES", "storage/resources"),
                      ("ETC_CACHE", "storage/cache"),
                      ("ETC_ANNOUNCE_CACHE", "storage/cache/announces"),
                      ("ETC_DISCOVERY", "storage/discovery"),
                      ("ETC_INTERFACES", "interfaces")):
        monkeypatch.setattr(ReticulumPaths, name, base / rel if rel else base)
    assert ReticulumPaths.ensure_system_dirs() is False
    assert list(outside.iterdir()) == [], list(outside.iterdir())
