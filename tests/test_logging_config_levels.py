"""setup_logging(level=X) must mean X unless the operator SAVED a level.

2026-10-09: moc's gateway logged ~1,300 DEBUG lines/h to journald although
bridge_cli calls setup_logging(level=INFO). _load_persisted_levels() read the
logging SettingsManager, whose DEFAULTS were global_level DEBUG — so with no
saved file at all, the persistence layer overrode every caller's explicit
level. A persisted level is operator intent; an absent one is not.
"""

import json
import logging

import pytest

import utils.common as common
import utils.logging_config as lc


@pytest.fixture
def fresh_logging(tmp_path, monkeypatch):
    """Isolate logging_config globals + the settings dir; restore root after."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    monkeypatch.setattr(common, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_initialized", False)
    monkeypatch.setattr(lc, "_log_settings", None)
    monkeypatch.setattr(lc, "_console_handler", None)
    monkeypatch.setattr(lc, "_file_handler", None)
    monkeypatch.setattr(lc, "_global_log_level", logging.DEBUG)
    monkeypatch.setattr(lc, "_component_levels", dict(lc._component_levels))
    yield tmp_path
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_no_saved_settings_caller_level_wins(fresh_logging):
    lc.setup_logging(level=logging.INFO, use_colors=False)
    assert lc._console_handler.level == logging.INFO
    assert lc.get_current_log_level() == "INFO"


def test_no_saved_settings_gateway_debug_not_emitted(fresh_logging, capsys):
    lc.setup_logging(level=logging.INFO, use_colors=False)
    log = lc.get_logger("gateway.node_state")
    log.debug("should-not-appear")
    log.info("should-appear")
    out = capsys.readouterr().out
    assert "should-appear" in out
    assert "should-not-appear" not in out


def test_no_saved_settings_writes_no_file(fresh_logging):
    lc.setup_logging(level=logging.INFO, use_colors=False)
    assert not (fresh_logging / "logging.json").exists()


def test_saved_global_level_still_restored(fresh_logging):
    (fresh_logging / "logging.json").write_text(
        json.dumps({"global_level": "DEBUG"}))
    lc.setup_logging(level=logging.INFO, use_colors=False)
    assert lc._console_handler.level == logging.DEBUG


def test_saved_component_level_still_restored(fresh_logging):
    (fresh_logging / "logging.json").write_text(
        json.dumps({"component_levels": {"rns": "WARNING"}}))
    lc.setup_logging(level=logging.INFO, use_colors=False)
    assert lc._component_levels["rns"] == logging.WARNING
    assert lc._console_handler.level == logging.INFO


def test_set_log_level_persists_and_round_trips(fresh_logging):
    lc.setup_logging(level=logging.INFO, use_colors=False)
    lc.set_log_level(logging.WARNING)
    saved = json.loads((fresh_logging / "logging.json").read_text())
    assert saved == {"global_level": "WARNING"}
