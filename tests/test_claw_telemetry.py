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
# device_info from firmware carrying the running version (added 2026-08-30).
# Version leads because snprintf truncates the TAIL and this is the field an
# OTA push must read back — shape copied from the firmware's format string.
DI_VERSIONED = ("Version: 0.4.0+dudeclaw.19, Free heap: 17764 bytes, "
                "Total heap: 210492 bytes, Min free heap: 9012 bytes, "
                "Max alloc block: 6144 bytes, Reset reason: poweron, "
                "Uptime: 109368 seconds, WiFi: connected (rssi -37 dBm), "
                "IP: 10.0.0.5, Chip: ESP32-S3 rev 2, 2 cores, 240 MHz")
# device_info from firmware carrying the board MAC (added +dudeclaw.21).
# MAC sits SECOND, behind Version, because both are identity fields and
# snprintf truncates the tail — shape copied from the firmware format string.
DI_MAC = ("Version: 0.4.0+dudeclaw.21, MAC: AA:BB:CC:DD:EE:FF, "
          "Free heap: 17764 bytes, Total heap: 210492 bytes, "
          "Min free heap: 9012 bytes, Max alloc block: 6144 bytes, "
          "Reset reason: poweron, Uptime: 109368 seconds, "
          "WiFi: connected (rssi -37 dBm), IP: 10.0.0.5, Chip: "
          "ESP32-S3 rev 2, 2 cores, 240 MHz")
# device_info from firmware carrying the heap-pressure + reset-reason fields.
DI_PRESSURE = ("Free heap: 17764 bytes, Total heap: 210492 bytes, "
               "Min free heap: 9012 bytes, Max alloc block: 6144 bytes, "
               "Reset reason: PANIC, Uptime: 109368 seconds, "
               "WiFi: connected (rssi -37 dBm), IP: 10.0.0.5, Chip: "
               "ESP32-S3 rev 2, 2 cores, 240 MHz")


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

    def test_mac_parses_and_keeps_the_firmware_casing(self):
        # NOT normalized: the value is compared byte-for-byte against the
        # host's /dev/serial/by-id/..._<MAC>-if00 name, so a helpful lowercase
        # fixup here would hide a real mismatch.
        d = parse_device_info(DI_MAC)
        assert d["mac"] == "AA:BB:CC:DD:EE:FF"
        assert d["version"] == "0.4.0+dudeclaw.21"

    def test_mac_absent_on_older_firmware_is_none(self):
        # UNKNOWN identity, never "matches" — a pre-.21 claw's port assignment
        # is still one-sided, which is exactly what the field exists to fix.
        assert parse_device_info(DI_VERSIONED)["mac"] is None
        assert parse_device_info(DI)["mac"] is None

    def test_truncated_mac_reads_as_absent_not_as_a_short_address(self):
        # A clipped reply must not yield a partial MAC: that would compare
        # unequal against the by-id name and read as the WRONG BOARD — a
        # degraded value landing inside the healthy domain.
        clipped = "Version: 0.4.0+dudeclaw.21, MAC: AA:BB:CC:DD"
        assert parse_device_info(clipped)["mac"] is None

    def test_mac_is_field_anchored_not_matched_mid_token(self):
        # "MAC:" must anchor at a field boundary like every other reader here.
        d = parse_device_info("Chip: ESP32-S3 rev 2, MAC: 11:22:33:44:55:66")
        assert d["mac"] == "11:22:33:44:55:66"

    def test_empty_or_none_returns_none(self):
        assert parse_device_info("") is None
        assert parse_device_info(None) is None
        assert parse_device_info("   ") is None

    def test_heap_pressure_fields_parse(self):
        d = parse_device_info(DI_PRESSURE)
        assert d["heap_free_bytes"] == 17764
        assert d["heap_min_free_bytes"] == 9012
        assert d["heap_max_alloc_bytes"] == 6144
        assert d["reset_reason"] == "PANIC"

    def test_min_free_never_shadows_live_free_heap(self):
        """'Free heap:' is a SUBSTRING of 'Min free heap:' and the patterns
        are IGNORECASE. Unanchored, a reordered firmware string would make
        the low-water mark masquerade as the live value — the device would
        look like it was sitting at its worst-ever heap forever, or (worse,
        reversed) the live value would hide a near-exhaustion mark."""
        reordered = ("Min free heap: 9012 bytes, Free heap: 17764 bytes, "
                     "Total heap: 210492 bytes")
        d = parse_device_info(reordered)
        assert d["heap_free_bytes"] == 17764     # NOT 9012
        assert d["heap_min_free_bytes"] == 9012

    def test_old_firmware_lacks_pressure_fields_is_unknown_not_healthy(self):
        """Firmware predating these fields must read as unknown. A missing
        reset reason is not a clean boot, and a missing low-water mark is not
        a comfortable one (honest_failure_modes #2)."""
        d = parse_device_info(DI)
        assert d["heap_min_free_bytes"] is None
        assert d["heap_max_alloc_bytes"] is None
        assert d["reset_reason"] is None
        assert d["heap_free_bytes"] == 17764     # old fields still parse

    def test_version_is_parsed_when_the_firmware_reports_it(self):
        """The running image must be POLLABLE: before 2026-08-30 the version
        was only announced at boot, so an OTA push had no way to ask which
        image it was talking to. Firmware emits it FIRST so a truncated reply
        keeps the field the push reads back."""
        d = parse_device_info(DI_VERSIONED)
        assert d["version"] == "0.4.0+dudeclaw.19"

    def test_leading_version_does_not_break_the_other_fields(self):
        """Prepending a field shifts every other one off start-of-string. The
        readers are anchored on '(?:^|,)\\s*' so they still match after a
        comma — this pins that, because a silent regression here would blank
        heap/uptime/reset fleet-wide the moment the new firmware shipped."""
        d = parse_device_info(DI_VERSIONED)
        assert d["heap_free_bytes"] == 17764
        assert d["uptime_s"] == 109368
        assert d["reset_reason"] == "poweron"
        assert d["chip"] == "ESP32-S3 rev 2"

    def test_firmware_without_version_is_unknown_not_assumed_current(self):
        """A claw whose image cannot be named is a claw an OTA cannot verify.
        None says so; a blank or an assumed-current string would not."""
        assert parse_device_info(DI)["version"] is None


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


