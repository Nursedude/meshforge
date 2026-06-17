"""Regression guard for the rnsd dual-install silent-failure (Issue #24 /
TUI audit #15, 2026-05-29).

`_pip_install_meshtastic` runs a second, system-wide `sudo pip3 install` so
rnsd's Python can find `meshtastic`. `subprocess.run(capture_output=True)`
does NOT raise on a nonzero exit, so a failed rnsd install used to pass
silently and the caller reported full success — defeating Issue #24. The fix
keeps the user-level success (returns True) but surfaces the rnsd-copy failure
via an in-app msgbox.
"""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from handlers.updates import UpdatesHandler

RNSD_IFACE = "/etc/reticulum/interfaces/Meshtastic_Interface.py"


def _path_exists(rnsd_present):
    """Path-aware fake: rnsd interface presence is the only thing that varies;
    force the non-venv pip branch so subprocess args are deterministic."""
    def fake(self):
        s = str(self)
        if s == RNSD_IFACE:
            return rnsd_present
        return False  # venv_pip / .no-venv absent -> pip3 branch
    return fake


def _result(returncode, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _handler():
    h = UpdatesHandler()
    h.ctx = MagicMock()
    return h


def _msgbox_titles(dialog):
    return [c.args[0] for c in dialog.msgbox.call_args_list if c.args]


class TestRnsdDualInstallSurfacing:
    def test_rnsd_install_failure_is_surfaced_but_user_install_succeeds(self):
        h = _handler()
        runs = [_result(0, stdout="user ok"), _result(1, stderr="permission denied")]
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.subprocess.run", side_effect=runs):
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True  # user-level install genuinely worked
        assert "rnsd Install Incomplete" in _msgbox_titles(h.ctx.dialog)

    def test_rnsd_install_success_shows_no_warning(self):
        h = _handler()
        runs = [_result(0, stdout="user ok"), _result(0, stdout="rnsd ok")]
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.subprocess.run", side_effect=runs):
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True
        assert "rnsd Install Incomplete" not in _msgbox_titles(h.ctx.dialog)

    def test_no_rnsd_interface_means_no_second_install(self):
        h = _handler()
        run = MagicMock(side_effect=[_result(0, stdout="user ok")])
        with patch.object(pathlib.Path, "exists", _path_exists(False)), \
                patch("handlers.updates.subprocess.run", run):
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True
        assert run.call_count == 1  # only the user-level install ran
        assert "rnsd Install Incomplete" not in _msgbox_titles(h.ctx.dialog)

    def test_user_level_failure_returns_false_without_rnsd_attempt(self):
        h = _handler()
        run = MagicMock(side_effect=[_result(1, stderr="boom")])
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.subprocess.run", run):
            success, msg = h._pip_install_meshtastic(upgrade=True)
        assert success is False
        assert "boom" in msg
        assert run.call_count == 1  # bailed before the rnsd dual-install


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestUpdateAllRoutesMeshtasticLib:
    """Update All must install the meshtastic library through the pip helper,
    not the raw update_command — the raw string lacks --break-system-packages
    (PEP 668 / externally-managed-environment) and the rnsd dual-install (#24).
    Surfaced by a live walk: "Update All" reported the library FAILED with
    'externally-managed-environment' while the standalone updater worked.
    """

    def test_lib_uses_pip_helper_others_use_run_command(self):
        from types import SimpleNamespace
        h = _handler()  # ctx is a MagicMock → dialog.yesno() is truthy
        versions = {
            'meshtastic_lib': SimpleNamespace(
                name='Meshtastic Library', update_available=True,
                update_command='pip3 install --break-system-packages --upgrade meshtastic'),
            'meshtasticd': SimpleNamespace(
                name='meshtasticd', update_available=True,
                update_command='sudo apt-get install --only-upgrade -y meshtasticd'),
        }
        h._pip_install_meshtastic = MagicMock(return_value=(True, 'lib ok'))
        h._run_update_command = MagicMock(return_value=(True, 'apt ok'))
        with patch('handlers.updates._check_all_versions', return_value=versions):
            h._update_all()

        # The library is routed through the helper (PEP 668 + #24 handled)...
        h._pip_install_meshtastic.assert_called_once_with(upgrade=True)
        # ...and NOT through the raw-command path; other components still are.
        routed = [c.args[0] for c in h._run_update_command.call_args_list]
        assert 'meshtasticd' in routed
        assert 'meshtastic_lib' not in routed

    def test_cli_uses_pipx_helper_not_raw_command(self):
        """The CLI must upgrade through `_pipx_upgrade_cli` (which runs pipx in
        the pipx that OWNS the resolved binary), not the raw `pipx upgrade`
        string — under sudo the raw command hits root's pipx, not the
        operator's, so the upgrade no-ops and the flag never clears (the
        read/write split, feedback_version_env_rigor)."""
        from types import SimpleNamespace
        h = _handler()
        versions = {
            'cli': SimpleNamespace(
                name='Meshtastic CLI', update_available=True,
                update_command='pipx upgrade meshtastic'),
            'meshtasticd': SimpleNamespace(
                name='meshtasticd', update_available=True,
                update_command='sudo apt-get install --only-upgrade -y meshtasticd'),
        }
        h._pipx_upgrade_cli = MagicMock(return_value=(True, 'cli ok'))
        h._run_update_command = MagicMock(return_value=(True, 'apt ok'))
        with patch('handlers.updates._check_all_versions', return_value=versions):
            h._update_all()

        h._pipx_upgrade_cli.assert_called_once_with()
        routed = [c.args[0] for c in h._run_update_command.call_args_list]
        assert 'cli' not in routed
        assert 'meshtasticd' in routed


class TestPipxUpgradeCliTargetsOwner:
    """`_pipx_upgrade_cli` must run pipx as the OWNER of the resolved CLI binary
    so the upgrade lands in the pipx home the reader reads from."""

    def test_drops_to_owner_when_owner_differs_from_euid(self):
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli',
                   return_value='/home/op/.local/bin/meshtastic'), \
                patch('os.stat', return_value=MagicMock(st_uid=1000)), \
                patch('pwd.getpwuid', return_value=MagicMock(pw_name='op')), \
                patch('os.geteuid', return_value=0), \
                patch('handlers.updates.subprocess.run',
                      return_value=_result(0, stdout='ok')) as run:
            success, _msg = h._pipx_upgrade_cli()
        assert success is True
        cmd = run.call_args.args[0]
        assert cmd[:4] == ['sudo', '-u', 'op', '-H']
        assert cmd[-3:] == ['pipx', 'upgrade', 'meshtastic']

    def test_plain_pipx_when_owner_is_current_user(self):
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli',
                   return_value='/usr/local/bin/meshtastic'), \
                patch('os.stat', return_value=MagicMock(st_uid=0)), \
                patch('pwd.getpwuid', return_value=MagicMock(pw_name='root')), \
                patch('os.geteuid', return_value=0), \
                patch('handlers.updates.subprocess.run',
                      return_value=_result(0)) as run:
            success, _msg = h._pipx_upgrade_cli()
        assert success is True
        cmd = run.call_args.args[0]
        assert cmd == ['pipx', 'upgrade', 'meshtastic']
        assert 'sudo' not in cmd

    def test_no_cli_found_returns_false(self):
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli', return_value=None):
            success, msg = h._pipx_upgrade_cli()
        assert success is False
        assert 'not found' in msg.lower()


