"""Tests for the NOC fix-routing primitive (service state -> remediation actions).

This is the shared map any monitoring view uses to turn a degraded service into
an in-app fix (TUI workflow arc, 2026-05-29). Pure logic — no TUI needed.
"""

from unittest.mock import MagicMock, patch

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


class TestServiceEnabledGate:
    """The profile gate: only offer a fix for a service this box is configured
    to run (unit installed AND enabled-at-boot). The deployment profile is NOT
    the gate — a 'full'-profile gateway box reports maps=True yet disables the
    map unit (the moc3 case)."""

    @patch('utils.service_check.is_service_unit_installed', return_value=True)
    @patch('utils.service_check.check_systemd_service', return_value=(False, True))
    def test_installed_and_enabled_is_expected(self, _m_chk, _m_inst):
        from service_remediation import service_enabled_here
        assert service_enabled_here("meshforge-map") is True

    @patch('utils.service_check.is_service_unit_installed', return_value=True)
    @patch('utils.service_check.check_systemd_service', return_value=(False, False))
    def test_installed_but_disabled_is_gated_out(self, _m_chk, _m_inst):
        # moc3: unit exists but disabled -> intentionally off -> no nag.
        from service_remediation import service_enabled_here
        assert service_enabled_here("meshforge-map") is False

    @patch('utils.service_check.is_service_unit_installed', return_value=False)
    def test_absent_unit_is_gated_out(self, _m_inst):
        from service_remediation import service_enabled_here
        assert service_enabled_here("meshforge-maps") is False


class TestCollectDegraded:
    @patch('utils.service_check.check_service')
    def test_collects_down_skips_available_and_not_installed(self, m_check):
        from service_remediation import collect_degraded_services
        from utils.service_check import ServiceState

        def fake(svc):
            r = MagicMock()
            if svc == "rnsd":
                r.available, r.state = False, ServiceState.NOT_RUNNING
            elif svc == "meshtasticd":
                r.available, r.state = True, ServiceState.AVAILABLE
            else:  # meshforge-maps: no unit on this box
                r.available, r.state = False, ServiceState.NOT_INSTALLED
            return r

        m_check.side_effect = fake
        out = collect_degraded_services(["rnsd", "meshtasticd", "meshforge-maps"])
        assert out == [("rnsd", False)]


class TestChooserGate:
    @patch('service_remediation.service_enabled_here', return_value=False)
    def test_chooser_skips_intentionally_off_service(self, _m_enabled):
        # rnsd is known + down but disabled here -> chooser not shown, no nag.
        from service_remediation import offer_service_fix_chooser
        ctx = MagicMock()
        assert offer_service_fix_chooser(ctx, [("rnsd", False)]) is False
        ctx.dialog.menu.assert_not_called()

    @patch('service_remediation.service_enabled_here', return_value=True)
    def test_chooser_shown_for_enabled_degraded(self, _m_enabled):
        from service_remediation import offer_service_fix_chooser
        ctx = MagicMock()
        ctx.dialog.menu.return_value = "__done__"  # dismiss immediately
        assert offer_service_fix_chooser(ctx, [("rnsd", False)]) is True
        ctx.dialog.menu.assert_called_once()

    @patch('service_remediation.service_enabled_here', return_value=False)
    def test_offer_service_fix_gated_returns_none(self, _m_enabled):
        from service_remediation import offer_service_fix
        ctx = MagicMock()
        assert offer_service_fix(ctx, "meshforge-map", running=False) is None


class TestRnsdGuidedRepair:
    """rnsd's failure modes (wedged @rns, stale auth, config drift) need more
    than a restart — it gets the guided repair wizard as an extra action."""

    def test_rnsd_gets_guided_repair_action(self):
        from service_remediation import _actions_for
        labels = [a.label for a in _actions_for(MagicMock(), "rnsd", running=True)]
        assert "Restart rnsd" in labels
        assert "Repair rnsd (guided wizard)" in labels

    def test_rnsd_down_offers_start_restart_and_repair(self):
        from service_remediation import _actions_for
        labels = [a.label for a in _actions_for(MagicMock(), "rnsd", running=False)]
        assert labels == ["Start rnsd", "Restart rnsd", "Repair rnsd (guided wizard)"]

    def test_other_services_get_no_repair_action(self):
        from service_remediation import _actions_for
        labels = [a.label for a in _actions_for(MagicMock(), "meshtasticd", running=True)]
        assert not any("Repair" in lbl for lbl in labels)

    def test_repair_action_routes_to_diagnostics_handler(self):
        from service_remediation import _rns_repair_action
        diag = MagicMock()
        ctx = MagicMock()
        ctx.registry.get_handler.return_value = diag
        ok, _msg = _rns_repair_action(ctx).apply()
        ctx.registry.get_handler.assert_called_once_with("rns_diagnostics")
        diag._rns_repair_menu.assert_called_once()
        assert ok is True

    def test_repair_action_handles_missing_handler(self):
        from service_remediation import _rns_repair_action
        ctx = MagicMock()
        ctx.registry.get_handler.return_value = None
        ok, msg = _rns_repair_action(ctx).apply()
        assert ok is False
        assert "not available" in msg