class TestInstanceBasename:
    """W5.1 multi-claw: the shape owner also owns the mini-instance artifact
    naming, so the daemon writer and the pusher R-tier reader share ONE
    formula (honest_failure_modes #5)."""

    def test_inserts_suffix_before_extension(self):
        from mini_dudeai.claw_telemetry import instance_basename
        assert instance_basename("mini_dudeai_claw_state.json", "dudeclaw-02") \
            == "mini_dudeai_claw_state.dudeclaw-02.json"

    def test_multi_char_extension_preserved(self):
        from mini_dudeai.claw_telemetry import instance_basename
        assert instance_basename(
            "mini_dudeai_claw_history.jsonl", "dudeclaw-02") \
            == "mini_dudeai_claw_history.dudeclaw-02.jsonl"

    def test_extensionless_basename_appends(self):
        from mini_dudeai.claw_telemetry import instance_basename
        assert instance_basename("claw_brief", "dudeclaw-02") \
            == "claw_brief.dudeclaw-02"

    def test_never_collides_with_primary(self):
        from mini_dudeai.claw_telemetry import instance_basename
        primary = "mini_dudeai_claw_state.json"
        assert instance_basename(primary, "dudeclaw-02") != primary

    def test_path_hostile_chars_sanitized(self):
        from mini_dudeai.claw_telemetry import instance_basename
        assert instance_basename("mini_dudeai_claw_state.json", "../evil/dev") \
            == "mini_dudeai_claw_state.---evil-dev.json"

    def test_empty_instance_raises(self):
        from mini_dudeai.claw_telemetry import instance_basename
        with pytest.raises(ValueError):
            instance_basename("mini_dudeai_claw_state.json", "   ")


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

    def test_required_half_missing_is_never_ok_even_when_accessories_answer(self):
        """Renamed + flipped 2026-07-26: this test used to assert
        ``reachable is False`` here, which PINNED the defect below — its own
        fixture has ble_stats answering, and a device that answers is not
        unreachable. ``ok`` is the flag that must stay False (required half
        missed); ``reachable`` is about the DEVICE."""
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply={"ok": False, "error": "no reply"},
                       ble_stats_reply=_ok(BS))
        assert t["ok"] is False
        assert t["reachable"] is True, "the device answered ble_stats"


