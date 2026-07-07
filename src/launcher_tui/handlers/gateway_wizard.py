"""Gateway Wizard — the guided, in-app SF ↔ MeshForge ↔ RNS setup.

The flagship consumer of ``guided_flow.GuidedFlow``. Gateway setup used to eject
the operator to a shell for its two most consequential steps
(``configure_gateway.sh`` + the service install) — a direct MF018 violation for
the mission feature. This handler drives the whole "bare box → verified bridged
message" flow **inside the TUI**: each step wraps an idempotent backend as a
``remediation.RemediationAction`` (propose → ratify → apply → honest report,
admin-gated, never crashes), and the final step verifies with the pre-flight
checks + points at the active RX probe.

Steps (see ``docs/GATEWAY_DEPLOYMENT.md`` — the runbook this automates):
  1. Role & variant   → provision_role.py   (which box runs a gateway unit)
  2. Radio            → configure_lora.sh    (region / preset / SF-via-preset)
  3. Bridge           → configure_gateway.sh (deps, gateway.json, rpc_key, MQTT flags)
  4. Service          → install_gateway_service.sh
  5. Verify           → gateway_preflight checks + the synthetic RX probe

The argv-builders (``_lora_argv`` etc.) are pure so they can be unit-tested
without hardware; the live end-to-end proof is the step-5 RX probe on a real box.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Tuple

from handler_protocol import BaseHandler
from guided_flow import GuidedFlow, WizardStep, StepResult, run_script_action

logger = logging.getLogger(__name__)

# Gateway-relevant fleet roles (see docs/fleet_roles.yaml) — most-specific first.
GATEWAY_ROLES = ["full-gateway", "gateway-only", "primary", "collector", "cloud-publisher"]

# Baked LoRa profiles (see scripts/configure_lora.sh PROFILES).
LORA_PROFILES = ["us_default", "us_longrange", "us_fast", "eu_default", "au_default"]


class GatewayWizardHandler(BaseHandler):
    """Guided, in-app SF ↔ MeshForge ↔ RNS gateway setup (MF018 — no shell eject)."""

    handler_id = "gateway_wizard"
    menu_section = "mesh_networks"

    def menu_items(self):
        # Feature-flagged to the gateway/full profiles (feature key "gateway").
        return [
            ("wizard", "Gateway Wizard      Guided SF↔MeshForge↔RNS setup", "gateway"),
        ]

    def execute(self, action):
        if action == "wizard":
            self.ctx.safe_call("Gateway Wizard", self._run_wizard)

    # -- paths / argv builders (pure — unit-testable) ----------------------

    def _repo_root(self) -> Path:
        # ctx.src_dir is the src/ dir; scripts live at repo_root/scripts.
        return Path(self.ctx.src_dir).parent

    def _script(self, name: str) -> str:
        return str(self._repo_root() / "scripts" / name)

    def _lora_argv(self, profile: str) -> List[str]:
        return ["bash", self._script("configure_lora.sh"), "--profile", profile]

    def _lora_show_argv(self) -> List[str]:
        return ["bash", self._script("configure_lora.sh"), "--show"]

    def _gateway_argv(self, user: str) -> List[str]:
        return ["bash", self._script("configure_gateway.sh"), user]

    def _service_argv(self) -> List[str]:
        return ["bash", self._script("install_gateway_service.sh")]

    def _role_preview_argv(self, role: str) -> List[str]:
        # Dry-run (no --apply) with the role overridden — shows the plan, writes nothing.
        return ["python3", self._script("provision_role.py"), "--role", role]

    def _role_write_argv(self, role: str) -> List[str]:
        # Persist the role into deployment.json (exits without converging).
        return ["python3", self._script("provision_role.py"), "--set-role", role]

    def _role_apply_argv(self) -> List[str]:
        # Converge systemd unit state to the persisted role.
        return ["python3", self._script("provision_role.py"), "--apply"]

    def _operator_user(self) -> str:
        from utils.paths import get_real_username
        return get_real_username()

    # -- shared step helpers ----------------------------------------------

    def _preview(self, ctx, title: str, argv: List[str], timeout: int = 60) -> None:
        """Show a read-only command's output in-pane (no eject)."""
        self._show_command_output(title, argv, timeout=timeout)

    def _propose(self, ctx, title: str, finding: str, actions) -> Tuple[bool, str]:
        """Route a mutating action through the remediation surface. Returns the
        (ok, msg) of the applied action, or (False, "declined") if the operator
        chose to do nothing — so a decline is never mis-recorded as success."""
        from remediation import propose_remediation
        result = propose_remediation(ctx, title, finding, actions)
        if result is None:
            return (False, "declined")
        return result

    def _chained_action(self, label, description, argv_list, timeout=180):
        """A RemediationAction that runs several argvs in order, stopping on the
        first failure. Used where a backend needs two calls (write role, then
        converge). Combines output; ok only if every call returns rc 0."""
        from remediation import RemediationAction

        def _apply():
            import subprocess
            out = []
            for argv in argv_list:
                try:
                    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
                except subprocess.TimeoutExpired:
                    return (False, "\n".join(out) + f"\n{argv[0]} timed out after {timeout}s")
                except (OSError, subprocess.SubprocessError) as e:
                    return (False, "\n".join(out) + f"\ncould not run {argv[0]}: {e}")
                out.append((r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else ""))
                if r.returncode != 0:
                    return (False, ("\n".join(out))[-1500:] + f"\n(exit {r.returncode})")
            return (True, ("\n".join(out)).strip()[-1500:] or "done")

        return RemediationAction(label=label, description=description,
                                 apply=_apply, requires_admin=True)

    # -- steps -------------------------------------------------------------

    def _build_steps(self) -> List[WizardStep]:
        return [
            WizardStep("role", "Role & Variant",
                       describe=self._d_role, run=self._r_role, verify=self._v_role),
            WizardStep("radio", "Radio (region / preset / SF)",
                       describe=self._d_radio, run=self._r_radio, verify=self._v_radio),
            WizardStep("bridge", "Bridge config (gateway.json + deps + rpc_key)",
                       describe=self._d_bridge, run=self._r_bridge),
            WizardStep("service", "Install gateway service",
                       describe=self._d_service, run=self._r_service, verify=self._v_service),
            WizardStep("verify", "Verify end-to-end",
                       describe=self._d_verify, run=self._r_verify, optional=False),
        ]

    # Step 1 — Role & variant
    def _d_role(self, ctx, state):
        return ("Decide whether this box runs a gateway unit, and converge its "
                "systemd state to a fleet role.\n\n"
                "Roles: " + ", ".join(GATEWAY_ROLES) + "\n\n"
                "Preview shows the plan (writes nothing); apply persists the role "
                "and converges unit state via provision_role.py.")

    def _r_role(self, ctx, state):
        choices = [(r, r) for r in GATEWAY_ROLES]
        role = ctx.dialog.menu("Role & Variant", "Pick the fleet role for this box:", choices)
        if not role:
            return StepResult.skipped("no role chosen")
        self._preview(ctx, f"Plan for role '{role}' (dry-run — no changes yet)",
                      self._role_preview_argv(role))
        ok, msg = self._propose(
            ctx, "Apply role", f"Persist role '{role}' and converge systemd units.",
            [self._chained_action(
                f"Set role '{role}' and converge", "provision_role.py --set-role + --apply",
                [self._role_write_argv(role), self._role_apply_argv()])],
        )
        if ok:
            state.setdefault("data", {})["role"] = role
            return StepResult.done(f"role '{role}' applied", role=role)
        return StepResult.failed(msg)

    def _v_role(self, ctx, state):
        role = state.get("data", {}).get("role")
        if not role:
            return (False, "no role recorded")
        # Re-read the persisted role via provision_role --print-role.
        out = self._capture_command(
            ["python3", self._script("provision_role.py"), "--print-role"], timeout=30)
        return (role in out, f"deployment.json role: {out.strip()[:200]}")

    # Step 2 — Radio
    def _d_radio(self, ctx, state):
        return ("Set the Meshtastic radio: region, channel, modem preset (this is "
                "where the mesh-side SPREADING FACTOR lives — implicitly, in the "
                "preset), TX power, hop limit.\n\n"
                "Profiles: " + ", ".join(LORA_PROFILES) + "\n\n"
                "SF on the RNS/RNode leg is a separate explicit setting in "
                "/etc/reticulum/config — not set here.")

    def _r_radio(self, ctx, state):
        choices = [(p, p) for p in LORA_PROFILES]
        profile = ctx.dialog.menu("Radio profile", "Pick a LoRa profile:", choices)
        if not profile:
            return StepResult.skipped("no profile chosen")
        self._preview(ctx, "Current LoRa config (before)", self._lora_show_argv())
        ok, msg = self._propose(
            ctx, "Apply radio profile", f"Apply LoRa profile '{profile}' to the radio.",
            [run_script_action(f"Apply '{profile}'", f"configure_lora.sh --profile {profile}",
                               self._lora_argv(profile), timeout=120)],
        )
        if ok:
            return StepResult.done(f"profile '{profile}' applied", lora_profile=profile)
        return StepResult.failed(msg)

    def _v_radio(self, ctx, state):
        out = self._capture_command(self._lora_show_argv(), timeout=60)
        # A profile applied means region + preset are set (non-empty).
        ok = bool(out) and "region" in out.lower()
        return (ok, "radio reports a LoRa config" if ok else "could not confirm LoRa config")

    # Step 3 — Bridge
    def _d_bridge(self, ctx, state):
        return ("Configure the bridge: install the service-python deps "
                "(lxmf/rns/paho-mqtt/meshtastic), enable the meshforge channel "
                "MQTT uplink/downlink flags, render gateway.json from the template, "
                "and check rpc_key pinning.\n\n"
                "Runs scripts/configure_gateway.sh in-app (no shell needed).")

    def _r_bridge(self, ctx, state):
        user = self._operator_user()
        # Preview via DRY_RUN=1 (env) — run_script_action carries the env so the
        # dry-run writes nothing, then apply for real below.
        dry_env = dict(os.environ, DRY_RUN="1")
        preview = run_script_action("preview", "dry-run", self._gateway_argv(user),
                                    timeout=90, requires_admin=False, env=dry_env)
        ok_p, out_p = preview.apply()
        ctx.dialog.textbox("Bridge config preview (DRY_RUN)", out_p or "(no output)")
        ok, msg = self._propose(
            ctx, "Configure bridge", f"Configure the gateway bridge for user '{user}'.",
            [run_script_action("Configure gateway", "configure_gateway.sh",
                               self._gateway_argv(user), timeout=300)],
        )
        if ok:
            return StepResult.done("gateway.json + deps + flags configured")
        return StepResult.failed(msg)

    # Step 4 — Service
    def _d_service(self, ctx, state):
        return ("Render, install, and enable meshforge-gateway.service, then start "
                "it. Runs scripts/install_gateway_service.sh in-app.")

    def _r_service(self, ctx, state):
        ok, msg = self._propose(
            ctx, "Install service", "Install + enable + start meshforge-gateway.service.",
            [run_script_action("Install gateway service", "install_gateway_service.sh",
                               self._service_argv(), timeout=180)],
        )
        if ok:
            return StepResult.done("meshforge-gateway installed + started")
        return StepResult.failed(msg)

    def _v_service(self, ctx, state):
        from utils.service_check import check_service
        try:
            # ServiceStatus.__bool__ == .available (systemctl is-active SSOT).
            st = check_service("meshforge-gateway")
            state_name = getattr(getattr(st, "state", None), "value", "?")
            msg = getattr(st, "message", str(st))
            return (bool(st), f"meshforge-gateway: {state_name} — {msg}")
        except Exception as e:  # never crash the wizard on a probe
            return (False, f"could not check service: {e}")

    # Step 5 — Verify
    def _d_verify(self, ctx, state):
        return ("Run the gateway pre-flight checks, then confirm the data path with "
                "an active RX probe.\n\n"
                "A gateway is only VERIFIED when a packet is observed crossing — not "
                "when the service is merely active. The synthetic RX probe (shown "
                "after the checks) is the honest end-to-end proof.")

    def _r_verify(self, ctx, state):
        fails, warns, summary = self._reuse_preflight(ctx)
        probe = (
            "Active RX probe (run on the gateway box, watch "
            "`journalctl -u meshforge-gateway -f`):\n\n"
            "  mosquitto_pub -h 127.0.0.1 -t 'msh/US/2/json/meshforge/!deadbeef' \\\n"
            "    -m '{\"payload\":{\"text\":\"rx-probe\"},\"sender\":\"!deadbeef\","
            "\"type\":\"text\",\"channel\":2,\"to\":4294967295,\"from\":3735928559}'\n\n"
            "R→M acceptance:  python3 scripts/validate_rns_to_mesh.py\n\n"
            "See docs/GATEWAY_DEPLOYMENT.md → 'Green-but-dead'."
        )
        ctx.dialog.textbox("Pre-flight + RX probe", summary + "\n\n" + probe)
        if fails == 0:
            return StepResult.done(f"pre-flight clean ({warns} warning(s)); run the RX probe to confirm")
        return StepResult.failed(f"{fails} pre-flight failure(s) — resolve then re-verify")

    def _reuse_preflight(self, ctx) -> Tuple[int, int, str]:
        """Reuse GatewayPreflightHandler's checks; return (fails, warns, text)."""
        import re as _re
        from handlers.gateway_preflight import GatewayPreflightHandler, _FAIL, _WARN
        pf = GatewayPreflightHandler()
        pf.set_context(ctx)
        results = []
        try:
            results.append(pf._check_lxmf())
            results.append(pf._check_meshtasticd())
            results.append(pf._check_rnsd())
            ch_result, uplinked = pf._check_channel_uplink()
            results.append(ch_result)
            results.append(pf._check_gateway_config_channel(uplinked))
            results.append(pf._check_gateway_identity())
            results.append(pf._check_nomadnet_identity_match())
        except Exception as e:  # a check must not crash the wizard
            results.append((_FAIL, f"pre-flight check errored: {e}", None))
        fails = sum(1 for s, _, _ in results if s == _FAIL)
        warns = sum(1 for s, _, _ in results if s == _WARN)
        ansi = _re.compile(r"\033\[[0-9;]*m")
        lines = ["Pre-flight results:", ""]
        for status, msg, fix in results:
            icon = "PASS" if status not in (_FAIL, _WARN) else ("FAIL" if status == _FAIL else "WARN")
            lines.append(f"  [{icon}] {ansi.sub('', msg)}")
            if fix and status in (_FAIL, _WARN):
                lines.append(f"        fix: {ansi.sub('', fix)}")
        lines.append("")
        lines.append(f"{fails} failure(s), {warns} warning(s).")
        return fails, warns, "\n".join(lines)

    # -- entrypoint --------------------------------------------------------

    def _run_wizard(self):
        ctx = self.ctx
        if os.geteuid() != 0:
            ctx.dialog.msgbox(
                "Gateway Wizard",
                "This wizard changes system configuration (radio, services, "
                "/etc) and needs Admin mode.\n\nStart MeshForge in Admin mode "
                "to continue, then reopen the wizard.",
            )
            return
        flow = GuidedFlow("gateway", "Gateway Wizard (SF ↔ MeshForge ↔ RNS)",
                          self._build_steps())
        flow.run(ctx)
