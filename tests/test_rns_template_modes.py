"""R9 (research rev 4.4): RNS templates carry interface-mode guidance and no dead keys.

Read from the fork's Reticulum.py (rns 1.3.8+mf.1): `_synthesize_interface`
overwrites `name`, `selected_interface_mode` and `configured_bitrate` at
runtime, so a template that sets them claims a mode it does not set; and
`interface_mode = gateway` reads the `mode` key instead and raises KeyError.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = sorted(
    list((ROOT / "templates").glob("*.conf"))
    + list((ROOT / "config_templates").glob("rns_*.conf"))
    + list((ROOT / "src/gateway/templates/rns").glob("*.conf"))
)
RUNTIME_ONLY = ("name", "selected_interface_mode", "configured_bitrate")


def _interface_sections(path):
    """Active (uncommented) ``[[name]]`` stanzas under ``[interfaces]`` -> {name: {key: value}}.

    A small parser on purpose: CI's minimal profile has no configobj, and these
    templates use only ``[section]``, ``[[subsection]]`` and ``key = value``.
    """
    sections, current, in_ifaces = {}, None, False
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m2 = re.fullmatch(r"\[\[(.+?)\]\]", line)
        m1 = re.fullmatch(r"\[([^\[\]]+)\]", line)
        if m2:
            current = sections.setdefault(m2.group(1).strip(), {}) if in_ifaces else None
        elif m1:
            in_ifaces, current = m1.group(1).strip() == "interfaces", None
        elif current is not None and "=" in line:
            k, v = line.split("=", 1)
            current[k.strip()] = v.strip()
    return sections


def test_parser_sees_the_stanzas_a_template_declares():
    # Guards the hand parser itself: it must find real stanzas, or every
    # runtime-key assertion below passes vacuously.
    secs = _interface_sections(ROOT / "src/gateway/templates/rns/regional_server.conf")
    assert set(secs) == {"Regional RNS", "my_rnode_gateway"}
    assert secs["my_rnode_gateway"]["type"] == "RNodeInterface"


def test_templates_were_found():
    assert len(TEMPLATES) >= 6, TEMPLATES


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_runtime_only_keys_in_interfaces(path):
    for section, body in _interface_sections(path).items():
        bad = [k for k in RUNTIME_ONLY if k in body]
        assert not bad, f"{path.name} [[{section}]] sets runtime-only {bad}"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_gateway_mode_uses_the_mode_key(path):
    text = path.read_text()
    assert not re.search(r"^\s*#?\s*interface_mode\s*=\s*(gateway|gw)\b", text, re.M | re.I), (
        f"{path.name}: `interface_mode = gateway` raises KeyError in RNS; use `mode = gateway`"
    )


def test_reticulum_template_carries_the_mode_guidance():
    text = (ROOT / "templates/reticulum.conf").read_text()
    for needle in ("INTERFACE MODES + ANNOUNCE RATE", "boundary", "roaming",
                   "access_point", "announce_rate_target", "What no mode does"):
        assert needle in text, needle
