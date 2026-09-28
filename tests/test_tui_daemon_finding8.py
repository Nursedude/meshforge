"""TUI audit finding 8 (2026-09-27): the Daemon screen showed another
program's journal as "Daemon Logs", could start a SECOND gateway bridge next
to systemd's meshforge-gateway, and the TUI's Config API silently lost a
:8081 bind race to the daemon's (then read "STOPPED" while the API was up).
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


def _daemon():
    from handlers.daemon import DaemonHandler
    h = DaemonHandler()
    h.set_context(make_handler_context())
    h.ctx.dialog._yesno_returns = [True]
    return h


def _cfg(gateway_enabled):
    return patch("daemon_config.DaemonConfig.load",
                 return_value=SimpleNamespace(gateway_enabled=gateway_enabled))


class TestNoSecondBridge:
    def test_refuses_when_the_gateway_service_already_runs(self):
        h = _daemon()
        with _cfg(True), \
             patch("utils.service_check.check_service",
                   return_value=SimpleNamespace(available=True)), \
             patch("handlers.daemon.subprocess.Popen") as popen:
            h._daemon_start()
        popen.assert_not_called()
        assert h.ctx.dialog.last_msgbox_title == "Daemon NOT Started"
        assert "TWO bridges" in h.ctx.dialog.last_msgbox_text

    def test_unknown_gateway_state_refuses_too(self):
        h = _daemon()
        with _cfg(True), \
             patch("utils.service_check.check_service", side_effect=RuntimeError), \
             patch("handlers.daemon.subprocess.Popen") as popen:
            h._daemon_start()
        popen.assert_not_called()

    def test_gateway_disabled_starts_and_logs_to_the_file(self, tmp_path):
        h = _daemon()
        log = tmp_path / "daemon.log"
        proc = MagicMock()
        proc.poll.return_value = None
        with _cfg(False), \
             patch("handlers.daemon._daemon_log_path", return_value=log), \
             patch("handlers.daemon.subprocess.Popen", return_value=proc) as popen, \
             patch("time.sleep"):
            h._daemon_start()
        popen.assert_called_once()
        kw = popen.call_args.kwargs
        import subprocess
        assert kw["stdout"] is not subprocess.DEVNULL      # not thrown away
        assert kw["stderr"] == subprocess.STDOUT
        assert log.exists()                                # the file Logs reads


class TestDaemonLogs:
    def test_never_reads_the_wrong_units_journal(self, tmp_path):
        h = _daemon()
        with patch("handlers.daemon._daemon_log_path", return_value=tmp_path / "none.log"), \
             patch("subprocess.run", side_effect=AssertionError("no journal read")):
            h._daemon_logs()
        texts = [c[1][1] for c in h.ctx.dialog.calls if c[0] == "textbox"]
        assert texts and "No daemon log yet" in texts[-1]


class TestConfigApiPort:
    def _api(self):
        from handlers.config_api import ConfigAPIHandler
        h = ConfigAPIHandler()
        h.set_context(make_handler_context())
        return h

    def test_auto_start_skips_a_port_already_served(self):
        h = self._api()
        with patch.object(type(h), "_port_in_use", staticmethod(lambda port=8081: True)), \
             patch("utils.config_api.ConfigAPIServer") as server:
            h._maybe_auto_start()
        server.assert_not_called()
        assert h._server is None

    def test_menu_says_served_elsewhere_not_stopped(self):
        h = self._api()
        h.ctx.dialog._menu_returns = ["back"]
        with patch.object(type(h), "_port_in_use", staticmethod(lambda port=8081: True)):
            h._config_api_menu()
        menus = [c for c in h.ctx.dialog.calls if c[0] == "menu"]
        body = str(menus[0])
        assert "SERVED BY ANOTHER PROCESS" in body and "STOPPED" not in body
