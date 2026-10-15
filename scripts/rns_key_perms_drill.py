"""Non-author adversarial review (2026-10-15) of b8d5a8d4 + db068468 — rnsd's
private key (storage/transport_identity) and the permission foundation. Each
test PASSES when the attack SUCCEEDS: the key ends up, or stays, exposed and
nothing says so.

Real functions on a real tmp tree; nothing here touches /etc/reticulum.
"""

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.paths import ReticulumPaths
from utils.rns_tree_perms import build_logfile_perms_script
from commands.rns import identity_exposure


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_attack_storage_sweep_opens_owner_only_key(tmp_path):
    """ATTACK B1: ReticulumPaths._fix_storage_file_permissions() — run by
    ensure_system_dirs() from the gateway's start, TUI startup checks, RNS
    diagnostics and the RNS repair handler — chmods EVERY file under storage/
    to 0666, the private key included. Its own docstring believes the secrets
    live in the parent dir. The 600 the foundation sets is undone by the next
    call."""
    storage = tmp_path / "storage"
    storage.mkdir()
    key = storage / "transport_identity"
    key.write_bytes(b"\x00" * 64)
    key.chmod(0o600)
    assert _mode(key) == 0o600

    with patch.object(ReticulumPaths, "ETC_STORAGE", storage):
        ReticulumPaths._fix_storage_file_permissions()

    assert _mode(key) == 0o666          # private key now world-readable AND writable
    assert _mode(storage) == 0o777      # and anyone may replace it


def test_attack_foundation_leaves_storage_dir_world_writable(tmp_path):
    """ATTACK B2: build_logfile_perms_script() chmods the key 600 but never
    touches storage/'s own mode. With storage/ at 0777 (what paths.py creates),
    any local user can rename the 600 key away and drop in their own: the key's
    mode protects its BYTES, the directory protects its IDENTITY. Run against a
    tmp tree (CD and the root-chown redirected — the only edits)."""
    script = build_logfile_perms_script("nobodyuser")
    assert 'chmod 600 "$CD/storage/transport_identity"' in script
    assert 'chmod' not in [l.strip().split()[0] for l in script.splitlines()
                           if '"$CD/storage"' in l and 'chmod' in l], \
        "script would have fixed the storage dir mode"

    cd = tmp_path / "reticulum"
    (cd / "storage").mkdir(parents=True)
    key = cd / "storage" / "transport_identity"
    key.write_bytes(b"\x00" * 64)
    key.chmod(0o666)
    (cd / "storage").chmod(0o777)
    me = os.getlogin() if hasattr(os, "getlogin") else "root"
    import pwd
    me = pwd.getpwuid(os.getuid()).pw_name
    runnable = (build_logfile_perms_script(me)
                .replace("CD=/etc/reticulum", f"CD={cd}")
                .replace(f"chown root:{me}", f"chown {me}:{me}"))
    import subprocess
    r = subprocess.run(["bash", "-c", runnable], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr

    assert _mode(key) == 0o600                      # the fix the commit claims
    assert _mode(cd / "storage") == 0o777           # the hole it leaves
    assert os.access(cd / "storage", os.W_OK)


def test_attack_exposure_warning_blind_to_writable_parent_dir(tmp_path):
    """ATTACK B3: identity_exposure() reads only the key file's mode bits. A
    600 key inside a 0777 storage/ (every box, by paths.py) is replaceable by
    any local user, and the Diagnostics screen says nothing."""
    storage = tmp_path / "storage"
    storage.mkdir()
    storage.chmod(0o777)
    key = storage / "transport_identity"
    key.write_bytes(b"\x00" * 64)
    key.chmod(0o600)

    assert identity_exposure(key) == ""


def test_attack_exposure_warning_blind_to_owner(tmp_path):
    """ATTACK B4: the warning text promises 'owned by the rnsd user' but no
    owner is ever compared — a 600 key owned by the WRONG user reads clean.
    (Measured 2026-09-25: 3 of the 666 keys were root-owned under a non-root
    rnsd.) Only the mode-bit half can be exercised without root; the function
    has no owner argument at all."""
    import inspect
    from commands import rns as rns_cmds
    src = inspect.getsource(rns_cmds.identity_exposure)
    assert "st_uid" not in src and "getpwuid" not in src and "getpwnam" not in src


def test_attack_rnsd_births_key_world_readable(tmp_path):
    """ATTACK B5: 'born correct' is false for a FRESH box. rnsd creates the key
    itself on first start via RNS.Identity.to_file(): a plain open(path,'wb'),
    no chmod. Under rnsd.service's UMask=0022 (read live on this box) the key
    is born 0644 — world-readable until the foundation script is re-run."""
    RNS = pytest.importorskip("RNS")
    key = tmp_path / "transport_identity"
    old = os.umask(0o022)
    try:
        assert RNS.Identity().to_file(str(key)) is True
    finally:
        os.umask(old)
    assert _mode(key) == 0o644
    assert _mode(key) & stat.S_IROTH
