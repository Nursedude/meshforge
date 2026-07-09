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


def test_list_templates_finds_longfast():
    # Re-exported 2026-07-09 from the live reference gateway (was
    # shortturbo_slot8; the fleet's soak-proven baseline is LONG_FAST/slot20).
    from handlers import _gateway_preflight_template as tmpl
    paths = tmpl.list_templates()
    assert any("longfast" in p.name for p in paths)


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


def test_load_templates_finds_both_radio_legs():
    """The fleet deliberately runs TWO radio profiles (operator decision
    2026-07-09): LONG_FAST/slot20 + SHORT_TURBO/slot8 joined by a
    cross-preset bridge. Both must ship as templates."""
    from handlers import _gateway_preflight_template as tmpl
    names = [t.get("name") for t in tmpl.load_templates()]
    assert "longfast-slot20-meshforge-bridge" in names
    assert "shortturbo-slot8-meshforge-bridge" in names


def _leg_template(preset, cnum, name):
    return {
        "name": name,
        "meshtastic": {
            "modem_preset": {"expected": preset, "severity": "fail"},
            "channel_num": {"expected": cnum, "severity": "fail"},
        },
    }


_LEGS = [_leg_template("LONG_FAST", 20, "longfast"),
         _leg_template("SHORT_TURBO", 8, "shortturbo")]


def test_best_match_picks_each_boxes_own_leg():
    """A SHORT_TURBO/8 box must be judged against the SHORT_TURBO template
    (0 fails), not false-failed by the LONG_FAST one — and vice versa."""
    from handlers import _gateway_preflight_template as tmpl
    live_st = {"meshtastic": {"modem_preset": "SHORT_TURBO", "channel_num": 8}}
    results, chosen, total = tmpl.check_template_drift_best(live_st, _LEGS)
    assert chosen == "shortturbo" and total == 2
    assert all(r[0] != tmpl._FAIL for r in results)

    live_lf = {"meshtastic": {"modem_preset": "LONG_FAST", "channel_num": 20}}
    results, chosen, _ = tmpl.check_template_drift_best(live_lf, _LEGS)
    assert chosen == "longfast"
    assert all(r[0] != tmpl._FAIL for r in results)


def test_best_match_no_leg_matches_stays_loud():
    """A genuinely drifted radio (matches NO template) still reports fails."""
    from handlers import _gateway_preflight_template as tmpl
    live = {"meshtastic": {"modem_preset": "MEDIUM_SLOW", "channel_num": 3}}
    results, chosen, _ = tmpl.check_template_drift_best(live, _LEGS)
    assert chosen in ("longfast", "shortturbo")
    assert sum(1 for r in results if r[0] == tmpl._FAIL) == 2


def test_best_match_no_templates_is_empty():
    from handlers import _gateway_preflight_template as tmpl
    results, chosen, total = tmpl.check_template_drift_best({}, [])
    assert results == [] and chosen is None and total == 0


def test_unobservable_radio_is_warn_not_fail():
    """A failed `meshtastic --info` leaves live values None/missing —
    unobservable is never drift (honest_failure_modes #1/#2; the 2026-07-09
    sweep manufactured 5 FAILs on a box whose radio query failed)."""
    from handlers import _gateway_preflight_template as tmpl
    template = {
        "meshtastic": {
            "region": {"expected": "US", "severity": "fail"},
            "modem_preset": {"expected": "LONG_FAST", "severity": "fail"},
            "bridge_channel_name": {"expected": "meshforge", "severity": "warn"},
            "bridge_channel_uplink_enabled": {"expected": True, "severity": "fail"},
        },
    }
    live = {"meshtastic": {}}  # --info never ran
    results = tmpl.check_template_drift(template, live)
    assert all(r[0] == tmpl._WARN for r in results), results
    assert all("unobservable" in r[1] for r in results)


def test_absent_gateway_config_is_warn_not_fail():
    """A box with no gateway.json (never a gateway) must read 'cannot
    verify', not 'drift' — the sweep false-FAILed every non-gateway box."""
    from handlers import _gateway_preflight_template as tmpl
    template = {
        "gateway": {
            "bridge_mode": {"expected": "mqtt_bridge", "severity": "fail"},
            "mqtt_region": {"expected": "US", "severity": "fail"},
        },
    }
    live = {"gateway": {}}
    results = tmpl.check_template_drift(template, live)
    assert all(r[0] == tmpl._WARN for r in results)


def test_observed_empty_channels_still_fail():
    """--info ran and returned NO uplinked channels: that is OBSERVED
    absence, and keeps its loud semantics (unobservable ≠ observed-empty)."""
    from handlers import _gateway_preflight_template as tmpl
    template = {
        "meshtastic": {
            "bridge_channel_uplink_enabled": {"expected": True, "severity": "fail"},
        },
    }
    live = {"meshtastic": {"bridge_channels": []}}
    results = tmpl.check_template_drift(template, live)
    assert results[0][0] == tmpl._FAIL


def test_present_but_null_gateway_field_is_observed_drift():
    """gateway.json was READ and lacks the field (key present, value None):
    that is OBSERVED misconfiguration, not unobservable — it must FAIL.
    (Fix re-review 2026-07-09: value-None conflated this with file-absent.)"""
    from handlers import _gateway_preflight_template as tmpl
    template = {"gateway": {"bridge_mode": {"expected": "mqtt_bridge", "severity": "fail"}}}
    live = {"gateway": {"bridge_mode": None, "mqtt_channel": "meshforge"}}
    results = tmpl.check_template_drift(template, live)
    assert results[0][0] == tmpl._FAIL
    assert "unobservable" not in results[0][1]


def test_observed_false_still_fails():
    """An observed wrong value (not None) keeps failing — the unobservable
    guard must not soften real drift."""
    from handlers import _gateway_preflight_template as tmpl
    template = {"gateway": {"bridge_mode": {"expected": "mqtt_bridge", "severity": "fail"}}}
    live = {"gateway": {"bridge_mode": "rns_transport"}}
    results = tmpl.check_template_drift(template, live)
    assert results[0][0] == tmpl._FAIL


def test_shared_instance_probe_derives_instance_name(monkeypatch):
    """Issue #82 class: with no explicit instance_name the probe must derive
    the box's configured one (e.g. 'volcano ai rns' → @rns/volcano ai rns),
    never assume 'default' — the 2026-07-09 sweep read a healthy custom-named
    rnsd as unreachable."""
    from utils import _port_detection as pd
    from utils.paths import ReticulumPaths
    seen = {}

    def fake_proc_check(socket_name):
        seen["socket"] = socket_name
        return True

    monkeypatch.setattr(pd, "_check_proc_net_unix", fake_proc_check)
    monkeypatch.setattr(ReticulumPaths, "get_configured_instance_name",
                        classmethod(lambda cls: "volcano ai rns"))
    info = pd.get_rns_shared_instance_info()
    assert info["available"] is True
    assert seen["socket"] == "rns/volcano ai rns"
    # Explicit name still wins over derivation.
    pd.get_rns_shared_instance_info("other")
    assert seen["socket"] == "rns/other"


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
