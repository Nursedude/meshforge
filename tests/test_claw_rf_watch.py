"""The uptime gate — `never` is only a finding once we've listened long enough.

Born the same day the watch field shipped (2026-07-29). Seconds after arming,
THREE of four watched fleet radios read `never` — not because they were mute, but
because the claw had been up for TEN SECONDS and those radios transmit roughly
once every couple of hours. A probe firing on `never` alone would page on every
claw reboot, flash and power cycle: an unobservable state rendered as a confident
claim, which is the exact defect the watch field exists to remove.

These tests pin the gate using the REAL values measured that day, so the
calibration case is the live one rather than one I invented.
"""

import pytest

from mini_dudeai.claw_rf_watch import (
    DEFAULT_EXPECTED_TX_INTERVAL_S,
    DEFAULT_SILENCE_MULTIPLE,
    HEARD,
    SILENT,
    UNOBSERVABLE,
    classify_watch,
    required_window_s,
    summarise,
)

MOC = "!32962f10"
MOC3 = "!ebfa1b11"


def _heard(age=2, pkts=1, rssi=-118):
    return {"age_s": age, "pkts": pkts, "rssi_dbm": rssi,
            "never": False, "parse_error": False}


def _never():
    return {"age_s": None, "pkts": 0, "rssi_dbm": None,
            "never": True, "parse_error": False}


def _garbled():
    return {"age_s": None, "pkts": None, "rssi_dbm": None,
            "never": False, "parse_error": True}


class TestTheLiveCalibrationCase:
    """2026-07-29, seconds after arming: claw-01 heard moc at -118 dBm while
    moc3/moc1/moc2 read `never` at ~10 s uptime. Exactly one of those is a fact
    about a transmitter; the rest are facts about how briefly we had listened."""

    def test_ten_second_uptime_makes_never_unobservable_not_silent(self):
        v = classify_watch({MOC: _heard(), MOC3: _never()}, uptime_s=10)
        assert v[MOC]["verdict"] == HEARD
        assert v[MOC3]["verdict"] == UNOBSERVABLE, \
            "never at 10s uptime must NOT be a finding — this is the reboot page"
        assert "nothing yet" in v[MOC3]["reason"]

    def test_marginal_rssi_still_counts_as_heard(self):
        """-118 dBm is at the noise floor but it IS reception. The gate judges
        silence, not link quality — conflating them would hide a working link."""
        v = classify_watch({MOC: _heard(age=2, rssi=-118)}, uptime_s=10)
        assert v[MOC]["verdict"] == HEARD


class TestTheWindow:

    def test_silent_once_the_window_elapses(self):
        need = required_window_s()
        v = classify_watch({MOC3: _never()}, uptime_s=need + 1)
        assert v[MOC3]["verdict"] == SILENT
        assert v[MOC3]["silent_for_at_least_s"] == need + 1

    def test_boundary_is_not_yet_actionable(self):
        need = required_window_s()
        v = classify_watch({MOC3: _never()}, uptime_s=need - 1)
        assert v[MOC3]["verdict"] == UNOBSERVABLE

    def test_default_window_is_conservative(self):
        """Under-fire on purpose: 3 intervals of 3h = 9h of listening before
        silence is claimed. A real mute node is still caught the next tick."""
        assert required_window_s() == DEFAULT_EXPECTED_TX_INTERVAL_S * DEFAULT_SILENCE_MULTIPLE
        assert required_window_s() >= 6 * 3600

    def test_per_node_interval_overrides(self):
        """A chatty node earns a shorter window; that is a per-node property of
        what it broadcasts, so it is config, not code."""
        v = classify_watch({MOC3: _never()}, uptime_s=400,
                           expected_intervals={MOC3: 60})
        assert v[MOC3]["verdict"] == SILENT, "60s interval x3 = 180s < 400s uptime"

    def test_silent_duration_is_a_lower_bound(self):
        """The firmware knows only 'not since radio start', so the reported
        silence is a floor, never an exact age."""
        v = classify_watch({MOC3: _never()}, uptime_s=99999)
        assert v[MOC3]["silent_for_at_least_s"] == 99999
        assert v[MOC3]["age_s"] is None


