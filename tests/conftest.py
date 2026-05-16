"""
Pytest configuration for MeshForge test suite.

Handles CI-specific settings:
- Auto-skip hardware-dependent tests in CI
- Timeout defaults
- Fixtures for common mocks
- Shared TUI handler test infrastructure (FakeDialog, make_handler_context)
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Ensure src and launcher_tui are importable for handler tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Detect CI environment
CI = os.environ.get('CI', 'false').lower() == 'true'
MESHFORGE_CI = os.environ.get('MESHFORGE_CI', 'false').lower() == 'true'


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "hardware: mark test as requiring hardware (skipped in CI)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow (may be skipped with --fast)"
    )
    config.addinivalue_line(
        "markers", "network: mark test as requiring network access"
    )

    # Disable cascade-detector probes during pytest. The
    # rns_rpc_wedge fingerprint calls `subprocess.run(["ss", ...],
    # timeout=2)`; when a daemon detector thread (started by any test
    # that exercises the full service init path) is still running
    # during another test's `with patch('subprocess.run')` window, the
    # probe's call leaks into the mock's call_args_list and trips
    # assertions like `assert call[1]['timeout'] == 60` —
    # observed on Python 3.9/3.11 in CI starting at commit 79f5d7b.
    # See project_ci_red_track0_followup.md for full forensics.
    # Tests that explicitly exercise probe behavior unset this via
    # `monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)`.
    os.environ["MESHFORGE_CASCADE_PROBE_DISABLED"] = "1"


def pytest_collection_modifyitems(config, items):
    """Auto-skip certain tests in CI environment."""
    if not (CI or MESHFORGE_CI):
        return

    skip_hardware = pytest.mark.skip(reason="Hardware not available in CI")
    skip_network = pytest.mark.skip(reason="Network tests skipped in CI")

    for item in items:
        # Skip hardware-marked tests
        if "hardware" in item.keywords:
            item.add_marker(skip_hardware)

        # Skip network-marked tests in CI
        if "network" in item.keywords:
            item.add_marker(skip_network)

        # Auto-detect likely hardware tests by name
        test_name = item.name.lower()
        if any(kw in test_name for kw in ['real_device', 'physical', 'actual_hardware']):
            item.add_marker(skip_hardware)


@pytest.fixture
def mock_meshtastic():
    """Mock meshtastic module for tests that don't need real hardware."""
    mock_module = MagicMock()
    mock_interface = MagicMock()
    mock_interface.nodes = {}
    mock_interface.myInfo = MagicMock()
    mock_interface.myInfo.my_node_num = 12345678

    mock_module.serial_interface.SerialInterface.return_value = mock_interface
    mock_module.tcp_interface.TCPInterface.return_value = mock_interface

    with patch.dict('sys.modules', {
        'meshtastic': mock_module,
        'meshtastic.serial_interface': mock_module.serial_interface,
        'meshtastic.tcp_interface': mock_module.tcp_interface,
    }):
        yield mock_module


@pytest.fixture
def mock_rns():
    """Mock RNS module for tests that don't need real Reticulum."""
    mock_module = MagicMock()

    with patch.dict('sys.modules', {
        'RNS': mock_module,
    }):
        yield mock_module


@pytest.fixture
def no_network():
    """Block network access for isolated tests."""
    import socket
    original_socket = socket.socket

    def guarded_socket(*args, **kwargs):
        raise OSError("Network access blocked in test")

    with patch.object(socket, 'socket', guarded_socket):
        yield


# =============================================================================
# TUI Handler Test Infrastructure
# =============================================================================

class FakeDialog:
    """Full-featured dialog stub for handler unit testing.

    Supports programmable return sequences for menu/inputbox/yesno,
    call recording for assertion, and attribute tracking.

    Usage:
        dialog = FakeDialog()
        dialog._menu_returns = ["status", "back"]  # pops from front
        dialog._yesno_returns = [True, False]
        dialog._inputbox_returns = ["localhost"]

        # After handler runs:
        assert dialog.last_msgbox_title == "Service Status"
        assert len(dialog.calls) == 3
    """

    def __init__(self):
        self.calls = []  # [(method, args, kwargs), ...]
        self._menu_returns = []
        self._inputbox_returns = []
        self._yesno_returns = []
        self._radiolist_returns = []
        self._checklist_returns = []
        self.last_msgbox_title = None
        self.last_msgbox_text = None

    def msgbox(self, title, text, **kwargs):
        self.calls.append(('msgbox', (title, text), kwargs))
        self.last_msgbox_title = title
        self.last_msgbox_text = text

    def menu(self, title, text, choices, **kwargs):
        self.calls.append(('menu', (title, text, choices), kwargs))
        if self._menu_returns:
            return self._menu_returns.pop(0)
        return None  # Exits menu loop

    def yesno(self, title, text, **kwargs):
        self.calls.append(('yesno', (title, text), kwargs))
        if self._yesno_returns:
            return self._yesno_returns.pop(0)
        return False

    def inputbox(self, title, text, init="", **kwargs):
        self.calls.append(('inputbox', (title, text), {'init': init, **kwargs}))
        if self._inputbox_returns:
            return self._inputbox_returns.pop(0)
        return init

    def radiolist(self, title, text, choices, **kwargs):
        self.calls.append(('radiolist', (title, text, choices), kwargs))
        if self._radiolist_returns:
            return self._radiolist_returns.pop(0)
        return None

    def checklist(self, title, text, choices, **kwargs):
        self.calls.append(('checklist', (title, text, choices), kwargs))
        if self._checklist_returns:
            return self._checklist_returns.pop(0)
        return []

    def textbox(self, path, **kwargs):
        self.calls.append(('textbox', (path,), kwargs))

    def gauge(self, text, percent, **kwargs):
        self.calls.append(('gauge', (text, percent), kwargs))

    def set_status_bar(self, bar):
        self.calls.append(('set_status_bar', (bar,), {}))


def make_handler_context(**overrides):
    """Factory for TUIContext with test defaults.

    Accepts any TUIContext field as a keyword override.

    Usage:
        ctx = make_handler_context()
        ctx = make_handler_context(feature_flags={"maps": True})
        ctx = make_handler_context(dialog=custom_dialog)
    """
    from handler_protocol import TUIContext
    defaults = dict(
        dialog=FakeDialog(),
        feature_flags={},
    )
    defaults.update(overrides)
    return TUIContext(**defaults)
