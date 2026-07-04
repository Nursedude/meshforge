"""Tests for scripts/claw_metrics_push.py — the capture-and-paint orchestration.

The parser/record honesty is covered in test_claw_telemetry.py; here we pin the
WRITER contract: a total NATS failure still persists an honest *unreachable*
tick (so /api/status shows claw_unreachable instead of the last good numbers
silently aging into "stale"), and a paint failure still persists the captured
tick before paging. IP test vectors are synthetic (MF014/MF015).
"""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "claw_metrics_push.py")
_spec = importlib.util.spec_from_file_location("claw_metrics_push", _SCRIPT)
cmp_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmp_mod)

DI = ("Free heap: 17764 bytes, Total heap: 210492 bytes, Uptime: 109368 "
      "seconds, WiFi: connected (rssi -37 dBm), IP: 10.0.0.5, Chip: "
      "ESP32-S3 rev 2, 2 cores, 240 MHz")
BS = "ble_adv_age_s: 0 (advs 767422, uniq 32+, last rssi -59 dBm, restarts 0/0)"


class _FakeNC:
    """Context-manager stand-in for NatsConnection."""

    def __init__(self, responses, connect_error=None):
        self._responses = responses
        self._connect_error = connect_error
        self.requests = []

    def __enter__(self):
        if self._connect_error:
            raise self._connect_error
        return self

    def __exit__(self, *a):
        return False

    def request(self, subject, payload):
        d = json.loads(payload)
        self.requests.append(d)
        r = self._responses.get(d.get("tool"))
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Patch out env load, the localhost /api/status read, and the tick path."""
    monkeypatch.setattr(cmp_mod, "_load_claw_env", lambda: {
        "MINI_DUDEAI_NATS_SERVER": "nats://x", "MINI_DUDEAI_CLAW_DEVICE": "dudeclaw-01"})
    monkeypatch.setattr(cmp_mod, "build_rows", lambda: ["row0", "row1"])
    tick_file = tmp_path / "claw_last_tick.json"
    monkeypatch.setattr(cmp_mod, "_tick_path", lambda: str(tick_file))
    return tick_file


def _install_conn(monkeypatch, fake):
    monkeypatch.setattr(cmp_mod, "NatsConnection", lambda *a, **k: fake)
    return fake


def test_happy_path_writes_ok_tick(wired, monkeypatch):
    _install_conn(monkeypatch, _FakeNC({
        "device_info": {"ok": True, "result": DI},
        "ble_stats": {"ok": True, "result": BS},
        "display_print": {"ok": True},
    }))
    rc = cmp_mod.main()
    assert rc == 0
    tick = json.loads(wired.read_text())
    assert tick["ok"] is True
    assert tick["device_info"]["uptime_s"] == 109368
    assert tick["ble"]["advs"] == 767422
    assert tick["host"] and tick["device"] == "dudeclaw-01"


def test_connect_failure_writes_unreachable_tick_and_pages(wired, monkeypatch):
    # Total NATS failure: an honest unreachable tick is still persisted, AND
    # the cron pages (SystemExit) so claw death is never silent.
    _install_conn(monkeypatch, _FakeNC({}, connect_error=cmp_mod.NatsError("refused")))
    with pytest.raises(SystemExit):
        cmp_mod.main()
    tick = json.loads(wired.read_text())
    assert tick["ok"] is False
    assert tick["device_info"] is None and tick["ble"] is None
    assert set(tick["errors"]) == {"device_info", "ble_stats"}


def test_paint_failure_still_persists_captured_tick_and_pages(wired, monkeypatch):
    # Capture succeeded but the OLED paint was refused: persist the good tick
    # (display stays current) yet still page on the paint failure.
    _install_conn(monkeypatch, _FakeNC({
        "device_info": {"ok": True, "result": DI},
        "ble_stats": {"ok": True, "result": BS},
        "display_print": {"ok": False, "error": "oled busy"},
    }))
    with pytest.raises(SystemExit):
        cmp_mod.main()
    tick = json.loads(wired.read_text())
    assert tick["ok"] is True
    assert tick["device_info"]["uptime_s"] == 109368


def test_half_unreachable_tick_is_not_ok(wired, monkeypatch):
    # device_info answers, ble_stats times out: tick not ok, ble error recorded.
    _install_conn(monkeypatch, _FakeNC({
        "device_info": {"ok": True, "result": DI},
        "ble_stats": cmp_mod.NatsError("timeout"),
        "display_print": {"ok": True},
    }))
    rc = cmp_mod.main()
    assert rc == 0
    tick = json.loads(wired.read_text())
    assert tick["ok"] is False
    assert tick["device_info"]["uptime_s"] == 109368
    assert tick["ble"] is None and "ble_stats" in tick["errors"]


class TestBrainTierPush:
    """display_tier wiring (firmware 0.4.0+dudeclaw.15): the glyph is pushed
    only when _probe_tier proved a tier; ladder decisions themselves are
    covered in test_claw_telemetry.py::TestComputeBrainTier."""

    def _nc_ok(self):
        return _FakeNC({
            "device_info": {"ok": True, "result": DI},
            "ble_stats": {"ok": True, "result": BS},
            "display_print": {"ok": True},
            "display_tier": {"ok": True, "result": "Brain tier set: F"},
        })

    def test_tier_pushed_and_recorded_in_tick(self, wired, monkeypatch):
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        fake = _install_conn(monkeypatch, self._nc_ok())
        rc = cmp_mod.main()
        assert rc == 0
        tier_reqs = [r for r in fake.requests if r.get("tool") == "display_tier"]
        assert tier_reqs == [{"tool": "display_tier", "tier": "F"}]
        tick = json.loads(wired.read_text())
        assert tick["brain_tier"] == "F"

    def test_unprovable_tier_pushes_nothing(self, wired, monkeypatch):
        # None = push NOTHING; the firmware decays the glyph to SOLO — the
        # pusher never fabricates a tier just to have something to send.
        monkeypatch.setattr(cmp_mod, "_probe_tier",
                            lambda env: (None, "no tier provable"))
        fake = _install_conn(monkeypatch, self._nc_ok())
        rc = cmp_mod.main()
        assert rc == 0
        assert not [r for r in fake.requests if r.get("tool") == "display_tier"]
        tick = json.loads(wired.read_text())
        assert tick["brain_tier"] is None

    def test_pre_dudeclaw15_firmware_warns_but_does_not_page(self, wired,
                                                             monkeypatch):
        # MF pulls can land before a claw reflash: a firmware without the
        # tool must not page the operator; the glyph simply stays absent.
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        nc = self._nc_ok()
        nc._responses["display_tier"] = {
            "ok": False, "error": "Error: unknown tool 'display_tier'"}
        _install_conn(monkeypatch, nc)
        assert cmp_mod.main() == 0

    def test_real_tier_refusal_pages(self, wired, monkeypatch):
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        nc = self._nc_ok()
        nc._responses["display_tier"] = {"ok": False, "error": "oled busy"}
        _install_conn(monkeypatch, nc)
        with pytest.raises(SystemExit):
            cmp_mod.main()

    def test_row_paint_failure_skips_tier_push(self, wired, monkeypatch):
        # Rows refused -> the claw's paint path is already broken; page once,
        # don't stack a second failing call onto it.
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        nc = self._nc_ok()
        nc._responses["display_print"] = {"ok": False, "error": "oled busy"}
        fake = _install_conn(monkeypatch, nc)
        with pytest.raises(SystemExit):
            cmp_mod.main()
        assert not [r for r in fake.requests if r.get("tool") == "display_tier"]


class TestProbeTier:
    """_probe_tier I/O honesty: unset URL disables the feed (never claims
    L/R without having LOOKED at the frontier); an unreachable SLO IS
    evidence and falls down the ladder. Endpoints here are synthetic."""

    def test_unset_slo_url_disables_feed(self):
        tier, note = cmp_mod._probe_tier({})
        assert tier is None
        assert "disabled" in note

    def test_frontier_proven_from_slo_verdict(self, monkeypatch):
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid:5000/fleet/slo",
               "MINI_DUDEAI_OLLAMA_URL": "http://llm.invalid:11434"}

        def fake_fetch(url, timeout=0):
            return {"schedules": {"verdicts": {"available": True, "jobs": [
                {"name": "mini_cadence", "status": "OK", "stale": False,
                 "age_s": 60.0}]}}}, None

        monkeypatch.setattr(cmp_mod, "fetch_json", fake_fetch)
        # ollama reachability now rides THE shared probe (chat_compiler)
        monkeypatch.setattr(cmp_mod, "probe_ollama",
                            lambda url, timeout_s=6: (True, "ollama 0.30"))
        tier, note = cmp_mod._probe_tier(env)
        assert tier == "F"

    def test_unreachable_slo_falls_to_local(self, monkeypatch):
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid:5000/fleet/slo",
               "MINI_DUDEAI_OLLAMA_URL": "http://llm.invalid:11434"}

        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: (None, "connection refused"))
        monkeypatch.setattr(cmp_mod, "probe_ollama",
                            lambda url, timeout_s=6: (True, "ollama 0.30"))
        tier, note = cmp_mod._probe_tier(env)
        assert tier == "L"

    def test_list_shaped_slo_fields_fall_through_not_crash(self, tmp_path,
                                                           monkeypatch):
        # 2026-07-04 hardening: `or {}` only guards None — a truthy wrong
        # type (schedules as a list) used to AttributeError the whole push
        # into a FALSE claw-liveness page.
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid/fleet/slo",
               "MINI_DUDEAI_HOME": str(tmp_path)}  # no state file -> no R
        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: ({"schedules": ["oops"]},
                                                    None))
        tier, note = cmp_mod._probe_tier(env)  # must not raise
        assert tier is None

    def test_rules_tier_reads_state_from_mini_dudeai_home(self, tmp_path,
                                                          monkeypatch):
        # 2026-07-04 hardening: the WRITER (claw daemon via resolve_home)
        # honors MINI_DUDEAI_HOME; the reader must follow the same
        # precedence or a homed-elsewhere daemon makes tier R unprovable.
        env = {"MINI_DUDEAI_TIER_SLO_URL": "http://brain.invalid/fleet/slo",
               "MINI_DUDEAI_HOME": str(tmp_path)}
        monkeypatch.setattr(cmp_mod, "fetch_json",
                            lambda url, timeout=0: (None, "refused"))
        (tmp_path / cmp_mod.CLAW_STATE_BASENAME).write_text("{}")
        tier, note = cmp_mod._probe_tier(env)
        assert tier == "R"


class TestUnknownToolCaseFolding:
    """2026-07-04 hardening: the pre-reflash detection must survive OTHER
    firmware vintages' casing — 'Unknown Tool: ...' must warn, not page."""

    def test_recased_unknown_tool_error_still_warns_not_pages(
            self, wired, monkeypatch):
        monkeypatch.setattr(cmp_mod, "_probe_tier", lambda env: ("F", "test"))
        nc = _FakeNC({
            "device_info": {"ok": True, "result": DI},
            "ble_stats": {"ok": True, "result": BS},
            "display_print": {"ok": True},
            "display_tier": {"ok": False,
                             "error": "Unknown Tool: display_tier"},
        })
        _install_conn(monkeypatch, nc)
        assert cmp_mod.main() == 0
