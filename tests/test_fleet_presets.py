"""Integrity guard for docs/fleet_presets.yaml — the lab-hardened topology
catalog the Session-C TUI fleet-provision feature reads.

A preset must reference a REAL role (in fleet_roles.yaml) and REAL bridge legs
(in its own `legs:` vocabulary) — otherwise the TUI would offer a variation
that provision_role.py / configure_gateway.sh can't apply. This is the
closed-consumer guard (honest_failure_modes #7): the catalog and the role SSOT
are validated together, not independently.

Run: python3 -m pytest tests/test_fleet_presets.py -v
"""

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

REPO = Path(__file__).resolve().parent.parent
PRESETS_PATH = REPO / "docs" / "fleet_presets.yaml"
ROLES_PATH = REPO / "docs" / "fleet_roles.yaml"

REQUIRED_FIELDS = {"title", "role", "bridge", "board_tier", "maturity", "use_when"}
ALLOWED_MATURITY = {"field-proven", "lab-proven", "alpha"}


@pytest.fixture(scope="module")
def presets_doc():
    with open(PRESETS_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def role_names():
    with open(ROLES_PATH) as f:
        roles = yaml.safe_load(f)
    return set(roles["roles"].keys())


def test_presets_file_parses(presets_doc):
    assert isinstance(presets_doc, dict)
    assert "presets" in presets_doc and presets_doc["presets"]
    assert "legs" in presets_doc and presets_doc["legs"]


def test_every_preset_has_required_fields(presets_doc):
    for name, p in presets_doc["presets"].items():
        missing = REQUIRED_FIELDS - set(p)
        assert not missing, f"preset {name!r} missing fields: {missing}"
        assert p["maturity"] in ALLOWED_MATURITY, \
            f"preset {name!r} bad maturity {p['maturity']!r}"


def test_every_preset_role_exists(presets_doc, role_names):
    """A preset's role MUST be a real role in fleet_roles.yaml — else the TUI
    offers a variation provision_role.py cannot converge."""
    for name, p in presets_doc["presets"].items():
        assert p["role"] in role_names, \
            f"preset {name!r} references unknown role {p['role']!r} " \
            f"(known: {sorted(role_names)})"


def test_bridge_legs_are_known_and_only_when_bridging(presets_doc):
    """bridge_legs must name legs in the vocabulary, and only bridge presets
    carry them (a monitor/hub preset runs no bridge daemon)."""
    leg_vocab = set(presets_doc["legs"].keys())
    for name, p in presets_doc["presets"].items():
        legs = p.get("bridge_legs", [])
        if not p["bridge"]:
            assert not legs, f"non-bridge preset {name!r} must not list bridge_legs"
            continue
        assert legs, f"bridge preset {name!r} must list at least one leg"
        unknown = set(legs) - leg_vocab
        assert not unknown, f"preset {name!r} unknown legs: {unknown}"


def test_singletons_match_role_singleton(presets_doc, role_names):
    """A preset flagged singleton must map to a role declared singleton in
    fleet_roles.yaml (primary, cloud-publisher) — keeps the catalog honest
    about the one_canonical_writer / one_cloud_publisher invariants."""
    with open(ROLES_PATH) as f:
        roles = yaml.safe_load(f)["roles"]
    for name, p in presets_doc["presets"].items():
        if p.get("singleton"):
            assert roles[p["role"]].get("singleton") is True, \
                f"preset {name!r} marked singleton but role {p['role']!r} is not"


def test_leg_gateway_json_flags_present(presets_doc):
    """Each leg declares the gateway.json flag(s) it sets so Session C can
    translate a preset to a gateway.json overlay deterministically."""
    for leg, spec in presets_doc["legs"].items():
        assert "gateway_json" in spec and spec["gateway_json"], \
            f"leg {leg!r} missing gateway_json mapping"


def test_gateway_template_defaults_true_origin_recognition():
    """Transport-truth invariant (2026-07-01): the gateway.json TEMPLATE must
    default rns.true_origin_downlink_enabled=true so every PROVISIONED gateway
    inherits the cross-box loop-guard recognition (Option A). Without it, a new
    or reactivated gateway on a segment where a peer delivers untagged
    true-origin content would re-bridge the echo (the moc<->moc3 loop this arc
    closed). Delivery stays opt-in via meshtastic.injection_mode=downlink + a
    channel PSK — only RECOGNITION is defaulted, which is always loop-safe.
    A silent removal of this default reopens the loop; pin it (honest_failure_modes
    #7 — the invariant needs a closed consumer, not memory)."""
    import json
    template = REPO / "templates" / "gateway" / "gateway.json.template"
    # Type-correct placeholder substitution so the render is valid JSON.
    rendered = (template.read_text()
                .replace("@MESHFORGE_CHANNEL_INDEX@", "2")
                .replace("@LXMF_DESTINATION@", "test_dest"))
    d = json.loads(rendered)
    assert d["rns"]["true_origin_downlink_enabled"] is True


def test_gateway_template_defaults_dual_path_dedup():
    """Transport-truth arc Phase 4 (2026-07-01): the gateway.json TEMPLATE must
    default rns.dual_path_dedup_enabled=true so every PROVISIONED gateway
    inherits the intra-box exactly-once guard. Without it, a reactivated
    dual-radio gateway (e.g. moc1, the designated future gateway, which had the
    recognition flag pre-set but NOT this one) would double-inject a message
    that reaches its primary radio via both the RNS→Mesh true-origin downlink
    AND the mesh_bridge ST→primary cross-band bridge — the live 0743 dup this
    phase fixed. Field-proven on moc + moc3. Loop-safe on single-radio gateways
    too (nothing registers a second egress path, so it never falsely suppresses;
    empty content_id never suppresses, honest_failure_modes #1). Pin it as a
    closed consumer, not memory (honest_failure_modes #7)."""
    import json
    template = REPO / "templates" / "gateway" / "gateway.json.template"
    rendered = (template.read_text()
                .replace("@MESHFORGE_CHANNEL_INDEX@", "2")
                .replace("@LXMF_DESTINATION@", "test_dest"))
    d = json.loads(rendered)
    assert d["rns"]["dual_path_dedup_enabled"] is True


def test_code_default_dual_path_dedup_true():
    """Review 2026-07-01 (the #62 saved-defaults class): the TEMPLATE default
    above only reaches a FRESHLY rendered gateway.json — an already-deployed
    config lacking the key silently constructs the CODE default, and there is
    no key-merge migration for gateway.json. So the code default is the only
    channel that protects e.g. a reactivated moc1 whose dormant config
    predates Phase 4. Pin RNSConfig's default True so the template and code
    defaults can never drift apart (honest_failure_modes #5)."""
    from gateway.config import RNSConfig
    assert RNSConfig().dual_path_dedup_enabled is True
