"""Tests for template loading, drift comparison, and export.

Adds to tests/test_gateway_preflight.py coverage. Uses the shipped
shortturbo_slot8_meshforge template as the reference.
"""

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))


def test_default_template_loads():
    from handlers import _gateway_preflight_template as tmpl
    t = tmpl.load_default_template()
    assert t is not None
    assert "name" in t
    assert "meshtastic" in t and "gateway" in t and "packages" in t


def test_list_templates_finds_shortturbo():
    from handlers import _gateway_preflight_template as tmpl
    paths = tmpl.list_templates()
    assert any("shortturbo" in p.name for p in paths)


def test_drift_all_match():
    """When live state matches template, no FAIL entries."""
    from handlers import _gateway_preflight_template as tmpl
    template = {
        "meshtastic": {
            "region": {"expected": "US", "severity": "fail"},
            "modem_preset": {"expected": "SHORT_TURBO", "severity": "fail"},
            "channel_num": {"expected": 8, "severity": "fail"},
        },
    }
    live = {
        "meshtastic": {"region": "US", "modem_preset": "SHORT_TURBO", "channel_num": 8},
    }
    results = tmpl.check_template_drift(template, live)
    fails = [r for r in results if r[0] == tmpl._FAIL]
    assert fails == []
    assert len(results) == 3


def test_drift_detects_region_mismatch():
    from handlers import _gateway_preflight_template as tmpl
    template = {"meshtastic": {"region": {"expected": "US", "severity": "fail"}}}
    live = {"meshtastic": {"region": "EU_868"}}
    results = tmpl.check_template_drift(template, live)
    assert len(results) == 1
    status, msg, _fix = results[0]
    assert status == tmpl._FAIL
    assert "US" in msg and "EU_868" in msg


def test_drift_package_version_ok():
    from handlers import _gateway_preflight_template as tmpl
    template = {"packages": {"lxmf": {"min_version": "0.9.0", "severity": "fail"}}}
    live = {"packages": {"lxmf": {"installed": True, "version": "0.9.4"}}}
    results = tmpl.check_template_drift(template, live)
    assert len(results) == 1
    assert results[0][0] == tmpl._OK


def test_drift_package_version_too_old():
    from handlers import _gateway_preflight_template as tmpl
    template = {
        "packages": {
            "lxmf": {
                "min_version": "0.9.0",
                "severity": "fail",
                "install": "pip3 install --user lxmf",
            }
        }
    }
    live = {"packages": {"lxmf": {"installed": True, "version": "0.5.0"}}}
    results = tmpl.check_template_drift(template, live)
    assert results[0][0] == tmpl._FAIL
    assert results[0][2] == "pip3 install --user lxmf"


def test_drift_service_inactive():
    from handlers import _gateway_preflight_template as tmpl
    template = {"services": {"rnsd": {"expected": "active", "severity": "fail"}}}
    live = {"services": {"rnsd": "inactive"}}
    results = tmpl.check_template_drift(template, live)
    assert results[0][0] == tmpl._FAIL
    assert "systemctl start rnsd" in results[0][2]


def test_drift_bridge_channel_name():
    from handlers import _gateway_preflight_template as tmpl
    template = {
        "meshtastic": {
            "bridge_channel_name": {"expected": "meshforge", "severity": "warn"}
        }
    }
    live_match = {"meshtastic": {"bridge_channels": [{"index": 2, "name": "meshforge"}]}}
    live_drift = {"meshtastic": {"bridge_channels": [{"index": 2, "name": "LongFast"}]}}
    live_empty = {"meshtastic": {"bridge_channels": []}}

    r_match = tmpl.check_template_drift(template, live_match)
    r_drift = tmpl.check_template_drift(template, live_drift)
    r_empty = tmpl.check_template_drift(template, live_empty)

    assert r_match[0][0] == tmpl._OK
    # severity=warn → glyph is _WARN not _FAIL when mismatched
    assert r_drift[0][0] == tmpl._WARN
    assert r_empty[0][0] == tmpl._WARN


def test_export_round_trip(tmp_path):
    """Exporting and re-parsing a live state works."""
    from handlers import _gateway_preflight_template as tmpl
    live = {
        "captured_at": "2026-04-18T00:00:00",
        "meshtastic": {"region": "US", "modem_preset": "SHORT_TURBO", "channel_num": 8},
        "gateway": {"bridge_mode": "mqtt_bridge"},
        "packages": {"rns": {"installed": True, "version": "1.1.4"}},
        "services": {"meshtasticd": "active"},
        "rns_shared_instance": {"available": True, "detail": "unix_socket"},
        "nomadnet": {},
    }
    target = tmpl.export_current_as_template(live, target_dir=tmp_path)
    assert target.exists()
    roundtrip = json.loads(target.read_text())
    assert roundtrip["meshtastic"]["region"] == "US"
    assert roundtrip["gateway"]["bridge_mode"] == "mqtt_bridge"


def test_version_compare_helper():
    from handlers import _gateway_preflight_template as tmpl
    assert tmpl._version_ge("1.1.4", "1.1.1") is True
    assert tmpl._version_ge("0.9.4", "0.9.0") is True
    assert tmpl._version_ge("0.8.0", "0.9.0") is False
    assert tmpl._version_ge("1.0", "0.9") is True


def test_handler_has_export_action():
    from handlers.gateway_preflight import GatewayPreflightHandler
    h = GatewayPreflightHandler()
    items = h.menu_items()
    actions = [i[0] for i in items]
    assert "check" in actions
    assert "export" in actions


def test_capture_live_state_parses_info():
    from handlers import _gateway_preflight_template as tmpl
    fake_info = '''...
    "region": "US",
    "usePreset": true,
    "modemPreset": "SHORT_TURBO",
    "channelNum": 8,
Channels:
  Index 0: PRIMARY { "name": "", "uplinkEnabled": false, "downlinkEnabled": false }
  Index 2: SECONDARY { "name": "meshforge", "uplinkEnabled": true, "downlinkEnabled": true }
'''
    live = tmpl.capture_live_state(fake_info)
    assert live["meshtastic"]["region"] == "US"
    assert live["meshtastic"]["modem_preset"] == "SHORT_TURBO"
    assert live["meshtastic"]["channel_num"] == 8
    bridges = live["meshtastic"]["bridge_channels"]
    assert len(bridges) == 1
    assert bridges[0]["name"] == "meshforge"
