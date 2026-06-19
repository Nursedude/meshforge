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
