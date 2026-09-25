"""The RNode profile — one source for every RNode config MeshForge writes.

Standardised 2026-09-25 (operator: "standardize the RNode profile … what works
best for our domain"). Measured that day: both fleet RNodes run the US default
below (903.625 MHz / 250 kHz / SF7 / CR 4/5 / 22 dBm, rnstatus 10.94 kbps), and
it already appeared as matching literals in three writers, beside a template
at 17 dBm, a dead table naming RNode profiles after Meshtastic presets, and a
dead generator whose "Meshtastic slot" table matched no firmware formula.

Rules this module encodes:
  * An RNode profile is NOT a Meshtastic preset. RNS and Meshtastic cannot
    decode each other; an RNode "on MEDIUM_FAST parameters" only shares
    airtime with that mesh. Pick a frequency CLEAR of the local Meshtastic
    channels, and SF/BW for the path.
  * Every RNode on one RF segment must match exactly (frequency, bandwidth,
    SF, CR) — a mismatch is silent deafness (09-22: an RNode reverted to SF12
    while its peer stayed SF7).

An operator on another band or path declares their own profile in
``~/.config/meshforge/rnode_profile.json`` (instance values never live in the
repo). A declaration that fails validation RAISES — it is never quietly
replaced by the default (honest_failure_modes #3).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from utils.paths import get_real_user_home

KEYS = ("frequency", "bandwidth", "spreading_factor", "coding_rate", "tx_power")

# Region defaults. US = the fleet's measured profile (see module docstring).
REGION_DEFAULTS: Dict[str, Dict[str, int]] = {
    "US": {"frequency": 903_625_000, "bandwidth": 250_000, "spreading_factor": 7,
           "coding_rate": 5, "tx_power": 22},
    "EU": {"frequency": 867_500_000, "bandwidth": 125_000, "spreading_factor": 8,
           "coding_rate": 5, "tx_power": 14},   # EU limit
    "AU": {"frequency": 917_000_000, "bandwidth": 250_000, "spreading_factor": 7,
           "coding_rate": 5, "tx_power": 22},
}

_VALID_BW = {7_800, 10_400, 15_600, 20_800, 31_250, 41_700, 62_500,
             125_000, 250_000, 500_000}


class ProfileError(ValueError):
    """A declared RNode profile the author cannot have meant."""


def declared_profile_path() -> Path:
    return get_real_user_home() / ".config" / "meshforge" / "rnode_profile.json"


def validate(profile: Dict) -> Dict[str, int]:
    """Return the profile's five fields as ints, or raise ProfileError."""
    unknown = set(profile) - set(KEYS) - {"note"}
    if unknown:
        raise ProfileError(f"unknown key(s) {sorted(unknown)}; allowed: {', '.join(KEYS)}, note")
    missing = [k for k in KEYS if k not in profile]
    if missing:
        raise ProfileError(f"missing key(s): {', '.join(missing)}")
    try:
        p = {k: int(profile[k]) for k in KEYS}
    except (TypeError, ValueError) as e:
        raise ProfileError(f"every field must be an integer ({e})") from None
    if not 137_000_000 <= p["frequency"] <= 3_000_000_000:
        raise ProfileError(f"frequency {p['frequency']} Hz is outside RNode hardware range")
    if p["bandwidth"] not in _VALID_BW:
        raise ProfileError(f"bandwidth {p['bandwidth']} Hz is not a LoRa bandwidth")
    if not 5 <= p["spreading_factor"] <= 12:
        raise ProfileError(f"spreading_factor {p['spreading_factor']} not in 5-12")
    if not 5 <= p["coding_rate"] <= 8:
        raise ProfileError(f"coding_rate {p['coding_rate']} not in 5-8 (4/5 … 4/8)")
    if not 0 <= p["tx_power"] <= 30:
        raise ProfileError(f"tx_power {p['tx_power']} dBm not in 0-30")
    return p


def rnode_profile(region: str = "US", path: Optional[Path] = None) -> Dict:
    """The profile to write: the operator's declaration if present, else the
    region default. Carries ``source`` so a screen can say which it is."""
    path = declared_profile_path() if path is None else path
    if path.is_file():
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise ProfileError(f"{path} unreadable ({e})") from None
        if not isinstance(doc, dict):
            raise ProfileError(f"{path} must hold a JSON object")
        return {**validate(doc), "source": f"declared ({path})"}
    region = region.upper()
    if region not in REGION_DEFAULTS:
        raise ProfileError(f"no RNode default for region {region}; "
                           f"known: {', '.join(REGION_DEFAULTS)} — declare one in {path}")
    return {**REGION_DEFAULTS[region], "source": f"MeshForge {region} default"}
