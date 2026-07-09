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
    def __init__(self, menu=None, yesno=None):
        self._menu = list(menu or [])
        self._yesno = list(yesno or [])
        self.msgboxes = []
        self.textboxes = []

    def menu(self, title, text, choices, **kw):
        return self._menu.pop(0) if self._menu else None

    def msgbox(self, title, text, **kw):
        self.msgboxes.append((title, text))

    def textbox(self, title, text, **kw):
        self.textboxes.append((title, text))

    def yesno(self, title, text, **kw):
        self.yesno_calls = getattr(self, "yesno_calls", 0) + 1
        return self._yesno.pop(0) if self._yesno else True


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

    def test_role_skipped_when_declined(self):
        # "Do nothing" at the remediation menu is a first-class decline —
        # recorded SKIPPED, never as a failure (2026-07-09 review).
        h, ctx = _handler(FakeDialog(menu=["gateway-only"]))
        with patch.object(h, "_preview"), \
             patch.object(h, "_propose", return_value=None):
            result = h._r_role(ctx, {})
        self.assertEqual(result.status, StepStatus.SKIPPED)

    def test_role_failed_when_apply_fails(self):
        h, ctx = _handler(FakeDialog(menu=["gateway-only"]))
        with patch.object(h, "_preview"), \
             patch.object(h, "_propose", return_value=(False, "boom")):
            result = h._r_role(ctx, {})
        self.assertEqual(result.status, StepStatus.FAILED)


class TestVerifyStep(unittest.TestCase):
    def test_verify_done_only_when_operator_confirms_probe(self):
        # Pre-flight clean is necessary, not sufficient — DONE requires the
        # operator to confirm the RX probe was OBSERVED (calibrated-claims
        # rule 7: the packet crossing is the consumer-of-record).
        h, ctx = _handler(FakeDialog(menu=["confirmed"]))
        with patch.object(h, "_reuse_preflight", return_value=(0, 1, "summary")), \
             patch.object(h, "_rx_probe_text", return_value="probe"):
            result = h._r_verify(ctx, {})
        self.assertEqual(result.status, StepStatus.DONE)
        self.assertIn("observed", result.message)
        self.assertTrue(ctx.dialog.textboxes)  # probe shown

    def test_verify_unconfirmed_probe_is_not_done(self):
        h, ctx = _handler(FakeDialog(menu=["later"]))
        with patch.object(h, "_reuse_preflight", return_value=(0, 0, "summary")), \
             patch.object(h, "_rx_probe_text", return_value="probe"):
            result = h._r_verify(ctx, {})
        self.assertEqual(result.status, StepStatus.SKIPPED)
        self.assertIn("NOT yet observed", result.message)

    def test_verify_cancelled_menu_is_not_done(self):
        h, ctx = _handler(FakeDialog(menu=[None]))
        with patch.object(h, "_reuse_preflight", return_value=(0, 0, "summary")), \
             patch.object(h, "_rx_probe_text", return_value="probe"):
            result = h._r_verify(ctx, {})
        self.assertNotEqual(result.status, StepStatus.DONE)

    def test_verify_failed_when_fails(self):
        h, ctx = _handler()
        with patch.object(h, "_reuse_preflight", return_value=(2, 0, "summary")), \
             patch.object(h, "_rx_probe_text", return_value="probe"):
            result = h._r_verify(ctx, {})
        self.assertEqual(result.status, StepStatus.FAILED)


