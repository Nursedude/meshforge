"""Frontier review S3 (2026-09-28): the headless gateway launcher must not
start a bridge on DEFAULTS when gateway.json failed to load.

`GatewayConfig.load()` returns defaults with `load_error` set when the file
exists but cannot be read; since 06e06c7c `save()` REFUSES to write those
defaults over it. `launch_gateway_bridge` ignored save()'s return, so the
"Enable and start now?" path ran `RNSMeshtasticBridge(defaults)` with
`enabled=True` — no mesh_bridge legs, default PSK — and said nothing.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import launcher


def _cfg(load_error=None, enabled=False, save_ok=True):
    cfg = SimpleNamespace(load_error=load_error, enabled=enabled)
    cfg.get_config_path = lambda: Path("/nonexistent/gateway.json")
    cfg.save = MagicMock(return_value=save_ok)
    return cfg


def _launch(cfg, answer="y"):
    bridge_cls = MagicMock()
    bridge_cls.return_value.start.return_value = False  # never enters the loop
    gw = MagicMock()
    gw.load.return_value = cfg
    with patch.object(launcher, "_HAS_BRIDGE", True), \
         patch.object(launcher, "_HAS_GATEWAY_CONFIG", True), \
         patch.object(launcher, "GatewayConfig", gw), \
         patch.object(launcher, "RNSMeshtasticBridge", bridge_cls), \
         patch("builtins.input", return_value=answer):
        launcher.launch_gateway_bridge(Path("/tmp"))
    return bridge_cls


def test_failed_load_starts_nothing(capsys):
    cfg = _cfg(load_error="JSONDecodeError: Expecting value", enabled=False)
    bridge = _launch(cfg)
    bridge.assert_not_called()
    cfg.save.assert_not_called()
    out = capsys.readouterr().out
    assert "failed to load" in out and "JSONDecodeError" in out
    assert "defaults" in out


def test_failed_load_with_enabled_default_true_still_starts_nothing():
    # enabled=True on a DEFAULTS object is exactly the silent path.
    bridge = _launch(_cfg(load_error="PermissionError: denied", enabled=True))
    bridge.assert_not_called()


def test_refused_save_starts_nothing(capsys):
    cfg = _cfg(load_error=None, enabled=False, save_ok=False)
    bridge = _launch(cfg, answer="y")
    cfg.save.assert_called_once()
    bridge.assert_not_called()
    assert "not starting" in capsys.readouterr().out


def test_clean_load_and_saved_enable_starts_the_bridge():
    cfg = _cfg(load_error=None, enabled=False, save_ok=True)
    bridge = _launch(cfg, answer="y")
    assert cfg.enabled is True
    bridge.assert_called_once_with(cfg)
