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

import pytest

from mini_dudeai.claw_telemetry import (
    CADENCE_VERDICT_NAME,
    build_tick,
    compute_brain_tier,
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


class TestComputeBrainTier:
    """The display_tier ladder claims only what a probe PROVED (research doc
    dudeclaw_local_brain_2026_07_03 §5.2). Conservative direction: any
    ambiguity falls DOWN the ladder, never up."""

    def _cadence(self, status="OK", stale=False, age_s=120.0):
        return {"name": CADENCE_VERDICT_NAME, "status": status,
                "stale": stale, "age_s": age_s}

    def test_frontier_when_cadence_ok_and_fresh(self):
        tier, note = compute_brain_tier([self._cadence()], ollama_ok=True,
                                        rules_age_s=10.0)
        assert tier == "F"
        assert "cadence" in note

    def test_stale_cadence_is_not_frontier(self):
        tier, _ = compute_brain_tier([self._cadence(stale=True)],
                                     ollama_ok=True, rules_age_s=10.0)
        assert tier == "L"

    def test_failed_cadence_is_not_frontier(self):
        tier, _ = compute_brain_tier([self._cadence(status="FAIL(1)")],
                                     ollama_ok=True, rules_age_s=10.0)
        assert tier == "L"

    def test_missing_stale_field_is_not_proven_fresh(self):
        # Unobservable freshness must never read as fresh (#80 class).
        job = {"name": CADENCE_VERDICT_NAME, "status": "OK"}
        tier, _ = compute_brain_tier([job], ollama_ok=True, rules_age_s=10.0)
        assert tier == "L"

    def test_local_when_no_verdicts_but_ollama_answers(self):
        tier, note = compute_brain_tier([], ollama_ok=True, rules_age_s=10.0)
        assert tier == "L"
        assert "ollama" in note

    def test_rules_when_no_llm_but_state_fresh(self):
        tier, note = compute_brain_tier([], ollama_ok=False, rules_age_s=45.0)
        assert tier == "R"
        assert "state fresh" in note

    def test_nothing_provable_is_none_not_r(self):
        # Stale state file proves nothing; None means "push nothing" and the
        # glass decays to SOLO — never a fabricated R.
        tier, note = compute_brain_tier([], ollama_ok=False,
                                        rules_age_s=99999.0)
        assert tier is None
        assert "SOLO" in note

    def test_absent_state_file_is_none(self):
        tier, _ = compute_brain_tier([], ollama_ok=False, rules_age_s=None)
        assert tier is None

    def test_negative_state_age_is_not_fresh(self):
        # A clock step can make mtime "newer than now" — forged freshness
        # must not prove the rules tier (RTC-less fleet discipline).
        tier, _ = compute_brain_tier([], ollama_ok=False, rules_age_s=-30.0)
        assert tier is None

    def test_other_verdicts_do_not_prove_frontier(self):
        jobs = [{"name": "synth_soak", "status": "OK", "stale": False}]
        tier, _ = compute_brain_tier(jobs, ollama_ok=False, rules_age_s=5.0)
        assert tier == "R"


class TestCadenceVerdictNamePin:
    def test_verdict_name_matches_launch_script_declaration(self):
        """honest_failure_modes #5 (2026-07-04 review fix): the F-tier
        evidence key and the launch script's declared cron-verdict name are
        two consumers of one artifact — pinned together so a rename can't
        silently kill tier F while the frontier is healthy."""
        import re
        from pathlib import Path
        sh = (Path(__file__).parent.parent / "scripts"
              / "mini_cadence_launch.sh").read_text()
        m = re.search(r'^CRON_VERDICT_NAME="([^"]+)"', sh, re.M)
        assert m, "mini_cadence_launch.sh must declare CRON_VERDICT_NAME"
        assert m.group(1) == CADENCE_VERDICT_NAME


class TestSecondaryTickBasename:
    """W5.1 multi-claw: the shape owner also owns the secondary naming."""

    def test_formula(self):
        from mini_dudeai.claw_telemetry import secondary_tick_basename
        assert secondary_tick_basename("dudeclaw-02") == \
            "claw_last_tick.dudeclaw-02.json"

    def test_never_collides_with_primary(self):
        # even a device literally named "json" yields a two-dot basename,
        # so the primary single-dot file can never be clobbered
        from mini_dudeai.claw_telemetry import (CLAW_TICK_BASENAME,
                                                secondary_tick_basename)
        for dev in ("json", "last_tick", "claw_last_tick"):
            assert secondary_tick_basename(dev) != CLAW_TICK_BASENAME

    def test_path_hostile_chars_sanitized(self):
        from mini_dudeai.claw_telemetry import secondary_tick_basename
        assert secondary_tick_basename("../evil/dev") == \
            "claw_last_tick.---evil-dev.json"

    def test_empty_device_raises(self):
        from mini_dudeai.claw_telemetry import secondary_tick_basename
        with pytest.raises(ValueError):
            secondary_tick_basename("   ")


class TestAccessoryAbsenceIsNotFailure:
    """2026-07-19 (structural-dark row 7): a claw with no BLE scanner is a
    correctly-built device, not a broken one.

    ``ok`` used to be an AND over device_info AND ble, so dudeclaw-02 — which
    has no BLE radio — sat at ``ok: false`` in every tick forever. A flag that
    is permanently false is not a conservative default: it trains every reader
    (human and probe) to ignore it, so a REAL failure hides inside it. Absence
    of a capability is not an error (honest_failure_modes #1/#3).
    """

    def test_missing_ble_scanner_still_reads_reachable_and_ok(self):
        t = build_tick(now=1.0, host="moc2", device="dudeclaw-02",
                       device_info_reply=_ok(DI),
                       ble_stats_reply={"ok": False,
                                        "error": "no BLE scanner on this device"})
        assert t["reachable"] is True
        assert t["ok"] is True, "an absent accessory must not read as a failure"
        assert t["ble"] is None

    def test_the_accessory_failure_still_leaves_a_witness(self):
        """Not-a-failure must not mean not-recorded: the miss is still in
        errors AND named in degraded_optional (honest_failure_modes #9), so a
        real BLE regression stays visible instead of being averaged away."""
        t = build_tick(now=1.0, host="moc2", device="dudeclaw-02",
                       device_info_reply=_ok(DI),
                       ble_stats_reply={"ok": False, "error": "scanner wedged"})
        assert "ble_stats" in t["errors"]
        assert t["degraded_optional"] == ["ble_stats"]
        # ok stays True (the DEVICE answered); accessory health is reported
        # separately rather than collapsed into one boolean this layer cannot
        # disambiguate from a permanent hardware absence.
        assert t["ok"] is True

    def test_unreachable_device_is_never_ok_regardless_of_accessories(self):
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply={"ok": False, "error": "no reply"},
                       ble_stats_reply=_ok(BS))
        assert t["reachable"] is False and t["ok"] is False


