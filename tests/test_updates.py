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
from utils.pip_install import PipResult

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
    """Issue #24: the rnsd dual-install must surface a failure honestly instead
    of passing as full success. After the install-hardening refactor the method
    routes through `utils.pip_install.pip_install` (which closes both the
    subprocess-doesn't-raise trap AND the installed≠importable trap), so these
    guards now patch the helper rather than raw subprocess.run."""

    def test_rnsd_install_failure_is_surfaced_but_user_install_succeeds(self):
        h = _handler()
        runs = [PipResult(True, stdout="user ok", verified=True),
                PipResult(False, detail="permission denied")]
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.pip_install", side_effect=runs) as pi:
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True  # user-level install genuinely worked
        assert pi.call_count == 2
        assert "rnsd Install Incomplete" in _msgbox_titles(h.ctx.dialog)

    def test_rnsd_install_success_shows_no_warning(self):
        h = _handler()
        runs = [PipResult(True, stdout="user ok", verified=True),
                PipResult(True, stdout="rnsd ok", verified=True)]
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.pip_install", side_effect=runs):
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True
        assert "rnsd Install Incomplete" not in _msgbox_titles(h.ctx.dialog)

    def test_no_rnsd_interface_means_no_second_install(self):
        h = _handler()
        pi = MagicMock(side_effect=[PipResult(True, stdout="user ok", verified=True)])
        with patch.object(pathlib.Path, "exists", _path_exists(False)), \
                patch("handlers.updates.pip_install", pi):
            success, _msg = h._pip_install_meshtastic(upgrade=True)
        assert success is True
        assert pi.call_count == 1  # only the user-level install ran
        assert "rnsd Install Incomplete" not in _msgbox_titles(h.ctx.dialog)

    def test_user_level_failure_returns_false_without_rnsd_attempt(self):
        h = _handler()
        pi = MagicMock(side_effect=[PipResult(False, detail="boom")])
        with patch.object(pathlib.Path, "exists", _path_exists(True)), \
                patch("handlers.updates.pip_install", pi):
            success, msg = h._pip_install_meshtastic(upgrade=True)
        assert success is False
        assert "boom" in msg
        assert pi.call_count == 1  # bailed before the rnsd dual-install

    def test_primary_install_verifies_meshtastic_imports(self):
        """The primary install must request import-as-consumer verification —
        'installed' is not 'importable' (Issue #24 / honest_failure_modes)."""
        h = _handler()
        pi = MagicMock(side_effect=[PipResult(True, stdout="ok", verified=True)])
        with patch.object(pathlib.Path, "exists", _path_exists(False)), \
                patch("handlers.updates.pip_install", pi):
            h._pip_install_meshtastic(upgrade=True)
        # first (primary) call carries verify_import='meshtastic'
        assert pi.call_args_list[0].kwargs.get("verify_import") == "meshtastic"


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
        with patch('handlers.updates._check_all_versions', return_value=versions), \
                patch('handlers.updates.run_meshtasticd_upgrade',
                      return_value=(True, 'apt verified')) as apt_run, \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')):
            h._update_all()

        # The library is routed through the helper (PEP 668 + #24 handled)...
        h._pip_install_meshtastic.assert_called_once_with(upgrade=True)
        # ...meshtasticd through the verified apt runner (2026-07-10 audit)...
        apt_run.assert_called_once_with()
        # ...and neither through the raw-command path.
        routed = [c.args[0] for c in h._run_update_command.call_args_list]
        assert 'meshtasticd' not in routed
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
        with patch('handlers.updates._check_all_versions', return_value=versions), \
                patch('handlers.updates.run_meshtasticd_upgrade',
                      return_value=(True, 'apt verified')) as apt_run, \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')):
            h._update_all()

        h._pipx_upgrade_cli.assert_called_once_with()
        routed = [c.args[0] for c in h._run_update_command.call_args_list]
        assert 'cli' not in routed
        assert 'meshtasticd' not in routed
        apt_run.assert_called_once_with()


