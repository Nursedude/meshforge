"""TUI audit finding 3 (2026-09-27): a FAILED gateway.json load must never be
saved back as defaults.

Before: GatewayConfig.load() caught every error and returned plain defaults
with nothing marking them; the next save() — TUI Save, the MeshCore toggle,
dual-radio failover, the wizard — wrote those defaults over the operator's
file. One JSON typo or one unknown key erased the whole bridge setup
(mesh_bridge legs, PSK, the moc RAK leg configured that day).
"""

import json
import os
import stat
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from gateway.config import GatewayConfig

GOOD = {"enabled": True, "bridge_mode": "mqtt_bridge",
        "mesh_bridge": {"enabled": True,
                        "secondary": {"connection_type": "serial",
                                      "serial_device": "/dev/serial/by-id/rak",
                                      "preset": "SHORT_TURBO"}}}


@pytest.fixture
def home(tmp_path):
    # the path comes from the SSOT, never a hardcode (the MA twin keeps it
    # under ~/.config/meshanchor — a hardcode hid that until the port)
    with patch("gateway.config.get_real_user_home", return_value=tmp_path):
        yield GatewayConfig.get_config_path()


def _write(path, text, mode=0o664):
    path.write_text(text)
    os.chmod(path, mode)
    return path.read_bytes()


@pytest.mark.parametrize("bad", [
    "{not json",                                               # a typo
    json.dumps({"mesh_bridge": {"enabled": True,
                                "secondary": {"brand_new_key": 1}}}),  # unknown key
    "[]",                                                      # wrong top-level type
])
def test_failed_load_is_marked_and_never_saved_over(home, bad):
    before = _write(home, bad)
    cfg = GatewayConfig.load()
    assert cfg.load_error, "a failed load must say so"
    assert cfg.save() is False, "defaults must not overwrite the operator's file"
    assert home.read_bytes() == before, "file bytes must be untouched"


def test_missing_file_is_not_a_failure(home):
    cfg = GatewayConfig.load()
    assert cfg.load_error is None
    assert cfg.save() is True and home.exists()


def test_good_file_round_trips(home):
    _write(home, json.dumps(GOOD))
    cfg = GatewayConfig.load()
    assert cfg.load_error is None
    assert cfg.mesh_bridge.secondary.serial_device == "/dev/serial/by-id/rak"
    assert cfg.save() is True
    assert json.loads(home.read_text())["mesh_bridge"]["secondary"]["serial_device"] \
        == "/dev/serial/by-id/rak"


def test_deliberate_reset_keeps_the_old_file(home):
    before = _write(home, "{not json")
    cfg = GatewayConfig.load()
    assert cfg.save(replace_unreadable=True) is True
    kept = [p for p in home.parent.iterdir() if p.name.startswith("gateway.json.unreadable-")]
    assert len(kept) == 1 and kept[0].read_bytes() == before
    json.loads(home.read_text())            # the new file is valid
    assert cfg.load_error is None           # and the object is now honest


def test_save_keeps_mode_and_leaves_no_temp_files(home):
    _write(home, json.dumps(GOOD), mode=0o640)
    cfg = GatewayConfig.load()
    assert cfg.save() is True
    assert stat.S_IMODE(home.stat().st_mode) == 0o640
    # GOOD has no schema_version, so its first save keeps the pre-v1 copy
    # (1.0 gate 2) — that one deliberate file, and no temp files.
    assert sorted(p.name for p in home.parent.iterdir()) == [
        "gateway.json", "gateway.json.pre-v1"]


class TestTuiIsHonest:
    def _handler(self, mod_name, cls_name):
        import importlib
        from handler_test_utils import make_handler_context
        mod = importlib.import_module(mod_name)
        h = getattr(mod, cls_name).__new__(getattr(mod, cls_name))
        h.ctx = make_handler_context()
        return h

    def test_gateway_menu_warns_and_declined_reset_changes_nothing(self, home):
        before = _write(home, "{not json")
        h = self._handler("handlers.gateway", "GatewayHandler")
        h.ctx.dialog._menu_returns = ["save", "back"]
        h.ctx.dialog._yesno_returns = [False]            # decline the reset
        h._gateway_config_menu()
        titles = [c[1][0] for c in h.ctx.dialog.calls if c[0] == "msgbox"]
        assert "gateway.json NOT READABLE" in titles
        assert home.read_bytes() == before

    def test_meshcore_toggle_refuses_on_unreadable(self, home):
        before = _write(home, "{not json")
        h = self._handler("handlers.meshcore", "MeshCoreHandler")
        with patch("handlers.meshcore._HAS_GW_CONFIG", True):
            h._meshcore_toggle()
        titles = [c[1][0] for c in h.ctx.dialog.calls if c[0] == "msgbox"]
        assert "gateway.json NOT READABLE" in titles
        assert home.read_bytes() == before
