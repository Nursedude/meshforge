"""Fleet preset tests — verify the wiring matches today's ~/mini_dudeai.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai.presets.meshforge_fleet import build_engine, _watchdog_extractor


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


def test_watchdog_extractor_reads_class_key():
    """Regression: on-disk watchdog.json uses key 'class' (signal_to_dict), not
    'cls'. Reading 'cls' silently disabled every signal_class rule."""
    # exact on-disk shape from utils.watchdog_probes.signal_to_dict
    data = {"signals": [{
        "class": "tracer_peer_unreachable", "subject": "primary-box",
        "severity": "wedge", "detail": "3 consecutive failed tracer fires",
        "first_seen": 1780039930.0,
        "extra": {"leading_fail": 3, "tier": "persistent"},
    }]}
    out = _watchdog_extractor(data)
    assert len(out) == 1
    assert out[0]["class"] == "tracer_peer_unreachable"  # NOT "unknown"
    assert out[0]["subject"] == "primary-box"
    assert out[0]["severity"] == "wedge"


def test_watchdog_extractor_legacy_cls_fallback():
    out = _watchdog_extractor({"signals": [{"cls": "main_thread_wedge", "subject": "x"}]})
    assert out[0]["class"] == "main_thread_wedge"


def test_gateway_mode_disables_federation_and_digest(tmp_path, monkeypatch):
    """Gateway boxes (no :5000, no digest) wire watchdog-only — no per-tick
    source_error noise from an unreachable federator / missing digest."""
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    engine = build_engine(
        home=str(tmp_path),
        watchdog_path=str(tmp_path / "watchdog.json"),
        enable_federation=False,
        enable_digest=False,
    )
    names = [getattr(s, "name", "?") for s in engine.sources]
    assert names == ["watchdog"]


def test_source_gating_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("MINI_DUDEAI_ENABLE_FEDERATION", "0")
    monkeypatch.setenv("MINI_DUDEAI_ENABLE_DIGEST", "0")
    engine = build_engine(home=str(tmp_path), watchdog_path=str(tmp_path / "w.json"))
    assert [getattr(s, "name", "?") for s in engine.sources] == ["watchdog"]