class TestPipxUpgradeCliTargetsOwner:
    """`_pipx_upgrade_cli` must run pipx as the OWNER of the resolved CLI
    binary (so the write lands in the pipx home the reader reads from) AND
    target the reviewed fleet floor, never PyPI-latest (2026-07-10 audit:
    `pipx upgrade` overshoots the reviewed pin the moment PyPI moves)."""

    def test_drops_to_owner_when_owner_differs_from_euid(self):
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli',
                   return_value='/home/op/.local/bin/meshtastic'), \
                patch('handlers.updates.read_floor', return_value='2.7.9'), \
                patch('os.stat', return_value=MagicMock(st_uid=1000)), \
                patch('pwd.getpwuid', return_value=MagicMock(pw_name='op')), \
                patch('os.geteuid', return_value=0), \
                patch('handlers.updates.subprocess.run',
                      return_value=_result(0, stdout='ok')) as run:
            success, _msg = h._pipx_upgrade_cli()
        assert success is True
        cmd = run.call_args.args[0]
        assert cmd[:4] == ['sudo', '-u', 'op', '-H']
        assert cmd[-4:] == ['pipx', 'install', '--force', 'meshtastic==2.7.9']

    def test_plain_pipx_when_owner_is_current_user(self):
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli',
                   return_value='/usr/local/bin/meshtastic'), \
                patch('handlers.updates.read_floor', return_value='2.7.9'), \
                patch('os.stat', return_value=MagicMock(st_uid=0)), \
                patch('pwd.getpwuid', return_value=MagicMock(pw_name='root')), \
                patch('os.geteuid', return_value=0), \
                patch('handlers.updates.subprocess.run',
                      return_value=_result(0)) as run:
            success, _msg = h._pipx_upgrade_cli()
        assert success is True
        cmd = run.call_args.args[0]
        assert cmd == ['pipx', 'install', '--force', 'meshtastic==2.7.9']
        assert 'sudo' not in cmd

    def test_no_cli_targets_real_user_fresh_install(self):
        """No resolved binary → install into the OPERATOR's pipx home
        (SUDO_USER under sudo), never root's by accident — the old install
        path ran `pipx install` in the current (root) context."""
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli', return_value=None), \
                patch('handlers.updates.read_floor', return_value='2.7.9'), \
                patch('utils.paths.get_real_username', return_value='op'), \
                patch('pwd.getpwnam', return_value=MagicMock(pw_uid=1000)), \
                patch('os.geteuid', return_value=0), \
                patch('handlers.updates.subprocess.run',
                      return_value=_result(0)) as run:
            success, _msg = h._pipx_upgrade_cli()
        assert success is True
        cmd = run.call_args.args[0]
        assert cmd[:4] == ['sudo', '-u', 'op', '-H']
        assert cmd[-1] == 'meshtastic==2.7.9'

    def test_unreadable_floor_refuses_blind_upgrade(self):
        h = _handler()
        with patch('handlers.updates.read_floor', return_value=None):
            success, msg = h._pipx_upgrade_cli()
        assert success is False
        assert 'baseline' in msg.lower()


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
            patch.object(vc, 'get_meshforge_git_snapshot', return_value=None),
            patch.object(vc, 'get_meshtasticd_apt_snapshot', return_value=None),
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