LORA = ("mesh_heard_age_s: 3, heard 225114 pkts, crc_err 1996, runts 1, "
        "from !58835c1f, rssi -100 dBm, snr 4.5")


class TestReachableMeansTheDeviceAnswered:
    """``reachable`` must mean "the DEVICE answered", not "my one request
    succeeded" (2026-07-26).

    Live defect, moc2: ``reachable`` was derived from ``device_info`` ALONE
    with no retry, so a single timed-out request produced ``reachable: false``
    — which ``probe_claw_device_dark`` renders as *"the DEVICE is silent ...
    look at wifi, the USB feed, or the node itself"*. The tick that triggered
    it carried, in the SAME capture: ble adv_age_s 0, battery 4.18 V, and lora
    reporting a packet heard 3 s earlier, against a claw with ~22 days of
    monotone uptime. Three of four requests answered and it still paged the
    operator at hardware.

    A timed-out request is an observation-channel failure, not evidence about
    the device (honest_failure_modes #1/#2 — degraded state must not map to a
    valid-looking definitive value). Any sibling reply is POSITIVE proof the
    device answered, and it is already sitting in the same record.
    """

    def test_ble_reply_proves_the_device_answered(self):
        t = build_tick(now=1.0, host="moc2", device="dudeclaw-01",
                       device_info_reply={"ok": False,
                                          "error": "timed out after 8.0s"},
                       ble_stats_reply=_ok(BS))
        assert t["reachable"] is True
        assert t["ok"] is False, "the required half still missed"
        assert "device_info" in t["errors"], "the miss keeps its witness (#9)"

    def test_lora_alone_proves_it_the_live_moc2_tick(self):
        """The exact shape of the 2026-07-26 09:00:02 tick: only the
        over-the-air witness and the gauge came back."""
        t = build_tick(now=1.0, host="moc2", device="dudeclaw-01",
                       device_info_reply={"ok": False,
                                          "error": "timed out after 8.0s"},
                       ble_stats_reply={"ok": False, "error": "timed out"},
                       battery_reply=_ok("Battery: 4.18 V (adc 853 mV)"),
                       lora_reply=_ok(LORA))
        assert t["reachable"] is True
        assert t["lora"]["heard_age_s"] == 3

    def test_total_silence_is_still_dark(self):
        """The fix must not blind the probe: when NOTHING answers, that is a
        real dark device and it must still read False."""
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply=None, ble_stats_reply=None,
                       battery_reply=None, lora_reply=None)
        assert t["reachable"] is False
        assert t["ok"] is False

    def test_every_half_timing_out_is_dark(self):
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply={"ok": False, "error": "timed out"},
                       ble_stats_reply={"ok": False, "error": "timed out"},
                       battery_reply={"ok": False, "error": "timed out"},
                       lora_reply={"ok": False, "error": "timed out"})
        assert t["reachable"] is False

    def test_answered_names_which_halves_replied(self):
        """A bare boolean is what got us here — record WHICH halves answered so
        the next reader can see the evidence without re-deriving it."""
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply={"ok": False, "error": "timed out"},
                       ble_stats_reply=_ok(BS),
                       lora_reply=_ok(LORA))
        assert t["answered"] == ["ble", "lora"]

    def test_healthy_tick_still_reachable_and_ok(self):
        t = build_tick(now=1.0, host="moc2", device="d",
                       device_info_reply=_ok(DI), ble_stats_reply=_ok(BS))
        assert t["reachable"] is True and t["ok"] is True
        assert t["answered"] == ["device_info", "ble"]


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


