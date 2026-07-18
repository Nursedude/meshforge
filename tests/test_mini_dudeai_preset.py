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
    assert "boot_health" in source_names


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
    source_error noise from an unreachable federator / missing digest.
    boot_health stays: a hard reset is fleet-relevant on gateways too
    (unexpected-reboot wiring, 2026-06-06)."""
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    engine = build_engine(
        home=str(tmp_path),
        watchdog_path=str(tmp_path / "watchdog.json"),
        enable_federation=False,
        enable_digest=False,
    )
    names = [getattr(s, "name", "?") for s in engine.sources]
    assert names == ["watchdog", "boot_health"]


def test_source_gating_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("MINI_DUDEAI_ENABLE_FEDERATION", "0")
    monkeypatch.setenv("MINI_DUDEAI_ENABLE_DIGEST", "0")
    engine = build_engine(home=str(tmp_path), watchdog_path=str(tmp_path / "w.json"))
    assert [getattr(s, "name", "?") for s in engine.sources] == ["watchdog", "boot_health"]


def test_watchdog_wired_by_default(tmp_path, monkeypatch):
    """The watchdog source needs NO opt-in — every MeshForge box gets it, so
    the source_error_watchdog dead-watchdog rule keeps its feed."""
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    engine = build_engine(
        home=str(tmp_path),
        watchdog_path=str(tmp_path / "w.json"),
        enable_federation=False,
        enable_digest=False,
    )
    assert "watchdog" in [getattr(s, "name", "?") for s in engine.sources]


def test_watchdog_env_gate_off_for_no_mf_watchdog_box(tmp_path, monkeypatch):
    """MINI_DUDEAI_ENABLE_WATCHDOG=0 — the MeshAnchor-only box class: no MF
    watchdog exists there by DESIGN, so the source must not be wired (else
    source_error_watchdog pages forever and src_errors pins at 1). Declared
    absent ≠ unobservable ≠ error (meshanchor-server enrollment, 2026-07-14)."""
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("MINI_DUDEAI_ENABLE_WATCHDOG", "0")
    engine = build_engine(
        home=str(tmp_path),
        watchdog_path=str(tmp_path / "w.json"),
        enable_federation=False,
        enable_digest=False,
    )
    names = [getattr(s, "name", "?") for s in engine.sources]
    assert "watchdog" not in names
    assert "boot_health" in names    # reboot detection stays — fleet-relevant everywhere


# === boot_health wiring (unexpected-reboot, 2026-06-06) ============


def test_boot_health_wired_by_default_with_ssot_paths(tmp_path, monkeypatch):
    """BootHealthSource defaults ON for every box, and the clean_exit_path is
    a single source of truth: the SAME path feeds the source (reader) and the
    engine (writer-on-graceful-stop). A path mismatch silently breaks the
    whole mechanism — every planned reboot would page as a crash."""
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    engine = build_engine(
        home=str(tmp_path),
        watchdog_path=str(tmp_path / "watchdog.json"),
        enable_federation=False,
        enable_digest=False,
    )
    bh = next(s for s in engine.sources if getattr(s, "name", "") == "boot_health")
    home = str(tmp_path)
    assert bh.clean_exit_path == os.path.join(home, "mini_dudeai_clean_exit")
    assert engine.clean_exit_path == bh.clean_exit_path          # the SSOT pairing
    assert bh.state_path == os.path.join(home, "mini_dudeai_state.json")
    assert bh.assessment_path == os.path.join(home, "mini_dudeai_boot_assessment.json")
    assert bh.power_log_path == os.path.join(home, "power_history.log")


def test_boot_health_env_gate_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("MINI_DUDEAI_ENABLE_BOOT_HEALTH", "0")
    engine = build_engine(
        home=str(tmp_path),
        watchdog_path=str(tmp_path / "w.json"),
        enable_federation=False,
        enable_digest=False,
    )
    names = [getattr(s, "name", "?") for s in engine.sources]
    assert "boot_health" not in names
    assert engine.clean_exit_path is None    # gate covers writer AND reader


# ------------------------------------------------- preset auto-resolution

def test_resolve_preset_name_passthrough():
    from mini_dudeai.daemon import _resolve_preset_name
    assert _resolve_preset_name("standalone") == "standalone"
    assert _resolve_preset_name("meshforge_fleet") == "meshforge_fleet"
    assert _resolve_preset_name("my.dotted.preset") == "my.dotted.preset"


def test_resolve_preset_auto_follows_fleet_declaration(tmp_path, monkeypatch):
    """--preset auto reads the SAME fleet_hosts SSOT the membership wizard
    writes: hosts declared -> fleet preset, none -> standalone."""
    from mini_dudeai.daemon import _resolve_preset_name
    hosts = tmp_path / "fleet_hosts"
    hosts.write_text("boxa\nboxb\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(hosts))
    assert _resolve_preset_name("auto") == "meshforge_fleet"


def test_resolve_preset_auto_standalone_when_no_declaration(tmp_path, monkeypatch):
    from mini_dudeai.daemon import _resolve_preset_name
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(tmp_path / "absent"))
    assert _resolve_preset_name("auto") == "standalone"
