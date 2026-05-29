"""Tests for the NOC fix-routing primitive (service state -> remediation actions).

This is the shared map any monitoring view uses to turn a degraded service into
an in-app fix (TUI workflow arc, 2026-05-29). Pure logic — no TUI needed.
"""

from service_remediation import service_fix_actions


class TestServiceFixActions:
    def test_unknown_service_yields_no_actions(self):
        # Don't pretend to fix something this box doesn't own.
        assert service_fix_actions("postgresql", running=False) == []
        assert service_fix_actions("sshd", running=True) == []

    def test_stopped_known_service_offers_start_then_restart(self):
        actions = service_fix_actions("meshtasticd", running=False)
        labels = [a.label for a in actions]
        assert labels == ["Start meshtasticd", "Restart meshtasticd"]

    def test_running_but_degraded_offers_restart_only(self):
        # Up-but-unhealthy (e.g. active-but-unreachable) -> restart, not start.
        actions = service_fix_actions("meshtasticd", running=True)
        labels = [a.label for a in actions]
        assert labels == ["Restart meshtasticd"]

    def test_actions_require_admin(self):
        # start/restart need root; the surface enforces the Viewer/Admin boundary.
        for action in service_fix_actions("rnsd", running=False):
            assert action.requires_admin is True

    def test_known_services_cover_the_noc_core(self):
        for svc in ("meshtasticd", "rnsd", "mosquitto", "meshforge-gateway",
                    "meshforge", "meshforge-map"):
            assert service_fix_actions(svc, running=False), f"{svc} should be fixable"

    def test_apply_contract_is_callable(self):
        # apply must be callable returning the (ok, msg) contract — we don't
        # invoke it here (it would touch systemd), just confirm the shape.
        action = service_fix_actions("rnsd", running=True)[0]
        assert callable(action.apply)
