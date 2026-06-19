"""Tests for the dude-claw telemetry parser + last-tick record builder
(``mini_dudeai.claw_telemetry``).

The claw answers two NATS ``tool_exec`` calls with **free-text** result
strings (firmware-side, no structured JSON — a fork change is deferred):

    device_info -> "Free heap: 17764 bytes, Total heap: 210492 bytes,
                    Uptime: 109368 seconds, WiFi: connected (rssi -37 dBm),
                    IP: <ip>, Chip: ESP32-S3 rev 2, 2 cores, 240 MHz"
    ble_stats   -> "ble_adv_age_s: 0 (advs 767422, uniq 32+, last rssi -59 dBm,
                    restarts 0/0, window 48/320ms)"

The parser runs ONCE at capture time (on the claw-brain box), so /api/status
and the /fleet rollup read structured fields, never the free text. The
honest-failure contract (honest_failure_modes.md): a field that is not present
parses to ``None`` (unknown), never a fabricated ``0``; a failed/absent NATS
reply yields ``None`` for that half plus an explicit error, and the top-level
``ok`` is False — a degraded capture must never read as healthy telemetry.

IP test vectors are SYNTHETIC (MF014/MF015 — no operator LAN IPs in source).
"""
from __future__ import annotations

from mini_dudeai.claw_telemetry import (
    build_tick,
    parse_ble_stats,
    parse_device_info,
)

# Synthetic samples — same SHAPE the firmware emits, sanitized IP.
DI = ("Free heap: 17764 bytes, Total heap: 210492 bytes, Uptime: 109368 "
      "seconds, WiFi: connected (rssi -37 dBm), IP: 10.0.0.5, Chip: "
      "ESP32-S3 rev 2, 2 cores, 240 MHz")
BS = ("ble_adv_age_s: 0 (advs 767422, uniq 32+, last rssi -59 dBm, "
      "restarts 0/0, window 48/320ms)")


class TestParseDeviceInfo:
    def test_full_string_parses_all_fields(self):
        d = parse_device_info(DI)
        assert d["heap_free_bytes"] == 17764
        assert d["heap_total_bytes"] == 210492
        assert d["uptime_s"] == 109368
        assert d["wifi_connected"] is True
        assert d["wifi_rssi_dbm"] == -37
        assert d["chip"] == "ESP32-S3 rev 2"
        assert d["ip"] == "10.0.0.5"

    def test_disconnected_wifi_has_no_rssi(self):
        d = parse_device_info("Free heap: 100 bytes, WiFi: disconnected")
        assert d["wifi_connected"] is False
        assert d["wifi_rssi_dbm"] is None  # honest: unknown, not 0
        assert d["heap_free_bytes"] == 100

    def test_missing_field_is_none_not_zero(self):
        d = parse_device_info("Uptime: 5 seconds")
        assert d["uptime_s"] == 5
        assert d["heap_free_bytes"] is None      # absent -> unknown, never 0
        assert d["heap_total_bytes"] is None

    def test_empty_or_none_returns_none(self):
        assert parse_device_info("") is None
        assert parse_device_info(None) is None
        assert parse_device_info("   ") is None


class TestParseBleStats:
    def test_full_string_parses_all_fields(self):
        b = parse_ble_stats(BS)
        assert b["adv_age_s"] == 0
        assert b["advs"] == 767422
        assert b["uniq"] == "32+"               # keep the '+', don't lie as int
        assert b["last_rssi_dbm"] == -59
        assert b["restarts"] == "0/0"
        assert b["window"] == "48/320ms"

    def test_missing_field_is_none(self):
        b = parse_ble_stats("ble_adv_age_s: 12 (advs 5)")
        assert b["adv_age_s"] == 12
        assert b["advs"] == 5
        assert b["last_rssi_dbm"] is None
        assert b["restarts"] is None

    def test_empty_or_none_returns_none(self):
        assert parse_ble_stats("") is None
        assert parse_ble_stats(None) is None


def _ok(result):
    return {"ok": True, "result": result}


class TestBuildTick:
    def test_both_ok_builds_healthy_tick(self):
        t = build_tick(now=1000.0, host="moc2", device="dudeclaw-01",
                       device_info_reply=_ok(DI), ble_stats_reply=_ok(BS))
        assert t["ok"] is True
        assert t["captured_at"] == 1000.0
        assert t["host"] == "moc2"
        assert t["device"] == "dudeclaw-01"
        assert t["device_info"]["uptime_s"] == 109368
        assert t["ble"]["advs"] == 767422
        assert t["errors"] == {}
        assert "captured_iso" in t

    def test_failed_device_info_is_not_ok_and_carries_error(self):
        # A degraded reply must NOT read as healthy telemetry: device_info is
        # null, the error is recorded, and ok is False (honest_failure_modes #1).
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply={"ok": False, "error": "timeout"},
                       ble_stats_reply=_ok(BS))
        assert t["ok"] is False
        assert t["device_info"] is None
        assert "device_info" in t["errors"]
        assert t["ble"]["advs"] == 767422   # the half that worked is preserved

    def test_none_replies_are_not_ok(self):
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply=None, ble_stats_reply=None)
        assert t["ok"] is False
        assert t["device_info"] is None and t["ble"] is None
        assert set(t["errors"]) == {"device_info", "ble_stats"}

    def test_ok_reply_with_unparseable_result_is_not_ok(self):
        # ok=True but the result string yields nothing -> we couldn't actually
        # read telemetry; do not claim ok.
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply=_ok(""), ble_stats_reply=_ok(BS))
        assert t["device_info"] is None
        assert "device_info" in t["errors"]
        assert t["ok"] is False
