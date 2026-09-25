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
# Regions transcribed from RDEF(...): (freqStart MHz, freqEnd MHz, spacing MHz).
REGIONS: Dict[str, Tuple[float, float, float]] = {
    "US": (902.0, 928.0, 0.0),
    "EU_868": (869.4, 869.65, 0.0),
    "ANZ": (915.0, 928.0, 0.0),
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
    start, end, spacing = REGIONS[region.upper()]
    _sf, bw_hz, _cr = firmware_params(preset)
    bw_mhz = bw_hz / 1e6
    num_channels = int((end - start) // (spacing + bw_mhz))
    if channel_num:
        idx = (channel_num - 1) % num_channels
    else:
        idx = djb2(channel_name or PRESET_DISPLAY_NAMES[preset.upper()]) % num_channels
    return start + bw_mhz / 2 + idx * bw_mhz, idx + 1, num_channels
