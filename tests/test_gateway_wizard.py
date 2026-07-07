"""Tests for handlers/gateway_wizard.py — the guided SF↔MeshForge↔RNS setup.

No hardware required: the argv-builders are pure, and the step logic is exercised
with a scripted dialog + patched remediation/preflight. The live end-to-end proof
(step 5's RX probe) is a manual drive on a real box.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from handlers.gateway_wizard import GatewayWizardHandler, GATEWAY_ROLES, LORA_PROFILES  # noqa: E402
from guided_flow import StepResult, StepStatus  # noqa: E402


class FakeDialog:
    def __init__(self, menu=None):
        self._menu = list(menu or [])
        self.msgboxes = []
        self.textboxes = []

    def menu(self, title, text, choices, **kw):
        return self._menu.pop(0) if self._menu else None

    def msgbox(self, title, text, **kw):
        self.msgboxes.append((title, text))

    def textbox(self, title, text, **kw):
        self.textboxes.append((title, text))

    def yesno(self, title, text, **kw):
        return True


class FakeCtx:
    def __init__(self, dialog, src_dir):
        self.dialog = dialog
        self.src_dir = src_dir
        self.report_calls = []

    def report_action(self, ok, st, sb, fail_title="", fail_body=""):
        self.report_calls.append(bool(ok))
        return bool(ok)

    def safe_call(self, name, method, *a, **k):
        return method(*a, **k)


def _handler(dialog=None):
    h = GatewayWizardHandler()
    ctx = FakeCtx(dialog or FakeDialog(), src_dir=Path("/opt/meshforge/src"))
    h.set_context(ctx)
    return h, ctx


class TestMenuAndRegistration(unittest.TestCase):
    def test_menu_item_feature_flagged_gateway(self):
        h, _ = _handler()
        items = h.menu_items()
        self.assertEqual(len(items), 1)
        tag, label, flag = items[0]
        self.assertEqual(tag, "wizard")
        self.assertEqual(flag, "gateway")

    def test_ids(self):
        h, _ = _handler()
        self.assertEqual(h.handler_id, "gateway_wizard")
        self.assertEqual(h.menu_section, "mesh_networks")


class TestArgvBuilders(unittest.TestCase):
    def test_lora_argv(self):
        h, _ = _handler()
        argv = h._lora_argv("us_default")
        self.assertEqual(argv[0], "bash")
        self.assertTrue(argv[1].endswith("scripts/configure_lora.sh"))
        self.assertEqual(argv[2:], ["--profile", "us_default"])

    def test_gateway_argv(self):
        h, _ = _handler()
        argv = h._gateway_argv("wh6gxz")
        self.assertTrue(argv[1].endswith("scripts/configure_gateway.sh"))
        self.assertEqual(argv[2], "wh6gxz")

    def test_service_argv(self):
        h, _ = _handler()
        self.assertTrue(h._service_argv()[1].endswith("scripts/install_gateway_service.sh"))

    def test_role_argvs(self):
        h, _ = _handler()
        self.assertEqual(h._role_preview_argv("full-gateway")[-2:], ["--role", "full-gateway"])
        self.assertEqual(h._role_write_argv("full-gateway")[-2:], ["--set-role", "full-gateway"])
        self.assertEqual(h._role_apply_argv()[-1], "--apply")
        for argv in (h._role_preview_argv("x"), h._role_write_argv("x"), h._role_apply_argv()):
            self.assertEqual(argv[0], "python3")
            self.assertTrue(argv[1].endswith("scripts/provision_role.py"))


class TestStepShape(unittest.TestCase):
    def test_five_steps_in_order(self):
        h, _ = _handler()
        steps = h._build_steps()
        self.assertEqual([s.key for s in steps],
                         ["role", "radio", "bridge", "service", "verify"])

    def test_verify_step_not_optional(self):
        h, _ = _handler()
        verify = [s for s in h._build_steps() if s.key == "verify"][0]
        self.assertFalse(verify.optional)

    def test_known_roles_and_profiles_nonempty(self):
        self.assertIn("full-gateway", GATEWAY_ROLES)
        self.assertIn("us_default", LORA_PROFILES)


class TestAdminGate(unittest.TestCase):
    @patch("handlers.gateway_wizard.os.geteuid", return_value=1000)
    @patch("handlers.gateway_wizard.GuidedFlow")
    def test_non_root_blocked(self, mock_flow, _geteuid):
        h, ctx = _handler()
        h._run_wizard()
        self.assertTrue(ctx.dialog.msgboxes)  # told to use Admin mode
        mock_flow.assert_not_called()          # flow never started

    @patch("handlers.gateway_wizard.os.geteuid", return_value=0)
    @patch("handlers.gateway_wizard.GuidedFlow")
    def test_root_runs_flow(self, mock_flow, _geteuid):
        h, ctx = _handler()
        h._run_wizard()
        mock_flow.assert_called_once()
        mock_flow.return_value.run.assert_called_once_with(ctx)


class TestRoleStep(unittest.TestCase):
    def test_role_recorded_on_success(self):
        h, ctx = _handler(FakeDialog(menu=["full-gateway"]))
        with patch.object(h, "_preview"), \
             patch.object(h, "_propose", return_value=(True, "applied")):
            result = h._r_role(ctx, {})
        self.assertEqual(result.status, StepStatus.DONE)
        self.assertEqual(result.data["role"], "full-gateway")

    def test_role_skipped_when_no_choice(self):
        h, ctx = _handler(FakeDialog(menu=[None]))
        result = h._r_role(ctx, {})
        self.assertEqual(result.status, StepStatus.SKIPPED)

    def test_role_failed_when_declined(self):
        h, ctx = _handler(FakeDialog(menu=["gateway-only"]))
        with patch.object(h, "_preview"), \
             patch.object(h, "_propose", return_value=(False, "declined")):
            result = h._r_role(ctx, {})
        self.assertEqual(result.status, StepStatus.FAILED)


class TestVerifyStep(unittest.TestCase):
    def test_verify_done_when_no_fails(self):
        h, ctx = _handler()
        with patch.object(h, "_reuse_preflight", return_value=(0, 1, "summary")):
            result = h._r_verify(ctx, {})
        self.assertEqual(result.status, StepStatus.DONE)
        self.assertTrue(ctx.dialog.textboxes)  # probe shown

    def test_verify_failed_when_fails(self):
        h, ctx = _handler()
        with patch.object(h, "_reuse_preflight", return_value=(2, 0, "summary")):
            result = h._r_verify(ctx, {})
        self.assertEqual(result.status, StepStatus.FAILED)


class TestServiceVerify(unittest.TestCase):
    def _fake_status(self, available):
        # Mirrors utils.service_check.ServiceStatus: __bool__ == .available.
        class _S:
            def __init__(s):
                s.state = type("E", (), {"value": "available" if available else "not_running"})()
                s.message = "svc active" if available else "svc down"
            def __bool__(s):
                return available
        return _S()

    def test_verify_true_when_service_available(self):
        h, ctx = _handler()
        with patch("utils.service_check.check_service", return_value=self._fake_status(True)):
            ok, msg = h._v_service(ctx, {})
        self.assertTrue(ok)

    def test_verify_false_when_service_down(self):
        h, ctx = _handler()
        with patch("utils.service_check.check_service", return_value=self._fake_status(False)):
            ok, msg = h._v_service(ctx, {})
        self.assertFalse(ok)

    def test_verify_false_on_probe_error(self):
        h, ctx = _handler()
        with patch("utils.service_check.check_service", side_effect=RuntimeError("boom")):
            ok, msg = h._v_service(ctx, {})
        self.assertFalse(ok)
        self.assertIn("could not check", msg)


class TestReusePreflight(unittest.TestCase):
    def test_counts_fails_and_warns(self):
        h, ctx = _handler()
        from handlers.gateway_preflight import _OK, _FAIL, _WARN
        fake_pf = MagicMock()
        fake_pf._check_lxmf.return_value = (_OK, "lxmf ok", None)
        fake_pf._check_meshtasticd.return_value = (_FAIL, "meshtasticd down", "start it")
        fake_pf._check_rnsd.return_value = (_OK, "rnsd ok", None)
        fake_pf._check_channel_uplink.return_value = ((_WARN, "no uplink", "enable"), [])
        fake_pf._check_gateway_config_channel.return_value = (_OK, "chan ok", None)
        fake_pf._check_gateway_identity.return_value = (_OK, "id ok", None)
        fake_pf._check_nomadnet_identity_match.return_value = (_OK, "nn ok", None)
        with patch("handlers.gateway_preflight.GatewayPreflightHandler", return_value=fake_pf):
            fails, warns, text = h._reuse_preflight(ctx)
        self.assertEqual(fails, 1)
        self.assertEqual(warns, 1)
        self.assertNotIn("\033[", text)  # ANSI stripped

    def test_preflight_exception_counts_as_fail(self):
        h, ctx = _handler()
        fake_pf = MagicMock()
        fake_pf._check_lxmf.side_effect = RuntimeError("boom")
        with patch("handlers.gateway_preflight.GatewayPreflightHandler", return_value=fake_pf):
            fails, warns, text = h._reuse_preflight(ctx)
        self.assertGreaterEqual(fails, 1)


if __name__ == "__main__":
    unittest.main()