class TestRxProbeText(unittest.TestCase):
    def _cfg(self, root="fleet", chan="mfbridge", idx=3):
        cfg = MagicMock()
        cfg.mqtt_bridge.root_topic = root
        cfg.mqtt_bridge.channel = chan
        cfg.meshtastic.channel = idx
        return cfg

    def test_probe_derived_from_rendered_config(self):
        # A hardcoded topic is the #34 topic-shape trap in probe form: the
        # probe must ride the values the gateway actually subscribes.
        h, _ = _handler()
        with patch("gateway.config.GatewayConfig.load", return_value=self._cfg()):
            text = h._rx_probe_text()
        self.assertIn("fleet/2/json/mfbridge/!deadbeef", text)
        self.assertIn('"channel":3', text)

    def test_probe_unreadable_config_says_so(self):
        h, _ = _handler()
        with patch("gateway.config.GatewayConfig.load", side_effect=OSError("nope")):
            text = h._rx_probe_text()
        self.assertIn("could not read gateway.json", text)
        self.assertIn("msh/2/json/meshforge/!deadbeef", text)  # honest fallback


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
    def test_counts_fails_and_warns_including_template_drift(self):
        h, ctx = _handler()
        from handlers.gateway_preflight import _OK, _FAIL, _WARN
        fake_pf = MagicMock()
        fake_pf.collect_results.return_value = (
            [(_OK, "lxmf ok", None),
             (_FAIL, "meshtasticd down", "start it"),
             (_WARN, "no uplink", "enable")],
            [(_WARN, "template drift: webserver port", "restore")],
        )
        with patch("handlers.gateway_preflight.GatewayPreflightHandler", return_value=fake_pf):
            fails, warns, text = h._reuse_preflight(ctx)
        self.assertEqual(fails, 1)
        self.assertEqual(warns, 2)  # template-drift results COUNT (2026-07-09 review)
        self.assertIn("template drift", text)
        self.assertNotIn("\033[", text)  # ANSI stripped

    def test_preflight_exception_counts_as_fail(self):
        h, ctx = _handler()
        fake_pf = MagicMock()
        fake_pf.collect_results.side_effect = RuntimeError("boom")
        with patch("handlers.gateway_preflight.GatewayPreflightHandler", return_value=fake_pf):
            fails, warns, text = h._reuse_preflight(ctx)
        self.assertGreaterEqual(fails, 1)

    def test_wizard_never_hand_enumerates_private_checks(self):
        # The check inventory SSOT is GatewayPreflightHandler.collect_results;
        # a hand-copied _check_* call list in the wizard silently drifts when
        # a check is added (it already omitted _run_template_drift once).
        import inspect
        import handlers.gateway_wizard as gw
        src = inspect.getsource(gw)
        self.assertNotIn("._check_", src)
        self.assertIn("collect_results", src)


class TestServiceStepRoleGate(unittest.TestCase):
    def test_role_declaring_no_gateway_skips_install(self):
        # Installing meshforge-gateway on a box whose role declares it
        # disabled/absent contradicts step 1 (probe_role_drift pages) — the
        # wizard must not do it silently (2026-07-09 review, high finding).
        for desired in ("disabled", "absent"):
            h, ctx = _handler(FakeDialog(yesno=[False]))  # decline "install anyway"
            with patch.object(h, "_gateway_unit_desired", return_value=desired), \
                 patch.object(h, "_propose") as propose:
                result = h._r_service(ctx, {})
            self.assertEqual(result.status, StepStatus.SKIPPED)
            propose.assert_not_called()

    def test_operator_can_override_role_gate(self):
        h, ctx = _handler(FakeDialog(yesno=[True]))  # "install anyway"
        with patch.object(h, "_gateway_unit_desired", return_value="disabled"), \
             patch.object(h, "_propose", return_value=(True, "installed")):
            result = h._r_service(ctx, {})
        self.assertEqual(result.status, StepStatus.DONE)

    def test_gateway_role_installs_without_prompt(self):
        for desired in ("enabled", "waived:disabled", "unknown", "unspecified"):
            h, ctx = _handler(FakeDialog())
            with patch.object(h, "_gateway_unit_desired", return_value=desired), \
                 patch.object(h, "_propose", return_value=(True, "installed")):
                result = h._r_service(ctx, {})
            self.assertEqual(result.status, StepStatus.DONE, desired)
            # PROMPT-ABSENCE is the claim: FakeDialog.yesno defaults to True,
            # so without this the test passes even if the gate wrongly fires.
            self.assertEqual(getattr(ctx.dialog, "yesno_calls", 0), 0, desired)

    def test_unit_desired_rejects_garbage_output(self):
        h, _ = _handler()
        for out in ("", "[python3 timed out after 30s]", "ERROR: no role\nweird"):
            with patch.object(h, "_capture_command", return_value=out):
                self.assertEqual(h._gateway_unit_desired(), "unknown", repr(out))

    def test_unit_desired_accepts_tokens(self):
        h, _ = _handler()
        for out, want in (("enabled\n", "enabled"), ("absent", "absent"),
                          ("waived:disabled\n", "waived:disabled")):
            with patch.object(h, "_capture_command", return_value=out):
                self.assertEqual(h._gateway_unit_desired(), want)


