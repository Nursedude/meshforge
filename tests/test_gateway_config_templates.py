"""Anti-rot guard for docs/gateway_config_templates/*.json.

The gateway config schema (src/gateway/config.py) changes frequently in beta.
These tests load EVERY template in docs/gateway_config_templates/ through the
REAL ``GatewayConfig.load()`` path so a template that drifts from the schema
breaks loudly in CI instead of rotting silently (the fate that already befell
examples/configs/gateway-mqtt.json).

Two checks are needed because load()'s unknown-key behavior is MIXED:

1. ``test_template_loads_and_takes_effect`` — load() wraps everything in a
   broad ``except Exception: return cls()`` (config.py), so a template whose
   splat-built section (meshtastic, rns, mqtt_bridge, meshcore, telemetry,
   mesh_bridge.primary/secondary, routing_rules) carries an unknown key would
   raise TypeError internally and be SILENTLY swallowed into pristine
   defaults. We catch that by asserting the loaded config actually differs
   from defaults (every template sets non-default values).

2. ``test_template_keys_are_known`` — the ``.get()``-built sections
   (top-level, mesh_bridge's own keys, meshtastic_broadcast, rns_transport)
   silently IGNORE unknown keys, and several top-level dataclass fields
   (failover_*, gateway_heartbeat_*, load_balancer_*, gateway_role,
   gateway_id) are defined on GatewayConfig but never read by load() at all.
   A template setting any of those would be a lie. We recurse the template
   against dataclasses.fields() — and against the explicit
   TOP_LEVEL_KEYS_READ_BY_LOAD set for the top level — so silent drops fail.

Annotation keys (``_``-prefixed, e.g. ``_description``) are allowed at the
TOP LEVEL ONLY: load() ignores unknown top-level keys, but a nested splat
section would crash on them.
"""

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.config import (  # noqa: E402
    GatewayConfig,
    MeshtasticBridgeConfig,
    MeshtasticConfig,
    RoutingRule,
)

TEMPLATE_DIR = Path(__file__).parent.parent / "docs" / "gateway_config_templates"

# Top-level keys that GatewayConfig.load() actually reads (config.py load()).
# Deliberately NARROWER than dataclasses.fields(GatewayConfig): the
# failover_*/gateway_heartbeat_*/load_balancer_*/gateway_role/gateway_id
# fields exist on the dataclass but are never read by load() — a template
# setting them would silently no-op. If load() grows a new key, add it here
# (this set rotting is exactly the loud failure we want).
TOP_LEVEL_KEYS_READ_BY_LOAD = {
    "enabled", "auto_start", "bridge_mode", "rns_bridge_enabled",
    "meshtastic", "rns", "mqtt_bridge", "rns_transport", "mesh_bridge",
    "meshcore", "routing_rules", "default_route", "telemetry",
    "meshtastic_broadcast", "log_level", "log_messages",
    "ai_diagnostics_enabled", "snr_analysis", "anomaly_detection",
}


def _templates():
    found = sorted(TEMPLATE_DIR.glob("*.json"))
    assert found, f"no templates found in {TEMPLATE_DIR}"
    return found


def _strip_doc_keys(data: dict) -> dict:
    """Drop top-level ``_``-prefixed annotation keys (allowed there only)."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _field_names(dc_type) -> set:
    return {f.name for f in dataclasses.fields(dc_type)}


def _assert_section_keys_known(section_name: str, data: dict, dc_type,
                               path: str) -> list:
    """Return a list of 'path: unknown key' violations for one dict section."""
    violations = []
    known = _field_names(dc_type)
    for key, value in data.items():
        if key.startswith("_"):
            violations.append(
                f"{path}.{key}: annotation keys are top-level only "
                f"(nested splat sections reject them)")
            continue
        if key not in known:
            violations.append(f"{path}.{key}: unknown key for {dc_type.__name__}")
            continue
        # Recurse into nested dataclass-typed dict values we know about.
        if section_name == "mesh_bridge" and key in ("primary", "secondary") \
                and isinstance(value, dict):
            violations.extend(_assert_section_keys_known(
                key, value, MeshtasticConfig, f"{path}.{key}"))
    return violations


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_is_valid_json_with_doc_header(path):
    data = json.loads(path.read_text())
    assert isinstance(data, dict)
    # Every template self-describes and points at this test.
    assert data.get("_name"), f"{path.name} missing _name annotation"
    assert data.get("_description"), f"{path.name} missing _description"
    assert "test_gateway_config_templates" in data.get("_validated_by", ""), \
        f"{path.name} missing _validated_by pointer"


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_loads_and_takes_effect(path, monkeypatch, tmp_path):
    """The REAL load() path must parse the template AND apply it.

    load() swallows all exceptions into pristine defaults, so 'no exception'
    proves nothing — we assert the result differs from GatewayConfig()
    (every template sets non-default values somewhere).
    """
    data = _strip_doc_keys(json.loads(path.read_text()))
    cfg_path = tmp_path / "gateway.json"
    cfg_path.write_text(json.dumps(data))
    monkeypatch.setattr(
        GatewayConfig, "get_config_path", classmethod(lambda cls: cfg_path))

    cfg = GatewayConfig.load()

    assert cfg != GatewayConfig(), (
        f"{path.name} loaded as pristine defaults — load() silently "
        f"swallowed a parse error (likely an unknown key in a splat-built "
        f"section). Run with log capture to see the underlying TypeError.")


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_template_keys_are_known(path):
    """Every key must be one load() actually reads — catches silent drops."""
    data = _strip_doc_keys(json.loads(path.read_text()))
    violations = []

    section_types = {
        "meshtastic": MeshtasticConfig,
        "rns": None,        # resolved from GatewayConfig fields below
        "mesh_bridge": MeshtasticBridgeConfig,
    }
    gateway_field_types = {
        f.name: f.type for f in dataclasses.fields(GatewayConfig)}
    # Map section name -> dataclass type via the GatewayConfig field defaults.
    dc_by_field = {}
    for f in dataclasses.fields(GatewayConfig):
        default = (f.default_factory() if f.default_factory
                   is not dataclasses.MISSING else f.default)
        if dataclasses.is_dataclass(default):
            dc_by_field[f.name] = type(default)
    assert section_types  # silence linter; explicit map above is documentation
    assert gateway_field_types

    for key, value in data.items():
        if key not in TOP_LEVEL_KEYS_READ_BY_LOAD:
            violations.append(
                f"{key}: not read by GatewayConfig.load() — would silently "
                f"no-op (see TOP_LEVEL_KEYS_READ_BY_LOAD)")
            continue
        if key == "routing_rules" and isinstance(value, list):
            for i, rule in enumerate(value):
                violations.extend(_assert_section_keys_known(
                    "routing_rules", rule, RoutingRule, f"routing_rules[{i}]"))
            continue
        if isinstance(value, dict) and key in dc_by_field:
            violations.extend(_assert_section_keys_known(
                key, value, dc_by_field[key], key))

    assert not violations, (
        f"{path.name} has unknown/silently-dropped keys:\n  "
        + "\n  ".join(violations))


def test_top_level_read_set_is_subset_of_dataclass_fields():
    """Self-check: the documented read-set never lists a nonexistent field.

    (The reverse — fields not in the read-set — is EXPECTED: failover_*,
    gateway_heartbeat_*, load_balancer_* are defined but unread by load().)
    """
    assert TOP_LEVEL_KEYS_READ_BY_LOAD <= _field_names(GatewayConfig)