class TestBatteryCapture:
    """Battery became a first-class fleet metric 2026-07-19: a battery claw
    drained to 2.41 V and died with the voltage recorded nowhere the fleet
    could see it."""

    def test_battery_parsed_into_the_tick(self):
        t = build_tick(now=1.0, host="moc2", device="dudeclaw-02",
                       device_info_reply=_ok(DI), ble_stats_reply=_ok(BS),
                       battery_reply=_ok("Battery: 4.06 V (adc 829 mV)"))
        assert t["battery"]["volts"] == 4.06
        assert t["ok"] is True

    def test_adc_millivolts_are_not_mistaken_for_the_pack_voltage(self):
        from mini_dudeai.claw_telemetry import parse_battery
        assert parse_battery("Battery: 2.41 V (adc 490 mV)")["volts"] == 2.41

    def test_unreadable_battery_is_none_never_zero(self):
        """A fabricated 0.0 V would breach every low-battery spec and read as a
        dying node — the degraded value must not overlap the healthy domain."""
        from mini_dudeai.claw_telemetry import parse_battery
        assert parse_battery("sensor busy") is None
        assert parse_battery("") is None
        assert parse_battery(None) is None

    def test_absent_battery_reply_leaves_no_error_and_stays_ok(self):
        """A claw with no gauge simply isn't asked/answered — that is not a
        capture failure."""
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply=_ok(DI), ble_stats_reply=_ok(BS),
                       battery_reply=None)
        assert t["battery"] is None and t["ok"] is True
        assert "battery" not in t["errors"]