class TestRadioVerify(unittest.TestCase):
    def _apply(self, ok, out):
        act = MagicMock()
        act.apply.return_value = (ok, out)
        return act

    def test_failed_show_is_not_verified(self):
        # rc-aware: a failing --show whose error text mentions "region" must
        # NOT read as verified (the old substring check did).
        h, ctx = _handler()
        with patch("handlers.gateway_wizard.run_script_action",
                   return_value=self._apply(False, "ERROR: could not read region")):
            ok, msg = h._v_radio(ctx, {})
        self.assertFalse(ok)

    def test_absent_region_line_is_not_verified(self):
        # A protobuf-default (UNSET=0) region is OMITTED from `--get lora`
        # output entirely — absence of the line means unconfigured.
        h, ctx = _handler()
        with patch("handlers.gateway_wizard.run_script_action",
                   return_value=self._apply(True, "lora.tx_enabled: True\n")):
            ok, msg = h._v_radio(ctx, {})
        self.assertFalse(ok)
        self.assertIn("no region", msg)

    def test_region_mismatching_profile_fails_symbolic(self):
        h, ctx = _handler()
        with patch("handlers.gateway_wizard.run_script_action",
                   return_value=self._apply(True, "region: EU_868")):
            ok, msg = h._v_radio(ctx, {"data": {"lora_profile": "us_default"}})
        self.assertFalse(ok)
        self.assertIn("does not match", msg)

    def test_numeric_region_decoded_via_protobufs(self):
        # THE REAL CLI SHAPE (fix re-review 2026-07-09): `meshtastic --get
        # lora` prints enum fields numerically — 'lora.region: 1'. Assuming
        # symbolic names made the verify a permanent false-negative.
        import sys as _sys
        h, ctx = _handler()
        pb2 = MagicMock()
        pb2.Config.LoRaConfig.RegionCode.Name.return_value = "US"
        with patch("handlers.gateway_wizard.run_script_action",
                   return_value=self._apply(True, "lora.region: 1\nlora.tx_enabled: True")), \
             patch.dict(_sys.modules, {"meshtastic": MagicMock(),
                                       "meshtastic.protobuf": MagicMock(config_pb2=pb2),
                                       "meshtastic.protobuf.config_pb2": pb2}):
            ok, msg = h._v_radio(ctx, {"data": {"lora_profile": "us_default"}})
        self.assertTrue(ok, msg)
        self.assertIn("matches profile", msg)
        pb2.Config.LoRaConfig.RegionCode.Name.assert_called_once_with(1)

    def test_numeric_region_mismatch_fails(self):
        import sys as _sys
        h, ctx = _handler()
        pb2 = MagicMock()
        pb2.Config.LoRaConfig.RegionCode.Name.return_value = "EU_868"
        with patch("handlers.gateway_wizard.run_script_action",
                   return_value=self._apply(True, "lora.region: 3")), \
             patch.dict(_sys.modules, {"meshtastic": MagicMock(),
                                       "meshtastic.protobuf": MagicMock(config_pb2=pb2),
                                       "meshtastic.protobuf.config_pb2": pb2}):
            ok, msg = h._v_radio(ctx, {"data": {"lora_profile": "us_default"}})
        self.assertFalse(ok)
        self.assertIn("does not match", msg)

    def test_numeric_region_undecodable_passes_with_caveat(self):
        # No protobufs importable: region IS set (non-default), the profile
        # comparison is honestly reported as not-checked — never a mismatch.
        import sys as _sys
        h, ctx = _handler()
        with patch("handlers.gateway_wizard.run_script_action",
                   return_value=self._apply(True, "lora.region: 1")), \
             patch.dict(_sys.modules, {"meshtastic": None,
                                       "meshtastic.protobuf": None,
                                       "meshtastic.protobuf.config_pb2": None}):
            ok, msg = h._v_radio(ctx, {"data": {"lora_profile": "us_default"}})
        self.assertTrue(ok)
        self.assertIn("not checked", msg)

    def test_region_matching_profile_passes_symbolic(self):
        h, ctx = _handler()
        with patch("handlers.gateway_wizard.run_script_action",
                   return_value=self._apply(True, "region: US\npreset: LONG_FAST")):
            ok, msg = h._v_radio(ctx, {"data": {"lora_profile": "us_default"}})
        self.assertTrue(ok)


class TestBridgeDryRunGate(unittest.TestCase):
    def test_failed_dry_run_declined_fails_step(self):
        # A failed DRY_RUN preview is a known-failed precondition; declining
        # the "run anyway" prompt must not proceed to the real run.
        h, ctx = _handler(FakeDialog(yesno=[False]))
        act = MagicMock()
        act.apply.return_value = (False, "NomadNet identity not found")
        with patch("handlers.gateway_wizard.run_script_action", return_value=act), \
             patch.object(h, "_operator_user", return_value="op"), \
             patch.object(h, "_propose") as propose:
            result = h._r_bridge(ctx, {})
        self.assertEqual(result.status, StepStatus.FAILED)
        propose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
