"""Fleet preset tests — verify the wiring matches today's ~/mini_dudeai.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai.presets.meshforge_fleet import build_engine


def test_fleet_preset_requires_ntfy_topic(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_DUDEAI_NTFY_TOPIC", raising=False)
    with pytest.raises(ValueError, match="ntfy_topic"):
        build_engine(home=str(tmp_path))


def test_fleet_preset_wires_expected_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    engine = build_engine(
        home=str(tmp_path),
        watchdog_path=str(tmp_path / "watchdog.json"),
        federator_url="http://127.0.0.1:1/nope",
        digest_path=str(tmp_path / "digest.md"),
    )
    source_names = [getattr(s, "name", "?") for s in engine.sources]
    assert "watchdog" in source_names
    assert "federation" in source_names
    assert "digest" in source_names


def test_fleet_preset_wires_expected_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    engine = build_engine(home=str(tmp_path))
    assert set(engine.actions.keys()) == {
        "ntfy", "annotate_digest", "propose_escalation", "none",
    }


def test_fleet_preset_candidate_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    engine = build_engine(home=str(tmp_path))
    assert engine.candidate_path.endswith(".candidate")
    assert engine.rules_path + ".candidate" == engine.candidate_path