class TestBlindnessStaysBlind:

    def test_unknown_uptime_is_unobservable(self):
        """No uptime = no window = no verdict. Not silence."""
        v = classify_watch({MOC3: _never()}, uptime_s=None)
        assert v[MOC3]["verdict"] == UNOBSERVABLE
        assert "uptime is unknown" in v[MOC3]["reason"]

    def test_garbled_entry_is_unobservable_not_silent(self):
        v = classify_watch({MOC3: _garbled()}, uptime_s=99999)
        assert v[MOC3]["verdict"] == UNOBSERVABLE
        assert "unreadable" in v[MOC3]["reason"]

    def test_absent_watch_list_is_none_not_all_clear(self):
        assert classify_watch(None, uptime_s=99999) is None
        assert classify_watch({}, uptime_s=99999) is None


class TestSummary:

    def test_blind_count_is_never_folded_into_healthy_or_silent(self):
        need = required_window_s()
        v = classify_watch({MOC: _heard(), MOC3: _never(), "!aaaaaaaa": _garbled()},
                           uptime_s=need + 1)
        s = summarise(v)
        assert s["heard"] == [MOC]
        assert s["silent"] == [MOC3]
        assert s["unobservable"] == ["!aaaaaaaa"]
        assert s["actionable"] is True

    def test_not_actionable_when_only_blind(self):
        s = summarise(classify_watch({MOC3: _never()}, uptime_s=5))
        assert s["silent"] == [] and s["unobservable"] == [MOC3]
        assert s["actionable"] is False, \
            "a fleet we have not listened to long enough is not an incident"


def _envelope(text):
    """The REAL NATS reply shape. build_tick's _extract requires the envelope and
    records 'malformed reply: str' for a bare string — a bare string is what I
    first wrote here, having invented the shape instead of using the one the
    device actually returns. Pinned so the next author does not repeat it."""
    return {"ok": True, "result": text}


class TestWiredIntoTheTick:

    def test_build_tick_carries_a_judged_verdict(self):
        from mini_dudeai.claw_telemetry import build_tick
        t = build_tick(
            now=1.0, host="h", device="dudeclaw-01",
            device_info_reply=_envelope("Chip: ESP32-S3 rev 2\nUptime: 10 s"),
            ble_stats_reply=_envelope("Error: no BLE scanner on this device"),
            battery_reply=_envelope("Battery: 4.17 V (adc 852 mV)"),
            lora_reply=_envelope(
                "mesh_heard_age_s: 5 (heard 20 pkts, crc_err 0, runts 0, "
                "last from=!435a9d24 to=!ffffffff ch=0x08 rssi=-103 snr=3.8)"
                " watch=!32962f10:2/1@-118,!ebfa1b11:never"))
        wv = t.get("watch_verdicts")
        assert wv is not None, "the gate must be applied in the tick, not left to consumers"
        assert wv["!32962f10"]["verdict"] == HEARD
        # uptime parsed from device_info is small, so never stays unobservable
        assert wv["!ebfa1b11"]["verdict"] == UNOBSERVABLE

    def test_no_watch_list_means_no_verdicts_key_value(self):
        from mini_dudeai.claw_telemetry import build_tick
        t = build_tick(now=1.0, host="h", device="d",
                       device_info_reply=_envelope("Uptime: 99999 s"),
                       ble_stats_reply=_envelope("x"), battery_reply=None,
                       lora_reply=_envelope(
                           "mesh_heard_age_s: 5 (heard 20 pkts, crc_err 0, "
                           "runts 0, last from=!a to=!b ch=0x08 rssi=-1 snr=1)"))
        assert t.get("watch_verdicts") is None
