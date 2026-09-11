"""Tests for the role-aware fleet provisioner (scripts/provision_role.py).

Covers role resolution (inherits/merge), the pure plan/diff engine against
mocked SSOT observe functions, action→SSOT-call mapping, external-role skip,
the masking invariant, and deployment.json role read/write.

Run: python3 -m pytest tests/test_provision_role.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "scripts", _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import provision_role as pr  # noqa: E402


# --------------------------------------------------------------------------
# Role resolution
# --------------------------------------------------------------------------

CATALOG = {
    "roles": {
        "primary": {
            "singleton": True,
            "services": {"meshtasticd": "enabled", "rnsd": "enabled",
                         "meshforge-map": "enabled"},
        },
        "full-gateway": {
            "services": {"meshtasticd": "enabled", "rnsd": "enabled",
                         "meshforge-map": "enabled", "meshforge-maps": "enabled"},
        },
        "cloud-publisher": {
            "inherits": "full-gateway",
            "singleton": True,
            "services": {"meshforge-cloud-push.timer": "enabled"},
        },
        "gateway-only": {
            "services": {"meshtasticd": "enabled", "rnsd": "enabled",
                         "meshforge-map": "disabled", "meshforge-maps": "absent"},
        },
        "meshanchor-noc": {
            "provisioned_by": "meshanchor",
            "services": {"rnsd": "enabled", "meshanchor-daemon": "enabled"},
        },
    }
}


class TestResolveRole:
    def test_inherits_merges_parent_services(self):
        r = pr.resolve_role(CATALOG, "cloud-publisher")
        # parent services present...
        assert r["services"]["meshtasticd"] == "enabled"
        assert r["services"]["meshforge-map"] == "enabled"
        # ...plus child's own
        assert r["services"]["meshforge-cloud-push.timer"] == "enabled"
        assert r["singleton"] is True

    def test_no_inherits_returns_own(self):
        r = pr.resolve_role(CATALOG, "gateway-only")
        assert r["services"]["meshforge-map"] == "disabled"
        assert "inherits" not in r

    def test_unknown_role_raises(self):
        with pytest.raises(KeyError):
            pr.resolve_role(CATALOG, "nope")

    def test_load_roles_requires_roles_key(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 0\n")
        with pytest.raises(ValueError):
            pr.load_roles(bad)


# --------------------------------------------------------------------------
# plan() — observe functions mocked
# --------------------------------------------------------------------------

def _mock_observe(running, enabled, installed, masked=False):
    return (
        patch.object(pr, "check_systemd_service", return_value=(running, enabled)),
        patch.object(pr, "is_service_unit_installed", return_value=installed),
        patch.object(pr, "is_service_masked", return_value=masked),
    )


def _plan_one(unit, desired, running, enabled, installed, masked=False):
    role_def = {"services": {unit: desired}}
    m1, m2, m3 = _mock_observe(running, enabled, installed, masked)
    with m1, m2, m3:
        return pr.plan(role_def)


class TestPlanUnitStates:
    def test_enabled_when_active_is_noop(self):
        a = [x for x in _plan_one("meshforge-map", "enabled", True, True, True)
             if x.item == "meshforge-map"][0]
        assert a.verb == "noop"

    def test_enabled_when_inactive_enables(self):
        a = [x for x in _plan_one("meshforge-map", "enabled", False, False, True)
             if x.item == "meshforge-map"][0]
        assert a.verb == "enable"

    def test_enabled_when_absent_warns_required(self):
        a = [x for x in _plan_one("meshforge-map", "enabled", False, False, False)
             if x.item == "meshforge-map"][0]
        assert a.verb == "warn" and a.required is True

    def test_disabled_when_active_disables(self):
        a = [x for x in _plan_one("meshforge-map", "disabled", True, True, True)
             if x.item == "meshforge-map"][0]
        assert a.verb == "disable"

    def test_disabled_when_absent_is_noop(self):
        a = [x for x in _plan_one("meshforge-map", "disabled", False, False, False)
             if x.item == "meshforge-map"][0]
        assert a.verb == "noop"

    def test_absent_when_present_warns_not_required(self):
        a = [x for x in _plan_one("meshforge-maps", "absent", False, False, True)
             if x.item == "meshforge-maps"][0]
        assert a.verb == "warn" and a.required is False

    def test_absent_when_missing_is_noop(self):
        a = [x for x in _plan_one("meshforge-maps", "absent", False, False, False)
             if x.item == "meshforge-maps"][0]
        assert a.verb == "noop"


class TestPlanServiceOverrides:
    """Per-node service_overrides (instance-local deployment.json) waivers."""

    def _plan_with_override(self, override, running, enabled, installed):
        # full-gateway expects meshforge-gateway enabled; the override waives it.
        role_def = {"services": {"meshforge-gateway": "enabled"}}
        m1, m2, m3 = _mock_observe(running, enabled, installed)
        with m1, m2, m3:
            return pr.plan(role_def, {"meshforge-gateway": override})

    def test_waiver_with_reason_is_nonblocking_advisory(self):
        """moc2 case: unit absent, role wants enabled, but a reasoned waiver
        downgrades the would-be blocking warn to a non-blocking advisory — so
        the box stops failing the drift check."""
        a = [x for x in self._plan_with_override(
                {"state": "disabled", "reason": "RF-sparse site, bridge intentionally off"},
                running=False, enabled=False, installed=False)
             if x.item == "meshforge-gateway"][0]
        assert a.verb == "warn"
        assert a.required is False                      # non-blocking → dry-run exit 0
        assert "RF-sparse" in a.detail                  # reason is surfaced (loud)
        assert a.desired == "waived:disabled"

    def test_waiver_without_reason_stays_blocking(self):
        """An unexplained waiver is hidden drift — NOT honored."""
        a = [x for x in self._plan_with_override(
                {"state": "disabled"}, running=False, enabled=False, installed=False)
             if x.item == "meshforge-gateway"][0]
        assert a.verb == "warn"
        assert a.required is True                       # still blocks
        assert "NOT honored" in a.detail

    def test_waived_unit_is_not_converged(self):
        """A waived unit produces no enable/disable action even when it diverges
        from the role (left as the operator set it)."""
        actions = self._plan_with_override(
            {"state": "disabled", "reason": "x"},
            running=False, enabled=False, installed=True)
        verbs = {x.verb for x in actions if x.item == "meshforge-gateway"}
        assert verbs == {"warn"}                         # never enable/disable

    def test_no_override_unchanged_behavior(self):
        """Without an override, the unit converges normally (regression guard)."""
        a = [x for x in _plan_one("meshforge-gateway", "enabled", False, False, True)
             if x.item == "meshforge-gateway"][0]
        assert a.verb == "enable"

    def test_read_overrides_parses_deployment_json(self, tmp_path, monkeypatch):
        dj = tmp_path / "deployment.json"
        dj.write_text(json.dumps({
            "role": "full-gateway",
            "service_overrides": {"meshforge-gateway": {"state": "disabled", "reason": "y"}},
        }))
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", dj)
        ov = pr.read_overrides()
        assert ov["meshforge-gateway"]["reason"] == "y"

    def test_read_overrides_absent_key_is_empty(self, tmp_path, monkeypatch):
        dj = tmp_path / "deployment.json"
        dj.write_text(json.dumps({"role": "full-gateway"}))
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", dj)
        assert pr.read_overrides() == {}


class TestPlanMaskingInvariant:
    def _plan_with_rival(self, rnsd_enabled, rival_installed, rival_masked):
        role_def = {"services": {"rnsd": "enabled" if rnsd_enabled else "disabled"}}
        # rnsd itself: installed+active; rival per args. Route by unit name.
        def inst(name):
            return rival_installed if name == "meshanchor-daemon" else True
        def masked(name):
            return rival_masked if name == "meshanchor-daemon" else False
        with patch.object(pr, "check_systemd_service", return_value=(True, True)), \
             patch.object(pr, "is_service_unit_installed", side_effect=inst), \
             patch.object(pr, "is_service_masked", side_effect=masked):
            return pr.plan(role_def)

    def test_rival_present_unmasked_gets_masked(self):
        acts = self._plan_with_rival(True, True, False)
        m = [a for a in acts if a.item == "mask:meshanchor-daemon"][0]
        assert m.verb == "mask"

    def test_rival_already_masked_is_noop(self):
        acts = self._plan_with_rival(True, False, True)
        m = [a for a in acts if a.item == "mask:meshanchor-daemon"][0]
        assert m.verb == "noop"

    def test_rival_absent_no_mask_action(self):
        acts = self._plan_with_rival(True, False, False)
        assert not [a for a in acts if a.item.startswith("mask:")]

    def test_no_rnsd_no_masking(self):
        acts = self._plan_with_rival(False, True, False)
        assert not [a for a in acts if a.item.startswith("mask:")]


# --------------------------------------------------------------------------
# apply_action — SSOT calls mocked
# --------------------------------------------------------------------------

class TestApplyAction:
    def test_enable_calls_enable_service_with_start(self):
        a = pr.Action("meshforge-map", "inactive/disabled", "enabled", "enable")
        with patch.object(pr, "enable_service", return_value=(True, "ok")) as m:
            assert pr.apply_action(a) is True
        m.assert_called_once_with("meshforge-map", start=True)

    def test_disable_calls_stop_then_disable(self):
        a = pr.Action("meshforge-map", "active/enabled", "disabled", "disable")
        with patch.object(pr, "stop_service", return_value=(True, "stopped")) as ms, \
             patch.object(pr, "disable_service", return_value=(True, "disabled")) as md:
            assert pr.apply_action(a) is True
        ms.assert_called_once_with("meshforge-map")
        md.assert_called_once_with("meshforge-map")

    def test_mask_calls_mask_service_with_bare_name(self):
        a = pr.Action("mask:meshanchor-daemon", "present", "masked", "mask")
        with patch.object(pr, "mask_service", return_value=(True, "masked")) as m:
            assert pr.apply_action(a) is True
        m.assert_called_once_with("meshanchor-daemon")

    def test_noop_and_warn_never_mutate(self):
        with patch.object(pr, "enable_service") as me, \
             patch.object(pr, "mask_service") as mm:
            pr.apply_action(pr.Action("x", "a", "b", "noop"))
            pr.apply_action(pr.Action("y", "a", "b", "warn"))
        me.assert_not_called()
        mm.assert_not_called()

    def test_enable_failure_propagates(self):
        a = pr.Action("meshforge-map", "absent", "enabled", "enable")
        with patch.object(pr, "enable_service", return_value=(False, "boom")):
            assert pr.apply_action(a) is False


# --------------------------------------------------------------------------
# foundation_actions() — cross-cutting permission foundation (D1/D3, mf.4/#73)
# --------------------------------------------------------------------------

class TestFoundationActions:
    def test_clean_yields_single_noop(self):
        with patch("utils.fleet_foundation.audit_foundation", return_value=[]):
            actions = pr.foundation_actions()
        assert len(actions) == 1
        assert actions[0].verb == "noop"
        assert actions[0].item == "foundation:perms"

    def test_drift_yields_required_foundation_action(self):
        with patch("utils.fleet_foundation.audit_foundation",
                   return_value=["/etc/reticulum root:root ...", "~/.cache/meshforge ..."]):
            actions = pr.foundation_actions()
        assert len(actions) == 1
        a = actions[0]
        assert a.verb == "foundation" and a.required is True
        assert "2 drift item(s)" in a.current
        assert "/etc/reticulum" in a.detail

    def test_detail_is_truncated(self):
        with patch("utils.fleet_foundation.audit_foundation",
                   return_value=["x" * 500]):
            a = pr.foundation_actions()[0]
        assert len(a.detail) <= 300 and a.detail.endswith("...")

    def test_audit_failure_is_nonblocking_warn(self):
        with patch("utils.fleet_foundation.audit_foundation",
                   side_effect=RuntimeError("boom")):
            a = pr.foundation_actions()[0]
        assert a.verb == "warn" and a.required is False
        assert "skipped" in a.detail


class TestApplyFoundationAction:
    def test_foundation_verb_calls_apply_foundation(self):
        a = pr.Action("foundation:perms", "1 drift item(s)", "operator-owned",
                      "foundation", required=True)
        with patch("utils.fleet_foundation.apply_foundation",
                   return_value=["chown ...", "apply RNS-tree perms ..."]) as m:
            assert pr.apply_action(a) is True
        m.assert_called_once()
        assert "applied 2 foundation step(s)" in a.result

    def test_foundation_apply_failure_propagates(self):
        a = pr.Action("foundation:perms", "1 drift", "operator-owned", "foundation")
        with patch("utils.fleet_foundation.apply_foundation",
                   side_effect=RuntimeError("nope")):
            assert pr.apply_action(a) is False
        assert "failed" in a.result


# --------------------------------------------------------------------------
# main() — external skip + role read/write
# --------------------------------------------------------------------------

class TestMainExternalSkip:
    def test_external_role_returns_2(self, tmp_path, capsys):
        rf = tmp_path / "roles.yaml"
        import yaml
        rf.write_text(yaml.safe_dump(CATALOG))
        rc = pr.main(["--role", "meshanchor-noc", "--roles-file", str(rf)])
        assert rc == 2
        assert "EXTERNAL" in capsys.readouterr().err


class TestRoleReadWrite:
    def test_write_then_read_roundtrip(self, tmp_path, monkeypatch):
        dj = tmp_path / "deployment.json"
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", dj)
        pr.write_role("primary")
        assert pr.read_role() == "primary"
        # preserves an existing profile key
        dj.write_text(json.dumps({"profile": "full", "role": "primary"}))
        pr.write_role("gateway-only")
        data = json.loads(dj.read_text())
        assert data["role"] == "gateway-only" and data["profile"] == "full"

    def test_read_role_missing_file_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", tmp_path / "nope.json")
        assert pr.read_role() is None


# --------------------------------------------------------------------------
# Fleet-aware singleton enforcement (v2)
# --------------------------------------------------------------------------

class TestParseFleetHosts:
    def test_strips_comments_and_blanks(self, tmp_path):
        f = tmp_path / "fleet_hosts"
        f.write_text("# header\nmoc\n\nmoc1  # inline\n  moc2\n#full-line\n")
        assert pr.parse_fleet_hosts(f) == ["moc", "moc1", "moc2"]

    def test_missing_file_empty(self, tmp_path):
        assert pr.parse_fleet_hosts(tmp_path / "nope") == []


class TestValidateFleet:
    def test_clean_fleet_no_violations(self):
        rm = {"(self)": "primary", "moc": "full-gateway",
              "moc1": "cloud-publisher", "moc2": "full-gateway",
              "moc3": "gateway-only"}
        assert pr.validate_fleet(CATALOG, rm) == []

    def test_duplicate_singleton_flagged(self):
        rm = {"(self)": "cloud-publisher", "moc1": "cloud-publisher"}
        v = pr.validate_fleet(CATALOG, rm)
        assert len(v) == 1 and "cloud-publisher" in v[0] and "2 hosts" in v[0]

    def test_unknown_role_flagged(self):
        rm = {"moc": "frobnicator"}
        v = pr.validate_fleet(CATALOG, rm)
        assert any("unknown role 'frobnicator'" in x for x in v)

    def test_unset_role_not_a_violation(self):
        rm = {"moc": None, "(self)": "primary"}
        assert pr.validate_fleet(CATALOG, rm) == []

    def test_two_full_gateways_ok_not_singleton(self):
        # full-gateway is not a singleton — multiples are fine
        rm = {"moc": "full-gateway", "moc2": "full-gateway"}
        assert pr.validate_fleet(CATALOG, rm) == []


class TestGatherFleetRoles:
    def test_self_and_peers_via_injected_ssh(self):
        # peer query is `<ssh> <host> <remote>`; fake ssh echoes a role per host
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            host = argv[1]
            out = {"moc": "full-gateway", "moc1": "cloud-publisher"}.get(host, "")
            return MagicMock(returncode=0, stdout=out + "\n", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            rm = pr.gather_fleet_roles(["moc", "moc1"], self_role="primary")
        assert rm == {"(self)": "primary", "moc": "full-gateway", "moc1": "cloud-publisher"}

    def test_unreachable_peer_is_none(self):
        with patch("subprocess.run", side_effect=OSError("no ssh")):
            rm = pr.gather_fleet_roles(["dead"], self_role="primary")
        assert rm["dead"] is None


# --------------------------------------------------------------------------
# config_delta_actions — map `defaults` asserted against real state (not mutated).
# The defaults are code-baked (caches/cap) or deployment-specific (bbox anchor),
# so these PASS when satisfied and WARN on genuine drift; they never mutate.
# --------------------------------------------------------------------------

from utils.node_history import DEFAULT_DIRECTORY_MAX_ROWS as _REAL_CAP  # noqa: E402

_MAP_ROLE = {"services": {"meshforge-map": "enabled"}}
_NON_MAP_ROLE = {"services": {"meshforge-gateway": "enabled"}}
_DEFAULTS = {
    "node_directory": {"bbox_filter": True, "node_cap": _REAL_CAP,
                       "operator_position_required": True},
    "response_caches": {"directory": True, "geojson": True, "topology": True},
}


def _by_item(actions, item):
    return next((a for a in actions if a.item == item), None)


class TestConfigDeltaActions:
    def test_non_map_role_emits_nothing(self):
        assert pr.config_delta_actions(_NON_MAP_ROLE, _DEFAULTS) == []

    def test_node_cap_match_is_noop(self):
        a = _by_item(pr.config_delta_actions(_MAP_ROLE, _DEFAULTS), "delta:node-cap")
        assert a is not None and a.verb == "noop"
        assert a.current == str(_REAL_CAP) and a.desired == str(_REAL_CAP)

    def test_node_cap_drift_warns(self):
        d = {"node_directory": {"node_cap": _REAL_CAP + 1}}
        a = _by_item(pr.config_delta_actions(_MAP_ROLE, d), "delta:node-cap")
        assert a is not None and a.verb == "warn" and not a.required
        assert "drifted" in a.detail

    def test_response_caches_present_is_noop(self):
        a = _by_item(pr.config_delta_actions(_MAP_ROLE, _DEFAULTS), "delta:response-caches")
        assert a is not None and a.verb == "noop"

    def test_bbox_anchor_position_file_is_noop(self, tmp_path, monkeypatch):
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "operator_position.json").write_text('{"lat": 19.4, "lon": -155.3}')
        monkeypatch.setattr(pr, "get_real_user_home", lambda: tmp_path)
        a = _by_item(pr.config_delta_actions(_MAP_ROLE, _DEFAULTS), "delta:bbox-anchor")
        assert a is not None and a.verb == "noop" and a.current == "operator_position.json"

    def test_bbox_anchor_external_bbox_is_noop(self, tmp_path, monkeypatch):
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "map_settings.json").write_text('{"external_bulk_bbox": {"lat_min": 19}}')
        monkeypatch.setattr(pr, "get_real_user_home", lambda: tmp_path)
        a = _by_item(pr.config_delta_actions(_MAP_ROLE, _DEFAULTS), "delta:bbox-anchor")
        assert a is not None and a.verb == "noop" and a.current == "external_bulk_bbox"

    def test_bbox_anchor_missing_warns(self, tmp_path, monkeypatch):
        (tmp_path / ".config" / "meshforge").mkdir(parents=True)  # no anchor at all
        monkeypatch.setattr(pr, "get_real_user_home", lambda: tmp_path)
        a = _by_item(pr.config_delta_actions(_MAP_ROLE, _DEFAULTS), "delta:bbox-anchor")
        assert a is not None and a.verb == "warn" and not a.required
        assert "bbox_filter is OFF" in a.detail


class TestWriteRoleClobberGuard:
    """QA 2026-07-05: write_role must never silently replace an unreadable
    deployment.json (that destroys service_overrides + profile, hfm #1),
    and its write is atomic (the old bare write_text was itself a
    torn-file source on this power-event-prone fleet)."""

    def _patch_path(self, monkeypatch, tmp_path):
        p = tmp_path / "deployment.json"
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", p)
        return p

    def test_corrupt_existing_file_refused_loud(self, monkeypatch, tmp_path):
        p = self._patch_path(monkeypatch, tmp_path)
        p.write_text('{"role": "collector", "service_overrides": {"x"')  # torn
        with pytest.raises(RuntimeError, match="refusing to overwrite"):
            pr.write_role("full-gateway")
        # the torn file is intact for forensics — nothing was clobbered
        assert "service_overrides" in p.read_text()

    def test_non_object_existing_file_refused(self, monkeypatch, tmp_path):
        p = self._patch_path(monkeypatch, tmp_path)
        p.write_text('["not", "an", "object"]')
        with pytest.raises(RuntimeError, match="not a JSON object"):
            pr.write_role("full-gateway")

    def test_healthy_file_merges_and_preserves_keys(self, monkeypatch,
                                                    tmp_path):
        import json as _json
        p = self._patch_path(monkeypatch, tmp_path)
        p.write_text(_json.dumps({
            "role": "collector", "profile": "monitor",
            "service_overrides": {"meshforge-map": {
                "state": "disabled", "reason": "gateway-only box"}}}))
        pr.write_role("full-gateway")
        data = _json.loads(p.read_text())
        assert data["role"] == "full-gateway"
        assert data["profile"] == "monitor"                    # preserved
        assert data["service_overrides"]["meshforge-map"]["state"] == \
            "disabled"                                         # preserved

    def test_fresh_file_created(self, monkeypatch, tmp_path):
        import json as _json
        p = self._patch_path(monkeypatch, tmp_path)
        pr.write_role("collector")
        assert _json.loads(p.read_text()) == {"role": "collector"}


class TestSetRoleCleanExit:
    """QA re-review 2026-07-05: --set-role on a torn deployment.json must
    exit 2 (clean 'could not proceed'), not dump an uncaught traceback."""

    def test_set_role_torn_file_exits_2_not_traceback(self, monkeypatch,
                                                      tmp_path, capsys):
        p = tmp_path / "deployment.json"
        p.write_text('{"role": "x", "service_over')  # torn
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", p)
        rc = pr.main(["--set-role", "primary"])
        assert rc == 2
        assert "ERROR" in capsys.readouterr().err
        assert "service_over" in p.read_text()  # file preserved

    def test_set_role_healthy_file_still_works(self, monkeypatch, tmp_path):
        import json as _json
        p = tmp_path / "deployment.json"
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", p)
        rc = pr.main(["--set-role", "collector"])
        assert rc == 0
        assert _json.loads(p.read_text())["role"] == "collector"


# --------------------------------------------------------------------------
# user_timers — systemd --user timers, declare-and-observe (2026-08-09)
#
# WHY THIS EXISTS: before the declaration, "never enrolled here" and "enrolled,
# then someone disabled it" were the SAME filesystem observation, so
# synth_soak_degraded had to call both inert and a silently-retired exerciser
# was indistinguishable from a box that never ran one.
#
# Every test injects enrollment. None of them may consult the real box's
# ~/.config/systemd/user (feedback_tests_must_pin_ambient_state).
# --------------------------------------------------------------------------

_UT_CATALOG = {
    "roles": {
        "full-gateway": {
            "services": {"rnsd": "enabled"},
            "user_timers": {"meshforge-synth-soak.timer": "enabled",
                            "meshforge-propagation-soak.timer": "enabled"},
        },
        "collector": {
            "inherits": "full-gateway",
            "user_timers": {"meshforge-synth-soak.timer": "absent",
                            "meshforge-propagation-soak.timer": "absent"},
        },
        "no-timers": {"services": {"rnsd": "enabled"}},
    }
}


def _enrolled(mapping):
    """Patch the SSOT enrollment reader with an explicit per-unit answer."""
    return patch.object(pr, "user_timer_enrolled",
                        side_effect=lambda unit: mapping.get(unit))


def _body(present):
    """Pin whether the user unit BODY exists in ~/.config/systemd/user.

    Ambient by default (it stats the real operator's home), so every test that
    reaches the not-enrolled branch must say which of the two states it means:
    `False` = never enrolled here, `True` = enrolled once and switched off.
    """
    return patch.object(pr, "_user_timer_unit_installed",
                        side_effect=lambda unit: present)


class TestUserTimerInheritance:
    def test_user_timers_inherit_like_services(self):
        """The whole point of merging them the same way: a key that inherited
        for `services` but silently not for `user_timers` would hand the next
        person a role with no coverage and no error."""
        r = pr.resolve_role(_UT_CATALOG, "collector")
        assert r["services"]["rnsd"] == "enabled"          # inherited
        assert r["user_timers"]["meshforge-synth-soak.timer"] == "absent"  # child wins

    def test_child_override_wins_over_parent(self):
        parent = pr.resolve_role(_UT_CATALOG, "full-gateway")
        child = pr.resolve_role(_UT_CATALOG, "collector")
        assert parent["user_timers"]["meshforge-synth-soak.timer"] == "enabled"
        assert child["user_timers"]["meshforge-synth-soak.timer"] == "absent"

    def test_role_without_the_key_gets_an_empty_map(self):
        r = pr.resolve_role(_UT_CATALOG, "no-timers")
        assert r["user_timers"] == {}


class TestUserTimerActions:
    SYNTH = "meshforge-synth-soak.timer"

    def test_declared_enabled_and_enrolled_is_noop(self):
        with _enrolled({self.SYNTH: True}):
            acts = pr._user_timer_actions({self.SYNTH: "enabled"})
        assert [a.verb for a in acts] == ["noop"]

    def test_declared_enabled_but_disabled_is_REQUIRED_warn(self):
        """THE case this whole change exists for — the drill.

        Someone disables the exerciser on a box whose role says it runs it.
        Before: synth_soak_degraded went inert and nothing said a word.

        The unit BODY is present (that is what makes this "switched off" and
        not "never enrolled"); pinned explicitly rather than read off whatever
        box runs the suite (feedback_tests_must_pin_ambient_state)."""
        with _enrolled({self.SYNTH: False}), _body(True):
            acts = pr._user_timer_actions({self.SYNTH: "enabled"})
        assert len(acts) == 1
        a = acts[0]
        assert a.verb == "warn" and a.required is True
        assert a.current == "not-enabled" and a.desired == "enabled"
        assert "systemctl --user enable --now" in a.detail, (
            "a blocking warn must name the command that clears it")

    def test_declared_absent_but_enrolled_is_REQUIRED_warn(self):
        """The other direction: an organ appears on a box that never declared
        it. Silent extra load is drift too."""
        with _enrolled({self.SYNTH: True}):
            acts = pr._user_timer_actions({self.SYNTH: "absent"})
        assert acts[0].verb == "warn" and acts[0].required is True

    def test_declared_absent_and_not_enrolled_is_noop(self):
        with _enrolled({self.SYNTH: False}):
            acts = pr._user_timer_actions({self.SYNTH: "absent"})
        assert [a.verb for a in acts] == ["noop"]

    def test_unobservable_is_advisory_never_drift(self):
        """Unknown is not drift. Flattening it to 'not enabled' would make an
        unreadable dir look like a disabled organ (honest_failure_modes #1)."""
        with _enrolled({self.SYNTH: None}):
            acts = pr._user_timer_actions({self.SYNTH: "enabled"})
        assert acts[0].verb == "warn" and acts[0].required is False
        assert "unreadable" in acts[0].detail

    def test_unknown_desired_state_is_advisory(self):
        with _enrolled({self.SYNTH: True}):
            acts = pr._user_timer_actions({self.SYNTH: "sometimes"})
        assert acts[0].verb == "warn" and acts[0].required is False

    def test_no_declarations_emits_nothing(self):
        assert pr._user_timer_actions({}) == []


class TestUserTimersNeverConverged:
    """--apply must never enable/disable a user unit.

    A converge sweep that can start units is exactly how the 2026-07-24
    incident happened (a restart loop started a unit disabled by design), and
    user units are a scope root cannot even reach (#82). Detection yes,
    convergence by hand.
    """
    SYNTH = "meshforge-synth-soak.timer"

    @pytest.mark.parametrize("declared,enrolled", [
        ("enabled", False), ("absent", True), ("enabled", True),
        ("disabled", False), ("enabled", None),
    ])
    @pytest.mark.parametrize("body", [True, False])
    def test_no_action_verb_is_ever_executable(self, declared, enrolled, body):
        with _enrolled({self.SYNTH: enrolled}), _body(body):
            acts = pr._user_timer_actions({self.SYNTH: declared})
        for a in acts:
            assert a.verb in ("noop", "warn"), (
                f"user timer produced executable verb {a.verb!r} — --apply "
                f"would act on a systemd --user unit")

    def test_apply_action_skips_the_required_warn(self):
        """Belt and braces: even handed the drift action, apply does nothing."""
        with _enrolled({self.SYNTH: False}), _body(True):
            acts = pr._user_timer_actions({self.SYNTH: "enabled"})
        with patch.object(pr, "enable_service") as en, \
                patch.object(pr, "stop_service") as st:
            pr.apply_action(acts[0])
        en.assert_not_called()
        st.assert_not_called()
        assert acts[0].result == "skipped"


class TestShippedCatalogDeclaresUserTimers:
    """The real docs/fleet_roles.yaml, not a fixture.

    user_timers inherits, so a role that inherits full-gateway and does NOT
    run the soaks must say `absent` out loud. Without this test, adding a new
    inheriting role silently signs it up for two exercisers it does not run —
    and the box would page for drift the author never declared.
    """

    def test_every_inheriting_role_declares_its_soak_timers(self):
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        parents_with_timers = {
            name for name, node in catalog["roles"].items()
            if node.get("user_timers")}
        for name, node in catalog["roles"].items():
            if node.get("inherits") in parents_with_timers:
                assert node.get("user_timers"), (
                    f"role '{name}' inherits '{node['inherits']}' which "
                    f"declares user_timers, but declares none of its own — "
                    f"it silently inherits them. Say `absent` explicitly.")

    def test_declared_states_are_all_valid(self):
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        for name, node in catalog["roles"].items():
            for unit, state in (node.get("user_timers") or {}).items():
                assert state in pr.VALID_UNIT_STATES, (
                    f"{name}.{unit} = {state!r}")


class TestWaiverIsActuallyChecked:
    """Aim-audit fix (2026-09-09). ``plan()`` computed ``cur`` on the override
    path and then never compared it to the waived state, so a VIOLATED waiver
    read as an honored exception — and ``probe_role_drift``, which fires only on
    blocking warnings, read ``clean`` over it.
    """

    def test_violated_waiver_blocks(self, monkeypatch):
        """Declared OFF while the unit is ENABLED is hidden drift, not an
        exception. This is the case that used to read as honored."""
        import provision_role as pr
        monkeypatch.setattr(pr, "_unit_current", lambda u: "active/enabled")
        acts = pr.plan({"services": {"u.service": "enabled"}},
                       {"u.service": {"state": "disabled", "reason": "declared off"}})
        a = [x for x in acts if x.item == "u.service"][0]
        assert a.required is True, "a waiver the box does not honor must block"
        assert "NOT MET" in a.detail

    def test_running_while_declared_disabled_discloses_but_does_not_block(
            self, monkeypatch):
        """The manager-box case. VALID_UNIT_STATES is enablement-only, so
        'disabled' cannot express 'not running'. The declaration IS met; paging
        would be paging about a human decision. It must SAY SO instead —
        silence is what let this read as a clean 'honored' for two days."""
        import provision_role as pr
        monkeypatch.setattr(pr, "_unit_current", lambda u: "active/disabled")
        acts = pr.plan({"services": {"u.service": "enabled"}},
                       {"u.service": {"state": "disabled", "reason": "runs RADIO-OFF"}})
        a = [x for x in acts if x.item == "u.service"][0]
        assert a.required is False, "must not page about an operator decision"
        assert "RUNNING" in a.detail and "UNVERIFIED" in a.detail
        assert "runs RADIO-OFF" in a.detail, "the operator's reason stays visible"

    def test_absent_satisfies_a_disabled_waiver(self, monkeypatch):
        """Regression pin: the first cut of the fix required an exact match and
        broke this — absent is off and then some. An existing test caught it."""
        import provision_role as pr
        monkeypatch.setattr(pr, "_unit_current", lambda u: "absent")
        acts = pr.plan({"services": {"u.service": "enabled"}},
                       {"u.service": {"state": "disabled", "reason": "RF-sparse site"}})
        a = [x for x in acts if x.item == "u.service"][0]
        assert a.required is False
        assert "NOT MET" not in a.detail


# --------------------------------------------------------------------------
# 2026-09-09 pass-2 review findings 4, 5, 6, 14
# --------------------------------------------------------------------------

class TestMaskingInvariantHonorsOverrides:
    """Finding 4: the masking invariant read the ROLE's `services['rnsd']`, not
    the override-honored EFFECTIVE state.

    A box whose reasoned waiver says "meshanchor-daemon owns @rns here" still
    got `mask:meshanchor-daemon (required)` planned — and `--apply` masked the
    RNS host the waiver names as owner. Issue #69 in reverse, executed by the
    converge; in dry-run the same line paged role_drift over an honored
    exception forever.
    """
    ROLE = {"services": {"rnsd": "enabled"}}

    def _plan(self, overrides, rival_installed=True, rival_masked=False):
        def inst(name):
            return rival_installed if name == "meshanchor-daemon" else True

        def masked(name):
            return rival_masked if name == "meshanchor-daemon" else False

        with patch.object(pr, "check_systemd_service", return_value=(True, True)), \
             patch.object(pr, "is_service_unit_installed", side_effect=inst), \
             patch.object(pr, "is_service_masked", side_effect=masked):
            return pr.plan(self.ROLE, overrides)

    def test_reasoned_rnsd_waiver_never_plans_a_mask(self):
        """THE plant: an honored `rnsd: disabled` waiver naming the RNS host."""
        acts = self._plan({"rnsd": {"state": "disabled",
                                    "reason": "meshanchor-daemon owns @rns here"}})
        assert not [a for a in acts if a.verb == "mask"], (
            "an honored rnsd waiver must never plan a mask of the RNS owner")

    def test_skipped_mask_says_so_out_loud(self):
        """Silence would be its own defect — the operator must see WHY the
        #69 invariant did not fire on a box whose role declares rnsd."""
        acts = self._plan({"rnsd": {"state": "disabled",
                                    "reason": "meshanchor-daemon owns @rns here"}})
        m = [a for a in acts if a.item == "mask:meshanchor-daemon"][0]
        assert m.verb == "warn" and m.required is False
        assert "SKIPPED" in m.detail and "#69" in m.detail

    def test_apply_cannot_mask_through_an_honored_waiver(self):
        """The consequence, not the wiring: no change verb reaches the rival."""
        acts = self._plan({"rnsd": {"state": "disabled", "reason": "MA owns @rns"}})
        for a in acts:
            if a.item.startswith("mask:"):
                assert a.verb in ("noop", "warn")

    def test_waiver_that_declares_rnsd_enabled_still_masks(self):
        """Regression pin: honoring an override must not disarm the invariant
        when the override AGREES the box owns rnsd."""
        acts = self._plan({"rnsd": {"state": "enabled", "reason": "explicit"}})
        assert [a for a in acts if a.verb == "mask"]

    def test_unhonored_rnsd_override_refuses_to_mask(self):
        """Ownership is DISPUTED (no reason → not honored). Masking is
        destructive, so the ambiguous case does nothing and says why."""
        acts = self._plan({"rnsd": {"state": "disabled"}})
        assert not [a for a in acts if a.verb == "mask"]
        m = [a for a in acts if a.item == "mask:meshanchor-daemon"][0]
        assert m.required is False and "NOT honored" in m.detail

    def test_no_rival_installed_emits_no_skip_noise(self):
        """The advisory is about a real rival; don't invent a line for a box
        that has no meshanchor-daemon at all."""
        acts = self._plan({"rnsd": {"state": "disabled", "reason": "x"}},
                          rival_installed=False)
        assert not [a for a in acts if a.item.startswith("mask:")]


class TestOverrideStateIsValidated:
    """Finding 6: an override with a missing or misspelled `state` was honored
    unconditionally — `_waiver_enablement_met` returned True for anything
    outside the vocabulary and no caller reported it. A waiver nothing can
    check is honest_failure_modes #3 (a validator absorbing what the author
    cannot have meant), inside the very branch the 09-09 commit hardened.
    """

    def _one(self, override, cur="active/enabled"):
        with patch.object(pr, "_unit_current", lambda u: cur):
            acts = pr.plan({"services": {"u.service": "enabled"}},
                           {"u.service": override})
        return [a for a in acts if a.item == "u.service"][0]

    def test_missing_state_key_is_a_loud_error(self):
        """THE plant: `{reason: 'RF-sparse'}` on an enabled+active unit used to
        read `waived:? required=False intentional per-node exception`."""
        a = self._one({"reason": "RF-sparse"})
        assert a.verb == "warn" and a.required is True
        assert "no 'state' key" in a.detail and "NOT honored" in a.detail

    def test_misspelled_state_is_a_loud_error(self):
        a = self._one({"state": "off", "reason": "RF-sparse"})
        assert a.required is True
        assert "unknown state 'off'" in a.detail
        assert "absent|disabled|enabled" in a.detail, "name the vocabulary"

    def test_invalid_state_keeps_the_reason_visible(self):
        a = self._one({"state": "off", "reason": "RF-sparse"})
        assert "RF-sparse" in a.detail

    def test_valid_state_still_honored(self):
        """Regression pin: validation must not eat the honored path."""
        a = self._one({"state": "disabled", "reason": "RF-sparse"},
                      cur="inactive/disabled")
        assert a.required is False and "intentional per-node exception" in a.detail

    def test_waiver_enablement_met_no_longer_defaults_to_met(self):
        """The mechanism, pinned directly: an unchecked value must never read
        as satisfied again if a future caller forgets to validate."""
        assert pr._waiver_enablement_met("active/enabled", "off") is False

    def test_absent_waiver_on_a_running_unit_discloses_RUNNING(self):
        """Sibling gap: `waived: absent` on an installed-and-RUNNING unit got
        the plain 'honored' text — the same silence one vocabulary word over
        from the `disabled` case that was fixed."""
        a = self._one({"state": "absent", "reason": "no radio"},
                      cur="active/disabled")
        assert a.required is False, "still an operator decision, not a page"
        assert "RUNNING" in a.detail and "UNVERIFIED" in a.detail
        assert "'absent' cannot" in a.detail
        assert "no radio" in a.detail


class TestUserTimerNeverEnrolledIsAdvisory:
    """Finding 5: every fresh full-gateway / gateway-only provision exited 1,
    in dry-run AND `--apply`, with a remediation nothing could run — no
    installer copies the soak user units and `--apply` never touches user
    scope. The Gateway Wizard chains `--set-role` + `--apply`, so a newcomer's
    first provision failed at 'Role & Variant'; probe_role_drift paged
    "converge with provision_role --apply", which could not work.

    The cure keeps BOTH claims by splitting the one state into two:
    never-enrolled (advisory + an enrollment command that exists) vs
    enrolled-then-switched-off (still REQUIRED drift — the 2026-08-09 case).
    """
    SYNTH = "meshforge-synth-soak.timer"

    def test_never_enrolled_is_advisory_not_drift(self):
        with _enrolled({self.SYNTH: False}), _body(False):
            a = pr._user_timer_actions({self.SYNTH: "enabled"})[0]
        assert a.verb == "warn"
        assert a.required is False, (
            "a fresh box must not fail provisioning on a unit nothing installs")
        assert a.current == "not-installed"

    def test_never_enrolled_names_the_command_that_fixes_it(self):
        """An advisory that only says 'converge by hand' is a silenced warning.
        This one has to name a path the product actually has."""
        with _enrolled({self.SYNTH: False}), _body(False):
            a = pr._user_timer_actions({self.SYNTH: "enabled"})[0]
        assert "--enroll-user-timers" in a.detail

    def test_switched_off_is_still_required_drift(self):
        with _enrolled({self.SYNTH: False}), _body(True):
            a = pr._user_timer_actions({self.SYNTH: "enabled"})[0]
        assert a.required is True and "INSTALLED here" in a.detail

    def test_unreadable_user_dir_is_not_drift(self):
        with _enrolled({self.SYNTH: False}), _body(None):
            a = pr._user_timer_actions({self.SYNTH: "enabled"})[0]
        assert a.required is False and "not judged" in a.detail

    def test_fresh_gateway_role_has_no_blocking_user_timer_warn(self):
        """End of the domain, not a proxy: the SHIPPED full-gateway role, on a
        box that has never enrolled either soak, contributes zero blocking
        warnings from the user_timers leg."""
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        rd = pr.resolve_role(catalog, "full-gateway")
        assert rd["user_timers"], "the role must still DECLARE them"
        with _enrolled({u: False for u in rd["user_timers"]}), _body(False):
            acts = pr._user_timer_actions(rd["user_timers"])
        assert acts and not [a for a in acts if a.required], (
            "a fresh full-gateway provision must not exit 1 on user timers")

    def test_declared_absent_but_enrolled_names_its_command_too(self):
        with _enrolled({self.SYNTH: True}), _body(True):
            a = pr._user_timer_actions({self.SYNTH: "absent"})[0]
        assert a.required is True
        assert "systemctl --user disable --now" in a.detail


class TestEnrollUserTimers:
    """The enrollment path that makes the declared state ACHIEVABLE.

    Deliberately NOT reachable from `--apply` (a converge sweep that starts
    units is the 2026-07-24 incident); it is an explicit operator command.
    The copy half is drilled for REAL against a scratch home — only the bus
    call is injected.
    """
    SYNTH = "meshforge-synth-soak.timer"

    def _runner(self, calls, ok=True):
        def run(argv):
            calls.append(list(argv))
            return (ok, "" if ok else "Failed to connect to bus")
        return run

    def test_copies_both_bodies_and_enables(self, tmp_path):
        calls = []
        ok, lines = pr.enroll_user_timers({self.SYNTH: "enabled"},
                                          home=str(tmp_path),
                                          runner=self._runner(calls))
        dest = tmp_path / ".config" / "systemd" / "user"
        assert ok, lines
        assert (dest / self.SYNTH).is_file(), "the timer body must land"
        assert (dest / "meshforge-synth-soak.service").is_file(), (
            "the timer is useless without the service it triggers")
        assert calls[0] == ["daemon-reload"]
        assert ["enable", "--now", self.SYNTH] in calls

    def test_content_matches_the_shipped_template(self, tmp_path):
        pr.enroll_user_timers({self.SYNTH: "enabled"}, home=str(tmp_path),
                              runner=self._runner([]))
        tmpl, _ = pr.user_timer_template_pair(self.SYNTH)
        landed = tmp_path / ".config" / "systemd" / "user" / self.SYNTH
        assert landed.read_text() == tmpl.read_text()

    def test_declared_absent_is_never_enrolled(self):
        """The safety argument: this enables ONLY what the role declares
        enabled, and never disables anything."""
        calls = []
        ok, lines = pr.enroll_user_timers({self.SYNTH: "absent"},
                                          home="/nonexistent",
                                          runner=self._runner(calls))
        assert ok and calls == []
        assert "nothing to enroll" in lines[0]

    def test_missing_template_fails_loud(self, tmp_path):
        calls = []
        ok, lines = pr.enroll_user_timers({"meshforge-nope.timer": "enabled"},
                                          home=str(tmp_path),
                                          runner=self._runner(calls))
        assert ok is False
        assert any("no shipped template body" in l for l in lines)
        assert calls == [], "never touch the bus for a unit we could not stage"

    def test_bus_failure_reports_copied_but_not_enabled(self, tmp_path):
        """Half-done must not read as done (honest_failure_modes #9): the
        bodies ARE on disk, and the report has to say exactly that."""
        calls = []
        ok, lines = pr.enroll_user_timers({self.SYNTH: "enabled"},
                                          home=str(tmp_path),
                                          runner=self._runner(calls, ok=False))
        assert ok is False
        assert any("installed but NOT enabled" in l for l in lines)
        assert (tmp_path / ".config" / "systemd" / "user" / self.SYNTH).is_file()

    def test_no_operator_resolvable_is_an_error_not_a_silent_pass(self):
        with patch.object(pr, "resolve_operator_home", return_value=None):
            ok, lines = pr.enroll_user_timers({self.SYNTH: "enabled"})
        assert ok is False and "no operator user resolved" in lines[0]

    def test_apply_never_reaches_enrollment(self):
        """The invariant this change must not break: --apply stays
        user-unit-free. No plan verb maps to enrollment."""
        assert "enroll" not in pr.PLAN_CHANGE_VERBS
        a = pr.Action(self.SYNTH, "not-installed", "enabled", "warn",
                      required=False)
        with patch.object(pr, "enroll_user_timers") as en:
            pr.apply_action(a)
        en.assert_not_called()


class TestPrintUnitStateAgreesWithPlan:
    """Finding 14: `--print-unit-state` read only `services`, so every unit the
    role declares under `user_timers` printed `unspecified` — "no declaration"
    for a unit the same role's plan() renders. Tooling that gates a timer
    install on the token was told there was nothing to enroll.
    """
    SYNTH = "meshforge-synth-soak.timer"

    def _token(self, unit, capsys, tmp_path, monkeypatch, deployment=None):
        dj = tmp_path / "deployment.json"
        dj.write_text(json.dumps(deployment or {"role": "full-gateway"}))
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", dj)
        rc = pr.main(["--role", "full-gateway", "--print-unit-state", unit])
        assert rc == 0
        return capsys.readouterr().out.strip()

    def test_declared_user_timer_is_not_unspecified(self, capsys, tmp_path,
                                                    monkeypatch):
        """THE plant, against the SHIPPED catalog: full-gateway declares this
        timer enabled and the token said 'unspecified'."""
        assert self._token(self.SYNTH, capsys, tmp_path, monkeypatch) == "enabled"

    def test_token_matches_what_plan_declares(self, capsys, tmp_path,
                                              monkeypatch):
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        declared = pr.resolve_role(catalog, "full-gateway")["user_timers"]
        for unit, state in declared.items():
            assert self._token(unit, capsys, tmp_path, monkeypatch) == state

    def test_service_token_unchanged(self, capsys, tmp_path, monkeypatch):
        """Regression pin — the Gateway Wizard gates its service install on
        this exact call."""
        assert self._token("meshforge-map", capsys, tmp_path,
                           monkeypatch) == "enabled"

    def test_undeclared_unit_still_unspecified(self, capsys, tmp_path,
                                               monkeypatch):
        assert self._token("nothing-here.service", capsys, tmp_path,
                           monkeypatch) == "unspecified"

    def test_reasoned_service_override_still_wins(self, capsys, tmp_path,
                                                  monkeypatch):
        tok = self._token("meshforge-map", capsys, tmp_path, monkeypatch,
                          deployment={"role": "full-gateway",
                                      "service_overrides": {
                                          "meshforge-map": {"state": "disabled",
                                                            "reason": "heavy"}}})
        assert tok == "waived:disabled"

    def test_unhonored_override_does_not_print_waived(self, capsys, tmp_path,
                                                      monkeypatch):
        """plan() refuses an override with an invalid state, so this must too —
        `waived:?` was a token no consumer could act on."""
        tok = self._token("meshforge-map", capsys, tmp_path, monkeypatch,
                          deployment={"role": "full-gateway",
                                      "service_overrides": {
                                          "meshforge-map": {"reason": "typo'd"}}})
        assert tok == "enabled"


class TestEnrollUserTimersCLI:
    """The CLI leg of the enrollment path — argument wiring and the root gate.

    `systemctl --user` from root addresses root's own (usually absent) session
    bus, not the operator's (#82): a root run would report success for units
    the operator's manager never saw. Refuse loudly instead.
    """

    def _roles(self, tmp_path, timers):
        f = tmp_path / "roles.yaml"
        f.write_text(json.dumps({"roles": {"r": {"services": {},
                                                 "user_timers": timers}}}))
        return f

    def test_root_is_refused_not_bridged(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(pr.os, "geteuid", lambda: 0)
        with patch.object(pr, "enroll_user_timers") as en:
            rc = pr.main(["--role", "r", "--enroll-user-timers",
                          "--roles-file", str(self._roles(
                              tmp_path, {"meshforge-synth-soak.timer": "enabled"}))])
        assert rc == 2
        assert en.call_count == 0, "root must not reach the user bus at all"
        assert "AS THE OPERATOR" in capsys.readouterr().err

    def test_operator_run_passes_the_roles_user_timers(self, tmp_path,
                                                       monkeypatch):
        monkeypatch.setattr(pr.os, "geteuid", lambda: 1000)
        declared = {"meshforge-synth-soak.timer": "enabled",
                    "meshforge-propagation-soak.timer": "absent"}
        with patch.object(pr, "enroll_user_timers",
                          return_value=(True, [])) as en:
            rc = pr.main(["--role", "r", "--enroll-user-timers",
                          "--roles-file", str(self._roles(tmp_path, declared))])
        assert rc == 0
        en.assert_called_once_with(declared)

    def test_failure_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pr.os, "geteuid", lambda: 1000)
        with patch.object(pr, "enroll_user_timers",
                          return_value=(False, ["[FAIL       ] boom"])):
            rc = pr.main(["--role", "r", "--enroll-user-timers",
                          "--roles-file", str(self._roles(
                              tmp_path, {"meshforge-synth-soak.timer": "enabled"}))])
        assert rc == 1, "a half-done enrollment must not exit 0"


# --------------------------------------------------------------------------
# service_dropins — declared systemd drop-in fragments (2026-09-11)
# --------------------------------------------------------------------------

class TestParseDropinKey:
    """The key names a path written under /etc/systemd/system AS ROOT, so it is
    an input-validation boundary, not a trusted string."""

    def test_accepts_the_declared_shape(self):
        assert pr.parse_dropin_key("meshtasticd.service.d/50-retry.conf") == (
            "meshtasticd.service.d", "50-retry.conf")

    @pytest.mark.parametrize("bad", [
        "/etc/systemd/system/x.service.d/50.conf",   # absolute
        "../../etc/passwd",                          # traversal
        "meshtasticd.service.d/../../../etc/x.conf",  # traversal, 4 segments
        "a/b/c.conf",                                # too many segments
        "50-retry.conf",                             # no unit dir
        "meshtasticd.service/50-retry.conf",         # dir not .service.d
        "meshtasticd.service.d/50-retry.txt",        # file not .conf
        "meshtasticd.service.d/",                    # empty file segment
        "",                                          # empty
    ])
    def test_rejects_anything_else(self, bad):
        with pytest.raises(ValueError):
            pr.parse_dropin_key(bad)

    def test_traversal_never_escapes_the_dropin_root(self):
        """RED proof: the rejected traversal would otherwise resolve OUTSIDE
        /etc/systemd/system, which is the whole reason this is validated."""
        escaped = (pr.SYSTEM_DROPIN_ROOT / "../../etc/passwd").resolve()
        assert not str(escaped).startswith(str(pr.SYSTEM_DROPIN_ROOT))
        with pytest.raises(ValueError):
            pr.parse_dropin_key("../../etc/passwd")


class TestDropinActions:
    KEY = "meshtasticd.service.d/50-sx1262-retry-patience.conf"

    def _actions(self, declared, present, tpl_exists=True):
        real_is_file = Path.is_file

        def fake_is_file(self):
            s = str(self)
            if s.startswith(str(pr.SYSTEM_DROPIN_ROOT)):
                return present
            if s.startswith(str(pr.SYSTEMD_TEMPLATE_DIR)):
                return tpl_exists
            return real_is_file(self)

        with patch.object(Path, "is_file", fake_is_file):
            return pr._dropin_actions(declared)

    def test_enabled_and_missing_plans_an_install(self):
        a, = self._actions({self.KEY: "enabled"}, present=False)
        assert a.verb == "dropin"
        assert a.item == f"dropin:{self.KEY}"
        assert a.current == "absent" and a.desired == "enabled"
        assert str(pr.SYSTEMD_TEMPLATE_DIR) in a.detail

    def test_enabled_and_present_is_noop_that_admits_what_it_did_not_check(self):
        """A converged line must not claim more than it measured — content is
        NOT compared, and the detail says so."""
        a, = self._actions({self.KEY: "enabled"}, present=True)
        assert a.verb == "noop"
        assert "not compared" in a.detail

    def test_declared_absent_but_present_warns_and_never_removes(self):
        """Mirror image of never restoring a deliberate stop: never delete a
        deliberate addition."""
        a, = self._actions({self.KEY: "absent"}, present=True)
        assert a.verb == "warn"
        assert a.required is False
        assert "not auto-removed" in a.detail
        assert a.verb not in pr.PLAN_CHANGE_VERBS

    def test_declared_absent_and_missing_is_noop(self):
        a, = self._actions({self.KEY: "absent"}, present=False)
        assert a.verb == "noop"

    def test_declared_with_no_template_warns_instead_of_planning(self):
        """A declaration nothing in the repo can satisfy is a finding, not a
        silent skip — the writer-with-no-reader class."""
        a, = self._actions({self.KEY: "enabled"}, present=False, tpl_exists=False)
        assert a.verb == "warn"
        assert "no template" in a.detail

    def test_unknown_state_warns(self):
        a, = self._actions({self.KEY: "sometimes"}, present=False)
        assert a.verb == "warn"
        assert "unknown desired state" in a.detail

    def test_malformed_key_warns_rather_than_raising(self):
        """A bad key in the SSOT must surface as a finding, never crash the
        whole converge for every other unit on the box."""
        a, = self._actions({"../escape.conf": "enabled"}, present=False)
        assert a.verb == "warn"

    def test_no_declaration_plans_nothing(self):
        assert self._actions({}, present=False) == []


class TestDropinsInheritAndReachThePlan:
    CATALOG = {
        "roles": {
            "parent": {"services": {},
                       "service_dropins": {"a.service.d/10-p.conf": "enabled"}},
            "child": {"inherits": "parent", "services": {},
                      "service_dropins": {"b.service.d/20-c.conf": "enabled"}},
            "overrider": {"inherits": "parent", "services": {},
                          "service_dropins": {"a.service.d/10-p.conf": "absent"}},
        }
    }

    def test_child_inherits_parent_dropins(self):
        d = pr.resolve_role(self.CATALOG, "child")["service_dropins"]
        assert d == {"a.service.d/10-p.conf": "enabled",
                     "b.service.d/20-c.conf": "enabled"}

    def test_child_can_override_to_absent(self):
        d = pr.resolve_role(self.CATALOG, "overrider")["service_dropins"]
        assert d == {"a.service.d/10-p.conf": "absent"}

    def test_plan_emits_the_dropin_action(self):
        role = {"services": {}, "user_timers": {},
                "service_dropins": {"a.service.d/10-p.conf": "enabled"}}
        with patch.object(Path, "is_file", lambda self: "templates" in str(self)):
            verbs = {a.verb for a in pr.plan(role)}
        assert "dropin" in verbs

    def test_dropin_is_a_change_verb_so_role_drift_counts_it(self):
        assert "dropin" in pr.PLAN_CHANGE_VERBS


class TestShippedRoleDeclaresTheDropin:
    """The SSOT on disk, not a fixture — if the real declaration is dropped or
    its template renamed, this fails."""

    def test_field_node_declares_the_sx1262_dropin_and_the_template_exists(self):
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        declared = pr.resolve_role(catalog, "field-node")["service_dropins"]
        key = "meshtasticd.service.d/50-sx1262-retry-patience.conf"
        assert declared.get(key) == "enabled"
        tpl, _ = pr._dropin_paths(key)
        assert tpl.is_file(), f"declared drop-in has no template at {tpl}"
        # Parse DIRECTIVES only. The comment block names both section headers
        # in prose, so a naive split on the raw text lands in the commentary
        # rather than the config (caught by this test failing on its first run).
        section, seen = None, {}
        for line in tpl.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line
                continue
            seen[line] = section
        # Each directive under its REQUIRED section — putting either in the
        # wrong one silently no-ops and systemd reports no error.
        assert seen.get("StartLimitIntervalSec=0") == "[Unit]", seen
        assert seen.get("RestartSec=30") == "[Service]", seen


class TestInstallDropin:
    """The WRITE half. Exercised against a scratch root, with the privileged
    commands stubbed — the live sudo + systemd path is only proven on a box."""

    KEY = "meshtasticd.service.d/50-sx1262-retry-patience.conf"

    def _run(self, tmp_path, reload_ok=True, write_ok=True):
        calls = []

        def fake_cmd(argv, timeout=30):
            calls.append(argv)
            if argv[:2] == ["systemctl", "daemon-reload"]:
                return (reload_ok, "ok" if reload_ok else "dbus is down")
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
            return (True, "ok")

        def fake_write(path, content, timeout=10):
            if not write_ok:
                return (False, "permission denied")
            Path(path).write_text(content)
            return (True, "ok")

        with patch.object(pr, "SYSTEM_DROPIN_ROOT", tmp_path), \
             patch.object(pr, "_sudo_cmd_run", fake_cmd), \
             patch.object(pr, "_sudo_write", fake_write):
            ok, msg = pr._install_dropin(self.KEY)
        return ok, msg, calls

    def test_writes_the_template_verbatim_and_reloads(self, tmp_path):
        ok, msg, calls = self._run(tmp_path)
        assert ok, msg
        dest = tmp_path / "meshtasticd.service.d" / "50-sx1262-retry-patience.conf"
        tpl, _ = pr._dropin_paths(self.KEY)
        assert dest.read_text() == tpl.read_text()
        assert ["systemctl", "daemon-reload"] in calls

    def test_a_failed_daemon_reload_is_a_FAILURE_not_a_success(self, tmp_path):
        """The file on disk changes nothing until systemd re-reads it. A write
        without the reload is a converged-looking no-op — the reader/writer
        half-wiring class (honest_failure_modes #4), so it must report False."""
        ok, msg, _ = self._run(tmp_path, reload_ok=False)
        assert ok is False
        assert "daemon-reload FAILED" in msg

    def test_a_failed_write_never_claims_success(self, tmp_path):
        ok, msg, calls = self._run(tmp_path, write_ok=False)
        assert ok is False and "write" in msg
        assert ["systemctl", "daemon-reload"] not in calls, \
            "must not reload after a write that did not land"

    def test_a_bad_key_is_refused_before_any_privileged_call(self, tmp_path):
        calls = []
        with patch.object(pr, "SYSTEM_DROPIN_ROOT", tmp_path), \
             patch.object(pr, "_sudo_cmd_run", lambda *a, **k: calls.append(a) or (True, "")), \
             patch.object(pr, "_sudo_write", lambda *a, **k: calls.append(a) or (True, "")):
            ok, msg = pr._install_dropin("../../etc/passwd")
        assert ok is False
        assert calls == [], "validation must precede every privileged call"

    def test_apply_action_routes_the_dropin_verb(self, tmp_path):
        a = pr.Action(f"dropin:{self.KEY}", "absent", "enabled", "dropin")
        with patch.object(pr, "_install_dropin", return_value=(True, "installed")) as m:
            assert pr.apply_action(a) is True
        m.assert_called_once_with(self.KEY)
        assert a.result == "installed"
