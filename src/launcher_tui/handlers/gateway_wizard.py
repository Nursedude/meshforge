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
import re
from pathlib import Path
from typing import List, Optional, Tuple

from handler_protocol import BaseHandler
from guided_flow import GuidedFlow, WizardStep, StepResult, run_script_action

logger = logging.getLogger(__name__)

# Gateway-relevant fleet roles (see docs/fleet_roles.yaml) — most-specific first.
GATEWAY_ROLES = ["full-gateway", "gateway-only", "primary", "collector", "cloud-publisher"]

# Baked LoRa profiles (see scripts/configure_lora.sh PROFILES).
LORA_PROFILES = ["us_default", "us_longrange", "us_fast", "eu_default", "au_default"]

# Profile-name prefix → expected radio region prefix, for the radio verify.
_PROFILE_REGION_PREFIX = {"us": "US", "eu": "EU", "au": "AU"}

# Unit states (per docs/fleet_roles.yaml) that contradict installing the
# gateway service on this box.
_NO_GATEWAY_STATES = {"disabled", "absent"}


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

    def _propose(self, ctx, title: str, finding: str, actions) -> Optional[Tuple[bool, str]]:
        """Route a mutating action through the remediation surface. Returns the
        (ok, msg) of the applied action, or None if the operator chose "do
        nothing" — a first-class decline (the remediation contract's words),
        which callers record as SKIPPED, never as a failure."""
        from remediation import propose_remediation
        return propose_remediation(ctx, title, finding, actions)

    def _chained_action(self, label, description, argv_list, timeout=180):
        """A RemediationAction that runs several argvs in order, stopping on the
        first failure. Composes run_script_action per argv so the subprocess
        semantics (timeout/stderr-combine/honest tail) live in ONE place."""
        from remediation import RemediationAction

        steps = [run_script_action(f"{label} [{i + 1}/{len(argv_list)}]", description,
                                   argv, timeout=timeout)
                 for i, argv in enumerate(argv_list)]

        def _apply():
            out = []
            for step in steps:
                ok, msg = step.apply()
                out.append(msg)
                if not ok:
                    return (False, ("\n".join(out))[-1500:])
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
        res = self._propose(
            ctx, "Apply role", f"Persist role '{role}' and converge systemd units.",
            [self._chained_action(
                f"Set role '{role}' and converge", "provision_role.py --set-role + --apply",
                [self._role_write_argv(role), self._role_apply_argv()])],
        )
        if res is None:
            return StepResult.skipped("declined — role left as-is")
        ok, msg = res
        if ok:
            # role rides StepResult.data — the engine merges it into state["data"].
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
        res = self._propose(
            ctx, "Apply radio profile", f"Apply LoRa profile '{profile}' to the radio.",
            [run_script_action(f"Apply '{profile}'", f"configure_lora.sh --profile {profile}",
                               self._lora_argv(profile), timeout=120)],
        )
        if res is None:
            return StepResult.skipped("declined — radio left as-is")
        ok, msg = res
        if ok:
            return StepResult.done(f"profile '{profile}' applied", lora_profile=profile)
        return StepResult.failed(msg)

    def _region_matches_profile(self, region_token: str, profile: str):
        """Compare the observed region token with the profile's expected region
        prefix. The CLI prints enum fields NUMERICALLY (``lora.region: 1``) —
        assuming symbolic names made the verify a permanent false-negative on a
        correctly-configured radio (fix re-review, 2026-07-09). A numeric token
        is decoded via the meshtastic protobufs; when that decode is
        unavailable, returns (None, ...) — comparison not performed, said so.

        Returns (True/False, detail) or (None, detail) when no expectation
        applies / the token can't be decoded."""
        want = _PROFILE_REGION_PREFIX.get(profile.split("_", 1)[0]) if profile else None
        if not want:
            return None, f"region {region_token}"
        token = region_token
        if token.isdigit():
            try:
                from meshtastic.protobuf import config_pb2
                token = config_pb2.Config.LoRaConfig.RegionCode.Name(int(token))
            except Exception:
                return None, (f"region enum {region_token} is set; symbolic "
                              f"decode unavailable — profile match not checked")
        if token.upper().startswith(want):
            return True, f"region {token} matches profile '{profile}'"
        return False, f"radio region {token} does not match profile '{profile}'"

    def _v_radio(self, ctx, state):
        # rc-aware capture (a substring match on exit-code-ignored output let a
        # failed --show read as verified — 2026-07-09 review). run_script_action
        # returns (rc == 0, tail).
        ok_rc, out = run_script_action(
            "show", "configure_lora.sh --show", self._lora_show_argv(),
            timeout=60, requires_admin=False,
        ).apply()
        if not ok_rc:
            return (False, f"configure_lora.sh --show failed: {(out or '')[-200:]}")
        # Matches both the CLI's real shape ('lora.region: 1') and symbolic
        # dialects ('region: US'). An UNSET (=0, protobuf default) region is
        # omitted from the CLI output entirely, so "no region line" = unset.
        m = re.search(r"(?:lora\.)?region:\s*([A-Za-z0-9_]+)", out or "", re.IGNORECASE)
        region = m.group(1) if m else ""
        if not region or region == "0" or region.upper() == "UNSET":
            return (False, "radio reports no region — the profile did not take")
        profile = state.get("data", {}).get("lora_profile", "")
        matched, detail = self._region_matches_profile(region, profile)
        if matched is False:
            return (False, detail)
        return (True, f"radio {detail}")

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
        if not ok_p:
            # A failed dry-run is a known-failed precondition — don't walk the
            # operator straight into the same failure for real.
            if not ctx.dialog.yesno(
                "Configure bridge",
                "The DRY_RUN preview FAILED (see output above) — the real run "
                "will most likely fail at the same point.\n\nAttempt the real "
                "run anyway?",
            ):
                return StepResult.failed("dry-run preview failed — resolve the "
                                         "error shown, then re-run this step")
        res = self._propose(
            ctx, "Configure bridge", f"Configure the gateway bridge for user '{user}'.",
            [run_script_action("Configure gateway", "configure_gateway.sh",
                               self._gateway_argv(user), timeout=300)],
        )
        if res is None:
            return StepResult.skipped("declined — bridge config left as-is")
        ok, msg = res
        if ok:
            return StepResult.done("gateway.json + deps + flags configured")
        return StepResult.failed(msg)

    # Step 4 — Service
    def _d_service(self, ctx, state):
        return ("Render, install, and enable meshforge-gateway.service, then start "
                "it. Runs scripts/install_gateway_service.sh in-app.")

    def _gateway_unit_desired(self) -> str:
        """The box's effective declared state for meshforge-gateway, via
        provision_role.py --print-unit-state (the role SSOT — never a second
        copy of the role→unit map here). Returns 'unknown' when the probe
        output isn't a recognized token (degraded probe ≠ a definitive state)."""
        out = self._capture_command(
            ["python3", self._script("provision_role.py"),
             "--print-unit-state", "meshforge-gateway"], timeout=30)
        tok = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if tok in ("enabled", "disabled", "absent", "unspecified") or tok.startswith("waived:"):
            return tok
        return "unknown"

    def _r_service(self, ctx, state):
        # Installing the gateway unit on a box whose role declares it
        # disabled/absent contradicts step 1: probe_role_drift pages and the
        # next provision_role --apply stops what this step started.
        desired = self._gateway_unit_desired()
        if desired in _NO_GATEWAY_STATES:
            if not ctx.dialog.yesno(
                "Install service",
                f"This box's fleet role declares meshforge-gateway '{desired}' "
                "— installing it contradicts the role you applied in step 1: "
                "probe_role_drift will page, and the next provision_role "
                "--apply will stop the service again.\n\nInstall anyway?",
            ):
                return StepResult.skipped(
                    f"role declares meshforge-gateway {desired} — install "
                    "skipped (pick a gateway role in step 1 to run a bridge here)")
        res = self._propose(
            ctx, "Install service", "Install + enable + start meshforge-gateway.service.",
            [run_script_action("Install gateway service", "install_gateway_service.sh",
                               self._service_argv(), timeout=180)],
        )
        if res is None:
            return StepResult.skipped("declined — service left as-is")
        ok, msg = res
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

    def _rx_probe_text(self) -> str:
        """The copy-paste RX probe, derived from the RENDERED gateway.json —
        a hardcoded topic is the Issue #34 topic-shape trap in probe form
        (2026-07-09 review). Uses the region-less topic shape, which the RX
        subscriber always subscribes alongside the region-ful one."""
        root, chan_name, chan_idx = "msh", "meshforge", 2  # runbook fallbacks
        note = ""
        try:
            from gateway.config import GatewayConfig
            cfg = GatewayConfig.load()
            root = getattr(cfg.mqtt_bridge, "root_topic", None) or root
            chan_name = getattr(cfg.mqtt_bridge, "channel", None) or chan_name
            chan_idx = int(getattr(cfg.meshtastic, "channel", chan_idx) or 0)
        except Exception as e:
            note = (f"\n  NOTE: could not read gateway.json ({e}) — the values "
                    "above are runbook DEFAULTS; substitute your configured "
                    "root_topic / channel before running.")
        return (
            "Active RX probe (run on the gateway box, watch "
            "System > Logs in-app):\n\n"
            f"  mosquitto_pub -h 127.0.0.1 -t '{root}/2/json/{chan_name}/!deadbeef' \\\n"
            "    -m '{\"payload\":{\"text\":\"rx-probe\"},\"sender\":\"!deadbeef\","
            f"\"type\":\"text\",\"channel\":{chan_idx},\"to\":4294967295,\"from\":3735928559}}'"
            f"{note}\n\n"
            "R→M acceptance:  python3 scripts/validate_rns_to_mesh.py\n\n"
            "See docs/GATEWAY_DEPLOYMENT.md → 'Green-but-dead'."
        )

    def _r_verify(self, ctx, state):
        fails, warns, summary = self._reuse_preflight(ctx)
        ctx.dialog.textbox("Pre-flight + RX probe", summary + "\n\n" + self._rx_probe_text())
        if fails > 0:
            return StepResult.failed(f"{fails} pre-flight failure(s) — resolve then re-verify")
        # Pre-flight clean is necessary, not sufficient: a gateway is only
        # VERIFIED when a packet is observed crossing (this step's own bar).
        # DONE here requires the operator to confirm the observation — the
        # probe is deliberately not auto-run (it would spam live NomadNet).
        confirmed = ctx.dialog.menu(
            "Verify end-to-end",
            f"Pre-flight clean ({warns} warning(s)).\n\n"
            "Run the RX probe shown (copy-paste), watch the gateway journal, "
            "then answer honestly:",
            [("confirmed", "I ran the probe and OBSERVED the message cross"),
             ("later", "Not yet — leave this step unconfirmed")],
        )
        if confirmed == "confirmed":
            return StepResult.done(
                f"pre-flight clean ({warns} warning(s)) + RX probe observed "
                "(operator-confirmed)")
        return StepResult.skipped(
            f"pre-flight clean ({warns} warning(s)); RX probe NOT yet observed "
            "— re-run this step to confirm end-to-end")

    def _reuse_preflight(self, ctx) -> Tuple[int, int, str]:
        """Render GatewayPreflightHandler.collect_results() (the SSOT check
        inventory — core + template drift); return (fails, warns, text)."""
        from handlers.gateway_preflight import GatewayPreflightHandler, _FAIL, _WARN
        # Registered instance when available (Q5, audit W16)
        _reg = getattr(ctx, 'registry', None)
        pf = _reg.get_handler('gateway_preflight') if _reg else None
        if pf is None:
            pf = GatewayPreflightHandler()
            pf.set_context(ctx)
        try:
            core, template = pf.collect_results()
            results = list(core) + list(template)
        except Exception as e:  # a check must not crash the wizard
            results = [(_FAIL, f"pre-flight check errored: {e}", None)]
        fails = sum(1 for s, _, _ in results if s == _FAIL)
        warns = sum(1 for s, _, _ in results if s == _WARN)
        ansi = re.compile(r"\033\[[0-9;]*m")
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