class TestUpdateMeshtasticdFlow:
    """The 2026-07-10 rework: _update_meshtasticd drives the apt state machine
    (candidate truth, hold as deliberate pin, dry-run installability, guided
    mismatched-repo repair) instead of a blind `apt upgrade` string."""

    def _state(self, **kw):
        from updates.meshtasticd_apt import MeshtasticdAptState
        defaults = dict(
            apt_available=True,
            installed='2.7.24.58~obs472b14c~alpha',
            candidate='2.7.26.61~obs54e0d8d~beta',
            held=False, update_available=True, candidate_installable=True,
        )
        defaults.update(kw)
        return MeshtasticdAptState(**defaults)

    def test_up_to_date_reports_no_update(self):
        h = _handler()
        state = self._state(candidate='2.7.24.58~obs472b14c~alpha',
                            update_available=False, candidate_installable=None)
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=state), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        assert 'No Update' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_held_decline_runs_nothing(self):
        """A hold is a deliberate pin: declining the unhold prompt must end
        the flow with zero writes."""
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=False)
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=self._state(held=True)), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        run.assert_not_called()
        # And the pin prompt is default-no.
        assert h.ctx.dialog.yesno.call_args.kwargs.get('default_no') is True

    def test_held_accept_unholds_and_reholds(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=self._state(held=True)), \
                patch('handlers.updates.run_meshtasticd_upgrade',
                      return_value=(True, 'ok 2.7.24 -> 2.7.26')) as run, \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')):
            h._update_meshtasticd()
        run.assert_called_once_with(unhold=True, rehold=True)

    def test_broken_candidate_without_repo_diagnosis_stops_honestly(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        state = self._state(candidate_installable=False,
                            blocking_detail='E: unmet deps: libc6 (>= 2.42)',
                            mismatched_repos=[])
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=state), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        assert 'Candidate Not Installable' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_broken_candidate_guided_repair_then_upgrade(self):
        """The 2026-07-10 field class: mismatched Debian_Testing repo → operator
        accepts the repair → repo disabled, apt refreshed, state RE-DERIVED,
        upgrade proceeds on the new state."""
        from updates.meshtasticd_apt import RepoLine
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        bad = RepoLine('/etc/apt/sources.list.d/meshtastic.list', 1,
                       'deb .../Meshtastic:/beta/Debian_Testing/ /')
        broken = self._state(candidate_installable=False,
                             blocking_detail='E: libc6 (>= 2.42)',
                             mismatched_repos=[bad])
        repaired = self._state(candidate_installable=True)
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   side_effect=[broken, repaired]) as get_state, \
                patch('handlers.updates.disable_repo_lines',
                      return_value=(True, 'disabled')) as disable, \
                patch('handlers.updates.apt_update',
                      return_value=(True, 'refreshed')), \
                patch('handlers.updates.run_meshtasticd_upgrade',
                      return_value=(True, 'verified')) as run, \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')):
            h._update_meshtasticd()
        disable.assert_called_once_with([bad])
        assert get_state.call_count == 2  # never proceeds on the stale state
        run.assert_called_once()

    def test_repair_that_does_not_cure_stops(self):
        from updates.meshtasticd_apt import RepoLine
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        bad = RepoLine('/etc/apt/x.list', 1, 'deb bad /')
        broken = self._state(candidate_installable=False,
                             blocking_detail='E: nope', mismatched_repos=[bad])
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   side_effect=[broken, broken]), \
                patch('handlers.updates.disable_repo_lines',
                      return_value=(True, 'disabled')), \
                patch('handlers.updates.apt_update', return_value=(True, 'ok')), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        assert 'Still Blocked' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_not_installed_points_at_installer(self):
        h = _handler()
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=self._state(installed=None,
                                            update_available=False,
                                            candidate_installable=None)), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        assert 'Not Installed' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()


