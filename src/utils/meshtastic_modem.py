"""Meshtastic modem presets as the FIRMWARE runs them — the one table.

Transcribed from meshtastic/firmware ``src/mesh/MeshRadio.h``
``modemPresetToParams()`` at tag v2.7.26.54e0d8d (the fleet's meshtasticd
build), non-wide LoRa (sub-GHz). Every preset table in MeshForge derives its
SF / bandwidth / coding rate from here (``lora_presets.MESHTASTIC_PRESETS``,
``preset_impact.PRESET_PARAMS``, the TUI Radio Presets menu).

Why (measured 2026-09-25, live-truth pass over Dashboard › Reports): three
hand-written tables had drifted from the firmware and from each other —
MEDIUM_FAST as SF10 (it is SF9), MEDIUM_SLOW at 125 kHz (250), SHORT_SLOW as
SF7/125 (SF8/250), coding rate 4/8 where the firmware uses 4/5, LONG_TURBO
missing. The TUI menu offered "MEDIUM_FAST 250kHz SF10" and the site planner
computed coverage for a radio that does not exist.

Updating: re-read ``modemPresetToParams`` at the new firmware tag, edit
``FIRMWARE_TAG`` and the table together; ``tests/test_meshtastic_modem.py``
pins both.
"""
from __future__ import annotations

import math
from typing import Dict, Tuple

FIRMWARE_TAG = "v2.7.26.54e0d8d"

# name -> (spreading_factor, bandwidth_hz, coding_rate_denominator  [4/x])
FIRMWARE_MODEM_PARAMS: Dict[str, Tuple[int, int, int]] = {
    "SHORT_TURBO": (7, 500_000, 5),
    "SHORT_FAST": (7, 250_000, 5),
    "SHORT_SLOW": (8, 250_000, 5),
    "MEDIUM_FAST": (9, 250_000, 5),
    "MEDIUM_SLOW": (10, 250_000, 5),
    "LONG_TURBO": (11, 500_000, 8),
    "LONG_FAST": (11, 250_000, 5),
    "LONG_MODERATE": (11, 125_000, 8),
    "LONG_SLOW": (12, 125_000, 8),
}

# Enum values with no case of their own: the firmware's ``default:`` branch
# runs them with LONG_FAST parameters. VERY_LONG_SLOW was deprecated in 2.5.
RUNS_AS_LONG_FAST = frozenset({"VERY_LONG_SLOW"})


def firmware_params(preset: str) -> Tuple[int, int, int]:
    """(sf, bw_hz, cr) the firmware actually uses for ``preset``.

    Raises KeyError for a name the firmware does not know at all — an
    unknown preset is not silently LONG_FAST here."""
    name = preset.upper()
    if name in RUNS_AS_LONG_FAST:
        return FIRMWARE_MODEM_PARAMS["LONG_FAST"]
    return FIRMWARE_MODEM_PARAMS[name]


def raw_bit_rate_bps(sf: int, bw_hz: int, cr: int) -> float:
    """LoRa raw bit rate: SF · BW / 2^SF · 4/CR (physics, not a measurement)."""
    return sf * bw_hz / (2 ** sf) * 4.0 / cr


# ── Channel centre frequency (RadioInterface.cpp, same tag) ─────────────
# Regions transcribed from RDEF(...) (freqStart MHz, freqEnd MHz, spacing MHz).
# Sub-GHz only: LORA_24 uses wide-LoRa bandwidths this module does not model.
REGIONS: Dict[str, Tuple[float, float, float]] = {
    "US": (902.0, 928.0, 0.0), "EU_433": (433.0, 434.0, 0.0),
    "EU_868": (869.4, 869.65, 0.0), "CN": (470.0, 510.0, 0.0),
    "JP": (920.5, 923.5, 0.0), "ANZ": (915.0, 928.0, 0.0),
    "ANZ_433": (433.05, 434.79, 0.0), "RU": (868.7, 869.2, 0.0),
    "KR": (920.0, 923.0, 0.0), "TW": (920.0, 925.0, 0.0),
    "IN": (865.0, 867.0, 0.0), "NZ_865": (864.0, 868.0, 0.0),
    "TH": (920.0, 925.0, 0.0), "UA_433": (433.0, 434.7, 0.0),
    "UA_868": (868.0, 868.6, 0.0), "MY_433": (433.0, 435.0, 0.0),
    "MY_919": (919.0, 924.0, 0.0), "SG_923": (917.0, 925.0, 0.0),
    "PH_433": (433.0, 434.7, 0.0), "PH_868": (868.0, 869.4, 0.0),
    "PH_915": (915.0, 918.0, 0.0), "KZ_433": (433.075, 434.775, 0.0),
    "KZ_863": (863.0, 868.0, 0.0), "NP_865": (865.0, 868.0, 0.0),
    "BR_902": (902.0, 907.5, 0.0),
}

# DisplayFormatters::getModemPresetDisplayName(preset, useShortName=false) —
# the channel name an EMPTY primary channel name resolves to, i.e. what slot 0
# hashes. VERY_LONG_SLOW has no case: the firmware's default arm says "Invalid".
PRESET_DISPLAY_NAMES: Dict[str, str] = {
    "SHORT_TURBO": "ShortTurbo", "SHORT_SLOW": "ShortSlow", "SHORT_FAST": "ShortFast",
    "MEDIUM_SLOW": "MediumSlow", "MEDIUM_FAST": "MediumFast", "LONG_SLOW": "LongSlow",
    "LONG_FAST": "LongFast", "LONG_TURBO": "LongTurbo", "LONG_MODERATE": "LongMod",
    "VERY_LONG_SLOW": "Invalid",
}


def djb2(text: str) -> int:
    """The firmware's ``hash()`` (djb2, uint32)."""
    h = 5381
    for byte in text.encode("utf-8"):
        h = (h * 33 + byte) & 0xFFFFFFFF
    return h


def channel_centre_mhz(preset: str, channel_num: int = 0, region: str = "US",
                       channel_name: str = "") -> Tuple[float, int, int]:
    """(centre MHz, 1-based slot, numChannels) the firmware tunes for this
    preset / channel_num / primary channel name. channel_num 0 = hashed from
    the channel name (the preset's display name when the name is empty)."""
    _sf, bw_hz, _cr = firmware_params(preset)
    return slot_centre_mhz(bw_hz, channel_num, region,
                           channel_name or PRESET_DISPLAY_NAMES[preset.upper()])


def num_channels(bw_hz: int, region: str = "US") -> int:
    """floor((freqEnd - freqStart) / (spacing + BW)), as the firmware computes it."""
    start, end, spacing = REGIONS[region.upper()]
    return int(math.floor((end - start) / (spacing + bw_hz / 1e6) + 1e-9))


def slot_centre_mhz(bw_hz: int, channel_num: int, region: str,
                    channel_name: str) -> Tuple[float, int, int]:
    """(centre MHz, 1-based channel_num actually used, numChannels).
    channel_num 0 = hashed from ``channel_name``; n > 0 wraps modulo numChannels
    exactly as the firmware does."""
    start, _end, _spacing = REGIONS[region.upper()]
    n = num_channels(bw_hz, region)
    if n < 1:
        raise ValueError(f"{bw_hz / 1000:g} kHz does not fit in region {region}")
    idx = (channel_num - 1) % n if channel_num else djb2(channel_name) % n
    bw_mhz = bw_hz / 1e6
    return start + bw_mhz / 2 + idx * bw_mhz, idx + 1, n
