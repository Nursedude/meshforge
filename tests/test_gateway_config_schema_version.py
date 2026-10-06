"""1.0 gate 2 (config upgrades): gateway.json carries a schema version.

Auto-migrate with a backup; refuse LOUDLY on a file written by a newer
version — never silently reinterpret a file this version does not
understand (ROADMAP.md, 1.0 gate 2).
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from gateway.config import GatewayConfig


@pytest.fixture
def home(tmp_path):
    with patch("gateway.config.get_real_user_home", return_value=tmp_path):
        yield GatewayConfig.get_config_path()


def test_save_stamps_schema_version_1(home):
    assert GatewayConfig().save() is True
    assert json.loads(home.read_text())["schema_version"] == 1


def test_a_newer_version_file_is_refused_and_never_overwritten(home):
    # A file from a future MeshForge: its keys may MEAN something this
    # version cannot know. Reading it as defaults-plus-what-we-recognise
    # and saving would rewrite it in the old dialect.
    original = json.dumps({"schema_version": 2, "enabled": True,
                           "bridge_mode": "mqtt_bridge"}, indent=2)
    home.write_text(original)

    cfg = GatewayConfig.load()

    assert cfg.load_error is not None
    assert "newer" in cfg.load_error
    assert "2" in cfg.load_error
    assert cfg.save() is False
    assert home.read_text() == original


def test_first_save_over_a_legacy_file_keeps_the_original_bytes(home):
    # Every pre-gate-2 gateway.json has no schema_version. load() migrates
    # it in memory (http_port 443 -> 9443); the save that writes the
    # migrated form must leave the operator's original beside it.
    original = json.dumps({"enabled": True,
                           "meshtastic": {"http_port": 443}}, indent=2)
    home.write_text(original)
    backup = home.with_name("gateway.json.pre-v1")

    cfg = GatewayConfig.load()
    assert cfg.save() is True

    assert json.loads(home.read_text())["schema_version"] == 1
    assert json.loads(home.read_text())["meshtastic"]["http_port"] == 9443
    assert backup.read_text() == original

    # A later save is over a v1 file: the pre-v1 copy stays the original.
    assert GatewayConfig.load().save() is True
    assert backup.read_text() == original


@pytest.mark.parametrize("bad", ["1", True, 1.0, -1, None, [1]])
def test_a_schema_version_that_is_not_a_plain_count_is_refused(home, bad):
    # true == 1 in Python and "1" is what a hand edit produces; guessing
    # either is reinterpreting a file nobody can have meant.
    original = json.dumps({"schema_version": bad, "enabled": True})
    home.write_text(original)

    cfg = GatewayConfig.load()

    assert cfg.load_error is not None
    assert "schema_version" in cfg.load_error
    assert repr(bad) in cfg.load_error
    assert cfg.save() is False
    assert home.read_text() == original


@pytest.mark.parametrize("text", [
    json.dumps({"schema_version": 2, "enabled": True}),   # newer MeshForge
    "{not json",                                          # unreadable
])
def test_the_gateway_service_refuses_to_start_on_a_refused_load(home, text, capsys):
    # The consumer of record: meshforge-gateway runs bridge_cli.main(). It
    # printed "Config loaded" and started bridges on DEFAULTS whenever load()
    # refused the file — the launcher's S3 fix (09-28) never reached it.
    from unittest.mock import MagicMock
    from gateway import bridge_cli

    home.write_text(text)
    resolve = MagicMock(return_value=[])
    with patch.object(bridge_cli, "assert_writable_or_exit"), \
         patch.object(bridge_cli, "resolve_bridges", resolve):
        with pytest.raises(SystemExit) as exc:
            bridge_cli.main()

    assert exc.value.code not in (0, None)
    out = capsys.readouterr().out
    assert "REFUSING" in out
    assert str(home) in out
    # The journal must not claim the file loaded right before refusing it
    # (seen live on moc3, 2026-10-06 drill).
    assert "Config loaded" not in out
    resolve.assert_not_called()
    assert home.read_text() == text


def test_the_gateway_service_still_reports_a_good_load(home, capsys):
    # Control for the assertion above: a fix that just deletes the line
    # would pass it. A file that loads must still say so.
    from gateway import bridge_cli

    home.write_text(json.dumps({"schema_version": 1, "enabled": True}))

    class _Stop(Exception):
        pass

    with patch.object(bridge_cli, "assert_writable_or_exit"), \
         patch.object(bridge_cli, "resolve_bridges", side_effect=_Stop):
        with pytest.raises(_Stop):
            bridge_cli.main()

    out = capsys.readouterr().out
    assert f"Config loaded from: {home}" in out
    assert "REFUSING" not in out


def test_the_headless_gateway_refuses_to_start_on_a_refused_load(home):
    # meshforge-daemon's path: start_gateway_headless() built the bridge with
    # no config, so the bridge called load() itself and ran its defaults.
    from gateway import gateway_cli

    text = json.dumps({"schema_version": 2, "enabled": True})
    home.write_text(text)
    with patch("gateway.rns_bridge.RNSMeshtasticBridge") as Bridge:
        assert gateway_cli.start_gateway_headless() is False
    Bridge.assert_not_called()
    assert home.read_text() == text


def test_the_gateway_unit_does_not_restart_into_a_config_refusal():
    # A refused config does not heal by retrying: Restart=on-failure would
    # re-run the refusal every RestartSec until StartLimitBurst. The unit's
    # RestartPreventExitStatus and bridge_cli's refusal code are ONE value.
    from pathlib import Path
    from gateway import bridge_cli

    unit = (Path(__file__).resolve().parent.parent / "contrib" / "systemd"
            / "meshforge-gateway.service.in").read_text()
    prevented = [line.split("=", 1)[1].split() for line in unit.splitlines()
                 if line.startswith("RestartPreventExitStatus=")]
    assert prevented, "the gateway unit restarts into a config refusal"
    assert str(bridge_cli.EXIT_CONFIG_REFUSED) in prevented[-1]


def test_a_load_that_raises_is_not_reported_as_loaded(home, capsys):
    # load() raising falls back to DEFAULTS with no load_error — that path
    # must not print "Config loaded" either.
    from gateway import bridge_cli

    class _Stop(Exception):
        pass

    with patch.object(bridge_cli, "assert_writable_or_exit"), \
         patch.object(bridge_cli.GatewayConfig, "load", side_effect=OSError("boom")), \
         patch.object(bridge_cli, "resolve_bridges", side_effect=_Stop):
        with pytest.raises(_Stop):
            bridge_cli.main()

    out = capsys.readouterr().out
    assert "Could not load config" in out
    assert "Config loaded" not in out
