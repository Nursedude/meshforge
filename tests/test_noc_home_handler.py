"""Tests for NocHomeHandler — the NOC operator landing (TUI workflow arc).

Covers the transport probes (with the enabled-at-boot gate so intentionally-off
transports read 'off', not red), the menu structure, and that actions route to
existing views rather than reimplementing them. Pure-ish: TUI dialog is mocked.
"""

from unittest.mock import MagicMock, patch

from handlers.noc_home import NocHomeHandler


def _make():
    h = NocHomeHandler()
    h.ctx = MagicMock()
    return h


class TestStructure:
    def test_ids(self):
        h = _make()
        assert h.handler_id == "noc_home"
        assert h.menu_section == "main"
        assert h.menu_items()[0][0] == "n"

    def test_execute_routes_to_view(self):
        h = _make()
        h.execute("n")
        h.ctx.safe_call.assert_called_once_with("NOC Home", h._noc_home)


class TestRnsProbe:
    @patch('service_remediation.service_enabled_here', return_value=False)
    def test_disabled_rnsd_reads_off_not_down(self, _m):
        # gateway-only / non-RNS box: rnsd intentionally off -> no false alarm.
        assert _make()._probe_rns() == ("off", False)

    @patch('utils.service_check.check_service')
    @patch('service_remediation.service_enabled_here', return_value=True)
    def test_enabled_but_not_running_is_down(self, _m_en, m_chk):
        m_chk.return_value = MagicMock(available=False)
        assert _make()._probe_rns() == ("down", False)

    @patch('utils.service_check.check_rns_shared_instance', return_value=True)
    @patch('utils.service_check.check_service')
    @patch('service_remediation.service_enabled_here', return_value=True)
    def test_running_with_shared_instance_is_up(self, _m_en, m_chk, _m_si):
        m_chk.return_value = MagicMock(available=True)
        assert _make()._probe_rns() == ("up", True)

    @patch('utils.service_check.check_rns_shared_instance', return_value=False)
    @patch('utils.service_check.check_service')
    @patch('service_remediation.service_enabled_here', return_value=True)
    def test_running_but_wedged_shared_instance_is_degraded(self, _m_en, m_chk, _m_si):
        # The #68/#69 wedge class: daemon up, shared instance not answering.
        m_chk.return_value = MagicMock(available=True)
        assert _make()._probe_rns() == ("degraded", True)


class TestSvcState:
    @patch('utils.service_check.check_service')
    @patch('service_remediation.service_enabled_here', return_value=True)
    def test_running(self, _m_en, m_chk):
        m_chk.return_value = MagicMock(available=True)
        assert _make()._svc_state("meshtasticd") == ("up", "running")

    @patch('service_remediation.service_enabled_here', return_value=False)
    def test_disabled_reads_off(self, _m_en):
        assert _make()._svc_state("meshforge-map")[0] == "off"


class TestViewLoopRoutes:
    def _patched_handler(self, rns=("up", True)):
        h = _make()
        h._svc_state = MagicMock(return_value=("up", "running"))
        h._probe_meshcore = MagicMock(return_value="off")
        h._probe_rns = MagicMock(return_value=rns)
        # make safe_call actually invoke the routed callable (like the real one)
        h.ctx.safe_call = lambda _name, fn, *a, **k: fn()
        return h

    @patch('service_remediation.collect_degraded_services', return_value=[])
    def test_back_returns_cleanly(self, _m_deg):
        h = self._patched_handler()
        h.ctx.dialog.menu.return_value = "back"
        h._noc_home()  # should print snapshot and return without error

    @patch('service_remediation.collect_degraded_services', return_value=[])
    def test_status_routes_to_dashboard_view(self, _m_deg):
        h = self._patched_handler()
        h.ctx.dialog.menu.side_effect = ["status", "back"]
        h._noc_home()
        h.ctx.registry.dispatch.assert_any_call("dashboard", "status")

    @patch('service_remediation.offer_service_fix')
    @patch('service_remediation.collect_degraded_services', return_value=[])
    def test_rns_repair_routes_to_offer_service_fix(self, _m_deg, m_offer):
        h = self._patched_handler(rns=("degraded", True))
        h.ctx.dialog.menu.side_effect = ["rns_repair", "back"]
        h._noc_home()
        m_offer.assert_called_once_with(h.ctx, "rnsd", True)
