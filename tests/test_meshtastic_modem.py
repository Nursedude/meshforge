"""One Meshtastic preset table, pinned to the firmware (2026-09-25).

Three hand-written tables had drifted: MEDIUM_FAST as SF10 (firmware: SF9),
MEDIUM_SLOW at 125 kHz (250), SHORT_SLOW as SF7/125 (SF8/250), CR 4/8 where
the firmware uses 4/5, LONG_TURBO missing. The expected values below are
transcribed by hand from meshtastic/firmware src/mesh/MeshRadio.h
modemPresetToParams() at the tag named in FIRMWARE_TAG — change both together.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

from utils import meshtastic_modem as mm  # noqa: E402

FIRMWARE_V2_7_26 = {   # name: (sf, bw_hz, cr)
    "SHORT_TURBO": (7, 500000, 5), "SHORT_FAST": (7, 250000, 5),
    "SHORT_SLOW": (8, 250000, 5), "MEDIUM_FAST": (9, 250000, 5),
    "MEDIUM_SLOW": (10, 250000, 5), "LONG_TURBO": (11, 500000, 8),
    "LONG_FAST": (11, 250000, 5), "LONG_MODERATE": (11, 125000, 8),
    "LONG_SLOW": (12, 125000, 8),
}


def test_table_is_the_firmware_transcription():
    assert mm.FIRMWARE_TAG == "v2.7.26.54e0d8d"
    assert mm.FIRMWARE_MODEM_PARAMS == FIRMWARE_V2_7_26


def test_deprecated_preset_runs_as_long_fast_and_unknown_is_refused():
    assert mm.firmware_params("very_long_slow") == FIRMWARE_V2_7_26["LONG_FAST"]
    with pytest.raises(KeyError):
        mm.firmware_params("NOT_A_PRESET")


@pytest.mark.parametrize("preset,bps", [("SHORT_TURBO", 21875), ("MEDIUM_FAST", 3515.6),
                                        ("LONG_FAST", 1074.2), ("LONG_SLOW", 183.1)])
def test_raw_bit_rate_is_the_lora_formula(preset, bps):
    assert mm.raw_bit_rate_bps(*mm.firmware_params(preset)) == pytest.approx(bps, abs=0.1)


def test_every_consumer_table_agrees_with_the_firmware():
    from utils.lora_presets import MESHTASTIC_PRESETS
    from utils.preset_impact import PRESET_PARAMS
    for name, (sf, bw, cr) in FIRMWARE_V2_7_26.items():
        assert (PRESET_PARAMS[name]["sf"], PRESET_PARAMS[name]["bw"], PRESET_PARAMS[name]["cr"]) == (sf, bw, cr)
        lp = MESHTASTIC_PRESETS[name]
        assert (lp["spreading_factor"], lp["bandwidth"], lp["coding_rate"]) == (sf, bw, cr)
    from config.lora import LoRaConfigurator
    for name, (sf, bw, cr) in FIRMWARE_V2_7_26.items():
        cp = LoRaConfigurator.MODEM_PRESETS.get(name)
        if cp is not None:
            assert (cp["spreading_factor"], cp["bandwidth"], cp["coding_rate"]) == (sf, bw // 1000, cr)
    assert "VERY_LONG_SLOW" not in PRESET_PARAMS   # not offered to the site planner
    assert MESHTASTIC_PRESETS["VERY_LONG_SLOW"]["spreading_factor"] == 11


def test_radio_menu_labels_and_apply_table_come_from_the_firmware(monkeypatch):
    from handlers import meshtasticd_radio as mr
    seen = {}
    h = mr.MeshtasticdRadioHandler.__new__(mr.MeshtasticdRadioHandler)

    class Dialog:
        def menu(self, title, text, items):
            seen["items"] = dict(items)
            return "MEDIUM_FAST"

        def inputbox(self, title, text, default):
            seen["slot_text"] = text
            return None   # cancel before anything is applied

    h.ctx = type("C", (), {"dialog": Dialog()})()
    monkeypatch.setattr("utils.lora_presets.detect_meshtastic_settings", lambda: None)
    h._radio_presets_menu()
    assert seen["items"]["MEDIUM_FAST"].startswith("250kHz SF9 ")
    assert seen["items"]["SHORT_SLOW"].startswith("250kHz SF8 ")
    assert "LONG_TURBO" in seen["items"]
    assert "slot 20 = 906.875 MHz" in seen["slot_text"]


def test_gateway_template_dialog_shows_firmware_numbers(monkeypatch):
    from handlers import channel_config as cc
    shown = {}
    h = cc.ChannelConfigHandler.__new__(cc.ChannelConfigHandler)

    class Dialog:
        def yesno(self, title, text, **k):
            shown["text"] = text
            return False   # decline: nothing is applied

    h.ctx = type("C", (), {"dialog": Dialog()})()
    h._apply_gateway_template("mtnmesh")
    assert "Spreading Factor: SF9" in shown["text"]


# --- channel maths (RadioInterface.cpp) against facts known independently ---

def test_long_fast_default_lands_on_the_fleets_slot_20():
    # The fleet's LongFast segment is ch20 / 906.875 MHz (measured on the radios).
    assert mm.channel_centre_mhz("LONG_FAST") == (906.875, 20, 104)


def test_explicit_slot_follows_the_firmware_formula():
    assert mm.channel_centre_mhz("SHORT_TURBO", 8) == (905.75, 8, 52)   # fleet's ShortTurbo ch8
    assert mm.channel_centre_mhz("LONG_FAST", 1)[0] == 902.125


def test_djb2_is_the_firmware_hash():
    assert mm.djb2("") == 5381
    assert mm.djb2("a") == 5381 * 33 + ord("a")
