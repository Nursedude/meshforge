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
