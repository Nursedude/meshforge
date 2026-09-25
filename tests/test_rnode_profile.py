"""One RNode profile source (2026-09-25, operator: "standardize the RNode profile").

Pins: the US default is the fleet's measured profile; every writer and the
shipped template agree with it; a bad declaration is REFUSED, never swapped
for the default; RNode profiles are not named after Meshtastic presets.
"""
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from utils import rnode_profile as rp  # noqa: E402

FLEET_MEASURED = {"frequency": 903625000, "bandwidth": 250000, "spreading_factor": 7,
                  "coding_rate": 5, "tx_power": 22}   # both RNodes' /etc/reticulum/config, 09-25


def test_us_default_is_the_fleet_measured_profile(tmp_path):
    p = rp.rnode_profile("US", path=tmp_path / "absent.json")
    assert {k: p[k] for k in rp.KEYS} == FLEET_MEASURED
    assert p["source"] == "MeshForge US default"


def test_valid_declaration_wins_and_says_so(tmp_path):
    f = tmp_path / "rnode_profile.json"
    f.write_text(json.dumps({**FLEET_MEASURED, "frequency": 904125000, "note": "field"}))
    p = rp.rnode_profile("US", path=f)
    assert p["frequency"] == 904125000 and p["source"].startswith("declared")


@pytest.mark.parametrize("bad", [
    {**FLEET_MEASURED, "spreadingfactor": 7},          # RNS spelling, not ours
    {k: v for k, v in FLEET_MEASURED.items() if k != "coding_rate"},
    {**FLEET_MEASURED, "bandwidth": 200000},
    {**FLEET_MEASURED, "spreading_factor": 13},
    {**FLEET_MEASURED, "tx_power": "lots"},
])
def test_bad_declaration_is_refused_not_defaulted(tmp_path, bad):
    f = tmp_path / "rnode_profile.json"
    f.write_text(json.dumps(bad))
    with pytest.raises(rp.ProfileError):
        rp.rnode_profile("US", path=f)


def test_unknown_region_is_refused(tmp_path):
    with pytest.raises(rp.ProfileError):
        rp.rnode_profile("MARS", path=tmp_path / "absent.json")


def test_writers_and_template_agree_with_the_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(rp, "declared_profile_path", lambda: tmp_path / "absent.json")
    from commands.rnode import get_recommended_config
    from commands.rns_templates import _rnode_template_settings
    cfg = get_recommended_config("/dev/ttyACM0", "US").data["config"]
    assert {k: cfg[k] for k in rp.KEYS} == FLEET_MEASURED
    t = _rnode_template_settings()
    assert (int(t["frequency"]), int(t["bandwidth"]), int(t["spreadingfactor"]),
            int(t["codingrate"]), int(t["txpower"])) == tuple(FLEET_MEASURED.values())
    text = open(os.path.join(ROOT, "src/gateway/templates/rns/basic_rnode.conf")).read()
    got = {k: int(re.search(rf"^\s*{k}\s*=\s*(\d+)", text, re.M).group(1))
           for k in ("frequency", "bandwidth", "spreadingfactor", "codingrate", "txpower")}
    assert got == {"frequency": 903625000, "bandwidth": 250000, "spreadingfactor": 7,
                   "codingrate": 5, "txpower": 22}


def test_rnode_profiles_are_not_meshtastic_presets():
    import utils.lora_presets as lp
    assert not hasattr(lp, "PROVEN_GATEWAY_CONFIGS")
    assert not hasattr(lp, "get_rnode_config_for_meshtastic_preset")
    assert not os.path.exists(os.path.join(ROOT, "src/config/rns_config.py"))
