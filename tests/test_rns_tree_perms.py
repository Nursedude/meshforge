"""Tests for the shared RNS-tree-perms SSOT (utils.rns_tree_perms).

This module is carried byte-identical in MeshForge and MeshAnchor and gated by
scripts/parity_check.py; this test file ports alongside it so each repo proves
the shared core directly (the drift logic is also exercised via the
rns_alignment adapter in test_rns_alignment.py).
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.rns_tree_perms import (  # noqa: E402
    CANONICAL_CONFIGDIR,
    RnsTreePerms,
    _group_writable,
    _parse_user_from_unit_files,
    apply_logfile_perms,
    build_logfile_perms_script,
    logfile_perms_drift,
)


def _perms(configdir_owner='root:wh6gxz', configdir_mode='1775',
           logfile_owner='wh6gxz:wh6gxz', logfile_exists=True,
           rnsd_user='wh6gxz') -> RnsTreePerms:
    """Aligned non-root rnsd perms with the canonical layout by default."""
    return RnsTreePerms(
        rnsd_user=rnsd_user,
        configdir_owner=configdir_owner,
        configdir_mode=configdir_mode,
        logfile_exists=logfile_exists,
        logfile_owner=logfile_owner,
    )


class TestGroupWritable:
    def test_sticky_group_writable(self):
        assert _group_writable('1775') is True

    def test_not_group_writable(self):
        assert _group_writable('755') is False

    def test_plain_group_writable(self):
        assert _group_writable('775') is True

    def test_none_and_garbage(self):
        assert _group_writable(None) is False
        assert _group_writable('xyz') is False


class TestLogfilePermsDrift:
    def test_canonical_nonroot_perms_no_drift(self):
        assert logfile_perms_drift(_perms()) is None

    def test_configdir_root_owned_flagged(self):
        # moc1/moc2 recurrence shape: re-provision left it root:root 755.
        reason = logfile_perms_drift(_perms(
            configdir_owner='root:root', configdir_mode='755',
            logfile_owner='root:root'))
        assert reason is not None
        assert 'cannot' in reason and 'wh6gxz' in reason

    def test_configdir_group_ok_but_not_group_writable_flagged(self):
        assert logfile_perms_drift(_perms(configdir_mode='755')) is not None

    def test_logfile_root_owned_with_good_dir_flagged(self):
        reason = logfile_perms_drift(_perms(logfile_owner='root:root'))
        assert reason is not None and 'logfile is owned by' in reason

    def test_logfile_absent_with_good_dir_no_drift(self):
        assert logfile_perms_drift(
            _perms(logfile_owner=None, logfile_exists=False)) is None

    def test_root_rnsd_never_flagged(self):
        assert logfile_perms_drift(_perms(
            configdir_owner='root:root', configdir_mode='755',
            logfile_owner='root:root', rnsd_user='root')) is None

    def test_unprobed_perms_never_flagged(self):
        assert logfile_perms_drift(_perms(
            configdir_owner=None, configdir_mode=None,
            logfile_owner=None, logfile_exists=False)) is None

    def test_weird_username_not_flagged(self):
        assert logfile_perms_drift(_perms(
            configdir_owner='root:root', configdir_mode='755',
            rnsd_user='bad user;rm')) is None


class TestBuildScript:
    def test_sets_canonical_layout(self):
        script = build_logfile_perms_script('wh6gxz')
        assert 'chown root:wh6gxz' in script
        assert 'chmod 1775' in script
        assert 'chown wh6gxz:wh6gxz' in script and 'logfile' in script
        assert 'storage' in script
        assert str(CANONICAL_CONFIGDIR) in script


class TestApplyLogfilePerms:
    def test_rejects_unsafe_username(self):
        with pytest.raises(ValueError):
            apply_logfile_perms("bad user; rm -rf /")

    def test_runs_canonical_chown_chmod(self):
        from utils import rns_tree_perms
        with patch.object(rns_tree_perms.subprocess, "run") as run:
            apply_logfile_perms("wh6gxz")
        run.assert_called_once()
        argv = run.call_args[0][0]
        assert argv[:3] == ["sudo", "bash", "-c"]
        assert "chown root:wh6gxz" in argv[3]


class TestParseUserFromUnitFiles:
    def test_dropin_last_wins(self, tmp_path):
        base = tmp_path / "rnsd.service"
        base.write_text("[Service]\nUser=root\nExecStart=/usr/local/bin/rnsd\n")
        dropin = tmp_path / "user.conf"
        dropin.write_text("[Service]\nUser=wh6gxz\n")
        # Drop-in is listed after the base unit -> last-wins yields the override.
        assert _parse_user_from_unit_files([str(base), str(dropin)]) == "wh6gxz"

    def test_no_user_line_returns_none(self, tmp_path):
        base = tmp_path / "rnsd.service"
        base.write_text("[Service]\nExecStart=/usr/local/bin/rnsd\n")
        assert _parse_user_from_unit_files([str(base)]) is None

    def test_missing_files_skipped(self, tmp_path):
        assert _parse_user_from_unit_files([str(tmp_path / "absent.service")]) is None


class TestRootContextNoSudo:
    """Root callers must stat DIRECTLY — sudo from the watchdog's
    NoNewPrivileges sandbox always fails, which silently mapped every probe
    field to None and left foundation_perms_drift inert (2026-06-09 finding;
    the #79 never-escalate-from-the-sandbox lesson applied to this module)."""

    def _no_subprocess(self, *a, **k):
        raise AssertionError("root context must not spawn sudo/subprocess")

    def test_stat_owner_root_direct_no_sudo(self, tmp_path):
        from utils.rns_tree_perms import _stat_owner
        p = tmp_path / "d"
        p.mkdir()
        with patch("utils.rns_tree_perms.os.geteuid", return_value=0), \
             patch("utils.rns_tree_perms.subprocess.run", self._no_subprocess):
            owner = _stat_owner(p, sudo=True)
        assert owner is not None and ":" in owner

    def test_stat_mode_root_direct_no_sudo(self, tmp_path):
        from utils.rns_tree_perms import _stat_mode
        p = tmp_path / "d"
        p.mkdir(mode=0o755)
        with patch("utils.rns_tree_perms.os.geteuid", return_value=0), \
             patch("utils.rns_tree_perms.subprocess.run", self._no_subprocess):
            mode = _stat_mode(p, sudo=True)
        assert mode == "755"

    def test_path_exists_root_direct_no_sudo(self, tmp_path):
        from utils.rns_tree_perms import _path_exists
        present = tmp_path / "f"
        present.touch()
        with patch("utils.rns_tree_perms.os.geteuid", return_value=0), \
             patch("utils.rns_tree_perms.subprocess.run", self._no_subprocess):
            assert _path_exists(present, sudo=True) is True
            assert _path_exists(tmp_path / "absent", sudo=True) is False

    def test_nonroot_still_escalates_via_sudo(self, tmp_path):
        """The operator-CLI context (non-root, root-owned configdir) keeps the
        sudo fallback — the fix must not regress it."""
        from utils.rns_tree_perms import _stat_owner
        seen = {}

        def fake_run(cmd, **k):
            seen["cmd"] = cmd

            class R:
                returncode = 0
                stdout = "root:root\n"
            return R()

        with patch("utils.rns_tree_perms.os.geteuid", return_value=1000), \
             patch("utils.rns_tree_perms.subprocess.run", fake_run):
            owner = _stat_owner(tmp_path, sudo=True)
        assert owner == "root:root"
        assert seen["cmd"][:2] == ["sudo", "-n"]

    def test_probe_as_root_yields_probed_fields(self, tmp_path):
        """End-to-end: as root (mocked), the probe must return REAL fields for
        an existing tree — the inert-probe regression case."""
        from utils import rns_tree_perms as rtp
        cd = tmp_path / "reticulum"
        cd.mkdir()
        (cd / "logfile").touch()
        with patch.object(rtp, "CANONICAL_CONFIGDIR", cd), \
             patch.object(rtp.os, "geteuid", return_value=0), \
             patch.object(rtp, "_read_rnsd_user", return_value="someuser"), \
             patch.object(rtp.subprocess, "run", self._no_subprocess):
            perms = rtp.probe_rns_tree_perms()
        assert perms.configdir_owner is not None
        assert perms.configdir_mode is not None
        assert perms.logfile_exists is True
        assert perms.logfile_owner is not None