class TestLoraWatchList:
    """Per-id last-heard — the field that closes the RF-ears blind spot (2026-07-29).

    `mesh_heard_age_s` answers "is the channel silent?". It CANNOT answer "is our
    own transmitter reaching the air?" — measured that day: neighbours chattering
    at 6-8 pkt/min held heard_age_s at 5 s on both claws, so a dead PA on our own
    gateway would leave every silence check clean. The watch list reports each
    tracked node id separately.

    THREE states must stay distinguishable. Merging any two rebuilds the blind
    spot, so each is pinned here.
    """

    def test_absent_field_is_none_not_empty(self):
        """Old firmware / no ids configured: we know NOTHING about our own
        transmitter. That must not read as an empty set of problems."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats("mesh_heard_age_s: 5 (heard 1300 pkts, crc_err 8, "
                             "runts 0, last from=!16cd7438 to=!ffffffff "
                             "ch=0x08 rssi=-104 snr=4.2)")
        assert d is not None
        assert d["watched"] is None, "absent watch list must be None, not {}"

    def test_never_heard_is_never_not_zero(self):
        """THE signal. A watched id not heard since radio start reports never —
        age_s 0 would read as 'heard just now', which is the exact
        degraded-value-overlaps-healthy trap."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats("mesh_heard_age_s: 5 (heard 1300 pkts, crc_err 8, "
                             "runts 0, last from=!16cd7438 to=!ffffffff ch=0x08 "
                             "rssi=-104 snr=4.2) watch=!32962f10:never")
        w = d["watched"]
        assert w is not None
        assert w["!32962f10"]["never"] is True
        assert w["!32962f10"]["age_s"] is None, "never-heard must NOT be age 0"

    def test_heard_id_carries_age_pkts_rssi(self):
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats("mesh_heard_age_s: 5 (heard 1300 pkts, crc_err 8, "
                             "runts 0, last from=!16cd7438 to=!ffffffff ch=0x08 "
                             "rssi=-104 snr=4.2) watch=!32962f10:12/45@-97")
        w = d["watched"]["!32962f10"]
        assert w["age_s"] == 12 and w["pkts"] == 45
        assert w["rssi_dbm"] == -97 and w["never"] is False

    def test_mixed_heard_and_never(self):
        """The realistic case: one fleet radio heard, another silent. The silent
        one is the finding and must survive alongside the healthy one."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats("mesh_heard_age_s: 5 (heard 1300 pkts, crc_err 8, "
                             "runts 0, last from=!16cd7438 to=!ffffffff ch=0x08 "
                             "rssi=-104 snr=4.2) "
                             "watch=!32962f10:12/45@-97,!ddfb8065:never")
        w = d["watched"]
        assert w["!32962f10"]["age_s"] == 12
        assert w["!ddfb8065"]["never"] is True

    def test_channel_busy_while_our_radio_is_never_heard(self):
        """The 2026-07-29 blind spot, expressed as data: the channel is alive
        (heard_age_s 5, 1300 pkts) while OUR node has never been heard. Before
        this field the two were indistinguishable."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats("mesh_heard_age_s: 5 (heard 1300 pkts, crc_err 8, "
                             "runts 0, last from=!16cd7438 to=!ffffffff ch=0x08 "
                             "rssi=-104 snr=4.2) watch=!32962f10:never")
        assert d["heard_age_s"] == 5, "channel demonstrably busy"
        assert d["watched"]["!32962f10"]["never"] is True, "our radio unheard"

    def test_unparseable_entry_is_omitted_not_guessed(self):
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats("mesh_heard_age_s: 5 (heard 1 pkts, crc_err 0, "
                             "runts 0, last from=!a to=!b ch=0x08 rssi=-1 snr=1) "
                             "watch=!32962f10:garbage,!ddfb8065:7/2@-50")
        w = d["watched"]
        # Kept, but marked unknown — NOT dropped. A dropped key is
        # indistinguishable from "not watched", which is the blind spot again.
        assert w["!32962f10"]["parse_error"] is True
        assert w["!32962f10"]["age_s"] is None, "guessed a value for a garbled entry"
        assert w["!32962f10"]["never"] is False, "garbled must not read as never"
        assert w["!ddfb8065"]["age_s"] == 7
        assert w["!ddfb8065"]["parse_error"] is False

    def test_dropped_ids_are_surfaced(self):
        """Ids past the firmware cap are reported, not silently ignored."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats("mesh_heard_age_s: 5 (heard 1 pkts, crc_err 0, "
                             "runts 0, last from=!a to=!b ch=0x08 rssi=-1 snr=1) "
                             "watch=!32962f10:never watch_dropped=2")
        assert d["watch_dropped"] == 2

    def test_never_heard_branch_on_a_deaf_radio(self):
        """The heard-nothing-at-all stats branch must still carry the watch list,
        or a totally deaf claw would look like 'not configured'."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats("mesh_heard_age_s: 900 (heard 0 pkts since radio "
                             "start, crc_err 0, runts 0, 906.875 MHz) "
                             "watch=!32962f10:never")
        assert d["heard_age_s"] == 900
        assert d["watched"]["!32962f10"]["never"] is True