class TestLibVersionReadsWriteTarget:
    """`get_meshtastic_lib_version` must read from the venv python the updater
    writes to — not the checker's own interpreter — so read == write target."""

    def test_reads_from_venv_python_when_present(self):
        import updates.version_checker as vc
        with patch.object(vc, 'get_meshforge_venv_dir',
                          return_value=pathlib.Path('/opt/meshforge/venv')), \
                patch('updates.version_checker.subprocess.run',
                      return_value=_result(0, stdout='2.7.9\n')) as run:
            v = vc.get_meshtastic_lib_version()
        assert v == '2.7.9'
        cmd = run.call_args.args[0]
        assert cmd[0] == '/opt/meshforge/venv/bin/python'
        assert '-c' in cmd

    def test_venv_present_but_meshtastic_missing_returns_none(self):
        import updates.version_checker as vc
        with patch.object(vc, 'get_meshforge_venv_dir',
                          return_value=pathlib.Path('/opt/meshforge/venv')), \
                patch('updates.version_checker.subprocess.run',
                      return_value=_result(1, stderr='PackageNotFoundError')):
            assert vc.get_meshtastic_lib_version() is None

    def test_no_venv_reads_in_process(self):
        import updates.version_checker as vc
        with patch.object(vc, 'get_meshforge_venv_dir', return_value=None), \
                patch('importlib.metadata.version', return_value='2.7.9'):
            assert vc.get_meshtastic_lib_version() == '2.7.9'


class TestVenvDirGate:
    """`get_meshforge_venv_dir` is the SSOT venv gate shared by reader+writer."""

    def test_none_when_no_venv_marker_present(self):
        import updates.version_checker as vc

        def fake_exists(self):
            s = str(self)
            if s.endswith('.no-venv'):
                return True            # opt-out marker present
            if s.endswith('venv/bin/python'):
                return True
            return False
        with patch.object(pathlib.Path, 'exists', fake_exists):
            assert vc.get_meshforge_venv_dir() is None

    def test_returns_dir_when_python_present_and_no_marker(self):
        import updates.version_checker as vc

        def fake_exists(self):
            s = str(self)
            if s.endswith('.no-venv'):
                return False
            if s.endswith('venv/bin/python'):
                return True
            return False
        with patch.object(pathlib.Path, 'exists', fake_exists):
            d = vc.get_meshforge_venv_dir()
        assert d is not None
        assert str(d).endswith('venv')


