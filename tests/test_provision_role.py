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