class TestCliFragmentationRepair:
    """A pip --user script shadowing the pipx shim means pipx upgrades never
    reach what runs — _update_cli must surface and repair it (2026-07-10)."""

    def _versions(self, installed='2.7.9'):
        from types import SimpleNamespace
        return {'cli': SimpleNamespace(
            name='Meshtastic CLI', installed=installed, latest='2.7.9',
            fleet_floor='2.7.9', update_available=False,
            install_command='pipx install meshtastic',
            update_command='pipx upgrade meshtastic')}

    def test_fragmented_cli_offers_and_runs_repair(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        h._pipx_upgrade_cli = MagicMock(return_value=(True, 'ok'))
        diag = {'path': '/home/op/.local/bin/meshtastic',
                'kind': 'pip-script', 'shebang': '#!/usr/bin/python3'}
        with patch('handlers.updates._check_all_versions',
                   return_value=self._versions()), \
                patch('utils.cli.diagnose_meshtastic_cli', return_value=diag):
            h._update_cli()
        h._pipx_upgrade_cli.assert_called_once_with()
        assert 'Repaired' in _msgbox_titles(h.ctx.dialog)

    def test_pipx_owned_cli_skips_repair(self):
        h = _handler()
        h._pipx_upgrade_cli = MagicMock(return_value=(True, 'ok'))
        diag = {'path': '/home/op/.local/bin/meshtastic', 'kind': 'pipx',
                'shebang': '#!/home/op/.local/share/pipx/venvs/meshtastic/bin/python'}
        with patch('handlers.updates._check_all_versions',
                   return_value=self._versions()), \
                patch('utils.cli.diagnose_meshtastic_cli', return_value=diag):
            h._update_cli()
        h._pipx_upgrade_cli.assert_not_called()
        assert 'No Update' in _msgbox_titles(h.ctx.dialog)


class TestPipInstallMeshtasticFloorPinned:
    """The lib writer must land ON the reviewed floor, not PyPI-latest."""

    def test_upgrade_pins_to_floor(self):
        h = _handler()
        pi = MagicMock(side_effect=[PipResult(True, stdout='ok', verified=True)])
        with patch.object(pathlib.Path, "exists", _path_exists(False)), \
                patch('handlers.updates.read_floor', return_value='2.7.9'), \
                patch("handlers.updates.pip_install", pi):
            success, _ = h._pip_install_meshtastic(upgrade=True)
        assert success is True
        assert pi.call_args_list[0].args[0] == ['meshtastic==2.7.9']

    def test_upgrade_without_floor_refuses(self):
        h = _handler()
        pi = MagicMock()
        with patch('handlers.updates.read_floor', return_value=None), \
                patch("handlers.updates.pip_install", pi):
            success, msg = h._pip_install_meshtastic(upgrade=True)
        assert success is False
        assert 'baseline' in msg.lower()
        pi.assert_not_called()

    def test_bootstrap_install_without_floor_proceeds_unpinned(self):
        h = _handler()
        pi = MagicMock(side_effect=[PipResult(True, stdout='ok', verified=True)])
        with patch.object(pathlib.Path, "exists", _path_exists(False)), \
                patch('handlers.updates.read_floor', return_value=None), \
                patch("handlers.updates.pip_install", pi):
            success, _ = h._pip_install_meshtastic(upgrade=False)
        assert success is True
        assert pi.call_args_list[0].args[0] == ['meshtastic']


class TestCheckAllVersionsMeshtasticdApt:
    """check_all_versions judges meshtasticd by the apt CANDIDATE and treats a
    hold as a deliberate pin (surfaced in notes, never a nagging update)."""

    def _snapshot(self, **kw):
        from updates.meshtasticd_apt import MeshtasticdAptState
        defaults = dict(apt_available=True, installed='2.7.24', candidate='2.7.26',
                        held=False, update_available=True)
        defaults.update(kw)
        return MeshtasticdAptState(**defaults)

    def _run_check(self, vc, snapshot):
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in [
                patch.object(vc, 'get_meshtasticd_apt_snapshot',
                             return_value=snapshot),
                patch.object(vc, 'get_meshtastic_cli_version', return_value=None),
                patch.object(vc, 'get_meshtastic_lib_version', return_value=None),
                patch.object(vc, 'get_latest_meshtastic_cli_version',
                             return_value=None),
                patch.object(vc, 'read_floor', return_value='2.7.9'),
                patch.object(vc, 'get_meshforge_version', return_value=None),
                patch.object(vc, 'get_latest_meshforge_version', return_value=None),
                patch.object(vc, 'get_meshforge_git_snapshot', return_value=None),
                patch.object(vc, 'get_meshtasticd_version', return_value=None),
                patch.object(vc, 'get_latest_meshtasticd_version', return_value=None),
                patch.object(vc, 'get_node_firmware_version', return_value=None),
                patch.object(vc, 'get_latest_firmware_version', return_value=None),
            ]:
                stack.enter_context(p)
            return vc.check_all_versions()

    def test_candidate_is_the_latest_source(self):
        import updates.version_checker as vc
        results = self._run_check(vc, self._snapshot())
        info = results['meshtasticd']
        assert info.installed == '2.7.24'
        assert info.latest == '2.7.26'
        assert info.update_available is True
        assert info.held is False

    def test_hold_is_a_pin_not_a_nag(self):
        import updates.version_checker as vc
        results = self._run_check(vc, self._snapshot(held=True))
        info = results['meshtasticd']
        assert info.update_available is False
        assert info.held is True
        assert 'pinned' in info.notes
        assert '2.7.26' in info.notes  # the waiting candidate is visible

    def test_hold_at_candidate_notes_plain_pin(self):
        import updates.version_checker as vc
        results = self._run_check(
            vc, self._snapshot(held=True, installed='2.7.26',
                               update_available=False))
        info = results['meshtasticd']
        assert info.update_available is False
        assert 'pinned' in info.notes

    def test_apt_unavailable_falls_back_to_legacy(self):
        import updates.version_checker as vc
        results = self._run_check(vc, None)
        info = results['meshtasticd']
        # legacy getters are patched to None → quiet, no phantom state
        assert info.installed is None
        assert info.update_available is False


class TestUpdateMeshforgeGitFlow:
    """The 2026-07-10 self-update redesign: git truth, --ff-only, per-step
    honest report, and the running services actually restarted."""

    def _state(self, **kw):
        from updates.meshforge_git import MeshForgeGitState
        defaults = dict(is_git_repo=True, repo_dir='/opt/x', head='a' * 40,
                        remote_head='b' * 40, behind=2, ahead=0, dirty=False,
                        update_available=True, fetch_ok=True)
        defaults.update(kw)
        return MeshForgeGitState(**defaults)

    def test_up_to_date_verified_against_fetch(self):
        h = _handler()
        state = self._state(behind=0, remote_head='a' * 40,
                            update_available=False)
        with patch('handlers.updates.get_meshforge_git_state',
                   return_value=state), \
                patch('handlers.updates.run_meshforge_git_update') as run:
            h._update_meshforge()
        assert 'Up To Date' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_offline_is_unknown_not_up_to_date(self):
        h = _handler()
        state = self._state(fetch_ok=False, error='fetch failed: no route')
        with patch('handlers.updates.get_meshforge_git_state',
                   return_value=state), \
                patch('handlers.updates.run_meshforge_git_update') as run:
            h._update_meshforge()
        assert 'Cannot Verify Remote' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_dirty_tree_refuses(self):
        h = _handler()
        with patch('handlers.updates.get_meshforge_git_state',
                   return_value=self._state(dirty=True)), \
                patch('handlers.updates.run_meshforge_git_update') as run:
            h._update_meshforge()
        assert 'Local Changes Present' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_git_step_failure_aborts_with_report(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        with patch('handlers.updates.get_meshforge_git_state',
                   return_value=self._state()), \
                patch('handlers.updates.meshforge_services_to_restart',
                      return_value=['meshforge']), \
                patch('handlers.updates.run_meshforge_git_update',
                      return_value=(False, 'ff failed')), \
                patch('handlers.updates.requirements_changed') as req, \
                patch('handlers.updates._apply_config_and_restart') as restart:
            h._update_meshforge()
        assert 'Update Failed' in _msgbox_titles(h.ctx.dialog)
        req.assert_not_called()       # aborted before the deps step
        restart.assert_not_called()   # and before any restart

    def test_full_path_restarts_active_services(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        h._refresh_service_files = MagicMock(return_value=(True, 'meshforge.service'))
        after = self._state(head='b' * 40, behind=0, update_available=False)
        with patch('handlers.updates.get_meshforge_git_state',
                   side_effect=[self._state(), after]), \
                patch('handlers.updates.meshforge_services_to_restart',
                      return_value=['meshforge', 'meshforge-map']), \
                patch('handlers.updates.run_meshforge_git_update',
                      return_value=(True, 'updated aaaa -> bbbb (verified)')), \
                patch('handlers.updates.requirements_changed',
                      return_value=False), \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')) as restart:
            h._update_meshforge()
        assert restart.call_count == 2
        restarted = [c.args[0] for c in restart.call_args_list]
        assert restarted == ['meshforge', 'meshforge-map']
        assert 'Update Applied' in _msgbox_titles(h.ctx.dialog)

    def test_deps_step_runs_when_diff_unknown(self):
        """None (could-not-determine) must run the deps step — skipping on
        unknown is the silent-failure direction."""
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        h._refresh_service_files = MagicMock(return_value=(True, 'ok'))
        after = self._state(head='b' * 40)
        with patch('handlers.updates.get_meshforge_git_state',
                   side_effect=[self._state(), after]), \
                patch('handlers.updates.meshforge_services_to_restart',
                      return_value=[]), \
                patch('handlers.updates.run_meshforge_git_update',
                      return_value=(True, 'updated (verified)')), \
                patch('handlers.updates.requirements_changed',
                      return_value=None), \
                patch('handlers.updates.repo_root',
                      return_value=pathlib.Path('/nonexistent-repo')), \
                patch('handlers.updates.pip_install') as pi:
            h._update_meshforge()
        # requirements.txt missing at the fake root -> deps step FAILS loudly
        # (never silently skipped); pip itself was not reachable.
        titles = _msgbox_titles(h.ctx.dialog)
        assert 'Update Failed' in titles

    def test_restart_failure_is_a_fail_line_not_masked(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        h._refresh_service_files = MagicMock(return_value=(True, 'ok'))
        after = self._state(head='b' * 40)
        with patch('handlers.updates.get_meshforge_git_state',
                   side_effect=[self._state(), after]), \
                patch('handlers.updates.meshforge_services_to_restart',
                      return_value=['meshforge']), \
                patch('handlers.updates.run_meshforge_git_update',
                      return_value=(True, 'updated (verified)')), \
                patch('handlers.updates.requirements_changed',
                      return_value=False), \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(False, 'unit crashed')):
            h._update_meshforge()
        assert 'Update Failed' in _msgbox_titles(h.ctx.dialog)
        # The failing unit is named in the report body.
        body = [c.args[1] for c in h.ctx.dialog.msgbox.call_args_list
                if c.args and c.args[0] == 'Update Failed'][0]
        assert 'restart meshforge' in body
        assert 'unit crashed' in body
