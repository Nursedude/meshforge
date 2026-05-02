"""Unit tests for MeshChatX handler + service-state SSOT.

Mirrors the regression-prevention shape of ``test_nomadnet_handler.py``:

  - Handler protocol contract (handler_id, menu_section, menu_items, execute)
  - Service-state SSOT contract (every key in the dict, default values)
  - port_bound liveness probe (the analog of NomadNet's tmux_session)
  - Service-state-line subtitle rendering across variants
  - "Open Web UI" gating on active + port_bound
  - Uninstall yesno gate

Run: python3 -m pytest tests/test_meshchatx_handler.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src',
                                 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402

from handlers.meshchatx import MeshChatXHandler  # noqa: E402


# ======================================================================
# Handler protocol contract
# ======================================================================

class TestHandlerProtocolContract:
    """The handler must satisfy the registry's expectations."""

    def test_handler_id(self):
        h = MeshChatXHandler()
        assert h.handler_id == "meshchatx"

    def test_menu_section(self):
        h = MeshChatXHandler()
        # mesh_networks is the same section NomadNet sits under — both
        # are LXMF-over-RNS clients, the operator should find them
        # together.
        assert h.menu_section == "mesh_networks"

    def test_menu_items_shape(self):
        h = MeshChatXHandler()
        items = h.menu_items()
        assert isinstance(items, list)
        assert len(items) == 1
        tag, label, flag = items[0]
        assert tag == "meshchatx"
        assert "MeshChatX" in label
        # Feature-flagged: must be gated by 'meshchatx' so the GATEWAY
        # default-False profile actually hides it until enabled.
        assert flag == "meshchatx"

    def test_execute_routes_meshchatx_to_main_menu(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        with patch.object(h, '_meshchatx_menu') as menu:
            h.execute("meshchatx")
            menu.assert_called_once()

    def test_execute_unknown_action_is_safe(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        # No registered dispatch for "frob" — should be a no-op, not raise.
        h.execute("frob")


# ======================================================================
# Service-state SSOT contract
# ======================================================================

class TestServiceStateSSoTContract:
    """Every consumer reads the SSOT dict — its shape must not drift."""

    REQUIRED_KEYS = {
        "unit_installed",
        "wrapper_installed",
        "active",
        "enabled",
        "sub_state",
        "main_pid",
        "n_restarts",
        "port_bound",
        "port",
        "error",
    }

    def _state_with_systemctl_unreachable(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        # Mock systemctl --user as totally unreachable (rc=-1) so we
        # exercise the early-return branch that still populates the
        # contract keys.
        with patch.object(h, '_user_systemctl_text', return_value=(-1, '')):
            return h._meshchatx_service_state()

    def test_returns_all_required_keys(self):
        s = self._state_with_systemctl_unreachable()
        assert set(s.keys()) == self.REQUIRED_KEYS, (
            f"missing: {self.REQUIRED_KEYS - set(s.keys())}, "
            f"extra: {set(s.keys()) - self.REQUIRED_KEYS}"
        )

    def test_default_port_is_8000(self):
        # Pinned by the wrapper template — every consumer assumes 8000
        # until we explicitly add per-box override support.
        s = self._state_with_systemctl_unreachable()
        assert s["port"] == 8000

    def test_systemctl_unreachable_sets_error_and_returns_inactive(self):
        s = self._state_with_systemctl_unreachable()
        assert s["error"] is not None
        assert s["active"] is False
        assert s["enabled"] is False

    def test_active_running_unit_populates_state(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())

        def fake_systemctl_text(verbs, timeout=10):
            if verbs == ['is-active', 'meshchatx']:
                return 0, 'active'
            if verbs == ['is-enabled', 'meshchatx']:
                return 0, 'enabled'
            if verbs[0] == 'show':
                return 0, "SubState=running\nMainPID=12345\nNRestarts=0"
            return -1, ''

        with patch.object(h, '_user_systemctl_text',
                          side_effect=fake_systemctl_text), \
             patch.object(h, '_port_bound_by_pid', return_value=True), \
             patch('handlers._meshchatx_service_ops.Path.exists',
                   return_value=True):
            s = h._meshchatx_service_state()

        assert s["active"] is True
        assert s["enabled"] is True
        assert s["sub_state"] == "running"
        assert s["main_pid"] == 12345
        assert s["n_restarts"] == 0
        assert s["port_bound"] is True
        assert s["error"] is None


# ======================================================================
# port_bound liveness probe (the analog of NomadNet's tmux_session)
# ======================================================================

class TestPortBoundProbe:
    """``ss -tnlp 'sport = :8000'`` parsing — the SSOT's liveness check."""

    def test_pid_zero_short_circuits(self):
        h = MeshChatXHandler()
        # We don't have a real listener; we just assert the function
        # doesn't shell out for a sentinel pid and returns False.
        assert h._port_bound_by_pid(0, port=8000) is False
        assert h._port_bound_by_pid(-1, port=8000) is False

    def test_no_ss_binary_returns_false(self):
        h = MeshChatXHandler()
        with patch('handlers._meshchatx_service_ops.shutil.which',
                   return_value=None):
            assert h._port_bound_by_pid(12345, port=8000) is False

    def test_pid_match_in_users_field(self):
        h = MeshChatXHandler()
        ss_output = (
            'LISTEN 0 5 127.0.0.1:8000 0.0.0.0:* '
            'users:(("meshchatx",pid=12345,fd=8))\n'
        )
        proc_mock = MagicMock(returncode=0, stdout=ss_output)
        with patch('handlers._meshchatx_service_ops.shutil.which',
                   return_value='/usr/bin/ss'), \
             patch('handlers._meshchatx_service_ops.subprocess.run',
                   return_value=proc_mock):
            assert h._port_bound_by_pid(12345, port=8000) is True

    def test_pid_mismatch_falls_back_to_any_listener(self):
        # When the unit's MainPID is the parent and a child holds the
        # actual socket (multi-process Python servers), we still want
        # a True signal — matches NomadNet's slightly-weaker SSOT shape.
        h = MeshChatXHandler()
        ss_output = (
            'LISTEN 0 5 127.0.0.1:8000 0.0.0.0:* '
            'users:(("python3",pid=99999,fd=8))\n'
        )
        proc_mock = MagicMock(returncode=0, stdout=ss_output)
        with patch('handlers._meshchatx_service_ops.shutil.which',
                   return_value='/usr/bin/ss'), \
             patch('handlers._meshchatx_service_ops.subprocess.run',
                   return_value=proc_mock):
            # PID doesn't match, but a listener exists → True
            assert h._port_bound_by_pid(12345, port=8000) is True

    def test_no_listener_returns_false(self):
        h = MeshChatXHandler()
        proc_mock = MagicMock(returncode=0, stdout='')
        with patch('handlers._meshchatx_service_ops.shutil.which',
                   return_value='/usr/bin/ss'), \
             patch('handlers._meshchatx_service_ops.subprocess.run',
                   return_value=proc_mock):
            assert h._port_bound_by_pid(12345, port=8000) is False


# ======================================================================
# _service_state_line subtitle rendering
# ======================================================================

class TestServiceStateLine:
    """The one-liner that lands in menu subtitles."""

    def test_unit_not_installed(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        line = h._service_state_line({
            "unit_installed": False, "active": False, "enabled": False,
            "sub_state": "", "main_pid": 0, "n_restarts": 0,
            "port_bound": False, "port": 8000,
            "wrapper_installed": False, "error": None,
        })
        assert "not installed" in line.lower()

    def test_active_with_port_bound(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        line = h._service_state_line({
            "unit_installed": True, "active": True, "enabled": True,
            "sub_state": "running", "main_pid": 12345, "n_restarts": 0,
            "port_bound": True, "port": 8000,
            "wrapper_installed": True, "error": None,
        })
        assert "active" in line.lower()
        assert "8000" in line
        assert "bound" in line.lower()

    def test_active_but_port_not_bound_warns(self):
        # Service is active but the daemon hasn't bound the port yet
        # (or crashed and is restarting). The subtitle MUST surface
        # this — the operator otherwise sees "active" and assumes
        # the UI is reachable.
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        line = h._service_state_line({
            "unit_installed": True, "active": True, "enabled": True,
            "sub_state": "running", "main_pid": 12345, "n_restarts": 3,
            "port_bound": False, "port": 8000,
            "wrapper_installed": True, "error": None,
        })
        assert "NOT bound" in line
        assert "restart" in line.lower()

    def test_failed_state_surfaces_clearly(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        line = h._service_state_line({
            "unit_installed": True, "active": False, "enabled": True,
            "sub_state": "failed", "main_pid": 0, "n_restarts": 5,
            "port_bound": False, "port": 8000,
            "wrapper_installed": True, "error": None,
        })
        assert "failed" in line.lower()


# ======================================================================
# "Open Web UI" gating
# ======================================================================

class TestOpenWebUI:
    """The action must refuse to "open" a UI that isn't actually up."""

    def _handler_with_state(self, state):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        h._meshchatx_service_state = lambda: state
        return h

    def test_refuses_when_inactive(self):
        h = self._handler_with_state({
            "unit_installed": True, "active": False, "enabled": True,
            "sub_state": "inactive", "main_pid": 0, "n_restarts": 0,
            "port_bound": False, "port": 8000,
            "wrapper_installed": True, "error": None,
        })
        h._open_web_ui()
        # Last msgbox must mention "not running"
        title = h.ctx.dialog.last_msgbox_title or ""
        text = h.ctx.dialog.last_msgbox_text or ""
        assert "not running" in title.lower() or "not running" in text.lower()

    def test_refuses_when_active_but_port_not_bound(self):
        h = self._handler_with_state({
            "unit_installed": True, "active": True, "enabled": True,
            "sub_state": "running", "main_pid": 12345, "n_restarts": 0,
            "port_bound": False, "port": 8000,
            "wrapper_installed": True, "error": None,
        })
        h._open_web_ui()
        text = h.ctx.dialog.last_msgbox_text or ""
        assert "warming" in text.lower() or "not yet" in text.lower()

    def test_headless_path_prints_url_and_tunnel_hint(self):
        h = self._handler_with_state({
            "unit_installed": True, "active": True, "enabled": True,
            "sub_state": "running", "main_pid": 12345, "n_restarts": 0,
            "port_bound": True, "port": 8000,
            "wrapper_installed": True, "error": None,
        })
        # Force headless: clear DISPLAY/WAYLAND_DISPLAY
        with patch.dict(os.environ, {}, clear=True):
            h._open_web_ui()
        text = h.ctx.dialog.last_msgbox_text or ""
        assert "http://127.0.0.1:8000/" in text
        # SSH-tunnel hint MUST appear so remote-workstation operators
        # know how to reach the UI.
        assert "ssh -L" in text
        assert "8000:localhost:8000" in text


# ======================================================================
# Uninstall safety
# ======================================================================

class TestUninstallSafety:
    """Uninstall MUST be gated by an explicit yesno; no surprise wipes."""

    def test_uninstall_aborts_when_user_says_no(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        h.ctx.dialog._yesno_returns = [False]   # operator says no
        with patch.object(h, '_systemctl_user') as systemctl:
            h._uninstall()
        # Service must NOT have been touched
        systemctl.assert_not_called()

    def test_uninstall_proceeds_when_user_confirms(self):
        h = MeshChatXHandler()
        h.set_context(make_handler_context())
        h.ctx.dialog._yesno_returns = [True]
        # Stub subprocess so pipx doesn't actually run
        proc_ok = MagicMock(returncode=0, stdout='', stderr='')
        with patch.object(h, '_systemctl_user',
                          return_value=(True, '')) as systemctl, \
             patch('handlers.meshchatx.subprocess.run',
                   return_value=proc_ok):
            h._uninstall()
        # Three systemctl actions: stop, disable, daemon-reload
        verbs = [c.args[0] for c in systemctl.call_args_list]
        assert 'stop' in verbs
        assert 'disable' in verbs
        assert 'daemon-reload' in verbs


# ======================================================================
# Lxmf exclusivity wiring (regression: meshchatx must be in the set)
# ======================================================================

class TestLXMFExclusivityWiring:
    """``_lxmf_utils`` must know about meshchatx so coexistence works."""

    def test_meshchatx_in_lxmf_client_names(self):
        from handlers._lxmf_utils import LXMF_CLIENT_NAMES
        assert "meshchatx" in LXMF_CLIENT_NAMES

    def test_meshchatx_default_config_dir(self):
        from handlers._lxmf_utils import DEFAULT_CONFIG_DIRS
        # The actual path follows the canonical storage dir we pin in
        # MeshChatXPaths — must match so coexistence detection works.
        assert DEFAULT_CONFIG_DIRS["meshchatx"] == ".local/share/meshchatx"