class TestApplyFleetFloor:
    """`_apply_fleet_floor` gates update_available on the REVIEWED fleet floor
    (requirements/core.txt), NOT raw PyPI-latest — the 2026-06-17 phantom-update
    fix (feedback_version_env_rigor)."""

    def _info(self, installed):
        import updates.version_checker as vc
        return vc.VersionInfo(name='Meshtastic Library', installed=installed)

    def test_below_floor_flags_update(self):
        import updates.version_checker as vc
        info = self._info('2.7.8')
        with patch.object(vc, 'read_floor', return_value='2.7.9'):
            vc._apply_fleet_floor(info, pypi_latest='2.7.10')
        assert info.update_available is True
        assert info.fleet_floor == '2.7.9'
        assert info.latest == '2.7.9'          # target shown = the floor
        assert info.pypi_latest == '2.7.10'    # informational only

    def test_at_floor_is_not_a_phantom_update(self):
        # The exact 2026-06-17 case: installed == fleet floor, PyPI moved ahead.
        # Must NOT report an update (the phantom the operator saw).
        import updates.version_checker as vc
        info = self._info('2.7.9')
        with patch.object(vc, 'read_floor', return_value='2.7.9'):
            vc._apply_fleet_floor(info, pypi_latest='2.7.10')
        assert info.update_available is False
        assert info.fleet_floor == '2.7.9'
        assert info.pypi_latest == '2.7.10'

    def test_above_floor_no_update(self):
        import updates.version_checker as vc
        info = self._info('2.7.10')
        with patch.object(vc, 'read_floor', return_value='2.7.9'):
            vc._apply_fleet_floor(info, pypi_latest='2.7.10')
        assert info.update_available is False

    def test_unreadable_floor_refuses_to_guess(self):
        # No floor → neither phantom-update NOR silently-healthy: update_available
        # False but the blindness is surfaced in error (honest_failure_modes).
        import updates.version_checker as vc
        info = self._info('2.7.8')
        with patch.object(vc, 'read_floor', return_value=None):
            vc._apply_fleet_floor(info, pypi_latest='2.7.10')
        assert info.update_available is False
        assert info.fleet_floor is None
        assert 'baseline' in (info.error or '').lower()

    def test_no_installed_version_no_update(self):
        import updates.version_checker as vc
        info = self._info(None)
        with patch.object(vc, 'read_floor', return_value='2.7.9'):
            vc._apply_fleet_floor(info, pypi_latest='2.7.10')
        assert info.update_available is False


class TestCheckAllVersionsFleetFloor:
    """End-to-end: a box AT the fleet floor with PyPI ahead reports ZERO
    meshtastic updates (cli + lib) — the regression guard for the operator's
    2026-06-17 '2 updates available' phantom."""

    def _patch_env(self, vc, *, installed, floor, pypi):
        # Isolate from the network + other components: only the two meshtastic
        # pip components matter here; null everything else.
        return [
            patch.object(vc, 'get_meshtastic_cli_version', return_value=installed),
            patch.object(vc, 'get_meshtastic_lib_version', return_value=installed),
            patch.object(vc, 'get_latest_meshtastic_cli_version', return_value=pypi),
            patch.object(vc, 'read_floor', return_value=floor),
            patch.object(vc, 'get_meshforge_version', return_value=None),
            patch.object(vc, 'get_latest_meshforge_version', return_value=None),
            patch.object(vc, 'get_meshtasticd_version', return_value=None),
            patch.object(vc, 'get_latest_meshtasticd_version', return_value=None),
            patch.object(vc, 'get_node_firmware_version', return_value=None),
            patch.object(vc, 'get_latest_firmware_version', return_value=None),
        ]

    def test_at_floor_pypi_ahead_no_phantom_updates(self):
        import contextlib
        import updates.version_checker as vc
        with contextlib.ExitStack() as stack:
            for p in self._patch_env(vc, installed='2.7.9', floor='2.7.9', pypi='2.7.10'):
                stack.enter_context(p)
            results = vc.check_all_versions()
        assert results['cli'].update_available is False
        assert results['meshtastic_lib'].update_available is False
        assert results['cli'].fleet_floor == '2.7.9'
        assert results['meshtastic_lib'].fleet_floor == '2.7.9'

    def test_below_floor_flags_both_components(self):
        import contextlib
        import updates.version_checker as vc
        with contextlib.ExitStack() as stack:
            for p in self._patch_env(vc, installed='2.7.8', floor='2.7.9', pypi='2.7.10'):
                stack.enter_context(p)
            results = vc.check_all_versions()
        assert results['cli'].update_available is True
        assert results['meshtastic_lib'].update_available is True

    def test_summary_exposes_floor_fields(self):
        import contextlib
        import updates.version_checker as vc
        with contextlib.ExitStack() as stack:
            for p in self._patch_env(vc, installed='2.7.9', floor='2.7.9', pypi='2.7.10'):
                stack.enter_context(p)
            summary = vc.get_version_summary()
        lib = next(c for c in summary['components'] if c['id'] == 'meshtastic_lib')
        assert lib['fleet_floor'] == '2.7.9'
        assert lib['pypi_latest'] == '2.7.10'
        assert lib['update_available'] is False