class TestDirectVsRelayedReception:
    """`watch=` and `direct=` answer DIFFERENT questions, and conflating them
    puts a digipeater in the wrong place.

    In a flood mesh the `from=` field survives rebroadcast, so a watch hit may
    be a NEARER node repeating the tracked node — its RSSI is the relay's
    signal. Only hops == 0 characterises a link to the node itself. Firmware
    exposes both as separate fields (2026-08-30) precisely so the difference
    survives to the host.
    """

    BASE = ("mesh_heard_age_s: 5 (heard 1300 pkts, crc_err 8, runts 0, "
            "last from=!16cd7438 to=!ffffffff rssi=-104 snr=4.2 hops=0)")

    def test_strong_watch_hit_with_no_direct_is_a_RELAYED_node(self):
        """THE case this field exists for. -62 dBm looks like a great link and
        is not one: nothing has ever reached us from that node directly."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE +
                             " watch=!0daee001:14/5@-62"
                             " direct=!0daee001:never")
        assert d["watched"]["!0daee001"]["rssi_dbm"] == -62      # any path
        assert d["direct"]["!0daee001"]["direct"] is False       # but repeated
        assert d["direct"]["!0daee001"]["age_s"] is None

    def test_direct_hit_carries_the_link_rssi(self):
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE +
                             " watch=!0daee001:14/5@-62"
                             " direct=!0daee001:20@-103")
        assert d["direct"]["!0daee001"]["direct"] is True
        assert d["direct"]["!0daee001"]["age_s"] == 20
        # The LINK is -103. The -62 in `watched` was somebody else's radio.
        assert d["direct"]["!0daee001"]["rssi_dbm"] == -103
        assert d["watched"]["!0daee001"]["rssi_dbm"] == -62

    def test_absent_direct_field_is_none_not_no_link(self):
        """Firmware predating the field knows nothing about hop distance.
        That is UNKNOWN, and must not read as 'no direct link exists'."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE + " watch=!0daee001:14/5@-62")
        assert d["direct"] is None

    def test_malformed_header_hops_is_minus_one_never_direct(self):
        """hop_start < hop_limit cannot happen on a well-formed packet. The
        firmware reports -1; claiming a direct link we cannot substantiate is
        the failure that matters, so -1 must never be read as 0."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE.replace("hops=0", "hops=-1"))
        assert d["last_hops"] == -1
        assert d["last_hops"] != 0

    def test_last_hops_absent_on_old_firmware_is_none(self):
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE.replace(" hops=0", ""))
        assert d["last_hops"] is None

    def test_garbled_direct_entry_is_kept_with_parse_error(self):
        """Dropping the key would make it indistinguishable from 'not tracked'
        to a consumer doing .get(id) (honest_failure_modes #9)."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE + " direct=!0daee001:xx@-103")
        assert d["direct"]["!0daee001"]["parse_error"] is True
        assert d["direct"]["!0daee001"]["direct"] is None


class TestStatsTruncationWitness:
    """F1 (+dudeclaw.20): the claw now WITNESSES its own truncated stats tail.

    The defect this closes is a confident wrong reading, not a crash. Two
    stacked caps clipped the reply silently, and a clipped `direct=` token
    parses as a VALID number — `@-104` loses its tail and becomes `@-1`, a
    perfectly plausible -1 dBm that no parser can distinguish from a real one.
    Siting a digipeater off that number puts it in the wrong place.

    The marker is deliberately ASYMMETRIC: presence proves truncation, absence
    proves nothing, because every build before +dudeclaw.20 truncates in
    silence. So the field is True/None and never True/False.
    """

    BASE = ("mesh_heard_age_s: 5 (heard 1300 pkts, crc_err 8, runts 0, "
            "last from=!16cd7438 to=!ffffffff rssi=-104 snr=4.2 hops=0)")

    def test_marker_present_sets_stats_truncated_true(self):
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE + " watch=!0daee001:14/5@-62 cut=1")
        assert d["stats_truncated"] is True

    def test_marker_absent_is_none_never_false(self):
        """Old firmware truncates silently, so 'no marker' is UNKNOWN. False
        would assert a complete reply we cannot substantiate — the exact
        overlap between the degraded and healthy domains (honest_failure_modes
        #1) that F1 exists to remove."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE + " watch=!0daee001:14/5@-62")
        assert d["stats_truncated"] is None
        assert d["stats_truncated"] is not False

    def test_truncated_reply_flags_every_direct_entry(self):
        """Nothing says WHICH token lost bytes, so no entry may read clean."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(
            self.BASE + " direct=!0daee001:12@-103,!16cd7438:8@-1 cut=1")
        for node in ("!0daee001", "!16cd7438"):
            assert d["direct"][node]["parse_error"] is True
            assert d["direct"][node]["direct"] is None
            assert d["direct"][node]["rssi_dbm"] is None

    def test_truncated_reply_flags_every_watch_entry(self):
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(
            self.BASE + " watch=!0daee001:14/5@-62,!16cd7438:3/9@-70 cut=1")
        for node in ("!0daee001", "!16cd7438"):
            assert d["watched"][node]["parse_error"] is True
            assert d["watched"][node]["rssi_dbm"] is None
            assert d["watched"][node]["age_s"] is None

    def test_the_forged_reading_does_not_survive_as_clean(self):
        """THE regression. `@-1` is what `@-104` decays into under truncation.
        Without the marker it is indistinguishable from a real -1 dBm; with it,
        the entry must refuse to present as a usable link reading."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        forged = parse_lora_stats(self.BASE + " direct=!0daee001:12@-1 cut=1")
        assert forged["direct"]["!0daee001"]["rssi_dbm"] is None
        assert forged["direct"]["!0daee001"]["parse_error"] is True
        # ...while the SAME text without the witness still reads as a value,
        # which is precisely why the firmware half had to ship with it.
        legacy = parse_lora_stats(self.BASE + " direct=!0daee001:12@-1")
        assert legacy["direct"]["!0daee001"]["rssi_dbm"] == -1
        assert legacy["stats_truncated"] is None

    def test_untruncated_entries_are_untouched(self):
        """The flag must not degrade a clean reply — it is the presence of the
        marker that matters, not the presence of the field."""
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(self.BASE + " direct=!0daee001:12@-103")
        assert d["direct"]["!0daee001"]["rssi_dbm"] == -103
        assert d["direct"]["!0daee001"]["parse_error"] is False
        assert d["stats_truncated"] is None

    def test_watch_dropped_is_not_mistaken_for_the_cut_marker(self):
        from mini_dudeai.claw_telemetry import parse_lora_stats
        d = parse_lora_stats(
            self.BASE + " watch=!0daee001:14/5@-62 watch_dropped=1")
        assert d["stats_truncated"] is None
        assert d["watch_dropped"] == 1
        assert d["watched"]["!0daee001"]["rssi_dbm"] == -62
